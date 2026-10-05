"""CTM tile names resolve to pack paths the way OptiFine reads them, and never leave their namespace."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from java_texture_paths import resolve_texture


class TexturePathTests(unittest.TestCase):
    def test_namespaced_paths_follow_java_sprite_locations(self):
        rule = 'assets/minecraft/optifine/ctm/a/a.properties'
        cases = {
            'minecraft:block/obsidian': 'assets/minecraft/textures/block/obsidian.png',
            'minecraft:textures/block/obsidian.png': 'assets/minecraft/textures/block/obsidian.png',
            'custom:0': 'assets/custom/textures/block/0.png',
            'minecraft:0-46': 'assets/minecraft/textures/block/0-46.png',
            'custom:optifine/ctm/surface': 'assets/custom/optifine/ctm/surface.png',
            './stone.variant': 'assets/minecraft/optifine/ctm/a/stone.variant.png',
            '~/ctm/0': 'assets/minecraft/optifine/ctm/0.png',
            'assets/minecraft/textures/block/stone': 'assets/minecraft/textures/block/stone.png'}
        for token, expected in cases.items():
            self.assertEqual(resolve_texture(token, rule), expected)

    def test_relative_sprites_keep_rule_namespace_and_reject_escapes(self):
        rule = 'assets/custom/optifine/ctm/a/a.properties'
        self.assertEqual(resolve_texture('0', rule), 'assets/custom/optifine/ctm/a/0.png')
        self.assertEqual(resolve_texture('stone', rule, True), 'assets/minecraft/textures/block/stone.png')
        escapes = ('./../../../../../outside', 'custom:../../outside', 'custom:./a', 'custom:~/a', 'custom:/a',
                   'C:\\secret')
        for token in escapes:
            with self.subTest(token=token), self.assertRaises(ValueError):
                resolve_texture(token, rule)


if __name__ == '__main__':
    unittest.main()
