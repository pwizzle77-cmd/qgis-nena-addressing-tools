# -*- coding: utf-8 -*-
"""
Address-number calculation (pure Python, no QGIS).

The number of a new address comes from where its driveway meets the road
(the *access point*):

1. **side** – the rooftop's side of the road relative to the road direction
   at the access point;
2. **raw position** – on the segment that was clicked, by the configured method:
     * ``distance`` (default): the segment's From on that side (or the start
       number) + distance from the segment's first point ÷ distance per
       number (e.g. 5.28 ft = 1000 numbers per mile);
     * ``interpolate``: FROM + (TO − FROM) × fraction along the segment's
       range on that side;
     * ``frontage``: the segment's From (or the start number) + step × whole
       frontage intervals from the segment's first point (e.g. next pair every 50 ft);
3. **parity** – rounded (nearest or down) to the side's odd/even;
4. **order** – kept between the existing addresses on that side of the
   same segment so numbers keep increasing along it;
5. **collision** – a taken number is resolved by rule: next free number,
   a suffix (101A), or the user is asked.
"""

import math
import string
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from ..ranging import BOTH, EVEN, ODD, ZERO, conform_down, conform_up, normalize_parity

M_DISTANCE = "distance"
M_INTERPOLATE = "interpolate"
M_FRONTAGE = "frontage"
METHODS = (M_DISTANCE, M_INTERPOLATE, M_FRONTAGE)

C_NEXT = "next"
C_SUFFIX = "suffix"
C_ASK = "ask"


@dataclass
class NumberingSettings:
    method: str = M_DISTANCE
    metres_per_number: float = 5.28 * 0.3048     # 5.28 ft = 1000 numbers per mile
    start_number: int = 100
    frontage_m: float = 50 * 0.3048
    frontage_step: int = 2
    rounding: str = "nearest"                     # nearest | down
    order_check: bool = True
    collision: str = C_NEXT


@dataclass
class Neighbor:
    distance: float          # metres along the segment (same measure as the new address)
    number: int
    suffix: str = ""


@dataclass
class NumberResult:
    number: Optional[int]
    suffix: str = ""
    raw: Optional[float] = None
    parity: str = BOTH
    ok: bool = True
    needs_choice: bool = False
    message: str = ""
    options: List[str] = field(default_factory=list)   # e.g. ["107", "105A"] when the user must choose

    @property
    def label(self) -> str:
        return "" if self.number is None else f"{self.number}{self.suffix}"


# ---------------------------------------------------------------------------
def side_of(road_dx: float, road_dy: float, to_x: float, to_y: float) -> Optional[str]:
    """'L' / 'R' for a point offset (to_x, to_y) from the road whose direction is (road_dx, road_dy)."""
    cross = road_dx * to_y - road_dy * to_x
    if abs(cross) < 1e-12:
        return None
    return "L" if cross > 0 else "R"


def raw_position(s: NumberingSettings, distance_m: Optional[float] = None, frac: Optional[float] = None,
                 seg_from: Optional[int] = None, seg_to: Optional[int] = None) -> float:
    if s.method == M_INTERPOLATE:
        if seg_from is None or seg_to is None or frac is None:
            raise ValueError("the segment has no address range on this side")
        return seg_from + (seg_to - seg_from) * max(0.0, min(1.0, frac))
    if distance_m is None:
        raise ValueError("no distance from the road origin")
    if s.method == M_FRONTAGE:
        if s.frontage_m <= 0:
            raise ValueError("frontage interval must be > 0")
        return float(s.start_number + s.frontage_step * math.floor(distance_m / s.frontage_m + 1e-9))
    if s.metres_per_number <= 0:
        raise ValueError("distance per number must be > 0")
    return s.start_number + distance_m / s.metres_per_number


