import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from java_native_fallbacks import select_native_fallbacks

FACES = ['north', 'east', 'south', 'west', 'up', 'down']


class NativeFallbackTests(unittest.TestCase):
    def test_compiled_compact_tile_stays_a_runtime_material(self):
        bindings = {'baseTextures': {'minecraft:glass': {'up': 'glass.png'}},
                    'authoredMaterialBindings': {'textures/blocks/glass': 'glass.png'}}
        document = {'rules': [{'id': 'glass', 'method': 'fixed', 'blocks': [], 'matchTiles': ['glass.png'],
                               'faces': FACES, 'tiles': ['textures/compact_tiles/glass/0']}]}
        with tempfile.TemporaryDirectory() as directory:
            report = select_native_fallbacks(bindings, document, directory)
        self.assertEqual(report['native_material_replacements'], {})
        self.assertEqual(report['runtime_only_compiled_previews'], {'glass.png': 'textures/compact_tiles/glass/0'})

    def test_java_warning_base_uses_authored_repeat_art_without_changing_selectors(self):
        bindings = {'baseTextures': {'minecraft:grass_block': {'up': 'warning.png'}},
                    'authoredMaterialBindings': {'textures/blocks/grass_top': 'warning.png'}}
        original = copy.deepcopy(bindings['baseTextures'])
        document = {'rules': [{'id': 'grass', 'method': 'repeat', 'blocks': [], 'matchTiles': ['warning.png'],
                               'faces': FACES, 'tiles': ['grass0.png', 'grass1.png', 'grass2.png', 'grass3.png'],
                               'width': 2, 'height': 2}]}
        with tempfile.TemporaryDirectory() as directory:
            report = select_native_fallbacks(bindings, document, directory)
        self.assertEqual(bindings['baseTextures'], original)
        self.assertEqual(report['native_material_replacements']['textures/blocks/grass_top'], 'grass0.png')

    def test_biome_only_replacement_does_not_leak_into_native_base(self):
        bindings = {'baseTextures': {'minecraft:stone': {'up': 'stone.png'}},
                    'authoredMaterialBindings': {'textures/blocks/stone': 'stone.png'}}
        document = {'rules': [{'id': 'snow', 'method': 'fixed', 'blocks': [], 'matchTiles': ['stone.png'],
                               'faces': FACES, 'tiles': ['snow.png'],
                               'biomes': {'ids': ['minecraft:snowy_plains'], 'exclude': False}}]}
        with tempfile.TemporaryDirectory() as directory:
            report = select_native_fallbacks(bindings, document, directory)
        self.assertEqual(report['native_material_replacements'], {})

    def test_replacement_chaining_uses_terminal_authored_tile(self):
        bindings = {'baseTextures': {'minecraft:stone': {'up': 'warning.png'}},
                    'authoredMaterialBindings': {'textures/blocks/stone': 'warning.png'}}
        document = {'rules': [{'id': 'first', 'method': 'fixed', 'blocks': [], 'matchTiles': ['warning.png'],
                               'faces': FACES, 'tiles': ['middle.png']},
                              {'id': 'last', 'method': 'fixed', 'blocks': [], 'matchTiles': ['middle.png'],
                               'faces': FACES, 'tiles': ['stone.png']}]}
        with tempfile.TemporaryDirectory() as directory:
            report = select_native_fallbacks(bindings, document, directory)
        self.assertEqual(report['native_material_replacements']['textures/blocks/stone'], 'stone.png')


if __name__ == '__main__':
    unittest.main()
