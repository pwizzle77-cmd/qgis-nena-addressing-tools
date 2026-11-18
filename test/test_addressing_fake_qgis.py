# -*- coding: utf-8 -*-
"""Addressing engine against the fake QGIS (see test_engines_fake_qgis.py)."""

import csv

import pytest

import qgis.core  # noqa: E402

if "fake_qgis" not in (getattr(qgis.core, "__file__", "") or ""):
    pytest.skip("real QGIS available - fake-QGIS tests skipped", allow_module_level=True)


from qgis.core import QgsField, QgsGeometry, QgsPointXY, QgsProject, QgsVectorLayer  # noqa: E402

from nena_addressing_and_rcl_tools.core import config as C  # noqa: E402
from nena_addressing_and_rcl_tools.core.addressing import engine as E  # noqa: E402
from nena_addressing_and_rcl_tools.core.addressing.checks import address_issues, centerline_boundary_issues  # noqa: E402
from nena_addressing_and_rcl_tools.core.addressing.reports import addresses_by_community_report, streets_report  # noqa: E402
from nena_addressing_and_rcl_tools.core.config import PluginConfig  # noqa: E402
from nena_addressing_and_rcl_tools.core.layer_access import ConfigError, FieldAccessor  # noqa: E402
from nena_addressing_and_rcl_tools.core.project_config import ProjectConfig, remember, save_project_config  # noqa: E402
from nena_addressing_and_rcl_tools.core.streets.repository import MASTER_SCHEMA  # noqa: E402
from nena_addressing_and_rcl_tools.core.streets.service import StreetService  # noqa: E402

RCL = ["St_Name", "St_PosTyp", "StreetID", "RCL_NGUID", "FromAddr_L", "ToAddr_L", "FromAddr_R", "ToAddr_R",
       "Parity_L", "Parity_R", "MSAGComm_L", "MSAGComm_R"]
ADDR = ["Add_Number", "AddNum_Suf", "St_Name", "St_PosTyp", "StreetID", "RCL_NGUID", "Access_X", "Access_Y",
        "Addr_Side", "Site_NGUID", "Unit", "MSAGComm", "Post_Code", "Placement", "DateUpdate", "Longitude",
        "Latitude"]
DW = ["DW_NGUID", "Site_NGUID", "RCL_NGUID", "StreetID", "Parent_DW", "DateUpdate"]
TYPES = {"Add_Number": "int", "FromAddr_L": "int", "ToAddr_L": "int", "FromAddr_R": "int", "ToAddr_R": "int",
         "Access_X": "double", "Access_Y": "double", "Longitude": "double", "Latitude": "double",
         "DateUpdate": "datetime"}


def _layer(name, fields):
    lyr = QgsVectorLayer("x", name)
    for f in fields:
        lyr.addAttribute(QgsField(f, TYPES.get(f, "string")))
    QgsProject.instance().addMapLayer(lyr)
    return lyr


def _poly(lyr, attrs, x0, x1):
    fid = lyr.add_committed(attrs, None)
    lyr._feats[fid].setGeometry(QgsGeometry.fromPolygonXY([[QgsPointXY(x0, -500), QgsPointXY(x1, -500),
                                                            QgsPointXY(x1, 500), QgsPointXY(x0, 500)]]))
    lyr._committed[fid] = lyr._feats[fid]
    return fid


