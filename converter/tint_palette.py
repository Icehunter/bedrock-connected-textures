"""Find every tint colour each connected-texture rule can need, without making any art.

A rule's tiles are pre-tinted for each colour Java could draw them with: grass and
foliage colours per biome, fixed model tints and custom tints. The search follows every
face, block state and biome a rule can apply to, including rules that swap in another
rule's tiles, so a palette may hold a colour that is never used but never misses one.
"""
from collections import defaultdict, deque
import math

WHITE = (255, 255, 255)
FACES = ('north', 'east', 'south', 'west', 'up', 'down')
SPECIAL_TILES = ('<skip>', '<default>')


def rgb8(value):
    """A colour with channels from 0 to 1 as 8-bit RGB, rounded to nearest and clamped."""
    return tuple(max(0, min(255, math.floor(float(channel) * 255 + .5))) for channel in value)


def rule_tint_palettes(document, rules=None):
    """{rule id: palette} with white first; see tint_palette_scope."""
    return tint_palette_scope(document, rules)['palettes']


def tint_palette_scope(document, rules=None):
    """A safe palette per rule, with white at index zero, and how it was found.

    Face, block, known state and biome limits are combined. Replacement tiles keep their
    original model tint through every rule that can reach them. Every neighbour mask and
    every rule order counts as possible, so a palette can include an unused colour but
    never depends on a single test position.
    """
    rules = list(document.get('rules', []) if rules is None else rules)
    search = _TintSearch(document, rules)
    contexts = _face_contexts(document, rules)
    for block, state_key, face, texture, tint_index, logical in contexts:
        search.follow_face(block, state_key, face, texture, tint_index, logical)
    palettes = {rule_id: [[channel / 255 for channel in color] for color in [WHITE, *sorted(colors - {WHITE})]]
                for rule_id, colors in search.colors.items()}
    return {'palettes': palettes,
            'blocks': {rule_id: sorted(blocks) for rule_id, blocks in search.affected_blocks.items()},
            'contexts': len(contexts), 'biome_ids': len(search.biomes) - 1,
            'scope': 'Conservative face/state/biome reachability; includes all neighbor masks and replacement paths.'}


def _face_contexts(document, rules):
    """Every (block, states, face, texture, tint index, Java block) a model face can show."""
    aliases = document.get('nativeBlockJavaIds', {})
    full_cubes = set(document.get('fullCubeBlocks', []))
    contexts = set()
    blocks = (set(document.get('baseTextures', {})) | set(document.get('baseTextureVariants', {}))
              | set(document.get('sourceBlocks', [])) | {block for rule in rules for block in rule.get('blocks', [])})
    for block in blocks:
        base = document.get('baseTextures', {}).get(block, {})
        for variant in document.get('baseTextureVariants', {}).get(block, []) or [{}]:
            states = variant.get('states', {})
            state_key = tuple(sorted((name, tuple(sorted(_state_values(value)))) for name, value in states.items()))
            choices = [*variant.get('modelChoices', [variant]),
                       *[choice for group in variant.get('modelPartsGroups', [])
                         for choice in group.get('modelChoices', [])]]
            for choice in choices:
                logical = choice.get('javaBlock', variant.get('javaBlock', aliases.get(block, block)))
                if full_cubes and block not in full_cubes and not choice.get('modelParts'):
                    continue
                faces = {**dict.fromkeys(FACES, block), **base, **choice.get('faces', {})}
                for face, texture in faces.items():
                    tint_index = choice.get('tintIndices', {}).get(face, -1)
                    contexts.add((block, state_key, face, texture, tint_index, logical))
                for face, layers in choice.get('faceLayers', {}).items():
                    for layer in layers:
                        contexts.add((block, state_key, face, layer['texture'], layer.get('tintIndex', -1), logical))
                for part in choice.get('modelParts', []):
                    for face, data in part.get('faces', {}).items():
                        world_face = data.get('worldFace', face)
                        tint_index = data.get('tintIndex', -1)
                        contexts.add((block, state_key, world_face, data['texture'], tint_index, logical))
    return contexts


