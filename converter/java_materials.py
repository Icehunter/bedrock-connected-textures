"""Turn a Java sprite and its LabPBR maps into a Bedrock texture set, keeping the author's files.

LabPBR _n (normal) and _s (specular) maps are decoded into Bedrock's normal and MERS
images. Whatever Bedrock cannot show is reported as a loss, and every LabPBR channel is
also kept as its own image, so nothing the author made is thrown away.

LabPBR 1.3: https://shaderlabs.org/wiki/LabPBR_Material_Standard
Bedrock: https://learn.microsoft.com/en-us/minecraft/creator/reference/content/texturesetsreference/texturesetsconcepts/texturesetsintroduction
"""
from dataclasses import dataclass
import hashlib
import io
import json
import struct
import zlib
from pathlib import Path

import numpy as np
from PIL import Image

from common import write_json
from image_io import PNG_SIGNATURE, checked_png
from java_texture_animation import Animation, compile_family, parse_animation

LABPBR_VERSION = '1.3'
MATERIAL_DECODER_REVISION = 2
LABPBR_CHANNELS = {
    'normal_x': '_n.R: DirectX tangent normal X',
    'normal_y': '_n.G: DirectX tangent normal Y',
    'ambient_occlusion': '_n.B: linear visibility, 0 occluded / 255 unoccluded',
    'height': '_n.A: linear height, 0 deepest / 255 surface',
    'perceptual_smoothness': '_s.R: perceptual smoothness',
    'reflectance_metal_id': '_s.G: linear dielectric F0 below 230; metal identity 230..255',
    'porosity_subsurface': '_s.B: porosity 0..64; subsurface 65..255',
    'emissive_raw': '_s.A: emission 0..254; 255 means no emission',
    'dielectric_f0': 'linear F0 byte below 230; zero for metals',
    'metal_id': 'source metal identity 230..255; zero for dielectrics',
    'metalness': 'decoded metal mask',
    'porosity': 'decoded linear porosity, 0..64 mapped to 0..255; zero for subsurface',
    'subsurface': 'decoded linear subsurface, 65..255 mapped to 0..255; zero for porosity',
    'emission': 'decoded linear emission, 0..254 mapped to 0..255; sentinel 255 mapped to zero',
    'roughness_perceptual': '1 - perceptual smoothness',
    'roughness_linear': '(1 - perceptual smoothness)^2',
}
# LabPBR specular green from 230 up names a metal; below it is the dielectric reflectance.
METAL_FROM = 230
# Bedrock assumes the usual 4% dielectric reflectance, LabPBR green 10.
DEFAULT_DIELECTRIC_F0 = 10
# PNG colour type -> samples per pixel.
SAMPLES_PER_PIXEL = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


@dataclass(frozen=True)
class MaterialPolicy:
    """How LabPBR maps become Bedrock ones; every choice is explicit."""
    encoding: str
    normal_y: str
    roughness: str
    allow_losses: bool = False

    def validate(self):
        if self.encoding != 'labpbr-1.3':
            raise ValueError('Select a supported material encoding explicitly')
        if self.normal_y not in ('directx', 'opengl'):
            raise ValueError('Specify target normal Y convention')
        if self.roughness not in ('linear', 'perceptual'):
            raise ValueError('Specify target roughness convention')


def source_image(data):
    """Decode a source PNG as RGBA. Returns (image, repair report or None).

    Some author archives have valid chunk CRCs and complete compressed data but a stale
    zlib (Adler-32) checksum. Only that checksum is corrected, and only once every
    scanline decodes; Pillow's truncated-image fallback is never used, and the original
    source bytes are kept as they are.
    """
    try:
        return Image.open(io.BytesIO(data)).convert('RGBA'), None
    except OSError as original:
        repaired = _with_corrected_adler32(data)
        if repaired is None:
            raise original
        png, repair = repaired
        return Image.open(io.BytesIO(png)).convert('RGBA'), repair


