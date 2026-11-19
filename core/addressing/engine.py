# -*- coding: utf-8 -*-
"""
Addressing engine (PyQGIS): plan an address from an access point and a
rooftop, write the address point (+ driveway), undo it, re-address, and
attribute features from boundary polygons.

Coordinates handed in by the map tool are in the **centerline layer's CRS**;
everything written to the address and driveway layers is transformed to
their own CRS.
"""

import math
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Dict, Hashable, List, Optional, Sequence, Tuple

from qgis.core import (Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeatureRequest, QgsGeometry,
                       QgsPointXY, QgsProject, QgsRectangle, QgsVectorLayer, QgsVectorLayerUtils, QgsWkbTypes)

from ...compat import py_value
from .. import geometry_utils as gu
from ..config import PluginConfig, load_from_layer
from ..layer_access import ConfigError, FieldAccessor, ensure_editable, ensure_full_name_mapping
from ..nguid import local_id, make_nguid
from ..project_config import ProjectConfig, layer_for
from ..ranging import normalize_parity, to_meters
from ..defaults import address_fallbacks
from ..schema import SIDE_FIELDS, auto_map
from ..streets import names as N
from ..streets.repository import StreetRepository
from ..streets.service import _with_street_id, suppressed
from ..template import effective_fields
from ..topology import SegmentInfo, assign_nodes, build_chains
from . import numbering as NB
from ..access_rules import nearest_access, suppressed_guard
from ..safe_call import attempt
from .ssap import ACCESS_FIELDS, COMMUNITY_FIELDS, DRIVEWAY_FIELDS, SSAP_FIELDS

ADDRESS_COMMAND = "NENA: add address"
DRIVEWAY_COMMAND = "NENA: add driveway"
DRAW_DRIVEWAY_COMMAND = "NENA: draw driveway"
ACCESS_COMMAND = "NENA: add access point"
_NUM = re.compile(r"^\s*(\d+)\s*([A-Za-z]?)")
# A stored access farther than this from its address is not the driveway end.
# Whole-degree coordinates (a SQL Server float saved as an integer) are.
_ACCESS_LIMIT_M = 5000.0
# Place address drops the access point this far off the centerline, on the building's side.
_ACCESS_OFFSET_M = to_meters(10.0, "feet")

# features created by the address tool, newest last: [(layer_id, fid), ...] per address
UNDO_STACK: List[List[Tuple[str, Hashable]]] = []


def _xf(src: QgsCoordinateReferenceSystem, dst: QgsCoordinateReferenceSystem) -> QgsCoordinateTransform:
    return QgsCoordinateTransform(src, dst, QgsProject.instance())


def _pt(geom_or_pt) -> QgsPointXY:
    if isinstance(geom_or_pt, QgsGeometry):
        g = geom_or_pt.centroid() if geom_or_pt.isMultipart() else geom_or_pt
        return QgsPointXY(g.asPoint())
    return QgsPointXY(geom_or_pt)


def parse_number(value, suffix_value=None) -> Tuple[Optional[int], str]:
    v = py_value(value)
    if v is None:
        return None, ""
    if isinstance(v, (int, float)):
        n, suf = int(v), ""
    else:
        m = _NUM.match(str(v))
        if not m:
            return None, ""
        n, suf = int(m.group(1)), m.group(2).upper()
    s2 = py_value(suffix_value)
    if s2:
        suf = str(s2).strip().upper()
    return n, suf


def address_mapping(layer: QgsVectorLayer, pcfg: ProjectConfig) -> Dict[str, str]:
    """NENA SSAP name -> field on the address layer (template-aware), plus manual overrides."""
    defs = effective_fields(pcfg.addresses.address_template or None, SSAP_FIELDS)
    m = auto_map([f.name() for f in layer.fields()], defs)
    m.update({k: v for k, v in pcfg.streets.address_mapping.items() if v})
    sid = layer.fields().lookupField(pcfg.streets.street_id_field)
    if sid >= 0:
        m["StreetID"] = layer.fields().at(sid).name()
    return m


def numbering_settings(a, community: str = "") -> NB.NumberingSettings:
    """NumberingSettings from AddressSettings, with a per-community override applied."""
    o = (a.overrides or {}).get((community or "").upper(), {}) if community else {}

    def g(k, default):
        return o.get(k, default) if o.get(k, None) not in (None, "") else default
    method = g("method", a.method)
    return NB.NumberingSettings(
        method=method,
        metres_per_number=to_meters(float(g("distance_per_number", a.distance_per_number)),
                                    g("distance_units", a.distance_units)),
        start_number=int(g("start_number", a.start_number)),
        frontage_m=to_meters(float(g("frontage_interval", a.frontage_interval)), g("distance_units", a.distance_units)),
        frontage_step=int(g("frontage_step", a.frontage_step)),
        rounding=a.rounding, order_check=a.order_check, collision=a.collision)


# ---------------------------------------------------------------------------
class AddressContext:
    """Resolved layers + field accessors for one addressing session."""

    def __init__(self, pcfg: ProjectConfig, centerline_layer: QgsVectorLayer, centerline_cfg: PluginConfig = None):
        self.pcfg = pcfg
        s, a = pcfg.streets, pcfg.addresses
        self.rcl = centerline_layer
        if self.rcl is None:
            raise ConfigError("Choose the centerline layer at the top of the window.")
        cfg = centerline_cfg or load_from_layer(self.rcl) or PluginConfig(
            mapping=auto_map([f.name() for f in self.rcl.fields()]))
        self.rcl_cfg = _with_street_id(cfg, self.rcl, s.street_id_field)
        self.rcl_acc = FieldAccessor(self.rcl, self.rcl_cfg)
        self.addr = layer_for(s, "address")
        if self.addr is None:
            raise ConfigError("Choose the address point layer in the Addresses window.")
        acfg = PluginConfig()
        acfg.mapping = address_mapping(self.addr, pcfg)
        self.addr_acc = FieldAccessor(self.addr, acfg)
        if not self.addr_acc.has("Add_Number"):
            raise ConfigError("The address layer has no address-number field (Add_Number).")
        self.dw = layer_for(a, "driveway")
        self.dw_acc = None
        if self.dw is not None:
            dcfg = PluginConfig()
            dcfg.mapping = dict(auto_map([f.name() for f in self.dw.fields()], DRIVEWAY_FIELDS),
                                **{k: v for k, v in a.driveway_mapping.items() if v})
            self.dw_acc = FieldAccessor(self.dw, dcfg)
        self.access = layer_for(a, "access")
        self.access_acc = None
        if self.access is not None:
            apcfg = PluginConfig()
            apcfg.mapping = auto_map([f.name() for f in self.access.fields()], ACCESS_FIELDS)
            self.access_acc = FieldAccessor(self.access, apcfg)
        self.master = layer_for(s, "master")
        self.repo = StreetRepository(self.master) if self.master is not None else None
        self.to_addr = _xf(self.rcl.crs(), self.addr.crs())
        self.from_addr = _xf(self.addr.crs(), self.rcl.crs())
        self.da = gu.make_distance_area(self.rcl.crs())
        self.tol = gu.metres_to_layer_units(max(self.rcl_cfg.ranging.snap_tolerance_m, 0.05), self.rcl.crs())
        self.edit = self.rcl_cfg.edit

    # -- streets -----------------------------------------------------------
    def street_of(self, feat) -> Tuple[str, Dict[str, str]]:
        """(StreetID or '', name parts) of a centerline feature; parts from the master when linked."""
        sid = N.clean(self.rcl_acc.get(feat, "StreetID")) if self.rcl_acc.has("StreetID") else ""
        if sid and self.repo is not None:
            rec = self.repo.by_id().get(sid)
            if rec is not None:
                return sid, dict(rec.parts)
        return sid, {p: N.clean(self.rcl_acc.get(feat, p)) for p in N.NAME_PARTS}

    def measure_for(self, feat) -> "StreetMeasure":
        sid, parts = self.street_of(feat)
        return StreetMeasure(self, sid, N.name_key(parts))


# ---------------------------------------------------------------------------
# Centerline segments per street, so a street is measured without reading
# every road in the county. One index per layer and field mapping, dropped as
# soon as the layer's data changes (edits, saves, undo, roll back).
_STREET_INDEX: Dict[tuple, tuple] = {}     # (layer id, kind) -> (signature, maps)
_INDEX_WATCHED: set = set()


