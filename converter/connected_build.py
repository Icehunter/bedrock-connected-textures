"""Build the connected-texture add-on for one pack from its rules document.

Each rule gets a carrier entity: a passive area-effect cloud the engine spawns in front of each face the
rule draws. Its geometry has one thin plane per world face, its texture array holds the rule's tiles and
its render controller shows the face and tile the engine picks. Blocks the author draws with model parts
(plants, paths, carpets) get carriers shaped like those parts instead.

Key decisions:
- The author's look wins: tiles, PBR channels, animations and tints are copied as authored; the engine only
  picks which tile each face shows.
- Carriers serve Classic and Vibrant Visuals. Entity materials have no ray-traced PBR, so RTX draws with
  native block materials and the carriers hide themselves there.
- Geometry is in the carrier's yaw-zero world frame with Java's face UVs, so Java's eight texture
  orientations are plain UV mirrors and quarter turns.
- A native face that a see-through tile replaces is blanked and redrawn by a fallback carrier, so the native
  texture never shows through.
"""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

from PIL import Image

from addon_package import packet, write_source_scripts
from biome_colors import resolve_colors
from carrier_atlas import consolidate_carrier_atlases
from carrier_materials import apply_carrier_filter, carrier_material, carrier_uv_materials, write_carrier_uv_materials
from carrier_structure import cloud_structure
from common import BLOCKS_FORMAT, read_json, samples_path, write_json
from java_texture_animation import parse_animation, sprite_frames, uv_animation_xy
from native_connected import export_material, has_animation
from palette_export import apply_tint_palettes
from terrain_pack import contained, image_path
from terrain_providers import MASKS, ROOT, carrier_controllers, manifest, server_entity
from terrain_providers import geometry as face_plane_geometry
from tint_palette import rule_tint_palettes
from vv_scene_profile import bind_block_fallbacks


# Tiles each method takes; None means any number. The repeat family is checked against width x height.
METHODS = {'ctm': 47, 'horizontal': 4, 'vertical': 4, 'horizontal+vertical': 7, 'vertical+horizontal': 7,
           'top': 1, 'fixed': 1, 'random': None, 'repeat': None, 'ctm_repeat': None, 'horizontal_repeat': None,
           'overlay': 17, 'overlay_ctm': 47, 'overlay_random': None, 'overlay_repeat': None, 'overlay_fixed': 1}
# The engine numbers carrier faces in this order (the bct:face property and bct:cull_mask bits).
FACES = ['north', 'east', 'south', 'west', 'up', 'down']
# Where each face's carrier stands, in blocks from the centre of the block's top face: 0.502 blocks out
# from the block centre, the engine's carrierPosition.
CARRIER_OFFSETS = {'north': (0, -.5, -.502), 'east': (.502, -.5, 0),
                   'south': (0, -.5, .502), 'west': (-.502, -.5, 0),
                   'up': (0, .002, 0), 'down': (0, -1.002, 0)}
# How far a face plane sits outside its block face, in blocks, so it draws over the face instead of
# fighting it.
SIDE_CLEARANCE = 1 / 256
# The top plane sits lower, so terrain transition surfaces (4/1024 of a block and up) still draw over it.
TOP_CLEARANCE = 3 / 1024

_SIDE_FACES = ('north', 'east', 'south', 'west')
_FACE_VECTORS = {'north': (0, 0, -1), 'east': (1, 0, 0), 'south': (0, 0, 1),
                 'west': (-1, 0, 0), 'up': (0, 1, 0), 'down': (0, -1, 0)}
# Java tile markers, not images: '<skip>' moves on to the next rule, '<default>' keeps the native face.
_TILE_MARKERS = ('<skip>', '<default>')
_RULE_FIELDS = frozenset({
    'id', 'method', 'blocks', 'faces', 'tiles', 'connect', 'width', 'height', 'weights', 'seed', 'matchTiles',
    'filename', 'weight', 'states', 'heights', 'symmetry', 'randomLoops', 'linked', 'innerSeams', 'orientation',
    'orient', 'biomes', 'blockMatchers', 'connectTiles', 'connectBlocks', 'layer', 'tintIndex', 'tintBlock'})
_OVERLAY_FIELDS = ('connectTiles', 'connectBlocks', 'layer', 'tintIndex', 'tintBlock')
_ORIENTED_METHODS = frozenset({'ctm', 'horizontal', 'vertical', 'horizontal+vertical', 'vertical+horizontal',
                               'repeat', 'ctm_repeat', 'horizontal_repeat', 'overlay_ctm', 'overlay_repeat'})
# Repeat methods hold width x height cells, each with its base method's tiles.
_REPEAT_TILES_PER_CELL = {'repeat': 1, 'overlay_repeat': 1, 'ctm_repeat': 47, 'horizontal_repeat': 4}
_RULE_ID = '[a-z][a-z0-9_]*'
_NAMESPACED_ID = '[a-z0-9_]+:[a-z0-9_]+'
_PACK_KEY = '[a-z][a-z0-9-]*'
_MAX_TILE_SIZE = 4096
# Quarter turns undo with the opposite turn; the four mirrored orientations undo themselves.
_INVERSE_ORIENTATION = (0, 3, 2, 1, 4, 5, 6, 7)
# Fields the converter adds to a model-part face; they must not change the face's carrier signature.
_GROUP_SIGNATURE_SKIPS = ('renderers', 'fallbackRule', 'bundled')
_FACE_SIGNATURE_SKIPS = ('renderers', 'fallbackRule')
# Native faces a carrier owns point at this fully transparent terrain texture.
_OWNED_TEXTURE = 'bct_owned_transparent'
# Older carriers used the actor colour-mask shaders; baked tints use the plain parents.
_PLAIN_MATERIALS = {'entity_change_color': 'entity', 'entity_alphatest_change_color': 'entity_alphatest'}
# No metal, no emission, fully rough, no subsurface: the block PBR fallback when the pack sets none.
_DEFAULT_BLOCK_MERS = (0, 0, 255, 0)
# The vanilla lingering-potion particle, overridden so carrier clouds emit nothing.
_CARRIER_PARTICLE = 'resource_pack/particles/mobspell_lingering.json'
# Version stamps of the carrier geometry and of the build report, recorded in the outputs.
_GEOMETRY_REVISION = 16
_REPORT_REVISION = 21
# Document fields the engine reads, in packet order; list fields default to [] and the others to {}.
_CONFIGURATION_FIELDS = ('baseTextures', 'sourceBlocks', 'textureOrientations', 'baseTextureVariants',
                         'fullCubeBlocks', 'opaqueBlocks', 'grassTints', 'foliageTints', 'customTints',
                         'modelTintTypes', 'customTintBlocks', 'nativeBlockJavaIds', 'modelGeometryBlocks',
                         'leafDistanceLeaves', 'leafDistanceLogs')
_LIST_FIELDS = frozenset({'sourceBlocks', 'fullCubeBlocks', 'opaqueBlocks', 'customTintBlocks',
                          'modelGeometryBlocks', 'leafDistanceLeaves', 'leafDistanceLogs'})
_LIMITATIONS = ['Entity materials do not provide RTX PBR.',
                'Source shade and light emission metadata do not override entity shader lighting.',
                'UV lock on cropped or element-rotated model parts requires a mesh adapter.',
                'Unknown orientation, tint, or geometry providers defer the face.',
                'Distant depth precision and entity culling require in-game verification.']


def validate(document, pack):
    """Check a rules document and return its rules with the defaults filled in.

    Every problem raises before anything is written. Tile images must lie inside the pack and be square
    stills, or animations with square frames, up to 4096 px.
    """
    if document.get('format_version') != 1 or not isinstance(document.get('rules'), list) or not document['rules']:
        raise ValueError('Rules require format_version 1 and a nonempty rules list')
    rules = []
    seen = set()
    for raw in document['rules']:
        rules.append(_validated_rule(dict(raw), document, pack, seen))
    return rules


def _validated_rule(rule, document, pack, seen):
    unsupported = set(rule) - _RULE_FIELDS
    if unsupported:
        raise ValueError('Unsupported rule fields: ' + str(sorted(unsupported)))
    name = rule.get('id', '')
    if not re.fullmatch(_RULE_ID, name) or name in seen:
        raise ValueError('Rule IDs must be unique lowercase names')
    seen.add(name)
    method = rule.get('method')
    if method not in METHODS:
        raise ValueError('Unsupported method: ' + str(method))
    _check_targets(rule)
    _check_orientation(rule, method, document)
    overlay = method.startswith('overlay')
    _check_overlay_fields(rule, overlay)
    _check_matchers(rule)
    tiles = rule.get('tiles')
    _check_tile_count(rule, method, tiles)
    _check_randomness(rule, method, tiles)
    _check_tile_images(tiles, overlay, pack)
    return rule


