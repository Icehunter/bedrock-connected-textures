"""Bake Java tint colours into copies of the generated carrier textures.

A carrier entity cannot tint each block the way Java does, so every colour a rule can
need gets its own tinted copy of each tile, and the carrier picks one through its
bct:palette property. Palette entry 0 is white: the original, untouched texture.
"""
import hashlib
from pathlib import Path

from PIL import Image

from carrier_materials import write_carrier_uv_materials
from common import read_json, write_json

WHITE = (255, 255, 255)
# The carrier's palette property has 256 synchronized states.
MAX_PALETTE_COLORS = 256
# Materials that tint with the actor colour, and the plain material each tinted copy uses.
PLAIN_MATERIALS = {'entity_change_color': 'entity', 'entity_alphatest_change_color': 'entity_alphatest'}


def tint_rgb8(color):
    """A Java tint (three channels from 0 to 1) as 8-bit RGB, rounded to nearest."""
    if len(color) != 3 or any(not isinstance(value, (int, float)) or not 0 <= value <= 1 for value in color):
        raise ValueError('Tint must contain three normalized RGB channels')
    return tuple(int(value * 255 + 0.5) for value in color)


def normalized_palette(colors):
    """White, then each distinct tint, as channels from 0 to 1 rounded to six places."""
    distinct = list(dict.fromkeys([WHITE, *(tint_rgb8(color) for color in colors)]))
    if len(distinct) > MAX_PALETTE_COLORS:
        raise ValueError('Carrier tint palette exceeds its 256 synchronized states')
    return [[round(value / 255, 6) for value in rgb] for rgb in distinct]


