"""Bind the author's item, painting, particle and entity textures to Bedrock's own textures.

A texture is bound only with evidence: an item atlas entry, identical vanilla pixels, or
an official particle definition. Every authored texture gets a record with either its
binding or the reason it has none. Geometry and entity animation are converted elsewhere;
ray tracing gets these textures colour only, since its PBR applies to blocks alone.
"""
from collections import Counter, defaultdict
import copy
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

import numpy as np
from PIL import Image

from common import read_json, write_json
from java_materials import MATERIAL_DECODER_REVISION, MaterialPolicy, compile_stack_material

JAVA = 'assets/minecraft/textures/'
AUTHORED_CATEGORIES = ('item', 'painting', 'particle', 'entity', 'gui')


def _item_aliases():
    """{Bedrock item texture name: Java item sprite name} where the two differ."""
    aliases = {
        'apple_golden': 'golden_apple', 'apple_golden_enchanted': 'enchanted_golden_apple',
        'beef_raw': 'beef', 'beef_cooked': 'cooked_beef', 'chicken_raw': 'chicken',
        'chicken_cooked': 'cooked_chicken', 'porkchop_raw': 'porkchop',
        'porkchop_cooked': 'cooked_porkchop', 'mutton_raw': 'mutton',
        'mutton_cooked': 'cooked_mutton', 'rabbit_raw': 'rabbit',
        'rabbit_cooked': 'cooked_rabbit', 'fish_raw': 'cod', 'fish_cooked': 'cooked_cod',
        'fish_salmon_raw': 'salmon', 'fish_salmon_cooked': 'cooked_salmon',
        'fish_clownfish_raw': 'tropical_fish', 'fish_pufferfish_raw': 'pufferfish',
        'carrot_golden': 'golden_carrot', 'carrot_on_a_stick': 'carrot_on_a_stick',
        'bucket_empty': 'bucket', 'bucket_water': 'water_bucket',
        'bucket_lava': 'lava_bucket', 'bucket_milk': 'milk_bucket',
        'bucket_cod': 'cod_bucket', 'bucket_salmon': 'salmon_bucket',
        'bucket_tropical': 'tropical_fish_bucket', 'bucket_pufferfish': 'pufferfish_bucket',
        'bucket_axolotl': 'axolotl_bucket', 'bucket_tadpole': 'tadpole_bucket',
        'bucket_powder_snow': 'powder_snow_bucket', 'reeds': 'sugar_cane',
        'slimeball': 'slime_ball', 'clay_ball': 'clay_ball', 'fireball': 'fire_charge',
        'fireworks': 'firework_rocket', 'fireworks_charge': 'firework_star',
        'map_empty': 'map', 'map_filled': 'filled_map', 'book_normal': 'book',
        'book_writable': 'writable_book', 'book_written': 'written_book',
        'book_enchanted': 'enchanted_book', 'dye_powder_red': 'red_dye',
        'dye_powder_blue': 'lapis_lazuli', 'dye_powder_black': 'ink_sac',
        'dye_powder_white': 'bone_meal', 'netherbrick': 'nether_brick',
        'ender_eye': 'ender_eye', 'ender_pearl': 'ender_pearl',
        'totem': 'totem_of_undying', 'lead': 'lead', 'string': 'string',
        'boat_darkoak': 'dark_oak_boat', 'oak_sign': 'oak_sign',
        'fishing_rod_uncast': 'fishing_rod', 'potion_bottle_empty': 'glass_bottle',
        'seeds_wheat': 'wheat_seeds', 'sweet_berries': 'sweet_berry',
    }
    for bedrock_material, java_material in (('wood', 'wooden'), ('gold', 'golden'), ('stone', 'stone'),
                                            ('iron', 'iron'), ('diamond', 'diamond'),
                                            ('netherite', 'netherite'), ('copper', 'copper')):
        for tool in ('sword', 'shovel', 'pickaxe', 'axe', 'hoe'):
            aliases[bedrock_material + '_' + tool] = java_material + '_' + tool
        for armor in ('helmet', 'chestplate', 'leggings', 'boots'):
            aliases[bedrock_material + '_' + armor] = java_material + '_' + armor
    for wood in ('oak', 'spruce', 'birch', 'jungle', 'acacia', 'dark_oak', 'mangrove', 'cherry', 'pale_oak', 'bamboo'):
        aliases['boat_' + wood] = wood + '_boat'
        aliases['chest_boat_' + wood] = wood + '_chest_boat'
    for color in ('white', 'orange', 'magenta', 'light_blue', 'yellow', 'lime', 'pink', 'gray', 'silver', 'cyan',
                  'purple', 'blue', 'brown', 'green', 'red', 'black'):
        java_color = 'light_gray' if color == 'silver' else color
        aliases['dye_powder_' + color] = java_color + '_dye'
        aliases['bed_' + color] = java_color + '_bed'
    # Bedrock's old dye textures show lapis lazuli, ink sacs and bone meal; the _new ones are the dyes.
    aliases.update({'dye_powder_blue': 'lapis_lazuli', 'dye_powder_black': 'ink_sac',
                    'dye_powder_white': 'bone_meal', 'dye_powder_blue_new': 'blue_dye',
                    'dye_powder_black_new': 'black_dye', 'dye_powder_white_new': 'white_dye'})
    return aliases


