"""Tools for packs written by hand for BCT (docs/AUTHORING.md).

`python bct.py init` makes a pack's behavior pack send its data to the engine;
`leaves` and `edge` write 3D leaf blocks and ground edge blocks the same way.
`python bct.py block` writes a pack author's custom copy of a vanilla block
into their own behavior and resource packs: the states the engine sets
(bct:x/bct:y/bct:z for a repeat, bct:r for random tiles, and the vanilla
block's own states as bct:<name>), one texture per face for every pattern
place, and the vanilla gameplay the converter gives its replacement blocks
(mining time and tool, drops, explosion resistance, map colour, sounds,
flammability, redstone), from the same tables. It also returns the block's
entry for scripts/bct.js.

The textures are the author's: tile n of a pattern is the texture
<pack>_<name>_<n> (a repeat counts its tiles row by row from the top left, as
in Java's CTM repeat method). With a grid image the tiles are cut from it;
otherwise the author adds the files the command lists.
"""
import json
import math
from pathlib import Path
import re
import uuid

import numpy as np
from PIL import Image

from bedrock_schema import Schemas, block_errors, check_tree
from block_gameplay import block_tags, components as gameplay_components, loot_table, mining, needed_tool
from block_shapes import shape_components, shape_of, shaped_block, single_slab
from common import BLOCKS_FORMAT, read_json, write_json
from edge_shapes import edge_alpha_from_image
from java_block_geometry import cube, default_uv
from engine_package import ENGINE, engine_dependency
from terrain_native import _copy_other_maps, _geometry_model, _material_block, _material_planes
from native_replacement import (AXIS_ROTATION, BLOCK_FORMAT, FACES, _behavior_of, _culling_rules,
                                _fallback_reason, _keep_vanilla_reason, _replacement_geometry, _state_values,
                                load_policy, profile_of, repeat_index, vanilla_tags_of)
from pack_scan import atlas_messages, atlas_paths, ray_tracing_messages

ROOT = Path(__file__).resolve().parents[1]
PACK_ID = re.compile(r'^[a-z0-9_-]{1,80}$')
BLOCK_ID = re.compile(r'^[a-z0-9_.-]+:[a-z0-9_./-]+$')
# The most pattern places one block may have (the converter's own permutation limit).
MAX_PLACES = 4096
MAX_REPEAT = 16
MAX_RANDOM = 16


class AuthoringError(ValueError):
    """A request the command refuses, with the reason for the author."""


def _vanilla_table(name, root=ROOT):
    """One of the engine's vanilla block lists (engine/vanilla-blocks.mjs)."""
    text = (Path(root) / 'engine/vanilla-blocks.mjs').read_text(encoding='utf-8')
    found = re.search(r'VANILLA_' + name + r' = Object\.freeze\((\[.*?\])\);', text)
    return set(json.loads(found.group(1)))


def vanilla_solid(root=ROOT):
    """The vanilla opaque full cubes."""
    return _vanilla_table('SOLID', root)


def vanilla_cubes(root=ROOT):
    """The vanilla full cubes, see-through ones (glass, ice) included."""
    return _vanilla_table('CUBES', root)


def places(pattern):
    """[(states, tile of each face)] for every place in the pattern; pattern is {'repeat': [w, h]} or {'random': weights}."""
    if 'repeat' in pattern:
        width, height = pattern['repeat']
        period = math.lcm(width, height)
        result = []
        for x in range(period):
            for y in range(height):
                for z in range(period):
                    tiles = {face: repeat_index((x, y, z), face, width, height) for face in FACES}
                    result.append(({'bct:x': x, 'bct:y': y, 'bct:z': z}, tiles))
        return result
    return [({'bct:r': pick}, {face: pick for face in FACES}) for pick in range(len(pattern['random']))]


def tile_count(pattern):
    return pattern['repeat'][0] * pattern['repeat'][1] if 'repeat' in pattern else len(pattern['random'])


def check_pattern(pattern):
    """The pattern as {'repeat': [w, h]} or {'random': weights}; AuthoringError when it is not one."""
    if 'repeat' in pattern:
        width, height = pattern['repeat']
        if not (1 <= width <= MAX_REPEAT and 1 <= height <= MAX_REPEAT):
            raise AuthoringError(f'a repeat is 1 to {MAX_REPEAT} tiles wide and high')
        if math.lcm(width, height) ** 2 * height > MAX_PLACES:
            raise AuthoringError(f'a {width}x{height} repeat needs more than {MAX_PLACES} block permutations; '
                                 'use sizes that share a factor, such as 2x4, 3x3 or 4x4')
    else:
        weights = pattern['random']
        if not (2 <= len(weights) <= MAX_RANDOM) or any(weight < 0 for weight in weights) or not any(weights):
            raise AuthoringError(f'random takes 2 to {MAX_RANDOM} weights, such as 4 2 1 1')
    return pattern


def see_through_method(files):
    """The render method see-through tiles need: blend when any pixel is partly clear, else alpha_test."""
    for path in files:
        with Image.open(path) as image:
            alpha = image.convert('RGBA').getchannel('A')
            if any(0 < value < 255 for value, count in enumerate(alpha.histogram()) if count):
                return 'blend'
    return 'alpha_test'


def _swap_target(vanilla, identifier, *, samples, policy, root, kind):
    """What a pack block standing in for a vanilla block starts from: the vanilla block's gameplay and states.

    kind names the block in messages ('pattern', 'connected').
    """
    if not BLOCK_ID.fullmatch(identifier):
        raise AuthoringError('the block id must look like mypack:stone')
    pack, name = identifier.split(':', 1)
    if not PACK_ID.fullmatch(pack) or pack == 'minecraft':
        raise AuthoringError("the block id's namespace must be your pack id, not minecraft")
    samples = Path(samples)
    game_blocks = read_json(samples / 'metadata/vanilladata_modules/mojang-blocks.json')
    known = {item['name']: item for item in game_blocks['data_items']}
    if vanilla not in known:
        raise AuthoringError(vanilla + ' is not a Bedrock block id')
    reason = _keep_vanilla_reason(vanilla, policy) or _fallback_reason(vanilla, policy)
    if reason:
        raise AuthoringError(vanilla + ' stays vanilla: ' + reason)
    profile = profile_of(vanilla, policy)
    # Double slabs are full cubes the engine's cube table leaves out; slabs are drawn by their shape.
    # Connected blocks draw on whole faces, so only pattern blocks take shapes.
    cube_like = vanilla in vanilla_solid(root) or single_slab(vanilla) is not None
    shape = shape_of(vanilla) if kind == 'pattern' else None
    refusal = (f'{vanilla} is not a full cube or a slab; pattern blocks are full cubes or slabs' if kind == 'pattern'
               else f'{vanilla} is not a full cube; {kind} blocks are full cubes')
    if profile is None:
        if not cube_like and not shape:
            raise AuthoringError(refusal)
        raise AuthoringError(vanilla + ' has no gameplay profile in converter/data/native-replacement.json yet')
    transparent = bool(profile.get('transparent'))
    if not transparent and not cube_like and not shape:
        raise AuthoringError(refusal)
    state_values = {item['name']: [entry['value'] for entry in item['values']] for item in game_blocks['block_properties']}
    vanilla_states = [prop['name'] for prop in known[vanilla].get('properties', [])]
    mirror = {state: 'bct:' + state.split(':')[-1] for state in vanilla_states}
    return {'vanilla': vanilla, 'identifier': identifier, 'pack': pack, 'name': name, 'samples': samples, 'policy': policy,
            'known': known, 'profile': profile, 'transparent': transparent, 'mirror': mirror, 'shape': shape,
            'states': {mirror[state]: _state_values(state_values[state]) for state in vanilla_states}}


def _swap_components(target, geometry, instances):
    """The block's components: its look, then the vanilla block's mining, drops, sounds and the rest."""
    profile, transparent, name = target['profile'], target['transparent'], target['name']
    components = {
        'minecraft:geometry': geometry,
        'minecraft:material_instances': instances,
        'minecraft:light_dampening': profile.get('light_dampening', 15),
        'minecraft:destructible_by_mining': mining(profile),
        'minecraft:loot': f"loot_tables/{target['pack']}/{name}.json",
        'minecraft:display_name': target['known'][target['vanilla']].get('serialization_id', 'tile.' + name) + '.name'}
    components.update(gameplay_components(profile, transparent))
    if profile['map_color']:
        components['minecraft:map_color'] = profile['map_color']
    tags = sorted(set(block_tags(profile)) | set(vanilla_tags_of(target['vanilla'], target['policy'])))
    if tags:
        components['minecraft:tags'] = tags
    for field, component in (('friction', 'minecraft:friction'), ('light_emission', 'minecraft:light_emission')):
        if field in profile:
            components[component] = profile[field]
    return components


