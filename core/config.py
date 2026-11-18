# -*- coding: utf-8 -*-
"""
Plugin configuration: field mapping + ranging / editing / validation options.

The configuration is a plain dataclass tree that serialises to JSON. It can be
stored:
  * as a layer custom property (``nena_rcl/config``) -> travels with the
    QGIS project, layer definition files (.qlr) and QML styles (when the
    "Custom properties" style category is included), or
  * as a standalone JSON file (export / import).

Only the persistence helpers at the bottom touch QGIS, and they import it
lazily so the rest of this module is testable without QGIS.
"""

import json
from dataclasses import dataclass, field, asdict, fields as dc_fields
from typing import Dict, List, Optional

CONFIG_VERSION = 1
CONFIG_PROPERTY = "nena_rcl/config"

# --- ranging methods -------------------------------------------------------
M_HUNDRED = "hundred_block"
M_INTERVAL = "interval"
M_PROPORTIONAL = "proportional"
M_POINTS = "address_points"
RANGING_METHODS = (M_HUNDRED, M_INTERVAL, M_PROPORTIONAL, M_POINTS)
# split re-ranging: proportional, but bent through existing address points
M_ANCHORED = "anchored"

# --- scope -----------------------------------------------------------------
SCOPE_CHAIN = "chain"       # connected segments with the same street name are ranged as one street
SCOPE_FEATURE = "feature"   # every feature is ranged on its own

# --- where does a chain start numbering? ----------------------------------
START_USER = "user"          # always start at base_address
START_EXISTING = "existing"  # keep the first segment's existing FROM value (fallback: base_address)
START_UPSTREAM = "upstream"  # continue from a connected, same-named, upstream segment (fallback: base_address)

# --- proportional extent source -------------------------------------------
PROP_EXISTING = "existing_extent"
PROP_USER = "user_range"

# --- behaviour after a plain geometry edit (vertex move etc.) -------------
GEOM_NONE = "none"
GEOM_INTERVAL = "interval"
GEOM_POINTS = "address_points"

DISTANCE_UNITS = ("meters", "feet", "us_survey_feet", "kilometers", "miles")


@dataclass
class RangingSettings:
    method: str = M_INTERVAL         # by distance (interval_distance per number); hundred-block is an option
    scope: str = SCOPE_CHAIN
    left_parity: str = "O"           # default convention: odd on the left, even on the right
    right_parity: str = "E"
    respect_existing_parity: bool = True   # a valid Parity_L/R already on a feature wins (lets you keep Z sides)
    start_mode: str = START_UPSTREAM
    base_address: int = 100
    block_size: int = 100            # hundred-block size (use 1000 for some rural grids)
    interval_distance: float = 5.28  # ground distance per address number (5.28 ft = 1000 numbers / mile)
    interval_units: str = "feet"
    proportional_source: str = PROP_EXISTING
    proportional_from: int = 100
    proportional_to: int = 199
    proportional_anchor_points: bool = True   # bend proportional ranges through address points (if a point layer is set)
    # address-point snapping
    point_layer_id: str = ""
    point_number_field: str = ""
    point_street_field: str = ""     # optional: full street name on the point layer, used to match lines
    point_search_distance_m: float = 60.0
    point_expand_to_block: bool = True
    point_infer_parity: bool = True
    # writing behaviour
    overwrite_existing: bool = True  # False: only fill features whose ranges are empty / zero
    write_parity: bool = True
    stamp_date_update: bool = True
    snap_tolerance_m: float = 0.5    # endpoint matching tolerance for connectivity


@dataclass
class EditSettings:
    auto_rerange_split: bool = True
    auto_merge: bool = False
    split_on_new_intersection: bool = True  # a new road ending on / crossing an existing one splits it + re-ranges
    split_at_crossings: bool = True         # also split where the new road crosses (off for overpasses)
    split_method: str = M_ANCHORED          # anchored | proportional | address_points
    geometry_edit_action: str = GEOM_NONE
    manage_split_policies: bool = True      # set Duplicate split policy on range fields, Unset on NGUID
    auto_nguid_new: bool = True             # give new / split-off features a fresh NGUID
    auto_sides: bool = True                 # ZIP / MSAG / ESN … sides from Settings, Sides on new and moved roads
    agency_id: str = ""                     # e.g. "psap.example.gov" used in NGUIDs and DiscrpAgID
    nguid_format: str = "urn"               # urn | legacy
    nguid_local_id: str = "uuid"            # uuid | fid


