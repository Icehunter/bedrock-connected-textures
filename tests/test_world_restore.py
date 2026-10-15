import struct
import subprocess
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


def subchunk_n(palette, indices):
    """A version 9 sub-chunk with one storage of 3 bits per block."""
    words, per = [0] * 410, 10
    for position, index in enumerate(indices):
        words[position // per] |= index << (position % per * 3)
    out = bytearray([9, 1, 4, 3 << 1]) + struct.pack('<410I', *words) + struct.pack('<i', len(palette))
    for item in palette:
        out += write_palette_entry(item)
    return bytes(out)


def palette_of_n(value):
    i, names = 4 + 4 * 410, []
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

    def test_log_files_without_a_number_in_their_name_are_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / '000005.log').write_bytes(b'')
            (Path(folder) / 'notes.log').write_bytes(b'not a leveldb log')
            (Path(folder) / '\u00b2.log').write_bytes(b'not a leveldb log')
            append_batch(folder, {b'small': b'1'}, 7)
            latest, last = read_database(folder)
            self.assertEqual((Path(folder) / 'notes.log').read_bytes(), b'not a leveldb log')
        self.assertEqual(latest, {b'small': b'1'})
        self.assertEqual(last, 7)


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

    @unittest.skipIf(sys.platform == 'win32', 'uses fcntl file locks')
    def test_a_world_the_game_holds_open_is_refused_even_though_its_lock_can_be_renamed(self):
        stairs = entry('bct_pack:r_oak_stairs', {'bct:t': (3, 5)})
        palette = [entry('minecraft:air', {}), stairs]
        indices = [position % 2 for position in range(4096)]
        code = (
            "import fcntl, sys\n"
            "handle = open(sys.argv[1], 'r+b')\n"
            "fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "print('locked', flush=True)\n"
            "sys.stdin.read()\n"
        )
        with tempfile.TemporaryDirectory() as world:
            db = Path(world) / 'db'
            db.mkdir()
            (db / '000003.log').write_bytes(b'')
            append_batch(db, {KEY: subchunk(palette, indices)}, 1)
            lock_path = str(db / 'LOCK')
            with open(lock_path, 'wb'):
                pass
            proc = subprocess.Popen([sys.executable, '-c', code, lock_path],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(proc.stdout.readline().rstrip('\n'), 'locked')
                with self.assertRaises(world_restore.WorldRestoreError) as context:
                    restore_world(world, SAMPLES, backup=False)
            finally:
                proc.stdin.close()
                proc.wait()
        self.assertIn('open in the game', str(context.exception))

    def test_an_authored_packs_swapped_blocks_go_back_but_its_other_blocks_stay(self):
        palette = [entry('minecraft:stone', {}),
                   entry('ashen_vow:sandstone', {'bct:n': (3, 1), 'bct:u': (3, 0)}),
                   entry('ashen_vow:oak_planks', {'bct:e': (3, 1)}),
                   entry('ashen_vow:iron_brazier', {'lit': (1, 1)}),
                   entry('ashen_vow:made_up_block', {'bct:n': (3, 1)})]
        indices = [position % 5 for position in range(4096)]
        with tempfile.TemporaryDirectory() as world:
            db = Path(world) / 'db'
            db.mkdir()
            (db / '000003.log').write_bytes(b'')
            append_batch(db, {KEY: subchunk_n(palette, indices)}, 1)
            report = restore_world(world, SAMPLES, backup=False)
            latest, _ = read_database(db)
        names = [item['name'][1] for item in palette_of_n(latest[KEY])]
        self.assertEqual(names, ['minecraft:stone', 'minecraft:sandstone', 'minecraft:oak_planks',
                                 'ashen_vow:iron_brazier', 'ashen_vow:made_up_block'])
        self.assertEqual(report['left'], {})

    def test_count_sees_only_blocks_in_use(self):
        palette = [entry('minecraft:stone', {}), entry('bct_pack:r_stone', {})]
        latest = {KEY: subchunk(palette, [0] * 4096)}
        self.assertEqual(count_bct_blocks(latest), {})
        latest = {KEY: subchunk(palette, [1] + [0] * 4095)}
        self.assertEqual(count_bct_blocks(latest), {'bct_pack:r_stone': 1})


if __name__ == '__main__':
    unittest.main()
