"""The engine add-on: a fixed identity, one behavior pack, and every module the engine loads."""
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'converter'))
from engine_package import ENGINE, build_engine, engine_dependency, engine_manifest, engine_modules

# The file named by a relative import in a script: from './x.js', import './x.js', import('./x.js').
IMPORTED_FILE = re.compile(r"""(?:\bfrom|\bimport)\s*\(?\s*['"]\./([^'"]+)['"]""")


def shipped_scripts():
    """{script name: text} for every script in a freshly built engine add-on."""
    with tempfile.TemporaryDirectory() as folder:
        with zipfile.ZipFile(build_engine(folder)) as packed:
            return {name.removeprefix('BCT_BP/scripts/'): packed.read(name).decode()
                    for name in packed.namelist() if name.startswith('BCT_BP/scripts/')}


class EnginePackageTests(unittest.TestCase):
    def test_identity_is_fixed(self):
        # Converted packs depend on these values; they must never change.
        self.assertEqual(ENGINE['header_uuid'], '2286eae6-39b3-5e23-9a2d-919e4da1bcaa')
        self.assertEqual(ENGINE['data_module_uuid'], 'd767177c-c5a5-5879-8165-7a0e31804fdd')
        self.assertEqual(ENGINE['script_module_uuid'], '7405b7ea-d03d-5dbf-ae5f-15be7fd05953')
        self.assertEqual(engine_dependency(), {'uuid': ENGINE['header_uuid'], 'version': ENGINE['version']})
        manifest = engine_manifest()
        self.assertEqual(manifest['header']['uuid'], ENGINE['header_uuid'])
        self.assertIn({'module_name': '@minecraft/server', 'version': '2.10.0'}, manifest['dependencies'])

    def test_every_import_in_the_shipped_scripts_is_shipped(self):
        scripts = shipped_scripts()
        self.assertIn('main.js', scripts)
        self.assertNotIn('publisher.js', scripts, 'the publisher runs in converted packs')
        for name, text in scripts.items():
            self.assertNotIn('.mjs', text, name)
            for imported in IMPORTED_FILE.findall(text):
                self.assertIn(imported, scripts, f'{name} imports ./{imported}, which the add-on does not ship')

    def test_every_shipped_script_is_loaded_by_the_engine(self):
        scripts = shipped_scripts()
        reached, pending = set(), ['main.js']
        while pending:
            name = pending.pop()
            if name not in reached:
                reached.add(name)
                pending.extend(IMPORTED_FILE.findall(scripts[name]))
        self.assertEqual(reached, set(scripts))

    def test_only_the_entry_point_takes_the_script_api(self):
        for name in engine_modules():
            if name != 'main.mjs':
                text = (ROOT / 'engine' / name).read_text(encoding='utf-8')
                self.assertIsNone(re.search(r"from '@minecraft/", text), name + ' takes the API from main.mjs')

    def test_new_modules_are_found_through_their_imports(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = Path(folder)
            (engine / 'main.mjs').write_text("import { startEngine } from './engine.mjs';\nimport('./late.mjs');\n")
            (engine / 'engine.mjs').write_text("import {\n  showsAt,\n} from \"./views.mjs\";\n"
                                               "export * from './core.mjs';\nimport './side-effect.mjs';\n")
            for name in ('views.mjs', 'core.mjs', 'late.mjs', 'side-effect.mjs', 'publisher.mjs'):
                (engine / name).write_text('export const value = 1;\n')
            self.assertEqual(engine_modules(engine),
                             ['main.mjs', 'core.mjs', 'engine.mjs', 'late.mjs', 'side-effect.mjs', 'views.mjs'])
            (engine / 'views.mjs').unlink()
            with self.assertRaisesRegex(ValueError, r'engine\.mjs imports \./views\.mjs'):
                engine_modules(engine)

    def test_engine_addon_is_one_behavior_pack(self):
        with tempfile.TemporaryDirectory() as folder:
            with zipfile.ZipFile(build_engine(folder)) as packed:
                names = packed.namelist()
                self.assertEqual({name.split('/')[0] for name in names}, {'BCT_BP'})
                scripts = sorted(name for name in names if '/scripts/' in name)
                expected = sorted('BCT_BP/scripts/' + name.replace('.mjs', '.js') for name in engine_modules())
                self.assertEqual(scripts, expected)
                self.assertEqual(json.loads(packed.read('BCT_BP/manifest.json')), engine_manifest())


if __name__ == '__main__':
    unittest.main()
