# -*- coding: utf-8 -*-
"""
Unit tests for the QGIS-independent core.

Run from the folder that CONTAINS the plugin folder:
    python -m pytest nena_rcl_tools/test -q
"""

import math

import pytest

from nena_rcl_tools.core import ranging as R
from nena_rcl_tools.core import topology as T
from nena_rcl_tools.core import rules as V
from nena_rcl_tools.core import schema as S
from nena_rcl_tools.core.config import PluginConfig, M_INTERVAL
from nena_rcl_tools.core.nguid import make_nguid


# ---------------------------------------------------------------- parity
def test_conform():
    assert R.conform_up(100, "O") == 101
    assert R.conform_up(101, "O") == 101
    assert R.conform_down(100, "O") == 99
    assert R.conform_up(101, "E") == 102
    assert R.conform_down(-3, "E") == -4
    assert R.conform_up(-3, "O") == -3


def test_infer_parity():
    assert R.infer_parity([101, 115]) == "O"
    assert R.infer_parity([100, 116]) == "E"
    assert R.infer_parity([100, 115]) == "B"
    assert R.infer_parity([]) == "Z"


# ---------------------------------------------------------------- methods
def test_hundred_block_chain():
    b = R.hundred_block_boundaries(3, 100, 100)
    res = R.range_chain(b, {"L": ["O"] * 3, "R": ["E"] * 3})
    assert res["L"] == [(101, 199, "O"), (201, 299, "O"), (301, 399, "O")]
    assert res["R"] == [(100, 198, "E"), (200, 298, "E"), (300, 398, "E")]


def test_hundred_block_base_not_aligned():
    b = R.hundred_block_boundaries(1, 250, 100)
    assert R.range_chain(b, {"L": ["O"]})["L"] == [(201, 299, "O")]


def test_interval_chain_no_overlap_and_monotonic():
    # 1000 numbers per mile; three segments of 500 ft, 20 ft (tiny), 1000 ft
    mpn = R.to_meters(5.28, "feet")
    lengths = [R.to_meters(x, "feet") for x in (500, 20, 1000)]
    b = R.interval_boundaries(lengths, 100, mpn)
    res = R.range_chain(b, {"L": ["O"] * 3, "R": ["E"] * 3})
    for side in "LR":
        rs = res[side]
        for (f1, t1, _), (f2, t2, _) in zip(rs, rs[1:]):
            assert f1 <= t1 < f2 <= t2
    # first segment ~ 100 + 500/5.28 = 194.7 -> left 101..193, right 100..194
    assert res["L"][0] == (101, 193, "O")
    assert res["R"][0] == (100, 194, "E")


def test_proportional():
    b = R.proportional_boundaries([100, 300], 100, 199)
    res = R.range_chain(b, {"L": ["O", "O"], "R": ["E", "E"]})
    assert res["L"] == [(101, 123, "O"), (125, 199, "O")]
    assert res["R"] == [(100, 124, "E"), (126, 198, "E")]


def test_zero_side_in_chain():
    b = R.hundred_block_boundaries(2, 100, 100)
    res = R.range_chain(b, {"L": ["O", "Z"]})
    assert res["L"] == [(101, 199, "O"), (0, 0, "Z")]


# ---------------------------------------------------------------- split / merge
def test_redistribute_even_split():
    assert R.redistribute(101, 199, "O", [50, 50]) == [(101, 149, "O"), (151, 199, "O")]
    assert R.redistribute(100, 198, "E", [50, 50]) == [(100, 148, "E"), (150, 198, "E")]


def test_redistribute_keeps_endpoints_and_no_overlap():
    pieces = R.redistribute(101, 199, "O", [10, 70, 20])
    assert pieces[0][0] == 101 and pieces[-1][1] == 199
    for (f1, t1, _), (f2, t2, _) in zip(pieces, pieces[1:]):
        assert t1 < f2


