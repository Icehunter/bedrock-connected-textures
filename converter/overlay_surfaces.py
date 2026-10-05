"""Native overlay surfaces: the pack author's overlay rules drawn by custom blocks.

OptiFine and Continuity draw an overlay rule (method overlay, overlay_ctm,
overlay_random, overlay_repeat or overlay_fixed) as extra quads over a block's
face. Here each of those quads belongs to a custom block in the empty cell in
front of the face: above a block for its top face, beside it for a side face,
below it for its bottom face (engine/terrain-native.mjs places them). A surface
block draws in Classic, Vibrant Visuals and ray tracing, with the author's
normal and MER/MERS maps and the Java tint as the material tint_method.

A surface block has channels. A channel is one host face (the top of the block
below, a side of a block beside it, the bottom of the block above) and the
rules and tiles that can show there; each rule tile is a quad just outside that
face, and block states pick which quads show. Bedrock allows 16 values per
state, 64 switchable bones and 64 material instances per block, and the
converter keeps each block under MAX_TYPE_PERMUTATIONS permutations, so it
writes several surface types:

- one per face direction and top height (full cubes; paths and farmland one
  pixel lower; bottom slabs half a block lower) that shows one rule there;
- for the top face, types that show two rules that can meet on one block;
- a top type that also shows, on each side, the strip a block above that side
  gives (tile 15: grass or sand over a cliff edge).

The engine gives each cell the type that draws the most, the top face first.
What a cell cannot show is listed in the report under `limits`.
"""
from fnmatch import fnmatchcase
import itertools
import math
from pathlib import Path
import shutil
import uuid

from PIL import Image

from bedrock_schema import require_valid
from block_ids import known_blocks
from common import BLOCKS_FORMAT, read_json, write_json
from java_block_geometry import cube
from native_connected import export_material

FACES = ('up', 'north', 'south', 'west', 'east', 'down')
SIDES = ('north', 'south', 'west', 'east')
METHODS = ('overlay', 'overlay_ctm', 'overlay_random', 'overlay_repeat', 'overlay_fixed')
# Tiles that draw nothing.
SKIPPED_TILES = ('<skip>', '<default>')
# method=overlay uses the 17 pieces of the overlay template.
OVERLAY_TEMPLATE_SIZE = 17
BLOCK_FORMAT = '1.26.50'
EMPTY_LOOT_TABLE = 'loot_tables/blocks/bct_empty.json'

# Bedrock block limits: 64 switchable bones and 64 material instances, one of them the "*" default.
MAX_BONES = 64
MAX_MATERIALS = 63
MAX_STATE_VALUES = 16
# placement_filter takes at most 64 conditions of at most 64 blocks each.
MAX_FILTER_BLOCKS = 64
MAX_FILTER_CONDITIONS = 64
# Permutation budgets for one surface block and for all of them together (our limits, far below the
# game's): a pair of rules on one top face costs 2304, and a pack with grass, sand and the generated
# sand edges has six such pairs, so every edge meeting another on one block can draw.
MAX_TYPE_PERMUTATIONS = 2400
MAX_SURFACE_PERMUTATIONS = 20000

# Distance of an overlay quad from the face it covers, in pixels; later rules lie above earlier ones.
QUAD_DEPTH = 0.12
RULE_STEP = 0.06
# With many rules the step shrinks so every quad stays within half a pixel of its face.
MAX_RULE_SPREAD = 0.36
# Corner pieces lie this much above the edge pieces of their rule.
TILE_STEP = 0.002
QUAD_THICKNESS = 0.001
# Java draws a cutout pixel whole when its alpha is at least 0.1; Bedrock's alpha test cuts at 0.5.
JAVA_CUTOUT_ALPHA = 26

# Overlay template pieces (engine/overlay.mjs) by the edges they cover: left 1, down 2, right 4, up 8 in texture space.
EDGE_TILES = {0: [], 1: [9], 2: [1], 3: [4], 4: [7], 5: [9, 7], 6: [3], 7: [5], 8: [15], 9: [11],
              10: [1, 15], 11: [6], 12: [10], 13: [13], 14: [12], 15: [8]}
# Corner pieces between left+down, down+right, right+up and up+left.
CORNER_TILES = (2, 0, 14, 16)
# The piece along the top edge of a side face: the strip the block above gives.
SIDE_STRIP_TILE = 15

# Full cubes that do not cover what is behind them (Java's solid render blocks are full opaque cubes).
NOT_SOLID = ('*glass', 'minecraft:ice', 'minecraft:frosted_ice', 'minecraft:slime', 'minecraft:slime_block',
             'minecraft:honey_block', '*copper_grate', 'minecraft:beacon', 'minecraft:mob_spawner',
             'minecraft:trial_spawner', 'minecraft:vault', 'minecraft:barrier', 'minecraft:structure_void',
             'minecraft:powder_snow', '*_leaves', 'minecraft:azalea_leaves_flowered', '*mangrove_roots')
# Blocks whose top face is a whole square below the top of the cell, in pixels.
LOW_TOPS = {'minecraft:grass_path': -1, 'minecraft:farmland': -1}
SLAB_STATE = 'minecraft:vertical_half'

# Java tint kinds (modelTintTypes) and the Bedrock tint_method that draws them.
TINT_METHODS = {'grass': 'grass', 'foliage': 'default_foliage', 'water': 'water'}
FOLIAGE_TINT_METHODS = {'minecraft:birch_leaves': 'birch_foliage', 'minecraft:spruce_leaves': 'evergreen_foliage'}
GRASS_BLOCKS = ('minecraft:grass_block', 'minecraft:grass')

# Rule fields the engine reads as they are.
ENGINE_RULE_FIELDS = ('connect', 'weights', 'randomLoops', 'symmetry', 'linked', 'width', 'height', 'orient',
                      'innerSeams', 'biomes', 'heights')
# Rule fields that make an overlay draw only in some places.
CONDITION_FIELDS = ('biomes', 'heights', 'blockMatchers', 'states')
# Suffixes of the block states one channel is spread over.
STATE_SUFFIXES = 'abcdefgh'

