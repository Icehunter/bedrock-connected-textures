"""BCT tools for pack authors, run from a clone of this repository.

    python bct.py init <pack id> --bp MyPack_BP --rp MyPack_RP
    python bct.py block <vanilla block> <your block> --bp MyPack_BP --rp MyPack_RP (--repeat W H | --random WEIGHTS...)
    python bct.py leaves <vanilla leaves> <your block> --bp MyPack_BP --rp MyPack_RP --near GEOMETRY... [--far GEOMETRY...]
    python bct.py edge <your edge block> --from BLOCK... --onto BLOCK... --texture TEXTURE --bp MyPack_BP --rp MyPack_RP
    python bct.py overlay-tiles --texture TEXTURE --tiles TILES --rp MyPack_RP
    python bct.py connected <vanilla block> <your block> (--ctm TILES | --alone T --across T --along T --joined T) --bp MyPack_BP --rp MyPack_RP
    python bct.py overlays --bp MyPack_BP --rp MyPack_RP
    python bct.py carriers --bp MyPack_BP --rp MyPack_RP
    python bct.py check --bp MyPack_BP --rp MyPack_RP

See docs/AUTHORING.md.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'converter'))

from authoring import (AuthoringError, add_edge_to_data, add_to_data, build_carriers, build_overlays, connected_tiles,  # noqa: E402
                       check_pack, init_pack,
                       parse_models, write_block, write_connected, write_edge, write_leaves, write_overlay_tiles)
from common import samples_path  # noqa: E402
from world_restore import WorldRestoreError, restore_world  # noqa: E402


def samples_of(options):
    """Mojang's bedrock-samples folder: --samples, BEDROCK_SAMPLES, or beside this repository."""
    samples = Path(options.samples or samples_path())
    if not (samples / 'metadata/vanilladata_modules/mojang-blocks.json').is_file():
        raise AuthoringError(f"Mojang's bedrock-samples are not in {samples}: run "
                             'git clone https://github.com/Mojang/bedrock-samples next to this repository, '
                             'or give their folder with --samples')
    return samples


def init_command(options):
    written, notes = init_pack(options.bp, options.rp, options.pack)
    for path in written:
        print('Wrote ' + str(path))
    for note in notes:
        print('Note: ' + note)
    if not notes:
        print('The behavior pack sends scripts/bct.js to the BCT engine. Players need BCT installed too.')


def block_command(options):
    pattern = {'repeat': options.repeat} if options.repeat else {'random': options.random}
    entry, missing = write_block(options.bp, options.rp, options.vanilla, options.block, pattern,
                                 samples=samples_of(options), grid=options.grid,
                                 texture_dir=options.texture_dir)
    report_entry(options, entry, 'blocks')
    if missing:
        print('Add these textures to the resource pack (tile 0 is the top left, counted row by row):')
        for path in missing:
            print('  ' + path)


def leaves_command(options):
    near = parse_models(options.near, options.near_weights, '--near') if options.near else None
    far = parse_models(options.far, options.far_weights, '--far') if options.far else None
    if far and not near:
        raise AuthoringError('--far needs --near models too')
    entry, todo = write_leaves(options.bp, options.rp, options.vanilla, options.block, near, far,
                               near_up_to=options.near_up_to, texture=options.texture,
                               samples=samples_of(options))
    report_entry(options, entry, 'leaves')
    if todo:
        print('Still to add to the resource pack:')
        for item in todo:
            print('  ' + item)


def edge_command(options):
    entry = write_edge(options.bp, options.rp, options.block, options.source, options.onto, texture=options.texture,
                       edge_texture=options.edge_texture, corner_texture=options.corner_texture, tint=options.tint,
                       samples=samples_of(options))
    script = Path(options.bp) / 'scripts/bct.js'
    print(f'Wrote {options.block} into {options.bp} and {options.rp}.')
    if add_edge_to_data(script, entry, options.block.split(':', 1)[0]):
        print(f'Added it to the end of the edges in {script}; earlier edges lie over later ones.')
    else:
        print(f'{script} is not plain JSON after `export default`; add this to its edges list:')
        print('  ' + json.dumps(entry))


def overlay_tiles_command(options):
    paths = write_overlay_tiles(options.rp, options.texture, options.tiles)
    print(f'Wrote {len(paths)} overlay tiles: {paths[0]} to {paths[-1]}.')


def overlays_command(options):
    report = build_overlays(options.bp, options.rp, samples=samples_of(options))
    for rule in report['rules_drawn']:
        print(f"overlays[{rule['rule'][1:]}]: drawn on {', '.join(rule['host_blocks'])}")
    for rule in report['rules_not_drawn']:
        print(f"overlays[{rule['rule'][1:]}]: not drawn: {rule['reason']}")
    print(f"{len(report['types'])} surface blocks; scripts/bct-overlays.js holds what the engine needs.")


