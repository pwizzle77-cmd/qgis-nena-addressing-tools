# -*- coding: utf-8 -*-
"""
Pure-Python address-ranging algorithms (no QGIS dependency).

Concepts
--------
* A *side range* is (from_addr, to_addr, parity) for one side of a segment.
  Parity is one of O (odd), E (even), B (both) or Z (zero / no addresses).
* Ranging works on an *ordered chain* of segments (digitized direction ==
  increasing addresses). Every method boils down to a list of continuous
  "address positions" ``a_0 <= a_1 <= ... <= a_n`` at the segment
  boundaries. Segment ``k`` receives the parity-conforming integers in the
  half-open interval ``[a_k, a_{k+1})``. Allocation is sequential, so
  consecutive segments can never overlap, even when a segment is so short
  that it only receives a single number.
"""

import math
from typing import Dict, List, Optional, Sequence, Tuple

ODD, EVEN, BOTH, ZERO = "O", "E", "B", "Z"
VALID_PARITIES = (ODD, EVEN, BOTH, ZERO)

SideRange = Tuple[int, int, str]   # (from, to, parity)

_UNIT_TO_M = {
    "meters": 1.0,
    "feet": 0.3048,
    "us_survey_feet": 1200.0 / 3937.0,
    "kilometers": 1000.0,
    "miles": 1609.344,
}


def to_meters(value: float, units: str) -> float:
    return float(value) * _UNIT_TO_M.get(units, 1.0)


# ---------------------------------------------------------------------------
# parity helpers
# ---------------------------------------------------------------------------
def normalize_parity(value, default: Optional[str] = None) -> Optional[str]:
    if value is None:
        return default
    s = str(value).strip().upper()
    if s in VALID_PARITIES:
        return s
    # tolerate spelled-out values seen in legacy data
    mapping = {"ODD": ODD, "EVEN": EVEN, "BOTH": BOTH, "ZERO": ZERO, "NONE": ZERO, "N": ZERO}
    return mapping.get(s, default)


def parity_step(parity: str) -> int:
    if parity in (ODD, EVEN):
        return 2
    if parity == BOTH:
        return 1
    return 0


def matches_parity(n: int, parity: str) -> bool:
    if parity == ODD:
        return n % 2 == 1
    if parity == EVEN:
        return n % 2 == 0
    if parity == ZERO:
        return n == 0
    return True


def conform_up(n: int, parity: str) -> int:
    """Smallest integer >= n with the requested parity."""
    if parity == ODD:
        return n if n % 2 == 1 else n + 1
    if parity == EVEN:
        return n if n % 2 == 0 else n + 1
    return n


def conform_down(n: int, parity: str) -> int:
    """Largest integer <= n with the requested parity."""
    if parity == ODD:
        return n if n % 2 == 1 else n - 1
    if parity == EVEN:
        return n if n % 2 == 0 else n - 1
    return n


def infer_parity(numbers: Sequence[int]) -> str:
    nums = [n for n in numbers if n is not None]
    if not nums:
        return ZERO
    odd = any(n % 2 for n in nums)
    even = any(n % 2 == 0 for n in nums)
    if odd and even:
        return BOTH
    return ODD if odd else EVEN


def is_zero_range(f, t) -> bool:
    return (f in (None, 0)) and (t in (None, 0))


# ---------------------------------------------------------------------------
# core sequential allocator
# ---------------------------------------------------------------------------
def _ceil(x: float) -> int:
    return int(math.ceil(round(x, 6)))