def _check_targets(rule):
    """Blocks, matchTiles, faces and connect, filling in their defaults."""
    blocks = rule.setdefault('blocks', [])
    if not isinstance(blocks, list) or any(not isinstance(block, str) or not re.fullmatch(_NAMESPACED_ID, block)
                                           for block in blocks):
        raise ValueError('Use a nonempty list of namespaced block IDs')
    if not blocks and not rule.get('matchTiles'):
        raise ValueError('Rules need blocks or matchTiles')
    if 'matchTiles' in rule and not _is_string_list(rule['matchTiles']):
        raise ValueError('Invalid matchTiles')
    rule.setdefault('faces', FACES.copy())
    if not isinstance(rule['faces'], list) or not rule['faces'] or any(face not in FACES for face in rule['faces']):
        raise ValueError('Invalid faces')
    rule.setdefault('connect', 'tile' if rule.get('matchTiles') else 'block')
    if rule['connect'] not in ('block', 'state', 'tile'):
        raise ValueError('connect must be block, state or tile')


def _check_orientation(rule, method, document):
    """Symmetry, a fixed orientation, or an orientation mode taken from the block's state or texture."""
    if rule.get('symmetry', 'none') not in ('none', 'opposite', 'all'):
        raise ValueError('Invalid symmetry')
    orientation = rule.get('orientation', 0)
    if type(orientation) is not int or not 0 <= orientation <= 7:
        raise ValueError('Invalid orientation')
    if orientation and method not in _ORIENTED_METHODS:
        raise ValueError('Orientation is not used by this method')
    if 'orient' not in rule:
        return
    if method not in _ORIENTED_METHODS or rule['orient'] not in ('none', 'state_axis', 'texture'):
        raise ValueError('Unsupported orientation mode for method')
    if rule['orient'] == 'texture' and not (document.get('textureOrientations')
                                            or document.get('baseTextureVariants')):
        raise ValueError('Texture orientation mode requires a block/face orientation provider')
    if orientation:
        raise ValueError('Choose explicit orientation or state-axis orientation, not both')


def _check_overlay_fields(rule, overlay):
    if any(field in rule for field in _OVERLAY_FIELDS) and not overlay:
        raise ValueError('Overlay fields require an overlay method')
    if rule.get('layer', 'cutout') not in ('cutout', 'cutout_mipped', 'translucent'):
        raise ValueError('Unsupported overlay layer')
    tint_index = rule.get('tintIndex', -1)
    if type(tint_index) is not int or tint_index < -1:
        raise ValueError('Invalid overlay tint index')
    for field in ('connectTiles', 'connectBlocks'):
        if field in rule and not _is_string_list(rule[field]):
            raise ValueError('Invalid ' + field)


def _check_matchers(rule):
    """randomLoops, the linked and innerSeams switches, and the state, biome and height matchers."""
    random_loops = rule.get('randomLoops', 0)
    if type(random_loops) is not int or not 0 <= random_loops <= 9:
        raise ValueError('Invalid randomLoops')
    for field in ('linked', 'innerSeams'):
        if field in rule and type(rule[field]) is not bool:
            raise ValueError('Invalid ' + field)
    if 'states' in rule:
        states = rule['states']
        if not isinstance(states, dict) or any(not isinstance(values, list) or not values
                                               for values in states.values()):
            raise ValueError('Invalid state matcher')
    if 'blockMatchers' in rule:
        _check_block_matchers(rule['blockMatchers'], rule['blocks'])
    if 'biomes' in rule and not _is_biome_matcher(rule['biomes']):
        raise ValueError('Invalid biome matcher')
    if 'heights' in rule and not _is_height_list(rule['heights']):
        raise ValueError('Invalid heights')


def _check_block_matchers(clauses, blocks):
    """Each clause names one of the rule's blocks and the state values it accepts."""
    if not isinstance(clauses, list) or not clauses:
        raise ValueError('Invalid block matchers')
    for clause in clauses:
        valid = (isinstance(clause, dict) and set(clause) == {'block', 'states'} and clause['block'] in blocks
                 and isinstance(clause['states'], dict)
                 and all(_is_string_list(values) for values in clause['states'].values()))
        if not valid:
            raise ValueError('Invalid block state clause')


def _is_biome_matcher(biomes):
    return (isinstance(biomes, dict) and set(biomes) == {'ids', 'exclude'} and type(biomes['exclude']) is bool
            and isinstance(biomes['ids'], list) and bool(biomes['ids'])
            and all(isinstance(value, str) and re.fullmatch(_NAMESPACED_ID, value) for value in biomes['ids']))


def _is_height_list(heights):
    return isinstance(heights, list) and all(
        isinstance(span, list) and len(span) == 2 and all(type(bound) is int for bound in span)
        and span[0] <= span[1]
        for span in heights)


def _is_string_list(value):
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) for item in value)


def _check_tile_count(rule, method, tiles):
    if not isinstance(tiles, list) or not 1 <= len(tiles) <= 256 or any(not isinstance(tile, str) for tile in tiles):
        raise ValueError('Supply 1 to 256 tile paths')
    count = METHODS[method]
    if method in _REPEAT_TILES_PER_CELL:
        if any(type(rule.get(field)) is not int or not 1 <= rule[field] <= 256 for field in ('width', 'height')):
            raise ValueError('Repeat width and height must be positive integers')
        count = rule['width'] * rule['height'] * _REPEAT_TILES_PER_CELL[method]
    if count is not None and len(tiles) != count:
        raise ValueError(f'{method} requires {count} tiles')


def _check_randomness(rule, method, tiles):
    if 'weights' in rule:
        weights = rule['weights']
        if (method not in ('random', 'overlay_random') or len(weights) != len(tiles)
                or any(type(weight) is not int or not 0 <= weight < 1e9 for weight in weights)
                or not 0 < sum(weights) < 2 ** 31):
            raise ValueError('Random weights must be nonnegative integers with a positive 32-bit total'
                             ' and match the tile count')
    if 'seed' in rule and (type(rule['seed']) is not int or not -(2 ** 31) <= rule['seed'] < 2 ** 31):
        raise ValueError('seed must be a signed 32-bit integer')
    # Java packs vary random tiles with randomLoops; a custom seed would pick tiles Java never shows.
    if rule.get('seed', 0):
        raise ValueError('Custom seed is not Java compatible; use randomLoops')


def _check_tile_images(tiles, overlay, pack):
    for tile in tiles:
        # An overlay draws on top of a face; it has no native face of its own to keep.
        if tile == '<default>' and overlay:
            raise ValueError('Overlay methods cannot use the default tile')
        if tile in _TILE_MARKERS:
            continue
        path = image_path(contained(pack, tile))
        if path is None:
            raise ValueError('Missing tile: ' + tile)
        with Image.open(path) as image:
            animation = _animation(path, image.size)
            if animation is not None:
                if animation.width != animation.height or animation.width > _MAX_TILE_SIZE:
                    raise ValueError('Animation frames must be square up to 4096px')
            elif image.width != image.height or image.width > _MAX_TILE_SIZE:
                raise ValueError('Tiles must be square stills up to 4096px')


def _animation(path, size):
    """The tile's Java animation from its .mcmeta, or None for a still tile."""
    metadata_path = Path(str(path) + '.mcmeta')
    if not metadata_path.exists():
        return None
    metadata = read_json(metadata_path)
    if 'animation' not in metadata:
        return None
    return parse_animation(size, metadata)


def model_material_rules(document):
    """Fixed fallback rules that give the author's model art its own carrier materials.

    Blocks whose weighted model choices look different, and custom-tinted blocks, are drawn from their
    model choices; face layers and model parts need their textures as materials too. Returns the rules,
    a texture -> rule id lookup, the blocks drawn from model choices and the blocks with face layers.
    """
    sprites, blocks, layer_blocks = _model_sprites(document)
    rules = []
    lookup = {}
    for sprite in sorted(sprites):
        name = 'model_texture_' + hashlib.sha256(sprite.encode()).hexdigest()[:20]
        lookup[sprite] = name
        rules.append({'id': name, 'method': 'fixed', 'blocks': [], 'faces': FACES.copy(),
                      'tiles': [sprite], 'fallback': True, 'modelFallback': True})
    return rules, lookup, sorted(blocks), sorted(layer_blocks)


