from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from java_block_states import states_java_to_bedrock


class BlockStateTests(unittest.TestCase):
    def test_partial_furnace_state_includes_both_native_ids(self):
        result = states_java_to_bedrock('furnace', {'facing': 'north'})
        north = {'minecraft:cardinal_direction': 'north'}
        self.assertEqual(result, [{'block': 'minecraft:furnace', 'states': north},
                                  {'block': 'minecraft:lit_furnace', 'states': north}])

    def test_barrel_partial_state_does_not_force_open_default(self):
        self.assertEqual(states_java_to_bedrock('barrel', {'facing': 'west'}),
                         [{'block': 'minecraft:barrel', 'states': {'facing_direction': 4}}])

    def test_beehive_direction_uses_its_native_enum(self):
        self.assertEqual(states_java_to_bedrock('beehive', {'facing': 'east', 'honey_level': '5'}),
                         [{'block': 'minecraft:beehive', 'states': {'direction': 3, 'honey_level': 5}}])

    def test_log_axis_and_snow_provider_are_explicit(self):
        self.assertEqual(states_java_to_bedrock('oak_log', {'axis': 'x'}),
                         [{'block': 'minecraft:oak_log', 'states': {'pillar_axis': 'x'}}])
        self.assertEqual(states_java_to_bedrock('podzol', {'snowy': 'true'}),
                         [{'block': 'minecraft:podzol', 'states': {'bct:snowy': True}}])

    def test_indistinguishable_java_state_predicate_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'cannot distinguish'):
            states_java_to_bedrock('note_block', {'note': '1'})

    def test_invalid_state_value_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'matches no reference'):
            states_java_to_bedrock('barrel', {'facing': 'sideways'})


if __name__ == '__main__':
    unittest.main()
