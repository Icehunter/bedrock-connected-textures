"""Importing Java connected-texture rules: names, options, accounting and the compiled pack."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from import_java_ctm import import_rules
from java_materials import MaterialPolicy
from java_block_states import resolve_known_block_selection


def ctm_folder(root):
    """An author pack's stone CTM folder holding the solid tiles 0-3."""
    folder = root / 'assets/minecraft/optifine/ctm/stone'
    folder.mkdir(parents=True)
    for index in range(4):
        Image.new('RGBA', (16, 16), (index, 90, 100, 255)).save(folder / f'{index}.png')
    return folder


def pack_files(root):
    """Every file under root with its bytes, to show a pack was left untouched."""
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def save_two_frame_tile(path, color, second_color):
    """A 16x32 animation strip: color on top, second_color below."""
    image = Image.new('RGBA', (16, 32), color)
    image.paste(second_color, (0, 16, 16, 32))
    image.save(path)


class JavaImportTests(unittest.TestCase):
    def test_split_native_ids_preserve_unfiltered_java_block_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = ctm_folder(root)
            (folder / 'a.properties').write_text('matchBlocks=redstone_ore\nmethod=fixed\ntiles=0')
            rule = import_rules(root, {}, block_state_resolver=resolve_known_block_selection)['rules'][0]
            self.assertEqual(set(rule['blocks']), {'minecraft:redstone_ore', 'minecraft:lit_redstone_ore'})

    def test_split_native_ids_keep_lit_and_facing_conditions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = ctm_folder(root)
            (folder / 'a.properties').write_text('matchBlocks=furnace:lit=true:facing=west\nmethod=fixed\ntiles=0')
            rule = import_rules(root, {}, block_state_resolver=resolve_known_block_selection)['rules'][0]
            self.assertEqual(rule['blocks'], ['minecraft:lit_furnace'])
            self.assertEqual(rule['blockMatchers'],
                             [{'block': 'minecraft:lit_furnace', 'states': {'minecraft:cardinal_direction': ['west']}}])

    def test_overlay_connection_blocks_include_native_lit_alternative(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = ctm_folder(root)
            (folder / 'a.properties').write_text(
                'matchBlocks=stone\nconnectBlocks=redstone_ore\nmethod=overlay_fixed\ntiles=0')
            rule = import_rules(root, {}, block_state_resolver=resolve_known_block_selection)['rules'][0]
            self.assertEqual(set(rule['connectBlocks']), {'minecraft:redstone_ore', 'minecraft:lit_redstone_ore'})

    def test_biome_tokens_do_not_change_random_weight_tile_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = ctm_folder(root)
            (folder / 'a.properties').write_text(
                'matchBlocks=stone\nmethod=random\ntiles=0-3\nweights=3 1\nbiomes=forest')
            rule = import_rules(root, {}, biome_names={'forest': 'minecraft:forest'})['rules'][0]
            self.assertEqual(rule['weights'], [3, 1, 2, 2])

    def test_complete_biome_union_collapses_but_individual_sub_biome_does_not(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = ctm_folder(root) / 'a.properties'
            path.write_text('matchBlocks=stone\nmethod=fixed\ntiles=0\nbiomes=!the_end end_highlands')
            union = [{'source': ['the_end', 'end_highlands'], 'target': ['minecraft:the_end']}]
            rule = import_rules(root, {}, biome_unions=union)['rules'][0]
            self.assertEqual(rule['biomes'], {'ids': ['minecraft:the_end'], 'exclude': True})
            path.write_text(path.read_text().replace('the_end end_highlands', 'end_highlands'))
            with self.assertRaisesRegex(ValueError, 'explicit Bedrock biome mapping'):
                import_rules(root, {}, biome_unions=union)

    def test_overlay_rules_keep_all_tiles_options_and_alpha(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = ctm_folder(root)
            for index in range(17):
                Image.new('RGBA', (16, 16), (30, 40, 50, index * 15)).save(folder / f'{index}.png')
            (folder / 'a.properties').write_text(
                'matchBlocks=stone\nmethod=overlay\ntiles=0-16\nconnectTiles=grass_block_top\n'
                'connectBlocks=grass_block\nlayer=cutout\ntintIndex=0\ntintBlock=grass_block')
            rule = import_rules(root, {})['rules'][0]
            self.assertEqual(rule['method'], 'overlay')
            self.assertEqual(len(rule['tiles']), 17)
            self.assertEqual(rule['connectTiles'], ['assets/minecraft/textures/block/grass_block_top.png'])
            self.assertEqual(rule['connectBlocks'], ['minecraft:grass_block'])
            self.assertEqual(rule['tintIndex'], 0)

    def test_dialect_exclusions_and_java_ignored_fields_have_accounting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = ctm_folder(root)
            (folder / 'a.properties').write_text('matchBlocks=stone\nmethod=fixed\ntiles=0\noptifineOnly=true')
            (folder / 'b.properties').write_text(
                'matchBlocks=andesite\nmethod=fixed\ntiles=1\n//matchBlocks=stone\nlayer=transparency\ntintIndex=1')
            optifine = import_rules(root, {}, dialect='optifine')
            continuity = import_rules(root, {}, dialect='continuity')
            self.assertEqual(len(optifine['rules']), 2)
            self.assertEqual(len(continuity['rules']), 1)
            self.assertEqual(len(continuity['importAccounting']['excludedRules']), 1)
            self.assertEqual(len(optifine['importAccounting']['ignoredProperties']), 3)

    def test_state_axis_repeat_is_retained_and_texture_orientation_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = ctm_folder(root) / 'a.properties'
            path.write_text('matchBlocks=basalt\nmethod=repeat\nwidth=2\nheight=2\ntiles=0-3\norient=state_axis')
            document = import_rules(root, {})
            self.assertEqual(document['rules'][0]['orient'], 'state_axis')
            path.write_text(path.read_text().replace('state_axis', 'texture'))
            with self.assertRaisesRegex(ValueError, 'orientation mode'):
                import_rules(root, {})

    def test_failed_late_rule_leaves_author_pack_and_output_untouched(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'input'
            output = Path(temporary) / 'output'
            folder = ctm_folder(root)
            Image.new('RGBA', (16, 16), (4, 90, 100, 255)).save(folder / '4.png')
            (folder / 'a.properties').write_text('matchBlocks=stone\nmethod=ctm_compact\ntiles=0-4\n')
            (folder / 'z.properties').write_text('matchBlocks=stone\nmethod=not_supported\ntiles=0\n')
            original = pack_files(root)
            with self.assertRaises(ValueError):
                import_rules(root, {}, compiled_pack=output)
            self.assertFalse(output.exists())
            self.assertEqual(original, pack_files(root))
            self.assertEqual(list(Path(temporary).glob('.ctm-import-*')), [])
            with self.assertRaisesRegex(ValueError, 'separate'):
                import_rules(root, {}, compiled_pack=root / 'bad-output')
            self.assertFalse((root / 'bad-output').exists())

    def test_java_animated_compact_material_maps_decode_before_expansion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'input'
            output = Path(temporary) / 'output'
            folder = ctm_folder(root)
            for index in range(5):
                save_two_frame_tile(folder / f'{index}.png', (index, 10, 20, 255), (index + 100, 10, 20, 255))
                (folder / f'{index}.png.mcmeta').write_text('{"animation":{"frametime":2,"interpolate":true}}')
                material = Image.new('RGBA', (16, 32), (0, 10, 0, 255))
                material.paste((255, 10, 0, 254), (0, 16, 16, 32))
                material.save(folder / f'{index}_s.png')
            (folder / 'a.properties').write_text('matchBlocks=stone\nmethod=ctm_compact\ntiles=0-4\nctm.0=2\n')
            (folder / 'b.properties').write_text('matchBlocks=andesite\nmethod=fixed\ntiles=0\n')
            document = import_rules(root, {}, compiled_pack=output,
                                    material_policy=MaterialPolicy('labpbr-1.3', 'directx', 'linear'))
            tile = output / (document['rules'][0]['tiles'][0] + '.png')
            descriptor = json.loads(tile.with_suffix('.texture_set.json').read_text())['minecraft:texture_set']
            material = tile.parent / (descriptor['metalness_emissive_roughness_subsurface'] + '.png')
            with Image.open(material) as image:
                self.assertEqual(image.getpixel((0, 16)), (0, 0, 64, 0))
            with Image.open(tile) as image:
                self.assertEqual(image.getpixel((0, 16)), (52, 10, 20, 255))
            self.assertEqual(len(document['materialReceipts']), 5)
            ordinary = output / document['rules'][1]['tiles'][0]
            self.assertEqual(json.loads(Path(str(ordinary) + '.mcmeta').read_text())['animation']['frames'],
                             [0, 1, 2, 3])

    def test_compiled_copy_keeps_animation_sidecars(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'input'
            output = Path(temporary) / 'output'
            folder = ctm_folder(root)
            Image.new('RGBA', (16, 32), (50, 60, 70, 255)).save(folder / '0.png')
            (folder / '0.png.mcmeta').write_text('{"animation":{"frametime":2}}')
            (folder / 'a.properties').write_text('matchBlocks=stone\nmethod=fixed\ntiles=0\n')
            document = import_rules(root, {}, compiled_pack=output)
            path = output / document['rules'][0]['tiles'][0]
            self.assertEqual(Path(str(path) + '.mcmeta').read_bytes(), (folder / '0.png.mcmeta').read_bytes())

    def test_chaining_paths_weights_and_source_faces(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = ctm_folder(root)
            (folder / 'a.properties').write_text('matchTiles=stone\nmethod=horizontal\nfaces=sides\ntiles=0-3\n')
            (folder / 'b.properties').write_text(
                'matchTiles=optifine/ctm/stone/1.png\nmethod=random\ntiles=2 3\nweights=7\n'
                'symmetry=opposite\nrandomLoops=2\n')
            base = {'minecraft:stone': {'north': 'assets/minecraft/textures/block/stone.png'}}
            document = import_rules(root, base)
            self.assertEqual(document['sourceBlocks'], ['minecraft:stone'])
            self.assertEqual(document['rules'][0]['blocks'], [])
            self.assertEqual(document['rules'][0]['faces'], ['north', 'east', 'south', 'west'])
            self.assertEqual(document['rules'][1]['matchTiles'], ['assets/minecraft/optifine/ctm/stone/1.png'])
            self.assertEqual(document['rules'][1]['weights'], [7, 7])

    def test_biomes_require_explicit_mapping_and_keep_negation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = ctm_folder(root)
            (folder / 'a.properties').write_text(
                'matchBlocks=stone\nmethod=fixed\ntiles=0\nbiomes=!snowy_plains forest\n')
            with self.assertRaises(ValueError):
                import_rules(root, {})
            biome_names = {'snowy_plains': 'minecraft:ice_plains',
                           'forest': ['minecraft:forest', 'minecraft:forest_hills']}
            rule = import_rules(root, {}, biome_names=biome_names)['rules'][0]
            self.assertEqual(rule['biomes'],
                             {'ids': ['minecraft:ice_plains', 'minecraft:forest', 'minecraft:forest_hills'],
                              'exclude': True})

    def test_state_clauses_keep_block_specific_alternatives(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = ctm_folder(root)
            (folder / 'a.properties').write_text(
                'matchBlocks=oak_log:axis=x,z minecraft:spruce_log:axis=y stone\nmethod=fixed\ntiles=0\n')
            axis = {'axis': {'name': 'pillar_axis', 'values': {'x': 'x', 'y': 'y', 'z': 'z'}}}
            state_names = {block: axis for block in ('minecraft:oak_log', 'minecraft:spruce_log')}
            rule = import_rules(root, {}, state_names=state_names)['rules'][0]
            self.assertEqual(rule['blockMatchers'], [
                {'block': 'minecraft:oak_log', 'states': {'pillar_axis': ['x', 'z']}},
                {'block': 'minecraft:spruce_log', 'states': {'pillar_axis': ['y']}},
                {'block': 'minecraft:stone', 'states': {}}])

    def test_unsupported_rules_fail_instead_of_approximating(self):
        options = ('method=overlay', 'method=ctm_compact', 'method=fixed\nbiomes=plains',
                   'method=fixed\nmatchBlocks=oak_log:axis=x')
        for option in options:
            with self.subTest(option=option), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                folder = ctm_folder(root)
                (folder / 'a.properties').write_text('matchBlocks=stone\ntiles=0\n' + option)
                with self.assertRaises(ValueError):
                    import_rules(root, {})

    def test_bricks_map_to_bedrock_and_negative_height_ranges_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = ctm_folder(root)
            (folder / 'a.properties').write_text(
                'matchBlocks=bricks\nmethod=fixed\ntiles=0\nheights=(-64)-(-1) 0-319\n')
            rule = import_rules(root, {})['rules'][0]
            self.assertEqual(rule['blocks'], ['minecraft:brick_block'])
            self.assertEqual(rule['heights'], [[-64, -1], [0, 319]])

    def test_compact_import_preserves_material_quadrants_and_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'input'
            output = Path(temporary) / 'output'
            folder = ctm_folder(root)
            Image.new('RGBA', (16, 16), (4, 90, 100, 255)).save(folder / '4.png')
            for index in range(5):
                Image.new('RGB', (16, 16), (128, 128, 200 + index)).save(folder / f'{index}_normal.png')
                texture_set = {'minecraft:texture_set': {'color': str(index), 'normal': f'{index}_normal'}}
                (folder / f'{index}.texture_set.json').write_text(json.dumps(texture_set))
            (folder / 'a.properties').write_text('matchBlocks=stone\nmethod=ctm_compact\ntiles=0-4\n')
            (folder / 'b.properties').write_text('matchBlocks=andesite\nmethod=fixed\ntiles=0\n')
            original = pack_files(root)
            document = import_rules(root, {}, compiled_pack=output)
            rule = document['rules'][0]
            self.assertEqual(rule['method'], 'ctm')
            self.assertEqual(len(rule['tiles']), 47)
            self.assertEqual((output / 'assets/minecraft/optifine/ctm/stone/0.png').read_bytes(),
                             (folder / '0.png').read_bytes())
            for tile in rule['tiles']:
                path = output / (tile + '.png')
                color = Image.open(path)
                normal = Image.open(path.with_name(path.stem + '_normal.png'))
                # Each quadrant keeps the normal map of the tile its colour came from.
                for coordinate in ((0, 0), (15, 0), (0, 15), (15, 15)):
                    self.assertEqual(normal.getpixel(coordinate)[2], 200 + color.getpixel(coordinate)[0])
            self.assertEqual(original, pack_files(root))

    def test_animated_compact_import_retains_compiled_timing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'input'
            output = Path(temporary) / 'output'
            folder = ctm_folder(root)
            for index in range(5):
                save_two_frame_tile(folder / f'{index}.png', (index, 10, 20, 255), (index + 100, 10, 20, 255))
                (folder / f'{index}.png.mcmeta').write_text(
                    json.dumps({'animation': {'frametime': 3, 'frames': [1, 0]}}))
            (folder / 'a.properties').write_text('matchBlocks=stone\nmethod=ctm_compact\ntiles=0-4\nctm.0=2\n')
            document = import_rules(root, {}, compiled_pack=output)
            tile = output / (document['rules'][0]['tiles'][0] + '.png')
            with Image.open(tile) as image:
                self.assertEqual(image.getpixel((0, 0)), (102, 10, 20, 255))
            metadata = json.loads(Path(str(tile) + '.mcmeta').read_text())['animation']
            self.assertEqual(metadata['frametime'], 3)
            self.assertEqual(metadata['frames'], [0, 1])


if __name__ == '__main__':
    unittest.main()
