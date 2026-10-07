"""Checks generated Bedrock JSON against Mojang's published schemas.

bedrock-samples ships JSON schemas under metadata/json_schemas. Of the files
the converter writes, block definitions (server/block/1.26.20), block culling
(client/block/1.21.80), entities (server/entity/1.26.30) and client biomes
(client/biome/1.21.130) have one. The game drops a whole block when any part
of it fails, so the converter checks every block it writes and stops instead of
shipping blocks that never load; addon_check() goes through every JSON file of
a finished add-on and lists the kinds that have no published schema.

Each file is checked with the newest schema version not newer than its
format_version. Integer and boolean state arrays ("bct:x": [0, 1, 2]) are a
documented block state form the game reads; the published 1.26.20 schema
lists only strings, so those arrays are checked here (1 to 16 distinct values
of one type) instead.

The schemas leave a few game rules out, which are checked here as well:
component names (documented minecraft: components, plus custom components a
script registers; "tag:" components are gone since format 1.26.20), the
query.block_state()-only Molang in permutation conditions and bone_visibility,
16 values per state, one render method per block, rotations in steps of 90
degrees, and bone_visibility in a permutation needing one in the base
components. Block geometry has no published schema; geometry_errors() checks
the structure the game reads and Bedrock's 30 pixel size limit.
"""
import io
import json
import math
from pathlib import Path
import re
from urllib.parse import unquote
import zipfile

from java_block_geometry import _rotate, fits

BLOCK_SCHEMA = 'server/block/1.26.20/Blocks.json'
COMPONENTS_SCHEMA = 'server/block/1.26.20/Components.json'
CULLING_SCHEMA = 'client/block/1.21.80/Culling.json'
# Documents checked with the schema version that matches their format_version (folder, file).
ENTITY_SCHEMA = ('server/entity', 'ActorDocument.json')
CLIENT_BIOME_SCHEMA = ('client/biome', 'Client Biome Document.json')
# Custom block components the engine registers (engine/repeat-blocks.mjs, engine/leaves.mjs, engine/replacement.mjs).
ENGINE_COMPONENTS = ('bct:update_repeat', 'bct:leaf')
STATE_QUERY = re.compile(r"\b(?:q|query)\.([a-z_]+)")
GEOMETRY_FACES = ('north', 'south', 'east', 'west', 'up', 'down')
# A block state has 1 to this many values.
MAX_STATE_VALUES = 16
# Problems require_valid lists before it stops counting.
SHOWN_PROBLEMS = 40


