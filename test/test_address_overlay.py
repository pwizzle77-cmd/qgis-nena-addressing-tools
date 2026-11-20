"""Actual QGIS canvas checks for address overlay deletion and signal cleanup."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

try:
    from qgis.core import QgsApplication, QgsFeature, QgsGeometry, QgsPointXY, QgsVectorLayer
    from qgis.gui import QgsMapCanvas
    HAS_QGIS = True
except ImportError:
    HAS_QGIS = False


@unittest.skipUnless(HAS_QGIS, 'Requires the real QGIS canvas')
class AddressOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        from nena_addressing_and_rcl_tools.ui import address_overlay as module
        self.module = module
        self.canvas = QgsMapCanvas()
        self.layer = QgsVectorLayer('Point?crs=EPSG:26917&field=Add_Number:integer', 'addresses', 'memory')
        self.road = QgsVectorLayer('LineString?crs=EPSG:26917', 'road', 'memory')
        for x, number in [(10, 100), (20, 102), (30, 104)]:
            feature = QgsFeature(self.layer.fields())
            feature.setGeometry(QgsGeometry.fromWkt(f'POINT({x} 10)'))
            feature.setAttributes([number])
            self.layer.dataProvider().addFeatures([feature])
        self.ids = [f.id() for f in self.layer.getFeatures()]
        self.layer.selectByIds(self.ids[:2])
        cfg = SimpleNamespace(addresses=SimpleNamespace(overlay=True, overlay_limit=50), streets=None)
        accessor = SimpleNamespace(has=lambda name: name == 'Add_Number',
                                   get=lambda feature, name: feature['Add_Number'] if name == 'Add_Number' else None)
        ctx = SimpleNamespace(pcfg=cfg, addr=self.layer, rcl=self.road, addr_acc=accessor,
                              from_addr=SimpleNamespace(transform=lambda point: point))
        self.patches = [
            patch.object(module, 'load_project_config', return_value=cfg),
            patch.object(module, 'layer_for', side_effect=lambda config, role: self.layer),
            patch.object(module.AddressOverlay, '_context', return_value=ctx),
            patch.object(module.E, 'stored_access', side_effect=lambda context, feature:
                         QgsPointXY(feature.geometry().asPoint().x(), 0)),
            patch.object(module.E, 'plan_address', return_value=SimpleNamespace(result=SimpleNamespace(number=None))),
        ]
        for item in self.patches:
            item.start()
        self.overlay = module.AddressOverlay(SimpleNamespace(mapCanvas=lambda: self.canvas))
        self.assertEqual(len(self.overlay.items), 6)  # Circle, line and label for each selected address.

    def tearDown(self):
        self.overlay.unload()
        for item in reversed(self.patches):
            item.stop()
        self.canvas.deleteLater()

    def test_delete_notification_removes_only_that_address_even_if_selection_is_stale(self):
        original = list(self.overlay.items)
        # Some providers still expose the feature/selection when deletion is announced.
        self.layer.featureDeleted.emit(self.ids[0])
        self.assertEqual(self.overlay.items, original[3:])
        self.assertTrue(all(item.scene() is None for item in original[:3]))
        self.assertTrue(all(item.scene() is self.canvas.scene() for item in original[3:]))

    def test_actual_delete_and_multiple_delete_clear_the_sketches(self):
        self.layer.startEditing()
        self.assertTrue(self.layer.deleteFeature(self.ids[0]))
        self.assertEqual(len(self.overlay.items), 3)
        self.assertTrue(self.layer.deleteFeature(self.ids[1]))
        self.assertEqual(self.overlay.items, [])

    def test_unselected_delete_does_not_clear_other_addresses(self):
        original = list(self.overlay.items)
        self.layer.startEditing()
        self.assertTrue(self.layer.deleteFeature(self.ids[2]))
        self.assertEqual(self.overlay.items, original)

    def test_delete_new_unsaved_address_clears_its_sketches(self):
        self.layer.startEditing()
        feature = QgsFeature(self.layer.fields())
        feature.setGeometry(QgsGeometry.fromWkt('POINT(40 10)'))
        feature.setAttributes([106])
        self.assertTrue(self.layer.addFeature(feature))
        self.assertLess(feature.id(), 0)
        self.layer.selectByIds([feature.id()])
        self.assertEqual(len(self.overlay.items), 3)
        self.assertTrue(self.layer.deleteFeature(feature.id()))
        self.assertEqual(self.overlay.items, [])

    def test_refresh_and_unload_disconnect_delete_notifications(self):
        old_layer = self.layer
        self.layer = QgsVectorLayer('Point?crs=EPSG:26917', 'replacement', 'memory')
        self.overlay.refresh()
        self.assertEqual(old_layer.receivers(old_layer.featureDeleted), 0)
        self.assertEqual(self.layer.receivers(self.layer.featureDeleted), 1)
        self.overlay.unload()
        self.assertEqual(self.layer.receivers(self.layer.featureDeleted), 0)
        self.assertEqual(self.overlay.items, [])


if __name__ == '__main__':
    unittest.main()
