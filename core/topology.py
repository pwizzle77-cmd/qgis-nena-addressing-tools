# -*- coding: utf-8 -*-
"""
Pure-Python connectivity helpers (no QGIS dependency).

Segments are described by their start/end coordinates (in layer CRS units),
a street key (normalised full street name) and their start/end bearings.
From these we build:

* node ids with a snapping tolerance,
* ordered *chains* of same-named segments that follow digitizing direction,
* digitizing-direction conflicts (two same-named segments meeting head to
  head or tail to tail),
* the ordering of split pieces.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Hashable, List, Optional, Sequence, Tuple

Point = Tuple[float, float]


@dataclass
class SegmentInfo:
    fid: Hashable
    start: Point
    end: Point
    length: float = 0.0               # metres
    key: Optional[str] = None         # street key; None = unnamed (never chained)
    start_bearing: Optional[float] = None   # radians, direction of travel leaving the start vertex
    end_bearing: Optional[float] = None     # radians, direction of travel arriving at the end vertex
    start_node: int = -1
    end_node: int = -1
    extra: dict = field(default_factory=dict)


class NodeIndex:
    """Grid-hashed point snapper: points within ``tol`` share a node id."""

    def __init__(self, tol: float):
        self.tol = max(float(tol), 1e-12)
        self._cells: Dict[Tuple[int, int], List[Tuple[int, Point]]] = defaultdict(list)
        self._count = 0

    def _cell(self, p: Point) -> Tuple[int, int]:
        return (int(math.floor(p[0] / self.tol)), int(math.floor(p[1] / self.tol)))

    def node_for(self, p: Point) -> int:
        cx, cy = self._cell(p)
        best, best_d = None, None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for nid, q in self._cells.get((cx + dx, cy + dy), ()):
                    d = math.hypot(p[0] - q[0], p[1] - q[1])
                    if d <= self.tol and (best_d is None or d < best_d):
                        best, best_d = nid, d
        if best is not None:
            return best
        nid = self._count
        self._count += 1
        self._cells[(cx, cy)].append((nid, p))
        return nid


def assign_nodes(segments: Sequence[SegmentInfo], tol: float) -> None:
    """Fill ``start_node`` / ``end_node`` in place."""
    idx = NodeIndex(tol)
    for s in segments:
        s.start_node = idx.node_for(s.start)
        s.end_node = idx.node_for(s.end)


def _turn(a: Optional[float], b: Optional[float]) -> float:
    if a is None or b is None:
        return math.pi
    d = (b - a + math.pi) % (2 * math.pi) - math.pi
    return abs(d)


def build_chains(segments: Sequence[SegmentInfo]) -> List[List[SegmentInfo]]:
    """
    Group segments into ordered chains that follow digitizing direction.

    Segments must already have node ids (see :func:`assign_nodes`). Each
    unnamed segment (``key is None``) is its own chain. At a junction with
    several same-named continuations the straightest one is followed; the
    others start their own chains.
    """
    groups: Dict[str, List[SegmentInfo]] = defaultdict(list)
    chains: List[List[SegmentInfo]] = []
    for s in segments:
        if s.key is None:
            chains.append([s])
        else:
            groups[s.key].append(s)

    for key in sorted(groups):
        segs = sorted(groups[key], key=lambda s: str(s.fid))
        by_start: Dict[int, List[SegmentInfo]] = defaultdict(list)
        ends = set()
        for s in segs:
            by_start[s.start_node].append(s)
            ends.add(s.end_node)
        visited = set()

        def walk(first: SegmentInfo) -> List[SegmentInfo]:
            chain = [first]
            visited.add(id(first))
            cur = first
            while True:
                cands = [c for c in by_start.get(cur.end_node, ()) if id(c) not in visited]
                if not cands:
                    break
                nxt = min(cands, key=lambda c: (_turn(cur.end_bearing, c.start_bearing), str(c.fid)))
                chain.append(nxt)
                visited.add(id(nxt))
                cur = nxt
            return chain

        heads = [s for s in segs if s.start_node not in ends]
        for h in heads:
            if id(h) not in visited:
                chains.append(walk(h))
        # remaining: branches whose start is fed by another segment, or loops
        for s in segs:
            if id(s) not in visited:
                chains.append(walk(s))
    return chains


def direction_conflicts(segments: Sequence[SegmentInfo]) -> List[Tuple[Hashable, Hashable, str]]:
    """
    Find same-named segments that meet head-to-head or tail-to-tail at a node
    where no same-named segment continues in the expected direction.

    :returns: list of (fid_a, fid_b, kind) with kind 'head_to_head' or 'tail_to_tail'.
    """
    ins: Dict[Tuple[str, int], List[SegmentInfo]] = defaultdict(list)
    outs: Dict[Tuple[str, int], List[SegmentInfo]] = defaultdict(list)
    for s in segments:
        if s.key is None or s.start_node == s.end_node:
            continue
        outs[(s.key, s.start_node)].append(s)
        ins[(s.key, s.end_node)].append(s)
    result = []
    for k, arriving in ins.items():
        if len(arriving) >= 2 and not outs.get(k):
            a, b = sorted(arriving, key=lambda s: str(s.fid))[:2]
            result.append((a.fid, b.fid, "head_to_head"))
    for k, leaving in outs.items():
        if len(leaving) >= 2 and not ins.get(k):
            a, b = sorted(leaving, key=lambda s: str(s.fid))[:2]
            result.append((a.fid, b.fid, "tail_to_tail"))
    return result


def upstream_segments(first: SegmentInfo, all_segments: Sequence[SegmentInfo]) -> List[SegmentInfo]:
    """Same-named segments (from the full layer) whose END node is ``first``'s START node."""
    if first.key is None:
        return []
    return [s for s in all_segments
            if s.key == first.key and s.end_node == first.start_node and s.fid != first.fid]