def tinted_albedo(image, color):
    """The image with RGB multiplied by the tint in 8-bit steps, as Java does; alpha is kept."""
    channels = image.convert('RGBA').split()
    rgb = tint_rgb8(color)
    tinted = [channels[index].point([(value * rgb[index] + 127) // 255 for value in range(256)])
              for index in range(3)]
    return Image.merge('RGBA', (*tinted, channels[3]))


def apply_tint_palettes(resource_pack, rules, rule_palettes):
    """Give each carrier rule its tint palette and write the tinted textures it needs.

    The original textures stay as palette entry 0. Tinted copies share the original's PBR
    maps and keep its size, so animation, padding and the controller clock still match.
    Returns a report of what was written.
    """
    resource_pack = Path(resource_pack).resolve()
    manifest = read_json(resource_pack / 'manifest.json')
    if not any(module.get('type') == 'resources' for module in manifest.get('modules', [])):
        raise ValueError('Tint palette export requires a generated resource pack')
    output_files = set()
    copies = _TintedCopies(resource_pack, output_files)
    changed = 0
    animated = False
    details = []
    for rule in rules:
        if rule['id'] not in rule_palettes:
            raise ValueError('Missing tint palette for carrier rule ' + rule['id'])
        palette = normalized_palette(rule_palettes[rule['id']])
        rule['tintPalette'] = palette
        stem = rule['entity'].replace(':', '_', 1)
        client_path = resource_pack / 'entity' / (stem + '.entity.json')
        output_files.add(client_path.relative_to(resource_pack).as_posix())
        client = read_json(client_path)
        description = client['minecraft:client_entity']['description']
        tile_count = len(rule['tiles'])
        textures, texture_array = _palette_textures(description['textures'], palette, tile_count, copies)
        description['textures'] = textures
        material = description['materials']['default']
        animated |= material.startswith('bct_uv')
        # The copies are already tinted, so the actor colour must not tint them again.
        description['materials']['default'] = PLAIN_MATERIALS.get(material, material)
        if read_json(client_path) != client:
            write_json(client_path, client)
            changed += 1
        for controller_name in description['render_controllers']:
            changed += _update_controller(resource_pack, stem, controller_name, texture_array, tile_count,
                                          output_files)
        details.append({'id': rule['id'], 'palette_colors': len(palette), 'tiles': tile_count,
                        'texture_bindings': len(textures), 'tintPalette': palette})
    if animated:
        registration = write_carrier_uv_materials(resource_pack)
        output_files.add(Path(registration['path']).relative_to(resource_pack).as_posix())
        changed += int(registration['changed'])
    return {'rules': details, 'generated_albedo_files': copies.created,
            'unique_tinted_materials': len(copies.copies),
            'updated_json_files': changed, 'original_albedo_modified': False, 'alpha_modified': False,
            'pbr_channel_files_modified': False, 'animation_layout_modified': False,
            'output_files': sorted(output_files),
            'pixel_math': 'RGB8 multiplied by Java RGB8 tint, rounded to nearest; alpha unchanged'}


def _palette_textures(base, palette, tile_count, copies):
    """The carrier's textures for every palette entry, and the render controller's texture array.

    Entry 0 keeps the original aliases t<n>; entry p adds tinted copies as p<p>_t<n>.
    """
    textures = {}
    texture_array = []
    for index in range(tile_count):
        alias = 't' + str(index)
        if alias not in base:
            raise ValueError('Carrier is missing its original tile alias ' + alias)
        textures[alias] = base[alias]
        texture_array.append('Texture.' + alias)
    for palette_index, color in enumerate(palette[1:], 1):
        for index in range(tile_count):
            alias = f'p{palette_index}_t{index}'
            textures[alias] = copies.copy_of(base['t' + str(index)], color)
            texture_array.append('Texture.' + alias)
    return textures, texture_array


def _update_controller(resource_pack, stem, controller_name, texture_array, tile_count, output_files):
    """Point one render controller at the palette's textures. Returns 1 when its file changed, else 0."""
    if isinstance(controller_name, dict) and len(controller_name) == 1:
        controller_name = next(iter(controller_name))
    if not isinstance(controller_name, str):
        raise ValueError('Unexpected carrier controller reference')
    controller_path = resource_pack / 'render_controllers' / (stem + '.json')
    output_files.add(controller_path.relative_to(resource_pack).as_posix())
    document = read_json(controller_path)
    controller = document['render_controllers'][controller_name]
    controller.setdefault('arrays', {}).setdefault('textures', {})['Array.tiles'] = texture_array
    # Palette p's copy of tile t sits at t + p * tile_count in the array.
    controller['textures'] = [f"Array.tiles[q.property('bct:tile') + q.property('bct:palette') * {tile_count}]"]
    controller['color'] = {'r': 1, 'g': 1, 'b': 1, 'a': 1}
    if read_json(controller_path) == document:
        return 0
    write_json(controller_path, document)
    return 1


class _TintedCopies:
    """Tinted copies of carrier textures, each written once per texture and colour."""

    def __init__(self, resource_pack, output_files):
        self.resource_pack = resource_pack
        self.output_files = output_files
        self.sources = {}
        self.copies = {}
        self.created = 0

    def copy_of(self, relative, color):
        """Pack path, without extension, of the texture at relative tinted by color."""
        rgb = tint_rgb8(color)
        key = (relative, rgb)
        if key in self.copies:
            return self.copies[key]
        path = self._contained(relative + '.png')
        if relative not in self.sources:
            self.sources[relative] = self._read_source(relative, path)
        path, descriptor, identity = self.sources[relative]
        hex_color = ''.join(f'{value:02x}' for value in rgb)
        output = path.with_name('bct_tint_' + identity + '_' + hex_color + '.png')
        self.output_files.add(output.relative_to(self.resource_pack).as_posix())
        if not output.exists():
            with Image.open(path) as original:
                tinted_albedo(original, color).save(output, compress_level=1)
            self.created += 1
        if descriptor:
            self._write_texture_set(output, descriptor)
        self.copies[key] = output.relative_to(self.resource_pack).with_suffix('').as_posix()
        return self.copies[key]

    def _read_source(self, relative, path):
        """(path, texture set or None, identity) of a source texture."""
        descriptor_path = path.with_suffix('.texture_set.json')
        descriptor = read_json(descriptor_path) if descriptor_path.exists() else None
        # The copy's name hashes the source pixels and texture set, so a changed source
        # gets a new copy instead of reusing a stale one.
        source_bytes = relative.encode() + path.read_bytes() + (descriptor_path.read_bytes() if descriptor else b'')
        identity = hashlib.sha256(b'tint-rgb8-v1\0' + source_bytes).hexdigest()[:20]
        return path, descriptor, identity

    def _write_texture_set(self, output, descriptor):
        """The source's texture set with the tinted copy as its colour; PBR maps are shared."""
        target = output.with_suffix('.texture_set.json')
        self.output_files.add(target.relative_to(self.resource_pack).as_posix())
        texture_set = {**descriptor['minecraft:texture_set'], 'color': output.stem}
        material = {**descriptor, 'minecraft:texture_set': texture_set}
        if not target.exists() or read_json(target) != material:
            write_json(target, material)

    def _contained(self, relative):
        path = (self.resource_pack / relative).resolve()
        if not path.is_relative_to(self.resource_pack):
            raise ValueError('Carrier texture escapes generated resource pack')
        return path
