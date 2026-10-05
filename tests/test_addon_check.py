import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from bedrock_schema import addon_check
from common import samples_path, write_json
from connected_build import build_connected


def manifest(module):
    header = {'name': module, 'uuid': '00000000-0000-4000-8000-00000000000' + str(len(module) % 10),
              'version': [1, 0, 0], 'min_engine_version': [1, 26, 50]}
    modules = [{'type': module, 'uuid': '00000000-0000-4000-8000-000000000010', 'version': [1, 0, 0]}]
    return {'format_version': 2, 'header': header, 'modules': modules}


class AddonCheckTests(unittest.TestCase):
    def test_a_carrier_build_passes_the_entity_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            pack = Path(folder)
            Image.new('RGBA', (16, 16), (50, 100, 150, 255)).save(pack / 'tile.png')
            rules = pack / 'rules.json'
            rule = {'id': 'test', 'method': 'fixed', 'blocks': ['minecraft:stone'], 'tiles': ['tile']}
            rules.write_text(json.dumps({'format_version': 1, 'rules': [rule]}))
            archive = build_connected(pack, rules, 'schema-check-test')
            result = addon_check(archive, samples_path())
            self.assertEqual(result['problems'], [])
            self.assertGreaterEqual(result['checked'].get('entity', 0), 1)
            self.assertGreaterEqual(result['checked'].get('entity geometry', 0), 1)
            self.assertIn('rp render_controllers', result['without_schema'],
                          'kinds without a published schema are listed')

    def test_problems_are_reported_by_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_json(root / 'BP/manifest.json', manifest('data'))
            write_json(root / 'RP/manifest.json', manifest('resources'))
            write_json(root / 'BP/blocks/bad.json', {'format_version': '1.26.50', 'minecraft:block': {
                'description': {'identifier': 'test:bad'},
                'components': {'tag:stone': {}, 'minecraft:geometry': 'geometry.test.missing'}}})
            write_json(root / 'RP/models/blocks/star.geo.json', {'format_version': '1.21.0', 'minecraft:geometry': [{
                'description': {'identifier': 'geometry.test.star'}, 'bones': [{'name': 'b', 'cubes': [
                    {'origin': [-8, 0, -8], 'size': [16, 16, 16],
                     'uv': {'north': {'uv': [0, 0], 'material_instance': '*'}}}]}]}]})
            (root / 'RP/textures').mkdir(parents=True)
            (root / 'RP/textures/terrain_texture.json').write_text('// a comment Bedrock allows\n{"texture_data": {}}')
            result = addon_check(root, samples_path())
            problems = '\n'.join(result['problems'])
            self.assertIn('BP/blocks/bad.json: components/tag:stone', problems)
            self.assertIn('geometry geometry.test.missing is not in the resource pack', problems)
            self.assertIn('"*" is not a material instance name in geometry', problems)
            self.assertEqual(result['checked'], {'block': 1, 'block geometry': 1})
            self.assertEqual(result['without_schema'], {'manifest': 2, 'rp textures': 1})

    def test_integer_and_boolean_state_arrays_are_accepted_like_the_game_does(self):
        from bedrock_schema import Schemas, block_errors
        schemas = Schemas(samples_path())

        def errors(states):
            body = {'description': {'identifier': 'test:b', 'states': states}, 'components': {}}
            return block_errors({'format_version': '1.26.50', 'minecraft:block': body}, schemas)
        self.assertEqual(errors({'bct:mask': [0, 1, 4], 'bct:flag': [False, True], 'bct:color': ['red', 'blue'],
                                 'bct:range': {'values': {'min': 0, 'max': 3}}}), [])
        self.assertIn('description/states/bct:mask: values repeat', errors({'bct:mask': [0, 0]}))
        self.assertIn('description/states/bct:mask: 17 values (1 to 16 allowed)', errors({'bct:mask': list(range(17))}))
        self.assertTrue(errors({'bct:mixed': [0, 'a']}), 'one type per state')

    def test_game_rules_the_schema_leaves_out_are_reported(self):
        from bedrock_schema import Schemas, block_errors
        schemas = Schemas(samples_path())
        visibility = {'shown': "q.block_state('bct:x') == 1", 'sneaking': 'q.is_sneaking'}
        instances = {'*': {'texture': 'a', 'render_method': 'opaque'}, 'up': {'texture': 'b', 'render_method': 'blend'}}
        document = {'format_version': '1.26.50', 'minecraft:block': {
            'description': {'identifier': 'test:rules', 'states': {'bct:x': [0, 1]}},
            'components': {'minecraft:geometry': {'identifier': 'geometry.test.rules', 'bone_visibility': visibility},
                           'minecraft:transformation': {'rotation': [0, 45, 0]},
                           'minecraft:material_instances': instances},
            'permutations': [{'condition': 'q.is_sneaking', 'components': {}}]}}
        problems = '\n'.join(block_errors(document, schemas))
        self.assertIn('bone_visibility/sneaking: only query.block_state() is allowed', problems)
        self.assertNotIn('bone_visibility/shown', problems)
        self.assertIn('minecraft:transformation: rotation must be in steps of 90 degrees', problems)
        self.assertIn("one render method per block, found ['blend', 'opaque']", problems)
        self.assertIn('permutations[0].condition: only query.block_state() is allowed', problems)
        permutation_only = {'format_version': '1.26.50', 'minecraft:block': {
            'description': {'identifier': 'test:rules', 'states': {'bct:x': [0, 1]}},
            'components': {'minecraft:geometry': 'geometry.test.rules'},
            'permutations': [{'condition': "q.block_state('bct:x') == 1", 'components': {
                'minecraft:geometry': {'identifier': 'geometry.test.rules', 'bone_visibility': {'shown': True}}}}]}}
        self.assertIn('permutations[0]: bone_visibility in a permutation needs bone_visibility in the base geometry',
                      block_errors(permutation_only, schemas))

    def test_check_tree_reports_references_the_packs_do_not_define(self):
        from bedrock_schema import check_tree
        with tempfile.TemporaryDirectory() as folder:
            behavior, resources = Path(folder) / 'BP', Path(folder) / 'RP'
            write_json(resources / 'models/blocks/test.geo.json', {'format_version': '1.21.0', 'minecraft:geometry': [{
                'description': {'identifier': 'geometry.test.block'},
                'bones': [{'name': 'base', 'cubes': [{'origin': [-8, 0, -8], 'size': [16, 16, 16]}]}]}]})
            geometry = {'identifier': 'geometry.test.block', 'culling': 'test:missing_culling',
                        'bone_visibility': {'base': True, 'ghost': True}}
            write_json(behavior / 'blocks/test.json', {'format_version': '1.26.50', 'minecraft:block': {
                'description': {'identifier': 'test:block'},
                'components': {'minecraft:geometry': geometry, 'minecraft:loot': 'loot_tables/missing.json'}}})
            write_json(resources / 'blocks.json', {'format_version': [1, 1, 0], 'test:undefined': {'sound': 'stone'}})
            problems = '\n'.join(check_tree(behavior, resources, samples_path()))
        self.assertIn("bone_visibility names a bone 'ghost' that geometry.test.block does not have", problems)
        self.assertIn('culling test:missing_culling is not in the resource pack', problems)
        self.assertIn('loot table loot_tables/missing.json is missing', problems)
        self.assertIn('blocks.json: test:undefined is not defined by the behavior pack', problems)
        self.assertNotIn("'base'", problems)


if __name__ == '__main__':
    unittest.main()
