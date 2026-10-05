"""Build a terrain transition pack (grass and sand edges) for a Bedrock resource pack folder, .mcpack or .zip.

prepare() copies the pack's own grass and sand materials, colour plus any Vibrant Visuals maps,
into the provider the terrain add-on builder reads. Textures the pack lacks come from vanilla.

Key decisions:
- Every map becomes a 256 square: smaller textures scale up by nearest neighbour so their pixels
  stay crisp, larger ones are filtered down.
- Grass keeps all of the pack's weighted variants; each sand surface uses one image.
- The grass edge follows the author's grass side overlay, and the whole sand family shares one
  edge outline cut from the author's sand.
"""
import argparse
from pathlib import Path
import re
import tempfile
import zipfile

from PIL import Image

from common import read_json, samples_path, write_json
from terrain_providers import ROOT, build

# Provider effect -> the vanilla terrain texture it starts from. Effect names are also the host
# blocks' names in blocks.json.
MATERIALS = {'grass': 'grass_top', 'sand': 'sand', 'red_sand': 'red_sand',
             'suspicious_sand': 'suspicious_sand_0', 'soul_sand': 'soul_sand'}
# Archives that would unpack past this size are refused.
_MAX_UNPACKED_BYTES = 2 * 1024 ** 3
# Side of every prepared map, in pixels.
_MAP_SIZE = 256
# Image files a texture path may name, in the order they are looked for.
_IMAGE_EXTENSIONS = ('.tga', '.png', '.jpg', '.jpeg')


def prepare(root, source, key, overrides=None, samples=None):
    """Write a pack's terrain provider and its materials into build/terrain-input-<key>.

    source is a resource pack folder, .mcpack or .zip. overrides map a material's vanilla terrain
    texture name (a MATERIALS value) to terrain atlas texture entries used instead of the pack's.
    Returns the provider path and a receipt of each material's inputs, also written as
    sources.json beside it.
    """
    if not re.fullmatch(r'[a-z][a-z0-9-]*', key):
        raise ValueError('Choose a lowercase pack key')
    work = root / 'build' / ('terrain-input-' + key)
    # Each preparation gets a clean extraction directory, preventing old files
    # from being mistaken for inputs in a replacement archive.
    work.mkdir(parents=True, exist_ok=True)
    extraction = Path(tempfile.mkdtemp(prefix='source-', dir=work))
    pack = pack_folder(source, extraction)
    samples = samples or samples_path(root)
    vanilla = samples / 'resource_pack'
    if not (vanilla / 'blocks.json').is_file():
        raise ValueError('Bedrock samples not found. Set --samples to the downloaded bedrock-samples folder')
    atlas_path = pack / 'textures/terrain_texture.json'
    atlas = read_json(atlas_path).get('texture_data', {}) if atlas_path.exists() else {}
    template = read_json(root / 'converter/data/terrain-provider.json')
    template['id'] = key.replace('-', '_')
    overrides = overrides or {}
    unknown = set(overrides) - set(MATERIALS.values())
    if unknown:
        raise ValueError('Unknown material overrides: ' + str(sorted(unknown)))
    receipt = {}
    blocks = read_json(pack / 'blocks.json') if (pack / 'blocks.json').exists() else {}
    vanilla_blocks = read_json(vanilla / 'blocks.json')
    vanilla_atlas = read_json(vanilla / 'textures/terrain_texture.json')['texture_data']
    for name, material in MATERIALS.items():
        effect = template['effects'][name]
        block = {**vanilla_blocks.get(name, {}), **blocks.get(name, {})}
        entry = _top_texture_entry(block, material, atlas, vanilla_atlas)
        weights = _summed_weights(overrides.get(material, entry.get('textures', 'textures/blocks/' + material)))
        paths = list(weights)
        if not paths:
            raise ValueError('Empty texture list for ' + material)
        # Sand entities select one source image; grass uses native atlas variants.
        if name != 'grass':
            paths = paths[:1]
        folder = work / 'materials' / name
        prepared = [_prepare_texture_set(root, pack, vanilla, relative, material, folder / str(index))
                    for index, relative in enumerate(paths)]
        effect['texture_sets'] = [descriptor for descriptor, _ in prepared]
        receipt[name] = [inputs for _, inputs in prepared]
        if effect.get('biome_tint') == 'grass':
            edge_source = _save_grass_edge_source(root, pack, folder)
            if edge_source is not None:
                effect['edge_source'] = edge_source
        effect['texture_weights'] = [weights[path] for path in paths]
        effect['ambient_occlusion'] = block.get('ambient_occlusion_exponent', 1.0)
    _share_sand_edge(template['effects'])
    provider = work / 'provider.json'
    write_json(provider, template)
    write_json(work / 'sources.json', receipt)
    return provider, receipt


