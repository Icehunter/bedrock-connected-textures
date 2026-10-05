"""Read a stack of Java resource packs as one pack, layered the way the game layers them.

Archives come lowest priority first: a later pack's file replaces the same path in an
earlier one, and every replacement is recorded. Member paths are checked so nothing
points outside its pack. ConversionAPI runs a translator for every file in scope and
checks each translator's receipt against the files on disk.
"""
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
import zipfile

from common import sha256_file
from import_java_ctm import parse_properties
from java_texture_paths import SPECIAL_TILES, resolve_texture

API_VERSION = 1
# Path parts of the files that block textures and CTM rules depend on.
BLOCK_ASSET_MARKERS = ('/textures/block/', '/textures/blocks/', '/optifine/ctm/', '/textures/colormap/',
                       '/optifine/colormap/', '/optifine/colors.properties')
TEXTURE_PROPERTIES = ('tiles', 'matchTiles', 'connectTiles')


@dataclass(frozen=True)
class Source:
    archive: Path
    member: str


class PackStack:
    """Java resource pack archives, ordered from lowest to highest priority, read as one pack."""

    def __init__(self, archives):
        self.archives = [Path(path).resolve() for path in archives]
        if not self.archives:
            raise ValueError('Pack stack requires at least one archive')
        if len(set(self.archives)) != len(self.archives):
            raise ValueError('Pack stack contains a duplicate archive')
        self.files = {}
        self.overrides = []
        self._archives = {}
        try:
            for path in self.archives:
                self._add_archive(path)
        except BaseException:
            self.close()
            raise

    def close(self):
        for archive in self._archives.values():
            archive.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def read(self, path):
        source = self.files[path]
        return self._archives[source.archive].read(source.member)

    def block_dependencies(self):
        """(paths, missing): the files block textures and CTM rules need, and tiles that point at nothing.

        Each tile brings its _n and _s maps and their .mcmeta animation files along.
        """
        selected = {path for path in self.files if any(marker in path for marker in BLOCK_ASSET_MARKERS)}
        missing = []
        for path in sorted(selected):
            if '/optifine/ctm/' not in path or not path.endswith('.properties'):
                continue
            properties = parse_properties(self.read(path).decode('utf-8-sig'), path)
            for key, tokens in _texture_references(path, properties).items():
                for name in _expanded_tokens(tokens):
                    target = resolve_texture(name, path, matching=key != 'tiles')
                    if target not in self.files:
                        status = 'requires_resource_resolution' if key == 'tiles' else 'requires_texture_binding'
                        missing.append({'rule': path, 'property': key, 'texture': target, 'status': status})
                        continue
                    selected.add(target)
                    for image in (target, target[:-4] + '_n.png', target[:-4] + '_s.png'):
                        if image in self.files:
                            selected.add(image)
                        if image + '.mcmeta' in self.files:
                            selected.add(image + '.mcmeta')
        return selected, missing

    def inspect(self, scope='all'):
        """Every file in scope with its kind, the CTM rules, and the stack's overrides.

        scope 'all' covers every file; 'block_ctm' only what block textures and CTM rules need.
        """
        if scope not in ('all', 'block_ctm'):
            raise ValueError('Unknown conversion scope')
        selected, missing = self.block_dependencies() if scope == 'block_ctm' else (set(self.files), [])
        assets = []
        rules = []
        for path, source in sorted(self.files.items()):
            if path not in selected:
                continue
            kind = _asset_kind(path, self.files)
            if kind == 'ctm_rule':
                rules.append({'path': path, 'properties': parse_properties(self.read(path).decode('utf-8-sig'), path)})
            assets.append({'path': path, 'kind': kind, 'source_archive': str(source.archive),
                           'source_member': source.member, 'conversion_status': 'pending'})
        return {'api_version': API_VERSION, 'scope': scope, 'stack_effective_files': len(self.files),
                'outside_scope_files': len(self.files) - len(assets),
                'archives': [str(path) for path in self.archives],
                'archive_sha256': {str(path): sha256_file(path) for path in self.archives},
                'effective_files': len(assets), 'overridden_files': len(self.overrides), 'overrides': self.overrides,
                'asset_counts': dict(Counter(asset['kind'] for asset in assets)),
                'ctm_methods': dict(Counter(rule['properties'].get('method', 'ctm') for rule in rules)),
                'assets': assets, 'rules': rules,
                'unresolved_texture_references': [entry for entry in missing
                                                  if entry['status'] == 'requires_resource_resolution'],
                'external_texture_selectors': [entry for entry in missing
                                               if entry['status'] == 'requires_texture_binding'],
                'full_support_verified': False}

    def _add_archive(self, path):
        """Open one archive and layer its files over the ones already in the stack."""
        archive = zipfile.ZipFile(path)
        self._archives[path] = archive
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate archive members: ' + str(path))
        roots = [name[:-len('pack.mcmeta')] for name in names if PurePosixPath(name).name == 'pack.mcmeta']
        if len(roots) != 1:
            raise ValueError('Expected one Java resource pack root: ' + str(path))
        root = roots[0]
        for name in names:
            if name.endswith('/'):
                continue
            if '\\' in name or name.startswith('/') or '..' in PurePosixPath(name).parts:
                raise ValueError('Unsafe archive path: ' + name)
            if not name.startswith(root):
                raise ValueError('File outside resource pack root: ' + name)
            relative = name[len(root):]
            if ':' in relative:
                raise ValueError('Unsafe archive path: ' + name)
            if relative in self.files:
                self.overrides.append({'path': relative, 'lower': str(self.files[relative].archive),
                                       'higher': str(path)})
            self.files[relative] = Source(path, name)


