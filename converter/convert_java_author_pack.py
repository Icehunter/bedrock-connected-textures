"""Convert a stack of Java resource packs into one add-on for the Bedrock Connected Textures engine.

The conversion is read-only and resumable. The source archives are never written; everything goes
into one output folder, which records the sources and settings it belongs to, and every converted
material keeps a receipt, so an interrupted run carries on where it stopped.

convert() runs these steps in order:

1. Read the pack: claim the output folder, read the pack's identity and icon, and find the Java
   client jar and Mojang's bedrock-samples.
2. Resolve bindings: the Java texture every Bedrock block face, carried slot and item shows, and
   the Bedrock names of the blocks, states and biomes the pack's rules test.
3. Convert materials: decode every block and CTM sprite with its LabPBR maps (prepare).
4. Import rules: turn the CTM properties into engine rules for the blocks the game has.
5. Plan the native textures: a rule that does not depend on the world shows its tile on the plain
   block, random rules become atlas variations, and the materials, items, entities and tints of
   the base packs are converted.
6. Export the base packs: one resource pack per graphics mode, Classic, Vibrant Visuals and RTX
   (export_base).
7. Build replacement blocks: full-cube rules become native custom blocks, and leaves with the
   author's own models become model blocks.
8. Build overlay surfaces: the pack's overlay rules as native blocks in front of the faces they
   cover.
9. Build the connected add-on: entity carriers draw the rules nothing native draws, within the
   carrier budget. Ray tracing hides the carriers, so the RTX pack restores the blocks they draw.
10. Publish one .mcaddon that depends on the engine.
11. Report where each rule draws in each graphics mode, the texture load and the schema checks.

The author's look wins throughout: textures, models, tints and animations are reproduced as the
author made them and nothing is invented (a missing map stays missing). What Bedrock cannot show
is listed in the reports with the reason.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import urllib.request
import uuid
import zipfile

import numpy as np
from PIL import Image

# Imported as modules: terrain_pack.prepare and terrain_providers.build would clash with prepare()
# here and with overlay_surfaces.build, and tests replace these functions on their modules.
import addon_package
import terrain_pack
import terrain_providers
from baked_tints import bake, parse_fixed_colormaps, plan_tints
from bedrock_grade import grade_addon
from bedrock_schema import addon_check
from block_ids import java_id, known_blocks, sanitize
from carried_slots import carried_tints, fill_carried_slots, fill_item_tints, item_tints
from carrier_budget import apply_budget, load_budget, rule_targets
from common import BLOCKS_FORMAT, read_json, samples_path, texture_file, write_json
from connected_build import build_connected
from engine_package import engine_dependency
from held_models import plan_held_items, write_held_items
from import_java_ctm import import_rules
from java_bedrock_reference import texture_paths
from java_block_bindings import _Models, resolve_bindings
from java_block_states import native_block_java_ids, resolve_known_block_selection
from java_entities import export_entities, prepare_entities
from java_environment_bindings import resolve_environment
from java_grass_native import adapt_grass_side, encode_grass_side, grass_overlay_source
from java_materials import MATERIAL_DECODER_REVISION, MaterialPolicy, compile_stack_material
from java_missing_sprite import MISSING_MODEL_SPRITE, missing_model_png
from java_model_tints import apply_native_biome_tints, copy_model_tint_images, resolve_model_tints
from java_native_assets import export_native_assets, prepare_native_assets
from java_native_fallbacks import require_node_runtime, select_native_fallbacks
from java_pack_api import PackStack
from java_rtx_compatibility import native_rtx_compatibility
from model_blocks import plan_all
from mosaic_random import kept_vanilla_patterns, only_vanilla_blocks, random_looks_authored
from native_replacement import WORLD_PERMUTATIONS, build_replacements, fallback_patterns, load_policy, shaped_over_budget
from overlay_surfaces import build as build_overlays, prune_generated_edges
from pack_identity import identity as pack_identity, pack_title, reference_version
from sand_edges import write_sand_edge_layer
from texture_dedupe import dedupe_addon
from texture_load import WARN_MIB, load_report, measure
from pack_scan import ATLAS_SIDE, addon_atlas_sizes, atlas_fit_width, estimate_atlas
from texture_size import cap_addon_block_textures, cap_block_textures, most_common_width
from top_decals import bake_top_decals, find_top_decals
from vv_scene_profile import WATER_TEXTURES, apply_scene_profile, read_scene_profile, read_water_textures


# The graphics modes a base pack is exported for: Classic, Vibrant Visuals and ray tracing.
RENDERERS = ('classic', 'vv', 'rtx')
FACES = ('north', 'east', 'south', 'west', 'up', 'down')

# The project folder; bedrock-samples may sit inside it.
ROOT = Path(__file__).resolve().parents[1]
# Java releases the converter can take vanilla files from, with the resource format each one reads.
JAVA_RELEASES = Path(__file__).resolve().parent / 'data/java-versions.json'
# The Java release downloaded when none is chosen.
DEFAULT_JAVA_RELEASE = '26.2'
MOJANG_VERSION_MANIFEST = 'https://piston-meta.mojang.com/mc/game/version_manifest_v2.json'

# LabPBR 1.3 maps decoded with DirectX normals and perceptual roughness. A channel Bedrock cannot
# show does not fail the material; its receipt lists it as a renderer loss.
MATERIAL_POLICY = MaterialPolicy('labpbr-1.3', 'directx', 'perceptual', allow_losses=True)
TEXTURE_SET_FORMAT = '1.21.30'

GRASS_SIDE = 'textures/blocks/grass_side'
GRASS_TOP_TEXTURE = 'assets/minecraft/textures/block/grass_block_top.png'
# Java textures drawn under a second, tinted layer (the grass side and its overlay).
LAYERED_TEXTURES = frozenset({'assets/minecraft/textures/block/grass_block_side.png'})
# The see-through pixels of an opaque leaf slot take the leaf's mean colour at this brightness.
OPAQUE_LEAF_GAP_SHADE = 0.35
# The texture the carrier pack gives the block faces its carriers draw over (connected_build).
CARRIER_BLANK_TEXTURE = 'bct_owned_transparent'

# Bindings the engine reads with the rules, so its matching and its source scan see every block
# state the way the converter does.
RUNTIME_BINDING_FIELDS = (
    'fullCubeBlocks', 'opaqueBlocks', 'textureOrientations', 'baseTextureVariants', 'unresolvedTextureOrientations',
    'modelTintTypes', 'grassTints', 'foliageTints', 'customTints', 'customTintBlocks', 'modelGeometryBlocks',
    'leafDistanceLeaves', 'leafDistanceLogs')

# The start of terrain_pack's error for an animated or non-square grass or sand texture.
ANIMATED_TERRAIN_ERROR = 'Animated or non-square textures need a square still image:'

# The parts of the overlay surface report repeated in conversion.json.
OVERLAY_SUMMARY_FIELDS = (
    'surface_blocks', 'permutations', 'textures', 'types', 'rules_drawn', 'rules_not_drawn', 'notes', 'limits')

# What every converted pack cannot do, repeated in its conversion report.
CONVERSION_LIMITATIONS = (
    'Connected textures on entity carriers draw in Classic and Vibrant Visuals only.',
    'Converted blocks stay converted: a world keeps the pack\'s blocks, so removing the pack leaves unknown blocks.',
    'Java block model parts are converted; custom item and entity models need a separate geometry adapter.',
    'Material-channel losses and native animation interpolation differences are listed in the material receipts.',
)


def convert(archives, destination, *, vanilla=None, samples=None, key='author-pack', title=None, workers=4,
            vv_scene=None, carrier_filter='bilinear', carrier_budget=None, version=None, authors=None, bedrock_grade=False,
            max_permutations=WORLD_PERMUTATIONS, scale_to_atlas=False):
    """Convert a Java pack stack into one add-on for the Bedrock Connected Textures engine.

    archives: the Java pack ZIPs, lowest priority first. vanilla: the Java client jar; without one,
    the release the pack was made for is downloaded and checked against Mojang's hash. samples:
    Mojang's bedrock-samples folder. vv_scene: a Bedrock pack whose Vibrant Visuals scene settings
    and water textures are used, and none of its other artwork. key seeds the pack's UUIDs and
    namespaces, so it stays the same between updates of one pack. scale_to_atlas scales the block
    textures down, never up, until the game's terrain atlas holds them. Returns the conversion report,
    which is also written to conversion.json.
    """
    # 1. Read the pack. What can fail early fails before any work: Node runs the engine's tile
    # selector, and the scene pack is read before anything is written.
    require_node_runtime()
    if bedrock_grade and not vv_scene:
        raise ValueError("--bedrock-grade needs the author's Bedrock pack (--vv-scene) to learn the grade from")
    # Without a title the pack keeps the author's name, version and resolution from their file name.
    title = title or pack_title(Path(archives[-1]).name)
    vv_profile = read_scene_profile(vv_scene) if vv_scene else None
    water = read_water_textures(vv_scene) if vv_scene else None
    destination = claim_destination(archives, destination, key, title, vanilla, vv_profile)
    identity, icon = read_pack_identity(archives, version, authors)
    # Red, suspicious and soul sand edges cut like the pack's own sand edge, as a layer under the pack.
    sand_edges = write_sand_edge_layer(archives, destination / 'sand-edges.zip')
    if sand_edges:
        archives = [sand_edges, *archives]
    java_release = reference_version(identity['formats'], read_json(JAVA_RELEASES)['releases'])
    vanilla = Path(vanilla) if vanilla else vanilla_reference(destination / 'reference', version=java_release)
    samples = find_samples(samples)
    resource_samples = samples / 'resource_pack'
    if not resource_samples.exists():
        raise ValueError('Mojang Bedrock reference is unavailable; set --samples to bedrock-samples')

    # 2. What every block face, carried slot and item shows.
    bindings, environment, held_items = resolve_pack_bindings(archives, vanilla, samples)
    write_json(destination / 'bindings.json', bindings)
    write_json(destination / 'environment.json', environment)

    # 3. Decode the sprites the blocks and rules use, with their LabPBR maps.
    material_report = prepare(archives, vanilla, destination, workers=workers)
    if not material_report['material_conversion_complete']:
        raise ValueError('Source material conversion failed; see ' + str(destination / 'material-report.json'))

    # 4. The compiled pack holds the art native blocks and carriers draw: the rule tiles (compact
    # rules expanded), later joined by the original faces and tint images. It is a new folder each
    # run, so no tile from an earlier run is reused.
    compiled = destination / ('compiled-' + uuid.uuid4().hex[:12])
    authored_rules = import_pack_rules(destination, compiled, bindings, environment, samples)

    # 5. Plan the native textures. Atlas variations depend only on the random rules; which rules
    # go on carriers is decided once the replacement blocks exist (step 9).
    select_native_fallbacks(bindings, authored_rules, destination)
    replacement_policy = load_policy()
    budget = load_budget(carrier_budget)
    budget['carrier_fallback'] = fallback_patterns(replacement_policy)
    random_mosaic = random_mosaic_check(destination / 'java-input', kept_vanilla_patterns(replacement_policy))
    _, bindings['nativeVariations'], variation_report = apply_budget(authored_rules, budget,
                                                                     random_mosaic=random_mosaic)
    write_json(destination / 'bindings.json', bindings)
    prepare_native_materials(archives, vanilla, destination, bindings, workers=workers)
    if read_json(destination / 'native-material-report.json')['failures']:
        raise ValueError('Native material conversion failed; see ' + str(destination / 'native-material-report.json'))
    bake_grass_decals(archives, destination / 'native-materials', bindings)
    write_json(destination / 'bindings.json', bindings)
    prepare_nonblock_resources(archives, vanilla, resource_samples, destination)
    tint_sprites = plan_baked_tints(archives, vanilla, resource_samples, bindings)

    # 6. One base resource pack per graphics mode.
    base = export_base(destination, bindings, resource_samples, key, title, vv_profile=vv_profile,
                       tint_sprites=tint_sprites, held_items=held_items, water=water)
    if any(item['failed_bindings'] for item in base):
        raise ValueError('Native texture binding failed; see ' + str(destination / 'base-exports.json'))

    # 7. Native replacement blocks are the primary renderer: they draw in Classic, Vibrant Visuals
    # and ray tracing. Carriers keep what they cannot express.
    include_base_materials(archives, vanilla, destination, compiled, bindings)
    with PackStack(archives) as stack:
        copy_model_tint_images(stack, vanilla, compiled, bindings)
    # 8. The pack's overlay rules draw as native surface blocks in front of the faces they cover,
    # in every graphics mode; carriers leave them out so nothing draws twice. When the custom blocks
    # together pass the game's permutation limit, the costliest shaped blocks are left out and both
    # are built again (the overlay surfaces name the replacement blocks they rest on).
    skip = set()
    for _ in range(2):
        replacement_data, replacement_report = build_replacement_blocks(
            archives, vanilla, samples, destination, compiled, key, authored_rules, replacement_policy,
            native_variation_rules(variation_report), skip=skip)
        overlay_data, overlay_report = build_overlays(authored_rules, compiled, destination / 'overlay', key=key,
                                                      samples=samples, replacement_data=replacement_data,
                                                      replacement_report=replacement_report)
        dropped = shaped_over_budget(replacement_report, overlay_report['permutations'], max_permutations)
        if not dropped:
            break
        skip |= dropped
    replaced_blocks = {entry['vanilla'] for entry in (replacement_data or {}).get('blocks', [])}
    overlay_hosts = {item['rule']: item['host_blocks'] for item in overlay_report['rules_drawn']}

    # 9. Entity carriers draw what is left, within the carrier budget.
    carried_rules, budget_report = choose_carrier_rules(destination, authored_rules, budget, replaced_blocks,
                                                        overlay_hosts)
    accounting = rule_accounting(authored_rules, carried_rules, budget_report, replacement_report, overlay_hosts)
    connected, coverage = build_connected_addon(destination, compiled, carried_rules, key, samples, vv_profile,
                                                carrier_filter)
    restored_blocks = restore_carrier_blocks_for_rtx(base, connected, resource_samples)

    # 10. One add-on that depends on the published engine.
    published = publish_conversion(destination, base, connected, key, title, samples, root=ROOT, icon=icon,
                                   replacement=destination / 'replacement' if replacement_data else None,
                                   identity=identity, overlay=destination / 'overlay' if overlay_data else None)

    # 11. The report: what was built, where each rule draws, and what the game is asked to load.
    rtx = rtx_summary(base, restored_blocks)
    report = {'base_exports': base, 'connected_intermediate': str(connected),
              'rule_count': len(carried_rules['rules']), **published,
              'carrier_budget': budget_report, 'rule_accounting': accounting,
              # replacement-report.json keeps the long list of unsupported blocks.
              'native_replacement': {name: value for name, value in replacement_report.items()
                                     if name != 'unsupported'},
              'materials': material_report, 'gallery_stations': bindings['galleryStations'],
              'renderer_coverage': coverage, 'full_support_verified': False, 'in_game_verified': False,
              'rtx': rtx, 'limitations': list(CONVERSION_LIMITATIONS)}
    report['texture_load'] = texture_load(base, destination, replacement_report, overlay_data, overlay_report)
    report['overlay_surfaces'] = overlay_summary(overlay_report, published['terrain_provider'])
    for warning in report['texture_load']['warnings']:
        print('Warning: ' + warning)
    # Nothing in the pack is drawn finer than the author's own textures (generated edges are made at 256).
    pack_width = most_common_width(Path(next(item for item in base if item['renderer'] == 'rtx')['resource_pack']) / 'textures/blocks')
    if pack_width:
        report['scaled_to_pack_resolution'] = cap_addon_block_textures(published['installable_packs'][0]['archive'], pack_width)
    report['terrain_atlas'] = fit_terrain_atlas(published['installable_packs'][0]['archive'], samples, scale_to_atlas)
    # Identical images are kept once (texture_dedupe.py): the game loads every copy into texture memory.
    report['deduplicated_textures'] = dedupe_addon(Path(published['installable_packs'][0]['archive']))
    if bedrock_grade:
        # The author's Bedrock colour grade over every block texture (bedrock_grade.py), before the final checks.
        report['bedrock_grade'] = grade_addon(Path(published['installable_packs'][0]['archive']), vv_scene)
    report['schema_check'] = check_schemas(published['installable_packs'][0]['archive'], samples, destination)
    write_json(destination / 'conversion.json', report)
    return report


def fit_terrain_atlas(archive, samples, scale):
    """The terrain atlas the add-on asks for (pack_scan.py); with scale, its block textures scaled down to fit.

    Past the largest atlas the game scales every block texture down itself, blurred, with a low
    resources warning. Scaling them here first keeps them sharp and loads faster. scale is False,
    True (the widest width that fits) or a width in pixels.
    """
    sizes = addon_atlas_sizes(archive, samples)
    estimate = estimate_atlas(sizes)
    result = {key: estimate[key] for key in ('side', 'share', 'slots', 'width')}
    if scale:
        width = atlas_fit_width(sizes) if scale is True else min(scale, estimate['width'])
        if width < estimate['width']:
            # The atlas also holds a few textures outside textures/blocks, such as the cracks of a breaking block.
            others = [texture for texture, (size, own) in sizes.items()
                      if own and size[0] > width and not texture.startswith('textures/blocks/')]
            result['scaled_to'] = width
            result['scaled_textures'] = cap_addon_block_textures(archive, width, also=others)
            fitted = estimate_atlas(addon_atlas_sizes(archive, samples))
            result.update(fitted_side=fitted['side'], fitted_share=fitted['share'])
            fits = 'the terrain atlas fits' if fitted['share'] <= 1 else \
                f"the terrain atlas still needs {fitted['side']} pixels square, over the {ATLAS_SIDE} the game builds"
            print(f"Scaled {result['scaled_textures']} block textures from {estimate['width']} to {width} pixels; "
                  f'{fits}.')
    elif estimate['share'] > 1:
        print(f"Warning: the terrain atlas needs {estimate['side']} pixels square, over the {ATLAS_SIDE} the game "
              'builds; the game will scale the block textures down and blur them. '
              'Convert with --scale-to-atlas to scale them sharply here.')
    return result


# --- Reading the pack ---

def claim_destination(archives, destination, key, title, vanilla=None, vv_profile=None):
    """Create the output folder, or reopen it for the same conversion.

    The folder records the sources and settings it was made from in .author-conversion.json: the
    hashes of the archives and the Java jar, the key, the title and the scene profile. A folder
    made from anything else is refused, so a resumed run never mixes two conversions.
    """
    destination = Path(destination).resolve()
    marker = destination / '.author-conversion.json'
    # A downloaded jar is checked against Mojang's own hash instead (vanilla_reference).
    if vanilla:
        java_reference = {'path': str(Path(vanilla).resolve()), 'sha256': file_sha256(vanilla)}
    else:
        java_reference = {'mojang_version': DEFAULT_JAVA_RELEASE}
    declared = {'archives': [{'path': str(Path(path).resolve()), 'sha256': file_sha256(path)} for path in archives],
                'key': key, 'title': title, 'vanilla': java_reference}
    if vv_profile:
        declared['vv_scene_profile'] = {'reference': vv_profile['reference'],
                                        'selected_source_sha256': vv_profile['selected_source_sha256']}
    if destination.exists() and any(destination.iterdir()):
        if not marker.exists() or read_json(marker) != declared:
            raise ValueError('Output belongs to different source bytes or settings; choose a new conversion directory')
    destination.mkdir(parents=True, exist_ok=True)
    write_json(marker, declared)
    return destination


def file_sha256(path):
    """The SHA-256 of a file in hex, read a megabyte at a time."""
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_pack_identity(archives, version, authors):
    """The author's description, credit and version (pack.mcmeta), and the pack icon (pack.png) or None.

    A converted pack is still the author's pack, so its manifest shows the author's own identity.
    identity['formats'] holds the resource formats the pack declares.
    """
    with PackStack(archives) as stack:
        mcmeta = stack.read('pack.mcmeta') if 'pack.mcmeta' in stack.files else None
        icon = stack.read('pack.png') if 'pack.png' in stack.files else None
    return pack_identity(mcmeta, Path(archives[0]).name, version=version, authors=authors), icon


def vanilla_reference(destination, version=DEFAULT_JAVA_RELEASE):
    """Fetch the official Java client reference and verify Mojang's SHA-1."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(MOJANG_VERSION_MANIFEST) as response:
        versions = json.load(response)
    entry = next((item for item in versions['versions'] if item['id'] == version), None)
    if entry is None:
        raise ValueError('Java version absent from Mojang manifest: ' + version)
    with urllib.request.urlopen(entry['url']) as response:
        metadata = json.load(response)
    client = metadata['downloads']['client']
    output = destination / ('client-' + version + '.jar')
    # A jar from an earlier run is reused only while it still matches Mojang's hash.
    if not output.exists() or hashlib.sha1(output.read_bytes()).hexdigest() != client['sha1']:
        with urllib.request.urlopen(client['url']) as response:
            data = response.read()
        if len(data) != client['size'] or hashlib.sha1(data).hexdigest() != client['sha1']:
            raise ValueError('Downloaded Java reference failed Mojang integrity check')
        output.write_bytes(data)
    write_json(destination / ('client-' + version + '-provenance.json'),
               {'version': version, 'url': client['url'], 'sha1': client['sha1'], 'bytes': client['size']})
    return output


