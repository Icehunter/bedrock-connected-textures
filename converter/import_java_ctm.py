"""Import the OptiFine connected-texture rules of an extracted Java pack as a rules document.

Every .properties file under assets/<namespace>/optifine/ctm becomes one rule, in sorted path
order, with Java block, state and biome names mapped to Bedrock ones through explicit maps.
Anything without an exact Bedrock meaning (an unknown option or method, an unmapped name)
stops the import instead of being approximated. An import is all or nothing: compiled tiles
go to a staging folder that takes the output's name only once every rule has imported and the
whole document validates. The Continuity dialect reads the same format but leaves out the
minecraft namespace's rules marked optifineOnly.
"""
import argparse
from pathlib import Path
import re
import shutil
import tempfile

from common import read_json, write_json
from connected_build import validate, FACES, METHODS
from java_texture_paths import SPECIAL_TILES, resolve_texture

# Every property the importer understands; a rule with any other one is refused.
SUPPORTED = {'method', 'matchBlocks', 'matchTiles', 'tiles', 'faces', 'connect', 'width', 'height',
             'weights', 'weight', 'symmetry', 'randomLoops', 'linked', 'innerSeams', 'heights', 'minHeight',
             'maxHeight', 'biomes', 'orient', 'connectTiles', 'connectBlocks', 'layer', 'tintIndex', 'tintBlock',
             'optifineOnly'}
# Java block ids whose Bedrock block has another name.
BLOCK_NAMES = {'minecraft:bricks': 'minecraft:brick_block'}
DIALECTS = ('optifine', 'continuity')
# Older OptiFine method names and the methods they stand for.
METHOD_ALIASES = {'glass': 'ctm', 'bookshelf': 'horizontal', 'sandstone': 'top'}
# OptiFine's compact method: five tiles quartered into the 47 connected ones. ctm.N=M options
# draw case N with the author's tile M instead.
COMPACT_METHOD = 'ctm_compact'
COMPACT_OVERRIDE = re.compile(r'ctm\.\d+')
CONNECTED_CASES = 47
# Where the composed compact tiles are written in the compiled pack.
COMPACT_TILE_FOLDER = 'textures/compact_tiles/'
# Methods the engine adds for its own rules; Java has no such method.
ENGINE_ONLY_METHODS = ('ctm_repeat', 'horizontal_repeat')
# Properties only overlay methods use; other rules set them aside in the import accounting.
OVERLAY_ONLY_OPTIONS = ('connectTiles', 'connectBlocks', 'layer', 'tintIndex', 'tintBlock')
INTEGER_OPTIONS = ('width', 'height', 'weight', 'randomLoops')
TEXT_OPTIONS = ('connect', 'symmetry')
BOOLEAN_OPTIONS = ('linked', 'innerSeams')
# Java face names that differ from Bedrock's.
FACE_NAMES = {'top': 'up', 'bottom': 'down'}
# Bedrock blocks have no snowy state; the engine works it out from the block above.
SNOWY_STATE = {'name': 'bct:snowy', 'values': {'true': True, 'false': False}}
# A key ends at the first =, : or run of whitespace.
PROPERTY_SEPARATOR = re.compile(r'\s*[=:]\s*|\s+')
# A run of numbers such as 0-16.
NUMBER_RANGE = re.compile(r'(\d+)-(\d+)')
# A height or a height range; negative heights are bracketed, as in (-64)-(-1), so their minus
# sign is not read as the range dash.
HEIGHT_RANGE = re.compile(r'(\(-?\d+\)|\d+)(?:-(\(-?\d+\)|\d+))?')
# The world's height limits, for the older minHeight/maxHeight form.
DEFAULT_MIN_HEIGHT = -64
DEFAULT_MAX_HEIGHT = 319
# A block id without state clauses, the only form overlay connectBlocks may use.
PLAIN_BLOCK_ID = re.compile(r'[a-z0-9_]+:[a-z0-9_]+')


