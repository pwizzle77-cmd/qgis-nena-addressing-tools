# -*- coding: utf-8 -*-
"""
Where the plugin's support tables sit in the Layers panel.

Support tables are the version (history) tables and the lookup tables
(road types, unit types, statuses, and the shared domain-values table).
The plugin reads and writes them all the time, but people rarely open them.

By default they go in two collapsed, unticked subgroups at the bottom of
the NENA group, below the layers people edit::

    NENA <project>
      Address points, Road centerlines, ...
      > Versions
      > Lookup tables

They stay ordinary layers there: you can open them, and change tracking
and the drop-down lists work whether they are ticked or not.

A project can instead hide them completely (Hide version and lookup
tables): QGIS's "private layer" flag keeps them out of the Layers panel
while they stay loaded. Their tree node is never removed: QGIS 4 deletes
a layer whose node is removed, on the next turn of the event loop.
"""

from qgis.core import QgsLayerTreeGroup, QgsMapLayer, QgsProject

VERSIONS_GROUP = "Versions"
LOOKUPS_GROUP = "Lookup tables"
GROUP_NAME = "NENA support tables"                      # where 3.6.15 put shown tables
_OLD_GROUPS = (GROUP_NAME, "NENA lookup lists")

ARCHIVE_ROLES = ("centerline", "streets", "address", "driveway", "access")
LOOKUP_ROLES = ("road_type", "unit_type", "address_status", "road_status", "address_type", "domains")


def _private_flag():
    flags = getattr(QgsMapLayer, "LayerFlag", QgsMapLayer)
    return flags.Private


def is_hidden(layer) -> bool:
    return bool(layer is not None and layer.flags() & _private_flag())


def _layers(settings, roles) -> list:
    from .project_config import layer_for
    out = []
    for role in roles:
        if not hasattr(settings, f"{role}_layer_id"):
            continue
        lyr = layer_for(settings, role)
        if lyr is not None and all(lyr.id() != seen.id() for seen in out):
            out.append(lyr)
    return out


def version_layers(pcfg) -> list:
    return _layers(pcfg.archive, ARCHIVE_ROLES)


def lookup_layers(pcfg) -> list:
    return _layers(pcfg.lookups, LOOKUP_ROLES)


def support_layers(pcfg) -> list:
    """The version and lookup tables this project uses, each once."""
    out = version_layers(pcfg)
    for lyr in lookup_layers(pcfg):
        if all(lyr.id() != seen.id() for seen in out):
            out.append(lyr)
    return out


# ---------------------------------------------------------------------------
# tree helpers
def _child_group(parent, name, create=True):
    for child in parent.children():
        if isinstance(child, QgsLayerTreeGroup) and child.name() == name:
            return child
    if not create:
        return None
    group = parent.addGroup(name)                   # added last: below the layers people edit
    group.setExpanded(False)
    group.setItemVisibilityChecked(False)
    return group


def _home_group(pcfg, root, support_ids):
    """The group that holds the project's live layers (centerline first), else the root."""
    from .project_config import layer_for
    candidates = []
    for settings, role in ((pcfg.streets, "centerline"), (pcfg.streets, "address"), (pcfg.streets, "master")):
        if hasattr(settings, f"{role}_layer_id"):
            candidates.append(layer_for(settings, role))
    for lyr in candidates:
        if lyr is None or lyr.id() in support_ids:
            continue
        node = root.findLayer(lyr.id())
        if node is not None and node.parent() is not None:
            parent = node.parent()
            # a live layer that sits inside one of our subgroups would be odd; use its parent
            while parent is not root and parent.name() in (VERSIONS_GROUP, LOOKUPS_GROUP) + _OLD_GROUPS:
                parent = parent.parent()
            return parent
    return root


def _move(layer, node, target):
    """Put ``layer`` in ``target``. The new node is made before the old one goes,
    so the layer is never without a node (QGIS 4 would delete it)."""
    if node is not None and node.parent() is target:
        return False
    new = target.addLayer(layer)
    if node is not None:
        new.setItemVisibilityChecked(node.itemVisibilityChecked())
        old_parent = node.parent()
        if old_parent is not None:
            old_parent.removeChildNode(node)
    else:
        new.setItemVisibilityChecked(False)
    return True