def _swap_definition(target, components, permutations):
    """The block definition, checked against Mojang's block schema."""
    description = {'identifier': target['identifier'], 'states': target['states'],
                   'menu_category': {'category': 'none', 'is_hidden_in_commands': True}}
    definition = {'format_version': BLOCK_FORMAT, 'minecraft:block': {
        'description': description, 'components': components, 'permutations': permutations}}
    problems = block_errors(definition, Schemas(target['samples']))
    if problems:
        raise AuthoringError("the block fails Mojang's block schema: " + '; '.join(problems[:5]))
    return definition


def _swap_entry(target, fields):
    """The block's bct.js entry: the given fields, then the vanilla behaviour the engine keeps, such as open, xp and strip."""
    profile = target['profile']
    entry = {'block': target['identifier'], **fields}
    if target['transparent']:
        entry['open'] = True
    if profile.get('xp'):
        entry['xp'] = list(profile['xp'])
        needed = needed_tool(profile)
        if needed:
            entry['tool'] = {'all': needed[0], 'any': needed[1]}
    entry.update(_behavior_of(target['vanilla'], target['policy'], target['known']))
    return entry


def _swap_result(target, definition, entry, **extra):
    vanilla, samples = target['vanilla'], target['samples']
    sound = read_json(samples / 'resource_pack/blocks.json').get(vanilla.removeprefix('minecraft:'), {}).get('sound')
    return {'definition': definition, 'loot': loot_table(vanilla, target['profile']), 'sound': sound, 'entry': entry,
            **extra}


def _see_through_geometry(target, parts_of=None):
    """({path in the resource pack: file}, geometry component) for a see-through block, which hides its faces
    against itself (glass against glass); parts_of(identifier) gives (model, parts) for a geometry of its own."""
    pack, stem = target['pack'], target['name'].replace('/', '_')
    identifier = f'geometry.{pack}.{stem}'
    model, parts = parts_of(identifier) if parts_of else _replacement_geometry(identifier, list(FACES), [])
    culling = f'{pack}:{stem}_culling'
    files = {f'models/blocks/{pack}_{stem}.geo.json': model,
             f'block_culling/{pack}_{stem}.json': _culling_rules(culling, parts, target['transparent'])}
    return files, {'identifier': identifier, 'culling': culling}


def build_block(vanilla, identifier, pattern, *, samples, policy=None, root=ROOT, see_through='blend'):
    """The pattern copy of a vanilla block: {'definition', 'loot', 'sound', 'entry', 'textures', 'files'}.

    files: the geometry and culling rules a see-through block needs ({path in the resource pack: content});
    see_through: the render method of a see-through block's tiles.
    """
    target = _swap_target(vanilla, identifier, samples=samples, policy=policy or load_policy(), root=root,
                          kind='pattern')
    check_pattern(pattern)
    pack, name, mirror = target['pack'], target['name'], target['mirror']
    textures = [f'{pack}_{name.replace("/", "_")}_{tile}' for tile in range(tile_count(pattern))]
    method = see_through if target['transparent'] else 'opaque'
    pattern_places = places(pattern)
    for key in pattern_places[0][0]:
        target['states'][key] = _state_values(sorted({place[key] for place, _ in pattern_places}))
    permutations = []
    for place, tiles in pattern_places:
        condition = ' && '.join(f"q.block_state('{key}') == {value}" for key, value in place.items())
        instances = {face: {'texture': textures[tile], 'render_method': method} for face, tile in tiles.items()}
        instances['*'] = instances['north']
        permutations.append({'condition': condition, 'components': {'minecraft:material_instances': instances}})
    axis_state = mirror.get('pillar_axis')
    if axis_state:
        for axis, rotation in AXIS_ROTATION.items():
            if axis != 'y':
                permutations.append({'condition': f"q.block_state('{axis_state}') == '{axis}'",
                                     'components': {'minecraft:transformation': {'rotation': rotation}}})
    files, geometry = {}, 'minecraft:geometry.full_block'
    shape = target['shape']
    if shape:
        files, geometry, extra = _shaped_geometry(target, permutations)
    elif target['transparent']:
        files, geometry = _see_through_geometry(target)
    components = _swap_components(target, geometry, permutations[0]['components']['minecraft:material_instances'])
    if shape:
        components.update(extra)
    definition = _swap_definition(target, components, permutations)
    fields = {'pattern': pattern, **({'shape': shape} if shape else {})}
    return _swap_result(target, definition, _swap_entry(target, fields), textures=textures, files=files)


def _shaped_geometry(target, permutations):
    """(files, geometry component, extra components) of a slab: the parts its shape state shows, and their boxes.

    Adds a permutation per shape state value carrying its collision and selection boxes.
    """
    pack, stem = target['pack'], target['name'].replace('/', '_')
    identifier = f'geometry.{pack}.{stem}'
    try:
        built = shaped_block(identifier, target['shape'], target['mirror'])
    except ValueError as error:
        raise AuthoringError(str(error)) from None
    culling = f'{pack}:{stem}_culling'
    files = {f'models/blocks/{pack}_{stem}.geo.json': built['geometry'],
             f'block_culling/{pack}_{stem}.json': _culling_rules(culling, built['parts'], target['transparent'])}
    permutations += built['permutations']
    extra = {**built['boxes'], **shape_components(target['shape'], target['vanilla'])}
    return files, {'identifier': identifier, 'culling': culling, 'bone_visibility': built['visibility']}, extra


def cut_grid(image_path, pattern, texture_folder, textures):
    """Cuts a repeat's grid image (width x height tiles, row by row) into one file per tile; returns the files."""
    width, height = pattern['repeat'] if 'repeat' in pattern else (len(pattern['random']), 1)
    with Image.open(image_path) as image:
        if image.width % width or image.height % height or image.width // width != image.height // height:
            raise AuthoringError(f'the grid image must be {width} x {height} square tiles')
        size = image.width // width
        files = []
        for index, texture in enumerate(textures):
            column, row = index % width, index // width
            path = Path(texture_folder) / (texture + '.png')
            path.parent.mkdir(parents=True, exist_ok=True)
            image.crop((column * size, row * size, (column + 1) * size, (row + 1) * size)).save(path)
            files.append(path)
    return files


def write_block(bp, rp, vanilla, identifier, pattern, *, samples, grid=None, texture_dir='textures/blocks'):
    """Writes the block into the author's packs; returns (bct.js entry, texture files still missing)."""
    bp, rp = Path(bp), Path(rp)
    built = build_block(vanilla, identifier, pattern, samples=samples)
    textures = built['textures']
    if grid:
        cut_grid(grid, pattern, rp / texture_dir, textures)
    present = [path for texture in textures for path in [rp / texture_dir / (texture + '.png')] if path.exists()]
    if built['files'] and present:
        built = build_block(vanilla, identifier, pattern, samples=samples, see_through=see_through_method(present))
    definition, entry = built['definition'], built['entry']
    pack, name = identifier.split(':', 1)
    write_json(bp / f'blocks/{name}.json', definition)
    write_json(bp / definition['minecraft:block']['components']['minecraft:loot'], built['loot'])
    for path, content in built['files'].items():
        write_json(rp / path, content)
    sound = built['sound']
    atlas_path = rp / 'textures/terrain_texture.json'
    atlas = read_json(atlas_path) if atlas_path.exists() else {
        'resource_pack_name': pack, 'texture_name': 'atlas.terrain', 'padding': 8, 'num_mip_levels': 4}
    atlas.setdefault('texture_data', {}).update(
        {texture: {'textures': f'{texture_dir}/{texture}'} for texture in textures})
    write_json(atlas_path, atlas)
    if sound:
        blocks_path = rp / 'blocks.json'
        blocks = read_json(blocks_path) if blocks_path.exists() else {'format_version': BLOCKS_FORMAT}
        blocks[identifier] = {'sound': sound}
        write_json(blocks_path, blocks)
    missing = [f'{texture_dir}/{texture}.png' for texture in textures
               if not any((rp / texture_dir / (texture + extension)).exists() for extension in ('.png', '.tga'))]
    return entry, missing


def _read_data(script, pack):
    """scripts/bct.js as data (a new one when missing); None when it is not plain `export default {json}`."""
    script = Path(script)
    if not script.exists():
        return {'format': 1, 'pack': pack}
    text = script.read_text(encoding='utf-8').strip()
    found = re.fullmatch(r'export default (\{.*\});?', text, re.S)
    try:
        return json.loads(found.group(1)) if found else None
    except json.JSONDecodeError:
        return None


def _write_data(script, data):
    script = Path(script)
    text = json.dumps(data, indent=2)
    # Lists of plain values on one line, as people write them: "repeat": [3, 3].
    text = re.sub(r'\[[^\[\]{}]*\]', lambda found: json.dumps(json.loads(found.group(0))), text)
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text('export default ' + text + ';\n', encoding='utf-8')


