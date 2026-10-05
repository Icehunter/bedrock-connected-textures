"""Java pack stacks: layering, path checks, CTM dependencies and verified conversion receipts."""
import sys
import tempfile
from pathlib import Path
import unittest
import zipfile
import hashlib
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from java_pack_api import PackStack, ConversionAPI


class PackAPITests(unittest.TestCase):
    def test_scoped_ctm_follows_external_tiles_and_material_animation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'source.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                files = {'pack.mcmeta': '{}', 'assets/minecraft/optifine/ctm/example/a.properties':
                         'method=fixed\nmatchBlocks=stone\ntiles=textures/shared/stone',
                         'assets/minecraft/textures/shared/stone.png': 'color',
                         'assets/minecraft/textures/shared/stone_n.png': 'normal',
                         'assets/minecraft/textures/shared/stone_n.png.mcmeta': '{"animation":{}}',
                         'assets/minecraft/textures/entity/cow.png': 'unrelated'}
                for name, data in files.items():
                    archive.writestr(name, data)
            with PackStack([path]) as stack:
                report = stack.inspect('block_ctm')
                self.assertEqual(report['effective_files'], 4)
                self.assertEqual(report['unresolved_texture_references'], [])
                self.assertEqual(report['asset_counts']['normal'], 1)
                self.assertEqual(report['asset_counts']['animation'], 1)

    def test_scoped_ctm_reports_inherited_or_missing_texture_before_conversion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'source.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('pack.mcmeta', '{}')
                archive.writestr('assets/minecraft/optifine/ctm/a.properties',
                                 'method=fixed\nmatchTiles=stone\ntiles=0')
            with PackStack([path]) as stack:
                report = stack.inspect('block_ctm')
                self.assertEqual({entry['texture'] for entry in report['unresolved_texture_references']},
                                 {'assets/minecraft/optifine/ctm/0.png'})
                self.assertEqual({entry['texture'] for entry in report['external_texture_selectors']},
                                 {'assets/minecraft/textures/block/stone.png'})
                with self.assertRaisesRegex(ValueError, 'Unresolved CTM'):
                    ConversionAPI().convert(stack, root / 'output', 'block_ctm')
                self.assertFalse((root / 'output').exists())

    def test_external_selectors_do_not_require_replacement_pixels(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'source.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('pack.mcmeta', '{}')
                archive.writestr('assets/minecraft/optifine/ctm/a.properties',
                                 'method=overlay\nmatchTiles=stone\nconnectTiles=grass_block_side\ntiles=0')
                archive.writestr('assets/minecraft/optifine/ctm/0.png', b'pixels')
            with PackStack([path]) as stack:
                report = stack.inspect('block_ctm')
                self.assertEqual(report['unresolved_texture_references'], [])
                self.assertEqual({entry['property'] for entry in report['external_texture_selectors']},
                                 {'matchTiles', 'connectTiles'})
                self.assertTrue(all(entry['status'] == 'requires_texture_binding'
                                    for entry in report['external_texture_selectors']))
                with self.assertRaisesRegex(ValueError, 'Missing translators'):
                    ConversionAPI().convert(stack, root / 'output', 'block_ctm')

    def test_block_texture_scope_excludes_model_and_mob_conversion(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'source.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                files = [('pack.mcmeta', '{}'),
                         ('assets/minecraft/models/block/stone.json', '{}'),
                         ('assets/minecraft/optifine/cem/cow.jem', '{}'),
                         ('assets/minecraft/textures/block/stone.png', 'sprite'),
                         ('assets/minecraft/textures/block/stone.png.mcmeta', '{"animation":{}}'),
                         ('assets/minecraft/textures/entity/cow.png', 'mob'),
                         ('assets/minecraft/optifine/ctm/stone/a.properties',
                          'method=fixed\nmatchBlocks=stone\ntiles=0')]
                for name, data in files:
                    archive.writestr(name, data)
            with PackStack([path]) as stack:
                report = ConversionAPI().plan(stack, scope='block_ctm')
                self.assertEqual(report['scope'], 'block_ctm')
                self.assertEqual(report['effective_files'], 3)
                self.assertEqual(report['outside_scope_files'], 4)
                self.assertEqual(set(report['asset_counts']), {'sprite', 'animation', 'ctm_rule'})

    def test_stack_priority_provenance_and_normal_classification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archives = []
            for index in range(2):
                path = root / (str(index) + '.zip')
                archives.append(path)
                with zipfile.ZipFile(path, 'w') as archive:
                    archive.writestr('pack.mcmeta', '{}')
                    archive.writestr('assets/minecraft/textures/block/stone.png', bytes([index]))
                    if index == 1:
                        archive.writestr('assets/minecraft/textures/block/stone_n.png', b'normal')
            with PackStack(archives) as stack:
                self.assertEqual(stack.read('assets/minecraft/textures/block/stone.png'), b'\x01')
                report = stack.inspect()
                self.assertEqual(report['effective_files'], 3)
                self.assertEqual(report['overridden_files'], 2)
                self.assertEqual(report['asset_counts']['normal'], 1)
                self.assertFalse(report['full_support_verified'])
                with self.assertRaisesRegex(ValueError, 'Missing translators'):
                    ConversionAPI().convert(stack, root / 'output')
                self.assertFalse((root / 'output').exists())

    def test_rejects_archive_paths_that_escape_the_pack(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'unsafe.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('pack.mcmeta', '{}')
                archive.writestr('../escaped.png', b'x')
            with self.assertRaisesRegex(ValueError, 'Unsafe archive path'):
                PackStack([path])

    def test_failed_translator_cannot_publish_partial_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / 'source.zip'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                archive.writestr('pack.mcmeta', '{}')
            api = ConversionAPI()

            def failing(stack, asset, destination):
                (destination / 'partial.txt').write_text('partial')
                raise ValueError('translator failed')
            api.register('metadata', failing)
            with PackStack([archive_path]) as stack:
                with self.assertRaisesRegex(ValueError, 'translator failed'):
                    api.convert(stack, root / 'output')
            self.assertFalse((root / 'output').exists())
            self.assertFalse(list(root.glob('.java-conversion-*')))

    def test_success_requires_verified_receipts_before_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / 'source.zip'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                archive.writestr('pack.mcmeta', '{}')
            api = ConversionAPI()

            def translator(stack, asset, destination):
                data = stack.read(asset['path'])
                (destination / 'metadata.json').write_bytes(data)
                output = {'path': 'metadata.json', 'sha256': hashlib.sha256(data).hexdigest()}
                return {'source': asset['path'], 'status': 'converted', 'outputs': [output]}
            api.register('metadata', translator)
            with PackStack([archive_path]) as stack:
                receipt = api.convert(stack, root / 'output')
            self.assertEqual(receipt['converted_sources'], 1)
            self.assertEqual((root / 'output/metadata.json').read_bytes(), b'{}')
            self.assertFalse(receipt['full_support_verified'])


if __name__ == '__main__':
    unittest.main()
