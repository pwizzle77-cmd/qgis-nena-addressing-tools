# -*- coding: utf-8 -*-
"""Settings tab: things you set once, each on its own page."""

from qgis.PyQt.QtWidgets import QTabWidget, QVBoxLayout, QWidget


class SettingsTab(QWidget):
    def __init__(self, pages, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.pages = QTabWidget()
        self.pages.setDocumentMode(True)
        self.pages.setUsesScrollButtons(True)
        self.pages.tabBar().setUsesScrollButtons(True)
        for title, widget, tip in pages:
            index = self.pages.addTab(widget, title)
            self.pages.setTabToolTip(index, tip)
        lay.addWidget(self.pages)
