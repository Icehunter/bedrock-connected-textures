"""Current Bedrock block ids for Java ids and legacy Bedrock names.

Java packs name blocks by Java id, and bedrock-samples' resource-pack
blocks.json still keys some blocks by legacy names (grass, seaLantern,
stonebrick). The engine only meets blocks by their current Bedrock id, so the
converter maps every id it publishes through this table and reports the ones
without a Bedrock block instead of shipping them.
"""
import json
from pathlib import Path

# Java id -> Bedrock id(s) where the editions differ (checked against
# bedrock-samples' mojang-blocks.json by tests/test_block_ids.py). A list means
# the Java block is several Bedrock blocks (lit and unlit, powered states...).
JAVA_RENAMES = {
    'minecraft:attached_melon_stem': 'minecraft:melon_stem',
    'minecraft:attached_pumpkin_stem': 'minecraft:pumpkin_stem',
    'minecraft:beetroots': 'minecraft:beetroot',
    'minecraft:big_dripleaf_stem': 'minecraft:big_dripleaf',
    'minecraft:bricks': 'minecraft:brick_block',
    'minecraft:cave_air': 'minecraft:air',
    'minecraft:void_air': 'minecraft:air',
    'minecraft:cave_vines_plant': ['minecraft:cave_vines', 'minecraft:cave_vines_body_with_berries'],
    'minecraft:cobblestone_stairs': 'minecraft:stone_stairs',
    'minecraft:cobweb': 'minecraft:web',
    'minecraft:comparator': ['minecraft:unpowered_comparator', 'minecraft:powered_comparator'],
    'minecraft:dead_bush': 'minecraft:deadbush',
    'minecraft:dirt_path': 'minecraft:grass_path',
    'minecraft:end_stone_brick_stairs': 'minecraft:end_brick_stairs',
    'minecraft:end_stone_bricks': 'minecraft:end_bricks',
    'minecraft:flowering_azalea_leaves': 'minecraft:azalea_leaves_flowered',
    'minecraft:frogspawn': 'minecraft:frog_spawn',
    'minecraft:glow_item_frame': 'minecraft:glow_frame',
    'minecraft:item_frame': 'minecraft:frame',
    'minecraft:jack_o_lantern': 'minecraft:lit_pumpkin',
    'minecraft:kelp_plant': 'minecraft:kelp',
    'minecraft:lava_cauldron': 'minecraft:cauldron',
    'minecraft:light_gray_glazed_terracotta': 'minecraft:silver_glazed_terracotta',
    'minecraft:lily_pad': 'minecraft:waterlily',
    'minecraft:magma_block': 'minecraft:magma',
    'minecraft:melon': 'minecraft:melon_block',
    'minecraft:nether_bricks': 'minecraft:nether_brick',
    'minecraft:nether_portal': 'minecraft:portal',
    'minecraft:nether_quartz_ore': 'minecraft:quartz_ore',
    'minecraft:note_block': 'minecraft:noteblock',
    'minecraft:oak_button': 'minecraft:wooden_button',
    'minecraft:oak_door': 'minecraft:wooden_door',
    'minecraft:oak_fence_gate': 'minecraft:fence_gate',
    'minecraft:oak_pressure_plate': 'minecraft:wooden_pressure_plate',
    'minecraft:oak_sign': 'minecraft:standing_sign',
    'minecraft:oak_wall_sign': 'minecraft:wall_sign',
    'minecraft:oak_trapdoor': 'minecraft:trapdoor',
    'minecraft:piston_head': 'minecraft:piston_arm_collision',
    'minecraft:powder_snow_cauldron': 'minecraft:cauldron',
    'minecraft:powered_rail': 'minecraft:golden_rail',
    'minecraft:prismarine_brick_stairs': 'minecraft:prismarine_bricks_stairs',
    'minecraft:red_nether_bricks': 'minecraft:red_nether_brick',
    'minecraft:redstone_wall_torch': ['minecraft:redstone_torch', 'minecraft:unlit_redstone_torch'],
    'minecraft:repeater': ['minecraft:unpowered_repeater', 'minecraft:powered_repeater'],
    'minecraft:rooted_dirt': 'minecraft:dirt_with_roots',
    'minecraft:shulker_box': 'minecraft:undyed_shulker_box',
    'minecraft:slime_block': 'minecraft:slime',
    'minecraft:small_dripleaf': 'minecraft:small_dripleaf_block',
    'minecraft:snow': 'minecraft:snow_layer',
    'minecraft:snow_block': 'minecraft:snow',
    'minecraft:soul_wall_torch': 'minecraft:soul_torch',
    'minecraft:copper_wall_torch': 'minecraft:copper_torch',
    'minecraft:spawner': 'minecraft:mob_spawner',
    'minecraft:stone_slab': 'minecraft:normal_stone_slab',
    'minecraft:stonecutter': 'minecraft:stonecutter_block',
    'minecraft:sugar_cane': 'minecraft:reeds',
    'minecraft:tall_seagrass': 'minecraft:seagrass',
    'minecraft:terracotta': 'minecraft:hardened_clay',
    'minecraft:tripwire': 'minecraft:trip_wire',
    'minecraft:twisting_vines_plant': 'minecraft:twisting_vines',
    'minecraft:wall_torch': 'minecraft:torch',
    'minecraft:water_cauldron': 'minecraft:cauldron',
    'minecraft:waxed_copper_block': 'minecraft:waxed_copper',
    'minecraft:weeping_vines_plant': 'minecraft:weeping_vines',
    # Pre-1.13 Java names some packs still use in matchBlocks.
    'minecraft:stonebrick': 'minecraft:stone_bricks',
    'minecraft:grass': 'minecraft:grass_block',
}
for _wood in ('acacia', 'birch', 'jungle', 'spruce', 'mangrove', 'cherry', 'bamboo', 'crimson', 'warped', 'pale_oak'):
    JAVA_RENAMES[f'minecraft:{_wood}_sign'] = f'minecraft:{_wood}_standing_sign'
