# -*- coding: utf-8 -*-
"""
Drop-down lists for the address, centerline, and street-name fields.

Three kinds of list feed the drop-downs:

* **Lookup tables** (road type, unit type, address status, road status,
  address type). One table per list, as before.
* **Domain values** (one shared table, ``NENA_Domains``). Every local list
  lives here, one row per value, with a ``Domain`` column naming the list:
  counties, municipalities, communities, ZIP codes, postal communities, ESNs,
  MSAG communities, road classes, placement methods, discrepancy agencies, and
  street-name separators. A ``Parent`` column links a value to another list,
  so a postal community drop-down only offers the communities of the ZIP
  code already chosen. Adding a list means adding rows, not a table.
* **Fixed NENA lists** (directionals, parity, one-way, valid, country, state).
  These never change, so they become value maps on the field and need no table.

The rows are data. Creating the schema (or Create missing tables in the Lookup
tables window) seeds the defaults. Features store the code; the form shows the
label.
"""

import contextlib
import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .schema import OPTIONAL as OPT, T_INT as INT, T_STRING as S, FieldDef, auto_map, normalize_name
from .streets.street_types import OFFICIAL_TYPES, TYPE_ABBREVIATIONS

LOOKUP_FIELDS = [
    FieldDef("Code", OPT, S, 100, "Value stored on the feature"),
    FieldDef("Label", OPT, S, 100, "Name shown in the drop-down"),
    FieldDef("SortOrder", OPT, INT, 0, "Order in the drop-down"),
    FieldDef("Active", OPT, S, 1, "Y while the value can be chosen, N when it is retired"),
    FieldDef("Notes", OPT, S, 254, "Notes"),
    FieldDef("IsDefault", OPT, S, 1, "Y on the one value a new feature gets when the field is empty"),
]

DOMAIN_EXTRA_FIELDS = [
    FieldDef("Domain", OPT, S, 32, "Which list this value belongs to (County, PostCode, ESN …)"),
    FieldDef("Parent", OPT, S, 100, "Code in the parent list (the ZIP code of a postal community)"),
]

DOMAINS_KEY = "domains"

# Lists that may have a default value (Lookup tables, Default column). Lists
# that come from where the feature is (ESN, ZIP, MSAG community, municipality,
# county …) are left out on purpose: a default there would hide a missing
# boundary polygon and give the address the wrong value.
DEFAULTABLE_TABLES = ("address_status", "address_type", "road_status")
DEFAULTABLE_DOMAINS = ("RoadClass", "Placement", "DiscrpAgID")

# Default rows ticked in new tables, and in older tables the first time they get the Default column.
SEED_DEFAULTS = {"address_status": "ACT", "road_status": "ACT", "RoadClass": "Local", "Placement": "Structure"}

# key, table, title, project-config role
TABLE_SPECS = (
    ("road_type", "RoadType", "Road types", "road_type"),
    ("unit_type", "UnitType", "Unit types", "unit_type"),
    ("address_status", "AddressStatus", "Address statuses", "address_status"),
    ("road_status", "RoadStatus", "Road statuses", "road_status"),
    ("address_type", "AddressType", "Address types", "address_type"),
)
LOOKUP_SPECS = TABLE_SPECS + ((DOMAINS_KEY, "NENA_Domains", "Domain values", DOMAINS_KEY),)
LOOKUP_KEYS = tuple(spec[0] for spec in LOOKUP_SPECS)
SPEC_BY_KEY = {spec[0]: spec for spec in LOOKUP_SPECS}

# name, title, parent domain
DOMAINS = (
    ("County", "Counties", ""),
    ("IncMuni", "Incorporated municipalities", ""),
    ("UnincCom", "Unincorporated communities", ""),
    ("NbrhdCom", "Neighborhoods", ""),
    ("PostCode", "ZIP codes", ""),
    ("PostComm", "Postal communities", "PostCode"),
    ("ESN", "ESNs", ""),
    ("MSAGComm", "MSAG communities", ""),
    ("RoadClass", "Road classes", ""),
    ("Placement", "Placement methods", ""),
    ("DiscrpAgID", "Discrepancy agencies", ""),
    ("PreSep", "Street name separators", ""),
)
DOMAIN_NAMES = tuple(d[0] for d in DOMAINS)
DOMAIN_TITLE = {d[0]: d[1] for d in DOMAINS}
DOMAIN_PARENT = {d[0]: d[2] for d in DOMAINS}

_DOMAIN_SEEDS = {
    # NENA road class registry
    "RoadClass": ("Primary", "Secondary", "Local", "Ramp", "Service Drive", "Vehicular Trail", "Walkway",
                  "Stairway", "Alley", "Private", "Parking Lot", "Trail", "Bridle Path", "Other"),
    # NENA placement method registry
    "Placement": ("Structure", "Site", "Property Access", "Parcel", "Geocoding", "Unknown"),
    "PreSep": ("of", "of the", "at", "de", "del", "de la", "de las", "de los", "des"),
}

# ---------------------------------------------------------------------------
# Fixed NENA lists (value maps, no table)
# ---------------------------------------------------------------------------
DIRECTIONALS = tuple((d, d) for d in (
    "North", "South", "East", "West", "Northeast", "Northwest", "Southeast", "Southwest"))
