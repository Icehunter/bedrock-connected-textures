"""LabPBR maps become Bedrock texture sets: exact channel maths, kept originals, and no partial output."""
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from java_materials import MaterialPolicy, decode_labpbr, compile_stack_material, source_image

DIRECTX_LINEAR = MaterialPolicy('labpbr-1.3', 'directx', 'linear')
DIRECTX_PERCEPTUAL = MaterialPolicy('labpbr-1.3', 'directx', 'perceptual')


def png(color, size=(2, 2)):
    """PNG bytes of an RGBA image filled with one colour."""
    buffer = io.BytesIO()
    Image.new('RGBA', size, color).save(buffer, format='PNG')
    return buffer.getvalue()


def column_png(pixels):
    """PNG bytes of a one-pixel-wide RGBA image with these pixels from top to bottom."""
    image = Image.new('RGBA', (1, len(pixels)))
    image.putdata(pixels)
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


class MemoryStack:
    """A pack stack whose files are bytes in memory."""

    def __init__(self, files):
        self.files = files

    def read(self, path):
        return self.files[path]


class MaterialTests(unittest.TestCase):
    def test_only_complete_png_with_bad_adler_is_recovered(self):
        buffer = io.BytesIO()
        original = Image.new('RGBA', (3, 2), (7, 30, 61, 90))
        original.save(buffer, format='PNG')
        data = buffer.getvalue()
        offset = 8
        broken = bytearray(data[:8])
        while offset < len(data):
            length = int.from_bytes(data[offset:offset + 4], 'big')
            kind = data[offset + 4:offset + 8]
            payload = data[offset + 8:offset + 8 + length]
            if kind == b'IDAT':
                # Flip a bit of the zlib checksum, then give the chunk a valid CRC again.
                payload = payload[:-1] + bytes([payload[-1] ^ 1])
            crc = zlib.crc32(kind + payload) & 0xffffffff
            broken.extend(struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', crc))
            offset += length + 12
        decoded, receipt = source_image(bytes(broken))
        self.assertEqual(decoded.tobytes(), original.tobytes())
        self.assertEqual(receipt['kind'], 'zlib_adler_checksum')
        with self.assertRaises((OSError, ValueError)):
            source_image(bytes(broken[:-15]))

    def test_labpbr_boundaries_and_emission_sentinel(self):
        specular = Image.new('RGBA', (6, 1))
        specular.putdata([(0, 10, 0, 255), (255, 230, 65, 254), (128, 229, 64, 127),
                          (0, 255, 255, 0), (0, 10, 66, 1), (0, 10, 0, 253)])
        result = decode_labpbr(specular=specular, policy=DIRECTX_LINEAR)
        raw = result['images']['metalness_emissive_roughness_subsurface'].tobytes()
        pixels = [tuple(raw[index:index + 4]) for index in range(0, len(raw), 4)]
        self.assertEqual(pixels[:4], [(0, 0, 255, 0), (255, 255, 0, 0), (0, 128, 63, 0), (255, 0, 255, 255)])
        self.assertEqual(set(result['renderer_losses']),
                         {'dielectric_reflectance', 'metal_optical_constants', 'porosity'})

    def test_normal_blue_is_reconstructed_and_source_data_preserved(self):
        normal = Image.new('RGBA', (1, 1), (128, 128, 12, 50))
        result = decode_labpbr(normal=normal, policy=DIRECTX_PERCEPTUAL)
        self.assertEqual(result['images']['normal'].getpixel((0, 0)), (128, 128, 255))
        self.assertEqual(result['preserved_channels']['ambient_occlusion'].getpixel((0, 0)), 12)
        self.assertEqual(result['preserved_channels']['height'].getpixel((0, 0)), 50)
        flipped = decode_labpbr(normal=normal, policy=MaterialPolicy('labpbr-1.3', 'opengl', 'perceptual'))
        self.assertEqual(flipped['images']['normal'].getpixel((0, 0))[1], 127)

    def test_all_labpbr_channels_have_explicit_extractions(self):
        normal = Image.new('RGBA', (3, 1), (128, 90, 44, 190))
        specular = Image.new('RGBA', (3, 1))
        specular.putdata([(128, 229, 64, 255), (255, 230, 65, 254), (0, 255, 255, 127)])
        channels = decode_labpbr(normal, specular, policy=DIRECTX_PERCEPTUAL)['preserved_channels']
        self.assertEqual(len(channels), 16)
        self.assertEqual(channels['normal_y'].getpixel((0, 0)), 90)
        self.assertEqual(channels['perceptual_smoothness'].getpixel((0, 0)), 128)
        self.assertEqual(list(channels['dielectric_f0'].getdata()), [229, 0, 0])
        self.assertEqual(list(channels['metal_id'].getdata()), [0, 230, 255])
        self.assertEqual(list(channels['porosity'].getdata()), [255, 0, 0])
        self.assertEqual(list(channels['subsurface'].getdata()), [0, 0, 255])
        self.assertEqual(list(channels['emissive_raw'].getdata()), [255, 254, 127])
        self.assertEqual(list(channels['emission'].getdata()), [0, 255, 128])
        self.assertEqual(channels['roughness_linear'].getpixel((0, 0)), 63)

    def test_channel_manifest_names_each_preserved_map(self):
        source = {'a.png': png((12, 30, 50, 255)), 'a_n.png': png((128, 128, 34, 90)),
                  'a_s.png': png((128, 10, 0, 255))}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            receipt = compile_stack_material(MemoryStack(source), 'a.png', output, 'textures/a.png',
                                             policy=MaterialPolicy('labpbr-1.3', 'directx', 'perceptual', True))
            manifest_path = output / receipt['channel_manifest']
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(manifest['version'], '1.3')
            self.assertEqual(len(manifest['channels']), 16)
            self.assertTrue(all(manifest_path.parent.joinpath(item['file']).is_file()
                                for item in manifest['channels'].values()))

    def test_palette_colour_is_written_as_rgba(self):
        palette = Image.new('RGBA', (2, 2), (200, 40, 10, 255)).convert('P')
        buffer = io.BytesIO()
        palette.save(buffer, format='PNG')
        source = {'a.png': buffer.getvalue()}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            compile_stack_material(MemoryStack(source), 'a.png', output, 'textures/a.png',
                                   policy=MaterialPolicy('labpbr-1.3', 'directx', 'perceptual', True))
            with Image.open(output / 'textures/a.png') as written:
                self.assertEqual(written.mode, 'RGBA')
                self.assertEqual(written.getpixel((0, 0)), (200, 40, 10, 255))

    def test_loss_gate_is_checked_before_any_output_write(self):
        source = {'a.png': png((1, 2, 3, 20)), 'a_n.png': png((128, 128, 12, 50))}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'output'
            with self.assertRaises(ValueError):
                compile_stack_material(MemoryStack(source), 'a.png', output, 'textures/a.png', policy=DIRECTX_LINEAR)
            self.assertFalse(output.exists())
            receipt = compile_stack_material(MemoryStack(source), 'a.png', output, 'textures/a.png',
                                             policy=MaterialPolicy('labpbr-1.3', 'directx', 'linear', True))
            self.assertEqual((output / 'textures/a.png').read_bytes(), source['a.png'])
            self.assertEqual((output / 'conversion_sources/textures/a_normal.png').read_bytes(), source['a_n.png'])
            self.assertFalse(receipt['binding_verified'])

    def test_animated_encoded_maps_are_synchronized_before_decode(self):
        metadata = json.dumps({'animation': {'frametime': 2, 'interpolate': True}}).encode()
        source = {'a.png': column_png([(20, 30, 40, 19), (100, 110, 120, 200)]),
                  'a.png.mcmeta': metadata,
                  'a_s.png': column_png([(0, 10, 0, 255), (255, 10, 0, 254)]),
                  'a_n.png': column_png([(128, 128, 255, 255)])}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'output'
            receipt = compile_stack_material(MemoryStack(source), 'a.png', output, 'textures/a.png',
                                             policy=DIRECTX_LINEAR)
            self.assertEqual(receipt['animation']['frames'], [0, 1, 2, 3])
            with Image.open(output / 'textures/a.png') as color:
                self.assertEqual(color.getpixel((0, 1)), (60, 70, 80, 19))
            with Image.open(output / 'textures/a_mers.png') as material:
                # Decode (1 - 127/255)^2, rather than interpolating decoded
                # endpoint roughness. The emission sentinel remains off.
                self.assertEqual(material.getpixel((0, 1)), (0, 0, 64, 0))
                self.assertEqual(material.getpixel((0, 3)), (0, 255, 64, 0))
            self.assertEqual((output / 'conversion_sources/textures/a_color.png.mcmeta').read_bytes(), metadata)
            written = json.loads((output / 'textures/a.png.mcmeta').read_text())
            self.assertEqual(written['animation']['frames'], [0, 1, 2, 3])

    def test_invalid_animated_channel_writes_nothing(self):
        buffer = io.BytesIO()
        Image.new('RGBA', (2, 2)).save(buffer, format='PNG')
        source = {'a.png': buffer.getvalue(), 'a_s.png': buffer.getvalue(),
                  'a_s.png.mcmeta': b'{"animation":{"frames":[true]}}'}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'output'
            with self.assertRaisesRegex(ValueError, 'Invalid animation frame'):
                compile_stack_material(MemoryStack(source), 'a.png', output, 'textures/a.png', policy=DIRECTX_LINEAR)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
