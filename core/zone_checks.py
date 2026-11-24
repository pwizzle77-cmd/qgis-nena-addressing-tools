# -*- coding: utf-8 -*-
"""
Check the zone layers, and the roads and addresses against them.

Zone layers (see ``zones.py``):

* ``ZONE_GEOMETRY``       – empty or invalid polygon
* ``ZONE_OVERLAP``        – two polygons of one layer overlap
* ``ZONE_GAP``            – part of the provisioning area (or a hole) no polygon covers
* ``ZONE_OUTSIDE``        – polygon reaches outside the provisioning boundary
* ``ZONE_FIELDS_MISSING`` – the layer has no column for a required NENA field
* ``ZONE_FIELD_EMPTY``    – a required field is empty
* ``ZONE_NGUID_DUPLICATE``– two polygons share an NGUID
* ``ZONE_URN``            – ServiceURN is missing or wrong for the kind of zone
* ``ESZ_MIXED``           – an ESZ/ESN polygon falls in more than one fire, law, or EMS zone

Roads:

* ``ZONE_ROAD_NOT_SPLIT`` – the road crosses a zone line where roads are split
* ``ROAD_OUTSIDE_PROVISIONING``

Addresses:

* ``ADDR_ZONE_ACCESS``    – the address and its access point are in different zones
* ``ADDR_ZONE_ROAD_SIDE`` – (no access point) the road beside the address is in another zone
* ``ADDR_NO_ZONE``        – the address is in no zone of a layer that should cover everything
* ``ADDR_OUTSIDE_PROVISIONING``

Sizes are measured in metres and square metres, whatever the layer CRS.
Nothing is changed. Exceptions (``check_exceptions.py``) mark results as
excused, or as excused before the feature's last edit.
"""

from dataclasses import dataclass
from typing import Callable, Dict, Hashable, List, Optional, Sequence

from qgis.core import (Qgis, QgsCoordinateTransform, QgsGeometry, QgsPoint, QgsPointXY,
                       QgsProject, QgsRectangle, QgsSpatialIndex)

from ..compat import py_value, tell
from . import geometry_utils as gu
from . import zones as Z
from .check_exceptions import EXCUSED, STALE, ExceptionSet
from .project_config import resolve_layer
from .rules import ERROR, INFO, WARNING

ROAD_PIECE_M = 1.0          # a road counts as in a zone when more than this much of it is inside
SHRINK_M = 0.5              # zone polygons are shrunk this much, so a road along a line is not a crossing
SIDE_SAMPLE_M = 3.0         # how far off the road the side of the road is read


@dataclass
class ZoneIssue:
    severity: str
    code: str
    role: str                     # zone | centerline | address
    layer_id: str
    fid: Optional[Hashable]
    key: str                      # NGUID, or fid:N / gap:x,y
    zone_kind: str
    zone_layer: str               # layer name, for the message column
    message: str
    geometry: Optional[QgsGeometry] = None     # where it is, in the CRS of ``layer_id``
    feature_date: object = None
    status: str = ""              # "" | excused | stale
    exception: object = None


def _xf(src, dst):
    return QgsCoordinateTransform(src, dst, QgsProject.instance())


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _text(value) -> str:
    value = py_value(value)
    return "" if value is None else str(value).strip()


def _lookup(fields, name: str) -> int:
    idx = fields.lookupField(name)
    if idx >= 0:
        return idx
    lower = name.lower()
    for i in range(fields.count()):
        if fields.at(i).name().lower() == lower:
            return i
    return -1


