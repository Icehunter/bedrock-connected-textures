"""Build a diagnostic pack of labelled test tiles, one rule per method.

Each tile shows its rule and number and an arrow pointing up, so a test world shows which
tile the engine picked and which way it is turned. Tiles of the 47-tile method also get a
black border on each open side and a black dot in each inner corner, to check the
engine's choice against the tile's neighbour mask.
"""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

from common import write_json
from connected_build import MASKS, ROOT, build_connected

# (rule id, method, block, number of tiles)
DEMO_RULES = (
    ('connected', 'ctm', 'sandstone', 47),
    ('pattern', 'repeat', 'brick_block', 6),
    ('horizontal', 'horizontal', 'oak_planks', 4),
    ('vertical', 'vertical', 'cobblestone', 4),
    ('variants', 'random', 'stone_bricks', 4),
)
TILE_SIZE = 256
UP_ARROW = [(128, 60), (128, 15), (112, 31), (128, 15), (144, 31)]
# Mask bits 0 to 3 are the up, right, down and left neighbours; bit k + 4 is the corner
# between side k and side k + 1.
SIDE_LINES = ((0, 0, 255, 0), (255, 0, 255, 255), (0, 255, 255, 255), (0, 0, 0, 255))
CORNER_CENTRES = ((240, 15), (240, 240), (15, 240), (15, 15))


def create_demo(root=ROOT, samples=None):
    """Write the tiles and rules.json under build/demo-input, then build the diagnostic pack."""
    pack = root / 'build/demo-input'
    pack.mkdir(parents=True, exist_ok=True)
    masks_by_tile = _masks_in_tile_order(root)
    rules = []
    for rule_id, method, block, count in DEMO_RULES:
        tiles = []
        for index in range(count):
            mask = masks_by_tile[index] if method == 'ctm' else None
            tile = f'{rule_id}_{index}.png'
            _labelled_tile(rule_id, index, mask).save(pack / tile)
            tiles.append(tile)
        rule = {'id': rule_id, 'method': method, 'blocks': ['minecraft:' + block], 'tiles': tiles}
        if method == 'repeat':
            rule.update(width=3, height=2)
        rules.append(rule)
    rule_file = pack / 'rules.json'
    write_json(rule_file, {'format_version': 1, 'rules': rules})
    return build_connected(pack, rule_file, 'diagnostic', root, samples)


def _masks_in_tile_order(root):
    """Every neighbour mask, ordered by the template tile the engine draws for it."""
    template = (root / 'engine/tile-template.mjs').read_text()
    tile_of_mask = json.loads(template.split('Object.freeze(', 1)[1].split(');', 1)[0])
    return sorted(MASKS, key=lambda mask: tile_of_mask[str(mask)])


def _labelled_tile(rule_id, index, mask=None):
    """A tile with its label and up arrow; with a mask, its open sides and inner corners too."""
    shade = (60 + index * 29 % 120, 60 + index * 43 % 120, 60 + index * 53 % 120)
    image = Image.new('RGB', (TILE_SIZE, TILE_SIZE), shade)
    draw = ImageDraw.Draw(image)
    draw.text((24, 100), f'{rule_id}\ntile {index}', fill='white', font_size=24)
    draw.line(UP_ARROW, fill='white', width=4)
    if mask is not None:
        for side, line in enumerate(SIDE_LINES):
            if not mask & (1 << side):
                draw.line(line, fill='black', width=12)
        for corner, (x, y) in enumerate(CORNER_CENTRES):
            sides = (1 << corner) | (1 << ((corner + 1) % 4))
            if mask & sides == sides and not mask & (1 << (corner + 4)):
                draw.ellipse((x - 12, y - 12, x + 12, y + 12), fill='black')
    return image


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=Path)
    print(create_demo(samples=parser.parse_args().samples))
