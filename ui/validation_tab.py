# -*- coding: utf-8 -*-
"""Validation options and results."""

from qgis.gui import QgsFieldComboBox, QgsMapLayerComboBox
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QBrush
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QFormLayout, QGridLayout, QGroupBox,
                                 QHBoxLayout, QHeaderView, QLabel, QPushButton, QSpinBox, QTabWidget,
                                 QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import config as C
from ..core import rules as V
from ..core.project_config import MsagSettings, load_project_config, resolve_layer, save_project_config
from .widgets import BoundedTable, combo, hint, page_into, set_combo_data

try:
    from qgis.core import Qgis
    _VECTOR = Qgis.LayerFilter.VectorLayer
except AttributeError:  # pragma: no cover
    from qgis.core import QgsMapLayerProxyModel
    _VECTOR = QgsMapLayerProxyModel.VectorLayer

CHECKS = [
    ("check_mandatory", "Mandatory fields"),
    ("check_parity", "Parity"),
    ("check_ranges", "Range sanity"),
    ("check_overlaps", "Overlaps"),
    ("check_gaps", "Gaps between segments"),
    ("check_direction", "Digitizing direction"),
    ("check_domains", "Domains (dir., OneWay…)"),
    ("check_nguid", "NGUID format / duplicates"),
    ("check_geometry", "Geometry"),
    ("check_names", "Names vs master list"),
]
SEV_COLORS = {V.ERROR: QColor(231, 76, 60, 60), V.WARNING: QColor(243, 156, 18, 60), V.INFO: QColor(52, 152, 219, 40)}


