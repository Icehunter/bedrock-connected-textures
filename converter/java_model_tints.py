"""Java block color providers and OptiFine block colormaps.

Biome colors come from the effective Java colormap images (the pack's, else
vanilla) sampled at each Java biome's climate, with the biome's own color
overrides and grass modifiers, as Java does. Only the blocks Java colors itself
and the blocks the pack's OptiFine palettes name get a tint: a model's tint
index alone never turns an ordinary block into grass or foliage.
"""
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import struct
import zipfile

from PIL import Image

from java_block_bindings import BLOCK_ALIASES
from java_block_states import native_block_java_ids
from java_environment_bindings import BIOME_NAMES, BLOCK_NAMES

GRASS_BLOCKS = ('grass_block', 'grass', 'short_grass', 'tall_grass', 'fern',
                'large_fern', 'potted_fern', 'bush', 'sugar_cane', 'reeds')
FOLIAGE_BLOCKS = ('oak_leaves', 'jungle_leaves', 'acacia_leaves', 'dark_oak_leaves',
                  'mangrove_leaves', 'vine')
# Verified against the supplied Mojang Java 26.2 BlockColors bytecode.
FIXED_VANILLA = {'spruce_leaves': 0x619961, 'birch_leaves': 0x80a755,
                 'attached_melon_stem': 0xe0c71c, 'attached_pumpkin_stem': 0xe0c71c,
                 'lily_pad': 0x208030, 'waterlily': 0x208030}
# Bedrock biomes that Java merged into one biome.
LEGACY_BIOMES = {'forest': ['forest_hills'], 'birch_forest': ['birch_forest_hills'],
                 'taiga': ['taiga_hills'], 'jungle': ['jungle_hills', 'jungle_mutated'],
                 'desert': ['desert_hills', 'desert_mutated'], 'snowy_taiga': ['cold_taiga_hills'],
                 'wooded_badlands': ['mesa_plateau', 'mesa_plateau_mutated']}
GRASS_COLORMAP = 'assets/minecraft/textures/colormap/grass.png'
FOLIAGE_COLORMAP = 'assets/minecraft/textures/colormap/foliage.png'
COLOR_PROPERTIES = 'assets/minecraft/optifine/color.properties'
BIOME_FOLDER = 'data/minecraft/worldgen/biome/'
COLORMAP_SIZE = (256, 256)
BLOCK_ID = re.compile(r'[a-z0-9_.-]+:[a-z0-9_/.-]+')
# Java's grass color modifiers: dark forest averages the color with this one;
# swamp grass switches between two colors by coordinate noise.
DARK_FOREST_BLEND = 0x28340a
SWAMP_GRASS = 0x6a7039
SWAMP_GRASS_BELOW = 0x4c763c
CLIENT_BIOME_VERSION = '1.21.40'


