# -*- coding: utf-8 -*-
"""
The fresh NENA schema this plugin can create, and the field matching used to
import existing layers into it.

Plain Python only. The four layers the plugin already edits stay in
``SCHEMA_LAYERS``:

* road centerlines (NENA-STA-006.2, or the loaded centerline template)
* master street names (the plugin's street list)
* site / structure address points (built-in SSAP, or the state template)
* driveways (the plugin's driveway fields)

``CREATABLE_LAYERS`` adds the access-point table and the lookup tables
(road type, unit type, address status, road status, address type) and the
shared domain-values table (counties, communities, ZIP codes, ESNs, …).

Boundary polygons stay the layers you already draw the attribution rules
from. This plugin does not invent a boundary schema.
"""

import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

from .addressing.ssap import ACCESS_FIELDS, DRIVEWAY_FIELDS, SSAP_FIELDS
from .lookups import LOOKUP_SPECS, lookup_field_defs
from .schema import MANDATORY, OPTIONAL, RCL_FIELDS, FieldDef, auto_map, get_def, normalize_name
from .template import effective_fields

# Plugin link fields kept when a state template replaces the SSAP list.
ADDRESS_LINK_FIELDS = ("StreetID", "RCL_NGUID", "Access_X", "Access_Y", "Addr_Side")
# Full road name, the access-point link, unit/status codes, and the editor columns stay with a template.
_ADDRESS_ALWAYS = ADDRESS_LINK_FIELDS + (
    "FullName", "AccessPointID", "UnitType", "AddressStatus", "AddressType", "WhoEntered", "LastUpdatedBy")

MAX_IDENT = 63


@dataclass(frozen=True)
class SchemaLayer:
    key: str
    table: str
    title: str
    wkb: str          # None | Point | LineString
    role: str         # project-config role, or "centerline" for the panel layer
    versioned: bool = True


SCHEMA_LAYERS: tuple = (
    SchemaLayer("centerline", "RoadCenterline", "Road centerlines", "LineString", "centerline"),
    SchemaLayer("streets", "StreetNames", "Street names", "None", "master"),
    SchemaLayer("address", "SiteStructureAddressPoint", "Address points", "Point", "address"),
    SchemaLayer("driveway", "Driveway", "Driveways", "LineString", "driveway"),
)

# Access points and lookup lists are created with the schema. They are not part of
# SCHEMA_LAYERS, so the original four live layers keep their archive pairing.
ACCESS_LAYER = SchemaLayer("access", "AccessPoint", "Access points", "Point", "access")
LOOKUP_LAYERS: tuple = tuple(
    SchemaLayer(key, table, title, "None", role, False) for key, table, title, role in LOOKUP_SPECS)
EXTRA_LAYERS: tuple = (ACCESS_LAYER,) + LOOKUP_LAYERS
CREATABLE_LAYERS: tuple = SCHEMA_LAYERS + EXTRA_LAYERS

LAYERS_BY_KEY = {s.key: s for s in CREATABLE_LAYERS}

# Plugin columns on a version table. These are not NENA-STA-006 fields.
# ChangeType is insert, update, or delete. ArchiveID is the id of one version row.
VERSION_TABLE_SUFFIX = "Versions"
ARCHIVE_COLUMNS = (
    ("ArchiveID", "string", 38, "Id of this version row"),
    ("Version", "int", 0, "Version of this feature. 1 is the first saved version"),
    ("ChangeType", "string", 10, "insert, update, or delete"),
    ("ChangeDate", "datetime", 0, "When this version was saved"),
    ("ChangedBy", "string", 64, "User who saved this version"),
    ("FeatureKey", "string", 254, "NGUID or StreetID of this version"),
    ("SourceFid", "string", 38, "Feature id on the live layer when this version was saved"),
    ("ChangedFields", "string", 254, "Fields this update changed. Includes geometry when the shape changed"),
    ("OldValues", "string", 4000, "Previous values of the fields this update changed"),
    ("NewValues", "string", 4000, "New values of the fields this update changed"),
)


def sanitize_ident(name: str) -> str:
    """Identifier safe for GeoPackage, File Geodatabase, and SQL (letters, digits, underscore)."""
    text = re.sub(r"[^0-9A-Za-z_]", "_", (name or "").strip())
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        return ""
    if text[0].isdigit():
        text = "_" + text
    return text


