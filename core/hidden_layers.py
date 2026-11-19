# -*- coding: utf-8 -*-
"""
Hide the plugin's support tables from the Layers panel.

Support tables are the version (history) tables and the lookup tables
(road types, unit types, statuses, and the shared domain-values table).
The plugin reads and writes them all the time, but people rarely open them.

Hidden tables stay loaded in the project, so change tracking and the
drop-down lists keep working. QGIS's own "private layer" flag keeps them
out of the Layers panel, and the flag is saved with the project.
"""

from qgis.core import QgsLayerTreeGroup, QgsMapLayer, QgsProject

GROUP_NAME = "NENA support tables"
_SUPPORT_GROUPS = (GROUP_NAME, "NENA lookup lists")

ARCHIVE_ROLES = ("centerline", "streets", "address", "driveway", "access")
LOOKUP_ROLES = ("road_type", "unit_type", "address_status", "road_status", "address_type", "domains")


def _private_flag():
    flags = getattr(QgsMapLayer, "LayerFlag", QgsMapLayer)
    return flags.Private


def is_hidden(layer) -> bool:
    return bool(layer is not None and layer.flags() & _private_flag())


def support_layers(pcfg) -> list:
    """The version and lookup tables this project uses, each once."""
    from .project_config import layer_for
    out = []
    for settings, roles in ((pcfg.archive, ARCHIVE_ROLES), (pcfg.lookups, LOOKUP_ROLES)):
        for role in roles:
            if not hasattr(settings, f"{role}_layer_id"):
                continue
            lyr = layer_for(settings, role)
            if lyr is not None and all(lyr.id() != seen.id() for seen in out):
                out.append(lyr)
    return out


def _group(root):
    for child in root.children():
        if isinstance(child, QgsLayerTreeGroup) and child.name() == GROUP_NAME:
            return child
    return root.addGroup(GROUP_NAME)


def _drop_empty_group(root):
    for child in list(root.children()):
        if isinstance(child, QgsLayerTreeGroup) and child.name() == GROUP_NAME and not child.children():
            root.removeChildNode(child)


def set_hidden(layer, hidden: bool, project=None) -> bool:
    """Hide or show one table. Returns True when something changed."""
    if layer is None:
        return False
    prj = project or QgsProject.instance()
    root = prj.layerTreeRoot()
    flag = _private_flag()
    node = root.findLayer(layer.id())
    if hidden:
        if is_hidden(layer):
            return False
        # The private flag alone keeps the table out of the Layers panel. Do not
        # remove its tree node: QGIS 4 deletes a layer whose node is removed on
        # the next event loop, even with the registry bridge turned off.
        layer.setFlags(layer.flags() | flag)
        if node is not None:
            node.setItemVisibilityChecked(False)
        return True
    if not is_hidden(layer):
        return False
    layer.setFlags(layer.flags() & ~flag)
    if node is None:
        _group(root).addLayer(layer).setItemVisibilityChecked(False)
    return True


def set_support_hidden(pcfg, hidden: bool, project=None) -> int:
    """Hide or show every support table. Returns how many changed."""
    prj = project or QgsProject.instance()
    changed = sum(1 for lyr in support_layers(pcfg) if set_hidden(lyr, hidden, prj))
    if hidden:
        _drop_empty_group(prj.layerTreeRoot())
    if changed:
        prj.setDirty(True)
    return changed


def hidden_count(pcfg) -> int:
    return sum(1 for lyr in support_layers(pcfg) if is_hidden(lyr))
