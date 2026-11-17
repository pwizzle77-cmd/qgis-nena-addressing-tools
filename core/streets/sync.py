# -*- coding: utf-8 -*-
"""
Live street-name sync.

* Typing a new name part directly into the master table cascades to every
  centerline and address point that uses that StreetID (option
  ``cascade_direct_edits``) and refreshes FullName.
* Picking a StreetID on a centerline or address point (form drop-down,
  attribute table, field calculator) copies that street's name parts onto
  the feature (option ``copy_on_assign``).

Changes are collected while the user edits and applied right after, as
their own undoable "NENA: …" commands. Edits made by the plugin itself are
ignored (see ``service.suppressed``).
"""

from collections import defaultdict
from typing import Callable, Dict, Set

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QObject, QTimer

from ..project_config import layer_for, load_project_config
from .names import NAME_PARTS
from .service import StreetService, is_suppressed
from ..qt_safety import disconnect_all, safe_disconnect


class StreetWatcherManager(QObject):

    def __init__(self, notify: Callable[[str, str], None]):
        super().__init__()
        self.notify = notify
        self._conns = []            # (signal, slot)
        self._pending_master: Set = set()
        self._pending: Dict[str, Set] = defaultdict(set)
        self._scheduled = False
        self._layers = {}
        prj = QgsProject.instance()
        prj.readProject.connect(self.refresh)
        prj.layersAdded.connect(self.refresh)
        self.refresh()

    def unload(self):
        prj = QgsProject.instance()
        for sig in (prj.readProject, prj.layersAdded):
            safe_disconnect(prj, sig, self.refresh)
        self._detach()

    def _detach(self):
        disconnect_all(self._conns)
        self._conns = []
        self._layers = {}

    def _connect(self, owner, sig, slot):
        sig.connect(slot)
        self._conns.append((owner, sig, slot))

    def refresh(self, *args):
        """(Re)attach to the layers named in the project configuration."""
        self._detach()
        cfg = load_project_config()
        s = cfg.streets
        master = layer_for(s, "master")
        if master is None:
            return
        self._layers["master"] = master
        if s.cascade_direct_edits:
            self._connect(master, master.attributeValueChanged, self._on_master_changed)
        if s.copy_on_assign:
            for role in ("centerline", "address"):
                lyr = layer_for(s, role)
                if lyr is None:
                    continue
                self._layers[role] = lyr
                self._connect(lyr, lyr.attributeValueChanged,
                              lambda fid, idx, value, r=role: self._on_linked_changed(r, fid, idx))

    # -- signal handlers -----------------------------------------------------
    def _on_master_changed(self, fid, idx, value=None):
        if is_suppressed():
            return
        master = self._layers.get("master")
        if master is None or master.fields().at(idx).name() not in NAME_PARTS:
            return
        self._pending_master.add(fid)
        self._schedule()

    def _on_linked_changed(self, role, fid, idx):
        if is_suppressed():
            return
        lyr = self._layers.get(role)
        sid_field = load_project_config().streets.street_id_field
        if lyr is None or lyr.fields().at(idx).name().lower() != sid_field.lower():
            return
        self._pending[role].add(fid)
        self._schedule()

    def _schedule(self):
        if not self._scheduled:
            self._scheduled = True
            QTimer.singleShot(0, self._flush)

    def _flush(self):
        self._scheduled = False
        master_fids, self._pending_master = set(self._pending_master), set()
        pending, self._pending = dict(self._pending), defaultdict(set)
        try:
            svc = StreetService(load_project_config())
            if master_fids:
                by_fid = {r.fid: r for r in svc.repo.records()}
                sids = [by_fid[f].street_id for f in master_fids if f in by_fid and by_fid[f].street_id]
                svc.repo.refresh_full_names(sids)
                res = svc.sync(sids)
                for sid in sids:
                    rec = svc.repo.by_id().get(sid)
                    if rec and svc.conflicts(rec.parts, rec.scope, exclude_ids=[sid]):
                        self.notify("Warning", f"'{rec.full_name}' now breaks the street-name uniqueness rule.")
                n = sum(res.values())
                if n:
                    self.notify("Info", f"Street name change applied to {n} linked feature(s).")
            for role, fids in pending.items():
                if fids:
                    svc.copy_parts(role, fids)
        except Exception as exc:  # noqa: BLE001 - never break the user's editing
            self.notify("Warning", f"Street-name sync failed: {exc}")