def allocate(boundaries: Sequence[float], parities: Sequence[str],
             clamp_max: Optional[int] = None) -> List[Optional[Tuple[int, int]]]:
    """
    Allocate integer ranges to consecutive segments.

    :param boundaries: n+1 non-decreasing address positions.
    :param parities:   n parities (one per segment). Z segments get (0, 0)
                       and do not consume numbers.
    :param clamp_max:  optional hard upper limit (inclusive). Segments that
                       cannot get a number at or below it receive ``None``.
    :returns: list of (from, to) tuples (from <= to) or None.
    """
    n = len(boundaries) - 1
    if n != len(parities):
        raise ValueError("need exactly one parity per segment")
    out: List[Optional[Tuple[int, int]]] = []
    next_avail: Dict[str, int] = {}   # per parity class, so O and E sides do not block each other
    for k in range(n):
        p = parities[k]
        if p == ZERO:
            out.append((0, 0))
            continue
        step = parity_step(p) or 1
        lo = conform_up(_ceil(boundaries[k]), p)
        nk = next_avail.get(p)
        if nk is not None and lo < nk:
            lo = conform_up(nk, p)
        hi = conform_down(_ceil(boundaries[k + 1]) - 1, p)
        if clamp_max is not None:
            if lo > clamp_max:
                out.append(None)
                continue
            hi = min(hi, conform_down(clamp_max, p))
        if hi < lo:
            hi = lo   # degenerate: the segment still gets one number
        out.append((lo, hi))
        next_avail[p] = hi + step
        # an "both" segment blocks odd and even numbers alike
        if p == BOTH:
            next_avail[ODD] = max(next_avail.get(ODD, hi + 1), hi + 1)
            next_avail[EVEN] = max(next_avail.get(EVEN, hi + 1), hi + 1)
        elif p in (ODD, EVEN):
            next_avail[BOTH] = max(next_avail.get(BOTH, hi + 1), hi + 1)
    return out


def cumulative(lengths: Sequence[float]) -> List[float]:
    acc = [0.0]
    for ln in lengths:
        acc.append(acc[-1] + max(float(ln or 0.0), 0.0))
    return acc


