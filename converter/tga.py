"""Decode true-colour TGA images whose run-length packets cross from one row to the next.

Some Bedrock sample textures repeat one run across several rows. Pillow's TGA reader
rejects these files even when every declared pixel is present, so this reader expands
the pixel stream as one line first and only then applies the origin flags.
"""
from __future__ import annotations

from pathlib import Path
import struct

from PIL import Image

HEADER_SIZE = 18
MAX_PIXELS = 64 * 1024 * 1024
UNCOMPRESSED_TRUE_COLOR = 2
RLE_TRUE_COLOR = 10
# Image descriptor bits.
ALPHA_BITS = 0x0F
RIGHT_TO_LEFT = 0x10
TOP_TO_BOTTOM = 0x20
INTERLEAVED = 0xC0


def decode_tga(path: Path) -> Image.Image:
    """Read a 24- or 32-bit true-colour TGA, checking every packet and the pixel count."""
    data = Path(path).read_bytes()
    if len(data) < HEADER_SIZE:
        raise OSError("Truncated TGA header")
    (id_length, color_map, image_type, _, map_length, map_depth,
     _, _, width, height, depth, descriptor) = struct.unpack("<BBBHHBHHHHBB", data[:HEADER_SIZE])
    if color_map != 0 or map_length != 0 or map_depth != 0:
        raise OSError("TGA fallback does not support color maps")
    if image_type not in {UNCOMPRESSED_TRUE_COLOR, RLE_TRUE_COLOR} or depth not in {24, 32}:
        raise OSError("TGA fallback requires 24/32-bit true-color pixels")
    if descriptor & INTERLEAVED:
        raise OSError("TGA fallback does not support interleaved scanlines")
    if (descriptor & ALPHA_BITS) not in ({0, 8} if depth == 32 else {0}):
        raise OSError("Unsupported TGA alpha-channel depth")
    pixel_count = width * height
    if not pixel_count or pixel_count > MAX_PIXELS:
        raise OSError("Invalid or excessive TGA image dimensions")
    bytes_per_pixel = depth // 8
    position = HEADER_SIZE + id_length
    if position > len(data):
        raise OSError("Truncated TGA image ID")
    if image_type == UNCOMPRESSED_TRUE_COLOR:
        end = position + pixel_count * bytes_per_pixel
        if end > len(data):
            raise OSError("Truncated TGA pixel data")
        pixels = data[position:end]
    else:
        pixels = _expand_rle(data, position, pixel_count, bytes_per_pixel)
    mode, raw_mode = ("RGBA", "BGRA") if bytes_per_pixel == 4 else ("RGB", "BGR")
    image = Image.frombytes(mode, (width, height), pixels, "raw", raw_mode).convert("RGBA")
    if bytes_per_pixel == 4 and descriptor & ALPHA_BITS == 0:
        # 32-bit pixels that declare no alpha bits are opaque.
        image.putalpha(255)
    if descriptor & RIGHT_TO_LEFT:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if not descriptor & TOP_TO_BOTTOM:
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    return image


def _expand_rle(data, position, pixel_count, bytes_per_pixel):
    """The pixel bytes of run-length packets starting at position, as one unbroken line."""
    expanded = bytearray()
    decoded = 0
    while decoded < pixel_count:
        if position >= len(data):
            raise OSError("Truncated TGA RLE packet header")
        packet_header = data[position]
        position += 1
        count = (packet_header & 0x7F) + 1
        if decoded + count > pixel_count:
            raise OSError("TGA RLE packet overruns declared pixel count")
        # A run packet holds one pixel repeated count times; a raw packet holds count pixels.
        is_run = bool(packet_header & 0x80)
        payload_size = bytes_per_pixel if is_run else count * bytes_per_pixel
        end = position + payload_size
        if end > len(data):
            raise OSError("Truncated TGA RLE packet payload")
        payload = data[position:end]
        expanded.extend(payload * count if is_run else payload)
        position = end
        decoded += count
    return bytes(expanded)