ITEM_ALIASES = _item_aliases()

# These skins keep the shared legacy cuboid UV layout. The pixel identity checks still
# reject any reference revision with different texture coordinates.
SHARED_ENTITY_UV_FAMILIES = {'creeper', 'enderman', 'endermite', 'blaze', 'ghast',
                             'slime', 'skeleton', 'wither_skeleton', 'stray',
                             'zombie', 'husk', 'drowned'}

# These bindings keep the official particle definition's movement and tint.
PARTICLES = {
    'flame': ('basic_flame.json', 'candle_flame.json', 'small_flame.json'),
    'glow': ('glow.json',),
    'big_smoke': ('campfire_smoke.json', 'campfire_smoke_tall.json'),
}
# Items that a CEM model draws as an entity; their sprite alone does not show them.
CEM_ITEMS = {'bow', 'crossbow', 'shield', 'trident'}
# A flat normal and a plain non-metal, non-emissive LabPBR specular for missing maps.
FLAT_NORMAL = (128, 128, 255, 255)
PLAIN_SPECULAR = (0, 10, 0, 255)
SMOKE_FRAMES = 12
MAX_PAINTING_ATLAS_SCALE = 64


def painting_regions(vanilla, samples):
    """Locate painting regions by exact vanilla pixels instead of guessed slots."""
    atlas_path = _native_image(samples, 'textures/painting/kz')
    if atlas_path is None:
        return {}, None
    atlas = np.asarray(Image.open(atlas_path).convert('RGBA'))
    regions = {}
    for path in sorted(vanilla.namelist()):
        if not path.startswith(JAVA + 'painting/') or not path.endswith('.png'):
            continue
        image = np.asarray(_image(vanilla.read(path)))
        height, width = image.shape[:2]
        matches = []
        for y in range(0, atlas.shape[0] - height + 1, 16):
            for x in range(0, atlas.shape[1] - width + 1, 16):
                if np.array_equal(image, atlas[y:y + height, x:x + width]):
                    matches.append([x, y, width, height])
        if len(matches) == 1:
            regions[path] = matches[0]
    return regions, atlas_path


