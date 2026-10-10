"""What `python bct.py check` reads from a resource pack's textures: the terrain atlas and ray tracing.

The game puts every block texture into one terrain atlas and gives each texture a
slot as wide as the widest one (texture_size.py). Animation strips take one slot
per frame. Past ATLAS_SIDE pixels square the game scales the block textures down
to fit; a 512 pixel pack three times over loaded that way, blurred. The estimate counts the vanilla textures (from bedrock-samples)
with the pack's own on top, as if the pack were the only resource pack.
atlas_fit_width finds the slot width that fits, for the converter's --scale-to-atlas.

Ray tracing reads a block's texture set (textures/blocks/*.texture_set.json) and
only from a pack whose manifest declares the `raytraced` capability; Vibrant
Visuals reads them from packs declaring `pbr`.
"""
import io
import json
import math
from pathlib import Path
import zipfile

from PIL import Image

from common import read_json, without_json_comments
from java_rtx_compatibility import texture_set_failures

ATLAS_SIDE = 16384
# Above this share of the atlas, check says the pack is near the limit.
ATLAS_NEAR = 0.75
# --scale-to-atlas never goes below this slot width.
SMALLEST_WIDTH = 16
TEXTURE_SET_REASONS = {
    'invalid_layer_combination': 'needs a color layer, and cannot have both normal and heightmap, '
                                 'or both MER and MERS',
    'missing_image': 'names an image that is not in the resource pack',
    'invalid_channel_count': 'has an image with the wrong channels (heightmap is greyscale, the rest RGB or RGBA)',
    'material_dimensions_differ': 'has layers of different sizes',
}


def atlas_paths(entry):
    """The texture paths a terrain atlas entry names: one path, a list, {path}, or weighted variations."""
    textures = entry.get('textures') if isinstance(entry, dict) else None
    if isinstance(textures, dict):
        textures = [textures] if 'path' in textures else textures.get('variations', [])
    if isinstance(textures, str):
        return [textures]
    if not isinstance(textures, list):
        return []
    return [item.get('path') if isinstance(item, dict) else item for item in textures]


def _image(folder, texture):
    for suffix in ('.png', '.tga'):
        path = Path(folder) / (texture + suffix)
        if path.is_file():
            return path
    return None


def _textures_of(atlas):
    """{texture: alias} of a terrain atlas document."""
    textures = {}
    for alias, entry in (atlas or {}).get('texture_data', {}).items():
        for texture in atlas_paths(entry):
            if isinstance(texture, str):
                textures.setdefault(texture, alias)
    return textures


def _atlas_textures(path):
    return _textures_of(read_json(path) if path.is_file() else None)


def _size(path):
    try:
        with Image.open(path) as image:
            return image.size
    except OSError:
        return None


def _vanilla_sizes(samples):
    vanilla = Path(samples) / 'resource_pack'
    textures = _atlas_textures(vanilla / 'textures/terrain_texture.json')
    sizes = {}
    for texture in textures:
        path = _image(vanilla, texture)
        size = _size(path) if path else None
        if size:
            sizes[texture] = (size, False)
    return sizes


def estimate_atlas(sizes, width=None):
    """{side, share, slots, width, widest} for {texture: ((width, height), the pack's own)}.

    width, when given, is the slot width with every wider texture scaled down to it.
    """
    if not sizes:
        return {'side': 0, 'share': 0.0, 'slots': 0, 'width': 0, 'widest': []}
    widest_width = max(size[0] for size, _ in sizes.values())
    slot = min(widest_width, width) if width else widest_width
    # Scaling keeps an image's shape, so an animation strip keeps its frame count.
    slots = sum(max(1, round(height / max(1, own_width))) for (own_width, height), _ in sizes.values())
    # No room for the atlas padding: a pack of 3894 slots at 256 pixels drew sharp, 63 slots a side
    # (16128 pixels), where 8 pixels each side would make it 17136; at 512 pixels it was scaled down,
    # blurred. The game's low resources warning is no guide: the author's own 256 pixel pack shows it.
    side = math.ceil(math.sqrt(slots)) * slot
    widest = sorted(texture for texture, ((own_width, _), mine) in sizes.items() if mine and own_width >= slot)
    return {'side': side, 'share': side * side / ATLAS_SIDE ** 2, 'slots': slots, 'width': slot, 'widest': widest}


def atlas_estimate(rp, samples):
    """{side, share, slots, width, widest}: the terrain atlas the pack needs on top of vanilla.

    side is the estimated atlas side in pixels, share that side's area over the largest
    atlas, width the slot width, and widest the pack's textures at that width.
    """
    rp = Path(rp)
    sizes = _vanilla_sizes(samples)
    own = _atlas_textures(rp / 'textures/terrain_texture.json')
    # The pack's own textures, and pack files at vanilla paths, which replace the vanilla texture.
    for texture in set(own) | set(sizes):
        path = _image(rp, texture)
        size = _size(path) if path else None
        if size:
            sizes[texture] = (size, True)
    return estimate_atlas(sizes)


