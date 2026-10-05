"""Authored random variants through Bedrock's native terrain atlas.

Each random rule's tiles become one atlas entry of weighted variations, and the
rule's faces of each vanilla block point at it in blocks.json, so the game
picks a variation per position itself. Native position selection is not Java's
random seed algorithm. This backend handles unfiltered random rules only and
does not replace neighbor-based connections.
"""
import argparse
from copy import deepcopy
import hashlib
from pathlib import Path
import re
import tempfile
import uuid
import zipfile

from common import read_json, write_json
from native_connected import FACES, export_material

RULE_FIELDS = {'id', 'method', 'blocks', 'faces', 'tiles', 'weights'}
RENDERERS = ('classic', 'vv', 'rtx')
SKIP_TILES = ('<skip>', '<default>')
MAX_TILES = 256
MAX_WEIGHT = 1000000
LIMITS = ['Requires its source resource pack for faces not overridden.',
          'Animated variations bind the shared color/material timeline per atlas alias; '
          'rendering requires engine validation.',
          'No neighbor connections, filters, chaining, linked textures, or Java seed equivalence.']


def build_native_random(pack, document, block_definitions, key, renderer, output):
    """Writes a resource pack archive at output that draws the document's random rules natively.

    pack: folder of the authored tiles; block_definitions: the vanilla
    resource-pack blocks.json entries (faces a rule does not change keep their
    vanilla texture). Returns the report, which the archive also holds.
    """
    pack = Path(pack).resolve()
    output = Path(output).resolve()
    if output.is_relative_to(pack):
        raise ValueError('Export must be outside the source pack')
    if output.exists():
        raise ValueError('Export destination already exists')
    if renderer not in RENDERERS:
        raise ValueError('Unknown target renderer')
    if not re.fullmatch('[a-z][a-z0-9-]*', key):
        raise ValueError('Invalid pack key')
    rules = document.get('rules', [])
    if document.get('format_version') != 1 or not rules:
        raise ValueError('Expected nonempty rules document')
    bindings, blocks, atlas, flipbooks, materials = [], {}, {}, [], {}
    claimed = set()
    with tempfile.TemporaryDirectory(prefix='ctm-native-random-') as temporary:
        root = Path(temporary)
        for rule in rules:
            tiles, weights, faces = _checked_rule(rule)
            alias = 'bct_' + hashlib.sha256((key + '\0' + str(len(atlas))).encode()).hexdigest()[:24]
            variations = _export_variations(pack, root, tiles, weights, alias, renderer, materials, flipbooks)
            atlas[alias] = {'textures': {'variations': variations}}
            for block in rule['blocks']:
                _point_faces_at(alias, block, faces, block_definitions, blocks, claimed)
                bindings.append({'block': block, 'faces': faces, 'rule': rule['id'], 'atlas_alias': alias})
        write_json(root / 'blocks.json', {'format_version': '1.21.20', **blocks})
        write_json(root / 'textures/terrain_texture.json', {'resource_pack_name': key, 'texture_name': 'atlas.terrain',
                                                            'padding': 8, 'num_mip_levels': 4, 'texture_data': atlas})
        if flipbooks:
            write_json(root / 'textures/flipbook_textures.json', flipbooks)
        write_json(root / 'manifest.json', _manifest(key, renderer))
        report = {'renderer': renderer, 'bindings': bindings, 'materials': len(materials),
                  'animated_materials': sum(animation is not None for _, animation in materials.values()),
                  'animation_bindings': len(flipbooks),
                  'selection': 'Bedrock native weighted terrain variations', 'java_random_seed_parity': False,
                  'in_game_verified': False, 'full_support_verified': False, 'limits': list(LIMITS)}
        write_json(root / 'native-variants-report.json', report)
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(root).as_posix())
    return report


