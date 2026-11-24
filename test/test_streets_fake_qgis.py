# -*- coding: utf-8 -*-
"""Master street names against the fake QGIS (see test_engines_fake_qgis.py)."""


import pytest

import qgis.core  # noqa: E402

if "fake_qgis" not in (getattr(qgis.core, "__file__", "") or ""):
    pytest.skip("real QGIS available - fake-QGIS tests skipped", allow_module_level=True)


from qgis.core import QgsField, QgsProject, QgsVectorLayer  # noqa: E402

from nena_addressing_and_rcl_tools.core import config as C  # noqa: E402
from nena_addressing_and_rcl_tools.core.layer_access import ConfigError  # noqa: E402
from nena_addressing_and_rcl_tools.core.project_config import (ProjectConfig, load_project_config, remember,  # noqa: E402
                                                save_project_config)
from nena_addressing_and_rcl_tools.core.streets import names as N  # noqa: E402
from nena_addressing_and_rcl_tools.core.streets.repository import MASTER_SCHEMA  # noqa: E402
from nena_addressing_and_rcl_tools.core.streets.service import NameConflictError, StreetService  # noqa: E402
from nena_addressing_and_rcl_tools.core.streets.sync import StreetWatcherManager  # noqa: E402

RCL_FIELDS = ["St_PreDir", "St_Name", "St_PosTyp", "StreetID", "DateUpdate", "MSAGComm_L"]
ADDR_FIELDS = ["Add_Number", "St_PreDir", "St_Name", "St_PosTyp", "StreetID"]


def _layer(name, fields, rows, geom=True):
    lyr = QgsVectorLayer("x", name)
    for f in fields:
        lyr.addAttribute(QgsField(f, "datetime" if f == "DateUpdate" else
                                  "int" if f == "Add_Number" else "string"))
    for i, r in enumerate(rows):
        lyr.add_committed(r, [(i * 100, 0), (i * 100 + 100, 0)] if geom else None)
    QgsProject.instance().addMapLayer(lyr)
    return lyr


def _master():
    lyr = QgsVectorLayer("None", "street_names")
    for name, kind, _l in MASTER_SCHEMA:
        lyr.addAttribute(QgsField(name, kind))
    QgsProject.instance().addMapLayer(lyr)
    return lyr


def setup(**settings):
    rcl = _layer("rcl", RCL_FIELDS, [
        [None, "Main", "St", None, None, "TOWN A"],
        [None, "MAIN", "ST", None, None, "TOWN A"],
        ["N", "Main", "Street", None, None, "TOWN A"],      # different road: North Main
        [None, "Smyth", "Rd", None, None, "TOWN B"],
        [None, "Smith", "Rd", None, None, "TOWN B"],
        [None, None, None, None, None, "TOWN B"],          # unnamed
    ])
    addr = _layer("addr", ADDR_FIELDS, [[101, None, "main", "st", None], [205, "North", "Main", "St", None]],
                  geom=False)
    master = _master()
    pcfg = ProjectConfig()
    for role, lyr in (("master", master), ("centerline", rcl), ("address", addr)):
        remember(pcfg.streets, role, lyr)
    for k, v in settings.items():
        setattr(pcfg.streets, k, v)
    save_project_config(pcfg)
    cfg = C.PluginConfig(mapping={f: f for f in RCL_FIELDS})
    C.save_to_layer(rcl, cfg)
    return rcl, addr, master, StreetService(load_project_config())


def attr(lyr, fid, name):
    v = lyr._feats[fid].attribute(lyr.fields().lookupField(name))
    return getattr(v, "v", v) if hasattr(v, "isNull") else v


