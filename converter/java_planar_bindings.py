"""Preserve Java model parts and independent weighted multipart selections.

java_block_bindings binds full-cube faces as textures; this module keeps the
rest of a Java block model as model parts with their exact bounds, rotations,
UVs and tint indices, and never replaces a crossed or raised plane with a
cube. Each matching multipart group keeps its own weighted random choice, as
Java picks one per group. Java states Bedrock lacks become derived engine
states (bct:leaf_distance for leaves, which is not Bedrock's update_bit).
"""
from collections import defaultdict
import copy
from functools import lru_cache
import math
from pathlib import PurePosixPath
import re

from block_ids import bedrock_ids, known_blocks
from common import samples_path
from java_block_bindings import (BLOCK_ALIASES, FACES, FACE_NORMALS, MAX_TOTAL_WEIGHT, MULTIPART_SELECTION,
                                 VARIANT_SELECTION, _as_list, _cube_element, _cube_surface_layer, _face_texture,
                                 _json, _multipart_matches, _resource, _rotate_vector, _rotated_cube_faces,
                                 _variant_states)
from java_block_geometry import default_uv, shaded
from java_block_states import native_block_java_ids, state_reference, states_java_to_bedrock
from java_missing_sprite import MISSING_MODEL_SPRITE

DOUBLE_PLANTS = {'tall_grass', 'large_fern', 'sunflower', 'lilac', 'rose_bush', 'peony'}
FOLIAGE_NAMES = {'short_grass', 'tall_grass', 'fern', 'large_fern', 'bush', 'dead_bush',
                 'azalea', 'flowering_azalea', 'short_dry_grass', 'tall_dry_grass', 'seagrass', 'tall_seagrass'}
# Java's leaf decay distance runs from 1 to 7.
LEAF_DISTANCES = range(1, 8)
BLOCKSTATE_PATH = re.compile(r'assets/([^/]+)/blockstates/(.+)\.json')
# A block without a cube keeps these binding fields empty.
NO_CUBE_FIELDS = ('faces', 'orientations', 'tintIndices', 'faceLayers')


def is_foliage(name):
    """Plants and leaves: blocks whose Java models are checked for geometry even when the pack has no blockstate."""
    name = name.removeprefix('minecraft:')
    return name in FOLIAGE_NAMES or name.endswith(('_leaves', '_sapling', '_bush'))


def ctm_geometry_candidates(models):
    """Find selected block models whose actual faces reference a CTM matcher.

    Returns (block ids, issues): the blocks a CTM rule names (matchBlocks) and
    the blocks whose selected models draw a face with a texture a CTM rule
    matches (matchTiles).
    """
    if models.stack is None:
        return set(), []
    targets, textures, issues = _ctm_matches(models.stack)
    for blockstate in sorted(models.names | set(models.stack.files)):
        match = BLOCKSTATE_PATH.fullmatch(blockstate)
        if not match:
            continue
        identifier = match[1] + ':' + match[2]
        try:
            for reference in _model_references(models.read(blockstate)):
                path = _resource(reference, 'models')
                model, variables = models.resolve(path), models.variables(path)
                if any(_face_texture(variables, face) in textures
                       for element in model.get('elements', [])
                       for face in element.get('faces', {}).values()):
                    targets.add(identifier)
                    break
        except (KeyError, ValueError) as error:
            # Unrelated unsupported models must not prevent other CTM targets.
            if identifier in targets:
                issues.append({'block': identifier, 'reason': str(error), 'geometry_preserved': False})
    return targets, issues


def authored_geometry_selection(models, blockstate):
    """Whether the author changed the geometry a blockstate shows.

    True for the pack's own blockstate, or when a selected model (or one of its
    parents) is the pack's and its geometry differs from vanilla. Untouched
    vanilla geometry belongs to native rendering.
    """
    if models.stack is None:
        return False
    if blockstate in models.stack.files:
        return True
    return _selects_authored_model(models, models.read(blockstate))


