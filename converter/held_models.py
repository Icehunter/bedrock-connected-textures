"""Held items and inventory icons made from the author's own 3D item models.

Where the author made a 3D model for an item the game draws as a flat sprite (a 3D
lantern, torch or rail), Bedrock would still draw the sprite. The converted pack holds the
author's model with an attachable, posed by the model's first- and third-person display
transforms on fixed hand frames (the frames GeyserMC's java2bedrock converter measured for
Bedrock), and shows an inventory icon rendered from the model's "gui" transform with Java's
inventory lighting. Items the author left alone keep Bedrock's own sprite, as Java keeps the game's.
"""
import io
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

from block_ids import JAVA_RENAMES
from common import BLOCKS_FORMAT, read_json, without_json_comments, write_json
from java_block_geometry import cube, default_uv, element_angles

# Hand frames: the attachable's root bone sits on the hand bone, then the Java
# display transform (translation, X, Y, Z rotation, scale) applies about the model centre.
THIRD_PERSON = {'rotation': [90, 0, 0], 'position': [0, 13, -3], 'scale': 1}
FIRST_PERSON = {'rotation': [90, 60, -40], 'position': [4, 10, 4], 'scale': 1.5}
CENTRE = [0, 8, 0]
GUTTER = 1  # model units of edge padding around each texture in a held atlas
ALPHA_CUTOFF = 26  # Java's cutout discards alpha below 0.1
FACE_NORMALS = {'north': (0, 0, -1), 'south': (0, 0, 1), 'east': (1, 0, 0), 'west': (-1, 0, 0),
                'up': (0, 1, 0), 'down': (0, -1, 0)}
# Corners of each face as (low=0 / high=1) per axis: the u0v0, u0v1, u1v1 and u1v0 corners
# seen from outside the face (Java's FaceInfo vertex order).
FACE_CORNERS = {
    'north': ((1, 1, 0), (1, 0, 0), (0, 0, 0), (0, 1, 0)),
    'south': ((0, 1, 1), (0, 0, 1), (1, 0, 1), (1, 1, 1)),
    'east': ((1, 1, 1), (1, 0, 1), (1, 0, 0), (1, 1, 0)),
    'west': ((0, 1, 0), (0, 0, 0), (0, 0, 1), (0, 1, 1)),
    'up': ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)),
    'down': ((0, 0, 1), (0, 0, 0), (1, 0, 0), (1, 0, 1)),
}
# The two axes a face spans; a face with no size along either draws nothing.
FACE_SPANS = {'north': (0, 1), 'south': (0, 1), 'east': (2, 1), 'west': (2, 1), 'up': (0, 2), 'down': (0, 2)}
# Clockwise quarter turns of a texture.
TURNS = {1: Image.Transpose.ROTATE_270, 2: Image.Transpose.ROTATE_180, 3: Image.Transpose.ROTATE_90}


def _rotation_x(degrees):
    cos, sin = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return np.array([[1, 0, 0], [0, cos, -sin], [0, sin, cos]])


def _rotation_y(degrees):
    cos, sin = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return np.array([[cos, 0, sin], [0, 1, 0], [-sin, 0, cos]])


def _rotation_z(degrees):
    cos, sin = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return np.array([[cos, -sin, 0], [sin, cos, 0], [0, 0, 1]])


def _gui_lights():
    """Java's two inventory lights for 3D items (Lighting.setupFor3DItems) in the item's view frame."""
    turn = np.diag([1, -1, 1]) @ _rotation_y(-22.5) @ _rotation_x(135)
    lights = (np.array([0.2, 1.0, -0.7]), np.array([-0.2, 1.0, 0.7]))
    return [turn @ (light / np.linalg.norm(light)) for light in lights]


LIGHTS = _gui_lights()


def item_model_name(item, read):
    """Model a Java item draws with, from its item definition; None for select/condition/special items."""
    data = read(f'assets/minecraft/items/{item}.json')
    definition = _read_pack_json(data) if data is not None else None
    if not isinstance(definition, dict):
        return None
    model = definition.get('model', {})
    if model.get('type', '').removeprefix('minecraft:') != 'model':
        return None
    return model.get('model')


