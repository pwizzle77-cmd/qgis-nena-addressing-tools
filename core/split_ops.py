# -*- coding: utf-8 -*-
"""
Shared split / re-range operations.

* :func:`rerange_pieces` – compute side ranges for the ordered pieces of an
  original centerline (used by the automatic split watcher and by the
  Split & Re-range map tool).
* :func:`split_and_rerange` – split one feature at a point and write both
  pieces with their new ranges in ONE undoable edit command.
"""

from datetime import datetime
from typing import Dict, Hashable, List, Sequence, Tuple

from qgis.core import (QgsFeatureRequest, QgsGeometry, QgsMessageLog, QgsVectorLayer,
                       QgsVectorLayerUtils, QgsWkbTypes)

from ..compat import msg_level, py_value
from . import geometry_utils as gu
from . import ranging as R
from .anchors import point_anchors, point_layer_ready
from .config import M_ANCHORED, M_INTERVAL, M_POINTS, PluginConfig
from .defaults import empty_defaults
from .layer_access import ConfigError, FieldAccessor, ensure_editable, side_values
from .nguid import agency_from, local_id, make_nguid
from .schema import RANGE_FIELDS, SIDE_FIELDS

NEW_PIECE = "__new_piece__"
SPLIT_COMMAND = "NENA: split & re-range"


def rerange_pieces(layer: QgsVectorLayer, cfg: PluginConfig, original: Dict[str, tuple],
                   pieces: Sequence[Tuple[Hashable, QgsGeometry]], street_key=None,
                   exclude_fids: Sequence[Hashable] = ()) -> Tuple[List[Dict[str, R.SideRange]], int]:
    """
    :param original: {'L': (from, to, parity), 'R': (...)} of the original line
    :param pieces: ordered (id, geometry) pieces
    :returns: (per piece {'L': SideRange, 'R': SideRange}, number of anchor points used)
    """
    da = gu.make_distance_area(layer.crs())
    lengths = [gu.length_m(da, g) for _pid, g in pieces]
    method = cfg.edit.split_method
    anchors = {"L": [], "R": []}
    if method in (M_ANCHORED, M_POINTS) and point_layer_ready(cfg):
        anchors = point_anchors(layer, cfg, pieces, street_key, exclude_fids)

    mpn = split_metres_per_number(cfg)
    per_side = {}
    for side in ("L", "R"):
        f, t, p = original[side]
        per_side[side] = R.redistribute(f, t, p, lengths, anchors[side] if method == M_ANCHORED else None,
                                        metres_per_number=mpn if method != M_POINTS else None)
        if method == M_POINTS and anchors[side]:
            obs = _anchors_per_piece(anchors[side], lengths)
            rs = cfg.ranging
            default = rs.left_parity if side == "L" else rs.right_parity
            res = R.ranges_from_observations(obs, R.normalize_parity(p, default), rs.block_size,
                                             rs.point_expand_to_block, rs.point_infer_parity)
            per_side[side] = [r if r is not None else per_side[side][i] for i, r in enumerate(res)]
    used = len(anchors["L"]) + len(anchors["R"])
    return [{"L": per_side["L"][i], "R": per_side["R"][i]} for i in range(len(pieces))], used


def split_metres_per_number(cfg: PluginConfig):
    """Metres per house number when addressing is by distance, else None.

    The same spacing Draw road uses: the Address rules distance when new
    addresses are numbered by distance, otherwise the Ranges interval when
    ranging by distance. Hundred-block and proportional ranging split
    proportionally, as before."""
    try:
        from .project_config import load_project_config
        a = load_project_config().addresses
        if a.method == "distance" and float(a.distance_per_number or 0) > 0:
            return R.to_meters(float(a.distance_per_number), a.distance_units)
    except Exception as err:  # noqa: BLE001 - no project settings
        QgsMessageLog.logMessage(
            f"split_ops: no project distance-ranging setting, falling back to cfg.ranging: {err!r}",
            "NENA Addressing and RCL Tools", msg_level("Info"))
    rs = cfg.ranging
    if rs.method == M_INTERVAL and float(rs.interval_distance or 0) > 0:
        return R.to_meters(float(rs.interval_distance), rs.interval_units)
    return None


