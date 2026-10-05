"""The Java pack conversion pipeline: resumable materials, base packs, publishing and the RTX edition."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from addon_package import checksum, exports
from common import BLOCKS_FORMAT, samples_path, scratch, write_json
from connected_build import build_connected
from convert_java_author_pack import (EffectiveStack, attach_binding_providers, claim_destination, export_base,
                                      manifest, package_directory, prepare, prepare_shared_terrain,
                                      publish_conversion, restore_carrier_blocks_for_rtx)
from engine_package import ENGINE, engine_dependency
from java_pack_api import PackStack

STONE = 'assets/minecraft/textures/block/stone.png'


def png(size=(2, 2), color=(100, 120, 140, 255)):
    output = io.BytesIO()
    Image.new('RGBA', size, color).save(output, format='PNG')
    return output.getvalue()


def write_zip(path, files):
    with zipfile.ZipFile(path, 'w') as output:
        for name, data in files.items():
            output.writestr(name, data)


def bedrock_samples(folder):
    """A tiny stand-in for bedrock-samples' resource_pack folder."""
    write_json(folder / 'blocks.json', {
        'format_version': [1, 1, 0],
        'stone': {'sound': 'stone', 'textures': 'stone'},
        'glass': {'sound': 'glass', 'textures': 'glass'},
        'chiseled_bookshelf': {'sound': 'chiseled_bookshelf', 'textures': 'chiseled_bookshelf'},
        'furnace': {'sound': 'stone', 'textures': {'up': 'furnace_top', 'down': 'furnace_top', 'side': 'furnace_side',
                                                   'north': 'furnace_front_off'}},
        'grass': {'isotropic': {'up': True, 'down': True}, 'sound': 'grass',
                  'textures': {'up': 'grass_top', 'down': 'grass_bottom', 'side': 'grass_side'}}})
    write_json(folder / 'textures/terrain_texture.json', {'texture_data': {
        'stone': {'textures': 'textures/blocks/stone'}, 'grass_top': {'textures': 'textures/blocks/grass_top'}}})
    write_json(folder / 'textures/flipbook_textures.json', [])
    return folder


def export_fixture(root, **bindings):
    """A conversion folder with one converted stone material, and bindings that show it on stone."""
    material = root / 'conversion/native-materials' / STONE
    material.parent.mkdir(parents=True)
    material.write_bytes(png())
    material_bindings = {'textures/blocks/stone': STONE}
    return root / 'conversion', {'materialBindings': material_bindings, 'authoredMaterialBindings': material_bindings,
                                 **bindings}


def block_files(conversion):
    """Each base pack's blocks.json by graphics mode, None where the pack has none."""
    files = {}
    for renderer in ('classic', 'vv', 'rtx'):
        path = conversion / ('base-' + renderer) / 'blocks.json'
        files[renderer] = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    return files