def pack_folder(source, work):
    """The resource pack folder of source: the folder itself, or the single pack in an archive.

    Archives unpack into work/input, and every member must stay inside it.
    """
    source = source.resolve()
    if source.is_dir():
        return source
    destination = work / 'input'
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive:
        total = sum(info.file_size for info in archive.infolist())
        if total > _MAX_UNPACKED_BYTES:
            raise ValueError('Resource archive exceeds 2 GiB unpacked')
        for info in archive.infolist():
            path = contained(destination, info.filename)
            if info.is_dir():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(info))
    candidates = [path.parent for path in destination.rglob('manifest.json')
                  if (path.parent / 'textures').is_dir()]
    if len(candidates) != 1:
        raise ValueError('Archive must contain exactly one resource pack')
    return candidates[0]


def contained(root, relative):
    """root / relative, resolved; raises when it escapes root."""
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Resource path escapes its pack: ' + str(relative))
    return path


def image_path(base):
    """base when it names an image file, else the first image found at base plus an extension; or None."""
    if base.suffix.lower() in _IMAGE_EXTENSIONS and base.is_file():
        return base
    for extension in _IMAGE_EXTENSIONS:
        path = Path(str(base) + extension)
        if path.is_file():
            return path
    return None


def texture_weights(value):
    """(path, weight) pairs of a terrain atlas textures entry: a path, a list or weighted variations."""
    if isinstance(value, str):
        return [(value, 1)]
    if isinstance(value, list):
        return [entry for item in value for entry in texture_weights(item)]
    if isinstance(value, dict):
        if 'variations' in value:
            return texture_weights(value['variations'])
        if 'path' in value:
            weight = value.get('weight', 1)
            if not isinstance(weight, (int, float)) or weight <= 0:
                raise ValueError('Texture variant weights must be positive')
            return [(value['path'], weight)]
    raise ValueError('Unsupported terrain atlas texture entry: ' + str(value))


def _top_texture_entry(block, material, atlas, vanilla_atlas):
    """The terrain atlas entry of a block's top texture, the pack's before vanilla's."""
    alias = block.get('textures', material)
    if isinstance(alias, dict):
        alias = alias.get('up', material)
    return atlas.get(alias, vanilla_atlas.get(alias, {}))


def _summed_weights(entries):
    """Total weight of each texture path in an atlas textures entry, in first-seen order."""
    weights = {}
    for path, weight in texture_weights(entries):
        weights[path] = weights.get(path, 0) + weight
    return weights


def _prepare_texture_set(root, pack, vanilla, relative, material, output):
    """Copy one texture and its maps into output as 256 squares.

    Returns the new texture set's path relative to root and the receipt of its inputs.
    """
    base, origin, descriptor_path = _locate_texture(pack, vanilla, relative, material)
    if descriptor_path.exists():
        descriptor = read_json(descriptor_path)['minecraft:texture_set']
    else:
        descriptor = {'color': base.name}
    values = {}
    for channel, reference in descriptor.items():
        if not isinstance(reference, str):
            values[channel] = reference
            continue
        _write_square_map(channel, _map_path(origin, base, reference), output)
        values[channel] = channel
    descriptor_out = output / 'material.texture_set.json'
    write_json(descriptor_out, {'format_version': '1.21.30', 'minecraft:texture_set': values})
    inputs = {'input': str(base), 'fallback': origin == vanilla, 'channels': list(values)}
    return descriptor_out.relative_to(root).as_posix(), inputs