def model_geometry_variants(models, blockstate, identifier):
    """Retain separate RNG selections for every matching multipart group.

    Returns ({Bedrock block: [variant entry]}, issues). An entry holds the
    Bedrock states, the Java block, and either the cube binding of
    java_block_bindings with the extra model parts, or weighted modelChoices,
    plus modelPartsGroups for independent multipart groups.
    """
    variants, issues = defaultdict(list), []
    try:
        selections = _selections(models, blockstate, identifier)
    except (KeyError, ValueError) as error:
        return {}, [{'block': identifier, 'reason': str(error), 'geometry_preserved': False}]
    for states, groups, algorithm in selections:
        try:
            targets = _bedrock_selections(identifier, states)
            binding = _state_binding(models, groups, algorithm)
            for target in targets:
                variants[target['block']].append({'states': target['states'], 'javaBlock': identifier,
                                                  **copy.deepcopy(binding)})
            if MISSING_MODEL_SPRITE in _part_textures({identifier: [binding]}):
                issues.append({'block': identifier, 'java_states': states,
                               'reason': 'author_model_has_unresolved_face_slot_java_missing_sprite_preserved',
                               'geometry_preserved': True})
        except (KeyError, ValueError) as error:
            issues.append({'block': identifier, 'java_states': states, 'reason': str(error),
                           'geometry_preserved': False})
    return dict(variants), issues


def model_parts(models, selection, *, extras_only=False):
    """Return actual parts; never replace a crossed/raised plane with a cube.

    One part per model element with a visible face: its bounds, rotation and,
    per face, the sprite, UV, UV rotation, tint index and the world face it
    shows after the blockstate rotation. extras_only leaves out the cube
    element and faces lying on the cube's surface, which the cube binding
    already draws as faces and face layers.
    """
    path = _resource(selection['model'], 'models')
    model = models.resolve(path)
    variables = models.variables(path)
    x, y = selection.get('x', 0), selection.get('y', 0)
    if any(type(angle) is not int or angle % 90 for angle in (x, y)):
        raise ValueError('Model rotation is not a quarter turn')
    uv_locked = selection.get('uvlock') and (x % 360 or y % 360)
    cube = _cube_element(model) if extras_only else None
    parts = []
    for index, element in enumerate(model.get('elements', [])):
        if element is cube:
            continue
        start = _numbers(element.get('from'), 3, 'element bounds')
        end = _numbers(element.get('to'), 3, 'element bounds')
        part = {'from': start, 'to': end, 'faces': {}, 'sourceModel': path,
                'elementIndex': index, 'shade': shaded(element),
                'modelRotation': {'x': x % 360, 'y': y % 360, 'uvlock': bool(selection.get('uvlock', False))}}
        if 'rotation' in element:
            part['rotation'] = _checked_rotation(element['rotation'])
        if 'light_emission' in element:
            part['lightEmission'] = element['light_emission']
        for face, data in element.get('faces', {}).items():
            if face not in FACES:
                raise ValueError('Unknown Java model face: ' + face)
            if extras_only and _cube_surface_layer(element, face):
                # Existing canonical faceLayers already render this face.
                continue
            uv = _numbers(data.get('uv', default_uv(face, start, end)), 4, 'face UV')
            if not _has_visible_area(face, uv, start, end):
                # Zero-area faces are authoring helpers, often #missing, and Java
                # emits no visible area. Ignore them before resolving their sprite.
                continue
            texture = _face_texture(variables, data)
            if texture is None:
                if not data.get('texture', '').startswith('#'):
                    raise ValueError('Unresolved visible Java model sprite: ' + path + '#' + data.get('texture', ''))
                # Java draws its missing sprite for an unresolved slot; keep it and report the slot.
                texture = MISSING_MODEL_SPRITE
                part.setdefault('sourceMissingFaces', []).append({'face': face, 'slot': data['texture']})
            uv_rotation = data.get('rotation', 0)
            tint = data.get('tintindex', -1)
            if type(uv_rotation) is not int or uv_rotation % 90 or type(tint) is not int or tint < -1:
                raise ValueError('Invalid Java face rotation or tint index')
            if uv_locked and (element.get('rotation') or uv != [0, 0, 16, 16] or uv_rotation % 360):
                raise ValueError('UV lock on cropped or rotated model parts is unsupported')
            bound = {'texture': texture, 'uv': uv, 'rotation': uv_rotation % 360,
                     'tintIndex': tint, 'worldFace': _world_face(face, x, y)}
            if 'cullface' in data:
                if data['cullface'] not in FACES:
                    raise ValueError('Unknown Java cull face: ' + str(data['cullface']))
                bound['cullface'] = _world_face(data['cullface'], x, y)
            part['faces'][face] = bound
        if part['faces']:
            parts.append(part)
    return parts


