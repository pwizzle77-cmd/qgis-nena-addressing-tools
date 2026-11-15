# -*- coding: utf-8 -*-
"""Auto-ranging options tab."""

from qgis.gui import QgsFieldComboBox, QgsMapLayerComboBox
from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtWidgets import (QCheckBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout,
                                 QLabel, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from ..compat import point_layer_filter
from ..core import config as C
from .widgets import PARITY_ITEMS, combo, hint, page_into, set_combo_data

METHOD_ITEMS = [
    ("Hundred-block (one block per segment)", C.M_HUNDRED),
    ("Interval (distance per address number)", C.M_INTERVAL),
    ("Proportional along length", C.M_PROPORTIONAL),
    ("From address points", C.M_POINTS),
]
METHOD_HELP = {
    C.M_HUNDRED: "Each segment of a street chain gets the next full block (e.g. 100–198 / 101–199, then 200–298 / "
                 "201–299 …). Classic intersection-to-intersection hundred-block addressing.",
    C.M_INTERVAL: "Addresses grow by one number every <i>interval distance</i> along the chain "
                  "(5.28 ft ≈ 1000 numbers per mile; 3.2 m ≈ 500 per km).",
    C.M_PROPORTIONAL: "A total range is spread over the chain in proportion to segment length. "
                      "Use the chain's existing extent to redistribute after edits, or a fixed range.",
    C.M_POINTS: "Ranges are derived from the house numbers of nearby address points (side determined "
                "geometrically). Segments without points keep their current values.",
}


class RangingTab(QWidget):
    rangeSelectedRequested = pyqtSignal()
    rangeAllRequested = pyqtSignal()
    fillGapsRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        pages = QTabWidget()
        pages.setDocumentMode(True)
        pages.setUsesScrollButtons(True)
        pages.tabBar().setUsesScrollButtons(True)
        run = QWidget()
        run_lay = page_into(run)
        method = QWidget()
        lay = page_into(method)
        pages.addTab(run, "Run")
        pages.setTabToolTip(0, "Range the roads, fill gaps, and split")
        pages.addTab(method, "Method")
        pages.setTabToolTip(1, "How numbers are assigned along a street")
        outer.addWidget(pages)

        run_lay.addWidget(hint("Uses the centerline at the top of the panel. Choose how numbers are assigned "
                               "on the Method tab."))
        self.lbl_method = QLabel("")
        self.lbl_method.setWordWrap(True)
        run_lay.addWidget(self.lbl_method)

        # -- method ------------------------------------------------------
        g = QGroupBox("Method")
        f = QFormLayout(g)
        self.cb_method = combo(METHOD_ITEMS)
        self.cb_method.currentIndexChanged.connect(self._update_visibility)
        f.addRow("Ranging method", self.cb_method)
        self.lbl_help = hint("")
        f.addRow(self.lbl_help)
        self.cb_scope = combo([("Street chains (connected, same name)", C.SCOPE_CHAIN),
                               ("Each feature independently", C.SCOPE_FEATURE)])
        f.addRow("Scope", self.cb_scope)
        self.cb_start = combo([("Continue from upstream segment", C.START_UPSTREAM),
                               ("Keep first segment's existing FROM", C.START_EXISTING),
                               ("Always start at base address", C.START_USER)])
        self.cb_start.setToolTip("Where numbering starts for each chain. Fallback order: upstream → existing → base.")
        f.addRow("Start numbering", self.cb_start)
        self.sp_base = QSpinBox()
        self.sp_base.setRange(0, 9999999)
        f.addRow("Base address", self.sp_base)
        self.sp_block = QSpinBox()
        self.sp_block.setRange(1, 1000000)
        f.addRow("Block size", self.sp_block)
        lay.addWidget(g)

        # -- parity ------------------------------------------------------
        g = QGroupBox("Parity (digitized direction = increasing addresses)")
        f = QFormLayout(g)
        self.cb_left = combo(PARITY_ITEMS)
        self.cb_right = combo(PARITY_ITEMS)
        f.addRow("Left side", self.cb_left)
        f.addRow("Right side", self.cb_right)
        self.chk_respect_parity = QCheckBox("Keep a valid existing Parity_L / Parity_R value (e.g. Z sides)")
        f.addRow(self.chk_respect_parity)
        lay.addWidget(g)

        # -- interval ----------------------------------------------------
        self.g_interval = QGroupBox("Interval")
        f = QFormLayout(self.g_interval)
        row = QHBoxLayout()
        self.sp_interval = QDoubleSpinBox()
        self.sp_interval.setDecimals(3)
        self.sp_interval.setRange(0.001, 100000)
        self.cb_units = combo([("feet", "feet"), ("US survey feet", "us_survey_feet"), ("meters", "meters"),
                               ("kilometers", "kilometers"), ("miles", "miles")])
        row.addWidget(self.sp_interval, 1)
        row.addWidget(self.cb_units)
        f.addRow("Distance per number", row)
        lay.addWidget(self.g_interval)

        # -- proportional ------------------------------------------------
        self.g_prop = QGroupBox("Proportional")
        f = QFormLayout(self.g_prop)
        self.cb_prop_src = combo([("Chain's existing min/max", C.PROP_EXISTING), ("Fixed range below", C.PROP_USER)])
        f.addRow("Range source", self.cb_prop_src)
        self.sp_prop_from = QSpinBox()
        self.sp_prop_from.setRange(0, 9999999)
        self.sp_prop_to = QSpinBox()
        self.sp_prop_to.setRange(0, 9999999)
        row = QHBoxLayout()
        row.addWidget(self.sp_prop_from)
        row.addWidget(self.sp_prop_to)
        f.addRow("From / To", row)
        self.chk_anchor = QCheckBox("Bend through address points (needs the point layer below)")
        self.chk_anchor.setToolTip("Existing address points act as anchors: each point's number stays on the "
                                   "segment it lies along, and the range is interpolated between points.")
        f.addRow(self.chk_anchor)
        lay.addWidget(self.g_prop)

        # -- address points ---------------------------------------------
        self.g_points = QGroupBox("Address points (also used for split re-ranging and anchoring)")
        f = QFormLayout(self.g_points)
        self.cb_point_layer = QgsMapLayerComboBox()
        self.cb_point_layer.setFilters(point_layer_filter())
        self.cb_point_layer.setAllowEmptyLayer(True)
        self.cb_point_number = QgsFieldComboBox()
        self.cb_point_street = QgsFieldComboBox()
        self.cb_point_street.setAllowEmptyFieldName(True)
        self.cb_point_layer.layerChanged.connect(self._point_layer_changed)
        f.addRow("Point layer", self.cb_point_layer)
        f.addRow("House number field", self.cb_point_number)
        f.addRow("Full street name field", self.cb_point_street)
        self.sp_search = QDoubleSpinBox()
        self.sp_search.setRange(1, 5000)
        self.sp_search.setSuffix(" m")
        f.addRow("Search distance", self.sp_search)
        self.chk_expand = QCheckBox("Expand observed numbers to the block (without touching neighbours)")
        self.chk_infer = QCheckBox("Infer parity from the points on each side")
        f.addRow(self.chk_expand)
        f.addRow(self.chk_infer)
        lay.addWidget(self.g_points)

        # -- output ------------------------------------------------------
        g = QGroupBox("Output")
        f = QFormLayout(g)
        self.chk_overwrite = QCheckBox("Overwrite existing ranges (off: only fill empty / 0-0 features)")
        self.chk_write_parity = QCheckBox("Write Parity_L / Parity_R")
        self.chk_stamp = QCheckBox("Set DateUpdate on changed features")
        self.sp_snap = QDoubleSpinBox()
        self.sp_snap.setDecimals(2)
        self.sp_snap.setRange(0.0, 100)
        self.sp_snap.setSuffix(" m")
        self.sp_snap.setToolTip("Endpoints closer than this are treated as connected")
        f.addRow(self.chk_overwrite)
        f.addRow(self.chk_write_parity)
        f.addRow(self.chk_stamp)
        f.addRow("Connectivity tolerance", self.sp_snap)
        lay.addWidget(g)

        lay.addStretch(1)

        btns = QHBoxLayout()
        self.btn_sel = QPushButton("Range selected")
        self.btn_all = QPushButton("Range layer")
        self.btn_sel.setToolTip("Assign ranges to the selected centerlines")
        self.btn_all.setToolTip("Assign ranges to every centerline")
        self.btn_sel.clicked.connect(lambda *_: self.rangeSelectedRequested.emit())
        self.btn_all.clicked.connect(lambda *_: self.rangeAllRequested.emit())
        btns.addWidget(self.btn_sel)
        btns.addWidget(self.btn_all)
        run_lay.addLayout(btns)
        b = QPushButton("Fill range gaps")
        b.setToolTip("Extend a segment so its numbers meet the next segment of the same street. "
                     "Selected segments are filled, or the whole layer when nothing is selected. "
                     "An overlap is left alone.")
        b.clicked.connect(lambda *_: self.fillGapsRequested.emit())
        run_lay.addWidget(b)
        run_lay.addWidget(hint("Each run is one undo step. Review the layer, then save or roll back."))
        self.manual_slot = QVBoxLayout()
        run_lay.addLayout(self.manual_slot)
        run_lay.addStretch(1)
        self._update_visibility()

    # ------------------------------------------------------------------
    def _point_layer_changed(self, layer):
        self.cb_point_number.setLayer(layer)
        self.cb_point_street.setLayer(layer)

    def _update_visibility(self, *args):
        m = self.cb_method.currentData()
        self.lbl_help.setText(METHOD_HELP.get(m, ""))
        label = (self.cb_method.currentText() or "").split(" (")[0]
        self.lbl_method.setText(f"Method: {label}" if label else "")
        self.g_interval.setVisible(m == C.M_INTERVAL)
        self.g_prop.setVisible(m == C.M_PROPORTIONAL)
        self.sp_block.setEnabled(m in (C.M_HUNDRED, C.M_POINTS))
        self.sp_base.setEnabled(m in (C.M_HUNDRED, C.M_INTERVAL))
        self.cb_start.setEnabled(m in (C.M_HUNDRED, C.M_INTERVAL))

    def load(self, cfg: C.PluginConfig):
        r = cfg.ranging
        set_combo_data(self.cb_method, r.method)
        set_combo_data(self.cb_scope, r.scope)
        set_combo_data(self.cb_start, r.start_mode)
        self.sp_base.setValue(r.base_address)
        self.sp_block.setValue(r.block_size)
        set_combo_data(self.cb_left, r.left_parity)
        set_combo_data(self.cb_right, r.right_parity)
        self.chk_respect_parity.setChecked(r.respect_existing_parity)
        self.sp_interval.setValue(r.interval_distance)
        set_combo_data(self.cb_units, r.interval_units)
        set_combo_data(self.cb_prop_src, r.proportional_source)
        self.sp_prop_from.setValue(r.proportional_from)
        self.sp_prop_to.setValue(r.proportional_to)
        self.chk_anchor.setChecked(r.proportional_anchor_points)
        from qgis.core import QgsProject
        pl = QgsProject.instance().mapLayer(r.point_layer_id) if r.point_layer_id else None
        self.cb_point_layer.setLayer(pl)
        self._point_layer_changed(pl)
        if r.point_number_field:
            self.cb_point_number.setField(r.point_number_field)
        self.cb_point_street.setField(r.point_street_field or "")
        self.sp_search.setValue(r.point_search_distance_m)
        self.chk_expand.setChecked(r.point_expand_to_block)
        self.chk_infer.setChecked(r.point_infer_parity)
        self.chk_overwrite.setChecked(r.overwrite_existing)
        self.chk_write_parity.setChecked(r.write_parity)
        self.chk_stamp.setChecked(r.stamp_date_update)
        self.sp_snap.setValue(r.snap_tolerance_m)
        self._update_visibility()

    def store(self, cfg: C.PluginConfig):
        r = cfg.ranging
        r.method = self.cb_method.currentData()
        r.scope = self.cb_scope.currentData()
        r.start_mode = self.cb_start.currentData()
        r.base_address = self.sp_base.value()
        r.block_size = self.sp_block.value()
        r.left_parity = self.cb_left.currentData()
        r.right_parity = self.cb_right.currentData()
        r.respect_existing_parity = self.chk_respect_parity.isChecked()
        r.interval_distance = self.sp_interval.value()
        r.interval_units = self.cb_units.currentData()
        r.proportional_source = self.cb_prop_src.currentData()
        r.proportional_from = self.sp_prop_from.value()
        r.proportional_to = self.sp_prop_to.value()
        r.proportional_anchor_points = self.chk_anchor.isChecked()
        pl = self.cb_point_layer.currentLayer()
        r.point_layer_id = pl.id() if pl else ""
        r.point_number_field = self.cb_point_number.currentField() or ""
        r.point_street_field = self.cb_point_street.currentField() or ""
        r.point_search_distance_m = self.sp_search.value()
        r.point_expand_to_block = self.chk_expand.isChecked()
        r.point_infer_parity = self.chk_infer.isChecked()
        r.overwrite_existing = self.chk_overwrite.isChecked()
        r.write_parity = self.chk_write_parity.isChecked()
        r.stamp_date_update = self.chk_stamp.isChecked()
        r.snap_tolerance_m = self.sp_snap.value()
