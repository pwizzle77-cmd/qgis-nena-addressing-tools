"""Existing-container checks against real QGIS providers (no production data)."""
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import unittest
import uuid
from unittest.mock import Mock, patch

from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsProject

from nena_addressing_and_rcl_tools.core import schema_reader as reader
from nena_addressing_and_rcl_tools.core.layer_access import ConfigError
from nena_addressing_and_rcl_tools.core.schema_pack import CREATABLE_LAYERS
from nena_addressing_and_rcl_tools.core.schema_writer import SchemaTarget, create_schema


class ExistingSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()
        cls.folder = Path(__file__).resolve().parents[2] / '_scratch' / ('nena-open-' + uuid.uuid4().hex)
        cls.folder.mkdir(parents=True)

    def setUp(self):
        QgsProject.instance().clear()

    @classmethod
    def tearDownClass(cls):
        from qgis.PyQt import sip
        QgsProject.instance().clear()
        for widget in getattr(cls, 'widgets', ()):
            sip.delete(widget)

    def tearDown(self):
        QgsProject.instance().clear()

    def make(self, kind='gpkg', keys=('centerline',), prefix='', archive=False):
        target = SchemaTarget(kind, str(self.folder / (uuid.uuid4().hex + '.' + kind)), prefix=prefix)
        layers = create_schema(target, QgsCoordinateReferenceSystem('EPSG:26917'), keys,
                               include_archive=archive)
        return target, layers

    def snapshot(self, path):
        with sqlite3.connect(path) as db:
            return list(db.iterdump())

    def test_full_geopackage_recognized_without_changes(self):
        target, layers = self.make(keys=[s.key for s in CREATABLE_LAYERS], prefix='county', archive=True)
        before = self.snapshot(target.path)
        opened = reader.open_schema(SchemaTarget('gpkg', target.path))
        self.assertEqual(set(opened), set(layers))
        self.assertEqual(self.snapshot(target.path), before)
        self.assertEqual(QgsProject.instance().mapLayers(), {})

    def test_minimal_and_already_loaded_pending_edits(self):
        target, layers = self.make()
        road = layers['centerline']
        QgsProject.instance().addMapLayer(road)
        road.startEditing()
        self.assertIs(reader.open_schema(target)['centerline'], road)
        self.assertTrue(road.isEditable())
        road.rollBack()

    def test_missing_path_is_not_created(self):
        path = self.folder / 'missing.gpkg'
        with self.assertRaises(ConfigError):
            reader.open_schema(SchemaTarget('gpkg', str(path)))
        self.assertFalse(path.exists())

    def test_invalid_schema_rejected_before_project_changes(self):
        target, layers = self.make()
        road = layers['centerline']
        self.assertTrue(road.dataProvider().deleteAttributes([road.fields().indexFromName('FromAddr_L')]))
        road.updateFields()
        with self.assertRaisesRegex(ConfigError, 'FromAddr_L'):
            reader.open_schema(target)
        self.assertEqual(QgsProject.instance().mapLayers(), {})

    def test_prefix_ambiguity_requires_choice(self):
        target, layers = self.make(prefix='one')
        create_schema(SchemaTarget('gpkg', target.path, prefix='two'),
                      QgsCoordinateReferenceSystem('EPSG:26917'), ['centerline'])
        with self.assertRaisesRegex(ConfigError, 'unique'):
            reader.open_schema(SchemaTarget('gpkg', target.path))
        self.assertEqual(set(reader.open_schema(target)), {'centerline'})

    def test_file_geodatabase_does_not_require_writer_to_open(self):
        target, layers = self.make('gdb', ('centerline', 'streets', 'address', 'road_type'), archive=True)
        with patch.object(reader.tables, 'file_gdb_driver', side_effect=AssertionError('writer queried')):
            opened = reader.open_schema(target)
        self.assertEqual(set(opened), set(layers))

    def test_saved_connection_schema_filter_and_case(self):
        target, layers = self.make()
        prop = SimpleNamespace(tableName=lambda: 'roadcenterline', schema=lambda: 'public')
        conn = SimpleNamespace(tables=lambda schema: [prop])
        with patch.object(reader.tables, 'provider_connection', return_value=conn), \
                patch.object(reader, '_open_one', return_value=layers['centerline']):
            result = reader.open_schema(SchemaTarget('conn', provider='postgres', connection='saved', schema='PUBLIC'))
        self.assertEqual(set(result), {'centerline'})

    def test_wrong_geometry_and_field_types_are_reported(self):
        from qgis.core import QgsVectorLayer
        from nena_addressing_and_rcl_tools.core.schema_pack import LAYERS_BY_KEY, defs_for
        layer = QgsVectorLayer('Point?crs=EPSG:26917&field=FromAddr_L:string', 'wrong', 'memory')
        errors = reader._problems(layer, LAYERS_BY_KEY['centerline'], defs_for('centerline'), False)
        self.assertIn('expected LineString geometry', errors)
        self.assertTrue(any('FromAddr_L: expected int' in error for error in errors))

    def test_old_lookup_without_default_column_and_unrelated_lookup(self):
        from nena_addressing_and_rcl_tools.core import lookups
        from nena_addressing_and_rcl_tools.core.project_config import ProjectConfig
        target, layers = self.make(keys=('centerline', 'road_type'))
        lookup = layers['road_type']
        lookup.dataProvider().deleteAttributes([lookup.fields().indexFromName('IsDefault')])
        lookup.updateFields()
        opened = reader.open_schema(target)
        self.assertIn('road_type', opened)
        QgsProject.instance().addMapLayer(lookup)
        cfg = ProjectConfig()
        cfg.lookups.discover_unassigned = False
        self.assertIsNone(lookups.lookup_layer(cfg, 'road_type'))

    def test_open_dialog_uses_existing_path_and_no_create_options(self):
        from nena_addressing_and_rcl_tools.ui.schema_dialogs import CreateSchemaDialog
        target, layers = self.make()
        dlg = CreateSchemaDialog(None, QgsCoordinateReferenceSystem(), str(self.folder), open_existing=True)
        dlg.ed_path.setText(target.path)
        dlg.accept()
        self.assertEqual(dlg.request()['target'].path, target.path)
        self.assertFalse(dlg.request()['target'].replace)
        self.assertFalse(dlg.request()['archive'])
        self.assertTrue(dlg.chk_replace.isHidden())
        dlg.deleteLater()

    def test_adoption_does_not_seed_migrate_or_duplicate_layers(self):
        from qgis.PyQt.QtWidgets import QMainWindow
        from qgis.gui import QgsMapCanvas
        from nena_addressing_and_rcl_tools.ui.dock import NenaDock
        from nena_addressing_and_rcl_tools.core import lookups
        target, layers = self.make(keys=[s.key for s in CREATABLE_LAYERS], archive=True)
        before = self.snapshot(target.path)
        window, canvas = QMainWindow(), QgsMapCanvas()
        iface = Mock()
        iface.mainWindow.return_value = window
        iface.mapCanvas.return_value = canvas
        dock = NenaDock(iface, Mock(), parent=window)
        from nena_addressing_and_rcl_tools.plugin import NenaRclPlugin
        controller = SimpleNamespace(dock=dock, _refresh_history=lambda: None, street_watchers=None,
            editor_watchers=None, derived_watchers=None, archive_watchers=None, access_guard=None, overlay=None)
        dock.projectConfigSaved.connect(lambda: NenaRclPlugin._config_saved(controller))
        with patch.object(lookups, 'seed_layer', side_effect=AssertionError('seed')), \
                patch.object(lookups, 'sync_defaults', side_effect=AssertionError('migration')):
            dock._opening_existing = True
            dock._adopt_schema(layers, target, None, None, existing=True)
            count = len(QgsProject.instance().layerTreeRoot().findLayers())
            dock._adopt_schema(reader.open_schema(target), target, None, None, existing=True)
        self.assertEqual(len(QgsProject.instance().layerTreeRoot().findLayers()), count)
        self.assertEqual(count, len(layers))
        self.assertEqual(self.snapshot(target.path), before)
        dock.projectConfigSaved.disconnect()
        # Keep Qt's layer pickers alive until all provider tests have finished.
        type(self).widgets = (dock, canvas, window)
