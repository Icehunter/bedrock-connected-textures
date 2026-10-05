"""Decide which blocks get entity-carried connected textures.

Entity carriers cost memory and frame time, so they are kept for a data-driven
allowlist of building blocks (glass, planks, bookshelves, bricks, sandstone and
similar). Common terrain blocks (stone, dirt, grass, sand, logs, leaves, wool,
terracotta, concrete...) never get carriers: their unconditional random rules
become native terrain-atlas variations instead, and other rules keep only the
native preview the converter already exports. Every other block also stays
native; rules on those blocks are drawn by native replacement blocks where
possible (native_replacement.py). A warning is reported when a pack needs more
carrier types than the budget allows.
"""
from fnmatch import fnmatchcase
import json
from pathlib import Path


DEFAULT_BUDGET = Path(__file__).resolve().parent / 'data/carrier-budget.json'
# Rule fields that make a random rule conditional; atlas variations always apply.
_CONDITION_FIELDS = ('biomes', 'heights', 'states', 'blockMatchers')
# "Draw nothing" and "draw the base texture" tiles have no image to add to the atlas.
_PLACEHOLDER_TILES = {'<skip>', '<default>'}
_ALL_FACES = ['north', 'east', 'south', 'west', 'up', 'down']


def load_budget(path=None):
    """Read and check a budget file; the bundled one when no path is given."""
    budget = json.loads(Path(path or DEFAULT_BUDGET).read_text(encoding='utf-8'))
    if not isinstance(budget.get('carrier_types'), int) or budget['carrier_types'] < 0:
        raise ValueError('carrier_types must be a nonnegative integer')
    for field in ('native_only', 'entity_allowlist'):
        patterns = budget.get(field)
        if not isinstance(patterns, list) or any(not isinstance(pattern, str) for pattern in patterns):
            raise ValueError(field + ' must be a list of block patterns')
    return budget


def carried(block, budget):
    """Whether a block may use entity carriers.

    carrier_fallback lists blocks native replacement must not swap; they keep
    entity carriers even when native_only names them.
    """
    if _matches_any(block, budget.get('carrier_fallback', [])):
        return True
    return _matches_any(block, budget['entity_allowlist']) and not _matches_any(block, budget['native_only'])


def rule_targets(rule, document):
    """Blocks a rule can draw on: its listed blocks plus the blocks whose faces show a matched texture."""
    targets = set(rule.get('blocks', []))
    matched_textures = set(rule.get('matchTiles', []))
    if matched_textures:
        targets.update(_blocks_showing(matched_textures, document))
    return targets


def apply_budget(document, budget, native=(), random_mosaic=None):
    """Split a rules document into carrier rules and native variations.

    Returns (document, variations, report); the input document is not changed.
    native lists blocks drawn by native replacement blocks, which carriers
    leave alone. Unconditional random rules become native atlas variations,
    which draw in every graphics mode, instead of carriers. So do repeat
    mosaics that random_mosaic(rule, targets) accepts (mosaic_random.py):
    mosaics on blocks that are never swapped, whose tiles look the same at random.
    """
    native = set(native)
    kept_rules = []
    dropped_rules = []
    variations = {}
    uncarried = {}
    for rule in document['rules']:
        targets = rule_targets(rule, document)
        as_variations = _is_variation_rule(rule) or (
            random_mosaic is not None and _is_plain_mosaic(rule) and random_mosaic(rule, targets))
        textures = _variation_textures(rule, document, targets) if as_variations else []
        if textures:
            _add_variations(variations, rule, textures)
            dropped_rules.append({**_rule_summary(rule, targets), 'native_variation': True})
            continue
        allowed = sorted(block for block in targets - native if carried(block, budget))
        if len(allowed) < len(targets) or not targets:
            uncarried[rule['id']] = sorted(targets - set(allowed) - native)
        if allowed:
            kept_rules.append(_narrowed_rule(rule, targets, allowed))
            continue
        native_only = sorted(block for block in targets if _matches_any(block, budget['native_only']))
        dropped_rules.append({**_rule_summary(rule, targets), 'native_only': native_only})
    result = _carried_document(document, kept_rules, budget, native)
    report = _budget_report(budget, kept_rules, dropped_rules, variations, native, uncarried)
    return result, variations, report


def _matches_any(block, patterns):
    return any(fnmatchcase(block, pattern) for pattern in patterns)


def _blocks_showing(textures, document):
    """Blocks with a face, or a model variant face, that shows one of the textures."""
    for block, faces in document.get('baseTextures', {}).items():
        if textures.intersection(faces.values()):
            yield block
    for block, variants in document.get('baseTextureVariants', {}).items():
        for variant in variants:
            for choice in variant.get('modelChoices', [variant]):
                if textures.intersection(choice.get('faces', {}).values()):
                    yield block