def _live_layer_below(home, target) -> bool:
    """Whether something other than the two subgroups comes after ``target`` in ``home``."""
    kids = list(home.children())
    if target not in kids:
        return False
    for child in kids[kids.index(target) + 1:]:
        if not (isinstance(child, QgsLayerTreeGroup) and child.name() in (VERSIONS_GROUP, LOOKUPS_GROUP)):
            return True
    return False


def _drop_empty(parent, names):
    for child in list(parent.children()):
        if isinstance(child, QgsLayerTreeGroup):
            _drop_empty(child, names)
            if child.name() in names and not child.children():
                parent.removeChildNode(child)


def arrange_support_layers(pcfg, project=None) -> int:
    """
    Put the version tables in a Versions subgroup and the lookup tables in a
    Lookup tables subgroup, collapsed and unticked, at the bottom of the
    group that holds the project's centerlines. Tables hidden by an earlier
    version are shown again. Returns how many tables moved or were shown.
    """
    prj = project or QgsProject.instance()
    root = prj.layerTreeRoot()
    versions, lookups = version_layers(pcfg), lookup_layers(pcfg)
    support_ids = {lyr.id() for lyr in versions + lookups}
    if not support_ids:
        return 0
    home = _home_group(pcfg, root, support_ids)
    flag = _private_flag()
    changed = 0
    for layers, name in ((versions, VERSIONS_GROUP), (lookups, LOOKUPS_GROUP)):
        if not layers:
            continue
        target = _child_group(home, name)
        for lyr in layers:
            was_hidden = is_hidden(lyr)
            if was_hidden:
                lyr.setFlags(lyr.flags() & ~flag)
            moved = _move(lyr, root.findLayer(lyr.id()), target)
            changed += int(moved or was_hidden)
        # keep the subgroups below the layers people edit
        if _live_layer_below(home, target):
            clone = target.clone()           # the clone's layer nodes exist before the old ones go
            home.addChildNode(clone)
            home.removeChildNode(target)
    _drop_empty(root, _OLD_GROUPS + (VERSIONS_GROUP, LOOKUPS_GROUP))
    if changed:
        prj.setDirty(True)
    return changed


# ---------------------------------------------------------------------------
# full hiding (opt-in)
def set_hidden(layer, hidden: bool, project=None) -> bool:
    """Hide or show one table. Returns True when something changed."""
    if layer is None:
        return False
    prj = project or QgsProject.instance()
    root = prj.layerTreeRoot()
    flag = _private_flag()
    if hidden:
        if is_hidden(layer):
            return False
        # The private flag alone keeps the table out of the Layers panel. The node stays.
        layer.setFlags(layer.flags() | flag)
        node = root.findLayer(layer.id())
        if node is not None:
            node.setItemVisibilityChecked(False)
        return True
    if not is_hidden(layer):
        return False
    layer.setFlags(layer.flags() & ~flag)
    if root.findLayer(layer.id()) is None:
        _child_group(root, GROUP_NAME).addLayer(layer).setItemVisibilityChecked(False)
    return True


def set_support_hidden(pcfg, hidden: bool, project=None) -> int:
    """Hide every support table completely, or show them in their subgroups. Returns how many changed."""
    prj = project or QgsProject.instance()
    if not hidden:
        return arrange_support_layers(pcfg, prj)
    changed = sum(1 for lyr in support_layers(pcfg) if set_hidden(lyr, True, prj))
    if changed:
        prj.setDirty(True)
    return changed


def apply_support_layout(pcfg, project=None) -> int:
    """Lay the support tables out the way the project says: subgroups, or hidden."""
    return set_support_hidden(pcfg, bool(pcfg.display.hide_support_tables), project)


def hidden_count(pcfg) -> int:
    return sum(1 for lyr in support_layers(pcfg) if is_hidden(lyr))
