"""Check author-pack audits report unsupported features without mutation."""
import sys
from pathlib import Path
import tempfile
import unittest
import zipfile
import io
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from audit_java_ctm import audit


class AuditTests(unittest.TestCase):
    def test_valid_animation_is_not_reported_as_missing_support(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'java.zip'
            pixels = io.BytesIO()
            Image.new('RGBA', (8, 16), (50, 60, 70, 255)).save(pixels, format='PNG')
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('assets/minecraft/optifine/ctm/a.properties',
                                 'method=fixed\nmatchBlocks=stone\ntiles=0')
                archive.writestr('assets/minecraft/optifine/ctm/0.png', pixels.getvalue())
                archive.writestr('assets/minecraft/optifine/ctm/0.png.mcmeta',
                                 '{"animation":{"frames":[1,0],"frametime":3}}')
            self.assertEqual(audit(path)['rules_with_known_gaps'], 0)
            with zipfile.ZipFile(path, 'a') as archive:
                archive.writestr('assets/minecraft/optifine/ctm/b.properties',
                                 'method=repeat\nmatchBlocks=stone\nwidth=1\nheight=1\ntiles=0\norient=texture')
            self.assertEqual(audit(path)['rules_with_known_gaps'], 1)

    def test_reports_method_and_geometry_gaps_and_preserves_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'java.zip'
            pixels = io.BytesIO()
            Image.new('RGBA', (8, 8), (50, 60, 70, 255)).save(pixels, format='PNG')
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('assets/minecraft/optifine/ctm/a/a.properties',
                                 'method=repeat\nmatchBlocks=stone\nwidth=2\nheight=2\ntiles=0-3')
                archive.writestr('assets/minecraft/optifine/ctm/b/b.properties',
                                 'method=ctm_compact\nmatchBlocks=glass_pane\ntiles=0-4')
                for name, count in [('a', 4), ('b', 5)]:
                    for index in range(count):
                        archive.writestr(f'assets/minecraft/optifine/ctm/{name}/{index}.png', pixels.getvalue())
            original = path.read_bytes()
            result = audit(path)
            self.assertEqual(result['ctm_rules'], 2)
            self.assertEqual(result['rules_with_known_gaps'], 1)
            self.assertEqual(len(result['rules'][1]['known_gaps']), 1)
            self.assertFalse(result['conversion_complete'])
            self.assertFalse(result['artwork_modified'])
            self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
