"""Vanilla blocks drawn with the author's own Java block models (leaves first).

A Java pack can give a vanilla block models that are not a full cube and reach
past their block (a pack's bushy leaves). A Bedrock resource pack
cannot change a vanilla block's geometry, so the converter writes a custom
block carrying the author's models and the engine swaps the vanilla block for
it (engine/leaves.mjs). What the author's blockstate decides is kept: which
model each Java state shows (a pack can draw oak with one model while
persistent or within 4 blocks of a log and another farther out), the weighted
random choice (picked per position with Java's model random, a state the
engine sets) and each choice's rotation (minecraft:transformation, turning in
the same direction as Blockbench's conversion). Models are converted like
Blockbench (java_block_geometry) and shrunk only as far as Bedrock's 30 pixel
geometry limit needs, each model on its own; a block switches geometry by state.

Which vanilla blocks get model blocks, and how they behave (hardness, drops,
decay, tint per species), comes from data/native-replacement.json (model_blocks).
Drops come from the Java vanilla loot table of the block: shears give the leaf
block, otherwise saplings, sticks and apples with Fortune; Silk Touch is
handled by the engine because Bedrock drops a custom block's own item for it.
"""
from fnmatch import fnmatchcase
import json
from pathlib import PurePosixPath

from bedrock_schema import block_errors, culling_errors, geometry_errors
from block_gameplay import mining
from block_ids import JAVA_RENAMES
from java_block_geometry import _has_area, _scaled, cube, fit_scale, geometry_document, shaded

# Java block states a behavior's blocks have (leaves: persistent and the 1-7 log distance).
JAVA_STATES = {'leaves': [{'persistent': persistent, 'distance': str(distance), 'waterlogged': 'false'}
                          for persistent in ('false', 'true') for distance in range(1, 8)]}
SELECTIONS = {'variants': 'java-26.2-block-position', 'multipart': 'java-26.2-multipart-block-position'}
FACE_NAMES = ('north', 'south', 'east', 'west', 'up', 'down')
# Java's log distance runs from 1 to 7.
LEAF_DISTANCES = range(1, 8)
# Fortune levels a loot pool can name (Bedrock's enchantment levels, as in vanilla).
MAX_FORTUNE = 3


def plan_all(models, policy, known, java_of, vanilla):
    """Plans for every policy target whose blockstate uses models beyond a full cube; reasons for the rest.

    vanilla: the Java reference jar (zipfile) holding the vanilla loot tables.
    """
    plans, skipped = [], []
    for bedrock, behavior, settings in _policy_targets(policy, known):
        java = java_of(bedrock)
        try:
            item = plan_model_block(models, bedrock, java, behavior)
            item['java_loot'] = _java_loot(vanilla, java)
        except KeyError as error:
            skipped.append({'block': bedrock, 'reason': f'no Java file {error} (a block Java does not have)'})
            continue
        except ValueError as error:
            skipped.append({'block': bedrock, 'reason': str(error)})
            continue
        if all(_is_full_cube(model['elements']) for model in item['models'].values()):
            skipped.append({'block': bedrock, 'reason': 'the blockstate shows full cubes; the base pack draws them'})
            continue
        item['settings'] = settings
        plans.append(item)
    return plans, skipped


def plan_model_block(models, bedrock, java, behavior):
    """What a model block shows for every Java state.

    models: java_block_bindings._Models over the author stack and the vanilla
    jar. Returns {'looks': [[(model, y rotation) per weighted choice]],
    'look_weights': [[weight per choice] per look], 'look_of': {(persistent, distance): look index},
    'selection', 'models': {path: resolved model}, ...} or raises ValueError. Each look keeps its
    own weighted choices (a pack can weigh its models differently by log distance).
    """
    blockstate = _resource(java, 'blockstates')
    document = models.read(blockstate)
    looks, look_weights, look_of, selection = [], [], {}, None
    for states in JAVA_STATES[behavior]:
        parts, state_selection = _selected_parts(document, states)
        if len(parts) != 1:
            raise ValueError(f'{len(parts)} model parts at once for {states} (one part per state is supported)')
        choices = parts[0]
        selection = state_selection
        if any(choice['x'] or choice['uvlock'] for choice in choices):
            raise ValueError('x rotations and uvlock are not supported for model blocks yet')
        look = [(choice['model'], choice['y']) for choice in choices]
        weights = [choice['weight'] for choice in choices]
        # A look is its choices with their weights: the same models weighed differently are another look.
        known = next((index for index, (other, other_weights) in enumerate(zip(looks, look_weights))
                      if other == look and other_weights == weights), None)
        if known is None:
            looks.append(look)
            look_weights.append(weights)
            known = len(looks) - 1
        look_of[(states['persistent'] == 'true', int(states['distance']))] = known
    resolved = {}
    for look in looks:
        for path, _ in look:
            if path not in resolved:
                model = models.resolve(path)
                if not model.get('elements'):
                    raise ValueError(path + ' has no elements')
                resolved[path] = {'elements': model['elements'], 'textures': models.variables(path),
                                  'ambientocclusion': model.get('ambientocclusion', True)}
    return {'bedrock': bedrock, 'java': java, 'behavior': behavior, 'blockstate': blockstate,
            'authored': models.stack is not None and blockstate in models.stack.files,
            'looks': looks, 'look_weights': look_weights, 'look_of': look_of, 'selection': selection,
            'models': resolved}


