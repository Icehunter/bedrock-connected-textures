"""Entity textures, custom entity models and their animations for a converted Java pack.

prepare_entities() reads the author's pack once and stages every generated
Bedrock file; export_entities() copies the staged files into one renderer's
resource pack (texture sets for Vibrant Visuals only: ray tracing draws
entities with colour only). Nothing from Mojang's packs is copied except the
client entity descriptions and render controllers that must be restated to
point at converted geometry, animations or textures.

A converted model replaces the vanilla geometry under its own identifier only
when no other entity draws that geometry; otherwise it gets a geometry of its
own, and the same holds for the textures it draws. Climate and baby variants
draw under Molang conditions on v.index and query.is_baby. A restated client
entity keeps its vanilla format: format 1.8.0 has no animate or initialize
script, so there the models' animations play through an animation controller
and their initial values are set on the first frame. OptiFine random
textures and random models are reported but not converted: Bedrock cannot
read the conditions they are picked by.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import re
import shutil
import zipfile

import numpy as np

from common import read_json, write_json
from java_entity_models import (ModelError, Target, box_uv_faces, convert, load_model, normalize_geometries,
                                resolve_inheritance, resolve_texture, shift_uv)
from java_entity_molang import molang_valid
from java_entity_textures import (JAVA_TEXTURES, TexturePlanner, build_output, encode, resized_mask,
                                  load_table as load_texture_table)

MODELS_TABLE = Path(__file__).with_name('data') / 'java-entity-models.json'
CEM_FOLDERS = ('assets/minecraft/optifine/cem/', 'assets/minecraft/emf/cem/')
RANDOM_FOLDERS = ('assets/minecraft/optifine/random/', 'assets/minecraft/optifine/mob/')
CLIMATE_KEYS = ('default', 'warm', 'cold')
BABY_CLIMATE_KEYS = ('baby', 'baby_warm', 'baby_cold')
# v.index of each climate variant, as the vanilla client entity scripts set it.
CLIMATE_INDEX = {'default': 0, 'warm': 1, 'cold': 2, 'baby': 0, 'baby_warm': 1, 'baby_cold': 2}
ALL_RENDERERS = ('classic', 'vv', 'rtx')
# Normal and MERS maps draw in Vibrant Visuals only: ray tracing draws entities with colour only.
PBR_RENDERERS = ('vv',)
RENDERER_NOTES = {'classic': 'colour textures, models and animations',
                  'vv': 'adds texture sets (normal and MERS) from LabPBR maps',
                  'rtx': 'colour textures, models and animations; ray tracing draws entity textures without PBR maps'}
# Client entity formats before 1.10.0 have no scripts.animate or scripts.initialize (the game reports
# "child 'animate' not valid here"): they play animations through animation_controllers.
SCRIPTED_ANIMATION_FORMAT = (1, 10, 0)
# Every vanilla animation controller file uses this format, those of 1.8.0 entities included.
ANIMATION_CONTROLLER_FORMAT = '1.10.0'
# Set once the converted models' initial values are in place, in entities without an initialize script.
INITIALIZED_FLAG = 'v.bct_initialized'
RANDOM_NOT_CONVERTED = ('Bedrock render controllers choose textures from server-side variant values; OptiFine random '
                        'rules pick by entity UUID with biome, height, name and other conditions the client cannot '
                        'read, so random textures and random models are listed, not converted.')


# --- entry points ---

def prepare_entities(stack, vanilla, samples, destination, *, models_table=None, texture_table=None):
    """Stage entity textures, models and animations; return the plan (also written to disk).

    stack is the author's pack stack, vanilla the Java client jar and samples the
    vanilla Bedrock resource pack. Files are staged under destination/staged.
    """
    destination = Path(destination)
    if destination.exists():
        shutil.rmtree(destination)
    staged = destination / 'staged'
    staged.mkdir(parents=True)
    reference = BedrockReference(samples)
    models_table = models_table or load_models_table()
    texture_table = texture_table or load_texture_table()
    with zipfile.ZipFile(vanilla) as jar:
        texture_planner = TexturePlanner(stack, jar, samples, texture_table, sampled=reference.sampled_mask)
        model_planner = _ModelPlanner(stack, reference, models_table, texture_planner,
                                      _java_textures_by_bedrock_path(texture_table))
        models_report, entity_plans, coupled = model_planner.plan()
        texture_planner.plan(coupled)
        files = _write_models(entity_plans, reference, staged)
        texture_report, texture_files = _write_textures(stack, texture_planner, staged)
        files += texture_files
    report = {'models': models_report, 'textures': texture_report, 'random': random_report(stack),
              'renderers': dict(RENDERER_NOTES), 'in_game_verified': False}
    report_path = destination / 'entities-report.json'
    write_json(report_path, report)
    plan = {'staged': str(staged.resolve()), 'files': files, 'report': str(report_path.resolve())}
    write_json(destination / 'entities-plan.json', plan)
    return plan


def export_entities(plan, resource_pack, renderer):
    """Copy staged entity files into one renderer's resource pack."""
    staged = Path(plan['staged'])
    resource_pack = Path(resource_pack)
    written = 0
    kept = []
    for entry in plan['files']:
        if renderer not in entry['renderers']:
            continue
        source = staged / entry['path']
        target = resource_pack / entry['path']
        entity_texture = entry['path'].startswith(('textures/entity/', 'textures/models/'))
        if entry['path'].startswith('textures/') and not entity_texture and target.exists():
            # Block textures an actor borrows (pottery patterns) stay with the block conversion when it wrote them.
            kept.append(entry['path'])
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        is_colour = target.suffix in ('.png', '.tga') and not target.stem.endswith(('_bct_normal', '_bct_mers'))
        if entity_texture and is_colour:
            # An entity colour texture replaces a same-named file of the other format written by an earlier step.
            for other in ('.png', '.tga'):
                sibling = target.with_suffix(other)
                if sibling != target and sibling.exists():
                    sibling.unlink()
        shutil.copyfile(source, target)
        written += 1
    return {'renderer': renderer, 'files': written, 'kept_existing': kept, 'report': plan['report']}


