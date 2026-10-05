"""Grass colour of each Bedrock biome, picked the way the game picks it.

A client biome file gives either a fixed colour or a colour map; with a map, the
biome's temperature and rainfall choose the pixel. The pack's own client biome files
and colour maps come first, then the vanilla ones in bedrock-samples.
"""
from PIL import Image

from common import read_json


def resolve_colors(samples, pack):
    """{biome identifier: [r, g, b] from 0 to 1} for every biome that has a climate."""
    colors = {}
    for source in sorted((samples / 'behavior_pack/biomes').glob('*.json')):
        biome = read_json(source)['minecraft:biome']
        identifier = biome['description']['identifier']
        climate = biome['components'].get('minecraft:climate')
        if climate is None:
            continue
        setting = _grass_color_setting(samples, pack, source)
        if isinstance(setting, str):
            color = tuple(bytes.fromhex(setting.lstrip('#'))[:3])
        else:
            color = _colormap_pixel(samples, pack, setting.get('color_map', 'grass'), climate)
        colors[identifier] = [round(value / 255, 6) for value in color]
    return colors


def _grass_color_setting(samples, pack, behavior_biome):
    """The biome's grass colour setting: '#rrggbb' or {'color_map': name}."""
    client_name = behavior_biome.stem.replace('.biome', '') + '.client_biome.json'
    client = pack / 'biomes' / client_name
    if not client.exists():
        client = samples / 'resource_pack/biomes' / client_name
    appearance = {}
    if client.exists():
        components = read_json(client)['minecraft:client_biome']['components']
        appearance = components.get('minecraft:grass_appearance', {})
    return appearance.get('color', {'color_map': 'grass'})


def _colormap_pixel(samples, pack, name, climate):
    """The colour map pixel for a climate; rainfall counts only as far as it is warm."""
    path = pack / 'textures/colormap' / (name + '.png')
    if not path.exists():
        path = samples / 'resource_pack/textures/colormap' / (name + '.png')
    temperature = max(0, min(1, climate['temperature']))
    rainfall = max(0, min(1, climate['downfall'])) * temperature
    with Image.open(path) as image:
        x = int((1 - temperature) * (image.width - 1))
        y = int((1 - rainfall) * (image.height - 1))
        return image.convert('RGB').getpixel((x, y))
