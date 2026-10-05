"""Material aliases for entity carriers, registered in the pack's entity.material.

Carriers draw with the vanilla entity, entity_alphatest and entity_alphablend
materials. Generated aliases inherit everything from those parents and change
one thing each:

- bct_uv*: adds USE_UV_ANIM so a render controller's uv_anim moves the UVs
  (atlas tiles, animated textures).
- bct_point* and bct_bilinear*: set sampler zero's texture filter, an opt-in
  experiment. Mip allocation is neither requested nor verified.

Minecraft only loads material definitions from materials/entity.material, so
the aliases are merged into that file and unrelated definitions in it are kept.
"""
import json
from pathlib import Path


CARRIER_UV_MATERIAL_PATH = 'materials/entity.material'
_MATERIAL_FILE_VERSION = '1.0.0'
# Vanilla parents and their UV animation aliases. Aliases of the plain
# 'entity' parent carry no parent suffix.
_UV_ALIASES = {
    'entity': 'bct_uv',
    'entity_alphatest': 'bct_uv_entity_alphatest',
    'entity_alphablend': 'bct_uv_entity_alphablend',
}
_SAMPLER_FILTERS = {'point': 'Point', 'bilinear': 'Bilinear'}


def carrier_material(material, animated=False, texture_filter=None):
    """Name the material a carrier client entity draws with.

    Without a filter: the vanilla parent, or its UV alias when animated. With
    a filter: bct_<filter>[_uv][_<parent>], e.g. bct_bilinear_uv_entity_alphatest.
    """
    if texture_filter is None:
        # The lookup also rejects unknown parents.
        uv_alias = _UV_ALIASES[material]
        return uv_alias if animated else material
    if texture_filter not in _SAMPLER_FILTERS:
        raise ValueError('Carrier texture filter must be point or bilinear')
    if material not in _UV_ALIASES:
        raise ValueError('Unknown carrier material parent: ' + str(material))
    parent_suffix = '' if material == 'entity' else '_' + material
    return 'bct_' + texture_filter + ('_uv' if animated else '') + parent_suffix


def carrier_uv_materials():
    """UV animation alias definitions, keyed 'alias:parent'."""
    return {uv_alias + ':' + parent: {'+defines': ['USE_UV_ANIM']}
            for parent, uv_alias in _UV_ALIASES.items()}


def carrier_filter_materials(texture_filter):
    """Sampler alias definitions for one filter, plain and animated, keyed 'alias:parent'.

    Only sampler zero changes and the parent's render state is inherited.
    Vanilla does the same in ui.material, where ui_texture_and_color_blur
    swaps an inherited Point sampler zero for Bilinear. Clamp matches sampler
    zero of entity_multitexture in entity.material.
    """
    if texture_filter not in _SAMPLER_FILTERS:
        raise ValueError('Carrier texture filter must be point or bilinear')
    definitions = {}
    for parent in _UV_ALIASES:
        for animated in (False, True):
            alias = carrier_material(parent, animated, texture_filter)
            definitions[alias + ':' + parent] = _sampler_definition(texture_filter, animated)
    return definitions


def write_carrier_uv_materials(resource_pack):
    """Merge the UV animation aliases into the pack's entity.material."""
    return _merge_material_definitions(resource_pack, carrier_uv_materials())


def write_carrier_filter_materials(resource_pack, texture_filter):
    """Merge one filter's sampler aliases into the pack's entity.material."""
    return _merge_material_definitions(resource_pack, carrier_filter_materials(texture_filter))


