# -*- coding: utf-8 -*-
"""
Integration tests of the ranging / split / merge / validation engines
against a tiny fake of qgis.core (test/fake_qgis). They exercise the plugin's
orchestration logic end to end without a QGIS install. When real QGIS is
importable these tests are skipped (run them inside QGIS instead, see README).
"""


import pytest

import qgis.core  # noqa: E402

if "fake_qgis" not in (getattr(qgis.core, "__file__", "") or ""):
    pytest.skip("real QGIS available - fake-QGIS tests skipped", allow_module_level=True)


from qgis.core import QgsFeature, QgsField, QgsGeometry, QgsPointXY, QgsProject, QgsVectorLayer  # noqa: E402

from nena_rcl_tools.core import config as C  # noqa: E402
from nena_rcl_tools.core.ranger import RangeEngine  # noqa: E402
from nena_rcl_tools.core.split_handler import LayerWatcher, apply_split_policies  # noqa: E402
from nena_rcl_tools.core.validator import Validator  # noqa: E402

FIELDS = ["St_Name", "St_PosTyp", "FromAddr_L", "ToAddr_L", "FromAddr_R", "ToAddr_R",
          "Parity_L", "Parity_R", "RCL_NGUID", "DateUpdate"]
TYPES = ["string", "string", "int", "int", "int", "int", "string", "string", "string", "datetime"]


def make_layer():
    lyr = QgsVectorLayer("LineString", "rcl")
    for n, t in zip(FIELDS, TYPES):
        lyr.addAttribute(QgsField(n, t))
    # Main St: three 100 m segments digitized west->east, Oak Ave one segment
    lyr.add_committed(["MAIN", "St"] + [None] * 8, [(0, 0), (100, 0)])
    lyr.add_committed(["MAIN", "St"] + [None] * 8, [(100, 0), (200, 0)])
    lyr.add_committed(["MAIN", "St"] + [None] * 8, [(200, 0), (300, 0)])
    lyr.add_committed(["OAK", "Ave"] + [None] * 8, [(100, 0), (100, 100)])
    QgsProject.instance().addMapLayer(lyr)
    cfg = C.PluginConfig()
    cfg.mapping = {n: n for n in FIELDS}
    cfg.street_key_fields = ["St_Name", "St_PosTyp"]
    cfg.edit.agency_id = "psap.example.gov"
    cfg.edit.auto_rerange_split = True
    cfg.edit.auto_merge = True
    return lyr, cfg


def ranges(lyr, fid):
    f = next(x for x in lyr.getFeatures() if x.id() == fid)
    a = [f.attribute(FIELDS.index(n)) for n in ("FromAddr_L", "ToAddr_L", "FromAddr_R", "ToAddr_R",
                                                 "Parity_L", "Parity_R")]
    return tuple(getattr(v, "v", v) if hasattr(v, "isNull") else v for v in a)


def test_hundred_block_whole_layer():
    lyr, cfg = make_layer()
    rep = RangeEngine(lyr, cfg).run(False)
    assert rep.updated == 4 and rep.chains == 2
    assert ranges(lyr, 1) == (101, 199, 100, 198, "O", "E")
    assert ranges(lyr, 2) == (201, 299, 200, 298, "O", "E")
    assert ranges(lyr, 3) == (301, 399, 300, 398, "O", "E")
    assert ranges(lyr, 4) == (101, 199, 100, 198, "O", "E")
    assert lyr.commands == ["NENA: auto-range"]
    # running again changes nothing
    rep2 = RangeEngine(lyr, cfg).run(False)
    assert rep2.updated == 0 and rep2.unchanged == 4


def test_interval_selected_continues_upstream():
    lyr, cfg = make_layer()
    RangeEngine(lyr, cfg).run(False)
    cfg.ranging.method = C.M_INTERVAL
    cfg.ranging.interval_distance = 1.0
    cfg.ranging.interval_units = "meters"
    lyr.selectByIds([2, 3])
    RangeEngine(lyr, cfg).run(True)
    # upstream (fid 1) ends at 199 -> starts at 200; 1 number per metre
    assert ranges(lyr, 2) == (201, 299, 200, 298, "O", "E")
    assert ranges(lyr, 3)[0] == 301 and ranges(lyr, 3)[1] == 399