class Schemas:
    """Loads Mojang's JSON schemas and validates instances (the draft-07 keywords they use)."""

    def __init__(self, samples):
        self.root = Path(samples) / 'metadata/json_schemas'
        if not self.root.is_dir():
            raise ValueError('Mojang JSON schemas are missing: ' + str(self.root))
        self.cache = {}

    def load(self, path):
        path = Path(path).resolve()
        if path not in self.cache:
            self.cache[path] = json.loads(path.read_text(encoding='utf-8'))
        return self.cache[path]

    def validate(self, instance, relative):
        """Error strings ('path: message') for an instance of the schema at a path relative to the schema root."""
        path = self.root / relative
        errors = []
        self._check(instance, self.load(path), path, '', errors)
        return errors

    def validate_file(self, instance, path):
        """Error strings for an instance of the schema in a file of its own (BCT's docs/bct.schema.json)."""
        path = Path(path).resolve()
        errors = []
        self._check(instance, self.load(path), path, '', errors)
        return errors

    def component_names(self):
        return set(self.load(self.root / COMPONENTS_SCHEMA)['properties'])

    def versioned(self, folder, name, format_version):
        """Relative path of the newest schema version of a document not newer than format_version (else the oldest)."""
        versions = sorted((_version(item.name), item.name) for item in (self.root / folder).iterdir()
                          if item.is_dir() and _version(item.name) and (item / name).is_file())
        if not versions:
            raise ValueError(f'no schema for {folder}/{name}')
        if isinstance(format_version, str) and _version(format_version):
            wanted = _version(format_version)
        else:
            wanted = versions[-1][0]
        older = [version for version in versions if version[0] <= wanted]
        return f'{folder}/{(older[-1] if older else versions[0])[1]}/{name}'

    def _resolve(self, reference, base):
        """The schema a $ref points at, and the file it is in."""
        file, _, fragment = reference.partition('#')
        target = (base.parent / unquote(file)).resolve() if file else base
        schema = self.load(target)
        for part in [part for part in fragment.split('/') if part]:
            schema = schema[unquote(part).replace('~1', '/').replace('~0', '~')]
        return schema, target

    def _check(self, value, schema, base, where, errors):
        if schema is True or not isinstance(schema, dict):
            return
        if '$ref' in schema:
            target, path = self._resolve(schema['$ref'], base)
            self._check(value, target, path, where, errors)
        pointer = where or '/'
        kinds = schema.get('type')
        if kinds is not None:
            kinds = kinds if isinstance(kinds, list) else [kinds]
            if not any(_is_type(value, kind) for kind in kinds):
                errors.append(f'{pointer}: expected {"/".join(kinds)}, found {type(value).__name__} {_short(value)}')
                return
        if 'enum' in schema and value not in schema['enum']:
            errors.append(f'{pointer}: {_short(value)} is not one of {schema["enum"]}')
        if 'const' in schema and value != schema['const']:
            errors.append(f'{pointer}: expected {schema["const"]!r}')
        if 'oneOf' in schema:
            self._check_one_of(value, schema['oneOf'], base, where, errors)
        if _is_type(value, 'number'):
            _check_number(value, schema, pointer, errors)
        elif isinstance(value, str):
            _check_string(value, schema, pointer, errors)
        elif isinstance(value, list):
            self._check_array(value, schema, base, where, errors)
        elif isinstance(value, dict):
            self._check_object(value, schema, base, where, errors)

    def _check_one_of(self, value, options, base, where, errors):
        pointer = where or '/'
        passed, failures = 0, []
        for option in options:
            found = []
            self._check(value, option, base, where, found)
            if found:
                failures.append(found)
            else:
                passed += 1
        if passed == 0:
            closest = min(failures, key=len)
            errors.append(f'{pointer}: matches none of the allowed forms ({"; ".join(closest[:3])})')
        elif passed > 1:
            errors.append(f'{pointer}: matches more than one allowed form')

    def _check_array(self, value, schema, base, where, errors):
        pointer = where or '/'
        if 'minItems' in schema and len(value) < schema['minItems']:
            errors.append(f'{pointer}: fewer than {schema["minItems"]} items')
        if 'maxItems' in schema and len(value) > schema['maxItems']:
            errors.append(f'{pointer}: more than {schema["maxItems"]} items')
        if schema.get('x-unique-values') and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            errors.append(f'{pointer}: values repeat')
        items = schema.get('items')
        if isinstance(items, dict):
            for index, item in enumerate(value):
                self._check(item, items, base, f'{where}[{index}]', errors)
        elif isinstance(items, list):
            for index, (item, option) in enumerate(zip(value, items)):
                self._check(item, option, base, f'{where}[{index}]', errors)

    def _check_object(self, value, schema, base, where, errors):
        pointer = where or '/'
        for name in schema.get('required', []):
            if name not in value:
                errors.append(f'{pointer}: missing {name!r}')
        if 'maxProperties' in schema and len(value) > schema['maxProperties']:
            errors.append(f'{pointer}: {len(value)} entries, more than the limit of {schema["maxProperties"]}')
        if 'minProperties' in schema and len(value) < schema['minProperties']:
            errors.append(f'{pointer}: fewer than {schema["minProperties"]} entries')
        names = schema.get('propertyNames')
        properties = schema.get('properties', {})
        patterns = schema.get('patternProperties', {})
        additional = schema.get('additionalProperties', True)
        for name, item in value.items():
            if names is not None:
                self._check(name, names, base, f'{where}/{name}(name)', errors)
            child = f'{where}/{name}'
            if name in properties:
                self._check(item, properties[name], base, child, errors)
                continue
            matched = [option for pattern, option in patterns.items() if re.search(pattern, name)]
            for option in matched:
                self._check(item, option, base, child, errors)
            if matched:
                continue
            if additional is False:
                errors.append(f'{child}: not allowed here')
            elif isinstance(additional, dict):
                self._check(item, additional, base, child, errors)


