"""Flat decals a Java pack lays over a block's top, baked into the top's random variations.

A pack can give a block flat model parts just above its top face, picked at
random per block (a clover layer on grass, with some blocks bare). Bedrock
cannot add model parts to a block it keeps vanilla (grass stays vanilla: its
gameplay needs it), but a decal lying flat on the face looks the same painted
into the face's texture: the half pixel it floats above the face does not show.
So each random variation of the top is made in several versions, bare and with
a decal variant, in the share the Java model gives (its "none" weight), and the
game picks one per block position as it does for every variation. Every
variation is randomly turned by the game already (the grass top is isotropic),
which stands in for the decal's own turns.
"""
import json
from pathlib import Path, PurePosixPath

from PIL import Image

from common import read_json, write_json

# Versions of each top tile: one in SLOTS is bare when the model is bare a quarter of the time.
SLOTS = 4
# A flat decal lies at most this far above the top face (pixels), covering the whole face.
DECAL_HEIGHT = 1.0


def _model(stack, name):
    path = 'assets/minecraft/models/' + name.split(':')[-1] + '.json'
    return json.loads(stack.read(path).decode('utf-8-sig')) if path in stack.files else None


def _flat_top_texture(model):
    """The texture of a model that is one full-face plane just above the top (a decal), or None."""
    if not model or len(model.get('elements', [])) != 1:
        return None
    element = model['elements'][0]
    start, end = element.get('from', []), element.get('to', [])
    if len(start) != 3 or start[1] != end[1] or not 16 <= start[1] <= 16 + DECAL_HEIGHT:
        return None
    if [start[0], start[2], end[0], end[2]] != [0, 0, 16, 16] or set(element.get('faces', {})) != {'up'}:
        return None
    reference = element['faces']['up'].get('texture', '').lstrip('#')
    texture = model.get('textures', {}).get(reference, '')
    while texture.startswith('#'):
        texture = model.get('textures', {}).get(texture[1:], '')
    return texture or None


def find_top_decals(stack, block):
    """{'texture': Java texture path, 'bare': share of bare blocks} for a block's random top decal, or None.

    Only a multipart part whose models are all flat full-face planes above the top (or air) counts,
    and only when every one of them uses the same texture.
    """
    path = f'assets/minecraft/blockstates/{block}.json'
    if path not in stack.files:
        return None
    state = json.loads(stack.read(path).decode('utf-8-sig'))
    for part in state.get('multipart', []):
        choices = part.get('apply')
        if not isinstance(choices, list):
            continue
        textures, bare, total = set(), 0, 0
        for choice in choices:
            weight = choice.get('weight', 1)
            total += weight
            name = choice.get('model', '')
            if name.split('/')[-1] == 'air':
                bare += weight
                continue
            texture = _flat_top_texture(_model(stack, name))
            if texture is None:
                break
            textures.add(texture)
        else:
            if len(textures) == 1 and total:
                texture = textures.pop().split(':')[-1]
                return {'texture': f'assets/minecraft/textures/{texture}.png', 'bare': bare / total}
    return None


def _channels(materials, sprite):
    """{channel: image path} of a converted material (its texture set), the colour image at least."""
    color = Path(materials) / sprite
    descriptor = color.with_suffix('.texture_set.json')
    channels = {'color': color}
    if descriptor.exists():
        for channel, value in read_json(descriptor)['minecraft:texture_set'].items():
            if channel != 'color':
                channels[channel] = color.parent / (value + '.png')
    return channels


def _over(base_path, decal_path, alpha):
    """The base image with the decal image laid over it where alpha (the decal's colour alpha) is set."""
    with Image.open(base_path) as base_image, Image.open(decal_path) as decal_image:
        base = base_image.convert('RGBA')
        decal = decal_image.convert('RGBA').resize(base.size, Image.LANCZOS)
    mask = alpha.resize(base.size, Image.LANCZOS)
    keep = base.getchannel('A')
    result = Image.composite(decal, base, mask)
    result.putalpha(keep)
    return result


def bake_top_decals(materials, tops, decals, bare_share, output):
    """Write the decal versions of a top's variations; returns the new [[sprite, weight]] choices.

    tops and decals: [[sprite, weight]] (sprites relative to materials). Each top tile gets SLOTS
    versions: round(SLOTS * bare_share) bare ones (the tile itself), the others each with the next
    decal variant in turn, so every decal variant shows about equally often.
    """
    materials, output = Path(materials), Path(output)
    bare_slots = min(SLOTS, max(0, round(SLOTS * bare_share)))
    choices, turn = [], 0
    for top, weight in tops:
        for slot in range(SLOTS):
            if slot < bare_slots:
                choices.append([top, weight])
                continue
            decal = decals[turn % len(decals)][0]
            turn += 1
            name = PurePosixPath(top).stem + f'_decal{slot}'
            sprite = (PurePosixPath('bct-decals') / PurePosixPath(top).parent.name / (name + '.png')).as_posix()
            target = output / sprite
            target.parent.mkdir(parents=True, exist_ok=True)
            base, layer = _channels(materials, top), _channels(materials, decal)
            with Image.open(layer['color']) as decal_color:
                alpha = decal_color.convert('RGBA').getchannel('A')
            texture_set = {'color': target.stem}
            for channel, path in base.items():
                over = layer.get(channel)
                image = _over(path, over, alpha) if over and over.exists() else Image.open(path).convert('RGBA')
                suffix = '' if channel == 'color' else path.stem[len(PurePosixPath(top).stem):]
                image.save(target.with_name(target.stem + suffix + '.png'))
                if channel != 'color':
                    texture_set[channel] = target.stem + suffix
            descriptor = (materials / top).with_suffix('.texture_set.json')
            if descriptor.exists():
                write_json(target.with_suffix('.texture_set.json'),
                           {'format_version': read_json(descriptor)['format_version'], 'minecraft:texture_set': texture_set})
            choices.append([sprite, weight])
    return choices