def test_build_from_centerlines_groups_variants_and_links():
    rcl, addr, master, svc = setup()
    analysis = svc.analyse_centerlines()
    assert analysis.unnamed == 1
    assert {g.key for g in analysis.groups if g.is_variant} == {"MAIN STREET"}
    rep = svc.build_from_centerlines(rewrite_spellings=True)
    assert rep.created == 4                         # Main St, North Main Street, Smyth Rd, Smith Rd
    assert rep.linked_centerlines == 5 and rep.linked_addresses == 2
    assert len(rep.soundalikes) == 1
    # variants rewritten to the chosen spelling, both linked to the same StreetID
    assert attr(rcl, 2, "St_Name") == "Main" and attr(rcl, 2, "St_PosTyp") == "St"
    assert attr(rcl, 1, "StreetID") == attr(rcl, 2, "StreetID") != attr(rcl, 3, "StreetID")
    assert attr(addr, 1, "StreetID") == attr(rcl, 1, "StreetID") and attr(addr, 1, "St_Name") == "Main"
    assert attr(addr, 2, "StreetID") == attr(rcl, 3, "StreetID")
    # running it again reuses the records instead of duplicating them
    rep2 = svc.build_from_centerlines()
    assert rep2.created == 0 and rep2.reused == 4 and len(master._feats) == 4


def test_build_ignores_a_road_with_no_street_name():
    rcl, _addr, master, svc = setup()
    rcl.add_committed([None, None, "Rd", None, None, "TOWN A"], [(600, 0), (700, 0)])
    rcl.add_committed(["North", " ", None, None, None, "TOWN A"], [(700, 0), (800, 0)])
    report = svc.analyse_centerlines()
    assert report.unnamed == 3
    built = svc.build_from_centerlines()
    assert built.created == 4
    assert all(N.clean(r.parts.get("St_Name")) for r in svc.repo.records())
    assert svc.repo.add([N.StreetRecord("{EMPTY}", N.parts_from({"St_PosTyp": "St"}))]) == []
    assert len(master._feats) == 4
    assert any(i.code == "STREET_NAME_EMPTY" for i in svc.master_issues()) is False


def test_import_skips_a_blank_street_name():
    from qgis.core import Qgis
    from nena_addressing_and_rcl_tools.core.importer import import_layer
    src = QgsVectorLayer("None", "src")
    dest = QgsVectorLayer("None", "street names")
    src._wkb = dest._wkb = Qgis.WkbType.NoGeometry
    for lyr in (src, dest):
        lyr.addAttribute(QgsField("St_Name", "string"))
        lyr.addAttribute(QgsField("St_PosTyp", "string"))
    src.add_committed(["Main", "St"], None)
    src.add_committed([None, "Rd"], None)
    src.add_committed(["", ""], None)
    result = import_layer(src, dest, {"St_Name": "St_Name", "St_PosTyp": "St_PosTyp"})
    assert result["written"] == 1 and result["blank"] == 2
    names = []
    for f in dest.getFeatures():
        value = f.attribute(dest.fields().lookupField("St_Name"))
        names.append(getattr(value, "v", value))
    assert names == ["Main"]


def test_import_treats_zero_coordinates_as_not_recorded():
    from qgis.core import Qgis
    from nena_addressing_and_rcl_tools.core.importer import import_layer
    src = QgsVectorLayer("None", "src")
    dest = QgsVectorLayer("None", "addresses")
    src._wkb = dest._wkb = Qgis.WkbType.NoGeometry
    for lyr in (src, dest):
        lyr.addAttribute(QgsField("St_Name", "string"))
        lyr.addAttribute(QgsField("Longitude", "double"))
        lyr.addAttribute(QgsField("Latitude", "double"))
    src.add_committed(["Main", -82.6, 30.2], None)
    src.add_committed(["Oak", 0.0, 0.0], None)
    result = import_layer(src, dest, {"St_Name": "St_Name", "Longitude": "Longitude", "Latitude": "Latitude"})
    assert result["written"] == 2 and result["zero_coordinates"] == 1
    lon = dest.fields().lookupField("Longitude")
    values = [getattr(v, "v", v) for v in (f.attribute(lon) for f in dest.getFeatures())]
    assert values[0] == -82.6 and values[1] is None


