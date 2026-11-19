"""Hiding the version and lookup tables from the Layers panel."""
import importlib
import sys

import qgis.core as qc


class _Flags:
    Private = 8


class FakeMapLayer:
    LayerFlag = _Flags

    def __init__(self, lid):
        self._id, self._flags = lid, 0

    def id(self):
        return self._id

    def flags(self):
        return self._flags

    def setFlags(self, flags):
        self._flags = flags


class FakeGroup:
    def __init__(self, name="", parent=None):
        self._name, self._parent, self._children = name, parent, []
        self.expanded, self.checked = True, True

    def setExpanded(self, on):
        self.expanded = on

    def setItemVisibilityChecked(self, on):
        self.checked = on

    def itemVisibilityChecked(self):
        return self.checked

    def addChildNode(self, node):
        node._parent = self
        self._children.append(node)

    def clone(self):
        g = FakeGroup(self._name, None)
        g.expanded, g.checked = self.expanded, self.checked
        for c in self._children:
            k = c.clone()
            k._parent = g
            g._children.append(k)
        return g

    def name(self):
        return self._name

    def parent(self):
        return self._parent

    def children(self):
        return list(self._children)

    def addGroup(self, name):
        g = FakeGroup(name, self)
        self._children.append(g)
        return g

    def addLayer(self, layer):
        n = FakeNode(layer, self)
        self._children.append(n)
        return n

    def removeChildNode(self, node):
        self._children.remove(node)
        root = self
        while root._parent is not None:
            root = root._parent
        # QGIS 4 deletes a layer that is left without a node
        for lyr in _layers_under(node):
            if root.findLayer(lyr.id()) is None:
                DELETED.append(lyr.id())

    def findLayer(self, lid):
        for c in self._children:
            if isinstance(c, FakeNode) and c.layer.id() == lid:
                return c
            if isinstance(c, FakeGroup):
                hit = c.findLayer(lid)
                if hit is not None:
                    return hit
        return None


DELETED = []


def _layers_under(node):
    if isinstance(node, FakeNode):
        return [node.layer]
    out = []
    for c in node.children():
        out += _layers_under(c)
    return out


class FakeNode:
    def __init__(self, layer, parent):
        self.layer, self._parent, self.checked = layer, parent, True

    def clone(self):
        n = FakeNode(self.layer, None)
        n.checked = self.checked
        return n

    def itemVisibilityChecked(self):
        return self.checked

    def parent(self):
        return self._parent

    def setItemVisibilityChecked(self, on):
        self.checked = on


class FakeBridge:
    def __init__(self):
        self.enabled = True

    def isEnabled(self):
        return self.enabled

    def setEnabled(self, on):
        self.enabled = on


class FakeProject:
    def __init__(self):
        self.root, self.bridge, self.dirty = FakeGroup(), FakeBridge(), False

    def layerTreeRoot(self):
        return self.root

    def layerTreeRegistryBridge(self):
        return self.bridge

    def setDirty(self, on):
        self.dirty = on


def _module(monkeypatch):
    monkeypatch.setattr(qc, "QgsMapLayer", FakeMapLayer, raising=False)
    monkeypatch.setattr(qc, "QgsLayerTreeGroup", FakeGroup, raising=False)
    sys.modules.pop("nena_addressing_and_rcl_tools.core.hidden_layers", None)
    return importlib.import_module("nena_addressing_and_rcl_tools.core.hidden_layers")


def test_hide_keeps_layer_loaded_and_show_puts_it_back(monkeypatch):
    H = _module(monkeypatch)
    prj = FakeProject()
    lookups = prj.root.addGroup("NENA lookup lists")
    road_types = FakeMapLayer("rt")
    lookups.addLayer(road_types)

    assert H.set_hidden(road_types, True, prj)
    assert H.is_hidden(road_types)
    node = prj.root.findLayer("rt")
    assert node is not None                    # QGIS 4 deletes a layer whose node is removed
    assert node.checked is False

    assert not H.set_hidden(road_types, True, prj)   # already hidden

    assert H.set_hidden(road_types, False, prj)
    assert not H.is_hidden(road_types)
    assert prj.root.findLayer("rt") is not None


def test_show_adds_a_node_for_a_layer_hidden_the_old_way(monkeypatch):
    H = _module(monkeypatch)
    prj = FakeProject()
    old = FakeMapLayer("old")
    old.setFlags(old.flags() | FakeMapLayer.LayerFlag.Private)   # hidden by 3.6.15: no node
    assert H.set_hidden(old, False, prj)
    node = prj.root.findLayer("old")
    assert node is not None and node.parent().name() == H.GROUP_NAME and node.checked is False


