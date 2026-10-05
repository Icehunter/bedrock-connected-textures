"""Pack members are copied into a new archive byte for byte, under their new names."""
import io
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from zip_members import copy_compressed, member_name


def compressed(path, name):
    with zipfile.ZipFile(path) as archive:
        member = archive.getinfo(name)
        with path.open('rb') as stream:
            stream.seek(member.header_offset)
            header = stream.read(30)
            name_size, extra_size = struct.unpack('<HH', header[26:30])
            stream.seek(name_size + extra_size, 1)
            return stream.read(member.compress_size)


def copied(source, name, target_name, root):
    output = root / 'out.zip'
    with zipfile.ZipFile(source) as reader, zipfile.ZipFile(output, 'w') as writer:
        copy_compressed(reader, reader.getinfo(name), writer, target_name)
    return output


class ZipMemberTests(unittest.TestCase):
    art = bytes(range(256)) * 512

    def test_unicode_zip64_member_is_renamed_without_recompressing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            packed = root / 'base.mcpack'
            with zipfile.ZipFile(packed, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                with archive.open('textures/caf\u00e9.png', 'w', force_zip64=True) as stream:
                    stream.write(self.art)
            original = packed.read_bytes()
            output = copied(packed, 'textures/caf\u00e9.png', 'Source_RP/textures/caf\u00e9.png', root)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.read('Source_RP/textures/caf\u00e9.png'), self.art)
                self.assertIsNone(archive.testzip())
            self.assertEqual(compressed(output, 'Source_RP/textures/caf\u00e9.png'),
                             compressed(packed, 'textures/caf\u00e9.png'))
            self.assertEqual(packed.read_bytes(), original)

    def test_streamed_data_descriptor_is_rewritten_with_the_same_payload(self):
        class Unseekable(io.BytesIO):
            def seek(self, *args):
                raise io.UnsupportedOperation('stream')

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stream = Unseekable()
            with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('albedo.png', self.art)
            packed = root / 'streamed.mcpack'
            packed.write_bytes(stream.getvalue())
            output = copied(packed, 'albedo.png', 'Source_RP/albedo.png', root)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.read('Source_RP/albedo.png'), self.art)
                self.assertIsNone(archive.testzip())
            self.assertEqual(compressed(output, 'Source_RP/albedo.png'), compressed(packed, 'albedo.png'))

    def test_unsafe_member_names_are_rejected(self):
        for name in ('', '/root.png', '../escape.png', 'a\\b.png', 'C:/drive.png'):
            with self.assertRaises(ValueError):
                member_name(name)
        self.assertEqual(member_name('textures/a.png'), 'textures/a.png')


if __name__ == '__main__':
    unittest.main()