def resolve_model_tints(stack, vanilla_jar, bindings, environment=None, samples=None):
    """Return portable color-provider metadata; do not write source resources.

    ``stack`` is the effective PackStack. The result can be merged into the
    runtime rule document. ``samples`` is accepted for caller compatibility;
    Bedrock climate and colors are deliberately not used as Java substitutes.
    """
    environment = environment or {}
    issues, limitations, evidence = [], [], []
    types = _vanilla_tint_types()
    # Index 0 is blank for these models; index 1 is the vegetation color.
    indexed = {'minecraft:pink_petals': {'0': [1, 1, 1], '1': 'grass'},
               'minecraft:wildflowers': {'0': [1, 1, 1], '1': 'grass'}}
    aliases = _bedrock_aliases(environment)
    with zipfile.ZipFile(vanilla_jar) as vanilla:
        colormaps = _Colormaps(stack, vanilla)
        biomes = _java_biomes(vanilla)
        grass, modifiers = _biome_table(colormaps.read(GRASS_COLORMAP), biomes, environment, 'grass')
        foliage, _ = _biome_table(colormaps.read(FOLIAGE_COLORMAP), biomes, environment, 'foliage')
        global_properties = (_parse_properties(stack.read(COLOR_PROPERTIES), COLOR_PROPERTIES, issues)
                             if COLOR_PROPERTIES in stack.files else {})
        if 'lilypad' in global_properties:
            color = _normalized_rgb(global_properties['lilypad'])
            types['minecraft:lily_pad'] = types['minecraft:waterlily'] = color
        custom, custom_blocks = {}, set()
        for origin, properties in _palettes(stack, global_properties, issues):
            mode = properties.get('format', 'vanilla')
            try:
                if mode == 'fixed':
                    tint, source_path = _normalized_rgb(properties.get('color', 'ffffff')), None
                elif mode == 'vanilla':
                    source_path = _colormap_path(properties['source'], origin.split('#')[0])
                    tint = 'custom:' + hashlib.sha256(source_path.encode()).hexdigest()[:16]
                    custom[tint], _ = _biome_table(colormaps.read(source_path), biomes, environment)
                    reason = 'custom_palette_height_temperature_and_biome_blending_require_runtime_sampling'
                    limitations.append({'source': origin, 'reason': reason})
                else:
                    issues.append({'source': origin, 'reason': 'unsupported_colormap_format', 'format': mode})
                    continue
                selected = _tint_palette_blocks(origin, properties, tint, aliases, types, custom_blocks, issues)
                evidence.append({'source': origin, 'format': mode, 'image': source_path,
                                 'blocks': selected, 'provider': tint})
            except (KeyError, ValueError, OSError) as error:
                issues.append({'source': origin, 'reason': str(error)})

    # Bedrock blocks take the tint of the Java block they stand for.
    for java, targets in aliases.items():
        if java in types:
            for target in targets:
                types.setdefault(target, types[java])
    if modifiers:
        limitations.append({'reason': 'swamp_grass_uses_coordinate_noise', 'biomes': sorted(modifiers)})
    limitations.append({'reason': 'biome_boundary_blending_is_not_encoded_in_per_biome_tables'})
    cube_ids = set(bindings.get('fullCubeBlocks', []))
    return {'modelTintTypes': types, 'modelTintIndices': indexed, 'grassTints': grass,
            'foliageTints': foliage, 'customTints': custom, 'grassTintModifiers': modifiers,
            'customTintBlocks': sorted(custom_blocks & cube_ids),
            'tintSourceImages': colormaps.sources,
            'tintAudit': {'source': 'effective_java_pack_and_mojang_java_26.2_reference',
                          'custom_palettes': evidence, 'unsupported': issues, 'limitations': limitations,
                          'model_tint_index_implies_grass': False}}


def copy_model_tint_images(stack, vanilla_jar, destination, metadata):
    """Copy effective source maps for packaged provenance and future sampling."""
    destination = Path(destination)
    outputs = []
    with zipfile.ZipFile(vanilla_jar) as vanilla:
        for path, source in metadata['tintSourceImages'].items():
            data = stack.read(path) if path in stack.files else vanilla.read(path)
            if hashlib.sha256(data).hexdigest() != source['sha256']:
                raise ValueError('Colormap changed after tint resolution: ' + path)
            output = destination / path.removeprefix('assets/minecraft/')
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
            outputs.append(str(output))
    return outputs


def apply_native_biome_tints(resource_pack, bindings):
    """Bind resolved Java RGB colors to native Bedrock biome appearance.

    Every biome with a resolved grass or foliage color gets those colors in its
    client biome file: the pack's own file when it has one (everything else in
    it stays), otherwise a new file. Every table entry is checked before any
    file is written.
    """
    resource_pack = Path(resource_pack).resolve()
    colors, counts = _native_biome_colors(bindings)
    existing = _existing_biome_files(resource_pack, colors)
    prepared = [_biome_document(resource_pack, identifier, components, existing)
                for identifier, components in sorted(colors.items())]
    changed = []
    for path, document in prepared:
        if not path.exists() or json.loads(path.read_text(encoding='utf-8-sig')) != document:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(document, indent=2) + '\n', encoding='utf-8')
            changed.append(path.relative_to(resource_pack).as_posix())
    limitations = []
    modifiers = sorted(set(bindings.get('grassTintModifiers', {})) & set(colors))
    if modifiers:
        limitations.append({'reason': 'native_fixed_colors_do_not_reproduce_java_swamp_coordinate_noise',
                            'biomes': modifiers})
    if colors:
        limitations.append({'reason': 'native_biome_boundary_interpolation_can_differ_from_java'})
    return {'biomes': len(colors), 'grass_colors': counts['grassTints'],
            'foliage_colors': counts['foliageTints'], 'changed_files': changed,
            'source': 'resolved_java_per_biome_tint_tables', 'albedo_modified': False,
            'native_particle_color_modified': False, 'limitations': limitations}