def _model_sprites(document):
    """Textures model carriers draw, the blocks drawn from model choices and the blocks with face layers."""
    cubes = set(document.get('fullCubeBlocks', []))
    blocks = set(document.get('customTintBlocks', []))
    layer_blocks = set()
    sprites = set()
    for block, variants in document.get('baseTextureVariants', {}).items():
        has_parts = any(_part_faces(choice) for variant in variants for choice in _variant_choices(variant))
        # With a cube list, only full cubes and blocks with model parts have carriers.
        if cubes and block not in cubes and not has_parts:
            continue
        for variant in variants:
            choices = variant.get('modelChoices', [variant])
            # Weighted choices that look alike draw like the plain block; only differing looks need models.
            if len({_look_signature(choice) for choice in choices}) > 1:
                blocks.add(block)
            for choice in choices:
                for layers in choice.get('faceLayers', {}).values():
                    if layers:
                        layer_blocks.add(block)
                    sprites.update(layer['texture'] for layer in layers)
            for choice in _variant_choices(variant):
                sprites.update(face['texture'] for face in _part_faces(choice))
    for block in blocks:
        sprites.update(document.get('baseTextures', {}).get(block, {}).values())
        for variant in document.get('baseTextureVariants', {}).get(block, []):
            for choice in variant.get('modelChoices', [variant]):
                sprites.update(choice.get('faces', {}).values())
    return sprites, blocks, layer_blocks


def _look_signature(choice):
    return json.dumps({key: choice.get(key, {}) for key in ('faces', 'orientations', 'tintIndices', 'faceLayers')},
                      sort_keys=True)


def _variant_choices(variant):
    """A variant's weighted model choices (or the variant itself), then the choices of its part groups."""
    group_choices = [choice for group in variant.get('modelPartsGroups', [])
                     for choice in group.get('modelChoices', [])]
    return [*variant.get('modelChoices', [variant]), *group_choices]


def _document_choices(document):
    return [choice for variants in document.get('baseTextureVariants', {}).values()
            for variant in variants for choice in _variant_choices(variant)]


def _part_faces(choice):
    return [face for part in choice.get('modelParts', []) for face in part.get('faces', {}).values()]


def transparent_replacements(document, rules, pack, samples):
    """Own the native faces that see-through replacement tiles draw over, with their original art as fallback.

    A connected tile with transparent pixels would show the native face (and its edges) through it. Those
    faces get a blank native texture instead, and a fallback rule draws the original art wherever no
    connected tile applies. Returns the blocks.json overrides, the owned faces of each block (face ->
    fallback rule id) and the fallback rules.
    """
    bases = document.get('baseTextures', {})
    cubes = set(document.get('fullCubeBlocks', []))
    if not cubes:
        return {}, {}, []
    native_blocks = read_json(samples / 'resource_pack/blocks.json')
    alpha_cache = {}
    overrides = {}
    fallbacks = {}
    fallback_rules = []
    fallback_by_tile = {}
    # Overlays draw on top of the native face rather than replacing it.
    replacements = [rule for rule in rules if not rule['method'].startswith('overlay')]
    for block in sorted(cubes & set(bases)):
        owned = {}
        for face, base_texture in bases[block].items():
            if face not in FACES:
                continue
            if not _reaches_transparency(replacements, base_texture, face, block, pack, alpha_cache):
                continue
            if image_path(contained(pack, base_texture)) is None:
                raise ValueError('Transparent replacement requires original fallback material: ' + base_texture)
            if base_texture not in fallback_by_tile:
                name = 'native_fallback_' + str(len(fallback_by_tile))
                fallback_by_tile[base_texture] = name
                fallback_rules.append({'id': name, 'method': 'fixed', 'blocks': [], 'faces': FACES.copy(),
                                       'tiles': [base_texture], 'fallback': True})
            owned[face] = fallback_by_tile[base_texture]
        if not owned:
            continue
        name, entry = _owned_block_entry(block, owned, native_blocks)
        overrides[name] = entry
        fallbacks[block] = owned
    return overrides, fallbacks, fallback_rules


def _reaches_transparency(rules, tile, face, block, pack, alpha_cache):
    """Whether any rule that can draw over this face draws a tile with transparent pixels."""
    transparent = False
    for rule in _rules_reaching(rules, tile, face, block):
        for drawn in rule['tiles']:
            transparent |= _has_transparency(drawn, pack, alpha_cache)
    return transparent


def _has_transparency(tile, pack, alpha_cache):
    if tile in _TILE_MARKERS:
        return False
    if tile not in alpha_cache:
        path = image_path(contained(pack, tile))
        if path is None:
            return False
        alpha_cache[tile] = _alpha_kinds(path)[0]
    return alpha_cache[tile]


def _owned_block_entry(block, owned, native_blocks):
    """The block's vanilla blocks.json entry with its owned faces pointing at the blank texture."""
    name = block.removeprefix('minecraft:')
    native_entry = native_blocks.get(name)
    if not native_entry:
        legacy = _legacy_block_name(name, native_blocks)
        if legacy:
            name = legacy
            native_entry = native_blocks[name]
    if not native_entry:
        raise ValueError('Missing native block face definition for transparent replacement: ' + block)
    entry = copy.deepcopy(native_entry)
    textures = entry.get('textures')
    if isinstance(textures, str):
        textures = {face: textures for face in FACES}
    elif isinstance(textures, dict):
        textures = {face: textures.get(face, textures.get('side') if face in _SIDE_FACES else textures.get('all'))
                    for face in FACES}
    else:
        raise ValueError('Unsupported native transparent face binding: ' + block)
    if any(value is None for value in textures.values()):
        raise ValueError('Incomplete native face binding: ' + block)
    entry['textures'] = {**textures, **{face: _OWNED_TEXTURE for face in owned}}
    return name, entry


def _legacy_block_name(name, native_blocks):
    """The older Bedrock name blocks.json still lists a renamed block under, if any."""
    # Imported on use: the Java binding modules import import_java_ctm, which imports this module.
    from java_block_bindings import BLOCK_ALIASES
    return next((alias for alias, target in BLOCK_ALIASES.items() if target == name and alias in native_blocks),
                None)


def _rules_reaching(rules, tile, face, block, java_block=None):
    """Yield each rule that can draw over a face showing this tile.

    A rule applies when it covers the face and the block (or the block's Java name) and has no matchTiles,
    or its matchTiles names the tile or a tile another applying rule draws: rules chain through the tiles
    they draw. Passes repeat until no new tile turns up, so a rule can be yielded more than once.
    """
    reachable = {tile}
    pending = True
    while pending:
        pending = False
        for rule in rules:
            if not _covers(rule, face, block, java_block):
                continue
            if rule.get('matchTiles') and not reachable.intersection(rule['matchTiles']):
                continue
            yield rule
            for drawn in rule['tiles']:
                if drawn not in _TILE_MARKERS and drawn not in reachable:
                    reachable.add(drawn)
                    pending = True


def _covers(rule, face, block, java_block):
    if face not in rule['faces']:
        return False
    return not rule['blocks'] or block in rule['blocks'] or java_block in rule['blocks']


def _alpha_kinds(path):
    """(any pixel below full alpha, any pixel between clear and opaque), over every frame an animation shows."""
    with Image.open(path) as image:
        animation = _animation(path, image.size)
        if animation is not None:
            frames = sprite_frames(image, animation)
            shown = {index for index, _ in animation.frames}
            histograms = [frames[index].getchannel('A').histogram() for index in shown]
        else:
            histograms = [image.convert('RGBA').getchannel('A').histogram()]
    has_transparency = any(any(histogram[:255]) for histogram in histograms)
    has_partial_alpha = any(any(histogram[1:255]) for histogram in histograms)
    return has_transparency, has_partial_alpha


def orient_geometry(base, identifier, orientation):
    """Copy carrier geometry with its face UVs in one of Java's eight texture orientations.

    Orientations 0-3 are quarter turns; 4-7 mirror the texture first.
    """
    result = copy.deepcopy(base)
    result['format_version'] = '1.21.0'
    model = result['minecraft:geometry'][0]
    model['description']['identifier'] = identifier
    for bone in model['bones']:
        face = bone['name']
        uv = bone['cubes'][0]['uv'][face]
        if orientation >= 4:
            uv['uv'][0] += uv['uv_size'][0]
            uv['uv_size'][0] *= -1
        # A compensated carrier has canonical side winding. Older generated
        # carriers used the opposite side winding without body-yaw compensation.
        compensated = bone.get('rotation') == [0, 180, 0]
        direction = -1 if compensated or face not in _SIDE_FACES else 1
        quarter_turns = ((orientation % 4) * direction) % 4
        if quarter_turns:
            uv['uv_rotation'] = quarter_turns * 90
        else:
            uv.pop('uv_rotation', None)
    return result