def plan_native_assets(stack, vanilla, samples):
    """Measured native texture coverage of the author's art; the inputs are only read.

    vanilla is the game's Java client archive and samples Mojang's bedrock-samples
    resource pack, the references that prove each binding.
    """
    samples = Path(samples)
    authored = sorted(path for path in stack.files if path.startswith(JAVA)
                      and path.endswith('.png') and not path.endswith(('_n.png', '_s.png'))
                      and path[len(JAVA):].split('/', 1)[0] in AUTHORED_CATEGORIES)
    with zipfile.ZipFile(vanilla) as reference:
        planner = _NativeAssetPlanner(stack, reference, samples)
        records = [planner.record(source) for source in authored]
        particle_definitions = planner.bind_particle_families(records)
    count = Counter(record['category'] for record in records)
    supported = Counter(record['category'] for record in records if record['status'] != 'unsupported')
    leaf_particle_art = any('leaves' in Path(record['source']).stem for record in records
                            if record['category'] == 'particle')
    return {'format_version': 1, 'records': records, 'bindings': planner.bindings,
            'item_atlas': planner.item_atlas, 'particle_definitions': particle_definitions,
            'painting_atlas': str(planner.painting_atlas) if planner.painting_atlas else None,
            'cem_models_not_converted': planner.cem_models,
            'counts': {category: {'authored_albedos': total, 'native_bound': supported[category],
                                  'unsupported': total - supported[category]}
                       for category, total in sorted(count.items())},
            'biome_tinted_leaf_particles': {'author_art_present': leaf_particle_art,
                                            'engine_color_preserved': True, 'definition_overridden': False},
            'geometry_converted': False, 'entity_animation_converted': False,
            'full_coverage': all(record['status'] != 'unsupported' and not record.get('renderer_losses')
                                 for record in records),
            'in_game_verified': False}


def prepare_native_assets(stack, vanilla, samples, destination, *, plan=None):
    """Compile the non-block LabPBR maps once. Returns the plan with the material receipts.

    Compiled materials are cached in destination and reused while their sources are unchanged.
    """
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    plan = copy.deepcopy(plan) if plan is not None else plan_native_assets(stack, vanilla, samples)
    plan['bindings'] = [binding for binding in plan['bindings'] if not binding.get('atlas_scale')]
    cache_path = destination / 'native-material-cache.json'
    cache = read_json(cache_path) if cache_path.is_file() else {}
    receipts = []
    policy = MaterialPolicy('labpbr-1.3', 'directx', 'perceptual', True)
    for binding in plan['bindings']:
        target = binding['target'] + '.png'
        source = binding['source']
        effective = _smoke_bank_stack(stack, binding['sources'], source) if binding.get('frame_bank') else stack
        is_particle = binding['category'] == 'particle' and not binding.get('frame_bank')
        receipts.append(_prepare_material(effective, source, destination, target, policy, cache, particle=is_particle))
    atlas_rows = [record for record in plan['records'] if record['status'] == 'native_atlas_bound']
    if atlas_rows:
        receipt, binding = _painting_atlas_material(stack, plan, atlas_rows, destination, policy, cache)
        receipts.append(receipt)
        plan['bindings'].append(binding)
    plan['material_root'] = str(destination.resolve())
    plan['material_receipts'] = receipts
    write_json(cache_path, cache)
    write_json(destination / 'native-assets-plan.json', plan)
    return plan