class _Colormaps:
    """Reads 256 x 256 colormaps from the pack stack, else the vanilla jar, and records each one read."""

    def __init__(self, stack, vanilla):
        self.stack = stack
        self.vanilla = vanilla
        self.sources = {}

    def read(self, path):
        in_pack = path in self.stack.files
        data = self.stack.read(path) if in_pack else self.vanilla.read(path)
        decoded = Image.open(io.BytesIO(data)).convert('RGB')
        if decoded.size != COLORMAP_SIZE:
            raise ValueError('Vanilla-format colormap must be 256 by 256: ' + path)
        self.sources[path] = {'sha256': hashlib.sha256(data).hexdigest(),
                              'origin': 'pack_stack' if in_pack else 'vanilla_reference'}
        return decoded


def _vanilla_tint_types():
    """{block: provider} for the blocks Java colors itself: 'grass', 'foliage' or a fixed RGB color."""
    types = {'minecraft:' + name: 'grass' for name in GRASS_BLOCKS}
    types.update({'minecraft:' + name: 'foliage' for name in FOLIAGE_BLOCKS})
    types.update({'minecraft:' + name: _normalized_rgb(color) for name, color in FIXED_VANILLA.items()})
    return types


def _bedrock_aliases(environment):
    """{Java block id: the Bedrock block ids that stand for it}."""
    aliases = {}
    for native, java in native_block_java_ids().items():
        aliases.setdefault(java, set()).add(native)
    for native, java in BLOCK_ALIASES.items():
        aliases.setdefault('minecraft:' + java, set()).add('minecraft:' + native)
    for java, native in {**BLOCK_NAMES, **environment.get('blockNames', {})}.items():
        # One Java block can be several Bedrock blocks (block_ids.JAVA_RENAMES).
        for name in native if isinstance(native, list) else [native]:
            aliases.setdefault(java, set()).add(name)
    return aliases


def _java_biomes(vanilla):
    """{biome name: Java biome definition} from the reference jar's worldgen data."""
    paths = sorted(path for path in vanilla.namelist() if path.startswith(BIOME_FOLDER) and path.endswith('.json'))
    return {PurePosixPath(path).stem: json.loads(vanilla.read(path)) for path in paths}


def _biome_table(colormap, biomes, environment, kind=None):
    """({Bedrock biome id: RGB}, {Bedrock biome id: swamp modifier}) for one colormap.

    kind 'grass' or 'foliage' applies the biome's own color override and, for
    grass, its color modifier; a custom palette (kind None) uses the colormap only.
    """
    colors, modifiers = {}, {}
    for name, biome in biomes.items():
        value = _packed_rgb(_colormap_color(colormap, biome['temperature'], biome['downfall']))
        effects = biome.get('effects', {})
        if kind and kind + '_color' in effects:
            override = effects[kind + '_color']
            value = int(override.lstrip('#'), 16) if isinstance(override, str) else override
        modifier = effects.get('grass_color_modifier') if kind == 'grass' else None
        if modifier == 'dark_forest':
            value = ((value & 0xfefefe) + DARK_FOREST_BLEND) >> 1
        elif modifier == 'swamp':
            # The table carries the normal branch. The separate
            # modifier preserves Java's coordinate-dependent rule.
            value = SWAMP_GRASS
        for target in _biome_targets(name, environment):
            colors[target] = _normalized_rgb(value)
            if modifier == 'swamp':
                modifiers[target] = {'type': 'swamp', 'scale': 0.0225, 'threshold': -0.1,
                                     'below': _normalized_rgb(SWAMP_GRASS_BELOW), 'above': _normalized_rgb(SWAMP_GRASS)}
    return colors, modifiers


