# -*- coding: utf-8 -*-
"""
NENA field templates.

A *field template* is the list of fields your road-centerline layer is
supposed to have: the NENA GIS Data Model template GeoPackage published for
your state, a state/regional profile with extra attributes, or a simple
JSON/CSV schema. Loading one lets the plugin

* show exactly those fields in the Field Mapping tab (with their M/C/O status),
* create missing fields with the template's own names, types and widths,
* auto-map by the template's names,
* check the template's mandatory fields during validation.

Template fields are matched to NENA logical names (FromAddr_L, St_Name …) by
name or known alias, or explicitly through a ``nena`` column. Fields that do
not match are kept as extra fields under their own name.

Supported sources
-----------------
* JSON  – ``{"name": "...", "fields": [{"name", "type", "length", "req", "description", "nena"}]}``
          (``type``: string | int | double | datetime;  ``req``: M | C | O)
* CSV   – header with at least ``name``; optional ``type,length,req,description,nena``
* any vector layer / file QGIS can open (GeoPackage, shapefile, GeoJSON,
  File Geodatabase layer in the project, PostGIS table …) – its fields are read.

The pure-Python part (JSON/CSV, merging) has no QGIS dependency.
"""

import csv
import io
import json
import os
from typing import Dict, List, Optional

from .schema import (CONDITIONAL, FIELD_TYPES, MANDATORY, OPTIONAL, RCL_FIELDS, T_DATETIME, T_DOUBLE, T_INT,
                     T_STRING, FieldDef, match_nena)

REQ_VALUES = (MANDATORY, CONDITIONAL, OPTIONAL)


class TemplateError(ValueError):
    pass


# ---------------------------------------------------------------------------
# normalisation
# ---------------------------------------------------------------------------
def _norm_type(value) -> str:
    v = str(value or "").strip().lower()
    if v in FIELD_TYPES:
        return v
    if any(k in v for k in ("date", "time")):
        return T_DATETIME
    if any(k in v for k in ("double", "real", "float", "numeric", "decimal")):
        return T_DOUBLE
    if "int" in v or v in ("long", "short", "smallint", "bigint", "integer64"):
        return T_INT
    return T_STRING


def _norm_req(value) -> str:
    v = str(value or "").strip().upper()
    if v in REQ_VALUES:
        return v
    if v.startswith("M") or v in ("REQUIRED", "Y", "YES", "TRUE"):
        return MANDATORY
    if v.startswith("C"):
        return CONDITIONAL
    return ""          # unknown -> inherit from the NENA definition (or O for extras)


def normalize_template(data: dict, source: str = "") -> dict:
    """Validate/clean a template dict into the stored form."""
    if not isinstance(data, dict) or not isinstance(data.get("fields"), list):
        raise TemplateError("A template needs a 'fields' list.")
    fields = []
    seen = set()
    for raw in data["fields"]:
        if isinstance(raw, str):
            raw = {"name": raw}
        if not isinstance(raw, dict) or not str(raw.get("name", "")).strip():
            continue
        name = str(raw["name"]).strip()
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        try:
            length = int(float(raw.get("length") or 0))
        except (TypeError, ValueError):
            length = 0
        fields.append({
            "name": name,
            "type": _norm_type(raw.get("type")),
            "length": max(length, 0),
            "req": _norm_req(raw.get("req")),
            "description": str(raw.get("description") or ""),
            "nena": str(raw.get("nena") or "").strip(),
        })
    if not fields:
        raise TemplateError("The template does not define any fields.")
    return {"name": str(data.get("name") or os.path.basename(source) or "Field template"),
            "source": source, "fields": fields}


def from_json_text(text: str, source: str = "") -> dict:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise TemplateError(f"Not valid JSON: {exc}") from exc
    if isinstance(data, list):
        data = {"fields": data}
    return normalize_template(data, source)


def from_csv_text(text: str, source: str = "") -> dict:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "name" not in [h.strip().lower() for h in reader.fieldnames]:
        raise TemplateError("CSV templates need a 'name' column (optional: type, length, req, description, nena).")
    rows = []
    for row in reader:
        rows.append({(k or "").strip().lower(): v for k, v in row.items()})
    return normalize_template({"fields": rows}, source)


def is_template_json(text: str) -> bool:
    """True for our JSON template format (as opposed to e.g. GeoJSON)."""
    try:
        data = json.loads(text)
    except ValueError:
        return False
    return isinstance(data, list) or (isinstance(data, dict) and isinstance(data.get("fields"), list))


def builtin_template() -> dict:
    return {
        "name": "NENA-STA-006.2 Road Centerline (built-in)",
        "source": "built-in",
        "fields": [{"name": f.name, "type": f.ftype, "length": f.length, "req": f.req,
                    "description": f.description, "nena": f.name} for f in RCL_FIELDS],
    }


