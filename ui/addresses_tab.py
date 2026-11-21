# -*- coding: utf-8 -*-
"""Addresses tab: layers, numbering, boundary attribution, tools, checks and reports."""

import os

from qgis.core import QgsMessageLog, QgsProject
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtWidgets import (QCheckBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
                                 QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMenu, QMessageBox, QPushButton,
                                 QSpinBox, QTableWidgetItem, QVBoxLayout, QWidget)

from ..compat import msg_level, point_layer_filter
from ..core import lookups as LK
from ..core import tables
from ..core.addressing import engine as E
from ..core.addressing.checks import address_issues, centerline_boundary_issues
from ..core.addressing.layers import create_layer
from ..core.addressing.reports import addresses_by_community_report, streets_report
from ..core.addressing.ssap import PLACEMENT_VALUES, SSAP_FIELDS
from ..core.layer_access import ConfigError
from ..core.project_config import layer_for, load_project_config, remember, save_project_config
from ..core.streets.service import StreetService
from ..core import template as T
from .street_dialogs import IssuesDialog
from .widgets import PARITY_ITEMS, BoundedTable, FittingScroll, combo, hint, set_combo_data

try:
    from qgis.core import Qgis
    _LINE = Qgis.LayerFilter.LineLayer
except AttributeError:  # pragma: no cover
    from qgis.core import QgsMapLayerProxyModel
    _LINE = QgsMapLayerProxyModel.LineLayer

METHODS = [("Defined distance (numbers per distance from the road start)", "distance"),
           ("Interpolate within the segment's range", "interpolate"),
           ("Fixed frontage interval", "frontage")]
UNITS = [("feet", "feet"), ("US survey feet", "us_survey_feet"), ("meters", "meters")]