def decode_labpbr(normal=None, specular=None, *, policy):
    """Decode LabPBR maps: Bedrock images, every source channel kept as an image, and renderer losses.

    Returns {'images': {Bedrock channel: image}, 'preserved_channels': {channel: image},
    'renderer_losses': [what Bedrock cannot show]}.
    """
    policy.validate()
    images = {}
    extras = {}
    losses = []
    if normal is not None:
        images['normal'], normal_extras, normal_losses = _decode_normal(normal, policy)
        extras.update(normal_extras)
        losses.extend(normal_losses)
    if specular is not None:
        mers, specular_extras, specular_losses = _decode_specular(specular, policy)
        images['metalness_emissive_roughness_subsurface'] = mers
        extras.update(specular_extras)
        losses.extend(specular_losses)
    if normal is not None and specular is not None and normal.size != specular.size:
        raise ValueError('Material map dimensions differ; no resampling permitted')
    return {'images': images, 'preserved_channels': extras, 'renderer_losses': losses}


def compile_stack_material(stack, sprite, destination, relative, *, policy, animation_layout='strip',
                           interpolation='bake'):
    """Compile one sprite of the pack stack, with its LabPBR maps and animation, into a texture set.

    Every original source byte is kept under conversion_sources/ and every LabPBR channel
    under conversion_channels/, beside the runtime files; binding the material to blocks is
    a separate step. interpolation 'bake' blends Java's interpolated frames into the
    flipbook; 'native' leaves the blending to Bedrock.
    """
    policy.validate()
    if interpolation not in ('bake', 'native'):
        raise ValueError('Unknown interpolation policy')
    destination = Path(destination).resolve()
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Use a contained relative material path')
    target = (destination / relative).resolve()
    if not target.is_relative_to(destination) or target.suffix != '.png':
        raise ValueError('Unsafe material output')
    paths = {'color': sprite, 'normal': sprite[:-4] + '_n.png', 'specular': sprite[:-4] + '_s.png'}
    source = {channel: stack.read(path) for channel, path in paths.items() if path in stack.files}
    if 'color' not in source:
        raise ValueError('Missing material color')
    decoded = {}
    repairs = {}
    for channel, data in source.items():
        decoded[channel], repair = source_image(data)
        if repair:
            repairs[channel] = repair
    metadata = {channel: stack.read(path + '.mcmeta') for channel, path in paths.items()
                if channel in source and path + '.mcmeta' in stack.files}
    animations, animation_diagnostics = _source_animations(metadata, decoded)
    flipbook = None
    native_blend = False
    if animations:
        animation = animations.get('color')
        if animation is None:
            color = decoded['color']
            if color.width != color.height:
                raise ValueError('Non-square color requires animation metadata')
            animation = Animation(color.width, color.height, ((0, 1),), False)
        if interpolation == 'native':
            native_blend = any(value.interpolate for value in animations.values())
            animations = {channel: Animation(value.width, value.height, value.frames, False)
                          for channel, value in animations.items()}
            animation = Animation(animation.width, animation.height, animation.frames, False)
        # The encoded source channels are animated before the LabPBR decoding, which is not
        # linear. Java keeps each current frame's alpha when it blends frames, and here the
        # alpha holds data: the emission sentinel and the normal map's height.
        decoded, flipbook = compile_family(decoded, animation, relative.with_suffix('').as_posix(), target.stem,
                                           channel_animations=animations, color_channels=set(decoded),
                                           layout=animation_layout,
                                           max_side=65536 if interpolation == 'native' else 16384)
        flipbook['blend_frames'] = native_blend
    elif any(image.size != decoded['color'].size for image in decoded.values()):
        raise ValueError('Material maps must match color dimensions')
    result = decode_labpbr(decoded.get('normal'), decoded.get('specular'), policy=policy)
    if result['renderer_losses'] and not policy.allow_losses:
        raise ValueError('Target cannot represent material channels: ' + ', '.join(result['renderer_losses']))
    # The author's colour bytes are copied as they are unless they had to change: frames were
    # baked, a checksum was repaired, or the PNG is palette or grey, which texture sets do not take.
    if flipbook or 'color' in repairs or Image.open(io.BytesIO(source['color'])).mode not in ('RGB', 'RGBA'):
        color_bytes = _png_bytes(decoded['color'])
    else:
        color_bytes = source['color']
    products = {relative.as_posix(): color_bytes}
    if flipbook:
        products[relative.as_posix() + '.mcmeta'] = _flipbook_mcmeta(flipbook, native_blend, animation_layout)
    descriptor = {'color': target.stem}
    for channel, image in result['images'].items():
        suffix = 'normal' if channel == 'normal' else 'mers'
        path = relative.with_name(target.stem + '_' + suffix + '.png')
        products[path.as_posix()] = _png_bytes(image)
        descriptor[channel] = path.stem
    # The originals and the extra channels sit outside the files the game loads.
    sources_folder = Path('conversion_sources') / relative.parent
    channels_folder = Path('conversion_channels') / relative.parent
    for channel, data in source.items():
        products[(sources_folder / (target.stem + '_' + channel + '.png')).as_posix()] = data
    for channel, data in metadata.items():
        products[(sources_folder / (target.stem + '_' + channel + '.png.mcmeta')).as_posix()] = data
    for channel, image in result['preserved_channels'].items():
        products[(channels_folder / (target.stem + '_' + channel + '.png')).as_posix()] = _png_bytes(image)
    channel_manifest = (Path('conversion_channels') / relative.with_suffix('.channels.json')).as_posix()
    products[channel_manifest] = _channel_manifest(sprite, target.stem, result)
    for path in products:
        output = destination / path
        if output.exists():
            raise ValueError('Material output already exists: ' + str(output))
    descriptor_path = target.with_suffix('.texture_set.json')
    if descriptor_path.exists():
        raise ValueError('Material descriptor already exists')
    for path, data in products.items():
        output = destination / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
    if result['images']:
        write_json(descriptor_path, {'format_version': '1.21.30', 'minecraft:texture_set': descriptor})
    outputs = [{'path': path, 'sha256': hashlib.sha256(data).hexdigest()} for path, data in products.items()]
    if result['images']:
        outputs.append({'path': descriptor_path.relative_to(destination).as_posix(),
                        'sha256': hashlib.sha256(descriptor_path.read_bytes()).hexdigest()})
    return {'source': sprite, 'status': 'material_exported', 'outputs': outputs,
            'renderer_losses': result['renderer_losses'], 'policy': policy.__dict__,
            'decoder_revision': MATERIAL_DECODER_REVISION, 'channel_manifest': channel_manifest,
            'extracted_channels': list(result['preserved_channels']),
            'source_repairs': repairs,
            'animation_diagnostics': animation_diagnostics,
            'interpolation_policy': interpolation,
            'interpolation_differences': (['Bedrock blends decoded material channels and alpha between keyframes']
                                          if native_blend else []),
            'descriptor': descriptor_path.relative_to(destination).as_posix() if result['images'] else None,
            'animation': flipbook, 'binding_verified': False, 'full_support_verified': False}


