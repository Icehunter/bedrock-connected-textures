"""Restoring a world's blocks without the game: every BCT block in the save goes back to its vanilla block.

For worlds whose BCT packs are already gone, or where commands are off. The
world's LevelDB is read (its tables, then its logs on top); each sub-chunk
whose palette names a BCT block gets that palette entry rewritten, and the
changed sub-chunks are added to the newest log as one more write, the way the
game writes. Only palette entries change, so every block keeps its place:

- `<pack>:r_<block>` (a replacement block) becomes `minecraft:<block>`;
- `<pack>:m_<leaf>` (a converted leaf) becomes `minecraft:<leaf>`;
- any other block of a `bct_` namespace (overlay surfaces, edge blocks) becomes air.

The vanilla states come back from the `bct:` states by name (`bct:vertical_half`
gives `minecraft:vertical_half`), checked against Mojang's block list, with 0
and 1 turned back into false and true; states only BCT uses (pattern places,
random picks) are dropped, and a state the BCT block did not keep takes the
game's default. The world folder is zipped next to itself first. The game must
not have the world open.
"""
from collections import Counter
import json
from pathlib import Path
import re
import shutil
import struct
import zlib

SUBCHUNK_TAG = 47
BLOCK_SIZE = 32768
BCT = re.compile(r'^bct_[a-z0-9_]+:')


class WorldRestoreError(Exception):
    pass


# ---- LevelDB ---------------------------------------------------------------------------------------------

def _varint(data, i):
    result = shift = 0
    while True:
        byte = data[i]; i += 1
        result |= (byte & 0x7f) << shift; shift += 7
        if byte < 0x80:
            return result, i


def _put_varint(value):
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7f) | 0x80); value >>= 7
    out.append(value)
    return bytes(out)


def _crc32c(data, crc=0):
    crc ^= 0xffffffff
    for byte in data:
        crc = _CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >> 8)
    return crc ^ 0xffffffff


_CRC_TABLE = []
for _n in range(256):
    _c = _n
    for _ in range(8):
        _c = (_c >> 1) ^ 0x82f63b78 if _c & 1 else _c >> 1
    _CRC_TABLE.append(_c)


def _masked_crc(data):
    crc = _crc32c(data)
    return (((crc >> 15) | (crc << 17)) + 0xa282ead8) & 0xffffffff


def _log_records(data):
    """The whole records of a LevelDB log (or MANIFEST) file."""
    records, partial, i = [], b'', 0
    while i + 7 <= len(data):
        left = BLOCK_SIZE - i % BLOCK_SIZE
        if left < 7:
            i += left; continue
        _, length, kind = struct.unpack('<IHB', data[i:i + 7]); i += 7
        if kind == 0 and length == 0:
            i += (BLOCK_SIZE - i % BLOCK_SIZE) % BLOCK_SIZE; continue
        fragment = data[i:i + length]; i += length
        if kind == 1:
            records.append(fragment)
        elif kind == 2:
            partial = fragment
        elif kind == 3:
            partial += fragment
        elif kind == 4:
            records.append(partial + fragment); partial = b''
    return records


def _batch_entries(record):
    """(sequence, key, value or None for a delete) of one write batch."""
    sequence, count = struct.unpack('<QI', record[:12]); i = 12
    for n in range(count):
        kind = record[i]; i += 1
        length, i = _varint(record, i); key = record[i:i + length]; i += length
        if kind == 1:
            length, i = _varint(record, i); yield sequence + n, key, record[i:i + length]; i += length
        else:
            yield sequence + n, key, None


def _block(data, offset, size):
    raw, kind = data[offset:offset + size], data[offset + size]
    if kind == 0:
        return raw
    if kind == 2:
        return zlib.decompress(raw)
    if kind == 4:
        return zlib.decompress(raw, -15)
    raise WorldRestoreError(f'unknown table block compression {kind}')