@dataclass
class ValidationSettings:
    check_mandatory: bool = True
    check_parity: bool = True
    check_ranges: bool = True
    check_overlaps: bool = True
    check_gaps: bool = True
    check_direction: bool = True
    check_domains: bool = True
    check_nguid: bool = True
    check_geometry: bool = True
    check_names: bool = True        # against the master street names table (when configured)
    gap_tolerance: int = 0          # extra numbers allowed between consecutive segments before a gap is flagged
    allow_decreasing: bool = False  # treat FROM > TO as a warning (False) or as acceptable (True)
    overlap_community_field: str = ""  # e.g. "MSAGComm" or "IncMuni" -> overlaps only within same street+community
    select_overlaps: bool = True       # select features with overlapping ranges after a validation run


@dataclass
class PluginConfig:
    version: int = CONFIG_VERSION
    mapping: Dict[str, str] = field(default_factory=dict)   # NENA name -> layer field name
    street_key_fields: List[str] = field(default_factory=lambda: [
        "St_PreMod", "St_PreDir", "St_PreTyp", "St_PreSep", "St_Name", "St_PosTyp", "St_PosDir", "St_PosMod"])
    ranging: RangingSettings = field(default_factory=RangingSettings)
    edit: EditSettings = field(default_factory=EditSettings)
    validation: ValidationSettings = field(default_factory=ValidationSettings)
    # optional field template (see core/template.py): {"name": str, "source": str, "fields": [ {...}, ... ]}
    template: Dict = field(default_factory=dict)
    # NENA name -> value written only into empty fields (state, county, one-way, parity, …)
    defaults: Dict[str, str] = field(default_factory=dict)

    # -- (de)serialisation ------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self, indent: Optional[int] = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    @classmethod
    def from_dict(cls, data: dict) -> "PluginConfig":
        data = data or {}
        cfg = cls()
        cfg.mapping = {str(k): str(v) for k, v in (data.get("mapping") or {}).items() if v}
        if isinstance(data.get("street_key_fields"), list):
            cfg.street_key_fields = [str(x) for x in data["street_key_fields"]]
        cfg.ranging = _load_section(RangingSettings, data.get("ranging"))
        cfg.edit = _load_section(EditSettings, data.get("edit"))
        cfg.validation = _load_section(ValidationSettings, data.get("validation"))
        tpl = data.get("template")
        if isinstance(tpl, dict) and isinstance(tpl.get("fields"), list):
            cfg.template = tpl
        raw = data.get("defaults")
        if isinstance(raw, dict):
            cfg.defaults = {str(k): str(v).strip() for k, v in raw.items()
                            if v not in (None, "") and str(v).strip()}
        return cfg

    @classmethod
    def from_json(cls, text: str) -> "PluginConfig":
        return cls.from_dict(json.loads(text))

    def mapped(self, nena_name: str) -> Optional[str]:
        val = self.mapping.get(nena_name)
        return val or None


def _load_section(klass, data):
    """Build a settings dataclass, ignoring unknown keys and coercing simple types."""
    obj = klass()
    if not isinstance(data, dict):
        return obj
    for f in dc_fields(klass):
        if f.name not in data:
            continue
        default = getattr(obj, f.name)
        value = data[f.name]
        try:
            if isinstance(default, bool):
                value = bool(value)
            elif isinstance(default, int):
                value = int(value)
            elif isinstance(default, float):
                value = float(value)
            elif isinstance(default, str):
                value = "" if value is None else str(value)
        except (TypeError, ValueError):
            continue
        setattr(obj, f.name, value)
    return obj


# ---------------------------------------------------------------------------
# QGIS persistence helpers (lazy imports)
# ---------------------------------------------------------------------------
def load_from_layer(layer) -> Optional[PluginConfig]:
    """Return the config stored on a layer, or None if there is none / it is unreadable."""
    if layer is None:
        return None
    raw = layer.customProperty(CONFIG_PROPERTY, "")
    if not raw:
        return None
    try:
        return PluginConfig.from_json(str(raw))
    except (ValueError, TypeError):
        return None


def save_to_layer(layer, cfg: PluginConfig) -> None:
    """Store config as a layer custom property and flag the project as modified."""
    layer.setCustomProperty(CONFIG_PROPERTY, cfg.to_json(indent=None))
    try:
        from qgis.core import QgsProject
        QgsProject.instance().setDirty(True)
    except ImportError:  # pragma: no cover - only outside QGIS
        pass


def has_config(layer) -> bool:
    return bool(layer is not None and layer.customProperty(CONFIG_PROPERTY, ""))


def save_to_file(path: str, cfg: PluginConfig) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(cfg.to_json())


def load_from_file(path: str) -> PluginConfig:
    with open(path, "r", encoding="utf-8") as fh:
        return PluginConfig.from_json(fh.read())
