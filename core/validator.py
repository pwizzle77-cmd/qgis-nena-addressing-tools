# -*- coding: utf-8 -*-
"""
Layer validation: reads the layer, runs the pure rules from ``rules.py``,
and returns a sorted list of issues. Also exports issues to CSV and to a
temporary review layer.
"""

import copy
import csv
from dataclasses import fields as dc_fields
from typing import Dict, Hashable, List, Optional

from qgis.core import QgsFeature, QgsFeatureRequest, QgsFields, QgsVectorLayer, QgsWkbTypes

from ..compat import make_field
from . import geometry_utils as gu
from . import rules as V
from .config import PluginConfig
from .layer_access import FieldAccessor, read_segments
from .schema import MANDATORY, RANGE_FIELDS
from .template import effective_fields
from .network import network_issues
from .topology import assign_nodes, build_chains, direction_conflicts


class Validator:
    def __init__(self, layer: QgsVectorLayer, cfg: PluginConfig):
        self.layer = layer
        self.cfg = cfg
        self.acc = FieldAccessor(layer, cfg)

    def run(self, selected_only: bool = False, progress=None, only=None) -> List[V.Issue]:
        """
        :param only: optional set of check names (e.g. {"check_overlaps"}) to run
                     instead of the configured ones.
        """
        vs = copy.copy(self.cfg.validation)
        if only is not None:
            for f in dc_fields(vs):
                if f.name.startswith("check_"):
                    setattr(vs, f.name, f.name in only)
        acc = self.acc
        issues: List[V.Issue] = []

        # -- layer-level: mapping --------------------------------------
        defs = effective_fields(self.cfg.template)
        if vs.check_mandatory:
            for fd in defs:
                if fd.req == MANDATORY and not acc.has(fd.name):
                    issues.append(V.Issue(None, V.ERROR, "FIELD_NOT_MAPPED",
                                          f"Mandatory field {fd.name} is not mapped / missing"))
        if acc.missing(RANGE_FIELDS):
            # nothing range-related can be checked
            return V.sort_issues(issues)

        segs = read_segments(self.layer, acc, None, progress=_scaled(progress, 0, 50))
        scope = set(self.layer.selectedFeatureIds()) if selected_only else None

        # -- per feature attribute checks ---------------------------------
        nguids: Dict[Hashable, object] = {}
        mand_mapped = [fd.name for fd in defs if fd.req == MANDATORY and acc.has(fd.name)]
        req = QgsFeatureRequest()
        if scope is not None:
            req.setFilterFids(list(scope))
        total = len(scope) if scope is not None else (self.layer.featureCount() or 1)
        for n, f in enumerate(self.layer.getFeatures(req)):
            fid = f.id()
            if progress and n % 200 == 0:
                progress(50 + int(30 * n / max(total, 1)))
            if vs.check_mandatory:
                for name in mand_mapped:
                    if name in RANGE_FIELDS or name in ("Parity_L", "Parity_R", "RCL_NGUID"):
                        continue  # reported by the dedicated checks with better messages
                    v = acc.get(f, name)
                    if v is None or (isinstance(v, str) and not v.strip()):
                        issues.append(V.Issue(fid, V.ERROR, "MANDATORY_EMPTY", f"{name} is empty"))
                if acc.has("St_Name") and not acc.get(f, "St_Name"):
                    issues.append(V.Issue(fid, V.INFO, "CONDITIONAL_EMPTY",
                                          "St_Name is empty (required when the road has a name)"))
            if vs.check_parity or vs.check_ranges:
                for side in ("L", "R"):
                    fr, to, par = acc.side_range(f, side)
                    for i in V.check_side(V.RangeRecord(fid, side, fr, to, par, None), vs.allow_decreasing):
                        is_par = i.code.startswith("PARITY")
                        if (is_par and vs.check_parity) or (not is_par and vs.check_ranges):
                            issues.append(i)
            if vs.check_domains:
                for name in ("St_PreDir", "St_PosDir"):
                    if acc.has(name):
                        issues.extend(V.check_directional(fid, name, acc.get(f, name)))
                if acc.has("OneWay"):
                    issues.extend(V.check_oneway(fid, acc.get(f, "OneWay")))
                if acc.has("SpeedLimit"):
                    issues.extend(V.check_speed(fid, acc.get(f, "SpeedLimit")))
            if vs.check_nguid and acc.has("RCL_NGUID"):
                v = acc.get(f, "RCL_NGUID")
                issues.extend(V.check_nguid_format(fid, v))
            if vs.check_geometry:
                s = segs.get(fid)
                if s is None or s.extra.get("empty"):
                    issues.append(V.Issue(fid, V.ERROR, "GEOMETRY_EMPTY", "Feature has no geometry"))
                else:
                    if s.length <= 0.0:
                        issues.append(V.Issue(fid, V.ERROR, "GEOMETRY_ZERO_LENGTH", "Zero-length line"))
                    if s.extra.get("disjoint"):
                        issues.append(V.Issue(fid, V.WARNING, "GEOMETRY_MULTIPART",
                                              f"Multipart line with {s.extra.get('parts')} disjoint parts; "
                                              "NENA centerlines should be single-part"))
                    if not f.geometry().isGeosValid():
                        issues.append(V.Issue(fid, V.INFO, "GEOMETRY_INVALID", "Geometry is not GEOS-valid"))

        if vs.check_geometry:
            snap = gu.metres_to_layer_units(self.cfg.ranging.snap_tolerance_m, self.layer.crs())
            near = gu.metres_to_layer_units(max(self.cfg.ranging.snap_tolerance_m * 10.0, 5.0), self.layer.crs())
            issues.extend(_in_scope(network_issues(self.layer, acc, segs, snap, near), scope))

        if vs.check_nguid and acc.has("RCL_NGUID"):
            nguid_idx = acc.index["RCL_NGUID"]
            for f in self.layer.getFeatures(QgsFeatureRequest().setSubsetOfAttributes([nguid_idx])):
                nguids[f.id()] = acc.get(f, "RCL_NGUID")
            issues.extend(_in_scope(V.find_duplicates(nguids, "NGUID_DUPLICATE", "RCL_NGUID"), scope))

        # -- relational checks (whole layer, reported for scope) ----------
        if vs.check_overlaps or vs.check_gaps or vs.check_direction:
            tol = gu.metres_to_layer_units(self.cfg.ranging.snap_tolerance_m, self.layer.crs())
            seglist = [s for s in segs.values() if not s.extra.get("empty")]
            assign_nodes(seglist, tol)
            if progress:
                progress(85)
            if vs.check_overlaps:
                recs = []
                comm = vs.overlap_community_field.strip()
                feats_comm = self._community_values(comm) if comm else {}
                for s in seglist:
                    for side in ("L", "R"):
                        fr, to, par = s.extra[side]
                        group = s.key
                        if group is not None and comm:
                            group = f"{group} | {feats_comm.get((s.fid, side)) or ''}"
                        recs.append(V.RangeRecord(s.fid, side, fr, to, par, group))
                issues.extend(_in_scope(V.find_overlaps(recs), scope))
            if vs.check_gaps:
                for chain in build_chains(seglist):
                    if len(chain) < 2:
                        continue
                    items = [{side: V.RangeRecord(s.fid, side, *s.extra[side], s.key) for side in ("L", "R")}
                             for s in chain]
                    issues.extend(_in_scope(V.check_chain_gaps(items, vs.gap_tolerance), scope))
            if vs.check_direction:
                for a, b, kind in direction_conflicts(seglist):
                    text = ("meet end-to-end" if kind == "head_to_head" else "start at the same point")
                    for fid, other in ((a, b), (b, a)):
                        if scope is None or fid in scope:
                            issues.append(V.Issue(fid, V.WARNING, "DIRECTION_CONFLICT",
                                                  f"Same-named segments {a} and {b} {text}; one is probably "
                                                  "digitized against the address direction", "", other))
        if vs.check_names:
            issues.extend(_in_scope(self._name_issues(), scope))
        if progress:
            progress(100)
        return V.sort_issues(issues)

    def _name_issues(self) -> List[V.Issue]:
        """Centerline names against the master street names table, when one is configured."""
        try:
            from .project_config import layer_for, load_project_config
            from .streets.service import StreetService
            pcfg = load_project_config()
            if layer_for(pcfg.streets, "master") is None:
                return []
            svc = StreetService(pcfg, centerline_layer=self.layer, centerline_cfg=self.cfg, address_layer=None)
            svc.linked = [ll for ll in svc.linked if ll.role == "centerline"]
            return svc.name_issues("centerline")
        except Exception as exc:  # noqa: BLE001 - name checks must not break validation
            return [V.Issue(None, V.WARNING, "NAME_CHECK_FAILED", f"Street-name check skipped: {exc}")]

    def _community_values(self, base: str) -> Dict[tuple, Optional[str]]:
        out = {}
        names = {"L": f"{base}_L", "R": f"{base}_R"}
        if not (self.acc.has(names["L"]) or self.acc.has(names["R"])):
            return out
        for f in self.layer.getFeatures():
            for side, n in names.items():
                v = self.acc.get(f, n)
                out[(f.id(), side)] = str(v).strip().upper() if v is not None else None
        return out