def test_redistribute_decreasing():
    pieces = R.redistribute(199, 101, "O", [50, 50])
    assert pieces == [(199, 151, "O"), (149, 101, "O")]


def test_redistribute_tiny_range():
    pieces = R.redistribute(101, 101, "O", [50, 50])
    assert pieces[0] == (101, 101, "O")
    assert pieces[1] == (0, 0, "Z")


def test_redistribute_zero():
    assert R.redistribute(0, 0, "Z", [1, 2]) == [(0, 0, "Z"), (0, 0, "Z")]


def test_combine():
    assert R.combine([(101, 149, "O"), (151, 199, "O")]) == (101, 199, "O")
    assert R.combine([(199, 151, "O"), (149, 101, "O")]) == (199, 101, "O")
    assert R.combine([(101, 149, "O"), (150, 198, "E")]) == (101, 198, "B")
    assert R.combine([(0, 0, "Z")]) == (0, 0, "Z")


def test_extend_to():
    mpn = R.to_meters(5.28, "feet")
    assert R.extend_to(101, "O", R.to_meters(528, "feet"), mpn) == 199


# ---------------------------------------------------------------- address points
def test_points_expand_respects_neighbours():
    obs = [[103, 117, 125], [], [131, 145], [215]]
    res = R.ranges_from_observations(obs, "O", 100, expand=True, infer=True)
    assert res[0] == (101, 129, "O")   # capped below the next observed (131)
    assert res[1] is None
    assert res[2][0] > res[0][1] and res[2][1] < 215
    assert res[3] == (201, 299, "O")


def test_points_no_expand():
    res = R.ranges_from_observations([[110, 102, 120]], "O", 100, expand=False, infer=True)
    assert res == [(102, 120, "E")]


# ---------------------------------------------------------------- topology
def _seg(fid, a, b, key="MAIN ST"):
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    return T.SegmentInfo(fid=fid, start=a, end=b, length=math.dist(a, b), key=key,
                         start_bearing=ang, end_bearing=ang)


def test_chain_building_and_branch():
    segs = [_seg(3, (200, 0), (300, 0)), _seg(1, (0, 0), (100, 0)), _seg(2, (100, 0), (200, 0)),
            _seg(4, (100, 0), (100, 100)),  # branch off at node 100,0
            _seg(9, (0, 50), (0, 60), key=None)]
    T.assign_nodes(segs, 0.01)
    chains = T.build_chains(segs)
    fids = [[s.fid for s in c] for c in chains]
    assert [9] in fids
    assert [1, 2, 3] in fids        # straight continuation preferred
    assert [4] in fids


def test_snapping_tolerance():
    segs = [_seg(1, (0, 0), (100, 0)), _seg(2, (100.3, 0.2), (200, 0))]
    T.assign_nodes(segs, 0.5)
    assert segs[0].end_node == segs[1].start_node


def test_direction_conflict():
    segs = [_seg(1, (0, 0), (100, 0)), _seg(2, (200, 0), (100, 0))]
    T.assign_nodes(segs, 0.01)
    conf = T.direction_conflicts(segs)
    assert conf and conf[0][2] == "head_to_head"


def test_upstream():
    segs = [_seg(1, (0, 0), (100, 0)), _seg(2, (100, 0), (200, 0))]
    T.assign_nodes(segs, 0.01)
    assert [s.fid for s in T.upstream_segments(segs[1], segs)] == [1]


def test_order_pieces():
    p = [_seg(-2, (50, 0), (100, 0)), _seg(5, (0, 0), (20, 0)), _seg(-1, (20, 0), (50, 0))]
    assert [x.fid for x in T.order_pieces(p, 0.01)] == [5, -1, -2]


def test_order_pieces_reversed_second_keeps_the_original_start():
    # The new piece was digitized from the far end back to the split.
    pieces = [_seg(2, (100, 0), (50, 0)), _seg(1, (0, 0), (50, 0))]
    assert [x.fid for x in T.order_pieces(pieces, 0.01)] == [1, 2]
    assert [x.fid for x in T.ordered_path(pieces, 0.01)] == [1, 2]


