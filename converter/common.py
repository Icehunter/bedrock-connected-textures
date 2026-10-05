"""Helpers shared across the converter: JSON files, file hashes and where inputs live.

JSON is read the way the game reads pack files, so a byte-order mark and // or /* */
comments are allowed. The bedrock-samples checkout and the built terrain pack default to
folders next to the project; environment variables point elsewhere.
"""
import hashlib
import json
import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# A JSON string, kept whole so "//" inside it survives, or a comment to remove.
_STRING_OR_COMMENT = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/')


# blocks.json format. Older formats apply an override to a block's whole legacy family (an
# override for sand also redraws red sand); the game's own file uses this one.
BLOCKS_FORMAT = '1.21.40'

def sha256_file(path):
    """SHA-256 hex digest of a file, read in 1 MiB chunks."""
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path):
    """Parse a JSON file that may have a byte-order mark and comments."""
    return json.loads(without_json_comments(path.read_text(encoding='utf-8-sig')))


def without_json_comments(text):
    """JSON text with its // and /* */ comments removed; strings are left whole."""
    return _STRING_OR_COMMENT.sub(lambda match: match[0] if match[0].startswith('"') else '', text)


def write_json(path: Path, data):
    """Write indented UTF-8 JSON, creating the folder if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def texture_file(folder, relative):
    """The texture at folder/relative as a .png, else as a .tga; None when there is neither."""
    for extension in ('.png', '.tga'):
        path = Path(folder) / (relative + extension)
        if path.exists():
            return path
    return None


def samples_path(root=None):
    """Mojang's bedrock-samples checkout: BEDROCK_SAMPLES, else a folder beside the project."""
    configured = os.environ.get('BEDROCK_SAMPLES')
    if configured:
        return Path(configured)
    return Path(root or PROJECT_ROOT).parent / 'bedrock-samples'


def terrain_pack(root=None):
    """Built terrain resource pack whose materials the engine reads.

    BCT_TERRAIN_PACK when set, else build/native/vv-combined-256 in the project.
    """
    configured = os.environ.get('BCT_TERRAIN_PACK')
    if configured:
        return Path(configured)
    return Path(root or PROJECT_ROOT) / 'build/native/vv-combined-256'


def scratch(folder):
    """Create a working folder (tests use folders under build/) and return it."""
    Path(folder).mkdir(parents=True, exist_ok=True)
    return folder
