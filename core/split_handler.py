# -*- coding: utf-8 -*-
"""
Automatic re-ranging after split / merge / geometry edits.

How it works
------------
QGIS map tools wrap their edits in ``beginEditCommand`` / ``endEditCommand``.
A :class:`LayerWatcher` listens to the layer's edit-command signals and
records which features had their geometry changed, were added or deleted
inside the command. When the command ends it classifies the edit:

* **split**  – geometry changed on existing feature(s) + new features added
* **merge**  – features deleted + exactly one feature changed (or added)
* **reshape**– geometry changed only (vertex tool, reshape, etc.)
* **new road** – an added feature that is not a piece of a split (digitizing,
  with or without topological editing): optionally split the existing
  centerlines it ends on / crosses and re-range them (see intersections.py)

and then (deferred with a zero-timer, outside the tool's own command)
applies a follow-up edit command such as "NENA: re-range after split".
The user can therefore undo the re-range separately from the split.

Original range values are taken, in order of preference, from a cache of
the selected features (taken on selection change), from the feature itself
(which keeps / duplicates its values when the field split policy is
"Duplicate value" – the plugin sets that policy on the range fields for
you) or, as a last resort, from the data provider (committed values).
"""

import math
from collections import defaultdict
from datetime import datetime
from typing import Callable, Dict, Hashable, List, Optional

from qgis.core import QgsFeatureRequest, QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import QObject, QTimer

from ..compat import set_split_policy
from . import geometry_utils as gu
from . import ranging as R
from .config import PluginConfig, M_POINTS, GEOM_INTERVAL, GEOM_POINTS, load_from_layer
from .defaults import configured_defaults, fill_empty_defaults
from .layer_access import FieldAccessor, side_values
from .nguid import local_id, make_nguid
from .schema import RANGE_FIELDS, PARITY_FIELDS, SIDE_FIELDS, STREET_NAME_FIELDS
from .split_ops import rerange_pieces
from .topology import SegmentInfo, order_pieces
from .qt_safety import alive

_CACHE_LIMIT = 5000
_MAX_NEW_ROADS = 50   # larger batches (paste/import) are not auto-split


