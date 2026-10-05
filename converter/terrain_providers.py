"""Build the terrain transition add-on from provider files.

A provider file lists surface effects (grass and sand edges that spill onto
neighbouring blocks) and the rules that place them. An effect with a
native_block draws as a custom surface block in the empty cell above its host
(terrain_native); the others draw on entity carriers, whose geometry, textures
and render controllers this module writes. The pack's own overlay surface
blocks (overlay_surfaces.build) can join the same packs.

Key decisions:
- Pack UUIDs derive from the pack key, so rebuilding a key updates the
  installed pack instead of adding a second one.
- Carrier tiles are 256 pixels square, the native texture resolution, and each
  distinct tile image is written once.
- Bedrock scripts cannot read pack files, so the providers reach the engine
  inside the behavior pack's script, as one 'terrain' packet.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import uuid
import zipfile

import numpy as np
from PIL import Image, ImageDraw

from addon_package import packet, write_source_scripts
from biome_colors import resolve_colors
from carrier_structure import cloud_structure
from common import read_json, samples_path, terrain_pack, write_json
from edge_shapes import edge_alpha
from engine_package import engine_dependency
from terrain_entity_surface import carrier_controllers, emit_entity_surfaces
from terrain_layers import PIXELS_PER_TIER, TEXTURE_RESOLUTION, native_surface_height, surface_height
from terrain_masks import catalog
from terrain_native import emit_native_surfaces, source_ambient_occlusion

ROOT = Path(__file__).resolve().parents[1]
VERSION = [0, 3, 1]
# Carrier faces in the order of the engine's bct:face values (engine/terrain.mjs).
FACES = ['north', 'east', 'south', 'west', 'up', 'down']
TOP_FACE = FACES.index('up')
PACK_KEY_PATTERN = r'[a-z][a-z0-9-]*'
ENTITY_ID_PATTERN = r'[a-z][a-z0-9_]*:[a-z][a-z0-9_]*'
TILE_SIZE = 256
# The contact sheet previews the first 47 tiles, a full connected tile set.
PREVIEW_TILES = 47
# Native block geometry must stay inside the bounds observed to work in Creator, in pixels:
# X and Z are centred on the block, Y starts at its bottom.
NATIVE_GEOMETRY_MIN = (-22, -14, -22)
NATIVE_GEOMETRY_MAX = (22, 30, 22)
# Files both the terrain packs and the overlay surface packs write; their entries are merged.
SHARED_PACK_FILES = ('textures/terrain_texture.json', 'textures/flipbook_textures.json', 'blocks.json')
# Carrier clouds are black and of the minimum radius, which normal lingering potion clouds are not.
CARRIER_CLOUD = ('variable.cloud_radius == 0.5 && variable.color.r == 0 && variable.color.g == 0'
                 ' && variable.color.b == 0')


def _canonical_mask(raw):
    """Keep a diagonal bit only when both of its edges connect; otherwise it cannot change the tile."""
    mask = raw & 15
    for corner in range(4):
        edges = (1 << corner) | (1 << ((corner + 1) % 4))
        if raw & edges == edges and raw & (1 << (corner + 4)):
            mask |= 1 << (corner + 4)
    return mask


# The 47 neighbour masks of a full connected tile set: four edge bits, then four diagonal bits.
MASKS = sorted({_canonical_mask(raw) for raw in range(256)})


def identity(name, pack_key):
    """Stable UUID of one manifest entry, derived from the pack key."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'bct/terrain/' + pack_key + '/' + name))


def manifest(kind, pack_key, title='Terrain transitions', version=None):
    """Manifest of the behavior pack (kind 'bp', which carries the script) or the resource pack ('rp')."""
    version = version or VERSION
    module_type = 'data' if kind == 'bp' else 'resources'
    result = {
        'format_version': 2,
        'header': {
            'name': title,
            'description': 'Needs the Bedrock Connected Textures engine.',
            'uuid': identity(kind + '-header', pack_key),
            'version': version,
            'min_engine_version': [1, 26, 50],
        },
        'modules': [{'type': module_type, 'uuid': identity(kind + '-module', pack_key), 'version': version}],
    }
    if kind == 'bp':
        result['modules'].append({'type': 'script', 'language': 'javascript', 'entry': 'scripts/main.js',
                                  'uuid': identity('script-module', pack_key), 'version': version})
        result['dependencies'] = [{'module_name': '@minecraft/server', 'version': '2.10.0'},
                                  {'uuid': identity('rp-header', pack_key), 'version': version},
                                  engine_dependency()]
    else:
        result['capabilities'] = ['pbr']
    return result


