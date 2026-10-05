"""Edge cutouts follow the author's art: the grass side overlay, or the material's height map."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from common import write_json
from edge_shapes import edge_alpha


class EdgeShapeTests(unittest.TestCase):
    def test_grass_edge_is_the_authors_side_overlay(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            side = np.zeros((16, 16, 4), dtype=np.uint8)
            side[:3, :, 3] = 255
            side[3, ::2, 3] = 255
            Image.fromarray(side).save(root / 'side.png')
            edge = np.asarray(edge_alpha(root, {'biome_tint': 'grass', 'edge_source': 'side.png'}))
            self.assertEqual(edge.shape, (256, 256))
            self.assertTrue((edge[:48] == 255).all())
            self.assertTrue((edge[64:] == 0).all())
            # The overlay's fourth row covers every other pixel, so its columns alternate.
            self.assertTrue((edge[48:64, ::32] == 255).all())
            self.assertTrue((edge[48:64, 16::32] == 0).all())

    def test_material_edge_follows_the_authors_height_map(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            generator = np.random.default_rng(3)
            Image.fromarray(generator.integers(0, 255, (64, 64), dtype=np.uint8)).save(root / 'heightmap.png')
            Image.new('RGBA', (64, 64), (200, 180, 120, 255)).save(root / 'color.png')
            write_json(root / 'sand.texture_set.json', {'format_version': '1.21.30',
                       'minecraft:texture_set': {'color': 'color', 'heightmap': 'heightmap'}})
            effect = {'material': 'sand', 'texture_sets': ['sand.texture_set.json']}
            edge = np.asarray(edge_alpha(root, effect)) >= 128
            self.assertTrue(edge[:25].all())
            self.assertFalse(edge[128:].any())
            depths = edge.sum(axis=0)
            self.assertGreater(depths.max() - depths.min(), 8, 'boundary should be ragged, not straight')
            # same material, same shape
            again = np.asarray(edge_alpha(root, effect)) >= 128
            self.assertTrue((edge == again).all())


if __name__ == '__main__':
    unittest.main()