class LayerWatcher(QObject):
    """Watches one layer's edit commands and re-ranges after splits/merges."""

    def __init__(self, layer: QgsVectorLayer, notify: Callable[[str, str], None]):
        super().__init__()
        self.layer = layer
        self.notify = notify            # notify(level, text)
        self._in_cmd = False
        self._busy = False
        self._changed = set()
        self._added = set()
        self._deleted = set()
        self._cache: Dict[Hashable, dict] = {}
        layer.editCommandStarted.connect(self._on_cmd_started)
        layer.editCommandEnded.connect(self._on_cmd_ended)
        layer.editCommandDestroyed.connect(self._on_cmd_destroyed)
        layer.geometryChanged.connect(self._on_geom_changed)
        layer.featureAdded.connect(self._on_added)
        layer.featureDeleted.connect(self._on_deleted)
        layer.selectionChanged.connect(self._on_selection)
        self._on_selection()

    def detach(self):
        if not alive(self.layer):
            return                       # the layer is gone and took its connections with it
        for sig, slot in ((self.layer.editCommandStarted, self._on_cmd_started),
                          (self.layer.editCommandEnded, self._on_cmd_ended),
                          (self.layer.editCommandDestroyed, self._on_cmd_destroyed),
                          (self.layer.geometryChanged, self._on_geom_changed),
                          (self.layer.featureAdded, self._on_added),
                          (self.layer.featureDeleted, self._on_deleted),
                          (self.layer.selectionChanged, self._on_selection)):
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass

    # -- configuration ---------------------------------------------------
    def config(self) -> Optional[PluginConfig]:
        return load_from_layer(self.layer)

    def apply_split_policies(self) -> int:
        """Duplicate range/name values onto split pieces, leave the NGUID empty."""
        cfg = self.config()
        if cfg is None:
            return 0
        return apply_split_policies(self.layer, cfg)

    # -- signal handlers ---------------------------------------------------
    def _on_selection(self, *args):
        if self._busy:
            return
        cfg = self.config()
        if cfg is None:
            return
        ids = self.layer.selectedFeatureIds()
        if not ids or len(ids) > _CACHE_LIMIT:
            self._cache = {}
            return
        acc = FieldAccessor(self.layer, cfg)
        cache = {}
        for f in self.layer.getFeatures(QgsFeatureRequest().setFilterFids(list(ids))):
            cache[f.id()] = _snapshot(acc, f)
        self._cache = cache

    def _on_cmd_started(self, text=""):
        if self._busy:
            return
        # the plugin's own commands ("NENA: …", e.g. the Split & Re-range tool) already
        # carry correct ranges – never post-process them
        if str(text or "").startswith("NENA:"):
            self._in_cmd = False
            return
        self._in_cmd = True
        self._changed, self._added, self._deleted = set(), set(), set()

    def _on_cmd_destroyed(self):
        self._in_cmd = False
        self._changed, self._added, self._deleted = set(), set(), set()

    def _on_geom_changed(self, fid, geom=None):
        if self._in_cmd and not self._busy:
            self._changed.add(fid)

    def _on_added(self, fid):
        if self._in_cmd and not self._busy:
            self._added.add(fid)

    def _on_deleted(self, fid):
        if self._in_cmd and not self._busy:
            self._deleted.add(fid)

    def _on_cmd_ended(self):
        if self._busy or not self._in_cmd:
            return
        self._in_cmd = False
        changed, added, deleted = set(self._changed), set(self._added), set(self._deleted)
        changed -= deleted
        if not (changed or added or deleted):
            return
        cache = dict(self._cache)
        QTimer.singleShot(0, lambda: self._process(changed, added, deleted, cache))

    # -- classification --------------------------------------------------
    def _process(self, changed, added, deleted, cache):
        if self.layer is None or not self.layer.isEditable():
            return
        cfg = self.config()
        if cfg is None:
            return
        acc = FieldAccessor(self.layer, cfg)
        if acc.missing(RANGE_FIELDS):
            self._fill_added(cfg, added, deleted)
            return
        try:
            if deleted and (len(changed) == 1 or (len(added) == 1 and not changed)):
                if cfg.edit.auto_merge:
                    target = next(iter(changed or added))
                    self._handle_merge(cfg, acc, target, deleted, cache)
                self._fill_added(cfg, added, deleted)
                return
            groups, new_roads = self._classify(cfg, acc, changed, added)
            if groups:
                if cfg.edit.auto_rerange_split:
                    self._handle_split(cfg, acc, groups, cache)
                elif cfg.edit.auto_nguid_new:
                    self._assign_nguids(cfg, acc, [a for m in groups.values() for a in m[1:]],
                                        "NENA: NGUID for new features")
            if new_roads:
                if cfg.edit.auto_nguid_new:
                    self._assign_nguids(cfg, acc, new_roads, "NENA: NGUID for new features")
                if cfg.edit.split_on_new_intersection:
                    if len(new_roads) > _MAX_NEW_ROADS:
                        self.notify("Info", f"{len(new_roads)} features were added at once (paste/import?) – "
                                            "intersections were not split automatically.")
                    else:
                        self._handle_new_roads(cfg, new_roads)
            if changed and not added and not deleted:
                if cfg.edit.geometry_edit_action in (GEOM_INTERVAL, GEOM_POINTS):
                    self._handle_reshape(cfg, acc, changed)
            self._fill_added(cfg, added, deleted)
        except Exception as exc:  # noqa: BLE001 - never break the user's edit session
            self.notify("Warning", f"Automatic re-ranging failed: {exc}")

    def _fill_added(self, cfg, added, deleted):
        """Write rarely-changing defaults into fields the new feature left empty."""
        live = [fid for fid in added if fid not in deleted]
        if not live or not configured_defaults(cfg):
            return
        self._busy = True
        try:
            fill_empty_defaults(self.layer, cfg, live)
        except Exception as exc:  # noqa: BLE001 - a default must not undo the edit the user just made
            self.notify("Warning", f"Default values were not filled: {exc}")
        finally:
            self._busy = False

    def _classify(self, cfg, acc, changed, added):
        """
        Separate split pieces from genuinely new roads.

        An added feature is a piece of a split when its name+range attributes
        equal those of a changed feature (QGIS duplicates them with the
        'Duplicate value' split policy), or – for layers without that policy –
        when one of its ends coincides with an end of the changed feature.
        Everything else that was added is a new road. Changed features that
        received no pieces (e.g. a vertex inserted by topological editing) are
        left alone.

        :returns: ({original_fid: [original_fid, piece, ...]}, [new road fids])
        """
        if not added:
            return {}, []
        tol = gu.metres_to_layer_units(max(cfg.ranging.snap_tolerance_m, 0.01), self.layer.crs())
        feats = {f.id(): f for f in self.layer.getFeatures(
            QgsFeatureRequest().setFilterFids(list(changed | added)))}
        infos = {fid: gu.line_info(f.geometry()) for fid, f in feats.items()}
        empty_sig = None
        sig_to_orig = defaultdict(list)
        for fid in changed:
            if fid in feats:
                sig = _signature(acc, feats[fid])
                if empty_sig is None:
                    empty_sig = tuple("None" for _ in sig)
                if sig != empty_sig:
                    sig_to_orig[sig].append(fid)

        def touches_end(a, c):
            ia, ic = infos.get(a), infos.get(c)
            if ia is None or ic is None or ia.empty or ic.empty:
                return False
            return any(math.dist(p, q) <= tol for p in (ia.start, ia.end) for q in (ic.start, ic.end))

        groups: Dict[Hashable, List[Hashable]] = {}
        new_roads = []
        for a in sorted(added, key=str):
            if a not in feats:
                continue
            cands = sig_to_orig.get(_signature(acc, feats[a]), [])
            if len(cands) > 1:
                cands = [c for c in cands if touches_end(a, c)] or cands[:1]
            if not cands:
                cands = [c for c in changed if c in feats and touches_end(a, c)][:1]
            if cands:
                groups.setdefault(cands[0], [cands[0]]).append(a)
            else:
                new_roads.append(a)
        # pieces can chain: in a 3-way split the middle/last piece may only touch another piece
        changed_any = True
        while groups and new_roads and changed_any:
            changed_any = False
            for a in list(new_roads):
                for orig, members in groups.items():
                    if any(touches_end(a, m) for m in members[1:]) and \
                            _signature(acc, feats[a]) == _signature(acc, feats[members[1]]):
                        members.append(a)
                        new_roads.remove(a)
                        changed_any = True
                        break
        return groups, new_roads

    def _handle_new_roads(self, cfg, new_roads):
        from .intersections import split_at_new_roads
        self._busy = True
        try:
            rep = split_at_new_roads(self.layer, cfg, new_roads)
        finally:
            self._busy = False
        if rep["split_existing"] or rep["split_new"]:
            msg = (f"New intersection: split {rep['split_existing']} existing centerline(s)"
                   + (f" and {rep['split_new']} new road(s)" if rep["split_new"] else "")
                   + f", {rep['new_pieces']} new section(s) re-ranged.")
            if rep["details"]:
                msg += " " + "; ".join(rep["details"][:3])
            self.notify("Info", msg + " Undo once to keep the roads unsplit.")

    # -- split -----------------------------------------------------------
    def _handle_split(self, cfg: PluginConfig, acc: FieldAccessor, groups, cache):
        da = gu.make_distance_area(self.layer.crs())
        tol = gu.metres_to_layer_units(max(cfg.ranging.snap_tolerance_m, 0.01), self.layer.crs())
        members_all = {m for ms in groups.values() for m in ms}
        feats = {f.id(): f for f in self.layer.getFeatures(QgsFeatureRequest().setFilterFids(list(members_all)))}

        changes = {}
        stamp = datetime.now().replace(microsecond=0)
        for orig, members in groups.items():
            if len(members) < 2:
                continue
            original = cache.get(orig) or _snapshot(acc, feats[orig])
            if all(original[s][0] is None for s in ("L", "R")):
                original = _provider_snapshot(self.layer, acc, orig) or original
            pieces = []
            for fid in members:
                g = feats[fid].geometry()
                info = gu.line_info(g)
                if info.empty:
                    continue
                pieces.append(SegmentInfo(fid=fid, start=info.start, end=info.end, length=gu.length_m(da, g)))
            ordered = order_pieces(pieces, tol)
            per_piece, _used = rerange_pieces(self.layer, cfg, original,
                                              [(p.fid, feats[p.fid].geometry()) for p in ordered],
                                              acc.street_key(feats[orig]),
                                              exclude_fids=[p.fid for p in ordered])
            for i, piece in enumerate(ordered):
                vals = {}
                for side in ("L", "R"):
                    has_par = acc.has(SIDE_FIELDS[side]["parity"])
                    vals.update(side_values(side, per_piece[i][side], has_par))
                if cfg.ranging.stamp_date_update and acc.has("DateUpdate"):
                    vals["DateUpdate"] = stamp
                if piece.fid != orig and cfg.edit.auto_nguid_new and acc.has("RCL_NGUID") and cfg.edit.agency_id:
                    current = acc.get(feats[piece.fid], "RCL_NGUID")
                    orig_nguid = acc.get(feats[orig], "RCL_NGUID")
                    if not current or current == orig_nguid:
                        vals["RCL_NGUID"] = make_nguid(local_id(cfg.edit.nguid_local_id, None),
                                                       cfg.edit.agency_id, cfg.edit.nguid_format)
                changes[piece.fid] = vals
        if changes:
            n = self._write(acc, changes, "NENA: re-range after split")
            self.notify("Info", f"Re-ranged {n} split piece(s). Undo once to keep the original values.")

    # -- merge -----------------------------------------------------------
    def _handle_merge(self, cfg, acc, target, deleted, cache):
        feat = next(self.layer.getFeatures(QgsFeatureRequest().setFilterFid(target)), None)
        if feat is None:
            return
        snaps = []
        for fid in list(deleted) + [target]:
            s = cache.get(fid) or _provider_snapshot(self.layer, acc, fid)
            if s:
                snaps.append(s)
        if len(snaps) < 2:
            self.notify("Warning", "Merged feature not re-ranged: original ranges unknown "
                                   "(select the features before merging).")
            return
        vals = {}
        for side in ("L", "R"):
            combined = R.combine([s[side] for s in snaps if s[side][0] is not None])
            vals.update(side_values(side, combined, acc.has(SIDE_FIELDS[side]["parity"])))
        if cfg.ranging.stamp_date_update and acc.has("DateUpdate"):
            vals["DateUpdate"] = datetime.now().replace(microsecond=0)
        self._write(acc, {target: vals}, "NENA: combine ranges after merge")
        self.notify("Info", "Merged feature ranges combined.")

    # -- reshape ----------------------------------------------------------
    def _handle_reshape(self, cfg, acc, changed):
        if cfg.edit.geometry_edit_action == GEOM_POINTS:
            from .ranger import RangeEngine
            engine = RangeEngine(self.layer, cfg)
            sel = self.layer.selectedFeatureIds()
            self._busy = True
            try:
                self.layer.selectByIds(list(changed))
                engine.run(True, {"method": M_POINTS}, command="NENA: re-range after reshape")
            finally:
                self.layer.selectByIds(sel)
                self._busy = False
            return
        da = gu.make_distance_area(self.layer.crs())
        mpn = R.to_meters(cfg.ranging.interval_distance, cfg.ranging.interval_units)
        if mpn <= 0:
            return
        changes = {}
        for f in self.layer.getFeatures(QgsFeatureRequest().setFilterFids(list(changed))):
            length = gu.length_m(da, f.geometry())
            vals = {}
            for side in ("L", "R"):
                fr, to, p = acc.side_range(f, side)
                if fr is None or R.is_zero_range(fr, to) or (to is not None and fr > to):
                    continue
                vals[SIDE_FIELDS[side]["to"]] = R.extend_to(fr, R.normalize_parity(p, R.BOTH), length, mpn)
            if vals:
                if cfg.ranging.stamp_date_update and acc.has("DateUpdate"):
                    vals["DateUpdate"] = datetime.now().replace(microsecond=0)
                changes[f.id()] = vals
        if changes:
            self._write(acc, changes, "NENA: re-range after reshape")

    # -- NGUIDs for new features -----------------------------------------
    def _assign_nguids(self, cfg, acc, fids, command):
        if not (acc.has("RCL_NGUID") and cfg.edit.agency_id):
            return
        changes = {}
        for f in self.layer.getFeatures(QgsFeatureRequest().setFilterFids(list(fids))):
            if not acc.get(f, "RCL_NGUID"):
                changes[f.id()] = {"RCL_NGUID": make_nguid(local_id(cfg.edit.nguid_local_id, None),
                                                           cfg.edit.agency_id, cfg.edit.nguid_format)}
        if changes:
            self._write(acc, changes, command)

    # -- write helper ----------------------------------------------------
    def _write(self, acc, changes, command) -> int:
        from .layer_access import apply_changes
        self._busy = True
        try:
            return apply_changes(self.layer, acc, changes, command)
        finally:
            self._busy = False