def server_entity(identifier, passive=True):
    """Server definition of a carrier: the drawing properties the engine syncs, and no collision,
    hitbox, gravity or damage.

    Passive carriers run as area effect clouds, which have no AI and stay where they are placed.
    """
    description = {'identifier': identifier}
    if passive:
        description['runtime_identifier'] = 'minecraft:area_effect_cloud'
    description['is_spawnable'] = False
    description['is_summonable'] = True
    description['properties'] = {
        'bct:active': {'type': 'bool', 'default': False, 'client_sync': True},
        'bct:face': {'type': 'int', 'range': [0, 5], 'default': 0, 'client_sync': True},
        'bct:tile': {'type': 'int', 'range': [0, 255], 'default': 0, 'client_sync': True},
        'bct:palette': {'type': 'int', 'range': [0, 255], 'default': 0, 'client_sync': True},
        **{'bct:tint_' + channel: {'type': 'float', 'range': [0.0, 1.0], 'default': 1.0, 'client_sync': True}
           for channel in ('r', 'g', 'b')},
    }
    return {
        'format_version': '1.26.50',
        'minecraft:entity': {
            'description': description,
            'components': {
                'minecraft:type_family': {'family': ['bct_carrier']},
                'minecraft:collision_box': {'width': 0, 'height': 0},
                'minecraft:custom_hit_test': {'hitboxes': [{'width': 0.0, 'height': 0.0, 'pivot': [0, -16, 0]}]},
                'minecraft:physics': {'has_gravity': False, 'has_collision': False},
                'minecraft:damage_sensor': {'triggers': [{'cause': 'all', 'deals_damage': 'no'}]},
                'minecraft:persistent': {},
                'minecraft:health': {'value': 1, 'max': 1},
                'minecraft:knockback_resistance': {'value': 1},
            },
            'events': {},
        },
    }


def geometry(identifier, model=False, layer=0):
    """Carrier geometry: one plane bone per face (the engine shows the one it draws), or the oak root fan."""
    if model:
        # Oak root fan cubes, vendored from the original scenery model.
        cubes = read_json(ROOT / 'converter/data/oak-root-fan.geometry.json')
        bones = [{'name': 'root', 'pivot': [0, 0, 0], 'cubes': cubes}]
    else:
        bones = [_face_bone(face, origin, size) for face, (origin, size) in _face_planes(layer).items()]
    return {'format_version': '1.21.0', 'minecraft:geometry': [{
        'description': {
            'identifier': identifier,
            'texture_width': 16,
            'texture_height': 16,
            'visible_bounds_width': 2,
            'visible_bounds_height': 2,
            'visible_bounds_offset': [0, .5 if model else -.5, 0],
        },
        'bones': bones,
    }]}


def tint_linear(rgb, tint):
    """Bake a shader colour multiplier in linear light, then encode the result as sRGB PNG values."""
    encoded = np.asarray(rgb, dtype=float) / 255
    linear = np.where(encoded <= .04045, encoded / 12.92, ((encoded + .055) / 1.055) ** 2.4)
    linear *= np.asarray(tint)
    encoded = np.where(linear <= .0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - .055)
    return np.uint8(np.round(np.clip(encoded, 0, 1) * 255))


def merge_overlay_surfaces(overlay, bp, rp):
    """Copy the pack's overlay surface blocks (an overlay_surfaces.build folder) into the terrain packs.

    Atlas, flipbook and block sound lists are merged rather than replaced. Returns the overlay engine
    data, which travels in the terrain packet as one {'overlay': data} entry.
    """
    overlay = Path(overlay)
    for source, target in ((overlay / 'Overlay_BP', bp), (overlay / 'Overlay_RP', rp)):
        for path in sorted(source.rglob('*')):
            relative = path.relative_to(source).as_posix()
            if not path.is_file() or relative == 'manifest.json':
                continue
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if relative in SHARED_PACK_FILES and destination.exists():
                write_json(destination, _merged_pack_file(relative, read_json(destination), read_json(path)))
            else:
                shutil.copyfile(path, destination)
    return read_json(overlay / 'engine-data.json')


