# -*- coding: utf-8 -*-
"""
Stop an access point from being deleted while an address or driveway still uses it.

Saving or rolling back the layer is not a delete: QGIS reports the
unsaved (temporary) feature ids as deleted while it swaps them for the
saved ones, so the guard stands aside between ``beforeCommitChanges`` /
``beforeRollBack`` and the matching ``after…`` signal.

QGIS commits a delete after ``featuresDeleted``. The edit buffer's deleted-id
set cannot be put back from that signal, so a blocked delete is undone on the
next turn of the event loop and a message explains why.
"""

from qgis.core import QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import QObject, QTimer

from ..compat import py_value
from .access_rules import GuardState, blocked_deletes, deletion_message
from .project_config import layer_for, load_project_config
from .qt_safety import disconnect_all, safe_disconnect


def _deleted_ids(layer):
    buffer = layer.editBuffer() if layer is not None else None
    if buffer is None:
        return set()
    try:
        return set(buffer.deletedFeatureIds())
    except Exception:  # noqa: BLE001
        return set()


def _stored_ids(layer, field_name):
    """AccessPointID values still on the layer, skipping rows already deleted in the buffer."""
    if layer is None:
        return []
    index = layer.fields().indexFromName(field_name)
    if index < 0:
        return []
    gone = _deleted_ids(layer)
    found = []
    for feature in layer.getFeatures():
        if feature.id() in gone:
            continue
        value = py_value(feature.attribute(index))
        text = "" if value is None else str(value).strip()
        if text:
            found.append(text)
    return found


class AccessGuard(QObject):
    """Undo a delete of an access point that address points or driveways still reference."""

    def __init__(self, notify):
        super().__init__()
        self.notify = notify
        self._conns = []
        self._cache = {}
        self._busy = False
        self._saving = False
        project = QgsProject.instance()
        project.readProject.connect(self.refresh)
        project.layersAdded.connect(self.refresh)
        removed = getattr(project, "layersRemoved", None)
        if removed is not None and hasattr(removed, "connect"):
            removed.connect(self.refresh)
        self.refresh()

    def unload(self):
        project = QgsProject.instance()
        for signal in (project.readProject, project.layersAdded, getattr(project, "layersRemoved", None)):
            if signal is None:
                continue
            safe_disconnect(project, signal, self.refresh)
        self._detach()

    def _detach(self):
        disconnect_all(self._conns)
        self._conns = []
        self._cache = {}

    def _connect(self, owner, signal, slot):
        if signal is None or not hasattr(signal, "connect"):
            return
        signal.connect(slot)
        self._conns.append((owner, signal, slot))

    def refresh(self, *_args):
        self._detach()
        layer = layer_for(load_project_config().addresses, "access")
        if not isinstance(layer, QgsVectorLayer):
            return
        self._cache = self._read_cache(layer)
        self._connect(layer, getattr(layer, "featuresDeleted", None), lambda fids, lyr=layer: self._deleted(lyr, fids))
        self._connect(layer, getattr(layer, "featureAdded", None), lambda fid, lyr=layer: self._remember(lyr, fid))
        changed = getattr(layer, "attributeValueChanged", None)
        self._connect(layer, changed, lambda fid, *_rest, lyr=layer: self._remember(lyr, fid))
        for name in ("beforeCommitChanges", "beforeRollBack"):
            self._connect(layer, getattr(layer, name, None), lambda *_a: self._set_saving(True))
        for name in ("afterCommitChanges", "afterRollBack", "commitError"):
            self._connect(layer, getattr(layer, name, None), lambda *_a, lyr=layer: self._saved(lyr))

    def _set_saving(self, value):
        self._saving = value

    def _saved(self, layer):
        """Saved or rolled back: ids changed, so read them again."""
        self._saving = False
        self._cache = self._read_cache(layer)

    def _read_cache(self, layer):
        index = layer.fields().indexFromName("AccessPointID")
        cache = {}
        if index < 0:
            return cache
        for feature in layer.getFeatures():
            value = py_value(feature.attribute(index))
            text = "" if value is None else str(value).strip()
            if text:
                cache[feature.id()] = text
        return cache

    def _remember(self, layer, fid):
        index = layer.fields().indexFromName("AccessPointID")
        if index < 0:
            return
        feature = layer.getFeature(fid)
        if feature is None or not feature.isValid():
            return
        value = py_value(feature.attribute(index))
        text = "" if value is None else str(value).strip()
        if text:
            self._cache[fid] = text
        else:
            self._cache.pop(fid, None)

    def _deleted(self, layer, fids):
        if self._busy or self._saving or GuardState.suppress:
            for fid in fids or []:
                self._cache.pop(fid, None)
            return
        ids = []
        for fid in fids or []:
            ids.append(self._cache.pop(fid, ""))
        pcfg = load_project_config()
        blocked = blocked_deletes(
            ids,
            _stored_ids(layer_for(pcfg.streets, "address"), "AccessPointID"),
            _stored_ids(layer_for(pcfg.addresses, "driveway"), "AccessPointID"))
        message = deletion_message(blocked)
        if not message:
            return
        QTimer.singleShot(0, lambda lyr=layer, text=message: self._undo(lyr, text))

    def _undo(self, layer, message):
        if self._busy:
            return
        self._busy = True
        try:
            stack = layer.undoStack() if hasattr(layer, "undoStack") else None
            if stack is not None and stack.canUndo():
                stack.undo()
                self.notify("Warning", message)
            # nothing to undo (e.g. the layer was just saved): stay quiet rather than
            # claim a delete was undone
        except Exception as exc:  # noqa: BLE001
            self.notify("Warning", f"The access point is still in use, and the delete could not be undone: {exc}")
        self._busy = False
        self._cache = self._read_cache(layer)