def find_samples(samples):
    """Mojang's bedrock-samples: the folder given, else one inside the project, else samples_path()."""
    if samples:
        return Path(samples)
    inside = ROOT / 'bedrock-samples'
    return inside if inside.exists() else samples_path(ROOT)


# --- Bindings ---

def resolve_pack_bindings(archives, vanilla, samples):
    """Work out what every Bedrock block face, carried slot and item shows in the author's pack.

    Returns (bindings, environment, held_items): the bindings document (bindings.json), the Bedrock
    names of the blocks, states and biomes the rules test, and the author's 3D item models for the
    hand and inventory.
    """
    resource_samples = samples / 'resource_pack'
    with PackStack(archives) as stack:
        bindings = resolve_bindings(stack, vanilla, resource_samples)
        environment = resolve_environment(stack, samples / 'metadata/vanilladata_modules')
        bindings.update(resolve_model_tints(stack, vanilla, bindings, environment, samples))
        with zipfile.ZipFile(vanilla) as jar:
            read_game = zip_reader(jar)

            def read(path):
                """The author's file, else the game's, else None."""
                return stack.read(path) if path in stack.files else read_game(path)

            bindings['carriedTints'] = carried_tints(read)
            bindings['itemTints'] = item_tints(bindings, read)
            held_items, bindings['heldItemsSkipped'] = plan_held_items(
                read, read_game, lambda path: path in stack.files, authored_item_ids(stack),
                read_json(resource_samples / 'blocks.json'))
    return bindings, environment, held_items


