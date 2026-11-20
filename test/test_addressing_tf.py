"""TF numbering uses the final vertex without reversing the geometry or its sides."""
import unittest

try:
    from qgis.core import QgsApplication, QgsFeature, QgsGeometry, QgsPointXY, QgsProject, QgsVectorLayer
    HAS_QGIS = True
except ImportError:
    HAS_QGIS = False


@unittest.skipUnless(HAS_QGIS, 'Requires real QGIS geometry')
class ToFromAddressingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QgsApplication.instance() or QgsApplication([], False)
        cls.app.initQgis()

    def setUp(self):
        from nena_addressing_and_rcl_tools.compat import make_field
        from nena_addressing_and_rcl_tools.core.addressing import engine
        from nena_addressing_and_rcl_tools.core.config import PluginConfig
        from nena_addressing_and_rcl_tools.core.project_config import ProjectConfig, remember
        self.engine = engine
        self.project = QgsProject.instance()
        self.road = QgsVectorLayer('LineString?crs=EPSG:26917', 'TF test road', 'memory')
        self.addresses = QgsVectorLayer('Point?crs=EPSG:26917', 'TF test addresses', 'memory')
        road_fields = ['St_Name', 'StreetID', 'RCL_NGUID', 'FromAddr_L', 'ToAddr_L',
                       'FromAddr_R', 'ToAddr_R', 'Parity_L', 'Parity_R', 'MSAGComm_L', 'MSAGComm_R', 'Travel']
        self.road.dataProvider().addAttributes([make_field(name, 'int' if 'Addr_' in name else 'string')
                                                for name in road_fields])
        self.road.updateFields()
        address_fields = ['St_Name', 'StreetID', 'RCL_NGUID', 'Add_Number', 'AddNum_Suf',
                          'Access_X', 'Access_Y', 'Addr_Side', 'Site_NGUID']
        self.addresses.dataProvider().addAttributes([make_field(name, 'int' if name == 'Add_Number'
            else 'double' if name in ('Access_X', 'Access_Y') else 'string') for name in address_fields])
        self.addresses.updateFields()
        self.project.addMapLayer(self.road)
        self.project.addMapLayer(self.addresses)
        feature = QgsFeature(self.road.fields())
        feature.setGeometry(QgsGeometry.fromWkt('LINESTRING(0 0, 200 0)'))
        feature.setAttributes(['Main', 'S1', 'R1', 101, 301, 100, 300, 'O', 'E', 'LEFT CITY', 'RIGHT CITY', 'TF'])
        self.assertTrue(self.road.dataProvider().addFeatures([feature])[0])
        self.fid = next(self.road.getFeatures()).id()
        self.pcfg = ProjectConfig()
        remember(self.pcfg.streets, 'centerline', self.road)
        remember(self.pcfg.streets, 'address', self.addresses)
        self.pcfg.addresses.distance_per_number = 1.0
        self.pcfg.addresses.distance_units = 'meters'
        self.pcfg.addresses.frontage_interval = 50.0
        mapping = {name: name for name in road_fields if name != 'Travel'}
        mapping['OneWay'] = 'Travel'  # Exercise the configured field mapping, not a hardcoded name.
        self.ctx = engine.AddressContext(self.pcfg, self.road, PluginConfig(mapping=mapping))
        self.ctx.da.setEllipsoid('NONE')  # Synthetic coordinates are exactly 200 planar meters long.

    def tearDown(self):
        self.project.removeMapLayer(self.addresses.id())
        self.project.removeMapLayer(self.road.id())

    def set_way(self, value):
        if not self.road.isEditable():
            self.road.startEditing()
        self.road.changeAttributeValue(self.fid, self.road.fields().indexFromName('Travel'), value)

    def plan(self, x, y=10):
        return self.engine.plan_address(self.ctx, QgsPointXY(x, 0), QgsPointXY(x, y), segment_fid=self.fid)

    def add_address(self, x, number):
        feature = QgsFeature(self.addresses.fields())
        feature.setGeometry(QgsGeometry.fromWkt(f'POINT({x} 10)'))
        feature.setAttributes(['Main', 'S1', 'R1', number, '', x, 0, 'L', f'site-{number}'])
        self.addresses.startEditing()
        self.assertTrue(self.addresses.addFeature(feature))
        self.assertTrue(self.addresses.commitChanges())

    def test_tf_counts_from_end_and_preserves_physical_sides(self):
        plan = self.plan(40)
        self.assertEqual((plan.distance_m, plan.result.raw, plan.result.number), (160, 261, 261))
        self.assertAlmostEqual(plan.frac, 0.2)  # Geometry/attribution still use the real click position.
        self.assertEqual((plan.side, plan.parity, plan.community['MSAGComm']), ('L', 'O', 'LEFT CITY'))
        right = self.plan(40, -10)
        self.assertEqual((right.side, right.parity, right.result.number, right.community['MSAGComm']),
                         ('R', 'E', 260, 'RIGHT CITY'))
        self.assertEqual(self.plan(200).result.number, 101)
        self.assertEqual(self.plan(0).result.number, 301)

    def test_other_oneway_values_and_missing_mapping_keep_original_results(self):
        for value in ['B', 'FT', None, '', 'unknown']:
            with self.subTest(value=value):
                self.set_way(value)
                plan = self.plan(40)
                self.assertEqual((plan.distance_m, plan.result.raw, plan.result.number), (40, 141, 141))
        self.set_way(' tf ')
        self.assertEqual(self.plan(40).result.number, 261)
        self.ctx.rcl_acc.index.pop('OneWay')
        self.assertEqual(self.plan(40).result.number, 141)

    def test_interpolation_frontage_and_fallback_use_same_rules(self):
        self.pcfg.addresses.method = 'interpolate'
        self.assertEqual(self.plan(40).result.number, 261)
        self.pcfg.addresses.method = 'frontage'
        self.assertEqual(self.plan(40).result.number, 107)
        self.set_way('FT')
        self.assertEqual(self.plan(40).result.number, 101)
        self.set_way('TF')
        self.pcfg.addresses.method = 'distance'
        self.road.changeAttributeValue(self.fid, self.road.fields().indexFromName('FromAddr_L'), None)
        self.assertEqual(self.plan(40).result.raw, 260)

    def test_tf_neighbors_are_ordered_from_end_and_collision_rule_is_unchanged(self):
        self.add_address(160, 141)
        self.add_address(40, 261)
        self.assertEqual(self.plan(100).result.number, 201)
        self.add_address(100, 201)
        self.assertEqual(self.plan(100).result.number, 203)
        self.pcfg.addresses.collision = 'suffix'
        self.assertEqual(self.plan(100).result.label, '201A')
        self.pcfg.addresses.collision = 'ask'
        self.assertTrue(self.plan(100).result.needs_choice)

    def test_tf_validation_does_not_report_reversed_order(self):
        from nena_addressing_and_rcl_tools.core.addressing.checks import address_issues
        self.add_address(160, 141)
        self.add_address(40, 261)
        self.assertNotIn('ADDR_OUT_OF_ORDER', [issue.code for issue in address_issues(self.ctx, False)])

    def test_mixed_street_segments_and_place_units_use_the_correct_origin(self):
        feature = QgsFeature(self.road.fields())
        feature.setGeometry(QgsGeometry.fromWkt('LINESTRING(200 0, 400 0)'))
        feature.setAttributes(['Main', 'S1', 'R2', 501, 701, 500, 700, 'O', 'E', 'LEFT CITY', 'RIGHT CITY', 'FT'])
        self.assertTrue(self.road.dataProvider().addFeatures([feature])[0])
        other_id = next(f.id() for f in self.road.getFeatures() if f.id() != self.fid)
        self.assertEqual(self.plan(40).result.number, 261)
        other = self.engine.plan_address(self.ctx, QgsPointXY(240, 0), QgsPointXY(240, 10), segment_fid=other_id)
        self.assertEqual((other.distance_m, other.result.number), (40, 541))
        numbers = self.engine.house_numbers_by_side(self.ctx, self.fid, QgsPointXY(40, 0))
        self.assertEqual(numbers, {'L': 261, 'R': 260})

    def test_community_spacing_override_still_controls_tf_numbering(self):
        self.pcfg.addresses.overrides = {'LEFT CITY': {'distance_per_number': 2.0}}
        self.assertEqual(self.plan(40).result.raw, 181)
        self.assertEqual(self.plan(40).result.number, 181)

    def test_bent_tf_line_keeps_access_point_at_the_clicked_location(self):
        self.road.startEditing()
        self.road.changeGeometry(self.fid, QgsGeometry.fromWkt('LINESTRING(0 0, 100 0, 100 100)'))
        plan = self.plan(20)
        self.assertEqual((plan.distance_m, plan.result.number), (180, 281))
        seat = self.engine.access_seat(self.ctx, plan)
        self.assertAlmostEqual(seat.x(), 20)
        self.assertAlmostEqual(seat.y(), 10 * 0.3048)
        self.assertEqual(self.road.getFeature(self.fid).geometry().asWkt(), 'LineString (0 0, 100 0, 100 100)')


if __name__ == '__main__':
    unittest.main()
