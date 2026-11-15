# -*- coding: utf-8 -*-
"""
Split a centerline where a side value changes along it.

Calculate left and right samples the line and reports a crossing. This cuts
the line at the change, writes each piece's own left and right values, and
copies that side onto the addresses that sit on the piece.
"""

from datetime import datetime
from typing import Dict, Hashable, List, Optional, Sequence

from qgis.core import QgsFeatureRequest, QgsGeometry, QgsPointXY, QgsVectorLayer, QgsWkbTypes

from .. import geometry_utils as gu
from ..config import PluginConfig
from ..layer_access import ConfigError, FieldAccessor, apply_changes, ensure_editable
from ..project_config import ProjectConfig, layer_for
from ..split_ops import apply_piece_split
from ..streets import names as N
from .engine import (AddressContext, StreetMeasure, attribution_values, community_from_segment,
                     offset_point, stored_access, _pt)
from .numbering import side_of
from .ssap import COMMUNITY_FIELDS

SIDE_SPLIT_COMMAND = "NENA: split where the side changes"
COPY_COMMAND = "NENA: copy side after split"
_SAMPLES = (0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95)


class _SampleCtx:
    """What the polygon lookup needs. Addresses are optional."""

    def __init__(self, layer: QgsVectorLayer, cfg: PluginConfig, pcfg: ProjectConfig):
        self.pcfg = pcfg
        self.rcl = layer
        self.rcl_cfg = cfg
        self.rcl_acc = FieldAccessor(layer, cfg)


def attribution_context(layer: QgsVectorLayer, cfg: PluginConfig, pcfg: ProjectConfig) -> _SampleCtx:
    return _SampleCtx(layer, cfg, pcfg)


def split_where_side_changes(layer: QgsVectorLayer, cfg: PluginConfig, pcfg: ProjectConfig,
                             fids: Optional[Sequence[Hashable]] = None) -> dict:
    """
    Split each centerline where a configured polygon changes along a side.

    :returns: {"split", "pieces", "addresses", "skipped"}
    """
    report = {"split": 0, "pieces": 0, "addresses": 0, "skipped": []}
    if not (pcfg.addresses.attribution_rules or []):
        raise ConfigError("Choose the ZIP, MSAG, and ESN polygon layers under Settings, Sides first.")
    ctx = _SampleCtx(layer, cfg, pcfg)
    off = gu.metres_to_layer_units(3.0, layer.crs())
    req = QgsFeatureRequest() if not fids else QgsFeatureRequest().setFilterFids(list(fids))
    planned = []
    for feat in layer.getFeatures(req):
        try:
            geom = gu.single_curve_geometry(feat.geometry())
        except gu.SplitError as exc:
            report["skipped"].append(f"Feature {feat.id()}: {exc}")
            continue
        cuts = _cut_distances(ctx, geom, off)
        if not cuts:
            continue
        multi = QgsWkbTypes.isMultiType(layer.wkbType())
        tol = gu.metres_to_layer_units(max(cfg.ranging.snap_tolerance_m, 0.05), layer.crs())
        pieces = gu.split_line_at_distances(geom, cuts, multi, tol)
        if len(pieces) < 2:
            report["skipped"].append(f"Feature {feat.id()}: the change is too close to an end.")
            continue
        planned.append((feat, pieces, [_side_values(ctx, g, off) for g in pieces]))
    if not planned:
        return report
    acc = ctx.rcl_acc
    stamp = datetime.now().replace(microsecond=0)
    piece_ids: List[Hashable] = []
    ensure_editable(layer)
    layer.beginEditCommand(SIDE_SPLIT_COMMAND)
    try:
        for feat, pieces, side_vals in planned:
            new_ids, _ranges, _used = apply_piece_split(layer, cfg, acc, feat, pieces, stamp)
            ids = [feat.id()] + list(new_ids)
            for fid, vals in zip(ids, side_vals):
                if vals:
                    layer.changeAttributeValues(fid, {acc.index[n]: acc.coerce(n, v) for n, v in vals.items()
                                                      if acc.has(n)})
            piece_ids.extend(ids)
            report["split"] += 1
            report["pieces"] += len(new_ids)
    except Exception:
        layer.destroyEditCommand()
        raise
    layer.endEditCommand()
    report["addresses"] = _copy_onto_addresses(layer, cfg, pcfg, piece_ids)
    return report


