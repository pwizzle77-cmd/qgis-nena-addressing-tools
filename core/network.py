# -*- coding: utf-8 -*-
"""Centerline connectivity: near-miss ends, crossings that were never split, and pseudo-nodes.

A real cul-de-sac is quiet. Only an end that stops within a few metres of another
road is reported. A crossing is skipped when either line is marked Overpass.
"""

import math
from collections import defaultdict

from qgis.core import Qgis, QgsPointXY

from .rules import WARNING, Issue
from .topology import assign_nodes

# Side values that should agree before two pieces are called the same road.
# Ranges are left out: the two pieces often carry different numbers on purpose.
_SIDE_ATTRS = ("PostCode_L", "PostCode_R", "MSAGComm_L", "MSAGComm_R", "ESN_L", "ESN_R",
               "RoadClass", "OneWay")
_YES = ("Y", "YES", "TRUE", "1")


def _clean(value) -> str:
    return "" if value is None else str(value).strip().upper()


def _box(geom):
    bb = geom.boundingBox()
    return bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum()


def _hits(geom):
    """Intersection points, or None when the intersection is a line (the roads overlap)."""
    if geom is None or geom.isEmpty():
        return []
    kind = getattr(geom, "kind", None)
    if kind in ("point", "multipoint"):
        return [(p.x(), p.y()) for p in geom.pts]
    if kind == "line":
        return None
    try:
        if geom.type() != Qgis.GeometryType.Point:
            return None
    except Exception:  # noqa: BLE001 - an unknown geometry is treated as an overlap
        return None
    if geom.isMultipart():
        return [(p.x(), p.y()) for p in geom.asMultiPoint()]
    p = geom.asPoint()
    return [(p.x(), p.y())]


def _near_end(pt, seg, tol) -> bool:
    return (math.hypot(pt[0] - seg.start[0], pt[1] - seg.start[1]) <= tol
            or math.hypot(pt[0] - seg.end[0], pt[1] - seg.end[1]) <= tol)


def _dist_to(geom, x, y):
    sqr, _closest, _after, _left = geom.closestSegmentWithContext(QgsPointXY(x, y))
    if sqr is None or sqr < 0:
        return None
    return sqr ** 0.5


def network_issues(layer, acc, segs, snap: float, near: float):
    """DANGLE_NEAR, UNSPLIT_CROSSING, and PSEUDO_NODE for one centerline layer."""
    seglist = [s for s in segs.values() if not s.extra.get("empty")]
    if len(seglist) < 2:
        return []
    assign_nodes(seglist, max(snap, 1e-9))
    geoms = {}
    attrs = {}
    overpass = set()
    for f in layer.getFeatures():
        geoms[f.id()] = f.geometry()
        attrs[f.id()] = tuple(_clean(acc.get(f, name)) if acc.has(name) else "" for name in _SIDE_ATTRS)
        if acc.has("Overpass") and _clean(acc.get(f, "Overpass")) in _YES:
            overpass.add(f.id())
    by_id = {s.fid: s for s in seglist}
    issues = []
    issues.extend(_dangles(seglist, geoms, snap, near))
    issues.extend(_crossings(seglist, geoms, snap, overpass))
    issues.extend(_pseudos(seglist, attrs, by_id))
    return issues


def _dangles(seglist, geoms, snap, near):
    degree = defaultdict(int)
    for s in seglist:
        degree[s.start_node] += 1
        if s.end_node != s.start_node:
            degree[s.end_node] += 1
    out = []
    seen = set()
    for s in seglist:
        for node, pt in ((s.start_node, s.start), (s.end_node, s.end)):
            if degree[node] != 1 or (s.fid, node) in seen:
                continue
            seen.add((s.fid, node))
            best = None
            other = None
            for cand in seglist:
                if cand.fid == s.fid or cand.fid not in geoms:
                    continue
                xmin, ymin, xmax, ymax = _box(geoms[cand.fid])
                if pt[0] < xmin - near or pt[0] > xmax + near or pt[1] < ymin - near or pt[1] > ymax + near:
                    continue
                dist = _dist_to(geoms[cand.fid], pt[0], pt[1])
                if dist is None:
                    continue
                if best is None or dist < best:
                    best, other = dist, cand.fid
            if best is not None and snap < best <= near:
                out.append(Issue(s.fid, WARNING, "DANGLE_NEAR",
                                 f"This end stops {best:.1f} map units short of another road",
                                 related_fid=other))
    return out


def _crossings(seglist, geoms, snap, overpass):
    # shortcut: every pair whose boxes meet, not a spatial index. Upgrade if Check is slow on a full county.
    out = []
    reported = set()
    boxes = {}
    for s in seglist:
        g = geoms.get(s.fid)
        if g is not None and not g.isEmpty():
            boxes[s.fid] = _box(g)
    ordered = [s for s in seglist if s.fid in boxes]
    for i, a in enumerate(ordered):
        ax0, ay0, ax1, ay1 = boxes[a.fid]
        for b in ordered[i + 1:]:
            if a.fid in overpass or b.fid in overpass:
                continue
            bx0, by0, bx1, by1 = boxes[b.fid]
            if ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0:
                continue
            pair = tuple(sorted((a.fid, b.fid), key=str))
            if pair in reported:
                continue
            hits = _hits(geoms[a.fid].intersection(geoms[b.fid]))
            if hits is None:
                reported.add(pair)
                out.append(Issue(a.fid, WARNING, "UNSPLIT_CROSSING",
                                 f"Roads {a.fid} and {b.fid} overlap. Split them, or mark Overpass Y "
                                 "where they cross at a different grade", related_fid=b.fid))
                continue
            for pt in hits:
                if _near_end(pt, a, snap) and _near_end(pt, b, snap):
                    continue
                reported.add(pair)
                out.append(Issue(a.fid, WARNING, "UNSPLIT_CROSSING",
                                 f"Roads {a.fid} and {b.fid} cross without a shared node. "
                                 "Split them, or mark Overpass Y where they cross at a different grade",
                                 related_fid=b.fid))
                break
    return out


def _pseudos(seglist, attrs, _by_id):
    touch = defaultdict(list)
    for s in seglist:
        touch[s.start_node].append(s)
        if s.end_node != s.start_node:
            touch[s.end_node].append(s)
    out = []
    for group in touch.values():
        if len(group) != 2:
            continue
        a, b = group
        if not a.key or a.key != b.key:
            continue
        if attrs.get(a.fid) != attrs.get(b.fid):
            continue
        out.append(Issue(a.fid, WARNING, "PSEUDO_NODE",
                         f"Segments {a.fid} and {b.fid} are the same road and meet at a node with nothing else. "
                         "Merge them unless the split is there for a reason", related_fid=b.fid))
    return out