def authored_item_ids(stack):
    """The Java item ids the author ships an item model or an item definition for."""
    return {path.rsplit('/', 1)[1].removesuffix('.json') for path in stack.files
            if path.endswith('.json') and path.startswith(('assets/minecraft/models/item/', 'assets/minecraft/items/'))}


def zip_reader(archive):
    """read(path) for an open ZIP archive: the member's bytes, or None when there is no such member."""
    names = set(archive.namelist())

    def read(path):
        return archive.read(path) if path in names else None

    return read


# --- Materials ---

class EffectiveStack:
    """The author's pack stack over the Java client jar: the author's files win, the rest are Mojang's.

    It reads like a PackStack (files, read) for compile_stack_material. Java's missing-texture
    sprite is a file too, so a face that names no texture shows the same checkerboard as in Java.
    """

    def __init__(self, stack, vanilla):
        self.stack = stack
        self.vanilla = zipfile.ZipFile(vanilla)
        self.files = {path for path in self.vanilla.namelist() if path.startswith('assets/')}
        self.files.update(stack.files)
        self.files.add(MISSING_MODEL_SPRITE)
        self._drop_lower_animation_metadata()

    def _drop_lower_animation_metadata(self):
        """Forget an image's animation metadata when it comes from a lower level of the stack than the image.

        Image metadata belongs to that image's resource-stack level or a higher level. A lower
        vanilla sidecar cannot animate an author's replacement static image (for example magma or
        prismarine).
        """
        level = {archive: index for index, archive in enumerate(self.stack.archives)}
        for path in list(self.files):
            if not path.endswith('.png.mcmeta'):
                continue
            image = path.removesuffix('.mcmeta')
            if image not in self.stack.files:
                continue
            image_level = level[self.stack.files[image].archive]
            # The game jar lies below every archive of the stack.
            metadata_level = level[self.stack.files[path].archive] if path in self.stack.files else -1
            if metadata_level < image_level:
                self.files.remove(path)

    def read(self, path):
        """The bytes of a file: the missing-texture sprite, else the author's file, else the game's."""
        if path == MISSING_MODEL_SPRITE:
            return missing_model_png()
        return self.stack.read(path) if path in self.stack.files else self.vanilla.read(path)

    def close(self):
        self.vanilla.close()

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()


def prepare(archives, vanilla, destination, *, workers=4):
    """Convert every effective authored block/CTM sprite; collect failures explicitly.

    Each material is published only after its conversion succeeds. Source archives and installed
    packs are never written. Existing receipts support interruption recovery, but are accepted
    only when their source and outputs still match. These materials feed the compiled pack that
    carriers, replacement blocks and overlay surfaces draw from, with Java's interpolated animation
    frames baked in. Writes material-report.json and returns it.
    """
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    material_root = destination / 'materials'
    receipts_root = destination / 'receipts'
    material_root.mkdir(exist_ok=True)
    receipts_root.mkdir(exist_ok=True)
    with PackStack(archives) as author, EffectiveStack(author, vanilla) as effective:
        selected, unresolved = select_block_resources(author, effective)
        sprites = sorted(path for path in selected
                         if path.endswith('.png') and not is_labpbr_map(path, effective.files))
        # The importer and the runtime read the authored properties, inherited resources and exact
        # source sprites from java-input, kept apart from the decoded material tree.
        source_root = destination / 'java-input'
        copy_java_input(effective, selected, source_root, material_root)

        def convert_sprite(sprite):
            return convert_material(effective, sprite, destination, material_root, receipts_root)

        def report_progress(converted, failed):
            processed = converted + failed
            if processed % 100 == 0 or processed == len(sprites):
                progress = {'total': len(sprites), 'processed': processed, 'converted': converted,
                            'failed': failed, 'complete': processed == len(sprites)}
                write_json(destination / 'progress.json', progress)
                print(json.dumps(progress), flush=True)

        receipts, failures = convert_in_parallel(convert_sprite, sprites, workers, report_progress)
        failures = [{**failure, 'status': 'failed'} for failure in failures]
        report = {'source_archives': [str(Path(path).resolve()) for path in archives],
                  'vanilla_reference': str(Path(vanilla).resolve()),
                  'material_root': str(material_root), 'source_root': str(source_root),
                  'authored_block_sprites': len(sprites), 'converted_materials': len(receipts),
                  'animation_count': sum(bool(receipt['animation']) for receipt in receipts),
                  'renderer_losses': dict(Counter(loss for receipt in receipts for loss in receipt['renderer_losses'])),
                  'failed_materials': failures, 'unresolved_references': unresolved,
                  'material_conversion_complete': not failures and not unresolved,
                  'connected_renderer_complete': False, 'in_game_verified': False,
                  'policy': asdict(MATERIAL_POLICY),
                  'labpbr_version': '1.3', 'decoder_revision': MATERIAL_DECODER_REVISION,
                  'channel_extraction_complete': all(receipt.get('channel_manifest') for receipt in receipts),
                  'preserved_channel_counts': dict(Counter(channel for receipt in receipts
                                                           for channel in receipt.get('extracted_channels', [])))}
        write_json(destination / 'material-report.json', report)
        return report


def select_block_resources(author, effective):
    """The files the block textures and CTM rules need, and the tile references nothing resolves.

    A reference the author's pack does not hold inherits Minecraft's resource, with its animation
    and LabPBR maps.
    """
    selected, missing = author.block_dependencies()
    unresolved = []
    for reference in missing:
        path = reference['texture']
        if path in effective.files:
            selected.add(path)
            stem = path.removesuffix('.png')
            for companion in (path + '.mcmeta', stem + '_n.png', stem + '_s.png'):
                if companion in effective.files:
                    selected.add(companion)
        elif reference['status'] == 'requires_resource_resolution':
            unresolved.append(reference)
    return selected, unresolved


def is_labpbr_map(path, files):
    """True for a LabPBR _n or _s map whose base sprite exists; it is converted with that sprite."""
    return path.endswith(('_n.png', '_s.png')) and path[:-len('_n.png')] + '.png' in files


def copy_java_input(effective, selected, source_root, material_root):
    """Copy the selected files unchanged into source_root; CTM .properties files also go to material_root.

    Files an earlier run copied that the pack no longer has are removed, so a rule it dropped (or one
    BCT generated under another name) is not read back as a rule.
    """
    wanted = set(selected)
    for root, pattern in ((source_root, '*'), (material_root, '*.properties')):
        for stale in [path for path in Path(root).rglob(pattern) if path.is_file()]:
            if stale.relative_to(root).as_posix() not in wanted:
                stale.unlink()
    for path in sorted(selected):
        output = source_root / path
        output.parent.mkdir(parents=True, exist_ok=True)
        data = effective.read(path)
        if not output.exists() or output.read_bytes() != data:
            output.write_bytes(data)
        if path.endswith('.properties'):
            compiled = material_root / path
            compiled.parent.mkdir(parents=True, exist_ok=True)
            compiled.write_bytes(data)


def convert_material(effective, sprite, destination, material_root, receipts_root):
    """Convert one sprite into the material tree, or reuse its receipt when nothing it depends on changed.

    The sprite is converted into a staging folder and moved into the tree only once it converted
    completely, so a failure never leaves part of a material behind.
    """
    receipt_file = receipts_root / (sprite_digest(sprite) + '.json')
    stem = sprite.removesuffix('.png')
    sources = [sprite, sprite + '.mcmeta', stem + '_n.png', stem + '_n.png.mcmeta', stem + '_s.png',
               stem + '_s.png.mcmeta']
    source_hashes = {name: hashlib.sha256(effective.read(name)).hexdigest()
                     for name in sources if name in effective.files}
    if receipt_file.exists():
        previous = read_json(receipt_file)
        if receipt_is_current(previous, source_hashes, material_root):
            return previous
    with tempfile.TemporaryDirectory(prefix='.material-', dir=destination) as temporary:
        staging = Path(temporary)
        receipt = compile_stack_material(effective, sprite, staging, sprite, policy=MATERIAL_POLICY,
                                         animation_layout='grid')
        move_outputs(receipt, staging, material_root)
        receipt['source_sha256'] = source_hashes
        write_json(receipt_file, receipt)
        return receipt


def receipt_is_current(receipt, source_hashes, material_root):
    """True when this decoder and policy made the receipt from these sources and its outputs are intact."""
    return (receipt.get('decoder_revision') == MATERIAL_DECODER_REVISION
            and receipt.get('source_sha256') == source_hashes
            and receipt.get('policy') == asdict(MATERIAL_POLICY)
            and all((material_root / output['path']).is_file()
                    and file_sha256(material_root / output['path']) == output['sha256']
                    for output in receipt['outputs']))