def compensate_carrier_geometry(document, orientation=None):
    """Put a generated carrier in world space with outward face winding.

    Bedrock's yaw-zero entity frame turns Blockbench's X/Z axes by 180 degrees.
    The bone turn cancels that frame; exported cube X bounds are reflected to
    cancel the Bedrock geometry import's X reflection. UVs retain Java's world
    face orientation. This can refresh generated geometry without touching art.
    """
    result = copy.deepcopy(document)
    model, = result['minecraft:geometry']
    bones = model['bones']
    if {bone['name'] for bone in bones} != set(FACES) or len(bones) != len(FACES):
        raise ValueError('Expected one generated carrier bone for each world face')
    if all(bone.get('rotation') == [0, 180, 0] for bone in bones):
        return result
    if any(bone.get('rotation') not in (None, [0, 0, 0]) for bone in bones):
        raise ValueError('Unexpected existing carrier bone rotation')
    if orientation is None:
        ending = model['description']['identifier'].rsplit('_', 1)[-1]
        orientation = int(ending) if ending.isdigit() else 0
    if type(orientation) is not int or not 0 <= orientation < 8:
        raise ValueError('Invalid carrier orientation')
    for bone in bones:
        cube, = bone['cubes']
        face = bone['name']
        bone['rotation'] = [0, 180, 0]
        cube['origin'][0] = -cube['origin'][0] - cube['size'][0]
        # Bedrock imports top and bottom UVs flipped, so those start from the far corner.
        if face in _SIDE_FACES:
            cube['uv'][face] = {'uv': [0, 0], 'uv_size': [16, 16]}
        else:
            cube['uv'][face] = {'uv': [16, 16], 'uv_size': [-16, -16]}
    return orient_geometry(result, model['description']['identifier'], orientation)


def model_part_geometry(part, face, identifier, anchor_face=None):
    """Export an authored Java cube face, retaining its pivot, UV and rotations.

    The cube is placed relative to the carrier of anchor_face (by default the face's world face). Its bones
    are a frame that cancels the yaw-zero entity turn, the blockstate turn about the block centre, and the
    world-face bone holding the cube.
    """
    start = list(part['from'])
    end = list(part['to'])
    data = part['faces'][face]
    if not _valid_bounds(start, end):
        raise ValueError('Invalid source model bounds')
    rotation = part.get('rotation', {})
    origin = rotation.get('origin', [8, 8, 8])
    angles = _element_angles(rotation)
    if rotation.get('rescale'):
        _rescale(start, end, origin, angles)
    uv = data.get('uv', [0, 0, 16, 16])
    emitted_uv = _bedrock_face_uv(face, uv, data.get('rotation', 0))
    world_face = anchor_face or data.get('worldFace', face)
    offsets = CARRIER_OFFSETS[world_face]
    minimum = _carrier_point(start, offsets)
    size = [end[axis] - start[axis] for axis in range(3)]
    # Bedrock's geometry import mirrors X; mirrored X bounds, pivots and X/Y turns cancel it.
    cube = {'origin': [-minimum[0] - size[0], minimum[1], minimum[2]], 'size': size, 'uv': {face: emitted_uv}}
    if any(angles):
        pivot = _carrier_point(origin, offsets)
        cube['pivot'] = [-pivot[0], pivot[1], pivot[2]]
        cube['rotation'] = [-angles[0], -angles[1], angles[2]]
    model_rotation = part.get('modelRotation', {})
    centre = _carrier_point([8, 8, 8], offsets)
    bones = [{'name': 'frame', 'pivot': [0, 0, 0], 'rotation': [0, 180, 0]},
             {'name': 'model', 'parent': 'frame', 'pivot': [-centre[0], centre[1], centre[2]],
              'rotation': [model_rotation.get('x', 0), model_rotation.get('y', 0), 0]},
             {'name': world_face, 'parent': 'model', 'pivot': [0, 0, 0], 'cubes': [cube]}]
    if model_rotation.get('uvlock') and (model_rotation.get('x', 0) % 360 or model_rotation.get('y', 0) % 360):
        if any(angles) or uv != [0, 0, 16, 16] or data.get('rotation', 0) % 360:
            raise ValueError('UV lock on cropped or rotated model parts requires a mesh adapter')
        _lock_uv(face, uv, model_rotation, emitted_uv)
    return {'format_version': '1.21.0', 'minecraft:geometry': [{
        'description': {'identifier': identifier, 'texture_width': 16, 'texture_height': 16,
                        'visible_bounds_width': 6, 'visible_bounds_height': 6, 'visible_bounds_offset': [0, 0, 0]},
        'bones': bones}]}


def _valid_bounds(start, end):
    return len(start) == 3 and len(end) == 3 and all(
        isinstance(value, (int, float)) and math.isfinite(value) for value in start + end)


def _element_angles(rotation):
    """An element turns about one named axis, or by separate x, y and z angles."""
    angles = [rotation.get(axis, 0) for axis in ('x', 'y', 'z')]
    if 'axis' in rotation:
        angles[['x', 'y', 'z'].index(rotation['axis'])] = rotation.get('angle', 0)
    return angles


def _rescale(start, end, origin, angles):
    # Java 26.2 scales each source axis by the reciprocal largest component
    # of its rotated unit vector, before applying Rz * Ry * Rx around pivot.
    for axis in range(3):
        unit = [int(other == axis) for other in range(3)]
        scale = 1 / max(abs(component) for component in _rotate_vector(unit, angles))
        start[axis] = origin[axis] + (start[axis] - origin[axis]) * scale
        end[axis] = origin[axis] + (end[axis] - origin[axis]) * scale


def _rotate_vector(vector, angles):
    """Turn a vector by the x, then the y, then the z angle, in degrees."""
    x, y, z = vector
    for axis, angle in enumerate(angles):
        cosine = math.cos(math.radians(angle))
        sine = math.sin(math.radians(angle))
        if axis == 0:
            y, z = cosine * y - sine * z, sine * y + cosine * z
        if axis == 1:
            x, z = cosine * x + sine * z, -sine * x + cosine * z
        if axis == 2:
            x, y = cosine * x - sine * y, sine * x + cosine * y
    return x, y, z


def _bedrock_face_uv(face, uv, uv_rotation):
    """The Bedrock UV of a Java face rectangle [u1, v1, u2, v2]."""
    if len(uv) != 4:
        raise ValueError('Invalid source model UV rectangle')
    u_start, v_start, u_end, v_end = uv
    # Bedrock imports top and bottom UVs flipped, so those start from the far corner.
    if face in ('up', 'down'):
        emitted = {'uv': [u_end, v_end], 'uv_size': [u_start - u_end, v_start - v_end]}
    else:
        emitted = {'uv': [u_start, v_start], 'uv_size': [u_end - u_start, v_end - v_start]}
    if uv_rotation % 360:
        emitted['uv_rotation'] = uv_rotation % 360
    return emitted


def _carrier_point(point, offsets):
    """A Java model point (pixels from the block's corner) relative to the face carrier, before the X mirror."""
    return [point[0] - 8 - offsets[0] * 16, point[1] - 16 - offsets[1] * 16, point[2] - 8 - offsets[2] * 16]


def _lock_uv(face, uv, model_rotation, emitted_uv):
    """Undo the turn a blockstate rotation puts on the face's texture, so it stays aligned to the world."""
    # Imported on use: the Java binding modules import import_java_ctm, which imports this module.
    from java_block_bindings import _rotated_face_coordinates
    _, orientation = _rotated_face_coordinates(face, {'uv': uv}, model_rotation | {'uvlock': False})
    inverse = _INVERSE_ORIENTATION[orientation]
    if inverse >= 4:
        emitted_uv['uv'][0] += emitted_uv['uv_size'][0]
        emitted_uv['uv_size'][0] *= -1
    turns = (-inverse % 4) * 90
    if turns:
        emitted_uv['uv_rotation'] = turns


