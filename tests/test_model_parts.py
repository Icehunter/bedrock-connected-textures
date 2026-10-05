"""Authored model parts drawn by carriers: client guards, part geometry, shared materials and culling."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from connected_build import build_connected, model_part_geometry
from terrain_entity_surface import guard_carrier_client
from addon_package import read_packets

FACES = ('north', 'east', 'south', 'west', 'up', 'down')
RAY_TRACING_GUARD = "!q.graphics_mode_is_any('raytraced')"


def face(texture, tint=0):
    return {'texture': texture, 'uv': [0, 0, 16, 16], 'rotation': 0, 'tintIndex': tint, 'worldFace': 'up'}


def model_parts_document():
    """Grass with a clover part group, a crossed plant, a culled fern and a path drawn by its Java name."""
    plant = {'from': [0, 0, 8], 'to': [16, 16, 8], 'rotation': {'origin': [8, 8, 8], 'axis': 'y', 'angle': 45},
             'faces': {'north': {**face('grass.png'), 'worldFace': 'north', 'uv': [2, 1, 14, 15]},
                       'south': {**face('grass.png'), 'worldFace': 'south'}},
             'modelRotation': {'x': 0, 'y': 0, 'uvlock': False}}
    clover = {'from': [0, 16.02, 0], 'to': [16, 16.02, 16], 'faces': {'up': face('clover.png')},
              'modelRotation': {'x': 0, 'y': 0, 'uvlock': False}}
    fern = {'from': [0, 0, 8], 'to': [16, 16, 8], 'faces': {
        'north': {**face('fern.png'), 'worldFace': 'north', 'cullface': 'north'},
        'south': {**face('fern.png'), 'worldFace': 'south', 'cullface': 'south'}}}
    path = {'from': [0, 0, 0], 'to': [16, 15, 16], 'faces': {'up': face('path.png', -1)}}
    dirt_faces = dict.fromkeys(FACES, 'dirt.png')
    return {
        'format_version': 1, 'fullCubeBlocks': ['minecraft:grass_block'],
        'sourceBlocks': ['minecraft:grass_block', 'minecraft:short_grass'],
        'baseTextures': {'minecraft:grass_block': dict(dirt_faces)},
        'grassTints': {'minecraft:plains': [.3, .6, .2]},
        'modelTintTypes': {'minecraft:grass_block': 'grass', 'minecraft:short_grass': 'grass',
                           'minecraft:fern': 'grass'},
        'nativeBlockJavaIds': {'minecraft:grass_path': 'minecraft:dirt_path'},
        'baseTextureVariants': {
            'minecraft:grass_block': [{
                'states': {}, 'faces': dict(dirt_faces), 'tintIndices': {},
                'modelPartsGroups': [{'modelSelection': 'java-26.2-multipart-block-position', 'modelChoices': [
                    {'weight': 1, 'modelParts': [clover]}, {'weight': 4, 'modelParts': []}]}]}],
            'minecraft:short_grass': [{'states': {}, 'modelParts': [plant]}],
            'minecraft:fern': [{'states': {}, 'modelParts': [fern]}],
            'minecraft:grass_path': [{'states': {}, 'javaBlock': 'minecraft:dirt_path', 'modelParts': [path]}]},
        'rules': [
            {'id': 'dirt', 'method': 'fixed', 'blocks': [], 'matchTiles': ['dirt.png'], 'tiles': ['dirt.png']},
            {'id': 'clover', 'method': 'random', 'blocks': [], 'matchTiles': ['clover.png'],
             'tiles': ['clover.png', 'clover_alt.png']},
            {'id': 'grass', 'method': 'random', 'blocks': [], 'matchTiles': ['grass.png'],
             'tiles': ['grass_alt.png', '<default>']},
            {'id': 'path', 'method': 'repeat', 'blocks': ['minecraft:dirt_path'], 'faces': ['up'],
             'tiles': ['path.png', 'path_alt.png'], 'width': 2, 'height': 1}],
        'leafDistanceLeaves': ['minecraft:oak_leaves'], 'leafDistanceLogs': ['minecraft:oak_log']}


def read_member(archive, name):
    return json.loads(archive.read(name))


def rule_with_id(config, rule_id):
    return next(rule for rule in config['rules'] if rule['id'] == rule_id)


def carrier_stem(rule):
    return rule['entity'].replace(':', '_', 1)


def last_cube(archive, stem):
    geometry = read_member(archive, 'Connected_RP/models/entity/' + stem + '.geo.json')['minecraft:geometry'][0]
    return geometry['bones'][-1]['cubes'][0]


class ModelPartTests(unittest.TestCase):
    def test_graphics_guard_preserves_existing_controller_conditions_and_resources(self):
        original = {'format_version': '1.10.0', 'minecraft:client_entity': {'description': {
            'identifier': 'example:surface', 'textures': {'t0': 'textures/entity/authored'},
            'geometry': {'default': 'geometry.example'}, 'materials': {'default': 'entity_alphatest'},
            'render_controllers': ['controller.render.base',
                                   {'controller.render.detail': "q.property('example:active')"}]}}}
        guarded = guard_carrier_client(original)
        self.assertEqual(guarded['format_version'], '1.21.80')
        before = original['minecraft:client_entity']['description']
        after = guarded['minecraft:client_entity']['description']
        for key in ('textures', 'geometry', 'materials', 'identifier'):
            self.assertEqual(before[key], after[key])
        self.assertEqual(after['render_controllers'][0], {'controller.render.base': RAY_TRACING_GUARD})
        self.assertEqual(after['render_controllers'][1],
                         {'controller.render.detail': "(q.property('example:active')) && " + RAY_TRACING_GUARD})
        self.assertEqual(guard_carrier_client(guarded), guarded)
        self.assertEqual(original['format_version'], '1.10.0')

    def test_build_keeps_clover_height_crossed_plant_geometry_and_baked_face_tint(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary)
            for name in ('clover', 'clover_alt', 'grass', 'grass_alt', 'dirt', 'fern', 'path', 'path_alt'):
                Image.new('RGBA', (16, 16), (120, 100, 80, 255)).save(pack / (name + '.png'))
            document = model_parts_document()
            rule_file = pack / 'rules.json'
            rule_file.write_text(json.dumps(document))
            archive = build_connected(pack, rule_file, 'model-parts-suite', artifact_directory=pack / 'out')
            with zipfile.ZipFile(archive) as result:
                clients = [read_member(result, name) for name in result.namelist()
                           if name.startswith('Connected_RP/entity/') and name.endswith('.json')]
                self.assertGreater(len(clients), 10)
                for client in clients:
                    self.assertEqual(client['format_version'], '1.21.80')
                    for reference in client['minecraft:client_entity']['description']['render_controllers']:
                        self.assertEqual(list(reference.values()), [RAY_TRACING_GUARD])
                config = read_packets(result.read('Connected_BP/scripts/source-data.js').decode())['connected']
                self.assertEqual(config['modelPartBlocks'],
                                 ['minecraft:fern', 'minecraft:grass_block', 'minecraft:grass_path',
                                  'minecraft:short_grass'])
                self.assertNotIn('minecraft:short_grass', config['fullCubeBlocks'])
                self.assertEqual(config['leafDistanceLeaves'], document['leafDistanceLeaves'])

                grass_block = config['baseTextureVariants']['minecraft:grass_block'][0]
                clover_face = grass_block['modelPartsGroups'][0]['modelChoices'][0]['modelParts'][0]['faces']['up']
                clover_rule = rule_with_id(config, clover_face['renderers']['clover'])
                self.assertEqual(len(clover_rule['tintPalette']), 2)
                cube = last_cube(result, carrier_stem(clover_rule))
                self.assertAlmostEqual(cube['origin'][1] + .002 * 16, 0.02)
                self.assertEqual(cube['size'], [16, 0, 16])

                plant = config['baseTextureVariants']['minecraft:short_grass'][0]['modelParts'][0]
                plant_rule = rule_with_id(config, plant['faces']['north']['renderers']['grass'])
                stem = carrier_stem(plant_rule)
                cube = last_cube(result, stem)
                self.assertEqual(cube['rotation'], [0, -45, 0])
                self.assertEqual(cube['size'], [16, 16, 0])
                self.assertEqual(cube['uv']['north'], {'uv': [2, 1], 'uv_size': [12, 14]})
                client = read_member(result, 'Connected_RP/entity/' + stem + '.entity.json')
                self.assertIn('p1_t0', client['minecraft:client_entity']['description']['textures'])

                overrides = read_member(result, 'Connected_RP/blocks.json')
                self.assertEqual(overrides['short_grass']['textures'], 'bct_owned_transparent')
                self.assertNotIn('grass_block', overrides)

                fern = config['baseTextureVariants']['minecraft:fern'][0]
                self.assertEqual(len(fern['modelRenderGroups']), 1, 'same-material faces share one carrier')
                stem = carrier_stem(rule_with_id(config, fern['modelRenderGroups'][0]['rule']))
                definition = read_member(result, 'Connected_BP/entities/' + stem + '.json')
                properties = definition['minecraft:entity']['description']['properties']
                self.assertEqual(properties['bct:cull_mask']['range'], [0, 63])
                controllers = read_member(result, 'Connected_RP/render_controllers/' + stem + '.json')
                controller = next(iter(controllers['render_controllers'].values()))
                self.assertEqual(len(controller['part_visibility']), 2)
                self.assertIn('cull_mask', str(controller['part_visibility']))

                path_face = config['baseTextureVariants']['minecraft:grass_path'][0]['modelParts'][0]['faces']['up']
                path_rule = rule_with_id(config, path_face['renderers']['path'])
                self.assertEqual(path_rule['blocks'], ['minecraft:dirt_path'], 'authored Java selector remains intact')
                self.assertEqual(path_rule['tiles'], ['path.png', 'path_alt.png'])

    def test_euler_pivot_and_axis_rescale_are_retained(self):
        part = {'from': [4, 0, 8], 'to': [12, 16, 8], 'rotation': {'origin': [8, 8, 8], 'x': 15, 'y': 25, 'z': 35},
                'faces': {'north': {**face('leaf', -1), 'worldFace': 'north'}}}
        geometry = model_part_geometry(part, 'north', 'geometry.test')['minecraft:geometry'][0]
        cube = geometry['bones'][-1]['cubes'][0]
        self.assertEqual(cube['rotation'], [-15, -25, 35])
        self.assertEqual(cube['pivot'], [0, 0, 8.032])
        part['rotation'] = {'origin': [8, 8, 8], 'axis': 'y', 'angle': 45, 'rescale': True}
        cube = model_part_geometry(part, 'north', 'geometry.test')['minecraft:geometry'][0]['bones'][-1]['cubes'][0]
        self.assertAlmostEqual(cube['size'][0], 8 * 2 ** .5)


if __name__ == '__main__':
    unittest.main()
