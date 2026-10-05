"""Move static carrier textures onto shared atlas pages without resampling them.

The connected build gives every carrier tile (and every tint palette of it)
its own texture file. This pass packs each carrier render controller's texture
bank onto atlas pages and gives the controller a uv_anim that selects the
tile, so a pack ships and binds far fewer textures. Key decisions:

- Pixels are copied, never resampled. Each tile gets a gutter of repeated
  edge texels so filtering does not pull in its neighbours.
- Tint palette banks are laid out so one UV expression of the tile and
  palette index finds every tile.
- PBR channel pages are shared by colour pages that use the same channel images.
- Controllers that already animate UVs (animated textures) are left alone.
- The pass writes carrier-atlas.json and never runs twice on a pack.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
import re
from typing import NamedTuple

from PIL import Image

from common import read_json, write_json
from carrier_materials import (
    carrier_material, write_carrier_filter_materials, write_carrier_uv_materials,
)


ATLAS_METADATA = 'carrier-atlas.json'
_PARENT_MATERIALS = ('entity', 'entity_alphatest', 'entity_alphablend')
# Every material a generated carrier can use, mapped to its (parent, sampler filter).
_CARRIER_MATERIALS = {carrier_material(parent, animated, texture_filter): (parent, texture_filter)
                      for parent in _PARENT_MATERIALS
                      for animated in (False, True)
                      for texture_filter in (None, 'point', 'bilinear')}
_TEXTURE_SET_VERSION = '1.21.30'


def consolidate_carrier_atlases(resource_pack, *, max_side=2048, gutter=2, prune=True):
    """Rewrite generated static carriers to draw from atlas pages; animated controllers stay intact.

    Returns the report also written to carrier-atlas.json. When that file
    already exists the pack is left as it is and its report is returned.
    """
    root = Path(resource_pack).resolve()
    metadata_path = root / ATLAS_METADATA
    if metadata_path.exists():
        return {**read_json(metadata_path), 'already_consolidated': True}
    manifest = read_json(root / 'manifest.json')
    if not any(module.get('type') == 'resources' for module in manifest.get('modules', [])):
        raise ValueError('Carrier atlas consolidation requires a resource pack')
    controllers, controller_documents = _read_render_controllers(root)
    clients = _read_static_clients(root)
    source_textures = _bound_textures(clients)
    progress = _atlas_controllers(root, controllers, clients, max_side, gutter)
    for client in clients:
        _drop_unused_aliases(client.description, controllers)
        write_json(client.path, client.document)
    for path, document in controller_documents.items():
        write_json(path, document)
    for texture_filter in progress.needed_filters:
        if texture_filter is None:
            write_carrier_uv_materials(root)
        else:
            write_carrier_filter_materials(root, texture_filter)
    result_textures = _bound_textures(clients)
    if prune:
        removed = _prune_replaced_images(root, progress.tiles, progress.replaced_channels, result_textures)
    else:
        removed = []
    report = {'format_version': 1, 'gutter': gutter, 'max_side': max_side,
              'source_color_textures': len(source_textures), 'result_color_textures': len(result_textures),
              'updated_controllers': len(progress.updated), 'skipped_controllers': progress.skipped,
              'tiles': progress.tiles, 'client_tiles': progress.client_tiles,
              'removed_files': removed, 'pixels_resampled': False,
              'existing_animated_grids_modified': False,
              'renderer_verification': 'Atlas pixels and references verified offline; GPU rendering requires testing.'}
    write_json(metadata_path, report)
    return report


def build_texture_atlas(resource_pack, textures, *, base_slots=None, max_side=2048,
                        gutter=2, cache=None):
    """Pack one indexed bank onto atlas pages and return its per-index UV and image bindings.

    A tint bank lists base_slots base tiles per palette. Palettes reuse the
    base tile positions on successive pages, and matching PBR pages are shared
    even when the colour pages use different tint palettes. Animated grids are
    handled by the caller and are not accepted here.
    """
    root = Path(resource_pack).resolve()
    cache = {} if cache is None else cache
    textures = tuple(textures)
    if not textures or gutter < 0 or max_side < 1:
        raise ValueError('Expected textures and positive atlas dimensions')
    slot_count = len(textures) if base_slots is None else base_slots
    if not isinstance(slot_count, int) or slot_count < 1 or len(textures) % slot_count:
        raise ValueError('Tint bank must contain complete base-tile groups')
    bank_key = (textures, slot_count, max_side, gutter)
    if bank_key in cache:
        return cache[bank_key]
    records = [_cached_texture_record(cache, root, relative) for relative in textures]
    tile_size = _shared_tile_size(records)
    layout = _page_layout(records, tile_size, slot_count, max_side, gutter)
    bindings = [None] * len(textures)
    pages = []
    channel_sources = set()
    for indices in _page_chunks(len(records), slot_count, layout):
        page_records = [(index, records[index]) for index in indices]
        page = _write_page(root, page_records, records[0]['channels'], layout, cache, channel_sources)
        pages.append(page)
        for position, (index, record) in enumerate(page_records):
            bindings[index] = _binding(record, page, position, layout)
    result = {'bindings': bindings, 'pages': sorted(set(pages)), 'size': list(layout.page_size),
              'source_size': list(tile_size), 'columns': layout.columns,
              'cell_size': [layout.cell_width, layout.cell_height],
              'slots_per_page': layout.slots_per_page, 'base_slots': slot_count,
              'grouped_palettes': layout.grouped_palettes, 'gutter': gutter,
              'channel_sources': sorted(channel_sources)}
    cache[bank_key] = result
    return result


def atlas_uv(bank, index_expression='0'):
    """Molang uv_anim that addresses the selected base tile; palette pages use the same UV positions."""
    if bank['grouped_palettes']:
        slot = f'math.mod(({index_expression}),{bank["slots_per_page"]})'
    else:
        slot = f'math.mod(math.mod(({index_expression}),{bank["base_slots"]}),{bank["slots_per_page"]})'
    width, height = bank['size']
    cell_width, cell_height = bank['cell_size']
    gutter = bank['gutter']
    columns = bank['columns']
    return {'offset': [f'(math.mod({slot},{columns})*{cell_width}+{gutter})/{width}',
                       f'(math.floor({slot}/{columns})*{cell_height}+{gutter})/{height}'],
            'scale': [bank['source_size'][0] / width, bank['source_size'][1] / height]}


# Atlas pages


@dataclass(frozen=True)
class _PageLayout:
    """A page is a grid of equal cells, each one tile plus its gutter."""
    tile_size: tuple
    gutter: int
    columns: int
    rows: int
    slots_per_page: int
    grouped_palettes: bool

    @property
    def cell_width(self):
        return self.tile_size[0] + 2 * self.gutter

    @property
    def cell_height(self):
        return self.tile_size[1] + 2 * self.gutter

    @property
    def page_size(self):
        return (self.columns * self.cell_width, self.rows * self.cell_height)

    def cell_origin(self, position):
        return (position % self.columns * self.cell_width, position // self.columns * self.cell_height)


def _contained(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Carrier texture escapes resource pack: ' + str(relative))
    return path


def _pack_path(folder, reference):
    """Texture set references are relative to their folder unless they start at textures/."""
    return reference if reference.startswith('textures/') else (folder / reference).as_posix()


def _cached_texture_record(cache, root, relative):
    key = ('texture', relative)
    if key not in cache:
        cache[key] = _texture_record(root, relative)
    return cache[key]


def _texture_record(root, relative):
    """Describe one carrier texture: its size, PBR channels and texture set layout."""
    path = _contained(root, relative + '.png')
    if not relative.startswith('textures/entity/') or not path.is_file():
        raise ValueError('Expected an existing carrier PNG: ' + relative)
    with Image.open(path) as image:
        size = image.size
    # Byte 24 of a PNG is its bit depth; 16-bit images need a deliberate conversion to 8-bit.
    if path.read_bytes()[24:25] == b'\x10':
        raise ValueError('16-bit carrier PNG needs an explicit color conversion')
    channels = _texture_set_channels(root, relative, path, size)
    signature = tuple((channel, 'image' if isinstance(value, str) else json.dumps(value))
                      for channel, value in sorted(channels.items()))
    return {'path': relative, 'size': size, 'channels': channels, 'signature': signature}


def _texture_set_channels(root, relative, path, size):
    """The texture set's non-colour channels, with image references made pack-relative."""
    descriptor = path.with_suffix('.texture_set.json')
    channels = read_json(descriptor)['minecraft:texture_set'] if descriptor.exists() else {}
    resolved = {}
    for channel, value in channels.items():
        if channel == 'color':
            continue
        if isinstance(value, str):
            resolved[channel] = _checked_channel_image(root, _pack_path(Path(relative).parent, value), size)
        else:
            resolved[channel] = value
    return resolved


