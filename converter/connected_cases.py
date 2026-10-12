"""Neighbour cases of a replacement block whose faces show Java ctm tiles.

A face's ctm tile depends on the neighbours at its four edges and the four
diagonals between them. Over the whole block these are the six side
neighbours and the twelve edge diagonals: 18 bits, more than the 16 bits of
state a block has. A diagonal only changes a tile when both of its sides join
and a face across the third axis shows, so at most four diagonals count at a
time and a block has 483 cases. Many cases look the same (a face whose
neighbour joins is hidden), so the block needs one state value per distinct
look of its six faces, not per case.

The engine works out a block's case key from its neighbours (`key`) and looks
up the look's index in the table the converter writes (`table`).
"""
import itertools
import json

from terrain_providers import ROOT

# The engine's side order (engine/replacement.mjs CONNECT_SIDES): bit n of a key is side n.
SIDES = {'north': (0, 0, -1), 'south': (0, 0, 1), 'west': (-1, 0, 0), 'east': (1, 0, 0), 'up': (0, 1, 0),
         'down': (0, -1, 0)}
SIDE_NAMES = list(SIDES)
# The neighbours on each face's texture up, right, down and left edges (engine/core.mjs FACE_EDGES).
FACE_EDGES = {'north': ['up', 'west', 'down', 'east'], 'east': ['up', 'north', 'down', 'south'],
              'south': ['up', 'east', 'down', 'west'], 'west': ['up', 'south', 'down', 'north'],
              'up': ['north', 'east', 'south', 'west'], 'down': ['south', 'east', 'north', 'west']}


def _axis(side):
    """The axis (0 x, 1 y, 2 z) of side index side."""
    return next(axis for axis in range(3) if SIDES[SIDE_NAMES[side]][axis])


# Pairs of sides on different axes, in order: bit 6 + n of a key is the diagonal of pair n.
DIAGONALS = [(first, second) for first, second in itertools.combinations(range(6), 2) if _axis(first) != _axis(second)]
# The state values a look index is split into: a low digit of 16 values, then higher digits.
DIGIT = 16


def key(joined, diagonals):
    """The case key of a block: joined is the set of side indices that join, diagonals the set of
    DIAGONALS indices whose block joins. Diagonals that cannot change a tile are left out."""
    value = sum(1 << side for side in joined)
    for index, (first, second) in enumerate(DIAGONALS):
        if index not in diagonals or first not in joined or second not in joined:
            continue
        third = 3 - _axis(first) - _axis(second)
        across = [side for side in range(6) if _axis(side) == third]
        if any(side not in joined for side in across):
            value |= 1 << (6 + index)
    return value


def cases():
    """Every case key a block can have."""
    found = set()
    for joined_bits in range(64):
        joined = {side for side in range(6) if joined_bits >> side & 1}
        relevant = [index for index in range(len(DIAGONALS)) if key(joined, {index}) != key(joined, set())]
        for count in range(len(relevant) + 1):
            for chosen in itertools.combinations(relevant, count):
                found.add(key(joined, set(chosen)))
    return sorted(found)


def _template_tiles():
    """The engine's tile number for each canonical mask, read from its own table."""
    source = (ROOT / 'engine/tile-template.mjs').read_text(encoding='utf-8')
    return {int(mask): tile for mask, tile in json.loads(source.split('Object.freeze(', 1)[1].split(');', 1)[0]).items()}


def _canonical(raw):
    mask = raw & 15
    for corner in range(4):
        edges = (1 << corner) | (1 << ((corner + 1) % 4))
        if mask & edges == edges and raw & (1 << (corner + 4)):
            mask |= 1 << (corner + 4)
    return mask


def face_tiles(case):
    """{face: 47-tile index, or None when the neighbour in front joins and the face is hidden}."""
    template = _template_tiles()
    filled = {SIDES[SIDE_NAMES[side]] for side in range(6) if case >> side & 1}
    for index, (first, second) in enumerate(DIAGONALS):
        if case >> (6 + index) & 1:
            filled.add(tuple(a + b for a, b in zip(SIDES[SIDE_NAMES[first]], SIDES[SIDE_NAMES[second]])))
    tiles = {}
    for face in SIDE_NAMES:
        if SIDES[face] in filled:
            tiles[face] = None
            continue
        edges = [SIDES[name] for name in FACE_EDGES[face]]
        raw = 0
        for bit, edge in enumerate(edges):
            if edge in filled:
                raw |= 1 << bit
            corner = tuple(a + b for a, b in zip(edge, edges[(bit + 1) % 4]))
            if corner in filled:
                raw |= 1 << (bit + 4)
        tiles[face] = template[_canonical(raw)]
    return tiles


def table():
    """(looks, {case key: look index}): each distinct look of the six faces once, in a stable order."""
    looks, index_of, by_case = [], {}, {}
    for case in cases():
        tiles = face_tiles(case)
        look = tuple(tiles[face] for face in SIDE_NAMES)
        if look not in index_of:
            index_of[look] = len(looks)
            looks.append(tiles)
        by_case[case] = index_of[look]
    return looks, by_case


def digit_states(count):
    """[(state name, value count)] that hold a look index below count, lowest digit first."""
    states, remaining = [], count
    while remaining > DIGIT:
        states.append((f'bct:c{len(states)}', DIGIT))
        remaining = -(-remaining // DIGIT)
    states.append((f'bct:c{len(states)}', remaining))
    return states


def digits(index, states):
    """A look index split over the digit states: {state: value}."""
    values = {}
    for position, (name, _) in enumerate(states):
        last = position == len(states) - 1
        values[name] = index if last else index % DIGIT
        index //= DIGIT
    return values


# Panes: the broad faces of the post and arms, one tile per facing. Pane faces are never hidden by a
# neighbour, so every diagonal would count and a pane would need 803 looks; without the diagonals it
# needs 64 and draws everything but inner corners inside a flat pane wall.
PANE_FACES = ('north', 'south', 'west', 'east')


def pane_face_tiles(case):
    """{pane face: 47-tile index} for a case key of side bits; a corner counts as joined when both of its edges join."""
    template = _template_tiles()
    joined = {SIDE_NAMES[side] for side in range(6) if case >> side & 1}
    tiles = {}
    for face in PANE_FACES:
        raw = 0
        for bit, edge in enumerate(FACE_EDGES[face]):
            if edge in joined:
                raw |= 1 << bit
        for corner in range(4):
            both = (1 << corner) | (1 << ((corner + 1) % 4))
            if raw & both == both:
                raw |= 1 << (corner + 4)
        tiles[face] = template[_canonical(raw)]
    return tiles


def pane_table():
    """(looks, {case key: look index}) for panes: the 64 side masks, each distinct look of the four faces once."""
    looks, index_of, by_case = [], {}, {}
    for case in range(64):
        tiles = pane_face_tiles(case)
        look = tuple(tiles[face] for face in PANE_FACES)
        if look not in index_of:
            index_of[look] = len(looks)
            looks.append(tiles)
        by_case[case] = index_of[look]
    return looks, by_case
