"""Overlay surfaces: the pack's overlay rules as native blocks (converter/overlay_surfaces.py).

All packs here are made up. The engine tests (tests/test_overlay_surfaces.mjs) read
tests/fixtures/overlay/engine-data.json, which this module builds:

    python tests/test_overlay_surfaces.py --write-fixture
"""
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'converter'))
from common import read_json, samples_path
import overlay_surfaces as surfaces
from import_java_ctm import import_rules
from java_block_states import resolve_known_block_selection

FIXTURE = Path(__file__).resolve().parent / 'fixtures/overlay/engine-data.json'
FACES = ('north', 'east', 'south', 'west', 'up', 'down')
SIDE_FACES = FACES[:4]
HAS_SAMPLES = (samples_path() / 'metadata/vanilladata_modules/mojang-blocks.json').is_file()


def texture(name):
    return f'assets/minecraft/textures/block/{name}.png'


def all_faces(texture_path):
    return {face: texture_path for face in FACES}


def tile(pack, path, color, alpha=255):
    """A 16 pixel tile with one opaque row along the top and fainter rows under it."""
    target = Path(pack) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new('RGBA', (16, 16), (0, 0, 0, 0))
    for x in range(16):
        image.putpixel((x, 0), color + (alpha,))
        image.putpixel((x, 1), color + (40,))
        image.putpixel((x, 2), color + (12,))
    image.save(target)
    return path


def add_material_maps(pack, path):
    """A normal and MERS map beside a tile, like a converted LabPBR pack."""
    source = pack / path
    Image.new('RGB', (16, 16), (128, 128, 255)).save(source.with_name(source.stem + '_normal.png'))
    Image.new('RGBA', (16, 16), (0, 0, 200, 0)).save(source.with_name(source.stem + '_mers.png'))
    texture_set = {'color': source.stem, 'normal': source.stem + '_normal',
                   'metalness_emissive_roughness_subsurface': source.stem + '_mers'}
    document = {'format_version': '1.21.30', 'minecraft:texture_set': texture_set}
    source.with_suffix('.texture_set.json').write_text(json.dumps(document))


def made_up_pack(folder):
    """A made-up compiled pack and its imported rules: grass and sand overlays and a random overlay on sand."""
    pack = Path(folder) / 'compiled'
    grass = [tile(pack, f'assets/minecraft/optifine/ctm/edge_grass/{number}.png', (110, 110, 110))
             for number in range(1, 18)]
    sand = [tile(pack, f'assets/minecraft/optifine/ctm/edge_sand/{number}.png', (220, 200, 150))
            for number in range(1, 18)]
    leaves = [tile(pack, f'assets/minecraft/optifine/ctm/fallen/{number}.png', (150, 70, 30))
              for number in range(1, 3)]
    for path in sand:
        add_material_maps(pack, path)
    grass_block = {**all_faces(texture('grass_block_side')), 'up': texture('grass_block_top'), 'down': texture('dirt')}
    snowy_grass = {'states': {'bct:snowy': True},
                   'faces': {**all_faces(texture('grass_block_snow')), 'up': texture('snow')}}
    plain_grass = {'states': {'bct:snowy': False}, 'faces': dict(grass_block),
                   'faceLayers': {face: [{'texture': texture('grass_block_side_overlay'), 'tintIndex': 0}]
                                  for face in SIDE_FACES}}
    grass_rule = {'id': 'java_0', 'filename': 'edge_grass.properties', 'method': 'overlay', 'blocks': [],
                  'matchTiles': [texture('stone'), texture('cobblestone'), texture('dirt_path_top')],
                  'connectTiles': [texture('grass_block_top')], 'tiles': grass, 'faces': list(FACES),
                  'tintIndex': 0, 'tintBlock': 'minecraft:grass_block', 'layer': 'cutout'}
    sand_rule = {'id': 'java_1', 'filename': 'edge_sand.properties', 'method': 'overlay', 'blocks': [],
                 'matchTiles': [texture('stone'), texture('cobblestone')], 'connectBlocks': ['minecraft:sand'],
                 'connectTiles': [texture('sand')], 'tiles': sand, 'faces': list(FACES), 'layer': 'cutout'}
    fallen_rule = {'id': 'java_2', 'filename': 'fallen.properties', 'method': 'overlay_random',
                   'blocks': ['minecraft:sand'], 'tiles': leaves + ['<skip>'], 'weights': [1, 1, 8], 'randomLoops': 1,
                   'faces': ['up'], 'layer': 'cutout'}
    document = {
        'dialect': 'optifine',
        'baseTextures': {
            'minecraft:stone': all_faces(texture('stone')),
            'minecraft:cobblestone': all_faces(texture('cobblestone')),
            'minecraft:sand': all_faces(texture('sand')),
            'minecraft:grass_block': grass_block,
            'minecraft:grass_path': {'up': texture('dirt_path_top'),
                                     **{face: texture('dirt_path_side') for face in SIDE_FACES}},
            'minecraft:cobblestone_slab': all_faces(texture('cobblestone')),
            'minecraft:cobblestone_double_slab': all_faces(texture('cobblestone')),
            'minecraft:stone_button': all_faces(texture('stone')),
        },
        'baseTextureVariants': {'minecraft:grass_block': [snowy_grass, plain_grass]},
        'fullCubeBlocks': ['minecraft:stone', 'minecraft:cobblestone', 'minecraft:sand', 'minecraft:grass_block'],
        'modelTintTypes': {'minecraft:grass_block': 'grass'},
        'rules': [grass_rule, sand_rule, fallen_rule],
    }
    return document, pack