def _split(lyr, fid, at_x):
    """Simulate QGIS Split Features with Duplicate split policy."""
    f = next(x for x in lyr.getFeatures() if x.id() == fid)
    pts = f.geometry().asPolyline()
    y = pts[0].y()
    lyr.beginEditCommand("Features split")
    lyr.changeGeometry(fid, QgsGeometry.fromPolylineXY([pts[0], QgsPointXY(at_x, y)]))
    nf = QgsFeature()
    nf.setAttributes(f.attributes())
    nf.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(at_x, y), pts[-1]]))
    lyr.addFeature(nf)
    lyr.endEditCommand()
    return nf.id()


def test_split_rerange_and_nguid():
    lyr, cfg = make_layer()
    C.save_to_layer(lyr, cfg)
    RangeEngine(lyr, cfg).run(False)
    lyr.startEditing()
    msgs = []
    w = LayerWatcher(lyr, lambda lvl, t: msgs.append((lvl, t)))
    new_id = _split(lyr, 2, 125.0)     # 25 m + 75 m
    assert ranges(lyr, 2) == (201, 225, 200, 224, "O", "E")
    assert ranges(lyr, new_id) == (227, 299, 226, 298, "O", "E")
    nguid = lyr._feats[new_id].attribute(FIELDS.index("RCL_NGUID"))
    assert str(nguid).startswith("urn:emergency:uid:gis:RCL:")
    assert lyr.commands[-1] == "NENA: re-range after split"
    assert not [m for m in msgs if m[0] == "Warning"]
    w.detach()


def test_merge_combines():
    lyr, cfg = make_layer()
    C.save_to_layer(lyr, cfg)
    RangeEngine(lyr, cfg).run(False)
    lyr.startEditing()
    w = LayerWatcher(lyr, lambda *a: None)
    lyr.selectByIds([1, 2])           # caches original ranges
    lyr.beginEditCommand("Merged features")
    lyr.changeGeometry(1, QgsGeometry.fromPolylineXY([QgsPointXY(0, 0), QgsPointXY(200, 0)]))
    lyr.deleteFeature(2)
    lyr.endEditCommand()
    assert ranges(lyr, 1) == (101, 299, 100, 298, "O", "E")
    w.detach()


def test_split_policies():
    lyr, cfg = make_layer()
    n = apply_split_policies(lyr, cfg)
    assert n == len(FIELDS)
    assert lyr.split_policies[FIELDS.index("RCL_NGUID")] == 3      # UnsetField
    assert lyr.split_policies[FIELDS.index("FromAddr_L")] == 1     # Duplicate


def test_validation_flags_overlap_and_parity():
    lyr, cfg = make_layer()
    RangeEngine(lyr, cfg).run(False)
    # corrupt: fid 3 left overlaps fid 2 ; fid 4 right has odd numbers with E parity
    lyr.changeAttributeValues(3, {FIELDS.index("FromAddr_L"): 251})
    lyr.changeAttributeValues(4, {FIELDS.index("FromAddr_R"): 101})
    issues = Validator(lyr, cfg).run(False)
    codes = {(i.code, i.fid) for i in issues}
    assert ("OVERLAP", 3) in codes
    assert ("PARITY_NUMBER_MISMATCH", 4) in codes
    assert ("NGUID_MISSING", 1) in codes
    assert any(i.code == "FIELD_NOT_MAPPED" for i in issues)   # DiscrpAgID etc. not on test layer


def test_address_points_method():
    lyr, cfg = make_layer()
    pts = QgsVectorLayer("Point", "addr")
    pts.addAttribute(QgsField("NUM", "string"))
    pts.addAttribute(QgsField("STREET", "string"))
    for num, x, y in (("105", 20, 10), ("117B", 60, 10), ("110", 30, -10), ("122", 80, -10),
                      ("215", 150, 10), ("999", 150, 300)):
        f = QgsFeature()
        f.setAttributes([num, "MAIN ST"])
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        pts.dataProvider().addFeatures([f])
    QgsProject.instance().addMapLayer(pts)
    cfg.ranging.method = C.M_POINTS
    cfg.ranging.point_layer_id = pts.id()
    cfg.ranging.point_number_field = "NUM"
    cfg.ranging.point_street_field = "STREET"
    cfg.ranging.point_expand_to_block = False
    rep = RangeEngine(lyr, cfg).run(False)
    # left side is +y when digitized west->east
    assert ranges(lyr, 1)[:4] == (105, 117, 110, 122)
    assert ranges(lyr, 2)[0:2] == (215, 215)
    assert rep.skipped >= 2   # fid 3 and OAK AVE had no points