def _drop_street_index(layer_id: str) -> None:
    for k in [k for k in _STREET_INDEX if k[0] == layer_id]:
        _STREET_INDEX.pop(k, None)


def _watch_for_index(layer) -> bool:
    """Drop the layer's indexes when its data changes. False when it cannot be watched."""
    lid = layer.id()
    if lid in _INDEX_WATCHED:
        return True
    signals = [getattr(layer, name, None) for name in ("dataChanged", "afterRollBack", "afterCommitChanges")]
    if signals[0] is None:
        return False
    for sig in signals:
        if sig is not None:
            sig.connect(lambda *_a, lid=lid: _drop_street_index(lid))
    destroyed = getattr(layer, "willBeDeleted", None)
    if destroyed is not None:
        destroyed.connect(lambda lid=lid: (_drop_street_index(lid), _INDEX_WATCHED.discard(lid)))
    _INDEX_WATCHED.add(lid)
    return True


def _no_geometry_request():
    req = QgsFeatureRequest()
    no_geom = getattr(getattr(QgsFeatureRequest, "Flag", QgsFeatureRequest), "NoGeometry", None)
    if no_geom is not None and hasattr(req, "setFlags"):
        req.setFlags(no_geom)
    return req


def _name_index(layer, acc, kind: str):
    """(by StreetID, by name key): feature ids in layer order. Cached until the layer changes."""
    signature = tuple(sorted((n, acc.index[n]) for n in ("StreetID",) + tuple(N.NAME_PARTS) if acc.has(n)))
    entry = _STREET_INDEX.get((layer.id(), kind))
    if entry is not None and entry[0] == signature:
        return entry[1]
    by_sid: Dict[str, List[Hashable]] = {}
    by_key: Dict[str, List[Hashable]] = {}
    has_sid = acc.has("StreetID")
    for f in layer.getFeatures(_no_geometry_request()):
        if has_sid:
            by_sid.setdefault(N.clean(acc.get(f, "StreetID")), []).append(f.id())
        by_key.setdefault(N.name_key({p: acc.get(f, p) for p in N.NAME_PARTS}), []).append(f.id())
    maps = (by_sid, by_key)
    if _watch_for_index(layer):
        _STREET_INDEX[(layer.id(), kind)] = (signature, maps)
    return maps


def _ordered_features(layer, ids: List[Hashable]) -> list:
    """The features with these ids, in the order a full read of the layer gives."""
    if not ids:
        return []
    rank = {fid: i for i, fid in enumerate(ids)}
    feats = list(layer.getFeatures(QgsFeatureRequest().setFilterFids(list(ids))))
    feats.sort(key=lambda f: rank.get(f.id(), len(rank)))
    return feats


def street_segment_ids(ctx: "AddressContext", sid: str, key: str) -> List[Hashable]:
    """Ids of the centerlines of one street (by StreetID, else by name), in layer order."""
    by_sid, by_key = _name_index(ctx.rcl, ctx.rcl_acc, "rcl")
    if sid:
        return list(by_sid.get(sid, ()))
    if not key:
        return []
    return list(by_key.get(key, ()))


def street_address_ids(ctx: "AddressContext", sid: str, key: str) -> List[Hashable]:
    """Ids of the address points on one street (by StreetID when mapped, else by name), in layer order."""
    by_sid, by_key = _name_index(ctx.addr, ctx.addr_acc, "addr")
    if sid and ctx.addr_acc.has("StreetID"):
        return list(by_sid.get(sid, ()))
    return list(by_key.get(key, ()))


def driveway_ids_for_site(ctx: "AddressContext", nguid) -> List[Hashable]:
    """Ids of the driveways that carry this Site_NGUID, in layer order."""
    layer, acc = ctx.dw, ctx.dw_acc
    signature = (acc.index.get("Site_NGUID"),)
    entry = _STREET_INDEX.get((layer.id(), "dw"))
    if entry is None or entry[0] != signature:
        by_site: Dict[object, List[Hashable]] = {}
        for d in layer.getFeatures(_no_geometry_request()):
            by_site.setdefault(acc.get(d, "Site_NGUID"), []).append(d.id())
        entry = (signature, by_site)
        if _watch_for_index(layer):
            _STREET_INDEX[(layer.id(), "dw")] = entry
    return list(entry[1].get(nguid, ()))


class StreetMeasure:
    """Distance from the road origin along all segments of one street (StreetID, else same name)."""

    def __init__(self, ctx: AddressContext, sid: str, key: str):
        self.ctx = ctx
        self.sid = sid
        self.segs: Dict[Hashable, Tuple[QgsGeometry, float, float]] = {}   # fid -> (linear geom, length_m, start_m)
        infos = []
        geoms = {}
        for f in _ordered_features(ctx.rcl, street_segment_ids(ctx, sid, key)):
            if sid:
                if N.clean(ctx.rcl_acc.get(f, "StreetID")) != sid:
                    continue
            else:
                if N.name_key({p: ctx.rcl_acc.get(f, p) for p in N.NAME_PARTS}) != key or not key:
                    continue
            g = gu.linear_geometry(f.geometry())
            info = gu.line_info(g)
            if info.empty:
                continue
            ln = gu.length_m(ctx.da, f.geometry())
            geoms[f.id()] = (g, ln)
            infos.append(SegmentInfo(f.id(), info.start, info.end, ln, "street", info.start_bearing,
                                     info.end_bearing))
        assign_nodes(infos, ctx.tol)
        node_dist: Dict[int, float] = {}
        # A "run" is one connected stretch of the street (a branch belongs to the run it
        # leaves). Distances are only comparable inside one run: a separate piece of the
        # same street starts again at 0.
        self.run: Dict[Hashable, int] = {}
        node_run: Dict[int, int] = {}
        runs = 0
        for chain in build_chains(infos):
            first = chain[0].start_node
            d = node_dist.get(first, 0.0)
            if first in node_run:
                run = node_run[first]
            else:
                run = runs
                runs += 1
            for s in chain:
                node_dist.setdefault(s.start_node, d)
                node_run.setdefault(s.start_node, run)
                g, ln = geoms[s.fid]
                self.segs[s.fid] = (g, ln, d)
                self.run[s.fid] = run
                d += ln
                node_dist.setdefault(s.end_node, d)
                node_run.setdefault(s.end_node, run)

    def locate(self, pt: QgsPointXY, only_fid: Optional[Hashable] = None):
        """-> (distance_m, fid, frac, (dx, dy) road direction, sq. distance) or None."""
        best = None
        for fid, (g, ln, start) in self.segs.items():
            if only_fid is not None and fid != only_fid:
                continue
            sqr, closest, after, _left = g.closestSegmentWithContext(pt)
            if sqr < 0:
                continue
            if best is None or sqr < best[0]:
                best = (sqr, fid, closest, after, g, ln, start)
        if best is None:
            return None
        sqr, fid, closest, after, g, ln, start = best
        planar = g.length() or 1e-12
        frac = max(0.0, min(1.0, g.lineLocatePoint(QgsGeometry.fromPointXY(QgsPointXY(closest))) / planar))
        a, b = g.vertexAt(after - 1), g.vertexAt(after)
        return start + frac * ln, fid, frac, (b.x() - a.x(), b.y() - a.y()), sqr


# ---------------------------------------------------------------------------
@dataclass
class AddressPlan:
    segment_fid: Hashable
    street_id: str
    parts: Dict[str, str]
    side: str
    parity: str
    distance_m: float
    frac: float
    access: QgsPointXY                    # centerline CRS
    rooftop: QgsPointXY                   # centerline CRS
    vertices: List[QgsPointXY]            # driveway bends (centerline CRS)
    result: NB.NumberResult
    community: Dict[str, str] = field(default_factory=dict)       # community key -> value
    community_notes: List[str] = field(default_factory=list)
    side_updates: Dict[str, str] = field(default_factory=dict)  # empty centerline L/R fields to fill
    rcl_nguid: str = ""
    parent_driveway: str = ""
    messages: List[str] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return N.full_name(self.parts)


