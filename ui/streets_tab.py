# -*- coding: utf-8 -*-
"""Streets tab: master street names table, uniqueness rule, build / rename / assign / sync."""

from qgis.core import QgsFeatureRequest, QgsProject
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
                                 QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
                                 QTableWidgetItem, QVBoxLayout, QWidget)

from ..compat import table_layer_filter
from ..core.layer_access import ConfigError
from ..core.project_config import (StreetTypeSettings, layer_for, load_project_config, remember,
                                   save_project_config)
from ..core.streets import names as N
from ..core.streets.street_types import merged_abbreviations, type_list
from ..core.streets import repository as REPO
from ..core.addressing import engine as E
from ..core.streets import dbsql
from ..core.streets.relations import setup_relations
from ..core.streets.listing import build_rows
from ..core.streets.service import StreetService, add_link_field
from .address_dialogs import ReaddressDialog, SqlDialog
from .street_dialogs import BuildDialog, IssuesDialog, NameDialog, StreetTypesDialog
from .street_table import StreetTableView
from .widgets import FittingScroll, combo, hint, set_combo_data

SCOPE_FIELDS = [("MSAG community (MSAGComm_L)", "MSAGComm_L"), ("Incorporated municipality (IncMuni_L)", "IncMuni_L"),
                ("Postal community (PostComm_L)", "PostComm_L"), ("ESN (ESN_L)", "ESN_L"),
                ("County (County_L)", "County_L")]


