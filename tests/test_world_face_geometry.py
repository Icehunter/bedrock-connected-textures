"""Check carrier winding, placement and UVs in the yaw-zero entity world frame."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from connected_build import compensate_carrier_geometry, orient_geometry


FACE_ORDER = ('north', 'east', 'south', 'west', 'up', 'down')
NORMALS = {'north': (0, 0, -1), 'east': (1, 0, 0), 'south': (0, 0, 1),
           'west': (-1, 0, 0), 'up': (0, 1, 0), 'down': (0, -1, 0)}
# Where the engine stands each face's carrier, in blocks from the block's corner.
ACTOR_OFFSETS = {'north': (.5, .5, -.002), 'east': (1.002, .5, .5),
                 'south': (.5, .5, 1.002), 'west': (-.002, .5, .5),
                 'up': (.5, 1.002, .5), 'down': (.5, -.002, .5)}
BLOCK = (-38, -60, -40)


def old_carrier():
    # Serialized coordinates from a pre-compensation basalt carrier.
    planes = {
        'north': ([-8, -8, -.0305], [16, 16, .001]),
        'east': ([.0295, -8, -8], [.001, 16, 16]),
        'south': ([-8, -8, .0295], [16, 16, .001]),
        'west': ([-.0305, -8, -8], [.001, 16, 16]),
        'up': ([-8, .013875, -8], [16, .001, 16]),
        'down': ([-8, -.0305, -8], [16, .001, 16]),
    }
    bones = []
    for face, (origin, size) in planes.items():
        if face in FACE_ORDER[:4]:
            uv = {'uv': [16, 0], 'uv_size': [-16, 16]}
        else:
            uv = {'uv': [0, 0], 'uv_size': [16, 16]}
        bones.append({'name': face, 'pivot': [0, 0, 0],
                      'cubes': [{'origin': origin, 'size': size, 'uv': {face: uv}}]})
    return {'format_version': '1.21.0', 'minecraft:geometry': [{
        'description': {'identifier': 'geometry.ctm_fixture_0'}, 'bones': bones}]}


def world_vertices(bone, block):
    cube = bone['cubes'][0]
    origin_x, origin_y, origin_z = cube['origin']
    size_x, size_y, size_z = cube['size']
    # Bedrock geometry import reflects exported X bounds. Vertex order is the
    # primary Blockbench setShape implementation, not the generator's bounds.
    x0, y0, z0 = -origin_x - size_x, origin_y, origin_z
    x1, y1, z1 = x0 + size_x, y0 + size_y, z0 + size_z
    face = bone['name']
    vertices = {
        'east': [(x1, y1, z1), (x1, y1, z0), (x1, y0, z1), (x1, y0, z0)],
        'west': [(x0, y1, z0), (x0, y1, z1), (x0, y0, z0), (x0, y0, z1)],
        'up': [(x0, y1, z0), (x1, y1, z0), (x0, y1, z1), (x1, y1, z1)],
        'down': [(x0, y0, z1), (x1, y0, z1), (x0, y0, z0), (x1, y0, z0)],
        'south': [(x0, y1, z1), (x1, y1, z1), (x0, y0, z1), (x1, y0, z1)],
        'north': [(x1, y1, z0), (x0, y1, z0), (x1, y0, z0), (x0, y0, z0)],
    }[face]
    half_turns = 1 + bone.get('rotation', [0, 0, 0])[1] // 180
    if half_turns % 2:
        vertices = [(-x, y, -z) for x, y, z in vertices]
    actor = [block[axis] + ACTOR_OFFSETS[face][axis] for axis in range(3)]
    return [tuple(actor[axis] + point[axis] / 16 for axis in range(3)) for point in vertices]


def normal(vertices):
    # Blockbench triangle indices are [0, 2, 1, 2, 3, 1].
    first_edge = [vertices[2][axis] - vertices[0][axis] for axis in range(3)]
    second_edge = [vertices[1][axis] - vertices[0][axis] for axis in range(3)]
    cross = (first_edge[1] * second_edge[2] - first_edge[2] * second_edge[1],
             first_edge[2] * second_edge[0] - first_edge[0] * second_edge[2],
             first_edge[0] * second_edge[1] - first_edge[1] * second_edge[0])
    return tuple(round(value) for value in cross)


def uv_vertices(bone):
    face = bone['name']
    uv = bone['cubes'][0]['uv'][face]
    u, v = uv['uv']
    width, height = uv['uv_size']
    # Bedrock's top/bottom face UV import exchanges both rectangle endpoints.
    if face in ('up', 'down'):
        u, v, width, height = u + width, v + height, -width, -height
    corners = [(u, v), (u + width, v), (u, v + height), (u + width, v + height)]
    for _ in range(uv.get('uv_rotation', 0) // 90):
        corners = [corners[2], corners[0], corners[3], corners[1]]
    return corners


def java_uv(face, point, block):
    x, y, z = [(point[axis] - block[axis]) * 16 for axis in range(3)]
    return {'north': (16 - x, 16 - y), 'south': (x, 16 - y),
            'west': (z, 16 - y), 'east': (16 - z, 16 - y),
            'up': (x, z), 'down': (x, 16 - z)}[face]


def oriented_uv(u, v, orientation):
    return [(u, v), (16 - v, u), (16 - u, 16 - v), (v, 16 - u),
            (16 - u, v), (v, u), (u, 16 - v), (16 - v, 16 - u)][orientation]


class WorldFaceGeometryTests(unittest.TestCase):
    def test_old_side_quads_are_back_facing_and_north_is_nearly_coplanar(self):
        bones = old_carrier()['minecraft:geometry'][0]['bones']
        for bone in bones[:4]:
            inward = tuple(-value for value in NORMALS[bone['name']])
            self.assertEqual(normal(world_vertices(bone, BLOCK)), inward)
        north_z = world_vertices(bones[0], BLOCK)[0][2]
        self.assertAlmostEqual(BLOCK[2] - north_z, .00009375)

    def test_all_faces_have_outward_winding_and_exact_clearance(self):
        geometry = compensate_carrier_geometry(old_carrier())
        for bone in geometry['minecraft:geometry'][0]['bones']:
            face = bone['name']
            vertices = world_vertices(bone, BLOCK)
            self.assertEqual(normal(vertices), NORMALS[face], face)
            axis = next(index for index, value in enumerate(NORMALS[face]) if value)
            direction = NORMALS[face][axis]
            clearance = 3 / 1024 if face == 'up' else 1 / 256
            plane = BLOCK[axis] + (1 if direction > 0 else 0) + direction * clearance
            for point in vertices:
                self.assertAlmostEqual(point[axis], plane, msg=face)
            for tangent in set(range(3)) - {axis}:
                self.assertAlmostEqual(min(point[tangent] for point in vertices), BLOCK[tangent], msg=face)
                self.assertAlmostEqual(max(point[tangent] for point in vertices), BLOCK[tangent] + 1, msg=face)

    def test_world_vertices_match_all_java_uv_transforms(self):
        repaired = compensate_carrier_geometry(old_carrier())
        for orientation in range(8):
            geometry = orient_geometry(repaired, 'geometry.ctm_fixture_' + str(orientation), orientation)
            for bone in geometry['minecraft:geometry'][0]['bones']:
                face = bone['name']
                for point, actual in zip(world_vertices(bone, BLOCK), uv_vertices(bone)):
                    canonical = java_uv(face, point, BLOCK)
                    expected = oriented_uv(*canonical, orientation)
                    for actual_axis, expected_axis in zip(actual, expected):
                        self.assertAlmostEqual(actual_axis, expected_axis, msg=str((face, orientation, point)))

    def test_serialized_orientation_is_preserved_and_repair_is_idempotent(self):
        for orientation in range(8):
            identifier = 'geometry.ctm_fixture_' + str(orientation)
            original = orient_geometry(old_carrier(), identifier, orientation)
            snapshot = copy.deepcopy(original)
            repaired = compensate_carrier_geometry(original)
            self.assertEqual(original, snapshot)
            self.assertEqual(compensate_carrier_geometry(repaired), repaired)
            expected = orient_geometry(compensate_carrier_geometry(old_carrier()), identifier, orientation)
            self.assertEqual(repaired, expected)


if __name__ == '__main__':
    unittest.main()