def test_hide_setting_round_trips():
    from nena_addressing_and_rcl_tools.core.project_config import ProjectConfig
    cfg = ProjectConfig()
    cfg.display.hide_support_tables = True
    cfg.display.support_layout_chosen = True
    assert ProjectConfig.from_json(cfg.to_json()).display.hide_support_tables is True
    assert ProjectConfig.from_dict({}).display.hide_support_tables is False


class _Settings:
    def __init__(self, roles):
        for role in roles:
            setattr(self, f"{role}_layer_id", role)
            setattr(self, f"{role}_layer_source", "")


def _pcfg(monkeypatch, layers):
    """A project config whose layer_for() returns ``layers[role]``."""
    from nena_addressing_and_rcl_tools.core import project_config as PC
    pcfg = PC.ProjectConfig()
    pcfg.streets = _Settings(("centerline", "address", "master"))
    pcfg.archive = _Settings(("centerline", "address"))
    pcfg.lookups = _Settings(("road_type", "domains"))

    def layer_for(settings, role):
        group = "archive" if settings is pcfg.archive else "lookups" if settings is pcfg.lookups else "live"
        return layers.get((group, role))
    monkeypatch.setattr(PC, "layer_for", layer_for)
    return pcfg


def _names(group):
    return [c.name() if isinstance(c, FakeGroup) else c.layer.id() for c in group.children()]


def test_support_tables_go_in_collapsed_subgroups_below_the_live_layers(monkeypatch):
    H = _module(monkeypatch)
    DELETED.clear()
    prj = FakeProject()
    home = prj.root.addGroup("NENA County")
    layers = {}
    # the order Create schema adds them: versions, lookups, then the live layers
    for key, lid in ((("archive", "centerline"), "rcl_v"), (("archive", "address"), "addr_v"),
                     (("lookups", "road_type"), "rt"), (("lookups", "domains"), "dom"),
                     (("live", "address"), "addr"), (("live", "centerline"), "rcl")):
        layers[key] = FakeMapLayer(lid)
        home.addLayer(layers[key])
    pcfg = _pcfg(monkeypatch, layers)

    assert H.arrange_support_layers(pcfg, prj) == 4
    assert _names(home) == ["addr", "rcl", H.VERSIONS_GROUP, H.LOOKUPS_GROUP]
    versions, lookups = home.children()[2], home.children()[3]
    assert _names(versions) == ["rcl_v", "addr_v"] and _names(lookups) == ["rt", "dom"]
    assert not versions.expanded and not versions.checked and not lookups.expanded and not lookups.checked
    assert DELETED == []                                   # no table was ever left without a node
    assert prj.dirty
    # running it again changes nothing
    assert H.arrange_support_layers(pcfg, prj) == 0
    assert _names(home) == ["addr", "rcl", H.VERSIONS_GROUP, H.LOOKUPS_GROUP]


def test_tables_hidden_by_an_older_version_come_back_in_the_subgroups(monkeypatch):
    H = _module(monkeypatch)
    DELETED.clear()
    prj = FakeProject()
    home = prj.root.addGroup("NENA County")
    old = prj.root.addGroup(H.GROUP_NAME)
    layers = {("live", "centerline"): FakeMapLayer("rcl"), ("archive", "centerline"): FakeMapLayer("rcl_v"),
              ("lookups", "road_type"): FakeMapLayer("rt")}
    home.addLayer(layers[("live", "centerline")])
    old.addLayer(layers[("archive", "centerline")])
    rt = layers[("lookups", "road_type")]
    rt.setFlags(rt.flags() | FakeMapLayer.LayerFlag.Private)    # 3.6.15: hidden, node removed
    pcfg = _pcfg(monkeypatch, layers)

    assert H.apply_support_layout(pcfg, prj) == 2
    assert not H.is_hidden(rt)
    assert _names(home) == ["rcl", H.VERSIONS_GROUP, H.LOOKUPS_GROUP]
    assert H.GROUP_NAME not in _names(prj.root)                 # the emptied old group is gone
    assert DELETED == []


def test_hiding_completely_is_opt_in_and_old_projects_move_to_the_subgroups():
    from nena_addressing_and_rcl_tools.core.project_config import ProjectConfig
    assert ProjectConfig().display.hide_support_tables is False
    # saved by 3.6.15, where hidden was the default nobody chose
    assert ProjectConfig.from_dict({"display": {"hide_support_tables": True}}).display.hide_support_tables is False
    chosen = ProjectConfig.from_dict({"display": {"hide_support_tables": True, "support_layout_chosen": True}})
    assert chosen.display.hide_support_tables is True