CELL_LIMITS = (
    'A cell draws the top face of the block below it with one rule, or with two rules where a pair type was written.',
    'With one rule on the top face, a cell also draws the strip each side gets from the block above that side '
    '(tile 15); other side overlays draw only in cells with no top face to draw.',
    'A cell in front of two side faces draws one of them unless both are strips; '
    'bottom faces draw only in cells with nothing else.',
    'Surfaces go only into air: faces behind water, snow, plants or glass show no overlay.',
)


def _free_corners(edges):
    """Corners whose two edges are both open; only those can get a corner piece."""
    return [corner for corner in range(4) if not edges & ((1 << corner) | (1 << ((corner + 1) % 4)))]


def _overlay_cases():
    """Every (edges, corners) result of the overlay method, sorted; index 0 is no overlay."""
    cases = []
    for edges in range(16):
        free = _free_corners(edges)
        for subset in range(1 << len(free)):
            corners = sum(1 << corner for index, corner in enumerate(free) if subset & (1 << index))
            cases.append((edges, corners))
    return sorted(cases)


def overlay_tiles(edges, corners):
    """Template tiles for one overlay case, in Java's drawing order."""
    tiles = list(EDGE_TILES[edges])
    # Java takes the corners in turn, starting after the edge when there is exactly one.
    single_edge = bin(edges).count('1') == 1
    first_corner = [1, 2, 4, 8].index(edges) + 1 if single_edge else 0
    free = _free_corners(edges)
    for step in range(4):
        corner = (first_corner + step) % 4
        if corners & (1 << corner) and corner in free:
            tiles.append(CORNER_TILES[corner])
    return tiles


COMBOS = _overlay_cases()
# The overlay case showing only the side strip (the up edge in texture space).
SIDE_STRIP_CASE = COMBOS.index((8, 0))


class _OverlayRule:
    """One overlay rule of the pack, with the faces and blocks it draws on."""

    def __init__(self, rule):
        self.rule = rule
        self.id = rule['id']
        self.method = rule['method']
        tiles = rule.get('tiles', [])
        self.skip = [index for index, tile in enumerate(tiles) if tile in SKIPPED_TILES]
        if self.method == 'overlay':
            # A channel value is an overlay case (COMBOS index); 0 shows nothing.
            values = list(range(1, len(COMBOS)))
            shown = {tile for case in COMBOS for tile in overlay_tiles(*case)}
        else:
            # A channel value is a tile index plus one; 0 shows nothing.
            values = [index + 1 for index, tile in enumerate(tiles) if tile not in SKIPPED_TILES]
            shown = set(range(len(tiles)))
        self.used = sorted(tile for tile in shown if tile < len(tiles) and tiles[tile] not in SKIPPED_TILES)
        # A case whose tiles are all <skip> shows nothing, so a surface never needs it.
        self.values = [value for value in values if self.tiles_of(value)]
        self.slots = {}         # (face, top offset) -> host blocks
        self.looks = {}         # (face, top offset) -> (host block, look number) pairs the rule draws on
        self.hosts = {}         # face -> host blocks
        self.sources = {}       # face -> blocks the overlay comes from (method=overlay)
        self.problems = []
        self.rank = None
        self.depth = None
        self.tint = None
        self.fixed_tint = None
        self.translucent = False
        self.replaced = []

    def tiles_of(self, value):
        """Tile indexes a channel value shows."""
        if self.method == 'overlay':
            return [tile for tile in overlay_tiles(*COMBOS[value]) if tile in self.used]
        return [value - 1] if value - 1 in self.used else []


class _Channel:
    """One host face of a surface block and the rules it can show there.

    Value 0 shows nothing and each rule's values follow in order. The values
    are spread over block states of 16 values or fewer.
    """

    def __init__(self, face, offset, options):
        self.face = face
        self.offset = offset
        self.options = options          # [(rule, [values])]
        self.sizes = _state_sizes(1 + sum(len(values) for _, values in options))
        self.states = []                # [(state name, size)], named when the block is written
        self._quads = None

    @property
    def permutations(self):
        return math.prod(self.sizes)

    def quads(self):
        """[(rule, tile, channel values showing it)], one per rule tile the channel can show."""
        if self._quads is None:
            self._quads = self._list_quads()
        return self._quads

    def _list_quads(self):
        quads = []
        first_value = 1
        for rule, values in self.options:
            channel_value = {value: first_value + index for index, value in enumerate(values)}
            for tile in rule.used:
                showing = [channel_value[value] for value in values if tile in rule.tiles_of(value)]
                if showing:
                    quads.append((rule, tile, showing))
            first_value += len(values)
        return quads


class _SurfaceTypes:
    """The surface block types planned so far, within the block limits and the permutation budget."""

    def __init__(self):
        self.types = []
        self.notes = []
        self.permutations = 0

    def add(self, channels, kind):
        """Add a type showing these channels, or note why it does not fit."""
        cost = math.prod(channel.permutations for channel in channels)
        bones = sum(len(channel.quads()) for channel in channels)
        materials = len({(rule.id, tile) for channel in channels for rule, tile, _ in channel.quads()})
        if bones > MAX_BONES or materials > MAX_MATERIALS or cost > MAX_TYPE_PERMUTATIONS:
            self.notes.append(f'{kind}: {bones} quads, {materials} materials, {cost} permutations '
                              'is over the block limits')
            return
        if self.permutations + cost > MAX_SURFACE_PERMUTATIONS:
            self.notes.append(f'{kind}: {cost} more permutations would pass the budget of {MAX_SURFACE_PERMUTATIONS}')
            return
        self.types.append({'kind': kind, 'channels': channels, 'permutations': cost, 'bones': bones})
        self.permutations += cost


