# -*- coding: utf-8 -*-
"""Address validation: empty fields, range, side/parity, order, duplicates, access point, boundary attribution."""

import math
import re
from collections import defaultdict
from typing import Dict, List

from qgis.core import QgsCoordinateReferenceSystem, QgsFeatureRequest, QgsPointXY

from ...compat import tell
from ..ranging import matches_parity, normalize_parity
from ..rules import ERROR, WARNING, Issue, check_directional
from ..schema import SIDE_FIELDS
from ..streets import names as N
from ..streets.street_types import OFFICIAL_BY_UPPER, TYPE_ABBREVIATIONS
from . import numbering as NB
from .engine import (AddressContext, StreetMeasure, _pt, _xf, attribute_centerline_sides, community_from_segment,
                     parse_number, resolve_street, stored_access)
from ..schema import MANDATORY
from .ssap import COMMUNITY_FIELDS, SSAP_FIELDS

# Not NENA mandatory, but the fields that are most often left empty and have a lookup default.
_ALSO_REQUIRED = ("AddressStatus", "AddressType", "Placement")
# Filled by the plugin itself or checked elsewhere (number, street).
_NOT_CHECKED = {"Add_Number", "DateUpdate"}
_ZIP5_RE = re.compile(r"^\d{5}$")
_ZIP4_RE = re.compile(r"^\d{4}$")
_LATLONG_TOL_M = 2.0


def _named_place(acc, feature) -> str:
    for name in ("LandmkName", "Mile_Post"):
        if acc.has(name):
            value = N.clean(acc.get(feature, name))
            if value:
                return value
    return ""


def _address_community(acc, feature) -> str:
    for name in ("MSAGComm", "Inc_Muni"):
        if acc.has(name):
            value = N.clean(acc.get(feature, name)).upper()
            if value:
                return value
    return ""


