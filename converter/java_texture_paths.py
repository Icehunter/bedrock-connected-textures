"""Turn the tile names in an OptiFine CTM rule into paths inside the pack.

A rule can name a tile as a bare name or number next to the rule file, a path under the
pack's textures, "namespace:path", "./name", or "~/path" for the optifine folder. A
resolved path must stay inside the namespace it names; author files are only read.
"""
from pathlib import PurePosixPath
import posixpath
import re

# Tile tokens with a meaning of their own: leave the face alone, or draw the default texture.
SPECIAL_TILES = ('<skip>', '<default>')


def resolve_texture(token, rule_path, matching=False):
    """Pack path of the PNG a CTM rule's token names.

    rule_path is the rule's own path, under assets/<namespace>/optifine/ctm/. matching is
    True for matchTiles and connectTiles, where a bare name is a block texture rather
    than a tile file next to the rule.
    """
    if token in SPECIAL_TILES:
        return token
    if not isinstance(token, str) or not token or '\\' in token:
        raise ValueError('Invalid CTM sprite reference')
    parts = PurePosixPath(rule_path).parts
    if len(parts) < 5 or parts[0] != 'assets' or parts[2:4] != ('optifine', 'ctm'):
        raise ValueError('CTM rule must be inside an assets namespace')
    if ':' in token:
        namespace, target = _namespaced_target(token)
    else:
        namespace, target = _plain_target(token, rule_path, parts[1], matching)
    if not target.endswith('.png'):
        target += '.png'
    target = posixpath.normpath(target)
    if not target.startswith(f'assets/{namespace}/') or ':' in target or '\\' in target:
        raise ValueError('CTM sprite reference escapes its namespace')
    return target


def _namespaced_target(token):
    """(namespace, path) for "namespace:name", read like a Java resource location."""
    namespace, name = token.split(':', 1)
    if not re.fullmatch(r'[a-z0-9_.-]+', namespace) or ':' in name or name.startswith(('./', '~/', '/')):
        raise ValueError('Invalid namespaced CTM sprite reference')
    if name.startswith(('textures/', 'optifine/')):
        relative = name
    elif '/' in name:
        relative = 'textures/' + name
    else:
        relative = 'textures/block/' + name
    return namespace, f'assets/{namespace}/' + relative


def _plain_target(token, rule_path, rule_namespace, matching):
    """(namespace, path) for a token without a namespace, following OptiFine's path rules."""
    name = token.removeprefix('assets/minecraft/')
    rule_folder = posixpath.dirname(rule_path)
    if name.startswith(('~/', '/')):
        return rule_namespace, f'assets/{rule_namespace}/optifine/' + name.lstrip('~/')
    if name.startswith('./'):
        return rule_namespace, rule_folder + '/' + name[2:]
    if '/' not in name and not matching:
        return rule_namespace, rule_folder + '/' + name
    if name.startswith('optifine/'):
        return rule_namespace, f'assets/{rule_namespace}/' + name
    return 'minecraft', 'assets/minecraft/' + (name if '/' in name else 'textures/block/' + name)
