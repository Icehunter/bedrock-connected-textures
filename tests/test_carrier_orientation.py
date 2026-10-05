"""Check emitted signed UV rectangles against the Java world-to-UV matrices."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from connected_build import FACES, orient_geometry

WORLD_CORNERS = [(0, 0), (16, 0), (0, 16), (16, 16)]


def bedrock_corners(uv, face):
    # Bedrock per-face import and corner rotation from Blockbench's own
    # js/formats/bedrock/bedrock.js and js/outliner/types/cube.js.
    u, v = uv['uv']
    width, height = uv['uv_size']
    if face in ('up', 'down'):
        u, v, width, height = u + width, v + height, -width, -height
    corners = [(u, v), (u + width, v), (u, v + height), (u + width, v + height)]
    for _ in range(uv.get('uv_rotation', 0) // 90):
        corners = [corners[2], corners[0], corners[3], corners[1]]
    return corners


def java_oriented(u, v, orientation):
    """Where Java's texture orientation sends a texture point: quarter turns, then the mirrored turns."""
    return [(u, v), (16 - v, u), (16 - u, 16 - v), (v, 16 - u),
            (16 - u, v), (v, u), (u, 16 - v), (16 - v, 16 - u)][orientation]


class GeometryOrientationTests(unittest.TestCase):
    def test_all_faces_match_all_eight_java_transforms_at_every_corner(self):
        bones = []
        for face in FACES:
            uv = {'uv': [16, 0], 'uv_size': [-16, 16]} if face in FACES[:4] else {'uv': [0, 0], 'uv_size': [16, 16]}
            bones.append({'name': face, 'cubes': [{'uv': {face: uv}}]})
        base = {'minecraft:geometry': [{'description': {}, 'bones': bones}]}
        for orientation in range(8):
            geometry = orient_geometry(base, 'geometry.test', orientation)
            for source, bone in zip(bones, geometry['minecraft:geometry'][0]['bones']):
                face = bone['name']
                canonical = bedrock_corners(source['cubes'][0]['uv'][face], face)
                actual = bedrock_corners(bone['cubes'][0]['uv'][face], face)
                for u, v in WORLD_CORNERS:
                    expected = java_oriented(u, v, orientation)
                    self.assertEqual(actual[canonical.index((u, v))], expected, (face, orientation, u, v))


if __name__ == '__main__':
    unittest.main()
