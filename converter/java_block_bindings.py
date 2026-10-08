"""Resolve Java block sprites to Bedrock materials without changing source pixels.

The reference editions establish material identity: each Bedrock terrain
material is matched to the Java sprite with the same item or block name, a
known legacy name, or identical vanilla pixels. Bedrock block faces then take
their material's sprite, and the author's models refine that. A full-cube
block keeps each Java state's model choices, rotations and UVs as texture
variants, with extra elements on the cube's surface as face layers; every
other model part is kept as geometry (java_planar_bindings). Author models
keep their parent chains. What cannot be bound exactly, including unsupported
state predicates, is reported rather than guessed.
"""
from collections import defaultdict
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import struct
import zipfile

from PIL import Image

from block_ids import java_id, known_blocks, legacy_key_id
from block_shapes import single_slab
from java_block_states import states_java_to_bedrock, state_reference

FACES = ('north', 'east', 'south', 'west', 'up', 'down')
JAVA_BLOCK = 'assets/minecraft/textures/block/'
JAVA_ITEM = 'assets/minecraft/textures/item/'
JAVA_BLOCKSTATES = 'assets/minecraft/blockstates/'
# How Java picks among weighted model choices: per block position for variants,
# and per multipart group (with its own seed) for multipart blockstates.
VARIANT_SELECTION = 'java-26.2-block-position'
MULTIPART_SELECTION = 'java-26.2-multipart-block-position'
# Java adds model weights up in a 32-bit int.
MAX_TOTAL_WEIGHT = 2147483647
# Model parents followed before a chain counts as excessive.
MAX_PARENT_DEPTH = 64
# The full face of a model element, as a "uv" rectangle.
CANONICAL_UV = [0, 0, 16, 16]
# One eighth of a model pixel: covers the 0.075 and 0.1 pixel shells authors
# put over a cube's faces to layer textures without z-fighting.
SURFACE_TOLERANCE = 0.125
# Plants whose Java models always become model geometry, authored or not.
ALWAYS_MODEL_GEOMETRY = {'short_grass', 'tall_grass', 'seagrass', 'tall_seagrass'}
# Derived engine states that need Java's leaves and logs tags.
TAG_DERIVED_STATES = ('bct:leaf_distance', 'bct:vine_up')

# These are renamed material identities, not fuzzy string substitutions. In
# particular sandstone_smooth is the old *cut* sandstone name, and smooth stone
# sides must not be confused with raw stone or polished andesite.
_RENAMED_SPRITES = {
    'brick': 'bricks', 'stonebrick': 'stone_bricks',
    'stonebrick_carved': 'chiseled_stone_bricks',
    'stonebrick_cracked': 'cracked_stone_bricks', 'stonebrick_mossy': 'mossy_stone_bricks',
    'cobblestone_mossy': 'mossy_cobblestone', 'nether_brick': 'nether_bricks',
    'red_nether_brick': 'red_nether_bricks', 'end_bricks': 'end_stone_bricks',
    'sandstone_normal': 'sandstone', 'sandstone_carved': 'chiseled_sandstone',
    'sandstone_smooth': 'cut_sandstone', 'red_sandstone_normal': 'red_sandstone',
    'red_sandstone_carved': 'chiseled_red_sandstone', 'red_sandstone_smooth': 'cut_red_sandstone',
    'quartz_block_chiseled': 'chiseled_quartz_block',
    'quartz_block_chiseled_top': 'chiseled_quartz_block_top',
    'quartz_block_lines': 'quartz_pillar', 'quartz_block_lines_top': 'quartz_pillar_top',
    'quartz_ore': 'nether_quartz_ore', 'prismarine_dark': 'dark_prismarine',
    'prismarine_rough': 'prismarine', 'ice_packed': 'packed_ice',
    'sponge_wet': 'wet_sponge', 'hardened_clay': 'terracotta',
    'stone_slab_top': 'smooth_stone', 'stone_slab_side': 'smooth_stone_slab_side',
    'slime': 'slime_block', 'noteblock': 'note_block', 'mob_spawner': 'spawner',
    'grass_top': 'grass_block_top', 'grass_side': 'grass_block_side',
    'grass_side_snowed': 'grass_block_snow', 'grass_path_top': 'dirt_path_top',
    'grass_path_side': 'dirt_path_side', 'dirt_podzol_top': 'podzol_top',
    'dirt_podzol_side': 'podzol_side', 'dirt_with_roots': 'rooted_dirt',
    'farmland_dry': 'farmland', 'farmland_wet': 'farmland_moist',
    'furnace_front_off': 'furnace_front', 'blast_furnace_front_off': 'blast_furnace_front',
    'smoker_front_off': 'smoker_front', 'dispenser_front_horizontal': 'dispenser_front',
    'dropper_front_horizontal': 'dropper_front', 'observer_back_lit': 'observer_back_on',
    'pumpkin_face_off': 'carved_pumpkin', 'pumpkin_face_on': 'jack_o_lantern',
    'redstone_lamp_off': 'redstone_lamp', 'redstone_torch_on': 'redstone_torch',
    'torch_on': 'torch', 'repeater_off': 'repeater', 'comparator_off': 'comparator',
    'piston_top_normal': 'piston_top', 'trapdoor': 'oak_trapdoor',
    'anvil_base': 'anvil', 'anvil_top_damaged_0': 'anvil_top',
    'anvil_top_damaged_1': 'chipped_anvil_top', 'anvil_top_damaged_2': 'damaged_anvil_top',
    'honey_side': 'honey_block_side', 'honey_top': 'honey_block_top',
    'honey_bottom': 'honey_block_bottom', 'honeycomb': 'honeycomb_block',
    'endframe_side': 'end_portal_frame_side', 'endframe_top': 'end_portal_frame_top',
    'endframe_eye': 'end_portal_frame_eye', 'portal': 'nether_portal',
    'fletcher_table_side1': 'fletching_table_side', 'fletcher_table_side2': 'fletching_table_front',
    'fletcher_table_top': 'fletching_table_top', 'compost': 'composter_compost',
    'compost_ready': 'composter_ready', 'dried_kelp_side_a': 'dried_kelp_side',
    'dried_kelp_side_b': 'dried_kelp_side', 'bamboo_stem': 'bamboo_stalk',
    'bamboo_leaf': 'bamboo_large_leaves', 'bamboo_small_leaf': 'bamboo_small_leaves',
    'bamboo_sapling': 'bamboo_stage0', 'azalea_leaves_flowers': 'flowering_azalea_leaves',
    'deadbush': 'dead_bush', 'web': 'cobweb', 'reeds': 'sugar_cane',
    'waterlily': 'lily_pad', 'mushroom_brown': 'brown_mushroom', 'mushroom_red': 'red_mushroom',
    'mushroom_block_skin_brown': 'brown_mushroom_block',
    'mushroom_block_skin_red': 'red_mushroom_block', 'mushroom_block_skin_stem': 'mushroom_stem',
    'tallgrass': 'short_grass', 'flower_rose': 'poppy', 'flower_houstonia': 'azure_bluet',
    'turtle_egg_not_cracked': 'turtle_egg', 'trip_wire': 'tripwire', 'trip_wire_source': 'tripwire_hook',
    'water_still_grey': 'water_still', 'water_flow_grey': 'water_flow',
    'crimson_nylium_top': 'crimson_nylium', 'warped_nylium_top': 'warped_nylium',
    'crimson_log_side': 'crimson_stem', 'crimson_log_top': 'crimson_stem_top',
    'warped_stem_side': 'warped_stem', 'stripped_crimson_stem_side': 'stripped_crimson_stem',
    'stripped_warped_stem_side': 'stripped_warped_stem',
}


