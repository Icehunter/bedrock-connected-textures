"""Native replacement blocks for rules that entity carriers do not draw.

Each replaced vanilla block gets one custom full-cube block that looks like the
author's rule: repeat rules keep their place in the pattern as coordinate
states, random rules as a variant state, overlay_random rules as a top layer
picked by a state. The engine swaps a vanilla block for its replacement while
players are in range and back when they leave; nothing turns back to vanilla
where players look, stand or dig. The replacement carries every vanilla state,
so swapping back restores the exact original permutation. Materials keep the
author's normal and MER/MERS maps so the blocks draw in Vibrant Visuals and ray
tracing.

Players mine, blow up and build with the replacement itself, so it carries the
vanilla gameplay (block_gameplay.py): destroy time and tool speeds, drops for
the right tool with Fortune, explosion resistance, flammability, redstone
conduction, note block sound, map color and light, and the vanilla block tags
of the block it stands for (vanilla_tags: log and wood on logs, stone on
stone...). Blocks whose vanilla behavior a custom block cannot carry (grass
spreading, ice melting, falling sand, redstone ore lighting...) are listed in
data/native-replacement.json under keep_vanilla and are never replaced; blocks
under carrier_fallback stay on entity carriers. A replaced block goes back to
vanilla only next to a block that needs the exact vanilla block (needs_vanilla:
cocoa on jungle logs, chorus on end stone...); torches, rails, carpets and the
like only need a sturdy face (attachments). Logs get engine fields from
`behaviors` (leaf decay guard, stripping with an axe) and ores their
experience. Panes and bars get a post-and-arms block.
Weighted Java models become a model state the engine picks per position, Java
multipart decorations become extra bones, and biome tints use the material
tint_method. Everything that cannot be represented is reported with a reason.

Everything written follows Mojang's block schema (format 1.26.20 and later,
checked by bedrock_schema.py after every build): tags go in minecraft:tags,
integer and boolean states are integer ranges (a boolean vanilla state is
mirrored as 0 or 1 and listed under `bools` for the engine), bone_visibility and
permutation conditions read block states only, and a geometry shows at most 64
bones and a block at most 64 material instances. Connected methods (ctm,
horizontal...) pick their tile from the neighbors, which block geometry cannot
query, so those rules stay on entity carriers.
"""
from fnmatch import fnmatchcase
import hashlib
import itertools
import json
import math
import re
from pathlib import Path
import shutil
import tempfile
import uuid

from PIL import Image

from bedrock_schema import Schemas, block_errors, culling_errors, geometry_errors, require_valid
from block_gameplay import block_tags, components as gameplay_components, loot_table, mining, needed_tool
from block_shapes import shape_components, shape_of, shaped_block, single_slab
from common import BLOCKS_FORMAT, read_json, write_json
from java_block_geometry import cube
from model_blocks import build as build_model_block
from native_connected import export_material, FACES
from terrain_pack import contained, image_path

DEFAULT_POLICY = Path(__file__).resolve().parent / 'data/native-replacement.json'
BLOCK_FORMAT = '1.26.50'
BASE_METHODS = {'repeat', 'random', 'fixed'}
OVERLAY_METHODS = {'overlay_random', 'overlay_fixed'}
RANDOM_METHODS = ('random', 'overlay_random')
# Methods whose tile depends on the neighbors: geometry can only read block states.
CONNECTED_METHODS = {'ctm', 'horizontal', 'vertical', 'horizontal+vertical', 'vertical+horizontal', 'top'}
# Bedrock limits (server/block/1.26.20 schema): bones in bone_visibility and material instances per block.
MAX_BONES = 64
MAX_MATERIALS = 64
# A random rule's pick is a block state, and a block state holds at most 16 values.
MAX_RANDOM_TILES = 16
# Bedrock warns that a world whose custom blocks have more permutations than this may load and run slowly.
WORLD_PERMUTATIONS = 65536
# Shaped blocks in sixteen dye colours, left out first when a pack passes the limit.
DYED = re.compile(r'_(concrete|wool|terracotta)_|stained_glass')
# Block permutations a replacement may have unless the policy sets max_permutations.
DEFAULT_MAX_PERMUTATIONS = 4096
SKIP = ('<skip>', '<default>')
CONDITIONS = ('biomes', 'heights', 'states', 'connectBlocks', 'connectTiles', 'connectBlockMatchers')
# World face shown by each local face of a pillar block lying along an axis.
AXIS_FACES = {
    'y': {face: face for face in FACES},
    'x': {'up': 'west', 'down': 'east', 'east': 'up', 'west': 'down', 'north': 'north', 'south': 'south'},
    'z': {'up': 'south', 'down': 'north', 'north': 'up', 'south': 'down', 'east': 'east', 'west': 'west'},
}
AXIS_ROTATION = {'y': [0, 0, 0], 'x': [0, 0, 90], 'z': [90, 0, 0]}
# Texture orientations 0-3 turn the texture by quarter turns, 4-7 mirror it
# first (engine/tiles.mjs).
TEXTURE_EDGES = [[0, 1, 2, 3], [3, 0, 1, 2], [2, 3, 0, 1], [1, 2, 3, 0],
                 [0, 3, 2, 1], [3, 2, 1, 0], [2, 1, 0, 3], [1, 0, 3, 2]]
FACE_INDEX = {'down': 0, 'up': 1, 'north': 2, 'south': 3, 'west': 4, 'east': 5}
# Faces sharing one repeat pattern per symmetry: each face, opposite pairs, or all six.
SYMMETRY = {'none': 1, 'opposite': 2, 'all': 6}
# Vanilla states that change which texture a face shows.
LOOK_STATES = ('pillar_axis', 'facing_direction')
# Java biome tints and the Bedrock material tint_method that draws them.
BIOME_TINTS = {'grass': 'grass', 'foliage': 'default_foliage'}
# The world axes each face's pattern coordinates run along.
FACE_AXES = {'down': ('x', 'z'), 'up': ('x', 'z'), 'north': ('x', 'y'), 'south': ('x', 'y'),
             'west': ('z', 'y'), 'east': ('z', 'y')}
# Where the block a needs_vanilla block needs is: under it, over it, beside it or on any side (engine/replacement.mjs).
NEED_SIDES = ('below', 'above', 'side', 'any')
# Thin planes just outside each face, carrying a face's overlay layer: (origin, size).
PLANES = {'up': ([-8, 16.01, -8], [16, 0, 16]), 'down': ([-8, -0.01, -8], [16, 0, 16]),
          'north': ([-8, 0, -8.01], [16, 16, 0]), 'south': ([-8, 0, 8.01], [16, 16, 0]),
          'west': ([-8.01, 0, -8], [0, 16, 16]), 'east': ([8.01, 0, -8], [0, 16, 16])}
# Pane pieces in Java coordinates: the broad faces of the post and each arm
# (face, segment from-to along the face, the arm state that shows it) and the
# arm boxes whose top, bottom and end show the edge texture.
PANE_ARMS = {'north': ([7, 0, 0], [9, 16, 7]), 'south': ([7, 0, 9], [9, 16, 16]),
             'west': ([0, 0, 7], [7, 16, 9]), 'east': ([9, 0, 7], [16, 16, 9])}
PANE_SEGMENTS = {
    'north': [((0, 7), 'west', True), ((7, 9), 'north', False), ((9, 16), 'east', True)],
    'south': [((0, 7), 'west', True), ((7, 9), 'south', False), ((9, 16), 'east', True)],
    'west': [((0, 7), 'north', True), ((7, 9), 'west', False), ((9, 16), 'south', True)],
    'east': [((0, 7), 'north', True), ((7, 9), 'east', False), ((9, 16), 'south', True)]}
PANE_SIDES = ['north', 'south', 'west', 'east']
REPORT_LIMITS = ['Random rules use one draw per block for all faces of that rule.',
                 'Replacement blocks need in-game validation of pillar rotation, texture turns (uv_rotation), '
                 'model part placement and overlay layer placement.',
                 'Blocks listed under attachments need in-game validation that they stay on a replacement block '
                 'when it is swapped in or out next to them.']


def load_policy(path=None):
    """data/native-replacement.json (or the policy at path), checked entry by entry."""
    policy = read_json(Path(path or DEFAULT_POLICY))
    if policy.get('format_version') != 1:
        raise ValueError('Unknown native replacement policy format')
    for kind in ('carrier_fallback', 'keep_vanilla', 'attachments'):
        for entry in policy.get(kind, []):
            if not entry.get('blocks') or not entry.get('reason'):
                raise ValueError(kind + ' entries need blocks and a reason')
    for entry in policy.get('behaviors', []):
        if not entry.get('blocks') or not entry.get('reason'):
            raise ValueError('behaviors entries need blocks and a reason')
    for entry in policy.get('needs_vanilla', []):
        if (not entry.get('blocks') or not entry.get('needs') or entry.get('side') not in NEED_SIDES
                or not entry.get('reason')):
            raise ValueError('needs_vanilla entries need blocks, needs, a side (' + ', '.join(NEED_SIDES)
                             + ') and a reason')
    tags = policy.get('vanilla_tags', {})
    unknown = set(tags.get('tags', {})) - set(tags.get('known', []))
    if unknown:
        raise ValueError('vanilla_tags names tags that are not vanilla block tags: ' + ', '.join(sorted(unknown)))
    for profile in policy.get('profiles', []):
        if (not profile.get('blocks') or 'destroy' not in profile or 'resistance' not in profile
                or 'map_color' not in profile):
            raise ValueError('Replacement profiles need blocks, destroy, resistance and map_color')
    return policy


def profile_of(block, policy):
    """The block's gameplay profile, its color family (dye, wood...) resolved by the block's name; None without one.

    A family color picks the color of the family member the name starts with.
    """
    profile = next((item for item in policy.get('profiles', []) if _matches(block, item['blocks'])), None)
    if profile is None:
        return _shaped_profile(block, policy)
    result = dict(profile)
    color = result['map_color']
    if color is not None and color in policy.get('colors', {}):
        family = policy['colors'][color]
        name = block.removeprefix('minecraft:').removeprefix('stripped_')
        # The longest member the name starts with wins: light_blue before blue.
        prefix = next((member for member in sorted(family, key=len, reverse=True) if name.startswith(member + '_')),
                      None)
        if prefix is None:
            return None
        result['map_color'] = family[prefix]
    return result


