import json
import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from native_random import build_native_random


def write_tile_set(source, tile):
    (source / (tile + '.texture_set.json')).write_text(json.dumps({'minecraft:texture_set': {
        'color': tile, 'metalness_emissive_roughness_subsurface': tile + '_mers'}}))


class NativeRandomTests(unittest.TestCase):
    def test_animation_binds_each_alias_and_variant_with_aligned_pbr(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.mkdir()
            for tile in ('a', 'b'):
                color = Image.new('RGBA', (8, 16), (10, 20, 30, 255))
                color.paste((90, 80, 70, 255), (0, 8, 8, 16))
                color.save(source / (tile + '.png'))
                material = Image.new('RGBA', (8, 16), (1, 2, 3, 4))
                material.paste((11, 12, 13, 14), (0, 8, 8, 16))
                material.save(source / (tile + '_mers.png'))
                (source / (tile + '.png.mcmeta')).write_text('{"animation":{"frametime":3,"frames":[1,0]}}')
                write_tile_set(source, tile)
            rules = [{'id': block, 'method': 'random', 'blocks': ['minecraft:' + block], 'tiles': ['a', 'b']}
                     for block in ('stone', 'andesite')]
            definitions = {block: {'textures': block} for block in ('stone', 'andesite')}
            for renderer in ('classic', 'vv', 'rtx'):
                output = root / (renderer + '.mcpack')
                report = build_native_random(source, {'format_version': 1, 'rules': rules}, definitions, 'animated',
                                             renderer, output)
                self.assertEqual(report['animated_materials'], 2)
                self.assertEqual(report['animation_bindings'], 4)
                with zipfile.ZipFile(output) as archive:
                    terrain = json.loads(archive.read('textures/terrain_texture.json'))['texture_data']
                    animations = json.loads(archive.read('textures/flipbook_textures.json'))
                    bound = {(entry['atlas_tile'], entry['atlas_tile_variant']) for entry in animations}
                    self.assertEqual(len(bound), 4)
                    for entry in animations:
                        self.assertEqual(entry['atlas_index'], 0)
                        self.assertEqual(entry['frames'], [0, 1])
                        self.assertEqual(entry['ticks_per_frame'], 3)
                        variation = terrain[entry['atlas_tile']]['textures']['variations'][entry['atlas_tile_variant']]
                        path = variation['path']
                        self.assertEqual(entry['flipbook_texture'], path)
                        with Image.open(io.BytesIO(archive.read(path + '.png'))) as image:
                            self.assertEqual(image.getpixel((0, 0))[:3], (90, 80, 70))
                        if renderer != 'classic':
                            descriptor = json.loads(archive.read(path + '.texture_set.json'))['minecraft:texture_set']
                            channel = 'metalness_emissive_roughness' + ('_subsurface' if renderer == 'vv' else '')
                            name = str(Path(path).parent / descriptor[channel]).replace('\\', '/') + '.png'
                            with Image.open(io.BytesIO(archive.read(name))) as image:
                                expected = (11, 12, 13, 14) if renderer == 'vv' else (11, 12, 13)
                                self.assertEqual(image.getpixel((0, 0)), expected)

    def test_native_variants_preserve_unselected_faces_and_pbr(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.mkdir()
            for tile in ('a', 'b'):
                Image.new('RGB', (8, 8), (30, 60, 90) if tile == 'a' else (90, 60, 30)).save(source / (tile + '.png'))
                Image.new('RGBA', (8, 8), (15, 25, 35, 45)).save(source / (tile + '_mers.png'))
                write_tile_set(source, tile)
            before = {path.name: path.read_bytes() for path in source.iterdir()}
            definition = {'textures': {'side': 'bark', 'up': 'endgrain', 'down': 'endgrain'}, 'sound': 'wood'}
            rule = {'id': 'wood', 'method': 'random', 'blocks': ['minecraft:oak_log'],
                    'faces': ['north', 'east', 'south', 'west'], 'tiles': ['a', 'b'], 'weights': [3, 1]}
            for renderer in ('classic', 'vv', 'rtx'):
                output = root / (renderer + '.mcpack')
                report = build_native_random(source, {'format_version': 1, 'rules': [rule]}, {'oak_log': definition},
                                             'test', renderer, output)
                self.assertFalse(report['java_random_seed_parity'])
                with zipfile.ZipFile(output) as archive:
                    block = json.loads(archive.read('blocks.json'))['oak_log']
                    self.assertEqual(block['textures']['up'], 'endgrain')
                    self.assertEqual(block['textures']['down'], 'endgrain')
                    self.assertEqual(block['sound'], 'wood')
                    terrain = json.loads(archive.read('textures/terrain_texture.json'))['texture_data']
                    entries = terrain[block['textures']['north']]['textures']['variations']
                    self.assertEqual([entry['weight'] for entry in entries], [3, 1])
                    self.assertFalse(any('entity' in name for name in archive.namelist()))
                    for entry in entries:
                        descriptor = entry['path'] + '.texture_set.json'
                        if renderer == 'classic':
                            self.assertNotIn(descriptor, archive.namelist())
                        else:
                            channels = json.loads(archive.read(descriptor))['minecraft:texture_set']
                            channel = 'metalness_emissive_roughness' + ('_subsurface' if renderer == 'vv' else '')
                            self.assertIn(channel, channels)
            self.assertEqual(before, {path.name: path.read_bytes() for path in source.iterdir()})
            self.assertEqual(definition['textures']['up'], 'endgrain')

    def test_java_selection_settings_are_not_silently_approximated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rule = {'id': 'ore', 'method': 'random', 'blocks': ['minecraft:stone'], 'tiles': ['tile'], 'linked': True}
            with self.assertRaisesRegex(ValueError, 'cannot discard'):
                build_native_random(root / 'source', {'format_version': 1, 'rules': [rule]}, {}, 'test', 'rtx',
                                    root / 'out.mcpack')
            self.assertFalse((root / 'out.mcpack').exists())

    def test_export_cannot_overwrite_source_or_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.mkdir()
            document = {'format_version': 1, 'rules': []}
            with self.assertRaisesRegex(ValueError, 'outside the source'):
                build_native_random(source, document, {}, 'test', 'rtx', source / 'output.mcpack')
            output = root / 'existing.mcpack'
            output.write_bytes(b'keep')
            with self.assertRaisesRegex(ValueError, 'already exists'):
                build_native_random(source, document, {}, 'test', 'rtx', output)
            self.assertEqual(output.read_bytes(), b'keep')


if __name__ == '__main__':
    unittest.main()
