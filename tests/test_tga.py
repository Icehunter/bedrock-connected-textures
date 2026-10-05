"""Strict TGA fallback fixtures include a complete cross-scanline RLE run."""

from __future__ import annotations

from pathlib import Path
import struct
import sys
import tempfile
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "converter"))
from tga import decode_tga  # noqa: E402
from common import samples_path


def pixel_list(image):
    return [image.getpixel((x, y)) for y in range(image.height) for x in range(image.width)]


class TgaDecoderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "fixture.tga"

    def write(self, pixels, width=2, height=2, descriptor=40, depth=32, image_type=10):
        header = struct.pack("<BBBHHBHHHHBB", 0, 0, image_type, 0, 0, 0, 0, 0, width, height, depth, descriptor)
        self.path.write_bytes(header + pixels)

    def test_run_can_cross_multiple_scanlines(self):
        self.write(bytes([0x83, 30, 20, 10, 40]))
        image = decode_tga(self.path)
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.size, (2, 2))
        self.assertEqual(pixel_list(image), [(10, 20, 30, 40)] * 4)

    def test_truncated_payload_is_rejected(self):
        self.write(bytes([0x83, 30, 20]))
        with self.assertRaisesRegex(OSError, "Truncated.*payload"):
            decode_tga(self.path)

    def test_missing_packet_is_rejected(self):
        self.write(bytes([0x81, 30, 20, 10, 40]))
        with self.assertRaisesRegex(OSError, "Truncated.*header"):
            decode_tga(self.path)

    def test_packet_overrun_is_rejected(self):
        self.write(bytes([0x84, 30, 20, 10, 40]))
        with self.assertRaisesRegex(OSError, "overruns declared pixel count"):
            decode_tga(self.path)

    def test_all_image_origins(self):
        # Raw packet colors in storage order: red, green, blue, white.
        colors = bytes([0, 0, 255, 255, 0, 255, 0, 255,
                        255, 0, 0, 255, 255, 255, 255, 255])
        expected = {
            40: [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255), (255, 255, 255, 255)],
            8: [(0, 0, 255, 255), (255, 255, 255, 255), (255, 0, 0, 255), (0, 255, 0, 255)],
            56: [(0, 255, 0, 255), (255, 0, 0, 255), (255, 255, 255, 255), (0, 0, 255, 255)],
            24: [(255, 255, 255, 255), (0, 0, 255, 255), (0, 255, 0, 255), (255, 0, 0, 255)],
        }
        for descriptor, rgba in expected.items():
            self.write(bytes([3]) + colors, descriptor=descriptor)
            self.assertEqual(pixel_list(decode_tga(self.path)), rgba)

    def test_24bit_pixels_gain_opaque_alpha(self):
        self.write(bytes([0x83, 30, 20, 10]), depth=24, descriptor=32)
        self.assertEqual(pixel_list(decode_tga(self.path)), [(10, 20, 30, 255)] * 4)

    def test_uncompressed_pixels(self):
        self.write(bytes([30, 20, 10, 40] * 4), image_type=2)
        self.assertEqual(pixel_list(decode_tga(self.path)), [(10, 20, 30, 40)] * 4)

    def test_actual_shelf_mushroom_recovers_all_material_pixels(self):
        source = samples_path() / "resource_pack/textures/blocks/shelf_mushroom_small_mers.tga"
        if not source.exists():
            self.skipTest("Bedrock Samples checkout is not available")
        image = decode_tga(source)
        self.assertEqual(image.size, (32, 32))
        self.assertEqual(image.getpixel((0, 0)), (0, 0, 180, 0))
        self.assertEqual(image.getpixel((31, 31)), (0, 0, 75, 0))
        # A round-trip through a standard encoder checks that all rows are valid.
        image.save(self.path, compression="tga_rle")
        with Image.open(self.path) as reopened:
            self.assertEqual(reopened.convert("RGBA").tobytes(), image.tobytes())


if __name__ == "__main__":
    unittest.main()