def build(item, *, materials, namespace, schemas, known_items, sounds, display_name, tints, files):
    """Block definition, geometry, culling and loot for one planned model block.

    materials: native_replacement.Materials (exports the author's texture sets);
    files: {'bp': behavior pack folder, 'rp': resource pack folder, 'out':
    {path: document}}, the caller writing 'out' once everything validates.
    Returns (block definition, engine entry, report, vanilla block sound).
    """
    bedrock = item['bedrock']
    stem = 'm_' + bedrock.removeprefix('minecraft:')
    identifier = namespace + ':' + stem
    tint = _tint_method(item, tints)
    instance_names, instances = _material_instances(item, materials, tint)
    rp_folder, bp_folder = files['rp'], files['bp']
    geometries, report_models = _add_geometries(item, namespace, stem, instance_names, tint, rp_folder, files['out'])
    states, permutations = _states_and_permutations(item, geometries)
    loot_path = f'loot_tables/bct/{namespace}/{stem}.json'
    components = _block_components(item, geometries, instances, loot_path, display_name, tint)
    description = {'identifier': identifier, 'menu_category': {'category': 'none', 'is_hidden_in_commands': True},
                   'states': states}
    definition = {'format_version': '1.26.50', 'minecraft:block': {
        'description': description, 'components': components, 'permutations': permutations}}

    def item_id(java):
        return _item_id(java, known_items)

    loot = bedrock_loot(item['java_loot'], item_id, item_id(item['java']))
    problems = block_errors(definition, schemas)
    for path, document in files['out'].items():
        if path.name.startswith(f'{namespace}_{stem}_'):
            if 'block_culling' in path.parts:
                problems += culling_errors(document, schemas)
            else:
                problems += geometry_errors(document)
    if problems:
        raise ValueError("fails Mojang's block schema: " + '; '.join(problems[:5]))
    files['out'][bp_folder / f'blocks/{stem}.json'] = definition
    files['out'][bp_folder / loot_path] = loot
    entry = _engine_entry(item, identifier)
    looks, weights = item['looks'], item['look_weights']
    report = {'block': bedrock, 'replacement': identifier, 'java_blockstate': item['blockstate'],
              'blockstate_from': 'author' if item['authored'] else 'vanilla', 'models': report_models,
              'tint_method': tint or 'none', 'states': sorted(states), 'permutations': len(permutations),
              'looks': [[{'model': path, 'y': y} for path, y in look] for look in looks],
              'turn_weights': weights if len(weights) > 1 else weights[0], 'selection': item['selection']}
    sound = sounds.get(bedrock.removeprefix('minecraft:'), {}).get('sound')
    return definition, entry, report, sound


