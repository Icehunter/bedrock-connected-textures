"""Java block model elements as Bedrock block geometry.

Coordinates follow Blockbench's Java-to-Bedrock block conversion, the tool pack
porters use: X and Z move to the block centre (-8), the Bedrock export mirrors
X (origin x = -(x + size)) and the pivot's x, and negates the X and Y
rotations. Top and bottom face UVs are flipped.

Bedrock rejects block geometry whose rotated vertices do not fit one 30x30x30
pixel box that also holds the block core, so an oversized model (bushy leaves,
tall plants) is scaled uniformly about the block centre until it fits, and no
further.
"""
import math

# Bedrock block geometry must fit inside one box this many pixels wide on every axis.
BOX_SIZE = 30
# (low, high) per axis in block space (x and z centred, y up from the bottom)
# that the box has to hold besides the model.
BLOCK_CORE = ((-7.0, 7.0), (1.0, 15.0), (-7.0, 7.0))
# Slack kept inside the box, so a fitted model stays clear of the limit.
FIT_MARGIN = 0.25
FACES = ('north', 'south', 'east', 'west', 'up', 'down')
# The element axes (0 x, 1 y, 2 z) each face spans.
FACE_SPAN_AXES = {'north': (0, 1), 'south': (0, 1), 'east': (2, 1), 'west': (2, 1), 'up': (0, 2), 'down': (0, 2)}
BLOCK_CENTRE = (8, 8, 8)


def shaded(element):
    """Whether Java dims the element's faces by direction.

    "shade": false, or (Java 26.3+) "shade_direction_override": "up", lights
    every face like a top face, so nothing is dimmed.
    """
    return element.get('shade', True) is not False and element.get('shade_direction_override') != 'up'


def element_angles(rotation):
    """[x, y, z] degrees of a Java element rotation (the single-axis form or the 1.21.11 multi-axis form)."""
    rotation = rotation or {}
    if 'axis' in rotation:
        angles = [0.0, 0.0, 0.0]
        angles['xyz'.index(rotation['axis'])] = float(rotation.get('angle', 0))
        return angles
    return [float(rotation.get(axis, 0)) for axis in 'xyz']


def _rotate(point, angles, origin):
    """point turned about origin by [x, y, z] degrees: X first, then Y, then Z (Rz * Ry * Rx)."""
    x, y, z = (point[axis] - origin[axis] for axis in range(3))
    x_angle, y_angle, z_angle = (math.radians(value) for value in angles)
    if x_angle:
        cosine, sine = math.cos(x_angle), math.sin(x_angle)
        y, z = cosine * y - sine * z, sine * y + cosine * z
    if y_angle:
        cosine, sine = math.cos(y_angle), math.sin(y_angle)
        x, z = cosine * x + sine * z, -sine * x + cosine * z
    if z_angle:
        cosine, sine = math.cos(z_angle), math.sin(z_angle)
        x, y = cosine * x - sine * y, sine * x + cosine * y
    return [x + origin[0], y + origin[1], z + origin[2]]


def _scaled(element, scale):
    """The element scaled about the block centre; its rotation origin moves with it."""
    if scale == 1:
        return element
    centre = (8.0, 8.0, 8.0)

    def moved(point):
        return [centre[axis] + (point[axis] - centre[axis]) * scale for axis in range(3)]

    result = {**element, 'from': moved(element['from']), 'to': moved(element['to'])}
    if element.get('rotation'):
        rotation = element['rotation']
        result['rotation'] = {**rotation, 'origin': moved(rotation.get('origin', BLOCK_CENTRE))}
    return result


def vertices(element, model_rotation=(0, 0)):
    """The element's eight corners after its own rotation and the blockstate's (x, y) model rotation.

    Corners are in Bedrock block space: the centre of the block's bottom is 0
    and the axes are Java's.
    """
    low, high = element['from'], element['to']
    rotation = element.get('rotation') or {}
    angles, origin = element_angles(rotation), rotation.get('origin', BLOCK_CENTRE)
    corners = [[x, y, z] for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])]
    # The blockstate turns the model about the block centre, X then Y; Java turns
    # clockwise seen from +y, the opposite of the rotation above.
    blockstate_turn = [-float(model_rotation[0]), -float(model_rotation[1]), 0.0]
    result = []
    for corner in corners:
        point = _rotate(corner, angles, origin)
        point = _rotate(point, blockstate_turn, BLOCK_CENTRE)
        result.append([point[0] - 8, point[1], point[2] - 8])
    return result


