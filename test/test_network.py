# -*- coding: utf-8 -*-
"""Centerline network checks: near-miss ends, unsplit crossings, pseudo-nodes."""

import pytest

import qgis.core  # noqa: E402

if "fake_qgis" not in (getattr(qgis.core, "__file__", "") or ""):
    pytest.skip("real QGIS available - fake-QGIS tests skipped", allow_module_level=True)

from qgis.core import QgsField, QgsProject, QgsVectorLayer  # noqa: E402

from nena_addressing_and_rcl_tools.core import config as C  # noqa: E402
from nena_addressing_and_rcl_tools.core.validator import Validator  # noqa: E402

FIELDS = ["St_Name", "FromAddr_L", "ToAddr_L", "FromAddr_R", "ToAddr_R", "Parity_L", "Parity_R"]


def _layer(extra=()):
    lyr = QgsVectorLayer("LineString", "rcl")
    for name in list(FIELDS) + list(extra):
        kind = "int" if name.startswith(("From", "To")) else "string"
        lyr.addAttribute(QgsField(name, kind))
    QgsProject.instance().addMapLayer(lyr)
    names = list(FIELDS) + list(extra)
    cfg = C.PluginConfig(mapping={name: name for name in names})
    cfg.street_key_fields = ["St_Name"]
    cfg.validation.check_mandatory = False
    cfg.validation.check_names = False
    cfg.validation.check_nguid = False
    cfg.validation.check_overlaps = False
    cfg.validation.check_gaps = False
    cfg.validation.check_direction = False
    cfg.validation.check_parity = False
    cfg.validation.check_ranges = False
    cfg.validation.check_domains = False
    return lyr, cfg


def _road(lyr, pts, name="MAIN"):
    attrs = [name, 100, 198, 101, 199, "E", "O"]
    if lyr.fields().lookupField("Overpass") >= 0:
        attrs.append(None)
    return lyr.add_committed(attrs, pts)


def _codes(lyr, cfg):
    return {(i.code, i.fid) for i in Validator(lyr, cfg).run(False)}


def test_crossing_near_end_and_pseudo_node():
    lyr, cfg = _layer()
    main_a = _road(lyr, [(0, 0), (100, 0)])
    main_b = _road(lyr, [(100, 0), (180, 0)])          # same road, shared node, same side attributes
    cross = _road(lyr, [(50, -40), (50, 40)], "OAK")   # crosses main_a in the middle
    _road(lyr, [(180, 3), (220, 3)], "STUB")           # 3 m short of main_b
    far = _road(lyr, [(0, 100), (40, 100)], "CUL")     # a real dead end, well clear of the others
    codes = _codes(lyr, cfg)
    assert ("UNSPLIT_CROSSING", cross) in codes or ("UNSPLIT_CROSSING", main_a) in codes
    assert ("PSEUDO_NODE", main_a) in codes or ("PSEUDO_NODE", main_b) in codes
    assert any(code == "DANGLE_NEAR" for code, _fid in codes)
    assert ("DANGLE_NEAR", far) not in codes


def test_meeting_at_an_end_is_not_a_crossing():
    lyr, cfg = _layer()
    _road(lyr, [(0, 0), (100, 0)])
    _road(lyr, [(100, 0), (100, 50)], "OAK")
    assert "UNSPLIT_CROSSING" not in {code for code, _fid in _codes(lyr, cfg)}


def test_overpass_is_not_an_unsplit_crossing():
    lyr, cfg = _layer(("Overpass",))
    _road(lyr, [(0, 0), (100, 0)])
    cross = _road(lyr, [(50, -40), (50, 40)], "OAK")
    lyr.changeAttributeValues(cross, {lyr.fields().lookupField("Overpass"): "Y"})
    assert "UNSPLIT_CROSSING" not in {code for code, _fid in _codes(lyr, cfg)}
