"""Compare compact quadrant selection with the independent Java formula."""
import sys
from pathlib import Path
import unittest
import tempfile
import json
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from compact_tiles import expand_images, quadrant_tiles, compile_materials
from terrain_providers import MASKS


class CompactTests(unittest.TestCase):
    def test_animated_compact_preserves_frame_order_alpha_and_material_phase(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.mkdir()
            for index in range(5):
                color = Image.new('RGBA', (4, 8), (index * 20, 10, 20, 200))
                color.paste((index * 20 + 100, 10, 20, 40), (0, 4, 4, 8))
                color.save(source / f'{index}.png')
                animation = {'animation': {'frametime': 2, 'interpolate': True}}
                (source / f'{index}.png.mcmeta').write_text(json.dumps(animation))
                material = Image.new('RGBA', (4, 8), (20, 30, 40, 10))
                material.paste((40, 50, 60, 90), (0, 4, 4, 8))
                material.save(source / f'{index}_mers.png')
                (source / f'{index}.texture_set.json').write_text(json.dumps({'minecraft:texture_set': {
                    'color': str(index), 'metalness_emissive_roughness_subsurface': f'{index}_mers'}}))
            before = {path.name: path.read_bytes() for path in source.iterdir()}
            tiles = [str(index) + '.png' for index in range(5)]
            names = compile_materials(source, tiles, root / 'output', 'animated', {0: 1})
            path = root / 'output' / (names[0] + '.png')
            with Image.open(path) as image:
                self.assertEqual(image.size, (4, 16))
                self.assertEqual([image.getpixel((0, row * 4)) for row in range(4)],
                                 [(20, 10, 20, 200), (70, 10, 20, 200), (120, 10, 20, 40), (70, 10, 20, 40)])
            with Image.open(path.with_name('0_mers.png')) as image:
                self.assertEqual([image.getpixel((0, row * 4))[3] for row in range(4)], [10, 50, 90, 50])
            metadata = json.loads(Path(str(path) + '.mcmeta').read_text())['animation']
            self.assertEqual(metadata, {'frametime': 1, 'frames': [0, 1, 2, 3], 'interpolate': False})
            self.assertEqual(before, {path.name: path.read_bytes() for path in source.iterdir()})

    def test_all_neighborhoods_match_java_quadrants(self):
        for mask in MASKS:
            expected = []
            for edge1, edge2, corner, quadrant in [(3, 0, 3, 0), (0, 1, 0, 3), (2, 3, 2, 1), (1, 2, 1, 2)]:
                first, second = bool(mask & (1 << edge1)), bool(mask & (1 << edge2))
                if first and second:
                    tile = 1 if mask & (1 << (corner + 4)) else 4
                elif first:
                    tile = 3 - quadrant % 2
                elif second:
                    tile = 2 + quadrant % 2
                else:
                    tile = 0
                expected.append(tile)
            self.assertEqual(quadrant_tiles(mask), expected)

    def test_expansion_preserves_pixels_alpha_and_special_case(self):
        images = [Image.new('RGBA', (8, 8), (index * 40, 1, 2, index * 50)) for index in range(5)]
        expanded = expand_images(images, {0: 3})
        self.assertEqual(len(expanded), 47)
        self.assertEqual(expanded[0].tobytes(), images[3].tobytes())
        self.assertEqual(expanded[26].tobytes(), images[1].tobytes())


if __name__ == '__main__':
    unittest.main()