def build_made_up(folder, replaced=None):
    document, pack = made_up_pack(folder)
    replacement_report = {'rules': replaced or {}}
    return surfaces.build(document, pack, Path(folder) / 'overlay', key='made-up', samples=samples_path(),
                          replacement_report=replacement_report)


def molang(expression, states):
    """Evaluates the block-state Molang the converter writes (comparisons, && and ||)."""
    code = re.sub(r"q\.block_state\('([^']+)'\)", lambda match: f"S[{match[1]!r}]", expression)
    return eval(code.replace('&&', ' and ').replace('||', ' or '), {'S': states})


def channel_state_values(channel, option):
    """The value of each of a channel's block states when the channel holds option."""
    values = {}
    rest = option
    for name, size in channel['states']:
        values[name] = rest % size
        rest = rest // size
    return values


@unittest.skipUnless(HAS_SAMPLES, 'needs BEDROCK_SAMPLES')
class OverlaySurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temporary.name)
        cls.data, cls.report = build_made_up(cls.folder, {'java_2': ['minecraft:sand']})
        cls.bp = cls.folder / 'overlay/Overlay_BP'
        cls.rp = cls.folder / 'overlay/Overlay_RP'

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def blocks(self):
        return {path.stem: read_json(path)['minecraft:block'] for path in sorted((self.bp / 'blocks').glob('*.json'))}

    def test_combo_table_and_tiles_match_the_engine(self):
        script = ("import {OVERLAY_COMBOS, overlayTiles} from './engine/overlay.mjs';"
                  "console.log(JSON.stringify(OVERLAY_COMBOS.map(([e, c]) => [e, c, overlayTiles(e, c)])));")
        output = subprocess.check_output(['node', '--input-type=module', '-e', script], cwd=ROOT, text=True)
        expected = [[edges, corners, surfaces.overlay_tiles(edges, corners)] for edges, corners in surfaces.COMBOS]
        self.assertEqual(json.loads(output), expected)

    def test_faces_follow_where_java_finds_a_connect_texture(self):
        drawn = {item['rule']: item for item in self.report['rules_drawn']}
        self.assertEqual(set(drawn), {'java_0', 'java_1', 'java_2'})
        # Grass blocks show grass_block_top only on top, so the grass overlay never reaches side faces.
        # Lowered tops (paths, bottom slabs) would need geometry below the surface's cell: not drawn.
        self.assertEqual(drawn['java_0']['faces'], {'up': [0]})
        self.assertIn('north, south, west, east, down', drawn['java_0']['problems'][0])
        self.assertEqual(set(drawn['java_1']['faces']), set(FACES))
        self.assertEqual(drawn['java_2']['faces'], {'up': [0]})
        # Buttons show stone too, but no face of a button is a whole square.
        self.assertNotIn('minecraft:stone_button', drawn['java_0']['host_blocks'])
        self.assertNotIn('minecraft:grass_path', self.data['shapes'], 'a path top is lowered: not a host')
        self.assertEqual(self.data['shapes']['minecraft:cobblestone_slab'],
                         [{'states': {'minecraft:vertical_half': ['bottom']}, 'faces': {'up': -8}},
                          {'states': {'minecraft:vertical_half': ['top']}, 'faces': {'up': 0}}])
        # A Bedrock double slab is a full block (Java's double slab state), on every face.
        self.assertNotIn('minecraft:cobblestone_double_slab', self.data['shapes'])
        self.assertIn('minecraft:cobblestone_double_slab', self.data['cubes'])
        self.assertIn('minecraft:cobblestone_double_slab', drawn['java_1']['host_blocks'])
        self.assertEqual(self.data['sources'],
                         {'minecraft:grass_block': ['up'], 'minecraft:sand': list(surfaces.FACES)})
        self.assertEqual(self.data['hosts'], {'minecraft:sand': ['up']})

    def test_engine_rules_keep_the_java_selection_fields(self):
        rules = {rule['id']: rule for rule in self.data['rules']}
        names = ('stone', 'cobblestone', 'dirt_path_top', 'grass_block_top', 'sand')
        textures = sorted({texture(name) for name in names})
        self.assertEqual(rules['java_1']['connectBlocks'], ['minecraft:sand'])
        self.assertEqual(rules['java_1']['connectTiles'], [textures.index(texture('sand'))])
        self.assertEqual(rules['java_2']['skip'], [2])
        self.assertEqual(rules['java_2']['weights'], [1, 1, 8])
        self.assertEqual(rules['java_2']['randomLoops'], 1)
        self.assertEqual(rules['java_2']['replaced'], ['minecraft:sand'],
                         'a replacement that draws the rule itself is skipped')
        self.assertNotIn('replaced', rules['java_0'])
        snowy = self.data['blocks']['minecraft:grass_block']['variants'][0]
        self.assertEqual(snowy['states'], {'bct:snowy': True})
        self.assertNotIn('up', snowy['faces'], 'a snowy grass block shows no connect texture on top')
        self.assertIn('minecraft:grass_block', self.data['solid'])

    def test_blocks_stay_within_bedrock_limits(self):
        total = 0
        for name, block in self.blocks().items():
            states = block['description']['states']
            self.assertTrue(all(1 <= len(values) <= 16 for values in states.values()), name)
            permutations = math.prod(len(values) for values in states.values())
            self.assertLessEqual(permutations, surfaces.MAX_TYPE_PERMUTATIONS, name)
            total += permutations
            components = block['components']
            self.assertLessEqual(len(components['minecraft:geometry']['bone_visibility']), 64, name)
            self.assertLessEqual(len(components['minecraft:material_instances']), 64, name)
        self.assertEqual(total, self.report['permutations'])
        self.assertLessEqual(total, surfaces.MAX_SURFACE_PERMUTATIONS)

    def test_surface_blocks_are_out_of_the_way(self):
        for block in self.blocks().values():
            components = block['components']
            self.assertIs(components['minecraft:collision_box'], False)
            self.assertIs(components['minecraft:selection_box'], False)
            self.assertEqual(components['minecraft:replaceable'], {})
            self.assertEqual(components['minecraft:light_dampening'], 0)
            detection = components['minecraft:liquid_detection']['detection_rules'][0]
            self.assertEqual(detection['on_liquid_touches'], 'broken')
            self.assertEqual(components['minecraft:movable'], {'movement_type': 'popped'})
            self.assertEqual(components['minecraft:precipitation_interactions'], {'precipitation_behavior': 'none'})
            self.assertEqual(components['minecraft:destruction_particles']['particle_count'], 0)
            self.assertEqual(read_json(self.bp / components['minecraft:loot']), {'pools': []})
        # A top surface goes with the block under it: it names every host (and the replacements standing for them).
        filters = {block['description']['identifier']: block['components'].get('minecraft:placement_filter')
                   for block in self.blocks().values()}
        for entry in self.data['types']:
            top_only = all(channel['face'] == 'up' for channel in entry['channels'])
            self.assertEqual(filters[entry['block']] is not None, top_only, entry['block'])

    def test_materials_carry_tint_cutout_and_maps(self):
        materials = {}
        for block in self.blocks().values():
            for name, material in block['components']['minecraft:material_instances'].items():
                if name != '*':
                    materials[name] = material
        grass_materials = [material for name, material in materials.items() if name.startswith('java_0_')]
        other_materials = [material for name, material in materials.items() if not name.startswith('java_0_')]
        self.assertTrue(all(material['tint_method'] == 'grass' for material in grass_materials))
        self.assertTrue(all(material['tint_method'] == 'none' for material in other_materials))
        self.assertTrue(all(material['render_method'] == 'alpha_test_single_sided' for material in materials.values()))
        atlas = read_json(self.rp / 'textures/terrain_texture.json')['texture_data']
        self.assertEqual(len(atlas), 17 + 17 + 2)
        sand_path = self.rp / (atlas['bct_ov_made_up_java_1_8']['textures'] + '.texture_set.json')
        sand = read_json(sand_path)['minecraft:texture_set']
        self.assertEqual(set(sand), {'color', 'normal', 'metalness_emissive_roughness_subsurface'})
        with Image.open(self.rp / (atlas['bct_ov_made_up_java_0_1']['textures'] + '.png')) as image:
            alpha = image.getchannel('A')
            # Java's cutout shows a pixel whole from alpha 0.1: the faint row stays, the fainter one goes.
            self.assertEqual([alpha.getpixel((3, row)) for row in range(4)], [255, 255, 0, 0])

    def test_state_encoding_shows_exactly_the_tiles_of_each_case(self):
        blocks = {block['description']['identifier']: block for block in self.blocks().values()}
        rules = self.data['rules']
        for entry in self.data['types']:
            visible = blocks[entry['block']]['components']['minecraft:geometry']['bone_visibility']
            names = [name for channel in entry['channels'] for name, _ in channel['states']]
            for number, channel in enumerate(entry['channels']):
                options = [(rule, value) for rule, values in channel['options'] for value in values]
                for option, (rule, value) in enumerate([(None, None)] + options):
                    values = channel_state_values(channel, option)
                    states = {name: values.get(name, 0) for name in names}
                    shown = {bone for bone, expression in visible.items()
                             if bone.startswith(f'c{number}_') and molang(expression, states)}
                    if rule is None:
                        self.assertEqual(shown, set(), entry['block'])
                        continue
                    source = rules[rule]
                    if source['method'] == 'overlay':
                        tiles = surfaces.overlay_tiles(*surfaces.COMBOS[value])
                    else:
                        tiles = [value - 1]
                    expected = {f"c{number}_{source['id']}_{tile_index}" for tile_index in tiles}
                    self.assertEqual(shown, expected, (entry['block'], option))

    def test_quads_lie_just_outside_the_face_they_cover(self):
        depths = [surfaces.QUAD_DEPTH + rank * surfaces.RULE_STEP + step * surfaces.TILE_STEP
                  for rank in range(3) for step in range(5)]
        # Bedrock block geometry mirrors x: a north quad sits near the cell's south edge (+8 in z).
        near_edges = {'north': (2, 8), 'south': (2, -8), 'west': (0, -8), 'east': (0, 8), 'down': (1, 16)}
        for path in (self.rp / 'models/blocks').glob('*.json'):
            for bone in read_json(path)['minecraft:geometry'][0]['bones']:
                cube, = bone['cubes']
                face, = cube['uv']
                low = cube['origin']
                high = [low[axis] + cube['size'][axis] for axis in range(3)]
                if face == 'up':
                    self.assertTrue(any(abs(high[1] - (offset + depth)) < 1e-6
                                        for offset in (0, -1, -8) for depth in depths), bone['name'])
                    self.assertEqual(cube['uv'][face]['uv_size'], [-16, -16])
                    continue
                axis, edge = near_edges[face]
                distance = min(abs(low[axis] - edge), abs(high[axis] - edge))
                self.assertLess(distance, 1, (path.name, bone['name']))
                # Bedrock turns top and bottom UVs (the Blockbench conversion); sides keep Java's.
                self.assertEqual(cube['uv'][face]['uv_size'], [-16, -16] if face == 'down' else [16, 16])

    def test_engine_fixture_is_up_to_date(self):
        self.assertEqual(self.data, read_json(FIXTURE), 'run python tests/test_overlay_surfaces.py --write-fixture')

    def test_authored_transitions_replace_generated_edges(self):
        provider = read_json(ROOT / 'converter/data/terrain-provider.json')
        authored = read_json(self.folder / 'overlay/authored.json')
        known = {'minecraft:grass_block', 'minecraft:sand', 'minecraft:red_sand', 'minecraft:stone',
                 'minecraft:cobblestone', 'minecraft:dirt'}
        taken, kept = surfaces.prune_generated_edges(provider, authored, known)
        pairs = {(item['sources'][0], item['target']) for item in taken}
        self.assertIn(('minecraft:grass_block', 'minecraft:cobblestone'), pairs)
        self.assertIn(('minecraft:sand', 'minecraft:stone'), pairs)
        grass = next(rule for rule in provider['rules'] if rule['id'] == 'grass_over_surfaces')
        self.assertNotIn('minecraft:cobblestone', grass['targets'])
        self.assertIn('minecraft:dirt', grass['targets'],
                      'the pack draws no grass overlay on dirt, so the generated edge stays')
        self.assertTrue(any(item['generated_rule'] == 'red_sand_over_surfaces' for item in kept),
                        'red sand is not a source of the sand rule')

    def test_conditional_rules_keep_generated_edges(self):
        provider = read_json(ROOT / 'converter/data/terrain-provider.json')
        authored = read_json(self.folder / 'overlay/authored.json')
        for entry in authored:
            entry['conditional'] = True
        known = {'minecraft:grass_block', 'minecraft:cobblestone', 'minecraft:sand', 'minecraft:stone'}
        taken, _ = surfaces.prune_generated_edges(provider, authored, known)
        self.assertEqual(taken, [])

    def test_surfaces_still_ship_when_the_terrain_edges_cannot_be_built(self):
        from convert_java_author_pack import prepare_shared_terrain
        error = ValueError('Animated or non-square textures need a square still image: grass.png')
        with patch('terrain_pack.prepare', side_effect=error), \
                patch('terrain_providers.build', return_value=('terrain.mcaddon', {})) as build:
            archive, report = prepare_shared_terrain('source-rp', 'made-up', 'Made Up', samples_path(), root=ROOT,
                                                     overlay=self.folder / 'overlay')
        build.assert_called_once_with(root=ROOT, provider_paths=[], pack_key='made-up', title='Made Up',
                                      samples=samples_path(), overlay=self.folder / 'overlay')
        self.assertEqual(archive, 'terrain.mcaddon')
        self.assertTrue(report['overlay_surfaces_only'])

    def test_terrain_build_carries_the_surfaces_and_their_data(self):
        from addon_package import read_packets
        from terrain_providers import build
        archive, report = build([], 'overlay-test', samples=samples_path(), overlay=self.folder / 'overlay')
        with zipfile.ZipFile(archive) as packed:
            names = set(packed.namelist())
            packets = read_packets(packed.read('Terrain_BP/scripts/source-data.js').decode())
        self.assertIn('Terrain_BP/blocks/bct_made_up_overlay_0.json', names)
        self.assertIn('Terrain_RP/models/blocks/bct_made_up_overlay_0.geo.json', names)
        self.assertEqual(packets['terrain'], [{'overlay': self.data}])
        self.assertEqual(report['overlay_surface_blocks'], len(self.data['types']))