class _Zone:
    """One zone layer read into memory: polygons, values, and a spatial index."""

    def __init__(self, cfg: Z.ZoneLayer, layer):
        self.cfg = cfg
        self.layer = layer
        self.crs = layer.crs()
        self.da = gu.make_distance_area(self.crs)
        self.unit = gu.metres_to_layer_units(1.0, self.crs) or 1.0
        fields = layer.fields()
        self.value_idx = _lookup(fields, cfg.value_field) if cfg.value_field else -1
        kind = Z.KINDS.get(cfg.kind)
        self.nguid_idx = _lookup(fields, kind.nguid_field) if kind else -1
        self.date_idx = _lookup(fields, "DateUpdate")
        self.geoms: Dict[Hashable, QgsGeometry] = {}
        self.values: Dict[Hashable, str] = {}
        self.keys: Dict[Hashable, str] = {}
        self.dates: Dict[Hashable, object] = {}
        self.attrs: Dict[Hashable, list] = {}
        self.bad: List[tuple] = []
        self.index = QgsSpatialIndex()
        self._engines = {}
        self._shrunk = {}
        for feat in layer.getFeatures():
            fid = feat.id()
            self.attrs[fid] = [py_value(v) for v in feat.attributes()]
            self.keys[fid] = _text(feat.attribute(self.nguid_idx)) if self.nguid_idx >= 0 else ""
            self.values[fid] = _text(feat.attribute(self.value_idx)) if self.value_idx >= 0 else str(fid)
            self.dates[fid] = py_value(feat.attribute(self.date_idx)) if self.date_idx >= 0 else None
            geom = feat.geometry() if feat.hasGeometry() else None
            if geom is None or geom.isNull() or geom.isEmpty():
                self.bad.append((fid, "has no shape"))
                continue
            geom = QgsGeometry(geom)
            if not geom.isGeosValid():
                fixed = geom.makeValid()
                self.bad.append((fid, "is not a valid polygon (it crosses itself or has a bad ring)"))
                if fixed is None or fixed.isNull() or fixed.isEmpty():
                    continue
                geom = fixed
            self.geoms[fid] = geom
            self.index.addFeature(fid, geom.boundingBox())

    @property
    def name(self) -> str:
        return self.layer.name()

    def key_of(self, fid) -> str:
        return self.keys.get(fid) or f"fid:{fid}"

    def area_m2(self, geom) -> float:
        try:
            raw = self.da.measureArea(geom)
            return float(self.da.convertAreaMeasurement(raw, Qgis.AreaUnit.SquareMeters))
        except Exception:  # noqa: BLE001
            return geom.area() / (self.unit * self.unit)

    def length_m(self, geom) -> float:
        try:
            raw = self.da.measureLength(geom)
            return float(self.da.convertLengthMeasurement(raw, Qgis.DistanceUnit.Meters))
        except Exception:  # noqa: BLE001
            return geom.length() / self.unit

    def candidates(self, rect: QgsRectangle) -> List[Hashable]:
        return [fid for fid in self.index.intersects(rect) if fid in self.geoms]

    def _engine(self, fid):
        engine = self._engines.get(fid)
        if engine is None:
            engine = QgsGeometry.createGeometryEngine(self.geoms[fid].constGet())
            engine.prepareGeometry()
            self._engines[fid] = engine
        return engine

    def zone_at(self, pt: QgsPointXY) -> Optional[Hashable]:
        """The polygon (fid) a point falls in, in this layer's CRS."""
        rect = QgsRectangle(pt.x(), pt.y(), pt.x(), pt.y())
        probe = QgsPoint(pt.x(), pt.y())
        for fid in self.candidates(rect):
            try:
                if self._engine(fid).intersects(probe):
                    return fid
            except Exception:  # noqa: BLE001 - fall back to the plain test
                if self.geoms[fid].contains(pt):
                    return fid
        return None

    def shrunk(self, fid) -> QgsGeometry:
        geom = self._shrunk.get(fid)
        if geom is None:
            geom = self.geoms[fid].buffer(-SHRINK_M * self.unit, 4)
            self._shrunk[fid] = geom
        return geom


def load_zones(pcfg) -> List[_Zone]:
    out = []
    for cfg in Z.load_zones(pcfg.zones.layers):
        layer = resolve_layer(cfg.layer_id, cfg.layer_source)
        if layer is None:
            continue
        out.append(_Zone(cfg, layer))
    return out


def _provisioning(zones: Sequence[_Zone]) -> Optional[_Zone]:
    for zone in zones:
        if zone.cfg.kind == "provisioning" and zone.geoms:
            return zone
    return None


def _union(geoms) -> Optional[QgsGeometry]:
    geoms = [g for g in geoms if g is not None and not g.isNull() and not g.isEmpty()]
    if not geoms:
        return None
    merged = QgsGeometry.unaryUnion(geoms)
    return merged if merged is not None and not merged.isNull() else None


def _parts(geom: QgsGeometry) -> List[QgsGeometry]:
    if geom is None or geom.isNull() or geom.isEmpty():
        return []
    if geom.isMultipart():
        return [QgsGeometry(p) for p in geom.asGeometryCollection() if p is not None and not p.isEmpty()]
    return [geom]


def _polygon_parts(geom):
    return [p for p in _parts(geom) if p.type() == Qgis.GeometryType.Polygon]