JAVA_RENAMES['minecraft:dark_oak_sign'] = 'minecraft:darkoak_standing_sign'
JAVA_RENAMES['minecraft:dark_oak_wall_sign'] = 'minecraft:darkoak_wall_sign'

# The pre-1.13 names above are not Java ids of today; java_id never maps a Bedrock block back to them.
PRE_FLATTENING_NAMES = ('minecraft:grass', 'minecraft:stonebrick')
# Java ids that take their rename even where Bedrock knows the id: Java's snow
# is the snow layer (Bedrock's snow is the snow block), and grass is the
# pre-1.13 name of grass_block.
ALWAYS_RENAMED = ('minecraft:snow', 'minecraft:grass')

# Legacy keys of the resource-pack blocks.json and the block each one stands for.
# Keys whose block has its own entry, flattened families (wool, log, coral...)
# and blocks the game no longer has map to None and are left out.
LEGACY_KEYS = {
    'grass': 'minecraft:grass_block',
    'seaLantern': 'minecraft:sea_lantern',
    'tripWire': 'minecraft:trip_wire',
    'pistonArmCollision': 'minecraft:piston_arm_collision',
    'stickyPistonArmCollision': 'minecraft:sticky_piston_arm_collision',
    'chain': 'minecraft:iron_chain',
}

# Rule document fields that list block ids.
ID_LISTS = ('fullCubeBlocks', 'opaqueBlocks', 'customTintBlocks', 'modelGeometryBlocks', 'leafDistanceLeaves',
            'leafDistanceLogs', 'sourceBlocks')
# Rule document maps keyed by block id. grassTints, foliageTints and
# customTints are keyed by biome or tint table and stay as they are.
ID_MAPS = ('baseTextures', 'baseTextureVariants', 'textureOrientations', 'modelTintTypes')


def known_blocks(samples):
    """Every block id the game knows (bedrock-samples metadata/vanilladata_modules/mojang-blocks.json)."""
    path = Path(samples) / 'metadata/vanilladata_modules/mojang-blocks.json'
    return {item['name'] for item in json.loads(path.read_text(encoding='utf-8'))['data_items']}


def bedrock_ids(java, known):
    """Bedrock block ids for a Java id; [] when Bedrock has no such block."""
    java = java if ':' in java else 'minecraft:' + java
    if java in known and java not in ALWAYS_RENAMED:
        return [java]
    renamed = JAVA_RENAMES.get(java)
    if renamed is None:
        return []
    return [name for name in (renamed if isinstance(renamed, list) else [renamed]) if name in known]


def java_id(bedrock):
    """The Java id of a Bedrock block (the reverse of a one-to-one rename, otherwise the same id)."""
    for java, renamed in JAVA_RENAMES.items():
        if renamed == bedrock and java not in PRE_FLATTENING_NAMES:
            return java
    return bedrock


def legacy_key_id(key, keys, known):
    """The block a resource-pack blocks.json key stands for, or None to leave the key out."""
    identifier = key if ':' in key else 'minecraft:' + key
    if identifier in known:
        return identifier
    renamed = LEGACY_KEYS.get(key)
    if renamed and renamed in known and renamed.removeprefix('minecraft:') not in keys:
        return renamed
    return None


def sanitize(document, known):
    """Drops block ids the game does not have from a rule document.

    A rule that named blocks and has none left is removed: an empty block list
    would match every block. Returns ({id: [where]}, [removed rule ids]).
    """
    dropped, removed = {}, []

    def keep(block, where):
        if block in known:
            return True
        dropped.setdefault(block, set()).add(where)
        return False

    for field in ID_LISTS:
        if isinstance(document.get(field), list):
            document[field] = [block for block in document[field] if keep(block, field)]
    for field in ID_MAPS:
        if isinstance(document.get(field), dict):
            # Only minecraft: ids can be checked against the game's block list; other namespaces stay.
            document[field] = {block: value for block, value in document[field].items()
                               if not block.startswith('minecraft:') or keep(block, field)}
    rules = []
    for rule in document.get('rules', []):
        emptied = False
        for field in ('blocks', 'connectBlocks'):
            if rule.get(field):
                where = f'rule {rule.get("id")} {field}'
                rule[field] = [block for block in rule[field] if keep(block, where)]
                emptied |= not rule[field]
        for field in ('blockMatchers', 'connectBlockMatchers'):
            if rule.get(field):
                where = f'rule {rule.get("id")} {field}'
                rule[field] = [clause for clause in rule[field] if keep(clause.get('block'), where)]
                emptied |= not rule[field]
        if emptied:
            removed.append(rule.get('id'))
        else:
            rules.append(rule)
    if 'rules' in document:
        document['rules'] = rules
    return {block: sorted(places) for block, places in sorted(dropped.items())}, removed
