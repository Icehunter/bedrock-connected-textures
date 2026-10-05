"""A test board for connected textures and terrain edges.

Writes a tiny synthetic Java pack (OptiFine ctm.properties with numbered tiles
for ctm, horizontal, vertical, horizontal+vertical, vertical+horizontal, top,
fixed, random, repeat and overlay), the board layout, the tile each board face
must show (worked out here from the OptiFine format, separately from the
engine's selection code), the terrain edges each cobblestone host must get
from grass, sand and red sand, and a station .mcfunction that builds the board
in game. The station only places blocks into air (`keep`).

    python converter/ctm_board.py --output tests/fixtures/board
    python converter/ctm_board.py --pack board-pack.zip   # the Java pack to convert

Coordinates are relative to the station origin: x east, y up, z south. Walls
stand at z = -6 and show their south face to a player standing at the origin;
floors lie at y = 0 and show their top face.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import zipfile

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))

WALL_Z = -6
FACE_INDEX = {'down': 0, 'up': 1, 'north': 2, 'south': 3, 'west': 4, 'east': 5}
# Texture up, right, down, left of the faces the board shows, seen from outside.
TEXTURE_AXES = {'south': ((0, 1, 0), (1, 0, 0), (0, -1, 0), (-1, 0, 0)),
                'up': ((0, 0, -1), (1, 0, 0), (0, 0, 1), (-1, 0, 0))}
# Outward normals of the faces the board shows.
FACE_NORMALS = {'south': (0, 0, 1), 'up': (0, 1, 0)}

# Java block (as the pack names it), Bedrock block, method, tiles, extra properties.
RULES = {
    'ctm': ('white_wool', 'minecraft:white_wool', 'ctm', 47, {}),
    'horizontal': ('oak_planks', 'minecraft:oak_planks', 'horizontal', 4, {}),
    'vertical': ('spruce_planks', 'minecraft:spruce_planks', 'vertical', 4, {}),
    'horizontal_vertical': ('birch_planks', 'minecraft:birch_planks', 'horizontal+vertical', 7, {}),
    'vertical_horizontal': ('jungle_planks', 'minecraft:jungle_planks', 'vertical+horizontal', 7, {}),
    'top': ('acacia_planks', 'minecraft:acacia_planks', 'top', 1, {}),
    'fixed': ('dark_oak_planks', 'minecraft:dark_oak_planks', 'fixed', 1, {}),
    'random': ('end_stone', 'minecraft:end_stone', 'random', 4, {'weights': '1 2 1 3'}),
    'repeat': ('bricks', 'minecraft:brick_block', 'repeat', 6, {'width': '3', 'height': '2'}),
    'overlay': ('mossy_cobblestone', 'minecraft:mossy_cobblestone', 'overlay', 17, {'connectBlocks': 'gravel'}),
}
COLORS = {'ctm': (235, 235, 235), 'horizontal': (196, 150, 90), 'vertical': (120, 90, 60),
          'horizontal_vertical': (215, 200, 140), 'vertical_horizontal': (170, 120, 80), 'top': (190, 100, 60),
          'fixed': (80, 55, 35), 'random': (220, 220, 160), 'repeat': (160, 80, 70), 'overlay': (90, 120, 90)}

# The OptiFine ctm template by joined edges and corners: tiles 0-3 are a row,
# 12/24/36 a column, 13-15, 25-27 and 37-39 a 3x3 area, 46 a cross without corners.
CTM_TILES = {
    ((), ()): 0, (('right',), ()): 1, (('left', 'right'), ()): 2, (('left',), ()): 3,
    (('down',), ()): 12, (('down', 'up'), ()): 24, (('up',), ()): 36,
    (('down', 'right'), ('right_down',)): 13, (('down', 'left', 'right'), ('down_left', 'right_down')): 14,
    (('down', 'left'), ('down_left',)): 15,
    (('down', 'right', 'up'), ('right_down', 'up_right')): 25,
    (('down', 'left', 'right', 'up'), ('down_left', 'left_up', 'right_down', 'up_right')): 26,
    (('down', 'left', 'up'), ('down_left', 'left_up')): 27,
    (('right', 'up'), ('up_right',)): 37, (('left', 'right', 'up'), ('left_up', 'up_right')): 38,
    (('left', 'up'), ('left_up',)): 39,
    (('down', 'left', 'right', 'up'), ()): 46,
}
EDGE_NAMES = ('up', 'right', 'down', 'left')
# The corner between each edge and the next one.
CORNER_NAMES = ('up_right', 'right_down', 'down_left', 'left_up')
# horizontal+vertical on a lone column, or vertical+horizontal on a lone row:
# (joined above or to the right, joined below or to the left) -> tile; 3 when alone.
LONE_LINE_TILES = {(True, False): 4, (True, True): 5, (False, True): 6}

# Overlay template pieces by the edges that get the overlay, in texture space seen
# from outside the face: left 1, down 2, right 4, up 8 (engine/overlay.mjs EDGE_TILES).
OVERLAY_EDGE_TILES = {0: [], 1: [9], 2: [1], 3: [4], 4: [7], 5: [9, 7], 6: [3], 7: [5], 8: [15], 9: [11],
                      10: [1, 15], 11: [6], 12: [10], 13: [13], 14: [12], 15: [8]}
# Corner pieces between left+down, down+right, right+up and up+left (engine/overlay.mjs CORNER_TILES).
OVERLAY_CORNER_TILES = (2, 0, 14, 16)
# Edge masks with the overlay on two opposite edges: left+right and down+up.
OPPOSITE_EDGES = (5, 10)

# Terrain edge sources: (priority, material index of the edge surface).
PRIORITY = {'minecraft:grass_block': (5, 1), 'minecraft:sand': (4, 2), 'minecraft:red_sand': (2, 3)}
# Neighbor offsets (dx, dz) by edge mask bit: sides N E S W, then corners NE SE SW NW.
NEIGHBORS = ((0, -1), (1, 0), (0, 1), (-1, 0), (1, -1), (1, 1), (-1, 1), (-1, -1))

# 64-bit arithmetic of the random draw: SplitMix64's golden-ratio increment and mixing constants.
MASK64 = (1 << 64) - 1
GAMMA = 0x9e3779b97f4a7c15


def board():
    """Every block the board places: [(x, y, z, Bedrock block, section, shown face or None)]."""
    cells = []

    def wall(section, points, x0):
        block = RULES[section][1]
        cells.extend((x0 + x, 1 + y, WALL_Z, block, section, 'south') for x, y in points)

    # ctm: alone, a row, a column, a 3x3 square and a plus.
    wall('ctm', [(0, 0)] + [(2 + i, 0) for i in range(3)] + [(6, i) for i in range(3)]
         + [(8 + i, j) for i in range(3) for j in range(3)] + [(13, 1), (12, 1), (14, 1), (13, 0), (13, 2)], 0)
    wall('horizontal', [(i, 0) for i in range(3)] + [(4, 0)], 17)
    wall('vertical', [(0, i) for i in range(3)] + [(2, 0)], 23)
    wall('horizontal_vertical', [(i, 0) for i in range(3)] + [(4, i) for i in range(3)], 27)
    wall('vertical_horizontal', [(0, i) for i in range(3)] + [(2 + i, 0) for i in range(3)], 33)
    wall('top', [(0, 0), (0, 1)], 39)
    wall('fixed', [(0, 0)], 41)
    wall('repeat', [(i, j) for i in range(6) for j in range(4)], 43)
    # Floors at y = 0 between the player and the walls, clear of the origin where the player stands.
    cells.extend((2 + x, 0, -4 + z, RULES['random'][1], 'random', 'up') for x in range(4) for z in range(4))
    overlay = RULES['overlay'][1]
    # Each mossy cobblestone has gravel on one side, or one corner only.
    for index, (dx, dz) in enumerate([(1, 0), (0, -1), (-1, 0), (0, 1), (1, -1)]):
        x, z = 7 + 4 * index, -2
        cells.append((x, 0, z, overlay, 'overlay', 'up'))
        cells.append((x + dx, 0, z + dz, 'minecraft:gravel', 'overlay', None))
    cells.extend(terrain())
    return cells


def terrain():
    """A cobblestone floor at y = 0 with grass, sand, red sand and dirt patches (z from 4 to 12)."""
    sources = {(3, 6): 'minecraft:grass_block', (9, 6): 'minecraft:sand', (15, 6): 'minecraft:red_sand',
               (4, 10): 'minecraft:grass_block', (5, 10): 'minecraft:dirt', (10, 10): 'minecraft:sand',
               (11, 10): 'minecraft:red_sand'}
    cells = []
    for x in range(0, 19):
        for z in range(4, 13):
            block = sources.get((x, z), 'minecraft:cobblestone')
            shown = 'up' if block in ('minecraft:cobblestone', 'minecraft:dirt') else None
            cells.append((x, 0, z, block, 'terrain', shown))
    return cells


def expected_tiles(cells):
    """{"x,y,z,face": tile} for every board face a rule draws; None where the native face shows."""
    placed = {(x, y, z): block for x, y, z, block, _, _ in cells}
    result = {}
    for x, y, z, block, section, face in cells:
        if face is None or section == 'terrain':
            continue
        at = (x, y, z)
        edges, corners = _same_block_neighbors(placed, block, at, face)
        up, right, down, left = edges
        if section == 'ctm':
            tile = ctm_tile(edges, corners)
        elif section == 'horizontal':
            tile = pair_tile(right, left)
        elif section == 'vertical':
            tile = pair_tile(up, down)
        elif section == 'horizontal_vertical':
            # A row uses the horizontal tiles; a lone column uses 4 (bottom), 5 (middle) and 6 (top).
            tile = pair_tile(right, left) if right or left else LONE_LINE_TILES.get((up, down), 3)
        elif section == 'vertical_horizontal':
            tile = pair_tile(up, down) if up or down else LONE_LINE_TILES.get((right, left), 3)
        elif section == 'top':
            tile = 0 if placed.get((x, y + 1, z)) == block else None
        elif section == 'fixed':
            tile = 0
        elif section == 'random':
            tile = weighted([1, 2, 1, 3], random_value(x, y, z, face) % 7)
        elif section == 'repeat':
            tile = repeat_tile(x, y, z, face, 3, 2)
        elif section == 'overlay':
            tile = overlay_tiles(placed, at, face)
        else:
            raise ValueError(section)
        result[f'{x},{y},{z},{face}'] = tile
    return result


def ctm_tile(edges, corners):
    """The ctm template tile for the joined edges (up, right, down, left) and corners."""
    joined_edges = tuple(sorted(name for name, joined in zip(EDGE_NAMES, edges) if joined))
    # A corner counts only between two joined edges.
    joined_corners = tuple(sorted(name for index, name in enumerate(CORNER_NAMES)
                                  if corners[index] and edges[index] and edges[(index + 1) % 4]))
    if (joined_edges, joined_corners) not in CTM_TILES:
        raise ValueError(f'board case outside the documented template: {joined_edges} {joined_corners}')
    return CTM_TILES[(joined_edges, joined_corners)]


def pair_tile(first, second):
    """A horizontal (first = right, second = left) or vertical (first = up, second = down) tile.

    0 one end, 1 the middle, 2 the other end, 3 alone.
    """
    if first and second:
        return 1
    if first:
        return 0
    return 2 if second else 3


def imul(first, second):
    """Java int multiplication: the product wrapped to a signed 32-bit integer."""
    return ((first * second + (1 << 31)) % (1 << 32)) - (1 << 31)


def block_seed(x, y, z):
    """Minecraft's per-position seed (Mth.getSeed)."""
    seed = imul(x, 3129871) ^ (z * 116129781) ^ y
    value = (seed * seed * 42317861 + seed * 11) & MASK64
    value = value - (1 << 64) if value >> 63 else value
    return value >> 16