def molang_queries(expression):
    """Query names an expression uses (block_state for q.block_state('x'))."""
    return set(STATE_QUERY.findall(expression if isinstance(expression, str) else ''))


def block_errors(document, schemas, custom_components=ENGINE_COMPONENTS):
    """Problems the game reports for a block definition file, or [] when it loads."""
    errors = []
    if not isinstance(document, dict) or not isinstance(document.get('format_version'), str):
        return ['format_version must be a version string']
    body = document.get('minecraft:block')
    if not isinstance(body, dict):
        return ['minecraft:block is missing']
    body = _with_typed_state_arrays_as_strings(body, errors)
    errors += schemas.validate(body, BLOCK_SCHEMA)
    known = schemas.component_names()
    parts = [('components', body.get('components', {}))]
    parts += [(f'permutations[{index}].components', item.get('components', {}))
              for index, item in enumerate(body.get('permutations', []))]
    for where, components in parts:
        errors += _component_errors(where, components, known, custom_components, document['format_version'])
    for index, item in enumerate(body.get('permutations', [])):
        if molang_queries(item.get('condition', '')) - {'block_state'}:
            errors.append(f'permutations[{index}].condition: only query.block_state() is allowed')
        geometry = item.get('components', {}).get('minecraft:geometry')
        base = body.get('components', {}).get('minecraft:geometry')
        if (isinstance(geometry, dict) and 'bone_visibility' in geometry
                and not (isinstance(base, dict) and 'bone_visibility' in base)):
            errors.append(f'permutations[{index}]: bone_visibility in a permutation needs bone_visibility '
                          'in the base geometry')
    for name, values in (body.get('description', {}).get('states') or {}).items():
        count = _state_value_count(values)
        if not 1 <= count <= MAX_STATE_VALUES:
            errors.append(f'description/states/{name}: {count} values (1 to 16 allowed)')
    return errors


def culling_errors(document, schemas):
    if not isinstance(document, dict) or not isinstance(document.get('format_version'), str):
        return ['format_version must be a version string']
    body = document.get('minecraft:block_culling_rules')
    if body is None:
        return ['minecraft:block_culling_rules is missing']
    return schemas.validate(body, CULLING_SCHEMA)


def geometry_errors(document, limit=True):
    """Structure the game reads from block geometry, and Bedrock's 30 pixel limit for blocks (when limit is set)."""
    errors = []
    if not isinstance(document, dict) or not isinstance(document.get('format_version'), str):
        return ['format_version must be a version string']
    models = document.get('minecraft:geometry')
    if not isinstance(models, list) or not models:
        return ['minecraft:geometry must be a non-empty list']
    for model in models:
        identifier = (model.get('description') or {}).get('identifier', '')
        if not re.fullmatch(r'geometry\.[A-Za-z0-9_.:-]+', identifier):
            errors.append(f'{identifier or "?"}: identifier must start with "geometry."')
        names, points = set(), []
        for bone in model.get('bones', []):
            name = bone.get('name')
            if not name or name in names:
                errors.append(f'{identifier}: bone name {name!r} is missing or repeats')
            names.add(name)
            for index, cube in enumerate(bone.get('cubes', [])):
                if len(cube.get('origin', [])) != 3 or len(cube.get('size', [])) != 3:
                    errors.append(f'{identifier}/{name}/cubes[{index}]: origin and size need three numbers')
                    continue
                uv_map = cube.get('uv')
                if isinstance(uv_map, list):
                    if len(uv_map) != 2:
                        errors.append(f'{identifier}/{name}/cubes[{index}]: box uv needs two numbers')
                    uv_map = {}  # box UV: the whole texture is laid out from one corner
                for face, uv in (uv_map or {}).items():
                    if face not in GEOMETRY_FACES:
                        errors.append(f'{identifier}/{name}/cubes[{index}]: unknown face {face!r}')
                    elif uv.get('material_instance') == '*':
                        errors.append(f'{identifier}/{name}/cubes[{index}]/{face}: '
                                      '"*" is not a material instance name in geometry')
                points += _cube_points(cube, bone)
        if limit and points and not fits([[point[0], point[1], point[2]] for point in points]):
            errors.append(f'{identifier}: geometry is larger than the 30 pixel block limit')
    return errors