# ---------------------------------------------------------------- new: anchors, split tool, template, overlaps
from nena_rcl_tools.core.split_ops import split_and_rerange, SPLIT_COMMAND  # noqa: E402
from nena_rcl_tools.core.layer_access import add_missing_fields  # noqa: E402
from nena_rcl_tools.core import template as TPL  # noqa: E402


def _point_layer(points):
    pts = QgsVectorLayer("Point", "addr")
    pts.addAttribute(QgsField("NUM", "string"))
    pts.addAttribute(QgsField("STREET", "string"))
    for num, x, y, street in points:
        f = QgsFeature()
        f.setAttributes([num, street])
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        pts.dataProvider().addFeatures([f])
    QgsProject.instance().addMapLayer(pts)
    return pts


def _use_points(cfg, pts):
    cfg.ranging.point_layer_id = pts.id()
    cfg.ranging.point_number_field = "NUM"
    cfg.ranging.point_street_field = "STREET"


def test_split_watcher_anchored_to_address_point():
    lyr, cfg = make_layer()
    RangeEngine(lyr, cfg).run(False)             # fid 2: 201-299 / 200-298 over x 100..200
    # 251 lies left of fid 2 at x=120 (20 m in); a plain proportional split at x=125 would give it to piece 2
    _use_points(cfg, _point_layer([("251", 120, 8, "MAIN ST"), ("260", 122, -8, "MAIN ST"),
                                   ("999", 120, 8, "OAK AVE")]))
    C.save_to_layer(lyr, cfg)
    lyr.startEditing()
    w = LayerWatcher(lyr, lambda *a: None)
    new_id = _split(lyr, 2, 125.0)
    a, b = ranges(lyr, 2), ranges(lyr, new_id)
    assert a[0] <= 251 <= a[1] and b[0] > 251       # left: point stays on the first piece
    assert a[2] <= 260 <= a[3] and b[2] > 260       # right too
    assert a[0] == 201 and b[1] == 299 and b[3] == 298
    w.detach()


def test_split_and_rerange_tool_single_undo_step():
    lyr, cfg = make_layer()
    RangeEngine(lyr, cfg).run(False)
    _use_points(cfg, _point_layer([("251", 120, 8, "MAIN ST")]))
    cfg.edit.auto_rerange_split = True
    C.save_to_layer(lyr, cfg)
    lyr.startEditing()
    w = LayerWatcher(lyr, lambda *a: None)       # must NOT re-process the tool's own command
    before = len(lyr.commands)
    new_id, text = split_and_rerange(lyr, cfg, 2, QgsPointXY(125, 3))
    assert lyr.commands[before:] == [SPLIT_COMMAND]
    a, b = ranges(lyr, 2), ranges(lyr, new_id)
    assert a[0] == 201 and a[0] <= 251 <= a[1] and b[1] == 299 and b[0] > 251
    g1 = lyr._feats[2].geometry().asPolyline()
    g2 = lyr._feats[new_id].geometry().asPolyline()
    assert (g1[-1].x(), g2[0].x(), g2[-1].x()) == (125.0, 125.0, 200.0)
    assert str(lyr._feats[new_id].attribute(FIELDS.index("RCL_NGUID"))).startswith("urn:emergency")
    assert "address point" in text
    w.detach()


def test_split_tool_rejects_end_point():
    from nena_rcl_tools.core.layer_access import ConfigError
    lyr, cfg = make_layer()
    lyr.startEditing()
    with pytest.raises(ConfigError):
        split_and_rerange(lyr, cfg, 2, QgsPointXY(100.0, 0))


def test_selected_split_pieces_divide_the_original_range():
    """QGIS Split Features copies the range. Range selected must divide it, not open a new block."""
    lyr, cfg = make_layer()
    idx = {n: FIELDS.index(n) for n in ("FromAddr_L", "ToAddr_L", "FromAddr_R", "ToAddr_R", "Parity_L", "Parity_R")}
    lyr.changeAttributeValues(2, {idx["FromAddr_L"]: 201, idx["ToAddr_L"]: 299, idx["FromAddr_R"]: 200,
                                  idx["ToAddr_R"]: 298, idx["Parity_L"]: "O", idx["Parity_R"]: "E"})
    new_id = _split(lyr, 2, 150.0)
    assert ranges(lyr, 2)[:4] == (201, 299, 200, 298)
    assert ranges(lyr, new_id)[:4] == (201, 299, 200, 298)
    lyr.selectByIds([2, new_id])
    rep = RangeEngine(lyr, cfg).run(True)
    assert ranges(lyr, 2) == (201, 249, 200, 248, "O", "E")
    assert ranges(lyr, new_id) == (251, 299, 250, 298, "O", "E")
    assert rep.updated == 2


