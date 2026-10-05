"""Convert OptiFine/EMF custom entity models (.jem/.jpm) to Bedrock geometry and animations.

Coordinate rules follow the OptiFine runtime (as reproduced by Entity Model
Features): every model entry is a model part whose pivot, rotation and boxes
are the JSON values with the ``invertAxis`` axes negated (boxes as
``-x - size``). A top-level entry is attached to the vanilla part named by
``part`` and replaces that part's own boxes unless ``attach`` is true; the
vanilla part keeps its vanilla pose, so vanilla animation still moves it.

Bedrock geometry uses Java model space with y flipped about the ground plane:
``(x, 24 - y, z)``. Bedrock bone rotation in degrees equals Java model part
rotation (radians) in degrees on all three axes, applied Z * Y * X like
Java. Per-face UVs keep their face names and use Blockbench's Bedrock
convention (up/down rectangles start at their far corner).

Vanilla Bedrock bones keep their names, pivots and rest rotations so the
vanilla animations still drive them; the author's parts become child bones
named ``cem_<id>``. Animation expressions are compiled into per-entity Molang
variables in ``pre_animation`` and read by one looping animation.

convert() runs the steps: load_model() parses the document, GeometryBuilder
builds the geometry and AnimationCompiler the scripts and animation.
"""
from __future__ import annotations

from collections import Counter
import copy
from dataclasses import dataclass, field
import itertools
import math
import re
from typing import NamedTuple

import numpy as np

from java_entity_molang import (DEGREES_PER_RADIAN, MODEL_VARIABLES, RADIANS_PER_DEGREE, UNMAPPED_VARIABLES,
                                ExpressionError, Translator, evaluate, identifiers, is_constant, parse)

GEOMETRY_FORMAT = '1.16.0'
# The bone above every vanilla bone when a model moves Java's whole-model 'root' part.
ROOT_BONE = 'bct_root'
# Java entity model space has y pointing down from an origin 24 pixels above the ground; Bedrock
# geometry has y pointing up from the ground, so a Java y becomes 24 - y.
_MODEL_ORIGIN_Y = 24.0
PART_VARIABLES = ('tx', 'ty', 'tz', 'rx', 'ry', 'rz', 'sx', 'sy', 'sz', 'visible', 'visible_boxes')
_TRANSLATION_VARIABLES = ('tx', 'ty', 'tz')
_ROTATION_VARIABLES = ('rx', 'ry', 'rz')
_SCALE_VARIABLES = ('sx', 'sy', 'sz')
_POSE_VARIABLES = _TRANSLATION_VARIABLES + _ROTATION_VARIABLES
# Older and newer Java names of the same model part.
PART_ALIASES = {
    'leg1': ('right_hind_leg', 'back_right_leg'), 'leg2': ('left_hind_leg', 'back_left_leg'),
    'leg3': ('right_front_leg', 'front_right_leg'), 'leg4': ('left_front_leg', 'front_left_leg'),
    'right_hind_leg': ('leg1', 'back_right_leg'), 'left_hind_leg': ('leg2', 'back_left_leg'),
    'right_front_leg': ('leg3', 'front_right_leg'), 'left_front_leg': ('leg4', 'front_left_leg'),
    'back_right_leg': ('leg1', 'right_hind_leg'), 'back_left_leg': ('leg2', 'left_hind_leg'),
    'front_right_leg': ('leg3', 'right_front_leg'), 'front_left_leg': ('leg4', 'left_front_leg'),
    'headwear': ('hat', 'head'), 'hat': ('headwear', 'head'), 'headwear2': ('hat', 'head'),
    'bodywear': ('jacket', 'body'), 'jacket': ('body',), 'torso': ('body',),
    'left_sleeve': ('left_arm',), 'right_sleeve': ('right_arm',),
    'left_pants': ('left_leg',), 'right_pants': ('right_leg',),
}
# JEM per-face UV keys and the face each one sets; uvFront ... uvBottom are the older names.
_JEM_FACE_UV_KEYS = {'uvNorth': 'north', 'uvSouth': 'south', 'uvEast': 'east', 'uvWest': 'west',
                     'uvUp': 'up', 'uvDown': 'down', 'uvFront': 'north', 'uvBack': 'south',
                     'uvLeft': 'west', 'uvRight': 'east', 'uvTop': 'up', 'uvBottom': 'down'}
_MODERN_BONE_KEYS = ('name', 'parent', 'pivot', 'rotation', 'bind_pose_rotation', 'mirror', 'inflate', 'cubes',
                     'locators')
# How a part's Bedrock bone was found, when that is certain enough to report nothing.
_EXACT_BONE_MATCHES = ('table', 'root', 'same name')
# A part is put on the bone nearest its vanilla pivot only when one is within half a block.
_NEAREST_BONE_DISTANCE = 8.0
_PIXELS_PER_BLOCK = 16.0
# Animation keys and expressions name the current top-level part 'this'; it becomes this marker
# plus the part's index, which cannot clash with a part name.
_THIS_MARKER = '__self__'


class ModelError(ValueError):
    """The document is not a usable entity model."""


@dataclass
class Box:
    """One box of a model part, in part-local Java model space (inverted axes applied)."""
    origin: tuple                                   # minimum corner
    size: tuple
    inflate: tuple = (0.0, 0.0, 0.0)                # growth on each axis (sizeAdd)
    texture_offset: tuple | None = None             # box-UV origin; None when the faces have their own UVs
    face_uvs: dict = field(default_factory=dict)    # face name -> [u1, v1, u2, v2]


@dataclass
class Part:
    """A model part: a top-level JEM model entry or one of its submodels."""
    id: str
    vanilla_part: str | None = None         # JEM 'part': the vanilla part a top-level entry attaches to
    attach: bool = False                    # keep the vanilla part's own boxes
    pivot: tuple = (0.0, 0.0, 0.0)          # Java model part x, y, z (local)
    rotation: tuple = (0.0, 0.0, 0.0)       # Java model part rotation, radians
    translate: tuple = (0.0, 0.0, 0.0)      # JSON value, for the vanilla pivot convention
    inverted_axes: str = ''                 # JEM invertAxis, lower case
    scale: float = 1.0
    mirror_u: bool = False
    mirror_v: bool = False
    boxes: list = field(default_factory=list)
    children: list = field(default_factory=list)
    animations: list = field(default_factory=list)  # top-level parts only: the JEM animation blocks
    texture: str | None = None
    texture_size: tuple | None = None


@dataclass
class Model:
    """A parsed .jem document."""
    texture: str | None
    texture_size: tuple
    shadow_size: float | None
    parts: list
    issues: list = field(default_factory=list)      # what the conversion leaves out, for the report


# --- parsing ---

def load_model(data, *, read_part=None):
    """Parse a .jem document. read_part(reference) returns a .jpm document or None."""
    if not isinstance(data, dict) or not isinstance(data.get('models'), list):
        raise ModelError('Not an entity model: no models list')
    issues = []
    if data.get('textureSize'):
        texture_size = _number_tuple(data.get('textureSize'), 2, (64, 32))
    else:
        texture_size = (64.0, 32.0)
        issues.append('textureSize missing; OptiFine uses 64x32')
    part_numbers = itertools.count(1)
    parts = [_parse_part(entry, top_level=True, read_part=read_part, issues=issues, part_numbers=part_numbers)
             for entry in data['models'] if isinstance(entry, dict)]
    shadow_size = data.get('shadowSize', data.get('shadow_size'))
    return Model(texture=data.get('texture') or None, texture_size=texture_size,
                 shadow_size=float(shadow_size) if isinstance(shadow_size, (int, float)) else None,
                 parts=parts, issues=issues)


