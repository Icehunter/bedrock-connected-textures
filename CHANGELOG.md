# Changelog

## Unreleased

Converted packs:
- Grass, sand and other blocks that stay vanilla show the pack's random tiles
  at 512 pixels too. Before, a 512 pixel pack could show its base texture
  there, which in some packs only says to enable connected textures.
- The converter warns when a pack is too large for the game's terrain atlas.
  The game then scales the block textures down and blurs them.
  `--scale-to-atlas` scales them down first, so they stay sharp. It never
  scales up.

Bedrock pack authors:
- `bct.py check` says when the terrain atlas is full or near full. Then the
  game scales the block textures down, so they look less sharp. Check counts
  the vanilla textures and the pack's, at the size of the widest one.
- `bct.py check` reads the texture sets. Broken ones are problems. Notes say
  when the manifest needs `raytraced` or `pbr`, and which textures have no
  texture set.
- Texture sets with `.tga` layers count as complete.

World restore:
- `bct.py restore` works on chunk sections that hold a single block type.

## 1.2.0

Converted packs:
- Slabs, stairs, fences and walls show the pack's patterns and random tiles.
  The pattern lines up with the full blocks next to them.
- These blocks keep the vanilla shape. You mine them, walk on them and break
  them like vanilla blocks. They drop the same items, make the same sounds
  and can hold water.
- Stairs make corners, and fences and walls join the blocks next to them, as
  in Java. Fence gates and buttons stay vanilla.
- A double slab counts as a full block and drops two slabs.
- The game slows down when a pack has too many block states (more than
  65,536). The converter counts them the way the game does and keeps the
  pack under that number. It leaves out dyed blocks first (concrete, wool,
  terracotta), then the blocks that cost the most. Those blocks keep the
  pack's plain texture. `--max-permutations` changes the number.
- A rule can name the state of a slab, stair, fence, wall or pane, such as
  `oak_slab:type=top`. These rules convert. If Bedrock has no such state, the
  converter leaves the rule out and says so. Before, it stopped.

Bedrock pack authors:
- `bct.py block` takes slabs, stairs, fences and walls.
- Connected blocks: each tile can have random variants. The game picks one
  for each block.
- Connected blocks: a face that does not join can show its own texture, such
  as a sandstone top.
- Poplar planks can be swapped like the other planks.

Everyone:
- Far from players, the engine only changes leaves. Leaves are what you see
  from far away. Other blocks change when a player comes near, so much less
  of the world changes. `/scriptevent bct:config {"far":{"blocks":1}}`
  changes blocks far away too, as in 1.1.
- Far away, the areas in front of you change first.
- `/scriptevent bct:control restore` puts the whole world back to vanilla
  blocks: swapped blocks, leaves, overlays and edges, near and far. It goes
  on after a restart and says when it is done. Then you can remove the packs.
  `bct:control on` cancels it.
- `python bct.py restore <world folder>` does the same with the game closed.
  It works without commands, and after the packs are removed. It zips the
  world first.
- A pack removed by mistake is not lost: its blocks show as unknown blocks
  until the pack is added back.

## 1.1.0

Bedrock pack authors:
- A pack made for Bedrock can use BCT with no Java pack. Its data goes in
  `scripts/bct.js`, and the engine reads it when the world loads.
- If the data has a mistake, players see which entry is wrong in chat.
- Patterns: a block can show a fixed pattern (such as 3x3 stone) or random
  tiles that never turn.
- 3D leaves: a pack's own leaf models take the place of vanilla leaves. They
  can use other models far from logs. Leaves still decay and drop as in
  vanilla.
- Edges: grass, sand or any ground can spread onto the tops of the blocks
  next to it.
- Overlays: the 17-tile overlay sets from Java packs, on any side of a block.
- Connected blocks: glass and other blocks join their neighbours, in every
  graphics mode, ray tracing too. Inner corners are not drawn.
- Carriers: the full 47-tile connected look, inner corners too, in Classic and
  Vibrant Visuals.
- `bct.py`, run from a copy of this repository, writes the blocks a pack
  needs, with the vanilla block's mining, drops and sounds filled in.
- `python bct.py check` lists what is wrong with a pack before you load a
  world.
- A guide for pack authors: `docs/AUTHORING.md`.

Engine:
- Connected-texture rules for some biomes only were never drawn. They draw.
- Converted leaves next to another tree's trunk decay as vanilla leaves do.
  Only a log their own leaves reach keeps them alive.
- Leaves whose pack picks models by distance from logs convert.
- An entity removed as it loads no longer causes an error.

Converter:
- Converted leaves drop what vanilla leaves drop. Before, breaking them gave
  the leaf block, a sapling, sticks and an apple every time.
- Converted mob models no longer flood the content log every frame, which
  could drop the game to 1 fps. Their animations play again.
- Zombies, husks, creepers, iron golems, phantoms and zombie pigmen keep
  their vanilla animations.
- Mob parts placed at negative positions show in the right place.
- No block texture is larger than the pack's own size.
- Textures that are exactly the same are kept once, so a pack uses less
  memory (about a fifth less for a large 64x pack).

Site:
- A website with guides for players, pack makers and pack authors, with
  search across all the docs.

## 1.0.0

First release.

Engine:
- Swaps blocks for look-alike blocks that draw a pack's `repeat`, `random`
  and `fixed` rules in Classic, Vibrant Visuals and ray tracing. They carry
  vanilla gameplay: tools, drops (Fortune, Silk Touch), explosions,
  flammability, redstone, light and map colours.
- Leaves drawn with the pack's own models, biome tinted, with vanilla decay.
- Overlay rules drawn as thin see-through blocks in every graphics mode,
  with Continuity's tile choice; several overlays meeting on one block draw
  together. Connected methods draw in Classic and Vibrant Visuals.
- Blocks, leaves and overlays change as soon as they load, nearest first,
  written in bulk (one native call per distinct look), and stay converted.
  A player's own placing and breaking takes effect in the same tick.
- Converts the world beyond the simulation distance in the background, one
  ticking area at a time, with the progress on the action bar.
- Waiting until no player looks (`replace.farDistance`) and swapping back
  when players leave (`replace.swapBack`) are optional settings.
- Converted blocks stay in the world; `/scriptevent bct:control status`
  reports what the engine is doing.

Converter:
- One `.mcaddon` per Java pack: textures, LabPBR materials, animations,
  OptiFine / Continuity rules, block models, 3D held items with inventory
  icons, item tints, fixed colormaps, mob textures, EMF models and animations.
- One pack for Classic, Vibrant Visuals and ray tracing, with no setting to
  pick a mode, RGB MER materials and the game's own water.
- Keeps the author's name (file name with version and resolution),
  description, credits and version; picks the Java release (26.2, 26.3) the
  pack was made for.
- Repeat mosaics on blocks that stay vanilla (sand, red sand, grass top)
  draw as the game's own random tiles when their tiles join smoothly, with
  the grass block's flat random decal painted in.
- Red and soul sand edges cut from the pack's own sand overlay, in the order
  grass, sand, red sand, suspicious sand, soul sand.
- `--vv-scene` takes the author's Bedrock lighting; `--bedrock-grade` also
  their Bedrock colour grade.
- Checks every block, culling file and model it writes against Mojang's
  schemas, keeps block textures within one resolution so the atlas fits, and
  reports texture memory and block permutations.
- Drag-and-drop launcher that sets up Python, downloads Mojang's Bedrock
  samples once, and accepts the author's Bedrock `.mcpack`.