def resolve_model(name, read):
    """Elements, textures and display transforms of a Java model, following parents.

    Each display context comes from the nearest model in the chain that defines it,
    as Java resolves them. Returns None when a model in the chain is missing.
    """
    textures, display, elements, chain, gui_light = {}, {}, None, [], None
    while name:
        name = name.removeprefix('minecraft:')
        if name in chain:
            break
        chain.append(name)
        if name.startswith('builtin/'):
            break
        data = read(f'assets/minecraft/models/{name}.json')
        model = _read_pack_json(data) if data is not None else None
        if not isinstance(model, dict):
            return None
        for key, value in model.get('textures', {}).items():
            textures.setdefault(key, value)
        for key, value in (model.get('display') or {}).items():
            display.setdefault(key, value)
        if elements is None and 'elements' in model:
            elements = model['elements']
        gui_light = gui_light or model.get('gui_light')
        name = model.get('parent')

    def follow(value, seen=()):
        """A texture variable's final value, following #references and stopping at a loop."""
        while isinstance(value, str) and value.startswith('#') and value not in seen:
            seen = (*seen, value)
            value = textures.get(value[1:])
        return value
    return {'chain': chain, 'elements': elements or [], 'display': display, 'gui_light': gui_light or 'side',
            'textures': {key: follow(value) for key, value in textures.items()}}


def transform(display, context):
    """A display context as Java reads it: missing values are identity; translation and scale clamped."""
    entry = display.get(context) or {}
    rotation = [float(value) for value in entry.get('rotation', [0, 0, 0])]
    translation = [min(max(float(value), -80.0), 80.0) for value in entry.get('translation', [0, 0, 0])]
    scale = [min(max(float(value), -4.0), 4.0) for value in entry.get('scale', [1, 1, 1])]
    return {'rotation': rotation, 'translation': translation, 'scale': scale}


def texture_path(reference):
    namespace, _, path = reference.rpartition(':')
    return f'assets/{namespace or "minecraft"}/textures/{path}.png'


def first_frame(image):
    """The first frame of an animated texture (frames are stacked vertically); a still image as it is."""
    if image.height > image.width and image.height % image.width == 0:
        return image.crop((0, 0, image.width, image.width))
    return image


def bedrock_item(java_item):
    target = JAVA_RENAMES.get('minecraft:' + java_item, 'minecraft:' + java_item)
    return (target[0] if isinstance(target, list) else target).removeprefix('minecraft:')


def plan_held_items(read, read_game, authored, java_items, blocks):
    """Items whose author model is 3D where the game's is a flat sprite.

    read(path) returns bytes from the author's pack, else the game; read_game(path) only
    the game; authored(path) is True for the author's files; java_items are Java item
    ids to look at (the ones the author ships a model or definition for); blocks is
    Bedrock's blocks.json. Returns (plan, skipped).
    """
    plan, skipped = [], []
    for item in sorted(java_items):
        name = item_model_name(item, read)
        if name is None:
            if read(f'assets/minecraft/items/{item}.json') is not None:
                skipped.append({'item': item, 'reason': 'not a single-model item definition'})
            continue
        author = resolve_model(name, read)
        if author is None or not author['elements']:
            continue  # a flat item: its sprite is bound like any other texture
        game_name = item_model_name(item, read_game)
        game = resolve_model(game_name, read_game) if game_name else None
        if game is not None and game['elements']:
            skipped.append({'item': item, 'reason': 'the game draws this item in 3D too'})
            continue
        authored_files = [f'assets/minecraft/models/{model}.json' for model in author['chain']]
        if not any(authored(path) for path in authored_files) and not authored(f'assets/minecraft/items/{item}.json'):
            continue
        target = bedrock_item(item)
        if target not in blocks:
            skipped.append({'item': item, 'reason': 'no Bedrock block item ' + target})
            continue
        if any('tintindex' in face for element in author['elements'] for face in shown_faces(element).values()):
            skipped.append({'item': item, 'reason': 'tinted item model faces'})
            continue
        images, missing = _face_textures(author, read)
        if not images:
            skipped.append({'item': item, 'reason': 'no textures'})
            continue
        # Faces Java can't texture show its missing-texture pattern; they are left out instead.
        elements = [{**element, 'faces': {face: data for face, data in shown_faces(element).items()
                                          if data.get('texture', '').lstrip('#') in images}}
                    for element in author['elements']]
        plan.append({'item': item, 'bedrock': target, 'model': {**author, 'elements': elements},
                     'images': images, 'faces_without_texture': missing})
    return plan, skipped


