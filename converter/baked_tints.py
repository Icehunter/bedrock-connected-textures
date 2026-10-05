"""Bake Java block tints that Bedrock does not apply into the textures.

Java colours some block textures as it draws them: the author's fixed colour maps (a pack
may ship grey wool, concrete or terracotta with a colour per block) and vanilla block
colours (sugar cane takes the grass colour). Bedrock tints only a fixed set of blocks, so
elsewhere the author's grey texture would stay grey; the colour is multiplied in instead.
An author colour map always wins unless Bedrock tints that texture itself; a vanilla tint
is baked only where Bedrock's own texture is coloured and the author's is grey; a sprite
shared by blocks with different tints is reported, never guessed.
"""
import io
from pathlib import Path, PurePosixPath

import numpy as np
from PIL import Image

from common import texture_file

# Bedrock textures the game tints itself (biome, age or power colour); never baked.
BEDROCK_TINTED = {
    'textures/blocks/grass_top', 'textures/blocks/grass_side', 'textures/blocks/tallgrass', 'textures/blocks/fern',
    'textures/blocks/double_plant_grass_top', 'textures/blocks/double_plant_grass_bottom',
    'textures/blocks/double_plant_fern_top', 'textures/blocks/double_plant_fern_bottom',
    'textures/blocks/leaves_oak', 'textures/blocks/leaves_jungle', 'textures/blocks/leaves_acacia',
    'textures/blocks/leaves_big_oak', 'textures/blocks/leaves_birch', 'textures/blocks/leaves_spruce',
    'textures/blocks/mangrove_leaves', 'textures/blocks/vine', 'textures/blocks/waterlily', 'textures/blocks/bush',
    'textures/blocks/leaf_litter',
    'textures/blocks/melon_stem_disconnected', 'textures/blocks/melon_stem_connected',
    'textures/blocks/pumpkin_stem_disconnected', 'textures/blocks/pumpkin_stem_connected',
    'textures/blocks/redstone_dust_cross', 'textures/blocks/redstone_dust_line',
}
# Java biome tints at the plains biome, the colour Bedrock gets when baked.
BIOME_DEFAULT = {'grass': (0x91, 0xBD, 0x59), 'foliage': (0x77, 0xAB, 0x2F)}
# Mean saturation limits (0 grey, 1 fully coloured):
# below this, Bedrock's own texture is grey, so the game tints it or it is meant to be grey;
BEDROCK_GREY_BELOW = 0.2
# from this up, Java's own texture is coloured, so Java draws the face untinted (dirt under grass);
JAVA_COLOURED_FROM = 0.12
# from this up, the author's texture is already coloured and needs no vanilla tint.
AUTHOR_COLOURED_FROM = 0.15
# Material maps that sit next to a colour texture and must never be tinted.
MATERIAL_MAP_SUFFIXES = ('_normal.png', '_mers.png', '_mer.png')
FACE_SUFFIXES = ('_top', '_bottom', '_side', '_front')


def parse_fixed_colormaps(files):
    """{block id: (r, g, b)} from the author's optifine/colormap/blocks/*.properties with format=fixed.

    files: {path: bytes} of the author's pack.
    """
    colors = {}
    for path, data in files.items():
        if '/optifine/colormap/blocks/' not in path or not path.endswith('.properties'):
            continue
        properties = _read_properties(data)
        if properties.get('format') != 'fixed' or 'color' not in properties:
            continue
        value = int(properties['color'], 16)
        rgb = (value >> 16 & 255, value >> 8 & 255, value & 255)
        for token in properties.get('blocks', PurePosixPath(path).stem).split():
            if '=' in token or token.isdecimal():
                continue
            colors[token if ':' in token else 'minecraft:' + token] = rgb
    return colors


