# -*- coding: utf-8 -*-
"""
The master street names table: schema, creation (GeoPackage or any database
connection QGIS knows), reading and writing records through the edit buffer.
"""

from datetime import datetime
from typing import Dict, Hashable, List, Optional, Sequence

from qgis.core import QgsFeature, QgsFields, QgsVectorLayer

from .. import tables as _tables

from ...compat import make_field, py_value
from ..layer_access import ConfigError, ensure_editable
from ..schema_pack import street_columns
from .names import NAME_PARTS, STATUS_ACTIVE, StreetRecord, clean, full_name

F_ID = "StreetID"
F_FULL = "FullName"
F_SCOPE = "Scope"
F_STATUS = "Status"
F_ALIAS = "AliasOf"
F_DATE = "DateUpdate"

# (name, kind, length) — the same list the fresh schema creates
MASTER_SCHEMA = street_columns()
REQUIRED = [F_ID, "St_Name", F_FULL, F_STATUS]
DEFAULT_TABLE = "street_names"


def master_fields() -> QgsFields:
    flds = QgsFields()
    for name, kind, length in MASTER_SCHEMA:
        flds.append(make_field(name, kind, length))
    return flds


# ---------------------------------------------------------------------------
# creation
# ---------------------------------------------------------------------------
def create_in_geopackage(path: str, table: str = DEFAULT_TABLE) -> QgsVectorLayer:
    """Create (or add to) a GeoPackage with an empty street-names table and load it."""
    return _tables.create_in_geopackage(path, table, master_fields(), display_name="Street names")


def db_connections() -> List[tuple]:
    return _tables.db_connections()


def create_in_connection(provider_key: str, connection: str, schema: str, table: str = DEFAULT_TABLE
                         ) -> QgsVectorLayer:
    """Create the street-names table through a QGIS database connection (PostGIS, SQL Server, Oracle …)."""
    return _tables.create_in_connection(provider_key, connection, schema, table, master_fields(),
                                        display_name="Street names")


# ---------------------------------------------------------------------------
class StreetRepository:
    """Read / write master records on a street-names layer (any provider)."""

    def __init__(self, layer: QgsVectorLayer):
        if layer is None:
            raise ConfigError("Choose or create a master street names table first.")
        self.layer = layer
        self.idx = {}
        flds = layer.fields()
        for name, _k, _l in MASTER_SCHEMA:
            i = flds.lookupField(name)
            if i >= 0:
                self.idx[name] = i

    def missing_fields(self) -> List[str]:
        return [n for n, _k, _l in MASTER_SCHEMA if n not in self.idx]

    def require(self):
        miss = [n for n in REQUIRED if n not in self.idx]
        if miss:
            raise ConfigError("The street-names table lacks required fields: " + ", ".join(miss) +
                              ". Use 'Add missing fields' or create a new table.")

    def add_missing_fields(self) -> List[str]:
        miss = self.missing_fields()
        if not miss:
            return []
        ensure_editable(self.layer)
        self.layer.beginEditCommand("NENA: street table fields")
        for name, kind, length in MASTER_SCHEMA:
            if name in miss:
                self.layer.addAttribute(make_field(name, kind, length))
        self.layer.endEditCommand()
        self.layer.updateFields()
        self.__init__(self.layer)
        return miss

    # -- reading -----------------------------------------------------------
    def _get(self, f, name):
        i = self.idx.get(name)
        return py_value(f.attribute(i)) if i is not None else None

    def record_of(self, f) -> StreetRecord:
        return StreetRecord(
            street_id=clean(self._get(f, F_ID)),
            parts={p: clean(self._get(f, p)) for p in NAME_PARTS},
            scope=clean(self._get(f, F_SCOPE)),
            status=clean(self._get(f, F_STATUS)) or STATUS_ACTIVE,
            alias_of=clean(self._get(f, F_ALIAS)),
            fid=f.id())

    def records(self) -> List[StreetRecord]:
        self.require()
        return [self.record_of(f) for f in self.layer.getFeatures()]

    def by_id(self) -> Dict[str, StreetRecord]:
        return {r.street_id: r for r in self.records() if r.street_id}

    # -- writing (inside a caller's edit command or our own) ---------------
    def _values(self, rec: StreetRecord, stamp) -> Dict[int, object]:
        vals = {F_ID: rec.street_id, F_FULL: full_name(rec.parts), F_SCOPE: rec.scope or None,
                F_STATUS: rec.status, F_ALIAS: rec.alias_of or None}
        vals.update({p: rec.parts.get(p) or None for p in NAME_PARTS})
        if stamp is not None:
            vals[F_DATE] = stamp
        return {self.idx[k]: _coerce(self.layer, self.idx[k], v) for k, v in vals.items() if k in self.idx}

    def add(self, recs: Sequence[StreetRecord], command: str = "NENA: add street name") -> List[Hashable]:
        self.require()
        recs = [r for r in recs if clean(r.parts.get("St_Name"))]
        if not recs:
            return []
        ensure_editable(self.layer)
        stamp = datetime.now().replace(microsecond=0)
        self.layer.beginEditCommand(command)
        fids = []
        try:
            for rec in recs:
                f = QgsFeature(self.layer.fields())
                attrs = [None] * self.layer.fields().count()
                for i, v in self._values(rec, stamp).items():
                    attrs[i] = v
                f.setAttributes(attrs)
                if not self.layer.addFeature(f):
                    raise ConfigError("The data provider refused the new street name.")
                rec.fid = f.id()
                fids.append(f.id())
        except Exception:
            self.layer.destroyEditCommand()
            raise
        self.layer.endEditCommand()
        return fids

    def update(self, recs: Sequence[StreetRecord], command: str = "NENA: update street name") -> int:
        self.require()
        ensure_editable(self.layer)
        stamp = datetime.now().replace(microsecond=0)
        self.layer.beginEditCommand(command)
        try:
            for rec in recs:
                self.layer.changeAttributeValues(rec.fid, self._values(rec, stamp))
        except Exception:
            self.layer.destroyEditCommand()
            raise
        self.layer.endEditCommand()
        return len(recs)

    def refresh_full_names(self, sids: Optional[Sequence[str]] = None) -> int:
        """Recompute FullName (after parts were typed into the table)."""
        if F_FULL not in self.idx:
            return 0
        changes = {}
        for f in self.layer.getFeatures():
            rec = self.record_of(f)
            if sids is not None and rec.street_id not in sids:
                continue
            fn = full_name(rec.parts)
            if clean(self._get(f, F_FULL)) != fn:
                changes[f.id()] = {self.idx[F_FULL]: fn}
        if not changes:
            return 0
        ensure_editable(self.layer)
        self.layer.beginEditCommand("NENA: full street names")
        for fid, vals in changes.items():
            self.layer.changeAttributeValues(fid, vals)
        self.layer.endEditCommand()
        return len(changes)


def _coerce(layer, idx, value):
    if value is None:
        return None
    fld = layer.fields().at(idx)
    tname = (fld.typeName() or "").lower()
    if isinstance(value, datetime):
        if "date" in tname or "time" in tname:
            from qgis.PyQt.QtCore import QDateTime
            return QDateTime(value)
        return value.isoformat(timespec="seconds")
    return str(value) if not fld.isNumeric() else value

