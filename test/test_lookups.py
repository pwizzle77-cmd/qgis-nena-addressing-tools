# -*- coding: utf-8 -*-
"""Lookup tables, access-point referential rules, and the extra schema layers."""

from nena_addressing_and_rcl_tools.core.access_rules import blocked_deletes, deletion_message, nearest_access
from nena_addressing_and_rcl_tools.core.lookups import (LOOKUP_SPECS, active_choices, builtin_unit_types, seeds_for)
from nena_addressing_and_rcl_tools.core.project_config import ProjectConfig
from nena_addressing_and_rcl_tools.core.schema_pack import CREATABLE_LAYERS, SCHEMA_LAYERS, address_defs, defs_for
from nena_addressing_and_rcl_tools.core.streets.street_types import OFFICIAL_TYPES


def _labels(key):
    return {row["Label"] for row in seeds_for(key)}


def _codes(key):
    return [row["Code"] for row in seeds_for(key)]


def test_schema_keeps_the_original_four_layers_and_adds_the_new_ones():
    assert [spec.key for spec in SCHEMA_LAYERS] == ["centerline", "streets", "address", "driveway"]
    keys = [spec.key for spec in CREATABLE_LAYERS]
    assert keys[:4] == ["centerline", "streets", "address", "driveway"]
    assert "access" in keys
    for key, _table, _title, _role in LOOKUP_SPECS:
        assert key in keys
        assert defs_for(key)[0].name == "Code"
    assert any(spec.key == "access" and spec.wkb == "Point" and spec.versioned for spec in CREATABLE_LAYERS)
    assert all(not spec.versioned for spec in CREATABLE_LAYERS if spec.key == "road_type")


def test_address_template_keeps_the_access_link():
    names = [item.name for item in address_defs({"fields": [{"name": "Add_Number", "type": "int"}]})]
    for link in ("AccessPointID", "UnitType", "AddressStatus", "AddressType"):
        assert link in names
    assert "AccessPointID" in [item.name for item in defs_for("access")]
    assert "AccessPointID" in [item.name for item in defs_for("driveway")]


def test_lookup_seeds_cover_the_everyday_values():
    road_labels = _labels("road_type")
    for label in ("Court", "Lane", "Glen", "Way", "Circle", "Place", "Terrace", "Trail",
                  "Parkway", "Boulevard", "Avenue", "Street", "Road", "Drive"):
        assert label in road_labels
    assert len(_codes("road_type")) == len(set(code.upper() for code in _codes("road_type")))
    assert len(seeds_for("road_type")) == len(OFFICIAL_TYPES)
    units = _labels("unit_type")
    for label in ("Apt", "Unit", "Suite", "Room", "Floor", "Bldg", "Building", "Space", "Lot"):
        assert label in units
    assert {"Active", "Proposed", "Retired", "Temporary", "Under Construction"} <= _labels("address_status")
    assert {"Active", "Proposed", "Retired", "Private", "Seasonal"} <= _labels("road_status")
    assert {"Building", "Residence", "Business", "Utility", "Pole", "Meter", "Hydrant", "Other"} <= _labels("address_type")
    assert active_choices(seeds_for("unit_type"))[0][0] == "APT"
    retired = list(seeds_for("unit_type"))
    retired[0] = dict(retired[0], Active="N")
    assert all(code != "APT" for code, _label in active_choices(retired))
    assert builtin_unit_types()[0] == ("APT", "Apt")


def test_old_project_json_still_loads_without_lookup_layers():
    cfg = ProjectConfig.from_dict({"addresses": {"create_driveway": True}})
    assert cfg.addresses.create_driveway is True
    assert cfg.addresses.access_layer_id == ""
    assert cfg.lookups.road_type_layer_id == ""
    assert cfg.archive.access_layer_id == ""
    again = ProjectConfig.from_json(cfg.to_json())
    again.lookups.unit_type_layer_id = "unit-1"
    restored = ProjectConfig.from_json(again.to_json())
    assert restored.lookups.unit_type_layer_id == "unit-1"
    assert restored.addresses.create_driveway is True


def test_nearest_access_point_wins_inside_the_tolerance():
    points = [("a", 0, 0), ("b", 5, 0), ("", 0.1, 0)]
    assert nearest_access(points, 0.4, 0, 1) == "a"
    assert nearest_access(points, 4.6, 0, 1) == "b"
    assert nearest_access(points, 3, 0, 1) == ""


def test_an_access_point_in_use_cannot_be_deleted():
    blocked = blocked_deletes(["ap-1", "ap-2", "  "], ["ap-1", "ap-1"], ["ap-2"])
    assert blocked == [
        {"id": "ap-1", "addresses": 2, "driveways": 0},
        {"id": "ap-2", "addresses": 0, "driveways": 1},
    ]
    text = deletion_message(blocked)
    assert "Cannot delete these access points" in text
    assert "2 address points" in text
    assert "1 driveway" in text
    assert "undone" in text
    assert blocked_deletes(["ap-1"], []) == []
    assert deletion_message([]) == ""
    one = deletion_message(blocked_deletes(["ap-9"], ["ap-9"]))
    assert one.startswith("Cannot delete this access point.")
    assert "1 address point" in one


# ---------------------------------------------------------------------------
# Drop-down coverage, domain list, and field matching
# ---------------------------------------------------------------------------
from nena_addressing_and_rcl_tools.core.lookups import (BINDINGS, DOMAIN_NAMES, DOMAIN_PARENT, DOMAINS_KEY, FIXED_LISTS,
                                         LOOKUP_KEYS, VALUE_MAP_NULL, bindings_for, lookup_field_defs,
                                         match_lookup_table, new_domain_rows, prefix_from_table,
                                         resolve_fields, rows_in_domain, value_map_config,
                                         value_relation_filter)
