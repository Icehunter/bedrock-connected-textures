import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bedrock_schema import check_tree, geometry_errors
from block_ids import java_id, known_blocks
from common import samples_path
from java_block_bindings import _Models
from java_pack_api import PackStack
from model_blocks import bedrock_loot, plan_all
from native_replacement import build_replacements, load_policy
from test_native_replacement import TEXTURES, material

FACES = ['north', 'east', 'south', 'west', 'up', 'down']
NOT_SHEARS = {'condition': 'minecraft:inverted', 'term': {'condition': 'minecraft:any_of', 'terms': [
    {'condition': 'minecraft:match_tool', 'predicate': {'items': 'minecraft:shears'}}]}}
# Java's table_bonus chances by Fortune level for the drops of oak leaves.
SAPLING_CHANCES = [0.05, 0.0625, 0.083333336, 0.1]
STICK_CHANCES = [0.02, 0.022222223, 0.025, 0.033333335, 0.1]
APPLE_CHANCES = [0.005, 0.0055555557, 0.00625, 0.008333334, 0.025]


def fortune_bonus(chances):
    return {'condition': 'minecraft:table_bonus', 'enchantment': 'minecraft:fortune', 'chances': chances}


def java_leaf_loot(leaves, sapling, apple=False):
    """Java 26.2's leaves loot table shape.

    The block for shears or Silk Touch, else sapling, sticks (and apples) by Fortune.
    """
    silk_touch = {'condition': 'minecraft:match_tool', 'predicate': {'predicates': {'minecraft:enchantments': [
        {'enchantments': 'minecraft:silk_touch', 'levels': {'min': 1}}]}}}
    shears_or_silk_touch = {'condition': 'minecraft:any_of', 'terms': [
        {'condition': 'minecraft:match_tool', 'predicate': {'items': 'minecraft:shears'}}, silk_touch]}
    sticks = {'function': 'minecraft:set_count', 'count': {'type': 'minecraft:uniform', 'min': 1.0, 'max': 2.0},
              'add': False}
    pools = [
        {'rolls': 1.0, 'bonus_rolls': 0.0, 'entries': [{'type': 'minecraft:alternatives', 'children': [
            {'type': 'minecraft:item', 'name': leaves, 'conditions': [shears_or_silk_touch]},
            {'type': 'minecraft:item', 'name': sapling, 'conditions': [
                {'condition': 'minecraft:survives_explosion'}, fortune_bonus(SAPLING_CHANCES)]}]}]},
        {'rolls': 1.0, 'bonus_rolls': 0.0, 'conditions': [NOT_SHEARS], 'entries': [
            {'type': 'minecraft:item', 'name': 'minecraft:stick', 'conditions': [fortune_bonus(STICK_CHANCES)],
             'functions': [sticks, {'function': 'minecraft:explosion_decay'}]}]}]
    if apple:
        pools.append({'rolls': 1.0, 'bonus_rolls': 0.0, 'conditions': [NOT_SHEARS], 'entries': [
            {'type': 'minecraft:item', 'name': 'minecraft:apple', 'conditions': [
                {'condition': 'minecraft:survives_explosion'}, fortune_bonus(APPLE_CHANCES)]}]})
    return {'type': 'minecraft:block', 'pools': pools}