def build_connected(pack, rule_file, key, root=ROOT, samples=None, artifact_name=None, renderer='vv',
                    block_material_fallback=None, carrier_filter=None, artifact_directory=None):
    """Build the connected-texture add-on for a pack and return the .mcaddon path.

    pack: the resource pack folder holding the rule tiles. rule_file: the rules document. key: the
    lowercase pack key used in entity ids and file names. renderer: 'classic' or 'vv' (only VV carries
    PBR texture sets). block_material_fallback: the MERS value for carrier faces without one.
    carrier_filter: an optional 'point' or 'bilinear' sampler for the carriers. The archive goes to
    artifact_directory (default dist/development), next to connected-<key>-report.json.
    """
    pack = pack.resolve()
    if renderer not in ('classic', 'vv'):
        raise ValueError('Entity carriers support Classic and VV; RTX uses native block materials')
    if not re.fullmatch(_PACK_KEY, key):
        raise ValueError('Use a lowercase pack key')
    document = read_json(rule_file)
    rules = validate(document, pack)
    samples = samples or samples_path(ROOT)
    transparent_overrides, fallback_faces, fallback_rules = transparent_replacements(document, rules, pack, samples)
    model_rules, model_textures, model_blocks, layer_blocks = model_material_rules(document)
    rules += fallback_rules + model_rules
    _add_grass_tints(document, rules, samples, pack)
    particle_path = samples / _CARRIER_PARTICLE
    if not particle_path.is_file():
        raise ValueError('Set --samples to Mojang bedrock-samples for the passive carrier particle override')
    staging = root / 'build' / ('connected-' + key)
    # Each build has its own generation directory; existing builds stay intact.
    staging.mkdir(parents=True, exist_ok=True)
    generation = Path(tempfile.mkdtemp(prefix='generation-', dir=staging))
    bp = generation / 'Connected_BP'
    rp = generation / 'Connected_RP'
    _write_manifests(bp, rp, key, renderer, root)
    _write_carrier_particle(particle_path, rp)
    if transparent_overrides:
        _override_native_blocks(rp, transparent_overrides)
        _register_owned_texture(rp)
    tile_textures = _TileTextures(pack, rp, renderer)
    orientations = _orientation_count(document)
    for rule in rules:
        _emit_rule_carrier(rule, key, bp, rp, tile_textures, orientations)
    palette_report = apply_tint_palettes(rp, rules, rule_tint_palettes(document, rules))
    part_rules, part_blocks = _emit_model_part_carriers(document, rules, model_textures, bp, rp, key)
    rules += part_rules
    _blank_part_only_blocks(rp, part_blocks, document, samples)
    write_json(staging / 'tint-palettes.json', palette_report)
    render_settings = _finish_carrier_materials(rp, block_material_fallback, carrier_filter)
    write_json(staging / 'carrier-render-settings.json', render_settings)
    configuration = _engine_configuration(document, rules, part_blocks, fallback_faces, model_textures,
                                          model_blocks, layer_blocks)
    write_source_scripts(bp, [packet('connected', key, configuration)])
    destination = Path(artifact_directory) if artifact_directory else root / 'dist/development'
    archive = _write_archive(generation, destination, artifact_name or f'connected-{key}.mcaddon')
    write_json(destination / f'connected-{key}-report.json', {
        'archive': str(archive),
        'revision': _REPORT_REVISION,
        'geometry_revision': _GEOMETRY_REVISION,
        'carrier_yaw': 0,
        'rules': len(rules) - len(fallback_rules) - len(model_rules) - len(part_rules),
        'render_rules': len(rules),
        'generation': str(generation),
        'carrier_color_filter': render_settings['color_filter'],
        'block_material_fallbacks': render_settings['block_fallbacks'],
        'carrier_texture_atlases': render_settings['texture_atlases'],
        'tinted_albedo_variants': palette_report['unique_tinted_materials'],
        'tint_palette_report': str(staging / 'tint-palettes.json'),
        'model_render_blocks': model_blocks,
        'model_layer_blocks': layer_blocks,
        'model_texture_materials': len(model_rules),
        'model_part_blocks': part_blocks,
        'model_part_renderers': len(part_rules),
        'overlay_rules': sum(rule['method'].startswith('overlay') for rule in rules),
        'graphics_mode': renderer,
        'transparent_owned_faces': fallback_faces,
        'fallback_materials': len(fallback_rules),
        **_binding_report(document, rules),
        **_carrier_facts(tile_textures)})
    return archive


def _add_grass_tints(document, rules, samples, pack):
    """Resolve biome grass colours when a rule tints by grass and the document brings none."""
    tints_by_grass = any(rule.get('tintBlock') in ('minecraft:grass_block', 'minecraft:grass')
                         and rule.get('tintIndex', -1) >= 0 for rule in rules)
    if tints_by_grass and not document.get('grassTints'):
        document['grassTints'] = resolve_colors(samples, pack)


def _write_manifests(bp, rp, key, renderer, root):
    """Manifests and pack icons of the behavior and resource packs."""
    for folder, kind in ((bp, 'bp'), (rp, 'rp')):
        data = manifest(kind, 'connected-' + key, f'{key} connected textures ({renderer.upper()})', [0, 1, 0])
        # Classic has no PBR, so its resource pack does not ask for it.
        if kind == 'rp' and renderer == 'classic':
            data.pop('capabilities', None)
        data['header']['description'] = ('Connected textures, repeat patterns, random variants and overlays.'
                                         ' Needs the Bedrock Connected Textures engine.')
        write_json(folder / 'manifest.json', data)
        shutil.copyfile(root / 'converter/data/artwork/pack-icon.png', folder / 'pack_icon.png')


def _write_carrier_particle(particle_path, rp):
    """Silence the lingering-potion particles of carriers only.

    Carriers are area-effect clouds saved with radius 0.5 and particle colour 0 (see carrier_structure);
    real lingering potions keep their particles.
    """
    particle = read_json(particle_path)
    rate = particle['particle_effect']['components']['minecraft:emitter_rate_instant']
    rate['num_particles'] = ('(variable.cloud_radius == 0.5 && variable.color.r == 0 && variable.color.g == 0'
                             ' && variable.color.b == 0) ? 0 : (' + rate['num_particles'] + ')')
    write_json(rp / 'particles/mobspell_lingering.json', particle)


def _override_native_blocks(rp, entries):
    """Merge blocks.json entries into the carrier pack's blocks.json."""
    path = rp / 'blocks.json'
    document = read_json(path) if path.exists() else {'format_version': BLOCKS_FORMAT}
    document.update(entries)
    write_json(path, document)


def _register_owned_texture(rp):
    """Add the blank 16x16 texture owned native faces point at to the terrain atlas."""
    image = rp / f'textures/blocks/{_OWNED_TEXTURE}.png'
    image.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGBA', (16, 16)).save(image)
    path = rp / 'textures/terrain_texture.json'
    atlas = read_json(path) if path.exists() else {
        'resource_pack_name': 'bct_owned_faces', 'texture_name': 'atlas.terrain', 'padding': 8,
        'num_mip_levels': 4, 'texture_data': {}}
    atlas['texture_data'][_OWNED_TEXTURE] = {'textures': 'textures/blocks/' + _OWNED_TEXTURE}
    write_json(path, atlas)


def _orientation_count(document):
    """Eight geometries, one per Java texture orientation, when any face, model choice or layer is turned."""
    choices = _document_choices(document)
    turned_faces = any(value for faces in document.get('textureOrientations', {}).values() for value in faces.values())
    turned_choices = any(value for choice in choices for value in choice.get('orientations', {}).values())
    turned_layers = any(layer.get('orientation', 0) for choice in choices
                        for layers in choice.get('faceLayers', {}).values() for layer in layers)
    return 8 if turned_faces or turned_choices or turned_layers else 1


def _carrier_names(key, rule_id):
    """Entity id and file stem of the carrier that draws a rule."""
    entity = 'bct:' + key.replace('-', '_') + '_' + rule_id
    return entity, entity.replace(':', '_', 1)


def _emit_rule_carrier(rule, key, bp, rp, tile_textures, orientations):
    """Write one rule's carrier: spawn structure, entity, tile textures, geometry, client entity and controller."""
    material = _entity_material(rule, tile_textures.pack)
    identifier, stem = _carrier_names(key, rule['id'])
    rule['entity'] = identifier
    rule['spawn_structure'] = 'bct:' + stem
    structure = bp / 'structures/bct' / (stem + '.mcstructure')
    structure.parent.mkdir(parents=True, exist_ok=True)
    structure.write_bytes(cloud_structure(identifier))
    textures = tile_textures.export_rule(rule, stem)
    entity_definition = server_entity(identifier, passive=True)
    entity_definition['minecraft:entity']['description']['properties']['bct:orientation'] = {
        'type': 'int', 'range': [0, 7], 'default': 0, 'client_sync': True}
    write_json(bp / f'entities/{stem}.json', entity_definition)
    geometries = _write_oriented_geometry(rp, stem, orientations)
    flipbooks = tile_textures.flipbooks_of(rule)
    if flipbooks:
        write_carrier_uv_materials(rp)
    controller = 'controller.render.' + stem
    write_json(rp / f'entity/{stem}.entity.json', {'format_version': '1.21.80', 'minecraft:client_entity': {
        'description': {'identifier': identifier,
                        'materials': {'default': carrier_material(material, bool(flipbooks))},
                        'textures': textures,
                        'geometry': {'default': geometries['g0'], **geometries},
                        'render_controllers': carrier_controllers([controller])}}})
    write_json(rp / f'render_controllers/{stem}.json', {'format_version': '1.8.0', 'render_controllers': {
        controller: _carrier_controller(len(textures), orientations, flipbooks)}})