def connected_command(options):
    tiles = connected_tiles(options.ctm, options.alone, options.across, options.along, options.joined)
    connect = options.connect if options.connect else 'same'
    textures = {}
    for item in options.face_texture or []:
        face, _, paths = item.partition('=')
        if not paths:
            raise AuthoringError('--face-texture is FACE=PATH, such as up=textures/blocks/sandstone_top')
        variants = paths.split(',')
        textures[face] = variants[0] if len(variants) == 1 else variants
    entry = write_connected(options.bp, options.rp, options.vanilla, options.block, tiles, faces=options.faces,
                            joins=options.joins, connect=connect, textures=textures, samples=samples_of(options))
    report_entry(options, entry, 'connected')


def carriers_command(options):
    written = build_carriers(options.bp, options.rp, samples=samples_of(options))
    print(f'Wrote {len(written)} carrier files; scripts/bct-carriers.js holds what the engine needs.')


def check_command(options):
    notes = []
    problems = check_pack(options.bp, options.rp, samples=samples_of(options), notes=notes)
    for problem in problems:
        print('  ' + problem)
    for note in notes:
        print('Note: ' + note)
    if problems:
        print(f'{len(problems)} problem(s): fix them and run python bct.py check again.')
        return 1
    print('The pack is ready for BCT.')
    return 0


def restore_command(options):
    try:
        report = restore_world(options.world, samples_of(options), backup=not options.no_backup)
    except WorldRestoreError as error:
        raise AuthoringError(str(error)) from None
    if report['backup']:
        print('Backup: ' + report['backup'])
    for name in sorted(report['rewritten']):
        print('  ' + name)
    print(f"Changed these blocks in {report['subchunks']} chunk section(s).")
    if report['left']:
        print('These BCT blocks are still in the world: ' + ', '.join(sorted(report['left'])))
        return 1
    print('The world has no BCT blocks. You can open it without the BCT packs.')
    return 0


