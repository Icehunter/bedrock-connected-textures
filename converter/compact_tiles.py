"""Expand the five tiles of an OptiFine ctm_compact rule into the full 47-tile connected template.

A compact rule supplies five tiles: 0 unconnected, 1 fully connected, 2 joined
vertically, 3 joined horizontally and 4 an inner corner. Each output tile is built from
four quarters, each taken from the source that matches the neighbours around that
corner. ``ctm.N=M`` properties replace output tile N with source tile M as a whole.
"""
import json
from pathlib import Path

from PIL import Image

from common import write_json
from java_texture_animation import compile_family, parse_animation, sprite_frames

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = 'textures/compact_tiles'
SOURCE_TILES = 5
UNCONNECTED, CONNECTED, VERTICAL, HORIZONTAL, INNER_CORNER = range(SOURCE_TILES)

# Quadrants top-left, top-right, bottom-left, bottom-right as (edge, edge, corner).
# Edge bits: 0 up, 1 right, 2 down, 3 left; corner bit k + 4 lies between edge k and edge k + 1.
QUADRANTS = ((3, 0, 3), (0, 1, 0), (2, 3, 2), (1, 2, 1))


def template_masks():
    """Canonical neighbour mask of each template tile, indexed by tile number."""
    text = (ROOT / 'engine/tile-template.mjs').read_text(encoding='utf-8')
    table = json.loads(text.split('Object.freeze(', 1)[1].split(');', 1)[0])
    masks = [None] * len(table)
    for mask, tile in table.items():
        masks[tile] = int(mask)
    return masks


def quadrant_tiles(mask):
    """Compact source tile for each quadrant of the tile with this canonical mask."""
    tiles = []
    for first_edge, second_edge, corner in QUADRANTS:
        first, second = bool(mask & (1 << first_edge)), bool(mask & (1 << second_edge))
        if first and second:
            tiles.append(CONNECTED if mask & (1 << (corner + 4)) else INNER_CORNER)
        elif first or second:
            joined_edge = first_edge if first else second_edge
            tiles.append(VERTICAL if joined_edge in (0, 2) else HORIZONTAL)
        else:
            tiles.append(UNCONNECTED)
    return tiles


def expand_images(images, replacements=None):
    """The 47 template tiles composed from the compact source images."""
    if len(images) < SOURCE_TILES:
        raise ValueError('compact_tiles needs five source tiles')
    size = images[0].size
    if any(image.size != size for image in images):
        raise ValueError('compact_tiles source tiles differ in size')
    replacements = replacements or {}
    if any(not 0 <= source < len(images) for source in replacements.values()):
        raise ValueError('compact_tiles replacement names a missing tile')
    width, height = size
    half_width, half_height = width // 2, height // 2
    boxes = ((0, 0, half_width, half_height), (half_width, 0, width, half_height),
             (0, half_height, half_width, height), (half_width, half_height, width, height))
    result = []
    for tile, mask in enumerate(template_masks()):
        if tile in replacements:
            result.append(images[replacements[tile]].copy())
            continue
        image = Image.new(images[0].mode, size)
        for box, source in zip(boxes, quadrant_tiles(mask)):
            image.paste(images[source].crop(box), box[:2])
        result.append(image)
    return result