def _entity_material(rule, pack):
    """Blended for soft alpha, alpha-tested for cut-outs, opaque otherwise."""
    transparent = False
    translucent = rule.get('layer') == 'translucent'
    for tile in rule['tiles']:
        if tile in _TILE_MARKERS:
            continue
        has_transparency, has_partial_alpha = _alpha_kinds(image_path(contained(pack, tile)))
        transparent |= has_transparency
        # Overlay tiles are cut out unless the rule asks for the translucent layer, as in Java.
        if not rule['method'].startswith('overlay'):
            translucent |= has_partial_alpha
    if translucent:
        return 'entity_alphablend'
    if transparent:
        return 'entity_alphatest'
    return 'entity'


class _TileTextures:
    """Copies rule tiles into the carrier pack, once per source image.

    Animated tiles go through the native exporter (frame strip plus a UV flipbook); PBR channel images that
    many tiles share are stored once. Classic packs get colour only.
    """

    def __init__(self, pack, resource_pack, renderer):
        self.pack = pack
        self.resource_pack = resource_pack
        self.renderer = renderer
        self.exported = {}
        self.flipbooks = {}
        self.channels = {}

    def export_rule(self, rule, stem):
        """The rule's texture slots t0, t1, ... in tile order."""
        textures = {}
        for index, tile in enumerate(rule['tiles']):
            # Marker slots are never drawn, but tile indices must line up, so a real tile fills the slot.
            if tile in _TILE_MARKERS:
                tile = _first_material_tile(rule)
            source = image_path(contained(self.pack, tile))
            if source not in self.exported:
                self.exported[source] = self._export(source, tile, f'{stem}_{index}')
            textures[f't{index}'] = self.exported[source]
        return textures

    def flipbooks_of(self, rule):
        """The UV flipbook of each animated tile slot, by slot index."""
        flipbooks = {}
        for index, tile in enumerate(rule['tiles']):
            if tile in _TILE_MARKERS:
                continue
            flipbook = self.flipbooks.get(image_path(contained(self.pack, tile)))
            if flipbook is not None:
                flipbooks[index] = flipbook
        return flipbooks

    def _export(self, source, tile, name):
        if has_animation(self.pack, source):
            relative, flipbook, _ = export_material(self.pack, tile, self.resource_pack, name,
                                                    texture_folder='textures/entity', renderer=self.renderer)
            self.flipbooks[source] = flipbook
            return relative
        relative = 'textures/entity/' + name
        destination = self.resource_pack / (relative + '.png')
        destination.parent.mkdir(parents=True, exist_ok=True)
        color_size = _copy_image(source, destination)
        descriptor = source.with_suffix('.texture_set.json')
        if descriptor.exists() and self.renderer != 'classic':
            self._export_texture_set(descriptor, source, destination, color_size)
        return relative

    def _export_texture_set(self, descriptor, source, destination, color_size):
        channels = read_json(descriptor)['minecraft:texture_set'].copy()
        channels['color'] = destination.stem
        for channel, value in list(channels.items()):
            if channel == 'color' or not isinstance(value, str):
                continue
            channels[channel] = self._shared_channel(channel, value, source, destination.parent, color_size)
        write_json(destination.with_suffix('.texture_set.json'),
                   {'format_version': '1.21.30', 'minecraft:texture_set': channels})

    def _shared_channel(self, channel, value, source, folder, color_size):
        """Store a PBR channel image once per distinct content and return its texture name."""
        if value.startswith('textures/'):
            base = contained(self.pack, value)
        else:
            base = contained(self.pack, source.parent.relative_to(self.pack) / value)
        channel_source = image_path(base)
        if channel_source is None:
            raise ValueError('Missing PBR channel: ' + value)
        digest = hashlib.sha256(channel_source.read_bytes()).hexdigest()
        shared = self.channels.get((channel, digest))
        name = shared or f'bct_shared_{channel}_{digest[:24]}'
        with Image.open(channel_source) as image:
            if image.size != color_size:
                raise ValueError('PBR channels must match tile dimensions')
            if shared is None:
                if channel_source.suffix.lower() == '.png':
                    shutil.copyfile(channel_source, folder / (name + '.png'))
                else:
                    image.save(folder / (name + '.png'))
                self.channels[channel, digest] = name
        return name


def _first_material_tile(rule):
    material = next((tile for tile in rule['tiles'] if tile not in _TILE_MARKERS), None)
    if material is None:
        raise ValueError('At least one material tile is required per renderer rule')
    return material


def _copy_image(source, destination):
    """Copy a tile as PNG and return its size; PNGs are copied byte for byte, other formats saved as RGBA."""
    with Image.open(source) as image:
        size = image.size
        if source.suffix.lower() != '.png':
            image.convert('RGBA').save(destination)
    if source.suffix.lower() == '.png':
        shutil.copyfile(source, destination)
    return size


def _write_oriented_geometry(rp, stem, orientations):
    """Write the carrier geometry once per texture orientation; returns geometry ids by name (g0, g1, ...)."""
    base = _carrier_geometry('geometry.' + stem)
    geometries = {}
    for orientation in range(orientations):
        geometry_id = 'geometry.' + stem + '_' + str(orientation)
        suffix = '_' + str(orientation) if orientation else ''
        write_json(rp / f'models/entity/{stem}{suffix}.geo.json', orient_geometry(base, geometry_id, orientation))
        geometries['g' + str(orientation)] = geometry_id
    return geometries


def _carrier_geometry(identifier):
    """Six face planes just outside the block, in world space with outward winding."""
    document = face_plane_geometry(identifier)
    model = document['minecraft:geometry'][0]
    for bone in model['bones']:
        # Start uncompensated: compensate_carrier_geometry mirrors the bounds and adds the half turn.
        bone.pop('rotation', None)
        cube = bone['cubes'][0]
        _place_face_plane(bone['name'], cube)
        cube['origin'] = [value - CARRIER_OFFSETS[bone['name']][axis] * 16 for axis, value in enumerate(cube['origin'])]
    model['description'].update(visible_bounds_width=3, visible_bounds_height=3, visible_bounds_offset=[0, 0, 0])
    return compensate_carrier_geometry(document, 0)


def _place_face_plane(face, cube):
    """Size the face's cube (pixels, block-centred) to a 0.001-thick plane over the whole face, outside it."""
    if face in ('north', 'south'):
        cube['origin'][0] = -8
        cube['size'][0] = 16
        cube['size'][2] = .001
    if face in ('east', 'west'):
        cube['origin'][2] = -8
        cube['size'][2] = 16
        cube['size'][0] = .001
    if face == 'north':
        cube['origin'][2] = -8 - SIDE_CLEARANCE * 16
    if face == 'south':
        cube['origin'][2] = 8 + SIDE_CLEARANCE * 16 - .001
    if face == 'west':
        cube['origin'][0] = -8 - SIDE_CLEARANCE * 16
    if face == 'east':
        cube['origin'][0] = 8 + SIDE_CLEARANCE * 16 - .001
    if face == 'down':
        cube['size'][1] = .001
        cube['origin'][1] = -16 - SIDE_CLEARANCE * 16
    if face == 'up':
        cube['origin'][1] = TOP_CLEARANCE * 16 - .001


def _carrier_controller(texture_count, orientations, flipbooks):
    """Render controller: the engine's face, tile, orientation and tint properties choose what shows."""
    arrays = {'textures': {'Array.tiles': [f'Texture.t{index}' for index in range(texture_count)]}}
    if orientations > 1:
        arrays['geometries'] = {'Array.orientations': [f'Geometry.g{index}' for index in range(orientations)]}
    return {
        'geometry': "Array.orientations[q.property('bct:orientation')]" if orientations > 1 else 'Geometry.default',
        'materials': [{'*': 'Material.default'}],
        'color': {**{channel: f"q.property('bct:tint_{channel}')" for channel in ('r', 'g', 'b')}, 'a': 1},
        **_uv_animation(flipbooks),
        'arrays': arrays,
        'textures': ["Array.tiles[q.property('bct:tile')]"],
        'part_visibility': [{face: f"q.property('bct:active') && q.property('bct:face') == {index}"}
                            for index, face in enumerate(FACES)]}


