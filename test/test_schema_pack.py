# -*- coding: utf-8 -*-
"""
Pure tests for the fresh schema and the import field match.

Run from the folder that contains the plugin folder:
    python -m unittest nena_addressing_manager.test.test_schema_pack
"""

import os
import sys
import unittest

_PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PARENT = os.path.dirname(_PLUGIN)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from nena_addressing_manager.core.schema import RCL_FIELDS, STREET_NAME_FIELDS  # noqa: E402
from nena_addressing_manager.core.schema_pack import (  # noqa: E402
    ARCHIVE_COLUMNS, SCHEMA_LAYERS, address_defs, archive_field_defs, centerline_defs, convert_value,
    format_import_report, normalize_container_path, qualified_table, sanitize_ident, street_columns,
    street_field_defs, suggest_field_map, unmapped_required)


class SchemaPackTests(unittest.TestCase):
    def test_layers_are_unique(self):
        keys = [s.key for s in SCHEMA_LAYERS]
        tables = [s.table for s in SCHEMA_LAYERS]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(tables), len(set(tables)))
        self.assertEqual(keys, ["centerline", "streets", "address", "driveway"])

    def test_street_columns_match_the_repository_formula(self):
        expected = [("StreetID", "string", 38)] + [
            (p, "string", 60 if p == "St_Name" else 35) for p in STREET_NAME_FIELDS] + [
            ("FullName", "string", 254), ("Scope", "string", 100), ("Status", "string", 10),
            ("AliasOf", "string", 38), ("Effective", "datetime", 0), ("Expire", "datetime", 0),
            ("DateUpdate", "datetime", 0), ("WhoEntered", "string", 100),
            ("LastUpdatedBy", "string", 100), ("Notes", "string", 254)]
        self.assertEqual(street_columns(), expected)
        self.assertEqual([d.name for d in street_field_defs()], [n for n, _k, _l in expected])

    def test_centerline_template_keeps_only_its_own_fields(self):
        self.assertEqual([d.name for d in centerline_defs()], [d.name for d in RCL_FIELDS])
        tpl = {"name": "t", "fields": [{"name": "L_F_ADD", "type": "int", "nena": "FromAddr_L"}]}
        defs = centerline_defs(tpl)
        self.assertEqual([d.physical_name for d in defs], ["L_F_ADD"])
        self.assertEqual(defs[0].name, "FromAddr_L")

    def test_address_template_keeps_plugin_link_fields(self):
        tpl = {"fields": [{"name": "Add_Number", "type": "int"}]}
        names = [d.name for d in address_defs(tpl)]
        self.assertIn("Add_Number", names)
        for link in ("StreetID", "RCL_NGUID", "Access_X", "Access_Y", "Addr_Side", "FullName"):
            self.assertIn(link, names)
        full = next(d for d in address_defs() if d.name == "FullName")
        self.assertEqual((full.req, full.ftype, full.length), ("O", "string", 254))

    def test_table_names(self):
        self.assertEqual(sanitize_ident("NG 911"), "NG_911")
        self.assertEqual(sanitize_ident("1road"), "_1road")
        self.assertEqual(qualified_table("RoadCenterline", "ng911"), "ng911_RoadCenterline")
        self.assertEqual(qualified_table("RoadCenterline", ""), "RoadCenterline")
        long_name = qualified_table("RoadCenterline", "p" * 80)
        self.assertEqual(len(long_name), 63)
        self.assertTrue(long_name.startswith("p"))
        archive_name = qualified_table("RoadCenterline", "p" * 80, "Archive")
        self.assertEqual(len(archive_name), 63)
        self.assertTrue(archive_name.endswith("_Archive"))
        self.assertNotEqual(archive_name, long_name)
        self.assertEqual(qualified_table("RoadCenterline", "", "Archive"), "RoadCenterline_Archive")
        self.assertEqual(qualified_table("SiteStructureAddressPoint", "ng911", "Archive"),
                         "ng911_SiteStructureAddressPoint_Archive")
        self.assertEqual(qualified_table("RoadCenterline", "", "Versions"), "RoadCenterline_Versions")
        versions_name = qualified_table("RoadCenterline", "p" * 80, "Versions")
        self.assertEqual(len(versions_name), 63)
        self.assertTrue(versions_name.endswith("_Versions"))

    def test_archive_fields_follow_the_live_fields(self):
        base = centerline_defs()
        defs = archive_field_defs(base)
        names = [d.physical_name for d in defs]
        self.assertEqual(names[:len(base)], [d.physical_name for d in base])
        self.assertEqual(names[len(base):], [col[0] for col in ARCHIVE_COLUMNS])
        self.assertEqual(len(names), len(set(n.lower() for n in names)))
        clash = archive_field_defs(base[:1] + [base[0].__class__(
            "ChangeType", "O", "string", 10, "live value")])
        clash_names = [d.physical_name for d in clash]
        self.assertEqual(clash_names.count("ChangeType"), 1)
        self.assertIn("ArchiveID", clash_names)

    def test_container_path(self):
        self.assertTrue(normalize_container_path("gpkg", "city").lower().endswith(".gpkg"))
        self.assertTrue(normalize_container_path("gdb", "city").lower().endswith(".gdb"))
        self.assertTrue(normalize_container_path("gpkg", "city.gpkg").lower().endswith("city.gpkg"))

    def test_suggest_field_map_uses_aliases_once(self):
        dest = ["FromAddr_L", "ToAddr_L", "St_Name", "PostCode_L", "Notes", "Extra"]
        source = ["L_F_ADD", "L_T_ADD", "STREETNAME", "ZIP_L", "Notes"]
        mapped = suggest_field_map(dest, source, list(RCL_FIELDS) + street_field_defs())
        self.assertEqual(mapped["FromAddr_L"], "L_F_ADD")
        self.assertEqual(mapped["ToAddr_L"], "L_T_ADD")
        self.assertEqual(mapped["St_Name"], "STREETNAME")
        self.assertEqual(mapped["PostCode_L"], "ZIP_L")
        self.assertEqual(mapped["Notes"], "Notes")
        self.assertNotIn("Extra", mapped)
        self.assertEqual(len(mapped.values()), len(set(mapped.values())))

    def test_unmapped_required(self):
        defs = centerline_defs()
        missing = unmapped_required(["FromAddr_L", "ToAddr_L", "St_Name"], {"St_Name": "STREET"}, defs)
        self.assertIn("FromAddr_L", missing)
        self.assertIn("ToAddr_L", missing)
        self.assertNotIn("St_Name", missing)

    def test_convert_value(self):
        self.assertIsNone(convert_value("", "int"))
        self.assertIsNone(convert_value(None, "string"))
        self.assertEqual(convert_value(" 101 ", "int"), 101)
        self.assertEqual(convert_value("1,234", "int"), 1234)
        self.assertEqual(convert_value(101.0, "int"), 101)
        self.assertEqual(convert_value(" 12.5 ", "double"), 12.5)
        self.assertEqual(convert_value(" Main ", "string"), "Main")
        self.assertRaises(ValueError, convert_value, "abc", "int")

    def test_import_report(self):
        text = format_import_report([
            {"title": "Road centerlines", "written": 2, "mapped": 3, "missing": ["DateUpdate"]},
            {"title": "Street names", "skipped_reason": "No fields were mapped."},
        ])
        self.assertIn("Road centerlines: 2 feature(s), 3 field(s) mapped.", text)
        self.assertIn("Mandatory fields with no source: DateUpdate.", text)
        self.assertIn("Street names: No fields were mapped.", text)


if __name__ == "__main__":
    unittest.main()