def _is_variation_rule(rule):
    """Unconditional random rules with real tiles can become native atlas variations."""
    if rule['method'] != 'random':
        return False
    if any(rule.get(field) for field in _CONDITION_FIELDS):
        return False
    return not _PLACEHOLDER_TILES.intersection(rule['tiles'])


def _is_plain_mosaic(rule):
    """Unconditional repeat rules with real tiles; whether they look right at random is checked separately."""
    if rule['method'] != 'repeat':
        return False
    if any(rule.get(field) for field in _CONDITION_FIELDS):
        return False
    return not _PLACEHOLDER_TILES.intersection(rule['tiles'])


def _variation_textures(rule, document, targets):
    """Textures a variation rule swaps: its matched tiles, or what its blocks show on its faces."""
    if rule.get('matchTiles'):
        return rule['matchTiles']
    faces = rule.get('faces', _ALL_FACES)
    base_textures = document.get('baseTextures', {})
    return sorted({texture
                   for block in targets
                   for face, texture in base_textures.get(block, {}).items()
                   if face in faces})


def _add_variations(variations, rule, textures):
    """The first rule to claim a texture wins; zero-weight tiles never draw."""
    weights = rule.get('weights') or [1] * len(rule['tiles'])
    for texture in textures:
        weighted_tiles = [[tile, weight] for tile, weight in zip(rule['tiles'], weights) if weight > 0]
        variations.setdefault(texture, weighted_tiles)


def _rule_summary(rule, targets):
    return {'rule': rule['id'], 'filename': rule.get('filename'), 'method': rule['method'],
            'targets': sorted(targets)}


def _narrowed_rule(rule, targets, allowed):
    """Limit a kept rule to the blocks that may carry it, copying instead of editing the rule."""
    if rule.get('matchTiles'):
        if len(allowed) < len(targets):
            return {**rule, 'blocks': allowed}
        return rule
    if set(allowed) == set(rule.get('blocks', [])):
        return rule
    narrowed = {**rule, 'blocks': allowed}
    if 'blockMatchers' in narrowed:
        narrowed['blockMatchers'] = [clause for clause in narrowed['blockMatchers'] if clause['block'] in allowed]
    return narrowed


# Bytes of state data one block may add to the packet a converted pack sends to the engine.
MAX_BLOCK_DATA = 65536


def _keeps_carrier(block, budget, native):
    return carried(block, budget) and block not in native


def _carried_document(document, kept_rules, budget, native):
    """The document with only the kept rules and the carried blocks' source data."""
    result = {**document, 'rules': kept_rules}
    result['sourceBlocks'] = [block for block in document.get('sourceBlocks', [])
                              if _keeps_carrier(block, budget, native)]
    # The engine reads face textures for the blocks it draws, and for neighbours only to see whether
    # they show the same texture. Every other block's entry is dead weight in the packet the pack
    # sends at world load (all blocks made it about 2 MB, and the engine stalled taking it in).
    variants = {block: entries for block, entries in document.get('baseTextureVariants', {}).items()
                if _keeps_carrier(block, budget, native)}
    # A block whose state data alone is too big to send at world load stays vanilla (the chiseled
    # bookshelf's 256 states with model parts came to 1.7 MB and stalled the engine on load).
    too_large = {block for block, entries in variants.items() if len(json.dumps(entries)) > MAX_BLOCK_DATA}
    variants = {block: entries for block, entries in variants.items() if block not in too_large}
    result['sourceBlocks'] = [block for block in result['sourceBlocks'] if block not in too_large]
    drawn = set(result['sourceBlocks']) | set(variants)
    shown = set()
    for block in drawn:
        shown.update(document.get('baseTextures', {}).get(block, {}).values())
        for entry in variants.get(block, []):
            shown.update(entry.get('faces', {}).values())
    if 'baseTextures' in document:
        result['baseTextures'] = {block: faces for block, faces in document['baseTextures'].items()
                                  if block in drawn or shown & set(faces.values())}
    if 'baseTextureVariants' in document:
        result['baseTextureVariants'] = variants
    if 'customTintBlocks' in document:
        result['customTintBlocks'] = [block for block in document['customTintBlocks']
                                      if _keeps_carrier(block, budget, native)]
    return result


def _budget_report(budget, kept_rules, dropped_rules, variations, native, uncarried):
    carrier_rules = len(kept_rules)
    report = {'carrier_type_budget': budget['carrier_types'], 'carrier_rules': carrier_rules,
              'native_rules': len(dropped_rules), 'native_variations': len(variations),
              'dropped_rules': dropped_rules,
              'native_replacement_blocks': sorted(native),
              'uncarried_targets': uncarried,
              'over_budget': carrier_rules > budget['carrier_types']}
    if report['over_budget']:
        report['warning'] = (f'{carrier_rules} carrier rules exceed the budget of {budget["carrier_types"]}; '
                             'narrow entity_allowlist or raise carrier_types after testing performance in game.')
    return report
