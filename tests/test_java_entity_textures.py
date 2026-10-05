import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from java_entity_textures import (FACE_TURN, TexturePlanner, apply_rects, build_output, double_chest_rects,
                                  merge_eyes, single_chest_rects)
from java_entity_models import box_uv_faces


def png(image):
    raw = io.BytesIO()
    image.save(raw, format='PNG')
    return raw.getvalue()


def island(size, box, color=(200, 100, 50, 255)):
    image = Image.new('RGBA', size, (0, 0, 0, 0))
    pixels = np.asarray(image).copy()
    x, y, w, h = box
    pixels[y:y + h, x:x + w] = color
    return Image.fromarray(pixels)


class Stack:
    def __init__(self, files):
        self.files = files

    def read(self, path):
        return self.files[path]


TABLE = {'direct': {'entity/thing/thing': [['textures/entity/thing', 1.0]]},
         'legacy_paths': {'entity/thing_old': 'entity/thing/thing'}, 'legacy_targets': {}, 'model_texture_pairs': {}}


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.jar_path = root / 'vanilla.jar'
        with zipfile.ZipFile(self.jar_path, 'w') as jar:
            jar.writestr('assets/minecraft/textures/entity/thing/thing.png', png(island((8, 8), (0, 0, 4, 4))))
        self.samples = root / 'samples'
        (self.samples / 'textures/entity').mkdir(parents=True)
        island((8, 8), (0, 0, 4, 4), (10, 20, 30, 255)).save(self.samples / 'textures/entity/thing.png')

    def tearDown(self):
        self.folder.cleanup()

    def plan(self, files):
        with zipfile.ZipFile(self.jar_path) as jar:
            return TexturePlanner(Stack(files), jar, self.samples, TABLE).plan()

    def test_texture_with_the_vanilla_layout_replaces_the_bedrock_texture(self):
        author = island((32, 32), (0, 0, 16, 16), (90, 90, 90, 255))
        planner = self.plan({'assets/minecraft/textures/entity/thing/thing.png': png(author)})
        record = planner.records[0]
        self.assertEqual(record['status'], 'converted')
        self.assertEqual(record['targets'], ['textures/entity/thing'])
        self.assertEqual(planner.outputs['textures/entity/thing']['extension'], '.png')

    def test_texture_that_leaves_the_vanilla_islands_empty_is_reported(self):
        author = island((32, 32), (16, 16, 16, 16))
        planner = self.plan({'assets/minecraft/textures/entity/thing/thing.png': png(author)})
        self.assertEqual(planner.records[0]['status'], 'unsupported')
        self.assertIn('custom model', planner.records[0]['reason'])
        self.assertEqual(planner.outputs, {})

    def test_older_paths_map_and_the_current_path_wins(self):
        author = png(island((8, 8), (0, 0, 4, 4)))
        legacy = self.plan({'assets/minecraft/textures/entity/thing_old.png': author})
        self.assertEqual(legacy.records[0]['status'], 'converted')
        self.assertEqual(legacy.records[0]['path_kind'], 'legacy')
        both = self.plan({'assets/minecraft/textures/entity/thing_old.png': author,
                          'assets/minecraft/textures/entity/thing/thing.png': author})
        status = {record['source'].rsplit('/', 1)[-1]: record['status'] for record in both.records}
        self.assertEqual(status, {'thing_old.png': 'superseded', 'thing.png': 'converted'})

    def test_paths_no_java_version_reads_are_unused(self):
        planner = self.plan({'assets/minecraft/textures/entity/thng.png': png(island((8, 8), (0, 0, 4, 4)))})
        self.assertEqual(planner.records[0]['status'], 'unused')


def faces_image(boxes, size=(64, 64)):
    """A texture whose every box face has its own colour and a marker in its first texel."""
    pixels = np.zeros((size[1], size[0], 4), np.uint8)
    colors = {}
    index = 0
    for name, offset, dims in boxes:
        for face, (x1, y1, x2, y2) in box_uv_faces(offset, dims).items():
            index += 1
            x, y, w, h = int(min(x1, x2)), int(min(y1, y2)), int(abs(x2 - x1)), int(abs(y2 - y1))
            color = (index * 9 % 256, index * 37 % 256, index * 71 % 256, 255)
            pixels[y:y + h, x:x + w] = color
            pixels[y, x] = (255, 255, 255, 255)
            colors[(name, face)] = ((x, y, w, h), color)
    return Image.fromarray(pixels), colors