def check_tree(bp=None, rp=None, samples=None, custom_components=ENGINE_COMPONENTS, schemas=None):
    """Every problem in the block, culling and geometry files of a behavior/resource pack pair."""
    schemas = schemas or Schemas(samples)
    problems = []
    blocks, geometries, cullings = {}, {}, set()
    if rp:
        rp = Path(rp)
        for path in sorted(rp.glob('models/blocks/**/*.json')):
            document = _read(path, problems)
            if document is None:
                continue
            problems += [f'{path.relative_to(rp).as_posix()}: {error}' for error in geometry_errors(document)]
            geometries.update(_bone_names(document.get('minecraft:geometry', [])))
        for path in sorted(rp.glob('block_culling/**/*.json')):
            document = _read(path, problems)
            if document is None:
                continue
            problems += [f'{path.relative_to(rp).as_posix()}: {error}' for error in culling_errors(document, schemas)]
            cullings.add(_culling_identifier(document.get('minecraft:block_culling_rules')))
    if bp:
        bp = Path(bp)
        for path in sorted(bp.glob('blocks/**/*.json')):
            document = _read(path, problems)
            if document is None:
                continue
            name = path.relative_to(bp).as_posix()
            problems += [f'{name}: {error}' for error in block_errors(document, schemas, custom_components)]
            body = document.get('minecraft:block') or {}
            blocks[(body.get('description') or {}).get('identifier')] = body
            if rp:
                problems += [f'{name}: {error}' for error in _references(body, geometries, cullings)]
            loot = (body.get('components') or {}).get('minecraft:loot')
            if isinstance(loot, str) and not (bp / loot).is_file():
                problems.append(f'{name}: loot table {loot} is missing')
    if rp and bp:
        sounds = Path(rp) / 'blocks.json'
        if sounds.is_file():
            for key in _read(sounds, problems) or {}:
                if ':' in key and not key.startswith('minecraft:') and key not in blocks:
                    problems.append(f'blocks.json: {key} is not defined by the behavior pack')
    return problems


def addon_check(path, samples, custom_components=ENGINE_COMPONENTS):
    """Every JSON file of a built add-on, checked against Mojang's schemas where one exists.

    Returns {'checked': {kind: files}, 'without_schema': {kind: files},
    'problems': ['file: problem']}. Kinds without a published schema (client
    entities, render controllers, loot tables, texture lists...) are only parsed.
    """
    schemas = Schemas(samples)
    files = _addon_files(path)
    roots = _pack_roots(files)
    checked, unchecked, problems = {}, {}, []
    blocks, geometries, cullings = {}, {}, set()
    for name in sorted(files):
        if not name.endswith('.json'):
            continue
        root = max((root for root in roots if name.startswith(root)), key=len, default=None)
        pack = roots.get(root, 'other')
        relative = name[len(root or ''):]
        if pack == 'rp' and relative.startswith('subpacks/') and relative.count('/') >= 2:
            # A subpack holds the same folders as its resource pack.
            relative = relative.split('/', 2)[2]
        try:
            document = json.loads(_strip_comments(files[name].decode('utf-8-sig')))
        except (UnicodeDecodeError, ValueError) as error:
            problems.append(f'{name}: not valid JSON ({error})')
            continue
        if relative == 'manifest.json':
            unchecked['manifest'] = unchecked.get('manifest', 0) + 1
            continue
        if pack == 'bp' and relative.startswith('blocks/'):
            kind = 'block'
            found = block_errors(document, schemas, custom_components)
            body = document.get('minecraft:block') if isinstance(document, dict) else None
            if isinstance(body, dict):
                blocks[name] = body
        elif pack == 'bp' and relative.startswith('entities/'):
            kind = 'entity'
            found = _entity_errors(document, schemas)
        elif pack == 'rp' and relative.startswith('block_culling/'):
            kind = 'block culling'
            found = culling_errors(document, schemas)
            body = document.get('minecraft:block_culling_rules') if isinstance(document, dict) else None
            cullings.add(_culling_identifier(body))
        elif (pack == 'rp' and relative.startswith('models/') and isinstance(document, dict)
              and 'minecraft:geometry' in document):
            block_geometry = relative.startswith('models/blocks/')
            kind = 'block geometry' if block_geometry else 'entity geometry'
            # The 30 pixel limit holds for block geometry only.
            found = geometry_errors(document, limit=block_geometry)
            geometries.update(_bone_names(model for model in document.get('minecraft:geometry', [])
                                          if isinstance(model, dict)))
        elif pack == 'rp' and relative.endswith('.client_biome.json'):
            kind = 'client biome'
            version = document.get('format_version') if isinstance(document, dict) else None
            found = schemas.validate(document, schemas.versioned(*CLIENT_BIOME_SCHEMA, version))
        else:
            folder = 'texture set' if relative.endswith('.texture_set.json') else relative.split('/', 1)[0]
            unchecked[f'{pack} {folder}'] = unchecked.get(f'{pack} {folder}', 0) + 1
            continue
        checked[kind] = checked.get(kind, 0) + 1
        problems += [f'{name}: {error}' for error in found]
    for name, body in blocks.items():
        problems += [f'{name}: {error}' for error in _references(body, geometries, cullings)]
    return {'checked': dict(sorted(checked.items())), 'without_schema': dict(sorted(unchecked.items())),
            'problems': problems}


