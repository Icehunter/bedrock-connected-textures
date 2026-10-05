"""Entity carriers for the surface effects of a native surface block group.

terrain_native writes one native block per group of surface effects. Effects
flagged entity_surface draw on entity carriers instead: each carrier reuses the
group's aligned quadrant bones and materials, turned into entity space, so its
textures are the files the block uses. With priority-exclusive quarters, an
effect gets one masked carrier instead, whose four quarters show the mask
shapes (terrain_masks) cut into a crop of the material.

Entity carriers cannot draw in ray tracing (blocks show their own native
textures), so every carrier render controller is limited to the other modes.
"""
from copy import deepcopy
import json

import numpy as np
from PIL import Image

from common import write_json
from terrain_masks import QUARTERS

NOT_RAY_TRACED = "!q.graphics_mode_is_any('raytraced')"
# Quadrant bone shapes of a native surface group; bones are named <shape><quadrant>.
SHAPES = ('edge', 'edge_side', 'joined', 'corner')
# The engine's bct:face value for a top face.
TOP_FACE = 4
# Where the engine sets the carrier's height, its quads lie flat just below the carrier's feet.
FLAT_ORIGIN = -.001
FLAT_THICKNESS = .001


def carrier_controllers(names):
    """Render controller references of a carrier client entity, each drawn only when not ray tracing."""
    return [{name: NOT_RAY_TRACED} for name in names]


def guard_carrier_client(document):
    """Copy of a client entity whose render controllers draw only when not ray tracing.

    References that already carry the guard are kept. An older format_version is raised to 1.21.80,
    the format of the carriers this converter writes.
    """
    result = deepcopy(document)
    description = result['minecraft:client_entity']['description']
    description['render_controllers'] = [_guarded(reference) for reference in description['render_controllers']]
    if tuple(map(int, result.get('format_version', '1.10.0').split('.'))) < (1, 21, 80):
        result['format_version'] = '1.21.80'
    return result


def emit_entity_surfaces(bp, rp, effects, bones, materials, atlas, server_entity, masks=None):
    """Write a carrier for every entity_surface effect of a native group; returns their entity ids.

    bones and materials are the group's aligned quadrant bones and material instances (terrain_native),
    atlas the terrain atlas they refer to, and server_entity the carrier server definition builder.
    masks maps an entity id to its quarter mask shapes when the group's priorities are exclusive.
    No new texture images are made, except the quarter crops of masked carriers.
    """
    emitted = []
    source_bones = {bone['name']: bone for bone in bones}
    for position, effect in enumerate(effects):
        if not effect.get('entity_surface'):
            continue
        if effect.get('biome_tint') not in (None, 'none', 'grass'):
            raise ValueError('Unsupported entity surface tint')
        identifier = effect['entity']
        # terrain_native prefixes the bones of every effect but the group's first with its material.
        prefix = '' if position == 0 else effect['material'] + '_'
        if masks is not None and effect.get('mask_lookup'):
            _emit_masked_entity(bp, rp, effect, bones, materials, atlas, server_entity, masks[identifier], prefix)
        else:
            _emit_quadrant_entity(bp, rp, effect, source_bones, materials, atlas, server_entity, prefix)
        emitted.append(identifier)
    return emitted


def _guarded(reference):
    if isinstance(reference, str):
        return {reference: NOT_RAY_TRACED}
    if not isinstance(reference, dict) or len(reference) != 1:
        raise ValueError('Unexpected carrier controller reference')
    name, condition = next(iter(reference.items()))
    if condition == NOT_RAY_TRACED or str(condition).endswith(' && ' + NOT_RAY_TRACED):
        return {name: condition}
    return {name: '(' + _molang_condition(condition) + ') && ' + NOT_RAY_TRACED}


def _molang_condition(condition):
    if condition is True:
        return '1.0'
    if condition is False:
        return '0.0'
    return str(condition)


# Quadrant carriers: the group's sixteen quadrant bones, shown by the engine's tile bits.

