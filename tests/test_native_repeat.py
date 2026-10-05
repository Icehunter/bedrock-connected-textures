import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from native_repeat import repeat_index, repeat_states, build_native_repeat
from engine_package import engine_dependency


class NativeRepeatTests(unittest.TestCase):
    def test_native_repeat_coordinates_match_runtime_in_all_faces_across_zero(self):
        source = Path(__file__).resolve().parents[1] / 'engine/tiles.mjs'
        script = ("import {repeatIndex} from '" + source.as_uri() + "';let result=[];"
                  "for(let x=-9;x<=9;x++)for(let y=-4;y<=4;y++)"
                  "for(const face of ['north','east','south','west','up','down'])"
                  "result.push([x,y,face,repeatIndex({x,y,z:-x},face,3,2)]);console.log(JSON.stringify(result));")
        vectors = json.loads(subprocess.check_output(['node', '--input-type=module', '-e', script], text=True))
        for x, y, face, expected in vectors:
            self.assertEqual(repeat_index((x, y, -x), face, 3, 2), expected)
            states = repeat_states((x, y, -x), 3, 2)
            self.assertEqual(repeat_index(tuple(states.values()), face, 3, 2), expected)

    def test_native_repeat_uses_bounded_states_and_exact_face_materials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = root / 'input'
            pack.mkdir()
            for index in range(7):
                Image.new('RGBA', (8, 8), (index, 20, 30, 255)).save(pack / f'{index}.png')
            document = {'format_version': 1, 'rules': [{
                'id': 'brick', 'method': 'repeat', 'blocks': ['minecraft:brick_block'],
                'tiles': [str(n) for n in range(6)], 'width': 3, 'height': 2,
                'faces': ['north', 'south', 'east', 'west'], 'baseTiles': {'up': '6', 'down': '6'}}]}
            rules = root / 'rules.json'
            rules.write_text(json.dumps(document))
            for mode in ('classic', 'vv', 'rtx'):
                archive = build_native_repeat(pack, rules, 'test', root, mode)
                with zipfile.ZipFile(archive) as output:
                    block = json.loads(output.read('NativeRepeat_BP/blocks/repeat_test_brick.json'))['minecraft:block']
                    permutations = block['permutations']
                    self.assertEqual(len(permutations), 72)
                    states = block['description']['states']
                    self.assertEqual([len(values) for values in states.values()], [6, 2, 6])
                    instances = [row['components']['minecraft:material_instances'] for row in permutations]
                    self.assertTrue(all(len(faces) == 7 for faces in instances))
                    up_textures = {faces['up']['texture'] for faces in instances}
                    self.assertEqual(len(up_textures), 1)
                    self.assertEqual(block['components']['bct:update_repeat'], {'height': 2, 'period': 6})
                    self.assertFalse([name for name in output.namelist() if '/scripts/' in name],
                                     'the engine registers the repeat component')
                    behavior = json.loads(output.read('NativeRepeat_BP/manifest.json'))
                    self.assertIn(engine_dependency(), behavior['dependencies'])
                    manifest = json.loads(output.read('NativeRepeat_RP/manifest.json'))
                    expected = None if mode == 'classic' else ['pbr'] if mode == 'vv' else ['raytraced']
                    self.assertEqual(manifest.get('capabilities'), expected)


if __name__ == '__main__':
    unittest.main()