def snap_access(ctx: AddressContext, pt: QgsPointXY) -> Optional[Tuple[Hashable, QgsPointXY]]:
    """Nearest point on a centerline within the snapping tolerance … or the nearest centerline overall."""
    r = gu.metres_to_layer_units(200.0, ctx.rcl.crs())
    rect = QgsRectangle(pt.x() - r, pt.y() - r, pt.x() + r, pt.y() + r)
    best = None
    for f in ctx.rcl.getFeatures(QgsFeatureRequest().setFilterRect(rect).setNoAttributes()):
        g = f.geometry()
        if g.isNull() or g.isEmpty():
            continue
        sqr, closest, _a, _l = g.closestSegmentWithContext(pt)
        if sqr >= 0 and (best is None or sqr < best[0]):
            best = (sqr, f.id(), QgsPointXY(closest))
    return None if best is None else (best[1], best[2])


def plan_address(ctx: AddressContext, access: QgsPointXY, rooftop: QgsPointXY,
                 vertices: Sequence[QgsPointXY] = (), segment_fid: Optional[Hashable] = None,
                 exclude_address: Optional[Hashable] = None, parent_driveway: str = "") -> AddressPlan:
    """Work out the number, name and community values for a new (or re-addressed) point."""
    if segment_fid is None:
        snapped = snap_access(ctx, access)
        if snapped is None:
            raise ConfigError("No centerline near the access point.")
        segment_fid, access = snapped
    feat = next(ctx.rcl.getFeatures(QgsFeatureRequest().setFilterFid(segment_fid)), None)
    if feat is None:
        raise ConfigError("Centerline not found.")
    sid, parts = ctx.street_of(feat)
    if not N.name_key(parts):
        raise ConfigError("This centerline has no street name – name it (Streets tab) before addressing.")
    measure = ctx.measure_for(feat)
    loc = measure.locate(access, only_fid=segment_fid)
    if loc is None:
        raise ConfigError("Could not measure along this street.")
    _street_dist, _fid, frac, (dx, dy), _sq = loc
    # Left and right come from the direction the segment was drawn.
    side = NB.side_of(dx, dy, rooftop.x() - access.x(), rooftop.y() - access.y())
    if side is None:
        raise ConfigError("The rooftop lies on the road line – click beside the road.")
    side_name = "left" if side == "L" else "right"
    a = ctx.pcfg.addresses
    # Odd / even comes from the settings (odd on the left, even on the right by default).
    parity = a.left_parity if side == "L" else a.right_parity
    parity_note = ""
    if ctx.rcl_acc.has(SIDE_FIELDS[side]["parity"]):
        seg_par = normalize_parity(ctx.rcl_acc.get(feat, SIDE_FIELDS[side]["parity"]), None)
        if seg_par == "Z":
            raise ConfigError(f"The {side_name} side of this segment is marked as having no addresses (parity Z).")
        if seg_par in ("O", "E") and seg_par != normalize_parity(parity, None):
            if getattr(a, "segment_parity_wins", False):
                parity = seg_par
            else:
                word = {"O": "odd", "E": "even"}
                parity_note = (f"This segment's {SIDE_FIELDS[side]['parity']} says {word[seg_par]}, but your "
                               f"settings put {word.get(normalize_parity(parity, ''), 'any')} numbers on the "
                               f"{side_name}. The settings were used. Re-range the segment to match.")
    community, notes, side_updates = community_from_segment(ctx, feat, side, frac)
    settings = numbering_settings(a, community.get(a.override_field, ""))
    seg_from, seg_to = ctx.rcl_acc.get_int(feat, SIDE_FIELDS[side]["from"]), \
        ctx.rcl_acc.get_int(feat, SIDE_FIELDS[side]["to"])
    # Everything is measured on the segment that was clicked: the distance from its first
    # point, starting at its From number on that side (the start number when it has none).
    seg_len = measure.segs[segment_fid][1] if segment_fid in measure.segs else 0.0
    dist = frac * seg_len
    base_note = ""
    if seg_from:
        settings = replace(settings, start_number=int(seg_from))
    elif settings.method != NB.M_INTERPOLATE:
        base_note = (f"This segment has no From number on the {side_name} side, so numbering starts at "
                     f"{settings.start_number}.")
    on_street = street_addresses(ctx, sid, parts, side, measure, exclude_address)
    neighbors = [NB.Neighbor(item.seg_distance, item.number, item.suffix)
                 for item in on_street if item.segment == segment_fid]
    res = NB.calculate(settings, parity, neighbors, dist, frac, seg_from, seg_to)
    plan = AddressPlan(segment_fid, sid, parts, side, parity, dist, frac, access, rooftop, list(vertices), res,
                       community, notes, side_updates, rcl_nguid=str(ctx.rcl_acc.get(feat, "RCL_NGUID") or ""),
                       parent_driveway=parent_driveway)
    for note in (parity_note, base_note, res.message):
        if note:
            plan.messages.append(note)
    if res.number is not None and not res.suffix:
        elsewhere = [item for item in on_street if item.segment != segment_fid and item.number == res.number
                     and not item.suffix]
        if elsewhere:
            plan.messages.append(f"{res.number} is already used on another segment of this street. "
                                 "Check the ranges with Validate.")
    if seg_from is not None and seg_to is not None and res.number is not None and (seg_from or seg_to):
        lo, hi = sorted((seg_from, seg_to))
        if not (lo <= res.number <= hi):
            plan.messages.append(f"{res.number} is outside this segment's range {seg_from}–{seg_to} on that side "
                                 "– consider re-ranging the segment.")
    return plan


def _beside(access: QgsPointXY, dx: float, dy: float, side: str, offset: float) -> QgsPointXY:
    """A point a short step to the left or right of the road direction."""
    length = math.hypot(dx, dy) or 1.0
    sign = 1 if side == "L" else -1
    return QgsPointXY(access.x() - sign * dy / length * offset, access.y() + sign * dx / length * offset)


def house_number_at(ctx: AddressContext, segment_fid: Hashable, access: QgsPointXY,
                    hint: Optional[QgsPointXY] = None, parent_driveway: str = "") -> int:
    """
    The house number where the road was clicked.

    The click position along the road fixes the number. ``hint`` is the raw
    click and picks the side when it falls off the centerline. The other side
    is tried when that side cannot be numbered. Place units keeps this number
    and only advances the unit.
    """
    feat = next(ctx.rcl.getFeatures(QgsFeatureRequest().setFilterFid(segment_fid)), None)
    if feat is None:
        raise ConfigError("Centerline not found.")
    measure = ctx.measure_for(feat)
    loc = measure.locate(access, only_fid=segment_fid)
    if loc is None:
        raise ConfigError("Could not measure along this street.")
    _dist, _fid, _frac, (dx, dy), _sq = loc
    hint_side = None
    if hint is not None:
        hint_side = NB.side_of(dx, dy, hint.x() - access.x(), hint.y() - access.y())
    sides = [hint_side] if hint_side else []
    for side in ("L", "R"):
        if side not in sides:
            sides.append(side)
    offset = gu.metres_to_layer_units(3.0, ctx.rcl.crs())
    if offset <= 0:
        offset = 3.0
    errors = []
    for side in sides:
        try:
            plan = plan_address(ctx, access, _beside(access, dx, dy, side, offset), [], segment_fid,
                                parent_driveway=parent_driveway)
        except ConfigError as exc:
            errors.append(str(exc))
            continue
        if plan.result.number is None or plan.result.needs_choice:
            errors.append(plan.result.message or "This spot needs a house number typed in.")
            continue
        return int(plan.result.number)
    raise ConfigError(errors[-1] if errors else "Could not number this point on the road.")