def _legacy_aliases():
    """Old (Bedrock and pre-1.13 Java) sprite names -> today's Java sprite name."""
    aliases = dict(_RENAMED_SPRITES)
    for stone in ('andesite', 'diorite', 'granite'):
        aliases['stone_' + stone] = stone
        aliases['stone_' + stone + '_smooth'] = 'polished_' + stone
    # Pre-1.13 wood names: big_oak is dark oak, the others kept their names.
    old_woods = [('big_oak', 'dark_oak')] + [(wood, wood) for wood in ('oak', 'spruce', 'birch', 'jungle', 'acacia')]
    for old, wood in old_woods:
        for old_name, name in [('planks_' + old, wood + '_planks'), ('log_' + old, wood + '_log'),
                               ('log_' + old + '_top', wood + '_log_top'), ('leaves_' + old, wood + '_leaves'),
                               ('sapling_' + old, wood + '_sapling')]:
            aliases[old_name] = name
    aliases['sapling_roofed_oak'] = 'dark_oak_sapling'
    for wood in ('oak', 'spruce', 'birch', 'jungle', 'acacia', 'dark_oak', 'iron', 'crimson', 'warped'):
        old = 'wood' if wood == 'oak' else wood
        for old_half, half in [('lower', 'bottom'), ('upper', 'top')]:
            aliases[f'door_{old}_{old_half}'] = f'{wood}_door_{half}'
            aliases[f'{wood}_door_{old_half}'] = f'{wood}_door_{half}'
    for wood in ('cherry', 'mangrove', 'pale_oak'):
        for prefix in ('', 'stripped_'):
            aliases[prefix + wood + '_log_side'] = prefix + wood + '_log'
    for old in ('white', 'orange', 'magenta', 'light_blue', 'yellow', 'lime', 'pink', 'gray',
                'silver', 'cyan', 'purple', 'blue', 'brown', 'green', 'red', 'black'):
        color = 'light_gray' if old == 'silver' else old
        for prefix, suffix in [('wool_colored_', '_wool'), ('concrete_', '_concrete'),
                               ('concrete_powder_', '_concrete_powder'), ('glass_', '_stained_glass'),
                               ('glass_pane_top_', '_stained_glass_pane_top'),
                               ('glazed_terracotta_', '_glazed_terracotta'),
                               ('hardened_clay_stained_', '_terracotta')]:
            aliases[prefix + old] = color + suffix
    for crop, count in [('wheat', 8), ('carrots', 4), ('potatoes', 4), ('beetroots', 4),
                        ('nether_wart', 3), ('cocoa', 3), ('torchflower_crop', 2)]:
        for stage in range(count):
            aliases[f'{crop}_stage_{stage}'] = f'{crop}_stage{stage}'
    for kind in ('activator', 'detector'):
        aliases['rail_' + kind] = kind + '_rail'
        aliases['rail_' + kind + '_powered'] = kind + '_rail_on'
    aliases.update(rail_golden='powered_rail', rail_golden_powered='powered_rail_on',
                   rail_normal='rail', rail_normal_turned='rail_corner')
    for flower in ('allium', 'blue_orchid', 'cornflower', 'dandelion', 'lily_of_the_valley',
                   'oxeye_daisy', 'wither_rose'):
        aliases['flower_' + flower] = flower
    for color in ('white', 'orange', 'pink', 'red'):
        aliases['flower_tulip_' + color] = color + '_tulip'
    for old, coral in [('blue', 'tube'), ('pink', 'brain'), ('purple', 'bubble'), ('red', 'fire'), ('yellow', 'horn')]:
        for old_suffix, prefix in [('', ''), ('_dead', 'dead_')]:
            for old_prefix, suffix in [('coral_', '_coral_block'), ('coral_plant_', '_coral'),
                                       ('coral_fan_', '_coral_fan')]:
                aliases[old_prefix + old + old_suffix] = prefix + coral + suffix
    for old, plant in [('fern', 'large_fern'), ('grass', 'tall_grass'), ('syringa', 'lilac'),
                       ('paeonia', 'peony'), ('rose', 'rose_bush'), ('sunflower', 'sunflower')]:
        for half in ('top', 'bottom'):
            aliases[f'double_plant_{old}_{half}'] = f'{plant}_{half}'
    for prefix in ('command_block', 'chain_command_block', 'repeating_command_block'):
        for face in ('front', 'back', 'side', 'conditional'):
            aliases[f'{prefix}_{face}_mipmap'] = f'{prefix}_{face}'
    aliases.update(beehive_top='beehive_end', lightning_rod_powered='lightning_rod_on',
                   stonecutter2_saw='stonecutter_saw', weeping_vines_base='weeping_vines_plant',
                   weeping_vines_bottom='weeping_vines', eyeblossom_blooming='open_eyeblossom',
                   eyeblossom_dormant='closed_eyeblossom')
    return aliases


def _log_axis_blocks():
    """Logs, wood, stems and hyphae: the blocks with a pillar axis, shown once per axis in the gallery."""
    blocks = set()
    for species in ('oak', 'spruce', 'birch', 'jungle', 'acacia', 'dark_oak', 'mangrove', 'cherry', 'pale_oak'):
        for prefix in ('', 'stripped_'):
            for part in ('log', 'wood'):
                blocks.add(prefix + species + '_' + part)
    for species in ('crimson', 'warped'):
        for prefix in ('', 'stripped_'):
            for part in ('stem', 'hyphae'):
                blocks.add(prefix + species + '_' + part)
    return blocks


LEGACY_ALIASES = _legacy_aliases()
# Bedrock block names (current ids and legacy blocks.json keys) whose Java block has another name.
BLOCK_ALIASES = {'stonebrick': 'stone_bricks', 'brick_block': 'bricks',
                 'grass': 'grass_block', 'grass_path': 'dirt_path', 'dirt_with_roots': 'rooted_dirt',
                 'azalea_leaves_flowered': 'flowering_azalea_leaves',
                 'seaLantern': 'sea_lantern', 'snow': 'snow_block',
                 'mossy_cobblestone': 'mossy_cobblestone', 'nether_brick': 'nether_bricks',
                 'red_nether_brick': 'red_nether_bricks', 'end_bricks': 'end_stone_bricks',
                 'hardened_clay': 'terracotta', 'slime': 'slime_block', 'noteblock': 'note_block'}
LOG_AXIS_BLOCKS = _log_axis_blocks()

# World face coordinates match engine/core.mjs FACE_EDGES: U points right and V
# points down when a face is viewed from outside the block.
FACE_NORMALS = {'north': (0, 0, -1), 'east': (1, 0, 0), 'south': (0, 0, 1),
                'west': (-1, 0, 0), 'up': (0, 1, 0), 'down': (0, -1, 0)}
