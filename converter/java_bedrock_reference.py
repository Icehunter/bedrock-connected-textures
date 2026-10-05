"""Find which Java sprite each Bedrock block face uses, from an author's own Bedrock edition.

When the author also made a Bedrock edition, a face's Bedrock texture that has exactly
the pixels of one Java sprite names that sprite. Only faces with one clear answer are
bound; every other face is listed with its candidates and the reason it was left out.
"""
import argparse
from collections import defaultdict
import hashlib
import io
import json
from pathlib import Path
import struct
import zipfile

from PIL import Image

from common import read_json, write_json
from java_pack_api import PackStack

FACES = ('north', 'east', 'south', 'west', 'up', 'down')


def texture_paths(value):
    """Texture paths in a terrain_texture.json 'textures' value: a path, a list, or path and variation objects."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [path for item in value for path in texture_paths(item)]
    if isinstance(value, dict):
        if 'path' in value:
            return texture_paths(value['path'])
        if 'variations' in value:
            return texture_paths(value['variations'])
    return []


def resolve_reference(stack, bedrock, blocks, terrain):
    """Face bindings {block: {face: Java sprite}} found by matching pixels with the Bedrock edition.

    blocks and terrain are vanilla blocks.json and terrain texture data; the Bedrock
    edition's own files override them. Ambiguous faces go to 'unresolved_faces'.
    """
    source_digests = _java_sprite_digests(stack)
    with zipfile.ZipFile(bedrock) as archive:
        names = set(archive.namelist())
        edition_blocks = json.loads(archive.read('blocks.json')) if 'blocks.json' in names else {}
        blocks = {**blocks, **edition_blocks}
        if 'textures/terrain_texture.json' in names:
            terrain = {**terrain, **json.loads(archive.read('textures/terrain_texture.json'))['texture_data']}
        matches = _java_matches(archive, names, terrain, source_digests)
    bindings, unresolved = _face_bindings(blocks, terrain, matches)
    return {'baseTextures': bindings, 'unresolved_faces': unresolved, 'material_matches': matches,
            'mapped_faces': sum(len(faces) for faces in bindings.values()),
            'source_artwork_modified': False, 'matching': 'exact dimensions and RGBA pixels',
            'full_support_verified': False}


def _java_sprite_digests(stack):
    """{pixel digest: [Java sprite paths]} for the block sprites, without their PBR maps."""
    digests = defaultdict(list)
    selected, _ = stack.block_dependencies()
    for path in sorted(selected):
        if not path.endswith('.png'):
            continue
        if path.endswith(('_n.png', '_s.png')) and path[:-6] + '.png' in stack.files:
            continue
        digests[_pixel_digest(stack.read(path))].append(path)
    return digests


def _java_matches(archive, names, terrain, source_digests):
    """{Bedrock texture path: [Java sprites with the same pixels]} for every texture the edition ships."""
    paths = {path for value in terrain.values() for path in texture_paths(value.get('textures'))}
    matches = {}
    for path in sorted(paths):
        image = path if path.endswith('.png') else path + '.png'
        if image not in names:
            continue
        candidates = source_digests.get(_pixel_digest(archive.read(image)), [])
        # A CTM tile can repeat the block's own sprite; the face binds to the block sprite,
        # not to a tile some rule draws later.
        block_sprites = [candidate for candidate in candidates
                         if '/textures/block/' in candidate or '/textures/blocks/' in candidate]
        matches[path] = block_sprites or candidates
    return matches


def _face_bindings(blocks, terrain, matches):
    """Faces whose every texture points to the same single Java sprite, and the faces that do not."""
    bindings = {}
    unresolved = []
    for block, definition in blocks.items():
        if block == 'format_version' or not isinstance(definition, dict):
            continue
        faces = definition.get('textures', {})
        if isinstance(faces, str):
            faces = {face: faces for face in FACES}
        if not isinstance(faces, dict):
            continue
        identifier = block if ':' in block else 'minecraft:' + block
        for face in FACES:
            alias = faces.get(face, faces.get('side'))
            if not isinstance(alias, str):
                continue
            materials = texture_paths(terrain.get(alias, {}).get('textures'))
            candidate_sets = [matches.get(path, []) for path in materials]
            candidates = sorted({name for values in candidate_sets for name in values})
            if candidate_sets and all(values for values in candidate_sets) and len(candidates) == 1:
                bindings.setdefault(identifier, {})[face] = candidates[0]
            else:
                unresolved.append({'block': identifier, 'face': face, 'atlas_alias': alias,
                                   'bedrock_materials': materials, 'java_candidates': candidates,
                                   'reason': ('state_or_texture_alias_ambiguity' if candidates
                                              else 'no_identical_authored_pixels')})
    return bindings, unresolved


def _pixel_digest(data):
    """SHA-256 of an image's size and RGBA pixels, so different file encodings still match."""
    with Image.open(io.BytesIO(data)) as image:
        return hashlib.sha256(struct.pack('<II', *image.size) + image.convert('RGBA').tobytes()).hexdigest()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--java', type=Path, nargs='+', required=True)
    parser.add_argument('--bedrock', type=Path, required=True)
    parser.add_argument('--samples', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Reference output already exists')
    with PackStack(args.java) as stack:
        report = resolve_reference(stack, args.bedrock, read_json(args.samples / 'blocks.json'),
                                   read_json(args.samples / 'textures/terrain_texture.json')['texture_data'])
    write_json(args.output, report)
    print(json.dumps({'mapped_faces': report['mapped_faces'], 'unresolved_faces': len(report['unresolved_faces'])}))