def import_rules(pack, base_textures, block_names=None, compiled_pack=None, biome_names=None, state_names=None,
                 material_policy=None, dialect='optifine', texture_orientations=None, double_slab_blocks=None,
                 biome_unions=None, block_state_resolver=None):
    """Validate a complete import before publishing any converted files.

    base_textures maps each Bedrock block to its Java face textures, so rules that match tiles
    can find the blocks showing them. compiled_pack, which must not exist yet, receives every
    tile the rules draw (and the composed ctm_compact tiles); converting PBR maps with
    material_policy needs it. A failed import leaves no compiled output behind.
    """
    pack = Path(pack).resolve()
    options = dict(block_names=block_names, biome_names=biome_names, state_names=state_names,
                   material_policy=material_policy, dialect=dialect, texture_orientations=texture_orientations,
                   double_slab_blocks=double_slab_blocks, biome_unions=biome_unions,
                   block_state_resolver=block_state_resolver)
    if compiled_pack is None:
        if material_policy is not None:
            raise ValueError('Material conversion requires a separate compiled output')
        return _import_rules(pack, base_textures, **options)
    destination = Path(compiled_pack).resolve()
    if destination == pack or destination.is_relative_to(pack) or pack.is_relative_to(destination):
        raise ValueError('Compiled output must be separate from author input')
    if destination.exists():
        raise ValueError('Compiled output already exists')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.ctm-import-', dir=destination.parent) as temporary:
        staging = Path(temporary) / 'pack'
        staging.mkdir()
        document = _import_rules(pack, base_textures, compiled_pack=staging, **options)
        staging.rename(destination)
    return document


def apply_block_mapping(rule, data, path, names, state_names=None, double_slab_blocks=None,
                        block_state_resolver=None):
    """Set a rule's Bedrock blocks from its matchBlocks, and an overlay rule's connectBlocks.

    names maps Java block ids to a Bedrock id or a list of ids; state_names translates Java
    states per block; double_slab_blocks names the Bedrock block of each Java double slab.
    block_state_resolver, when given, is asked first and returns None for a block it does not
    know. State conditions go to blockMatchers, which is left out when no clause has any.
    """
    if 'matchBlocks' in data:
        clauses = []
        for item in data['matchBlocks'].split():
            clauses.extend(_block_clauses(item, path, names, state_names, double_slab_blocks, block_state_resolver))
        rule['blocks'] = list(dict.fromkeys(clause['block'] for clause in clauses))
        rule.pop('blockMatchers', None)
        if any(clause['states'] for clause in clauses):
            rule['blockMatchers'] = clauses
    if rule['method'].startswith('overlay') and 'connectBlocks' in data:
        rule['connectBlocks'] = _connect_blocks(data['connectBlocks'], names, block_state_resolver)


def properties(path):
    """The properties of a Java .properties file, which may start with a byte-order mark."""
    return parse_properties(path.read_text(encoding='utf-8-sig'), str(path))


def parse_properties(text, origin='<properties>'):
    """Key/value pairs of Java .properties text, in file order.

    A line ending in a backslash continues on the next one, # and ! start comments, and a key
    ends at =, : or whitespace. Other backslash escapes are refused rather than guessed.
    origin names the text in error messages.
    """
    result = {}
    pending = ''
    for raw_line in text.splitlines():
        line = pending + raw_line.strip()
        if line.endswith('\\'):
            pending = line[:-1]
            continue
        pending = ''
        if not line or line.startswith(('#', '!')):
            continue
        parts = PROPERTY_SEPARATOR.split(line, maxsplit=1)
        if len(parts) != 2:
            raise ValueError(f'{origin}: malformed property {line}')
        if '\\' in line:
            raise ValueError(f'{origin}: escaped properties need explicit conversion')
        key, value = parts
        result[key] = value
    if pending:
        raise ValueError(f'{origin}: incomplete continuation')
    return result


def integers(value):
    """The numbers of a space-separated list in which a-b stands for every number from a to b."""
    result = []
    for item in value.split():
        match = NUMBER_RANGE.fullmatch(item)
        if match:
            result.extend(range(int(match[1]), int(match[2]) + 1))
        else:
            result.append(int(item))
    return result


