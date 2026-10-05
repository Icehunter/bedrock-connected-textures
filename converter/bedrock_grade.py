"""The author's own Bedrock colour grade, learned from their Bedrock pack (--bedrock-grade).

Some authors also publish a Bedrock edition of their pack and tune its colours
for Bedrock's lighting: one 256-pixel pack measured about 15% less contrast, darks
lifted by about 10 levels and slightly less warmth than its Java art. When the
author's Bedrock pack is given (--vv-scene) and --bedrock-grade is set, the
converter compares the block textures both packs share, fits one colour grade
(each output channel a weighted sum of the three input channels plus an offset,
by least squares) and applies it to every block colour texture of the
converted pack: rule tiles, variations and edges included. Material maps
(normals, MER/MERS, heightmaps) are left as they are. Without the option the
Java colours are kept exactly.
"""
import io
from pathlib import PurePosixPath
import zipfile

import numpy as np
from PIL import Image

BLOCKS = 'textures/blocks/'
MAP_SUFFIXES = ('_mer', '_mers', '_normal', '_heightmap')
# Fewer shared textures than this give no reliable grade.
MIN_SHARED = 50
# Every n-th opaque pixel of a texture pair is used for the fit.
SAMPLE_STEP = 37


def _is_color(name):
    stem = PurePosixPath(name).stem
    return name.endswith('.png') and not stem.endswith(MAP_SUFFIXES)


def _rgba(data, size=None):
    with Image.open(io.BytesIO(data)) as image:
        image = image.convert('RGBA')
        if size and image.size != size:
            image = image.resize(size, Image.LANCZOS)
        return np.asarray(image, dtype=float)


def _block_textures(archive, prefix):
    """{texture name: member} for the colour textures directly under a pack's textures/blocks/."""
    found = {}
    for name in archive.namelist():
        if not name.startswith(prefix + BLOCKS):
            continue
        rest = name[len(prefix + BLOCKS):]
        if '/' in rest or not (_is_color(name) or rest.endswith('.tga')):
            continue
        found[PurePosixPath(rest).stem] = name
    return found


def fit_grade(converted, converted_prefix, bedrock):
    """The 4 x 3 grade matrix from the shared block textures, with the fit's numbers; None with too few shared."""
    ours, theirs = _block_textures(converted, converted_prefix), _block_textures(bedrock, '')
    inputs, outputs, shared = [], [], 0
    for name, member in ours.items():
        if name not in theirs:
            continue
        try:
            source = _rgba(converted.read(member))
            target = _rgba(bedrock.read(theirs[name]), size=source.shape[1::-1])
        except (OSError, ValueError):
            continue
        opaque = (source[..., 3] > 200) & (target[..., 3] > 200)
        if opaque.sum() < 500:
            continue
        inputs.append(source[opaque][::SAMPLE_STEP, :3])
        outputs.append(target[opaque][::SAMPLE_STEP, :3])
        shared += 1
    if shared < MIN_SHARED:
        return None
    inputs, outputs = np.concatenate(inputs), np.concatenate(outputs)
    design = np.c_[inputs, np.ones(len(inputs))]
    matrix = np.linalg.lstsq(design, outputs, rcond=None)[0]
    return {'matrix': matrix, 'shared_textures': shared,
            'mean_error_before': round(float(np.abs(inputs - outputs).mean()), 2),
            'mean_error_after': round(float(np.abs(design @ matrix - outputs).mean()), 2)}


def graded(data, matrix):
    """PNG bytes with the grade applied to the colour channels; alpha unchanged."""
    pixels = _rgba(data)
    rgb = np.c_[pixels[..., :3].reshape(-1, 3), np.ones(pixels.shape[0] * pixels.shape[1])] @ matrix
    pixels[..., :3] = np.clip(rgb, 0, 255).reshape(pixels.shape[0], pixels.shape[1], 3)
    output = io.BytesIO()
    Image.fromarray(pixels.round().astype(np.uint8), 'RGBA').save(output, 'PNG')
    return output.getvalue()


def grade_addon(addon, bedrock_pack, resource_folder='Source_RP/'):
    """Fit the grade and rewrite the add-on's block colour textures with it; returns the report."""
    with zipfile.ZipFile(addon) as converted, zipfile.ZipFile(bedrock_pack) as bedrock:
        fit = fit_grade(converted, resource_folder, bedrock)
        if fit is None:
            return {'applied': False, 'reason': f'fewer than {MIN_SHARED} block textures shared with the Bedrock pack'}
        members = [(info, converted.read(info.filename)) for info in converted.infolist()]
    count = 0
    temporary = addon.with_suffix('.grading')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as output:
        for info, data in members:
            if info.filename.startswith(resource_folder + BLOCKS) and _is_color(info.filename):
                data = graded(data, fit['matrix'])
                count += 1
            output.writestr(info, data)
    temporary.replace(addon)
    return {'applied': True, 'graded_textures': count, 'shared_textures': fit['shared_textures'],
            'mean_error_before': fit['mean_error_before'], 'mean_error_after': fit['mean_error_after'],
            'matrix': [[round(float(value), 4) for value in row] for row in fit['matrix']]}
