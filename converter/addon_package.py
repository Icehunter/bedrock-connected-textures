"""Compose a converted pack: one add-on with the pack's resources and a small behavior pack.

The behavior pack carries no engine code; its scripts only send the pack's data to the
Bedrock Connected Textures engine, which converted packs depend on. Resources of the base,
terrain, connected and replacement packs are merged in rank order into one pack for every
graphics mode, and the author's compressed artwork is copied into the add-on byte for byte.
"""
from contextlib import ExitStack
import io
import json
from pathlib import Path
import re
import tempfile
import uuid
import zipfile

from PIL import Image

from engine_package import ENGINE, engine_dependency
from pack_identity import manifest_description
from zip_members import copy_compressed, member_name

ENGINE_VERSION = '.'.join(map(str, ENGINE['version']))
ROOT = Path(__file__).resolve().parents[1]
SOURCE_SCRIPTS = ('main.js', 'source-data.js', 'publisher.js')
# Stamped on the scripts the converter writes into a converted pack (see LICENSE-EXCEPTION.md).
OUTPUT_NOTICE = (b'// Written by the Bedrock Connected Textures converter. Converter Output Exception\n'
                 b'// (GPL-3.0 section 7): this file may be distributed under any terms.\n')
# Each part of a packet travels in one script event; ASCII payloads need at most two
# characters of JSON escaping per character.
PACKET_PART_SIZE = 750
# The texture carrier packs give the faces their carriers draw over (connected_build.py).
CARRIER_BLANK = 'bct_owned_transparent'
# Files that several merged packs can each ship; their JSON is combined instead of replaced.
MERGED_JSON_FILES = ('blocks.json', 'textures/terrain_texture.json', 'textures/flipbook_textures.json',
                     'materials/entity.material')
MIN_ENGINE_VERSION = [1, 26, 50]


def dump(value):
    """Compact ASCII JSON bytes with a trailing newline."""
    return (json.dumps(value, separators=(',', ':'), ensure_ascii=True) + '\n').encode()


def source_manifest_dependencies(rp_uuid, version):
    """A pack's behavior pack needs the script API, its own resources and the engine."""
    return [{'module_name': '@minecraft/server', 'version': '2.10.0'}, {'uuid': rp_uuid, 'version': version},
            engine_dependency()]


def write_source_scripts(bp, packets):
    """The only scripts a converted pack carries: its data and the publisher that sends it to the engine."""
    scripts = Path(bp) / 'scripts'
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / 'main.js').write_bytes(source_main())
    (scripts / 'source-data.js').write_bytes(source_data(packets))
    (scripts / 'publisher.js').write_bytes(publisher_script())


def source_main():
    return OUTPUT_NOTICE + (b"import {system,world} from '@minecraft/server';\n"
                            b"import {sources} from './source-data.js';\n"
                            b"import {publishSources} from './publisher.js';\n"
                            b"publishSources({system,world,sources});\n")


def source_data(packets):
    return OUTPUT_NOTICE + b'export const sources = ' + dump(packets).rstrip() + b';\n'


def publisher_script():
    return OUTPUT_NOTICE + (ROOT / 'engine/publisher.mjs').read_bytes()


def read_packets(text):
    """Data carried by a pack's source-data.js, keyed by engine part."""
    result = {}
    for item in exports(text)['sources']:
        result[item['engine']] = json.loads(''.join(item['parts']))
    return result


def exports(text):
    """Read generated JSON exports without executing a source pack's code."""
    result = {}
    decoder = json.JSONDecoder()
    for match in re.finditer(r'export const (\w+)\s*=\s*', text):
        value, _ = decoder.raw_decode(text[match.end():])
        result[match[1]] = value
    if not result:
        raise ValueError('Generated source data is absent')
    return result


def checksum(text):
    """FNV-1a 32-bit hash as 8 hex digits; the engine checks every received packet with the same hash."""
    value = 2166136261
    for character in text:
        value = ((value ^ ord(character)) * 16777619) & 0xffffffff
    return format(value, '08x')


