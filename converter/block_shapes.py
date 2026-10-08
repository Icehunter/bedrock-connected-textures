"""Shaped vanilla blocks drawn as replacement blocks: the parts each shape state shows and their boxes.

A shaped replacement shows the face tiles a full block in its place would
show; its geometry keeps only the parts its shape states show. Each part is a
Java element in world orientation with Java's default UVs, which is what Java's
uvlock gives a turned stair: a slab side shows the half of the tile a Java slab
shows (the lower half on a bottom slab, the upper half on a top slab), and a
stair's faces show the parts of the tile their place in the block covers.
Faces on the block's outside cull against full neighbours, as the Java model's
cullfaces do; faces inside the block never cull. Collision follows the parts
(one box each) and selection is one box around them, the most Bedrock allows.

Stairs carry Bedrock's own corner state (minecraft:corner, the Java stair
shape); the engine works it out from the neighboring stairs as Java does.
"""
import itertools

from java_block_geometry import FACES, cube

# Bedrock's weirdo_direction: the side a stair's tall back faces (Java's facing).
STAIR_FACING = {0: 'east', 1: 'west', 2: 'south', 3: 'north'}
STAIR_CORNERS = ['none', 'inner_left', 'inner_right', 'outer_left', 'outer_right']
# Per shape: the vanilla states that pick the shape.
SHAPE_STATES = {'slab': ['minecraft:vertical_half'],
                'stairs': ['weirdo_direction', 'upside_down_bit', 'minecraft:corner'],
                'fence': ['minecraft:connection_north', 'minecraft:connection_east', 'minecraft:connection_south',
                          'minecraft:connection_west'],
                'wall': ['wall_connection_type_north', 'wall_connection_type_east', 'wall_connection_type_south',
                         'wall_connection_type_west', 'wall_post_bit']}
# A fence's post and, per side, its two bars and its collision arm (Java's fence models and FenceBlock shapes:
# 24 high, so nothing jumps over it).
FENCE_POST = ([6, 0, 6], [10, 16, 10])
FENCE_BARS = {'north': [([7, 12, 0], [9, 15, 6]), ([7, 6, 0], [9, 9, 6])],
              'east': [([10, 12, 7], [16, 15, 9]), ([10, 6, 7], [16, 9, 9])],
              'south': [([7, 12, 10], [9, 15, 16]), ([7, 6, 10], [9, 9, 16])],
              'west': [([0, 12, 7], [6, 15, 9]), ([0, 6, 7], [6, 9, 9])]}
# A wall's post, and per side its arm, short (14 high) or tall (16), and collision 24 high (Java's wall models
# and WallBlock shapes).
WALL_POST = ([4, 0, 4], [12, 16, 12])
WALL_ARMS = {'north': ([5, 0, 0], [11, 16, 8]), 'east': ([8, 0, 5], [16, 16, 11]),
             'south': ([5, 0, 8], [11, 16, 16]), 'west': ([0, 0, 5], [8, 16, 11])}
WALL_HEIGHTS = {'short': 14, 'tall': 16}
FENCE_COLLISION = {'post': ([6, 0, 6], [10, 24, 10]), 'north': ([7, 0, 0], [9, 24, 6]), 'east': ([10, 0, 7], [16, 24, 9]),
                   'south': ([7, 0, 10], [9, 24, 16]), 'west': ([0, 0, 7], [6, 24, 9])}
# Shaped blocks hold water like their vanilla block; the water is clipped to the collision box, as on a vanilla
# slab (from format 1.26.0 the game draws it across the whole block otherwise).
LIQUID_DETECTION = {'detection_rules': [{'liquid_type': 'water', 'can_contain_liquid': True,
                                         'on_liquid_touches': 'blocking', 'use_liquid_clipping': True}]}


def shape_components(shape, vanilla):
    """Components a shaped replacement carries besides its look: it holds water like its vanilla block, and a
    fence only lets the vanilla fences join it that join it in Java (wooden fences; the game counts every vanilla
    fence but nether brick as one with only_fences)."""
    components = {'minecraft:liquid_detection': LIQUID_DETECTION}
    if shape == 'fence':
        accepts = 'none' if vanilla == 'minecraft:nether_brick_fence' else 'only_fences'
        components['minecraft:connection_rule'] = {'accepts_connections_from': accepts}
    return components


# Whether a face of an element lies on the block's outside, and the neighbour direction it culls against.
_OUTSIDE = {'down': lambda low, high: low[1] == 0, 'up': lambda low, high: high[1] == 16,
            'north': lambda low, high: low[2] == 0, 'south': lambda low, high: high[2] == 16,
            'west': lambda low, high: low[0] == 0, 'east': lambda low, high: high[0] == 16}