@pytest.fixture()
def world():
    rcl = _layer("rcl", RCL)
    rcl.add_committed(["Main", "St", "S1", "R1", 101, 199, 100, 198, "O", "E", "TOWN A", "TOWN A"], [(0, 0), (100, 0)])
    rcl.add_committed(["Main", "St", "S1", "R2", 201, 299, 200, 298, "O", "E", "TOWN A", "TOWN A"],
                      [(100, 0), (200, 0)])
    rcl.add_committed(["Oak", "Ave", "S2", "R3", 101, 199, 100, 198, "O", "E", "TOWN A", "TOWN A"],
                      [(100, 0), (100, 100)])
    master = _layer("streets", [n for n, _k, _l in MASTER_SCHEMA])
    for sid, name, typ in (("S1", "Main", "St"), ("S2", "Oak", "Ave")):
        vals = {"StreetID": sid, "St_Name": name, "St_PosTyp": typ, "FullName": f"{name} {typ}", "Status": "Active"}
        master.add_committed([vals.get(n) for n, _k, _l in MASTER_SCHEMA], None)
    addr = _layer("addr", ADDR)
    dw = _layer("dw", DW)
    msag = _layer("msag", ["NAME"])
    _poly(msag, ["TOWN A"], -500, 150)
    _poly(msag, ["TOWN B"], 150, 500)
    zips = _layer("zip", ["ZIP5"])
    _poly(zips, ["12345"], -500, 500)
    pcfg = ProjectConfig()
    remember(pcfg.streets, "master", master)
    remember(pcfg.streets, "centerline", rcl)
    remember(pcfg.streets, "address", addr)
    remember(pcfg.addresses, "driveway", dw)
    a = pcfg.addresses
    a.distance_per_number, a.distance_units, a.start_number = 1.0, "meters", 100
    a.attribution_rules = [{"layer_id": msag.id(), "source_field": "NAME", "target": "MSAGComm"},
                           {"layer_id": zips.id(), "source_field": "ZIP5", "target": "PostCode"}]
    save_project_config(pcfg)
    cfg = C.PluginConfig(mapping={f: f for f in RCL})
    cfg.edit.agency_id = "psap.example.gov"
    C.save_to_layer(rcl, cfg)
    return dict(rcl=rcl, addr=addr, dw=dw, master=master, pcfg=pcfg, cfg=cfg,
                ctx=E.AddressContext(pcfg, rcl, cfg))


def attr(lyr, fid, name):
    v = lyr._feats[fid].attribute(lyr.fields().lookupField(name))
    return getattr(v, "v", v) if hasattr(v, "isNull") else v


def P(x, y):
    return QgsPointXY(x, y)


def test_plan_distance_method_side_and_attribution(world):
    ctx = world["ctx"]
    plan = E.plan_address(ctx, P(50, 1), P(50, 20))          # snaps to (50,0) on R1, left side
    assert plan.segment_fid == 1 and plan.side == "L" and plan.street_id == "S1"
    assert plan.result.number in (149, 151) and plan.full_name == "Main St"
    assert plan.community == {"MSAGComm": "TOWN A", "PostCode": "12345"}
    right = E.plan_address(ctx, P(160, 0), P(160, -30))       # 160 m along Main St, right side
    # The centerline's right side says TOWN A, so the rooftop falling in TOWN B does not win.
    assert (right.side, right.result.number, right.community["MSAGComm"]) == ("R", 260, "TOWN A")
    assert right.community["PostCode"] == "12345"
    with pytest.raises(ConfigError):
        E.plan_address(ctx, P(50, 0), P(80, 0))               # rooftop on the road line


def test_commit_creates_address_and_driveway_then_undo(world):
    ctx, addr, dw = world["ctx"], world["addr"], world["dw"]
    plan = E.plan_address(ctx, P(50, 0), P(55, 30), vertices=[P(52, 15)])
    ids = E.commit_address(ctx, plan, plan.result.number, unit="", create_driveway=True)
    a = ids["address"]
    assert attr(addr, a, "Add_Number") == plan.result.number and attr(addr, a, "St_Name") == "Main"
    assert attr(addr, a, "FullName") == "Main St"
    assert attr(addr, a, "StreetID") == "S1" and attr(addr, a, "RCL_NGUID") == "R1"
    assert (attr(addr, a, "Access_X"), attr(addr, a, "Access_Y"), attr(addr, a, "Addr_Side")) == (50.0, 0.0, "L")
    assert attr(addr, a, "MSAGComm") == "TOWN A" and attr(addr, a, "Post_Code") == "12345"
    assert str(attr(addr, a, "Site_NGUID")).startswith("urn:emergency:uid:gis:SSAP:")
    line = dw._feats[ids["driveway"]].geometry().asPolyline()
    assert [(p.x(), p.y()) for p in line] == [(50, 0), (52, 15), (55, 30)]
    assert attr(dw, ids["driveway"], "Site_NGUID") == attr(addr, a, "Site_NGUID")
    assert E.undo_last_address() == 2 and not addr._feats and not dw._feats