def plan_tints(bindings, author_fixed, vanilla_rp, read_vanilla_java=lambda sprite: None):
    """{bedrock texture path: ((r, g, b), block, authored, sprite, replace)} to bake, and a report.

    Each Bedrock texture belongs to one Java block. The author's model for that block may draw
    it with another texture (light blue concrete drawn with the blue one): then that texture is
    used (replace=True). read_vanilla_java(sprite) returns the game's own Java texture bytes: a
    vanilla tint is only baked into textures Java itself draws grey, so untinted faces (a grass
    block's dirt bottom) are never coloured.
    """
    tints = dict(bindings.get('modelTintTypes') or {})
    for block, color in author_fixed.items():
        tints[block] = [channel / 255 for channel in color]
    faces = bindings.get('baseTextures') or {}
    wanted, report = {}, []
    for bedrock, java_texture in (bindings.get('materialBindings') or {}).items():
        if not bedrock.startswith('textures/blocks/') or bedrock in BEDROCK_TINTED:
            continue
        block = _block_for(java_texture, tints)
        if block is None:
            continue
        rgb = _tint_rgb(tints[block])
        if rgb is None:
            continue
        authored = block in author_fixed
        model_textures = set((faces.get(block) or {}).values())
        sprite = java_texture
        if model_textures and java_texture not in model_textures:
            if len(model_textures) > 1:
                report.append({'texture': bedrock, 'block': block,
                               'reason': "the author's model uses several other textures"})
                continue
            sprite = next(iter(model_textures))
        if not authored and not _vanilla_tint_applies(vanilla_rp, bedrock, read_vanilla_java(sprite)):
            continue
        wanted[bedrock] = (rgb, block, authored, sprite, sprite != java_texture)
    return wanted, report


def bake(rp, plan, read_sprite=lambda sprite: None):
    """Multiply each planned texture (and its random-variant tiles) by its tint. Returns baked paths.

    A texture the pack doesn't ship (the author left the vanilla one, as a pack may for concrete) is
    written from read_sprite(sprite), the Java texture the author's model tints.
    """
    rp = Path(rp)
    baked = []
    for relative, ((red, green, blue), _block, authored, sprite, replace) in sorted(plan.items()):
        base = rp / relative
        if replace or texture_file(rp, relative) is None:
            data = read_sprite(sprite)
            if data is None:
                continue
            base.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(io.BytesIO(data)) as image:
                image.convert('RGBA').save(base.with_suffix('.png'))
        own = texture_file(rp, relative)
        targets = ([own] if own else []) + sorted(base.parent.glob(base.name + '_v[0-9]*.png'))
        for path in targets:
            if path.name.endswith(MATERIAL_MAP_SUFFIXES):
                continue
            with Image.open(path) as image:
                pixels = np.asarray(image.convert('RGBA')).astype(np.uint32)
            if not authored and _saturation(Image.fromarray(pixels.astype(np.uint8))) >= AUTHOR_COLOURED_FROM:
                continue
            pixels[:, :, :3] = pixels[:, :, :3] * np.array([red, green, blue], dtype=np.uint32) // 255
            Image.fromarray(pixels.astype(np.uint8)).save(path.with_suffix('.png'))
            baked.append(path.relative_to(rp).as_posix())
    return baked


def _read_properties(data):
    """key=value pairs of a .properties file; # starts a comment."""
    properties = {}
    for line in data.decode('utf-8-sig').splitlines():
        line = line.split('#', 1)[0].strip()
        if '=' in line:
            key, value = line.split('=', 1)
            properties[key.strip()] = value.strip()
    return properties


def _block_for(java_texture, tints):
    """The tinted Java block a texture stands for: its own name, or its name without a face suffix."""
    stem = PurePosixPath(java_texture).stem
    names = (stem, *(stem[:-len(suffix)] for suffix in FACE_SUFFIXES if stem.endswith(suffix)))
    for name in names:
        if 'minecraft:' + name in tints:
            return 'minecraft:' + name
    return None


def _tint_rgb(tint):
    """8-bit RGB of a model tint: a [r, g, b] colour from 0 to 1, or a biome tint name; None otherwise."""
    if isinstance(tint, list):
        return tuple(round(channel * 255) for channel in tint)
    if tint in BIOME_DEFAULT:
        return BIOME_DEFAULT[tint]
    return None


def _vanilla_tint_applies(vanilla_rp, bedrock, java_bytes):
    """Whether a vanilla Java tint belongs in this texture: Bedrock's own is coloured and Java's is grey."""
    vanilla = texture_file(vanilla_rp, bedrock)
    if vanilla is None or java_bytes is None:
        return False
    with Image.open(vanilla) as image:
        if _saturation(image) < BEDROCK_GREY_BELOW:
            return False
    with Image.open(io.BytesIO(java_bytes)) as image:
        return _saturation(image) < JAVA_COLOURED_FROM


def _saturation(image):
    """Mean saturation of the visible pixels, from 0 (grey) to 1."""
    pixels = np.asarray(image.convert('RGBA')).astype(np.float32) / 255
    visible = pixels[:, :, 3] > 0.5
    if not visible.any():
        return 0.0
    rgb = pixels[visible][:, :3]
    high, low = rgb.max(axis=1), rgb.min(axis=1)
    return float(((high - low) / np.maximum(high, 1e-6)).mean())