def apply_carrier_filter(resource_pack, texture_filter='bilinear'):
    """Move every generated carrier onto the sampler alias for texture_filter.

    All client entities are checked before anything is written, so an
    unrecognised material leaves the pack untouched. Textures, geometry and
    render controllers are never modified.
    """
    definitions = carrier_filter_materials(texture_filter)
    resource_pack = Path(resource_pack).resolve()
    manifest = json.loads((resource_pack / 'manifest.json').read_text(encoding='utf-8-sig'))
    if not any(module.get('type') == 'resources' for module in manifest.get('modules', [])):
        raise ValueError('Carrier filter requires a generated resource pack')
    updates = _filter_material_updates(resource_pack, texture_filter)
    registration = _merge_material_definitions(resource_pack, definitions)
    for path, document in updates:
        path.write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')
    output_files = [CARRIER_UV_MATERIAL_PATH] + [path.relative_to(resource_pack).as_posix() for path, _ in updates]
    return {'resource_pack': str(resource_pack), 'texture_filter': texture_filter,
            'sampler0': {'textureFilter': _SAMPLER_FILTERS[texture_filter], 'textureWrap': 'Clamp'},
            'client_bindings_changed': len(updates), 'material_file_changed': registration['changed'],
            'output_files': sorted(output_files),
            'other_sampler_indices_modified': False, 'parent_render_states_modified': False,
            'textures_modified': False, 'geometry_modified': False,
            'mipmap_allocation': 'unchanged and unverified',
            'renderer_verification': 'Staged sampler experiment; Classic and VV rendering not verified.'}


def _sampler_definition(texture_filter, animated):
    definition = {'+samplerStates': [{'samplerIndex': 0,
                                      'textureFilter': _SAMPLER_FILTERS[texture_filter],
                                      'textureWrap': 'Clamp'}]}
    if animated:
        definition['+defines'] = ['USE_UV_ANIM']
    return definition


def _material_origins():
    """Map each material a generated carrier can use to its (parent, animated)."""
    origins = {carrier_material(parent, animated, texture_filter): (parent, animated)
               for parent in _UV_ALIASES
               for animated in (False, True)
               for texture_filter in (None, *_SAMPLER_FILTERS)}
    # Carriers tinted at draw time use the vanilla change_color materials.
    origins.update({'entity_change_color': ('entity', False),
                    'entity_alphatest_change_color': ('entity_alphatest', False)})
    return origins


def _filter_material_updates(resource_pack, texture_filter):
    """Client documents whose material changes, as (path, updated document)."""
    origins = _material_origins()
    updates = []
    for path in sorted((resource_pack / 'entity').glob('bct_*.entity.json')):
        document = json.loads(path.read_text(encoding='utf-8-sig'))
        description = document.get('minecraft:client_entity', {}).get('description', {})
        if not description.get('identifier', '').startswith('bct:'):
            continue
        material = description.get('materials', {}).get('default')
        if material not in origins:
            raise ValueError('Unrecognized generated carrier material: ' + str(material))
        parent, animated = origins[material]
        selected = carrier_material(parent, animated, texture_filter)
        if material != selected:
            description['materials']['default'] = selected
            updates.append((path, document))
    return updates


def _alias_name(key):
    """Material keys read 'alias:parent'."""
    return key.split(':', 1)[0]


def _merge_material_definitions(resource_pack, definitions):
    """Replace this module's aliases in entity.material, keeping every other definition."""
    path = Path(resource_pack) / CARRIER_UV_MATERIAL_PATH
    if path.exists():
        document = json.loads(path.read_text(encoding='utf-8-sig'))
    else:
        document = {'materials': {'version': _MATERIAL_FILE_VERSION}}
    materials = document.get('materials')
    if not isinstance(materials, dict) or materials.get('version') != _MATERIAL_FILE_VERSION:
        raise ValueError('Expected an entity material document with version 1.0.0')
    # An alias may already exist under another parent; it is replaced, not duplicated.
    replaced_aliases = {_alias_name(key) for key in definitions}
    merged = {key: value for key, value in materials.items() if _alias_name(key) not in replaced_aliases}
    merged.update(definitions)
    changed = merged != materials
    if changed:
        document['materials'] = merged
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')
    return {'path': str(path), 'changed': changed,
            'aliases': [_alias_name(key) for key in definitions],
            'textures_modified': False, 'client_entities_modified': False}
