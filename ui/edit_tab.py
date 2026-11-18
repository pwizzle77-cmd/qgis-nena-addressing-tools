# -*- coding: utf-8 -*-
"""Split / merge / edit behaviour and NGUID tools."""

from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtWidgets import (QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QLineEdit, QPushButton,
                                 QVBoxLayout, QWidget)

from ..core import config as C
from .widgets import combo, hint, set_combo_data


class EditTab(QWidget):
    applyPoliciesRequested = pyqtSignal()
    redistributeRequested = pyqtSignal()
    splitToolRequested = pyqtSignal()
    splitExistingRequested = pyqtSignal()
    populateIdsRequested = pyqtSignal(bool)      # selected only
    settingsChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        g = QGroupBox("While you edit")
        f = QFormLayout(g)
        self.chk_split = QCheckBox("Re-range pieces after Split Features")
        self.chk_merge = QCheckBox("Combine ranges after Merge Selected Features")
        self.cb_split_method = combo([("Proportional, anchored to address points (recommended)", C.M_ANCHORED),
                                      ("Proportional to piece length only", C.M_PROPORTIONAL),
                                      ("Observed address points, proportional fallback", C.M_POINTS)])
        self.cb_split_method.setToolTip(
            "Anchored: the split boundary is interpolated between existing address points, so a point "
            "close to the split keeps its number on the piece it lies along. Without a point layer "
            "(Ranges, Method) this is plain proportional.")
        self.cb_geom = combo([("Do nothing", C.GEOM_NONE),
                              ("Recompute TO with interval method (keep FROM)", C.GEOM_INTERVAL),
                              ("Re-derive from address points", C.GEOM_POINTS)])
        self.chk_new_int = QCheckBox("Split existing roads where a new road creates an intersection")
        self.chk_new_int.setToolTip(
            "When you digitize a new road that ends on (or crosses) an existing centerline, the existing "
            "centerline is split there and its range is redistributed so the new section continues the "
            "numbering of the previous one. One undo step right after adding the road.")
        self.chk_crossings = QCheckBox("…also where the new road crosses an existing road (off for overpasses)")
        self.chk_new_int.toggled.connect(self.chk_crossings.setEnabled)
        self.chk_policies = QCheckBox("Manage field split policies (duplicate ranges, unset NGUID)")
        self.chk_nguid_new = QCheckBox("Give new and split-off features a new NGUID")
        self.chk_sides = QCheckBox("Fill ZIP, MSAG, ESN… sides on new and moved roads (Settings, Sides)")
        self.chk_sides.setToolTip(
            "When a road is added, split, or its shape changes, its left and right values are read from "
            "the polygon layers chosen under Settings, Sides.")
        for w in (self.chk_split, self.chk_merge, self.chk_policies, self.chk_nguid_new,
                  self.chk_new_int, self.chk_crossings, self.chk_sides):
            w.toggled.connect(lambda *_: self.settingsChanged.emit())
        self.cb_geom.currentIndexChanged.connect(lambda *_: self.settingsChanged.emit())
        self.cb_split_method.currentIndexChanged.connect(lambda *_: self.settingsChanged.emit())
        f.addRow(self.chk_split)
        f.addRow("Split method", self.cb_split_method)
        f.addRow(self.chk_merge)
        f.addRow(self.chk_new_int)
        f.addRow(self.chk_crossings)
        f.addRow("After reshape / vertex edit", self.cb_geom)
        f.addRow(self.chk_policies)
        f.addRow(self.chk_nguid_new)
        f.addRow(self.chk_sides)
        f.addRow(hint("Undo once restores the ranges. Undo again restores the split. "
                      "Select the roads before a merge so their ranges are known."))
        b = QPushButton("Apply split policies")
        b.setToolTip("Duplicate range and name values onto split pieces, and leave the NGUID empty")
        b.clicked.connect(lambda *_: self.applyPoliciesRequested.emit())
        f.addRow(b)
        self.auto_group = g

        g = QGroupBox("Split and redistribute")
        v = QVBoxLayout(g)
        b = QPushButton("Split && re-range")
        b.setToolTip("Click a centerline to split it there. Both pieces are re-ranged and the new piece "
                     "gets a new NGUID, in one undo step. Snapping is honored.")
        b.clicked.connect(lambda *_: self.splitToolRequested.emit())
        v.addWidget(b)
        b = QPushButton("Redistribute selected")
        b.setToolTip("Spread the combined range of the selected segments by their length.")
        b.clicked.connect(lambda *_: self.redistributeRequested.emit())
        v.addWidget(b)
        b = QPushButton("Split at intersections")
        b.setToolTip("Split selected roads where one meets another in the middle. End-to-end joins stay. "
                     "The whole layer is used when nothing is selected. Ranges are redistributed, and sides "
                     "are calculated again when the polygon layers are set.")
        b.clicked.connect(lambda *_: self.splitExistingRequested.emit())
        v.addWidget(b)
        self.manual_group = g

        g = QGroupBox("NGUID")
        f = QFormLayout(g)
        self.ed_agency = QLineEdit()
        self.ed_agency.setPlaceholderText("e.g. psap.example.gov")
        self.cb_format = combo([("urn:emergency:uid:gis:RCL:{id}:{agency} (STA-006.2)", "urn"),
                                ("RCL_{id}@{agency} (legacy STA-006.1)", "legacy")])
        self.cb_local = combo([("Random UUID (recommended)", "uuid"), ("Feature id", "fid")])
        f.addRow("Agency identifier", self.ed_agency)
        f.addRow("NGUID format", self.cb_format)
        f.addRow("Unique part", self.cb_local)
        row = QHBoxLayout()
        b1 = QPushButton("Fill empty – selected")
        b2 = QPushButton("Fill empty – all")
        b1.clicked.connect(lambda *_: self.populateIdsRequested.emit(True))
        b2.clicked.connect(lambda *_: self.populateIdsRequested.emit(False))
        row.addWidget(b1)
        row.addWidget(b2)
        f.addRow(row)
        f.addRow(hint("Fills empty RCL_NGUID values (and replaces ones that copy an older road's), and empty "
                      "DiscrpAgID with the agency identifier. Empty agency: the one the existing NGUIDs use."))
        self.nguid_group = g

    def load(self, cfg: C.PluginConfig):
        e = cfg.edit
        widgets = (self.chk_split, self.chk_merge, self.chk_policies, self.chk_nguid_new, self.cb_geom,
                   self.cb_split_method, self.chk_new_int, self.chk_crossings, self.chk_sides)
        for w in widgets:
            w.blockSignals(True)
        self.chk_split.setChecked(e.auto_rerange_split)
        self.chk_merge.setChecked(e.auto_merge)
        self.chk_new_int.setChecked(e.split_on_new_intersection)
        self.chk_crossings.setChecked(e.split_at_crossings)
        self.chk_crossings.setEnabled(e.split_on_new_intersection)
        set_combo_data(self.cb_split_method, e.split_method)
        set_combo_data(self.cb_geom, e.geometry_edit_action)
        self.chk_policies.setChecked(e.manage_split_policies)
        self.chk_nguid_new.setChecked(e.auto_nguid_new)
        self.chk_sides.setChecked(e.auto_sides)
        self.ed_agency.setText(e.agency_id)
        set_combo_data(self.cb_format, e.nguid_format)
        set_combo_data(self.cb_local, e.nguid_local_id)
        for w in widgets:
            w.blockSignals(False)

    def store(self, cfg: C.PluginConfig):
        e = cfg.edit
        e.auto_rerange_split = self.chk_split.isChecked()
        e.auto_merge = self.chk_merge.isChecked()
        e.split_on_new_intersection = self.chk_new_int.isChecked()
        e.split_at_crossings = self.chk_crossings.isChecked()
        e.split_method = self.cb_split_method.currentData()
        e.geometry_edit_action = self.cb_geom.currentData()
        e.manage_split_policies = self.chk_policies.isChecked()
        e.auto_nguid_new = self.chk_nguid_new.isChecked()
        e.auto_sides = self.chk_sides.isChecked()
        e.agency_id = self.ed_agency.text().strip()
        e.nguid_format = self.cb_format.currentData()
        e.nguid_local_id = self.cb_local.currentData()