def _sample(ctx, geom, side: str, fraction: float, off: float) -> dict:
    pt = offset_point(geom, fraction, side, off)
    if pt is None:
        return {}
    return attribution_values(ctx, pt)[0]


def _refine(ctx, geom, side, off, t0, t1, left, right, depth=10) -> float:
    if depth <= 0 or (t1 - t0) < 0.002:
        return (t0 + t1) / 2.0
    mid = (t0 + t1) / 2.0
    found = _sample(ctx, geom, side, mid, off)
    if found == left:
        return _refine(ctx, geom, side, off, mid, t1, found, right, depth - 1)
    if found == right:
        return _refine(ctx, geom, side, off, t0, mid, left, found, depth - 1)
    return mid


def _cut_distances(ctx, geom, off: float) -> List[float]:
    total = geom.length()
    if total <= 0:
        return []
    fractions = []
    for side in ("L", "R"):
        samples = [_sample(ctx, geom, side, t, off) for t in _SAMPLES]
        for i in range(len(_SAMPLES) - 1):
            if samples[i] != samples[i + 1]:
                fractions.append(_refine(ctx, geom, side, off, _SAMPLES[i], _SAMPLES[i + 1],
                                         samples[i], samples[i + 1]))
    cuts = []
    for fraction in fractions:
        point = geom.interpolate(total * fraction).asPoint()
        cuts.append(geom.lineLocatePoint(QgsGeometry.fromPointXY(QgsPointXY(point))))
    return cuts


def _side_values(ctx, geom, off: float) -> Dict[str, str]:
    vals = {}
    for side in ("L", "R"):
        found = _sample(ctx, geom, side, 0.5, off)
        for key, value in found.items():
            field_name = COMMUNITY_FIELDS[key][1 if side == "L" else 2]
            if value and ctx.rcl_acc.has(field_name):
                vals[field_name] = value
    return vals


def _copy_onto_addresses(layer, cfg, pcfg, piece_ids: Sequence[Hashable]) -> int:
    if not piece_ids or layer_for(pcfg.streets, "address") is None:
        return 0
    try:
        ctx = AddressContext(pcfg, layer, cfg)
    except ConfigError:
        return 0
    piece_set = set(piece_ids)
    feats = {f.id(): f for f in layer.getFeatures(QgsFeatureRequest().setFilterFids(list(piece_set)))}
    nguids = {fid: (ctx.rcl_acc.get(feat, "RCL_NGUID") if ctx.rcl_acc.has("RCL_NGUID") else None)
              for fid, feat in feats.items()}
    measures = {}
    changes = {}
    for feat in ctx.addr.getFeatures():
        if not feat.hasGeometry():
            continue
        sid = N.clean(ctx.addr_acc.get(feat, "StreetID")) if ctx.addr_acc.has("StreetID") else ""
        key = N.name_key({p: ctx.addr_acc.get(feat, p) for p in N.NAME_PARTS})
        street = sid or key
        if not street:
            continue
        if street not in measures:
            measures[street] = StreetMeasure(ctx, sid, key)
        if not set(measures[street].segs) & piece_set:
            continue
        probe = stored_access(ctx, feat) or ctx.from_addr.transform(_pt(feat.geometry()))
        loc = measures[street].locate(QgsPointXY(probe))
        if loc is None or loc[1] not in piece_set:
            continue
        seg = feats.get(loc[1])
        if seg is None:
            continue
        vals = {}
        side = N.clean(ctx.addr_acc.get(feat, "Addr_Side")).upper() if ctx.addr_acc.has("Addr_Side") else ""
        if side not in ("L", "R"):
            roof = ctx.from_addr.transform(_pt(feat.geometry()))
            dx, dy = loc[3]
            side = side_of(dx, dy, roof.x() - probe.x(), roof.y() - probe.y()) or ""
        if side in ("L", "R"):
            community, _notes, _updates = community_from_segment(ctx, seg, side, loc[2])
            for ckey, value in community.items():
                field_name = COMMUNITY_FIELDS[ckey][0]
                if ctx.addr_acc.has(field_name):
                    vals[field_name] = value
        if ctx.addr_acc.has("RCL_NGUID"):
            vals["RCL_NGUID"] = nguids.get(loc[1]) or None
        if vals:
            changes[feat.id()] = vals
    return apply_changes(ctx.addr, ctx.addr_acc, changes, COPY_COMMAND)
