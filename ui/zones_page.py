# -*- coding: utf-8 -*-
"""
Zones: which boundary layers to use (Settings, Zones) and their check
results (the Zones tab of the Check window).

Zone layers stay wherever they are kept, usually their own database. They
are only pointed to here. Each one has a kind, the field that names a zone,
**Split roads at this boundary** (on by default), and which checks run.
"""

import contextlib
from datetime import datetime

from qgis.core import QgsCoordinateTransform, QgsProject, QgsRectangle, QgsVectorLayer
from qgis.gui import QgsFieldComboBox, QgsMapLayerComboBox
from qgis.PyQt.QtCore import QDate, Qt
from qgis.PyQt.QtGui import QBrush, QColor
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QDateEdit, QDialogButtonBox, QDoubleSpinBox,
                                 QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
                                 QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QTableWidget,
                                 QTableWidgetItem, QVBoxLayout, QWidget)

from ..compat import table_layer_filter
from ..core import check_exceptions as X
from ..core import zones as Z
from ..core.layer_access import ConfigError
from ..core.project_config import layer_for, load_project_config, remember, resolve_layer, save_project_config
from ..core.rules import ERROR, INFO, WARNING
from .widgets import NormalDialog, combo, hint, page_into, set_combo_data

try:
    from qgis.core import Qgis
    _POLYGONS = Qgis.LayerFilter.PolygonLayer
except AttributeError:  # pragma: no cover - QGIS before 3.34
    from qgis.core import QgsMapLayerProxyModel
    _POLYGONS = QgsMapLayerProxyModel.PolygonLayer

SEV_COLORS = {ERROR: QColor(231, 76, 60, 60), WARNING: QColor(243, 156, 18, 60), INFO: QColor(52, 152, 219, 40)}
_GREY = QColor(130, 130, 130)


def _cell(text, data=None, bg=None, fg=None, tip=None):
    it = QTableWidgetItem("" if text is None else str(text))
    it.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
    if data is not None:
        it.setData(Qt.ItemDataRole.UserRole, data)
    if bg is not None:
        it.setBackground(QBrush(bg))
    if fg is not None:
        it.setForeground(QBrush(fg))
    if tip:
        it.setToolTip(tip)
    return it


def _table(columns):
    t = QTableWidget(0, len(columns))
    t.setHorizontalHeaderLabels(columns)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setWordWrap(False)
    t.horizontalHeader().setStretchLastSection(True)
    return t


