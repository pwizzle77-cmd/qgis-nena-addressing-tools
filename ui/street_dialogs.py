# -*- coding: utf-8 -*-
"""Dialogs for master street names: edit/rename, build from centerlines, name check results."""

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                                 QFormLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget,
                                 QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QHBoxLayout)

from ..core.layer_access import ConfigError
from ..core.schema import DIRECTIONAL_DOMAIN
from ..core.streets import names as N
from .widgets import hint

PART_LABELS = {
    "St_PreMod": "Pre modifier", "St_PreDir": "Pre directional", "St_PreTyp": "Pre type",
    "St_PreSep": "Pre type separator", "St_Name": "Street name", "St_PosTyp": "Post type (suffix)",
    "St_PosDir": "Post directional", "St_PosMod": "Post modifier",
}


class NameDialog(QDialog):
    """Enter or change a road name; shows the full name and uniqueness conflicts live."""

    def __init__(self, parent, title, parts=None, scope="", scope_enabled=False, check=None,
                 show_alias=False, keep_alias=True, info=""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.check = check            # callable(parts, scope) -> [Conflict]; may raise ConfigError
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

    def parts(self):
        return N.parts_from({p: self._text(p) for p in N.NAME_PARTS})

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
