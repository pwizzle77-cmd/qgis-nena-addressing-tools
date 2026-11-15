# -*- coding: utf-8 -*-
"""
NENA Addressing Tools – QGIS plugin entry point (formerly "NENA RCL Tools").

Creates the toolbar, menu entries, dock panel, the Split & Re-range map tool
and the edit watchers that re-range centerlines after split / merge.
"""

from qgis.PyQt.QtCore import QSize, Qt, QTimer
from qgis.PyQt.QtWidgets import QSizePolicy, QWidget

from .compat import QAction, msg_level
from .core.archive_watch import ArchiveWatcher
from .core.editor_user import EditorUserWatcher
from .core.split_handler import WatcherManager
from .core.streets.sync import StreetWatcherManager
from .ui.dock import NenaDock, TITLE
from .ui.split_tool import SplitRerangeTool
from .ui.address_tool import AddressTool
from .ui.address_overlay import AddressOverlay
from .core.addressing import engine as AE
from .core.layer_access import ConfigError
from .ui.widgets import icon

MENU = "&NENA Addressing Tools"


class _ToolbarSpacer(QWidget):
    """Leading spacer so the tools sit at the right of a full-width toolbar row."""

    def __init__(self, parent=None):
        super().__init__(parent)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHorizontalStretch(1)
        self.setSizePolicy(policy)

    def sizeHint(self):
        return QSize(0, 0)

    def minimumSizeHint(self):
        return QSize(0, 0)


class NenaRclPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.actions = []
        self.toolbar = None
        self.dock = None
        self.watchers = None
        self.street_watchers = None
        self.editor_watchers = None
        self.archive_watchers = None
        self.split_tool = None
        self.address_tool = None
        self.overlay = None

    # ------------------------------------------------------------------
    def initGui(self):  # noqa: N802 - QGIS API name
        self.toolbar = self.iface.addToolBar(TITLE)
        self.toolbar.setObjectName("NenaRclToolsToolbar")   # kept so QGIS remembers the toolbar position
        self.toolbar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.toolbar.setMovable(True)
        self._toolbar_spacer = _ToolbarSpacer(self.toolbar)
        self.toolbar.addWidget(self._toolbar_spacer)

        self.watchers = WatcherManager(self._notify)
        self.street_watchers = StreetWatcherManager(self._notify)
        # Connect before the version watcher so a save records the login that was just stamped.
        self.editor_watchers = EditorUserWatcher(self._notify)
        self.archive_watchers = ArchiveWatcher(self._notify)

        self.act_panel = self._add_action("icon.svg", "NENA Addressing panel", self.toggle_panel, checkable=True)
        self.act_schema = self._add_menu_action("icon.svg", "Create NENA schema…",
                                               lambda *_: self._dock().create_schema())
        self.act_import = self._add_menu_action("mapping.svg", "Import into NENA schema…",
                                               lambda *_: self._dock().import_schema())
        self.act_archive = self._add_menu_action("icon.svg", "Create version tables…",
                                                lambda *_: self._dock().create_archive_schema())
        self.act_streets = self._add_action("streets.svg", "Master street names",
                                            lambda *_: self.show_tab("streets"))
        self.act_address = self._add_action("address.svg", "Address && driveway tool (click road, then rooftop)",
                                            self.activate_address_tool, checkable=True, use_toggled=False)
        self.act_undo_addr = self._add_action("undo_address.svg", "Undo last address",
                                              lambda *_: self._undo_address())
        self.act_range = self._add_action("range.svg", "Auto-range selected centerlines",
                                          lambda *_: self._dock().run_ranging(True))
        self.act_redistribute = self._add_action("redistribute.svg", "Redistribute ranges across selected segments",
                                                 lambda *_: self._dock().redistribute_selected())
        self.act_split_tool = self._add_action("split_tool.svg", "Split && Re-range tool (click a centerline)",
                                               self.activate_split_tool, checkable=True, use_toggled=False)
        self.act_auto = self._add_action("split.svg", "Auto re-range after split/merge (current layer)",
                                         self.toggle_auto, checkable=True)
        self.act_validate = self._add_action("validate.svg", "Validate centerline layer",
                                             lambda *_: self._dock().run_validation(False))
        self.act_overlaps = self._add_action("overlaps.svg", "Find && select overlapping address ranges",
                                             lambda *_: self._dock().find_overlaps(False))

        self.split_tool = SplitRerangeTool(self.iface.mapCanvas(), self._split_target, self._notify)
        self.split_tool.setAction(self.act_split_tool)
        self.address_tool = AddressTool(self.iface.mapCanvas(), self._address_context, self._notify,
                                        on_created=lambda ids: self.overlay and self.overlay.refresh())
        self.address_tool.setAction(self.act_address)
        self.overlay = AddressOverlay(self.iface)
        from qgis.core import QgsProject
        QgsProject.instance().readProject.connect(self._project_loaded)
        self.iface.currentLayerChanged.connect(self._sync_auto_action)
        self._place_toolbar()
        # QGIS restores the window after initGui. ArcSkin then hides every toolbar and
        # builds its ribbon. Place this bar again once that has finished.
        QTimer.singleShot(0, self._place_toolbar)
        QTimer.singleShot(800, self._place_toolbar)
        QTimer.singleShot(2000, self._place_toolbar)
        try:
            self.iface.initializationCompleted.connect(self._place_toolbar_soon)
        except (TypeError, RuntimeError):
            pass

    def unload(self):
        try:
            self.iface.initializationCompleted.disconnect(self._place_toolbar_soon)
        except (TypeError, RuntimeError):
            pass
        try:
            self.iface.currentLayerChanged.disconnect(self._sync_auto_action)
        except (TypeError, RuntimeError):
            pass
        try:
            from qgis.core import QgsProject
            QgsProject.instance().readProject.disconnect(self._project_loaded)
        except (TypeError, RuntimeError):
            pass
        if self.address_tool is not None:
            if self.iface.mapCanvas().mapTool() is self.address_tool:
                self.iface.mapCanvas().unsetMapTool(self.address_tool)
            self.address_tool.cleanup()
            self.address_tool = None
        if self.overlay is not None:
            self.overlay.unload()
            self.overlay = None
        if self.split_tool is not None:
            if self.iface.mapCanvas().mapTool() is self.split_tool:
                self.iface.mapCanvas().unsetMapTool(self.split_tool)
            self.split_tool.cleanup()
            self.split_tool = None
        for a in self.actions:
            self.iface.removePluginVectorMenu(MENU, a)
            self.iface.removeToolBarIcon(a)
        self.actions = []
        if self.toolbar is not None:
            self.toolbar.deleteLater()
            self.toolbar = None
        if self.dock is not None:
            self.dock.cleanup()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.watchers is not None:
            self.watchers.unload()
            self.watchers = None
        if self.street_watchers is not None:
            self.street_watchers.unload()
            self.street_watchers = None
        if self.editor_watchers is not None:
            self.editor_watchers.unload()
            self.editor_watchers = None
        if self.archive_watchers is not None:
            self.archive_watchers.unload()
            self.archive_watchers = None

    # ------------------------------------------------------------------
    def _add_action(self, icon_name, text, slot, checkable=False, use_toggled=True):
        a = QAction(icon(icon_name), text, self.iface.mainWindow())
        a.setCheckable(checkable)
        if checkable and use_toggled:
            a.toggled.connect(slot)
        else:
            a.triggered.connect(slot)
        self.toolbar.addAction(a)
        self.iface.addPluginToVectorMenu(MENU, a)
        self.actions.append(a)
        return a

    def _add_menu_action(self, icon_name, text, slot):
        a = QAction(icon(icon_name), text, self.iface.mainWindow())
        a.triggered.connect(slot)
        self.iface.addPluginToVectorMenu(MENU, a)
        self.actions.append(a)
        return a

    def _ensure_spacer_first(self):
        tb = self.toolbar
        spacer = getattr(self, "_toolbar_spacer", None)
        if tb is None or spacer is None:
            return
        acts = tb.actions()
        spacer_act = next((a for a in acts if tb.widgetForAction(a) is spacer), None)
        if spacer_act is None:
            if acts:
                tb.insertWidget(acts[0], spacer)
            else:
                tb.addWidget(spacer)
            return
        if acts and acts[0] is not spacer_act:
            tb.removeAction(spacer_act)
            first = tb.actions()[0] if tb.actions() else None
            if first is not None:
                tb.insertWidget(first, spacer)
            else:
                tb.addWidget(spacer)

    def _place_toolbar_soon(self, *_args):
        QTimer.singleShot(0, self._place_toolbar)
        QTimer.singleShot(600, self._place_toolbar)

    def _place_toolbar(self):
        """Own full-width row at the top of the window, with the tools at the right.

        ArcSkin hides plugin toolbars and puts a ribbon in the top area. This bar
        is placed on the row above that ribbon (or above the first QGIS toolbar)
        and shown again after the skin hides it.
        """
        tb = self.toolbar
        if tb is None:
            return
        main = self.iface.mainWindow()
        if main is None:
            return
        self._ensure_spacer_first()
        area = Qt.ToolBarArea.TopToolBarArea
        anchor = main.findChild(type(tb), "ArcSkinRibbonBar")
        if anchor is None or anchor.parent() is not main:
            anchor = main.findChild(type(tb), "mFileToolBar")
        if anchor is None or anchor.parent() is not main or main.toolBarArea(anchor) != area:
            anchor = next((bar for bar in main.findChildren(type(tb))
                           if bar is not tb and bar.parent() is main and main.toolBarArea(bar) == area), None)
        if anchor is not None:
            main.insertToolBar(anchor, tb)
            if not main.toolBarBreak(anchor):
                main.insertToolBarBreak(anchor)
        else:
            main.addToolBar(area, tb)
        tb.setVisible(True)

    def _catalog_host(self, main, dock):
        from qgis.PyQt.QtWidgets import QDockWidget
        found = []
        side = (Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea)
        for widget in main.findChildren(QDockWidget):
            if widget is dock or widget.isFloating():
                continue
            area = main.dockWidgetArea(widget)
            if area == Qt.DockWidgetArea.NoDockWidgetArea:
                continue
            title = f"{widget.objectName()} {widget.windowTitle()}".lower()
            if "browser" in title or "catalog" in title:
                rank = 0
            elif "layer" in title:
                rank = 1
            elif area in side:
                rank = 2
            else:
                rank = 3
            found.append((rank, widget))
        if not found:
            return None
        found.sort(key=lambda item: item[0])
        return found[0][1]

    def _install_dock(self):
        """Dock the panel as a tab on the catalog stack so it shares that space."""
        dock = self.dock
        if dock is None:
            return
        main = self.iface.mainWindow()
        host = self._catalog_host(main, dock)
        area = Qt.DockWidgetArea.RightDockWidgetArea
        if host is not None:
            host_area = main.dockWidgetArea(host)
            if host_area != Qt.DockWidgetArea.NoDockWidgetArea:
                area = host_area
        self.iface.addDockWidget(area, dock)
        if host is not None:
            main.tabifyDockWidget(host, dock)
        if dock.isVisible():
            dock.raise_()

    def _dock(self) -> NenaDock:
        if self.dock is None:
            self.dock = NenaDock(self.iface, self.watchers, self.iface.mainWindow(), self.street_watchers)
            self._install_dock()
            self.dock.visibilityChanged.connect(self._dock_visibility)
            self.dock.cb_layer.layerChanged.connect(self._sync_auto_action)
            self.dock.splitToolRequested.connect(self.activate_split_tool)
            self.dock.addressToolRequested.connect(self.activate_address_tool)
            self.dock.projectConfigSaved.connect(self._config_saved)
            self.dock.hide()
            QTimer.singleShot(0, self._install_dock)
            QTimer.singleShot(300, self._install_dock)
        return self.dock

    def show_tab(self, name):
        d = self._dock()
        d.setVisible(True)
        d.raise_()
        d.tabs.setCurrentWidget(getattr(d, name))

    def toggle_panel(self, checked):
        d = self._dock()
        d.setVisible(checked)
        if checked:
            d.raise_()

    def _dock_visibility(self, visible):
        self.act_panel.blockSignals(True)
        self.act_panel.setChecked(visible)
        self.act_panel.blockSignals(False)
        self._sync_auto_action()

    # -- split & re-range tool -------------------------------------------
    def _split_target(self):
        d = self._dock()
        if d.layer is None:
            return None, None
        return d.layer, d.collect()

    def activate_split_tool(self, *args):
        d = self._dock()
        if d.layer is None:
            self._notify("Warning", "Choose a centerline layer in the NENA Addressing panel first.")
            self.act_split_tool.setChecked(False)
            return
        d._persist()  # noqa: SLF001 - make sure the stored config matches the panel
        self.iface.mapCanvas().setMapTool(self.split_tool)
        if not d.layer.isEditable():
            self._notify("Info", f"Split & Re-range: toggle editing on '{d.layer.name()}', then click a centerline.")
        else:
            self._notify("Info", "Split & Re-range: click a centerline where it should be split "
                                 "(right-click or Esc to stop).")

    # -- addressing ----------------------------------------------------------
    def _address_context(self):
        d = self._dock()
        if d.layer is None:
            raise ConfigError("Choose the centerline layer in the NENA Addressing panel first.")
        return AE.AddressContext(d.addresses.store(), d.layer, d.collect())

    def activate_address_tool(self, *args):
        try:
            self._address_context()
        except ConfigError as exc:
            self._notify("Warning", str(exc))
            self.act_address.setChecked(False)
            self.show_tab("addresses")
            return
        self.iface.mapCanvas().setMapTool(self.address_tool)
        self._notify("Info", "Address tool: click the road at the driveway entrance, optional bends, then "
                             "right-click (or double-click) the rooftop. Backspace removes a click, Esc cancels.")

    def _undo_address(self):
        try:
            n = AE.undo_last_address()
        except ConfigError as exc:
            self._notify("Info", str(exc))
            return
        self._notify("Success", f"Last address removed ({n} feature(s)).")
        if self.overlay:
            self.overlay.refresh()

    def _config_saved(self):
        if self.street_watchers is not None:
            self.street_watchers.refresh()
        if self.editor_watchers is not None:
            self.editor_watchers.refresh()
        if self.archive_watchers is not None:
            self.archive_watchers.refresh()
        if self.overlay is not None:
            self.overlay.refresh()

    def _project_loaded(self, *args):
        if self.overlay is not None:
            self.overlay.refresh()
        if self.dock is not None:
            self.dock.streets.load()
            self.dock.addresses.load()
            self.dock.sync_archive_option()

    # -- auto re-range toggle ----------------------------------------------
    def toggle_auto(self, checked):
        d = self._dock()
        if d.layer is None:
            self._notify("Warning", "Choose a centerline layer in the NENA Addressing panel first.")
            self._sync_auto_action()
            return
        d.edit.chk_merge.setChecked(checked)
        d.set_auto_split(checked)
        self._notify("Info", f"Automatic re-ranging {'enabled' if checked else 'disabled'} for "
                             f"'{d.layer.name()}'.")

    def _sync_auto_action(self, *args):
        if self.dock is None:
            return
        self.act_auto.blockSignals(True)
        self.act_auto.setChecked(self.dock.layer is not None and self.dock.current_cfg_auto_split())
        self.act_auto.blockSignals(False)

    def _notify(self, level, text):
        self.iface.messageBar().pushMessage(TITLE, text, level=msg_level(level), duration=5)