# ---------------------------------------------------------------------------
# Settings, Zones
# ---------------------------------------------------------------------------
class ZonesSettingsPage(QWidget):
    """Pick the zone layers, how each is used, and the exceptions table."""

    def __init__(self, dock, parent=None):
        super().__init__(parent)
        self.dock = dock
        self.zones = []
        self._loading = False
        lay = page_into(self)
        lay.addWidget(hint("PSAP, provisioning, law, fire, EMS, city limits, and other boundary polygons. "
                           "They stay where you keep them (their own database is fine): add the layers to the "
                           "project and list them here. Nothing is copied onto roads or addresses. Check uses "
                           "them, and roads are split at the boundaries ticked <b>Split roads</b>."))

        g = QGroupBox("Zone layers")
        v = QVBoxLayout(g)
        self.tbl = _table(["Layer", "Kind", "Name field", "Split roads"])
        self.tbl.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tbl.setMinimumHeight(140)
        self.tbl.itemSelectionChanged.connect(self._picked)
        v.addWidget(self.tbl)
        row = QHBoxLayout()
        for text, tip, slot in (
                ("Add", "Add a zone layer from the project", self._add),
                ("Add all from project", "Add every polygon layer whose name looks like a zone layer "
                                         "(fire, law, sheriff, EMS, PSAP, provisioning, city limits, ESN…)",
                 self._add_guessed),
                ("Remove", "Stop using the selected zone layer (the layer itself is not touched)", self._remove)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda *_, s=slot: s())
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        lay.addWidget(g)

        self.editor = QGroupBox("Selected zone layer")
        f = QFormLayout(self.editor)
        self.cb_layer = QgsMapLayerComboBox()
        self.cb_layer.setFilters(_POLYGONS)
        self.cb_layer.layerChanged.connect(self._layer_changed)
        f.addRow("Layer", self.cb_layer)
        self.cb_kind = combo([(k.title, k.key) for k in Z.ZONE_KINDS])
        self.cb_kind.currentIndexChanged.connect(lambda *_: self._edited(kind_changed=True))
        f.addRow("Kind", self.cb_kind)
        self.fc_value = QgsFieldComboBox()
        self.fc_value.setToolTip("The field that names each zone in messages, e.g. DsplayName or ESN")
        self.fc_value.fieldChanged.connect(lambda *_: self._edited())
        f.addRow("Name field", self.fc_value)
        self.chk_split = QCheckBox("Split roads at this boundary")
        self.chk_split.setToolTip("Roads that cross this boundary are split there (Split where the side "
                                  "changes) and Check reports roads that are not. Untick it for a layer whose "
                                  "lines may run through the middle of roads.")
        self.chk_split.toggled.connect(lambda *_: self._edited())
        f.addRow(self.chk_split)
        box = QGroupBox("Checks for this layer")
        grid = QGridLayout(box)
        self.chk = {}
        for i, (key, title) in enumerate(Z.CHECKS):
            cb = QCheckBox(title)
            cb.toggled.connect(lambda *_: self._edited())
            self.chk[key] = cb
            grid.addWidget(cb, i, 0)
        f.addRow(box)
        self.sp_tol = QDoubleSpinBox()
        self.sp_tol.setRange(0, 1e7)
        self.sp_tol.setDecimals(1)
        self.sp_tol.setSuffix(" m²")
        self.sp_tol.setToolTip("Gaps, overlaps, and slivers smaller than this are not reported")
        self.sp_tol.valueChanged.connect(lambda *_: self._edited())
        f.addRow("Ignore gaps / overlaps under", self.sp_tol)
        lay.addWidget(self.editor)

        g = QGroupBox("Check exceptions table")
        f = QFormLayout(g)
        f.addRow(hint("Results you accept in the Check window are stored here, with who, when, and why. "
                      "Keep it with your NENA tables or in its own file."))
        self.cb_exc = QgsMapLayerComboBox()
        self.cb_exc.setFilters(table_layer_filter())
        self.cb_exc.setAllowEmptyLayer(True)
        self.cb_exc.layerChanged.connect(lambda *_: self._store_exceptions())
        f.addRow("Table", self.cb_exc)
        b = QPushButton("Create in a GeoPackage…")
        b.setToolTip("Make an empty CheckExceptions table in a new or existing GeoPackage and use it")
        b.clicked.connect(self._create_exceptions)
        f.addRow(b)
        lay.addWidget(g)
        lay.addStretch(1)
        self.load()

    # -- load / store -----------------------------------------------------
    def load(self):
        pcfg = load_project_config()
        self.zones = Z.load_zones(pcfg.zones.layers)
        self._loading = True
        try:
            self.cb_exc.setLayer(layer_for(pcfg.zones, "exceptions"))
        finally:
            self._loading = False
        self._fill(0 if self.zones else -1)

    def _save(self):
        pcfg = load_project_config()
        pcfg.zones.layers = Z.dump_zones(self.zones)
        save_project_config(pcfg)

    def _store_exceptions(self):
        if self._loading:
            return
        pcfg = load_project_config()
        remember(pcfg.zones, "exceptions", self.cb_exc.currentLayer())
        save_project_config(pcfg)

    def _fill(self, select=-1):
        self.tbl.blockSignals(True)
        try:
            self.tbl.setRowCount(len(self.zones))
            for row, zone in enumerate(self.zones):
                layer = resolve_layer(zone.layer_id, zone.layer_source)
                name = layer.name() if layer is not None else "(not in the project)"
                self.tbl.setItem(row, 0, _cell(name, fg=None if layer is not None else _GREY,
                                               tip=zone.layer_source))
                self.tbl.setItem(row, 1, _cell(zone.title))
                self.tbl.setItem(row, 2, _cell(zone.value_field))
                self.tbl.setItem(row, 3, _cell("Yes" if zone.split_roads else "No"))
            self.tbl.resizeColumnsToContents()
        finally:
            self.tbl.blockSignals(False)
        if 0 <= select < len(self.zones):
            self.tbl.selectRow(select)
        self._picked()

    def _row(self) -> int:
        rows = self.tbl.selectionModel().selectedRows() if self.tbl.selectionModel() else []
        return rows[0].row() if rows else -1

    def _picked(self):
        row = self._row()
        self.editor.setEnabled(0 <= row < len(self.zones))
        if not 0 <= row < len(self.zones):
            return
        zone = self.zones[row]
        self._loading = True
        try:
            layer = resolve_layer(zone.layer_id, zone.layer_source)
            self.cb_layer.setLayer(layer)
            self.fc_value.setLayer(layer)
            set_combo_data(self.cb_kind, zone.kind)
            self.fc_value.setField(zone.value_field)
            self.chk_split.setChecked(zone.split_roads)
            for key, cb in self.chk.items():
                cb.setChecked(zone.check_on(key))
            self.sp_tol.setValue(zone.gap_tolerance_m2)
        finally:
            self._loading = False

    def _layer_changed(self, layer):
        if self._loading:
            return
        self.fc_value.setLayer(layer)
        row = self._row()
        if 0 <= row < len(self.zones) and layer is not None:
            zone = self.zones[row]
            zone.layer_id, zone.layer_source = layer.id(), layer.source()
            if layer.fields().lookupField(zone.value_field) < 0:
                zone.value_field = _value_field(layer, zone.kind)
                self.fc_value.setField(zone.value_field)
            self._save()
            self._fill(row)

    def _edited(self, kind_changed=False):
        if self._loading:
            return
        row = self._row()
        if not 0 <= row < len(self.zones):
            return
        zone = self.zones[row]
        new_kind = self.cb_kind.currentData() or zone.kind
        if kind_changed and new_kind != zone.kind:
            zone.kind = new_kind
            zone.checks = {}
            layer = resolve_layer(zone.layer_id, zone.layer_source)
            zone.value_field = _value_field(layer, new_kind) if layer is not None else Z.default_value_field(new_kind)
            self._save()
            self._fill(row)
            return
        zone.value_field = self.fc_value.currentField() or zone.value_field
        split_was = zone.split_roads
        zone.split_roads = self.chk_split.isChecked()
        for key, cb in self.chk.items():
            if key == "roads" and split_was != zone.split_roads:
                cb.blockSignals(True)
                cb.setChecked(zone.split_roads)
                cb.blockSignals(False)
            if cb.isChecked() != Z.default_check(zone.kind, key, zone.split_roads):
                zone.checks[key] = cb.isChecked()
            else:
                zone.checks.pop(key, None)
        zone.gap_tolerance_m2 = self.sp_tol.value()
        self._save()
        self._fill(row)

    # -- buttons -------------------------------------------------------------
    def _add(self):
        used = {z.layer_id for z in self.zones}
        layer = self.dock.iface.activeLayer()
        if not (isinstance(layer, QgsVectorLayer) and _is_polygon(layer) and layer.id() not in used):
            layer = next((lyr for lyr in _polygon_layers() if lyr.id() not in used), None)
        if layer is None:
            self.dock._msg("Add the zone polygon layers to the project first (any database or file).", "Info")
            return
        kind = Z.guess_kind(layer.name()) or "fire"
        self.zones.append(Z.new_zone(layer.id(), layer.source(), kind, _value_field(layer, kind)))
        self._save()
        self._fill(len(self.zones) - 1)

    def _add_guessed(self):
        used = {z.layer_id for z in self.zones}
        added = 0
        for layer in _polygon_layers():
            if layer.id() in used:
                continue
            kind = Z.guess_kind(layer.name())
            if not kind or "version" in layer.name().lower():
                continue
            self.zones.append(Z.new_zone(layer.id(), layer.source(), kind, _value_field(layer, kind)))
            added += 1
        self._save()
        self._fill(len(self.zones) - 1 if added else self._row())
        self.dock._msg(f"Added {added} zone layer(s). Check each one's kind and name field." if added else
                       "No polygon layer in the project looks like a zone layer. Use Add to pick one.", "Info")

    def _remove(self):
        row = self._row()
        if 0 <= row < len(self.zones):
            del self.zones[row]
            self._save()
            self._fill(min(row, len(self.zones) - 1))

    def _create_exceptions(self):
        path, _ = QFileDialog.getSaveFileName(self, "GeoPackage for the CheckExceptions table", "",
                                              "GeoPackage (*.gpkg)",
                                              options=QFileDialog.Option.DontConfirmOverwrite)
        if not path:
            return
        from ..core.schema_writer import SchemaTarget, create_schema

        def do():
            target = SchemaTarget("gpkg", path=path)
            created = create_schema(target, None, ["exceptions"])
            layer = created.get("exceptions")
            if layer is None:
                raise ConfigError("The table was written but could not be opened.")
            QgsProject.instance().addMapLayer(layer)
            self.cb_exc.setLayer(layer)
            self._store_exceptions()
            self.dock._msg(f"Created {X.TABLE} in {path}.", "Success")
        self.dock._guard(do)