class ValidationTab(QWidget):
    runRequested = pyqtSignal(bool)          # selected only
    selectRequested = pyqtSignal(list)       # fids
    findOverlapsRequested = pyqtSignal(bool) # selected only – run the overlap check alone and select
    zoomRequested = pyqtSignal(list)
    exportCsvRequested = pyqtSignal()
    loadLayerRequested = pyqtSignal()
    compareMsagRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.issues = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.pages = QTabWidget()
        self.pages.setDocumentMode(True)
        self.pages.setUsesScrollButtons(True)
        self.pages.tabBar().setUsesScrollButtons(True)
        outer.addWidget(self.pages)

        results = QWidget()
        lay = page_into(results)
        row = QHBoxLayout()
        b1 = QPushButton("Validate selected")
        b2 = QPushButton("Validate layer")
        b1.setToolTip("Check the selected centerlines")
        b2.setToolTip("Check every centerline")
        b1.clicked.connect(lambda *_: self.runRequested.emit(True))
        b2.clicked.connect(lambda *_: self.runRequested.emit(False))
        row.addWidget(b1)
        row.addWidget(b2)
        lay.addLayout(row)
        b3 = QPushButton("Find overlaps")
        b3.setToolTip("Run only the overlap check and select every feature involved")
        b3.clicked.connect(lambda *_: self.findOverlapsRequested.emit(False))
        lay.addWidget(b3)

        top = QHBoxLayout()
        self.lbl_summary = QLabel("Not run yet.")
        self.lbl_summary.setWordWrap(True)
        top.addWidget(self.lbl_summary, 1)
        self.cb_filter = combo([("All", ""), ("Errors", V.ERROR), ("Warnings", V.WARNING), ("Info", V.INFO)])
        self.cb_filter.currentIndexChanged.connect(self._fill)
        top.addWidget(self.cb_filter)
        lay.addLayout(top)

        self.table = BoundedTable(0, 5, min_height=120, max_height=280)
        self.table.setHorizontalHeaderLabels(["Severity", "FID", "Rule", "Side", "Message"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)
        hh = self.table.horizontalHeader()
        for c in range(4):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.itemDoubleClicked.connect(lambda *_: self.zoomRequested.emit(self._selected_fids()))
        lay.addWidget(self.table, 1)

        grid = QGridLayout()
        for i, (text, tip, slot) in enumerate((
                ("Select issues", "Select the features listed", lambda *_: self.selectRequested.emit(self._all_fids())),
                ("Select overlaps", "Select features with overlapping ranges",
                 lambda *_: self.selectRequested.emit(V.overlap_fids(self.issues))),
                ("Zoom", "Zoom to the selected rows", lambda *_: self.zoomRequested.emit(self._selected_fids())),
                ("Export CSV…", "Save the list", lambda *_: self.exportCsvRequested.emit()),
                ("Load as layer", "Add the list to the project", lambda *_: self.loadLayerRequested.emit()))):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid)
        lay.addStretch(1)
        self._results_page = results
        self.pages.addTab(results, "Results")

        checks = QWidget()
        clay = page_into(checks)
        g = QGroupBox("What to check")
        grid = QGridLayout(g)
        self.chk = {}
        for i, (key, label) in enumerate(CHECKS):
            cb = QCheckBox(label)
            self.chk[key] = cb
            grid.addWidget(cb, i // 2, i % 2)
        form = QFormLayout()
        self.sp_gap = QSpinBox()
        self.sp_gap.setRange(0, 100000)
        self.sp_gap.setToolTip("Numbers that may be skipped between consecutive segments before a gap is reported")
        self.chk_decreasing = QCheckBox("Accept decreasing ranges (FROM > TO)")
        self.cb_comm = combo([("Street only", ""), ("Street + MSAG community", "MSAGComm"),
                              ("Street + incorporated municipality", "IncMuni"),
                              ("Street + postal community", "PostComm"), ("Street + ESN", "ESN")])
        form.addRow("Gap tolerance", self.sp_gap)
        form.addRow("Overlap scope", self.cb_comm)
        form.addRow(self.chk_decreasing)
        self.chk_select_overlaps = QCheckBox("Select overlapping ranges after a check")
        form.addRow(self.chk_select_overlaps)
        clay.addWidget(g)
        clay.addLayout(form)
        clay.addWidget(hint("Saved with the centerline automatically."))
        clay.addStretch(1)
        self.pages.addTab(checks, "Checks")

        msag = QWidget()
        mlay = page_into(msag)
        g = QGroupBox("MSAG extract")
        form = QFormLayout(g)
        form.addRow(hint("Compare each side of a road to the telephone company's street, community, "
                         "low–high, and odd/even. Load the extract into the project first."))
        self.cb_msag = QgsMapLayerComboBox()
        self.cb_msag.setFilters(_VECTOR)
        self.cb_msag.setAllowEmptyLayer(True)
        self.fc_street = QgsFieldComboBox()
        self.fc_comm = QgsFieldComboBox()
        self.fc_low = QgsFieldComboBox()
        self.fc_high = QgsFieldComboBox()
        self.fc_parity = QgsFieldComboBox()
        self._msag_fields = (self.fc_street, self.fc_comm, self.fc_low, self.fc_high, self.fc_parity)
        self.cb_msag.layerChanged.connect(self._msag_layer_changed)
        form.addRow("MSAG layer", self.cb_msag)
        form.addRow("Street", self.fc_street)
        form.addRow("Community", self.fc_comm)
        form.addRow("Low", self.fc_low)
        form.addRow("High", self.fc_high)
        form.addRow("Odd/even", self.fc_parity)
        b = QPushButton("Compare to MSAG")
        b.setToolTip("List streets, communities, and ranges that do not match the centerline sides")
        b.clicked.connect(lambda *_: self.compareMsagRequested.emit())
        form.addRow(b)
        mlay.addWidget(g)
        mlay.addStretch(1)
        self.pages.addTab(msag, "MSAG")

    # ------------------------------------------------------------------
    def load(self, cfg: C.PluginConfig, msag: MsagSettings = None):
        v = cfg.validation
        for key, cb in self.chk.items():
            cb.setChecked(bool(getattr(v, key)))
        self.sp_gap.setValue(v.gap_tolerance)
        self.chk_decreasing.setChecked(v.allow_decreasing)
        set_combo_data(self.cb_comm, v.overlap_community_field)
        self.chk_select_overlaps.setChecked(v.select_overlaps)
        if msag is not None:
            self.load_msag(msag)

    def _msag_layer_changed(self, layer):
        for box in self._msag_fields:
            box.setLayer(layer)

    def load_msag(self, msag: MsagSettings):
        lyr = resolve_layer(msag.layer_id, msag.layer_source)
        self.cb_msag.blockSignals(True)
        self.cb_msag.setLayer(lyr)
        self.cb_msag.blockSignals(False)
        self._msag_layer_changed(lyr)
        for box, name in zip(self._msag_fields, (msag.street_field, msag.community_field, msag.low_field,
                                                 msag.high_field, msag.parity_field)):
            if name:
                box.setField(name)

    def store_msag(self, msag: MsagSettings):
        lyr = self.cb_msag.currentLayer()
        msag.layer_id = lyr.id() if lyr else ""
        msag.layer_source = lyr.source() if lyr else ""
        msag.street_field = self.fc_street.currentField() or ""
        msag.community_field = self.fc_comm.currentField() or ""
        msag.low_field = self.fc_low.currentField() or ""
        msag.high_field = self.fc_high.currentField() or ""
        msag.parity_field = self.fc_parity.currentField() or ""

    def write_msag(self):
        """Save the MSAG field picks into the project without touching the other settings."""
        pcfg = load_project_config()
        self.store_msag(pcfg.msag)
        save_project_config(pcfg)

    def store(self, cfg: C.PluginConfig):
        v = cfg.validation
        for key, cb in self.chk.items():
            setattr(v, key, cb.isChecked())
        v.gap_tolerance = self.sp_gap.value()
        v.allow_decreasing = self.chk_decreasing.isChecked()
        v.overlap_community_field = self.cb_comm.currentData()
        v.select_overlaps = self.chk_select_overlaps.isChecked()

    def set_issues(self, issues):
        self.issues = list(issues)
        self.pages.setCurrentWidget(self._results_page)
        counts = {s: sum(1 for i in self.issues if i.severity == s) for s in (V.ERROR, V.WARNING, V.INFO)}
        feats = len({i.fid for i in self.issues if i.fid is not None})
        if not self.issues:
            self.lbl_summary.setText("<b style='color:#27ae60'>No issues found.</b>")
        else:
            n_ov = len(V.overlap_fids(self.issues))
            self.lbl_summary.setText(f"<b>{counts[V.ERROR]}</b> errors, <b>{counts[V.WARNING]}</b> warnings, "
                                     f"{counts[V.INFO]} info on {feats} feature(s)"
                                     + (f" – <b>{n_ov}</b> feature(s) with overlapping ranges" if n_ov else ""))
        self._fill()

    def _visible(self):
        sev = self.cb_filter.currentData()
        return [i for i in self.issues if not sev or i.severity == sev]

    def _fill(self, *args):
        rows = self._visible()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for r, issue in enumerate(rows):
            vals = [issue.severity, "" if issue.fid is None else issue.fid, issue.code, issue.side, issue.message]
            for c, val in enumerate(vals):
                it = QTableWidgetItem()
                if c == 1 and val != "":
                    it.setData(Qt.ItemDataRole.DisplayRole, int(val))
                else:
                    it.setText(str(val))
                it.setData(Qt.ItemDataRole.UserRole, issue.fid)
                if c == 0:
                    it.setBackground(QBrush(SEV_COLORS.get(issue.severity, QColor(0, 0, 0, 0))))
                if c == 4:
                    it.setToolTip(issue.message)
                self.table.setItem(r, c, it)
        self.table.setSortingEnabled(True)

    def _selected_fids(self):
        fids = []
        for idx in self.table.selectionModel().selectedRows():
            fid = self.table.item(idx.row(), 0).data(Qt.ItemDataRole.UserRole)
            if fid is not None and fid not in fids:
                fids.append(fid)
        return fids

    def _all_fids(self):
        """Features in the visible issues, including the 'other' feature of overlaps/gaps."""
        return V.involved_fids(self._visible())