def load_models_table(path=MODELS_TABLE):
    return read_json(Path(path))


def cem_models(stack):
    """Model name -> path of every .jem directly in an OptiFine or EMF cem folder.

    Paths are visited in sorted order and the first wins, so a model in the EMF
    folder takes precedence over an OptiFine model of the same name.
    """
    models = {}
    for path in sorted(stack.files):
        for folder in CEM_FOLDERS:
            if path.startswith(folder) and path.endswith('.jem') and '/' not in path[len(folder):]:
                models.setdefault(path[len(folder):-4], path)
    return models


def random_report(stack):
    """OptiFine/ETF random textures and random models, reported (not converted)."""
    rule_files = sorted(path for path in stack.files
                        if path.endswith('.properties') and path.startswith(RANDOM_FOLDERS + CEM_FOLDERS))
    variant_textures = sorted(path for path in stack.files if path.startswith(RANDOM_FOLDERS)
                              and path.endswith('.png') and not path.endswith(('_n.png', '_s.png')))
    rules = []
    for path in rule_files:
        text = stack.read(path).decode('utf-8', 'replace')
        rules.append({'file': path, 'properties': _property_names(text)})
    return {'rule_files': len(rule_files), 'variant_textures': len(variant_textures), 'rules': rules,
            'converted': 0, 'reason': RANDOM_NOT_CONVERTED}


def _property_names(text):
    """Property names of a .properties rule file without their rule number ('textures.2' -> 'textures')."""
    return sorted({line.split('=', 1)[0].strip().split('.')[0] for line in text.splitlines()
                   if '=' in line and not line.strip().startswith('#')})


# --- Bedrock reference ---

class BedrockReference:
    """Current client entities, geometries and render controllers of the vanilla resource pack."""

    def __init__(self, samples):
        self.samples = Path(samples)
        self.entities = _load_client_entities(self.samples / 'entity')
        self.geometries = resolve_inheritance(_load_geometries(self.samples / 'models'))
        self.render_controllers, self.render_controller_formats = _load_render_controllers(
            self.samples / 'render_controllers')
        # Which (entity, key) pairs draw each geometry and texture, to tell an entity's own from shared ones.
        self.geometry_users = {}
        self.texture_users = {}
        for identifier, entry in self.entities.items():
            for key, geometry in (entry['description'].get('geometry') or {}).items():
                self.geometry_users.setdefault(geometry, set()).add((identifier, key))
            for key, texture in (entry['description'].get('textures') or {}).items():
                self.texture_users.setdefault(texture, set()).add((identifier, key))

    def sampled_mask(self, texture):
        """UV rectangles the Bedrock geometries drawing this texture sample (texture-size grid)."""
        geometries = set()
        for identifier, key in self.texture_users.get(texture, set()):
            mapping = self.entities[identifier]['description'].get('geometry') or {}
            baby = key.startswith('baby')
            # Baby textures pair with baby geometry, the rest with every other geometry of the entity.
            geometries.update(value for name, value in mapping.items() if name.startswith('baby') == baby)
        masks = [geometry_uv_mask(self.geometries[name]) for name in sorted(geometries) if name in self.geometries]
        if not masks:
            return None
        height = max(mask.shape[0] for mask in masks)
        width = max(mask.shape[1] for mask in masks)
        union = np.zeros((height, width), bool)
        for mask in masks:
            union |= resized_mask(mask, (width, height))
        return union

    def uses_climate_variants(self, identifier):
        """Entities that pick their warm, temperate and cold look from v.index."""
        scripts = self.entities[identifier]['description'].get('scripts', {})
        script = ' '.join(map(str, scripts.get('pre_animation', [])))
        return 'climate_variant' in script and 'v.index' in script


