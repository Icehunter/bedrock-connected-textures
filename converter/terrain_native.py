"""Native custom blocks that draw terrain transitions (grass and sand edges) on a host's top face.

The engine places one of these blocks in the air cell above a host block. Its geometry is a set
of thin planes just above the host's top face, one per edge or corner a transition touches, each
cut out by the edge shape taken from the author's art (edge_shapes.py). The source material's
colour and Vibrant Visuals maps are copied unchanged; only alpha is cut.

Key decisions:
- Cutouts are binary (alpha_test): coverage is all or nothing, so the source RGB and PBR maps
  survive untouched.
- A block with one material uses two 16-value states, bct:edges and bct:corners, one bit per
  side.
- A cell holds one block, so up to three materials share it: each of four bct:quadrant<i> states
  holds the edge material plus four times the corner material, within a state's 16 values.
- Shared blocks keep their quarter planes unrotated in their compass quadrant and turn the cutout
  in the alpha instead, so the colour and VV maps stay aligned with the world.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import shutil

import numpy as np
from PIL import Image

from common import BLOCKS_FORMAT, read_json, samples_path, terrain_pack, write_json
from edge_shapes import edge_alpha
from terrain_layers import native_surface_height, surface_height

# The vanilla block a terrain material inherits ambient occlusion from, where the names differ.
_MATERIAL_BLOCKS = {'grass_top': 'grass', 'suspicious_sand_0': 'suspicious_sand'}
# The vanilla grass top and its two variants.
_GRASS_TOP_TEXTURES = ['grass_top', 'grass_top_av_var1', 'grass_top_av_var2']
# Plane shapes of a shared block; a single-material block draws only the first two.
_SHARED_SHAPES = ('edge', 'corner', 'edge_side', 'joined')
# Quarter planes run clockwise from north-east: where each one starts in model space (x, z), and
# the corner its top-face UVs run back from, so it samples its own quarter of the texture.
_QUADRANT_ORIGINS = [(-8, -8), (-8, 0), (0, 0), (0, -8)]
_QUADRANT_UVS = [(16, 8), (16, 16), (8, 16), (8, 8)]
_ATLAS = 'textures/terrain_texture.json'
_EMPTY_LOOT_TABLE = 'loot_tables/blocks/bct_empty.json'


def emit_native_surfaces(root, bp, rp, effects, targets, entity_emitter=None, samples=None):
    """Write the native block of one group of surface effects; returns the texture images written.

    All effects name the same native_block. A lone effect without a native_material id gets a
    single-material block. Otherwise each effect is material 1, 2 or 3 of a shared block: every
    material is cut as quarter planes that join the first effect's block. Planes of effects
    drawn by entities (entity_surface) go to entity_emitter(effects, bones, materials) and stay
    out of the block. targets are the host blocks the block may be placed on.
    """
    if len(effects) == 1 and not effects[0].get('native_material'):
        return _emit_material_block(root, bp, rp, effects[0], targets, samples)
    _check_shared_group(effects, entity_emitter)
    stem = effects[0]['native_block'].replace(':', '_')
    planes = _cut_shared_planes(root, bp, rp, effects, targets, samples)
    block = read_json(bp / f'blocks/{stem}.json')['minecraft:block']
    planes.images += _align_quadrant_materials(rp, planes.bones, planes.materials)
    if entity_emitter:
        entity_emitter(effects, planes.bones, planes.materials)
    native_effects = [effect for effect in effects if not effect.get('entity_surface')]
    bones = _finish_shared_block(block, effects, native_effects, planes)
    geometry = _geometry_model('geometry.' + stem, bones)
    geometries = [geometry, *_height_profile_geometries(block, native_effects, geometry)]
    write_json(bp / f'blocks/{stem}.json', {'format_version': '1.26.50', 'minecraft:block': block})
    geometry_document = {'format_version': '1.21.0', 'minecraft:geometry': geometries}
    write_json(rp / f'models/blocks/{stem}.geo.json', geometry_document)
    _remove_merged_block_sounds(rp, effects)
    return planes.images


def source_ambient_occlusion(root, effect):
    """Ambient occlusion exponent of a surface: the effect's own, else its material's.

    The terrain pack's blocks.json override wins over the inherited vanilla value, which
    defaults to 1.0.
    """
    if 'ambient_occlusion' in effect:
        return float(effect['ambient_occlusion'])
    block_name = _MATERIAL_BLOCKS.get(effect['material'], effect['material'])
    vanilla = read_json(samples_path(root) / 'resource_pack/blocks.json')
    overrides = read_json(terrain_pack(root) / 'blocks.json')
    vanilla_exponent = vanilla.get(block_name, {}).get('ambient_occlusion_exponent', 1.0)
    return float(overrides.get(block_name, {}).get('ambient_occlusion_exponent', vanilla_exponent))


@dataclass
class _SharedPlanes:
    """The quarter planes of every material in a shared block, gathered before it is written."""
    bones: list = field(default_factory=list)
    # Molang showing each bone, by bone name.
    visibility: dict = field(default_factory=dict)
    # Material instances by slot name.
    materials: dict = field(default_factory=dict)
    # Bones the block draws itself; entities draw the rest.
    native_bone_names: set = field(default_factory=set)
    images: int = 0


def _check_shared_group(effects, entity_emitter):
    if any(effect.get('entity_surface') for effect in effects) and entity_emitter is None:
        raise ValueError('Entity surface groups require an entity emitter')
    material_ids = [effect.get('native_material') for effect in effects]
    if len(set(material_ids)) != len(material_ids) or any(value not in (1, 2, 3) for value in material_ids):
        raise ValueError('Shared surface materials require distinct IDs 1 through 3')


def _cut_shared_planes(root, bp, rp, effects, targets, samples):
    """Cut every material of a shared group as quarter planes.

    Each material is first written as a block of its own. The first one's block becomes the
    shared block; the others are read back and deleted, their bone and slot names prefixed with
    their material.
    """
    planes = _SharedPlanes()
    for effect in effects:
        primary = effect is effects[0]
        prefix = '' if primary else effect['material'] + '_'
        identifier = _material_block_id(effects, effect)
        local_effect = dict(effect, native_block=identifier)
        planes.images += _emit_material_block(root, bp, rp, local_effect, targets, samples, quadrants=True)
        instances, bones = _read_material_block(bp, rp, identifier, remove=not primary)
        for shape in _SHARED_SHAPES:
            planes.materials[prefix + shape] = instances[shape]
        for bone in bones:
            shape = bone['name'][:-1]
            quadrant = int(bone['name'][-1])
            bone['name'] = prefix + bone['name']
            bone['cubes'][0]['uv']['up']['material_instance'] = prefix + shape
            planes.visibility[bone['name']] = _quadrant_visibility(shape, quadrant, effect['native_material'])
            planes.bones.append(bone)
            if not effect.get('entity_surface'):
                planes.native_bone_names.add(bone['name'])
    return planes


def _material_block_id(effects, effect):
    """The block a shared group's material is first written as; the first one's is the shared block."""
    base = effects[0]['native_block']
    if effect is effects[0]:
        return base
    return base + '_' + effect['material']


def _read_material_block(bp, rp, identifier, remove):
    """Material instances and bones of a block _emit_material_block wrote; remove deletes its files."""
    stem = identifier.replace(':', '_')
    block_path = bp / f'blocks/{stem}.json'
    geometry_path = rp / f'models/blocks/{stem}.geo.json'
    instances = read_json(block_path)['minecraft:block']['components']['minecraft:material_instances']
    bones = read_json(geometry_path)['minecraft:geometry'][0]['bones']
    if remove:
        block_path.unlink()
        geometry_path.unlink()
    return instances, bones


def _quadrant_visibility(shape, quadrant, material_id):
    """Molang showing a quarter plane of this shape when its quadrant's contacts call for it.

    Quadrant i lies between edge i and edge i + 1, with corner i between them; its state holds
    edge i's material plus four times corner i's.
    """
    state = f"q.block_state('bct:quadrant{quadrant}')"
    next_state = f"q.block_state('bct:quadrant{(quadrant + 1) % 4}')"
    own_edge = f'math.mod({state},4) == {material_id}'
    next_edge = f'math.mod({next_state},4) == {material_id}'
    corner = f'math.floor({state}/4) == {material_id}'
    return {
        'edge': f'({own_edge}) && !({next_edge})',
        'edge_side': f'!({own_edge}) && ({next_edge})',
        'joined': f'({own_edge}) && ({next_edge})',
        'corner': f'!({own_edge}) && !({next_edge}) && ({corner})',
    }[shape]


def _align_quadrant_materials(rp, bones, materials):
    """Move every quarter plane, unrotated, into its compass quadrant; returns the images added.

    Turning a plane into place would turn its colour and VV maps with it. Instead quadrants 1 to 3
    use copies of their material with the cutout alpha turned, so the maps stay world aligned.
    """
    atlas_path = rp / _ATLAS
    atlas = read_json(atlas_path)
    texture_dir = rp / 'textures/blocks'
    added = 0
    for bone in bones:
        quadrant = int(bone['name'][-1])
        cube = bone['cubes'][0]
        face = cube['uv']['up']
        if quadrant:
            slot = face['material_instance']
            turned_slot = slot + '_q' + str(quadrant)
            if turned_slot not in materials:
                turned, images = _turned_material(atlas, texture_dir, materials[slot], quadrant)
                materials[turned_slot] = turned
                added += images
            face['material_instance'] = turned_slot
        cube['origin'][0], cube['origin'][2] = _QUADRANT_ORIGINS[quadrant]
        face['uv'] = list(_QUADRANT_UVS[quadrant])
        bone['rotation'] = [0, 0, 0]
    write_json(atlas_path, atlas)
    return added


def _turned_material(atlas, texture_dir, material, quadrant):
    """Copy a material with its cutout alpha turned clockwise by `quadrant` quarter turns.

    The atlas gains the copy's alias; returns the copied material and the images written.
    """
    alias = material['texture']
    suffix = '_q' + str(quadrant)
    variations = []
    for variation in atlas['texture_data'][alias]['textures']['variations']:
        source_name = Path(variation['path']).name
        name = source_name + suffix
        descriptor = read_json(texture_dir / (source_name + '.texture_set.json'))
        with Image.open(texture_dir / (source_name + '.png')) as original:
            tile = original.convert('RGBA')
        tile.putalpha(tile.getchannel('A').rotate(-90 * quadrant))
        tile.save(texture_dir / (name + '.png'))
        descriptor['minecraft:texture_set']['color'] = name
        write_json(texture_dir / (name + '.texture_set.json'), descriptor)
        variations.append(dict(variation, path='textures/blocks/' + name))
    atlas['texture_data'][alias + suffix] = {'textures': {'variations': variations}}
    return dict(material, texture=alias + suffix), len(variations)


def _finish_shared_block(block, effects, native_effects, planes):
    """Turn the first material's block into the shared block; returns the bones it draws."""
    if native_effects:
        bones, visibility, materials = _block_drawn_planes(planes)
        material_ids = [effect['native_material'] for effect in native_effects]
    else:
        # A group drawn only by entities keeps every material in its states, so the engine can
        # still recover the cells it owns.
        bones, visibility, materials = planes.bones, planes.visibility, planes.materials
        material_ids = [effect['native_material'] for effect in effects]
    first_material = next(iter(materials.values()))
    instances = {'*': first_material, **materials}
    components = block['components']
    if effects[0].get('native_offset', 1) == 2:
        # An untextured anchor keeps the oversized geometry inside its own cell.
        bones = [*bones, _anchor_bone()]
        visibility = {**visibility, 'anchor': '0'}
        components.pop('minecraft:placement_filter', None)
    if any(effect.get('native_disabled') for effect in effects) or not native_effects:
        # A disabled type stays loadable so the engine can clear cells it placed.
        bones = [_anchor_bone()]
        visibility = {'anchor': '0'}
        instances = {'*': first_material}
    state_values = _quadrant_state_values(material_ids)
    block['description']['states'] = {f'bct:quadrant{index}': state_values for index in range(4)}
    components['minecraft:geometry']['bone_visibility'] = visibility
    components['minecraft:material_instances'] = instances
    # Quadrants share boundaries, never area, so one material uses one plane.
    components['minecraft:transformation'] = {'translation': [0, 0, 0]}
    return bones


