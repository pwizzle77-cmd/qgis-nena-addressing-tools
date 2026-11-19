# -*- coding: utf-8 -*-
"""
Which line layers can be the centerline.

The Centerline picker in every window lists line layers. Version (history)
tables and driveways are line layers too, but editing them as the centerline
is always a mistake (a street assigned or drawn into the version table), so
they are left out of the picker and are never followed from the Layers panel.
"""

from typing import List, Optional

from qgis.core import QgsProject, QgsVectorLayer

from . import config as C
from .project_config import layer_for, load_project_config
from .schema_pack import VERSION_TABLE_SUFFIX

_ARCHIVE_ROLES = ("centerline", "streets", "address", "driveway", "access")


def _norm(text: str) -> str:
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def not_a_centerline(layer, pcfg=None) -> bool:
    """True for a version table, a driveway layer, or another layer the
    project uses for something else (address, access point, master streets)."""
    if not isinstance(layer, QgsVectorLayer):
        return True
    if pcfg is None:
        try:
            pcfg = load_project_config()
        except Exception:  # noqa: BLE001 - no project settings yet
            pcfg = None
    if pcfg is not None:
        others = [layer_for(pcfg.addresses, "driveway"), layer_for(pcfg.addresses, "access"),
                  layer_for(pcfg.streets, "address"), layer_for(pcfg.streets, "master")]
        others += [layer_for(pcfg.archive, role) for role in _ARCHIVE_ROLES]
        if any(o is not None and o.id() == layer.id() for o in others):
            return True
    for text in (layer.name(), _table_name(layer)):
        name = _norm(text)
        if not name:
            continue
        if name.endswith(_norm(VERSION_TABLE_SUFFIX)) or name.endswith("history"):
            return True
        if "driveway" in name:
            return True
    return False


def _table_name(layer) -> str:
    """The table (or GeoPackage layer) name behind the layer, when it shows."""
    source = layer.source() or ""
    for key in ("layername=", "table="):
        if key in source:
            part = source.split(key, 1)[1].split("|")[0].split(" ")[0]
            return part.strip("\"'").split(".")[-1].strip("\"'")
    return ""


def excluded_layers(pcfg=None) -> List[QgsVectorLayer]:
    if pcfg is None:
        try:
            pcfg = load_project_config()
        except Exception:  # noqa: BLE001
            pcfg = None
    return [lyr for lyr in QgsProject.instance().mapLayers().values()
            if isinstance(lyr, QgsVectorLayer) and not_a_centerline(lyr, pcfg)]


def preferred_centerline(candidates, pcfg=None) -> Optional[QgsVectorLayer]:
    """The centerline to open with: the one the project remembers, else a line
    layer that has centerline settings, else the first remaining one."""
    if pcfg is None:
        try:
            pcfg = load_project_config()
        except Exception:  # noqa: BLE001
            pcfg = None
    usable = [c for c in candidates if isinstance(c, QgsVectorLayer) and not not_a_centerline(c, pcfg)]
    remembered = layer_for(pcfg.streets, "centerline") if pcfg is not None else None
    if remembered is not None and not any(c.id() == remembered.id() for c in usable):
        remembered = None
    # The settings live on the centerline layer, so a layer that has them comes first.
    if remembered is not None and C.has_config(remembered):
        return remembered
    for c in usable:
        if C.has_config(c):
            return c
    if remembered is not None:
        return remembered
    return usable[0] if usable else None


def should_follow(new_layer, current) -> bool:
    """Switch the centerline when another line layer is made active in the Layers panel?

    Not away from a configured centerline to a line layer that has no NENA
    settings (railroads, a boundary drawn as lines, …): the settings would
    then be read from, and saved onto, the wrong layer. Choose that layer in
    the Centerline picker instead.
    """
    if new_layer is None or new_layer is current:
        return False
    if current is None or not C.has_config(current):
        return True
    return C.has_config(new_layer)
