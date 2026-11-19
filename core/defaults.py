# -*- coding: utf-8 -*-
"""
Default values for centerline fields that rarely change (state, county,
one-way, parity, and any other field the user adds).

A default is written only into an empty field. A value already on the
feature, including a parity that ranging calculated, is left alone.
"""

from typing import Dict, Hashable, Optional, Sequence

from qgis.core import QgsFeatureRequest, QgsVectorLayer

from .addressing.ssap import COMMUNITY_FIELDS
from .config import PluginConfig
from .layer_access import FieldAccessor, apply_changes

FILL_COMMAND = "NENA: fill default values"


def is_blank(value) -> bool:
    return value is None or str(value).strip() == ""


def configured_defaults(cfg: PluginConfig) -> Dict[str, str]:
    """
    NENA name -> text, dropping blanks.

    Road status, road class and discrepancy agency follow the Default ticked
    in the lookup tables when those tables have the Default column: the
    ticked value wins over Settings, Defaults, and an unticked list means no
    default. Without lookup tables the settings apply as before.
    """
    out = {}
    for name, raw in (getattr(cfg, "defaults", None) or {}).items():
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            out[str(name)] = text
    for name, code in lookup_defaults("centerline").items():
        if code:
            out[name] = code
        else:
            out.pop(name, None)
    return out


def lookup_defaults(role: str) -> Dict[str, str]:
    """The project's lookup-table defaults for one layer role (``{}`` when there are none)."""
    try:
        from .lookups import field_defaults
        from .project_config import load_project_config
        return field_defaults(load_project_config(), role, governed=True)
    except Exception:  # noqa: BLE001 - no project, no lookup tables: the settings decide
        return {}


def empty_defaults(cfg: PluginConfig, acc: FieldAccessor, current: Dict[str, object]) -> Dict[str, str]:
    """Defaults whose field is mapped and currently empty."""
    out = {}
    for name, text in configured_defaults(cfg).items():
        if acc.has(name) and is_blank(current.get(name)):
            out[name] = text
    return out


def fill_empty_defaults(layer: QgsVectorLayer, cfg: PluginConfig,
                        fids: Optional[Sequence[Hashable]] = None) -> int:
    """Write defaults into empty fields. One undo step. Returns features changed."""
    wanted = configured_defaults(cfg)
    if not wanted:
        return 0
    acc = FieldAccessor(layer, cfg)
    if not any(acc.has(name) for name in wanted):
        return 0
    req = QgsFeatureRequest() if not fids else QgsFeatureRequest().setFilterFids(list(fids))
    changes = {}
    for feat in layer.getFeatures(req):
        current = {name: acc.get(feat, name) for name in wanted}
        vals = empty_defaults(cfg, acc, current)
        if vals:
            changes[feat.id()] = vals
    return apply_changes(layer, acc, changes, FILL_COMMAND)


def address_fallbacks(cfg: PluginConfig, side: str, community: Dict[str, str]) -> Dict[str, str]:
    """
    Address fields to fill when that side of the centerline did not supply them.

    ``community`` is keyed by the attribution name (``State``, ``County``, …).
    The result is keyed by the address field name.
    """
    if side not in ("L", "R"):
        return {}
    defaults = configured_defaults(cfg)
    if not defaults:
        return {}
    index = 1 if side == "L" else 2
    out = {}
    for key, fields in COMMUNITY_FIELDS.items():
        if community.get(key):
            continue
        val = defaults.get(fields[index])
        if val:
            out[fields[0]] = val
    return out
