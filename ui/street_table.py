# -*- coding: utf-8 -*-
"""
The master street list as a model and a view.

Qt asks the model only for the rows on screen, so a county's few thousand
street names open at once, and the search filters through a proxy model
instead of hiding rows one by one.
"""

from qgis.PyQt.QtCore import QAbstractTableModel, QModelIndex, QSize, QSortFilterProxyModel, Qt
from qgis.PyQt.QtWidgets import QAbstractItemView, QAbstractScrollArea, QHeaderView, QSizePolicy, QTableView

from ..core.streets.listing import COLUMNS, row_visible, sort_key

SID_ROLE = Qt.ItemDataRole.UserRole
SORT_ROLE = Qt.ItemDataRole.UserRole + 1


class StreetTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802 (Qt API name)
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(COLUMNS)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            value = row[index.column()]
            return value if isinstance(value, int) else str(value)
        if role == SID_ROLE:
            return row.street_id
        if role == SORT_ROLE:
            return sort_key(row, index.column())
        if role == Qt.ItemDataRole.TextAlignmentRole and isinstance(row[index.column()], int):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return COLUMNS[section]
        return None

    def row_of_sid(self, sid):
        for i, row in enumerate(self.rows):
            if row.street_id == sid:
                return i
        return -1


class StreetFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ""
        self.show_retired = False
        self.setSortRole(SORT_ROLE)

    def set_filter(self, text, show_retired):
        self.text, self.show_retired = text, bool(show_retired)
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):  # noqa: N802
        rows = self.sourceModel().rows
        return 0 <= source_row < len(rows) and row_visible(rows[source_row], self.text, self.show_retired)


class StreetTableView(QTableView):
    """The street list: scrolls instead of widening or lengthening the window."""

    def __init__(self, parent=None, min_height=180, max_height=320):
        super().__init__(parent)
        self._floor = min_height
        self.source = StreetTableModel(self)
        self.proxy = StreetFilter(self)
        self.proxy.setSourceModel(self.source)
        self.setModel(self.proxy)
        self.setMinimumWidth(1)
        self.setMinimumHeight(min_height)
        if max_height:
            self.setMaximumHeight(max_height)
        self.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setWordWrap(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(self.fontMetrics().height() + 6)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSortingEnabled(True)
        self.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        header = self.horizontalHeader()
        header.setMinimumSectionSize(40)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)       # the name takes what is left
        for column in range(1, len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

    def minimumSizeHint(self):  # noqa: N802
        return QSize(1, self._floor)

    def sizeHint(self):  # noqa: N802
        return QSize(320, self._floor)

    def set_rows(self, rows):
        self.source.set_rows(rows)

    def set_filter(self, text, show_retired):
        self.proxy.set_filter(text, show_retired)

    def selected_sid(self):
        rows = self.selectionModel().selectedRows()
        return rows[0].data(SID_ROLE) if rows else None

    def select_sid(self, sid):
        row = self.source.row_of_sid(sid)
        if row < 0:
            return False
        index = self.proxy.mapFromSource(self.source.index(row, 0))
        if not index.isValid():          # filtered out by the search
            return False
        self.selectRow(index.row())
        self.scrollTo(index)
        return True
