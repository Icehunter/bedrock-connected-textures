"""Java sprite animations become Bedrock flipbooks with Java's timing, frame filtering and blending."""
from pathlib import Path
import sys
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from java_texture_animation import parse_animation, compile_flipbook, compile_family, uv_animation_xy


class AnimationTests(unittest.TestCase):
    def test_java_compatibility_removes_out_of_image_indices_without_inventing_frames(self):
        metadata = {'animation': {'frames': list(range(32)), 'frametime': 3}}
        diagnostics = []
        animation = parse_animation((4, 16), metadata, invalid_frames='java', diagnostics=diagnostics)
        self.assertEqual(animation.frames, ((0, 3), (1, 3), (2, 3), (3, 3)))
        removed = [entry for entry in diagnostics if entry['code'] == 'invalid_frame_index']
        self.assertEqual([entry['index'] for entry in removed], list(range(4, 32)))
        self.assertEqual(metadata['animation']['frames'], list(range(32)))
        with self.assertRaises(ValueError):
            parse_animation((4, 16), metadata)

    def test_java_empty_filtered_and_single_frame_lists_use_static_image_origin(self):
        for sequence in ([], [99], [2], [99, 2]):
            diagnostics = []
            animation = parse_animation((4, 16), {'animation': {'frames': sequence, 'interpolate': True}},
                                        invalid_frames='java', diagnostics=diagnostics)
            self.assertEqual(animation.frames, ((0, 1),))
            self.assertFalse(animation.interpolate)
            self.assertEqual(diagnostics[-1]['code'], 'java_static_sprite')
        absent = parse_animation((4, 16), {'animation': {}}, invalid_frames='java')
        self.assertEqual(absent.frames, ((0, 1), (1, 1), (2, 1), (3, 1)))

    def test_java_codec_invalid_values_still_fail(self):
        for data in ({'frames': [-1]}, {'frames': [{'index': 0, 'time': 0}]}, {'frames': [True]},
                     {'frametime': 0}, {'interpolate': 'true'}):
            with self.assertRaises(ValueError):
                parse_animation((4, 16), {'animation': data}, invalid_frames='java')
        diagnostics = []
        animation = parse_animation((4, 16), {'animation': {'future_field': 4}},
                                    invalid_frames='java', diagnostics=diagnostics)
        self.assertEqual(len(animation.frames), 4)
        self.assertEqual(diagnostics, [{'code': 'ignored_animation_fields', 'fields': ['future_field']}])

    def test_entity_grid_preserves_frames_that_exceed_vertical_strip_budget(self):
        image = Image.new('RGBA', (2, 4))
        image.paste((10, 20, 30, 17), (0, 0, 2, 2))
        image.paste((110, 120, 130, 210), (0, 2, 2, 4))
        animation = parse_animation(image.size, {'animation': {'frametime': 4, 'interpolate': True}})
        with self.assertRaisesRegex(ValueError, 'texture-size budget'):
            compile_family({'color': image}, animation, 'textures/test', 'test', max_side=8)
        images, entry = compile_family({'color': image}, animation, 'textures/test', 'test', max_side=8, layout='grid')
        self.assertEqual(images['color'].size, (6, 6))
        self.assertEqual(entry['frames'], list(range(8)))
        self.assertEqual(images['color'].getpixel((2, 2)), (110, 120, 130, 210))
        self.assertEqual(images['color'].getpixel((4, 2)), (85, 95, 105, 210))
        self.assertEqual(uv_animation_xy(entry)['scale'], [1 / 3, 1 / 3])
        self.assertIn('q.time_stamp', uv_animation_xy(entry)['offset'][0])

    def test_padded_entity_uv_samples_only_authored_inner_frame(self):
        entry = {'frames': [0, 1], 'ticks_per_frame': 1, 'columns': 2, 'rows': 1, 'padding': 32,
                 'cell_width': 320, 'cell_height': 320, 'pixel_width': 640, 'pixel_height': 320}
        transform = uv_animation_xy(entry)
        self.assertEqual(transform['scale'], [256 / 640, 256 / 320])
        self.assertIn('0.05', transform['offset'][0])
        self.assertEqual(transform['offset'][1], '0.1')

    def test_grid_layout_reordered_frames_and_unequal_duration(self):
        image = Image.new('RGBA', (4, 4))
        for index, color in enumerate([(10, 0, 0, 255), (20, 0, 0, 255), (30, 0, 0, 255), (40, 0, 0, 255)]):
            image.paste(color, ((index % 2) * 2, (index // 2) * 2, (index % 2 + 1) * 2, (index // 2 + 1) * 2))
        frames = [3, {'index': 0, 'time': 4}, 1]
        metadata = {'animation': {'width': 2, 'height': 2, 'frametime': 2, 'frames': frames}}
        animation = parse_animation(image.size, metadata)
        strip, entry = compile_flipbook(image, animation, 'textures/test', 'test')
        self.assertEqual(entry['ticks_per_frame'], 2)
        self.assertEqual(entry['frames'], [0, 1, 1, 2])
        self.assertEqual([strip.getpixel((0, y))[0] for y in (0, 2, 4)], [40, 10, 20])
        self.assertEqual(animation.ticks, 8)

    def test_interpolation_preserves_current_alpha_and_loop_boundary(self):
        image = Image.new('RGBA', (2, 4))
        image.paste((0, 10, 20, 50), (0, 0, 2, 2))
        image.paste((100, 110, 120, 200), (0, 2, 2, 4))
        animation = parse_animation(image.size, {'animation': {'frametime': 2, 'interpolate': True}})
        strip, entry = compile_flipbook(image, animation, 'textures/test', 'test')
        expected = [(0, 10, 20, 50), (50, 60, 70, 50), (100, 110, 120, 200), (50, 60, 70, 200)]
        self.assertEqual([strip.getpixel((0, y)) for y in (0, 2, 4, 6)], expected)
        self.assertEqual(entry['ticks_per_frame'], 1)
        self.assertFalse(entry['blend_frames'])

    def test_material_channels_share_indices_even_with_constant_color(self):
        color = Image.new('RGBA', (2, 4), (50, 60, 70, 255))
        normal = Image.new('RGB', (2, 4))
        normal.paste((128, 128, 255), (0, 0, 2, 2))
        normal.paste((200, 128, 200), (0, 2, 2, 4))
        animation = parse_animation(color.size, {'animation': {}})
        still_mers = Image.new('RGBA', (2, 2), (0, 0, 255, 0))
        strips, entry = compile_family({'color': color, 'normal': normal, 'mers': still_mers}, animation,
                                       'textures/test', 'test')
        self.assertEqual(entry['frames'], [0, 1])
        self.assertTrue(all(strip.size == (2, 4) for strip in strips.values()))
        self.assertEqual(strips['normal'].getpixel((0, 2))[:3], (200, 128, 200))

    def test_invalid_metadata_and_size_budget_fail_explicitly(self):
        for metadata in ({'frames': [2]}, {'frametime': 0}, {'interpolate': 'true'}, {'width': 3}, {'frames': [True]}):
            with self.assertRaises(ValueError):
                parse_animation((2, 4), {'animation': metadata})
        image = Image.new('RGBA', (2, 4))
        image.paste((1, 2, 3, 255), (0, 2, 2, 4))
        animation = parse_animation(image.size, {'animation': {}})
        with self.assertRaisesRegex(ValueError, 'no downsampling'):
            compile_family({'color': image}, animation, 'textures/test', 'test', max_side=2)

    def test_independent_material_timelines_remain_synchronized(self):
        color = Image.new('RGBA', (2, 4), (10, 20, 30, 255))
        color.paste((100, 20, 30, 255), (0, 2, 2, 4))
        normal = Image.new('RGB', (2, 6), (128, 128, 255))
        normal.paste((200, 128, 200), (0, 2, 2, 4))
        normal.paste((40, 128, 200), (0, 4, 2, 6))
        animation = parse_animation(color.size, {'animation': {}})
        own = parse_animation(normal.size, {'animation': {}})
        strips, entry = compile_family({'color': color, 'normal': normal}, animation,
                                       'textures/test', 'test', channel_animations={'normal': own})
        self.assertEqual(len(entry['frames']), 6)
        colors = [strips['color'].getpixel((0, row * 2))[0] for row in entry['frames']]
        normals = [strips['normal'].getpixel((0, row * 2))[0] for row in entry['frames']]
        self.assertEqual(colors, [10, 100, 10, 100, 10, 100])
        self.assertEqual(normals, [128, 200, 40, 128, 200, 40])


if __name__ == '__main__':
    unittest.main()