def export_native_assets(material_root, plan, destination, renderer='vv'):
    """Write the native texture resources; RTX gets their colour only."""
    if renderer not in ('classic', 'vv', 'rtx'):
        raise ValueError('Unknown renderer')
    material_root, destination = Path(material_root), Path(destination)
    written = set()
    for binding in plan['bindings']:
        target = binding['target']
        source = material_root / (target + '.png')
        if target in written:
            continue
        if not source.is_file():
            raise ValueError('Prepared native material absent: ' + target)
        output = destination / (target + '.png')
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, output)
        written.add(target)
        descriptor = source.with_suffix('.texture_set.json')
        # Ray tracing applies PBR to blocks only, so these textures keep their colour alone.
        if renderer == 'vv' and descriptor.is_file():
            data = read_json(descriptor)
            for key, value in data['minecraft:texture_set'].items():
                if key == 'color' or not isinstance(value, str):
                    continue
                shutil.copyfile(source.parent / (value + '.png'), output.parent / (value + '.png'))
            write_json(output.with_suffix('.texture_set.json'), data)
    if any(record['category'] == 'item' and record['status'] == 'native_texture_bound' for record in plan['records']):
        write_json(destination / 'textures/item_texture.json', plan['item_atlas'])
    for row in plan['particle_definitions']:
        write_json(destination / row['path'], _particle_definition(row, material_root))
    report = {'renderer': renderer, 'native_texture_outputs': len(written),
              'counts': plan['counts'], 'records': plan['records'],
              'native_output_bindings': plan['bindings'],
              'unsupported': [record for record in plan['records'] if record['status'] == 'unsupported'],
              'cem_models_not_converted': plan['cem_models_not_converted'],
              'biome_tinted_leaf_particles': plan['biome_tinted_leaf_particles'],
              'particle_timing': ('Java texture frames use particle-local time; '
                                  'official Bedrock motion, emitter and tint are retained'),
              'rtx_nonblock_pbr_supported': False, 'full_coverage': plan['full_coverage'], 'in_game_verified': False}
    write_json(destination / 'native-assets-coverage.json', report)
    return report


