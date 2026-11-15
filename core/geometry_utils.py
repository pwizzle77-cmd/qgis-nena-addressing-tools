# -*- coding: utf-8 -*-
"""
Geometry helpers (PyQGIS).

All lengths are returned in metres, measured on the ellipsoid, so results
are correct for any layer CRS (geographic or projected). Curves are
segmentized and contiguous multipart lines are merged before endpoints and
bearings are taken.
"""

import math
from typing import List, Optional, Tuple

from qgis.core import (Qgis, QgsCoordinateReferenceSystem, QgsDistanceArea, QgsGeometry,
                       QgsProject, QgsUnitTypes, QgsWkbTypes)

Point = Tuple[float, float]
_DEG_PER_M = 1.0 / 111320.0   # rough metres->degrees, only used for tolerances/search radii


class LineInfo:
    __slots__ = ("start", "end", "start_bearing", "end_bearing", "parts", "disjoint", "empty")

    def __init__(self):
        self.start: Optional[Point] = None
        self.end: Optional[Point] = None
        self.start_bearing: Optional[float] = None
        self.end_bearing: Optional[float] = None
        self.parts = 0
        self.disjoint = False
        self.empty = True


def make_distance_area(crs: QgsCoordinateReferenceSystem) -> QgsDistanceArea:
    """Ellipsoidal distance calculator for a layer CRS."""
    da = QgsDistanceArea()
    project = QgsProject.instance()
    da.setSourceCrs(crs, project.transformContext())
    ellipsoid = project.ellipsoid()
    if not ellipsoid or ellipsoid.upper() == "NONE":
        ellipsoid = crs.ellipsoidAcronym() or "EPSG:7030"
    if not da.setEllipsoid(ellipsoid):
        da.setEllipsoid("EPSG:7030")
    return da


def length_m(da: QgsDistanceArea, geom: QgsGeometry) -> float:
    if geom is None or geom.isNull() or geom.isEmpty():
        return 0.0
    raw = da.measureLength(geom)
    return float(da.convertLengthMeasurement(raw, Qgis.DistanceUnit.Meters))


def metres_to_layer_units(metres: float, crs: QgsCoordinateReferenceSystem) -> float:
    """Convert a distance in metres to layer map units (approximate for geographic CRSs)."""
    if crs.isGeographic():
        return metres * _DEG_PER_M
    factor = QgsUnitTypes.fromUnitToUnitFactor(Qgis.DistanceUnit.Meters, crs.mapUnits())
    return metres * factor


def _bearing(a, b) -> Optional[float]:
    dx, dy = b.x() - a.x(), b.y() - a.y()
    if dx == 0 and dy == 0:
        return None
    return math.atan2(dy, dx)


def linear_geometry(geom: QgsGeometry) -> QgsGeometry:
    """Segmentize curves and merge contiguous multipart lines (returns a copy)."""
    g = QgsGeometry(geom)
    if g.isNull() or g.isEmpty():
        return g
    if QgsWkbTypes.isCurvedType(g.wkbType()):
        g = QgsGeometry(g.constGet().segmentize())
    if g.isMultipart():
        merged = g.mergeLines()
        if merged is not None and not merged.isNull() and not merged.isEmpty():
            g = merged
    return g


class SplitError(ValueError):
    """The line cannot be split at the requested point."""


def single_curve_geometry(geom: QgsGeometry) -> QgsGeometry:
    g = QgsGeometry(geom)
    if g.isNull() or g.isEmpty():
        raise SplitError("feature has no geometry")
    if g.isMultipart():
        coll = g.constGet()
        if coll.numGeometries() == 1:
            g = QgsGeometry(coll.geometryN(0).clone())
        else:
            merged = g.mergeLines()
            if merged.isNull() or merged.isEmpty() or merged.isMultipart():
                raise SplitError("multipart line with disjoint parts – fix the geometry first")
            g = merged
    return g


def split_line_at_distances(geom: QgsGeometry, distances, multi_output: bool, end_tolerance: float = 0.0):
    """
    Split a line at several distances along it (layer units, as returned by
    ``lineLocatePoint``). Distances closer than ``end_tolerance`` to an end
    or to each other are ignored. Curves, Z and M are preserved.

    :returns: list of piece geometries in digitized order (1 element = no split)
    """
    g = single_curve_geometry(geom)
    curve = g.constGet()
    total = curve.length()
    tol = max(end_tolerance, total * 1e-6)
    cuts = []
    for d in sorted(float(x) for x in distances):
        if d <= tol or d >= total - tol:
            continue
        if cuts and d - cuts[-1] <= tol:
            continue
        cuts.append(d)
    bounds = [0.0] + cuts + [total]
    pieces = []
    for d0, d1 in zip(bounds, bounds[1:]):
        p = QgsGeometry(curve.curveSubstring(d0, d1))
        if multi_output:
            p.convertToMultiType()
        pieces.append(p)
    return pieces


def split_line_at(geom: QgsGeometry, point, multi_output: bool, end_tolerance: float = 0.0):
    """
    Split a (single-part or contiguous multipart) line at the point on the line
    nearest to ``point`` (layer CRS). Curves, Z and M are preserved
    (``QgsCurve.curveSubstring``).

    :param multi_output: convert both pieces to multi-type (layer is MultiLineString)
    :param end_tolerance: refuse splits closer than this (layer units) to either end
    :returns: (first_piece, second_piece, distance_along_in_layer_units)
    """
    g = single_curve_geometry(geom)
    d = g.lineLocatePoint(QgsGeometry.fromPointXY(point))
    pieces = split_line_at_distances(g, [d], multi_output, end_tolerance)
    if len(pieces) != 2:
        raise SplitError("split point is too close to an end of the line")
    return pieces[0], pieces[1], d


def polylines(geom: QgsGeometry) -> List[list]:
    g = linear_geometry(geom)
    if g.isNull() or g.isEmpty():
        return []
    if g.isMultipart():
        return [p for p in g.asMultiPolyline() if p]
    pl = g.asPolyline()
    return [pl] if pl else []


def line_info(geom: QgsGeometry) -> LineInfo:
    info = LineInfo()
    parts = polylines(geom) if geom is not None else []
    if not parts:
        return info
    info.empty = False
    info.parts = len(parts)
    info.disjoint = len(parts) > 1
    first, last = parts[0], parts[-1]
    info.start = (first[0].x(), first[0].y())
    info.end = (last[-1].x(), last[-1].y())
    # bearings from the first/last non-degenerate vertex pairs
    for i in range(len(first) - 1):
        b = _bearing(first[i], first[i + 1])
        if b is not None:
            info.start_bearing = b
            break
    for i in range(len(last) - 1, 0, -1):
        b = _bearing(last[i - 1], last[i])
        if b is not None:
            info.end_bearing = b
            break
    return info