def bedrock_loot(java_table, item_ids, leaf_item):
    """A Bedrock loot table for a Java block loot table (the vanilla leaves form).

    Java gives the block itself for shears or Silk Touch, otherwise items with
    chances by Fortune level (table_bonus). Bedrock loot tables have no
    "otherwise": the shears pool gives the block, the other pools roll their base
    chance plus, per Fortune level, the extra chance that makes the total match
    Java. The engine removes the other drops when shears are used, and turns the
    custom block item Bedrock drops for Silk Touch into the vanilla block.
    item_ids(java item) gives the Bedrock item.
    """
    pools = [{'rolls': 1, 'conditions': [{'condition': 'match_tool', 'item': 'minecraft:shears'}],
              'entries': [{'type': 'item', 'name': leaf_item}]}]
    for pool in java_table.get('pools', []):
        entries = []
        for entry in pool.get('entries', []):
            entries += entry['children'] if entry.get('type', '').endswith('alternatives') else [entry]
        for entry in entries:
            conditions = {condition['condition'].removeprefix('minecraft:'): condition
                          for condition in _loot_conditions(entry)}
            if 'any_of' in conditions:
                continue  # the block itself for shears or Silk Touch
            item = item_ids(entry.get('name'))
            functions = _bedrock_loot_functions(entry, conditions)
            unknown = set(conditions) - {'survives_explosion', 'table_bonus'}
            if unknown:
                raise ValueError('Java loot conditions have no Bedrock form: ' + ', '.join(sorted(unknown)))
            chances = conditions.get('table_bonus', {}).get('chances', [1.0])
            if (conditions.get('table_bonus')
                    and conditions['table_bonus']['enchantment'].removeprefix('minecraft:') != 'fortune'):
                raise ValueError('table_bonus for an enchantment other than Fortune')
            result = {'type': 'item', 'name': item}
            if functions:
                result['functions'] = functions
            pools += _chance_pools(result, chances)
    return {'pools': [pool for pool in pools if pool['entries']]}


def _policy_targets(policy, known):
    """(Bedrock block, behavior, settings) for every vanilla block a model_blocks policy entry names."""
    result = []
    for entry in policy.get('model_blocks', []):
        for block in sorted(name for name in known if any(fnmatchcase(name, pattern) for pattern in entry['blocks'])):
            result.append((block, entry['behavior'], entry))
    return result


def _resource(identifier, kind):
    """The jar path of a namespaced Java resource: a blockstate or model (.json) or a texture (.png)."""
    namespace, name = identifier.split(':', 1) if ':' in identifier else ('minecraft', identifier)
    if '..' in PurePosixPath(name).parts:
        raise ValueError('Unsafe resource identifier: ' + identifier)
    return f'assets/{namespace}/{kind}/{name}' + ('.json' if kind != 'textures' else '.png')


def _weighted_choices(apply):
    """A blockstate apply/variant entry as a list of weighted choices."""
    result = []
    for item in apply if isinstance(apply, list) else [apply]:
        weight = item.get('weight', 1)
        if type(weight) is not int or weight < 1:
            raise ValueError('Invalid Java model weight')
        result.append({'model': _resource(item['model'], 'models'), 'x': int(item.get('x', 0)) % 360,
                       'y': int(item.get('y', 0)) % 360, 'uvlock': bool(item.get('uvlock', False)), 'weight': weight})
    return result


def _variant_matches(key, states):
    """Whether a blockstate variant key ("a=1,b=2", "" for every state) matches the states."""
    for item in [part for part in key.split(',') if part]:
        name, _, value = item.partition('=')
        if str(states.get(name)) != value:
            return False
    return True


def _multipart_matches(condition, states):
    """Whether a multipart "when" condition (with OR / AND lists and a|b|!c values) matches the states."""
    for name, expected in condition.items():
        if name in ('OR', 'AND'):
            values = [_multipart_matches(item, states) for item in expected]
            if not (any(values) if name == 'OR' else all(values)):
                return False
            continue
        if name not in states:
            raise ValueError('Java state property not modelled: ' + name)
        expected = str(expected).lower() if isinstance(expected, bool) else str(expected)
        negate = expected.startswith('!')
        found = str(states[name]) in (expected[1:] if negate else expected).split('|')
        if found == negate:
            return False
    return True


def _selected_parts(document, states):
    """The model parts a Java blockstate shows for one state: ([choices per part], selection algorithm)."""
    if 'multipart' in document:
        parts = [_weighted_choices(part['apply']) for part in document['multipart']
                 if _multipart_matches(part.get('when', {}), states)]
        return parts, SELECTIONS['multipart']
    matches = [value for key, value in document.get('variants', {}).items() if _variant_matches(key, states)]
    if len(matches) != 1:
        raise ValueError(f'{len(matches)} blockstate variants match {states}')
    return [_weighted_choices(matches[0])], SELECTIONS['variants']


def _java_loot(vanilla, java):
    """Java's vanilla loot table for a block from the reference jar."""
    name = java.split(':', 1)[1]
    return json.loads(vanilla.read(f'data/minecraft/loot_table/blocks/{name}.json'))