def fits(points):
    """Whether one Bedrock geometry box holds every point (in block space) and the block core."""
    for axis in range(3):
        values = [point[axis] for point in points]
        low, high = min(values), max(values)
        core_low, core_high = BLOCK_CORE[axis]
        if max(high, core_high) - min(low, core_low) > BOX_SIZE - FIT_MARGIN:
            return False
    return True


def fit_scale(elements, model_rotations=((0, 0),)):
    """Largest uniform scale (at most 1) about the block centre that fits every element in every model rotation."""
    def fits_at(scale):
        return all(fits([corner for element in elements for corner in vertices(_scaled(element, scale), rotation)])
                   for rotation in model_rotations)

    if fits_at(1.0):
        return 1.0
    # Bisection; 30 halvings settle the scale far below a pixel.
    low, high = 0.0, 1.0
    for _ in range(30):
        middle = (low + high) / 2
        if fits_at(middle):
            low = middle
        else:
            high = middle
    return low


def default_uv(face, low, high):
    """Java's automatic [u0, v0, u1, v1] for a face without a "uv"."""
    x0, y0, z0 = low
    x1, y1, z1 = high
    return {'down': [x0, 16 - z1, x1, 16 - z0], 'up': [x0, z0, x1, z1],
            'north': [16 - x1, 16 - y1, 16 - x0, 16 - y0], 'south': [x0, 16 - y1, x1, 16 - y0],
            'west': [z0, 16 - y1, z1, 16 - y0], 'east': [16 - z1, 16 - y1, 16 - z0, 16 - y0]}[face]


# Where the side faces under a raised top face sample its edge, in texture units inside the edge
# (a texture is 16 wide): less than one texel of a 1024 pixel texture, so they take the edge texels.
_EDGE_SAMPLE = .01
# Thickness of the turned cubes that close a raised top face's west and east sides, in pixels.
_SIDE_THICKNESS = .001


def close_top_face(plane, bottom):
    """Cubes that close the gap under a raised top face, down to the face it covers at `bottom`.

    plane is a Bedrock cube with an "up" face and no rotation. The plane becomes a block from
    `bottom` to its top, whose north and south faces repeat the top's texels along those edges.
    A face UV cannot turn a row of texels upright without uv_rotation, which samples outside the
    texture in Bedrock, so the west and east sides are thin cubes turned a quarter turn about the
    north-south axis: their top and bottom faces, standing upright, take the texels of the top's
    column there. Seen from the side, the raised plane then meets the lower face next to it.
    """
    (x, y, z), (width, height, depth) = plane['origin'], plane['size']
    top = plane['uv']['up']
    if 'uv_rotation' in top or 'rotation' in plane:
        raise ValueError('Only an unturned top face can be closed')
    (u, v), (du, dv) = top['uv'], top['uv_size']
    named = {'material_instance': top['material_instance']} if 'material_instance' in top else {}

    def strip(edge, inward):
        """Where a strip along an edge samples, right inside the edge, and its width there.

        The width is 0: every texel row of the strip reads the same edge row. A strip seen edge on
        would otherwise squeeze its rows into a fraction of a screen pixel, so the GPU would pick a
        small mip level, where neighbouring atlas textures blend in as coloured dashes.
        """
        return edge + (_EDGE_SAMPLE if inward > 0 else -_EDGE_SAMPLE), 0

    top_y = y + height
    # The top's u grows from the low x side, its v from the high z side (Bedrock flips top faces).
    # A north face reads u from low x, a south face from high x.
    north_v, north_size = strip(v + dv, -dv)
    south_v, south_size = strip(v, dv)
    block = {'origin': [x, bottom, z], 'size': [width, top_y - bottom, depth], 'uv': {
        'up': top,
        'north': {'uv': [u, north_v], 'uv_size': [du, north_size], **named},
        'south': {'uv': [u + du, south_v], 'uv_size': [-du, south_size], **named}}}
    cubes = [block]
    rise = top_y - bottom
    centre_y = (top_y + bottom) / 2
    for side_x, edge_u, inward in ((x + width - _SIDE_THICKNESS / 2, u + du, -du), (x + _SIDE_THICKNESS / 2, u, du)):
        side_u, side_size = strip(edge_u, inward)
        # Turning about z keeps z, so the faces read v along z exactly as the top does; a bottom
        # face reads v the other way, so it runs back from the far end. Whichever of the two
        # faces turns outward shows.
        cubes.append({
            'origin': [side_x - rise / 2, centre_y - _SIDE_THICKNESS / 2, z],
            'size': [rise, _SIDE_THICKNESS, depth],
            'pivot': [side_x, centre_y, z + depth / 2],
            'rotation': [0, 0, 90],
            'uv': {'up': {'uv': [side_u, v], 'uv_size': [side_size, dv], **named},
                   'down': {'uv': [side_u, v + dv], 'uv_size': [side_size, -dv], **named}}})
    return cubes