def leaf_distance_tags(models):
    """Resolve effective Java leaves/logs tags used by the distance predicate.

    Returns ({'leafDistanceLeaves': blocks, 'leafDistanceLogs': blocks}, issues);
    each list holds the Java tag's blocks and the Bedrock blocks standing for them.
    """
    bedrock_names = defaultdict(set)
    for alias, java in BLOCK_ALIASES.items():
        bedrock_names['minecraft:' + java].add('minecraft:' + alias)
    for alias, java in native_block_java_ids().items():
        bedrock_names[java].add(alias)
    output, issues = {}, []
    for field, tag in (('leafDistanceLeaves', 'minecraft:leaves'), ('leafDistanceLogs', 'minecraft:logs')):
        try:
            java = _block_tag_members(models, tag)
            output[field] = sorted({alias for block in java for alias in bedrock_names.get(block, {block})} | java)
        except (KeyError, ValueError) as error:
            issues.append({'tag': tag, 'reason': str(error)})
    return output, issues


def _ctm_matches(stack):
    """(blocks, textures, issues) that the pack's CTM .properties files match.

    A file without matchBlocks or matchTiles matches by its name: block_<name>
    names a block, any other name a tile. Numeric (pre-1.13) block ids are skipped.
    """
    from import_java_ctm import parse_properties
    from java_texture_paths import resolve_texture
    targets, textures, issues = set(), set(), []
    for path in sorted(stack.files):
        if '/optifine/ctm/' not in path or not path.endswith('.properties'):
            continue
        try:
            data = parse_properties(stack.read(path).decode('utf-8-sig'), path)
            if 'matchBlocks' not in data and 'matchTiles' not in data:
                stem = PurePosixPath(path).stem
                data['matchBlocks' if stem.startswith('block_') else 'matchTiles'] = stem.removeprefix('block_')
            for token in data.get('matchBlocks', '').split():
                # namespace:name:state=value...: the block name ends before the first state filter.
                parts = token.split(':')
                first_state = next((index for index, part in enumerate(parts) if '=' in part), len(parts))
                name = ':'.join(parts[:first_state])
                if not name.isdecimal():
                    targets.add(name if ':' in name else 'minecraft:' + name)
            textures.update(resolve_texture(token, path, True) for token in data.get('matchTiles', '').split())
        except (UnicodeError, ValueError) as error:
            issues.append({'source': path, 'reason': str(error), 'geometry_preserved': False})
    return targets, textures, issues


def _model_references(value):
    """Every model a blockstate document (or part of one) selects."""
    if isinstance(value, dict):
        if 'model' in value:
            yield value['model']
        else:
            for item in value.values():
                yield from _model_references(item)
    elif isinstance(value, list):
        for item in value:
            yield from _model_references(item)


def _selects_authored_model(models, value):
    """Whether a blockstate document (or part of one) selects a model with authored geometry."""
    if isinstance(value, dict):
        if 'model' in value and _has_authored_geometry(models, value['model']):
            return True
        return any(_selects_authored_model(models, item) for item in value.values())
    if isinstance(value, list):
        return any(_selects_authored_model(models, item) for item in value)
    return False


