# -*- coding: utf-8 -*-
"""Pure tests for zone settings and check exceptions."""

import os
import sys
from datetime import datetime

_PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PARENT = os.path.dirname(_PLUGIN)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from nena_addressing_and_rcl_tools.core import check_exceptions as X  # noqa: E402
from nena_addressing_and_rcl_tools.core import zones as Z  # noqa: E402
from nena_addressing_and_rcl_tools.core.project_config import ProjectConfig  # noqa: E402
from nena_addressing_and_rcl_tools.core.schema_pack import CREATABLE_LAYERS, defs_for  # noqa: E402


def test_new_zone_splits_roads_by_default_and_round_trips():
    zone = Z.new_zone("L1", "src", "fire")
    assert zone.split_roads is True and zone.value_field == "DsplayName"
    assert zone.check_on("roads") and zone.check_on("gaps") and zone.check_on("addresses")
    zone.split_roads = False
    assert not zone.check_on("roads")            # no split, so no "not split here" results
    zone.checks["gaps"] = False
    again = Z.ZoneLayer.from_dict(zone.to_dict())
    assert again == zone
    assert not Z.new_zone("L2", "", "nbrhd").check_on("gaps")     # neighborhoods need not cover the county
    assert Z.new_zone("L3", "", "bogus").kind == "other_esb"


def test_zones_are_kept_in_the_project_config():
    cfg = ProjectConfig()
    cfg.zones.layers = Z.dump_zones([Z.new_zone("L1", "src", "ems")])
    cfg.zones.exceptions_layer_id = "X1"
    back = ProjectConfig.from_json(cfg.to_json())
    assert Z.load_zones(back.zones.layers)[0].kind == "ems"
    assert back.zones.exceptions_layer_id == "X1"
    assert ProjectConfig.from_json("{}").zones.layers == []


def test_urns_old_and_new_forms():
    assert Z.urn_matches("fire", "urn:emergency:service:responder.fire")
    assert Z.urn_matches("fire", "urn:nena:service:responder.fire")
    assert Z.urn_matches("law", "urn:emergency:service:responder.police.sheriff")
    assert Z.urn_matches("psap", "urn:service:sos")
    assert not Z.urn_matches("fire", "urn:emergency:service:responder.ems")
    assert not Z.urn_matches("ems", "responder.ems")
    assert not Z.urn_matches("ems", "")
    assert Z.urn_matches("county", "anything")                      # no URN for this kind


def test_guess_kind_and_field_defs():
    assert Z.guess_kind("Fire_Districts") == "fire"
    assert Z.guess_kind("Sheriff Zones") == "law"
    assert Z.guess_kind("City Limits") == "incmuni"
    assert Z.guess_kind("Unincorporated Communities") == "uninc"
    assert Z.guess_kind("ProvisioningBoundary") == "provisioning"
    assert Z.guess_kind("roads") == ""
    names = [d.name for d in Z.zone_field_defs("fire")]
    assert "ES_NGUID" in names and "ServiceURN" in names and "DsplayName" in names
    assert "PB_NGUID" in [d.name for d in Z.zone_field_defs("provisioning")]
    keys = [spec.key for spec in CREATABLE_LAYERS]
    assert "zone_fire" in keys and "exceptions" in keys
    zone_specs = [spec for spec in CREATABLE_LAYERS if spec.key.startswith("zone_")]
    assert all(spec.wkb == "MultiPolygon" and not spec.default_on for spec in zone_specs)
    assert defs_for("exceptions")[0].name == "ExceptionID"


def test_esz_conflicts():
    found = Z.esz_conflicts({"E1": {"fire": {"FD1"}, "law": {"SO", "PD"}}, "E2": {"ems": {"A", None}}})
    assert found == [("E1", "law", ["PD", "SO"])]


def test_exception_matching_expiry_and_edits():
    now = datetime(2026, 11, 19, 10, 0)
    exc = X.new_exception("ZONE_ROAD_NOT_SPLIT", "centerline", "R1", "Bridge", zone_kind="fire",
                          feature_date="2026-11-01 08:00:00", now=now)
    excs = X.ExceptionSet([exc])
    assert excs.status("ZONE_ROAD_NOT_SPLIT", "centerline", "R1", "fire", "2026-11-01 08:00:00", now)[0] == X.EXCUSED
    assert excs.status("ZONE_ROAD_NOT_SPLIT", "centerline", "R1", "law", None, now)[0] is None     # other zone
    assert excs.status("ROAD_OUTSIDE_PROVISIONING", "centerline", "R1", "", None, now)[0] is None   # other check
    assert excs.status("ZONE_ROAD_NOT_SPLIT", "centerline", "R1", "fire", datetime(2026, 11, 5), now)[0] == X.STALE

    every = X.new_exception("ADDR_ZONE_ACCESS", "address", "A1", "Long driveway", expires="2026-12-31", now=now)
    excs.add(every)
    assert excs.status("ADDR_ZONE_ACCESS", "address", "A1", "ems", None, now)[0] == X.EXCUSED   # any zone
    assert excs.status("ADDR_ZONE_ACCESS", "address", "A1", "ems", None, datetime(2027, 1, 2))[0] is None

    row = X.from_values({k.lower(): v for k, v in every.to_values().items()}, fid=7)
    assert row.key == "A1" and row.fid == 7 and row.expires == datetime(2026, 12, 31)
    assert X.from_values({"CheckCode": "X"}) is None
    try:
        X.new_exception("X", "address", "A1", "  ")
    except ValueError:
        pass
    else:
        raise AssertionError("a reason is required")
