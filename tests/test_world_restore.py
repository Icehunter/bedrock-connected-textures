import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))

from common import samples_path  # noqa: E402
import world_restore  # noqa: E402
from world_restore import (append_batch, count_bct_blocks, read_database, read_palette_entry, restore_world,  # noqa: E402
                           write_palette_entry)

SAMPLES = Path(samples_path())
KEY = struct.pack('<ii', 2, -3) + bytes([47, 4])


def entry(name, states):
    return {'name': (8, name), 'states': (10, states), 'version': (3, 18100737)}


def subchunk(palette, indices):
    """A version 9 sub-chunk with one storage of 1 bit per block."""
    words = [0] * 128
    for position, index in enumerate(indices):
        words[position // 32] |= index << (position % 32)
    out = bytearray([9, 1, 4, 1 << 1]) + struct.pack('<128I', *words) + struct.pack('<i', len(palette))
    for item in palette:
        out += write_palette_entry(item)
    return bytes(out)


def palette_of(value):
    i, names = 4 + 4 * 128, []
    size = struct.unpack('<i', value[i:i + 4])[0]; i += 4
    for _ in range(size):
        item, i = read_palette_entry(value, i)
        names.append(item)
    return names


class LogTest(unittest.TestCase):
    def test_crc32c(self):
        self.assertEqual(world_restore._crc32c(b'123456789'), 0xe3069283)

    def test_a_batch_added_to_the_log_reads_back_across_blocks(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / '000005.log').write_bytes(b'')
            append_batch(folder, {b'small': b'1'}, 7)
            big = bytes(range(256)) * 300
            append_batch(folder, {b'big': big, b'small': b'2'}, 8)
            latest, last = read_database(folder)
        self.assertEqual(latest, {b'small': b'2', b'big': big})
        self.assertEqual(last, 9)


@unittest.skipUnless((SAMPLES / 'metadata/vanilladata_modules/mojang-blocks.json').is_file(), 'needs bedrock-samples')
class RestoreTest(unittest.TestCase):
    def test_bct_blocks_go_back_to_vanilla_in_place(self):
        stairs = entry('bct_pack:r_oak_stairs', {'bct:weirdo_direction': (3, 2), 'bct:upside_down_bit': (1, 1),
                                                 'bct:t': (3, 5)})
        palette = [entry('minecraft:air', {}), stairs]
        leaves = [entry('bct_pack:m_oak_leaves', {'bct:persistent_bit': (1, 1), 'bct:look': (3, 2)}),
                  entry('bct_pack:overlay_grass_top', {'bct:o': (3, 1)})]
        indices = [position % 2 for position in range(4096)]
        with tempfile.TemporaryDirectory() as world:
            db = Path(world) / 'db'
            db.mkdir()
            (db / '000003.log').write_bytes(b'')
            append_batch(db, {KEY: subchunk(palette, indices), KEY[:-1] + b'\x05': subchunk(leaves, indices)}, 1)
            report = restore_world(world, SAMPLES, backup=False)
            latest, _ = read_database(db)
        self.assertEqual(report['subchunks'], 2)
        self.assertEqual(report['left'], {})
        self.assertEqual(count_bct_blocks(latest), {})
        restored = palette_of(latest[KEY])
        self.assertEqual(restored[0]['name'][1], 'minecraft:air')
        self.assertEqual(restored[1]['name'][1], 'minecraft:oak_stairs')
        self.assertEqual(restored[1]['states'][1], {'minecraft:corner': (8, 'none'), 'upside_down_bit': (1, 1),
                                                    'weirdo_direction': (3, 2)})
        self.assertEqual(latest[KEY][4:4 + 4 * 128], subchunk(palette, indices)[4:4 + 4 * 128])
        other = palette_of(latest[KEY[:-1] + b'\x05'])
        self.assertEqual(other[0]['name'][1], 'minecraft:oak_leaves')
        self.assertEqual(other[0]['states'][1]['persistent_bit'], (1, 1))
        self.assertEqual(other[1]['name'][1], 'minecraft:air')

    def test_count_sees_only_blocks_in_use(self):
        palette = [entry('minecraft:stone', {}), entry('bct_pack:r_stone', {})]
        latest = {KEY: subchunk(palette, [0] * 4096)}
        self.assertEqual(count_bct_blocks(latest), {})
        latest = {KEY: subchunk(palette, [1] + [0] * 4095)}
        self.assertEqual(count_bct_blocks(latest), {'bct_pack:r_stone': 1})


if __name__ == '__main__':
    unittest.main()