class AddressesTab(QWidget):
    toolRequested = pyqtSignal()
    drivewayToolRequested = pyqtSignal()
    unitsToolRequested = pyqtSignal()
    configSaved = pyqtSignal()

    def __init__(self, dock, parent=None):
        super().__init__(parent)
        self.dock = dock
        self.pcfg = load_project_config()
        outer = QVBoxLayout(self)
        scroll = FittingScroll()
        body = QWidget()
        lay = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        row = QHBoxLayout()
        for text, slot, tip in (
                ("Place address", lambda: self.toolRequested.emit(),
                 "Click the road, then the building. An access point is saved 10 feet off the road."),
                ("Draw driveway", lambda: self.drivewayToolRequested.emit(),
                 "Click the access point, then each bend. Right-click or double-click to finish."),
                ("Place units", lambda: self.unitsToolRequested.emit(),
                 "One access click, then each rooftop. Right-click or Esc ends that access.")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda *_, s=slot: s())
            row.addWidget(b)
        lay.addLayout(row)
        row = QHBoxLayout()
        for text, slot, tip in (("Undo last", self.undo, "Remove the last address and the access point made with it"),
                                ("Check", self.check, "Range, side, order, duplicates, boundaries"),
                                ("Settings…", lambda: self.dock.open_settings("Address rules"),
                                 "The address layers, numbering, and what new addresses get")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda *_, s=slot: s())
            row.addWidget(b)
        lay.addLayout(row)

        # -- layers --------------------------------------------------------
        g = QGroupBox("Layers")
        f = QFormLayout(g)
        self.cb_addr = QgsMapLayerComboBox()
        self.cb_addr.setFilters(point_layer_filter())
        self.cb_addr.setAllowEmptyLayer(True)
        f.addRow("Address points", self._with_create(self.cb_addr, "address"))
        self.cb_dw = QgsMapLayerComboBox()
        self.cb_dw.setFilters(_LINE)
        self.cb_dw.setAllowEmptyLayer(True)
        f.addRow("Driveways", self._with_create(self.cb_dw, "driveway"))
        self.cb_access = QgsMapLayerComboBox()
        self.cb_access.setFilters(point_layer_filter())
        self.cb_access.setAllowEmptyLayer(True)
        f.addRow("Access points", self.cb_access)
        row = QHBoxLayout()
        self.lbl_tpl = QLabel()
        row.addWidget(self.lbl_tpl, 1)
        b = QPushButton("Template ▾")
        m = QMenu(b)
        a = m.addAction("Load a state template (GeoPackage, shapefile, JSON, CSV)…")
        a.triggered.connect(lambda *_: self.load_template())
        a = m.addAction("Use the built-in NENA schema")
        a.triggered.connect(lambda *_: self.reset_template())
        b.setMenu(m)
        row.addWidget(b)
        f.addRow("Address schema", row)
        self.layers_group = g                       # shown on Settings, Layers

        # -- numbering -----------------------------------------------------
        g = QGroupBox("Numbering")
        f = QFormLayout(g)
        self.cb_method = combo(METHODS)
        self.cb_method.currentIndexChanged.connect(lambda *_: self._vis())
        f.addRow("Method", self.cb_method)
        row = QHBoxLayout()
        self.sp_dist = QDoubleSpinBox()
        self.sp_dist.setDecimals(3)
        self.sp_dist.setRange(0.001, 100000)
        self.cb_units = combo(UNITS)
        row.addWidget(self.sp_dist, 1)
        row.addWidget(self.cb_units)
        self.lbl_dist = QLabel("Distance per number")
        f.addRow(self.lbl_dist, row)
        self.sp_start = QSpinBox()
        self.sp_start.setRange(0, 9999999)
        f.addRow("Number at the road start", self.sp_start)
        row = QHBoxLayout()
        self.sp_front = QDoubleSpinBox()
        self.sp_front.setRange(0.1, 100000)
        self.sp_step = QSpinBox()
        self.sp_step.setRange(1, 1000)
        row.addWidget(self.sp_front, 1)
        row.addWidget(QLabel("numbers per lot"))
        row.addWidget(self.sp_step)
        self.lbl_front = QLabel("Frontage interval")
        f.addRow(self.lbl_front, row)
        self.cb_round = combo([("Nearest", "nearest"), ("Round down", "down")])
        f.addRow("Rounding", self.cb_round)
        row = QHBoxLayout()
        self.cb_left = combo(PARITY_ITEMS[:3])
        self.cb_right = combo(PARITY_ITEMS[:3])
        row.addWidget(QLabel("Left"))
        row.addWidget(self.cb_left)
        row.addWidget(QLabel("Right"))
        row.addWidget(self.cb_right)
        f.addRow("Parity", row)
        self.chk_segpar = QCheckBox("Use the centerline's own Parity_L / Parity_R first (off: the parity above always wins)")
        self.chk_order = QCheckBox("Keep numbers increasing between neighbouring addresses")
        self.cb_coll = combo([("Use the next free number", "next"), ("Add a suffix (101A)", "suffix"),
                              ("Ask each time", "ask")])
        f.addRow(self.chk_segpar)
        f.addRow(self.chk_order)
        f.addRow("Number taken", self.cb_coll)
        self.numbering_group = g

        g = QGroupBox("Overrides per community")
        v = QVBoxLayout(g)
        row = QHBoxLayout()
        row.addWidget(QLabel("Keyed by"))
        self.cb_okey = combo([(k, k) for k in ("MSAGComm", "ESN", "PostCode", "IncMuni", "County")])
        row.addWidget(self.cb_okey)
        row.addStretch(1)
        v.addLayout(row)
        self.tbl_over = BoundedTable(0, 5, min_height=90, max_height=180)
        self.tbl_over.setHorizontalHeaderLabels(["Community value", "Method", "Distance/number", "Units",
                                                 "Start number"])
        self.tbl_over.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self.tbl_over)
        row = QHBoxLayout()
        for text, slot in (("Add override", self._add_override), ("Remove", lambda: self._remove(self.tbl_over))):
            b = QPushButton(text)
            b.clicked.connect(lambda *_, s=slot: s())
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        self.overrides_group = g

        g = QGroupBox("New addresses")
        f = QFormLayout(g)
        self.cb_place = combo([(p, p) for p in PLACEMENT_VALUES])
        f.addRow("Placement", self.cb_place)
        f.addRow(hint("Status, type and placement start on the value ticked <b>Default</b> in "
                      "<b>Lookup tables</b>. A value ticked there wins over Placement above."))
        self.chk_overlay = QCheckBox("Show where selected addresses were addressed on the road")
        f.addRow(self.chk_overlay)
        self.new_group = g                          # shown on Settings, Address rules

        g = QGroupBox("Empty fields")
        v = QVBoxLayout(g)
        b = QPushButton("Fill empty fields")
        b.setToolTip("Give the selected address points (all, when none are selected) the Default value of "
                     "each list where the field is empty: status, type, placement, agency. Filled fields stay.")
        b.clicked.connect(self.fill_empty)
        v.addWidget(b)
        lay.addWidget(g)

        g = QGroupBox("Access points")
        v = QVBoxLayout(g)
        v.addWidget(hint("Routing needs an access point for every address. Imported addresses have none: "
                         "this creates them 10 feet off the road, from the driveway start, Access_X / "
                         "Access_Y, or the nearest road, and links each address by AccessPointID."))
        b = QPushButton("Create access points…")
        b.setToolTip("Create access points for the selected address points (all, when none are selected) "
                     "that have none. Addresses already linked are left alone.")
        b.clicked.connect(self.create_access_points)
        v.addWidget(b)
        lay.addWidget(g)

        g = QGroupBox("ZIP, MSAG, and ESN")
        v = QVBoxLayout(g)
        v.addWidget(hint("Copies the side of the road onto the selected addresses. "
                         "Set the polygons under Settings, Sides."))
        b = QPushButton("Copy road side")
        b.setToolTip("Copy ZIP, MSAG community, ESN, and the other side fields from the centerline "
                     "onto the selected addresses. Each address gets the side it sits on.")
        b.clicked.connect(self.attribute_addresses)
        v.addWidget(b)
        lay.addWidget(g)

        row = QHBoxLayout()
        for text, tip, slot in (("Streets report…", "Counts of streets and address ranges", self.report_streets),
                                ("By community…", "Addresses grouped by community", self.report_communities)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda *_, s=slot: s())
            row.addWidget(b)
        lay.addLayout(row)
        lay.addStretch(1)
        self.load()

    # ------------------------------------------------------------------ widgets
    def _with_create(self, cb, kind):
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(cb, 1)
        b = QPushButton("Create ▾")
        m = QMenu(b)
        a = m.addAction("Create full NENA schema…")
        a.triggered.connect(lambda *_: self.dock.create_schema())
        a = m.addAction("Import into schema…")
        a.triggered.connect(lambda *_: self.dock.import_schema())
        a = m.addAction("Create version tables…")
        a.triggered.connect(lambda *_: self.dock.create_archive_schema())
        m.addSeparator()
        a = m.addAction("New layer in a GeoPackage…")
        a.triggered.connect(lambda *_: self.create(kind, "gpkg"))
        a = m.addAction("New table in a database connection…")
        a.triggered.connect(lambda *_: self.create(kind, "conn"))
        b.setMenu(m)
        row.addWidget(b)
        return w

    def _vis(self):
        m = self.cb_method.currentData()
        for w in (self.sp_dist, self.lbl_dist):
            w.setEnabled(m == "distance")
        for w in (self.sp_front, self.sp_step, self.lbl_front):
            w.setEnabled(m == "frontage")
        self.sp_start.setEnabled(m != "interpolate")

    def _remove(self, table):
        rows = sorted({i.row() for i in table.selectedIndexes()}, reverse=True) or \
            ([table.rowCount() - 1] if table.rowCount() else [])
        for r in rows:
            table.removeRow(r)

    def _add_override(self, data=None):
        data = data or {}
        r = self.tbl_over.rowCount()
        self.tbl_over.insertRow(r)
        self.tbl_over.setItem(r, 0, QTableWidgetItem(data.get("community", "")))
        cb = combo([("(default)", "")] + [(t.split(" (")[0], v) for t, v in METHODS])
        set_combo_data(cb, data.get("method", ""))
        self.tbl_over.setCellWidget(r, 1, cb)
        self.tbl_over.setItem(r, 2, QTableWidgetItem(str(data.get("distance_per_number", ""))))
        ucb = combo([("(default)", "")] + UNITS)
        set_combo_data(ucb, data.get("distance_units", ""))
        self.tbl_over.setCellWidget(r, 3, ucb)
        self.tbl_over.setItem(r, 4, QTableWidgetItem(str(data.get("start_number", ""))))

    # ------------------------------------------------------------------ settings
    def load(self):
        self.pcfg = load_project_config()
        s, a = self.pcfg.streets, self.pcfg.addresses
        self.cb_addr.setLayer(layer_for(s, "address"))
        self.cb_dw.setLayer(layer_for(a, "driveway"))
        self.cb_access.setLayer(layer_for(a, "access"))
        self._show_template()
        set_combo_data(self.cb_method, a.method)
        self.sp_dist.setValue(a.distance_per_number)
        set_combo_data(self.cb_units, a.distance_units)
        self.sp_start.setValue(a.start_number)
        self.sp_front.setValue(a.frontage_interval)
        self.sp_step.setValue(a.frontage_step)
        set_combo_data(self.cb_round, a.rounding)
        set_combo_data(self.cb_left, a.left_parity)
        set_combo_data(self.cb_right, a.right_parity)
        self.chk_segpar.setChecked(a.segment_parity_wins)
        self.chk_order.setChecked(a.order_check)
        set_combo_data(self.cb_coll, a.collision)
        set_combo_data(self.cb_okey, a.override_field)
        self.tbl_over.setRowCount(0)
        for comm, o in sorted(a.overrides.items()):
            self._add_override(dict(o, community=comm))
        lookup_place = ""
        try:
            lookup_place = LK.list_defaults(self.pcfg).get("Placement", "")
        except Exception:  # noqa: BLE001
            lookup_place = ""
        if lookup_place and self.cb_place.findData(lookup_place) < 0:
            self.cb_place.addItem(lookup_place, lookup_place)
        set_combo_data(self.cb_place, lookup_place or a.placement)
        self.cb_place.setEnabled(not lookup_place)
        self.cb_place.setToolTip("Set by the value ticked Default in Lookup tables" if lookup_place
                                 else "Placement method new addresses get")
        self.chk_overlay.setChecked(a.overlay)
        self.dock.mapping.load_rules(a.attribution_rules)
        self._vis()

    def _show_template(self):
        tpl = self.pcfg.addresses.address_template
        self.lbl_tpl.setText(f"State template: <b>{tpl.get('name')}</b>" if tpl and tpl.get("fields")
                             else "Built-in NENA site/structure address point schema")

    def store(self):
        self.pcfg = load_project_config()          # keep what the Streets tab saved
        s, a = self.pcfg.streets, self.pcfg.addresses
        remember(s, "address", self.cb_addr.currentLayer())
        remember(a, "driveway", self.cb_dw.currentLayer())
        remember(a, "access", self.cb_access.currentLayer())
        if self.dock.layer is not None:
            remember(s, "centerline", self.dock.layer)
        a.method = self.cb_method.currentData()
        a.distance_per_number = self.sp_dist.value()
        a.distance_units = self.cb_units.currentData()
        a.start_number = self.sp_start.value()
        a.frontage_interval = self.sp_front.value()
        a.frontage_step = self.sp_step.value()
        a.rounding = self.cb_round.currentData()
        a.left_parity = self.cb_left.currentData()
        a.right_parity = self.cb_right.currentData()
        a.segment_parity_wins = self.chk_segpar.isChecked()
        a.order_check = self.chk_order.isChecked()
        a.collision = self.cb_coll.currentData()
        a.override_field = self.cb_okey.currentData()
        a.overrides = {}
        for r in range(self.tbl_over.rowCount()):
            comm = (self.tbl_over.item(r, 0).text() if self.tbl_over.item(r, 0) else "").strip().upper()
            if not comm:
                continue
            o = {"method": self.tbl_over.cellWidget(r, 1).currentData(),
                 "distance_units": self.tbl_over.cellWidget(r, 3).currentData()}
            for col, key, cast in ((2, "distance_per_number", float), (4, "start_number", int)):
                txt = self.tbl_over.item(r, col).text().strip() if self.tbl_over.item(r, col) else ""
                if txt:
                    try:
                        o[key] = cast(txt)
                    except ValueError as err:
                        QgsMessageLog.logMessage(
                            f"addresses_tab: ignoring invalid {key} override {txt!r}: {err!r}",
                            "NENA Addressing and RCL Tools", msg_level("Info"))
            a.overrides[comm] = {k: v for k, v in o.items() if v not in ("", None)}
        if self.cb_place.isEnabled():
            a.placement = self.cb_place.currentData()
        a.create_driveway = False
        a.overlay = self.chk_overlay.isChecked()
        self.dock.mapping.store_rules(a)
        save_project_config(self.pcfg)
        self.configSaved.emit()
        return self.pcfg

    def save(self):
        self.dock._guard(lambda: (self.store(), self.dock._msg("Address settings saved in the project.", "Success")))

    def context(self) -> E.AddressContext:
        self.store()
        return E.AddressContext(self.pcfg, self.dock.layer, self.dock.collect())

    # ------------------------------------------------------------------ actions
    def create(self, kind, where):
        def do():
            label = "address points" if kind == "address" else "driveways"
            crs = (self.dock.layer.crs() if self.dock.layer is not None else QgsProject.instance().crs())
            if where == "gpkg":
                path, _ = QFileDialog.getSaveFileName(self, f"GeoPackage for the {label}", self.dock._last_dir(),
                                                      "GeoPackage (*.gpkg)")
                if not path:
                    return
                if not path.lower().endswith(".gpkg"):
                    path += ".gpkg"
                target = ("gpkg", path, "address_points" if kind == "address" else "driveways")
            else:
                conns = tables.db_connections()
                if not conns:
                    raise ConfigError("No database connections are configured in QGIS.")
                labels = [c[0] for c in conns]
                lab, ok = QInputDialog.getItem(self, label.title(), "Database connection:", labels, 0, False)
                if not ok:
                    return
                _l, key, name = conns[labels.index(lab)]
                schema, ok = QInputDialog.getText(self, label.title(), "Schema (empty for none):",
                                                  text="public" if key == "postgres" else "")
                if not ok:
                    return
                table, ok = QInputDialog.getText(self, label.title(), "Table name:",
                                                 text="address_points" if kind == "address" else "driveways")
                if not ok or not table.strip():
                    return
                target = ("conn", key, name, schema.strip(), table.strip())
            lyr = create_layer(kind, target, crs, self.pcfg.addresses.address_template or None)
            QgsProject.instance().addMapLayer(lyr)
            (self.cb_addr if kind == "address" else self.cb_dw).setLayer(lyr)
            self.store()
            self.dock._msg(f"{lyr.name()} created.", "Success")
        self.dock._guard(do)

    def load_template(self):
        path, _ = QFileDialog.getOpenFileName(self, "State address point template", self.dock._last_dir(),
                                              "Templates (*.gpkg *.shp *.geojson *.json *.csv *.sqlite);;All (*)")
        if not path:
            return

        def do():
            tpl = T.load_file(path, lambda names: names.index(QInputDialog.getItem(
                self, "Template", "Which layer holds the address points?", names, 0, False)[0]))
            self.pcfg = load_project_config()
            self.pcfg.addresses.address_template = tpl
            save_project_config(self.pcfg)
            self._show_template()
            info = T.summary(tpl, SSAP_FIELDS)
            self.dock._msg(f"State template '{tpl['name']}': {info['fields']} fields, {info['matched']} matched to "
                           f"NENA, {info['extra']} extra. New address layers use it.", "Success")
        self.dock._guard(do)

    def reset_template(self):
        self.pcfg = load_project_config()
        self.pcfg.addresses.address_template = {}
        save_project_config(self.pcfg)
        self._show_template()

    def undo(self):
        self.dock._guard(lambda: self.dock._msg(f"Removed {E.undo_last_address()} feature(s).", "Success"))

    def check(self):
        def do():
            ctx = self.context()
            issues = {"address": address_issues(ctx), "centerline": centerline_boundary_issues(ctx)}
            dlg = IssuesDialog(self, issues)
            layers = {"address": ctx.addr, "centerline": ctx.rcl}
            dlg.selectRequested.connect(lambda role, fids: layers[role].selectByIds(fids))
            dlg.exec()
        self.dock._guard(do)

    def fill_empty(self):
        def do():
            pcfg = self.store()
            ctx = self.context()
            LK.sync_defaults(pcfg, self.dock.layer)
            if not LK.field_defaults(pcfg, "address"):
                self.dock._msg("No address list has a Default ticked. Tick one in Lookup tables first.", "Info")
                return
            fids = ctx.addr.selectedFeatureIds() or None
            n = LK.fill_empty_from_lookups(pcfg, "address", ctx.addr, fids)
            self.dock._msg(f"Filled empty fields on {n} address point(s) from the lookup defaults.", "Success")
        self.dock._guard(do)

    def create_access_points(self):
        def do():
            from ..core.addressing import access_build as AB
            ctx = self.context()
            fids = ctx.addr.selectedFeatureIds() or None
            scope = f"the {len(fids)} selected address point(s)" if fids else "every address point"
            result = self.dock._with_progress(lambda cb: AB.plan_access_points(ctx, fids, cb))
            if not result.plans:
                self._select_skipped(ctx, result)
                self.dock._msg("No access points to create: " + result.summary()
                               + self._skip_note(result), "Info")
                return
            text = (f"Checked {scope}.\n\n{result.summary()}{self._skip_note(result)}\n\n"
                    "The new access points and the address links go into edit sessions on "
                    f"'{ctx.access.name()}' and '{ctx.addr.name()}' as one undo step each. "
                    "Save those layers to keep them.")
            if not ctx.addr_acc.has("AccessPointID"):
                text += f"\n\nAn AccessPointID field will be added to '{ctx.addr.name()}'."
            box = QMessageBox.question(self, "Create access points", text,
                                       QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            if box != QMessageBox.StandardButton.Ok:
                return
            AB.ensure_link_field(ctx)
            AB.apply_access_points(ctx, result, commit=False)
            self._select_skipped(ctx, result)
            note = " The skipped addresses are selected." if result.skipped else ""
            self.dock._msg(result.summary() + " Save the access point and address layers to keep them." + note,
                           "Success")
        self.dock._guard(do)

    @staticmethod
    def _skip_note(result) -> str:
        if not result.skipped:
            return ""
        reasons = {}
        for _fid, reason in result.skipped:
            reasons[reason] = reasons.get(reason, 0) + 1
        return " Skipped: " + "; ".join(f"{n} — {reason}" for reason, n in sorted(reasons.items())) + "."

    @staticmethod
    def _select_skipped(ctx, result):
        if result.skipped:
            ctx.addr.selectByIds([fid for fid, _reason in result.skipped])

    def attribute_addresses(self):
        def do():
            ctx = self.context()
            fids = ctx.addr.selectedFeatureIds() or None
            n, notes = E.attribute_addresses(ctx, fids)
            self.dock._msg(f"{n} address(es) copied from the side of the road"
                           + (f"; {len(notes)} note(s), e.g. {notes[0]}" if notes else "."), "Success")
        self.dock._guard(do)

    def attribute_sides(self):
        def do():
            ctx = self.context()
            fids = ctx.rcl.selectedFeatureIds() or None
            n, crossings = E.attribute_centerline_sides(ctx, fids)
            if crossings:
                ctx.rcl.selectByIds(sorted({c[0] for c in crossings}))
            self.dock._msg(f"Calculated the left and right side of {n} centerline(s)."
                           + (f" {len(crossings)} side(s) change partway along and are selected. "
                              "Split where the side changes to give each piece its own values."
                              if crossings else ""), "Success")
        self.dock._guard(do)

    def _report_path(self, name):
        path, _ = QFileDialog.getSaveFileName(self, "Save report", os.path.join(self.dock._last_dir(), name),
                                              "CSV (*.csv)")
        return path

    def report_streets(self):
        def do():
            path = self._report_path("streets_report.csv")
            if not path:
                return
            pcfg = self.store()
            svc = StreetService(pcfg, centerline_layer=self.dock.layer, centerline_cfg=self.dock.collect())
            try:
                ctx = E.AddressContext(pcfg, self.dock.layer, self.dock.collect())
            except ConfigError:
                ctx = None
            n = streets_report(path, svc, ctx)
            self.dock._msg(f"{n} street(s) written to {path}", "Success")
        self.dock._guard(do)

    def report_communities(self):
        def do():
            path = self._report_path("addresses_by_community.csv")
            if path:
                n = addresses_by_community_report(path, self.context())
                self.dock._msg(f"{n} address(es) summarised in {path}", "Success")
        self.dock._guard(do)