def add_to_data(script, vanilla, entry, pack, section='blocks'):
    """Adds the entry to a section of scripts/bct.js (creating it); False when the file is not plain `export default {json}`."""
    data = _read_data(script, pack)
    if data is None:
        return False
    data.setdefault(section, {})[vanilla] = entry
    _write_data(script, data)
    return True


# Stamped on the scripts bct.py copies into a pack (see LICENSE-EXCEPTION.md).
SCRIPT_NOTICE = ('// Written by Bedrock Connected Textures (bct.py). Converter Output Exception\n'
                 '// (GPL-3.0 section 7): this file may be distributed under any terms.\n')
MAIN_SCRIPT = SCRIPT_NOTICE + ("import { system, world } from '@minecraft/server';\n"
                               "import data from './bct.js';\n"
                               "import overlaySurfaces from './bct-overlays.js';\n"
                               "import carrierSurfaces from './bct-carriers.js';\n"
                               "import { authoredSource, publishSources } from './publisher.js';\n"
                               "publishSources({ system, world, sources: [authoredSource({ ...data, overlaySurfaces, carrierSurfaces })] });\n")
# main.js as earlier 1.1 builds wrote it; init replaces these.
EARLIER_MAIN_SCRIPTS = (
    SCRIPT_NOTICE + ("import { system, world } from '@minecraft/server';\n"
                     "import data from './bct.js';\n"
                     "import { authoredSource, publishSources } from './publisher.js';\n"
                     "publishSources({ system, world, sources: [authoredSource(data)] });\n"),
    SCRIPT_NOTICE + ("import { system, world } from '@minecraft/server';\n"
                     "import data from './bct.js';\n"
                     "import overlaySurfaces from './bct-overlays.js';\n"
                     "import { authoredSource, publishSources } from './publisher.js';\n"
                     "publishSources({ system, world, sources: [authoredSource({ ...data, overlaySurfaces })] });\n"))
SERVER_MODULE = {'module_name': '@minecraft/server', 'version': '2.10.0'}


def _manifest(path, name, module_type):
    """The pack's manifest, or a new one with fresh ids."""
    if path.exists():
        return read_json(path)
    return {'format_version': 2,
            'header': {'name': name, 'description': '', 'uuid': str(uuid.uuid4()), 'version': [1, 0, 0],
                       'min_engine_version': list(ENGINE['min_engine_version'])},
            'modules': [{'type': module_type, 'uuid': str(uuid.uuid4()), 'version': [1, 0, 0]}]}


def init_pack(bp, rp, pack):
    """Makes a behavior pack send its BCT data to the engine; creates the packs when they do not exist yet.

    Adds the script module and the dependencies (script API, its resource pack,
    the BCT engine) to the behavior pack's manifest, copies main.js and
    publisher.js, and starts scripts/bct.js. A pack that runs a script of its
    own keeps it: main.js is not written, and the script has to import the BCT
    files itself (docs/AUTHORING.md). Safe to run again: it refreshes
    publisher.js after a BCT update. Returns (files written, notes for the author).
    """
    if not PACK_ID.fullmatch(pack) or pack == 'minecraft':
        raise AuthoringError('the pack id is letters, digits, - and _, such as mypack')
    bp, rp = Path(bp), Path(rp)
    scripts = bp / 'scripts'
    main = scripts / 'main.js'
    own_script = main.exists() and main.read_text(encoding='utf-8') not in (MAIN_SCRIPT, *EARLIER_MAIN_SCRIPTS)
    rp_manifest = _manifest(rp / 'manifest.json', pack + ' resources', 'resources')
    bp_manifest = _manifest(bp / 'manifest.json', pack + ' behaviors', 'data')
    if not any(module.get('type') == 'script' for module in bp_manifest['modules']):
        bp_manifest['modules'].append({'type': 'script', 'language': 'javascript', 'entry': 'scripts/main.js',
                                       'uuid': str(uuid.uuid4()), 'version': [1, 0, 0]})
    elif not any(module.get('entry') == 'scripts/main.js' for module in bp_manifest['modules']):
        own_script = True
    wanted = [SERVER_MODULE, {'uuid': rp_manifest['header']['uuid'], 'version': rp_manifest['header']['version']},
              engine_dependency()]
    dependencies = [item for item in bp_manifest.get('dependencies', [])
                    if item.get('module_name') != SERVER_MODULE['module_name']
                    and item.get('uuid') not in {entry.get('uuid') for entry in wanted}]
    bp_manifest['dependencies'] = dependencies + wanted
    write_json(rp / 'manifest.json', rp_manifest)
    write_json(bp / 'manifest.json', bp_manifest)
    scripts.mkdir(parents=True, exist_ok=True)
    written, notes = [bp / 'manifest.json', rp / 'manifest.json'], []
    if own_script:
        notes.append('your pack runs a script of its own, so main.js was not written: import ./bct.js, '
                     './bct-overlays.js, ./bct-carriers.js and ./publisher.js from it as docs/AUTHORING.md shows')
    else:
        main.write_text(MAIN_SCRIPT, encoding='utf-8')
        written.append(main)
    (scripts / 'publisher.js').write_text(SCRIPT_NOTICE + (ROOT / 'engine/publisher.mjs').read_text(encoding='utf-8'),
                                          encoding='utf-8')
    written.append(scripts / 'publisher.js')
    for generated, command in (('bct-overlays.js', 'overlays'), ('bct-carriers.js', 'carriers')):
        path = scripts / generated
        if not path.exists():
            path.write_text(SCRIPT_NOTICE + f'// Written by `python bct.py {command}`.\nexport default null;\n',
                            encoding='utf-8')
            written.append(path)
    data = scripts / 'bct.js'
    if not data.exists():
        data.write_text('export default ' + json.dumps({'format': 1, 'pack': pack, 'blocks': {}}, indent=2) + ';\n',
                        encoding='utf-8')
        written.append(data)
    return written, notes


LEAF_STATES = {'bct:persistent_bit': {'values': {'min': 0, 'max': 1}}, 'bct:update_bit': {'values': {'min': 0, 'max': 1}}}
VANILLA_LEAVES = ROOT / 'converter/data/vanilla-leaves.json'
# Java's log distance: near models up to this distance by default (the engine's NEAR_UP_TO).
NEAR_UP_TO = 3


def parse_models(texts, weights, where):
    """[(geometry id, y rotation, weight)] from `geometry.id` or `geometry.id@90` texts and their weights."""
    if weights is None:
        weights = [1] * len(texts)
    if len(weights) != len(texts):
        raise AuthoringError(f'{where}: give one weight per model ({len(texts)} models, {len(weights)} weights)')
    if not 1 <= len(texts) <= 16 or any(weight < 0 for weight in weights) or not any(weights):
        raise AuthoringError(f'{where}: 1 to 16 models, with weights of 0 or more and at least one above 0')
    models = []
    for text, weight in zip(texts, weights):
        geometry, _, turn = text.partition('@')
        if not re.fullmatch(r'geometry\.[A-Za-z0-9_.-]+', geometry):
            raise AuthoringError(f'{where}: {geometry} is not a geometry id such as geometry.mypack.oak_leaves')
        if turn and turn not in ('0', '90', '180', '270'):
            raise AuthoringError(f'{where}: {text} turns by 0, 90, 180 or 270 degrees')
        models.append((geometry, int(turn or 0), weight))
    return models


