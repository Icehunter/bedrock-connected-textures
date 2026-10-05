"""Texture loading gets past bad metadata checksums but never past damaged pixels."""
import io
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
from image_io import load_rgba
from common import samples_path


class ImageIOTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "test.png"
        self.pixels = np.random.default_rng(17).integers(0, 256, (7, 11, 4), dtype=np.uint8)
        image = io.BytesIO()
        Image.fromarray(self.pixels).save(image, format="PNG")
        self.png = image.getvalue()

    def tearDown(self):
        self.directory.cleanup()

    def test_bad_ancillary_crc_retains_image_pixels(self):
        payload = b"Comment\x00metadata"
        kind = b"tEXt"
        bad_crc = (zlib.crc32(kind + payload) ^ 1) & 0xFFFFFFFF
        chunk = struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", bad_crc)
        self.path.write_bytes(self.png[:33] + chunk + self.png[33:])
        np.testing.assert_array_equal(np.asarray(load_rgba(self.path)), self.pixels)

    def test_critical_crc_error_is_rejected(self):
        data = bytearray(self.png)
        offset = 8
        while offset < len(data):
            length = struct.unpack_from(">I", data, offset)[0]
            if data[offset + 4:offset + 8] == b"IDAT":
                data[offset + length + 11] ^= 1
                break
            offset += length + 12
        self.path.write_bytes(data)
        with self.assertRaisesRegex(OSError, "test.png.*critical PNG chunk IDAT"):
            load_rgba(self.path)

    def test_truncated_chunk_is_rejected(self):
        self.path.write_bytes(self.png[:40])
        with self.assertRaisesRegex(OSError, "test.png"):
            load_rgba(self.path)

    def test_bad_transparency_chunk_is_not_discarded(self):
        payload = b"\x00\x00\x00\x00\x00\x00"
        kind = b"tRNS"
        bad_crc = (zlib.crc32(kind + payload) ^ 1) & 0xFFFFFFFF
        chunk = struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", bad_crc)
        self.path.write_bytes(self.png[:33] + chunk + self.png[33:])
        with self.assertRaisesRegex(OSError, "PNG data chunk tRNS"):
            load_rgba(self.path)

    def test_sample_pngs_with_bad_profiles_decode(self):
        samples = samples_path() / "resource_pack" / "textures" / "ui"
        if not samples.is_dir():
            self.skipTest("Bedrock sample checkout is not available.")
        names = ("equipped_item_border", "topbar_off_left", "topbar_off_middle", "topbar_on_left",
                 "topbar_on_middle", "topbar_on_right")
        for name in names:
            with self.subTest(name=name):
                source = samples / f"{name}.png"
                before = source.read_bytes()
                image = load_rgba(source)
                self.assertEqual(image.size, (18, 18) if name == "equipped_item_border" else (5, 5))
                self.assertEqual(image.mode, "RGBA")
                self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