def _block_drawn_planes(planes):
    """Bones, visibility and used materials of the planes the block draws rather than an entity."""
    names = planes.native_bone_names
    bones = [bone for bone in planes.bones if bone['name'] in names]
    visibility = {name: molang for name, molang in planes.visibility.items() if name in names}
    used_slots = {face['material_instance'] for bone in bones for cube in bone['cubes']
                  for face in cube['uv'].values()}
    materials = {slot: material for slot, material in planes.materials.items() if slot in used_slots}
    return bones, visibility, materials


def _quadrant_state_values(material_ids):
    """Values a quadrant state takes: its edge material plus four times its corner material.

    The engine never pairs an edge with a corner of the same material, since that corner lies
    inside the edge's cutout.
    """
    choices = [0, *material_ids]
    return sorted(edge + 4 * corner for edge in choices for corner in choices if not edge or edge != corner)


def _anchor_bone():
    """A tiny untextured cube at the bottom centre of the cell."""
    cube = {'origin': [-.5, 0, -.5], 'size': [1, 1, 1], 'uv': {}}
    return {'name': 'anchor', 'pivot': [0, 0, 0], 'cubes': [cube]}


def _height_profile_geometries(block, effects, geometry):
    """Add a manually selected two-pixel profile to opted-in native surfaces.

    Returns the extra geometry: a copy with every plane's top twice as high, shown when the
    block's bct:height_profile state is 1.
    """
    if not any('height_profiles' in effect for effect in effects):
        return []
    if any(effect.get('height_profiles') != [1, 2] for effect in effects):
        raise ValueError('Native surface groups require matching height_profiles [1, 2]')
    if any(effect.get('native_offset', 1) != 1 or effect.get('native_disabled') for effect in effects):
        raise ValueError('Height profiles require active native surfaces at offset 1')
    variant = deepcopy(geometry)
    identifier = geometry['description']['identifier'] + '.height_profile_1'
    variant['description']['identifier'] = identifier
    for bone in variant['bones']:
        for cube in bone.get('cubes', []):
            top = cube['origin'][1] + cube['size'][1]
            cube['origin'][1] += top
    block['description']['states']['bct:height_profile'] = [0, 1]
    block['permutations'] = [{
        'condition': "q.block_state('bct:height_profile') == 1",
        'components': {'minecraft:geometry': {
            'identifier': identifier,
            'bone_visibility': dict(block['components']['minecraft:geometry']['bone_visibility']),
        }},
    }]
    return [variant]


