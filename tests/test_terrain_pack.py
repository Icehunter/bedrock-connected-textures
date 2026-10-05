"""Exercise pack adaptation with remapped atlases, partial inputs and hostile paths."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'converter'))
from terrain_pack import prepare, pack_folder, contained
from common import read_json, samples_path, scratch, write_json
from terrain_providers import identity
from package_converter import package
from addon_package import read_packets
from engine_package import engine_dependency


class PackAdapterTests(unittest.TestCase):
    def write_pack(self, folder):
        """A pack remapping grass and sand to its own textures, with VV maps on one grass variant."""
        write_json(folder / 'manifest.json', {'header': {'name': 'Other pack'}})
        write_json(folder / 'blocks.json', {
            'grass': {'textures': {'up': 'custom_grass'}},
            'sand': {'textures': 'custom_sand'},
        })
        write_json(folder / 'textures/terrain_texture.json', {'texture_data': {
            'custom_grass': {'textures': {'variations': [
                {'path': 'textures/custom/turf', 'weight': 2},
                {'path': 'textures/custom/turf2', 'weight': 1},
            ]}},
            'custom_sand': {'textures': 'textures/custom/dunes'},
            'suspicious_sand': {'textures': ['textures/custom/sus', 'textures/custom/sus1']},
        }})
        textures = [('turf', 16, (10, 80, 30)), ('turf2', 512, (15, 70, 20)),
                    ('dunes', 32, (170, 140, 90)), ('sus', 64, (160, 130, 80))]
        for name, size, color in textures:
            path = folder / 'textures/custom' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (size, size), color).save(str(path) + '.png')
        Image.new('RGB', (16, 16), (128, 128, 255)).save(folder / 'textures/custom/turf_normal.tga')
        Image.new('RGBA', (16, 16), (0, 0, 180, 80)).save(folder / 'textures/custom/turf_mers.png')
        write_json(folder / 'textures/custom/turf.texture_set.json', {'minecraft:texture_set': {
            'color': 'turf',
            'normal': 'turf_normal',
            'metalness_emissive_roughness_subsurface': 'turf_mers',
        }})

    def test_remap_variants_plain_color_pbr_and_fallback(self):
        with tempfile.TemporaryDirectory(dir=scratch(ROOT / 'build')) as directory:
            folder = Path(directory)
            self.write_pack(folder)
            provider, receipt = prepare(ROOT, folder, 'adapter-test')
            data = read_json(provider)
            grass = data['effects']['grass']
            self.assertEqual(len(grass['texture_sets']), 2)
            self.assertEqual(grass['texture_weights'], [2, 1])
            self.assertFalse(receipt['grass'][0]['fallback'])
            self.assertTrue(receipt['soul_sand'][0]['fallback'])
            self.assertFalse(receipt['suspicious_sand'][0]['fallback'])
            grass_set = ROOT / grass['texture_sets'][0]
            descriptor = read_json(grass_set)['minecraft:texture_set']
            self.assertEqual(set(descriptor), {'color', 'normal', 'metalness_emissive_roughness_subsurface'})
            with Image.open(grass_set.parent / 'metalness_emissive_roughness_subsurface.png') as image:
                self.assertEqual(image.getpixel((10, 10)), (0, 0, 180, 80))
            with Image.open(grass_set.parent / 'color.png') as image:
                self.assertEqual(image.size, (256, 256))
                self.assertEqual(image.getpixel((10, 10)), (10, 80, 30, 255))
            sand = read_json(ROOT / data['effects']['sand']['texture_sets'][0])['minecraft:texture_set']
            self.assertEqual(sand, {'color': 'color'})
            effects = data['effects'].values()
            self.assertEqual([effect['height_pixels'] for effect in effects], [1] * 5)
            self.assertEqual({effect['priority'] for effect in effects}, set(range(1, 6)))
            self.assertNotEqual(identity('rp-header', 'adapter-test'), identity('rp-header', 'other-pack'))

    def test_archive_and_path_guards(self):
        with tempfile.TemporaryDirectory(dir=scratch(ROOT / 'build')) as directory:
            folder = Path(directory)
            self.write_pack(folder / 'pack')
            archive_path = folder / 'pack.mcpack'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                for path in (folder / 'pack').rglob('*'):
                    if path.is_file():
                        archive.write(path, path.relative_to(folder / 'pack'))
            self.assertTrue((pack_folder(archive_path, folder / 'extracted') / 'manifest.json').exists())
            with self.assertRaises(ValueError):
                contained(folder, '../escape.png')
            with zipfile.ZipFile(folder / 'bad.zip', 'w') as archive:
                archive.writestr('../escape', 'x')
            with self.assertRaises(ValueError):
                pack_folder(folder / 'bad.zip', folder / 'rejected')

    def test_animation_and_unknown_overrides_fail_clearly(self):
        with tempfile.TemporaryDirectory(dir=scratch(ROOT / 'build')) as directory:
            folder = Path(directory)
            self.write_pack(folder)
            with self.assertRaisesRegex(ValueError, 'Unknown material'):
                prepare(ROOT, folder, 'adapter-test', {'oops': 'textures/x'})
            Image.new('RGB', (16, 32)).save(folder / 'textures/custom/dunes.png')
            with self.assertRaisesRegex(ValueError, 'square still image'):
                prepare(ROOT, folder, 'adapter-test')

    def test_packaged_converter_builds_a_terrain_pack_for_the_engine(self):
        with tempfile.TemporaryDirectory(dir=scratch(ROOT / 'build')) as directory:
            folder = Path(directory)
            self.write_pack(folder / 'pack')
            with zipfile.ZipFile(package()) as archive:
                archive.extractall(folder)
            toolkit = folder / 'Bedrock-Connected-Textures-Converter'
            command = [sys.executable, str(toolkit / 'converter/terrain_pack.py'),
                       '--resource-pack', str(folder / 'pack'), '--samples', str(samples_path()),
                       '--key', 'portable']
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            with zipfile.ZipFile(toolkit / 'dist/development/terrain-portable-0.3.1.mcaddon') as archive:
                scripts = sorted(name for name in archive.namelist() if '/scripts/' in name)
                self.assertEqual(scripts, ['Terrain_BP/scripts/main.js', 'Terrain_BP/scripts/publisher.js',
                                           'Terrain_BP/scripts/source-data.js'])
                manifest = json.loads(archive.read('Terrain_BP/manifest.json'))
                self.assertIn(engine_dependency(), manifest['dependencies'])
                provider = read_packets(archive.read('Terrain_BP/scripts/source-data.js').decode())['terrain'][0]
                self.assertEqual(len(provider['effects']), 5)
                self.assertTrue(all(effect['height_pixels'] == 1 for effect in provider['effects'].values()))
                grass = json.loads(archive.read('Terrain_BP/blocks/bct_grass_surface.json'))['minecraft:block']
                self.assertEqual(grass['components']['minecraft:material_instances']['*']['tint_method'], 'grass')
                atlas = json.loads(archive.read('Terrain_RP/textures/terrain_texture.json'))['texture_data']
                for alias in atlas.values():
                    variations = alias['textures']['variations']
                    if len(variations) == 2:
                        self.assertEqual([variation['weight'] for variation in variations], [2, 1])
                for effect in provider['effects'].values():
                    self.assertEqual(len(effect['mask_lookup']), 216)


if __name__ == '__main__':
    unittest.main()