def _import_rules(pack, base_textures, block_names=None, compiled_pack=None, biome_names=None, state_names=None,
                  material_policy=None, dialect='optifine', texture_orientations=None, double_slab_blocks=None,
                  biome_unions=None, block_state_resolver=None):
    """import_rules, writing into compiled_pack (the staging folder) when one is given."""
    pack = pack.resolve()
    names = {**BLOCK_NAMES, **(block_names or {})}
    if dialect not in DIALECTS:
        raise ValueError('Choose optifine or continuity dialect')
    materials = None
    if material_policy is not None:
        materials = _MaterialConversion(pack, compiled_pack.parent / 'materials', material_policy)
    paths = _rule_paths(pack)
    importer = _RuleImporter(pack=pack, dialect=dialect, block_names=names, state_names=state_names,
                             double_slab_blocks=double_slab_blocks, block_state_resolver=block_state_resolver,
                             biome_names=biome_names, biome_unions=biome_unions, compiled_pack=compiled_pack,
                             materials=materials)
    rules = []
    for ordinal, path in enumerate(paths):
        rule = importer.import_rule(ordinal, path)
        if rule is not None:
            rules.append(rule)
    document = _rules_document(base_textures, rules, dialect, len(paths), importer)
    if texture_orientations is not None:
        document['textureOrientations'] = texture_orientations
    if materials is not None and materials.receipts:
        document['materialReceipts'] = materials.receipts
    if compiled_pack is not None:
        compiled_pack = Path(compiled_pack).resolve()
        _publish_compiled_pack(pack, compiled_pack, rules, materials)
    validate(document, compiled_pack or pack)
    if any(rule.get('matchTiles') for rule in rules) and not base_textures:
        raise ValueError('Texture matching requires a Bedrock block/face to Java base-texture map')
    return document


def _rule_paths(pack):
    """Every CTM properties file of every namespace, sorted; rule ids follow this order."""
    paths = sorted(path for namespace in (pack / 'assets').iterdir() if namespace.is_dir()
                   for path in (namespace / 'optifine/ctm').rglob('*.properties'))
    if not paths:
        raise ValueError('No assets/minecraft/optifine/ctm properties found')
    return paths


def _rules_document(base_textures, rules, dialect, source_rules, importer):
    """The rules document, with the accounting of what the import left out."""
    matched = {tile for rule in rules for tile in rule.get('matchTiles', [])}
    # Blocks showing a texture that a rule matches by name; the engine tracks them like rule blocks.
    source_blocks = [block for block, faces in base_textures.items()
                     if any(tile in matched for tile in faces.values())]
    accounting = {'sourceRules': source_rules, 'importedRules': len(rules), 'excludedRules': importer.excluded,
                  'ignoredProperties': importer.ignored}
    return {'format_version': 1, 'baseTextures': base_textures, 'sourceBlocks': source_blocks, 'rules': rules,
            'dialect': dialect, 'importAccounting': accounting}