def _byte_channel(values):
    """Values from 0 to 1 as bytes, clamped and rounded to nearest."""
    return np.floor(np.clip(values, 0, 1) * 255 + .5).astype(np.uint8)


def _decode_normal(normal, policy):
    """(Bedrock normal image, kept channels, losses) from a LabPBR _n map.

    LabPBR stores only X and Y; Z is rebuilt so the normal has unit length. Blue is
    ambient occlusion and alpha is height, which a Bedrock normal map cannot carry.
    """
    rgba = normal.convert('RGBA')
    data = np.asarray(rgba, dtype=np.uint8)
    xy = data[:, :, :2].astype(np.float64) / 255 * 2 - 1
    z = np.sqrt(np.maximum(0, 1 - np.sum(xy * xy, axis=2)))
    length = np.sqrt(np.sum(xy * xy, axis=2) + z * z)
    xy = xy / length[:, :, None]
    z = z / length
    if policy.normal_y == 'opengl':
        xy[:, :, 1] *= -1
    image = Image.fromarray(np.dstack((_byte_channel(xy * .5 + .5), _byte_channel(z * .5 + .5))))
    extras = {'normal_x': rgba.getchannel('R'), 'normal_y': rgba.getchannel('G'),
              'ambient_occlusion': rgba.getchannel('B'), 'height': rgba.getchannel('A')}
    losses = []
    if np.any(data[:, :, 2] != 255):
        losses.append('material_ambient_occlusion')
    if np.any(data[:, :, 3] != 255):
        losses.append('height_with_normal')
    return image, extras, losses