def build(provider_paths, pack_key, title='Terrain transitions', root=ROOT, samples=None, version=None,
          overlay=None):
    """Build the terrain add-on into dist/development; returns the archive path and the build report.

    overlay: an overlay_surfaces.build folder whose surface blocks and engine data join the add-on.
    """
    root = root.resolve()
    samples = (samples or samples_path(root)).resolve()
    if not re.fullmatch(PACK_KEY_PATTERN, pack_key):
        raise ValueError('Pack key must contain lowercase letters, digits and hyphens')
    version = version or VERSION
    providers = [read_json(path) for path in provider_paths]
    mask_catalogs = _prepare_providers(providers, root, samples)
    staging = root / ('build/terrain-' + pack_key)
    bp = staging / 'Terrain_BP'
    rp = staging / 'Terrain_RP'
    _clear_staging(bp, rp, pack_key)
    _start_packs(bp, rp, root, pack_key, title, version)
    destination = root / 'dist/development'
    destination.mkdir(parents=True, exist_ok=True)
    _hide_carrier_particles(rp, samples)
    entities, native_blocks = _emit_effects(providers, mask_catalogs, root, bp, rp, samples)
    _prune_terrain_atlas(bp, rp, providers)
    native_diagnostics = _native_diagnostics(providers, root, rp)
    # The pack's overlay surfaces join after the atlas pruning above, which only knows edge materials.
    entries = list(providers)
    overlay_data = None
    if overlay is not None:
        overlay_data = merge_overlay_surfaces(overlay, bp, rp)
        entries.append({'overlay': overlay_data})
    write_source_scripts(bp, [packet('terrain', pack_key, entries)])
    release = '.'.join(map(str, version))
    archive = _write_archive(staging, bp, rp, destination / f'terrain-{pack_key}-{release}.mcaddon')
    report = _build_report(archive, version, providers, entities, native_blocks, native_diagnostics, bp, rp,
                           overlay_data)
    write_json(destination / ('terrain-' + pack_key + '-build-report.json'), report)
    return archive, report


# Build steps.

def _prepare_providers(providers, root, samples):
    """Add what the engine reads at run time to each provider; returns the quarter mask catalogs by id."""
    mask_catalogs = {}
    for provider in providers:
        effects = provider['effects']
        if provider.get('exclusive_quarters'):
            mask_catalogs[provider['id']] = catalog(root, list(effects.values()))
        else:
            mask_catalogs[provider['id']] = None
        registered = _registered_block_names(samples)
        # The engine scans the world for these neighbours; names the game does not register are left out.
        provider['scan_sources'] = sorted({source for rule in provider['rules'] for source in rule['neighbors']
                                           if source in registered})
        if provider.get('biome_colors_source') == 'client_grass' and any(
                _grass_tinted(effect) and _drawn_by_entity(effect) for effect in effects.values()):
            # The engine colours grass on entity carriers from these; native blocks take theirs from the game.
            provider['biome_colors'] = resolve_colors(samples_path(root), terrain_pack(root))
            if any(_grass_tinted(effect) and not effect.get('native_block') for effect in effects.values()):
                # Carrier-only grass bakes every distinct biome colour into its own tiles.
                provider['biome_palette'] = sorted({tuple(color) for color in provider['biome_colors'].values()})
        for effect in effects.values():
            if effect['kind'] == 'surface' and _drawn_by_entity(effect):
                # Passive carriers are placed from a structure that sets up their cloud (carrier_structure).
                effect['spawn_structure'] = 'bct:' + effect['entity'].replace(':', '_')
    return mask_catalogs


def _registered_block_names(samples):
    blocks = read_json(samples / 'metadata/vanilladata_modules/mojang-blocks.json')
    return {item['name'] for item in blocks['data_items']}


def _grass_tinted(effect):
    return effect.get('biome_tint') == 'grass'


def _drawn_by_entity(effect):
    """Effects without a native block, and native ones flagged entity_surface, draw on entity carriers."""
    return not effect.get('native_block') or bool(effect.get('entity_surface'))


def _clear_staging(bp, rp, pack_key):
    """Delete the files of an earlier build of these packs, refusing folders this build did not make."""
    for pack, kind in ((bp, 'bp'), (rp, 'rp')):
        if not pack.exists():
            continue
        if read_json(pack / 'manifest.json')['header']['uuid'] != identity(kind + '-header', pack_key):
            raise ValueError('Unexpected staging pack identity')
        for path in pack.rglob('*'):
            if path.is_file():
                # A link inside the pack could point outside it; never delete through one.
                if not path.resolve().is_relative_to(pack.resolve()):
                    raise ValueError('Staging path escapes pack')
                path.unlink()


def _start_packs(bp, rp, root, pack_key, title, version):
    for pack, kind in ((bp, 'bp'), (rp, 'rp')):
        pack.mkdir(parents=True, exist_ok=True)
        write_json(pack / 'manifest.json', manifest(kind, pack_key, title, version))
        shutil.copyfile(root / 'converter/data/artwork/pack-icon.png', pack / 'pack_icon.png')
    for directory in (bp / 'entities', bp / 'scripts', rp / 'textures/entity'):
        directory.mkdir(parents=True, exist_ok=True)


def _hide_carrier_particles(rp, samples):
    """Keep passive carriers from showing lingering potion particles.

    The game ignores a cloud's ParticleId when it emits, so the potion emitter itself skips carrier
    clouds; normal lingering potions keep their particles.
    """
    particle = read_json(samples / 'resource_pack/particles/mobspell_lingering.json')
    rate = particle['particle_effect']['components']['minecraft:emitter_rate_instant']
    rate['num_particles'] = '(' + CARRIER_CLOUD + ') ? 0 : (' + rate['num_particles'] + ')'
    write_json(rp / 'particles/mobspell_lingering.json', particle)


