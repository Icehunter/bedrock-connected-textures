import fnmatch
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from addon_package import compose_source_addon, packet, read_packets, source_data
from bedrock_schema import check_tree
from carrier_budget import apply_budget, load_budget, rule_targets
from common import samples_path
from native_replacement import (build_replacements, fallback_patterns, load_policy, repeat_index, repeat_moduli,
                                representative, shaped_over_budget, vanilla_tags_of)

FACES = ['north', 'east', 'south', 'west', 'up', 'down']
TEXTURES = 'assets/minecraft/textures/block/'
CTM = 'assets/minecraft/optifine/ctm/'
REPLACE_BP = 'replacement/Replace_BP'
REPLACE_RP = 'replacement/Replace_RP'


def material(root, name, color, pbr=True):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGBA', (4, 4), color).save(path)
    if pbr:
        Image.new('RGB', (4, 4), (128, 128, 255)).save(path.with_name(path.stem + '_normal.png'))
        Image.new('RGBA', (4, 4), (0, 0, 200, 0)).save(path.with_name(path.stem + '_mers.png'))
        texture_set = {'color': path.stem, 'normal': path.stem + '_normal',
                       'metalness_emissive_roughness_subsurface': path.stem + '_mers'}
        path.with_suffix('.texture_set.json').write_text(json.dumps({'format_version': '1.21.30',
                                                                     'minecraft:texture_set': texture_set}))


def cube(texture, top=None):
    return {face: TEXTURES + (top if top and face in ('up', 'down') else texture) + '.png' for face in FACES}


def read_json(path):
    return json.loads(Path(path).read_text())


def condition_values(permutation):
    """{state: value} of a permutation condition made of q.block_state('state') == value terms."""
    terms = permutation['condition'].replace("q.block_state('", '').replace("'", '').split(' && ')
    return dict(term.split(') == ') for term in terms)


def document():
    """A terrain pack's rule set: repeat/random/overlay rules a carrier budget drops."""
    rules = [
        {'id': 'stone', 'method': 'repeat', 'blocks': ['minecraft:stone'], 'faces': FACES, 'width': 2, 'height': 2,
         'tiles': [CTM + f'stone/{n}.png' for n in range(4)]},
        {'id': 'deepslate', 'method': 'repeat', 'blocks': [], 'matchTiles': [TEXTURES + 'deepslate.png'],
         'faces': FACES, 'width': 2, 'height': 2, 'tiles': [CTM + f'deepslate/{n}.png' for n in range(4)]},
        {'id': 'end_stone', 'method': 'random', 'blocks': ['minecraft:end_stone'], 'faces': FACES, 'weights': [1, 3],
         'matchTiles': [TEXTURES + 'end_stone.png'], 'tiles': [CTM + 'end_stone/0.png', '<default>']},
        {'id': 'variation', 'method': 'random', 'blocks': [], 'faces': FACES,
         'matchTiles': [TEXTURES + 'deepslate_top.png'], 'tiles': [CTM + 'deepslate/0.png', CTM + 'deepslate/1.png']},
        {'id': 'glass', 'method': 'horizontal', 'blocks': [],
         'matchTiles': [TEXTURES + 'glass.png', TEXTURES + 'glass_pane.png'],
         'faces': FACES, 'tiles': [CTM + f'glass/{n}.png' for n in range(4)]},
        {'id': 'sand', 'method': 'repeat', 'blocks': [], 'matchTiles': [TEXTURES + 'sand.png'], 'faces': FACES,
         'width': 2, 'height': 2, 'tiles': [CTM + f'sand/{n}.png' for n in range(4)]},
        {'id': 'litter', 'method': 'overlay_random', 'blocks': ['minecraft:sand'], 'faces': ['up'], 'layer': 'cutout',
         'weights': [1, 3], 'tiles': [CTM + 'litter/0.png', '<skip>']},
        {'id': 'wool', 'method': 'repeat', 'blocks': ['minecraft:red_wool', 'minecraft:red_carpet'], 'faces': FACES,
         'width': 1, 'height': 1, 'tiles': [CTM + 'wool/0.png']},
        {'id': 'log', 'method': 'repeat', 'blocks': [], 'matchTiles': [TEXTURES + 'oak_log.png'], 'faces': FACES,
         'width': 1, 'height': 2, 'tiles': [TEXTURES + 'oak_log.png', CTM + 'log/1.png']},
        {'id': 'filtered', 'method': 'repeat', 'blocks': ['minecraft:stone'], 'faces': FACES, 'width': 1, 'height': 1,
         'heights': [[0, 10]], 'tiles': [CTM + 'stone/0.png']},
        {'id': 'chained', 'method': 'random', 'blocks': [], 'matchTiles': [CTM + 'vine/1.png'], 'faces': FACES,
         'tiles': [CTM + 'vine/1.png', '<default>']},
        {'id': 'cactus', 'method': 'repeat', 'blocks': ['minecraft:cactus'], 'faces': FACES, 'width': 1, 'height': 1,
         'tiles': [CTM + 'stone/0.png']},
        {'id': 'grass_top', 'method': 'repeat', 'blocks': [], 'matchTiles': [TEXTURES + 'grass_block_top.png'],
         'faces': FACES, 'width': 2, 'height': 2, 'tiles': [CTM + f'grass_top/{n}.png' for n in range(4)]},
        {'id': 'grass_overlay', 'method': 'repeat', 'blocks': [],
         'matchTiles': [TEXTURES + 'grass_block_side_overlay.png'], 'faces': FACES,
         'width': 2, 'height': 2, 'tiles': [CTM + f'grass_overlay/{n}.png' for n in range(4)]},
        {'id': 'clover', 'method': 'random', 'blocks': [], 'matchTiles': [TEXTURES + 'cloverleaf_overlay.png'],
         'faces': ['up'], 'tiles': [CTM + 'clover/0.png', CTM + 'clover/1.png']},
        {'id': 'glazed', 'method': 'repeat', 'blocks': ['minecraft:white_glazed_terracotta'], 'faces': FACES,
         'width': 1, 'height': 1, 'tiles': [CTM + 'glazed/0.png']},
        {'id': 'mycelium', 'method': 'repeat', 'blocks': [], 'matchTiles': [TEXTURES + 'mycelium_side.png'],
         'faces': FACES, 'width': 1, 'height': 1, 'tiles': [CTM + 'stone/0.png']},
        {'id': 'pane', 'method': 'fixed', 'blocks': ['minecraft:glass_pane'], 'faces': FACES,
         'tiles': [CTM + 'glass/1.png']},
    ]
    grass_part = {'from': [0, 16.5, 0], 'to': [16, 16.5, 16], 'shade': False,
                  'modelRotation': {'x': 0, 'y': 90, 'uvlock': False},
                  'rotation': {'angle': 0, 'axis': 'y', 'origin': [8, 17, 8]},
                  'faces': {'up': {'texture': TEXTURES + 'cloverleaf_overlay.png', 'uv': [0, 0, 16, 16], 'rotation': 0,
                                   'tintIndex': 0, 'cullface': 'up'}}}
    side_layer = [{'texture': TEXTURES + 'grass_block_side_overlay.png', 'orientation': 0, 'tintIndex': 0}]
    zero = {face: 0 for face in FACES}
    grass_faces = {**cube('grass_block_side'), 'up': TEXTURES + 'grass_block_top.png', 'down': TEXTURES + 'dirt.png'}
    clover_group = {'modelSelection': 'java-26.2-multipart-block-position', 'modelChoices': [
        {'weight': 3, 'modelParts': [grass_part]}, {'weight': 1, 'modelParts': []}]}
    variants = {
        'minecraft:grass_block': [
            {'states': {'bct:snowy': True}, 'faces': cube('dirt'), 'orientations': zero},
            {'states': {'bct:snowy': False}, 'faces': grass_faces, 'orientations': zero, 'tintIndices': {'up': 0},
             'faceLayers': {face: side_layer for face in FACES[:4]}, 'modelParts': [],
             'modelPartsGroups': [clover_group]}],
        'minecraft:white_glazed_terracotta': [
            {'states': {'facing_direction': facing}, 'faces': cube('white_glazed_terracotta'),
             'orientations': {**zero, 'up': turn, 'north': (turn + 2) % 4}}
            for facing, turn in ((2, 0), (3, 2), (4, 1), (5, 3))],
        'minecraft:mycelium': [
            {'states': {'bct:snowy': False}, 'modelSelection': 'java-26.2-block-position', 'modelChoices': [
                {'weight': 1, 'faces': cube('mycelium_side', 'mycelium_top'), 'orientations': {**zero, 'up': turn}}
                for turn in range(4)]}],
    }
    base = {'minecraft:stone': cube('stone'), 'minecraft:deepslate': cube('deepslate', 'deepslate_top'),
            'minecraft:end_stone': cube('end_stone'), 'minecraft:sand': cube('sand'),
            'minecraft:red_wool': cube('red_wool'), 'minecraft:oak_log': cube('oak_log', 'oak_log_top'),
            'minecraft:cactus': cube('cactus'), 'minecraft:glass': cube('glass'),
            'minecraft:glass_pane': cube('glass_pane')}
    return {'format_version': 1, 'rules': rules, 'baseTextures': base, 'baseTextureVariants': variants,
            'fullCubeBlocks': ['minecraft:stone', 'minecraft:deepslate', 'minecraft:end_stone', 'minecraft:sand',
                               'minecraft:red_wool', 'minecraft:oak_log', 'minecraft:glass', 'minecraft:grass_block',
                               'minecraft:white_glazed_terracotta', 'minecraft:mycelium'],
            'opaqueBlocks': ['minecraft:stone', 'minecraft:deepslate', 'minecraft:end_stone', 'minecraft:sand',
                             'minecraft:red_wool', 'minecraft:oak_log'],
            'customTintBlocks': ['minecraft:red_wool'],
            'modelTintTypes': {'minecraft:red_wool': [1.0, 0.5, 0.25], 'minecraft:grass_block': 'grass'}}


