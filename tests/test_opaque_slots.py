import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from convert_java_author_pack import fill_opaque_slots


class OpaqueSlotTests(unittest.TestCase):
    def test_opaque_leaf_slot_uses_the_authors_texture_and_maps(self):
        with tempfile.TemporaryDirectory() as folder:
            rp = Path(folder)
            blocks = rp / 'textures/blocks'; blocks.mkdir(parents=True)
            Image.new('RGBA', (4, 4), (10, 200, 30, 128)).save(blocks / 'leaves_oak.png')
            Image.new('RGB', (4, 4), (128, 128, 255)).save(blocks / 'leaves_oak_normal.png')
            Image.new('RGBA', (4, 4), (0, 0, 200, 40)).save(blocks / 'leaves_oak_mers.png')
            (blocks / 'leaves_oak.texture_set.json').write_text(json.dumps({'format_version': '1.21.30', 'minecraft:texture_set': {
                'color': 'leaves_oak', 'normal': 'leaves_oak_normal', 'metalness_emissive_roughness_subsurface': 'leaves_oak_mers'}}))
            Image.new('RGBA', (4, 4), (1, 2, 3, 255)).save(blocks / 'stone_opaque.png')  # authored: left alone
            atlas = {'oak_leaves': {'textures': ['textures/blocks/leaves_oak', 'textures/blocks/leaves_oak_opaque']},
                     'stone': {'textures': ['textures/blocks/stone_opaque']},
                     'spruce_leaves': {'textures': ['textures/blocks/leaves_spruce', 'textures/blocks/leaves_spruce_opaque']}}
            filled = fill_opaque_slots(rp, atlas)
            self.assertEqual(filled, ['textures/blocks/leaves_oak_opaque'])  # spruce has no authored base
            # drawn without transparency: visible leaf pixels keep their colour, gaps get a dark shade
            Image.new('RGBA', (4, 4), (10, 200, 30, 255)).save(blocks / 'leaves_oak.png')
            (blocks / 'leaves_oak_opaque.png').unlink(); (blocks / 'leaves_oak_opaque.texture_set.json').unlink()
            fill_opaque_slots(rp, atlas)
            self.assertEqual(Image.open(blocks / 'leaves_oak_opaque.png').getpixel((0, 0)), (10, 200, 30, 255))
            gap = Image.new('RGBA', (2, 1), (250, 250, 250, 0)); gap.putpixel((0, 0), (100, 50, 20, 255)); gap.save(blocks / 'leaves_oak.png')
            (blocks / 'leaves_oak_opaque.png').unlink(); (blocks / 'leaves_oak_opaque.texture_set.json').unlink()
            fill_opaque_slots(rp, atlas)
            self.assertEqual(Image.open(blocks / 'leaves_oak_opaque.png').getpixel((1, 0)), (35, 17, 7, 255))
            descriptor = json.loads((blocks / 'leaves_oak_opaque.texture_set.json').read_text())['minecraft:texture_set']
            self.assertEqual(descriptor, {'color': 'leaves_oak_opaque', 'normal': 'leaves_oak_opaque_normal',
                                          'metalness_emissive_roughness_subsurface': 'leaves_oak_opaque_mers'})
            for name in descriptor.values():
                self.assertTrue((blocks / (name + '.png')).exists())
            self.assertEqual(Image.open(blocks / 'stone_opaque.png').getpixel((0, 0)), (1, 2, 3, 255))
            self.assertEqual(fill_opaque_slots(rp, atlas), [])


if __name__ == '__main__':
    unittest.main()