class ConversionAPI:
    """Run a registered translator for every file in scope; each must return a receipt."""

    def __init__(self):
        self.translators = {}

    def register(self, kind, translator):
        if kind in self.translators:
            raise ValueError('Translator already registered: ' + kind)
        self.translators[kind] = translator

    def plan(self, stack, scope='all'):
        return stack.inspect(scope)

    def convert(self, stack, destination, scope='all'):
        """Translate every file in scope into an empty destination folder; returns the receipts."""
        destination = Path(destination).resolve()
        if destination.exists() and any(destination.iterdir()):
            raise ValueError('Conversion output must be empty')
        plan = self.plan(stack, scope)
        missing = sorted(set(plan['asset_counts']) - self.translators.keys())
        if plan['unresolved_texture_references']:
            raise ValueError('Unresolved CTM texture references require resource resolution')
        if missing:
            raise ValueError('Missing translators: ' + ', '.join(missing))
        destination.parent.mkdir(parents=True, exist_ok=True)
        receipts = []
        # Everything is written to a staging folder first and only renamed into place once
        # every receipt checks out, so a failed run leaves no partial output.
        with tempfile.TemporaryDirectory(prefix='.java-conversion-', dir=destination.parent) as temporary:
            staging = Path(temporary) / 'pack'
            staging.mkdir()
            for asset in plan['assets']:
                receipt = self.translators[asset['kind']](stack, asset, staging)
                if receipt.get('source') != asset['path'] or receipt.get('status') != 'converted':
                    raise ValueError('Translator did not convert source: ' + asset['path'])
                if not receipt.get('outputs'):
                    raise ValueError('Missing conversion outputs: ' + asset['path'])
                receipts.append(receipt)
            # Checked after every translator finishes, so a later write cannot slip past an earlier receipt.
            for receipt in receipts:
                for output in receipt['outputs']:
                    path = (staging / output['path']).resolve()
                    if not path.is_relative_to(staging) or not path.is_file():
                        raise ValueError('Invalid conversion output')
                    if output.get('sha256') != sha256_file(path):
                        raise ValueError('Conversion output receipt differs')
            if destination.exists():
                destination.rmdir()
            staging.rename(destination)
        return {'api_version': API_VERSION, 'scope': scope, 'receipts': receipts,
                'converted_sources': len(receipts), 'full_support_verified': False}


def _texture_references(path, properties):
    """{property: tokens} for the textures a CTM rule names.

    OptiFine reads a rule without matchTiles or matchBlocks from its file name:
    <tile>.properties matches that tile, block_<id>.properties that block.
    """
    references = {key: properties[key].split() for key in TEXTURE_PROPERTIES if key in properties}
    stem = PurePosixPath(path).stem
    if 'matchTiles' not in properties and 'matchBlocks' not in properties and not stem.startswith('block_'):
        references['matchTiles'] = [stem]
    return references


def _expanded_tokens(tokens):
    """Tile tokens with ranges such as 0-46 written out, leaving out <skip> and <default>."""
    for token in tokens:
        if token in SPECIAL_TILES:
            continue
        interval = re.fullmatch(r'(\d+)-(\d+)', token)
        if interval:
            yield from map(str, range(int(interval[1]), int(interval[2]) + 1))
        else:
            yield token


def _asset_kind(path, files):
    """What a pack file is, which picks the translator that converts it."""
    if path.endswith('.png'):
        if path.endswith(('_n.png', '_s.png')) and path[:-6] + '.png' in files:
            return 'normal' if path.endswith('_n.png') else 'specular'
        return 'sprite'
    if '/optifine/ctm/' in path and path.endswith('.properties'):
        return 'ctm_rule'
    if '/optifine/cem/' in path and path.endswith(('.jem', '.jpm')):
        return 'entity_model'
    if '/optifine/random/' in path and path.endswith('.properties'):
        return 'random_entity_rule'
    if '/optifine/anim/' in path and path.endswith('.properties'):
        return 'custom_animation_rule'
    if '/models/' in path and path.endswith('.json'):
        return 'model'
    if '/blockstates/' in path and path.endswith('.json'):
        return 'blockstate'
    if path.endswith('.png.mcmeta'):
        return 'animation'
    if '/optifine/' in path and path.endswith('.properties'):
        return 'optifine_configuration'
    return 'metadata'


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--java', type=Path, nargs='+', required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--scope', choices=['all', 'block_ctm'], default='all')
    args = parser.parse_args()
    with PackStack(args.java) as stack:
        report = ConversionAPI().plan(stack, args.scope)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key not in ('assets', 'rules', 'overrides')},
                     indent=2))
