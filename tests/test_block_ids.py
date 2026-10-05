from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from block_ids import JAVA_RENAMES, LEGACY_KEYS, bedrock_ids, known_blocks, legacy_key_id, sanitize
from common import read_json, samples_path


class BlockIdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.known = known_blocks(samples_path())

    def test_every_rename_names_a_bedrock_block(self):
        for java, renamed in JAVA_RENAMES.items():
            for name in renamed if isinstance(renamed, list) else [renamed]:
                self.assertIn(name, self.known, java)
        for key, name in LEGACY_KEYS.items():
            self.assertIn(name, self.known, key)

    def test_java_ids_map_to_their_bedrock_blocks(self):
        expected = {'minecraft:bricks': ['minecraft:brick_block'], 'minecraft:stonebrick': ['minecraft:stone_bricks'],
                    'minecraft:grass': ['minecraft:grass_block'], 'minecraft:dirt_path': ['minecraft:grass_path'],
                    'minecraft:end_stone_bricks': ['minecraft:end_bricks'],
                    'minecraft:attached_melon_stem': ['minecraft:melon_stem'],
                    'minecraft:snow': ['minecraft:snow_layer'], 'minecraft:snow_block': ['minecraft:snow'],
                    'minecraft:flowering_azalea_leaves': ['minecraft:azalea_leaves_flowered'],
                    'minecraft:oak_button': ['minecraft:wooden_button'], 'stone': ['minecraft:stone'],
                    'minecraft:comparator': ['minecraft:unpowered_comparator', 'minecraft:powered_comparator']}
        for java, ids in expected.items():
            self.assertEqual(bedrock_ids(java, self.known), ids, java)
        self.assertEqual(bedrock_ids('minecraft:bell_floor', self.known), [],
                         'no Bedrock block: reported, never guessed')

    def test_legacy_blocks_json_keys_take_their_current_id(self):
        keys = read_json(samples_path() / 'resource_pack/blocks.json')
        self.assertEqual(legacy_key_id('grass', keys, self.known), 'minecraft:grass_block')
        self.assertEqual(legacy_key_id('seaLantern', keys, self.known), 'minecraft:sea_lantern')
        self.assertIsNone(legacy_key_id('stonebrick', keys, self.known), 'stone_bricks has its own entry')
        self.assertIsNone(legacy_key_id('deprecated_anvil', keys, self.known))
        for key in keys:
            identifier = legacy_key_id(key, keys, self.known) if isinstance(keys[key], dict) else None
            self.assertTrue(identifier is None or identifier in self.known, key)

    def test_sanitize_drops_unknown_ids_and_rules_left_without_blocks(self):
        document = {'rules': [{'id': 'a', 'blocks': ['minecraft:stone', 'minecraft:bell_floor']},
                              {'id': 'b', 'blocks': ['minecraft:bell_floor']},
                              {'id': 'c', 'blocks': [], 'matchTiles': ['x.png']}],
                    'baseTextures': {'minecraft:stone': {}, 'minecraft:deprecated_anvil': {}},
                    'fullCubeBlocks': ['minecraft:stone', 'minecraft:seaLantern']}
        dropped, removed = sanitize(document, self.known)
        self.assertEqual([rule['id'] for rule in document['rules']], ['a', 'c'])
        self.assertEqual(document['rules'][0]['blocks'], ['minecraft:stone'])
        self.assertEqual(removed, ['b'], 'an empty block list would match every block')
        self.assertEqual(sorted(dropped),
                         ['minecraft:bell_floor', 'minecraft:deprecated_anvil', 'minecraft:seaLantern'])
        self.assertEqual(list(document['baseTextures']), ['minecraft:stone'])

    def test_sanitize_keeps_biome_tint_tables(self):
        tints = {'minecraft:badlands': [144, 129, 77], 'minecraft:plains': [145, 189, 89]}
        document = {'rules': [], 'grassTints': dict(tints), 'foliageTints': dict(tints),
                    'customTints': {'custom:abc': dict(tints)}}
        dropped, _ = sanitize(document, self.known)
        self.assertEqual(dropped, {})
        self.assertEqual((document['grassTints'], document['foliageTints'], document['customTints']),
                         (tints, tints, {'custom:abc': tints}))


if __name__ == '__main__':
    unittest.main()