def addon_atlas_sizes(addon, samples, resource_folder='Source_RP/'):
    """{texture: ((width, height), the pack's own)} for the resource pack inside an add-on."""
    sizes = _vanilla_sizes(samples)
    with zipfile.ZipFile(addon) as archive:
        names = set(archive.namelist())
        atlas_name = resource_folder + 'textures/terrain_texture.json'
        atlas = json.loads(without_json_comments(archive.read(atlas_name).decode('utf-8-sig')))             if atlas_name in names else None
        own = _textures_of(atlas)
        for texture in set(own) | set(sizes):
            name = next((resource_folder + texture + suffix for suffix in ('.png', '.tga')
                         if resource_folder + texture + suffix in names), None)
            if name is None:
                continue
            size = _size(io.BytesIO(archive.read(name)))
            if size:
                sizes[texture] = (size, True)
    return sizes


def atlas_fit_width(sizes):
    """The widest slot width, halving from the widest texture, at which the atlas fits; never wider than the widest texture."""
    width = max((size[0] for size, _ in sizes.values()), default=0)
    while width > SMALLEST_WIDTH and estimate_atlas(sizes, width)['share'] > 1:
        width //= 2
    return width


def atlas_messages(rp, samples):
    """(problems, notes) about the terrain atlas. A full atlas still loads, so it is only ever a note."""
    estimate = atlas_estimate(rp, samples)
    if estimate['share'] <= ATLAS_NEAR:
        return [], []
    widest = estimate['widest']
    named = ', '.join(widest[:5]) + (f' and {len(widest) - 5} more' if len(widest) > 5 else '')
    sizes = f"about {estimate['slots']} textures in {estimate['width']} px slots, "             f"{estimate['side']} px square"
    if estimate['share'] > 1:
        fix = f'make the {estimate["width"]} px textures ({named}) smaller' if widest else 'use fewer textures'
        return [], [f'the terrain atlas needs {sizes}, over the {ATLAS_SIDE} px the game builds: the game scales '
                    f'the block textures down, so they look blurred. '
                    f'To keep them sharp, {fix}']
    return [], [f'the terrain atlas needs {sizes}, {round(estimate["share"] * 100)}% of the {ATLAS_SIDE} px '
                'the game builds; with more random variants or larger textures, the game scales them down']


def ray_tracing_messages(rp):
    """(problems, notes) about the pack's texture sets and its pbr and raytraced capabilities."""
    rp = Path(rp)
    manifest = rp / 'manifest.json'
    capabilities = set(read_json(manifest).get('capabilities', [])) if manifest.is_file() else set()
    blocks = rp / 'textures/blocks'
    descriptors = sorted(blocks.rglob('*.texture_set.json')) if blocks.is_dir() else []
    problems = []
    for descriptor in descriptors:
        for failure in texture_set_failures(descriptor, rp):
            channel = f" ({failure['channel']})" if 'channel' in failure else ''
            problems.append(f"{failure['path']}{channel}: {TEXTURE_SET_REASONS[failure['reason']]}")
    if not descriptors:
        if 'raytraced' in capabilities:
            return problems, ['manifest.json declares raytraced, but the pack has no texture sets: '
                              'its blocks look flat with ray tracing']
        return problems, ['the pack has no texture sets (textures/blocks/*.texture_set.json): '
                          'its blocks look flat with ray tracing and Vibrant Visuals']
    notes = []
    if problems:
        return problems, notes
    if 'raytraced' not in capabilities:
        notes.append('the texture sets are ready for ray tracing: add "raytraced" to "capabilities" '
                     'in the resource pack manifest.json')
    if 'pbr' not in capabilities:
        notes.append('Vibrant Visuals reads the texture sets only with "pbr" in "capabilities" '
                     'in the resource pack manifest.json')
    own = _atlas_textures(rp / 'textures/terrain_texture.json')
    covered = {descriptor.as_posix().removesuffix('.texture_set.json') for descriptor in descriptors}
    flat = sorted(texture for texture in own
                  if texture.startswith('textures/blocks/') and _image(rp, texture)
                  and (rp / texture).as_posix() not in covered)
    if flat:
        named = ', '.join(flat[:5]) + (f' and {len(flat) - 5} more' if len(flat) > 5 else '')
        notes.append(f'{len(flat)} texture(s) have no texture set and look flat with ray tracing: {named}')
    return problems, notes
