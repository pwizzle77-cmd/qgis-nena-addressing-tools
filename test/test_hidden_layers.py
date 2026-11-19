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

    def findLayer(self, lid):
        for c in self._children:
            if isinstance(c, FakeNode) and c.layer.id() == lid:
                return c
            if isinstance(c, FakeGroup):
                hit = c.findLayer(lid)
                if hit is not None:
                    return hit
        return None


class FakeNode:
    def __init__(self, layer, parent):
        self.layer, self._parent, self.checked = layer, parent, True

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


def test_hide_setting_defaults_on_and_round_trips():
    from nena_addressing_and_rcl_tools.core.project_config import ProjectConfig
    assert ProjectConfig().display.hide_support_tables is True
    cfg = ProjectConfig()
    cfg.display.hide_support_tables = False
    assert ProjectConfig.from_json(cfg.to_json()).display.hide_support_tables is False
    assert ProjectConfig.from_dict({}).display.hide_support_tables is True
