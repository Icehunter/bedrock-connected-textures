import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from carrier_atlas import ATLAS_METADATA, atlas_uv, build_texture_atlas, consolidate_carrier_atlases


def save_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document))


def read_json(path):
    return json.loads(path.read_text())


def texture_set(root, texture):
    return read_json(root / (texture + '.texture_set.json'))['minecraft:texture_set']


class CarrierAtlasTests(unittest.TestCase):
    def write_texture(self, root, name, color, normal='normal', mers='mers'):
        """An 8x8 colour image with distinct corner texels, plus normal and MERS channel images."""
        directory = root / 'textures/entity'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (name + '.png')
        image = Image.new('RGBA', (8, 8), color)
        image.putpixel((0, 0), (3, 6, 9, 0))
        image.putpixel((7, 7), (60, 90, 120, 127))
        image.save(path)
        channel_images = ((normal, 'RGB', (64, 128, 244)), (mers, 'RGBA', (25, 30, 200, 42)))
        for filename, mode, value in channel_images:
            Image.new(mode, (8, 8), value).save(directory / (filename + '.png'))
        save_json(path.with_suffix('.texture_set.json'), {
            'format_version': '1.21.30',
            'minecraft:texture_set': {'color': name, 'normal': normal,
                                      'metalness_emissive_roughness_subsurface': mers},
        })
        return 'textures/entity/' + name

    def write_carrier_pack(self, root, animated=False):
        """One carrier client whose controller picks one of four textures from an array."""
        save_json(root / 'manifest.json', {'modules': [{'type': 'resources'}]})
        textures = [self.write_texture(root, 'tile' + str(i), (i * 40, 100, 130, 255)) for i in range(4)]
        description = {
            'identifier': 'bct:test',
            'materials': {'default': 'bct_bilinear_entity_alphatest'},
            'textures': {'t' + str(i): texture for i, texture in enumerate(textures)},
            'geometry': {'default': 'geometry.test'},
            'render_controllers': [{'controller.render.test': 'q.is_alive'}],
        }
        save_json(root / 'entity/bct_test.entity.json', {'minecraft:client_entity': {'description': description}})
        controller = {
            'geometry': 'Geometry.default',
            'materials': [{'*': 'Material.default'}],
            'color': {'r': 0.8, 'g': 0.7, 'b': 0.5, 'a': 1},
            'textures': ["Array.tiles[q.property('bct:tile')]"],
            'arrays': {'textures': {'Array.tiles': ['Texture.t' + str(i) for i in range(4)]}},
        }
        if animated:
            controller['uv_anim'] = {'offset': [0, 'q.life_time'], 'scale': [1, .25]}
        save_json(root / 'render_controllers/test.json',
                  {'render_controllers': {'controller.render.test': controller}})
        return textures

    def test_interior_rgba_and_gutter_preserve_source_pixels(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            textures = [self.write_texture(root, 'tile' + str(i), (i * 40, 100, 130, 255)) for i in range(5)]
            bank = build_texture_atlas(root, textures, max_side=48, gutter=2)
            self.assertLessEqual(max(bank['size']), 48)
            for binding in bank['bindings']:
                with Image.open(root / (binding['texture'] + '.png')) as atlas:
                    with Image.open(root / (binding['source'] + '.png')) as source:
                        self.assertEqual(atlas.crop(binding['crop']).tobytes(), source.tobytes())
                        x, y = binding['crop'][:2]
                        self.assertEqual(atlas.getpixel((x - 1, y - 1)), source.getpixel((0, 0)))
                        self.assertEqual(atlas.getpixel((x + 8, y + 8)), source.getpixel((7, 7)))

    def test_palette_pages_share_pbr_maps_and_fixed_base_tile_uvs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            textures = [self.write_texture(root, 'tile' + str(i), (i * 20, 100, 130, 255)) for i in range(6)]
            bank = build_texture_atlas(root, textures, base_slots=3, max_side=24, gutter=2)
            self.assertEqual(len(bank['pages']), 2)
            first, second = [texture_set(root, page) for page in bank['pages']]
            self.assertEqual(first['normal'], second['normal'])
            self.assertEqual(first['metalness_emissive_roughness_subsurface'],
                             second['metalness_emissive_roughness_subsurface'])
            for index in range(3):
                self.assertEqual(bank['bindings'][index]['offset'], bank['bindings'][index + 3]['offset'])
            transform = atlas_uv(bank, "q.property('bct:tile') + q.property('bct:palette') * 3")
            self.assertIn('math.mod', transform['offset'][0])
            self.assertLess(len(transform['offset'][0]), 260)

    def test_small_tint_banks_pack_multiple_palettes_and_share_partial_page_maps(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            textures = [self.write_texture(root, 'tile' + str(i), (i * 20, 100, 130, 255),
                                           normal='normal' + str(i % 2), mers='mers' + str(i % 2))
                        for i in range(10)]
            bank = build_texture_atlas(root, textures, base_slots=2, max_side=24)
            self.assertEqual(bank['slots_per_page'], 2)
            self.assertEqual(len(bank['pages']), 5)
            descriptors = [texture_set(root, page) for page in bank['pages']]
            self.assertEqual(len({descriptor['normal'] for descriptor in descriptors}), 1)
            for index, binding in enumerate(bank['bindings']):
                slot = index % 2
                expected_corner = [slot % bank['columns'] * 12 + 2, slot // bank['columns'] * 12 + 2]
                self.assertEqual(binding['crop'][:2], expected_corner)
                with Image.open(root / (binding['texture'] + '.png')) as page:
                    with Image.open(root / (binding['source'] + '.png')) as original:
                        self.assertEqual(page.crop(binding['crop']).tobytes(), original.tobytes())
            for texture in textures:
                save_json(root / (texture + '.texture_set.json'), {'minecraft:texture_set': {
                    'color': Path(texture).name,
                    'metalness_emissive_roughness_subsurface': [5, 0, 245, 0],
                }})
            constant_bank = build_texture_atlas(root, textures, base_slots=2, max_side=24)
            self.assertEqual(constant_bank['slots_per_page'], 4)
            self.assertEqual(len(constant_bank['pages']), 3)

    def test_postprocessor_rewrites_supported_banks_and_prunes_only_replaced_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            originals = self.write_carrier_pack(root)
            other = root / 'textures/entity/author.png'
            Image.new('RGBA', (16, 16), 'red').save(other)
            report = consolidate_carrier_atlases(root, max_side=48)
            self.assertEqual(report['source_color_textures'], 4)
            self.assertEqual(report['result_color_textures'], 1)
            self.assertEqual(report['updated_controllers'], 1)
            self.assertTrue(other.exists())
            self.assertTrue(all(not (root / (texture + '.png')).exists() for texture in originals))
            client = read_json(root / 'entity/bct_test.entity.json')['minecraft:client_entity']['description']
            self.assertEqual(client['materials']['default'], 'bct_bilinear_uv_entity_alphatest')
            self.assertEqual(client['render_controllers'], [{'controller.render.test': 'q.is_alive'}])
            self.assertEqual(len(report['client_tiles']['entity/bct_test.entity.json']), 4)
            controllers = read_json(root / 'render_controllers/test.json')['render_controllers']
            controller = controllers['controller.render.test']
            self.assertEqual(controller['color'], {'r': .8, 'g': .7, 'b': .5, 'a': 1})
            self.assertIn('uv_anim', controller)
            self.assertTrue((root / ATLAS_METADATA).exists())
            self.assertTrue(consolidate_carrier_atlases(root)['already_consolidated'])

    def test_existing_animation_is_retained_with_original_image_bank(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            originals = self.write_carrier_pack(root, animated=True)
            original_controller = read_json(root / 'render_controllers/test.json')
            report = consolidate_carrier_atlases(root)
            self.assertEqual(report['result_color_textures'], 4)
            self.assertEqual(report['updated_controllers'], 0)
            self.assertTrue(all((root / (texture + '.png')).exists() for texture in originals))
            self.assertEqual(read_json(root / 'render_controllers/test.json'), original_controller)

    def test_mixed_dimensions_are_retained_and_escaping_paths_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.write_texture(root, 'first', (20, 30, 40, 255))
            second = self.write_texture(root, 'second', (40, 60, 80, 255))
            Image.new('RGBA', (8, 16)).save(root / (second + '.png'))
            with self.assertRaises(ValueError):
                build_texture_atlas(root, [first, second])
            with self.assertRaises(ValueError):
                build_texture_atlas(root, ['../escaped'])

    def test_equal_albedo_with_different_material_maps_keeps_distinct_descriptors(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.write_texture(root, 'first', (20, 30, 40, 255), normal='normal_a')
            second = self.write_texture(root, 'second', (20, 30, 40, 255), normal='normal_b')
            Image.new('RGB', (8, 8), (200, 80, 230)).save(root / 'textures/entity/normal_b.png')
            left = build_texture_atlas(root, [first, first])
            right = build_texture_atlas(root, [second, second])
            self.assertNotEqual(left['pages'], right['pages'])
            left_descriptor = texture_set(root, left['pages'][0])
            right_descriptor = texture_set(root, right['pages'][0])
            self.assertNotEqual(left_descriptor['normal'], right_descriptor['normal'])


if __name__ == '__main__':
    unittest.main()