class RemapTests(unittest.TestCase):
    BOXES = (('lid', (0, 0), (14, 5, 14)), ('base', (0, 19), (14, 10, 14)), ('lock', (0, 0), (2, 4, 1)))

    def test_single_chest_faces_turn_half_a_turn_about_x(self):
        java, colors = faces_image(self.BOXES[:2])
        bedrock = np.asarray(apply_rects({'java': java}, {'java': (64, 64)}, single_chest_rects(), (64, 64)))
        for (name, face), ((x, y, w, h), _) in colors.items():
            source_face, orient = FACE_TURN[face]
            (sx, sy, sw, sh), color = colors[(name, source_face)]
            region = bedrock[y:y + h, x:x + w]
            self.assertTrue((region[..., :3] == color[:3]).sum() > 0, (name, face))
            # The marker texel moves with the face orientation.
            marker = np.argwhere((region[..., :3] == 255).all(axis=2))
            self.assertEqual(len(marker), 1, (name, face))
            expected = {'fy': (h - 1, 0), 'r180': (h - 1, w - 1)}[orient]
            self.assertEqual(tuple(marker[0]), expected, (name, face))

    def test_double_chest_joins_both_halves(self):
        halves = (('lid', (0, 0), (15, 5, 14)), ('base', (0, 19), (15, 10, 14)))
        left, left_colors = faces_image(halves)
        right, right_colors = faces_image(halves)
        right = Image.fromarray(255 - np.asarray(right))
        right_pixels = np.asarray(right)
        result = np.asarray(apply_rects({'left': left, 'right': right}, {'left': (64, 64), 'right': (64, 64)},
                                        double_chest_rects(), (128, 64)))
        self.assertEqual(result.shape[:2], (64, 128))
        # Lid top: first half from the right piece's lid bottom, second half from the left piece's.
        (x, y, w, h), _ = right_colors[('lid', 'down')]
        np.testing.assert_array_equal(result[0:14, 14:29, :3], right_pixels[y:y + h, x:x + w][::-1, :, :3])
        (x, y, w, h), _ = left_colors[('lid', 'down')]
        np.testing.assert_array_equal(result[0:14, 29:44, :3], np.asarray(left)[y:y + h, x:x + w][::-1, :, :3])

    def test_remap_keeps_the_author_resolution_and_flips_normals(self):
        java, _ = faces_image(self.BOXES[:1])
        large = java.resize((256, 256), Image.Resampling.NEAREST)
        out = apply_rects({'java': large}, {'java': (64, 64)}, single_chest_rects(), (64, 64))
        self.assertEqual(out.size, (256, 256))
        normal = Image.new('RGBA', (64, 64), (200, 60, 255, 255))
        flipped = np.asarray(apply_rects({'java': normal}, {'java': (64, 64)}, [['java', [0, 0, 4, 4], [8, 8, 4, 4], 'r180']],
                                         (64, 64), normal=True))
        np.testing.assert_array_equal(flipped[8, 8, :2], [55, 195])


class OutputTests(unittest.TestCase):
    def test_labpbr_maps_become_a_texture_set(self):
        color = Image.new('RGBA', (4, 4), (120, 80, 40, 255))
        normal = Image.new('RGBA', (4, 4), (128, 128, 255, 255))
        specular = np.zeros((4, 4, 4), np.uint8)
        specular[..., 0] = 200
        specular[..., 3] = 255
        specular[0, 0, 3] = 127
        files = {'a.png': png(color), 'a_n.png': png(normal), 'a_s.png': png(Image.fromarray(specular))}
        output = {'recipe': {'kind': 'direct', 'source': 'a.png'}, 'alpha_codes': None}
        built = build_output(output, files.get, Image.new('RGBA', (4, 4)))
        mers = np.asarray(built['mers'])
        self.assertEqual(mers.shape, (4, 4, 4))
        self.assertEqual(int(mers[0, 0, 1]), 128)        # emission 127/254
        self.assertEqual(int(mers[1, 1, 1]), 0)          # 255 means no emission
        self.assertEqual(int(mers[1, 1, 2]), 55)         # roughness 1 - 200/255
        self.assertIsNotNone(built['normal'])

    def test_eye_layer_becomes_alpha_emission_with_the_vanilla_code(self):
        body = Image.new('RGBA', (8, 4), (40, 40, 40, 255))
        eyes = island((8, 4), (2, 1, 2, 1), (255, 0, 0, 255))
        vanilla = Image.new('RGBA', (8, 4), (0, 0, 0, 255))
        vanilla.putpixel((5, 2), (255, 0, 0, 7))
        merged, mask = merge_eyes(body, eyes, vanilla)
        pixels = np.asarray(merged)
        self.assertEqual(tuple(pixels[1, 2]), (255, 0, 0, 7))
        self.assertEqual(int(pixels[0, 0, 3]), 255)
        self.assertEqual(int(mask.sum()), 2)
        files = {'b.png': png(body), 'e.png': png(eyes)}
        output = {'recipe': {'kind': 'eyes', 'source': 'b.png', 'eyes': 'e.png'}}
        built = build_output(output, files.get, vanilla)
        mers = np.asarray(built['mers'])
        self.assertEqual(int(mers[1, 2, 1]), 255)
        self.assertEqual(int(mers[0, 0, 1]), 0)

    def test_emissive_overlay_draws_into_colour_and_emission(self):
        base = Image.new('RGBA', (4, 4), (20, 20, 20, 255))
        glow = island((4, 4), (1, 1, 1, 1), (250, 240, 10, 255))
        files = {'a.png': png(base), 'a_glow.png': png(glow)}
        output = {'recipe': {'kind': 'direct', 'source': 'a.png'}, 'alpha_codes': None, 'emissive_suffix': '_glow'}
        built = build_output(output, files.get, Image.new('RGBA', (4, 4)))
        self.assertEqual(tuple(np.asarray(built['color'])[1, 1]), (250, 240, 10, 255))
        mers = np.asarray(built['mers'])
        self.assertEqual(int(mers[1, 1, 1]), 255)
        self.assertEqual(int(mers[0, 0, 1]), 0)

    def test_material_alpha_codes_stay_under_the_author_colours(self):
        author = island((8, 8), (0, 0, 8, 4), (10, 200, 10, 255))
        vanilla = Image.new('RGBA', (4, 4), (0, 0, 0, 90))
        files = {'g.png': png(author)}
        uniform = build_output({'recipe': {'kind': 'direct', 'source': 'g.png'}, 'alpha_codes': 'uniform', 'codes': [90]},
                               files.get, vanilla)
        alpha = np.asarray(uniform['color'])[..., 3]
        self.assertEqual(set(np.unique(alpha[:4]).tolist()), {90})
        self.assertEqual(set(np.unique(alpha[4:]).tolist()), {0})


if __name__ == '__main__':
    unittest.main()
