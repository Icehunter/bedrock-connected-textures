"""One copy of each identical block texture in a finished add-on.

A converted pack writes the same image under several names: an author's normal
map copied for every block alias that shows it, a rule tile that equals the
base texture, the frames two animations share. The game loads every copy into
its texture memory. This pass keeps one file of each identical image (within a
folder, since texture sets name their maps relative to their own folder) and
points everything at it:

- a material map (normal, MER, MERS, heightmap) is merged when nothing but
  texture sets names it, and the texture sets are rewritten to the kept name;
- a colour image is merged only when its whole texture set (every map it
  names) is identical as well, so no block ends up with another block's
  material; the atlas, the flipbooks and any other file naming its path are
  rewritten.

Nothing that is still named anywhere else is removed.
"""
from collections import defaultdict
import hashlib
import json
from pathlib import PurePosixPath
import re
import zipfile

BLOCKS = 'textures/blocks/'
COLOR = 'color'


def _texture_sets(files, prefix):
    """{set path: document} for the block texture sets."""
    found = {}
    for name, data in files.items():
        if name.startswith(prefix + BLOCKS) and name.endswith('.texture_set.json'):
            try:
                found[name] = json.loads(data.decode('utf-8-sig'))
            except ValueError:
                continue
    return found


def _set_of(image):
    """The texture set path beside a colour image (name.png -> name.texture_set.json)."""
    return image[:-len('.png')] + '.texture_set.json'


def _channel_file(set_path, value):
    return str(PurePosixPath(set_path).parent / (value + '.png'))


def dedupe_addon(addon, resource_folder='Source_RP/'):
    """Merge identical block textures in the add-on's resource pack; returns {'files_removed', 'bytes_removed'}."""
    prefix = resource_folder
    # Images are only hashed, one at a time: a large add-on is gigabytes, too much to hold in memory.
    # The text files are kept, to read and rewrite the names in them.
    with zipfile.ZipFile(addon) as source:
        infos = {info.filename: info for info in source.infolist()}
        images = [name for name in infos if name.startswith(prefix + BLOCKS) and name.endswith('.png')]
        digest = {name: hashlib.sha256(source.read(name)).hexdigest() for name in images}
        files = {name: source.read(name) for name in infos
                 if name.startswith(prefix) and name.endswith(('.json', '.material'))}
    sets = _texture_sets(files, prefix)
    # Which images texture sets name as maps, and which as colours.
    map_users, color_images = defaultdict(list), set()
    for set_path, document in sets.items():
        for channel, value in document.get('minecraft:texture_set', {}).items():
            if not isinstance(value, str):
                continue
            image = _channel_file(set_path, value)
            if channel == COLOR:
                color_images.add(image)
            else:
                map_users[image].append((set_path, channel))
    # Every other text file, to find names a merge must leave alone or rewrite.
    texts = {name: data.decode('utf-8-sig', 'replace') for name, data in files.items()
             if name.startswith(prefix) and name.endswith(('.json', '.material')) and name not in sets}

    quoted = {match for text in texts.values() for match in re.findall(r'"([^"\\]+)"', text)}

    def named_elsewhere(image):
        return image[len(prefix):-len('.png')] in quoted

    groups = defaultdict(list)
    for name in sorted(images):
        groups[(str(PurePosixPath(name).parent), digest[name])].append(name)
    removed = set()
    renamed_paths = {}   # 'textures/blocks/x' -> 'textures/blocks/kept'
    # 1. Material maps named only by texture sets.
    for members in groups.values():
        maps = [name for name in members if name in map_users and name not in color_images and not named_elsewhere(name)]
        if len(maps) < 2:
            continue
        kept, rest = maps[0], maps[1:]
        kept_name = PurePosixPath(kept).stem
        for image in rest:
            for set_path, channel in map_users[image]:
                sets[set_path]['minecraft:texture_set'][channel] = kept_name
            removed.add(image)

    # 2. Colour images whose whole material is identical.
    def material_key(image):
        set_path = _set_of(image)
        if set_path not in sets:
            return (digest[image],)
        channels = sets[set_path].get('minecraft:texture_set', {})
        key = [digest[image]]
        for channel in sorted(channels):
            value = channels[channel]
            if channel == COLOR:
                continue
            if isinstance(value, str):
                target = _channel_file(set_path, value)
                key.append((channel, digest.get(target, target)))
            else:
                key.append((channel, json.dumps(value)))
        return tuple(key)

    for members in groups.values():
        colors = [name for name in members if name not in map_users and name not in removed]
        by_material = defaultdict(list)
        for name in colors:
            by_material[material_key(name)].append(name)
        for same in by_material.values():
            if len(same) < 2:
                continue
            kept, rest = same[0], same[1:]
            for image in rest:
                renamed_paths[image[len(prefix):-len('.png')]] = kept[len(prefix):-len('.png')]
                removed.add(image)
                if _set_of(image) in sets:
                    removed.add(_set_of(image))
    # Rewrite every quoted path of a merged colour image.
    rewritten = {}
    if renamed_paths:
        pattern = re.compile('"(' + '|'.join(re.escape(path) for path in sorted(renamed_paths, key=len, reverse=True)) + ')"')
        for name, text in texts.items():
            updated = pattern.sub(lambda match: '"' + renamed_paths[match.group(1)] + '"', text)
            if updated != text:
                rewritten[name] = updated.encode('utf-8')
    for set_path, document in sets.items():
        if set_path not in removed:
            rewritten[set_path] = (json.dumps(document, indent=2) + '\n').encode('utf-8')
    # A map left without any texture set naming it (its sets were merged away) goes too.
    still_named = {_channel_file(set_path, value) for set_path, document in sets.items() if set_path not in removed
                   for value in document.get('minecraft:texture_set', {}).values() if isinstance(value, str)}
    for image in list(map_users):
        if image not in still_named and image not in color_images and not named_elsewhere(image):
            removed.add(image)
    saved = sum(infos[name].file_size for name in removed if name in infos)
    temporary = addon.with_suffix('.dedupe')
    with zipfile.ZipFile(addon) as source, zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as output:
        for name, info in infos.items():
            if name not in removed:
                output.writestr(info, rewritten[name] if name in rewritten else source.read(name))
    temporary.replace(addon)
    return {'files_removed': len(removed), 'bytes_removed': saved}
