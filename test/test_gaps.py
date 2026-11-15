# -*- coding: utf-8 -*-
"""Range gaps, intersection splits, defaults, and the MSAG comparison."""

import pytest

import qgis.core  # noqa: E402

if "fake_qgis" not in (getattr(qgis.core, "__file__", "") or ""):
    pytest.skip("real QGIS available - fake-QGIS tests skipped", allow_module_level=True)


from qgis.core import QgsField, QgsProject, QgsVectorLayer  # noqa: E402

from nena_rcl_tools.core import config as C  # noqa: E402
from nena_rcl_tools.core.defaults import fill_empty_defaults  # noqa: E402
from nena_rcl_tools.core.gaps import fill_range_gaps  # noqa: E402
from nena_rcl_tools.core.intersections import split_existing_intersections  # noqa: E402
from nena_rcl_tools.core.msag import compare_msag  # noqa: E402
from nena_rcl_tools.core.project_config import MsagSettings  # noqa: E402

FIELDS = ["St_Name", "St_PosTyp", "FromAddr_L", "ToAddr_L", "FromAddr_R", "ToAddr_R",
          "Parity_L", "Parity_R", "State_L", "County_L"]


def _layer():
    lyr = QgsVectorLayer("LineString", "rcl")
    for name in FIELDS:
        kind = "int" if name.startswith(("From", "To")) else "string"
        lyr.addAttribute(QgsField(name, kind))
    QgsProject.instance().addMapLayer(lyr)
    cfg = C.PluginConfig(mapping={name: name for name in FIELDS})
    cfg.street_key_fields = ["St_Name", "St_PosTyp"]
    cfg.edit.agency_id = "psap.example.gov"
    return lyr, cfg


def _road(lyr, pts, attrs):
    return lyr.add_committed(attrs, pts)


def _val(lyr, fid, name):
    value = lyr._feats[fid].attribute(FIELDS.index(name))
    return getattr(value, "v", value) if hasattr(value, "isNull") else value


def test_fill_range_gap_extends_the_upstream_end():
    lyr, cfg = _layer()
    a = _road(lyr, [(0, 0), (100, 0)], ["MAIN", "St", 101, 199, 100, 198, "O", "E", None, None])
    b = _road(lyr, [(100, 0), (200, 0)], ["MAIN", "St", 301, 399, 300, 398, "O", "E", None, None])
    assert fill_range_gaps(lyr, cfg) == 1
    assert (_val(lyr, a, "ToAddr_L"), _val(lyr, a, "ToAddr_R")) == (299, 298)
    assert _val(lyr, b, "FromAddr_L") == 301


def test_fill_range_gap_on_the_selected_downstream_segment_only():
    lyr, cfg = _layer()
    a = _road(lyr, [(0, 0), (100, 0)], ["MAIN", "St", 101, 199, 100, 198, "O", "E", None, None])
    b = _road(lyr, [(100, 0), (200, 0)], ["MAIN", "St", 301, 399, 300, 398, "O", "E", None, None])
    lyr.selectByIds([b])
    assert fill_range_gaps(lyr, cfg, [b]) == 1
    assert _val(lyr, a, "ToAddr_L") == 199
    assert (_val(lyr, b, "FromAddr_L"), _val(lyr, b, "FromAddr_R")) == (201, 200)


def test_overlap_and_tolerance_are_not_filled():
    lyr, cfg = _layer()
    a = _road(lyr, [(0, 0), (100, 0)], ["MAIN", "St", 101, 199, 100, 198, "O", "E", None, None])
    _road(lyr, [(100, 0), (200, 0)], ["MAIN", "St", 151, 249, 150, 248, "O", "E", None, None])
    assert fill_range_gaps(lyr, cfg) == 0
    assert _val(lyr, a, "ToAddr_L") == 199
    lyr, cfg = _layer()
    a = _road(lyr, [(0, 0), (100, 0)], ["MAIN", "St", 101, 199, 100, 198, "O", "E", None, None])
    _road(lyr, [(100, 0), (200, 0)], ["MAIN", "St", 301, 399, 300, 398, "O", "E", None, None])
    cfg.validation.gap_tolerance = 100
    assert fill_range_gaps(lyr, cfg) == 0
    assert _val(lyr, a, "ToAddr_L") == 199


def test_defaults_fill_empty_fields_only():
    lyr, cfg = _layer()
    kept = _road(lyr, [(0, 0), (10, 0)], ["MAIN", "St", 101, 199, 100, 198, "E", "E", None, "Nelson"])
    empty = _road(lyr, [(10, 0), (20, 0)], ["MAIN", "St", 201, 299, 200, 298, None, "E", None, None])
    cfg.defaults = {"State_L": "KY", "County_L": "Hardin", "Parity_L": "O"}
    assert fill_empty_defaults(lyr, cfg) == 2
    assert (_val(lyr, kept, "State_L"), _val(lyr, kept, "County_L"), _val(lyr, kept, "Parity_L")) == ("KY", "Nelson", "E")
    assert (_val(lyr, empty, "State_L"), _val(lyr, empty, "County_L"), _val(lyr, empty, "Parity_L")) == (
        "KY", "Hardin", "O")


