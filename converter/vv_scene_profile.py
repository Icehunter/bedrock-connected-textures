"""Bring an author's Vibrant Visuals scene settings, and water, from their own Bedrock edition.

A Java pack has no Vibrant Visuals lighting, fog or water settings. When the author also
made a Bedrock edition, its scene settings and the biome links to them are copied into the
converted pack; its artwork is not. Models, sprites, texture sets, colour maps and
grass and foliage colours stay the converted pack's own, and the reference's sky and water
colours only fill in where a biome has none.
"""
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

from block_ids import JAVA_RENAMES, known_blocks
from common import read_json, write_json

# Scene settings folder -> the document's main key.
PROFILE_TYPES = {
    'lighting': 'minecraft:lighting_settings',
    'color_grading': 'minecraft:color_grading_settings',
    'atmospherics': 'minecraft:atmosphere_settings',
    'water': 'minecraft:water_settings',
    'pbr': 'minecraft:pbr_fallback_settings',
    'shadows': 'minecraft:shadow_settings',
    'fogs': 'minecraft:fog_settings',
    'local_lighting': 'minecraft:local_light_settings',
}
# Client biome component -> (identifier field, settings folder it names).
BIOME_LINKS = {
    'minecraft:lighting_identifier': ('lighting_identifier', 'lighting'),
    'minecraft:color_grading_identifier': ('color_grading_identifier', 'color_grading'),
    'minecraft:atmosphere_identifier': ('atmosphere_identifier', 'atmospherics'),
    'minecraft:water_identifier': ('water_identifier', 'water'),
    'minecraft:fog_appearance': ('fog_identifier', 'fogs'),
}
# Biome looks a Java pack never defines; taken from the reference only where the destination has none.
APPEARANCE = ('minecraft:sky_color', 'minecraft:water_appearance')
DESTINATION_FIRST = {'minecraft:fog_appearance', *APPEARANCE}
WATER_TEXTURES = ('water_still', 'water_still_grey', 'water_flow', 'water_flow_grey')
WATER_MAP_SUFFIXES = ('normal', 'heightmap', 'mer', 'mers')
# The block fallback when the reference sets none: no metal, no emission, fully rough, no subsurface.
DEFAULT_BLOCK_MERS = (0, 0, 255, 0)


def version_tuple(value):
    return tuple(int(part) for part in value.split('.'))


def complete_fog_density(document):
    """Give non-uniform volumetric fog the heights current Bedrock requires.

    Older fog files could leave max_density_height and zero_density_height out; current
    Bedrock rejects them unless "uniform" is true. Vanilla's own fogs use 320 for both, so
    the author's densities stay the same and the files load.
    """
    density = document['minecraft:fog_settings'].get('volumetric', {}).get('density', {})
    for layer in density.values():
        if isinstance(layer, dict) and not layer.get('uniform', False):
            layer.setdefault('max_density_height', 320.0)
            layer.setdefault('zero_density_height', 320.0)


def read_scene_profile(reference):
    """Read and resolve the reference's scene settings before anything is written.

    reference is the author's Bedrock pack: a folder or an .mcpack/.zip with one
    resource pack. Every biome link must name a settings file the reference ships.
    """
    reference = Path(reference).resolve()
    members = _scene_members(reference)
    documents = {}
    identifiers = {folder: {} for folder in PROFILE_TYPES}
    biomes = []
    for relative, data in sorted(members.items()):
        folder = PurePosixPath(relative).parts[0]
        document = json.loads(data.decode('utf-8-sig'))
        if folder == 'biomes':
            if 'minecraft:client_biome' in document:
                biomes.append((relative, document))
            continue
        kind = PROFILE_TYPES[folder]
        if kind not in document:
            raise ValueError('Invalid VV scene document: ' + relative)
        if folder == 'fogs':
            complete_fog_density(document)
        documents[relative] = document
        identifier = document[kind].get('description', {}).get('identifier')
        if identifier:
            if identifier in identifiers[folder]:
                raise ValueError('Duplicate VV scene identifier: ' + identifier)
            identifiers[folder][identifier] = relative
    if not documents:
        raise ValueError('Reference has no VV scene settings')
    links = [link for link in (_biome_link(relative, document, identifiers) for relative, document in biomes) if link]
    pbr = documents.get('pbr/global.json', {}).get('minecraft:pbr_fallback_settings', {})
    block_mers = pbr.get('blocks', {}).get('global_metalness_emissive_roughness_subsurface', list(DEFAULT_BLOCK_MERS))
    if len(block_mers) != 4 or any(not isinstance(value, int) or not 0 <= value <= 255 for value in block_mers):
        raise ValueError('Expected four RGB8 block fallback MERS channels')
    return {'reference': str(reference), 'documents': documents, 'biome_links': links,
            'block_mers': block_mers,
            'selected_source_sha256': {path: hashlib.sha256(members[path]).hexdigest()
                                       for path in (*documents, *(link[0] for link in links))}}