def _block_entries(block):
    restarts = struct.unpack('<I', block[-4:])[0]
    end, i, key = len(block) - 4 - 4 * restarts, 0, b''
    while i < end:
        shared, i = _varint(block, i); unshared, i = _varint(block, i); length, i = _varint(block, i)
        key = key[:shared] + block[i:i + unshared]; i += unshared
        yield key, block[i:i + length]; i += length


def _table_entries(data):
    footer, i = data[-48:], 0
    _, i = _varint(footer, i); _, i = _varint(footer, i)
    index_offset, i = _varint(footer, i); index_size, i = _varint(footer, i)
    for _, handle in _block_entries(_block(data, index_offset, index_size)):
        offset, j = _varint(handle, 0); size, j = _varint(handle, j)
        for internal, value in _block_entries(_block(data, offset, size)):
            tag = struct.unpack('<Q', internal[-8:])[0]
            yield tag >> 8, internal[:-8], value if tag & 0xff == 1 else None


def _manifest_sequence(folder):
    """The last sequence number the MANIFEST recorded (0 without one)."""
    current = folder / 'CURRENT'
    if not current.exists():
        return 0
    manifest = folder / current.read_text().strip()
    last = 0
    for record in _log_records(manifest.read_bytes()):
        i = 0
        while i < len(record):
            tag, i = _varint(record, i)
            if tag in (1,):
                length, i = _varint(record, i); i += length
            elif tag in (2, 3, 9):
                _, i = _varint(record, i)
            elif tag == 4:
                value, i = _varint(record, i); last = max(last, value)
            elif tag == 5:
                _, i = _varint(record, i); length, i = _varint(record, i); i += length
            elif tag == 6:
                _, i = _varint(record, i); _, i = _varint(record, i)
            elif tag == 7:
                _, i = _varint(record, i); _, i = _varint(record, i); _, i = _varint(record, i)
                for _ in range(2):
                    length, i = _varint(record, i); i += length
            else:
                break
    return last


def read_database(folder):
    """({key: latest value}, last sequence number) of a LevelDB folder: its tables, then its logs on top."""
    folder = Path(folder)
    entries = []
    for path in sorted(folder.glob('*.ldb')):
        entries += list(_table_entries(path.read_bytes()))
    for path in sorted(folder.glob('*.log')):
        for record in _log_records(path.read_bytes()):
            entries += list(_batch_entries(record))
    latest, last = {}, _manifest_sequence(folder)
    for sequence, key, value in sorted(entries, key=lambda entry: entry[0]):
        latest[key] = value
        last = max(last, sequence)
    return {key: value for key, value in latest.items() if value is not None}, last


def append_batch(folder, puts, sequence):
    """Adds one write batch {key: value} to the newest log, as LevelDB writes it; the game replays it on load."""
    logs = sorted(Path(folder).glob('*.log'), key=lambda path: int(path.stem))
    if not logs:
        raise WorldRestoreError('the world has no LevelDB log to add to')
    batch = bytearray(struct.pack('<QI', sequence, len(puts)))
    for key, value in puts.items():
        batch += b'\x01' + _put_varint(len(key)) + key + _put_varint(len(value)) + value
    data, path = bytes(batch), logs[-1]
    out = bytearray()
    offset = path.stat().st_size
    first = True
    while True:
        left = BLOCK_SIZE - (offset + len(out)) % BLOCK_SIZE
        if left < 7:
            out += b'\x00' * left; continue
        room = left - 7
        fragment, data = data[:room], data[room:]
        kind = (1 if not data else 2) if first else (4 if not data else 3)
        out += struct.pack('<IHB', _masked_crc(bytes([kind]) + fragment), len(fragment), kind) + fragment
        first = False
        if not data:
            break
    with path.open('ab') as handle:
        handle.write(out)


# ---- little-endian NBT, only what block palettes use ----------------------------------------------------