def _mix(value, first_shift, first_multiplier, second_shift, second_multiplier, final_shift):
    """A 64-bit xor-shift-multiply mix; a final_shift of 0 leaves out the last xor-shift."""
    value = ((value ^ (value >> first_shift)) * first_multiplier) & MASK64
    value = ((value ^ (value >> second_shift)) * second_multiplier) & MASK64
    return value ^ (value >> final_shift) if final_shift else value


# One seed per face (SplitMix64 of the face's index), mixed into each face's draw.
FACE_SEEDS = [_mix((GAMMA * (face + 1)) & MASK64, 30, 0xbf58476d1ce4e5b9, 27, 0x94d049bb133111eb, 31)
              for face in range(6)]


def random_value(x, y, z, face, loops=0):
    """Continuity's random draw for a block face (no symmetry)."""
    value = ((block_seed(x, y, z) ^ FACE_SEEDS[FACE_INDEX[face]]) + GAMMA * (loops + 1)) & MASK64
    value = _mix(value, 33, 0x62a9d9ed799705f5, 28, 0xcb24d0a5c88c35b3, 0)
    return (value >> 32) & 0x7fffffff


def weighted(weights, draw):
    """The index a draw in [0, sum of weights) falls on."""
    for index, weight in enumerate(weights):
        if draw < weight:
            return index
        draw -= weight
    return len(weights) - 1


