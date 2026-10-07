import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from common import read_json, write_json
from java_entities import export_entities, prepare_entities
from java_entity_molang import molang_evaluate, molang_valid
from java_pack_api import PackStack


def png(size, color):
    raw = io.BytesIO()
    Image.new('RGBA', size, color).save(raw, format='PNG')
    return raw.getvalue()


def entity(identifier, textures, geometry, controllers, scripts=None):
    description = {'identifier': identifier, 'min_engine_version': '1.8.0', 'materials': {'default': 'thing'},
                   'textures': textures, 'geometry': geometry, 'render_controllers': controllers}
    if scripts:
        description['scripts'] = scripts
    return {'format_version': '1.10.0', 'minecraft:client_entity': {'description': description}}


def legacy_entity(identifier, textures, geometry, controllers):
    """A format 1.8.0 client entity: animations play through animation_controllers, there is no animate script."""
    description = {'identifier': identifier, 'min_engine_version': '1.8.0', 'materials': {'default': 'thing'},
                   'textures': textures, 'geometry': geometry, 'animations': {'walk': 'animation.mob.walk'},
                   'animation_controllers': [{'move': 'controller.animation.mob.move'}],
                   'render_controllers': controllers, 'scripts': {'pre_animation': ['v.vanilla = 1;']}}
    return {'format_version': '1.8.0', 'minecraft:client_entity': {'description': description}}


def geometry(identifier, bones):
    return {'format_version': '1.12.0', 'minecraft:geometry': [
        {'description': {'identifier': identifier, 'texture_width': 64, 'texture_height': 32}, 'bones': bones}]}


MOB_BONES = [{'name': 'body', 'pivot': [0, 12, 0], 'cubes': [{'origin': [-4, 6, -4], 'size': [8, 6, 8], 'uv': [0, 0]}]},
             {'name': 'head', 'parent': 'body', 'pivot': [0, 12, -4], 'cubes': [{'origin': [-2, 10, -8], 'size': [4, 4, 4], 'uv': [32, 0]}]}]

MODEL = {'textureSize': [64, 32], 'models': [
    {'part': 'head', 'id': 'head', 'invertAxis': 'xy', 'translate': [0, -12, 4],
     'submodels': [{'id': 'horn', 'invertAxis': 'xy', 'translate': [0, 14, -6], 'rotate': [20, 0, 0],
                    'boxes': [{'coordinates': [-1, 0, -1, 2, 3, 2], 'uvNorth': [0, 0, 2, 3], 'uvUp': [2, 0, 4, 2]}]}],
     'animations': [{'horn.rx': 'torad(20) + sin(limb_swing) * limb_speed'}]}]}

# Reads its own rotation and the entity id, so the converted model needs initial values too.
MODEL_WITH_INITIAL_VALUES = json.loads(json.dumps(MODEL))
MODEL_WITH_INITIAL_VALUES['models'][0]['animations'] = [{'horn.rx': 'torad(20) + sin(limb_swing) * limb_speed',
                                                         'horn.ry': 'horn.rx * 0.5', 'var.seed': 'id'}]
# The script keys of vanilla client entities in format 1.8.0: no animate and no initialize.
FORMAT_1_8_SCRIPT_KEYS = {'pre_animation', 'scale', 'scaleX', 'scaleY', 'scaleZ'}

TABLES = {'models': {'models': {'mob': {'entity': 'test:mob', 'geometry': 'default', 'bones': {'head': 'head', 'body': 'body'},
                                        'java_parts': {'head': {'pivot': [0, 12, -4]}, 'body': {'pivot': [0, 12, 0]}}},
                                'cold_mob': {'entity': 'test:mob', 'geometry': 'cold', 'bones': {'head': 'head', 'body': 'body'}},
                                'other': {'entity': 'test:other', 'geometry': 'default', 'bones': {'head': 'head'}}},
                     'unsupported': {'mob_saddle': 'drawn inside the mob geometry'}, 'families': {}},
          'textures': {'direct': {'entity/mob/mob_temperate': [['textures/entity/mob/mob', 1.0]]}, 'legacy_paths': {},
                       'legacy_targets': {}, 'model_texture_pairs': {}}}


