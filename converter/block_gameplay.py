"""Vanilla gameplay a replacement block carries itself.

A custom block that stands in for a vanilla block must mine, drop, burn,
conduct redstone and sound under a note block like the vanilla block, because
players dig and build with it directly. Everything here comes from a policy
profile in data/native-replacement.json (values from Java 26.2's Blocks and
block tags, which Bedrock shares for these blocks):

- destroy: the block's hardness. Bedrock mines a custom block as if the tool
  were always right (hardness x 1.5 seconds, divided by the tool's speed). A
  block that needs a tool (requires_tool, tier) takes hardness x 5 without it,
  so the block's own time is hardness x 10/3 and item_specific_speeds gives
  the right tools (and tiers) the plain hardness back. `tool` adds the block
  tag that lets that tool's speed count.
- loot: what the block drops; pools are gated by the right tool (match_tool
  with item tags) and ores get Java's Fortune ore_drops bonus as extra pools.
  Silk Touch makes Bedrock drop the custom block's own item; the engine turns
  it into the vanilla block's item.
- redstone_conductivity: opaque blocks conduct, see-through ones do not and
  let no wire step down them.
- flammable (catch, destroy) and ignited_by_lava, instrument (note blocks),
  movable (glazed terracotta is push only).
"""
# Block tags that let a tool's mining speed count on the block.
TOOL_BLOCK_TAGS = {'pickaxe': 'minecraft:is_pickaxe_item_destructible',
                   'shovel': 'minecraft:is_shovel_item_destructible',
                   'axe': 'minecraft:is_axe_item_destructible',
                   'hoe': 'minecraft:is_hoe_item_destructible',
                   'shears': 'minecraft:is_shears_item_destructible'}
# Item tags of the tools a block can need.
TOOL_ITEM_TAGS = {'pickaxe': 'minecraft:is_pickaxe', 'shovel': 'minecraft:is_shovel', 'axe': 'minecraft:is_axe',
                  'hoe': 'minecraft:is_hoe'}
# Java's needs_<tier>_tool: the tool tiers that may mine the block for drops (golden tools count as wooden ones).
TIER_TAGS = {
    'stone': ['minecraft:stone_tier', 'minecraft:copper_tier', 'minecraft:iron_tier', 'minecraft:diamond_tier',
              'minecraft:netherite_tier'],
    'iron': ['minecraft:iron_tier', 'minecraft:diamond_tier', 'minecraft:netherite_tier'],
    'diamond': ['minecraft:diamond_tier', 'minecraft:netherite_tier'],
}
# Java's ore_drops formula is count x (1 + max(0, random(fortune + 2) - 1)).
# Per Fortune level: the weight of no extra drop, then of 1, 2, 3 extra multiples.
ORE_EXTRA = {1: [2, 1], 2: [2, 1, 1], 3: [2, 1, 1, 1]}


def needed_tool(profile):
    """(item tags all required, item tags of which one is required) for a block that needs a tool, else None."""
    if not profile.get('requires_tool'):
        return None
    tool = profile.get('tool')
    if tool not in TOOL_ITEM_TAGS:
        raise ValueError(f'requires_tool needs a tool with an item tag, not {tool!r}')
    tier = profile.get('tier')
    if tier is not None and tier not in TIER_TAGS:
        raise ValueError(f'unknown tool tier {tier!r}')
    return [TOOL_ITEM_TAGS[tool]], list(TIER_TAGS[tier]) if tier else []


def mining(profile, item_speeds=()):
    """minecraft:destructible_by_mining: hardness, slowed for blocks that need a tool, with item-specific hardness.

    item_speeds: [{'item': descriptor, 'speed': Java tool speed}] for items that
    mine the block faster without a block tag (shears and swords on leaves); the
    item's hardness is the block's divided by that speed.
    """
    hardness = profile['destroy']
    needed = needed_tool(profile)
    component = {'seconds_to_destroy': _rounded(hardness * 10 / 3 if needed else hardness)}
    speeds = []
    if needed:
        speeds.append({'item': {'tags': _tool_query(needed)}, 'destroy_speed': _rounded(hardness)})
    for item in item_speeds:
        speeds.append({'item': item['item'], 'destroy_speed': _rounded(hardness / item['speed'])})
    if speeds:
        component['item_specific_speeds'] = speeds
    return component