def _in_scope(issues, scope):
    if scope is None:
        return issues
    return [i for i in issues if i.fid in scope or i.related_fid in scope]


def _scaled(progress, lo, hi):
    if progress is None:
        return None
    return lambda pct: progress(lo + int((hi - lo) * pct / 100))


# ---------------------------------------------------------------------------
HEADER = ["severity", "fid", "code", "side", "message", "related_fid"]


def export_csv(path: str, issues: List[V.Issue], layer_name: str = "") -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["layer"] + HEADER)
        for i in issues:
            w.writerow([layer_name] + i.as_row())


def issues_layer(source: QgsVectorLayer, issues: List[V.Issue]) -> QgsVectorLayer:
    """Memory layer with one feature per issue (copy of the offending geometry)."""
    wkb = QgsWkbTypes.displayString(source.wkbType())
    uri = f"{wkb}?crs={source.crs().authid() or source.crs().toWkt()}"
    out = QgsVectorLayer(uri, f"{source.name()} - NENA issues", "memory")
    fields = QgsFields()
    for name, kind in (("severity", "string"), ("src_fid", "string"), ("code", "string"),
                       ("side", "string"), ("message", "string"), ("related", "string")):
        fields.append(make_field(name, kind, 254 if name == "message" else 40))
    out.dataProvider().addAttributes(fields.toList())
    out.updateFields()
    fids = {i.fid for i in issues if i.fid is not None}
    geoms = {f.id(): f.geometry() for f in source.getFeatures(QgsFeatureRequest().setFilterFids(list(fids))
                                                               .setNoAttributes())} if fids else {}
    feats = []
    for i in issues:
        if i.fid is None or i.fid not in geoms:
            continue
        f = QgsFeature(out.fields())
        f.setGeometry(geoms[i.fid])
        f.setAttributes([i.severity, str(i.fid), i.code, i.side, i.message[:254],
                         "" if i.related_fid is None else str(i.related_fid)])
        feats.append(f)
    out.dataProvider().addFeatures(feats)
    out.updateExtents()
    return out

