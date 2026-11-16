# -*- coding: utf-8 -*-
"""Dialogs for master street names: edit/rename, build from centerlines, name check results."""

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QCompleter, QDialog, QDialogButtonBox,
                                 QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                                 QPushButton, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
                                 QVBoxLayout)

from ..core.layer_access import ConfigError
from ..core.project_config import StreetTypeSettings
from ..core.schema import DIRECTIONAL_DOMAIN
from ..core.streets import names as N
from ..core.streets.street_types import OFFICIAL_TYPES, display_type, type_list
from .widgets import hint

PART_LABELS = {
    "St_PreMod": "Pre modifier", "St_PreDir": "Pre directional", "St_PreTyp": "Pre type",
    "St_PreSep": "Pre type separator", "St_Name": "Street name", "St_PosTyp": "Post type (suffix)",
    "St_PosDir": "Post directional", "St_PosMod": "Post modifier",
}


class NameDialog(QDialog):
    """Enter or change a road name; shows the full name and uniqueness conflicts live."""

    def __init__(self, parent, title, parts=None, scope="", scope_enabled=False, check=None,
                 show_alias=False, keep_alias=True, info="", street_types=None, abbreviations=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.check = check            # callable(parts, scope) -> [Conflict]; may raise ConfigError
        self.street_types = list(street_types) if street_types else list(OFFICIAL_TYPES)
        self.abbreviations = abbreviations
        lay = QVBoxLayout(self)
        if info:
            lay.addWidget(hint(info))
        form = QFormLayout()
        self.edits = {}
        for p in N.NAME_PARTS:
            if p in ("St_PreDir", "St_PosDir"):
                w = QComboBox()
                w.setEditable(True)
                w.addItems([""] + list(DIRECTIONAL_DOMAIN))
                w.setCurrentText((parts or {}).get(p, ""))
                w.currentTextChanged.connect(self._update)
            elif p in N.TYPE_PARTS:
                w = QComboBox()
                w.setEditable(True)
                w.addItem("")
                w.addItems(self.street_types)
                done = QCompleter(self.street_types, w)
                done.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
                done.setFilterMode(Qt.MatchFlag.MatchContains)
                w.setCompleter(done)
                w.setCurrentText((parts or {}).get(p, ""))
                w.currentTextChanged.connect(self._update)
                if w.lineEdit() is not None:
                    w.lineEdit().editingFinished.connect(lambda part=p: self._standardize(part))
            else:
                w = QLineEdit((parts or {}).get(p, ""))
                w.textChanged.connect(self._update)
            self.edits[p] = w
            form.addRow(PART_LABELS[p], w)
        self.ed_scope = QLineEdit(scope or "")
        self.ed_scope.setEnabled(scope_enabled)
        self.ed_scope.setToolTip("Uniqueness scope value (only used when names are unique per field value)")
        self.ed_scope.textChanged.connect(self._update)
        form.addRow("Scope", self.ed_scope)
        lay.addLayout(form)
        self.lbl_full = QLabel()
        self.lbl_full.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.lbl_full)
        self.lbl_check = QLabel()
        self.lbl_check.setWordWrap(True)
        self.lbl_check.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.lbl_check)
        self.chk_alias = QCheckBox("Keep the old name as a retired alias")
        self.chk_alias.setChecked(keep_alias)
        self.chk_alias.setVisible(show_alias)
        lay.addWidget(self.chk_alias)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._update()

    def _text(self, p):
        w = self.edits[p]
        return w.currentText() if isinstance(w, QComboBox) else w.text()

    def _standardize(self, part):
        w = self.edits[part]
        shown = w.currentText()
        standard = display_type(shown, self.abbreviations)
        if standard != shown:
            w.blockSignals(True)
            w.setCurrentText(standard)
            w.blockSignals(False)
        self._update()

    def parts(self):
        raw = N.parts_from({p: self._text(p) for p in N.NAME_PARTS})
        for p in N.TYPE_PARTS:
            raw[p] = display_type(raw[p], self.abbreviations)
        return raw

    def scope(self):
        return N.clean(self.ed_scope.text())

    def _update(self, *args):
        parts = self.parts()
        self.lbl_full.setText(f"Full name: <b>{N.full_name(parts) or '—'}</b>")
        ok_btn = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if not parts["St_Name"]:
            self.lbl_check.setText("<span style='color:#c0392b'>A street name is required.</span>")
            ok_btn.setEnabled(False)
            return
        conflicts = []
        if self.check is not None:
            try:
                conflicts = self.check(parts, self.scope())
            except ConfigError as exc:
                self.lbl_check.setText(f"<span style='color:#c0392b'>{exc}</span>")
                ok_btn.setEnabled(False)
                return
        blocking = [c for c in conflicts if c.blocking]
        lines = []
        for c in conflicts:
            colour = "#c0392b" if c.blocking else "#d35400"
            what = "Duplicate of" if c.kind == "duplicate" else "Sounds like"
            lines.append(f"<span style='color:{colour}'>{what} '{c.other.full_name}'"
                         f"{' (' + c.other.scope + ')' if c.other.scope else ''}</span>")
        self.lbl_check.setText("<br>".join(lines) or "<span style='color:#27ae60'>Name is unique.</span>")
        ok_btn.setEnabled(not blocking)


