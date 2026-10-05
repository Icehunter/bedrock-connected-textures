"""The author's Vibrant Visuals scene settings, local lights and water come from their Bedrock edition."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from vv_scene_profile import read_scene_profile, apply_scene_profile, bind_block_fallbacks, read_water_textures


def client_biome(identifier, components, version='1.21.70'):
    return {'format_version': version,
            'minecraft:client_biome': {'description': {'identifier': identifier}, 'components': components}}


class SceneProfileTests(unittest.TestCase):
    def source(self, path, missing=False):
        """A reference Bedrock pack with lighting, fog, PBR and local light settings and two linked biomes."""
        plains = client_biome('plains', {
            'minecraft:lighting_identifier': {'lighting_identifier': 'missing' if missing else 'author:light'},
            'minecraft:grass_appearance': {'color': '#ff00ff'},
            'minecraft:sky_color': {'sky_color': '#ff00ff'},
            'minecraft:fog_appearance': {'fog_identifier': 'author:fog'}})
        forest = client_biome('forest', {
            'minecraft:fog_appearance': {'fog_identifier': 'author:fog'},
            'minecraft:sky_color': {'sky_color': '#6b98c6'}})
        density = {'air': {'max_density': 0.037, 'uniform': False}, 'weather': {'max_density': 0.01, 'uniform': True}}
        files = {
            'manifest.json': {'modules': [{'type': 'resources'}]},
            'lighting/global.json': {'format_version': '1.21.80', 'minecraft:lighting_settings': {
                'description': {'identifier': 'author:light'}, 'sky': {'intensity': 0.3}}},
            'biomes/plains.client_biome.json': plains,
            'pbr/global.json': {'format_version': '1.21.40', 'minecraft:pbr_fallback_settings': {
                'blocks': {'global_metalness_emissive_roughness_subsurface': [5, 0, 245, 0]},
                'actors': {'global_metalness_emissive_roughness_subsurface': [0, 0, 235, 30]}}},
            'fogs/default.json': {'format_version': '1.21.90', 'minecraft:fog_settings': {
                'description': {'identifier': 'author:fog'}, 'volumetric': {'density': density}}},
            'local_lighting/local_lighting.json': {'format_version': '1.21.120', 'minecraft:local_light_settings': {
                'minecraft:torch': {'light_color': '#FFC97A'}}},
            'biomes/forest.client_biome.json': forest,
        }
        with zipfile.ZipFile(path, 'w') as archive:
            for name, value in files.items():
                archive.writestr('Reference/' + name, json.dumps(value))
            archive.writestr('Reference/textures/blocks/stone.png', b'other author artwork')

    def pack(self, path):
        path.mkdir()
        (path / 'manifest.json').write_text(json.dumps({'capabilities': ['pbr']}))

    def test_reference_scene_links_preserve_destination_appearance_and_artwork(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source.mcpack'
            self.source(source)
            source_bytes = source.read_bytes()
            pack = root / 'pack'
            self.pack(pack)
            (pack / 'biomes').mkdir()
            biome = pack / 'biomes/plains.client_biome.json'
            original = client_biome('plains', {
                'minecraft:grass_appearance': {'color': '#35462f'},
                'minecraft:sky_color': {'sky_color': '#8090aa'},
                'minecraft:fog_appearance': {'fog_identifier': 'mine:fog'}}, version='1.16.0')
            biome.write_text(json.dumps(original))
            (pack / 'textures').mkdir()
            art = pack / 'textures/stone.png'
            art.write_bytes(b'my art')
            profile = read_scene_profile(source)
            report = apply_scene_profile(pack, profile)
            actual = json.loads(biome.read_text())['minecraft:client_biome']['components']
            self.assertEqual(json.loads(biome.read_text())['format_version'], '1.21.70')
            for key, value in original['minecraft:client_biome']['components'].items():
                self.assertEqual(actual[key], value)
            self.assertEqual(actual['minecraft:lighting_identifier']['lighting_identifier'], 'author:light')
            # the reference's fog and local lights come along; this destination keeps its own fog link
            self.assertTrue((pack / 'fogs/default.json').exists())
            fog = json.loads((pack / 'fogs/default.json').read_text())
            density = fog['minecraft:fog_settings']['volumetric']['density']
            # non-uniform layers get vanilla's heights so current Bedrock accepts them; uniform layers are left alone
            self.assertEqual(density['air'], {'max_density': 0.037, 'uniform': False,
                                              'max_density_height': 320.0, 'zero_density_height': 320.0})
            self.assertEqual(density['weather'], {'max_density': 0.01, 'uniform': True})
            self.assertTrue((pack / 'local_lighting/local_lighting.json').exists())
            # a biome the destination doesn't define gets the reference's fog and sky colour
            forest = json.loads((pack / 'biomes/forest.client_biome.json').read_text())
            components = forest['minecraft:client_biome']['components']
            self.assertEqual(components['minecraft:fog_appearance'], {'fog_identifier': 'author:fog'})
            self.assertEqual(components['minecraft:sky_color'], {'sky_color': '#6b98c6'})
            self.assertEqual(art.read_bytes(), b'my art')
            self.assertEqual(source.read_bytes(), source_bytes)
            self.assertEqual(report['biomes_linked'], 2)
            self.assertEqual(apply_scene_profile(pack, profile)['changed_files'], [])

    def test_local_lights_name_bedrock_blocks_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            (source / 'local_lighting').mkdir(parents=True)
            lights = {'minecraft:wall_torch': {'light_color': '#000001'},
                      'minecraft:torch': {'light_color': '#FFC97A'},
                      'minecraft:jack_o_lantern': {'light_color': '#FF9900'},
                      'minecraft:not_a_block': {'light_color': '#FFFFFF'}}
            (source / 'local_lighting/local_lighting.json').write_text(json.dumps(
                {'format_version': '1.21.120', 'minecraft:local_light_settings': lights}))
            samples = root / 'bedrock-samples'
            (samples / 'resource_pack').mkdir(parents=True)
            (samples / 'metadata/vanilladata_modules').mkdir(parents=True)
            known = {'data_items': [{'name': 'minecraft:torch'}, {'name': 'minecraft:lit_pumpkin'}]}
            (samples / 'metadata/vanilladata_modules/mojang-blocks.json').write_text(json.dumps(known))
            pack = root / 'pack'
            self.pack(pack)
            report = apply_scene_profile(pack, read_scene_profile(source), samples=samples / 'resource_pack')
            written = json.loads((pack / 'local_lighting/local_lighting.json').read_text())
            # The author's own torch entry wins over wall_torch, which Bedrock calls torch too.
            self.assertEqual(written['minecraft:local_light_settings'],
                             {'minecraft:torch': {'light_color': '#FFC97A'},
                              'minecraft:lit_pumpkin': {'light_color': '#FF9900'}})
            self.assertEqual(report['local_lights_renamed'], [
                {'file': 'local_lighting/local_lighting.json', 'block': 'minecraft:wall_torch',
                 'bedrock': ['minecraft:torch']},
                {'file': 'local_lighting/local_lighting.json', 'block': 'minecraft:jack_o_lantern',
                 'bedrock': ['minecraft:lit_pumpkin']}])
            self.assertEqual(report['local_lights_dropped'],
                             [{'file': 'local_lighting/local_lighting.json', 'block': 'minecraft:not_a_block'}])

    def test_water_comes_from_the_reference_pack_with_its_flipbooks(self):
        still_water = {'flipbook_texture': 'textures/blocks/water_still_grey', 'atlas_tile': 'still_water_grey',
                       'ticks_per_frame': 4}
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'scene.mcpack'
            with zipfile.ZipFile(archive, 'w') as pack:
                pack.writestr('Pack/manifest.json', '{}')
                for name in ('water_still_grey.png', 'water_still_grey_normal.png', 'water_still_grey.texture_set.json',
                             'water_still_grey.png.mcmeta', 'waterlily.png', 'water_still_grey_extra.png'):
                    pack.writestr('Pack/textures/blocks/' + name, name)
                pack.writestr('Pack/textures/flipbook_textures.json', json.dumps([
                    still_water, {'flipbook_texture': 'textures/blocks/lava_still', 'atlas_tile': 'still_lava'}]))
            water = read_water_textures(archive)
            self.assertEqual(sorted(water['files']), ['textures/blocks/water_still_grey.png',
                                                      'textures/blocks/water_still_grey.png.mcmeta',
                                                      'textures/blocks/water_still_grey.texture_set.json',
                                                      'textures/blocks/water_still_grey_normal.png'])
            self.assertEqual(water['flipbooks'], [still_water])
            empty = Path(directory) / 'none'
            (empty / 'textures/blocks').mkdir(parents=True)
            self.assertIsNone(read_water_textures(empty))

    def test_unresolved_profile_is_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source.mcpack'
            self.source(source, missing=True)
            with self.assertRaisesRegex(ValueError, 'Unresolved VV scene link'):
                read_scene_profile(source)
            self.assertEqual(set(root.iterdir()), {source})

    def test_carriers_receive_block_defaults_and_preserve_authored_material_maps(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary) / 'pack'
            self.pack(pack)
            (pack / 'entity').mkdir()
            (pack / 'textures/entity').mkdir(parents=True)
            textures = {'t0': 'textures/entity/missing', 't1': 'textures/entity/authored'}
            (pack / 'entity/bct_test.entity.json').write_text(json.dumps({
                'minecraft:client_entity': {'description': {'identifier': 'bct:test', 'textures': textures}}}))
            for name in ('missing', 'authored'):
                (pack / f'textures/entity/{name}.png').write_bytes(b'original artwork')
            authored = pack / 'textures/entity/authored.texture_set.json'
            original = {'minecraft:texture_set': {'color': 'authored', 'normal': 'original_normal',
                                                  'metalness_emissive_roughness': 'original_mer'}}
            authored.write_text(json.dumps(original))
            bind_block_fallbacks(pack, [5, 0, 245, 0])
            generated = json.loads((pack / 'textures/entity/missing.texture_set.json').read_text())
            self.assertEqual(generated['minecraft:texture_set']['metalness_emissive_roughness_subsurface'],
                             [5, 0, 245, 0])
            self.assertEqual(json.loads(authored.read_text()), original)
            self.assertEqual(bind_block_fallbacks(pack, [5, 0, 245, 0])['changed_files'], [])
            for name in ('missing', 'authored'):
                self.assertEqual((pack / f'textures/entity/{name}.png').read_bytes(), b'original artwork')


if __name__ == '__main__':
    unittest.main()
