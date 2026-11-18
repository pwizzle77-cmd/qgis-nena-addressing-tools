# -*- coding: utf-8 -*-
"""
NENA Addressing and RCL Tools – QGIS plugin entry point (formerly "NENA RCL Tools").

Creates the toolbar, menu entries, dock panel, the Split & Re-range map tool
and the edit watchers that re-range centerlines after split / merge.
"""

import contextlib

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QMenu, QToolButton

from .compat import QAction, msg_level
from .core.access_guard import AccessGuard
from .core.archive_watch import ArchiveWatcher
from .core.editor_user import EditorUserWatcher
from .core.split_handler import WatcherManager
from .core.streets.sync import StreetWatcherManager
from .ui.dock import NenaDock, TITLE
from .ui.windows import WindowHost
from .ui.split_tool import SplitRerangeTool
from .ui.address_tool import AddressTool, DrawDrivewayTool, PlaceUnitsTool
from .ui.address_overlay import AddressOverlay
from .core.addressing import engine as AE
from .core.layer_access import ConfigError
from .ui.widgets import icon

MENU = "&NENA Addressing and RCL Tools"


class NenaRclPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.actions = []
        self.toolbar = None
        self.menu = None
        self.dock = None
        self.windows = None
        self.access_guard = None
        self.watchers = None
        self.street_watchers = None
        self.editor_watchers = None
        self.archive_watchers = None
        self.split_tool = None
        self.address_tool = None
        self.driveway_tool = None
        self.units_tool = None
        self.overlay = None

    # ------------------------------------------------------------------
    def initGui(self):  # noqa: N802 - QGIS API name
        self.toolbar = self.iface.addToolBar(TITLE)
        self.toolbar.setObjectName("NenaRclToolsToolbar")   # kept so QGIS remembers the toolbar position
        self.toolbar.setMovable(True)
        # Short names under the icons, so nothing needs a hover to explain it.
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)

        self.watchers = WatcherManager(self._notify)
        self.street_watchers = StreetWatcherManager(self._notify)
        # Connect before the version watcher so a save records the login that was just stamped.
        self.editor_watchers = EditorUserWatcher(self._notify)
        self.archive_watchers = ArchiveWatcher(self._notify)
        self.access_guard = AccessGuard(self._notify)

        self._menu_groups = {"windows": [], "centerline": [], "addressing": [], "setup": []}

        # Windows: one labelled "NENA" drop-down, so no window button shares a picture with a tool.
        self.act_streets = self._add_menu_action("streets.svg", "Streets",
                                                 lambda *_: self.show_tab("streets"), "windows")
        self.act_addresses = self._add_menu_action("", "Addresses",
                                                   lambda *_: self.show_tab("addresses"), "windows")
        self.act_ranges = self._add_menu_action("", "Ranges",
                                                lambda *_: self.show_tab("ranges"), "windows")
        self.act_check = self._add_menu_action("", "Check",
                                               lambda *_: self.show_tab("validation"), "windows")
        self.act_lookups = self._add_menu_action("lookups.svg", "Lookup tables (drop-down lists)",
                                                 lambda *_: self.show_tab("lookups"), "windows")
        self.act_schema_window = self._add_menu_action("schema.svg", "Schema and import",
                                                       lambda *_: self.show_tab("schema"), "windows")
        self.act_settings = self._add_menu_action("settings.svg", "Settings",
                                                  lambda *_: self.show_tab("settings"), "windows")
        self.act_docked = QAction("Dock the NENA windows in the side panel", self.iface.mainWindow())
        self.act_docked.setCheckable(True)
        self.act_docked.setToolTip("Open the NENA windows as tabs beside the catalog instead of as "
                                   "separate windows")
        from .ui.windows import docked_preference
        self.act_docked.setChecked(docked_preference())
        self.act_docked.toggled.connect(self._set_docked)
        self.actions.append(self.act_docked)
        self.act_hide_tables = QAction("Hide version and lookup tables", self.iface.mainWindow())
        self.act_hide_tables.setCheckable(True)
        self.act_hide_tables.setToolTip("Keep the version (history) tables and the lookup tables out of the "
                                        "Layers panel. They stay loaded, so change tracking and the "
                                        "drop-down lists keep working.")
        self.act_hide_tables.toggled.connect(self._set_tables_hidden)
        self.actions.append(self.act_hide_tables)
        self._windows_button_slot = self.toolbar.addSeparator()

        # Centerline tools
        self.act_split_tool = self._add_action("split_tool.svg", "Split && Re-range tool (click a centerline)",
                                               self.activate_split_tool, "centerline", checkable=True,
                                               use_toggled=False, short="Split")
        self.act_auto = self._add_action("split.svg", "Auto re-range after split/merge (current layer)",
                                         self.toggle_auto, "centerline", checkable=True, short="Auto re-range")
        self.act_range = self._add_action("range.svg", "Auto-range selected centerlines",
                                          lambda *_: self._dock().run_ranging(True), "centerline",
                                          short="Range")
        self.act_redistribute = self._add_action("redistribute.svg", "Redistribute ranges across selected segments",
                                                 lambda *_: self._dock().redistribute_selected(), "centerline",
                                                 short="Redistribute")
        self.act_validate = self._add_action("validate.svg", "Validate centerline layer",
                                             lambda *_: self._dock().run_validation(False), "centerline",
                                             short="Validate")
        self.act_reverse = self._add_action("reverse.svg",
                                            "Reverse the direction of the selected centerlines (sides and ranges "
                                            "follow, so every house keeps its number)",
                                            lambda *_: self._dock().reverse_selected(), "centerline",
                                            short="Reverse")
        self._add_one_way_button()
        self.act_overlaps = self._add_action("overlaps.svg", "Find && select overlapping address ranges",
                                             lambda *_: self._dock().find_overlaps(False), "centerline",
                                             short="Overlaps")
        self._toolbar_separator()
        # Addressing tools
        self.act_address = self._add_action("address.svg", "Place address (click road, then rooftop)",
                                            self.activate_address_tool, "addressing", checkable=True,
                                            use_toggled=False, short="Address")
        self.act_driveway = self._add_action("driveway.svg", "Draw driveway (click road, then the path)",
                                             self.activate_driveway_tool, "addressing", checkable=True,
                                             use_toggled=False, short="Driveway")
        self.act_units = self._add_action("units.svg", "Place units (one house number, many units)",
                                          self.activate_units_tool, "addressing", checkable=True,
                                          use_toggled=False, short="Units")
        self.act_undo_addr = self._add_action("undo_address.svg", "Undo last address",
                                              lambda *_: self._undo_address(), "addressing", short="Undo")
        # Setup (menus only)
        self.act_style = self._add_menu_action("style.svg", "Style NENA layers (roads, addresses, driveways)",
                                               lambda *_: self._dock().style_layers(), "setup")
        self.act_schema = self._add_menu_action("schema.svg", "Create NENA schema…",
                                                lambda *_: self._dock().create_schema(), "setup")
        self.act_import = self._add_menu_action("", "Import into NENA schema…",
                                                lambda *_: self._dock().import_schema(), "setup")
        self.act_archive = self._add_menu_action("", "Create version tables…",
                                                 lambda *_: self._dock().create_archive_schema(), "setup")
        self._add_windows_button(before=self._windows_button_slot)
        self._build_menu()

        self.split_tool = SplitRerangeTool(self.iface.mapCanvas(), self._split_target, self._notify)
        self.split_tool.setAction(self.act_split_tool)
        canvas = self.iface.mapCanvas()
        refresh = self._refresh_overlay
        self.address_tool = AddressTool(canvas, self._address_context, self._notify, on_created=refresh)
        self.address_tool.setAction(self.act_address)
        self.driveway_tool = DrawDrivewayTool(canvas, self._address_context, self._notify, on_created=refresh)
        self.driveway_tool.setAction(self.act_driveway)
        self.units_tool = PlaceUnitsTool(canvas, self._address_context, self._notify, on_created=refresh)
        self.units_tool.setAction(self.act_units)
        self.overlay = AddressOverlay(self.iface)
        from qgis.core import QgsProject
        QgsProject.instance().readProject.connect(self._project_loaded)
        self.iface.currentLayerChanged.connect(self._sync_auto_action)
        # A plain toolbar, like Parcel Fabric's: QGIS remembers where it goes, and
        # ribbon interfaces (Workspace Ribbon) turn it into a tab of their own.
        self._dock()

    def unload(self):
        try:
            self.iface.currentLayerChanged.disconnect(self._sync_auto_action)
        except (TypeError, RuntimeError):
            pass
        try:
            from qgis.core import QgsProject
            QgsProject.instance().readProject.disconnect(self._project_loaded)
        except (TypeError, RuntimeError):
            pass
        for name in ("address_tool", "driveway_tool", "units_tool"):
            tool = getattr(self, name, None)
            if tool is None:
                continue
            if self.iface.mapCanvas().mapTool() is tool:
                self.iface.mapCanvas().unsetMapTool(tool)
            tool.cleanup()
            setattr(self, name, None)
        if self.overlay is not None:
            self.overlay.unload()
            self.overlay = None
        if self.split_tool is not None:
            if self.iface.mapCanvas().mapTool() is self.split_tool:
                self.iface.mapCanvas().unsetMapTool(self.split_tool)
            self.split_tool.cleanup()
            self.split_tool = None
        for a in self.actions:
            self.iface.removeToolBarIcon(a)
        self.actions = []
        if self.menu is not None:
            try:
                self.iface.vectorMenu().removeAction(self.menu.menuAction())
            except (TypeError, RuntimeError):
                pass
            self.menu.deleteLater()
            self.menu = None
        if self.toolbar is not None:
            self.toolbar.deleteLater()
            self.toolbar = None
        if self.windows is not None:
            self.windows.unload()
            self.windows = None
        if self.dock is not None:
            self.dock.cleanup()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.access_guard is not None:
            self.access_guard.unload()
            self.access_guard = None
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
    def _add_action(self, icon_name, text, slot, group, checkable=False, use_toggled=True, toolbar=True,
                    short=""):
        a = QAction(icon(icon_name) if icon_name else QIcon(), text, self.iface.mainWindow())
        a.setToolTip(text.replace("&&", "&"))
        if short:
            a.setIconText(short)
        a.setCheckable(checkable)
        if checkable and use_toggled:
            a.toggled.connect(slot)
        else:
            a.triggered.connect(slot)
        if toolbar:
            self.toolbar.addAction(a)
        self._menu_groups.setdefault(group, []).append(a)
        self.actions.append(a)
        return a

    def _add_menu_action(self, icon_name, text, slot, group):
        return self._add_action(icon_name, text, slot, group, toolbar=False)

    def _toolbar_separator(self):
        self.toolbar.addSeparator()

    def _windows_menu(self, parent):
        menu = QMenu(parent)
        for action in self._menu_groups.get("windows") or []:
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction(self.act_style)
        menu.addAction(self.act_docked)
        menu.addAction(self.act_hide_tables)
        menu.aboutToShow.connect(self._sync_hide_tables)
        return menu

    def _add_windows_button(self, before=None):
        """A labelled drop-down that opens the NENA windows."""
        button = QToolButton(self.toolbar)
        button.setText("NENA")
        button.setIcon(icon("windows.svg"))
        button.setToolTip("Open a NENA window: Streets, Addresses, Ranges, Check, Lookup tables, "
                          "Schema and import, Settings")
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self._windows_popup = self._windows_menu(button)
        button.setMenu(self._windows_popup)
        self._windows_button = button
        if before is not None:
            self._windows_button_action = self.toolbar.insertWidget(before, button)
        else:
            self._windows_button_action = self.toolbar.addWidget(button)

    def _add_one_way_button(self):
        """A labelled drop-down: mark selected centerlines two-way, FT, or TF (for routing)."""
        from .core.direction import ONE_WAY_CHOICES
        button = QToolButton(self.toolbar)
        button.setText("One-way")
        button.setIcon(icon("oneway.svg"))
        button.setToolTip("Mark the selected centerlines two-way (B), one-way From → To (FT), or one-way "
                          "To → From (TF). FT and TF follow the direction the line was drawn")
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        menu = QMenu(button)
        self._one_way_actions = []
        for code, text, tip in ONE_WAY_CHOICES + ((None, "Clear one-way", "Empty the OneWay field"),):
            if code is None:
                menu.addSeparator()
            act = QAction(text, menu)
            act.setToolTip(tip)
            act.triggered.connect(lambda *_, c=code: self._dock().set_one_way_selected(c))
            menu.addAction(act)
            self._one_way_actions.append(act)
        button.setMenu(menu)
        self._one_way_menu = menu
        self._one_way_button = button
        self.toolbar.addWidget(button)
        group = self._menu_groups.setdefault("centerline", [])
        sub = QMenu("One-way (routing)", self.iface.mainWindow())
        for act in self._one_way_actions:
            sub.addAction(act)
        self._one_way_submenu = sub
        group.append(sub.menuAction())

    def _set_docked(self, checked):
        self._dock()
        if self.windows is not None:
            self.windows.set_docked(checked)

    def _refresh_overlay(self, *_ids):
        if self.overlay:
            self.overlay.refresh()

    def _build_menu(self):
        menu = QMenu(MENU, self.iface.mainWindow())
        titles = (("windows", "Windows"), ("centerline", "Centerlines"), ("addressing", "Addressing"),
                  ("setup", "Setup"))
        started = False
        for key, title in titles:
            actions = self._menu_groups.get(key) or []
            if not actions:
                continue
            if started:
                menu.addSeparator()
            started = True
            label = QAction(title, menu)
            label.setEnabled(False)
            font = label.font()
            font.setBold(True)
            label.setFont(font)
            menu.addAction(label)
            for action in actions:
                menu.addAction(action)
            if key == "windows":
                menu.addAction(self.act_docked)
                menu.addAction(self.act_hide_tables)
        self.iface.vectorMenu().addMenu(menu)
        self.menu = menu

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
            self.windows = WindowHost(self.iface, self.dock, find_dock_host=self._catalog_host)
            self.windows.on_visibility = self._windows_visibility
            self.dock.cb_layer.layerChanged.connect(self._sync_auto_action)
            self.dock.splitToolRequested.connect(self.activate_split_tool)
            self.dock.addressToolRequested.connect(self.activate_address_tool)
            self.dock.drivewayToolRequested.connect(self.activate_driveway_tool)
            self.dock.unitsToolRequested.connect(self.activate_units_tool)
            self.dock.projectConfigSaved.connect(self._config_saved)
            self.dock.hide()
        return self.dock

    def show_tab(self, name):
        """Open one window. ``streets`` and ``addresses`` keep their old names."""
        self._dock()
        aliases = {"check": "validation", "ranges": "ranges"}
        self.windows.show(aliases.get(name, name))

    def _windows_visibility(self):
        if self.windows is None:
            return
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
            self._notify("Warning", "Choose a centerline layer in a NENA window first.")
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
            raise ConfigError("Choose the centerline layer in a NENA window first.")
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
        self._notify("Info", "Place address: click the road (or a driveway), then click the building. "
                             "An access point is saved 10 feet off the road. Esc cancels.")

    def activate_driveway_tool(self, *args):
        try:
            ctx = self._address_context()
        except ConfigError as exc:
            self._notify("Warning", str(exc))
            self.act_driveway.setChecked(False)
            self.show_tab("addresses")
            return
        if ctx.dw is None:
            self._notify("Warning", "Choose a driveway layer in the Addresses window first.")
            self.act_driveway.setChecked(False)
            self.show_tab("addresses")
            return
        self.iface.mapCanvas().setMapTool(self.driveway_tool)
        self._notify("Info", "Draw driveway: click the road where the driveway meets it, click each bend, "
                             "then right-click or double-click to finish. Esc cancels.")

    def activate_units_tool(self, *args):
        try:
            self._address_context()
        except ConfigError as exc:
            self._notify("Warning", str(exc))
            self.act_units.setChecked(False)
            self.show_tab("addresses")
            return
        self.iface.mapCanvas().setMapTool(self.units_tool)
        self._notify("Info", "Place units: click the road to take the house number, then click each unit "
                             "under that roof. The house number stays and the unit number changes. "
                             "Right-click or Esc ends this roof. Esc again leaves the tool.")

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
        if self.access_guard is not None:
            self.access_guard.refresh()
        if self.overlay is not None:
            self.overlay.refresh()
        with contextlib.suppress(Exception):  # relations are a convenience, not the save
            from .core.lookups import apply_value_relations
            from .core.project_config import load_project_config
            apply_value_relations(load_project_config(), self.dock.layer if self.dock is not None else None)

    def _project_loaded(self, *args):
        if self.overlay is not None:
            self.overlay.refresh()
        if self.dock is not None:
            self.dock.streets.load()
            self.dock.addresses.load()
            self.dock.lookups.load()
            self.dock.sync_archive_option()
        self._sync_hide_tables()
        # Put the drop-downs back on fields added since the project was last saved.
        QTimer.singleShot(0, self._apply_dropdowns)

    # -- support tables in the Layers panel ---------------------------------
    def _sync_hide_tables(self, *_args):
        """Tick the menu item when every support table is hidden."""
        from .core.hidden_layers import hidden_count, support_layers
        from .core.project_config import load_project_config
        pcfg = load_project_config()
        tables = support_layers(pcfg)
        self.act_hide_tables.blockSignals(True)
        try:
            self.act_hide_tables.setEnabled(bool(tables))
            self.act_hide_tables.setChecked(bool(tables) and hidden_count(pcfg) == len(tables))
        finally:
            self.act_hide_tables.blockSignals(False)

    def _set_tables_hidden(self, hidden):
        from .core.hidden_layers import set_support_hidden
        from .core.project_config import load_project_config, save_project_config
        pcfg = load_project_config()
        pcfg.display.hide_support_tables = bool(hidden)
        save_project_config(pcfg)
        changed = set_support_hidden(pcfg, bool(hidden))
        if hidden:
            text = (f"Hid {changed} version and lookup table(s). They are still loaded and still updated. "
                    "Untick NENA ▾ → Hide version and lookup tables to see them again.")
        else:
            text = f"Showing {changed} version and lookup table(s) in the group 'NENA support tables'."
        self._notify("Info", text)

    def _apply_dropdowns(self):
        with contextlib.suppress(Exception):  # a drop-down is a convenience, not a reason to fail loading
            from .core.lookups import apply_dropdowns
            from .core.project_config import load_project_config
            centerline = self.dock.layer if self.dock is not None else None
            apply_dropdowns(load_project_config(), centerline)

    # -- auto re-range toggle ----------------------------------------------
    def toggle_auto(self, checked):
        d = self._dock()
        if d.layer is None:
            self._notify("Warning", "Choose a centerline layer in a NENA window first.")
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