def _remove_merged_block_sounds(rp, effects):
    """Drop the blocks.json entries of the material blocks merged into the shared block."""
    blocks_path = rp / 'blocks.json'
    blocks = read_json(blocks_path)
    for effect in effects[1:]:
        blocks.pop(_material_block_id(effects, effect), None)
    write_json(blocks_path, blocks)


def _emit_material_block(root, bp, rp, effect, targets, samples=None, quadrants=False):
    """Write one material's block with its geometry, cut textures and atlas aliases.

    Returns the number of texture images written. With quadrants, the planes are quarter planes
    and the extra shapes of a shared block are cut too.
    """
    if effect.get('debug_opaque') or effect.get('debug_shapes'):
        raise ValueError('Native overlays require their authored alpha cutouts')
    stem = effect['native_block'].replace(':', '_')
    samples = samples or samples_path(root)
    vanilla_blocks = read_json(samples / 'metadata/vanilladata_modules/mojang-blocks.json')['data_items']
    # A group whose transitions are all drawn by the pack's own overlay rules keeps no targets:
    # its block is still written (the engine never places it), without a placement filter.
    targets = targets & {item['name'] for item in vanilla_blocks}
    sources = _source_texture_sets(root, effect)
    shapes = _cutout_shapes(root, effect, quadrants)
    aliases, image_count = _write_cut_textures(root, rp, stem, effect, sources, shapes)
    _add_atlas_aliases(rp, stem, aliases)
    bones, visibility = _material_planes(effect, shapes, quadrants)
    geometry_id = 'geometry.' + stem
    geometry = {'format_version': '1.21.0', 'minecraft:geometry': [_geometry_model(geometry_id, bones)]}
    write_json(rp / f'models/blocks/{stem}.geo.json', geometry)
    block = _material_block(root, effect, stem, geometry_id, shapes, visibility, targets)
    write_json(bp / f'blocks/{stem}.json', block)
    write_json(bp / _EMPTY_LOOT_TABLE, {'pools': []})
    _add_block_sound(rp, effect['native_block'])
    return image_count