def packet(engine, key, data):
    """Script-event packet for one engine part ('connected', 'terrain' or 'replace')."""
    text = json.dumps(data, separators=(',', ':'), ensure_ascii=True)
    return {'engine': engine, 'provider': key, 'digest': checksum(text),
            'parts': [text[index:index + PACKET_PART_SIZE] for index in range(0, len(text), PACKET_PART_SIZE)]}


def compose_source_addon(base_rp, connected_addon, output, *, key, title, version=None, terrain_addon=None,
                         icon=None, replacement_addon=None, identity=None, raytraced=False):
    """Publish one converted pack: resources plus a behavior pack that depends on the engine.

    raytraced: base_rp is one pack for every graphics mode (like the author's own Bedrock pack): it
    declares ray tracing without a subpack, every block material uses RGB MER, and the blocks the
    carrier pack blanks keep base_rp's definition, so they show their own faces where carriers do
    not draw (ray tracing).

    icon: PNG bytes of the Java pack's own pack.png, used for both packs.
    replacement_addon: folder from native_replacement.build_replacements with
    Replace_BP, Replace_RP and the engine data in engine-data.json.
    """
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,79}', key):
        raise ValueError('Source key must be a lowercase slug')
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        sources = _SourcePacks(stack, output)
        sources.collect(base_rp, 0)
        sources.collect(terrain_addon, 10)
        sources.collect(connected_addon, 0)
        sources.collect(replacement_addon, 5)
        packs = sources.packs
        resource_packs = [item for item in packs if item['kind'] == 'rp']
        if not resource_packs:
            raise ValueError('Source addon has no base resources')
        base = min(resource_packs, key=lambda item: item['rank'])
        version = list(version or (identity or {}).get('version') or base['manifest']['header']['version'])
        if len(version) != 3 or any(type(value) is not int or value < 0 for value in version):
            raise ValueError('Invalid source version')
        rp_uuid = base['manifest']['header']['uuid']
        target_manifests = _target_manifests(key, title, version, rp_uuid, identity, raytraced=raytraced)
        replacement = None
        if replacement_addon is not None and (Path(replacement_addon) / 'engine-data.json').is_file():
            replacement = json.loads((Path(replacement_addon) / 'engine-data.json').read_text(encoding='utf-8'))
        selected, configuration, terrain = _merge_regular_packs(packs)
        if raytraced:
            _keep_blanked_faces(selected['rp'], base)
            # Like the author's own Bedrock pack, every block material uses RGB MER, which ray tracing
            # needs and Vibrant Visuals reads too: the swapped blocks, leaves and edges are rewritten.
            texture_sets, converted_maps = _mer_texture_sets(selected['rp'])
            selected['rp'].update(texture_sets)
            selected['rp'].update(converted_maps)
            _drop_unused_mers(selected['rp'])
        if configuration is None:
            raise ValueError('Source addon has no connected-texture data')
        _share_repeat_update_component(selected['bp'])
        packets = [packet('connected', key, configuration)]
        if terrain:
            packets.append(packet('terrain', key, terrain))
        if replacement:
            packets.append(packet('replace', key, replacement))
        for kind in ('bp', 'rp'):
            if icon is not None:
                selected[kind]['pack_icon.png'] = icon
            else:
                selected[kind].pop('pack_icon.png', None)
        selected['bp']['scripts/main.js'] = source_main()
        selected['bp']['scripts/source-data.js'] = source_data(packets)
        selected['bp']['scripts/publisher.js'] = publisher_script()
        for kind in ('bp', 'rp'):
            selected[kind]['manifest.json'] = dump(target_manifests[kind])
        preserved = _write_addon(output, selected, target_manifests)
    receipt = {'archive': str(output), 'key': key, 'title': title, 'version': version,
               'packs': {kind: item['header'] for kind, item in target_manifests.items()},
               'engine_dependency': engine_dependency(),
               'connected_rules': len(configuration['rules']), 'terrain_providers': len(terrain),
               'replacement_blocks': len(replacement['blocks']) if replacement else 0,
               'source_artwork_compressed_members_preserved': len(preserved), 'in_game_verified': False}
    output.with_suffix('.report.json').write_bytes(dump(receipt))
    return output


