# -*- coding: utf-8 -*-
"""Create a fresh NENA schema, and import existing layers into one."""

import os

from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes
from qgis.gui import QgsProjectionSelectionWidget
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (QCheckBox, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
                                 QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
                                 QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.importer import destination_fields, geometry_problem, source_field_names
from ..core.schema_pack import (ARCHIVE_COLUMNS, CREATABLE_LAYERS, SCHEMA_LAYERS, ZONE_SCHEMA_LAYERS, VERSION_TABLE_SUFFIX, defs_for,
                               qualified_table, suggest_field_map)
from ..core.schema_writer import SchemaTarget
from ..core.tables import db_connections
from .widgets import NormalDialog, combo, hint, set_combo_data

TITLE = "NENA Addressing and RCL Tools"
_DEFAULT_SCHEMA = {"postgres": "public", "mssql": "dbo"}


def _layer_label(layer) -> str:
    try:
        geom = QgsWkbTypes.displayString(layer.wkbType())
    except Exception:  # noqa: BLE001
        geom = layer.providerType()
    return f"{layer.name()}  ({geom})"


class CreateSchemaDialog(NormalDialog):
    """Pick a GeoPackage, File Geodatabase, or database connection and the layers to create."""

    def __init__(self, parent, crs, last_dir, archive_only=False):
        super().__init__(parent)
        self._archive_only = bool(archive_only)
        self.setWindowTitle("Create version tables" if self._archive_only else "Create NENA schema")
        self._dir = last_dir or os.path.expanduser("~")
        self._result = None
        lay = QVBoxLayout(self)
        if self._archive_only:
            intro = ("Creates a version table for each layer you check. Each saved insert, update, or delete "
                     "adds one version of that feature, with the old and new values of an update. "
                     "The table has the same fields as the live layer, plus the version columns.")
        else:
            intro = ("Creates empty road centerlines, street names, address points, and driveways. "
                     "A loaded field template under Settings, Fields is used for the centerlines, and a state "
                     "address template is used for the address points.")
        lay.addWidget(hint(intro))

        form = QFormLayout()
        self.cb_kind = combo([
            ("GeoPackage (.gpkg)", "gpkg"),
            ("File Geodatabase (.gdb)", "gdb"),
            ("Database connection (PostgreSQL, SQL Server, Oracle, …)", "conn"),
        ])
        self.cb_kind.currentIndexChanged.connect(lambda *_: self._show_kind())
        form.addRow("Destination", self.cb_kind)

        self.ed_path = QLineEdit()
        self.ed_path.setPlaceholderText(r"C:\data\nena.gpkg")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        self._file_row = QWidget()
        row = QHBoxLayout(self._file_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.ed_path, 1)
        row.addWidget(browse)
        self.lbl_path = QLabel("File")
        form.addRow(self.lbl_path, self._file_row)

        self.cb_conn = combo([])
        self._load_connections()
        self.cb_conn.currentIndexChanged.connect(lambda *_: self._conn_changed())
        self.ed_schema = QLineEdit()
        self.ed_schema.setPlaceholderText("public for PostgreSQL, dbo for SQL Server")
        self._conn_changed()
        self.lbl_conn = QLabel("Connection")
        self.lbl_dbschema = QLabel("Database schema")
        form.addRow(self.lbl_conn, self.cb_conn)
        form.addRow(self.lbl_dbschema, self.ed_schema)
        lay.addLayout(form)
        self._form = form
        self.lbl_schema_hint = hint("The database schema is created when it is not already there. "
                                    "Leave it empty when the connection has no schema (SpatiaLite).")
        lay.addWidget(self.lbl_schema_hint)

        form2 = QFormLayout()
        self.ed_prefix = QLineEdit()
        self.ed_prefix.setPlaceholderText("optional, for example ng911")
        self.ed_prefix.textChanged.connect(lambda *_: self._refresh_names())
        form2.addRow("Table prefix", self.ed_prefix)
        self.crs_widget = QgsProjectionSelectionWidget(self)
        self.crs_widget.setCrs(crs)
        form2.addRow("CRS", self.crs_widget)
        lay.addLayout(form2)

        self.chk_replace = QCheckBox("Replace tables that already exist")
        self.chk_replace.setToolTip("Overwrite tables that already have these names. "
                                    "Other tables in the file or database stay as they are.")
        lay.addWidget(self.chk_replace)

        archive_names = ", ".join(name for name, *_rest in ARCHIVE_COLUMNS)
        self.chk_archive = QCheckBox("Also create version tables")
        self.chk_archive.setToolTip("Add one version table per checked layer. "
                                    "The table name ends in _Versions.")
        self.chk_watch = QCheckBox("Track changes in those tables")
        self.chk_watch.setToolTip("After you save a centerline, street name, address, or driveway, "
                                  "add a version of that feature. The version is saved immediately.")
        self.lbl_archive = hint("Each row is one version of a feature: the feature as it was saved, the version "
                                "number, and, for an update, the old and new values. Columns: " + archive_names + ".")
        if self._archive_only:
            self.chk_archive.setChecked(True)
            self.chk_archive.setVisible(False)
            self.chk_watch.setChecked(True)
            self.lbl_archive.setVisible(True)
        else:
            self.chk_watch.setEnabled(False)
            self.lbl_archive.setVisible(False)
            self.chk_archive.toggled.connect(self._on_archive)
        lay.addWidget(self.chk_archive)
        lay.addWidget(self.chk_watch)
        lay.addWidget(self.lbl_archive)

        box = QGroupBox("Layers")
        box_lay = QVBoxLayout(box)
        self._checks = []
        specs = [spec for spec in CREATABLE_LAYERS if spec.versioned] if self._archive_only else CREATABLE_LAYERS
        for spec in specs:
            cb = QCheckBox(spec.title)
            cb.setChecked(spec.default_on)
            self._checks.append((cb, spec))
            box_lay.addWidget(cb)
        lay.addWidget(box)
        self._refresh_names()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.resize(560, 560)
        self._show_kind()

    def _on_archive(self, on):
        if self._archive_only:
            return
        self.chk_watch.setEnabled(on)
        if not on:
            self.chk_watch.setChecked(False)
        elif not self.chk_watch.isChecked():
            self.chk_watch.setChecked(True)
        self.lbl_archive.setVisible(on)
        self._refresh_names()

    def _load_connections(self):
        self.cb_conn.clear()
        conns = db_connections()
        if not conns:
            self.cb_conn.addItem("No database connections configured", None)
            return
        for label, key, name in conns:
            self.cb_conn.addItem(label, (key, name))

    def _conn_changed(self):
        data = self.cb_conn.currentData()
        if not data:
            return
        provider, _name = data
        current = self.ed_schema.text().strip()
        if current in ("", "public", "dbo"):
            self.ed_schema.setText(_DEFAULT_SCHEMA.get(provider, ""))

    def _show_kind(self):
        kind = self.cb_kind.currentData()
        file_dest = kind in ("gpkg", "gdb")
        self.lbl_path.setText("File Geodatabase" if kind == "gdb" else "GeoPackage")
        self._row(self.lbl_path, self._file_row, file_dest)
        database = kind == "conn"
        self._row(self.lbl_conn, self.cb_conn, database)
        self._row(self.lbl_dbschema, self.ed_schema, database)
        self.lbl_schema_hint.setVisible(database)

    def _row(self, label, field, visible):
        label.setVisible(visible)
        field.setVisible(visible)
        setter = getattr(self._form, "setRowVisible", None)
        if setter is not None:
            setter(label, visible)

    def _browse(self):
        kind = self.cb_kind.currentData()
        start = self.ed_path.text().strip() or self._dir
        if kind == "gdb":
            folder = start if os.path.isdir(start) else os.path.dirname(start) or self._dir
            path = QFileDialog.getExistingDirectory(self, "Folder for the File Geodatabase", folder)
            if not path:
                return
            if not path.lower().endswith(".gdb"):
                path = os.path.join(path, "nena.gdb")
            self.ed_path.setText(path)
            return
        path, _ = QFileDialog.getSaveFileName(self, "GeoPackage", os.path.join(self._dir, "nena.gpkg"),
                                              "GeoPackage (*.gpkg)")
        if path:
            self.ed_path.setText(path)

    def _table(self, base, suffix=""):
        try:
            return qualified_table(base, self.ed_prefix.text(), suffix)
        except ValueError:
            return base

    def _refresh_names(self):
        include_archive = self._archive_only or self.chk_archive.isChecked()
        for cb, spec in self._checks:
            live = self._table(spec.table)
            archive = self._table(spec.table, VERSION_TABLE_SUFFIX)
            if self._archive_only:
                shown = archive
            elif include_archive:
                shown = f"{live}, {archive}"
            else:
                shown = live
            cb.setText(f"{spec.title}   ({shown})")

    def accept(self):
        keys = [spec.key for cb, spec in self._checks if cb.isChecked()]
        if not keys:
            QMessageBox.warning(self, TITLE, "Choose at least one layer.")
            return
        kind = self.cb_kind.currentData()
        spatial = any(spec.wkb != "None" for cb, spec in self._checks if cb.isChecked() and spec.key in keys)
        if spatial and not self.crs_widget.crs().isValid():
            QMessageBox.warning(self, TITLE, "Choose a coordinate reference system.")
            return
        if kind == "conn":
            data = self.cb_conn.currentData()
            if not data:
                QMessageBox.warning(self, TITLE, "Add the database in QGIS Data Source Manager, then try again.")
                return
            provider, name = data
            target = SchemaTarget("conn", provider=provider, connection=name,
                                  schema=self.ed_schema.text().strip(), prefix=self.ed_prefix.text(),
                                  replace=self.chk_replace.isChecked())
        else:
            if not self.ed_path.text().strip():
                QMessageBox.warning(self, TITLE, "Choose a file for the schema.")
                return
            target = SchemaTarget(kind, path=self.ed_path.text().strip(), prefix=self.ed_prefix.text(),
                                  replace=self.chk_replace.isChecked())
        archive = True if self._archive_only else self.chk_archive.isChecked()
        self._result = {
            "target": target,
            "crs": self.crs_widget.crs(),
            "keys": keys,
            "archive": archive,
            "archive_only": self._archive_only,
            "watch": bool(archive and self.chk_watch.isChecked()),
        }
        super().accept()

    def request(self):
        return self._result


class _FieldMapDialog(NormalDialog):
    def __init__(self, parent, title, dest_names, source_names, current):
        super().__init__(parent)
        self.setWindowTitle(f"Fields — {title}")
        lay = QVBoxLayout(self)
        lay.addWidget(hint("Each destination field can take one source field. Blank leaves the destination empty."))
        self.table = QTableWidget(len(dest_names), 2)
        self.table.setHorizontalHeaderLabels(["Destination", "Source"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        from qgis.PyQt.QtCore import Qt
        options = [""] + list(source_names)
        for row, name in enumerate(dest_names):
            item = QTableWidgetItem(name)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, item)
            cb = combo([(opt or "(leave empty)", opt) for opt in options])
            set_combo_data(cb, current.get(name, ""))
            self.table.setCellWidget(row, 1, cb)
        lay.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.resize(640, 480)

    def mapping(self):
        out = {}
        for row in range(self.table.rowCount()):
            dest = self.table.item(row, 0).text()
            src = self.table.cellWidget(row, 1).currentData()
            if src:
                out[dest] = src
        return out


class _LayerRow(QGroupBox):
    def __init__(self, spec, layers, dest, rcl_template, addr_template, parent=None):
        super().__init__(spec.title, parent)
        self.spec = spec
        self.defs = defs_for(spec.key, rcl_template, addr_template)
        self._override = None
        form = QFormLayout(self)
        self.cb_dest = combo([("(none)", "")])
        self.cb_src = combo([("(skip)", "")])
        for lyr in layers:
            self.cb_dest.addItem(_layer_label(lyr), lyr.id())
            self.cb_src.addItem(_layer_label(lyr), lyr.id())
        if dest is not None:
            set_combo_data(self.cb_dest, dest.id())
        self.lbl = QLabel("Choose a source to see the field match.")
        self.lbl.setWordWrap(True)
        self.btn = QPushButton("Fields…")
        self.btn.setEnabled(False)
        self.btn.clicked.connect(self._edit)
        extra = QWidget()
        row = QHBoxLayout(extra)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.lbl, 1)
        row.addWidget(self.btn)
        form.addRow("Destination", self.cb_dest)
        form.addRow("Source", self.cb_src)
        form.addRow(extra)
        self.cb_dest.currentIndexChanged.connect(lambda *_: self._changed())
        self.cb_src.currentIndexChanged.connect(lambda *_: self._changed())
        self._changed()

    def _layer(self, combo_box):
        lid = combo_box.currentData()
        lyr = QgsProject.instance().mapLayer(lid) if lid else None
        return lyr if isinstance(lyr, QgsVectorLayer) else None

    def dest_layer(self):
        return self._layer(self.cb_dest)

    def source_layer(self):
        return self._layer(self.cb_src)

    def set_source(self, layer):
        if layer is not None:
            set_combo_data(self.cb_src, layer.id())

    def _changed(self):
        self._override = None
        self._refresh()

    def _refresh(self):
        dest, src = self.dest_layer(), self.source_layer()
        if dest is None or src is None:
            self.lbl.setText("Choose a source to see the field match.")
            self.btn.setEnabled(False)
            return
        mapped = self.field_map()
        tag = "edited" if self._override is not None else "auto"
        text = f"{len(mapped)} of {len(destination_fields(dest))} field(s) mapped ({tag})."
        problem = geometry_problem(src, dest)
        self.lbl.setText(f"{problem} {text}" if problem else text)
        self.btn.setEnabled(True)

    def field_map(self):
        if self._override is not None:
            return dict(self._override)
        dest, src = self.dest_layer(), self.source_layer()
        if dest is None or src is None:
            return {}
        return suggest_field_map(destination_fields(dest), source_field_names(src), self.defs)

    def _edit(self):
        dest, src = self.dest_layer(), self.source_layer()
        if dest is None or src is None:
            return
        names = destination_fields(dest)
        dlg = _FieldMapDialog(self, self.spec.title, names, source_field_names(src), self.field_map())
        if dlg.exec():
            self._override = dlg.mapping()
            self._refresh()


class ImportDialog(NormalDialog):
    """Map existing project layers onto the schema layers and copy the features."""

    def __init__(self, parent, roles, rcl_template, addr_template):
        super().__init__(parent)
        self.setWindowTitle("Import into NENA schema")
        lay = QVBoxLayout(self)
        lay.addWidget(hint("Copies features into the schema layers and saves them immediately. "
                           "Roads, street names, address points, and driveways can each come from a layer "
                           "you already have. Blank source rows are left out."))
        layers = [lyr for lyr in QgsProject.instance().mapLayers().values() if isinstance(lyr, QgsVectorLayer)]
        layers.sort(key=lambda lyr: lyr.name().lower())
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_lay = QVBoxLayout(body)
        self.rows = []
        used = set()
        for spec in SCHEMA_LAYERS:
            row = _LayerRow(spec, layers, roles.get(spec.key), rcl_template, addr_template)
            guess = _guess_source(layers, spec.key, row.dest_layer(), used)
            if guess is not None:
                row.set_source(guess)
                used.add(guess.id())
            self.rows.append(row)
            body_lay.addWidget(row)
        # Zones usually stay in their own database, so importing them is optional and starts off.
        from ..core import zones as Z
        from ..core.project_config import load_project_config, resolve_layer
        configured = {z.kind: resolve_layer(z.layer_id, z.layer_source)
                      for z in Z.load_zones(load_project_config().zones.layers)}
        self.zone_box = QGroupBox("Also import zone layers (optional)")
        self.zone_box.setCheckable(True)
        self.zone_box.setChecked(False)
        self.zone_box.setToolTip("Copy boundary polygons into NENA zone tables. Not needed when the zone "
                                 "layers stay in their own database: list those under Settings, Zones.")
        zone_lay = QVBoxLayout(self.zone_box)
        zone_body = QWidget()
        zone_rows = QVBoxLayout(zone_body)
        zone_rows.setContentsMargins(0, 0, 0, 0)
        self.zone_rows = []
        for spec in ZONE_SCHEMA_LAYERS:
            row = _LayerRow(spec, layers, configured.get(spec.key[5:]), rcl_template, addr_template)
            self.zone_rows.append(row)
            zone_rows.addWidget(row)
        zone_lay.addWidget(zone_body)
        zone_body.setVisible(False)
        self.zone_box.toggled.connect(zone_body.setVisible)
        body_lay.addWidget(self.zone_box)
        body_lay.addStretch(1)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)
        self.chk_selected = QCheckBox("Import selected features only")
        self.chk_selected.setToolTip("Each source uses its own selection. A source with nothing selected is reported and skipped.")
        lay.addWidget(self.chk_selected)
        self.chk_access = QCheckBox("Create access points for the imported addresses")
        self.chk_access.setChecked(True)
        self.chk_access.setToolTip("Routing needs an access point for every address. After the import, each "
                                   "address with no access point gets one 10 feet off the road, placed from "
                                   "its driveway, its Access_X / Access_Y, or the nearest road, and linked by "
                                   "AccessPointID. Needs an access point layer (Create schema makes one).")
        lay.addWidget(self.chk_access)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.resize(680, 560)

    def accept(self):
        if not self.jobs():
            QMessageBox.warning(self, TITLE, "Choose a source and a destination for at least one layer.")
            return
        super().accept()

    def create_access_points(self) -> bool:
        """True when access points should be made after the import (addresses are imported)."""
        return self.chk_access.isChecked() and any(job["key"] == "address" for job in self.jobs())

    def jobs(self):
        selected = self.chk_selected.isChecked()
        out = []
        rows = list(self.rows) + (list(self.zone_rows) if self.zone_box.isChecked() else [])
        for row in rows:
            dest, src = row.dest_layer(), row.source_layer()
            if dest is None or src is None:
                continue
            out.append({
                "key": row.spec.key,
                "title": row.spec.title,
                "source": src,
                "dest": dest,
                "field_map": row.field_map(),
                "defs": row.defs,
                "selected_only": selected,
            })
        return out


