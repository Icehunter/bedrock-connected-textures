"""Terrain add-on contracts: carrier structures, the packaged add-on, the script API and biome colours."""
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'converter'))
from biome_colors import resolve_colors
from carrier_structure import cloud_structure
from common import samples_path, terrain_pack
from terrain_providers import build

# Little-endian struct formats of the NBT number tags carrier structures use.
NUMBER_FORMATS = {1: '<b', 3: '<i', 4: '<q', 5: '<f'}
STRING_TAG = 8
LIST_TAG = 9
COMPOUND_TAG = 10


class NbtReader:
    """Reads the little-endian NBT of a Bedrock structure file."""

    def __init__(self, data):
        self.stream = io.BytesIO(data)

    def number(self, number_format):
        return struct.unpack(number_format, self.stream.read(struct.calcsize(number_format)))[0]

    def payload(self, tag):
        if tag in NUMBER_FORMATS:
            return self.number(NUMBER_FORMATS[tag])
        if tag == STRING_TAG:
            return self.stream.read(self.number('<H')).decode()
        if tag == LIST_TAG:
            item_tag = self.number('<B')
            count = self.number('<i')
            return [self.payload(item_tag) for _ in range(count)]
        if tag == COMPOUND_TAG:
            result = {}
            while True:
                item_tag = self.number('<B')
                if not item_tag:
                    return result
                name = self.payload(STRING_TAG)
                result[name] = self.payload(item_tag)
        raise AssertionError('Unexpected NBT tag')

    def remainder(self):
        return self.stream.read()


class FrameworkPackageTests(unittest.TestCase):
    def test_passive_carrier_templates_are_entity_only_and_have_no_effects(self):
        reader = NbtReader(cloud_structure('bct:grass'))
        self.assertEqual(reader.number('<B'), COMPOUND_TAG)
        self.assertEqual(reader.payload(STRING_TAG), '')
        data = reader.payload(COMPOUND_TAG)
        self.assertEqual(reader.remainder(), b'')
        self.assertEqual(data['size'], [1, 1, 1])
        self.assertEqual(data['structure']['block_indices'], [[-1], [-1]])
        entity, = data['structure']['entities']
        self.assertEqual(entity['identifier'], 'bct:grass')
        self.assertEqual(entity['mobEffects'], [])
        self.assertEqual(entity['ParticleId'], 0)
        self.assertGreater(entity['Duration'], 1000000)
        self.assertEqual(entity['RadiusPerTick'], 0)
        self.assertEqual(entity['Pos'], [.5, 0, .5])

    @unittest.skipUnless(terrain_pack().is_dir() and samples_path().is_dir(),
                         'needs BCT_TERRAIN_PACK and BEDROCK_SAMPLES')
    def test_masked_package_preserves_registration_and_links_every_texture(self):
        archive, report = build([ROOT / 'converter/data/terrain-provider.json'], 'package-test')
        self.assertFalse(report['baked_grass_palette'])
        self.assertIsNone(report['runtime_limits']['entity_cap'])
        with zipfile.ZipFile(archive) as packed:
            self.assertIsNone(packed.testzip())
            files = set(packed.namelist())
            for name in files:
                if '/entity/' not in name or not name.endswith('.entity.json'):
                    continue
                client = json.loads(packed.read(name))['minecraft:client_entity']['description']
                for path in client['textures'].values():
                    self.assertIn('Continuity_RP/' + path + '.png', files)
            self.assertFalse(any(name.endswith('.material') for name in files))
            self.assertIn('Continuity_BP/scripts/chunks.js', files)
            self.assertEqual(len([name for name in files if name.endswith('.mcstructure')]), 4)

    def test_stable_sample_api_contains_used_framework_methods(self):
        path = samples_path() / 'metadata/script_modules/@minecraft/server-bindings_2.9.0.json'
        data = json.loads(path.read_text())
        declarations = {item['name']: item for group in data.values() if isinstance(group, list)
                        for item in group if isinstance(item, dict) and 'name' in item}

        def members(name):
            return {member['name'] for key in ('functions', 'properties')
                    for member in declarations[name].get(key, [])}

        self.assertTrue({'sendScriptEvent', 'runInterval', 'runJob', 'afterEvents'}.issubset(members('System')))
        self.assertTrue({'spawnEntity', 'getEntities', 'getBlock', 'containsBlock', 'getTopmostBlock',
                         'isChunkLoaded', 'heightRange'}.issubset(members('Dimension')))
        self.assertTrue({'setProperty', 'setDynamicProperty', 'getDynamicProperty', 'isValid', 'remove',
                         'setRotation'}.issubset(members('Entity')))
        self.assertIn('scriptEventReceive', members('SystemAfterEvents'))
        self.assertTrue({'color', 'tintedColor'}.issubset(members('BlockMapColorComponent')))
        self.assertIn('getComponent', members('Block'))
        self.assertTrue({'setType', 'setPermutation', 'permutation'}.issubset(members('Block')))
        self.assertTrue({'resolve', 'getState'}.issubset(members('BlockPermutation')))
        self.assertIn('getBiome', members('Dimension'))
        self.assertIn('structureManager', members('World'))
        self.assertIn('tickingAreaManager', members('World'))
        self.assertIn('getAllTickingAreas', members('TickingAreaManager'))
        self.assertTrue({'dimension', 'boundingBox', 'identifier'}.issubset(members('TickingArea')))
        self.assertIn('place', members('StructureManager'))
        self.assertTrue({'playerPlaceBlock', 'playerBreakBlock', 'blockExplode',
                         'entityLoad'}.issubset(members('WorldAfterEvents')))

    def test_client_palette_respects_pack_colormap_and_explicit_biome_color(self):
        samples = samples_path()
        native = ROOT / 'build/native/vv-combined-256'
        colors = resolve_colors(samples, native)
        cherry = json.loads((samples / 'resource_pack/biomes/cherry_grove.client_biome.json').read_text())
        color = cherry['minecraft:client_biome']['components']['minecraft:grass_appearance']['color']
        expected = [round(value / 255, 6) for value in bytes.fromhex(color[1:])]
        self.assertEqual(colors['minecraft:cherry_grove'], expected)
        with tempfile.TemporaryDirectory() as directory:
            pack = Path(directory)
            (pack / 'textures/colormap').mkdir(parents=True)
            Image.new('RGB', (256, 256), (80, 140, 180)).save(pack / 'textures/colormap/grass.png')
            overridden = resolve_colors(samples, pack)
            self.assertEqual(overridden['minecraft:taiga'], [round(value / 255, 6) for value in (80, 140, 180)])


if __name__ == '__main__':
    unittest.main()