def _emit_effects(providers, mask_catalogs, root, bp, rp, samples):
    """Write every provider effect; returns the carrier entity ids and the native surface block ids."""
    entities = set()
    native_blocks = set()
    for provider in providers:
        for effect in provider['effects'].values():
            if effect.get('native_block'):
                if effect['native_block'] not in native_blocks:
                    _emit_native_group(root, bp, rp, provider, effect['native_block'],
                                       mask_catalogs[provider['id']], samples)
                    native_blocks.add(effect['native_block'])
                if not effect.get('entity_surface'):
                    continue
            if effect['entity'] in entities:
                raise ValueError('Duplicate provider entity')
            entities.add(effect['entity'])
            if effect.get('spawn_structure'):
                _write_carrier_structure(bp, effect['entity'])
            if effect['kind'] not in ('model', 'surface') or not re.fullmatch(ENTITY_ID_PATTERN, effect['entity']):
                raise ValueError('Unsupported provider effect')
            if not effect.get('native_block'):
                _emit_carrier_effect(root, bp, rp, effect, provider.get('biome_palette'))
    return entities, native_blocks


def _emit_native_group(root, bp, rp, provider, native_block, mask_catalog, samples):
    """Write the native surface block shared by every effect naming it, plus its entity surfaces."""
    group = [effect for effect in provider['effects'].values() if effect.get('native_block') == native_block]
    targets = {target for rule in provider['rules'] if provider['effects'][rule['effect']] in group
               for target in rule['targets']}

    def emit_entity_carriers(effects, bones, materials):
        # Entity surfaces reuse the materials terrain_native has just written to the atlas.
        atlas = read_json(rp / 'textures/terrain_texture.json')
        return emit_entity_surfaces(bp, rp, effects, bones, materials, atlas, server_entity, mask_catalog)

    emit_native_surfaces(root, bp, rp, group, targets, entity_emitter=emit_entity_carriers, samples=samples)


def _write_carrier_structure(bp, identifier):
    # The structure id bct:<stem> is the file structures/bct/<stem>.mcstructure.
    structure = bp / 'structures/bct' / (identifier.replace(':', '_') + '.mcstructure')
    structure.parent.mkdir(parents=True, exist_ok=True)
    structure.write_bytes(cloud_structure(identifier))


def _prune_terrain_atlas(bp, rp, providers):
    """Keep only the atlas entries block materials use.

    Entity controllers bind texture files directly, so they need no atlas entries.
    """
    atlas_path = rp / 'textures/terrain_texture.json'
    if not atlas_path.exists():
        return
    used_aliases = set()
    for path in (bp / 'blocks').glob('*.json'):
        instances = read_json(path)['minecraft:block']['components']['minecraft:material_instances']
        used_aliases.update(material['texture'] for material in instances.values())
    atlas = read_json(atlas_path)
    atlas['texture_data'] = {alias: entry for alias, entry in atlas['texture_data'].items()
                             if alias in used_aliases}
    write_json(atlas_path, atlas)
    if any(provider.get('exclusive_quarters') for provider in providers):
        # Masked carriers draw from their own quarter crops, so block textures that no atlas entry
        # uses any more are left over. Unmasked carriers draw straight from the block textures.
        _delete_unused_block_textures(rp, atlas)


def _delete_unused_block_textures(rp, atlas):
    retained = set()
    for entry in atlas['texture_data'].values():
        for variation in entry['textures']['variations']:
            path = rp / variation['path']
            retained.update((path.with_suffix('.png'), path.with_suffix('.texture_set.json')))
            descriptor = read_json(path.with_suffix('.texture_set.json'))['minecraft:texture_set']
            retained.update(path.parent / (reference + '.png') for reference in descriptor.values()
                            if isinstance(reference, str))
    for path in (rp / 'textures/blocks').glob('*'):
        if path.is_file() and path not in retained:
            path.unlink()


def _native_diagnostics(providers, root, rp):
    """One report entry per native surface effect: how it renders, its heights and its geometry extent."""
    diagnostics = []
    for provider in providers:
        for name, effect in provider['effects'].items():
            if effect.get('native_block'):
                diagnostics.append(_native_diagnostic(provider, name, effect, root, rp))
    return diagnostics