PARITY = (("Odd (O)", "O"), ("Even (E)", "E"), ("Both (B)", "B"), ("Zero / none (Z)", "Z"))
ONE_WAY = (("Both directions (B)", "B"), ("From → To (FT)", "FT"), ("To → From (TF)", "TF"))
YES_NO = (("Yes (Y)", "Y"), ("No (N)", "N"))
COUNTRIES = (("United States (US)", "US"), ("Canada (CA)", "CA"), ("Mexico (MX)", "MX"))
_STATES = (
    ("AL", "Alabama"), ("AK", "Alaska"), ("AZ", "Arizona"), ("AR", "Arkansas"), ("CA", "California"),
    ("CO", "Colorado"), ("CT", "Connecticut"), ("DE", "Delaware"), ("DC", "District of Columbia"),
    ("FL", "Florida"), ("GA", "Georgia"), ("HI", "Hawaii"), ("ID", "Idaho"), ("IL", "Illinois"),
    ("IN", "Indiana"), ("IA", "Iowa"), ("KS", "Kansas"), ("KY", "Kentucky"), ("LA", "Louisiana"),
    ("ME", "Maine"), ("MD", "Maryland"), ("MA", "Massachusetts"), ("MI", "Michigan"), ("MN", "Minnesota"),
    ("MS", "Mississippi"), ("MO", "Missouri"), ("MT", "Montana"), ("NE", "Nebraska"), ("NV", "Nevada"),
    ("NH", "New Hampshire"), ("NJ", "New Jersey"), ("NM", "New Mexico"), ("NY", "New York"),
    ("NC", "North Carolina"), ("ND", "North Dakota"), ("OH", "Ohio"), ("OK", "Oklahoma"), ("OR", "Oregon"),
    ("PA", "Pennsylvania"), ("RI", "Rhode Island"), ("SC", "South Carolina"), ("SD", "South Dakota"),
    ("TN", "Tennessee"), ("TX", "Texas"), ("UT", "Utah"), ("VT", "Vermont"), ("VA", "Virginia"),
    ("WA", "Washington"), ("WV", "West Virginia"), ("WI", "Wisconsin"), ("WY", "Wyoming"),
    ("AS", "American Samoa"), ("GU", "Guam"), ("MP", "Northern Mariana Islands"), ("PR", "Puerto Rico"),
    ("VI", "U.S. Virgin Islands"),
)
STATES = tuple((f"{name} ({code})", code) for code, name in _STATES)

FIXED_LISTS = {
    "directional": ("Directionals", DIRECTIONALS),
    "parity": ("Parity", PARITY),
    "one_way": ("One-way", ONE_WAY),
    "yes_no": ("Yes / no", YES_NO),
    "country": ("Countries", COUNTRIES),
    "state": ("States", STATES),
}

# QGIS stores "no value" in a value map under this key.
VALUE_MAP_NULL = "{2839923C-8B7D-419E-B84B-CA2FE9B80EC7}"


# ---------------------------------------------------------------------------
# Which field gets which list
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Binding:
    role: str             # address | centerline | master
    field: str            # NENA field name
    kind: str             # table | domain | map
    source: str           # lookup key, domain name, or fixed-list key
    key_column: str = "Code"
    parent_field: str = ""   # NENA field on the same layer that filters this list


def _sides(role, base, kind, source, key_column="Code", parent=""):
    out = []
    for side in ("_L", "_R"):
        out.append(Binding(role, base + side, kind, source, key_column, parent + side if parent else ""))
    return tuple(out)


_NAME_BINDINGS = (
    ("St_PreTyp", "table", "road_type", "Label"),
    ("St_PosTyp", "table", "road_type", "Label"),
    ("St_PreDir", "map", "directional", "Code"),
    ("St_PosDir", "map", "directional", "Code"),
    ("St_PreSep", "domain", "PreSep", "Code"),
)

BINDINGS: Tuple[Binding, ...] = (
    # address points (NENA SSAP names)
    Binding("address", "UnitType", "table", "unit_type"),
    Binding("address", "AddressStatus", "table", "address_status"),
    Binding("address", "AddressType", "table", "address_type"),
    *(Binding("address", f, k, s, c) for f, k, s, c in _NAME_BINDINGS),
    Binding("address", "Country", "map", "country"),
    Binding("address", "State", "map", "state"),
    Binding("address", "County", "domain", "County"),
    Binding("address", "Inc_Muni", "domain", "IncMuni"),
    Binding("address", "Uninc_Comm", "domain", "UnincCom"),
    Binding("address", "Nbrhd_Comm", "domain", "NbrhdCom"),
    Binding("address", "Post_Code", "domain", "PostCode"),
    Binding("address", "Post_Comm", "domain", "PostComm", parent_field="Post_Code"),
    Binding("address", "ESN", "domain", "ESN"),
    Binding("address", "MSAGComm", "domain", "MSAGComm"),
    Binding("address", "Placement", "domain", "Placement"),
    Binding("address", "DiscrpAgID", "domain", "DiscrpAgID"),
    # road centerlines (NENA RCL names; both sides)
    Binding("centerline", "RoadStatus", "table", "road_status"),
    *(Binding("centerline", f, k, s, c) for f, k, s, c in _NAME_BINDINGS),
    *_sides("centerline", "Parity", "map", "parity"),
    Binding("centerline", "OneWay", "map", "one_way"),
    *_sides("centerline", "Valid", "map", "yes_no"),
    *_sides("centerline", "Country", "map", "country"),
    *_sides("centerline", "State", "map", "state"),
    *_sides("centerline", "County", "domain", "County"),
    *_sides("centerline", "IncMuni", "domain", "IncMuni"),
    *_sides("centerline", "UnincCom", "domain", "UnincCom"),
    *_sides("centerline", "NbrhdCom", "domain", "NbrhdCom"),
    *_sides("centerline", "PostCode", "domain", "PostCode"),
    *_sides("centerline", "PostComm", "domain", "PostComm", parent="PostCode"),
    *_sides("centerline", "ESN", "domain", "ESN"),
    *_sides("centerline", "MSAGComm", "domain", "MSAGComm"),
    Binding("centerline", "RoadClass", "domain", "RoadClass"),
    Binding("centerline", "DiscrpAgID", "domain", "DiscrpAgID"),
    # master street names
    *(Binding("master", f, k, s, c) for f, k, s, c in _NAME_BINDINGS),
)


def bindings_for(role: str = "", kind: str = "", source: str = "") -> List[Binding]:
    return [b for b in BINDINGS
            if (not role or b.role == role) and (not kind or b.kind == kind) and (not source or b.source == source)]