def _emit_quadrant_entity(bp, rp, effect, source_bones, materials, atlas, server_entity, prefix):
    """One carrier with a bone per quadrant shape, each with its own render controller.

    The engine picks the material variation (bct:variation) and passes the biome tint (bct:tint_*).
    """
    identifier = effect['entity']
    stem = identifier.replace(':', '_')
    geometry_id = 'geometry.' + stem
    geometry_bones = []
    textures = {}
    controllers = {}
    tint = {channel: f"q.property('bct:tint_{channel}')" for channel in ('r', 'g', 'b')}
    for shape in SHAPES:
        for quadrant in range(4):
            name = shape + str(quadrant)
            bone, variations = _quadrant_bone(source_bones[prefix + name], name, effect, materials, atlas, textures)
            geometry_bones.append(bone)
            controllers['controller.render.' + stem + '_' + name] = {
                'geometry': 'Geometry.default',
                'materials': [{'*': 'Material.default'}],
                'textures': ["Array.variants[math.clamp(q.property('bct:variation'),0,2)]"],
                # Always three slots; a material with a single variation repeats it.
                'arrays': {'textures': {'Array.variants': [variations[index % len(variations)]
                                                           for index in range(3)]}},
                'color': {**tint, 'a': 1},
                'part_visibility': [{'*': False}, {name: _visible_shape(shape, quadrant)}],
            }
    server = server_entity(identifier)
    server['minecraft:entity']['description']['properties']['bct:variation'] = {
        'type': 'int', 'range': [0, 2], 'default': 0, 'client_sync': True,
    }
    client = _client_description(identifier, 'entity_alphatest_change_color', textures, geometry_id, controllers)
    _write_carrier(bp, rp, stem, server, _carrier_model(geometry_id, 16, geometry_bones), client, controllers)


def _quadrant_bone(source_bone, name, effect, materials, atlas, textures):
    """Copy a native quadrant bone into entity space and add its material's variations to textures.

    Returns the bone and the texture references of its variations, in variation order.
    """
    bone = deepcopy(source_bone)
    bone['name'] = name
    bone['pivot'] = [0, 0, 0]
    # Carriers spawn on the host's top; the half turn compensates for the entity's yaw.
    bone['rotation'] = [0, 180, 0]
    for cube in bone['cubes']:
        if effect.get('height_tuning'):
            cube['origin'][1] = FLAT_ORIGIN
            cube['size'][1] = FLAT_THICKNESS
        else:
            # Native geometry belongs to a block native_offset cells above the host, the carrier to the
            # cell just above it.
            cube['origin'][1] += (effect.get('native_offset', 1) - 1) * 16
        if set(cube['uv']) != {'up'}:
            raise ValueError('Entity surface geometry must contain top faces only')
        face = cube['uv']['up']
        slot = face.pop('material_instance')
        variations = atlas['texture_data'][materials[slot]['texture']]['textures']['variations']
        if len(variations) not in (1, 3):
            raise ValueError('Entity surface material requires one or three source variations')
        references = []
        for index, variation in enumerate(variations):
            alias = name if index == 0 else name + '_v' + str(index)
            textures[alias] = variation['path']
            references.append('Texture.' + alias)
    return bone, references


def _visible_shape(shape, quadrant):
    """Molang: show the quadrant bone of this shape on a top face when the engine's tile bits ask for it.

    Bit n is a contact on side n and bit n + 4 the diagonal between sides n and n + 1. The quadrant
    between sides q and q + 1 shows edge when only side q touches, edge_side when only side q + 1
    does, joined when both do and corner when neither does but the diagonal does.
    """
    first = _tile_bit(quadrant)
    second = _tile_bit((quadrant + 1) % 4)
    diagonal = _tile_bit(quadrant + 4)
    selected = {
        'edge': f'({first}) && !({second})',
        'edge_side': f'!({first}) && ({second})',
        'joined': f'({first}) && ({second})',
        'corner': f'!({first}) && !({second}) && ({diagonal})',
    }[shape]
    return f"q.property('bct:active') && q.property('bct:face') == {TOP_FACE} && (" + selected + ')'


def _tile_bit(index):
    return f"math.mod(math.floor(q.property('bct:tile')/{2 ** index}),2) == 1"


# Masked carriers: one quad per quarter, showing the mask shape the engine picks.

