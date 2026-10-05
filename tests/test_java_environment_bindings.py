"""CTM state and biome predicates get Bedrock names only where Mojang's metadata confirms them."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from common import write_json
from java_environment_bindings import resolve_environment


class EnvironmentTests(unittest.TestCase):
    def resolve(self, text):
        class Stack:
            files = {'assets/minecraft/optifine/ctm/a.properties'}

            def read(self, path):
                return text.encode()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / 'mojang-blocks.json', {'data_items': []})
            write_json(root / 'mojang-biomes.json', {'data_items': [{'name': 'minecraft:the_end'}]})
            return resolve_environment(Stack(), root)

    def test_complete_end_union_is_explicit_and_namespace_aware(self):
        result = self.resolve(
            'biomes=!minecraft:the_end small_end_islands end_midlands end_highlands end_barrens the_void')
        self.assertEqual(result['unresolvedPredicates'], [])
        self.assertEqual(len(result['biomeUnions'][0]['source']), 6)
        self.assertEqual(result['biomeUnions'][0]['target'], ['minecraft:the_end'])

    def test_individual_end_subbiome_remains_unresolved(self):
        result = self.resolve('biomes=end_midlands')
        self.assertEqual(result['biomeUnions'], [])
        self.assertEqual(result['unresolvedPredicates'][0]['biome'], 'end_midlands')

    def test_snowy_provider_is_distinct_from_native_block_state(self):
        result = self.resolve('matchBlocks=grass_block:snowy=true')
        self.assertEqual(result['unresolvedPredicates'], [])
        self.assertEqual(result['stateNames'], {})
        self.assertEqual(result['derivedPredicates'][0]['provider'], 'bct:snowy')


if __name__ == '__main__':
    unittest.main()