def _guess_source(layers, key, dest, used_ids):
    words = {
        "centerline": ("centerline", "roadcenter", "rcl"),
        "streets": ("streetname", "street_name", "streetnames", "masterstreet"),
        "address": ("address", "ssap", "sitepoint"),
        "driveway": ("driveway",),
    }[key]
    dest_id = dest.id() if dest is not None else None
    ranked = []
    for lyr in layers:
        if lyr.id() == dest_id or lyr.id() in used_ids:
            continue
        raw = lyr.name().lower()
        compact = raw.replace(" ", "").replace("_", "")
        if "archive" in compact or "versions" in compact:
            continue
        for i, word in enumerate(words):
            if word.replace("_", "") in compact or word in raw:
                ranked.append((i, lyr))
                break
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0])
    return ranked[0][1]


class UpdateSchemaDialog(NormalDialog):
    """What Update schema would add. Each field and table can be unticked."""

    def __init__(self, parent, plan):
        super().__init__(parent)
        from qgis.PyQt.QtWidgets import QTreeWidget, QTreeWidgetItem
        self.setObjectName("NenaUpdateSchemaDialog")
        self.setWindowTitle("Update schema")
        self.plan = plan
        lay = QVBoxLayout(self)
        lay.addWidget(hint("Fields and tables the plugin uses that your project's tables do not have yet. "
                           "Only what is ticked is added. Nothing is removed, renamed, or replaced, and "
                           "the data in your tables stays as it is. Make a copy of the data first if you can."))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Add", "Type", "Note"])
        self.tree.setRootIsDecorated(True)
        self._items = []
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
        for table in plan.tables:
            if not table.fields:
                continue
            parent = QTreeWidgetItem([table.title, "", table.note])
            parent.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsAutoTristate | Qt.ItemFlag.ItemIsUserCheckable)
            self.tree.addTopLevelItem(parent)
            for item in table.fields:
                kind = item.ftype or (item.qfield.typeName() if item.qfield is not None else "")
                if item.length and kind == "string":
                    kind = f"text ({item.length})"
                note = item.note or ("required by NENA" if item.required else "")
                child = QTreeWidgetItem([item.physical, kind, note])
                child.setFlags(flags)
                child.setCheckState(0, Qt.CheckState.Checked if item.checked else Qt.CheckState.Unchecked)
                parent.addChild(child)
                self._items.append((child, item))
            parent.setExpanded(any(item.checked for item in table.fields))
        if plan.missing:
            where = plan.container or "(no file)"
            parent = QTreeWidgetItem(["New tables", "", f"in {where}" if plan.container else ""])
            parent.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsAutoTristate | Qt.ItemFlag.ItemIsUserCheckable)
            self.tree.addTopLevelItem(parent)
            for item in plan.missing:
                child = QTreeWidgetItem([item.title, "table", item.note])
                child.setFlags(flags if item.can_create else Qt.ItemFlag.ItemIsEnabled)
                child.setCheckState(0, Qt.CheckState.Checked if item.checked else Qt.CheckState.Unchecked)
                parent.addChild(child)
                self._items.append((child, item))
            parent.setExpanded(True)
        self.tree.resizeColumnToContents(0)
        self.tree.resizeColumnToContents(1)
        lay.addWidget(self.tree, 1)
        lay.addWidget(hint("A table with unsaved edits is skipped, so your edits are not saved by accident. "
                           "Fields on zone layers start unticked: those layers often follow a state or "
                           "regional format."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Update")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.resize(720, 560)

    def accept(self):
        for child, item in self._items:
            item.checked = child.checkState(0) == Qt.CheckState.Checked and \
                bool(child.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        if not any(item.checked for _child, item in self._items):
            QMessageBox.information(self, "Update schema", "Nothing is ticked.")
            return
        super().accept()