class _RuleImporter:
    """Turns one pack's properties files into rules with the name maps of one import.

    excluded and ignored collect the files and properties the import leaves out.
    """

    def __init__(self, pack, dialect, block_names, state_names, double_slab_blocks, block_state_resolver,
                 biome_names, biome_unions, compiled_pack, materials):
        self.pack = pack
        self.dialect = dialect
        self.block_names = block_names
        self.state_names = state_names
        self.double_slab_blocks = double_slab_blocks
        self.block_state_resolver = block_state_resolver
        self.biome_names = biome_names
        self.biome_unions = biome_unions
        self.compiled_pack = compiled_pack
        self.materials = materials
        self.excluded = []
        self.ignored = []

    def import_rule(self, ordinal, path):
        """The rule one properties file describes, or None when the dialect leaves the file out."""
        data = properties(path)
        filename = path.relative_to(self.pack).as_posix()
        if self._dialect_excludes(data, path):
            self.excluded.append({'filename': filename, 'reason': 'optifineOnly=true in Continuity dialect'})
            return None
        comments = [key for key in data if key.startswith('//')]
        self._set_aside(data, filename, comments, 'Unknown Java property has no engine behavior')
        _reject_unknown_options(data, path)
        method = data.get('method', 'ctm')
        method = METHOD_ALIASES.get(method, method)
        if not method.startswith('overlay'):
            overlay_options = [key for key in OVERLAY_ONLY_OPTIONS if key in data]
            self._set_aside(data, filename, overlay_options, 'Overlay-only property on a non-overlay Java rule')
        if (method not in METHODS and method != COMPACT_METHOD) or method in ENGINE_ONLY_METHODS:
            raise ValueError(f'{path}: unsupported Java method {method}')
        rule = {'id': f'java_{ordinal}', 'filename': filename, 'method': method, 'blocks': []}
        if 'orient' in data:
            rule['orient'] = data['orient'].lower()
        _match_by_file_name(data, path)
        apply_block_mapping(rule, data, path, self.block_names, self.state_names, self.double_slab_blocks,
                            self.block_state_resolver)
        for key in ('matchTiles', 'connectTiles'):
            if key in data:
                rule[key] = [resolve_texture(token, filename, True) for token in data[key].split()]
        self._add_overlay_tint(rule, data)
        rule['tiles'] = [resolve_texture(token, filename) for token in _tile_tokens(data.get('tiles', ''))]
        if self.materials is not None:
            self.materials.convert(rule['tiles'])
        if method == COMPACT_METHOD:
            self._expand_compact(rule, data)
        rule['faces'] = _faces(data.get('faces', 'all'))
        if 'biomes' in data:
            rule['biomes'] = self._biomes(data['biomes'], path)
        _add_plain_options(rule, data, path)
        if 'weights' in data:
            rule['weights'] = _weights(data['weights'], len(rule['tiles']), path)
        heights = _heights(data, path)
        if heights is not None:
            rule['heights'] = heights
        return rule

    def _dialect_excludes(self, data, path):
        """Whether a Continuity import leaves the file out: minecraft rules marked optifineOnly."""
        if data.get('optifineOnly', 'false') not in ('true', 'false'):
            raise ValueError('Invalid optifineOnly value')
        return (self.dialect == 'continuity' and data.get('optifineOnly') == 'true'
                and path.is_relative_to(self.pack / 'assets/minecraft'))

    def _set_aside(self, data, filename, keys, reason):
        """Take keys out of data, accounting for each as an ignored property."""
        for key in keys:
            self.ignored.append({'filename': filename, 'key': key, 'value': data.pop(key), 'reason': reason})

    def _add_overlay_tint(self, rule, data):
        """An overlay's render layer and the tint it borrows from another block."""
        if 'layer' in data:
            rule['layer'] = data['layer']
        if 'tintIndex' in data:
            rule['tintIndex'] = int(data['tintIndex'])
        if 'tintBlock' in data:
            block = _namespaced(data['tintBlock'])
            rule['tintBlock'] = self.block_names.get(block, block)

    def _expand_compact(self, rule, data):
        """Compose the 47 connected tiles from ctm_compact's five into the compiled pack."""
        if self.compiled_pack is None:
            raise ValueError('Compact CTM requires a separate --compiled-pack output')
        from compact_tiles import compile_materials
        replacements = {int(key[4:]): int(value) for key, value in data.items() if key.startswith('ctm.')}
        if any(not 0 <= case < CONNECTED_CASES for case in replacements):
            raise ValueError('Compact case outside 0-46')
        # Converted materials are read in place of the author's maps.
        source = self.materials.folder if self.materials is not None else self.pack
        rule['tiles'] = compile_materials(source, rule['tiles'], self.compiled_pack, rule['id'], replacements)
        rule['method'] = 'ctm'

    def _biomes(self, value, path):
        """The rule's Bedrock biome filter; a leading ! makes it an exclusion."""
        value = value.strip()
        exclude = value.startswith('!')
        tokens = (value[1:] if exclude else value).split()
        ids = []
        for union in self.biome_unions or []:
            if (not isinstance(union, dict) or set(union) != {'source', 'target'} or not union['source']
                    or not union['target']):
                raise ValueError('Invalid biome-union mapping')
            # Several Java biomes can be one Bedrock biome, which a rule names only by listing all of them.
            if set(union['source']) <= set(tokens):
                ids.extend(union['target'])
                tokens = [token for token in tokens if token not in union['source']]
        for token in tokens:
            ids.extend(self._bedrock_biomes(token, path))
        return {'ids': list(dict.fromkeys(ids)), 'exclude': exclude}

    def _bedrock_biomes(self, token, path):
        """The Bedrock biome ids one Java biome maps to."""
        mapped = (self.biome_names or {}).get(token)
        if mapped is None:
            raise ValueError(f'{path}: biome {token} requires an explicit Bedrock biome mapping')
        mapped = [mapped] if isinstance(mapped, str) else mapped
        if not isinstance(mapped, list) or not mapped:
            raise ValueError('Invalid biome mapping: ' + token)
        return mapped