def source_pack(root):
    for name in ('stone', 'deepslate', 'deepslate_top', 'end_stone', 'sand', 'red_wool', 'oak_log', 'oak_log_top',
                 'cactus', 'grass_block_top', 'grass_block_side', 'dirt', 'white_glazed_terracotta', 'mycelium_top',
                 'mycelium_side'):
        material(root, TEXTURES + name + '.png', (90, 90, 90, 255))
    for name in ('grass_block_side_overlay', 'cloverleaf_overlay'):
        material(root, TEXTURES + name + '.png', (120, 120, 120, 0))
    for n in range(4):
        material(root, CTM + f'grass_top/{n}.png', (40 * n, 120, 40, 255))
        material(root, CTM + f'grass_overlay/{n}.png', (120, 120, 120, 40 * n))
    for n in range(2):
        material(root, CTM + f'clover/{n}.png', (100, 100 + n, 100, 0))
    material(root, CTM + 'glazed/0.png', (200, 10, 10, 255))
    for name in ('glass', 'glass_pane'):
        material(root, TEXTURES + name + '.png', (200, 220, 255, 90))
    for n in range(4):
        material(root, CTM + f'glass/{n}.png', (200, 220, 255, 60 + n))
    for folder in ('stone', 'deepslate', 'sand'):
        for n in range(4):
            material(root, CTM + f'{folder}/{n}.png', (10 * n, 50, 60, 255))
    for n in range(2):
        material(root, CTM + f'end_stone/{n}.png', (200, 200, 100 + n, 255))
    material(root, CTM + 'litter/0.png', (30, 120, 30, 128))
    material(root, CTM + 'wool/0.png', (200, 200, 200, 255))
    material(root, CTM + 'log/1.png', (120, 80, 40, 255))


class PermutationBudgetTests(unittest.TestCase):
    def test_the_costliest_shaped_blocks_go_first_until_the_pack_fits(self):
        report = {'permutations': 70000, 'block_permutations': {
            'minecraft:stone': [9000, None], 'minecraft:oak_stairs': [2560, 'stairs'],
            'minecraft:stone_stairs': [1280, 'stairs'], 'minecraft:oak_slab': [128, 'slab']}}
        self.assertEqual(shaped_over_budget(report, 0, limit=67500), {'minecraft:oak_stairs'})
        self.assertEqual(shaped_over_budget(report, 1000, limit=67500), {'minecraft:oak_stairs', 'minecraft:stone_stairs'})
        self.assertEqual(shaped_over_budget(report, 0, limit=70000), set(), 'a pack within the limit keeps everything')
        self.assertEqual(shaped_over_budget(report, 0, limit=1), {'minecraft:oak_stairs', 'minecraft:stone_stairs',
                                                                    'minecraft:oak_slab'}, 'full cubes are never left out')
        report['block_permutations']['minecraft:white_concrete_stairs'] = [640, 'stairs']
        report['permutations'] += 640
        self.assertEqual(shaped_over_budget(report, 0, limit=70000), {'minecraft:white_concrete_stairs'},
                         'dyed stairs go before the costlier wood and stone stairs')