def test_driveway_is_off_unless_asked_and_a_drawn_driveway_is_separate(world):
    ctx, dw = world["ctx"], world["dw"]
    assert ctx.pcfg.addresses.create_driveway is False
    plan = E.plan_address(ctx, P(10, 0), P(10, 20))
    ids = E.commit_address(ctx, plan, plan.result.number)
    assert "driveway" not in ids
    before = len(E.UNDO_STACK)
    with pytest.raises(ConfigError):
        E.commit_driveway(ctx, P(40, 0), [])
    fid = E.commit_driveway(ctx, P(40, 0), [P(48, 25)], segment_fid=1)
    assert len(E.UNDO_STACK) == before
    assert attr(dw, fid, "StreetID") == "S1"
    assert attr(dw, fid, "RCL_NGUID") == "R1"
    assert not attr(dw, fid, "Site_NGUID")
    line = dw._feats[fid].geometry().asPolyline()
    assert [(p.x(), p.y()) for p in line] == [(40, 0), (48, 25)]


def test_place_address_saves_an_access_point_ten_feet_off_the_road(world):
    """The access point sits 10 feet straight off the click, linked to the address and the road."""
    addr, rcl = world["addr"], world["rcl"]
    addr.addAttribute(QgsField("AccessPointID", "string"))
    access = _layer("access", ["AccessPointID", "RCL_NGUID", "StreetID", "RoadSide", "DateUpdate"])
    remember(world["pcfg"].addresses, "access", access)
    ctx = E.AddressContext(world["pcfg"], rcl, world["cfg"])
    off = 10 * 0.3048

    plan = E.plan_address(ctx, P(50, 0), P(55, 30))
    ids = E.commit_address(ctx, plan, plan.result.number)
    assert "driveway" not in ids and ids["access_created"] is True
    assert attr(addr, ids["address"], "AccessPointID") == ids["access"]
    assert attr(addr, ids["address"], "RCL_NGUID") == "R1"
    # The house number is still measured from the click on the road.
    assert (attr(addr, ids["address"], "Access_X"), attr(addr, ids["address"], "Access_Y")) == (50.0, 0.0)
    feat = next(f for f in access._feats.values() if _raw(f, access, "AccessPointID") == ids["access"])
    pt = feat.geometry().asPoint()
    assert pt.x() == pytest.approx(50)
    assert pt.y() == pytest.approx(off)
    assert _raw(feat, access, "RCL_NGUID") == "R1"
    assert _raw(feat, access, "RoadSide") == "L"
    assert _raw(feat, access, "StreetID") == "S1"

    again = E.plan_address(ctx, P(50, 0), P(52, 25))
    ids2 = E.commit_address(ctx, again, again.result.number + 2)
    assert ids2["access"] == ids["access"] and ids2["access_created"] is False
    assert len(access._feats) == 1

    right = E.plan_address(ctx, P(40, 0), P(40, -20))
    ids3 = E.commit_address(ctx, right, right.result.number)
    other = next(f for f in access._feats.values() if _raw(f, access, "AccessPointID") == ids3["access"])
    assert other.geometry().asPoint().y() == pytest.approx(-off)
    assert _raw(other, access, "RoadSide") == "R"
    assert attr(addr, ids3["address"], "AccessPointID") == ids3["access"]

    # Walking west, "straight out" on the left is south, and the point stays opposite the click.
    west = rcl.add_committed(["West", "Ln", "S9", "R9", 101, 199, 100, 198, "O", "E", "TOWN A", "TOWN A"],
                             [(80, 500), (0, 500)])
    south = E.plan_address(ctx, P(40, 500), P(30, 480), segment_fid=west)
    assert south.side == "L"
    ids4 = E.commit_address(ctx, south, south.result.number)
    seat = next(f for f in access._feats.values() if _raw(f, access, "AccessPointID") == ids4["access"])
    assert seat.geometry().asPoint().x() == pytest.approx(40)
    assert seat.geometry().asPoint().y() == pytest.approx(500 - off)
    assert _raw(seat, access, "RCL_NGUID") == "R9"
    assert attr(addr, ids4["address"], "RCL_NGUID") == "R9"


