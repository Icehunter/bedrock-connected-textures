"""Keep every block texture at or below the pack's own resolution.

Bedrock packs all block textures into one atlas and gives each texture a slot
as large as the largest one. A pack made at 256 pixels with a couple of 1024
pixel textures (an anvil drawn bigger) needs sixteen times the atlas space, more
than the game allows, and textures then go missing in game (placed torches, rails
and chains that the pack does not draw itself turn invisible). Scaling those few
textures down to the size most of the pack uses keeps the atlas within limits.
"""
from collections import Counter
import io
from pathlib import Path
import zipfile

from PIL import Image


def most_common_width(folder):
    """The width most block textures in the folder have, or None when it holds none."""
    widths = Counter()
    for path in Path(folder).glob('*.png'):
        with Image.open(path) as image:
            widths[image.width] += 1
    return widths.most_common(1)[0][0] if widths else None


def cap_block_textures(pack):
    """Scale block textures (and their material maps) wider than the pack's usual width down to it.

    Animated textures are frames stacked vertically, so the height keeps its ratio to the width.
    Returns the scaled files as {pack path: [old width, new width]}.
    """
    folder = Path(pack) / 'textures/blocks'
    width = most_common_width(folder)
    scaled = {}
    if not width:
        return scaled
    for path in sorted(folder.glob('*.png')):
        with Image.open(path) as image:
            if image.width <= width:
                continue
            height = max(1, round(image.height * width / image.width))
            smaller = image.resize((width, height), Image.LANCZOS)
            old_width = image.width
        smaller.save(path)
        scaled[path.relative_to(pack).as_posix()] = [old_width, width]
    return scaled


def cap_addon_block_textures(addon, width, resource_folder='Source_RP/'):
    """Scale the block textures of a finished add-on wider than width down to it; returns how many.

    BCT's own generated textures (the ground edge surfaces) are drawn at 256 pixels whatever the
    pack's resolution; in a 64-pixel pack they alone would outgrow the atlas. Block geometry maps
    textures on a 16-unit grid, so a smaller image draws the same.
    """
    addon = Path(addon)
    prefix = resource_folder + 'textures/blocks/'
    with zipfile.ZipFile(addon) as source:
        members = [(info, source.read(info.filename)) for info in source.infolist()]
    scaled = 0
    temporary = addon.with_suffix('.sizing')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as output:
        for info, data in members:
            if info.filename.startswith(prefix) and info.filename.endswith('.png'):
                with Image.open(io.BytesIO(data)) as image:
                    if image.width > width:
                        height = max(1, round(image.height * width / image.width))
                        buffer = io.BytesIO()
                        image.resize((width, height), Image.LANCZOS).save(buffer, 'PNG')
                        data = buffer.getvalue()
                        scaled += 1
            output.writestr(info, data)
    temporary.replace(addon)
    return scaled
