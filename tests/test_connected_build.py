"""Connected-texture carriers: rule checks, carrier materials, animation, transparency and face geometry."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from addon_package import read_packets
from connected_build import (validate, build_connected, ROOT, CARRIER_OFFSETS, transparent_replacements,
                             model_material_rules, carrier_material, carrier_uv_materials,
                             refresh_carrier_materials)
from package_converter import package
from common import samples_path, scratch

FACES = ('north', 'east', 'south', 'west', 'up', 'down')
SIDES = ('north', 'east', 'south', 'west')


def write_rules(folder, document):
    path = folder / 'rules.json'
    path.write_text(json.dumps(document))
    return path


def read_member(archive, name):
    return json.loads(archive.read(name))


def client_description(archive, stem):
    return read_member(archive, f'Connected_RP/entity/{stem}.entity.json')['minecraft:client_entity']['description']


def first_controller(archive, stem):
    controllers = read_member(archive, f'Connected_RP/render_controllers/{stem}.json')['render_controllers']
    return next(iter(controllers.values()))


class CTMTests(unittest.TestCase):
    def test_baked_tint_materials_use_plain_parents_without_actor_color_masks(self):
        self.assertEqual(carrier_material('entity'), 'entity')
        self.assertEqual(carrier_material('entity_alphatest'), 'entity_alphatest')
        self.assertEqual(carrier_material('entity_alphablend'), 'entity_alphablend')
        definitions = carrier_uv_materials()
        for source, parent in [('entity', 'entity'),
                               ('entity_alphatest', 'entity_alphatest'),
                               ('entity_alphablend', 'entity_alphablend')]:
            self.assertEqual(definitions[carrier_material(source, True) + ':' + parent], {'+defines': ['USE_UV_ANIM']})

    def test_material_refresh_preserves_art_alpha_geometry_and_animation_aliases(self):
        with tempfile.TemporaryDirectory(dir=scratch(ROOT / 'build')) as temporary:
            pack = Path(temporary)
            (pack / 'manifest.json').write_text(json.dumps({'modules': [{'type': 'resources'}]}))
            (pack / 'entity').mkdir()
            (pack / 'materials').mkdir()
            (pack / 'textures').mkdir()
            art = Image.new('RGBA', (3, 1))
            art.putdata([(120, 150, 90, 0), (120, 150, 90, 127), (120, 150, 90, 255)])
            art.save(pack / 'textures/tile.png')
            (pack / 'geometry.json').write_text('{"geometry":"unchanged"}')
            for name, material in [('opaque', 'entity_change_color'), ('cutout', 'entity_alphatest_change_color'),
                                   ('blend', 'entity_alphablend'), ('animated', 'bct_uv_entity_alphatest')]:
                (pack / f'entity/bct_{name}.entity.json').write_text(json.dumps({'minecraft:client_entity': {
                    'description': {'identifier': 'bct:' + name, 'materials': {'default': material},
                                    'textures': {'t0': 'textures/tile'},
                                    'geometry': {'default': 'geometry.unchanged'}}}}))
            (pack / 'materials/connected_build.material').write_text(json.dumps({'materials': {
                'version': '1.0.0', 'bct_uv_entity_alphatest:entity_alphatest': {'+defines': ['USE_UV_ANIM']}}}))
            originals = {name: (pack / name).read_bytes()
                         for name in ('textures/tile.png', 'geometry.json', 'manifest.json')}
            result = refresh_carrier_materials(pack)
            self.assertEqual(result['client_bindings_changed'], 2)
            self.assertTrue(result['uv_material_file_changed'])
            for name, expected in [('opaque', 'entity'), ('cutout', 'entity_alphatest'),
                                   ('blend', 'entity_alphablend'), ('animated', 'bct_uv_entity_alphatest')]:
                document = json.loads((pack / f'entity/bct_{name}.entity.json').read_text())
                self.assertEqual(document['minecraft:client_entity']['description']['materials']['default'], expected)
            for name, original in originals.items():
                self.assertEqual((pack / name).read_bytes(), original)
            self.assertEqual(refresh_carrier_materials(pack)['client_bindings_changed'], 0)
            self.assertFalse(refresh_carrier_materials(pack)['uv_material_file_changed'])

    def test_packaged_model_layer_and_custom_tint_configuration_reaches_renderer(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary)
            for index, name in enumerate(('dirt', 'top', 'overlay', 'white')):
                alpha = 128 if name == 'overlay' else 255
                Image.new('RGBA', (8, 8), (40 + index, 80, 120, alpha)).save(pack / (name + '.png'))
            faces = {face: 'dirt.png' for face in FACES}
            faces['up'] = 'top.png'
            layers = {'north': [{'texture': 'overlay.png', 'orientation': 3, 'tintIndex': 0}]}
            document = {
                'format_version': 1, 'sourceBlocks': [],
                'fullCubeBlocks': ['minecraft:grass_block', 'minecraft:white_wool'],
                'baseTextures': {'minecraft:grass_block': faces, 'minecraft:white_wool': {'north': 'white.png'}},
                'baseTextureVariants': {'minecraft:grass_block': [{
                    'states': {}, 'faces': faces, 'orientations': {face: 0 for face in faces},
                    'tintIndices': {'up': 0}, 'faceLayers': layers}]},
                'modelTintTypes': {'minecraft:grass_block': 'grass', 'minecraft:white_wool': [0.9, 0.8, 0.7]},
                'customTintBlocks': ['minecraft:white_wool'],
                'grassTints': {'minecraft:plains': [0.2, 0.4, 0.6]},
                'foliageTints': {'minecraft:plains': [0.1, 0.3, 0.5]},
                'customTints': {'custom:palette': {'minecraft:plains': [0.7, 0.8, 0.9]}},
                'rules': [{'id': 'grass_top', 'method': 'fixed', 'blocks': ['minecraft:grass_block'],
                           'faces': ['up'], 'tiles': ['top.png']}]}
            archive = build_connected(pack, write_rules(pack, document), 'layer-config-test', renderer='classic')
            with zipfile.ZipFile(archive) as output:
                config = read_packets(output.read('Connected_BP/scripts/source-data.js').decode())['connected']
                for field in ('modelTintTypes', 'customTintBlocks', 'grassTints', 'foliageTints', 'customTints'):
                    self.assertEqual(config[field], document[field])
                self.assertEqual(config['baseTextureVariants']['minecraft:grass_block'][0]['faceLayers'], layers)
                self.assertEqual(set(config['sourceBlocks']), {'minecraft:grass_block', 'minecraft:white_wool'})
                self.assertEqual(config['modelRenderBlocks'], ['minecraft:white_wool'])
                self.assertEqual(set(config['modelTextureRules']), {'overlay.png', 'white.png'})
                layer_rule = next(rule for rule in config['rules']
                                  if rule['id'] == config['modelTextureRules']['overlay.png'])
                stem = layer_rule['entity'].replace(':', '_', 1)
                client = client_description(output, stem)
                self.assertTrue(all('g' + str(index) in client['geometry'] for index in range(8)))
                renderer = first_controller(output, stem)
                self.assertEqual(renderer['color'], {'r': 1, 'g': 1, 'b': 1, 'a': 1})
                self.assertIn([0.2, 0.4, 0.6], layer_rule['tintPalette'])
                self.assertEqual(len(client['textures']), 2)
                self.assertIn("q.property('bct:palette') * 1", renderer['textures'][0])

    def test_model_layers_and_weighted_faces_have_explicit_renderer_materials(self):
        document = {
            'fullCubeBlocks': ['minecraft:stone', 'minecraft:grass_block', 'minecraft:white_wool'],
            'customTintBlocks': ['minecraft:white_wool'],
            'baseTextures': {'minecraft:white_wool': {'north': 'white.png'}},
            'baseTextureVariants': {
                'minecraft:stone': [
                    {'states': {'a': True}, 'faces': {'north': 'plain.png'}},
                    {'modelChoices': [{'faces': {'north': 'a.png'}, 'weight': 1},
                                      {'faces': {'north': 'b.png'}, 'weight': 2}]}],
                'minecraft:grass_block': [{'faces': {'north': 'dirt.png'}, 'faceLayers': {
                    'north': [{'texture': 'grass.png', 'orientation': 3, 'tintIndex': 0}]}}]}}
        rules, textures, blocks, layers = model_material_rules(document)
        self.assertEqual(set(textures), {'a.png', 'b.png', 'plain.png', 'grass.png', 'white.png'})
        self.assertNotIn('minecraft:grass_block', blocks)
        self.assertEqual(layers, ['minecraft:grass_block'])
        self.assertEqual(set(blocks), {'minecraft:stone', 'minecraft:white_wool'})
        self.assertTrue(all(rule['fallback'] and rule['modelFallback'] for rule in rules))
        self.assertEqual(model_material_rules(document)[1], textures)

    def test_animated_cube_frames_use_client_uv_clock_and_keep_pbr_aligned(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary)
            color = Image.new('RGBA', (8, 16), (10, 20, 30, 255))
            color.paste((80, 90, 100, 255), (0, 8, 8, 16))
            color.save(pack / 'animated.png')
            (pack / 'animated.png.mcmeta').write_text(json.dumps(
                {'animation': {'frametime': 2, 'frames': [1, {'index': 0, 'time': 4}]}}))
            Image.new('RGB', (8, 16), (128, 128, 255)).save(pack / 'normal.png')
            (pack / 'animated.texture_set.json').write_text(json.dumps(
                {'minecraft:texture_set': {'color': 'animated', 'normal': 'normal'}}))
            rule_file = write_rules(pack, {'format_version': 1, 'rules': [
                {'id': 'test', 'method': 'fixed', 'blocks': ['minecraft:stone'], 'tiles': ['animated']}]})
            archive = build_connected(pack, rule_file, 'animation-test')
            with zipfile.ZipFile(archive) as output:
                client = client_description(output, 'bct_animation_test_test')
                self.assertEqual(client['materials']['default'], 'bct_uv')
                materials = read_member(output, 'Connected_RP/materials/entity.material')['materials']
                self.assertEqual(materials['bct_uv:entity'], {'+defines': ['USE_UV_ANIM']})
                self.assertNotIn('Connected_RP/materials/connected_build.material', output.namelist())
                controller = first_controller(output, 'bct_animation_test_test')
                self.assertIn('q.time_stamp / 2', controller['uv_anim']['offset'][1])
                self.assertIn(str(8 / 144), controller['uv_anim']['scale'][1])
                for suffix in ('', '_normal'):
                    texture = 'Connected_RP/textures/entity/bct_animation_test_test_0' + suffix + '.png'
                    with output.open(texture) as stream:
                        image = Image.open(stream)
                        self.assertEqual(image.size, (72, 144))
                        if not suffix:
                            self.assertEqual(image.getpixel((32, 32)), (80, 90, 100, 255))
                            self.assertEqual(image.getpixel((0, 0)), image.getpixel((32, 32)))
            with Image.open(pack / 'animated.png') as source:
                self.assertEqual(color.tobytes(), source.tobytes())

    def test_transparent_replacement_hides_native_borders_and_exports_base_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary)
            connected = Image.new('RGBA', (16, 16), (100, 100, 100, 0))
            connected.putpixel((0, 0), (100, 100, 100, 255))
            connected.save(pack / 'connected.png')
            Image.new('RGBA', (16, 16), (255, 255, 255, 80)).save(pack / 'glass.png')
            document = {'format_version': 1, 'fullCubeBlocks': ['minecraft:glass'],
                        'baseTextures': {'minecraft:glass': {face: 'glass' for face in FACES}},
                        'rules': [{'id': 'glass', 'method': 'fixed', 'blocks': ['minecraft:glass'],
                                   'faces': list(FACES), 'tiles': ['connected']}]}
            rules = validate(document, pack)
            overrides, fallbacks, generated = transparent_replacements(document, rules, pack, samples_path())
            self.assertEqual(set(overrides['glass']['textures'].values()), {'bct_owned_transparent'})
            self.assertEqual(len(generated), 1)
            self.assertEqual(generated[0]['tiles'], ['glass'])
            self.assertTrue(generated[0]['fallback'])
            self.assertEqual(len(fallbacks['minecraft:glass']), 6)
            archive = build_connected(pack, write_rules(pack, document), 'startup-test', renderer='classic')
            with zipfile.ZipFile(archive) as output:
                cutout = client_description(output, 'bct_startup_test_glass')
                self.assertEqual(cutout['materials']['default'], 'entity_alphatest')
                self.assertEqual(output.read('Connected_RP/textures/entity/bct_startup_test_glass_0.png'),
                                 (pack / 'connected.png').read_bytes())
                translucent = client_description(output, 'bct_startup_test_native_fallback_0')
                self.assertEqual(translucent['materials']['default'], 'entity_alphablend')
            document['rules'][0]['method'] = 'overlay_fixed'
            overrides, fallbacks, generated = transparent_replacements(
                document, validate(document, pack), pack, samples_path())
            self.assertEqual((overrides, fallbacks, generated), ({}, {}, []))

    def test_standalone_authoring_zip_builds_diagnostic_without_workspace_inputs(self):
        archive = package()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            with zipfile.ZipFile(archive) as packed:
                packed.extractall(destination)
            builder = destination / 'Bedrock-Connected-Textures-Converter'
            result = subprocess.run([sys.executable, str(builder / 'converter/demo_pack.py'),
                                     '--samples', str(samples_path())],
                                    cwd=builder, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((builder / 'dist/development/connected-diagnostic.mcaddon').is_file())

    def test_build_preserves_pixels_pbr_and_generates_face_controller(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary)
            Image.new('RGBA', (16, 16), (50, 100, 150, 255)).save(pack / 'tile.png')
            Image.new('RGBA', (16, 16), (10, 20, 30, 40)).save(pack / 'mers.png')
            (pack / 'tile.texture_set.json').write_text(json.dumps({'minecraft:texture_set': {
                'color': 'tile', 'metalness_emissive_roughness_subsurface': 'mers'}}))
            rule_file = write_rules(pack, {'format_version': 1, 'rules': [
                {'id': 'test', 'method': 'fixed', 'blocks': ['minecraft:stone'], 'tiles': ['tile']}]})
            archive = build_connected(pack, rule_file, 'test-suite')
            with zipfile.ZipFile(archive) as output:
                names = output.namelist()
                self.assertEqual(sorted(name for name in names if '/scripts/' in name),
                                 ['Connected_BP/scripts/main.js', 'Connected_BP/scripts/publisher.js',
                                  'Connected_BP/scripts/source-data.js'],
                                 'carrier packs carry data, not the engine')
                entity = read_member(output, 'Connected_BP/entities/bct_test_suite_test.json')
                self.assertEqual(entity['minecraft:entity']['description']['runtime_identifier'],
                                 'minecraft:area_effect_cloud')
                self.assertIn('Connected_BP/structures/bct/bct_test_suite_test.mcstructure', names)
                texture_set = read_member(output, 'Connected_RP/textures/entity/bct_test_suite_test_0.texture_set.json')
                channel = texture_set['minecraft:texture_set']['metalness_emissive_roughness_subsurface']
                with output.open('Connected_RP/textures/entity/' + channel + '.png') as stream:
                    with Image.open(stream) as image:
                        self.assertEqual(image.getpixel((0, 0)), (10, 20, 30, 40))
                self.assertNotIn('Connected_BP/blocks', names)
                client = client_description(output, 'bct_test_suite_test')
                self.assertEqual(client['materials']['default'], 'entity')
                self.assertEqual(len(first_controller(output, 'bct_test_suite_test')['part_visibility']), 6)
                model = read_member(output, 'Connected_RP/models/entity/bct_test_suite_test.geo.json')
                self.assert_face_planes(model['minecraft:geometry'][0]['bones'])

    def assert_face_planes(self, bones):
        """Each plane covers its whole face, 1/256 of a block outside it (the top plane 3/1024)."""
        top = next(bone for bone in bones if bone['name'] == 'up')['cubes'][0]
        self.assertAlmostEqual((top['origin'][1] + top['size'][1] + CARRIER_OFFSETS['up'][1] * 16) * 16, .75)
        surfaces = {'north': -8.0625, 'east': 8.0625, 'south': 8.0625, 'west': -8.0625, 'up': 3 / 64, 'down': -16.0625}
        normal_axes = {'north': 2, 'east': 0, 'south': 2, 'west': 0, 'up': 1, 'down': 1}
        for bone in bones:
            face = bone['name']
            cube = bone['cubes'][0]
            self.assertEqual(bone['rotation'], [0, 180, 0])
            if face in SIDES:
                expected_uv = {'uv': [0, 0], 'uv_size': [16, 16]}
            else:
                expected_uv = {'uv': [16, 16], 'uv_size': [-16, -16]}
            self.assertEqual(cube['uv'][face], expected_uv)
            # Undo the geometry import's X mirror, then the carrier's offset from the block.
            imported_origin = cube['origin'].copy()
            imported_origin[0] = -imported_origin[0] - cube['size'][0]
            restored = [value + CARRIER_OFFSETS[face][axis] * 16 for axis, value in enumerate(imported_origin)]
            axis = normal_axes[face]
            surface = restored[axis] + (cube['size'][axis] if face in ('east', 'south', 'up') else 0)
            self.assertAlmostEqual(surface, surfaces[face])
            if face in SIDES:
                tangent = 0 if face in ('north', 'south') else 2
                self.assertAlmostEqual(restored[tangent], -8)
                self.assertAlmostEqual(restored[tangent] + cube['size'][tangent], 8)

    def test_invalid_inputs_fail_before_emission(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary)
            Image.new('RGBA', (16, 16), (50, 100, 150, 255)).save(pack / 'tile.png')
            base = {'id': 'test', 'method': 'fixed', 'blocks': ['minecraft:stone'], 'tiles': ['tile']}
            for changes in ({'tiles': ['../outside']}, {'method': 'repeat', 'width': 2, 'height': 2},
                            {'method': 'unknown'}, {'faces': ['sideways']}, {'typo': True}):
                with self.assertRaises(ValueError):
                    validate({'format_version': 1, 'rules': [{**base, **changes}]}, pack)
            Image.new('RGBA', (16, 16)).save(pack / 'tile.png')
            self.assertEqual(validate({'format_version': 1, 'rules': [base]}, pack)[0]['tiles'], ['tile'])

    def test_combined_repeat_methods_validate_the_full_connection_product(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary)
            Image.new('RGBA', (16, 16), (50, 100, 150, 255)).save(pack / 'tile.png')
            for method, count, width, height in [('ctm_repeat', 188, 2, 2), ('horizontal_repeat', 12, 3, 1)]:
                rule = {'id': 'test', 'method': method, 'blocks': ['minecraft:stone'],
                        'tiles': ['tile'] * count, 'width': width, 'height': height}
                self.assertEqual(len(validate({'format_version': 1, 'rules': [rule]}, pack)[0]['tiles']), count)
                rule['tiles'] = rule['tiles'][:-1]
                with self.assertRaises(ValueError):
                    validate({'format_version': 1, 'rules': [rule]}, pack)


if __name__ == '__main__':
    unittest.main()
