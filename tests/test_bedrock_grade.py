"""--bedrock-grade learns the author's Bedrock colour grade from shared block textures and applies it to block colours only."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from bedrock_grade import grade_addon

# The author's grade: less contrast, darks lifted.
GRADE = np.array([[0.85, 0.0, 0.0], [0.0, 0.85, 0.0], [0.0, 0.0, 0.85], [10.0, 10.0, 10.0]])


def png(pixels):
    output = io.BytesIO()
    Image.fromarray(pixels.astype(np.uint8), 'RGBA').save(output, 'PNG')
    return output.getvalue()


class BedrockGradeTests(unittest.TestCase):
    def test_learns_the_grade_and_applies_it_to_block_colours_only(self):
        rng = np.random.default_rng(3)
        with tempfile.TemporaryDirectory() as folder:
            addon, bedrock = Path(folder) / 'pack.mcaddon', Path(folder) / 'author.mcpack'
            with zipfile.ZipFile(addon, 'w') as ours, zipfile.ZipFile(bedrock, 'w') as theirs:
                for index in range(60):
                    rgb = rng.integers(0, 256, (32, 32, 3)).astype(float)
                    alpha = np.full((32, 32, 1), 255.0)
                    ours.writestr(f'Source_RP/textures/blocks/block{index}.png', png(np.concatenate([rgb, alpha], 2)))
                    target = np.clip(np.c_[rgb.reshape(-1, 3), np.ones(32 * 32)] @ GRADE, 0, 255).reshape(32, 32, 3)
                    theirs.writestr(f'textures/blocks/block{index}.png', png(np.concatenate([target, alpha], 2)))
                tile = np.concatenate([np.full((8, 8, 3), 200.0), np.full((8, 8, 1), 255.0)], 2)
                ours.writestr('Source_RP/textures/blocks/bctr_tile.png', png(tile))
                ours.writestr('Source_RP/textures/blocks/block0_normal.png', png(tile))
                ours.writestr('Source_RP/textures/entity/pig.png', png(tile))
            report = grade_addon(addon, bedrock)
            self.assertTrue(report['applied'])
            self.assertLess(report['mean_error_after'], 1.0)
            with zipfile.ZipFile(addon) as result:
                pixel = lambda name: Image.open(io.BytesIO(result.read(name))).convert('RGBA').getpixel((0, 0))
                self.assertEqual(pixel('Source_RP/textures/blocks/bctr_tile.png')[:3], (180, 180, 180), 'a rule tile is graded')
                self.assertEqual(pixel('Source_RP/textures/blocks/block0_normal.png')[:3], (200, 200, 200), 'maps are not')
                self.assertEqual(pixel('Source_RP/textures/entity/pig.png')[:3], (200, 200, 200), 'only block textures')

    def test_too_few_shared_textures_change_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            addon, bedrock = Path(folder) / 'pack.mcaddon', Path(folder) / 'author.mcpack'
            with zipfile.ZipFile(addon, 'w') as ours, zipfile.ZipFile(bedrock, 'w') as theirs:
                ours.writestr('Source_RP/textures/blocks/stone.png', png(np.full((4, 4, 4), 200.0)))
                theirs.writestr('textures/blocks/stone.png', png(np.full((4, 4, 4), 100.0)))
            before = addon.read_bytes()
            self.assertFalse(grade_addon(addon, bedrock)['applied'])
            self.assertEqual(addon.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