class _TintSearch:
    """Rules indexed for the search, and the colours found so far.

    Biomes are bits of an integer mask, so a colour's biomes can be combined and
    intersected cheaply as the search moves from rule to rule.
    """

    def __init__(self, document, rules):
        self.custom_tint_blocks = set(document.get('customTintBlocks', []))
        self.aliases = document.get('nativeBlockJavaIds', {})
        self.tint_types = document.get('modelTintTypes', {})
        self.tint_tables = {'grass': document.get('grassTints', {}),
                            'foliage': document.get('foliageTints', {}), **document.get('customTints', {})}
        self.biomes = sorted({'*'} | {name for table in self.tint_tables.values() for name in table}
                             | {name for rule in rules for name in rule.get('biomes', {}).get('ids', [])})
        self.biome_bits = {name: 1 << index for index, name in enumerate(self.biomes)}
        self.all_biomes = (1 << len(self.biomes)) - 1
        self.rules_by_block = defaultdict(list)
        self.rules_by_tile = defaultdict(list)
        self.fallbacks_by_tile = defaultdict(list)
        self.colors = {rule['id']: {WHITE} for rule in rules}
        self.affected_blocks = defaultdict(set)
        self.biome_masks = {}
        for rule in rules:
            selected = rule.get('biomes')
            mask = sum(self.biome_bits[name] for name in set(selected['ids'])) if selected else self.all_biomes
            self.biome_masks[rule['id']] = self.all_biomes ^ mask if selected and selected['exclude'] else mask
            if rule.get('fallback'):
                for tile in rule['tiles']:
                    self.fallbacks_by_tile[tile].append(rule)
            else:
                for block in rule.get('blocks', []):
                    self.rules_by_block[block].append(rule)
                for tile in rule.get('matchTiles', []):
                    self.rules_by_tile[tile].append(rule)

    def follow_face(self, block, state_key, face, texture, tint_index, logical):
        """Add the face's tint to every rule that can draw it, following tile replacements."""
        states = {key: list(value) for key, value in state_key}
        tint = self._tint_colors(block, logical, tint_index, logical)
        for rule in self.fallbacks_by_tile.get(texture, []):
            if self._applies(rule, block, states, face, texture, logical):
                self._add_colors(rule, block, tint, self.biome_masks[rule['id']])
        # (texture, biomes, is the face's own texture): the face's texture first, then each
        # tile a matching rule puts there, for the biomes where that rule applies. Rules that
        # match by block only see the face's own texture.
        queue = deque([(texture, self.all_biomes, True)])
        visited = defaultdict(int)
        while queue:
            current, mask, is_face_texture = queue.popleft()
            fresh = mask & ~visited[current, is_face_texture]
            if not fresh:
                continue
            visited[current, is_face_texture] |= fresh
            candidates = self.rules_by_tile[current]
            if is_face_texture:
                candidates = candidates + self.rules_by_block[block]
                if logical != block:
                    candidates = candidates + self.rules_by_block[logical]
            for rule in candidates:
                if not self._applies(rule, block, states, face, current, logical):
                    continue
                applicable = fresh & self.biome_masks[rule['id']]
                if not applicable:
                    continue
                if rule['method'].startswith('overlay'):
                    # An overlay draws over the face with its own tint, if it has one.
                    if rule.get('tintIndex', -1) >= 0:
                        tint_block = rule.get('tintBlock', block)
                        overlay_tint = self._tint_colors(block, tint_block, rule['tintIndex'], logical)
                        self._add_colors(rule, block, overlay_tint, applicable)
                else:
                    self._add_colors(rule, block, tint, applicable)
                    for tile in rule['tiles']:
                        if tile not in SPECIAL_TILES:
                            queue.append((tile, applicable, False))

    def _tint_colors(self, block, tint_block, tint_index, logical=None):
        """{rgb8 colour: biome mask} Java tints a face with; white everywhere when untinted."""
        logical = logical or self.aliases.get(block, block)
        custom = self.custom_tint_blocks
        is_custom = tint_block in custom or (tint_block == logical and block in custom)
        if tint_index < 0 and not is_custom:
            return {WHITE: self.all_biomes}
        kind = self.tint_types.get(tint_block, self.tint_types.get(block) if tint_block == logical else None)
        if kind is None and tint_block in ('minecraft:grass_block', 'minecraft:grass'):
            kind = 'grass'
        if isinstance(kind, (list, tuple)):
            return {rgb8(kind): self.all_biomes}
        if kind is None:
            return {WHITE: self.all_biomes}
        colors = defaultdict(int)
        for biome, value in self.tint_tables.get(kind, {}).items():
            colors[rgb8(value)] |= self.biome_bits[biome]
        return colors

    def _applies(self, rule, block, states, face, texture, logical):
        """Whether a rule can draw this face of this block in these states."""
        if face not in rule.get('faces', FACES):
            return False
        if rule.get('blocks') and block not in rule['blocks'] and logical not in rule['blocks']:
            return False
        if rule.get('matchTiles') and texture not in rule['matchTiles']:
            return False
        if not _compatible(rule.get('states', {}), states):
            return False
        return not rule.get('blockMatchers') or any(
            matcher['block'] in (block, logical) and _compatible(matcher['states'], states)
            for matcher in rule['blockMatchers'])

    def _add_colors(self, rule, block, tint, mask):
        for color, color_mask in tint.items():
            if mask & color_mask:
                self.colors[rule['id']].add(color)
                if color != WHITE:
                    self.affected_blocks[rule['id']].add(block)


def _state_values(value):
    values = value if isinstance(value, list) else [value]
    return {str(item).lower() if isinstance(item, bool) else str(item) for item in values}


def _compatible(required, available):
    """Whether states could match: a state the face does not pin down could be anything."""
    return all(name not in available or _state_values(value) & _state_values(available[name])
               for name, value in required.items())