def _source_texture_sets(root, effect):
    """Texture sets a surface copies: its own, else the terrain pack's material (grass with its variants)."""
    sources = effect.get('texture_sets')
    if sources is not None:
        return sources
    names = _GRASS_TOP_TEXTURES if effect['material'] == 'grass_top' else [effect['material']]
    return [terrain_pack(root) / 'textures/blocks' / (name + '.texture_set.json') for name in names]


def _cutout_shapes(root, effect, quadrants):
    """Alpha of each plane shape, starting from the edge cutout along the top side.

    corner is where the edge overlaps itself turned onto the east side; shared blocks add that
    turned edge (edge_side) and both edges together (joined).
    """
    # Cutout coverage is binary; preserve all source RGB and VV channels.
    edge = edge_alpha(root, effect).point(lambda value: 255 if value >= 128 else 0)
    side = edge.rotate(-90)
    corner = Image.fromarray(np.minimum(np.asarray(edge), np.asarray(side)))
    shapes = {'edge': edge, 'corner': corner}
    if quadrants:
        shapes['edge_side'] = side
        shapes['joined'] = Image.fromarray(np.maximum(np.asarray(edge), np.asarray(side)))
    return shapes


def _write_cut_textures(root, rp, stem, effect, sources, shapes):
    """Cut every source texture set by every shape; returns the atlas aliases and images written.

    Each shape gets one weighted variation per source, keeping the author's random variants.
    The colour map takes the cutout as alpha; the other maps are copied once per source.
    """
    texture_dir = rp / 'textures/blocks'
    texture_dir.mkdir(parents=True, exist_ok=True)
    weights = effect.get('texture_weights', [1] * len(sources))
    aliases = {shape: {'textures': {'variations': []}} for shape in shapes}
    image_count = 0
    for index, source in enumerate(sources):
        source = root / source
        descriptor = read_json(source)['minecraft:texture_set']
        with Image.open(source.parent / (descriptor['color'] + '.png')) as image:
            color = image.convert('RGBA')
        other_maps = sum(isinstance(reference, str)
                         for channel, reference in descriptor.items() if channel != 'color')
        image_count += len(shapes) + other_maps
        channels = _copy_other_maps(source.parent, descriptor, texture_dir, f'{stem}_{index}')
        for shape, alpha in shapes.items():
            name = f'{stem}_{shape}_{index}'
            tile = color.copy()
            tile.putalpha(alpha)
            tile.save(texture_dir / (name + '.png'))
            texture_set = {'format_version': '1.21.30', 'minecraft:texture_set': {'color': name, **channels}}
            write_json(texture_dir / (name + '.texture_set.json'), texture_set)
            variation = {'path': 'textures/blocks/' + name, 'weight': weights[index]}
            aliases[shape]['textures']['variations'].append(variation)
    return aliases, image_count