class _MaterialConversion:
    """Decodes each tile's Java PBR maps once, into a folder merged into the compiled pack at the end."""

    def __init__(self, pack, folder, policy):
        from java_materials import compile_stack_material
        self._compile_material = compile_stack_material
        self.stack = _FolderStack(pack)
        self.folder = folder
        self.policy = policy
        self.converted = set()
        self.receipts = []

    def convert(self, tiles):
        """Decode the tiles not converted yet, keeping each one's receipt."""
        for tile in tiles:
            if tile in SPECIAL_TILES or tile in self.converted:
                continue
            self.receipts.append(self._compile_material(self.stack, tile, self.folder, tile, policy=self.policy))
            self.converted.add(tile)


class _FolderStack:
    """An extracted pack read through the files/read interface of a Java pack stack."""

    def __init__(self, pack):
        self._pack = pack
        self.files = {path.relative_to(pack).as_posix() for path in pack.rglob('*') if path.is_file()}

    def read(self, path):
        source = (self._pack / path).resolve()
        if not source.is_relative_to(self._pack):
            raise ValueError('Material source escapes pack')
        return source.read_bytes()


def _reject_unknown_options(data, path):
    """Stop on any option that cannot be carried over exactly; ctm.N belongs to ctm_compact."""
    compact = data.get('method') == COMPACT_METHOD
    unknown = {key for key in data
               if key not in SUPPORTED and not (compact and COMPACT_OVERRIDE.fullmatch(key))}
    if unknown:
        raise ValueError(f'{path}: unsupported Java options {sorted(unknown)}; no approximate conversion written')


def _match_by_file_name(data, path):
    """OptiFine's default when a rule names neither blocks nor tiles: its file name.

    block_<id>.properties matches that block; any other file name matches the tile of that name.
    """
    if 'matchBlocks' not in data and 'matchTiles' not in data:
        key = 'matchBlocks' if path.stem.startswith('block_') else 'matchTiles'
        data[key] = path.stem.removeprefix('block_')


def _tile_tokens(value):
    """The tiles option's tokens, with each number range such as 0-16 spelled out."""
    tokens = []
    for token in value.split():
        match = NUMBER_RANGE.fullmatch(token)
        if match:
            tokens.extend(str(number) for number in range(int(match[1]), int(match[2]) + 1))
        else:
            tokens.append(token)
    return tokens


def _faces(value):
    """Bedrock face names for a faces option, in order, each once; all and sides expand."""
    faces = []
    for face in value.split():
        if face == 'all':
            faces.extend(FACES)
        elif face == 'sides':
            faces.extend(FACES[:4])
        else:
            faces.append(FACE_NAMES.get(face, face))
    return list(dict.fromkeys(faces))


def _add_plain_options(rule, data, path):
    """Options carried over as they are: numbers, words and true/false switches."""
    for key in INTEGER_OPTIONS:
        if key in data:
            rule[key] = int(data[key])
    for key in TEXT_OPTIONS:
        if key in data:
            rule[key] = data[key]
    for key in BOOLEAN_OPTIONS:
        if key in data:
            if data[key] not in ('true', 'false'):
                raise ValueError(f'{path}: invalid {key}')
            rule[key] = data[key] == 'true'


def _weights(value, tile_count, path):
    """One random weight per tile; like OptiFine, extra weights are dropped and missing ones get the average."""
    weights = integers(value)[:tile_count]
    if not weights:
        raise ValueError(f'{path}: empty weights')
    average = sum(weights) // len(weights)
    weights.extend([average] * (tile_count - len(weights)))
    return weights