def _is_polygon(layer) -> bool:
    try:
        return layer.geometryType() == Qgis.GeometryType.Polygon
    except Exception:  # noqa: BLE001
        return False


def _polygon_layers():
    layers = [lyr for lyr in QgsProject.instance().mapLayers().values()
              if isinstance(lyr, QgsVectorLayer) and _is_polygon(lyr)]
    return sorted(layers, key=lambda lyr: lyr.name().lower())


def _value_field(layer, kind) -> str:
    """The kind's usual name field when the layer has it, else the first text field."""
    wanted = Z.default_value_field(kind)
    fields = layer.fields() if layer is not None else None
    if fields is None:
        return wanted
    for name in (wanted, "DsplayName", "NAME", "Name", "LABEL", "DISTRICT", "ZONE"):
        idx = fields.lookupField(name)
        if idx >= 0:
            return fields.at(idx).name()
    for i in range(fields.count()):
        if "string" in (fields.at(i).typeName() or "").lower() or fields.at(i).type() == 10:
            return fields.at(i).name()
    return wanted


# ---------------------------------------------------------------------------
# Check window, Zones tab
# ---------------------------------------------------------------------------
class ZoneResultsPage(QWidget):
    """Run the zone checks and review, accept, or reopen results."""

    COLUMNS = ["Severity", "Layer", "Feature", "Check", "Zone", "Message"]

    def __init__(self, dock, parent=None):
        super().__init__(parent)
        self.dock = dock
        self.issues = []
        lay = page_into(self)
        row = QHBoxLayout()
        b = QPushButton("Check zones")
        b.setToolTip("Check the zone layers, and the roads and addresses against them (Settings, Zones)")
        b.clicked.connect(lambda *_: self.run())
        row.addWidget(b)
        b = QPushButton("Settings…")
        b.clicked.connect(lambda *_: self.dock.open_settings("Zones"))
        row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        top = QHBoxLayout()
        self.lbl = QLabel("Not run yet.")
        self.lbl.setWordWrap(True)
        top.addWidget(self.lbl, 1)
        self.cb_filter = combo([("All", ""), ("Errors", ERROR), ("Warnings", WARNING), ("Info", INFO)])
        self.cb_filter.currentIndexChanged.connect(self._fill)
        top.addWidget(self.cb_filter)
        self.chk_excused = QCheckBox("Show exceptions")
        self.chk_excused.setToolTip("Also list the results accepted as exceptions (greyed out)")
        self.chk_excused.toggled.connect(self._fill)
        top.addWidget(self.chk_excused)
        lay.addLayout(top)
        self.tbl = _table(self.COLUMNS)
        self.tbl.setMinimumHeight(180)
        hh = self.tbl.horizontalHeader()
        for c in range(5):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl.setSortingEnabled(True)
        self.tbl.itemDoubleClicked.connect(lambda *_: self.zoom())
        self.tbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tbl.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.tbl, 1)
        row = QHBoxLayout()
        for text, tip, slot in (("Zoom", "Zoom to the selected result", self.zoom),
                                ("Select", "Select the features in the listed results", self.select),
                                ("Accept as exception…", "Excuse the selected results, with a reason",
                                 self.accept_selected),
                                ("Remove exception", "Show the selected excused results again",
                                 self.remove_selected)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda *_, s=slot: s())
            row.addWidget(b)
        lay.addLayout(row)
        lay.addStretch(1)

    # -- running ---------------------------------------------------------------
    def run(self):
        from ..core.zone_checks import run_zone_checks

        def do():
            self.dock.open_page(self.dock.validation)
            self.dock.validation.pages.setCurrentWidget(self)
            self.lbl.setText("Checking zones…")
            pcfg = load_project_config()
            if not Z.load_zones(pcfg.zones.layers):
                raise ConfigError("No zone layers yet. Add them under Settings, Zones.")
            rcl = self.dock.layer
            key_field = (self.dock.cfg.mapping.get("RCL_NGUID") if self.dock.cfg is not None else "") or "RCL_NGUID"
            try:
                ctx = self.dock.addresses.context()
            except ConfigError:
                ctx = None
            exc = X.ExceptionSet.from_layer(layer_for(pcfg.zones, "exceptions"))

            def work(cb):
                def note(pct, text=None):
                    if text:
                        self.lbl.setText(text)
                    cb(pct, text)
                return run_zone_checks(pcfg, rcl, key_field, ctx, exc, note)

            issues = self.dock._with_progress(work)
            if issues is None:
                return
            self.issues = list(issues)
            self._fill()
            if ctx is None:
                self.dock._msg("Address checks were skipped: choose the address layer in the Addresses window.",
                               "Info")
        self.dock._guard(do)

    def _visible(self):
        sev = self.cb_filter.currentData()
        show_excused = self.chk_excused.isChecked()
        return [i for i in self.issues
                if (not sev or i.severity == sev) and (show_excused or i.status != X.EXCUSED)]

    def _fill(self, *_args):
        from ..core.zone_checks import counts
        n = counts(self.issues)
        if not self.issues:
            self.lbl.setText("<b style='color:#27ae60'>No zone issues found.</b>" if hasattr(self, "_ran")
                             else "Not run yet.")
        else:
            text = (f"<b>{n[ERROR]}</b> errors, <b>{n[WARNING]}</b> warnings, {n[INFO]} info")
            if n[X.EXCUSED]:
                text += f"; {n[X.EXCUSED]} accepted as exceptions"
            if n[X.STALE]:
                text += f"; <b>{n[X.STALE]}</b> were excused before the feature's last edit"
            self.lbl.setText(text + ".")
        self._ran = True
        rows = self._visible()
        position = {id(issue): pos for pos, issue in enumerate(self.issues)}
        self.tbl.setSortingEnabled(False)
        self.tbl.setRowCount(len(rows))
        for r, issue in enumerate(rows):
            excused = issue.status == X.EXCUSED
            fg = _GREY if excused else None
            layer = QgsProject.instance().mapLayer(issue.layer_id)
            msg = issue.message
            if issue.status == X.STALE:
                msg = "Excused before the last edit. " + msg
            elif excused and issue.exception is not None:
                msg = f"Accepted: {issue.exception.reason}. " + msg
            cells = (issue.severity, layer.name() if layer is not None else issue.role, issue.key, issue.code,
                     issue.zone_layer, msg)
            for c, value in enumerate(cells):
                bg = SEV_COLORS.get(issue.severity) if c == 0 and not excused else None
                self.tbl.setItem(r, c, _cell(value, data=position[id(issue)], bg=bg, fg=fg,
                                             tip=msg if c == 5 else None))
        self.tbl.setSortingEnabled(True)

    def _selected(self):
        out = []
        for idx in self.tbl.selectionModel().selectedRows():
            item = self.tbl.item(idx.row(), 0)
            pos = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if pos is not None and 0 <= int(pos) < len(self.issues):
                out.append(self.issues[int(pos)])
        return out

    def _menu(self, point):
        menu = QMenu(self.tbl)
        menu.addAction("Zoom", self.zoom)
        menu.addAction("Select", self.select)
        menu.addSeparator()
        menu.addAction("Accept as exception…", self.accept_selected)
        menu.addAction("Remove exception", self.remove_selected)
        menu.exec(self.tbl.viewport().mapToGlobal(point))

    # -- map -------------------------------------------------------------------
    def zoom(self):
        picked = self._selected()[:1]
        if not picked:
            return
        issue = picked[0]
        layer = QgsProject.instance().mapLayer(issue.layer_id)
        canvas = self.dock.iface.mapCanvas()
        if layer is None:
            return
        if issue.fid is not None:
            with contextlib.suppress(Exception):
                layer.selectByIds([issue.fid])
        geom = issue.geometry
        if geom is None and issue.fid is not None:
            feat = layer.getFeature(issue.fid)
            geom = feat.geometry() if feat is not None and feat.hasGeometry() else None
        if geom is None or geom.isEmpty():
            return
        xform = QgsCoordinateTransform(layer.crs(), canvas.mapSettings().destinationCrs(), QgsProject.instance())
        rect = xform.transformBoundingBox(geom.boundingBox())
        if rect.width() == 0 and rect.height() == 0:
            rect = QgsRectangle(rect.xMinimum() - 50, rect.yMinimum() - 50, rect.xMaximum() + 50,
                                rect.yMaximum() + 50)
        else:
            rect.scale(1.4)
        canvas.setExtent(rect)
        canvas.refresh()
        with contextlib.suppress(Exception):
            canvas.flashGeometries([geom], layer.crs())

    def select(self):
        by_layer = {}
        for issue in self._selected() or self._visible():
            if issue.fid is not None:
                by_layer.setdefault(issue.layer_id, set()).add(issue.fid)
        for lid, fids in by_layer.items():
            layer = QgsProject.instance().mapLayer(lid)
            if layer is not None:
                layer.selectByIds(sorted(fids))

    # -- exceptions -------------------------------------------------------------
    def _exceptions_layer(self):
        layer = layer_for(load_project_config().zones, "exceptions")
        if layer is None:
            raise ConfigError("Choose or create the Check exceptions table under Settings, Zones first.")
        return layer

    def accept_selected(self):
        def do():
            picked = [i for i in self._selected() if i.status != X.EXCUSED]
            if not picked:
                self.dock._msg("Select one or more results that are not already excused.", "Info")
                return
            layer = self._exceptions_layer()
            dlg = ExceptionDialog(picked, self)
            if not dlg.exec():
                return
            from ..core.archive_watch import current_user
            who = current_user()
            done = 0
            for issue in picked:
                if issue.status == X.STALE and issue.exception is not None:
                    X.remove_exception(layer, issue.exception)
                zone = issue.zone_kind if dlg.this_zone_only() else ""
                item = X.new_exception(issue.code, issue.role, issue.key, dlg.reason(), zone, who,
                                       dlg.expires(), issue.feature_date)
                X.write_exception(layer, item)
                issue.status, issue.exception = X.EXCUSED, item
                done += 1
            self._fill()
            self.dock._msg(f"Accepted {done} result(s) as exceptions.", "Success")
        self.dock._guard(do)

    def remove_selected(self):
        def do():
            picked = [i for i in self._selected() if i.exception is not None]
            if not picked:
                self.dock._msg("Select excused results (tick Show exceptions to see them).", "Info")
                return
            layer = self._exceptions_layer()
            for issue in picked:
                X.remove_exception(layer, issue.exception)
                issue.status, issue.exception = "", None
            self._fill()
            self.dock._msg(f"Removed {len(picked)} exception(s).", "Success")
        self.dock._guard(do)


