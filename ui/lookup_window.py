# -*- coding: utf-8 -*-
"""
Lookup tables window.

The top list shows every drop-down list, where it is stored, and how many
fields use it. Create missing tables, Load values from my data, and Reconnect
drop-downs act on all of them. Pick a list to edit it below: type in the
cells, tick In list to show or retire a value, and drag rows (or use Up and
Down) to change the order. Every change is saved straight away.
"""

from qgis.PyQt.QtCore import Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QBrush, QColor
from qgis.PyQt.QtWidgets import (QAbstractItemView, QComboBox, QDialogButtonBox, QGroupBox,
                                 QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
                                 QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
from qgis.gui import QgsMapLayerComboBox

from ..compat import table_layer_filter
from ..core import lookups as L
from ..core.layer_access import ConfigError, ensure_editable
from ..core.project_config import load_project_config, remember, save_project_config
from .widgets import NormalDialog, hint

ROLE_FID = Qt.ItemDataRole.UserRole
ROLE_LIST = Qt.ItemDataRole.UserRole   # used on other items than ROLE_FID

ROLE_TITLES = {"address": "address points", "centerline": "centerlines", "master": "street names"}


def _kind_of(list_id):
    """('table', key) | ('domain', name) | ('map', key)."""
    kind, _sep, name = (list_id or "").partition(":")
    return kind, name


class _OrderTree(QTreeWidget):
    """Value list that can be reordered by dragging."""

    reordered = pyqtSignal()

    def dropEvent(self, event):  # noqa: N802
        super().dropEvent(event)
        self.reordered.emit()


class LoadValuesDialog(NormalDialog):
    """Review the values found in the data before they are added to the lists."""

    def __init__(self, parent, found, existing_rows):
        super().__init__(parent)
        self.setWindowTitle("Load values from my data")
        self.resize(520, 560)
        lay = QVBoxLayout(self)
        lay.addWidget(hint("These values are on your address points and centerlines but not yet in the lists. "
                           "Untick anything that is a typo, then click OK to add the rest."))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Value", "Parent"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.tree, 1)
        self.total = 0
        for name in L.DOMAIN_NAMES:
            rows = L.new_domain_rows(name, found.get(name, []), existing_rows)
            if not rows:
                continue
            top = QTreeWidgetItem([f"{L.DOMAIN_TITLE[name]} ({len(rows)} new)", ""])
            top.setData(0, ROLE_LIST, name)
            top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
            top.setCheckState(0, Qt.CheckState.Checked)
            for row in rows:
                child = QTreeWidgetItem([row["Code"], row["Parent"]])
                child.setData(0, ROLE_FID, (row["Code"], row["Parent"]))
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(0, Qt.CheckState.Checked)
                top.addChild(child)
                self.total += 1
            self.tree.addTopLevelItem(top)
        self.tree.expandAll()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def chosen(self):
        """{domain: [(value, parent), …]} of the ticked values."""
        out = {}
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            name = top.data(0, ROLE_LIST)
            for j in range(top.childCount()):
                child = top.child(j)
                if child.checkState(0) == Qt.CheckState.Checked:
                    out.setdefault(name, []).append(tuple(child.data(0, ROLE_FID)))
        return out


class LookupPage(QWidget):
    """Every drop-down list in one window."""

    def __init__(self, dock, parent=None):
        super().__init__(parent)
        self.dock = dock
        self.layers = {}
        self.report = []
        self._filling = False
        self._current = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(hint("These lists fill the drop-downs on address points, centerlines, and street names, "
                           "so values are typed the same way every time. Pick a list to edit it below."))

        split = QSplitter(Qt.Orientation.Vertical)
        lay.addWidget(split, 1)

        # -- every list and its status --------------------------------------
        top = QGroupBox("Lists")
        top_lay = QVBoxLayout(top)
        self.status = QTreeWidget()
        self.status.setHeaderLabels(["List", "Stored in", "Drop-downs on"])
        self.status.setRootIsDecorated(True)
        self.status.setUniformRowHeights(True)
        header = self.status.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.status.currentItemChanged.connect(lambda *_: self._pick())
        top_lay.addWidget(self.status, 1)
        row = QHBoxLayout()
        for text, slot, tip in (
                ("Create missing tables", self.create_missing,
                 "Create the lookup tables that are not in the project yet, in the same GeoPackage or "
                 "database as your address points, fill them with the standard values, and connect the "
                 "drop-downs. Tables that already exist there are reconnected, not replaced."),
                ("Load values from my data…", self.load_from_data,
                 "Collect the counties, communities, ZIP codes, ESNs and other values already on your "
                 "address points and centerlines. You review them before they are added."),
                ("Reconnect drop-downs", self.reconnect,
                 "Set the drop-down on every field that has a list. Run this after adding fields or layers."),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        top_lay.addLayout(row)
        split.addWidget(top)

        # -- editor for one list --------------------------------------------
        bottom = QGroupBox("Values")
        self.editor_box = bottom
        ed = QVBoxLayout(bottom)
        head = QHBoxLayout()
        self.lbl_table = QLabel("Table")
        head.addWidget(self.lbl_table)
        self.cb_layer = QgsMapLayerComboBox()
        self.cb_layer.setFilters(table_layer_filter())
        self.cb_layer.setAllowEmptyLayer(True)
        self.cb_layer.setToolTip("The table this list is stored in. It is found automatically; change it "
                                 "only if you keep the list in a different table.")
        self.cb_layer.layerChanged.connect(lambda *_: self._layer_picked())
        head.addWidget(self.cb_layer, 1)
        ed.addLayout(head)
        self.lbl_fixed = hint("")
        ed.addWidget(self.lbl_fixed)

        self.values = _OrderTree()
        self.values.setRootIsDecorated(False)
        self.values.setAlternatingRowColors(True)
        self.values.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.values.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.values.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.values.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                    | QAbstractItemView.EditTrigger.EditKeyPressed
                                    | QAbstractItemView.EditTrigger.SelectedClicked)
        self.values.itemChanged.connect(self._item_changed)
        self.values.reordered.connect(self._save_order)
        ed.addWidget(self.values, 1)

        add = QHBoxLayout()
        self.ed_code = QLineEdit()
        self.ed_code.setPlaceholderText("Code (stored)")
        self.ed_label = QLineEdit()
        self.ed_label.setPlaceholderText("Label (shown)")
        self.cb_parent = QComboBox()
        self.cb_parent.setEditable(True)
        self.cb_parent.setToolTip("Parent value, e.g. the ZIP code of a postal community")
        self.ed_label.returnPressed.connect(self._add)
        self.ed_code.returnPressed.connect(self._add)
        self.b_add = QPushButton("Add")
        self.b_add.setToolTip("Add this value to the list")
        self.b_add.clicked.connect(self._add)
        for widget, stretch in ((self.ed_code, 1), (self.ed_label, 2), (self.cb_parent, 1), (self.b_add, 0)):
            add.addWidget(widget, stretch)
        ed.addLayout(add)

        tools = QHBoxLayout()
        self.b_up = QPushButton("Move up")
        self.b_up.clicked.connect(lambda: self._move(-1))
        self.b_down = QPushButton("Move down")
        self.b_down.clicked.connect(lambda: self._move(1))
        self.b_sort = QPushButton("Sort A–Z")
        self.b_sort.setToolTip("Put the values in alphabetical order of their label")
        self.b_sort.clicked.connect(self._sort)
        self.b_delete = QPushButton("Delete")
        self.b_delete.setToolTip("Delete a value added by mistake. To stop offering a value that features "
                                 "already use, untick In list instead.")
        self.b_delete.clicked.connect(self._delete)
        for button in (self.b_up, self.b_down, self.b_sort):
            tools.addWidget(button)
        tools.addStretch(1)
        tools.addWidget(self.b_delete)
        ed.addLayout(tools)
        ed.addWidget(hint("Double-click a cell to change it. Features store the code, so changing a code "
                          "does not rewrite features that already use the old one."))
        split.addWidget(bottom)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)

        self.lbl = QLabel("")
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)

    # ------------------------------------------------------------------ loading
    def load(self):
        pcfg = load_project_config()
        before = pcfg.to_json()
        self.layers = {key: L.lookup_layer(pcfg, key) for key in L.LOOKUP_KEYS}
        if pcfg.to_json() != before:          # a table was found in the project; keep the link
            save_project_config(pcfg)
        try:
            self.report = L.apply_dropdowns(pcfg, self.dock.layer, dry_run=True)
        except Exception:  # noqa: BLE001 - the window still opens without the counts
            self.report = []
        self._fill_status()

    def store(self):
        """Remember the lookup layers and wire the drop-downs. Called by the panel's save."""
        pcfg = load_project_config()
        for key, _table, _title, role in L.LOOKUP_SPECS:
            remember(pcfg.lookups, role, self.layers.get(key))
        save_project_config(pcfg)
        self.report = L.apply_dropdowns(pcfg, self.dock.layer)
        self._fill_status()
        return pcfg

    def _fields_for(self, kind, source):
        out = []
        for item in self.report:
            if item["kind"] == kind and item["source"] == source and item["status"] in ("wired", "ready"):
                out.append(f"{item['physical']} ({ROLE_TITLES.get(item['role'], item['role'])})")
        return out

    def _status_row(self, parent, list_id, title, stored, kind, source, missing=False):
        fields = self._fields_for(kind, source)
        text = f"{len(fields)} field(s)" if fields else "no matching fields"
        item = QTreeWidgetItem([title, stored, text])
        item.setData(0, ROLE_LIST, list_id)
        item.setToolTip(2, "\n".join(fields) if fields else
                        "None of your layers has a field for this list.")
        if missing:
            item.setForeground(1, QBrush(QColor(170, 30, 30)))
            item.setToolTip(1, "Click Create missing tables.")
        parent.addChild(item)
        return item

    def _fill_status(self):
        keep = self._current
        self.status.blockSignals(True)
        self.status.clear()
        tables = QTreeWidgetItem(["Lookup tables", "", ""])
        for key, _table, title, _role in L.TABLE_SPECS:
            layer = self.layers.get(key)
            stored = layer.name() if layer is not None else "Missing"
            self._status_row(tables, f"table:{key}", title, stored, "table", key, layer is None)
        local = QTreeWidgetItem(["Local lists", "", ""])
        domains = self.layers.get(L.DOMAINS_KEY)
        rows = L.read_rows(domains) if domains is not None else []
        for name in L.DOMAIN_NAMES:
            count = len(L.rows_in_domain(rows, name))
            stored = f"{domains.name()} · {count} value(s)" if domains is not None else "Missing"
            self._status_row(local, f"domain:{name}", L.DOMAIN_TITLE[name], stored, "domain", name,
                             domains is None)
        fixed = QTreeWidgetItem(["Fixed NENA lists", "", ""])
        for key, (title, pairs) in L.FIXED_LISTS.items():
            self._status_row(fixed, f"map:{key}", title, f"Built in · {len(pairs)} value(s)", "map", key)
        for group in (tables, local, fixed):
            group.setFlags(Qt.ItemFlag.ItemIsEnabled)
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            self.status.addTopLevelItem(group)
        self.status.expandAll()
        self.status.blockSignals(False)
        missing = [title for key, _t, title, _r in L.LOOKUP_SPECS if self.layers.get(key) is None]
        if missing:
            self.lbl.setText("Missing: " + ", ".join(missing) + ". Click <b>Create missing tables</b>.")
        else:
            wired = sum(1 for item in self.report if item["status"] in ("wired", "ready"))
            self.lbl.setText(f"All lookup tables are in the project. {wired} field(s) have a drop-down.")
        self._select(keep or "table:road_type")

    def _select(self, list_id):
        it = self.status.invisibleRootItem()
        for i in range(it.childCount()):
            group = it.child(i)
            for j in range(group.childCount()):
                child = group.child(j)
                if child.data(0, ROLE_LIST) == list_id:
                    self.status.setCurrentItem(child)
                    self._pick()
                    return

    # ------------------------------------------------------------------ one list
    def _pick(self):
        item = self.status.currentItem()
        list_id = item.data(0, ROLE_LIST) if item is not None else None
        if not list_id:
            return
        self._current = list_id
        kind, name = _kind_of(list_id)
        fixed = kind == "map"
        title = item.text(0)
        self.editor_box.setTitle(f"Values · {title}")
        for widget in (self.lbl_table, self.cb_layer):
            widget.setVisible(not fixed)
        for widget in (self.ed_code, self.ed_label, self.b_add, self.b_up, self.b_down, self.b_sort,
                       self.b_delete):
            widget.setEnabled(not fixed)
        parent_domain = L.DOMAIN_PARENT.get(name, "") if kind == "domain" else ""
        self.cb_parent.setVisible(bool(parent_domain))
        if parent_domain:
            self.cb_parent.lineEdit().setPlaceholderText(L.DOMAIN_TITLE[parent_domain][:-1] if
                                                         L.DOMAIN_TITLE[parent_domain].endswith("s")
                                                         else L.DOMAIN_TITLE[parent_domain])
        if fixed:
            self.lbl_fixed.setText("A fixed NENA list. Its values are built into the drop-down and need no table.")
        elif kind == "domain":
            self.lbl_fixed.setText("Stored in the shared domain-values table with Domain = "
                                   f"<b>{name}</b>." + (" Each value can name its "
                                                       f"{L.DOMAIN_TITLE[parent_domain].lower()[:-1]} as "
                                                       "Parent, so the drop-down narrows to it."
                                                       if parent_domain else ""))
        else:
            self.lbl_fixed.setText("")
        self.lbl_fixed.setVisible(bool(self.lbl_fixed.text()))
        self._filling = True
        try:
            self.cb_layer.setLayer(self._layer())
        finally:
            self._filling = False
        self._fill_values()

    def _layer(self):
        kind, name = _kind_of(self._current)
        if kind == "table":
            return self.layers.get(name)
        if kind == "domain":
            return self.layers.get(L.DOMAINS_KEY)
        return None

    def _layer_picked(self):
        if self._filling:
            return
        kind, name = _kind_of(self._current)
        key = name if kind == "table" else L.DOMAINS_KEY if kind == "domain" else ""
        if not key:
            return
        self.layers[key] = self.cb_layer.currentLayer()
        pcfg = load_project_config()
        remember(pcfg.lookups, L.SPEC_BY_KEY[key][3], self.layers[key])
        save_project_config(pcfg)
        self.report = L.apply_dropdowns(pcfg, self.dock.layer)
        self._fill_status()

    def _columns(self):
        kind, name = _kind_of(self._current)
        parent = kind == "domain" and bool(L.DOMAIN_PARENT.get(name))
        cols = ["Code", "Label"] + (["Parent"] if parent else []) + ["In list"]
        return cols

    def _fill_values(self):
        kind, name = _kind_of(self._current)
        cols = self._columns()
        current = self.values.currentItem()
        keep_fid = current.data(0, ROLE_FID) if current is not None else None
        self._filling = True
        try:
            self.values.clear()
            self.values.setColumnCount(len(cols))
            self.values.setHeaderLabels(cols)
            if kind == "map":
                for label, code in L.FIXED_LISTS[name][1]:
                    item = QTreeWidgetItem([code, label, "Yes"])
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    self.values.addTopLevelItem(item)
                self.values.setColumnCount(2)
                self.values.setHeaderLabels(["Code", "Label"])
                self._resize()
                return
            layer = self._layer()
            rows = L.read_rows(layer, name if kind == "domain" else "")
            rows.sort(key=lambda row: (L._order(row), str(row.get("Label") or "").upper()))  # noqa: SLF001
            flags = (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEditable
                     | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            for row in rows:
                texts = [str(row.get("Code") or ""), str(row.get("Label") or "")]
                if "Parent" in cols:
                    texts.append(str(row.get("Parent") or ""))
                texts.append("")
                item = QTreeWidgetItem(texts)
                item.setFlags(flags)
                item.setData(0, ROLE_FID, row.get("_fid"))
                active = str(row.get("Active") or "Y").strip().upper() != "N"
                item.setCheckState(len(cols) - 1, Qt.CheckState.Checked if active else Qt.CheckState.Unchecked)
                self.values.addTopLevelItem(item)
                if keep_fid is not None and row.get("_fid") == keep_fid:
                    self.values.setCurrentItem(item)
            self._fill_parent_choices()
        finally:
            self._filling = False
        self._resize()
        if self._layer() is None:
            self.lbl.setText("This list has no table yet. Click <b>Create missing tables</b>.")

    def _fill_parent_choices(self):
        kind, name = _kind_of(self._current)
        parent = L.DOMAIN_PARENT.get(name, "") if kind == "domain" else ""
        self.cb_parent.clear()
        if not parent:
            return
        self.cb_parent.addItem("")
        for code, label in L.active_choices(L.read_rows(self.layers.get(L.DOMAINS_KEY), parent)):
            self.cb_parent.addItem(code if code == label else f"{code} – {label}", code)

    def _resize(self):
        for col in range(self.values.columnCount() - 1):
            self.values.resizeColumnToContents(col)

    # ------------------------------------------------------------------ edits
    def _edit(self, title, mutator):
        """Run ``mutator(layer)`` and save at once (unless you already had the table open for editing)."""
        layer = self._layer()
        if layer is None:
            self.dock._msg("This list has no table yet. Click Create missing tables.", "Warning")
            return False
        try:
            started = ensure_editable(layer)
        except ConfigError as exc:
            self.dock._msg(str(exc), "Warning")
            return False
        layer.beginEditCommand(title)
        try:
            ok = mutator(layer)
        except Exception as exc:  # noqa: BLE001
            layer.destroyEditCommand()
            if started:
                layer.rollBack()
            self.dock._msg(str(exc), "Warning")
            return False
        if not ok:
            layer.destroyEditCommand()
            if started:
                layer.rollBack()
            return False
        layer.endEditCommand()
        try:
            L.save_edits(layer, started)
        except ConfigError as exc:
            self.dock._msg(str(exc), "Warning")
            return False
        if not started:
            self.dock._msg(f"{layer.name()} was already open for editing, so this change waits in its edit "
                           "buffer. Save that layer to keep it.", "Info")
        return True

    def _item_changed(self, item, column):
        if self._filling:
            return
        cols = self._columns()
        name = cols[column] if column < len(cols) else ""
        fid = item.data(0, ROLE_FID)
        if name == "In list":
            value = "Y" if item.checkState(column) == Qt.CheckState.Checked else "N"
            field = "Active"
        else:
            value = item.text(column).strip()
            field = name
            if field in ("Code", "Label") and not value:
                self.dock._msg(f"{field} cannot be empty.", "Warning")
                QTimer.singleShot(0, self._fill_values)
                return

        def mutate(layer):
            index = layer.fields().indexFromName(field)
            if index < 0:
                raise ConfigError(f"{layer.name()} has no {field} field.")
            return layer.changeAttributeValue(fid, index, value)

        if not self._edit("NENA: edit lookup value", mutate):
            QTimer.singleShot(0, self._fill_values)
            return
        self._after_change(refill=False)

    def _order_items(self):
        return [self.values.topLevelItem(i) for i in range(self.values.topLevelItemCount())]

    def _save_order(self):
        if self._filling:
            return
        items = self._order_items()

        def mutate(layer):
            index = layer.fields().indexFromName("SortOrder")
            if index < 0:
                raise ConfigError(f"{layer.name()} has no SortOrder field.")
            for position, item in enumerate(items):
                layer.changeAttributeValue(item.data(0, ROLE_FID), index, (position + 1) * 10)
            return True

        self._edit("NENA: reorder lookup", mutate)
        self._after_change()

    def _move(self, delta):
        item = self.values.currentItem()
        if item is None:
            return
        row = self.values.indexOfTopLevelItem(item)
        other = row + delta
        if other < 0 or other >= self.values.topLevelItemCount():
            return
        self._filling = True
        try:
            taken = self.values.takeTopLevelItem(row)
            self.values.insertTopLevelItem(other, taken)
            self.values.setCurrentItem(taken)
        finally:
            self._filling = False
        self._save_order()

    def _sort(self):
        self._filling = True
        try:
            self.values.sortItems(1, Qt.SortOrder.AscendingOrder)
        finally:
            self._filling = False
        self._save_order()

    def _add(self):
        kind, name = _kind_of(self._current)
        if kind == "map":
            return
        code = self.ed_code.text().strip()
        label = self.ed_label.text().strip() or code
        if not code:
            self.dock._msg("Type a code (the value stored on the feature).", "Warning")
            return
        layer = self._layer()
        if layer is None:
            self.dock._msg("This list has no table yet. Click Create missing tables.", "Warning")
            return
        existing = L.read_rows(layer, name if kind == "domain" else "")
        if any(str(r.get("Code") or "").strip().upper() == code.upper() for r in existing):
            self.dock._msg(f"{code} is already in this list.", "Warning")
            return
        order = max([L._order(r) for r in existing] or [0]) + 10  # noqa: SLF001
        row = {"Code": code, "Label": label, "SortOrder": order, "Active": "Y"}
        if kind == "domain":
            row["Domain"] = name
            parent = self.cb_parent.currentData()
            row["Parent"] = parent if parent is not None else self.cb_parent.currentText().split(" – ")[0].strip()

        def mutate(target):
            from qgis.core import QgsGeometry, QgsVectorLayerUtils
            attrs = {}
            for field, value in row.items():
                index = target.fields().indexFromName(field)
                if index >= 0:
                    attrs[index] = value
            feature = QgsVectorLayerUtils.createFeature(target, QgsGeometry(), attrs,
                                                        target.createExpressionContext())
            return target.addFeature(feature)

        if self._edit("NENA: add lookup value", mutate):
            self.ed_code.clear()
            self.ed_label.clear()
            self.ed_code.setFocus()
            self._after_change()

    def _delete(self):
        item = self.values.currentItem()
        if item is None:
            self.dock._msg("Select a value first.", "Info")
            return
        if QMessageBox.question(self, "Delete value",
                                f"Remove {item.text(0)} from this list?\n\nFeatures that already use it keep "
                                "the value. To only stop offering it, untick In list instead."
                                ) != QMessageBox.StandardButton.Yes:
            return
        fid = item.data(0, ROLE_FID)
        if self._edit("NENA: delete lookup value", lambda layer: layer.deleteFeature(fid)):
            self._after_change()

    def _after_change(self, refill=True):
        """Refresh after the current signal has finished (the tree must not be rebuilt inside it)."""
        def later():
            kind, _name = _kind_of(self._current)
            if kind == "domain":
                self._fill_status()        # also refills the values
            elif refill:
                self._fill_values()
        QTimer.singleShot(0, later)

    # ------------------------------------------------------------------ whole-window actions
    def _anchor(self, pcfg):
        layers = L.feature_layers(pcfg, self.dock.layer)
        return layers.get("address") or layers.get("centerline") or layers.get("master")

    def create_missing(self):
        def do():
            import os
            from qgis.core import QgsLayerTreeGroup, QgsProject
            pcfg = load_project_config()
            missing = [key for key in L.LOOKUP_KEYS if L.lookup_layer(pcfg, key) is None]
            if not missing:
                save_project_config(pcfg)
                self.load()
                self.reconnect()
                return
            target = L.container_target(self._anchor(pcfg))
            where = target.path if target.kind in ("gpkg", "gdb") else (
                target.connection + (f" / {target.schema}" if target.schema else ""))
            titles = [L.SPEC_BY_KEY[key][2] for key in missing]
            note = ""
            if target.kind == "gpkg" and not os.path.exists(target.path):
                note = "\n\nYour layers are not in a GeoPackage, so a new GeoPackage will be made there."
            if QMessageBox.question(self, "Create missing tables",
                                    "Create these lookup tables?\n\n" + "\n".join(titles)
                                    + f"\n\nIn: {where}{note}") != QMessageBox.StandardButton.Yes:
                return
            made, _target = L.create_missing_lookups(pcfg, self._anchor(pcfg), missing)
            prj = QgsProject.instance()
            group = None
            for child in prj.layerTreeRoot().children():
                if isinstance(child, QgsLayerTreeGroup) and child.name() == "NENA lookup lists":
                    group = child
            if group is None:
                group = prj.layerTreeRoot().addGroup("NENA lookup lists")
            for key in L.LOOKUP_KEYS:
                layer = made.get(key)
                if layer is not None:
                    prj.addMapLayer(layer, False)
                    group.addLayer(layer)
            save_project_config(pcfg)
            self.dock._hide_support_tables()
            self.report = L.apply_dropdowns(pcfg, self.dock.layer)
            self.load()
            wired = sum(1 for item in self.report if item["status"] == "wired")
            self.dock._msg(f"{len(made)} lookup table(s) ready in {where}. {wired} field(s) now have a drop-down. "
                           "Next: Load values from my data to fill the local lists.", "Success")
        self.dock._guard(do)

    def reconnect(self):
        def do():
            pcfg = load_project_config()
            self.report = L.apply_dropdowns(pcfg, self.dock.layer)
            save_project_config(pcfg)
            kept = [item for item in self.report if item["status"] == "kept"]
            if kept and QMessageBox.question(
                    self, "Reconnect drop-downs",
                    f"{len(kept)} field(s) already have a different widget set up by hand:\n\n"
                    + ", ".join(sorted({item['physical'] for item in kept}))
                    + "\n\nReplace those with the NENA drop-downs too?") == QMessageBox.StandardButton.Yes:
                self.report = L.apply_dropdowns(pcfg, self.dock.layer, force=True)
            self._fill_status()
            wired = sum(1 for item in self.report if item["status"] == "wired")
            no_table = sorted({item["source"] for item in self.report if item["status"] == "no table"})
            text = f"{wired} field(s) have a drop-down."
            if no_table:
                text += " Some lists have no table yet; click Create missing tables."
            self.dock._msg(text, "Success" if not no_table else "Info")
        self.dock._guard(do)

    def load_from_data(self):
        def do():
            pcfg = load_project_config()
            domains = L.lookup_layer(pcfg, L.DOMAINS_KEY)
            if domains is None:
                if QMessageBox.question(self, "Load values from my data",
                                        "The domain-values table is not in the project yet. Create the "
                                        "missing tables first?") == QMessageBox.StandardButton.Yes:
                    self.create_missing()
                return
            found = L.collect_domain_values(pcfg, self.dock.layer)
            existing = L.read_rows(domains)
            dlg = LoadValuesDialog(self, found, existing)
            if dlg.total == 0:
                self.dock._msg("Every value on your address points and centerlines is already in the lists.",
                               "Info")
                return
            if not dlg.exec():
                return
            rows = []
            for name, values in dlg.chosen().items():
                rows.extend(L.new_domain_rows(name, values, existing + rows))
            added = L.add_rows(domains, rows)
            self.load()
            self.dock._msg(f"{added} value(s) added to the lists.", "Success")
        self.dock._guard(do)