def repeat_tile(x, y, z, face, width, height):
    """OptiFine's repeat: column and row from the block position, per face."""
    column, row = {'south': (x, -y), 'up': (x, z)}[face]
    return (row % height) * width + (column % width)


def overlay_tiles(placed, at, face):
    """Overlay pieces from gravel beside a mossy cobblestone (OptiFine dialect), as engine/overlay.mjs picks them.

    A piece lies on the side of the gravel that gives it: gravel to the left of
    the face draws tile 9 along the face's left edge. A corner piece needs both
    edges next to it free of the overlay, gravel on its diagonal and, unless two
    adjacent edges have the overlay, a mossy cobblestone (a block with the same
    rule) beside it. A neighbor with a block in front of it neither gives the
    overlay nor counts as carrying it.
    """
    up, right, down, left = TEXTURE_AXES[face]
    normal = FACE_NORMALS[face]

    def block_at(offset):
        return placed.get(_vector_sum(at, offset))

    def covered(offset):
        return block_at(_vector_sum(offset, normal)) is not None

    # In the bit order of OVERLAY_EDGE_TILES.
    sides = (left, down, right, up)
    gravel_edges, same_rule_edges = 0, 0
    for bit, offset in enumerate(sides):
        if covered(offset):
            continue
        if block_at(offset) == 'minecraft:gravel':
            gravel_edges |= 1 << bit
        if block_at(offset) == 'minecraft:mossy_cobblestone':
            same_rule_edges |= 1 << bit
    tiles = list(OVERLAY_EDGE_TILES[gravel_edges])
    two_adjacent_edges = bin(gravel_edges).count('1') == 2 and gravel_edges not in OPPOSITE_EDGES
    for corner in range(4):
        corner_sides = (1 << corner) | (1 << ((corner + 1) % 4))
        if gravel_edges & corner_sides:
            continue
        # OptiFine skips the same-rule neighbor for the corner across from two adjacent overlay edges.
        if not two_adjacent_edges and not same_rule_edges & corner_sides:
            continue
        diagonal = _vector_sum(sides[corner], sides[(corner + 1) % 4])
        if block_at(diagonal) == 'minecraft:gravel' and not covered(diagonal):
            tiles.append(OVERLAY_CORNER_TILES[corner])
    return sorted(tiles)