class _NativeAssetPlanner:
    """The vanilla references for each authored texture, and the bindings found so far."""

    def __init__(self, stack, reference, samples):
        self.stack = stack
        self.reference = reference
        self.samples = samples
        self.names = set(reference.namelist())
        atlas_path = samples / 'textures/item_texture.json'
        self.item_atlas = read_json(atlas_path) if atlas_path.is_file() else {'texture_data': {}}
        self.item_targets = self._item_targets()
        self.regions, self.painting_atlas = painting_regions(reference, samples)
        self.entity_targets = self._entity_targets()
        self.cem_models = sorted(path for path in stack.files
                                 if '/optifine/cem/' in path and path.endswith(('.jem', '.jpm')))
        self.cem_names = {Path(path).stem.removeprefix('warm_').removeprefix('cold_') for path in self.cem_models}
        self.bindings = []

    def record(self, source):
        """The coverage record of one authored texture; any bindings it earns join self.bindings."""
        category = source[len(JAVA):].split('/', 1)[0]
        row = {'source': source, 'category': category, 'status': 'unsupported', 'targets': []}
        metadata = source + '.mcmeta'
        try:
            animation = json.loads(self.stack.read(metadata)).get('animation') if metadata in self.stack.files else None
        except ValueError:
            row['reason'] = 'texture_animation_metadata_invalid'
            return row
        if animation is not None and category != 'particle':
            row['reason'] = 'native_nonparticle_texture_animation_requires_runtime_adapter'
            return row
        if animation and animation.get('interpolate'):
            row['renderer_losses'] = ['native_particle_flipbook_does_not_blend_java_texture_frames']
        if category == 'item':
            self._bind_item(row)
        elif category == 'painting':
            self._bind_painting(row)
        elif category == 'entity':
            self._bind_entity(row)
        elif category == 'particle':
            self._bind_particle(row)
        else:
            row['reason'] = 'gui_layout_requires_ui_adapter'
        return row

    def bind_particle_families(self, records):
        """Bind each particle family whose frames are all bound; returns the particle definitions to write.

        Campfire smoke needs all twelve frames, which become one frame bank.
        """
        definitions = []
        for family, templates in PARTICLES.items():
            target = 'textures/particle/java_' + family
            inputs = [record for record in records if record['category'] == 'particle'
                      and record['status'] == 'native_particle_bound' and record['targets'] == [target]]
            if not inputs:
                continue
            sources = sorted((record['source'] for record in inputs),
                             key=lambda path: int(re.search(r'_(\d+)\.png$', path)[1]) if family == 'big_smoke' else 0)
            expected_frames = ['big_smoke_' + str(index) for index in range(SMOKE_FRAMES)]
            if family == 'big_smoke' and [Path(path).stem for path in sources] != expected_frames:
                for record in inputs:
                    record.update(status='unsupported', targets=[],
                                  reason='campfire_particle_requires_all_twelve_frames')
                continue
            self.bindings.append({'source': sources[0], 'sources': sources, 'target': target,
                                  'category': 'particle', 'frame_bank': family == 'big_smoke'})
            for name in templates:
                path = self.samples / 'particles' / name
                if path.is_file():
                    definitions.append({'path': 'particles/' + name, 'definition': read_json(path),
                                        'target': target, 'family': family, 'source': sources[0]})
        return definitions

    def _item_targets(self):
        """{Java item sprite: [Bedrock item textures]} through the item atlas and the renamed sprites."""
        targets = sorted({path for entry in self.item_atlas['texture_data'].values()
                          for path in _paths(entry.get('textures'))})
        source_targets = defaultdict(list)
        for target in targets:
            bedrock_name = Path(target).name
            source = JAVA + 'item/' + ITEM_ALIASES.get(bedrock_name, bedrock_name) + '.png'
            if source in self.stack.files:
                source_targets[source].append(target)
        return source_targets

    def _entity_targets(self):
        """{Java entity texture: [Bedrock entity textures]} where exactly one vanilla texture has the same pixels."""
        by_digest = defaultdict(list)
        for path in self.names:
            if path.startswith(JAVA + 'entity/') and path.endswith('.png'):
                by_digest[_digest(_image(self.reference.read(path)))].append(path)
        targets = defaultdict(list)
        for path in sorted((self.samples / 'textures/entity').rglob('*')):
            if path.suffix.lower() not in ('.png', '.tga') or path.stem.endswith(('_mer', '_mers', '_normal')):
                continue
            try:
                candidates = by_digest.get(_digest(Image.open(path).convert('RGBA')), [])
            except OSError:
                continue
            if len(candidates) == 1:
                targets[candidates[0]].append(path.relative_to(self.samples).with_suffix('').as_posix())
        return targets

    def _bind_item(self, row):
        source = row['source']
        name = Path(source).stem
        issue = _item_model_issue(self.stack, self.reference, name)
        if name in self.cem_names & CEM_ITEMS:
            issue = 'custom_entity_item_model_requires_model_conversion'
        if issue:
            row['reason'] = issue
        elif self.item_targets[source]:
            row.update(status='native_texture_bound', targets=self.item_targets[source],
                       evidence='explicit_native_item_atlas_material_identity')
            for target in row['targets']:
                self.bindings.append({'source': source, 'target': target, 'category': row['category']})
        else:
            row['reason'] = 'no_verified_native_item_material'

    def _bind_painting(self, row):
        source = row['source']
        direct = 'textures/painting/' + Path(source).stem
        native = _native_image(self.samples, direct)
        vanilla = JAVA + 'painting/' + Path(source).name
        if (native and vanilla in self.names
                and _digest(Image.open(native).convert('RGBA')) == _digest(_image(self.reference.read(vanilla)))):
            row.update(status='native_texture_bound', targets=[direct], evidence='identical_vanilla_painting_pixels')
            self.bindings.append({'source': source, 'target': direct, 'category': row['category']})
        elif source in self.regions:
            row.update(status='native_atlas_bound', targets=['textures/painting/kz'],
                       region=self.regions[source], evidence='identical_vanilla_rgba_atlas_region')
        else:
            row['reason'] = 'no_unambiguous_native_painting_slot'

    def _bind_entity(self, row):
        source = row['source']
        segment = source[len(JAVA + 'entity/'):]
        family = segment.split('/')[0].removesuffix('.png')
        stem = Path(source).stem
        if any(name in segment.split('/') or stem.endswith(name) for name in self.cem_names):
            row['reason'] = 'custom_entity_model_changes_uv_binding'
        elif family not in SHARED_ENTITY_UV_FAMILIES or stem not in SHARED_ENTITY_UV_FAMILIES:
            row['reason'] = 'entity_model_uv_layout_not_verified'
        elif source in self.entity_targets and source in self.names:
            own = _image(self.stack.read(source))
            vanilla = _image(self.reference.read(source))
            if own.width * vanilla.height != own.height * vanilla.width:
                row['reason'] = 'entity_texture_aspect_differs_from_reference'
            else:
                row.update(status='native_texture_bound', targets=self.entity_targets[source],
                           evidence='shared_legacy_cuboid_uv_family_unique_vanilla_rgba_no_authored_cem')
                for target in row['targets']:
                    self.bindings.append({'source': source, 'target': target, 'category': row['category']})
        else:
            row['reason'] = 'no_verified_matching_entity_uv_material'

    def _bind_particle(self, row):
        name = Path(row['source']).stem
        family = 'big_smoke' if re.fullmatch(r'big_smoke_\d+', name) else name
        templates = PARTICLES.get(family, ())
        valid = [template for template in templates if (self.samples / 'particles' / template).is_file()]
        if valid:
            row.update(status='native_particle_bound', targets=['textures/particle/java_' + family],
                       evidence='official_native_particle_definition_adapter', definitions=valid)
        else:
            row['reason'] = 'no_verified_native_particle_effect_adapter'


