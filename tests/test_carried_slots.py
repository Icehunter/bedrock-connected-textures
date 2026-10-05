"""Held and inventory textures are made from the author's art with Java's item tints."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from carried_slots import carried_tints, fill_carried_slots, grass_color, item_tint


def png(color, size=(256, 256)):
    buffer = io.BytesIO()
    Image.new('RGB' if len(color) == 3 else 'RGBA', size, color).save(buffer, 'PNG')
    return buffer.getvalue()


class CarriedSlotTests(unittest.TestCase):
    def test_item_tints_follow_java_item_models(self):
        files = {
            'assets/minecraft/items/oak_leaves.json': json.dumps(
                {'model': {'tints': [{'type': 'minecraft:constant', 'value': -12012264}]}}).encode(),
            'assets/minecraft/items/grass_block.json': json.dumps(
                {'model': {'tints': [{'type': 'minecraft:grass', 'temperature': 0.5, 'downfall': 1.0}]}}).encode(),
            'assets/minecraft/items/seagrass.json': json.dumps({'model': {'type': 'minecraft:model'}}).encode(),
        }
        colormap = Image.new('RGB', (256, 256), (0, 0, 0))
        colormap.putpixel((127, 127), (145, 189, 89))
        buffer = io.BytesIO()
        colormap.save(buffer, 'PNG')
        files['assets/minecraft/textures/colormap/grass.png'] = buffer.getvalue()
        read = files.get
        self.assertEqual(item_tint('oak_leaves', read), (0x48, 0xB5, 0x18))
        self.assertEqual(item_tint('grass_block', read), (145, 189, 89))
        self.assertIsNone(item_tint('seagrass', read))
        self.assertEqual(carried_tints(read)['textures/blocks/leaves_oak_carried'], [0x48, 0xB5, 0x18])

    def test_grass_colour_map_lookup_matches_java(self):
        colormap = Image.new('RGB', (256, 256))
        colormap.putpixel((127, 127), (1, 2, 3))
        colormap.putpixel((0, 255), (9, 9, 9))
        self.assertEqual(grass_color(colormap, 0.5, 1.0), (1, 2, 3))
        self.assertEqual(grass_color(colormap, 1.0, 0.0), (9, 9, 9))

    def test_missing_carried_texture_is_the_authors_tinted_texture(self):
        with tempfile.TemporaryDirectory() as folder:
            rp = Path(folder)
            blocks = rp / 'textures/blocks'
            blocks.mkdir(parents=True)
            Image.new('RGBA', (8, 8), (200, 200, 200, 128)).save(blocks / 'leaves_oak.png')
            Image.new('RGB', (8, 8), (128, 128, 255)).save(blocks / 'leaves_oak_normal.png')
            texture_set = {'minecraft:texture_set': {'color': 'leaves_oak', 'normal': 'leaves_oak_normal'}}
            (blocks / 'leaves_oak.texture_set.json').write_text(json.dumps(texture_set))
            Image.new('RGBA', (8, 8), (5, 5, 5, 255)).save(blocks / 'vine_carried.png')  # authored: left alone
            Image.new('RGBA', (8, 8), (90, 90, 90, 255)).save(blocks / 'vine.png')
            tints = {'textures/blocks/leaves_oak_carried': [0x48, 0xB5, 0x18],
                     'textures/blocks/vine_carried': [1, 1, 1],
                     'textures/blocks/leaves_spruce_carried': [1, 2, 3]}
            filled = fill_carried_slots(rp, tints)
            self.assertEqual(filled, ['textures/blocks/leaves_oak_carried'])  # vine authored, spruce has no base
            pixel = Image.open(blocks / 'leaves_oak_carried.png').getpixel((0, 0))
            self.assertEqual(pixel, (200 * 0x48 // 255, 200 * 0xB5 // 255, 200 * 0x18 // 255, 128))
            carried_set = json.loads((blocks / 'leaves_oak_carried.texture_set.json').read_text())
            descriptor = carried_set['minecraft:texture_set']
            self.assertEqual(descriptor, {'color': 'leaves_oak_carried', 'normal': 'leaves_oak_carried_normal'})
            self.assertTrue((blocks / 'leaves_oak_carried_normal.png').exists())
            self.assertEqual(Image.open(blocks / 'vine_carried.png').getpixel((0, 0)), (5, 5, 5, 255))


if __name__ == '__main__':
    unittest.main()