def test_selected_reversed_piece_divides_from_the_original_start():
    lyr, cfg = make_layer()
    idx = {n: FIELDS.index(n) for n in ("FromAddr_L", "ToAddr_L", "FromAddr_R", "ToAddr_R", "Parity_L", "Parity_R")}
    lyr.changeAttributeValues(2, {idx["FromAddr_L"]: 201, idx["ToAddr_L"]: 299, idx["FromAddr_R"]: 200,
                                  idx["ToAddr_R"]: 298, idx["Parity_L"]: "O", idx["Parity_R"]: "E"})
    original = next(x for x in lyr.getFeatures() if x.id() == 2)
    lyr.changeGeometry(2, QgsGeometry.fromPolylineXY([QgsPointXY(100, 0), QgsPointXY(150, 0)]))
    piece = QgsFeature()
    piece.setAttributes(original.attributes())
    piece.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(200, 0), QgsPointXY(150, 0)]))
    lyr.addFeature(piece)
    new_id = piece.id()
    lyr.selectByIds([2, new_id])
    RangeEngine(lyr, cfg).run(True, {"method": C.M_PROPORTIONAL, "proportional_source": C.PROP_EXISTING,
                                    "overwrite_existing": True})
    assert ranges(lyr, 2) == (201, 249, 200, 248, "O", "E")
    assert ranges(lyr, new_id) == (251, 299, 250, 298, "O", "E")


def test_redistribute_engine_anchored():
    lyr, cfg = make_layer()
    RangeEngine(lyr, cfg).run(False)
    _use_points(cfg, _point_layer([("161", 30, 8, "MAIN ST")]))   # on fid 1, 30 m from its start
    lyr.selectByIds([1, 2])
    RangeEngine(lyr, cfg).run(True, {"method": C.M_PROPORTIONAL, "proportional_source": C.PROP_EXISTING})
    # chain extent 100..299 over 200 m; 161 must stay on fid 1
    assert ranges(lyr, 1)[0] <= 161 <= ranges(lyr, 1)[1]
    assert ranges(lyr, 2)[1] == 299


def test_validator_only_overlaps():
    lyr, cfg = make_layer()
    RangeEngine(lyr, cfg).run(False)
    lyr.changeAttributeValues(3, {FIELDS.index("FromAddr_L"): 251})
    issues = Validator(lyr, cfg).run(False, only={"check_overlaps"})
    assert {i.code for i in issues} == {"OVERLAP"}
    from nena_rcl_tools.core.rules import overlap_fids
    assert overlap_fids(issues) == [2, 3]


def test_template_fields_added_with_template_names():
    lyr, cfg = make_layer()
    cfg.template = TPL.from_json_text('{"name": "St", "fields": ['
                                      '{"name": "SEGMENT_NGUID", "nena": "RCL_NGUID"},'
                                      '{"name": "ESN_LEFT", "nena": "ESN_L", "length": 5},'
                                      '{"name": "STATE_ID", "req": "M"}]}')
    cfg.mapping.pop("RCL_NGUID")
    added = add_missing_fields(lyr, cfg, ["RCL_NGUID", "ESN_L", "STATE_ID"])
    # the layer already has a NENA-named RCL_NGUID field -> it is re-used, not duplicated
    assert added == ["ESN_LEFT", "STATE_ID"] and cfg.mapping["RCL_NGUID"] == "RCL_NGUID"
    assert cfg.mapping["ESN_L"] == "ESN_LEFT" and cfg.mapping["STATE_ID"] == "STATE_ID"
    issues = Validator(lyr, cfg).run(False, only={"check_mandatory"})
    assert any(i.code == "MANDATORY_EMPTY" and "STATE_ID" in i.message for i in issues)


def test_template_from_vector_layer():
    lyr, _cfg = make_layer()
    tpl = TPL.from_vector_layer(lyr, "x.gpkg")
    assert [f["name"] for f in tpl["fields"]][:3] == ["St_Name", "St_PosTyp", "FromAddr_L"]
    assert tpl["fields"][2]["type"] == "int"


