from pathlib import Path
import math
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from common import read_json, samples_path
from java_entity_models import (ROOT_BONE, Target, box_uv_faces, bedrock_face_uv, convert, load_model,
                                normalize_geometries, resolve_inheritance)
from java_entity_molang import molang_evaluate


def vanilla(bones, width=64, height=32):
    return {'description': {'identifier': 'geometry.test', 'texture_width': width, 'texture_height': height}, 'bones': bones}


def geometry_bones(result):
    return {bone['name']: bone for bone in result['geometry']['minecraft:geometry'][0]['bones']}


def rotation(angles):
    """Java model-part rotation (radians), applied Z * Y * X."""
    x, y, z = angles
    rx = np.array([[1, 0, 0], [0, math.cos(x), -math.sin(x)], [0, math.sin(x), math.cos(x)]])
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rz = np.array([[math.cos(z), -math.sin(z), 0], [math.sin(z), math.cos(z), 0], [0, 0, 1]])
    return rz @ ry @ rx


def to_bedrock(point):
    return np.array([point[0], 24 - point[1], point[2]])


def bedrock_world(bones):
    """World corners of every cube; a bone rotation in degrees is the Java rotation, y flipped."""
    flip = np.diag([1.0, -1.0, 1.0])
    cache = {}

    def matrix(name):
        if name in cache:
            return cache[name]
        bone = bones[name]
        parent = matrix(bone['parent']) if bone.get('parent') in bones else np.eye(4)
        local = np.eye(4)
        if bone.get('rotation'):
            pivot = np.array(bone['pivot'], float)
            turn = flip @ rotation([math.radians(value) for value in bone['rotation']]) @ flip
            local[:3, :3] = turn
            local[:3, 3] = pivot - turn @ pivot
        cache[name] = parent @ local
        return cache[name]

    corners = {}
    for name, bone in bones.items():
        for cube in bone.get('cubes', []):
            origin, size = np.array(cube['origin'], float), np.array(cube['size'], float)
            points = [origin + size * np.array([i >> 2 & 1, i >> 1 & 1, i & 1]) for i in range(8)]
            world = [(matrix(name) @ np.append(point, 1))[:3] for point in points]
            corners.setdefault(name, []).append(np.round(sorted(map(tuple, world)), 4))
    return corners


