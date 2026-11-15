# -*- coding: utf-8 -*-
"""WhoEntered and LastUpdatedBy take the database connection login."""

import pytest

import qgis.core  # noqa: E402

if "fake_qgis" not in (getattr(qgis.core, "__file__", "") or ""):
    pytest.skip("real QGIS available - fake-QGIS tests skipped", allow_module_level=True)

from qgis.core import QgsFeature, QgsField, QgsVectorLayer  # noqa: E402

from nena_rcl_tools.compat import py_value  # noqa: E402
from nena_rcl_tools.core.editor_user import connection_login, stamp_editor_user, uri_username  # noqa: E402


def _val(lyr, fid, name):
    index = lyr.fields().lookupField(name)
    value = lyr._feats[fid].attribute(index)
    return py_value(value)


def _db_layer(names, uri):
    lyr = QgsVectorLayer("LineString", "roads")
    lyr.providerType = lambda: "postgres"
    lyr.source = lambda: uri
    for name in names:
        lyr.addAttribute(QgsField(name, "string"))
    lyr.startEditing()
    return lyr


class _Buffer:
    def __init__(self, added, changed, deleted=(), geoms=()):
        self._added = added
        self._changed = changed
        self._deleted = list(deleted)
        self._geoms = {fid: object() for fid in geoms}

    def addedFeatures(self):
        return dict(self._added)

    def changedAttributeValues(self):
        return dict(self._changed)

    def changedGeometries(self):
        return dict(self._geoms)

    def deletedFeatureIds(self):
        return list(self._deleted)


def _arm(lyr, buffer):
    lyr.editBuffer = lambda: buffer
    lyr.isModified = lambda: True


def test_uri_username_reads_the_connection_user():
    assert uri_username("dbname='gis' host=localhost user='psap_editor' table=\"public\".\"roads\"") == "psap_editor"
    assert uri_username('uid="COUNTY\\jsmith"') == "COUNTY\\jsmith"
    assert uri_username("username=editor password=secret") == "editor"
    assert uri_username("dbname='gis' host=localhost sslmode=disable") == ""


def test_file_layer_and_a_connection_without_a_login_are_left_alone():
    file_layer = QgsVectorLayer("LineString", "roads")
    file_layer.source = lambda: "user='psap_editor'"
    assert connection_login(file_layer) == ""
    db = _db_layer(["WhoEntered", "LastUpdatedBy"], "dbname='gis' host=localhost")
    assert connection_login(db) == ""
    fid = db.add_committed([None, None], None)
    _arm(db, _Buffer({}, {fid: {0: "x"}}))
    assert stamp_editor_user(db) == 0
    assert _val(db, fid, "LastUpdatedBy") is None


def test_save_fills_an_empty_who_entered_and_refreshes_last_updated_by():
    uri = "dbname='gis' host=localhost port=5432 user='psap_editor' sslmode=disable"
    lyr = _db_layer(["St_Name", "WHO_ENTERED", "LAST_UPDATED_BY"], uri)
    assert connection_login(lyr) == "psap_editor"
    existing = lyr.add_committed(["Main", "ada", "ada"], None)
    copied = lyr.add_committed(["Oak", "ada", None], None)
    blank = lyr.add_committed(["Elm", "  ", "ada"], None)
    lyr.changeAttributeValues(existing, {0: "Main Street"})
    fresh = QgsFeature()
    fresh.setAttributes(["Pine", "  ", None])
    lyr.addFeature(fresh)
    _arm(lyr, _Buffer(
        {fresh.id(): lyr._feats[fresh.id()], copied: lyr._feats[copied]},
        {existing: {0: "Main Street"}, blank: {0: "Elm Street"}},
    ))
    assert stamp_editor_user(lyr) == 4
    assert (_val(lyr, existing, "WHO_ENTERED"), _val(lyr, existing, "LAST_UPDATED_BY")) == ("ada", "psap_editor")
    assert (_val(lyr, fresh.id(), "WHO_ENTERED"), _val(lyr, fresh.id(), "LAST_UPDATED_BY")) == (
        "psap_editor", "psap_editor")
    # an update with an empty creator does not claim the old row
    assert (_val(lyr, blank, "WHO_ENTERED"), _val(lyr, blank, "LAST_UPDATED_BY")) == ("  ", "psap_editor")
    # a new piece that already copied WhoEntered keeps that creator
    assert (_val(lyr, copied, "WHO_ENTERED"), _val(lyr, copied, "LAST_UPDATED_BY")) == ("ada", "psap_editor")


def test_geometry_edit_updates_last_updated_by_and_a_delete_is_skipped():
    uri = "dbname='gis' host=db user='editor'"
    lyr = _db_layer(["WhoEntered", "LastUpdatedBy"], uri)
    lyr.fields().at(1).setLength(4)
    kept = lyr.add_committed(["ada", "old"], None)
    gone = lyr.add_committed(["ada", "old"], None)
    _arm(lyr, _Buffer({}, {}, deleted=[gone], geoms=[kept]))
    assert stamp_editor_user(lyr) == 1
    assert (_val(lyr, kept, "WhoEntered"), _val(lyr, kept, "LastUpdatedBy")) == ("ada", "edit")
    assert (_val(lyr, gone, "WhoEntered"), _val(lyr, gone, "LastUpdatedBy")) == ("ada", "old")
    _arm(lyr, _Buffer({}, {}, geoms=[kept]))
    assert stamp_editor_user(lyr) == 0