def test_import_fills_empty_coordinates_from_the_point():
    from qgis.core import QgsGeometry, QgsPointXY
    from nena_addressing_and_rcl_tools.core.importer import coordinates_to_write, import_layer
    assert coordinates_to_write(None, None, (1, 2), lambda _xy: (-82.1, 29.6))[:2] == (-82.1, 29.6)
    assert coordinates_to_write(-82.6, 30.2, (1, 2), lambda _xy: (0, 0))[:2] == (-82.6, 30.2)
    src = QgsVectorLayer("Point", "src")
    dest = QgsVectorLayer("Point", "addresses")
    src._wkb = dest._wkb = 1
    for lyr in (src, dest):
        lyr.addAttribute(QgsField("Longitude", "double"))
        lyr.addAttribute(QgsField("Latitude", "double"))
    kept = src.add_committed([-82.6, 30.2], None)
    blank = src.add_committed([None, None], None)
    zero = src.add_committed([0.0, 0.0], None)
    src._feats[kept].setGeometry(QgsGeometry.fromPointXY(QgsPointXY(1, 2)))
    src._feats[blank].setGeometry(QgsGeometry.fromPointXY(QgsPointXY(500000, 3000000)))
    src._feats[zero].setGeometry(QgsGeometry.fromPointXY(QgsPointXY(400000, 3200000)))
    result = import_layer(src, dest, {"Longitude": "Longitude", "Latitude": "Latitude"})
    assert result["coordinates_filled"] == 2 and result["zero_coordinates"] == 1
    lon = dest.fields().lookupField("Longitude")
    values = [getattr(v, "v", v) for v in (f.attribute(lon) for f in dest.getFeatures())]
    assert values[0] == -82.6 and values[1] == 500000 and values[2] == 400000


def test_rename_cascades_and_keeps_alias():
    rcl, addr, master, svc = setup()
    svc.build_from_centerlines()
    sid = attr(rcl, 1, "StreetID")
    assert svc.rename_preview(sid) == (2, 1)
    res = svc.rename(sid, N.parts_from({"St_Name": "Market", "St_PosTyp": "St"}))
    assert res["centerline"] == 2 and res["address"] == 1
    assert attr(rcl, 1, "St_Name") == attr(rcl, 2, "St_Name") == attr(addr, 1, "St_Name") == "Market"
    assert attr(rcl, 3, "St_Name") == "Main"                                    # North Main untouched
    recs = svc.repo.records()
    alias = [r for r in recs if r.alias_of == sid]
    assert len(alias) == 1 and alias[0].status == "Retired" and alias[0].full_name == "Main St"
    assert attr(rcl, 1, "DateUpdate") is not None
    # the old name is free again (retired), a duplicate of another active name is blocked
    with pytest.raises(NameConflictError):
        svc.rename(sid, N.parts_from({"St_PreDir": "North", "St_Name": "Main", "St_PosTyp": "St"}))


def test_uniqueness_county_wide_vs_scope_and_type_rule():
    rcl, addr, master, svc = setup()
    svc.build_from_centerlines()
    with pytest.raises(NameConflictError):                 # county-wide, full name
        svc.add_street(N.parts_from({"St_Name": "Main", "St_PosTyp": "Street"}), scope="ELSEWHERE")
    rec, warnings = svc.add_street(N.parts_from({"St_Name": "Main", "St_PosTyp": "Ave"}))   # type differs: allowed
    assert rec.street_id and [w.kind for w in warnings] == ["soundalike"]
    svc.rule.strictness = N.STRICT_IGNORE_TYPE
    with pytest.raises(NameConflictError):
        svc.add_street(N.parts_from({"St_Name": "Main", "St_PosTyp": "Ct"}))
    svc.rule.action = "warn"
    rec, warnings = svc.add_street(N.parts_from({"St_Name": "Main", "St_PosTyp": "Ct"}))
    assert any(w.kind == "duplicate" for w in warnings)
    with pytest.raises(ConfigError):
        svc.add_street(N.parts_from({"St_PosTyp": "St"}))   # no name