def resolve_texture(reference, model_path):
    """Pack path of a texture (or part model) reference in a model, by OptiFine's path rules.

    'namespace:path' is a full asset path, './x' and a bare 'x' sit beside the model,
    '~/x' is under optifine/ and any other 'folder/x' is under assets/minecraft/.
    """
    if not reference:
        return None
    reference = str(reference).strip()
    if not reference.endswith('.png'):
        reference += '.png'
    folder = model_path.rsplit('/', 1)[0] if '/' in model_path else ''
    if ':' in reference:
        namespace, path = reference.split(':', 1)
        return 'assets/%s/%s' % (namespace, path)
    if reference.startswith('./'):
        return folder + '/' + reference[2:]
    if reference.startswith('~/'):
        return 'assets/minecraft/optifine/' + reference[2:]
    if '/' not in reference:
        return folder + '/' + reference
    return 'assets/minecraft/' + reference


def shift_uv(part, v_offset):
    """Move every box of a part tree down the texture by v_offset (stacking a layer model under another)."""
    for box in part.boxes:
        if box.texture_offset is not None:
            box.texture_offset = (box.texture_offset[0], box.texture_offset[1] + v_offset)
        box.face_uvs = {face: [rect[0], rect[1] + v_offset, rect[2], rect[3] + v_offset]
                        for face, rect in box.face_uvs.items()}
    for child in part.children:
        shift_uv(child, v_offset)


def _parse_part(data, *, top_level, read_part, issues, part_numbers):
    if not isinstance(data, dict):
        raise ModelError('Model entry is not an object')
    if top_level and data.get('model') and read_part:
        data = _with_part_model(data, read_part, issues)
    inverted_axes = str(data.get('invertAxis', '')).lower()
    translate = _number_tuple(data.get('translate'), 3, (0, 0, 0))
    rotate = _number_tuple(data.get('rotate'), 3, (0, 0, 0))
    pivot = tuple(-value if axis in inverted_axes else value for value, axis in zip(translate, 'xyz'))
    rotation = tuple((-value if axis in inverted_axes else value) * RADIANS_PER_DEGREE
                     for value, axis in zip(rotate, 'xyz'))
    # Every part is numbered in document order; one without an id or part name is named by its number.
    number = next(part_numbers)
    identifier = str(data.get('id') or data.get('part') or 'part%d' % number)
    mirror = str(data.get('mirrorTexture', '')).lower()
    part = Part(id=identifier,
                vanilla_part=data.get('part') if top_level else None,
                attach=bool(data.get('attach', False)) if top_level else False,
                pivot=pivot, rotation=rotation, translate=translate, inverted_axes=inverted_axes,
                scale=float(data.get('scale', 1.0) or 1.0), mirror_u='u' in mirror, mirror_v='v' in mirror,
                texture=data.get('texture') or None,
                texture_size=_number_tuple(data.get('textureSize'), 2, (0, 0)) if data.get('textureSize') else None)
    if top_level and data.get('baseId'):
        issues.append('baseId inheritance is not converted: ' + str(data['baseId']))
    if data.get('sprites'):
        issues.append('sprites are not converted in ' + identifier)
    if data.get('attachments'):
        issues.append('attachment points are not converted in ' + identifier)
    for box in data.get('boxes', []) or []:
        part.boxes.append(_parse_box(box, inverted_axes, issues))
    for child in _submodels(data):
        part.children.append(_parse_part(child, top_level=False, read_part=read_part, issues=issues,
                                         part_numbers=part_numbers))
    if top_level:
        part.animations = [entry for entry in (data.get('animations') or []) if isinstance(entry, dict)]
    elif data.get('animations'):
        issues.append('animations below the top level are ignored by OptiFine: ' + identifier)
    return part


def _with_part_model(data, read_part, issues):
    """A top-level entry that names a .jpm part model takes the part model's fields; its own fields win."""
    external = read_part(data['model'])
    if external is None:
        issues.append('part model file not found: ' + str(data['model']))
        return data
    merged = dict(external)
    merged.update({key: value for key, value in data.items() if key != 'model'})
    # An entry's empty boxes, submodels or animations do not hide the part model's.
    for key in ('submodels', 'boxes', 'animations'):
        if not data.get(key) and external.get(key):
            merged[key] = external[key]
    return merged


def _submodels(data):
    children = list(data.get('submodels', []) or [])
    if isinstance(data.get('submodel'), dict):
        children.append(data['submodel'])
    return children


def _parse_box(data, inverted_axes, issues):
    coordinates = data.get('coordinates')
    if not isinstance(coordinates, list) or len(coordinates) != 6:
        raise ModelError('Box without six coordinates')
    x, y, z, size_x, size_y, size_z = (float(value) for value in coordinates)
    # An inverted axis mirrors the box, so its far corner becomes the minimum corner.
    if 'x' in inverted_axes:
        x = -x - size_x
    if 'y' in inverted_axes:
        y = -y - size_y
    if 'z' in inverted_axes:
        z = -z - size_z
    inflate = _box_inflation(data)
    texture_offset, face_uvs = _box_texture(data, issues)
    if texture_offset is None and not face_uvs:
        issues.append('box without texture coordinates is not drawn')
    return Box((x, y, z), (size_x, size_y, size_z), inflate, texture_offset, face_uvs)


def _box_inflation(data):
    """Growth of a box on each axis: sizeAdd, overridden by sizesAdd, then by sizeAddX/Y/Z."""
    growth = float(data.get('sizeAdd', 0.0) or 0.0)
    inflate = [growth, growth, growth]
    if isinstance(data.get('sizesAdd'), list) and len(data['sizesAdd']) == 3:
        inflate = [float(value) for value in data['sizesAdd']]
    for axis, key in enumerate(('sizeAddX', 'sizeAddY', 'sizeAddZ')):
        if key in data:
            inflate[axis] = float(data[key])
    return tuple(inflate)


def _box_texture(data, issues):
    """(box-UV origin, per-face UV rectangles): textureOffset when given, else the per-face keys."""
    offset = data.get('textureOffset')
    if offset is not None:
        if not isinstance(offset, list) or len(offset) != 2:
            raise ModelError('Invalid textureOffset')
        return (float(offset[0]), float(offset[1])), {}
    face_uvs = {}
    for key, face in _JEM_FACE_UV_KEYS.items():
        value = data.get(key)
        if isinstance(value, list) and len(value) == 4:
            face_uvs[face] = [float(item) for item in value]
        elif value not in (None, []):
            issues.append('ignored invalid ' + key)
    return None, face_uvs


def _number_tuple(value, count, default):
    """A JSON list of count numbers as a float tuple; default when the value is absent."""
    if value is None:
        return tuple(default)
    if not isinstance(value, (list, tuple)) or len(value) != count:
        raise ModelError('Expected %d numbers, found %r' % (count, value))
    return tuple(float(item) for item in value)


# --- rotations and coordinates ---