class Fixture:
    def __init__(self, root, models, extra_files=None, share=True):
        self.root = Path(root)
        samples = self.root / 'samples'
        climate = {'pre_animation': ["t.variant = query.property('minecraft:climate_variant');",
                                     "v.index = (t.variant == 'temperate') ? 0 : ((t.variant == 'warm') ? 1 : 2);"]}
        write_json(samples / 'entity/mob.entity.json', entity('test:mob', {'default': 'textures/entity/mob/mob', 'cold': 'textures/entity/mob/mob_cold'},
                                                               {'default': 'geometry.mob'}, ['controller.render.mob'], climate))
        other_texture = 'textures/entity/mob/mob' if share else 'textures/entity/other/other'
        write_json(samples / 'entity/other.entity.json', entity('test:other', {'default': other_texture},
                                                                 {'default': 'geometry.mob'}, ['controller.render.mob']))
        write_json(samples / 'models/entity/mob.geo.json', geometry('geometry.mob', MOB_BONES))
        write_json(samples / 'render_controllers/mob.render_controllers.json', {'format_version': '1.8.0', 'render_controllers': {
            'controller.render.mob': {'arrays': {'textures': {'Array.t': ['Texture.default', 'Texture.default', 'Texture.cold']}},
                                      'geometry': 'Geometry.default', 'materials': [{'*': 'Material.default'}], 'textures': ['Array.t[v.index]']}}})
        (samples / 'textures/entity/mob').mkdir(parents=True)
        Image.new('RGBA', (64, 32), (0, 0, 0, 255)).save(samples / 'textures/entity/mob/mob.png')
        Image.new('RGBA', (64, 32), (0, 0, 0, 255)).save(samples / 'textures/entity/mob/mob_cold.png')
        self.samples = samples
        self.jar = self.root / 'vanilla.jar'
        with zipfile.ZipFile(self.jar, 'w') as jar:
            jar.writestr('assets/minecraft/textures/entity/mob/mob_temperate.png', png((64, 32), (1, 2, 3, 255)))
        self.pack = self.root / 'author.zip'
        with zipfile.ZipFile(self.pack, 'w') as pack:
            pack.writestr('pack.mcmeta', '{"pack": {"pack_format": 88, "description": "test"}}')
            for name, data in models.items():
                pack.writestr('assets/minecraft/optifine/cem/%s.jem' % name, json.dumps(data))
            pack.writestr('assets/minecraft/textures/entity/mob/mob_temperate.png', png((256, 128), (150, 90, 60, 255)))
            pack.writestr('assets/minecraft/textures/entity/mob/mob_temperate_n.png', png((256, 128), (128, 128, 255, 255)))
            pack.writestr('assets/minecraft/textures/entity/mob/mob_temperate_s.png', png((256, 128), (180, 10, 0, 255)))
            for name, data in (extra_files or {}).items():
                pack.writestr(name, data)

    def prepare(self):
        with PackStack([self.pack]) as stack:
            return prepare_entities(stack, self.jar, self.samples, self.root / 'out', models_table=TABLES['models'],
                                    texture_table=TABLES['textures'])