def _has_area(face, size):
    """Whether a face of an element of this size covers any area; Java draws nothing for a flat one."""
    return all(abs(size[axis]) > 1e-6 for axis in FACE_SPAN_AXES[face])


def cube(element, material=lambda face, data: None):
    """One Java element as a Bedrock cube, converted like Blockbench.

    material(face, face data) names the block material instance a face uses;
    faces without one use the block's "*" instance ("*" itself is never written
    into the geometry, as in Blockbench). Faces without area are left out.
    """
    low, high = element['from'], element['to']
    size = [high[axis] - low[axis] for axis in range(3)]
    result = {'origin': [8 - high[0], low[1], low[2] - 8], 'size': size, 'uv': {}}
    rotation = element.get('rotation') or {}
    angles = element_angles(rotation)
    if any(angles):
        origin = rotation.get('origin', BLOCK_CENTRE)
        result['pivot'] = [8 - origin[0], origin[1], origin[2] - 8]
        result['rotation'] = [-angles[0], -angles[1], angles[2]]
    for face, data in element.get('faces', {}).items():
        if face in FACES and _has_area(face, size):
            result['uv'][face] = _face_uv(face, data, low, high, material)
    return result


def _face_uv(face, data, low, high, material):
    u0, v0, u1, v1 = data.get('uv') or default_uv(face, low, high)
    if face in ('up', 'down'):
        # Blockbench flips top and bottom faces: their UV runs back from the far corner.
        uv = {'uv': [u1, v1], 'uv_size': [u0 - u1, v0 - v1]}
    else:
        uv = {'uv': [u0, v0], 'uv_size': [u1 - u0, v1 - v0]}
    if data.get('rotation', 0) % 360:
        uv['uv_rotation'] = data['rotation'] % 360
    name = material(face, data)
    if name:
        uv['material_instance'] = name
    return uv


def geometry_document(identifier, bones):
    """A block geometry file with one model; visible bounds reach a block past every side."""
    description = {'identifier': identifier, 'texture_width': 16, 'texture_height': 16,
                   'visible_bounds_width': 3, 'visible_bounds_height': 3, 'visible_bounds_offset': [0, 0.5, 0]}
    return {'format_version': '1.21.0', 'minecraft:geometry': [{'description': description, 'bones': bones}]}


def geometry(identifier, variants, material=lambda face, data: None):
    """Block geometry with one bone per variant, all scaled by one common factor so they keep the same size.

    variants: [(bone name, elements, (x rotation, y rotation))]. Returns (geometry, scale).
    """
    scale = fit_scale([element for _, elements, _ in variants for element in elements],
                      [rotation for _, _, rotation in variants])
    bones = []
    for name, elements, rotation in variants:
        cubes = [cube(_scaled(element, scale), material) for element in elements]
        bone = {'name': name, 'pivot': [0, 8, 0], 'cubes': cubes}
        if rotation[0] % 360 or rotation[1] % 360:
            bone['rotation'] = [-rotation[0], -rotation[1], 0]
        bones.append(bone)
    return geometry_document(identifier, bones), scale