def _is_full_cube(elements):
    return (len(elements) == 1 and elements[0].get('from') == [0, 0, 0] and elements[0].get('to') == [16, 16, 16]
            and not any((elements[0].get('rotation') or {}).get(key) for key in ('angle', 'x', 'y', 'z')))


def _drawn_faces(element):
    """Faces of an element that have an area (Java draws nothing for the others)."""
    low, high = element['from'], element['to']
    size = [high[axis] - low[axis] for axis in range(3)]
    return {face: data for face, data in element.get('faces', {}).items()
            if face in FACE_NAMES and _has_area(face, size)}


def _tint_method(item, document_tints):
    """The Bedrock tint_method for a block's tinted faces, from the policy and the Java color provider."""
    settings = item['settings']
    if item['bedrock'] in settings.get('tint_methods', {}):
        return settings['tint_methods'][item['bedrock']]
    kind = document_tints.get(item['bedrock'], document_tints.get(item['java']))
    if kind == 'foliage':
        return 'default_foliage'
    if kind == 'grass':
        return 'grass'
    if kind is None:
        return None
    raise ValueError(f'tint {kind} has no Bedrock tint method')


def _face_texture(model, data):
    reference = data.get('texture', '')
    if reference.startswith('#'):
        texture = model['textures'].get(reference.lstrip('#'))
    else:
        texture = _resource(reference, 'textures')
    if not texture:
        raise ValueError('a drawn face has no texture: ' + reference)
    return texture


def _material_key(model, element, data, tint):
    """What gives a face its own material instance: texture, tinted, face dimming, ambient occlusion."""
    return (_face_texture(model, data), tint is not None and data.get('tintindex', -1) >= 0,
            shaded(element), bool(model['ambientocclusion']))


def _material_instances(item, materials, tint):
    """({material key: instance name}, {instance name: material instance}) for every drawn face.

    The most used material is the block's "*" instance; the others are m1, m2...
    """
    uses = {}
    for model in item['models'].values():
        for element in model['elements']:
            for data in _drawn_faces(element).values():
                key = _material_key(model, element, data, tint)
                uses[key] = uses.get(key, 0) + 1
    by_use = sorted(uses, key=lambda key: -uses[key])
    names = {key: '*' if index == 0 else f'm{index}' for index, key in enumerate(by_use)}
    instances = {}
    for key, name in names.items():
        texture, tinted, shade, occlusion = key
        # Java draws leaves cut out on both sides; Bedrock wants one render method per block.
        instance = {'texture': materials.alias(texture), 'render_method': 'alpha_test',
                    'ambient_occlusion': 1.0 if occlusion else 0.0, 'face_dimming': shade}
        if tinted:
            instance['tint_method'] = tint
        instances[name] = instance
    return names, instances


def _model_geometry(identifier, model, material_of):
    """One geometry with the model's elements on one bone, fitted to Bedrock's limit.

    Returns (geometry document, scale, culling parts [(bone, face, direction, cube index)]).
    """
    # Elements without a face that has an area draw nothing in Java; leaving them out keeps
    # the fit as large as it can be.
    elements = [element for element in model['elements'] if _drawn_faces(element)]
    scale = fit_scale(elements, [(0, 0)])
    cubes, culls = [], []
    for index, element in enumerate(elements):
        drawn = _drawn_faces(element)
        shaped = _scaled({**element, 'faces': drawn}, scale)

        def instance_of(face, data, element=element, drawn=drawn):
            return material_of(element, face, drawn[face])

        cubes.append(cube(shaped, instance_of))
        for face, data in drawn.items():
            if data.get('cullface') in FACE_NAMES:
                culls.append(('model', face, data['cullface'], index))
    document = geometry_document(identifier, [{'name': 'model', 'pivot': [0, 8, 0], 'cubes': cubes}])
    return document, scale, culls


