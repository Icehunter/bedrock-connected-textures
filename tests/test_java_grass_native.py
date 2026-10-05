import sys
from pathlib import Path
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from java_grass_native import encode_grass_side, grass_overlay_source


class NativeGrassTests(unittest.TestCase):
    def test_soil_remains_untinted_and_grass_has_a_separate_mask(self):
        soil = Image.new('RGBA', (2, 1))
        soil.putdata([(130, 91, 57, 255), (120, 83, 41, 255)])
        overlay = Image.new('RGBA', (2, 1))
        overlay.putdata([(211, 190, 177, 0), (150, 150, 150, 255)])
        encoded = encode_grass_side(soil, overlay)
        self.assertEqual(encoded.getpixel((0, 0)), (130, 91, 57, 0))
        self.assertEqual(encoded.getpixel((1, 0)), (150, 150, 150, 255))
        self.assertEqual(soil.getpixel((0, 0)), (130, 91, 57, 255))

    def test_dimension_difference_is_not_resampled(self):
        with self.assertRaisesRegex(ValueError, 'dimensions differ'):
            encode_grass_side(Image.new('RGBA', (2, 2)), Image.new('RGBA', (1, 1)))

    def test_custom_declared_mask_is_used_instead_of_filename_guess(self):
        bindings = {'baseTextureVariants': {'minecraft:grass_block': [{'faceLayers': {
            'north': [{'texture': 'assets/author/textures/my_grass_mask.png', 'tintIndex': 0}],
            'south': [{'texture': 'assets/author/textures/my_grass_mask.png', 'tintIndex': 0}]}}]}}
        self.assertEqual(grass_overlay_source(bindings), 'assets/author/textures/my_grass_mask.png')

    def test_incompatible_state_masks_are_explicit(self):
        bindings = {'baseTextureVariants': {'minecraft:grass_block': [{'faceLayers': {
            'north': [{'texture': 'mask_a.png', 'tintIndex': 0}],
            'south': [{'texture': 'mask_b.png', 'tintIndex': 0}]}}]}}
        with self.assertRaisesRegex(ValueError, 'multiple side tint masks'):
            grass_overlay_source(bindings)


if __name__ == '__main__':
    unittest.main()