def move_outputs(receipt, staging, material_root):
    """Move a converted material's files from its staging folder into the material tree."""
    for output in receipt['outputs']:
        target = material_root / output['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        (staging / output['path']).replace(target)


def convert_in_parallel(convert_sprite, sprites, workers, report_progress=None):
    """Run convert_sprite over the sprites on a thread pool; returns (receipts, failures) in completion order.

    One sprite failing never stops the others: each failure keeps its error, so the report lists
    every material that could not be converted. report_progress(converted, failed) is called after
    each sprite.
    """
    receipts, failures = [], []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(convert_sprite, sprite): sprite for sprite in sprites}
        for future in as_completed(pending):
            try:
                receipts.append(future.result())
            except Exception as error:
                failures.append({'source': pending[future], 'error': str(error)})
            if report_progress:
                report_progress(len(receipts), len(failures))
    return receipts, failures


def sprite_digest(sprite):
    """A stable, file-name-safe key for a sprite path: the SHA-256 of the path in hex."""
    return hashlib.sha256(sprite.encode()).hexdigest()


# --- Rules ---

def import_pack_rules(destination, compiled, bindings, environment, samples):
    """The engine's rule document for the pack's CTM properties, limited to the blocks the game has.

    Java and legacy block ids without a Bedrock block are left out and listed in block-ids.json.
    """
    rules = import_rules(destination / 'materials', bindings['baseTextures'], compiled_pack=compiled,
                         block_names=environment['blockNames'], biome_names=environment['biomeNames'],
                         state_names=environment['stateNames'], double_slab_blocks=environment['doubleSlabBlocks'],
                         texture_orientations=bindings['textureOrientations'],
                         biome_unions=environment['biomeUnions'], block_state_resolver=resolve_known_block_selection)
    attach_binding_providers(rules, bindings)
    dropped_ids, dropped_rules = sanitize(rules, known_blocks(samples))
    write_json(destination / 'block-ids.json', {
        'dropped_block_ids': dropped_ids, 'rules_without_a_bedrock_block': dropped_rules,
        'blocks_json_keys_left_out': bindings.get('unmapped_block_keys', [])})
    return rules


def attach_binding_providers(rules, bindings):
    """Keep runtime matching and source discovery aligned with every block state.

    The engine scans a block for rules only when it is a source block: one with a face that shows
    a texture some rule matches (in any state).
    """
    rules['baseTextures'] = bindings['baseTextures']
    for field in RUNTIME_BINDING_FIELDS:
        if field in bindings:
            rules[field] = bindings[field]
    matched = {tile for rule in rules['rules'] for tile in rule.get('matchTiles', [])}
    rules['sourceBlocks'] = sorted({block for block, faces in binding_faces(bindings)
                                    if matched.intersection(faces.values())})
    rules['nativeBlockJavaIds'] = native_block_java_ids()
    return rules


def binding_faces(bindings):
    """Yield (block, {face: texture}) for every texture a block can show, in any state.

    That is the plain base textures and, for each state variant, its faces, each model choice, the
    face layers (as a 'layer' face), the model parts and the parts of model part groups.
    """
    yield from bindings['baseTextures'].items()
    for block, variants in bindings.get('baseTextureVariants', {}).items():
        for variant in variants:
            yield block, variant.get('faces', {})
            # A variant without model choices is its own single choice.
            for choice in variant.get('modelChoices', [variant]):
                yield block, choice.get('faces', {})
                for layers in choice.get('faceLayers', {}).values():
                    for layer in layers:
                        yield block, {'layer': layer['texture']}
                for faces in model_part_faces(choice.get('modelParts', [])):
                    yield block, faces
                # The variant's own groups follow below, once.
                if choice is not variant:
                    for faces in group_part_faces(choice.get('modelPartsGroups', [])):
                        yield block, faces
            for faces in group_part_faces(variant.get('modelPartsGroups', [])):
                yield block, faces


def model_part_faces(parts):
    """Yield the {face: texture} map of each model part."""
    for part in parts:
        yield {face: data['texture'] for face, data in part['faces'].items()}


def group_part_faces(groups):
    """Yield the {face: texture} map of each model part of every option of the model part groups."""
    for group in groups:
        for option in group['modelChoices']:
            yield from model_part_faces(option.get('modelParts', []))


# --- Native textures, items, entities and tints ---

def bake_grass_decals(archives, materials, bindings):
    """Paint the grass block's random top decal (a clover layer) into the grass top's variations.

    Grass stays vanilla, so the decal model parts cannot be drawn; flat on the face, painted in they
    look the same (top_decals.py). Changes bindings['nativeVariations'] in place.
    """
    variations = bindings.get('nativeVariations', {})
    with PackStack(archives) as stack:
        decal = find_top_decals(stack, 'grass_block')
    if not decal:
        return
    tops = variations.get(GRASS_TOP_TEXTURE) or [[GRASS_TOP_TEXTURE, 1]]
    decals = variations.get(decal['texture']) or [[decal['texture'], 1]]
    # Only what was converted can be painted in.
    if not all((Path(materials) / sprite).exists() for sprite, _ in tops + decals):
        return
    variations[GRASS_TOP_TEXTURE] = bake_top_decals(materials, tops, decals, decal['bare'], materials)
    bindings['nativeVariations'] = variations


def random_mosaic_check(java_input, vanilla_patterns):
    """rule, targets -> whether a repeat mosaic draws as native random tiles (mosaic_random.py).

    Only mosaics on blocks that always stay vanilla qualify; swapped blocks draw the real mosaic.
    """
    def check(rule, targets):
        if not only_vanilla_blocks(targets, vanilla_patterns):
            return False
        # The grass side is one image with its overlay encoded in; random side tiles would each need
        # the overlay tile of the same place, which a separate overlay mosaic does not give.
        if LAYERED_TEXTURES.intersection(rule.get('matchTiles') or []):
            return False
        paths = [java_input / tile for tile in rule['tiles']]
        if not all(path.is_file() for path in paths):
            return False
        return random_looks_authored(paths, rule.get('width'), rule.get('height'))
    return check


def native_variation_rules(budget_report):
    """The ids of the rules the carrier budget turned into native atlas variations."""
    return {item['rule'] for item in budget_report['dropped_rules'] if item.get('native_variation')}


def prepare_native_materials(archives, vanilla, destination, bindings, *, workers=4):
    """Convert the materials the base packs show into native-materials; returns that folder.

    Native blocks animate with Bedrock's own flipbook blending, unlike the carrier materials, which
    bake Java's interpolated frames. Writes native-material-report.json.
    """
    destination = Path(destination)
    output = destination / 'native-materials'
    with PackStack(archives) as author, EffectiveStack(author, vanilla) as effective:
        sprites = native_material_sprites(bindings)

        def convert_sprite(sprite):
            with tempfile.TemporaryDirectory(prefix='.native-material-', dir=destination) as temporary:
                staging = Path(temporary)
                receipt = compile_stack_material(effective, sprite, staging, sprite, policy=MATERIAL_POLICY,
                                                 interpolation='native')
                move_outputs(receipt, staging, output)
                return receipt

        receipts, failures = convert_in_parallel(convert_sprite, sorted(sprites), workers)
        write_json(destination / 'native-material-report.json', {
            'converted': len(receipts), 'failures': failures,
            'interpolated_materials': [receipt['source'] for receipt in receipts
                                       if receipt['interpolation_differences']],
            'animation_policy': 'Bedrock native frame blending; '
                                'exact Java integer-tick baking is in the carrier material tree',
            'java_shader_interpolation_equivalence': False})
    return output


def native_material_sprites(bindings):
    """Every sprite a base pack shows: bound materials, face overrides, preview tiles and variation tiles."""
    face_fallbacks = bindings.get('nativeFaceFallbacks', {})
    sprites = set(bindings['authoredMaterialBindings'].values())
    sprites.update(sprite for faces in bindings.get('blockFaceOverrides', {}).values() for sprite in faces.values())
    sprites.update(bindings.get('nativeFallbacks', {}).values())
    sprites.update(face_fallbacks.values())
    sprites.update(tile.removesuffix('.png') + '.png'
                   for choices in bindings.get('nativeVariations', {}).values() for tile, _ in choices)
    if GRASS_SIDE in bindings['authoredMaterialBindings']:
        # The grass side is encoded together with its tinted overlay (see write_block_textures).
        overlay = grass_overlay_source(bindings)
        sprites.add(face_fallbacks.get(overlay, overlay))
    return sprites


def prepare_nonblock_resources(archives, vanilla, resource_samples, destination):
    """Convert the item, painting, particle and entity resources once; export_base copies them into each pack."""
    with PackStack(archives) as stack:
        plan = prepare_native_assets(stack, vanilla, resource_samples, destination / 'native-assets')
        write_json(destination / 'native-assets-plan.json', plan)
        # Entity textures, converted entity models and their animations (java_entities).
        prepare_entities(stack, vanilla, resource_samples, destination / 'entities')


def plan_baked_tints(archives, vanilla, resource_samples, bindings):
    """Plan the Java tints Bedrock does not apply, which export_base bakes into the textures.

    Java colours some textures as it draws them: with the author's fixed colour maps, and with
    vanilla tints Bedrock does not apply. Sets bindings['bakedTints'] and
    bindings['bakedTintReport']; returns {sprite: bytes} for the sprites the baker reads.
    """
    with PackStack(archives) as stack, zipfile.ZipFile(vanilla) as jar:
        read_vanilla = zip_reader(jar)

        def read_sprite(sprite):
            return stack.read(sprite) if sprite in stack.files else read_vanilla(sprite)

        fixed_colormaps = parse_fixed_colormaps({path: stack.read(path) for path in stack.files
                                                 if '/optifine/colormap/blocks/' in path})
        tint_plan, tint_report = plan_tints(bindings, fixed_colormaps, resource_samples, read_vanilla)
        # Each plan entry is ((r, g, b), block, authored, sprite, replace).
        tint_sprites = {entry[3]: read_sprite(entry[3]) for entry in tint_plan.values()}
    # Kept JSON-ready in the bindings: the colour tuple as a list.
    bindings['bakedTints'] = {path: [list(entry[0]), *entry[1:]] for path, entry in tint_plan.items()}
    bindings['bakedTintReport'] = tint_report
    return tint_sprites


# --- Base packs ---