def _raw(feat, lyr, name):
    value = feat.attribute(lyr.fields().lookupField(name))
    return getattr(value, "v", value) if hasattr(value, "isNull") else value


def test_sql_server_float_keeps_fractional_coordinates():
    lyr = QgsVectorLayer("x", "floaty")
    lyr.addAttribute(QgsField("Access_X", "float"))
    lyr.addAttribute(QgsField("Add_Number", "int"))
    acc = FieldAccessor(lyr, PluginConfig(mapping={"Access_X": "Access_X", "Add_Number": "Add_Number"}))
    assert acc.coerce("Access_X", -82.650071) == pytest.approx(-82.650071)
    assert acc.coerce("Add_Number", 116.9) == 116


def test_whole_degree_access_does_not_number_from_the_end(world):
    ctx, addr = world["ctx"], world["addr"]
    far = E.plan_address(ctx, P(90, 0), P(90, 20))
    ids = E.commit_address(ctx, far, 190, create_driveway=False)
    feat = addr._feats[ids["address"]]
    feat.setAttribute(addr.fields().lookupField("Access_X"), -10000)
    feat.setAttribute(addr.fields().lookupField("Access_Y"), -10000)
    near = E.plan_address(ctx, P(10, 0), P(10, 20))
    assert 100 < near.result.number < 150


def test_neighbours_keep_order_and_collisions(world):
    ctx = world["ctx"]
    first = E.plan_address(ctx, P(50, 0), P(50, 20))
    E.commit_address(ctx, first, 151, create_driveway=False)
    # a house just before it whose raw number would be 151 too -> pushed below 151
    p = E.plan_address(ctx, P(49.5, 0), P(49.5, 20))
    assert p.result.number < 151
    # a house just after -> above 151
    p = E.plan_address(ctx, P(50.6, 0), P(50.6, 20))
    assert p.result.number > 151


def test_interpolation_and_frontage_methods(world):
    ctx, pcfg = world["ctx"], world["pcfg"]
    pcfg.addresses.method = "interpolate"
    p = E.plan_address(ctx, P(150, 0), P(150, 20))            # R2 left 201..299, halfway
    assert p.result.number in (249, 251)
    pcfg.addresses.method = "frontage"
    pcfg.addresses.frontage_interval, pcfg.addresses.distance_units = 20.0, "meters"
    p = E.plan_address(ctx, P(150, 0), P(150, -20))           # 50 m into R2 = 2 lots -> From_R 200 + 4 = 204
    assert p.result.number == 204


def test_overrides_per_community(world):
    ctx, pcfg, rcl = world["ctx"], world["pcfg"], world["rcl"]
    rcl.changeAttributeValues(2, {rcl.fields().lookupField("MSAGComm_R"): "TOWN B"})
    pcfg.addresses.overrides = {"TOWN B": {"method": "distance", "distance_per_number": 2.0,
                                           "distance_units": "meters", "start_number": 1000}}
    p = E.plan_address(ctx, P(160, 0), P(160, -30))           # TOWN B spacing on R2: From_R 200 + 60/2 = 230
    assert p.community["MSAGComm"] == "TOWN B" and p.result.number == 230


