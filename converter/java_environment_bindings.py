"""Bedrock names for the blocks, block states and biomes that CTM rules test.

A Java rule can match blocks by state (lit=true, axis=y) or by biome. Each predicate is
translated only where Mojang's current Bedrock metadata confirms the translated name;
everything else is listed as unresolved with a reason, never guessed.
"""
from pathlib import Path

from block_ids import JAVA_RENAMES
from common import read_json
from import_java_ctm import parse_properties

# Java block ids that Bedrock names differently, beyond block_ids.JAVA_RENAMES.
BLOCK_NAMES = {'minecraft:bricks': 'minecraft:brick_block',
               'minecraft:oak_trapdoor': 'minecraft:trapdoor',
               'minecraft:stone_slab': 'minecraft:normal_stone_slab',
               'minecraft:nether_bricks': 'minecraft:nether_brick',
               'minecraft:red_nether_bricks': 'minecraft:red_nether_brick',
               'minecraft:end_stone_bricks': 'minecraft:end_bricks',
               'minecraft:terracotta': 'minecraft:hardened_clay'}

# Java biome ids that Bedrock names differently; one Java biome can be several Bedrock biomes.
BIOME_NAMES = {
    'badlands': ['mesa'], 'eroded_badlands': ['mesa_bryce'],
    'wooded_badlands': ['mesa_plateau_stone', 'mesa_plateau_stone_mutated'],
    'dark_forest': ['roofed_forest', 'roofed_forest_mutated'],
    'nether_wastes': ['hell'], 'soul_sand_valley': ['soulsand_valley'],
    'mushroom_fields': ['mushroom_island', 'mushroom_island_shore'],
    'old_growth_birch_forest': ['birch_forest_mutated', 'birch_forest_hills_mutated'],
    'old_growth_pine_taiga': ['mega_taiga', 'mega_taiga_hills'],
    'old_growth_spruce_taiga': ['redwood_taiga_mutated', 'redwood_taiga_hills_mutated'],
    'snowy_beach': ['cold_beach'], 'snowy_plains': ['ice_plains', 'ice_mountains'],
    'snowy_taiga': ['cold_taiga', 'cold_taiga_hills', 'cold_taiga_mutated'],
    'ice_spikes': ['ice_plains_spikes'], 'stony_shore': ['stone_beach'],
    'sparse_jungle': ['jungle_edge', 'jungle_edge_mutated'],
    'swamp': ['swampland', 'swampland_mutated'],
    'windswept_hills': ['extreme_hills', 'extreme_hills_edge'],
    'windswept_forest': ['extreme_hills_plus_trees'],
    'windswept_gravelly_hills': ['extreme_hills_mutated', 'extreme_hills_plus_trees_mutated'],
    'windswept_savanna': ['savanna_mutated', 'savanna_plateau_mutated'],
}
# Java splits the End into these biomes; Bedrock has one, so a rule that names all of
# them stands for minecraft:the_end.
END_BIOME_UNION = {'the_end', 'small_end_islands', 'end_midlands', 'end_highlands', 'end_barrens', 'the_void'}
# Bedrock's trapdoor direction numbers differ from its usual horizontal direction numbers;
# checked against PyMCTranslate.
TRAPDOOR_DIRECTIONS = {'north': 3, 'east': 0, 'south': 2, 'west': 1}
# Java stair facing as Bedrock's weirdo_direction, and Java stair shape as Bedrock's corner.
STAIR_DIRECTIONS = {'east': 0, 'west': 1, 'south': 2, 'north': 3}
STAIR_SHAPES = {'straight': 'none', 'inner_left': 'inner_left', 'inner_right': 'inner_right', 'outer_left': 'outer_left',
                'outer_right': 'outer_right'}
# Java wall side heights as Bedrock's wall_connection_type values.
WALL_SIDES = {'none': 'none', 'low': 'short', 'tall': 'tall'}


def resolve_environment(stack, metadata):
    """Bedrock names for the blocks, states and biomes the pack's CTM rules test.

    metadata is a folder holding Mojang's mojang-blocks.json and mojang-biomes.json.
    """
    metadata = Path(metadata)
    block_data = read_json(metadata / 'mojang-blocks.json')
    block_states = {item['name']: {prop['name'] for prop in item.get('properties', [])}
                    for item in block_data['data_items']}
    biome_ids = {item['name'] for item in read_json(metadata / 'mojang-biomes.json')['data_items']}
    mapping = _EnvironmentMapping(block_states, biome_ids)
    for path in sorted(stack.files):
        if '/optifine/ctm/' not in path or not path.endswith('.properties'):
            continue
        properties = parse_properties(stack.read(path).decode('utf-8-sig'), path)
        for token in properties.get('matchBlocks', '').split():
            mapping.add_block(path, token)
        mapping.add_biomes(path, properties.get('biomes', '').lstrip('!').split())
    return mapping.report()