def _emit_masked_entity(bp, rp, effect, bones, materials, atlas, server_entity, variants, prefix):
    """One carrier with a flat quad per quarter; bct:quarter<n> picks the mask shape quarter n shows.

    Every mask shape is baked into the alpha of the quarter's crop of the material, one image per
    quarter and shape, so where surfaces of different priority meet each pixel shows only one.
    """
    stem = effect['entity'].replace(':', '_')
    textures = {}
    controllers = {}
    mesh = []
    directory = rp / 'textures/entity'
    directory.mkdir(parents=True, exist_ok=True)
    for quadrant, region in enumerate(QUARTERS):
        source_bone = next(bone for bone in bones if bone['name'] == prefix + 'edge' + str(quadrant))
        bone, slot = _quarter_bone(source_bone, quadrant)
        mesh.append(bone)
        source = rp / atlas['texture_data'][materials[slot]['texture']]['textures']['variations'][0]['path']
        with Image.open(source.with_suffix('.png')) as image:
            quarter_pixels = np.asarray(image.convert('RGBA'))[region].copy()
        quarter_name = stem + '_q' + str(quadrant)
        channels = _crop_material_channels(source, region, directory, quarter_name)
        references = []
        for index, mask in enumerate(variants):
            name = quarter_name + '_mask' + str(index)
            pixels = quarter_pixels.copy()
            # Mask shapes are cut for quarter 0; each later quarter turns them a further quarter clockwise.
            pixels[:, :, 3] = np.rot90(mask, -quadrant).astype(np.uint8) * 255
            Image.fromarray(pixels).save(directory / (name + '.png'))
            write_json(directory / (name + '.texture_set.json'),
                       {'format_version': '1.21.30', 'minecraft:texture_set': {'color': name, **channels}})
            alias = 'q' + str(quadrant) + 'm' + str(index)
            textures[alias] = 'textures/entity/' + name
            references.append('Texture.' + alias)
        quarter = "q.property('bct:quarter" + str(quadrant) + "')"
        controllers['controller.render.' + quarter_name] = {
            'geometry': 'Geometry.default',
            'materials': [{'*': 'Material.default'}],
            'arrays': {'textures': {'Array.masks': references}},
            'textures': ['Array.masks[' + quarter + ']'],
            # Shape 0 is the empty mask, so the quarter hides.
            'part_visibility': [{'*': False}, {bone['name']: "q.property('bct:active') && " + quarter + ' > 0'}],
        }
    server = server_entity(effect['entity'])
    server['minecraft:entity']['description']['properties'].update({
        'bct:quarter' + str(quadrant): {'type': 'int', 'range': [0, len(variants) - 1], 'default': 0,
                                        'client_sync': True}
        for quadrant in range(4)})
    geometry_id = 'geometry.' + stem
    client = _client_description(effect['entity'], 'entity_alphatest', textures, geometry_id, controllers)
    _write_carrier(bp, rp, stem, server, _carrier_model(geometry_id, 8, mesh), client, controllers)


def _quarter_bone(source_bone, quadrant):
    """The quadrant's edge bone as a flat quad showing one whole quarter texture.

    Returns the bone and its material slot.
    """
    bone = deepcopy(source_bone)
    bone['name'] = 'quarter' + str(quadrant)
    bone['rotation'] = [0, 180, 0]
    cube = bone['cubes'][0]
    # The engine places masked carriers one texture pixel above the terrain.
    cube['origin'][1] = FLAT_ORIGIN
    cube['size'][1] = FLAT_THICKNESS
    face = cube['uv']['up']
    slot = face.pop('material_instance')
    # The texture is the quarter crop alone, so the face maps all of it (backwards, as top faces do).
    face['uv'] = [8, 8]
    face['uv_size'] = [-8, -8]
    return bone, slot


def _crop_material_channels(source, region, directory, quarter_name):
    """Crop the material's other channels (normal, MER) to the quarter; returns the texture set channels."""
    texture_set = json.loads(source.with_suffix('.texture_set.json').read_text())['minecraft:texture_set']
    rows, columns = region
    channels = {}
    for channel, reference in texture_set.items():
        if channel == 'color':
            continue
        if isinstance(reference, str):
            name = quarter_name + '_' + channel
            with Image.open(source.parent / (reference + '.png')) as image:
                image.crop((columns.start, rows.start, columns.stop, rows.stop)).save(directory / (name + '.png'))
            channels[channel] = name
        else:
            channels[channel] = reference
    return channels


# Carrier files.

def _carrier_model(geometry_id, texture_size, bones):
    return {
        'description': {
            'identifier': geometry_id,
            'texture_width': texture_size,
            'texture_height': texture_size,
            'visible_bounds_width': 2,
            'visible_bounds_height': 2,
            'visible_bounds_offset': [0, 0, 0],
        },
        'bones': bones,
    }


def _client_description(identifier, material, textures, geometry_id, controllers):
    return {
        'identifier': identifier,
        'materials': {'default': material},
        'textures': textures,
        'geometry': {'default': geometry_id},
        'render_controllers': carrier_controllers(controllers),
    }


def _write_carrier(bp, rp, stem, server, model, client, controllers):
    write_json(bp / f'entities/{stem}.json', server)
    write_json(rp / f'models/entity/{stem}.geo.json', {'format_version': '1.21.0', 'minecraft:geometry': [model]})
    write_json(rp / f'entity/{stem}.entity.json',
               {'format_version': '1.21.80', 'minecraft:client_entity': {'description': client}})
    write_json(rp / f'render_controllers/{stem}.render_controllers.json',
               {'format_version': '1.8.0', 'render_controllers': controllers})
