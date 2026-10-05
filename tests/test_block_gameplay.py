import itertools
import json
from fnmatch import fnmatchcase
from fractions import Fraction
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from block_gameplay import TIER_TAGS, TOOL_ITEM_TAGS, components, loot_table, mining, tool_condition
from common import samples_path
from native_replacement import load_policy, profile_of


def total_counts(table, fortune):
    """Distribution of the dropped count for a loot table of single-item pools.

    For a pickaxe with this Fortune level.
    """
    outcomes = {0: Fraction(1)}
    for pool in table['pools']:
        conditions = pool.get('conditions', [])
        levels = [condition['enchantments'][0]['levels'] for condition in conditions if 'enchantments' in condition]
        if levels and not (levels[0]['range_min'] <= fortune <= levels[0]['range_max']):
            continue
        weights = sum(entry['weight'] for entry in pool['entries'])
        step = {}
        for entry in pool['entries']:
            count = 0
            if entry['type'] == 'item':
                count = next((function['count'] for function in entry.get('functions', [])
                              if function['function'] == 'set_count'), 1)
            step[count] = step.get(count, 0) + Fraction(entry['weight'], weights)
        merged = {}
        for (total, chance), (count, share) in itertools.product(outcomes.items(), step.items()):
            merged[total + count] = merged.get(total + count, 0) + chance * share
        outcomes = merged
    return outcomes


def java_ore_drops(fortune):
    """Java's ApplyBonusCount.ORE_DROPS for one item: 1 x (1 + max(0, random(fortune + 2) - 1))."""
    result = {}
    for roll in range(fortune + 2):
        count = 1 + max(0, roll - 1)
        result[count] = result.get(count, 0) + Fraction(1, fortune + 2)
    return result


class BlockGameplayTests(unittest.TestCase):
    def test_fortune_on_replaced_ores_matches_java(self):
        policy = load_policy()
        diamond = profile_of('minecraft:diamond_ore', policy)
        table = loot_table('minecraft:diamond_ore', diamond)
        for fortune in range(4):
            distribution = total_counts(table, fortune)
            expected = java_ore_drops(fortune) if fortune else {1: Fraction(1)}
            self.assertEqual({count: chance for count, chance in distribution.items() if chance}, expected,
                             f'Fortune {fortune}')

    def test_drops_need_the_right_tool_and_tier(self):
        policy = load_policy()
        self.assertEqual(tool_condition(profile_of('minecraft:stone', policy)),
                         {'condition': 'match_tool', 'minecraft:match_tool_filter_all': ['minecraft:is_pickaxe']})
        gold = tool_condition(profile_of('minecraft:gold_ore', policy))
        self.assertEqual(gold['minecraft:match_tool_filter_any'], TIER_TAGS['iron'])
        self.assertNotIn('minecraft:golden_tier', gold['minecraft:match_tool_filter_any'],
                         'golden tools mine like wooden ones')
        iron = tool_condition(profile_of('minecraft:iron_ore', policy))
        self.assertIn('minecraft:copper_tier', iron['minecraft:match_tool_filter_any'])
        self.assertIsNone(tool_condition(profile_of('minecraft:oak_planks', policy)), 'planks drop by hand')
        snow = loot_table('minecraft:snow', profile_of('minecraft:snow', policy))
        self.assertEqual(snow['pools'][0]['conditions'][0]['minecraft:match_tool_filter_all'], ['minecraft:is_shovel'])
        for pool in loot_table('minecraft:lapis_ore', profile_of('minecraft:lapis_ore', policy))['pools']:
            self.assertEqual(pool['conditions'][0]['minecraft:match_tool_filter_any'], TIER_TAGS['stone'],
                             'Fortune pools need the tool too')

    def test_mining_times_follow_vanilla(self):
        policy = load_policy()
        # Without the right tool vanilla takes hardness x 5 seconds; Bedrock mines custom blocks at hardness x 1.5.
        iron_pickaxe = ("q.any_tag('minecraft:is_pickaxe') && "
                        "q.any_tag('minecraft:iron_tier', 'minecraft:diamond_tier', 'minecraft:netherite_tier')")
        right_tool = {'item': {'tags': iron_pickaxe}, 'destroy_speed': 3.0}
        self.assertEqual(mining(profile_of('minecraft:diamond_ore', policy)),
                         {'seconds_to_destroy': 10.0, 'item_specific_speeds': [right_tool]})
        self.assertEqual(mining(profile_of('minecraft:oak_log', policy)), {'seconds_to_destroy': 2})
        leaves = next(item for item in policy['model_blocks'] if item['behavior'] == 'leaves')
        speeds = mining(leaves, leaves['item_speeds'])['item_specific_speeds']
        self.assertEqual(speeds[0], {'item': 'minecraft:shears', 'destroy_speed': round(0.2 / 15, 6)},
                         'shears cut leaves 15 times faster')
        self.assertEqual(speeds[1]['destroy_speed'], round(0.2 / 1.5, 6))

    def test_see_through_blocks_do_not_conduct_redstone(self):
        policy = load_policy()
        glass = components(profile_of('minecraft:glass', policy), transparent=True)
        self.assertEqual(glass['minecraft:redstone_conductivity'],
                         {'redstone_conductor': False, 'allows_wire_to_step_down': False})
        self.assertEqual(glass['minecraft:instrument_sound'], {'up': 'note.hat'})
        planks = components(profile_of('minecraft:oak_planks', policy), transparent=False)
        self.assertEqual(planks['minecraft:redstone_conductivity'],
                         {'redstone_conductor': True, 'allows_wire_to_step_down': True})
        self.assertEqual(planks['minecraft:flammable'],
                         {'catch_chance_modifier': 5, 'destroy_chance_modifier': 20, 'lava_flammable': 'always'})
        crimson = components(profile_of('minecraft:crimson_planks', policy), transparent=False)
        self.assertNotIn('minecraft:flammable', crimson, 'nether wood does not burn')

    def test_policy_names_real_blocks_items_and_instruments(self):
        samples = samples_path()
        modules = samples / 'metadata/vanilladata_modules'
        blocks = {item['name'] for item in json.loads((modules / 'mojang-blocks.json').read_text())['data_items']}
        items = {item['name'] for item in json.loads((modules / 'mojang-items.json').read_text())['data_items']}
        instrument_schema = samples / 'metadata/json_schemas/server/block/1.26.20/Instrument.json'
        instruments = json.loads(instrument_schema.read_text())['enum']
        policy = load_policy()
        for section in ('profiles', 'keep_vanilla', 'carrier_fallback', 'model_blocks'):
            for entry in policy[section]:
                for pattern in entry['blocks']:
                    self.assertTrue(any(fnmatchcase(block, pattern) for block in blocks),
                                    f'{section}: {pattern} names no Bedrock block')
        for profile in policy['profiles']:
            for drop in profile['loot'] if isinstance(profile['loot'], list) else []:
                self.assertIn(drop['item'], items)
            if profile.get('requires_tool'):
                self.assertIn(profile['tool'], TOOL_ITEM_TAGS)
            if 'instrument' in profile:
                self.assertIn(profile['instrument'], instruments)


if __name__ == '__main__':
    unittest.main()