@unittest.skipUnless(HAS_SAMPLES, 'needs BEDROCK_SAMPLES')
class OverlayRepeatTests(unittest.TestCase):
    def test_repeat_overlays_keep_their_pattern_size_and_state_matchers(self):
        roots = 'minecraft:muddy_mangrove_roots'
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary) / 'compiled'
            tiles = [tile(pack, f'assets/minecraft/optifine/ctm/roots/{number}.png', (60, 40, 30))
                     for number in range(1, 17)]
            rule = {'id': 'java_7', 'method': 'overlay_repeat', 'blocks': [roots],
                    'blockMatchers': [{'block': roots, 'states': {'pillar_axis': ['z']}}],
                    'matchTiles': [texture('muddy_mangrove_roots_side')], 'tiles': tiles, 'width': 4, 'height': 4,
                    'faces': ['north', 'south', 'up', 'down'], 'layer': 'cutout'}
            document = {'dialect': 'continuity', 'fullCubeBlocks': [roots],
                        'baseTextures': {roots: {**all_faces(texture('muddy_mangrove_roots_side')),
                                                 'up': texture('muddy_mangrove_roots_top')}},
                        'rules': [rule]}
            data, report = surfaces.build(document, pack, Path(temporary) / 'overlay', key='roots',
                                          samples=samples_path())
        engine_rule, = data['rules']
        self.assertEqual((engine_rule['width'], engine_rule['height'], engine_rule['tiles']), (4, 4, 16))
        self.assertEqual(engine_rule['matchers'], [{'block': roots, 'states': {'pillar_axis': ['z']}}])
        # The top shows the top texture, which the rule does not match; the side faces it names do.
        self.assertEqual(engine_rule['faces'], ['north', 'south', 'down'])
        self.assertEqual(data['hosts'], {roots: ['north', 'south', 'down']})
        channel = data['types'][0]['channels'][0]
        self.assertEqual(channel['options'], [[0, list(range(1, 17))]])
        self.assertGreaterEqual(math.prod(size for _, size in channel['states']), 17)
        self.assertEqual(report['rules_not_drawn'], [])