def java_26_leaf_loot(leaves, sapling):
    """Java 26.3's leaves loot table shape: one `condition` named by `type`, named tool predicates, `modifier`."""
    shears_or_silk_touch = {'type': 'minecraft:any_of', 'terms': ['minecraft:tool/can_shear', 'minecraft:tool/can_silk_touch']}
    not_shears = {'type': 'minecraft:inverted', 'term': shears_or_silk_touch}

    def bonus(chances):
        return {'type': 'minecraft:table_bonus', 'chances': chances, 'enchantment': 'minecraft:fortune'}
    return {'type': 'minecraft:block', 'pools': [
        {'rolls': 1, 'entries': [{'type': 'minecraft:alternatives', 'children': [
            {'type': 'minecraft:item', 'name': leaves, 'condition': shears_or_silk_touch},
            {'type': 'minecraft:item', 'name': sapling, 'condition': {'type': 'minecraft:all_of', 'terms': [
                {'type': 'minecraft:survives_explosion'}, bonus(SAPLING_CHANCES)]}}]}]},
        {'rolls': 1, 'condition': not_shears, 'entries': [
            {'type': 'minecraft:item', 'name': 'minecraft:stick', 'condition': bonus(STICK_CHANCES),
             'modifier': [{'type': 'minecraft:set_count', 'count': {'type': 'minecraft:uniform', 'max': 2, 'min': 1}},
                          {'type': 'minecraft:explosion_decay'}]}]},
        {'rolls': 1, 'condition': not_shears, 'entries': [
            {'type': 'minecraft:item', 'name': 'minecraft:apple', 'condition': {'type': 'minecraft:all_of', 'terms': [
                {'type': 'minecraft:survives_explosion'}, bonus(APPLE_CHANCES)]}}]}]}


def cube_faces(texture, tint=0):
    return {face: {'uv': [0, 0, 16, 16], 'texture': texture, 'tintindex': tint, 'cullface': face} for face in FACES}


def write_fixture(root):
    """A Java reference jar with vanilla oak and birch leaves, and an author pack that draws oak with an inner
    model (persistent or within 4 of a log) and an outer model at 5 to 7, each in two weighted turns."""
    jar = root / 'client.jar'
    with zipfile.ZipFile(jar, 'w') as archive:
        archive.writestr('assets/minecraft/models/block/leaves.json', json.dumps({
            'textures': {'particle': '#all'},
            'elements': [{'from': [0, 0, 0], 'to': [16, 16, 16], 'faces': cube_faces('#all')}]}))
        for wood in ('oak', 'birch', 'jungle'):
            archive.writestr(f'assets/minecraft/blockstates/{wood}_leaves.json',
                             json.dumps({'variants': {'': {'model': f'minecraft:block/{wood}_leaves'}}}))
            archive.writestr(f'assets/minecraft/models/block/{wood}_leaves.json', json.dumps({
                'parent': 'minecraft:block/leaves', 'textures': {'all': f'minecraft:block/{wood}_leaves'}}))
            archive.writestr(f'data/minecraft/loot_table/blocks/{wood}_leaves.json', json.dumps(
                java_leaf_loot(f'minecraft:{wood}_leaves', f'minecraft:{wood}_sapling', apple=wood == 'oak')))
    pack = root / 'author.zip'
    leaf = {'from': [-5, 0, 8], 'to': [21, 16, 8], 'shade': False,
            'faces': {'north': {'uv': [0, 0, 16, 16], 'texture': '#leaves', 'tintindex': 0},
                      'south': {'uv': [0, 0, 16, 16], 'texture': '#leaves', 'tintindex': 0}}}
    wide = {**leaf, 'from': [-10, -2, 8], 'to': [26, 18, 8]}
    with zipfile.ZipFile(pack, 'w') as archive:
        archive.writestr('pack.mcmeta', json.dumps({'pack': {'pack_format': 64, 'description': 'test'}}))
        archive.writestr('assets/minecraft/blockstates/oak_leaves.json', json.dumps({'multipart': [
            {'when': {'OR': [{'persistent': 'true'}, {'distance': '1|2|3|4'}]},
             'apply': [{'model': 'minecraft:block/oak_inner'},
                       {'model': 'minecraft:block/oak_inner', 'y': 90, 'weight': 3}]},
            {'when': {'persistent': 'false', 'distance': '5|6|7'},
             'apply': [{'model': 'minecraft:block/oak_outer'},
                       {'model': 'minecraft:block/oak_outer', 'y': 90, 'weight': 3}]}]}))
        archive.writestr('assets/minecraft/models/block/oak_inner.json', json.dumps({
            'textures': {'leaves': 'minecraft:block/oak_leaves', 'particle': '#leaves'},
            'elements': [{'from': [0, 0, 0], 'to': [16, 16, 16], 'faces': cube_faces('#leaves')}, leaf]}))
        archive.writestr('assets/minecraft/models/block/oak_outer.json', json.dumps({
            'textures': {'leaves': 'minecraft:block/oak_leaves', 'particle': '#leaves'}, 'elements': [wide]}))
        # Jungle weighs the same two turns differently by log distance (1:1 near a log, 1:3 farther out).
        archive.writestr('assets/minecraft/blockstates/jungle_leaves.json', json.dumps({'multipart': [
            {'when': {'OR': [{'persistent': 'true'}, {'distance': '1|2|3|4'}]},
             'apply': [{'model': 'minecraft:block/jungle_leafy'}, {'model': 'minecraft:block/jungle_leafy', 'y': 90}]},
            {'when': {'persistent': 'false', 'distance': '5|6|7'},
             'apply': [{'model': 'minecraft:block/jungle_leafy'},
                       {'model': 'minecraft:block/jungle_leafy', 'y': 90, 'weight': 3}]}]}))
        archive.writestr('assets/minecraft/models/block/jungle_leafy.json', json.dumps({
            'textures': {'leaves': 'minecraft:block/jungle_leaves', 'particle': '#leaves'}, 'elements': [leaf]}))
    compiled = root / 'compiled'
    for wood in ('oak', 'birch', 'jungle'):
        material(compiled, TEXTURES + f'{wood}_leaves.png', (60, 140, 40, 200))
    return jar, pack, compiled


class ModelBlockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        root = Path(cls.folder.name)
        jar, pack, compiled = write_fixture(root)
        policy = load_policy()
        samples = samples_path()
        with PackStack([pack]) as stack, zipfile.ZipFile(jar) as vanilla:
            cls.plans, cls.skipped = plan_all(_Models(vanilla, stack), policy, known_blocks(samples), java_id, vanilla)
        document = {'format_version': 1, 'rules': [], 'modelTintTypes': {'minecraft:oak_leaves': 'foliage'}}
        cls.out = root / 'replacement'
        cls.data, cls.report = build_replacements(document, {}, policy, source=compiled, samples=samples,
                                                  key='pack-test', output=cls.out, model_blocks=cls.plans)
        cls.bp, cls.rp = cls.out / 'Replace_BP', cls.out / 'Replace_RP'

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def oak_leaves(self):
        return json.loads((self.bp / 'blocks/m_oak_leaves.json').read_text())['minecraft:block']

    def test_only_leaves_with_their_own_models_become_model_blocks(self):
        self.assertEqual(sorted(plan['bedrock'] for plan in self.plans), ['minecraft:jungle_leaves', 'minecraft:oak_leaves'])
        reasons = {item['block']: item['reason'] for item in self.skipped}
        self.assertEqual(reasons['minecraft:birch_leaves'], 'the blockstate shows full cubes; the base pack draws them')
        self.assertIn('minecraft:spruce_leaves', reasons, 'a leaf the reference jar does not have is reported')
        self.assertEqual(self.report['model_blocks_not_built'], [])

    def test_states_pick_the_inner_or_outer_model_and_a_weighted_turn(self):
        entry = next(entry for entry in self.data['leaves']['blocks'] if entry['vanilla'] == 'minecraft:oak_leaves')
        self.assertEqual(entry['mirror'], {'persistent_bit': 'bct:persistent_bit', 'update_bit': 'bct:update_bit'})
        self.assertEqual(entry['look'], {'state': 'bct:look', 'table': [[0, 0, 0, 0, 1, 1, 1], [0, 0, 0, 0, 0, 0, 0]]},
                         'inner while persistent or within 4 of a log, outer at 5 to 7')
        self.assertEqual(entry['turn'], {'state': 'bct:t', 'weights': [1, 3],
                                         'selection': 'java-26.2-multipart-block-position'})
        block = self.oak_leaves()
        self.assertEqual(sorted(block['description']['states']),
                         ['bct:look', 'bct:persistent_bit', 'bct:t', 'bct:update_bit'])
        permutations = {permutation['condition']: permutation['components'] for permutation in block['permutations']}
        self.assertEqual(len(permutations), 4)
        turned = permutations["q.block_state('bct:look') == 1 && q.block_state('bct:t') == 1"]
        self.assertEqual(turned['minecraft:transformation'], {'rotation': [0, 270, 0]},
                         'Java y=90 turns the model like Blockbench')
        self.assertTrue(turned['minecraft:geometry']['identifier'].endswith('.1'))
        unturned = permutations["q.block_state('bct:look') == 0 && q.block_state('bct:t') == 0"]
        self.assertNotIn('minecraft:transformation', unturned)

    def test_each_look_keeps_its_own_weighted_turns(self):
        entry = next(entry for entry in self.data['leaves']['blocks'] if entry['vanilla'] == 'minecraft:jungle_leaves')
        self.assertEqual(entry['look'], {'state': 'bct:look', 'table': [[0, 0, 0, 0, 1, 1, 1], [0, 0, 0, 0, 0, 0, 0]]},
                         'the same models weighed differently are another look')
        self.assertEqual(entry['turn']['byLook'], [[1, 1], [1, 3]])
        block = json.loads((self.bp / 'blocks/m_jungle_leaves.json').read_text())['minecraft:block']
        self.assertEqual(block['description']['states']['bct:t'], {'values': {'min': 0, 'max': 1}})
        self.assertEqual(len(block['permutations']), 4)

    def test_geometry_fits_bedrock_and_never_names_the_default_material(self):
        oak = next(block for block in self.report['model_blocks'] if block['block'] == 'minecraft:oak_leaves')
        fits = {item['model'].rsplit('/', 1)[1]: item['fit_scale'] for item in oak['models']}
        self.assertEqual(fits['oak_inner.json'], 1.0, 'a model inside the limit keeps its size')
        self.assertLess(fits['oak_outer.json'], 1.0, 'a model past the 30 pixel limit is shrunk just enough')
        for path in (self.rp / 'models/blocks').glob('*.geo.json'):
            document = json.loads(path.read_text())
            self.assertEqual(geometry_errors(document), [])
            for bone in document['minecraft:geometry'][0]['bones']:
                for cube in bone.get('cubes', []):
                    for face in cube.get('uv', {}).values():
                        self.assertNotEqual(face.get('material_instance'), '*')
        self.assertEqual(check_tree(self.bp, self.rp, samples_path()), [])

    def test_tinted_faces_take_the_foliage_color_and_keep_their_pbr_maps(self):
        block = self.oak_leaves()
        instances = block['components']['minecraft:material_instances']
        self.assertEqual({item.get('tint_method') for item in instances.values()}, {'default_foliage'})
        self.assertEqual({item['render_method'] for item in instances.values()}, {'alpha_test'})
        self.assertEqual(block['components']['minecraft:map_color'],
                         {'color': '#007C00', 'tint_method': 'default_foliage'})
        texture_set_path = self.rp / f"textures/blocks/{instances['*']['texture']}.texture_set.json"
        texture_set = json.loads(texture_set_path.read_text())['minecraft:texture_set']
        self.assertIn('metalness_emissive_roughness_subsurface', texture_set,
                      'Vibrant Visuals and RTX materials come along')

    def test_leaves_behave_like_vanilla_leaves(self):
        components = self.oak_leaves()['components']
        self.assertEqual(components['minecraft:light_dampening'], 1)
        self.assertEqual(components['minecraft:destructible_by_explosion'], {'explosion_resistance': 0.2})
        self.assertEqual(components['minecraft:flammable'],
                         {'catch_chance_modifier': 30, 'destroy_chance_modifier': 60, 'lava_flammable': 'always'})
        self.assertEqual(components['minecraft:destructible_by_mining']['seconds_to_destroy'], 0.2)
        self.assertIn('minecraft:is_hoe_item_destructible', components['minecraft:tags'])
        self.assertEqual(components['bct:leaf'], {})
        decay = self.data['leaves']['decay']
        self.assertEqual((decay['bedrockDistance'], decay['javaDistance']), (4, 6))
        self.assertIn('minecraft:oak_log', self.data['leaves']['logs'])

    def test_drops_follow_the_java_loot_table(self):
        loot = json.loads((self.bp / 'loot_tables/bct/bct_pack_test/m_oak_leaves.json').read_text())
        pools = loot['pools']
        self.assertEqual(pools[0], {'rolls': 1, 'conditions': [{'condition': 'match_tool', 'item': 'minecraft:shears'}],
                                    'entries': [{'type': 'item', 'name': 'minecraft:oak_leaves'}]})
        java = {'minecraft:oak_sapling': SAPLING_CHANCES, 'minecraft:stick': STICK_CHANCES[:4],
                'minecraft:apple': APPLE_CHANCES[:4]}
        for item, chances in java.items():
            mine = [pool for pool in pools[1:] if pool['entries'][0]['name'] == item]
            base = next(pool for pool in mine
                        if not any(condition['condition'] == 'match_tool' for condition in pool['conditions']))
            chance = base['conditions'][0]['chance']
            self.assertAlmostEqual(chance, chances[0], places=6)
            for level in (1, 2, 3):
                extra = next(pool['conditions'][1]['chance'] for pool in mine
                             if pool['conditions'][0].get('enchantments', [{}])[0]
                             .get('levels', {}).get('range_min') == level)
                self.assertAlmostEqual(1 - (1 - chance) * (1 - extra), chances[level], places=6,
                                       msg=f'{item} with Fortune {level}')
        stick = next(pool for pool in pools if pool['entries'][0]['name'] == 'minecraft:stick')
        self.assertEqual(stick['entries'][0]['functions'],
                         [{'function': 'set_count', 'count': {'min': 1, 'max': 2}}, {'function': 'explosion_decay'}])