def build(document, compiled, output, *, key, samples, replacement_data=None, replacement_report=None):
    """Write Overlay_BP, Overlay_RP, engine-data.json, authored.json and overlay-report.json into output.

    document: the authored rules (import_java_ctm with the binding providers).
    compiled: the compiled pack holding the tiles and their maps.
    replacement_data, replacement_report: the native replacement blocks; a
    replacement block hosts the surfaces of the vanilla block it stands in for,
    and the rules it draws itself are passed on to the engine.
    Returns (engine data or None, report).
    """
    output = Path(output)
    if output.exists():
        shutil.rmtree(output)
    known = known_blocks(samples)
    states = _known_states(samples)
    cubes = _full_cubes(document, known)
    dialect = document.get('dialect', 'optifine')
    replaced_rules = (replacement_report or {}).get('rules', {})
    rules, left_out = _plan_rules(document, known, cubes, states, replaced_rules, dialect)
    report = {'rules_drawn': [], 'rules_not_drawn': left_out, 'types': [], 'permutations': 0, 'textures': 0,
              'limits': list(CELL_LIMITS)}
    if not rules:
        return None, report
    types, notes, total = _plan_types(rules)
    report['notes'] = notes
    if not types:
        report['rules_not_drawn'] += [_not_drawn(rule.rule, 'no surface block fits') for rule in rules]
        return None, report
    rules = _rules_with_a_type(rules, types, report)

    key_name = key.replace('-', '_')
    namespace = 'bct_' + key_name
    bp = output / 'Overlay_BP'
    rp = output / 'Overlay_RP'
    _write_manifests(bp, rp, key)
    aliases, atlas, flipbooks = _export_tiles(rules, compiled, rp, key_name)
    write_json(rp / 'textures/terrain_texture.json', {'resource_pack_name': namespace + '_overlay',
                                                      'texture_name': 'atlas.terrain', 'padding': 8,
                                                      'num_mip_levels': 4, 'texture_data': atlas})
    if flipbooks:
        write_json(rp / 'textures/flipbook_textures.json', flipbooks)
    write_json(bp / EMPTY_LOOT_TABLE, {'pools': []})
    replacement_blocks = _replacement_blocks(replacement_data)
    engine_types = _write_surface_blocks(bp, rp, namespace, types, rules, aliases, replacement_blocks, report)

    data = _engine_data(document, rules, engine_types, cubes, states, dialect)
    write_json(output / 'engine-data.json', data)
    write_json(output / 'authored.json', _authored_overlays(rules))
    report['rules_drawn'].extend(_drawn_rule_report(rule) for rule in rules)
    report['permutations'] = total
    report['textures'] = len(atlas)
    report['surface_blocks'] = len(types)
    require_valid(bp, rp, samples)
    write_json(output / 'overlay-report.json', report)
    return data, report


def prune_generated_edges(provider, authored, known):
    """Leave BCT's generated edges out where the pack's own overlay rules draw the same transition.

    A generated rule draws its sources' material over its targets' faces. When an
    unconditional overlay rule of the pack (authored.json) draws on that target face
    and every generated source that is a Bedrock block gives that rule's overlay
    there, the target is left to the pack's rule. Overlay rules with biome, height
    or state conditions do not draw everywhere, so they leave the generated edge.
    Changes the provider in place; returns (authored transitions, generated ones left).
    """
    authored_edges = []
    generated_edges = []
    effects = provider.get('effects', {})
    for rule in provider.get('rules', []):
        if effects.get(rule.get('effect'), {}).get('generator') != 'neighbor_overlay':
            continue
        faces = rule.get('faces', ['up'])
        sources = [block for block in rule.get('neighbors', []) if block in known]
        retained = []
        for target in rule.get('targets', []):
            owner = _authored_owner(authored, sources, target, faces)
            if owner:
                authored_edges.append({'sources': sources, 'target': target, 'faces': faces,
                                       'generated_rule': rule['id'], 'authored_rule': owner['rule'],
                                       'filename': owner['filename']})
            else:
                retained.append(target)
        rule['targets'] = retained
        known_targets = [target for target in retained if target in known]
        if known_targets:
            generated_edges.append({'sources': sources, 'targets': known_targets, 'faces': faces,
                                    'generated_rule': rule['id']})
    provider['rules'] = [rule for rule in provider.get('rules', []) if rule.get('targets')]
    return authored_edges, generated_edges


def _authored_owner(authored, sources, target, faces):
    """The first unconditional authored overlay that draws on all of target's faces from every source, or None."""
    if not sources:
        return None
    for entry in authored:
        if entry['conditional']:
            continue
        if all(_draws_from(entry, face, target, sources) for face in faces):
            return entry
    return None


def _draws_from(entry, face, target, sources):
    if face not in entry['faces']:
        return False
    drawn = entry['faces'][face]
    return target in drawn['hosts'] and set(sources) <= set(drawn['sources'])


# Blocks and their faces


def _known_states(samples):
    """{block: set of state names} from bedrock-samples' vanilla block list."""
    data = read_json(Path(samples) / 'metadata/vanilladata_modules/mojang-blocks.json')
    return {item['name']: {state['name'] for state in item.get('properties', [])} for item in data['data_items']}


def _full_cubes(document, known):
    # Bedrock's double slabs are separate blocks; in Java they are the slab's full-cube state.
    double_slabs = {block for block in known if block.endswith('_double_slab')}
    return (set(document.get('fullCubeBlocks', [])) | double_slabs) & known


def _matches_any(block, patterns):
    return any(fnmatchcase(block, pattern) for pattern in patterns)


def _host_shapes(block, cubes, states):
    """[(state filter or None, {face: top offset in pixels})] of the faces of a block that are whole squares.

    Java draws an overlay only on a face that is a whole square.
    """
    if block in cubes:
        return [(None, {face: 0 for face in FACES})]
    if block in LOW_TOPS:
        return [(None, {'up': LOW_TOPS[block]})]
    if block.endswith('_slab') and SLAB_STATE in states.get(block, ()):
        return [({SLAB_STATE: ['bottom']}, {'up': -8}), ({SLAB_STATE: ['top']}, {'up': 0})]
    return []


def _face_textures(document, block):
    """Every texture each face of a block can show ({face: set}): base faces, variants, layers and model parts."""
    result = {face: set() for face in FACES}
    for face, texture in document.get('baseTextures', {}).get(block, {}).items():
        if face in result:
            result[face].add(texture)
    for variant in document.get('baseTextureVariants', {}).get(block, []):
        for face, textures in _variant_faces(variant).items():
            result[face].update(textures[1:])
            if textures[0]:
                result[face].add(textures[0])
    return result


