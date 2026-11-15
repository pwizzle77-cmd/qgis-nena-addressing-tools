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
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Hashable, List, Optional, Sequence, Tuple

from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeatureRequest, QgsGeometry,
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
from .ssap import COMMUNITY_FIELDS, DRIVEWAY_FIELDS, SSAP_FIELDS

ADDRESS_COMMAND = "NENA: add address"
DRIVEWAY_COMMAND = "NENA: add driveway"
_NUM = re.compile(r"^\s*(\d+)\s*([A-Za-z]?)")

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
            raise ConfigError("Choose the centerline layer at the top of the panel.")
        cfg = centerline_cfg or load_from_layer(self.rcl) or PluginConfig(
            mapping=auto_map([f.name() for f in self.rcl.fields()]))
        self.rcl_cfg = _with_street_id(cfg, self.rcl, s.street_id_field)
        self.rcl_acc = FieldAccessor(self.rcl, self.rcl_cfg)
        self.addr = layer_for(s, "address")
        if self.addr is None:
            raise ConfigError("Choose the address point layer (Addresses tab).")
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
class StreetMeasure:
    """Distance from the road origin along all segments of one street (StreetID, else same name)."""

    def __init__(self, ctx: AddressContext, sid: str, key: str):
        self.ctx = ctx
        self.sid = sid
        self.segs: Dict[Hashable, Tuple[QgsGeometry, float, float]] = {}   # fid -> (linear geom, length_m, start_m)
        infos = []
        geoms = {}
        for f in ctx.rcl.getFeatures():
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
        for chain in build_chains(infos):
            d = node_dist.get(chain[0].start_node, 0.0)
            for s in chain:
                node_dist.setdefault(s.start_node, d)
                g, ln = geoms[s.fid]
                self.segs[s.fid] = (g, ln, d)
                d += ln
                node_dist.setdefault(s.end_node, d)

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
    dist, _fid, frac, (dx, dy), _sq = loc
    side = NB.side_of(dx, dy, rooftop.x() - access.x(), rooftop.y() - access.y())
    if side is None:
        raise ConfigError("The rooftop lies on the road line – click beside the road.")
    a = ctx.pcfg.addresses
    parity = a.left_parity if side == "L" else a.right_parity
    if a.use_segment_parity and ctx.rcl_acc.has(SIDE_FIELDS[side]["parity"]):
        seg_par = normalize_parity(ctx.rcl_acc.get(feat, SIDE_FIELDS[side]["parity"]), None)
        if seg_par in ("O", "E"):
            parity = seg_par
        elif seg_par == "Z":
            raise ConfigError(f"The {'left' if side == 'L' else 'right'} side of this segment is marked "
                              "as having no addresses (parity Z).")
    community, notes, side_updates = community_from_segment(ctx, feat, side, frac)
    settings = numbering_settings(a, community.get(a.override_field, ""))
    seg_from, seg_to = ctx.rcl_acc.get_int(feat, SIDE_FIELDS[side]["from"]), \
        ctx.rcl_acc.get_int(feat, SIDE_FIELDS[side]["to"])
    neighbors = existing_neighbors(ctx, sid, parts, side, measure, exclude_address)
    res = NB.calculate(settings, parity, neighbors, dist, frac, seg_from, seg_to)
    plan = AddressPlan(segment_fid, sid, parts, side, parity, dist, frac, access, rooftop, list(vertices), res,
                       community, notes, side_updates, rcl_nguid=str(ctx.rcl_acc.get(feat, "RCL_NGUID") or ""),
                       parent_driveway=parent_driveway)
    if res.message:
        plan.messages.append(res.message)
    if seg_from is not None and seg_to is not None and res.number is not None and (seg_from or seg_to):
        lo, hi = sorted((seg_from, seg_to))
        if not (lo <= res.number <= hi):
            plan.messages.append(f"{res.number} is outside this segment's range {seg_from}–{seg_to} on that side "
                                 "– consider re-ranging the segment.")
    return plan


def existing_neighbors(ctx: AddressContext, sid: str, parts, side: str, measure: StreetMeasure,
                       exclude: Optional[Hashable] = None) -> List[NB.Neighbor]:
    """Addresses already on this street and side, measured along the street."""
    acc = ctx.addr_acc
    key = N.name_key(parts)
    out = []
    for f in ctx.addr.getFeatures():
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
            out.append(NB.Neighbor(loc[0], num, suf))
    return out