def _biome_targets(name, environment):
    """The Bedrock biome ids a Java biome stands for."""
    targets = {'minecraft:' + name}
    targets.update('minecraft:' + target for target in BIOME_NAMES.get(name, []))
    targets.update('minecraft:' + target for target in LEGACY_BIOMES.get(name, []))
    for key in (name, 'minecraft:' + name):
        targets.update(environment.get('biomeNames', {}).get(key, []))
    return targets


def _colormap_color(colormap, temperature, downfall):
    """Java's colormap lookup: the pixel at the biome's clamped temperature and downfall x temperature."""
    # JSON climate fields are Java floats before promotion to doubles.
    temperature, downfall = (struct.unpack('<f', struct.pack('<f', value))[0] for value in (temperature, downfall))
    temperature = max(0.0, min(1.0, temperature))
    downfall = max(0.0, min(1.0, downfall)) * temperature
    return colormap.getpixel((int((1 - temperature) * 255), int((1 - downfall) * 255)))[:3]


def _palettes(stack, global_properties, issues):
    """[(origin, properties)] for every OptiFine block palette.

    First the palette.block.* entries of color.properties (with the palette
    image's own .properties beside it), then each colormap/blocks/*.properties.
    """
    palette_format = global_properties.get('palette.format', 'vanilla')
    palettes = []
    for key, value in global_properties.items():
        if key.startswith('palette.block.'):
            path = _colormap_path(key.removeprefix('palette.block.'), COLOR_PROPERTIES)
            sibling = path.removesuffix('.png') + '.properties'
            properties = {'blocks': value, 'source': path, 'format': palette_format}
            if sibling in stack.files:
                properties.update(_parse_properties(stack.read(sibling), sibling, issues))
            palettes.append((COLOR_PROPERTIES + '#' + key, properties))
    for path in sorted(stack.files):
        if '/optifine/colormap/blocks/' not in path or not path.endswith('.properties'):
            continue
        properties = _parse_properties(stack.read(path), path, issues)
        properties.setdefault('blocks', PurePosixPath(path).stem)
        properties.setdefault('format', palette_format)
        properties.setdefault('source', str(PurePosixPath(path).with_suffix('.png')))
        palettes.append((path, properties))
    return palettes


def _tint_palette_blocks(origin, properties, tint, aliases, types, custom_blocks, issues):
    """Gives the palette's blocks (and the Bedrock blocks they stand for) its tint; returns the blocks."""
    selected = []
    for token in properties.get('blocks', '').split():
        if '=' in token or token.isdecimal():
            # Block states and numeric ids need a selector the per-block tint tables do not have.
            issues.append({'source': origin, 'block': token, 'reason': 'state_or_legacy_id_colormap_requires_selector'})
            continue
        block = token if ':' in token else 'minecraft:' + token
        if not BLOCK_ID.fullmatch(block):
            raise ValueError('Invalid colormap block selector: ' + token)
        for target in {block} | aliases.get(block, set()):
            types[target] = tint
            custom_blocks.add(target)
        selected.append(block)
    return selected


def _parse_properties(data, origin, issues):
    """A Java .properties file as {key: value}; lines it cannot read are reported in issues."""
    result = {}
    pending = ''
    for line_number, line in enumerate(data.decode('utf-8-sig').splitlines(), 1):
        line = pending + line.strip()
        if line.endswith('\\'):
            pending = line[:-1]
            continue
        pending = ''
        if not line or line.startswith(('#', '!')):
            continue
        # A bare key is a legal Java property with an empty value. Some packs'
        # color.properties contain their own filename as such a key.
        match = re.match(r'([^=\s]+)\s*=\s*(.*)$', line)
        if match:
            key, value = match.groups()
        elif not any(char.isspace() for char in line):
            key, value = line, ''
        else:
            issues.append({'source': origin, 'line': line_number, 'reason': 'unrecognized_property_syntax'})
            continue
        result[key] = value
    if pending:
        issues.append({'source': origin, 'reason': 'unfinished_property_continuation'})
    return result


def _colormap_path(value, properties_path):
    """The pack path of a colormap image named in a .properties file (OptiFine's ~/ and relative forms)."""
    if value.startswith('~/'):
        path = 'assets/minecraft/optifine/' + value[2:]
    elif value.startswith('assets/'):
        path = value
    elif value.startswith('/'):
        path = 'assets/minecraft/' + value.lstrip('/')
    else:
        path = str(PurePosixPath(properties_path).parent / value)
    if '..' in PurePosixPath(path).parts or not path.startswith('assets/'):
        raise ValueError('Unsafe custom colormap path: ' + value)
    return path if path.endswith('.png') else path + '.png'