def read_water_textures(reference):
    """The reference pack's own water textures and their flipbook entries, or None.

    Java water textures are drawn for Java's renderer (often opaque, left to shaders);
    a pack author's Bedrock edition carries water made for Bedrock's water shading.
    Returns {'files': {relative path: bytes}, 'flipbooks': [entries]}.
    """
    reference = Path(reference).resolve()
    if reference.is_dir():
        members = {path.relative_to(reference).as_posix(): path for path in reference.rglob('*') if path.is_file()}
        return _water_textures(members, lambda relative: members[relative].read_bytes())
    with zipfile.ZipFile(reference) as archive:
        prefix = _pack_prefix(archive)
        members = {name[len(prefix):]: name for name in archive.namelist() if name.startswith(prefix)}
        return _water_textures(members, lambda relative: archive.read(members[relative]))


def apply_scene_profile(resource_pack, profile, *, samples=None):
    """Write the scene settings and biome links into a VV pack, keeping its own biome looks.

    samples is bedrock-samples' resource_pack folder. A linked biome the pack does not
    define starts from Mojang's client biome there, else from an empty one. Local lights
    must name Bedrock blocks (the game logs "is not a block" otherwise): a Java id is
    renamed to its Bedrock block and a block Bedrock does not know is dropped. Without
    Mojang's block list next to samples the local lights are copied as they are.
    """
    resource_pack = Path(resource_pack).resolve()
    manifest = read_json(resource_pack / 'manifest.json')
    if 'pbr' not in manifest.get('capabilities', []):
        raise ValueError('VV scene settings require a VV resource pack')
    prepared = dict(profile['documents'])
    renamed_lights, dropped_lights = [], []
    known = _known_bedrock_blocks(samples) if samples else None
    if known is not None:
        for relative, document in profile['documents'].items():
            if PurePosixPath(relative).parts[0] == 'local_lighting':
                prepared[relative], renamed, dropped = _bedrock_local_lights(relative, document, known)
                renamed_lights.extend(renamed)
                dropped_lights.extend(dropped)
    existing = {identifier: (path.relative_to(resource_pack).as_posix(), document)
                for path, identifier, document in _client_biomes(resource_pack / 'biomes')}
    inherited = {}
    if samples:
        inherited = {identifier: document for _, identifier, document in _client_biomes(Path(samples) / 'biomes')}
    for relative, identifier, version, scene_links, appearance in profile['biome_links']:
        if identifier in existing:
            relative, document = existing[identifier]
            document = copy.deepcopy(document)
        else:
            empty = {'format_version': version,
                     'minecraft:client_biome': {'description': {'identifier': identifier}, 'components': {}}}
            document = copy.deepcopy(inherited.get(identifier, empty))
        if version_tuple(document['format_version']) < version_tuple(version):
            document['format_version'] = version
        components = document['minecraft:client_biome'].setdefault('components', {})
        own = existing[identifier][1]['minecraft:client_biome'].get('components', {}) if identifier in existing else {}
        for component, value in {**appearance, **scene_links}.items():
            if component in DESTINATION_FIRST and component in own:
                continue
            components[component] = value
        prepared[relative] = document
    changed = []
    for relative, document in prepared.items():
        path = (resource_pack / relative).resolve()
        if not path.is_relative_to(resource_pack):
            raise ValueError('VV scene output escapes resource pack')
        if not path.exists() or read_json(path) != document:
            write_json(path, document)
            changed.append(relative)
    return {'reference': profile['reference'], 'scene_files': len(profile['documents']),
            'biomes_linked': len(profile['biome_links']), 'changed_files': sorted(changed),
            'output_files': sorted(prepared),
            'local_lights_renamed': renamed_lights, 'local_lights_dropped': dropped_lights,
            'block_fallback_mers': profile['block_mers'],
            'selected_source_sha256': profile['selected_source_sha256'],
            'artwork_modified': False,
            'biome_appearance_imported': any(link[4] for link in profile['biome_links']),
            'source_modified': False, 'in_game_verified': False}


