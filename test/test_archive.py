# -*- coding: utf-8 -*-
"""Pure tests for archive rows and archive project settings."""

import os
import sys
import unittest

_PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PARENT = os.path.dirname(_PLUGIN)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from nena_addressing_manager.core.archive_model import (  # noqa: E402
    assign_versions, roles_missing_archive, stored_change)
from nena_addressing_manager.core.project_config import ProjectConfig  # noqa: E402


class ArchiveModelTests(unittest.TestCase):
    def test_insert_update_and_delete_choose_the_stored_feature(self):
        added = stored_change(
            "insert", {}, {"FromAddr_L": 100, "RCL_NGUID": "abc"},
            feature_key_fields=["RCL_NGUID"], when="2026-11-15 12:00:00", who="ada", source_fid=4)
        self.assertEqual(added["ChangeType"], "insert")
        self.assertEqual(added["FromAddr_L"], 100)
        self.assertEqual(added["FeatureKey"], "abc")
        self.assertEqual(added["SourceFid"], "4")
        self.assertEqual(added["ChangedFields"], "")
        self.assertEqual(added["ChangedBy"], "ada")
        self.assertEqual(added["OldValues"], "")
        self.assertEqual(added["NewValues"], "")
        self.assertIsNone(added["Version"])
        self.assertTrue(added["ArchiveID"])

        retired = stored_change(
            "update", {"FromAddr_L": 100, "RCL_NGUID": "abc"}, {"FromAddr_L": 200, "RCL_NGUID": "abc"},
            feature_key_fields=["RCL_NGUID"], source_fid=4)
        self.assertEqual(retired["ChangeType"], "update")
        self.assertEqual(retired["FromAddr_L"], 200)
        self.assertEqual(retired["FeatureKey"], "abc")
        self.assertIn("FromAddr_L", retired["ChangedFields"])
        self.assertNotIn("RCL_NGUID", retired["ChangedFields"])
        self.assertEqual(retired["OldValues"], '{"FromAddr_L": 100}')
        self.assertEqual(retired["NewValues"], '{"FromAddr_L": 200}')

        removed = stored_change(
            "delete", {"FromAddr_L": 200, "StreetID": "s1"}, {},
            feature_key_fields=["StreetID"], source_fid=4)
        self.assertEqual(removed["ChangeType"], "delete")
        self.assertEqual(removed["FromAddr_L"], 200)
        self.assertEqual(removed["FeatureKey"], "s1")

    def test_update_skips_a_row_when_nothing_changed(self):
        self.assertIsNone(stored_change("update", {"A": 1}, {"A": 1}))
        self.assertIsNone(stored_change("update", {"A": None}, {"A": "  "}))
        self.assertIsNone(stored_change("update", {"A": 1}, {"A": 1.0}))
        moved = stored_change("update", {"A": 1}, {"A": 1}, geometry_changed=True)
        self.assertEqual(moved["ChangedFields"], "geometry")
        self.assertEqual(moved["A"], 1)

    def test_live_field_keeps_its_value_when_the_name_clashes(self):
        row = stored_change(
            "update",
            {"ChangeType": "keep", "FromAddr_L": 1},
            {"ChangeType": "keep", "FromAddr_L": 2},
            live_names=["ChangeType", "FromAddr_L"], source_fid=5)
        self.assertEqual(row["ChangeType"], "keep")
        self.assertEqual(row["FromAddr_L"], 2)
        self.assertIn("ArchiveID", row)
        self.assertIn("FromAddr_L", row["ChangedFields"])
        self.assertIn("1", row["OldValues"])
        self.assertIn("2", row["NewValues"])

    def test_changed_fields_stay_within_254_characters(self):
        before = {f"Field{i}": 0 for i in range(40)}
        after = {name: 1 for name in before}
        row = stored_change("update", before, after)
        self.assertLessEqual(len(row["ChangedFields"]), 254)
        self.assertTrue(row["ChangedFields"].endswith("..."))

    def test_versions_follow_one_feature_and_keep_a_renamed_key(self):
        first = stored_change(
            "insert", {}, {"FromAddr_L": 100, "RCL_NGUID": "abc"},
            feature_key_fields=["RCL_NGUID"], source_fid=4)
        second = stored_change(
            "update", {"FromAddr_L": 100, "RCL_NGUID": "abc"}, {"FromAddr_L": 200, "RCL_NGUID": "abc"},
            feature_key_fields=["RCL_NGUID"], source_fid=4)
        assign_versions([first, second], {})
        self.assertEqual(first["Version"], 1)
        self.assertEqual(second["Version"], 2)

        again = stored_change(
            "update", {"FromAddr_L": 200, "RCL_NGUID": "abc"}, {"FromAddr_L": 300, "RCL_NGUID": "abc"},
            feature_key_fields=["RCL_NGUID"], source_fid=4)
        assign_versions([again], {"k:abc": 2, "f:4": 2})
        self.assertEqual(again["Version"], 3)

        renamed = stored_change(
            "update", {"RCL_NGUID": "abc", "FromAddr_L": 1}, {"RCL_NGUID": "def", "FromAddr_L": 1},
            feature_key_fields=["RCL_NGUID"], source_fid=4)
        self.assertEqual(renamed["FeatureKey"], "def")
        self.assertIn("RCL_NGUID", renamed["ChangedFields"])
        assign_versions([renamed], {"k:abc": 4})
        self.assertEqual(renamed["Version"], 5)

    def test_a_live_version_field_is_not_renumbered(self):
        row = stored_change(
            "update", {"Version": 9, "A": 1}, {"Version": 9, "A": 2},
            live_names=["Version", "A"])
        self.assertEqual(row["Version"], 9)
        self.assertFalse(row["_track_version"])
        assign_versions([row], {})
        self.assertEqual(row["Version"], 9)

    def test_old_and_new_values_stay_within_4000_characters(self):
        before = {"Notes": "x" * 5000}
        after = {"Notes": "y" * 5000}
        row = stored_change("update", before, after)
        self.assertLessEqual(len(row["OldValues"]), 4000)
        self.assertLessEqual(len(row["NewValues"]), 4000)

    def test_missing_archive_roles(self):
        self.assertEqual(
            roles_missing_archive({"centerline": True, "streets": True}, {"centerline": True}),
            ["Street names"])
        self.assertEqual(roles_missing_archive({"centerline": True}, {"centerline": True}), [])
        self.assertEqual(roles_missing_archive({}, {}), [])

    def test_project_config_round_trip(self):
        cfg = ProjectConfig()
        cfg.archive.enabled = True
        cfg.archive.centerline_layer_id = "abc"
        cfg.archive.streets_layer_source = "gpkg"
        again = ProjectConfig.from_json(cfg.to_json())
        self.assertTrue(again.archive.enabled)
        self.assertEqual(again.archive.centerline_layer_id, "abc")
        self.assertEqual(again.archive.streets_layer_source, "gpkg")
        self.assertFalse(again.archive.address_layer_id)
        self.assertEqual(again.msag.street_field, "")
        cfg.msag.street_field = "STREET"
        again = ProjectConfig.from_json(cfg.to_json())
        self.assertEqual(again.msag.street_field, "STREET")
        legacy = ProjectConfig.from_dict({"streets": {"stamp_date_update": False}})
        self.assertFalse(legacy.archive.enabled)
        self.assertFalse(legacy.streets.stamp_date_update)

    def test_write_rows_numbers_versions_and_stores_the_saved_feature(self):
        import qgis.core
        if "fake_qgis" not in (getattr(qgis.core, "__file__", "") or ""):
            self.skipTest("fake QGIS is not loaded")
        from qgis.core import QgsField, QgsVectorLayer

        from nena_addressing_manager.core.archive_watch import write_rows

        lyr = QgsVectorLayer("x", "RoadCenterline_Versions")
        lyr._wkb = 100
        lyr.addAttribute(QgsField("FromAddr_L", "int"))
        lyr.addAttribute(QgsField("RCL_NGUID", "string"))
        item = {
            "key_fields": ["RCL_NGUID"],
            "live_names": ["FromAddr_L", "RCL_NGUID"],
            "who": "ada",
        }
        added = write_rows(lyr, dict(item, rows=[{
            "change_type": "insert",
            "before": {},
            "after": {"FromAddr_L": 100, "RCL_NGUID": "abc"},
            "geometry_changed": False,
            "fid": 4,
        }]))
        changed = write_rows(lyr, dict(item, rows=[{
            "change_type": "update",
            "before": {"FromAddr_L": 100, "RCL_NGUID": "abc"},
            "after": {"FromAddr_L": 200, "RCL_NGUID": "abc"},
            "geometry_changed": False,
            "fid": 4,
        }]))
        self.assertEqual((added, changed), (1, 1))
        self.assertGreaterEqual(lyr.fields().lookupField("Version"), 0)
        self.assertGreaterEqual(lyr.fields().lookupField("OldValues"), 0)
        fields = lyr.fields()

        def val(feat, name):
            return feat.attribute(fields.lookupField(name))

        rows = list(lyr.getFeatures())
        self.assertEqual(len(rows), 2)
        self.assertEqual(val(rows[0], "Version"), 1)
        self.assertEqual(val(rows[0], "FromAddr_L"), 100)
        self.assertEqual(val(rows[0], "ChangeType"), "insert")
        self.assertEqual(val(rows[1], "Version"), 2)
        self.assertEqual(val(rows[1], "FromAddr_L"), 200)
        self.assertEqual(val(rows[1], "ChangeType"), "update")
        self.assertEqual(val(rows[1], "OldValues"), '{"FromAddr_L": 100}')
        self.assertEqual(val(rows[1], "NewValues"), '{"FromAddr_L": 200}')
        self.assertEqual(val(rows[1], "FeatureKey"), "abc")


if __name__ == "__main__":
    unittest.main()
