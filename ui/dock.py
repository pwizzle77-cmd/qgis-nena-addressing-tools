# -*- coding: utf-8 -*-
"""
Main dock panel. Owns the current layer + configuration and wires the tabs
to the core engines.
"""

import contextlib
import os

from qgis.core import QgsCoordinateReferenceSystem, QgsProject, QgsRectangle, QgsVectorLayer
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtCore import QCoreApplication, QSettings, QSize, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (QCheckBox, QDockWidget, QFileDialog, QGroupBox, QHBoxLayout, QInputDialog,
                                 QLabel, QMessageBox, QProgressBar, QPushButton, QVBoxLayout,
                                 QWidget)

from ..compat import line_layer_filter, msg_level
from ..core import config as C
from ..core.layer_access import ConfigError, add_missing_fields
from ..core.maintenance import (create_template_layer, default_mapping_for_template, populate_ids,
                                template_field_defs)
from ..core.ranger import RangeEngine
from ..core import rules as V
from ..core import template as T
from ..core.archive_model import roles_missing_archive
from ..core.archive_watch import pairing
from ..core.importer import destination_fields, import_layer
from ..core.addressing.engine import attribute_centerline_sides
from ..core.addressing.side_split import attribution_context, split_where_side_changes
from ..core.defaults import fill_empty_defaults
from ..core.gaps import fill_range_gaps
from ..core.intersections import split_existing_intersections
from ..core.msag import compare_msag
from ..core.project_config import layer_for, load_project_config, remember, save_project_config
from ..core.schema import MANDATORY
from ..core.schema_pack import (address_defs, centerline_defs, format_import_report, unmapped_required)
from ..core.schema_writer import SchemaCreateError, create_schema, list_existing
from ..core.centerline_pick import excluded_layers, not_a_centerline, preferred_centerline
from ..core.split_handler import apply_split_policies
from ..core.validator import Validator, export_csv, issues_layer
from .edit_tab import EditTab
from .mapping_tab import MappingTab
from .ranging_tab import RangingTab
from .settings_tab import SettingsTab
from .streets_tab import StreetsTab
from .addresses_tab import AddressesTab
from .schema_dialogs import CreateSchemaDialog, ImportDialog
from .validation_tab import ValidationTab
from .lookup_window import LookupPage
from .widgets import FittingScroll, Surface, hint, host_groups, page_into

TITLE = "NENA Addressing and RCL Tools"


