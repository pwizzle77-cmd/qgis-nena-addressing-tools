# -*- coding: utf-8 -*-
"""Submission export rules, retire, and the current-feature filter."""

from datetime import date

import pytest

import qgis.core  # noqa: E402

if "fake_qgis" not in (getattr(qgis.core, "__file__", "") or ""):
    pytest.skip("real QGIS available - fake-QGIS tests skipped", allow_module_level=True)

from qgis.core import QgsField, QgsVectorLayer  # noqa: E402

from nena_addressing_and_rcl_tools.core.config import PluginConfig  # noqa: E402
from nena_addressing_and_rcl_tools.core.addressing.checks import lonlat_metres  # noqa: E402
from nena_addressing_and_rcl_tools.core.addressing.engine import resolve_street  # noqa: E402
from nena_addressing_and_rcl_tools.core.retire import current_subset, is_current, retire_layer  # noqa: E402
from nena_addressing_and_rcl_tools.core.submit import (ADDRESS_OMIT, kept_fields,  # noqa: E402
                                                       submission_target)


def test_current_rows_and_the_filter_text():
    today = date(2026, 11, 23)
    assert is_current("ACT", None, today)
    assert is_current("", None, today)
    assert not is_current("RET", None, today)
    assert not is_current("ACT", "2020-01-01", today)
    assert is_current("ACT", "2027-01-01", today)
    ogr = current_subset("ogr", "RoadStatus")
    assert "date('now')" in ogr and "RoadStatus" in ogr and "RET" in ogr
    assert "CURRENT_TIMESTAMP" in current_subset("postgres", "")
    assert "GETDATE()" in current_subset("mssql", "AddressStatus")


def test_submission_omits_plugin_links_and_falls_back_without_a_gdb_writer(tmp_path):
    names = ["St_Name", "StreetID", "FullName", "RoadStatus", "Overpass", "FromAddr_L"]
    assert kept_fields("centerline", names) == ["St_Name", "FullName", "FromAddr_L"]
    assert "Access_X" in ADDRESS_OMIT and "LandmkName" not in ADDRESS_OMIT
    path, driver, note = submission_target(str(tmp_path / "out.gdb"))
    from nena_addressing_and_rcl_tools.core.tables import file_gdb_driver
    if file_gdb_driver():
        assert driver in ("OpenFileGDB", "FileGDB") and note == ""
    else:
        assert driver == "GPKG" and path.endswith(".gpkg") and "GeoPackage" in note


def test_retire_sets_status_and_expire():
    lyr = QgsVectorLayer("LineString", "rcl")
    lyr.addAttribute(QgsField("RoadStatus", "string"))
    lyr.addAttribute(QgsField("Expire", "string"))
    fid = lyr.add_committed(["ACT", None], [(0, 0), (1, 0)])
    lyr.selectByIds([fid])
    cfg = PluginConfig(mapping={"RoadStatus": "RoadStatus", "Expire": "Expire"})
    assert retire_layer(lyr, cfg, "RoadStatus") == 1
    status = lyr._feats[fid].attribute(0)
    expire = lyr._feats[fid].attribute(1)
    assert getattr(status, "v", status) == "RET"
    assert str(getattr(expire, "v", expire)).startswith("20")


def test_alias_resolves_one_hop():
    class Rec:
        def __init__(self, street_id, parts, alias_of=""):
            self.street_id, self.parts, self.alias_of = street_id, parts, alias_of

    live = Rec("S1", {"St_Name": "Main", "St_PosTyp": "Street"})
    old = Rec("S9", {"St_Name": "Old", "St_PosTyp": "Street"}, "S1")
    by_id = {"S1": live, "S9": old}
    assert resolve_street(by_id, "S9", old.parts)[0] == "S1"
    assert resolve_street(by_id, "", old.parts)[0] == "S1"
    assert resolve_street(by_id, "S1", live.parts)[0] == "S1"
    # a loop stays where it is
    loop = Rec("A", {"St_Name": "Loop"}, "A")
    assert resolve_street({"A": loop}, "A", loop.parts)[0] == "A"


def test_two_metres_is_the_latlong_gap():
    assert lonlat_metres(-82.0, 30.0, -82.0, 30.0) == 0
    assert lonlat_metres(-82.0, 30.0, -82.01, 30.0) > 2