def lookup_field_defs(key: str = "") -> List[FieldDef]:
    if key == DOMAINS_KEY:
        return list(LOOKUP_FIELDS) + list(DOMAIN_EXTRA_FIELDS)
    return list(LOOKUP_FIELDS)


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------
_PREFERRED_ROAD_CODE = (
    "ST", "RD", "DR", "AVE", "LN", "CT", "BLVD", "PKWY", "CIR", "PL", "TER", "TRL",
    "WY", "GLN", "CO RD", "ST HWY", "US HWY", "SR", "TWP RD", "FM",
)

_UNIT_TYPES = (
    ("APT", "Apt"), ("UNIT", "Unit"), ("STE", "Suite"), ("RM", "Room"), ("FL", "Floor"),
    ("BLDG", "Bldg"), ("BUILDING", "Building"), ("SPC", "Space"), ("LOT", "Lot"),
    ("DEPT", "Dept"), ("OFC", "Office"), ("KEY", "Key"), ("SLIP", "Slip"), ("PIER", "Pier"),
    ("HANGAR", "Hangar"),
)

_ADDRESS_STATUSES = (
    ("ACT", "Active"), ("PROP", "Proposed"), ("RET", "Retired"), ("TEMP", "Temporary"),
    ("UC", "Under Construction"), ("PEND", "Pending"),
)

_ROAD_STATUSES = (
    ("ACT", "Active"), ("PROP", "Proposed"), ("RET", "Retired"), ("PRIV", "Private"),
    ("SEAS", "Seasonal"), ("MAINT", "Under Maintenance"),
)

_ADDRESS_TYPES = (
    ("BLDG", "Building"), ("RES", "Residence"), ("BUS", "Business"), ("UTIL", "Utility"),
    ("POLE", "Pole"), ("METER", "Meter"), ("HYD", "Hydrant"), ("SITE", "Site"),
    ("PARCEL", "Parcel"), ("OTHER", "Other"),
)

_PLAIN = {
    "unit_type": _UNIT_TYPES,
    "address_status": _ADDRESS_STATUSES,
    "road_status": _ROAD_STATUSES,
    "address_type": _ADDRESS_TYPES,
}


def _road_code(label: str) -> str:
    spelled = label.upper()
    hits = [abbr for abbr, target in TYPE_ABBREVIATIONS.items() if target == spelled]
    for preferred in _PREFERRED_ROAD_CODE:
        if preferred in hits:
            return preferred
    if hits:
        hits.sort(key=lambda abbr: (len(abbr), abbr))
        return hits[0]
    return label


def _rows(pairs: Sequence[Tuple[str, str]], domain: str = "") -> List[dict]:
    rows = []
    seen = set()
    order = 10
    for code, label in pairs:
        key = str(code).strip()
        text = str(label).strip()
        if not key or not text or key.upper() in seen:
            continue
        seen.add(key.upper())
        row = {"Code": key, "Label": text, "SortOrder": order, "Active": "Y", "Notes": "", "IsDefault": ""}
        if domain:
            row["Domain"] = domain
            row["Parent"] = ""
        rows.append(row)
        order += 10
    return rows


def road_type_seeds() -> List[dict]:
    """One row per official street type. Code is the usual abbreviation when there is one."""
    pairs = []
    used = set()
    for label in OFFICIAL_TYPES:
        code = _road_code(label)
        if code.upper() in used:
            code = label
        if code.upper() in used:
            continue
        used.add(code.upper())
        pairs.append((code, label))
    return _rows(pairs)


def domain_seeds() -> List[dict]:
    """Starting rows of the shared domain table. Local lists start empty."""
    rows = []
    for name, values in _DOMAIN_SEEDS.items():
        rows.extend(_rows([(v, v) for v in values], name))
    return rows


def seeds_for(key: str) -> List[dict]:
    if key == "road_type":
        rows = road_type_seeds()
    elif key == DOMAINS_KEY:
        rows = domain_seeds()
    else:
        pairs = _PLAIN.get(key)
        if pairs is None:
            raise KeyError(key)
        rows = _rows(pairs)
    for row in rows:
        list_name = row.get("Domain") or key
        if can_default(list_name) and SEED_DEFAULTS.get(list_name, "").upper() == str(row["Code"]).upper():
            row["IsDefault"] = "Y"
    return rows


def builtin_unit_types() -> List[Tuple[str, str]]:
    """(code, label) used when the project has no Unit Type table yet."""
    return [(row["Code"], row["Label"]) for row in seeds_for("unit_type")]


def _order(row) -> int:
    try:
        return int(row.get("SortOrder") or 0)
    except (TypeError, ValueError):
        return 0


def active_choices(rows: Sequence[dict]) -> List[Tuple[str, str]]:
    """Active (code, label) pairs in sort order."""
    kept = []
    for row in rows:
        if str(row.get("Active") or "Y").strip().upper() == "N":
            continue
        code = str(row.get("Code") or "").strip()
        label = str(row.get("Label") or "").strip() or code
        if code:
            kept.append((_order(row), code, label))
    kept.sort(key=lambda item: (item[0], item[2].upper(), item[1]))
    return [(code, label) for _order_, code, label in kept]


# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------
def can_default(list_name: str) -> bool:
    """True for a lookup key or domain name whose list may have a default (status, type, road class …)."""
    return list_name in DEFAULTABLE_TABLES or list_name in DEFAULTABLE_DOMAINS


def is_default(row: dict) -> bool:
    return str(row.get("IsDefault") or "").strip().upper() in ("Y", "YES", "TRUE", "T", "1")


def is_active(row: dict) -> bool:
    return str(row.get("Active") or "Y").strip().upper() != "N"


def default_code(rows: Sequence[dict]) -> str:
    """Code of the default value: the ticked active row (the first in sort order if several are ticked)."""
    ticked = [row for row in rows if is_default(row) and is_active(row) and str(row.get("Code") or "").strip()]
    if not ticked:
        return ""
    ticked.sort(key=lambda row: (_order(row), str(row.get("Label") or "").upper()))
    return str(ticked[0]["Code"]).strip()