def qualified_table(base: str, prefix: str = "", suffix: str = "") -> str:
    """Table name with an optional prefix and suffix, capped at 63 characters.

    A suffix such as ``Versions`` is kept intact so a long prefix cannot make
    the version table the same name as the live table.
    """
    base_s = sanitize_ident(base)
    if not base_s:
        raise ValueError("Table name is empty.")
    pre = sanitize_ident(prefix)
    suf = sanitize_ident(suffix)
    head = f"{pre}_{base_s}" if pre else base_s
    if not suf:
        return head[:MAX_IDENT]
    tail = "_" + suf
    if len(tail) >= MAX_IDENT:
        return suf[:MAX_IDENT]
    trimmed = head[:MAX_IDENT - len(tail)].rstrip("_")
    if not trimmed:
        return suf[:MAX_IDENT]
    return trimmed + tail


def normalize_container_path(kind: str, path: str) -> str:
    """Absolute path ending in .gpkg or .gdb."""
    text = os.path.abspath(os.path.expanduser((path or "").strip().strip('"')))
    if kind == "gpkg" and not text.lower().endswith(".gpkg"):
        text += ".gpkg"
    if kind == "gdb" and not text.lower().endswith(".gdb"):
        text += ".gdb"
    return text


def street_columns() -> List[tuple]:
    """(name, kind, length) of the master street-name table. Shared with the street repository."""
    from .streets.names import NAME_PARTS
    return [("StreetID", "string", 38)] + [(p, "string", 60 if p == "St_Name" else 35) for p in NAME_PARTS] + [
        ("FullName", "string", 254), ("Scope", "string", 100), ("Status", "string", 10),
        ("AliasOf", "string", 38), ("Effective", "datetime", 0), ("Expire", "datetime", 0),
        ("DateUpdate", "datetime", 0), ("WhoEntered", "string", 100), ("LastUpdatedBy", "string", 100),
        ("Notes", "string", 254)]


_STREET_EXTRAS = {
    "FullName": (("FULLNAME", "FULL_NAME", "FULL_STREET", "STREET_FULL"), "Full road name"),
    "Scope": (("SCOPE", "COMMUNITY"), "Name uniqueness scope"),
    "Status": (("STATUS",), "Active, Proposed, or Retired"),
    "AliasOf": (("ALIASOF", "ALIAS_OF"), "StreetID of the name this one aliases"),
    "Notes": (("NOTES", "COMMENT", "COMMENTS"), "Notes"),
}


def street_field_defs() -> List[FieldDef]:
    out = []
    for name, kind, length in street_columns():
        base = get_def(name)
        if base is not None:
            out.append(FieldDef(name, OPTIONAL, kind, length, base.description, base.aliases))
        elif name in _STREET_EXTRAS:
            aliases, desc = _STREET_EXTRAS[name]
            out.append(FieldDef(name, OPTIONAL, kind, length, desc, aliases))
        else:
            out.append(FieldDef(name, OPTIONAL, kind, length, name))
    return out


def centerline_defs(template: Optional[dict] = None) -> List[FieldDef]:
    """Template fields only, or the full built-in road-centerline schema."""
    if template and template.get("fields"):
        names = {f["name"].lower() for f in template["fields"]}
        return [d for d in effective_fields(template) if d.physical_name.lower() in names]
    return list(RCL_FIELDS)


def address_defs(template: Optional[dict] = None) -> List[FieldDef]:
    """Built-in SSAP schema, or the state template's fields plus the plugin link fields."""
    if not template or not template.get("fields"):
        return list(SSAP_FIELDS)
    names = {f["name"].lower() for f in template["fields"]}
    defs = [d for d in effective_fields(template, SSAP_FIELDS) if d.physical_name.lower() in names]
    have = {d.name for d in defs}
    defs += [d for d in SSAP_FIELDS if d.name in _ADDRESS_ALWAYS and d.name not in have]
    return defs


def driveway_defs() -> List[FieldDef]:
    return list(DRIVEWAY_FIELDS)


def access_defs() -> List[FieldDef]:
    return list(ACCESS_FIELDS)