def _has_authored_geometry(models, identifier, chain=()):
    """Whether a model or one of its parents is the pack's own with geometry unlike vanilla's."""
    stack = models.stack
    path = _resource(identifier, 'models')
    if path in stack.files:
        new = models.read(path)
        if path not in models.names:
            return True
        old = _json(models.vanilla.read(path))
        if _comparable_geometry(new) != _comparable_geometry(old):
            return True
    if path in chain:
        raise ValueError('Cyclic model parent chain: ' + path)
    parent = models.read(path).get('parent')
    return bool(parent and not parent.startswith('builtin/')
                and _has_authored_geometry(models, parent, chain + (path,)))


def _comparable_geometry(document):
    """A model's parent, textures and elements without differences that draw the same."""
    result = copy.deepcopy({key: value for key, value in document.items() if key in ('parent', 'textures', 'elements')})
    for element in result.get('elements', []):
        # 26.2 "shade": false and 26.3 "shade_direction_override": "up" draw the same.
        flat = not shaded(element)
        element.pop('shade', None)
        element.pop('shade_direction_override', None)
        if flat:
            element['shade'] = False
        for face, data in element.get('faces', {}).items():
            # Face tint is handled by the color provider. An
            # explicit canonical UV is the same geometry as its
            # implicit rectangle, not a new mesh to render.
            data.pop('tintindex', None)
            if 'uv' not in data:
                data['uv'] = default_uv(face, element['from'], element['to'])
            if not data.get('rotation', 0):
                data.pop('rotation', None)
    return result


def _numbers(value, length, label):
    """value as a list of `length` finite numbers; ValueError naming the Java field otherwise."""
    if not isinstance(value, list) or len(value) != length or any(not _finite(number) for number in value):
        raise ValueError('Invalid Java ' + label)
    return list(value)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _checked_rotation(rotation):
    """A copy of a Java element rotation (single-axis or Euler form) after checking its numbers."""
    rotation = copy.deepcopy(rotation)
    _numbers(rotation.get('origin'), 3, 'rotation origin')
    if 'axis' in rotation:
        if rotation['axis'] not in ('x', 'y', 'z') or not _finite(rotation.get('angle')):
            raise ValueError('Invalid Java element axis rotation')
    elif not all(_finite(rotation.get(axis, 0)) for axis in ('x', 'y', 'z')):
        raise ValueError('Invalid Java element Euler rotation')
    return rotation


def _has_visible_area(face, uv, start, end):
    """Whether a face covers texture and space: neither its UV rectangle nor its extent across the face is flat."""
    normal_axis = next(axis for axis, value in enumerate(FACE_NORMALS[face]) if value)
    return not (uv[0] == uv[2] or uv[1] == uv[3]
                or any(start[axis] == end[axis] for axis in range(3) if axis != normal_axis))


def _world_face(face, x, y):
    """The world face a model face ends up on after the blockstate's x and y quarter turns."""
    normal = _rotate_vector(FACE_NORMALS[face], x, y)
    return next(name for name, vector in FACE_NORMALS.items() if vector == normal)


def _weight(choice):
    weight = choice.get('weight', 1)
    if type(weight) is not int or weight < 1:
        raise ValueError('Invalid Java model weight')
    return weight


def _choices(models, choices):
    """[{'weight', 'modelParts'}] for each weighted model choice of a group."""
    result = [{'weight': _weight(selection), 'modelParts': model_parts(models, selection)}
              for selection in _as_list(choices)]
    if not result or sum(choice['weight'] for choice in result) > MAX_TOTAL_WEIGHT:
        raise ValueError('Invalid total Java model weight')
    return result


