"""Build native-block connected textures without replacing world blocks.

Each rule becomes a custom block whose geometry holds one face-sized bone per face and tile.
Molang bone visibility asks which neighbours carry the rule's block tag, so the game itself
shows the connected tile, with no script or entity. The block renderer binds the authored
texture sets through the terrain atlas, so Vibrant Visuals and RTX get the pack's PBR maps.
The blocks have to be placed, or swapped in by a separate world conversion, and the neighbour
queries in geometry visibility still need in-game RTX validation.
"""
import argparse
import json
import math
from pathlib import Path
import re
import tempfile
import uuid
import zipfile

from PIL import Image

from common import read_json, write_json
from terrain_pack import contained, image_path
from terrain_providers import ROOT, MASKS
from java_texture_animation import parse_animation, compile_family
from java_texture_paths import SPECIAL_TILES

FACES = ['north', 'east', 'south', 'west', 'up', 'down']
# Offset of the neighbour each face looks at.
VECTORS = {'north': (0, 0, -1), 'east': (1, 0, 0), 'south': (0, 0, 1), 'west': (-1, 0, 0), 'up': (0, 1, 0),
           'down': (0, -1, 0)}
# The neighbours on each face's up, right, down and left texture edges: the bit order of the
# engine's canonical neighbour masks (engine/tile-template.mjs).
EDGES = {'north': ['up', 'west', 'down', 'east'], 'east': ['up', 'north', 'down', 'south'],
         'south': ['up', 'east', 'down', 'west'], 'west': ['up', 'south', 'down', 'north'],
         'up': ['north', 'east', 'south', 'west'], 'down': ['south', 'east', 'north', 'west']}
# The methods native blocks can draw, with the number of tiles each one takes.
NATIVE_METHODS = {'ctm': 47, 'horizontal': 4, 'vertical': 4, 'fixed': 1}
# The EDGES positions a linear method connects along: left and right, or down and up.
LINEAR_EDGES = {'horizontal': (3, 1), 'vertical': (2, 0)}
# Java's tile for each linear case; bit 0 is set when the first of those edges connects, bit 1 the second.
LINEAR_TILES = (3, 2, 0, 1)
# Every rule property the native blocks can honour; filters and rule chaining have no native form.
NATIVE_RULE_KEYS = {'id', 'method', 'blocks', 'faces', 'tiles', 'connect'}
RENDERERS = ('classic', 'vv', 'rtx')
TEXTURE_FOLDERS = ('textures/blocks', 'textures/entity')
SAFE_NAME = re.compile('[a-z][a-z0-9_]*')
PACK_KEY = re.compile('[a-z][a-z0-9-]*')
MER = 'metalness_emissive_roughness'
# Vibrant Visuals' MER with a fourth, subsurface channel, which is not an RTX property.
MERS = 'metalness_emissive_roughness_subsurface'
# Texture-set channels a native material can have; normal and heightmap exclude each other, as do MER and MERS.
MATERIAL_CHANNELS = {'color', 'normal', 'heightmap', MER, MERS}
# File-name suffix of each exported channel image.
CHANNEL_SUFFIXES = {'normal': 'normal', 'heightmap': 'height', MER: 'mer', MERS: 'mers'}
# What each renderer's pack carries, for the report.
RENDERER_CHANNELS = {'classic': 'albedo only', 'vv': 'normal/height and MER/MERS',
                     'rtx': 'normal/height and RGB MER; no subsurface property'}
REPORT_LIMITS = ['Native custom blocks require placement or an explicit world conversion.',
                 'Neighbor-driven geometry visibility must be validated in game.',
                 'Repeat/random, filters, rule chaining, and overlay layers are not yet implemented in this backend.',
                 'Geometry complexity and texture atlas budgets require measurement.',
                 'Animation interpolation is verified only at integer game ticks.']