def _copy_other_maps(folder, descriptor, texture_dir, prefix):
    """Copy a texture set's maps other than colour under the block's names; uniform values stay."""
    channels = {}
    for channel, reference in descriptor.items():
        if channel == 'color':
            continue
        if isinstance(reference, str):
            name = f'{prefix}_{channel}'
            shutil.copyfile(folder / (reference + '.png'), texture_dir / (name + '.png'))
            channels[channel] = name
        else:
            channels[channel] = reference
    return channels


def _add_atlas_aliases(rp, stem, aliases):
    """Register each shape's variations in the terrain atlas as <stem>_<shape>."""
    atlas_path = rp / _ATLAS
    if atlas_path.exists():
        atlas = read_json(atlas_path)
    else:
        atlas = {'resource_pack_name': 'bct_terrain', 'texture_name': 'atlas.terrain', 'texture_data': {}}
    for shape, definition in aliases.items():
        atlas['texture_data'][stem + '_' + shape] = definition
    write_json(atlas_path, atlas)


def _material_planes(effect, shapes, quadrants):
    """One plane per shape and side, each shown by its side's bit of bct:edges or bct:corners."""
    top = _plane_top(effect, quadrants)
    size = 8 if quadrants else 16
    bones = []
    visibility = {}
    for shape in shapes:
        state = 'bct:edges' if shape == 'edge' else 'bct:corners'
        for side in range(4):
            name = shape + str(side)
            visibility[name] = f"math.mod(math.floor(q.block_state('{state}')/{2 ** side}),2) == 1"
            bones.append(_plane_bone(name, shape, side, top, size))
    return bones, visibility


