"""Report which parts of a Java pack's CTM rules the converter cannot carry over.

The audit lists, rule by rule, what blocks a faithful conversion: unknown methods and
options, shapes and orientations that need their own rendering, biome and state names
that need mapping, and tiles that are missing, transparent or not square. Neither the
Java pack nor the author's Bedrock edition is changed.
"""
import argparse
from collections import Counter
import hashlib
import io
import json
import re
from pathlib import Path
import zipfile

from PIL import Image

from common import write_json
from connected_build import METHODS
from import_java_ctm import SUPPORTED, parse_properties
from java_texture_animation import parse_animation
from java_texture_paths import SPECIAL_TILES, resolve_texture

# OptiFine's compact connected method; its ctm.N options replace single tiles.
COMPACT_METHOD = 'ctm_compact'
# Words in a block id that mean a shape other than a full cube.
NON_CUBE_WORDS = ('stairs', 'slab', 'fence', 'button', 'bell', 'pane', 'door', 'wall', 'chain')
# Words in a block id or state that mean the block turns along an axis.
ORIENTED_WORDS = ('log', 'wood', 'stem', 'hyphae', 'pillar', 'axis=')


def audit(java, bedrock=None):
    """Audit report for a Java pack ZIP, with a summary of its Bedrock edition when given."""
    with zipfile.ZipFile(java) as archive:
        names = archive.namelist()
        rules = [_audit_rule(archive, names, name) for name in sorted(names)
                 if '/optifine/ctm/' in name and name.endswith('.properties')]
    result = {'java_sha256': hashlib.sha256(Path(java).read_bytes()).hexdigest(),
              'ctm_rules': len(rules),
              'methods': dict(Counter(rule['method'] for rule in rules)),
              'rules_with_known_gaps': sum(bool(rule['known_gaps']) for rule in rules),
              'rules': rules,
              'conversion_complete': False,
              'artwork_modified': False,
              'scope': 'Compatibility audit; this command does not convert or install a pack.'}
    if bedrock:
        result['bedrock'] = _bedrock_summary(bedrock)
    return result


def _audit_rule(archive, names, name):
    """One rule's report: its properties, known gaps and the tiles (with PBR maps) it draws."""
    properties = parse_properties(archive.read(name).decode('utf-8-sig'), name)
    method = properties.get('method', 'ctm')
    gaps = _rule_gaps(properties, method)
    material_sources = []
    for token in _tile_tokens(properties):
        if token in SPECIAL_TILES:
            continue
        prefix = name[:name.index('assets/')]
        tile = prefix + resolve_texture(token, name[len(prefix):])
        if tile not in names:
            gaps.append('Missing or unmapped CTM texture: ' + tile)
            continue
        gaps.extend(_tile_gaps(archive, names, tile))
        channels = [tile[:-4] + suffix + '.png' for suffix in ('_n', '_s') if tile[:-4] + suffix + '.png' in names]
        material_sources.append({'color': tile, 'java_pbr_channels': channels})
    return {'path': name, 'method': method, 'properties': properties, 'known_gaps': gaps,
            'material_sources': material_sources,
            'status': 'blocked' if gaps else 'needs texture/block/PBR mapping and image validation'}


def _rule_gaps(properties, method):
    """Gaps found in the rule's own properties, before its tiles are read."""
    gaps = []
    if method not in METHODS and method != COMPACT_METHOD:
        gaps.append('Unsupported rendering method: ' + method)
    unknown = sorted(key for key in set(properties) - SUPPORTED
                     if not (method == COMPACT_METHOD and re.fullmatch(r'ctm\.\d+', key)))
    if unknown:
        gaps.append('Unsupported rule options: ' + ', '.join(unknown))
    if 'orient' in properties and (method != 'repeat'
                                   or properties['orient'].lower() not in ('none', 'state_axis')):
        gaps.append('Texture/model orientation needs an explicit rendering binding')
    if 'biomes' in properties:
        gaps.append('Java biome names require a checked Bedrock biome mapping')
    blocks = properties.get('matchBlocks', '').split()
    non_cube = [block for block in blocks if any(word in block for word in NON_CUBE_WORDS)]
    oriented = [block for block in blocks if any(word in block for word in ORIENTED_WORDS)]
    if non_cube:
        gaps.append('Non-cube geometry requires dedicated rendering: ' + ', '.join(non_cube))
    if oriented:
        gaps.append('Orientation-aware face mapping required: ' + ', '.join(oriented))
    if any('=' in block or block.isdecimal() for block in blocks):
        gaps.append('Java block states or legacy identifiers require mapping')
    return gaps


def _tile_tokens(properties):
    """The rule's tile tokens, with ranges such as 0-46 written out."""
    tokens = []
    for token in properties.get('tiles', '').split():
        interval = re.fullmatch(r'(\d+)-(\d+)', token)
        if interval:
            tokens.extend(map(str, range(int(interval[1]), int(interval[2]) + 1)))
        else:
            tokens.append(token)
    return tokens


def _tile_gaps(archive, names, tile):
    """Gaps in one tile image: a bad animation, a non-square still, or transparency."""
    gaps = []
    with Image.open(io.BytesIO(archive.read(tile))) as image:
        metadata = json.loads(archive.read(tile + '.mcmeta')) if tile + '.mcmeta' in names else {}
        if 'animation' in metadata:
            try:
                animation = parse_animation(image.size, metadata)
                if animation.width != animation.height:
                    raise ValueError('Non-square animation frames')
            except ValueError as error:
                gaps.append('Invalid animation ' + tile + ': ' + str(error))
        elif image.width != image.height:
            gaps.append('Non-square tile has no animation metadata: ' + tile)
        if image.convert('RGBA').getchannel('A').getextrema() != (255, 255):
            gaps.append('Transparent CTM rendering is unsupported: ' + tile)
    return gaps


def _bedrock_summary(bedrock):
    """Name, hash, generator and CTM rule count of the author's Bedrock edition."""
    with zipfile.ZipFile(bedrock) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        return {'name': manifest['header']['name'],
                'sha256': hashlib.sha256(Path(bedrock).read_bytes()).hexdigest(),
                'generated_with': manifest.get('metadata', {}).get('generated_with', {}),
                'ctm_properties': sum(name.endswith('.properties') and '/ctm/' in name
                                      for name in archive.namelist())}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--java', type=Path, required=True)
    parser.add_argument('--bedrock', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.java, args.bedrock)
    write_json(args.report, report)
    print(json.dumps({key: value for key, value in report.items() if key != 'rules'}, indent=2))
