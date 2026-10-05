"""Authored CTM tiles as native previews, without changing CTM matching.

A native block face shows one fixed image. Where a CTM rule that does not
depend on the world swaps a texture for an authored tile on a lone block, the
native material shows that tile instead. The JavaScript selector
(select_java_native_fallbacks.mjs) runs the engine's own rule resolver on an
isolated block, so the choice is the engine's. Rules that depend on biomes,
heights, states or neighbors stay runtime-only, and the model bindings the CTM
selectors read are left unchanged.
"""
from collections import defaultdict
import json
from pathlib import Path
import shutil
import subprocess

from common import write_json

NODE_MESSAGE = 'Install Node.js 18 or later from https://nodejs.org and reopen the terminal before converting.'
NODE_VERSION_CHECK = "process.exit(Number(process.versions.node.split('.')[0]) >= 18 ? 0 : 1)"
# Tiles compiled from compact CTM rules: render materials made by the converter, not source sprites.
COMPILED_TILES = 'textures/compact_tiles/'


def require_node_runtime():
    """Fail before any conversion work when the JavaScript selector cannot run; returns the node executable."""
    executable = shutil.which('node')
    if not executable:
        raise ValueError(NODE_MESSAGE)
    try:
        result = subprocess.run([executable, '--eval', NODE_VERSION_CHECK], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(NODE_MESSAGE) from error
    if result.returncode:
        raise ValueError(NODE_MESSAGE)
    return executable


def select_native_fallbacks(bindings, rules, destination):
    """Resolve unconditional replacements on an isolated representative cube.

    Sets bindings['nativeFallbacks'] ({Bedrock material: tile}) and
    bindings['nativeFaceFallbacks'] ({Java texture: tile}), writes
    native-fallbacks.json into destination and returns that report.
    """
    from terrain_providers import ROOT
    selector_input = {key: value for key, value in rules.items() if key == 'rules'}
    selector_input['baseTextures'] = bindings['baseTextures']
    selector_input['layerTextures'] = _layer_textures(bindings)
    result = subprocess.run([require_node_runtime(), str(ROOT / 'converter/select_java_native_fallbacks.mjs')],
                            input=json.dumps(selector_input), text=True, capture_output=True, check=True, cwd=ROOT)
    faces = json.loads(result.stdout)
    tiles_by_source = defaultdict(set)
    for face in faces:
        tiles_by_source[face['source']].add(face['replacement'])
    unique = {source: next(iter(tiles)) for source, tiles in tiles_by_source.items() if len(tiles) == 1}
    ambiguous = {source: sorted(tiles) for source, tiles in tiles_by_source.items() if len(tiles) > 1}
    # Compiled compact tiles keep their original native preview; the runtime
    # draws the compiled tile with its own material channels.
    compiled = {source: tile for source, tile in unique.items() if tile.startswith(COMPILED_TILES)}
    unique = {source: tile for source, tile in unique.items() if source not in compiled}
    # The Java selectors keep the original model bindings; only the native
    # material export reads these preview substitutions.
    replacements = {material: unique[source] for material, source in bindings['authoredMaterialBindings'].items()
                    if source in unique}
    bindings['nativeFallbacks'] = replacements
    bindings['nativeFaceFallbacks'] = dict(unique)
    report = {'native_material_replacements': replacements, 'resolved_faces': faces, 'ambiguous_sources': ambiguous,
              'runtime_only_compiled_previews': compiled,
              'selection': ('Unconditional CTM rules on an isolated origin cube; '
                            'world-dependent rules remain runtime-only.'),
              'runtime_source_bindings_modified': False}
    write_json(Path(destination) / 'native-fallbacks.json', report)
    return report


def _layer_textures(bindings):
    """Every face layer texture of every block's Java models, as [{'block', 'face', 'texture'}]."""
    layers = []
    for block, variants in bindings.get('baseTextureVariants', {}).items():
        for variant in variants:
            for choice in variant.get('modelChoices', [variant]):
                for face, face_layers in choice.get('faceLayers', {}).items():
                    for layer in face_layers:
                        layers.append({'block': block, 'face': face, 'texture': layer['texture']})
    return layers