def geometry_uv_mask(geometry):
    """Texture pixels (in geometry UV units) that a geometry's cubes sample."""
    width = int(math.ceil(float(geometry['description'].get('texture_width', 64))))
    height = int(math.ceil(float(geometry['description'].get('texture_height', 64))))
    mask = np.zeros((max(height, 1), max(width, 1)), bool)

    def mark(u1, v1, u2, v2):
        left, right = sorted((u1, u2))
        top, bottom = sorted((v1, v2))
        mask[max(0, int(math.floor(top))):min(height, int(math.ceil(bottom))),
             max(0, int(math.floor(left))):min(width, int(math.ceil(right)))] = True

    for bone in geometry['bones']:
        for cube in bone.get('cubes', []) or []:
            uv = cube.get('uv')
            if isinstance(uv, list):
                # Box UV, laid out from the cube size rounded down.
                for rect in box_uv_faces(uv, [math.floor(size) for size in cube['size']]).values():
                    mark(*rect)
            elif isinstance(uv, dict):
                for face in uv.values():
                    u, v = face.get('uv', [0, 0])
                    size_u, size_v = face.get('uv_size', [0, 0])
                    mark(u, v, u + size_u, v + size_v)
    return mask


def _load_client_entities(folder):
    """Identifier -> {'version', 'file', 'document', 'description'}; the newest min_engine_version wins."""
    entities = {}
    for path in sorted(folder.glob('*.json')):
        try:
            document = read_json(path)
        except ValueError:
            continue
        description = document.get('minecraft:client_entity', {}).get('description', {})
        identifier = description.get('identifier')
        if not identifier:
            continue
        version = _engine_version(description.get('min_engine_version', '0'))
        current = entities.get(identifier)
        if current is None or version >= current['version']:
            entities[identifier] = {'version': version, 'file': path.name, 'document': document,
                                    'description': description}
    return entities


def _load_geometries(folder):
    found = {}
    for path in sorted(folder.rglob('*.json')):
        try:
            found.update(normalize_geometries(read_json(path)))
        except (ValueError, AttributeError):
            continue
    return found


def _load_render_controllers(folder):
    """(name -> render controller, name -> format_version of the file that defines it)."""
    controllers = {}
    formats = {}
    for path in sorted(folder.glob('*.json')):
        try:
            document = read_json(path)
            defined = document.get('render_controllers', {})
            controllers.update(defined)
        except ValueError:
            continue
        for name in defined:
            formats[name] = document.get('format_version')
    return controllers, formats


def _engine_version(text):
    """A dotted version as a comparable tuple; (0,) when it is not numeric."""
    try:
        return tuple(int(part) for part in str(text).split('.'))
    except ValueError:
        return (0,)


# --- planning ---

