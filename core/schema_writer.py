# -*- coding: utf-8 -*-
"""Create the fresh NENA schema (centerlines, street names, address points, driveways, access points, lookups)."""

import os
from dataclasses import dataclass, field

from qgis.core import QgsCoordinateReferenceSystem, QgsFields, QgsMessageLog

from ..compat import make_field, msg_level
from . import tables
from .layer_access import ConfigError
from .schema_pack import (CREATABLE_LAYERS, VERSION_TABLE_SUFFIX, archive_field_defs, defs_for,
                         normalize_container_path, qualified_table)

LOG_TAG = "NENA Addressing Tools"


class SchemaCreateError(ConfigError):
    """A schema create stopped part way. ``created`` holds the layers that did open."""

    def __init__(self, message, created=None):
        super().__init__(message)
        self.created = dict(created or {})


@dataclass
class SchemaTarget:
    kind: str                 # gpkg | gdb | conn
    path: str = ""
    provider: str = ""
    connection: str = ""
    schema: str = ""
    prefix: str = ""
    replace: bool = False
    driver: str = field(default="", repr=False)


def planned_tables(target: SchemaTarget, keys, include_archive: bool = False, archive_only: bool = False):
    """[(SchemaLayer, table name, is_archive)] in schema order for the chosen keys."""
    wanted = set(keys or [])
    if not wanted:
        raise ConfigError("Choose at least one layer.")
    if archive_only:
        include_archive = True
    plan = []
    for spec in CREATABLE_LAYERS:
        if spec.key not in wanted:
            continue
        try:
            if not archive_only:
                plan.append((spec, qualified_table(spec.table, target.prefix), False))
            if include_archive and spec.versioned:
                plan.append((spec, qualified_table(spec.table, target.prefix, VERSION_TABLE_SUFFIX), True))
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
    if not plan:
        raise ConfigError("Choose at least one layer.")
    names = [table.lower() for _spec, table, _archive in plan]
    if len(names) != len(set(names)):
        raise ConfigError("The table prefix is too long, so two tables would get the same name. "
                          "Use a shorter prefix.")
    return plan


def _prepare_target(target: SchemaTarget) -> None:
    if target.kind in ("gpkg", "gdb"):
        if not (target.path or "").strip():
            raise ConfigError("Choose a file for the schema.")
        target.path = normalize_container_path(target.kind, target.path)
        parent = os.path.dirname(target.path)
        if parent and not os.path.isdir(parent):
            raise ConfigError(f"The folder does not exist: {parent}")
        if target.kind == "gpkg" and os.path.isdir(target.path):
            raise ConfigError("The GeoPackage path is a folder. Choose a .gpkg file.")
        if target.kind == "gdb":
            if os.path.exists(target.path) and not os.path.isdir(target.path):
                raise ConfigError("The File Geodatabase path is a file. Choose a folder ending in .gdb.")
            target.driver = tables.file_gdb_driver()
            if not target.driver:
                raise ConfigError("This QGIS build cannot create a File Geodatabase "
                                  "(the OpenFileGDB writer is not available).")
        else:
            target.driver = "GPKG"
        return
    if target.kind != "conn":
        raise ConfigError("Choose a GeoPackage, a File Geodatabase, or a database connection.")
    if not target.provider or not target.connection:
        raise ConfigError("Choose a database connection.")


def _connection(target: SchemaTarget):
    return tables.provider_connection(target.provider, target.connection)


def list_existing(target: SchemaTarget, keys, include_archive: bool = False, archive_only: bool = False) -> list:
    """Table names that already exist for this target. Does not create a database schema."""
    _prepare_target(target)
    plan = planned_tables(target, keys, include_archive, archive_only)
    found = []
    if target.kind == "conn":
        conn = _connection(target)
        for _spec, table, _archive in plan:
            if tables.connection_has_table(conn, target.schema, table):
                found.append(table)
        return found
    for _spec, table, _archive in plan:
        if tables.ogr_has_table(target.path, table):
            found.append(table)
    return found


def _ensure_schema(conn, schema: str) -> None:
    if not schema:
        return
    try:
        have = [s.lower() for s in (conn.schemas() or [])]
    except Exception:  # noqa: BLE001
        have = []
    if schema.lower() in have:
        return
    try:
        conn.createSchema(schema)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "exist" in msg or "already" in msg:
            return
        raise ConfigError(f"Could not create schema '{schema}': {exc}") from exc