def has_default_column(layer) -> bool:
    return layer is not None and layer.fields().indexFromName("IsDefault") >= 0


def list_defaults(pcfg) -> Dict[str, str]:
    """
    ``{list name: default code}`` for every list that may have a default and
    whose table has the Default column. ``""`` means the list has no default
    (nothing ticked). Lists without a table or column are left out, so the
    older settings still apply to them.
    """
    out = {}
    for key in DEFAULTABLE_TABLES:
        layer = lookup_layer(pcfg, key)
        if has_default_column(layer):
            out[key] = default_code(read_rows(layer))
    domains = lookup_layer(pcfg, DOMAINS_KEY)
    if has_default_column(domains):
        rows = read_rows(domains)
        for name in DEFAULTABLE_DOMAINS:
            out[name] = default_code(rows_in_domain(rows, name))
    return out


def field_defaults(pcfg, role: str, governed: bool = False) -> Dict[str, str]:
    """
    ``{NENA field: default code}`` for the fields of ``role`` (address,
    centerline) whose list has a default. With ``governed`` the fields whose
    list has the Default column but nothing ticked are included with ``""``.
    """
    defaults = list_defaults(pcfg)
    out = {}
    for binding in BINDINGS:
        if binding.role != role or binding.kind not in ("table", "domain") or binding.key_column != "Code":
            continue
        if binding.source not in defaults:
            continue
        code = defaults[binding.source]
        if code or governed:
            out[binding.field] = code
    return out


def default_choice(pcfg, key: str, fallback: str = "") -> str:
    """The default code of one list (lookup key or domain name), else ``fallback``."""
    return list_defaults(pcfg).get(key) or fallback


def default_placement(pcfg) -> str:
    """Placement for new addresses: the Placement default in the lookup tables, else Settings, Addresses."""
    return default_choice(pcfg, "Placement", getattr(pcfg.addresses, "placement", "") or "Structure")


def choices_for(pcfg, key: str) -> List[Tuple[str, str]]:
    """Active (code, label) pairs of a lookup table or domain list, for a drop-down in a plugin window."""
    if key in SPEC_BY_KEY:
        rows = read_rows(lookup_layer(pcfg, key))
    else:
        rows = read_rows(lookup_layer(pcfg, DOMAINS_KEY), key)
    return active_choices(rows)


def migration_defaults(pcfg, centerline_cfg=None) -> Dict[str, str]:
    """
    Defaults to tick when a table first gets the Default column: what the
    older settings used (road status and class from Settings, Defaults;
    placement from Settings, Addresses), else the standard ones.
    """
    out = dict(SEED_DEFAULTS)
    old = dict(getattr(centerline_cfg, "defaults", None) or {})
    if old.get("RoadStatus"):
        out["road_status"] = str(old["RoadStatus"]).strip()
    if old.get("RoadClass"):
        out["RoadClass"] = str(old["RoadClass"]).strip()
    placement = getattr(getattr(pcfg, "addresses", None), "placement", "")
    if placement:
        out["Placement"] = placement
    return out


def defaults_to_tick(rows: Sequence[dict], wanted: Dict[str, str], key: str) -> List:
    """Feature ids to mark Y when a table gets the Default column (codes compared without case)."""
    out = []
    names = DEFAULTABLE_DOMAINS if key == DOMAINS_KEY else ((key,) if key in DEFAULTABLE_TABLES else ())
    for name in names:
        code = str(wanted.get(name) or "").strip().upper()
        if not code:
            continue
        pool = rows_in_domain(rows, name) if key == DOMAINS_KEY else rows
        for row in sorted(pool, key=_order):
            if str(row.get("Code") or "").strip().upper() == code and is_active(row):
                out.append(row["_fid"])
                break
    return out


def ensure_default_column(layer, key: str, wanted: Optional[Dict[str, str]] = None) -> bool:
    """
    Add the Default column (IsDefault) to an older lookup table and tick the
    defaults the project used so far. Returns True when the column was added.
    """
    if layer is None or has_default_column(layer):
        return False
    if key != DOMAINS_KEY and key not in DEFAULTABLE_TABLES:
        return False
    from .layer_access import ConfigError, ensure_editable
    try:
        from ..compat import make_field
        field = make_field("IsDefault", "string", 1)
    except Exception:  # noqa: BLE001 - test double without Qgis version info
        from qgis.core import QgsField
        field = QgsField("IsDefault", "string")
    if layer.isEditable():
        if not layer.addAttribute(field):
            return False
    elif not layer.dataProvider().addAttributes([field]):
        raise ConfigError(f"Could not add the Default column to {layer.name()}.")
    layer.updateFields()
    fids = defaults_to_tick(read_rows(layer), wanted or SEED_DEFAULTS, key)
    if fids:
        started = ensure_editable(layer)
        index = layer.fields().indexFromName("IsDefault")
        layer.beginEditCommand("NENA: tick lookup defaults")
        for fid in fids:
            layer.changeAttributeValue(fid, index, "Y")
        layer.endEditCommand()
        save_edits(layer, started)
    return True


def set_default(layer, fid, domain: str = "") -> int:
    """
    Make one row the default of its list (``fid`` None clears the list's default).
    Only one row per list keeps Y. Changes go into the layer's edit buffer;
    the caller opens and saves the edit session. Returns rows changed.
    """
    index = layer.fields().indexFromName("IsDefault")
    if index < 0:
        return 0
    changed = 0
    for row in read_rows(layer, domain):
        if fid is not None and row["_fid"] == fid:
            if str(row.get("IsDefault") or "") != "Y":
                layer.changeAttributeValue(row["_fid"], index, "Y")
                changed += 1
        elif str(row.get("IsDefault") or "").strip():
            layer.changeAttributeValue(row["_fid"], index, None)
            changed += 1
    return changed


def _default_expression(code: str) -> str:
    return _quote(code)


_LAYER_DEFAULTS_PROP = "nena/lookup_defaults"