# ---------------------------------------------------------------------------
# boundary generators for the ranging methods
# ---------------------------------------------------------------------------
def hundred_block_boundaries(n_segments: int, base: int, block_size: int) -> List[float]:
    """Each segment gets one full block: base_block, base_block+bs, ..."""
    bs = max(int(block_size), 1)
    start = (int(base) // bs) * bs
    return [float(start + k * bs) for k in range(n_segments + 1)]


def interval_boundaries(lengths_m: Sequence[float], base: int, metres_per_number: float) -> List[float]:
    """Address grows by one number every ``metres_per_number`` along the chain."""
    if metres_per_number <= 0:
        raise ValueError("interval distance must be > 0")
    return [base + d / metres_per_number for d in cumulative(lengths_m)]


def proportional_boundaries(lengths_m: Sequence[float], range_from: int, range_to: int) -> List[float]:
    """Spread the inclusive range [range_from, range_to] proportionally to length."""
    cum = cumulative(lengths_m)
    total = cum[-1]
    span = (range_to + 1) - range_from
    if total <= 0:
        # zero-length chain: share equally
        n = len(lengths_m)
        return [range_from + span * k / max(n, 1) for k in range(n + 1)]
    return [range_from + span * d / total for d in cum]


# ---------------------------------------------------------------------------
# high-level helpers
# ---------------------------------------------------------------------------
def range_chain(boundaries: Sequence[float], side_parities: Dict[str, Sequence[str]]
                ) -> Dict[str, List[Optional[SideRange]]]:
    """Apply :func:`allocate` for each side. Returns {'L': [...], 'R': [...]}."""
    result = {}
    for side, pars in side_parities.items():
        alloc = allocate(boundaries, pars)
        result[side] = [None if a is None else (a[0], a[1], pars[i]) for i, a in enumerate(alloc)]
    return result


Anchor = Tuple[float, int]   # (distance along the line in metres, house number)


def monotone_anchors(anchors: Sequence[Anchor], lo: int, hi: int, parity: str) -> List[Anchor]:
    """
    Keep the anchors that can be trusted: inside [lo, hi], matching the side's
    parity, and forming the longest sequence whose numbers do not decrease
    with distance (outliers such as a mis-numbered or mis-snapped point are
    dropped).
    """
    pts = sorted((float(d), int(n)) for d, n in anchors
                 if n is not None and lo <= int(n) <= hi and matches_parity(int(n), parity))
    if len(pts) < 2:
        return pts
    # longest non-decreasing subsequence on the numbers (O(n^2) is fine: few points per segment)
    best = [1] * len(pts)
    prev = [-1] * len(pts)
    for i in range(len(pts)):
        for j in range(i):
            if pts[j][1] <= pts[i][1] and best[j] + 1 > best[i]:
                best[i], prev[i] = best[j] + 1, j
    i = max(range(len(pts)), key=lambda k: (best[k], -k))
    keep = []
    while i >= 0:
        keep.append(pts[i])
        i = prev[i]
    return keep[::-1]


def anchored_boundaries(lengths_m: Sequence[float], range_from: int, range_to: int,
                        anchors: Sequence[Anchor]) -> List[float]:
    """
    Like :func:`proportional_boundaries`, but the address-vs-distance curve is
    piecewise linear through the anchors (existing address points), and every
    boundary is clamped so that an anchored number always stays on the piece
    the point actually lies along.

    Anchors must already be filtered (see :func:`monotone_anchors`).
    """
    cum = cumulative(lengths_m)
    total = cum[-1]
    if not anchors or total <= 0:
        return proportional_boundaries(lengths_m, range_from, range_to)
    knots = [(0.0, float(range_from))]
    for d, n in anchors:
        d = min(max(d, 0.0), total)
        knots.append((d, float(n)))
    knots.append((total, float(range_to + 1)))

    def interp(x: float) -> float:
        for (d0, a0), (d1, a1) in zip(knots, knots[1:]):
            if d0 <= x <= d1:
                if d1 - d0 <= 1e-12:
                    return a1
                return a0 + (a1 - a0) * (x - d0) / (d1 - d0)
        return knots[-1][1]

    bounds = [float(range_from)]
    for d in cum[1:-1]:
        a = interp(d)
        before = [n for (ad, n) in anchors if ad < d]
        after = [n for (ad, n) in anchors if ad >= d]
        if before:
            a = max(a, max(before) + 1)
        if after:
            a = min(a, min(after))
        bounds.append(a)
    bounds.append(float(range_to + 1))
    # keep boundaries non-decreasing even if clamps fought each other
    for i in range(1, len(bounds)):
        bounds[i] = max(bounds[i], bounds[i - 1])
    return bounds


def redistribute(from_addr: Optional[int], to_addr: Optional[int], parity: Optional[str],
                 lengths_m: Sequence[float], anchors: Optional[Sequence[Anchor]] = None) -> List[SideRange]:
    """
    Split one side range across ordered pieces proportionally to length.

    Used after a segment has been split. The first piece keeps the original
    FROM, the last piece keeps the original TO (as far as parity allows);
    decreasing ranges (FROM > TO) are preserved as decreasing.
    Pieces that cannot receive a number inside the original range get
    (0, 0, 'Z').

    ``anchors`` – optional (distance_m from the original start, number) pairs
    from address points on this side. When given, the split boundaries follow
    the points (piecewise-linear interpolation) so a point near the split
    keeps its number on the piece it lies along.
    """
    n = len(lengths_m)
    p = normalize_parity(parity, None)
    if is_zero_range(from_addr, to_addr) or p == ZERO:
        return [(0, 0, ZERO)] * n
    f = int(from_addr or 0)
    t = int(to_addr if to_addr is not None else f)
    if p is None:
        p = infer_parity([f, t]) if f and t else BOTH
    decreasing = f > t
    lo, hi = (t, f) if decreasing else (f, t)
    lengths = list(lengths_m)
    total = sum(max(float(x or 0), 0.0) for x in lengths)
    anc = list(anchors or [])
    if decreasing:
        lengths = lengths[::-1]          # allocate from the low end, then mirror back
        anc = [(total - d, num) for d, num in anc]
    anc = monotone_anchors(anc, lo, hi, p)
    bounds = anchored_boundaries(lengths, lo, hi, anc) if anc else proportional_boundaries(lengths, lo, hi)
    alloc = allocate(bounds, [p] * n, clamp_max=hi)
    pieces: List[SideRange] = []
    for a in alloc:
        if a is None:
            pieces.append((0, 0, ZERO))
        else:
            pieces.append((a[0], a[1], p))
    if decreasing:
        pieces = [(b, a, par) if par != ZERO else (0, 0, ZERO) for (a, b, par) in pieces[::-1]]
    return pieces


def combine(ranges: Sequence[Optional[SideRange]]) -> SideRange:
    """Combine side ranges of segments being merged into one."""
    real = [r for r in ranges if r is not None and not is_zero_range(r[0], r[1])]
    if not real:
        return (0, 0, ZERO)
    inc = sum(1 for r in real if (r[0] or 0) <= (r[1] or 0))
    increasing = inc >= len(real) - inc
    lows = [min(r[0] or 0, r[1] or 0) for r in real]
    highs = [max(r[0] or 0, r[1] or 0) for r in real]
    pars = {normalize_parity(r[2], BOTH) for r in real}
    par = pars.pop() if len(pars) == 1 else BOTH
    if increasing:
        return (min(lows), max(highs), par)
    return (max(highs), min(lows), par)


def extend_to(from_addr: int, parity: str, length_m: float, metres_per_number: float) -> int:
    """Interval method for a single segment whose FROM is fixed: compute the new TO."""
    p = normalize_parity(parity, BOTH)
    if p == ZERO:
        return 0
    bounds = [float(from_addr), from_addr + length_m / metres_per_number]
    a = allocate(bounds, [p])[0]
    return a[1] if a else from_addr


def ranges_from_observations(observations: Sequence[Sequence[int]], parity: str, block_size: int,
                             expand: bool, infer: bool) -> List[Optional[SideRange]]:
    """
    Derive side ranges from address numbers observed along an ordered chain.

    :param observations: per segment (chain order), the house numbers found on this side.
    :param parity: configured parity, used when ``infer`` is False.
    :param expand: widen observed min/max to the enclosing block, without
                   touching the neighbouring segments' observed numbers.
    :returns: per segment a SideRange, or None when no numbers were observed.
    """
    n = len(observations)
    bs = max(int(block_size), 1)
    base: List[Optional[SideRange]] = []
    for obs in observations:
        nums = sorted(int(x) for x in obs if x is not None)
        if not nums:
            base.append(None)
            continue
        p = infer_parity(nums) if infer else (normalize_parity(parity, BOTH))
        if p == ZERO:
            p = BOTH
        base.append((conform_up(nums[0], p), conform_down(nums[-1], p), p))
        if base[-1][1] < base[-1][0]:
            base[-1] = (nums[0], nums[-1], BOTH)
    if not expand:
        return base

    out: List[Optional[SideRange]] = []
    prev_hi: Optional[int] = None
    for k in range(n):
        r = base[k]
        if r is None:
            out.append(None)
            continue
        lo, hi, p = r
        lo_e = conform_up((lo // bs) * bs, p)
        hi_e = conform_down((hi // bs) * bs + bs - 1, p)
        if prev_hi is not None:
            lo_e = max(lo_e, conform_up(prev_hi + 1, p))
        # look ahead to the next segment that has observations
        nxt = next((base[j] for j in range(k + 1, n) if base[j] is not None), None)
        if nxt is not None:
            hi_e = min(hi_e, conform_down(nxt[0] - 1, p))
        lo_e = min(lo_e, lo)
        hi_e = max(hi_e, hi)
        out.append((lo_e, hi_e, p))
        prev_hi = hi_e
    return out
