"""A Java pack's flat random top decal (clover on grass) is painted into the top's random variations."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from java_pack_api import PackStack
from top_decals import bake_top_decals, find_top_decals


def decal_model(height):
    return json.dumps({'textures': {'0': 'block/clover'}, 'elements': [
        {'from': [0, height, 0], 'to': [16, height, 16], 'faces': {'up': {'uv': [0, 0, 16, 16], 'texture': '#0'}}}]})


class TopDecalTests(unittest.TestCase):
    def test_finds_the_decal_and_its_bare_share(self):
        state = {'multipart': [{'apply': {'model': 'block/grass_block'}},
                               {'apply': [{'model': 'block/clover1', 'weight': 3}, {'model': 'block/clover2', 'y': 90},
                                          {'model': 'block/air', 'weight': 4}]}]}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'pack.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('pack.mcmeta', '{"pack": {"pack_format": 88, "description": ""}}')
                archive.writestr('assets/minecraft/blockstates/grass_block.json', json.dumps(state))
                archive.writestr('assets/minecraft/models/block/clover1.json', decal_model(16.5))
                archive.writestr('assets/minecraft/models/block/clover2.json', decal_model(16.8))
            with PackStack([path]) as stack:
                self.assertEqual(find_top_decals(stack, 'grass_block'),
                                 {'texture': 'assets/minecraft/textures/block/clover.png', 'bare': 0.5})
                self.assertIsNone(find_top_decals(stack, 'stone'))

    def test_each_top_tile_gets_bare_and_decal_versions_with_their_maps(self):
        with tempfile.TemporaryDirectory() as folder:
            materials = Path(folder)
            for name, color, alpha in (('top', (100, 80, 40), 255), ('clover', (40, 200, 40), 0)):
                Image.new('RGBA', (4, 4), color + (255,)).save(materials / f'{name}.png')
                Image.new('RGBA', (4, 4), (128, 128, 255, 255)).save(materials / f'{name}_normal.png')
                (materials / f'{name}.texture_set.json').write_text(json.dumps(
                    {'format_version': '1.21.30', 'minecraft:texture_set': {'color': name, 'normal': name + '_normal'}}))
            # The clover covers only the left half of its tile.
            clover = Image.open(materials / 'clover.png').convert('RGBA')
            for x in range(2, 4):
                for y in range(4):
                    clover.putpixel((x, y), (0, 0, 0, 0))
            clover.save(materials / 'clover.png')
            choices = bake_top_decals(materials, [['top.png', 1]], [['clover.png', 1]], 0.25, materials)
            self.assertEqual(len(choices), 4)
            self.assertEqual(choices[0], ['top.png', 1], 'one in four bare')
            baked = Image.open(materials / choices[1][0]).convert('RGBA')
            self.assertEqual(baked.getpixel((0, 0))[:3], (40, 200, 40), 'clover painted in')
            self.assertEqual(baked.getpixel((3, 0))[:3], (100, 80, 40), 'the top shows around it')
            texture_set = json.loads((materials / choices[1][0]).with_suffix('.texture_set.json').read_text())
            self.assertTrue((materials / choices[1][0]).with_name(texture_set['minecraft:texture_set']['normal'] + '.png').exists())


if __name__ == '__main__':
    unittest.main()