def _normalized_rgb(value):
    """[r, g, b] from 0 to 1 for a packed integer or an "rrggbb" / "#rrggbb" string."""
    if isinstance(value, str):
        if not re.fullmatch(r'#?[0-9a-fA-F]{6}', value):
            raise ValueError('Invalid RGB color: ' + value)
        value = int(value.lstrip('#'), 16)
    return [round(((value >> shift) & 255) / 255, 6) for shift in (16, 8, 0)]


def _packed_rgb(color):
    return (color[0] << 16) | (color[1] << 8) | color[2]


def _native_biome_colors(bindings):
    """({biome id: {appearance component: '#RRGGBB'}}, {table: entries}) from the grass and foliage tables."""
    colors, counts = {}, {}
    for field, component in (('grassTints', 'minecraft:grass_appearance'),
                             ('foliageTints', 'minecraft:foliage_appearance')):
        table = bindings.get(field, {})
        counts[field] = len(table)
        for identifier, channels in table.items():
            if not _safe_biome_identifier(identifier):
                raise ValueError('Invalid native biome identifier: ' + str(identifier))
            if (not isinstance(channels, (list, tuple)) or len(channels) != 3
                    or any(type(value) not in (int, float) or not math.isfinite(value)
                           or not 0 <= value <= 1 for value in channels)):
                raise ValueError('Expected normalized RGB tint for ' + identifier)
            colors.setdefault(identifier, {})[component] = '#' + ''.join(
                f'{round(value * 255):02X}' for value in channels)
    return colors, counts


def _safe_biome_identifier(identifier):
    """A namespaced id that cannot name a path outside the biomes folder."""
    return (isinstance(identifier, str) and BLOCK_ID.fullmatch(identifier)
            and identifier.split(':')[0] not in ('.', '..')
            and not any(part in ('.', '..') for part in identifier.split(':')[1].split('/')))


def _existing_biome_files(resource_pack, colors):
    """{biome id: (path, document)} of the pack's client biome files for the biomes that get colors."""
    existing = {}
    for path in sorted((resource_pack / 'biomes').rglob('*.json')):
        document = json.loads(path.read_text(encoding='utf-8-sig'))
        biome = document.get('minecraft:client_biome', {})
        identifier = biome.get('description', {}).get('identifier')
        if isinstance(identifier, str) and ':' not in identifier:
            identifier = 'minecraft:' + identifier
        if identifier in colors:
            if identifier in existing:
                raise ValueError('Duplicate native biome definition: ' + identifier)
            existing[identifier] = (path, document)
    return existing


def _biome_document(resource_pack, identifier, components, existing):
    """(path, document) of a biome's client biome file with the colors set, the pack's own file when it has one."""
    if identifier in existing:
        path, document = existing[identifier]
    else:
        namespace, name = identifier.split(':')
        relative = name if namespace == 'minecraft' else namespace + '/' + name
        path = resource_pack / 'biomes' / (relative + '.client_biome.json')
        if path.exists():
            raise ValueError('Native biome output already has another identifier: ' + str(path))
        document = {'format_version': CLIENT_BIOME_VERSION, 'minecraft:client_biome': {
            'description': {'identifier': identifier}, 'components': {}}}
    if not path.resolve().is_relative_to(resource_pack / 'biomes'):
        raise ValueError('Native biome output escapes resource pack: ' + str(path))
    version = document.get('format_version', CLIENT_BIOME_VERSION)
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Invalid client biome format version: ' + str(version))
    # The appearance components need client biome format 1.21.40 or later.
    if tuple(map(int, version.split('.'))) < (1, 21, 40):
        document['format_version'] = CLIENT_BIOME_VERSION
    document.setdefault('format_version', CLIENT_BIOME_VERSION)
    target = document['minecraft:client_biome'].setdefault('components', {})
    for component, color in components.items():
        target.setdefault(component, {})['color'] = color
    return path, document