class RepeatCoordinateTests(unittest.TestCase):
    def test_repeat_index_matches_the_engine_with_texture_orientations(self):
        source = Path(__file__).resolve().parents[1] / 'engine/tiles.mjs'
        script = ("import {repeatIndex} from '" + source.as_uri() + "';let result=[];"
                  "for(let x=-5;x<=5;x++)for(let y=-3;y<=3;y++)"
                  "for(const face of ['north','east','south','west','up','down'])"
                  "for(let o=0;o<8;o++)result.push([x,y,face,o,repeatIndex({x,y,z:2*x-1},face,4,5,'none',o)]);"
                  "console.log(JSON.stringify(result));")
        vectors = json.loads(subprocess.check_output(['node', '--input-type=module', '-e', script], text=True))
        for x, y, face, orientation, expected in vectors:
            self.assertEqual(repeat_index((x, y, 2 * x - 1), face, 4, 5, 'none', orientation), expected)

    def test_coordinate_states_hold_every_residue_a_face_reads(self):
        for face in FACES:
            for orientation in range(8):
                pairs = repeat_moduli(face, 4, 5, 'none', orientation)
                for x, y, z in ((7, -3, 11), (-9, 4, -2)):
                    residues = {axis: [(modulus, value % modulus) for name, modulus in pairs if name == axis]
                                for axis, value in zip('xyz', (x, y, z))}
                    location = tuple(representative(residues[axis]) for axis in 'xyz')
                    self.assertEqual(repeat_index(location, face, 4, 5, 'none', orientation),
                                     repeat_index((x, y, z), face, 4, 5, 'none', orientation))


def drawing_policy():
    """The shipped policy without keep_vanilla, so the drawing features (layers, decorations, weighted models) can be
    checked on the grass, sand and mycelium of the test rule set; the shipped policy keeps those blocks vanilla."""
    policy = load_policy()
    policy['keep_vanilla'] = []
    policy['profiles'] += [
        {'blocks': ['minecraft:grass_block', 'minecraft:mycelium'], 'destroy': 0.6, 'resistance': 0.6,
         'map_color': '#7FB238', 'tool': 'shovel', 'loot': [{'item': 'minecraft:dirt'}]},
        {'blocks': ['minecraft:sand'], 'destroy': 0.5, 'resistance': 0.5, 'map_color': '#F7E9A3', 'tool': 'shovel',
         'loot': 'self'}]
    return policy


