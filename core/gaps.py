# -*- coding: utf-8 -*-
"""Close address-range holes between consecutive segments of one street."""

from typing import Hashable, Optional, Sequence

from qgis.core import QgsVectorLayer

from . import geometry_utils as gu
from . import ranging as R
from .config import PluginConfig
from .layer_access import FieldAccessor, apply_changes, read_segments
from .schema import SIDE_FIELDS
from .topology import assign_nodes, build_chains

FILL_GAPS_COMMAND = "NENA: fill range gaps"


def fill_range_gaps(layer: QgsVectorLayer, cfg: PluginConfig,
                    fids: Optional[Sequence[Hashable]] = None) -> int:
    """
    Extend a segment so its numbers meet the next segment of the same street.

    The upstream high end is raised when that feature is in scope. When only
    the downstream feature is in scope, its low end is lowered instead.
    Overlaps (the next segment starts at or below this one) are left alone.
    ``fids`` empty or None means the whole layer.

    :returns: number of features changed
    """
    acc = FieldAccessor(layer, cfg)
    scope = set(fids) if fids else None
    segs = read_segments(layer, acc)
    tol = gu.metres_to_layer_units(cfg.ranging.snap_tolerance_m, layer.crs())
    seglist = [s for s in segs.values() if not s.extra.get("empty")]
    assign_nodes(seglist, tol)
    changes = {}
    tolerance = cfg.validation.gap_tolerance
    for chain in build_chains(seglist):
        for left, right in zip(chain, chain[1:]):
            for side in ("L", "R"):
                _close_pair(changes, side, left, right, scope, tolerance)
    return apply_changes(layer, acc, changes, FILL_GAPS_COMMAND)


def _close_pair(changes, side, left, right, scope, tolerance) -> None:
    fr, to, parity = left.extra[side]
    bfr, bto, bparity = right.extra[side]
    if None in (fr, to, bfr, bto):
        return
    if R.is_zero_range(fr, to) or R.is_zero_range(bfr, bto):
        return
    pa = R.normalize_parity(parity, R.BOTH)
    pb = R.normalize_parity(bparity, R.BOTH)
    if pa == R.ZERO or pb == R.ZERO:
        return
    step = R.parity_step(pb if pb == pa else R.BOTH) or 1
    a_hi = max(int(fr), int(to))
    b_lo = min(int(bfr), int(bto))
    if b_lo <= a_hi or (b_lo - a_hi - step) <= tolerance:
        return
    upstream = scope is None or left.fid in scope
    downstream = scope is None or right.fid in scope
    if upstream:
        new_hi = R.conform_down(b_lo - step, pa)
        if a_hi < new_hi < b_lo:
            _put(changes, left.fid, _high_field(side, fr, to), new_hi)
        return
    if downstream:
        new_lo = R.conform_up(a_hi + step, pb)
        if a_hi < new_lo < b_lo:
            _put(changes, right.fid, _low_field(side, bfr, bto), new_lo)


def _high_field(side, fr, to) -> str:
    names = SIDE_FIELDS[side]
    return names["to"] if int(to) >= int(fr) else names["from"]


def _low_field(side, fr, to) -> str:
    names = SIDE_FIELDS[side]
    return names["from"] if int(fr) <= int(to) else names["to"]


def _put(changes, fid, field_name, value) -> None:
    changes.setdefault(fid, {})[field_name] = int(value)