# Java coordinates: x runs west to east, z north to south. The half of the block on each side.
_HALF = {'west': ((0, 8), None), 'east': ((8, 16), None), 'north': (None, (0, 8)), 'south': (None, (8, 16))}
# A stair's left and right, seen by a player facing the way the stair faces.
_LEFT = {'north': 'west', 'south': 'east', 'east': 'north', 'west': 'south'}
_OPPOSITE = {'north': 'south', 'south': 'north', 'east': 'west', 'west': 'east'}


def shape_of(block):
    """The shape of a vanilla block drawn by shape (slab, stairs, fence, wall), or None.

    Double slabs are full cubes; fence gates open and close, which a replacement cannot do, so they stay vanilla.
    """
    name = block.removeprefix('minecraft:')
    if name.endswith('_slab') and 'double' not in name:
        return 'slab'
    if name.endswith('_stairs'):
        return 'stairs'
    if name.endswith('_fence'):
        return 'fence'
    if name.endswith('_wall'):
        return 'wall'
    return None


def single_slab(block):
    """The slab of a Bedrock double slab (oak_double_slab -> oak_slab, double_cut_copper_slab -> cut_copper_slab),
    or None for any other block."""
    name = block.removeprefix('minecraft:')
    if name.endswith('_double_slab'):
        single = name.removesuffix('_double_slab') + '_slab'
    elif 'double_' in name and name.endswith('_slab'):
        single = name.replace('double_', '', 1)
    else:
        return None
    return 'minecraft:' + single


def _box(sides, y):
    """A Java element ([from], [to]) over the quarter or half of the block the given sides name, at height y."""
    x, z = (0, 16), (0, 16)
    for side in sides:
        side_x, side_z = _HALF[side]
        x, z = side_x or x, side_z or z
    return [x[0], y[0], z[0]], [x[1], y[1], z[1]]


def _stair_elements(facing, upside_down, corner):
    """The Java elements of a stair: the slab half and the step over it (or under it, upside down)."""
    slab_y, step_y = ((8, 16), (0, 8)) if upside_down else ((0, 8), (8, 16))
    back, left = facing, _LEFT[facing]
    right, front = _OPPOSITE[left], _OPPOSITE[facing]
    steps = {'none': [[back]], 'outer_left': [[back, left]], 'outer_right': [[back, right]],
             'inner_left': [[back], [front, left]], 'inner_right': [[back], [front, right]]}[corner]
    return [_box([], slab_y)] + [_box(sides, step_y) for sides in steps]


def shape_variants(shape):
    """[({vanilla state: value}, [Java elements], [collision elements] or None for the elements themselves)]
    for every combination of the shape's states."""
    if shape == 'slab':
        return [({'minecraft:vertical_half': 'bottom'}, [([0, 0, 0], [16, 8, 16])], None),
                ({'minecraft:vertical_half': 'top'}, [([0, 8, 0], [16, 16, 16])], None)]
    if shape == 'stairs':
        return [({'weirdo_direction': direction, 'upside_down_bit': upside_down, 'minecraft:corner': corner},
                 _stair_elements(STAIR_FACING[direction], upside_down, corner), None)
                for direction, upside_down, corner in itertools.product(STAIR_FACING, (False, True), STAIR_CORNERS)]
    if shape == 'fence':
        variants = []
        for joined in itertools.product((False, True), repeat=4):
            sides = [side for side, on in zip(FENCE_BARS, joined) if on]
            values = {'minecraft:connection_' + side: on for side, on in zip(FENCE_BARS, joined)}
            elements = [FENCE_POST] + [bar for side in sides for bar in FENCE_BARS[side]]
            variants.append((values, elements, [FENCE_COLLISION['post']] + [FENCE_COLLISION[side] for side in sides]))
        return variants
    if shape == 'wall':
        variants = []
        for heights in itertools.product(('none', 'short', 'tall'), repeat=4):
            for post in (False, True):
                values = {'wall_connection_type_' + side: height for side, height in zip(WALL_ARMS, heights)}
                values['wall_post_bit'] = post
                arms = [(side, height) for side, height in zip(WALL_ARMS, heights) if height != 'none']
                elements = ([WALL_POST] if post else []) + [_wall_arm(side, height) for side, height in arms]
                collision = ([([4, 0, 4], [12, 24, 12])] if post else []) + [_wall_arm(side, 24) for side, _ in arms]
                # A wall with nothing at all still has a body to select and stand on: its post's place.
                variants.append((values, elements or [WALL_POST], collision or [([4, 0, 4], [12, 24, 12])]))
        return variants
    raise ValueError('unknown shape ' + shape)


