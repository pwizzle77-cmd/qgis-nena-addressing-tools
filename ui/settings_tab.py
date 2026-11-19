# -*- coding: utf-8 -*-
"""
Settings: everything you set once, in one window.

A list of pages on the left, like QGIS's own Options. The tool windows keep
only what you do (buttons, lists, results); each has a Settings… button that
opens its page here. The setting groups still belong to the tab that built
them, so loading and saving work as before.
"""

from qgis.PyQt.QtWidgets import QHBoxLayout, QListWidget, QListWidgetItem, QStackedWidget, QWidget


class SettingsTab(QWidget):
    def __init__(self, pages, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setObjectName("NenaSettingsPages")
        self.list.setMaximumWidth(150)
        self.list.setMinimumWidth(110)
        self.stack = QStackedWidget()
        self.titles = []
        for title, widget, tip in pages:
            item = QListWidgetItem(title)
            item.setToolTip(tip)
            self.list.addItem(item)
            self.stack.addWidget(widget)
            self.titles.append(title)
        self.list.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.list.setCurrentRow(0)
        lay.addWidget(self.list)
        lay.addWidget(self.stack, 1)

    def show_page(self, title) -> bool:
        """Turn to the page called ``title``. False when there is none."""
        if title not in self.titles:
            return False
        self.list.setCurrentRow(self.titles.index(title))
        return True

    def page_title(self) -> str:
        row = self.list.currentRow()
        return self.titles[row] if 0 <= row < len(self.titles) else ""