class AuthorConversionTests(unittest.TestCase):
    def test_converted_pack_is_one_addon_that_depends_on_the_engine(self):
        workspace = Path(__file__).resolve().parents[1]
        samples = samples_path()
        with tempfile.TemporaryDirectory(dir=scratch(workspace / 'build')) as directory:
            folder = Path(directory)
            resource = folder / 'resource-pack'
            write_json(resource / 'manifest.json', manifest('shared-publication-fixture', 'Fixture', 'vv'))
            materials = ('grass_top', 'sand', 'red_sand', 'suspicious_sand_0', 'soul_sand')
            write_json(resource / 'textures/terrain_texture.json', {'texture_data': {
                ('suspicious_sand' if material == 'suspicious_sand_0' else material):
                    {'textures': 'textures/blocks/' + material} for material in materials}})
            for index, material in enumerate(materials):
                path = resource / 'textures/blocks' / (material + '.png')
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new('RGBA', (8, 8), (60 + index * 10, 80, 40, 255)).save(path)
            artwork = folder / 'connected-source'
            artwork.mkdir()
            tile = png((8, 8), (70, 90, 110, 255))
            (artwork / 'stone.png').write_bytes(tile)
            rule_file = artwork / 'rules.json'
            write_json(rule_file, {'format_version': 1, 'rules': [{
                'id': 'fixture', 'method': 'fixed', 'blocks': ['minecraft:stone'], 'tiles': ['stone']}]})
            connected = build_connected(artwork, rule_file, 'shared-publication-fixture-vv',
                                        samples=samples, renderer='vv', artifact_directory=folder / 'connected')
            base = [{'renderer': 'rtx', 'resource_pack': str(resource), 'archive': str(resource)}]
            published = publish_conversion(folder, base, connected, 'shared-publication-fixture', 'Fixture', samples,
                                           root=workspace)
            self.assertEqual(sorted(path.name for path in folder.glob('*.mcaddon')),
                             ['shared-publication-fixture.mcaddon'])
            self.assertFalse((folder / 'shared-engines').exists())
            with zipfile.ZipFile(published['installable_packs'][0]['archive']) as source:
                behavior_manifest = json.loads(source.read('Source_BP/manifest.json'))
                resource_manifest = json.loads(source.read('Source_RP/manifest.json'))
                self.assertEqual(behavior_manifest['header']['name'], 'Fixture')
                self.assertEqual(resource_manifest['header']['name'], 'Fixture')
                self.assertEqual(resource_manifest['capabilities'], ['pbr', 'raytraced'])
                self.assertNotIn('subpacks', resource_manifest, 'one pack for every graphics mode, no setting')
                self.assertEqual(sorted(name for name in source.namelist() if '/scripts/' in name), [
                    'Source_BP/scripts/main.js', 'Source_BP/scripts/publisher.js', 'Source_BP/scripts/source-data.js'])
                engine_modules = {name + '.js' for name in ('engine', 'connected', 'terrain', 'tiles', 'scanner')}
                self.assertFalse(any(name.rsplit('/', 1)[-1] in engine_modules for name in source.namelist()))
                self.assertIn(engine_dependency(), behavior_manifest['dependencies'])
                self.assertEqual(engine_dependency()['uuid'], ENGINE['header_uuid'])
                packets = exports(source.read('Source_BP/scripts/source-data.js').decode())['sources']
                self.assertEqual([packet['engine'] for packet in packets], ['connected', 'terrain'])
                for packet in packets:
                    payload = ''.join(packet['parts'])
                    self.assertEqual(checksum(payload), packet['digest'])
                    self.assertEqual(packet['provider'], 'shared-publication-fixture')
                    data = json.loads(payload)
                    if packet['engine'] == 'connected':
                        self.assertEqual(len(data['rules']), 1)
                    else:
                        self.assertEqual(len(data), 1)
                        self.assertTrue(all(effect['entity'].startswith('bct_shared_publication_fixture:')
                                            for effect in data[0]['effects'].values()))
                self.assertIn(tile, [source.read(name) for name in source.namelist()
                                     if name.startswith('Source_RP/textures/entity/') and name.endswith('.png')])
            dependencies = {item['uuid']: item['version']
                            for item in behavior_manifest['dependencies'] if 'uuid' in item}
            self.assertEqual(dependencies[resource_manifest['header']['uuid']], resource_manifest['header']['version'])

    def test_publishing_composes_one_pack_for_every_graphics_mode(self):
        # Like the author's own Bedrock pack: the RTX base is the one resource pack, ray tracing included,
        # with no setting to pick a mode.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = [{'renderer': 'classic', 'resource_pack': 'classic-rp', 'archive': 'classic.mcpack'},
                    {'renderer': 'vv', 'resource_pack': 'pbr-rp', 'archive': 'pbr.mcpack'},
                    {'renderer': 'rtx', 'resource_pack': 'native-rtx-rp', 'archive': 'native-rtx.mcpack'}]
            with patch('convert_java_author_pack.prepare_shared_terrain',
                       return_value=('terrain.mcaddon', {'status': 'built'})) as terrain,                     patch('addon_package.compose_source_addon', return_value=root / 'author.mcaddon') as compose:
                report = publish_conversion(root, base, 'connected.mcaddon', 'author', 'Author', root / 'samples',
                                            root=root)
            terrain.assert_called_once_with('native-rtx-rp', 'author', 'Author', root / 'samples', root=root, overlay=None)
            compose.assert_called_once_with('native-rtx.mcpack', 'connected.mcaddon', root / 'author.mcaddon',
                                            key='author', title='Author', terrain_addon='terrain.mcaddon', icon=None,
                                            identity=None, replacement_addon=None, raytraced=True)
            self.assertEqual(len(report['installable_packs']), 1)
            self.assertEqual(report['installable_packs'][0]['renderer'], 'classic-vv-rtx')
            self.assertEqual(report['installable_packs'][0]['capabilities'], ['pbr', 'raytraced'])

    def test_animated_terrain_is_reported_without_replacing_source_animation(self):
        with patch('terrain_pack.prepare', side_effect=ValueError(
                'Animated or non-square textures need a square still image: grass.png')), \
                patch('terrain_providers.build') as build:
            archive, report = prepare_shared_terrain('source-rp', 'author', 'Author', Path('samples'), root=Path('.'))
        self.assertIsNone(archive)
        self.assertEqual(report['status'], 'unsupported_animated_terrain')
        self.assertTrue(report['animations_preserved_in_source_pack'])
        self.assertFalse(report['animation_replaced_with_still'])
        build.assert_not_called()

    def test_resume_rejects_changed_archive_at_same_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'author.zip'
            vanilla = root / 'vanilla.jar'
            output = root / 'converted'
            write_zip(source, {'pack.mcmeta': '{}', 'kept.png': png()})
            write_zip(vanilla, {})
            claim_destination([source], output, 'test', 'Test', vanilla)
            marker = (output / '.author-conversion.json').read_bytes()
            claim_destination([source], output, 'test', 'Test', vanilla)
            write_zip(source, {'pack.mcmeta': '{}'})
            with self.assertRaisesRegex(ValueError, 'different source bytes'):
                claim_destination([source], output, 'test', 'Test', vanilla)
            self.assertEqual((output / '.author-conversion.json').read_bytes(), marker)

    def test_resume_rejects_changed_explicit_vanilla_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'author.zip'
            vanilla = root / 'vanilla.jar'
            output = root / 'converted'
            write_zip(source, {'pack.mcmeta': '{}'})
            write_zip(vanilla, {})
            claim_destination([source], output, 'test', 'Test', vanilla)
            write_zip(vanilla, {'changed': 'reference'})
            with self.assertRaisesRegex(ValueError, 'different source bytes'):
                claim_destination([source], output, 'test', 'Test', vanilla)

    def test_state_only_texture_match_enters_runtime_source_scan(self):
        bindings = {'baseTextures': {'minecraft:oak_log': {'up': 'end.png'}},
                    'baseTextureVariants': {'minecraft:oak_log': [
                        {'states': {'pillar_axis': 'x'}, 'faces': {'up': 'side.png'}}]}}
        rules = {'rules': [{'matchTiles': ['side.png']}], 'sourceBlocks': []}
        attach_binding_providers(rules, bindings)
        self.assertEqual(rules['sourceBlocks'], ['minecraft:oak_log'])
        self.assertEqual(rules['baseTextureVariants'], bindings['baseTextureVariants'])

    def test_static_author_override_does_not_inherit_vanilla_animation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sprite = 'assets/minecraft/textures/block/magma.png'
            write_zip(root / 'vanilla.jar', {sprite: png((2, 4)), sprite + '.mcmeta': '{"animation":{"frames":[0,1]}}'})
            write_zip(root / 'author.zip', {'pack.mcmeta': '{}', sprite: png()})
            with PackStack([root / 'author.zip']) as source:
                effective = EffectiveStack(source, root / 'vanilla.jar')
                try:
                    self.assertNotIn(sprite + '.mcmeta', effective.files)
                    self.assertEqual(effective.read(sprite), png())
                finally:
                    effective.close()
            report = prepare([root / 'author.zip'], root / 'vanilla.jar', root / 'converted', workers=1)
            self.assertTrue(report['material_conversion_complete'])
            self.assertEqual(report['animation_count'], 0)
            self.assertEqual((root / 'converted/materials' / sprite).read_bytes(), png())

    def test_metadata_can_override_an_inherited_image(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sprite = 'assets/minecraft/textures/block/water_still.png'
            metadata = b'{"animation":{"frames":[1,0],"frametime":3}}'
            write_zip(root / 'vanilla.jar', {sprite: png((2, 4)), sprite + '.mcmeta': '{"animation":{}}'})
            write_zip(root / 'author.zip', {'pack.mcmeta': '{}', sprite + '.mcmeta': metadata})
            with PackStack([root / 'author.zip']) as source:
                effective = EffectiveStack(source, root / 'vanilla.jar')
                try:
                    self.assertIn(sprite + '.mcmeta', effective.files)
                    self.assertEqual(effective.read(sprite + '.mcmeta'), metadata)
                finally:
                    effective.close()

    def test_failed_material_is_reported_without_partial_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sprite = 'assets/minecraft/textures/block/fire_0.png'
            write_zip(root / 'vanilla.jar', {})
            write_zip(root / 'author.zip', {'pack.mcmeta': '{}', sprite: png(),
                                            sprite + '.mcmeta': '{"animation":{"frames":[true]}}'})
            before = (root / 'author.zip').read_bytes()
            report = prepare([root / 'author.zip'], root / 'vanilla.jar', root / 'converted', workers=1)
            self.assertFalse(report['material_conversion_complete'])
            self.assertEqual(report['failed_materials'][0]['source'], sprite)
            self.assertFalse((root / 'converted/materials' / sprite).exists())
            self.assertEqual((root / 'author.zip').read_bytes(), before)


class BaseBlockDefinitionTests(unittest.TestCase):
    """The base packs define only the blocks they change, so the game's own definitions stay in charge."""

    def test_rtx_base_pack_defines_only_the_blocks_it_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            samples = bedrock_samples(root / 'samples')
            conversion, bindings = export_fixture(root, blockFaceOverrides={'minecraft:furnace': {'north': STONE}})
            export_base(conversion, bindings, samples, 'fixture', 'Fixture')
            files = block_files(conversion)
            for renderer in ('classic', 'vv', 'rtx'):
                self.assertEqual(set(files[renderer]), {'format_version', 'furnace'})
                faces = files[renderer]['furnace']['textures']
                self.assertTrue(faces['north'].startswith('author_face_'))
                self.assertEqual((faces['up'], faces['side']), ('furnace_top', 'furnace_side'))
            with zipfile.ZipFile(conversion / 'fixture-base-rtx.mcpack') as archive:
                self.assertEqual(set(json.loads(archive.read('blocks.json'))), {'format_version', 'furnace'})

    def test_rtx_base_pack_keeps_an_empty_block_file_for_the_composer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            samples = bedrock_samples(root / 'samples')
            conversion, bindings = export_fixture(root)
            export_base(conversion, bindings, samples, 'fixture', 'Fixture')
            files = block_files(conversion)
            self.assertIsNone(files['classic'])
            self.assertIsNone(files['vv'])
            self.assertEqual(files['rtx'], {'format_version': BLOCKS_FORMAT})

    def test_grass_top_keeps_its_random_turn(self):
        # Grass stays vanilla, so the tile a world-independent rule picks for its top turns as vanilla's does.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            samples = bedrock_samples(root / 'samples')
            conversion, bindings = export_fixture(root, nativeFaceFallbacks={
                'assets/minecraft/textures/block/grass_block_top.png': STONE})
            export_base(conversion, bindings, samples, 'fixture', 'Fixture')
            for renderer, blocks in block_files(conversion).items():
                self.assertNotIn('grass', blocks or {}, renderer)
                self.assertNotIn('grass_block', blocks or {}, renderer)

    def test_rtx_pack_restores_the_blocks_carriers_draw_over(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            samples = bedrock_samples(root / 'samples')
            pack = root / 'base-rtx'
            author_furnace = {'sound': 'stone', 'textures': {'north': 'author_face_0', 'side': 'furnace_side'}}
            write_json(pack / 'blocks.json', {'format_version': [1, 1, 0], 'furnace': author_furnace})
            archive = package_directory(pack, root / 'fixture-base-rtx.mcpack')
            connected = root / 'connected.mcaddon'
            blank = 'bct_owned_transparent'
            write_zip(connected, {'Connected_RP/blocks.json': json.dumps({
                'format_version': [1, 1, 0],
                'glass': {'sound': 'glass', 'textures': {face: blank for face in ('north', 'up')}},
                'chiseled_bookshelf': {'sound': 'chiseled_bookshelf', 'textures': blank},
                'furnace': {'textures': {'north': blank}},
                'stone': {'textures': 'stone'}})})
            base = [{'renderer': 'vv', 'resource_pack': str(root / 'base-vv'), 'archive': 'vv.mcpack'},
                    {'renderer': 'rtx', 'resource_pack': str(pack), 'archive': str(archive)}]
            restored = restore_carrier_blocks_for_rtx(base, connected, samples)
            self.assertEqual(restored, ['chiseled_bookshelf', 'glass'])
            blocks = json.loads((pack / 'blocks.json').read_text(encoding='utf-8'))
            self.assertEqual(blocks, {'format_version': [1, 1, 0], 'furnace': author_furnace,
                                      'chiseled_bookshelf': {'sound': 'chiseled_bookshelf',
                                                             'textures': 'chiseled_bookshelf'},
                                      'glass': {'sound': 'glass', 'textures': 'glass'}})
            with zipfile.ZipFile(archive) as packaged:
                self.assertEqual(json.loads(packaged.read('blocks.json')), blocks)

    def test_rtx_pack_is_left_alone_when_carriers_blank_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            samples = bedrock_samples(root / 'samples')
            pack = root / 'base-rtx'
            write_json(pack / 'blocks.json', {'format_version': [1, 1, 0]})
            archive = package_directory(pack, root / 'fixture-base-rtx.mcpack')
            before = archive.read_bytes()
            connected = root / 'connected.mcaddon'
            write_zip(connected, {'Connected_BP/manifest.json': '{}'})
            base = [{'renderer': 'rtx', 'resource_pack': str(pack), 'archive': str(archive)}]
            self.assertEqual(restore_carrier_blocks_for_rtx(base, connected, samples), [])
            self.assertEqual(archive.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
