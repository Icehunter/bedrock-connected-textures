import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from java_model_tints import resolve_model_tints, copy_model_tint_images, apply_native_biome_tints

# Biome effects in the stand-in client jar: Java's grass modifiers and the fixed colours that replace the colormap.
BIOME_EFFECTS = {
    'plains': {},
    'dark_forest': {'grass_color_modifier': 'dark_forest'},
    'swamp': {'grass_color_modifier': 'swamp', 'foliage_color': '#6a7039'},
    'pale_garden': {'grass_color': '#778272'},
}


def png(color):
    stream = io.BytesIO()
    Image.new('RGB', (256, 256), color).save(stream, format='PNG')
    return stream.getvalue()


class Stack:
    def __init__(self, files):
        self.files = files

    def read(self, path):
        return self.files[path]


class ModelTintTests(unittest.TestCase):
    def reference(self, directory):
        path = Path(directory) / 'client.jar'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('assets/minecraft/textures/colormap/grass.png', png((100, 150, 200)))
            archive.writestr('assets/minecraft/textures/colormap/foliage.png', png((50, 75, 100)))
            for biome, effects in BIOME_EFFECTS.items():
                climate = {'temperature': 0.8, 'downfall': 0.4, 'effects': effects}
                archive.writestr('data/minecraft/worldgen/biome/' + biome + '.json', json.dumps(climate))
        return path

    def test_generic_tintindex_does_not_imply_a_grass_provider(self):
        with tempfile.TemporaryDirectory() as folder:
            bindings = {'fullCubeBlocks': ['minecraft:stone'],
                        'baseTextureVariants': {'minecraft:stone': [{'tintIndices': {'north': 0}}]}}
            result = resolve_model_tints(Stack({}), self.reference(folder), bindings)
        self.assertNotIn('minecraft:stone', result['modelTintTypes'])
        self.assertEqual(result['modelTintTypes']['minecraft:grass_block'], 'grass')
        self.assertEqual(result['modelTintTypes']['minecraft:oak_leaves'], 'foliage')
        self.assertEqual(result['modelTintTypes']['minecraft:spruce_leaves'], [0.380392, 0.6, 0.380392])
        self.assertEqual(result['tintAudit']['unsupported'], [])

    def test_effective_source_colormap_and_java_biome_override_are_used(self):
        with tempfile.TemporaryDirectory() as folder:
            stack = Stack({'assets/minecraft/textures/colormap/grass.png': png((255, 0, 0))})
            result = resolve_model_tints(stack, self.reference(folder), {},
                                         {'biomeNames': {'plains': ['minecraft:test_plains']}})
        self.assertEqual(result['grassTints']['minecraft:plains'], [1.0, 0.0, 0.0])
        self.assertEqual(result['grassTints']['minecraft:test_plains'], [1.0, 0.0, 0.0])
        self.assertEqual(result['grassTints']['minecraft:pale_garden'], [0.466667, 0.509804, 0.447059])
        sources = result['tintSourceImages']
        self.assertEqual(sources['assets/minecraft/textures/colormap/grass.png']['origin'], 'pack_stack')
        self.assertEqual(sources['assets/minecraft/textures/colormap/foliage.png']['origin'], 'vanilla_reference')

    def test_fixed_optifine_palette_keeps_native_aliases_without_needing_an_image(self):
        with tempfile.TemporaryDirectory() as folder:
            masonry = b'blocks=bricks terracotta\nformat=fixed\ncolor=ff8040'
            stack = Stack({'assets/minecraft/optifine/colormap/blocks/masonry.properties': masonry})
            bindings = {'fullCubeBlocks': ['minecraft:brick_block', 'minecraft:hardened_clay']}
            result = resolve_model_tints(stack, self.reference(folder), bindings)
        self.assertEqual(result['modelTintTypes']['minecraft:brick_block'], [1.0, 0.501961, 0.25098])
        self.assertEqual(result['customTintBlocks'], ['minecraft:brick_block', 'minecraft:hardened_clay'])
        self.assertEqual(result['customTints'], {})

    def test_palette_block_climate_map_applies_to_cubes_without_model_tint_indices(self):
        with tempfile.TemporaryDirectory() as folder:
            color_properties = b'palette.block.~/colormap/blocktint.png=stone stone_stairs\nlilypad=4d993d'
            stack = Stack({'assets/minecraft/optifine/color.properties': color_properties,
                           'assets/minecraft/optifine/colormap/blocktint.png': png((237, 255, 202))})
            result = resolve_model_tints(stack, self.reference(folder), {'fullCubeBlocks': ['minecraft:stone']})
        provider = result['modelTintTypes']['minecraft:stone']
        self.assertTrue(provider.startswith('custom:'))
        self.assertEqual(result['customTints'][provider]['minecraft:plains'], [0.929412, 1.0, 0.792157])
        self.assertEqual(result['customTintBlocks'], ['minecraft:stone'])
        self.assertEqual(result['modelTintTypes']['minecraft:waterlily'], [0.301961, 0.6, 0.239216])

    def test_swamp_modifier_and_dark_forest_packed_integer_color_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            result = resolve_model_tints(Stack({}), self.reference(folder), {})
        self.assertEqual(result['grassTints']['minecraft:dark_forest'], [0.27451, 0.396078, 0.411765])
        self.assertEqual(result['grassTintModifiers']['minecraft:swampland']['below'], [0.298039, 0.462745, 0.235294])
        self.assertEqual(result['grassTintModifiers']['minecraft:swampland']['threshold'], -0.1)
        self.assertEqual(result['foliageTints']['minecraft:swampland'], [0.415686, 0.439216, 0.223529])

    def test_unsupported_grid_and_state_selectors_are_reported_without_global_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            colormaps = 'assets/minecraft/optifine/colormap/blocks/'
            stack = Stack({
                colormaps + 'grid.properties': b'blocks=stone\nformat=grid',
                colormaps + 'state.properties': b'blocks=redstone_ore:lit=true\nformat=fixed\ncolor=ff0000',
            })
            result = resolve_model_tints(stack, self.reference(folder), {})
        self.assertNotIn('minecraft:stone', result['modelTintTypes'])
        self.assertNotIn('minecraft:redstone_ore', result['modelTintTypes'])
        reasons = {issue['reason'] for issue in result['tintAudit']['unsupported']}
        self.assertEqual(reasons, {'unsupported_colormap_format', 'state_or_legacy_id_colormap_requires_selector'})

    def test_source_map_copy_preserves_effective_bytes_and_rejects_changed_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            jar = self.reference(folder)
            name = 'assets/minecraft/textures/colormap/grass.png'
            source = png((255, 0, 0))
            stack = Stack({name: source})
            metadata = resolve_model_tints(stack, jar, {})
            copy_model_tint_images(stack, jar, Path(folder) / 'out', metadata)
            self.assertEqual((Path(folder) / 'out/textures/colormap/grass.png').read_bytes(), source)
            stack.files[name] = png((0, 255, 0))
            with self.assertRaisesRegex(ValueError, 'changed after tint resolution'):
                copy_model_tint_images(stack, jar, Path(folder) / 'out', metadata)

    def test_native_biome_colors_use_exact_resolved_bytes_and_preserve_scene(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'resource_pack'
            path = root / 'biomes' / 'authored-name.client_biome.json'
            path.parent.mkdir(parents=True)
            scene = {'minecraft:lighting_identifier': {'lighting_identifier': 'author:lighting'},
                     'minecraft:water_appearance': {'surface_color': '#123456'},
                     'minecraft:ambient_sounds': {'ambient_sound': 'forest'},
                     'minecraft:grass_appearance': {'color': {'color_map': 'swamp_grass'}},
                     'minecraft:foliage_appearance': {'color': {'color_map': 'swamp_foliage'}}}
            path.write_text(json.dumps({'format_version': '1.21.120', 'minecraft:client_biome': {
                'description': {'identifier': 'minecraft:swampland'}, 'components': scene}}))
            metadata = resolve_model_tints(Stack({}), self.reference(folder), {})
            receipt = apply_native_biome_tints(root, metadata)
            result = json.loads(path.read_text())
            components = result['minecraft:client_biome']['components']
            self.assertEqual(result['format_version'], '1.21.120')
            self.assertEqual(components['minecraft:grass_appearance'], {'color': '#6A7039'})
            self.assertEqual(components['minecraft:foliage_appearance'], {'color': '#6A7039'})
            for key in scene.keys() - {'minecraft:grass_appearance', 'minecraft:foliage_appearance'}:
                self.assertEqual(components[key], scene[key])
            plains = json.loads((root / 'biomes' / 'plains.client_biome.json').read_text())
            self.assertEqual(plains['minecraft:client_biome']['components']['minecraft:grass_appearance'],
                             {'color': '#6496C8'})
            self.assertEqual(plains['minecraft:client_biome']['components']['minecraft:foliage_appearance'],
                             {'color': '#324B64'})
            self.assertIn('native_fixed_colors_do_not_reproduce_java_swamp_coordinate_noise',
                          {entry['reason'] for entry in receipt['limitations']})
            self.assertIn('native_biome_boundary_interpolation_can_differ_from_java',
                          {entry['reason'] for entry in receipt['limitations']})
            self.assertEqual(apply_native_biome_tints(root, metadata)['changed_files'], [])

    def test_native_biome_schema_upgrade_aliases_and_leaf_particle_color_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'biomes' / 'plains.client_biome.json'
            path.parent.mkdir()
            path.write_text(json.dumps({'format_version': '1.21.30', 'minecraft:client_biome': {
                'description': {'identifier': 'minecraft:plains'}, 'components': {}}}))
            particle = root / 'particles' / 'biome_tinted_leaves_particle.json'
            particle.parent.mkdir()
            particle_bytes = (b'{"minecraft:particle_appearance_tinting":'
                              b'{"color":["variable.color.r","variable.color.g","variable.color.b",1]}}')
            particle.write_bytes(particle_bytes)
            receipt = apply_native_biome_tints(root, {'foliageTints': {
                'minecraft:plains': [1, 0, 0], 'author:test_biome': [0, 1, 0]}})
            self.assertEqual(json.loads(path.read_text())['format_version'], '1.21.40')
            alias = root / 'biomes' / 'author' / 'test_biome.client_biome.json'
            self.assertEqual(json.loads(alias.read_text())['minecraft:client_biome']['description'],
                             {'identifier': 'author:test_biome'})
            self.assertEqual(particle.read_bytes(), particle_bytes)
            self.assertFalse(receipt['native_particle_color_modified'])

    def test_native_biome_invalid_table_does_not_partially_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for invalid in ([1, 2, 0], [float('nan'), 0, 0], [0, 0], [True, 0, 0]):
                with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'normalized RGB'):
                    apply_native_biome_tints(root, {'grassTints': {
                        'minecraft:plains': [1, 0, 0], 'minecraft:swampland': invalid}})
                self.assertFalse((root / 'biomes').exists())
            with self.assertRaisesRegex(ValueError, 'Invalid native biome identifier'):
                apply_native_biome_tints(root, {'grassTints': {'minecraft:../escape': [0, 0, 0]}})

    def test_unqualified_existing_biome_identifier_matches_minecraft_namespace(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'biomes' / 'bamboo_jungle.client_biome.json'
            path.parent.mkdir()
            path.write_text(json.dumps({'format_version': '1.21.120', 'minecraft:client_biome': {
                'description': {'identifier': 'bamboo_jungle'}, 'components': {
                    'minecraft:lighting_identifier': {'lighting_identifier': 'author:forest'}}}}))
            receipt = apply_native_biome_tints(root, {
                'grassTints': {'minecraft:bamboo_jungle': [100 / 255, 150 / 255, 200 / 255]}})
            document = json.loads(path.read_text())['minecraft:client_biome']
            self.assertEqual(document['description']['identifier'], 'bamboo_jungle')
            self.assertEqual(document['components']['minecraft:grass_appearance'], {'color': '#6496C8'})
            self.assertEqual(receipt['biomes'], 1)
            self.assertEqual(len(list((root / 'biomes').rglob('*.json'))), 1)


if __name__ == '__main__':
    unittest.main()