def require_valid(bp=None, rp=None, samples=None, custom_components=ENGINE_COMPONENTS):
    """Raises ValueError listing every problem (at most 40) when the packs would not load cleanly."""
    problems = check_tree(bp, rp, samples, custom_components)
    if problems:
        shown = problems[:SHOWN_PROBLEMS]
        more = f'\n  ... and {len(problems) - len(shown)} more' if len(problems) > len(shown) else ''
        raise ValueError('Generated Bedrock files fail Mojang\'s schemas:\n  ' + '\n  '.join(shown) + more)
    return True


def _version(text):
    """A dotted version as a tuple of numbers, or None for anything else."""
    return tuple(int(part) for part in text.split('.')) if re.fullmatch(r'\d+(\.\d+)*', text) else None


def _is_type(value, kind):
    if kind == 'integer':
        return ((isinstance(value, int) and not isinstance(value, bool))
                or (isinstance(value, float) and value.is_integer()))
    if kind == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == 'boolean':
        return isinstance(value, bool)
    if kind == 'string':
        return isinstance(value, str)
    if kind == 'array':
        return isinstance(value, list)
    if kind == 'object':
        return isinstance(value, dict)
    if kind == 'null':
        return value is None
    return True


def _check_number(value, schema, pointer, errors):
    if 'minimum' in schema and value < schema['minimum']:
        errors.append(f'{pointer}: {value} is below the minimum {schema["minimum"]}')
    if 'maximum' in schema and value > schema['maximum']:
        errors.append(f'{pointer}: {value} is above the maximum {schema["maximum"]}')
    if 'exclusiveMinimum' in schema and value <= schema['exclusiveMinimum']:
        errors.append(f'{pointer}: {value} must be above {schema["exclusiveMinimum"]}')
    if 'exclusiveMaximum' in schema and value >= schema['exclusiveMaximum']:
        errors.append(f'{pointer}: {value} must be below {schema["exclusiveMaximum"]}')
    if 'multipleOf' in schema:
        quotient = value / schema['multipleOf']
        if not math.isclose(quotient, round(quotient), abs_tol=1e-9):
            errors.append(f'{pointer}: {value} is not a multiple of {schema["multipleOf"]}')


def _check_string(value, schema, pointer, errors):
    if 'maxLength' in schema and len(value) > schema['maxLength']:
        errors.append(f'{pointer}: longer than {schema["maxLength"]} characters')
    if 'minLength' in schema and len(value) < schema['minLength']:
        errors.append(f'{pointer}: shorter than {schema["minLength"]} characters')
    if 'pattern' in schema and not re.search(schema['pattern'], value, _flags(schema)):
        errors.append(f'{pointer}: {value!r} does not match {schema["pattern"]}')