def _uv_animation(flipbooks):
    """UV animation keyed on the shown tile, so each animated tile steps through its own frames."""
    if not flipbooks:
        return {}
    offset = ['0.0', '0.0']
    scale = ['1.0', '1.0']
    for index, flipbook in reversed(list(flipbooks.items())):
        values = uv_animation_xy(flipbook)
        for axis in range(2):
            offset[axis] = f"(q.property('bct:tile') == {index} ? {values['offset'][axis]} : {offset[axis]})"
            scale[axis] = f"(q.property('bct:tile') == {index} ? {values['scale'][axis]} : {scale[axis]})"
    return {'uv_anim': {'offset': offset, 'scale': scale}}


def _emit_model_part_carriers(document, rules, model_textures, bp, rp, key):
    """Carriers shaped like the author's model parts, sharing each resolved rule's artwork.

    Records the carriers on the document's model choices for the engine (modelRenderGroups on each choice,
    renderers and fallbackRule on each face). Returns the new rules and the blocks drawn by model parts.
    """
    carriers = _ModelPartCarriers(rules, model_textures, key, bp, rp)
    java_names = document.get('nativeBlockJavaIds', {})
    blocks = set()
    for block, variants in document.get('baseTextureVariants', {}).items():
        for variant in variants:
            for choice in _variant_choices(variant):
                java_block = choice.get('javaBlock', variant.get('javaBlock', java_names.get(block, block)))
                if carriers.emit_choice(choice, block, java_block):
                    blocks.add(block)
    return list(carriers.clones.values()), sorted(blocks)


class _ModelPartCarriers:
    """Clones resolved rule carriers into carriers shaped like model-part faces.

    Faces no authored rule can reach are batched per texture and tint into one group carrier; the engine
    hides each of its faces whose cullface neighbour covers it (bct:cull_mask). Every other face gets one
    carrier per rule that can draw it.
    """

    def __init__(self, rules, model_textures, key, bp, rp):
        self.rules_by_id = {rule['id']: rule for rule in rules}
        self.authored = [rule for rule in rules if not rule.get('fallback')]
        self.model_textures = model_textures
        self.key = key
        self.bp = bp
        self.rp = rp
        self.clones = {}

    def emit_choice(self, choice, block, java_block):
        """Emit the carriers of one model choice; returns whether any face got one."""
        groups = self._fallback_groups(choice, block, java_block)
        choice['modelRenderGroups'] = []
        drawn = False
        for (texture, tint), faces in groups.items():
            self._emit_group(choice, texture, tint, faces)
            drawn = True
        for part in choice.get('modelParts', []):
            for source_face, data in part.get('faces', {}).items():
                if data.get('bundled'):
                    continue
                self._emit_face(part, source_face, data, block, java_block)
                drawn = True
        return drawn

    def _fallback_groups(self, choice, block, java_block):
        """The choice's faces that only their fallback material draws, by (texture, tint index)."""
        groups = {}
        for part in choice.get('modelParts', []):
            for face, data in part.get('faces', {}).items():
                texture = data['texture']
                world_face = data.get('worldFace', face)
                drawn_by_rule = any(_covers(rule, world_face, block, java_block)
                                    and (not rule.get('matchTiles') or texture in rule['matchTiles'])
                                    for rule in self.authored)
                if not drawn_by_rule:
                    groups.setdefault((texture, data.get('tintIndex', -1)), []).append((part, face, data))
        return groups

    def _emit_group(self, choice, texture, tint, faces):
        """One carrier for a choice's faces that share a texture and tint and only the fallback draws."""
        fallback = self.model_textures[texture]
        anchor = faces[0][2].get('worldFace', faces[0][1])
        records = [_face_record(part, face, data, _GROUP_SIGNATURE_SKIPS) for part, face, data in faces]
        clone_id = fallback + '_group_' + _signature(records)
        choice['modelRenderGroups'].append({
            'texture': texture, 'tintIndex': tint, 'worldFace': anchor, 'rule': clone_id,
            'cullFaces': sorted({data['cullface'] for _, _, data in faces if data.get('cullface')})})
        for _, _, data in faces:
            data['bundled'] = True
        if clone_id not in self.clones:
            _, stem = _carrier_names(self.key, clone_id)
            geometry_document, visibility = _grouped_model_geometry(faces, 'geometry.' + stem, anchor)
            self.clones[clone_id] = self._clone(fallback, clone_id, geometry_document, visibility, grouped=True)

    def _emit_face(self, part, source_face, data, block, java_block):
        """One carrier per rule that can draw this face, each shaped like the face."""
        world_face = data.setdefault('worldFace', source_face)
        # A face that keeps its source face's name takes the world face the blockstate turn gives it.
        if world_face == source_face:
            world_face = _turned_face(source_face, part.get('modelRotation', {}))
            data['worldFace'] = world_face
        texture = data['texture']
        fallback = self.model_textures[texture]
        candidates = {fallback}
        for rule in _rules_reaching(self.authored, texture, world_face, block, java_block):
            candidates.add(rule['id'])
        signature = _signature(_face_record(part, source_face, data, _FACE_SIGNATURE_SKIPS))
        data['fallbackRule'] = fallback
        data['renderers'] = {}
        for rule_id in sorted(candidates):
            clone_id = rule_id + '_part_' + signature
            data['renderers'][rule_id] = clone_id
            if clone_id in self.clones:
                continue
            _, stem = _carrier_names(self.key, clone_id)
            geometry_document = model_part_geometry(part, source_face, 'geometry.' + stem)
            visibility = [{world_face: "q.property('bct:active')"}]
            self.clones[clone_id] = self._clone(rule_id, clone_id, geometry_document, visibility, grouped=False)

    def _clone(self, base_id, clone_id, geometry_document, part_visibility, grouped):
        """Copy a rule's carrier under a new id with its own geometry; textures and tint stay shared."""
        base_rule = self.rules_by_id[base_id]
        rule = copy.deepcopy(base_rule)
        flags = {'modelGroup': True} if grouped else {}
        rule.update(id=clone_id, fallback=True, modelPart=True, **flags)
        entity, stem = _carrier_names(self.key, clone_id)
        base_stem = base_rule['entity'].replace(':', '_', 1)
        rule.update(entity=entity, spawn_structure='bct:' + stem)
        definition = read_json(self.bp / f'entities/{base_stem}.json')
        description = definition['minecraft:entity']['description']
        description['identifier'] = entity
        if grouped:
            description['properties']['bct:cull_mask'] = {'type': 'int', 'range': [0, 63], 'default': 0,
                                                          'client_sync': True}
        write_json(self.bp / f'entities/{stem}.json', definition)
        (self.bp / f'structures/bct/{stem}.mcstructure').write_bytes(cloud_structure(entity))
        geometry_id = geometry_document['minecraft:geometry'][0]['description']['identifier']
        write_json(self.rp / f'models/entity/{stem}.geo.json', geometry_document)
        client = read_json(self.rp / f'entity/{base_stem}.entity.json')
        client['minecraft:client_entity']['description'].update(
            identifier=entity, geometry={'default': geometry_id},
            render_controllers=carrier_controllers(['controller.render.' + stem]))
        write_json(self.rp / f'entity/{stem}.entity.json', client)
        base_controllers = read_json(self.rp / f'render_controllers/{base_stem}.json')['render_controllers']
        controller = next(iter(base_controllers.values()))
        # The clone has one shape, so it draws its single geometry instead of the orientation array.
        controller['geometry'] = 'Geometry.default'
        controller.get('arrays', {}).pop('geometries', None)
        controller['part_visibility'] = part_visibility
        write_json(self.rp / f'render_controllers/{stem}.json',
                   {'format_version': '1.8.0', 'render_controllers': {'controller.render.' + stem: controller}})
        return rule