def _rotation_matrix(angles_degrees):
    """Rotation matrix of a Bedrock bone rotation, in Bedrock geometry space."""
    # Bedrock values (x, y, z) are right-handed angles (-x, y, -z) applied Z * Y * X.
    angle_x = -angles_degrees[0] * RADIANS_PER_DEGREE
    angle_y = angles_degrees[1] * RADIANS_PER_DEGREE
    angle_z = -angles_degrees[2] * RADIANS_PER_DEGREE
    rotate_x = np.array([[1, 0, 0],
                         [0, math.cos(angle_x), -math.sin(angle_x)],
                         [0, math.sin(angle_x), math.cos(angle_x)]])
    rotate_y = np.array([[math.cos(angle_y), 0, math.sin(angle_y)],
                         [0, 1, 0],
                         [-math.sin(angle_y), 0, math.cos(angle_y)]])
    rotate_z = np.array([[math.cos(angle_z), -math.sin(angle_z), 0],
                         [math.sin(angle_z), math.cos(angle_z), 0],
                         [0, 0, 1]])
    return rotate_z @ rotate_y @ rotate_x


def _bedrock_rotation(matrix):
    """Inverse of _rotation_matrix: the Bedrock bone rotation (degrees) of a rotation matrix."""
    angle_y = math.asin(max(-1.0, min(1.0, -matrix[2, 0])))
    if abs(matrix[2, 0]) < 0.999999:
        angle_x = math.atan2(matrix[2, 1], matrix[2, 2])
        angle_z = math.atan2(matrix[1, 0], matrix[0, 0])
    else:
        # Gimbal lock (y at +-90 degrees): x and z turn about one axis, so x takes all of it.
        angle_x = math.atan2(-matrix[1, 2], matrix[1, 1])
        angle_z = 0.0
    return [_rounded(-angle_x * DEGREES_PER_RADIAN), _rounded(angle_y * DEGREES_PER_RADIAN),
            _rounded(-angle_z * DEGREES_PER_RADIAN)]


def _inverse_rotation(angles_degrees):
    """The Bedrock bone rotation that undoes angles_degrees."""
    return _bedrock_rotation(_rotation_matrix(angles_degrees).T)


def _rounded(value, digits=6):
    """A value rounded for the output files, never a negative zero."""
    value = round(float(value), digits)
    return 0.0 if value == 0 else value


def _rounded_list(values):
    return [_rounded(value) for value in values]


def _java_to_bedrock(point):
    return [point[0], _MODEL_ORIGIN_Y - point[1], point[2]]


def _degrees_list(radians):
    return _rounded_list(value * DEGREES_PER_RADIAN for value in radians)


# --- UV ---

def box_uv_faces(offset, size, mirror=False):
    """Per-face UV rectangles [u1, v1, u2, v2] of a box-UV cube, keyed by JEM/Bedrock face name.

    The standard layout: up and down side by side on the top row, east, north, west
    and south below them. Up and down are written from their right edge (a negative
    u extent), and up also from its bottom edge.
    """
    size_x, size_y, size_z = size
    # (face, start, extent), relative to the texture offset.
    faces = [('east', [0, size_z], [size_z, size_y]),
             ('west', [size_z + size_x, size_z], [size_z, size_y]),
             ('up', [size_z + size_x, size_z], [-size_x, -size_z]),
             ('down', [size_z + 2 * size_x, 0], [-size_x, size_z]),
             ('south', [2 * size_z + size_x, size_z], [size_x, size_y]),
             ('north', [size_z, size_z], [size_x, size_y])]
    if mirror:
        faces = _mirrored_faces(faces)
    return {name: [offset[0] + start[0], offset[1] + start[1],
                   offset[0] + start[0] + extent[0], offset[1] + start[1] + extent[1]]
            for name, start, extent in faces}


def _mirrored_faces(faces):
    """A mirrored box: every face flipped horizontally, and east and west swap rectangles."""
    flipped = {name: ([start[0] + extent[0], start[1]], [-extent[0], extent[1]]) for name, start, extent in faces}
    flipped['east'], flipped['west'] = flipped['west'], flipped['east']
    return [(name, *flipped[name]) for name, _, _ in faces]


def bedrock_face_uv(face, rect):
    """Bedrock per-face UV of a rectangle; up and down start at their far corner (Blockbench's convention)."""
    u1, v1, u2, v2 = rect
    if face in ('up', 'down'):
        return {'uv': _rounded_list([u2, v2]), 'uv_size': _rounded_list([u1 - u2, v1 - v2])}
    return {'uv': _rounded_list([u1, v1]), 'uv_size': _rounded_list([u2 - u1, v2 - v1])}


def _bedrock_cube(part_pivot, box, mirror_u, frame_scale=1.0, frame_offset=(0.0, 0.0, 0.0)):
    """Bedrock cube of a box on a part whose pivot is part_pivot (absolute Java model space)."""
    origin = [part_pivot[axis] + box.origin[axis] for axis in range(3)]
    # Java y points down, so the cube's lowest Bedrock corner is its Java corner of largest y.
    corner = [origin[0], _MODEL_ORIGIN_Y - (origin[1] + box.size[1]), origin[2]]
    cube = {'origin': _rounded_list([corner[axis] * frame_scale + frame_offset[axis] for axis in range(3)]),
            'size': _rounded_list([value * frame_scale for value in box.size])}
    inflate = tuple(value * frame_scale for value in box.inflate)
    whole_size = all(float(value).is_integer() for value in box.size)
    if inflate[0] == inflate[1] == inflate[2]:
        if inflate[0]:
            cube['inflate'] = _rounded(inflate[0])
        uniform_inflate = True
    else:
        # Bedrock inflates uniformly; grow the box and keep the authored UV rectangles.
        cube['origin'] = _rounded_list([cube['origin'][axis] - inflate[axis] for axis in range(3)])
        cube['size'] = _rounded_list([box.size[axis] * frame_scale + 2 * inflate[axis] for axis in range(3)])
        uniform_inflate = False
    if box.texture_offset is not None and whole_size and uniform_inflate and frame_scale == 1.0:
        # Bedrock lays box UV out from the cube's size, which only matches the Java layout for a
        # whole-pixel box at its own size; any other box keeps its rectangles as per-face UV.
        cube['uv'] = _rounded_list(box.texture_offset)
        if mirror_u:
            cube['mirror'] = True
        return cube
    if box.texture_offset is not None:
        face_uvs = box_uv_faces(box.texture_offset, box.size, mirror_u)
    else:
        face_uvs = box.face_uvs
    cube['uv'] = {face: bedrock_face_uv(face, rect) for face, rect in face_uvs.items()}
    return cube


def _write_per_face_uvs(bones):
    """Write box-UV cubes as the equivalent per-face UV (one UV form in every generated geometry)."""
    for bone in bones:
        bone_mirror = bool(bone.get('mirror'))
        for cube in bone.get('cubes', []) or []:
            uv = cube.get('uv')
            if not isinstance(uv, list):
                cube.pop('mirror', None)
                continue
            # Bedrock lays box UV out from the cube size rounded down.
            size = [math.floor(value) for value in cube['size']]
            faces = box_uv_faces(uv, size, bool(cube.pop('mirror', bone_mirror)))
            # A face without area draws nothing.
            cube['uv'] = {face: bedrock_face_uv(face, rect) for face, rect in faces.items()
                          if rect[0] != rect[2] and rect[1] != rect[3]}
        bone.pop('mirror', None)


