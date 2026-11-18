# -*- coding: utf-8 -*-
"""
Address-point anchors for re-ranging.

Given a set of ordered line pieces (the parts of one original centerline
after a split, or a chain of segments), find the address points that belong
to them – nearest line wins, among *all* centerlines around, so a point of a
cross street is never borrowed – and return, per side, the point's distance
along the pieces (metres from the start of the first piece) and its house
number. These anchors drive :func:`ranging.redistribute`.
"""

from typing import Dict, Hashable, List, Optional, Sequence, Tuple

from qgis.core import (QgsCoordinateTransform, QgsFeatureRequest, QgsGeometry, QgsPointXY, QgsProject,
                       QgsRectangle, QgsVectorLayer)

from . import geometry_utils as gu
from .config import PluginConfig
from .safe_call import succeeds

Anchors = Dict[str, List[Tuple[float, int]]]


def point_layer_ready(cfg: PluginConfig) -> bool:
    rs = cfg.ranging
    if not (rs.point_layer_id and rs.point_number_field):
        return False
    pl = QgsProject.instance().mapLayer(rs.point_layer_id)
    return isinstance(pl, QgsVectorLayer) and pl.fields().lookupField(rs.point_number_field) >= 0


def point_anchors(line_layer: QgsVectorLayer, cfg: PluginConfig,
                  pieces: Sequence[Tuple[Hashable, QgsGeometry]],
                  street_key: Optional[str] = None,
                  exclude_fids: Sequence[Hashable] = ()) -> Anchors:
    """
    :param pieces: ordered (id, geometry in line-layer CRS). Ids may be
                   placeholders for pieces not yet in the layer.
    :param street_key: normalised street name of the pieces; when the point
                   layer has a street-name field, only matching points count.
    :param exclude_fids: layer features represented by ``pieces`` (their
                   current geometry in the layer must be ignored).
    :returns: {'L': [(distance_m, number), ...], 'R': [...]}
    """
    from .ranger import parse_house_number, _norm_street  # local import avoids a cycle

    out: Anchors = {"L": [], "R": []}
    if not pieces or not point_layer_ready(cfg):
        return out
    rs = cfg.ranging
    pl = QgsProject.instance().mapLayer(rs.point_layer_id)
    num_idx = pl.fields().lookupField(rs.point_number_field)
    street_idx = pl.fields().lookupField(rs.point_street_field) if rs.point_street_field else -1
    crs = line_layer.crs()
    radius = gu.metres_to_layer_units(rs.point_search_distance_m, crs)
    da = gu.make_distance_area(crs)

    # pieces: linear geometry, planar + ellipsoidal length, offset along the chain
    piece_info = []
    offset = 0.0
    bbox: Optional[QgsRectangle] = None
    for pid, g in pieces:
        lg = gu.linear_geometry(g)
        if lg.isNull() or lg.isEmpty():
            continue
        planar = lg.length() or 1e-12
        ell = gu.length_m(da, g)
        piece_info.append((pid, lg, planar, ell, offset))
        offset += ell
        bb = lg.boundingBox()
        if bbox is None:
            bbox = QgsRectangle(bb)
        else:
            bbox.combineExtentWith(bb)
    if bbox is None:
        return out
    bbox.grow(radius * 1.5)

    # competing centerlines nearby
    excluded = set(exclude_fids)
    others = []
    for f in line_layer.getFeatures(QgsFeatureRequest().setFilterRect(bbox).setNoAttributes()):
        if f.id() in excluded:
            continue
        g = gu.linear_geometry(f.geometry())
        if not g.isNull() and not g.isEmpty():
            others.append(g)

    to_pts = QgsCoordinateTransform(crs, pl.crs(), QgsProject.instance())
    to_lines = QgsCoordinateTransform(pl.crs(), crs, QgsProject.instance())
    try:
        pt_rect = to_pts.transformBoundingBox(bbox)
    except Exception:  # noqa: BLE001
        pt_rect = bbox
    for pf in pl.getFeatures(QgsFeatureRequest().setFilterRect(pt_rect)):
        num = parse_house_number(pf.attribute(num_idx))
        if num is None or num <= 0 or not pf.hasGeometry():
            continue
        if street_idx >= 0 and street_key:
            st = _norm_street(pf.attribute(street_idx))
            if st and st != street_key:
                continue
        pg = QgsGeometry(pf.geometry())
        if not succeeds(pg.transform, to_lines):
            continue
        pt = QgsPointXY(pg.centroid().asPoint() if pg.isMultipart() else pg.asPoint())
        best = None
        for pid, lg, planar, ell, off in piece_info:
            sqr, _c, _a, left_of = lg.closestSegmentWithContext(pt)
            if best is None or sqr < best[0]:
                best = (sqr, (pid, lg, planar, ell, off), left_of)
        if best is None or best[0] ** 0.5 > radius:
            continue
        # a nearer competing centerline owns the point
        if any(o.closestSegmentWithContext(pt)[0] < best[0] for o in others):
            continue
        _pid, lg, planar, ell, off = best[1]
        side = "L" if best[2] < 0 else "R" if best[2] > 0 else None
        if side is None:
            continue
        frac = lg.lineLocatePoint(QgsGeometry.fromPointXY(pt)) / planar
        out[side].append((off + max(0.0, min(1.0, frac)) * ell, num))
    return out
