from pathlib import Path
import math
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from java_entity_molang import (ExpressionError, evaluate, molang_evaluate, molang_valid, parse, translate)

QUERIES = {
    'limb_swing': 'query.modified_distance_moved',
    'limb_speed': 'query.modified_move_speed',
    'head_yaw': 'query.target_y_rotation',
    'head_pitch': 'query.target_x_rotation',
    'is_child': 'query.is_baby',
    'pos_y': 'query.position(1)',
    'age': '(query.life_time * 20)',
    'pi': 'math.pi',
}


def molang_env(values):
    return {'query.modified_distance_moved': values['limb_swing'], 'query.modified_move_speed': values['limb_speed'],
            'query.target_y_rotation': values['head_yaw'], 'query.target_x_rotation': values['head_pitch'],
            'query.is_baby': values['is_child'], 'query.position(1)': values['pos_y'],
            'query.life_time': values['age'] / 20}


class TranslationTests(unittest.TestCase):
    EXPRESSIONS = (
        'sin(limb_swing * 0.6662) * 1.4 * limb_speed',
        'torad(-67.5) + head_pitch / 90',
        'if(limb_speed > 1, sin(limb_swing / 2.5) * limb_speed, is_child, 0.5, -cos(limb_swing))',
        'clamp(90 * sin(torad(head_yaw)), -90, 90) + min(1, 2, limb_speed) - max(pos_y, 3)',
        '!is_child && limb_speed >= 0.5 || pos_y < 2',
        'between(age % 40, 10, 20) + equals(head_yaw, 30, 5) + in(is_child, 0, 2)',
        'pow(limb_speed, 2) + abs(-pos_y) + sqrt(age) + asin(0.25) + acos(0.5) + atan2(1, 2) + todeg(1)',
        'frac(age / 7) + signum(head_yaw) + fmod(-7, 3) + round(2.5) + floor(-1.5) + ceil(1.2) + lerp(0.25, 2, 6)',
        'tan(0.3) + exp(0.5) + log(2) - -limb_swing',
    )

    def test_translated_expressions_evaluate_like_optifine(self):
        generator = random.Random(7)
        for text in self.EXPRESSIONS:
            molang, unresolved, _ = translate(text, QUERIES.get)
            self.assertFalse(unresolved, text)
            self.assertTrue(molang_valid(molang), molang)
            for _ in range(40):
                values = {'limb_swing': generator.uniform(-30, 30), 'limb_speed': generator.uniform(0, 1.5),
                          'head_yaw': generator.uniform(-90, 90), 'head_pitch': generator.uniform(-90, 90),
                          'is_child': generator.choice([0, 1]), 'pos_y': generator.uniform(-5, 70),
                          'age': generator.uniform(1, 5000)}
                expected = evaluate(parse(text), values)
                actual = molang_evaluate(molang, molang_env(values))
                self.assertTrue(math.isclose(expected, actual, rel_tol=1e-6, abs_tol=1e-6), (text, values, expected, actual))

    def test_trigonometry_moves_from_radians_to_degrees(self):
        molang, _, _ = translate('sin(limb_swing)', QUERIES.get)
        self.assertIn('math.sin(', molang)
        self.assertIn('57.29577', molang)
        direct, _, _ = translate('cos(torad(head_yaw))', QUERIES.get)
        self.assertEqual(direct, 'math.cos(query.target_y_rotation)')

    def test_unknown_names_are_reported(self):
        _, unresolved, _ = translate('mystery + limb_swing', QUERIES.get)
        self.assertEqual(unresolved, {'mystery'})

    def test_parse_errors_are_explicit(self):
        with self.assertRaises(ExpressionError):
            parse('sin(limb_swing')
        with self.assertRaises(ExpressionError):
            parse('1 +* 2')


class MolangCheckTests(unittest.TestCase):
    def test_brace_scope_runs_only_when_the_condition_holds(self):
        script = '(!query.is_baby && v.index == 1) ? { v.cem_a = 2; v.cem_b = v.cem_a + 1; };'
        self.assertTrue(molang_valid(script))
        adult = {'query.is_baby': 0, 'variable.index': 1}
        molang_evaluate(script, adult)
        self.assertEqual((adult['variable.cem_a'], adult['variable.cem_b']), (2.0, 3.0))
        baby = {'query.is_baby': 1, 'variable.index': 1}
        molang_evaluate(script, baby)
        self.assertNotIn('variable.cem_a', baby)

    def test_variables_persist_between_frames(self):
        env = {}
        for _ in range(3):
            molang_evaluate('v.cem_frame_counter = v.cem_frame_counter + 1;', env)
        self.assertEqual(env['variable.cem_frame_counter'], 3.0)

    def test_invalid_molang_is_rejected(self):
        self.assertFalse(molang_valid('v.x = (1 + ;'))
        self.assertFalse(molang_valid('v.x = { v.y = 1;'))


if __name__ == '__main__':
    unittest.main()