def _where(geom: QgsGeometry) -> str:
    c = geom.centroid().asPoint() if geom is not None and not geom.isEmpty() else None
    return "" if c is None else f"{c.x():.1f},{c.y():.1f}"


# ---------------------------------------------------------------------------
# Zone layers
# ---------------------------------------------------------------------------
def _layer_issues(zone: _Zone, prov: Optional[_Zone], prov_union) -> List[ZoneIssue]:
    cfg, out = zone.cfg, []
    kind = Z.KINDS.get(cfg.kind)
    tol = cfg.gap_tolerance_m2

    def issue(sev, code, fid, message, geom=None, key=None):
        out.append(ZoneIssue(sev, code, "zone", zone.layer.id(), fid,
                             key or (zone.key_of(fid) if fid is not None else ""), cfg.kind, zone.name, message,
                             geom, zone.dates.get(fid) if fid is not None else None))

    if cfg.check_on("geometry"):
        for fid, why in zone.bad:
            issue(ERROR, "ZONE_GEOMETRY", fid, f"{zone.values.get(fid) or 'Polygon'} {why}.")

    if cfg.check_on("overlaps"):
        done = set()
        for fid, geom in zone.geoms.items():
            for other in zone.candidates(geom.boundingBox()):
                if other == fid or (other, fid) in done:
                    continue
                done.add((fid, other))
                inter = geom.intersection(zone.geoms[other])
                if inter is None or inter.isEmpty():
                    continue
                area = sum(zone.area_m2(p) for p in _polygon_parts(inter))
                if area > tol:
                    issue(ERROR, "ZONE_OVERLAP", fid,
                          f"{zone.values.get(fid) or fid} overlaps {zone.values.get(other) or other} "
                          f"by {area:,.0f} m².", inter)

    if cfg.check_on("gaps") and zone.geoms:
        union = _union(zone.geoms.values())
        holes: List[QgsGeometry] = []
        if prov_union is not None and union is not None:
            area_in = prov_union if prov.crs == zone.crs else _transformed(prov_union, prov.crs, zone.crs)
            holes = _polygon_parts(area_in.difference(union))
        elif union is not None:
            for part in _polygon_parts(union):
                poly = part.asPolygon()
                for ring in poly[1:]:
                    holes.append(QgsGeometry.fromPolygonXY([ring]))
        for hole in holes:
            area = zone.area_m2(hole)
            if area > tol:
                where = _where(hole)
                issue(WARNING, "ZONE_GAP", None,
                      f"A gap of {area:,.0f} m² is in no {cfg.title.lower()} zone"
                      + (" (inside the provisioning boundary)." if prov_union is not None else "."),
                      hole, key=f"gap:{where}")

    if cfg.check_on("outside") and prov_union is not None and cfg.kind != "provisioning":
        area_in = prov_union if prov.crs == zone.crs else _transformed(prov_union, prov.crs, zone.crs)
        grown = area_in.buffer(SHRINK_M * zone.unit, 4)
        for fid, geom in zone.geoms.items():
            outside = geom.difference(grown)
            area = sum(zone.area_m2(p) for p in _polygon_parts(outside))
            if area > tol:
                issue(WARNING, "ZONE_OUTSIDE", fid,
                      f"{zone.values.get(fid) or fid} reaches {area:,.0f} m² outside the provisioning boundary.",
                      outside)

    if cfg.check_on("fields"):
        fields = zone.layer.fields()
        required = Z.required_fields(cfg.kind)
        absent = [name for name in required if _lookup(fields, name) < 0]
        if absent:
            issue(INFO, "ZONE_FIELDS_MISSING", None, "The layer has no " + ", ".join(absent) + " field.",
                  key=f"layer:{cfg.kind}")
        present = [(name, _lookup(fields, name)) for name in required if _lookup(fields, name) >= 0]
        seen: Dict[str, Hashable] = {}
        urn_idx = _lookup(fields, "ServiceURN")
        for fid, attrs in zone.attrs.items():
            empty = [name for name, idx in present if _blank(attrs[idx] if idx < len(attrs) else None)]
            if empty:
                issue(ERROR, "ZONE_FIELD_EMPTY", fid,
                      f"{zone.values.get(fid) or fid}: " + ", ".join(empty) + " empty.")
            key = zone.keys.get(fid)
            if key:
                if key in seen:
                    issue(ERROR, "ZONE_NGUID_DUPLICATE", fid,
                          f"{kind.nguid_field} {key} is also on feature {seen[key]}.")
                else:
                    seen[key] = fid
            if urn_idx >= 0 and cfg.kind in Z.ESB_KINDS:
                urn = attrs[urn_idx] if urn_idx < len(attrs) else None
                if not _blank(urn) and not Z.urn_matches(cfg.kind, urn):
                    issue(ERROR, "ZONE_URN", fid,
                          f"{zone.values.get(fid) or fid}: ServiceURN '{urn}' is not a {cfg.title.lower()} URN.")
    return out