def round_to_parity(x: float, parity: str, rounding: str = "nearest") -> int:
    p = normalize_parity(parity, BOTH)
    if p == ZERO:
        p = BOTH
    lo = conform_down(math.floor(x + 1e-9), p)
    if rounding == "down":
        return max(lo, 0)
    hi = conform_up(math.ceil(x - 1e-9), p)
    best = lo if abs(x - lo) <= abs(hi - x) else hi
    return max(best, 0)


def _free_between(candidate, parity, lower, upper, taken) -> Optional[int]:
    """Nearest parity number to ``candidate`` strictly between lower and upper that is not taken."""
    step = 2 if parity in (ODD, EVEN) else 1
    lo_lim = -math.inf if lower is None else lower
    hi_lim = math.inf if upper is None else upper
    for k in range(0, 100000):
        for n in (candidate + k * step, candidate - k * step):
            if n <= 0 or not (lo_lim < n < hi_lim):
                continue
            if n not in taken:
                return n
        if candidate - k * step <= lo_lim and candidate + k * step >= hi_lim:
            break
    return None


def _next_suffix(base: int, neighbors: Sequence[Neighbor]) -> str:
    used = {n.suffix.upper() for n in neighbors if n.number == base}
    for ch in string.ascii_uppercase:
        if ch not in used:
            return ch
    return "Z"


def resolve(candidate: int, parity: str, distance: float, neighbors: Sequence[Neighbor],
            s: NumberingSettings) -> NumberResult:
    """
    Fit ``candidate`` among the existing numbers on the same street and side.

    :param distance: position of the new address (same measure as the neighbours').
    """
    p = normalize_parity(parity, BOTH)
    if p == ZERO:
        p = BOTH
    taken = {n.number for n in neighbors if not n.suffix}
    lower = max((n.number for n in neighbors if n.distance < distance), default=None)
    upper = min((n.number for n in neighbors if n.distance > distance), default=None)
    note = ""
    if s.order_check:
        if lower is not None and candidate <= lower:
            candidate, note = conform_up(lower + 1, p), f"raised above {lower} (previous address)"
        if upper is not None and candidate >= upper:
            candidate, note = conform_down(upper - 1, p), f"kept below {upper} (next address)"
        b_lo, b_hi = lower, upper
    else:
        b_lo, b_hi = None, None
    in_order = (b_lo is None or candidate > b_lo) and (b_hi is None or candidate < b_hi) and candidate > 0
    if in_order and candidate not in taken:
        return NumberResult(candidate, "", parity=p, message=note)

    free = _free_between(candidate, p, b_lo, b_hi, taken)
    base = candidate if candidate in taken and in_order else (b_lo if b_lo is not None else candidate)
    suffix_opt = f"{base}{_next_suffix(base, neighbors)}"
    if s.collision == C_NEXT and free is not None:
        return NumberResult(free, "", parity=p, message=(f"{candidate} is taken – next free number used"
                                                         if in_order else note))
    if s.collision == C_ASK:
        options = ([str(free)] if free is not None else []) + [suffix_opt]
        return NumberResult(None, "", parity=p, ok=False, needs_choice=True, options=options,
                            message=f"{candidate} is not available here – choose a number")
    # suffix (also the fallback when no whole number fits between the neighbours)
    return NumberResult(base, suffix_opt[len(str(base)):], parity=p,
                        message=("no whole number fits between the neighbouring addresses – suffix used"
                                 if free is None else f"{candidate} is taken – suffix used"))


def calculate(s: NumberingSettings, parity: str, neighbors: Sequence[Neighbor], distance_m: Optional[float],
              frac: Optional[float] = None, seg_from: Optional[int] = None,
              seg_to: Optional[int] = None) -> NumberResult:
    """Full calculation: raw position → parity → order / collision."""
    try:
        raw = raw_position(s, distance_m, frac, seg_from, seg_to)
    except ValueError as exc:
        return NumberResult(None, ok=False, message=str(exc))
    cand = round_to_parity(raw, parity, s.rounding)
    res = resolve(cand, parity, distance_m if distance_m is not None else (frac or 0.0), neighbors, s)
    res.raw = raw
    return res