def _decode_specular(specular, policy):
    """(Bedrock MERS image, kept channels, losses) from a LabPBR _s map.

    Red is perceptual smoothness, green reflectance or a metal, blue porosity (0 to 64) or
    subsurface (65 up), alpha emission with 255 meaning none.
    """
    rgba = specular.convert('RGBA')
    data = np.asarray(rgba, dtype=np.uint8)
    red, green, blue, alpha = (data[:, :, index] for index in range(4))
    smoothness = red.astype(np.float64) / 255
    roughness = 1 - smoothness
    if policy.roughness == 'linear':
        roughness = roughness ** 2
    metalness = np.where(green >= METAL_FROM, 255, 0).astype(np.uint8)
    emission = _byte_channel(np.where(alpha == 255, 0, alpha.astype(np.float64) / 254))
    subsurface = _byte_channel(np.maximum(0, blue.astype(np.float64) - 65) / 190)
    image = Image.fromarray(np.dstack((metalness, emission, _byte_channel(roughness), subsurface)))
    extras = {
        'reflectance_metal_id': rgba.getchannel('G'),
        'porosity_subsurface': rgba.getchannel('B'),
        'perceptual_smoothness': rgba.getchannel('R'),
        'emissive_raw': rgba.getchannel('A'),
        'dielectric_f0': Image.fromarray(np.where(green < METAL_FROM, green, 0).astype(np.uint8)),
        'metal_id': Image.fromarray(np.where(green >= METAL_FROM, green, 0).astype(np.uint8)),
        'metalness': Image.fromarray(metalness),
        'porosity': Image.fromarray(_byte_channel(np.where(blue <= 64, blue.astype(np.float64) / 64, 0))),
        'subsurface': Image.fromarray(subsurface),
        'emission': Image.fromarray(emission),
        'roughness_perceptual': Image.fromarray(255 - red),
        'roughness_linear': Image.fromarray(_byte_channel((1 - smoothness) ** 2)),
    }
    losses = []
    if np.any((green < METAL_FROM) & (green != DEFAULT_DIELECTRIC_F0)):
        losses.append('dielectric_reflectance')
    if np.any((green >= METAL_FROM) & (green < 255)):
        losses.append('metal_optical_constants')
    if np.any((blue > 0) & (blue <= 64)):
        losses.append('porosity')
    return image, extras, losses


def _source_animations(metadata, decoded):
    """({channel: Animation}, {channel: diagnostics}) from the .mcmeta files, read as Java reads them."""
    animations = {}
    diagnostics_by_channel = {}
    for channel, data in metadata.items():
        document = json.loads(data.decode('utf-8-sig'))
        if 'animation' in document:
            diagnostics = []
            animations[channel] = parse_animation(decoded[channel].size, document, invalid_frames='java',
                                                  diagnostics=diagnostics)
            if diagnostics:
                diagnostics_by_channel[channel] = diagnostics
    return animations, diagnostics_by_channel


def _flipbook_mcmeta(flipbook, native_blend, animation_layout):
    """The .mcmeta written beside a baked flipbook, describing its frames."""
    animation = {'frametime': flipbook['ticks_per_frame'], 'frames': flipbook['frames'], 'interpolate': native_blend}
    if animation_layout == 'grid':
        animation.update({'width': flipbook['frame_width'], 'height': flipbook['frame_height']})
    return json.dumps({'animation': animation}, separators=(',', ':')).encode('utf-8')