def _add_geometries(item, namespace, stem, instance_names, tint, rp_folder, out):
    """Adds each model's geometry (and culling) file to out ({path: document}).

    Returns ({model path: minecraft:geometry component}, report entries).
    """
    geometries, report_models = {}, []
    for index, (path, model) in enumerate(sorted(item['models'].items())):
        def material_of(element, face, data, model=model):
            name = instance_names[_material_key(model, element, data, tint)]
            return None if name == '*' else name

        geometry_id = f'geometry.{namespace}.{stem}.{index}'
        document, scale, culls = _model_geometry(geometry_id, model, material_of)
        culling_id = f'{namespace}:{stem}_{index}_culling'
        culling = {'format_version': '1.21.80', 'minecraft:block_culling_rules': {
            'description': {'identifier': culling_id},
            'rules': [{'geometry_part': {'bone': bone, 'cube': cube_index, 'face': face}, 'direction': direction}
                      for bone, face, direction, cube_index in culls]}}
        out[rp_folder / f'models/blocks/{namespace}_{stem}_{index}.geo.json'] = document
        component = {'identifier': geometry_id}
        if culls:
            out[rp_folder / f'block_culling/{namespace}_{stem}_{index}.json'] = culling
            component['culling'] = culling_id
        geometries[path] = component
        report_models.append({'model': path, 'fit_scale': round(scale, 3), 'geometry': geometry_id,
                              'elements': len(document['minecraft:geometry'][0]['bones'][0]['cubes']),
                              'culled_faces': len(culls)})
    return geometries, report_models


def _states_and_permutations(item, geometries):
    """The block's states and one permutation per look and weighted turn.

    bct:persistent_bit and bct:update_bit mirror the vanilla leaf states;
    bct:t is the weighted pick and bct:look the model the Java states choose
    (each only when there is more than one).
    """
    looks = item['looks']
    choices = max(len(look) for look in looks)
    states = {'bct:persistent_bit': {'values': {'min': 0, 'max': 1}},
              'bct:update_bit': {'values': {'min': 0, 'max': 1}}}
    if choices > 1:
        states['bct:t'] = {'values': {'min': 0, 'max': choices - 1}}
    if len(looks) > 1:
        states['bct:look'] = {'values': {'min': 0, 'max': len(looks) - 1}}
    permutations = []
    for look_index, look in enumerate(looks):
        for turn, (path, y) in enumerate(look):
            terms = []
            if len(looks) > 1:
                terms.append(f"q.block_state('bct:look') == {look_index}")
            if choices > 1:
                terms.append(f"q.block_state('bct:t') == {turn}")
            components = {'minecraft:geometry': geometries[path]}
            if y:
                # Java turns a model clockwise seen from above; Blockbench's conversion negates the angle.
                components['minecraft:transformation'] = {'rotation': [0, (360 - y) % 360, 0]}
            permutations.append({'condition': ' && '.join(terms) or '1.0', 'components': components})
    return states, permutations


def _block_components(item, geometries, instances, loot_path, display_name, tint):
    bedrock, settings = item['bedrock'], item['settings']
    first_model = item['looks'][0][0][0]
    particle = instances['*']
    return {
        'minecraft:geometry': geometries[first_model],
        'minecraft:material_instances': instances,
        'minecraft:collision_box': True, 'minecraft:selection_box': True,
        'minecraft:light_dampening': settings['light_dampening'],
        # Shears and swords mine leaves faster without a block tag: their hardness is the
        # block's over Java's tool speed.
        'minecraft:destructible_by_mining': mining(settings, settings.get('item_speeds', [])),
        'minecraft:destructible_by_explosion': {'explosion_resistance': settings['resistance']},
        'minecraft:flammable': {'catch_chance_modifier': settings['flammable'][0],
                                'destroy_chance_modifier': settings['flammable'][1],
                                **({'lava_flammable': 'always'} if settings.get('ignited_by_lava') else {})},
        'minecraft:map_color': _map_color(settings, bedrock, tint),
        'minecraft:loot': loot_path,
        'minecraft:tags': sorted(settings['tags']),
        'minecraft:display_name': display_name,
        'minecraft:destruction_particles': {'texture': particle['texture'],
                                            'tint_method': particle.get('tint_method', 'none')},
        'minecraft:liquid_detection': {'detection_rules': [{'liquid_type': 'water', 'can_contain_liquid': True,
                                                            'on_liquid_touches': 'blocking'}]},
        'minecraft:precipitation_interactions': {'precipitation_behavior': 'obstruct_rain_accumulate_snow'},
        'bct:leaf': {},
    }


