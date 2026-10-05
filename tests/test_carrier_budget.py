from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from carrier_budget import apply_budget, carried, load_budget

FACES = ['north', 'east', 'south', 'west', 'up', 'down']
BUILDING_BLOCKS = (
    'minecraft:glass', 'minecraft:red_stained_glass', 'minecraft:glass_pane', 'minecraft:oak_planks',
    'minecraft:bookshelf', 'minecraft:brick_block', 'minecraft:sandstone', 'minecraft:red_sandstone',
)
TERRAIN_BLOCKS = (
    'minecraft:stone', 'minecraft:deepslate', 'minecraft:dirt', 'minecraft:grass_block', 'minecraft:sand',
    'minecraft:gravel', 'minecraft:oak_log', 'minecraft:oak_leaves', 'minecraft:white_wool',
    'minecraft:white_carpet', 'minecraft:white_terracotta', 'minecraft:white_concrete', 'minecraft:iron_ore',
    'minecraft:unknown_modded_block',
)


def rule(rule_id, **fields):
    return {'id': rule_id, 'method': 'ctm', 'blocks': [], 'faces': FACES, 'tiles': ['t'], **fields}


def every_face(texture):
    return {face: texture for face in FACES}


class CarrierBudgetTests(unittest.TestCase):
    budget = load_budget()

    def test_default_allowlist_keeps_building_blocks_and_drops_terrain(self):
        for block in BUILDING_BLOCKS:
            self.assertTrue(carried(block, self.budget), block)
        for block in TERRAIN_BLOCKS:
            self.assertFalse(carried(block, self.budget), block)

    def test_rules_keep_only_allowlisted_targets(self):
        document = {
            'format_version': 1,
            'rules': [
                rule('glass', blocks=['minecraft:glass', 'minecraft:stone']),
                rule('stone', blocks=['minecraft:stone']),
            ],
            'sourceBlocks': ['minecraft:glass', 'minecraft:dirt'],
        }
        result, variations, report = apply_budget(document, self.budget)
        self.assertEqual([item['id'] for item in result['rules']], ['glass'])
        self.assertEqual(result['rules'][0]['blocks'], ['minecraft:glass'])
        self.assertEqual(result['sourceBlocks'], ['minecraft:glass'])
        self.assertEqual(report['native_rules'], 1)
        self.assertEqual(report['dropped_rules'][0]['native_only'], ['minecraft:stone'])
        self.assertEqual(variations, {})
        self.assertEqual(document['rules'][0]['blocks'], ['minecraft:glass', 'minecraft:stone'],
                         'input is not modified')

    def test_texture_rules_resolve_targets_through_base_textures(self):
        document = {
            'format_version': 1,
            'baseTextures': {'minecraft:oak_planks': every_face('planks'), 'minecraft:oak_log': every_face('planks')},
            'rules': [rule('planks', matchTiles=['planks'])],
        }
        result, _, _ = apply_budget(document, self.budget)
        self.assertEqual(result['rules'][0]['blocks'], ['minecraft:oak_planks'])

    def test_unconditional_random_terrain_becomes_native_variations(self):
        plains = {'ids': ['minecraft:plains'], 'exclude': False}
        document = {
            'format_version': 1,
            'baseTextures': {'minecraft:stone': every_face('stone')},
            'rules': [
                rule('stone', method='random', matchTiles=['stone'], tiles=['a', 'b', 'c'], weights=[3, 0, 1]),
                rule('dirt', method='random', blocks=['minecraft:dirt'], tiles=['d'], biomes=plains),
            ],
        }
        result, variations, report = apply_budget(document, self.budget)
        self.assertEqual(result['rules'], [])
        self.assertEqual(variations, {'stone': [['a', 3], ['c', 1]]})
        self.assertEqual(report['native_variations'], 1)

    def test_a_pack_over_the_type_budget_is_reported(self):
        budget = {**self.budget, 'carrier_types': 1}
        document = {'format_version': 1,
                    'rules': [rule('a', blocks=['minecraft:glass']), rule('b', blocks=['minecraft:oak_planks'])]}
        _, _, report = apply_budget(document, budget)
        self.assertTrue(report['over_budget'])
        self.assertIn('exceed the budget of 1', report['warning'])
        _, _, report = apply_budget(document, self.budget)
        self.assertFalse(report['over_budget'])
        self.assertNotIn('warning', report)

    def test_model_variants_of_terrain_blocks_are_not_carried(self):
        document = {
            'format_version': 1,
            'rules': [],
            'baseTextureVariants': {'minecraft:short_grass': [{'states': {}}],
                                    'minecraft:glass_pane': [{'states': {}}]},
            'customTintBlocks': ['minecraft:oak_leaves', 'minecraft:glass'],
        }
        result, _, _ = apply_budget(document, self.budget)
        self.assertEqual(list(result['baseTextureVariants']), ['minecraft:glass_pane'])
        self.assertEqual(result['customTintBlocks'], ['minecraft:glass'])


if __name__ == '__main__':
    unittest.main()
