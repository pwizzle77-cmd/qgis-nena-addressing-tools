# -*- coding: utf-8 -*-
"""Pure tests for calculated fields, new lookup lists, and the PSAP side rule."""

import os
import sys

_PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PARENT = os.path.dirname(_PLUGIN)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from nena_addressing_and_rcl_tools.core import derived as D  # noqa: E402
from nena_addressing_and_rcl_tools.core import lookups as L  # noqa: E402
from nena_addressing_and_rcl_tools.core import zones as Z  # noqa: E402
from nena_addressing_and_rcl_tools.core.project_config import ProjectConfig  # noqa: E402
from nena_addressing_and_rcl_tools.core.schema import FIELDS_BY_NAME, auto_map  # noqa: E402


def test_new_centerline_fields_and_aliases():
    for name in ("PSAP_L", "PSAP_R", "Hierarchy", "FullName", "LSt_PreDir", "LSt_Name", "LSt_Typ",
                 "LSt_PosDir", "LSt_FullName", "TravelTime", "Surface"):
        assert name in FIELDS_BY_NAME
    found = auto_map(["PSAP_LEFT", "PSAP_RIGHT", "FULL_NAME", "LEGACY_FULLNAME", "TRAVEL_TIME", "SURFACE",
                      "ROUTE_HIER"])
    assert found["PSAP_L"] == "PSAP_LEFT" and found["PSAP_R"] == "PSAP_RIGHT"
    assert found["FullName"] == "FULL_NAME" and found["LSt_FullName"] == "LEGACY_FULLNAME"
    assert found["TravelTime"] == "TRAVEL_TIME" and found["Surface"] == "SURFACE" and found["Hierarchy"] == "ROUTE_HIER"


def test_full_names():
    parts = {"St_PreDir": "North", "St_Name": "Main", "St_PosTyp": "Street"}
    assert D.road_full_name(parts) == "North Main Street"
    assert D.legacy_full_name({"LSt_PreDir": "N", "LSt_Name": "MAIN", "LSt_Typ": "ST"}) == "N MAIN ST"


def test_full_address():
    base = {"Add_Number": 123, "St_PreDir": "North", "St_Name": "Main", "St_PosTyp": "Street"}
    assert D.full_address(base) == "123 North Main Street"
    assert D.full_address(dict(base, AddNum_Suf="A", Unit="4", UnitType="Apt")) == "123A North Main Street Apt 4"
    assert D.full_address(dict(base, AddNum_Suf="1/2")) == "123 1/2 North Main Street"
    assert D.full_address(dict(base, AddNum_Pre="N", Add_Number=1234.0)) == "N1234 North Main Street"
    assert D.full_address(dict(base, Unit="Apt 4", UnitType="Apt")) == "123 North Main Street Apt 4"
    assert D.full_address(base, "Elm Road") == "123 Elm Road"
    bare = {"St_Name": "Main", "St_PosTyp": "Street", "LandmkName": "City Hall"}
    assert D.full_address(bare) == "City Hall Main Street"
    assert D.full_address({"Mile_Post": "12.4", "St_Name": "Main", "St_PosTyp": "Street"}) == "Milepost 12.4 Main Street"


def test_travel_time_and_hierarchy():
    assert D.travel_minutes(1609.344, 60) == 1.0
    assert D.travel_minutes(1609.344, 30) == 2.0
    assert D.travel_minutes(100, None) is None and D.travel_minutes(100, 0) is None
    assert D.hierarchy_for("Local") == 6 and D.hierarchy_for("primary") == 2
    assert D.hierarchy_for("Walkway") is None and D.hierarchy_for("") is None


def test_centerline_changes_only_what_differs():
    values = {"St_Name": "Main", "St_PosTyp": "Street", "FullName": "Main Street", "SpeedLimit": 30,
              "TravelTime": None, "RoadClass": "Local", "Hierarchy": None, "LSt_Name": "MAIN", "LSt_Typ": "ST"}
    have = ["FullName", "LSt_FullName", "TravelTime", "Hierarchy"]
    out = D.centerline_changes(values, 1609.344, have)
    assert out == {"LSt_FullName": "MAIN ST", "TravelTime": 2.0, "Hierarchy": 6}
    kept = D.centerline_changes(dict(values, Hierarchy=3), 1609.344, have)
    assert "Hierarchy" not in kept                       # a hierarchy someone set is kept
    no_speed = D.centerline_changes(dict(values, SpeedLimit=None, TravelTime=1.5), 100, have)
    assert no_speed["TravelTime"] is None                # no speed, no guessed travel time


def test_address_changes():
    values = {"Add_Number": 5, "St_Name": "Elm", "St_PosTyp": "Road", "FullName": "Elm Rd", "FullAddr": ""}
    out = D.address_changes(values, ["FullName", "FullAddr"])
    assert out == {"FullName": "Elm Road", "FullAddr": "5 Elm Road"}


def test_surface_and_hierarchy_lists():
    rows = [r for r in L.domain_seeds() if r["Domain"] == "Surface"]
    assert [r["Code"] for r in rows][:2] == ["Paved", "Concrete"] and "Shell" in [r["Code"] for r in rows]
    assert [r["Code"] for r in L.seeds_for(L.DOMAINS_KEY) if r.get("IsDefault") == "Y" and r["Domain"] == "Surface"] \
        == ["Paved"]
    assert L.can_default("Surface")
    assert any(b.field == "Surface" and b.source == "Surface" for b in L.bindings_for("centerline"))
    assert any(b.field == "Hierarchy" and b.kind == "map" for b in L.bindings_for("centerline"))
    later = L.later_domain_rows([{"Domain": "RoadClass", "Code": "Local"}])
    assert {r["Domain"] for r in later} == {"Surface"}
    assert L.later_domain_rows([{"Domain": "Surface", "Code": "Paved"}]) == []


def test_psap_sides_come_from_the_psap_zone_layer():
    cfg = ProjectConfig()
    cfg.addresses.attribution_rules = [{"layer_id": "Z", "source_field": "ZIP5", "target": "PostCode"}]
    assert [r["target"] for r in Z.side_rules(cfg)] == ["PostCode"]
    cfg.zones.layers = Z.dump_zones([Z.new_zone("P1", "src", "psap", "DsplayName")])
    rules = Z.side_rules(cfg)
    assert rules[-1] == {"layer_id": "P1", "layer_source": "src", "source_field": "DsplayName", "target": "PSAP",
                         "optional": True}
    cfg.addresses.attribution_rules.append({"layer_id": "X", "source_field": "NAME", "target": "PSAP"})
    assert [r["layer_id"] for r in Z.side_rules(cfg) if r["target"] == "PSAP"] == ["X"]   # a Sides rule wins
