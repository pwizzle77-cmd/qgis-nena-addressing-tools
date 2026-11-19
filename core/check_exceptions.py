# -*- coding: utf-8 -*-
"""
Check exceptions: results someone has reviewed and accepted.

One table holds every exception, so the NENA layers keep only NENA fields
and each exception says who accepted what, when, and why. An exception
covers one result: one check, on one feature, optionally for one zone layer
kind. Every other check still runs on that feature.

* An exception with an **Expires** date stops applying after that date.
* When the feature was edited after the exception was made (its DateUpdate is
  newer than the one stored with the exception), the result shows again,
  marked "excused before the last edit", so an old excuse does not hide a new
  problem.

The matching is plain Python. ``ExceptionSet.from_layer`` reads the table.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Tuple

from .schema import MANDATORY, OPTIONAL, FieldDef

TABLE = "CheckExceptions"
EXCUSED = "excused"
STALE = "stale"

FIELDS = (
    FieldDef("ExceptionID", MANDATORY, "string", 38, "Id of this exception"),
    FieldDef("CheckCode", MANDATORY, "string", 40, "The check that is excused, e.g. ZONE_ROAD_NOT_SPLIT"),
    FieldDef("LayerRole", MANDATORY, "string", 40, "centerline, address, or zone"),
    FieldDef("FeatureKey", MANDATORY, "string", 254, "NGUID of the excused feature (fid:N when it has none)"),
    FieldDef("ZoneKind", OPTIONAL, "string", 40, "Zone layer kind it applies to; empty for every zone"),
    FieldDef("Reason", MANDATORY, "string", 254, "Why the result is accepted"),
    FieldDef("EnteredBy", OPTIONAL, "string", 100, "Who accepted it"),
    FieldDef("DateEntered", OPTIONAL, "datetime", 0, "When it was accepted"),
    FieldDef("Expires", OPTIONAL, "datetime", 0, "Stops applying after this date"),
    FieldDef("FeatureDate", OPTIONAL, "datetime", 0, "The feature's DateUpdate when it was accepted"),
)
FIELD_NAMES = tuple(d.name for d in FIELDS)


def as_datetime(value) -> Optional[datetime]:
    """A Python datetime from a datetime, date, QDateTime/QDate, or ISO text. None when empty."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    for name in ("toPyDateTime", "toPython"):
        method = getattr(value, name, None)
        if method is not None:
            try:
                if hasattr(value, "isValid") and not value.isValid():
                    return None
                return as_datetime(method())
            except Exception:  # noqa: BLE001
                return None
    if hasattr(value, "toPyDate"):
        try:
            return as_datetime(value.toPyDate())
        except Exception:  # noqa: BLE001
            return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("T", " ").split(".")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


@dataclass
class CheckException:
    exception_id: str
    check: str
    role: str
    key: str
    zone_kind: str = ""
    reason: str = ""
    entered_by: str = ""
    entered: Optional[datetime] = None
    expires: Optional[datetime] = None
    feature_date: Optional[datetime] = None
    fid: object = None                  # feature id in the exceptions table

    def applies_to(self, check: str, role: str, key: str, zone_kind: str = "") -> bool:
        if self.check != check or self.role != role or self.key != key:
            return False
        return not self.zone_kind or self.zone_kind == (zone_kind or "")

    def expired(self, now: Optional[datetime] = None) -> bool:
        return self.expires is not None and self.expires < (now or datetime.now())

    def to_values(self) -> dict:
        return {
            "ExceptionID": self.exception_id, "CheckCode": self.check, "LayerRole": self.role,
            "FeatureKey": self.key, "ZoneKind": self.zone_kind or None, "Reason": self.reason,
            "EnteredBy": self.entered_by or None, "DateEntered": self.entered, "Expires": self.expires,
            "FeatureDate": self.feature_date,
        }


def new_exception(check: str, role: str, key: str, reason: str, zone_kind: str = "", entered_by: str = "",
                  expires=None, feature_date=None, now: Optional[datetime] = None) -> CheckException:
    if not (reason or "").strip():
        raise ValueError("Give a reason for the exception.")
    return CheckException(uuid.uuid4().hex, check, role, key, zone_kind or "", reason.strip()[:254],
                          (entered_by or "")[:100], (now or datetime.now()).replace(microsecond=0),
                          as_datetime(expires), as_datetime(feature_date))