def house_numbers_by_side(ctx: AddressContext, segment_fid: Hashable, access: QgsPointXY,
                          parent_driveway: str = "") -> Dict[str, Optional[int]]:
    """
    The house number at this spot on the road, for each side: ``{"L": 151, "R": 150}``.

    A road click sits on the centerline, so it cannot say which side the
    building is on. Place units works out both and lets the first unit click
    (which is beside the road) pick the side. A side that cannot be numbered
    (parity Z, a number that needs a choice) is None.
    """
    feat = next(ctx.rcl.getFeatures(QgsFeatureRequest().setFilterFid(segment_fid)), None)
    if feat is None:
        raise ConfigError("Centerline not found.")
    loc = ctx.measure_for(feat).locate(access, only_fid=segment_fid)
    if loc is None:
        raise ConfigError("Could not measure along this street.")
    _dist, _fid, _frac, (dx, dy), _sq = loc
    offset = gu.metres_to_layer_units(3.0, ctx.rcl.crs())
    if offset <= 0:
        offset = 3.0
    out: Dict[str, Optional[int]] = {}
    for side in ("L", "R"):
        try:
            plan = plan_address(ctx, access, _beside(access, dx, dy, side, offset), [], segment_fid,
                                parent_driveway=parent_driveway)
        except ConfigError:
            out[side] = None
            continue
        ok = plan.result.number is not None and not plan.result.needs_choice
        out[side] = int(plan.result.number) if ok else None
    return out


def parity_matches(number: int, parity: str) -> bool:
    """True when ``number`` is odd for O and even for E. Both / unknown always match."""
    p = (parity or "").strip().upper()
    if p == "O":
        return number % 2 == 1
    if p == "E":
        return number % 2 == 0
    return True


@dataclass
class StreetAddress:
    segment: Hashable        # centerline the address is measured on
    seg_distance: float      # metres from that segment's first point
    street_distance: float   # metres from the start of the connected street
    number: int
    suffix: str = ""


def street_addresses(ctx: AddressContext, sid: str, parts, side: str, measure: StreetMeasure,
                     exclude: Optional[Hashable] = None) -> List[StreetAddress]:
    """Addresses already on this street and side, with the segment each one sits on."""
    out = []
    for f, num, suf, loc in _street_address_locations(ctx, sid, parts, side, measure, exclude):
        seg_len = measure.segs[loc[1]][1] if loc[1] in measure.segs else 0.0
        out.append(StreetAddress(loc[1], loc[2] * seg_len, loc[0], num, suf))
    return out


def existing_neighbors(ctx: AddressContext, sid: str, parts, side: str, measure: StreetMeasure,
                       exclude: Optional[Hashable] = None, segment: Optional[Hashable] = None) -> List[NB.Neighbor]:
    """
    Addresses already on this street and side.

    With ``segment``, only the addresses on that segment, measured from its first
    point. Without it, every address on the street, measured along the street.
    """
    out = []
    for item in street_addresses(ctx, sid, parts, side, measure, exclude):
        if segment is None:
            out.append(NB.Neighbor(item.street_distance, item.number, item.suffix))
        elif item.segment == segment:
            out.append(NB.Neighbor(item.seg_distance, item.number, item.suffix))
    return out


def _street_address_locations(ctx: AddressContext, sid: str, parts, side: str, measure: StreetMeasure,
                              exclude: Optional[Hashable] = None):
    """(feature, number, suffix, measure.locate result) for each address on this street and side."""
    acc = ctx.addr_acc
    key = N.name_key(parts)
    for f in _ordered_features(ctx.addr, street_address_ids(ctx, sid, key)):
        if f.id() == exclude:
            continue
        if sid and acc.has("StreetID"):
            if N.clean(acc.get(f, "StreetID")) != sid:
                continue
        elif N.name_key({p: acc.get(f, p) for p in N.NAME_PARTS}) != key:
            continue
        num, suf = parse_number(acc.get(f, "Add_Number"), acc.get(f, "AddNum_Suf") if acc.has("AddNum_Suf") else None)
        if num is None:
            continue
        acc_pt = stored_access(ctx, f)
        roof = ctx.from_addr.transform(_pt(f.geometry())) if f.hasGeometry() else None
        probe = acc_pt or roof
        if probe is None:
            continue
        loc = measure.locate(QgsPointXY(probe))
        if loc is None:
            continue
        s = N.clean(acc.get(f, "Addr_Side")).upper() if acc.has("Addr_Side") else ""
        if s not in ("L", "R") and roof is not None:
            s = NB.side_of(loc[3][0], loc[3][1], roof.x() - probe.x(), roof.y() - probe.y()) or ""
        if s == side:
            yield f, num, suf, loc


def _metres_between(ctx: AddressContext, a: QgsPointXY, b: QgsPointXY) -> float:
    try:
        raw = ctx.da.measureLine(QgsPointXY(a), QgsPointXY(b))
        return float(ctx.da.convertLengthMeasurement(raw, Qgis.DistanceUnit.Meters))
    except Exception:  # noqa: BLE001 - the fake distance tool has no measureLine
        return math.hypot(a.x() - b.x(), a.y() - b.y())


def stored_access(ctx: AddressContext, f) -> Optional[QgsPointXY]:
    """Access point of an address (centerline CRS): stored fields, else its driveway's first vertex."""
    acc = ctx.addr_acc
    if acc.has("Access_X") and acc.has("Access_Y"):
        x, y = py_value(f.attribute(acc.index["Access_X"])), py_value(f.attribute(acc.index["Access_Y"]))
        if x not in (None, "") and y not in (None, ""):
            pt = ctx.from_addr.transform(QgsPointXY(float(x), float(y)))
            if _stored_access_near_address(ctx, f, pt):
                return pt
    if ctx.dw is not None and ctx.dw_acc is not None and ctx.dw_acc.has("Site_NGUID") and acc.has("Site_NGUID"):
        nguid = acc.get(f, "Site_NGUID")
        if nguid:
            for d in _ordered_features(ctx.dw, driveway_ids_for_site(ctx, nguid)):
                if ctx.dw_acc.get(d, "Site_NGUID") == nguid and d.hasGeometry():
                    pl = gu.polylines(d.geometry())
                    if pl:
                        return _xf(ctx.dw.crs(), ctx.rcl.crs()).transform(QgsPointXY(pl[0][0]))
    return None


def _stored_access_near_address(ctx: AddressContext, feature, access: QgsPointXY) -> bool:
    """False when the stored access is kilometres from the address.

    Those coordinates were rounded to a whole degree. Using them puts every
    house at the start of the street, and the next number is then forced up
    to the last house on the road.
    """
    if not feature.hasGeometry():
        return True
    try:
        roof = ctx.from_addr.transform(_pt(feature.geometry()))
    except Exception:  # noqa: BLE001 - keep the stored point when the address geometry cannot be read
        return True
    if roof is None:
        return True
    return _metres_between(ctx, roof, access) <= _ACCESS_LIMIT_M


# ---------------------------------------------------------------------------
def offset_point(geom, fraction: float, side: str, offset: float) -> Optional[QgsPointXY]:
    """A point `offset` layer units to the left or right of `fraction` along a line."""
    g = gu.linear_geometry(geom)
    if g.isNull() or g.isEmpty():
        return None
    total = g.length()
    if total <= 0:
        return None
    t = max(0.0, min(1.0, fraction))
    p = g.interpolate(total * t).asPoint()
    q = g.interpolate(min(total, total * t + total * 0.01)).asPoint()
    dx, dy = q.x() - p.x(), q.y() - p.y()
    ln = math.hypot(dx, dy) or 1.0
    sign = 1 if side == "L" else -1
    return QgsPointXY(p.x() - sign * dy / ln * offset, p.y() + sign * dx / ln * offset)


def community_from_segment(ctx: AddressContext, feat, side: str, fraction: float,
                           offset_m: float = 3.0) -> Tuple[Dict[str, str], List[str], Dict[str, str]]:
    """
    ZIP, MSAG, ESN and the other community values for one side of a centerline.

    The address copies the value stored on that side of the road. An empty
    side is calculated from the polygon just beside the road, then written
    onto the centerline and copied. The house point itself is not sampled.
    The third dict is the empty centerline fields this calculation fills.
    """
    rules = _side_rules(ctx.pcfg)
    samples: Dict[str, Tuple[Dict[str, str], List[str]]] = {}
    if rules:
        off = gu.metres_to_layer_units(offset_m, ctx.rcl.crs())
        t = max(0.02, min(0.98, 0.5 if fraction is None else fraction))
        for s in ("L", "R"):
            samples[s] = boundary_side_values(ctx, feat.geometry(), t, s, off)
    community: Dict[str, str] = {}
    updates: Dict[str, str] = {}
    needed = set()
    for key, (_afield, left, right) in COMMUNITY_FIELDS.items():
        for s, fld in (("L", left), ("R", right)):
            has = ctx.rcl_acc.has(fld)
            current = ctx.rcl_acc.get(feat, fld) if has else None
            if current not in (None, ""):
                value = str(current)
            else:
                if s == side:
                    needed.add(key)
                value = samples.get(s, ({}, []))[0].get(key) or ""
                if value and has:
                    updates[fld] = value
            if s == side and value:
                community[key] = str(value)
    raw_notes = samples.get(side, ({}, []))[1]
    notes = [n for n in raw_notes if any(f"({key})" in n for key in needed)]
    return community, notes, updates


