# -*- coding: utf-8 -*-
"""
Auto-ranging engine: reads a layer, computes left/right FROM/TO ranges with
the configured method, and writes them through the edit buffer in one
undoable command.
"""

import copy
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Hashable, List, Optional

from qgis.core import (QgsCoordinateTransform, QgsFeature, QgsFeatureRequest, QgsPointXY,
                       QgsProject, QgsSpatialIndex, QgsVectorLayer)

from ..compat import py_value
from . import geometry_utils as gu
from . import ranging as R
from .config import (PluginConfig, RangingSettings, M_HUNDRED, M_INTERVAL, M_PROPORTIONAL,
                     M_POINTS, SCOPE_CHAIN, START_EXISTING, START_UPSTREAM, PROP_EXISTING)
from .anchors import point_anchors, point_layer_ready
from .layer_access import (ConfigError, FieldAccessor, apply_changes, read_segments, side_values)
from .schema import RANGE_FIELDS
from .split_ops import rerange_pieces
from .topology import (SegmentInfo, assign_nodes, build_chains, ordered_path, touching_components)

_NUM_RE = re.compile(r"^\s*(\d+)")


@dataclass
class RangingReport:
    targets: int = 0
    chains: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    messages: List[str] = field(default_factory=list)

    def summary(self) -> str:
        s = (f"{self.updated} feature(s) updated, {self.unchanged} unchanged, {self.skipped} skipped "
             f"({self.targets} processed in {self.chains} chain(s)).")
        if self.messages:
            s += " " + " ".join(self.messages[:5])
        return s


def parse_house_number(value) -> Optional[int]:
    v = py_value(value)
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    m = _NUM_RE.match(str(v))
    return int(m.group(1)) if m else None


def _norm_street(v) -> Optional[str]:
    v = py_value(v)
    if v is None or not str(v).strip():
        return None
    return " ".join(str(v).upper().split())


# ---------------------------------------------------------------------------
def collect_point_observations(line_layer: QgsVectorLayer, segs: Dict[Hashable, SegmentInfo],
                               rs: RangingSettings, target_fids, progress=None) -> Dict[Hashable, Dict[str, list]]:
    """
    Assign each address point to its nearest line (optionally with the same
    street name) and to a side. Returns {fid: {'L': [nums], 'R': [nums]}}
    for target features only.
    """
    pl = QgsProject.instance().mapLayer(rs.point_layer_id) if rs.point_layer_id else None
    if not isinstance(pl, QgsVectorLayer):
        raise ConfigError("Choose an address point layer for the 'address points' method.")
    num_idx = pl.fields().lookupField(rs.point_number_field or "")
    if num_idx < 0:
        raise ConfigError("Choose the house-number field of the address point layer.")
    street_idx = pl.fields().lookupField(rs.point_street_field) if rs.point_street_field else -1

    xform = QgsCoordinateTransform(pl.crs(), line_layer.crs(), QgsProject.instance())
    radius = gu.metres_to_layer_units(rs.point_search_distance_m, line_layer.crs())

    index = QgsSpatialIndex()
    geoms = {}
    for f in line_layer.getFeatures(QgsFeatureRequest().setNoAttributes()):
        g = gu.linear_geometry(f.geometry())
        if g.isNull() or g.isEmpty():
            continue
        geoms[f.id()] = g
        tmp = QgsFeature(f.id())
        tmp.setGeometry(g)
        index.addFeature(tmp)

    targets = set(target_fids)
    obs: Dict[Hashable, Dict[str, list]] = {fid: {"L": [], "R": []} for fid in targets}
    total = pl.featureCount() or 1
    req = QgsFeatureRequest()
    for n, pf in enumerate(pl.getFeatures(req)):
        if progress and n % 500 == 0:
            progress(int(100 * n / total))
        num = parse_house_number(pf.attribute(num_idx))
        if num is None or num <= 0 or not pf.hasGeometry():
            continue
        pg = pf.geometry()
        try:
            pg.transform(xform)
        except Exception:  # noqa: BLE001 - transform failure: skip the point
            continue
        pt = pg.centroid().asPoint() if pg.isMultipart() else pg.asPoint()
        pt = QgsPointXY(pt)
        street = _norm_street(pf.attribute(street_idx)) if street_idx >= 0 else None
        best = None
        for cand in index.nearestNeighbor(pt, 6, radius):
            g = geoms.get(cand)
            if g is None:
                continue
            seg = segs.get(cand)
            if street and seg is not None and seg.key and seg.key != street:
                continue
            sqr, _closest, _after, left_of = g.closestSegmentWithContext(pt)
            if best is None or sqr < best[0]:
                best = (sqr, cand, left_of)
        if best is None or best[1] not in targets:
            continue
        side = "L" if best[2] < 0 else "R" if best[2] > 0 else None
        if side:
            obs[best[1]][side].append(num)
    return obs