def report_entry(options, entry, section):
    """Adds the entry to the pack's scripts/bct.js, or prints it when the file is the author's own form."""
    script = Path(options.bp) / 'scripts/bct.js'
    print(f'Wrote {options.block} into {options.bp} and {options.rp}.')
    if add_to_data(script, options.vanilla, entry, options.block.split(':', 1)[0], section=section):
        print(f'Added {options.vanilla} to the {section} of {script}.')
    else:
        print(f'{script} is not plain JSON after `export default`; add this to its {section} section:')
        print(f'  "{options.vanilla}": {json.dumps(entry)}')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='bct.py', description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='Make your packs ready for BCT (manifests and scripts); safe to rerun')
    init.add_argument('pack', help='A short id for your pack, such as mypack')
    init.add_argument('--bp', required=True, help='Your behavior pack folder (made when missing)')
    init.add_argument('--rp', required=True, help='Your resource pack folder (made when missing)')
    init.set_defaults(run=init_command)
    block = commands.add_parser('block', help='Make a pattern copy of a vanilla block in your packs')
    block.add_argument('vanilla', help='The vanilla block, such as minecraft:stone')
    block.add_argument('block', help='Your block id, such as mypack:stone')
    block.add_argument('--bp', required=True, help='Your behavior pack folder')
    block.add_argument('--rp', required=True, help='Your resource pack folder')
    kind = block.add_mutually_exclusive_group(required=True)
    kind.add_argument('--repeat', nargs=2, type=int, metavar=('WIDTH', 'HEIGHT'), help='A fixed pattern of tiles')
    kind.add_argument('--random', nargs='+', type=int, metavar='WEIGHT', help='Random tiles with these weights')
    block.add_argument('--grid', type=Path, help='One image holding every tile, cut into the tile files')
    block.add_argument('--texture-dir', default='textures/blocks', help='Where the tiles live in the resource pack')
    block.add_argument('--samples', type=Path, help="Mojang's bedrock-samples folder")
    block.set_defaults(run=block_command)
    leaves = commands.add_parser('leaves', help='Make a 3D leaf block for a vanilla leaf in your packs')
    leaves.add_argument('vanilla', help='The vanilla leaves, such as minecraft:oak_leaves')
    leaves.add_argument('block', help='Your block id, such as mypack:oak_leaves')
    leaves.add_argument('--bp', required=True, help='Your behavior pack folder')
    leaves.add_argument('--rp', required=True, help='Your resource pack folder')
    leaves.add_argument('--near', nargs='+', metavar='GEOMETRY',
                        help='Your leaf models (geometry ids; add @90, @180 or @270 to turn one)')
    leaves.add_argument('--near-weights', nargs='+', type=int, metavar='WEIGHT', help='How often each --near model shows')
    leaves.add_argument('--far', nargs='+', metavar='GEOMETRY', help='Other models for leaves farther from logs')
    leaves.add_argument('--far-weights', nargs='+', type=int, metavar='WEIGHT', help='How often each --far model shows')
    leaves.add_argument('--near-up-to', type=int, default=3,
                        help='The log distance (1 to 5) up to which leaves use --near models (default 3)')
    leaves.add_argument('--texture', help='Your leaf texture in the resource pack, such as textures/blocks/oak_leaves')
    leaves.add_argument('--samples', type=Path, help="Mojang's bedrock-samples folder")
    leaves.set_defaults(run=leaves_command)
    edge = commands.add_parser('edge', help='Make an edge block: a ground block spreading onto the tops of others')
    edge.add_argument('block', help='Your edge block id (letters, digits and _), such as mypack:grass_edge')
    edge.add_argument('--from', dest='source', nargs='+', required=True, metavar='BLOCK',
                      help='The block that spreads, such as minecraft:grass_block')
    edge.add_argument('--onto', nargs='+', required=True, metavar='BLOCK', help='The blocks it spreads onto')
    edge.add_argument('--texture', help='The texture to cut the edge from, such as textures/blocks/grass_top')
    edge.add_argument('--edge-texture', help='Or your own cut edge tile, hanging from its top side (with --corner-texture)')
    edge.add_argument('--corner-texture', help='Your own cut corner tile, in its top right corner (with --edge-texture)')
    edge.add_argument('--tint', default='none', choices=('none', 'grass', 'foliage'), help='Biome colour, as grass has')
    edge.add_argument('--bp', required=True, help='Your behavior pack folder')
    edge.add_argument('--rp', required=True, help='Your resource pack folder')
    edge.add_argument('--samples', type=Path, help="Mojang's bedrock-samples folder")
    edge.set_defaults(run=edge_command)
    tiles = commands.add_parser('overlay-tiles', help='Cut the 17 overlay tiles from a ground texture')
    tiles.add_argument('--texture', required=True, help='The ground texture, such as textures/blocks/grass_top')
    tiles.add_argument('--tiles', required=True, help='Where the tiles go, such as textures/blocks/grass_overlay')
    tiles.add_argument('--rp', required=True, help='Your resource pack folder')
    tiles.set_defaults(run=overlay_tiles_command)
    overlays = commands.add_parser('overlays', help='Build the surface blocks for the overlays in scripts/bct.js')
    overlays.add_argument('--bp', required=True, help='Your behavior pack folder')
    overlays.add_argument('--rp', required=True, help='Your resource pack folder')
    overlays.add_argument('--samples', type=Path, help="Mojang's bedrock-samples folder")
    overlays.set_defaults(run=overlays_command)
    connected = commands.add_parser('connected', help='Make a connected copy of a vanilla block, such as glass')
    connected.add_argument('vanilla', help='The vanilla block, such as minecraft:glass')
    connected.add_argument('block', help='Your block id, such as mypack:glass')
    connected.add_argument('--ctm', nargs='+', help='A standard 47-tile set, such as textures/blocks/glass_ctm '
                           '(glass_ctm_0 to _46); several sets are random variants')
    connected.add_argument('--alone', nargs='+', help='Or the four tiles: a block on its own (several: random variants)')
    connected.add_argument('--across', nargs='+', help='Joined left and right')
    connected.add_argument('--along', nargs='+', help='Joined up and down')
    connected.add_argument('--joined', nargs='+', help='Joined all round')
    connected.add_argument('--face-texture', action='append', metavar='FACE=PATH',
                           help='A face that does not join shows this texture (PATH,PATH,... for random variants)')
    connected.add_argument('--faces', nargs='+', help='The faces that join (default all six)')
    connected.add_argument('--joins', default='all', help='all, horizontal or vertical (bookshelves: horizontal)')
    connected.add_argument('--connect', nargs='+', metavar='BLOCK', help='The blocks it joins (default: the same block)')
    connected.add_argument('--bp', required=True, help='Your behavior pack folder')
    connected.add_argument('--rp', required=True, help='Your resource pack folder')
    connected.add_argument('--samples', type=Path, help="Mojang's bedrock-samples folder")
    connected.set_defaults(run=connected_command)
    carriers = commands.add_parser('carriers', help='Build the carriers (full 47-tile look) for the carriers in scripts/bct.js')
    carriers.add_argument('--bp', required=True, help='Your behavior pack folder')
    carriers.add_argument('--rp', required=True, help='Your resource pack folder')
    carriers.add_argument('--samples', type=Path, help="Mojang's bedrock-samples folder")
    carriers.set_defaults(run=carriers_command)
    check = commands.add_parser('check', help='Check a pack: its data, its files and how it is wired to the engine')
    check.add_argument('--bp', required=True, help='Your behavior pack folder')
    check.add_argument('--rp', required=True, help='Your resource pack folder')
    check.add_argument('--samples', type=Path, help="Mojang's bedrock-samples folder")
    check.set_defaults(run=check_command)
    restore = commands.add_parser('restore', help='Put every BCT block in a world back to its vanilla block, '
                                  'with the game closed; works after the packs are removed')
    restore.add_argument('world', help='The world folder (the one holding level.dat and db)')
    restore.add_argument('--no-backup', action='store_true', help='Do not zip the world first')
    restore.add_argument('--samples', type=Path, help="Mojang's bedrock-samples folder")
    restore.set_defaults(run=restore_command)
    options = parser.parse_args(argv)
    try:
        return options.run(options) or 0
    except AuthoringError as error:
        print('bct.py: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