def export_base(destination, bindings, samples, key, title, *, vv_profile=None, biome_colors=False,
                tint_sprites=None, held_items=None, water=None):
    """Export native base textures, keeping all original Bedrock atlas selectors.

    Writes one resource pack per graphics mode (base-classic, base-vv, base-rtx), each also packaged
    as an .mcpack, and lists them in base-exports.json. samples is bedrock-samples' resource_pack
    folder: its atlas entries and block definitions are the vanilla ones each pack builds on.

    A Java pack has no Bedrock lighting, so no Vibrant Visuals scene is written unless vv_profile
    supplies one. Biome colour files replace other packs' whole biome definitions (fog and lighting
    links included), so they are opt-in (biome_colors).
    """
    destination = Path(destination).resolve()
    # The materials converted for native blocks (prepare_native_materials), else the carrier ones.
    materials = destination / 'native-materials'
    if not materials.exists():
        materials = destination / 'materials'
    samples = Path(samples)
    vanilla_atlas = read_json(samples / 'textures/terrain_texture.json')['texture_data']
    vanilla_blocks = read_json(samples / 'blocks.json')
    vanilla_flipbooks = read_json(samples / 'textures/flipbook_textures.json')
    exports = []
    for renderer in RENDERERS:
        pack = destination / ('base-' + renderer)
        # Built from empty each run: files of an earlier run (old water, removed textures) must not ship.
        shutil.rmtree(pack, ignore_errors=True)
        pack.mkdir()
        write_json(pack / 'manifest.json', manifest(key, title, renderer))
        material_bindings, block_overrides, face_aliases = plan_material_bindings(bindings, vanilla_blocks)
        atlas, animations, failures, grass_adapters = write_block_textures(
            pack, renderer, materials, bindings, material_bindings, face_aliases,
            vanilla_atlas=vanilla_atlas, vanilla_flipbooks=vanilla_flipbooks, water=water)
        write_block_definitions(pack, renderer, block_overrides, vanilla_blocks)
        opaque_slots, carried_slots, baked_tints = fill_slots_and_tints(pack, atlas, bindings, tint_sprites)
        animations = install_water(pack, renderer, water, animations)
        write_atlas(pack, key, atlas, animations)
        native_assets, entities = export_nonblock_resources(destination, pack, renderer)
        # The RTX pack is the published one for every mode, so it carries the author's Vibrant Visuals scene too.
        scene = apply_scene_profile(pack, vv_profile, samples=samples) if renderer in ('vv', 'rtx') and vv_profile else None
        native_biome_tints = None
        if biome_colors and (bindings.get('grassTints') or bindings.get('foliageTints')):
            native_biome_tints = apply_native_biome_tints(pack, bindings)
        rtx_compatibility = declare_ray_tracing(pack) if renderer == 'rtx' else None
        # The author's 3D item models in the hand and inventory, where the game draws a flat sprite.
        # They rewrite blocks.json and the atlas, so they are written after both.
        held = write_held_items(pack, held_items or [], key.replace('-', '_'), vanilla_blocks, vanilla_atlas)
        # A few oversized textures would make the game's block atlas too large to load.
        capped = cap_block_textures(pack)
        archive = package_directory(pack, destination / (key + '-base-' + renderer + '.mcpack'))
        export = {'renderer': renderer, 'resource_pack': str(pack), 'archive': str(archive),
                  'manifest': read_json(pack / 'manifest.json'), 'texture_bindings': len(material_bindings),
                  'failed_bindings': failures, 'native_animations': len(animations), 'vv_scene_profile': scene,
                  'native_grass_adapters': grass_adapters, 'opaque_slots_from_author_texture': opaque_slots,
                  'carried_slots_from_author_texture': carried_slots, 'baked_java_tints': baked_tints,
                  'held_items': held}
        for name, value in (('capped_textures', capped), ('native_biome_tints', native_biome_tints), ('native_assets', native_assets),
                            ('entities', entities), ('rtx_compatibility', rtx_compatibility)):
            if value:
                export[name] = value
        exports.append(export)
    write_json(destination / 'base-exports.json', exports)
    return exports


def manifest(key, title, renderer):
    """The manifest of the base resource pack for one graphics mode.

    The UUIDs derive from the key and the mode, so converting the same pack again updates it in the
    game instead of adding a second pack.
    """
    data = {'format_version': 2,
            'header': {'name': title + ' - ' + renderer.upper(),
                       'description': 'Author textures with native Bedrock material bindings.',
                       'uuid': str(uuid.uuid5(uuid.NAMESPACE_URL, 'bct/base/' + key + '/' + renderer)),
                       'version': [1, 2, 1], 'min_engine_version': [1, 26, 20]},
            'modules': [{'type': 'resources',
                         'uuid': str(uuid.uuid5(uuid.NAMESPACE_URL, 'bct/base/module/' + key + '/' + renderer)),
                         'version': [1, 2, 1]}]}
    if renderer != 'classic':
        data['capabilities'] = ['pbr', 'raytraced'] if renderer == 'rtx' else ['pbr']
    return data


def plan_material_bindings(bindings, vanilla_blocks):
    """Which converted material each Bedrock texture shows, and the per-block face overrides.

    When the author's model gives one face of a block another texture than vanilla's model does,
    the block gets a copy of its vanilla definition whose face names a new atlas entry
    (author_face_<hash>), so the other faces and blocks sharing the Bedrock texture keep theirs.
    Returns (material_bindings, block_overrides, face_aliases): {Bedrock texture path: material},
    {block: definition} and {texture path: alias}.
    """
    material_bindings = dict(bindings.get('authoredMaterialBindings', bindings['materialBindings']))
    # The tile a world-independent rule shows on a lone block replaces the plain texture. Every face
    # keeps Bedrock's random rotation, the grass top included: grass stays vanilla (it is never
    # replaced), so no authored mosaic is drawn on it that a rotation could disturb.
    material_bindings.update(bindings.get('nativeFallbacks', {}))
    face_fallbacks = bindings.get('nativeFaceFallbacks', {})
    block_overrides, face_aliases = {}, {}
    for block, faces in bindings.get('blockFaceOverrides', {}).items():
        name = block.removeprefix('minecraft:')
        original = vanilla_blocks.get(name)
        if original is None:
            raise ValueError('Block face override has no native definition: ' + block)
        definition = copy.deepcopy(original)
        textures = definition.get('textures', {})
        if isinstance(textures, str):
            textures = {face: textures for face in FACES}
        for face, sprite in faces.items():
            sprite = face_fallbacks.get(sprite, sprite)
            alias = 'author_face_' + sprite_digest(sprite)[:20]
            relative = 'textures/blocks/' + alias
            material_bindings[relative] = sprite
            face_aliases[relative] = alias
            textures[face] = alias
        definition['textures'] = textures
        block_overrides[name] = definition
    return material_bindings, block_overrides, face_aliases


def write_block_textures(pack, renderer, materials, bindings, material_bindings, face_aliases, *,
                         vanilla_atlas, vanilla_flipbooks, water):
    """Copy every bound material into the pack with its maps, random variations, atlas entries and flipbooks.

    Each texture keeps the vanilla atlas entries that show it, so every block showing it in vanilla
    shows the author's texture. Returns (atlas, animations, failures, grass_adapters).
    """
    variations = bindings.get('nativeVariations', {})
    authored_bindings = bindings.get('authoredMaterialBindings', {})
    atlas, animations, failures, grass_adapters = {}, [], [], []
    animated = set()  # (atlas alias, texture) pairs that have their flipbook entry
    for bedrock_path, sprite in sorted(material_bindings.items()):
        relative = bedrock_path.removesuffix('.png')
        # Water: the author's Bedrock water when supplied; otherwise Java water only in Classic,
        # since Vibrant Visuals waves and ray-traced water need Bedrock's own water textures.
        if relative.removeprefix('textures/blocks/') in WATER_TEXTURES and (water or renderer != 'classic'):
            continue
        source = materials / sprite
        if not source.is_file():
            failures.append({'target': bedrock_path, 'source': sprite, 'error': 'Material conversion failed'})
            continue
        target = pack / (relative + '.png')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        grass_overlay = None
        if relative == GRASS_SIDE:
            # Java draws soil under a tinted overlay; Bedrock tints the grass side by its alpha, so
            # the two are encoded into one image with the overlay's alpha as the tint mask.
            grass_adapters.append(adapt_grass_side(target, materials, sprite, bindings))
            grass_overlay = grass_overlay_source(bindings)
        copy_channels(source, target, renderer)
        # Random rules on terrain blocks become native atlas variations.
        choices = variations.get(authored_bindings.get(bedrock_path, sprite)) or variations.get(sprite) or []
        variation_paths, variation_failures = write_variation_tiles(
            target, bedrock_path, choices, materials, renderer, variations, grass_overlay)
        failures += variation_failures
        metadata = Path(str(source) + '.mcmeta')
        for alias, entry in vanilla_atlas.items():
            paths = texture_paths(entry.get('textures'))
            if relative not in paths:
                continue
            atlas[alias] = with_variations(entry, paths, relative, variation_paths)
            if metadata.exists() and (alias, relative) not in animated:
                timing = read_json(metadata)['animation']
                animations.append(flipbook_entry(alias, relative, timing, vanilla_flipbooks))
                animated.add((alias, relative))
        if relative in face_aliases:
            atlas[face_aliases[relative]] = {'textures': relative}
    return atlas, animations, failures, grass_adapters


def write_variation_tiles(target, bedrock_path, choices, materials, renderer, variations, grass_overlay=None):
    """Write each random choice beside target as <name>_v<index>.png, with its maps.

    choices are [tile, weight] pairs. For the grass side, grass_overlay names the overlay texture
    its models declare, and each side tile is encoded with the overlay tile of the same index, as
    the plain side is. Returns (atlas variations, failures).
    """
    relative = bedrock_path.removesuffix('.png')
    overlay_tiles = []
    if grass_overlay is not None:
        overlay_tiles = variations.get(grass_overlay) or []
    variation_paths, failures = [], []
    for index, (tile, weight) in enumerate(choices):
        tile_source = materials / (tile.removesuffix('.png') + '.png')
        if not tile_source.is_file():
            failures.append({'target': bedrock_path, 'source': tile, 'error': 'Variation material conversion failed'})
            continue
        tile_target = target.with_name(target.stem + f'_v{index}.png')
        if grass_overlay is not None:
            overlay_name = overlay_tiles[index][0] if index < len(overlay_tiles) else grass_overlay
            overlay = materials / overlay_name
            if not overlay.is_file():
                failures.append({'target': bedrock_path, 'source': overlay_name,
                                 'error': 'Variation material conversion failed'})
                continue
            with Image.open(tile_source) as side_image, Image.open(overlay) as overlay_image:
                encode_grass_side(side_image, overlay_image).save(tile_target)
        else:
            shutil.copyfile(tile_source, tile_target)
        copy_channels(tile_source, tile_target, renderer)
        variation_paths.append({'path': relative + f'_v{index}', 'weight': weight})
    return variation_paths, failures