class PrepareTests(unittest.TestCase):
    def test_model_texture_animation_and_entity_override(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = Fixture(folder, {'mob': MODEL, 'mob_saddle': MODEL})
            plan = fixture.prepare()
            report = read_json(Path(plan['report']))
            self.assertEqual(report['models']['mob_saddle']['status'], 'unsupported')
            mob = report['models']['mob']
            self.assertEqual(mob['status'], 'converted')
            # geometry.mob is shared with another entity, so the converted model gets its own identifier.
            self.assertEqual(mob['geometry'], 'geometry.bct.mob')
            staged = Path(plan['staged'])
            document = read_json(staged / 'entity/mob.entity.json')['minecraft:client_entity']['description']
            self.assertEqual(document['geometry']['default'], 'geometry.bct.mob')
            self.assertIn('bct_cem_mob', document['animations'])
            self.assertTrue(all(molang_valid(line) for line in document['scripts']['pre_animation']))
            geo = read_json(staged / 'models/entity/bct_mob.geo.json')['minecraft:geometry'][0]
            names = {bone['name']: bone for bone in geo['bones']}
            self.assertEqual(names['head']['cubes'], [])
            self.assertEqual(names['cem_horn']['rotation'], [-20.0, 0.0, 0.0])
            # The texture is shared with another entity that keeps the vanilla model: it moves to a path of its own.
            written = {item['target'] for item in mob['textures']['written']}
            self.assertEqual(written, {'textures/entity/bct/mob'})
            self.assertEqual(document['textures']['default'], 'textures/entity/bct/mob')
            with Image.open(staged / 'textures/entity/bct/mob.png') as texture:
                self.assertEqual(texture.size, (256, 128))
            texture_set = read_json(staged / 'textures/entity/bct/mob.texture_set.json')['minecraft:texture_set']
            self.assertEqual(texture_set['normal'], 'mob_bct_normal')
            self.assertEqual(texture_set['metalness_emissive_roughness_subsurface'], 'mob_bct_mers')

    def test_each_renderer_gets_its_files(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = Fixture(folder, {'mob': MODEL}, share=False)
            plan = fixture.prepare()
            for renderer in ('classic', 'vv', 'rtx'):
                pack = Path(folder) / ('rp-' + renderer)
                (pack / 'textures/entity/mob').mkdir(parents=True)
                (pack / 'textures/entity/mob/mob.tga').write_bytes(b'old')
                export_entities(plan, pack, renderer)
                self.assertTrue((pack / 'textures/entity/mob/mob.png').is_file())
                self.assertFalse((pack / 'textures/entity/mob/mob.tga').exists())
                self.assertTrue((pack / 'models/entity/bct_mob.geo.json').is_file())
                self.assertTrue((pack / 'animations/bct_cem_mob.animation.json').is_file())
                has_set = (pack / 'textures/entity/mob/mob.texture_set.json').is_file()
                self.assertEqual(has_set, renderer == 'vv', renderer)

    def test_climate_variant_gets_a_render_controller_that_picks_its_geometry(self):
        cold = json.loads(json.dumps(MODEL))
        cold['models'][0]['animations'] = []
        with tempfile.TemporaryDirectory() as folder:
            fixture = Fixture(folder, {'mob': MODEL, 'cold_mob': cold})
            plan = fixture.prepare()
            staged = Path(plan['staged'])
            description = read_json(staged / 'entity/mob.entity.json')['minecraft:client_entity']['description']
            self.assertEqual(description['geometry']['cold'], 'geometry.bct.cold_mob')
            self.assertEqual(description['render_controllers'], ['controller.render.bct.mob'])
            controller = read_json(staged / 'render_controllers/bct_mob.render_controllers.json')['render_controllers']['controller.render.bct.mob']
            self.assertEqual(controller['arrays']['geometries']['Array.bct_geos'], ['Geometry.default', 'Geometry.default', 'Geometry.cold'])
            self.assertEqual(controller['geometry'], 'Array.bct_geos[v.index]')
            animate = description['scripts']['animate']
            self.assertIn({'bct_cem_mob': '!query.is_baby && v.index == 0'}, animate)


class FormatTests(unittest.TestCase):
    def test_format_1_8_entity_plays_the_model_through_an_animation_controller(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = Fixture(folder, {'mob': MODEL_WITH_INITIAL_VALUES}, share=False)
            write_json(fixture.samples / 'entity/mob.entity.json',
                       legacy_entity('test:mob', {'default': 'textures/entity/mob/mob'}, {'default': 'geometry.mob'},
                                     ['controller.render.mob']))
            plan = fixture.prepare()
            staged = Path(plan['staged'])
            document = read_json(staged / 'entity/mob.entity.json')
            description = document['minecraft:client_entity']['description']
            # The game rejects animate and initialize in format 1.8.0 ("child 'animate' not valid here").
            self.assertEqual(document['format_version'], '1.8.0')
            self.assertLessEqual(set(description['scripts']), FORMAT_1_8_SCRIPT_KEYS)
            self.assertEqual(description['animations']['bct_cem_mob'], 'animation.bct.cem.mob')
            self.assertEqual(description['animation_controllers'],
                             [{'move': 'controller.animation.mob.move'},
                              {'bct_cem_controller': 'controller.animation.bct.cem.mob'}])
            controller_path = 'animation_controllers/bct_cem_mob.animation_controllers.json'
            self.assertIn(controller_path, [entry['path'] for entry in plan['files']])
            controllers = read_json(staged / controller_path)
            self.assertEqual(controllers['format_version'], '1.10.0')
            states = controllers['animation_controllers']['controller.animation.bct.cem.mob']['states']
            self.assertEqual(states['default']['animations'], ['bct_cem_mob'])
            # The initial values come after the vanilla lines and before the model's own, and run once.
            lines = description['scripts']['pre_animation']
            self.assertEqual(lines[0], 'v.vanilla = 1;')
            self.assertTrue(lines[1].startswith('(!(v.bct_initialized ?? 0)) ? {'), lines[1])
            self.assertIn('v.cem_id = math.random(0, 1000000);', lines[1])
            self.assertTrue(all(molang_valid(line) for line in lines))
            environment = {}
            for line in lines:
                molang_evaluate(line, environment)
            environment['variable.cem_id'] = 7.0
            for line in lines:
                molang_evaluate(line, environment)
            self.assertEqual(environment['variable.cem_id'], 7.0)

    def test_format_1_10_entity_keeps_the_animate_and_initialize_scripts(self):
        with tempfile.TemporaryDirectory() as folder:
            plan = Fixture(folder, {'mob': MODEL_WITH_INITIAL_VALUES}, share=False).prepare()
            staged = Path(plan['staged'])
            scripts = read_json(staged / 'entity/mob.entity.json')['minecraft:client_entity']['description']['scripts']
            self.assertIn({'bct_cem_mob': '!query.is_baby && v.index == 0'}, scripts['animate'])
            self.assertIn('v.cem_id = math.random(0, 1000000);', scripts['initialize'])
            self.assertFalse((staged / 'animation_controllers').exists())

    def test_restated_render_controller_keeps_the_format_of_its_source(self):
        cold = json.loads(json.dumps(MODEL))
        cold['models'][0]['animations'] = []
        with tempfile.TemporaryDirectory() as folder:
            fixture = Fixture(folder, {'mob': MODEL, 'cold_mob': cold})
            controller_file = fixture.samples / 'render_controllers/mob.render_controllers.json'
            controllers = read_json(controller_file)
            # Vanilla uses filter_lighting only in format 1.10.0 render controllers.
            controllers['format_version'] = '1.10.0'
            controllers['render_controllers']['controller.render.mob']['filter_lighting'] = True
            write_json(controller_file, controllers)
            staged = Path(fixture.prepare()['staged'])
            restated = read_json(staged / 'render_controllers/bct_mob.render_controllers.json')
            self.assertEqual(restated['format_version'], '1.10.0')
            self.assertTrue(restated['render_controllers']['controller.render.bct.mob']['filter_lighting'])




class UnsetVariableTests(unittest.TestCase):
    def test_every_variable_a_script_or_animation_uses_gets_a_start_value(self):
        from java_entities import _unset_variables
        animations = {'animations': {'animation.bct.cem.cold_pig': {'bones': {'root': {
            'rotation': [0, 'v.cem_cold_pig_root_ry * 57.3', 'variable.cem_cold_pig_tilt'], 'position': ['query.v_x', 0, 0]}}}}}
        scripts = ['v.cem_cold_pig_frame_counter = v.cem_cold_pig_frame_counter + 1;', '(!(v.bct_initialized ?? 0)) ? {};']
        self.assertEqual(_unset_variables(animations, scripts, ['v.cem_cold_pig_tilt = 1;', 'v.other == 2;']),
                         ['v.cem_cold_pig_frame_counter = 0;', 'v.cem_cold_pig_root_ry = 0;'])


class ControllerTests(unittest.TestCase):
    def test_a_newer_format_entity_plays_its_vanilla_controllers_from_animate(self):
        # Some vanilla entities of format 1.10+ still list animation_controllers; restated in a pack the game
        # rejects that list, so the controllers move to animations and scripts.animate.
        with tempfile.TemporaryDirectory() as folder:
            fixture = Fixture(folder, {'mob': MODEL_WITH_INITIAL_VALUES}, share=False)
            document = legacy_entity('test:mob', {'default': 'textures/entity/mob/mob'}, {'default': 'geometry.mob'},
                                     ['controller.render.mob'])
            document['format_version'] = '1.10.0'
            document['minecraft:client_entity']['description']['scripts']['animate'] = ['walk']
            write_json(fixture.samples / 'entity/mob.entity.json', document)
            staged = Path(fixture.prepare()['staged'])
            description = read_json(staged / 'entity/mob.entity.json')['minecraft:client_entity']['description']
            self.assertNotIn('animation_controllers', description)
            self.assertEqual(description['animations']['move'], 'controller.animation.mob.move')
            self.assertIn('move', description['scripts']['animate'])
            self.assertIn('walk', description['scripts']['animate'])


if __name__ == '__main__':
    unittest.main()