class _Unsupported(Exception):
    """A model the conversion leaves out, with the reason for the report."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class _ModelPlanner:
    """Converts every custom entity model of a pack and decides which textures each draws."""

    def __init__(self, stack, reference, models_table, texture_planner, java_textures):
        self.stack = stack
        self.reference = reference
        self.models_table = models_table
        self.texture_planner = texture_planner
        self.java_textures = java_textures      # Bedrock texture path -> the Java texture that fills it
        self.report = {}
        self.entity_plans = {}                  # entity identifier -> {'models': [model item, ...]}
        self.coupled = {}                       # Bedrock texture -> {'source', 'reference'} a converted model draws
        self.claimed_geometry = {}              # vanilla geometry -> [(entity, key)] of the models built on it

    def plan(self):
        """Convert every model the pack ships; returns (models report, entity plans, coupled textures)."""
        found = cem_models(self.stack)
        for name, path in sorted(found.items()):
            try:
                identifier, item = self._read_model(name, path, found)
            except _Unsupported as unsupported:
                self.report[name] = {'source': path, 'status': 'unsupported', 'reason': unsupported.reason}
                continue
            self.entity_plans.setdefault(identifier, {'models': []})['models'].append(item)
            self.claimed_geometry.setdefault(item['vanilla_id'], []).append((identifier, item['key']))
        self._stack_sheep_wool(found)
        for identifier, entity_plan in sorted(self.entity_plans.items()):
            self._convert_entity(identifier, entity_plan)
        return self.report, self.entity_plans, self.coupled

    def _read_model(self, name, path, found):
        """(entity identifier, model item) for one .jem; raises _Unsupported with the reason it cannot convert."""
        entry = self.models_table['models'].get(name)
        variant = re.fullmatch(r'(.+?)(\d+)', name)
        if entry is None and variant and variant.group(1) in found:
            raise _Unsupported('random model variant of %s; Bedrock cannot pick models by OptiFine random rules'
                               % variant.group(1))
        if entry is None:
            reason = self.models_table['unsupported'].get(name, 'no known Bedrock entity for this model name')
            raise _Unsupported(reason)
        identifier, key = entry['entity'], entry['geometry']
        bedrock = self.reference.entities.get(identifier)
        if bedrock is None:
            raise _Unsupported('Bedrock has no client entity ' + identifier)
        description = bedrock['description']
        geometry_key = _geometry_key(description, key)
        vanilla_id = (description.get('geometry') or {}).get(geometry_key) if geometry_key else None
        if vanilla_id not in self.reference.geometries:
            raise _Unsupported('Bedrock geometry for %s/%s not found' % (identifier, key))
        try:
            data = json.loads(self.stack.read(path).decode('utf-8-sig'))
        except (ValueError, UnicodeDecodeError) as error:
            raise _Unsupported('model JSON is invalid: %s' % error) from error
        try:
            model = load_model(data, read_part=_part_model_reader(self.stack, path))
        except ModelError as error:
            raise _Unsupported(str(error)) from error
        item = {'name': name, 'source': path, 'key': key, 'geometry_key': geometry_key, 'vanilla_id': vanilla_id,
                'model': model, 'entry': entry}
        return identifier, item

    def _stack_sheep_wool(self, found):
        """Bedrock draws a sheep's wool with its body geometry and one stacked texture.

        With both the body model and the wool layer model, the wooled geometry gets
        both (wool UVs moved to the lower half of a 64x64 texture) and the sheared
        geometry the body alone; the stacked texture comes from the sheep remap.
        """
        entity_plan = self.entity_plans.get('minecraft:sheep')
        if not entity_plan or 'sheep_wool' not in found:
            return
        body = next((item for item in entity_plan['models'] if item['name'] == 'sheep'), None)
        if body is None:
            return
        wool_path = found['sheep_wool']
        try:
            wool = load_model(json.loads(self.stack.read(wool_path).decode('utf-8-sig')))
        except (ValueError, ModelError) as error:
            self.report['sheep_wool'] = {'source': wool_path, 'status': 'unsupported',
                                         'reason': 'model not read: %s' % error}
            return
        height = body['model'].texture_size[1]
        if body['model'].texture_size != wool.texture_size or body['model'].texture_size[0] != 2 * height:
            self.report['sheep_wool'] = {'source': wool_path, 'status': 'unsupported',
                                         'reason': 'body and wool models must share a 2:1 texture size to stack'}
            return
        sheared = copy.deepcopy(body)
        body['model'] = _with_wool_layer(body, wool, height)
        body['stacked_layers'] = ['sheep_wool']
        sheared['model'].texture_size = (sheared['model'].texture_size[0], 2 * height)
        sheared['name'] = 'sheep_sheared'
        sheared['key'] = 'sheared'
        sheared['geometry_key'] = 'sheared'
        sheep_geometry = self.reference.entities['minecraft:sheep']['description'].get('geometry') or {}
        sheared['vanilla_id'] = sheep_geometry.get('sheared')
        if sheared['vanilla_id'] in self.reference.geometries:
            entity_plan['models'].append(sheared)
        self.report['sheep_wool'] = {'source': wool_path, 'status': 'merged',
                                     'reason': 'drawn with the sheep body geometry on the stacked sheep texture'}

    def _convert_entity(self, identifier, entity_plan):
        description = self.reference.entities[identifier]['description']
        model_keys = [item['key'] for item in entity_plan['models']]
        several_models = len(entity_plan['models']) > 1
        uses_climate = self.reference.uses_climate_variants(identifier)
        for item in entity_plan['models']:
            self._convert_model(item, identifier, description, model_keys, several_models, uses_climate)
            self.report[item['name']] = item['report']

    def _convert_model(self, item, identifier, description, model_keys, several_models, uses_climate):
        name, key = item['name'], item['key']
        in_place = self._replaces_vanilla_geometry(item, identifier)
        output_id = item['vanilla_id'] if in_place else 'geometry.bct.' + _slug(name)
        # The scripts of all an entity's models run on its variables, so each model gets a prefix of its own.
        prefix = 'cem_' + _slug(name) if several_models else 'cem'
        result = convert(item['model'], self._target(item, output_id, prefix), variable_prefix=prefix)
        item.update(result=result, output_id=output_id, in_place=in_place, prefix=prefix,
                    condition=_model_condition(key, uses_climate, model_keys))
        if not item.get('stacked_layers') and name != 'sheep_sheared':
            textures = self._model_textures(item, identifier, description)
        else:
            textures = {'texture_keys': ['default'], 'written': [], 'kept_vanilla': [], 'redirected': {},
                        'note': 'textures/entity/sheep/sheep is the stacked body and wool texture'}
        item.setdefault('texture_redirects', {})
        invalid = [line for line in result['pre_animation'] + result['initialize'] if not molang_valid(line)]
        item['report'] = {'source': item['source'], 'status': 'converted', 'entity': identifier, 'key': key,
                          'geometry': output_id, 'overrides_vanilla_geometry': in_place,
                          'textures': textures, **result['report'],
                          'animation_coverage': _animation_coverage(result['report']),
                          'invalid_molang': invalid[:5]}

    def _replaces_vanilla_geometry(self, item, identifier):
        """True when the model may take over the vanilla geometry identifier.

        Only when its key draws that geometry itself, no other entity or key draws
        it and no other model claims it.
        """
        users = self.reference.geometry_users.get(item['vanilla_id'], set())
        own = {(identifier, item['key'])} if item['key'] == item['geometry_key'] else set()
        return bool(own) and users <= own and len(self.claimed_geometry.get(item['vanilla_id'], [])) == 1

    def _target(self, item, output_id, prefix):
        entry = item['entry']
        family = self.models_table.get('families', {}).get(entry.get('family'), {})
        frame = entry.get('frame', {})
        return Target(output_id, self.reference.geometries[item['vanilla_id']], entry.get('bones', {}),
                      vanilla_parts=entry.get('java_parts', {}), formulas=family, prefix=prefix,
                      frame_scale=float(frame.get('scale', 1.0)), frame_offset=tuple(frame.get('offset', (0, 0, 0))))

    def _model_textures(self, item, identifier, description):
        """Decide which Bedrock textures the converted model draws and which author files fill them.

        A texture the model names itself, or the pack's own texture for that mob
        variant, is drawn in the model's UV layout. When other Bedrock entities
        share that texture path the model gets a texture path of its own.
        """
        textures = description.get('textures') or {}
        keys = _texture_keys_for(description, item['key'])
        info = {'texture_keys': keys, 'written': [], 'kept_vanilla': [], 'redirected': {}}
        model_texture = item['model'].texture
        explicit = resolve_texture(model_texture, item['source']) if model_texture else None
        if explicit and explicit not in self.stack.files:
            info['note'] = ('the model names %s, which the pack does not ship; Bedrock keeps its vanilla texture'
                            % explicit)
            explicit = None
        item['texture_redirects'] = {}
        own_keys = {(identifier, name) for name in textures}
        for texture_key in keys:
            path = textures.get(texture_key)
            if not path:
                continue
            source = explicit if explicit is not None else self._pack_texture_for(path)
            if source is None:
                info['kept_vanilla'].append(path)
                continue
            target = path
            users = self.reference.texture_users.get(path, set())
            if not users <= own_keys:
                target = 'textures/entity/bct/' + _slug(item['name'])
                if texture_key != 'default':
                    target += '_' + _slug(texture_key)
                item['texture_redirects'][texture_key] = target
                info['redirected'][texture_key] = {'from': path, 'to': target,
                                                   'shared_with': sorted('%s/%s' % user for user in users - own_keys)}
            self.coupled[target] = {'source': source, 'reference': path}
            info['written'].append({'target': target, 'source': source})
        return info

    def _pack_texture_for(self, bedrock_path):
        """The pack's texture for a Bedrock texture path, under its current or an older Java name; or None."""
        java = self.java_textures.get(bedrock_path)
        if not java:
            return None
        legacy_paths = self.texture_planner.table.get('legacy_paths', {})
        candidates = [java] + [old for old, new in legacy_paths.items() if new == java]
        for name in candidates:
            if JAVA_TEXTURES + name + '.png' in self.stack.files:
                return JAVA_TEXTURES + name + '.png'
        return None