class BuildDialog(QDialog):
    """Review the names found on the centerlines before building the master list."""

    def __init__(self, parent, report):
        super().__init__(parent)
        self.setWindowTitle("Build master street names from centerlines")
        self.resize(640, 560)
        self.report = report
        lay = QVBoxLayout(self)
        n_var = sum(1 for g in report.groups if g.is_variant)
        lay.addWidget(hint(
            f"Found <b>{len(report.groups)}</b> road names on the centerlines ({report.unnamed} unnamed segments "
            f"skipped). <b>{n_var}</b> name(s) are spelled more than one way – the bold spelling is used for "
            "the master record. Double-click another spelling to use it instead."))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name / spelling", "Segments", "Scope"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.itemDoubleClicked.connect(self._choose)
        for g in sorted(report.groups, key=lambda g: (not g.is_variant, g.key)):
            top = QTreeWidgetItem([g.label(g.chosen), str(sum(g.spellings.values())), g.scope])
            top.setData(0, Qt.ItemDataRole.UserRole, g)
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            if g.is_variant:
                for sp, cnt in sorted(g.spellings.items(), key=lambda kv: -kv[1]):
                    ch = QTreeWidgetItem([g.label(sp), str(cnt), ""])
                    ch.setData(0, Qt.ItemDataRole.UserRole, sp)
                    top.addChild(ch)
                top.setExpanded(True)
            self.tree.addTopLevelItem(top)
        lay.addWidget(self.tree, 1)
        if report.soundalikes:
            names = "; ".join(" / ".join(g.label(g.chosen) for g in grp) for grp in report.soundalikes[:10])
            lay.addWidget(hint(f"<b>Sound-alike names</b> (check they are really different roads): {names}"))
        self.chk_rewrite = QCheckBox("Rewrite centerline and address names to the chosen spelling")
        self.chk_rewrite.setChecked(True)
        self.chk_addr = QCheckBox("Also link address points with matching names")
        self.chk_addr.setChecked(True)
        self.chk_overwrite = QCheckBox("Re-link features that already have a valid StreetID")
        for w in (self.chk_rewrite, self.chk_addr, self.chk_overwrite):
            lay.addWidget(w)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.btn_build = QPushButton("Build")
        bb.addButton(self.btn_build, QDialogButtonBox.ButtonRole.AcceptRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _choose(self, item, _col):
        parent = item.parent()
        if parent is None:
            return
        g = parent.data(0, Qt.ItemDataRole.UserRole)
        g.chosen = item.data(0, Qt.ItemDataRole.UserRole)
        parent.setText(0, g.label(g.chosen))


class IssuesDialog(QDialog):
    """Name-check results across the master table, centerlines and address points."""
    selectRequested = pyqtSignal(str, list)     # role, fids

    def __init__(self, parent, issues_by_role):
        super().__init__(parent)
        self.setWindowTitle("Street name check")
        self.resize(720, 460)
        lay = QVBoxLayout(self)
        total = sum(len(v) for v in issues_by_role.values())
        lay.addWidget(QLabel(f"{total} issue(s)" if total else "No street-name issues found."))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Layer", "Severity", "FID", "Rule", "Message"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        rows = [(role, i) for role, items in issues_by_role.items() for i in items]
        self.table.setRowCount(len(rows))
        for r, (role, i) in enumerate(rows):
            for c, val in enumerate([role, i.severity, "" if i.fid is None else str(i.fid), i.code, i.message]):
                it = QTableWidgetItem(val)
                it.setData(Qt.ItemDataRole.UserRole, (role, i.fid))
                self.table.setItem(r, c, it)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        b = QPushButton("Select in layer")
        b.clicked.connect(self._select)
        row.addWidget(b)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)

    def _select(self, *args):
        picked = {}
        for idx in self.table.selectionModel().selectedRows():
            role, fid = self.table.item(idx.row(), 0).data(Qt.ItemDataRole.UserRole)
            if fid is not None:
                picked.setdefault(role, []).append(fid)
        for role, fids in picked.items():
            self.selectRequested.emit(role, fids)