def from_values(values: dict, fid=None) -> Optional[CheckException]:
    """An exception from one table row (field names in any case)."""
    lower = {str(k).lower(): v for k, v in (values or {}).items()}

    def text(name):
        value = lower.get(name.lower())
        return "" if value is None else str(value).strip()

    check, role, key = text("CheckCode"), text("LayerRole"), text("FeatureKey")
    if not (check and role and key):
        return None
    return CheckException(text("ExceptionID") or str(fid or ""), check, role, key, text("ZoneKind"),
                          text("Reason"), text("EnteredBy"), as_datetime(lower.get("dateentered")),
                          as_datetime(lower.get("expires")), as_datetime(lower.get("featuredate")), fid)


class ExceptionSet:
    """The exceptions in force, looked up by check, layer role, and feature key."""

    def __init__(self, items: Iterable[CheckException] = ()):
        self.items: List[CheckException] = []
        self._by: Dict[Tuple[str, str, str], List[CheckException]] = {}
        for item in items:
            self.add(item)

    def add(self, item: CheckException) -> None:
        self.items.append(item)
        self._by.setdefault((item.check, item.role, item.key), []).append(item)

    def __len__(self):
        return len(self.items)

    def find(self, check: str, role: str, key: str, zone_kind: str = "",
             now: Optional[datetime] = None) -> Optional[CheckException]:
        for item in self._by.get((check, role, key), ()):
            if item.applies_to(check, role, key, zone_kind) and not item.expired(now):
                return item
        return None

    def status(self, check: str, role: str, key: str, zone_kind: str = "", feature_date=None,
               now: Optional[datetime] = None) -> Tuple[Optional[str], Optional[CheckException]]:
        """(``EXCUSED`` / ``STALE`` / None, the exception)."""
        item = self.find(check, role, key, zone_kind, now)
        if item is None:
            return None, None
        edited = as_datetime(feature_date)
        if edited is not None and item.feature_date is not None and edited > item.feature_date:
            return STALE, item
        return EXCUSED, item

    @classmethod
    def from_layer(cls, layer) -> "ExceptionSet":
        """Read every row of the exceptions table (a QGIS layer)."""
        out = cls()
        if layer is None:
            return out
        from ..compat import py_value
        names = [f.name() for f in layer.fields()]
        for feat in layer.getFeatures():
            values = {name: py_value(feat.attribute(i)) for i, name in enumerate(names)}
            item = from_values(values, feat.id())
            if item is not None:
                out.add(item)
        return out


def write_exception(layer, item: CheckException) -> None:
    """Add one exception row to the table and save it."""
    from qgis.core import QgsFeature
    from .archive_watch import _coerce
    fields = layer.fields()
    feat = QgsFeature(fields)
    for name, value in item.to_values().items():
        idx = fields.lookupField(name)
        if idx >= 0:
            feat.setAttribute(idx, _coerce(value, fields.field(idx)) if value is not None else None)
    was_editing = layer.isEditable()
    if not was_editing and not layer.startEditing():
        raise ValueError(f"'{layer.name()}' cannot be edited.")
    if not layer.addFeature(feat):
        if not was_editing:
            layer.rollBack()
        raise ValueError(f"'{layer.name()}' refused the exception.")
    if not was_editing and not layer.commitChanges():
        errors = "; ".join(layer.commitErrors()[:3]) if hasattr(layer, "commitErrors") else ""
        layer.rollBack()
        raise ValueError(f"'{layer.name()}' did not save the exception. {errors}")


def remove_exception(layer, item: CheckException) -> None:
    """Delete one exception row and save the table."""
    if item.fid is None:
        return
    was_editing = layer.isEditable()
    if not was_editing and not layer.startEditing():
        raise ValueError(f"'{layer.name()}' cannot be edited.")
    layer.deleteFeature(item.fid)
    if not was_editing and not layer.commitChanges():
        layer.rollBack()
        raise ValueError(f"'{layer.name()}' did not save the change.")