def _visible_bounds(bones):
    """Visible bounds (in blocks) around every cube, wide enough for parts that turn."""
    corners = []
    for bone in bones:
        for cube in bone.get('cubes', []) or []:
            origin, size = cube['origin'], cube['size']
            inflate = cube.get('inflate', 0) or 0
            corners.append([origin[axis] - inflate for axis in range(3)])
            corners.append([origin[axis] + size[axis] + inflate for axis in range(3)])
    if not corners:
        return {'visible_bounds_width': 1, 'visible_bounds_height': 1, 'visible_bounds_offset': [0, 0.5, 0]}
    points = np.array(corners, dtype=float)
    # Rotated parts can reach past their unrotated box; use the farthest corner as a radius.
    radius = float(np.max(np.sqrt(points[:, 0] ** 2 + points[:, 2] ** 2))) / _PIXELS_PER_BLOCK
    low = float(points[:, 1].min()) / _PIXELS_PER_BLOCK
    high = float(points[:, 1].max()) / _PIXELS_PER_BLOCK
    reach = max(abs(low), abs(high))
    # Rounded up to quarter blocks after half a block of margin.
    width = math.ceil((2 * max(radius, reach) + 0.5) * 4) / 4
    height = math.ceil((max(high, 0) - min(low, 0) + reach + 0.5) * 4) / 4
    return {'visible_bounds_width': _rounded(width, 3),
            'visible_bounds_height': _rounded(height, 3),
            'visible_bounds_offset': [0, _rounded((max(high, 0) + min(low, 0)) / 2, 3), 0]}


# --- vanilla Bedrock geometry ---

def normalize_geometries(document):
    """Identifier -> geometry {'description', 'bones', ...} for both Bedrock geometry file formats.

    Current files list geometries under 'minecraft:geometry'; legacy files key each
    one 'geometry.name:parent' and give its size as texturewidth/textureheight.
    """
    found = {}
    if isinstance(document.get('minecraft:geometry'), list):
        for entry in document['minecraft:geometry']:
            description = dict(entry.get('description', {}))
            found[description.get('identifier')] = {'description': description,
                                                    'bones': copy.deepcopy(entry.get('bones', []) or []),
                                                    'format': document.get('format_version')}
    for key, entry in document.items():
        if not key.startswith('geometry.') or not isinstance(entry, dict):
            continue
        identifier, _, parent = key.partition(':')
        description = {'identifier': identifier, 'texture_width': entry.get('texturewidth', 64),
                       'texture_height': entry.get('textureheight', 64)}
        for name in ('visible_bounds_width', 'visible_bounds_height', 'visible_bounds_offset'):
            if name in entry:
                description[name] = entry[name]
        found[identifier] = {'description': description, 'bones': copy.deepcopy(entry.get('bones', []) or []),
                             'parent': parent or None, 'format': document.get('format_version'), 'legacy': True,
                             # Whether the size was written: resolve_inheritance keeps the parent's otherwise.
                             'raw_texture': ('texturewidth' in entry, 'textureheight' in entry)}
    return found


def resolve_inheritance(geometries):
    """Merge every legacy child geometry ('geometry.child:parent') over its resolved parent."""
    resolved = {}

    def resolve(identifier, chain=()):
        if identifier in resolved:
            return resolved[identifier]
        entry = geometries[identifier]
        parent = entry.get('parent')
        # chain holds the children waiting on this geometry; a parent already on it would be a cycle.
        if parent and parent in geometries and parent not in chain:
            entry = _inherit(entry, resolve(parent, chain + (identifier,)))
        resolved[identifier] = entry
        return entry

    for identifier in geometries:
        resolve(identifier)
    return resolved


def _inherit(entry, base):
    """A child geometry merged over its parent: new bones are added, same-named bones merged."""
    bones = {bone['name']: copy.deepcopy(bone) for bone in base['bones']}
    order = [bone['name'] for bone in base['bones']]
    for bone in entry['bones']:
        if bone['name'] not in bones:
            order.append(bone['name'])
            bones[bone['name']] = copy.deepcopy(bone)
            continue
        # A child geometry adds cubes to a parent bone of the same name (the wool of a sheep
        # over its body) and overrides the other fields.
        merged = bones[bone['name']]
        cubes = list(merged.get('cubes', []) or []) + copy.deepcopy(bone.get('cubes', []) or [])
        merged.update({key: copy.deepcopy(value) for key, value in bone.items() if key != 'cubes'})
        merged['cubes'] = cubes
    # A legacy child that leaves out texturewidth keeps its parent's texture size.
    keeps_parent_size = not entry.get('raw_texture', (True, True))[0]
    description = dict(base['description'])
    description.update({key: value for key, value in entry['description'].items()
                        if key not in ('texture_width', 'texture_height') or not keeps_parent_size})
    return {**entry, 'description': description, 'bones': [bones[name] for name in order]}


def _modern_bone(bone):
    """A vanilla bone in the current geometry format (legacy flags reduced)."""
    result = {key: copy.deepcopy(value) for key, value in bone.items() if key in _MODERN_BONE_KEYS}
    if bone.get('neverRender'):
        result['cubes'] = []
    bind_pose = result.get('bind_pose_rotation')
    if bind_pose and any(bind_pose) and all(not cube.get('rotation') for cube in result.get('cubes', [])):
        # The legacy bind pose turns only the bone's own cubes about its pivot: the same as cube rotations.
        for cube in result.get('cubes', []):
            cube['rotation'] = list(bind_pose)
            cube['pivot'] = list(result.get('pivot', [0, 0, 0]))
        result.pop('bind_pose_rotation')
    return result


def _rest_rotation(bone):
    return [float(value) for value in bone.get('rotation', [0, 0, 0])]


# --- geometry ---

@dataclass
class Target:
    """Where a model goes in Bedrock."""
    identifier: str                 # output geometry identifier
    vanilla: dict                   # vanilla Bedrock geometry (normalized)
    bone_map: dict                  # JEM part name -> Bedrock bone name
    vanilla_parts: dict = field(default_factory=dict)   # JEM part name -> {'pivot' (Java space), 'rotation' (degrees)}
    formulas: dict = field(default_factory=dict)        # '<part>.<var>' -> OptiFine expression of the vanilla value
    prefix: str = 'cem'             # bone name prefix of the author's parts
    # Java model space differs from the Bedrock geometry by a renderer scale and offset for a few mobs.
    frame_scale: float = 1.0
    frame_offset: tuple = (0.0, 0.0, 0.0)


class _VanillaPartFrame(NamedTuple):
    """How a top-level entry sits on its vanilla part, for the animation compiler."""
    bone: str | None            # the bone carrying the vanilla part's pose, which animations of the part drive
    rest_rotation: tuple        # the vanilla part's rest rotation, radians


class _VanillaSkeleton:
    """The vanilla bones of a geometry, indexed for attaching the author's parts."""

    def __init__(self, bones, part_bone_names):
        self.by_name = {bone['name']: bone for bone in bones}
        self.part_bone_names = part_bone_names      # bones that stand for a Java model part of their own
        self.children = {}
        for bone in bones:
            self.children.setdefault(bone.get('parent'), []).append(bone['name'])
        self.top_level_bone = next((bone['name'] for bone in bones if not bone.get('parent')), None)

    def hide_cubes(self, bone_name):
        """Hide a replaced bone's cubes and those of the bones below it that are no model part themselves."""
        self.by_name[bone_name]['cubes'] = []
        pending = list(self.children.get(bone_name, []))
        while pending:
            child = pending.pop()
            if child not in self.part_bone_names:
                self.by_name[child]['cubes'] = []
                pending.extend(self.children.get(child, []))