def _native_diagnostic(provider, name, effect, root, rp):
    entity_surface = effect.get('entity_surface', False)
    identifier = effect['entity'] if entity_surface else effect['native_block']
    model_folder = 'entity' if entity_surface else 'blocks'
    geometry_path = rp / 'models' / model_folder / f"{identifier.replace(':', '_')}.geo.json"
    models = read_json(geometry_path)['minecraft:geometry']
    cubes = [cube for model in models for bone in model['bones'] for cube in bone.get('cubes', [])]
    minimum = [min(cube['origin'][axis] for cube in cubes) for axis in range(3)]
    maximum = [max(cube['origin'][axis] + cube['size'][axis] for cube in cubes) for axis in range(3)]
    if not entity_surface and _outside_native_bounds(minimum, maximum):
        raise ValueError('Native geometry exceeds observed Creator bounds: ' + effect['native_block'])
    default_height = native_surface_height(effect['material'], effect.get('layer', 2), effect.get('priority', 1))
    return {
        'provider': provider['id'],
        'effect': name,
        'block': effect['native_block'],
        'renderer': 'entity' if entity_surface else 'native_block',
        'entity': effect['entity'] if entity_surface else None,
        'offset': 1 if entity_surface else effect.get('native_offset', 1),
        'material': effect['material'],
        'priority': effect.get('priority', 1),
        'height_pixels_256': effect.get('height_pixels', default_height * TEXTURE_RESOLUTION),
        'height_mode': 'session_tuned' if effect.get('height_tuning') and not effect.get('mask_lookup') else 'fixed',
        'height_profiles': effect.get('height_profiles'),
        'height_rendering': 'entity_position' if effect.get('height_tuning') else 'baked_geometry',
        'geometry_variants': len(models),
        'step_pixels_256': PIXELS_PER_TIER,
        'geometry_min': minimum,
        'geometry_max': maximum,
        'bones': len(models[0]['bones']),
        'disabled': effect.get('native_disabled', False),
        'ambient_occlusion': None if entity_surface else source_ambient_occlusion(root, effect),
        'reason': ('Offset2 planes exceed Creator lower Y bound; quarantined for cleanup'
                   if effect.get('native_disabled') else None),
    }


def _outside_native_bounds(minimum, maximum):
    return any(minimum[axis] < NATIVE_GEOMETRY_MIN[axis] or maximum[axis] > NATIVE_GEOMETRY_MAX[axis]
               for axis in range(3))


def _write_archive(staging, bp, rp, archive):
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as packed:
        for pack in (bp, rp):
            for path in sorted(pack.rglob('*')):
                if path.is_file():
                    packed.write(path, path.relative_to(staging).as_posix())
    return archive


def _build_report(archive, version, providers, entities, native_blocks, native_diagnostics, bp, rp, overlay_data):
    images = sum(1 for path in rp.rglob('*') if path.suffix in ('.png', '.tga'))
    return {
        'archive': str(archive),
        'archive_bytes': archive.stat().st_size,
        'version': version,
        'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        'providers': [provider['id'] for provider in providers],
        'entity_types': sorted(entities),
        'images': images,
        'masks': MASKS,
        'block_conversion': False,
        'renderer': 'priority-exclusive quarter masks; native biome grass and sand entities at 1 texture pixel',
        'native_surface_blocks': sorted(native_blocks),
        'native_permutations': _block_permutations(bp),
        'native_texture_tiles': _atlas_tiles(rp),
        'overlay_surface_blocks': len(overlay_data['types']) if overlay_data is not None else 0,
        'temporary_air_cells': any(not effect.get('entity_surface') and effect.get('native_block')
                                   for provider in providers for effect in provider['effects'].values()),
        'baked_grass_palette': False,
        'corner_probe': False,
        'native_diagnostics': native_diagnostics,
        'in_game_tested': False,
        'measured_game_memory': False,
    }


def _block_permutations(bp):
    """Permutations of every block: per block, the product of its states' value counts."""
    total = 0
    for path in (bp / 'blocks').glob('*.json'):
        states = read_json(path)['minecraft:block']['description']['states']
        total += math.prod(len(values) for values in states.values())
    return total


def _atlas_tiles(rp):
    """Images the terrain atlas loads: each variation of a variation entry, else one per entry."""
    atlas = read_json(rp / 'textures/terrain_texture.json')
    return sum(len(entry['textures']['variations']) if isinstance(entry['textures'], dict) else 1
               for entry in atlas['texture_data'].values())


def _merged_pack_file(relative, existing, added):
    if isinstance(existing, list):
        return existing + [entry for entry in added if entry not in existing]
    if relative == 'textures/terrain_texture.json':
        return {**existing, 'texture_data': {**existing.get('texture_data', {}), **added['texture_data']}}
    return {**existing, **added}


# Entity carrier effects (effects without a native block).

