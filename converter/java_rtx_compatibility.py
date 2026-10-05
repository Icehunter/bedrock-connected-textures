"""Check native block PBR separately from entity carriers."""
from pathlib import Path

from PIL import Image
from common import read_json


def native_rtx_compatibility(resource_pack):
    root=Path(resource_pack).resolve(); failures=[]; count=0
    for descriptor in sorted((root/'textures/blocks').glob('*.texture_set.json')):
        data=read_json(descriptor).get('minecraft:texture_set',{})
        if 'color' not in data or ('normal' in data and 'heightmap' in data) or (
            'metalness_emissive_roughness' in data and 'metalness_emissive_roughness_subsurface' in data):
            failures.append({'path':descriptor.relative_to(root).as_posix(),'reason':'invalid_layer_combination'})
            continue
        sizes=[]
        for channel,value in data.items():
            if not isinstance(value,str): continue
            path=descriptor.parent/(value+'.png')
            if not path.is_file():
                failures.append({'path':descriptor.relative_to(root).as_posix(),'channel':channel,'reason':'missing_image'})
                continue
            with Image.open(path) as image:
                sizes.append(image.size)
                expected=('L',) if channel=='heightmap' else ('RGB','RGBA')
                if image.mode not in expected:
                    failures.append({'path':descriptor.relative_to(root).as_posix(),'channel':channel,'reason':'invalid_channel_count'})
        if len(set(sizes))>1:
            failures.append({'path':descriptor.relative_to(root).as_posix(),'reason':'material_dimensions_differ'})
        count+=1
    return {'native_block_pbr_supported':count>0 and not failures,
            'native_block_texture_sets':count,'invalid_texture_sets':failures,
            'entity_carrier_pbr_supported':False,
            'entity_item_particle_pbr_supported_in_rtx':False,
            'labpbr_channels_preserved':True,'in_game_verified':False,
            'limitations':['RTX PBR applies to native blocks; entity carrier faces do not gain RTX PBR from a manifest capability.',
                           'LabPBR height, ambient occlusion, dielectric F0, metal identities and porosity require renderer support beyond MER/normal.']}
