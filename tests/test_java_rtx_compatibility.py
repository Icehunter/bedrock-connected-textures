import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'converter'))
from java_rtx_compatibility import native_rtx_compatibility


class NativeRTXTests(unittest.TestCase):
    def material(self,root,extra=None):
        folder=Path(root)/'textures/blocks';folder.mkdir(parents=True)
        for name in ('stone','stone_normal','stone_mer'):
            Image.new('RGB',(4,4)).save(folder/(name+'.png'))
        layers={'color':'stone','normal':'stone_normal','metalness_emissive_roughness':'stone_mer'}
        layers.update(extra or {})
        (folder/'stone.texture_set.json').write_text(json.dumps({'minecraft:texture_set':layers}))
        return folder

    def test_native_material_is_eligible_without_claiming_entity_ctm(self):
        with tempfile.TemporaryDirectory() as root:
            self.material(root)
            result=native_rtx_compatibility(root)
            self.assertTrue(result['native_block_pbr_supported'])
            self.assertFalse(result['entity_carrier_pbr_supported'])

    def test_mutually_exclusive_normal_and_height_rejects_tag(self):
        with tempfile.TemporaryDirectory() as root:
            self.material(root,{'heightmap':'height'})
            self.assertFalse(native_rtx_compatibility(root)['native_block_pbr_supported'])

    def test_missing_or_wrong_sized_channel_rejects_tag(self):
        with tempfile.TemporaryDirectory() as root:
            folder=self.material(root)
            Image.new('RGB',(2,2)).save(folder/'stone_mer.png')
            self.assertFalse(native_rtx_compatibility(root)['native_block_pbr_supported'])
            (folder/'stone_mer.png').unlink()
            self.assertFalse(native_rtx_compatibility(root)['native_block_pbr_supported'])