def _bedrock_selections(identifier, states):
    """[{'block', 'states'}] of the Bedrock blocks and states that stand for a Java block in these states."""
    if identifier.startswith('minecraft:potted_'):
        raise ValueError('Bedrock flower-pot contents require a block-entity adapter: ' + identifier)
    if identifier == 'minecraft:flowering_azalea_leaves' and not states:
        return [{'block': 'minecraft:azalea_leaves_flowered', 'states': {}}]
    if identifier == 'minecraft:dirt_path' and not states:
        return [{'block': 'minecraft:grass_path', 'states': {}}]
    if identifier == 'minecraft:seagrass' and not states:
        return [{'block': 'minecraft:seagrass', 'states': {'sea_grass_type': 'default'}}]
    if identifier == 'minecraft:tall_seagrass' and set(states) == {'half'} and states['half'] in ('lower', 'upper'):
        seagrass_type = 'double_top' if states['half'] == 'upper' else 'double_bot'
        return [{'block': 'minecraft:seagrass', 'states': {'sea_grass_type': seagrass_type}}]
    try:
        return states_java_to_bedrock(identifier, states)
    except ValueError as error:
        name = identifier.removeprefix('minecraft:')
        # Without a verified state reference the block keeps its states but takes its current Bedrock id.
        targets = _bedrock_ids(identifier)
        if not targets:
            raise ValueError('No Bedrock block for ' + identifier) from error
        if not states and str(error).startswith('No verified Java state reference'):
            return [{'block': target, 'states': {}} for target in targets]
        if name in DOUBLE_PLANTS and set(states) == {'half'} and states['half'] in ('lower', 'upper'):
            return [{'block': target, 'states': {'upper_block_bit': states['half'] == 'upper'}} for target in targets]
        if name.endswith('_leaves') and set(states) == {'persistent', 'distance'}:
            return [{'block': target, 'states': {'persistent_bit': states['persistent'] == 'true',
                                                 'bct:leaf_distance': int(states['distance'])}}
                    for target in targets]
        raise


def _bedrock_ids(identifier):
    """Current Bedrock ids for a Java id (block_ids); the id itself when Mojang's block list is not available."""
    known = _known_blocks()
    if known is None:
        return [identifier]
    return bedrock_ids(identifier, known)


@lru_cache(maxsize=1)
def _known_blocks():
    """The game's block ids, or None when bedrock-samples cannot be read."""
    try:
        return known_blocks(samples_path())
    except OSError:
        return None


def _selections(models, blockstate, identifier):
    """[(Java states, model groups, selection algorithm)] a blockstate shows.

    A variants blockstate gives one group per variant. A multipart blockstate
    is read for every verified Java state (every persistent and distance pair
    for leaves, or the one stateless case when no part has a condition) and
    gives every matching part's apply value as its own group.
    """
    document = models.read(blockstate)
    if 'multipart' not in document:
        return [(_variant_states(state), [choices], VARIANT_SELECTION)
                for state, choices in document.get('variants', {}).items()]
    rows = state_reference()['blocks'].get(identifier)
    if not rows:
        if identifier.endswith('_leaves'):
            # Bedrock persistence is stored; Java's 1..7 log distance is a
            # derived runtime predicate, not an alias of update_bit.
            rows = [{'java': {'persistent': persistent, 'distance': str(distance)}}
                    for persistent in ('false', 'true') for distance in LEAF_DISTANCES]
        elif all(not part.get('when') for part in document['multipart']):
            rows = [{'java': {}}]
        else:
            raise ValueError('No verified Java states for multipart model geometry: ' + identifier)
    result, seen = [], set()
    for row in rows:
        states = row['java']
        key = tuple(sorted(states.items()))
        if key in seen:
            continue
        seen.add(key)
        groups = [part['apply'] for part in document['multipart'] if _multipart_matches(part.get('when', {}), states)]
        if groups:
            result.append((states, groups, MULTIPART_SELECTION))
    return result


def _no_cube_binding():
    return {field: {} for field in NO_CUBE_FIELDS}


