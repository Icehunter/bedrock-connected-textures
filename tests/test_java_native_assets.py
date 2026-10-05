"""Item, painting, particle and entity textures bind to Bedrock's only with evidence, or say why not."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from java_native_assets import plan_native_assets, prepare_native_assets, export_native_assets

FLAT_NORMAL = (128, 128, 255, 255)
ARROW_ATLAS = {'texture_data': {'arrow': {'textures': 'textures/items/arrow'}}}


def png(image):
    raw = io.BytesIO()
    image.save(raw, format='PNG')
    return raw.getvalue()


def flat_png(size, color=(0, 0, 0, 0)):
    return png(Image.new('RGBA', size, color))


class Stack:
    def __init__(self, files):
        self.files = files

    def read(self, path):
        return self.files[path]


def fixture(folder, reference):
    """A vanilla Java jar holding the reference files, and an empty bedrock-samples folder."""
    jar = folder / 'vanilla.jar'
    with zipfile.ZipFile(jar, 'w') as output:
        for path, data in reference.items():
            output.writestr(path, data)
    samples = folder / 'samples'
    samples.mkdir()
    return jar, samples


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


class NativeAssetsTests(unittest.TestCase):
    def test_tool_aliases_preserve_native_array_and_author_pbr(self):
        sprite = 'assets/minecraft/textures/item/golden_axe.png'
        source = flat_png((2, 2), (30, 60, 90, 100))
        stack = Stack({sprite: source, sprite[:-4] + '_n.png': flat_png((2, 2), FLAT_NORMAL)})
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            jar, samples = fixture(folder, {})
            atlas = {'texture_data': {'axe': {'textures': ['textures/items/wood_axe', 'textures/items/gold_axe']}}}
            write(samples / 'textures/item_texture.json', atlas)
            plan = prepare_native_assets(stack, jar, samples, folder / 'materials')
            export_native_assets(folder / 'materials', plan, folder / 'rp', 'vv')
            self.assertEqual((folder / 'rp/textures/items/gold_axe.png').read_bytes(), source)
            self.assertEqual(json.loads((folder / 'rp/textures/item_texture.json').read_text()), atlas)
            self.assertTrue((folder / 'rp/textures/items/gold_axe.texture_set.json').is_file())
            self.assertEqual(plan['counts']['item']['native_bound'], 1)

    def test_custom_item_geometry_and_cem_skin_remain_explicitly_unsupported(self):
        item = 'assets/minecraft/textures/item/arrow.png'
        entity = 'assets/minecraft/textures/entity/creeper/creeper.png'
        skin = flat_png((4, 2), (40, 70, 60, 255))
        model = json.dumps({'elements': [{'from': [0, 0, 0], 'to': [1, 1, 1]}]}).encode()
        stack = Stack({item: skin, entity: skin, 'assets/minecraft/models/item/arrow.json': model,
                       'assets/minecraft/optifine/cem/creeper.jem': b'{}'})
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            jar, samples = fixture(folder, {entity: skin})
            write(samples / 'textures/item_texture.json', ARROW_ATLAS)
            path = samples / 'textures/entity/creeper/creeper.png'
            path.parent.mkdir(parents=True)
            path.write_bytes(skin)
            plan = plan_native_assets(stack, jar, samples)
            self.assertFalse(plan['bindings'])
            reasons = {record['reason'] for record in plan['records']}
            self.assertEqual(reasons, {'custom_item_geometry_requires_model_conversion',
                                       'custom_entity_model_changes_uv_binding'})

    def test_verified_painting_slot_keeps_art_pixels_and_other_atlas_regions(self):
        source = 'assets/minecraft/textures/painting/kebab.png'
        reference = Image.new('RGBA', (16, 16), (40, 50, 60, 255))
        author = Image.new('RGBA', (32, 32), (130, 40, 90, 255))
        author.putpixel((3, 7), (1, 2, 3, 4))
        stack = Stack({source: png(author)})
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            jar, samples = fixture(folder, {source: png(reference)})
            atlas = Image.new('RGBA', (32, 16), (90, 70, 30, 255))
            atlas.paste(reference, (16, 0))
            path = samples / 'textures/painting/kz.png'
            path.parent.mkdir(parents=True)
            atlas.save(path)
            plan = prepare_native_assets(stack, jar, samples, folder / 'materials')
            export_native_assets(folder / 'materials', plan, folder / 'rp')
            output = Image.open(folder / 'rp/textures/painting/kz.png').convert('RGBA')
            self.assertTrue(np.array_equal(np.array(output.crop((32, 0, 64, 32))), np.array(author)))
            self.assertEqual(output.getpixel((0, 0)), (90, 70, 30, 255))
            self.assertEqual(plan['records'][0]['region'], [16, 0, 16, 16])

    def test_flame_animation_rewrites_only_uv_and_keeps_tint_and_motion(self):
        source = 'assets/minecraft/textures/particle/flame.png'
        image = Image.new('RGBA', (2, 4), (100, 80, 60, 255))
        stack = Stack({source: png(image), source + '.mcmeta': b'{"animation":{"frametime":3}}'})
        tinting = {'color': ['variable.color.r', 'variable.color.g', 'variable.color.b', 1]}
        native = {'particle_effect': {
            'description': {'identifier': 'minecraft:basic_flame_particle',
                            'basic_render_parameters': {'texture': 'textures/particle/particles',
                                                        'material': 'particles_alpha'}},
            'components': {'minecraft:particle_appearance_billboard': {'size': [1, 1], 'uv': {'uv': [0, 24]}},
                           'minecraft:particle_motion_dynamic': {'linear_acceleration': [0, 1, 0]},
                           'minecraft:particle_appearance_tinting': tinting}}}
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            jar, samples = fixture(folder, {})
            write(samples / 'particles/basic_flame.json', native)
            plan = prepare_native_assets(stack, jar, samples, folder / 'materials')
            export_native_assets(folder / 'materials', plan, folder / 'rp')
            effect = json.loads((folder / 'rp/particles/basic_flame.json').read_text())['particle_effect']
            native_components = native['particle_effect']['components']
            for component in ('minecraft:particle_motion_dynamic', 'minecraft:particle_appearance_tinting'):
                self.assertEqual(effect['components'][component], native_components[component])
            uv = effect['components']['minecraft:particle_appearance_billboard']['uv']
            self.assertEqual(uv['flipbook']['max_frame'], 2)
            self.assertEqual(uv['flipbook']['frames_per_second'], 20 / 3)
            self.assertFalse((folder / 'rp/particles/biome_tinted_leaves_particle.json').exists())

    def test_incomplete_smoke_bank_is_not_published(self):
        source = 'assets/minecraft/textures/particle/big_smoke_0.png'
        native = {'particle_effect': {'description': {}, 'components': {}}}
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            jar, samples = fixture(folder, {})
            write(samples / 'particles/campfire_smoke.json', native)
            plan = plan_native_assets(Stack({source: flat_png((2, 2))}), jar, samples)
            self.assertFalse(plan['bindings'])
            self.assertEqual(plan['records'][0]['reason'], 'campfire_particle_requires_all_twelve_frames')

    def test_repeated_prepare_refreshes_changed_art_and_removes_old_maps(self):
        source = 'assets/minecraft/textures/item/arrow.png'
        first = flat_png((2, 2), (10, 20, 30, 255))
        second = flat_png((2, 2), (70, 80, 90, 255))
        stack = Stack({source: first, source[:-4] + '_n.png': flat_png((2, 2), FLAT_NORMAL)})
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            jar, samples = fixture(folder, {})
            write(samples / 'textures/item_texture.json', ARROW_ATLAS)
            materials = folder / 'materials'
            plan = prepare_native_assets(stack, jar, samples, materials)
            stack.files = {source: second}
            refreshed = prepare_native_assets(stack, jar, samples, materials, plan=plan)
            self.assertEqual((materials / 'textures/items/arrow.png').read_bytes(), second)
            self.assertFalse((materials / 'textures/items/arrow.texture_set.json').exists())
            self.assertFalse((materials / 'textures/items/arrow_normal.png').exists())
            self.assertEqual(len(refreshed['material_receipts']), 1)
            # A valid cached family is still accounted for in the returned receipts.
            again = prepare_native_assets(stack, jar, samples, materials, plan=refreshed)
            self.assertEqual(again['material_receipts'], refreshed['material_receipts'])

    def test_animated_item_is_not_published_as_a_stretched_still(self):
        source = 'assets/minecraft/textures/item/arrow.png'
        stack = Stack({source: flat_png((2, 4)), source + '.mcmeta': b'{"animation":{"frametime":2}}'})
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            jar, samples = fixture(folder, {})
            write(samples / 'textures/item_texture.json', ARROW_ATLAS)
            plan = plan_native_assets(stack, jar, samples)
            self.assertFalse(plan['bindings'])
            self.assertEqual(plan['records'][0]['reason'],
                             'native_nonparticle_texture_animation_requires_runtime_adapter')

    def test_resumed_painting_plan_has_one_synthetic_atlas_binding(self):
        source = 'assets/minecraft/textures/painting/kebab.png'
        reference = Image.new('RGBA', (16, 16), (40, 50, 60, 255))
        stack = Stack({source: flat_png((32, 32), (70, 80, 90, 255))})
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            jar, samples = fixture(folder, {source: png(reference)})
            atlas_path = samples / 'textures/painting/kz.png'
            atlas_path.parent.mkdir(parents=True)
            reference.save(atlas_path)
            plan = prepare_native_assets(stack, jar, samples, folder / 'materials')
            resumed = prepare_native_assets(stack, jar, samples, folder / 'materials', plan=plan)
            self.assertEqual(len(plan['bindings']), 1)
            self.assertEqual(len(resumed['bindings']), 1)
            self.assertEqual(len(resumed['material_receipts']), 1)


if __name__ == '__main__':
    unittest.main()