# ---------------------------------------------------------------- new road creates an intersection
def _add_road(lyr, pts, name="ELM", topo_vertex_on=None):
    """Simulate the Add Line Feature tool (optionally with topological editing)."""
    lyr.beginEditCommand("Feature added")
    if topo_vertex_on is not None:     # topological editing inserts a vertex into the existing line
        fid, x = topo_vertex_on
        g = lyr._feats[fid].geometry().asPolyline()
        lyr.changeGeometry(fid, QgsGeometry.fromPolylineXY([g[0], QgsPointXY(x, 0), g[-1]]))
    f = QgsFeature()
    f.setAttributes([name, "St"] + [None] * 8)
    f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(*p) for p in pts]))
    lyr.addFeature(f)
    lyr.endEditCommand()
    return f.id()


def _setup_new_road_layer(**edit):
    lyr, cfg = make_layer()
    for k, v in edit.items():
        setattr(cfg.edit, k, v)
    C.save_to_layer(lyr, cfg)
    RangeEngine(lyr, cfg).run(False)
    lyr.startEditing()
    return lyr, cfg, LayerWatcher(lyr, lambda *a: None)


def _pieces_of(lyr, x0, x1):
    """Main St features fully inside [x0, x1], ordered west->east."""
    out = []
    for fid, f in lyr._feats.items():
        pl = f.geometry().asPolyline()
        if f.attribute(0) == "MAIN" and x0 - 1e-6 <= pl[0].x() and pl[-1].x() <= x1 + 1e-6:
            out.append((pl[0].x(), fid))
    return [fid for _, fid in sorted(out)]


def test_new_road_t_intersection_splits_and_continues_numbering():
    lyr, cfg, w = _setup_new_road_layer()
    new_road = _add_road(lyr, [(150, 50), (150, 0)])            # ends on the middle of fid 2
    assert lyr.commands[-1] == "NENA: split at new intersection"
    first, second = _pieces_of(lyr, 100, 200)
    assert first == 2
    assert ranges(lyr, first)[:4] == (201, 249, 200, 248)
    assert ranges(lyr, second)[:4] == (251, 299, 250, 298)      # continues from the previous section
    assert ranges(lyr, 3)[:2] == (301, 399)                      # downstream untouched
    # (the original fid 2 had no NGUID in this fixture) the new section and the new road get fresh ones
    nguids = {str(lyr._feats[x].attribute(FIELDS.index("RCL_NGUID"))) for x in (second, new_road)}
    assert len(nguids) == 2 and all(n.startswith("urn:emergency") for n in nguids)
    assert len(lyr._feats[new_road].geometry().asPolyline()) == 2  # new road itself not split (T)
    w.detach()


def test_new_road_crossing_splits_both():
    lyr, cfg, w = _setup_new_road_layer()
    _add_road(lyr, [(250, 50), (250, -50)])                       # crosses fid 3 at x=250
    a, b = _pieces_of(lyr, 200, 300)
    assert ranges(lyr, a)[:2] == (301, 349) and ranges(lyr, b)[:2] == (351, 399)
    elm = [f for f in lyr._feats.values() if f.attribute(0) == "ELM"]
    assert len(elm) == 2                                          # new road split at the crossing too
    w.detach()


def test_new_road_crossing_option_off_only_t():
    lyr, cfg, w = _setup_new_road_layer(split_at_crossings=False)
    _add_road(lyr, [(250, 50), (250, -50)])
    assert _pieces_of(lyr, 200, 300) == [3]
    w.detach()


def test_new_road_option_off():
    lyr, cfg, w = _setup_new_road_layer(split_on_new_intersection=False)
    _add_road(lyr, [(150, 50), (150, 0)])
    assert _pieces_of(lyr, 100, 200) == [2]
    w.detach()


def test_new_road_with_topological_editing_is_not_a_split_piece():
    lyr, cfg, w = _setup_new_road_layer(auto_rerange_split=True)
    new_road = _add_road(lyr, [(150, 50), (150, 0)], topo_vertex_on=(2, 150.0))
    first, second = _pieces_of(lyr, 100, 200)
    assert ranges(lyr, first)[:2] == (201, 249) and ranges(lyr, second)[:2] == (251, 299)
    assert ranges(lyr, new_road)[0] is None                       # the new road was not "re-ranged"
    w.detach()


def test_new_road_extension_does_not_split():
    lyr, cfg, w = _setup_new_road_layer()
    _add_road(lyr, [(300, 0), (400, 0)], name="MAIN")
    assert _pieces_of(lyr, 0, 300) == [1, 2, 3]
    w.detach()