def _stamp_centerline_side(ctx: AddressContext, plan: AddressPlan) -> None:
    """Write side values that were empty when this address was planned."""
    if not plan.side_updates:
        return
    from ..layer_access import apply_changes
    with suppressed():
        apply_changes(ctx.rcl, ctx.rcl_acc, {plan.segment_fid: dict(plan.side_updates)}, "NENA: stamp road side")


def attribution_values(ctx: AddressContext, pt_rcl: QgsPointXY) -> Tuple[Dict[str, str], List[str]]:
    """Evaluate the boundary rules at a point (centerline CRS)."""
    out, notes = {}, []
    for rule in _side_rules(ctx.pcfg):
        value, note = _rule_value(ctx, rule, pt_rcl)
        if note:
            notes.append(note)
        if value not in (None, ""):
            out[rule.get("target", "")] = value
    _apply_outside_only(ctx, out)
    return out, notes


# A boundary that runs along the road (city limits) is often drawn on the
# right-of-way line, not the centerline. A point 3 m off the road then lands
# on the wrong side, so these rules vote over points spread out to depth_m.
_DEPTH_SAMPLES = 5


def boundary_side_values(ctx, geom, fraction: float, side: str, offset: float) -> Tuple[Dict[str, str], List[str]]:
    """
    Every boundary rule for one side of a line at `fraction`.

    A rule with ``depth_m`` samples several points from `offset` out to that
    depth and keeps the value most of them agree on; the nearest point breaks
    a tie. Other rules use the single point `offset` off the line.
    """
    out, notes = {}, []
    for rule in _side_rules(ctx.pcfg):
        votes, first_note = [], None
        for dist in _rule_depths(ctx, rule, offset):
            pt = offset_point(geom, fraction, side, dist)
            if pt is None:
                continue
            value, note = _rule_value(ctx, rule, pt)
            if note and first_note is None:
                first_note = note
            if value is not None:
                votes.append(value)
        if votes:
            best = max(votes, key=lambda v: (votes.count(v), -votes.index(v)))
            if best != "":
                out[rule.get("target", "")] = best
        elif first_note:
            notes.append(first_note)
    _apply_outside_only(ctx, out)
    return out, notes


def _rule_depths(ctx, rule: dict, offset: float) -> List[float]:
    try:
        depth_m = float(rule.get("depth_m") or 0)
    except (TypeError, ValueError):
        depth_m = 0.0
    deep = gu.metres_to_layer_units(depth_m, ctx.rcl.crs()) if depth_m > 0 else 0.0
    if deep <= offset:
        return [offset]
    step = (deep - offset) / (_DEPTH_SAMPLES - 1)
    return [offset + i * step for i in range(_DEPTH_SAMPLES)]


def _rule_value(ctx, rule: dict, pt_rcl: QgsPointXY) -> Tuple[Optional[str], Optional[str]]:
    """
    One rule at one point: (value, note). value is None when the point
    can't be decided (layer missing, overlap). Outside every polygon gives
    the rule's ``outside_value`` (e.g. "Unincorporated") when it has one.
    """
    target = rule.get("target", "")
    optional = bool(rule.get("optional"))
    lyr = layer_for_rule(rule)
    if lyr is None:
        return None, f"Boundary layer for {target} not found"
    idx = lyr.fields().lookupField(rule.get("source_field", ""))
    if idx < 0:
        return None, f"Field {rule.get('source_field')} not on {lyr.name()}"
    p = _xf(ctx.rcl.crs(), lyr.crs()).transform(pt_rcl)
    pg = QgsGeometry.fromPointXY(p)
    vals = []
    for f in lyr.getFeatures(QgsFeatureRequest().setFilterRect(pg.boundingBox().buffered(1e-9))):
        if f.hasGeometry() and f.geometry().contains(pg):
            vals.append(py_value(f.attribute(idx)))
    if not vals:
        outside = rule.get("outside_value")
        if outside is not None:
            return str(outside), None
        return None, (None if optional else f"No {lyr.name()} polygon here ({target})")
    if len(set(map(str, vals))) > 1:
        return None, f"Several {lyr.name()} polygons overlap here ({target})"
    return ("" if vals[0] in (None, "") else str(vals[0])), None


def _apply_outside_only(ctx, out: Dict[str, str]) -> None:
    """
    A rule marked ``outside_of`` (unincorporated community) only applies
    where that other rule gave its outside value; inside a city it is blank.
    """
    rules = _side_rules(ctx.pcfg)
    by_target = {r.get("target"): r for r in rules}
    for rule in rules:
        parent_key = rule.get("outside_of")
        if not parent_key:
            continue
        parent = by_target.get(parent_key)
        if parent is None or parent_key not in out:
            continue
        if out[parent_key] != str(parent.get("outside_value") or ""):
            out[rule.get("target", "")] = ""


def layer_for_rule(rule: dict):
    from ..project_config import resolve_layer
    return resolve_layer(rule.get("layer_id", ""), rule.get("layer_source", ""))


# ---------------------------------------------------------------------------
def nguid_agency(ctx: AddressContext, like: str = ""):
    """
    (agency, format) for new Site / driveway NGUIDs: the agency identifier
    from the settings; else the one the existing address-point, driveway or
    centerline NGUIDs use (``like``: the NGUID of the road being addressed,
    tried before scanning the layers), else DiscrpAgID. Looked up once per
    addressing session. (None, format) when there is nothing to take it from.
    """
    if ctx.edit.agency_id:
        return ctx.edit.agency_id.strip(), ctx.edit.nguid_format
    cached = getattr(ctx, "_nguid_agency", None)
    if cached:
        return cached
    from ..nguid import agency_from
    from ..split_ops import agency_from_layers
    agency, fmt = agency_from_layers([(ctx.addr, ctx.addr_acc, "Site_NGUID"),
                                      (ctx.dw, ctx.dw_acc, "DW_NGUID")], ctx.edit.nguid_format)
    if not agency and like:
        agency, fmt = agency_from([like])
    if not agency:
        agency, fmt = agency_from_layers([(ctx.rcl, ctx.rcl_acc, "RCL_NGUID"),
                                          (ctx.addr, ctx.addr_acc, "DiscrpAgID"),
                                          (ctx.rcl, ctx.rcl_acc, "DiscrpAgID")], ctx.edit.nguid_format)
    if agency:
        ctx._nguid_agency = (agency, fmt or ctx.edit.nguid_format)
        return ctx._nguid_agency
    return None, ctx.edit.nguid_format


def _new_nguid(ctx: AddressContext, kind: str, like: str = "") -> Optional[str]:
    agency, fmt = nguid_agency(ctx, like)
    if not agency:
        return None
    return make_nguid(local_id(ctx.edit.nguid_local_id, None), agency, fmt, kind)


def _new_access_id() -> str:
    from ..streets.names import new_street_id
    return new_street_id()


def _parent_access_id(ctx: AddressContext, parent_driveway: str) -> str:
    """AccessPointID already stored on the driveway this click branched from."""
    if not parent_driveway or ctx.dw is None or ctx.dw_acc is None or not ctx.dw_acc.has("AccessPointID"):
        return ""
    for feature in ctx.dw.getFeatures():
        if str(ctx.dw_acc.get(feature, "DW_NGUID") or "") != parent_driveway:
            continue
        return str(ctx.dw_acc.get(feature, "AccessPointID") or "").strip()
    return ""