def apply_layer_defaults(pcfg, centerline=None) -> int:
    """
    Put each list's default on the fields that use it, as the field's QGIS
    default value, so every new feature gets it: the plugin's tools, QGIS's
    own Add Feature tool, pasting, and imports. A default someone set on a
    field by hand is left alone. Returns the number of fields changed.
    """
    import json
    try:
        from qgis.core import QgsDefaultValue
    except ImportError:  # pragma: no cover
        return 0
    layers = feature_layers(pcfg, centerline)
    maps = field_maps(pcfg, layers)
    changed = 0
    for role in ("address", "centerline"):
        layer = layers.get(role)
        if layer is None or not hasattr(layer, "setDefaultValueDefinition"):
            continue
        try:
            ours = json.loads(layer.customProperty(_LAYER_DEFAULTS_PROP, "") or "{}")
        except (TypeError, ValueError):
            ours = {}
        for field, code in field_defaults(pcfg, role, governed=True).items():
            physical = maps.get(role, {}).get(field)
            index = layer.fields().indexFromName(physical) if physical else -1
            if index < 0:
                continue
            current = layer.defaultValueDefinition(index).expression() or ""
            if current and current != ours.get(physical):
                continue                       # set by hand: keep it
            wanted = _default_expression(code) if code else ""
            if wanted == current:
                continue
            layer.setDefaultValueDefinition(index, QgsDefaultValue(wanted, False))
            if wanted:
                ours[physical] = wanted
            else:
                ours.pop(physical, None)
            changed += 1
        layer.setCustomProperty(_LAYER_DEFAULTS_PROP, json.dumps(ours))
    return changed


def sync_defaults(pcfg, centerline=None) -> int:
    """Give older lookup tables the Default column, then put the defaults on the fields."""
    from .layer_access import ConfigError
    cfg = None
    if centerline is not None:
        with contextlib.suppress(Exception):
            from .config import load_from_layer
            cfg = load_from_layer(centerline)
    wanted = migration_defaults(pcfg, cfg)
    for key in DEFAULTABLE_TABLES + (DOMAINS_KEY,):
        with contextlib.suppress(ConfigError):
            ensure_default_column(lookup_layer(pcfg, key), key, wanted)
    return apply_layer_defaults(pcfg, centerline)


def fill_empty_from_lookups(pcfg, role: str, layer, fids=None) -> int:
    """Write each list's default into the empty fields of existing features. One undo step."""
    from qgis.core import QgsFeatureRequest
    from .layer_access import ensure_editable
    if layer is None:
        return 0
    maps = field_maps(pcfg, {role: layer})
    targets = {}
    for field, code in field_defaults(pcfg, role).items():
        physical = maps.get(role, {}).get(field)
        index = layer.fields().indexFromName(physical) if physical else -1
        if index >= 0 and code:
            targets[index] = code
    if not targets:
        return 0
    request = QgsFeatureRequest() if not fids else QgsFeatureRequest().setFilterFids(list(fids))
    changes = {}
    for feature in layer.getFeatures(request):
        vals = {i: code for i, code in targets.items() if not _text(feature.attribute(i))}
        if vals:
            changes[feature.id()] = vals
    if not changes:
        return 0
    provider = layer.dataProvider() if hasattr(layer, "dataProvider") else None
    if not layer.isEditable() and provider is not None and hasattr(provider, "changeAttributeValues"):
        # Not being edited: the change would be saved at once anyway, so write it
        # in one batch. Going through the edit buffer took minutes on 37,000 points.
        if provider.changeAttributeValues(changes):
            layer.reload()
            return len(changes)
    started = ensure_editable(layer)
    layer.beginEditCommand("NENA: fill empty fields from lookup defaults")
    for fid, vals in changes.items():
        layer.changeAttributeValues(fid, vals)
    layer.endEditCommand()
    if started:
        save_edits(layer, started)
    return len(changes)


def rows_in_domain(rows: Iterable[dict], domain: str) -> List[dict]:
    want = domain.upper()
    return [row for row in rows if str(row.get("Domain") or "").strip().upper() == want]


def new_domain_rows(domain: str, values: Iterable, existing: Iterable[dict]) -> List[dict]:
    """
    Rows to add so ``values`` are all in ``domain``.

    ``values`` holds plain values or (value, parent) pairs. Values already in
    the domain (any case) and blanks are left out. New rows go after the last
    sort order.
    """
    have = {str(row.get("Code") or "").strip().upper() for row in rows_in_domain(existing, domain)}
    order = max([_order(row) for row in rows_in_domain(existing, domain)] or [0])
    out = []
    for item in values:
        value, parent = (item if isinstance(item, (tuple, list)) else (item, ""))
        text = "" if value is None else str(value).strip()
        if not text or text.upper() in have:
            continue
        have.add(text.upper())
        order += 10
        out.append({"Domain": domain, "Code": text, "Label": text, "SortOrder": order, "Active": "Y",
                    "Parent": "" if parent is None else str(parent).strip(), "Notes": "", "IsDefault": ""})
    return out


# ---------------------------------------------------------------------------
# Drop-down widget configuration (plain dicts, testable without QGIS)
# ---------------------------------------------------------------------------
def value_map_config(pairs: Sequence[Tuple[str, str]], allow_null: bool = True) -> dict:
    entries = []
    if allow_null:
        entries.append({"": VALUE_MAP_NULL})
    entries.extend({label: value} for label, value in pairs)
    return {"map": entries}


