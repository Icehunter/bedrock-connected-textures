"""Texture memory and block permutation counts warn before a pack is too large to load."""
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
import texture_load
from texture_load import load_report, measure


class TextureLoadTests(unittest.TestCase):
    def test_measures_images_with_mipmaps(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'textures').mkdir()
            Image.new('RGBA', (1024, 1024)).save(root / 'textures/a.png')
            Image.new('RGBA', (1024, 1024)).save(root / 'textures/b.png')
            (root / 'textures/a.texture_set.json').write_text('{}')
            self.assertEqual(measure([root]), {'images': 2, 'megapixels': 2.1, 'estimated_gpu_mib': 11})

    def test_warns_for_packs_too_large_to_load(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            Image.new('RGBA', (2048, 2048)).save(root / 'big.png')
            original = texture_load.WARN_MIB
            texture_load.WARN_MIB = 16
            try:
                report = load_report({'vv': root}, permutations=40000)
            finally:
                texture_load.WARN_MIB = original
            self.assertEqual(report['renderers']['vv']['images'], 1)
            self.assertEqual(len(report['warnings']), 2)


if __name__ == '__main__':
    unittest.main()