class OverlayImportTests(unittest.TestCase):
    def test_java_overlay_properties_import_with_their_selection_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary)
            folder = pack / 'assets/minecraft/optifine/ctm/edges'
            folder.mkdir(parents=True)
            for number in range(1, 18):
                tile(pack, f'assets/minecraft/optifine/ctm/edges/{number}.png', (90, 90, 90))
            (folder / 'grass.properties').write_text('method=overlay\nlayer=cutout\ntiles=1-17\n'
                                                     'connectTiles=grass_block_top\ntintIndex=0\n'
                                                     'tintBlock=grass_block\nmatchTiles=cobblestone stone\n')
            (folder / 'sand.properties').write_text('method=overlay\ntiles=1-17\nconnectBlocks=sand\n'
                                                    'connectTiles=sand\nmatchTiles=stone\n')
            (folder / 'fallen.properties').write_text('matchBlocks=sand\ntiles=1-4 <skip>\nmethod=overlay_random\n'
                                                      'layer=cutout\nfaces=top\nweights = 1 1 1 1 96\n'
                                                      'randomLoops=1\n')
            base = {'minecraft:stone': all_faces(texture('stone'))}
            imported = import_rules(pack, base, block_state_resolver=resolve_known_block_selection)
            rules = {Path(rule['filename']).stem: rule for rule in imported['rules']}
        grass = rules['grass']
        self.assertEqual(grass['connectTiles'], [texture('grass_block_top')])
        self.assertEqual((grass['tintIndex'], grass['tintBlock'], grass['layer']),
                         (0, 'minecraft:grass_block', 'cutout'))
        self.assertEqual(grass['matchTiles'], [texture('cobblestone'), texture('stone')])
        self.assertEqual(rules['sand']['connectBlocks'], ['minecraft:sand'])
        self.assertEqual(rules['fallen']['faces'], ['up'])
        self.assertEqual(rules['fallen']['weights'], [1, 1, 1, 1, 96])
        self.assertEqual(rules['fallen']['tiles'][-1], '<skip>')
        self.assertEqual(len(grass['tiles']), 17)


def write_fixture():
    with tempfile.TemporaryDirectory() as temporary:
        data, _ = build_made_up(temporary, {'java_2': ['minecraft:sand']})
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(data, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    return FIXTURE


if __name__ == '__main__':
    if sys.argv[1:] == ['--write-fixture']:
        print(write_fixture())
    else:
        unittest.main()
