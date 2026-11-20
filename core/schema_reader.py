"""Recognize and open an existing plugin schema without creating or changing tables."""
import os

from qgis.core import Qgis, QgsProject, QgsVectorLayer

from . import tables
from .layer_access import ConfigError
from .lookups import LOOKUP_SPECS
from .schema import MANDATORY, auto_map
from .schema_pack import (ARCHIVE_COLUMNS, CREATABLE_LAYERS, VERSION_TABLE_SUFFIX,
                          archive_field_defs, defs_for, qualified_table)
from .schema_writer import _open_one
from .template import _norm_type

_LINK_FIELDS = {
    "centerline": {"StreetID", "FromAddr_L", "ToAddr_L", "FromAddr_R", "ToAddr_R", "Parity_L", "Parity_R"},
    "streets": {"StreetID", "St_Name"},
    "address": {"Add_Number", "StreetID", "RCL_NGUID", "Access_X", "Access_Y", "Addr_Side"},
}


def _table_names(target):
    if target.kind in ("gpkg", "gdb"):
        if not target.path.strip():
            raise ConfigError("Choose an existing GeoPackage or File Geodatabase.")
        target.path = os.path.abspath(os.path.expanduser(target.path.strip().strip('"')))
        if target.kind == "gpkg" and (not target.path.lower().endswith(".gpkg") or not os.path.isfile(target.path)):
            raise ConfigError("Choose an existing .gpkg file.")
        if target.kind == "gdb" and (not target.path.lower().endswith(".gdb") or not os.path.isdir(target.path)):
            raise ConfigError("Choose an existing .gdb directory.")
        return tables.ogr_table_names(target.path)
    if target.kind != "conn" or not target.provider or not target.connection:
        raise ConfigError("Choose a GeoPackage, File Geodatabase, or saved QGIS database connection.")
    conn = tables.provider_connection(target.provider, target.connection)
    try:
        props = conn.tables(target.schema) if target.schema else conn.tables()
    except Exception as exc:
        raise ConfigError("Could not list tables in the selected connection/schema.") from exc
    return [prop.tableName() for prop in props
            if not target.schema or not prop.schema() or prop.schema().casefold() == target.schema.casefold()]


def _match(names, expected):
    matches = [name for name in names if name.casefold() == expected.casefold()]
    if len(matches) > 1:
        raise ConfigError(f"More than one table matches {expected}. Choose a database schema explicitly.")
    return matches[0] if matches else None


def _prefix(target, names):
    if target.prefix.strip():
        if not _match(names, qualified_table("RoadCenterline", target.prefix)):
            raise ConfigError("RoadCenterline was not found with this table prefix.")
        return
    candidates = [name for name in names if name.casefold() == "roadcenterline"
                  or name.casefold().endswith("_roadcenterline")]
    if len(candidates) != 1:
        raise ConfigError("No unique RoadCenterline table was found. Select the database schema and/or "
                          "enter the table prefix when several NENA schemas share a container.")
    target.prefix = "" if candidates[0].casefold() == "roadcenterline" else candidates[0][:-len("_RoadCenterline")]


def field_mapping(layer, key, rcl_template=None, addr_template=None):
    return _mapping(layer, defs_for(key, rcl_template, addr_template))


def _mapping(layer, definitions):
    names = [field.name() for field in layer.fields()]
    mapping = auto_map(names, definitions)
    physical = {name.casefold(): name for name in names}
    for definition in definitions:
        if definition.physical_name.casefold() in physical:
            mapping[definition.name] = physical[definition.physical_name.casefold()]
    return mapping


def _problems(layer, spec, definitions, archive):
    problems = []
    if spec.wkb == "None":
        if layer.isSpatial():
            problems.append("expected a non-spatial table")
    else:
        kind = {"Point": Qgis.GeometryType.Point, "LineString": Qgis.GeometryType.Line,
                "MultiPolygon": Qgis.GeometryType.Polygon}[spec.wkb]
        if layer.geometryType() != kind:
            problems.append(f"expected {spec.wkb} geometry")
        if not layer.crs().isValid():
            problems.append("missing or invalid coordinate reference system")
    mapping = _mapping(layer, definitions)
    required = {definition.name for definition in definitions if definition.req == MANDATORY}
    required.update(_LINK_FIELDS.get(spec.key, set()) & {definition.name for definition in definitions})
    if spec.key in {item[0] for item in LOOKUP_SPECS}:
        required.update({"Code", "Label", "SortOrder", "Active"})
        if spec.key == "domains":
            required.add("Domain")
    elif spec.key == "exceptions":
        required.update(definition.name for definition in definitions)
    if archive:
        required.update(name for name, *_ in ARCHIVE_COLUMNS)
    for definition in definitions:
        physical = mapping.get(definition.name)
        if not physical:
            if definition.name in required:
                problems.append("missing field " + definition.physical_name)
            continue
        field = layer.fields().field(physical)
        actual = _norm_type(field.typeName())
        if actual != definition.ftype and not (definition.ftype == "int" and actual == "double"):
            problems.append(f"{physical}: expected {definition.ftype}, found {field.typeName()}")
    return problems


def open_schema(target, rcl_template=None, addr_template=None):
    """Return role -> layer only after every recognized table has passed validation.

    RoadCenterline is required; the other live/support/version/zone tables are optional.
    Reuse already loaded layers to preserve their settings and pending edits.
    """
    names = _table_names(target)
    _prefix(target, names)
    opened, problems = {}, []
    for spec in CREATABLE_LAYERS:
        for archive in ((False, True) if spec.versioned else (False,)):
            table = _match(names, qualified_table(spec.table, target.prefix, VERSION_TABLE_SUFFIX if archive else ""))
            if table is None:
                continue
            layer = _open_one(target, table, spec.title + (" (versions)" if archive else ""))
            layer = next((existing for existing in QgsProject.instance().mapLayers().values()
                          if isinstance(existing, QgsVectorLayer) and existing.providerType() == layer.providerType()
                          and existing.source() == layer.source()), layer)
            if spec.key == "centerline" and not archive:
                from .config import load_from_layer
                stored = load_from_layer(layer)
                if stored is not None:
                    rcl_template = stored.template or None
            definitions = defs_for(spec.key, rcl_template, addr_template)
            if archive:
                definitions = archive_field_defs(definitions)
            errors = _problems(layer, spec, definitions, archive)
            if errors:
                problems.append(table + ": " + "; ".join(errors))
            else:
                opened[spec.key + ("_archive" if archive else "")] = layer
    if "centerline" not in opened and not problems:
        problems.append("RoadCenterline could not be opened.")
    if problems:
        raise ConfigError("The existing schema is not compatible:\n\n" + "\n".join(problems)
                          + "\n\nNo tables were created, replaced or changed.")
    return opened