def _flags(schema):
    return re.IGNORECASE if 'icase' in schema.get('x-regex-flags', '') else 0


def _short(value):
    text = json.dumps(value)
    return text if len(text) <= 60 else text[:57] + '...'


def _with_typed_state_arrays_as_strings(body, errors):
    """The block body as the published schema reads it: integer and boolean state arrays as string arrays.

    The game reads those arrays, but the 1.26.20 schema lists string arrays
    only; repeated values are reported here instead.
    """
    description = body.get('description')
    states = (description or {}).get('states') if isinstance(description, dict) else None
    if not isinstance(states, dict):
        return body
    as_strings = {}
    for name, values in states.items():
        typed = (isinstance(values, list) and values
                 and (all(_is_type(value, 'integer') for value in values)
                      or all(isinstance(value, bool) for value in values)))
        if typed:
            if len(set(map(json.dumps, values))) != len(values):
                errors.append(f'description/states/{name}: values repeat')
            as_strings[name] = [json.dumps(value) for value in values]
        else:
            as_strings[name] = values
    return dict(body, description=dict(body['description'], states=as_strings))


def _component_errors(where, components, known, custom_components, format_version):
    """Game rules the schema leaves out, for one components object (the base or a permutation's)."""
    errors = []
    for name in components:
        if name not in known and name not in custom_components:
            errors.append(f'{where}/{name}: not a block component for format {format_version}'
                          + (' (tags go in minecraft:tags)' if name.startswith('tag:') else ''))
    geometry = components.get('minecraft:geometry')
    if isinstance(geometry, dict):
        for bone, expression in geometry.get('bone_visibility', {}).items():
            if not isinstance(expression, bool) and molang_queries(expression) - {'block_state'}:
                errors.append(f'{where}/minecraft:geometry/bone_visibility/{bone}: only query.block_state() is allowed')
    rotation = (components.get('minecraft:transformation') or {}).get('rotation')
    if isinstance(rotation, list) and any(value % 90 for value in rotation):
        errors.append(f'{where}/minecraft:transformation: rotation must be in steps of 90 degrees')
    instances = components.get('minecraft:material_instances')
    if isinstance(instances, dict):
        methods = {item.get('render_method', 'opaque') for item in instances.values() if isinstance(item, dict)}
        if len(methods) > 1:
            errors.append(f'{where}/minecraft:material_instances: one render method per block, found {sorted(methods)}')
    return errors


def _state_value_count(values):
    """How many values a block state declares: a {'values': {min, max}} range or a list."""
    options = values.get('values') if isinstance(values, dict) else values
    if isinstance(options, dict) and isinstance(options.get('min'), int) and isinstance(options.get('max'), int):
        return options['max'] - options['min'] + 1
    return len(options) if isinstance(options, list) else 0


def _entity_errors(document, schemas):
    body = document.get('minecraft:entity') if isinstance(document, dict) else None
    if not isinstance(document, dict) or not isinstance(document.get('format_version'), str):
        return ['format_version must be a version string']
    if not isinstance(body, dict):
        return ['minecraft:entity is missing']
    return schemas.validate(body, schemas.versioned(*ENTITY_SCHEMA, document['format_version']))


def _cube_points(cube, bone):
    """Cube corners in block space after the cube and bone rotations."""
    (x, y, z), (width, height, depth) = cube['origin'], cube['size']
    corners = [[x + width * far_x, y + height * far_y, z + depth * far_z]
               for far_x in (0, 1) for far_y in (0, 1) for far_z in (0, 1)]
    result = []
    for corner in corners:
        point = corner
        if cube.get('rotation'):
            point = _bedrock_rotate(point, cube['rotation'], cube.get('pivot', [0, 0, 0]))
        if bone.get('rotation'):
            point = _bedrock_rotate(point, bone['rotation'], bone.get('pivot', [0, 0, 0]))
        result.append(point)
    return result