def _close(a: Point, b: Point, tol: float) -> bool:
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= tol


def _touches(a: SegmentInfo, b: SegmentInfo, tol: float) -> bool:
    return any(_close(p, q, tol) for p in (a.start, a.end) for q in (b.start, b.end))


def touching_components(pieces: Sequence[SegmentInfo], tol: float) -> List[List[SegmentInfo]]:
    """Groups of pieces that meet at an endpoint, including a reversed piece."""
    remaining = list(pieces)
    groups: List[List[SegmentInfo]] = []
    while remaining:
        group = [remaining.pop()]
        grew = True
        while grew:
            grew = False
            for piece in list(remaining):
                if any(_touches(piece, other, tol) for other in group):
                    group.append(piece)
                    remaining.remove(piece)
                    grew = True
        groups.append(group)
    return groups


def _fid_key(seg: SegmentInfo):
    """Saved features before temporary edit ids, then the smaller id."""
    try:
        number = int(seg.fid)
    except (TypeError, ValueError):
        return (2, str(seg.fid))
    if number < 0:
        return (1, number)
    return (0, number)


def _order_forward(items: List[SegmentInfo], tol: float) -> Optional[List[SegmentInfo]]:
    """End-to-start order, or None when a piece is reversed or the path branches."""
    firsts = [p for p in items if not any(_close(p.start, q.end, tol) for q in items if q is not p)]
    if len(firsts) != 1:
        return None
    ordered = [firsts[0]]
    remaining = [p for p in items if p is not firsts[0]]
    while remaining:
        cur = ordered[-1]
        nxt = [p for p in remaining if _close(cur.end, p.start, tol)]
        if len(nxt) != 1:
            return None
        ordered.append(nxt[0])
        remaining.remove(nxt[0])
    return ordered


def _oriented_path(items: List[SegmentInfo], tol: float) -> Optional[List[SegmentInfo]]:
    """
    One path, flipping a reversed piece so its shared end meets the previous piece.

    When several directions work, keep the one that flips fewer pieces and starts
    at the original feature (a saved id beats a temporary negative id).
    """
    candidates = []
    for head in items:
        ordered = [(head, False)]
        used = {id(head)}
        flips = 0
        while len(ordered) < len(items):
            cur, flipped = ordered[-1]
            cur_end = cur.start if flipped else cur.end
            found = None
            for piece in items:
                if id(piece) in used:
                    continue
                if _close(cur_end, piece.start, tol):
                    found = (piece, False)
                    break
                if _close(cur_end, piece.end, tol):
                    found = (piece, True)
                    break
            if found is None:
                break
            ordered.append(found)
            used.add(id(found[0]))
            flips += int(found[1])
        if len(ordered) == len(items):
            candidates.append((flips, _fid_key(head), [piece for piece, _flipped in ordered]))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def ordered_path(pieces: Sequence[SegmentInfo], tol: float) -> Optional[List[SegmentInfo]]:
    """Pieces along one road, or None when they are not a single path."""
    items = list(pieces)
    if len(items) < 2:
        return items
    forward = _order_forward(items, tol)
    if forward is not None:
        return forward
    return _oriented_path(items, tol)


def order_pieces(pieces: Sequence[SegmentInfo], tol: float) -> List[SegmentInfo]:
    """
    Order the pieces of a split line along the original digitizing direction.

    A piece digitized backwards is turned around so it still continues the
    path. Falls back to input order when the pieces do not form a single path.
    """
    path = ordered_path(pieces, tol)
    return list(pieces) if path is None else path