def _java_textures_by_bedrock_path(texture_table):
    """Bedrock texture path -> the Java texture that fills it when a converted model draws it.

    The model texture pairs come first; a Bedrock texture with the vanilla layout of
    a Java texture ('direct') takes that texture instead.
    """
    java_textures = dict(texture_table.get('model_texture_pairs', {}))
    for java, targets in texture_table['direct'].items():
        for target, _ in targets:
            java_textures[target] = java
    return java_textures


def _part_model_reader(stack, model_path):
    """read_part for load_model: the .jpm a model entry names, found by OptiFine's texture path rules."""
    def read_part(reference):
        target = resolve_texture(re.sub(r'\.jpm$', '', str(reference)), model_path)
        # resolve_texture gives a .png path; the part model sits beside it as .jpm.
        target = target[:-4] + '.jpm' if target else target
        if target not in stack.files:
            return None
        try:
            return json.loads(stack.read(target).decode('utf-8-sig'))
        except ValueError:
            return None
    return read_part


def _with_wool_layer(body, wool, height):
    """The body model with the wool model's parts added, on a texture twice as tall.

    Wool parts that are not vanilla parts get a 'wool_' prefix so they cannot clash
    with the body's parts, and the wool animations follow the new names.
    """
    combined = copy.deepcopy(body['model'])
    layer = copy.deepcopy(wool)
    vanilla_names = ({part.vanilla_part for part in body['model'].parts + layer.parts if part.vanilla_part}
                     | set(body['entry'].get('java_parts', {})))
    renamed = {}
    for part in layer.parts:
        shift_uv(part, height)
        _prefix_wool_ids(part, vanilla_names, renamed)
    if renamed:
        _rename_in_animations(layer.parts, renamed)
    combined.parts.extend(layer.parts)
    combined.issues.extend(layer.issues)
    combined.texture_size = (combined.texture_size[0], 2 * height)
    return combined