def _state_binding(models, groups, algorithm):
    """The variant entry fields for one Java state's model groups (states and Java block left out).

    At most one group may hold complete cubes: it becomes the cube binding (with
    its extra parts), every other group an independent modelPartsGroups entry.
    """
    cube_group = _cube_group(models, groups)
    binding = _cube_binding(models, cube_group, algorithm) if cube_group is not None else _no_cube_binding()
    part_groups = [{'modelSelection': algorithm, 'modelChoices': _choices(models, group)}
                   for group in groups if not (cube_group is not None and group is cube_group)]
    if part_groups:
        binding['modelPartsGroups'] = part_groups
    if cube_group is None and len(part_groups) == 1:
        # A regular noncube variants selection remains a normal
        # weighted modelChoices entry, so the runtime selects it once.
        choices = part_groups[0]['modelChoices']
        binding.pop('modelPartsGroups')
        if len(choices) == 1:
            binding['modelParts'] = choices[0]['modelParts']
        else:
            binding = {'modelSelection': algorithm,
                       'modelChoices': [{**_no_cube_binding(), **choice} for choice in choices]}
    return binding


def _cube_group(models, groups):
    """The one group whose choices are all complete cubes, or None; ValueError for mixed or several cube groups."""
    cube_groups = []
    for group in groups:
        cubes = [_cube_element(models.resolve(_resource(choice['model'], 'models'))) is not None
                 for choice in _as_list(group)]
        if any(cubes) and not all(cubes):
            raise ValueError('Weighted selection mixes complete cubes and custom geometry')
        if all(cubes):
            cube_groups.append(group)
    if len(cube_groups) > 1:
        raise ValueError('Multipart selection has multiple complete cube groups')
    return cube_groups[0] if cube_groups else None


def _cube_binding(models, cube_group, algorithm):
    """The cube faces of each weighted choice (java_block_bindings) with the model's other parts."""
    resolved = []
    for choice in _as_list(cube_group):
        value = {'weight': _weight(choice), **_rotated_cube_faces(models, choice)}
        value['modelParts'] = model_parts(models, choice, extras_only=True)
        resolved.append(value)
    if sum(choice['weight'] for choice in resolved) > MAX_TOTAL_WEIGHT:
        raise ValueError('Invalid total Java model weight')
    if len(resolved) == 1:
        return {key: value for key, value in resolved[0].items() if key != 'weight'}
    return {'modelSelection': algorithm, 'modelChoices': resolved}


def _part_textures(variants):
    """Yield all bound parts, including independent multipart choices."""
    for entries in variants.values():
        for entry in entries:
            groups = entry.get('modelPartsGroups', [])
            for choice in entry.get('modelChoices', [entry]):
                groups = groups + choice.get('modelPartsGroups', [])
                for part in choice.get('modelParts', []):
                    yield from (face['texture'] for face in part['faces'].values())
            for group in groups:
                for choice in group['modelChoices']:
                    for part in choice.get('modelParts', []):
                        yield from (face['texture'] for face in part['faces'].values())


def _block_tag_members(models, identifier, chain=()):
    """The blocks a Java block tag holds, following nested #tags (optional ones may be missing)."""
    if identifier in chain:
        raise ValueError('Cyclic Java block tag: ' + identifier)
    namespace, name = identifier.split(':', 1) if ':' in identifier else ('minecraft', identifier)
    if '..' in name.split('/'):
        raise ValueError('Unsafe Java block tag: ' + identifier)
    document = models.read(f'data/{namespace}/tags/block/{name}.json')
    members = set()
    for entry in document.get('values', []):
        required = True
        if isinstance(entry, dict):
            required = entry.get('required', True)
            entry = entry['id']
        if entry.startswith('#'):
            try:
                members.update(_block_tag_members(models, entry[1:], chain + (identifier,)))
            except KeyError:
                if required:
                    raise
        else:
            members.add(entry if ':' in entry else 'minecraft:' + entry)
    return members