def build_leaves(vanilla, identifier, near, far=None, *, near_up_to=NEAR_UP_TO, texture=None, samples, policy=None):
    """The leaf block for a vanilla leaf: {'definition', 'loot', 'sound', 'entry', 'texture'}.

    near / far: [(geometry id, y rotation, weight)] (parse_models); far models
    show from log distance near_up_to + 1 on; without models the block is a
    plain cube until the author adds theirs. texture: the leaf texture's path
    in the resource pack (textures/blocks/<pack>_<name> by default).
    """
    policy = policy or load_policy()
    if not BLOCK_ID.fullmatch(identifier):
        raise AuthoringError('the block id must look like mypack:oak_leaves')
    pack, name = identifier.split(':', 1)
    if not PACK_ID.fullmatch(pack) or pack == 'minecraft':
        raise AuthoringError("the block id's namespace must be your pack id, not minecraft")
    table = read_json(VANILLA_LEAVES)
    if vanilla not in table:
        raise AuthoringError(vanilla + ' is not a vanilla leaf block')
    if far and not 1 <= near_up_to <= 5:
        raise AuthoringError('--near-up-to is a log distance from 1 to 5')
    samples = Path(samples)
    settings = next(entry for entry in policy['model_blocks'] if entry['behavior'] == 'leaves')
    leaf = table[vanilla]
    looks = [near or [('minecraft:geometry.full_block', 0, 1)]] + ([far] if far else [])
    choices = max(len(look) for look in looks)
    states = dict(LEAF_STATES)
    if choices > 1:
        states['bct:t'] = _state_values(range(choices))
    if far:
        states['bct:look'] = _state_values(range(2))
    permutations = []
    for look_index, look in enumerate(looks):
        for turn, (geometry, rotation, _) in enumerate(look):
            terms = ([f"q.block_state('bct:look') == {look_index}"] if far else []) + (
                [f"q.block_state('bct:t') == {turn}"] if choices > 1 else [])
            components = {'minecraft:geometry': geometry}
            if rotation:
                components['minecraft:transformation'] = {'rotation': [0, rotation, 0]}
            permutations.append({'condition': ' && '.join(terms) or '1.0', 'components': components})
    texture_name = f'{pack}_{name.replace("/", "_")}'
    instance = {'texture': texture_name, 'render_method': 'alpha_test', 'ambient_occlusion': 1.0, 'face_dimming': True}
    if leaf['tint']:
        instance['tint_method'] = leaf['tint']
    map_color = settings.get('map_colors', {}).get(vanilla, settings['map_color'])
    known = {item['name']: item for item in
             read_json(samples / 'metadata/vanilladata_modules/mojang-blocks.json')['data_items']}
    loot_path = f'loot_tables/{pack}/{name}.json'
    components = {
        'minecraft:geometry': looks[0][0][0],
        'minecraft:material_instances': {'*': instance},
        'minecraft:collision_box': True, 'minecraft:selection_box': True,
        'minecraft:light_dampening': settings['light_dampening'],
        'minecraft:destructible_by_mining': mining(settings, settings.get('item_speeds', [])),
        'minecraft:destructible_by_explosion': {'explosion_resistance': settings['resistance']},
        'minecraft:flammable': {'catch_chance_modifier': settings['flammable'][0],
                                'destroy_chance_modifier': settings['flammable'][1],
                                **({'lava_flammable': 'always'} if settings.get('ignited_by_lava') else {})},
        'minecraft:map_color': {'color': map_color, 'tint_method': leaf['tint']} if leaf['tint'] else map_color,
        'minecraft:loot': loot_path,
        'minecraft:tags': sorted(settings['tags']),
        'minecraft:display_name': known[vanilla].get('serialization_id', 'tile.' + name) + '.name',
        'minecraft:destruction_particles': {'texture': texture_name, 'tint_method': leaf['tint'] or 'none'},
        'minecraft:liquid_detection': {'detection_rules': [{'liquid_type': 'water', 'can_contain_liquid': True,
                                                            'on_liquid_touches': 'blocking'}]},
        'minecraft:precipitation_interactions': {'precipitation_behavior': 'obstruct_rain_accumulate_snow'},
        'bct:leaf': {}}
    description = {'identifier': identifier, 'menu_category': {'category': 'none', 'is_hidden_in_commands': True},
                   'states': states}
    definition = {'format_version': BLOCK_FORMAT, 'minecraft:block': {
        'description': description, 'components': components, 'permutations': permutations}}
    problems = block_errors(definition, Schemas(samples))
    if problems:
        raise AuthoringError("the block fails Mojang's block schema: " + '; '.join(problems[:5]))
    entry = {'block': identifier}
    weights = [[weight for _, _, weight in look] for look in looks]
    if far:
        entry['models'] = {'near': weights[0], 'far': weights[1]}
        if near_up_to != NEAR_UP_TO:
            entry['models']['nearUpTo'] = near_up_to
    elif len(weights[0]) > 1:
        entry['models'] = weights[0]
    sound = read_json(samples / 'resource_pack/blocks.json').get(vanilla.removeprefix('minecraft:'), {}).get('sound')
    return {'definition': definition, 'loot': leaf['loot'], 'sound': sound, 'entry': entry,
            'texture': (texture_name, texture or f'textures/blocks/{texture_name}')}


def geometry_ids(rp):
    """The geometry identifiers the resource pack's models define."""
    found = set()
    for path in Path(rp).glob('models/**/*.json'):
        try:
            document = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        for item in document.get('minecraft:geometry', []) if isinstance(document, dict) else []:
            identifier = item.get('description', {}).get('identifier') if isinstance(item, dict) else None
            if identifier:
                found.add(identifier)
    return found


def write_leaves(bp, rp, vanilla, identifier, near, far=None, *, near_up_to=NEAR_UP_TO, texture=None, samples):
    """Writes the leaf block into the author's packs; returns (bct.js entry, what the author still has to add)."""
    bp, rp = Path(bp), Path(rp)
    built = build_leaves(vanilla, identifier, near, far, near_up_to=near_up_to, texture=texture, samples=samples)
    pack, name = identifier.split(':', 1)
    write_json(bp / f'blocks/{name}.json', built['definition'])
    write_json(bp / built['definition']['minecraft:block']['components']['minecraft:loot'], built['loot'])
    texture_name, texture_path = built['texture']
    atlas_path = rp / 'textures/terrain_texture.json'
    atlas = read_json(atlas_path) if atlas_path.exists() else {
        'resource_pack_name': pack, 'texture_name': 'atlas.terrain', 'padding': 8, 'num_mip_levels': 4}
    atlas.setdefault('texture_data', {})[texture_name] = {'textures': texture_path}
    write_json(atlas_path, atlas)
    if built['sound']:
        blocks_path = rp / 'blocks.json'
        blocks = read_json(blocks_path) if blocks_path.exists() else {'format_version': BLOCKS_FORMAT}
        blocks[identifier] = {'sound': built['sound']}
        write_json(blocks_path, blocks)
    todo = []
    if not any((rp / (texture_path + extension)).exists() for extension in ('.png', '.tga')):
        todo.append(f'the leaf texture {texture_path}.png')
    defined = geometry_ids(rp)
    for geometry in sorted({model[0] for look in (near or [], far or []) for model in look} - defined):
        todo.append(f'the model {geometry} (in models/blocks/*.geo.json)')
    if not near:
        todo.append('your leaf models: run again with --near (the block is a plain cube until then)')
    return built['entry'], todo


# Edge blocks: the engine's terrain data takes only these ids.
EDGE_BLOCK_ID = re.compile(r'^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$')
EDGE_TINTS = {'none': 'none', 'grass': 'grass', 'foliage': 'default_foliage'}


def _texture_file(rp, texture):
    """The .png or .tga file of a texture path in the resource pack (textures/blocks/grass_top)."""
    for extension in ('.png', '.tga'):
        path = Path(rp) / (texture + extension)
        if path.exists():
            return path
    raise AuthoringError(f'{texture}.png is not in the resource pack')


def edge_shapes(rp, texture=None, edge_texture=None, corner_texture=None):
    """{'edge': image, 'corner': image}: cut from the texture's own brightness, or the author's own cut tiles.

    A cut edge hangs from the top side of the tile; its corner is where it
    overlaps itself turned onto the east side (as converted packs cut them).
    """
    if edge_texture or corner_texture:
        if not (edge_texture and corner_texture):
            raise AuthoringError('give both --edge-texture and --corner-texture, or neither')
        shapes = {}
        for shape, path in (('edge', edge_texture), ('corner', corner_texture)):
            with Image.open(_texture_file(rp, path)) as image:
                shapes[shape] = image.convert('RGBA')
        return shapes, None
    if not texture:
        raise AuthoringError('give --texture (cut by its own brightness) or --edge-texture and --corner-texture')
    with Image.open(_texture_file(rp, texture)) as image:
        color = image.convert('RGBA')
    edge = edge_alpha_from_image(color).point(lambda value: 255 if value >= 128 else 0)
    corner = Image.fromarray(np.minimum(np.asarray(edge), np.asarray(edge.rotate(-90))))
    shapes = {}
    for shape, alpha in (('edge', edge), ('corner', corner)):
        tile = color.copy()
        tile.putalpha(alpha.resize(color.size, Image.Resampling.NEAREST))
        shapes[shape] = tile
    return shapes, texture