def _heights(data, path):
    """[minimum, maximum] height ranges from heights, or from minHeight/maxHeight; None without either."""
    if 'heights' in data:
        ranges = []
        for item in data['heights'].split():
            match = HEIGHT_RANGE.fullmatch(item)
            if not match:
                raise ValueError(f'{path}: invalid height range {item}')
            minimum = int(match[1].strip('()'))
            maximum = int((match[2] or match[1]).strip('()'))
            ranges.append([minimum, maximum])
        return ranges
    if 'minHeight' in data or 'maxHeight' in data:
        return [[int(data.get('minHeight', DEFAULT_MIN_HEIGHT)), int(data.get('maxHeight', DEFAULT_MAX_HEIGHT))]]
    return None


def _block_clauses(item, path, names, state_names, double_slab_blocks, block_state_resolver):
    """Clauses for one matchBlocks entry such as oak_log:axis=x,z, one per Bedrock block."""
    if item.isdecimal():
        raise ValueError(f'{path}: legacy IDs require explicit Bedrock block mapping')
    block, selection = _parse_block_matcher(item)
    resolved = block_state_resolver(block, selection) if block_state_resolver else None
    if resolved is not None:
        if not resolved:
            raise ValueError('Empty native block-state resolution: ' + item)
        return [{'block': target['block'],
                 'states': {name: [_state_text(value)] for name, value in target['states'].items()}}
                for target in resolved]
    mapped = names.get(block, block)
    states = {}
    for key, values in selection.items():
        if key == 'type' and values == ['double'] and block in (double_slab_blocks or {}):
            # A Java double slab is a block of its own on Bedrock.
            mapped = double_slab_blocks[block]
            continue
        mapping = _state_mapping(block, key, values, path, state_names)
        name = mapping['name']
        if name in states:
            raise ValueError('Duplicate translated state: ' + name)
        states[name] = [_state_text(mapping['values'][value]) for value in values]
    return [{'block': target, 'states': dict(states)} for target in _block_list(mapped)]


def _parse_block_matcher(item):
    """The block id and {state: [values]} of a Java block matcher: [namespace:]name[:state=v1,v2]..."""
    parts = item.split(':')
    first_state = next((index for index, part in enumerate(parts) if '=' in part), len(parts))
    block = _namespaced(':'.join(parts[:first_state]))
    selection = {}
    for state in parts[first_state:]:
        if '=' not in state:
            raise ValueError('Malformed Java state matcher: ' + item)
        key, values = state.split('=', 1)
        if key in selection:
            raise ValueError('Duplicate Java state: ' + key)
        selection[key] = values.split(',')
    return block, selection


def _state_mapping(block, key, values, path, state_names):
    """The {name, values} translation of one Java state, checked to cover every value asked for."""
    mapping = (state_names or {}).get(block, {}).get(key)
    if key == 'snowy':
        mapping = SNOWY_STATE
    if not mapping:
        raise ValueError(f'{path}: {block}:{key} requires explicit Bedrock state mapping')
    if not values or any(value not in mapping['values'] for value in values):
        raise ValueError('Unmapped Java state value: ' + key)
    return mapping


def _connect_blocks(value, names, block_state_resolver):
    """The Bedrock blocks an overlay rule's connectBlocks names, each once."""
    blocks = []
    for token in value.split():
        block = _namespaced(token)
        if not PLAIN_BLOCK_ID.fullmatch(block):
            raise ValueError('Overlay connectBlocks state clauses require explicit mapping')
        resolved = block_state_resolver(block, {}) if block_state_resolver else None
        if resolved is not None:
            blocks.extend(item['block'] for item in resolved)
        else:
            blocks.extend(_block_list(names.get(block, block)))
    return list(dict.fromkeys(blocks))


def _namespaced(block):
    """A Java block id with the minecraft namespace filled in when it has none."""
    return block if ':' in block else 'minecraft:' + block


def _block_list(mapped):
    """A mapped block as a list: one Java block can stand for several Bedrock blocks."""
    return mapped if isinstance(mapped, list) else [mapped]


def _state_text(value):
    """A state value as the engine compares it: text, with booleans as true/false."""
    return str(value).lower() if isinstance(value, bool) else str(value)