def _wall_arm(side, height):
    """A wall arm on one side at a height ('short', 'tall' or a number of pixels)."""
    low, high = WALL_ARMS[side]
    top = WALL_HEIGHTS.get(height, height)
    return list(low), [high[0], top, high[2]]


def _wall_parts():
    """[(bone, [Java elements], {vanilla state: value} it shows for)] of a wall: the post, and a short and a tall
    arm per side. A wall has 162 combinations, more than the 64 bones a block can switch, so its parts are bones."""
    parts = [('post', [WALL_POST], {'wall_post_bit': True})]
    for side in WALL_ARMS:
        for height in ('short', 'tall'):
            parts.append((f'{side}_{height}', [_wall_arm(side, height)], {'wall_connection_type_' + side: height}))
    return parts


def _term(state, value):
    """A Molang test of one mirrored state; booleans are mirrored as 0 or 1."""
    if isinstance(value, bool):
        return f"q.block_state('{state}') == {int(value)}"
    if isinstance(value, int):
        return f"q.block_state('{state}') == {value}"
    return f"q.block_state('{state}') == '{value}'"


def _bone_name(values):
    return 'shape_' + '_'.join(str(int(value) if isinstance(value, bool) else value) for value in values.values())


def shaped_block(identifier, shape, mirror):
    """Geometry and boxes of a shaped replacement.

    mirror: {vanilla state: the replacement's state}. Returns {'geometry',
    'parts' (culling parts [(bone, face, direction, cube index)]),
    'visibility' (bone visibility), 'permutations' (one per shape state
    combination carrying its collision and selection boxes), 'boxes' (the
    components for the first combination)}. Each face of the geometry uses the
    material instance named after it.
    """
    missing = [state for state in SHAPE_STATES[shape] if state not in mirror]
    if missing:
        raise ValueError(f'{shape} without its {", ".join(missing)} state')
    bones, parts, visibility, permutations = [], [], {}, []

    def add_bone(name, elements, values):
        for index, (low, high) in enumerate(elements):
            parts.extend((name, face, face if _OUTSIDE[face](low, high) else None, index) for face in FACES)
        bones.append({'name': name, 'pivot': [0, 0, 0], 'cubes': [_cube(low, high) for low, high in elements]})
        visibility[name] = ' && '.join(_term(mirror[state], value) for state, value in values.items())

    if shape == 'wall':
        for name, elements, values in _wall_parts():
            add_bone(name, elements, values)
    for values, elements, collision in shape_variants(shape):
        if shape != 'wall':
            add_bone(_bone_name(values), elements, values)
        cubes = [_cube(low, high) for low, high in elements]
        solid = [_cube(low, high) for low, high in collision] if collision else cubes
        condition = ' && '.join(_term(mirror[state], value) for state, value in values.items())
        permutations.append({'condition': condition, 'components': _boxes(solid, cubes)})
    description = {'identifier': identifier, 'texture_width': 16, 'texture_height': 16,
                   'visible_bounds_width': 2, 'visible_bounds_height': 2, 'visible_bounds_offset': [0, 0.5, 0]}
    geometry = {'format_version': '1.21.0', 'minecraft:geometry': [{'description': description, 'bones': bones}]}
    return {'geometry': geometry, 'parts': parts, 'visibility': visibility, 'permutations': permutations,
            'boxes': permutations[0]['components']}


def _cube(low, high):
    """A Java element ([from], [to]) as a geometry cube whose faces use the material instance named after them."""
    return cube({'from': low, 'to': high, 'faces': {face: {} for face in FACES}}, lambda face, data: face)


def _boxes(solid, drawn):
    """Collision (one box per solid cube) and selection (one box around the drawn cubes), in the geometry's own
    coordinates so they line up with what is drawn."""
    boxes = [{'origin': list(item['origin']), 'size': list(item['size'])} for item in solid]
    low = [min(item['origin'][axis] for item in drawn) for axis in range(3)]
    high = [max(item['origin'][axis] + item['size'][axis] for item in drawn) for axis in range(3)]
    selection = {'origin': low, 'size': [high[axis] - low[axis] for axis in range(3)]}
    return {'minecraft:collision_box': boxes[0] if len(boxes) == 1 else boxes, 'minecraft:selection_box': selection}