class _SourcePacks:
    """The packs inside each source add-on (a folder or a ZIP), read without unpacking."""

    def __init__(self, stack, output):
        self.stack = stack
        self.output = output
        self.packs = []
        self.opened = set()

    def collect(self, source, rank):
        """Add every pack in a source add-on; a higher rank wins when packs ship the same file."""
        if source is None:
            return
        source = Path(source).resolve()
        if self.output == source or (source.is_dir() and self.output.is_relative_to(source)):
            raise ValueError('Source addon must not replace its input')
        if source in self.opened:
            return
        files, reader = self._files(source)
        self.opened.add(source)
        roots = [name.removesuffix('manifest.json') for name in files
                 if name == 'manifest.json' or (name.count('/') == 1 and name.endswith('/manifest.json'))]
        for prefix in roots:
            manifest = json.loads(reader(files[prefix + 'manifest.json']))
            types = {module['type'] for module in manifest['modules']}
            kind = 'rp' if 'resources' in types else 'bp' if 'data' in types else None
            if kind is None:
                raise ValueError('Source pack has unsupported module types')
            names = {name[len(prefix):]: value for name, value in files.items() if name.startswith(prefix)}
            folder = prefix.rstrip('/')
            # Connected packs win over repeat packs, which win over the rest of the same source.
            priority = rank + (30 if folder.startswith('Connected_') else 20 if 'Repeat_' in folder else 0)
            self.packs.append({'manifest': manifest, 'kind': kind, 'files': names, 'read': reader,
                               'rank': priority, 'folder': folder})

    def _files(self, source):
        """{relative path: Path or (archive, entry)} of a source, and a function that reads one."""
        if source.is_dir():
            files = {path.relative_to(source).as_posix(): path for path in source.rglob('*') if path.is_file()}
            return files, lambda value: value.read_bytes()
        archive = self.stack.enter_context(zipfile.ZipFile(source))
        files = {}
        for entry in archive.infolist():
            name = member_name(entry.filename)
            if not entry.is_dir():
                if name in files:
                    raise ValueError('Duplicate source member')
                files[name] = (archive, entry)
        return files, lambda value: value[0].read(value[1])


def _target_manifests(key, title, version, rp_uuid, identity, *, raytraced=False):
    """The add-on's behavior and resource pack manifests.

    The pack keeps its author's words, credit and version; it only adds what it needs.
    """
    manifests = {}
    for kind, pack_uuid in (('bp', _source_uuid(key, 'bp')), ('rp', rp_uuid)):
        metadata = {'generated_with': {'bedrock_connected_textures': [ENGINE_VERSION]}}
        if identity and identity.get('authors'):
            metadata['authors'] = identity['authors']
        manifest = {'format_version': 2,
                    'header': {'name': title, 'description': manifest_description(identity or {'description': ''}),
                               'uuid': pack_uuid, 'version': version, 'min_engine_version': list(MIN_ENGINE_VERSION)},
                    'metadata': metadata,
                    'modules': [{'type': 'data' if kind == 'bp' else 'resources',
                                 'uuid': _source_uuid(key, kind + '-module'), 'version': version}]}
        if kind == 'bp':
            manifest['modules'].append({'type': 'script', 'language': 'javascript', 'entry': 'scripts/main.js',
                                        'uuid': _source_uuid(key, 'script'), 'version': version})
            manifest['dependencies'] = source_manifest_dependencies(rp_uuid, version)
        else:
            manifest['capabilities'] = ['pbr', 'raytraced'] if raytraced else ['pbr']
        manifests[kind] = manifest
    return manifests


