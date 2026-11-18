# -*- coding: utf-8 -*-
"""Create a table in a GeoPackage, a File Geodatabase, or any QGIS database connection."""

import contextlib
import os

from qgis.core import (Qgis, QgsCoordinateReferenceSystem, QgsFields, QgsProject, QgsProviderRegistry,
                       QgsVectorFileWriter, QgsVectorLayer, QgsWkbTypes)

from .layer_access import ConfigError
from .safe_call import attempt

DB_PROVIDERS = ("postgres", "mssql", "oracle", "hana", "spatialite", "ogr")


def db_connections():
    """[(label, provider_key, connection_name)] of the database connections configured in QGIS."""
    out = []
    reg = QgsProviderRegistry.instance()
    for key in DB_PROVIDERS:
        md = reg.providerMetadata(key)
        if md is None:
            continue
        conns = attempt(md.connections)          # None: provider without a connection API
        if conns is None:
            continue
        for name in sorted(conns):
            out.append((f"{name}  [{key}]", key, name))
    return out


def file_gdb_driver() -> str:
    """'OpenFileGDB' when this QGIS can create a File Geodatabase, else ''."""
    try:
        details = QgsVectorFileWriter.supportedFiltersAndFormats()
    except Exception:  # noqa: BLE001
        return ""
    names = []
    for item in details:
        name = getattr(item, "driverName", None)
        if not name and isinstance(item, (tuple, list)) and len(item) > 1:
            name = item[1]
        if name:
            names.append(name)
    for prefer in ("OpenFileGDB", "FileGDB"):
        if prefer in names:
            return prefer
    return ""


def ogr_table_names(path: str):
    if not path or not os.path.exists(path):
        return []
    try:
        details = QgsProviderRegistry.instance().querySublayers(path)
    except Exception:  # noqa: BLE001
        return []
    return [d.name() for d in details if d.name()]


def ogr_has_table(path: str, table: str) -> bool:
    if not path or not os.path.exists(path):
        return False
    names = ogr_table_names(path)
    if names:
        return any(n.lower() == table.lower() for n in names)
    lyr = QgsVectorLayer(f"{path}|layername={table}", "", "ogr")
    ok = lyr.isValid()
    del lyr
    return ok


def _memory_layer(table: str, fields: QgsFields, wkb, crs) -> QgsVectorLayer:
    wkb = Qgis.WkbType.NoGeometry if wkb is None else wkb
    if wkb == Qgis.WkbType.NoGeometry:
        uri = "None"
    else:
        geom = QgsWkbTypes.displayString(wkb)
        auth = (crs or QgsCoordinateReferenceSystem("EPSG:4326")).authid() or "EPSG:4326"
        uri = f"{geom}?crs={auth}"
    mem = QgsVectorLayer(uri, table, "memory")
    if not mem.isValid():
        raise ConfigError(f"Could not prepare '{table}'.")
    mem.dataProvider().addAttributes(fields.toList())
    mem.updateFields()
    return mem


def write_ogr_table(path: str, table: str, fields: QgsFields, wkb=None, crs=None, driver: str = "GPKG") -> None:
    """
    Add one empty table to a GeoPackage or File Geodatabase.

    Callers that write several tables must finish every write before opening
    a layer. An open GeoPackage layer locks the file.
    """
    mem = _memory_layer(table, fields, wkb, crs)
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = driver
    opts.layerName = table
    opts.fileEncoding = "UTF-8"
    actions = getattr(QgsVectorFileWriter, "ActionOnExistingFile", QgsVectorFileWriter)
    opts.actionOnExistingFile = (actions.CreateOrOverwriteLayer if os.path.exists(path)
                                 else actions.CreateOrOverwriteFile)
    try:
        res = QgsVectorFileWriter.writeAsVectorFormatV3(mem, path, QgsProject.instance().transformContext(), opts)
    finally:
        del mem
    err = res[0] if isinstance(res, tuple) else res
    no_error = getattr(QgsVectorFileWriter, "WriterError", QgsVectorFileWriter).NoError
    if err != no_error:
        detail = res[1] if isinstance(res, tuple) and len(res) > 1 else err
        raise ConfigError(f"Could not write '{table}': {detail}")


