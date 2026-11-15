# -*- coding: utf-8 -*-
"""
Pure-Python validation rules (no QGIS dependency).

Every rule returns a list of :class:`Issue`. The QGIS-side validator
(``validator.py``) reads the layer into plain records and calls these.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Hashable, Iterable, List, Optional, Sequence, Tuple

from .ranging import (BOTH, ZERO, VALID_PARITIES, conform_up, matches_parity,
                      normalize_parity, parity_step, is_zero_range)
from .schema import (DIRECTIONAL_DOMAIN, DIRECTIONAL_ABBREVIATIONS, ONEWAY_DOMAIN,
                     NGUID_URN_RE, NGUID_LEGACY_RE)

ERROR = "Error"
WARNING = "Warning"
INFO = "Info"
SEVERITY_ORDER = {ERROR: 0, WARNING: 1, INFO: 2}


@dataclass
class Issue:
    fid: Optional[Hashable]     # None = layer-level issue
    severity: str
    code: str
    message: str
    side: str = ""
    related_fid: Optional[Hashable] = None

    def as_row(self):
        return [self.severity, "" if self.fid is None else str(self.fid), self.code, self.side,
                self.message, "" if self.related_fid is None else str(self.related_fid)]


@dataclass
class RangeRecord:
    """One side range of one feature, as read from the layer."""
    fid: Hashable
    side: str
    from_addr: Optional[int]
    to_addr: Optional[int]
    parity: Optional[str]     # raw value as stored
    group: Optional[str]      # street (+community) key; None = not comparable


# ---------------------------------------------------------------------------
# per-side checks
# ---------------------------------------------------------------------------
def check_side(rec: RangeRecord, allow_decreasing: bool) -> List[Issue]:
    out: List[Issue] = []
    f, t, raw = rec.from_addr, rec.to_addr, rec.parity
    p = normalize_parity(raw, None)
    side = rec.side
    if raw in (None, ""):
        out.append(Issue(rec.fid, ERROR, "PARITY_MISSING", f"Parity_{side} is empty", side))
    elif p is None or str(raw).strip().upper() not in VALID_PARITIES:
        out.append(Issue(rec.fid, ERROR, "PARITY_DOMAIN",
                         f"Parity_{side} '{raw}' is not one of O, E, B, Z", side))
    if f is None or t is None:
        out.append(Issue(rec.fid, ERROR, "RANGE_MISSING",
                         f"FromAddr_{side}/ToAddr_{side} is empty (use 0/0 with parity Z for no addresses)", side))
        return out
    if f < 0 or t < 0:
        out.append(Issue(rec.fid, ERROR, "RANGE_NEGATIVE", f"Negative address number on {side} side", side))
    zero = is_zero_range(f, t)
    if (f == 0) != (t == 0):
        out.append(Issue(rec.fid, ERROR, "RANGE_HALF_ZERO",
                         f"{side} range {f}-{t}: one end is 0, the other is not", side))
    if p == ZERO and not zero:
        out.append(Issue(rec.fid, ERROR, "PARITY_ZERO_MISMATCH",
                         f"Parity_{side} is Z but range is {f}-{t}", side))
    if zero and p not in (None, ZERO):
        out.append(Issue(rec.fid, WARNING, "PARITY_ZERO_MISMATCH",
                         f"{side} range is 0-0 but Parity_{side} is {p} (expected Z)", side))
    if not zero and p in ("O", "E"):
        bad = [n for n in (f, t) if n != 0 and not matches_parity(n, p)]
        if bad:
            out.append(Issue(rec.fid, ERROR, "PARITY_NUMBER_MISMATCH",
                             f"Parity_{side} is {p} but {', '.join(map(str, bad))} does not match", side))
    if not zero and f > t and not allow_decreasing:
        out.append(Issue(rec.fid, WARNING, "RANGE_REVERSED",
                         f"{side} range is decreasing ({f} > {t}); addresses should increase in digitized direction",
                         side))
    return out


# ---------------------------------------------------------------------------
# overlaps (parity aware, both sides pooled per street group)
# ---------------------------------------------------------------------------
def _common_number(lo1, hi1, p1, lo2, hi2, p2) -> bool:
    lo, hi = max(lo1, lo2), min(hi1, hi2)
    if lo > hi:
        return False
    a = p1 if p1 in ("O", "E") else None
    b = p2 if p2 in ("O", "E") else None
    if a and b and a != b:
        return False
    target = a or b or BOTH
    return conform_up(lo, target) <= hi


def find_overlaps(records: Iterable[RangeRecord]) -> List[Issue]:
    """
    Flag address numbers that would be duplicated on the same street.

    Both sides are pooled per group because a number is meant to be unique on
    a street regardless of which side it falls on. Zero ranges and records
    without a group are ignored.
    """
    groups: Dict[str, List[Tuple[int, int, str, RangeRecord]]] = defaultdict(list)
    for r in records:
        if r.group is None or r.from_addr is None or r.to_addr is None:
            continue
        if is_zero_range(r.from_addr, r.to_addr):
            continue
        p = normalize_parity(r.parity, BOTH)
        if p == ZERO:
            continue
        lo, hi = sorted((int(r.from_addr), int(r.to_addr)))
        groups[r.group].append((lo, hi, p, r))
    issues: List[Issue] = []
    for g, items in groups.items():
        items.sort(key=lambda x: (x[0], x[1]))
        active: List[Tuple[int, int, str, RangeRecord]] = []
        for it in items:
            lo, hi, p, r = it
            active = [a for a in active if a[1] >= lo]
            for a in active:
                if a[3].fid == r.fid:
                    # both sides of the same segment: only a problem when they share numbers
                    if _common_number(a[0], a[1], a[2], lo, hi, p):
                        issues.append(Issue(r.fid, WARNING, "OVERLAP_SAME_SEGMENT",
                                            f"Left and right ranges share numbers ({a[0]}-{a[1]} / {lo}-{hi})", ""))
                    continue
                if _common_number(a[0], a[1], a[2], lo, hi, p):
                    issues.append(Issue(r.fid, ERROR, "OVERLAP",
                                        f"{r.side} range {lo}-{hi} overlaps {a[3].side} range {a[0]}-{a[1]} "
                                        f"of feature {a[3].fid} on '{g}'", r.side, a[3].fid))
            active.append(it)
    return issues


# ---------------------------------------------------------------------------
# gaps between consecutive segments of a chain
# ---------------------------------------------------------------------------
def check_chain_gaps(chain: Sequence[Dict[str, RangeRecord]], tolerance: int) -> List[Issue]:
    """
    :param chain: ordered list; each item maps side ('L'/'R') -> RangeRecord.
    """
    issues: List[Issue] = []
    for a, b in zip(chain, chain[1:]):
        for side in ("L", "R"):
            ra, rb = a.get(side), b.get(side)
            if ra is None or rb is None:
                continue
            if None in (ra.from_addr, ra.to_addr, rb.from_addr, rb.to_addr):
                continue
            if is_zero_range(ra.from_addr, ra.to_addr) or is_zero_range(rb.from_addr, rb.to_addr):
                continue
            pa = normalize_parity(ra.parity, BOTH)
            pb = normalize_parity(rb.parity, BOTH)
            step = parity_step(pb if pb == pa else BOTH) or 1
            a_hi = max(ra.from_addr, ra.to_addr)
            b_lo = min(rb.from_addr, rb.to_addr)
            if b_lo <= a_hi:
                issues.append(Issue(rb.fid, WARNING, "NOT_INCREASING",
                                    f"{side} range starts at {b_lo}, not above upstream feature {ra.fid} "
                                    f"which ends at {a_hi}", side, ra.fid))
                continue
            gap = b_lo - a_hi - step
            if gap > tolerance:
                issues.append(Issue(rb.fid, WARNING if gap > 2 * max(tolerance, 1) else INFO, "GAP",
                                    f"{side} gap of {gap} number(s) after upstream feature {ra.fid} "
                                    f"({a_hi} -> {b_lo})", side, ra.fid))
    return issues


# ---------------------------------------------------------------------------
# attribute domains
# ---------------------------------------------------------------------------
def check_directional(fid, field_name: str, value) -> List[Issue]:
    if value in (None, ""):
        return []
    v = str(value).strip()
    if v in DIRECTIONAL_DOMAIN:
        return []
    fix = DIRECTIONAL_ABBREVIATIONS.get(v.upper().replace(".", ""))
    if fix:
        return [Issue(fid, WARNING, "DIRECTIONAL_ABBREVIATED",
                      f"{field_name} '{v}' must be spelled out ('{fix}')")]
    if v.title() in DIRECTIONAL_DOMAIN:
        return [Issue(fid, INFO, "DIRECTIONAL_CASE", f"{field_name} '{v}' should be '{v.title()}'")]
    return [Issue(fid, ERROR, "DIRECTIONAL_DOMAIN", f"{field_name} '{v}' is not a valid directional")]


def check_oneway(fid, value) -> List[Issue]:
    if value in (None, ""):
        return []
    if str(value).strip().upper() in ONEWAY_DOMAIN:
        return []
    return [Issue(fid, ERROR, "ONEWAY_DOMAIN", f"OneWay '{value}' is not B, FT or TF")]


def check_speed(fid, value) -> List[Issue]:
    if value in (None, ""):
        return []
    try:
        v = int(value)
    except (TypeError, ValueError):
        return [Issue(fid, ERROR, "SPEED_DOMAIN", f"SpeedLimit '{value}' is not an integer")]
    if v <= 0 or v > 100:
        return [Issue(fid, WARNING, "SPEED_RANGE", f"SpeedLimit {v} is outside 1-100")]
    if v % 5:
        return [Issue(fid, INFO, "SPEED_ODD", f"SpeedLimit {v} is not a multiple of 5")]
    return []


def check_nguid_format(fid, value) -> List[Issue]:
    if value in (None, ""):
        return [Issue(fid, ERROR, "NGUID_MISSING", "RCL_NGUID is empty")]
    v = str(value).strip()
    if NGUID_URN_RE.match(v):
        return []
    if NGUID_LEGACY_RE.match(v):
        return [Issue(fid, INFO, "NGUID_LEGACY",
                      "RCL_NGUID uses the legacy 'RCL_id@agency' form; NENA-STA-006.2 uses "
                      "'urn:emergency:uid:gis:RCL:{id}:{agency}'")]
    return [Issue(fid, WARNING, "NGUID_FORMAT", f"RCL_NGUID '{v}' does not follow a NENA NGUID pattern")]


def find_duplicates(values: Dict[Hashable, object], code: str, label: str) -> List[Issue]:
    seen: Dict[str, Hashable] = {}
    out = []
    for fid, v in values.items():
        if v in (None, ""):
            continue
        k = str(v).strip()
        if k in seen:
            out.append(Issue(fid, ERROR, code, f"Duplicate {label} '{k}' (also on feature {seen[k]})",
                             related_fid=seen[k]))
        else:
            seen[k] = fid
    return out


OVERLAP_CODES = ("OVERLAP", "OVERLAP_SAME_SEGMENT")


def involved_fids(issues: Iterable[Issue], codes: Optional[Sequence[str]] = None) -> List[Hashable]:
    """
    Feature ids involved in issues (both the reported feature and the related
    one, e.g. both sides of an overlap), optionally limited to some rule codes.
    """
    out = set()
    for i in issues:
        if codes is not None and i.code not in codes:
            continue
        for fid in (i.fid, i.related_fid):
            if fid is not None:
                out.add(fid)
    return sorted(out, key=lambda x: (str(type(x)), x))


def overlap_fids(issues: Iterable[Issue]) -> List[Hashable]:
    """Every feature taking part in an address-range overlap."""
    return involved_fids(issues, OVERLAP_CODES)


def sort_issues(issues: List[Issue]) -> List[Issue]:
    return sorted(issues, key=lambda i: (SEVERITY_ORDER.get(i.severity, 9), i.code, str(i.fid)))
