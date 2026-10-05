"""Edges for the sands a pack draws no overlay for, cut like the pack's own sand edge.

Many packs give sand (and grass) an overlay rule that spreads it over the
blocks around it, and nothing for red sand, suspicious sand or soul sand. BCT
draws those edges too, in a fixed order where each one lies over the ones
after it: grass, sand, red sand, suspicious sand, soul sand, everything else.
They are made from the pack's own art so they match its sand edge: the
outline of each tile (its alpha) comes from the pack's sand overlay tiles and
the surface from the other sand's texture, with its LabPBR maps when the pack
has them. They are written as an extra pack layer under the author's pack, as
if the author had made them, so every later step treats them like the
author's overlay rules (one surface block per cell holds every edge there).

A pack without a sand overlay gets nothing here; the engine's generated edges
draw those transitions instead.
"""
import io
from pathlib import PurePosixPath
import zipfile

from PIL import Image

from java_pack_api import PackStack

# The sands in the order they lie over each other, with the Java texture each one shows on top.
SAND_ORDER = (('sand', 'sand'), ('red_sand', 'red_sand'), ('suspicious_sand', 'suspicious_sand_0'),
              ('soul_sand', 'soul_sand'))
# Sources that lie over every sand.
ABOVE_SANDS = ('grass_block', 'grass_block_top')
CTM_FOLDER = 'assets/minecraft/optifine/ctm/'
TEXTURES = 'assets/minecraft/textures/block/'
# LabPBR maps beside a texture.
MAP_SUFFIXES = ('_n', '_s')


def read_properties(text):
    """An OptiFine .properties file as a dict (later keys win; comments and blank lines skipped)."""
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(('#', '!')) or '=' not in line:
            continue
        name, value = line.split('=', 1)
        values[name.strip()] = value.strip()
    return values


def write_properties(values):
    return ''.join(f'{name}={value}\n' for name, value in values.items()).encode('utf-8')


def tile_numbers(spec):
    """Tile numbers from a tiles= value such as 1-17 or 1 2 3."""
    numbers = []
    for part in spec.split():
        if '-' in part:
            first, last = part.split('-', 1)
            if first.isdigit() and last.isdigit():
                numbers.extend(range(int(first), int(last) + 1))
        elif part.isdigit():
            numbers.append(int(part))
    return numbers


def _names(value):
    """Block or texture names of a connect/match value, without namespace or path."""
    return {name.split(':')[-1].split('/')[-1] for name in (value or '').split()}


def overlay_rules(stack):
    """The pack's overlay rules: [(properties path, values)]."""
    found = []
    for path in sorted(stack.files):
        if path.startswith(CTM_FOLDER) and path.endswith('.properties'):
            values = read_properties(stack.read(path).decode('utf-8', 'replace'))
            if values.get('method') == 'overlay':
                found.append((path, values))
    return found


def _connects_to(values, block, texture):
    return bool({block, texture} & (_names(values.get('connectBlocks')) | _names(values.get('connectTiles'))))


def _tile_path(folder, number, suffix, files):
    """A rule tile's path (number.png in the rule's folder), or None when the pack lacks it."""
    path = f'{folder}/{number}{suffix}.png'
    return path if path in files else None


def _masked(surface_bytes, mask_bytes):
    """The surface image (scaled to the mask's size) with the mask's alpha."""
    with Image.open(io.BytesIO(mask_bytes)) as mask, Image.open(io.BytesIO(surface_bytes)) as surface:
        alpha = mask.convert('RGBA').getchannel('A')
        image = surface.convert('RGBA').resize(mask.size, Image.LANCZOS)
    image.putalpha(alpha)
    output = io.BytesIO()
    image.save(output, 'PNG')
    return output.getvalue()


def plan_sand_edges(stack):
    """The rule files to add, as {zip path: bytes}; empty when the pack has no sand overlay or needs none."""
    rules = overlay_rules(stack)
    sand_rule = next(((path, values) for path, values in rules if _connects_to(values, 'sand', 'sand')), None)
    if sand_rule is None:
        return {}
    sand_path, sand_values = sand_rule
    folder = str(PurePosixPath(sand_path).parent)
    numbers = tile_numbers(sand_values.get('tiles', ''))
    if not numbers or not all(_tile_path(folder, number, '', stack.files) for number in numbers):
        return {}
    files = {}
    for index, (block, texture) in enumerate(SAND_ORDER):
        if block == 'sand' or any(_connects_to(values, block, texture) for _, values in rules):
            continue
        surface = TEXTURES + texture + '.png'
        if surface not in stack.files:
            continue
        # Over the sand rule's own targets, never over the sands above this one or grass, and over the sands below it.
        above = {name for pair in SAND_ORDER[:index + 1] for name in pair} | set(ABOVE_SANDS)
        below_tiles = {name for _, name in SAND_ORDER[index + 1:] if TEXTURES + name + '.png' in stack.files}
        target_tiles = sorted((_names(sand_values.get('matchTiles')) - above) | below_tiles)
        target_blocks = sorted(_names(sand_values.get('matchBlocks')) - above)
        # Overlay rules draw in file name order, each over the ones before: the lowest sand gets the
        # smallest number, and every one of them sorts before the pack's own overlays (grass, sand).
        name = f'_bct_overlay_{len(SAND_ORDER) - index}_{block}'
        values = {key: value for key, value in sand_values.items()
                  if key not in ('connectBlocks', 'connectTiles', 'matchBlocks', 'matchTiles', 'tiles', 'tintIndex', 'tintBlock')}
        # Matched by block: a Java texture name (suspicious_sand_0) need not be one a Bedrock block shows.
        values.update({'tiles': f'1-{len(numbers)}', 'connectBlocks': block,
                       'matchTiles': ' '.join(target_tiles)})
        if target_blocks:
            values['matchBlocks'] = ' '.join(target_blocks)
        files[f'{CTM_FOLDER}{name}/{name}.properties'] = write_properties(values)
        surface_bytes = stack.read(surface)
        for position, number in enumerate(numbers, 1):
            mask = stack.read(_tile_path(folder, number, '', stack.files))
            files[f'{CTM_FOLDER}{name}/{position}.png'] = _masked(surface_bytes, mask)
            for suffix in MAP_SUFFIXES:
                surface_map = TEXTURES + texture + suffix + '.png'
                if surface_map in stack.files:
                    files[f'{CTM_FOLDER}{name}/{position}{suffix}.png'] = _masked(stack.read(surface_map), mask)
    return files


def write_sand_edge_layer(archives, output):
    """Write the extra pack layer for the archives' missing sand edges; returns its path, or None when none is needed."""
    with PackStack(archives) as stack:
        files = plan_sand_edges(stack)
        mcmeta = stack.read('pack.mcmeta') if 'pack.mcmeta' in stack.files else None
    if not files:
        return None
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        # The layer declares the same formats as the author's pack, so the stack treats it alike.
        if mcmeta is not None:
            archive.writestr('pack.mcmeta', mcmeta)
        for path, data in sorted(files.items()):
            archive.writestr(path, data)
    return output