FACE_UV = {'north': ((-1, 0, 0), (0, -1, 0)), 'east': ((0, 0, -1), (0, -1, 0)),
           'south': ((1, 0, 0), (0, -1, 0)), 'west': ((0, 0, 1), (0, -1, 0)),
           'up': ((1, 0, 0), (0, 0, 1)), 'down': ((1, 0, 0), (0, 0, -1))}
# Texture orientations (engine/tiles.mjs) as 2x2 matrices (a, b, c, d) = [[a, b], [c, d]]:
# 0-3 turn the texture by quarter turns, 4-7 mirror it first.
ORIENTATION_MATRICES = ((1, 0, 0, 1), (0, -1, 1, 0), (-1, 0, 0, -1), (0, 1, -1, 0),
                        (-1, 0, 0, 1), (0, 1, 1, 0), (1, 0, 0, -1), (0, -1, -1, 0))
# Strings and comments of JSON with comments, which Java model files may have.
_STRING_OR_COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/')


def resolve_bindings(stack, vanilla_jar, samples_resource_pack):
    """Return auditable static face/material bindings and unresolved state choices.

    Java paths can originate from the supplied pack stack or its vanilla fallback.
    No source or destination is written. Bedrock material keys omit extensions,
    as they do in terrain_texture.json. Texture paths retain .png extensions.
    """
    samples = Path(samples_resource_pack).resolve()
    blocks, unmapped_keys = _blocks_by_current_id(_json((samples / 'blocks.json').read_bytes()), samples)
    terrain = _json((samples / 'textures/terrain_texture.json').read_bytes())['texture_data']
    materials = sorted({path for value in terrain.values() for path in _texture_paths(value.get('textures'))})
    with zipfile.ZipFile(vanilla_jar) as vanilla:
        names = set(vanilla.namelist())
        sprites_by_pixels = _vanilla_sprites_by_pixels(vanilla, names)
        replacements, substitution_evidence, model_issues = _model_substitutions(stack, vanilla)
        material_bindings, material_evidence, unresolved_materials, model_ambiguities = _bind_materials(
            materials, samples, sprites_by_pixels, names, stack, replacements, substitution_evidence)
        bindings, unresolved_faces, state_ambiguities = _bind_faces(blocks, terrain, material_bindings)
        cube_metadata = _cube_metadata(stack, vanilla, blocks, bindings, terrain, samples)
        geometry_blocks = _add_model_geometry(cube_metadata, _Models(vanilla, stack), stack, names)
        _fill_base_textures(bindings, cube_metadata)
    # Deduplicate reports for a texture reused by multiple atlas materials.
    model_ambiguities = list({entry['java_texture']: entry for entry in model_ambiguities}.values())
    return {'baseTextures': bindings, 'materialBindings': material_bindings,
            'authoredMaterialBindings': {key: value for key, value in material_bindings.items()
                                         if value in stack.files},
            'inheritedMaterialBindings': {key: value for key, value in material_bindings.items()
                                          if value not in stack.files},
            'material_evidence': material_evidence, 'unresolved_materials': unresolved_materials,
            'unresolved_faces': unresolved_faces, 'state_ambiguities': state_ambiguities,
            'model_texture_ambiguities': model_ambiguities, 'model_resolution_issues': model_issues,
            'mapped_faces': sum(len(value) for value in bindings.values()),
            'mapped_materials': len(material_bindings), 'source_artwork_modified': False,
            'unmapped_block_keys': sorted(unmapped_keys),
            'geometry_converted': False, 'model_geometry_bindings_preserved': bool(geometry_blocks),
            'full_support_verified': False, **cube_metadata}


class _Models:
    """Java block models: the author's pack stack (when given) over the vanilla jar."""

    def __init__(self, vanilla, stack=None):
        self.vanilla = vanilla
        self.names = set(vanilla.namelist())
        self.stack = stack
        self.cache = {}
        # Model elements and faces the cube bindings leave out (reported by resolve_bindings).
        self.excluded_geometry = set()
        self.excluded_faces = set()

    def read(self, path):
        """A JSON file of the pack stack, else of the vanilla jar; KeyError when neither has it."""
        if self.stack is not None and path in self.stack.files:
            return _json(self.stack.read(path))
        return _json(self.vanilla.read(path))

    def resolve(self, path, chain=()):
        """The model with its parents merged in: the child's fields win, texture variables merge."""
        if path in self.cache:
            return self.cache[path]
        if path in chain or len(chain) >= MAX_PARENT_DEPTH:
            raise ValueError('Cyclic or excessive model parent chain: ' + path)
        document = self.read(path)
        parent = document.get('parent')
        inherited = {}
        if parent and not parent.startswith('builtin/'):
            inherited = self.resolve(_resource(parent, 'models'), chain + (path,))
        result = {**inherited, **document}
        result['textures'] = {**inherited.get('textures', {}), **document.get('textures', {})}
        self.cache[path] = result
        return result

    def variables(self, path):
        """{texture variable: sprite path} of the resolved model, following #variable references."""
        textures = self.resolve(path).get('textures', {})
        result = {}
        for key in textures:
            value = textures[key]
            visited = {key}
            while isinstance(value, str) and value.startswith('#'):
                reference = value[1:]
                if reference in visited:
                    raise ValueError('Cyclic model texture variable: ' + path + '#' + key)
                visited.add(reference)
                value = textures.get(reference)
            # Java 26.2 can attach render flags to a sprite reference. Those
            # flags do not change the sprite's identity or face coordinates.
            if isinstance(value, dict):
                value = value.get('sprite')
            if isinstance(value, str):
                result[key] = _resource(value, 'textures')
        return result


