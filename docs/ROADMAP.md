# Roadmap

What comes after 1.2.1. Nothing here is promised for a date; items move into
`CHANGELOG.md` when they ship.

## Shaped blocks after 1.2

1.2.0 swaps slabs, stairs, fences and walls, with patterns and random tiles,
the vanilla shapes and gameplay, and Java's stair corners and fence and wall
joins. What Bedrock allows, and what is left:

### What Bedrock allows (tested in game on 1.26.50)

Test packs built a custom stair, fence and slab and read their states back.

- A block has 16 bits of state in all. A state costs enough bits for its
  values: 2 to 4 values take 2 bits, 5 to 8 take 3, 9 to 16 take 4. Four
  states of 16 values load. Five states of 16 values and 17 yes/no states load
  too, but the extra states are lost: setting the fifth state resets the
  first four to 0. 2,400 permutations load without complaint, so bits are the
  limit, not permutations.
- A stair needs about 6 bits (direction 2, upside down 1, corner 3), leaving
  about 10 for patterns and variants. A wall needs about 9 (four connections
  at 2 bits, plus the post), leaving about 7.
- Shapes can follow state: bones switch on `bone_visibility`, and
  `minecraft:collision_box` takes an array of boxes (up to 16, up to 24 tall)
  that a permutation can change per state. The selection box is still one box,
  at most 16 tall, so a stair or wall is selected as a full cube.
- `minecraft:support` with `stair` or `fence` works, and buttons and torches
  place on a custom stair. They do not only go on flat faces.
- The `minecraft:corner_and_cardinal_direction` trait works between custom
  stairs: the game sets `minecraft:corner` itself (an L of two stairs came out
  `inner_right`).
- Corners with vanilla stairs: a vanilla stair beside a custom stair stays
  `none`, and a custom trait stair placed before a vanilla one stayed `none`
  too. Not tested with the vanilla stair placed first, or by hand.
- A custom stair with its own corner state (`shapesprobe:corner`) never
  changes it. The engine has to work corners out from the neighbours, as it
  does for panes.
- Fences: custom fences join each other and stone. They do not join vanilla
  fences or panes. Vanilla fences and panes do join custom fences. A custom
  fence beside a vanilla wall is untested.
- Worlds over 65,536 block permutations get a performance warning.

Still to test: a vanilla wall beside a custom fence, and a vanilla stair placed
before a custom trait stair.

### Left for later

- Connected textures (ctm and the other neighbour methods) on shaped blocks.
  They need neighbour states on top of the shape states, so they cost far more
  permutations than patterns. They need their own plan.
- Overlay layers and decorations on shaped blocks: vanilla slabs, stairs,
  fences and walls have no layered models, so only odd author models use
  them. They are reported, not drawn.
- Fence gates and buttons stay vanilla: they open, close, make sounds and send
  redstone. The engine would have to copy all of that for a small texture
  change.
- Packs with large patterns on many blocks keep only some shaped blocks within
  the 65,536 permutations; dyed ones (concrete, wool, terracotta) are left out
  first, then the costliest.

## Trunks

Rounder, more natural tree trunks, as 3D leaves did for leaves.

- First as textures only: bark that reads as round, in the pack's own art.
- Then as shaped logs, on the shaped-block geometry 1.2 added.

## Known gaps in 1.2.1

- Suspicious sand edges are not drawn in converted packs.
- Poplar leaves stay vanilla when a pack has no poplar leaf art.
- Connected blocks draw no inner corners: a block's states cannot hold its
  diagonal neighbours. Carriers draw them, except in ray tracing.
- Some Java packs give a block a base texture that only says to enable
  connected textures, because a rule always covers it in Java. The converter
  keeps the base texture when the rule gives the block's faces different
  tiles, as a mosaic does. The text then shows on item icons and on blocks
  the engine has not swapped yet, such as stone far away.