def _engine_entry(item, identifier):
    """The engine's data for a model block: the mirrored leaf states, the weighted turn and the look by state."""
    looks, look_weights = item['looks'], item['look_weights']
    entry = {'vanilla': item['bedrock'], 'block': identifier,
             'mirror': {'persistent_bit': 'bct:persistent_bit', 'update_bit': 'bct:update_bit'},
             'bools': ['persistent_bit', 'update_bit']}
    if max(len(weights) for weights in look_weights) > 1:
        entry['turn'] = {'state': 'bct:t', 'weights': look_weights[0], 'selection': item['selection']}
        if any(weights != look_weights[0] for weights in look_weights):
            # The pick uses the weights of the leaf's look (by persistence and log distance).
            entry['turn']['byLook'] = look_weights
    if len(looks) > 1:
        # Rows: not persistent, then persistent; columns: log distance 1 to 7.
        table = [[item['look_of'][(persistent, distance)] for distance in LEAF_DISTANCES]
                 for persistent in (False, True)]
        entry['look'] = {'state': 'bct:look', 'table': table}
    return entry


def _loot_conditions(entry):
    """A Java loot entry's conditions as [{'condition': name, ...}], from either loot table format.

    Before Java 26 an entry lists `conditions`, each named by `condition`; from
    26 on it has one `condition` named by `type`, all_of holding several, and a
    term may be a named predicate (a string such as minecraft:tool/can_shear).
    """
    found = list(entry.get('conditions', []))
    single = entry.get('condition')
    if single is not None:
        found += single.get('terms', []) if single.get('type', '').removeprefix('minecraft:') == 'all_of' else [single]
    result = []
    for condition in found:
        if isinstance(condition, str):
            result.append({'condition': 'predicate', 'id': condition})
        elif 'type' in condition and 'condition' not in condition:
            result.append({'condition': condition['type'], **{k: v for k, v in condition.items() if k != 'type'}})
        else:
            result.append(condition)
    return result


def _loot_functions(entry):
    """A Java loot entry's functions as [{'function': name, ...}]: `functions` before Java 26, `modifier` from 26 on."""
    modifiers = entry.get('modifier', [])
    modifiers = modifiers if isinstance(modifiers, list) else [modifiers]
    return list(entry.get('functions', [])) + [
        {'function': item['type'], **{k: v for k, v in item.items() if k != 'type'}} for item in modifiers]


def _bedrock_loot_functions(entry, conditions):
    """A Java loot entry's functions in Bedrock form; survives_explosion becomes explosion_decay."""
    functions = []
    for function in _loot_functions(entry):
        kind = function['function'].removeprefix('minecraft:')
        if kind == 'set_count':
            count = function['count']
            if isinstance(count, dict):
                count = {'min': int(count['min']), 'max': int(count['max'])}
            else:
                count = int(count)
            functions.append({'function': 'set_count', 'count': count})
        elif kind == 'explosion_decay':
            functions.append({'function': 'explosion_decay'})
        else:
            raise ValueError('Java loot function has no Bedrock form: ' + kind)
    if 'survives_explosion' in conditions and {'function': 'explosion_decay'} not in functions:
        functions.append({'function': 'explosion_decay'})
    return functions


def _chance_pools(result, chances):
    """The pool rolling Java's base chance, and per Fortune level a pool adding the chance Fortune adds."""
    base = chances[0]
    pools = [{'rolls': 1, 'conditions': [{'condition': 'random_chance', 'chance': round(base, 9)}] if base < 1 else [],
              'entries': [result]}]
    for level in range(1, min(len(chances), MAX_FORTUNE + 1)):
        # Rolling the base pool and this one gives Java's chance at this level: 1 - (1 - base)(1 - extra).
        extra = 1 - (1 - chances[level]) / (1 - base) if base < 1 else 0
        if extra > 0:
            fortune = {'condition': 'match_tool',
                       'enchantments': [{'enchantment': 'fortune', 'levels': {'range_min': level, 'range_max': level}}]}
            chance = {'condition': 'random_chance', 'chance': round(extra, 9)}
            pools.append({'rolls': 1, 'conditions': [fortune, chance], 'entries': [result]})
    return pools


def _item_id(java, known_items):
    """The Bedrock item for a Java item id (block items take the block rename)."""
    if java in known_items:
        return java
    renamed = JAVA_RENAMES.get(java)
    if isinstance(renamed, str) and renamed in known_items:
        return renamed
    raise ValueError('no Bedrock item for ' + java)


def _map_color(settings, bedrock, tint):
    color = settings.get('map_colors', {}).get(bedrock, settings['map_color'])
    return {'color': color, 'tint_method': tint} if tint else color