def _plane_top(effect, quadrants):
    """Pixel height of a plane's top face above the host's top face."""
    layer = effect.get('layer', 2)
    if quadrants:
        default_pixels = native_surface_height(effect['material'], layer, effect.get('priority', 1)) * 256
        top = effect.get('height_pixels', default_pixels) / 256 * 16
    else:
        top = surface_height(layer) * 16
    # A block placed two cells above its host draws its planes one block lower.
    top -= (effect.get('native_offset', 1) - 1) * 16
    return top


def _plane_bone(name, shape, side, top, size):
    """A flat plane just under `top`, turned a quarter turn per side."""
    # Negative top-face UV sizes put texture row zero at north. Native blocks have no entity yaw
    # to compensate with a half turn, so the authored top-right cutout selects north-east at
    # zero yaw.
    return {
        'name': name,
        'pivot': [0, 0, 0],
        'rotation': [0, 90 * side, 0],
        'cubes': [{
            'origin': [-8, top - .001, -8],
            'size': [size, .001, size],
            'uv': {'up': {'uv': [16, size], 'uv_size': [-size, -size], 'material_instance': shape}},
        }],
    }


def _geometry_model(identifier, bones):
    description = {'identifier': identifier, 'texture_width': 16, 'texture_height': 16}
    return {'description': description, 'bones': bones}


def _material_block(root, effect, stem, geometry_id, shapes, visibility, targets):
    """Block definition of one material: no collision, selection, light blocking or drops."""
    tint = effect.get('biome_tint', 'none')
    ambient_occlusion = source_ambient_occlusion(root, effect)
    instances = {'*': _material_instance(stem, 'edge', tint, ambient_occlusion)}
    for shape in shapes:
        instances[shape] = _material_instance(stem, shape, tint, ambient_occlusion)
    components = {
        'minecraft:geometry': {'identifier': geometry_id, 'bone_visibility': visibility},
        'minecraft:material_instances': instances,
        'minecraft:collision_box': False,
        'minecraft:selection_box': False,
        'minecraft:replaceable': {},
        'minecraft:light_dampening': 0,
        'minecraft:destruction_particles': {'texture': stem + '_edge', 'tint_method': tint, 'particle_count': 0},
        'minecraft:loot': _EMPTY_LOOT_TABLE,
    }
    if targets:
        components['minecraft:placement_filter'] = {
            'conditions': [{'allowed_faces': ['up'], 'block_filter': sorted(targets)}],
        }
    states = {'bct:edges': list(range(16)), 'bct:corners': list(range(16))}
    # Engine-placed surfaces are not items: keep them out of the creative menu and commands.
    description = {'identifier': effect['native_block'], 'states': states,
                   'menu_category': {'category': 'none', 'is_hidden_in_commands': True}}
    block = {'description': description, 'components': components}
    return {'format_version': '1.26.50', 'minecraft:block': block}


def _material_instance(stem, shape, tint, ambient_occlusion):
    return {
        'texture': stem + '_' + shape,
        'render_method': 'alpha_test_single_sided',
        'tint_method': tint,
        'ambient_occlusion': ambient_occlusion,
        'face_dimming': True,
        'isotropic': False,
    }


def _add_block_sound(rp, identifier):
    """Give the block grass sounds in blocks.json."""
    blocks_path = rp / 'blocks.json'
    blocks = read_json(blocks_path) if blocks_path.exists() else {'format_version': BLOCKS_FORMAT}
    blocks[identifier] = {'sound': 'grass'}
    write_json(blocks_path, blocks)