def stored_access(ctx: AddressContext, f) -> Optional[QgsPointXY]:
    """Access point of an address (centerline CRS): stored fields, else its driveway's first vertex."""
    acc = ctx.addr_acc
    if acc.has("Access_X") and acc.has("Access_Y"):
        x, y = py_value(f.attribute(acc.index["Access_X"])), py_value(f.attribute(acc.index["Access_Y"]))
        if x not in (None, "") and y not in (None, ""):
            return ctx.from_addr.transform(QgsPointXY(float(x), float(y)))
    if ctx.dw is not None and ctx.dw_acc is not None and ctx.dw_acc.has("Site_NGUID") and acc.has("Site_NGUID"):
        nguid = acc.get(f, "Site_NGUID")
        if nguid:
            for d in ctx.dw.getFeatures():
                if ctx.dw_acc.get(d, "Site_NGUID") == nguid and d.hasGeometry():
                    pl = gu.polylines(d.geometry())
                    if pl:
                        return _xf(ctx.dw.crs(), ctx.rcl.crs()).transform(QgsPointXY(pl[0][0]))
    return None


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
    rules = ctx.pcfg.addresses.attribution_rules or []
    samples: Dict[str, Tuple[Dict[str, str], List[str]]] = {}
    if rules:
        off = gu.metres_to_layer_units(offset_m, ctx.rcl.crs())
        t = max(0.02, min(0.98, 0.5 if fraction is None else fraction))
        for s in ("L", "R"):
            pt = offset_point(feat.geometry(), t, s, off)
            samples[s] = attribution_values(ctx, pt) if pt is not None else ({}, [])
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
    for rule in ctx.pcfg.addresses.attribution_rules:
        lyr = layer_for_rule(rule)
        if lyr is None:
            notes.append(f"Boundary layer for {rule.get('target')} not found")
            continue
        idx = lyr.fields().lookupField(rule.get("source_field", ""))
        if idx < 0:
            notes.append(f"Field {rule.get('source_field')} not on {lyr.name()}")
            continue
        p = _xf(ctx.rcl.crs(), lyr.crs()).transform(pt_rcl)
        pg = QgsGeometry.fromPointXY(p)
        vals = []
        for f in lyr.getFeatures(QgsFeatureRequest().setFilterRect(pg.boundingBox().buffered(1e-9))):
            if f.hasGeometry() and f.geometry().contains(pg):
                vals.append(py_value(f.attribute(idx)))
        target = rule.get("target", "")
        if not vals:
            notes.append(f"No {lyr.name()} polygon here ({target})")
        elif len(set(map(str, vals))) > 1:
            notes.append(f"Several {lyr.name()} polygons overlap here ({target})")
        else:
            if vals[0] not in (None, ""):
                out[target] = str(vals[0])
    return out, notes


def layer_for_rule(rule: dict):
    from ..project_config import resolve_layer
    return resolve_layer(rule.get("layer_id", ""), rule.get("layer_source", ""))


# ---------------------------------------------------------------------------
def _new_nguid(ctx: AddressContext, kind: str) -> Optional[str]:
    if not ctx.edit.agency_id:
        return None
    return make_nguid(local_id(ctx.edit.nguid_local_id, None), ctx.edit.agency_id, ctx.edit.nguid_format, kind)


