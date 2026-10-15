from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from carrier_budget import apply_budget, carried, load_budget, rule_targets

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

    def chained_document(self):
        return {
            'format_version': 1,
            'baseTextures': {'minecraft:cobblestone': every_face('cobble')},
            'rules': [
                rule('pick', method='random', matchTiles=['cobble'], tiles=['a', 'b']),
                rule('mosaic', method='repeat', matchTiles=['a'], width=2, height=2, tiles=['m0', 'm1', 'm2', 'm3']),
                rule('then', method='random', matchTiles=['m0'], tiles=['x', 'y']),
                rule('lost', method='repeat', matchTiles=['nobody'], width=1, height=1, tiles=['z']),
            ],
        }

    def test_chained_rules_target_the_blocks_their_source_rules_draw(self):
        document = self.chained_document()
        by_id = {item['id']: item for item in document['rules']}
        self.assertEqual(rule_targets(by_id['mosaic'], document), {'minecraft:cobblestone'})
        self.assertEqual(rule_targets(by_id['then'], document), {'minecraft:cobblestone'}, 'chains run several steps')
        self.assertEqual(rule_targets(by_id['lost'], document), set(), 'nothing draws the matched tile')

    def test_chained_rules_never_become_atlas_variations(self):
        _, variations, _ = apply_budget(self.chained_document(), self.budget)
        self.assertEqual(sorted(variations), ['cobble'], 'only the rule on the real texture swaps the atlas')

    def grass_document(self):
        return {
            'format_version': 1,
            'baseTextures': {'minecraft:grass_block': every_face('top')},
            'rules': [
                rule('pick', method='random', matchTiles=['top'], tiles=['a0', 'b0'], weights=[1, 5]),
                rule('mosaic_a', method='repeat', matchTiles=['a0'], width=2, height=1, tiles=['a0', 'a1']),
                rule('mosaic_b', method='repeat', matchTiles=['b0'], width=3, height=1, tiles=['b0', 'b1', 'b2']),
            ],
        }

    def test_a_picked_tile_with_a_random_mosaic_swaps_in_the_whole_mosaic(self):
        document = self.grass_document()
        _, variations, _ = apply_budget(document, self.budget, random_mosaic=lambda rule, targets: True)
        weights = {tile: weight for tile, weight in variations['top']}
        self.assertEqual(sorted(weights), ['a0', 'a1', 'b0', 'b1', 'b2'])
        total = sum(weights.values())
        self.assertEqual(weights['a0'] / total, 1 / 6 / 2, 'the pick keeps its share, split over its mosaic')
        self.assertEqual(weights['b2'] / total, 5 / 6 / 3)
        self.assertEqual(weights['a0'], weights['a1'])

    def test_a_mosaic_that_does_not_look_right_at_random_keeps_the_picked_tile(self):
        document = self.grass_document()
        _, variations, _ = apply_budget(document, self.budget, random_mosaic=lambda rule, targets: rule['id'] == 'mosaic_a')
        self.assertEqual(sorted(tile for tile, _ in variations['top']), ['a0', 'a1', 'b0'])

    def test_a_pick_whose_mosaic_cannot_draw_at_random_shows_the_mosaics_first_tile(self):
        document = {
            'format_version': 1,
            'baseTextures': {'minecraft:oak_log': every_face('top')},
            'rules': [
                rule('pick', method='random', matchTiles=['top'], tiles=['sel_a', 'sel_b'], weights=[1, 5]),
                rule('mosaic_a', method='repeat', matchTiles=['sel_a'], width=2, height=1, tiles=['a0', 'a1']),
                rule('mosaic_b', method='repeat', matchTiles=['sel_b'], width=3, height=1, tiles=['b0', 'b1', 'b2']),
            ],
        }
        _, variations, _ = apply_budget(document, self.budget, random_mosaic=lambda rule, targets: False)
        self.assertEqual(variations['top'], [['a0', 1], ['b0', 5]],
                         'the selector tile is a stand-in; the mosaic it starts is what shows')

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

    def test_a_mosaic_that_needs_only_the_not_snowy_state_becomes_native_variations(self):
        snow = rule('snow', method='repeat', matchTiles=['grass_top'], width=1, height=2, tiles=['s1', 's2'], blocks=['minecraft:grass_block'], blockMatchers=[{'block': 'minecraft:grass_block', 'states': {'bct:snowy': ['true']}}])
        plain = rule('plain', method='repeat', matchTiles=['grass_top'], width=1, height=2, tiles=['g1', 'g2'], blocks=['minecraft:grass_block'], blockMatchers=[{'block': 'minecraft:grass_block', 'states': {'bct:snowy': ['false']}}])
        document = {
            'format_version': 1,
            'baseTextures': {'minecraft:grass_block': every_face('grass_top')},
            'rules': [snow, plain],
        }
        result, variations, report = apply_budget(document, self.budget, random_mosaic=lambda rule, targets: True)
        self.assertEqual(variations, {'grass_top': [['g1', 1], ['g2', 1]]})
        self.assertIn('plain', [item['rule'] for item in report['dropped_rules'] if item.get('native_variation')])
        self.assertNotIn('snow', [item['rule'] for item in report['dropped_rules'] if item.get('native_variation')])

        two_states = rule('two_states', method='repeat', matchTiles=['grass_top'], width=1, height=2, tiles=['t1', 't2'], blocks=['minecraft:grass_block'], blockMatchers=[{'block': 'minecraft:grass_block', 'states': {'bct:snowy': ['false'], 'other_state': ['x']}}])
        document = {
            'format_version': 1,
            'baseTextures': {'minecraft:grass_block': every_face('grass_top')},
            'rules': [two_states],
        }
        _, variations, _ = apply_budget(document, self.budget, random_mosaic=lambda rule, targets: True)
        self.assertEqual(variations, {})

        document = {
            'format_version': 1,
            'baseTextures': {'minecraft:grass_block': every_face('grass_top')},
            'rules': [plain],
        }
        _, variations, _ = apply_budget(document, self.budget)
        self.assertEqual(variations, {})

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