def _emit_carrier_effect(root, bp, rp, effect, palette=None):
    """Write one effect's entity carrier: tiles, server and client entity, geometry and render controller."""
    identifier = effect['entity']
    stem = identifier.replace(':', '_')
    model = effect['kind'] == 'model'
    if model and effect.get('geometry') != 'oak_root_fan' and not effect.get('geometry_file'):
        raise ValueError('Provider model requires geometry_file or oak_root_fan')
    source, descriptor, color = _carrier_material(root, effect)
    if effect.get('biome_tint') == 'grass' and palette:
        _emit_palette_effect(root, bp, rp, effect, descriptor, source, color, palette)
        return
    _copy_material_channels(source, descriptor, rp, stem)
    overlay = effect.get('generator') == 'neighbor_overlay'
    tile_count = 1 if model else 256 if overlay else len(MASKS)
    sheet = _ContactSheet()
    textures = {}
    written = {}
    for index, tile in zip(range(tile_count), _carrier_tiles(root, effect, color, model, overlay, tile_count)):
        digest = hashlib.sha256(tile.tobytes()).hexdigest()
        if digest in written:
            textures[f't{index}'] = written[digest]
            continue
        path = f'textures/entity/{stem}_{index:02d}'
        written[digest] = path
        tile.save(rp / (path + '.png'))
        write_json(rp / (path + '.texture_set.json'), _tile_texture_set(path, stem, descriptor))
        textures[f't{index}'] = path
        if not model and index < PREVIEW_TILES:
            sheet.add(tile, index, index if overlay else MASKS[index])
    write_json(bp / f'entities/{stem}.json', server_entity(identifier, not model))
    geometry_id = 'geometry.' + stem
    write_json(rp / f'models/entity/{stem}.geo.json', _carrier_geometry(root, effect, geometry_id, model))
    controller_id = 'controller.render.' + stem
    material = 'entity_alphatest_change_color' if effect.get('biome_tint') == 'grass' else 'entity_alphatest'
    write_json(rp / f'entity/{stem}.entity.json',
               _client_entity(identifier, material, textures, geometry_id, [controller_id]))
    controller = _carrier_controller(effect, model, tile_count)
    if not model:
        sheet.save(root / f'dist/development/{stem}-TILES.png')
    write_json(rp / f'render_controllers/{stem}.render_controllers.json',
               {'format_version': '1.8.0', 'render_controllers': {controller_id: controller}})


def _carrier_material(root, effect):
    """The effect's texture set file, its channels and its colour image, which must be 256 square."""
    if effect.get('texture_set'):
        source = root / effect['texture_set']
    else:
        source = terrain_pack(root) / f'textures/blocks/{effect["material"]}.texture_set.json'
    descriptor = read_json(source)['minecraft:texture_set']
    with Image.open(source.parent / (descriptor['color'] + '.png')) as image:
        color = image.convert('RGBA')
    if color.size != (TILE_SIZE, TILE_SIZE):
        raise ValueError('Provider material must use native 256 delivery')
    return source, descriptor, color


def _copy_material_channels(source, descriptor, rp, stem):
    """Copy the material's other channels (normal, MER and height maps) once; every tile shares them."""
    for channel, reference in descriptor.items():
        if channel != 'color' and isinstance(reference, str):
            shutil.copyfile(source.parent / (reference + '.png'), rp / f'textures/entity/{stem}_{channel}.png')


def _tile_texture_set(path, stem, descriptor):
    """Texture set of one carrier tile: its own colour image plus the material's shared channels."""
    values = {'color': path.rsplit('/', 1)[-1]}
    values.update({channel: f'{stem}_{channel}' if isinstance(reference, str) else reference
                   for channel, reference in descriptor.items() if channel != 'color'})
    return {'format_version': '1.21.30', 'minecraft:texture_set': values}


def _carrier_tiles(root, effect, color, model, overlay, tile_count):
    """The carrier's tile images in mask order (a model carrier has one)."""
    if overlay:
        yield from _overlay_tiles(root, effect, color)
        return
    for index in range(tile_count):
        if effect.get('tiles'):
            yield _provided_tile(root, effect, index, tile_count)
        elif not model and effect.get('generator') != 'moss_ground':
            raise ValueError('Surface provider requires 47 tiles or an explicit supported generator')
        else:
            yield color if model else _moss_tile(color, MASKS[index])


def _provided_tile(root, effect, index, tile_count):
    if len(effect['tiles']) != tile_count:
        raise ValueError('Provider tile count must match its effect')
    with Image.open(root / effect['tiles'][index]) as image:
        tile = image.convert('RGBA')
        if tile.size != (TILE_SIZE, TILE_SIZE):
            raise ValueError('Provider tile must be 256 square')
    return tile


def _overlay_tiles(root, effect, color):
    """All 256 neighbour masks cut from the effect's edge outline over the material colour.

    Bits 0 to 3 add the outline on that side (turned a quarter per side); bits 4 to 7 add the corner
    between that side and the next (where both turned outlines overlap).
    """
    edge = edge_alpha(root, effect)
    edges = [np.asarray(edge.rotate(-90 * side), dtype=np.uint8) for side in range(4)]
    corners = [np.minimum(edges[side], edges[(side + 1) % 4]) for side in range(4)]
    pixels = np.asarray(color).copy()
    if 'tint' in effect:
        pixels[:, :, :3] = np.uint8(pixels[:, :, :3] * np.array(effect['tint']))
    for mask in range(256):
        alpha = np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.uint8)
        for bit, patch in enumerate(edges + corners):
            if mask & (1 << bit):
                alpha = np.maximum(alpha, patch)
        tile = Image.fromarray(pixels)
        tile.putalpha(Image.fromarray(alpha))
        yield tile


