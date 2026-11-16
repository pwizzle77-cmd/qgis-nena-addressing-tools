# -*- coding: utf-8 -*-
"""Dialogs: confirm a new address, re-address after a partial rename, database integrity SQL."""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QGuiApplication
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                                 QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                                 QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..core.addressing.ssap import PLACEMENT_VALUES
from .widgets import hint


class AddressDialog(QDialog):
    """Review the calculated address before it is written."""

    def __init__(self, parent, plan, placement="Structure", driveway_available=True, create_driveway=False):
        super().__init__(parent)
        self.setWindowTitle("New address")
        self.plan = plan
        lay = QVBoxLayout(self)
        res = plan.result
        side = "left" if plan.side == "L" else "right"
        lay.addWidget(QLabel(f"<b>{res.label or '?'} {plan.full_name}</b>"))
        lay.addWidget(hint(f"{side} side ({'odd' if plan.parity == 'O' else 'even' if plan.parity == 'E' else 'any'})"
                           f", {plan.distance_m:.0f} m from the start of the street"
                           + (f" (raw {res.raw:.1f})" if res.raw is not None else "")))
        form = QFormLayout()
        row = QHBoxLayout()
        self.sp_number = QSpinBox()
        self.sp_number.setRange(0, 9999999)
        self.ed_suffix = QLineEdit(res.suffix)
        self.ed_suffix.setMaximumWidth(60)
        self.cb_choice = QComboBox()
        if res.needs_choice and res.options:
            self.cb_choice.addItems(res.options)
            self.cb_choice.currentTextChanged.connect(self._choice)
            self._choice(res.options[0])
        else:
            self.cb_choice.setVisible(False)
            self.sp_number.setValue(res.number or 0)
        row.addWidget(self.sp_number)
        row.addWidget(QLabel("suffix"))
        row.addWidget(self.ed_suffix)
        row.addWidget(self.cb_choice)
        form.addRow("Number", row)
        self.ed_unit = QLineEdit()
        form.addRow("Unit", self.ed_unit)
        self.cb_place = QComboBox()
        self.cb_place.addItems(PLACEMENT_VALUES)
        self.cb_place.setCurrentText(placement)
        form.addRow("Placement", self.cb_place)
        self.chk_dw = QCheckBox("Draw a straight driveway")
        self.chk_dw.setChecked(create_driveway and driveway_available)
        self.chk_dw.setEnabled(driveway_available)
        self.chk_dw.setToolTip("Off unless you held Shift or turned this on. Draw driveway is the tool for a real path."
                               if driveway_available else "Choose a driveway layer on the Addresses tab.")
        form.addRow(self.chk_dw)
        lay.addLayout(form)
        if plan.community:
            lay.addWidget(hint("Community fields: " + ", ".join(f"<b>{k}</b> {v}" for k, v in plan.community.items())))
        msgs = list(plan.messages) + list(plan.community_notes)
        if msgs:
            lay.addWidget(hint("<span style='color:#d35400'>" + "<br>".join(msgs) + "</span>"))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Create address")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _choice(self, text):
        digits = "".join(ch for ch in text if ch.isdigit())
        self.sp_number.setValue(int(digits or 0))
        self.ed_suffix.setText("".join(ch for ch in text if ch.isalpha()))

    def values(self):
        return dict(number=self.sp_number.value(), suffix=self.ed_suffix.text().strip().upper(),
                    unit=self.ed_unit.text().strip(), placement=self.cb_place.currentText(),
                    create_driveway=self.chk_dw.isChecked())


class ReaddressDialog(QDialog):
    """Part of a road was renamed: keep numbers, re-address all, or only the ticked addresses."""
    KEEP, ALL, TICKED = 0, 1, 2

    def __init__(self, parent, rows, new_name):
        super().__init__(parent)
        self.setWindowTitle("Renaming part of a road")
        self.resize(560, 420)
        self.rows = rows
        self.choice = self.KEEP
        lay = QVBoxLayout(self)
        lay.addWidget(hint(f"<b>{len(rows)}</b> address point(s) on the renamed segments now belong to "
                           f"<b>{new_name}</b>. Their names were updated. Should their numbers be recalculated "
                           "for the new street?"))
        self.table = QTableWidget(len(rows), 3)
        self.table.setHorizontalHeaderLabels(["Re-address", "Current", "New"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for r, (fid, old, plan) in enumerate(rows):
            chk = QTableWidgetItem()
            chk.setFlags(chk.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            chk.setCheckState(Qt.CheckState.Checked if plan.result.number is not None else Qt.CheckState.Unchecked)
            self.table.setItem(r, 0, chk)
            self.table.setItem(r, 1, QTableWidgetItem(f"{old} {new_name}"))
            new = plan.result.label or "(choose manually)"
            self.table.setItem(r, 2, QTableWidgetItem(f"{new} {plan.full_name}"
                                                      + (f"  – {plan.result.message}" if plan.result.message else "")))
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, choice in (("Keep numbers", self.KEEP), ("Re-address all", self.ALL),
                             ("Re-address ticked", self.TICKED)):
            b = QPushButton(text)
            b.clicked.connect(lambda *_, c=choice: self._done(c))
            row.addWidget(b)
        lay.addLayout(row)

    def _done(self, choice):
        self.choice = choice
        self.accept()

    def selected(self):
        """[(fid, plan)] to re-address according to the choice."""
        if self.choice == self.KEEP:
            return []
        out = []
        for r, (fid, _old, plan) in enumerate(self.rows):
            if plan.result.number is None:
                continue
            if self.choice == self.ALL or self.table.item(r, 0).checkState() == Qt.CheckState.Checked:
                out.append((fid, plan))
        return out


class SqlDialog(QDialog):
    """Show database-integrity SQL; copy it or run it on the database."""

    def __init__(self, parent, sql, can_run):
        super().__init__(parent)
        self.setWindowTitle("Database integrity (PostgreSQL)")
        self.resize(720, 520)
        self.run = False
        lay = QVBoxLayout(self)
        lay.addWidget(hint("This SQL makes the database enforce the street-name links for every client: a unique "
                           "StreetID, foreign keys, a trigger that cascades renames and one that copies name parts "
                           "when a StreetID is set. Review it, then copy it to your DBA or run it here (needs "
                           "ALTER/CREATE rights). Save pending QGIS edits first."))
        self.text = QPlainTextEdit(sql)
        self.text.setReadOnly(True)
        lay.addWidget(self.text, 1)
        row = QHBoxLayout()
        b = QPushButton("Copy")
        b.clicked.connect(lambda *_: QGuiApplication.clipboard().setText(sql))
        row.addWidget(b)
        b = QPushButton("Run on the database")
        b.setEnabled(can_run)
        b.clicked.connect(lambda *_: self._run())
        row.addWidget(b)
        row.addStretch(1)
        c = QPushButton("Close")
        c.clicked.connect(self.reject)
        row.addWidget(c)
        lay.addLayout(row)

    def _run(self):
        self.run = True
        self.accept()