def _transformed(geom, src, dst):
    g = QgsGeometry(geom)
    g.transform(_xf(src, dst))
    return g


def _esz_issues(zones: Sequence[_Zone]) -> List[ZoneIssue]:
    out = []
    services = [z for z in zones if z.cfg.kind in ("fire", "law", "ems")]
    for esz in (z for z in zones if z.cfg.kind == "esz" and z.cfg.check_on("overlaps")):
        members: Dict[Hashable, Dict[str, set]] = {}
        for fid, geom in esz.geoms.items():
            for svc in services:
                g = geom if svc.crs == esz.crs else _transformed(geom, esz.crs, svc.crs)
                shrunk = g.buffer(-SHRINK_M * svc.unit, 4)
                for other in svc.candidates(g.boundingBox()):
                    inter = shrunk.intersection(svc.geoms[other])
                    if inter is not None and not inter.isEmpty() and svc.area_m2(inter) > esz.cfg.gap_tolerance_m2:
                        members.setdefault(fid, {}).setdefault(svc.cfg.kind, set()).add(svc.values.get(other))
        for fid, kind, values in Z.esz_conflicts(members):
            out.append(ZoneIssue(WARNING, "ESZ_MIXED", "zone", esz.layer.id(), fid, esz.key_of(fid), kind,
                                 esz.name, f"ESN {esz.values.get(fid) or fid} lies in more than one "
                                 f"{Z.KINDS[kind].title.lower()} zone: " + ", ".join(values) + ".",
                                 None, esz.dates.get(fid)))
    return out


# ---------------------------------------------------------------------------
# Roads
# ---------------------------------------------------------------------------
def _road_issues(zones: Sequence[_Zone], rcl, key_field: str, prov: Optional[_Zone], prov_union,
                 progress=None) -> List[ZoneIssue]:
    out: List[ZoneIssue] = []
    split_zones = [z for z in zones if z.cfg.split_roads and z.cfg.check_on("roads") and z.geoms]
    check_prov = prov is not None and prov_union is not None and prov.cfg.check_on("roads")
    if rcl is None or (not split_zones and not check_prov):
        return out
    fields = rcl.fields()
    k_idx = _lookup(fields, key_field) if key_field else -1
    d_idx = _lookup(fields, "DateUpdate")
    xforms = {}
    feats = list(rcl.getFeatures())
    total = max(len(feats), 1)
    prov_grown = prov_union.buffer(SHRINK_M * prov.unit, 4) if check_prov else None
    for pos, feat in enumerate(feats):
        if progress is not None and pos % 500 == 0:
            progress(pos / total)
        if not feat.hasGeometry():
            continue
        geom = feat.geometry()
        if geom is None or geom.isNull() or geom.isEmpty():
            continue
        key = _text(feat.attribute(k_idx)) if k_idx >= 0 else ""
        key = key or f"fid:{feat.id()}"
        date = py_value(feat.attribute(d_idx)) if d_idx >= 0 else None
        for zone in split_zones:
            g = geom
            if zone.crs != rcl.crs():
                xf = xforms.setdefault(zone.layer.id(), _xf(rcl.crs(), zone.crs))
                g = QgsGeometry(geom)
                g.transform(xf)
            inside: Dict[str, float] = {}
            for fid in zone.candidates(g.boundingBox()):
                part = g.intersection(zone.shrunk(fid))
                if part is None or part.isEmpty():
                    continue
                metres = zone.length_m(part)
                if metres > ROAD_PIECE_M:
                    value = zone.values.get(fid) or str(fid)
                    inside[value] = inside.get(value, 0.0) + metres
            if len(inside) > 1:
                names = ", ".join(f"{v} ({m:,.0f} m)" for v, m in sorted(inside.items()))
                out.append(ZoneIssue(WARNING, "ZONE_ROAD_NOT_SPLIT", "centerline", rcl.id(), feat.id(), key,
                                     zone.cfg.kind, zone.name,
                                     f"Crosses the {zone.cfg.title.lower()} line and is not split there: {names}.",
                                     QgsGeometry(geom), date))
        if check_prov:
            g = geom
            if prov.crs != rcl.crs():
                g = QgsGeometry(geom)
                g.transform(_xf(rcl.crs(), prov.crs))
            outside = g.difference(prov_grown)
            if outside is not None and not outside.isEmpty():
                metres = prov.length_m(outside)
                if metres > ROAD_PIECE_M:
                    out.append(ZoneIssue(ERROR, "ROAD_OUTSIDE_PROVISIONING", "centerline", rcl.id(), feat.id(), key,
                                         "provisioning", prov.name,
                                         f"{metres:,.0f} m of this road is outside the provisioning boundary.",
                                         QgsGeometry(geom), date))
    return out


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------
def _point(geom) -> Optional[QgsPointXY]:
    if geom is None or geom.isNull() or geom.isEmpty():
        return None
    if geom.isMultipart():
        pts = geom.asMultiPoint()
        return QgsPointXY(pts[0]) if pts else None
    return QgsPointXY(geom.asPoint())