def _checked_rule(rule):
    """(tiles, weights, faces) of a rule this backend draws exactly; ValueError otherwise."""
    if set(rule) - RULE_FIELDS:
        raise ValueError('Native random rules cannot discard selectors or Java random settings')
    if rule.get('method') != 'random' or not rule.get('blocks'):
        raise ValueError('Expected a block-bound random rule')
    tiles = rule.get('tiles', [])
    weights = rule.get('weights', [1] * len(tiles))
    faces = rule.get('faces', FACES)
    if not 1 <= len(tiles) <= MAX_TILES or any(not isinstance(tile, str) or tile in SKIP_TILES for tile in tiles):
        raise ValueError('Expected 1-256 explicit material tiles')
    if len(weights) != len(tiles) or any(type(weight) is not int or not 1 <= weight <= MAX_WEIGHT
                                         for weight in weights):
        raise ValueError('Expected one integer weight from 1 to 1000000 per tile')
    if not faces or len(set(faces)) != len(faces) or any(face not in FACES for face in faces):
        raise ValueError('Invalid material faces')
    return tiles, weights, faces


def _export_variations(pack, root, tiles, weights, alias, renderer, materials, flipbooks):
    """The atlas variations of one rule; each tile's material is exported once for all rules."""
    variations = []
    for variant, (tile, weight) in enumerate(zip(tiles, weights)):
        if tile not in materials:
            stem = 'bct_' + hashlib.sha256(tile.encode()).hexdigest()[:24]
            name, animation, method = export_material(pack, tile, root, stem, renderer=renderer)
            if method != 'opaque':
                raise ValueError('Native random binding requires an opaque source block material')
            materials[tile] = (name, animation)
        name, animation = materials[tile]
        variations.append({'path': name, 'weight': weight})
        if animation:
            # The alias has one texture entry with a nested variations array, so
            # each animation binds to its alias and variation, even when several
            # rules share the same exported image.
            flipbooks.append({**animation, 'atlas_tile': alias, 'atlas_index': 0, 'atlas_tile_variant': variant})
    return variations


def _point_faces_at(alias, block, faces, block_definitions, blocks, claimed):
    """Points the rule's faces of a vanilla block's blocks.json entry at the rule's atlas alias."""
    if not block.startswith('minecraft:'):
        raise ValueError('Native override requires a mapped vanilla block')
    name = block.removeprefix('minecraft:')
    if name not in block_definitions:
        raise ValueError('Missing base block face definition: ' + block)
    entry = blocks.setdefault(name, deepcopy(block_definitions[name]))
    mapping = entry.get('textures')
    if isinstance(mapping, str):
        mapping = {face: mapping for face in FACES}
    if not isinstance(mapping, dict):
        raise ValueError('Missing base textures: ' + block)
    # Name every face on its own; a face without an entry takes "side".
    mapping = {face: mapping.get(face, mapping.get('side')) for face in FACES}
    if any(value is None for value in mapping.values()):
        raise ValueError('Incomplete base face mapping: ' + block)
    for face in faces:
        if (block, face) in claimed:
            raise ValueError('Overlapping native random rules require explicit priority resolution')
        claimed.add((block, face))
        mapping[face] = alias
    entry['textures'] = mapping


def _manifest(key, renderer):
    def pack_uuid(part):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f'bct/native-random/{key}/{renderer}/{part}'))

    manifest = {'format_version': 2,
                'header': {'name': f'{key} Native Variants {renderer.upper()}',
                           'description': 'Authored variants using native Bedrock terrain selection.',
                           'uuid': pack_uuid('header'), 'version': [0, 1, 0], 'min_engine_version': [1, 26, 20]},
                'modules': [{'type': 'resources', 'uuid': pack_uuid('resources'), 'version': [0, 1, 0]}]}
    if renderer != 'classic':
        manifest['capabilities'] = ['raytraced' if renderer == 'rtx' else 'pbr']
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resource-pack', type=Path, required=True)
    parser.add_argument('--rules', type=Path, required=True)
    parser.add_argument('--blocks', type=Path, required=True)
    parser.add_argument('--key', required=True)
    parser.add_argument('--renderer', choices=list(RENDERERS), required=True)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    build_native_random(arguments.resource_pack, read_json(arguments.rules), read_json(arguments.blocks),
                        arguments.key, arguments.renderer, arguments.output)
