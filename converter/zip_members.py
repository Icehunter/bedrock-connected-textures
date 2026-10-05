"""Copy members between ZIP archives without recompressing them.

A converted pack keeps the author's compressed artwork byte for byte: the compressed data
is copied under a new name with a fresh local header, so neither the pixels nor the
compression change.
"""
import copy
from pathlib import PurePosixPath
import struct
import zipfile

LOCAL_HEADER_SIZE = 30
LOCAL_HEADER_SIGNATURE = b'PK\x03\x04'
COPY_CHUNK_SIZE = 8 * 1024 * 1024
# ZIP64 sizes and offsets, and the Unicode path field's CRC of the old name, are wrong
# after the copy; zipfile writes fresh ones.
_REWRITTEN_EXTRA_FIELDS = (0x0001, 0x7075)


def member_name(value):
    """A ZIP member name, checked so it cannot point outside the archive's folders."""
    path = PurePosixPath(value)
    if not value or '\\' in value or path.is_absolute() or '..' in path.parts or ':' in value:
        raise ValueError('Unsafe pack member: ' + value)
    return path.as_posix()


def copy_compressed(source, entry, output, name):
    """Copy entry's compressed bytes from the source ZipFile into the output ZipFile as name."""
    if entry.flag_bits & 1:
        raise ValueError('Encrypted pack members are unsupported')
    if entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise ValueError('Bedrock packs require stored or deflated ZIP members')
    source.fp.seek(entry.header_offset)
    header = source.fp.read(LOCAL_HEADER_SIZE)
    if len(header) != LOCAL_HEADER_SIZE or header[:4] != LOCAL_HEADER_SIGNATURE:
        raise ValueError('Invalid ZIP local header')
    name_size, extra_size = struct.unpack('<HH', header[26:30])
    source.fp.seek(name_size + extra_size, 1)
    info = copy.copy(entry)
    info.filename = info.orig_filename = name
    # The new header carries the sizes, so no data descriptor follows the data.
    info.flag_bits &= ~8
    info.extra = _without_rewritten_extras(info.extra)
    # zipfile has no public way to add data that is already compressed; these are the
    # steps ZipFile.write takes inside.
    output._writecheck(info)
    output._didModify = True
    info.header_offset = output.fp.tell()
    needs_zip64 = info.file_size > zipfile.ZIP64_LIMIT or info.compress_size > zipfile.ZIP64_LIMIT
    output.fp.write(info.FileHeader(needs_zip64))
    remaining = info.compress_size
    while remaining:
        chunk = source.fp.read(min(COPY_CHUNK_SIZE, remaining))
        if not chunk:
            raise ValueError('Truncated compressed pack member')
        output.fp.write(chunk)
        remaining -= len(chunk)
    output.filelist.append(info)
    output.NameToInfo[name] = info
    output.start_dir = output.fp.tell()


def _without_rewritten_extras(extra):
    """The member's extra fields minus the ones zipfile rewrites for the copy."""
    kept = bytearray()
    while extra:
        if len(extra) < 4:
            raise ValueError('Truncated ZIP extra field')
        field_id, size = struct.unpack('<HH', extra[:4])
        if len(extra) < size + 4:
            raise ValueError('Truncated ZIP extra value')
        if field_id not in _REWRITTEN_EXTRA_FIELDS:
            kept.extend(extra[:size + 4])
        extra = extra[size + 4:]
    return bytes(kept)
