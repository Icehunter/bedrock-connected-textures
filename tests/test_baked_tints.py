"""Java block tints Bedrock does not apply are baked in; Bedrock's own tinting and untinted faces stay."""
import io
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from baked_tints import bake, parse_fixed_colormaps, plan_tints

SPRITE = 'assets/minecraft/textures/block/'


def png(color):
    buffer = io.BytesIO()
    Image.new('RGBA', (4, 4), color).save(buffer, 'PNG')
    return buffer.getvalue()


def first_pixel(path):
    with Image.open(path) as image:
        return image.getpixel((0, 0))


class BakedTintTests(unittest.TestCase):
    def test_fixed_colormaps_are_read_from_the_authors_files(self):
        files = {'assets/minecraft/optifine/colormap/blocks/wool/red.properties':
                 b'blocks=red_wool red_carpet\nformat=fixed\ncolor=a12723\n',
                 'assets/minecraft/optifine/colormap/blocks/grass.properties': b'blocks=grass_block\nformat=vanilla\n'}
        self.assertEqual(parse_fixed_colormaps(files),
                         {'minecraft:red_wool': (0xA1, 0x27, 0x23), 'minecraft:red_carpet': (0xA1, 0x27, 0x23)})

    def test_rules(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            vanilla = root / 'vanilla'
            rp = root / 'rp'
            for pack in (vanilla, rp):
                (pack / 'textures/blocks').mkdir(parents=True)
            # vanilla Bedrock: white wool grey (but not tinted by the game), reeds already green,
            # leaves grey (the game tints them), dirt brown
            for name, color in (('wool_colored_white', (233, 236, 237, 255)), ('reeds', (90, 160, 60, 255)),
                                ('leaves_oak', (120, 120, 120, 255)), ('dirt', (130, 90, 60, 255))):
                Image.new('RGBA', (4, 4), color).save(vanilla / f'textures/blocks/{name}.png')
            # the author's textures, grey and made to be tinted (dirt is brown, untinted in Java)
            for name in ('wool_colored_white', 'reeds', 'leaves_oak'):
                Image.new('RGBA', (4, 4), (200, 200, 200, 255)).save(rp / f'textures/blocks/{name}.png')
            Image.new('RGBA', (4, 4), (200, 200, 200, 255)).save(rp / 'textures/blocks/wool_colored_white_v0.png')
            Image.new('RGBA', (4, 4), (130, 90, 60, 255)).save(rp / 'textures/blocks/dirt.png')
            bindings = {
                'materialBindings': {'textures/blocks/wool_colored_white': SPRITE + 'white_wool.png',
                                     'textures/blocks/reeds': SPRITE + 'sugar_cane.png',
                                     'textures/blocks/leaves_oak': SPRITE + 'oak_leaves.png',
                                     'textures/blocks/dirt': SPRITE + 'dirt.png',
                                     'textures/blocks/concrete_light_blue': SPRITE + 'light_blue_concrete.png'},
                'baseTextures': {'minecraft:white_wool': {'up': SPRITE + 'white_wool.png'},
                                 'minecraft:oak_leaves': {'up': SPRITE + 'oak_leaves.png'},
                                 'minecraft:grass_block': {'up': SPRITE + 'grass_block_top.png',
                                                           'down': SPRITE + 'dirt.png'},
                                 # the author draws light blue concrete with the blue texture
                                 'minecraft:light_blue_concrete': {'up': SPRITE + 'blue_concrete.png'}},
                'modelTintTypes': {'minecraft:sugar_cane': 'grass', 'minecraft:oak_leaves': 'foliage',
                                   'minecraft:grass_block': 'grass'},
            }
            java = {SPRITE + 'sugar_cane.png': png((128, 128, 128, 255)), SPRITE + 'dirt.png': png((130, 90, 60, 255)),
                    SPRITE + 'blue_concrete.png': png((40, 40, 160, 255))}
            author = {'minecraft:white_wool': (233, 236, 237), 'minecraft:light_blue_concrete': (102, 153, 216)}
            plan, report = plan_tints(bindings, author, vanilla, java.get)
            self.assertEqual(set(plan), {'textures/blocks/wool_colored_white', 'textures/blocks/reeds',
                                         'textures/blocks/concrete_light_blue'})
            self.assertEqual(report, [])
            self.assertEqual(plan['textures/blocks/concrete_light_blue'][3], SPRITE + 'blue_concrete.png')
            baked = bake(rp, plan, java.get)
            self.assertIn('textures/blocks/wool_colored_white_v0.png', baked, 'random variant tiles are tinted too')
            self.assertEqual(first_pixel(rp / 'textures/blocks/wool_colored_white.png'),
                             (200 * 233 // 255, 200 * 236 // 255, 200 * 237 // 255, 255))
            self.assertEqual(first_pixel(rp / 'textures/blocks/reeds.png'),
                             (200 * 0x91 // 255, 200 * 0xBD // 255, 200 * 0x59 // 255, 255))
            self.assertEqual(first_pixel(rp / 'textures/blocks/concrete_light_blue.png'),
                             (40 * 102 // 255, 40 * 153 // 255, 160 * 216 // 255, 255))
            self.assertEqual(first_pixel(rp / 'textures/blocks/leaves_oak.png'), (200, 200, 200, 255),
                             'Bedrock tints leaves itself')
            self.assertEqual(first_pixel(rp / 'textures/blocks/dirt.png'), (130, 90, 60, 255),
                             "a grass block's dirt bottom stays untinted")


if __name__ == '__main__':
    unittest.main()