def _source_uuid(key, part):
    """A fixed UUID for one part of a converted pack, so updates replace the same pack in the game."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'bct/source/' + key + '/' + part))


def _keep_blanked_faces(resources, base):
    """Give the blocks a carrier pack blanked their definition from the base pack back.

    Carriers draw those faces in Classic and Vibrant Visuals over the block's own faces; ray tracing
    hides carriers, so in one pack for every mode the block needs its faces.
    """
    if 'blocks.json' not in resources or 'blocks.json' not in base['files']:
        return
    merged = json.loads(_bytes_of(resources['blocks.json']))
    own = json.loads(base['read'](base['files']['blocks.json']).decode('utf-8-sig'))
    for name, entry in merged.items():
        textures = entry.get('textures') if isinstance(entry, dict) else None
        faces = textures.values() if isinstance(textures, dict) else [textures]
        if CARRIER_BLANK in faces and name in own:
            merged[name] = own[name]
    resources['blocks.json'] = dump(merged)


def _drop_unused_mers(resources):
    """Remove the block MERS images no texture set refers to any more (they were rewritten to MER)."""
    referenced = set()
    for name, value in resources.items():
        if name.startswith('textures/blocks/') and name.endswith('.texture_set.json'):
            for channel in json.loads(_bytes_of(value)).get('minecraft:texture_set', {}).values():
                if isinstance(channel, str):
                    referenced.add((Path(name).parent / (channel + '.png')).as_posix())
    for name in [name for name in resources if name.startswith('textures/blocks/') and name.endswith('_mers.png')]:
        if name not in referenced:
            del resources[name]


def _merge_regular_packs(packs):
    """Merge the regular packs in rank order: ({'bp': files, 'rp': files}, connected data, terrain providers).

    Scripts are not copied: their data is read and published again by the add-on's own
    scripts.
    """
    selected = {'bp': {}, 'rp': {}}
    configuration = None
    terrain = []
    for pack in sorted(packs, key=lambda item: item['rank']):
        for name, value in pack['files'].items():
            if name == 'manifest.json' or name.startswith(('licenses/', '.')):
                continue
            if pack['kind'] == 'bp' and name.startswith('scripts/'):
                if name == 'scripts/source-data.js':
                    data = read_packets(pack['read'](value).decode('utf-8-sig'))
                    if 'connected' in data:
                        configuration = data['connected']
                    terrain.extend(data.get('terrain', []))
                continue
            previous = selected[pack['kind']].get(name)
            if previous is not None and name in MERGED_JSON_FILES:
                selected[pack['kind']][name] = _merged_json(name, previous, value)
            else:
                selected[pack['kind']][name] = value
    return selected, configuration, terrain


def _share_repeat_update_component(behavior_files):
    """Point each repeat block's bct:update_repeat component at its own pattern size.

    Repeat blocks keep the materials of the source they came from; only this component is
    shared with the engine.
    """
    for name, value in list(behavior_files.items()):
        if not name.startswith('blocks/') or not name.endswith('.json'):
            continue
        block = json.loads(_bytes_of(value))
        body = block.get('minecraft:block', {})
        component = body.get('components', {}).get('bct:update_repeat')
        if component is not None:
            states = body['description']['states']
            height, period = len(states['bct:y']), len(states['bct:x'])
            body['components']['bct:update_repeat'] = {'height': height, 'period': period}
            behavior_files[name] = dump(block)


def _mer_texture_sets(resources):
    """Block texture sets with a four-channel MERS map, rewritten to the RGB MER ray tracing reads.

    Returns (texture sets, converted MER images).
    """
    texture_sets = {}
    converted_maps = {}
    for name, value in resources.items():
        if not name.startswith('textures/blocks/') or not name.endswith('.texture_set.json'):
            continue
        document = json.loads(_bytes_of(value))
        channels = document.get('minecraft:texture_set', {})
        mers = channels.pop('metalness_emissive_roughness_subsurface', None)
        if mers is None:
            continue
        if isinstance(mers, list):
            channels['metalness_emissive_roughness'] = mers[:3]
        elif isinstance(mers, str):
            source = (Path(name).parent / (mers + '.png')).as_posix()
            if source not in resources:
                raise ValueError('MERS image is absent: ' + source)
            target = (Path(name).parent / (Path(mers).name + '_mer.png')).as_posix()
            if target not in converted_maps:
                with Image.open(io.BytesIO(_bytes_of(resources[source]))) as image:
                    converted_maps[target] = _png_bytes(image.convert('RGB'))
            channels['metalness_emissive_roughness'] = Path(target).stem
        else:
            raise ValueError('Invalid native MERS binding: ' + name)
        texture_sets[name] = dump(document)
    return texture_sets, converted_maps


def _write_addon(output, selected, target_manifests):
    """Write Source_BP and Source_RP into the .mcaddon, then check it before it replaces output.

    Returns {member: (CRC, size, compressed size)} for the artwork copied without
    recompressing; those members are checked to be unchanged.
    """
    with tempfile.NamedTemporaryFile(prefix='.source-addon-', suffix='.mcaddon', dir=output.parent,
                                     delete=False) as handle:
        temporary = Path(handle.name)
    preserved = {}
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as destination:
            for kind, prefix in (('bp', 'Source_BP'), ('rp', 'Source_RP')):
                # Windows file names ignore case, so two names that differ only by case collide.
                folded = set()
                for name, value in selected[kind].items():
                    name = member_name(name)
                    if name.casefold() in folded:
                        raise ValueError('Merged source paths collide on Windows')
                    folded.add(name.casefold())
                    target = prefix + '/' + name
                    if isinstance(value, bytes):
                        destination.writestr(target, value)
                    elif isinstance(value, Path):
                        destination.write(value, target)
                    else:
                        source, entry = value
                        copy_compressed(source, entry, destination, target)
                        preserved[target] = (entry.CRC, entry.file_size, entry.compress_size)
        with zipfile.ZipFile(temporary) as checked:
            for name, signature in preserved.items():
                entry = checked.getinfo(name)
                if (entry.CRC, entry.file_size, entry.compress_size) != signature:
                    raise ValueError('Source artwork metadata changed')
            for kind, prefix in (('bp', 'Source_BP'), ('rp', 'Source_RP')):
                if json.loads(checked.read(prefix + '/manifest.json')) != target_manifests[kind]:
                    raise ValueError('Source manifest failed verification')
            scripts = [name for name in checked.namelist() if name.startswith('Source_BP/scripts/')]
            if sorted(scripts) != sorted('Source_BP/scripts/' + name for name in SOURCE_SCRIPTS):
                raise ValueError('A converted pack must not contain engine scripts')
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return preserved


def _bytes_of(item):
    """Bytes of a selected file: bytes as they are, a file's contents, or an (archive, entry) member."""
    if isinstance(item, bytes):
        return item
    return item.read_bytes() if isinstance(item, Path) else item[0].read(item[1])


def _merged_json(name, prior, incoming):
    """Two packs' copies of one JSON file combined, the incoming one winning on equal keys."""
    left, right = json.loads(_bytes_of(prior)), json.loads(_bytes_of(incoming))
    if name == 'textures/terrain_texture.json':
        result = {**left, **right, 'texture_data': {**left.get('texture_data', {}), **right.get('texture_data', {})}}
    elif name == 'materials/entity.material':
        result = {**left, **right, 'materials': {**left.get('materials', {}), **right.get('materials', {})}}
    elif isinstance(left, list) and isinstance(right, list):
        result = list(left)
        for entry in right:
            if entry not in result:
                result.append(entry)
    else:
        result = {**left, **right}
    return dump(result)


def _png_bytes(image):
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()
