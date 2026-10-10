# Making Bedrock packs for BCT

This guide is for authors of **Bedrock** resource packs. With the Bedrock
Connected Textures engine installed, your pack can do what Java packs do with
OptiFine or Continuity, written in plain Bedrock files, with no Java pack, no
OptiFine properties and no conversion:

| Feature | What players see | Graphics modes |
| --- | --- | --- |
| [Random variants](#1-random-variants-no-bct-needed) | Weighted random textures per block | All (plain Bedrock, no BCT needed) |
| [Patterns](#2-patterns) | Fixed repeats (a 3x3 stone pattern) and random tiles that never turn | All |
| [3D leaves](#3-3d-leaves) | Your leaf models in place of vanilla leaves, with vanilla decay and drops | All |
| [Edges](#4-edges) | Grass, sand or any ground spreading onto the tops of blocks beside it | All |
| [Overlays](#5-overlays) | The 17-tile overlay template on any face (grass over a cliff edge) | All |
| [Connected blocks](#6-connected-blocks) | Glass and other blocks joining their neighbours | All, including ray tracing |
| [Carriers](#7-carriers) | The full 47-tile connected look, inner corners included | Classic and Vibrant Visuals |

Players install BCT and your pack. You write your pack; BCT's command-line
tool (`bct.py`) writes the custom blocks for you from your textures and
models, with the vanilla block's gameplay already filled in, and checks the
result.

Contents:

- [How it works](#how-it-works)
- [Setup](#setup)
- [Quick start](#quick-start)
- Features 1 to 7 (above)
- [The data file](#the-data-file-scriptsbctjs)
- [Checking and testing](#checking-and-testing)
- [Updating BCT](#updating-bct)
- [Limits](#limits)
- [Licence](#licence)

## How it works

Bedrock sets two limits. A script cannot create block types or textures, so
every custom block the engine uses must be defined in your pack. And a script
cannot read another pack's files, so your behavior pack sends its BCT data to
the engine with a small script.

So a BCT-ready pack is your normal pack plus:

```
MyPack_RP/
  textures/blocks/...              your textures (any resolution, PBR texture sets as usual)
  textures/terrain_texture.json    bct.py adds its entries here
  models/blocks/...                your leaf models; geometry bct.py writes for connected blocks
MyPack_BP/
  manifest.json                    depends on the BCT engine (bct.py init adds it)
  blocks/...                       custom blocks bct.py writes
  loot_tables/<pack>/...           their drops (vanilla drops, written by bct.py)
  scripts/main.js                  copied by bct.py; do not edit
  scripts/publisher.js             copied by bct.py; do not edit
  scripts/bct.js                   your BCT data (below); bct.py adds to it, you can edit it
  scripts/bct-overlays.js          written by `bct.py overlays`; do not edit
  scripts/bct-carriers.js          written by `bct.py carriers`; do not edit
```

When a world loads, the engine reads your data, finds the vanilla blocks it
names in the loaded chunks around players and swaps them for your blocks,
nearest first. Blocks players place swap at once. Beyond the simulation
distance, leaves change in the background, one area at a time; other blocks
change when a player comes near.
Swapped blocks stay swapped. To take your pack out of a world, players run
`/scriptevent bct:control restore`. They wait until it says the world is back
to vanilla blocks, then remove the packs. If they remove the pack first, its
blocks show as unknown blocks until the pack is added back.
`python bct.py restore` works with the game closed, but it only knows the
blocks of converted packs, not yours. So players of your pack need the
command. Tell them this where you share the pack.

Your blocks keep the vanilla block's gameplay: mining time and tool, drops
(Fortune, Silk Touch), explosion resistance, map colour, sounds,
flammability, redstone, note block instruments, stripping logs with an axe.
Blocks whose gameplay a custom block cannot reproduce stay vanilla and are
refused: grass, dirt, sand, gravel, farmland, mycelium and the like (plants
are placed only on the real blocks), infested blocks, bookshelves (enchanting
tables count real bookshelves). Their textures are still yours through
normal Bedrock texture variations, edges and overlays.

## Setup

You need, once:

1. [Python](https://www.python.org) 3.10 or later, with Pillow and NumPy:
   `python -m pip install -r requirements.txt` in the repository.
2. [Node.js](https://nodejs.org) 18 or later (`bct.py check` runs the
   engine's own code).
3. This repository and Mojang's samples next to it:

   ```
   git clone https://github.com/Icehunter/bedrock-connected-textures
   git clone https://github.com/Mojang/bedrock-samples
   ```

   The samples give `bct.py` the game's block list, states, sounds and JSON
   schemas. Elsewhere, pass `--samples <folder>` or set `BEDROCK_SAMPLES`.

Run every command from the repository folder:

```
python bct.py --help
python bct.py <command> --help
```

Develop with your packs in `development_behavior_packs` and
`development_resource_packs`, so the game reloads them every time a world
opens, and test in a copy of a world.

## Quick start

A 3x3 stone pattern, from nothing:

```
python bct.py init mypack --bp MyPack_BP --rp MyPack_RP
python bct.py block minecraft:stone mypack:stone --repeat 3 3 --grid stone_grid.png --bp MyPack_BP --rp MyPack_RP
python bct.py check --bp MyPack_BP --rp MyPack_RP
```

`stone_grid.png` is one image holding the nine tiles, three across and three
down. Add both packs and the BCT engine to a world: stone shows your pattern.

## 1. Random variants (no BCT needed)

Bedrock picks a texture per block position by itself, for any block,
including grass, dirt and sand:

```json
"stone": {
  "textures": { "variations": [
    { "path": "textures/blocks/stone_1", "weight": 2 },
    { "path": "textures/blocks/stone_2", "weight": 1 }
  ] }
}
```

This goes in your `terrain_texture.json` and works with or without BCT.
Make the tiles join seamlessly; Bedrock may also turn the tiles of blocks
marked `isotropic` in `blocks.json`. Use a BCT pattern instead when the tiles
must never turn, or must follow a fixed layout.

## 2. Patterns

The engine swaps a vanilla block for your copy and sets its pattern states.

```
python bct.py block <vanilla block> <your block> (--repeat W H | --random WEIGHT...) --bp ... --rp ...
```

| Option | Meaning |
| --- | --- |
| `--repeat W H` | A fixed pattern W tiles wide and H high. Tile 0 is the top left; tiles count row by row, as in a Java repeat. Up to 16 by 16; sizes that share a factor (2x4, 3x3, 4x4) keep the block small. |
| `--random W...` | 2 to 16 tiles with these weights (`4 2 1 1`: tile 0 shows half the time). Unlike texture variations, they never turn. |
| `--grid image.png` | Cuts the tiles from one image: W x H square tiles for a repeat, the tiles side by side for random. |
| `--texture-dir` | Where the tiles go (default `textures/blocks`). |

The tiles are `<pack>_<name>_<n>` (`mypack_stone_0.png` ...). Without
`--grid`, the command lists the files to add. PBR texture sets work as usual:
give a tile a `.texture_set.json` next to it.

In `bct.js`:

```js
blocks: {
  "minecraft:stone":       { block: "mypack:stone",  pattern: { repeat: [3, 3] } },
  "minecraft:cobblestone": { block: "mypack:cobble", pattern: { random: [4, 2, 1, 1] } }
}
```

`bct.py block` also writes fields such as `open: true` (see-through blocks),
`strip` (logs), `xp` and `tool` (ores): they tell the engine the vanilla
behaviour to keep. Leave them as written.

What the block gets: the vanilla block's own states as `bct:<name>`
(`bct:pillar_axis` turns a log's pattern with the log), the pattern states
(`bct:x`, `bct:y`, `bct:z` for a repeat, `bct:r` for random), one texture per
face for every place in the pattern, and the gameplay listed above.
See-through blocks (glass) hide their faces against each other and take
`alpha_test` or `blend` from your tiles' alpha.

Slabs, stairs, fences and walls take patterns too:

```
python bct.py block minecraft:oak_slab mypack:oak_slab --repeat 2 2 ...
```

Each face shows the tile that a full block in the same place would show. So a
slab or a stair lines up with the planks next to it. The side of a bottom slab
shows the lower half of the tile.

The block gets its own geometry, with a part for each shape. Its states pick
the part that shows:

| Block | States |
| --- | --- |
| Slab | `bct:vertical_half` (2 shapes) |
| Stairs | `bct:weirdo_direction`, `bct:upside_down_bit`, `bct:corner` (40 shapes) |
| Fence | `bct:connection_north`, `_east`, `_south`, `_west` (16 shapes) |
| Wall | `bct:wall_connection_type_north`, `_east`, `_south`, `_west`, `bct:wall_post_bit` |

The collision and selection boxes follow the shape, and the block can hold
water. The engine works out a stair's corner, and how a fence or a wall joins
the blocks next to it. `bct.py` adds `shape: "slab"`, `"stairs"`, `"fence"` or
`"wall"` to the entry. Fence gates stay vanilla. Double slabs
(`minecraft:oak_double_slab`) are full cubes and drop two slabs.

The block file is yours afterwards: change its textures, add components, or
write your own from scratch. The engine needs only the states your entry
uses; vanilla states you leave out are not mirrored.

## 3. 3D leaves

Bedrock cannot give vanilla leaves a model, so the engine swaps them for your
leaf block and keeps their gameplay: decay when the logs are gone (as
slowly as vanilla: one random tick at a time), placed leaves never decaying,
vanilla drops, shears and Silk Touch giving the leaf block.

```
python bct.py leaves minecraft:oak_leaves mypack:oak_leaves --bp ... --rp ...
    --near geometry.mypack.leaf_a geometry.mypack.leaf_a@90 geometry.mypack.leaf_b --near-weights 2 2 1
    --far geometry.mypack.leaf_wispy --near-up-to 2
    --texture textures/blocks/oak_leaves_3d
```

| Option | Meaning |
| --- | --- |
| `--near` | Your leaf models (geometry identifiers from your `models/blocks/*.geo.json`). Add `@90`, `@180` or `@270` to use a model turned. |
| `--near-weights` | How often each model shows (default all equal). The pick is fixed per position. |
| `--far`, `--far-weights` | Other models for leaves farther from the logs (optional). |
| `--near-up-to` | Leaves up to this log distance use the `--near` models (1 to 5, default 3). A leaf touching a log is at distance 1; Java counts up to 7 (no log near). |
| `--texture` | The leaf texture in your pack (default `textures/blocks/<pack>_<name>`). |

Without `--near` the block is a plain cube until you run the command again
with your models. The command lists anything still missing (texture, model
identifiers it cannot find).

In `bct.js`:

```js
leaves: {
  "minecraft:oak_leaves":   { block: "mypack:oak_leaves", models: { near: [2, 2, 1], far: [1], nearUpTo: 2 } },
  "minecraft:birch_leaves": { block: "mypack:birch_leaves", models: [3, 1] },
  "minecraft:spruce_leaves":{ block: "mypack:spruce_leaves" }
}
```

The leaf block has the states `bct:persistent_bit` and `bct:update_bit`
(the vanilla leaf states), `bct:t` (which model, when there is more than
one) and `bct:look` (near or far), and the `bct:leaf` component the engine
uses for decay. It takes the vanilla leaf's biome tint: the foliage colour for
oak, jungle, acacia, dark oak and mangrove, birch's and spruce's own colours,
and none for cherry, azalea and pale oak.

## 4. Edges

Ground spreading onto the tops of the blocks beside it: grass creeping over
stone, sand over gravel. Each edge is a see-through, walk-through block the
engine places in the air above the block it spreads onto, drawing a thin
plane per side and per corner. Edges spread between blocks at the same
height, onto top faces.

```
python bct.py edge mypack:grass_edge --from minecraft:grass_block --onto minecraft:stone minecraft:dirt
    --texture textures/blocks/grass_top --tint grass --bp ... --rp ...
```

| Option | Meaning |
| --- | --- |
| `--from` | The block that spreads (one or more). |
| `--onto` | The blocks it spreads onto. Your pattern blocks count as the vanilla block they stand for. |
| `--texture` | The texture to cut the edge from: BCT cuts a ragged edge from its own brightness, and keeps its PBR maps. |
| `--edge-texture`, `--corner-texture` | Or your own cut tiles: the edge hanging from the tile's top side, the corner in its top right corner. |
| `--tint` | `grass` or `foliage` for a biome colour (draw the texture in grey), else `none`. |

The edge block id may use only letters, digits and `_`.

In `bct.js`, a list; earlier edges lie over later ones:

```js
edges: [
  { from: "minecraft:grass_block", onto: ["minecraft:stone", "minecraft:dirt"], block: "mypack:grass_edge" },
  { from: ["minecraft:sand", "minecraft:suspicious_sand"], onto: ["minecraft:stone"], block: "mypack:sand_edge" }
]
```

A cell holds one block: where two edges reach the same block, the earlier one
draws there. Edges cost nothing per frame.

## 5. Overlays

The 17-tile overlay template, the same as Java overlays, on any face: grass
on the top and sides of bricks beside it, grass hanging over a cliff edge.
The tile choice follows the neighbours as Continuity picks it.

Tiles, by where the overlay lies on the face (texture space):

| Tile | Covers | Tile | Covers | Tile | Covers |
| --- | --- | --- | --- | --- | --- |
| 0 | corner down+right | 6 | left+down+up | 12 | down+right+up |
| 1 | down | 7 | right | 13 | left+right+up |
| 2 | corner left+down | 8 | all four | 14 | corner right+up |
| 3 | down+right | 9 | left | 15 | up |
| 4 | left+down | 10 | right+up | 16 | corner up+left |
| 5 | left+down+right | 11 | left+up | | |

Draw the 17 tiles (`grass_overlay_0.png` to `grass_overlay_16.png`), or cut
them from a ground texture with BCT's edge shape:

```
python bct.py overlay-tiles --texture textures/blocks/grass_top --tiles textures/blocks/grass_overlay --rp ...
```

List the overlays in `bct.js`:

```js
overlays: [
  { tiles: "textures/blocks/grass_overlay", onto: ["minecraft:stone_bricks", "minecraft:brick_block"],
    from: "minecraft:grass_block", tint: "grass" }       // faces: optional, all six by default
]
```

Then build their surface blocks:

```
python bct.py overlays --bp ... --rp ...
```

An overlay needs several generated surface blocks with hundreds of states,
so this is a build step: `bct.py overlays` writes them
(`blocks/bct_<pack>_overlay_*`, their models and tiles) and
`scripts/bct-overlays.js`, and removes what an earlier run wrote. Run it
again whenever you change `overlays`: until you do, the engine says in chat
that the overlays changed since they were built.

## 6. Connected blocks

Glass joining glass, drawn by the block itself, so it works in every graphics
mode, ray tracing included, at no per-frame cost. The engine keeps six
neighbour states on your block and updates them when a neighbour is placed or
broken; each quarter of each face shows the quarter of one of four tiles:

| Tile | Of a 47-tile set | Shows on a quarter that is |
| --- | --- | --- |
| alone | 0 | joined on neither of its edges |
| across | 2 | joined along its left or right edge |
| along | 24 | joined along its top or bottom edge |
| joined | 26 | joined along both |

Every case draws as in the 47-tile set except inner corners (where only a
diagonal neighbour is missing): Bedrock block states cannot hold the
diagonal neighbours. Use [carriers](#7-carriers) for those.

```
python bct.py connected minecraft:glass mypack:glass --ctm textures/blocks/glass_ctm --bp ... --rp ...
```

| Option | Meaning |
| --- | --- |
| `--ctm` | A standard 47-tile set (`glass_ctm_0` to `_46`); tiles 0, 2, 24 and 26 are used. Several sets are random variants. |
| `--alone`, `--across`, `--along`, `--joined` | Or the four tiles one by one; several paths per tile are random variants. |
| `--faces` | The faces that join (default all six). |
| `--face-texture FACE=PATH` | A face that does not join shows this texture instead of the alone tile, such as `up=textures/blocks/sandstone_top`. `PATH,PATH` gives random variants. Repeat for each face. |
| `--joins` | `horizontal` or `vertical` to join only along the texture's left/right or up/down edges. |
| `--connect` | The blocks it joins (default the same block). |

In `bct.js`:

```js
connected: {
  "minecraft:glass": { block: "mypack:glass", open: true },
  "minecraft:oak_planks": { block: "mypack:planks", connect: ["minecraft:oak_planks", "minecraft:spruce_planks"] }
}
```

Random variants become `variations` in your `terrain_texture.json`: Bedrock
picks one per block position, as it does for vanilla blocks, at no cost.

The block has the states `bct:n`, `bct:s`, `bct:w`, `bct:e`, `bct:u` and
`bct:d` (1 where that neighbour joins) and 64 permutations. A block is either
patterned or connected.

## 7. Carriers

The full 47-tile connected look, inner corners included, as converted Java
packs draw it: a thin entity in front of each face shows the tile the
engine picks. Carriers draw in Classic and Vibrant Visuals; in ray tracing
the block shows its own face. They cost a little each frame (a few
milliseconds for a large window seen from both sides), within the engine's
carrier allowance.

```js
carriers: [
  { tiles: "textures/blocks/glass_ctm", blocks: ["minecraft:light_blue_stained_glass"] }   // faces: optional
]
```

```
python bct.py carriers --bp ... --rp ...
```

`bct.py carriers` builds the carrier entities, models, render controllers and
texture atlas into your packs (merging `materials/entity.material` with
yours), records what it wrote in `bct-carriers-files.json` and removes it on
the next run. Run it again whenever you change `carriers`. A block drawn by
carriers is not swapped, so it cannot also be a connected or patterned block.

## The data file: scripts/bct.js

Bedrock scripts cannot load JSON, so the data is a JavaScript module that
exports one object. It reads like JSON; comments are fine:

```js
// My pack's BCT data
export default {
  format: 1,
  pack: "mypack",       // a short id: lowercase letters, digits, - and _
  priority: 0,          // when two BCT packs draw the same block, the higher wins
  blocks: { ... },      // 2. patterns
  leaves: { ... },      // 3. 3D leaves
  edges: [ ... ],       // 4. edges
  overlays: [ ... ],    // 5. overlays (then python bct.py overlays)
  connected: { ... },   // 6. connected blocks
  carriers: [ ... ]     // 7. carriers (then python bct.py carriers)
};
```

Every section is optional. The commands add their entries to the file while
it is plain JSON after `export default`; once you add comments or other
JavaScript, they print the entry for you to paste instead.
`bct.py overlays` and `bct.py carriers` read the file themselves and need it
to stay plain JSON.

### A behavior pack with scripts of its own

`bct.py init` writes `scripts/main.js` only when the pack has no script of
its own. If yours already runs a script, keep it and add BCT's three lines to
it, then run `bct.py init` for the manifest and the other files (it leaves
your script alone and says so):

```js
import { system, world } from '@minecraft/server';
import data from './bct.js';
import overlaySurfaces from './bct-overlays.js';
import carrierSurfaces from './bct-carriers.js';
import { authoredSource, publishSources } from './publisher.js';

publishSources({ system, world, sources: [authoredSource({ ...data, overlaySurfaces, carrierSurfaces })] });
```

Copy `publisher.js`, `bct-overlays.js` and `bct-carriers.js` from a pack `init`
made, or from `engine/publisher.mjs` in this repository (`publisher.js`).

[`docs/bct.schema.json`](bct.schema.json) describes every field (JSON
Schema). Editors that understand JSON Schema can check a JSON copy of the
data as you type.

When the world loads, the engine checks the data and tells players in chat
which entry is wrong, for example:

```
[BCT] mypack (scripts/bct.js): blocks["minecraft:stone"].block: mypack:nope is not a block in your packs (is its blocks/ file in the behavior pack?)
```

A pack with a mistake draws nothing until it is fixed.

## Checking and testing

```
python bct.py check --bp MyPack_BP --rp MyPack_RP
```

checks, before the world loads:

- `bct.js`, compiled with the engine's own code: the messages players would
  see in chat;
- `bct.js` against the schema;
- your packs' block, geometry and culling files against Mojang's schemas
  (the errors the game would put in the content log);
- the textures your BCT atlas entries name;
- that the behavior pack depends on the engine, its `main.js` and
  `publisher.js` are current, and the overlays and carriers are built from the
  current `bct.js`;
- the terrain atlas: the game gives every block texture a slot as wide as the
  widest one, and each animation frame and random variant takes a slot. Past
  16384 pixels square, the game scales the block textures down, so they look
  blurred. Check counts the vanilla textures
  and yours, and says when the atlas is over that size, or near it;
- texture sets (`textures/blocks/*.texture_set.json`): broken ones are
  problems. Notes say when the resource pack's `manifest.json` needs
  `"raytraced"` (ray tracing) or `"pbr"` (Vibrant Visuals) in `capabilities`,
  and which of your textures have no texture set and look flat.

Lines starting with `Note:` are not mistakes; the pack still loads.

In game:

- `/scriptevent bct:control status` shows what the engine received and is
  doing: blocks it swaps, leaves, edges, overlays, carriers. A `broken` list
  names blocks it switched off; turn on debug to see why.
- `/scriptevent bct:config {"debug":true}` writes every rejected entry and swap
  error to the content log. `/scriptevent bct:probe` (with debug on) logs what
  the engine sees on the face under the crosshair.
- The content log is written in batches; quit to the title screen to read all
  of it.

## Updating BCT

```
git pull
python bct.py init <pack> --bp ... --rp ...     # refreshes main.js, publisher.js and the engine dependency
python bct.py overlays --bp ... --rp ...        # if you use overlays
python bct.py carriers --bp ... --rp ...        # if you use carriers
python bct.py check --bp ... --rp ...
```

`init` is safe to run again: it keeps your manifests, blocks and `bct.js`.
Your manifest depends on the engine's version as a minimum, so players with
a newer engine can use your pack.

## Limits

| Limit | Why |
| --- | --- |
| Patterns, leaves and connected blocks swap blocks in the world; they stay swapped. | Bedrock cannot change a vanilla block's look per position or neighbour. |
| Grass, dirt, sand, gravel, farmland, bookshelves and similar stay vanilla. | Their gameplay needs the real block. Use texture variations, edges and overlays. |
| Pattern blocks are full cubes, slabs, stairs, fences and walls; connected blocks are full cubes. Fence gates and buttons stay vanilla. | Custom block geometry is drawn by state, not by the vanilla model; each shape needs its own geometry. |
| Connected blocks draw no inner corners. | 64 states hold six neighbours; diagonals would need more than a block can have. Carriers draw them. |
| Carriers do not draw in ray tracing. | Entity materials have no ray-traced PBR. |
| One edge block per cell. | A cell holds one block. |
| Overlays and carriers on hand-written packs: the 17-tile overlay and 47-tile connected methods. | The other Java methods (overlay random, repeat, fixed; horizontal, vertical) come from the converter. |
| Animated tiles: overlays cannot use them. | Patterns, leaves, edges and connected blocks use your terrain atlas, so your `flipbook_textures.json` animates them. |

## Licence

`main.js`, `publisher.js` and every file `bct.py` writes into your packs may
be shipped under any terms, closed or paid
([LICENSE-EXCEPTION.md](../LICENSE-EXCEPTION.md)). The engine itself stays
GPL-3.0; players install it separately.