class _EnvironmentMapping:
    """Translations collected rule by rule, with every predicate that has none."""

    def __init__(self, block_states, biome_ids):
        self.block_states = block_states
        self.biome_ids = biome_ids
        # Java ids that are different blocks in Bedrock; a list maps one Java block to several.
        self.block_names = {**JAVA_RENAMES, **BLOCK_NAMES}
        self.state_names = {}
        self.biome_names = {}
        self.double_slabs = {}
        self.derived = []
        self.biome_unions = []
        self.unresolved = []

    def report(self):
        return {'blockNames': self.block_names, 'stateNames': self.state_names, 'biomeNames': self.biome_names,
                'doubleSlabBlocks': self.double_slabs, 'derivedPredicates': self.derived,
                'biomeUnions': self.biome_unions, 'unresolvedPredicates': self.unresolved}

    def add_block(self, rule, token):
        """Translate one matchBlocks token: a block id followed by optional key=value states."""
        parts = token.split(':')
        first_state = next((index for index, value in enumerate(parts) if '=' in value), len(parts))
        name = ':'.join(parts[:first_state])
        source = name if ':' in name else 'minecraft:' + name
        target = self.block_names.get(source, source)
        target = target[0] if isinstance(target, list) else target
        for predicate in parts[first_state:]:
            key, value = predicate.split('=', 1)
            if key == 'type' and value == 'double' and source.endswith('_slab'):
                self._add_double_slab(rule, source, target, predicate)
                continue
            choices = value.split(',')
            if key == 'snowy' and all(choice in ('true', 'false') for choice in choices):
                # Bedrock has no snowy state; the engine works it out from the block above.
                self.derived.append({'rule': rule, 'block': source, 'predicate': predicate,
                                     'provider': 'bct:snowy', 'neighbors': ['minecraft:snow', 'minecraft:snow_layer']})
                continue
            translated = _translated_state(key, choices, source)
            if translated is None:
                reason = 'Requires a runtime-derived predicate' if key == 'snowy' else 'No declared state translation'
                self._unresolved_block(rule, source, predicate, reason)
                continue
            if translated['name'] not in self.block_states.get(target, set()):
                self._unresolved_block(rule, source, predicate, 'Translated state absent from current Bedrock metadata')
                continue
            states = self.state_names.setdefault(source, {})
            existing = states.setdefault(key, {'name': translated['name'], 'values': {}})
            existing['values'].update(translated['values'])

    def add_biomes(self, rule, tokens):
        """Translate a rule's biome list, folding a complete set of End biomes into the_end."""
        complete_end = END_BIOME_UNION <= {token.removeprefix('minecraft:') for token in tokens}
        if complete_end:
            union = {'source': [token for token in tokens if token.removeprefix('minecraft:') in END_BIOME_UNION],
                     'target': ['minecraft:the_end']}
            if union not in self.biome_unions:
                self.biome_unions.append(union)
        for token in tokens:
            local = token.removeprefix('minecraft:')
            if complete_end and local in END_BIOME_UNION:
                continue
            targets = ['minecraft:' + name for name in BIOME_NAMES.get(local, [local])]
            if all(target in self.biome_ids for target in targets):
                self.biome_names[token] = targets
            else:
                self.unresolved.append({'rule': rule, 'biome': token,
                                        'reason': 'Java biome has no distinct native Bedrock biome'})

    def _add_double_slab(self, rule, source, target, predicate):
        # Bedrock names a double slab <slab>_double_slab, except cut copper: double_cut_copper_slab.
        if 'cut_copper_slab' in target:
            double = target.replace('cut_copper_slab', 'double_cut_copper_slab')
        else:
            double = target.removesuffix('_slab') + '_double_slab'
        if double in self.block_states:
            self.double_slabs[source] = double
        else:
            self._unresolved_block(rule, source, predicate, 'No native double slab ID')

    def _unresolved_block(self, rule, source, predicate, reason):
        self.unresolved.append({'rule': rule, 'block': source, 'predicate': predicate, 'reason': reason})


def _translated_state(key, choices, source):
    """The Bedrock state {'name', 'values'} for a Java state predicate, or None without a known one."""
    if key == 'axis':
        return {'name': 'pillar_axis', 'values': {choice: choice for choice in choices}}
    if key == 'lit':
        return {'name': 'lit', 'values': {choice: choice == 'true' for choice in choices}}
    if key == 'open' and source.endswith('trapdoor'):
        return {'name': 'open_bit', 'values': {choice: choice == 'true' for choice in choices}}
    if key == 'facing' and source.endswith('trapdoor'):
        return {'name': 'direction', 'values': {choice: TRAPDOOR_DIRECTIONS[choice] for choice in choices}}
    if key == 'power' and source == 'minecraft:redstone_wire':
        return {'name': 'redstone_signal', 'values': {choice: int(choice) for choice in choices}}
    return _shaped_state(key, choices, source)


def _shaped_state(key, choices, source):
    """The Bedrock state of a slab's, stair's, fence's, pane's or wall's Java shape state, or None."""
    sides = ('north', 'east', 'south', 'west')
    if key == 'type' and source.endswith('_slab') and set(choices) <= {'bottom', 'top'}:
        return {'name': 'minecraft:vertical_half', 'values': {choice: choice for choice in choices}}
    if source.endswith('_stairs'):
        if key == 'half' and set(choices) <= {'bottom', 'top'}:
            return {'name': 'upside_down_bit', 'values': {choice: choice == 'top' for choice in choices}}
        if key == 'facing' and set(choices) <= set(STAIR_DIRECTIONS):
            return {'name': 'weirdo_direction', 'values': {choice: STAIR_DIRECTIONS[choice] for choice in choices}}
        if key == 'shape' and set(choices) <= set(STAIR_SHAPES):
            return {'name': 'minecraft:corner', 'values': {choice: STAIR_SHAPES[choice] for choice in choices}}
    if source.endswith('_wall'):
        if key in sides and set(choices) <= set(WALL_SIDES):
            return {'name': 'wall_connection_type_' + key, 'values': {choice: WALL_SIDES[choice] for choice in choices}}
        if key == 'up' and set(choices) <= {'true', 'false'}:
            return {'name': 'wall_post_bit', 'values': {choice: choice == 'true' for choice in choices}}
    joins = source.endswith('_fence') or source.endswith('glass_pane') or source.endswith('_bars')
    if joins and key in sides and set(choices) <= {'true', 'false'}:
        return {'name': 'minecraft:connection_' + key, 'values': {choice: choice == 'true' for choice in choices}}
    return None