def _access_points(access, access_acc) -> Dict[str, QgsPointXY]:
    out = {}
    if access is None or access_acc is None or not access_acc.has("AccessPointID"):
        return out
    for feat in access.getFeatures():
        aid = _text(access_acc.get(feat, "AccessPointID"))
        pt = _point(feat.geometry()) if feat.hasGeometry() else None
        if aid and pt is not None and aid not in out:
            out[aid] = pt
    return out


def _road_side(ctx, roads, feat, roof_rcl: QgsPointXY) -> Optional[QgsPointXY]:
    """A point a few metres off the address's road, toward the address (centerline CRS)."""
    acc = ctx.addr_acc
    nguid = _text(acc.get(feat, "RCL_NGUID")) if acc.has("RCL_NGUID") else ""
    found = None
    if nguid and nguid in roads.by_nguid:
        found = roads.closest(roads.by_nguid[nguid], roof_rcl)
    if found is None:
        hit = roads.nearest(roof_rcl)
        found = hit[1] if hit is not None else None
    if found is None:
        return None
    metres, closest, _direction = found
    if metres <= 0:
        return None
    step = min(SIDE_SAMPLE_M, metres) / metres
    return QgsPointXY(closest.x() + (roof_rcl.x() - closest.x()) * step,
                      closest.y() + (roof_rcl.y() - closest.y()) * step)


