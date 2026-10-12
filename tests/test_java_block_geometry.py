from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from java_block_geometry import close_top_face, cube, fit_scale, fits, geometry, shaded, vertices


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

    def test_closed_top_face_repeats_the_top_texels_along_each_edge(self):
        # Bedrock face UVs as cube() writes them: a top face reads u from low x and v from high z,
        # a bottom face v from low z, a north face u from low x and a south face u from high x.
        for java_uv in ([0, 0, 16, 16], [16, 8, 8, 16], [8, 0, 16, 8]):
            plane = cube({'from': [0, 0.1, 0], 'to': [16, 0.124, 16], 'faces': {'up': {'uv': java_uv}}},
                         lambda face, data: 'tile')
            (x, _, z), (width, _, depth) = plane['origin'], plane['size']
            top = plane['uv']['up']
            u = lambda at_x: top['uv'][0] + top['uv_size'][0] * (at_x - x) / width
            v = lambda at_z: top['uv'][1] + top['uv_size'][1] * (z + depth - at_z) / depth
            block, west, east = close_top_face(plane, -1)
            self.assertEqual(block['origin'][1], -1)
            self.assertAlmostEqual(block['origin'][1] + block['size'][1], 0.124)
            north, south = block['uv']['north'], block['uv']['south']
            for at_x in (x, x + width):
                self.assertAlmostEqual(north['uv'][0] + north['uv_size'][0] * (at_x - x) / width, u(at_x))
                self.assertAlmostEqual(south['uv'][0] + south['uv_size'][0] * (x + width - at_x) / width, u(at_x))
            self.assertAlmostEqual(north['uv'][1], v(z), delta=.02)
            self.assertAlmostEqual(south['uv'][1], v(z + depth), delta=.02)
            for side, edge_x in ((west, x + width), (east, x)):
                self.assertEqual(side['rotation'], [0, 0, 90])
                self.assertEqual(side['uv']['up']['material_instance'], 'tile')
                # A quarter turn about z either way swaps the x and y sizes about the pivot.
                pivot, size, origin = side['pivot'], side['size'], side['origin']
                self.assertAlmostEqual(pivot[1] - size[0] / 2, -1)
                self.assertAlmostEqual(pivot[1] + size[0] / 2, 0.124)
                self.assertLess(abs(pivot[0] - edge_x), .001)
                self.assertEqual((origin[2], size[2]), (z, depth))
                up, down = side['uv']['up'], side['uv']['down']
                for at_z in (z, z + depth):
                    self.assertAlmostEqual(up['uv'][1] + up['uv_size'][1] * (z + depth - at_z) / depth, v(at_z))
                    self.assertAlmostEqual(down['uv'][1] + down['uv_size'][1] * (at_z - z) / depth, v(at_z))
                for face in (up, down):
                    self.assertAlmostEqual(face['uv'][0], u(edge_x), delta=.02)
                    # One texel column only, so a strip seen edge on keeps the top's mip level.
                    self.assertEqual(face['uv_size'][0], 0)
            self.assertEqual((north['uv_size'][1], south['uv_size'][1]), (0, 0))


if __name__ == '__main__':
    unittest.main()