def _fields(defs) -> QgsFields:
    names = []
    out = QgsFields()
    for d in defs:
        key = d.physical_name.lower()
        if key in names:
            raise ConfigError(f"The field list has '{d.physical_name}' more than once.")
        names.append(key)
        out.append(make_field(d.physical_name, d.ftype, d.length))
    if not names:
        raise ConfigError("The field list is empty.")
    return out


def _write_one(target: SchemaTarget, table: str, defs, wkb_name: str, crs) -> None:
    fields = _fields(defs)
    wkb = {"None": None, "Point": _wkb("Point"), "LineString": _wkb("LineString")}[wkb_name]
    if target.kind in ("gpkg", "gdb"):
        tables.write_ogr_table(target.path, table, fields, wkb, crs, target.driver or "GPKG")
        return
    conn = _connection(target)
    if target.replace and tables.connection_has_table(conn, target.schema, table):
        try:
            tables.drop_connection_table(conn, target.schema, table)
        except Exception as exc:  # noqa: BLE001
            raise ConfigError(f"Could not replace {target.schema + '.' if target.schema else ''}{table}: {exc}") from exc
    tables.create_connection_table(target.provider, target.connection, target.schema, table, fields, wkb, crs)


def _wkb(name: str):
    from qgis.core import Qgis
    return {"Point": Qgis.WkbType.Point, "LineString": Qgis.WkbType.LineString}[name]


def _open_one(target: SchemaTarget, table: str, title: str):
    if target.kind in ("gpkg", "gdb"):
        lyr = tables.open_ogr_table(target.path, table, title)
    else:
        lyr = tables.open_connection_table(target.provider, target.connection, target.schema, table, title)
    lyr.setName(title)
    return lyr


def _require_crs(plan, crs):
    if any(spec.wkb != "None" for spec, _t, _archive in plan):
        if crs is None or not crs.isValid():
            raise ConfigError("Choose a coordinate reference system.")
    return crs or QgsCoordinateReferenceSystem()


def _layer_key(spec, is_archive: bool) -> str:
    return f"{spec.key}_archive" if is_archive else spec.key


def _layer_title(spec, is_archive: bool) -> str:
    return f"{spec.title} (versions)" if is_archive else spec.title


def create_schema(target: SchemaTarget, crs, keys, rcl_template=None, addr_template=None,
                  include_archive: bool = False, archive_only: bool = False) -> dict:
    """
    Create the chosen empty tables and return ``{key: layer}``.

    Version tables use the key ``centerline_archive`` and the other
    ``<role>_archive`` names. Every table is written before any layer is
    opened, so a GeoPackage or File Geodatabase is not locked halfway through.
    """
    _prepare_target(target)
    plan = planned_tables(target, keys, include_archive, archive_only)
    crs = _require_crs(plan, crs)
    if target.kind == "conn":
        _ensure_schema(_connection(target), target.schema)
    if target.kind == "conn":
        conn = _connection(target)
        existing = [table for _s, table, _a in plan if tables.connection_has_table(conn, target.schema, table)]
    else:
        existing = [table for _s, table, _a in plan if tables.ogr_has_table(target.path, table)]
    if existing and not target.replace:
        raise ConfigError("These tables already exist: " + ", ".join(existing)
                          + ". Turn on Replace tables that already exist to overwrite them.")

    written = []
    try:
        for spec, table, is_archive in plan:
            defs = defs_for(spec.key, rcl_template, addr_template)
            if is_archive:
                defs = archive_field_defs(defs)
            _write_one(target, table, defs, spec.wkb, crs)
            written.append((spec, table, is_archive))
    except ConfigError as exc:
        opened = _open_written(target, written)
        done = ", ".join(table for _s, table, _a in written) or "none"
        raise SchemaCreateError(f"{exc} Tables written before the error: {done}.", opened) from exc

    try:
        created = _open_written(target, written, raise_errors=True)
    except ConfigError as exc:
        raise SchemaCreateError(str(exc), {}) from exc
    where = target.path if target.kind in ("gpkg", "gdb") else (
        f"{target.connection}" + (f" / {target.schema}" if target.schema else ""))
    QgsMessageLog.logMessage(
        f"Created {', '.join(table for _s, table, _a in written)} in {where}.", LOG_TAG, msg_level("Info"))
    return created


def _open_written(target, written, raise_errors: bool = False) -> dict:
    opened = {}
    for spec, table, is_archive in written:
        try:
            opened[_layer_key(spec, is_archive)] = _open_one(target, table, _layer_title(spec, is_archive))
        except ConfigError:
            if raise_errors:
                raise
    return opened