def _access_candidates(ctx: AddressContext, point: QgsPointXY):
    """(access id, x, y) near ``point`` in the access layer's CRS."""
    layer = ctx.access
    acc = ctx.access_acc
    if layer is None or acc is None or not acc.has("AccessPointID"):
        return [], None, 0.0
    origin = _xf(ctx.rcl.crs(), layer.crs()).transform(point)
    tol = gu.metres_to_layer_units(max(ctx.rcl_cfg.ranging.snap_tolerance_m, 0.05), layer.crs())
    rect = QgsRectangle(origin.x() - tol, origin.y() - tol, origin.x() + tol, origin.y() + tol)
    found = []
    for feature in layer.getFeatures(QgsFeatureRequest().setFilterRect(rect)):
        geom = feature.geometry()
        if geom is None or geom.isNull() or geom.isEmpty():
            continue
        located = attempt(geom.asPoint)          # a bad geometry is not a match
        if located is None:
            continue
        aid = str(acc.get(feature, "AccessPointID") or "").strip()
        if aid:
            found.append((aid, located.x(), located.y()))
    return found, origin, tol


def _forget_access(layer, fid) -> None:
    """Remove an access point this command created after the address itself was refused."""
    if layer is None or fid is None:
        return
    with suppressed_guard():
        try:
            ensure_editable(layer)
            layer.beginEditCommand("NENA: undo access point")
            if layer.deleteFeature(fid):
                layer.endEditCommand()
            else:
                layer.destroyEditCommand()
        except Exception:  # noqa: BLE001 - the original error is the one to report
            attempt(layer.destroyEditCommand)


def access_seat(ctx: AddressContext, plan: AddressPlan) -> QgsPointXY:
    """Point 10 feet straight off the road click, on the address side (centerline CRS).

    The click stays the place the house number is measured. The access-point
    feature sits beside the road so it is not drawn on top of the centerline.
    """
    origin = QgsPointXY(plan.access)
    direction = _segment_direction(ctx, plan)
    if direction is None:
        return origin
    return _move_metres(ctx, origin, direction[0], direction[1], _ACCESS_OFFSET_M)


def _segment_direction(ctx: AddressContext, plan: AddressPlan):
    """Unit vector straight off the clicked segment, toward ``plan.side``."""
    feat = next(ctx.rcl.getFeatures(QgsFeatureRequest().setFilterFid(plan.segment_fid)), None)
    if feat is None or not feat.hasGeometry():
        return None
    geom = gu.linear_geometry(feat.geometry())
    if geom.isNull() or geom.isEmpty():
        return None
    total = geom.length()
    if total <= 0:
        return None
    t = max(0.0, min(1.0, plan.frac))
    here = geom.interpolate(total * t).asPoint()
    step = max(total * 0.01, 1e-9)
    ahead = geom.interpolate(min(total, total * t + step)).asPoint()
    dx, dy = ahead.x() - here.x(), ahead.y() - here.y()
    if dx == 0 and dy == 0:
        back = geom.interpolate(max(0.0, total * t - step)).asPoint()
        dx, dy = here.x() - back.x(), here.y() - back.y()
    length = math.hypot(dx, dy)
    if length == 0:
        return None
    sign = 1 if plan.side == "L" else -1
    return -sign * dy / length, sign * dx / length


def _move_metres(ctx: AddressContext, origin: QgsPointXY, ux: float, uy: float, metres: float) -> QgsPointXY:
    """Move ``metres`` along a layer-space unit vector. Geographic layers use a measured step."""
    step = gu.metres_to_layer_units(1.0, ctx.rcl.crs())
    if step <= 0:
        step = 1.0
    probe = QgsPointXY(origin.x() + ux * step, origin.y() + uy * step)
    measured = _metres_between(ctx, origin, probe)
    if measured <= 1e-9:
        measured = step
    scale = metres / measured * step
    return QgsPointXY(origin.x() + ux * scale, origin.y() + uy * scale)


def link_access(ctx: AddressContext, point: QgsPointXY, street_id: str = "", rcl_nguid: str = "",
                side: str = "", parent_driveway: str = ""):
    """
    Reuse an access point at this spot, or create one.

    Returns ``(AccessPointID, new feature id or None)``. An empty id means the
    project has no access-point layer (or it has no AccessPointID field). The
    new feature, when there is one, is already in that layer's edit buffer.
    """
    parent_id = _parent_access_id(ctx, parent_driveway)
    if parent_id:
        return parent_id, None
    candidates, origin, tol = _access_candidates(ctx, point)
    found = nearest_access(candidates, origin.x(), origin.y(), tol) if origin is not None else ""
    if found:
        return found, None
    layer = ctx.access
    acc = ctx.access_acc
    if layer is None or acc is None or not acc.has("AccessPointID") or origin is None:
        return "", None
    geom = QgsGeometry.fromPointXY(origin)
    if QgsWkbTypes.isMultiType(layer.wkbType()):
        geom.convertToMultiType()
    aid = _new_access_id()
    stamp = datetime.now().replace(microsecond=0)
    vals = {"AccessPointID": aid, "RCL_NGUID": rcl_nguid or None, "StreetID": street_id or None,
            "RoadSide": side or None, "DateUpdate": stamp}
    attrs = {acc.index[k]: acc.coerce(k, v) for k, v in vals.items() if acc.has(k)}
    feat = QgsVectorLayerUtils.createFeature(layer, geom, attrs, layer.createExpressionContext())
    ensure_editable(layer)
    layer.beginEditCommand(ACCESS_COMMAND)
    if not layer.addFeature(feat):
        layer.destroyEditCommand()
        raise ConfigError("The access point layer refused the new point.")
    layer.endEditCommand()
    return aid, feat.id()


def address_lookup_defaults(ctx: AddressContext) -> Dict[str, str]:
    """Defaults ticked in the lookup tables for address fields (status, type, placement …)."""
    try:
        from ..lookups import field_defaults
        return field_defaults(ctx.pcfg, "address")
    except Exception:  # noqa: BLE001 - no lookup tables
        return {}


def commit_address(ctx: AddressContext, plan: AddressPlan, number: int, suffix: str = "", unit: str = "",
                   placement: Optional[str] = None, create_driveway: Optional[bool] = None, *,
                   unit_type: str = "", building: str = "", address_status: str = "",
                   address_type: str = "") -> Dict[str, Hashable]:
    """Write the address point (and driveway) into the edit buffers. Returns their ids."""
    a = ctx.pcfg.addresses
    acc = ensure_full_name_mapping(ctx.addr, ctx.addr_acc)
    ctx.addr_acc = acc
    stamp = datetime.now().replace(microsecond=0)
    roof_addr = ctx.to_addr.transform(plan.rooftop)
    # House number and Access_X/Y stay on the road click. The access-point
    # feature is 10 feet straight off that click, on this side of the road.
    access_addr = ctx.to_addr.transform(plan.access)
    site_nguid = _new_nguid(ctx, "SSAP", plan.rcl_nguid or "")
    seat = plan.access if plan.parent_driveway else access_seat(ctx, plan)
    aid, access_fid = link_access(ctx, seat, plan.street_id, plan.rcl_nguid, plan.side,
                                  plan.parent_driveway)
    vals = dict(plan.parts)
    vals.update({"Add_Number": int(number), "AddNum_Suf": suffix or None, "Unit": unit or None,
                 "Placement": placement or None, "StreetID": plan.street_id or None,
                 "FullName": plan.full_name or None,
                 "RCL_NGUID": plan.rcl_nguid or None, "Access_X": access_addr.x(), "Access_Y": access_addr.y(),
                 "Addr_Side": plan.side, "Site_NGUID": site_nguid, "DateUpdate": stamp,
                 "DiscrpAgID": nguid_agency(ctx, plan.rcl_nguid or "")[0] or None})
    if aid:
        vals["AccessPointID"] = aid
    if unit_type:
        vals["UnitType"] = unit_type
    if building:
        vals["Building"] = building
    if address_status:
        vals["AddressStatus"] = address_status
    if address_type:
        vals["AddressType"] = address_type
    for key, value in plan.community.items():
        vals[COMMUNITY_FIELDS[key][0]] = value
    for field_name, value in address_fallbacks(ctx.rcl_cfg, plan.side, plan.community).items():
        if vals.get(field_name) in (None, ""):
            vals[field_name] = value
    # What the user picked wins, then the side of the road, then the lookup-table default.
    for field_name, value in address_lookup_defaults(ctx).items():
        if vals.get(field_name) in (None, ""):
            vals[field_name] = value
    if vals.get("Placement") in (None, ""):
        vals["Placement"] = a.placement or None
    wgs = _xf(ctx.rcl.crs(), QgsCoordinateReferenceSystem("EPSG:4326")).transform(plan.rooftop)
    vals["Longitude"], vals["Latitude"] = round(wgs.x(), 7), round(wgs.y(), 7)
    geom = QgsGeometry.fromPointXY(roof_addr)
    if QgsWkbTypes.isMultiType(ctx.addr.wkbType()):
        geom.convertToMultiType()
    attrs = {acc.index[k]: acc.coerce(k, v) for k, v in vals.items() if acc.has(k)}
    feat = QgsVectorLayerUtils.createFeature(ctx.addr, geom, attrs, ctx.addr.createExpressionContext())
    # Access point first, so Undo last address deletes the address before the access point.
    created = []
    if access_fid is not None and ctx.access is not None:
        created.append((ctx.access.id(), access_fid))
    address_ok = False
    try:
        with suppressed():
            ensure_editable(ctx.addr)
            ctx.addr.beginEditCommand(ADDRESS_COMMAND)
            if not ctx.addr.addFeature(feat):
                ctx.addr.destroyEditCommand()
                raise ConfigError("The address layer refused the new point.")
            ctx.addr.endEditCommand()
            created.append((ctx.addr.id(), feat.id()))
            address_ok = True
            out = {"address": feat.id(), "access": aid or None, "access_created": access_fid is not None,
                   "no_site_nguid": acc.has("Site_NGUID") and not site_nguid}
            # Place address never draws a driveway. Callers pass True only for
            # Place units, when that window's driveway box is ticked.
            want_dw = bool(create_driveway)
            if want_dw and ctx.dw is not None:
                out["driveway"] = _write_driveway(ctx, plan, site_nguid, stamp, aid)
                created.append((ctx.dw.id(), out["driveway"]))
        UNDO_STACK.append(created)
        del UNDO_STACK[:-50]
    except Exception:
        if address_ok:
            UNDO_STACK.append(created)
            del UNDO_STACK[:-50]
        elif access_fid is not None:
            _forget_access(ctx.access, access_fid)
        raise
    _stamp_centerline_side(ctx, plan)
    return out