def _channel_manifest(sprite, stem, result):
    """JSON describing each kept LabPBR channel file and what Bedrock cannot show."""
    channels = {channel: {'file': stem + '_' + channel + '.png', 'meaning': LABPBR_CHANNELS[channel]}
                for channel in result['preserved_channels']}
    manifest = {'format': 'labpbr', 'version': LABPBR_VERSION, 'decoder_revision': MATERIAL_DECODER_REVISION,
                'source': sprite, 'channels': channels, 'renderer_losses': result['renderer_losses'],
                'unsupported_channels_preserved': True}
    return (json.dumps(manifest, indent=2) + '\n').encode()


def _png_bytes(image):
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def _with_corrected_adler32(data):
    """(PNG bytes with the zlib checksum corrected, repair report), or None when that is not the fault."""
    checked, removed = checked_png(data)
    chunks, header, compressed = _png_chunks(checked)
    # The image data needs at least a 2-byte zlib header and a 4-byte checksum, and the
    # repair handles only non-interlaced images.
    if header is None or header[6] != 0 or len(compressed) < 6:
        return None
    width, height, depth, color_type, compression, filter_method, _ = header
    samples = SAMPLES_PER_PIXEL.get(color_type)
    if samples is None or compression or filter_method:
        return None
    decoder = zlib.decompressobj(-zlib.MAX_WBITS)
    try:
        raw = decoder.decompress(bytes(compressed[2:-4])) + decoder.flush()
    except zlib.error:
        return None
    row_bytes = (width * samples * depth + 7) // 8
    if (not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
            or len(raw) != (row_bytes + 1) * height):
        return None
    # Each scanline starts with a filter type from 0 to 4.
    if any(raw[row * (row_bytes + 1)] > 4 for row in range(height)):
        return None
    checksum = zlib.adler32(raw) & 0xffffffff
    if checksum == int.from_bytes(compressed[-4:], 'big'):
        return None
    corrected = bytes(compressed[:-4]) + struct.pack('>I', checksum)
    zlib.decompress(corrected)
    report = {'kind': 'zlib_adler_checksum', 'original_adler': bytes(compressed[-4:]).hex(),
              'decoded_adler': f'{checksum:08x}', 'decoded_scanline_bytes': len(raw),
              'critical_png_crcs_valid': True, 'removed_invalid_metadata_chunks': removed}
    return _rebuilt_png(chunks, corrected), report


def _png_chunks(png):
    """([(type, payload)] up to IEND, IHDR fields or None, all IDAT payloads joined) of a checked PNG."""
    chunks = []
    compressed = bytearray()
    header = None
    offset = len(PNG_SIGNATURE)
    while offset < len(png):
        length = struct.unpack_from('>I', png, offset)[0]
        chunk_type = png[offset + 4:offset + 8]
        payload = png[offset + 8:offset + 8 + length]
        chunks.append((chunk_type, payload))
        if chunk_type == b'IHDR':
            header = struct.unpack('>IIBBBBB', payload)
        if chunk_type == b'IDAT':
            compressed.extend(payload)
        offset += length + 12
        if chunk_type == b'IEND':
            break
    return chunks, header, compressed


def _rebuilt_png(chunks, image_data):
    """PNG bytes from chunks with all image data in the first IDAT, every CRC recomputed."""
    result = bytearray(PNG_SIGNATURE)
    wrote_image_data = False
    for chunk_type, payload in chunks:
        if chunk_type == b'IDAT':
            if wrote_image_data:
                continue
            payload = image_data
            wrote_image_data = True
        crc = zlib.crc32(chunk_type + payload) & 0xffffffff
        result.extend(struct.pack('>I', len(payload)) + chunk_type + payload + struct.pack('>I', crc))
    return result
