"""Whether a repeat mosaic can be drawn as random tiles instead.

A repeat rule cuts one large picture into tiles and gives each block the tile
for its position. Bedrock draws that only on blocks the engine swaps for blocks
that know their position. Grass, dirt and sand stay vanilla (their gameplay
needs the vanilla block), so on them a mosaic would show its first tile
everywhere. Bedrock can pick a tile at random per block position by itself
(atlas variations), and many mosaics, sand especially, look the same either
way: any tile joins any other about as smoothly as its real neighbour. This
module measures that, so only mosaics that still look like the author's are
drawn at random.
"""
from fnmatch import fnmatchcase
from itertools import permutations

import numpy as np
from PIL import Image

# A mosaic may be drawn at random when its tiles, put next to each other at
# random, join at most this much worse than in the author's order, side by side
# and one above the other. Measured on a 256-pixel pack: sand 1.27, red
# sand 1.35, grass top 1.65; at random the grass shows a few soft patches and no
# hard seams, much closer to the author's look than one tile everywhere. A
# mosaic of large distinct shapes measures well above 2. Edges are compared at
# full resolution: scaling down hides fine grain and leaves only the broad
# shapes, which reverses those results.
RANDOM_SEAM_LIMIT = 1.7


def kept_vanilla_patterns(policy):
    """Block patterns the replacement policy keeps vanilla (never swapped)."""
    return [pattern for entry in policy.get('keep_vanilla', []) for pattern in entry['blocks']]


def only_vanilla_blocks(targets, patterns):
    """Whether every target block stays vanilla, so a repeat rule on them could never draw by position."""
    return bool(targets) and all(any(fnmatchcase(block, pattern) for pattern in patterns) for block in targets)


def _edges(path):
    """The four edges of a tile as float arrays: left, right, top, bottom."""
    with Image.open(path) as image:
        pixels = np.asarray(image.convert('RGB'), dtype=float)
    return pixels[:, 0], pixels[:, -1], pixels[0], pixels[-1]


def _join(first, second, across):
    """How far apart the touching edges are when second sits right of first (across) or below it."""
    if across:
        return float(np.abs(first[1] - second[0]).mean())
    return float(np.abs(first[3] - second[2]).mean())


def random_looks_authored(tile_paths, width, height):
    """Whether the mosaic's tiles, placed at random, join nearly as smoothly as in the author's grid.

    tile_paths are in the rule's order, row by row, width tiles to a row. Both directions must pass.
    """
    if len(tile_paths) < 2 or not width or not height or len(tile_paths) != width * height:
        return False
    edges = [_edges(path) for path in tile_paths]
    # Tiles of different sizes are not one mosaic.
    if len({(edge[0].shape, edge[2].shape) for edge in edges}) > 1:
        return False
    for across in (True, False):
        authored = []
        for index in range(len(edges)):
            column, row = index % width, index // width
            beside = row * width + (column + 1) % width if across else ((row + 1) % height) * width + column
            authored.append(_join(edges[index], edges[beside], across))
        at_random = [_join(edges[first], edges[second], across) for first, second in permutations(range(len(edges)), 2)]
        if np.mean(at_random) > max(np.mean(authored), 1e-6) * RANDOM_SEAM_LIMIT:
            return False
    return True
