# -*- coding: utf-8 -*-
"""
Create access points for address points that have none.

Imported addresses arrive without access points: Place address makes one for
each address it places, but an import only copies the address points. Routing
needs the access point, so this builds it the way Place address would:

* **Where the address meets the road.** The start of the address's driveway
  (matched by Site_NGUID), else the stored Access_X / Access_Y, else the point
  on the road nearest the address. Stored coordinates far from the address
  (rounded to a whole degree, for example) are not used.
* **Which road.** The centerline named by the address's RCL_NGUID when it is
  near that point, else the nearest centerline.
* **Which side.** The side of the road the address point is on (the stored
  Addr_Side when the point sits on the road line).
* **The access point** is placed 10 feet straight off the road on that side,
  with AccessPointID, RCL_NGUID, StreetID, and RoadSide. Addresses that meet
  the road at the same spot on the same side share one access point, and an
  access point already within the snapping tolerance is reused.
* **The address** gets the AccessPointID, and Access_X / Access_Y, Addr_Side,
  and RCL_NGUID where those are empty. A driveway with no AccessPointID gets it.

An address whose AccessPointID names an access point that does not exist gets
that access point created with the same id, so links made elsewhere survive.
Addresses already linked to an existing access point are left alone.

Everything goes into edit sessions as one undo step per layer. ``commit=True``
saves them (the import does this); otherwise they are left for you to save.
"""

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Hashable, List, Optional, Sequence, Tuple

from qgis.core import QgsFeatureRequest, QgsGeometry, QgsPointXY, QgsSpatialIndex, QgsVectorLayerUtils, QgsWkbTypes

from ...compat import make_field, py_value
from .. import geometry_utils as gu
from ..layer_access import ConfigError, FieldAccessor, apply_changes, ensure_editable
from ..safe_call import attempt
from ..streets import names as N
from . import engine as E
from . import numbering as NB

BUILD_COMMAND = "NENA: create access points"
# How far the address's RCL_NGUID road may be from the access spot and still be used.
LINK_LIMIT_M = 60.0
# How far an address may be from any road and still get an access point.
ROAD_LIMIT_M = 1500.0

SRC_DRIVEWAY = "driveway"
SRC_STORED = "Access_X/Y"
SRC_NEAREST = "nearest road"


@dataclass
class AccessPlan:
    address_fid: Hashable
    access_id: str
    create: bool                      # a new access-point feature is needed
    seat: QgsPointXY                  # access-point location, centerline CRS
    road: QgsPointXY                  # where the address meets the road, centerline CRS
    side: str
    segment_fid: Hashable
    rcl_nguid: str
    street_id: str
    source: str
    shared: bool = False              # reuses an access point made or found for another address
    address_values: Dict[str, object] = field(default_factory=dict)
    driveway_fid: Optional[Hashable] = None


@dataclass
class BuildResult:
    plans: List[AccessPlan] = field(default_factory=list)
    skipped: List[Tuple[Hashable, str]] = field(default_factory=list)
    already: int = 0                  # addresses already linked to an existing access point
    created: int = 0
    linked: int = 0
    saved: bool = False
    errors: List[str] = field(default_factory=list)

    @property
    def to_create(self) -> int:
        return sum(1 for plan in self.plans if plan.create)

    @property
    def sources(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for plan in self.plans:
            out[plan.source] = out.get(plan.source, 0) + 1
        return out

    def summary(self) -> str:
        parts = []
        if self.created or self.linked:
            parts.append(f"{self.created} access point(s) created, {self.linked} address(es) linked")
        else:
            parts.append(f"{self.to_create} access point(s) to create for {len(self.plans)} address(es)")
        shared = sum(1 for plan in self.plans if plan.shared)
        if shared:
            parts.append(f"{shared} address(es) share an access point with another address")
        src = self.sources
        if src:
            parts.append("placed from " + ", ".join(f"{name} ({n})" for name, n in sorted(src.items())))
        if self.already:
            parts.append(f"{self.already} address(es) already had one")
        if self.skipped:
            parts.append(f"{len(self.skipped)} address(es) skipped")
        return "; ".join(parts) + "."


def _clean(value) -> str:
    value = py_value(value)
    if value is None:
        return ""
    return str(value).strip()


def _float(value) -> Optional[float]:
    value = py_value(value)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out):
        return None
    return out


def _point_of(geom) -> Optional[QgsPointXY]:
    """The point of a point or single-part multipoint geometry."""
    if geom is None or geom.isNull() or geom.isEmpty():
        return None
    if geom.isMultipart():
        parts = attempt(geom.asMultiPoint)
        return QgsPointXY(parts[0]) if parts else None
    pt = attempt(geom.asPoint)
    return QgsPointXY(pt) if pt is not None else None