def _read_tag(data, i, kind):
    if kind == 1:
        return data[i], i + 1
    if kind == 2:
        return struct.unpack('<h', data[i:i + 2])[0], i + 2
    if kind == 3:
        return struct.unpack('<i', data[i:i + 4])[0], i + 4
    if kind == 4:
        return struct.unpack('<q', data[i:i + 8])[0], i + 8
    if kind == 5:
        return struct.unpack('<f', data[i:i + 4])[0], i + 4
    if kind == 6:
        return struct.unpack('<d', data[i:i + 8])[0], i + 8
    if kind == 8:
        length = struct.unpack('<H', data[i:i + 2])[0]
        return data[i + 2:i + 2 + length].decode('utf-8'), i + 2 + length
    if kind == 10:
        result = {}
        while True:
            sub = data[i]; i += 1
            if sub == 0:
                return result, i
            length = struct.unpack('<H', data[i:i + 2])[0]
            name = data[i + 2:i + 2 + length].decode('utf-8'); i += 2 + length
            value, i = _read_tag(data, i, sub)
            result[name] = (sub, value)
    raise WorldRestoreError(f'palette NBT tag {kind} is not handled')


def _write_tag(kind, value):
    if kind == 1:
        return bytes([value & 0xff])
    if kind == 2:
        return struct.pack('<h', value)
    if kind == 3:
        return struct.pack('<i', value)
    if kind == 4:
        return struct.pack('<q', value)
    if kind == 5:
        return struct.pack('<f', value)
    if kind == 6:
        return struct.pack('<d', value)
    if kind == 8:
        raw = value.encode('utf-8')
        return struct.pack('<H', len(raw)) + raw
    if kind == 10:
        out = bytearray()
        for name, (sub, item) in value.items():
            raw = name.encode('utf-8')
            out += bytes([sub]) + struct.pack('<H', len(raw)) + raw + _write_tag(sub, item)
        return bytes(out + b'\x00')
    raise WorldRestoreError(f'palette NBT tag {kind} is not handled')


def read_palette_entry(data, i):
    """(entry {name: (tag, value)}, end) of one palette compound (a named root compound) at i."""
    if data[i] != 10:
        raise WorldRestoreError('a palette entry is not a compound')
    length = struct.unpack('<H', data[i + 1:i + 3])[0]
    return _read_tag(data, i + 3 + length, 10)


def write_palette_entry(entry):
    return b'\x0a\x00\x00' + _write_tag(10, entry)


# ---- sub-chunks ------------------------------------------------------------------------------------------

