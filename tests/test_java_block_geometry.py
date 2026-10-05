from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from java_block_geometry import cube, fit_scale, fits, geometry, shaded, vertices


class JavaBlockGeometryTests(unittest.TestCase):
    def test_rotated_element_follows_blockbench(self):
        element = {'from': [2, 4, 6], 'to': [10, 12, 14],
                   'rotation': {'angle': 22.5, 'axis': 'y', 'origin': [5, 8, 7]},
                   'faces': {'north': {'uv': [0, 0, 8, 8], 'texture': '#0'}}}
        result = cube(element)
        self.assertEqual(result['origin'], [-2, 4, -2])  # x: 8 - to.x, z: from.z - 8
        self.assertEqual(result['size'], [8, 8, 8])
        self.assertEqual(result['pivot'], [3, 8, -1])
        self.assertEqual(result['rotation'], [-0.0, -22.5, 0.0])
        self.assertEqual(result['uv']['north'], {'uv': [0, 0], 'uv_size': [8, 8]})
        named = cube(element, lambda face, data: 'leaf')
        self.assertEqual(named['uv']['north']['material_instance'], 'leaf')

    def test_multi_axis_rotation_negates_x_and_y_only(self):
        element = {'from': [0, 8, 0], 'to': [16, 8, 16],
                   'rotation': {'x': -47.5, 'y': 10, 'z': 30, 'origin': [8, 8, 8]},
                   'faces': {'up': {'uv': [0, 0, 16, 16]}}}
        self.assertEqual(cube(element)['rotation'], [47.5, -10.0, 30.0])

    def test_top_and_bottom_uvs_are_flipped_and_flat_sides_dropped(self):
        plane = {'from': [0, 9, 0], 'to': [16, 9, 16],
                 'faces': {face: {'uv': [0, 0, 16, 16]} for face in ('up', 'down', 'north', 'east')}}
        uv = cube(plane)['uv']
        self.assertEqual(sorted(uv), ['down', 'up'])
        self.assertEqual(uv['up']['uv'], [16, 16])
        self.assertEqual(uv['up']['uv_size'], [-16, -16])

    def test_missing_uv_uses_java_automatic_uv(self):
        element = {'from': [4, 0, 2], 'to': [12, 6, 14], 'faces': {'north': {}, 'up': {}}}
        uv = cube(element)['uv']
        self.assertEqual(uv['north']['uv'], [4, 10])  # 16 - to.x, 16 - to.y
        self.assertEqual(uv['north']['uv_size'], [8, 6])

    def test_full_block_needs_no_scaling(self):
        block = {'from': [0, 0, 0], 'to': [16, 16, 16], 'faces': {'up': {}}}
        self.assertEqual(fit_scale([block], [(0, 0), (0, 90)]), 1.0)

    def test_oversized_leaf_planes_are_fitted_inside_the_limit(self):
        # a bushy-leaf frond: 18 px plane reaching well past the block, tilted
        frond = {'from': [7.9, 9.4, -9.5], 'to': [25.9, 9.4, 8.5],
                 'rotation': {'x': -47.5, 'y': 0, 'z': 0, 'origin': [8.9, 5.8, 7.4]},
                 'faces': {'up': {'uv': [0, 0, 16, 16]}, 'down': {'uv': [0, 0, 16, 16]}}}
        other = {'from': [-4.7, 7.8, 2.6], 'to': [13.3, 7.8, 20.6],
                 'rotation': {'angle': 22.5, 'axis': 'x', 'origin': [8.9, -8.2, 4.5]},
                 'faces': {'up': {'uv': [0, 0, 16, 16]}}}
        rotations = [(0, 0), (0, 90), (0, 180), (0, 270)]
        self.assertFalse(all(fits(vertices(e, r)) for e in (frond, other) for r in rotations)
                         and fits([v for e in (frond, other) for r in rotations for v in vertices(e, r)]))
        scale = fit_scale([frond, other], rotations)
        self.assertLess(scale, 1.0)
        self.assertGreater(scale, 0.7)
        built, used = geometry('geometry.test', [(f'v{i}', [frond, other], r) for i, r in enumerate(rotations)])
        self.assertEqual(used, scale)
        bones = built['minecraft:geometry'][0]['bones']
        self.assertEqual([b['name'] for b in bones], ['v0', 'v1', 'v2', 'v3'])
        self.assertEqual(bones[1]['rotation'], [0, -90, 0])
        self.assertNotIn('rotation', bones[0])


    def test_shading_follows_26_2_and_26_3_flags(self):
        self.assertTrue(shaded({}))
        self.assertFalse(shaded({'shade': False}))
        self.assertFalse(shaded({'shade_direction_override': 'up'}), '26.3 writes this where 26.2 wrote shade: false')
        self.assertTrue(shaded({'shade_direction_override': 'north'}))


if __name__ == '__main__':
    unittest.main()