class StreetsTab(QWidget):

    def __init__(self, dock, parent=None):
        super().__init__(parent)
        self.dock = dock
        self.pcfg = load_project_config()
        outer = QVBoxLayout(self)
        scroll = FittingScroll()
        body = QWidget()
        lay = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        top = QHBoxLayout()
        top.addWidget(hint("One list of road names. Roads and addresses link to it by <b>StreetID</b>. "
                           "Renaming a name here updates every linked feature."), 1)
        b = QPushButton("Settings…")
        b.setToolTip("The master table, the link field, and the street-name rules")
        b.clicked.connect(lambda *_: self.dock.open_settings("Street rules"))
        top.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(top)

        # -- layers --------------------------------------------------------
        g = QGroupBox("Layers")
        f = QFormLayout(g)
        row = QHBoxLayout()
        self.cb_master = QgsMapLayerComboBox()
        self.cb_master.setFilters(table_layer_filter())
        self.cb_master.setAllowEmptyLayer(True)
        row.addWidget(self.cb_master, 1)
        b = QPushButton("Create ▾")
        m = QMenu(b)
        a = m.addAction("Create full NENA schema…")
        a.triggered.connect(lambda *_: self.dock.create_schema())
        a = m.addAction("Import into schema…")
        a.triggered.connect(lambda *_: self.dock.import_schema())
        a = m.addAction("Create version tables…")
        a.triggered.connect(lambda *_: self.dock.create_archive_schema())
        m.addSeparator()
        a = m.addAction("New table in a GeoPackage…")
        a.triggered.connect(lambda *_: self.create_gpkg())
        a = m.addAction("New table in a database connection…")
        a.triggered.connect(lambda *_: self.create_db())
        a = m.addAction("Add missing fields to the chosen table")
        a.triggered.connect(lambda *_: self.add_master_fields())
        b.setMenu(m)
        row.addWidget(b)
        f.addRow("Master street names", row)
        self.lbl_addr = QLabel()
        f.addRow("Address points", self.lbl_addr)
        row = QHBoxLayout()
        self.ed_link = QLineEdit()
        row.addWidget(self.ed_link, 1)
        b = QPushButton("Add to layers")
        b.setToolTip("Add the link field to the centerline and address layers if missing")
        b.clicked.connect(lambda *_: self.add_link_fields())
        row.addWidget(b)
        f.addRow("Link field", row)
        f.addRow(hint("The centerline is chosen at the top of the tool windows."))
        self.layers_group = g                       # shown on Settings, Layers

        # -- uniqueness (shown on Settings) --------------------------------
        g = QGroupBox("Name uniqueness")
        f = QFormLayout(g)
        self.cb_scope = combo([("County-wide", "county"), ("Per field value", "field")])
        self.cb_scope_field = combo(SCOPE_FIELDS)
        self.cb_scope.currentIndexChanged.connect(
            lambda *_: self.cb_scope_field.setEnabled(self.cb_scope.currentData() == "field"))
        self.cb_strict = combo([("Full road name (directionals, name, types)", N.STRICT_FULL),
                                ("Ignore street type (Oak St = Oak Ave)", N.STRICT_IGNORE_TYPE)])
        self.chk_sound = QCheckBox("Warn about sound-alike names (Smith / Smyth)")
        self.cb_action = combo([("Block duplicates", "block"), ("Warn only", "warn")])
        f.addRow("Scope", self.cb_scope)
        f.addRow("Scope field", self.cb_scope_field)
        f.addRow("Compare", self.cb_strict)
        f.addRow(self.chk_sound)
        f.addRow("Duplicates", self.cb_action)
        b = QPushButton("Street types…")
        b.setToolTip("NENA and USPS street types for the name form. Add local types and abbreviations here.")
        b.clicked.connect(self.edit_types)
        f.addRow(b)
        self.uniqueness_group = g
        self.street_types = StreetTypeSettings()

        g = QGroupBox("When names change")
        v = QVBoxLayout(g)
        self.chk_alias = QCheckBox("Keep the old name as a retired alias when renaming")
        self.chk_cascade = QCheckBox("Edits typed into the master table cascade to linked features")
        self.chk_copy = QCheckBox("Picking a StreetID on a feature copies the name parts")
        self.chk_stamp = QCheckBox("Set DateUpdate on changed features")
        for w in (self.chk_alias, self.chk_cascade, self.chk_copy, self.chk_stamp):
            v.addWidget(w)
        self.behaviour_group = g

        grid = QGridLayout()
        actions = (
            ("Build from roads…", "Create the master list from the names on the centerlines", self.build),
            ("Sync to features", "Copy master names onto every linked feature", self.sync),
            ("Check names", "Compare centerlines and addresses with the master list", self.check),
            ("StreetID drop-down", "StreetID becomes a drop-down of master names in forms", self.relations),
            ("Database SQL…", "PostgreSQL: foreign keys and triggers that enforce the links", self.db_sql),
        )
        for i, (text, tip, slot) in enumerate(actions):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda *_, s=slot: s())
            grid.addWidget(btn, i // 2, i % 2)
        lay.addLayout(grid)

        # -- street list ---------------------------------------------------
        g = QGroupBox("Streets")
        v = QVBoxLayout(g)
        row = QHBoxLayout()
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("Search names…")
        self.ed_search.textChanged.connect(lambda *_: self._filter())
        self.chk_retired = QCheckBox("Show retired")
        self.chk_retired.toggled.connect(lambda *_: self._filter())
        b = QPushButton("Refresh")
        b.clicked.connect(lambda *_: self.refresh_list())
        row.addWidget(self.ed_search, 1)
        row.addWidget(self.chk_retired)
        row.addWidget(b)
        v.addLayout(row)
        self.table = StreetTableView(min_height=180, max_height=320)
        self.table.doubleClicked.connect(lambda *_: self.rename())
        v.addWidget(self.table)
        grid = QGridLayout()
        for i, (text, tip, slot) in enumerate((
                ("Add…", "Add a street name", self.add),
                ("Rename…", "Rename the selected street. Linked features follow.", self.rename),
                ("Retire", "Mark the selected street retired", self.retire),
                ("Assign to selected", "Write this street onto the selected centerlines", self.assign),
                ("Select roads", "Select the centerlines that use this street", self.select),
                ("Draw road", "Draw a new centerline for this street. Right-click to finish: it is named "
                              "and ranged by distance automatically.", self.draw))):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda *_, s=slot: s())
            grid.addWidget(btn, i // 2, i % 2)
        v.addLayout(grid)
        lay.addWidget(g)
        lay.addStretch(1)
        self.load()

    # ------------------------------------------------------------------ settings
    def load(self):
        self.pcfg = load_project_config()
        s = self.pcfg.streets
        self.cb_master.setLayer(layer_for(s, "master"))
        addr = layer_for(s, "address")
        self.lbl_addr.setText(addr.name() if addr else "<i>none (Addresses window)</i>")
        self.ed_link.setText(s.street_id_field)
        set_combo_data(self.cb_scope, s.scope_mode)
        set_combo_data(self.cb_scope_field, s.scope_field or "MSAGComm_L")
        self.cb_scope_field.setEnabled(s.scope_mode == "field")
        set_combo_data(self.cb_strict, s.strictness)
        self.chk_sound.setChecked(s.soundalike)
        set_combo_data(self.cb_action, s.action)
        self.chk_alias.setChecked(s.keep_alias_on_rename)
        self.chk_cascade.setChecked(s.cascade_direct_edits)
        self.chk_copy.setChecked(s.copy_on_assign)
        self.chk_stamp.setChecked(s.stamp_date_update)
        st = self.pcfg.street_types
        self.street_types = StreetTypeSettings(custom=st.custom, types=list(st.types),
                                               abbreviations=dict(st.abbreviations))
        self.refresh_list()

    def store(self):
        self.pcfg = load_project_config()          # keep what the Addresses tab saved
        s = self.pcfg.streets
        remember(s, "master", self.cb_master.currentLayer())
        if self.dock.layer is not None:
            remember(s, "centerline", self.dock.layer)
        s.street_id_field = self.ed_link.text().strip() or "StreetID"
        s.scope_mode = self.cb_scope.currentData()
        s.scope_field = self.cb_scope_field.currentData() if s.scope_mode == "field" else ""
        s.strictness = self.cb_strict.currentData()
        s.soundalike = self.chk_sound.isChecked()
        s.action = self.cb_action.currentData()
        s.keep_alias_on_rename = self.chk_alias.isChecked()
        s.cascade_direct_edits = self.chk_cascade.isChecked()
        s.copy_on_assign = self.chk_copy.isChecked()
        s.stamp_date_update = self.chk_stamp.isChecked()
        kept = self.street_types
        self.pcfg.street_types = StreetTypeSettings(custom=kept.custom, types=list(kept.types),
                                                    abbreviations=dict(kept.abbreviations))
        save_project_config(self.pcfg)
        if getattr(self.dock, "street_watchers", None) is not None:
            self.dock.street_watchers.refresh()
        if hasattr(self.dock, "addresses"):
            self.dock.addresses.configSaved.emit()
        return self.pcfg

    def save(self):
        self.dock._guard(lambda: (self.store(), self.dock._msg("Street-name settings saved in the project.",
                                                                 "Success")))

    def service(self) -> StreetService:
        self.store()
        if self.dock.layer is None:
            raise ConfigError("Choose the centerline layer at the top of the window first.")
        return StreetService(self.pcfg, centerline_layer=self.dock.layer, centerline_cfg=self.dock.collect())

    def edit_types(self):
        dlg = StreetTypesDialog(self, self.street_types)
        if dlg.exec():
            self.street_types = dlg.settings()
            self.dock.autosave()

    def _name_dialog(self, title, **kwargs):
        return NameDialog(self, title, street_types=type_list(self.street_types),
                          abbreviations=merged_abbreviations(self.street_types.abbreviations), **kwargs)

    # ------------------------------------------------------------------ table creation
    def create_gpkg(self):
        path, _ = QFileDialog.getSaveFileName(self, "GeoPackage for the street names", self.dock._last_dir(),
                                              "GeoPackage (*.gpkg)")
        if not path:
            return
        if not path.lower().endswith(".gpkg"):
            path += ".gpkg"

        def do():
            lyr = REPO.create_in_geopackage(path, REPO.DEFAULT_TABLE)
            QgsProject.instance().addMapLayer(lyr)
            self.cb_master.setLayer(lyr)
            self.store()
            self.dock._msg(f"Street names table created in {path}.", "Success")
        self.dock._guard(do)

    def create_db(self):
        conns = REPO.db_connections()
        if not conns:
            self.dock._msg("No database connections are configured in QGIS (Data Source Manager).", "Warning")
            return
        labels = [c[0] for c in conns]
        label, ok = QInputDialog.getItem(self, "Street names table", "Database connection:", labels, 0, False)
        if not ok:
            return
        _l, key, name = conns[labels.index(label)]
        schema, ok = QInputDialog.getText(self, "Street names table", "Schema (empty for none):",
                                          text="public" if key == "postgres" else "")
        if not ok:
            return
        table, ok = QInputDialog.getText(self, "Street names table", "Table name:", text=REPO.DEFAULT_TABLE)
        if not ok or not table.strip():
            return

        def do():
            lyr = REPO.create_in_connection(key, name, schema.strip(), table.strip())
            QgsProject.instance().addMapLayer(lyr)
            self.cb_master.setLayer(lyr)
            self.store()
            self.dock._msg(f"Street names table '{table}' created through '{name}'.", "Success")
        self.dock._guard(do)

    def add_master_fields(self):
        def do():
            lyr = self.cb_master.currentLayer()
            if lyr is None:
                raise ConfigError("Choose a table first.")
            added = REPO.StreetRepository(lyr).add_missing_fields()
            self.dock._msg(f"Added {len(added)} field(s)." if added else "The table already has every field.",
                           "Success")
        self.dock._guard(do)

    def add_link_fields(self):
        def do():
            name = self.ed_link.text().strip() or "StreetID"
            done = []
            for lyr in (self.dock.layer, layer_for(self.pcfg.streets, "address")):
                if lyr is not None and add_link_field(lyr, name):
                    done.append(lyr.name())
            if self.dock.layer is not None:
                self.dock.mapping.set_layer_fields([f.name() for f in self.dock.layer.fields()])
                self.dock.cfg.mapping["StreetID"] = name
                self.dock.mapping.load(self.dock.cfg)
                self.dock._persist()
            self.store()
            self.dock._msg(f"'{name}' added to: {', '.join(done)} (save the layers to keep it)." if done
                           else f"'{name}' already exists on the layers.", "Success")
        self.dock._guard(do)

    # ------------------------------------------------------------------ actions
    def build(self):
        def do():
            svc = self.service()
            report = svc.analyse_centerlines()
            if not report.groups:
                self.dock._msg("No named centerlines found (check the St_* field mapping).", "Warning")
                return
            dlg = BuildDialog(self, report)
            if not dlg.exec():
                return
            chosen = {(g.scope, g.key): g.chosen for g in report.groups}
            rep = svc.build_from_centerlines(dlg.chk_rewrite.isChecked(), dlg.chk_addr.isChecked(),
                                             dlg.chk_overwrite.isChecked(), chosen)
            self.refresh_list()
            self.dock._msg(f"{rep.created} street name(s) created, {rep.reused} reused; linked "
                           f"{rep.linked_centerlines} centerline(s) and {rep.linked_addresses} address(es); "
                           f"{rep.rewritten} spelling(s) standardised. Save the layers to keep the changes.",
                           "Success")
        self.dock._guard(do)

    def relations(self):
        def do():
            svc = self.service()
            fname = self.pcfg.streets.street_id_field
            n = setup_relations(svc.master, [(ll.layer, ll.acc.fields.at(ll.acc.index["StreetID"]).name())
                                             for ll in svc.linked if ll.usable])
            self.dock._msg(f"StreetID is now a drop-down of master names on {n} layer(s)." if n else
                           f"No layer has the '{fname}' field yet – use 'Add to layers' first.",
                           "Success" if n else "Warning")
        self.dock._guard(do)

    def check(self):
        def do():
            svc = self.service()
            issues = {"master": svc.master_issues(), "centerline": svc.name_issues("centerline")}
            if svc.layer("address") is not None:
                issues["address"] = svc.name_issues("address")
            dlg = IssuesDialog(self, issues)
            layers = {"master": svc.master, **{ll.role: ll.layer for ll in svc.linked}}
            dlg.selectRequested.connect(lambda role, fids: layers[role].selectByIds(fids))
            dlg.exec()
        self.dock._guard(do)

    def sync(self):
        def do():
            res = self.service().sync()
            self.dock._msg("Names synced: " + ", ".join(f"{v} {k} feature(s)" for k, v in res.items()), "Success")
        self.dock._guard(do)

    # ------------------------------------------------------------------ list
    def refresh_list(self):
        keep = self.table.selected_sid()
        master = self.cb_master.currentLayer()
        if master is None:
            self.table.set_rows([])
            return
        try:
            self.pcfg.streets.street_id_field = self.ed_link.text().strip() or "StreetID"
            svc = StreetService(self.pcfg, centerline_layer=self.dock.layer,
                                centerline_cfg=self.dock.cfg if self.dock.layer else None, master_layer=master)
            rows = build_rows(svc.repo.records(), svc.counts())
        except ConfigError as exc:
            self.table.setToolTip(str(exc))      # e.g. a table without the master fields yet
            self.table.set_rows([])
            return
        self.table.setToolTip("")
        self.table.set_rows(rows)
        self._filter()
        if keep:
            self.table.select_sid(keep)

    def _filter(self):
        self.table.set_filter(self.ed_search.text(), self.chk_retired.isChecked())

    def _selected_sid(self):
        sid = self.table.selected_sid()
        if not sid:
            raise ConfigError("Select a street in the list first.")
        return sid

    def add(self):
        def do():
            svc = self.service()
            dlg = self._name_dialog("Add street name", scope_enabled=self.pcfg.streets.scope_mode == "field",
                                    check=lambda p, sc: svc.conflicts(p, sc))
            if not dlg.exec():
                return
            rec, warnings = svc.add_street(dlg.parts(), dlg.scope())
            self.refresh_list()
            self._select_sid(rec.street_id)
            self.dock._msg(f"Added '{rec.full_name}'." + (" Note: similar names exist." if warnings else ""),
                           "Success")
            if self.dock.layer is not None and QMessageBox.question(
                    self, "Draw road", f"Draw '{rec.full_name}' on the map now?"
                    ) == QMessageBox.StandardButton.Yes:
                self._start_draw(rec.street_id, rec.full_name)
        self.dock._guard(do)

    def _select_sid(self, sid):
        if not self.table.select_sid(sid) and self.ed_search.text():
            self.ed_search.clear()               # the new name may not match the search
            self.table.select_sid(sid)

    def draw(self):
        def do():
            svc = self.service()
            sid = self._selected_sid()
            rec = svc.repo.by_id().get(sid)
            if rec is None:
                raise ConfigError("Street not found.")
            self._start_draw(sid, rec.full_name)
        self.dock._guard(do)

    def _start_draw(self, sid, full_name):
        from .draw_street_tool import DrawStreetTool
        layer = self.dock._require_layer()
        svc = self.service()
        ll = svc.layer("centerline")
        if ll is None or not ll.usable:
            raise ConfigError(f"The centerline layer has no '{self.pcfg.streets.street_id_field}' field. "
                              "Use 'Add to layers' first.")
        if not layer.isEditable():
            layer.startEditing()
        iface = self.dock.iface
        iface.setActiveLayer(layer)
        self._draw_tool = DrawStreetTool(self.dock, layer, sid, full_name)
        iface.mapCanvas().setMapTool(self._draw_tool)
        self.dock._msg(f"Draw '{full_name}': click to add points, right-click to finish (Esc cancels).", "Info")

    def rename(self):
        def do():
            svc = self.service()
            sid = self._selected_sid()
            rec = svc.repo.by_id().get(sid)
            seg, adr = svc.rename_preview(sid)
            dlg = self._name_dialog(f"Rename '{rec.full_name}'", parts=rec.parts, scope=rec.scope,
                                    scope_enabled=self.pcfg.streets.scope_mode == "field",
                                    check=lambda p, sc: svc.conflicts(p, sc, exclude_ids=[sid]), show_alias=True,
                                    keep_alias=self.pcfg.streets.keep_alias_on_rename,
                                    info=f"The new name will be written to <b>{seg}</b> centerline(s) and "
                                         f"<b>{adr}</b> address point(s) that use this street.")
            if not dlg.exec():
                return
            new_name = N.full_name(dlg.parts())
            if QMessageBox.question(self, "Rename street",
                                    f"Rename '{rec.full_name}' to '{new_name}'?\n\n{seg} centerline(s) and {adr} "
                                    "address point(s) will be updated.") != QMessageBox.StandardButton.Yes:
                return
            res = svc.rename(sid, dlg.parts(), dlg.scope(), dlg.chk_alias.isChecked())
            self.refresh_list()
            self.dock._msg(f"Renamed to '{new_name}': {res.get('centerline', 0)} centerline(s), "
                           f"{res.get('address', 0)} address(es) updated. Save the layers to keep the changes.",
                           "Success")
        self.dock._guard(do)

    def retire(self):
        def do():
            svc = self.service()
            sid = self._selected_sid()
            rec = svc.repo.by_id().get(sid)
            seg, adr = svc.rename_preview(sid)
            if QMessageBox.question(self, "Retire street",
                                    f"Retire '{rec.full_name}'? It stays in the table for history and alias "
                                    f"searches.\n\n{seg} centerline(s) and {adr} address point(s) still use it – "
                                    "validation will flag them.") != QMessageBox.StandardButton.Yes:
                return
            svc.retire(sid)
            self.refresh_list()
        self.dock._guard(do)

    def assign(self):
        def do():
            svc = self.service()
            sid = self._selected_sid()
            fids = list(self.dock.layer.selectedFeatureIds())
            if not fids:
                raise ConfigError("Select centerlines on the map first.")
            rec = svc.repo.by_id().get(sid)
            if QMessageBox.question(self, "Assign street",
                                    f"Give {len(fids)} selected centerline(s) the name '{rec.full_name}'?"
                                    ) != QMessageBox.StandardButton.Yes:
                return
            # addresses along segments that change street = renaming part of a road
            ctx, affected = None, []
            ll = svc.layer("centerline")
            changing = [f.id() for f in ll.layer.getFeatures(QgsFeatureRequest().setFilterFids(fids))
                        if N.clean(ll.acc.get(f, "StreetID")) != sid]
            addr = svc.layer("address")
            if changing and addr is not None and addr.usable:
                try:
                    ctx = E.AddressContext(self.pcfg, self.dock.layer, self.dock.collect())
                    affected = [a for a in E.addresses_on_segments(ctx, changing)
                                if N.clean(addr.acc.get(next(addr.layer.getFeatures(
                                    QgsFeatureRequest().setFilterFid(a))), "StreetID")) != sid]
                except ConfigError:
                    affected = []
            n = svc.assign(sid, fids)
            msg = f"{n} centerline(s) now use '{rec.full_name}'."
            if affected:
                svc.assign(sid, affected, role="address")         # the name follows; numbers decided below
                rows = E.readdress(ctx, affected)
                dlg = ReaddressDialog(self, rows, rec.full_name)
                dlg.exec()
                chosen = dlg.selected()
                if chosen:
                    k = E.apply_readdress(ctx, chosen)
                    msg += f" {len(affected)} address(es) renamed, {k} re-addressed."
                else:
                    msg += f" {len(affected)} address(es) renamed, numbers kept."
            self.refresh_list()
            self.dock._msg(msg, "Success")
        self.dock._guard(do)

    def db_sql(self):
        def do():
            svc = self.service()
            if svc.master.providerType() != "postgres":
                raise ConfigError("Database-side integrity is generated for PostgreSQL/PostGIS tables. With a "
                                  "GeoPackage (or other databases) the plugin keeps the links itself.")
            ms, mt = dbsql.layer_table(svc.master)
            master = (ms, mt, REPO.F_ID, dbsql.part_columns(svc.master))
            linked = []
            for ll in svc.linked:
                if ll.usable and ll.layer.providerType() == "postgres":
                    s_, t_ = dbsql.layer_table(ll.layer)
                    linked.append((s_, t_, ll.acc.fields.at(ll.acc.index["StreetID"]).name(),
                                   dbsql.part_columns(ll.layer, {p: ll.acc.fields.at(ll.acc.index[p]).name()
                                                                 for p in N.NAME_PARTS if ll.acc.has(p)})))
            if not linked:
                raise ConfigError("No linked PostgreSQL centerline/address table with a StreetID field.")
            sql = dbsql.build_sql(master, linked)
            dlg = SqlDialog(self, sql, can_run=True)
            dlg.exec()
            if dlg.run:
                dbsql.run_sql(svc.master, sql)
                self.dock._msg("Integrity constraints and triggers created in the database.", "Success")
        self.dock._guard(do)

    def select(self):
        def do():
            svc = self.service()
            sid = self._selected_sid()
            ll = svc.layer("centerline")
            if ll is None or not ll.usable:
                raise ConfigError("The centerline layer has no StreetID field.")
            fids = [f.id() for f in ll.layer.getFeatures() if N.clean(ll.acc.get(f, "StreetID")) == sid]
            ll.layer.selectByIds(fids)
            if fids:
                self.dock.iface.mapCanvas().zoomToSelected(ll.layer)
        self.dock._guard(do)
