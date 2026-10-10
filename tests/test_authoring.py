"""`python bct.py block`: a pack author's pattern block, written into their own packs with vanilla gameplay."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'converter'))
from authoring import (AuthoringError, add_edge_to_data, add_to_data, build_block, build_edge, build_leaves,  # noqa: E402
                       build_carriers, build_overlays, check_pack, connected_tiles, init_pack, overlay_digest, overlay_tile_images, parse_models,
                       write_block, write_connected, write_edge, write_leaves, write_overlay_tiles)
from bedrock_schema import check_tree  # noqa: E402
from common import samples_path  # noqa: E402
from native_replacement import repeat_index  # noqa: E402

SAMPLES = samples_path()


def grid(path, columns, rows, alpha=255):
    image = Image.new('RGBA', (columns * 4, rows * 4))
    for index in range(columns * rows):
        column, row = index % columns, index // columns
        image.paste((index * 20, 0, 0, alpha), (column * 4, row * 4, column * 4 + 4, row * 4 + 4))
    image.save(path)
    return path


def script_data(bp):
    text = (Path(bp) / 'scripts/bct.js').read_text(encoding='utf-8')
    return json.loads(text.removeprefix('export default ').rstrip().removesuffix(';'))


@unittest.skipUnless(SAMPLES.is_dir(), 'needs bedrock-samples')
class PatternBlockTest(unittest.TestCase):
    def test_a_repeat_block_has_every_place_its_tiles_and_vanilla_gameplay(self):
        with tempfile.TemporaryDirectory() as folder:
            bp, rp = Path(folder) / 'BP', Path(folder) / 'RP'
            entry, missing = write_block(bp, rp, 'minecraft:deepslate', 'mypack:deepslate', {'repeat': [3, 2]},
                                         samples=SAMPLES, grid=grid(Path(folder) / 'grid.png', 3, 2))
            self.assertEqual(missing, [])
            self.assertEqual(check_tree(bp, rp, SAMPLES), [])
            block = json.loads((bp / 'blocks/deepslate.json').read_text())['minecraft:block']
            states = block['description']['states']
            # Top faces read rows along z, so x and z count to lcm(3, 2) = 6 as the engine sets them.
            self.assertEqual(states['bct:x'], {'values': {'min': 0, 'max': 5}})
            self.assertEqual(states['bct:y'], {'values': {'min': 0, 'max': 1}})
            self.assertEqual(states['bct:pillar_axis'], ['y', 'x', 'z'])
            place = next(item for item in block['permutations']
                         if item['condition'] == "q.block_state('bct:x') == 4 && q.block_state('bct:y') == 1 "
                                                 "&& q.block_state('bct:z') == 5")
            for face in ('up', 'north', 'west'):
                tile = repeat_index((4, 1, 5), face, 3, 2)
                self.assertEqual(place['components']['minecraft:material_instances'][face]['texture'],
                                 f'mypack_deepslate_{tile}')
            components = block['components']
            self.assertEqual(components['minecraft:loot'], 'loot_tables/mypack/deepslate.json')
            self.assertEqual(components['minecraft:map_color'], '#646464')
            self.assertIn('minecraft:is_pickaxe_item_destructible', components['minecraft:tags'])
            loot = json.loads((bp / 'loot_tables/mypack/deepslate.json').read_text())
            self.assertEqual(loot['pools'][0]['entries'][0]['name'], 'minecraft:cobbled_deepslate')
            # Tile 4 is column 1 of row 1, cut from the grid.
            with Image.open(rp / 'textures/blocks/mypack_deepslate_4.png') as tile:
                self.assertEqual(tile.getpixel((0, 0)), (80, 0, 0, 255))
            atlas = json.loads((rp / 'textures/terrain_texture.json').read_text())
            self.assertEqual(atlas['texture_data']['mypack_deepslate_0'],
                             {'textures': 'textures/blocks/mypack_deepslate_0'})
            self.assertEqual(json.loads((rp / 'blocks.json').read_text())['mypack:deepslate'], {'sound': 'deepslate'})
            self.assertEqual(entry, {'block': 'mypack:deepslate', 'pattern': {'repeat': [3, 2]}})
            self.assertTrue(add_to_data(bp / 'scripts/bct.js', 'minecraft:deepslate', entry, 'mypack'))
            self.assertEqual(script_data(bp), {'format': 1, 'pack': 'mypack', 'blocks': {'minecraft:deepslate': entry}})
            self.assertIn('"repeat": [3, 2]', (bp / 'scripts/bct.js').read_text(encoding='utf-8'))

    def test_a_log_carries_stripping_and_the_leaf_guard_in_its_entry(self):
        built = build_block('minecraft:oak_log', 'mypack:oak_log', {'random': [2, 1, 1]}, samples=SAMPLES)
        self.assertEqual(built['entry']['strip'], 'minecraft:stripped_oak_log')
        self.assertIn('minecraft:oak_leaves', built['entry']['leafGuard']['leaves'])
        states = built['definition']['minecraft:block']['description']['states']
        self.assertEqual(states['bct:r'], {'values': {'min': 0, 'max': 2}})
        self.assertEqual(built['textures'], ['mypack_oak_log_0', 'mypack_oak_log_1', 'mypack_oak_log_2'])

    def test_see_through_blocks_hide_faces_against_themselves_with_the_method_their_tiles_need(self):
        for alpha, method in ((255, 'alpha_test'), (128, 'blend')):
            with tempfile.TemporaryDirectory() as folder:
                bp, rp = Path(folder) / 'BP', Path(folder) / 'RP'
                entry, _ = write_block(bp, rp, 'minecraft:glass', 'mypack:glass', {'random': [1, 1]},
                                       samples=SAMPLES, grid=grid(Path(folder) / 'grid.png', 2, 1, alpha))
                self.assertEqual(check_tree(bp, rp, SAMPLES), [])
                self.assertTrue(entry['open'])
                block = json.loads((bp / 'blocks/glass.json').read_text())['minecraft:block']
                self.assertEqual(block['components']['minecraft:geometry']['culling'], 'mypack:glass_culling')
                instances = block['permutations'][0]['components']['minecraft:material_instances']
                self.assertEqual(instances['up']['render_method'], method)
                culling = json.loads((rp / 'block_culling/mypack_glass.json').read_text())
                self.assertTrue(all(rule['condition'] == 'same_block'
                                    for rule in culling['minecraft:block_culling_rules']['rules']))

    def test_a_slab_takes_the_pattern_on_half_a_block(self):
        with tempfile.TemporaryDirectory() as folder:
            bp, rp = Path(folder) / 'BP', Path(folder) / 'RP'
            entry, missing = write_block(bp, rp, 'minecraft:oak_slab', 'mypack:oak_slab', {'repeat': [2, 2]},
                                         samples=SAMPLES, grid=grid(Path(folder) / 'grid.png', 2, 2))
            self.assertEqual(missing, [])
            self.assertEqual(check_tree(bp, rp, SAMPLES), [])
            self.assertEqual(entry['shape'], 'slab')
            block = json.loads((bp / 'blocks/oak_slab.json').read_text())['minecraft:block']
            self.assertEqual(block['description']['states']['bct:vertical_half'], ['bottom', 'top'])
            geometry = block['components']['minecraft:geometry']
            self.assertEqual(geometry['bone_visibility']['shape_bottom'], "q.block_state('bct:vertical_half') == 'bottom'")
            boxes = [permutation['components']['minecraft:collision_box'] for permutation in block['permutations']
                     if 'minecraft:collision_box' in permutation['components']]
            self.assertEqual(boxes, [{'origin': [-8, 0, -8], 'size': [16, 8, 16]}, {'origin': [-8, 8, -8], 'size': [16, 8, 16]}])
            self.assertTrue((rp / 'models/blocks/mypack_oak_slab.geo.json').exists())
            double = build_block('minecraft:oak_double_slab', 'mypack:oak_double_slab', {'repeat': [2, 2]}, samples=SAMPLES)
            self.assertEqual(double['definition']['minecraft:block']['components']['minecraft:geometry'],
                             'minecraft:geometry.full_block')
            self.assertEqual(double['loot']['pools'][0]['entries'][0]['name'], 'minecraft:oak_slab')
            entry, _ = write_block(bp, rp, 'minecraft:oak_stairs', 'mypack:oak_stairs', {'repeat': [2, 2]},
                                   samples=SAMPLES, grid=grid(Path(folder) / 'grid.png', 2, 2))
            self.assertEqual(check_tree(bp, rp, SAMPLES), [])
            self.assertEqual(entry['shape'], 'stairs')
            stairs = json.loads((bp / 'blocks/oak_stairs.json').read_text())['minecraft:block']
            self.assertEqual(len(stairs['components']['minecraft:geometry']['bone_visibility']), 40)
            entry, _ = write_block(bp, rp, 'minecraft:oak_fence', 'mypack:oak_fence', {'repeat': [2, 2]},
                                   samples=SAMPLES, grid=grid(Path(folder) / 'grid.png', 2, 2))
            self.assertEqual(check_tree(bp, rp, SAMPLES), [])
            self.assertEqual(entry['shape'], 'fence')
            fence = json.loads((bp / 'blocks/oak_fence.json').read_text())['minecraft:block']
            self.assertEqual(fence['components']['minecraft:connection_rule'], {'accepts_connections_from': 'only_fences'},
                             'vanilla nether brick fences, panes and walls do not join a wooden fence')
            entry, _ = write_block(bp, rp, 'minecraft:cobblestone_wall', 'mypack:cobblestone_wall', {'repeat': [2, 2]},
                                   samples=SAMPLES, grid=grid(Path(folder) / 'grid.png', 2, 2))
            self.assertEqual(check_tree(bp, rp, SAMPLES), [])
            self.assertEqual(entry['shape'], 'wall')

    def test_blocks_that_cannot_be_swapped_are_refused_with_the_reason(self):
        def refused(vanilla, identifier, pattern, text):
            with self.assertRaises(AuthoringError) as caught:
                build_block(vanilla, identifier, pattern, samples=SAMPLES)
            self.assertIn(text, str(caught.exception))
        refused('minecraft:grass_block', 'mypack:grass', {'repeat': [2, 2]}, 'stays vanilla')
        refused('minecraft:torch', 'mypack:torch', {'repeat': [2, 2]}, 'not a full cube or a slab')
        refused('minecraft:stone', 'minecraft:stone2', {'repeat': [2, 2]}, 'must be your pack id')
        refused('minecraft:stone', 'stone', {'repeat': [2, 2]}, 'must look like mypack:stone')
        refused('minecraft:stone', 'mypack:stone', {'repeat': [7, 5]}, 'more than 4096 block permutations')
        refused('minecraft:stone', 'mypack:stone', {'random': [1]}, '2 to 16 weights')

    def test_a_hand_edited_data_file_is_left_alone(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'scripts/bct.js'
            script.parent.mkdir()
            script.write_text('// my notes\nexport default { format: 1 };\n', encoding='utf-8')
            self.assertFalse(add_to_data(script, 'minecraft:stone', {'block': 'p:stone'}, 'p'))
            self.assertIn('my notes', script.read_text(encoding='utf-8'))



def leaf_geometry(identifier):
    """A small leaf model in Bedrock's geometry format."""
    return {'format_version': '1.21.0', 'minecraft:geometry': [{
        'description': {'identifier': identifier, 'texture_width': 16, 'texture_height': 16},
        'bones': [{'name': 'leaf', 'pivot': [0, 0, 0], 'cubes': [{'origin': [-8, 0, -8], 'size': [16, 16, 16],
                                                                   'uv': [0, 0]}]}]}]}