def _prefix_wool_ids(part, vanilla_names, renamed):
    if part.id not in vanilla_names:
        renamed[part.id] = 'wool_' + part.id
        part.id = renamed[part.id]
    for child in part.children:
        _prefix_wool_ids(child, vanilla_names, renamed)


def _rename_in_animations(parts, renamed):
    """Rewrite '<id>.' references in the animation keys and expressions of parts to the renamed ids."""
    # Longest ids first, so the longest matching id wins.
    alternatives = '|'.join(re.escape(old) for old in sorted(renamed, key=len, reverse=True))
    pattern = re.compile(r'(?<![\w.])(' + alternatives + r')(?=\.)')

    def rename(text):
        return pattern.sub(lambda match: renamed[match.group(1)], text)

    for part in parts:
        part.animations = [{rename(key): (rename(value) if isinstance(value, str) else value)
                            for key, value in block.items()}
                           for block in part.animations]


def _slug(name):
    return re.sub(r'[^a-z0-9_]', '_', name.lower())


def _texture_keys_for(description, model_key):
    """Texture keys of the client entity drawn by the model for model_key."""
    textures = description.get('textures') or {}
    if model_key in textures:
        return [model_key]
    if model_key == 'default':
        return [name for name in textures if not name.startswith('baby')]
    if model_key == 'baby':
        exact = [name for name in textures if name in ('baby', 'baby_default')]
        return exact or [name for name in textures if name.startswith('baby')]
    return []


def _geometry_key(description, model_key):
    """The client entity geometry key a model key draws with: its own, else the shared baby or default one."""
    geometry = description.get('geometry') or {}
    if model_key in geometry:
        return model_key
    if model_key.startswith('baby_') and 'baby' in geometry:
        return 'baby'
    if model_key in ('warm', 'cold') and 'default' in geometry:
        return 'default'
    return None


def _model_condition(model_key, uses_climate, model_keys):
    """Molang condition under which the model for model_key draws, or None when it always draws.

    Climate entities pick their variant by v.index; baby models share one condition
    unless the pack also ships warm or cold baby models.
    """
    if uses_climate and model_key in CLIMATE_KEYS + BABY_CLIMATE_KEYS:
        baby = model_key.startswith('baby')
        separate_baby_climates = baby and any(other in model_keys for other in ('baby_warm', 'baby_cold'))
        if baby and not separate_baby_climates:
            return 'query.is_baby'
        return '%squery.is_baby && v.index == %d' % ('' if baby else '!', CLIMATE_INDEX[model_key])
    if model_key == 'default' and 'baby' in model_keys:
        return '!query.is_baby'
    if model_key == 'baby':
        return 'query.is_baby'
    return None


def _animation_coverage(report):
    """One line on how much of a model's animation was translated."""
    total = report['assignments']
    if not total:
        return 'no animations'
    lost = len(report['unresolved_identifiers']) + len(report['unmapped_variables'])
    if lost == 0 and not report['approximations']:
        return 'all %d expressions translated' % total
    return '%d expressions translated; %d identifiers without a Bedrock value, %d approximations' % (
        total, lost, len(report['approximations']))


# --- staging ---

def _staged_file(path, renderers):
    return {'path': path, 'renderers': list(renderers)}


def _write_models(entity_plans, reference, staged):
    """Stage every converted entity; returns the staged files."""
    files = []
    for identifier, entity_plan in sorted(entity_plans.items()):
        files += _write_entity(identifier, entity_plan, reference, staged)
    return files