def _float_or_none(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def lonlat_metres(lon1, lat1, lon2, lat2) -> float:
    """Metres between two WGS84 points. Enough to tell a few feet from a wrong coordinate."""
    mid = math.radians((lat1 + lat2) / 2.0)
    dx = math.radians(lon2 - lon1) * 6371000.0 * math.cos(mid)
    dy = math.radians(lat2 - lat1) * 6371000.0
    return math.hypot(dx, dy)


def _type_issue(fid, field_name, value) -> List[Issue]:
    """Street pre/post type abbreviated instead of spelled out (e.g. 'St' for 'Street').

    A value the registry does not recognise at all is left alone: agencies
    can and do add local street types (see streets/street_types.py).
    """
    v = N.clean(value)
    key = v.upper().replace(".", "")
    if not key or key in OFFICIAL_BY_UPPER:
        return []
    fix = TYPE_ABBREVIATIONS.get(key)
    if not fix:
        return []
    spelled = OFFICIAL_BY_UPPER.get(fix, fix.title())
    return [Issue(fid, WARNING, "ADDR_STREET_TYPE_ABBREVIATED",
                  f"{field_name} '{v}' must be spelled out ('{spelled}')")]


def _zip_issues(fid, acc, f) -> List[Issue]:
    out = []
    if acc.has("Post_Code"):
        v = N.clean(acc.get(f, "Post_Code"))
        if v and not _ZIP5_RE.match(v):
            out.append(Issue(fid, WARNING, "ADDR_ZIP_FORMAT", f"Post_Code '{v}' is not a 5-digit ZIP code"))
    if acc.has("Post_Code4"):
        v = N.clean(acc.get(f, "Post_Code4"))
        if v and not _ZIP4_RE.match(v):
            out.append(Issue(fid, WARNING, "ADDR_ZIP4_FORMAT", f"Post_Code4 '{v}' is not a 4-digit ZIP+4 extension"))
    return out


def empty_required_fields(acc, feature) -> List[str]:
    """Mandatory NENA address fields (and status, type, placement) that are mapped but empty."""
    names = [fd.name for fd in SSAP_FIELDS if fd.req == MANDATORY] + list(_ALSO_REQUIRED)
    out = []
    for name in names:
        if name in _NOT_CHECKED or not acc.has(name):
            continue
        if not N.clean(acc.get(feature, name)):
            out.append(name)
    return out


def empty_fields_message(empty, fillable) -> str:
    """Say which empty fields Fill empty fields can fix and which need a default first."""
    can = [n for n in empty if n in fillable]
    cannot = [n for n in empty if n not in fillable]
    bits = []
    if can:
        bits.append(", ".join(can) + " (Fill empty fields gives them the lookup defaults)")
    if cannot:
        bits.append(", ".join(cannot) + " (no default: tick one in Lookup tables, or enter the values)")
    return "Empty: " + "; ".join(bits)


def address_issues(ctx: AddressContext, check_attribution: bool = True, progress=None) -> List[Issue]:
    acc = ctx.addr_acc
    out: List[Issue] = []
    measures: Dict[str, StreetMeasure] = {}
    segments: Dict = {}                      # centerline features already read, by id
    seen = defaultdict(list)
    by_point = defaultdict(list)             # rounded (x, y) -> [(fid, unit)] - duplicate locations
    per_side = defaultdict(list)            # (street, side) -> [(distance, number, fid)]
    access_tol = ctx.tol * 4
    try:
        from ..lookups import field_defaults
        fillable = {k for k, v in field_defaults(ctx.pcfg, "address").items() if v}
    except Exception:  # noqa: BLE001 - the hint is a convenience
        fillable = set()
    tell(progress, 0, "Reading addresses")
    feats = list(ctx.addr.getFeatures())
    total = max(len(feats), 1)
    by_id = ctx.repo.by_id() if ctx.repo is not None else {}
    wgs = _xf(ctx.addr.crs(), QgsCoordinateReferenceSystem("EPSG:4326"))
    tell(progress, 5, "Checking numbers, sides, ranges, and coordinates")
    for pos, f in enumerate(feats):
        if pos % 200 == 0:
            tell(progress, 5 + 80.0 * pos / total, "Checking numbers, sides, ranges, and coordinates")
        if not f.hasGeometry():
            out.append(Issue(f.id(), ERROR, "ADDR_NO_GEOMETRY", "Address point has no geometry"))
            continue
        empty = empty_required_fields(acc, f)
        if empty:
            out.append(Issue(f.id(), WARNING, "ADDR_EMPTY_FIELDS", empty_fields_message(empty, fillable)))
        roof = ctx.from_addr.transform(_pt(f.geometry()))
        unit = N.clean(acc.get(f, "Unit")).upper() if acc.has("Unit") else ""
        by_point[(round(roof.x() / ctx.tol), round(roof.y() / ctx.tol))].append((f.id(), unit))
        for field_name in ("St_PreDir", "St_PosDir"):
            if acc.has(field_name):
                out.extend(check_directional(f.id(), field_name, acc.get(f, field_name)))
        for field_name in ("St_PreTyp", "St_PosTyp"):
            if acc.has(field_name):
                out.extend(_type_issue(f.id(), field_name, acc.get(f, field_name)))
        out.extend(_zip_issues(f.id(), acc, f))
        num, suf = parse_number(acc.get(f, "Add_Number"), acc.get(f, "AddNum_Suf") if acc.has("AddNum_Suf") else None)
        _latlong_issue(out, acc, f, wgs)
        sid = N.clean(acc.get(f, "StreetID")) if acc.has("StreetID") else ""
        parts = {p: acc.get(f, p) for p in N.NAME_PARTS}
        sid, parts = resolve_street(by_id, sid, parts)
        key = N.name_key(parts)
        community = "" if sid else _address_community(acc, f)
        street = sid or (f"{key}|{community}" if key else "")
        named = _named_place(acc, f)
        if num is None or (num == 0 and named):
            if not named:
                out.append(Issue(f.id(), WARNING, "ADDR_NO_NUMBER", "Address point has no address number"))
            continue
        if not street:
            out.append(Issue(f.id(), WARNING, "ADDR_NO_STREET", f"Address {num}{suf} has no street"))
            continue
        seen[(street, num, suf, unit)].append(f.id())
        if street not in measures:
            measures[street] = StreetMeasure(ctx, sid, key, "" if sid else community)
        m = measures[street]
        if not m.segs:
            out.append(Issue(f.id(), WARNING, "ADDR_STREET_NOT_ON_MAP",
                             f"No centerline carries the street of {num}{suf} {N.full_name(parts)}"))
            continue
        access = stored_access(ctx, f)
        probe = access or roof
        loc = m.locate(QgsPointXY(probe))
        if loc is None:
            continue
        dist, seg_fid, frac, (dx, dy), sq = loc
        if access is not None and sq ** 0.5 > access_tol:
            out.append(Issue(f.id(), WARNING, "ACCESS_OFF_ROAD",
                             "The stored access point is no longer on its street's centerline"))
        stored_side = N.clean(acc.get(f, "Addr_Side")).upper() if acc.has("Addr_Side") else ""
        geo_side = NB.side_of(dx, dy, roof.x() - probe.x(), roof.y() - probe.y()) or ""
        if stored_side in ("L", "R") and geo_side and geo_side != stored_side:
            out.append(Issue(f.id(), WARNING, "ADDR_SIDE_MISMATCH",
                             f"Addr_Side is '{stored_side}' but {num}{suf} sits geometrically on the "
                             f"{geo_side} side of segment {seg_fid}", stored_side, seg_fid))
        # The building's side wins. A stored Addr_Side does not hide a number on the other side of the road.
        side = geo_side or (stored_side if stored_side in ("L", "R") else "")
        if not side:
            continue
        _geometry, length, start = m.segs[seg_fid]
        numbering_dist = start + m.numbering_fraction(seg_fid, frac) * length
        per_side[(street, side)].append((numbering_dist, num, f.id()))
        if seg_fid not in segments:
            segments[seg_fid] = next(ctx.rcl.getFeatures(QgsFeatureRequest().setFilterFid(seg_fid)), None)
        seg = segments[seg_fid]
        if seg is None:
            continue
        names = SIDE_FIELDS[side]
        lo_v, hi_v = ctx.rcl_acc.get_int(seg, names["from"]), ctx.rcl_acc.get_int(seg, names["to"])
        if lo_v is not None and hi_v is not None:
            lo, hi = sorted((lo_v, hi_v))
            if lo == hi == 0:
                out.append(Issue(f.id(), ERROR, "ADDR_OUTSIDE_RANGE",
                                 f"{num}{suf} is on a side of segment {seg_fid} that has no address range"))
            elif not (lo <= num <= hi):
                out.append(Issue(f.id(), ERROR, "ADDR_OUTSIDE_RANGE",
                                 f"{num}{suf} is outside segment {seg_fid}'s {side} range {lo_v}–{hi_v}",
                                 side, seg_fid))
        par = normalize_parity(ctx.rcl_acc.get(seg, names["parity"]), None) if ctx.rcl_acc.has(names["parity"]) \
            else None
        if par is None:
            par = ctx.pcfg.addresses.left_parity if side == "L" else ctx.pcfg.addresses.right_parity
        if par in ("O", "E") and not matches_parity(num, par):
            out.append(Issue(f.id(), ERROR, "ADDR_WRONG_SIDE",
                             f"{num}{suf} is {'even' if num % 2 == 0 else 'odd'} but the {side} side of segment "
                             f"{seg_fid} is {'odd' if par == 'O' else 'even'}", side, seg_fid))
        if check_attribution and _side_rules(ctx.pcfg):
            community, notes, _updates = community_from_segment(ctx, seg, side, frac)
            for n in notes:
                code = "ATTRIB_MULTIPLE" if "overlap" in n else "ATTRIB_NO_POLYGON"
                out.append(Issue(f.id(), WARNING, code, n))
            for k, v in community.items():
                fld = COMMUNITY_FIELDS[k][0]
                if acc.has(fld) and N.clean(acc.get(f, fld)).upper() != N.clean(v).upper():
                    out.append(Issue(f.id(), WARNING, "ATTRIB_MISMATCH",
                                     f"{fld} is '{acc.get(f, fld) or ''}' but the {side} side of the road says '{v}'"))
    tell(progress, 88, "Checking duplicate addresses")
    for (street, num, suf, unit), fids in seen.items():
        if len(fids) > 1:
            for fid in fids:
                out.append(Issue(fid, ERROR, "ADDR_DUPLICATE",
                                 f"{num}{suf}{' unit ' + unit if unit else ''} appears {len(fids)} times on this street",
                                 related_fid=[x for x in fids if x != fid][0]))
    tell(progress, 92, "Checking stacked address points")
    for entries in by_point.values():
        if len(entries) < 2:
            continue
        units = [u for _fid, u in entries]
        if "" not in units and len(set(units)) == len(units):
            continue  # different units of the same building sharing one point - expected
        fids = [fid for fid, _u in entries]
        for fid in fids:
            out.append(Issue(fid, WARNING, "ADDR_DUPLICATE_LOCATION",
                             f"{len(fids)} address points sit at the same location",
                             related_fid=[x for x in fids if x != fid][0]))
    tell(progress, 96, "Checking address order")
    for (street, side), rows in per_side.items():
        rows.sort()
        top, top_fid = None, None
        for dist, num, fid in rows:
            if top is not None and num < top:
                out.append(Issue(fid, WARNING, "ADDR_OUT_OF_ORDER",
                                 f"{num} comes after {top} along the street (side {side})", side, top_fid))
            if top is None or num > top:
                top, top_fid = num, fid
    tell(progress, 100.0, "Finished")
    return out


def _latlong_issue(out, acc, feature, wgs) -> None:
    if not acc.has("Longitude") or not acc.has("Latitude"):
        return
    lon, lat = _float_or_none(acc.get(feature, "Longitude")), _float_or_none(acc.get(feature, "Latitude"))
    if lon is None or lat is None or (lon == 0.0 and lat == 0.0):
        return
    try:
        point = wgs.transform(_pt(feature.geometry()))
    except Exception:  # noqa: BLE001 - a CRS the project cannot transform is not a mismatch
        return
    gap = lonlat_metres(lon, lat, point.x(), point.y())
    if gap > _LATLONG_TOL_M:
        out.append(Issue(feature.id(), WARNING, "ADDR_LATLONG_MISMATCH",
                         f"Stored longitude/latitude is {gap:.0f} m from the point"))


def centerline_boundary_issues(ctx: AddressContext) -> List[Issue]:
    if not _side_rules(ctx.pcfg):
        return []
    _n, crossings = attribute_centerline_sides(ctx, write=False)
    return [Issue(fid, WARNING, "RCL_CROSSES_BOUNDARY",
                  f"The {side} side crosses a boundary partway along – split the segment at the boundary", side)
            for fid, side in crossings]





def _side_rules(pcfg):
    """Settings, Sides rules plus PSAP from the PSAP zone layer (see zones.side_rules)."""
    from ..zones import side_rules
    return side_rules(pcfg)
