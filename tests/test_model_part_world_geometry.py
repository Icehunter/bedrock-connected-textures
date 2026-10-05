"""Compare source model vertices with serialized Bedrock geometry in world space."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from connected_build import model_part_geometry


# Where the engine stands each face's carrier, in blocks from the block's corner.
ACTORS = {'north': (.5, .5, -.002), 'east': (1.002, .5, .5), 'south': (.5, .5, 1.002),
          'west': (-.002, .5, .5), 'up': (.5, 1.002, .5), 'down': (.5, -.002, .5)}


def rotate(point, pivot, angles):
    """Turn a point about the pivot by the x, then the y, then the z angle, in degrees."""
    x, y, z = [point[axis] - pivot[axis] for axis in range(3)]
    for axis, angle in enumerate(angles):
        cosine = math.cos(math.radians(angle))
        sine = math.sin(math.radians(angle))
        if axis == 0:
            y, z = cosine * y - sine * z, sine * y + cosine * z
        if axis == 1:
            x, z = cosine * x + sine * z, -sine * x + cosine * z
        if axis == 2:
            x, y = cosine * x - sine * y, sine * x + cosine * y
    return [x + pivot[0], y + pivot[1], z + pivot[2]]


def corners(start, end, face):
    x0, y0, z0 = start
    x1, y1, z1 = end
    return {'north': [(x1, y1, z0), (x0, y1, z0), (x1, y0, z0), (x0, y0, z0)],
            'up': [(x0, y1, z0), (x1, y1, z0), (x0, y1, z1), (x1, y1, z1)]}[face]


def import_rotation(element):
    """Pivot and angles of a cube or bone after Bedrock's import mirrors X."""
    angles = element.get('rotation', [0, 0, 0])
    pivot = element.get('pivot', [0, 0, 0])
    return [-pivot[0], pivot[1], pivot[2]], [-angles[0], -angles[1], angles[2]]


def imported_vertices(geometry, face, anchor, block):
    bones = geometry['minecraft:geometry'][0]['bones']
    cube = bones[-1]['cubes'][0]
    origin = cube['origin']
    size = cube['size']
    start = [-origin[0] - size[0], origin[1], origin[2]]
    vertices = corners(start, [start[axis] + size[axis] for axis in range(3)], face)
    pivot, angles = import_rotation(cube)
    vertices = [rotate(vertex, pivot, angles) for vertex in vertices]
    for bone in reversed(bones):
        pivot, angles = import_rotation(bone)
        vertices = [rotate(vertex, pivot, angles) for vertex in vertices]
    # Yaw-zero entity orientation is a separate half turn after the model frame.
    vertices = [rotate(vertex, [0, 0, 0], [0, 180, 0]) for vertex in vertices]
    return [[block[axis] + ACTORS[anchor][axis] + vertex[axis] / 16 for axis in range(3)] for vertex in vertices]


def java_vertices(part, face, angles, block):
    """The face's corners as Java places them: element rescale and turn, then the blockstate turn."""
    vertices = []
    for vertex in corners(part['from'], part['to'], face):
        pivot = part.get('rotation', {}).get('origin', [8, 8, 8])
        if part.get('rotation', {}).get('rescale'):
            units = [[int(index == axis) for index in range(3)] for axis in range(3)]
            scales = [1 / max(abs(value) for value in rotate(unit, [0, 0, 0], angles)) for unit in units]
            vertex = [pivot[axis] + (vertex[axis] - pivot[axis]) * scales[axis] for axis in range(3)]
        point = rotate(vertex, pivot, angles)
        state = part.get('modelRotation', {})
        point = rotate(point, [8, 8, 8], [-state.get('x', 0), -state.get('y', 0), 0])
        vertices.append([block[axis] + point[axis] / 16 for axis in range(3)])
    return vertices


class ModelPartWorldTests(unittest.TestCase):
    def test_element_pivot_blockstate_turn_and_carrier_anchors_preserve_world_vertices(self):
        block = (-38, -60, -44)
        cases = [
            ({'from': [0, 0, 8], 'to': [16, 16, 8], 'rotation': {'origin': [6, 7, 8], 'axis': 'y', 'angle': 45},
              'modelRotation': {'x': 0, 'y': 90}}, 'north', 'east', [0, 45, 0]),
            ({'from': [0, 16.02, 0], 'to': [16, 16.02, 16], 'modelRotation': {'x': 0, 'y': 270}},
             'up', 'up', [0, 0, 0]),
            ({'from': [-2, 5, 8], 'to': [19, 18, 8], 'rotation': {'origin': [5, 6, 7], 'x': -47.5, 'y': 22.5, 'z': 11},
              'modelRotation': {'x': 90, 'y': 180}}, 'north', 'down', [-47.5, 22.5, 11]),
            ({'from': [0, 0, 8], 'to': [16, 16, 8],
              'rotation': {'origin': [8, 8, 8], 'x': 22.5, 'y': -15, 'z': 45, 'rescale': True},
              'modelRotation': {'x': 0, 'y': 90}}, 'north', 'east', [22.5, -15, 45]),
        ]
        for part, face, anchor, angles in cases:
            part['faces'] = {face: {'texture': 'fixture', 'uv': [0, 0, 16, 16], 'worldFace': anchor}}
            actual = imported_vertices(model_part_geometry(part, face, 'geometry.fixture'), face, anchor, block)
            expected = java_vertices(part, face, angles, block)
            for actual_vertex, expected_vertex in zip(actual, expected):
                for actual_value, expected_value in zip(actual_vertex, expected_vertex):
                    self.assertAlmostEqual(actual_value, expected_value, msg=str((face, anchor, part)))


if __name__ == '__main__':
    unittest.main()