# ---------------------------------------------------------------------------
# merging with the NENA schema
# ---------------------------------------------------------------------------
def effective_fields(template: Optional[dict], base_fields: Optional[List[FieldDef]] = None) -> List[FieldDef]:
    """
    Field definitions the plugin works with: template fields first (in
    template order, matched to NENA logical names where possible), followed
    by the remaining built-in NENA fields. Without a template: the built-in list.
    """
    all_base = list(base_fields) if base_fields is not None else list(RCL_FIELDS)
    if not template or not template.get("fields"):
        return all_base
    base = {f.name: f for f in all_base}
    out: List[FieldDef] = []
    used = set()
    for tf in template["fields"]:
        logical = tf.get("nena") if tf.get("nena") in base else match_nena(tf["name"], all_base)
        if logical and logical not in used:
            b = base[logical]
            out.append(FieldDef(
                name=logical,
                req=tf.get("req") or b.req,
                ftype=tf.get("type") or b.ftype,
                length=tf.get("length") or b.length,
                description=tf.get("description") or b.description,
                aliases=(tf["name"],) + tuple(a for a in b.aliases if a != tf["name"]),
                create_as=tf["name"],
            ))
            used.add(logical)
        elif not logical:
            out.append(FieldDef(name=tf["name"], req=tf.get("req") or OPTIONAL, ftype=tf.get("type") or T_STRING,
                                length=tf.get("length") or 0,
                                description=tf.get("description") or "Template field (not part of the NENA list)",
                                aliases=(), create_as=tf["name"]))
            used.add(tf["name"])
    out.extend(f for f in all_base if f.name not in used)
    return out


def summary(template: dict, base_fields: Optional[List[FieldDef]] = None) -> Dict[str, int]:
    fields = template.get("fields", [])
    matched = sum(1 for f in fields if (f.get("nena") or match_nena(f["name"], base_fields)))
    return {"fields": len(fields), "matched": matched, "extra": len(fields) - matched}


# ---------------------------------------------------------------------------
# QGIS-dependent loaders
# ---------------------------------------------------------------------------
def from_vector_layer(layer, source: str = "") -> dict:
    """Read the field list of any QgsVectorLayer (NENA template GeoPackage, shapefile, PostGIS …)."""
    rows = []
    pk = set(layer.primaryKeyAttributes()) if hasattr(layer, "primaryKeyAttributes") else set()
    for i, f in enumerate(layer.fields()):
        if i in pk or f.name().lower() in ("fid", "ogc_fid", "objectid", "shape_length", "shape_len"):
            continue
        rows.append({"name": f.name(), "type": _norm_type(f.typeName() or ""), "length": f.length(),
                     "description": f.comment() or f.alias() or ""})
    return normalize_template({"name": layer.name(), "fields": rows}, source or layer.source())


def vector_sublayers(path: str):
    """[(name, uri)] of the vector layers in a file (GeoPackage can hold several)."""
    try:
        from qgis.core import QgsProviderRegistry
        details = QgsProviderRegistry.instance().querySublayers(path)
        out = []
        for d in details:
            uri = d.uri()
            name = d.name() or os.path.basename(path)
            if d.providerKey() == "ogr":
                out.append((name, uri))
        return out
    except Exception:  # noqa: BLE001 - very old QGIS / unexpected provider: treat as a single layer
        return [(os.path.basename(path), path)]


def load_file(path: str, choose_sublayer=None) -> dict:
    """
    Load a template from a file. ``choose_sublayer(names) -> index`` is
    called when a file contains several vector layers (UI hook).
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in (".json", ".csv", ".txt"):
        with open(path, "r", encoding="utf-8-sig") as fh:
            text = fh.read()
        if ext == ".json" and is_template_json(text):
            return from_json_text(text, path)
        if ext in (".csv", ".txt"):
            return from_csv_text(text, path)
    from qgis.core import QgsVectorLayer
    subs = vector_sublayers(path)
    uri, name = path, os.path.basename(path)
    if len(subs) > 1:
        preferred = [i for i, (n, _u) in enumerate(subs)
                     if any(k in n.lower() for k in ("centerline", "centreline", "rcl", "road"))]
        idx = preferred[0] if len(preferred) == 1 else (choose_sublayer([n for n, _ in subs]) if choose_sublayer
                                                       else 0)
        if idx is None or idx < 0:
            raise TemplateError("No layer chosen.")
        name, uri = subs[idx]
    elif subs:
        name, uri = subs[0]
    lyr = QgsVectorLayer(uri, name, "ogr")
    if not lyr.isValid():
        raise TemplateError(f"QGIS cannot open '{path}' as a vector layer.")
    return from_vector_layer(lyr, path)


def save_json(path: str, template: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"name": template.get("name", "Field template"),
                   "fields": template.get("fields", [])}, fh, indent=2)

