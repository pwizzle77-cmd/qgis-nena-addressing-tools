# -*- coding: utf-8 -*-
"""Pure tests for reading a feature's history and planning a roll back."""

import os
import sys
import unittest

_PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PARENT = os.path.dirname(_PLUGIN)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from nena_addressing_and_rcl_tools.core import history as H  # noqa: E402
from nena_addressing_and_rcl_tools.core.archive_model import stored_change  # noqa: E402

KEYS = ["RCL_NGUID"]


def row(change, before, after, version, fid=4, when="2026-11-01 10:00:00", geometry_changed=False):
    attrs = stored_change(change, before, after, feature_key_fields=KEYS, source_fid=fid,
                          when=when, who="ada", geometry_changed=geometry_changed)
    attrs["Version"] = version
    for key in list(attrs):
        if key.startswith("_"):
            attrs.pop(key)
    return attrs


class HistoryTests(unittest.TestCase):
    def setUp(self):
        v1 = {"RCL_NGUID": "R1", "St_Name": "Main", "FromAddr_L": 1}
        v2 = dict(v1, St_Name="Maine", FromAddr_L=101)
        v3 = dict(v2, St_Name="Main")
        self.rows = [
            row("insert", {}, v1, 1),
            row("update", v1, v2, 2, when="2026-11-02 10:00:00", geometry_changed=True),
            row("update", v2, v3, 3, when="2026-11-03 10:00:00"),
            row("insert", {}, {"RCL_NGUID": "OTHER", "St_Name": "Oak"}, 1, fid=9),
        ]
        self.entries = [H.entry_from_row(r, row_id=i) for i, r in enumerate(self.rows)]

    def test_entry_reads_json_and_meta(self):
        entry = self.entries[1]
        self.assertEqual(entry.version, 2)
        self.assertEqual(entry.change_type, "update")
        self.assertEqual(entry.who, "ada")
        self.assertEqual(entry.old_values, {"FromAddr_L": 1, "St_Name": "Main"})
        self.assertEqual(entry.new_values, {"FromAddr_L": 101, "St_Name": "Maine"})
        self.assertTrue(entry.geometry_changed)
        self.assertNotIn("OldValues", entry.values)
        self.assertNotIn("Version", entry.values)
        self.assertEqual(entry.values["St_Name"], "Maine")

    def test_feature_entries_by_key_oldest_first(self):
        found = H.feature_entries(reversed(self.entries), "R1", 4, KEYS)
        self.assertEqual([e.version for e in found], [1, 2, 3])

    def test_reused_fid_with_other_key_is_not_taken(self):
        other = H.entry_from_row(row("insert", {}, {"RCL_NGUID": "OLD", "St_Name": "Gone"}, 1, fid=4))
        found = H.feature_entries(self.entries + [other], "R1", 4, KEYS)
        self.assertNotIn(other, found)

    def test_renamed_key_follows_earlier_rows(self):
        before = {"RCL_NGUID": "R1", "St_Name": "Main"}
        after = dict(before, RCL_NGUID="R1-NEW")
        renamed = H.entry_from_row(row("update", before, after, 4))
        found = H.feature_entries(self.entries + [renamed], "R1-NEW", 4, KEYS)
        self.assertEqual([e.version for e in found], [1, 2, 3, 4])

    def test_no_key_matches_by_fid(self):
        plain = [H.entry_from_row(row("insert", {}, {"St_Name": "A"}, 1, fid=7))]
        self.assertEqual(len(H.feature_entries(plain, "", 7, KEYS)), 1)
        self.assertEqual(H.feature_entries(plain, "", 8, KEYS), [])

    def test_field_changes_from_json(self):
        changes = H.field_changes(self.entries[1], self.entries[0])
        by_name = {c.name: (c.old, c.new) for c in changes}
        self.assertEqual(by_name["St_Name"], ("Main", "Maine"))
        self.assertEqual(by_name["FromAddr_L"], (1, 101))
        self.assertIn("geometry", by_name)

    def test_field_changes_fall_back_when_json_is_cut_off(self):
        entry = self.entries[2]
        entry.values_ok = False
        entry.old_values = {}
        entry.new_values = {}
        changes = H.field_changes(entry, self.entries[1])
        self.assertEqual([(c.name, c.old, c.new) for c in changes], [("St_Name", "Maine", "Main")])

    def test_cut_off_json_is_flagged(self):
        attrs = dict(self.rows[1], NewValues='{"St_Name": "Mai')
        self.assertFalse(H.entry_from_row(attrs).values_ok)

    def test_insert_and_delete_list_values(self):
        added = H.field_changes(self.entries[0])
        self.assertIn(("St_Name", None, "Main"), [(c.name, c.old, c.new) for c in added])
        gone = H.entry_from_row(row("delete", {"RCL_NGUID": "R1", "St_Name": "Main"}, {}, 5))
        self.assertIn(("St_Name", "Main", None), [(c.name, c.old, c.new) for c in H.field_changes(gone)])

    def test_rollback_lists_only_differing_live_fields(self):
        current = {"RCL_NGUID": "R1", "St_Name": "Main", "FromAddr_L": 101, "LastUpdatedBy": "bob", "fid": 4}
        live = ["fid", "RCL_NGUID", "St_Name", "FromAddr_L", "LastUpdatedBy", "NotStored"]
        target = dict(self.entries[0].values, LastUpdatedBy="ada")
        self.entries[0].values = target
        plan = H.rollback_changes(self.entries[0], current, live, keep=["LastUpdatedBy"])
        self.assertEqual([(c.name, c.old, c.new) for c in plan], [("FromAddr_L", 101, 1)])

    def test_rollback_matches_names_without_case(self):
        entry = H.entry_from_row({"st_name": "Elm", "Version": 1, "ChangeType": "insert"})
        plan = H.rollback_changes(entry, {"St_Name": "Oak"}, ["St_Name"])
        self.assertEqual([(c.name, c.old, c.new) for c in plan], [("St_Name", "Oak", "Elm")])

    def test_summary_and_display(self):
        self.assertEqual(H.changes_summary(self.entries[0]), "Feature added")
        self.assertEqual(H.changes_summary(self.entries[1]), "St_Name, FromAddr_L, shape")
        self.assertEqual(H.display_value(None), "")
        self.assertEqual(H.display_value(5.0), "5")
        self.assertIn('"St_Name": "Maine"', H.pretty_json(self.rows[1]["NewValues"]))


if __name__ == "__main__":
    unittest.main()
