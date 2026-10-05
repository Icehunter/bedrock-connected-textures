"""Sands a pack has no overlay for get edges cut from its own sand overlay, in the grass > sand > red sand > ... order."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from sand_edges import read_properties, write_sand_edge_layer

CTM = 'assets/minecraft/optifine/ctm/'
BLOCK = 'assets/minecraft/textures/block/'


def png(color, alpha=255, size=8):
    output = io.BytesIO()
    Image.new('RGBA', (size, size), color + (alpha,)).save(output, 'PNG')
    return output.getvalue()


def pack(folder, files):
    path = Path(folder) / 'pack.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('pack.mcmeta', '{"pack": {"pack_format": 88, "description": "test"}}')
        for name, data in files.items():
            archive.writestr(name, data)
    return path


class SandEdgeTests(unittest.TestCase):
    def test_missing_sands_get_the_pack_sand_edge_shape_and_their_own_surface(self):
        with tempfile.TemporaryDirectory() as folder:
            files = {CTM + '_sand/_sand.properties': b'method=overlay\ntiles=1-2\nconnectBlocks=sand\nmatchTiles=cobblestone red_sand dirt\n',
                     CTM + '_sand/1.png': png((200, 190, 150), alpha=0), CTM + '_sand/2.png': png((200, 190, 150), alpha=255),
                     BLOCK + 'sand.png': png((200, 190, 150)), BLOCK + 'red_sand.png': png((190, 100, 50)),
                     BLOCK + 'soul_sand.png': png((80, 60, 50)), BLOCK + 'red_sand_n.png': png((128, 128, 255))}
            layer = write_sand_edge_layer([pack(folder, files)], Path(folder) / 'edges.zip')
            with zipfile.ZipFile(layer) as archive:
                names = set(archive.namelist())
                red = read_properties(archive.read(CTM + '_bct_overlay_3_red_sand/_bct_overlay_3_red_sand.properties').decode())
                soul = read_properties(archive.read(CTM + '_bct_overlay_1_soul_sand/_bct_overlay_1_soul_sand.properties').decode())
                tile = Image.open(io.BytesIO(archive.read(CTM + '_bct_overlay_3_red_sand/2.png'))).convert('RGBA')
                hole = Image.open(io.BytesIO(archive.read(CTM + '_bct_overlay_3_red_sand/1.png'))).convert('RGBA')
            self.assertIn('pack.mcmeta', names)
            self.assertNotIn(CTM + '_bct_overlay_2_suspicious_sand/_bct_overlay_2_suspicious_sand.properties', names,
                             'no texture for it in the pack')
            self.assertEqual(red['connectBlocks'], 'red_sand')
            self.assertEqual(set(red['matchTiles'].split()), {'cobblestone', 'dirt', 'soul_sand'},
                             'over the sand rule targets and the sands below, never over itself or the sands above')
            self.assertNotIn('red_sand', soul['matchTiles'].split())
            self.assertEqual(tile.getpixel((0, 0)), (190, 100, 50, 255), 'red sand surface')
            self.assertEqual(hole.getpixel((0, 0))[3], 0, 'the outline of the pack sand tile')
            self.assertIn(CTM + '_bct_overlay_3_red_sand/1_n.png', names, 'with the sand LabPBR maps the pack has')

    def test_nothing_without_a_sand_overlay_or_when_the_pack_has_its_own(self):
        with tempfile.TemporaryDirectory() as folder:
            none = pack(folder, {BLOCK + 'red_sand.png': png((190, 100, 50))})
            self.assertIsNone(write_sand_edge_layer([none], Path(folder) / 'edges.zip'))


if __name__ == '__main__':
    unittest.main()