def test_segment_parity_zero_side_refused(world):
    rcl = world["rcl"]
    rcl.changeAttributeValues(1, {rcl.fields().lookupField("Parity_L"): "Z"})
    with pytest.raises(ConfigError):
        E.plan_address(world["ctx"], P(50, 0), P(50, 20))


def test_address_checks(world):
    ctx, addr = world["ctx"], world["addr"]
    ids = [E.commit_address(ctx, E.plan_address(ctx, P(x, 0), P(x, 20)), n, create_driveway=False)["address"]
           for x, n in ((20, 121), (60, 161), (80, 181))]
    idx = addr.fields().lookupField("Add_Number")
    addr.changeAttributeValues(ids[1], {idx: 150})          # even number on the odd side
    addr.changeAttributeValues(ids[2], {idx: 121})          # duplicate of the first + out of order
    issues = address_issues(ctx)
    codes = {(i.code, i.fid) for i in issues}
    assert ("ADDR_WRONG_SIDE", ids[1]) in codes
    assert ("ADDR_DUPLICATE", ids[0]) in codes and ("ADDR_DUPLICATE", ids[2]) in codes
    assert ("ADDR_OUT_OF_ORDER", ids[2]) in codes
    addr.changeAttributeValues(ids[0], {idx: 555})
    assert ("ADDR_OUTSIDE_RANGE", ids[0]) in {(i.code, i.fid) for i in address_issues(ctx)}
    mis = addr.fields().lookupField("MSAGComm")
    addr.changeAttributeValues(ids[0], {mis: "WRONG"})
    assert ("ATTRIB_MISMATCH", ids[0]) in {(i.code, i.fid) for i in address_issues(ctx)}


def _box(lyr, attrs, ring):
    fid = lyr.add_committed(attrs, None)
    lyr._feats[fid].setGeometry(QgsGeometry.fromPolygonXY([ring]))
    lyr._committed[fid] = lyr._feats[fid]
    return fid


def test_new_address_copies_the_centerline_side(world):
    rcl, addr, pcfg, cfg = world["rcl"], world["addr"], world["pcfg"], world["cfg"]
    rcl.changeAttributeValues(2, {rcl.fields().lookupField("MSAGComm_L"): None,
                                  rcl.fields().lookupField("MSAGComm_R"): None})
    for name in ("ESN_L", "ESN_R"):
        rcl.addAttribute(QgsField(name, "string"))
        cfg.mapping[name] = name
    addr.addAttribute(QgsField("ESN", "string"))
    C.save_to_layer(rcl, cfg)
    esn = _layer("esn", ["ESN"])
    _box(esn, ["101"], [QgsPointXY(-500, 0), QgsPointXY(500, 0), QgsPointXY(500, 500), QgsPointXY(-500, 500)])
    _box(esn, ["202"], [QgsPointXY(-500, -500), QgsPointXY(500, -500), QgsPointXY(500, 0), QgsPointXY(-500, 0)])
    pcfg.addresses.attribution_rules.append(
        {"layer_id": esn.id(), "source_field": "ESN", "target": "ESN"})
    save_project_config(pcfg)
    ctx = E.AddressContext(pcfg, rcl, cfg)
    right = E.plan_address(ctx, P(160, 0), P(160, -30))
    assert right.side == "R"
    assert right.community["MSAGComm"] == "TOWN B" and right.community["ESN"] == "202"
    assert right.community["PostCode"] == "12345"
    assert right.side_updates["ESN_R"] == "202" and right.side_updates["ESN_L"] == "101"
    assert right.side_updates["MSAGComm_R"] == "TOWN B"
    left = E.plan_address(ctx, P(50, 0), P(50, 20))
    assert left.community["MSAGComm"] == "TOWN A" and left.community["ESN"] == "101"
    assert "MSAGComm_L" not in left.side_updates and left.side_updates["ESN_L"] == "101"
    ids = E.commit_address(ctx, right, right.result.number, create_driveway=False)
    assert attr(addr, ids["address"], "ESN") == "202" and attr(addr, ids["address"], "MSAGComm") == "TOWN B"
    assert attr(rcl, 2, "ESN_R") == "202" and attr(rcl, 2, "ESN_L") == "101"
    assert attr(rcl, 2, "MSAGComm_R") == "TOWN B"


