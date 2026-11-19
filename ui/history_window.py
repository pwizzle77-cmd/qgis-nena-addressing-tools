# -*- coding: utf-8 -*-
"""
History window: every saved version of the selected road, address, street
name, driveway, or access point, and a roll back to one of them.

The versions come from the feature's version table (see
``core/archive_watch.py``). Picking a version shows what that save changed
(read from the stored OldValues/NewValues JSON), the whole feature as it was
saved, and the stored JSON itself. **Roll back to this version** writes that
version's values (and, if wanted, its shape) back onto the live feature. With
change tracking on, the roll back is saved as a new version, so it can be
undone the same way.
"""

import contextlib
from datetime import datetime

from qgis.core import (Qgis, QgsExpression, QgsExpressionContext, QgsExpressionContextUtils, QgsFeatureRequest,
                       QgsGeometry, QgsProject)
from qgis.gui import QgsRubberBand
from qgis.PyQt.QtCore import QSettings, Qt, QTimer
from qgis.PyQt.QtGui import QBrush, QColor, QFont
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialogButtonBox, QHBoxLayout,
                                 QHeaderView, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
                                 QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..compat import py_value
from ..core import history as H
from ..core.archive_model import ROLE_TITLE
from ..core.archive_watch import _coerce, _live_names, _values, key_fields_for, pairing
from ..core.project_config import load_project_config
from .widgets import NormalDialog, hint, icon

SETTINGS_KEY = "nena_addressing_and_rcl_tools/history_window"
# Edit-tracking columns the save fills itself. A roll back leaves them to the save.
KEEP_FIELDS = ("WhoEntered", "LastUpdatedBy", "DateUpdate")
STAMP_FIELD = "DateUpdate"
MAX_CANDIDATES = 200

ROLE_NOUN = {
    "centerline": "Road",
    "streets": "Street name",
    "address": "Address",
    "driveway": "Driveway",
    "access": "Access point",
}
ROLE_PRIORITY = ("centerline", "address", "streets", "driveway", "access")

_OLD_BG = QColor(253, 228, 228)
_NEW_BG = QColor(226, 243, 228)
_DIFF_BG = QColor(255, 244, 204)
_MUTED = QColor(120, 120, 120)


def _item(text, *, bold=False, italic=False, bg=None, fg=None, tip=None, data=None):
    it = QTableWidgetItem(text)
    it.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
    if bold or italic:
        font = QFont(it.font())
        font.setBold(bold)
        font.setItalic(italic)
        it.setFont(font)
    if bg is not None:
        it.setBackground(QBrush(bg))
    if fg is not None:
        it.setForeground(QBrush(fg))
    if tip:
        it.setToolTip(tip)
    if data is not None:
        it.setData(Qt.ItemDataRole.UserRole, data)
    return it


def _value_item(value, bg=None):
    text = H.display_value(value)
    if text == "":
        return _item("(empty)", italic=True, fg=_MUTED, bg=bg)
    return _item(text, bg=bg, tip=text if len(text) > 40 else None)


def _table(columns, stretch_last=True):
    t = QTableWidget(0, len(columns))
    t.setHorizontalHeaderLabels(columns)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.setAlternatingRowColors(False)
    t.setWordWrap(False)
    header = t.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setStretchLastSection(stretch_last)
    return t


def _when_text(value) -> str:
    text = H.display_value(py_value(value))
    return text[:16] if len(text) >= 16 else text


class _Target:
    """The live feature the window shows."""

    def __init__(self, role, layer, fid, label):
        self.role = role
        self.layer = layer
        self.fid = fid
        self.label = label

    def same(self, other) -> bool:
        return other is not None and other.layer is self.layer and other.fid == self.fid


