"""Neighbour cases of ctm replacement blocks: what each case shows, and the same keys in the engine."""
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'converter'))
from connected_cases import DIAGONALS, SIDE_NAMES, SIDES, cases, digit_states, digits, face_tiles, key, table  # noqa: E402

SIDE = {name: index for index, name in enumerate(SIDE_NAMES)}


def diagonal(first, second):
    return DIAGONALS.index(tuple(sorted((SIDE[first], SIDE[second]))))


class ConnectedCasesTest(unittest.TestCase):
    def test_a_block_has_483_cases_and_each_looks_different(self):
        self.assertEqual(len(cases()), 483)
        looks, by_case = table()
        self.assertEqual(len(looks), 483)
        self.assertEqual(sorted(set(by_case.values())), list(range(483)))

    def test_faces_show_the_java_tiles(self):
        self.assertEqual(set(face_tiles(0).values()), {0})
        # A block in a row running west to east: the north face joins on its left and right.
        row = face_tiles(key({SIDE['west'], SIDE['east']}, set()))
        self.assertEqual((row['north'], row['south'], row['up']), (2, 2, 2))
        self.assertEqual((row['west'], row['east']), (None, None))
        # In a wall, an inner corner shows only while the diagonal block is missing.
        joined = {SIDE['up'], SIDE['west']}
        without = face_tiles(key(joined, set()))
        with_corner = face_tiles(key(joined, {diagonal('up', 'west')}))
        self.assertNotEqual(without['north'], with_corner['north'])
        self.assertEqual(with_corner['north'], 37)

    def test_a_diagonal_only_counts_while_a_face_across_shows(self):
        # Up and west join with north and south both covered: no face shows the up-west corner.
        joined = {SIDE['up'], SIDE['west'], SIDE['north'], SIDE['south']}
        self.assertEqual(key(joined, {diagonal('up', 'west')}), key(joined, set()))
        self.assertEqual(max(len([index for index in range(12) if key(set(range(6)) - {side}, {index}) !=
                                  key(set(range(6)) - {side}, set())]) for side in range(6)), 4)

    def test_digits_hold_every_look(self):
        states = digit_states(483)
        self.assertEqual(states, [('bct:c0', 16), ('bct:c1', 16), ('bct:c2', 2)])
        seen = {tuple(digits(index, states).values()) for index in range(483)}
        self.assertEqual(len(seen), 483)
        for index in range(483):
            values = digits(index, states)
            self.assertTrue(all(0 <= values[name] < size for name, size in states))
        self.assertEqual(digit_states(5), [('bct:c0', 5)])

    @unittest.skipUnless(shutil.which('node'), 'needs node')
    def test_the_engine_finds_the_same_case_and_states(self):
        looks, by_case = table()
        ctm = {'with': ['minecraft:glass'], 'states': [name for name, _ in digit_states(len(looks))],
               'sides': [list(offset) for offset in SIDES.values()], 'diagonals': [list(pair) for pair in DIAGONALS],
               'cases': {str(case): index for case, index in by_case.items()}}
        picker = random.Random(7)
        samples = []
        for _ in range(300):
            joined = {side for side in range(6) if picker.random() < .6}
            filled = {tuple(SIDES[SIDE_NAMES[side]]) for side in joined}
            filled |= {tuple(a + b for a, b in zip(SIDES[SIDE_NAMES[first]], SIDES[SIDE_NAMES[second]]))
                       for first, second in DIAGONALS if picker.random() < .5}
            diagonals = {index for index, (first, second) in enumerate(DIAGONALS)
                         if tuple(a + b for a, b in zip(SIDES[SIDE_NAMES[first]], SIDES[SIDE_NAMES[second]])) in filled}
            expected = key(joined, diagonals)
            samples.append({'filled': sorted(filled), 'key': expected,
                            'states': digits(by_case[expected], digit_states(len(looks)))})
        script = ("import { ctmCase, ctmStates } from './engine/replacement.mjs';"
                  "import { readFileSync } from 'node:fs';"
                  "const [ctm, samples] = JSON.parse(readFileSync(0, 'utf8'));"
                  "process.stdout.write(JSON.stringify(samples.map(sample => {"
                  "  const filled = new Set(sample.filled.map(String));"
                  "  const found = ctmCase(ctm, delta => filled.has(String(delta)));"
                  "  return [found, ctmStates(ctm, found)];"
                  "})));")
        result = subprocess.run(['node', '--input-type=module', '-e', script], input=json.dumps([ctm, samples]),
                                cwd=ROOT, capture_output=True, text=True, check=True)
        for sample, (found, states) in zip(samples, json.loads(result.stdout)):
            self.assertEqual(found, sample['key'])
            self.assertEqual(states, sample['states'])


if __name__ == '__main__':
    unittest.main()