def build_edge(identifier, source, onto, *, rp, texture=None, edge_texture=None, corner_texture=None, tint='none',
               samples):
    """The edge block of a ground block spreading onto others: {'definition', 'geometry', 'images', 'texture_sets',
    'sound', 'entry'}.

    The block is see-through and walk-through, sits in the air above each block
    the edge spreads onto, and draws a thin plane per side (bct:edges) and per
    corner (bct:corners), like the edges of converted packs.
    """
    if not EDGE_BLOCK_ID.fullmatch(identifier):
        raise AuthoringError('the edge block id is letters, digits and _ only, such as mypack:grass_edge')
    pack, name = identifier.split(':', 1)
    if pack == 'minecraft':
        raise AuthoringError("the block id's namespace must be your pack id, not minecraft")
    if tint not in EDGE_TINTS:
        raise AuthoringError('--tint is none, grass or foliage')
    samples = Path(samples)
    known = {item['name'] for item in
             read_json(samples / 'metadata/vanilladata_modules/mojang-blocks.json')['data_items']}
    for block in [*source, *onto]:
        if block not in known:
            raise AuthoringError(block + ' is not a Bedrock block id')
    shapes, cut_from = edge_shapes(rp, texture, edge_texture, corner_texture)
    stem = identifier.replace(':', '_')
    effect = {'native_block': identifier, 'biome_tint': EDGE_TINTS[tint], 'layer': 2, 'ambient_occlusion': 1.0}
    bones, visibility = _material_planes(effect, shapes, False)
    geometry_id = 'geometry.' + stem
    # No placement filter: the engine places and removes edges itself, and a filter naming the vanilla blocks
    # would drop an edge resting on a pack's own pattern block that stands for one of them.
    definition = _material_block(None, effect, stem, geometry_id, shapes, visibility, set())
    definition['minecraft:block']['components']['minecraft:loot'] = f'loot_tables/{pack}/empty.json'
    problems = block_errors(definition, Schemas(samples))
    if problems:
        raise AuthoringError("the block fails Mojang's block schema: " + '; '.join(problems[:5]))
    geometry = {'format_version': '1.21.0', 'minecraft:geometry': [_geometry_model(geometry_id, bones)]}
    # The PBR maps of the cut texture go with each cut tile unchanged; only the colour map is cut.
    texture_sets = {}
    if cut_from:
        set_path = Path(rp) / (cut_from + '.texture_set.json')
        if set_path.exists():
            texture_sets = read_json(set_path)['minecraft:texture_set']
    sounds = read_json(samples / 'resource_pack/blocks.json')
    sound = sounds.get(source[0].removeprefix('minecraft:'), {}).get('sound', 'grass')
    entry = {'from': source if len(source) > 1 else source[0], 'onto': list(onto), 'block': identifier}
    return {'definition': definition, 'geometry': geometry, 'images': shapes, 'texture_sets': texture_sets,
            'stem': stem, 'sound': sound, 'entry': entry, 'cut_from': cut_from}


def write_edge(bp, rp, identifier, source, onto, *, texture=None, edge_texture=None, corner_texture=None, tint='none',
               samples):
    """Writes the edge block into the author's packs; returns its bct.js entry."""
    bp, rp = Path(bp), Path(rp)
    built = build_edge(identifier, source, onto, rp=rp, texture=texture, edge_texture=edge_texture,
                       corner_texture=corner_texture, tint=tint, samples=samples)
    pack, name = identifier.split(':', 1)
    stem = built['stem']
    write_json(bp / f'blocks/{name}.json', built['definition'])
    write_json(bp / f'loot_tables/{pack}/empty.json', {'pools': []})
    write_json(rp / f'models/blocks/{stem}.geo.json', built['geometry'])
    texture_dir = rp / 'textures/blocks'
    texture_dir.mkdir(parents=True, exist_ok=True)
    atlas_path = rp / 'textures/terrain_texture.json'
    atlas = read_json(atlas_path) if atlas_path.exists() else {
        'resource_pack_name': pack, 'texture_name': 'atlas.terrain', 'padding': 8, 'num_mip_levels': 4}
    channels = {}
    if built['texture_sets']:
        source_dir = (rp / built['cut_from']).parent
        channels = _copy_other_maps(source_dir, built['texture_sets'], texture_dir, stem)
    for shape, image in built['images'].items():
        texture_name = f'{stem}_{shape}'
        image.save(texture_dir / (texture_name + '.png'))
        if channels:
            write_json(texture_dir / (texture_name + '.texture_set.json'),
                       {'format_version': '1.21.30', 'minecraft:texture_set': {'color': texture_name, **channels}})
        atlas.setdefault('texture_data', {})[texture_name] = {'textures': f'textures/blocks/{texture_name}'}
    write_json(atlas_path, atlas)
    blocks_path = rp / 'blocks.json'
    blocks = read_json(blocks_path) if blocks_path.exists() else {'format_version': BLOCKS_FORMAT}
    blocks[identifier] = {'sound': built['sound']}
    write_json(blocks_path, blocks)
    return built['entry']


def add_edge_to_data(script, entry, pack):
    """Adds an edge to the end of the edges list of scripts/bct.js (replacing one with the same block).

    False when the file is not plain `export default {json}`.
    """
    script = Path(script)
    data = _read_data(script, pack)
    if data is None:
        return False
    edges = [edge for edge in data.get('edges', []) if edge.get('block') != entry['block']]
    data['edges'] = edges + [entry]
    _write_data(script, data)
    return True


# Overlays: the 17-tile overlay template. Edges each tile covers (texture space: left 1, down 2, right 4,
# up 8), or the corner it covers (left+down 0, down+right 1, right+up 2, up+left 3).
OVERLAY_TILE_EDGES = {9: 1, 1: 2, 4: 3, 7: 4, 3: 6, 5: 7, 15: 8, 11: 9, 6: 11, 10: 12, 13: 13, 12: 14, 8: 15}
OVERLAY_TILE_CORNERS = {2: 0, 0: 1, 14: 2, 16: 3}
OVERLAY_TILES = 17
OVERLAY_FACES = ('up', 'north', 'south', 'west', 'east', 'down')
# Tints an overlay can take, by the vanilla block whose biome colour it follows.
OVERLAY_TINT_BLOCKS = {'grass': 'minecraft:grass_block', 'foliage': 'minecraft:oak_leaves'}
OVERLAYS_SCRIPT = 'scripts/bct-overlays.js'


def overlay_tile_images(rp, texture):
    """The 17 overlay tiles cut from a ground texture with the edge shape its brightness gives.

    Each tile keeps the texture where its edges (or its corner) lie: an edge
    hangs from the top side of the edge shape, turned to the tile's sides.
    """
    with Image.open(_texture_file(rp, texture)) as image:
        color = image.convert('RGBA')
    top = edge_alpha_from_image(color).point(lambda value: 255 if value >= 128 else 0)
    # Turned so the shape hangs from the left, bottom, right and top sides (PIL turns counter-clockwise).
    sides = [np.asarray(top.rotate(angle)) for angle in (90, 180, -90, 0)]
    corners = [np.minimum(sides[corner], sides[(corner + 1) % 4]) for corner in range(4)]
    tiles = []
    for tile in range(OVERLAY_TILES):
        if tile in OVERLAY_TILE_CORNERS:
            mask = corners[OVERLAY_TILE_CORNERS[tile]]
        else:
            edges = OVERLAY_TILE_EDGES[tile]
            mask = np.zeros_like(sides[0])
            for side in range(4):
                if edges & (1 << side):
                    mask = np.maximum(mask, sides[side])
        piece = color.copy()
        piece.putalpha(Image.fromarray(mask).resize(color.size, Image.Resampling.NEAREST))
        tiles.append(piece)
    return tiles


def overlay_digest(overlays):
    """The engine's checksum (FNV-1a) of the overlays section, so it can tell when the blocks are out of date."""
    text = json.dumps(overlays, separators=(',', ':'), ensure_ascii=False)
    value = 2166136261
    for character in text:
        for unit in _utf16_units(character):
            value = ((value ^ unit) * 16777619) & 0xFFFFFFFF
    return f'{value:08x}'


def _utf16_units(character):
    code = ord(character)
    if code < 0x10000:
        return [code]
    code -= 0x10000
    return [0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)]


def overlay_rules(overlays, rp, known):
    """The converter's rules for the pack's overlays section; AuthoringError names a wrong entry."""
    if not isinstance(overlays, list):
        raise AuthoringError('overlays: must be a list')
    rules = []
    for index, entry in enumerate(overlays):
        where = f'overlays[{index}]'
        if not isinstance(entry, dict):
            raise AuthoringError(where + ': must be an object with tiles, onto and from')
        unknown = set(entry) - {'tiles', 'onto', 'from', 'faces', 'tint'}
        if unknown:
            raise AuthoringError(f'{where}.{sorted(unknown)[0]}: is not tiles, onto, from, faces or tint')
        tiles = entry.get('tiles')
        if not isinstance(tiles, str) or not tiles.startswith('textures/'):
            raise AuthoringError(where + '.tiles: must be the tile path without its number, such as '
                                 'textures/blocks/grass_overlay (tiles grass_overlay_0 to _16)')
        for tile in range(OVERLAY_TILES):
            _texture_file(rp, f'{tiles}_{tile}')
        lists = {}
        for field in ('onto', 'from'):
            value = entry.get(field)
            value = [value] if isinstance(value, str) else value
            if not isinstance(value, list) or not value or any(block not in known for block in value):
                raise AuthoringError(f'{where}.{field}: must be one or more Bedrock block ids')
            lists[field] = value
        faces = entry.get('faces', list(OVERLAY_FACES))
        if not isinstance(faces, list) or not faces or any(face not in OVERLAY_FACES for face in faces):
            raise AuthoringError(where + '.faces: must name faces from up, north, south, west, east and down')
        rule = {'id': f'o{index}', 'method': 'overlay', 'tiles': [f'{tiles}_{tile}' for tile in range(OVERLAY_TILES)],
                'blocks': lists['onto'], 'connectBlocks': lists['from'], 'faces': faces}
        tint = entry.get('tint', 'none')
        if tint not in ('none', *OVERLAY_TINT_BLOCKS):
            raise AuthoringError(where + '.tint: must be none, grass or foliage')
        if tint != 'none':
            rule.update(tintIndex=0, tintBlock=OVERLAY_TINT_BLOCKS[tint])
        rules.append(rule)
    return rules