class HistoryWindow(NormalDialog):
    """Version history of the selected NENA feature, with roll back."""

    def __init__(self, iface, notify, parent=None):
        super().__init__(parent or iface.mainWindow())
        self.iface = iface
        self.notify = notify
        self.setObjectName("NenaHistoryWindow")
        self.setWindowTitle("NENA feature history")
        self.setWindowIcon(icon("history.svg"))
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)

        self._live = {}
        self._archive = {}
        self._pcfg = None
        self._watched = []
        self._target = None
        self._entries = []          # oldest first
        self._band = None
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(150)
        self._reload_timer.timeout.connect(self._selection_changed)

        self._build()
        self._restore_geometry()

    # ------------------------------------------------------------------ UI
    def _build(self):
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Feature:"))
        self.cb_feature = QComboBox()
        self.cb_feature.setMinimumWidth(320)
        self.cb_feature.setToolTip("The selected roads, addresses, and other tracked features. "
                                   "Pick one to see its history.")
        self.cb_feature.currentIndexChanged.connect(self._feature_picked)
        top.addWidget(self.cb_feature, 1)
        self.chk_follow = QCheckBox("Follow map selection")
        self.chk_follow.setChecked(True)
        self.chk_follow.setToolTip("Show the history of whatever road or address you select on the map")
        self.chk_follow.toggled.connect(lambda on: on and self._selection_changed())
        top.addWidget(self.chk_follow)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.reload)
        top.addWidget(self.btn_refresh)
        lay.addLayout(top)

        self.lbl_status = hint("")
        lay.addWidget(self.lbl_status)

        split = QSplitter(Qt.Orientation.Vertical)
        split.setChildrenCollapsible(False)

        self.tbl_versions = _table(["Version", "Saved", "By", "Change", "What changed"])
        self.tbl_versions.setToolTip("Every saved version of this feature, newest first")
        self.tbl_versions.itemSelectionChanged.connect(self._version_picked)
        self.tbl_versions.itemDoubleClicked.connect(lambda *_: self._zoom_version())
        split.addWidget(self.tbl_versions)

        self.tabs = QTabWidget()
        # What changed
        self.tbl_changes = _table(["Field", "Before", "After"])
        changes_page = QWidget()
        cl = QVBoxLayout(changes_page)
        cl.setContentsMargins(4, 4, 4, 4)
        self.lbl_changes = hint("")
        cl.addWidget(self.lbl_changes)
        cl.addWidget(self.tbl_changes)
        self.tabs.addTab(changes_page, "What changed")
        # All fields
        fields_page = QWidget()
        fl = QVBoxLayout(fields_page)
        fl.setContentsMargins(4, 4, 4, 4)
        self.chk_differs = QCheckBox("Only fields that differ from the feature now")
        self.chk_differs.toggled.connect(lambda *_: self._fill_fields())
        fl.addWidget(self.chk_differs)
        self.tbl_fields = _table(["Field", "In this version", "Now"])
        fl.addWidget(self.tbl_fields)
        self.tabs.addTab(fields_page, "All fields")
        # Stored JSON
        json_page = QWidget()
        jl = QHBoxLayout(json_page)
        jl.setContentsMargins(4, 4, 4, 4)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self.txt_old = QPlainTextEdit()
        self.txt_new = QPlainTextEdit()
        for title, box in (("OldValues", self.txt_old), ("NewValues", self.txt_new)):
            box.setReadOnly(True)
            box.setFont(mono)
            col = QVBoxLayout()
            col.addWidget(QLabel(title))
            col.addWidget(box)
            jl.addLayout(col)
        self.tabs.addTab(json_page, "Stored JSON")
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        lay.addWidget(split, 1)

        buttons = QHBoxLayout()
        self.chk_shape = QCheckBox("Show this version's shape on the map")
        self.chk_shape.setChecked(True)
        self.chk_shape.toggled.connect(lambda *_: self._show_shape())
        buttons.addWidget(self.chk_shape)
        self.btn_zoom = QPushButton("Zoom to version")
        self.btn_zoom.clicked.connect(self._zoom_version)
        buttons.addWidget(self.btn_zoom)
        buttons.addStretch(1)
        self.btn_rollback = QPushButton(icon("undo_address.svg"), "Roll back to this version…")
        self.btn_rollback.setToolTip("Write the values (and shape) of the version picked above back onto "
                                     "the feature")
        self.btn_rollback.clicked.connect(self._rollback)
        buttons.addWidget(self.btn_rollback)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        buttons.addWidget(btn_close)
        lay.addLayout(buttons)
        self._sync_buttons()

    def _restore_geometry(self):
        geo = QSettings().value(f"{SETTINGS_KEY}/geometry")
        if geo is not None:
            with contextlib.suppress(Exception):
                self.restoreGeometry(geo)
                return
        self.resize(820, 640)

    # ------------------------------------------------------------ lifecycle
    def open_for_selection(self):
        """Show the window on the current selection."""
        self._configure()
        self._fill_candidates(prefer=self.iface.activeLayer())
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):  # noqa: N802
        QSettings().setValue(f"{SETTINGS_KEY}/geometry", self.saveGeometry())
        self._clear_band()
        self._unwatch()
        super().closeEvent(event)

    def cleanup(self):
        self._clear_band()
        self._unwatch()
        with contextlib.suppress(Exception):
            QgsProject.instance().layersRemoved.disconnect(self._layers_removed)

    def _configure(self):
        """Read which live layers have a version table, and watch their selections."""
        self._unwatch()
        self._pcfg = load_project_config()
        live, archive = pairing(self._pcfg)
        self._live = {}
        self._archive = {}
        for role, layer in live.items():
            dest = archive.get(role)
            if layer is None or dest is None or layer.id() == dest.id():
                continue
            self._live[role] = layer
            self._archive[role] = dest
        for layer in self._live.values():
            layer.selectionChanged.connect(self._schedule)
            self._watched.append(layer)
        with contextlib.suppress(Exception):
            QgsProject.instance().layersRemoved.disconnect(self._layers_removed)
        QgsProject.instance().layersRemoved.connect(self._layers_removed)

    def _unwatch(self):
        for layer in self._watched:
            with contextlib.suppress(Exception):
                layer.selectionChanged.disconnect(self._schedule)
        self._watched = []

    def _layers_removed(self, ids):
        ids = set(ids or [])
        gone = [lyr for lyr in list(self._live.values()) + list(self._archive.values())
                if _layer_id(lyr) in ids or _layer_id(lyr) is None]
        if gone and self.isVisible():
            self._target = None
            self._configure()
            self._fill_candidates()

    def _schedule(self, *_args):
        if self.isVisible() and self.chk_follow.isChecked():
            self._reload_timer.start()

    def _selection_changed(self):
        if not self.isVisible():
            return
        self._fill_candidates(prefer=self.iface.activeLayer())

    # ------------------------------------------------------- feature picker
    def _role_of(self, layer):
        for role, lyr in self._live.items():
            if lyr is layer:
                return role
        return None

    def _label(self, role, layer, feat) -> str:
        noun = ROLE_NOUN.get(role, ROLE_TITLE.get(role, "Feature"))
        text = ""
        with contextlib.suppress(Exception):
            exp = QgsExpression(layer.displayExpression())
            ctx = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
            ctx.setFeature(feat)
            text = H.display_value(py_value(exp.evaluate(ctx)))
        key = H.current_key(_values(feat), key_fields_for(layer, role, self._pcfg))
        parts = [f"{noun}:"]
        if text and text != key:
            parts.append(text)
        if key:
            parts.append(f"({key})")
        if len(parts) == 1:
            parts.append(f"feature {feat.id()}")
        return " ".join(parts)

    def _fill_candidates(self, prefer=None):
        roles = list(ROLE_PRIORITY)
        preferred = self._role_of(prefer) if prefer is not None else None
        if preferred:
            roles.remove(preferred)
            roles.insert(0, preferred)
        found = []
        for role in roles:
            layer = self._live.get(role)
            if layer is None:
                continue
            ids = list(layer.selectedFeatureIds())[:MAX_CANDIDATES - len(found)]
            if not ids:
                continue
            req = QgsFeatureRequest().setFilterFids(ids)
            for feat in layer.getFeatures(req):
                found.append(_Target(role, layer, feat.id(), self._label(role, layer, feat)))
            if len(found) >= MAX_CANDIDATES:
                break
        keep = self._target
        self.cb_feature.blockSignals(True)
        try:
            self.cb_feature.clear()
            pick = 0
            for pos, target in enumerate(found):
                self.cb_feature.addItem(target.label, pos)
                if target.same(keep):
                    pick = pos
            self._candidates = found
            if found:
                self.cb_feature.setCurrentIndex(pick)
        finally:
            self.cb_feature.blockSignals(False)
        self._target = found[self.cb_feature.currentIndex()] if found else None
        self.reload()

    def _feature_picked(self, index):
        found = getattr(self, "_candidates", [])
        self._target = found[index] if 0 <= index < len(found) else None
        self.reload()

    # ----------------------------------------------------------- reading
    def reload(self):
        """Read the versions of the current feature again."""
        self._entries = []
        self.tbl_versions.setRowCount(0)
        self._clear_details()
        if not self._live:
            self._status("Change tracking has no version tables in this project. Use NENA ▾ menu → Setup → "
                         "<b>Create version tables…</b>, then turn on <b>Track changes</b> in Settings → Editing.")
            self._sync_buttons()
            return
        target = self._target
        if target is None:
            tracked = ", ".join(ROLE_NOUN.get(role, role).lower() + "s" for role in ROLE_PRIORITY
                                if role in self._live)
            self._status(f"Select a road or address on the map (tracked: {tracked}).")
            self._sync_buttons()
            return
        feat = target.layer.getFeature(target.fid)
        if feat is None or not feat.isValid():
            self._status("The selected feature is no longer on the layer.")
            self._sync_buttons()
            return
        try:
            self._entries = self._read_entries(target, feat)
        except Exception as exc:  # noqa: BLE001 - show the problem instead of failing the window
            self._status(f"Could not read the version table: {exc}")
            self._sync_buttons()
            return
        version_layer = self._archive.get(target.role)
        if not self._entries:
            note = "" if self._pcfg.archive.enabled else " Track changes is off, so saves are not recorded."
            self._status(f"No saved versions yet for this feature in '{version_layer.name()}'.{note}")
        else:
            latest = self._entries[-1]
            note = "" if self._pcfg.archive.enabled else " Track changes is off: new saves are not recorded."
            self._status(f"{len(self._entries)} version(s) in '{version_layer.name()}'. "
                         f"Latest: version {latest.version or '?'} saved {_when_text(latest.when)}"
                         f"{' by ' + latest.who if latest.who else ''}.{note}")
        self._fill_versions()
        self._sync_buttons()

    def _status(self, text):
        self.lbl_status.setText(text)

    def _read_entries(self, target, feat):
        layer = target.layer
        version_layer = self._archive[target.role]
        key_fields = key_fields_for(layer, target.role, self._pcfg)
        key = H.current_key(_values(feat), key_fields)
        fields = version_layer.fields()
        has_key = fields.lookupField("FeatureKey") >= 0
        has_fid = fields.lookupField("SourceFid") >= 0
        keys = {key} if key else set()
        rows = {}
        for _round in range(6):
            parts = []
            if keys and has_key:
                values = ", ".join(QgsExpression.quotedValue(k) for k in sorted(keys))
                parts.append(f"{QgsExpression.quotedColumnRef('FeatureKey')} IN ({values})")
            if has_fid:
                parts.append(f"{QgsExpression.quotedColumnRef('SourceFid')} = "
                             f"{QgsExpression.quotedValue(str(target.fid))}")
            req = QgsFeatureRequest()
            if parts:
                req.setFilterExpression(" OR ".join(parts))
            for row in version_layer.getFeatures(req):
                if row.id() in rows:
                    continue
                attrs = {}
                for i, fld in enumerate(row.fields()):
                    attrs[fld.name()] = py_value(row.attribute(i))
                geom = row.geometry() if row.hasGeometry() else None
                rows[row.id()] = H.entry_from_row(attrs, geometry=QgsGeometry(geom) if geom else None,
                                                  row_id=row.id())
            chosen = H.feature_entries(rows.values(), key, target.fid, key_fields)
            found = {e.feature_key for e in chosen if e.feature_key}
            for entry in chosen:
                for name in key_fields:
                    for side in (entry.old_values, entry.new_values):
                        value = side.get(name)
                        if value not in (None, ""):
                            found.add(str(value).strip())
            if not has_key or found <= keys:
                return chosen
            keys |= found
        return H.feature_entries(rows.values(), key, target.fid, key_fields)

    # ---------------------------------------------------------- showing
    def _fill_versions(self):
        t = self.tbl_versions
        t.setRowCount(0)
        newest_first = list(reversed(self._entries))
        for pos, entry in enumerate(newest_first):
            t.insertRow(pos)
            index = len(self._entries) - 1 - pos
            current = pos == 0
            number = str(entry.version) if entry.version is not None else "?"
            t.setItem(pos, 0, _item(number + ("  (latest)" if current else ""), bold=current, data=index))
            t.setItem(pos, 1, _item(_when_text(entry.when)))
            t.setItem(pos, 2, _item(entry.who))
            t.setItem(pos, 3, _item(entry.title))
            summary = H.changes_summary(entry)
            t.setItem(pos, 4, _item(summary, tip=", ".join(entry.changed_fields) or None))
        t.resizeColumnsToContents()
        if newest_first:
            t.selectRow(0)

    def _picked(self):
        rows = self.tbl_versions.selectionModel().selectedRows() if self.tbl_versions.selectionModel() else []
        if not rows:
            return None, None
        item = self.tbl_versions.item(rows[0].row(), 0)
        index = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if index is None or not 0 <= int(index) < len(self._entries):
            return None, None
        index = int(index)
        previous = self._entries[index - 1] if index > 0 else None
        return self._entries[index], previous

    def _version_picked(self):
        entry, previous = self._picked()
        self._clear_details()
        if entry is None:
            self._sync_buttons()
            return
        self._fill_changes(entry, previous)
        self._fill_fields()
        self.txt_old.setPlainText(H.pretty_json(entry.old_values) if entry.old_values else
                                  "(nothing stored — only an update stores old values)")
        self.txt_new.setPlainText(H.pretty_json(entry.new_values) if entry.new_values else
                                  "(nothing stored — only an update stores new values)")
        self._show_shape()
        self._sync_buttons()

    def _fill_changes(self, entry, previous):
        changes = H.field_changes(entry, previous)
        t = self.tbl_changes
        t.setRowCount(0)
        when = _when_text(entry.when)
        who = f" by {entry.who}" if entry.who else ""
        if entry.change_type == "insert":
            note = f"Version {entry.version or '?'}: the feature was added {when}{who}. Its values:"
        elif entry.change_type == "delete":
            note = f"Version {entry.version or '?'}: the feature was deleted {when}{who}. It had:"
        else:
            note = f"Version {entry.version or '?'}: saved {when}{who}."
            if not entry.values_ok:
                note += " The stored change text was cut off, so the change is worked out from the versions."
        self.lbl_changes.setText(note)
        for pos, change in enumerate(changes):
            t.insertRow(pos)
            if change.name == H.GEOMETRY:
                t.setItem(pos, 0, _item("Shape", bold=True))
                t.setItem(pos, 1, _item("(previous shape)", italic=True, bg=_OLD_BG))
                t.setItem(pos, 2, _item("(shape moved or reshaped — see the map)", italic=True, bg=_NEW_BG))
                continue
            t.setItem(pos, 0, _item(change.name, bold=True))
            insert = entry.change_type == "insert"
            delete = entry.change_type == "delete"
            t.setItem(pos, 1, _item("—", fg=_MUTED) if insert else _value_item(change.old, _OLD_BG))
            t.setItem(pos, 2, _item("—", fg=_MUTED) if delete else _value_item(change.new, _NEW_BG))
        if not changes:
            t.insertRow(0)
            t.setItem(0, 0, _item("No field changes were stored for this version.", italic=True, fg=_MUTED))
        t.resizeColumnToContents(0)
        t.setColumnWidth(1, max(160, t.columnWidth(1)))

    def _fill_fields(self):
        entry, _previous = self._picked()
        t = self.tbl_fields
        t.setRowCount(0)
        if entry is None or self._target is None:
            return
        feat = self._target.layer.getFeature(self._target.fid)
        now = _values(feat) if feat is not None and feat.isValid() else {}
        names = [n for n in _live_names(self._target.layer) if n.lower() not in H.SKIP_NAMES]
        stored = {str(k).lower(): v for k, v in entry.values.items()}
        only_diff = self.chk_differs.isChecked()
        row = 0
        for name in names:
            in_version = name.lower() in stored
            value = stored.get(name.lower())
            current = now.get(name)
            differs = in_version and not H.same_value(value, current)
            if only_diff and not differs:
                continue
            t.insertRow(row)
            bg = _DIFF_BG if differs else None
            t.setItem(row, 0, _item(name, bold=differs, bg=bg))
            if in_version:
                t.setItem(row, 1, _value_item(value, bg))
            else:
                t.setItem(row, 1, _item("(not stored)", italic=True, fg=_MUTED, bg=bg))
            t.setItem(row, 2, _value_item(current, bg))
            row += 1
        t.resizeColumnToContents(0)

    def _clear_details(self):
        self.tbl_changes.setRowCount(0)
        self.tbl_fields.setRowCount(0)
        self.txt_old.clear()
        self.txt_new.clear()
        self.lbl_changes.setText("")
        self._clear_band()

    def _sync_buttons(self):
        entry, _prev = self._picked() if hasattr(self, "tbl_versions") else (None, None)
        has = entry is not None and self._target is not None
        self.btn_rollback.setEnabled(has and self._editable(self._target.layer))
        self.btn_zoom.setEnabled(has and entry.geometry is not None)

    @staticmethod
    def _editable(layer) -> bool:
        if layer is None:
            return False
        with contextlib.suppress(Exception):
            if layer.readOnly():
                return False
        return True

    # ------------------------------------------------------------- map
    def _clear_band(self):
        if self._band is not None:
            with contextlib.suppress(Exception):
                self.iface.mapCanvas().scene().removeItem(self._band)
            self._band = None

    def _show_shape(self):
        self._clear_band()
        if not self.chk_shape.isChecked() or self._target is None:
            return
        entry, _prev = self._picked()
        if entry is None or entry.geometry is None:
            return
        version_layer = self._archive.get(self._target.role)
        geom = QgsGeometry(entry.geometry)
        band = QgsRubberBand(self.iface.mapCanvas(), geom.type())
        band.setColor(QColor(230, 120, 0, 200))
        band.setFillColor(QColor(230, 120, 0, 60))
        band.setWidth(4 if geom.type() != _point_type() else 2)
        with contextlib.suppress(Exception):
            band.setIconSize(14)
        band.setToGeometry(geom, version_layer or self._target.layer)
        self._band = band

    def _zoom_version(self):
        entry, _prev = self._picked()
        if entry is None or entry.geometry is None or self._target is None:
            return
        canvas = self.iface.mapCanvas()
        source = self._archive.get(self._target.role) or self._target.layer
        with contextlib.suppress(Exception):
            from qgis.core import QgsCoordinateTransform
            xform = QgsCoordinateTransform(source.crs(), canvas.mapSettings().destinationCrs(),
                                           QgsProject.instance())
            rect = xform.transformBoundingBox(entry.geometry.boundingBox())
            if rect.width() == 0 and rect.height() == 0:
                rect.grow(50)
            else:
                rect.scale(1.6)
            canvas.setExtent(rect)
            canvas.refresh()
        self._show_shape()

    # ---------------------------------------------------------- roll back
    def _rollback(self):
        entry, _prev = self._picked()
        target = self._target
        if entry is None or target is None:
            return
        layer = target.layer
        feat = layer.getFeature(target.fid)
        if feat is None or not feat.isValid():
            QMessageBox.warning(self, "Roll back", "The feature is no longer on the layer.")
            return
        changes = H.rollback_changes(entry, _values(feat), _live_names(layer), keep=KEEP_FIELDS)
        shape = None
        if entry.geometry is not None and layer.isSpatial():
            current = feat.geometry()
            if current is None or current.isNull() or not current.equals(entry.geometry):
                shape = QgsGeometry(entry.geometry)
        if not changes and shape is None:
            QMessageBox.information(self, "Roll back",
                                    f"The feature already matches version {entry.version or '?'}.")
            return
        dlg = RollbackDialog(target.label, entry, changes, shape is not None, self)
        if not dlg.exec():
            return
        if not dlg.restore_shape():
            shape = None
        if not changes and shape is None:
            return
        self._apply(target, entry, changes, shape)

    def _apply(self, target, entry, changes, shape):
        layer = target.layer
        was_editing = layer.isEditable()
        if not was_editing and not layer.startEditing():
            QMessageBox.warning(self, "Roll back", f"Could not start editing '{layer.name()}'.")
            return
        fields = layer.fields()
        number = entry.version or "?"
        layer.beginEditCommand(f"Roll back to version {number}")
        failed = []
        try:
            for change in changes:
                idx = fields.lookupField(change.name)
                if idx < 0:
                    failed.append(change.name)
                    continue
                value = _coerce(change.new, fields.field(idx))
                if value is None and change.new is not None:
                    value = change.new
                if not layer.changeAttributeValue(target.fid, idx, value):
                    failed.append(change.name)
            stamp = fields.lookupField(STAMP_FIELD)
            if stamp >= 0 and (changes or shape is not None):
                layer.changeAttributeValue(target.fid, stamp, _coerce(datetime.now().replace(microsecond=0),
                                                                      fields.field(stamp)))
            if shape is not None and not layer.changeGeometry(target.fid, shape):
                failed.append("shape")
        except Exception as exc:  # noqa: BLE001
            layer.destroyEditCommand()
            if not was_editing:
                layer.rollBack()
            QMessageBox.warning(self, "Roll back", f"The roll back was not applied: {exc}")
            return
        layer.endEditCommand()

        if was_editing:
            msg = (f"Rolled back to version {number} in the open edit session on '{layer.name()}'. "
                   "Save your edits to keep it (Undo reverses it).")
            self.notify("Info", msg)
        else:
            if not layer.commitChanges():
                errors = "\n".join(layer.commitErrors()) if hasattr(layer, "commitErrors") else ""
                layer.rollBack()
                QMessageBox.warning(self, "Roll back", f"'{layer.name()}' did not save the roll back.\n\n{errors}")
                return
            tracked = " It was recorded as a new version." if self._pcfg and self._pcfg.archive.enabled else ""
            self.notify("Success", f"Rolled back to version {number}.{tracked}")
        if failed:
            self.notify("Warning", "These could not be written back: " + ", ".join(failed))
        layer.triggerRepaint()
        # The new version row is written just after the save returns.
        QTimer.singleShot(600, self.reload)