def _variant_faces(variant):
    """{face: [base texture or None, other textures...]} for one block state variant."""
    choices = variant.get('modelChoices') or [variant]
    part_options = [option for group in variant.get('modelPartsGroups', []) for option in group.get('modelChoices', [])]
    result = {}
    for face in FACES:
        base = choices[0].get('faces', {}).get(face)
        others = []
        for choice in choices:
            texture = choice.get('faces', {}).get(face)
            if texture and texture != base:
                others.append(texture)
            others += [layer['texture'] for layer in choice.get('faceLayers', {}).get(face, [])]
            others += _part_textures(choice.get('modelParts', []), face)
        for option in part_options:
            others += _part_textures(option.get('modelParts', []), face)
        if base or others:
            result[face] = [base] + list(dict.fromkeys(texture for texture in others if texture != base))
    return result


def _part_textures(parts, face):
    """Textures of the model part faces that face this way in the world."""
    return [data['texture'] for part in parts for side, data in part.get('faces', {}).items()
            if data.get('worldFace', side) == face]


def _block_looks(document, block):
    """[(states or None, {face: set of textures})]: the block's base look and one per state variant."""
    base_faces = document.get('baseTextures', {}).get(block, {})
    looks = [(None, {face: {texture} for face, texture in base_faces.items() if face in FACES})]
    for variant in document.get('baseTextureVariants', {}).get(block, []):
        faces = {face: {texture for texture in textures if texture}
                 for face, textures in _variant_faces(variant).items()}
        looks.append((variant.get('states') or {}, faces))
    return looks


def _look_allows(rule, block, states):
    """Whether a rule's state matchers allow a look with these block states (unknown states allow it)."""
    clauses = [clause for clause in rule.get('blockMatchers', []) if clause['block'] == block]
    if not clauses or states is None:
        return True
    return any(_clause_allows(clause, states) for clause in clauses)


def _clause_allows(clause, states):
    for name, values in clause.get('states', {}).items():
        if name in states and str(states[name]).lower() not in [str(value).lower() for value in values]:
            return False
    return True


def _tint_method(rule, document, dialect):
    """(Bedrock tint_method, fixed RGB factors or None, problem or None) for an overlay rule's tint."""
    tint_index = rule.get('tintIndex', -1)
    tint_block = rule.get('tintBlock')
    # Continuity tints only with a tintBlock; OptiFine uses the block the overlay is drawn on.
    if tint_index is None or tint_index < 0 or (tint_block is None and dialect != 'optifine'):
        return 'none', None, None
    if tint_block is None:
        return 'none', None, 'tintIndex without tintBlock takes the tint of each block it is drawn on; drawn untinted'
    kind = document.get('modelTintTypes', {}).get(tint_block)
    if kind is None and tint_block in GRASS_BLOCKS:
        kind = 'grass'
    if isinstance(kind, list) and len(kind) == 3:
        return 'none', [float(value) for value in kind], None
    if kind == 'foliage':
        return FOLIAGE_TINT_METHODS.get(tint_block, 'default_foliage'), None, None
    if kind in TINT_METHODS:
        return TINT_METHODS[kind], None, None
    return 'none', None, f'tint of {tint_block} has no Bedrock tint_method; drawn untinted'


# Planning: which rules draw where, and the surface types that show them


def _plan_rules(document, known, cubes, states, replaced_rules, dialect):
    """The overlay rules surfaces draw, with their hosts and sources, and the rules left out with reasons."""
    drawn = []
    left_out = []
    textures = {block: _face_textures(document, block) for block in known}
    textured = [block for block in known if any(textures[block].values())]
    looks_by_block = {}
    for rule in _rules_in_weight_order(document):
        if not str(rule.get('method', '')).startswith('overlay'):
            continue
        reason = _method_problem(rule)
        if reason:
            left_out.append(_not_drawn(rule, reason))
            continue
        overlay_rule = _OverlayRule(rule)
        if not overlay_rule.used:
            left_out.append(_not_drawn(rule, 'every tile is <skip> or <default>'))
            continue
        _find_hosts(overlay_rule, document, known, cubes, states, textures, textured, looks_by_block)
        _find_sources(overlay_rule, cubes, textures, textured)
        if not overlay_rule.slots:
            reason = '; '.join(overlay_rule.problems) or 'no full block face shows a matched texture'
            left_out.append(_not_drawn(rule, reason))
            continue
        overlay_rule.tint, overlay_rule.fixed_tint, problem = _tint_method(rule, document, dialect)
        if problem:
            overlay_rule.problems.append(problem)
        overlay_rule.translucent = rule.get('layer') == 'translucent'
        overlay_rule.replaced = sorted(set(replaced_rules.get(rule['id'], [])))
        drawn.append(overlay_rule)
    _stack_rules(drawn)
    return drawn, left_out


def _rules_in_weight_order(document):
    """The document's rules, higher weight first, then in file order."""
    numbered = enumerate(document.get('rules', []))
    return [rule for _, rule in sorted(numbered, key=lambda item: (-(item[1].get('weight') or 0), item[0]))]


def _method_problem(rule):
    if rule['method'] not in METHODS:
        return 'method ' + rule['method'] + ' is not an overlay method'
    if rule['method'] == 'overlay' and len(rule.get('tiles', [])) != OVERLAY_TEMPLATE_SIZE:
        return 'method=overlay needs 17 tiles'
    return None


def _not_drawn(rule, reason):
    return {'rule': rule['id'], 'filename': rule.get('filename'), 'reason': reason}


def _find_hosts(overlay_rule, document, known, cubes, states, textures, textured, looks_by_block):
    """Record the whole-square faces (and their top heights) the rule draws on, and the block looks there."""
    rule = overlay_rule.rule
    match_tiles = set(rule.get('matchTiles', []))
    match_blocks = set(rule.get('blocks', [])) | {clause['block'] for clause in rule.get('blockMatchers', [])}
    candidates = (match_blocks if match_blocks else set(textured)) & set(known)
    rule_faces = rule.get('faces', FACES)
    for block in sorted(candidates):
        for _, offsets in _host_shapes(block, cubes, states):
            for face, offset in offsets.items():
                if face not in rule_faces:
                    continue
                # A lowered top (path, bottom slab) would need geometry below the surface's own cell,
                # which the game rejects for the whole block type.
                if offset:
                    continue
                if match_tiles and not textures[block][face] & match_tiles:
                    continue
                overlay_rule.slots.setdefault((face, offset), set()).add(block)
                overlay_rule.hosts.setdefault(face, set()).add(block)
                if block not in looks_by_block:
                    looks_by_block[block] = _block_looks(document, block)
                # Two rules meet on a face only in a block look (state variant) both draw on.
                for number, (look_states, look_faces) in enumerate(looks_by_block[block]):
                    if match_tiles and not look_faces.get(face, set()) & match_tiles:
                        continue
                    if _look_allows(rule, block, look_states):
                        overlay_rule.looks.setdefault((face, offset), set()).add((block, number))