def tool_condition(profile):
    """The match_tool loot condition for the right tool, or None when any tool drops the block."""
    needed = needed_tool(profile)
    if not needed:
        return None
    every, one_of = needed
    condition = {'condition': 'match_tool', 'minecraft:match_tool_filter_all': every}
    if one_of:
        condition['minecraft:match_tool_filter_any'] = one_of
    return condition


def loot_table(block, profile):
    """The block's drops: the vanilla items, only for the right tool, with explosion decay and Fortune for ores."""
    loot = profile.get('loot', 'self')
    if loot == 'self':
        loot = [{'item': block}]
    gate = tool_condition(profile)
    pools = []
    for drop in loot:
        low, high = drop.get('min', 1), drop.get('max', drop.get('min', 1))
        functions = [] if (low, high) == (1, 1) else [{'function': 'set_count', 'count': _count(low, high)}]
        entry = {'type': 'item', 'name': drop['item'], 'weight': 1,
                 'functions': functions + [{'function': 'explosion_decay'}]}
        pool = {'rolls': 1, 'entries': [entry]}
        if gate:
            pool['conditions'] = [gate]
        pools.append(pool)
        if profile.get('fortune') == 'ore':
            pools.extend(_fortune_pools(drop['item'], low, high, gate))
        elif profile.get('fortune'):
            raise ValueError(f"unknown fortune bonus {profile['fortune']!r}")
    return {'pools': pools}


def components(profile, transparent):
    """The gameplay components every replacement of this profile gets (besides mining, loot and look)."""
    result = {'minecraft:destructible_by_explosion': {'explosion_resistance': profile['resistance']},
              'minecraft:redstone_conductivity': {'redstone_conductor': not transparent,
                                                  'allows_wire_to_step_down': not transparent}}
    if 'flammable' in profile:
        catch, destroy = profile['flammable'][0], profile['flammable'][1]
        flammable = {'catch_chance_modifier': catch, 'destroy_chance_modifier': destroy}
        if profile.get('ignited_by_lava'):
            flammable['lava_flammable'] = 'always'
        result['minecraft:flammable'] = flammable
    if profile.get('instrument'):
        result['minecraft:instrument_sound'] = {'up': profile['instrument']}
    if profile.get('movable'):
        result['minecraft:movable'] = {'movement_type': profile['movable']}
    return result


def block_tags(profile):
    """The profile's block tags plus the tag that lets its tool mine the block faster."""
    tags = set(profile.get('tags', []))
    if TOOL_BLOCK_TAGS.get(profile.get('tool')):
        tags.add(TOOL_BLOCK_TAGS[profile['tool']])
    return sorted(tags)


def _rounded(value):
    return round(float(value), 6)


def _tool_query(needed):
    """Molang matching an item that has every tag of the first list and one of the second."""
    every, one_of = needed
    terms = ["q.any_tag('" + tag + "')" for tag in every]
    if one_of:
        terms.append('q.any_tag(' + ', '.join("'" + tag + "'" for tag in one_of) + ')')
    return ' && '.join(terms)


def _count(low, high, times=1):
    """A set_count count: the number itself, or a {min, max} range."""
    low, high = low * times, high * times
    return low if low == high else {'min': low, 'max': high}


def _fortune_pools(item, low, high, gate):
    """Extra pools for each Fortune level, adding Java's ore_drops multiples of the drop."""
    pools = []
    for level, weights in ORE_EXTRA.items():
        entries = [{'type': 'empty', 'weight': weights[0]}]
        for times, weight in enumerate(weights[1:], start=1):
            entries.append({'type': 'item', 'name': item, 'weight': weight,
                            'functions': [{'function': 'set_count', 'count': _count(low, high, times)}]})
        condition = dict(gate or {'condition': 'match_tool'})
        condition['enchantments'] = [{'enchantment': 'fortune', 'levels': {'range_min': level, 'range_max': level}}]
        pools.append({'rolls': 1, 'conditions': [condition], 'entries': entries})
    return pools
