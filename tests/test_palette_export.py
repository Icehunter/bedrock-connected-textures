"""Tinted carrier copies: Java's 8-bit tint maths, untouched originals, shared PBR maps and palette lookups."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from palette_export import apply_tint_palettes, normalized_palette, tinted_albedo

GRASS_TINT = [145 / 255, 189 / 255, 89 / 255]


def tinted(value, factor):
    """One 8-bit channel multiplied by an 8-bit tint, rounded to nearest."""
    return (value * factor + 127) // 255


class TintPaletteExportTests(unittest.TestCase):
    def test_rgb8_multiplier_preserves_every_alpha_value(self):
        original = Image.new('RGBA', (256, 1))
        original.putdata([(channel, 255 - channel, 128, channel) for channel in range(256)])
        result = tinted_albedo(original, GRASS_TINT)
        for channel, pixel in enumerate(result.getdata()):
            self.assertEqual(pixel, (tinted(channel, 145), tinted(255 - channel, 189), tinted(128, 89), channel))
        self.assertEqual(original.getchannel('A').tobytes(), result.getchannel('A').tobytes())
        self.assertEqual(normalized_palette([[1, 1, 1], GRASS_TINT]), [[1, 1, 1], [0.568627, 0.741176, 0.34902]])

    def test_existing_animated_pack_preserves_sources_and_shares_pbr_channels(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary)
            for directory in ('entity', 'render_controllers', 'textures/entity'):
                (pack / directory).mkdir(parents=True)

            def write(name, document):
                (pack / name).write_text(json.dumps(document))

            write('manifest.json', {'modules': [{'type': 'resources'}]})
            original = Image.new('RGBA', (4, 8), (150, 110, 90, 255))
            original.putpixel((0, 0), (30, 60, 90, 0))
            original.putpixel((2, 6), (220, 180, 140, 127))
            original.save(pack / 'textures/entity/tile.png')
            Image.new('RGBA', (4, 8), (10, 20, 30, 40)).save(pack / 'textures/entity/mers.png')
            write('textures/entity/tile.texture_set.json', {'format_version': '1.21.30', 'minecraft:texture_set': {
                'color': 'tile', 'metalness_emissive_roughness_subsurface': 'mers'}})
            rules = []
            for name in ('first', 'second'):
                stem = 'bct_' + name
                controller_name = 'controller.render.' + stem
                description = {'identifier': 'bct:' + name, 'materials': {'default': 'bct_uv'},
                               'textures': {'t0': 'textures/entity/tile'}, 'render_controllers': [controller_name],
                               'geometry': {'default': 'geometry.' + stem}}
                write('entity/' + stem + '.entity.json', {'minecraft:client_entity': {'description': description}})
                write('render_controllers/' + stem + '.json', {'render_controllers': {controller_name: {
                    'geometry': 'Geometry.default', 'textures': ['Array.tiles[0]'],
                    'uv_anim': {'offset': [0, 'q.time_stamp / 2'], 'scale': [1, 0.5]},
                    'part_visibility': [{'up': True}], 'color': {'r': 0.5, 'g': 0.5, 'b': 0.5, 'a': 1}}}})
                rules.append({'id': name, 'entity': description['identifier'], 'tiles': ['tile']})
            originals = {path.relative_to(pack): path.read_bytes()
                         for path in (pack / 'textures').rglob('*') if path.is_file()}
            palettes = {rule['id']: [[1, 1, 1], GRASS_TINT] for rule in rules}
            report = apply_tint_palettes(pack, rules, palettes)
            self.assertEqual(report['generated_albedo_files'], 1)
            self.assertEqual(report['unique_tinted_materials'], 1)
            for name, raw in originals.items():
                self.assertEqual((pack / name).read_bytes(), raw)
            client = json.loads((pack / 'entity/bct_first.entity.json').read_text())
            description = client['minecraft:client_entity']['description']
            variant = pack / (description['textures']['p1_t0'] + '.png')
            with Image.open(variant) as image:
                self.assertEqual(image.size, original.size)
                self.assertEqual(image.getchannel('A').tobytes(), original.getchannel('A').tobytes())
                self.assertEqual(image.getpixel((2, 6)), (tinted(220, 145), tinted(180, 189), tinted(140, 89), 127))
            descriptor = json.loads(variant.with_suffix('.texture_set.json').read_text())['minecraft:texture_set']
            self.assertEqual(descriptor['metalness_emissive_roughness_subsurface'], 'mers')
            self.assertEqual(descriptor['color'], variant.stem)
            controllers = json.loads((pack / 'render_controllers/bct_first.json').read_text())
            controller = controllers['render_controllers']['controller.render.bct_first']
            self.assertEqual(controller['textures'],
                             ["Array.tiles[q.property('bct:tile') + q.property('bct:palette') * 1]"])
            self.assertEqual(controller['arrays']['textures']['Array.tiles'], ['Texture.t0', 'Texture.p1_t0'])
            self.assertEqual(controller['color'], {'r': 1, 'g': 1, 'b': 1, 'a': 1})
            self.assertEqual(controller['uv_anim'], {'offset': [0, 'q.time_stamp / 2'], 'scale': [1, 0.5]})
            self.assertEqual(controller['part_visibility'], [{'up': True}])
            materials = json.loads((pack / 'materials/entity.material').read_text())['materials']
            self.assertEqual(materials['bct_uv:entity'], {'+defines': ['USE_UV_ANIM']})
            self.assertIn(variant.relative_to(pack).as_posix(), report['output_files'])
            again = apply_tint_palettes(pack, rules, palettes)
            self.assertEqual(again['generated_albedo_files'], 0)
            self.assertEqual(again['updated_json_files'], 0)

    def test_palette_limit_is_explicit(self):
        with self.assertRaisesRegex(ValueError, '256'):
            normalized_palette([[index / 255, 0, 0] for index in range(256)])


if __name__ == '__main__':
    unittest.main()