def expected_edges(cells, provider):
    """{"x,y,z": quadrant states} for the native edge surface above each host, from the provider's rules.

    A side neighbor that is a rule's source and has air above sets that side;
    a diagonal one sets its corner only when neither side next to it is set
    and both blocks between are the rule's targets with air above (path
    corners). Each quadrant (side k with the corner after it) takes the
    material of the highest priority rule that sets it.
    """
    placed = {(x, z): block for x, y, z, block, _, _ in cells if y == 0}
    result = {}
    for (x, z), host in placed.items():
        masks = []
        for rule in provider['rules']:
            if host not in rule['targets']:
                continue
            mask = _edge_mask(placed, x, z, rule)
            if mask:
                masks.append((PRIORITY[rule['neighbors'][0]], mask))
        if not masks:
            continue
        masks.sort(key=lambda item: -item[0][0])
        states = {}
        for quadrant in range(4):
            edge = next((material for (_, material), mask in masks if mask & (1 << quadrant)), 0)
            corner = next((material for (_, material), mask in masks if mask & (1 << (quadrant + 4))), 0)
            states[f'bct:quadrant{quadrant}'] = edge + 4 * corner
        result[f'{x},0,{z}'] = states
    return result


def station(cells):
    """mcfunction lines building the board around the executing player, only into air."""
    lines = ['# Bedrock Connected Textures test board: rows of connected-texture methods and terrain edges.',
             '# Walls at z-6 show their south face; floors at y0. Every command places only into air (keep).']
    for x, y, z, block, _, _ in sorted(cells, key=lambda cell: (cell[1], cell[2], cell[0])):
        lines.append(f'setblock ~{x} ~{y} ~{z} {block.removeprefix("minecraft:")} keep')
    return '\n'.join(lines) + '\n'


