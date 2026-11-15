# -*- coding: utf-8 -*-
"""Tiny Qt helpers shared by the dock tabs."""

import os

from qgis.PyQt.QtCore import QSize, Qt
from qgis.PyQt.QtGui import QIcon, QPalette
from qgis.PyQt.QtWidgets import (QAbstractItemView, QAbstractScrollArea, QComboBox, QFrame, QLabel,
                                 QScrollArea, QSizePolicy, QTableWidget, QVBoxLayout, QWidget)

PLUGIN_DIR = os.path.dirname(os.path.dirname(__file__))


def icon(name: str) -> QIcon:
    return QIcon(os.path.join(PLUGIN_DIR, "icons", name))


def combo(items, parent=None) -> QComboBox:
    """items: list of (label, data)."""
    cb = QComboBox(parent)
    for label, data in items:
        cb.addItem(label, data)
    return cb


def set_combo_data(cb: QComboBox, data) -> None:
    i = cb.findData(data)
    if i >= 0:
        cb.setCurrentIndex(i)


def hint(text: str) -> QLabel:
    """Muted wrapped label. A style sheet here paints the whole dock black in QGIS."""
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setTextFormat(Qt.TextFormat.RichText)
    font = lbl.font()
    size = font.pointSizeF()
    if size > 0:
        font.setPointSizeF(size * 0.9)
        lbl.setFont(font)
    pal = lbl.palette()
    mid = pal.color(QPalette.ColorRole.Mid)
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        pal.setColor(group, QPalette.ColorRole.WindowText, mid)
        pal.setColor(group, QPalette.ColorRole.Text, mid)
    lbl.setPalette(pal)
    return lbl


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFrameShadow(QFrame.Shadow.Sunken)
    return f


PARITY_ITEMS = [("Odd (O)", "O"), ("Even (E)", "E"), ("Both (B)", "B"), ("Zero / none (Z)", "Z")]


class FittingScroll(QScrollArea):
    """Scroll area that can shrink inside a QGIS dock or tab.

    A plain scroll area reports its contents as the size hint. The dock then
    stays as large as the form, QGIS clips it, and the scroll bars sit past
    the edge of the panel. This one asks only for a modest size, keeps both
    scroll bars on the visible edges, and still scrolls the full contents.
    """

    def __init__(self, parent=None, compact=False):
        super().__init__(parent)
        self._compact = compact
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff if compact else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed if compact else QSizePolicy.Policy.Expanding)
        # A styled viewport keeps a black fill after the dock palette is restored.
        self.viewport().setAutoFillBackground(False)

    def minimumSizeHint(self):
        if self._compact:
            return QSize(120, self._compact_height())
        return QSize(160, 80)

    def sizeHint(self):
        if self._compact:
            return QSize(320, self._compact_height())
        return QSize(380, 440)

    def _compact_height(self):
        widget = self.widget()
        base = widget.sizeHint().height() if widget is not None else 36
        bar = self.horizontalScrollBar().sizeHint().height()
        return max(36, base + bar + 2)


def page_into(owner: QWidget) -> QVBoxLayout:
    """Fill owner with a shrinkable scroll page and return the layout to build into."""
    outer = QVBoxLayout(owner)
    outer.setContentsMargins(0, 0, 0, 0)
    body = QWidget()
    lay = QVBoxLayout(body)
    scroll = FittingScroll(owner)
    scroll.setWidget(body)
    outer.addWidget(scroll)
    return lay


def host_groups(*groups) -> QWidget:
    """A scroll page that holds groups built on another tab."""
    page = QWidget()
    lay = page_into(page)
    for group in groups:
        lay.addWidget(group)
    lay.addStretch(1)
    return page


class BoundedTable(QTableWidget):
    """Table that scrolls instead of widening or lengthening the dock."""

    def __init__(self, rows, columns, parent=None, min_height=120, max_height=280):
        super().__init__(rows, columns, parent)
        self._floor = min_height
        self.setMinimumWidth(1)
        self.setMinimumHeight(min_height)
        if max_height:
            self.setMaximumHeight(max_height)
        self.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setWordWrap(False)

    def minimumSizeHint(self):
        return QSize(1, self._floor)

    def sizeHint(self):
        return QSize(320, self._floor)