# ---------------------------------------------------------------------------
def _snapshot(acc: FieldAccessor, feature) -> dict:
    return {"L": acc.side_range(feature, "L"), "R": acc.side_range(feature, "R")}


def _provider_snapshot(layer, acc, fid) -> Optional[dict]:
    """Committed values from the data provider (only for features that exist there)."""
    try:
        if int(fid) < 0:
            return None
    except (TypeError, ValueError):
        return None
    f = next(layer.dataProvider().getFeatures(QgsFeatureRequest().setFilterFid(fid)), None)
    if f is None or not f.isValid():
        return None
    return _snapshot(acc, f)


def _signature(acc, feature):
    vals = []
    for n in list(RANGE_FIELDS) + list(PARITY_FIELDS) + list(STREET_NAME_FIELDS):
        vals.append(str(acc.get(feature, n)))
    return tuple(vals)


def apply_split_policies(layer: QgsVectorLayer, cfg: PluginConfig) -> int:
    """
    Range, parity and street-name fields -> 'Duplicate value' (the plugin
    recalculates ranges itself); NGUID -> 'Unset field' (new pieces need a
    new id). Returns the number of fields configured.
    """
    acc = FieldAccessor(layer, cfg)
    n = 0
    for nena, idx in acc.index.items():
        if nena == "RCL_NGUID":
            ok = set_split_policy(layer, idx, "UnsetField")
        else:
            ok = set_split_policy(layer, idx, "Duplicate")
        if not ok:
            return 0
        n += 1
    return n