def _quote(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def value_relation_filter(domain: str = "", parent_physical: str = "") -> str:
    """Filter expression: active rows of one list, narrowed by the parent field when it has a value."""
    parts = ["coalesce(\"Active\", 'Y') <> 'N'"]
    if domain:
        parts.append(f"\"Domain\" = {_quote(domain)}")
    if parent_physical:
        current = f"current_value({_quote(parent_physical)})"
        parts.append(f"({current} IS NULL OR {current} = '' OR \"Parent\" IS NULL OR \"Parent\" = '' "
                     f"OR \"Parent\" = {current})")
    return " AND ".join(parts)


def resolve_fields(layer_field_names: Sequence[str], defs: Sequence[FieldDef],
                   explicit: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    NENA name -> field on the layer.

    The plugin's own field mapping wins, then the exact NENA name, then the known
    aliases (PRETYPE -> St_PreTyp). Names are matched without regard to case.
    """
    by_norm = {normalize_name(name): name for name in layer_field_names}
    out = {}
    for logical, physical in (explicit or {}).items():
        found = by_norm.get(normalize_name(physical or ""))
        if found:
            out[logical] = found
    taken = set(out.values())
    for logical, physical in auto_map(list(layer_field_names), list(defs)).items():
        if logical not in out and physical not in taken:
            out[logical] = physical
            taken.add(physical)
    return out


def match_lookup_table(table: str, candidates: Sequence[Tuple[str, str]]) -> int:
    """
    Index of the candidate that is the lookup table ``table``, or -1.

    Each candidate is (layer name, table name in its data source). A table with a
    prefix (``County_RoadType``) or a schema (``gis.RoadType``) still matches.
    """
    want = table.lower()

    def score(name: str, source: str) -> int:
        src = (source or "").lower().split(".")[-1].strip('"')
        lname = (name or "").lower()
        if src == want:
            return 3
        if src.endswith("_" + want):
            return 2
        if lname == want or lname.replace(" ", "") == want:
            return 1
        return 0

    best, best_score = -1, 0
    for index, (name, source) in enumerate(candidates):
        value = score(name, source)
        if value > best_score:
            best, best_score = index, value
    return best


_BASE_TABLES = ("SiteStructureAddressPoint", "RoadCenterline", "StreetNames", "Driveway", "AccessPoint")


def prefix_from_table(table: str) -> str:
    """'Wayne_SiteStructureAddressPoint' -> 'Wayne'. Empty when there is no prefix."""
    text = (table or "").strip()
    for base in _BASE_TABLES:
        if text.lower().endswith("_" + base.lower()) and len(text) > len(base) + 1:
            return text[:-(len(base) + 1)]
    return ""


def summarize(report: Sequence[dict]) -> Dict[str, dict]:
    """Per list: how many fields are wired and which fields were looked for but not found."""
    out: Dict[str, dict] = {}
    for item in report:
        entry = out.setdefault(item["source"], {"wired": [], "missing_table": False, "kind": item["kind"]})
        if item["status"] == "wired":
            entry["wired"].append(f"{item['role']}.{item['physical']}")
        elif item["status"] == "no table":
            entry["missing_table"] = True
    return out


# ---------------------------------------------------------------------------
# QGIS helpers. Imported lazily so the lists above stay plain Python.
# ---------------------------------------------------------------------------
def lookup_layer(pcfg, key: str):
    """The configured lookup layer, else a matching table already in the project."""
    from .project_config import layer_for, remember
    role = SPEC_BY_KEY[key][3]
    layer = layer_for(pcfg.lookups, role)
    if layer is not None:
        return layer
    layer = find_lookup_layer(key)
    if layer is not None:
        remember(pcfg.lookups, role, layer)
    return layer


def _source_table(layer) -> str:
    from qgis.core import QgsDataSourceUri, QgsProviderRegistry
    try:
        parts = QgsProviderRegistry.instance().decodeUri(layer.providerType(), layer.source()) or {}
    except Exception:  # noqa: BLE001
        parts = {}
    name = parts.get("layerName") or parts.get("table") or ""
    if not name:
        try:
            name = QgsDataSourceUri(layer.source()).table()
        except Exception:  # noqa: BLE001
            name = ""
    if not name and layer.providerType() == "ogr":
        path = parts.get("path") or layer.source().split("|")[0]
        name = os.path.splitext(os.path.basename(path))[0]
    return name or ""


def find_lookup_layer(key: str):
    """A table in the project that looks like this lookup table (by table or layer name)."""
    from qgis.core import QgsProject, QgsVectorLayer
    table = SPEC_BY_KEY[key][1]
    layers = [lyr for lyr in QgsProject.instance().mapLayers().values()
              if isinstance(lyr, QgsVectorLayer) and not lyr.isSpatial()]
    index = match_lookup_table(table, [(lyr.name(), _source_table(lyr)) for lyr in layers])
    if index < 0:
        return None
    layer = layers[index]
    if layer.fields().indexFromName("Code") < 0:
        return None
    if key == DOMAINS_KEY and layer.fields().indexFromName("Domain") < 0:
        return None
    return layer


def feature_layers(pcfg, centerline=None) -> dict:
    """address / centerline / master layers. ``centerline`` overrides the project setting."""
    from .project_config import layer_for
    return {
        "address": layer_for(pcfg.streets, "address"),
        "centerline": centerline if centerline is not None else layer_for(pcfg.streets, "centerline"),
        "master": layer_for(pcfg.streets, "master"),
    }


def field_maps(pcfg, layers: dict) -> Dict[str, Dict[str, str]]:
    """NENA name -> field, for each feature layer."""
    from . import config as C
    from .addressing.ssap import SSAP_FIELDS
    from .schema import RCL_FIELDS
    from .schema_pack import address_defs, street_field_defs
    out = {}
    lyr = layers.get("centerline")
    if lyr is not None:
        stored = C.load_from_layer(lyr)
        explicit = dict(stored.mapping) if stored is not None else {}
        out["centerline"] = resolve_fields([f.name() for f in lyr.fields()], RCL_FIELDS, explicit)
    lyr = layers.get("address")
    if lyr is not None:
        defs = list(SSAP_FIELDS)
        template = pcfg.addresses.address_template or None
        if template:
            defs = address_defs(template) + defs
        out["address"] = resolve_fields([f.name() for f in lyr.fields()], defs,
                                        dict(pcfg.streets.address_mapping or {}))
    lyr = layers.get("master")
    if lyr is not None:
        out["master"] = resolve_fields([f.name() for f in lyr.fields()], street_field_defs(), {})
    return out


def unit_type_choices(pcfg) -> List[Tuple[str, str]]:
    """Unit types from the project table, or the built-in list when that table is absent."""
    layer = lookup_layer(pcfg, "unit_type")
    rows = read_rows(layer) if layer is not None else []
    choices = active_choices(rows)
    return choices or builtin_unit_types()


_ROW_FIELDS = ("Code", "Label", "SortOrder", "Active", "Notes", "Domain", "Parent", "IsDefault")


def read_rows(layer, domain: str = "") -> List[dict]:
    """Lookup rows currently on the layer, including unsaved edits. ``domain`` keeps one list."""
    if layer is None:
        return []
    present = {name: layer.fields().indexFromName(name) for name in _ROW_FIELDS}
    present = {name: index for name, index in present.items() if index >= 0}
    rows = []
    for feature in layer.getFeatures():
        row = {}
        for name, index in present.items():
            value = feature.attribute(index)
            with contextlib.suppress(Exception):
                from ..compat import is_null
                if is_null(value):
                    value = None
            row[name] = "" if value is None else value
        row["_fid"] = feature.id()
        rows.append(row)
    return rows_in_domain(rows, domain) if domain else rows


def save_edits(layer, started: bool) -> bool:
    """Commit when this call opened the edit session. Leaves the user's own session alone."""
    if not started:
        return False
    if not layer.commitChanges():
        errors = "; ".join(layer.commitErrors() or [])
        layer.rollBack()
        from .layer_access import ConfigError
        raise ConfigError(f"Could not save {layer.name()}: {errors}")
    return True


def add_rows(layer, rows: Sequence[dict]) -> int:
    """Add lookup rows and save them (unless the layer was already being edited)."""
    if layer is None or not rows:
        return 0
    from qgis.core import QgsGeometry, QgsVectorLayerUtils
    from .layer_access import ensure_editable
    started = ensure_editable(layer)
    layer.beginEditCommand("NENA: add lookup values")
    added = 0
    for row in rows:
        attrs = {}
        for name, value in row.items():
            index = layer.fields().indexFromName(name)
            if index >= 0:
                attrs[index] = value
        # QGIS 4 needs an empty geometry here, not None (the lookup tables have no geometry)
        feature = QgsVectorLayerUtils.createFeature(layer, QgsGeometry(), attrs, layer.createExpressionContext())
        if layer.addFeature(feature):
            added += 1
    if not added:
        layer.destroyEditCommand()
        if started:
            layer.rollBack()
        return 0
    layer.endEditCommand()
    save_edits(layer, started)
    return added


def seed_layer(layer, key: str) -> int:
    """Write the default rows into an empty lookup layer and save them. A layer with rows is left alone."""
    if layer is None:
        return 0
    try:
        if layer.featureCount() > 0:
            return 0
    except Exception:  # noqa: BLE001
        return 0
    from .layer_access import ConfigError
    try:
        return add_rows(layer, seeds_for(key))
    except ConfigError:
        return 0


def _widget_type(layer, index) -> str:
    try:
        return layer.editorWidgetSetup(index).type() or ""
    except Exception:  # noqa: BLE001
        return ""


def apply_dropdowns(pcfg, centerline=None, force: bool = False, dry_run: bool = False) -> List[dict]:
    """
    Give every bound field its drop-down.

    Returns one report row per binding:
    ``{role, field, physical, kind, source, status}`` where status is
    ``wired``, ``no field`` (the layer has no such field), ``no layer``, ``no table``
    (the lookup table is missing), or ``kept`` (the field already has a widget of
    another kind that someone set up by hand; ``force`` replaces it).
    With ``dry_run`` nothing changes and fields that would be wired say ``ready``.
    """
    from qgis.core import QgsEditorWidgetSetup

    layers = feature_layers(pcfg, centerline)
    maps = field_maps(pcfg, layers)
    lookups = {key: lookup_layer(pcfg, key) for key in LOOKUP_KEYS}
    report = []
    for binding in BINDINGS:
        entry = {"role": binding.role, "field": binding.field, "physical": "", "kind": binding.kind,
                 "source": binding.source, "status": ""}
        report.append(entry)
        layer = layers.get(binding.role)
        if layer is None:
            entry["status"] = "no layer"
            continue
        physical = maps.get(binding.role, {}).get(binding.field)
        index = layer.fields().indexFromName(physical) if physical else -1
        if index < 0:
            entry["status"] = "no field"
            continue
        entry["physical"] = physical
        if binding.kind == "map":
            title, pairs = FIXED_LISTS[binding.source]
            wanted, config = "ValueMap", value_map_config(pairs)
        else:
            lookup = lookups.get(DOMAINS_KEY if binding.kind == "domain" else binding.source)
            if lookup is None or lookup.fields().indexFromName(binding.key_column) < 0 \
                    or lookup.fields().indexFromName("Label") < 0:
                entry["status"] = "no table"
                continue
            parent = maps.get(binding.role, {}).get(binding.parent_field) if binding.parent_field else ""
            domain = binding.source if binding.kind == "domain" else ""
            wanted = "ValueRelation"
            config = {
                "Layer": lookup.id(),
                "LayerName": lookup.name(),
                "LayerSource": lookup.source(),
                "LayerProviderName": lookup.providerType(),
                "Key": binding.key_column,
                "Value": "Label",
                "AllowMulti": False,
                "AllowNull": True,
                "FilterExpression": value_relation_filter(domain, parent or ""),
                "OrderByValue": False,
                "OrderByField": True,
                "OrderByFieldName": "SortOrder",
                # True turns the field into a text box with suggestions; False is a real drop-down list
                "UseCompleter": False,
            }
        current = _widget_type(layer, index)
        if not force and current not in ("", "TextEdit", wanted):
            entry["status"] = "kept"
            continue
        if dry_run:
            entry["status"] = "wired" if current == wanted else "ready"
            continue
        layer.setEditorWidgetSetup(index, QgsEditorWidgetSetup(wanted, config))
        entry["status"] = "wired"
    if not dry_run:
        with contextlib.suppress(Exception):
            sync_defaults(pcfg, centerline)
    return report


def apply_value_relations(pcfg, centerline=None) -> int:
    """Older name. Wires every drop-down and returns how many fields got one."""
    return sum(1 for row in apply_dropdowns(pcfg, centerline) if row["status"] == "wired")


def container_target(layer):
    """Where to put new lookup tables: the same GeoPackage, File Geodatabase, or database as ``layer``."""
    from qgis.core import QgsDataSourceUri, QgsProviderRegistry
    from .layer_access import ConfigError
    from .schema_writer import SchemaTarget

    if layer is None:
        raise ConfigError("Choose the address or centerline layer first, so the tables go in the same place.")
    provider = layer.providerType()
    registry = QgsProviderRegistry.instance()
    try:
        parts = registry.decodeUri(provider, layer.source()) or {}
    except Exception:  # noqa: BLE001
        parts = {}
    prefix = prefix_from_table(_source_table(layer))
    if provider == "ogr":
        path = (parts.get("path") or layer.source().split("|")[0]).rstrip("/\\")
        low = path.lower()
        if low.endswith(".gpkg"):
            return SchemaTarget("gpkg", path=path, prefix=prefix)
        if low.endswith(".gdb"):
            return SchemaTarget("gdb", path=path, prefix=prefix)
        folder = os.path.dirname(path) if os.path.isfile(path) else path
        return SchemaTarget("gpkg", path=os.path.join(folder or os.getcwd(), "NENA_Lookups.gpkg"))
    md = registry.providerMetadata(provider)
    if md is None:
        raise ConfigError(f"Lookup tables cannot be created next to a '{provider}' layer. "
                          "Use Schema and import to choose a place.")
    uri = QgsDataSourceUri(layer.source())
    try:
        connections = md.connections()
    except Exception:  # noqa: BLE001
        connections = {}
    for name, conn in connections.items():
        other = QgsDataSourceUri(conn.uri())
        same_service = bool(uri.service()) and other.service() == uri.service()
        same_db = (other.database() == uri.database() and other.host() == uri.host()
                   and (other.port() or "") == (uri.port() or ""))
        if same_service or same_db:
            return SchemaTarget("conn", provider=provider, connection=name, schema=uri.schema(), prefix=prefix)
    raise ConfigError(f"No saved QGIS connection matches the database of '{layer.name()}'. "
                      "Add the connection in the Browser, or use Schema and import to create the tables.")


def create_missing_lookups(pcfg, anchor, keys: Optional[Sequence[str]] = None):
    """
    Create (or reconnect) the lookup tables that are missing, next to ``anchor``.

    A table that already exists in that GeoPackage or database is opened rather
    than replaced. New tables get the default rows. Returns ``({key: layer}, target)``;
    the caller adds the layers to the project.
    """
    from qgis.core import QgsCoordinateReferenceSystem
    from .project_config import remember
    from .schema_pack import LAYERS_BY_KEY, qualified_table
    from .schema_writer import _open_one, create_schema, list_existing

    target = container_target(anchor)
    wanted = [key for key in (keys or LOOKUP_KEYS) if lookup_layer(pcfg, key) is None]
    if not wanted:
        return {}, target
    there = {name.lower() for name in list_existing(target, wanted)}
    made = {}
    fresh = []
    for key in wanted:
        spec = LAYERS_BY_KEY[key]
        table = qualified_table(spec.table, target.prefix)
        if table.lower() in there:
            made[key] = _open_one(target, table, spec.title)
        else:
            fresh.append(key)
    if fresh:
        made.update(create_schema(target, QgsCoordinateReferenceSystem(), fresh))
    for key, layer in made.items():
        seed_layer(layer, key)
        remember(pcfg.lookups, SPEC_BY_KEY[key][3], layer)
    return made, target


def collect_domain_values(pcfg, centerline=None) -> Dict[str, List[Tuple[str, str]]]:
    """
    Values already on the address points and centerlines, per domain list.

    Returns ``{domain: [(value, parent), …]}`` with each value once. Lists with a
    parent (postal community under ZIP code) keep the pairing they have in the data.
    """
    from qgis.core import QgsFeatureRequest
    layers = feature_layers(pcfg, centerline)
    maps = field_maps(pcfg, layers)
    found: Dict[str, Dict[str, str]] = {}
    for binding in bindings_for(kind="domain"):
        layer = layers.get(binding.role)
        physical = maps.get(binding.role, {}).get(binding.field)
        if layer is None or not physical or layer.fields().indexFromName(physical) < 0:
            continue
        bucket = found.setdefault(binding.source, {})
        parent = maps.get(binding.role, {}).get(binding.parent_field) if binding.parent_field else ""
        if parent and layer.fields().indexFromName(parent) >= 0:
            request = QgsFeatureRequest()
            flag = getattr(getattr(QgsFeatureRequest, "Flag", QgsFeatureRequest), "NoGeometry", None)
            if flag is not None:
                request.setFlags(flag)
            request.setSubsetOfAttributes([physical, parent], layer.fields())
            for feature in layer.getFeatures(request):
                value, owner = feature[physical], feature[parent]
                text = _text(value)
                if text and text.upper() not in {k.upper() for k in bucket}:
                    bucket[text] = _text(owner)
            continue
        try:
            values = layer.uniqueValues(layer.fields().indexFromName(physical))
        except Exception:  # noqa: BLE001
            values = set()
        for value in values:
            text = _text(value)
            if text and text.upper() not in {k.upper() for k in bucket}:
                bucket[text] = ""
    return {domain: sorted(pairs.items(), key=lambda item: item[0].upper()) for domain, pairs in found.items()}


def _text(value) -> str:
    with contextlib.suppress(Exception):
        from ..compat import is_null
        if is_null(value):
            return ""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()