def _write_driveway(ctx: AddressContext, plan: AddressPlan, site_nguid, stamp, access_id: str = "") -> Hashable:
    xf = _xf(ctx.rcl.crs(), ctx.dw.crs())
    if plan.parent_driveway and plan.vertices:
        # a spur off a shared driveway: starts where the user clicked on that driveway (first vertex)
        chain = list(plan.vertices) + [plan.rooftop]
    else:
        chain = [plan.access] + list(plan.vertices) + [plan.rooftop]
    pts = [xf.transform(p) for p in chain]
    geom = QgsGeometry.fromPolylineXY(pts)
    if QgsWkbTypes.isMultiType(ctx.dw.wkbType()):
        geom.convertToMultiType()
    acc = ctx.dw_acc
    vals = {"DW_NGUID": _new_nguid(ctx, "DW", plan.rcl_nguid or ""), "Site_NGUID": site_nguid,
            "RCL_NGUID": plan.rcl_nguid or None,
            "StreetID": plan.street_id or None, "Parent_DW": plan.parent_driveway or None,
            "AccessPointID": access_id or None, "DateUpdate": stamp}
    attrs = {acc.index[k]: acc.coerce(k, v) for k, v in vals.items() if acc.has(k)}
    feat = QgsVectorLayerUtils.createFeature(ctx.dw, geom, attrs, ctx.dw.createExpressionContext())
    ensure_editable(ctx.dw)
    ctx.dw.beginEditCommand(DRIVEWAY_COMMAND)
    if not ctx.dw.addFeature(feat):
        ctx.dw.destroyEditCommand()
        raise ConfigError("The driveway layer refused the new line.")
    ctx.dw.endEditCommand()
    return feat.id()


def _distinct_chain(points: Sequence[QgsPointXY]) -> List[QgsPointXY]:
    chain = []
    for pt in points:
        if pt is None:
            continue
        if chain and pt.x() == chain[-1].x() and pt.y() == chain[-1].y():
            continue
        chain.append(pt)
    return chain


def commit_driveway(ctx: AddressContext, access: QgsPointXY, vertices: Sequence[QgsPointXY] = (),
                    segment_fid: Optional[Hashable] = None, parent_driveway: str = "") -> Hashable:
    """
    Write a driveway that is not yet tied to an address.

    The line starts at the access point (the centerline, or the click on an
    existing driveway). Later address points link to it by snapping to it.
    This is its own undo step. It is not part of *Undo last address*.
    """
    if ctx.dw is None or ctx.dw_acc is None:
        raise ConfigError("Choose a driveway layer in the Addresses window first.")
    chain = _distinct_chain([access] + list(vertices))
    if len(chain) < 2:
        raise ConfigError("A driveway needs the access point and at least one more point.")
    sid, rcl_nguid = "", ""
    if segment_fid is not None:
        feat = next(ctx.rcl.getFeatures(QgsFeatureRequest().setFilterFid(segment_fid)), None)
        if feat is not None:
            sid, _parts = ctx.street_of(feat)
            rcl_nguid = str(ctx.rcl_acc.get(feat, "RCL_NGUID") or "")
    aid, access_fid = link_access(ctx, access, sid, rcl_nguid, "", parent_driveway)
    xf = _xf(ctx.rcl.crs(), ctx.dw.crs())
    geom = QgsGeometry.fromPolylineXY([xf.transform(p) for p in chain])
    if QgsWkbTypes.isMultiType(ctx.dw.wkbType()):
        geom.convertToMultiType()
    acc = ctx.dw_acc
    stamp = datetime.now().replace(microsecond=0)
    vals = {"DW_NGUID": _new_nguid(ctx, "DW", rcl_nguid or ""), "RCL_NGUID": rcl_nguid or None,
            "StreetID": sid or None, "Parent_DW": parent_driveway or None,
            "AccessPointID": aid or None, "DateUpdate": stamp}
    attrs = {acc.index[k]: acc.coerce(k, v) for k, v in vals.items() if acc.has(k)}
    created = QgsVectorLayerUtils.createFeature(ctx.dw, geom, attrs, ctx.dw.createExpressionContext())
    try:
        with suppressed():
            ensure_editable(ctx.dw)
            ctx.dw.beginEditCommand(DRAW_DRIVEWAY_COMMAND)
            if not ctx.dw.addFeature(created):
                ctx.dw.destroyEditCommand()
                raise ConfigError("The driveway layer refused the new line.")
            ctx.dw.endEditCommand()
    except Exception:
        if access_fid is not None:
            _forget_access(ctx.access, access_fid)
        raise
    return created.id()


def undo_last_address() -> int:
    """Remove the features created by the last address-tool click sequence (all layers at once)."""
    if not UNDO_STACK:
        raise ConfigError("Nothing to undo.")
    created = UNDO_STACK.pop()
    n = 0
    with suppressed_guard():
        with suppressed():
            for layer_id, fid in reversed(created):
                lyr = QgsProject.instance().mapLayer(layer_id)
                if lyr is None or not lyr.isEditable():
                    continue
                if next(lyr.getFeatures(QgsFeatureRequest().setFilterFid(fid)), None) is None:
                    continue
                lyr.beginEditCommand("NENA: undo last address")
                if lyr.deleteFeature(fid):
                    n += 1
                    lyr.endEditCommand()
                else:
                    lyr.destroyEditCommand()
    return n


# ---------------------------------------------------------------------------
def shared_driveway_at(ctx: AddressContext, pt: QgsPointXY, tol: float):
    """(driveway feature, access point in centerline CRS) when ``pt`` hits an existing driveway."""
    if ctx.dw is None:
        return None
    xf = _xf(ctx.rcl.crs(), ctx.dw.crs())
    back = _xf(ctx.dw.crs(), ctx.rcl.crs())
    p = xf.transform(pt)
    r = tol
    rect = QgsRectangle(p.x() - r, p.y() - r, p.x() + r, p.y() + r)
    for d in ctx.dw.getFeatures(QgsFeatureRequest().setFilterRect(rect)):
        g = d.geometry()
        if g.isNull():
            continue
        sqr, _c, _a, _l = g.closestSegmentWithContext(p)
        if sqr >= 0 and math.sqrt(sqr) <= r:
            pl = gu.polylines(g)
            if pl:
                return d, back.transform(QgsPointXY(pl[0][0]))
    return None