class GeometryTests(unittest.TestCase):
    def test_part_replaces_its_vanilla_bone_and_keeps_the_bone_for_animation(self):
        bones = [{'name': 'body', 'pivot': [0, 24, 0], 'cubes': [{'origin': [-4, 12, -2], 'size': [8, 12, 4], 'uv': [16, 16]}]},
                 {'name': 'head', 'parent': 'body', 'pivot': [0, 24, 0], 'cubes': [{'origin': [-4, 24, -4], 'size': [8, 8, 8], 'uv': [0, 0]}]}]
        model = load_model({'textureSize': [64, 32], 'models': [
            {'part': 'head', 'id': 'head', 'invertAxis': 'xy', 'translate': [0, -24, 0],
             'submodels': [{'id': 'snout', 'invertAxis': 'xy', 'translate': [0, 26, -4],
                            'boxes': [{'coordinates': [-1, -1, -2, 2, 2, 2], 'textureOffset': [0, 0]}]}]}]})
        result = convert(model, Target('geometry.test', vanilla(bones), {'head': 'head', 'body': 'body'}))
        out = geometry_bones(result)
        self.assertEqual(out['head']['cubes'], [])
        self.assertEqual(len(out['body']['cubes']), 1)
        self.assertEqual(out['cem_head']['parent'], 'head')
        self.assertEqual(out['cem_snout']['parent'], 'cem_head')
        # Top-level boxes and pivots are absolute in the author's editor space; the snout pivot sits at y 26.
        self.assertEqual(out['cem_snout']['pivot'], [0.0, 26.0, -4.0])
        self.assertEqual(out['cem_snout']['cubes'][0]['origin'], [-1.0, 25.0, -6.0])

    def test_rotated_cube_lands_where_java_draws_it(self):
        data = {'textureSize': [64, 64], 'models': [
            {'part': 'body', 'id': 'body', 'invertAxis': 'xy', 'translate': [0, -12, 0],
             'submodels': [{'id': 'fin', 'invertAxis': 'xy', 'translate': [3, 14, 2], 'rotate': [30, -20, 15],
                            'boxes': [{'coordinates': [-1, -2, -3, 2, 4, 6], 'uvNorth': [0, 0, 2, 4]}],
                            'submodels': [{'id': 'tip', 'invertAxis': 'xy', 'translate': [0, 4, 0], 'rotate': [0, 0, 45],
                                           'boxes': [{'coordinates': [0, 0, 0, 1, 1, 1], 'uvUp': [4, 4, 5, 5]}]}]}]}]}
        model = load_model(data)
        result = convert(model, Target('geometry.test', vanilla([{'name': 'body', 'pivot': [0, 12, 0]}]), {'body': 'body'}))
        world = bedrock_world(geometry_bones(result))
        # Independent OptiFine runtime: pivots and boxes negated on the inverted axes, parts nested.
        body_pivot = np.array([0.0, 12.0, 0.0])           # vanilla body pivot in Java space (y = 24 - 12)
        top_local = np.array([-0.0, 12.0, 0.0])           # translate [0, -12, 0] with x and y negated
        fin_local = np.array([-3.0, -14.0, 2.0])
        fin_turn = rotation([math.radians(-30), math.radians(20), math.radians(15)])
        tip_local = np.array([0.0, -4.0, 0.0])
        tip_turn = rotation([0, 0, math.radians(45)])

        def corners(origin, size, chain):
            result = []
            for i in range(8):
                point = np.array(origin) + np.array(size) * np.array([i >> 2 & 1, i >> 1 & 1, i & 1])
                for pivot, turn in reversed(chain):
                    point = pivot + turn @ point
                result.append(tuple(to_bedrock(point)))
            return np.round(sorted(result), 4)

        identity = np.eye(3)
        base = [(body_pivot, identity), (top_local, identity), (fin_local, fin_turn)]
        expected_fin = corners([-(-1) - 2, -(-2) - 4, -3], [2, 4, 6], base)
        expected_tip = corners([-0 - 1, -0 - 1, 0], [1, 1, 1], base + [(tip_local, tip_turn)])
        np.testing.assert_allclose(world['cem_fin'][0], expected_fin, atol=1e-3)
        np.testing.assert_allclose(world['cem_tip'][0], expected_tip, atol=1e-3)
        fin = geometry_bones(result)['cem_fin']
        self.assertEqual(fin['rotation'], [-30.0, 20.0, 15.0])

    def test_face_uvs_keep_face_names_and_up_down_start_at_their_far_corner(self):
        model = load_model({'textureSize': [32, 32], 'models': [
            {'part': 'body', 'id': 'body', 'invertAxis': 'xy', 'translate': [0, 0, 0],
             'boxes': [{'coordinates': [0, 0, 0, 2, 3, 4], 'uvNorth': [1, 2, 3, 5], 'uvUp': [6, 7, 8, 11]}]}]})
        result = convert(model, Target('geometry.test', vanilla([{'name': 'body', 'pivot': [0, 24, 0]}]), {'body': 'body'}))
        cube = geometry_bones(result)['cem_body']['cubes'][0]
        self.assertEqual(cube['uv']['north'], {'uv': [1.0, 2.0], 'uv_size': [2.0, 3.0]})
        self.assertEqual(cube['uv']['up'], {'uv': [8.0, 11.0], 'uv_size': [-2.0, -4.0]})
        self.assertEqual(set(cube['uv']), {'north', 'up'})

    def test_box_uv_layout_and_mirror(self):
        faces = box_uv_faces((0, 0), (8, 8, 8))
        self.assertEqual(faces['north'], [8, 8, 16, 16])
        self.assertEqual(faces['east'], [0, 8, 8, 16])
        self.assertEqual(faces['up'], [16, 8, 8, 0])
        mirrored = box_uv_faces((0, 0), (8, 8, 8), True)
        self.assertEqual(mirrored['north'], [16, 8, 8, 16])
        self.assertEqual(mirrored['east'][0:3:2], [24, 16])
        self.assertEqual(bedrock_face_uv('down', [24, 0, 16, 8]), {'uv': [16.0, 8.0], 'uv_size': [8.0, -8.0]})

    def test_vanilla_rest_rotation_wraps_the_parts_and_constant_rotations_fold(self):
        model = load_model({'textureSize': [64, 64], 'models': [
            {'part': 'body', 'id': 'body', 'invertAxis': 'xy', 'translate': [0, -19, -2], 'rotate': [-90, 0, 0],
             'boxes': [{'coordinates': [-6, 11, -5, 12, 18, 10], 'textureOffset': [18, 4]}],
             'animations': [{'this.rx': 0}]}]})
        target = Target('geometry.test', vanilla([{'name': 'body', 'pivot': [0, 19, 2]}], 64, 64), {'body': 'body'},
                        vanilla_parts={'body': {'pivot': [0, 5, 2], 'rotation': [90, 0, 0]}})
        result = convert(model, target)
        out = geometry_bones(result)
        self.assertEqual(out['cem_body_vanilla']['rotation'], [90.0, 0.0, 0.0])
        self.assertNotIn('rotation', out['cem_body'])
        self.assertEqual(result['pre_animation'], [])
        self.assertEqual(result['report']['folded_constants'], 1)
        # Upright box, turned a quarter about the vanilla pivot: 18 long along z, 10 tall.
        corners = bedrock_world(out)['cem_body'][0]
        self.assertAlmostEqual(float(corners[:, 2].max() - corners[:, 2].min()), 18.0, places=3)
        self.assertAlmostEqual(float(corners[:, 1].max() - corners[:, 1].min()), 10.0, places=3)


    def test_renderer_frame_scales_and_moves_the_model(self):
        model = load_model({'textureSize': [64, 32], 'models': [
            {'part': 'body', 'id': 'body', 'invertAxis': 'xy', 'translate': [0, -20, 0],
             'boxes': [{'coordinates': [-8, 12, -8, 16, 16, 16], 'textureOffset': [0, 0]}],
             'animations': [{'this.ty': '22 + 0 * limb_speed'}]}]})
        target = Target('geometry.test', vanilla([{'name': 'body', 'pivot': [0, 20, 0]}]), {'body': 'body'},
                        frame_scale=4.5, frame_offset=(0, -58, 0))
        result = convert(model, target)
        cube = geometry_bones(result)['cem_body']['cubes'][0]
        self.assertEqual(cube['size'], [72.0, 72.0, 72.0])
        self.assertEqual(cube['origin'], [-36.0, -4.0, -36.0])
        # ty is the Java (y down) pivot: 22 against a rest of 20 is 2 down, 9 Bedrock units at 4.5 times the size.
        env = {'query.modified_move_speed': 1}
        for line in result['pre_animation']:
            molang_evaluate(line, env)
        self.assertAlmostEqual(molang_evaluate(result['animation_bones']['cem_body']['position'][1], env), -9.0)