def _shaped_profile(block, policy):
    """A shaped block's profile, borrowed from its full block (policy shaped); None when the full block has none.

    oak_slab and oak_stairs borrow oak_planks, cobblestone_slab cobblestone;
    `base` names the full block where the name does not lead to it (Bedrock's
    stone_stairs are cobblestone stairs). A slab or stair drops itself and a
    double slab two slabs, whatever the full block drops.
    """
    shaped = policy.get('shaped', {})
    if not _matches(block, shaped.get('blocks', [])):
        return None
    slab = single_slab(block) or block
    stem = slab.removesuffix('_slab').removesuffix('_stairs').removesuffix('_fence').removesuffix('_wall')
    names = [shaped.get('base', {}).get(slab)] + [stem + suffix for suffix in ('_planks', 's', '_block', '')]
    base = next((name for name in names if name and profile_of(name, policy)), None)
    if base is None:
        return None
    result = {key: value for key, value in profile_of(base, policy).items() if key not in ('xp', 'fortune', 'loot')}
    result['blocks'] = [block]
    if slab != block:
        result['loot'] = [{'item': slab, 'min': 2}]
    for override in shaped.get('overrides', []):
        if _matches(block, override['blocks']):
            result.update({key: value for key, value in override.items() if key not in ('blocks', 'reason')})
    return result


def vanilla_tags_of(block, policy):
    """The vanilla Bedrock block tags of a vanilla block (policy vanilla_tags), which its replacement carries too."""
    return sorted(tag for tag, patterns in policy.get('vanilla_tags', {}).get('tags', {}).items()
                  if _matches(block, patterns))


def fallback_patterns(policy):
    """The block patterns that stay on entity carriers (policy carrier_fallback)."""
    return [pattern for entry in policy.get('carrier_fallback', []) for pattern in entry['blocks']]


def registered_permutations(definition):
    """The permutations the game registers for a block: every combination of its states' values."""
    states = definition['minecraft:block']['description'].get('states', {})
    counts = [len(spec) if isinstance(spec, list) else spec['values']['max'] - spec['values']['min'] + 1
              for spec in states.values()]
    return math.prod(counts)


def shaped_over_budget(report, other_permutations, limit=WORLD_PERMUTATIONS):
    """The shaped blocks (slabs, stairs, fences, walls) to leave out so the pack stays within the game's permutation limit.

    report: build_replacements' report; other_permutations: the pack's other
    custom blocks (overlay surfaces). Dyed blocks go first (concrete, wool and
    terracotta stairs and slabs, sixteen colours of rarely built blocks), then
    the costliest (stairs with large patterns before slabs); they stay vanilla
    blocks showing the author's base texture. Returns the vanilla block ids, or
    an empty set.
    """
    total = report['permutations'] + other_permutations
    dropped = set()
    shaped = [(cost, block) for block, (cost, shape) in report['block_permutations'].items() if shape]
    for cost, block in sorted(shaped, key=lambda item: (not DYED.search(item[1]), -item[0], item[1])):
        if total <= limit:
            break
        dropped.add(block)
        total -= cost
    return dropped


def build_replacements(document, candidates, policy, *, source, samples, key, output, passengers=None, model_blocks=(),
                       skip=()):
    """Write Replace_BP and Replace_RP for every representable block.

    candidates: {rule id: [target blocks]} for rules that should draw natively.
    passengers: {rule id: [target blocks]} for rules already drawn natively in
    another way (atlas variations); they join a block that another rule
    replaces so the replacement keeps showing them, but never cause one.
    Rules whose matchTiles name a layer texture (grass side overlay) or a Java
    model part texture (grass decals) join the blocks showing those textures.
    source: folder holding the compiled author materials (rule tiles and base faces).
    samples: bedrock-samples root (block states, ids and sounds).
    model_blocks: model_blocks.plan_all plans (blocks drawn with the author's Java
    models, leaves first); they go in the same packs, with their engine data
    under 'leaves'.
    skip: blocks left out to keep the pack within the permutation limit (shaped_over_budget).
    Returns (engine data or None, report).
    """
    samples, output = Path(samples), Path(output)
    schemas = Schemas(samples)
    game_blocks = read_json(samples / 'metadata/vanilladata_modules/mojang-blocks.json')
    known = {item['name']: item for item in game_blocks['data_items']}
    state_values = {item['name']: [entry['value'] for entry in item['values']]
                    for item in game_blocks['block_properties']}
    sounds = read_json(samples / 'resource_pack/blocks.json')
    rules = {rule['id']: rule for rule in document['rules']}
    order = {rule['id']: index for index, rule in enumerate(document['rules'])}
    full = set(document.get('fullCubeBlocks', []))
    pane_patterns = policy.get('panes', {}).get('blocks', [])
    rules_by_block = _rules_by_block(candidates)
    passengers_by_block = _rules_by_block(passengers or {})
    namespace = 'bct_' + key.replace('-', '_')
    material_prefix = 'bctr_' + hashlib.sha256(key.encode()).hexdigest()[:8] + '_'
    if output.exists():
        shutil.rmtree(output)
    bp, rp = output / 'Replace_BP', output / 'Replace_RP'
    unsupported, kept_vanilla = [], {}
    with tempfile.TemporaryDirectory(prefix='replacement-') as staging:
        materials = Materials(source, rp, material_prefix, staging)
        writer = _ReplacementWriter(document, rules, policy, materials, schemas, known, state_values, sounds,
                                    namespace, bp, rp)
        for block in sorted(rules_by_block):
            rule_ids = sorted(rules_by_block[block], key=order.get)
            reason = _not_replaceable(block, known, full, pane_patterns, policy, kept_vanilla)
            if block in skip:
                reason = (f"left out: the pack's custom blocks would pass the game's {WORLD_PERMUTATIONS} permutations; "
                          'it shows the base texture')
            if reason:
                unsupported += _unsupported(rule_ids, block, reason)
                continue
            problems = {rule_id: _rule_problem(rules[rule_id]) for rule_id in rule_ids}
            for rule_id, problem in problems.items():
                if problem:
                    unsupported.append({'rule': rule_id, 'block': block, 'reason': problem})
            rule_ids = [rule_id for rule_id in rule_ids if not problems[rule_id]]
            if not rule_ids:
                continue
            rule_ids = _with_joining_rules(rule_ids, block, document, rules, passengers_by_block, order)
            profile = profile_of(block, policy)
            if profile is None:
                unsupported += _unsupported(rule_ids, block, 'no replacement profile (destroy time, explosion '
                                                             'resistance, map color, drops) in native-replacement.json')
                continue
            try:
                writer.add(block, rule_ids, profile, pane=_matches(block, pane_patterns))
            except (ValueError, OSError) as error:
                unsupported += _unsupported(rule_ids, block, str(error))
        known_items = {item['name'] for item in
                       read_json(samples / 'metadata/vanilladata_modules/mojang-items.json')['data_items']}
        leaf_entries, model_reports, model_skipped = writer.add_model_blocks(
            model_blocks, known_items, document.get('modelTintTypes', {}))
        if writer.entries or leaf_entries:
            writer.write_pack_files(key)
    blocks = writer.entries
    open_blocks = sorted(name for name in known if _matches(name, policy.get('open_blocks', [])))
    # Opaque full cubes hide the faces next to them; next to anything else a block shows.
    solid = sorted((full & set(known)) - set(open_blocks))
    # Blocks that need an exact vanilla neighbor keep it vanilla; nothing else does.
    needs, needs_report = _needs_data(policy, known, {entry['vanilla'] for entry in blocks})
    data = None
    if blocks or leaf_entries:
        data = {'format_version': 1, 'blocks': blocks, 'open': open_blocks, 'solid': solid}
        if needs:
            data['needs'] = needs
        if leaf_entries:
            data['leaves'] = _leaf_data(leaf_entries, model_blocks, document, known)
        pane_types = sorted(name for name in known if _matches(name, policy.get('panes', {}).get('connect', [])))
        if pane_types:
            data['paneConnect'] = pane_types
        write_json(output / 'engine-data.json', data)
        # Each block was checked on its own; this also checks what links them (geometry, culling and sound references).
        require_valid(bp, rp, samples)
    replaced = sorted(writer.replaced.items(), key=lambda item: order[item[0]])
    report = {'replacement_blocks': len(blocks), 'permutations': writer.permutations,
              'block_permutations': dict(sorted(writer.block_permutations.items())),
              'materials': len(materials.atlas),
              'rules': {rule_id: sorted(targets) for rule_id, targets in replaced},
              'unsupported': unsupported, 'notes': writer.notes, 'kept_vanilla': dict(sorted(kept_vanilla.items())),
              'needs_vanilla': needs_report,
              'attachments': [{'blocks': item['blocks'], 'reason': item['reason']}
                              for item in policy.get('attachments', [])],
              'vanilla_tags': dict(sorted(writer.tagged.items())),
              'model_blocks': model_reports, 'model_blocks_not_built': model_skipped,
              'limits': list(REPORT_LIMITS)}
    return data, report