class BedrockLootTests(unittest.TestCase):
    @staticmethod
    def loot(entry):
        return bedrock_loot({'pools': [{'entries': [entry]}]}, lambda java: java, 'minecraft:oak_leaves')

    def test_java_loot_without_a_bedrock_form_is_refused(self):
        stick = {'type': 'minecraft:item', 'name': 'minecraft:stick'}
        with self.assertRaisesRegex(ValueError, 'function has no Bedrock form: apply_bonus'):
            self.loot({**stick, 'functions': [{'function': 'minecraft:apply_bonus'}]})
        with self.assertRaisesRegex(ValueError, 'conditions have no Bedrock form: random_chance'):
            self.loot({**stick, 'conditions': [{'condition': 'minecraft:random_chance', 'chance': 0.5}]})
        with self.assertRaisesRegex(ValueError, 'enchantment other than Fortune'):
            looting = {'condition': 'minecraft:table_bonus', 'enchantment': 'minecraft:looting', 'chances': [0.5]}
            self.loot({**stick, 'conditions': [looting]})

    def test_java_26_loot_tables_give_the_same_drops_as_the_older_format(self):
        # Read as the older format, the 26 form lost every chance: leaves, a sapling, sticks and an apple each break.
        newer = bedrock_loot(java_26_leaf_loot('minecraft:oak_leaves', 'minecraft:oak_sapling'), lambda java: java,
                             'minecraft:oak_leaves')
        older = bedrock_loot(java_leaf_loot('minecraft:oak_leaves', 'minecraft:oak_sapling', apple=True),
                             lambda java: java, 'minecraft:oak_leaves')
        self.assertEqual(newer, older)
        self.assertFalse(any(entry['name'] == 'minecraft:oak_leaves' for pool in newer['pools'][1:]
                             for entry in pool['entries']), 'the leaf block drops only for shears')

    def test_counts_and_explosion_decay_carry_over(self):
        loot = self.loot({'type': 'minecraft:item', 'name': 'minecraft:stick',
                          'conditions': [{'condition': 'minecraft:survives_explosion'}],
                          'functions': [{'function': 'minecraft:set_count', 'count': 2.0}]})
        self.assertEqual(loot['pools'][1], {'rolls': 1, 'conditions': [], 'entries': [
            {'type': 'item', 'name': 'minecraft:stick',
             'functions': [{'function': 'set_count', 'count': 2}, {'function': 'explosion_decay'}]}]},
            'a sure drop rolls without a chance; survives_explosion becomes explosion decay')
        self.assertEqual(len(loot['pools']), 2, 'no Fortune pools without table_bonus')


if __name__ == '__main__':
    unittest.main()
