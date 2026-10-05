import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from carrier_materials import (
    CARRIER_UV_MATERIAL_PATH, carrier_material, write_carrier_uv_materials,
    carrier_filter_materials, apply_carrier_filter,
)
from common import scratch

PARENTS = ('entity', 'entity_alphatest', 'entity_alphablend')


def read_json(path):
    return json.loads(path.read_text())


def client_description(path):
    return read_json(path)['minecraft:client_entity']['description']


def write_client(path, description):
    path.write_text(json.dumps({'minecraft:client_entity': {'description': description}}))


def write_resource_manifest(pack):
    (pack / 'manifest.json').write_text(json.dumps({'modules': [{'type': 'resources'}]}))


class CarrierMaterialTests(unittest.TestCase):
    def test_animated_client_aliases_resolve_in_registered_entity_material_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary)
            (pack / 'materials').mkdir()
            stale = pack / 'materials/bct.material'
            stale.write_text(json.dumps({'materials': {'version': '1.0.0',
                                                       'bct_uv:entity': {'+defines': ['USE_UV_ANIM']}}}))
            (pack / 'entity').mkdir()
            for parent in PARENTS:
                write_client(pack / 'entity' / (parent + '.json'),
                             {'materials': {'default': carrier_material(parent, True)}})
            (pack / 'textures').mkdir()
            (pack / 'textures/tile.png').write_bytes(b'original color bytes')
            (pack / 'textures/tile_mers.png').write_bytes(b'original PBR bytes')
            originals = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                         for path in pack.rglob('*') if path.is_file()}

            result = write_carrier_uv_materials(pack)
            self.assertTrue(result['changed'])
            self.assertEqual(CARRIER_UV_MATERIAL_PATH, 'materials/entity.material')
            material = read_json(pack / CARRIER_UV_MATERIAL_PATH)['materials']
            registered = {key.split(':', 1)[0]: key for key in material if key != 'version'}
            for parent in PARENTS:
                alias = client_description(pack / 'entity' / (parent + '.json'))['materials']['default']
                self.assertEqual(registered[alias], alias + ':' + parent)
                self.assertEqual(material[registered[alias]], {'+defines': ['USE_UV_ANIM']})
            self.assertEqual(len(registered), 3)
            for path, digest in originals.items():
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
            canonical_before = (pack / CARRIER_UV_MATERIAL_PATH).read_bytes()
            self.assertFalse(write_carrier_uv_materials(pack)['changed'])
            self.assertEqual((pack / CARRIER_UV_MATERIAL_PATH).read_bytes(), canonical_before)

    def test_merge_preserves_other_definitions_and_replaces_only_owned_aliases(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / CARRIER_UV_MATERIAL_PATH
            path.parent.mkdir()
            existing = {'materials': {
                'version': '1.0.0',
                'author_material:entity': {'+states': ['DisableCulling']},
                'bct_uv:entity_change_color': {'+defines': ['WRONG']},
            }}
            path.write_text(json.dumps(existing))
            write_carrier_uv_materials(Path(temporary))
            materials = read_json(path)['materials']
            self.assertEqual(materials['author_material:entity'], existing['materials']['author_material:entity'])
            self.assertNotIn('bct_uv:entity_change_color', materials)
            self.assertIn('bct_uv:entity', materials)

    def test_filter_candidate_changes_only_sampler_zero_and_retains_plain_parents(self):
        for texture_filter, sampler_filter in [('point', 'Point'), ('bilinear', 'Bilinear')]:
            definitions = carrier_filter_materials(texture_filter)
            self.assertEqual(len(definitions), 6)
            for parent in PARENTS:
                for animated in (False, True):
                    alias = carrier_material(parent, animated, texture_filter)
                    definition = definitions[alias + ':' + parent]
                    self.assertEqual(definition['+samplerStates'], [
                        {'samplerIndex': 0, 'textureFilter': sampler_filter, 'textureWrap': 'Clamp'}])
                    self.assertEqual(definition.get('+defines', []), ['USE_UV_ANIM'] if animated else [])
                    expected_fields = {'+samplerStates', '+defines'} if animated else {'+samplerStates'}
                    self.assertEqual(set(definition), expected_fields)
        for texture_filter in ('Trilinear', 'mipmap', '', 'TexelAA'):
            with self.assertRaises(ValueError):
                carrier_filter_materials(texture_filter)

    def test_staged_filter_migration_preserves_art_controllers_and_point_compatibility(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary)
            (pack / 'entity').mkdir()
            (pack / 'textures').mkdir()
            write_resource_manifest(pack)
            (pack / 'textures/color.png').write_bytes(b'unchanged color and alpha')
            (pack / 'textures/normal.png').write_bytes(b'unchanged normal map')
            (pack / 'textures/mers.png').write_bytes(b'unchanged material channels')
            (pack / 'geometry.json').write_text('{"uv":"unchanged"}')
            (pack / 'controller.json').write_text('{"uv_anim":"unchanged","color":"white"}')
            for parent in PARENTS:
                for animated in (False, True):
                    name = f'bct_{parent}_{animated}'
                    write_client(pack / 'entity' / (name + '.entity.json'), {
                        'identifier': 'bct:' + name[4:],
                        'materials': {'default': carrier_material(parent, animated)},
                        'textures': {'t0': 'textures/color'},
                    })
            write_client(pack / 'entity/bct_unrelated.entity.json',
                         {'identifier': 'author:untouched', 'materials': {'default': 'author_shader'}})
            originals = {path: path.read_bytes() for path in pack.rglob('*')
                         if path.is_file() and (path.parent.name != 'entity' or 'unrelated' in path.name)}
            result = apply_carrier_filter(pack, 'bilinear')
            self.assertEqual(result['client_bindings_changed'], 6)
            self.assertTrue(result['material_file_changed'])
            self.assertEqual(result['sampler0'], {'textureFilter': 'Bilinear', 'textureWrap': 'Clamp'})
            self.assertIn('unverified', result['mipmap_allocation'])
            for parent in PARENTS:
                for animated in (False, True):
                    client = client_description(pack / 'entity' / f'bct_{parent}_{animated}.entity.json')
                    self.assertEqual(client['materials']['default'], carrier_material(parent, animated, 'bilinear'))
                    self.assertEqual(client['textures'], {'t0': 'textures/color'})
            repeated = apply_carrier_filter(pack, 'bilinear')
            self.assertEqual(repeated['client_bindings_changed'], 0)
            self.assertFalse(repeated['material_file_changed'])
            self.assertEqual(apply_carrier_filter(pack, 'point')['client_bindings_changed'], 6)
            for path, content in originals.items():
                self.assertEqual(path.read_bytes(), content)
            self.assertEqual(carrier_material('entity'), 'entity')

    def test_unknown_carrier_material_fails_before_modifying_pack(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary)
            (pack / 'entity').mkdir()
            write_resource_manifest(pack)
            path = pack / 'entity/bct_invalid.entity.json'
            write_client(path, {'identifier': 'bct:invalid', 'materials': {'default': 'unmapped'}})
            original = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'Unrecognized'):
                apply_carrier_filter(pack, 'bilinear')
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse((pack / CARRIER_UV_MATERIAL_PATH).exists())

    def test_animated_filter_alias_survives_palette_and_shader_refresh_pipeline(self):
        from PIL import Image
        from connected_build import ROOT, refresh_carrier_materials
        from palette_export import apply_tint_palettes
        with tempfile.TemporaryDirectory(dir=scratch(ROOT / 'build')) as temporary:
            pack = Path(temporary)
            for folder in ('entity', 'render_controllers', 'textures/entity'):
                (pack / folder).mkdir(parents=True)
            write_resource_manifest(pack)
            Image.new('RGBA', (4, 8), (90, 120, 150, 255)).save(pack / 'textures/entity/tile.png')
            write_client(pack / 'entity/bct_test.entity.json', {
                'identifier': 'bct:test',
                'materials': {'default': carrier_material('entity', True)},
                'textures': {'t0': 'textures/entity/tile'},
                'render_controllers': ['controller.render.bct_test'],
            })
            uv = {'offset': [0, 'q.time_stamp / 2'], 'scale': [1, 0.5]}
            (pack / 'render_controllers/bct_test.json').write_text(json.dumps({
                'render_controllers': {'controller.render.bct_test': {'uv_anim': uv}}}))
            write_carrier_uv_materials(pack)
            rules = [{'id': 'test', 'entity': 'bct:test', 'tiles': ['tile']}]
            palettes = {'test': [[1, 1, 1], [0.2, 0.4, 0.6]]}
            apply_tint_palettes(pack, rules, palettes)
            refresh_carrier_materials(pack)
            apply_carrier_filter(pack, 'bilinear')
            registered = (pack / CARRIER_UV_MATERIAL_PATH).read_bytes()
            # A repeat palette/refresh pass must not replace an opted-in alias.
            apply_tint_palettes(pack, rules, palettes)
            refresh_carrier_materials(pack)
            self.assertEqual(apply_carrier_filter(pack, 'bilinear')['client_bindings_changed'], 0)
            self.assertEqual((pack / CARRIER_UV_MATERIAL_PATH).read_bytes(), registered)
            description = client_description(pack / 'entity/bct_test.entity.json')
            alias = carrier_material('entity', True, 'bilinear')
            self.assertEqual(description['materials']['default'], alias)
            self.assertEqual(len(description['textures']), 2)
            controllers = read_json(pack / 'render_controllers/bct_test.json')['render_controllers']
            controller = controllers['controller.render.bct_test']
            self.assertEqual(controller['uv_anim'], uv)
            self.assertEqual(controller['color'], {'r': 1, 'g': 1, 'b': 1, 'a': 1})
            material = json.loads(registered)['materials'][alias + ':entity']
            self.assertEqual(material['+defines'], ['USE_UV_ANIM'])
            self.assertEqual(material['+samplerStates'],
                             [{'samplerIndex': 0, 'textureFilter': 'Bilinear', 'textureWrap': 'Clamp'}])


if __name__ == '__main__':
    unittest.main()
