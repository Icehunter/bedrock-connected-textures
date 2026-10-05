"""Repeat rules on full-cube blocks as native Bedrock blocks.

Each repeat rule becomes one custom full-cube block whose block states hold the
block's place in the pattern: bct:x and bct:z count positions modulo the least
common multiple of the pattern's width and height, bct:y modulo its height.
Every state combination is a permutation that shows, on each face, the tile
the repeat method picks there. The engine's bct:update_repeat component keeps
the states in step with the block's position. A Bedrock block state holds at
most 16 values, which bounds the pattern size.
"""
import itertools
import math
from pathlib import Path
import re
import tempfile
import uuid
import zipfile

from common import read_json, write_json
from engine_package import engine_dependency
from native_connected import FACES, export_material
from terrain_providers import ROOT

RULE_FIELDS = {'id', 'method', 'blocks', 'tiles', 'faces', 'width', 'height', 'symmetry', 'baseTiles'}
SYMMETRIES = ('none', 'opposite', 'all')
# A Bedrock block state holds at most this many values.
MAX_STATE_VALUES = 16
SKIP_TILES = ('<skip>', '<default>')
# With symmetry "opposite" a face shows the pattern of the face across from it.
OPPOSITE_FACES = {'up': 'down', 'south': 'north', 'east': 'west'}


def repeat_index(location, face, width, height, symmetry='none'):
    """The tile a width x height repeat shows on one face of the block at location (engine/tiles.mjs repeatIndex)."""
    x, y, z = location
    if symmetry == 'opposite':
        face = OPPOSITE_FACES.get(face, face)
    if symmetry == 'all':
        face = 'down'
    column, row = {'down': (x, -z - 1), 'up': (x, z), 'north': (-x - 1, -y), 'south': (x, -y),
                   'west': (z, -y), 'east': (-z - 1, -y)}[face]
    return row % height * width + column % width


def repeat_states(location, width, height):
    """The coordinate states (bct:x, bct:y, bct:z) that hold a position's place in the pattern."""
    period = math.lcm(width, height)
    return {'bct:x': location[0] % period, 'bct:y': location[1] % height, 'bct:z': location[2] % period}


def build_native_repeat(pack, rule_file, key, root=ROOT, renderer='rtx'):
    """Writes dist/development/native-repeat-<key>-<renderer>.mcaddon under root and returns its path.

    pack: folder of the authored tiles; rule_file: a rules document of repeat
    rules with one source block each; renderer: classic, vv or rtx (the
    resource pack's capabilities).
    """
    pack = Path(pack).resolve()
    root = Path(root)
    if not re.fullmatch('[a-z][a-z0-9-]*', key):
        raise ValueError('Invalid repeat pack key')
    rules = _checked_rules(read_json(rule_file))
    ids = {kind: str(uuid.uuid5(uuid.NAMESPACE_URL, f'bct/native-repeat/{renderer}/{key}/{kind}'))
           for kind in ('bp', 'rp', 'bp_module', 'rp_module')}
    destination = root / 'dist/development'
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f'native-repeat-{key}-{renderer}.mcaddon'
    bindings, atlas, flipbooks = [], {}, []
    total = 0
    with tempfile.TemporaryDirectory(prefix='native-repeat-') as temporary:
        generation = Path(temporary)
        bp, rp = generation / 'NativeRepeat_BP', generation / 'NativeRepeat_RP'
        _write_manifests(bp, rp, ids, key, renderer)
        for rule in rules:
            binding = _write_block(rule, pack, bp, rp, key, renderer, atlas, flipbooks)
            total += binding['permutations']
            bindings.append(binding)
        write_json(rp / 'textures/terrain_texture.json', {'resource_pack_name': 'bct_native_repeat',
                                                          'texture_name': 'atlas.terrain', 'padding': 8,
                                                          'num_mip_levels': 4, 'texture_data': atlas})
        if flipbooks:
            write_json(rp / 'textures/flipbook_textures.json', flipbooks)
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
            for path in sorted(generation.rglob('*')):
                if path.is_file():
                    output.write(path, path.relative_to(generation).as_posix())
    write_json(archive.with_suffix('.report.json'), {
        'archive': str(archive), 'renderer': renderer, 'bindings': bindings, 'permutations': total,
        'java_repeat_coordinates': True, 'in_game_tested': False, 'vanilla_blocks_replaced': False,
        'limits': ['Custom full-cube blocks require placement.',
                   'No rule chaining, biome filters, or neighbor connections in this backend.',
                   'Changes converge on the next block tick.']})
    return archive


def _checked_rules(document):
    """The document's repeat rules; ValueError for anything this backend cannot draw exactly."""
    rules = document.get('rules', [])
    if document.get('format_version') != 1 or not rules:
        raise ValueError('Repeat rules are required')
    for rule in rules:
        _check_rule(rule)
    if len({rule['id'] for rule in rules}) != len(rules):
        raise ValueError('Duplicate repeat rule IDs')
    return rules