def shown_faces(element):
    """Faces with area: a flat plane keeps only its two broad faces."""
    low, high = element['from'], element['to']
    size = [high[axis] - low[axis] for axis in range(3)]
    return {face: data for face, data in element.get('faces', {}).items()
            if face in FACE_SPANS and all(abs(size[axis]) > 1e-6 for axis in FACE_SPANS[face])}


def turned_uv(uv, turns):
    """The face's UV rectangle in its texture turned clockwise by quarter turns, so the
    face draws the same without a per-face rotation (entity geometry has none)."""
    u0, v0, u1, v1 = uv
    for _ in range(turns):
        u0, v0, u1, v1 = 16 - v1, u0, 16 - v0, u1
    return [u0, v0, u1, v1]


def held_atlas(model, images):
    """One texture holding every texture (and turned texture) the model's faces use, side by side with edge padding."""
    cells = []
    for element in model['elements']:
        for data in element.get('faces', {}).values():
            if _cell_key(data) not in cells:
                cells.append(_cell_key(data))
    resolution = max(images[variable].width for variable, _ in cells)
    pad = resolution * GUTTER // 16
    size = resolution + 2 * pad
    atlas = Image.new('RGBA', (size * len(cells), size), (0, 0, 0, 0))
    for index, (variable, turns) in enumerate(cells):
        tile = images[variable].resize((resolution, resolution), Image.Resampling.NEAREST)
        if turns:
            tile = tile.transpose(TURNS[turns])
        # Repeating the edge pixels keeps filtering at the cell border from picking up a neighbour.
        padded = np.pad(np.asarray(tile), ((pad, pad), (pad, pad), (0, 0)), mode='edge')
        atlas.paste(Image.fromarray(padded), (index * size, 0))
    return atlas, cells


def held_geometry(identifier, model, cells):
    """Entity geometry: a root bone on the hand and three nested bones for Java's X, Y, Z display rotation."""
    cell = 16 + 2 * GUTTER
    cubes = []
    for element in model['elements']:
        faces, offsets = {}, {}
        for face, data in element.get('faces', {}).items():
            uv = data.get('uv') or default_uv(face, element['from'], element['to'])
            faces[face] = {**{key: value for key, value in data.items() if key != 'rotation'},
                           'uv': turned_uv(uv, _cell_key(data)[1])}
            offsets[face] = cells.index(_cell_key(data)) * cell + GUTTER
        converted = cube({**element, 'faces': faces})
        for face, uv in converted['uv'].items():
            uv['uv'][0] += offsets[face]
            uv['uv'][1] += GUTTER
        cubes.append(converted)
    return {'format_version': '1.16.0', 'minecraft:geometry': [{
        'description': {'identifier': identifier, 'texture_width': cell * len(cells), 'texture_height': cell,
                        'visible_bounds_width': 4, 'visible_bounds_height': 4.5, 'visible_bounds_offset': [0, 0.75, 0]},
        'bones': [{'name': 'held', 'binding': 'q.item_slot_to_bone_name(c.item_slot)', 'pivot': CENTRE},
                  {'name': 'held_x', 'parent': 'held', 'pivot': CENTRE},
                  {'name': 'held_y', 'parent': 'held_x', 'pivot': CENTRE},
                  {'name': 'held_z', 'parent': 'held_y', 'pivot': CENTRE, 'cubes': cubes}]}]}