class NativeReplacementTests(unittest.TestCase):
    def build(self, root, policy=None, doc=None):
        source = root / 'compiled'
        source_pack(source)
        # The same order as convert_java_author_pack.convert: variations, replacement blocks, then carriers.
        policy = policy or load_policy()
        budget = load_budget()
        budget['carrier_fallback'] = fallback_patterns(policy)
        doc = doc or document()
        _, _, first = apply_budget(doc, budget)
        variations = {item['rule'] for item in first['dropped_rules'] if item.get('native_variation')}
        targets = {rule['id']: sorted(rule_targets(rule, doc)) for rule in doc['rules']}
        data, built = build_replacements(
            doc, {rule: blocks for rule, blocks in targets.items() if rule not in variations}, policy,
            source=source, samples=samples_path(), key='pack-test', output=root / 'replacement',
            passengers={rule: blocks for rule, blocks in targets.items() if rule in variations})
        kept, _, report = apply_budget(doc, budget, native={entry['vanilla'] for entry in data['blocks']})
        return kept, report, data, built

    def block(self, root, name):
        return read_json(root / REPLACE_BP / 'blocks' / (name + '.json'))['minecraft:block']

    def test_every_rule_draws_natively_and_nothing_is_dropped_silently(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            kept, report, data, built = self.build(root, drawing_policy())
            log = next(entry for entry in data['blocks'] if entry['vanilla'] == 'minecraft:oak_log')
            self.assertEqual(log['leafGuard']['radius'], 6, 'logs are replaced with the leaf decay guard')
            self.assertIn('minecraft:azalea_leaves_flowered', log['leafGuard']['leaves'])
            self.assertIn('minecraft:cherry_leaves', log['leafGuard']['leaves'])
            self.assertEqual(log['cost'], 8)
            self.assertEqual(log['strip'], 'minecraft:stripped_oak_log', 'an axe strips a replaced log')
            self.assertFalse({'rotation', 'openTop', 'standOn', 'keepUnder', 'keepNear', 'gravity', 'coveredTop'}
                             & {name for entry in data['blocks'] for name in entry},
                             'no block turns back to vanilla near players')
            self.assertEqual(sorted(built['rules']), ['clover', 'deepslate', 'end_stone', 'glazed', 'grass_overlay',
                                                      'grass_top', 'litter', 'log', 'mycelium', 'pane', 'sand', 'stone',
                                                      'variation', 'wool'])
            self.assertEqual([rule['id'] for rule in kept['rules']], ['glass'],
                             'carriers keep only what native blocks cannot draw')
            self.assertIn('minecraft:glass_pane', built['rules']['pane'], 'glass panes get a pane replacement')
            reasons = {(item['rule'], item['block']): item['reason'] for item in built['unsupported']}
            self.assertIn('neighbors', reasons[('glass', 'minecraft:glass')], 'connected methods stay on carriers')
            self.assertIn('filters', reasons[('filtered', 'minecraft:stone')])
            self.assertEqual(reasons[('cactus', 'minecraft:cactus')], 'not a full cube')
            self.assertEqual(reasons[('wool', 'minecraft:red_carpet')], 'not a full cube')
            self.assertEqual(report['uncarried_targets']['chained'], [],
                             'a rule with no target is reported by the accounting')

    def test_slabs_show_the_tile_of_a_full_block_in_their_place_on_half_a_block(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            material(root / 'compiled', TEXTURES + 'cobblestone.png', (90, 90, 90, 255))
            for n in range(4):
                material(root / 'compiled', CTM + f'cobble/{n}.png', (10 * n, 90, 90, 255))
            doc = document()
            doc['rules'].append({'id': 'cobble', 'method': 'repeat', 'blocks': [],
                                 'matchTiles': [TEXTURES + 'cobblestone.png'], 'faces': FACES, 'width': 2, 'height': 2,
                                 'tiles': [CTM + f'cobble/{n}.png' for n in range(4)]})
            # Bedrock's stone_stairs are cobblestone stairs.
            for block in ('cobblestone', 'cobblestone_slab', 'cobblestone_double_slab', 'stone_stairs', 'nether_brick_fence',
                          'cobblestone_wall'):
                doc['baseTextures']['minecraft:' + block] = cube('cobblestone')
            doc['fullCubeBlocks'] += ['minecraft:cobblestone', 'minecraft:cobblestone_double_slab']
            _, _, data, built = self.build(root, doc=doc)
            self.assertEqual(sorted(built['rules']['cobble']), ['minecraft:cobblestone', 'minecraft:cobblestone_double_slab',
                                                               'minecraft:cobblestone_slab', 'minecraft:cobblestone_wall',
                                                               'minecraft:nether_brick_fence',
                                                               'minecraft:stone_stairs'])
            entries = {entry['vanilla']: entry for entry in data['blocks']}
            slab_entry = entries['minecraft:cobblestone_slab']
            self.assertEqual(slab_entry['shape'], 'slab')
            self.assertEqual(slab_entry['mirror'], {'minecraft:vertical_half': 'bct:vertical_half'})
            self.assertNotIn('shape', entries['minecraft:cobblestone_double_slab'], 'a double slab is a full cube')

            def looks(definition):
                return {permutation['condition']: permutation['components']['minecraft:material_instances']
                        for permutation in definition['permutations']
                        if 'minecraft:material_instances' in permutation['components']}

            cobble, slab = self.block(root, 'r_cobblestone'), self.block(root, 'r_cobblestone_slab')
            self.assertEqual(looks(slab), looks(cobble), 'every place shows the tile the full block shows there')
            self.assertEqual(slab['description']['states']['bct:vertical_half'], ['bottom', 'top'])
            boxes = {permutation['condition']: permutation['components']['minecraft:collision_box']
                     for permutation in slab['permutations'] if 'minecraft:collision_box' in permutation['components']}
            self.assertEqual(boxes, {"q.block_state('bct:vertical_half') == 'bottom'": {'origin': [-8, 0, -8],
                                                                                        'size': [16, 8, 16]},
                                     "q.block_state('bct:vertical_half') == 'top'": {'origin': [-8, 8, -8],
                                                                                     'size': [16, 8, 16]}})
            self.assertTrue(slab['components']['minecraft:liquid_detection']['detection_rules'][0]['can_contain_liquid'])
            geometry = slab['components']['minecraft:geometry']
            self.assertEqual(geometry['bone_visibility']['shape_top'], "q.block_state('bct:vertical_half') == 'top'")
            model = read_json(root / REPLACE_RP / f"models/blocks/{geometry['identifier'].split('.')[1]}_r_cobblestone_slab.geo.json")
            bones = {bone['name']: bone['cubes'][0] for bone in model['minecraft:geometry'][0]['bones']}
            self.assertEqual(bones['shape_bottom']['uv']['north'], {'uv': [0, 8], 'uv_size': [16, 8],
                                                                    'material_instance': 'north'},
                             'a bottom slab side shows the lower half of the tile, as in Java')
            self.assertEqual(bones['shape_top']['uv']['north']['uv'], [0, 0])
            culling = read_json(root / REPLACE_RP / f"block_culling/{geometry['identifier'].split('.')[1]}_r_cobblestone_slab.json")
            culled = {(rule['geometry_part']['bone'], rule['geometry_part']['face'])
                      for rule in culling['minecraft:block_culling_rules']['rules']}
            self.assertIn(('shape_bottom', 'down'), culled)
            self.assertNotIn(('shape_bottom', 'up'), culled, 'the inside face of a slab never culls')
            loot = read_json(root / REPLACE_BP / 'loot_tables/bct/bct_pack_test/r_cobblestone_double_slab.json')
            entry = loot['pools'][0]['entries'][0]
            self.assertEqual((entry['name'], entry['functions'][0]['count']), ('minecraft:cobblestone_slab', 2))
            self.assertEqual(self.block(root, 'r_cobblestone_double_slab')['components']['minecraft:geometry'],
                             'minecraft:geometry.full_block')
            stairs_entry = entries['minecraft:stone_stairs']
            self.assertEqual(stairs_entry['shape'], 'stairs')
            self.assertEqual(stairs_entry['mirror']['minecraft:corner'], 'bct:corner')
            stairs = self.block(root, 'r_stone_stairs')
            self.assertEqual(looks(stairs), looks(cobble), 'stairs show the same tiles as the full block too')
            self.assertEqual(len(stairs['components']['minecraft:geometry']['bone_visibility']), 40)
            self.assertEqual(sum('minecraft:collision_box' in permutation['components']
                                 for permutation in stairs['permutations']), 40)
            loot = read_json(root / REPLACE_BP / 'loot_tables/bct/bct_pack_test/r_stone_stairs.json')
            self.assertEqual(loot['pools'][0]['entries'][0]['name'], 'minecraft:stone_stairs', 'a stair drops itself')
            self.assertEqual(entries['minecraft:nether_brick_fence']['shape'], 'fence')
            fence = self.block(root, 'r_nether_brick_fence')
            self.assertEqual(looks(fence), looks(cobble))
            self.assertEqual(fence['components']['minecraft:connection_rule'], {'accepts_connections_from': 'none'},
                             'vanilla wooden fences must not join a nether brick fence')
            wall = self.block(root, 'r_cobblestone_wall')
            self.assertEqual(entries['minecraft:cobblestone_wall']['shape'], 'wall')
            self.assertEqual(looks(wall), looks(cobble))
            self.assertEqual(len(wall['components']['minecraft:geometry']['bone_visibility']), 9,
                             'a post and a short and a tall arm per side')
            self.assertEqual(sum('minecraft:collision_box' in permutation['components']
                                 for permutation in wall['permutations']), 162)
            self.assertEqual(sum('minecraft:collision_box' in permutation['components']
                                 for permutation in fence['permutations']), 16)

    def test_repeat_blocks_mirror_vanilla_and_cover_every_position(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, _, data, _ = self.build(root)
            stone = self.block(root, 'r_stone')
            self.assertEqual(stone['description']['identifier'], 'bct_pack_test:r_stone')
            two = {'values': {'min': 0, 'max': 1}}
            self.assertEqual(stone['description']['states'], {'bct:x': two, 'bct:y': two, 'bct:z': two})
            self.assertEqual(stone['description']['menu_category'], {'category': 'none', 'is_hidden_in_commands': True})
            self.assertEqual(len(stone['permutations']), 8)
            components = stone['components']
            self.assertEqual(components['minecraft:geometry'], 'minecraft:geometry.full_block')
            # Stone needs a pickaxe: by hand it takes hardness x 5 seconds (x 10/3 over Bedrock's x 1.5),
            # any pickaxe gets the hardness back.
            pickaxe_speed = {'item': {'tags': "q.any_tag('minecraft:is_pickaxe')"}, 'destroy_speed': 1.5}
            self.assertEqual(components['minecraft:destructible_by_mining'],
                             {'seconds_to_destroy': 5.0, 'item_specific_speeds': [pickaxe_speed]})
            self.assertEqual(components['minecraft:destructible_by_explosion'], {'explosion_resistance': 6})
            self.assertEqual(components['minecraft:map_color'], '#707070')
            self.assertEqual(components['minecraft:light_dampening'], 15)
            self.assertEqual(components['minecraft:tags'], ['minecraft:is_pickaxe_item_destructible', 'stone'],
                             'the pickaxe tag and the vanilla tag of stone')
            self.assertEqual(components['minecraft:redstone_conductivity'],
                             {'redstone_conductor': True, 'allows_wire_to_step_down': True})
            self.assertEqual(components['minecraft:instrument_sound'], {'up': 'note.bd'},
                             'a note block on stone plays the bass drum')
            self.assertNotIn('minecraft:flammable', components)
            self.assertFalse([name for name in components if name.startswith('tag:')],
                             'format 1.26.20+ has no tag: components')
            loot = read_json(root / REPLACE_BP / components['minecraft:loot'])
            pickaxe = {'condition': 'match_tool', 'minecraft:match_tool_filter_all': ['minecraft:is_pickaxe']}
            self.assertEqual(loot, {'pools': [{'rolls': 1, 'conditions': [pickaxe], 'entries': [
                {'type': 'item', 'name': 'minecraft:cobblestone', 'weight': 1,
                 'functions': [{'function': 'explosion_decay'}]}]}]},
                'stone drops cobblestone only for a pickaxe')
            sounds = read_json(root / REPLACE_RP / 'blocks.json')
            self.assertEqual(sounds['bct_pack_test:r_stone'], {'sound': 'stone'})
            # Each permutation shows the tile repeat_index picks for its coordinates.
            atlas = read_json(root / REPLACE_RP / 'textures/terrain_texture.json')['texture_data']
            for permutation in stone['permutations']:
                values = condition_values(permutation)
                location = tuple(int(values['bct:' + axis]) for axis in 'xyz')
                for face in FACES:
                    alias = permutation['components']['minecraft:material_instances'][face]['texture']
                    tile = root / 'compiled' / CTM / 'stone' / f'{repeat_index(location, face, 2, 2)}.png'
                    expected = Image.open(tile).getpixel((0, 0))
                    color = Image.open(root / REPLACE_RP / (atlas[alias]['textures'] + '.png')).getpixel((0, 0))
                    self.assertEqual(color, expected)
            entry = next(item for item in data['blocks'] if item['vanilla'] == 'minecraft:stone')
            self.assertEqual(entry['axes'], [['bct:x', 'x', 2], ['bct:y', 'y', 2], ['bct:z', 'z', 2]])
            self.assertIn('minecraft:air', data['open'])
            self.assertIn('minecraft:stone', data['solid'])

    def test_pillar_blocks_keep_their_axis_state_and_turn(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, _, data, _ = self.build(root)
            deepslate = self.block(root, 'r_deepslate')
            self.assertEqual(deepslate['description']['states']['bct:pillar_axis'], ['y', 'x', 'z'])
            entry = next(item for item in data['blocks'] if item['vanilla'] == 'minecraft:deepslate')
            self.assertEqual(entry['mirror'], {'pillar_axis': 'bct:pillar_axis'})
            rotations = {permutation['condition'].split("'")[3]:
                         permutation['components'].get('minecraft:transformation')
                         for permutation in deepslate['permutations']}
            self.assertEqual(rotations, {'x': {'rotation': [0, 0, 90]}, 'y': {'rotation': [0, 0, 0]},
                                         'z': {'rotation': [90, 0, 0]}})
            for permutation in deepslate['permutations']:
                instances = permutation['components']['minecraft:material_instances']
                self.assertEqual(instances['up']['texture'], instances['down']['texture'],
                                 'the end texture stays on the local ends')

    def test_random_overlay_and_tint_materials(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, _, data, _ = self.build(root, drawing_policy())
            entry = next(item for item in data['blocks'] if item['vanilla'] == 'minecraft:end_stone')
            self.assertEqual(entry['random'], [{'state': 'bct:r0', 'count': 2, 'face': 'north', 'loops': 0,
                                                'symmetry': 'none', 'weights': [1, 3]}])
            deepslate = next(item for item in data['blocks'] if item['vanilla'] == 'minecraft:deepslate')
            self.assertEqual(len(deepslate['random']), 1,
                             'a replaced block keeps showing the atlas variation of its other faces')
            sand = self.block(root, 'r_sand')
            self.assertEqual(len(sand['permutations']), 16)
            self.assertEqual(sand['components']['minecraft:geometry']['bone_visibility'],
                             {'layer_up': "(q.block_state('bct:r0') == 0)"})
            self.assertTrue((root / REPLACE_RP / 'models/blocks/bct_pack_test_r_sand.geo.json').is_file())
            instances = sand['permutations'][0]['components']['minecraft:material_instances']
            self.assertEqual({item['render_method'] for item in instances.values()}, {'alpha_test_single_sided'},
                             'one render method per block: faces and cut-out layers alike')
            wool = self.block(root, 'r_red_wool')
            alias = wool['permutations'][0]['components']['minecraft:material_instances']['north']['texture']
            textures = root / REPLACE_RP / 'textures/blocks'
            self.assertEqual(Image.open(textures / (alias + '.png')).getpixel((0, 0)), (200, 100, 50, 255),
                             'the fixed block tint is baked in')
            texture_set = read_json(textures / (alias + '.texture_set.json'))['minecraft:texture_set']
            self.assertEqual(texture_set['color'], alias)
            self.assertTrue((textures / (texture_set['normal'] + '.png')).is_file(),
                            'tinted materials share the normal map')
            self.assertIn('metalness_emissive_roughness_subsurface', texture_set)
            self.assertEqual(wool['components']['minecraft:flammable'],
                             {'catch_chance_modifier': 30, 'destroy_chance_modifier': 60, 'lava_flammable': 'always'})
            self.assertEqual(wool['components']['minecraft:instrument_sound'], {'up': 'note.guitar'})
            self.assertEqual(wool['components']['minecraft:destructible_by_mining'], {'seconds_to_destroy': 0.8},
                             'wool drops by hand')
            self.assertIn('minecraft:is_shears_item_destructible', wool['components']['minecraft:tags'])

    def test_source_addon_carries_replacements_and_their_rtx_materials(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, _, data, _ = self.build(root)

            def manifest(name, kind, capabilities=()):
                header = {'name': name, 'uuid': str(uuid.uuid5(uuid.NAMESPACE_URL, name)), 'version': [1, 0, 0],
                          'min_engine_version': [1, 26, 50]}
                module = {'type': 'resources' if kind == 'rp' else 'data',
                          'uuid': str(uuid.uuid5(uuid.NAMESPACE_URL, name + 'module')), 'version': [1, 0, 0]}
                return {'format_version': 2, 'header': header, 'modules': [module], 'capabilities': list(capabilities)}

            base, rtx = root / 'base', root / 'rtx'
            for pack, capabilities in ((base, ['pbr']), (rtx, ['pbr', 'raytraced'])):
                pack.mkdir()
                (pack / 'manifest.json').write_text(json.dumps(manifest(pack.name, 'rp', capabilities)))
                (pack / 'blocks.json').write_text(json.dumps({'format_version': [1, 1, 0],
                                                              'stone': {'textures': 'stone', 'sound': 'stone'}}))
                (pack / 'textures').mkdir()
                (pack / 'textures/terrain_texture.json').write_text(json.dumps(
                    {'texture_data': {'stone': {'textures': 'textures/blocks/stone'}}}))
            connected = root / 'connected.mcaddon'
            with zipfile.ZipFile(connected, 'w') as addon:
                addon.writestr('Connected_BP/manifest.json', json.dumps(manifest('connected-bp', 'bp')))
                addon.writestr('Connected_RP/manifest.json', json.dumps(manifest('connected-rp', 'rp', ['pbr'])))
                addon.writestr('Connected_BP/scripts/source-data.js',
                               source_data([packet('connected', 'pack-test', {'rules': []})]))
            output = compose_source_addon(rtx, connected, root / 'pack.mcaddon', key='pack-test', title='Pack test',
                                          replacement_addon=root / 'replacement', raytraced=True)
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                self.assertIn('Source_BP/blocks/r_stone.json', names)
                self.assertIn('Source_BP/loot_tables/bct/bct_pack_test/r_stone.json', names)
                packets = read_packets(archive.read('Source_BP/scripts/source-data.js').decode())
                self.assertEqual(packets['replace'], data)
                blocks = json.loads(archive.read('Source_RP/blocks.json'))
                self.assertEqual(blocks['bct_pack_test:r_stone'], {'sound': 'stone'}, 'replacement sounds are kept')
                atlas = json.loads(archive.read('Source_RP/textures/terrain_texture.json'))['texture_data']
                stone = json.loads(archive.read('Source_BP/blocks/r_stone.json'))['minecraft:block']
                alias = stone['components']['minecraft:material_instances']['north']['texture']
                self.assertIn(alias, atlas)
                material = json.loads(archive.read(f'Source_RP/textures/blocks/{alias}.texture_set.json'))
                self.assertIn('metalness_emissive_roughness', material['minecraft:texture_set'],
                              'replacement materials use RGB MER, which ray tracing needs')
                self.assertNotIn('metalness_emissive_roughness_subsurface', material['minecraft:texture_set'])

    def test_everything_written_passes_mojangs_schemas(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.build(root)
            problems = check_tree(root / REPLACE_BP, root / REPLACE_RP, samples_path())
            self.assertEqual(problems, [])
            for path in (root / REPLACE_RP / 'block_culling').glob('*.json'):
                for rule in read_json(path)['minecraft:block_culling_rules']['rules']:
                    # Without a condition a rule culls against full opaque neighbors; "default" is not a value.
                    self.assertIn(rule.get('condition', 'same_block'), ('same_block',))

    def test_blocks_with_vanilla_behavior_stay_vanilla(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            kept, report, data, built = self.build(root)
            replaced = {entry['vanilla'] for entry in data['blocks']}
            for block in ('minecraft:grass_block', 'minecraft:sand', 'minecraft:mycelium'):
                self.assertNotIn(block, replaced, block + ' spreads, falls or grows plants: it is never replaced')
                self.assertIn(block, built['kept_vanilla'])
            self.assertIn('spread', built['kept_vanilla']['minecraft:grass_block'])
            self.assertIn('fall', built['kept_vanilla']['minecraft:sand'])
            reasons = {(item['rule'], item['block']): item['reason'] for item in built['unsupported']}
            self.assertTrue(reasons[('grass_top', 'minecraft:grass_block')].startswith('stays vanilla: '))
            self.assertEqual(report['uncarried_targets']['grass_top'], ['minecraft:grass_block'],
                             'what is not drawn is reported')
            self.assertFalse((root / REPLACE_BP / 'blocks/r_grass_block.json').exists())
            self.assertIn('minecraft:stone', replaced)

    def test_connected_methods_stay_on_carriers(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            kept, _, data, built = self.build(root)
            self.assertNotIn('minecraft:glass', [entry['vanilla'] for entry in data['blocks']])
            self.assertEqual([rule['id'] for rule in kept['rules']], ['glass'])
            self.assertNotIn('glass', built['rules'])


class PolicyBlockTests(unittest.TestCase):
    """Grass, glazed terracotta, panes, weighted models and engine behaviors."""
    build = NativeReplacementTests.build
    block = NativeReplacementTests.block

    def entry(self, data, vanilla):
        return next(item for item in data['blocks'] if item['vanilla'] == vanilla)

    def geometry(self, root, stem):
        return read_json(root / REPLACE_RP / f'models/blocks/bct_pack_test_{stem}.geo.json')['minecraft:geometry'][0]

    def test_grass_repeats_top_and_sides_with_a_tinted_overlay_and_java_decorations(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, _, data, built = self.build(root, drawing_policy())
            grass = self.block(root, 'r_grass_block')
            states = grass['description']['states']
            self.assertEqual(states['bct:g0'], {'values': {'min': 0, 'max': 1}},
                             'the multipart decoration group is a state')
            self.assertEqual({name: values['values']['max'] + 1 for name, values in states.items()
                              if name in ('bct:x', 'bct:z')}, {'bct:x': 2, 'bct:z': 2})
            instances = grass['permutations'][0]['components']['minecraft:material_instances']
            self.assertEqual(instances['up']['tint_method'], 'grass', 'the top keeps its biome tint')
            self.assertNotIn('tint_method', instances['north'], 'the soil under the side overlay is not tinted')
            self.assertEqual(instances['layer_north']['tint_method'], 'grass',
                             'the side overlay is a tinted layer like in Java')
            self.assertEqual({item['render_method'] for item in instances.values()}, {'alpha_test_single_sided'})
            decal = instances['g0_0_0_up']
            self.assertEqual((decal['tint_method'], decal['face_dimming'], decal['ambient_occlusion']),
                             ('grass', False, 0.0))
            clovers = {permutation['components']['minecraft:material_instances']['g0_0_0_up']['texture']
                       for permutation in grass['permutations']}
            self.assertLessEqual(len(clovers), 2, 'the clover tile is a material per pattern cell, not a bone per tile')
            # The top tile follows the 2x2 repeat.
            atlas = read_json(root / REPLACE_RP / 'textures/terrain_texture.json')['texture_data']
            for permutation in grass['permutations']:
                values = condition_values(permutation)
                location = (int(values['bct:x']), int(values.get('bct:y', 0)), int(values['bct:z']))
                alias = permutation['components']['minecraft:material_instances']['up']['texture']
                color = Image.open(root / REPLACE_RP / (atlas[alias]['textures'] + '.png')).getpixel((0, 0))
                self.assertEqual(color[0], 40 * repeat_index(location, 'up', 2, 2))
            geometry = self.geometry(root, 'r_grass_block')
            bones = {bone['name']: bone for bone in geometry['bones']}
            decals = [name for name in bones if name.startswith('g0_')]
            self.assertEqual(decals, ['g0_0_0'], 'one bone per Java model part')
            bone = bones['g0_0_0']
            self.assertEqual(bone['cubes'][0]['origin'], [-8, 16.5, -8])
            self.assertEqual(bone['cubes'][0]['uv']['up']['material_instance'], 'g0_0_0_up')
            self.assertEqual(bone['rotation'], [0, -90, 0], 'the Java model y rotation turns the decoration')
            visibility = grass['components']['minecraft:geometry']['bone_visibility']
            self.assertEqual(visibility['g0_0_0'], "q.block_state('bct:g0') == 0")
            self.assertEqual(visibility['layer_north'], '1.0')
            culling = read_json(root / REPLACE_RP / 'block_culling/bct_pack_test_r_grass_block.json')
            self.assertIn({'geometry_part': {'bone': 'g0_0_0', 'cube': 0, 'face': 'up'}, 'direction': 'up'},
                          culling['minecraft:block_culling_rules']['rules'])
            entry = self.entry(data, 'minecraft:grass_block')
            self.assertEqual(entry['models'], [{'state': 'bct:g0', 'weights': [3, 1],
                                                'selection': 'java-26.2-multipart-block-position'}])
            self.assertIn('minecraft:grass_block', built['rules']['grass_overlay'],
                          'the overlay rule draws on the layer')

    def test_glazed_terracotta_turns_its_texture_with_facing_direction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, _, data, _ = self.build(root)
            glazed = self.block(root, 'r_white_glazed_terracotta')
            self.assertEqual(glazed['components']['minecraft:movable'], {'movement_type': 'push'},
                             'sticky pistons do not pull it')
            self.assertEqual(glazed['description']['states']['bct:facing_direction'], {'values': {'min': 0, 'max': 5}})
            visibility = glazed['components']['minecraft:geometry']['bone_visibility']
            self.assertEqual(visibility['up_o2'], "q.block_state('bct:facing_direction') == 3")
            self.assertEqual(visibility['north_o3'], "q.block_state('bct:facing_direction') == 4")
            bones = {bone['name']: bone for bone in self.geometry(root, 'r_white_glazed_terracotta')['bones']}
            self.assertEqual(bones['up_o1']['cubes'][0]['uv']['up']['uv_rotation'], 270,
                             'top faces turn the other way round')
            self.assertEqual(bones['north_o1']['cubes'][0]['uv']['north']['uv_rotation'], 90)
            self.assertEqual(self.entry(data, 'minecraft:white_glazed_terracotta')['mirror'],
                             {'facing_direction': 'bct:facing_direction'})

    def test_weighted_java_models_become_a_model_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, _, data, _ = self.build(root, drawing_policy())
            mycelium = self.block(root, 'r_mycelium')
            self.assertEqual(mycelium['description']['states']['bct:m'], {'values': {'min': 0, 'max': 3}})
            self.assertEqual(self.entry(data, 'minecraft:mycelium')['models'],
                             [{'state': 'bct:m', 'weights': [1, 1, 1, 1], 'selection': 'java-26.2-block-position'}])
            visibility = mycelium['components']['minecraft:geometry']['bone_visibility']
            self.assertEqual(visibility['up_o2'], "q.block_state('bct:m') == 2")

    def test_panes_get_a_post_and_arms_shown_by_their_connections(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, _, data, _ = self.build(root)
            pane = self.block(root, 'r_glass_pane')
            entry = self.entry(data, 'minecraft:glass_pane')
            self.assertEqual(entry['pane'], {'north': 'bct:connection_north', 'south': 'bct:connection_south',
                                             'west': 'bct:connection_west', 'east': 'bct:connection_east'})
            self.assertEqual(entry['mirror']['minecraft:connection_east'], 'bct:connection_east')
            self.assertEqual(sorted(entry['bools']), ['minecraft:connection_east', 'minecraft:connection_north',
                                                      'minecraft:connection_south', 'minecraft:connection_west'],
                             'boolean vanilla states are mirrored as 0 or 1')
            self.assertEqual(pane['description']['states']['bct:connection_east'], {'values': {'min': 0, 'max': 1}})
            self.assertTrue(entry['open'])
            self.assertIn('minecraft:iron_bars', data['paneConnect'])
            self.assertIn('minecraft:cobblestone_wall', data['paneConnect'])
            self.assertEqual(len(pane['permutations']), 16)
            boxes = {permutation['condition']: permutation['components']['minecraft:collision_box']
                     for permutation in pane['permutations']}
            east_west = ("q.block_state('bct:connection_north') == 0 && q.block_state('bct:connection_south') == 0 && "
                         "q.block_state('bct:connection_west') == 1 && q.block_state('bct:connection_east') == 1")
            self.assertEqual(boxes[east_west], {'origin': [-8, 0, -1], 'size': [16, 16, 2]})
            visibility = pane['components']['minecraft:geometry']['bone_visibility']
            self.assertEqual(visibility['arm_east'], "q.block_state('bct:connection_east')")
            self.assertEqual(visibility['north_s0_p'], "q.block_state('bct:connection_west')",
                             'the broad face over the west arm')
            self.assertEqual(len(visibility), 4 * 3 + 4, 'three strips per broad face and four arms')
            self.assertFalse([name for name in pane['components'] if name.startswith('tag:')])
            self.assertEqual(pane['components']['minecraft:redstone_conductivity'],
                             {'redstone_conductor': False, 'allows_wire_to_step_down': False})
            loot = read_json(root / REPLACE_BP / pane['components']['minecraft:loot'])
            self.assertEqual(loot, {'pools': []}, 'glass panes drop nothing without Silk Touch')

    def test_replacements_carry_the_vanilla_tags_of_their_block(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, _, data, built = self.build(root)
            log = self.block(root, 'r_oak_log')
            self.assertEqual(log['components']['minecraft:tags'],
                             ['log', 'minecraft:is_axe_item_destructible', 'oak', 'wood'],
                             'vanilla leaves count blocks tagged log as logs')
            self.assertEqual(built['vanilla_tags']['minecraft:oak_log'], ['log', 'oak', 'wood'])
            self.assertEqual(built['vanilla_tags']['minecraft:stone'], ['stone'])
            self.assertNotIn('minecraft:end_stone', built['vanilla_tags'], 'end stone is in no vanilla tag list')
            self.assertEqual(self.block(root, 'r_end_stone')['components']['minecraft:tags'],
                             ['minecraft:is_pickaxe_item_destructible'])

    def test_vanilla_tags_are_vanilla_block_tags(self):
        policy = load_policy()
        section = policy['vanilla_tags']
        self.assertEqual(set(section['tags']) - set(section['known']), set())
        for name in ('log', 'wood', 'stone'):
            self.assertIn(name, section['tags'])
        self.assertEqual(vanilla_tags_of('minecraft:stripped_cherry_log', policy), ['log', 'wood'],
                         'every log gets log and wood')
        self.assertEqual(vanilla_tags_of('minecraft:mangrove_wood', policy), ['log', 'wood'])
        self.assertEqual(vanilla_tags_of('minecraft:crimson_planks', policy), ['wood'], 'every plank gets wood')
        self.assertEqual(vanilla_tags_of('minecraft:jungle_log', policy), ['jungle', 'log', 'wood'])
        self.assertEqual(vanilla_tags_of('minecraft:cobblestone', policy), ['stone'])
        self.assertEqual(vanilla_tags_of('minecraft:deepslate', policy), [], 'only blocks the vanilla tag lists name')
        bad = json.loads(json.dumps(policy))
        bad['vanilla_tags']['tags']['made_up'] = ['minecraft:stone']
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'policy.json'
            path.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):
                load_policy(path)

    def test_only_blocks_that_need_the_exact_vanilla_block_keep_a_neighbor_vanilla(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, _, data, built = self.build(root)
            chorus = {'blocks': ['minecraft:chorus_flower', 'minecraft:chorus_plant'], 'needs': ['minecraft:end_stone'],
                      'side': 'below'}
            self.assertIn(chorus, data['needs'],
                          'end stone is replaced here: chorus keeps the end stone it stands on vanilla')
            self.assertFalse([entry for entry in data['needs'] if 'minecraft:cocoa' in entry['blocks']],
                             'no jungle log is replaced here, so cocoa needs nothing from the engine')
            report = {tuple(item['blocks']): item for item in built['needs_vanilla']}
            self.assertEqual(report[('minecraft:cocoa',)]['keeps_vanilla'], [])
            self.assertEqual(report[('minecraft:cocoa',)]['side'], 'side')
            self.assertIn('minecraft:jungle_log', report[('minecraft:cocoa',)]['needs'])
            self.assertEqual(report[('minecraft:chorus_flower', 'minecraft:chorus_plant')]['keeps_vanilla'],
                             ['minecraft:end_stone'])
            self.assertTrue(built['attachments'] and all(item['reason'] for item in built['attachments']),
                            'the report lists the attachments')

    def test_attachments_never_keep_a_block_vanilla(self):
        policy = load_policy()
        samples = samples_path()
        names = {item['name'] for item in
                 read_json(samples / 'metadata/vanilladata_modules/mojang-blocks.json')['data_items']}

        def named_by(section):
            return {name for entry in policy[section] for name in names
                    if any(fnmatch.fnmatchcase(name, pattern) for pattern in entry['blocks'])}

        needy, attached = named_by('needs_vanilla'), named_by('attachments')
        for block in ('minecraft:torch', 'minecraft:lantern', 'minecraft:lever', 'minecraft:stone_button',
                      'minecraft:rail', 'minecraft:red_carpet', 'minecraft:stone_pressure_plate', 'minecraft:oak_slab',
                      'minecraft:oak_stairs', 'minecraft:snow_layer', 'minecraft:ladder', 'minecraft:vine',
                      'minecraft:wall_sign', 'minecraft:wall_banner', 'minecraft:bell', 'minecraft:frame',
                      'minecraft:redstone_wire', 'minecraft:unpowered_repeater'):
            self.assertIn(block, attached, block + ' only needs a sturdy face')
            self.assertNotIn(block, needy)
        self.assertEqual(needy & attached, set())
        self.assertIn('minecraft:cocoa', needy)
        for section in ('needs_vanilla', 'attachments'):
            for entry in policy[section]:
                for pattern in entry['blocks'] + entry.get('needs', []):
                    self.assertTrue(any(fnmatch.fnmatchcase(name, pattern) for name in names),
                                    f'{section}: {pattern} names no Bedrock block')
        for tag, patterns in policy['vanilla_tags']['tags'].items():
            for pattern in patterns:
                self.assertTrue(any(fnmatch.fnmatchcase(name, pattern) for name in names),
                                f'vanilla_tags {tag}: {pattern} names no Bedrock block')

    def test_behaviors_reach_the_engine_data(self):
        policy = load_policy()
        names = {item['blocks'][0] for item in policy['behaviors']}
        self.assertEqual(names, {'minecraft:*_log'}, 'logs are the only blocks with engine behaviors')
        self.assertNotIn('minecraft:grass_block', fallback_patterns(policy))
        self.assertNotIn('*_log', ' '.join(fallback_patterns(policy)))
        kept = [pattern for item in policy['keep_vanilla'] for pattern in item['blocks']]
        for block in ('minecraft:grass_block', 'minecraft:sand', 'minecraft:ice', 'minecraft:redstone_ore',
                      'minecraft:soul_sand', 'minecraft:bookshelf', 'minecraft:tube_coral_block'):
            self.assertIn(block, kept)


if __name__ == '__main__':
    unittest.main()