def _check_rule(rule):
    if set(rule) - RULE_FIELDS:
        raise ValueError('Native repeat does not support filters, chained rules, or texture orientation')
    if rule.get('method') != 'repeat' or len(rule.get('blocks', [])) != 1:
        raise ValueError('Use one source block per repeat rule')
    if not re.fullmatch('[a-z][a-z0-9_]*', rule.get('id', '')):
        raise ValueError('Invalid repeat rule ID')
    if any(type(rule.get(field)) is not int or rule[field] < 1 for field in ('width', 'height')):
        raise ValueError('Repeat dimensions must be positive integers')
    if math.lcm(rule['width'], rule['height']) > MAX_STATE_VALUES or rule['height'] > MAX_STATE_VALUES:
        raise ValueError('Repeat coordinate states exceed 16 values; split the native block family explicitly')
    if len(rule['tiles']) != rule['width'] * rule['height'] or any(tile in SKIP_TILES for tile in rule['tiles']):
        raise ValueError('Repeat requires every authored tile')
    if rule.get('symmetry', 'none') not in SYMMETRIES:
        raise ValueError('Invalid repeat symmetry')
    faces = rule.get('faces', FACES)
    if not faces or any(face not in FACES for face in faces):
        raise ValueError('Invalid repeat faces')
    if any(face not in faces and face not in rule.get('baseTiles', {}) for face in FACES):
        raise ValueError('Unchanged faces require explicit baseTiles materials')


def _write_manifests(bp, rp, ids, key, renderer):
    for kind, folder, module in [('bp', bp, 'data'), ('rp', rp, 'resources')]:
        header = {'name': f'{key} {renderer.upper()} repeat blocks',
                  'description': 'Native full-cube repeat materials. Custom block placement is required.',
                  'uuid': ids[kind], 'version': [1, 0, 0], 'min_engine_version': [1, 26, 50]}
        manifest = {'format_version': 2, 'header': header,
                    'modules': [{'type': module, 'uuid': ids[kind + '_module'], 'version': [1, 0, 0]}]}
        if kind == 'bp':
            # The engine registers bct:update_repeat; this pack only declares blocks.
            manifest['dependencies'] = [{'uuid': ids['rp'], 'version': [1, 0, 0]}, engine_dependency()]
        elif renderer != 'classic':
            manifest['capabilities'] = ['raytraced'] if renderer == 'rtx' else ['pbr']
        write_json(folder / 'manifest.json', manifest)


def _write_block(rule, pack, bp, rp, key, renderer, atlas, flipbooks):
    """Writes one rule's block and exports its tiles; returns the rule's binding for the report."""
    stem = 'repeat_' + key.replace('-', '_') + '_' + rule['id']
    identifier = 'bct_repeat:' + stem
    instances = {}

    def material_instance(tile):
        if tile not in instances:
            alias = stem + '_' + str(len(instances))
            name, animation, render_method = export_material(pack, tile, rp, alias, renderer=renderer)
            atlas[alias] = {'textures': name}
            if animation:
                flipbooks.append(animation)
            instances[tile] = {'texture': alias, 'render_method': render_method, 'ambient_occlusion': 1.0,
                               'face_dimming': True}
        return instances[tile]

    tiles = [material_instance(tile) for tile in rule['tiles']]
    unchanged = {face: material_instance(tile) for face, tile in rule.get('baseTiles', {}).items()}
    width, height, symmetry = rule['width'], rule['height'], rule.get('symmetry', 'none')
    patterned_faces = rule.get('faces', FACES)
    period = math.lcm(width, height)
    permutations = []
    for x, y, z in itertools.product(range(period), range(height), range(period)):
        faces = {face: tiles[repeat_index((x, y, z), face, width, height, symmetry)] if face in patterned_faces
                 else unchanged[face] for face in FACES}
        faces['*'] = tiles[0]
        condition = ' && '.join(f"q.block_state('bct:{axis}') == {value}" for axis, value in zip('xyz', (x, y, z)))
        permutations.append({'condition': condition, 'components': {'minecraft:material_instances': faces}})
    components = {'minecraft:geometry': 'minecraft:geometry.full_block',
                  'minecraft:material_instances': permutations[0]['components']['minecraft:material_instances'],
                  'minecraft:collision_box': True, 'minecraft:selection_box': True,
                  'minecraft:destructible_by_mining': {'seconds_to_destroy': 1},
                  'minecraft:tick': {'interval_range': [1, 1], 'looping': True},
                  'bct:update_repeat': {'height': height, 'period': period}}
    states = {'bct:x': list(range(period)), 'bct:y': list(range(height)), 'bct:z': list(range(period))}
    write_json(bp / f'blocks/{stem}.json', {'format_version': '1.26.50', 'minecraft:block': {
        'description': {'identifier': identifier, 'states': states},
        'components': components, 'permutations': permutations}})
    return {'source_block': rule['blocks'][0], 'native_block': identifier, 'width': width, 'height': height,
            'period': period, 'permutations': len(permutations)}