@unittest.skipUnless(SAMPLES.is_dir(), 'needs bedrock-samples')
class LeafBlockTest(unittest.TestCase):
    def test_near_and_far_models_pick_by_state_with_vanilla_leaf_gameplay(self):
        with tempfile.TemporaryDirectory() as folder:
            bp, rp = Path(folder) / 'BP', Path(folder) / 'RP'
            for name in ('leaf_a', 'leaf_b', 'leaf_far'):
                path = rp / f'models/blocks/{name}.geo.json'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(leaf_geometry(f'geometry.mypack.{name}')))
            near = parse_models(['geometry.mypack.leaf_a', 'geometry.mypack.leaf_a@90', 'geometry.mypack.leaf_b'],
                                [2, 2, 1], '--near')
            far = parse_models(['geometry.mypack.leaf_far'], None, '--far')
            entry, todo = write_leaves(bp, rp, 'minecraft:oak_leaves', 'mypack:oak_leaves', near, far, near_up_to=4,
                                       samples=SAMPLES)
            self.assertEqual(entry, {'block': 'mypack:oak_leaves', 'models': {'near': [2, 2, 1], 'far': [1], 'nearUpTo': 4}})
            self.assertEqual(todo, ['the leaf texture textures/blocks/mypack_oak_leaves.png'])
            self.assertEqual(check_tree(bp, rp, SAMPLES), [])
            block = json.loads((bp / 'blocks/oak_leaves.json').read_text())['minecraft:block']
            states = block['description']['states']
            self.assertEqual(states['bct:t'], {'values': {'min': 0, 'max': 2}})
            self.assertEqual(states['bct:look'], {'values': {'min': 0, 'max': 1}})
            self.assertEqual(states['bct:persistent_bit'], {'values': {'min': 0, 'max': 1}})
            turned = next(item for item in block['permutations']
                          if item['condition'] == "q.block_state('bct:look') == 0 && q.block_state('bct:t') == 1")
            self.assertEqual(turned['components'], {'minecraft:geometry': 'geometry.mypack.leaf_a',
                                                    'minecraft:transformation': {'rotation': [0, 90, 0]}})
            components = block['components']
            self.assertIn('bct:leaf', components)
            self.assertEqual(components['minecraft:material_instances']['*']['tint_method'], 'default_foliage')
            self.assertEqual(components['minecraft:loot'], 'loot_tables/mypack/oak_leaves.json')
            loot = json.loads((bp / 'loot_tables/mypack/oak_leaves.json').read_text())
            # Shears give the leaves; otherwise saplings, sticks and apples by chance, never the leaves.
            self.assertEqual(loot['pools'][0]['conditions'], [{'condition': 'match_tool', 'item': 'minecraft:shears'}])
            self.assertTrue(all(pool['conditions'] for pool in loot['pools']), 'every drop has a condition')
            self.assertEqual(json.loads((rp / 'blocks.json').read_text())['mypack:oak_leaves'], {'sound': 'grass'})

    def test_one_model_list_and_no_models(self):
        weighted = build_leaves('minecraft:cherry_leaves', 'mypack:cherry',
                                parse_models(['geometry.mypack.a', 'geometry.mypack.b'], [3, 1], '--near'), samples=SAMPLES)
        self.assertEqual(weighted['entry'], {'block': 'mypack:cherry', 'models': [3, 1]})
        self.assertNotIn('bct:look', weighted['definition']['minecraft:block']['description']['states'])
        self.assertNotIn('tint_method', weighted['definition']['minecraft:block']['components']['minecraft:material_instances']['*'],
                         'cherry leaves are not tinted')
        plain = build_leaves('minecraft:spruce_leaves', 'mypack:spruce', None, samples=SAMPLES)
        self.assertEqual(plain['entry'], {'block': 'mypack:spruce'})
        self.assertEqual(plain['definition']['minecraft:block']['components']['minecraft:geometry'], 'minecraft:geometry.full_block')

    def test_mistakes_are_refused(self):
        with self.assertRaisesRegex(AuthoringError, 'not a vanilla leaf block'):
            build_leaves('minecraft:stone', 'mypack:stone', None, samples=SAMPLES)
        with self.assertRaisesRegex(AuthoringError, 'one weight per model'):
            parse_models(['geometry.a', 'geometry.b'], [1], '--near')
        with self.assertRaisesRegex(AuthoringError, 'turns by 0, 90, 180 or 270'):
            parse_models(['geometry.a@45'], None, '--near')
        with self.assertRaisesRegex(AuthoringError, 'is not a geometry id'):
            parse_models(['leaf_a'], None, '--near')