def _write_entity(identifier, entity_plan, reference, staged):
    """Stage an entity's geometries and animations and, when they changed, its render controllers and description.

    Formats before 1.10.0 also get an animation controller that plays the animations.
    """
    entry = reference.entities[identifier]
    document = copy.deepcopy(entry['document'])
    description = document['minecraft:client_entity']['description']
    # Sections the vanilla description lacks are added here and dropped again if they stay empty.
    geometry = description.setdefault('geometry', {})
    textures = description.setdefault('textures', {})
    animations = description.setdefault('animations', {})
    scripts = description.setdefault('scripts', {})
    # An older format plays the models' animations through an animation controller and sets their initial
    # values once from pre_animation, where newer formats use the animate and initialize scripts.
    scripted = _engine_version(document.get('format_version', '0')) >= SCRIPTED_ANIMATION_FORMAT
    vanilla_line_count = len(scripts.get('pre_animation', []))
    entity_name = _slug(identifier.split(':')[-1])
    animation_file = {'format_version': '1.8.0', 'animations': {}}
    keys_needing_controller = []
    controller_animations = []
    initial_values = []
    several_models = len(entity_plan['models']) > 1
    files = []
    for item in entity_plan['models']:
        result = item['result']
        model_name = _slug(item['name'])
        geometry_path = 'models/entity/bct_%s.geo.json' % model_name
        write_json(staged / geometry_path, result['geometry'])
        files.append(_staged_file(geometry_path, ALL_RENDERERS))
        key = item['key']
        if item['output_id'] != item['vanilla_id'] or key != item['geometry_key']:
            geometry[key] = item['output_id']
            if key != item['geometry_key']:
                # Vanilla draws this variant with a shared geometry, so only a render controller can pick the new one.
                keys_needing_controller.append(key)
        for texture_key, target in item['texture_redirects'].items():
            textures[texture_key] = target
        if result['animation_bones']:
            animation_id = 'animation.bct.cem.' + model_name
            animation_file['animations'][animation_id] = {'loop': True, 'bones': result['animation_bones']}
            short_name = 'bct_cem_' + model_name
            animations[short_name] = animation_id
            animate = {short_name: item['condition']} if item['condition'] else short_name
            if scripted:
                scripts.setdefault('animate', []).append(animate)
            else:
                controller_animations.append(animate)
        if result['pre_animation']:
            lines = result['pre_animation']
            if item['condition'] and several_models:
                # Only the script of the variant being drawn runs.
                lines = ['(%s) ? { %s };' % (item['condition'], ' '.join(lines))]
            item['report']['invalid_molang'] = [line[:200] for line in lines if not molang_valid(line)][:5]
            scripts.setdefault('pre_animation', []).extend(lines)
        if result['initialize']:
            if scripted:
                scripts.setdefault('initialize', []).extend(result['initialize'])
            else:
                initial_values.extend(result['initialize'])
    if initial_values:
        # Ahead of the models' own lines, so their first frame already reads the initial values.
        scripts.setdefault('pre_animation', []).insert(vanilla_line_count, _run_once(initial_values))
    if keys_needing_controller:
        files += _rewrite_render_controllers(description, entity_name, geometry, reference, staged)
    if animation_file['animations']:
        animation_path = 'animations/bct_cem_%s.animation.json' % entity_name
        write_json(staged / animation_path, animation_file)
        files.append(_staged_file(animation_path, ALL_RENDERERS))
    if controller_animations:
        controller_id, controller_file = _write_animation_controller(entity_name, controller_animations, staged)
        files.append(controller_file)
        description.setdefault('animation_controllers', []).append({'bct_cem_controller': controller_id})
    original = entry['description']
    for section in ('geometry', 'textures', 'animations', 'scripts'):
        if section not in original and not description.get(section):
            description.pop(section, None)
    if document != entry['document']:
        # Only a description that now points at converted geometry, animations or textures is restated.
        entity_path = 'entity/' + entry['file']
        write_json(staged / entity_path, document)
        files.append(_staged_file(entity_path, ALL_RENDERERS))
    return files


def _rewrite_render_controllers(description, entity_name, geometry, reference, staged):
    """Restate the entity's climate render controllers to pick each variant's geometry by v.index.

    Updates the description's controller list; returns the staged files.
    """
    files = []
    rewritten = []
    for controller in description.get('render_controllers', []):
        name = controller if isinstance(controller, str) else next(iter(controller))
        condition = None if isinstance(controller, str) else controller[name]
        source = reference.render_controllers.get(name)
        if not (source and 'geometry' in source and 'v.index' in json.dumps(source)):
            rewritten.append(controller)
            continue
        new_name = 'controller.render.bct.' + entity_name
        path = 'render_controllers/bct_%s.render_controllers.json' % entity_name
        body = _climate_geometry_controller(source, geometry)
        # The copy keeps the format of the vanilla file it comes from, whose keys it carries.
        format_version = reference.render_controller_formats.get(name) or '1.8.0'
        write_json(staged / path, {'format_version': format_version, 'render_controllers': {new_name: body}})
        files.append(_staged_file(path, ALL_RENDERERS))
        rewritten.append(new_name if condition is None else {new_name: condition})
    description['render_controllers'] = rewritten
    return files