def _find_sources(overlay_rule, cubes, textures, textured):
    """For method=overlay with connectBlocks or connectTiles: the full blocks the overlay comes from, per face.

    Java tests the neighbor's texture on the face drawn, so a face no full block
    shows a connect texture on never gets the overlay; that face is dropped.
    """
    rule = overlay_rule.rule
    if rule['method'] != 'overlay' or not (rule.get('connectBlocks') or rule.get('connectTiles')):
        return
    connect_blocks = set(rule.get('connectBlocks', []))
    connect_tiles = set(rule.get('connectTiles', []))
    candidates = (connect_blocks if connect_blocks else set(textured)) & cubes
    sourceless = []
    for face in list(overlay_rule.hosts):
        found = {block for block in candidates
                 if not connect_tiles or textures.get(block, {}).get(face, set()) & connect_tiles}
        if found:
            overlay_rule.sources[face] = found
            continue
        sourceless.append(face)
        overlay_rule.hosts.pop(face)
        overlay_rule.slots = {slot: blocks for slot, blocks in overlay_rule.slots.items() if slot[0] != face}
    if sourceless:
        overlay_rule.problems.append('no full block shows a connect texture on its ' + ', '.join(sourceless) +
                                     ' face, so those faces never get this overlay')


def _stack_rules(rules):
    """Number the drawn rules and lift each later rule's quads above the earlier ones'."""
    step = min(RULE_STEP, MAX_RULE_SPREAD / max(1, len(rules) - 1))
    for rank, rule in enumerate(rules):
        rule.rank = rank
        rule.depth = QUAD_DEPTH + rank * step


def _plan_types(rules):
    """Surface types for the drawn rules: (types, notes, total permutations).

    One-rule types for each face and top height come first, then the top face
    with the side strips, then pairs of rules on a top face.
    """
    plan = _SurfaceTypes()
    top_groups = _add_single_face_types(plan, rules)
    _add_top_with_strips(plan, rules, top_groups)
    _add_top_pairs(plan, rules)
    return plan.types, plan.notes, plan.permutations


def _add_single_face_types(plan, rules):
    """One type per face and top height for each group of rules that fits a block; returns the top face groups."""
    slots = sorted({slot for rule in rules for slot in rule.slots}, key=lambda slot: (FACES.index(slot[0]), -slot[1]))
    top_groups = []
    for face, offset in slots:
        members = [rule for rule in rules if (face, offset) in rule.slots]
        groups = _split_for_block_limits(members, plan.notes)
        if (face, offset) == ('up', 0):
            top_groups = groups
        for group in groups:
            plan.add([_Channel(face, offset, [(rule, rule.values) for rule in group])], f'{face}{offset or ""}')
    return top_groups


def _split_for_block_limits(rules, notes):
    """The rules of one face in groups that each fit one block's bones and materials."""
    groups = []
    group = []
    size = 0
    for rule in rules:
        if len(rule.used) > MAX_BONES:
            notes.append(f'{rule.id}: {len(rule.used)} tiles are more than one block can show')
            continue
        if group and size + len(rule.used) > min(MAX_BONES, MAX_MATERIALS):
            groups.append(group)
            group = []
            size = 0
        group.append(rule)
        size += len(rule.used)
    if group:
        groups.append(group)
    return groups


def _add_top_with_strips(plan, rules, top_groups):
    """The top face together with the strip each side gets from the block above it (tile 15 of method=overlay)."""
    strips = {side: [rule for rule in rules
                     if rule.method == 'overlay' and (side, 0) in rule.slots and SIDE_STRIP_TILE in rule.used]
              for side in SIDES}
    if len(top_groups) == 1 and any(strips.values()):
        channels = [_Channel('up', 0, [(rule, rule.values) for rule in top_groups[0]])]
        for side in SIDES:
            if strips[side]:
                channels.append(_Channel(side, 0, [(rule, [SIDE_STRIP_CASE]) for rule in strips[side]]))
        plan.add(channels, 'up+side strips')
    elif len(top_groups) > 1:
        plan.notes.append('the top face rules need more than one block, so top faces get no side strips')


def _add_top_pairs(plan, rules):
    """Types that show two rules on one top face, for rules that can draw on the same block look.

    Pairs sharing the most looks come first.
    """
    top_rules = [rule for rule in rules if ('up', 0) in rule.slots]
    pairs = []
    for first, second in itertools.combinations(top_rules, 2):
        shared = first.looks.get(('up', 0), set()) & second.looks.get(('up', 0), set())
        if shared:
            pairs.append((-len(shared), first.rank, second.rank, first, second))
    for _, _, _, first, second in sorted(pairs, key=lambda pair: pair[:3]):
        channels = [_Channel('up', 0, [(first, first.values)]), _Channel('up', 0, [(second, second.values)])]
        plan.add(channels, f'up pair {first.id}+{second.id}')


def _rules_with_a_type(rules, types, report):
    """The rules some surface type shows; the others are reported as not drawn."""
    shown = {rule.id for entry in types for channel in entry['channels'] for rule, _ in channel.options}
    for rule in rules:
        if rule.id not in shown:
            report['rules_not_drawn'].append(_not_drawn(rule.rule, 'no surface block fits its tiles'))
    return [rule for rule in rules if rule.id in shown]


def _state_sizes(count):
    """Sizes of the block states (16 values or fewer each) that hold count values, with the fewest permutations."""
    if count <= MAX_STATE_VALUES:
        return [max(count, 2)]
    best = None
    for size in range(2, MAX_STATE_VALUES + 1):
        rest = math.ceil(count / size)
        option = [size, rest] if rest <= MAX_STATE_VALUES else [size, *_state_sizes(rest)]
        if best is None or (math.prod(option), len(option)) < (math.prod(best), len(best)):
            best = option
    return best


# Resource and behavior pack files


