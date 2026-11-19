# -*- coding: utf-8 -*-
"""Rows of the master street list, and which of them a search shows. No Qt here."""

from typing import Dict, Iterable, List, NamedTuple, Tuple

from . import names as N


class StreetRow(NamedTuple):
    full_name: str
    status: str
    scope: str
    segments: int
    addresses: int
    street_id: str


COLUMNS = ("Full name", "Status", "Scope", "Segments", "Addresses")


def build_rows(records: Iterable, counts: Dict[str, Tuple[int, int]]) -> List[StreetRow]:
    """One row per master record, by name, with how many centerlines and addresses use it."""
    rows = []
    for rec in records:
        seg, adr = counts.get(rec.street_id, (0, 0))
        rows.append(StreetRow(rec.full_name or "", rec.status or "", rec.scope or "", int(seg), int(adr),
                              rec.street_id))
    rows.sort(key=lambda r: r.full_name.upper())
    return rows


def row_visible(row: StreetRow, text: str, show_retired: bool) -> bool:
    """Whether the search ``text`` (any case) and the Show retired box keep this row."""
    if row.status == N.STATUS_RETIRED and not show_retired:
        return False
    text = (text or "").strip().upper()
    return not text or text in row.full_name.upper()


def sort_key(row: StreetRow, column: int):
    value = row[column]
    return value if isinstance(value, int) else str(value).upper()
