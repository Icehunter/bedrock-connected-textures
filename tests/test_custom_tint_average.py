import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))

from native_replacement import _tint_of  # noqa: E402


class CustomTintAverageTest(unittest.TestCase):
    def test_custom_palette_tint_is_the_average_of_its_biome_colours(self):
        document = {
            'customTintBlocks': ['minecraft:cobblestone'],
            'modelTintTypes': {'minecraft:cobblestone': 'custom:abc'},
            'customTints': {
                'custom:abc': {
                    'minecraft:plains': [1.0, 1.0, 1.0],
                    'minecraft:desert': [0.8, 0.6, 0.4],
                }
            },
        }
        tint = _tint_of(document, 'minecraft:cobblestone', 'minecraft:cobblestone', -1)
        self.assertAlmostEqual(tint[0], 0.9, places=5)
        self.assertAlmostEqual(tint[1], 0.8, places=5)
        self.assertAlmostEqual(tint[2], 0.7, places=5)

    def test_custom_palette_without_a_table_still_has_no_tint_method(self):
        document = {
            'customTintBlocks': ['minecraft:cobblestone'],
            'modelTintTypes': {'minecraft:cobblestone': 'custom:abc'},
            'customTints': {},
        }
        with self.assertRaisesRegex(ValueError, 'has no Bedrock tint method'):
            _tint_of(document, 'minecraft:cobblestone', 'minecraft:cobblestone', -1)


if __name__ == '__main__':
    unittest.main()