def with_variations(entry, paths, relative, variation_paths):
    """The vanilla atlas entry, switched to the random variations when it shows this texture alone."""
    settings = texture_settings(entry)
    # Biome tint (overlay_color) only applies to the plain entry; inside variations the placeholder
    # colour is drawn literally (grass sides turn orange-red), so tinted tiles keep one texture.
    if variation_paths and set(paths) == {relative} and 'overlay_color' not in settings:
        return {**entry, 'textures': {'variations': [{**settings, **variation} for variation in variation_paths]}}
    return entry


def texture_settings(entry):
    """The settings an atlas entry gives its textures (such as overlay_color), without their paths."""
    textures = entry.get('textures', [])
    items = [textures] if isinstance(textures, dict) else textures
    settings = {}
    for item in items:
        if isinstance(item, dict):
            settings.update((name, value) for name, value in item.items() if name != 'path')
    return settings


def flipbook_entry(alias, texture, timing, vanilla_flipbooks):
    """The flipbook entry of an animated texture: Java's frame timing over vanilla's entry for that tile.

    timing is the converted material's animation metadata (frames, frametime, interpolate).
    """
    vanilla = next((entry for entry in vanilla_flipbooks
                    if entry.get('atlas_tile') == alias and entry.get('flipbook_texture') == texture), {})
    return {**vanilla, 'atlas_tile': alias, 'flipbook_texture': texture,
            'ticks_per_frame': timing.get('frametime', 1), 'frames': timing['frames'],
            'blend_frames': timing.get('interpolate', False)}


def copy_channels(source, target, renderer):
    """Copy a converted material's normal and MERS maps next to its color image for one renderer.

    Classic draws colour only. Vibrant Visuals takes the MERS map with its subsurface channel; ray
    tracing reads RGB MER, so there the subsurface channel (alpha) is dropped.
    """
    descriptor = source.with_suffix('.texture_set.json')
    if renderer == 'classic' or not descriptor.exists():
        return
    converted = {'color': target.stem}
    for channel, value in read_json(descriptor)['minecraft:texture_set'].items():
        if channel == 'color':
            continue
        image = source.parent / (value + '.png')
        if channel == 'normal':
            map_name = 'normal'
        elif renderer == 'vv':
            map_name = 'mers'
        else:
            map_name = 'mer'
        output = target.with_name(target.stem + '_' + map_name + '.png')
        if renderer == 'rtx' and channel == 'metalness_emissive_roughness_subsurface':
            with Image.open(image) as decoded:
                decoded.convert('RGB').save(output)
            channel = 'metalness_emissive_roughness'
        else:
            shutil.copyfile(image, output)
        converted[channel] = output.stem
    write_json(target.with_suffix('.texture_set.json'),
               {'format_version': TEXTURE_SET_FORMAT, 'minecraft:texture_set': converted})


# Blocks some game versions leave out of their own blocks.json: 1.26.52 draws red sand through the
# legacy "sand" entry and shows it as plain sand. The samples' entry gives red sand its own texture.
MISSING_IN_SOME_GAMES = ('red_sand',)


def write_block_definitions(pack, renderer, block_overrides, block_definitions=None):
    """Write blocks.json with only the blocks this pack changes.

    A full copy of bedrock-samples' blocks.json would replace the player's game version's own
    block definitions, which can differ from the samples' (a game version may still draw red sand
    through the legacy "sand" entry, for example). The RTX pack always gets the file: the add-on
    composer requires it, and restore_carrier_blocks_for_rtx adds to it.
    """
    missing = {name: block_definitions[name] for name in MISSING_IN_SOME_GAMES
               if block_definitions and name in block_definitions and name not in block_overrides}
    if block_overrides or missing or renderer == 'rtx':
        write_json(pack / 'blocks.json', {'format_version': BLOCKS_FORMAT, **missing, **block_overrides})


def fill_slots_and_tints(pack, atlas, bindings, tint_sprites):
    """Fill the texture slots Java packs lack, and bake in the tints Bedrock does not apply.

    Returns (opaque_slots, carried_slots, baked_tints): what each step wrote.
    """
    opaque_slots = fill_opaque_slots(pack, atlas)
    carried_slots = fill_carried_slots(pack, bindings.get('carriedTints', {}))
    carried_slots += fill_item_tints(pack, bindings.get('itemTints', {}))
    # The bindings keep each tint colour as a JSON list; bake() takes an (r, g, b) tuple.
    tint_plan = {path: (tuple(entry[0]), *entry[1:]) for path, entry in bindings.get('bakedTints', {}).items()}
    baked_tints = bake(pack, tint_plan, (tint_sprites or {}).get)
    return opaque_slots, carried_slots, baked_tints


def fill_opaque_slots(rp, atlas):
    """Give Bedrock's opaque leaf slots the author's own texture.

    Bedrock atlas entries such as oak_leaves list a second "<name>_opaque" texture that some
    block states use (player-placed leaves among them). Java packs have no such texture, so the
    slot would fall back to vanilla's 16 px leaf. It gets the author's texture and maps instead,
    so every state of the block looks the same. rp is the resource pack folder; returns the
    filled slots.
    """
    filled = []
    for entry in atlas.values():
        for relative in texture_paths(entry.get('textures')):
            slot = rp / relative
            if not slot.name.endswith('_opaque') or texture_file(rp, relative):
                continue
            leaf = slot.with_name(slot.name.removesuffix('_opaque'))
            image = texture_file(leaf.parent, leaf.name)
            if image is None:
                continue
            write_opaque_leaf(image, slot.parent / (slot.name + '.png'))
            copy_leaf_maps(leaf, slot)
            filled.append(relative)
    return sorted(set(filled))


def write_opaque_leaf(image, output):
    """Save a leaf texture without transparency, its see-through pixels a dark shade of the leaf.

    Bedrock draws this slot without transparency, as vanilla's opaque leaves; the colour the author
    left in the hidden pixels is often white, so it would show as white gaps.
    """
    with Image.open(image) as decoded:
        pixels = np.asarray(decoded.convert('RGBA')).copy()
    visible = pixels[:, :, 3] >= 128
    if visible.any():
        shade = (pixels[visible][:, :3].mean(axis=0) * OPAQUE_LEAF_GAP_SHADE).astype(np.uint8)
    else:
        shade = np.zeros(3, dtype=np.uint8)
    pixels[~visible, :3] = shade
    pixels[:, :, 3] = 255
    Image.fromarray(pixels).save(output)


def copy_leaf_maps(leaf, slot):
    """Give the opaque slot a copy of the leaf's texture set, with the leaf's maps renamed after the slot."""
    descriptor = leaf.parent / (leaf.name + '.texture_set.json')
    if not descriptor.exists():
        return
    channels = {}
    for channel, value in read_json(descriptor)['minecraft:texture_set'].items():
        if channel == 'color':
            channels[channel] = slot.name
        elif isinstance(value, str) and value.startswith(leaf.name):
            name = slot.name + value[len(leaf.name):]
            shutil.copyfile(leaf.parent / (value + '.png'), slot.parent / (name + '.png'))
            channels[channel] = name
        else:
            channels[channel] = value
    write_json(slot.parent / (slot.name + '.texture_set.json'),
               {'format_version': TEXTURE_SET_FORMAT, 'minecraft:texture_set': channels})


def install_water(pack, renderer, water, animations):
    """Write the water this graphics mode draws; returns the flipbook entries to keep.

    water holds the --vv-scene pack's own water textures and flipbooks, which replace the Java
    water. The RTX pack, which is the published pack for every mode, ships no water at all: the
    game's own water draws right in Vibrant Visuals (waves) and in ray tracing, where an author's
    water textures look milky.
    """
    if renderer == 'rtx':
        return animations
    if water:
        for relative, data in water['files'].items():
            (pack / relative).parent.mkdir(parents=True, exist_ok=True)
            (pack / relative).write_bytes(data)
        replaced = {entry['flipbook_texture'] for entry in water['flipbooks']}
        kept = [entry for entry in animations if entry.get('flipbook_texture') not in replaced]
        animations = kept + water['flipbooks']
    return animations


def write_atlas(pack, key, atlas, animations):
    """Write the terrain atlas (with vanilla's atlas settings) and, when anything is animated, the flipbooks."""
    write_json(pack / 'textures/terrain_texture.json', {'resource_pack_name': key, 'texture_name': 'atlas.terrain',
                                                        'padding': 8, 'num_mip_levels': 4, 'texture_data': atlas})
    if animations:
        write_json(pack / 'textures/flipbook_textures.json', animations)


def export_nonblock_resources(destination, pack, renderer):
    """Copy the converted items, paintings, particles and entities into one pack.

    Returns (native_assets, entities); each is None when its plan was not written.
    """
    native_assets = None
    asset_plan = destination / 'native-assets-plan.json'
    if asset_plan.is_file():
        plan = read_json(asset_plan)
        native_assets = export_native_assets(plan['material_root'], plan, pack, renderer)
    entities = None
    entity_plan = destination / 'entities' / 'entities-plan.json'
    if entity_plan.is_file():
        entities = export_entities(read_json(entity_plan), pack, renderer)
    return native_assets, entities


def declare_ray_tracing(pack):
    """Check the pack's block texture sets for ray tracing; the manifest declares 'raytraced' only if all pass."""
    compatibility = native_rtx_compatibility(pack)
    definition = read_json(pack / 'manifest.json')
    definition['capabilities'] = ['pbr', 'raytraced'] if compatibility['native_block_pbr_supported'] else ['pbr']
    write_json(pack / 'manifest.json', definition)
    return compatibility