def _generated_overlay_files(bp, rp, pack):
    """Files a previous `bct.py overlays` run wrote into the packs."""
    key = pack.replace('-', '_')
    return [*Path(bp).glob(f'blocks/bct_{key}_overlay_*.json'), *Path(rp).glob(f'models/blocks/bct_{key}_overlay_*.geo.json'),
            *Path(rp).glob(f'textures/blocks/bct_ov_{key}_*')]


def build_overlays(bp, rp, *, samples, root=ROOT):
    """Writes the surface blocks for the overlays section of the pack's scripts/bct.js, and the engine's data.

    Returns the report of the converter's overlay builder ({'rules_drawn', 'rules_not_drawn', 'types', ...}).
    """
    import shutil
    import tempfile
    import overlay_surfaces
    bp, rp = Path(bp), Path(rp)
    data = _read_data(bp / 'scripts/bct.js', None)
    if data is None or not data.get('pack'):
        raise AuthoringError('scripts/bct.js must be plain JSON after `export default`, with its pack id, '
                             'for bct.py to read its overlays')
    pack, overlays = data['pack'], data.get('overlays', [])
    samples = Path(samples)
    known = {item['name']: item for item in
             read_json(samples / 'metadata/vanilladata_modules/mojang-blocks.json')['data_items']}
    rules = overlay_rules(overlays, rp, known)
    for path in _generated_overlay_files(bp, rp, pack):
        path.unlink()
    key = pack.replace('-', '_')
    atlas_path, blocks_path = rp / 'textures/terrain_texture.json', rp / 'blocks.json'
    atlas = read_json(atlas_path) if atlas_path.exists() else {
        'resource_pack_name': pack, 'texture_name': 'atlas.terrain', 'padding': 8, 'num_mip_levels': 4}
    atlas['texture_data'] = {name: value for name, value in atlas.get('texture_data', {}).items()
                             if not name.startswith(f'bct_ov_{key}_')}
    blocks = read_json(blocks_path) if blocks_path.exists() else {'format_version': BLOCKS_FORMAT}
    blocks = {name: value for name, value in blocks.items() if not name.startswith(f'bct_{key}:overlay_')}
    engine = None
    report = {'rules_drawn': [], 'rules_not_drawn': [], 'types': []}
    if rules:
        document = {'dialect': 'optifine', 'rules': rules, 'fullCubeBlocks': sorted(vanilla_cubes(root)),
                    'modelTintTypes': {'minecraft:grass_block': 'grass', 'minecraft:oak_leaves': 'foliage'}}
        # The pack's pattern blocks show the overlays of the vanilla blocks they stand for.
        replacement = {'blocks': [{'vanilla': vanilla, 'block': entry['block']}
                                  for vanilla, entry in data.get('blocks', {}).items() if isinstance(entry, dict)]}
        with tempfile.TemporaryDirectory(prefix='bct-overlays-') as folder:
            output = Path(folder) / 'out'
            engine, report = overlay_surfaces.build(document, rp, output, key=pack, samples=samples,
                                                    replacement_data=replacement)
            if engine:
                built_bp, built_rp = output / 'Overlay_BP', output / 'Overlay_RP'
                for source in [*built_bp.glob('blocks/*.json'), *built_bp.glob('loot_tables/**/*.json')]:
                    target = bp / source.relative_to(built_bp)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                for source in [*built_rp.glob('models/blocks/*.json'), *built_rp.glob('textures/blocks/*')]:
                    target = rp / source.relative_to(built_rp)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                atlas['texture_data'].update(read_json(built_rp / 'textures/terrain_texture.json')['texture_data'])
                blocks.update({name: value for name, value in read_json(built_rp / 'blocks.json').items()
                               if name != 'format_version'})
                if (built_rp / 'textures/flipbook_textures.json').exists():
                    raise AuthoringError('animated overlay tiles are not supported yet')
    write_json(atlas_path, atlas)
    write_json(blocks_path, blocks)
    surfaces = {'digest': overlay_digest(overlays), 'data': engine} if engine else None
    script = bp / OVERLAYS_SCRIPT
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(SCRIPT_NOTICE + '// Written by `python bct.py overlays` from the overlays in bct.js: '
                      'run it again after changing them.\nexport default '
                      + json.dumps(surfaces, separators=(',', ':')) + ';\n', encoding='utf-8')
    return report


def write_overlay_tiles(rp, texture, tiles):
    """Saves the 17 overlay tiles cut from a ground texture as <tiles>_0.png to _16.png; returns their paths."""
    if not isinstance(tiles, str) or not tiles.startswith('textures/'):
        raise AuthoringError('--tiles is the tile path without its number, such as textures/blocks/grass_overlay')
    paths = []
    for index, image in enumerate(overlay_tile_images(rp, texture)):
        path = Path(rp) / f'{tiles}_{index}.png'
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
        paths.append(path)
    return paths


# Connected blocks: each face is four quarters, and each quarter shows the quarter of one of four tiles:
# alone, joined along its texture-left/right edge, joined along its up/down edge, or joined along both.
CONNECT_STATES = {'north': 'bct:n', 'south': 'bct:s', 'west': 'bct:w', 'east': 'bct:e', 'up': 'bct:u', 'down': 'bct:d'}
# The neighbours on each face's texture up, right, down and left edges (engine/tile-template.mjs order).
TEXTURE_EDGES = {'north': ['up', 'west', 'down', 'east'], 'east': ['up', 'north', 'down', 'south'],
                 'south': ['up', 'east', 'down', 'west'], 'west': ['up', 'south', 'down', 'north'],
                 'up': ['north', 'east', 'south', 'west'], 'down': ['south', 'east', 'north', 'west']}
# The tiles of the standard 47-tile set a connected block uses: alone, joined left and right, joined up and
# down, joined all round (tile-template.mjs masks 0, 10, 5 and 255).
CONNECTED_TILES = {'alone': 0, 'across': 2, 'along': 24, 'joined': 26}
JOINS = ('all', 'horizontal', 'vertical')
# Java element faces at the block's boundary: the axis each face lies across and where.
FACE_PLANES = {'north': (2, 0), 'south': (2, 16), 'west': (0, 0), 'east': (0, 16), 'down': (1, 0), 'up': (1, 16)}