def _address_issues(zones: Sequence[_Zone], ctx, progress=None) -> List[ZoneIssue]:
    out: List[ZoneIssue] = []
    active = [z for z in zones if z.cfg.check_on("addresses") and z.geoms]
    if ctx is None or not active:
        return out
    from .addressing.access_build import _Roads
    addr, acc = ctx.addr, ctx.addr_acc
    access_pts = _access_points(ctx.access, ctx.access_acc)
    roads = None
    key_idx = acc.index.get("Site_NGUID", -1) if acc.has("Site_NGUID") else -1
    d_idx = acc.index.get("DateUpdate", -1) if acc.has("DateUpdate") else -1
    to_zone = {z.layer.id(): _xf(addr.crs(), z.crs) for z in active}
    acc_to_zone = {z.layer.id(): _xf(ctx.access.crs(), z.crs) for z in active} if ctx.access is not None else {}
    rcl_to_zone = {z.layer.id(): _xf(ctx.rcl.crs(), z.crs) for z in active}
    feats = list(addr.getFeatures())
    total = max(len(feats), 1)
    for pos, feat in enumerate(feats):
        if progress is not None and pos % 1000 == 0:
            progress(pos / total)
        roof = _point(feat.geometry()) if feat.hasGeometry() else None
        if roof is None:
            continue
        key = _text(feat.attribute(key_idx)) if key_idx >= 0 else ""
        key = key or f"fid:{feat.id()}"
        date = py_value(feat.attribute(d_idx)) if d_idx >= 0 else None
        aid = _text(acc.get(feat, "AccessPointID")) if acc.has("AccessPointID") else ""
        apt = access_pts.get(aid) if aid else None
        side_rcl = None
        for zone in active:
            here = to_zone[zone.layer.id()].transform(roof)
            fid = zone.zone_at(here)
            kind = zone.cfg.kind
            if fid is None:
                if kind == "provisioning":
                    out.append(ZoneIssue(ERROR, "ADDR_OUTSIDE_PROVISIONING", "address", addr.id(), feat.id(), key,
                                         kind, zone.name, "The address is outside the provisioning boundary.",
                                         QgsGeometry.fromPointXY(roof), date))
                elif Z.KINDS[kind].covers_area:
                    out.append(ZoneIssue(WARNING, "ADDR_NO_ZONE", "address", addr.id(), feat.id(), key, kind,
                                         zone.name, f"The address is in no {zone.cfg.title.lower()} zone.",
                                         QgsGeometry.fromPointXY(roof), date))
                continue
            if kind == "provisioning":
                continue
            value = zone.values.get(fid) or str(fid)
            if apt is not None and zone.layer.id() in acc_to_zone:
                other = zone.zone_at(acc_to_zone[zone.layer.id()].transform(apt))
                other_value = zone.values.get(other) if other is not None else ""
                if other != fid and other_value != value:
                    out.append(ZoneIssue(WARNING, "ADDR_ZONE_ACCESS", "address", addr.id(), feat.id(), key, kind,
                                         zone.name, f"The address is in {zone.cfg.title.lower()} zone {value} but "
                                         f"its access point is in {other_value or 'no zone'}.",
                                         QgsGeometry.fromPointXY(roof), date))
                continue
            if apt is None:
                if side_rcl is None:
                    if roads is None:
                        roads = _Roads(ctx)
                    side_rcl = _road_side(ctx, roads, feat, ctx.from_addr.transform(roof)) or False
                if side_rcl:
                    other = zone.zone_at(rcl_to_zone[zone.layer.id()].transform(side_rcl))
                    other_value = zone.values.get(other) if other is not None else ""
                    if other != fid and other_value != value:
                        out.append(ZoneIssue(WARNING, "ADDR_ZONE_ROAD_SIDE", "address", addr.id(), feat.id(), key,
                                             kind, zone.name, f"The address is in {zone.cfg.title.lower()} zone "
                                             f"{value} but the road beside it is in {other_value or 'no zone'}.",
                                             QgsGeometry.fromPointXY(roof), date))
    return out


# ---------------------------------------------------------------------------
def run_zone_checks(pcfg, rcl=None, rcl_key_field: str = "RCL_NGUID", address_ctx=None,
                    exceptions: Optional[ExceptionSet] = None,
                    progress: Optional[Callable[[float], None]] = None) -> List[ZoneIssue]:
    """Every zone check that is switched on. ``progress`` gets 0–100."""
    zones = load_zones(pcfg)
    if not zones:
        return []
    prov = _provisioning(zones)
    prov_union = _union(prov.geoms.values()) if prov is not None else None
    issues: List[ZoneIssue] = []
    steps = len(zones) + 3

    def tick(step, frac=0.0, text=None):
        tell(progress, min(100.0, 100.0 * (step + frac) / steps), text)

    for i, zone in enumerate(zones):
        title = zone.cfg.title or "zone"
        tick(i, 0, "Checking %s" % title)
        issues.extend(_layer_issues(zone, prov, prov_union))
    tick(len(zones), 0, "Checking emergency service zones")
    issues.extend(_esz_issues(zones))
    tick(len(zones) + 1, 0, "Checking roads against zones")
    issues.extend(_road_issues(zones, rcl, rcl_key_field, prov, prov_union,
                               lambda f: tick(len(zones) + 1, f, "Checking roads against zones")))
    tick(len(zones) + 2, 0, "Checking addresses against zones")
    issues.extend(_address_issues(zones, address_ctx, lambda f: tick(len(zones) + 2, f,
                                                                     "Checking addresses against zones")))
    tick(steps, 0, "Finished")
    if exceptions is not None:
        apply_exceptions(issues, exceptions)
    return issues


def apply_exceptions(issues: Sequence[ZoneIssue], exceptions: ExceptionSet) -> None:
    for issue in issues:
        status, item = exceptions.status(issue.code, issue.role, issue.key, issue.zone_kind, issue.feature_date)
        issue.status = status or ""
        issue.exception = item


def counts(issues: Sequence[ZoneIssue]) -> Dict[str, int]:
    out = {ERROR: 0, WARNING: 0, INFO: 0, EXCUSED: 0, STALE: 0}
    for issue in issues:
        if issue.status == EXCUSED:
            out[EXCUSED] += 1
            continue
        if issue.status == STALE:
            out[STALE] += 1
        out[issue.severity] = out.get(issue.severity, 0) + 1
    return out

