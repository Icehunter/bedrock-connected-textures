"""Load texture images, getting past damage that does not touch the pixels.

Some packs ship PNGs whose metadata chunks (colour profile, text, time) have bad
checksums, which Pillow refuses. Those chunks are dropped and the image is read again;
a bad checksum on a chunk that holds pixels is still an error. TGA files Pillow cannot
read go to tga.decode_tga.
"""
from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path

from PIL import Image

from tga import decode_tga

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Chunks that describe an image but hold none of its pixels.
_PNG_METADATA_CHUNKS = {b"iCCP", b"gAMA", b"cHRM", b"sRGB", b"cICP", b"mDCV", b"cLLI",
                        b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"pHYs", b"tIME", b"bKGD", b"hIST"}
# A chunk is length (4 bytes), type (4), payload, then a CRC (4) of type and payload.
_CHUNK_OVERHEAD = 12


def load_rgba(path: Path) -> Image.Image:
    """Read an image as RGBA, dropping only PNG metadata chunks with bad checksums."""
    path = Path(path)
    try:
        data = path.read_bytes()
        is_png = data.startswith(PNG_SIGNATURE)
        try:
            image = _decode_rgba(data)
        except (OSError, ValueError, SyntaxError) as original_error:
            if is_png:
                repaired, removed = checked_png(data)
                if not removed:
                    raise original_error
                return _decode_rgba(repaired)
            if path.suffix.lower() == ".tga" and isinstance(original_error, OSError):
                return decode_tga(path)
            raise
        if is_png:
            # Pillow can accept a bad IDAT checksum once it has decoded the pixels.
            checked_png(data)
        return image
    except (OSError, ValueError, SyntaxError, struct.error) as error:
        raise OSError(f"Cannot decode texture {path}: {error}") from error


def checked_png(data: bytes) -> tuple[bytes, int]:
    """Check every chunk of a PNG and return it without metadata chunks whose CRC is wrong.

    Returns (PNG bytes, number of chunks removed). Raises ValueError for a broken chunk
    layout or a bad CRC on any chunk that is not plain metadata.
    """
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("Invalid PNG signature.")
    result = bytearray(PNG_SIGNATURE)
    removed = 0
    saw_header = saw_image_data = saw_end = False
    chunk_start = len(PNG_SIGNATURE)
    while chunk_start < len(data):
        if len(data) - chunk_start < _CHUNK_OVERHEAD:
            raise ValueError("Truncated PNG chunk header.")
        payload_size = struct.unpack_from(">I", data, chunk_start)[0]
        chunk_end = chunk_start + payload_size + _CHUNK_OVERHEAD
        if chunk_end > len(data):
            raise ValueError("PNG chunk extends beyond the file.")
        chunk_type = data[chunk_start + 4:chunk_start + 8]
        if not all(65 <= value <= 90 or 97 <= value <= 122 for value in chunk_type):
            raise ValueError("Invalid PNG chunk name.")
        if not saw_header and chunk_type != b"IHDR":
            raise ValueError("PNG starts without IHDR.")
        stored_crc = struct.unpack_from(">I", data, chunk_end - 4)[0]
        computed_crc = zlib.crc32(data[chunk_start + 4:chunk_end - 4]) & 0xFFFFFFFF
        if computed_crc != stored_crc:
            # A lowercase first letter (bit 0x20) marks an ancillary chunk; the rest are critical.
            if not chunk_type[0] & 0x20:
                raise ValueError(f"Invalid CRC in critical PNG chunk {chunk_type.decode('ascii')}.")
            if chunk_type not in _PNG_METADATA_CHUNKS:
                raise ValueError(f"Invalid CRC in PNG data chunk {chunk_type.decode('ascii')}.")
            removed += 1
        else:
            result.extend(data[chunk_start:chunk_end])
        if chunk_type == b"IHDR":
            if saw_header or payload_size != 13:
                raise ValueError("Invalid PNG IHDR.")
            saw_header = True
        elif chunk_type == b"IDAT":
            saw_image_data = True
        elif chunk_type == b"IEND":
            if payload_size != 0:
                raise ValueError("Invalid PNG IEND.")
            saw_end = True
            # Bytes after IEND do not change the image, so they are kept as they are.
            result.extend(data[chunk_end:])
            break
        chunk_start = chunk_end
    if not (saw_header and saw_image_data and saw_end):
        raise ValueError("PNG is missing a required image chunk.")
    return bytes(result), removed


def _decode_rgba(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as image:
        return image.convert("RGBA")