def _quarter_elements():
    """[(face, (qu, qv), Java element)]: four flat elements per face, one per quarter of its texture."""
    quarters = []
    for face, (axis, at) in FACE_PLANES.items():
        others = [other for other in range(3) if other != axis]
        for first in (0, 8):
            for second in (0, 8):
                low, high = [0, 0, 0], [0, 0, 0]
                low[axis] = high[axis] = at
                low[others[0]], high[others[0]] = first, first + 8
                low[others[1]], high[others[1]] = second, second + 8
                u0, v0, _, _ = default_uv(face, low, high)
                element = {'from': low, 'to': high, 'faces': {face: {'cullface': face}}}
                quarters.append((face, (int(u0) // 8, int(v0) // 8), element))
    return quarters


def _quarter_tile(face, quarter, joined, faces, joins):
    """Which of the four tiles a quarter shows, given the faces whose neighbours are joined."""
    if face not in faces:
        return 'alone'
    qu, qv = quarter
    edges = TEXTURE_EDGES[face]
    across = joins != 'vertical' and edges[3 if qu == 0 else 1] in joined
    along = joins != 'horizontal' and edges[0 if qv == 0 else 2] in joined
    return {(False, False): 'alone', (True, False): 'across', (False, True): 'along', (True, True): 'joined'}[(across, along)]


def build_connected(vanilla, identifier, tiles, *, faces=None, joins='all', connect='same', textures=None, rp=None,
                    samples, policy=None, root=ROOT):
    """The connected copy of a vanilla block: {'definition', 'geometry', 'culling', 'loot', 'sound', 'entry', 'tiles'}.

    tiles: {'alone', 'across', 'along', 'joined'} texture paths in the resource pack; a list of paths is random
    variants, picked per block position. faces: the faces that join (default all six); joins: 'all', or
    'horizontal' / 'vertical' to join only along the texture's left and right, or up and down, edges
    (bookshelves: sides, horizontal). connect: 'same' or the blocks it joins. textures: {face: path or list}
    for faces that do not join (default the alone tile), such as a top and bottom of their own. The engine
    sets the six neighbour states; inner corners are not drawn.
    """
    target = _swap_target(vanilla, identifier, samples=samples, policy=policy or load_policy(), root=root,
                          kind='connected')
    faces = list(faces or OVERLAY_FACES)
    if any(face not in OVERLAY_FACES for face in faces):
        raise AuthoringError('--faces names faces from up, north, south, west, east and down')
    if joins not in JOINS:
        raise AuthoringError('--joins is all, horizontal or vertical')
    textures = dict(textures or {})
    if any(face not in OVERLAY_FACES for face in textures):
        raise AuthoringError('a face texture names a face from up, north, south, west, east and down')
    if any(face in faces for face in textures):
        raise AuthoringError('a face with its own texture does not join: leave it out of --faces')
    known = target['known']
    if connect != 'same' and (not isinstance(connect, list) or not connect or any(block not in known for block in connect)):
        raise AuthoringError('--connect is same or a list of Bedrock block ids')
    pack, name = target['pack'], target['name']
    stem = name.replace('/', '_')
    method = 'opaque'
    if target['transparent']:
        files = [_texture_file(rp, path) for path in _texture_paths([*tiles.values(), *textures.values()])] if rp else []
        method = see_through_method(files) if files else 'blend'
    aliases = {kind: f'{pack}_{stem}_{kind}' for kind in CONNECTED_TILES}
    aliases.update({face: f'{pack}_{stem}_{face}' for face in textures})
    quarters = _quarter_elements()
    names = [f'{face}_{quarter[0]}{quarter[1]}' for face, quarter, _ in quarters]
    bone = {'name': 'quarters', 'pivot': [0, 0, 0],
            'cubes': [cube(element, lambda face, data, name=name: name) for (_, _, element), name in zip(quarters, names)]}
    geometry_id = f'geometry.{pack}.{stem}'
    geometry = {'format_version': '1.21.0', 'minecraft:geometry': [{'description': {
        'identifier': geometry_id, 'texture_width': 16, 'texture_height': 16, 'visible_bounds_width': 2,
        'visible_bounds_height': 2, 'visible_bounds_offset': [0, 0.5, 0]}, 'bones': [bone]}]}
    culling_id = f'{pack}:{stem}_culling'
    culling = _culling_rules(culling_id, [('quarters', face, face, index) for index, (face, _, _) in enumerate(quarters)],
                             target['transparent'])
    for state in CONNECT_STATES.values():
        target['states'][state] = _state_values(range(2))
    sides = list(CONNECT_STATES)
    permutations = []
    for combination in range(64):
        joined = {side for bit, side in enumerate(sides) if combination & (1 << bit)}
        instances = {'*': {'texture': aliases['alone'], 'render_method': method}}
        for (face, quarter, _), instance in zip(quarters, names):
            kind = face if face in textures else _quarter_tile(face, quarter, joined, faces, joins)
            instances[instance] = {'texture': aliases[kind], 'render_method': method}
        condition = ' && '.join(f"q.block_state('{CONNECT_STATES[side]}') == {int(side in joined)}" for side in sides)
        permutations.append({'condition': condition, 'components': {'minecraft:material_instances': instances}})
    components = _swap_components(target, {'identifier': geometry_id, 'culling': culling_id},
                                  permutations[0]['components']['minecraft:material_instances'])
    definition = _swap_definition(target, components, permutations)
    entry = _swap_entry(target, {} if connect == 'same' else {'connect': list(connect)})
    return _swap_result(target, definition, entry, geometry=geometry, culling=culling,
                        tiles={aliases[kind]: path for kind, path in {**tiles, **textures}.items()}, stem=stem)


def _texture_paths(values):
    """Every path of these textures, counting each random variant."""
    return [path for value in values for path in (value if isinstance(value, list) else [value])]


def atlas_entry(value):
    """The terrain_texture.json entry of a texture path, or of a list of random variants."""
    if isinstance(value, list):
        return {'textures': {'variations': [{'path': path} for path in value]}}
    return {'textures': value}


def connected_tiles(ctm=None, alone=None, across=None, along=None, joined=None):
    """The four tile paths: picked from a 47-tile set (<ctm>_0 to _46), or given one by one.

    Several 47-tile sets, or several paths for each tile, are random variants of each tile.
    """
    given = {'alone': alone, 'across': across, 'along': along, 'joined': joined}
    if ctm:
        if any(given.values()):
            raise AuthoringError('give --ctm or the four tiles, not both')
        sets = ctm if isinstance(ctm, list) else [ctm]
        return {kind: _one_or_variants([f'{name}_{tile}' for name in sets]) for kind, tile in CONNECTED_TILES.items()}
    if not all(given.values()):
        raise AuthoringError('give --ctm (a 47-tile set) or all of --alone, --across, --along and --joined')
    return {kind: _one_or_variants(value if isinstance(value, list) else [value]) for kind, value in given.items()}


def _one_or_variants(paths):
    return paths[0] if len(paths) == 1 else list(paths)


def write_connected(bp, rp, vanilla, identifier, tiles, *, faces=None, joins='all', connect='same', textures=None,
                    samples):
    """Writes the connected block into the author's packs; returns its bct.js entry."""
    bp, rp = Path(bp), Path(rp)
    for path in _texture_paths([*tiles.values(), *(textures or {}).values()]):
        _texture_file(rp, path)
    built = build_connected(vanilla, identifier, tiles, faces=faces, joins=joins, connect=connect, textures=textures,
                            rp=rp, samples=samples)
    pack, name = identifier.split(':', 1)
    write_json(bp / f'blocks/{name}.json', built['definition'])
    write_json(bp / built['definition']['minecraft:block']['components']['minecraft:loot'], built['loot'])
    write_json(rp / f"models/blocks/{pack}_{built['stem']}.geo.json", built['geometry'])
    write_json(rp / f"block_culling/{pack}_{built['stem']}.json", built['culling'])
    atlas_path = rp / 'textures/terrain_texture.json'
    atlas = read_json(atlas_path) if atlas_path.exists() else {
        'resource_pack_name': pack, 'texture_name': 'atlas.terrain', 'padding': 8, 'num_mip_levels': 4}
    atlas.setdefault('texture_data', {}).update({alias: atlas_entry(path) for alias, path in built['tiles'].items()})
    write_json(atlas_path, atlas)
    if built['sound']:
        blocks_path = rp / 'blocks.json'
        blocks = read_json(blocks_path) if blocks_path.exists() else {'format_version': BLOCKS_FORMAT}
        blocks[identifier] = {'sound': built['sound']}
        write_json(blocks_path, blocks)
    return built['entry']


# Carriers: the full 47-tile connected look drawn by entities in front of each face (Classic and Vibrant
# Visuals; ray tracing shows the block's own faces). `python bct.py carriers` builds them with the converter.
CARRIERS_SCRIPT = 'scripts/bct-carriers.js'
# The files `bct.py carriers` wrote last time, so a rebuild removes them first (in the behavior pack's root).
CARRIER_FILES = 'bct-carriers-files.json'
CARRIER_TILES = 47
# Files of the carrier add-on that stay out of the author's packs: its own manifests, icons and scripts.
CARRIER_SKIPPED = ('manifest.json', 'pack_icon.png', 'carrier-atlas.json')
# Files the author's pack may have too, merged by key.
CARRIER_MERGED = ('materials/entity.material', 'blocks.json', 'textures/terrain_texture.json')


def carrier_rules(carriers, rp, known):
    """The converter's rules for the pack's carriers section; AuthoringError names a wrong entry."""
    if not isinstance(carriers, list):
        raise AuthoringError('carriers: must be a list')
    rules = []
    for index, entry in enumerate(carriers):
        where = f'carriers[{index}]'
        if not isinstance(entry, dict):
            raise AuthoringError(where + ': must be an object with tiles and blocks')
        unknown = set(entry) - {'tiles', 'blocks', 'faces'}
        if unknown:
            raise AuthoringError(f'{where}.{sorted(unknown)[0]}: is not tiles, blocks or faces')
        tiles = entry.get('tiles')
        if not isinstance(tiles, str) or not tiles.startswith('textures/'):
            raise AuthoringError(where + '.tiles: must be the tile path without its number, such as '
                                 'textures/blocks/glass_ctm (tiles glass_ctm_0 to _46)')
        for tile in range(CARRIER_TILES):
            _texture_file(rp, f'{tiles}_{tile}')
        blocks = entry.get('blocks')
        blocks = [blocks] if isinstance(blocks, str) else blocks
        if not isinstance(blocks, list) or not blocks or any(block not in known for block in blocks):
            raise AuthoringError(where + '.blocks: must be one or more Bedrock block ids')
        faces = entry.get('faces', list(OVERLAY_FACES))
        if not isinstance(faces, list) or not faces or any(face not in OVERLAY_FACES for face in faces):
            raise AuthoringError(where + '.faces: must name faces from up, north, south, west, east and down')
        rules.append({'id': f'c{index}', 'method': 'ctm', 'blocks': blocks, 'faces': faces, 'connect': 'block',
                      'tiles': [f'{tiles}_{tile}' for tile in range(CARRIER_TILES)]})
    return rules


def _merge_json(target, source):
    """Merges a generated JSON file into the pack's own (top-level keys, and their objects one level down)."""
    data = read_json(target) if target.exists() else {}
    for key, value in read_json(source).items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key] = {**data[key], **value}
        else:
            data[key] = value
    write_json(target, data)


def build_carriers(bp, rp, *, samples, root=ROOT):
    """Builds the carriers in the carriers section of scripts/bct.js into the packs; returns the files written."""
    import tempfile
    import zipfile
    from addon_package import read_packets
    import connected_build
    bp, rp = Path(bp), Path(rp)
    data = _read_data(bp / 'scripts/bct.js', None)
    if data is None or not data.get('pack'):
        raise AuthoringError('scripts/bct.js must be plain JSON after `export default`, with its pack id, '
                             'for bct.py to read its carriers')
    pack, carriers = data['pack'], data.get('carriers', [])
    samples = Path(samples)
    known = {item['name'] for item in
             read_json(samples / 'metadata/vanilladata_modules/mojang-blocks.json')['data_items']}
    rules = carrier_rules(carriers, rp, known)
    record = bp / CARRIER_FILES
    for relative in (read_json(record) if record.exists() else []):
        path = (bp if relative.startswith('BP/') else rp) / relative[3:]
        if path.is_file():
            path.unlink()
    written, configuration = [], None
    if rules:
        document = {'format_version': 1, 'fullCubeBlocks': sorted(vanilla_cubes(root)),
                    'opaqueBlocks': sorted(vanilla_solid(root)), 'rules': rules}
        key = re.sub('[^a-z0-9-]', '-', pack.lower())
        if not key[0].isalpha():
            key = 'p' + key
        with tempfile.TemporaryDirectory(prefix='bct-carriers-') as folder:
            folder = Path(folder)
            write_json(folder / 'rules.json', document)
            # The builder stages under its root and takes the pack icon from there.
            icon = folder / 'converter/data/artwork/pack-icon.png'
            icon.parent.mkdir(parents=True)
            icon.write_bytes((root / 'converter/data/artwork/pack-icon.png').read_bytes())
            archive = connected_build.build_connected(rp, folder / 'rules.json', key, root=folder, samples=samples,
                                                      artifact_directory=folder / 'dist')
            with zipfile.ZipFile(archive) as zipped:
                zipped.extractall(folder / 'addon')
            built_bp, built_rp = folder / 'addon/Connected_BP', folder / 'addon/Connected_RP'
            configuration = read_packets((built_bp / 'scripts/source-data.js').read_text(encoding='utf-8'))['connected']
            for side, built, target in (('BP', built_bp, bp), ('RP', built_rp, rp)):
                for source in sorted(path for path in built.rglob('*') if path.is_file()):
                    relative = source.relative_to(built).as_posix()
                    if relative in CARRIER_SKIPPED or relative.startswith('scripts/'):
                        continue
                    destination = target / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if relative in CARRIER_MERGED:
                        _merge_json(destination, source)
                        continue
                    if destination.exists() and not relative.startswith(('entities/bct_', 'entity/bct_', 'models/entity/bct_',
                                                                          'render_controllers/bct_', 'structures/bct/',
                                                                          'textures/entity/bct_')):
                        raise AuthoringError(f'{side} {relative} is your own file; the carriers would replace it')
                    destination.write_bytes(source.read_bytes())
                    written.append(f'{side}/{relative}')
    write_json(record, written)
    surfaces = {'digest': overlay_digest(carriers), 'data': configuration} if configuration else None
    script = bp / CARRIERS_SCRIPT
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(SCRIPT_NOTICE + '// Written by `python bct.py carriers` from the carriers in bct.js: '
                      'run it again after changing them.\nexport default '
                      + json.dumps(surfaces, separators=(',', ':')) + ';\n', encoding='utf-8')
    return written


SCHEMA = ROOT / 'docs/bct.schema.json'
CHECKER = ROOT / 'converter/check_pack.mjs'


def _atlas_problems(rp, pack):
    """Textures the pack's own atlas entries (named after the pack) name that are not in the resource pack."""
    path = Path(rp) / 'textures/terrain_texture.json'
    if not path.exists():
        return []
    key = pack.replace('-', '_')
    problems = []
    for alias, entry in read_json(path).get('texture_data', {}).items():
        if not alias.startswith((key + '_', f'bct_ov_{key}_')):
            continue
        for texture in atlas_paths(entry):
            if isinstance(texture, str) and not any((Path(rp) / (texture + extension)).exists()
                                                    for extension in ('.png', '.tga')):
                problems.append(f'textures/terrain_texture.json: {alias} names {texture}, which is not in the resource pack')
    return problems


def check_pack(bp, rp, *, samples, node='node', notes=None):
    """Everything wrong with a BCT pack, as messages; [] when it is ready.

    notes, a list, gets what is not wrong but worth knowing: an atlas near its limit, ray tracing set-up.
    """
    import shutil
    import subprocess
    bp, rp = Path(bp), Path(rp)
    problems = []
    manifest_path = bp / 'manifest.json'
    if not manifest_path.exists():
        return [f'{bp} has no manifest.json: run python bct.py init']
    manifest = read_json(manifest_path)
    engine = engine_dependency()
    dependency = next((item for item in manifest.get('dependencies', []) if item.get('uuid') == engine['uuid']), None)
    if dependency is None:
        problems.append('manifest.json: does not depend on the BCT engine: run python bct.py init')
    scripts = bp / 'scripts'
    main = scripts / 'main.js'
    entries = [module.get('entry') for module in manifest.get('modules', []) if module.get('type') == 'script']
    entry = bp / entries[0] if entries else main
    if not entries:
        problems.append('manifest.json: runs no script: run python bct.py init')
    elif not entry.exists():
        problems.append(f'{entries[0]} is missing: run python bct.py init')
    elif entry != main and 'authoredSource' not in entry.read_text(encoding='utf-8'):
        problems.append(f'{entries[0]} does not send bct.js to the engine: add the lines docs/AUTHORING.md shows')
    elif entry == main and main.read_text(encoding='utf-8') in EARLIER_MAIN_SCRIPTS:
        problems.append('scripts/main.js is from an earlier BCT: run python bct.py init')
    publisher = scripts / 'publisher.js'
    current = SCRIPT_NOTICE + (ROOT / 'engine/publisher.mjs').read_text(encoding='utf-8')
    if not publisher.exists():
        problems.append('scripts/publisher.js is missing: run python bct.py init')
    elif publisher.read_text(encoding='utf-8') != current:
        problems.append('scripts/publisher.js is from another BCT version: run python bct.py init')
    for generated, command in (('bct-overlays.js', 'overlays'), ('bct-carriers.js', 'carriers')):
        if not (scripts / generated).exists():
            problems.append(f'scripts/{generated} is missing: run python bct.py init (or python bct.py {command})')
    data = None
    executable = shutil.which(node)
    if executable is None:
        problems.append('Node.js is not installed, so scripts/bct.js was not compiled the way the engine does')
        data = _read_data(scripts / 'bct.js', None)
    else:
        result = subprocess.run([executable, str(CHECKER), str(bp), str(samples)], capture_output=True, text=True)
        try:
            report = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            report = {'data': None, 'error': 'the check could not run: ' + (result.stderr.strip() or result.stdout.strip())}
        data = report['data']
        if report['error']:
            problems.append('scripts/bct.js: ' + report['error'])
    if isinstance(data, dict):
        problems += ['scripts/bct.js' + error for error in Schemas(samples).validate_file(data, SCHEMA)]
        if isinstance(data.get('pack'), str):
            problems += _atlas_problems(rp, data['pack'])
    problems += check_tree(bp, rp, samples)
    for scan in (atlas_messages(rp, samples), ray_tracing_messages(rp)):
        problems += scan[0]
        if notes is not None:
            notes += scan[1]
    return list(dict.fromkeys(problems))