def open_ogr_table(path: str, table: str, display_name: str = "") -> QgsVectorLayer:
    lyr = QgsVectorLayer(f"{path}|layername={table}", display_name or table, "ogr")
    if not lyr.isValid():
        # File Geodatabase lookups are case-insensitive; take the stored name.
        for name in ogr_table_names(path):
            if name.lower() == table.lower():
                lyr = QgsVectorLayer(f"{path}|layername={name}", display_name or table, "ogr")
                break
    if not lyr.isValid():
        raise ConfigError(f"The new table '{table}' could not be opened.")
    return lyr


def create_in_geopackage(path: str, table: str, fields: QgsFields, wkb=None, crs=None,
                         display_name: str = "") -> QgsVectorLayer:
    write_ogr_table(path, table, fields, wkb, crs, "GPKG")
    return open_ogr_table(path, table, display_name)


def create_in_filegdb(path: str, table: str, fields: QgsFields, wkb=None, crs=None,
                      display_name: str = "") -> QgsVectorLayer:
    driver = file_gdb_driver()
    if not driver:
        raise ConfigError("This QGIS build cannot create a File Geodatabase "
                          "(the OpenFileGDB writer is not available).")
    if not str(path).lower().endswith(".gdb"):
        raise ConfigError("A File Geodatabase path has to end in .gdb.")
    write_ogr_table(path, table, fields, wkb, crs, driver)
    return open_ogr_table(path, table, display_name)


def provider_connection(provider_key: str, connection: str):
    md = QgsProviderRegistry.instance().providerMetadata(provider_key)
    conn = md.connections().get(connection) if md else None
    if conn is None:
        raise ConfigError(f"Database connection '{connection}' not found.")
    return conn


def _match_table_name(conn, schema: str, table: str) -> str:
    try:
        props = conn.tables(schema) if schema else conn.tables()
    except TypeError:
        try:
            props = conn.tables()
        except Exception:  # noqa: BLE001
            return ""
    except Exception:  # noqa: BLE001
        return ""
    for prop in props:
        name = prop.tableName()
        sch = prop.schema() if hasattr(prop, "schema") else ""
        if name.lower() == table.lower() and (not schema or not sch or sch.lower() == schema.lower()):
            return name
    return ""


def connection_has_table(conn, schema: str, table: str) -> bool:
    with contextlib.suppress(Exception):
        if conn.tableExists(schema, table):
            return True
    return bool(_match_table_name(conn, schema, table))


def drop_connection_table(conn, schema: str, table: str) -> None:
    name = _match_table_name(conn, schema, table) or table
    conn.dropVectorTable(schema, name)


def create_connection_table(provider_key: str, connection: str, schema: str, table: str, fields: QgsFields,
                            wkb=None, crs=None) -> None:
    conn = provider_connection(provider_key, connection)
    try:
        conn.createVectorTable(schema, table, fields, Qgis.WkbType.NoGeometry if wkb is None else wkb,
                               crs or QgsCoordinateReferenceSystem(), False, {})
    except Exception as exc:  # noqa: BLE001 - QgsProviderConnectionException
        raise ConfigError(f"Could not create {schema + '.' if schema else ''}{table}: {exc}") from exc


def open_connection_table(provider_key: str, connection: str, schema: str, table: str,
                          display_name: str = "") -> QgsVectorLayer:
    conn = provider_connection(provider_key, connection)
    lyr = QgsVectorLayer(conn.tableUri(schema, table), display_name or table, provider_key)
    if lyr.isValid():
        return lyr
    match = _match_table_name(conn, schema, table)
    if match and match != table:
        lyr = QgsVectorLayer(conn.tableUri(schema, match), display_name or table, provider_key)
        if lyr.isValid():
            return lyr
    raise ConfigError(f"The new table '{table}' could not be opened.")


def create_in_connection(provider_key: str, connection: str, schema: str, table: str, fields: QgsFields,
                         wkb=None, crs=None, display_name: str = "") -> QgsVectorLayer:
    create_connection_table(provider_key, connection, schema, table, fields, wkb, crs)
    return open_connection_table(provider_key, connection, schema, table, display_name)