def _write_manifests(bp, rp, key):
    for folder, kind, module in ((bp, 'bp', 'data'), (rp, 'rp', 'resources')):
        folder.mkdir(parents=True)
        header = {'name': key + ' overlay surfaces', 'description': 'Native overlay surfaces.',
                  'uuid': _pack_uuid(key, kind), 'version': [1, 0, 0], 'min_engine_version': [1, 26, 50]}
        modules = [{'type': module, 'uuid': _pack_uuid(key, kind + '/module'), 'version': [1, 0, 0]}]
        write_json(folder / 'manifest.json', {'format_version': 2, 'header': header, 'modules': modules})


def _pack_uuid(key, name):
    """A UUID that is the same in every conversion with this pack key."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'bct/overlay/{key}/{name}'))


def _export_tiles(rules, compiled, rp, key):
    """Each tile the surfaces show as a terrain texture with its normal and MER(S) maps.

    Returns ({(rule id, tile): (texture alias, render method)}, terrain atlas entries, flipbook entries).
    """
    aliases = {}
    atlas = {}
    flipbooks = []
    for rule in rules:
        for tile in rule.used:
            stem = f'bct_ov_{key}_{rule.id}_{tile}'
            name, flipbook, _ = export_material(compiled, rule.rule['tiles'][tile], rp, stem, renderer='vv')
            render_method = _finish_color(Path(rp) / (name + '.png'), rule)
            atlas[stem] = {'textures': name}
            if flipbook:
                flipbooks.append(flipbook)
            aliases[(rule.id, tile)] = (stem, render_method)
    return aliases, atlas, flipbooks


def _finish_color(path, rule):
    """Bake a fixed tint into a tile's colour image and cut it out as Java does; returns the render method."""
    with Image.open(path) as image:
        pixels = image.convert('RGBA')
    if rule.fixed_tint:
        pixels = _multiply_color(pixels, rule.fixed_tint)
    if rule.translucent:
        render_method = 'blend'
    else:
        # A cutout pixel shows whole in Java from alpha 0.1; keep exactly those for Bedrock's alpha test.
        pixels.putalpha(pixels.getchannel('A').point(lambda value: 255 if value >= JAVA_CUTOUT_ALPHA else 0))
        render_method = 'alpha_test_single_sided'
    pixels.save(path)
    return render_method


def _multiply_color(pixels, factors):
    red, green, blue, alpha = pixels.split()
    tinted = [channel.point(lambda value, factor=factor: round(value * factor))
              for channel, factor in zip((red, green, blue), factors)]
    return Image.merge('RGBA', (*tinted, alpha))


def _replacement_blocks(replacement_data):
    """{vanilla block: [replacement blocks standing in for it]}."""
    result = {}
    for entry in (replacement_data or {}).get('blocks', []):
        result.setdefault(entry['vanilla'], []).append(entry['block'])
    return result


def _write_surface_blocks(bp, rp, namespace, types, rules, aliases, replacement_blocks, report):
    """Write each surface type's block and geometry and the block sounds; returns the types for the engine."""
    rule_numbers = {rule.id: number for number, rule in enumerate(rules)}
    sounds = {'format_version': BLOCKS_FORMAT}
    engine_types = []
    for number, surface_type in enumerate(types):
        identifier = f'{namespace}:overlay_{number}'
        geometry_id = f'geometry.{namespace}_overlay_{number}'
        channels = surface_type['channels']
        _name_channel_states(channels)
        hosts = _type_hosts(channels, replacement_blocks)
        write_json(bp / f'blocks/{namespace}_overlay_{number}.json',
                   _block_definition(identifier, geometry_id, channels, aliases, hosts))
        write_json(rp / f'models/blocks/{namespace}_overlay_{number}.geo.json',
                   _geometry_definition(geometry_id, channels))
        sounds[identifier] = {'sound': 'grass'}
        engine_types.append({'block': identifier, 'channels': [_engine_channel(channel, rule_numbers)
                                                               for channel in channels]})
        report['types'].append(_type_report(identifier, surface_type))
    write_json(rp / 'blocks.json', sounds)
    return engine_types


def _name_channel_states(channels):
    """Name each channel's block states: bct:c<channel>, with a, b, c... when its values span several states."""
    for number, channel in enumerate(channels):
        several = len(channel.sizes) > 1
        names = [f'bct:c{number}' + (STATE_SUFFIXES[index] if several else '') for index in range(len(channel.sizes))]
        channel.states = list(zip(names, channel.sizes))


def _type_hosts(channels, replacement_blocks):
    """The blocks a type's surfaces sit on, with the replacement blocks standing in for them."""
    hosts = set()
    for channel in channels:
        for rule, _ in channel.options:
            for block in rule.slots.get((channel.face, channel.offset), ()):
                hosts.add(block)
                hosts.update(replacement_blocks.get(block, []))
    return hosts


def _block_definition(identifier, geometry_id, channels, aliases, hosts):
    """The surface block: states, geometry, materials and the components that keep it out of the way."""
    states = {}
    for channel in channels:
        for name, size in channel.states:
            states[name] = list(range(size))
    visible = {}
    for number, channel in enumerate(channels):
        for rule, tile, values in channel.quads():
            visible[f'c{number}_{rule.id}_{tile}'] = _molang_visibility(channel.states, values)
    quads = [(rule, tile) for channel in channels for rule, tile, _ in channel.quads()]
    # A block has one render method: translucent tiles make the whole block blend.
    methods = {aliases[(rule.id, tile)][1] for rule, tile in quads}
    render_method = 'blend' if 'blend' in methods else 'alpha_test_single_sided'
    instances = {}
    for rule, tile in quads:
        instances[f'{rule.id}_{tile}'] = {'texture': aliases[(rule.id, tile)][0], 'render_method': render_method,
                                          'tint_method': rule.tint, 'ambient_occlusion': 1.0,
                                          'face_dimming': True, 'isotropic': False}
    default_instance = next(iter(instances.values()))
    components = {
        'minecraft:geometry': {'identifier': geometry_id, 'bone_visibility': visible},
        'minecraft:material_instances': {'*': default_instance, **instances},
        **_out_of_the_way_components(default_instance['texture']),
    }
    if all(channel.face == 'up' for channel in channels) and hosts:
        components['minecraft:placement_filter'] = _top_placement_filter(hosts)
    return {'format_version': BLOCK_FORMAT, 'minecraft:block': {
        # Engine-placed surfaces are not items: keep them out of the creative menu and commands.
        'description': {'identifier': identifier, 'states': states,
                        'menu_category': {'category': 'none', 'is_hidden_in_commands': True}},
        'components': components}}