# ---------------------------------------------------------------- rules
def _rr(fid, side, f, t, p, g="MAIN ST"):
    return V.RangeRecord(fid, side, f, t, p, g)


def test_check_side():
    codes = lambda r: {i.code for i in V.check_side(r, False)}
    assert codes(_rr(1, "L", 101, 199, "O")) == set()
    assert "PARITY_NUMBER_MISMATCH" in codes(_rr(1, "L", 100, 199, "O"))
    assert "RANGE_REVERSED" in codes(_rr(1, "L", 199, 101, "O"))
    assert "PARITY_ZERO_MISMATCH" in codes(_rr(1, "L", 101, 199, "Z"))
    assert "RANGE_HALF_ZERO" in codes(_rr(1, "L", 0, 199, "O"))
    assert "PARITY_DOMAIN" in codes(_rr(1, "L", 101, 199, "X"))
    assert "RANGE_MISSING" in codes(_rr(1, "L", None, 199, "O"))


def test_overlaps():
    recs = [_rr(1, "L", 101, 199, "O"), _rr(1, "R", 100, 198, "E"),
            _rr(2, "L", 151, 251, "O"),          # overlaps fid 1 left
            _rr(3, "R", 150, 250, "E"),          # overlaps fid 1 right
            _rr(4, "L", 301, 399, "O"),
            _rr(5, "L", 101, 199, "O", g="OAK AVE")]
    issues = [i for i in V.find_overlaps(recs) if i.code == "OVERLAP"]
    pairs = {(i.fid, i.related_fid) for i in issues}
    assert (2, 1) in pairs and (3, 1) in pairs
    assert not any(4 in p or 5 in p for p in pairs)
    # odd vs even never collide; odd vs both does
    assert not V.find_overlaps([_rr(1, "L", 101, 199, "O"), _rr(2, "L", 100, 198, "E")])
    assert V.find_overlaps([_rr(1, "L", 101, 199, "O"), _rr(2, "L", 150, 160, "B")])


def test_gaps():
    a = {"L": _rr(1, "L", 101, 199, "O"), "R": _rr(1, "R", 100, 198, "E")}
    b = {"L": _rr(2, "L", 201, 299, "O"), "R": _rr(2, "R", 300, 398, "E")}
    issues = V.check_chain_gaps([a, b], 0)
    assert [(i.code, i.side) for i in issues] == [("GAP", "R")]
    c = {"L": _rr(3, "L", 151, 299, "O")}
    assert V.check_chain_gaps([a, c], 0)[0].code == "NOT_INCREASING"


def test_domains():
    assert V.check_directional(1, "St_PreDir", "North") == []
    assert V.check_directional(1, "St_PreDir", "N")[0].code == "DIRECTIONAL_ABBREVIATED"
    assert V.check_oneway(1, "FT") == []
    assert V.check_oneway(1, "X")[0].code == "ONEWAY_DOMAIN"
    assert V.check_speed(1, 35) == []
    assert V.check_nguid_format(1, "urn:emergency:uid:gis:RCL:abc:psap.example.gov") == []
    assert V.check_nguid_format(1, "RCL_1@x.gov")[0].code == "NGUID_LEGACY"
    assert V.find_duplicates({1: "a", 2: "a", 3: "b"}, "DUP", "x")[0].fid == 2


# ---------------------------------------------------------------- schema / config / nguid
def test_auto_map():
    m = S.auto_map(["OBJECTID", "L_F_ADD", "L_T_ADD", "R_F_ADD", "R_T_ADD", "PREDIR", "STREETNAME",
                    "SUFFIX", "st_posdir", "ONE_WAY", "SPEED"])
    assert m["FromAddr_L"] == "L_F_ADD" and m["ToAddr_R"] == "R_T_ADD"
    assert m["St_Name"] == "STREETNAME" and m["St_PosDir"] == "st_posdir"
    assert m["OneWay"] == "ONE_WAY" and m["SpeedLimit"] == "SPEED"