def _anchors_per_piece(anchors, lengths):
    out = [[] for _ in lengths]
    cum = R.cumulative(lengths)
    for d, n in anchors:
        for i in range(len(lengths)):
            if cum[i] <= d < cum[i + 1] or (i == len(lengths) - 1 and d >= cum[i]):
                out[i].append(n)
                break
    return out


def piece_values(acc: FieldAccessor, cfg: PluginConfig, ranges: Dict[str, R.SideRange], stamp) -> dict:
    vals = {}
    for side in ("L", "R"):
        vals.update(side_values(side, ranges[side], acc.has(SIDE_FIELDS[side]["parity"])))
    if cfg.ranging.stamp_date_update and acc.has("DateUpdate"):
        vals["DateUpdate"] = stamp
    return vals


NGUID_SAMPLE = 200   # existing NGUIDs read to find the agency when none is set


def nguid_agency(cfg: PluginConfig, layer: QgsVectorLayer = None, acc: FieldAccessor = None, like=None):
    """
    (agency, format) for new RCL NGUIDs: the agency identifier from the
    settings; else the one in the NGUID (or DiscrpAgID) of ``like`` – the
    road a piece was split from; else the one most of the layer's existing
    NGUIDs use. (None, format) when there is no way to know it.
    """
    agency = (cfg.edit.agency_id or "").strip()
    if agency:
        return agency, cfg.edit.nguid_format
    if acc is not None and like is not None:
        if acc.has("RCL_NGUID"):
            found, fmt = agency_from([acc.get(like, "RCL_NGUID")])
            if found:
                return found, fmt
        if acc.has("DiscrpAgID") and acc.get(like, "DiscrpAgID"):
            return str(acc.get(like, "DiscrpAgID")).strip(), cfg.edit.nguid_format
    return agency_from_layers([(layer, acc, "RCL_NGUID")], cfg.edit.nguid_format)


def agency_from_layers(sources, default_fmt: str = "urn"):
    """
    (agency, format) most NGUIDs of the first source that has any use;
    ``sources``: [(layer, accessor, NENA field name)]. A ``DiscrpAgID``
    source gives the agency as stored, with ``default_fmt``.
    (None, default_fmt) when none has one.
    """
    for layer, acc, field in sources:
        if layer is None or acc is None or not acc.has(field):
            continue
        values = _sample_nguids(layer, acc, field)
        if field == "DiscrpAgID":
            values = [str(v).strip() for v in values if str(v).strip()]
            if values:
                return max(set(values), key=values.count), default_fmt
            continue
        found, fmt = agency_from(values)
        if found:
            return found, fmt
    return None, default_fmt


def _sample_nguids(layer: QgsVectorLayer, acc: FieldAccessor, field: str = "RCL_NGUID",
                   limit: int = NGUID_SAMPLE, scan: int = 20 * NGUID_SAMPLE):
    """Up to ``limit`` non-empty values of ``field`` (reading at most ``scan`` features)."""
    request = QgsFeatureRequest().setSubsetOfAttributes([acc.index[field]])
    flag = getattr(getattr(QgsFeatureRequest, "Flag", QgsFeatureRequest), "NoGeometry", None)
    if flag is not None and hasattr(request, "setFlags"):
        request.setFlags(flag)
    found = []
    for n, f in enumerate(layer.getFeatures(request)):
        value = acc.get(f, field)
        if value:
            found.append(value)
        if len(found) >= limit or n >= scan:
            break
    return found


def new_nguid(cfg: PluginConfig, layer: QgsVectorLayer = None, acc: FieldAccessor = None, like=None):
    """A fresh RCL NGUID, or None when the agency can't be found (see nguid_agency)."""
    agency, fmt = nguid_agency(cfg, layer, acc, like)
    if not agency:
        return None
    return make_nguid(local_id(cfg.edit.nguid_local_id, None), agency, fmt or cfg.edit.nguid_format)


