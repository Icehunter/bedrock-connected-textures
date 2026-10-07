"""Identical block textures in a finished add-on are kept once, without any block losing its material."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from texture_dedupe import dedupe_addon

BLOCKS = 'Source_RP/textures/blocks/'


def png(color):
    output = io.BytesIO()
    Image.new('RGBA', (4, 4), color).save(output, 'PNG')
    return output.getvalue()


def texture_set(name, normal=None):
    channels = {'color': name}
    if normal:
        channels['normal'] = normal
    return json.dumps({'format_version': '1.21.30', 'minecraft:texture_set': channels}).encode()


class TextureDedupeTests(unittest.TestCase):
    def build(self, folder, files):
        path = Path(folder) / 'pack.mcaddon'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        return path

    def test_identical_maps_and_materials_are_kept_once(self):
        flat, bumpy = png((128, 128, 255, 255)), png((100, 140, 250, 255))
        files = {
            # a and b: the same colour and the same normal map (one material, written twice).
            BLOCKS + 'a.png': png((10, 20, 30, 255)), BLOCKS + 'a_normal.png': flat, BLOCKS + 'a.texture_set.json': texture_set('a', 'a_normal'),
            BLOCKS + 'b.png': png((10, 20, 30, 255)), BLOCKS + 'b_normal.png': flat, BLOCKS + 'b.texture_set.json': texture_set('b', 'b_normal'),
            # c: the same colour with another normal map: its colour must stay its own.
            BLOCKS + 'c.png': png((10, 20, 30, 255)), BLOCKS + 'c_normal.png': bumpy, BLOCKS + 'c.texture_set.json': texture_set('c', 'c_normal'),
            # d: another colour sharing a's normal map: the map is shared, the colour stays.
            BLOCKS + 'd.png': png((90, 90, 90, 255)), BLOCKS + 'd_normal.png': flat, BLOCKS + 'd.texture_set.json': texture_set('d', 'd_normal'),
            'Source_RP/textures/terrain_texture.json': json.dumps({'texture_data': {
                'a': {'textures': 'textures/blocks/a'}, 'b': {'textures': 'textures/blocks/b'},
                'c': {'textures': 'textures/blocks/c'}, 'd': {'textures': {'variations': [{'path': 'textures/blocks/d'}]}}}}).encode(),
        }
        with tempfile.TemporaryDirectory() as folder:
            addon = self.build(folder, files)
            report = dedupe_addon(addon)
            with zipfile.ZipFile(addon) as archive:
                names = set(archive.namelist())
                atlas = json.loads(archive.read('Source_RP/textures/terrain_texture.json'))['texture_data']
                d_set = json.loads(archive.read(BLOCKS + 'd.texture_set.json'))['minecraft:texture_set']
                c_set = json.loads(archive.read(BLOCKS + 'c.texture_set.json'))['minecraft:texture_set']
            self.assertEqual(atlas['b']['textures'], 'textures/blocks/a', 'b draws with a, the same material')
            self.assertEqual(atlas['c']['textures'], 'textures/blocks/c', 'c keeps its own material')
            self.assertNotIn(BLOCKS + 'b.png', names)
            self.assertNotIn(BLOCKS + 'b.texture_set.json', names)
            self.assertEqual(d_set['normal'], 'a_normal', 'd shares the identical normal map')
            self.assertNotIn(BLOCKS + 'd_normal.png', names)
            self.assertEqual(c_set['normal'], 'c_normal')
            self.assertIn(BLOCKS + 'a_normal.png', names)
            self.assertEqual(report['files_removed'], 4)

    def test_an_image_another_file_names_is_left_alone(self):
        files = {BLOCKS + 'a.png': png((1, 2, 3, 255)), BLOCKS + 'a_normal.png': png((128, 128, 255, 255)),
                 BLOCKS + 'a.texture_set.json': texture_set('a', 'a_normal'),
                 BLOCKS + 'held_normal.png': png((128, 128, 255, 255)),
                 BLOCKS + 'held.png': png((5, 5, 5, 255)), BLOCKS + 'held.texture_set.json': texture_set('held', 'held_normal'),
                 'Source_RP/attachables/held.json': json.dumps({'textures': {'normal': 'textures/blocks/held_normal'}}).encode()}
        with tempfile.TemporaryDirectory() as folder:
            addon = self.build(folder, files)
            dedupe_addon(addon)
            with zipfile.ZipFile(addon) as archive:
                self.assertIn(BLOCKS + 'held_normal.png', archive.namelist())


if __name__ == '__main__':
    unittest.main()
