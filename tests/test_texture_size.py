"""Oversized block textures are scaled to the pack's usual width so the game's atlas fits."""
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from texture_size import cap_block_textures


class TextureSizeTests(unittest.TestCase):
    def test_textures_wider_than_most_are_scaled_down_keeping_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            blocks = Path(folder) / 'textures/blocks'
            blocks.mkdir(parents=True)
            for name in ('stone', 'dirt', 'sand'):
                Image.new('RGBA', (64, 64)).save(blocks / (name + '.png'))
            Image.new('RGBA', (256, 256)).save(blocks / 'anvil_base.png')
            Image.new('RGBA', (256, 256)).save(blocks / 'anvil_base_normal.png')
            Image.new('RGBA', (128, 512)).save(blocks / 'lava_still.png')  # four stacked frames

            scaled = cap_block_textures(folder)

            self.assertEqual(set(scaled), {'textures/blocks/anvil_base.png', 'textures/blocks/anvil_base_normal.png',
                                           'textures/blocks/lava_still.png'})
            for name, size in (('anvil_base', (64, 64)), ('anvil_base_normal', (64, 64)),
                               ('lava_still', (64, 256)), ('stone', (64, 64))):
                with Image.open(blocks / (name + '.png')) as image:
                    self.assertEqual(image.size, size)


if __name__ == '__main__':
    unittest.main()