class AnimationTests(unittest.TestCase):
    def test_expressions_become_variables_and_bone_channels(self):
        model = load_model({'textureSize': [64, 32], 'models': [
            {'part': 'head', 'id': 'head', 'invertAxis': 'xy', 'translate': [0, -24, 0],
             'submodels': [{'id': 'ear', 'invertAxis': 'xy', 'translate': [0, 30, 0], 'rotate': [10, 0, 0],
                            'boxes': [{'coordinates': [0, 0, 0, 1, 2, 1], 'textureOffset': [0, 0]}]}],
             'animations': [{'var.swing': 'sin(limb_swing) * limb_speed',
                             'ear.rx': 'var.swing + leg1.rx', 'ear.ty': '-30 + is_child'}]}]})
        target = Target('geometry.test', vanilla([{'name': 'head', 'pivot': [0, 24, 0]}]), {'head': 'head'},
                        formulas={'leg1.rx': 'cos(limb_swing*0.6662)*1.4*limb_speed'})
        result = convert(model, target)
        self.assertEqual(result['report']['unresolved_identifiers'], [])
        channel = result['animation_bones']['cem_ear']
        env = {'query.modified_distance_moved': 2.0, 'query.modified_move_speed': 0.5, 'query.is_baby': 1.0}
        for line in result['pre_animation']:
            molang_evaluate(line, env)
        expected = math.sin(2.0) * 0.5 + math.cos(2.0 * 0.6662) * 1.4 * 0.5
        self.assertAlmostEqual(env['variable.cem_ear_rx'], expected, places=6)
        # The channel adds to the rest rotation (-10 degrees from rotate [10, 0, 0]).
        degrees = molang_evaluate(channel['rotation'][0], dict(env))
        self.assertAlmostEqual(degrees, math.degrees(expected) + 10.0, places=4)
        # ty -29 against a rest of -30: one pixel down in Java is one pixel down in Bedrock.
        offset = molang_evaluate(channel['position'][1], dict(env))
        self.assertAlmostEqual(offset, -1.0, places=6)

    def test_root_part_gets_its_own_bone_above_the_model(self):
        model = load_model({'textureSize': [64, 32], 'models': [
            {'part': 'root', 'id': 'root', 'invertAxis': 'xy', 'translate': [0, 0, 0],
             'animations': [{'root.sy': '1 + sin(age) * 0.1'}]}]})
        target = Target('geometry.test', vanilla([{'name': 'body', 'pivot': [0, 12, 0]}]), {})
        result = convert(model, target)
        out = geometry_bones(result)
        self.assertEqual(out[ROOT_BONE]['pivot'], [0, 24, 0])
        self.assertEqual(out['body']['parent'], ROOT_BONE)
        self.assertIn(ROOT_BONE, result['animation_bones'])
        self.assertIn('scale', result['animation_bones'][ROOT_BONE])