def test_config_roundtrip_and_tolerance():
    cfg = PluginConfig()
    cfg.mapping = {"FromAddr_L": "L_F_ADD"}
    cfg.ranging.method = M_INTERVAL
    cfg.ranging.interval_distance = 3.0
    cfg.defaults = {"State_L": "KY", "State_R": "KY", "OneWay": "B"}
    back = PluginConfig.from_json(cfg.to_json())
    assert back.to_dict() == cfg.to_dict()
    assert back.defaults["State_L"] == "KY"
    assert PluginConfig.from_dict({"defaults": {"OneWay": "B", "County_L": "  "}}).defaults == {"OneWay": "B"}
    odd = PluginConfig.from_dict({"ranging": {"base_address": "200", "unknown": 1, "block_size": "x"}})
    assert odd.ranging.base_address == 200 and odd.ranging.block_size == 100


def test_nguid():
    assert make_nguid("abc", "psap.example.gov") == "urn:emergency:uid:gis:RCL:abc:psap.example.gov"
    assert make_nguid("12", "x.gov", "legacy") == "RCL_12@x.gov"
    with pytest.raises(ValueError):
        make_nguid("1", "")


# ---------------------------------------------------------------- anchored split (address points near split)
def test_anchor_keeps_point_on_its_piece():
    # plain proportional would put 151 (20 m from the start) on the second piece
    assert R.redistribute(101, 199, "O", [25, 75]) == [(101, 125, "O"), (127, 199, "O")]
    pieces = R.redistribute(101, 199, "O", [25, 75], anchors=[(20, 151)])
    assert pieces[0][0] <= 151 <= pieces[0][1]
    assert pieces[1][0] > 151 and pieces[1][1] == 199


def test_anchor_point_right_at_split_and_outlier():
    pieces = R.redistribute(101, 199, "O", [25, 75],
                            anchors=[(20, 151), (24.9, 153), (25, 155), (26, 113), (30, 160)])
    # 113 is out of order (outlier), 160 wrong parity -> ignored; 155 sits exactly at the split -> piece 2
    assert pieces[0] == (101, 153, "O")
    assert pieces[1][0] == 155


def test_anchor_three_pieces_and_decreasing():
    pieces = R.redistribute(101, 199, "O", [25, 25, 50], anchors=[(10, 111), (30, 181), (60, 185)])
    assert pieces[0][0] <= 111 <= pieces[0][1]
    assert pieces[1][0] <= 181 <= pieces[1][1]
    assert pieces[2][0] <= 185 <= pieces[2][1]
    dec = R.redistribute(199, 101, "O", [25, 75], anchors=[(20, 121)])
    assert dec[0][0] == 199 and dec[0][1] <= 121 and dec[1][1] == 101


def test_monotone_anchors():
    kept = R.monotone_anchors([(1, 105), (2, 199), (3, 111), (4, 117), (5, 300)], 101, 199, "O")
    assert kept == [(1.0, 105), (3.0, 111), (4.0, 117)]


# ---------------------------------------------------------------- templates
from nena_rcl_tools.core import template as TPL  # noqa: E402