class NenaDock(QDockWidget):
    splitToolRequested = pyqtSignal()
    addressToolRequested = pyqtSignal()
    drivewayToolRequested = pyqtSignal()
    unitsToolRequested = pyqtSignal()
    projectConfigSaved = pyqtSignal()

    def __init__(self, iface, watchers, parent=None, street_watchers=None):
        super().__init__(TITLE, parent)
        self.street_watchers = street_watchers
        self.setObjectName("NenaRclToolsDock")
        # Message boxes and input dialogs are parented to this (hidden) dock: give them a normal look.
        from .widgets import apply_normal_look
        apply_normal_look(self, "NenaRclToolsDock")
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
                             | Qt.DockWidgetArea.BottomDockWidgetArea | Qt.DockWidgetArea.TopDockWidgetArea)
        self.iface = iface
        self.watchers = watchers
        self.layer = None
        self.cfg = C.PluginConfig()

        root = QWidget()
        lay = QVBoxLayout(root)
        header = QWidget()
        header_lay = QVBoxLayout(header)
        header_lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("Centerline layer"))
        self.cb_layer = QgsMapLayerComboBox()
        self.cb_layer.setFilters(line_layer_filter())
        self.cb_layer.layerChanged.connect(self.set_layer)
        row.addWidget(self.cb_layer, 1)
        header_lay.addLayout(row)
        self.lbl_cfg = QLabel("")
        self.lbl_cfg.setWordWrap(True)
        header_lay.addWidget(self.lbl_cfg)
        header_scroll = FittingScroll(compact=True)
        header_scroll.setWidget(header)
        lay.addWidget(header_scroll)

        self.mapping = MappingTab()
        self.ranging = RangingTab()
        self.edit = EditTab()
        self.validation = ValidationTab()
        self.streets = StreetsTab(self)
        self.addresses = AddressesTab(self)
        self.lookups = LookupPage(self)
        self.ranging.manual_slot.addWidget(self.edit.manual_group)
        self.schema_page = self._project_page()
        self.settings = SettingsTab((
            ("Fields", self.mapping, "Match NENA fields to the centerline"),
            ("Sides", host_groups(self.mapping.side_group),
             "ZIP, MSAG, and ESN on each side of the road"),
            ("Defaults", host_groups(self.mapping.defaults_group),
             "Values that fill empty fields"),
            ("Street rules", host_groups(self.streets.uniqueness_group, self.streets.behaviour_group),
             "How street names must be unique, and what a rename does"),
            ("Address rules", host_groups(self.addresses.numbering_group, self.addresses.overrides_group),
             "How a new address gets its number"),
            ("Editing", host_groups(self.edit.auto_group, self.edit.nguid_group),
             "What happens while you edit, and NGUID"),
        ))
        self.addresses.toolRequested.connect(lambda *_: self.addressToolRequested.emit())
        self.addresses.drivewayToolRequested.connect(lambda *_: self.drivewayToolRequested.emit())
        self.addresses.unitsToolRequested.connect(lambda *_: self.unitsToolRequested.emit())
        self.addresses.configSaved.connect(lambda *_: self.projectConfigSaved.emit())
        self._progress_bars = []
        self._layer_mirrors = []
        self._layer_sync = False
        self.progress = self.make_progress()
        settings_wrap = Surface()
        settings_lay = QVBoxLayout(settings_wrap)
        settings_lay.setContentsMargins(0, 0, 0, 0)
        settings_lay.addWidget(header_scroll)
        settings_lay.addWidget(self.settings, 1)
        settings_lay.addWidget(self.progress)
        self.settings_root = settings_wrap
        self.open_page = lambda *_args: None
        # The pages live in their own windows. This dock keeps the objects and the save order.
        self.setWidget(QWidget(self))

        # wiring
        self.mapping.autoMapRequested.connect(self.auto_map)
        self.mapping.addFieldsRequested.connect(self.add_fields)
        self.mapping.saveRequested.connect(self.save_config)
        self.mapping.importRequested.connect(self.import_json)
        self.mapping.exportRequested.connect(self.export_json)
        self.mapping.templateRequested.connect(self.new_template)
        self.mapping.calculateSidesRequested.connect(lambda *_: self.addresses.attribute_sides())
        self.mapping.splitSidesRequested.connect(lambda *_: self.split_where_side_changes())
        self.mapping.fillDefaultsRequested.connect(lambda *_: self.fill_defaults())
        self.ranging.rangeSelectedRequested.connect(lambda *_: self.run_ranging(True))
        self.ranging.rangeAllRequested.connect(lambda *_: self.run_ranging(False))
        self.ranging.fillGapsRequested.connect(lambda *_: self.fill_range_gaps())
        self.edit.applyPoliciesRequested.connect(self.apply_policies)
        self.edit.redistributeRequested.connect(self.redistribute_selected)
        self.edit.populateIdsRequested.connect(self.populate_ids)
        self.edit.settingsChanged.connect(self.save_config_quiet)
        self.validation.runRequested.connect(self.run_validation)
        self.validation.selectRequested.connect(self.select_fids)
        self.validation.zoomRequested.connect(self.zoom_fids)
        self.validation.exportCsvRequested.connect(self.export_issues_csv)
        self.validation.loadLayerRequested.connect(self.load_issues_layer)
        self.validation.findOverlapsRequested.connect(self.find_overlaps)
        self.mapping.loadTemplateFileRequested.connect(self.load_template_file)
        self.mapping.loadTemplateLayerRequested.connect(self.load_template_layer)
        self.mapping.resetTemplateRequested.connect(self.reset_template)
        self.mapping.exportTemplateRequested.connect(self.export_template)
        self.edit.splitToolRequested.connect(lambda *_: self.splitToolRequested.emit())
        self.edit.splitExistingRequested.connect(lambda *_: self.split_existing_roads())
        self.validation.compareMsagRequested.connect(lambda *_: self.compare_msag())

        self.iface.currentLayerChanged.connect(self._follow_active_layer)
        prj = QgsProject.instance()
        prj.layersAdded.connect(self._refresh_centerline_choices)
        prj.layersRemoved.connect(self._refresh_centerline_choices)
        self.projectConfigSaved.connect(self._refresh_centerline_choices)
        self._refresh_centerline_choices()
        self.set_layer(self.cb_layer.currentLayer())

    def _refresh_centerline_choices(self, *_):
        """Leave version tables and driveways out of the Centerline pickers, and
        open on the project's centerline rather than whatever is listed first."""
        try:
            hidden = excluded_layers()
        except Exception:  # noqa: BLE001 - never block the window over a picker
            return
        for combo in [self.cb_layer] + list(getattr(self, "_layer_mirrors", [])):
            try:
                combo.setExceptedLayerList(hidden)
            except RuntimeError:
                continue
        current = self.cb_layer.currentLayer()
        if current is None or not_a_centerline(current):
            best = preferred_centerline(QgsProject.instance().mapLayers().values())
            if best is not None and best is not current:
                self.cb_layer.setLayer(best)

    def _project_page(self):
        """Schema, import, and change tracking. These are project settings, not daily tools."""
        page = QWidget()
        lay = page_into(page)
        group = QGroupBox("Schema and versions")
        box = QVBoxLayout(group)
        box.addWidget(hint("Create the NENA tables, the access-point table, and the lookup lists. "
                           "Copy existing data in, and keep a version of each save."))
        b_schema = QPushButton("Create schema…")
        b_schema.setToolTip("Create empty NENA road, street, address, driveway, and access-point tables, "
                            "plus the lookup lists, in a GeoPackage, File Geodatabase, or database connection.")
        b_schema.clicked.connect(self.create_schema)
        b_import = QPushButton("Import…")
        b_import.setToolTip("Copy existing roads, street names, addresses, and driveways into the schema layers.")
        b_import.clicked.connect(self.import_schema)
        b_archive = QPushButton("Versions…")
        b_archive.setToolTip("Create version tables for those layers in a GeoPackage, File Geodatabase, "
                             "or database connection. Each save records one version of the feature.")
        b_archive.clicked.connect(self.create_archive_schema)
        box.addWidget(b_schema)
        box.addWidget(b_import)
        box.addWidget(b_archive)
        self._syncing_archive = False
        self.chk_archive_edits = QCheckBox("Track changes")
        self.chk_archive_edits.setToolTip("When you save a centerline, street name, address, or driveway, "
                                          "add a version of that feature to its version table.")
        self.chk_archive_edits.toggled.connect(self._toggle_archive)
        box.addWidget(self.chk_archive_edits)
        lay.addWidget(group)
        lay.addStretch(1)
        self.sync_archive_option()
        return page

    def minimumSizeHint(self):
        # Small enough that a catalog tab can shrink and the scroll bars take over.
        return QSize(160, 100)

    def sizeHint(self):
        return QSize(440, 640)

    def cleanup(self):
        try:
            self.iface.currentLayerChanged.disconnect(self._follow_active_layer)
        except (TypeError, RuntimeError):
            pass
        prj = QgsProject.instance()
        for signal in (prj.layersAdded, prj.layersRemoved):
            try:
                signal.disconnect(self._refresh_centerline_choices)
            except (TypeError, RuntimeError):
                pass

    # ------------------------------------------------------------------ layer/config
    def _follow_active_layer(self, layer):
        if isinstance(layer, QgsVectorLayer) and layer is not self.layer and not not_a_centerline(layer):
            from ..compat import geometry_type_line
            if layer.geometryType() == geometry_type_line():
                self.cb_layer.setLayer(layer)

    def set_layer(self, layer):
        self.layer = layer if isinstance(layer, QgsVectorLayer) else None
        names = [f.name() for f in self.layer.fields()] if self.layer else []
        self.mapping.set_layer_fields(names)
        stored = C.load_from_layer(self.layer) if self.layer else None
        if stored is not None:
            self.cfg = stored
            self.lbl_cfg.setText("<i>Configuration loaded from layer.</i>")
        else:
            self.cfg = C.PluginConfig()
            self.mapping.load(self.cfg)
            n = self.mapping.apply_auto_map() if self.layer else 0
            self.mapping.store(self.cfg)
            self.lbl_cfg.setText(f"<i>No stored configuration – {n} field(s) auto-mapped. "
                                 "Review them under Settings, Fields. Changes save automatically.</i>"
                                 if self.layer else "")
        self._load_tabs()

    def _load_tabs(self):
        if hasattr(self, "streets"):
            self.streets.refresh_list()
        if hasattr(self, "addresses"):
            self.addresses.load()
        if hasattr(self, "lookups"):
            self.lookups.load()
        self.mapping.load(self.cfg)
        self.ranging.load(self.cfg)
        self.edit.load(self.cfg)
        self.validation.load(self.cfg, load_project_config().msag)

    def collect(self) -> C.PluginConfig:
        self.mapping.store(self.cfg)
        self.ranging.store(self.cfg)
        self.edit.store(self.cfg)
        self.validation.store(self.cfg)
        return self.cfg

    def _require_layer(self) -> QgsVectorLayer:
        if self.layer is None:
            raise ConfigError("Choose a line layer first.")
        return self.layer

    def _persist(self):
        """Store config on the layer and (re)attach the edit watcher."""
        if self.layer is None:
            return
        C.save_to_layer(self.layer, self.collect())
        if self.watchers is not None:
            self.watchers.refresh(self.layer)

    def save_config(self):
        def do():
            self.streets.store()
            if self.layer is not None:
                self._persist()
                self.lbl_cfg.setText("<i>Configuration stored on layer.</i>")
            self.addresses.store()
            self.validation.write_msag()
            if hasattr(self, "lookups"):
                self.lookups.store()
            if self.layer is not None:
                self._msg("Settings saved.", "Success")
            else:
                self._msg("Project settings saved. Choose a centerline layer to store field mapping on it.",
                          "Success")
        self._guard(do)

    # ------------------------------------------------------------------ auto-save
    def watch_for_changes(self, root, exclude=()):
        """Save the settings shortly after you change anything under ``root``.

        Only signals that come from you are used (a click, a pick from a list,
        leaving a text box), so loading a layer never writes settings by itself.
        """
        from qgis.PyQt.QtWidgets import QAbstractSpinBox, QComboBox, QLineEdit, QRadioButton
        skip = [w for w in exclude if w is not None]

        def excluded(widget):
            return any(widget is w or w.isAncestorOf(widget) for w in skip)

        for widget in root.findChildren(QWidget):
            if excluded(widget) or widget.property("nenaAutosave") is not None:
                continue
            if isinstance(widget, (QCheckBox, QRadioButton)):
                widget.clicked.connect(self.autosave)
            elif isinstance(widget, QComboBox):
                widget.activated.connect(self.autosave)
            elif isinstance(widget, QAbstractSpinBox):
                widget.editingFinished.connect(self.autosave)
            elif isinstance(widget, QLineEdit) and not isinstance(widget.parent(), (QComboBox, QAbstractSpinBox)):
                widget.editingFinished.connect(self.autosave)
            else:
                continue
            widget.setProperty("nenaAutosave", True)

    def autosave(self, *_args):
        """Save a moment later, so several quick changes make one save."""
        timer = getattr(self, "_autosave_timer", None)
        if timer is None:
            from qgis.PyQt.QtCore import QTimer
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(600)
            timer.timeout.connect(self._autosave_now)
            self._autosave_timer = timer
        timer.start()

    def _autosave_now(self):
        try:
            self.streets.store()
            if self.layer is not None:
                self._persist()
                self.lbl_cfg.setText("<i>Settings are saved automatically.</i>")
            self.addresses.store()
            self.validation.write_msag()
            self.projectConfigSaved.emit()
        except ConfigError as exc:
            self._msg(str(exc), "Warning")
        except Exception:  # noqa: BLE001 - never interrupt typing with a crash
            import traceback
            from qgis.core import QgsMessageLog
            QgsMessageLog.logMessage(traceback.format_exc(), TITLE, msg_level("Warning"))

    def save_config_quiet(self):
        if self.layer is not None and C.has_config(self.layer):
            self._persist()

    def auto_map(self):
        n = self.mapping.apply_auto_map()
        self._msg(f"{n} NENA field(s) matched.", "Info")

    def add_fields(self, mandatory_only: bool):
        def do():
            layer = self._require_layer()
            self.collect()
            names = [fd.name for fd in template_field_defs(self.cfg.template)
                     if (fd.req == MANDATORY or not mandatory_only)]
            missing = [n for n in names if not self.cfg.mapping.get(n)]
            if not missing:
                self._msg("Nothing to add – all requested fields are mapped.", "Info")
                return
            if QMessageBox.question(self, TITLE, f"Add {len(missing)} field(s) to '{layer.name()}'?\n\n"
                                    + ", ".join(missing)) != QMessageBox.StandardButton.Yes:
                return
            added = add_missing_fields(layer, self.cfg, missing)
            self.mapping.set_layer_fields([f.name() for f in layer.fields()])
            self.mapping.load(self.cfg)
            self._persist()
            self._msg(f"Added {len(added)} field(s) (in the edit buffer – save the layer to keep them).", "Success")
        self._guard(do)

    def import_json(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import NENA RCL configuration", self._last_dir(),
                                              "JSON (*.json)")
        if not path:
            return

        def do():
            self.cfg = C.load_from_file(path)
            self._remember_dir(path)
            self._load_tabs()
            self.autosave()
            self._msg("Configuration imported and saved.", "Success")
        self._guard(do)

    def export_json(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export NENA RCL configuration",
                                              os.path.join(self._last_dir(), "nena_rcl_config.json"),
                                              "JSON (*.json)")
        if not path:
            return
        self._guard(lambda: (C.save_to_file(path, self.collect()), self._remember_dir(path),
                             self._msg(f"Configuration written to {path}", "Success")))

    # ------------------------------------------------------------------ schema / import
    def create_schema(self):
        self._create_schema(False)

    def create_archive_schema(self):
        self._create_schema(True)

    def sync_archive_option(self):
        """Show whether saved edits are being recorded as versions."""
        self._syncing_archive = True
        try:
            self.chk_archive_edits.setChecked(load_project_config().archive.enabled)
        finally:
            self._syncing_archive = False

    def _toggle_archive(self, checked):
        if self._syncing_archive:
            return
        if not checked:
            self._set_archive_enabled(False)
            self._msg("Change tracking is off.", "Info")
            return
        self._remember_live_layers()
        live, archive = pairing(load_project_config())
        ready = {role: layer is not None for role, layer in archive.items()}
        if not any(ready.values()):
            self._syncing_archive = True
            self.chk_archive_edits.setChecked(False)
            self._syncing_archive = False
            if QMessageBox.question(
                    self, TITLE,
                    "Change tracking records each saved insert, update, and delete as a version. "
                    "Create the version tables now?"
            ) == QMessageBox.StandardButton.Yes:
                self.create_archive_schema()
            return
        self._set_archive_enabled(True)
        missing = roles_missing_archive({role: layer is not None for role, layer in live.items()}, ready)
        if missing:
            self._msg("Change tracking is on. Version tables are still needed for: " + ", ".join(missing) + ".", "Info")
        else:
            self._msg("Change tracking is on. Each save adds a version of the feature.", "Info")

    def _set_archive_enabled(self, enabled):
        pcfg = load_project_config()
        pcfg.archive.enabled = bool(enabled)
        save_project_config(pcfg)
        self.sync_archive_option()
        self.projectConfigSaved.emit()

    def _remember_live_layers(self):
        self.streets.store()
        self.addresses.store()

    def _create_schema(self, archive_only):
        if getattr(self, "_schema_busy", False):
            return
        crs = QgsProject.instance().crs()
        if self.layer is not None and self.layer.crs().isValid():
            crs = self.layer.crs()
        if not crs.isValid():
            crs = QgsCoordinateReferenceSystem("EPSG:4326")
        dlg = CreateSchemaDialog(self, crs, self._last_dir(), archive_only=archive_only)
        if not dlg.exec():
            return
        spec = dlg.request()

        def do():
            self.collect()
            target, keys = spec["target"], spec["keys"]
            rcl_tpl = self.cfg.template or None
            addr_tpl = (load_project_config().addresses.address_template or None)
            existing = list_existing(target, keys, spec["archive"], spec["archive_only"])
            if existing and target.replace:
                if QMessageBox.question(
                        self, TITLE,
                        "Replace these tables?\n\n" + "\n".join(existing)
                        + "\n\nOther tables in the file or database stay as they are.") != QMessageBox.StandardButton.Yes:
                    return
            try:
                created = create_schema(target, spec["crs"], keys, rcl_tpl, addr_tpl,
                                        spec["archive"], spec["archive_only"])
            except SchemaCreateError as exc:
                self._adopt_schema(exc.created, target, rcl_tpl, addr_tpl, spec["watch"])
                raise
            self._adopt_schema(created, target, rcl_tpl, addr_tpl, spec["watch"])
            if target.path:
                self._remember_dir(target.path)
            where = target.path or target.connection
            if target.schema:
                where = f"{where} / {target.schema}"
            names = ", ".join(lyr.name() for lyr in created.values())
            if spec["archive_only"]:
                text = f"Created version tables {names} in {where}."
            else:
                text = f"Created {names} in {where}. Use Import to load existing roads and addresses."
            if spec["watch"]:
                text += " Each save adds a version of what changed."
            self._msg(text, "Success")

        self._schema_busy = True
        try:
            self._guard(do)
        finally:
            self._schema_busy = False

    def import_schema(self):
        if getattr(self, "_schema_busy", False):
            return
        self.collect()
        pcfg = load_project_config()
        roles = {
            "centerline": self.layer,
            "streets": layer_for(pcfg.streets, "master"),
            "address": layer_for(pcfg.streets, "address"),
            "driveway": layer_for(pcfg.addresses, "driveway"),
        }
        dlg = ImportDialog(self, roles, self.cfg.template, pcfg.addresses.address_template)
        if not dlg.exec():
            return
        jobs = dlg.jobs()

        def do():
            entries = []

            def run(progress):
                count = len(jobs)
                for i, job in enumerate(jobs):
                    def tick(pct, index=i):
                        progress((index + pct / 100.0) * 100.0 / max(count, 1))
                    try:
                        result = import_layer(job["source"], job["dest"], job["field_map"],
                                              job["selected_only"], tick)
                    except ConfigError as exc:
                        entries.append({"title": job["title"], "skipped_reason": str(exc)})
                        tick(100)
                        continue
                    entries.append({
                        "title": job["title"],
                        "written": result["written"],
                        "skipped": result["skipped"],
                        "blank": result.get("blank") or 0,
                        "null_geometry": result["null_geometry"],
                        "bad_values": result["bad_values"],
                        "multipart": result["multipart"],
                        "mapped": result["mapped"],
                        "missing": unmapped_required(destination_fields(job["dest"]), job["field_map"], job["defs"]),
                    })

            self._with_progress(run)
            text = format_import_report(entries) or "Nothing was imported."
            QMessageBox.information(self, TITLE, text)
            written = sum(int(entry.get("written") or 0) for entry in entries)
            self._msg(f"Import finished. {written} feature(s) saved.", "Success")

        self._schema_busy = True
        try:
            self._guard(do)
        finally:
            self._schema_busy = False

    def _adopt_schema(self, created, target, rcl_tpl, addr_tpl, watch=False):
        """Add the new layers to the project and point the panel at them."""
        if not created:
            return
        prj = QgsProject.instance()
        if created.get("centerline") is not None:
            fresh = C.PluginConfig.from_dict(self.collect().to_dict())
            fresh.mapping = {d.name: d.physical_name for d in centerline_defs(rcl_tpl)}
            fresh.template = dict(rcl_tpl) if rcl_tpl else {}
            C.save_to_layer(created["centerline"], fresh)
        self.streets.store()
        self.addresses.store()
        pcfg = load_project_config()
        if created.get("centerline") is not None:
            remember(pcfg.streets, "centerline", created["centerline"])
        if created.get("streets") is not None:
            remember(pcfg.streets, "master", created["streets"])
        if created.get("address") is not None:
            remember(pcfg.streets, "address", created["address"])
            from ..core.streets.names import NAME_PARTS
            wanted = set(NAME_PARTS) | {"StreetID", "DateUpdate"}
            pcfg.streets.address_mapping = {d.name: d.physical_name for d in address_defs(addr_tpl)
                                            if d.name in wanted}
        if created.get("driveway") is not None:
            remember(pcfg.addresses, "driveway", created["driveway"])
            from ..core.schema_pack import driveway_defs
            pcfg.addresses.driveway_mapping = {d.name: d.physical_name for d in driveway_defs()}
        if created.get("access") is not None:
            remember(pcfg.addresses, "access", created["access"])
        from ..core.lookups import LOOKUP_SPECS, apply_value_relations, seed_layer
        for key, _table, _title, role in LOOKUP_SPECS:
            lyr = created.get(key)
            if lyr is None:
                continue
            remember(pcfg.lookups, role, lyr)
            seed_layer(lyr, key)
        archive_roles = (
            ("centerline", "centerline_archive"),
            ("streets", "streets_archive"),
            ("address", "address_archive"),
            ("driveway", "driveway_archive"),
            ("access", "access_archive"),
        )
        made_archive = False
        for role, key in archive_roles:
            lyr = created.get(key)
            if lyr is None:
                continue
            remember(pcfg.archive, role, lyr)
            made_archive = True
        if made_archive:
            pcfg.archive.enabled = bool(watch)
        save_project_config(pcfg)
        with contextlib.suppress(Exception):  # the tables exist even if a drop-down cannot be wired yet
            apply_value_relations(pcfg)

        label = os.path.splitext(os.path.basename(target.path))[0] if target.path else (
            target.schema or target.connection or "schema")
        group = self._schema_group(prj, f"NENA {label}"[:80])
        try:
            self.iface.currentLayerChanged.disconnect(self._follow_active_layer)
        except (TypeError, RuntimeError):
            pass
        try:
            # Archive layers first, then streets, so centerlines are added last and sit on top.
            order = [key for _role, key in archive_roles]
            order += [key for key, _table, _title, _role in LOOKUP_SPECS]
            order += ["streets", "driveway", "access", "address", "centerline"]
            for key in order:
                lyr = created.get(key)
                if lyr is None:
                    continue
                prj.addMapLayer(lyr, False)
                group.addLayer(lyr)
        finally:
            self.iface.currentLayerChanged.connect(self._follow_active_layer)
        self.streets.load()
        self.addresses.load()
        self.lookups.load()
        if created.get("centerline") is not None:
            self.cb_layer.setLayer(created["centerline"])
        # New layers get the NENA look straight away.
        fresh = {key: created.get(key) for key in ("centerline", "address", "driveway", "access")
                 if created.get(key) is not None}
        if fresh:
            self.style_layers(only=fresh, ask=False)
        self.sync_archive_option()
        self.projectConfigSaved.emit()

    def _schema_group(self, prj, name):
        from qgis.core import QgsLayerTreeGroup
        for child in prj.layerTreeRoot().children():
            if isinstance(child, QgsLayerTreeGroup) and child.name() == name:
                return child
        return prj.layerTreeRoot().insertGroup(0, name)

    def new_template(self):
        def do():
            crs = QgsProject.instance().crs()
            self.collect()
            tpl = self.cfg.template
            lyr = create_template_layer(crs.authid() or "EPSG:4326", template=tpl)
            cfg = C.PluginConfig()
            cfg.template = dict(tpl) if tpl else {}
            cfg.mapping = default_mapping_for_template(tpl)
            C.save_to_layer(lyr, cfg)
            QgsProject.instance().addMapLayer(lyr)
            self.cb_layer.setLayer(lyr)
            self._msg("Template layer created in memory – use Export > Save Features As… "
                      "to write it to a GeoPackage.", "Success")
        self._guard(do)

    # ------------------------------------------------------------------ field templates
    def load_template_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load NENA field template", self._last_dir(),
            "Templates (*.gpkg *.shp *.geojson *.json *.csv *.sqlite *.gml *.fgb);;All files (*)")
        if not path:
            return

        def choose(names):
            name, ok = QInputDialog.getItem(self, TITLE, "This file contains several layers.\n"
                                            "Which one is the road centerline template?", names, 0, False)
            return names.index(name) if ok else None

        def do():
            tpl = T.load_file(path, choose)
            self._remember_dir(path)
            self._apply_template(tpl)
        self._guard(do)

    def load_template_layer(self):
        layers = [lyr for lyr in QgsProject.instance().mapLayers().values() if isinstance(lyr, QgsVectorLayer)]
        if not layers:
            self._msg("No vector layers in the project.", "Info")
            return
        names = [f"{lyr.name()}  [{lyr.providerType()}]" for lyr in layers]
        name, ok = QInputDialog.getItem(self, TITLE, "Use the fields of this layer as the template:", names, 0, False)
        if ok:
            self._guard(lambda: self._apply_template(T.from_vector_layer(layers[names.index(name)])))

    def _apply_template(self, tpl: dict):
        self.collect()
        self.cfg.template = tpl
        self.mapping.load(self.cfg)
        self.mapping.apply_auto_map()
        self.mapping.store(self.cfg)
        info = T.summary(tpl)
        text = (f"Template '{tpl['name']}' loaded: {info['fields']} field(s), {info['matched']} matched to NENA "
                f"attributes, {info['extra']} extra.")
        if self.layer is not None:
            names = [fd.name for fd in template_field_defs(tpl) if not self.cfg.mapping.get(fd.name)]
            if names and QMessageBox.question(
                    self, TITLE, text + f"\n\n{len(names)} template field(s) are missing on "
                    f"'{self.layer.name()}'. Add them now?\n\n" + ", ".join(names[:40])
                    + (" …" if len(names) > 40 else "")) == QMessageBox.StandardButton.Yes:
                added = add_missing_fields(self.layer, self.cfg, names)
                self.mapping.set_layer_fields([f.name() for f in self.layer.fields()])
                self.mapping.load(self.cfg)
                text += f" {len(added)} field(s) added (in the edit buffer)."
            self._persist()
        self._msg(text, "Success")

    def reset_template(self):
        self.collect()
        self.cfg.template = {}
        self.mapping.load(self.cfg)
        if self.layer is not None:
            self._persist()
        self._msg("Using the built-in NENA-STA-006.2 schema.", "Info")

    def export_template(self):
        self.collect()
        tpl = self.cfg.template or T.builtin_template()
        path, _ = QFileDialog.getSaveFileName(self, "Export field template",
                                              os.path.join(self._last_dir(), "nena_rcl_template.json"),
                                              "JSON (*.json)")
        if path:
            self._guard(lambda: (T.save_json(path, tpl), self._remember_dir(path),
                                 self._msg(f"Template written to {path}", "Success")))

    # ------------------------------------------------------------------ ranging
    def run_ranging(self, selected_only: bool, overrides=None, command="NENA: auto-range"):
        def do():
            layer = self._require_layer()
            self._persist()
            if not selected_only and overrides is None:
                if QMessageBox.question(self, TITLE, f"Range all {layer.featureCount()} feature(s) of "
                                        f"'{layer.name()}'?") != QMessageBox.StandardButton.Yes:
                    return
            engine = RangeEngine(layer, self.cfg)
            report = self._with_progress(lambda cb: engine.run(selected_only, overrides, cb, command))
            if report is not None:
                self._msg(report.summary(), "Success" if report.updated else "Info")
                layer.triggerRepaint()
        self._guard(do)

    def redistribute_selected(self):
        self.run_ranging(True, {"method": C.M_PROPORTIONAL, "proportional_source": C.PROP_EXISTING,
                                "scope": C.SCOPE_CHAIN, "overwrite_existing": True},
                         command="NENA: redistribute ranges")

    def reverse_selected(self):
        """Reverse the selected centerlines. Sides, ranges, and linked address sides follow."""
        def do():
            from ..core.addressing.ssap import ACCESS_FIELDS
            from ..core.direction import reverse_centerlines
            from ..core import lookups as L
            layer = self._require_layer()
            fids = list(layer.selectedFeatureIds())
            if not fids:
                raise ConfigError(f"Select the centerlines to reverse on '{layer.name()}' first. "
                                  "The green dot is where a line starts and the red square is where it ends.")
            pcfg = load_project_config()
            linked = []
            maps = L.field_maps(pcfg, L.feature_layers(pcfg, layer))
            addr = layer_for(pcfg.streets, "address")
            am = maps.get("address", {})
            if addr is not None and am.get("Addr_Side") and am.get("RCL_NGUID"):
                linked.append((addr, am["Addr_Side"], am["RCL_NGUID"]))
            access = layer_for(pcfg.addresses, "access")
            if access is not None:
                xm = L.resolve_fields([f.name() for f in access.fields()], ACCESS_FIELDS, {})
                if xm.get("RoadSide") and xm.get("RCL_NGUID"):
                    linked.append((access, xm["RoadSide"], xm["RCL_NGUID"]))
            box = QMessageBox(self)
            box.setWindowTitle("Reverse direction")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText(f"Reverse {len(fids)} centerline(s) on '{layer.name()}'?")
            rs = self.collect().ranging
            odd_even = {"O": "odd", "E": "even", "B": "both", "Z": "none"}
            box.setInformativeText(
                "The line will start at the other end, and left and right follow the new direction. "
                "Left/right values (ESN, ZIP, community) swap so they stay on the same side of the road, "
                "and address points and access points on these roads switch side to match.\n\n"
                "Reverse and re-range (usual): give the roads new ranges in the new direction, with "
                f"{odd_even.get(rs.left_parity, rs.left_parity)} numbers on the left and "
                f"{odd_even.get(rs.right_parity, rs.right_parity)} on the right.\n\n"
                "Reverse only: keep the existing numbers. Each side's From/To and parity move with it, "
                "so they now read the other way.")
            rerange = box.addButton("Reverse and re-range", QMessageBox.ButtonRole.AcceptRole)
            only = box.addButton("Reverse only", QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(rerange)
            box.exec()
            clicked = box.clickedButton()
            if clicked not in (only, rerange):
                return
            self._persist()
            defaults = {"L": rs.left_parity, "R": rs.right_parity} if clicked is rerange else None
            result = reverse_centerlines(layer, self.collect(), fids, linked, parity_defaults=defaults)
            text = f"Reversed {result['lines']} centerline(s)."
            if result["linked"]:
                text += f" {result['linked']} address/access point side(s) switched to match."
            self._msg(text + " Undo puts them back. Save the layers to keep it.", "Success")
            if clicked is rerange:
                self.run_ranging(True)
        self._guard(do)

    def set_one_way_selected(self, value):
        """Mark the selected centerlines two-way, one-way FT or TF, or clear it. Used for routing."""
        def do():
            from ..core.direction import set_one_way
            layer = self._require_layer()
            fids = list(layer.selectedFeatureIds())
            if not fids:
                raise ConfigError(f"Select the centerlines on '{layer.name()}' to mark first.")
            self._persist()
            try:
                n = set_one_way(layer, self.collect(), fids, value)
            except ConfigError as exc:
                if str(exc) != "NO_ONEWAY_FIELD":
                    raise
                if QMessageBox.question(self, TITLE, f"'{layer.name()}' has no OneWay field matched. "
                                        "Add the NENA OneWay field now?") != QMessageBox.StandardButton.Yes:
                    return
                add_missing_fields(layer, self.collect(), ["OneWay"])
                self._persist()
                n = set_one_way(layer, self.collect(), fids, value)
            label = {"B": "two-way (B)", "FT": "one-way From → To (FT)", "TF": "one-way To → From (TF)"}
            self._msg(f"{n} centerline(s) marked {label.get(value, 'with no one-way value')}. "
                      "Red arrows show the direction of travel once the layer has the NENA style. "
                      "Save the layer to keep it.", "Success")
            layer.triggerRepaint()
        self._guard(do)

    def style_layers(self, only=None, ask=True):
        """Give the NENA layers road / address-point styles with direction arrows and labels."""
        def do():
            from ..core import styles as S
            pcfg = load_project_config()
            layers = only if only is not None else S.nena_layers(pcfg, self.layer)
            names = [lyr.name() for lyr in layers.values() if lyr is not None]
            if not names:
                raise ConfigError("Choose the centerline layer (and the address layer on the Addresses window) "
                                  "first, so the plugin knows which layers to style.")
            if ask and QMessageBox.question(
                    self, TITLE,
                    "Apply the NENA map style to these layers?\n\n" + "\n".join(names)
                    + "\n\nRoads get a road look with arrows showing their direction, a green dot where each "
                      "line starts and a red square where it ends. Address points become houses with their "
                      "number.\n\nYour current style is kept. Switch back any time: right-click the layer → "
                      "Styles.") != QMessageBox.StandardButton.Yes:
                return
            done = S.apply_styles(pcfg, self.layer, layers)
            with contextlib.suppress(Exception):  # the legend refreshes on its own later
                view = self.iface.layerTreeView()
                for lyr in layers.values():
                    if lyr is not None:
                        view.refreshLayerSymbology(lyr.id())
            if ask:
                self._msg(f"Styled {len(done)} layer(s): " + ", ".join(done) + ".", "Success")
        self._guard(do)

    # ------------------------------------------------------------------ edit tools
    def apply_policies(self):
        def do():
            layer = self._require_layer()
            self._persist()
            n = apply_split_policies(layer, self.cfg)
            if n:
                self._msg(f"Split policies set on {n} mapped field(s).", "Success")
            else:
                self._msg("This QGIS version does not support field split policies; "
                          "set them in Layer Properties > Attributes Form.", "Warning")
        self._guard(do)

    def populate_ids(self, selected_only: bool):
        def do():
            layer = self._require_layer()
            self._persist()
            n_ids, n_ag = populate_ids(layer, self.cfg, selected_only)
            self._msg(f"{n_ids} NGUID(s) and {n_ag} DiscrpAgID value(s) written.", "Success")
        self._guard(do)

    # ------------------------------------------------------------------ validation
    def run_validation(self, selected_only: bool):
        def do():
            layer = self._require_layer()
            self._persist()
            v = Validator(layer, self.cfg)
            issues = self._with_progress(lambda cb: v.run(selected_only, cb))
            if issues is not None:
                self.validation.set_issues(issues)
                self.open_page(self.validation)
                if self.cfg.validation.select_overlaps and self.cfg.validation.check_overlaps:
                    fids = V.overlap_fids(issues)
                    if fids:
                        layer.selectByIds(fids)
                        self._msg(f"{len(fids)} feature(s) with overlapping address ranges selected.", "Warning")
        self._guard(do)

    def find_overlaps(self, selected_only: bool = False):
        """Run only the overlap check and select every feature involved."""
        def do():
            layer = self._require_layer()
            self._persist()
            v = Validator(layer, self.cfg)
            issues = self._with_progress(lambda cb: v.run(selected_only, cb, only={"check_overlaps"}))
            if issues is None:
                return
            self.validation.set_issues(issues)
            self.open_page(self.validation)
            fids = V.overlap_fids(issues)
            layer.selectByIds(fids)
            if fids:
                self._msg(f"{len(fids)} feature(s) with overlapping address ranges selected "
                          f"({sum(1 for i in issues if i.code in V.OVERLAP_CODES)} overlap(s)).", "Warning")
            else:
                self._msg("No overlapping address ranges found.", "Success")
        self._guard(do)

    def select_fids(self, fids):
        if self.layer is not None:
            self.layer.selectByIds(list(fids))

    def zoom_fids(self, fids):
        if self.layer is None or not fids:
            return
        self.layer.selectByIds(list(fids))
        canvas = self.iface.mapCanvas()
        canvas.zoomToSelected(self.layer)
        if len(fids) == 1:
            ext = QgsRectangle(canvas.extent())
            ext.scale(1.5)
            canvas.setExtent(ext)
        canvas.refresh()

    def export_issues_csv(self):
        if not self.validation.issues:
            self._msg("Run a validation first.", "Info")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export issues", os.path.join(self._last_dir(),
                                              "nena_rcl_issues.csv"), "CSV (*.csv)")
        if path:
            self._guard(lambda: (export_csv(path, self.validation.issues, self.layer.name() if self.layer else ""),
                                 self._remember_dir(path), self._msg(f"Issues written to {path}", "Success")))

    def load_issues_layer(self):
        if not self.validation.issues or self.layer is None:
            self._msg("Run a validation first.", "Info")
            return
        self._guard(lambda: QgsProject.instance().addMapLayer(issues_layer(self.layer, self.validation.issues)))

    def fill_defaults(self):
        def do():
            layer = self._require_layer()
            cfg = self.collect()
            self._persist()
            fids = layer.selectedFeatureIds() or None
            n = fill_empty_defaults(layer, cfg, fids)
            where = "selected centerline(s)" if fids else "centerline(s)"
            self._msg(f"Filled empty fields on {n} {where}.", "Success")
        self._guard(do)

    def fill_range_gaps(self):
        def do():
            layer = self._require_layer()
            cfg = self.collect()
            self._persist()
            fids = layer.selectedFeatureIds() or None
            n = fill_range_gaps(layer, cfg, fids)
            self._msg(f"Closed the range gap on {n} centerline(s).", "Success" if n else "Info")
        self._guard(do)

    def split_existing_roads(self):
        def do():
            layer = self._require_layer()
            cfg = self.collect()
            self._persist()
            pcfg = load_project_config()
            fids = layer.selectedFeatureIds() or None
            rep = split_existing_intersections(layer, cfg, fids)
            extra = ""
            if rep["affected"] and pcfg.addresses.attribution_rules:
                ctx = attribution_context(layer, cfg, pcfg)
                n, crossings = attribute_centerline_sides(ctx, rep["affected"])
                extra = f" Calculated the side of {n} section(s)."
                if crossings:
                    extra += f" {len(crossings)} still change partway along."
            if not rep["split"]:
                self._msg("No selected road meets another road in the middle.", "Info")
                return
            self._msg(f"Split {rep['split']} road(s) at intersections, "
                      f"{rep['new_pieces']} new section(s).{extra}", "Success")
        self._guard(do)

    def split_where_side_changes(self):
        def do():
            layer = self._require_layer()
            cfg = self.collect()
            self._persist()
            pcfg = load_project_config()
            # polygon picks live on the project, and Settings, Sides may have been edited
            self.addresses.store()
            pcfg = load_project_config()
            fids = layer.selectedFeatureIds() or None
            rep = split_where_side_changes(layer, cfg, pcfg, fids)
            if not rep["split"]:
                note = f" {rep['skipped'][0]}" if rep["skipped"] else ""
                self._msg("No road changes side along its length." + note, "Info")
                return
            self._msg(f"Split {rep['split']} road(s) where the side changes, {rep['pieces']} new section(s). "
                      f"{rep['addresses']} address(es) copied from the new side.", "Success")
        self._guard(do)

    def compare_msag(self):
        def do():
            layer = self._require_layer()
            cfg = self.collect()
            self.validation.write_msag()
            pcfg = load_project_config()
            issues = compare_msag(layer, cfg, pcfg.msag)
            self.validation.set_issues(issues)
            if issues:
                self._msg(f"MSAG comparison found {len(issues)} difference(s).", "Info")
            else:
                self._msg("Centerlines match the MSAG extract.", "Success")
        self._guard(do)

    # ------------------------------------------------------------------ helpers
    def make_progress(self):
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setVisible(False)
        self._progress_bars.append(bar)
        return bar

    def make_layer_row(self):
        """A centerline picker synced with the one on Settings, plus Save."""
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("Centerline"))
        combo = QgsMapLayerComboBox()
        combo.setFilters(line_layer_filter())
        combo.setAllowEmptyLayer(True)
        combo.setExceptedLayerList(list(self.cb_layer.exceptedLayerList()))
        if self.cb_layer.currentLayer() is not None:
            combo.setLayer(self.cb_layer.currentLayer())
        combo.layerChanged.connect(lambda lyr, source=combo: self._mirror_from(source, lyr))
        self.cb_layer.layerChanged.connect(lambda lyr, target=combo: self._mirror_to(target, lyr))
        self._layer_mirrors.append(combo)
        lay.addWidget(combo, 1)
        return row

    def _mirror_from(self, source, layer):
        if self._layer_sync:
            return
        self._layer_sync = True
        try:
            if self.cb_layer.currentLayer() != layer:
                self.cb_layer.setLayer(layer)
            for combo in self._layer_mirrors:
                if combo is not source and combo.currentLayer() != layer:
                    combo.setLayer(layer)
        finally:
            self._layer_sync = False

    def _mirror_to(self, combo, layer):
        if self._layer_sync:
            return
        self._layer_sync = True
        try:
            if combo.currentLayer() != layer:
                combo.setLayer(layer)
        finally:
            self._layer_sync = False

    def wrap_page(self, page, with_layer_row):
        wrap = Surface()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(4, 4, 4, 4)
        if with_layer_row:
            lay.addWidget(self.make_layer_row())
        lay.addWidget(page, 1)
        lay.addWidget(self.make_progress())
        return wrap

    def _with_progress(self, func):
        bars = list(self._progress_bars) or [self.progress]
        for bar in bars:
            bar.setValue(0)
            bar.setVisible(True)

        def cb(pct):
            value = max(0, min(100, int(pct)))
            for bar in bars:
                bar.setValue(value)
            QCoreApplication.processEvents()
        try:
            return func(cb)
        finally:
            for bar in bars:
                bar.setVisible(False)

    def _guard(self, func):
        try:
            func()
        except ConfigError as exc:
            self._msg(str(exc), "Warning")
        except Exception as exc:  # noqa: BLE001 - surface anything unexpected to the user
            import traceback
            from qgis.core import QgsMessageLog
            QgsMessageLog.logMessage(traceback.format_exc(), TITLE, msg_level("Critical"))
            self._msg(f"Unexpected error: {exc} (details in the Log Messages panel)", "Critical")

    def _msg(self, text, level="Info"):
        self.iface.messageBar().pushMessage(TITLE, text, level=msg_level(level), duration=6)

    def _last_dir(self):
        return QSettings().value("nena_rcl_tools/last_dir", os.path.expanduser("~"))

    def _remember_dir(self, path):
        QSettings().setValue("nena_rcl_tools/last_dir", os.path.dirname(path))

    def current_cfg_auto_split(self) -> bool:
        return bool(self.cfg.edit.auto_rerange_split)

    def set_auto_split(self, enabled: bool):
        if self.layer is None:
            return
        self.edit.chk_split.setChecked(enabled)
        self._persist()

