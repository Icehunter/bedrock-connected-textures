"""The author's own identity for a converted pack: description, credits, version.

A converted pack is still the author's pack, so its Bedrock manifest shows the
author's name (their file name, with its version and resolution), their
description from pack.mcmeta unchanged, their credit, and their version:
--version, or a version tag in the pack's file name (R4.1.0, v4.1). Without
one the version is 1.0.0. The game number in a name (26.3) is not a pack
version, so only tagged numbers count.
"""
import json
from pathlib import Path
import re

# A Minecraft formatting code: the section sign and one character naming a colour or a style.
FORMATTING = re.compile('§.')
# R4.1.0 or v4.1 standing alone in a file name; a number inside a word does not count.
VERSION_TAG = re.compile(r'(?<![a-z0-9])[rv](\d+)(?:\.(\d+))?(?:\.(\d+))?(?![\d.])', re.IGNORECASE)
# A description line starting "By" or "Made by" names the authors.
CREDIT = re.compile(r'(?:^|\n)\s*(?:made\s+)?by\s+([^\n]+)', re.IGNORECASE)


def plain_text(component):
    """A Java text component (string, {"text", "extra"} object or list) as plain text, formatting codes removed."""
    if isinstance(component, str):
        return FORMATTING.sub('', component)
    if isinstance(component, list):
        return ''.join(plain_text(item) for item in component)
    if isinstance(component, dict):
        text = plain_text(component.get('text', component.get('translate', '')))
        return text + plain_text(component.get('extra', []))
    return ''


def version_from_name(name):
    """[major, minor, patch] from a version tag in a file name, or None without one."""
    match = VERSION_TAG.search(Path(name).stem)
    return [int(part or 0) for part in match.groups()] if match else None


def parse_version(text):
    """[major, minor, patch] from text such as 4.1.0, v4.1 or R4."""
    parts = text.strip().lstrip('rRvV').split('.')
    if not 1 <= len(parts) <= 3 or not all(part.isdigit() for part in parts):
        raise ValueError('Version must look like 4.1.0')
    return [int(part) for part in parts] + [0] * (3 - len(parts))


def _format_number(value):
    """A pack format as (major, minor): 97, 97.1 or [97, 1]."""
    if isinstance(value, list):
        if len(value) == 1:
            return _format_number(value[0])
        return (int(value[0]), int(value[1])) if value else None
    if isinstance(value, (int, float)):
        major = int(value)
        minor = round((value - major) * 10) if isinstance(value, float) else 0
        return major, minor
    return None


def resource_formats(pack):
    """(lowest, highest) resource format the pack declares, as (major, minor) pairs; None when it declares none."""
    low = _format_number(pack.get('min_format'))
    high = _format_number(pack.get('max_format'))
    supported = pack.get('supported_formats')
    if isinstance(supported, list) and len(supported) == 2 and all(isinstance(value, int) for value in supported):
        low = low or (supported[0], 0)
        high = high or (supported[1], 0)
    elif isinstance(supported, dict):
        low = low or _format_number(supported.get('min_inclusive'))
        high = high or _format_number(supported.get('max_inclusive'))
    single = _format_number(pack.get('pack_format'))
    low, high = low or single, high or single
    return (low, high) if low and high else None


def identity(mcmeta, pack_name, *, version=None, authors=None):
    """Name parts for the converted pack from pack.mcmeta bytes (or None) and the first pack's file name."""
    try:
        pack = json.loads(mcmeta.decode('utf-8-sig')).get('pack', {}) if mcmeta else {}
    except ValueError:
        pack = {}
    description = plain_text(pack.get('description', '')).strip()
    if not authors:
        authors = [credit.strip() for credit in CREDIT.findall(description)]
    return {'description': description,
            'authors': list(authors),
            'version': _pack_version(version, pack_name),
            'formats': resource_formats(pack)}


def _pack_version(version, pack_name):
    """--version when given, else a version tag in the file name, else 1.0.0."""
    if isinstance(version, str):
        return parse_version(version)
    return version or version_from_name(pack_name) or [1, 0, 0]


def manifest_description(found):
    """The Bedrock pack description: the author's own words, unchanged (the engine dependency is in the manifest)."""
    return found['description']


def pack_title(pack_name):
    """The name players see when none is given: the author's file name, which carries the pack's version and
    resolution (My Pack R4.1.0 256x), without its extension."""
    return Path(pack_name).stem.replace('_', ' ').strip()


def reference_version(formats, releases):
    """The Java release to read vanilla files from: the newest one whose resource format the pack accepts.

    releases: [{"version", "resource": [major, minor]}] oldest first. A pack declaring no format, or
    only formats newer than every known release, gets the newest; one older than all of them the oldest.
    """
    known = [(tuple(item['resource']), item['version']) for item in releases]
    if not formats:
        return known[-1][1]
    low, high = formats
    inside = [version for resource, version in known if low <= resource <= high]
    if inside:
        return inside[-1]
    older = [version for resource, version in known if resource <= high]
    return older[-1] if older else known[0][1]