def repeat_index(location, face, width, height, symmetry='none', orientation=0):
    """Port of engine/tiles.mjs repeatIndex."""
    x, y, z = location
    step = SYMMETRY.get(symmetry, 1)
    # Faces sharing a pattern read the coordinates of the first face of their group.
    side = list(FACE_INDEX)[FACE_INDEX[face] // step * step]
    column, row = {'down': (x, -z - 1), 'up': (x, z), 'north': (-x - 1, -y), 'south': (x, -y),
                   'west': (z, -y), 'east': (-z - 1, -y)}[side]
    # Coordinates along the texture's four edges; the orientation picks which ones run across and down.
    along = [-row - 1, column, row, -column - 1]
    edges = TEXTURE_EDGES[orientation] if 0 <= orientation < 8 else TEXTURE_EDGES[0]
    return along[edges[2]] % height * width + along[edges[1]] % width


def repeat_moduli(face, width, height, symmetry='none', orientation=0):
    """(axis, modulus) pairs a repeat face reads: its column runs mod width, its row mod height."""
    step = SYMMETRY.get(symmetry, 1)
    side = list(FACE_INDEX)[FACE_INDEX[face] // step * step]
    u, v = FACE_AXES[side]
    edges = TEXTURE_EDGES[orientation] if 0 <= orientation < 8 else TEXTURE_EDGES[0]
    # along[0] and along[2] follow v; along[1] and along[3] follow u.
    row = v if edges[2] in (0, 2) else u
    column = v if edges[1] in (0, 2) else u
    return {(row, height), (column, width)}


def representative(residues):
    """Smallest non-negative value with the given residues, or None when they disagree."""
    value, modulus = 0, 1
    for divisor, remainder in residues:
        for candidate in range(value, value + modulus * divisor, modulus):
            if candidate % divisor == remainder:
                value, modulus = candidate, math.lcm(modulus, divisor)
                break
        else:
            return None
    return value


class Materials:
    """Exports each distinct (tile, layers, tint) material once into the resource pack."""

    def __init__(self, source, rp, prefix, staging):
        self.source = Path(source)
        self.rp = Path(rp)
        self.prefix = prefix
        self.staging = Path(staging)
        self.aliases = {}
        self.atlas = {}
        self.flipbooks = []
        # Render method of each exported material (opaque, alpha_test, blend).
        self.methods = {}

    def alias(self, tile, layers=(), tint=(1, 1, 1)):
        """The atlas alias of the tile with its layers on top and its color multiplied by tint."""
        key = (tile, tuple(layers), tuple(round(value, 6) for value in tint))
        if key in self.aliases:
            return self.aliases[key]
        stem = self.prefix + hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16]
        if not layers and key[2] != (1, 1, 1):
            plain = self.alias(tile)
            # A tint changes only the color; the untinted export's normal and MER maps are shared.
            # An animated material falls through to _compose, which refuses to tint it.
            if not any(item.get('atlas_tile') == plain for item in self.flipbooks):
                folder = self.rp / 'textures/blocks'
                with Image.open(folder / (plain + '.png')) as image:
                    tinted = _tinted(image.convert('RGBA'), key[2])
                tinted.save(folder / (stem + '.png'))
                descriptor = folder / (plain + '.texture_set.json')
                if descriptor.exists():
                    texture_set = read_json(descriptor)
                    texture_set['minecraft:texture_set']['color'] = stem
                    write_json(folder / (stem + '.texture_set.json'), texture_set)
                self.atlas[stem] = {'textures': 'textures/blocks/' + stem}
                self.aliases[key] = stem
                self.methods[stem] = self.methods[plain]
                return stem
        if layers or key[2] != (1, 1, 1):
            composed = self._compose(stem, tile, layers, key[2])
            name, animation, method = export_material(self.staging, composed, self.rp, stem,
                                                      texture_folder='textures/blocks', renderer='vv')
        else:
            name, animation, method = export_material(self.source, tile, self.rp, stem,
                                                      texture_folder='textures/blocks', renderer='vv')
        self.atlas[stem] = {'textures': name}
        if animation:
            self.flipbooks.append(animation)
        self.aliases[key] = stem
        self.methods[stem] = method
        return stem

    def _compose(self, stem, tile, layers, tint):
        """Writes layered and tinted channels for one material into the staging pack; returns its path there."""
        base = self._channel_images(tile)
        size = base['color'].size
        for layer in layers:
            top = self._channel_images(layer)
            if top['color'].size != size:
                top = {name: image.resize(size, Image.NEAREST) for name, image in top.items()}
            mask = top['color'].getchannel('A')
            base['color'] = Image.alpha_composite(base['color'], top['color'])
            for name, image in top.items():
                if name == 'color':
                    continue
                # A map the base lacks starts black, or flat for the normal map.
                empty = (0, 0, 0, 255) if name != 'normal' else (128, 128, 255, 255)
                below = base.get(name, Image.new('RGBA', size, empty))
                base[name] = Image.composite(image, below, mask)
        if tuple(tint) != (1, 1, 1):
            base['color'] = _tinted(base['color'], tint)
        target = self.staging / 'replacement' / (stem + '.png')
        target.parent.mkdir(parents=True, exist_ok=True)
        base['color'].save(target)
        descriptor = {'color': stem}
        for name, image in base.items():
            if name == 'color':
                continue
            image.save(target.with_name(stem + '_' + name + '.png'))
            descriptor[name] = stem + '_' + name
        if len(descriptor) > 1:
            write_json(target.with_suffix('.texture_set.json'),
                       {'format_version': '1.21.30', 'minecraft:texture_set': descriptor})
        return 'replacement/' + stem + '.png'

    def _channel_images(self, path):
        """{texture set channel: RGBA image} of a source material; a uniform channel value becomes an image."""
        source = image_path(contained(self.source, path))
        if source is None:
            raise ValueError('Missing material: ' + path)
        images = {'color': Image.open(source).convert('RGBA')}
        descriptor = source.with_suffix('.texture_set.json')
        if descriptor.exists():
            for channel, value in read_json(descriptor)['minecraft:texture_set'].items():
                if channel == 'color':
                    continue
                if isinstance(value, str) and value.startswith('#'):
                    value = [int(value[index:index + 2], 16) for index in range(1, len(value), 2)]
                if isinstance(value, str):
                    images[channel] = Image.open(source.parent / (value + '.png')).convert('RGBA')
                else:
                    images[channel] = Image.new('RGBA', images['color'].size, tuple(value) + (255,) * (4 - len(value)))
        if Path(str(source) + '.mcmeta').exists():
            raise ValueError('Animated textures cannot be layered or tinted for replacement blocks: ' + path)
        return images


def _tinted(image, tint):
    """An RGBA image with its color channels multiplied by tint; alpha stays."""
    red, green, blue, alpha = image.split()
    red, green, blue = (channel.point(lambda value, factor=factor: round(value * factor))
                        for channel, factor in zip((red, green, blue), tint))
    return Image.merge('RGBA', (red, green, blue, alpha))


class _ReplacementWriter:
    """Writes each replacement block's definition, loot, geometry and culling, and collects its engine data."""

    def __init__(self, document, rules, policy, materials, schemas, known, state_values, sounds, namespace, bp, rp):
        self.document = document
        self.rules = rules
        self.policy = policy
        self.materials = materials
        self.schemas = schemas
        self.known = known
        self.state_values = state_values
        self.sounds = sounds
        self.namespace = namespace
        self.bp = bp
        self.rp = rp
        # Engine data per replaced block, the resource pack's blocks.json sounds, {rule id: replaced blocks},
        # report notes, {block: vanilla tags its replacement carries} and the permutations written.
        self.entries = []
        self.terrain_sounds = {}
        self.replaced = {}
        self.notes = []
        self.tagged = {}
        self.permutations = 0
        # {vanilla block: (permutations the game registers for its replacement, its shape or None)}
        self.block_permutations = {}

    def add(self, block, rule_ids, profile, pane):
        """Writes the replacement of one block; ValueError or OSError says why it cannot be replaced."""
        vanilla_states = [prop['name'] for prop in self.known[block].get('properties', [])]
        mirror = {name: 'bct:' + name.split(':')[-1] for name in vanilla_states}
        transparent = bool(profile.get('transparent'))
        stem = 'r_' + block.removeprefix('minecraft:')
        identifier = self.namespace + ':' + stem
        block_rules = [self.rules[rule_id] for rule_id in rule_ids]
        build_look = self._pane_look if pane else self._shaped_look if shape_of(block) else self._cube_look
        states, components, permutations, files, entry_extra, plan = build_look(
            block, block_rules, vanilla_states, mirror, transparent, stem)
        loot_path = f'loot_tables/bct/{self.namespace}/{stem}.json'
        components.update({
            'minecraft:light_dampening': profile.get('light_dampening', 15),
            'minecraft:destructible_by_mining': mining(profile),
            'minecraft:loot': loot_path,
            'minecraft:display_name': self.known[block].get('serialization_id', 'tile.' + stem) + '.name'})
        components.update(gameplay_components(profile, transparent))
        if profile['map_color']:
            components['minecraft:map_color'] = profile['map_color']
        # The tool tag from the profile and the vanilla tags of the block it stands for.
        vanilla_tags = vanilla_tags_of(block, self.policy)
        tags = sorted(set(block_tags(profile)) | set(vanilla_tags))
        if tags:
            components['minecraft:tags'] = tags
        if vanilla_tags:
            self.tagged[block] = vanilla_tags
        if 'friction' in profile:
            components['minecraft:friction'] = profile['friction']
        if 'light_emission' in profile:
            components['minecraft:light_emission'] = profile['light_emission']
        description = {'identifier': identifier, 'menu_category': {'category': 'none', 'is_hidden_in_commands': True}}
        if states:
            description['states'] = states
        definition = {'format_version': BLOCK_FORMAT, 'minecraft:block': {
            'description': description, 'components': components, 'permutations': permutations}}
        self._check_schema(definition, files)
        for path, written in files.items():
            write_json(path, written)
        write_json(self.bp / f'blocks/{stem}.json', definition)
        write_json(self.bp / loot_path, loot_table(block, profile))
        vanilla_sound = self.sounds.get(block.removeprefix('minecraft:'), {}).get('sound')
        if vanilla_sound:
            self.terrain_sounds[identifier] = {'sound': vanilla_sound}
        cost = registered_permutations(definition)
        self.permutations += cost
        self.block_permutations[block] = (cost, shape_of(block) if not pane else None)
        entry = self._engine_entry(block, identifier, mirror, vanilla_states, plan, entry_extra, transparent, profile)
        self.entries.append(entry)
        for rule_id in rule_ids:
            self.replaced.setdefault(rule_id, []).append(block)

    def add_model_blocks(self, model_blocks, known_items, tints):
        """Writes the blocks drawn with the author's Java models (model_blocks.build) into the same packs.

        Returns (engine entries, reports, blocks not built with the reason).
        """
        entries, reports, skipped = [], [], []
        for item in model_blocks:
            files = {'bp': self.bp, 'rp': self.rp, 'out': {}}
            try:
                game_block = self.known[item['bedrock']]
                display_name = game_block.get('serialization_id', 'tile.' + item['bedrock'].split(':')[1]) + '.name'
                definition, entry, report, sound = build_model_block(
                    item, materials=self.materials, namespace=self.namespace, schemas=self.schemas,
                    known_items=known_items, sounds=self.sounds, display_name=display_name, tints=tints, files=files)
            except (ValueError, OSError, KeyError) as error:
                skipped.append({'block': item['bedrock'], 'reason': str(error)})
                continue
            for path, written in files['out'].items():
                write_json(path, written)
            if sound:
                self.terrain_sounds[entry['block']] = {'sound': sound}
            self.permutations += registered_permutations(definition)
            entries.append(entry)
            reports.append(report)
        return entries, reports, skipped

    def write_pack_files(self, key):
        """The atlas, flipbooks, block sounds and manifests of Replace_BP and Replace_RP."""
        write_json(self.rp / 'textures/terrain_texture.json',
                   {'resource_pack_name': self.namespace, 'texture_name': 'atlas.terrain', 'padding': 8,
                    'num_mip_levels': 4, 'texture_data': self.materials.atlas})
        if self.materials.flipbooks:
            write_json(self.rp / 'textures/flipbook_textures.json', self.materials.flipbooks)
        write_json(self.rp / 'blocks.json', {'format_version': BLOCKS_FORMAT, **self.terrain_sounds})
        for kind, folder, module in (('bp', self.bp, 'data'), ('rp', self.rp, 'resources')):
            header = {'name': key + ' replacement blocks', 'description': 'Native replacement blocks.',
                      'uuid': str(_pack_uuid(key + '/' + kind)), 'version': [1, 0, 0],
                      'min_engine_version': [1, 26, 50]}
            modules = [{'type': module, 'uuid': str(_pack_uuid(key + '/' + kind + '/module')), 'version': [1, 0, 0]}]
            write_json(folder / 'manifest.json', {'format_version': 2, 'header': header, 'modules': modules})

    def _instance(self, alias, transparent, layer=False, tint=None, shade=True):
        """A material instance for an exported material; layers are cut out or blended over the face below."""
        method = self.materials.methods[alias]
        if layer:
            method = 'blend' if method == 'blend' else 'alpha_test'
        elif method != 'opaque' and not transparent:
            raise ValueError('a face material is see-through on a block that is not')
        result = {'texture': alias, 'render_method': method, 'ambient_occlusion': 1.0 if shade else 0.0,
                  'face_dimming': shade}
        if isinstance(tint, str):
            result['tint_method'] = tint
        return result

    def _face_material(self, look):
        """The material of a face look: its base tile with its layers, a fixed tint baked in."""
        tint = look['tint'] if isinstance(look['tint'], list) else (1, 1, 1)
        return self.materials.alias(look['base'], look['layers'], tint)

    def _pane_look(self, block, block_rules, vanilla_states, mirror, transparent, stem):
        def pane_instance(alias):
            return self._instance(alias, transparent)

        built = _build_pane(self.document, block, block_rules, vanilla_states, mirror, self.materials,
                            pane_instance, self.namespace, stem, self.rp, transparent)
        states = {mirror[name]: _state_values(self.state_values[name]) for name in vanilla_states}
        return states, built['components'], built['permutations'], dict(built['files']), {'pane': built['pane']}, None

    def _cube_look(self, block, block_rules, vanilla_states, mirror, transparent, stem):
        """(states, components, permutations, files, engine entry fields, plan) of a full-cube replacement."""
        plan = self._plan_within_limit(block, block_rules, vanilla_states)
        oriented = self._oriented_faces(plan, mirror)
        # Java multipart decorations: one bone per model part, shown by its group state; the
        # tile of each face is a per-permutation material, so tile picks add no bones.
        decals, decal_faces, decal_visibility = _decals(plan)
        permutations, overlay_faces = self._permutations(plan, block, mirror, transparent, decal_faces)
        files = {}
        geometry = 'minecraft:geometry.full_block'
        if overlay_faces or transparent or oriented or decals:
            geometry_id = 'geometry.' + self.namespace + '.' + stem
            culling = self.namespace + ':' + stem + '_culling'
            plain = [face for face in FACES if face not in oriented]
            model, parts = _replacement_geometry(geometry_id, plain, sorted(overlay_faces),
                                                 {face: sorted(turns) for face, turns in oriented.items()}, decals)
            files[self.rp / f'models/blocks/{self.namespace}_{stem}.geo.json'] = model
            files[self.rp / f'block_culling/{self.namespace}_{stem}.json'] = _culling_rules(culling, parts, transparent)
            for permutation in permutations:
                instances = permutation['components']['minecraft:material_instances']
                for face in overlay_faces:
                    below = instances.get(face, instances['*'])
                    instances.setdefault('layer_' + face, {**below, 'render_method': 'alpha_test'})
                _uniform_render_method(instances, transparent)
            visibility = self._bone_visibility(plan, mirror, oriented, overlay_faces, decal_visibility)
            if len(visibility) > MAX_BONES:
                raise ValueError(f"{len(visibility)} switchable bones exceed Bedrock's limit of {MAX_BONES}")
            geometry = {'identifier': geometry_id, 'culling': culling}
            if visibility:
                geometry['bone_visibility'] = visibility
        largest = max(len(permutation['components']['minecraft:material_instances']) for permutation in permutations)
        if largest > MAX_MATERIALS:
            raise ValueError(f"{largest} material instances exceed Bedrock's limit of {MAX_MATERIALS}")
        states = self._cube_states(plan, vanilla_states, mirror)
        components = {'minecraft:geometry': geometry,
                      'minecraft:material_instances': permutations[0]['components']['minecraft:material_instances'],
                      'minecraft:collision_box': True, 'minecraft:selection_box': True}
        models = _model_states(plan)
        return states, components, permutations, files, ({'models': models} if models else {}), plan

    def _shaped_look(self, block, block_rules, vanilla_states, mirror, transparent, stem):
        """(states, components, permutations, files, engine entry fields, plan) of a shaped replacement (a slab, stair, fence or wall).

        Each face shows what a full block in its place would show; the geometry
        keeps the parts the shape state shows, and collision and selection follow it.
        """
        shape = shape_of(block)
        plan = self._plan_within_limit(block, block_rules, vanilla_states)
        if self._oriented_faces(plan, mirror):
            raise ValueError('turned textures on a shaped block')
        if plan['groups']:
            raise ValueError('model parts on a shaped block')
        permutations, overlay_faces = self._permutations(plan, block, mirror, transparent, [])
        if overlay_faces:
            raise ValueError('overlay layers on a shaped block')
        largest = max(len(permutation['components']['minecraft:material_instances']) for permutation in permutations)
        if largest > MAX_MATERIALS:
            raise ValueError(f"{largest} material instances exceed Bedrock's limit of {MAX_MATERIALS}")
        geometry_id = 'geometry.' + self.namespace + '.' + stem
        culling = self.namespace + ':' + stem + '_culling'
        built = shaped_block(geometry_id, shape, mirror)
        files = {self.rp / f'models/blocks/{self.namespace}_{stem}.geo.json': built['geometry'],
                 self.rp / f'block_culling/{self.namespace}_{stem}.json':
                     _culling_rules(culling, built['parts'], transparent)}
        instances = permutations[0]['components']['minecraft:material_instances']
        # Boxes depend only on the shape states; these permutations add them to whichever look permutation matches.
        permutations += built['permutations']
        components = {'minecraft:geometry': {'identifier': geometry_id, 'culling': culling,
                                             'bone_visibility': built['visibility']},
                      'minecraft:material_instances': instances, **built['boxes'],
                      **shape_components(shape, block)}
        states = self._cube_states(plan, vanilla_states, mirror)
        models = _model_states(plan)
        entry = {'shape': shape, **({'models': models} if models else {})}
        return states, components, permutations, files, entry, plan

    def _plan_within_limit(self, block, block_rules, vanilla_states):
        """The block's plan; the weighted Java model turns are dropped when they exceed the permutation limit."""
        limit = self.policy.get('max_permutations', DEFAULT_MAX_PERMUTATIONS)
        plan = _plan_block(self.document, block, block_rules, vanilla_states)
        if self._permutation_count(plan, vanilla_states) > limit and plan['choices']:
            plan = _plan_block(self.document, block, block_rules, vanilla_states, single_choice=True)
            self.notes.append({'block': block,
                               'note': 'weighted Java model turns dropped: they exceed the permutation limit'})
        if not plan['combos']:
            raise ValueError('no consistent state combination')
        count = self._permutation_count(plan, vanilla_states)
        if count > limit:
            raise ValueError(f'{count} block permutations exceed the limit of {limit}')
        return plan

    def _permutation_count(self, plan, vanilla_states):
        """Every combination of the plan's combos, the other vanilla states and the decoration choices."""
        vanilla = math.prod(len(self.state_values[name]) for name in vanilla_states if name != plan['state'])
        decorations = math.prod(len(group.get('modelChoices', [])) for group in plan['groups'])
        return len(plan['combos']) * vanilla * decorations

    def _oriented_faces(self, plan, mirror):
        """{face: {texture orientation: [look conditions]}} for faces whose texture turns between looks.

        Weighted Java model turns and glazed terracotta's facing turn a face's
        texture; each orientation gets its own bone.
        """
        first = plan['combos'][0]
        oriented = {}
        for local in FACES:
            turns = {}
            for look_key in plan['looks']:
                look = _face_look(plan, self.document, look_key, local, first[2], first[3])
                condition = ' && '.join(_look_condition(plan, mirror, look_key)) or '1.0'
                turns.setdefault(look['orientation'], []).append(condition)
            if set(turns) != {0}:
                oriented[local] = turns
        return oriented

    def _permutations(self, plan, block, mirror, transparent, decal_faces):
        """One permutation per combo with the material instance of every face, layer and decoration face.

        Returns (permutations, faces with an overlay layer).
        """
        permutations, overlay_faces = [], set()
        for look_key, coordinate_values, location, picks in plan['combos']:
            instances = {}
            for local in FACES:
                look = _face_look(plan, self.document, look_key, local, location, picks)
                if len(look['overlays']) > 1:
                    raise ValueError('more than one overlay layer on a face')
                instances[local] = self._instance(self._face_material(look), transparent, tint=look['tint'])
                if look['overlays']:
                    tint = look['tint'] if isinstance(look['tint'], list) else (1, 1, 1)
                    overlay = self.materials.alias(look['overlays'][0], (), tint)
                    instances['layer_' + local] = self._instance(overlay, transparent, layer=True,
                                                                 tint=look['overlay_tint'] or look['tint'])
                    overlay_faces.add(local)
            for material_name, part, item in decal_faces:
                tint = _tint_of(self.document, block, plan['logical'], item.get('tintIndex', -1))
                tile = _decal_tile(plan, item['texture'], coordinate_values)
                alias = self.materials.alias(tile, (), tint if isinstance(tint, list) else (1, 1, 1))
                instances[material_name] = self._instance(alias, transparent, layer=True, tint=tint,
                                                          shade=part.get('shade', True))
            _uniform_render_method(instances, transparent)
            instances['*'] = next(iter(instances.values()))
            components = {'minecraft:material_instances': instances}
            if plan['turns']:
                components['minecraft:transformation'] = {'rotation': AXIS_ROTATION[look_key[0]]}
            permutations.append({'condition': _condition(plan, mirror, look_key, coordinate_values, picks),
                                 'components': components})
        return permutations, overlay_faces

    def _bone_visibility(self, plan, mirror, oriented, overlay_faces, decal_visibility):
        """When each switchable bone shows.

        Decorations show by their group state, orientation bones by look, and
        overlay layers by look and overlay pick.
        """
        visibility = dict(decal_visibility)
        for face, turns in oriented.items():
            for orientation, terms in turns.items():
                visibility[f'{face}_o{orientation}'] = _any_of(terms)
        # Whether a layer shows depends only on the look and the overlay picks.
        overlay_states = [index for index in plan['drawn_randoms']
                          if self.rules[plan['random_states'][index]['rule']]['method'] in OVERLAY_METHODS]
        for face in overlay_faces:
            cases = {}
            for look_key, _, location, picks in plan['combos']:
                terms = _look_condition(plan, mirror, look_key) + [
                    _state_term(plan['random_states'][index]['state'], picks[index]) for index in overlay_states]
                shown_here = bool(_face_look(plan, self.document, look_key, face, location, picks)['overlays'])
                cases.setdefault(' && '.join(terms) or '1.0', shown_here)
            shown = [case for case, visible in cases.items() if visible]
            if len(shown) == len(cases):
                visibility['layer_' + face] = '1.0'
            elif shown:
                visibility['layer_' + face] = ' || '.join(f'({case})' for case in shown)
            else:
                visibility['layer_' + face] = '0.0'
        return visibility

    def _cube_states(self, plan, vanilla_states, mirror):
        """The mirrored vanilla states, then the coordinate, random pick, model and decoration group states."""
        states = {mirror[name]: _state_values(self.state_values[name]) for name in vanilla_states}
        for item in plan['coordinates']:
            states[item['state']] = _state_values(range(item['mod']))
        for item in plan['random_states']:
            states[item['state']] = _state_values(range(item['count']))
        if plan['choices']:
            states['bct:m'] = _state_values(range(len(plan['choices']['weights'])))
        for index, group in enumerate(plan['groups']):
            states[f'bct:g{index}'] = _state_values(range(len(group.get('modelChoices', []))))
        return states

    def _check_schema(self, definition, files):
        problems = block_errors(definition, self.schemas)
        for path, written in files.items():
            if 'block_culling' in path.parts:
                problems += culling_errors(written, self.schemas)
            else:
                problems += geometry_errors(written)
        if problems:
            raise ValueError("fails Mojang's block schema: " + '; '.join(problems[:5]))

    def _engine_entry(self, block, identifier, mirror, vanilla_states, plan, entry_extra, transparent, profile):
        """The engine's data for a replaced block: mirrored states, pattern axes, random picks and behaviors."""
        entry = {'vanilla': block, 'block': identifier, 'mirror': mirror, 'axes': [], 'random': []}
        bools = [name for name in vanilla_states if all(isinstance(value, bool) for value in self.state_values[name])]
        if bools:
            entry['bools'] = bools
        if plan:
            entry['axes'] = [[item['state'], item['axis'], item['mod']] for item in plan['coordinates']]
            entry['random'] = [self._random_entry(item) for item in plan['random_states']]
        entry.update(entry_extra)
        if transparent:
            entry['open'] = True
        if profile.get('xp'):
            # Experience only for the right tool (and never for Silk Touch, which the engine checks).
            entry['xp'] = list(profile['xp'])
            needed = needed_tool(profile)
            if needed:
                entry['tool'] = {'all': needed[0], 'any': needed[1]}
        entry.update(_behavior_of(block, self.policy, self.known))
        return entry

    def _random_entry(self, random_state):
        """How the engine draws one random rule's pick (the rule's first face, loops, symmetry and weights)."""
        rule = self.rules[random_state['rule']]
        return {'state': random_state['state'], 'count': random_state['count'],
                'face': (rule.get('faces') or FACES)[0],
                'loops': rule.get('randomLoops', 0),
                'symmetry': rule.get('symmetry', 'none'),
                'weights': rule.get('weights', [1] * random_state['count'])}


def _matches(block, patterns):
    return any(fnmatchcase(block, pattern) for pattern in patterns)


def _fallback_reason(block, policy):
    return next((entry['reason'] for entry in policy.get('carrier_fallback', []) if _matches(block, entry['blocks'])),
                None)


def _keep_vanilla_reason(block, policy):
    """Why a block is never replaced (vanilla behavior a custom block cannot carry), or None."""
    return next((entry['reason'] for entry in policy.get('keep_vanilla', []) if _matches(block, entry['blocks'])), None)


def _rules_by_block(targets_by_rule):
    """{block: [rule ids]} from {rule id: [target blocks]}."""
    by_block = {}
    for rule_id, targets in targets_by_rule.items():
        for block in targets:
            by_block.setdefault(block, []).append(rule_id)
    return by_block


def _unsupported(rule_ids, block, reason):
    return [{'rule': rule_id, 'block': block, 'reason': reason} for rule_id in rule_ids]


def _not_replaceable(block, known, full, pane_patterns, policy, kept_vanilla):
    """Why a block gets no replacement whatever its rules, or None; blocks kept vanilla are recorded in kept_vanilla."""
    if block not in known:
        return 'not a Bedrock block id (a Java name; its Bedrock block is listed separately when the rule draws on it)'
    reason = _keep_vanilla_reason(block, policy)
    if reason:
        kept_vanilla[block] = reason
        return 'stays vanilla: ' + reason
    if block not in full and not _matches(block, pane_patterns) and not shape_of(block):
        return 'not a full cube'
    return _fallback_reason(block, policy)


def _with_joining_rules(rule_ids, block, document, rules, passengers_by_block, order):
    """The block's rules plus the drawable rules that join it, in document order.

    Passengers join, and so do rules whose matchTiles name a layer or model
    part texture the block shows.
    """
    extra = list(passengers_by_block.get(block, []))
    info = _block_looks(document, block)
    if not isinstance(info, str):
        layer_textures, part_textures = _look_textures(info)
        extra += [rule['id'] for rule in document['rules']
                  if set(rule.get('matchTiles', [])) & (layer_textures | part_textures)]
    return sorted(set(rule_ids + [rule_id for rule_id in extra if not _rule_problem(rules[rule_id])]), key=order.get)


def _needs_data(policy, known, replaced):
    """Engine `needs` entries and the report of the policy's needs_vanilla list.

    A block listed under needs_vanilla needs the exact vanilla block next to it
    (cocoa on a jungle log): the engine keeps a replaceable block vanilla while
    such a block is next to it on that side. known: Bedrock block names;
    replaced: the vanilla blocks this pack replaces (only those reach the engine).
    """
    entries, report = [], []
    for item in policy.get('needs_vanilla', []):
        blocks = sorted(name for name in known if _matches(name, item['blocks']))
        needed = sorted(name for name in known if _matches(name, item['needs']))
        kept = [name for name in needed if name in replaced]
        report.append({'blocks': blocks, 'needs': needed, 'side': item['side'], 'reason': item['reason'],
                       'keeps_vanilla': kept})
        if blocks and kept:
            entries.append({'blocks': blocks, 'needs': kept, 'side': item['side']})
    return entries, report


def _behavior_of(block, policy, known):
    """Engine fields for a block from the policy's behaviors: the leaf decay guard and stripping for logs."""
    fields = {}
    for item in policy.get('behaviors', []):
        if not _matches(block, item['blocks']):
            continue
        unknown = set(item) - {'blocks', 'reason', 'leaf_guard', 'cost', 'strip'}
        if unknown:
            raise ValueError('unknown behavior fields: ' + ', '.join(sorted(unknown)))
        if 'leaf_guard' in item:
            guard = item['leaf_guard']
            fields['leafGuard'] = {'radius': guard['radius'],
                                   'leaves': sorted(name for name in known if _matches(name, guard['leaves']))}
        if 'cost' in item:
            fields['cost'] = item['cost']
        if item.get('strip'):
            # An axe turns a log into its stripped log with the same states (pillar_axis), as in vanilla.
            name = block.split(':', 1)[1]
            target = 'minecraft:stripped_' + name
            if (not name.startswith('stripped_') and target in known
                    and [prop['name'] for prop in known[target].get('properties', [])]
                    == [prop['name'] for prop in known[block].get('properties', [])]):
                fields['strip'] = target
    return fields


def _leaf_data(entries, plans, document, known):
    """Engine data for leaves drawn with the author's models.

    The blocks, what counts as leaves and logs, and the decay rules.

    Logs and leaves are the policy's patterns plus Java's #minecraft:logs and
    #minecraft:leaves tags as the author's pack resolves them (the distance that
    picks a pack's inner or outer leaf model counts through those).
    """
    settings = next(item['settings'] for item in plans if item['behavior'] == 'leaves')
    decay = settings['decay']
    leaves = ({name for name in known if _matches(name, decay['leaves'])}
              | {name for name in document.get('leafDistanceLeaves', []) if name in known})
    logs = ({name for name in known if _matches(name, decay['logs'])}
            | {name for name in document.get('leafDistanceLogs', []) if name in known})
    return {'blocks': entries, 'leaves': sorted(leaves), 'logs': sorted(logs),
            'decay': {'bedrockDistance': decay['bedrock_distance'], 'javaDistance': decay['java_distance'],
                      'logRadius': decay['log_update_radius'], 'leafRadius': decay['leaf_update_radius']}}


def _rule_problem(rule):
    """Why a rule cannot draw on a replacement block, or None."""
    if any(rule.get(field) for field in CONDITIONS):
        return 'biome, height, state or neighbor-connection filters need the runtime renderer'
    if rule['method'] in CONNECTED_METHODS:
        return (f'method={rule["method"]} picks its tile from the neighbors; Bedrock block geometry reads only '
                'block states (query.block_state) and shows at most 64 bones, so it stays on entity carriers')
    if rule['method'] not in BASE_METHODS | OVERLAY_METHODS:
        return f'method={rule["method"]} has no native block form'
    if rule['method'] in RANDOM_METHODS and len(rule['tiles']) > MAX_RANDOM_TILES:
        return 'more than 16 random tiles'
    return None


def _rule_applies(rule, block, logical, face, texture, states):
    """Whether a rule draws on this face of the block (by face, texture, block id and block state matchers)."""
    if rule.get('faces') and face not in rule['faces']:
        return False
    if rule.get('matchTiles') and texture not in rule['matchTiles']:
        return False
    ids = {block, logical}
    if rule.get('blocks') and not ids.intersection(rule['blocks']):
        return False
    if rule.get('blockMatchers'):
        return any(clause['block'] in ids and all(str(states.get(name)) in [str(value) for value in values]
                                                  for name, values in clause.get('states', {}).items())
                   for clause in rule['blockMatchers'])
    return True


def _look_of(entry, states):
    """A look (faces, orientations, layers, tint indices, states) of a binding entry; None without six faces."""
    faces = entry.get('faces', {})
    if set(faces) != set(FACES):
        return None
    return {'faces': faces, 'orientations': {face: entry.get('orientations', {}).get(face, 0) for face in FACES},
            'layers': entry.get('faceLayers', {}), 'tints': entry.get('tintIndices', {}), 'states': states}


def _block_looks(document, block):
    """Every look of a block, or a reason string.

    Returns {'state': the vanilla state that changes the look (pillar_axis or
    facing_direction) or None, 'looks': {(state value, model choice): look},
    'choices': {'weights', 'selection'} for weighted Java models or None,
    'groups': weighted Java multipart groups drawn as extra model parts}.
    """
    variants = document.get('baseTextureVariants', {}).get(block)
    if not variants:
        faces = document.get('baseTextures', {}).get(block)
        if not faces or set(faces) != set(FACES):
            return 'no texture for every face'
        look = _look_of({'faces': faces, 'orientations': document.get('textureOrientations', {}).get(block, {})}, {})
        return {'state': None, 'looks': {(None, 0): look}, 'choices': None, 'groups': []}
    names, looks, choices, groups, counts = set(), {}, None, None, set()
    for variant in variants:
        states = variant.get('states', {})
        if states.get('bct:snowy') is True:
            continue
        look_states = {name: value for name, value in states.items() if not name.startswith('bct:')}
        if set(look_states) - set(LOOK_STATES) or len(look_states) > 1:
            return 'texture depends on block state ' + ', '.join(sorted(set(look_states) - {'pillar_axis'}))
        name, value = next(iter(look_states.items()), (None, None))
        names.add(name)
        options = variant.get('modelChoices', [variant])
        if any(option.get('modelParts') for option in options):
            return 'model has parts beyond one cube'
        if len(options) > 1:
            selection = {'weights': [option.get('weight', 1) for option in options],
                         'selection': variant.get('modelSelection')}
            if choices not in (None, selection):
                return 'weighted models differ between block states'
            choices = selection
        counts.add(len(options))
        if groups is not None and groups != variant.get('modelPartsGroups', []):
            return 'model parts differ between block states'
        groups = variant.get('modelPartsGroups', [])
        for index, option in enumerate(options):
            look = _look_of(option, look_states)
            if look is None:
                return 'no texture for every face'
            looks[(value, index)] = look
    if not looks:
        return 'no plain block variant'
    if len(names) > 1:
        return 'mixed axis and plain variants' if 'pillar_axis' in names else 'mixed block state variants'
    if len(counts) > 1:
        return 'weighted models differ between block states'
    state = names.pop()
    if state == 'pillar_axis' and {key[0] for key in looks} != {'x', 'y', 'z'}:
        return 'pillar variants do not cover every axis'
    return {'state': state, 'looks': looks, 'choices': choices, 'groups': groups or []}


def _look_textures(info):
    """(layer textures, model part textures) a block's looks show besides their faces."""
    layers, parts = set(), set()
    for look in info['looks'].values():
        for items in look['layers'].values():
            layers.update(layer['texture'] for layer in items)
    for group in info['groups']:
        for choice in group.get('modelChoices', []):
            for part in choice.get('modelParts', []):
                parts.update(face['texture'] for face in part.get('faces', {}).values())
    return layers, parts


def _tint_of(document, block, logical, tint_index):
    """Fixed RGB multiplier for a face, [1,1,1] for none, or the Bedrock tint_method of a biome tint."""
    custom = block in set(document.get('customTintBlocks', []))
    if tint_index < 0 and not custom:
        return [1, 1, 1]
    kind = document.get('modelTintTypes', {}).get(logical, document.get('modelTintTypes', {}).get(block))
    if kind is None:
        return [1, 1, 1]
    if isinstance(kind, list) and len(kind) == 3:
        return [float(value) for value in kind]
    if kind in BIOME_TINTS:
        return BIOME_TINTS[kind]
    raise ValueError('tint ' + str(kind) + ' has no Bedrock tint method')


def _plan_block(document, block, rules, vanilla_states=(), single_choice=False):
    """Look of one replacement block: its states and what each face shows for every state combination.

    rules: representable rules targeting the block, in document order; rules
    whose matchTiles name a layer or model part texture draw on those.
    single_choice keeps one weighted Java model (the unturned one). Returns a
    plan dict or raises ValueError with the reason the block cannot be replaced.
    """
    logical = document.get('nativeBlockJavaIds', {}).get(block, block)
    info = _block_looks(document, block)
    if isinstance(info, str):
        raise ValueError(info)
    looks, state, choices = dict(info['looks']), info['state'], info['choices']
    if single_choice and choices:
        looks, choices = _unturned_looks(looks), None
    if 'pillar_axis' in vanilla_states and state is None:
        # Bedrock turns the whole block for pillar_axis; each world face shows the local face turned onto it.
        looks, state = _looks_per_axis(looks), 'pillar_axis'
    turns = state == 'pillar_axis'
    if state and state not in vanilla_states:
        raise ValueError('axis variants without a pillar_axis state' if turns
                         else 'look depends on ' + state + ' which the block does not have')
    layer_textures, part_textures = _look_textures(info)
    face_textures = {texture for look in looks.values() for texture in look['faces'].values()}
    randoms, decal_rules = [], []
    for rule in rules:
        matched = set(rule.get('matchTiles', []))
        if matched & part_textures and not matched & (face_textures | layer_textures):
            decal_rules.append(rule)
        elif rule['method'] in RANDOM_METHODS:
            randoms.append(rule)
    plan = {'block': block, 'logical': logical, 'rules': rules, 'looks': looks, 'state': state, 'turns': turns}
    coordinates = _axis_states(_repeat_axes(plan))
    random_states = [{'state': f'bct:r{index}', 'rule': rule['id'], 'count': len(rule['tiles'])}
                     for index, rule in enumerate(randoms)]
    drawn = list(range(len(random_states)))
    plan.update({'coordinates': coordinates, 'random_states': random_states, 'randoms': randoms,
                 'combos': _combos(looks, coordinates, random_states, drawn), 'choices': choices,
                 'groups': info['groups'], 'drawn_randoms': drawn, 'decal_rules': decal_rules})
    return plan


def _unturned_looks(looks):
    """One look per state value: the first weighted model choice, or the first without a texture turn."""
    kept = {}
    for (value, _), look in sorted(looks.items(), key=lambda item: item[0][1]):
        if value not in kept or (any(kept[value]['orientations'].values()) and not any(look['orientations'].values())):
            kept[value] = look
    return {(value, 0): look for value, look in kept.items()}


def _looks_per_axis(looks):
    """Each look turned onto every pillar axis: the faces, layers and tints move to the world faces they show."""
    turned_looks = {}
    for (_, index), plain in looks.items():
        for axis, turned in AXIS_FACES.items():
            turned_looks[(axis, index)] = {
                **plain, 'faces': {turned[local]: plain['faces'][local] for local in FACES},
                'layers': {turned[local]: plain['layers'][local] for local in FACES if local in plain['layers']},
                'tints': {turned[local]: plain['tints'][local] for local in FACES if local in plain['tints']},
                'orientations': {face: 0 for face in FACES}}
    return turned_looks


def _repeat_axes(plan):
    """{(axis, modulus)} the plan's repeat rules read, on faces and on face layers."""
    pairs = set()
    for key, look in plan['looks'].items():
        for local in FACES:
            world = AXIS_FACES[key[0] if plan['turns'] else 'y'][local]
            for rule in plan['rules']:
                if rule['method'] == 'repeat' and _rule_applies(rule, plan['block'], plan['logical'], world,
                                                                look['faces'][world], look['states']):
                    orientation = 0 if rule.get('orient') == 'none' else look['orientations'][world]
                    pairs |= _rule_moduli(rule, world, orientation)
                    break
            for layer in look['layers'].get(world, []):
                rule = _layer_rule(plan, layer['texture'], world, look)
                if rule and rule['method'] == 'repeat':
                    orientation = 0 if rule.get('orient') == 'none' else layer.get('orientation', 0)
                    pairs |= _rule_moduli(rule, world, orientation)
    return pairs


def _rule_moduli(rule, world, orientation):
    return repeat_moduli(world, rule['width'], rule['height'], rule.get('symmetry', 'none'), orientation)


def _axis_states(pairs):
    """State layout for coordinate residues: one state per axis when its period fits 16 values."""
    states = []
    for axis in 'xyz':
        moduli = sorted({modulus for name, modulus in pairs if name == axis and modulus > 1})
        if not moduli:
            continue
        period = math.lcm(*moduli)
        if period <= 16:
            states.append({'state': 'bct:' + axis, 'axis': axis, 'mod': period})
        else:
            for modulus in moduli:
                if modulus > 16:
                    raise ValueError(f'repeat size {modulus} along {axis} exceeds 16 block-state values')
                states.append({'state': f'bct:{axis}{modulus}', 'axis': axis, 'mod': modulus})
    return states


def _combos(looks, coordinates, random_states, drawn):
    """[(look key, coordinate state values, a location with those residues, random picks)] for every permutation.

    State values whose residues no location has (they disagree on an axis) are left out.
    """
    combos = []
    for key in sorted(looks, key=lambda item: (str(item[0]), item[1])):
        for values in itertools.product(*[range(item['mod']) for item in coordinates]):
            residues = {name: [] for name in 'xyz'}
            for item, value in zip(coordinates, values):
                residues[item['axis']].append((item['mod'], value))
            location = [representative(residues[name]) for name in 'xyz']
            if None in location:
                continue
            for chosen in itertools.product(*[range(random_states[index]['count']) for index in drawn]):
                picks = [0] * len(random_states)
                for index, pick in zip(drawn, chosen):
                    picks[index] = pick
                combos.append((key, values, tuple(location), tuple(picks)))
    return combos


def _decal_tile(plan, texture, values):
    """The tile a model part texture shows in one permutation (values: its coordinate states).

    A random rule on model part textures picks its tile from the block's x
    and z pattern states (a stable weighted hash per pattern cell) instead of
    a state of its own, which would multiply the block's permutations. The
    tile is a per-permutation material, so every pick shares one bone.
    """
    rule = next((rule for rule in plan['decal_rules'] if texture in rule.get('matchTiles', [])), None)
    if rule is None or rule['method'] not in ('random', 'fixed', 'repeat'):
        return texture
    if rule['method'] != 'random':
        return texture if rule['tiles'][0] in SKIP else rule['tiles'][0]
    cell = []
    for axis in 'xz':
        index = next((index for index, item in enumerate(plan['coordinates']) if item['axis'] == axis), None)
        if index is not None:
            cell.append(values[index])
    weights = rule.get('weights') or [1] * len(rule['tiles'])
    draw = int(hashlib.sha256(json.dumps([rule['id'], cell]).encode()).hexdigest()[:8], 16) % sum(weights)
    index = next(index for index, total in enumerate(itertools.accumulate(weights)) if draw < total)
    return texture if rule['tiles'][index] in SKIP else rule['tiles'][index]


def _layer_rule(plan, texture, world, look):
    """The first base rule whose matchTiles name a layer or model part texture."""
    for rule in plan['rules']:
        if (rule['method'] in BASE_METHODS and texture in rule.get('matchTiles', [])
                and _rule_applies(rule, plan['block'], plan['logical'], world, texture, look['states'])):
            return rule
    return None


def _rule_tile(plan, rule, world, location, picks, orientation, fallback):
    """The tile a base rule shows on a world face at a location with these random picks; fallback for a skip."""
    if rule['method'] == 'repeat':
        orientation = 0 if rule.get('orient') == 'none' else orientation
        tile = rule['tiles'][repeat_index(location, world, rule['width'], rule['height'],
                                          rule.get('symmetry', 'none'), orientation)]
    elif rule['method'] in RANDOM_METHODS:
        tile = rule['tiles'][picks[plan['randoms'].index(rule)]]
    else:
        tile = rule['tiles'][0]
    return fallback if tile in SKIP else tile


def _face_look(plan, document, key, local, location, picks):
    """What one local face shows: {base, layers, overlays, tint, overlay_tint, orientation}.

    tint is an RGB multiplier baked into the material or a Bedrock tint_method
    name. A biome-tinted layer over an untinted face (grass sides) is drawn as
    an overlay plane with its own tint_method (overlay_tint), like Java's
    separate tinted layer.
    """
    look = plan['looks'][key]
    world = AXIS_FACES[key[0] if plan['turns'] else 'y'][local]
    texture = look['faces'][world]
    base, overlays = texture, []
    chosen = False
    for rule in plan['rules']:
        if not _rule_applies(rule, plan['block'], plan['logical'], world, texture, look['states']):
            continue
        if rule['method'] in OVERLAY_METHODS:
            if rule['method'] == 'overlay_random':
                tile = rule['tiles'][picks[plan['randoms'].index(rule)]]
            else:
                tile = rule['tiles'][0]
            if tile not in SKIP:
                overlays.append(tile)
            continue
        # The first base rule picks the face's tile.
        if chosen:
            continue
        chosen = True
        base = _rule_tile(plan, rule, world, location, picks, look['orientations'][world], texture)
    layers, layer_tints = [], []
    for layer in look['layers'].get(world, []):
        rule = _layer_rule(plan, layer['texture'], world, look)
        if rule:
            layers.append(_rule_tile(plan, rule, world, location, picks, layer.get('orientation', 0), layer['texture']))
        else:
            layers.append(layer['texture'])
        if layer.get('tintIndex', -1) >= 0:
            layer_tints.append(_tint_of(document, plan['block'], plan['logical'], layer['tintIndex']))
        else:
            layer_tints.append([1, 1, 1])
    tint = _tint_of(document, plan['block'], plan['logical'], look['tints'].get(world, -1))
    if any(isinstance(value, list) and value != [1, 1, 1] for value in layer_tints):
        raise ValueError('face ' + world + ' has a tinted layer')
    overlay_tint = None
    biome = [value for value in layer_tints if isinstance(value, str)]
    if biome:
        if len(layer_tints) != 1 or tint != [1, 1, 1] or overlays:
            raise ValueError('face ' + world + ' mixes a biome-tinted layer with other tints or overlays')
        overlays, layers, overlay_tint = layers, [], biome[0]
    elif isinstance(tint, str) and layers:
        raise ValueError('face ' + world + ' has layers over a biome-tinted face')
    orientation = 0 if plan['turns'] else look['orientations'][world]
    return {'base': base, 'layers': layers, 'overlays': overlays, 'tint': tint, 'overlay_tint': overlay_tint,
            'orientation': orientation}


def _decals(plan):
    """(bones with their culled faces, [(instance name, part, face)], {bone: visibility}) for the model parts."""
    decals, decal_faces, decal_visibility = [], [], {}
    for group_index, group in enumerate(plan['groups']):
        for choice_index, choice in enumerate(group.get('modelChoices', [])):
            for part_index, part in enumerate(choice.get('modelParts', [])):
                name = f'g{group_index}_{choice_index}_{part_index}'
                names = {face: f'{name}_{face}' for face in part['faces']}
                bone, drawn = _decal_bone(name, part, names)
                decals.append((bone, [(face, part['faces'][face].get('cullface')) for face in drawn]))
                decal_faces += [(names[face], part, part['faces'][face]) for face in drawn]
                decal_visibility[name] = _state_term(f'bct:g{group_index}', choice_index)
    return decals, decal_faces, decal_visibility


def _model_states(plan):
    """The engine's weighted model states: the Java model choice (bct:m) and each decoration group (bct:g<n>)."""
    models = []
    if plan['choices']:
        models.append({'state': 'bct:m', 'weights': plan['choices']['weights'],
                       'selection': plan['choices']['selection']})
    for index, group in enumerate(plan['groups']):
        models.append({'state': f'bct:g{index}',
                       'weights': [choice.get('weight', 1) for choice in group['modelChoices']],
                       'selection': group.get('modelSelection')})
    return models


def _uniform_render_method(instances, transparent):
    """Bedrock wants one render method per block: an opaque block with cut-out
    layers or model parts draws every material alpha-tested on one side."""
    if transparent or all(item['render_method'] == 'opaque' for item in instances.values()):
        return
    for item in instances.values():
        item['render_method'] = 'alpha_test_single_sided'


def _state_term(name, value):
    """A Molang test of one block state; booleans are mirrored as 0 or 1."""
    if isinstance(value, bool):
        value = 1 if value else 0
    return f"q.block_state('{name}') == " + (f"'{value}'" if isinstance(value, str) else str(value))


def _look_condition(plan, mirror, key):
    """Molang terms selecting a look: the vanilla state that changes it and the weighted model state."""
    terms = []
    if plan['state'] is not None:
        terms.append(_state_term(mirror[plan['state']], key[0]))
    if plan['choices']:
        terms.append(_state_term('bct:m', key[1]))
    return terms


def _condition(plan, mirror, key, values, picks):
    """The permutation condition of a combo: its look, coordinate states and random picks."""
    terms = _look_condition(plan, mirror, key)
    for item, value in zip(plan['coordinates'], values):
        terms.append(_state_term(item['state'], value))
    for index in plan['drawn_randoms']:
        terms.append(_state_term(plan['random_states'][index]['state'], picks[index]))
    return ' && '.join(terms) or '1.0'


def _any_of(terms):
    """Molang true when any of the terms holds ('1.0' always, '0.0' never)."""
    terms = sorted(set(terms))
    if '1.0' in terms:
        return '1.0'
    if len(terms) > 1:
        return ' || '.join(f'({term})' for term in terms)
    return terms[0] if terms else '0.0'


def _oriented_uv(face, orientation, material):
    """A face UV showing the texture turned by a texture orientation (0-3 quarter turns, 4-7 mirrored first)."""
    uv = {'uv': [0, 0], 'uv_size': [16, 16], 'material_instance': material}
    if orientation >= 4:
        uv['uv'], uv['uv_size'] = [16, 0], [-16, 16]
    # Top and bottom faces turn the other way round from the side faces.
    turns = (orientation % 4) * (1 if face in FACES[:4] else -1) % 4
    if turns:
        uv['uv_rotation'] = turns * 90
    return uv


def _decal_bone(name, part, instance_names):
    """One Java model element as a bone, converted like Blockbench (java_block_geometry.cube).

    instance_names: {face: material instance name}. The blockstate x and y
    rotation turn the bone about the block centre. Returns (bone, faces drawn).
    """
    if (part.get('modelRotation') or {}).get('uvlock'):
        raise ValueError('uvlock model parts')
    faces = {face: {key: item[key] for key in ('uv', 'rotation') if key in item}
             for face, item in part['faces'].items()}
    element = {'from': part['from'], 'to': part['to'], 'rotation': part.get('rotation'), 'faces': faces}
    built = cube(element, lambda face, data: instance_names[face])
    bone = {'name': name, 'pivot': [0, 8, 0], 'cubes': [built]}
    model = part.get('modelRotation') or {}
    if model.get('x') or model.get('y'):
        bone['rotation'] = [-model.get('x', 0), -model.get('y', 0), 0]
    return bone, sorted(built['uv'])


def _replacement_geometry(identifier, plain, layers, oriented=None, decals=()):
    """A cube whose plain faces sit on one bone, a bone per turned texture
    orientation, a thin layer per overlaid face, and Java model parts.

    oriented: {face: [orientation]}; decals: [(bone dict, [(face, cull direction)])].
    Returns (geometry, [(bone, face, cull direction)]).
    """
    bones, parts = [], []
    if plain:
        uv = {face: {'uv': [0, 0], 'uv_size': [16, 16], 'material_instance': face} for face in plain}
        bones.append({'name': 'base', 'pivot': [0, 0, 0],
                      'cubes': [{'origin': [-8, 0, -8], 'size': [16, 16, 16], 'uv': uv}]})
        parts += [('base', face, face) for face in plain]
    for face, orientations in (oriented or {}).items():
        for orientation in orientations:
            name = f'{face}_o{orientation}'
            bones.append({'name': name, 'pivot': [0, 0, 0], 'cubes': [{'origin': [-8, 0, -8], 'size': [16, 16, 16],
                          'uv': {face: _oriented_uv(face, orientation, face)}}]})
            parts.append((name, face, face))
    for face in layers:
        origin, size = PLANES[face]
        uv = {face: {'uv': [0, 0], 'uv_size': [16, 16], 'material_instance': 'layer_' + face}}
        bones.append({'name': 'layer_' + face, 'pivot': [0, 0, 0],
                      'cubes': [{'origin': origin, 'size': size, 'uv': uv}]})
        parts.append(('layer_' + face, face, face))
    for bone, culls in decals:
        bones.append(bone)
        parts += [(bone['name'], face, cull) for face, cull in culls]
    description = {'identifier': identifier, 'texture_width': 16, 'texture_height': 16,
                   'visible_bounds_width': 2, 'visible_bounds_height': 2, 'visible_bounds_offset': [0, 0.5, 0]}
    return {'format_version': '1.21.0', 'minecraft:geometry': [{'description': description, 'bones': bones}]}, parts


def _culling_rules(identifier, parts, transparent, same=None):
    """Hide faces against full opaque neighbors, and for see-through blocks also against the same block.

    A rule without a condition culls against full opaque neighbors
    (cull_against_full_and_opaque defaults to true); "same_block" adds the
    block itself. parts: [(bone, face, direction, cube index)] or [(bone, face, direction)].
    """
    rules = []
    for part in parts:
        bone, face, direction = part[:3]
        index = part[3] if len(part) > 3 else 0
        if direction is None:
            continue
        rule = {'geometry_part': {'bone': bone, 'cube': index, 'face': face}, 'direction': direction}
        if transparent and (same is None or direction in same):
            rule['condition'] = 'same_block'
        rules.append(rule)
    description = {'identifier': identifier}
    return {'format_version': '1.21.80', 'minecraft:block_culling_rules': {'description': description, 'rules': rules}}


def _state_values(values):
    """A block state's values in the 1.26.20 schema form: strings as a list, integers and booleans as a range."""
    if all(isinstance(value, str) for value in values):
        return list(values)
    numbers = [int(value) for value in values]
    if sorted(numbers) != list(range(min(numbers), max(numbers) + 1)):
        raise ValueError(f'state values {values} are not one integer range')
    return {'values': {'min': min(numbers), 'max': max(numbers)}}


def _build_pane(document, block, rules, vanilla_states, mirror, materials, instance, namespace, stem, rp, transparent):
    """A pane or bars replacement: post and four arms shown by the mirrored connection states.

    The broad faces show the pane texture (or a fixed rule's tile); the thin
    edges show the edge texture. Collision and selection follow the connected
    arms as one box per permutation. Returns components, permutations, the
    engine's pane states and the geometry and culling files to write.
    """
    if any('minecraft:connection_' + side not in vanilla_states for side in PANE_SIDES):
        raise ValueError('pane without connection states')
    faces = document.get('baseTextures', {}).get(block, {})
    # The most used texture is the broad pane, the least used the edge.
    textures = sorted(set(faces.values()), key=lambda texture: -list(faces.values()).count(texture))
    if not textures:
        raise ValueError('no texture for the pane')
    broad_texture, edge_texture = textures[0], textures[-1]
    logical = document.get('nativeBlockJavaIds', {}).get(block, block)
    tint = _tint_of(document, block, logical, -1)
    if not isinstance(tint, list):
        raise ValueError('biome-tinted panes')
    owner = next((rule for rule in rules if _rule_applies(rule, block, logical, 'north', broad_texture, {})), None)
    if owner and owner['method'] != 'fixed':
        raise ValueError(f'method={owner["method"]} on a pane has no native form')
    broad, instances = {}, {'edge': instance(materials.alias(edge_texture, (), tint))}
    for face in PANE_SIDES:
        tile = owner['tiles'][0] if owner and owner['tiles'][0] not in SKIP else broad_texture
        instances[face + '_p'] = instance(materials.alias(tile, (), tint))
        broad[face] = [('p', '1.0')]
    instances['*'] = instances['edge']
    geometry_id = 'geometry.' + namespace + '.' + stem
    model, parts, visibility = _pane_geometry(geometry_id, broad, mirror)
    culling = namespace + ':' + stem + '_culling'
    culling_document = _culling_rules(culling, parts, transparent, same={'up', 'down'})
    files = {rp / f'models/blocks/{namespace}_{stem}.geo.json': model,
             rp / f'block_culling/{namespace}_{stem}.json': culling_document}
    permutations = []
    for combination in itertools.product((False, True), repeat=4):
        connections = dict(zip(PANE_SIDES, combination))
        box = _pane_box(connections)
        condition = ' && '.join(_state_term(mirror['minecraft:connection_' + side], value)
                                for side, value in connections.items())
        permutations.append({'condition': condition,
                             'components': {'minecraft:collision_box': box, 'minecraft:selection_box': box}})
    components = {'minecraft:geometry': {'identifier': geometry_id, 'culling': culling, 'bone_visibility': visibility},
                  'minecraft:material_instances': instances}
    pane = {side: mirror['minecraft:connection_' + side] for side in PANE_SIDES}
    return {'components': components, 'permutations': permutations, 'pane': pane, 'files': files}


def _pane_segment(face, low, high, material):
    """One broad pane face strip; low-high runs along x for north/south faces and along z for west/east."""
    if face in ('north', 'south'):
        strip = {'origin': [low - 8, 0, -1 if face == 'north' else 1], 'size': [high - low, 16, 0]}
        u = 16 - high if face == 'north' else low
    else:
        strip = {'origin': [-1 if face == 'west' else 1, 0, low - 8], 'size': [0, 16, high - low]}
        u = low if face == 'west' else 16 - high
    strip['uv'] = {face: {'uv': [u, 0], 'uv_size': [high - low, 16], 'material_instance': material}}
    return strip


def _pane_geometry(identifier, broad, mirror):
    """Post and four arms. broad: {face: [(material suffix, molang case)]}. Returns (geometry, parts, visibility)."""
    bones, parts, visibility = [], [], {}

    def arm(side):
        return f"q.block_state('{mirror['minecraft:connection_' + side]}')"

    for face, cases in broad.items():
        for index, ((low, high), side, shown) in enumerate(PANE_SEGMENTS[face]):
            for suffix, expression in cases:
                name = f'{face}_s{index}_{suffix}'
                strip = _pane_segment(face, low, high, face + '_' + suffix)
                bones.append({'name': name, 'pivot': [0, 0, 0], 'cubes': [strip]})
                parts.append((name, face, None))
                state = arm(side) if shown else '!' + arm(side)
                visibility[name] = state if expression == '1.0' else f'{state} && ({expression})'
    post = {'origin': [-1, 0, -1], 'size': [2, 16, 2],
            'uv': {'up': {'uv': [7, 7], 'uv_size': [2, 2], 'material_instance': 'edge'},
                   'down': {'uv': [7, 7], 'uv_size': [2, 2], 'material_instance': 'edge'}}}
    bones.append({'name': 'post', 'pivot': [0, 0, 0], 'cubes': [post]})
    parts += [('post', 'up', 'up'), ('post', 'down', 'down')]
    for side, (low, high) in PANE_ARMS.items():
        size = [high[axis] - low[axis] for axis in range(3)]
        uv = {'up': {'uv': [7, 0], 'uv_size': [2, 7]}, 'down': {'uv': [7, 0], 'uv_size': [2, 7]},
              side: {'uv': [7, 0], 'uv_size': [2, 16]}}
        for item in uv.values():
            item['material_instance'] = 'edge'
        bones.append({'name': 'arm_' + side, 'pivot': [0, 0, 0],
                      'cubes': [{'origin': [low[0] - 8, low[1], low[2] - 8], 'size': size, 'uv': uv}]})
        parts += [('arm_' + side, 'up', 'up'), ('arm_' + side, 'down', 'down'), ('arm_' + side, side, side)]
        visibility['arm_' + side] = arm(side)
    description = {'identifier': identifier, 'texture_width': 16, 'texture_height': 16,
                   'visible_bounds_width': 2, 'visible_bounds_height': 2, 'visible_bounds_offset': [0, 0.5, 0]}
    geometry = {'format_version': '1.21.0', 'minecraft:geometry': [{'description': description, 'bones': bones}]}
    return geometry, parts, visibility


def _pane_box(connections):
    """Collision and selection box around the post and the connected arms (one box per block is all Bedrock allows)."""
    x0, x1 = (0 if connections['west'] else 7), (16 if connections['east'] else 9)
    z0, z1 = (0 if connections['north'] else 7), (16 if connections['south'] else 9)
    return {'origin': [x0 - 8, 0, z0 - 8], 'size': [x1 - x0, 16, z1 - z0]}


def _pack_uuid(name):
    return uuid.uuid5(uuid.NAMESPACE_URL, 'bct/replacement/' + name)