def apply_piece_split(layer: QgsVectorLayer, cfg: PluginConfig, acc: FieldAccessor, feat,
                      piece_geoms: Sequence[QgsGeometry], stamp, exclude_fids: Sequence[Hashable] = ()):
    """
    Replace ``feat`` by its ordered pieces **inside an edit command the caller
    has started**: the feature keeps the first piece, every further piece is
    added as a new feature (attributes copied except provider primary keys;
    new NGUID). The original side ranges are redistributed over the pieces so
    each piece continues the numbering of the previous one (anchored to
    address points when configured). Features with no ranges are only split.

    :returns: (list of new feature ids, per-piece ranges or None, anchor points used)
    """
    fid = feat.id()
    original = {side: acc.side_range(feat, side) for side in ("L", "R")}
    has_ranges = any(original[s][0] is not None or original[s][1] is not None for s in ("L", "R"))
    ranges, used = None, 0
    if has_ranges:
        ids = [fid] + [f"{NEW_PIECE}{i}" for i in range(1, len(piece_geoms))]
        ranges, used = rerange_pieces(layer, cfg, original, list(zip(ids, piece_geoms)), acc.street_key(feat),
                                      exclude_fids=[fid] + list(exclude_fids))
    pk = set(layer.primaryKeyAttributes())
    if not layer.changeGeometry(fid, piece_geoms[0]):
        raise ConfigError("The data provider refused the geometry change.")
    current = {name: acc.get(feat, name) for name in (cfg.defaults or {})}
    if ranges:
        vals1 = piece_values(acc, cfg, ranges[0], stamp)
        current.update(vals1)
        layer.changeAttributeValues(fid, {acc.index[n]: acc.coerce(n, v) for n, v in vals1.items() if acc.has(n)})
    kept = empty_defaults(cfg, acc, current)
    if kept:
        layer.changeAttributeValues(fid, {acc.index[n]: acc.coerce(n, v) for n, v in kept.items() if acc.has(n)})
    new_ids = []
    for i, g in enumerate(piece_geoms[1:], start=1):
        vals = piece_values(acc, cfg, ranges[i], stamp) if ranges else {}
        if acc.has("RCL_NGUID"):
            # None = leave empty rather than duplicate the original id
            vals["RCL_NGUID"] = new_nguid(cfg, layer, acc, feat)
        attrs = {k: v for k, v in enumerate(feat.attributes()) if k not in pk}
        copied = {nena: py_value(attrs[idx]) for nena, idx in acc.index.items() if idx in attrs}
        copied.update(vals)
        vals.update(empty_defaults(cfg, acc, copied))
        for nena, value in vals.items():
            if acc.has(nena):
                attrs[acc.index[nena]] = acc.coerce(nena, value)
        new = QgsVectorLayerUtils.createFeature(layer, g, attrs, layer.createExpressionContext())
        if not layer.addFeature(new):
            raise ConfigError("The data provider refused the new feature.")
        new_ids.append(new.id())
    return new_ids, ranges, used


def split_and_rerange(layer: QgsVectorLayer, cfg: PluginConfig, fid: Hashable, point) -> Tuple[Hashable, str]:
    """
    Split feature ``fid`` at the point on it nearest to ``point`` (layer CRS)
    and re-range both pieces – geometry change, attribute changes and the new
    feature form one undo step ("NENA: split & re-range").

    :returns: (id of the new feature, human readable summary)
    """
    acc = FieldAccessor(layer, cfg)
    acc.require(RANGE_FIELDS)
    feat = next(layer.getFeatures(QgsFeatureRequest().setFilterFid(fid)), None)
    if feat is None:
        raise ConfigError("Feature not found.")
    tol = gu.metres_to_layer_units(max(cfg.ranging.snap_tolerance_m, 0.05), layer.crs())
    try:
        g1, g2, _d = gu.split_line_at(feat.geometry(), point, QgsWkbTypes.isMultiType(layer.wkbType()), tol)
    except gu.SplitError as exc:
        raise ConfigError(f"Cannot split: {exc}.") from exc

    stamp = datetime.now().replace(microsecond=0)
    ensure_editable(layer)
    layer.beginEditCommand(SPLIT_COMMAND)
    try:
        new_ids, ranges, used = apply_piece_split(layer, cfg, acc, feat, [g1, g2], stamp)
    except Exception:
        layer.destroyEditCommand()
        raise
    layer.endEditCommand()
    return new_ids[0], describe(ranges, used)


def describe(ranges, used=0) -> str:
    if not ranges:
        return "No address ranges to redistribute."

    def side(s):
        return " | ".join(f"{r[s][0]}–{r[s][1]}" for r in ranges)
    return (f"Left {side('L')}, right {side('R')}"
            + (f" ({used} address point(s) used)" if used else ""))