def _climate_geometry_controller(source, geometry):
    """A copy of a vanilla render controller that picks the geometry from arrays indexed by v.index."""
    body = copy.deepcopy(source)
    arrays = body.setdefault('arrays', {}).setdefault('geometries', {})
    # A variant without a geometry of its own uses the default (or baby) geometry.
    arrays['Array.bct_geos'] = ['Geometry.' + (key if key in geometry else 'default') for key in CLIMATE_KEYS]
    if 'baby' in geometry:
        arrays['Array.bct_baby_geos'] = ['Geometry.' + (key if key in geometry else 'baby')
                                         for key in BABY_CLIMATE_KEYS]
        body['geometry'] = 'query.is_baby ? Array.bct_baby_geos[v.index] : Array.bct_geos[v.index]'
    else:
        body['geometry'] = 'Array.bct_geos[v.index]'
    return body


def _write_animation_controller(entity_name, played_animations, staged):
    """Stage an animation controller whose one state plays the converted models' animations.

    Returns (controller identifier, staged file).
    """
    controller_id = 'controller.animation.bct.cem.' + entity_name
    path = 'animation_controllers/bct_cem_%s.animation_controllers.json' % entity_name
    controller = {'initial_state': 'default', 'states': {'default': {'animations': played_animations}}}
    write_json(staged / path, {'format_version': ANIMATION_CONTROLLER_FORMAT,
                               'animation_controllers': {controller_id: controller}})
    return controller_id, _staged_file(path, ALL_RENDERERS)


def _run_once(statements):
    """One pre_animation statement that runs statements on the entity's first frame only."""
    # The flag was never set before the first frame, so it reads as 0 there.
    return '(!%s) ? { %s %s = 1; };' % (INITIALIZED_FLAG, ' '.join(statements), INITIALIZED_FLAG)


def _write_textures(stack, planner, staged):
    """Build and stage every planned Bedrock texture; returns (textures report, staged files)."""
    files = []
    outputs = []

    def read(path):
        if path is None:
            return None
        return stack.read(path) if path in stack.files else None

    for target, output in sorted(planner.outputs.items()):
        vanilla, _ = planner.vanilla_bedrock(output.get('reference') or target)
        built = build_output(output, read, vanilla)
        if built is None:
            continue
        extension = output['extension'] or '.png'
        files += _write_texture_files(staged, target, extension, built)
        outputs.append({'target': target, 'extension': extension, 'recipe': output['recipe']['kind'],
                        'sources': output['sources'], 'evidence': output['evidence'],
                        'alpha_codes_kept': output.get('alpha_codes'),
                        'pbr': [channel for channel in ('normal', 'mers') if built[channel] is not None],
                        'material_losses': built['losses']})
    counts = {}
    for record in planner.records:
        counts[record['status']] = counts.get(record['status'], 0) + 1
    report = {'authored': len(planner.records), 'counts': counts, 'outputs': outputs,
              'records': planner.records, 'bedrock_textures_written': len(outputs)}
    return report, files


def _write_texture_files(staged, target, extension, built):
    """Write a texture's colour image, PBR maps and texture set; returns the staged files."""
    color_path = target + extension
    (staged / color_path).parent.mkdir(parents=True, exist_ok=True)
    (staged / color_path).write_bytes(encode(built['color'], extension))
    files = [_staged_file(color_path, ALL_RENDERERS)]
    folder = Path(target).parent
    stem = Path(target).name
    texture_set = {'color': stem}
    for channel, suffix, layer in (('normal', '_bct_normal', 'normal'),
                                   ('mers', '_bct_mers', 'metalness_emissive_roughness_subsurface')):
        if built[channel] is None:
            continue
        name = stem + suffix
        built[channel].save(staged / (str(folder / name) + '.png'))
        texture_set[layer] = name
        files.append(_staged_file(str(folder / name).replace('\\', '/') + '.png', PBR_RENDERERS))
    set_path = target + '.texture_set.json'
    write_json(staged / set_path, {'format_version': '1.21.30', 'minecraft:texture_set': texture_set})
    files.append(_staged_file(set_path, PBR_RENDERERS))
    return files