def condition(mask, face, tag):
    """Molang that is true when a face's neighbours form one canonical 47-tile mask.

    Each edge bit asks whether that edge's neighbour has the tag; a corner bit is asked only
    when both of its edges connect, as in the engine's canonical masks.
    """
    vectors = [VECTORS[name] for name in EDGES[face]]
    terms = [_neighbor_term(vector, tag, mask & (1 << index)) for index, vector in enumerate(vectors)]
    for index, edge in enumerate(vectors):
        following = (index + 1) % 4
        if mask & (1 << index) and mask & (1 << following):
            corner = tuple(edge_axis + next_axis for edge_axis, next_axis in zip(edge, vectors[following]))
            terms.append(_neighbor_term(corner, tag, mask & (1 << (index + 4))))
    return ' && '.join(terms)


def native_geometry(identifier, tag, method='ctm'):
    """Block geometry with one bone per face and tile, and the Molang that shows each bone.

    Returns the geometry document and {bone name: visibility expression}; exactly one bone of
    each face is visible for any neighbourhood.
    """
    if method not in NATIVE_METHODS:
        raise ValueError('Unsupported native connection method')
    template_tiles = _template_tiles()
    bones = []
    visibility = {}
    for face in FACES:
        for tile, expression in _face_cases(face, tag, method, template_tiles):
            name = f'{face}_{tile}'
            bones.append(_face_bone(name, face, tile))
            visibility[name] = expression
    description = {'identifier': identifier, 'texture_width': 16, 'texture_height': 16,
                   'visible_bounds_width': 2, 'visible_bounds_height': 2, 'visible_bounds_offset': [0, 0.5, 0]}
    geometry = {'format_version': '1.12.0', 'minecraft:geometry': [{'description': description, 'bones': bones}]}
    return geometry, visibility


def has_animation(pack, source):
    """Whether a tile, or an image channel of its texture set, has a Java animation."""
    if _animation_metadata(source) is not None:
        return True
    descriptor = source.with_suffix('.texture_set.json')
    if descriptor.exists():
        for channel, value in read_json(descriptor)['minecraft:texture_set'].items():
            if channel == 'color' or not isinstance(value, str):
                continue
            path = image_path(_channel_base(pack, source, value))
            if path and _animation_metadata(path) is not None:
                return True
    return False


def export_material(pack, tile, rp, stem, texture_folder='textures/blocks', renderer='vv'):
    """Write a tile as the texture <texture_folder>/<stem> of resource pack rp, for one renderer.

    Classic gets the colour only; Vibrant Visuals gets the texture set as authored; RTX gets an
    RGB MER in place of a MERS. Animated tiles become a flipbook (padded for entity textures).
    Returns (texture path, flipbook entry or None, render method the tile's alpha needs).
    """
    pack = Path(pack).resolve()
    resource_pack = Path(rp)
    if renderer not in RENDERERS:
        raise ValueError('Unknown target renderer')
    if not SAFE_NAME.fullmatch(stem) or texture_folder not in TEXTURE_FOLDERS:
        raise ValueError('Unsafe material output name')
    source = image_path(contained(pack, tile))
    if source is None:
        raise ValueError('Missing native tile: ' + tile)
    with Image.open(source) as image:
        color = image.convert('RGBA')
    images, uniform, channel_animations = _read_channels(pack, tile, source, color)
    if renderer == 'rtx':
        _use_rtx_mer(images, channel_animations)
    elif renderer == 'classic':
        images = {'color': images['color']}
        uniform = {}
        channel_animations = {}
    name = texture_folder + '/' + stem
    flipbook = None
    animation_alpha = None
    if has_animation(pack, source):
        animation = _tile_animation(source, color)
        images, flipbook = _compile_animation(images, animation, name, stem, channel_animations, texture_folder)
        if texture_folder == 'textures/entity':
            # The padded sheet can hold empty cells, so transparency is judged before padding.
            animation_alpha = images['color'].convert('RGBA').getchannel('A')
            images, flipbook = pad_entity_animation(images, flipbook)
    else:
        if color.width != color.height:
            raise ValueError('Non-square native tile requires animation metadata')
        if any(image.size != color.size for image in images.values()):
            raise ValueError('Material sizes differ')
    destination = resource_pack / (name + '.png')
    _save_texture_set(images, uniform, stem, destination)
    alpha = animation_alpha if animation_alpha is not None else images['color'].convert('RGBA').getchannel('A')
    return name, flipbook, _render_method(alpha)


