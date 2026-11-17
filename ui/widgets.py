# -*- coding: utf-8 -*-
"""Tiny Qt helpers shared by the dock tabs."""

import os

from qgis.PyQt.QtCore import QSize, Qt
from qgis.PyQt.QtGui import QColor, QIcon, QPainter, QPalette
from qgis.PyQt.QtWidgets import (QAbstractItemView, QAbstractScrollArea, QApplication, QComboBox, QDialog, QFrame,
                                 QLabel, QScrollArea, QSizePolicy, QTableWidget, QVBoxLayout, QWidget)

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
    """Muted wrapped label."""
    lbl = QLabel(text)
    lbl.setObjectName("nenaHint")
    lbl.setWordWrap(True)
    lbl.setTextFormat(Qt.TextFormat.RichText)
    font = lbl.font()
    size = font.pointSizeF()
    if size > 0:
        font.setPointSizeF(size * 0.9)
        lbl.setFont(font)
    pal = lbl.palette()
    mid = hint_color()
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        pal.setColor(group, QPalette.ColorRole.WindowText, mid)
        pal.setColor(group, QPalette.ColorRole.Text, mid)
    lbl.setPalette(pal)
    return lbl


# ---------------------------------------------------------------------------
# Normal window look
#
# QGIS themes and ArcSkin put a style sheet on the main window. Every window the
# plugin opens is a child of the main window, so it inherits that sheet, which
# leaves panels and scroll areas black. When a style sheet applies, Qt ignores
# palette fills, so painting with the palette alone cannot fix it. Each plugin
# window therefore gets its own small style sheet, scoped by object name, with
# the colours of a plain, unthemed window. A widget's own sheet beats the sheet
# it inherits from the main window.
# ---------------------------------------------------------------------------
_LIGHT = ((240, 240, 240), (255, 255, 255), (0, 0, 0))


def standard_colors():
    """(window, base, text) of a normal light window."""
    app = QApplication.instance()
    if app is None:
        return tuple(QColor(*rgb) for rgb in _LIGHT)
    pal = app.style().standardPalette()
    window = pal.color(QPalette.ColorRole.Window)
    base = pal.color(QPalette.ColorRole.Base)
    text = pal.color(QPalette.ColorRole.WindowText)
    if window.lightness() < 128 or abs(window.lightness() - text.lightness()) < 80:
        return tuple(QColor(*rgb) for rgb in _LIGHT)
    return window, base, text


def surface_color() -> QColor:
    """The normal window colour."""
    return standard_colors()[0]


def hint_color() -> QColor:
    window, _base, text = standard_colors()
    def mix(a, b):
        return max(0, min(255, (a + 2 * b) // 3 + 30))
    return QColor(mix(window.red(), text.red()), mix(window.green(), text.green()),
                  mix(window.blue(), text.blue()))


def normal_palette() -> QPalette:
    """A light palette that matches a plain window."""
    app = QApplication.instance()
    pal = QPalette(app.style().standardPalette()) if app is not None else QPalette()
    window, base, text = standard_colors()
    roles = (
        (QPalette.ColorRole.Window, window), (QPalette.ColorRole.Button, window),
        (QPalette.ColorRole.Base, base), (QPalette.ColorRole.AlternateBase, base.darker(104)),
        (QPalette.ColorRole.WindowText, text), (QPalette.ColorRole.Text, text),
        (QPalette.ColorRole.ButtonText, text), (QPalette.ColorRole.ToolTipText, text),
    )
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        for role, color in roles:
            pal.setColor(group, role, color)
    return pal


def window_style_sheet(object_name: str) -> str:
    """Style sheet that gives everything inside ``object_name`` the normal window colours."""
    window, base, text = (c.name() for c in standard_colors())
    line = standard_colors()[0].darker(130).name()
    muted = hint_color().name()
    s = f"#{object_name}"
    panels = ", ".join(f"{s} {sel}" for sel in (
        "QScrollArea", "QScrollArea > QWidget", "QScrollArea > QWidget > QWidget", "QStackedWidget",
        "QStackedWidget > QWidget", "QTabWidget > QWidget", "QSplitter", "QToolBox", "QToolBox > QWidget"))
    labels = ", ".join(f"{s} {sel}" for sel in ("QLabel", "QCheckBox", "QRadioButton"))
    inputs = ", ".join(f"{s} {sel}" for sel in (
        "QAbstractItemView", "QLineEdit", "QPlainTextEdit", "QTextEdit", "QAbstractSpinBox"))
    return f"""
{s}, {s} QDialog {{ background-color: {window}; color: {text}; }}
{panels} {{ background-color: {window}; color: {text}; }}
{labels} {{ background-color: transparent; color: {text}; }}
{s} QLabel#nenaHint {{ color: {muted}; }}
{s} QGroupBox {{ background-color: {window}; color: {text}; border: 1px solid {line};
    border-radius: 4px; margin-top: 1.1em; padding-top: 4px; }}
{s} QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 3px; color: {text}; }}
{s} QTabWidget::pane {{ background-color: {window}; border: 1px solid {line}; top: -1px; }}
{inputs} {{ background-color: {base}; color: {text}; }}
{s} QHeaderView::section {{ background-color: {window}; color: {text}; border: 0;
    border-right: 1px solid {line}; border-bottom: 1px solid {line}; padding: 3px 4px; }}
{s} QTableCornerButton::section {{ background-color: {window}; border: 0; }}
"""


def apply_normal_look(widget, object_name: str = "") -> None:
    """Give a top-level plugin window (or dock) the colours of a normal window."""
    name = object_name or widget.objectName()
    if not name:
        name = f"nena_{id(widget)}"
        widget.setObjectName(name)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    widget.setPalette(normal_palette())
    widget.setStyleSheet(window_style_sheet(name))


class NormalDialog(QDialog):
    """A dialog with the colours of a normal window, whatever theme the main window uses."""

    def showEvent(self, event):  # noqa: N802
        apply_normal_look(self, self.objectName() or f"Nena{type(self).__name__}")
        super().showEvent(event)


class Surface(QWidget):
    """A plain panel that always paints the normal window colour."""

    def __init__(self, parent=None, flags=Qt.WindowType.Widget):
        super().__init__(parent, flags)
        self.setAutoFillBackground(True)

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(event.rect(), surface_color())


def apply_panel_fill(root):
    """Kept for older callers: apply the normal window look to a top-level window."""
    apply_normal_look(root)


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
        # The style sheet leaves a scroll viewport black. This viewport paints the window color.
        self.setViewport(Surface(self))

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
    body = Surface()
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