# ---------------------------------------------------------------------------
class RangeEngine:
    """Compute and apply address ranges for a layer."""

    def __init__(self, layer: QgsVectorLayer, cfg: PluginConfig):
        self.layer = layer
        self.cfg = cfg
        self.acc = FieldAccessor(layer, cfg)

    # -- public ----------------------------------------------------------
    def run(self, selected_only: bool, overrides: Optional[dict] = None, progress=None,
            command: str = "NENA: auto-range") -> RangingReport:
        rs = copy.deepcopy(self.cfg.ranging)
        for k, v in (overrides or {}).items():
            setattr(rs, k, v)
        self._rs = rs
        self.acc.require(RANGE_FIELDS)

        if selected_only:
            targets = list(self.layer.selectedFeatureIds())
            if not targets:
                raise ConfigError("No features are selected.")
        else:
            targets = None

        report = RangingReport()
        segs = read_segments(self.layer, self.acc, None, progress=_scaled(progress, 0, 30))
        target_ids = list(segs.keys()) if targets is None else [t for t in targets if t in segs]
        report.targets = len(target_ids)

        tol = gu.metres_to_layer_units(rs.snap_tolerance_m, self.layer.crs())
        assign_nodes(list(segs.values()), tol)

        tsegs = [segs[f] for f in target_ids if not segs[f].extra.get("empty")]
        report.skipped += len(target_ids) - len(tsegs)
        chains = build_chains(tsegs) if rs.scope == SCOPE_CHAIN else [[s] for s in tsegs]
        report.chains = len(chains)

        obs = None
        if rs.method == M_POINTS:
            obs = collect_point_observations(self.layer, segs, rs, [s.fid for s in tsegs],
                                             progress=_scaled(progress, 30, 70))

        by_end = defaultdict(list)
        for s in segs.values():
            if s.key is not None:
                by_end[(s.key, s.end_node)].append(s)

        new_ranges: Dict[Hashable, Dict[str, R.SideRange]] = {}
        # A split copies the original From/To onto every piece. Hundred-block
        # would hand the next piece a new block, and an unchained or reversed
        # piece would keep the full range. Divide that shared range by length.
        if rs.method != M_POINTS:
            dup_paths = _duplicate_paths(tsegs, tol)
            geoms = _geometries(self.layer, [s.fid for path in dup_paths for s in path])
            consumed = set()
            used_paths = 0
            for path in dup_paths:
                pieces = [(s.fid, geoms[s.fid]) for s in path if s.fid in geoms]
                if len(pieces) != len(path):
                    continue
                original = {side: path[0].extra[side] for side in ("L", "R")}
                per_piece, _used = rerange_pieces(
                    self.layer, self.cfg, original, pieces, path[0].key,
                    exclude_fids=[s.fid for s in path])
                for seg, sides in zip(path, per_piece):
                    new_ranges[seg.fid] = sides
                    consumed.add(seg.fid)
                used_paths += 1
            if consumed:
                tsegs = [s for s in tsegs if s.fid not in consumed]
                chains = build_chains(tsegs) if rs.scope == SCOPE_CHAIN else [[s] for s in tsegs]
                report.chains = len(chains) + used_paths
        for ci, chain in enumerate(chains):
            if progress and ci % 50 == 0:
                progress(70 + int(25 * ci / max(len(chains), 1)))
            result = self._range_chain(chain, rs, obs, by_end, new_ranges)
            for seg, sides in zip(chain, result):
                if sides:
                    new_ranges[seg.fid] = sides

        changes = {}
        stamp = datetime.now().replace(microsecond=0)
        for fid, sides in new_ranges.items():
            seg = segs[fid]
            if not rs.overwrite_existing and _has_ranges(seg):
                report.skipped += 1
                continue
            vals = {}
            differs = False
            for side, rng in sides.items():
                old = seg.extra[side]
                if (old[0], old[1]) != (rng[0], rng[1]) or (
                        rs.write_parity and R.normalize_parity(old[2]) != rng[2]):
                    differs = True
                vals.update(side_values(side, rng, rs.write_parity and self.acc.has(
                    "Parity_L" if side == "L" else "Parity_R")))
            if not differs:
                report.unchanged += 1
                continue
            if rs.stamp_date_update and self.acc.has("DateUpdate"):
                vals["DateUpdate"] = stamp
            changes[fid] = vals
        if obs is not None:
            no_pts = sum(1 for s in tsegs if s.fid not in new_ranges)
            if no_pts:
                report.messages.append(f"{no_pts} feature(s) had no address points nearby and were left unchanged.")
                report.skipped += no_pts
        report.updated = apply_changes(self.layer, self.acc, changes, command)
        if progress:
            progress(100)
        return report

    # -- internals -------------------------------------------------------
    def _side_parities(self, chain: List[SegmentInfo], rs: RangingSettings) -> Dict[str, List[str]]:
        out = {}
        for side, default in (("L", rs.left_parity), ("R", rs.right_parity)):
            pars = []
            for s in chain:
                existing = R.normalize_parity(s.extra[side][2], None)
                if rs.respect_existing_parity and existing is not None:
                    pars.append(existing)
                else:
                    pars.append(R.normalize_parity(default, R.BOTH))
            out[side] = pars
        return out

    def _range_chain(self, chain, rs, obs, by_end, new_ranges) -> List[Optional[Dict[str, R.SideRange]]]:
        pars = self._side_parities(chain, rs)
        n = len(chain)
        lengths = [s.length for s in chain]

        if rs.method == M_POINTS:
            per_side = {}
            for side in ("L", "R"):
                default = rs.left_parity if side == "L" else rs.right_parity
                o = [obs.get(s.fid, {}).get(side, []) for s in chain]
                res = R.ranges_from_observations(o, default, rs.block_size,
                                                 rs.point_expand_to_block, rs.point_infer_parity)
                # a side explicitly marked Z stays zero
                res = [(0, 0, R.ZERO) if pars[side][i] == R.ZERO and rs.respect_existing_parity else r
                       for i, r in enumerate(res)]
                per_side[side] = res
            out = []
            for i, s in enumerate(chain):
                l, r = per_side["L"][i], per_side["R"][i]
                if l is None and r is None:
                    out.append(None)
                    continue
                out.append({"L": l if l is not None else _existing_or_zero(s, "L"),
                            "R": r if r is not None else _existing_or_zero(s, "R")})
            return out

        if rs.method == M_HUNDRED:
            base = self._chain_base(chain, rs, by_end, new_ranges, block=True)
            bounds = R.hundred_block_boundaries(n, base, rs.block_size)
        elif rs.method == M_INTERVAL:
            base = self._chain_base(chain, rs, by_end, new_ranges, block=False)
            mpn = R.to_meters(rs.interval_distance, rs.interval_units)
            if mpn <= 0:
                raise ConfigError("Interval distance must be greater than zero.")
            bounds = R.interval_boundaries(lengths, base, mpn)
        elif rs.method == M_PROPORTIONAL:
            lo, hi = rs.proportional_from, rs.proportional_to
            if rs.proportional_source == PROP_EXISTING:
                ext = _chain_extent(chain)
                if ext:
                    lo, hi = ext
            if hi < lo:
                raise ConfigError(f"Proportional range {lo}-{hi} is invalid (TO must be >= FROM).")
            bounds = R.proportional_boundaries(lengths, lo, hi)
            if rs.proportional_anchor_points:
                anchored = self._anchored_bounds(chain, lengths, lo, hi, pars)
                if anchored:
                    res = {side: [(a[0], a[1], pars[side][i]) if a else None
                                  for i, a in enumerate(R.allocate(anchored[side], pars[side], clamp_max=hi))]
                           for side in ("L", "R")}
                    return [{"L": res["L"][i] or (0, 0, R.ZERO), "R": res["R"][i] or (0, 0, R.ZERO)}
                            for i in range(n)]
        else:
            raise ConfigError(f"Unknown ranging method '{rs.method}'.")

        res = R.range_chain(bounds, pars)
        return [{"L": res["L"][i], "R": res["R"][i]} for i in range(n)]

    def _anchored_bounds(self, chain, lengths, lo, hi, pars):
        """Per-side boundaries bent through address points, or None when there are no usable points."""
        cfg = copy.copy(self.cfg)
        cfg.ranging = self._rs
        if not point_layer_ready(cfg):
            return None
        geoms = {f.id(): f.geometry() for f in self.layer.getFeatures(
            QgsFeatureRequest().setFilterFids([s.fid for s in chain]).setNoAttributes())}
        pieces = [(s.fid, geoms[s.fid]) for s in chain if s.fid in geoms]
        if len(pieces) != len(chain):
            return None
        anchors = point_anchors(self.layer, cfg, pieces, chain[0].key, exclude_fids=[s.fid for s in chain])
        out, used = {}, 0
        for side in ("L", "R"):
            side_par = max(set(pars[side]), key=pars[side].count)
            anc = R.monotone_anchors(anchors[side], lo, hi, side_par)
            used += len(anc)
            out[side] = R.anchored_boundaries(lengths, lo, hi, anc)
        return out if used else None

    def _chain_base(self, chain, rs, by_end, new_ranges, block: bool) -> int:
        first = chain[0]
        if rs.start_mode == START_UPSTREAM and first.key is not None:
            highs = []
            for up in by_end.get((first.key, first.start_node), []):
                if up.fid == first.fid or any(up.fid == c.fid for c in chain):
                    continue
                sides = new_ranges.get(up.fid) or {"L": up.extra["L"], "R": up.extra["R"]}
                for rng in sides.values():
                    if rng and rng[0] is not None and rng[1] is not None and not R.is_zero_range(rng[0], rng[1]):
                        highs.append(max(rng[0], rng[1]))
            if highs:
                top = max(highs)
                if block:
                    bs = max(rs.block_size, 1)
                    return (top // bs + 1) * bs
                return top + 1
        if rs.start_mode in (START_UPSTREAM, START_EXISTING):
            lows = [min(first.extra[s][0], first.extra[s][1]) for s in ("L", "R")
                    if first.extra[s][0] is not None and first.extra[s][1] is not None
                    and not R.is_zero_range(first.extra[s][0], first.extra[s][1])]
            if lows:
                return min(lows)
        return int(rs.base_address)


def _existing_or_zero(seg, side) -> R.SideRange:
    f, t, p = seg.extra[side]
    if f is None or t is None:
        return (0, 0, R.ZERO)
    return (f, t, R.normalize_parity(p, R.BOTH if (f or t) else R.ZERO))


def _has_ranges(seg) -> bool:
    for side in ("L", "R"):
        f, t, _ = seg.extra[side]
        if f is not None and t is not None and not R.is_zero_range(f, t):
            return True
    return False


def _chain_extent(chain):
    vals = []
    for s in chain:
        for side in ("L", "R"):
            f, t, _ = s.extra[side]
            if f is not None and t is not None and not R.is_zero_range(f, t):
                vals.extend([f, t])
    if not vals:
        return None
    return min(vals), max(vals)


def _scaled(progress, lo, hi):
    if progress is None:
        return None
    return lambda pct: progress(lo + int((hi - lo) * pct / 100))


def _span_key(seg):
    """Identical From/To/parity on every side, or None when the segment has no range."""
    parts = []
    real = False
    for side in ("L", "R"):
        frm, to, parity = seg.extra.get(side, (None, None, None))
        if frm is None or to is None or R.is_zero_range(frm, to):
            parts.append(None)
        else:
            real = True
            parts.append((int(frm), int(to), R.normalize_parity(parity, None)))
    return tuple(parts) if real else None


def _duplicate_paths(segments, tol) -> List[List[SegmentInfo]]:
    """
    Paths whose pieces all still carry one shared From/To (the split-duplicate signature).

    Pieces of different streets stay apart. A reversed piece is included when
    its endpoints still meet the path.
    """
    buckets = defaultdict(list)
    for seg in segments:
        span = _span_key(seg)
        if span is not None:
            buckets[(seg.key, span)].append(seg)
    paths = []
    for items in buckets.values():
        if len(items) < 2:
            continue
        for comp in touching_components(items, tol):
            if len(comp) < 2:
                continue
            path = ordered_path(comp, tol)
            if path is not None:
                paths.append(path)
    return paths


def _geometries(layer, fids):
    if not fids:
        return {}
    out = {}
    request = QgsFeatureRequest().setFilterFids(list(fids)).setNoAttributes()
    for feat in layer.getFeatures(request):
        out[feat.id()] = feat.geometry()
    return out