def pad_entity_animation(images, entry, padding=32, max_side=16384):
    """Extrude each animation frame so mip filtering stays inside its material.

    The frames are laid out on a grid of cells, each frame surrounded by `padding` pixels that
    repeat its outermost ones. Returns the padded sheets and the flipbook entry with the grid's
    layout added.
    """
    source = images['color']
    columns = entry.get('columns', 1)
    count = max(entry['frames']) + 1
    width = entry.get('frame_width', source.width // columns)
    height = entry.get('frame_height', source.height // entry.get('rows', count))
    cell_width = width + 2 * padding
    cell_height = height + 2 * padding
    if count > (max_side // cell_width) * (max_side // cell_height):
        raise ValueError('Padded entity animation exceeds texture-size budget')
    target_columns = columns
    if math.ceil(count / target_columns) * cell_height > max_side or target_columns * cell_width > max_side:
        target_columns = min(max_side // cell_width, math.ceil(math.sqrt(count)))
    target_rows = math.ceil(count / target_columns)
    sheets = {}
    for channel, image in images.items():
        sheet = Image.new(image.mode, (target_columns * cell_width, target_rows * cell_height))
        for index in range(count):
            x = (index % columns) * width
            y = (index // columns) * height
            cell = _padded_cell(image.crop((x, y, x + width, y + height)), padding)
            sheet.paste(cell, ((index % target_columns) * cell_width, (index // target_columns) * cell_height))
        sheets[channel] = sheet
    layout = {'layout': 'grid', 'columns': target_columns, 'rows': target_rows, 'frame_width': width,
              'frame_height': height, 'padding': padding, 'cell_width': cell_width, 'cell_height': cell_height,
              'pixel_width': target_columns * cell_width, 'pixel_height': target_rows * cell_height}
    return sheets, {**entry, **layout}


def build_native_ctm(pack, rule_file, key, root=ROOT, renderer='rtx'):
    """Build the native connected-texture add-on for a rules document and return its path.

    The .mcaddon and its report are written to <root>/dist/development.
    """
    pack = Path(pack).resolve()
    root = Path(root)
    if renderer not in RENDERERS:
        raise ValueError('Unknown target renderer')
    if not PACK_KEY.fullmatch(key):
        raise ValueError('Use a lowercase native pack key')
    rules = _native_rules(read_json(rule_file))
    destination = root / 'dist/development'
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='native-ctm-') as temporary:
        generation = Path(temporary)
        behavior_pack = generation / 'NativeConnected_BP'
        resource_pack = generation / 'NativeConnected_RP'
        _write_manifests(behavior_pack, resource_pack, key, renderer)
        atlas = {}
        flipbooks = []
        bindings = []
        for rule in rules:
            stem = 'bct_' + key.replace('-', '_') + '_' + rule['id']
            block_id = 'bct_native:' + stem
            # Each block carries a tag named after itself, which its neighbours' geometry looks for.
            tag = block_id
            materials = _export_rule_tiles(pack, rule['tiles'], stem, resource_pack, renderer, atlas, flipbooks)
            geometry, visibility = native_geometry('geometry.' + stem, tag, rule['method'])
            write_json(resource_pack / f'models/blocks/{stem}.geo.json', geometry)
            write_json(behavior_pack / f'blocks/{stem}.json',
                       _block_definition(block_id, tag, 'geometry.' + stem, visibility, materials))
            bindings.append({'source_block': rule['blocks'][0], 'native_block': block_id, 'rule': rule['id']})
        write_json(resource_pack / 'textures/terrain_texture.json',
                   {'resource_pack_name': 'bct_native_connected', 'texture_name': 'atlas.terrain', 'padding': 8,
                    'num_mip_levels': 4, 'texture_data': atlas})
        if flipbooks:
            write_json(resource_pack / 'textures/flipbook_textures.json', flipbooks)
        archive = destination / f'native-connected-{key}-{renderer}.mcaddon'
        _write_archive(archive, generation)
    report = {'archive': str(archive), 'renderer': 'native block terrain materials', 'graphics_mode': renderer,
              'material_channels': RENDERER_CHANNELS[renderer], 'bindings': bindings,
              'animated_tiles': len(flipbooks), 'world_blocks_modified': False, 'in_game_tested': False,
              'rtx_verified': False, 'requires_engine': [1, 26, 20], 'limits': REPORT_LIMITS}
    write_json(destination / f'native-connected-{key}-{renderer}-report.json', report)
    return archive


def _neighbor_term(offset, tag, connected):
    """Molang asking whether the block at offset has the tag, negated when it must not."""
    query = "q.block_neighbor_has_any_tag(%s, '%s')" % (', '.join(map(str, offset)), tag)
    return query if connected else '!' + query


def _template_tiles():
    """The engine's tile number for each canonical mask, read from its own table."""
    source = (ROOT / 'engine/tile-template.mjs').read_text()
    return json.loads(source.split('Object.freeze(', 1)[1].split(');', 1)[0])


def _face_cases(face, tag, method, template_tiles):
    """(tile, Molang condition) for every neighbour case the method tells apart on one face."""
    if method == 'ctm':
        return [(template_tiles[str(mask)], condition(mask, face, tag)) for mask in MASKS]
    if method == 'fixed':
        return [(0, '1.0')]
    vectors = [VECTORS[EDGES[face][index]] for index in LINEAR_EDGES[method]]
    cases = []
    for combination, tile in enumerate(LINEAR_TILES):
        terms = [_neighbor_term(vector, tag, combination & (1 << index)) for index, vector in enumerate(vectors)]
        cases.append((tile, ' && '.join(terms)))
    return cases


def _face_bone(name, face, tile):
    """A full-block cube that draws only one face, with the tile's material instance."""
    uv = {face: {'uv': [0, 0], 'uv_size': [16, 16], 'material_instance': f'tile_{tile}'}}
    return {'name': name, 'pivot': [0, 0, 0], 'cubes': [{'origin': [-8, 0, -8], 'size': [16, 16, 16], 'uv': uv}]}


def _animation_metadata(path):
    """A texture's Java .mcmeta sidecar when it has an animation section, else None."""
    metadata = Path(str(path) + '.mcmeta')
    if metadata.exists():
        document = read_json(metadata)
        if 'animation' in document:
            return document
    return None


def _channel_base(pack, source, value):
    """A texture-set channel's image path without extension.

    A channel named from textures/ is pack-relative; any other name sits beside the tile.
    """
    if value.startswith('textures/'):
        return contained(pack, value)
    return contained(pack, source.parent.relative_to(pack) / value)


def _read_channels(pack, tile, source, color):
    """The tile's channel images, its uniform channel values and the channels' own animations."""
    images = {'color': color}
    uniform = {}
    channel_animations = {}
    descriptor = source.with_suffix('.texture_set.json')
    if not descriptor.exists():
        return images, uniform, channel_animations
    channels = read_json(descriptor)['minecraft:texture_set']
    if 'color' in channels and channels['color'] not in (source.stem, source.name, tile, tile.removesuffix('.png')):
        raise ValueError('Texture-set color indirection requires explicit source mapping')
    if set(channels) - MATERIAL_CHANNELS:
        raise ValueError('Unknown material channels')
    if {'normal', 'heightmap'} <= set(channels) or {MER, MERS} <= set(channels):
        raise ValueError('Conflicting texture-set channels')
    for channel, value in channels.items():
        if channel == 'color':
            continue
        if not isinstance(value, str):
            if channel == MERS:
                raise ValueError('Native MERS requires an image-based channel')
            uniform[channel] = value
            continue
        path = image_path(_channel_base(pack, source, value))
        if path is None:
            raise ValueError('Missing native material channel: ' + value)
        with Image.open(path) as image:
            images[channel] = image.copy()
        metadata = _animation_metadata(path)
        if metadata is not None:
            channel_animations[channel] = parse_animation(images[channel].size, metadata)
    return images, uniform, channel_animations


def _use_rtx_mer(images, channel_animations):
    """RTX reads an RGB MER: a MERS loses its subsurface channel, which is no RTX property."""
    if MERS in images:
        images[MER] = images.pop(MERS).convert('RGB')
        if MERS in channel_animations:
            channel_animations[MER] = channel_animations.pop(MERS)


def _tile_animation(source, color):
    """The tile's own animation; a tile animated only through a channel keeps its single frame."""
    metadata = _animation_metadata(source)
    if metadata is None:
        metadata = {'animation': {'frames': [0]}}
    return parse_animation(color.size, metadata)


def _compile_animation(images, animation, name, stem, channel_animations, texture_folder):
    """The flipbook sheets and entry; a strip too tall for the texture budget becomes a grid on entities."""
    try:
        return compile_family(images, animation, name, stem, channel_animations=channel_animations)
    except ValueError as error:
        # Block flipbooks must be one strip; entity textures animate by UV and can use a grid.
        if texture_folder != 'textures/entity' or 'texture-size budget' not in str(error):
            raise
        return compile_family(images, animation, name, stem, channel_animations=channel_animations, layout='grid')


def _padded_cell(frame, padding):
    """The frame inside a border of `padding` pixels that repeats its outermost pixels."""
    width, height = frame.size
    cell_width = width + 2 * padding
    cell = Image.new(frame.mode, (cell_width, height + 2 * padding))
    cell.paste(frame, (padding, padding))
    cell.paste(frame.crop((0, 0, 1, height)).resize((padding, height)), (0, padding))
    cell.paste(frame.crop((width - 1, 0, width, height)).resize((padding, height)), (padding + width, padding))
    top_row = cell.crop((0, padding, cell_width, padding + 1))
    cell.paste(top_row.resize((cell_width, padding)), (0, 0))
    bottom_row = cell.crop((0, padding + height - 1, cell_width, padding + height))
    cell.paste(bottom_row.resize((cell_width, padding)), (0, padding + height))
    return cell


def _save_texture_set(images, uniform, stem, destination):
    """Save the colour image at destination, each other channel beside it, and the texture set."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    images['color'].save(destination)
    texture_set = {'color': stem, **uniform}
    for channel, image in images.items():
        if channel == 'color':
            continue
        channel_name = stem + '_' + CHANNEL_SUFFIXES[channel]
        # Bedrock reads a heightmap as greyscale and a normal map as RGB.
        if channel == 'heightmap':
            image = image.convert('L')
        elif channel == 'normal':
            image = image.convert('RGB')
        image.save(destination.with_name(channel_name + '.png'))
        texture_set[channel] = channel_name
    if len(texture_set) > 1:
        write_json(destination.with_suffix('.texture_set.json'),
                   {'format_version': '1.21.30', 'minecraft:texture_set': texture_set})


def _render_method(alpha):
    """opaque when every pixel is solid, blend when any is partly see-through, else alpha test."""
    if alpha.getextrema() == (255, 255):
        return 'opaque'
    if any(0 < value < 255 for value in alpha.tobytes()):
        return 'blend'
    return 'alpha_test_single_sided'


def _native_rules(document):
    """The document's rules, each checked to be one the native blocks draw exactly."""
    rules = document.get('rules', [])
    if document.get('format_version') != 1 or not rules:
        raise ValueError('Native connected textures need a nonempty rules document')
    for rule in rules:
        _check_native_rule(rule)
    if len({rule['id'] for rule in rules}) != len(rules):
        raise ValueError('Duplicate native rule ID')
    return rules


def _check_native_rule(rule):
    """Refuse a rule the native blocks cannot draw exactly."""
    if set(rule) - NATIVE_RULE_KEYS:
        raise ValueError('Native connected textures do not support rule filters or chaining')
    if rule.get('method') not in NATIVE_METHODS or len(rule.get('tiles', [])) != NATIVE_METHODS[rule['method']]:
        raise ValueError('Native connection method has an unsupported tile count')
    if rule.get('connect', 'block') != 'block' or set(rule.get('faces', FACES)) != set(FACES):
        raise ValueError('Native connected textures need all cube faces with block connectivity')
    if len(rule.get('blocks', [])) != 1:
        raise ValueError('Native connected textures need one source block per rule')
    if not SAFE_NAME.fullmatch(rule.get('id', '')):
        raise ValueError('Invalid native rule ID')
    if any(tile in SPECIAL_TILES for tile in rule['tiles']):
        raise ValueError('Native connected textures need material tiles for every case')


def _write_manifests(behavior_pack, resource_pack, key, renderer):
    """Manifests for the behaviour and resource packs, with ids stable for a key and renderer."""
    ids = {kind: str(uuid.uuid5(uuid.NAMESPACE_URL, f'bct/native-connected/{renderer}/{key}/{kind}'))
           for kind in ('bp', 'rp', 'bp_module', 'rp_module')}
    for kind, folder, module in [('bp', behavior_pack, 'data'), ('rp', resource_pack, 'resources')]:
        header = {'name': f'Native connected textures ({renderer.upper()})',
                  'description': ('Native block materials; no automatic vanilla replacement; '
                                  'in-game validation pending.'),
                  'uuid': ids[kind], 'version': [0, 1, 0], 'min_engine_version': [1, 26, 20]}
        manifest = {'format_version': 2, 'header': header,
                    'modules': [{'type': module, 'uuid': ids[kind + '_module'], 'version': [0, 1, 0]}]}
        if kind == 'bp':
            manifest['dependencies'] = [{'uuid': ids['rp'], 'version': [0, 1, 0]}]
        elif renderer != 'classic':
            # The game reads texture sets only from packs declaring raytraced (RTX) or pbr (Vibrant Visuals).
            manifest['capabilities'] = ['raytraced'] if renderer == 'rtx' else ['pbr']
        write_json(folder / 'manifest.json', manifest)


def _export_rule_tiles(pack, tiles, stem, resource_pack, renderer, atlas, flipbooks):
    """Export a rule's tiles into the atlas and flipbooks; returns the block's material instances."""
    materials = {}
    for index, tile in enumerate(tiles):
        texture = stem + '_' + str(index)
        name, flipbook, render_method = export_material(pack, tile, resource_pack, texture, renderer=renderer)
        atlas[texture] = {'textures': name}
        materials[f'tile_{index}'] = {'texture': texture, 'render_method': render_method, 'ambient_occlusion': 1.0,
                                      'face_dimming': True}
        if flipbook:
            flipbooks.append(flipbook)
    # A block draws with one render method, so its tiles must all need the same one.
    if len({material['render_method'] for material in materials.values()}) != 1:
        raise ValueError('Mixed tile transparency requires an explicit shared render method; '
                         'no invalid native block exported')
    materials['*'] = 'tile_0'
    return materials


def _block_definition(block_id, tag, geometry_id, visibility, materials):
    """A solid, mineable block showing the geometry's visible bones with the tile materials."""
    components = {'minecraft:geometry': {'identifier': geometry_id, 'bone_visibility': visibility},
                  'minecraft:material_instances': materials,
                  'tag:' + tag: {},
                  'minecraft:collision_box': True,
                  'minecraft:selection_box': True,
                  'minecraft:destructible_by_mining': {'seconds_to_destroy': 1}}
    return {'format_version': '1.26.20',
            'minecraft:block': {'description': {'identifier': block_id}, 'components': components}}


def _write_archive(archive, folder):
    """Zip every file under folder, by its path relative to folder."""
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as output:
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                output.write(path, path.relative_to(folder).as_posix())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resource-pack', type=Path, required=True)
    parser.add_argument('--rules', type=Path, required=True)
    parser.add_argument('--key', required=True)
    parser.add_argument('--renderer', choices=['classic', 'vv', 'rtx'], default='rtx')
    args = parser.parse_args()
    print(build_native_ctm(args.resource_pack, args.rules, args.key, renderer=args.renderer))