def _vanilla_pose(part, target):
    """Java absolute pivot and rest rotation (radians) of the vanilla part a top-level entry attaches to."""
    known = target.vanilla_parts.get(part.vanilla_part) or {}
    if 'pivot' in known:
        pivot = tuple(float(value) for value in known['pivot'])
    else:
        # The author's top-level translate is the negated vanilla pivot (OptiFine convention).
        x, y, z = part.translate
        inverted = part.inverted_axes
        pivot = (x if 'x' in inverted else -x,
                 _MODEL_ORIGIN_Y + y if 'y' in inverted else _MODEL_ORIGIN_Y - y,
                 z if 'z' in inverted else -z)
    rotation = tuple(float(value) * RADIANS_PER_DEGREE for value in known.get('rotation', (0, 0, 0)))
    return pivot, rotation


def _identifier_safe(name):
    """A name for Molang variables and bones: letters, digits and underscores, not starting with a digit."""
    cleaned = re.sub(r'[^A-Za-z0-9_]', '_', str(name))
    return cleaned if cleaned and not cleaned[0].isdigit() else '_' + cleaned


class GeometryBuilder:
    """Builds the Bedrock geometry of one model: the vanilla bones plus a bone for every author part.

    After build(), part_bones and vanilla_part_frames tell the animation compiler
    which bone carries each part.
    """

    def __init__(self, model, target):
        self.model = model
        self.target = target
        self.issues = list(model.issues)
        self.part_bones = {}                # JEM part id -> bone of the first part with that id
        self.vanilla_part_frames = {}       # vanilla part name -> _VanillaPartFrame
        self.used_bone_names = set()

    def build(self, detached_parts=frozenset()):
        """The Bedrock geometry document; detached_parts are vanilla parts the animations move themselves."""
        uses_root = any(part.vanilla_part == 'root' for part in self.model.parts) or 'root' in detached_parts
        bones = [_modern_bone(bone) for bone in self.target.vanilla['bones']]
        if uses_root:
            # Java's model root sits at the model origin; Bedrock gets a bone above every top-level bone.
            for bone in bones:
                if not bone.get('parent'):
                    bone['parent'] = ROOT_BONE
            bones.insert(0, {'name': ROOT_BONE, 'pivot': [0, 24, 0]})
        skeleton = _VanillaSkeleton(bones, set(self.target.bone_map.values()))
        self.used_bone_names = set(skeleton.by_name)
        attachments = []
        for part in self.model.parts:
            bone_name, how = self._vanilla_bone_for(part.vanilla_part, skeleton.by_name)
            attachments.append((part, bone_name, how))
        claims = Counter(bone_name for _, bone_name, _ in attachments)
        added_bones = []
        for part, bone_name, how in attachments:
            self._attach_part(part, bone_name, how, skeleton, claims, detached_parts, added_bones)
        if uses_root and 'root' not in self.vanilla_part_frames:
            self.vanilla_part_frames['root'] = _VanillaPartFrame(ROOT_BONE, (0.0, 0.0, 0.0))
        geometry_bones = bones + added_bones
        _write_per_face_uvs(geometry_bones)
        return self._document(geometry_bones)

    def _vanilla_bone_for(self, part_name, bones):
        """(Bedrock bone, how it was found) for a Java part name, or (None, None).

        Tried in order: the table, the model root, the base part of a rotation helper
        (name_r1), older or newer Java names of the part, the same name, and last the
        bone nearest the vanilla pivot.
        """
        mapping = self.target.bone_map
        if part_name in mapping and mapping[part_name] in bones:
            return mapping[part_name], 'table'
        if part_name == 'root':
            return ROOT_BONE, 'root'
        base = re.sub(r'_r\d+$', '', part_name)
        if base != part_name and base in mapping and mapping[base] in bones:
            return mapping[base], 'rotation helper of ' + base
        for alias in PART_ALIASES.get(part_name, ()):
            if alias in mapping and mapping[alias] in bones:
                return mapping[alias], 'alias of ' + alias
            if alias in bones:
                return alias, 'alias of ' + alias
        if part_name in bones:
            return part_name, 'same name'
        return self._nearest_bone(part_name, bones)

    def _nearest_bone(self, part_name, bones):
        top_level_part = next((part for part in self.model.parts if part.vanilla_part == part_name), None)
        if top_level_part is None:
            return None, None
        pivot = self._bedrock_point(_vanilla_pose(top_level_part, self.target)[0])
        best = None
        for bone in bones.values():
            if bone['name'] == ROOT_BONE:
                continue
            distance = math.dist(pivot, bone.get('pivot', [0, 0, 0]))
            if best is None or distance < best[0]:
                best = (distance, bone['name'])
        if best and best[0] <= _NEAREST_BONE_DISTANCE:
            return best[1], 'nearest pivot'
        return None, None

    def _attach_part(self, part, bone_name, how, skeleton, claims, detached_parts, added_bones):
        """Hang one top-level entry on its vanilla bone, with the frame bones its pose needs."""
        if bone_name is None:
            bone_name = skeleton.top_level_bone
            self.issues.append('part %r has no Bedrock bone; attached to %s'
                               % (part.vanilla_part, bone_name or 'the model root'))
            how = 'model root'
        elif how not in _EXACT_BONE_MATCHES:
            self.issues.append('part %r attached to Bedrock bone %s (%s)' % (part.vanilla_part, bone_name, how))
        vanilla_bone = skeleton.by_name.get(bone_name)
        # A part found by name replaces its vanilla part; one placed by position or as a fallback does not.
        replaces = how in _EXACT_BONE_MATCHES or how.startswith(('alias', 'rotation helper'))
        is_root = part.vanilla_part == 'root'
        detached = part.vanilla_part in detached_parts
        if vanilla_bone is not None and not part.attach and replaces and bone_name != ROOT_BONE:
            skeleton.hide_cubes(bone_name)
        pivot, rest = _vanilla_pose(part, self.target)
        if is_root:
            pivot, rest = (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        if (vanilla_bone is not None and bone_name != ROOT_BONE and replaces and claims[bone_name] == 1
                and not detached):
            # The vanilla animation turns the part about the Java pivot, as in Java.
            vanilla_bone['pivot'] = _rounded_list(self._bedrock_point(pivot))
        parent = bone_name
        frame_bone = None
        if is_root:
            frame_bone = ROOT_BONE
        elif detached and vanilla_bone is not None:
            # The animations pose this part themselves: it hangs beside the vanilla bone, not under it.
            parent = vanilla_bone.get('parent')
        elif vanilla_bone is not None and any(_rest_rotation(vanilla_bone)) and any(rest):
            parent = self._add_undo_bone(part, vanilla_bone, parent, added_bones)
        if not is_root and (any(rest) or detached):
            frame_bone = self._add_vanilla_frame(part, pivot, rest, parent, added_bones)
            parent = frame_bone
        self.vanilla_part_frames[part.vanilla_part] = _VanillaPartFrame(frame_bone, rest)
        self._add_part_bones(part, parent, pivot, added_bones)

    def _add_undo_bone(self, part, vanilla_bone, parent, added_bones):
        """A bone that cancels the vanilla bone's Bedrock rest rotation, so the Java rest pose applies alone."""
        undo = {'name': self._unique_bone_name(part.id + '_undo'),
                'pivot': _rounded_list(vanilla_bone.get('pivot', [0, 0, 0])),
                'rotation': _inverse_rotation(_rest_rotation(vanilla_bone))}
        if parent:
            undo['parent'] = parent
        added_bones.append(undo)
        return undo['name']

    def _add_vanilla_frame(self, part, pivot, rest, parent, added_bones):
        """A bone at the vanilla pivot with the vanilla rest rotation: the pose Java draws the part in."""
        name = self._unique_bone_name(part.id + '_vanilla')
        frame = {'name': name, 'pivot': _rounded_list(self._bedrock_point(pivot)), 'rotation': _degrees_list(rest)}
        if parent:
            frame['parent'] = parent
        added_bones.append(frame)
        return name

    def _add_part_bones(self, part, parent, parent_pivot, added_bones):
        """Add a bone for a part and for every part below it (a part's pivot is relative to its parent's)."""
        pivot = tuple(parent_pivot[axis] + part.pivot[axis] for axis in range(3))
        name = self._unique_bone_name(part.id)
        bone = {'name': name, 'pivot': _rounded_list(self._bedrock_point(pivot))}
        if parent:
            bone['parent'] = parent
        if any(part.rotation):
            bone['rotation'] = _degrees_list(part.rotation)
        if part.texture or part.texture_size:
            self.issues.append('per-part texture %r in %s is drawn with the model texture' % (part.texture, part.id))
        if part.mirror_v:
            self.issues.append('vertical texture mirroring in %s is not converted' % part.id)
        cubes = [_bedrock_cube(pivot, box, part.mirror_u, self.target.frame_scale, self.target.frame_offset)
                 for box in part.boxes]
        if cubes:
            bone['cubes'] = cubes
        added_bones.append(bone)
        self.part_bones.setdefault(part.id, bone)
        for child in part.children:
            self._add_part_bones(child, name, pivot, added_bones)

    def _unique_bone_name(self, identifier):
        base = self.target.prefix + '_' + _identifier_safe(identifier)
        name = base
        number = 2
        while name in self.used_bone_names:
            name = '%s_%d' % (base, number)
            number += 1
        self.used_bone_names.add(name)
        return name

    def _bedrock_point(self, java_point):
        bedrock = _java_to_bedrock(java_point)
        return [bedrock[axis] * self.target.frame_scale + self.target.frame_offset[axis] for axis in range(3)]

    def _document(self, geometry_bones):
        vanilla_description = self.target.vanilla['description']
        width, height = self.model.texture_size
        description = {'identifier': self.target.identifier,
                       'texture_width': _rounded(width), 'texture_height': _rounded(height)}
        bounds = _visible_bounds(geometry_bones)
        for key in ('visible_bounds_width', 'visible_bounds_height'):
            # Never smaller than the vanilla bounds.
            description[key] = max(float(vanilla_description.get(key, 0) or 0), bounds[key])
        description['visible_bounds_offset'] = bounds['visible_bounds_offset']
        return {'format_version': GEOMETRY_FORMAT,
                'minecraft:geometry': [{'description': description, 'bones': geometry_bones}]}


# --- animation ---

@dataclass
class Assignment:
    """One 'target.variable': expression entry of a JEM animation block."""
    key: str                    # the key with whitespace removed and 'this'/'part' replaced
    target: str                 # 'var', 'varb', 'render', '<part id>' or vanilla part name
    variable: str
    expression: object          # parsed tree
    kind: str | None = None     # 'var', 'varb', 'render', 'vanilla' or 'custom'; None when unknown


def _normalised_animation_text(text, part, index):
    """Strip whitespace and replace 'this.' and 'part.' by the names they stand for in top-level part index.

    'this' names the current custom part even when its id equals a vanilla part name.
    """
    text = re.sub(r'\s', '', str(text))
    text = re.sub(r'(?<![\w.])this(?=\.)', _THIS_MARKER + str(index), text)
    if part.vanilla_part:
        text = re.sub(r'(?<![\w.])part(?=\.)', part.vanilla_part, text)
    return text


def _collect_assignments(model, issues):
    """Every entry of the model's animation blocks, parsed, in document order."""
    found = []
    for index, part in enumerate(model.parts):
        for block in part.animations:
            for key, value in block.items():
                key = _normalised_animation_text(key, part, index)
                if '.' not in key:
                    issues.append('animation key without a target ignored: ' + key)
                    continue
                target, variable = key.split('.', 1)
                expression = _normalised_animation_text(value, part, index) if isinstance(value, str) else value
                try:
                    tree = parse(expression)
                except ExpressionError as error:
                    issues.append('expression not parsed for %s: %s' % (key, error))
                    continue
                found.append(Assignment(key, target, variable, tree))
    return found


def _axis(variable):
    """Axis index (0, 1, 2) of a part variable such as 'tx' or 'rz'."""
    return 'xyz'.index(variable[1])


class AnimationCompiler:
    """Compile JEM animations into Molang variables and one looping Bedrock animation.

    OptiFine runs every assignment each frame, in order. Each becomes a
    'v.<prefix>_<part>_<variable> = ...;' line of the entity's pre_animation
    script, and the animation reads those variables into bone rotation,
    position and scale.
    """

    def __init__(self, builder, *, variable_prefix='cem'):
        self.builder = builder
        self.model = builder.model
        self.target = builder.target
        self.prefix = variable_prefix
        self.issues = builder.issues
        self.parts_by_id = {}           # JEM part id -> the first part with that id
        for part in self.model.parts:
            self._index_parts(part)
        self.vanilla_names = (set(self.target.vanilla_parts)
                              | {part.vanilla_part for part in self.model.parts if part.vanilla_part} | {'root'}
                              | {key.split('.', 1)[0] for key in self.target.formulas})
        self.assignments = _collect_assignments(self.model, self.issues)
        for assignment in self.assignments:
            self._classify(assignment)
        self.unresolved = set()
        self.unmapped = set()
        self.approximations = set()
        self.statements = []
        self.initialize = []
        self.channels = {}              # (kind, part name) -> the part variables the animations set
        self.folded = {}                # (custom part id, variable) -> constant baked into the geometry

    def _index_parts(self, part):
        self.parts_by_id.setdefault(part.id, part)
        for child in part.children:
            self._index_parts(child)

    def _classify(self, assignment):
        if assignment.target in ('var', 'varb', 'render'):
            assignment.kind = assignment.target
        elif assignment.variable in PART_VARIABLES:
            assignment.kind = self._part_kind(assignment.target)
            assignment.target = self._part_id(assignment.target)

    def _part_kind(self, name):
        """'custom' or 'vanilla' for a part name, None when unknown (OptiFine's order: this, vanilla, custom)."""
        if name.startswith(_THIS_MARKER):
            return 'custom'
        if name in self.vanilla_names:
            return 'vanilla'
        if name in self.parts_by_id:
            return 'custom'
        return None

    def _part_id(self, name):
        """The part name itself, or for a 'this' marker the id of the top-level part it stands for."""
        if name.startswith(_THIS_MARKER):
            return self.model.parts[int(name[len(_THIS_MARKER):])].id
        return name

    def detached_parts(self):
        """Vanilla parts the animations set: they leave the vanilla pose and follow the animation."""
        return {assignment.target for assignment in self.assignments if assignment.kind == 'vanilla'}

    def molang_variable(self, *names):
        """The Molang variable v.<prefix>_<name>_... of this model."""
        return 'v.' + '_'.join([self.prefix] + [_identifier_safe(name) for name in names])

    def _part_variable(self, kind, name, variable):
        if kind == 'vanilla':
            return self.molang_variable('vanilla', name, variable)
        return self.molang_variable(name, variable)

    def uses(self, identifier):
        """True when any expression reads identifier."""
        return any(identifier in identifiers(assignment.expression) for assignment in self.assignments)

    def compile(self):
        """Translate every assignment into pre_animation statements and initialize lines; returns self."""
        self._fold_constants()
        assigned = set()
        read_custom = set()
        for assignment in self.assignments:
            self._compile_assignment(assignment, assigned, read_custom)
        # Custom part values the expressions read start at the part's rest pose.
        for part_id, variable in sorted(read_custom):
            value = self._custom_rest_value(part_id, variable)
            self.initialize.append('%s = %s;' % (self.molang_variable(part_id, variable), _constant_text(value)))
        return self

    def _fold_constants(self):
        """A custom part translation or rotation set once, to a constant, becomes the part's rest pose."""
        assignments_by_channel = {}
        for assignment in self.assignments:
            if assignment.kind == 'custom':
                channel = (assignment.target, assignment.variable)
                assignments_by_channel.setdefault(channel, []).append(assignment)
        for channel, entries in assignments_by_channel.items():
            if len(entries) == 1 and is_constant(entries[0].expression) and channel[1] in _POSE_VARIABLES:
                self.folded[channel] = evaluate(entries[0].expression, {})
        for (part_id, variable), value in self.folded.items():
            self._apply_fold(part_id, variable, value)

    def _apply_fold(self, part_id, variable, value):
        bone = self.builder.part_bones.get(part_id)
        if bone is None:
            return
        part = self.parts_by_id[part_id]
        axis = _axis(variable)
        if variable in _ROTATION_VARIABLES:
            rotation = list(part.rotation)
            rotation[axis] = value
            part.rotation = tuple(rotation)
            degrees = _degrees_list(rotation)
            if any(degrees):
                bone['rotation'] = degrees
            else:
                bone.pop('rotation', None)
        else:
            delta = value - part.pivot[axis]
            pivot = list(part.pivot)
            pivot[axis] = value
            part.pivot = tuple(pivot)
            self._move_part(part, axis, delta)

    def _move_part(self, part, axis, delta):
        """Move a part's bone and cubes, and every part below it, by delta along a Java axis."""
        if not delta:
            return
        # Java y points down, Bedrock y up.
        offset = (delta if axis != 1 else -delta) * self.target.frame_scale
        pending = [part]
        while pending:
            current = pending.pop()
            bone = self.builder.part_bones.get(current.id)
            if bone is not None:
                bone['pivot'][axis] = _rounded(bone['pivot'][axis] + offset)
                for cube in bone.get('cubes', []):
                    cube['origin'][axis] = _rounded(cube['origin'][axis] + offset)
            pending.extend(current.children)

    def _compile_assignment(self, assignment, assigned, read_custom):
        translator = Translator(lambda identifier: self._resolve(identifier, assigned, read_custom))
        try:
            molang = translator.emit(assignment.expression)
        except ExpressionError as error:
            self.issues.append('expression not translated for %s: %s' % (assignment.key, error))
            return
        self.unresolved |= translator.unresolved
        self.approximations |= translator.approximations
        kind = assignment.kind
        if kind in ('var', 'varb'):
            if kind == 'varb':
                variable = self.molang_variable('b', assignment.variable)
            else:
                variable = self.molang_variable(assignment.variable)
            self.statements.append('%s = %s;' % (variable, molang))
            return
        if kind == 'render':
            self.issues.append('render variable not converted: ' + assignment.key)
            return
        if assignment.variable not in PART_VARIABLES:
            self.issues.append('unknown model variable in key: ' + assignment.key)
            return
        if kind is None:
            self.issues.append('animation targets an unknown part: ' + assignment.key)
            return
        assigned.add((kind, assignment.target, assignment.variable))
        if kind == 'custom' and (assignment.target, assignment.variable) in self.folded:
            return
        variable = self._part_variable(kind, assignment.target, assignment.variable)
        self.statements.append('%s = %s;' % (variable, molang))
        self.channels.setdefault((kind, assignment.target), set()).add(assignment.variable)

    def _resolve(self, identifier, assigned, read_custom):
        """Molang for an identifier read by an expression, or None when it means nothing here.

        assigned holds the part variables set by earlier assignments; read_custom
        collects the custom part values read, which then need an initial value.
        """
        if '.' in identifier:
            return self._resolve_dotted(identifier, assigned, read_custom)
        if identifier == 'id':
            # OptiFine's id is a number unique to the entity: a random number drawn once stands in.
            self.initialize.append('%s = math.random(0, 1000000);' % self.molang_variable('id'))
            return self.molang_variable('id')
        if identifier == 'frame_counter':
            return self.molang_variable('frame_counter')
        if identifier in MODEL_VARIABLES:
            return MODEL_VARIABLES[identifier]
        if identifier in UNMAPPED_VARIABLES:
            self.unmapped.add(identifier)
            return UNMAPPED_VARIABLES[identifier]
        return None

    def _resolve_dotted(self, identifier, assigned, read_custom):
        head, tail = identifier.split('.', 1)
        if head == 'var':
            return self.molang_variable(tail)
        if head == 'varb':
            return self.molang_variable('b', tail)
        if head == 'render':
            self.unmapped.add(identifier)
            return '0'
        if tail not in PART_VARIABLES:
            return None
        kind = self._part_kind(head)
        name = self._part_id(head)
        if kind == 'vanilla':
            return self._vanilla_value(name, tail, assigned, read_custom)
        if kind == 'custom':
            if (name, tail) in self.folded:
                return _constant_text(self.folded[(name, tail)])
            read_custom.add((name, tail))
            return self.molang_variable(name, tail)
        return None

    def _vanilla_value(self, name, variable, assigned, read_custom):
        """A vanilla part value: as assigned earlier, else its vanilla motion, else its rest value."""
        if ('vanilla', name, variable) in assigned:
            return self._part_variable('vanilla', name, variable)
        formula = self.target.formulas.get(name + '.' + variable)
        if formula is not None:
            nested = Translator(lambda inner: self._resolve(inner, assigned, read_custom))
            return '(' + nested.emit(parse(formula)) + ')'
        self.approximations.add('vanilla value %s.%s uses its rest value' % (name, variable))
        return _constant_text(self._vanilla_rest_value(name, variable))

    def _custom_rest_value(self, part_id, variable):
        """A custom part variable at rest: the part's JEM pivot, rotation or scale, or 1 for visibility."""
        part = self.parts_by_id[part_id]
        if variable in _TRANSLATION_VARIABLES:
            return part.pivot[_axis(variable)]
        if variable in _ROTATION_VARIABLES:
            return part.rotation[_axis(variable)]
        if variable in _SCALE_VARIABLES:
            return part.scale
        return 1.0

    def _vanilla_rest_value(self, name, variable):
        """A vanilla part variable at rest: its pivot, its rest rotation, or 1 for scale and visibility."""
        if name == 'root':
            return 1.0 if variable[0] == 's' or variable.startswith('visible') else 0.0
        known = self.target.vanilla_parts.get(name, {})
        if variable in _TRANSLATION_VARIABLES:
            pivot = known.get('pivot')
            if pivot is None:
                top_level_part = next((part for part in self.model.parts if part.vanilla_part == name), None)
                if top_level_part is None:
                    return 0.0
                pivot = _vanilla_pose(top_level_part, self.target)[0]
            return float(pivot[_axis(variable)])
        if variable in _ROTATION_VARIABLES:
            return float(known.get('rotation', (0, 0, 0))[_axis(variable)]) * RADIANS_PER_DEGREE
        return 1.0

    def animation_bones(self):
        """Bedrock animation bones (rotation, position and scale channels) for every animated part."""
        bones = {}
        for (kind, name), variables in sorted(self.channels.items()):
            if kind == 'custom':
                bone = self.builder.part_bones.get(name)
                if bone is None:
                    continue
                bone_name = bone['name']
                part = self.parts_by_id[name]
                rest_pivot, rest_rotation, rest_scale = part.pivot, part.rotation, part.scale
            else:
                frame = self.builder.vanilla_part_frames.get(name)
                if frame is None or not frame.bone:
                    self.issues.append('vanilla part %s is animated but has no converted bone' % name)
                    continue
                bone_name = frame.bone
                rest_pivot = tuple(self._vanilla_rest_value(name, variable) for variable in _TRANSLATION_VARIABLES)
                rest_rotation, rest_scale = frame.rest_rotation, 1.0
            channels = {}
            rotation = self._rotation_channel(kind, name, variables, rest_rotation)
            if any(value != 0 for value in rotation):
                channels['rotation'] = rotation
            position = self._position_channel(kind, name, variables, rest_pivot)
            if any(value != 0 for value in position):
                channels['position'] = position
            scale = self._scale_channel(kind, name, variables, rest_scale)
            if scale is not None:
                channels['scale'] = scale
            if channels:
                bones[bone_name] = channels
        # A scaled top-level part keeps its scale even when nothing animates it.
        for part in self.model.parts:
            bone = self.builder.part_bones.get(part.id)
            if bone is not None and part.scale != 1.0 and bone['name'] not in bones:
                bones[bone['name']] = {'scale': [_constant_text(part.scale)] * 3}
        return bones

    def _rotation_channel(self, kind, name, variables, rest_rotation):
        # The channel adds to the bone's rest rotation, so the rest is subtracted from the assigned value.
        rotation = [0, 0, 0]
        for index, axis in enumerate('xyz'):
            variable = 'r' + axis
            rest_degrees = rest_rotation[index] * DEGREES_PER_RADIAN
            if variable in variables:
                value = self._part_variable(kind, name, variable)
                rotation[index] = '%s * %s%s' % (value, repr(DEGREES_PER_RADIAN), _offset_text(-rest_degrees))
            elif kind == 'vanilla' and name + '.' + variable in self.target.formulas:
                # The detached part still follows its vanilla motion on axes the model leaves alone.
                translator = Translator(lambda inner: self._resolve(inner, set(), set()))
                formula = translator.emit(parse(self.target.formulas[name + '.' + variable]))
                rotation[index] = '(%s) * %s%s' % (formula, repr(DEGREES_PER_RADIAN), _offset_text(-rest_degrees))
        return rotation

    def _position_channel(self, kind, name, variables, rest_pivot):
        # The channel moves the bone from its rest pivot.
        position = [0, 0, 0]
        for index, axis in enumerate('xyz'):
            variable = 't' + axis
            if variable not in variables:
                continue
            rest = rest_pivot[index]
            if axis == 'y':
                # Java y points down: a larger ty moves the part down.
                term = '(%s - %s)' % (_constant_text(rest), self._part_variable(kind, name, 'ty'))
            else:
                term = '(%s%s)' % (self._part_variable(kind, name, variable), _offset_text(-rest))
            if self.target.frame_scale != 1.0:
                term = '%s * %s' % (term, _constant_text(self.target.frame_scale))
            position[index] = term
        return position

    def _scale_channel(self, kind, name, variables, rest_scale):
        """Scale terms, with visibility as a zero scale; None when neither is animated."""
        visible = 'visible' in variables
        terms = []
        for variable in _SCALE_VARIABLES:
            if variable in variables:
                term = self._part_variable(kind, name, variable)
            else:
                term = _constant_text(rest_scale)
            if visible:
                # Bedrock animations have no visibility channel: a hidden part is scaled to nothing.
                term = '(%s) * (%s != 0)' % (term, self._part_variable(kind, name, 'visible'))
            terms.append(term)
        scale = terms if visible or any(variable in variables for variable in _SCALE_VARIABLES) else None
        if 'visible_boxes' in variables:
            self.issues.append('visible_boxes of %s is converted as visible (children hide too)' % name)
            shown = '(%s != 0)' % self._part_variable(kind, name, 'visible_boxes')
            scale = ['(%s) * %s' % (term, shown) for term in (scale or [_constant_text(rest_scale)] * 3)]
        return scale


def _constant_text(value):
    """A number as Molang text: whole numbers without a fraction, others rounded to 9 decimals."""
    value = float(value)
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(round(value, 9))


def _offset_text(value):
    """' + n' or ' - n' to append to a Molang term; empty for zero."""
    if not value:
        return ''
    return (' + ' if value > 0 else ' - ') + _constant_text(abs(value))


# --- conversion ---

def convert(model, target, *, variable_prefix='cem'):
    """Return geometry, animation bones, Molang scripts and a report for one model."""
    builder = GeometryBuilder(model, target)
    compiler = AnimationCompiler(builder, variable_prefix=variable_prefix)
    # Vanilla parts the animations move are built apart from their vanilla bone, so find them first.
    detached = frozenset(compiler.detached_parts())
    geometry = builder.build(detached)
    compiler.compile()
    animation_bones = compiler.animation_bones()
    pre_animation = []
    if compiler.uses('frame_counter'):
        counter = compiler.molang_variable('frame_counter')
        pre_animation.append('%s = %s + 1;' % (counter, counter))
    pre_animation.extend(compiler.statements)
    part_bone_names = {bone['name'] for bone in builder.part_bones.values()}
    geometry_bones = geometry['minecraft:geometry'][0]['bones']
    report = {'parts': len(model.parts),
              'custom_bones': len(part_bone_names),
              'cubes': sum(len(bone.get('cubes', [])) for bone in geometry_bones if bone['name'] in part_bone_names),
              'assignments': len(compiler.assignments),
              'compiled_statements': len(compiler.statements),
              'folded_constants': len(compiler.folded),
              'animated_bones': len(animation_bones),
              'detached_vanilla_parts': sorted(detached),
              'unresolved_identifiers': sorted(compiler.unresolved),
              'unmapped_variables': sorted(compiler.unmapped),
              'approximations': sorted(compiler.approximations),
              'issues': sorted(set(builder.issues))}
    return {'geometry': geometry, 'animation_bones': animation_bones, 'pre_animation': pre_animation,
            'initialize': sorted(set(compiler.initialize)), 'report': report}
