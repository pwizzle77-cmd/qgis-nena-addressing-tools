# -*- coding: utf-8 -*-
"""
One window per major tool, opened from the toolbar.

Each tool lives in a normal movable window. The window remembers its size and
place. With "Dock the NENA windows" on, the same pages open as tabs in the QGIS
side panel instead (beside the catalog). Both hosts get the normal window look,
so neither turns black under a QGIS theme or ArcSkin.
"""

import contextlib

from qgis.PyQt.QtCore import QByteArray, QObject, QSize, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import QDockWidget, QVBoxLayout

from .widgets import Surface, apply_normal_look

SETTINGS_ROOT = "NENA_Addressing/windows"
DOCKED_KEY = f"{SETTINGS_ROOT}/docked"

# Starting sizes. A window keeps whatever size you give it after that.
DEFAULT_SIZES = {
    "lookups": QSize(760, 640),
    "settings": QSize(700, 680),
    "schema": QSize(460, 420),
    "validation": QSize(560, 620),
    "streets": QSize(520, 660),
    "addresses": QSize(520, 660),
    "ranges": QSize(480, 600),
}


def _settings():
    from qgis.core import QgsSettings
    return QgsSettings()


def docked_preference() -> bool:
    try:
        return bool(_settings().value(DOCKED_KEY, False, type=bool))
    except Exception:  # noqa: BLE001
        return False


def _slot():
    """A container the page can be moved in and out of."""
    slot = Surface()
    lay = QVBoxLayout(slot)
    lay.setContentsMargins(0, 0, 0, 0)
    return slot


class ToolWindow(Surface):
    """A normal movable window that remembers its size and place."""

    visibilityChanged = pyqtSignal(bool)

    def __init__(self, object_name, title, parent=None, default_size=None):
        flags = (Qt.WindowType.Window | Qt.WindowType.WindowMinimizeButtonHint
                 | Qt.WindowType.WindowMaximizeButtonHint | Qt.WindowType.WindowCloseButtonHint)
        super().__init__(parent, flags)
        self.setObjectName(object_name)
        self.setWindowTitle(title)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self._default = default_size or QSize(480, 600)
        self._restored = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.slot = _slot()
        lay.addWidget(self.slot)
        apply_normal_look(self, object_name)

    def minimumSizeHint(self):
        return QSize(160, 100)

    def sizeHint(self):
        return self._default

    def _key(self):
        return f"{SETTINGS_ROOT}/{self.objectName()}/geometry"

    def save_geometry(self):
        with contextlib.suppress(Exception):  # a missing setting just means the default size next time
            _settings().setValue(self._key(), self.saveGeometry())

    def _restore_geometry(self):
        if self._restored:
            return
        self._restored = True
        try:
            value = _settings().value(self._key(), None)
        except Exception:  # noqa: BLE001
            value = None
        if isinstance(value, (QByteArray, bytes)) and value and self.restoreGeometry(QByteArray(value)):
            return
        self.resize(self._default)

    def closeEvent(self, event):  # noqa: N802
        event.ignore()
        self.hide()

    def showEvent(self, event):  # noqa: N802
        self._restore_geometry()
        super().showEvent(event)
        self.visibilityChanged.emit(True)

    def hideEvent(self, event):  # noqa: N802
        self.save_geometry()
        super().hideEvent(event)
        self.visibilityChanged.emit(False)


class ToolDock(QDockWidget):
    """The same page as a tab in the QGIS side panel."""

    def __init__(self, object_name, title, parent=None):
        super().__init__(title, parent)
        self.setObjectName(object_name)
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
                             | Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.TopDockWidgetArea)
        self.slot = _slot()
        self.setWidget(self.slot)
        apply_normal_look(self, object_name)
        self.added = False

    def minimumSizeHint(self):
        return QSize(160, 100)


