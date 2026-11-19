# -*- coding: utf-8 -*-
"""
Read one feature's history from its version table, and plan a roll back.

Plain Python, so it can be tested without QGIS. The window in
``ui/history_window.py`` reads the version rows, turns each into a
:class:`HistoryEntry` here, and asks this module which rows belong to the
selected feature, what each version changed, and which live fields a roll back
would write.

A version row holds the whole feature as it was saved (see
``archive_model.stored_change``), plus the plugin's own columns. An update also
holds ``OldValues`` and ``NewValues``: JSON objects of the fields that changed.
Those columns are clipped at 4000 characters, so a very large change can leave
broken JSON. Then the change is worked out by comparing the saved versions.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .archive_model import CHANGE_DELETE, CHANGE_INSERT, CHANGE_UPDATE, same_value
from .schema_pack import ARCHIVE_COLUMNS

META_NAMES = tuple(name for name, _kind, _length, _desc in ARCHIVE_COLUMNS)
_META_LOWER = {name.lower() for name in META_NAMES}
# Columns the data source owns. They are never shown as changes or written back.
SKIP_NAMES = {
    "fid", "ogc_fid", "objectid", "object_id", "gdb_geomattr_data",
    "shape", "shape_length", "shape_area", "shape_len", "geom", "geometry", "wkb_geometry",
}

CHANGE_TITLES = {
    CHANGE_INSERT: "Added",
    CHANGE_UPDATE: "Changed",
    CHANGE_DELETE: "Deleted",
}

GEOMETRY = "geometry"


@dataclass
class FieldChange:
    """One field in one version: the value before the save and after it."""

    name: str
    old: Any = None
    new: Any = None


@dataclass
class HistoryEntry:
    """One version row, read from a version table."""

    archive_id: str = ""
    version: Optional[int] = None
    change_type: str = ""
    when: Any = None
    who: str = ""
    feature_key: str = ""
    source_fid: str = ""
    changed_fields: List[str] = field(default_factory=list)
    old_values: Dict[str, Any] = field(default_factory=dict)
    new_values: Dict[str, Any] = field(default_factory=dict)
    values_ok: bool = True        # False when OldValues/NewValues could not be read
    values: Dict[str, Any] = field(default_factory=dict)   # the feature as stored in this version
    geometry: Any = None          # the stored shape (a QgsGeometry in QGIS), or None
    row_id: Any = None            # feature id of the row in the version table

    @property
    def title(self) -> str:
        return CHANGE_TITLES.get(self.change_type, self.change_type.title() or "Saved")

    @property
    def geometry_changed(self) -> bool:
        return GEOMETRY in (name.lower() for name in self.changed_fields)


# ---------------------------------------------------------------------------
# Reading rows
# ---------------------------------------------------------------------------
def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _text(value) -> str:
    return "" if _blank(value) else str(value).strip()


def _lookup(values: dict, name: str):
    """Value of ``name`` in ``values``, matching the name without regard to case."""
    if name in values:
        return values[name]
    lower = name.lower()
    for key, value in values.items():
        if str(key).lower() == lower:
            return value
    return None


def parse_values(text) -> tuple:
    """``(dict, ok)`` from an OldValues/NewValues column. Empty text is ``({}, True)``."""
    if _blank(text):
        return {}, True
    if isinstance(text, dict):
        return dict(text), True
    try:
        data = json.loads(str(text))
    except (TypeError, ValueError):
        return {}, False
    if not isinstance(data, dict):
        return {}, False
    return data, True


def split_fields(text) -> List[str]:
    """``ChangedFields`` as a list. A clipped list ends in '...', which is dropped."""
    names = []
    for part in _text(text).split(","):
        name = part.strip()
        if not name or name.endswith("..."):
            continue
        names.append(name)
    return names


def _int(value) -> Optional[int]:
    if _blank(value) or isinstance(value, bool):
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def entry_from_row(attrs: dict, geometry=None, row_id=None) -> HistoryEntry:
    """A :class:`HistoryEntry` from the attribute values of one version row."""
    attrs = dict(attrs or {})
    old, old_ok = parse_values(_lookup(attrs, "OldValues"))
    new, new_ok = parse_values(_lookup(attrs, "NewValues"))
    values = {}
    for name, value in attrs.items():
        lower = str(name).lower()
        if lower in _META_LOWER or lower in SKIP_NAMES:
            continue
        values[str(name)] = value
    change = _text(_lookup(attrs, "ChangeType")).lower()
    return HistoryEntry(
        archive_id=_text(_lookup(attrs, "ArchiveID")),
        version=_int(_lookup(attrs, "Version")),
        change_type=change,
        when=_lookup(attrs, "ChangeDate"),
        who=_text(_lookup(attrs, "ChangedBy")),
        feature_key=_text(_lookup(attrs, "FeatureKey")),
        source_fid=_text(_lookup(attrs, "SourceFid")),
        changed_fields=split_fields(_lookup(attrs, "ChangedFields")),
        old_values=old,
        new_values=new,
        values_ok=old_ok and new_ok,
        values=values,
        geometry=geometry,
        row_id=row_id,
    )


# ---------------------------------------------------------------------------
# Which rows belong to the selected feature
# ---------------------------------------------------------------------------
def current_key(values: Optional[dict], key_fields: Sequence[str]) -> str:
    """The first non-empty key (NGUID or StreetID) of a live feature."""
    for name in key_fields or ():
        if not name:
            continue
        value = _lookup(values or {}, name)
        if not _blank(value):
            return str(value).strip()
    return ""


def _old_keys(entry: HistoryEntry, key_fields: Sequence[str]) -> List[str]:
    """Keys this update replaced (the feature's NGUID was changed in this save)."""
    found = []
    for name in key_fields or ():
        value = _lookup(entry.old_values, name)
        if not _blank(value):
            found.append(str(value).strip())
    return found


def feature_entries(entries: Iterable[HistoryEntry], key: str, fid, key_fields: Sequence[str] = ()) -> List[HistoryEntry]:
    """
    The rows that belong to one live feature, oldest first.

    A row belongs when its FeatureKey is the feature's key. When the key was
    changed by a save, the earlier key is followed too. A row with no key
    belongs when it was saved from the same feature id. With no key on the
    live feature, rows are matched by feature id alone. Feature ids can be
    reused after a delete, so a row with a different key is never taken just
    because the id matches.
    """
    entries = list(entries or [])
    fid_text = "" if fid is None else str(fid).strip()
    keys = {key.strip()} if key and key.strip() else set()
    chosen = {}
    changed = True
    while changed:
        changed = False
        for pos, entry in enumerate(entries):
            if pos in chosen:
                continue
            take = False
            if entry.feature_key and entry.feature_key in keys:
                take = True
            elif fid_text and entry.source_fid == fid_text and (not entry.feature_key or not keys):
                take = True
            if not take:
                continue
            chosen[pos] = entry
            changed = True
            if entry.feature_key and not keys:
                keys.add(entry.feature_key)
            for old in _old_keys(entry, key_fields):
                if old not in keys:
                    keys.add(old)
    return sort_entries(chosen.values())


def _when_key(value) -> str:
    if value is None:
        return ""
    for attr in ("toString",):
        method = getattr(value, attr, None)
        if method is not None and not isinstance(value, str):
            try:
                return str(method("yyyy-MM-dd HH:mm:ss"))
            except TypeError:
                pass
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat(sep=" ")
        except TypeError:
            return value.isoformat()
    return str(value)


def sort_entries(entries: Iterable[HistoryEntry]) -> List[HistoryEntry]:
    """Oldest first: by version number, then by date."""
    def order(entry):
        number = entry.version if entry.version is not None else 10 ** 9
        return (number, _when_key(entry.when), str(entry.row_id or ""))
    return sorted(entries, key=order)


# ---------------------------------------------------------------------------
# What each version changed
# ---------------------------------------------------------------------------
def _shown(name: str) -> bool:
    lower = str(name).lower()
    return lower not in _META_LOWER and lower not in SKIP_NAMES


def field_changes(entry: HistoryEntry, previous: Optional[HistoryEntry] = None) -> List[FieldChange]:
    """
    The fields this version changed, with their values before and after.

    An update reads its stored OldValues/NewValues. When they are missing or
    were cut off, the change is worked out from the previous version. An
    added feature lists every field that has a value. A deleted one lists the
    values it had. A changed shape is listed as ``geometry`` with no values.
    """
    changes: List[FieldChange] = []
    if entry.change_type == CHANGE_INSERT:
        for name, value in entry.values.items():
            if _shown(name) and not _blank(value):
                changes.append(FieldChange(name, None, value))
        return changes
    if entry.change_type == CHANGE_DELETE:
        for name, value in entry.values.items():
            if _shown(name) and not _blank(value):
                changes.append(FieldChange(name, value, None))
        return changes

    if entry.values_ok and (entry.old_values or entry.new_values):
        order = [name for name in entry.changed_fields if name.lower() != GEOMETRY]
        for name in list(entry.old_values) + list(entry.new_values):
            if name not in order:
                order.append(name)
        for name in order:
            if not _shown(name):
                continue
            changes.append(FieldChange(name, _lookup(entry.old_values, name), _lookup(entry.new_values, name)))
    elif previous is not None:
        before = previous.values
        names = []
        for name in list(before) + list(entry.values):
            if name not in names and _shown(name):
                names.append(name)
        for name in names:
            old = _lookup(before, name)
            new = _lookup(entry.values, name)
            if not same_value(old, new):
                changes.append(FieldChange(name, old, new))
    else:
        for name in entry.changed_fields:
            if name.lower() != GEOMETRY and _shown(name):
                changes.append(FieldChange(name, None, _lookup(entry.values, name)))
    if entry.geometry_changed:
        changes.append(FieldChange(GEOMETRY))
    return changes


def changes_summary(entry: HistoryEntry, limit: int = 6) -> str:
    """Short text for the version list: what this version changed."""
    if entry.change_type == CHANGE_INSERT:
        return "Feature added"
    if entry.change_type == CHANGE_DELETE:
        return "Feature deleted"
    names = [("shape" if name.lower() == GEOMETRY else name) for name in entry.changed_fields]
    if not names:
        names = [("shape" if name.lower() == GEOMETRY else name)
                 for name in list(entry.new_values) or list(entry.old_values)]
    if not names:
        return ""
    if len(names) > limit:
        return ", ".join(names[:limit]) + f" (+{len(names) - limit} more)"
    return ", ".join(names)


# ---------------------------------------------------------------------------
# Roll back
# ---------------------------------------------------------------------------
def rollback_changes(entry: HistoryEntry, current: dict, live_names: Iterable[str],
                     keep: Iterable[str] = ()) -> List[FieldChange]:
    """
    Live fields to write so the feature matches ``entry`` again.

    Each change is ``(field, current value, value in that version)``, using the
    live layer's field names. Only fields that exist on the live layer and
    differ are listed. The plugin's version columns, data-source columns, and
    the names in ``keep`` (such as the edit-tracking fields the save fills
    itself) are left alone.
    """
    keep_lower = {str(name).lower() for name in keep or ()}
    out = []
    for name in live_names or ():
        lower = str(name).lower()
        if not _shown(name) or lower in keep_lower:
            continue
        if not any(str(key).lower() == lower for key in entry.values):
            continue
        target = _lookup(entry.values, name)
        now = _lookup(current or {}, name)
        if same_value(now, target):
            continue
        out.append(FieldChange(name, now, target))
    return out


def display_value(value) -> str:
    """A value as it reads in the history window. Empty is ''."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    method = getattr(value, "toString", None)
    if method is not None and not isinstance(value, str):
        for fmt in ("yyyy-MM-dd HH:mm:ss",):
            try:
                return str(method(fmt))
            except TypeError:
                break
    if hasattr(value, "isoformat") and not isinstance(value, str):
        try:
            return value.isoformat(sep=" ")
        except TypeError:
            return value.isoformat()
    return str(value)


def pretty_json(value) -> str:
    """Stored OldValues/NewValues text, indented for reading. Broken JSON comes back as stored."""
    if _blank(value):
        return ""
    data, ok = parse_values(value)
    if not ok:
        return str(value)
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2)