def commit_address(ctx: AddressContext, plan: AddressPlan, number: int, suffix: str = "", unit: str = "",
                   placement: Optional[str] = None, create_driveway: Optional[bool] = None) -> Dict[str, Hashable]:
    """Write the address point (and driveway) into the edit buffers. Returns their ids."""
    a = ctx.pcfg.addresses
    acc = ensure_full_name_mapping(ctx.addr, ctx.addr_acc)
    ctx.addr_acc = acc
    stamp = datetime.now().replace(microsecond=0)
    roof_addr = ctx.to_addr.transform(plan.rooftop)
    access_addr = ctx.to_addr.transform(plan.access)
    site_nguid = _new_nguid(ctx, "SSAP")
    vals = dict(plan.parts)
    vals.update({"Add_Number": int(number), "AddNum_Suf": suffix or None, "Unit": unit or None,
                 "Placement": placement or a.placement, "StreetID": plan.street_id or None,
                 "FullName": plan.full_name or None,
                 "RCL_NGUID": plan.rcl_nguid or None, "Access_X": access_addr.x(), "Access_Y": access_addr.y(),
                 "Addr_Side": plan.side, "Site_NGUID": site_nguid, "DateUpdate": stamp,
                 "DiscrpAgID": ctx.edit.agency_id or None})
    for key, value in plan.community.items():
        vals[COMMUNITY_FIELDS[key][0]] = value
    for field_name, value in address_fallbacks(ctx.rcl_cfg, plan.side, plan.community).items():
        if vals.get(field_name) in (None, ""):
            vals[field_name] = value
    wgs = _xf(ctx.rcl.crs(), QgsCoordinateReferenceSystem("EPSG:4326")).transform(plan.rooftop)
    vals["Longitude"], vals["Latitude"] = round(wgs.x(), 7), round(wgs.y(), 7)
    geom = QgsGeometry.fromPointXY(roof_addr)
    if QgsWkbTypes.isMultiType(ctx.addr.wkbType()):
        geom.convertToMultiType()
    attrs = {acc.index[k]: acc.coerce(k, v) for k, v in vals.items() if acc.has(k)}
    feat = QgsVectorLayerUtils.createFeature(ctx.addr, geom, attrs, ctx.addr.createExpressionContext())
    created = []
    with suppressed():
        ensure_editable(ctx.addr)
        ctx.addr.beginEditCommand(ADDRESS_COMMAND)
        if not ctx.addr.addFeature(feat):
            ctx.addr.destroyEditCommand()
            raise ConfigError("The address layer refused the new point.")
        ctx.addr.endEditCommand()
        created.append((ctx.addr.id(), feat.id()))
        out = {"address": feat.id()}
        want_dw = a.create_driveway if create_driveway is None else create_driveway
        if want_dw and ctx.dw is not None:
            out["driveway"] = _write_driveway(ctx, plan, site_nguid, stamp)
            created.append((ctx.dw.id(), out["driveway"]))
    UNDO_STACK.append(created)
    del UNDO_STACK[:-50]
    _stamp_centerline_side(ctx, plan)
    return out


def _write_driveway(ctx: AddressContext, plan: AddressPlan, site_nguid, stamp) -> Hashable:
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
    vals = {"DW_NGUID": _new_nguid(ctx, "DW"), "Site_NGUID": site_nguid, "RCL_NGUID": plan.rcl_nguid or None,
            "StreetID": plan.street_id or None, "Parent_DW": plan.parent_driveway or None, "DateUpdate": stamp}
    attrs = {acc.index[k]: acc.coerce(k, v) for k, v in vals.items() if acc.has(k)}
    feat = QgsVectorLayerUtils.createFeature(ctx.dw, geom, attrs, ctx.dw.createExpressionContext())
    ensure_editable(ctx.dw)
    ctx.dw.beginEditCommand(DRIVEWAY_COMMAND)
    if not ctx.dw.addFeature(feat):
        ctx.dw.destroyEditCommand()
        raise ConfigError("The driveway layer refused the new line.")
    ctx.dw.endEditCommand()
    return feat.id()


def undo_last_address() -> int:
    """Remove the features created by the last address-tool click sequence (all layers at once)."""
    if not UNDO_STACK:
        raise ConfigError("Nothing to undo.")
    created = UNDO_STACK.pop()
    n = 0
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
    for f in ctx.addr.getFeatures(QgsFeatureRequest().setFilterFids(list(address_fids))):
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
    for f in ctx.addr.getFeatures(req):
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
    for f in ctx.rcl.getFeatures(req):
        g = gu.linear_geometry(f.geometry())
        if g.isNull() or g.isEmpty():
            continue
        vals = {}
        for side in ("L", "R"):
            samples = []
            for t in (0.1, 0.5, 0.9):
                pt = offset_point(g, t, side, off)
                samples.append(attribution_values(ctx, pt)[0] if pt is not None else {})
            mid = samples[1]
            for key, value in mid.items():
                fld = COMMUNITY_FIELDS[key][1 if side == "L" else 2]
                if ctx.rcl_acc.has(fld):
                    vals[fld] = value
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
    for f in ctx.addr.getFeatures():
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