class WindowHost(QObject):
    """Owns the tool windows (or docks). The dock panel still owns the pages and the save order."""

    def __init__(self, iface, dock, find_dock_host=None):
        super().__init__(dock)
        self.iface = iface
        self.dock = dock
        self.find_dock_host = find_dock_host
        self.windows = {}
        self.docks = {}
        self.bodies = {}
        self.titles = {}
        self._by_widget = {}
        self.on_visibility = None
        self.docked = docked_preference()
        main = iface.mainWindow()
        pages = (
            ("streets", "NenaStreetsWindow", "Streets", dock.streets, True),
            ("addresses", "NenaAddressesWindow", "Addresses", dock.addresses, True),
            ("ranges", "NenaRangesWindow", "Ranges", dock.ranging, True),
            ("validation", "NenaCheckWindow", "Check", dock.validation, True),
            ("settings", "NenaSettingsWindow", "Settings", dock.settings_root, False),
            ("schema", "NenaSchemaWindow", "Schema and import", dock.schema_page, True),
            ("lookups", "NenaLookupsWindow", "Lookup tables", dock.lookups, False),
        )
        for name, object_name, title, widget, layer_row in pages:
            body = dock.wrap_page(widget, layer_row)
            window = ToolWindow(object_name, title, main, DEFAULT_SIZES.get(name))
            window.hide()
            window.visibilityChanged.connect(self._visibility)
            self.windows[name] = window
            self.bodies[name] = body
            self.titles[name] = (object_name, title)
            self._by_widget[id(widget)] = name
            self._place(name)
            watch = getattr(dock, "watch_for_changes", None)
            if watch is not None:
                watch(body, exclude=(getattr(dock, "lookups", None),))
        dock.open_page = self.open_widget

    # -- hosts ---------------------------------------------------------------
    def _dock_for(self, name):
        found = self.docks.get(name)
        if found is None:
            object_name, title = self.titles[name]
            found = ToolDock(object_name + "Dock", f"NENA {title}", self.iface.mainWindow())
            found.visibilityChanged.connect(self._visibility)
            found.hide()
            self.docks[name] = found
        return found

    def _host(self, name):
        return self._dock_for(name) if self.docked else self.windows[name]

    def _place(self, name):
        host = self._host(name)
        body = self.bodies[name]
        if body.parent() is not host.slot:
            host.slot.layout().addWidget(body)
        body.show()

    def _add_dock(self, dock_widget):
        if dock_widget.added:
            return
        main = self.iface.mainWindow()
        anchor = self.find_dock_host(main, dock_widget) if self.find_dock_host else None
        area = Qt.DockWidgetArea.RightDockWidgetArea
        if anchor is not None and main.dockWidgetArea(anchor) != Qt.DockWidgetArea.NoDockWidgetArea:
            area = main.dockWidgetArea(anchor)
        self.iface.addDockWidget(area, dock_widget)
        if anchor is not None:
            main.tabifyDockWidget(anchor, dock_widget)
        dock_widget.added = True

    def set_docked(self, docked):
        """Move every page into side-panel docks, or back into separate windows."""
        docked = bool(docked)
        if docked == self.docked:
            return
        visible = [name for name in self.windows if self._host(name).isVisible()]
        for name in self.windows:
            self._host(name).hide()
        self.docked = docked
        with contextlib.suppress(Exception):
            _settings().setValue(DOCKED_KEY, docked)
        for name in self.windows:
            self._place(name)
        for name in visible:
            self.show(name)

    def _visibility(self, *_args):
        if not self.any_visible():
            autosave = getattr(self.dock, "autosave", None)
            if autosave is not None:
                autosave()
        if self.on_visibility is not None:
            self.on_visibility()

    # -- public --------------------------------------------------------------
    def show(self, name):
        if name not in self.windows:
            return
        self._place(name)
        host = self._host(name)
        if self.docked:
            self._add_dock(host)
            host.show()
            host.raise_()
            return
        host.show()
        host.raise_()
        host.activateWindow()

    def open_widget(self, widget):
        name = self._by_widget.get(id(widget))
        if name:
            self.show(name)

    def any_visible(self):
        return any(self._host(name).isVisible() for name in self.windows)

    def set_all_visible(self, visible):
        for name in self.windows:
            if visible:
                self.show(name)
            else:
                self._host(name).hide()

    def unload(self):
        for window in list(self.windows.values()):
            try:
                window.visibilityChanged.disconnect(self._visibility)
            except (TypeError, RuntimeError):
                pass
            if window.isVisible():
                window.save_geometry()
            window.hide()
            window.deleteLater()
        for dock_widget in list(self.docks.values()):
            try:
                dock_widget.visibilityChanged.disconnect(self._visibility)
            except (TypeError, RuntimeError):
                pass
            if dock_widget.added:
                try:
                    self.iface.removeDockWidget(dock_widget)
                except (TypeError, RuntimeError):
                    pass
            dock_widget.deleteLater()
        self.windows.clear()
        self.docks.clear()
        self.bodies.clear()
        self._by_widget.clear()
