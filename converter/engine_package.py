"""Build the Bedrock Connected Textures engine add-on and describe its fixed identity.

The engine is published once. Converted packs never contain a copy; their behavior packs
depend on the header UUID in engine/identity.json, which never changes between builds.
The add-on ships main.mjs and every engine module it reaches through relative imports, so
a new module is packaged as soon as the engine uses it.
"""
import argparse
import json
from pathlib import Path
import re
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ENGINE_FOLDER = ROOT / 'engine'
ENGINE = json.loads((ENGINE_FOLDER / 'identity.json').read_text(encoding='utf-8'))
ENTRY_MODULE = 'main.mjs'
# The file of a relative import: from './x.mjs', import './x.mjs' or import('./x.mjs').
RELATIVE_IMPORT = re.compile(r"""(?:\bfrom|\bimport)\s*\(?\s*['"]\./([^'"]+)['"]""")


def engine_dependency():
    """Manifest dependency entry that links a converted pack to the engine."""
    return {'uuid': ENGINE['header_uuid'], 'version': list(ENGINE['version'])}


def engine_manifest():
    version = list(ENGINE['version'])
    return {'format_version': 2,
            'header': {'name': ENGINE['name'],
                       'description': 'Connected textures and terrain transitions for converted texture packs.',
                       'uuid': ENGINE['header_uuid'], 'version': version,
                       'min_engine_version': list(ENGINE['min_engine_version'])},
            'modules': [{'type': 'data', 'uuid': ENGINE['data_module_uuid'], 'version': version},
                        {'type': 'script', 'language': 'javascript', 'entry': 'scripts/main.js',
                         'uuid': ENGINE['script_module_uuid'], 'version': version}],
            'dependencies': [{'module_name': '@minecraft/server', 'version': ENGINE['server_version']},
                             {'module_name': '@minecraft/server-ui', 'version': ENGINE['server_ui_version']}]}


def engine_modules(folder=ENGINE_FOLDER):
    """The engine modules to ship: main.mjs first, then every module it reaches, by name.

    Modules the engine never imports stay out; publisher.mjs, for one, runs in converted
    packs. A module that imports a missing file raises ValueError, because the engine
    would fail to load in the game.
    """
    folder = Path(folder)
    reached = []
    pending = [(ENTRY_MODULE, None)]
    while pending:
        name, importer = pending.pop()
        if name in reached:
            continue
        path = folder / name
        if not path.is_file():
            if importer is None:
                raise ValueError(f'The engine entry point {name} is missing from {folder}.')
            raise ValueError(f'Engine module {importer} imports ./{name}, which does not exist. '
                             f'Add it to {folder} or fix the import.')
        reached.append(name)
        imported = RELATIVE_IMPORT.findall(path.read_text(encoding='utf-8'))
        pending.extend((module, name) for module in imported)
    return [ENTRY_MODULE, *sorted(reached[1:])]


def engine_script(name):
    """An engine module as shipped: .mjs imports become .js."""
    return (ENGINE_FOLDER / name).read_text(encoding='utf-8').replace('.mjs', '.js')


def build_engine(output):
    """Write Bedrock-Connected-Textures-<version>.mcaddon into the output folder and return its path."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / ('Bedrock-Connected-Textures-' + '.'.join(map(str, ENGINE['version'])) + '.mcaddon')
    modules = engine_modules()
    with tempfile.NamedTemporaryFile(prefix='.engine-', suffix='.mcaddon', dir=output, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as destination:
            destination.writestr('BCT_BP/manifest.json', json.dumps(engine_manifest(), indent=2) + '\n')
            destination.write(ROOT / 'converter/data/artwork/pack-icon.png', 'BCT_BP/pack_icon.png')
            for name in modules:
                destination.writestr('BCT_BP/scripts/' + name.replace('.mjs', '.js'), engine_script(name))
            destination.write(ROOT / 'LICENSE', 'BCT_BP/LICENSE.txt')
        with zipfile.ZipFile(temporary) as checked:
            if checked.testzip():
                raise ValueError('Engine archive is corrupt')
        temporary.replace(archive)
    finally:
        temporary.unlink(missing_ok=True)
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    print(build_engine(parser.parse_args().output))