from nena_addressing_and_rcl_tools.core.addressing.ssap import SSAP_FIELDS
from nena_addressing_and_rcl_tools.core.schema import RCL_FIELDS


def test_every_binding_points_at_a_real_list_and_a_real_nena_field():
    rcl = {d.name for d in RCL_FIELDS}
    ssap = {d.name for d in SSAP_FIELDS}
    for b in BINDINGS:
        if b.kind == "map":
            assert b.source in FIXED_LISTS
        elif b.kind == "domain":
            assert b.source in DOMAIN_NAMES
        else:
            assert b.source in LOOKUP_KEYS
        if b.role == "centerline":
            assert b.field in rcl, b.field
            if b.parent_field:
                assert b.parent_field in rcl
        if b.role == "address":
            assert b.field in ssap, b.field
            if b.parent_field:
                assert b.parent_field in ssap


def test_both_sides_of_a_centerline_get_drop_downs():
    fields = {b.field for b in bindings_for("centerline")}
    for base in ("Parity", "Valid", "Country", "State", "County", "IncMuni", "UnincCom", "NbrhdCom",
                 "PostCode", "PostComm", "ESN", "MSAGComm"):
        assert base + "_L" in fields and base + "_R" in fields
    for name in ("St_PreDir", "St_PosDir", "St_PreTyp", "St_PosTyp", "OneWay", "RoadClass", "RoadStatus"):
        assert name in fields
    post_comm = [b for b in bindings_for("centerline", source="PostComm")]
    assert {b.parent_field for b in post_comm} == {"PostCode_L", "PostCode_R"}
    assert DOMAIN_PARENT["PostComm"] == "PostCode"


def test_address_points_get_the_local_lists():
    sources = {b.field: b.source for b in bindings_for("address")}
    assert sources["County"] == "County"
    assert sources["Post_Comm"] == "PostComm"
    assert sources["Placement"] == "Placement"
    assert sources["St_PreDir"] == "directional"
    assert sources["State"] == "state"


def test_domain_table_has_code_first_and_the_domain_columns():
    names = [d.name for d in lookup_field_defs(DOMAINS_KEY)]
    assert names[0] == "Code"
    assert "Domain" in names and "Parent" in names
    assert "Domain" not in [d.name for d in lookup_field_defs("unit_type")]
    seeds = seeds_for(DOMAINS_KEY)
    assert {r["Label"] for r in rows_in_domain(seeds, "RoadClass")} >= {"Primary", "Local", "Ramp"}
    assert {r["Label"] for r in rows_in_domain(seeds, "Placement")} >= {"Structure", "Site"}
    assert rows_in_domain(seeds, "County") == []


def test_new_domain_rows_skip_known_values_and_keep_the_parent():
    existing = [{"Domain": "PostComm", "Code": "Springfield", "SortOrder": 30}]
    rows = new_domain_rows("PostComm", [("springfield", "49001"), ("Shelbyville", "49002"), ("", "x"),
                                        ("Shelbyville", "49002")], existing)
    assert [(r["Code"], r["Parent"], r["SortOrder"]) for r in rows] == [("Shelbyville", "49002", 40)]
    plain = new_domain_rows("County", ["Wayne", " wayne ", None], [])
    assert [r["Code"] for r in plain] == ["Wayne"]
    assert plain[0]["Active"] == "Y" and plain[0]["Domain"] == "County"


def test_value_map_keeps_order_and_offers_no_value():
    config = value_map_config(FIXED_LISTS["parity"][1])
    assert config["map"][0] == {"": VALUE_MAP_NULL}
    assert [list(item.values())[0] for item in config["map"][1:]] == ["O", "E", "B", "Z"]
    assert len(FIXED_LISTS["state"][1]) >= 51


def test_value_relation_filter_narrows_by_parent_only_when_it_is_set():
    plain = value_relation_filter()
    assert "Active" in plain and "Domain" not in plain
    text = value_relation_filter("PostComm", "ZIP_L")
    assert "\"Domain\" = 'PostComm'" in text
    assert "current_value('ZIP_L') IS NULL" in text
    assert "\"Parent\" = current_value('ZIP_L')" in text
    assert "O''Brien" in value_relation_filter("O'Brien")


def test_fields_are_found_by_mapping_name_and_alias():
    names = ["PRETYPE", "SUFFIX", "zip_l", "MyCounty", "St_PreDir"]
    found = resolve_fields(names, RCL_FIELDS, {"County_L": "mycounty", "ESN_L": "gone"})
    assert found["County_L"] == "MyCounty"
    assert found["St_PreTyp"] == "PRETYPE"
    assert found["PostCode_L"] == "zip_l"
    assert found["St_PreDir"] == "St_PreDir"
    assert "ESN_L" not in found


def test_lookup_tables_are_found_with_a_prefix_or_by_layer_name():
    candidates = [("Street names", "StreetNames"), ("Unit types", "Wayne_UnitType"), ("RoadType", "")]
    assert match_lookup_table("UnitType", candidates) == 1
    assert match_lookup_table("RoadType", candidates) == 2
    assert match_lookup_table("AddressStatus", candidates) == -1
    assert match_lookup_table("NENA_Domains", [("x", "gis.NENA_Domains")]) == 0


def test_prefix_comes_from_the_address_table_name():
    assert prefix_from_table("Wayne_SiteStructureAddressPoint") == "Wayne"
    assert prefix_from_table("SiteStructureAddressPoint") == ""
    assert prefix_from_table("addresses") == ""