class WatcherManager:
    """Keeps one LayerWatcher per configured layer in the current project."""

    def __init__(self, notify):
        self.notify = notify
        self.watchers: Dict[str, LayerWatcher] = {}
        prj = QgsProject.instance()
        prj.layersAdded.connect(self._on_layers_added)
        prj.layerWillBeRemoved.connect(self._on_layer_removed)
        self.refresh_all()

    def unload(self):
        prj = QgsProject.instance()
        for sig, slot in ((prj.layersAdded, self._on_layers_added),
                          (prj.layerWillBeRemoved, self._on_layer_removed)):
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        for w in self.watchers.values():
            w.detach()
        self.watchers.clear()

    def refresh_all(self):
        for lyr in QgsProject.instance().mapLayers().values():
            self.refresh(lyr)

    def refresh(self, layer):
        """Attach or detach a watcher depending on the layer's stored config."""
        if not isinstance(layer, QgsVectorLayer):
            return
        cfg = load_from_layer(layer)
        wanted = cfg is not None and (cfg.edit.auto_rerange_split or cfg.edit.auto_merge or
                                      cfg.edit.geometry_edit_action != "none" or cfg.edit.auto_nguid_new or
                                      cfg.edit.split_on_new_intersection)
        lid = layer.id()
        if wanted and lid not in self.watchers:
            self.watchers[lid] = LayerWatcher(layer, self.notify)
        elif not wanted and lid in self.watchers:
            self.watchers.pop(lid).detach()
        if wanted and cfg.edit.manage_split_policies:
            apply_split_policies(layer, cfg)

    def _on_layers_added(self, layers):
        for lyr in layers:
            self.refresh(lyr)

    def _on_layer_removed(self, layer_id):
        w = self.watchers.pop(layer_id, None)
        if w is not None:
            w.detach()
