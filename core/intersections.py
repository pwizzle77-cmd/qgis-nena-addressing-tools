# -*- coding: utf-8 -*-
"""
Split existing centerlines where a newly digitized road creates an intersection.

When a new road is added and

* one of its **end points** lies on the interior of an existing centerline
  (a T-intersection), or
* it **crosses** an existing centerline (optional, off for layers with
  overpasses),

the existing centerline is split at that point. Its original left/right
ranges are redistributed over the pieces in digitized order, so the second
section continues the numbering of the first one (FROM = previous TO + 2 for
odd/even sides), the original FROM and TO stay at the ends, and – when an
address point layer is configured – each address point keeps its number on
the section it lies along. New sections get a new NGUID.

At crossings the new road itself is split as well, so both roads end at the
intersection node as NENA centerlines should.

Everything happens in one edit command ("NENA: split at new intersection"),
i.e. one undo step right after the "add feature" step.
"""

import math
from collections import defaultdict
from datetime import datetime
from typing import Dict, Hashable, List, Optional, Sequence

from qgis.core import Qgis, QgsFeatureRequest, QgsGeometry, QgsPointXY, QgsVectorLayer, QgsWkbTypes

from . import geometry_utils as gu
from .config import PluginConfig
from .layer_access import FieldAccessor, ensure_editable
from .split_ops import apply_piece_split

INTERSECTION_COMMAND = "NENA: split at new intersection"
EXISTING_COMMAND = "NENA: split at intersections"


def _points_of(geom: QgsGeometry) -> List[QgsPointXY]:
    pts = []
    if geom is None or geom.isNull() or geom.isEmpty():
        return pts
    for part in geom.asGeometryCollection():
        if part.type() != Qgis.GeometryType.Point:
            continue          # overlapping (collinear) stretches are not intersections
        if part.isMultipart():
            pts.extend(QgsPointXY(p) for p in part.asMultiPoint())
        else:
            pts.append(QgsPointXY(part.asPoint()))
    return pts


def find_splits(layer: QgsVectorLayer, new_fids: Sequence[Hashable], tol: float, include_crossings: bool):
    """
    :returns: (existing, new) – dicts fid -> list of distances along that feature
              (layer units) where it must be split.
    """
    new_set = set(new_fids)
    existing: Dict[Hashable, List[float]] = defaultdict(list)
    new: Dict[Hashable, List[float]] = defaultdict(list)
    for nf in list(layer.getFeatures(QgsFeatureRequest().setFilterFids(list(new_set)))):
        try:
            ng = gu.single_curve_geometry(nf.geometry())
        except gu.SplitError:
            continue
        info = gu.line_info(ng)
        if info.empty:
            continue
        ends = [QgsPointXY(*info.start), QgsPointXY(*info.end)]
        bbox = ng.boundingBox()
        bbox.grow(tol * 2)
        for f in layer.getFeatures(QgsFeatureRequest().setFilterRect(bbox).setNoAttributes()):
            if f.id() in new_set:
                continue
            try:
                g = gu.single_curve_geometry(f.geometry())
            except gu.SplitError:
                continue
            total = g.constGet().length()
            found = []
            # T-intersections: an end of the new road on the existing line
            for ep in ends:
                sqr, closest, _after, _left = g.closestSegmentWithContext(ep)
                if sqr >= 0 and math.sqrt(sqr) <= tol:
                    found.append(g.lineLocatePoint(QgsGeometry.fromPointXY(QgsPointXY(closest))))
            # X-intersections: the new road crosses the existing line
            if include_crossings:
                for p in _points_of(ng.intersection(g)):
                    pg = QgsGeometry.fromPointXY(p)
                    found.append(g.lineLocatePoint(pg))
                    dn = ng.lineLocatePoint(pg)
                    if tol < dn < ng.constGet().length() - tol:
                        new[nf.id()].append(dn)
            for d in found:
                if tol < d < total - tol and not any(abs(d - x) <= tol for x in existing[f.id()]):
                    existing[f.id()].append(d)
    return ({k: v for k, v in existing.items() if v}, {k: v for k, v in new.items() if v})