def _publish_compiled_pack(pack, compiled_pack, rules, materials):
    """Fill the compiled pack with the decoded materials and every other author tile the rules draw."""
    if compiled_pack == pack or compiled_pack.is_relative_to(pack):
        raise ValueError('Compiled output must be separate from author input')
    converted = set()
    if materials is not None:
        _copy_material_files(materials.folder, compiled_pack)
        converted = materials.converted
    for tile in {tile for rule in rules for tile in rule['tiles']}:
        if tile in SPECIAL_TILES or tile in converted:
            continue
        _copy_tile(pack, compiled_pack, tile)


def _copy_material_files(folder, compiled_pack):
    """Copy the decoded materials into the compiled pack; none may land on a compiled tile."""
    for source in folder.rglob('*'):
        if not source.is_file():
            continue
        destination = compiled_pack / source.relative_to(folder)
        if destination.exists():
            raise ValueError('Material output conflicts with compiled CTM')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def _copy_tile(pack, compiled_pack, tile):
    """Copy one author tile with its animation, its texture set and the set's channel images."""
    destination = (compiled_pack / tile).with_suffix('.png')
    source = (pack / tile).with_suffix('.png')
    if destination.exists():
        # The composed compact tiles are already in place.
        if tile.startswith(COMPACT_TILE_FOLDER):
            return
        # Two tile names can lead to one file, which is fine only when they agree.
        if destination.read_bytes() != source.read_bytes():
            raise ValueError('Compiled output contains a conflicting tile: ' + tile)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    _copy_animation(source, destination)
    descriptor = source.with_suffix('.texture_set.json')
    if not descriptor.exists():
        return
    shutil.copyfile(descriptor, destination.with_suffix('.texture_set.json'))
    for channel, value in read_json(descriptor)['minecraft:texture_set'].items():
        if channel == 'color' or not isinstance(value, str):
            continue
        _copy_channel_image(pack, compiled_pack, source, value)


def _copy_channel_image(pack, compiled_pack, tile_source, value):
    """Copy the image (and animation) of one texture-set channel, which must stay inside the pack."""
    # A channel named from textures/ is pack-relative; any other name sits beside the tile.
    relative = Path(value) if value.startswith('textures/') else tile_source.parent.relative_to(pack) / value
    channel_source = (pack / relative).with_suffix('.png').resolve()
    if not channel_source.is_relative_to(pack):
        raise ValueError('Material channel escapes source pack')
    channel_destination = (compiled_pack / relative).with_suffix('.png')
    channel_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(channel_source, channel_destination)
    _copy_animation(channel_source, channel_destination)


def _copy_animation(source, destination):
    """Copy a texture's Java animation sidecar (<texture>.png.mcmeta) when it has one."""
    metadata = Path(str(source) + '.mcmeta')
    if metadata.exists():
        shutil.copyfile(metadata, Path(str(destination) + '.mcmeta'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack', type=Path, required=True)
    parser.add_argument('--base-textures', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compiled-pack', type=Path)
    parser.add_argument('--biome-map', type=Path)
    parser.add_argument('--state-map', type=Path)
    parser.add_argument('--material-encoding', choices=['labpbr-1.3'])
    parser.add_argument('--normal-y', choices=['directx', 'opengl'])
    parser.add_argument('--roughness', choices=['linear', 'perceptual'])
    parser.add_argument('--allow-material-losses', action='store_true')
    args = parser.parse_args()
    policy = None
    if args.material_encoding:
        from java_materials import MaterialPolicy
        if not args.normal_y or not args.roughness:
            parser.error('Material conversion requires --normal-y and --roughness')
        policy = MaterialPolicy(args.material_encoding, args.normal_y, args.roughness, args.allow_material_losses)
    elif args.normal_y or args.roughness or args.allow_material_losses:
        parser.error('Material options require --material-encoding')
    if args.output.resolve().is_relative_to(args.pack.resolve()):
        parser.error('Rules output must be outside the author pack')
    if args.output.exists():
        parser.error('Rules output already exists')
    document = import_rules(args.pack, read_json(args.base_textures), compiled_pack=args.compiled_pack,
                            biome_names=read_json(args.biome_map) if args.biome_map else None,
                            state_names=read_json(args.state_map) if args.state_map else None,
                            material_policy=policy)
    write_json(args.output, document)