def ensure_link_field(ctx) -> bool:
    """Add AccessPointID to the address layer when it has none. Returns True if added."""
    if ctx.addr_acc.has("AccessPointID"):
        return False
    provider = ctx.addr.dataProvider()
    if provider is None or not provider.addAttributes([make_field("AccessPointID", "string", 38)]):
        raise ConfigError(f"'{ctx.addr.name()}' has no AccessPointID field and one could not be added. "
                          "Add a text field AccessPointID (38 characters) and try again.")
    ctx.addr.updateFields()
    mapping = E.address_mapping(ctx.addr, ctx.pcfg)
    from ..config import PluginConfig
    cfg = PluginConfig()
    cfg.mapping = mapping
    ctx.addr_acc = FieldAccessor(ctx.addr, cfg)
    return True


def _require(ctx) -> None:
    if ctx.access is None or ctx.access_acc is None:
        raise ConfigError("Choose the access point layer in the Addresses window (Access points) first. "
                          "Create schema makes one.")
    if not ctx.access_acc.has("AccessPointID"):
        raise ConfigError(f"The access point layer '{ctx.access.name()}' has no AccessPointID field.")


class _Roads:
    """Centerline segments in memory, with a spatial index, for one run."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.geoms: Dict[Hashable, QgsGeometry] = {}
        self.nguid: Dict[Hashable, str] = {}
        self.sid: Dict[Hashable, str] = {}
        self.by_nguid: Dict[str, Hashable] = {}
        self.index = QgsSpatialIndex()
        acc = ctx.rcl_acc
        for feat in ctx.rcl.getFeatures():
            if not feat.hasGeometry():
                continue
            geom = feat.geometry()
            if geom is None or geom.isNull() or geom.isEmpty():
                continue
            fid = feat.id()
            self.geoms[fid] = QgsGeometry(geom)
            nguid = _clean(acc.get(feat, "RCL_NGUID")) if acc.has("RCL_NGUID") else ""
            self.nguid[fid] = nguid
            self.sid[fid] = N.clean(acc.get(feat, "StreetID")) if acc.has("StreetID") else ""
            if nguid and nguid not in self.by_nguid:
                self.by_nguid[nguid] = fid
            self.index.addFeature(feat)

    def closest(self, fid, pt: QgsPointXY):
        """(metres, closest point, (dx, dy) of the road there) on segment ``fid``."""
        geom = self.geoms.get(fid)
        if geom is None:
            return None
        found = geom.closestSegmentWithContext(pt)
        if not found or found[0] < 0:
            return None
        _d2, closest, after, _left = found[0], QgsPointXY(found[1]), found[2], found[3]
        a = geom.vertexAt(max(after - 1, 0))
        b = geom.vertexAt(after)
        dx, dy = b.x() - a.x(), b.y() - a.y()
        if dx == 0 and dy == 0:
            return None
        return E._metres_between(self.ctx, pt, closest), closest, (dx, dy)  # noqa: SLF001

    def nearest(self, pt: QgsPointXY):
        best = None
        for fid in self.index.nearestNeighbor(pt, 3):
            found = self.closest(fid, pt)
            if found is not None and (best is None or found[0] < best[1][0]):
                best = (fid, found)
        return best


class _Seats:
    """Access points by location (access-layer CRS): existing ones and those made in this run."""

    def __init__(self, tol: float):
        self.tol = max(tol, 1e-9)
        self.cells: Dict[Tuple[int, int], List[Tuple[str, float, float]]] = {}

    def _cell(self, x, y):
        return int(math.floor(x / self.tol)), int(math.floor(y / self.tol))

    def add(self, aid: str, x: float, y: float) -> None:
        self.cells.setdefault(self._cell(x, y), []).append((aid, x, y))

    def near(self, x: float, y: float) -> str:
        cx, cy = self._cell(x, y)
        best = None
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                for aid, px, py in self.cells.get((cx + i, cy + j), ()):
                    d = math.hypot(px - x, py - y)
                    if d <= self.tol and (best is None or d < best[0]):
                        best = (d, aid)
        return best[1] if best else ""


def _driveways(ctx) -> Dict[str, dict]:
    """First driveway of each Site_NGUID: its id, both end points (centerline CRS), and AccessPointID."""
    out: Dict[str, dict] = {}
    dw, acc = ctx.dw, ctx.dw_acc
    if dw is None or acc is None or not acc.has("Site_NGUID"):
        return out
    to_rcl = E._xf(dw.crs(), ctx.rcl.crs())  # noqa: SLF001
    for feat in dw.getFeatures():
        nguid = _clean(acc.get(feat, "Site_NGUID"))
        if not nguid or nguid in out or not feat.hasGeometry():
            continue
        lines = gu.polylines(feat.geometry())
        if not lines or not lines[0]:
            continue
        out[nguid] = {
            "fid": feat.id(),
            "ends": (to_rcl.transform(QgsPointXY(lines[0][0])), to_rcl.transform(QgsPointXY(lines[-1][-1]))),
            "aid": _clean(acc.get(feat, "AccessPointID")) if acc.has("AccessPointID") else "",
        }
    return out


def plan_access_points(ctx, fids: Optional[Sequence[Hashable]] = None, progress=None) -> BuildResult:
    """Work out an access point for each address with none. Nothing is written."""
    _require(ctx)
    result = BuildResult()
    acc = ctx.addr_acc
    access, aacc = ctx.access, ctx.access_acc
    to_access = E._xf(ctx.rcl.crs(), access.crs())  # noqa: SLF001
    tol = gu.metres_to_layer_units(max(ctx.rcl_cfg.ranging.snap_tolerance_m, 0.05), access.crs())
    seats = _Seats(tol)
    existing = set()
    for feat in access.getFeatures():
        aid = _clean(aacc.get(feat, "AccessPointID"))
        if not aid:
            continue
        existing.add(aid)
        pt = _point_of(feat.geometry()) if feat.hasGeometry() else None
        if pt is not None:
            seats.add(aid, pt.x(), pt.y())
    made: Dict[str, AccessPlan] = {}
    roads = _Roads(ctx)
    driveways = _driveways(ctx)
    has_xy = acc.has("Access_X") and acc.has("Access_Y")

    request = QgsFeatureRequest()
    if fids is not None:
        request.setFilterFids(list(fids))
    feats = list(ctx.addr.getFeatures(request))
    total = max(len(feats), 1)
    for pos, feat in enumerate(feats):
        if progress is not None and pos % 200 == 0:
            progress(100.0 * pos / total)
        fid = feat.id()
        current = _clean(acc.get(feat, "AccessPointID")) if acc.has("AccessPointID") else ""
        if current and current in existing:
            result.already += 1
            continue
        roof = None
        if feat.hasGeometry():
            pt = _point_of(feat.geometry())
            if pt is not None:
                roof = ctx.from_addr.transform(pt)

        road, source = None, ""
        site = _clean(acc.get(feat, "Site_NGUID")) if acc.has("Site_NGUID") else ""
        drive = driveways.get(site) if site else None
        if drive is not None:
            # The end that touches a road (a driveway may have been drawn from the house).
            ends = []
            for end in drive["ends"]:
                hit = roads.nearest(end)
                ends.append((hit[1][0] if hit is not None else float("inf"), end))
            road, source = QgsPointXY(min(ends, key=lambda item: item[0])[1]), SRC_DRIVEWAY
        if road is None and has_xy:
            x, y = _float(acc.get(feat, "Access_X")), _float(acc.get(feat, "Access_Y"))
            if x is not None and y is not None and not (x == 0 and y == 0):
                stored = ctx.from_addr.transform(QgsPointXY(x, y))
                if roof is None or E._metres_between(ctx, roof, stored) <= E._ACCESS_LIMIT_M:  # noqa: SLF001
                    road, source = stored, SRC_STORED
        if road is None and roof is not None:
            road, source = QgsPointXY(roof), SRC_NEAREST
        if road is None:
            result.skipped.append((fid, "no location: no point, driveway, or Access_X/Access_Y"))
            continue

        segment, found = None, None
        nguid = _clean(acc.get(feat, "RCL_NGUID")) if acc.has("RCL_NGUID") else ""
        if nguid and nguid in roads.by_nguid:
            near = roads.closest(roads.by_nguid[nguid], road)
            if near is not None and near[0] <= LINK_LIMIT_M:
                segment, found = roads.by_nguid[nguid], near
        if segment is None:
            hit = roads.nearest(road)
            if hit is not None:
                segment, found = hit
        if segment is None or found is None:
            result.skipped.append((fid, "no road found"))
            continue
        metres, closest, (dx, dy) = found
        if metres > ROAD_LIMIT_M:
            result.skipped.append((fid, f"nearest road is {metres:,.0f} m away"))
            continue

        side = None
        if roof is not None:
            side = NB.side_of(dx, dy, roof.x() - closest.x(), roof.y() - closest.y())
        if side is None and acc.has("Addr_Side"):
            stored_side = _clean(acc.get(feat, "Addr_Side")).upper()[:1]
            side = stored_side if stored_side in ("L", "R") else None
        if side is None:
            result.skipped.append((fid, "the address sits on the road line, so its side is unknown"))
            continue

        length = math.hypot(dx, dy)
        ux, uy = (-dy / length, dx / length) if side == "L" else (dy / length, -dx / length)
        seat = E._move_metres(ctx, closest, ux, uy, E._ACCESS_OFFSET_M)  # noqa: SLF001
        seat_a = to_access.transform(seat)

        create, shared = False, False
        if drive is not None and drive["aid"]:
            aid = drive["aid"]
            shared = aid in existing or aid in made
            create = not shared
        elif current:
            aid = current
            shared = aid in made
            create = not shared
        else:
            aid = seats.near(seat_a.x(), seat_a.y())
            if aid:
                shared = True
            else:
                aid = uuid.uuid4().hex
                create = True
        if create:
            seats.add(aid, seat_a.x(), seat_a.y())
        plan = AccessPlan(fid, aid, create, seat, QgsPointXY(closest), side, segment,
                          roads.nguid.get(segment, ""), roads.sid.get(segment, ""), source, shared)
        if create:
            made[aid] = plan
        values = {"AccessPointID": aid}
        if has_xy and (_float(acc.get(feat, "Access_X")) is None or _float(acc.get(feat, "Access_Y")) is None):
            on_addr = ctx.to_addr.transform(closest)
            values["Access_X"], values["Access_Y"] = on_addr.x(), on_addr.y()
        if acc.has("Addr_Side") and not _clean(acc.get(feat, "Addr_Side")):
            values["Addr_Side"] = side
        if acc.has("RCL_NGUID") and not nguid and plan.rcl_nguid:
            values["RCL_NGUID"] = plan.rcl_nguid
        plan.address_values = values
        if drive is not None and not drive["aid"]:
            plan.driveway_fid = drive["fid"]
            drive["aid"] = aid
        result.plans.append(plan)
    if progress is not None:
        progress(100.0)
    return result


def apply_access_points(ctx, result: BuildResult, commit: bool = False) -> BuildResult:
    """Write the planned access points and address links. ``commit`` saves the layers."""
    if not result.plans:
        return result
    access, aacc = ctx.access, ctx.access_acc
    stamp = datetime.now().replace(microsecond=0)
    ensure_editable(access)
    access.beginEditCommand(BUILD_COMMAND)
    created = 0
    try:
        for plan in result.plans:
            if not plan.create:
                continue
            geom = QgsGeometry.fromPointXY(E._xf(ctx.rcl.crs(), access.crs()).transform(plan.seat))  # noqa: SLF001
            if QgsWkbTypes.isMultiType(access.wkbType()):
                geom.convertToMultiType()
            vals = {"AccessPointID": plan.access_id, "RCL_NGUID": plan.rcl_nguid or None,
                    "StreetID": plan.street_id or None, "RoadSide": plan.side, "DateUpdate": stamp}
            attrs = {aacc.index[k]: aacc.coerce(k, v) for k, v in vals.items() if aacc.has(k)}
            feat = QgsVectorLayerUtils.createFeature(access, geom, attrs, access.createExpressionContext())
            if not access.addFeature(feat):
                raise ConfigError(f"The access point layer '{access.name()}' refused a new point.")
            created += 1
    except Exception:
        access.destroyEditCommand()
        raise
    access.endEditCommand()

    changes = {plan.address_fid: plan.address_values for plan in result.plans}
    linked = apply_changes(ctx.addr, ctx.addr_acc, changes, BUILD_COMMAND)
    dw_changes = {plan.driveway_fid: {"AccessPointID": plan.access_id}
                  for plan in result.plans if plan.driveway_fid is not None}
    if dw_changes and ctx.dw is not None and ctx.dw_acc is not None and ctx.dw_acc.has("AccessPointID"):
        apply_changes(ctx.dw, ctx.dw_acc, dw_changes, BUILD_COMMAND)
    result.created, result.linked = created, linked

    if commit:
        # Access points first, so no saved address points at a missing access point.
        for layer in (access, ctx.addr, ctx.dw):
            if layer is None or not layer.isEditable() or not layer.isModified():
                continue
            if not layer.commitChanges():
                errors = list(layer.commitErrors()) if hasattr(layer, "commitErrors") else []
                result.errors.append(f"'{layer.name()}' did not save: " + "; ".join(errors[:3]))
        result.saved = not result.errors
    return result


def build_access_points(ctx, fids: Optional[Sequence[Hashable]] = None, commit: bool = False,
                        progress=None) -> BuildResult:
    """Plan and write access points for addresses that have none."""
    ensure_link_field(ctx)
    result = plan_access_points(ctx, fids, progress)
    return apply_access_points(ctx, result, commit)