def rewrite_subchunk(value, mapping):
    """The sub-chunk with its palette entries passed through mapping(entry) -> entry or None (unchanged).

    Returns (new value or None when nothing changed, Counter of rewritten names).
    """
    version = value[0]
    if version not in (8, 9):
        return None, Counter()
    out, i = bytearray(value[:2 + (version == 9)]), 2 + (version == 9)
    changed = Counter()
    for _ in range(value[1]):
        header = value[i]; i += 1
        bits = header >> 1
        words = 0 if bits == 0 else -(-4096 // (32 // bits))
        out += bytes([header]) + value[i:i + 4 * words]; i += 4 * words
        size = struct.unpack('<i', value[i:i + 4])[0]; out += value[i:i + 4]; i += 4
        for _ in range(size):
            start = i
            entry, i = read_palette_entry(value, i)
            replaced = mapping(entry)
            if replaced is None:
                out += value[start:i]
            else:
                changed[entry['name'][1]] += 1
                out += write_palette_entry(replaced)
    out += value[i:]
    return (bytes(out) if changed else None), changed


def is_subchunk_key(key):
    return len(key) in (10, 14) and key[-2] == SUBCHUNK_TAG


# ---- BCT blocks back to vanilla ---------------------------------------------------------------------------

def vanilla_states(samples):
    """{block id: [(state name, [values])]} from Mojang's block list."""
    data = json.loads((Path(samples) / 'metadata/vanilladata_modules/mojang-blocks.json').read_text(encoding='utf-8'))
    values = {item['name']: [entry['value'] for entry in item['values']] for item in data['block_properties']}
    return {item['name']: [(prop['name'], values[prop['name']]) for prop in item.get('properties', [])]
            for item in data['data_items']}


def bct_mapping(known):
    """mapping(entry) for rewrite_subchunk: a BCT block's palette entry as its vanilla block (or air)."""
    def mapping(entry):
        name = entry.get('name', (8, ''))[1]
        if not BCT.match(name):
            return None
        stem = name.split(':', 1)[1]
        vanilla = None
        for prefix in ('r_', 'm_'):
            if stem.startswith(prefix) and 'minecraft:' + stem[len(prefix):] in known:
                vanilla = 'minecraft:' + stem[len(prefix):]
        version = entry.get('version', (3, 0))
        if vanilla is None:
            return {'name': (8, 'minecraft:air'), 'states': (10, {}), 'version': version}
        kept = entry.get('states', (10, {}))[1]
        states = {}
        for state, choices in known[vanilla]:
            short = 'bct:' + state.split(':')[-1]
            value = kept[short][1] if short in kept else choices[0]
            if all(isinstance(choice, bool) for choice in choices):
                states[state] = (1, 1 if value in (True, 1) else 0)
            elif all(isinstance(choice, int) for choice in choices):
                states[state] = (3, int(value))
            else:
                states[state] = (8, str(value) if str(value) in choices else choices[0])
        return {'name': (8, vanilla), 'states': (10, states), 'version': version}
    return mapping


def count_bct_blocks(latest):
    """{block name: count} of BCT blocks actually placed (palette entries some block uses)."""
    found = Counter()
    for key, value in latest.items():
        if not is_subchunk_key(key) or b'bct_' not in value:
            continue
        version = value[0]
        if version not in (8, 9):
            continue
        i = 2 + (version == 9)
        for _ in range(value[1]):
            header = value[i]; i += 1
            bits = header >> 1
            if bits == 0:
                indices = Counter({0: 4096})
            else:
                per = 32 // bits; words = -(-4096 // per)
                raw = struct.unpack('<%dI' % words, value[i:i + 4 * words]); i += 4 * words
                mask = (1 << bits) - 1
                indices = Counter([(word >> (k * bits)) & mask for word in raw for k in range(per)][:4096])
            size = struct.unpack('<i', value[i:i + 4])[0]; i += 4
            for index in range(size):
                entry, i = read_palette_entry(value, i)
                name = entry['name'][1]
                if BCT.match(name) and indices.get(index):
                    found[name] += indices[index]
    return found


def restore_world(world, samples, backup=True):
    """Puts every BCT block in a world back to vanilla. Returns a report dict."""
    world = Path(world)
    folder = world / 'db'
    if not folder.is_dir():
        raise WorldRestoreError(f'{world} is not a Bedrock world folder (no db folder)')
    lock = folder / 'LOCK'
    if lock.exists():
        try:
            lock.rename(folder / 'LOCK.bct')
            (folder / 'LOCK.bct').rename(lock)
        except OSError:
            raise WorldRestoreError('the world is open in the game; close it first') from None
    archive = None
    if backup:
        archive = shutil.make_archive(str(world) + '.bct-backup', 'zip', world)
    latest, last = read_database(folder)
    mapping = bct_mapping(vanilla_states(samples))
    puts, rewritten = {}, Counter()
    for key, value in latest.items():
        if is_subchunk_key(key) and b'bct_' in value:
            new, changed = rewrite_subchunk(value, mapping)
            if new is not None:
                puts[key] = new
                rewritten.update(changed)
    if puts:
        append_batch(folder, puts, last + 1)
    left = count_bct_blocks(read_database(folder)[0])
    return {'backup': archive, 'subchunks': len(puts), 'rewritten': dict(rewritten), 'left': dict(left)}