class RollbackDialog(NormalDialog):
    """Confirm a roll back: the fields it will write, and whether to restore the shape."""

    def __init__(self, label, entry, changes, shape_differs, parent=None):
        super().__init__(parent)
        self.setObjectName("NenaRollbackDialog")
        self.setWindowTitle(f"Roll back to version {entry.version or '?'}")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        lay = QVBoxLayout(self)
        when = _when_text(entry.when)
        who = f" by {entry.who}" if entry.who else ""
        lay.addWidget(QLabel(f"<b>{label}</b><br>Restore the values saved in version "
                             f"{entry.version or '?'} ({when}{who})."))
        if changes:
            t = _table(["Field", "Now", "Will become"])
            for pos, change in enumerate(changes):
                t.insertRow(pos)
                t.setItem(pos, 0, _item(change.name, bold=True))
                t.setItem(pos, 1, _value_item(change.old, _OLD_BG))
                t.setItem(pos, 2, _value_item(change.new, _NEW_BG))
            t.resizeColumnsToContents()
            t.setMinimumHeight(min(320, 60 + 26 * len(changes)))
            lay.addWidget(t)
        else:
            lay.addWidget(hint("Every field already matches that version."))
        self.chk_shape = QCheckBox("Also restore the shape from that version")
        self.chk_shape.setChecked(shape_differs)
        self.chk_shape.setEnabled(shape_differs)
        if not shape_differs:
            self.chk_shape.setText("The shape already matches that version")
        lay.addWidget(self.chk_shape)
        lay.addWidget(hint("WhoEntered, LastUpdatedBy, and DateUpdate are filled by the save, not copied "
                           "from the old version. If the layer is not being edited, the roll back is saved "
                           "right away; otherwise it goes into your edit session for you to save."))
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Ok).setText("Roll back")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.resize(560, 420)

    def restore_shape(self) -> bool:
        return self.chk_shape.isEnabled() and self.chk_shape.isChecked()


def _layer_id(layer):
    try:
        return layer.id()
    except Exception:  # noqa: BLE001 - the layer was deleted
        return None


def _point_type():
    return Qgis.GeometryType.Point
