"""Author 3D item models become held attachables and inventory icons posed and lit as Java draws them."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from held_models import (TURNS, held_animation, held_geometry, held_atlas, plan_held_items, render_icon, turned_uv,
                         write_held_items)

ALL_FACES = ('north', 'south', 'east', 'west', 'up', 'down')


def png(color, size=(16, 16)):
    buffer = io.BytesIO()
    Image.new('RGBA', size, color).save(buffer, 'PNG')
    return buffer.getvalue()


def dump(value):
    return json.dumps(value).encode()


def item_definition(model):
    return dump({'model': {'type': 'minecraft:model', 'model': model}})


LANTERN = {
    'parent': 'block/block', 'textures': {'lantern': 'block/lantern'},
    'elements': [
        {'from': [5, 0, 5], 'to': [11, 7, 11],
         'faces': {face: {'uv': [0, 2, 6, 9], 'texture': '#lantern'} for face in ALL_FACES}},
        # a flat plane: only its two broad faces can show, the others name no texture
        {'from': [6.5, 9, 8], 'to': [9.5, 11, 8], 'faces': {
            'north': {'uv': [14, 1, 11, 3], 'texture': '#lantern'},
            'south': {'uv': [11, 1, 14, 3], 'texture': '#lantern'},
            'east': {'uv': [0, 0, 0, 2], 'texture': '#missing'},
            'up': {'uv': [0, 0, 0, 2], 'texture': '#missing'}}}],
    'display': {'thirdperson_righthand': {'rotation': [71, 0, 0], 'translation': [0, -1, -0.5]},
                'firstperson_righthand': {'rotation': [26, 4, 5], 'translation': [3.5, 3.5, -2.75]}}}
GAME = {
    'assets/minecraft/items/lantern.json': item_definition('minecraft:item/lantern'),
    'assets/minecraft/models/item/lantern.json': dump(
        {'parent': 'minecraft:item/generated', 'textures': {'layer0': 'minecraft:item/lantern'}}),
    'assets/minecraft/items/powered_rail.json': item_definition('minecraft:item/powered_rail'),
    'assets/minecraft/models/item/powered_rail.json': dump(
        {'parent': 'minecraft:item/generated', 'textures': {'layer0': 'minecraft:block/powered_rail'}}),
    'assets/minecraft/items/grass_block.json': item_definition('minecraft:block/grass_block'),
    'assets/minecraft/models/block/grass_block.json': dump(
        {'parent': 'block/cube', 'textures': {'top': 'block/grass_block_top'}}),
    'assets/minecraft/models/block/cube.json': dump(
        {'parent': 'block/block', 'elements': [{'from': [0, 0, 0], 'to': [16, 16, 16], 'faces': {}}]}),
    'assets/minecraft/items/iron_sword.json': item_definition('minecraft:item/iron_sword'),
    'assets/minecraft/models/item/iron_sword.json': dump(
        {'parent': 'minecraft:item/handheld', 'textures': {'layer0': 'minecraft:item/iron_sword'}}),
    'assets/minecraft/models/item/handheld.json': dump({'parent': 'item/generated'}),
    'assets/minecraft/models/item/generated.json': dump({'parent': 'builtin/generated'}),
    'assets/minecraft/models/block/block.json': dump(
        {'display': {'gui': {'rotation': [30, 225, 0], 'scale': [0.625, 0.625, 0.625]}}}),
}
AUTHOR = {
    'assets/minecraft/models/item/lantern.json': dump(LANTERN),
    'assets/minecraft/textures/block/lantern.png': png((200, 160, 60, 255)),
    'assets/minecraft/models/item/powered_rail.json': dump({'textures': {'0': 'block/rail_wood'}, 'elements': [
        {'from': [0, 0, 0], 'to': [16, 1, 16], 'faces': {'up': {'uv': [0, 0, 16, 16], 'texture': '#0'}}}]}),
    'assets/minecraft/textures/block/rail_wood.png': png((90, 70, 40, 255)),
    'assets/minecraft/models/item/grass_block.json': dump({'parent': 'block/grass_block'}),
    'assets/minecraft/models/item/iron_sword.json': dump(
        {'parent': 'minecraft:item/handheld', 'textures': {'layer0': 'item/iron_sword'}}),
}
BLOCKS = {'lantern': {'carried_textures': 'lantern_carried', 'textures': 'lantern', 'sound': 'lantern'},
          'golden_rail': {'sound': 'metal',
                          'textures': {'down': 'rail_golden', 'side': 'rail_golden', 'up': 'rail_golden_powered'}},
          'grass': {'textures': 'grass'}}
TERRAIN = {'lantern_carried': {'textures': 'textures/items/lantern'}}


def read(path):
    return AUTHOR.get(path, GAME.get(path))


def plan():
    items = ['lantern', 'powered_rail', 'grass_block', 'iron_sword']
    return plan_held_items(read, GAME.get, AUTHOR.__contains__, items, BLOCKS)


class HeldModelTests(unittest.TestCase):
    def test_only_author_3d_models_of_flat_game_items_are_held(self):
        held, skipped = plan()
        self.assertEqual([(entry['item'], entry['bedrock']) for entry in held],
                         [('lantern', 'lantern'), ('powered_rail', 'golden_rail')])
        self.assertEqual(skipped, [{'item': 'grass_block', 'reason': 'the game draws this item in 3D too'}])
        lantern = held[0]['model']
        # faces without area are dropped; the plane keeps its two broad faces
        self.assertEqual([sorted(element['faces']) for element in lantern['elements']],
                         [['down', 'east', 'north', 'south', 'up', 'west'], ['north', 'south']])
        # display contexts resolve through the parent chain
        self.assertEqual(lantern['display']['gui']['rotation'], [30, 225, 0])

    def test_hand_pose_follows_the_authors_display_transforms(self):
        bones = held_animation('animation.test', LANTERN['display'])['animations']['animation.test']['bones']
        self.assertEqual(bones['held']['rotation'], [90, 'c.is_first_person ? 60 : 0', 'c.is_first_person ? -40 : 0'])
        self.assertEqual(bones['held_x']['rotation'][0], 'c.is_first_person ? -26 : -71')
        self.assertEqual(bones['held_x']['position'], ['c.is_first_person ? -3.5 : 0', 'c.is_first_person ? 3.5 : -1',
                                                       'c.is_first_person ? 2.75 : -0.5'])
        self.assertEqual(bones['held_y']['rotation'][1], 'c.is_first_person ? -4 : 0')
        self.assertEqual(bones['held_z']['rotation'][2], 'c.is_first_person ? 5 : 0')

    def test_geometry_binds_to_the_hand_and_maps_each_texture_into_the_atlas(self):
        rail = plan()[0][1]
        atlas, used = held_atlas(rail['model'], rail['images'])
        geometry = held_geometry('geometry.test', rail['model'], used)['minecraft:geometry'][0]
        self.assertEqual(geometry['bones'][0]['binding'], 'q.item_slot_to_bone_name(c.item_slot)')
        self.assertEqual([bone['name'] for bone in geometry['bones']], ['held', 'held_x', 'held_y', 'held_z'])
        self.assertEqual(atlas.size, (18, 18))
        up = geometry['bones'][3]['cubes'][0]['uv']['up']
        self.assertEqual(up, {'uv': [17, 17], 'uv_size': [-16, -16]})

    def test_turned_textures_draw_faces_like_javas_face_rotation(self):
        texture = Image.new('RGBA', (16, 16))
        for x in range(16):
            for y in range(16):
                texture.putpixel((x, y), (x * 16, y * 16, 0, 255))
        uv = [2, 4, 10, 14]
        corners = [(uv[0], uv[1]), (uv[0], uv[3]), (uv[2], uv[3]), (uv[2], uv[1])]
        # Points on the face as fractions along its u and v edges, away from texel boundaries.
        face_points = ((0.13, 0.17), (0.87, 0.23), (0.31, 0.79), (0.71, 0.61))
        for turns in range(4):
            turned = texture.transpose(TURNS[turns]) if turns else texture
            rect = turned_uv(uv, turns)
            # Java: face corner i (top-left, bottom-left, bottom-right, top-right) takes UV corner (i + turns) % 4
            java = [corners[(index + turns) % 4] for index in range(4)]
            for along_u, along_v in face_points:
                u = java[0][0] + along_u * (java[3][0] - java[0][0]) + along_v * (java[1][0] - java[0][0])
                v = java[0][1] + along_u * (java[3][1] - java[0][1]) + along_v * (java[1][1] - java[0][1])
                expected = texture.getpixel((int(u), int(v)))
                baked = turned.getpixel((int(rect[0] + along_u * (rect[2] - rect[0])),
                                         int(rect[1] + along_v * (rect[3] - rect[1]))))
                self.assertEqual(baked, expected, (turns, along_u, along_v))

    def test_rotated_faces_use_a_turned_atlas_cell(self):
        model = {'elements': [{'from': [0, 0, 0], 'to': [16, 2, 16], 'faces': {
            'up': {'uv': [0, 0, 16, 1], 'rotation': 90, 'texture': '#a'},
            'north': {'uv': [0, 0, 16, 2], 'texture': '#a'}}}]}
        atlas, cells = held_atlas(model, {'a': Image.new('RGBA', (16, 16), (1, 2, 3, 255))})
        self.assertEqual(cells, [('a', 1), ('a', 0)])
        geometry = held_geometry('geometry.test', model, cells)
        self.assertEqual(geometry['format_version'], '1.16.0')
        faces = geometry['minecraft:geometry'][0]['bones'][3]['cubes'][0]['uv']
        self.assertNotIn('uv_rotation', faces['up'])
        self.assertEqual(faces['north']['uv'][0], 18 + 1)

    def test_icon_uses_javas_inventory_angle_and_lighting(self):
        cube = {'elements': [{'from': [0, 0, 0], 'to': [16, 16, 16],
                              'faces': {face: {'texture': '#all'} for face in ALL_FACES}}],
                'display': {'gui': {'rotation': [30, 225, 0], 'scale': [0.625, 0.625, 0.625]}}, 'gui_light': 'side'}
        icon = render_icon(cube, {'all': Image.new('RGBA', (16, 16), (255, 255, 255, 255))}, size=64)
        top, left, right = (icon.getpixel(point)[0] for point in ((32, 18), (20, 38), (44, 38)))
        self.assertEqual(top, 255)
        self.assertAlmostEqual(left, 207, delta=3)
        self.assertAlmostEqual(right, 159, delta=3)
        self.assertEqual(icon.getpixel((1, 1))[3], 0)

    def test_icons_replace_only_existing_item_sprites(self):
        held, _ = plan()
        with tempfile.TemporaryDirectory() as folder:
            rp = Path(folder)
            (rp / 'textures/items').mkdir(parents=True)
            (rp / 'textures/items/lantern.tga').write_bytes(b'old')
            written = write_held_items(rp, held, 'pack', BLOCKS, TERRAIN)
            # A rail has no item sprite of its own: it keeps the game's icon, and its block is untouched
            # (a carried texture would change how the placed block draws).
            self.assertEqual([entry['icon'] for entry in written], ['textures/items/lantern', None])
            self.assertTrue((rp / 'textures/items/lantern.png').exists())
            self.assertFalse((rp / 'textures/items/lantern.tga').exists())
            self.assertFalse((rp / 'blocks.json').exists())
            self.assertFalse((rp / 'textures/terrain_texture.json').exists())
            attachable_file = json.loads((rp / 'attachables/pack_held_lantern.json').read_text())
            attachable = attachable_file['minecraft:attachable']['description']
            self.assertEqual(attachable['identifier'], 'minecraft:lantern')
            self.assertEqual(attachable['geometry'], {'default': 'geometry.pack.held.lantern'})
            self.assertEqual(attachable['textures']['default'], 'textures/pack/held/lantern')
            self.assertTrue((rp / 'textures/pack/held/lantern.png').exists())
            self.assertTrue((rp / 'models/entity/pack_held_lantern.geo.json').exists())
            self.assertTrue((rp / 'animations/pack_held_lantern.animation.json').exists())


if __name__ == '__main__':
    unittest.main()