def _bedrock_rotate(point, angles, pivot):
    """Bedrock geometry rotation: X, then Y, then Z, with X and Y mirrored like the converter writes them."""
    # Bedrock stores X mirrored and X/Y rotations negated; undo that to rotate in Java axes, then mirror back.
    java = [-point[0], point[1], point[2]]
    centre = [-pivot[0], pivot[1], pivot[2]]
    turned = _rotate(java, [-angles[0], -angles[1], angles[2]], centre)
    return [-turned[0], turned[1], turned[2]]


def _bone_names(models):
    """{geometry identifier: its bone names} for the models of a geometry file."""
    return {(model.get('description') or {}).get('identifier'): {bone.get('name') for bone in model.get('bones', [])}
            for model in models}


def _culling_identifier(body):
    return ((body or {}).get('description') or {}).get('identifier')


def _references(body, geometries, cullings):
    """A block's geometry, bone and culling references the resource pack does not define."""
    errors = []
    parts = [body.get('components', {})] + [item.get('components', {}) for item in body.get('permutations', [])]
    for components in parts:
        geometry = components.get('minecraft:geometry')
        identifier = geometry.get('identifier') if isinstance(geometry, dict) else geometry
        if identifier and not identifier.startswith('minecraft:geometry.') and identifier not in geometries:
            errors.append(f'geometry {identifier} is not in the resource pack')
            continue
        if isinstance(geometry, dict):
            bones = geometries.get(identifier, set())
            for bone in geometry.get('bone_visibility', {}):
                if bones and bone not in bones:
                    errors.append(f'bone_visibility names a bone {bone!r} that {identifier} does not have')
            if geometry.get('culling') and geometry['culling'] not in cullings:
                errors.append(f'culling {geometry["culling"]} is not in the resource pack')
    return errors


def _read(path, problems):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as error:
        problems.append(f'{path}: {error}')
        return None


def _strip_comments(text):
    """JSON with // and /* */ comments outside strings (Bedrock reads those) as plain JSON."""
    result, index, quoted = [], 0, False
    while index < len(text):
        char = text[index]
        if quoted:
            result.append(char)
            if char == '\\':
                result.append(text[index + 1:index + 2])
                index += 2
                continue
            quoted = char != '"'
        elif char == '"':
            quoted = True
            result.append(char)
        elif text.startswith('//', index):
            end = text.find('\n', index)
            index = len(text) if end < 0 else end
            continue
        elif text.startswith('/*', index):
            end = text.find('*/', index + 2)
            index = len(text) if end < 0 else end + 2
            continue
        else:
            result.append(char)
        index += 1
    return ''.join(result)


def _addon_files(path):
    """{name: bytes} of the JSON files of a built add-on.

    path is an .mcaddon, .mcpack or .zip (nested packs included) or a folder.
    """
    files = {}

    def read_zip(archive, prefix):
        for name in archive.namelist():
            if name.lower().endswith(('.mcpack', '.mcaddon', '.zip')):
                with zipfile.ZipFile(io.BytesIO(archive.read(name))) as inner:
                    read_zip(inner, prefix + name + '/')
            elif name.endswith('.json'):
                files[prefix + name] = archive.read(name)

    path = Path(path)
    if path.is_dir():
        for item in sorted(path.rglob('*.json')):
            if item.is_file():
                files[item.relative_to(path).as_posix()] = item.read_bytes()
    else:
        with zipfile.ZipFile(path) as archive:
            read_zip(archive, '')
    return files


def _pack_roots(files):
    """{folder holding a manifest.json: 'bp', 'rp' or 'other'}, by the manifest's module types."""
    roots = {}
    for name, data in files.items():
        if name.rsplit('/', 1)[-1] != 'manifest.json':
            continue
        try:
            modules = json.loads(_strip_comments(data.decode('utf-8-sig'))).get('modules', [])
        except ValueError:
            modules = []
        types = {module.get('type') for module in modules if isinstance(module, dict)}
        if types & {'data', 'script'}:
            roots[name[:-len('manifest.json')]] = 'bp'
        else:
            roots[name[:-len('manifest.json')]] = 'rp' if 'resources' in types else 'other'
    return roots