def _out_of_the_way_components(particle_texture):
    """No collision or selection, replaceable, lets light through, gives way to water, pistons and weather, no drops."""
    return {
        'minecraft:collision_box': False,
        'minecraft:selection_box': False,
        'minecraft:replaceable': {},
        'minecraft:light_dampening': 0,
        'minecraft:liquid_detection': {'detection_rules': [
            {'liquid_type': 'water', 'can_contain_liquid': False, 'on_liquid_touches': 'broken'}]},
        'minecraft:movable': {'movement_type': 'popped'},
        'minecraft:precipitation_interactions': {'precipitation_behavior': 'none'},
        'minecraft:destruction_particles': {'texture': particle_texture, 'particle_count': 0},
        'minecraft:loot': EMPTY_LOOT_TABLE,
    }


def _top_placement_filter(hosts):
    """A top surface goes with the block under it, even when no script sees that block go."""
    blocks = sorted(hosts)
    conditions = [{'allowed_faces': ['up'], 'block_filter': blocks[start:start + MAX_FILTER_BLOCKS]}
                  for start in range(0, len(blocks), MAX_FILTER_BLOCKS)]
    return {'conditions': conditions[:MAX_FILTER_CONDITIONS]}


def _molang_visibility(states, values):
    """Molang over a channel's block states, true when the channel holds one of values.

    states: [(name, size)], least significant first; the channel value is
    state0 + size0 * (state1 + size1 * (...)).
    """
    name, size = states[0]
    variable = f"q.block_state('{name}')"
    if len(states) == 1:
        return _molang_one_of(variable, values)
    lows_by_high = {}
    for value in values:
        lows_by_high.setdefault(value // size, set()).add(value % size)
    terms = []
    for high, lows in sorted(lows_by_high.items()):
        higher = _molang_visibility(states[1:], {high})
        if len(lows) == size:
            terms.append(higher)
        else:
            terms.append(f'({higher} && {_molang_one_of(variable, lows)})')
    return _molang_any(terms)


def _molang_one_of(variable, values):
    """Molang that is true when variable is one of the integers in values; consecutive runs become ranges."""
    terms = []
    for first, last in _consecutive_runs(sorted(set(values))):
        if first == last:
            terms.append(f'{variable} == {first}')
        else:
            terms.append(f'({variable} >= {first} && {variable} <= {last})')
    return _molang_any(terms)


def _consecutive_runs(values):
    """[(first, last)] of the runs of consecutive integers in sorted values."""
    runs = []
    for value in values:
        if runs and value == runs[-1][1] + 1:
            runs[-1][1] = value
        else:
            runs.append([value, value])
    return [(first, last) for first, last in runs]


def _molang_any(terms):
    return terms[0] if len(terms) == 1 else '(' + ' || '.join(terms) + ')'


def _geometry_definition(geometry_id, channels):
    """One bone per channel quad, named as the block's bone_visibility names it."""
    bones = []
    for number, channel in enumerate(channels):
        for rule, tile, _ in channel.quads():
            depth = rule.depth
            if tile in CORNER_TILES:
                # Corner pieces draw over edge pieces of the same rule, as Java draws them after.
                depth += TILE_STEP * (1 + CORNER_TILES.index(tile))
            material = f'{rule.id}_{tile}'
            element = _quad_element(channel.face, channel.offset, depth)
            bones.append({'name': f'c{number}_{rule.id}_{tile}', 'pivot': [0, 0, 0],
                          'cubes': [cube(element, lambda face, data, material=material: material)]})
    description = {'identifier': geometry_id, 'texture_width': 16, 'texture_height': 16,
                   'visible_bounds_width': 2, 'visible_bounds_height': 2, 'visible_bounds_offset': [0, 0.5, 0]}
    return {'format_version': '1.21.0', 'minecraft:geometry': [{'description': description, 'bones': bones}]}


def _quad_element(face, offset, depth):
    """A Java element covering one host face from the surface cell, depth pixels outside the face.

    offset lowers a top face (paths, bottom slabs), in pixels.
    """
    if face == 'up':
        y = offset + depth
        low = [0, y - QUAD_THICKNESS, 0]
        high = [16, y, 16]
    elif face == 'down':
        y = 16 - depth
        low = [0, y, 0]
        high = [16, y + QUAD_THICKNESS, 16]
    elif face == 'north':
        # The host south of the cell shows its north face at z = 16.
        z = 16 - depth
        low = [0, 0, z]
        high = [16, 16, z + QUAD_THICKNESS]
    elif face == 'south':
        # The host north of the cell shows its south face at z = 0.
        z = depth
        low = [0, 0, z - QUAD_THICKNESS]
        high = [16, 16, z]
    elif face == 'west':
        # The host east of the cell shows its west face at x = 16.
        x = 16 - depth
        low = [x, 0, 0]
        high = [x + QUAD_THICKNESS, 16, 16]
    else:
        # The host west of the cell shows its east face at x = 0.
        x = depth
        low = [x - QUAD_THICKNESS, 0, 0]
        high = [x, 16, 16]
    return {'from': low, 'to': high, 'faces': {face: {'uv': [0, 0, 16, 16]}}}


# Engine data


def _engine_data(document, rules, engine_types, cubes, states, dialect):
    """What the engine needs to place the surfaces (engine-data.json)."""
    texture_table = _texture_table(rules)
    sources, host_faces = _faces_to_scan(rules)
    all_hosts = {block for rule in rules for blocks in rule.hosts.values() for block in blocks}
    engine_blocks = set(sources) | set(host_faces) | all_hosts
    return {'format_version': 1, 'dialect': dialect,
            'rules': [_engine_rule(rule, texture_table) for rule in rules],
            'types': engine_types,
            'blocks': _engine_blocks(document, engine_blocks, texture_table),
            'cubes': sorted(cubes),
            'solid': sorted(block for block in cubes if not _matches_any(block, NOT_SOLID)),
            'shapes': _engine_shapes(rules, cubes, states),
            'sources': _faces_in_order(sources),
            'hosts': _faces_in_order(host_faces),
            'scan': sorted(set(sources) | set(host_faces))}


def _texture_table(rules):
    """{texture: index} for the textures rules match or connect to; the engine refers to them by index."""
    textures = {texture for rule in rules for field in ('matchTiles', 'connectTiles')
                for texture in rule.rule.get(field, [])}
    return {texture: number for number, texture in enumerate(sorted(textures))}


def _faces_to_scan(rules):
    """({source block: faces}, {host block: faces}): the blocks whose faces the engine checks for surfaces.

    method=overlay with connect blocks or tiles is found from the blocks it comes
    from; the other rules from the blocks they draw on.
    """
    sources = {}
    host_faces = {}
    for rule in rules:
        if rule.method == 'overlay' and rule.sources:
            for face, blocks in rule.sources.items():
                for block in blocks:
                    sources.setdefault(block, set()).add(face)
        else:
            for face, blocks in rule.hosts.items():
                for block in blocks:
                    host_faces.setdefault(block, set()).add(face)
    return sources, host_faces


def _faces_in_order(faces_by_block):
    return {block: sorted(faces, key=FACES.index) for block, faces in sorted(faces_by_block.items())}


def _engine_rule(rule, texture_table):
    source = rule.rule
    item = {'id': rule.id, 'method': rule.method, 'faces': sorted({face for face, _ in rule.slots}, key=FACES.index),
            'tiles': len(source['tiles']), 'skip': rule.skip}
    if source.get('matchTiles'):
        item['matchTiles'] = [texture_table[texture] for texture in source['matchTiles']]
    if source.get('blocks'):
        item['blocks'] = source['blocks']
    if source.get('blockMatchers'):
        item['matchers'] = source['blockMatchers']
    if source.get('connectTiles'):
        item['connectTiles'] = [texture_table[texture] for texture in source['connectTiles']]
    if source.get('connectBlocks'):
        item['connectBlocks'] = source['connectBlocks']
    for field in ENGINE_RULE_FIELDS:
        if field in source:
            item[field] = source[field]
    if rule.replaced:
        item['replaced'] = rule.replaced
    return item


def _engine_channel(channel, rule_numbers):
    return {'face': channel.face, 'offset': channel.offset,
            'options': [[rule_numbers[rule.id], values] for rule, values in channel.options],
            'states': [list(state) for state in channel.states]}


def _engine_blocks(document, blocks, texture_table):
    """Face textures of the blocks the engine reads, as indexes into the texture table.

    {block: {'faces': {face: [orientation, texture]}, 'variants'?: [{'states', 'faces':
    {face: [orientation, base texture or -1, other textures...]}}]}}
    """
    result = {}
    for block in sorted(blocks):
        faces = _engine_base_faces(document, block, texture_table)
        variants = [_engine_variant(variant, texture_table)
                    for variant in document.get('baseTextureVariants', {}).get(block, [])]
        if faces or any(variant['faces'] for variant in variants):
            result[block] = {'faces': faces, **({'variants': variants} if variants else {})}
    return result


def _engine_base_faces(document, block, texture_table):
    orientations = document.get('textureOrientations', {}).get(block, {})
    faces = {}
    for face, texture in document.get('baseTextures', {}).get(block, {}).items():
        if face not in FACES:
            continue
        index = texture_table.get(texture, -1)
        if index >= 0:
            faces[face] = [orientations.get(face, 0) or 0, index]
    return faces


def _engine_variant(variant, texture_table):
    faces = {}
    for face, textures in _variant_faces(variant).items():
        base = texture_table.get(textures[0], -1)
        others = [texture_table.get(texture, -1) for texture in textures[1:] if texture_table.get(texture, -1) >= 0]
        if base >= 0 or others:
            first_choice = (variant.get('modelChoices') or [variant])[0]
            orientation = (first_choice.get('orientations') or {}).get(face, 0) or 0
            faces[face] = [orientation, base if textures[0] else -1, *others]
    return {'states': variant.get('states', {}), 'faces': faces}


def _engine_shapes(rules, cubes, states):
    """The whole-square faces of the hosts that are not full cubes: {block: [{'states'?, 'faces'}]}."""
    shapes = {}
    for rule in rules:
        for blocks in rule.slots.values():
            for block in blocks:
                if block in cubes or block in shapes:
                    continue
                shapes[block] = [{**({'states': state_filter} if state_filter else {}), 'faces': offsets}
                                 for state_filter, offsets in _host_shapes(block, cubes, states)]
    return shapes


# Reports


def _authored_overlays(rules):
    """The pack's method=overlay rules with their hosts and sources per face (authored.json)."""
    return [{'rule': rule.id, 'filename': rule.rule.get('filename'), 'method': rule.method,
             'conditional': any(rule.rule.get(field) for field in CONDITION_FIELDS),
             'faces': {face: {'hosts': sorted(rule.hosts.get(face, ())), 'sources': sorted(rule.sources.get(face, ()))}
                       for face in sorted(rule.hosts, key=FACES.index)}}
            for rule in rules if rule.method == 'overlay']


def _type_report(identifier, surface_type):
    channels = [{'face': channel.face, 'offset_pixels': channel.offset,
                 'rules': [rule.id for rule, _ in channel.options]}
                for channel in surface_type['channels']]
    return {'block': identifier, 'kind': surface_type['kind'], 'permutations': surface_type['permutations'],
            'quads': surface_type['bones'], 'channels': channels}


def _drawn_rule_report(rule):
    entry = {'rule': rule.id, 'filename': rule.rule.get('filename'), 'method': rule.method,
             'faces': {face: sorted({offset for side, offset in rule.slots if side == face})
                       for face in sorted(rule.hosts, key=FACES.index)},
             'host_blocks': sorted({block for blocks in rule.hosts.values() for block in blocks}),
             'sources': {face: len(blocks) for face, blocks in rule.sources.items()},
             'tiles': len(rule.used), 'tint_method': rule.tint}
    if rule.replaced:
        entry['replacement_draws_it_on'] = rule.replaced
    if rule.problems:
        entry['problems'] = rule.problems
    return entry
