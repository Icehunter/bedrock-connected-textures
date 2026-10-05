"""Tint palettes hold every colour a rule can draw with, following tile swaps, states and biomes."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from tint_palette import rule_tint_palettes, rgb8


class TintPaletteTests(unittest.TestCase):
    def test_java_logical_alias_matching_and_selected_model_identity_keep_tinted_palettes(self):
        tall_seagrass_face = {'texture': 'tall_grass.png', 'worldFace': 'north', 'tintIndex': 0}
        document = {'fullCubeBlocks': ['minecraft:stone'],
                    'nativeBlockJavaIds': {'minecraft:seagrass': 'minecraft:seagrass'},
                    'baseTextureVariants': {'minecraft:seagrass': [{
                        'states': {}, 'javaBlock': 'minecraft:tall_seagrass',
                        'modelParts': [{'faces': {'north': tall_seagrass_face}}]}]},
                    'modelTintTypes': {'minecraft:tall_seagrass': 'grass'},
                    'grassTints': {'minecraft:plains': [.2, .4, .6]},
                    'rules': [{'id': 'logical_tall', 'method': 'fixed', 'blocks': ['minecraft:tall_seagrass'],
                               'faces': ['north'], 'tiles': ['alternate.png']}]}
        self.assertEqual({rgb8(color) for color in rule_tint_palettes(document)['logical_tall']},
                         {(255, 255, 255), (51, 102, 153)},
                         'selected Java model identity overrides the native alias for matching and tint')

    def test_explicit_overlay_and_source_blocks_without_texture_bindings(self):
        document = {
            'sourceBlocks': ['minecraft:dirt'],
            'grassTints': {'minecraft:plains': [0.2, 0.4, 0.6]},
            'rules': [
                {'id': 'explicit', 'method': 'overlay_fixed', 'blocks': ['minecraft:stone'],
                 'faces': ['north'], 'tiles': ['overlay.png'], 'tintBlock': 'minecraft:grass_block', 'tintIndex': 0},
                {'id': 'source', 'method': 'overlay_fixed', 'matchTiles': ['minecraft:dirt'],
                 'faces': ['down'], 'tiles': ['overlay.png'], 'tintBlock': 'minecraft:grass_block', 'tintIndex': 0},
                {'id': 'untinted_base', 'method': 'fixed', 'blocks': ['minecraft:stone'], 'tiles': ['stone.png']},
            ],
        }
        palettes = rule_tint_palettes(document)
        for key in ('explicit', 'source'):
            self.assertEqual({rgb8(value) for value in palettes[key]}, {(255, 255, 255), (51, 102, 153)})
        self.assertEqual(palettes['untinted_base'], [[1, 1, 1]])

    def document(self):
        return {
            'fullCubeBlocks': ['minecraft:grass_block', 'minecraft:stone'],
            'baseTextures': {'minecraft:grass_block': {'up': 'grass.png', 'north': 'dirt.png'},
                             'minecraft:stone': {'up': 'stone.png'}},
            'baseTextureVariants': {'minecraft:grass_block': [
                {'states': {'snowy': False}, 'tintIndices': {'up': 0}}]},
            'modelTintTypes': {'minecraft:grass_block': 'grass'},
            'grassTints': {'minecraft:plains': [0.2, 0.4, 0.6], 'minecraft:desert': [0.8, 0.6, 0.4]},
        }

    def test_model_tint_survives_replacement_chain_and_model_fallback(self):
        document = self.document()
        document['rules'] = [
            {'id': 'first', 'method': 'random', 'matchTiles': ['grass.png'], 'tiles': ['a.png', 'b.png']},
            {'id': 'chained', 'method': 'fixed', 'matchTiles': ['b.png'], 'tiles': ['c.png']},
            {'id': 'fallback', 'method': 'fixed', 'fallback': True, 'modelFallback': True, 'tiles': ['grass.png']},
            {'id': 'untinted_side', 'method': 'fixed', 'matchTiles': ['dirt.png'], 'tiles': ['side.png']},
        ]
        palettes = rule_tint_palettes(document)
        for key in ('first', 'chained', 'fallback'):
            self.assertEqual({rgb8(value) for value in palettes[key]},
                             {(255, 255, 255), (51, 102, 153), (204, 153, 102)})
        self.assertEqual(palettes['untinted_side'], [[1, 1, 1]])

    def test_overlay_requires_explicit_tint_and_does_not_inherit_base_custom_color(self):
        document = self.document()
        document['customTintBlocks'] = ['minecraft:stone']
        document['modelTintTypes']['minecraft:stone'] = [0.5, 0.5, 0.5]
        document['rules'] = [
            {'id': 'plain_overlay', 'method': 'overlay_fixed', 'blocks': ['minecraft:stone'], 'tiles': ['overlay.png']},
            {'id': 'grass_overlay', 'method': 'overlay_fixed', 'blocks': ['minecraft:stone'],
             'tiles': ['grass_overlay.png'], 'tintBlock': 'minecraft:grass_block', 'tintIndex': 0},
            {'id': 'custom_stone', 'method': 'fixed', 'blocks': ['minecraft:stone'], 'tiles': ['gray.png']},
        ]
        palettes = rule_tint_palettes(document)
        self.assertEqual(palettes['plain_overlay'], [[1, 1, 1]])
        self.assertEqual(len(palettes['grass_overlay']), 3)
        self.assertEqual({rgb8(value) for value in palettes['custom_stone']}, {(255, 255, 255), (128, 128, 128)})

    def test_face_state_and_biome_filters_bound_palette_through_chaining(self):
        document = self.document()
        document['rules'] = [
            {'id': 'plains', 'method': 'fixed', 'matchTiles': ['grass.png'], 'tiles': ['plain.png'],
             'biomes': {'ids': ['minecraft:plains'], 'exclude': False}},
            {'id': 'later', 'method': 'fixed', 'matchTiles': ['plain.png'], 'tiles': ['later.png']},
            {'id': 'wrong_face', 'method': 'fixed', 'matchTiles': ['grass.png'], 'faces': ['north'],
             'tiles': ['a.png']},
            {'id': 'wrong_state', 'method': 'fixed', 'matchTiles': ['grass.png'], 'states': {'snowy': ['true']},
             'tiles': ['b.png']},
            {'id': 'wrong_block', 'method': 'fixed', 'matchTiles': ['grass.png'], 'blocks': ['minecraft:stone'],
             'tiles': ['c.png']},
        ]
        palettes = rule_tint_palettes(document)
        for key in ('plains', 'later'):
            self.assertEqual({rgb8(value) for value in palettes[key]}, {(255, 255, 255), (51, 102, 153)})
        for key in ('wrong_face', 'wrong_state', 'wrong_block'):
            self.assertEqual(palettes[key], [[1, 1, 1]])


if __name__ == '__main__':
    unittest.main()
