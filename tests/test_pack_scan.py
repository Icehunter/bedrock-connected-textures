"""`python bct.py check` reads the terrain atlas size and the ray tracing set-up from a resource pack."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from pack_scan import (addon_atlas_sizes, atlas_estimate, atlas_fit_width, atlas_messages, atlas_paths,  # noqa: E402
                       estimate_atlas, ray_tracing_messages)


def image(path, size, mode='RGBA'):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new(mode, size).save(path)


def atlas(folder, paths):
    folder = Path(folder) / 'textures'
    folder.mkdir(parents=True, exist_ok=True)
    data = {f'tile_{index}': {'textures': path} for index, path in enumerate(paths)}
    (folder / 'terrain_texture.json').write_text(json.dumps({'padding': 8, 'texture_data': data}))


class AtlasTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.samples, self.rp = root / 'samples', root / 'RP'
        vanilla = self.samples / 'resource_pack'
        paths = [f'textures/blocks/v{index}' for index in range(98)]
        for path in paths:
            image(vanilla / (path + '.png'), (16, 16))
        image(vanilla / 'textures/blocks/lava.png', (16, 32 * 16))  # 32 frames
        atlas(vanilla, paths + ['textures/blocks/lava'])

    def tearDown(self):
        self.folder.cleanup()

    def test_slots_are_as_wide_as_the_widest_texture_and_frames_take_a_slot_each(self):
        image(self.rp / 'textures/blocks/v0.png', (64, 64))
        image(self.rp / 'textures/blocks/mine.png', (64, 64))
        atlas(self.rp, ['textures/blocks/mine'])
        estimate = atlas_estimate(self.rp, self.samples)
        self.assertEqual(estimate['slots'], 98 + 32 + 1)
        self.assertEqual(estimate['width'], 64)
        self.assertEqual(estimate['side'], 12 * 64)
        self.assertEqual(estimate['widest'], ['textures/blocks/mine', 'textures/blocks/v0'])

    def test_a_pack_over_or_near_the_atlas_gets_a_note_since_the_game_scales_it_down(self):
        image(self.rp / 'textures/blocks/big.png', (2048, 2048))
        atlas(self.rp, ['textures/blocks/big'])
        problems, notes = atlas_messages(self.rp, self.samples)
        self.assertEqual(problems, [])
        self.assertIn('over the 16384 px the game builds', notes[0])
        self.assertIn('scales the block textures down', notes[0])
        self.assertIn('make the 2048 px textures (textures/blocks/big) smaller', notes[0])
        image(self.rp / 'textures/blocks/big.png', (1200, 1200))
        atlas(self.rp, ['textures/blocks/big'])
        problems, notes = atlas_messages(self.rp, self.samples)
        self.assertEqual(problems, [])
        self.assertIn('% of the 16384 px the game builds', notes[0])

    def test_the_fit_width_halves_until_the_atlas_holds_the_pack_and_never_grows(self):
        sizes = {texture: ((2048, 2048), True) for texture in ('a', 'b')}
        sizes.update({f'v{index}': ((16, 16), False) for index in range(129)})
        # 131 slots, a 12 by 12 grid: 2048 pixel slots make it 24576 pixels square, 1024 make it 12288.
        self.assertEqual(atlas_fit_width(sizes), 1024)
        self.assertEqual(atlas_fit_width({'a': ((64, 64), True)}), 64)

    def test_an_addon_is_read_like_a_folder(self):
        image(self.rp / 'textures/blocks/mine.png', (64, 64))
        image(self.rp / 'textures/blocks/v0.png', (64, 64))
        atlas(self.rp, ['textures/blocks/mine'])
        addon = Path(self.folder.name) / 'pack.mcaddon'
        with zipfile.ZipFile(addon, 'w') as archive:
            for path in self.rp.rglob('*'):
                if path.is_file():
                    archive.write(path, 'Source_RP/' + path.relative_to(self.rp).as_posix())
        self.assertEqual(estimate_atlas(addon_atlas_sizes(addon, self.samples)), atlas_estimate(self.rp, self.samples))

    def test_a_small_pack_says_nothing(self):
        image(self.rp / 'textures/blocks/mine.png', (16, 16))
        atlas(self.rp, ['textures/blocks/mine'])
        self.assertEqual(atlas_messages(self.rp, self.samples), ([], []))

    def test_every_way_an_entry_names_its_textures(self):
        self.assertEqual(atlas_paths({'textures': 'a'}), ['a'])
        self.assertEqual(atlas_paths({'textures': ['a', {'path': 'b'}]}), ['a', 'b'])
        self.assertEqual(atlas_paths({'textures': {'path': 'a', 'tint_color': '#ffffff'}}), ['a'])
        self.assertEqual(atlas_paths({'textures': {'variations': [{'path': 'a', 'weight': 1}]}}), ['a'])


class RayTracingTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.rp = Path(self.folder.name)
        self.blocks = self.rp / 'textures/blocks'
        image(self.blocks / 'mine.png', (4, 4))
        image(self.blocks / 'plain.png', (4, 4))
        atlas(self.rp, ['textures/blocks/mine', 'textures/blocks/plain'])
        self.manifest([])

    def tearDown(self):
        self.folder.cleanup()

    def manifest(self, capabilities):
        (self.rp / 'manifest.json').write_text(json.dumps({'format_version': 2, 'capabilities': capabilities}))

    def texture_set(self, layers):
        (self.blocks / 'mine.texture_set.json').write_text(json.dumps({'minecraft:texture_set': layers}))

    def test_no_texture_sets(self):
        self.assertEqual(ray_tracing_messages(self.rp)[1][0][:31], 'the pack has no texture sets (t')
        self.manifest(['raytraced'])
        self.assertIn('declares raytraced, but the pack has no texture sets', ray_tracing_messages(self.rp)[1][0])

    def test_ready_texture_sets_need_the_capabilities_and_flat_textures_are_named(self):
        image(self.blocks / 'mine_mer.png', (4, 4), 'RGB')
        self.texture_set({'color': 'mine', 'metalness_emissive_roughness': 'mine_mer'})
        problems, notes = ray_tracing_messages(self.rp)
        self.assertEqual(problems, [])
        self.assertIn('add "raytraced" to "capabilities"', notes[0])
        self.assertIn('with "pbr" in "capabilities"', notes[1])
        self.assertEqual(notes[2], '1 texture(s) have no texture set and look flat with ray tracing: textures/blocks/plain')
        self.manifest(['pbr', 'raytraced'])
        self.assertEqual(len(ray_tracing_messages(self.rp)[1]), 1)

    def test_broken_texture_sets_are_problems(self):
        self.texture_set({'color': 'mine', 'metalness_emissive_roughness': 'mine_mer'})
        problems, notes = ray_tracing_messages(self.rp)
        self.assertEqual(problems, ['textures/blocks/mine.texture_set.json (metalness_emissive_roughness): '
                                    'names an image that is not in the resource pack'])
        self.assertEqual(notes, [])
        image(self.blocks / 'mine_mer.png', (8, 8), 'RGB')
        self.assertIn('has layers of different sizes', ray_tracing_messages(self.rp)[0][0])
        self.texture_set({'color': 'mine', 'normal': 'mine', 'heightmap': 'mine'})
        self.assertIn('needs a color layer', ray_tracing_messages(self.rp)[0][0])


if __name__ == '__main__':
    unittest.main()
