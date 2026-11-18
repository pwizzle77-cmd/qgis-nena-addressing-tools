# -*- coding: utf-8 -*-
"""Field mapping tab: NENA attribute -> layer field, plus field templates."""

from qgis.gui import QgsFieldComboBox, QgsMapLayerComboBox
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QGridLayout,
                                 QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
                                 QPushButton, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.addressing.ssap import COMMUNITY_FIELDS, PRIMARY_BOUNDARIES
from ..core.config import PluginConfig
from ..core.project_config import resolve_layer
from ..core.schema import ONEWAY_DOMAIN, PARITY_DOMAIN, RCL_FIELDS, YES_NO_DOMAIN, MANDATORY, CONDITIONAL, auto_map
from ..core.template import effective_fields
from .widgets import BoundedTable, combo, hint, page_into, set_combo_data

try:
    from qgis.core import Qgis
    _POLY = Qgis.LayerFilter.PolygonLayer
except AttributeError:  # pragma: no cover
    from qgis.core import QgsMapLayerProxyModel
    _POLY = QgsMapLayerProxyModel.PolygonLayer

REQ_LABEL = {MANDATORY: "M", CONDITIONAL: "C"}


class MappingTab(QWidget):
    autoMapRequested = pyqtSignal()
    addFieldsRequested = pyqtSignal(bool)      # True = mandatory only
    saveRequested = pyqtSignal()
    importRequested = pyqtSignal()
    exportRequested = pyqtSignal()
    templateRequested = pyqtSignal()
    # field templates
    loadTemplateFileRequested = pyqtSignal()
    loadTemplateLayerRequested = pyqtSignal()
    resetTemplateRequested = pyqtSignal()
    exportTemplateRequested = pyqtSignal()
    calculateSidesRequested = pyqtSignal()
    splitSidesRequested = pyqtSignal()
    fillDefaultsRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layer_fields = []
        self._defs = list(RCL_FIELDS)
        self._combos = {}
        lay = page_into(self)

        lay.addWidget(hint("Match each NENA field to the centerline. <b>M</b> is mandatory, <b>C</b> is conditional. "
                           "On a database, Save writes the connection login into an empty <b>WhoEntered</b> "
                           "and into <b>LastUpdatedBy</b>."))

        trow = QHBoxLayout()
        self.lbl_template = QLabel("Template: built-in NENA-STA-006.2")
        self.lbl_template.setWordWrap(True)
        trow.addWidget(self.lbl_template, 1)
        self.btn_template = QPushButton("Field template ▾")
        menu = QMenu(self.btn_template)
        for text, sig in (("Load from file (GeoPackage, shapefile, GeoJSON, JSON, CSV)…",
                           self.loadTemplateFileRequested),
                          ("Use a layer in the project as template…", self.loadTemplateLayerRequested),
                          (None, None),
                          ("Export current template as JSON…", self.exportTemplateRequested),
                          ("Back to the built-in NENA schema", self.resetTemplateRequested)):
            if text is None:
                menu.addSeparator()
                continue
            act = menu.addAction(text)
            act.triggered.connect(lambda *_, s=sig: s.emit())
        self.btn_template.setMenu(menu)
        trow.addWidget(self.btn_template)
        lay.addLayout(trow)

        top = QHBoxLayout()
        self.chk_optional = QCheckBox("Show optional fields")
        self.chk_optional.setChecked(False)
        self.chk_optional.toggled.connect(self._apply_filter)
        top.addWidget(self.chk_optional)
        top.addStretch(1)
        self.lbl_status = QLabel("")
        top.addWidget(self.lbl_status)
        lay.addLayout(top)

        self.table = BoundedTable(0, 4, min_height=160, max_height=360)
        self.table.setHorizontalHeaderLabels(["NENA field", "Req", "Layer field", "Description"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(2, 140)
        lay.addWidget(self.table, 1)

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("Street key"))
        self.ed_key = QLineEdit()
        self.ed_key.setToolTip("NENA fields (comma separated) whose values together identify a street. "
                               "Segments are chained and overlap-checked per street key.")
        key_row.addWidget(self.ed_key, 1)
        lay.addLayout(key_row)

        grid = QGridLayout()
        buttons = [
            ("Auto-map", "Match layer fields by NENA name and common legacy names",
             (lambda *_: self.autoMapRequested.emit())),
            ("Add mandatory", "Create unmapped mandatory fields on the layer",
             lambda *_: self.addFieldsRequested.emit(True)),
            ("Add all fields", "Create every unmapped field on the layer",
             lambda *_: self.addFieldsRequested.emit(False)),
            ("Import…", "Load a configuration file", (lambda *_: self.importRequested.emit())),
            ("Export…", "Save the configuration to a file", (lambda *_: self.exportRequested.emit())),
            ("Empty layer", "Create an empty in-memory layer with this schema",
             (lambda *_: self.templateRequested.emit())),
        ]
        for i, (text, tip, slot) in enumerate(buttons):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid)

        g = QGroupBox("Left and right side of the road")
        v = QVBoxLayout(g)
        v.addWidget(hint("A road can sit between two ZIP codes, communities, or ESNs. "
                         "Pick the polygon for each, then calculate. A new address copies the side it is on."))
        form = QFormLayout()
        self._boundary = {}
        for key, label in PRIMARY_BOUNDARIES:
            lcb = QgsMapLayerComboBox()
            lcb.setFilters(_POLY)
            lcb.setAllowEmptyLayer(True)
            fcb = QgsFieldComboBox()
            lcb.layerChanged.connect(fcb.setLayer)
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(lcb, 2)
            h.addWidget(QLabel("Field"))
            h.addWidget(fcb, 1)
            form.addRow(label, row)
            self._boundary[key] = (lcb, fcb)
        v.addLayout(form)
        v.addWidget(hint("Postal community, municipality, and county use the same left and right fields."))
        self.tbl_rules = BoundedTable(0, 3, min_height=110, max_height=200)
        self.tbl_rules.setHorizontalHeaderLabels(["Polygon layer", "Copy field", "Into"])
        self.tbl_rules.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        v.addWidget(self.tbl_rules)
        grid = QGridLayout()
        for i, (text, tip, slot) in enumerate((
                ("Add boundary", "Add another polygon, such as municipality or county", lambda: self._add_rule({})),
                ("Remove", "Remove the selected extra boundary", self._remove_rule),
                ("Calculate sides", "Write the left and right values onto the centerlines",
                 lambda: self.calculateSidesRequested.emit()),
                ("Split where side changes", "Cut a road where the polygon changes and copy the new side onto addresses",
                 lambda: self.splitSidesRequested.emit()))):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda *_, s=slot: s())
            grid.addWidget(b, i // 2, i % 2)
        v.addLayout(grid)
        self.side_group = g

        g = QGroupBox("Values that rarely change")
        dv = QVBoxLayout(g)
        dv.addWidget(hint("Fills <b>empty</b> fields only. A value already on the feature stays. "
                          "Leave parity blank when ranging should set odd and even."))
        self._def_widgets = {}
        form = QFormLayout()
        for label, left, right, domain in (
                ("Country", "Country_L", "Country_R", None),
                ("State", "State_L", "State_R", None),
                ("County", "County_L", "County_R", None),
                ("Valid", "Valid_L", "Valid_R", YES_NO_DOMAIN)):
            form.addRow(label, self._pair_editors(left, right, domain))
        for label, name, domain in (
                ("One-way", "OneWay", ONEWAY_DOMAIN),
                ("Parity left", "Parity_L", PARITY_DOMAIN),
                ("Parity right", "Parity_R", PARITY_DOMAIN),
                ("Road status", "RoadStatus", None),
                ("Road class", "RoadClass", None),
                ("Discrepancy agency", "DiscrpAgID", None)):
            form.addRow(label, self._one_editor(name, domain))
        dv.addLayout(form)
        status = self._def_widgets.get("RoadStatus")
        if status is not None:
            status.setPlaceholderText("ACT")
            status.setToolTip("The road-status code new roads get (ACT = Active, PROP = Proposed, "
                              "PRIV = Private…; see Lookup tables, Road statuses)")
        dv.addWidget(hint("New roads get road status <b>ACT</b> (Active) and road class <b>Local</b> unless you "
                          "change them here. Clear a box to stop filling that field."))
        self.tbl_defaults = BoundedTable(0, 2, min_height=70, max_height=140)
        self.tbl_defaults.setHorizontalHeaderLabels(["Field", "Default"])
        self.tbl_defaults.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        dv.addWidget(self.tbl_defaults)
        row = QHBoxLayout()
        for text, slot in (("Add a field", lambda: self._add_extra("", "")),
                           ("Remove", self._remove_extra),
                           ("Fill empty fields", lambda: self.fillDefaultsRequested.emit())):
            b = QPushButton(text)
            b.clicked.connect(lambda *_, s=slot: s())
            row.addWidget(b)
        dv.addLayout(row)
        self.defaults_group = g
        self._build_rows()

    def _one_editor(self, name, domain):
        widget = self._domain_or_text(domain)
        self._def_widgets[name] = widget
        return widget

    def _pair_editors(self, left, right, domain):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        for caption, name in (("L", left), ("R", right)):
            h.addWidget(QLabel(caption))
            h.addWidget(self._one_editor(name, domain), 1)
        return row

    def _domain_or_text(self, domain):
        if domain:
            return combo([("(no default)", "")] + [(item, item) for item in domain])
        edit = QLineEdit()
        return edit

    def _widget_value(self, widget):
        if isinstance(widget, QComboBox):
            return widget.currentData() or ""
        return widget.text().strip()

    def _set_widget(self, widget, value):
        if isinstance(widget, QComboBox):
            set_combo_data(widget, value or "")
        else:
            widget.setText(value or "")

    def _extra_names(self, current=""):
        used = set(self._def_widgets)
        for r in range(self.tbl_defaults.rowCount()):
            box = self.tbl_defaults.cellWidget(r, 0)
            if box is not None and box.currentData():
                used.add(box.currentData())
        names = [fd.name for fd in self._defs if fd.name not in used]
        if current and current not in names:
            names.insert(0, current)
        return names

    def _add_extra(self, name, value):
        r = self.tbl_defaults.rowCount()
        self.tbl_defaults.insertRow(r)
        box = combo([(n, n) for n in self._extra_names(name)] or [("(choose)", "")])
        if name:
            set_combo_data(box, name)
        edit = QLineEdit(value or "")
        self.tbl_defaults.setCellWidget(r, 0, box)
        self.tbl_defaults.setCellWidget(r, 1, edit)

    def _remove_extra(self):
        rows = sorted({i.row() for i in self.tbl_defaults.selectedIndexes()}, reverse=True) or \
            ([self.tbl_defaults.rowCount() - 1] if self.tbl_defaults.rowCount() else [])
        for r in rows:
            self.tbl_defaults.removeRow(r)

    def _load_defaults(self, cfg):
        values = dict(cfg.defaults or {})
        for name, widget in self._def_widgets.items():
            self._set_widget(widget, values.pop(name, ""))
        self.tbl_defaults.setRowCount(0)
        for name, val in values.items():
            self._add_extra(name, val)

    def _store_defaults(self, cfg):
        out = {}
        for name, widget in self._def_widgets.items():
            val = self._widget_value(widget)
            if val:
                out[name] = val
        fixed = set(self._def_widgets)
        for r in range(self.tbl_defaults.rowCount()):
            box = self.tbl_defaults.cellWidget(r, 0)
            edit = self.tbl_defaults.cellWidget(r, 1)
            name = box.currentData() if box is not None else ""
            val = edit.text().strip() if edit is not None else ""
            if name and val and name not in fixed:
                out[name] = val
        cfg.defaults = out

    def _remove_rule(self):
        rows = sorted({i.row() for i in self.tbl_rules.selectedIndexes()}, reverse=True) or \
            ([self.tbl_rules.rowCount() - 1] if self.tbl_rules.rowCount() else [])
        for r in rows:
            self.tbl_rules.removeRow(r)

    def _add_rule(self, rule):
        r = self.tbl_rules.rowCount()
        self.tbl_rules.insertRow(r)
        lcb = QgsMapLayerComboBox()
        lcb.setFilters(_POLY)
        fcb = QgsFieldComboBox()
        lcb.layerChanged.connect(fcb.setLayer)
        lyr = resolve_layer(rule.get("layer_id", ""), rule.get("layer_source", "")) if rule else lcb.currentLayer()
        lcb.setLayer(lyr)
        fcb.setLayer(lyr)
        if rule.get("source_field"):
            fcb.setField(rule["source_field"])
        tcb = QComboBox()
        primary = {key for key, _label in PRIMARY_BOUNDARIES}
        for key, (afield, lf, rf) in COMMUNITY_FIELDS.items():
            if key in primary:
                continue
            tcb.addItem(f"{afield} / {lf}, {rf}", key)
        set_combo_data(tcb, rule.get("target") or "PostComm")
        self.tbl_rules.setCellWidget(r, 0, lcb)
        self.tbl_rules.setCellWidget(r, 1, fcb)
        self.tbl_rules.setCellWidget(r, 2, tcb)

    def load_rules(self, rules):
        """Show the saved polygon layers that fill the centerline's left and right fields."""
        self.tbl_rules.setRowCount(0)
        primary = {}
        for rule in rules or []:
            target = rule.get("target")
            if target in self._boundary and target not in primary:
                primary[target] = rule
            elif target not in self._boundary:
                self._add_rule(rule)
        for key, (lcb, fcb) in self._boundary.items():
            rule = primary.get(key) or {}
            lyr = resolve_layer(rule.get("layer_id", ""), rule.get("layer_source", "")) if rule else None
            lcb.setLayer(lyr)
            fcb.setLayer(lyr)
            if rule.get("source_field"):
                fcb.setField(rule["source_field"])

    def store_rules(self, addresses) -> None:
        """Write the polygon layers onto the project settings."""
        rules = []
        for key, (lcb, fcb) in self._boundary.items():
            lyr, fld = lcb.currentLayer(), fcb.currentField()
            if lyr is None or not fld:
                continue
            rules.append({"layer_id": lyr.id(), "layer_source": lyr.source(), "source_field": fld, "target": key})
        for r in range(self.tbl_rules.rowCount()):
            lyr = self.tbl_rules.cellWidget(r, 0).currentLayer()
            fld = self.tbl_rules.cellWidget(r, 1).currentField()
            target = self.tbl_rules.cellWidget(r, 2).currentData()
            if lyr is None or not fld or target in self._boundary:
                continue
            rules.append({"layer_id": lyr.id(), "layer_source": lyr.source(), "source_field": fld,
                          "target": target})
        addresses.attribution_rules = rules

    # ------------------------------------------------------------------ rows
    def _build_rows(self):
        current = {n: cb.currentData() for n, cb in self._combos.items()}
        self._combos = {}
        self.table.setRowCount(0)
        self.table.setRowCount(len(self._defs))
        for row, fd in enumerate(self._defs):
            label = fd.name if fd.physical_name == fd.name else f"{fd.name}  ({fd.physical_name})"
            it = QTableWidgetItem(label)
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, it)
            req = QTableWidgetItem(REQ_LABEL.get(fd.req, "O"))
            req.setFlags(req.flags() & ~Qt.ItemFlag.ItemIsEditable)
            req.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 1, req)
            cb = QComboBox()
            cb.addItem("— not mapped —", "")
            for n in self._layer_fields:
                cb.addItem(n, n)
            cb.setCurrentIndex(max(cb.findData(current.get(fd.name) or ""), 0))
            cb.currentIndexChanged.connect(self._update_status)
            self._combos[fd.name] = cb
            self.table.setCellWidget(row, 2, cb)
            d = QTableWidgetItem(fd.description)
            d.setFlags(d.flags() & ~Qt.ItemFlag.ItemIsEditable)
            d.setToolTip(fd.description)
            self.table.setItem(row, 3, d)
        self._apply_filter()
        self._update_status()

    def set_template(self, template: dict):
        """Rebuild the rows for a template (None/{} = built-in schema)."""
        self._defs = effective_fields(template)
        if template and template.get("fields"):
            src = template.get("source") or ""
            self.lbl_template.setText(f"Template: <b>{template.get('name', 'custom')}</b> "
                                      f"({len(template['fields'])} fields)")
            self.lbl_template.setToolTip(src)
        else:
            self.lbl_template.setText("Template: built-in NENA-STA-006.2")
            self.lbl_template.setToolTip("")
        self._build_rows()

    def set_layer_fields(self, names):
        self._layer_fields = list(names)
        self._build_rows()

    # ------------------------------------------------------------------ config
    def load(self, cfg: PluginConfig):
        self._defs = effective_fields(cfg.template)
        self.set_template(cfg.template)
        for name, cb in self._combos.items():
            cb.blockSignals(True)
            cb.setCurrentIndex(max(cb.findData(cfg.mapping.get(name, "")), 0))
            cb.blockSignals(False)
        self.ed_key.setText(", ".join(cfg.street_key_fields))
        self._load_defaults(cfg)
        self._update_status()

    def store(self, cfg: PluginConfig):
        cfg.mapping = {n: cb.currentData() for n, cb in self._combos.items() if cb.currentData()}
        keys = [k.strip() for k in self.ed_key.text().split(",") if k.strip()]
        if keys:
            cfg.street_key_fields = keys
        self._store_defaults(cfg)

    def apply_auto_map(self):
        suggestion = auto_map(self._layer_fields, self._defs)
        for name, field_name in suggestion.items():
            cb = self._combos.get(name)
            if cb is not None and not cb.currentData():
                cb.setCurrentIndex(max(cb.findData(field_name), 0))
        self._update_status()
        return len(suggestion)

    # ------------------------------------------------------------------
    def _apply_filter(self):
        show_opt = self.chk_optional.isChecked()
        for row, fd in enumerate(self._defs):
            self.table.setRowHidden(row, fd.req not in (MANDATORY, CONDITIONAL) and not show_opt)

    def _update_status(self, *args):
        missing = [fd.name for fd in self._defs
                   if fd.req == MANDATORY and not self._combos[fd.name].currentData()]
        if missing:
            self.lbl_status.setText(f"<span style='color:#c0392b'>{len(missing)} mandatory unmapped</span>")
            self.lbl_status.setToolTip(", ".join(missing))
        else:
            self.lbl_status.setText("<span style='color:#27ae60'>All mandatory fields mapped</span>")
            self.lbl_status.setToolTip("")
        for row, fd in enumerate(self._defs):
            item = self.table.item(row, 0)
            if item is None:
                continue
            font = item.font()
            font.setBold(fd.req == MANDATORY and not self._combos[fd.name].currentData())
            item.setFont(font)