class _VirtualStack:
    """A pack stack held in memory, for compiling images made by the converter."""

    def __init__(self, data):
        self.files = data

    def read(self, path):
        return self.files[path]


def _paths(value):
    """Every string inside a nested texture value."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _paths(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _paths(item)


def _image(data):
    return Image.open(io.BytesIO(data)).convert('RGBA')


def _digest(image):
    """SHA-256 of an image's size and pixels."""
    return hashlib.sha256(str(image.size).encode() + image.tobytes()).hexdigest()


def _native_image(samples, relative):
    for suffix in ('.tga', '.png', '.jpg', '.jpeg'):
        path = samples / (relative + suffix)
        if path.is_file():
            return path
    return None


def _item_model_issue(stack, vanilla, name):
    """Why an authored item model blocks a sprite binding, or None; only authored models are checked."""
    path = 'assets/minecraft/models/item/' + name + '.json'
    if path not in stack.files:
        return None
    seen = set()
    while path not in seen:
        seen.add(path)
        try:
            data = json.loads(stack.read(path) if path in stack.files else vanilla.read(path))
        except (ValueError, KeyError):
            return 'item_model_json_invalid_or_missing'
        if data.get('elements'):
            return 'custom_item_geometry_requires_model_conversion'
        parent = data.get('parent')
        if parent in ('builtin/generated', 'minecraft:builtin/generated'):
            return None
        if not parent:
            return 'authored_item_model_has_no_generated_parent'
        if 'builtin/entity' in parent:
            return 'entity_item_model_requires_model_conversion'
        namespace, resource = parent.split(':', 1) if ':' in parent else ('minecraft', parent)
        path = f'assets/{namespace}/models/{resource}.json'
        if path not in stack.files and path not in vanilla.namelist():
            return 'item_model_parent_missing'
    return 'item_model_parent_cycle'


def _smoke_bank_stack(stack, sources, name):
    """The campfire smoke frames stacked into one sprite sheet, with their maps, as a stack to compile from."""
    images = [_image(stack.read(source)) for source in sources]
    if len({image.size for image in images}) != 1 or images[0].width != images[0].height:
        raise ValueError('Particle frame bank requires equal square frames')
    data = {}
    for suffix, default in (('', None), ('_n', FLAT_NORMAL), ('_s', PLAIN_SPECULAR)):
        if suffix and not any(source[:-4] + suffix + '.png' in stack.files for source in sources):
            continue
        canvas = Image.new('RGBA', (images[0].width, images[0].height * len(images)))
        for index, source in enumerate(sources):
            channel = source[:-4] + suffix + '.png'
            if channel in stack.files:
                image = _image(stack.read(channel))
            else:
                image = Image.new('RGBA', images[0].size, default)
            if image.size != images[0].size:
                raise ValueError('Particle bank maps must match the authored color dimensions')
            canvas.paste(image, (0, index * image.height))
        data[name[:-4] + suffix + '.png'] = _png_bytes(canvas)
    return _VirtualStack(data)