def test_split_where_the_side_changes_and_copies_addresses(world):
    from nena_addressing_and_rcl_tools.core.addressing.side_split import split_where_side_changes
    ctx, rcl, addr = world["ctx"], world["rcl"], world["addr"]
    aid = E.commit_address(ctx, E.plan_address(ctx, P(160, 0), P(160, -30)), 260, create_driveway=False)["address"]
    assert attr(addr, aid, "MSAGComm") == "TOWN A"
    rep = split_where_side_changes(rcl, world["cfg"], world["pcfg"])
    assert rep["split"] == 1 and rep["pieces"] == 1 and rep["addresses"] == 1
    west = rcl._feats[2].geometry().asPolyline()
    assert 140 < west[-1].x() < 160
    assert attr(rcl, 2, "MSAGComm_L") == "TOWN A" and attr(rcl, 2, "FromAddr_L") == 201
    east = [f for f in rcl._feats.values() if f.id() != 2 and f.hasGeometry() and f.geometry().asPolyline()
            and f.geometry().asPolyline()[0].x() > 140]
    assert len(east) == 1
    eid = east[0].id()
    assert attr(rcl, eid, "MSAGComm_L") == "TOWN B" and attr(rcl, eid, "MSAGComm_R") == "TOWN B"
    assert attr(rcl, eid, "ToAddr_L") == 299
    assert attr(addr, aid, "MSAGComm") == "TOWN B"
    assert attr(addr, aid, "RCL_NGUID") not in (None, "", "R2")


def test_new_address_uses_the_state_default_when_the_side_is_empty(world):
    rcl, addr, cfg, pcfg = world["rcl"], world["addr"], world["cfg"], world["pcfg"]
    addr.addAttribute(QgsField("State", "string"))
    for name in ("State_L", "State_R"):
        rcl.addAttribute(QgsField(name, "string"))
        cfg.mapping[name] = name
    cfg.defaults = {"State_L": "KY", "State_R": "KY"}
    rcl.changeAttributeValues(1, {rcl.fields().lookupField("State_L"): "IN"})
    C.save_to_layer(rcl, cfg)
    ctx = E.AddressContext(pcfg, rcl, cfg)
    left = E.plan_address(ctx, P(50, 0), P(50, 20))
    assert left.community["State"] == "IN"
    lid = E.commit_address(ctx, left, left.result.number, create_driveway=False)["address"]
    right = E.plan_address(ctx, P(160, 0), P(160, -30))
    assert "State" not in right.community
    rid = E.commit_address(ctx, right, right.result.number, create_driveway=False)["address"]
    assert attr(addr, lid, "State") == "IN"
    assert attr(addr, rid, "State") == "KY"


def test_centerline_sides_and_boundary_crossing(world):
    ctx = world["ctx"]
    n, crossings = E.attribute_centerline_sides(ctx)
    assert (2, "L") in crossings and (2, "R") in crossings     # R2 runs 100..200 across the 150 boundary
    assert not [c for c in crossings if c[0] == 1]
    assert {i.code for i in centerline_boundary_issues(ctx)} == {"RCL_CROSSES_BOUNDARY"}