def _moss_tile(color, mask):
    """Moss rising from the bottom of a side face, for providers without tiles of their own.

    The moss reaches a wavy line about a quarter of the way up and fades out toward every side whose
    neighbour is not moss; where both sides of a corner connect but its diagonal does not, a rounded
    notch stays open in that corner.
    """
    rows, columns = np.mgrid[:TILE_SIZE, :TILE_SIZE]
    last = TILE_SIZE - 1
    phase = columns / last * 2 * np.pi
    limit = 188 + 24 * np.sin(phase * 3) + 13 * np.cos(phase * 5)
    alpha = np.clip((rows - limit) / 9, 0, 1) * 255
    for side, distance in enumerate((rows, last - columns, last - rows, columns)):
        if not mask & (1 << side):
            alpha *= np.clip(distance / 7, 0, 1)
    for corner, (corner_x, corner_y) in enumerate(((last, 0), (last, last), (0, last), (0, 0))):
        sides = (1 << corner) | (1 << ((corner + 1) % 4))
        if mask & sides == sides and not mask & (1 << (corner + 4)):
            alpha *= np.clip(np.hypot(columns - corner_x, rows - corner_y) / 12, 0, 1)
    result = color.convert('RGBA')
    result.putalpha(Image.fromarray(np.uint8(alpha)))
    return result


class _ContactSheet:
    """Preview of a carrier's first tiles, eight to a row, saved beside the add-on for checking by eye."""

    def __init__(self):
        self.image = Image.new('RGB', (8 * 128, 6 * 148), '#181c19')
        self.draw = ImageDraw.Draw(self.image)

    def add(self, tile, index, mask):
        thumbnail = tile.resize((120, 120), Image.Resampling.LANCZOS)
        x = index % 8 * 128 + 4
        y = index // 8 * 148
        self.image.paste(thumbnail, (x, y), thumbnail)
        self.draw.text((x, y + 122), f'{index:02d} mask={mask:03d}', fill='#e5dac0')

    def save(self, path):
        self.image.save(path)


def _carrier_geometry(root, effect, geometry_id, model):
    if not effect.get('geometry_file'):
        return geometry(geometry_id, model, effect.get('layer', 0))
    model_data = read_json(root / effect['geometry_file'])
    if len(model_data['minecraft:geometry']) != 1:
        raise ValueError('Provider geometry file must contain one model')
    model_data['minecraft:geometry'][0]['description']['identifier'] = geometry_id
    return model_data


def _client_entity(identifier, material, textures, geometry_id, controllers):
    return {'format_version': '1.21.80', 'minecraft:client_entity': {'description': {
        'identifier': identifier,
        'materials': {'default': material},
        'textures': textures,
        'geometry': {'default': geometry_id},
        'render_controllers': carrier_controllers(controllers),
    }}}


def _carrier_controller(effect, model, tile_count):
    """Render controller of a carrier: the engine picks the tile (bct:tile) and the face it draws (bct:face)."""
    controller = {
        'geometry': 'Geometry.default',
        'materials': [{'*': 'Material.default'}],
        'textures': ['Texture.t0'] if model else ["Array.tiles[q.property('bct:tile')]"],
    }
    if effect.get('biome_tint') == 'grass':
        controller['color'] = {channel: f"q.property('bct:tint_{channel}')" for channel in ('r', 'g', 'b')}
        controller['color']['a'] = 1
    if model:
        controller['part_visibility'] = [{'*': "q.property('bct:active')"}]
    else:
        controller['arrays'] = {'textures': {'Array.tiles': [f'Texture.t{index}' for index in range(tile_count)]}}
        controller['part_visibility'] = [{face: f"q.property('bct:active') && q.property('bct:face') == {index}"}
                                         for index, face in enumerate(FACES)]
    return controller


def _emit_palette_effect(root, bp, rp, effect, descriptor, source, color, palette):
    """A grass carrier with the client biome palette baked in.

    Each palette colour gets two cutouts, an edge and a corner; bones turn them to the four sides,
    the engine's tile bits pick which bones show and bct:palette picks the colour.
    """
    stem = effect['entity'].replace(':', '_')
    edge = edge_alpha(root, effect)
    corner = Image.fromarray(np.minimum(np.asarray(edge), np.asarray(edge.rotate(-90))))
    _copy_material_channels(source, descriptor, rp, stem)
    textures = _write_palette_tiles(rp, stem, descriptor, color, palette, edge, corner)
    geometry_id = 'geometry.' + stem
    model = _palette_geometry(geometry_id, effect.get('layer', 0))
    controllers = {'controller.render.' + stem + '_' + shape: _palette_controller(shape, len(palette))
                   for shape in ('edge', 'corner')}
    write_json(bp / f'entities/{stem}.json', server_entity(effect['entity']))
    write_json(rp / f'models/entity/{stem}.geo.json', model)
    write_json(rp / f'entity/{stem}.entity.json',
               _client_entity(effect['entity'], 'entity_alphatest', textures, geometry_id, controllers))
    write_json(rp / f'render_controllers/{stem}.render_controllers.json',
               {'format_version': '1.8.0', 'render_controllers': controllers})