def held_animation(identifier, display):
    """One always-on animation choosing first- or third-person values inside each channel
    (the pattern that also holds in ray tracing)."""
    first = transform(display, 'firstperson_righthand')
    third = transform(display, 'thirdperson_righthand')
    return {'format_version': '1.8.0', 'animations': {identifier: {'loop': True, 'bones': {
        'held': {'rotation': _per_view_each(FIRST_PERSON['rotation'], THIRD_PERSON['rotation']),
                 'position': _per_view_each(FIRST_PERSON['position'], THIRD_PERSON['position']),
                 'scale': _per_view(FIRST_PERSON['scale'], THIRD_PERSON['scale'])},
        # Bedrock's X runs the other way; the first-person frame also turns Z around.
        'held_x': {'rotation': [_per_view(-first['rotation'][0], -third['rotation'][0]), 0, 0],
                   'position': _per_view_each(
                       [-first['translation'][0], first['translation'][1], -first['translation'][2]],
                       [-third['translation'][0], third['translation'][1], third['translation'][2]]),
                   'scale': _per_view_each(first['scale'], third['scale'])},
        'held_y': {'rotation': [0, _per_view(-first['rotation'][1], -third['rotation'][1]), 0]},
        'held_z': {'rotation': [0, 0, _per_view(first['rotation'][2], third['rotation'][2])]}}}}}


def attachable(item, geometry, animation, texture):
    return {'format_version': '1.10.0', 'minecraft:attachable': {'description': {
        'identifier': 'minecraft:' + item,
        'materials': {'default': 'entity_alphatest', 'enchanted': 'entity_alphatest_glint'},
        'textures': {'default': texture, 'enchanted': 'textures/misc/enchanted_item_glint'},
        'geometry': {'default': geometry}, 'animations': {'hold': animation}, 'scripts': {'animate': ['hold']},
        'render_controllers': ['controller.render.item_default']}}}


