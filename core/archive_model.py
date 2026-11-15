# -*- coding: utf-8 -*-
"""
What one saved edit stores in a version table.

Plain Python. Each save adds one version of a feature. An insert stores the
feature that was added (version 1 when that feature is new). An update stores
the feature as it was saved, plus the old and new values of the fields that
changed. A delete stores the feature that was removed. The live table keeps
the current feature. The previous version is the earlier row for that feature.

The version columns are the plugin's own columns (see ARCHIVE_COLUMNS). They
are not NENA-STA-006 fields. A column the live layer already uses keeps the
live value.
"""

import json
import uuid
from typing import Dict, Iterable, List, Optional, Sequence

from .schema_pack import ARCHIVE_COLUMNS, SCHEMA_LAYERS

CHANGE_INSERT = "insert"
CHANGE_UPDATE = "update"
CHANGE_DELETE = "delete"
CHANGE_TYPES = (CHANGE_INSERT, CHANGE_UPDATE, CHANGE_DELETE)

ROLE_ORDER = tuple(spec.key for spec in SCHEMA_LAYERS)
ROLE_TITLE = {spec.key: spec.title for spec in SCHEMA_LAYERS}

_META_WIDTH = {name: length for name, _kind, length, _desc in ARCHIVE_COLUMNS if length}


def roles_missing_archive(live_ready: Dict[str, bool], archive_ready: Dict[str, bool]) -> List[str]:
    """Titles of live layers that have no version table yet."""
    return [ROLE_TITLE[role] for role in ROLE_ORDER
            if live_ready.get(role) and not archive_ready.get(role)]


def same_value(left, right) -> bool:
    """True when two attribute values are the same for change tracking."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and bool(left) is bool(right)
    if left == right:
        return True
    if _blank(left) and _blank(right):
        return True
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    return False


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def diff_field_names(before: Optional[dict], after: Optional[dict]) -> List[str]:
    """Field names whose values differ, in a stable order."""
    before = before or {}
    after = after or {}
    names = []
    seen = set()
    for name in list(before) + list(after):
        if name in seen:
            continue
        seen.add(name)
        if not same_value(before.get(name), after.get(name)):
            names.append(name)
    return names


def join_changed(names: Sequence[str], geometry_changed: bool, limit: int = 254) -> str:
    """Comma-separated changed fields, with ``geometry`` when the shape changed."""
    items = [name for name in names if name]
    if geometry_changed and "geometry" not in items:
        items.append("geometry")
    text = ", ".join(items)
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[:limit - 3].rstrip() + "..."


def feature_key(values: Optional[dict], fields: Iterable[str]) -> str:
    """First non-empty NGUID or StreetID among ``fields``."""
    if not values:
        return ""
    lookup = {str(key).lower(): value for key, value in values.items()}
    for name in fields or ():
        if not name:
            continue
        value = lookup.get(str(name).lower())
        if _blank(value):
            continue
        return str(value)
    return ""


def _clip(value, width: int) -> str:
    if value is None:
        return ""
    text = str(value)
    if width and len(text) > width:
        return text[:width]
    return text


def _jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat(sep=" ")
        except TypeError:
            return value.isoformat()
    return str(value)


def format_change_values(values: Optional[dict], limit: int) -> str:
    """JSON object of changed field values, clipped to the column width."""
    if not values:
        return ""
    payload = {str(name): _jsonable(val) for name, val in values.items()}
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(", ", ": "))
    return _clip(text, limit)


def _chain_keys(before: dict, after: dict, fields: Sequence[str], source_fid) -> List[str]:
    """Lookup tokens for the version chain. A renamed key stays on the old chain."""
    keys = []
    width = _META_WIDTH.get("FeatureKey", 254)
    for values in (after, before):
        found = feature_key(values, fields)
        if not found:
            continue
        token = "k:" + _clip(found, width)
        if token not in keys:
            keys.append(token)
    if source_fid is not None and str(source_fid).strip():
        token = "f:" + _clip(source_fid, _META_WIDTH.get("SourceFid", 38))
        if token not in keys:
            keys.append(token)
    return keys


def assign_versions(rows: Sequence[Optional[dict]], existing_max: Optional[Dict[str, int]] = None) -> None:
    """Fill an empty ``Version`` on each row this plugin owns. 1 is the first version."""
    running = {}
    for key, value in (existing_max or {}).items():
        try:
            running[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    for row in rows:
        if not row or not row.get("_track_version"):
            continue
        current = row.get("Version")
        if current is not None:
            try:
                number = int(current)
            except (TypeError, ValueError):
                number = None
            if number is not None:
                for key in row.get("_version_keys") or []:
                    if number > running.get(key, 0):
                        running[key] = number
                continue
        keys = [key for key in (row.get("_version_keys") or []) if key]
        highest = 0
        for key in keys:
            highest = max(highest, running.get(key, 0))
        number = highest + 1
        row["Version"] = number
        for key in keys:
            running[key] = number


def stored_change(change_type: str, before: Optional[dict], after: Optional[dict], *,
                  geometry_changed: bool = False, feature_key_fields: Sequence[str] = (),
                  live_names: Optional[Iterable[str]] = None, when=None, who: str = "",
                  source_fid=None, archive_id: str = "", version=None) -> Optional[dict]:
    """
    Attribute values for one version row, or None when an update changed nothing.

    An update stores the feature after the save. ``OldValues`` and ``NewValues``
    hold the fields that differ. ``live_names`` are the live layer's field names.
    A version column that already exists there keeps the value from the stored feature.
    ``Version`` stays empty until :func:`assign_versions` numbers the chain.
    """
    if change_type not in CHANGE_TYPES:
        raise ValueError("Change type must be insert, update, or delete.")
    before = dict(before or {})
    after = dict(after or {})
    old_vals = {}
    new_vals = {}
    if change_type == CHANGE_INSERT:
        snapshot = after
        changed = ""
    elif change_type == CHANGE_DELETE:
        snapshot = before
        changed = ""
    else:
        names = diff_field_names(before, after)
        changed = join_changed(names, geometry_changed)
        if not changed:
            return None
        snapshot = after
        for name in names:
            old_vals[name] = before.get(name)
            new_vals[name] = after.get(name)
    occupied = {str(name).lower() for name in (live_names if live_names is not None else snapshot)}
    version_value = None
    if version is not None:
        version_value = int(version)
    meta = {
        "ArchiveID": archive_id or uuid.uuid4().hex,
        "Version": version_value,
        "ChangeType": change_type,
        "ChangeDate": when,
        "ChangedBy": _clip(who, _META_WIDTH.get("ChangedBy", 64)),
        "FeatureKey": _clip(feature_key(snapshot, feature_key_fields), _META_WIDTH.get("FeatureKey", 254)),
        "SourceFid": "" if source_fid is None else _clip(source_fid, _META_WIDTH.get("SourceFid", 38)),
        "ChangedFields": _clip(changed, _META_WIDTH.get("ChangedFields", 254)),
        "OldValues": format_change_values(old_vals, _META_WIDTH.get("OldValues", 4000)),
        "NewValues": format_change_values(new_vals, _META_WIDTH.get("NewValues", 4000)),
    }
    track_version = "version" not in occupied
    row = dict(snapshot)
    for name, value in meta.items():
        if name == "Version" and not track_version:
            continue
        if name.lower() in occupied:
            continue
        row[name] = value
    row["_version_keys"] = _chain_keys(before, after, feature_key_fields, source_fid)
    row["_track_version"] = track_version
    return row