def bind_block_fallbacks(resource_pack, block_mers=DEFAULT_BLOCK_MERS):
    """Give carrier faces the block fallback instead of the mob fallback.

    Only a missing MER/MERS channel receives a constant texture-set value.
    Supplied maps, normals, albedo, alpha and animation sheets stay unchanged.
    """
    resource_pack = Path(resource_pack).resolve()
    if len(block_mers) != 4 or any(type(value) is not int or not 0 <= value <= 255 for value in block_mers):
        raise ValueError('Expected four RGB8 block fallback MERS channels')
    manifest = read_json(resource_pack / 'manifest.json')
    if 'pbr' not in manifest.get('capabilities', []):
        return {'changed_files': [], 'block_fallback_mers': list(block_mers)}
    textures = set()
    for path in (resource_pack / 'entity').glob('bct_*.entity.json'):
        description = read_json(path).get('minecraft:client_entity', {}).get('description', {})
        if description.get('identifier', '').startswith('bct:'):
            textures.update(description.get('textures', {}).values())
    changed = []
    for relative in sorted(textures):
        color = (resource_pack / (relative + '.png')).resolve()
        if not color.is_relative_to(resource_pack) or not color.is_file():
            raise ValueError('Missing generated carrier albedo: ' + relative)
        path = color.with_suffix('.texture_set.json')
        document = read_json(path) if path.exists() else {
            'format_version': '1.21.30', 'minecraft:texture_set': {'color': color.stem}}
        channels = document['minecraft:texture_set']
        if not any(key in channels for key in ('metalness_emissive_roughness',
                                               'metalness_emissive_roughness_subsurface')):
            channels['metalness_emissive_roughness_subsurface'] = list(block_mers)
            write_json(path, document)
            changed.append(path.relative_to(resource_pack).as_posix())
    return {'changed_files': changed, 'block_fallback_mers': list(block_mers),
            'authored_pbr_modified': False, 'albedo_modified': False}


def _pack_prefix(archive):
    """The folder of the one resource pack in a reference archive; subpacks do not count."""
    manifests = [name for name in archive.namelist()
                 if name.endswith('manifest.json') and '/subpacks/' not in '/' + name]
    if len(manifests) != 1:
        raise ValueError('VV scene reference must contain one resource pack')
    return manifests[0].removesuffix('manifest.json')


def _scene_members(reference):
    """{relative path: bytes} of the reference's scene settings and client biome JSON files."""
    folders = (*PROFILE_TYPES, 'biomes')
    if reference.is_dir():
        return {path.relative_to(reference).as_posix(): path.read_bytes()
                for folder in folders for path in (reference / folder).rglob('*.json')}
    with zipfile.ZipFile(reference) as archive:
        prefix = _pack_prefix(archive)
        members = {}
        for name in archive.namelist():
            if not name.startswith(prefix) or not name.endswith('.json'):
                continue
            relative = name[len(prefix):]
            path = PurePosixPath(relative)
            if path.is_absolute() or '..' in path.parts or '\\' in relative:
                raise ValueError('Unsafe scene reference member: ' + name)
            if path.parts and path.parts[0] in folders:
                members[relative] = archive.read(name)
        return members