def render_icon(model, images, size=None, supersample=2):
    """The model as Java draws it in an inventory slot: its "gui" display transform,
    orthographic, Java's 3D item lighting, cutout alpha. The 16-unit slot fills the image."""
    if size is None:
        resolution = max(image.width for image in images.values())
        size = 16 * max(2, min(16, resolution // 16))
    gui = transform(model['display'], 'gui')
    view_turn = _rotation_x(gui['rotation'][0]) @ _rotation_y(gui['rotation'][1]) @ _rotation_z(gui['rotation'][2])
    scale, shift = np.array(gui['scale']), np.array(gui['translation'])
    raster = _IconRaster(size * supersample)
    for element in model['elements']:
        low, high = element['from'], element['to']
        rotation = element.get('rotation') or {}
        angles, origin = element_angles(rotation), np.array(rotation.get('origin', [8, 8, 8]), dtype=float)
        element_turn = _rotation_z(angles[2]) @ _rotation_y(angles[1]) @ _rotation_x(angles[0])

        def place(point):
            """A model point in the slot's view: the element's rotation, then the gui transform."""
            point = element_turn @ (np.array(point, dtype=float) - origin) + origin
            return view_turn @ (scale * (point - 8)) + shift

        for face, data in element.get('faces', {}).items():
            image = images.get(data.get('texture', '').lstrip('#'))
            if image is None or face not in FACE_CORNERS:
                continue
            normal = view_turn @ (element_turn @ np.array(FACE_NORMALS[face], dtype=float))
            if normal[2] <= 1e-6:
                continue  # Java culls faces turned away from the viewer
            corners = [place([(low, high)[index][axis] for axis, index in enumerate(spec)])
                       for spec in FACE_CORNERS[face]]
            raster.draw_face(corners, face, data, (low, high), image, _shade(normal, model.get('gui_light')))
    return raster.image(size, supersample)


def write_held_items(rp, plan, namespace, vanilla_blocks, vanilla_terrain):
    """Write attachables, geometry, animations, atlases and icons into a resource pack.

    The icon replaces the item's own sprite when the block has one (a textures/items path);
    a block without one (torch, rails) keeps the game's sprite in the inventory.
    """
    rp = Path(rp)
    if not plan:
        return []
    blocks_path, terrain_path = rp / 'blocks.json', rp / 'textures/terrain_texture.json'
    blocks = read_json(blocks_path) if blocks_path.exists() else {'format_version': BLOCKS_FORMAT}
    terrain = read_json(terrain_path) if terrain_path.exists() else {
        'resource_pack_name': namespace, 'texture_name': 'atlas.terrain', 'padding': 8, 'num_mip_levels': 4,
        'texture_data': {}}
    written = []
    for entry in plan:
        item, target, model = entry['item'], entry['bedrock'], entry['model']
        geometry_id = f'geometry.{namespace}.held.{target}'
        animation_id = f'animation.{namespace}.held.{target}'
        texture = f'textures/{namespace}/held/{target}'
        atlas, used = held_atlas(model, entry['images'])
        for folder in ('attachables', 'models/entity', 'animations', f'textures/{namespace}/held'):
            (rp / folder).mkdir(parents=True, exist_ok=True)
        atlas.save(rp / (texture + '.png'))
        write_json(rp / f'models/entity/{namespace}_held_{target}.geo.json', held_geometry(geometry_id, model, used))
        write_json(rp / f'animations/{namespace}_held_{target}.animation.json',
                   held_animation(animation_id, model['display']))
        write_json(rp / f'attachables/{namespace}_held_{target}.json',
                   attachable(target, geometry_id, animation_id, texture))
        sprite = _icon_sprite(blocks, terrain, target, vanilla_blocks, vanilla_terrain)
        if sprite:
            for extension in ('.tga', '.jpg', '.jpeg', '.texture_set.json'):
                stale = rp / (sprite + extension)
                if stale.exists():
                    stale.unlink()
            (rp / sprite).parent.mkdir(parents=True, exist_ok=True)
            render_icon(model, entry['images']).save(rp / (sprite + '.png'))
        written.append({'item': item, 'bedrock': 'minecraft:' + target, 'model': model['chain'][0],
                        'textures': {variable: model['textures'].get(variable) for variable, _ in used},
                        'icon': sprite})
    return written


class _IconRaster:
    """A supersampled RGBA canvas with a depth buffer, 16 model units across."""

    def __init__(self, full_size):
        self.unit = full_size / 16
        self.canvas = np.zeros((full_size, full_size, 4), dtype=np.float32)
        self.depth = np.full((full_size, full_size), -np.inf, dtype=np.float32)
        # Pixel centres.
        self.pixel_y, self.pixel_x = np.mgrid[0:full_size, 0:full_size].astype(np.float32) + 0.5

    def draw_face(self, corners, face, data, bounds, image, shade):
        """Draw one textured face, keeping the nearest surface at each pixel and cutting out low alpha.

        corners are the face's u0v0, u0v1, u1v1 and u1v0 corners in view space; data is the
        face's model entry and bounds the element's (from, to).
        """
        screen = [np.array([(8 + corner[0]) * self.unit, (8 - corner[1]) * self.unit, corner[2]])
                  for corner in corners]
        start, along_u, along_v = screen[0], screen[3] - screen[0], screen[1] - screen[0]
        determinant = along_u[0] * along_v[1] - along_u[1] * along_v[0]
        if abs(determinant) < 1e-9:
            return  # seen exactly edge-on
        offset_x, offset_y = self.pixel_x - start[0], self.pixel_y - start[1]
        # How far along the face's u and v edges each pixel lies, from 0 to 1 inside the face.
        face_u = (offset_x * along_v[1] - offset_y * along_v[0]) / determinant
        face_v = (along_u[0] * offset_y - along_u[1] * offset_x) / determinant
        inside = (face_u >= 0) & (face_u <= 1) & (face_v >= 0) & (face_v <= 1)
        if not inside.any():
            return
        z = start[2] + face_u * along_u[2] + face_v * along_v[2]
        u0, v0, u1, v1 = data.get('uv') or default_uv(face, *bounds)
        corner_uv = [(u0, v0), (u0, v1), (u1, v1), (u1, v0)]
        turns = (data.get('rotation', 0) // 90) % 4
        turned = [corner_uv[(index + turns) % 4] for index in range(4)]
        texture_u = turned[0][0] + face_u * (turned[3][0] - turned[0][0]) + face_v * (turned[1][0] - turned[0][0])
        texture_v = turned[0][1] + face_u * (turned[3][1] - turned[0][1]) + face_v * (turned[1][1] - turned[0][1])
        pixels = np.asarray(image, dtype=np.float32)
        column = np.clip((texture_u / 16 * image.width).astype(int), 0, image.width - 1)
        row = np.clip((texture_v / 16 * image.height).astype(int), 0, image.height - 1)
        sample = pixels[row, column]
        visible = inside & (sample[..., 3] >= ALPHA_CUTOFF) & (z > self.depth)
        self.canvas[visible, :3] = sample[visible, :3] * shade
        self.canvas[visible, 3] = 255
        self.depth[visible] = z[visible]

    def image(self, size, supersample):
        """The canvas averaged down to size x size; premultiplied alpha keeps edges from darkening."""
        alpha = self.canvas[..., 3:4] / 255
        premultiplied = np.concatenate([self.canvas[..., :3] * alpha, alpha], axis=-1)
        blocks = premultiplied.reshape(size, supersample, size, supersample, 4).mean(axis=(1, 3))
        coverage = blocks[..., 3:4]
        colour = np.where(coverage > 0, blocks[..., :3] / np.maximum(coverage, 1e-6), 0)
        rgba = np.concatenate([colour, coverage * 255], axis=-1)
        return Image.fromarray(rgba.clip(0, 255).round().astype(np.uint8))


def _read_pack_json(data):
    """JSON as Java reads pack files: a byte-order mark and comments are allowed. None if unreadable."""
    text = data.decode('utf-8-sig') if isinstance(data, bytes) else data
    try:
        return json.loads(without_json_comments(text))
    except ValueError:
        return None


def _face_textures(model, read):
    """({texture variable: first frame} for the variables the model's faces use, [variables not found])."""
    images, missing = {}, []
    variables = {face.get('texture', '').lstrip('#') for element in model['elements']
                 for face in shown_faces(element).values()}
    for variable in sorted(variables):
        reference = model['textures'].get(variable)
        data = read(texture_path(reference)) if isinstance(reference, str) else None
        if data is None:
            missing.append(variable)
            continue
        images[variable] = first_frame(Image.open(io.BytesIO(data)).convert('RGBA'))
    return images, missing


def _cell_key(data):
    """Atlas cell of a face: its texture, turned by the face's quarter turns."""
    return data.get('texture', '').lstrip('#'), (data.get('rotation', 0) // 90) % 4


def _number(value):
    """A value rounded to 4 places, as an int when whole, so the animation JSON stays short."""
    value = round(float(value), 4)
    return int(value) if value == int(value) else value


def _per_view(first, third):
    """One animation value: a constant when both views agree, else a first-person test in Molang."""
    first, third = _number(first), _number(third)
    return first if first == third else f'c.is_first_person ? {first} : {third}'


def _per_view_each(first_values, third_values):
    return [_per_view(first, third) for first, third in zip(first_values, third_values)]


def _shade(normal, gui_light):
    """Java's inventory light on a face: two directional lights plus 0.4 ambient; 'front' items are fully lit."""
    if gui_light == 'front':
        return 1.0
    return min(1.0, 0.6 * sum(max(0.0, float(light @ normal)) for light in LIGHTS) + 0.4)


def _icon_sprite(blocks, terrain, target, vanilla_blocks, vanilla_terrain):
    """The block's own item sprite (a textures/items path) to draw the icon over, or None.

    A block without one keeps its sprite: adding a carried texture to a block such as a torch
    changes how the game draws the placed block, not just its icon.
    """
    definition = blocks.get(target) or vanilla_blocks.get(target) or {}
    carried = definition.get('carried_textures')
    if not isinstance(carried, str):
        return None
    entry_textures = terrain['texture_data'].get(carried, vanilla_terrain.get(carried, {})).get('textures')
    paths = _texture_paths(entry_textures)
    if len(paths) == 1 and isinstance(paths[0], str) and paths[0].startswith('textures/items/'):
        return paths[0]
    return None


def _texture_paths(value):
    """The paths of a terrain_texture.json 'textures' value: a path, a {"path"} object, or a list of either."""
    items = value if isinstance(value, list) else [value]
    return [item if isinstance(item, str) else (item or {}).get('path') for item in items]
