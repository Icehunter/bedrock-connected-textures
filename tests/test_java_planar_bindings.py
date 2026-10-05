import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from convert_java_author_pack import EffectiveStack, attach_binding_providers, binding_faces
from java_block_bindings import _Models
from java_block_states import states_java_to_bedrock
from java_missing_sprite import MISSING_MODEL_SPRITE, missing_model_png
from java_planar_bindings import (authored_geometry_selection, ctm_geometry_candidates, model_geometry_variants,
                                  model_parts)

BLOCKSTATES = 'assets/minecraft/blockstates/'
FACES = ('north', 'east', 'south', 'west', 'up', 'down')


class Stack:
    """An author pack stack held in memory."""
    archives = []

    def __init__(self, files):
        self.files = files

    def read(self, path):
        return self.files[path]


class PlanarTests(unittest.TestCase):
    def archive(self, root, models, states):
        path = root / 'models.jar'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, value in models.items():
                archive.writestr('assets/minecraft/models/block/' + name + '.json', json.dumps(value))
            for name, value in states.items():
                archive.writestr(BLOCKSTATES + name + '.json', json.dumps(value))
        return path

    @staticmethod
    def variants_of(archive, block):
        """model_geometry_variants of one blockstate in the test jar."""
        return model_geometry_variants(_Models(archive), BLOCKSTATES + block + '.json', 'minecraft:' + block)

    def test_multipart_weights_are_independent_and_air_keeps_its_weight(self):
        cube = {'textures': {'all': 'block/stone'}, 'elements': [{'from': [0, 0, 0], 'to': [16, 16, 16],
                'faces': {face: {'texture': '#all'} for face in FACES}}]}
        plane = {'textures': {'leaf': 'block/cloverleaf_overlay'}, 'elements': [{
            'from': [0, 16.5, 0], 'to': [16, 16.5, 16], 'shade': False,
            'faces': {'up': {'texture': '#leaf', 'tintindex': 0}}}]}
        document = {'multipart': [
            {'apply': [{'model': 'block/cube', 'weight': 3}, {'model': 'block/cube', 'y': 180}]},
            {'apply': [{'model': 'block/plane', 'weight': 2}, {'model': 'block/plane', 'y': 90},
                       {'model': 'block/air', 'weight': 4}]}]}
        with tempfile.TemporaryDirectory() as folder:
            path = self.archive(Path(folder), {'cube': cube, 'plane': plane, 'air': {'elements': []}},
                                {'grass_block': document})
            with zipfile.ZipFile(path) as archive:
                variants, issues = self.variants_of(archive, 'grass_block')
        self.assertEqual(issues, [])
        entry = variants['minecraft:grass_block'][0]
        self.assertEqual([choice['weight'] for choice in entry['modelChoices']], [3, 1])
        group = entry['modelPartsGroups'][0]
        self.assertEqual([choice['weight'] for choice in group['modelChoices']], [2, 1, 4])
        self.assertEqual(group['modelChoices'][-1]['modelParts'], [])
        self.assertEqual(group['modelChoices'][1]['modelParts'][0]['modelRotation']['y'], 90)
        self.assertFalse(group['modelChoices'][0]['modelParts'][0]['shade'])

    def test_non_cube_euler_pivot_cropped_uv_and_half_are_preserved(self):
        model = {'textures': {'plant': 'block/large_fern_bottom'}, 'elements': [{
            'from': [-3, 0, 8], 'to': [19, 16, 8],
            'rotation': {'origin': [8, 0.5, 8], 'x': -55, 'y': -90, 'z': 0},
            'faces': {'north': {'uv': [0, 7, 16, 16], 'texture': '#plant', 'rotation': 180, 'tintindex': 1},
                      'east': {'uv': [2, 7, 2, 16], 'texture': '#missing'}}}]}
        with tempfile.TemporaryDirectory() as folder:
            path = self.archive(Path(folder), {'fern': model},
                                {'large_fern': {'variants': {'half=lower': {'model': 'block/fern', 'y': 90}}}})
            with zipfile.ZipFile(path) as archive:
                variants, issues = self.variants_of(archive, 'large_fern')
        self.assertEqual(issues, [])
        entry = variants['minecraft:large_fern'][0]
        self.assertEqual(entry['states'], {'upper_block_bit': False})
        part = entry['modelParts'][0]
        self.assertEqual(part['from'], [-3, 0, 8])
        self.assertEqual(part['rotation'], model['elements'][0]['rotation'])
        self.assertEqual(part['faces']['north']['uv'], [0, 7, 16, 16])
        self.assertEqual(part['faces']['north']['worldFace'], 'east')
        self.assertNotIn('east', part['faces'])

    def test_leaf_distance_is_derived_and_never_mapped_to_update_bit(self):
        plane = {'textures': {'all': 'block/oak_leaves'}, 'elements': [{'from': [0, 0, 8], 'to': [16, 16, 8],
                 'faces': {'north': {'texture': '#all'}}}]}
        document = {'multipart': [
            {'when': {'OR': [{'persistent': 'true'}, {'distance': '1|2|3|4'}]}, 'apply': {'model': 'block/dense'}},
            {'when': {'persistent': 'false', 'distance': '5|6|7'}, 'apply': {'model': 'block/sparse', 'y': 90}}]}
        with tempfile.TemporaryDirectory() as folder:
            path = self.archive(Path(folder), {'dense': plane, 'sparse': plane}, {'oak_leaves': document})
            with zipfile.ZipFile(path) as archive:
                variants, issues = self.variants_of(archive, 'oak_leaves')
        self.assertEqual(issues, [])
        entries = variants['minecraft:oak_leaves']
        self.assertEqual(len(entries), 14)
        self.assertFalse(any('update_bit' in entry['states'] for entry in entries))
        sparse = next(entry for entry in entries
                      if entry['states'] == {'persistent_bit': False, 'bct:leaf_distance': 7})
        self.assertEqual(sparse['modelParts'][0]['modelRotation']['y'], 90)

    def test_invalid_element_numbers_are_refused(self):
        def plane(**fields):
            return {'from': [0, 0, 8], 'to': [16, 16, 8], 'faces': {'north': {'texture': '#all'}}, **fields}
        cases = [(plane(rotation={'origin': [8, 8, 8], 'axis': 'w', 'angle': 45}),
                  'Invalid Java element axis rotation'),
                 (plane(rotation={'origin': [8, 8, 8], 'x': 'ten'}), 'Invalid Java element Euler rotation'),
                 (plane(rotation={'origin': [8, 8], 'axis': 'y', 'angle': 45}), 'Invalid Java rotation origin'),
                 (plane(**{'from': [0, 0]}), 'Invalid Java element bounds'),
                 (plane(faces={'north': {'texture': '#all', 'uv': [0, 0, 16]}}), 'Invalid Java face UV'),
                 (plane(faces={'north': {'texture': '#all', 'rotation': 45}}), 'Invalid Java face rotation')]
        with tempfile.TemporaryDirectory() as folder:
            models = {f'case{index}': {'textures': {'all': 'block/fern'}, 'elements': [element]}
                      for index, (element, _) in enumerate(cases)}
            path = self.archive(Path(folder), models, {})
            with zipfile.ZipFile(path) as archive:
                for index, (_, reason) in enumerate(cases):
                    with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                        model_parts(_Models(archive), {'model': f'block/case{index}'})

    def test_unrepresentable_uv_lock_is_reported_without_flattening(self):
        plane = {'textures': {'all': 'block/fern'}, 'elements': [{'from': [0, 0, 8], 'to': [16, 16, 8],
                 'faces': {'north': {'texture': '#all', 'uv': [0, 7, 16, 16]}}}]}
        with tempfile.TemporaryDirectory() as folder:
            path = self.archive(Path(folder), {'plane': plane},
                                {'fern': {'variants': {'': {'model': 'block/plane', 'y': 90, 'uvlock': True}}}})
            with zipfile.ZipFile(path) as archive:
                variants, issues = self.variants_of(archive, 'fern')
        self.assertEqual(variants, {})
        self.assertIn('UV lock', issues[0]['reason'])

    def test_part_sprite_discovery_reaches_independent_groups_and_derived_metadata(self):
        sprite = 'assets/minecraft/textures/block/cloverleaf_overlay.png'
        group = {'modelSelection': 'java-26.2-multipart-block-position',
                 'modelChoices': [{'weight': 1, 'modelParts': [{'faces': {'up': {'texture': sprite}}}]}]}
        bindings = {'baseTextures': {},
                    'baseTextureVariants': {'minecraft:grass_block': [{'states': {}, 'faces': {},
                                                                       'modelPartsGroups': [group]}]},
                    'modelGeometryBlocks': ['minecraft:grass_block'], 'leafDistanceLogs': ['minecraft:oak_log']}
        found = {path for _, faces in binding_faces(bindings) for path in faces.values()}
        self.assertIn(sprite, found)
        rules = attach_binding_providers({'rules': [{'matchTiles': [sprite]}]}, bindings)
        self.assertEqual(rules['sourceBlocks'], ['minecraft:grass_block'])
        self.assertEqual(rules['leafDistanceLogs'], ['minecraft:oak_log'])

    def test_unchanged_parent_cube_geometry_keeps_native_mesh(self):
        cube = {'elements': [{'from': [0, 0, 0], 'to': [16, 16, 16],
                              'faces': {face: {'texture': '#all'} for face in FACES}}]}
        child = {'parent': 'block/cube', 'textures': {'all': 'block/stone'}}
        explicit = json.loads(json.dumps(cube))
        for data in explicit['elements'][0]['faces'].values():
            data.update({'uv': [0, 0, 16, 16], 'tintindex': 0})
        stack = Stack({'assets/minecraft/models/block/cube.json': json.dumps(explicit).encode()})
        with tempfile.TemporaryDirectory() as folder:
            path = self.archive(Path(folder), {'cube': cube, 'child': child},
                                {'stone': {'variants': {'': {'model': 'block/child'}}}})
            with zipfile.ZipFile(path) as archive:
                self.assertFalse(authored_geometry_selection(_Models(archive, stack), BLOCKSTATES + 'stone.json'))

    def test_seagrass_halves_remain_distinct_states_on_the_same_native_block(self):
        plane = {'textures': {'all': 'block/tall_seagrass_top'}, 'elements': [{'from': [0, 0, 8], 'to': [16, 16, 8],
                 'faces': {'north': {'texture': '#all'}}}]}
        document = {'variants': {'half=lower': {'model': 'block/plane'}, 'half=upper': {'model': 'block/plane'}}}
        with tempfile.TemporaryDirectory() as folder:
            path = self.archive(Path(folder), {'plane': plane}, {'tall_seagrass': document})
            with zipfile.ZipFile(path) as archive:
                variants, issues = self.variants_of(archive, 'tall_seagrass')
        self.assertEqual(issues, [])
        self.assertEqual([entry['states'] for entry in variants['minecraft:seagrass']],
                         [{'sea_grass_type': 'double_bot'}, {'sea_grass_type': 'double_top'}])
        self.assertTrue(all(entry['javaBlock'] == 'minecraft:tall_seagrass'
                            for entry in variants['minecraft:seagrass']))

    def test_ctm_candidate_discovery_follows_actual_partial_model_face_slots(self):
        model = {'textures': {'top': 'block/custom_path', 'unused': 'block/unrelated'}, 'elements': [
            {'from': [0, 0, 0], 'to': [16, 15, 16], 'faces': {'up': {'texture': '#top'}}}]}
        stack = Stack({'assets/minecraft/optifine/ctm/path/rule.properties':
                       b'method=random\nmatchTiles=custom_path\ntiles=0 1',
                       'assets/minecraft/optifine/ctm/masonry/block_custom.properties': b'method=fixed\ntiles=0'})
        with tempfile.TemporaryDirectory() as folder:
            path = self.archive(Path(folder), {'path': model},
                                {'dirt_path': {'variants': {'': {'model': 'block/path'}}}})
            with zipfile.ZipFile(path) as archive:
                candidates, issues = ctm_geometry_candidates(_Models(archive, stack))
                variants, geometry_issues = self.variants_of(archive, 'dirt_path')
        self.assertEqual(issues, [])
        self.assertEqual(candidates, {'minecraft:dirt_path', 'minecraft:custom'})
        self.assertEqual(geometry_issues, [])
        self.assertEqual(variants['minecraft:grass_path'][0]['modelParts'][0]['to'], [16, 15, 16])

    def test_partial_reference_preserves_vine_bitmask_and_ceiling_predicate(self):
        for face, bit in [('south', 1), ('west', 2), ('north', 4), ('east', 8)]:
            source = {direction: str(direction == face).lower() for direction in ('south', 'west', 'north', 'east')}
            source['up'] = 'true'
            self.assertEqual(states_java_to_bedrock('minecraft:vine', source), [{
                'block': 'minecraft:vine', 'states': {'bct:vine_up': True, 'vine_direction_bits': bit}}])

    def test_partial_reference_preserves_bookshelf_slot_bits_and_facing(self):
        slots = {'slot_' + str(slot) + '_occupied': str(slot in (0, 5)).lower() for slot in range(6)}
        source = {'facing': 'north', **slots}
        self.assertEqual(states_java_to_bedrock('minecraft:chiseled_bookshelf', source), [{
            'block': 'minecraft:chiseled_bookshelf', 'states': {'books_stored': 33, 'direction': 2}}])

    def test_visible_unresolved_slot_preserves_java_builtin_sprite_and_source_warning(self):
        model = {'textures': {'side': 'block/cactus_side'}, 'elements': [{
            'from': [3, 0, 2], 'to': [15, 16, 14],
            'faces': {'north': {'texture': '#side'},
                      'down': {'uv': [0, 0, 14, 14], 'texture': '#missing', 'cullface': 'down'}}}]}
        with tempfile.TemporaryDirectory() as folder:
            path = self.archive(Path(folder), {'cactus': model},
                                {'cactus': {'variants': {'': {'model': 'block/cactus'}}}})
            with zipfile.ZipFile(path) as archive:
                variants, issues = self.variants_of(archive, 'cactus')
            effective = EffectiveStack(Stack({}), path)
            try:
                self.assertIn(MISSING_MODEL_SPRITE, effective.files)
                self.assertEqual(effective.read(MISSING_MODEL_SPRITE), missing_model_png())
            finally:
                effective.close()
        part = variants['minecraft:cactus'][0]['modelParts'][0]
        self.assertEqual(part['faces']['down']['texture'], MISSING_MODEL_SPRITE)
        self.assertEqual(part['sourceMissingFaces'], [{'face': 'down', 'slot': '#missing'}])
        self.assertTrue(issues[0]['geometry_preserved'])
        with Image.open(io.BytesIO(missing_model_png())) as image:
            self.assertEqual(image.getpixel((0, 0)), (0, 0, 0, 255))
            self.assertEqual(image.getpixel((8, 0)), (248, 0, 248, 255))
            self.assertEqual(image.getpixel((0, 8)), (248, 0, 248, 255))
            self.assertEqual(image.getpixel((8, 8)), (0, 0, 0, 255))


if __name__ == '__main__':
    unittest.main()
