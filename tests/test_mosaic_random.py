"""Repeat mosaics on never-swapped blocks draw as random tiles only when their tiles join smoothly at random."""
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from mosaic_random import kept_vanilla_patterns, only_vanilla_blocks, random_looks_authored


def write_mosaic(folder, picture, columns, rows):
    """Cut a picture into columns x rows tiles, row by row; returns their paths."""
    size = picture.shape[0] // rows
    paths = []
    for index in range(columns * rows):
        column, row = index % columns, index // columns
        tile = picture[row * size:(row + 1) * size, column * size:(column + 1) * size]
        path = Path(folder) / f'{index}.png'
        Image.fromarray(tile.astype(np.uint8)).save(path)
        paths.append(path)
    return paths


class MosaicRandomTests(unittest.TestCase):
    def test_grain_without_structure_draws_at_random(self):
        noise = np.random.default_rng(1).integers(100, 140, (64, 64, 3))
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(random_looks_authored(write_mosaic(folder, noise, 2, 2), 2, 2))

    def test_a_picture_with_large_shapes_keeps_one_tile(self):
        # A smooth gradient across the mosaic: tiles only join where the author put them side by side.
        ramp = np.linspace(0, 255, 64)
        picture = np.stack([np.add.outer(ramp, ramp) / 2] * 3, axis=-1)
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(random_looks_authored(write_mosaic(folder, picture, 4, 4), 4, 4))

    def test_only_mosaics_on_blocks_that_stay_vanilla_qualify(self):
        patterns = kept_vanilla_patterns({'keep_vanilla': [{'blocks': ['minecraft:sand', 'minecraft:*_concrete_powder'],
                                                            'reason': 'gameplay'}]})
        self.assertTrue(only_vanilla_blocks({'minecraft:sand', 'minecraft:red_concrete_powder'}, patterns))
        self.assertFalse(only_vanilla_blocks({'minecraft:sand', 'minecraft:stone'}, patterns))
        self.assertFalse(only_vanilla_blocks(set(), patterns))


if __name__ == '__main__':
    unittest.main()