def _checked_channel_image(root, relative, size):
    path = _contained(root, relative + '.png')
    if not path.is_file():
        raise ValueError('Missing carrier channel: ' + relative)
    with Image.open(path) as image:
        if image.size != size:
            raise ValueError('Carrier channel dimensions differ: ' + relative)
    return relative


def _shared_tile_size(records):
    """A bank shares one page grid, so its images must match in size and texture set layout."""
    size = records[0]['size']
    if any(record['size'] != size or record['signature'] != records[0]['signature'] for record in records):
        raise ValueError('Carrier bank has mixed image dimensions or material layouts')
    if size[0] != size[1]:
        raise ValueError('Animated or nonsquare carrier image retained separately')
    return size


def _page_layout(records, tile_size, slot_count, max_side, gutter):
    cell_width = tile_size[0] + 2 * gutter
    cell_height = tile_size[1] + 2 * gutter
    max_columns = max_side // cell_width
    max_rows = max_side // cell_height
    if not max_columns or not max_rows:
        raise ValueError('Carrier image exceeds selected atlas side')
    capacity = max_columns * max_rows
    # Image channels are already shared between tint variants. Replicating
    # them inside a larger palette page would increase the material budget.
    grouped_palettes = slot_count <= capacity and not any(
        isinstance(value, str) for value in records[0]['channels'].values())
    if grouped_palettes:
        slots_per_page = slot_count * min(len(records) // slot_count, capacity // slot_count)
    else:
        slots_per_page = min(slot_count, capacity)
    columns, rows = _smallest_grid(slots_per_page, max_columns, max_rows, cell_width, cell_height)
    return _PageLayout(tile_size, gutter, columns, rows, slots_per_page, grouped_palettes)


def _smallest_grid(slots, max_columns, max_rows, cell_width, cell_height):
    """Fewest cells that hold the slots, then the squarest page; ties keep fewer columns."""
    grids = [(columns, math.ceil(slots / columns))
             for columns in range(1, max_columns + 1)
             if math.ceil(slots / columns) <= max_rows]
    return min(grids, key=lambda grid: (grid[0] * grid[1], abs(grid[0] * cell_width - grid[1] * cell_height)))


def _page_chunks(texture_count, slot_count, layout):
    """Bank indices drawn on each page.

    Grouped banks fill pages in index order, several palettes to a page.
    Otherwise each palette is paged on its own, so tile n of every palette
    sits where tile n of the base palette does.
    """
    per_page = layout.slots_per_page
    if layout.grouped_palettes:
        return [list(range(start, min(texture_count, start + per_page)))
                for start in range(0, texture_count, per_page)]
    return [list(range(palette + start, palette + min(slot_count, start + per_page)))
            for palette in range(0, texture_count, slot_count)
            for start in range(0, slot_count, per_page)]


def _write_page(root, page_records, bank_channels, layout, cache, channel_sources):
    """Write one colour page, named by its content, and its texture set; return the page's pack path."""
    page, identity = _color_page(root, page_records, layout)
    relative = 'textures/entity/bct_atlas_color_' + identity
    destination = _contained(root, relative + '.png')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        page.save(destination, compress_level=1)
    channels = {'color': destination.stem}
    for channel, value in bank_channels.items():
        if not isinstance(value, str):
            channels[channel] = value
            continue
        sources = tuple(record['channels'][channel] for _, record in page_records)
        channel_sources.update(sources)
        channels[channel] = _channel_page(root, channel, sources, layout, destination.parent, cache)
    if bank_channels:
        write_json(destination.with_suffix('.texture_set.json'),
                   {'format_version': _TEXTURE_SET_VERSION, 'minecraft:texture_set': channels})
    return relative


def _color_page(root, page_records, layout):
    """Paste the colour tiles; the page is identified by its pixels, layout and channel references."""
    page = Image.new('RGBA', layout.page_size)
    identity = hashlib.sha256()
    for position, (_, record) in enumerate(page_records):
        with Image.open(_contained(root, record['path'] + '.png')) as image:
            rgba = image.convert('RGBA')
        identity.update(rgba.tobytes())
        page.paste(_with_gutter(rgba, layout.gutter), layout.cell_origin(position))
    identity.update(json.dumps([layout.page_size, layout.gutter, len(page_records)]).encode())
    identity.update(json.dumps([record['channels'] for _, record in page_records], sort_keys=True).encode())
    return page, identity.hexdigest()[:24]


def _channel_page(root, channel, sources, layout, folder, cache):
    """Name of the page holding these channel images; identical pages are written once and shared."""
    channel_key = ('channel_page', channel, sources, layout.page_size, layout.gutter)
    if channel_key not in cache:
        mode = 'RGBA' if 'subsurface' in channel else 'RGB'
        page = Image.new(mode, layout.page_size)
        for position, source in enumerate(sources):
            with Image.open(_contained(root, source + '.png')) as image:
                converted = image.convert(mode)
            page.paste(_with_gutter(converted, layout.gutter), layout.cell_origin(position))
        digest = hashlib.sha256(page.tobytes() + json.dumps([layout.page_size, channel]).encode()).hexdigest()[:24]
        name = 'bct_atlas_' + channel + '_' + digest
        path = folder / (name + '.png')
        if not path.exists():
            page.save(path, compress_level=1)
        cache[channel_key] = name
    return cache[channel_key]


def _with_gutter(image, gutter):
    """Surround a tile with copies of its edge texels; the interior keeps the original bytes."""
    image = image.copy()
    width, height = image.size
    result = Image.new(image.mode, (width + 2 * gutter, height + 2 * gutter))
    result.paste(image, (gutter, gutter))
    if not gutter:
        return result
    nearest = Image.Resampling.NEAREST
    left_column = image.crop((0, 0, 1, height)).resize((gutter, height), nearest)
    right_column = image.crop((width - 1, 0, width, height)).resize((gutter, height), nearest)
    top_row = image.crop((0, 0, width, 1)).resize((width, gutter), nearest)
    bottom_row = image.crop((0, height - 1, width, height)).resize((width, gutter), nearest)
    result.paste(left_column, (0, gutter))
    result.paste(right_column, (width + gutter, gutter))
    result.paste(top_row, (gutter, 0))
    result.paste(bottom_row, (gutter, height + gutter))
    corners = ((0, 0, 0, 0),
               (width + gutter, 0, width - 1, 0),
               (0, height + gutter, 0, height - 1),
               (width + gutter, height + gutter, width - 1, height - 1))
    for x, y, source_x, source_y in corners:
        result.paste(image.getpixel((source_x, source_y)), (x, y, x + gutter, y + gutter))
    return result


def _binding(record, page, position, layout):
    """Where a source texture landed: its crop on the page and the matching UV offset and scale."""
    cell_x, cell_y = layout.cell_origin(position)
    x = cell_x + layout.gutter
    y = cell_y + layout.gutter
    width, height = layout.tile_size
    page_width, page_height = layout.page_size
    return {'source': record['path'], 'texture': page,
            'crop': [x, y, x + width, y + height],
            'offset': [x / page_width, y / page_height],
            'scale': [width / page_width, height / page_height]}


# Carrier clients and controllers


@dataclass
class _CarrierClient:
    """A client entity that draws with a carrier material and binds only entity texture files."""
    path: Path
    document: dict
    description: dict
    relative_path: str


class _ControllerPlan(NamedTuple):
    """Where one controller's texture bank landed and the uv_anim that selects its tile."""
    aliases: list
    bindings: list
    uv: dict
    channel_sources: list


@dataclass
class _AtlasProgress:
    """What the controller pass changed, for pruning and the report.

    tiles maps each source texture to its first atlas binding, which later
    fixed controllers reuse.
    """
    tiles: dict = field(default_factory=dict)
    client_tiles: dict = field(default_factory=dict)
    replaced_channels: set = field(default_factory=set)
    skipped: list = field(default_factory=list)
    updated: list = field(default_factory=list)
    needed_filters: set = field(default_factory=set)


def _read_render_controllers(root):
    """Every render controller by name, plus each file's document for rewriting."""
    controllers = {}
    documents = {}
    for path in sorted((root / 'render_controllers').glob('*.json')):
        document = read_json(path)
        documents[path] = document
        for name, controller in document.get('render_controllers', {}).items():
            if name in controllers:
                raise ValueError('Duplicate render controller identifier: ' + name)
            controllers[name] = controller
    return controllers, documents


def _read_static_clients(root):
    clients = []
    for path in sorted((root / 'entity').glob('*.json')):
        document = read_json(path)
        description = document.get('minecraft:client_entity', {}).get('description', {})
        if description.get('materials', {}).get('default') not in _CARRIER_MATERIALS:
            continue
        textures = description.get('textures', {})
        if not textures or not all(_is_entity_texture(texture) for texture in textures.values()):
            continue
        clients.append(_CarrierClient(path, document, description, path.relative_to(root).as_posix()))
    return clients


def _is_entity_texture(texture):
    return isinstance(texture, str) and texture.startswith('textures/entity/')


def _controller_names(description):
    for entry in description.get('render_controllers', []):
        if isinstance(entry, str):
            yield entry
        elif isinstance(entry, dict):
            yield from entry


def _controller_users(clients):
    """Clients listed under each render controller they use."""
    users = {}
    for client in clients:
        for name in _controller_names(client.description):
            users.setdefault(name, []).append(client)
    return users


def _bound_textures(clients):
    return {texture for client in clients for texture in client.description['textures'].values()}


def _atlas_controllers(root, controllers, clients, max_side, gutter):
    """Move each usable controller's texture bank onto atlas pages."""
    progress = _AtlasProgress()
    users = _controller_users(clients)
    cache = {}

    # Controllers with texture arrays go first so their page bindings can be reused by fixed ones.
    def array_controllers_first(controller_name):
        return (not controllers.get(controller_name, {}).get('arrays'), controller_name)

    for name in sorted(users, key=array_controllers_first):
        controller = controllers.get(name)
        if controller is None:
            progress.skipped.append({'controller': name, 'reason': 'missing controller'})
            continue
        if 'uv_anim' in controller:
            progress.skipped.append({'controller': name, 'reason': 'existing UV animation retained'})
            continue
        try:
            plan = _plan_controller(root, controller, users[name], progress.tiles, cache, max_side, gutter)
        except (ValueError, KeyError) as error:
            progress.skipped.append({'controller': name, 'reason': str(error)})
            continue
        if plan is None:
            continue
        progress.replaced_channels.update(plan.channel_sources)
        _apply_plan(plan, controller, users[name], progress)
        progress.updated.append(name)
        for binding in plan.bindings:
            progress.tiles.setdefault(binding['source'], binding)
    return progress


def _texture_selection(controller):
    """The aliases a controller can draw, the Molang index that picks one, and the base tile count.

    Palette banks index as tile + q.property('bct:palette') * base tiles.
    """
    textures = controller.get('textures', [])
    if len(textures) != 1 or not isinstance(textures[0], str):
        raise ValueError('Only single-texture carrier controllers can be atlased')
    selected = textures[0]
    if selected.startswith('Texture.'):
        return [selected[len('Texture.'):]], '0', 1
    match = re.fullmatch(r'(Array\.[\w]+)\[(.*)\]', selected)
    if not match:
        raise ValueError('Unsupported carrier texture selection')
    entries = controller.get('arrays', {}).get('textures', {}).get(match[1], [])
    if not entries or any(not isinstance(entry, str) or not entry.startswith('Texture.') for entry in entries):
        raise ValueError('Carrier texture array must contain direct aliases')
    expression = match[2]
    palette = re.search(r"q\.property\('bct:palette'\)\s*\*\s*(\d+)", expression)
    base_slots = int(palette[1]) if palette else len(entries)
    return [entry[len('Texture.'):] for entry in entries], expression, base_slots


def _plan_controller(root, controller, clients, tiles, cache, max_side, gutter):
    """Atlas bindings and uv_anim for one controller's texture bank, or None when it gains nothing."""
    aliases, expression, base_slots = _texture_selection(controller)
    banks = {tuple(client.description['textures'][alias] for alias in aliases) for client in clients}
    if len(banks) != 1:
        raise ValueError('Shared controller has different texture banks')
    textures = next(iter(banks))
    if len(textures) == 1 and textures[0] in tiles:
        # A fixed texture that an array controller already placed reuses its tile.
        binding = tiles[textures[0]]
        return _ControllerPlan(aliases, [binding], {'offset': binding['offset'], 'scale': binding['scale']}, [])
    if len(set(textures)) == 1:
        # A single distinct image gains nothing from a page.
        return None
    bank = build_texture_atlas(root, textures, base_slots=base_slots, max_side=max_side,
                               gutter=gutter, cache=cache)
    return _ControllerPlan(aliases, bank['bindings'], atlas_uv(bank, expression), bank['channel_sources'])


def _apply_plan(plan, controller, clients, progress):
    """Point each client's aliases at the atlas pages and give the controller its uv_anim."""
    for client in clients:
        description = client.description
        for alias, binding in zip(plan.aliases, plan.bindings):
            description['textures'][alias] = binding['texture']
            progress.client_tiles.setdefault(client.relative_path, {})[alias] = binding
        # uv_anim only moves UVs in a material that defines USE_UV_ANIM.
        parent, texture_filter = _CARRIER_MATERIALS[description['materials']['default']]
        description['materials']['default'] = carrier_material(parent, True, texture_filter)
        progress.needed_filters.add(texture_filter)
    controller['uv_anim'] = plan.uv


def _drop_unused_aliases(description, controllers):
    """Keep only the texture aliases the client's controllers still select."""
    used = set()
    for name in _controller_names(description):
        if name not in controllers:
            continue
        try:
            used.update(_texture_selection(controllers[name])[0])
        except ValueError:
            # A selection this pass cannot read keeps every alias.
            used.update(description['textures'])
    description['textures'] = {alias: texture for alias, texture in description['textures'].items()
                               if alias in used}


def _prune_replaced_images(root, tiles, replaced_channels, result_textures):
    """Delete source images now on atlas pages, and replaced channel images nothing references."""
    removed = []
    for relative in sorted(set(tiles) - result_textures):
        for suffix in ('.png', '.texture_set.json'):
            _remove_file(root, relative + suffix, removed)
    # Read after the colour images go, so channels only they referenced are no longer live.
    live_channels = _referenced_channel_images(root)
    for relative in sorted(replaced_channels - live_channels - result_textures):
        _remove_file(root, relative + '.png', removed)
    return removed


def _referenced_channel_images(root):
    referenced = set()
    for path in (root / 'textures').rglob('*.texture_set.json'):
        folder = path.parent.relative_to(root)
        for channel, value in read_json(path).get('minecraft:texture_set', {}).items():
            if channel != 'color' and isinstance(value, str):
                referenced.add(_pack_path(folder, value))
    return referenced


def _remove_file(root, relative, removed):
    path = _contained(root, relative)
    if path.exists():
        path.unlink()
        removed.append(path.relative_to(root).as_posix())