def _write_palette_tiles(rp, stem, descriptor, color, palette, edge, corner):
    textures = {}
    for index, tint in enumerate(palette):
        pixels = np.asarray(color).copy()
        pixels[:, :, :3] = tint_linear(pixels[:, :, :3], tint)
        for shape, alpha in (('edge', edge), ('corner', corner)):
            path = f'textures/entity/{stem}_{shape}_{index}'
            tile = Image.fromarray(pixels)
            tile.putalpha(alpha)
            tile.save(rp / (path + '.png'))
            write_json(rp / (path + '.texture_set.json'), _tile_texture_set(path, stem, descriptor))
            textures[f'{shape}{index}'] = path
    return textures


def _palette_geometry(geometry_id, layer):
    """Eight copies of the top plane: the edge and the corner, each turned to the four sides."""
    model = geometry(geometry_id, layer=layer)
    top = next(bone for bone in model['minecraft:geometry'][0]['bones'] if bone['name'] == 'up')
    bones = []
    for shape in ('edge', 'corner'):
        for index in range(4):
            bone = deepcopy(top)
            bone['name'] = shape + str(index)
            bone['rotation'][1] = 180 + 90 * index
            # Each quad sits a hair above the one before, so overlapping cutouts never z-fight.
            bone['cubes'][0]['origin'][1] += _palette_bit(shape, index) * .0001
            bones.append(bone)
    model['minecraft:geometry'][0]['bones'] = bones
    return model


def _palette_controller(shape, palette_size):
    visibility = [{'*': False}]
    for index in range(4):
        tile_bit = f"math.mod(math.floor(q.property('bct:tile') / {2 ** _palette_bit(shape, index)}), 2) == 1"
        shown = f"q.property('bct:active') && q.property('bct:face') == {TOP_FACE} && " + tile_bit
        visibility.append({shape + str(index): shown})
    return {
        'geometry': 'Geometry.default',
        'materials': [{'*': 'Material.default'}],
        'textures': ["Array.colors[q.property('bct:palette')]"],
        'arrays': {'textures': {'Array.colors': [f'Texture.{shape}{index}' for index in range(palette_size)]}},
        'part_visibility': visibility,
    }


def _palette_bit(shape, side):
    """Tile bit of a palette bone: the edges are bits 0 to 3, the corners bits 4 to 7."""
    return side + (4 if shape == 'corner' else 0)


# Geometry pieces.

def _face_planes(layer):
    """(origin, size) of each face plane in block pixels.

    A small outward bias prevents coplanar fighting with the block face; the top plane rises with the
    surface layer.
    """
    return {
        'north': ([-8, 0, -8.025], [16, 16, 0]),
        'east': ([8.025, 0, -8], [0, 16, 16]),
        'south': ([-8, 0, 8.025], [16, 16, 0]),
        'west': ([-8.025, 0, -8], [0, 16, 16]),
        'up': ([-8, 16 + surface_height(layer) * 16 - .001, -8], [16, .001, 16]),
        'down': ([-8, -.025, -8], [16, 0, 16]),
    }


def _face_bone(face, origin, size):
    top_or_bottom = face in ('up', 'down')
    return {
        'name': face,
        'pivot': [0, 0, 0],
        # Carriers spawn on the host's top; the half turn compensates for the entity's yaw.
        'rotation': [0, 180, 0],
        'cubes': [{
            # The planes are laid out on the host block, one block (16 pixels) below the carrier.
            'origin': [origin[0], origin[1] - 16, origin[2]],
            'size': size,
            # Top and bottom faces run their UVs backwards so the tile keeps the block's orientation.
            'uv': {face: {'uv': [16, 16] if top_or_bottom else [0, 0],
                          'uv_size': [-16, -16] if top_or_bottom else [16, 16]}},
        }],
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', type=Path, action='append', required=True)
    parser.add_argument('--key', required=True, help='lowercase pack key; seeds the pack UUIDs')
    parser.add_argument('--title', default='Terrain transitions')
    parser.add_argument('--samples', type=Path, help='Mojang bedrock-samples checkout (default: BEDROCK_SAMPLES)')
    args = parser.parse_args()
    print(json.dumps(build(args.provider, args.key, args.title, samples=args.samples)[1], indent=2))