def compile_materials(pack, tiles, destination, key, replacements=None):
    """Write the 47 expanded tiles (with material channels and animation) and return their texture names."""
    pack, destination = Path(pack), Path(destination)
    sources = [_png_path(pack / tile) for tile in tiles]
    colors = [Image.open(path).convert('RGBA') for path in sources]
    materials = [_material_channels(path, pack) for path in sources]
    channel_files, descriptor = materials[0]
    channel_names = set(channel_files)
    if any(set(channels) != channel_names for channels, _ in materials):
        raise ValueError('compact_tiles sources declare different material channels')
    animations = [_animation(path, image.size) for path, image in zip(sources, colors)]
    animated = [entry for entry in animations if entry[0]]
    if animated and (len(animated) != len(animations) or any(entry[1] != animated[0][1] for entry in animations)):
        raise ValueError('compact_tiles sources must share one animation')
    animation = animated[0][0] if animated else None
    channel_images = {name: [Image.open(channels[name][0]) for channels, _ in materials] for name in channel_names}
    modes = {'color': 'RGBA', **{name: images[0].mode for name, images in channel_images.items()}}
    layers = {'color': colors,
              **{name: [image.convert('RGBA') for image in images] for name, images in channel_images.items()}}
    channel_animations = {}
    for name in channel_names:
        own, _ = _animation(channel_files[name][0], layers[name][0].size)
        if own:
            channel_animations[name] = own
    outputs = {name: _expanded_layer(layers[name], channel_animations.get(name) or animation, replacements)
               for name in layers}
    folder = destination / OUTPUT / key
    folder.mkdir(parents=True, exist_ok=True)
    names = []
    for tile in range(len(outputs['color'])):
        name = f'{OUTPUT}/{key}/{tile}'
        images = {channel: outputs[channel][tile] for channel in layers}
        if animation:
            images, entry = compile_family(images, animation, name, name, channel_animations=channel_animations)
            write_json(folder / f'{tile}.png.mcmeta', {'animation': {
                'frametime': entry['ticks_per_frame'], 'frames': entry['frames'], 'interpolate': False}})
        images['color'].convert(modes['color']).save(folder / f'{tile}.png')
        for channel in channel_names:
            suffix = channel_files[channel][1]
            image = images[channel]
            if modes[channel] in ('L', 'RGB', 'RGBA'):
                image = image.convert(modes[channel])
            image.save(folder / f'{tile}{suffix}.png')
        if descriptor is not None:
            texture_set = {channel: value for channel, value in descriptor['minecraft:texture_set'].items()
                           if not isinstance(value, str)}
            texture_set['color'] = str(tile)
            for channel in channel_names:
                texture_set[channel] = f'{tile}{channel_files[channel][1]}'
            write_json(folder / f'{tile}.texture_set.json', {**descriptor, 'minecraft:texture_set': texture_set})
        names.append(name)
    return names


def _expanded_layer(images, animation, replacements):
    """The 47 tiles of one layer; with an animation, each tile is a strip with every frame expanded."""
    if not animation or images[0].size == (animation.width, animation.height):
        return expand_images(images, replacements)
    frames = [sprite_frames(image, animation) for image in images]
    frame_count = len(frames[0])
    expanded_frames = [expand_images([source[index] for source in frames], replacements)
                       for index in range(frame_count)]
    strips = []
    for tile in range(len(expanded_frames[0])):
        strip = Image.new('RGBA', (animation.width, animation.height * len(expanded_frames)))
        for index, frame_tiles in enumerate(expanded_frames):
            strip.paste(frame_tiles[tile], (0, index * animation.height))
        strips.append(strip)
    return strips


def _png_path(path):
    return path if path.suffix == '.png' else Path(str(path) + '.png')


def _material_channels(source, pack):
    """Material channels of one source tile, {channel: (path, file suffix)}, and its texture set."""
    descriptor = source.with_name(source.stem + '.texture_set.json')
    if not descriptor.exists():
        return {}, None
    data = json.loads(descriptor.read_text(encoding='utf-8'))
    texture_set = data['minecraft:texture_set']
    color = texture_set.get('color')
    stem = Path(color).name if isinstance(color, str) else source.stem
    channels = {}
    for channel, value in texture_set.items():
        if channel == 'color' or not isinstance(value, str):
            continue
        path = _png_path(pack / value if value.startswith('textures/') else source.parent / value)
        name = Path(value).name
        # Keep the author's suffix (stone_n -> 3_n); a map named unlike its colour gets _<channel>.
        suffix = name[len(stem):] if name.startswith(stem) and name != stem else '_' + channel
        channels[channel] = (path, suffix)
    return channels, data


def _animation(path, size):
    """(Animation, raw .mcmeta) for a texture, or (None, None) when it has no .mcmeta."""
    metadata = Path(str(path) + '.mcmeta')
    if not metadata.exists():
        return None, None
    document = json.loads(metadata.read_text(encoding='utf-8'))
    return parse_animation(size, document), document