def test_template_json_and_effective_fields():
    tpl = TPL.from_json_text("""{"name": "State RCL", "fields": [
        {"name": "FROMADDR_L", "type": "Integer", "req": "M"},
        {"name": "St_Name", "type": "String", "length": 80},
        {"name": "Effective", "req": "M"},
        {"name": "STATE_SEGID", "type": "String", "length": 20, "req": "M", "description": "state id"},
        {"name": "L_F_ADD_X", "nena": "ToAddr_L", "type": "int"}]}""", "state.json")
    assert tpl["name"] == "State RCL" and len(tpl["fields"]) == 5
    defs = {d.name: d for d in TPL.effective_fields(tpl)}
    assert defs["FromAddr_L"].physical_name == "FROMADDR_L"
    assert defs["St_Name"].length == 80 and defs["St_Name"].req == "C"      # req inherited
    assert defs["Effective"].req == "M"                                        # overridden
    assert defs["STATE_SEGID"].req == "M" and defs["STATE_SEGID"].physical_name == "STATE_SEGID"
    assert defs["ToAddr_L"].physical_name == "L_F_ADD_X"                      # explicit nena column
    assert "RCL_NGUID" in defs                                                 # rest of NENA kept
    names = [d.name for d in TPL.effective_fields(tpl)]
    assert names[:5] == ["FromAddr_L", "St_Name", "Effective", "STATE_SEGID", "ToAddr_L"]
    assert TPL.summary(tpl) == {"fields": 5, "matched": 4, "extra": 1}
    m = S.auto_map(["FROMADDR_L", "STATE_SEGID", "L_F_ADD_X"], TPL.effective_fields(tpl))
    assert m == {"FromAddr_L": "FROMADDR_L", "STATE_SEGID": "STATE_SEGID", "ToAddr_L": "L_F_ADD_X"}


def test_template_csv_and_errors():
    tpl = TPL.from_csv_text("Name,Type,Length,Req\nFromAddr_R,long,,M\nZIP_R,text,5,C\n", "t.csv")
    assert [f["type"] for f in tpl["fields"]] == ["int", "string"]
    assert {d.name: d for d in TPL.effective_fields(tpl)}["PostCode_R"].physical_name == "ZIP_R"
    assert TPL.is_template_json('{"fields": []}') and not TPL.is_template_json('{"type": "FeatureCollection"}')
    with pytest.raises(TPL.TemplateError):
        TPL.from_csv_text("a,b\n1,2\n")
    with pytest.raises(TPL.TemplateError):
        TPL.from_json_text('{"fields": []}')
    assert len(TPL.builtin_template()["fields"]) == len(S.RCL_FIELDS)


def test_config_keeps_template():
    cfg = PluginConfig()
    cfg.template = TPL.builtin_template()
    assert PluginConfig.from_json(cfg.to_json()).template["name"].startswith("NENA")


# ---------------------------------------------------------------- overlap selection helpers
def test_overlap_fids():
    recs = [_rr(1, "L", 101, 199, "O"), _rr(2, "L", 151, 251, "O"), _rr(3, "L", 301, 399, "O")]
    issues = V.find_overlaps(recs) + [V.Issue(3, V.ERROR, "PARITY_DOMAIN", "x")]
    assert V.overlap_fids(issues) == [1, 2]
    assert V.involved_fids(issues) == [1, 2, 3]


def test_interval_5_28_ft_ranges_a_2112_ft_road_100_to_500():
    # 2112 ft at one number per 5.28 ft = 400 numbers: 100 ... 499
    mpn = R.to_meters(5.28, "feet")
    b = R.interval_boundaries([R.to_meters(2112, "feet")], 100, mpn)
    res = R.range_chain(b, {"L": ["O"], "R": ["E"]})
    assert res["L"][0] == (101, 499, "O")
    assert res["R"][0] == (100, 498, "E")



def test_new_road_ranging_uses_house_number_spacing():
    from nena_rcl_tools.core.config import RangingSettings
    from nena_rcl_tools.core.project_config import ProjectConfig
    from nena_rcl_tools.core.ranger import distance_overrides
    pcfg = ProjectConfig()
    pcfg.addresses.method, pcfg.addresses.distance_per_number, pcfg.addresses.distance_units = "distance", 5.28, "feet"
    rs = RangingSettings()          # Ranging tab still on hundred-block
    o = distance_overrides(pcfg, rs)
    assert o["method"] == M_INTERVAL and o["interval_distance"] == 5.28 and o["interval_units"] == "feet"
    pcfg.addresses.method = "interpolate"
    rs.interval_distance = 3.2
    rs.interval_units = "meters"
    assert distance_overrides(pcfg, rs)["interval_distance"] == 3.2
