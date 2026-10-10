"""Oversized block textures are scaled to the pack's usual width so the game's atlas fits."""
from pathlib import Path
import io
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from texture_size import cap_addon_block_textures, cap_block_textures


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


class AddonTextureSizeTests(unittest.TestCase):
    def test_block_textures_and_the_named_atlas_textures_are_scaled_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as folder:
            addon = Path(folder) / 'pack.mcaddon'
            members = {'Source_RP/textures/blocks/stone.png': (512, 512),
                       'Source_RP/textures/blocks/water.png': (512, 512 * 4),
                       'Source_RP/textures/environment/destroy_stage_0.png': (512, 512),
                       'Source_RP/textures/entity/pig.png': (512, 512),
                       'Source_RP/textures/blocks/small.png': (64, 64)}
            with zipfile.ZipFile(addon, 'w') as archive:
                for name, size in members.items():
                    buffer = io.BytesIO()
                    Image.new('RGBA', size).save(buffer, 'PNG')
                    archive.writestr(name, buffer.getvalue())

            scaled = cap_addon_block_textures(addon, 256, also=['textures/environment/destroy_stage_0'])

            self.assertEqual(scaled, 3)
            with zipfile.ZipFile(addon) as archive:
                sizes = {name: Image.open(io.BytesIO(archive.read(name))).size for name in members}
            self.assertEqual(sizes['Source_RP/textures/blocks/stone.png'], (256, 256))
            self.assertEqual(sizes['Source_RP/textures/blocks/water.png'], (256, 1024), 'frames keep their count')
            self.assertEqual(sizes['Source_RP/textures/environment/destroy_stage_0.png'], (256, 256))
            self.assertEqual(sizes['Source_RP/textures/entity/pig.png'], (512, 512), 'not in the terrain atlas')
            self.assertEqual(sizes['Source_RP/textures/blocks/small.png'], (64, 64), 'never scaled up')


if __name__ == '__main__':
    unittest.main()