def _painting_atlas_material(stack, plan, atlas_rows, destination, policy, cache):
    """Compile the paintings that live in Bedrock's shared painting atlas into a copy of that atlas.

    The atlas is enlarged by a whole number so every authored painting fills its region by
    repeating pixels; nothing is ever downscaled. Returns (receipt, binding).
    """
    scales = []
    for row in atlas_rows:
        image = _image(stack.read(row['source']))
        width, height = row['region'][2:]
        scales.extend((image.width // math.gcd(width, image.width), image.height // math.gcd(height, image.height)))
    scale = math.lcm(*scales)
    if scale > MAX_PAINTING_ATLAS_SCALE:
        raise ValueError('Painting atlas scale exceeds the supported size; no texture downsampled')
    reference = Image.open(plan['painting_atlas']).convert('RGBA')
    base = reference.resize((reference.width * scale, reference.height * scale), Image.Resampling.NEAREST)
    normal = Image.new('RGBA', base.size, FLAT_NORMAL)
    specular = Image.new('RGBA', base.size, PLAIN_SPECULAR)
    for row in atlas_rows:
        x, y, width, height = row['region']
        box = (width * scale, height * scale)
        for suffix, canvas in (('', base), ('_n', normal), ('_s', specular)):
            source = row['source'][:-4] + suffix + '.png'
            if source in stack.files:
                image = _image(stack.read(source))
                if box[0] % image.width or box[1] % image.height:
                    raise ValueError('Painting maps cannot fit the verified atlas by integer pixel replication')
                canvas.paste(image.resize(box, Image.Resampling.NEAREST), (x * scale, y * scale))
        painting = _image(stack.read(row['source']))
        row['atlas_pixel_replication'] = [box[0] // painting.width, box[1] // painting.height]
    name = JAVA + 'painting/java_verified_atlas.png'
    data = {name[:-4] + suffix + '.png': _png_bytes(image)
            for suffix, image in (('', base), ('_n', normal), ('_s', specular))}
    receipt = _prepare_material(_VirtualStack(data), name, destination, 'textures/painting/kz.png', policy, cache)
    binding = {'source': name, 'sources': [row['source'] for row in atlas_rows],
               'target': 'textures/painting/kz', 'category': 'painting', 'atlas_scale': scale}
    return receipt, binding


def _unfold_particle_animation(destination, target, receipt):
    """Write a particle flipbook's frames out in play order.

    Bedrock particle flipbooks step through the rows in order, so a Java frame list that
    repeats or reorders frames becomes one row per entry, keeping Java's timing.
    """
    metadata = Path(destination) / (target + '.mcmeta')
    if not metadata.is_file():
        return
    document = read_json(metadata)
    frames = document['animation']['frames']
    if frames == list(range(len(frames))):
        return
    color = Path(destination) / target
    textures = [color]
    descriptor = color.with_suffix('.texture_set.json')
    if descriptor.is_file():
        textures.extend(color.parent / (value + '.png')
                        for channel, value in read_json(descriptor)['minecraft:texture_set'].items()
                        if channel != 'color' and isinstance(value, str))
    for path in textures:
        image = Image.open(path).copy()
        side = image.width
        expanded = Image.new(image.mode, (side, side * len(frames)))
        for index, frame in enumerate(frames):
            expanded.paste(image.crop((0, side * frame, side, side * (frame + 1))), (0, side * index))
        expanded.save(path)
    document['animation']['frames'] = list(range(len(frames)))
    write_json(metadata, document)
    for output in receipt['outputs']:
        path = Path(destination) / output['path']
        output['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()


def _material_signature(stack, source, target, particle, policy):
    """A hash of everything a compiled material depends on, to tell when the cache is stale."""
    digest = hashlib.sha256(json.dumps({'source': source, 'target': target, 'particle': particle,
                                        'policy': policy.__dict__, 'decoder': MATERIAL_DECODER_REVISION,
                                        'native_adapter_revision': 2}, sort_keys=True).encode())
    for suffix in ('', '_n', '_s'):
        path = source[:-4] + suffix + '.png'
        for candidate in (path, path + '.mcmeta'):
            digest.update(candidate.encode())
            digest.update(stack.read(candidate) if candidate in stack.files else b'absent')
    return digest.hexdigest()


def _contained_output(destination, relative):
    output = (destination / relative).resolve()
    if not output.is_relative_to(destination.resolve()):
        raise ValueError('Native material output escapes destination')
    return output


def _prepare_material(stack, source, destination, target, policy, cache, *, particle=False):
    """Compile one material into destination, or reuse the cached one while it is still current."""
    signature = _material_signature(stack, source, target, particle, policy)
    previous = cache.get(target, {})
    receipt = previous.get('receipt', {})
    if previous.get('signature') == signature and receipt.get('outputs'):
        if all(_contained_output(destination, output['path']).is_file()
               and hashlib.sha256(_contained_output(destination, output['path']).read_bytes()).hexdigest()
               == output['sha256'] for output in receipt['outputs']):
            return receipt
    # The whole family is compiled before any existing output is replaced.
    with tempfile.TemporaryDirectory(prefix='native-material-') as temporary:
        compiled = Path(temporary)
        receipt = compile_stack_material(stack, source, compiled, target, policy=policy, interpolation='native')
        if particle:
            _unfold_particle_animation(compiled, target, receipt)
        products = {output['path'] for output in receipt['outputs']}
        stale = {output['path'] for output in previous.get('receipt', {}).get('outputs', [])} - products
        # Files an earlier compile of this target may have left that this one does not write.
        color = Path(target)
        stale.update(str(color.with_suffix(suffix)).replace('\\', '/')
                     for suffix in ('.texture_set.json', '.png.mcmeta'))
        stale.update((color.parent / (color.stem + suffix)).as_posix() for suffix in ('_normal.png', '_mers.png'))
        for relative in products:
            output = _contained_output(destination, relative)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(compiled / relative, output)
        for relative in stale - products:
            output = _contained_output(destination, relative)
            if output.is_file():
                output.unlink()
    cache[target] = {'signature': signature, 'receipt': receipt}
    return receipt


def _particle_definition(row, material_root):
    """The official particle definition pointed at the author's texture, with its flipbook timing."""
    definition = copy.deepcopy(row['definition'])
    effect = definition['particle_effect']
    effect['description']['basic_render_parameters']['texture'] = row['target']
    billboard = effect['components']['minecraft:particle_appearance_billboard']
    if row['family'] == 'big_smoke':
        # Each smoke particle shows one random frame of the bank for its whole life.
        billboard['uv'] = {'texture_width': 1, 'texture_height': SMOKE_FRAMES,
                           'uv': [0, 'math.floor(variable.particle_random_2 * 12)'], 'uv_size': [1, 1]}
    else:
        metadata = Path(material_root) / (row['target'] + '.png.mcmeta')
        if metadata.is_file():
            timing = read_json(metadata)['animation']
            with Image.open(Path(material_root) / (row['target'] + '.png')) as image:
                frames = image.height // image.width
            billboard['uv'] = {'texture_width': 1, 'texture_height': frames,
                               'flipbook': {'base_UV': [0, 0], 'size_UV': [1, 1], 'step_UV': [0, 1],
                                            'frames_per_second': 20 / timing['frametime'],
                                            'max_frame': frames, 'stretch_to_lifetime': False, 'loop': True}}
        else:
            billboard['uv'] = {'texture_width': 1, 'texture_height': 1, 'uv': [0, 0], 'uv_size': [1, 1]}
    return definition


def _png_bytes(image):
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()