def package_directory(source, output):
    """Publish complete ZIPs atomically, leaving an existing archive intact.

    The archive is written beside the output and moved into place only after every member reads
    back undamaged.
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix='.' + output.stem, suffix='.zip', dir=output.parent, delete=False) as file:
        temporary = Path(file.name)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for path in sorted(Path(source).rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(source).as_posix())
        with zipfile.ZipFile(temporary) as archive:
            damaged = archive.testzip()
            if damaged:
                raise ValueError('Damaged archive member: ' + damaged)
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


# --- Replacement blocks ---

def include_base_materials(archives, vanilla, destination, compiled, bindings):
    """Include original face families required for transparent face replacement.

    Every face texture a block shows in any state is copied into the compiled pack with its
    animation metadata, texture set and maps; a face no earlier step converted is converted here.
    Writes base-material-copy.json.
    """
    destination = Path(destination)
    compiled = Path(compiled)
    materials = destination / 'materials'
    sprites = {sprite for _, faces in binding_faces(bindings) for sprite in faces.values()}
    copied = 0
    fallbacks = 0
    missing = []
    with PackStack(archives) as author, EffectiveStack(author, vanilla) as effective:
        for sprite in sorted(sprites):
            source = materials / sprite
            if not source.exists():
                if sprite not in effective.files:
                    missing.append(sprite)
                    continue
                compile_stack_material(effective, sprite, materials, sprite, policy=MATERIAL_POLICY,
                                       animation_layout='grid')
                fallbacks += 1
            for path in material_family(source):
                target = compiled / path.relative_to(materials)
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    shutil.copyfile(path, target)
                    copied += 1
    report = {'copied_files': copied, 'fallback_materials': fallbacks, 'missing': missing}
    write_json(destination / 'base-material-copy.json', report)
    if missing:
        raise ValueError('Original face material is unresolved: ' + missing[0])
    return report


def material_family(image):
    """A converted image with its animation metadata, texture set and channel maps, where they exist."""
    family = [image]
    metadata = Path(str(image) + '.mcmeta')
    descriptor = image.with_suffix('.texture_set.json')
    if metadata.exists():
        family.append(metadata)
    if descriptor.exists():
        family.append(descriptor)
        for channel, value in read_json(descriptor)['minecraft:texture_set'].items():
            if channel != 'color' and isinstance(value, str):
                family.append(image.parent / (value + '.png'))
    return family


def build_replacement_blocks(archives, vanilla, samples, destination, compiled, key, rules, policy, variation_rules,
                             skip=()):
    """Write the replacement blocks and the leaf model blocks into destination/replacement.

    Rules already drawn as atlas variations never cause a replacement; they ride along on blocks
    another rule replaces, so the replacement keeps showing them. Leaves whose blockstate shows the
    author's own models (not a full cube) become model blocks. Writes replacement-report.json;
    returns (engine data or None, report).
    """
    targets = {rule['id']: sorted(rule_targets(rule, rules)) for rule in rules['rules']}
    candidates = {rule_id: blocks for rule_id, blocks in targets.items() if rule_id not in variation_rules}
    passengers = {rule_id: blocks for rule_id, blocks in targets.items() if rule_id in variation_rules}
    with PackStack(archives) as stack, zipfile.ZipFile(vanilla) as jar:
        # java_block_bindings' model reader: the author's model files first, then the game's.
        models = _Models(jar, stack)
        model_plans, model_skipped = plan_all(models, policy, known_blocks(samples), java_id, jar)
    replacement_data, report = build_replacements(rules, candidates, policy, source=compiled, samples=samples, key=key,
                                                  output=destination / 'replacement', passengers=passengers,
                                                  model_blocks=model_plans, skip=skip)
    report['model_blocks_skipped'] = model_skipped
    write_json(destination / 'replacement-report.json', report)
    return replacement_data, report


# --- Carriers ---

def choose_carrier_rules(destination, rules, budget, replaced_blocks, overlay_hosts):
    """The rules entity carriers draw: those no replacement block or overlay surface draws, within the budget.

    Writes carrier-budget.json and warns when the pack has more carrier rules than the budget
    allows. Returns (carried rule document, budget report).
    """
    carried_document = {**rules, 'rules': [rule for rule in rules['rules'] if rule['id'] not in overlay_hosts]}
    carried_rules, _, budget_report = apply_budget(carried_document, budget, native=replaced_blocks)
    write_json(destination / 'carrier-budget.json', budget_report)
    if budget_report.get('warning'):
        print('Warning: ' + budget_report['warning'])
    return carried_rules, budget_report


def build_connected_addon(destination, compiled, rules, key, samples, vv_profile, carrier_filter):
    """Build the entity carrier add-on for the carried rules.

    Writes rules.json and connected-report.json; returns the add-on's copy in destination and the
    renderer coverage.
    """
    rule_file = destination / 'rules.json'
    write_json(rule_file, rules)
    # PBR resources also serve Classic graphics, so one carrier build covers both.
    archive = build_connected(compiled, rule_file, key + '-vv', samples=samples,
                              artifact_name=key + '-connected.mcaddon', renderer='vv',
                              block_material_fallback=vv_profile['block_mers'] if vv_profile else None,
                              carrier_filter=carrier_filter)
    connected = destination / archive.name
    shutil.copyfile(archive, connected)
    runtime_report = read_json(archive.parent / ('connected-' + key + '-vv-report.json'))
    write_json(destination / 'connected-report.json', runtime_report)
    coverage = {'imported_rules': runtime_report['rules'],
                'rendered_full_cube_blocks': len(runtime_report['rendered_source_blocks']),
                'blocks_outside_cube_renderer': runtime_report['outside_cube_renderer'],
                'animated_materials': runtime_report['animated_materials'], 'in_game_verified': False}
    return connected, coverage


def restore_carrier_blocks_for_rtx(base, connected, resource_samples):
    """Restore, in the RTX pack, the blocks whose faces the carrier pack blanks; returns their names.

    The carrier pack blanks the faces its carriers draw over (transparent tiles, model parts), and
    ray tracing hides the carriers, so the RTX pack gives those blocks their bedrock-samples
    definition back and is packaged again. Only those blocks: every other definition stays the
    game's own.
    """
    rtx = next(item for item in base if item['renderer'] == 'rtx')
    pack = Path(rtx['resource_pack'])
    blocks = read_json(pack / 'blocks.json')
    # A block the RTX pack already defines (an author's face, a held item icon) shows its faces.
    restored = sorted(name for name in carrier_blanked_blocks(connected) if name not in blocks)
    if restored:
        vanilla_blocks = read_json(resource_samples / 'blocks.json')
        blocks.update({name: vanilla_blocks[name] for name in restored})
        write_json(pack / 'blocks.json', blocks)
        package_directory(pack, rtx['archive'])
    return restored


def carrier_blanked_blocks(connected):
    """The blocks.json names of the blocks whose faces the carrier add-on blanks."""
    blanked = set()
    with zipfile.ZipFile(connected) as archive:
        for name in archive.namelist():
            # Only a pack's own blocks.json, directly inside its folder.
            path = PurePosixPath(name)
            if path.name != 'blocks.json' or len(path.parts) != 2:
                continue
            for block, entry in json.loads(archive.read(name).decode('utf-8-sig')).items():
                textures = entry.get('textures') if isinstance(entry, dict) else None
                faces = textures.values() if isinstance(textures, dict) else [textures]
                if CARRIER_BLANK_TEXTURE in faces:
                    blanked.add(block)
    return blanked


# --- Publishing ---

def publish_conversion(destination, base, connected_addon, key, title, samples, *, root, icon=None,
                       replacement=None, identity=None, overlay=None):
    """Publish the converted pack as one add-on that depends on the published engine.

    Like the author's own Bedrock pack, one resource pack serves every graphics mode, with no
    setting to pick: the RTX base pack (RGB MER maps, which Vibrant Visuals reads too, the author's
    Vibrant Visuals scene, the game's water, and the blocks carriers draw over with their own faces,
    since ray tracing hides carriers). It is also the source of the terrain edges.
    """
    destination = Path(destination)
    published = next(item for item in base if item['renderer'] == 'rtx')
    terrain, terrain_report = prepare_shared_terrain(published['resource_pack'], key, title, samples,
                                                     root=root, overlay=overlay)
    archive = addon_package.compose_source_addon(published['archive'], connected_addon,
                                                 destination / (key + '.mcaddon'), key=key, title=title,
                                                 terrain_addon=terrain, icon=icon, identity=identity,
                                                 replacement_addon=replacement, raytraced=True)
    installable = {'renderer': 'classic-vv-rtx', 'archive': str(archive), 'capabilities': ['pbr', 'raytraced'],
                   'engine_dependency': engine_dependency()}
    return {'installable_packs': [installable], 'terrain_provider': terrain_report}


def prepare_shared_terrain(resource_pack, key, title, samples, *, root, overlay=None):
    """Build static terrain provider artwork, recording unsupported animation.

    The generated grass and sand edges are drawn from resource_pack. overlay: the
    overlay_surfaces.build folder. Its surface blocks and engine data join the terrain add-on, and
    generated edges the pack's own overlay rules already draw are left out. Returns (terrain
    add-on or None, report).
    """
    try:
        provider, sources = terrain_pack.prepare(root, Path(resource_pack), key, samples=samples)
    except ValueError as error:
        if ANIMATED_TERRAIN_ERROR not in str(error):
            raise
        unsupported = {'status': 'unsupported_animated_terrain', 'error': str(error),
                       'animations_preserved_in_source_pack': True,
                       'animation_replaced_with_still': False}
        if overlay is None:
            return None, unsupported
        # The overlay surfaces still need a terrain add-on to travel in.
        addon, _ = terrain_providers.build(root=root, provider_paths=[], pack_key=key, title=title, samples=samples,
                                           overlay=overlay)
        return addon, {**unsupported, 'archive': str(addon), 'overlay_surfaces_only': True}
    data = read_json(provider)
    namespace_effects(data, key)
    transitions = None
    if overlay is not None:
        authored, generated = prune_generated_edges(data, read_json(Path(overlay) / 'authored.json'),
                                                    known_blocks(samples))
        transitions = {'authored_overlays': authored, 'generated_edges': generated}
    write_json(provider, data)
    addon, build_report = terrain_providers.build(root=root, provider_paths=[provider], pack_key=key, title=title,
                                                  samples=samples, overlay=overlay)
    report = {'status': 'built', 'archive': str(addon), 'sources': sources,
              'provider_count': len(build_report['providers']), 'in_game_verified': False}
    if transitions:
        report['transitions'] = transitions
    return addon, report


def namespace_effects(provider, key):
    """Move the provider's bct: block and entity types into the pack's own namespace, bct_<key>.

    Providers must have distinct block and actor types when several converted packs are active
    together. Vanilla source block names remain unchanged.
    """
    namespace = 'bct_' + key.replace('-', '_')
    for effect in provider['effects'].values():
        for field in ('entity', 'native_block'):
            if effect.get(field, '').startswith('bct:'):
                effect[field] = namespace + ':' + effect[field].split(':', 1)[1]


# --- Reports ---

def rule_accounting(document, carried, budget_report, replacement_report, overlay_hosts=None):
    """Where each authored rule draws, per block and per graphics mode.

    Native variations, replacement blocks and overlay surfaces (overlay_hosts:
    {rule: blocks}) draw in Classic, Vibrant Visuals and ray tracing; entity
    carriers only in Classic and Vibrant Visuals. Every target block that
    nothing draws is listed with the reasons.
    """
    overlay_hosts = overlay_hosts or {}
    carrier_rules = {rule['id'] for rule in carried['rules']}
    variation_rules = native_variation_rules(budget_report)
    replaced_blocks = set(budget_report.get('native_replacement_blocks', []))
    replacement_reasons = {}
    for item in replacement_report['unsupported']:
        replacement_reasons.setdefault((item['rule'], item['block']), []).append(item['reason'])
    mode_totals = {mode: {'full': 0, 'partial': 0, 'none': 0} for mode in ('classic', 'vibrant_visuals', 'ray_tracing')}
    details = []
    for rule in document['rules']:
        rule_id = rule['id']
        targets = rule_targets(rule, document)
        variation_blocks = set(targets) if rule_id in variation_rules else set()
        replacement_blocks = set(replacement_report['rules'].get(rule_id, [])) - variation_blocks
        surface_blocks = set(overlay_hosts.get(rule_id, []))
        native_blocks = variation_blocks | replacement_blocks | surface_blocks
        # Layer and model part rules have no face targets; the replacement blocks showing them count.
        targets = set(targets) | native_blocks
        uncarried = set(budget_report['uncarried_targets'].get(rule_id, []))
        carrier_blocks = targets - uncarried - replaced_blocks if rule_id in carrier_rules else set()
        drawn_by = (('native_variation', variation_blocks), ('native_replacement', replacement_blocks),
                    ('native_overlay_surface', surface_blocks), ('entity_carrier', carrier_blocks))
        entry = {'rule': rule_id, 'filename': rule.get('filename'), 'method': rule['method'],
                 'drawn_by': [method for method, blocks in drawn_by if blocks]}
        if replacement_blocks:
            entry['replacement_blocks'] = sorted(replacement_blocks)
        if surface_blocks:
            entry['overlay_surface_hosts'] = sorted(surface_blocks)
        if carrier_blocks:
            entry['carrier_blocks'] = sorted(carrier_blocks)
        missing = sorted(targets - native_blocks - carrier_blocks)
        not_drawn = not_drawn_blocks(rule_id, targets, missing, surface_blocks, replacement_reasons)
        if not_drawn:
            entry['not_drawn'] = not_drawn
        drawn = {'classic': native_blocks | carrier_blocks, 'vibrant_visuals': native_blocks | carrier_blocks,
                 'ray_tracing': native_blocks}
        entry['modes'] = {}
        for mode, blocks in drawn.items():
            coverage = 'full' if targets and blocks >= targets else 'partial' if blocks else 'none'
            entry['modes'][mode] = coverage
            mode_totals[mode][coverage] += 1
        details.append(entry)

    def rules_drawn_by(method):
        return sum(method in item['drawn_by'] for item in details)

    return {'rules': len(details),
            'entity_carrier': rules_drawn_by('entity_carrier'),
            'native_variation': rules_drawn_by('native_variation'),
            'native_replacement': rules_drawn_by('native_replacement'),
            'native_overlay_surface': rules_drawn_by('native_overlay_surface'),
            'not_drawn_anywhere': sum(not item['drawn_by'] for item in details),
            'partially_drawn': sum(bool(item['drawn_by']) and bool(item.get('not_drawn')) for item in details),
            'modes': mode_totals, 'details': details}


def not_drawn_blocks(rule_id, targets, missing, surface_blocks, replacement_reasons):
    """The target blocks of a rule that nothing draws, each with the reason."""
    if not targets:
        return [{'block': None, 'reason': 'the matched texture is not shown by any block face '
                                          '(chained rule or Java-only texture name)'}]
    if missing and surface_blocks:
        # Java draws an overlay only on a face that is a whole square (stairs, buttons and the like get none).
        return [{'block': block, 'reason': 'no face of this block is a whole square, so Java draws no overlay on it'}
                for block in missing]
    return [{'block': block, 'reason': '; '.join(replacement_reasons.get((rule_id, block), [])
                                                 + ['kept off entity carriers by the carrier budget'])}
            for block in missing]


def texture_load(base, destination, replacement_report, overlay_data, overlay_report):
    """Images and estimated GPU memory per graphics mode, and the custom block permutations.

    Each mode loads its base pack, the replacement blocks and, when there are any, the overlay
    surfaces.
    """
    permutations = replacement_report.get('permutations', 0) + overlay_report.get('permutations', 0)
    load = load_report({item['renderer']: item['resource_pack'] for item in base}, destination / 'replacement',
                       permutations)
    if overlay_data:
        # Overlay surface textures load in every graphics mode, on top of the base and replacement packs.
        overlay_load = measure([destination / 'overlay' / 'Overlay_RP'])
        load['overlay_surfaces'] = {**overlay_load, 'permutations': overlay_report['permutations']}
        for renderer, renderer_load in load['renderers'].items():
            renderer_load['images'] += overlay_load['images']
            renderer_load['megapixels'] = round(renderer_load['megapixels'] + overlay_load['megapixels'], 1)
            before = renderer_load['estimated_gpu_mib']
            renderer_load['estimated_gpu_mib'] += overlay_load['estimated_gpu_mib']
            # load_report already warned about modes over the limit; this warns about the ones the
            # overlay surfaces push over it.
            if before <= WARN_MIB < renderer_load['estimated_gpu_mib']:
                load['warnings'].append(f"{renderer}: about {renderer_load['estimated_gpu_mib']} MiB of textures; "
                                        'packs this large have failed to load. '
                                        'Convert a lower-resolution edition of the pack.')
    return load


def overlay_summary(overlay_report, terrain_report):
    """The overlay surface report for conversion.json, with the grass and sand transitions."""
    summary = {field: overlay_report.get(field) for field in OVERLAY_SUMMARY_FIELDS}
    # Which grass and sand transitions the pack's own overlays draw, and which keep generated edges.
    summary['transitions'] = terrain_report.get('transitions')
    return summary


def rtx_summary(base, restored_blocks):
    """Ray tracing in the conversion report: the published pack's capabilities and checks, and the
    blocks that keep their own faces because ray tracing hides the carriers drawn over them."""
    rtx = next(item for item in base if item['renderer'] == 'rtx')
    return {'archive': rtx['archive'],
            'capabilities': rtx['manifest'].get('capabilities', []),
            'compatibility': rtx['rtx_compatibility'],
            'mode_selection': 'none: one pack for every graphics mode',
            'restored_carrier_blocks': restored_blocks}


def check_schemas(archive, samples, destination):
    """Check every JSON file of the finished add-on against Mojang's schemas; returns the counts.

    Writes schema-check.json, which also lists the kinds of file Mojang publishes no schema for.
    """
    schema_check = addon_check(archive, samples)
    write_json(destination / 'schema-check.json', schema_check)
    summary = {'checked': schema_check['checked'], 'without_schema': schema_check['without_schema'],
               'problems': len(schema_check['problems'])}
    if schema_check['problems']:
        print(f"Warning: {len(schema_check['problems'])} generated files fail Mojang's schemas; see schema-check.json")
    return summary


# --- Command line ---

def parse_arguments():
    """The command-line options; docs/CONVERTER.md describes them."""
    parser = argparse.ArgumentParser(
        description='Convert a Java texture pack into one Bedrock add-on for the Bedrock Connected Textures '
                    'engine. The source packs are never changed; an interrupted run resumes.')
    parser.add_argument('--java', type=Path, nargs='+', required=True,
                        help='Java pack ZIP files, base pack first; later files win')
    parser.add_argument('--vanilla', type=Path,
                        help='Java client JAR to read vanilla files from (default: downloaded from Mojang for the '
                             "pack's Java version)")
    parser.add_argument('--output', type=Path, required=True, help='Folder for the converted pack and its reports')
    parser.add_argument('--workers', type=int, default=4, help='Textures converted at the same time (default 4)')
    parser.add_argument('--samples', type=Path,
                        help="Mojang's bedrock-samples folder (default: BEDROCK_SAMPLES, or ../bedrock-samples)")
    parser.add_argument('--key', default='author-pack',
                        help='Short name used for file names and pack identifiers (letters, digits, dashes)')
    parser.add_argument('--title', default=None,
                        help="The pack's name as players see it (default: the Java pack's file name, with its version and resolution)")
    parser.add_argument('--materials-only', action='store_true',
                        help='Only convert the textures and material maps, then stop')
    parser.add_argument('--vv-scene', type=Path,
                        help='Local Bedrock pack supplying VV scene settings; its artwork is not imported')
    parser.add_argument('--bedrock-grade', action='store_true',
                        help="Give the block textures the colour grade of the author's own Bedrock pack (--vv-scene), "
                             'learned from the textures both packs share; without it the Java colours are kept')
    parser.add_argument('--carrier-filter', choices=['point', 'bilinear'], default='bilinear',
                        help='Carrier texture filtering; point keeps nearest sampling')
    parser.add_argument('--carrier-budget', type=Path,
                        help='JSON budget: entity_allowlist, native_only and carrier_types '
                             '(default converter/data/carrier-budget.json)')
    parser.add_argument('--max-permutations', type=int, default=WORLD_PERMUTATIONS,
                        help='Custom block permutations the pack may have before the costliest slabs and stairs are '
                             f'left out (default {WORLD_PERMUTATIONS}, the number the game warns about)')
    parser.add_argument('--scale-to-atlas', nargs='?', type=int, const=True, default=False, metavar='WIDTH',
                        help="Scale the block textures down (never up) until the game's terrain atlas holds them, "
                             'or to WIDTH pixels; without it, a pack too large for the atlas is scaled down by the '
                             'game, blurred')
    parser.add_argument('--version',
                        help="The pack's own version, such as 4.1.0 "
                             '(default: a tag like R4.1.0 in the file name, else 1.0.0)')
    parser.add_argument('--author', action='append',
                        help='Pack author credited in the Bedrock manifest (default: a "By ..." line in pack.mcmeta); '
                             'repeat for several')
    return parser.parse_args()


def main():
    """Convert from the command line, or with --materials-only decode the materials alone."""
    options = parse_arguments()
    if options.materials_only:
        reference = options.vanilla or vanilla_reference(options.output / 'reference')
        report = prepare(options.java, reference, options.output, workers=options.workers)
        print(f"Material conversion: {report['converted_materials']}/{report['authored_block_sprites']}")
        print('Details: ' + str((options.output / 'material-report.json').resolve()))
    else:
        report = convert(options.java, options.output, vanilla=options.vanilla, samples=options.samples,
                         key=options.key, title=options.title, workers=options.workers,
                         vv_scene=options.vv_scene, carrier_filter=options.carrier_filter,
                         carrier_budget=options.carrier_budget, version=options.version, authors=options.author,
                         bedrock_grade=options.bedrock_grade, max_permutations=options.max_permutations,
                         scale_to_atlas=options.scale_to_atlas)
        print(f"Imported {report['rule_count']} connected-texture rules.")
        for item in report['installable_packs']:
            print('Converted pack: ' + item['archive'])
        print('Install Bedrock Connected Textures once, then import this pack and turn it on in the world.')
        print('Coverage and limits: ' + str((options.output / 'conversion.json').resolve()))


if __name__ == '__main__':
    main()