def test_scope_field_mode_builds_separate_records():
    rcl, addr, master, svc = setup(scope_mode="field", scope_field="MSAGComm_L")
    rep = svc.build_from_centerlines()
    assert {r.scope for r in svc.repo.records()} == {"TOWN A", "TOWN B"}
    assert rep.linked_addresses == 0          # address layer has no scope field -> scope "" -> no match


def test_assign_sync_and_name_issues():
    rcl, addr, master, svc = setup()
    svc.build_from_centerlines()
    sid_smith = attr(rcl, 5, "StreetID")
    assert svc.assign(sid_smith, [4]) == 1
    assert attr(rcl, 4, "St_Name") == "Smith" and attr(rcl, 4, "StreetID") == sid_smith
    # someone edits a centerline name by hand -> out of sync
    rcl.changeAttributeValues(1, {rcl.fields().lookupField("St_Name"): "Mian"})
    codes = {(i.code, i.fid) for i in svc.name_issues("centerline")}
    assert ("NAME_OUT_OF_SYNC", 1) in codes
    assert svc.sync()["centerline"] == 1 and attr(rcl, 1, "St_Name") == "Main"
    assert not [i for i in svc.name_issues("centerline") if i.code == "NAME_OUT_OF_SYNC"]
    rcl.changeAttributeValues(6, {rcl.fields().lookupField("St_Name"): "Pine"})
    assert ("STREETID_MISSING", 6) in {(i.code, i.fid) for i in svc.name_issues("centerline")}
    assert any(i.code == "NAME_SOUNDALIKE" for i in svc.master_issues())


def test_live_watchers_cascade_master_edit_and_copy_on_assign():
    rcl, addr, master, svc = setup()
    svc.build_from_centerlines()
    notes = []
    mgr = StreetWatcherManager(lambda lvl, t: notes.append(t))
    sid_main = attr(rcl, 1, "StreetID")
    rec = svc.repo.by_id()[sid_main]
    # user types a new name straight into the master table
    master.changeAttributeValues(rec.fid, {master.fields().lookupField("St_Name"): "Market"})
    assert attr(rcl, 1, "St_Name") == attr(addr, 1, "St_Name") == "Market"
    assert attr(master, rec.fid, "FullName") == "Market St"
    # user picks a different street in a centerline form
    sid_smith = attr(rcl, 5, "StreetID")
    rcl.changeAttributeValues(2, {rcl.fields().lookupField("StreetID"): sid_smith})
    assert attr(rcl, 2, "St_Name") == "Smith" and attr(rcl, 2, "St_PosTyp") == "Rd"
    mgr.unload()


def test_street_list_rows_sort_and_filter():
    from nena_addressing_and_rcl_tools.core.streets.listing import build_rows, row_visible, sort_key
    recs = [N.StreetRecord("{2}", N.parts_from({"St_Name": "Oak", "St_PosTyp": "St"})),
            N.StreetRecord("{1}", N.parts_from({"St_Name": "Main", "St_PosTyp": "St"})),
            N.StreetRecord("{3}", N.parts_from({"St_Name": "Elm", "St_PosTyp": "Ave"}), status=N.STATUS_RETIRED)]
    rows = build_rows(recs, {"{1}": (3, 12), "{2}": (1, 0)})
    assert [r.street_id for r in rows] == ["{3}", "{1}", "{2}"]          # by name
    assert (rows[1].segments, rows[1].addresses) == (3, 12) and (rows[0].segments, rows[0].addresses) == (0, 0)
    assert not row_visible(rows[0], "", False) and row_visible(rows[0], "", True)   # retired
    assert row_visible(rows[1], " mai ", False) and not row_visible(rows[2], "mai", False)
    assert sort_key(rows[1], 3) == 3 and sort_key(rows[1], 0) == "MAIN ST"
