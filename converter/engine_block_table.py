"""Write the engine's built-in tables of vanilla blocks (engine/vanilla-blocks.mjs) and leaf drops.

The engine has to know which blocks are opaque full cubes (they hide the faces
next to them) and which are see-through. Converted packs carry those lists in
their data; a pack written by hand (BCT 1.1 authoring) should not have to, and
the game's script API cannot tell (there is no "is solid" query). So the engine
ships the vanilla lists itself, made here from the vanilla Java models and
textures the converter already reads, mapped to Bedrock block ids, with the
policy's see-through blocks (open_blocks) left out of the solid list. The
leaves and logs that leaf decay counts, and its distances, come from the
policy's leaf settings.

Bedrock draws vanilla leaf drops in code (it has no loot table to point a
custom leaf block at), so the drops `bct.py leaves` gives a pack's leaf block
come from the vanilla Java loot tables in Bedrock form, with each leaf's
Bedrock tint, in converter/data/vanilla-leaves.json.

Run after a game update:
    python converter/engine_block_table.py --vanilla client-26.3.jar --samples ../bedrock-samples
"""
import argparse
import json
from pathlib import Path
import tempfile
import zipfile

from block_ids import java_id, known_blocks
from common import read_json, samples_path, write_json
from java_block_bindings import resolve_bindings
from java_model_tints import FOLIAGE_BLOCKS, FIXED_VANILLA
from java_pack_api import PackStack
from model_blocks import _item_id, bedrock_loot
from native_replacement import _matches, load_policy

ROOT = Path(__file__).resolve().parents[1]
# Full cubes the game draws opaque whose Java models the opacity check misreads: a tinted overlay layer
# (grass, mycelium) or textures that change with the block's state (pumpkins, barrels, hives).
OPAQUE_ANYWAY = frozenset({
    'minecraft:barrel', 'minecraft:bee_nest', 'minecraft:beehive', 'minecraft:carved_pumpkin', 'minecraft:crafter',
    'minecraft:grass_block', 'minecraft:mycelium', 'minecraft:observer', 'minecraft:pumpkin',
    'minecraft:respawn_anchor', 'minecraft:sculk_catalyst'})
OUTPUT = ROOT / 'engine/vanilla-blocks.mjs'
LEAVES_OUTPUT = ROOT / 'converter/data/vanilla-leaves.json'


def vanilla_tables(vanilla_jar, samples):
    """{'solid': [...], 'open': [...]} for the game's blocks, from the vanilla Java models alone."""
    samples = Path(samples)
    with tempfile.TemporaryDirectory() as folder:
        empty = Path(folder) / 'vanilla.zip'
        with zipfile.ZipFile(empty, 'w') as archive:
            archive.writestr('pack.mcmeta', json.dumps({'pack': {'pack_format': 88, 'description': ''}}))
        with PackStack([empty]) as stack:
            bindings = resolve_bindings(stack, Path(vanilla_jar), samples / 'resource_pack')
    known = set(known_blocks(samples))
    policy = load_policy()
    open_blocks = sorted(name for name in known if _matches(name, policy.get('open_blocks', [])))
    full = set(bindings['fullCubeBlocks']) & known
    opaque = (set(bindings['opaqueBlocks']) | OPAQUE_ANYWAY) & known
    solid = sorted((full & opaque) - set(open_blocks))
    decay = leaf_settings(policy)['decay']
    # Full cubes, see-through ones (glass, ice) included: the blocks overlays and carriers can draw on.
    return {'solid': solid, 'open': open_blocks, 'cubes': sorted(full),
            'leaves': sorted(name for name in known if _matches(name, decay['leaves'])),
            'logs': sorted(name for name in known if _matches(name, decay['logs'])),
            'decay': {'bedrockDistance': decay['bedrock_distance'], 'javaDistance': decay['java_distance'],
                      'logRadius': decay['log_update_radius'], 'leafRadius': decay['leaf_update_radius']}}


def leaf_settings(policy):
    """The policy's model_blocks entry for leaves (gameplay, tints, decay)."""
    return next(entry for entry in policy['model_blocks'] if entry['behavior'] == 'leaves')


def leaf_table(vanilla_jar, samples, leaves, policy):
    """{leaf block: {'loot': Bedrock loot table, 'tint': Bedrock tint_method or None}} from the vanilla Java data."""
    samples = Path(samples)
    known_items = {item['name'] for item in
                   read_json(samples / 'metadata/vanilladata_modules/mojang-items.json')['data_items']}
    tints = leaf_settings(policy).get('tint_methods', {})
    table = {}
    with zipfile.ZipFile(vanilla_jar) as vanilla:
        for block in leaves:
            java = java_id(block)
            name = java.split(':', 1)[1]
            loot = json.loads(vanilla.read(f'data/minecraft/loot_table/blocks/{name}.json'))
            tint = tints.get(block)
            if tint is None and name in FOLIAGE_BLOCKS:
                tint = 'default_foliage'
            if tint is None and name in FIXED_VANILLA:
                raise ValueError(f'{block} has a fixed Java tint and no Bedrock tint method in the policy')

            def item_id(item):
                return _item_id(item, known_items)

            table[block] = {'loot': bedrock_loot(loot, item_id, item_id(java)), 'tint': tint}
    return table


def module_text(tables, source):
    """The engine module: two frozen arrays."""
    lines = ['// Vanilla block shapes for the engine, written by converter/engine_block_table.py from ' + source + '.',
             '// Opaque full cubes hide the faces next to them; open blocks let a neighbour show.']
    for name in ('solid', 'open', 'cubes', 'leaves', 'logs'):
        lines.append(f'export const VANILLA_{name.upper()} = Object.freeze({json.dumps(tables[name])});')
    lines.append('// Leaf decay: Bedrock and Java log distances and how far log and leaf changes reach.')
    lines.append(f"export const LEAF_DECAY = Object.freeze({json.dumps(tables['decay'])});")
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--vanilla', required=True, type=Path, help='The vanilla Java client jar')
    parser.add_argument('--samples', type=Path, help="Mojang's bedrock-samples folder")
    options = parser.parse_args()
    samples = options.samples or samples_path()
    tables = vanilla_tables(options.vanilla, samples)
    OUTPUT.write_text(module_text(tables, options.vanilla.name), encoding='utf-8')
    print(f'{OUTPUT}: {len(tables["solid"])} solid, {len(tables["open"])} open, {len(tables["cubes"])} full cube blocks')
    write_json(LEAVES_OUTPUT, leaf_table(options.vanilla, samples, tables['leaves'], load_policy()))
    print(f'{LEAVES_OUTPUT}: {len(tables["leaves"])} leaves')


if __name__ == '__main__':
    main()
