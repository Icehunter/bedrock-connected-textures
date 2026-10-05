"""Edge cutout silhouettes taken from the converted pack's own materials.

Every shape comes from the author's art: grass uses the alpha of the pack's
grass side overlay (the same edge the author drew for the block side); other
surfaces derive a ragged boundary from the material's height map, or its
luminance when it has none. Shapes grow from the top edge into a 256 square
and wrap horizontally, like the material itself. An edge never reaches past the
middle of the square, so it fits the two quadrants along its side.
"""
import numpy as np
from PIL import Image

from common import read_json, terrain_pack

SIZE = 256
# A material edge sits about a quarter of the way down; the material's broad column pattern
# swings it by up to _FINGER_SWING, and its single pixels roughen the boundary.
_EDGE_DEPTH = 0.25
_FINGER_SWING = 0.09
# Column-pattern frequencies kept for the fingers, counting the constant one.
_FINGER_FREQUENCIES = 7
_PIXEL_ROUGHNESS = 0.035
# Extreme height map pixels count no more than this many standard deviations.
_ROUGHNESS_LIMIT = 2.5
# The top tenth of every material edge is solid, so the edge always meets its side.
_SOLID_BAND = 0.1


def edge_alpha(root, effect):
    """256 square 'L' cutout for one surface effect, solid at the top edge."""
    pack = terrain_pack(root)
    if effect.get('biome_tint') == 'grass':
        overlay = _grass_overlay(_grass_side(root, effect, pack))
        if overlay is not None:
            return overlay
    return _material_edge(_material_field(_edge_texture_set(root, effect, pack)))


def _grass_side(root, effect, pack):
    """The grass side texture whose alpha holds the author's overlay, or None."""
    if effect.get('edge_source'):
        with Image.open(root / effect['edge_source']) as image:
            return image.convert('RGBA')
    return _open_texture(pack / 'textures/blocks/grass_side')


def _grass_overlay(side):
    """The grass side overlay as an edge cutout, or None when it does not reach the top row."""
    if side is None:
        return None
    alpha = np.asarray(side.resize((SIZE, SIZE), Image.Resampling.NEAREST))[:, :, 3]
    # grass_side alpha is the overlay's tint mask: an edge hangs from the top row and ends
    # at the middle.
    covered = alpha >= 128
    if not covered[0].any():
        return None
    covered[SIZE // 2:] = False
    return _cutout(covered)


def _edge_texture_set(root, effect, pack):
    """The texture set an edge is cut from: the family's shared one, else the effect's first."""
    shared = effect.get('edge_texture_set')
    if shared:
        return root / shared
    sources = effect.get('texture_sets')
    if sources:
        return root / sources[0]
    return pack / f'textures/blocks/{effect["material"]}.texture_set.json'


def _material_field(source):
    """The material's height map, else its luminance, as a 256 square of standard scores."""
    descriptor = read_json(source)['minecraft:texture_set']
    height = descriptor.get('heightmap')
    image = _open_texture(source.parent / height) if isinstance(height, str) else None
    if image is None:
        image = _open_texture(source.parent / descriptor['color'])
    grey = image.convert('L').resize((SIZE, SIZE), Image.Resampling.LANCZOS)
    field = np.asarray(grey, dtype=np.float32) / 255
    spread = field.std()
    if spread > 1e-6:
        return (field - field.mean()) / spread
    return np.zeros_like(field)


def _material_edge(field):
    """A ragged edge: broad fingers follow the material's columns, its pixels break up the boundary."""
    profile = _periodic_lowpass(field.mean(axis=0), _FINGER_FREQUENCIES)
    profile = profile / (np.abs(profile).max() or 1)
    depth = _EDGE_DEPTH + _FINGER_SWING * profile
    rows = (np.arange(SIZE, dtype=np.float32)[:, None] + 0.5) / SIZE
    roughness = _PIXEL_ROUGHNESS * np.clip(field, -_ROUGHNESS_LIMIT, _ROUGHNESS_LIMIT)
    covered = rows < depth[None, :] + roughness
    covered[: int(SIZE * _SOLID_BAND)] = True
    covered[SIZE // 2:] = False
    return _cutout(covered)


def _periodic_lowpass(values, keep):
    """Keep the `keep` lowest frequencies of a row that wraps around."""
    spectrum = np.fft.rfft(values)
    spectrum[keep:] = 0
    return np.fft.irfft(spectrum, n=len(values))


def _cutout(covered):
    """An 'L' image, opaque where covered."""
    return Image.fromarray(np.where(covered, 255, 0).astype(np.uint8))


def _open_texture(path_without_suffix):
    """The .png, else .tga, image at this path as RGBA, or None."""
    for suffix in ('.png', '.tga'):
        path = path_without_suffix.with_suffix(suffix)
        if path.is_file():
            with Image.open(path) as image:
                return image.convert('RGBA')
    return None