def ground_texture(path, size=16):
    """A speckled ground texture."""
    image = Image.new('RGBA', (size, size))
    for x in range(size):
        for y in range(size):
            shade = 90 + (x * 37 + y * 11) % 120
            image.putpixel((x, y), (shade, shade, shade, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


@unittest.skipUnless(SAMPLES.is_dir(), 'needs bedrock-samples')
class EdgeBlockTest(unittest.TestCase):
    def test_an_edge_cut_from_the_ground_texture_draws_a_plane_per_side_and_corner(self):
        with tempfile.TemporaryDirectory() as folder:
            bp, rp = Path(folder) / 'BP', Path(folder) / 'RP'
            ground_texture(rp / 'textures/blocks/my_grass.png')
            (rp / 'textures/blocks/my_grass.texture_set.json').write_text(json.dumps(
                {'format_version': '1.21.30', 'minecraft:texture_set': {'color': 'my_grass', 'metalness_emissive_roughness': [0, 0, 200]}}))
            entry = write_edge(bp, rp, 'mypack:grass_edge', ['minecraft:grass_block'], ['minecraft:stone', 'minecraft:dirt'],
                               texture='textures/blocks/my_grass', tint='grass', samples=SAMPLES)
            self.assertEqual(entry, {'from': 'minecraft:grass_block', 'onto': ['minecraft:stone', 'minecraft:dirt'],
                                     'block': 'mypack:grass_edge'})
            self.assertEqual(check_tree(bp, rp, SAMPLES), [])
            block = json.loads((bp / 'blocks/grass_edge.json').read_text())['minecraft:block']
            self.assertEqual(block['description']['states'], {'bct:edges': list(range(16)), 'bct:corners': list(range(16))})
            components = block['components']
            self.assertFalse(components['minecraft:collision_box'])
            self.assertEqual(components['minecraft:material_instances']['edge']['tint_method'], 'grass')
            self.assertNotIn('minecraft:placement_filter', components, 'an edge may rest on a pattern block too')
            visibility = components['minecraft:geometry']['bone_visibility']
            self.assertEqual(visibility['edge2'], "math.mod(math.floor(q.block_state('bct:edges')/4),2) == 1")
            self.assertEqual(visibility['corner0'], "math.mod(math.floor(q.block_state('bct:corners')/1),2) == 1")
            with Image.open(rp / 'textures/blocks/mypack_grass_edge_edge.png') as edge:
                self.assertEqual(edge.size, (16, 16))
                self.assertEqual(edge.getpixel((5, 0))[3], 255, 'the edge is solid along its top side')
                self.assertEqual(edge.getpixel((5, 15))[3], 0, 'and clear past the middle')
            texture_set = json.loads((rp / 'textures/blocks/mypack_grass_edge_corner.texture_set.json').read_text())
            self.assertEqual(texture_set['minecraft:texture_set']['metalness_emissive_roughness'], [0, 0, 200],
                             'the PBR values of the ground texture go with the cut tiles')
            self.assertEqual(json.loads((rp / 'blocks.json').read_text())['mypack:grass_edge'], {'sound': 'grass'})

    def test_edges_keep_their_order_in_the_data_file_and_a_rewritten_edge_keeps_one_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            script = Path(folder) / 'scripts/bct.js'
            grass = {'from': 'minecraft:grass_block', 'onto': ['minecraft:stone'], 'block': 'p:grass_edge'}
            sand = {'from': 'minecraft:sand', 'onto': ['minecraft:stone'], 'block': 'p:sand_edge'}
            for entry in (grass, sand, {**grass, 'onto': ['minecraft:dirt']}):
                self.assertTrue(add_edge_to_data(script, entry, 'p'))
            self.assertEqual(script_data(Path(folder))['edges'], [sand, {**grass, 'onto': ['minecraft:dirt']}])

    def test_own_cut_tiles_and_mistakes(self):
        with tempfile.TemporaryDirectory() as folder:
            rp = Path(folder) / 'RP'
            ground_texture(rp / 'textures/blocks/cut_edge.png')
            ground_texture(rp / 'textures/blocks/cut_corner.png')
            built = build_edge('mypack:sand_edge', ['minecraft:sand'], ['minecraft:stone'], rp=rp,
                               edge_texture='textures/blocks/cut_edge', corner_texture='textures/blocks/cut_corner',
                               samples=SAMPLES)
            self.assertEqual(built['sound'], 'sand')
            self.assertEqual(sorted(built['images']), ['corner', 'edge'])
            for kwargs, text in (({'texture': 'textures/blocks/missing'}, 'is not in the resource pack'),
                                 ({'edge_texture': 'textures/blocks/cut_edge'}, 'give both'),
                                 ({}, 'give --texture')):
                with self.assertRaisesRegex(AuthoringError, text):
                    build_edge('mypack:sand_edge', ['minecraft:sand'], ['minecraft:stone'], rp=rp, samples=SAMPLES, **kwargs)
            with self.assertRaisesRegex(AuthoringError, 'letters, digits and _ only'):
                build_edge('mypack:sand-edge', ['minecraft:sand'], ['minecraft:stone'], rp=rp, samples=SAMPLES,
                           texture='textures/blocks/cut_edge')
            with self.assertRaisesRegex(AuthoringError, 'minecraft:nope is not a Bedrock block id'):
                build_edge('mypack:sand_edge', ['minecraft:nope'], ['minecraft:stone'], rp=rp, samples=SAMPLES,
                           texture='textures/blocks/cut_edge')



@unittest.skipUnless(SAMPLES.is_dir(), 'needs bedrock-samples')
class OverlayTest(unittest.TestCase):
    def test_tiles_cut_from_a_texture_follow_the_overlay_template(self):
        with tempfile.TemporaryDirectory() as folder:
            rp = Path(folder)
            ground_texture(rp / 'textures/blocks/ground.png')
            tiles = overlay_tile_images(rp, 'textures/blocks/ground')
            self.assertEqual(len(tiles), 17)
            alpha = [tile.getchannel('A') for tile in tiles]
            # Tile 9 lies along the left edge, 7 along the right, 15 along the top and 1 along the bottom.
            self.assertEqual((alpha[9].getpixel((0, 8)), alpha[9].getpixel((15, 8))), (255, 0))
            self.assertEqual((alpha[7].getpixel((15, 8)), alpha[7].getpixel((0, 8))), (255, 0))
            self.assertEqual((alpha[15].getpixel((8, 0)), alpha[15].getpixel((8, 15))), (255, 0))
            self.assertEqual((alpha[1].getpixel((8, 15)), alpha[1].getpixel((8, 0))), (255, 0))
            self.assertEqual(alpha[8].getpixel((8, 8)), 0, 'all four edges leave the middle clear')
            self.assertEqual([alpha[8].getpixel(at) for at in ((0, 8), (15, 8), (8, 0), (8, 15))], [255] * 4)
            # Corner tiles: 0 down+right, 2 left+down, 14 right+up, 16 up+left.
            self.assertEqual(alpha[0].getpixel((15, 15)), 255)
            self.assertEqual(alpha[0].getpixel((0, 0)), 0)
            self.assertEqual(alpha[16].getpixel((0, 0)), 255)

    def test_overlays_build_their_surface_blocks_and_the_data_the_engine_checks(self):
        with tempfile.TemporaryDirectory() as folder:
            bp, rp = Path(folder) / 'BP', Path(folder) / 'RP'
            init_pack(bp, rp, 'mypack')
            self.assertIn('export default null', (bp / 'scripts/bct-overlays.js').read_text())
            ground_texture(rp / 'textures/blocks/ground.png')
            write_overlay_tiles(rp, 'textures/blocks/ground', 'textures/blocks/grass_overlay')
            overlays = [{'tiles': 'textures/blocks/grass_overlay', 'onto': ['minecraft:stone'],
                         'from': 'minecraft:grass_block', 'tint': 'grass'}]
            script = bp / 'scripts/bct.js'
            script.write_text('export default ' + json.dumps({'format': 1, 'pack': 'mypack', 'overlays': overlays}) + ';\n')
            report = build_overlays(bp, rp, samples=SAMPLES)
            self.assertEqual(report['rules_not_drawn'], [])
            self.assertEqual(report['rules_drawn'][0]['host_blocks'], ['minecraft:stone'])
            self.assertEqual(check_tree(bp, rp, SAMPLES), [])
            blocks = sorted(path.name for path in (bp / 'blocks').glob('bct_mypack_overlay_*.json'))
            self.assertTrue(blocks)
            text = (bp / 'scripts/bct-overlays.js').read_text()
            built = json.loads(text[text.index('export default ') + len('export default '):].rstrip().rstrip(';'))
            self.assertEqual(built['digest'], overlay_digest(overlays))
            self.assertEqual(built['data']['rules'][0]['connectBlocks'], ['minecraft:grass_block'])
            # Rebuilt with no overlays, the generated blocks and textures go and the engine gets nothing.
            script.write_text('export default ' + json.dumps({'format': 1, 'pack': 'mypack'}) + ';\n')
            build_overlays(bp, rp, samples=SAMPLES)
            self.assertEqual(list((bp / 'blocks').glob('bct_mypack_overlay_*.json')), [])
            self.assertEqual(list((rp / 'textures/blocks').glob('bct_ov_mypack_*')), [])
            atlas = json.loads((rp / 'textures/terrain_texture.json').read_text())['texture_data']
            self.assertFalse([name for name in atlas if name.startswith('bct_ov_mypack_')])
            self.assertIn('export default null', (bp / 'scripts/bct-overlays.js').read_text())

    def test_wrong_overlays_are_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            bp, rp = Path(folder) / 'BP', Path(folder) / 'RP'
            init_pack(bp, rp, 'mypack')
            for overlays, text in (([{'tiles': 'textures/blocks/missing', 'onto': 'minecraft:stone', 'from': 'minecraft:sand'}],
                                    'missing_0.png is not in the resource pack'),
                                   ([{'tiles': 'grass', 'onto': 'minecraft:stone', 'from': 'minecraft:sand'}],
                                    r'overlays\[0\]\.tiles'),
                                   ([{'tile': 'textures/blocks/x'}], r'overlays\[0\]\.tile: is not')):
                (bp / 'scripts/bct.js').write_text('export default ' + json.dumps(
                    {'format': 1, 'pack': 'mypack', 'overlays': overlays}) + ';\n')
                with self.assertRaisesRegex(AuthoringError, text):
                    build_overlays(bp, rp, samples=SAMPLES)

    @unittest.skipUnless(shutil.which('node'), 'needs node')
    def test_the_digest_is_the_engine_checksum(self):
        overlays = [{'tiles': 'textures/blocks/grass_overlay', 'onto': ['minecraft:stone'], 'from': 'minecraft:grass_block',
                     'tint': 'grass', 'faces': ['up', 'north']}]
        script = ("import { checksum } from './engine/sources.mjs';"
                  "process.stdout.write(checksum(JSON.stringify(JSON.parse(process.argv[1]))));")
        result = subprocess.run(['node', '--input-type=module', '-e', script, json.dumps(overlays)], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, overlay_digest(overlays))



def quarter_textures(definition, states):
    """{(face, quarter): tile kind} the permutation for these neighbour states shows."""
    sides = {'north': 'bct:n', 'south': 'bct:s', 'west': 'bct:w', 'east': 'bct:e', 'up': 'bct:u', 'down': 'bct:d'}
    condition = ' && '.join(f"q.block_state('{state}') == {int(side in states)}" for side, state in sides.items())
    permutation = next(item for item in definition['minecraft:block']['permutations'] if item['condition'] == condition)
    instances = permutation['components']['minecraft:material_instances']
    return {name: value['texture'].rsplit('_', 1)[1] for name, value in instances.items() if name != '*'}


@unittest.skipUnless(SAMPLES.is_dir(), 'needs bedrock-samples')
class ConnectedBlockTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.bp, self.rp = Path(self.folder.name) / 'BP', Path(self.folder.name) / 'RP'
        for tile in (0, 2, 24, 26):
            ground_texture(self.rp / f'textures/blocks/glass_ctm_{tile}.png')

    def tearDown(self):
        self.folder.cleanup()

    def test_each_quarter_of_a_face_shows_the_tile_its_two_edges_need(self):
        entry = write_connected(self.bp, self.rp, 'minecraft:glass', 'mypack:glass', connected_tiles('textures/blocks/glass_ctm'),
                                samples=SAMPLES)
        self.assertEqual(entry, {'block': 'mypack:glass', 'open': True})
        self.assertEqual(check_tree(self.bp, self.rp, SAMPLES), [])
        definition = json.loads((self.bp / 'blocks/glass.json').read_text())
        self.assertEqual(len(definition['minecraft:block']['permutations']), 64)
        # The north face's texture left is east and its right is west.
        joined = quarter_textures(definition, {'east', 'west'})
        self.assertEqual({joined[f'north_{q}'] for q in ('00', '10', '01', '11')}, {'across'})
        joined = quarter_textures(definition, {'up', 'east'})
        self.assertEqual((joined['north_00'], joined['north_10'], joined['north_01'], joined['north_11']),
                         ('joined', 'along', 'across', 'alone'), 'top left joins up and east, bottom right neither')
        self.assertEqual({joined[f'up_{q}'] for q in ('00', '10', '01', '11')}, {'alone', 'across'},
                         'the top face joins east only')
        atlas = json.loads((self.rp / 'textures/terrain_texture.json').read_text())['texture_data']
        self.assertEqual(atlas['mypack_glass_along'], {'textures': 'textures/blocks/glass_ctm_24'})
        culling = json.loads((self.rp / 'block_culling/mypack_glass.json').read_text())
        self.assertTrue(all(rule['condition'] == 'same_block' for rule in culling['minecraft:block_culling_rules']['rules']))

    def test_tiles_can_have_random_variants_and_faces_that_do_not_join_a_texture_of_their_own(self):
        for tile in (0, 2, 24, 26):
            ground_texture(self.rp / f'textures/blocks/glass_ctm_b_{tile}.png')
        ground_texture(self.rp / 'textures/blocks/end_0.png')
        ground_texture(self.rp / 'textures/blocks/end_1.png')
        sides = ['north', 'south', 'west', 'east']
        write_connected(self.bp, self.rp, 'minecraft:oak_planks', 'mypack:planks',
                        connected_tiles(['textures/blocks/glass_ctm', 'textures/blocks/glass_ctm_b']), faces=sides,
                        textures={'up': ['textures/blocks/end_0', 'textures/blocks/end_1'],
                                  'down': 'textures/blocks/end_0'}, samples=SAMPLES)
        self.assertEqual(check_tree(self.bp, self.rp, SAMPLES), [])
        atlas = json.loads((self.rp / 'textures/terrain_texture.json').read_text())['texture_data']
        self.assertEqual(atlas['mypack_planks_alone'], {'textures': {'variations': [
            {'path': 'textures/blocks/glass_ctm_0'}, {'path': 'textures/blocks/glass_ctm_b_0'}]}})
        self.assertEqual(len(atlas['mypack_planks_up']['textures']['variations']), 2)
        self.assertEqual(atlas['mypack_planks_down'], {'textures': 'textures/blocks/end_0'})
        definition = json.loads((self.bp / 'blocks/planks.json').read_text())
        joined = quarter_textures(definition, {'up', 'down', 'east'})
        self.assertEqual({joined[f'up_{q}'] for q in ('00', '10', '01', '11')}, {'up'})
        self.assertEqual({joined[f'down_{q}'] for q in ('00', '10', '01', '11')}, {'down'})
        with self.assertRaisesRegex(AuthoringError, 'does not join: leave it out of --faces'):
            write_connected(self.bp, self.rp, 'minecraft:oak_planks', 'mypack:planks', connected_tiles('textures/blocks/glass_ctm'),
                            textures={'up': 'textures/blocks/end_0'}, samples=SAMPLES)

    def test_planks_can_join_sideways_on_their_sides_only(self):
        entry = write_connected(self.bp, self.rp, 'minecraft:oak_planks', 'mypack:planks',
                                connected_tiles('textures/blocks/glass_ctm'), faces=['north', 'south', 'west', 'east'],
                                joins='horizontal', connect=['minecraft:oak_planks', 'minecraft:spruce_planks'],
                                samples=SAMPLES)
        self.assertEqual(entry['connect'], ['minecraft:oak_planks', 'minecraft:spruce_planks'])
        self.assertNotIn('open', entry)
        definition = json.loads((self.bp / 'blocks/planks.json').read_text())
        joined = quarter_textures(definition, {'up', 'east', 'north'})
        self.assertEqual({joined[f'up_{q}'] for q in ('00', '10', '01', '11')}, {'alone'}, 'the top never joins')
        self.assertEqual((joined['south_00'], joined['south_10']), ('alone', 'across'),
                         'the south face joins east (its right) but not up')
        loot = json.loads((self.bp / 'loot_tables/mypack/planks.json').read_text())
        self.assertEqual(loot['pools'][0]['entries'][0]['name'], 'minecraft:oak_planks')
        with self.assertRaisesRegex(AuthoringError, 'bookshelf stays vanilla: Enchanting tables count bookshelves'):
            write_connected(self.bp, self.rp, 'minecraft:bookshelf', 'mypack:shelf', connected_tiles('textures/blocks/glass_ctm'),
                            samples=SAMPLES)

    def test_mistakes(self):
        with self.assertRaisesRegex(AuthoringError, 'glass_ctm_0.png is not in the resource pack'):
            write_connected(self.bp, Path(self.folder.name) / 'empty', 'minecraft:glass', 'mypack:glass',
                            connected_tiles('textures/blocks/glass_ctm'), samples=SAMPLES)
        with self.assertRaisesRegex(AuthoringError, 'give --ctm or the four tiles'):
            connected_tiles('textures/blocks/glass_ctm', alone='textures/blocks/a')
        with self.assertRaisesRegex(AuthoringError, 'all of --alone'):
            connected_tiles(alone='textures/blocks/a')
        with self.assertRaisesRegex(AuthoringError, '--joins is all, horizontal or vertical'):
            write_connected(self.bp, self.rp, 'minecraft:glass', 'mypack:glass', connected_tiles('textures/blocks/glass_ctm'),
                            joins='diagonal', samples=SAMPLES)
        with self.assertRaisesRegex(AuthoringError, 'not a full cube; connected blocks are full cubes'):
            write_connected(self.bp, self.rp, 'minecraft:torch', 'mypack:torch', connected_tiles('textures/blocks/glass_ctm'),
                            samples=SAMPLES)



@unittest.skipUnless(SAMPLES.is_dir(), 'needs bedrock-samples')
class CarrierTest(unittest.TestCase):
    def test_carriers_build_into_the_packs_and_a_rebuild_removes_what_the_last_one_wrote(self):
        with tempfile.TemporaryDirectory() as folder:
            bp, rp = Path(folder) / 'BP', Path(folder) / 'RP'
            init_pack(bp, rp, 'mypack')
            self.assertIn('export default null', (bp / 'scripts/bct-carriers.js').read_text())
            for tile in range(47):
                ground_texture(rp / f'textures/blocks/glass_ctm_{tile}.png')
            carriers = [{'tiles': 'textures/blocks/glass_ctm', 'blocks': ['minecraft:glass']}]
            script = bp / 'scripts/bct.js'
            script.write_text('export default ' + json.dumps({'format': 1, 'pack': 'mypack', 'carriers': carriers}) + ';' + chr(10))
            written = build_carriers(bp, rp, samples=SAMPLES)
            self.assertIn('BP/entities/bct_mypack_c0.json', written)
            self.assertIn('RP/render_controllers/bct_mypack_c0.json', written)
            self.assertFalse([path for path in written if path.endswith(('manifest.json', 'pack_icon.png')) or '/scripts/' in path])
            self.assertTrue(all(((bp if path.startswith('BP/') else rp) / path[3:]).is_file() for path in written))
            text = (bp / 'scripts/bct-carriers.js').read_text()
            built = json.loads(text[text.index('export default ') + len('export default '):].rstrip().rstrip(';'))
            self.assertEqual(built['digest'], overlay_digest(carriers))
            self.assertEqual(built['data']['rules'][0]['blocks'], ['minecraft:glass'])
            # The engine draws carriers only on full cubes: see-through ones count.
            self.assertIn('minecraft:glass', built['data']['fullCubeBlocks'])
            self.assertNotIn('minecraft:glass', built['data']['opaqueBlocks'])
            self.assertEqual(json.loads((bp / 'bct-carriers-files.json').read_text()), written)
            script.write_text('export default ' + json.dumps({'format': 1, 'pack': 'mypack'}) + ';' + chr(10))
            self.assertEqual(build_carriers(bp, rp, samples=SAMPLES), [])
            self.assertFalse([path for path in written if ((bp if path.startswith('BP/') else rp) / path[3:]).exists()])
            self.assertIn('export default null', (bp / 'scripts/bct-carriers.js').read_text())

    def test_wrong_carriers_are_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            bp, rp = Path(folder) / 'BP', Path(folder) / 'RP'
            init_pack(bp, rp, 'mypack')
            for carriers, text in (([{'tiles': 'textures/blocks/none', 'blocks': 'minecraft:glass'}], 'none_0.png is not in the resource pack'),
                                   ([{'tiles': 'textures/blocks/none', 'block': 'minecraft:glass'}], r'carriers\[0\]\.block: is not')):
                (bp / 'scripts/bct.js').write_text('export default ' + json.dumps({'format': 1, 'pack': 'mypack', 'carriers': carriers}) + ';')
                with self.assertRaisesRegex(AuthoringError, text):
                    build_carriers(bp, rp, samples=SAMPLES)



@unittest.skipUnless(SAMPLES.is_dir() and shutil.which('node'), 'needs bedrock-samples and node')
class CheckTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.bp, self.rp = Path(self.folder.name) / 'BP', Path(self.folder.name) / 'RP'
        init_pack(self.bp, self.rp, 'mypack')
        entry, _ = write_block(self.bp, self.rp, 'minecraft:stone', 'mypack:stone', {'repeat': [2, 2]}, samples=SAMPLES,
                               grid=grid(Path(self.folder.name) / 'grid.png', 2, 2))
        add_to_data(self.bp / 'scripts/bct.js', 'minecraft:stone', entry, 'mypack')
        self.script = self.bp / 'scripts/bct.js'

    def tearDown(self):
        self.folder.cleanup()

    def write(self, text):
        self.script.write_text(text, encoding='utf-8')

    def test_a_ready_pack_has_no_problems_even_with_comments_in_its_data(self):
        self.assertEqual(check_pack(self.bp, self.rp, samples=SAMPLES), [])
        self.write('// my pack' + chr(10) + self.script.read_text(encoding='utf-8').replace('"format": 1,', '"format": 1, // first'))
        self.assertEqual(check_pack(self.bp, self.rp, samples=SAMPLES), [])

    def test_notes_say_what_is_not_wrong_but_worth_knowing(self):
        notes = []
        self.assertEqual(check_pack(self.bp, self.rp, samples=SAMPLES, notes=notes), [])
        self.assertEqual(len(notes), 1)
        self.assertIn('no texture sets', notes[0])

    def test_the_engine_message_and_the_schema_problems_are_reported(self):
        self.write('export default ' + json.dumps({'format': 1, 'pack': 'mypack',
                                                   'blocks': {'minecraft:stone': {'block': 'mypack:nope'}}}) + ';')
        problems = check_pack(self.bp, self.rp, samples=SAMPLES)
        self.assertIn('scripts/bct.js: blocks["minecraft:stone"].block: mypack:nope is not a block in your packs '
                      '(is its blocks/ file in the behavior pack?)', problems)
        self.write('export default ' + json.dumps({'format': 1, 'pack': 'mypack',
                                                   'leaves': {'minecraft:oak_leaves': {'block': 'mypack:l', 'model': [1]}}}) + ';')
        problems = check_pack(self.bp, self.rp, samples=SAMPLES)
        self.assertTrue(any('/leaves/minecraft:oak_leaves/model: not allowed here' in problem for problem in problems), problems)

    def test_out_of_date_scripts_unbuilt_overlays_and_missing_textures_are_reported(self):
        (self.bp / 'scripts/publisher.js').write_text('// old', encoding='utf-8')
        data = json.loads(self.script.read_text(encoding='utf-8').removeprefix('export default ').rstrip().rstrip(';'))
        data['overlays'] = [{'tiles': 'textures/blocks/x', 'onto': 'minecraft:stone', 'from': 'minecraft:grass_block'}]
        self.write('export default ' + json.dumps(data) + ';')
        (self.rp / 'textures/blocks/mypack_stone_3.png').unlink()
        problems = check_pack(self.bp, self.rp, samples=SAMPLES)
        self.assertIn('scripts/publisher.js is from another BCT version: run python bct.py init', problems)
        self.assertIn('scripts/bct.js: overlays: changed since their surface blocks were built: run python bct.py overlays again',
                      problems)
        self.assertIn('textures/terrain_texture.json: mypack_stone_3 names textures/blocks/mypack_stone_3, '
                      'which is not in the resource pack', problems)

    def test_a_pack_with_a_script_of_its_own_keeps_it(self):
        with tempfile.TemporaryDirectory() as folder:
            bp, rp = Path(folder) / 'BP', Path(folder) / 'RP'
            (bp / 'scripts').mkdir(parents=True)
            manifest = {'format_version': 2, 'header': {'name': 'p', 'uuid': '11111111-1111-4111-8111-111111111111',
                                                        'version': [1, 0, 0], 'min_engine_version': [1, 26, 50]},
                        'modules': [{'type': 'script', 'language': 'javascript', 'entry': 'scripts/index.js',
                                     'uuid': '22222222-2222-4222-8222-222222222222', 'version': [1, 0, 0]}]}
            (bp / 'manifest.json').write_text(json.dumps(manifest))
            own = "import { world } from '@minecraft/server';" + chr(10)
            (bp / 'scripts/index.js').write_text(own)
            written, notes = init_pack(bp, rp, 'mypack')
            self.assertFalse((bp / 'scripts/main.js').exists())
            self.assertIn('import ./bct.js', notes[0])
            self.assertEqual((bp / 'scripts/index.js').read_text(), own)
            self.assertIn('2286eae6-39b3-5e23-9a2d-919e4da1bcaa', (bp / 'manifest.json').read_text())
            problems = check_pack(bp, rp, samples=SAMPLES)
            self.assertIn('scripts/index.js does not send bct.js to the engine: add the lines docs/AUTHORING.md shows', problems)
            (bp / 'scripts/index.js').write_text(own + "import data from './bct.js';" + chr(10) +
                                                 'publishSources({ system, world, sources: [authoredSource(data)] });')
            self.assertFalse([problem for problem in check_pack(bp, rp, samples=SAMPLES) if 'index.js' in problem])

    def test_a_pack_not_wired_to_the_engine(self):
        manifest = json.loads((self.bp / 'manifest.json').read_text())
        manifest['dependencies'] = manifest['dependencies'][:1]
        (self.bp / 'manifest.json').write_text(json.dumps(manifest))
        (self.bp / 'scripts/main.js').unlink()
        problems = check_pack(self.bp, self.rp, samples=SAMPLES)
        self.assertIn('manifest.json: does not depend on the BCT engine: run python bct.py init', problems)
        self.assertIn('scripts/main.js is missing: run python bct.py init', problems)


if __name__ == '__main__':
    unittest.main()
