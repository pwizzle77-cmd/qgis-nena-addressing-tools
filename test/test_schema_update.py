# -*- coding: utf-8 -*-
"""Pure tests for Update schema's field comparison."""

import os
import sys

_PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PARENT = os.path.dirname(_PLUGIN)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from nena_addressing_and_rcl_tools.core.addressing.ssap import ACCESS_FIELDS, SSAP_FIELDS  # noqa: E402
from nena_addressing_and_rcl_tools.core.schema_update import (PLUGIN_ADDRESS, compare_fields,  # noqa: E402
                                                              container_of)


def test_present_by_name_case_alias_or_mapping():
    names = [d.name for d in compare_fields(ACCESS_FIELDS, ["accesspointid", "RCL_NGUID"], ["StreetID"])]
    assert "AccessPointID" not in names and "RCL_NGUID" not in names and "StreetID" not in names
    assert "RoadSide" in names and "Notes" in names


def test_ticks_mandatory_and_plugin_fields_only():
    found = {f.name: f for f in compare_fields(SSAP_FIELDS, ["Add_Number", "St_Name"], (), PLUGIN_ADDRESS)}
    assert found["AccessPointID"].checked and found["Site_NGUID"].checked
    optional_nena = [f for f in found.values() if not f.required and f.name not in PLUGIN_ADDRESS]
    assert optional_nena and not any(f.checked for f in optional_nena)
    assert all(f.checked for f in compare_fields(ACCESS_FIELDS, [], tick_all=True))


def test_shapefile_names_over_ten_characters_are_not_ticked():
    found = {f.name: f for f in compare_fields(ACCESS_FIELDS, [], tick_all=True, shapefile=True)}
    assert not found["AccessPointID"].checked and "shapefile" in found["AccessPointID"].note
    assert found["RoadSide"].checked


class _Layer:
    def __init__(self, provider, source):
        self._p, self._s = provider, source

    def providerType(self):
        return self._p

    def source(self):
        return self._s


def test_container_of():
    assert container_of(_Layer("ogr", "C:/data/nena.gpkg|layername=RoadCenterline")) == ("C:/data/nena.gpkg", "gpkg")
    assert container_of(_Layer("ogr", "C:/data/nena.gdb|layername=RCL")) == ("C:/data/nena.gdb", "gdb")
    assert container_of(_Layer("ogr", "C:/data/roads.shp")) == ("", "")
    assert container_of(_Layer("mssql", "dbname=x")) == ("", "")
    assert container_of(None) == ("", "")