def _locate_texture(pack, vanilla, relative, material):
    """Find a texture in the pack, else in vanilla, else take vanilla's own texture of the material.

    Returns the texture path without extension, the pack it lives in and its texture set path.
    """
    base = contained(pack, relative)
    descriptor_path = base.with_suffix('.texture_set.json')
    if image_path(base) or descriptor_path.exists():
        return base, pack, descriptor_path
    base = contained(vanilla, relative)
    descriptor_path = base.with_suffix('.texture_set.json')
    if image_path(base) or descriptor_path.exists():
        return base, vanilla, descriptor_path
    base = vanilla / 'textures/blocks' / material
    return base, vanilla, base.with_suffix('.texture_set.json')


def _map_path(origin, base, reference):
    """A map's path: from the pack root when it starts at textures/, else beside its texture."""
    if str(Path(reference)).replace('\\', '/').startswith('textures/'):
        return contained(origin, reference)
    return contained(origin, base.parent.relative_to(origin) / reference)


def _write_square_map(channel, path, output):
    """Save the map image at path as output/<channel>.png, a 256 square."""
    image = image_path(path)
    if image is None:
        raise ValueError('Missing ' + channel + ' texture: ' + str(path))
    output.mkdir(parents=True, exist_ok=True)
    with Image.open(image) as original:
        if original.width != original.height:
            raise ValueError('Animated or non-square textures need a square still image: ' + str(image))
        # MERS stores subsurface in alpha; retain all four channels.
        mode = 'RGBA' if channel == 'color' or original.mode in ('RGBA', 'LA') else 'RGB'
        if original.width < _MAP_SIZE:
            resampling = Image.Resampling.NEAREST
        else:
            resampling = Image.Resampling.LANCZOS
        original.convert(mode).resize((_MAP_SIZE, _MAP_SIZE), resampling).save(output / (channel + '.png'))


def _save_grass_edge_source(root, pack, folder):
    """Save the pack's grass side texture into folder; returns its path relative to root, or None.

    The author's grass side overlay (Bedrock stores it as grass_side alpha) shapes the grass edge.
    """
    side = image_path(contained(pack, 'textures/blocks/grass_side'))
    if side is None:
        return None
    edge_source = folder / 'edge_source.png'
    with Image.open(side) as original:
        original.convert('RGBA').save(edge_source)
    return edge_source.relative_to(root).as_posix()


def _share_sand_edge(effects):
    """Give every non-grass surface the sand edge outline.

    One edge outline for the whole sand family, cut from the author's sand: each lower-priority
    edge is cut by the ones above it, so separate outlines multiply the combinations past the
    engine's 20 per edge.
    """
    sand = effects.get('sand', {}).get('texture_sets')
    if not sand:
        return
    for effect in effects.values():
        if effect.get('kind') == 'surface' and effect.get('biome_tint') != 'grass':
            effect['edge_texture_set'] = sand[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--resource-pack', required=True, type=Path)
    parser.add_argument('--key', required=True, help='Stable pack key, e.g. my-pack')
    parser.add_argument('--title', default='Terrain transitions', help='Name shown in Minecraft')
    parser.add_argument('--textures', type=Path,
                        help='JSON mapping material aliases to pack-relative texture paths')
    parser.add_argument('--samples', type=Path, default=samples_path(ROOT),
                        help='Mojang bedrock-samples checkout')
    args = parser.parse_args()
    overrides = read_json(args.textures) if args.textures else None
    provider, _ = prepare(ROOT, args.resource_pack, args.key, overrides, args.samples)
    archive, _ = build(provider_paths=[provider], pack_key=args.key, title=args.title, samples=args.samples)
    print(archive)
    print('Texture sources: ' + str(provider.parent / 'sources.json'))


if __name__ == '__main__':
    main()
