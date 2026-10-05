"""Held and inventory ("carried") textures made from the author's art.

Bedrock draws some blocks in the hand and inventory with separate pre-tinted "_carried"
textures (grass top, leaves, ferns, vines, lily pad). Java packs have none, so without
these the game shows vanilla's 16 px ones. Each is made from the author's block texture
with the tint Java gives that item: the item model's "tints" (a constant, or the grass
colour map at a temperature and downfall), read from the author's pack when it overrides
the item, otherwise from the game.
"""
import io
import json
import shutil
from pathlib import Path

from PIL import Image

from common import read_json, texture_file, write_json

# Bedrock carried texture -> (author's base texture, Java item whose tint it shows)
CARRIED = {
    'textures/blocks/grass_carried': ('textures/blocks/grass_top', 'grass_block'),
    'textures/blocks/tallgrass_carried': ('textures/blocks/tallgrass', 'short_grass'),
    'textures/blocks/fern_carried': ('textures/blocks/fern', 'fern'),
    'textures/blocks/double_plant_grass_carried': ('textures/blocks/double_plant_grass_top', 'tall_grass'),
    'textures/blocks/double_plant_fern_carried': ('textures/blocks/double_plant_fern_top', 'large_fern'),
    'textures/blocks/leaves_oak_carried': ('textures/blocks/leaves_oak', 'oak_leaves'),
    'textures/blocks/leaves_spruce_carried': ('textures/blocks/leaves_spruce', 'spruce_leaves'),
    'textures/blocks/leaves_birch_carried': ('textures/blocks/leaves_birch', 'birch_leaves'),
    'textures/blocks/leaves_jungle_carried': ('textures/blocks/leaves_jungle', 'jungle_leaves'),
    'textures/blocks/leaves_acacia_carried': ('textures/blocks/leaves_acacia', 'acacia_leaves'),
    'textures/blocks/leaves_big_oak_carried': ('textures/blocks/leaves_big_oak', 'dark_oak_leaves'),
    'textures/blocks/mangrove_leaves_carried': ('textures/blocks/mangrove_leaves', 'mangrove_leaves'),
    'textures/blocks/vine_carried': ('textures/blocks/vine', 'vine'),
    'textures/blocks/seagrass_carried': ('textures/blocks/seagrass', 'seagrass'),
    'textures/blocks/carried_waterlily': ('textures/blocks/waterlily', 'lily_pad'),
}


def grass_color(colormap, temperature, downfall):
    """Java's GrassColor.get: a pixel of the 256x256 grass colour map."""
    temperature = min(max(temperature, 0.0), 1.0)
    downfall = min(max(downfall, 0.0), 1.0) * temperature
    x, y = int((1.0 - temperature) * 255.0), int((1.0 - downfall) * 255.0)
    return colormap.convert('RGB').getpixel((x, y))


def item_tint(item, read):
    """RGB tint Java applies to tint index 0 of an item, or None when the item is untinted.

    read(path) returns bytes from the author's pack or the game jar (author first), or None.
    """
    data = read(f'assets/minecraft/items/{item}.json')
    if data is None:
        return None
    tints = json.loads(data).get('model', {}).get('tints', [])
    if not tints:
        return None
    tint = tints[0]
    if tint.get('type') == 'minecraft:constant':
        value = tint['value'] & 0xFFFFFF
        return (value >> 16 & 255, value >> 8 & 255, value & 255)
    if tint.get('type') == 'minecraft:grass':
        colormap = read('assets/minecraft/textures/colormap/grass.png')
        if colormap is None:
            return None
        return grass_color(Image.open(io.BytesIO(colormap)), tint.get('temperature', 0.5), tint.get('downfall', 1.0))
    return None


def carried_tints(read):
    """{carried texture path: [r, g, b] or None} for every carried slot."""
    tints = {}
    for path, (_, item) in CARRIED.items():
        tint = item_tint(item, read)
        tints[path] = list(tint) if tint else None
    return tints


def item_tints(bindings, read):
    """{Bedrock item texture: [r, g, b]} for item sprites Java draws from a tinted block texture.

    A bush item, for one, is the grass-tinted bush texture.
    """
    tints = {}
    for bedrock, sprite in (bindings.get('materialBindings') or {}).items():
        if bedrock.startswith('textures/items/') and '/textures/block/' in sprite:
            tint = item_tint(Path(bedrock).name, read)
            if tint:
                tints[bedrock] = list(tint)
    return tints


def fill_carried_slots(rp, tints):
    """Write each missing carried texture from the author's base texture, tinted as Java tints the item."""
    rp = Path(rp)
    filled = []
    for path, (base, _) in CARRIED.items():
        if texture_file(rp, path) or path not in tints:
            continue
        source = texture_file(rp, base)
        if source is None:
            continue
        target = rp / (path + '.png')
        with Image.open(source) as image:
            image = image.convert('RGBA')
            tint = tints[path]
            if tint:
                image = _multiplied(image, tint)
            image.save(target)
        _copy_texture_set(source, target)
        filled.append(path)
    return filled


def fill_item_tints(rp, tints):
    """Bake each item's Java tint into its sprite; Bedrock draws item sprites untinted."""
    rp = Path(rp)
    baked = []
    for bedrock, tint in sorted(tints.items()):
        image_path = texture_file(rp, bedrock)
        if image_path is None:
            continue
        with Image.open(image_path) as image:
            _multiplied(image.convert('RGBA'), tint).save(image_path.with_suffix('.png'))
        baked.append(bedrock)
    return baked


def _multiplied(image, tint):
    """An RGBA image with each colour channel multiplied by the tint's 0-255 value; alpha is kept."""
    red, green, blue, alpha = image.split()
    red, green, blue = (channel.point(lambda value, factor=factor: value * factor // 255)
                        for channel, factor in zip((red, green, blue), tint))
    return Image.merge('RGBA', (red, green, blue, alpha))


def _copy_texture_set(source, target):
    """Give the carried texture the base texture's material maps, copied under the carried name."""
    descriptor = source.with_name(source.stem + '.texture_set.json')
    if not descriptor.exists():
        return
    renamed = {}
    for channel, value in read_json(descriptor)['minecraft:texture_set'].items():
        if channel == 'color':
            renamed[channel] = target.stem
        elif isinstance(value, str) and value.startswith(source.stem):
            name = target.stem + value[len(source.stem):]
            shutil.copyfile(source.parent / (value + '.png'), target.parent / (name + '.png'))
            renamed[channel] = name
        else:
            renamed[channel] = value
    write_json(target.with_name(target.stem + '.texture_set.json'),
               {'format_version': '1.21.30', 'minecraft:texture_set': renamed})
