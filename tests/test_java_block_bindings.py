import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from java_block_bindings import JAVA_BLOCK, resolve_bindings, _Models, _rotated_cube_faces, _cube_texture_variants

FACES = ('north', 'east', 'south', 'west', 'up', 'down')
BLOCKSTATES = 'assets/minecraft/blockstates/'
FULL_CUBE = {'from': [0, 0, 0], 'to': [16, 16, 16]}


def png(color):
    stream = io.BytesIO()
    Image.new('RGBA', (4, 4), color).save(stream, format='PNG')
    return stream.getvalue()


class Stack:
    def __init__(self, files):
        self.files = files

    def read(self, path):
        return self.files[path]


class BindingTests(unittest.TestCase):
    def fixture(self, root, blocks, terrain, java, bedrock, models=None):
        samples = root / 'samples'
        (samples / 'textures/blocks').mkdir(parents=True)
        (samples / 'blocks.json').write_text(json.dumps(blocks))
        (samples / 'textures/terrain_texture.json').write_text('// reference\n' + json.dumps({'texture_data': terrain}))
        for name, data in bedrock.items():
            (samples / ('textures/blocks/' + name + '.png')).write_bytes(data)
        jar = root / 'vanilla.jar'
        with zipfile.ZipFile(jar, 'w') as archive:
            for name, data in java.items():
                archive.writestr(JAVA_BLOCK + name + '.png', data)
            for name, data in (models or {}).items():
                archive.writestr('assets/minecraft/models/block/' + name + '.json', json.dumps(data))
        return jar, samples

    def test_log_faces_and_raw_stone_are_not_replaced_by_end_or_polished_texture(self):
        with tempfile.TemporaryDirectory() as folder:
            materials = {'log': 'log_oak', 'end': 'log_oak_top', 'raw': 'stone_andesite',
                         'polished': 'stone_andesite_smooth'}
            java_names = ('oak_log', 'oak_log_top', 'andesite', 'polished_andesite')
            jar, samples = self.fixture(
                Path(folder),
                {'oak_log': {'textures': {'side': 'log', 'up': 'end', 'down': 'end'}},
                 'andesite': {'textures': 'raw'}, 'polished_andesite': {'textures': 'polished'}},
                {alias: {'textures': 'textures/blocks/' + material} for alias, material in materials.items()},
                {name: png((index, 0, 0, 255)) for index, name in enumerate(java_names)},
                {name: png((8, 0, 0, 255)) for name in materials.values()})
            report = resolve_bindings(Stack({}), jar, samples)
            log = report['baseTextures']['minecraft:oak_log']
            self.assertEqual(log['north'], JAVA_BLOCK + 'oak_log.png')
            self.assertEqual(log['up'], JAVA_BLOCK + 'oak_log_top.png')
            self.assertEqual(report['baseTextures']['minecraft:andesite']['up'], JAVA_BLOCK + 'andesite.png')
            self.assertEqual(report['baseTextures']['minecraft:polished_andesite']['up'],
                             JAVA_BLOCK + 'polished_andesite.png')

    def test_exact_pixels_resolve_renames_but_state_arrays_remain_ambiguous(self):
        with tempfile.TemporaryDirectory() as folder:
            jar, samples = self.fixture(
                Path(folder), {'stone': {'textures': 'states'}},
                {'states': {'textures': ['textures/blocks/old_a', {'path': 'textures/blocks/old_b'}]}},
                {'first': png((1, 2, 3, 255)), 'second': png((3, 2, 1, 255))},
                {'old_a': png((1, 2, 3, 255)), 'old_b': png((3, 2, 1, 255))})
            report = resolve_bindings(Stack({JAVA_BLOCK + 'first.png': png((99, 0, 0, 255))}), jar, samples)
            self.assertEqual(report['materialBindings']['textures/blocks/old_a'], JAVA_BLOCK + 'first.png')
            self.assertNotIn('minecraft:stone', report['baseTextures'])
            self.assertEqual(len(report['state_ambiguities']), 6)
            self.assertEqual(report['material_evidence']['textures/blocks/old_a']['origin'], 'pack_stack')

    def test_bedrock_item_materials_bind_the_java_item_sprite(self):
        with tempfile.TemporaryDirectory() as folder:
            jar, samples = self.fixture(Path(folder), {'lantern': {'textures': 'lantern'}},
                                        {'lantern': {'textures': 'textures/items/lantern'}},
                                        {'lantern': png((1, 0, 0, 255))}, {})
            item = 'assets/minecraft/textures/item/lantern.png'
            report = resolve_bindings(Stack({item: png((5, 0, 0, 255))}), jar, samples)
            self.assertEqual(report['materialBindings']['textures/items/lantern'], item,
                             'a Bedrock item texture is the Java item sprite, not the block sprite of the same name')
            self.assertEqual(report['material_evidence']['textures/items/lantern']['matching'], 'same_item_name')
            self.assertEqual(report['baseTextures']['minecraft:lantern']['north'], item)

    def test_legacy_blocks_json_keys_take_the_current_block_id(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            jar, samples = self.fixture(
                root, {'grass': {'textures': 'top'}, 'stone': {'textures': 'stone'},
                       'retired_block': {'textures': 'stone'}},
                {'top': {'textures': 'textures/blocks/grass_top'}, 'stone': {'textures': 'textures/blocks/stone'}},
                {'grass_block_top': png((1, 0, 0, 255)), 'stone': png((2, 0, 0, 255))},
                {'grass_top': png((1, 0, 0, 255)), 'stone': png((2, 0, 0, 255))})
            registry = root / 'metadata/vanilladata_modules/mojang-blocks.json'
            registry.parent.mkdir(parents=True)
            registry.write_text(json.dumps({'data_items': [{'name': 'minecraft:grass_block'},
                                                           {'name': 'minecraft:stone'}]}))
            report = resolve_bindings(Stack({}), jar, samples)
            self.assertEqual(report['baseTextures']['minecraft:grass_block']['up'], JAVA_BLOCK + 'grass_block_top.png')
            self.assertNotIn('minecraft:grass', report['baseTextures'])
            self.assertEqual(report['unmapped_block_keys'], ['retired_block'], 'a key without a block is reported')

    def test_parent_texture_overrides_are_reported_without_corrupting_shared_material(self):
        with tempfile.TemporaryDirectory() as folder:
            models = {'parent': {'textures': {'all': 'minecraft:block/stone'}},
                      'child': {'parent': 'minecraft:block/parent', 'textures': {'up': '#all'}}}
            jar, samples = self.fixture(Path(folder), {'stone': {'textures': 'stone'}},
                                        {'stone': {'textures': 'textures/blocks/stone'}},
                                        {'stone': png((1, 0, 0, 255)), 'other': png((2, 0, 0, 255))},
                                        {'stone': png((1, 0, 0, 255))}, models)
            parent = json.dumps({'textures': {'all': 'minecraft:block/other'}}).encode()
            report = resolve_bindings(Stack({'assets/minecraft/models/block/parent.json': parent}), jar, samples)
            self.assertEqual(report['materialBindings']['textures/blocks/stone'], JAVA_BLOCK + 'stone.png')
            issue = report['model_texture_ambiguities'][0]
            self.assertEqual(issue['java_candidates'], [JAVA_BLOCK + 'other.png'])
            self.assertTrue(any(item['model'].endswith('/child.json') for item in issue['model_slots']))
            self.assertFalse(report['source_artwork_modified'])

    def test_identical_texture_collisions_do_not_choose_arbitrary_sprite(self):
        with tempfile.TemporaryDirectory() as folder:
            data = png((0, 0, 0, 255))
            jar, samples = self.fixture(Path(folder), {'test': {'textures': 'ambiguous'}},
                                        {'ambiguous': {'textures': 'textures/blocks/unrelated'}},
                                        {'one': data, 'two': data}, {'unrelated': data})
            report = resolve_bindings(Stack({}), jar, samples)
            self.assertEqual(report['materialBindings'], {})
            self.assertEqual(report['unresolved_materials'][0]['reason'], 'ambiguous_vanilla_pixels')

    def test_cube_and_uv_evidence_excludes_slabs_and_rotated_models(self):
        with tempfile.TemporaryDirectory() as folder:
            faces = {face: {'texture': '#all', 'uv': [0, 0, 16, 16]} for face in FACES}
            cube = {'textures': {'all': 'minecraft:block/stone'}, 'elements': [{**FULL_CUBE, 'faces': faces}]}
            slab = {**cube, 'elements': [{'from': [0, 0, 0], 'to': [16, 8, 16], 'faces': faces}]}
            blocks = {name: {'textures': 'stone'} for name in ('stone', 'slab', 'rotated')}
            jar, samples = self.fixture(Path(folder), blocks, {'stone': {'textures': 'textures/blocks/stone'}},
                                        {'stone': png((1, 0, 0, 255))}, {'stone': png((1, 0, 0, 255))},
                                        {'cube': cube, 'slab': slab})
            with zipfile.ZipFile(jar, 'a') as archive:
                for block, model, rotation in [('stone', 'cube', 0), ('slab', 'slab', 0), ('rotated', 'cube', 90)]:
                    archive.writestr(f'{BLOCKSTATES}{block}.json', json.dumps(
                        {'variants': {'': {'model': 'minecraft:block/' + model, 'y': rotation}}}))
            report = resolve_bindings(Stack({JAVA_BLOCK + 'stone.png': png((2, 0, 0, 255))}), jar, samples)
            self.assertEqual(report['fullCubeBlocks'], ['minecraft:rotated', 'minecraft:stone'])
            self.assertEqual(report['opaqueBlocks'], report['fullCubeBlocks'])
            self.assertNotIn('minecraft:slab', {entry['block'] for entry in report['galleryStations']})
            self.assertEqual(report['textureOrientations']['minecraft:stone']['north'], 0)
            self.assertNotIn('minecraft:rotated', report['textureOrientations'])

    def test_author_cube_face_override_does_not_replace_another_blocks_material(self):
        with tempfile.TemporaryDirectory() as folder:
            faces = {face: {'texture': '#all'} for face in FACES}
            cube = {'elements': [{**FULL_CUBE, 'faces': faces}]}
            models = {'cube': cube,
                      'light_blue_concrete': {'parent': 'block/cube', 'textures': {'all': 'block/light_blue_concrete'}},
                      'blue_concrete': {'parent': 'block/cube', 'textures': {'all': 'block/blue_concrete'}},
                      'bookshelf': {'parent': 'block/cube', 'textures': {'all': 'block/bookshelf'}}}
            names = ('light_blue_concrete', 'blue_concrete', 'bookshelf')
            jar, samples = self.fixture(Path(folder), {name: {'textures': name} for name in names},
                                        {name: {'textures': 'textures/blocks/' + name} for name in names},
                                        {name: png((index, 0, 0, 255)) for index, name in enumerate(names)},
                                        {name: png((index, 0, 0, 255)) for index, name in enumerate(names)}, models)
            with zipfile.ZipFile(jar, 'a') as archive:
                for block in names:
                    archive.writestr(f'{BLOCKSTATES}{block}.json', json.dumps(
                        {'variants': {'': {'model': 'minecraft:block/' + block}}}))
            stack = Stack({
                'assets/minecraft/models/block/light_blue_concrete.json': json.dumps(
                    {'parent': 'block/cube', 'textures': {'all': 'block/blue_concrete'}}).encode(),
                'assets/minecraft/models/block/bookshelf.json': json.dumps(
                    {'elements': [{'from': [0, 0, 0], 'to': [16, 15, 16], 'faces': faces}],
                     'textures': {'all': 'block/blue_concrete'}}).encode()})
            report = resolve_bindings(stack, jar, samples)
            self.assertEqual(report['blockFaceOverrides']['minecraft:light_blue_concrete']['up'],
                             JAVA_BLOCK + 'blue_concrete.png')
            self.assertEqual(report['baseTextures']['minecraft:light_blue_concrete']['up'],
                             JAVA_BLOCK + 'blue_concrete.png')
            self.assertEqual(report['materialBindings']['textures/blocks/light_blue_concrete'],
                             JAVA_BLOCK + 'light_blue_concrete.png')
            self.assertNotIn('minecraft:bookshelf', report['blockFaceOverrides'])
            self.assertTrue(any(issue['block'] == 'minecraft:bookshelf'
                                and issue['reason'] == 'custom_shape_model_out_of_scope'
                                for issue in report['unresolved_block_face_overrides']))

    def test_log_axis_variants_place_endgrain_on_world_axis_and_preserve_java_uv_rotation(self):
        with tempfile.TemporaryDirectory() as folder:
            faces = {face: {'texture': '#end' if face in ('up', 'down') else '#side'} for face in FACES}
            horizontal = {face: dict(value) for face, value in faces.items()}
            horizontal['up']['rotation'] = 180
            models = {
                'oak_log': {'textures': {'side': 'block/oak_log', 'end': 'block/oak_log_top'},
                            'elements': [{**FULL_CUBE, 'faces': faces}]},
                'oak_log_horizontal': {'parent': 'block/oak_log', 'elements': [{**FULL_CUBE, 'faces': horizontal}]}}
            jar, samples = self.fixture(
                Path(folder), {'oak_log': {'textures': {'side': 'side', 'up': 'end', 'down': 'end'}}},
                {'side': {'textures': 'textures/blocks/log_oak'}, 'end': {'textures': 'textures/blocks/log_oak_top'}},
                {'oak_log': png((1, 0, 0, 255)), 'oak_log_top': png((2, 0, 0, 255))},
                {'log_oak': png((1, 0, 0, 255)), 'log_oak_top': png((2, 0, 0, 255))}, models)
            with zipfile.ZipFile(jar, 'a') as archive:
                archive.writestr(BLOCKSTATES + 'oak_log.json', json.dumps({'variants': {
                    'axis=x': {'model': 'block/oak_log_horizontal', 'x': 90, 'y': 90},
                    'axis=y': {'model': 'block/oak_log'}, 'axis=z': {'model': 'block/oak_log_horizontal', 'x': 90}}}))
            report = resolve_bindings(Stack({JAVA_BLOCK + 'oak_log.png': png((3, 0, 0, 255))}), jar, samples)
            variants = {value['states']['pillar_axis']: value
                        for value in report['baseTextureVariants']['minecraft:oak_log']}
            for axis, end_faces in [('x', {'east', 'west'}), ('y', {'up', 'down'}), ('z', {'north', 'south'})]:
                ends = {face for face, path in variants[axis]['faces'].items() if path.endswith('oak_log_top.png')}
                self.assertEqual(ends, end_faces)
            order = ('down', 'up', 'north', 'south', 'west', 'east')
            expected = {'x': [3, 3, 1, 3, 0, 0], 'y': [0, 0, 0, 0, 0, 0], 'z': [2, 0, 0, 0, 1, 3]}
            for axis in expected:
                self.assertEqual([variants[axis]['orientations'][face] for face in order], expected[axis])
            self.assertEqual([station['states']['pillar_axis'] for station in report['galleryStations']],
                             ['y', 'x', 'z'])

    def test_java_uv_rotation_and_reflection_are_not_assumed_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            faces = {face: {'texture': '#all'} for face in FACES}
            faces['north']['rotation'] = 90
            faces['south']['uv'] = [16, 0, 0, 16]
            models = {'cube': {'textures': {'all': 'block/stone'}, 'elements': [{**FULL_CUBE, 'faces': faces}]}}
            jar, _ = self.fixture(Path(folder), {}, {}, {'stone': png((1, 0, 0, 255))}, {}, models)
            with zipfile.ZipFile(jar) as archive:
                result = _rotated_cube_faces(_Models(archive), {'model': 'block/cube'})
            self.assertEqual(result['orientations']['north'], 3)
            self.assertEqual(result['orientations']['south'], 4)

    def test_java_sprite_object_keeps_translucent_glass_face_bindings(self):
        with tempfile.TemporaryDirectory() as folder:
            faces = {face: {'texture': '#all'} for face in FACES}
            cube = {'textures': {'all': {'sprite': 'minecraft:block/glass', 'force_translucent': True}},
                    'elements': [{**FULL_CUBE, 'faces': faces}]}
            jar, _ = self.fixture(Path(folder), {}, {}, {}, {}, {'glass': cube})
            with zipfile.ZipFile(jar) as archive:
                result = _rotated_cube_faces(_Models(archive), {'model': 'block/glass'})
            self.assertEqual(set(result['faces'].values()), {JAVA_BLOCK + 'glass.png'})
            self.assertEqual(len(result['faces']), 6)

    def test_uv_lock_cancels_geometry_rotation_for_plain_cube_faces(self):
        with tempfile.TemporaryDirectory() as folder:
            faces = {face: {'texture': '#all'} for face in FACES}
            model = {'textures': {'all': 'block/stone'}, 'elements': [{**FULL_CUBE, 'faces': faces}]}
            jar, _ = self.fixture(Path(folder), {}, {}, {'stone': png((1, 0, 0, 255))}, {}, {'cube': model})
            with zipfile.ZipFile(jar) as archive:
                selection = {'model': 'block/cube', 'x': 90, 'y': 90, 'uvlock': True}
                result = _rotated_cube_faces(_Models(archive), selection)
            self.assertEqual(set(result['orientations'].values()), {0})

    def test_furnace_variants_follow_native_state_directions_and_lit_block_ids(self):
        with tempfile.TemporaryDirectory() as folder:
            faces = {face: {'texture': '#front' if face == 'north' else '#side'} for face in FACES}
            cube = {'textures': {'front': 'block/furnace_front', 'side': 'block/furnace_side'},
                    'elements': [{**FULL_CUBE, 'faces': faces}]}
            jar, _ = self.fixture(Path(folder), {}, {}, {}, {}, {'furnace': cube})
            with zipfile.ZipFile(jar, 'a') as archive:
                archive.writestr(BLOCKSTATES + 'furnace.json', json.dumps({'variants': {
                    'facing=east,lit=false': {'model': 'block/furnace', 'y': 90},
                    'facing=east,lit=true': {'model': 'block/furnace', 'y': 90}}}))
            with zipfile.ZipFile(jar) as archive:
                variants, issues = _cube_texture_variants(_Models(archive), BLOCKSTATES + 'furnace.json',
                                                          'minecraft:furnace')
            self.assertEqual(issues, [])
            self.assertEqual(set(variants), {'minecraft:furnace', 'minecraft:lit_furnace'})
            for entries in variants.values():
                self.assertEqual(entries[0]['states'], {'minecraft:cardinal_direction': 'east'})
                self.assertEqual(entries[0]['faces']['east'], JAVA_BLOCK + 'furnace_front.png')
                self.assertEqual(entries[0]['faces']['north'], JAVA_BLOCK + 'furnace_side.png')

    def test_weighted_cube_choices_keep_order_weights_and_uv_reflections(self):
        with tempfile.TemporaryDirectory() as folder:
            faces = {face: {'texture': '#all'} for face in FACES}
            cube = {'textures': {'all': 'block/stone'}, 'elements': [{**FULL_CUBE, 'faces': faces}]}
            mirrored = {'parent': 'block/cube', 'elements': [{
                **FULL_CUBE, 'faces': {face: {**value, 'uv': [16, 0, 0, 16]} for face, value in faces.items()}}]}
            jar, _ = self.fixture(Path(folder), {}, {}, {}, {}, {'cube': cube, 'mirrored': mirrored})
            with zipfile.ZipFile(jar, 'a') as archive:
                archive.writestr(BLOCKSTATES + 'stone.json', json.dumps({'variants': {'': [
                    {'model': 'block/cube', 'weight': 2}, {'model': 'block/mirrored', 'weight': 3},
                    {'model': 'block/cube', 'y': 180, 'weight': 1}]}}))
            with zipfile.ZipFile(jar) as archive:
                variants, issues = _cube_texture_variants(_Models(archive), BLOCKSTATES + 'stone.json',
                                                          'minecraft:stone')
            self.assertEqual(issues, [])
            entry = variants['minecraft:stone'][0]
            self.assertEqual(entry['modelSelection'], 'java-26.2-block-position')
            self.assertEqual([choice['weight'] for choice in entry['modelChoices']], [2, 3, 1])
            self.assertEqual(entry['modelChoices'][1]['orientations']['north'], 4)
            self.assertEqual(entry['modelChoices'][2]['orientations']['up'], 2)

    def test_exclusive_multipart_cube_states_use_multipart_random_seed(self):
        with tempfile.TemporaryDirectory() as folder:
            faces = {face: {'texture': '#all'} for face in FACES}
            cube = {'textures': {'all': 'block/ochre_froglight_side'}, 'elements': [{**FULL_CUBE, 'faces': faces}]}
            jar, _ = self.fixture(Path(folder), {}, {}, {}, {}, {'cube': cube})
            document = {'multipart': [
                {'when': {'axis': 'x'},
                 'apply': [{'model': 'block/cube', 'x': 90, 'y': 90}, {'model': 'block/cube', 'x': 270, 'y': 270}]},
                {'when': {'OR': [{'axis': 'y'}, {'axis': 'z'}]},
                 'apply': [{'model': 'block/cube'}, {'model': 'block/cube', 'y': 90}]}]}
            with zipfile.ZipFile(jar, 'a') as archive:
                archive.writestr(BLOCKSTATES + 'ochre_froglight.json', json.dumps(document))
            with zipfile.ZipFile(jar) as archive:
                variants, issues = _cube_texture_variants(_Models(archive), BLOCKSTATES + 'ochre_froglight.json',
                                                          'minecraft:ochre_froglight')
            self.assertEqual(issues, [])
            entries = variants['minecraft:ochre_froglight']
            self.assertEqual({entry['states']['pillar_axis'] for entry in entries}, {'x', 'y', 'z'})
            self.assertTrue(all(entry['modelSelection'] == 'java-26.2-multipart-block-position'
                                for entry in entries))
            self.assertTrue(all(len(entry['modelChoices']) == 2 for entry in entries))

    def test_multipart_with_overlapping_parts_or_missing_states_is_not_flattened(self):
        with tempfile.TemporaryDirectory() as folder:
            faces = {face: {'texture': '#all'} for face in FACES}
            cube = {'textures': {'all': 'block/stone'}, 'elements': [{**FULL_CUBE, 'faces': faces}]}
            jar, _ = self.fixture(Path(folder), {}, {}, {}, {}, {'cube': cube})
            blockstate = BLOCKSTATES + 'ochre_froglight.json'
            for parts in ([{'apply': {'model': 'block/cube'}}, {'apply': {'model': 'block/cube'}}],
                          [{'when': {'axis': 'x'}, 'apply': {'model': 'block/cube'}}]):
                stack = Stack({blockstate: json.dumps({'multipart': parts}).encode()})
                with zipfile.ZipFile(jar) as archive:
                    with self.assertRaisesRegex(ValueError, 'exactly one cube model'):
                        _cube_texture_variants(_Models(archive, stack), blockstate, 'minecraft:ochre_froglight')

    def test_grass_alias_and_cube_surface_survive_optional_multipart_decoration(self):
        with tempfile.TemporaryDirectory() as folder:
            faces = {face: {'texture': '#top' if face == 'up' else '#side'} for face in FACES}
            side_layer = {face: {'texture': '#side', 'tintindex': 0} for face in FACES[:4]}
            cube = {'textures': {'top': 'block/grass_block_top', 'side': 'block/grass_block_side'},
                    'elements': [{**FULL_CUBE, 'faces': faces}, {**FULL_CUBE, 'faces': side_layer}]}
            flower = {'textures': {'all': 'block/flower'}, 'elements': [{'from': [0, 17, 0], 'to': [16, 17, 16],
                      'faces': {'up': {'texture': '#all'}}}]}
            jar, samples = self.fixture(
                Path(folder), {'grass': {'textures': {'up': 'top', 'down': 'side', 'side': 'side'}}},
                {'top': {'textures': 'textures/blocks/grass_top'}, 'side': {'textures': 'textures/blocks/grass_side'}},
                {'grass_block_top': png((1, 0, 0, 255)), 'grass_block_side': png((2, 0, 0, 255))},
                {'grass_top': png((1, 0, 0, 255)), 'grass_side': png((2, 0, 0, 255))},
                {'grass_block': cube, 'flower': flower})
            with zipfile.ZipFile(jar, 'a') as archive:
                archive.writestr(BLOCKSTATES + 'grass_block.json', json.dumps({'variants': {
                    'snowy=false': {'model': 'block/grass_block'}, 'snowy=true': {'model': 'block/grass_block'}}}))
            document = {'multipart': [
                {'apply': {'model': 'block/grass_block'}},
                {'when': {'snowy': 'false'},
                 'apply': [{'model': 'block/flower', 'weight': 2}, {'model': 'block/flower', 'y': 90}]}]}
            stack = Stack({BLOCKSTATES + 'grass_block.json': json.dumps(document).encode(),
                           JAVA_BLOCK + 'grass_block_top.png': png((9, 0, 0, 255))})
            result = resolve_bindings(stack, jar, samples)
            variants = result['baseTextureVariants']['minecraft:grass_block']
            self.assertIn('minecraft:grass', result['fullCubeBlocks'])
            self.assertIn('minecraft:grass_block', result['fullCubeBlocks'])
            self.assertIn('minecraft:grass_block', result['baseTextureVariants'])
            self.assertNotIn('minecraft:grass_block', result['unresolvedTextureOrientations'])
            self.assertEqual(variants[0]['faces']['up'], JAVA_BLOCK + 'grass_block_top.png')
            self.assertEqual(result['excluded_model_geometry'], [])
            group = next(entry for entry in variants if entry['states']['bct:snowy'] is False)['modelPartsGroups'][0]
            self.assertEqual([choice['weight'] for choice in group['modelChoices']], [2, 1])
            self.assertEqual([choice['modelParts'][0]['modelRotation']['y'] for choice in group['modelChoices']],
                             [0, 90])
            self.assertEqual(group['modelChoices'][0]['modelParts'][0]['from'], [0, 17, 0])
            self.assertEqual(variants[0]['faceLayers']['north'][0],
                             {'texture': JAVA_BLOCK + 'grass_block_side.png', 'orientation': 0, 'tintIndex': 0})

    def test_extra_element_before_cube_does_not_change_face_orientation(self):
        with tempfile.TemporaryDirectory() as folder:
            faces = {face: {'texture': '#all'} for face in FACES}
            faces['north']['rotation'] = 90
            model = {'textures': {'all': 'block/stone'}, 'elements': [
                {'from': [0, 17, 0], 'to': [16, 17, 16], 'faces': {'up': {'texture': '#all'}}},
                {**FULL_CUBE, 'faces': faces}]}
            jar, _ = self.fixture(Path(folder), {}, {}, {}, {}, {'cube': model})
            with zipfile.ZipFile(jar) as archive:
                result = _rotated_cube_faces(_Models(archive), {'model': 'block/cube'})
            self.assertEqual(result['orientations']['north'], 3)
            self.assertEqual(len(result['faces']), 6)

    def test_cube_subset_does_not_export_lower_layer_as_complete_native_material(self):
        with tempfile.TemporaryDirectory() as folder:
            faces = {face: {'texture': '#all'} for face in FACES}
            cube = {'textures': {'all': 'block/stone'}, 'elements': [{**FULL_CUBE, 'faces': faces}]}
            jar, samples = self.fixture(Path(folder), {'stone': {'textures': 'stone'}},
                                        {'stone': {'textures': 'textures/blocks/stone'}},
                                        {'stone': png((1, 0, 0, 255)), 'dirt': png((2, 0, 0, 255))},
                                        {'stone': png((1, 0, 0, 255))}, {'stone': cube})
            with zipfile.ZipFile(jar, 'a') as archive:
                archive.writestr(BLOCKSTATES + 'stone.json', json.dumps({'variants': {'': {'model': 'block/stone'}}}))
            layered = {**cube, 'textures': {'all': 'block/dirt'}, 'elements': cube['elements'] + [
                {'from': [0, 16.1, 0], 'to': [16, 16.1, 16], 'faces': {'up': {'texture': 'block/stone'}}}]}
            stack = Stack({'assets/minecraft/models/block/stone.json': json.dumps(layered).encode()})
            report = resolve_bindings(stack, jar, samples)
            self.assertNotIn('minecraft:stone', report['blockFaceOverrides'])
            self.assertEqual(report['materialBindings']['textures/blocks/stone'], JAVA_BLOCK + 'stone.png')
            self.assertTrue(any(issue['reason'] == 'layered_model_native_face_requires_composition'
                                for issue in report['unresolved_block_face_overrides']))

    def test_cube_surface_layers_rotate_face_uv_and_tint_together(self):
        with tempfile.TemporaryDirectory() as folder:
            faces = {face: {'texture': '#all'} for face in FACES}
            faces['north']['tintindex'] = 2
            model = {'textures': {'all': 'block/stone', 'moss': 'block/moss'}, 'elements': [
                {**FULL_CUBE, 'faces': faces},
                {'from': [-0.075, -0.075, -0.075], 'to': [16.075, 16.075, 16.075],
                 'faces': {'north': {'texture': '#moss', 'rotation': 90, 'tintindex': 0}}},
                {'from': [0, 0, 0], 'to': [16.1, 16, 16.1],
                 'rotation': {'angle': 0, 'axis': 'y', 'origin': [8, 8, 8]},
                 'faces': {'north': {'texture': '#moss', 'uv': [16, 0, 0, 16]}}}]}
            jar, _ = self.fixture(Path(folder), {}, {}, {}, {}, {'cube': model})
            with zipfile.ZipFile(jar) as archive:
                models = _Models(archive)
                result = _rotated_cube_faces(models, {'model': 'block/cube', 'y': 90})
            self.assertEqual(result['tintIndices'], {'east': 2})
            self.assertEqual(result['faceLayers'], {'east': [
                {'texture': JAVA_BLOCK + 'moss.png', 'orientation': 3, 'tintIndex': 0},
                {'texture': JAVA_BLOCK + 'moss.png', 'orientation': 4, 'tintIndex': -1}]})
            self.assertEqual(models.excluded_geometry, set())

    def test_geometry_outside_cube_surface_is_not_promoted_to_texture_layer(self):
        with tempfile.TemporaryDirectory() as folder:
            faces = {face: {'texture': '#all'} for face in FACES}
            model = {'textures': {'all': 'block/stone'}, 'elements': [
                {**FULL_CUBE, 'faces': faces},
                {'from': [0, 16.5, 0], 'to': [16, 16.5, 16], 'faces': {'up': {'texture': '#all'}}},
                {'from': [1, 0, 0], 'to': [16, 16, 16], 'faces': {'north': {'texture': '#all'}}},
                {'from': [3, 3, 3], 'to': [13, 13, 13], 'faces': faces},
                {**FULL_CUBE, 'rotation': {'axis': 'y', 'angle': 22.5}, 'faces': {'north': {'texture': '#all'}}}]}
            jar, _ = self.fixture(Path(folder), {}, {}, {}, {}, {'cube': model})
            with zipfile.ZipFile(jar) as archive:
                models = _Models(archive)
                result = _rotated_cube_faces(models, {'model': 'block/cube'})
            self.assertEqual(result['faceLayers'], {})
            self.assertEqual({index for _, index, _ in models.excluded_faces}, {1, 2, 3, 4})

    def test_full_cube_texture_shell_keeps_authored_layer_order(self):
        with tempfile.TemporaryDirectory() as folder:
            model = {'textures': {'base': 'block/stone', 'shell': 'block/grass'}, 'elements': [
                {**FULL_CUBE, 'faces': {face: {'texture': '#base'} for face in FACES}},
                {**FULL_CUBE, 'faces': {face: {'texture': '#shell', 'tintindex': 0} for face in FACES}}]}
            jar, _ = self.fixture(Path(folder), {}, {}, {}, {}, {'cube': model})
            with zipfile.ZipFile(jar) as archive:
                result = _rotated_cube_faces(_Models(archive), {'model': 'block/cube'})
            self.assertEqual(set(result['faces'].values()), {JAVA_BLOCK + 'stone.png'})
            self.assertEqual(set(result['faceLayers']), set(FACES))
            self.assertTrue(all(layer[0]['texture'] == JAVA_BLOCK + 'grass.png'
                                for layer in result['faceLayers'].values()))


if __name__ == '__main__':
    unittest.main()