@unittest.skipUnless((samples_path() / 'resource_pack/models/entity/creeper.geo.json').exists(), 'Bedrock samples missing')
class VanillaGeometryTests(unittest.TestCase):
    def test_a_vanilla_layout_model_lands_on_the_vanilla_bedrock_geometry(self):
        geometries = resolve_inheritance(normalize_geometries(read_json(samples_path() / 'resource_pack/models/entity/creeper.geo.json')))
        reference = geometries['geometry.creeper.v1.8']
        legs = []
        for index, (x, z) in enumerate(((-2, 4), (2, 4), (-2, -4), (2, -4)), start=1):
            legs.append({'part': 'leg%d' % index, 'id': 'leg%d' % index, 'invertAxis': 'xy', 'translate': [x, -6, -z],
                         'boxes': [{'coordinates': [-x - 2, 0, z - 2, 4, 6, 4], 'textureOffset': [0, 16]}]})
        model = load_model({'textureSize': [64, 32], 'models': [
            {'part': 'head', 'id': 'head', 'invertAxis': 'xy', 'translate': [0, -18, 0],
             'boxes': [{'coordinates': [-4, 18, -4, 8, 8, 8], 'textureOffset': [0, 0]}]},
            {'part': 'body', 'id': 'body', 'invertAxis': 'xy', 'translate': [0, -18, 0],
             'boxes': [{'coordinates': [-4, 6, -2, 8, 12, 4], 'textureOffset': [16, 16]}]}] + legs})
        bone_map = {'head': 'head', 'body': 'body', 'leg1': 'leg0', 'leg2': 'leg1', 'leg3': 'leg2', 'leg4': 'leg3'}
        result = convert(model, Target('geometry.creeper.v1.8', reference, bone_map))
        converted = bedrock_world(geometry_bones(result))
        original = bedrock_world({bone['name']: bone for bone in reference['bones']})
        for part, bone in bone_map.items():
            np.testing.assert_allclose(converted['cem_' + part][0], original[bone][0], atol=1e-3)


if __name__ == '__main__':
    unittest.main()
