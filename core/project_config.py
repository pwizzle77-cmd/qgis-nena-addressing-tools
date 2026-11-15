# -*- coding: utf-8 -*-
"""
Project-level configuration for the addressing tools.

Street names and addresses span several layers, so – unlike the per-layer
centerline configuration – these settings live in the QGIS project
(``.qgz``, scope ``nena_addressing``) and can be exported to JSON.

Layers are remembered by id and by data source, so the configuration
survives a layer being removed and added again.
"""

import json
from dataclasses import asdict, dataclass, field, fields as dc_fields
from typing import Dict, List

PROJECT_SCOPE = "nena_addressing"
PROJECT_KEY = "/config"
CONFIG_VERSION = 1


@dataclass
class StreetSettings:
    master_layer_id: str = ""
    master_layer_source: str = ""
    centerline_layer_id: str = ""
    centerline_layer_source: str = ""
    address_layer_id: str = ""
    address_layer_source: str = ""
    street_id_field: str = "StreetID"         # link field on centerlines and address points
    address_mapping: Dict[str, str] = field(default_factory=dict)   # NENA part name -> address field
    # uniqueness
    scope_mode: str = "county"                # county | field
    scope_field: str = ""                     # NENA field on centerlines used as scope (e.g. MSAGComm_L)
    strictness: str = "full"                  # full | ignore_type
    soundalike: bool = True
    action: str = "block"                     # block | warn
    # behaviour
    keep_alias_on_rename: bool = True
    cascade_direct_edits: bool = True         # edits typed into the master table cascade automatically
    copy_on_assign: bool = True               # picking a StreetID on a feature copies the name parts
    stamp_date_update: bool = True


@dataclass
class AddressSettings:
    driveway_layer_id: str = ""
    driveway_layer_source: str = ""
    address_template: Dict = field(default_factory=dict)      # optional state template (built-in NENA SSAP otherwise)
    driveway_mapping: Dict[str, str] = field(default_factory=dict)
    # numbering
    method: str = "distance"                  # distance | interpolate | frontage
    distance_per_number: float = 5.28
    distance_units: str = "feet"
    start_number: int = 100
    frontage_interval: float = 50.0
    frontage_step: int = 2
    rounding: str = "nearest"                 # nearest | down
    left_parity: str = "O"
    right_parity: str = "E"
    use_segment_parity: bool = True           # a segment's own Parity_L/R (O/E) wins when set
    order_check: bool = True
    collision: str = "next"                   # next | suffix | ask
    overrides: Dict[str, Dict] = field(default_factory=dict)   # community value -> {method, distance_per_number, ...}
    override_field: str = "MSAGComm"          # community key the overrides are keyed by
    # creation
    placement: str = "Structure"
    create_driveway: bool = True
    attribute_at: str = "rooftop"             # kept so an older project still loads; addresses copy the road side
    attribution_rules: List[Dict] = field(default_factory=list)   # {layer_id, layer_source, source_field, target}
    overlay: bool = True
    overlay_limit: int = 300


@dataclass
class MsagSettings:
    """A telephone-company MSAG extract loaded in the project, and which fields to read."""

    layer_id: str = ""
    layer_source: str = ""
    street_field: str = ""
    community_field: str = ""
    low_field: str = ""
    high_field: str = ""
    parity_field: str = ""


@dataclass
class ArchiveSettings:
    """Version tables paired with the live centerline, streets, addresses, and driveways.

    The project key stays ``archive`` so a project saved by an earlier build still loads.
    """

    enabled: bool = False
    centerline_layer_id: str = ""
    centerline_layer_source: str = ""
    streets_layer_id: str = ""
    streets_layer_source: str = ""
    address_layer_id: str = ""
    address_layer_source: str = ""
    driveway_layer_id: str = ""
    driveway_layer_source: str = ""


@dataclass
class ProjectConfig:
    version: int = CONFIG_VERSION
    streets: StreetSettings = field(default_factory=StreetSettings)
    addresses: AddressSettings = field(default_factory=AddressSettings)
    msag: MsagSettings = field(default_factory=MsagSettings)
    archive: ArchiveSettings = field(default_factory=ArchiveSettings)

    def to_json(self, indent=None) -> str:
        return json.dumps(asdict(self), indent=indent)

    @classmethod
    def from_dict(cls, data) -> "ProjectConfig":
        cfg = cls()
        _load(cfg.streets, (data or {}).get("streets") or {}, str_dicts=("address_mapping",))
        _load(cfg.addresses, (data or {}).get("addresses") or {}, str_dicts=("driveway_mapping",))
        _load(cfg.msag, (data or {}).get("msag") or {})
        _load(cfg.archive, (data or {}).get("archive") or {})
        return cfg

    @classmethod
    def from_json(cls, text) -> "ProjectConfig":
        return cls.from_dict(json.loads(text) if text else {})


def _load(obj, data: dict, str_dicts=()) -> None:
    for f in dc_fields(obj):
        if f.name not in data:
            continue
        default = getattr(obj, f.name)
        v = data[f.name]
        try:
            if isinstance(default, bool):
                v = bool(v)
            elif isinstance(default, int):
                v = int(v)
            elif isinstance(default, float):
                v = float(v)
            elif isinstance(default, dict):
                v = dict(v or {})
                if f.name in str_dicts:
                    v = {str(k): str(x) for k, x in v.items() if x}
            elif isinstance(default, list):
                v = [x for x in (v or []) if isinstance(x, dict)]
            elif isinstance(default, str):
                v = "" if v is None else str(v)
        except (TypeError, ValueError):
            continue
        setattr(obj, f.name, v)


# ---------------------------------------------------------------------------
# QGIS helpers (lazy imports)
# ---------------------------------------------------------------------------
def load_project_config() -> ProjectConfig:
    from qgis.core import QgsProject
    raw, ok = QgsProject.instance().readEntry(PROJECT_SCOPE, PROJECT_KEY, "")
    if not ok or not raw:
        return ProjectConfig()
    try:
        return ProjectConfig.from_json(raw)
    except (ValueError, TypeError):
        return ProjectConfig()


def save_project_config(cfg: ProjectConfig) -> None:
    from qgis.core import QgsProject
    prj = QgsProject.instance()
    prj.writeEntry(PROJECT_SCOPE, PROJECT_KEY, cfg.to_json())
    prj.setDirty(True)


def resolve_layer(layer_id: str, source: str = ""):
    """Layer by id, else the first vector layer with the same data source."""
    from qgis.core import QgsProject, QgsVectorLayer
    prj = QgsProject.instance()
    lyr = prj.mapLayer(layer_id) if layer_id else None
    if isinstance(lyr, QgsVectorLayer):
        return lyr
    if source:
        for cand in prj.mapLayers().values():
            if isinstance(cand, QgsVectorLayer) and cand.source() == source:
                return cand
    return None


def remember(settings, role: str, layer) -> None:
    """Store a layer for a role ('master', 'centerline', 'address' on StreetSettings; 'driveway' on AddressSettings)."""
    setattr(settings, f"{role}_layer_id", layer.id() if layer else "")
    setattr(settings, f"{role}_layer_source", layer.source() if layer else "")


def layer_for(settings, role: str):
    return resolve_layer(getattr(settings, f"{role}_layer_id"), getattr(settings, f"{role}_layer_source"))