def test_split_existing_crossing_and_leave_end_joins():
    lyr, cfg = _layer()
    _road(lyr, [(0, 0), (100, 0)], ["MAIN", "St", 101, 199, 100, 198, "O", "E", "IN", None])
    _road(lyr, [(100, 0), (200, 0)], ["MAIN", "St", 201, 299, 200, 298, "O", "E", "IN", None])
    _road(lyr, [(50, -40), (50, 40)], ["OAK", "Ave", 101, 199, 100, 198, "O", "E", None, None])
    cfg.defaults = {"State_L": "KY", "County_L": "Nelson"}
    rep = split_existing_intersections(lyr, cfg)
    assert rep["split"] == 2 and rep["new_pieces"] == 2
    main = sorted((f.geometry().asPolyline()[0].x(), fid) for fid, f in lyr._feats.items()
                  if f.attribute(0) == "MAIN" and f.geometry().asPolyline()[-1].x() <= 100)
    assert len(main) == 2
    west, east = [fid for _x, fid in main]
    assert (_val(lyr, west, "FromAddr_L"), _val(lyr, west, "ToAddr_L")) == (101, 149)
    assert (_val(lyr, east, "FromAddr_L"), _val(lyr, east, "ToAddr_L")) == (151, 199)
    assert _val(lyr, west, "State_L") == "IN" and _val(lyr, east, "State_L") == "IN"
    assert _val(lyr, west, "County_L") == "Nelson" and _val(lyr, east, "County_L") == "Nelson"
    oak = [f for f in lyr._feats.values() if f.attribute(0) == "OAK"]
    assert len(oak) == 2 and all(_val(lyr, f.id(), "State_L") == "KY" for f in oak)
    assert len([f for f in lyr._feats.values() if f.attribute(0) == "MAIN"]) == 3


def test_crossing_option_off_splits_a_tee_only():
    lyr, cfg = _layer()
    cfg.edit.split_at_crossings = False
    _road(lyr, [(0, 0), (100, 0)], ["MAIN", "St", 101, 199, 100, 198, "O", "E", None, None])
    _road(lyr, [(40, 30), (40, -30)], ["OAK", "Ave", 101, 199, 100, 198, "O", "E", None, None])
    assert split_existing_intersections(lyr, cfg)["split"] == 0
    _road(lyr, [(60, 30), (60, 0)], ["ELM", "St", 101, 199, 100, 198, "O", "E", None, None])
    rep = split_existing_intersections(lyr, cfg)
    assert rep["split"] == 1
    assert len([f for f in lyr._feats.values() if f.attribute(0) == "MAIN"]) == 2
    assert len([f for f in lyr._feats.values() if f.attribute(0) == "ELM"]) == 1


def test_msag_compare_reports_range_parity_and_rows_missing_from_the_map():
    lyr, cfg = _layer()
    lyr.addAttribute(QgsField("MSAGComm_L", "string"))
    lyr.addAttribute(QgsField("MSAGComm_R", "string"))
    cfg.mapping["MSAGComm_L"] = "MSAGComm_L"
    cfg.mapping["MSAGComm_R"] = "MSAGComm_R"
    fid = _road(lyr, [(0, 0), (100, 0)], ["MAIN", "St", 101, 199, 100, 198, "O", "E", None, None])
    lyr.changeAttributeValues(fid, {lyr.fields().lookupField("MSAGComm_L"): "TOWN A",
                                    lyr.fields().lookupField("MSAGComm_R"): "TOWN A"})
    msag = QgsVectorLayer("x", "msag")
    for name in ("STREET", "COMM", "LOW", "HIGH", "OE"):
        msag.addAttribute(QgsField(name, "string"))
    QgsProject.instance().addMapLayer(msag)
    msag.add_committed(["MAIN ST", "TOWN A", "101", "199", "O"], None)
    msag.add_committed(["MAIN ST", "TOWN A", "100", "150", "E"], None)
    msag.add_committed(["ELM ST", "TOWN A", "1", "10", "O"], None)
    settings = MsagSettings(layer_id=msag.id(), street_field="STREET", community_field="COMM",
                            low_field="LOW", high_field="HIGH", parity_field="OE")
    codes = {i.code for i in compare_msag(lyr, cfg, settings)}
    assert "MSAG_RANGE" in codes
    assert "MSAG_NOT_IN_GIS" in codes
    assert "MSAG_MISSING" not in codes and "MSAG_PARITY" not in codes