def write_pack(folder):
    """The synthetic Java pack: pack.mcmeta, numbered tiles and one ctm.properties per method."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    pack_metadata = {'pack': {'pack_format': 64, 'description': 'BCT test board'}}
    (folder / 'pack.mcmeta').write_text(json.dumps(pack_metadata) + '\n')
    for section, (java, _, method, count, extra) in RULES.items():
        directory = folder / 'assets/minecraft/optifine/ctm/board' / section
        directory.mkdir(parents=True, exist_ok=True)
        for number in range(count):
            _tile_image(section, number, COLORS[section]).save(directory / f'{number}.png')
        lines = [f'method={method}', f'matchBlocks={java}', f'tiles=0-{count - 1}' if count > 1 else 'tiles=0']
        lines += [f'{key}={value}' for key, value in extra.items()]
        (directory / f'{section}.properties').write_text('\n'.join(lines) + '\n')
        texture = folder / f'assets/minecraft/textures/block/{java}.png'
        texture.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGBA', (16, 16), COLORS[section] + (255,)).save(texture)
    return folder


def imported_rules(pack):
    """The pack's rules the way the converter imports them (Java ids mapped to Bedrock ids)."""
    from block_ids import JAVA_RENAMES
    from import_java_ctm import import_rules
    from java_block_states import resolve_known_block_selection
    base = {bedrock: {face: f'assets/minecraft/textures/block/{java}.png' for face in FACE_INDEX}
            for java, bedrock, *_ in RULES.values()}
    document = import_rules(pack, base, block_names=JAVA_RENAMES, block_state_resolver=resolve_known_block_selection)
    return {'rules': document['rules'], 'baseTextures': base}


def build(output):
    """Writes layout.json, rules.json, expected.json and station.mcfunction into output."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    cells = board()
    provider = json.loads((Path(__file__).resolve().parent / 'data/terrain-provider.json').read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory() as folder:
        rules = imported_rules(write_pack(Path(folder) / 'pack'))
    layout = [{'x': x, 'y': y, 'z': z, 'block': block, 'section': section, 'face': face}
              for x, y, z, block, section, face in cells]
    files = {'layout.json': layout, 'rules.json': rules,
             'expected.json': {'tiles': expected_tiles(cells), 'edges': expected_edges(cells, provider)}}
    for name, value in files.items():
        (output / name).write_text(json.dumps(value, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    (output / 'station.mcfunction').write_text(station(cells), encoding='utf-8')
    return output


def write_pack_zip(path):
    """The Java test pack as a .zip for the converter."""
    with tempfile.TemporaryDirectory() as folder:
        pack = write_pack(Path(folder) / 'pack')
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for item in sorted(pack.rglob('*')):
                if item.is_file():
                    archive.write(item, item.relative_to(pack).as_posix())
    return path


def _same_block_neighbors(placed, block, at, face):
    """Edges (up, right, down, left) and corners (between each edge and the next) joined to the same block."""
    axes = TEXTURE_AXES[face]

    def same(offset):
        return placed.get(_vector_sum(at, offset)) == block

    edges = [same(axes[index]) for index in range(4)]
    corners = [same(_vector_sum(axes[index], axes[(index + 1) % 4])) for index in range(4)]
    return edges, corners


def _vector_sum(first, second):
    """Two coordinate tuples added component by component (a position and an offset, or two offsets)."""
    return tuple(a + b for a, b in zip(first, second))


def _edge_mask(placed, x, z, rule):
    """The NEIGHBORS bits one terrain rule sets for the host at (x, z)."""
    def holds(offset, blocks):
        dx, dz = offset
        return placed.get((x + dx, z + dz)) in blocks

    mask = 0
    for bit, offset in enumerate(NEIGHBORS):
        if holds(offset, rule['neighbors']):
            mask |= 1 << bit
    for corner in range(4):
        corner_bit = 1 << (corner + 4)
        sides = (corner, (corner + 1) % 4)
        side_bits = (1 << sides[0]) | (1 << sides[1])
        # A corner stays only on a path corner: neither side next to it set, both of those blocks targets.
        if mask & corner_bit and (mask & side_bits or not all(holds(NEIGHBORS[side], rule['targets'])
                                                              for side in sides)):
            mask &= ~corner_bit
    return mask


def _tile_image(label, number, color, size=32):
    """A numbered tile with an arrow pointing to the texture's top, so turns and mirrors show in game."""
    image = Image.new('RGBA', (size, size), color + (255,))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, size - 1, size - 1], outline=(0, 0, 0, 255))
    draw.polygon([(size // 2, 2), (size // 2 - 4, 8), (size // 2 + 4, 8)], fill=(0, 0, 0, 255))
    draw.text((4, 11), str(number), fill=(0, 0, 0, 255))
    draw.text((4, 21), label[:6], fill=(0, 0, 0, 255))
    return image


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path,
                        help='folder for layout.json, rules.json, expected.json and station.mcfunction')
    parser.add_argument('--pack', type=Path, help='write the synthetic Java pack as a .zip here')
    arguments = parser.parse_args()
    if not arguments.output and not arguments.pack:
        parser.error('give --output, --pack or both')
    if arguments.output:
        print(build(arguments.output))
    if arguments.pack:
        print(write_pack_zip(arguments.pack))