class StreetTypesDialog(QDialog):
    """Edit the street-type list and local abbreviations stored in the project."""

    def __init__(self, parent, settings):
        super().__init__(parent)
        self.setWindowTitle("Street types")
        self.resize(460, 520)
        lay = QVBoxLayout(self)
        lay.addWidget(hint(
            "These are the full spellings offered for pre type and post type. "
            "Abbreviations such as St and GLN still match the spelled form when names are checked. "
            "A type you type once in the name form is not added here. "
            "Click Save at the top of the panel to keep this list in the project."))
        self.lst = QListWidget()
        self.lst.addItems(type_list(settings))
        lay.addWidget(self.lst, 1)
        row = QHBoxLayout()
        self.ed_add = QLineEdit()
        self.ed_add.setPlaceholderText("Add a local type")
        self.ed_add.returnPressed.connect(self._add)
        row.addWidget(self.ed_add, 1)
        b = QPushButton("Add")
        b.clicked.connect(self._add)
        row.addWidget(b)
        b = QPushButton("Remove")
        b.clicked.connect(self._remove)
        row.addWidget(b)
        lay.addLayout(row)
        lay.addWidget(QLabel("Local abbreviations (short form, then the spelled type)"))
        self.tbl = QTableWidget(0, 2)
        self.tbl.setHorizontalHeaderLabels(["Abbreviation", "Spelled type"])
        self.tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for key, value in (settings.abbreviations or {}).items():
            self._add_abbr(key, value)
        lay.addWidget(self.tbl)
        row = QHBoxLayout()
        b = QPushButton("Add abbreviation")
        b.clicked.connect(lambda *_: self._add_abbr("", ""))
        row.addWidget(b)
        b = QPushButton("Remove abbreviation")
        b.clicked.connect(self._remove_abbr)
        row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        reset = bb.addButton("Reset to built-in", QDialogButtonBox.ButtonRole.ResetRole)
        reset.clicked.connect(self._reset)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _add(self):
        text = " ".join(self.ed_add.text().split())
        if not text:
            return
        existing = {self.lst.item(i).text().upper() for i in range(self.lst.count())}
        if text.upper() not in existing:
            self.lst.addItem(display_type(text))
        self.ed_add.clear()

    def _remove(self):
        for item in self.lst.selectedItems():
            self.lst.takeItem(self.lst.row(item))

    def _add_abbr(self, key="", value=""):
        r = self.tbl.rowCount()
        self.tbl.insertRow(r)
        self.tbl.setItem(r, 0, QTableWidgetItem(key))
        self.tbl.setItem(r, 1, QTableWidgetItem(value))

    def _remove_abbr(self):
        rows = sorted({i.row() for i in self.tbl.selectedIndexes()}, reverse=True)
        for r in rows:
            self.tbl.removeRow(r)

    def _reset(self):
        self.lst.clear()
        self.lst.addItems(OFFICIAL_TYPES)
        self.tbl.setRowCount(0)

    def _types(self):
        return [self.lst.item(i).text().strip() for i in range(self.lst.count()) if self.lst.item(i).text().strip()]

    def _abbreviations(self):
        out = {}
        for r in range(self.tbl.rowCount()):
            key = self.tbl.item(r, 0).text() if self.tbl.item(r, 0) else ""
            value = self.tbl.item(r, 1).text() if self.tbl.item(r, 1) else ""
            key = " ".join(key.upper().replace(".", "").split())
            value = " ".join(value.split())
            if key and value:
                out[key] = value
        return out

    def settings(self) -> StreetTypeSettings:
        types = self._types()
        abbreviations = self._abbreviations()
        custom = types != list(OFFICIAL_TYPES) or bool(abbreviations)
        if not custom:
            return StreetTypeSettings()
        return StreetTypeSettings(custom=True, types=types, abbreviations=abbreviations)