def defs_for(key: str, rcl_template: Optional[dict] = None, addr_template: Optional[dict] = None) -> List[FieldDef]:
    if key == "centerline":
        return centerline_defs(rcl_template)
    if key == "streets":
        return street_field_defs()
    if key == "address":
        return address_defs(addr_template)
    if key == "driveway":
        return driveway_defs()
    if key == "access":
        return access_defs()
    if key in {spec[0] for spec in LOOKUP_SPECS}:
        return lookup_field_defs(key)
    raise KeyError(key)


def archive_field_defs(base: Sequence[FieldDef]) -> List[FieldDef]:
    """Live fields, then the version columns whose names are still free."""
    have = {d.physical_name.lower() for d in base}
    extra = []
    for name, kind, length, desc in ARCHIVE_COLUMNS:
        if name.lower() in have:
            continue
        extra.append(FieldDef(name, OPTIONAL, kind, length, desc))
    return list(base) + extra


def suggest_field_map(dest_names: Sequence[str], source_names: Sequence[str],
                      defs: Sequence[FieldDef]) -> Dict[str, str]:
    """
    Destination field -> source field.

    NENA names and known aliases win (L_F_ADD -> FromAddr_L). A destination
    field the schema does not know is matched when the source has the same name.
    Each source field is used once.
    """
    logical_to_source = auto_map(list(source_names), list(defs))
    by_norm = {}
    for d in defs:
        by_norm.setdefault(normalize_name(d.physical_name), d)
        by_norm.setdefault(normalize_name(d.name), d)
    source_by_norm = {}
    for src in source_names:
        source_by_norm.setdefault(normalize_name(src), src)
    out = {}
    used = set()
    for dest in dest_names:
        d = by_norm.get(normalize_name(dest))
        src = logical_to_source.get(d.name) if d else None
        if not src or src in used:
            src = source_by_norm.get(normalize_name(dest))
        if src and src not in used:
            out[dest] = src
            used.add(src)
    return out


def unmapped_required(dest_names: Iterable[str], field_map: Dict[str, str],
                      defs: Sequence[FieldDef]) -> List[str]:
    """Mandatory schema fields that are on the destination and have no source."""
    dest_norm = {normalize_name(n): n for n in dest_names}
    mapped = {normalize_name(k) for k, v in field_map.items() if v}
    missing = []
    for d in defs:
        if d.req != MANDATORY:
            continue
        present = dest_norm.get(normalize_name(d.physical_name)) or dest_norm.get(normalize_name(d.name))
        if present and normalize_name(present) not in mapped:
            missing.append(present)
    return missing


def convert_value(value, kind: str):
    """Coerce a plain Python value to a schema field kind. Empty becomes None."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    if kind == "string":
        text = str(value).strip()
        return text or None
    if kind == "int":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        return int(float(text))
    if kind == "double":
        if isinstance(value, bool):
            return float(int(value))
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        return float(text)
    if kind == "datetime":
        if hasattr(value, "isoformat"):
            try:
                return value.isoformat(sep=" ")
            except TypeError:
                return value.isoformat()
        text = str(value).strip()
        return text or None
    return value


def format_import_report(entries: Sequence[dict]) -> str:
    """One line per layer for the import summary."""
    lines = []
    for entry in entries:
        title = entry.get("title") or "Layer"
        reason = entry.get("skipped_reason")
        if reason:
            lines.append(f"{title}: {reason}")
            continue
        bits = [f"{int(entry.get('written') or 0)} feature(s)",
                f"{int(entry.get('mapped') or 0)} field(s) mapped"]
        nulls = int(entry.get("null_geometry") or 0)
        skipped = int(entry.get("skipped") or 0)
        multipart = int(entry.get("multipart") or 0)
        bad = int(entry.get("bad_values") or 0)
        if nulls:
            bits.append(f"{nulls} without geometry left out")
        blank = int(entry.get("blank") or 0)
        if blank:
            bits.append(f"{blank} empty street name(s) left out")
        if skipped:
            bits.append(f"{skipped} could not be reprojected")
        if multipart:
            bits.append(f"{multipart} multi-part feature(s) kept the first part")
        if bad:
            bits.append(f"{bad} value(s) left empty because they did not fit the field")
        line = f"{title}: " + ", ".join(bits) + "."
        missing = entry.get("missing") or []
        if missing:
            line += " Mandatory fields with no source: " + ", ".join(missing) + "."
        lines.append(line)
    return "\n".join(lines)
