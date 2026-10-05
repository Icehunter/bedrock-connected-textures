"""Java's separate grass side overlay as Bedrock's grass tint mask.

Java draws the grass block side as two layers: untinted soil, and a grass
overlay the biome color tints. Bedrock's grass_side material is one image whose
alpha is a tint mask, not opacity. The native material encodes the two Java
layers into that image; the connected-texture renderer keeps them separate.
"""
from pathlib import Path

import numpy as np
from PIL import Image

GRASS_OVERLAY = 'assets/minecraft/textures/block/grass_block_side_overlay.png'
GRASS_BLOCKS = ('minecraft:grass_block', 'minecraft:grass')
SIDE_FACES = ('north', 'east', 'south', 'west')


def grass_overlay_source(bindings):
    """The tinted side layer the Java grass models declare (a pack can name its own), else the vanilla overlay."""
    declared = set()
    for block in GRASS_BLOCKS:
        for variant in bindings.get('baseTextureVariants', {}).get(block, []):
            for choice in variant.get('modelChoices', [variant]):
                for face, layers in choice.get('faceLayers', {}).items():
                    if face in SIDE_FACES:
                        declared.update(layer['texture'] for layer in layers if layer.get('tintIndex', -1) >= 0)
    if len(declared) > 1:
        raise ValueError('Grass models declare multiple side tint masks; a single native preview cannot represent them')
    return next(iter(declared), GRASS_OVERLAY)


def encode_grass_side(base, overlay):
    """Soil RGB under the overlay's RGB by the overlay's alpha; the overlay's alpha becomes the tint mask.

    The soil stays untinted: only where the overlay covers it does Bedrock's
    tint apply. No pixel is resampled, so both images must have one size.
    """
    base, overlay = base.convert('RGBA'), overlay.convert('RGBA')
    if base.size != overlay.size:
        raise ValueError('Grass side and overlay dimensions differ; no resampling permitted')
    soil = np.asarray(base, dtype=np.uint8)
    vegetation = np.asarray(overlay, dtype=np.uint8)
    coverage = vegetation[:, :, 3:4].astype(np.uint32)
    soil_rgb = soil[:, :, :3].astype(np.uint32)
    vegetation_rgb = vegetation[:, :, :3].astype(np.uint32)
    # Alpha blend with rounding: (soil x (255 - a) + grass x a + 127) / 255.
    color = ((soil_rgb * (255 - coverage) + vegetation_rgb * coverage + 127) // 255).astype(np.uint8)
    return Image.fromarray(np.dstack((color, vegetation[:, :, 3])))


def adapt_grass_side(target, materials, sprite, bindings):
    """Writes the encoded grass side to target; the author's source images stay untouched.

    materials: folder of the converted Java materials; sprite: the grass side
    texture in it. Returns the adapter's receipt.
    """
    materials = Path(materials)
    replacements = bindings.get('nativeFaceFallbacks', {})
    declared = grass_overlay_source(bindings)
    overlay = replacements.get(declared, declared)
    source = materials / sprite
    overlay_path = materials / overlay
    if not overlay_path.is_file():
        raise ValueError('Grass tint mask is missing: ' + overlay)
    with Image.open(source) as base, Image.open(overlay_path) as layer:
        encoded = encode_grass_side(base, layer)
        encoded.save(target)
    return {'material': 'textures/blocks/grass_side', 'base': sprite, 'overlay': overlay,
            'alpha_semantics': 'grass_biome_tint_mask',
            'soil_tinted': False, 'source_artwork_modified': False,
            'partial_alpha_preview_equivalence': False,
            'exact_java_layers_rendered_by_ctm': True}
