"""Faces bind to a Java sprite only when the Bedrock edition shows exactly its pixels."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from java_bedrock_reference import resolve_reference
from java_pack_api import PackStack


class ReferenceBindingTests(unittest.TestCase):
    def test_exact_bindings_keep_caps_distinct_and_reject_state_ambiguity(self):
        def png(color):
            output = io.BytesIO()
            Image.new('RGBA', (4, 4), color).save(output, format='PNG')
            return output.getvalue()
        side = png((20, 40, 60, 255))
        cap = png((80, 60, 40, 255))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            java = root / 'java.zip'
            bedrock = root / 'bedrock.mcpack'
            with zipfile.ZipFile(java, 'w') as archive:
                archive.writestr('pack.mcmeta', '{}')
                archive.writestr('assets/minecraft/textures/block/oak_log.png', side)
                archive.writestr('assets/minecraft/textures/block/oak_log_top.png', cap)
                archive.writestr('assets/minecraft/optifine/ctm/copy.png', side)
            with zipfile.ZipFile(bedrock, 'w') as archive:
                archive.writestr('textures/blocks/bark.png', side)
                archive.writestr('textures/blocks/endgrain.png', cap)
            before = java.read_bytes(), bedrock.read_bytes()
            blocks = {'oak_log': {'textures': {'side': 'bark', 'up': 'cap', 'down': 'cap'}},
                      'mixed': {'textures': 'states'}}
            terrain = {'bark': {'textures': 'textures/blocks/bark'}, 'cap': {'textures': 'textures/blocks/endgrain'},
                       'states': {'textures': ['textures/blocks/bark', 'textures/blocks/endgrain']}}
            with PackStack([java]) as stack:
                result = resolve_reference(stack, bedrock, blocks, terrain)
            self.assertEqual(result['mapped_faces'], 6)
            mapping = result['baseTextures']['minecraft:oak_log']
            self.assertEqual(mapping['up'], 'assets/minecraft/textures/block/oak_log_top.png')
            self.assertEqual(mapping['north'], 'assets/minecraft/textures/block/oak_log.png')
            self.assertNotIn('minecraft:mixed', result['baseTextures'])
            self.assertEqual(before, (java.read_bytes(), bedrock.read_bytes()))


if __name__ == '__main__':
    unittest.main()
