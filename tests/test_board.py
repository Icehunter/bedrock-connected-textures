from pathlib import Path
import json
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
import ctm_board as board_module

FIXTURES = Path(__file__).resolve().parent / 'fixtures/board'
FIXTURE_FILES = ('layout.json', 'rules.json', 'expected.json', 'station.mcfunction')
REGENERATE = 'run python converter/ctm_board.py --output tests/fixtures/board'


class TestBoardTests(unittest.TestCase):
    def test_fixtures_match_the_generator(self):
        with tempfile.TemporaryDirectory() as folder:
            built = board_module.build(Path(folder))
            for name in FIXTURE_FILES:
                self.assertEqual((built / name).read_text(encoding='utf-8'),
                                 (FIXTURES / name).read_text(encoding='utf-8'),
                                 name + ' is out of date: ' + REGENERATE)

    def test_expected_tiles_follow_the_optifine_templates(self):
        tiles = json.loads((FIXTURES / 'expected.json').read_text())['tiles']

        def wall(x, y):
            return tiles[f'{x},{y},-6,south']

        self.assertEqual([wall(0, 1), wall(2, 1), wall(3, 1), wall(4, 1)], [0, 1, 2, 3],
                         'ctm: alone, then a row seen from the south')
        self.assertEqual([wall(6, 3), wall(6, 2), wall(6, 1)], [12, 24, 36], 'ctm column from the top')
        self.assertEqual([[wall(x, y) for x in (8, 9, 10)] for y in (3, 2, 1)],
                         [[13, 14, 15], [25, 26, 27], [37, 38, 39]])
        self.assertEqual(wall(13, 2), 46, 'a cross without corners')
        self.assertEqual([wall(17, 1), wall(18, 1), wall(19, 1), wall(21, 1)], [0, 1, 2, 3],
                         'horizontal: left end, middle, right end, alone')
        self.assertEqual([wall(23, 1), wall(23, 2), wall(23, 3), wall(25, 1)], [0, 1, 2, 3],
                         'vertical: bottom, middle, top, alone')
        self.assertEqual([wall(31, 1), wall(31, 2), wall(31, 3)], [4, 5, 6], 'horizontal+vertical lone column')
        self.assertEqual([wall(35, 1), wall(36, 1), wall(37, 1)], [4, 5, 6], 'vertical+horizontal lone row')
        self.assertEqual([wall(39, 1), wall(39, 2)], [0, None], 'top: only under the same block')
        self.assertEqual([wall(x, 4) for x in range(43, 49)], [1, 2, 0, 1, 2, 0], 'repeat 3x2 column steps east')
        self.assertEqual([wall(43, y) for y in (1, 2, 3, 4)], [4, 1, 4, 1], 'and its row steps down')
        # The overlay lies on the side of the gravel: east is the texture's right (tile 7), north its top (15).
        # A corner piece needs a block with the same rule beside it, so gravel only to the north-east draws nothing.
        self.assertEqual([tiles[f'{x},0,-2,up'] for x in (7, 11, 15, 19, 23)], [[7], [15], [9], [1], []],
                         'overlay: gravel east, north, west, south and only north-east')
        random = [tiles[f'{2 + x},0,{-4 + z},up'] for x in range(4) for z in range(4)]
        self.assertTrue(set(random) <= {0, 1, 2, 3} and len(set(random)) > 1)

    def test_block_seed_is_minecrafts_position_seed(self):
        self.assertEqual(board_module.block_seed(0, 0, 0), 0, 'Mth.getSeed of the origin')
        self.assertEqual(board_module.imul(3129871, 1000), -1165096296, 'Java int multiplication wraps to 32 bits')

    def test_station_only_places_into_air(self):
        lines = (FIXTURES / 'station.mcfunction').read_text().splitlines()
        commands = [line for line in lines if line and not line.startswith('#')]
        self.assertTrue(commands)
        self.assertTrue(all(line.startswith('setblock ~') and line.endswith(' keep') for line in commands))
        layout = json.loads((FIXTURES / 'layout.json').read_text())
        self.assertEqual(len(commands), len(layout))
        cells = [{key: cell[key] for key in 'xyz'} for cell in layout]
        self.assertNotIn({'x': 0, 'y': 0, 'z': 0}, cells, 'the player cell stays free')

    def test_converter_imports_the_board_pack(self):
        rules = json.loads((FIXTURES / 'rules.json').read_text())['rules']
        methods = {rule['method']: rule for rule in rules}
        self.assertEqual(sorted(methods), ['ctm', 'fixed', 'horizontal', 'horizontal+vertical', 'overlay', 'random',
                                           'repeat', 'top', 'vertical', 'vertical+horizontal'])
        self.assertEqual(methods['repeat']['blocks'], ['minecraft:brick_block'], 'the Java id bricks maps to Bedrock')
        self.assertEqual(methods['overlay']['connectBlocks'], ['minecraft:gravel'])
        with tempfile.TemporaryDirectory() as folder:
            archive = board_module.write_pack_zip(Path(folder) / 'board.zip')
            with zipfile.ZipFile(archive) as pack:
                names = set(pack.namelist())
        self.assertIn('pack.mcmeta', names)
        self.assertIn('assets/minecraft/optifine/ctm/board/ctm/46.png', names)


if __name__ == '__main__':
    unittest.main()
