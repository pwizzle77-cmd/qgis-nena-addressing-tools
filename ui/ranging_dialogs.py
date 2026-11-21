# -*- coding: utf-8 -*-
"""Dialogs for the ranging / re-ranging tools."""

from qgis.PyQt.QtWidgets import QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout

from .ranging_tab import METHOD_HELP, METHOD_ITEMS
from .widgets import NormalDialog, combo, hint, set_combo_data


class RedistributeMethodDialog(NormalDialog):
    """Pick the ranging method before redistributing the selected segments' range."""

    def __init__(self, parent, default_method):
        super().__init__(parent)
        self.setWindowTitle("Redistribute ranges")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Spread the combined range of the selected segments by their length, "
                             "using:"))
        form = QFormLayout()
        self.cb_method = combo(METHOD_ITEMS)
        set_combo_data(self.cb_method, default_method)
        self.cb_method.currentIndexChanged.connect(self._update_help)
        form.addRow("Method", self.cb_method)
        lay.addLayout(form)
        self.lbl_help = hint("")
        lay.addWidget(self.lbl_help)
        self._update_help()
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Redistribute")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _update_help(self, *_):
        self.lbl_help.setText(METHOD_HELP.get(self.cb_method.currentData(), ""))

    def method(self) -> str:
        return self.cb_method.currentData()