def test_partial_rename_readdress(world):
    ctx, addr, rcl = world["ctx"], world["addr"], world["rcl"]
    a1 = E.commit_address(ctx, E.plan_address(ctx, P(150, 0), P(150, 20)), 251, create_driveway=False)["address"]
    a0 = E.commit_address(ctx, E.plan_address(ctx, P(50, 0), P(50, 20)), 151, create_driveway=False)["address"]
    assert E.addresses_on_segments(ctx, [2]) == [a1]
    # segment R2 becomes "Elm Rd" (new street S3) starting at its own origin
    svc = StreetService(world["pcfg"], centerline_layer=rcl, centerline_cfg=world["cfg"])
    import nena_addressing_and_rcl_tools.core.streets.names as N
    rec, _w = svc.add_street(N.parts_from({"St_Name": "Elm", "St_PosTyp": "Rd"}))
    svc.assign(rec.street_id, [2])
    svc.assign(rec.street_id, [a1], role="address")
    rows = E.readdress(ctx, [a1])
    (fid, old, plan), = rows
    # numbered on segment R2 itself: From_L 201 + 50 m
    assert old == "251" and plan.street_id == rec.street_id and plan.result.number == 251
    assert E.apply_readdress(ctx, [(fid, plan)]) == 1
    assert attr(addr, a1, "Add_Number") == plan.result.number and attr(addr, a1, "St_Name") == "Elm"
    assert attr(addr, a0, "Add_Number") == 151


def test_shared_driveway(world):
    ctx = world["ctx"]
    plan = E.plan_address(ctx, P(50, 0), P(50, 40))
    E.commit_address(ctx, plan, 151, create_driveway=True)
    hit = E.shared_driveway_at(ctx, P(50.2, 20), 1.0)
    assert hit is not None and (hit[1].x(), hit[1].y()) == (50.0, 0.0)


def test_reports(world, tmp_path):
    ctx = world["ctx"]
    E.commit_address(ctx, E.plan_address(ctx, P(50, 0), P(50, 20)), 151, create_driveway=False)
    svc = StreetService(world["pcfg"], centerline_layer=world["rcl"], centerline_cfg=world["cfg"])
    p1, p2 = tmp_path / "s.csv", tmp_path / "a.csv"
    assert streets_report(str(p1), svc, ctx) == 2
    rows = list(csv.DictReader(open(p1)))
    main = [r for r in rows if r["FullName"] == "Main St"][0]
    assert (main["Segments"], main["Addresses"], main["LowestNumber"]) == ("2", "1", "151")
    assert addresses_by_community_report(str(p2), ctx) == 1


# ---------------------------------------------------------------------------
# Left / right on roads in every direction (odd on the left, even on the right)
# ---------------------------------------------------------------------------
def _add_road(world, name, fid_tag, coords):
    world["rcl"].add_committed([name, "Rd", "", fid_tag, None, None, None, None, "O", "E", "TOWN A", "TOWN A"],
                               coords)


@pytest.mark.parametrize("coords, house, side", [
    ([(300, 0), (300, 100)], (320, 50), "R"),     # drawn south -> north: east is right
    ([(300, 0), (300, 100)], (280, 50), "L"),     # drawn south -> north: west is left
    ([(300, 100), (300, 0)], (320, 50), "L"),     # drawn north -> south: east is left
    ([(300, 100), (300, 0)], (280, 50), "R"),     # drawn north -> south: west is right
    ([(300, 0), (400, 0)], (350, 20), "L"),       # drawn west -> east: north is left
    ([(400, 0), (300, 0)], (350, 20), "R"),       # drawn east -> west: north is right
])
def test_side_and_parity_follow_the_drawn_direction(world, coords, house, side):
    _add_road(world, "Birch", "RB", coords)
    mid = ((coords[0][0] + coords[1][0]) / 2, (coords[0][1] + coords[1][1]) / 2)
    plan = E.plan_address(world["ctx"], P(*mid), P(*house))
    assert plan.side == side
    assert plan.parity == ("O" if side == "L" else "E")
    assert plan.result.number % 2 == (1 if side == "L" else 0)