def _biome_link(relative, document, identifiers):
    """(path, biome, format version, linked components, appearance) for one client biome, or None.

    Raises ValueError when the biome links to settings the reference does not ship.
    """
    biome = document['minecraft:client_biome']
    components = biome.get('components', {})
    linked = {}
    for component, (field, folder) in BIOME_LINKS.items():
        value = components.get(component)
        if value is None:
            continue
        identifier = value.get(field)
        if identifier not in identifiers[folder]:
            raise ValueError('Unresolved VV scene link: ' + str(identifier))
        linked[component] = copy.deepcopy(value)
    appearance = {component: copy.deepcopy(biome['components'][component])
                  for component in APPEARANCE if component in components}
    if not linked and not appearance:
        return None
    return relative, biome['description']['identifier'], document['format_version'], linked, appearance


def _known_bedrock_blocks(samples):
    """Every Bedrock block id, from the bedrock-samples checkout samples is in; None when not found."""
    samples = Path(samples)
    for root in (samples, samples.parent):
        if (root / 'metadata/vanilladata_modules/mojang-blocks.json').is_file():
            return known_blocks(root)
    return None


def _bedrock_local_lights(relative, document, known):
    """(document, renamed, dropped): a local lighting document keyed by Bedrock block ids only.

    An author's Bedrock edition can name Java blocks (wall_torch, jack_o_lantern); each
    becomes its Bedrock block when block_ids knows one, else it is dropped. An entry the
    author keyed by the Bedrock id itself wins over one renamed onto the same block.
    """
    result = copy.deepcopy(document)
    settings = result['minecraft:local_light_settings']
    explicit = {_namespaced(name) for name in settings if _namespaced(name) in known}
    lights = {}
    renamed, dropped = [], []
    for name, value in settings.items():
        block = _namespaced(name)
        if block in known:
            lights[name] = value
            continue
        targets = JAVA_RENAMES.get(block) or []
        targets = [target for target in (targets if isinstance(targets, list) else [targets]) if target in known]
        if not targets:
            dropped.append({'file': relative, 'block': name})
            continue
        renamed.append({'file': relative, 'block': name, 'bedrock': targets})
        for target in targets:
            if target not in explicit:
                lights.setdefault(target, value)
    result['minecraft:local_light_settings'] = lights
    return result, renamed, dropped


def _namespaced(block):
    return block if ':' in block else 'minecraft:' + block


def _water_textures(members, read):
    """{'files', 'flipbooks'} of the water textures among members, or None without any."""
    files = {}
    for relative in members:
        path = PurePosixPath(relative)
        if path.parent.as_posix() != 'textures/blocks' or '..' in path.parts:
            continue
        if _is_water_texture(path.name.split('.')[0]):
            files[relative] = read(relative)
    flipbooks = []
    if 'textures/flipbook_textures.json' in members:
        entries = json.loads(read('textures/flipbook_textures.json').decode('utf-8-sig'))
        flipbooks = [entry for entry in entries
                     if entry.get('flipbook_texture', '').removeprefix('textures/blocks/') in WATER_TEXTURES]
    return {'files': files, 'flipbooks': flipbooks} if files else None


def _is_water_texture(stem):
    """A water texture, or one of its normal, heightmap, MER or MERS maps."""
    return any(stem == name or (stem.startswith(name + '_') and stem[len(name) + 1:] in WATER_MAP_SUFFIXES)
               for name in WATER_TEXTURES)


def _client_biomes(folder):
    """(path, identifier, document) of each client biome file in a folder that names its biome."""
    for path in folder.glob('*.json'):
        document = read_json(path)
        biome = document.get('minecraft:client_biome', {})
        identifier = biome.get('description', {}).get('identifier')
        if identifier:
            yield path, identifier, document
