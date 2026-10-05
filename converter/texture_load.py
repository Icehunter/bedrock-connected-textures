"""How much a converted pack asks of the game when it loads.

Very large packs fail to load on Bedrock (12,000+ textures at 256x crashed the game).
Every image the pack ships is measured as GPU memory with mipmaps, along with the number
of custom block permutations, and the report warns when a pack is in the range where
loading has failed.
"""
from pathlib import Path

from PIL import Image

# A full mipmap chain adds a third to the memory of the base image.
MIPMAPS = 4 / 3
WARN_MIB = 2048
WARN_PERMUTATIONS = 30000
IMAGE_SUFFIXES = ('.png', '.tga', '.jpg', '.jpeg')


def measure(folders):
    """Images, megapixels and estimated GPU MiB (RGBA8 with mipmaps) under the folders."""
    images = pixels = 0
    for folder in folders:
        for path in Path(folder).rglob('*'):
            if path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            try:
                with Image.open(path) as image:
                    width, height = image.size
            except OSError:
                continue
            images += 1
            pixels += width * height
    return {'images': images, 'megapixels': round(pixels / 1e6, 1),
            'estimated_gpu_mib': round(pixels * 4 * MIPMAPS / 2 ** 20)}


def load_report(base_packs, replacement=None, permutations=0):
    """Texture load per renderer (its base pack plus the replacement pack) and the permutation count."""
    report = {'permutations': permutations, 'renderers': {}}
    warnings = []
    extra_folders = [Path(replacement)] if replacement and Path(replacement).exists() else []
    for renderer, folder in base_packs.items():
        load = measure([Path(folder), *extra_folders])
        report['renderers'][renderer] = load
        if load['estimated_gpu_mib'] > WARN_MIB:
            warnings.append(f"{renderer}: about {load['estimated_gpu_mib']} MiB of textures; "
                            'packs this large have failed to load. '
                            'Convert a lower-resolution edition of the pack.')
    if permutations > WARN_PERMUTATIONS:
        warnings.append(f'{permutations} custom block permutations; worlds may load slowly or fail to load.')
    report['warnings'] = warnings
    return report