def test_place_units_offers_a_number_for_each_side(world):
    # Oak Ave runs south -> north from (100, 0) to (100, 100)
    ctx = world["ctx"]
    fid = next(f.id() for f in world["rcl"].getFeatures()
               if f.attribute(world["rcl"].fields().lookupField("RCL_NGUID")) == "R3")
    numbers = E.house_numbers_by_side(ctx, fid, P(100, 50))
    assert numbers["L"] % 2 == 1 and numbers["R"] % 2 == 0
    west = E.plan_address(ctx, P(100, 50), P(80, 50), segment_fid=fid)
    east = E.plan_address(ctx, P(100, 50), P(120, 50), segment_fid=fid)
    assert (west.side, east.side) == ("L", "R")
    assert numbers[west.side] == west.result.number
    assert numbers[east.side] == east.result.number


def test_parity_matches():
    assert E.parity_matches(101, "O") and not E.parity_matches(101, "E")
    assert E.parity_matches(100, "E") and not E.parity_matches(100, "O")
    assert E.parity_matches(7, "B") and E.parity_matches(8, "")


def test_an_address_on_a_separate_piece_of_the_street_does_not_push_the_number(world):
    ctx = world["ctx"]
    rcl = world["rcl"]
    world["pcfg"].addresses.segment_parity_wins = False   # settings' parity, segment's is reported
    # Two unconnected pieces of Pine Rd, both drawn east -> west, each measuring from its own start
    rcl.add_committed(["Pine", "Rd", "", "P1", 100, 310, 101, 311, "E", "O", "TOWN A", "TOWN A"],
                      [(800, 300), (600, 300)])
    rcl.add_committed(["Pine", "Rd", "", "P2", 400, 498, 401, 499, "E", "O", "TOWN A", "TOWN A"],
                      [(1500, 300), (1300, 300)])
    far = E.plan_address(ctx, P(1470, 300), P(1470, 280))
    E.commit_address(ctx, far, 418, create_driveway=False)
    ctx = E.AddressContext(world["pcfg"], rcl, world["cfg"])
    plan = E.plan_address(ctx, P(700, 300), P(700, 280))
    # P1 is drawn east -> west, so south is left. The settings put odd numbers on the left;
    # the segment's Parity_L (E) is reported, not followed. From_L 100 + 100 m -> 199.
    assert plan.side == "L" and plan.parity == "O"
    assert plan.result.number == 199
    assert not any("418" in m for m in plan.messages)
    assert any("Parity_L says even" in m for m in plan.messages)


def test_numbers_come_from_the_clicked_segment_not_the_rest_of_the_street(world):
    ctx, pcfg = world["ctx"], world["pcfg"]
    # An address far down Main St on R2 must not push numbers on R1
    E.commit_address(ctx, E.plan_address(ctx, P(190, 0), P(190, 20)), 291, create_driveway=False)
    ctx = E.AddressContext(pcfg, world["rcl"], world["cfg"])
    p = E.plan_address(ctx, P(30, 0), P(30, 20))                 # R1 left: From_L 101 + 30 m
    assert p.result.number == 131 and not p.messages
    pcfg.addresses.method = "interpolate"
    p = E.plan_address(ctx, P(25, 0), P(25, -20))                # R1 right 100..198, a quarter along
    assert p.side == "R" and p.result.number in (124, 126)


def test_segment_parity_can_win_when_asked(world):
    rcl, pcfg = world["rcl"], world["pcfg"]
    rcl.changeAttributeValues(1, {rcl.fields().lookupField("Parity_L"): "E"})
    pcfg.addresses.segment_parity_wins = False
    plan = E.plan_address(world["ctx"], P(50, 0), P(50, 20))
    assert plan.parity == "O" and any("Parity_L says even" in m for m in plan.messages)
    pcfg.addresses.segment_parity_wins = True
    plan = E.plan_address(world["ctx"], P(50, 0), P(50, 20))
    assert plan.parity == "E" and plan.result.number % 2 == 0