def split_at_new_roads(layer: QgsVectorLayer, cfg: PluginConfig, new_fids: Sequence[Hashable]) -> dict:
    """
    Split existing centerlines (and, at crossings, the new roads) and re-range.
    Runs in its own edit command. Returns a small report.
    """
    report = {"split_existing": 0, "new_pieces": 0, "split_new": 0, "anchors": 0, "details": [],
              "affected": []}
    if not new_fids:
        return report
    tol = gu.metres_to_layer_units(max(cfg.ranging.snap_tolerance_m, 0.05), layer.crs())
    existing, new = find_splits(layer, new_fids, tol, cfg.edit.split_at_crossings)
    if not existing and not new:
        return report
    acc = FieldAccessor(layer, cfg)
    multi = QgsWkbTypes.isMultiType(layer.wkbType())
    stamp = datetime.now().replace(microsecond=0)
    targets = list(existing.items()) + list(new.items())
    feats = {f.id(): f for f in layer.getFeatures(QgsFeatureRequest().setFilterFids([t[0] for t in targets]))}
    layer.beginEditCommand(INTERSECTION_COMMAND)
    try:
        for fid, dists in targets:
            feat = feats.get(fid)
            if feat is None:
                continue
            pieces = gu.split_line_at_distances(feat.geometry(), dists, multi, tol)
            if len(pieces) < 2:
                continue
            new_ids, ranges, used = apply_piece_split(layer, cfg, acc, feat, pieces, stamp)
            report["affected"] += [fid] + list(new_ids)
            report["new_pieces"] += len(new_ids)
            report["anchors"] += used
            if fid in existing:
                report["split_existing"] += 1
                if ranges:
                    name = acc.street_key(feat) or f"feature {fid}"
                    report["details"].append(
                        f"{name}: L " + " | ".join(f"{r['L'][0]}–{r['L'][1]}" for r in ranges)
                        + ", R " + " | ".join(f"{r['R'][0]}–{r['R'][1]}" for r in ranges))
            else:
                report["split_new"] += 1
    except Exception:
        layer.destroyEditCommand()
        raise
    layer.endEditCommand()
    return report


def find_interior_splits(layer: QgsVectorLayer, fids: Optional[Sequence[Hashable]], tol: float,
                         include_crossings: bool) -> Dict[Hashable, List[float]]:
    """
    Distances along each in-scope centerline where another road meets its interior.

    An end-to-end join is not a split. ``fids`` empty or None means the whole layer.
    When crossings are off, only a T (another road's end on this line) is returned.
    """
    scope = set(fids) if fids else None
    cuts: Dict[Hashable, List[float]] = defaultdict(list)
    for host in list(layer.getFeatures()):
        if scope is not None and host.id() not in scope:
            continue
        try:
            geom = gu.single_curve_geometry(host.geometry())
        except gu.SplitError:
            continue
        total = geom.constGet().length()
        if total <= tol * 2:
            continue
        bbox = geom.boundingBox()
        bbox.grow(tol * 2)
        for other in layer.getFeatures(QgsFeatureRequest().setFilterRect(bbox)):
            if other.id() == host.id():
                continue
            try:
                other_geom = gu.single_curve_geometry(other.geometry())
            except gu.SplitError:
                continue
            found = []
            info = gu.line_info(other_geom)
            if not info.empty:
                for end in (QgsPointXY(*info.start), QgsPointXY(*info.end)):
                    sqr, closest, _after, _left = geom.closestSegmentWithContext(end)
                    if sqr >= 0 and math.sqrt(sqr) <= tol:
                        found.append(geom.lineLocatePoint(QgsGeometry.fromPointXY(QgsPointXY(closest))))
            if include_crossings:
                for point in _points_of(geom.intersection(other_geom)):
                    found.append(geom.lineLocatePoint(QgsGeometry.fromPointXY(point)))
            for dist in found:
                if tol < dist < total - tol and not any(abs(dist - have) <= tol for have in cuts[host.id()]):
                    cuts[host.id()].append(dist)
    return {fid: dists for fid, dists in cuts.items() if dists}


def split_existing_intersections(layer: QgsVectorLayer, cfg: PluginConfig,
                                 fids: Optional[Sequence[Hashable]] = None) -> dict:
    """
    Split roads that already cross or tee in the middle, and re-range the pieces.

    One undo step. Returns split, new_pieces, affected (the pieces), and details.
    """
    report = {"split": 0, "new_pieces": 0, "affected": [], "details": []}
    tol = gu.metres_to_layer_units(max(cfg.ranging.snap_tolerance_m, 0.05), layer.crs())
    cuts = find_interior_splits(layer, fids, tol, cfg.edit.split_at_crossings)
    if not cuts:
        return report
    acc = FieldAccessor(layer, cfg)
    multi = QgsWkbTypes.isMultiType(layer.wkbType())
    stamp = datetime.now().replace(microsecond=0)
    feats = {f.id(): f for f in layer.getFeatures(QgsFeatureRequest().setFilterFids(list(cuts)))}
    ensure_editable(layer)
    layer.beginEditCommand(EXISTING_COMMAND)
    try:
        for fid, dists in cuts.items():
            feat = feats.get(fid)
            if feat is None:
                continue
            pieces = gu.split_line_at_distances(feat.geometry(), dists, multi, tol)
            if len(pieces) < 2:
                continue
            new_ids, ranges, _used = apply_piece_split(layer, cfg, acc, feat, pieces, stamp)
            report["split"] += 1
            report["new_pieces"] += len(new_ids)
            report["affected"].extend([fid] + list(new_ids))
            if ranges:
                name = acc.street_key(feat) or f"feature {fid}"
                report["details"].append(
                    f"{name}: L " + " | ".join(f"{r['L'][0]}–{r['L'][1]}" for r in ranges)
                    + ", R " + " | ".join(f"{r['R'][0]}–{r['R'][1]}" for r in ranges))
    except Exception:
        layer.destroyEditCommand()
        raise
    layer.endEditCommand()
    return report