def readdress(ctx: AddressContext, address_fids: Sequence[Hashable]) -> List[Tuple[Hashable, str, AddressPlan]]:
    """Recalculate numbers for existing addresses (e.g. after part of a road was renamed). Nothing written."""
    out = []
    for f in list(ctx.addr.getFeatures(QgsFeatureRequest().setFilterFids(list(address_fids)))):
        if not f.hasGeometry():
            continue
        roof = ctx.from_addr.transform(_pt(f.geometry()))
        access = stored_access(ctx, f) or roof
        num, suf = parse_number(ctx.addr_acc.get(f, "Add_Number"),
                                ctx.addr_acc.get(f, "AddNum_Suf") if ctx.addr_acc.has("AddNum_Suf") else None)
        try:
            plan = plan_address(ctx, access, roof, exclude_address=f.id())
        except ConfigError:
            continue
        out.append((f.id(), "" if num is None else f"{num}{suf}", plan))
    return out


def apply_readdress(ctx: AddressContext, rows: Sequence[Tuple[Hashable, AddressPlan]]) -> int:
    """Write the new numbers (and the street name) of re-addressed points – one edit command."""
    from ..layer_access import apply_changes
    ctx.addr_acc = ensure_full_name_mapping(ctx.addr, ctx.addr_acc)
    stamp = datetime.now().replace(microsecond=0)
    changes = {}
    for fid, plan in rows:
        if plan.result.number is None:
            continue
        vals = dict(plan.parts)
        vals.update({"Add_Number": plan.result.number, "AddNum_Suf": plan.result.suffix or None,
                     "StreetID": plan.street_id or None, "FullName": plan.full_name or None,
                     "Addr_Side": plan.side,
                     "RCL_NGUID": plan.rcl_nguid or None, "DateUpdate": stamp})
        for key, value in plan.community.items():
            vals[COMMUNITY_FIELDS[key][0]] = value
        changes[fid] = vals
    with suppressed():
        n = apply_changes(ctx.addr, ctx.addr_acc, changes, "NENA: re-address")
        for _fid, plan in rows:
            _stamp_centerline_side(ctx, plan)
        return n


# ---------------------------------------------------------------------------
def attribute_addresses(ctx: AddressContext, fids: Optional[Sequence[Hashable]] = None) -> Tuple[int, List[str]]:
    """Fill address community fields from the centerline side the point is on."""
    from ..layer_access import apply_changes
    changes, notes = {}, []
    measures = {}
    req = QgsFeatureRequest() if fids is None else QgsFeatureRequest().setFilterFids(list(fids))
    for f in list(ctx.addr.getFeatures(req)):
        if not f.hasGeometry():
            continue
        roof = ctx.from_addr.transform(_pt(f.geometry()))
        access = stored_access(ctx, f)
        probe = access or roof
        community, n = _address_community(ctx, f, roof, probe, measures)
        notes.extend(f"Address {f.id()}: {x}" for x in n)
        mapped = {COMMUNITY_FIELDS[k][0]: v for k, v in community.items() if ctx.addr_acc.has(COMMUNITY_FIELDS[k][0])}
        if mapped:
            changes[f.id()] = mapped
    with suppressed():
        return apply_changes(ctx.addr, ctx.addr_acc, changes, "NENA: attribute addresses"), notes


def _address_community(ctx, feature, roof, probe, measures):
    """Community values for an existing address, from its centerline side."""
    sid = N.clean(ctx.addr_acc.get(feature, "StreetID")) if ctx.addr_acc.has("StreetID") else ""
    key = N.name_key({p: ctx.addr_acc.get(feature, p) for p in N.NAME_PARTS})
    street = sid or key
    if not street:
        return {}, []
    if street not in measures:
        measures[street] = StreetMeasure(ctx, sid, key)
    loc = measures[street].locate(QgsPointXY(probe))
    if loc is None:
        return {}, []
    _dist, seg_fid, frac, (dx, dy), _sq = loc
    side = N.clean(ctx.addr_acc.get(feature, "Addr_Side")).upper() if ctx.addr_acc.has("Addr_Side") else ""
    if side not in ("L", "R"):
        side = NB.side_of(dx, dy, roof.x() - probe.x(), roof.y() - probe.y()) or ""
    if side not in ("L", "R"):
        return {}, []
    seg = next(ctx.rcl.getFeatures(QgsFeatureRequest().setFilterFid(seg_fid)), None)
    if seg is None:
        return {}, []
    community, notes, _updates = community_from_segment(ctx, seg, side, frac)
    return community, notes


def attribute_centerline_sides(ctx: AddressContext, fids: Optional[Sequence[Hashable]] = None,
                               offset_m: float = 3.0, write: bool = True) -> Tuple[int, List[Tuple[Hashable, str]]]:
    """
    Fill _L/_R community fields of centerlines from the boundary rules, sampling a point just off each side
    at 10 %, 50 % and 90 % of the segment. A side whose samples disagree crosses a boundary (reported).
    """
    from ..layer_access import apply_changes
    off = gu.metres_to_layer_units(offset_m, ctx.rcl.crs())
    changes, crossings = {}, []
    req = QgsFeatureRequest() if fids is None else QgsFeatureRequest().setFilterFids(list(fids))
    for f in list(ctx.rcl.getFeatures(req)):
        g = gu.linear_geometry(f.geometry())
        if g.isNull() or g.isEmpty():
            continue
        vals = {}
        for side in ("L", "R"):
            samples = []
            for t in (0.1, 0.5, 0.9):
                samples.append(boundary_side_values(ctx, g, t, side, off)[0])
            mid = samples[1]
            for key, value in mid.items():
                fld = COMMUNITY_FIELDS[key][1 if side == "L" else 2]
                if ctx.rcl_acc.has(fld):
                    vals[fld] = value or None
            if any(s != mid for s in samples):
                crossings.append((f.id(), side))
        if vals:
            changes[f.id()] = vals
    if not write:
        return 0, crossings
    with suppressed():
        return apply_changes(ctx.rcl, ctx.rcl_acc, changes, "NENA: attribute centerline sides"), crossings


def addresses_on_segments(ctx: AddressContext, seg_fids: Sequence[Hashable]) -> List[Hashable]:
    """
    Address points that were addressed from (or sit along) the given centerline segments: matched by
    RCL_NGUID when stored, otherwise by the nearest segment of their street to their access point.
    """
    seg_set = set(seg_fids)
    nguids = {}
    for f in ctx.rcl.getFeatures(QgsFeatureRequest().setFilterFids(list(seg_set))):
        n = ctx.rcl_acc.get(f, "RCL_NGUID") if ctx.rcl_acc.has("RCL_NGUID") else None
        if n:
            nguids[str(n)] = f.id()
    out = []
    measures = {}
    for f in list(ctx.addr.getFeatures()):
        n = ctx.addr_acc.get(f, "RCL_NGUID") if ctx.addr_acc.has("RCL_NGUID") else None
        if n and str(n) in nguids:
            out.append(f.id())
            continue
        if not f.hasGeometry():
            continue
        sid = N.clean(ctx.addr_acc.get(f, "StreetID")) if ctx.addr_acc.has("StreetID") else ""
        key = N.name_key({p: ctx.addr_acc.get(f, p) for p in N.NAME_PARTS})
        street = sid or key
        if not street:
            continue
        if street not in measures:
            measures[street] = StreetMeasure(ctx, sid, key)
        if not set(measures[street].segs) & seg_set:
            continue
        probe = stored_access(ctx, f) or ctx.from_addr.transform(_pt(f.geometry()))
        loc = measures[street].locate(QgsPointXY(probe))
        if loc is not None and loc[1] in seg_set:
            out.append(f.id())
    return out


def _side_rules(pcfg):
    """Settings, Sides rules plus PSAP from the PSAP zone layer (see zones.side_rules)."""
    from ..zones import side_rules
    return side_rules(pcfg)