def _rotate_vector(vector, x_degrees, y_degrees):
    """A vector turned by the blockstate's x then y rotation (quarter turns)."""
    x, y, z = vector
    for _ in range((x_degrees % 360) // 90):
        x, y, z = x, z, -y
    for _ in range((y_degrees % 360) // 90):
        x, y, z = -z, y, x
    return x, y, z


def _rotated_face_coordinates(face, data, selection):
    """(world face, texture orientation 0-7) a full model face shows after the blockstate rotation."""
    x, y = selection.get('x', 0), selection.get('y', 0)
    if type(x) is not int or type(y) is not int or x % 90 or y % 90:
        raise ValueError('Model rotation is not a quarter turn')
    normal = _rotate_vector(FACE_NORMALS[face], x, y)
    target = next(name for name, value in FACE_NORMALS.items() if value == normal)
    uv = data.get('uv', CANONICAL_UV)
    if len(uv) != 4 or {uv[0], uv[2]} != {0, 16} or {uv[1], uv[3]} != {0, 16}:
        raise ValueError('Cropped model face UV requires a texture-coordinate adapter')
    rotation = data.get('rotation', 0)
    if type(rotation) is not int or rotation % 90:
        raise ValueError('Face UV rotation is not a quarter turn')
    # Java CuboidFace rotates vertex indices 0,1,2,3 by +quarter-turns;
    # its vertex UV order is (0,0), (0,1), (1,1), (1,0).
    face_rotation = ORIENTATION_MATRICES[(-rotation // 90) % 4]
    # A reversed UV rectangle ([16, 0, 0, 16]) mirrors the texture along that axis.
    uv_flip = (1 if uv[2] > uv[0] else -1, 0, 0, 1 if uv[3] > uv[1] else -1)
    rotated_u, rotated_v = (_rotate_vector(axis, x, y) for axis in FACE_UV[face])
    target_u, target_v = FACE_UV[target]
    world_to_local = (_dot(rotated_u, target_u), _dot(rotated_u, target_v),
                      _dot(rotated_v, target_u), _dot(rotated_v, target_v))
    matrix = _matrix_product(_matrix_product(uv_flip, face_rotation), world_to_local)
    if selection.get('uvlock') and (x % 360 or y % 360):
        if rotation % 360 or uv != CANONICAL_UV:
            raise ValueError('UV lock combined with authored face rotation/reflection requires a UV-lock adapter')
        # Java's UV-lock face transformation cancels the geometry rotation
        # for the canonical, unmodified unit-square UV rectangle.
        matrix = ORIENTATION_MATRICES[0]
    return target, ORIENTATION_MATRICES.index(matrix)


def _rotated_cube_faces(models, selection):
    """The faces, orientations, tint indices and face layers a cube model choice shows in world space."""
    path = _resource(selection['model'], 'models')
    model = models.resolve(path)
    textures = _face_textures(models, path)
    if textures is None:
        raise ValueError('Custom shape model is outside cube texture binding')
    element = _cube_element(model)
    faces, orientations, tint_indices = {}, {}, {}
    for face, texture in textures.items():
        data = element['faces'][face]
        target, orientation = _rotated_face_coordinates(face, data, selection)
        faces[target] = texture
        orientations[target] = orientation
        tint = data.get('tintindex', -1)
        if type(tint) is not int or tint < -1:
            raise ValueError('Invalid model face tint index')
        if tint >= 0:
            tint_indices[target] = tint
    if set(faces) != set(FACES):
        raise ValueError('Model does not resolve all six cube faces')
    variables = models.variables(path)
    face_layers = defaultdict(list)
    for index, layer in enumerate(model.get('elements', [])):
        if layer is element:
            continue
        excluded = []
        for face, data in layer.get('faces', {}).items():
            if not _cube_surface_layer(layer, face):
                excluded.append(face)
                continue
            target, orientation = _rotated_face_coordinates(face, data, selection)
            texture = _face_texture(variables, data)
            if not texture:
                raise ValueError('Unresolved model face layer texture')
            tint = data.get('tintindex', -1)
            if type(tint) is not int or tint < -1:
                raise ValueError('Invalid model layer tint index')
            face_layers[target].append({'texture': texture, 'orientation': orientation, 'tintIndex': tint})
        if excluded:
            models.excluded_geometry.add((path, 'additional_model_elements', (index,)))
            models.excluded_faces.add((path, index, tuple(sorted(excluded))))
    return {'faces': faces, 'orientations': orientations,
            'tintIndices': tint_indices, 'faceLayers': dict(face_layers)}


def _multipart_matches(condition, states):
    """Whether a multipart "when" condition (with OR / AND lists and a|b|!c values) matches the states."""
    if not isinstance(condition, dict):
        raise ValueError('Invalid multipart condition')
    tests = []
    for name, expected in condition.items():
        if name in ('OR', 'AND'):
            if not isinstance(expected, list) or not expected:
                raise ValueError('Invalid multipart logical condition')
            values = [_multipart_matches(item, states) for item in expected]
            tests.append(any(values) if name == 'OR' else all(values))
        else:
            if name not in states:
                raise ValueError('Unknown multipart state property: ' + name)
            expected = str(expected).lower() if isinstance(expected, bool) else str(expected)
            negate = expected.startswith('!')
            options = expected[1:] if negate else expected
            match = str(states[name]) in options.split('|')
            tests.append(not match if negate else match)
    return all(tests)


def _variant_states(key):
    """The states of a blockstate variant key: "facing=east,lit=false" -> {'facing': 'east', 'lit': 'false'}."""
    return dict(item.split('=', 1) for item in key.split(',') if item)


def _as_list(choices):
    """A blockstate apply or variant value (one model choice or a weighted list) as a list."""
    return choices if isinstance(choices, list) else [choices]


def _cube_texture_variants(models, blockstate, identifier):
    """Keep state selections and weighted model order from the Java source.

    Returns ({Bedrock block: [{'states', faces...}]}, issues): each Java state's
    cube binding, or its weighted modelChoices in Java's order when they differ.
    """
    document = models.read(blockstate)
    output = defaultdict(list)
    issues = []
    for states, choices, algorithm in _cube_state_selections(document, identifier, models):
        selection = ','.join(name + '=' + str(value) for name, value in states.items())
        try:
            try:
                targets = states_java_to_bedrock(identifier, states)
            except ValueError as error:
                # A block the state table does not list is fine while no state is selected.
                if states or not str(error).startswith('No verified Java state reference'):
                    raise
                targets = [{'block': identifier, 'states': {}}]
            resolved = []
            for choice in _as_list(choices):
                weight = choice.get('weight', 1)
                if type(weight) is not int or weight < 1:
                    raise ValueError('Invalid Java model weight')
                resolved.append({'weight': weight, **_rotated_cube_faces(models, choice)})
            if not resolved or sum(value['weight'] for value in resolved) > MAX_TOTAL_WEIGHT:
                raise ValueError('Invalid total Java model weight')
            first = _without_weight(resolved[0])
            # Choices that look the same need no weighted pick.
            if all(_without_weight(choice) == first for choice in resolved):
                binding = first
            else:
                binding = {'modelSelection': algorithm, 'modelChoices': resolved}
            for target in targets:
                output[target['block']].append({'states': target['states'], **binding})
        except (KeyError, ValueError) as error:
            issues.append({'block': identifier, 'java_state': selection, 'reason': str(error)})
    return dict(output), issues


def _without_weight(choice):
    return {key: value for key, value in choice.items() if key != 'weight'}


def _cube_state_selections(document, identifier, models):
    """[(Java states, model choices, selection algorithm)] for each state of a cube blockstate.

    A multipart blockstate is read for every verified Java state; a state may
    show one cube part plus decoration parts (which are left to model
    geometry), but never two cube parts.
    """
    if 'multipart' not in document:
        return [(_variant_states(state), choices, VARIANT_SELECTION)
                for state, choices in document.get('variants', {}).items()]
    rows = state_reference()['blocks'].get(identifier)
    if not rows:
        raise ValueError('No verified Java states for multipart selection: ' + identifier)
    selections = []
    seen = set()
    for row in rows:
        state = row['java']
        key = tuple(sorted(state.items()))
        if key in seen:
            continue
        seen.add(key)
        matching = [part for part in document['multipart'] if _multipart_matches(part.get('when', {}), state)]
        if len(matching) > 1:
            matching = _cube_part_only(models, matching)
        if len(matching) != 1:
            raise ValueError('Multipart state ' + str(state) + ' selects ' + str(len(matching))
                             + ' models; exactly one cube model is required')
        selections.append((state, matching[0]['apply'], MULTIPART_SELECTION))
    return selections


def _cube_part_only(models, matching):
    """The matching multipart parts reduced to their one cube part, the others recorded as decoration.

    The parts stay as they are when not exactly one of them is a cube.
    """
    cube_parts = []
    for part in matching:
        cubes = [_cube_element(models.resolve(_resource(choice['model'], 'models'))) is not None
                 for choice in _as_list(part['apply'])]
        if any(cubes) and not all(cubes):
            raise ValueError('Multipart weighted selection mixes cube and custom geometry')
        if all(cubes):
            cube_parts.append(part)
    if len(cube_parts) != 1:
        return matching
    for part in matching:
        if part is not cube_parts[0]:
            for choice in _as_list(part['apply']):
                models.excluded_geometry.add((_resource(choice['model'], 'models'), 'multipart_decoration', ()))
    return cube_parts


def _json(data):
    """JSON text or bytes that may have a byte-order mark and comments."""
    text = data.decode('utf-8-sig') if isinstance(data, bytes) else data
    return json.loads(_STRING_OR_COMMENT.sub(_keep_strings, text))


def _keep_strings(match):
    return match[0] if match[0].startswith('"') else ''


def _texture_paths(value):
    """Material paths of a terrain_texture.json "textures" value (a path, a list, or variations)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [path for item in value for path in _texture_paths(item)]
    if isinstance(value, dict):
        return _texture_paths(value.get('path', value.get('variations', [])))
    return []


def _pixel_digest(data):
    """A hash of an image's size and RGBA pixels, equal for images that look the same."""
    with Image.open(io.BytesIO(data)) as image:
        return hashlib.sha256(struct.pack('<II', *image.size) + image.convert('RGBA').tobytes()).hexdigest()


def _resource(identifier, kind):
    """The pack path of a namespaced model ('models', .json) or texture (.png); ValueError for unsafe ids."""
    namespace, name = identifier.split(':', 1) if ':' in identifier else ('minecraft', identifier)
    if not re.fullmatch(r'[a-z0-9_.-]+', namespace) or '..' in PurePosixPath(name).parts or name.startswith('/'):
        raise ValueError('Unsafe resource identifier: ' + identifier)
    return f'assets/{namespace}/{kind}/{name}' + ('.json' if kind == 'models' else '.png')


def _blocks_by_current_id(blocks, samples):
    """(blocks.json with every key turned into its current block name, the keys left out).

    blocks.json keeps legacy keys (grass, seaLantern); bindings use the current
    block id. Without Mojang's block list next to the samples the keys stay.
    """
    unmapped_keys = []
    registry = samples.parent / 'metadata/vanilladata_modules/mojang-blocks.json'
    if not registry.is_file():
        return blocks, unmapped_keys
    known = known_blocks(samples.parent)
    current = {}
    for key, definition in blocks.items():
        if not isinstance(definition, dict):
            current[key] = definition
            continue
        identifier = legacy_key_id(key, blocks, known)
        if identifier is None:
            unmapped_keys.append(key)
            continue
        current[identifier.removeprefix('minecraft:')] = definition
    return current, unmapped_keys


def _vanilla_sprites_by_pixels(vanilla, names):
    """{pixel digest: vanilla block sprites with those pixels}."""
    by_pixels = defaultdict(list)
    for path in sorted(names):
        if path.startswith(JAVA_BLOCK) and path.endswith('.png'):
            by_pixels[_pixel_digest(vanilla.read(path))].append(path)
    return by_pixels


def _model_substitutions(stack, vanilla):
    """Compare resolved texture slots; keep conflicting author choices separate.

    Returns ({vanilla sprite: author sprites replacing it in some model},
    {vanilla sprite: [where]}, issues).
    """
    source = _Models(vanilla)
    authored = _Models(vanilla, stack)
    replacements = defaultdict(set)
    evidence = defaultdict(list)
    issues = []
    # A changed parent can affect vanilla child models. Resolve every vanilla
    # block model, not only the files present in the pack, and compare each
    # child's final slots.
    for path in sorted(source.names):
        if '/models/block/' not in path or not path.endswith('.json'):
            continue
        try:
            old, new = source.variables(path), authored.variables(path)
        except (KeyError, ValueError) as error:
            issues.append({'model': path, 'reason': str(error)})
            continue
        for slot, original in old.items():
            replacement = new.get(slot)
            if replacement and replacement != original:
                replacements[original].add(replacement)
                evidence[original].append({'model': path, 'slot': slot, 'texture': replacement})
    return replacements, evidence, issues


def _bind_materials(materials, samples, sprites_by_pixels, names, stack, replacements, substitution_evidence):
    """The Java sprite of each Bedrock terrain material.

    Returns ({material: sprite}, {material: evidence}, unresolved materials,
    model-specific texture substitutions).
    """
    bindings, evidence, unresolved, model_ambiguities = {}, {}, [], []
    for material in materials:
        chosen, reason, candidates = _java_sprite(material, samples, sprites_by_pixels, names, stack)
        if chosen is None:
            reason = 'ambiguous_vanilla_pixels' if candidates else 'no_reference_material_identity'
            unresolved.append({'bedrock_material': material, 'java_candidates': candidates, 'reason': reason})
            continue
        options = replacements.get(chosen, set())
        if options:
            # A source shared by several blocks cannot globally take one
            # block's model override. Expose it for a state/model adapter.
            model_ambiguities.append({'java_texture': chosen, 'java_candidates': sorted(options),
                                      'model_slots': substitution_evidence[chosen],
                                      'reason': 'model_specific_texture_substitution'})
        if chosen not in stack.files and chosen not in names:
            unresolved.append({'bedrock_material': material, 'java_candidates': [chosen],
                               'reason': 'missing_source_texture'})
            continue
        bindings[material] = chosen
        evidence[material] = {'java_texture': chosen, 'reference_texture': chosen, 'matching': reason,
                              'vanilla_pixels_identical': chosen in candidates,
                              'origin': 'pack_stack' if chosen in stack.files else 'vanilla_fallback'}
    return bindings, evidence, unresolved, model_ambiguities


def _java_sprite(material, samples, sprites_by_pixels, names, stack):
    """(Java sprite or None, how it was matched, vanilla sprites with the reference material's pixels)."""
    stem = PurePosixPath(material).stem
    reference = _sample_image(samples, material)
    candidates = sprites_by_pixels.get(_pixel_digest(reference.read_bytes()), []) if reference else []
    same_name = JAVA_BLOCK + stem + '.png'
    alias = JAVA_BLOCK + LEGACY_ALIASES[stem] + '.png' if stem in LEGACY_ALIASES else None
    # Bedrock item textures (candles, lanterns, leaf litter...) are Java item sprites, not the block texture.
    item = JAVA_ITEM + stem + '.png' if material.startswith('textures/items/') else None
    if item and (item in names or item in stack.files):
        return item, 'same_item_name', candidates
    if same_name in names:
        return same_name, 'same_material_name', candidates
    if alias in names:
        return alias, 'explicit_legacy_material_alias', candidates
    if len(candidates) == 1:
        return candidates[0], 'identical_vanilla_rgba_pixels', candidates
    return None, None, candidates


def _sample_image(samples, material):
    """The bedrock-samples image of a material (.tga before .png), or None."""
    return next((samples / (material + suffix) for suffix in ('.tga', '.png')
                 if (samples / (material + suffix)).is_file()), None)


def _bind_faces(blocks, terrain, material_bindings):
    """{block: {face: Java sprite}} for faces whose atlas alias resolves to one sprite.

    Returns (bindings, unresolved faces, state ambiguities): an alias with
    several sprites (a state or variation array) needs runtime selection.
    """
    bindings, unresolved_faces, state_ambiguities = {}, [], []
    for block, definition in sorted(blocks.items()):
        if not isinstance(definition, dict):
            continue
        textures = definition.get('textures', {})
        if isinstance(textures, str):
            textures = {face: textures for face in FACES}
        if not isinstance(textures, dict):
            continue
        identifier = block if ':' in block else 'minecraft:' + block
        for face in FACES:
            alias = textures.get(face, textures.get('side') if face not in ('up', 'down') else None)
            if not isinstance(alias, str):
                continue
            materials = _texture_paths(terrain.get(alias, {}).get('textures'))
            candidates = sorted({material_bindings[path] for path in materials if path in material_bindings})
            complete = bool(materials) and all(path in material_bindings for path in materials)
            if complete and len(candidates) == 1:
                bindings.setdefault(identifier, {})[face] = candidates[0]
                continue
            item = {'block': identifier, 'face': face, 'atlas_alias': alias,
                    'bedrock_materials': materials, 'java_candidates': candidates,
                    'reason': 'state_or_variation_array' if len(candidates) > 1 else 'unresolved_material'}
            unresolved_faces.append(item)
            if len(candidates) > 1:
                state_ambiguities.append(item)
    return bindings, unresolved_faces, state_ambiguities


def _state_models(document):
    """[(variant key, model choice)] of a variants blockstate; None for a multipart one."""
    if 'multipart' in document:
        return None
    result = []
    for state, choices in document.get('variants', {}).items():
        for choice in _as_list(choices):
            result.append((state, choice))
    return result


def _unit_cube(model):
    """Whether the model is exactly one unrotated 0-16 cube with all six faces."""
    elements = model.get('elements', [])
    if len(elements) != 1:
        return False
    element = elements[0]
    return (element.get('from') == [0, 0, 0] and element.get('to') == [16, 16, 16]
            and element.get('rotation', {}).get('angle', 0) == 0
            and set(element.get('faces', {})) == set(FACES))


def _cube_element(model):
    """Find the first complete cube; later exterior faces retain layer order."""
    candidates = [element for element in model.get('elements', []) if _unit_cube({'elements': [element]})]
    return candidates[0] if candidates else None


def _cube_surface_layer(element, face):
    """Recognize a full exterior face, allowing a small z-fighting offset.

    One eighth of a model pixel covers the authored 0.075/0.1 pixel shells.
    Faces inside the cube, raised foliage planes and partial panels are not
    texture layers and remain model geometry.
    """
    if face not in FACES or element.get('rotation', {}).get('angle', 0) != 0:
        return False
    start, end = element.get('from', []), element.get('to', [])
    if len(start) != 3 or len(end) != 3:
        return False
    normal = FACE_NORMALS[face]
    axis = next(index for index, value in enumerate(normal) if value)
    boundary = 16 if normal[axis] > 0 else 0
    position = end[axis] if normal[axis] > 0 else start[axis]
    if abs(position - boundary) > SURFACE_TOLERANCE:
        return False
    if any(abs(start[index]) > SURFACE_TOLERANCE or abs(end[index] - 16) > SURFACE_TOLERANCE
           for index in range(3) if index != axis):
        return False
    return element['faces'][face].get('cullface', face) == face


def _face_texture(variables, data):
    """The sprite path a model face names: a #variable of the model or a direct sprite; None when unset."""
    value = data.get('texture', '')
    if value.startswith('#'):
        return variables.get(value[1:])
    return _resource(value, 'textures') if value else None


def _face_textures(models, path):
    """{face: sprite} of a model's cube element; None when the model has no complete cube."""
    model = models.resolve(path)
    element = _cube_element(model)
    if element is None:
        return None
    variables = models.variables(path)
    result = {}
    for face, data in element['faces'].items():
        value = _face_texture(variables, data)
        if value:
            result[face] = value
    return result


def _cube_face_overrides(identifier, reference, authored, blockstate, bindings):
    """Compare texture slots in corresponding unit-cube state selections.

    A texture replacement can be applied to that block's Bedrock face only if
    every corresponding state/model choice agrees. Geometry and state rotation
    changes are reported rather than flattened into a different block design.
    """
    original_states = reference.read(blockstate).get('variants', {})
    authored_document = authored.read(blockstate)
    author_states = authored_document.get('variants', {})
    issues = []
    substitutions = defaultdict(set)
    if 'multipart' in authored_document or set(original_states) != set(author_states):
        return {}, [{'block': identifier, 'reason': 'author_blockstate_structure_changed'}]
    for state, originals in original_states.items():
        originals = _as_list(originals)
        choices = _as_list(author_states[state])
        # Pair equal model references where possible; a changed model reference
        # is still supported for a single, unambiguous choice in that state.
        pairs = []
        for choice in choices:
            matching = [item for item in originals if item.get('model') == choice.get('model')
                        and item.get('x', 0) == choice.get('x', 0) and item.get('y', 0) == choice.get('y', 0)]
            if len(matching) == 1:
                pairs.append((matching[0], choice))
            elif len(originals) == 1 and len(choices) == 1:
                pairs.append((originals[0], choice))
            else:
                issues.append({'block': identifier, 'state': state, 'reason': 'ambiguous_author_model_choice'})
                return {}, issues
        for original, choice in pairs:
            if (original.get('x', 0) % 360 != choice.get('x', 0) % 360
                    or original.get('y', 0) % 360 != choice.get('y', 0) % 360):
                return {}, [{'block': identifier, 'state': state, 'reason': 'author_model_rotation_changed'}]
            author_model = authored.resolve(_resource(choice['model'], 'models'))
            if _cube_element(author_model) is not None and not _unit_cube(author_model):
                # Cube selectors can operate underneath decoration, but the
                # native face is a complete material and cannot replace a
                # composite surface with only its bottom layer.
                return {}, [{'block': identifier, 'state': state, 'model': choice['model'],
                             'reason': 'layered_model_native_face_requires_composition'}]
            source_faces = _face_textures(reference, _resource(original['model'], 'models'))
            author_faces = _face_textures(authored, _resource(choice['model'], 'models'))
            if author_faces is None:
                return {}, [{'block': identifier, 'state': state, 'model': choice['model'],
                             'reason': 'custom_shape_model_out_of_scope'}]
            for face, texture in source_faces.items():
                if face not in author_faces:
                    return {}, [{'block': identifier, 'state': state, 'face': face,
                                 'reason': 'unresolved_author_face_texture'}]
                substitutions[texture].add(author_faces[face])
    overrides = {}
    for face, texture in bindings.items():
        options = substitutions.get(texture, set())
        if len(options) == 1:
            replacement = next(iter(options))
            if replacement == texture:
                continue
            in_pack = authored.stack is not None and replacement in authored.stack.files
            if replacement not in authored.names and not in_pack:
                issues.append({'block': identifier, 'face': face, 'texture': replacement,
                               'reason': 'missing_author_face_texture'})
            else:
                overrides[face] = replacement
        elif len(options) > 1:
            issues.append({'block': identifier, 'face': face, 'java_candidates': sorted(options),
                           'reason': 'state_dependent_author_face_texture'})
    return overrides, issues


class _Opacity:
    """Whether images are fully opaque (every alpha 255), cached per image."""

    def __init__(self, stack, vanilla, samples):
        self.stack = stack
        self.vanilla = vanilla
        self.samples = samples
        self.materials = {}
        self.sprites = {}

    def material(self, material):
        """A bedrock-samples material; False when it has no image."""
        if material not in self.materials:
            image_path = _sample_image(self.samples, material)
            if image_path:
                with Image.open(image_path) as image:
                    self.materials[material] = _fully_opaque(image)
            else:
                self.materials[material] = False
        return self.materials[material]

    def sprite(self, texture):
        """A Java sprite of the pack stack, else vanilla; False when it is missing or unreadable."""
        if texture not in self.sprites:
            try:
                pixels = self.stack.read(texture) if texture in self.stack.files else self.vanilla.read(texture)
                with Image.open(io.BytesIO(pixels)) as image:
                    self.sprites[texture] = _fully_opaque(image)
            except (KeyError, OSError):
                self.sprites[texture] = False
        return self.sprites[texture]


def _fully_opaque(image):
    return image.convert('RGBA').getchannel('A').getextrema() == (255, 255)


def _cube_metadata(stack, vanilla, blocks, bindings, terrain, samples):
    """Full-cube facts for every bound block whose vanilla blockstate shows only cubes.

    Texture variants per Java state, the author's cube face overrides, opaque
    blocks, gallery stations and unrotated face orientations, with an issue for
    everything that cannot be bound.
    """
    reference = _Models(vanilla)
    authored = _Models(vanilla, stack)
    opacity = _Opacity(stack, vanilla, samples)
    full_cubes, opaque, stations = [], [], []
    orientations, orientation_issues = {}, []
    face_overrides, face_override_issues = {}, []
    texture_variants, variant_issues = {}, []
    alternate_cubes, alternate_opaque = set(), set()
    for identifier, face_bindings in sorted(bindings.items()):
        block = identifier.removeprefix('minecraft:')
        slab_blockstate = _double_slab_blockstate(block)
        if slab_blockstate:
            # Bedrock keeps a double slab as a block of its own; Java draws it as the slab's type=double model.
            if slab_blockstate in reference.names and _double_slab_cube(reference, slab_blockstate):
                full_cubes.append(identifier)
                materials = _block_materials(blocks, block, identifier, terrain)
                if _all_opaque(materials, set(face_bindings.values()), opacity) and len(face_bindings) == 6:
                    opaque.append(identifier)
            continue
        java_name = BLOCK_ALIASES.get(block, block)
        blockstate = f'{JAVA_BLOCKSTATES}{java_name}.json'
        if blockstate not in reference.names or not _vanilla_full_cube(reference, blockstate):
            continue
        full_cubes.append(identifier)
        variants, issues = _block_texture_variants(authored, blockstate, identifier, java_name)
        variant_issues.extend(issues)
        for target, entries in variants.items():
            texture_variants.setdefault(target, []).extend(entries)
            alternate_cubes.add(target)
        overrides, issues = _block_face_overrides(identifier, reference, authored, blockstate, face_bindings)
        if overrides:
            face_overrides[identifier] = overrides
        face_override_issues.extend(issues)
        effective_faces = {**face_bindings, **face_overrides.get(identifier, {})}
        materials = _block_materials(blocks, block, identifier, terrain)
        sprites = set(effective_faces.values()) | _variant_textures(variants)
        if _all_opaque(materials, sprites, opacity) and len(effective_faces) == 6:
            opaque.append(identifier)
            alternate_opaque.update(variants)
        if len(face_bindings) == 6 and any(path in stack.files for path in face_bindings.values()):
            stations += _gallery_stations(identifier, block, java_name)
        try:
            authored_states, unturned = _unturned_faces(authored, blockstate)
        except (KeyError, ValueError):
            orientation_issues.append({'block': identifier, 'reason': 'unresolved_author_model'})
        else:
            if authored_states and unturned:
                orientations[identifier] = {face: 0 for face in sorted(unturned)}
            if not authored_states or len(unturned) != 6:
                orientation_issues.append({'block': identifier, 'reason': 'state_rotation_or_nontrivial_model_uv'})
    full_cubes = sorted(set(full_cubes) | alternate_cubes)
    opaque = sorted(set(opaque) | alternate_opaque)
    unresolved_blocks = sorted({item['block'] for item in orientation_issues if item['block'] not in texture_variants}
                               | {item['block'] for item in variant_issues})
    return {'fullCubeBlocks': full_cubes, 'opaqueBlocks': opaque, 'galleryStations': stations,
            'textureOrientations': orientations, 'unresolved_texture_orientations': orientation_issues,
            'blockFaceOverrides': face_overrides, 'unresolved_block_face_overrides': face_override_issues,
            'baseTextureVariants': texture_variants, 'unresolved_base_texture_variants': variant_issues,
            'unresolvedTextureOrientations': unresolved_blocks,
            'excluded_model_geometry': [{'model': path, 'reason': reason, 'element_indices': list(indices)}
                                        for path, reason, indices in sorted(authored.excluded_geometry)],
            'excluded_model_faces': [{'model': path, 'element_index': index, 'faces': list(faces)}
                                     for path, index, faces in sorted(authored.excluded_faces)]}


def _vanilla_full_cube(reference, blockstate):
    """Whether every model choice of a vanilla variants blockstate holds a complete cube."""
    try:
        state_models = _state_models(reference.read(blockstate))
        return bool(state_models) and all(
            _cube_element(reference.resolve(_resource(item['model'], 'models'))) is not None
            for _, item in state_models)
    except (KeyError, ValueError):
        return False


def _double_slab_blockstate(block):
    """The Java slab blockstate of a Bedrock double slab (oak_double_slab, double_cut_copper_slab), else None."""
    single = single_slab(block)
    if single is None:
        return None
    return f"{JAVA_BLOCKSTATES}{java_id(single).removeprefix('minecraft:')}.json"


def _double_slab_cube(reference, blockstate):
    """Whether every type=double model of a vanilla slab blockstate holds a complete cube."""
    try:
        doubles = [item for state, item in _state_models(reference.read(blockstate)) or [] if 'type=double' in state]
        return bool(doubles) and all(
            _cube_element(reference.resolve(_resource(item['model'], 'models'))) is not None for item in doubles)
    except (KeyError, ValueError):
        return False


def _block_texture_variants(authored, blockstate, identifier, java_name):
    """The author's cube texture variants by Bedrock block, and issues.

    The terrain atlas can retain an old identifier after the runtime block
    registry has renamed it (grass -> grass_block, for example), so the
    variants are supplied under both identities without changing either
    material's pixels.
    """
    java_identifier = 'minecraft:' + java_name
    try:
        variants, issues = _cube_texture_variants(authored, blockstate, java_identifier)
    except (KeyError, ValueError) as error:
        return {}, [{'block': identifier, 'reason': str(error)}]
    if identifier != java_identifier and java_identifier in variants:
        variants.setdefault(identifier, variants[java_identifier])
    return variants, issues


def _block_face_overrides(identifier, reference, authored, blockstate, face_bindings):
    try:
        return _cube_face_overrides(identifier, reference, authored, blockstate, face_bindings)
    except (KeyError, ValueError) as error:
        return {}, [{'block': identifier, 'reason': 'unresolved_author_model', 'detail': str(error)}]


def _block_materials(blocks, block, identifier, terrain):
    """The Bedrock terrain materials a block's blocks.json entry names."""
    definition = blocks.get(block, blocks.get(identifier, {}))
    aliases = definition.get('textures', {})
    aliases = list(aliases.values()) if isinstance(aliases, dict) else [aliases]
    return {path for alias in aliases for path in _texture_paths(terrain.get(alias, {}).get('textures'))}


def _variant_textures(variants):
    return {texture for entries in variants.values() for entry in entries
            for choice in entry.get('modelChoices', [entry]) for texture in choice['faces'].values()}


def _all_opaque(materials, sprites, opacity):
    """Whether the block's Bedrock materials and Java sprites are all fully opaque (every image is checked)."""
    materials_opaque = [opacity.material(material) for material in materials]
    sprites_opaque = [opacity.sprite(texture) for texture in sprites]
    return bool(materials) and all(materials_opaque) and all(sprites_opaque)


def _gallery_stations(identifier, block, java_name):
    """Gallery entries for a block the pack retextures: one per pillar axis for logs and the like."""
    label = java_name.replace('_', ' ').title()
    if block in LOG_AXIS_BLOCKS:
        return [{'block': identifier, 'states': {'pillar_axis': axis}, 'label': label + ' / axis ' + axis.upper()}
                for axis in ('y', 'x', 'z')]
    return [{'block': identifier, 'states': {}, 'label': label}]


def _unturned_faces(authored, blockstate):
    """(the author's state models or None, faces every one of them shows unturned and uncropped).

    Any blockstate rotation, or a model other than one plain cube, leaves no face.
    """
    authored_states = _state_models(authored.read(blockstate))
    unturned = set(FACES)
    for _, item in authored_states or []:
        model = authored.resolve(_resource(item['model'], 'models'))
        if not _unit_cube(model) or item.get('x', 0) % 360 or item.get('y', 0) % 360:
            unturned.clear()
            break
        for face, data in model['elements'][0]['faces'].items():
            if data.get('rotation', 0) % 360 or data.get('uv', CANONICAL_UV) != CANONICAL_UV:
                unturned.discard(face)
    return authored_states, unturned


def _add_model_geometry(cube_metadata, authored_models, stack, names):
    """Adds Java model geometry (java_planar_bindings) to cube_metadata; returns the blocks that have it.

    Candidates are the full-cube blocks, vanilla plants and leaves, every
    blockstate in the pack and the blocks of the pack's CTM rules. A
    candidate's geometry is kept when the author changed it, a CTM rule
    draws on it, or the block is one of the plants that always keep theirs.
    """
    # java_planar_bindings builds on this module, so it is imported where it is used.
    from java_planar_bindings import (authored_geometry_selection, ctm_geometry_candidates, is_foliage,
                                      leaf_distance_tags, model_geometry_variants)
    geometry_issues, geometry_blocks, geometry_models = [], set(), set()
    candidates = set(cube_metadata['fullCubeBlocks'])
    candidates.update('minecraft:' + PurePosixPath(path).stem for path in names
                      if path.startswith(JAVA_BLOCKSTATES) and path.endswith('.json')
                      and is_foliage(PurePosixPath(path).stem))
    candidates.update('minecraft:' + PurePosixPath(path).stem for path in stack.files
                      if path.startswith(JAVA_BLOCKSTATES) and path.endswith('.json'))
    ctm_candidates, ctm_issues = ctm_geometry_candidates(authored_models)
    candidates.update(ctm_candidates)
    geometry_issues.extend(ctm_issues)
    variants_of = cube_metadata['baseTextureVariants']
    processed_java = set()
    for identifier in sorted(candidates):
        bedrock_name = identifier.removeprefix('minecraft:')
        name = BLOCK_ALIASES.get(bedrock_name, bedrock_name)
        if name in processed_java:
            continue
        processed_java.add(name)
        blockstate = f'{JAVA_BLOCKSTATES}{name}.json'
        if blockstate not in authored_models.names and blockstate not in stack.files:
            continue
        if (identifier not in ctm_candidates and name not in ALWAYS_MODEL_GEOMETRY
                and not authored_geometry_selection(authored_models, blockstate)):
            continue
        variants, issues = model_geometry_variants(authored_models, blockstate, 'minecraft:' + name)
        geometry_issues.extend(issues)
        for target, entries in variants.items():
            parts = _model_parts_of(entries)
            if not parts:
                continue
            if target in geometry_blocks:
                variants_of[target].extend(entries)
            else:
                variants_of[target] = entries
            geometry_blocks.add(target)
            geometry_models.update(part['sourceModel'] for part in parts)
            if identifier != target and name == BLOCK_ALIASES.get(bedrock_name):
                variants_of[identifier] = entries
                geometry_blocks.add(identifier)
    cube_metadata['modelGeometryBlocks'] = sorted(geometry_blocks)
    cube_metadata['unresolved_model_geometry'] = geometry_issues
    cube_metadata['excluded_model_geometry'] = [entry for entry in cube_metadata['excluded_model_geometry']
                                                if entry['model'] not in geometry_models]
    cube_metadata['excluded_model_faces'] = [entry for entry in cube_metadata['excluded_model_faces']
                                             if entry['model'] not in geometry_models]
    if any(any(state in entry['states'] for state in TAG_DERIVED_STATES)
           for target in geometry_blocks for entry in variants_of[target]):
        distance_tags, tag_issues = leaf_distance_tags(authored_models)
        cube_metadata.update(distance_tags)
        cube_metadata['unresolved_model_geometry'].extend(tag_issues)
    return geometry_blocks


def _model_parts_of(entries):
    """Every model part of variant entries: their choices' parts, then their multipart groups' parts."""
    parts = []
    for entry in entries:
        for choice in entry.get('modelChoices', [entry]):
            parts.extend(choice.get('modelParts', []))
        for group in entry.get('modelPartsGroups', []):
            for choice in group['modelChoices']:
                parts.extend(choice.get('modelParts', []))
    return parts


def _fill_base_textures(bindings, cube_metadata):
    """Adds the author's cube face overrides and a base texture for every block with texture variants."""
    for block, faces in cube_metadata['blockFaceOverrides'].items():
        bindings.setdefault(block, {}).update(faces)
    for block, entries in cube_metadata['baseTextureVariants'].items():
        # Native alternate IDs (for example lit_furnace) need a fallback
        # identity even though runtime selection uses their exact states.
        first = entries[0].get('modelChoices', [entries[0]])[0]
        if block not in bindings:
            bindings[block] = dict(first['faces'])
        if len(entries) == 1 and not entries[0]['states'] and 'faces' in entries[0]:
            bindings[block] = dict(entries[0]['faces'])


def _dot(a, b):
    return sum(left * right for left, right in zip(a, b))


def _matrix_product(a, b):
    """The product of two 2x2 matrices (a, b, c, d) = [[a, b], [c, d]]."""
    return (a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3])
