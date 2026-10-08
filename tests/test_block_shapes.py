from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from block_shapes import shape_of, shape_variants, shaped_block, single_slab

STAIR_MIRROR = {'weirdo_direction': 'bct:weirdo_direction', 'upside_down_bit': 'bct:upside_down_bit',
                'minecraft:corner': 'bct:corner'}


def stair(direction, upside_down, corner):
    return next(elements for values, elements, _ in shape_variants('stairs')
                if values == {'weirdo_direction': direction, 'upside_down_bit': upside_down, 'minecraft:corner': corner})


class BlockShapeTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual(shape_of('minecraft:oak_slab'), 'slab')
        self.assertEqual(shape_of('minecraft:stone_stairs'), 'stairs')
        self.assertIsNone(shape_of('minecraft:oak_double_slab'), 'a double slab is a full cube')
        self.assertEqual(single_slab('minecraft:waxed_double_cut_copper_slab'), 'minecraft:waxed_cut_copper_slab')

    def test_stairs_have_their_tall_back_on_the_side_they_face_and_corners_left_and_right_of_it(self):
        slab = ([0, 0, 0], [16, 8, 16])
        # Facing north (weirdo_direction 3): the back is the north half, z 0-8 in Java coordinates.
        self.assertEqual(stair(3, False, 'none'), [slab, ([0, 8, 0], [16, 16, 8])])
        # Inner right: the back plus the front quarter on the right (east for a stair facing north).
        self.assertEqual(stair(3, False, 'inner_right'), [slab, ([0, 8, 0], [16, 16, 8]), ([8, 8, 8], [16, 16, 16])])
        # Outer left facing east: only the back quarter on the left (north for a stair facing east).
        self.assertEqual(stair(0, False, 'outer_left'), [slab, ([8, 8, 0], [16, 16, 8])])
        # Upside down: the slab on top, the step under it.
        self.assertEqual(stair(2, True, 'none'), [([0, 8, 0], [16, 16, 16]), ([0, 0, 8], [16, 8, 16])])

    def test_a_stair_has_a_bone_and_boxes_per_state_combination(self):
        built = shaped_block('geometry.test.stairs', 'stairs', STAIR_MIRROR)
        self.assertEqual(len(built['visibility']), 40, 'four facings, two halves, five corners')
        self.assertEqual(built['visibility']['shape_3_0_inner_right'],
                         "q.block_state('bct:weirdo_direction') == 3 && q.block_state('bct:upside_down_bit') == 0"
                         " && q.block_state('bct:corner') == 'inner_right'")
        boxes = {item['condition']: item['components'] for item in built['permutations']}
        straight = boxes[built['visibility']['shape_3_0_none']]
        self.assertEqual(len(straight['minecraft:collision_box']), 2, 'a box for the slab and one for the step')
        self.assertEqual(straight['minecraft:selection_box'], {'origin': [-8, 0, -8], 'size': [16, 16, 16]})
        with self.assertRaisesRegex(ValueError, 'without its minecraft:corner state'):
            shaped_block('geometry.test.stairs', 'stairs', {'weirdo_direction': 'a', 'upside_down_bit': 'b'})

    def test_a_fence_shows_its_post_and_the_bars_of_each_joined_side_and_is_too_tall_to_jump(self):
        self.assertEqual(shape_of('minecraft:oak_fence'), 'fence')
        self.assertIsNone(shape_of('minecraft:spruce_fence_gate'), 'gates open and close, so they stay vanilla')
        mirror = {f'minecraft:connection_{side}': f'bct:connection_{side}' for side in ('north', 'east', 'south', 'west')}
        built = shaped_block('geometry.test.fence', 'fence', mirror)
        self.assertEqual(len(built['visibility']), 16)
        bones = {bone['name']: bone['cubes'] for bone in built['geometry']['minecraft:geometry'][0]['bones']}
        self.assertEqual(len(bones['shape_0_0_0_0']), 1, 'a lone post')
        self.assertEqual(len(bones['shape_1_1_0_0']), 5, 'the post and two bars for each of two sides')
        boxes = {item['condition']: item['components'] for item in built['permutations']}
        north_east = boxes[built['visibility']['shape_1_1_0_0']]
        self.assertEqual([box['size'][1] for box in north_east['minecraft:collision_box']], [24, 24, 24])
        self.assertEqual(north_east['minecraft:selection_box']['size'][1], 16, 'selection stays a block high')

    def test_a_wall_switches_its_post_and_arms_as_parts(self):
        self.assertEqual(shape_of('minecraft:cobblestone_wall'), 'wall')
        mirror = {name: 'bct:' + name for name in ('wall_connection_type_north', 'wall_connection_type_east',
                                                   'wall_connection_type_south', 'wall_connection_type_west', 'wall_post_bit')}
        built = shaped_block('geometry.test.wall', 'wall', mirror)
        self.assertEqual(sorted(built['visibility']), ['east_short', 'east_tall', 'north_short', 'north_tall', 'post',
                                                       'south_short', 'south_tall', 'west_short', 'west_tall'])
        self.assertEqual(len(built['permutations']), 162, 'boxes for every combination of three heights per side and the post')
        tall = {item['condition']: item['components'] for item in built['permutations']}[
            "q.block_state('bct:wall_connection_type_north') == 'tall' && q.block_state('bct:wall_connection_type_east') == 'none'"
            " && q.block_state('bct:wall_connection_type_south') == 'tall' && q.block_state('bct:wall_connection_type_west') == 'none'"
            " && q.block_state('bct:wall_post_bit') == 0"]
        self.assertEqual([box['size'][1] for box in tall['minecraft:collision_box']], [24, 24])
        self.assertEqual(tall['minecraft:selection_box']['size'], [6, 16, 16])

    def test_collision_matches_the_drawn_geometry(self):
        built = shaped_block('geometry.test.stairs', 'stairs', STAIR_MIRROR)
        bones = {bone['name']: bone['cubes'] for bone in built['geometry']['minecraft:geometry'][0]['bones']}
        for item in built['permutations']:
            name = next(name for name, condition in built['visibility'].items() if condition == item['condition'])
            collision = item['components']['minecraft:collision_box']
            collision = collision if isinstance(collision, list) else [collision]
            self.assertEqual(collision, [{'origin': cube['origin'], 'size': cube['size']} for cube in bones[name]])


if __name__ == '__main__':
    unittest.main()
