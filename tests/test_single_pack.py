"""One converted pack for every graphics mode, like an author's own Bedrock pack: no subpack or setting."""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from addon_package import compose_source_addon, packet, source_data
from engine_package import engine_dependency


def png(mode='RGBA', color=(100, 80, 60, 255)):
    stream = io.BytesIO()
    Image.new(mode, (2, 2), color).save(stream, format='PNG')
    return stream.getvalue()


def manifest(name, kind, capabilities=()):
    header = {'name': name, 'uuid': str(uuid.uuid5(uuid.NAMESPACE_URL, name)),
              'version': [1, 0, 0], 'min_engine_version': [1, 26, 50]}
    module = {'type': 'resources' if kind == 'rp' else 'data',
              'uuid': str(uuid.uuid5(uuid.NAMESPACE_URL, name + 'module')), 'version': [1, 0, 0]}
    return {'format_version': 2, 'header': header, 'modules': [module], 'capabilities': list(capabilities)}


def carrier_entity(name):
    return {'format_version': '1.10.0', 'minecraft:client_entity': {'description': {
        'identifier': 'bct_test:' + name, 'textures': {'default': 'textures/entity/large_bank'}}}}


class SinglePackTests(unittest.TestCase):
    def fixture(self, root):
        base = root / 'base'
        rtx = root / 'rtx'
        base.mkdir()
        rtx.mkdir()
        colour = png()
        normal = png(color=(128, 128, 255, 255))
        mers = png(color=(20, 30, 40, 50))
        ordinary = {
            'manifest.json': manifest('base', 'rp', ['pbr']),
            'blocks.json': {'format_version': [1, 1, 0], 'stone': {'textures': 'stone'}},
            'textures/terrain_texture.json': {'texture_data': {
                'stone': {'textures': 'textures/blocks/stone'},
                'repeat': {'textures': 'textures/blocks/repeat'}}},
            'textures/blocks/stone.png': colour, 'textures/blocks/stone_normal.png': normal,
            'textures/items/sword.png': colour, 'textures/entity/zombie.png': colour,
            'textures/particle/leaf.png': colour, 'textures/painting/art.png': colour,
            'entity/zombie.entity.json': {'format_version': '1.10.0', 'minecraft:client_entity': {'description': {
                'identifier': 'minecraft:zombie', 'textures': {'default': 'textures/entity/zombie'}}}},
            'textures/blocks/repeat.png': colour, 'textures/blocks/repeat_mers.png': mers,
            'textures/blocks/repeat.texture_set.json': {'format_version': '1.21.30', 'minecraft:texture_set': {
                'color': 'repeat', 'metalness_emissive_roughness_subsurface': 'repeat_mers'}},
        }
        native = {
            'manifest.json': manifest('native-rtx', 'rp', ['pbr', 'raytraced']),
            'blocks.json': {'format_version': [1, 1, 0], 'stone': {'textures': 'stone'},
                            'short_grass': {'textures': 'grass'}},
            'textures/terrain_texture.json': {'texture_data': {
                'stone': {'textures': 'textures/blocks/stone'},
                'grass': {'textures': 'textures/blocks/grass'}}},
            'textures/blocks/stone.png': colour, 'textures/blocks/stone_normal.png': normal,
            'textures/blocks/stone_mer.png': png('RGB', (20, 30, 40)),
            'textures/blocks/stone.texture_set.json': {'format_version': '1.21.30', 'minecraft:texture_set': {
                'color': 'stone', 'normal': 'stone_normal', 'metalness_emissive_roughness': 'stone_mer'}},
            'textures/items/sword.png': colour, 'textures/entity/zombie.png': colour,
            'textures/particle/leaf.png': colour, 'textures/painting/art.png': colour,
            'biomes/plains.client_biome.json': {'format_version': '1.21.40', 'minecraft:client_biome': {
                'description': {'identifier': 'minecraft:plains'},
                'components': {'minecraft:grass_appearance': {'color': '#6496C8'}}}},
        }
        for directory, values in ((base, ordinary), (rtx, native)):
            for name, data in values.items():
                target = directory / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
        connected = root / 'connected.mcaddon'
        rules = [{'id': 'basic', 'entity': 'bct_test:basic'}, {'id': 'second', 'entity': 'bct_test:second'}]
        with zipfile.ZipFile(connected, 'w') as addon:
            addon.writestr('Connected_BP/manifest.json', json.dumps(manifest('connected-bp', 'bp')))
            addon.writestr('Connected_RP/manifest.json', json.dumps(manifest('connected-rp', 'rp', ['pbr'])))
            addon.writestr('Connected_BP/scripts/source-data.js',
                           source_data([packet('connected', 'fixture', {'rules': rules})]))
            addon.writestr('Connected_RP/blocks.json', json.dumps({'short_grass': {'textures': 'bct_owned_transparent'}}))
            for name in ('basic', 'second'):
                addon.writestr('Connected_RP/entity/' + name + '.entity.json', json.dumps(carrier_entity(name)))
            addon.writestr('Connected_RP/textures/entity/large_bank.png', colour)
        return base, rtx, connected, ordinary

    def test_one_pack_declares_ray_tracing_uses_mer_and_keeps_blanked_faces(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _, rtx, connected, ordinary = self.fixture(root)
            # The RTX base is the published pack; a material written with MERS (a swapped block) joins it.
            for name in ('textures/blocks/repeat.png', 'textures/blocks/repeat_mers.png',
                         'textures/blocks/repeat.texture_set.json'):
                data = ordinary[name]
                (rtx / name).write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
            output = compose_source_addon(rtx, connected, root / 'pack.mcaddon', key='fixture', title='Fixture',
                                          raytraced=True)
            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                resources = json.loads(archive.read('Source_RP/manifest.json'))
                self.assertEqual(resources['capabilities'], ['pbr', 'raytraced'])
                self.assertNotIn('subpacks', resources, 'no setting to pick a mode')
                self.assertFalse(any('/subpacks/' in name for name in names))
                blocks = json.loads(archive.read('Source_RP/blocks.json'))
                self.assertEqual(blocks['short_grass']['textures'], 'grass',
                                 'a block the carriers blank keeps its faces, for ray tracing')
                material = json.loads(archive.read('Source_RP/textures/blocks/repeat.texture_set.json'))['minecraft:texture_set']
                self.assertNotIn('metalness_emissive_roughness_subsurface', material)
                mer = 'Source_RP/textures/blocks/' + material['metalness_emissive_roughness'] + '.png'
                with Image.open(io.BytesIO(archive.read(mer))) as image:
                    self.assertEqual(image.mode, 'RGB')
                    self.assertEqual(image.getpixel((0, 0)), (20, 30, 40))
                self.assertNotIn('Source_RP/textures/blocks/repeat_mers.png', names, 'the replaced MERS image is gone')
                for name in ('textures/items/sword.png', 'textures/entity/zombie.png'):
                    self.assertIn('Source_RP/' + name, names)
                behavior = json.loads(archive.read('Source_BP/manifest.json'))
                dependencies = {value['uuid'] for value in behavior['dependencies'] if 'uuid' in value}
                self.assertEqual(dependencies, {resources['header']['uuid'], engine_dependency()['uuid']})
                self.assertEqual(sum(name.endswith('/manifest.json') for name in names), 2)
                self.assertIsNone(archive.testzip())

    def test_without_ray_tracing_the_pack_declares_pbr_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            base, _, connected, _ = self.fixture(root)
            output = compose_source_addon(base, connected, root / 'regular.mcaddon', key='fixture', title='Fixture')
            with zipfile.ZipFile(output) as archive:
                result = json.loads(archive.read('Source_RP/manifest.json'))
                self.assertEqual(result['capabilities'], ['pbr'])
                self.assertNotIn('subpacks', result)


if __name__ == '__main__':
    unittest.main()