def _turned_face(source_face, model_rotation):
    """The world face a source face ends up on after the blockstate's x, then y quarter turns."""
    x, y, z = _FACE_VECTORS[source_face]
    # Blockstate turns use Java's negative X/Y convention.
    for _ in range((model_rotation.get('x', 0) % 360) // 90):
        x, y, z = x, z, -y
    for _ in range((model_rotation.get('y', 0) % 360) // 90):
        x, y, z = -z, y, x
    return next(name for name, vector in _FACE_VECTORS.items() if vector == (x, y, z))


def _face_record(part, face, data, skipped):
    """What makes two part faces the same carrier: the part without its faces, the face and its data."""
    return {'part': {name: value for name, value in part.items() if name != 'faces'}, 'face': face,
            'data': {name: value for name, value in data.items() if name not in skipped}}


def _signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def _grouped_model_geometry(faces, identifier, anchor_face):
    """One geometry holding every face of a group, plus a visibility entry per face honouring its cullface."""
    bones = []
    visibility = []
    for index, (part, face, data) in enumerate(faces):
        model = model_part_geometry(part, face, identifier, anchor_face)['minecraft:geometry'][0]
        renamed = {bone['name']: bone['name'] + '_' + str(index) for bone in model['bones']}
        for bone in model['bones']:
            bone['name'] = renamed[bone['name']]
            if 'parent' in bone:
                bone['parent'] = renamed[bone['parent']]
            bones.append(bone)
        visibility.append({renamed[anchor_face]: _cull_condition(data.get('cullface'))})
    model['bones'] = bones
    return {'format_version': '1.21.0', 'minecraft:geometry': [model]}, visibility


def _cull_condition(cullface):
    """Shown while active, unless the engine set the bct:cull_mask bit of the face's cullface."""
    condition = "q.property('bct:active')"
    if cullface:
        condition += f" && math.mod(math.floor(q.property('bct:cull_mask')/{2 ** FACES.index(cullface)}),2) == 0"
    return condition


def _blank_part_only_blocks(rp, part_blocks, document, samples):
    """Blank the native look of blocks only model parts draw (plants, paths), so the author's model shows alone."""
    hidden = set(part_blocks) - set(document.get('fullCubeBlocks', []))
    if not hidden:
        return
    native_blocks = read_json(samples / 'resource_pack/blocks.json')
    entries = {}
    for block in sorted(hidden):
        name = block.removeprefix('minecraft:')
        if name not in native_blocks:
            legacy = _legacy_block_name(name, native_blocks)
            if legacy is not None:
                name = legacy
        if name not in native_blocks:
            raise ValueError('Missing native definition for model-part replacement: ' + block)
        entry = copy.deepcopy(native_blocks[name])
        entry['textures'] = _OWNED_TEXTURE
        entries[name] = entry
    _override_native_blocks(rp, entries)
    _register_owned_texture(rp)


def _finish_carrier_materials(rp, block_material_fallback, carrier_filter):
    """Give the finished carriers their block PBR fallback and optional sampler, then pack their atlases."""
    block_fallbacks = bind_block_fallbacks(rp, block_material_fallback or list(_DEFAULT_BLOCK_MERS))
    color_filter = None
    if carrier_filter:
        color_filter = apply_carrier_filter(rp, carrier_filter)
    # Atlas pages are built from the carriers' final materials and textures, so they come last.
    texture_atlases = consolidate_carrier_atlases(rp)
    return {'block_fallbacks': block_fallbacks, 'color_filter': color_filter, 'texture_atlases': texture_atlases}


def _engine_configuration(document, rules, part_blocks, fallback_faces, model_textures, model_blocks,
                          layer_blocks):
    """The data packet the engine reads: the document's block and tint data plus every carrier rule."""
    configuration = {field: document.get(field, [] if field in _LIST_FIELDS else {})
                     for field in _CONFIGURATION_FIELDS}
    # Packs without a dialect follow OptiFine's tile rules.
    configuration.update(rules=rules, dialect=document.get('dialect', 'optifine'),
                         carrierGeometryRevision=_GEOMETRY_REVISION, modelPartBlocks=part_blocks,
                         fallbackFaces=fallback_faces, modelTextureRules=model_textures,
                         modelRenderBlocks=model_blocks)
    configuration['sourceBlocks'] = sorted(set(configuration['sourceBlocks']) | set(model_blocks) | set(layer_blocks))
    return configuration


def _write_archive(generation, destination, artifact_name):
    """Zip the generation folder into the .mcaddon, members in path order."""
    destination.mkdir(parents=True, exist_ok=True)
    if Path(artifact_name).name != artifact_name or not artifact_name.endswith('.mcaddon'):
        raise ValueError('Use a simple .mcaddon output filename')
    archive = destination / artifact_name
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as output:
        for path in sorted(generation.rglob('*')):
            if path.is_file():
                output.write(path, path.relative_to(generation).as_posix())
    return archive


def _binding_report(document, rules):
    """Which source blocks the carriers draw, and which still lack a face orientation."""
    cubes = set(document.get('fullCubeBlocks', []))
    sources = set(document.get('sourceBlocks', [])) | {block for rule in rules for block in rule['blocks']}
    _, _, model_blocks, layer_blocks = model_material_rules(document)
    sources.update(model_blocks)
    sources.update(layer_blocks)
    variants = document.get('baseTextureVariants', {})
    orientations = document.get('textureOrientations', {})
    parts = {block for block, entries in variants.items()
             if any(_part_faces(choice) for entry in entries for choice in _variant_choices(entry))}
    sources.update(parts)
    rendered = (sources & cubes) | parts if cubes else sources
    configured = bool(variants or orientations)
    unresolved = []
    for block in sorted(rendered):
        if block in parts and block not in cubes:
            continue
        if configured and not _orientations_bound(block, variants, orientations):
            unresolved.append(block)
    return {'rendered_source_blocks': sorted(rendered),
            'outside_cube_renderer': sorted(sources - cubes) if cubes else [],
            'outside_carrier_renderer': sorted(sources - rendered),
            'authored_model_part_blocks': sorted(parts),
            'state_texture_blocks': len(variants),
            'state_texture_variants': sum(len(value) for value in variants.values()),
            'weighted_model_variants': sum('modelChoices' in entry
                                           for entries in variants.values() for entry in entries),
            'unresolved_orientation_blocks': unresolved,
            'unresolved_author_models': document.get('unresolvedTextureOrientations', []),
            'neighborhood_orientation': 'Java DirectionMaps: rotations and reflections before tile selection'}


def _orientations_bound(block, variants, orientations):
    """Whether every face of the block has an orientation, from the block map or from all its model choices."""
    if all(face in orientations.get(block, {}) for face in FACES):
        return True
    choices = [choice for entry in variants.get(block, []) for choice in entry.get('modelChoices', [entry])]
    return bool(choices) and all(all(face in choice.get('orientations', {}) for face in FACES) for choice in choices)


def _carrier_facts(tile_textures):
    """Facts about the carrier renderer that close the build report, with the shared texture counts."""
    return {'masks': MASKS,
            'tile_numbering': 'OptiFine',
            'random_reference': 'Continuity 64-bit position mix',
            'top_clearance_pixels_256': TOP_CLEARANCE * 256,
            'side_clearance_pixels_256': SIDE_CLEARANCE * 256,
            'carrier_offsets': CARRIER_OFFSETS,
            'in_game_tested': False,
            'renderer': 'cube faces and authored model parts in cloud carriers',
            'requires_server_api': '2.10.0',
            'unique_pbr_channels': len(tile_textures.channels),
            'animated_materials': len(tile_textures.flipbooks),
            'animation_clock': 'q.time_stamp; engine units and phase require in-game verification',
            'animation_interpolation': 'integer game ticks; fractional rendering unverified',
            'limitations': list(_LIMITATIONS)}


def refresh_carrier_materials(resource_pack):
    """Update generated carrier shaders without touching textures or geometry."""
    resource_pack = Path(resource_pack).resolve()
    if not resource_pack.is_relative_to((ROOT / 'build').resolve()):
        raise ValueError('Only generated build resource packs can be refreshed')
    manifest_data = read_json(resource_pack / 'manifest.json')
    if not any(module.get('type') == 'resources' for module in manifest_data.get('modules', [])):
        raise ValueError('Carrier shader refresh requires a resource pack')
    uv_aliases = {name.split(':', 1)[0] for name in carrier_uv_materials()}
    carriers_checked = 0
    bindings_changed = 0
    animated = False
    for path in sorted((resource_pack / 'entity').glob('bct_*.entity.json')):
        document = read_json(path)
        description = document.get('minecraft:client_entity', {}).get('description', {})
        if not description.get('identifier', '').startswith('bct:'):
            continue
        carriers_checked += 1
        materials = description.get('materials', {})
        material = materials.get('default')
        animated |= material in uv_aliases
        plain = _PLAIN_MATERIALS.get(material, material)
        if material != plain:
            materials['default'] = plain
            write_json(path, document)
            bindings_changed += 1
    uv_material_file_changed = False
    if animated:
        uv_material_file_changed = write_carrier_uv_materials(resource_pack)['changed']
    return {'resource_pack': str(resource_pack), 'carrier_entities_checked': carriers_checked,
            'client_bindings_changed': bindings_changed, 'uv_material_file_changed': uv_material_file_changed,
            'textures_modified': False, 'geometry_modified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--resource-pack', type=Path, required=True, help='folder holding the rule tiles')
    parser.add_argument('--rules', type=Path, required=True, help='rules document (JSON)')
    parser.add_argument('--key', required=True, help='lowercase pack key, used in entity ids and file names')
    parser.add_argument('--samples', type=Path, help='Mojang bedrock-samples checkout')
    args = parser.parse_args()
    print(build_connected(args.resource_pack.resolve(), args.rules, args.key, samples=args.samples))
