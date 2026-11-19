# -*- coding: utf-8 -*-
"""Address validation: empty fields, range, side/parity, order, duplicates, access point, boundary attribution."""

from collections import defaultdict
from typing import Dict, List

from qgis.core import QgsFeatureRequest, QgsPointXY

from ..ranging import matches_parity, normalize_parity
from ..rules import ERROR, WARNING, Issue
from ..schema import SIDE_FIELDS
from ..streets import names as N
from . import numbering as NB
from .engine import (AddressContext, StreetMeasure, _pt, attribute_centerline_sides, community_from_segment,
                     parse_number, stored_access)
from ..schema import MANDATORY
from .ssap import COMMUNITY_FIELDS, SSAP_FIELDS

# Not NENA mandatory, but the fields that are most often left empty and have a lookup default.
_ALSO_REQUIRED = ("AddressStatus", "AddressType", "Placement")
# Filled by the plugin itself or checked elsewhere (number, street).
_NOT_CHECKED = {"Add_Number", "DateUpdate"}


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


def address_issues(ctx: AddressContext, check_attribution: bool = True) -> List[Issue]:
    acc = ctx.addr_acc
    out: List[Issue] = []
    measures: Dict[str, StreetMeasure] = {}
    segments: Dict = {}                      # centerline features already read, by id
    seen = defaultdict(list)
    per_side = defaultdict(list)            # (street, side) -> [(distance, number, fid)]
    access_tol = ctx.tol * 4
    try:
        from ..lookups import field_defaults
        fillable = {k for k, v in field_defaults(ctx.pcfg, "address").items() if v}
    except Exception:  # noqa: BLE001 - the hint is a convenience
        fillable = set()
    for f in list(ctx.addr.getFeatures()):
        if not f.hasGeometry():
            out.append(Issue(f.id(), ERROR, "ADDR_NO_GEOMETRY", "Address point has no geometry"))
            continue
        empty = empty_required_fields(acc, f)
        if empty:
            out.append(Issue(f.id(), WARNING, "ADDR_EMPTY_FIELDS", empty_fields_message(empty, fillable)))
        num, suf = parse_number(acc.get(f, "Add_Number"), acc.get(f, "AddNum_Suf") if acc.has("AddNum_Suf") else None)
        sid = N.clean(acc.get(f, "StreetID")) if acc.has("StreetID") else ""
        parts = {p: acc.get(f, p) for p in N.NAME_PARTS}
        key = N.name_key(parts)
        street = sid or key
        if num is None:
            out.append(Issue(f.id(), WARNING, "ADDR_NO_NUMBER", "Address point has no address number"))
            continue
        if not street:
            out.append(Issue(f.id(), WARNING, "ADDR_NO_STREET", f"Address {num}{suf} has no street"))
            continue
        unit = N.clean(acc.get(f, "Unit")).upper() if acc.has("Unit") else ""
        seen[(street, num, suf, unit)].append(f.id())
        if street not in measures:
            measures[street] = StreetMeasure(ctx, sid, key)
        m = measures[street]
        if not m.segs:
            out.append(Issue(f.id(), WARNING, "ADDR_STREET_NOT_ON_MAP",
                             f"No centerline carries the street of {num}{suf} {N.full_name(parts)}"))
            continue
        roof = ctx.from_addr.transform(_pt(f.geometry()))
        access = stored_access(ctx, f)
        probe = access or roof
        loc = m.locate(QgsPointXY(probe))
        if loc is None:
            continue
        dist, seg_fid, frac, (dx, dy), sq = loc
        if access is not None and sq ** 0.5 > access_tol:
            out.append(Issue(f.id(), WARNING, "ACCESS_OFF_ROAD",
                             "The stored access point is no longer on its street's centerline"))
        side = N.clean(acc.get(f, "Addr_Side")).upper() if acc.has("Addr_Side") else ""
        if side not in ("L", "R"):
            side = NB.side_of(dx, dy, roof.x() - probe.x(), roof.y() - probe.y()) or ""
        if not side:
            continue
        per_side[(street, side)].append((dist, num, f.id()))
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
    for (street, num, suf, unit), fids in seen.items():
        if len(fids) > 1:
            for fid in fids:
                out.append(Issue(fid, ERROR, "ADDR_DUPLICATE",
                                 f"{num}{suf}{' unit ' + unit if unit else ''} appears {len(fids)} times on this street",
                                 related_fid=[x for x in fids if x != fid][0]))
    for (street, side), rows in per_side.items():
        rows.sort()
        top, top_fid = None, None
        for dist, num, fid in rows:
            if top is not None and num < top:
                out.append(Issue(fid, WARNING, "ADDR_OUT_OF_ORDER",
                                 f"{num} comes after {top} along the street (side {side})", side, top_fid))
            if top is None or num > top:
                top, top_fid = num, fid
    return out


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
