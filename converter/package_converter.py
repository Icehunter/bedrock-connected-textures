"""Package the converter as one ZIP for release.

The ZIP holds the Python and Node scripts, their data, the engine modules the converter
reads, the docs and licences, and the drag-and-drop launchers.
"""
from pathlib import Path
import uuid
import zipfile

from engine_package import ENGINE

ROOT = Path(__file__).resolve().parents[1]
FOLDER = 'Bedrock-Connected-Textures-Converter'
# (file in the project, name inside the ZIP's folder)
EXTRA_FILES = (
    ('docs/CONVERTER.md', 'README.md'),
    ('LICENSE', 'LICENSE'),
    ('LICENSE-EXCEPTION.md', 'LICENSE-EXCEPTION.md'),
    ('NOTICE', 'NOTICE'),
    ('requirements.txt', 'requirements.txt'),
    ('converter/convert_java_pack.cmd', 'Convert-Java-Pack.cmd'),
    ('converter/convert_java_pack.ps1', 'Convert-Java-Pack.ps1'),
)


def package(root=ROOT, output=None):
    """Write the converter ZIP and return its path (dist/<FOLDER>-<version>.zip by default)."""
    root = Path(root)
    files = [path for path in sorted((root / 'converter').rglob('*')) + sorted((root / 'engine').glob('*'))
             if path.is_file() and '__pycache__' not in path.parts]
    version = '.'.join(map(str, ENGINE['version']))
    destination = Path(output) if output else root / 'dist' / f'{FOLDER}-{version}.zip'
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Built under a random name and moved into place once checked, so a failed run never
    # leaves a broken ZIP under the real name.
    temporary = destination.with_name('.' + uuid.uuid4().hex + '.zip')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, Path(FOLDER) / path.relative_to(root))
        for source, name in EXTRA_FILES:
            archive.write(root / source, f'{FOLDER}/{name}')
    with zipfile.ZipFile(temporary) as archive:
        damaged = archive.testzip()
        if damaged:
            raise ValueError('Invalid converter archive member: ' + damaged)
    temporary.replace(destination)
    return destination


if __name__ == '__main__':
    print(package())