class ExceptionDialog(NormalDialog):
    """Reason, scope, and an optional end date for new exceptions."""

    def __init__(self, issues, parent=None):
        super().__init__(parent)
        self.setObjectName("NenaExceptionDialog")
        self.setWindowTitle("Accept as exception")
        lay = QVBoxLayout(self)
        codes = sorted({i.code for i in issues})
        lay.addWidget(QLabel(f"Accept {len(issues)} result(s): {', '.join(codes)}."))
        form = QFormLayout()
        self.ed_reason = QLineEdit()
        self.ed_reason.setPlaceholderText("Why this is correct, e.g. bridge over the district line, agreed with FD")
        self.ed_reason.setMaxLength(254)
        form.addRow("Reason", self.ed_reason)
        self.chk_zone = QCheckBox("Only for this kind of zone")
        self.chk_zone.setChecked(True)
        self.chk_zone.setToolTip("Untick to excuse the same check on this feature for every zone layer")
        form.addRow(self.chk_zone)
        self.chk_expires = QCheckBox("Expires")
        self.dt_expires = QDateEdit(QDate.currentDate().addYears(1))
        self.dt_expires.setCalendarPopup(True)
        self.dt_expires.setEnabled(False)
        self.chk_expires.toggled.connect(self.dt_expires.setEnabled)
        row = QHBoxLayout()
        row.addWidget(self.chk_expires)
        row.addWidget(self.dt_expires, 1)
        form.addRow(row)
        lay.addLayout(form)
        lay.addWidget(hint("If the feature is edited later, the result shows again as "
                           "\"excused before the last edit\"."))
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Ok).setText("Accept")
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.resize(480, 200)

    def accept(self):
        if not self.ed_reason.text().strip():
            QMessageBox.warning(self, "Accept as exception", "Give a reason.")
            return
        super().accept()

    def reason(self) -> str:
        return self.ed_reason.text().strip()

    def this_zone_only(self) -> bool:
        return self.chk_zone.isChecked()

    def expires(self):
        if not self.chk_expires.isChecked():
            return None
        d = self.dt_expires.date()
        return datetime(d.year(), d.month(), d.day(), 23, 59, 59)
