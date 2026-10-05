# Engine

The engine draws connected textures and terrain transitions (grass and sand edges reaching onto nearby blocks) over Bedrock blocks. Entity carriers and terrain surfaces never replace the blocks under them; native replacement blocks and leaf model blocks stand in for vanilla blocks near players and go back to vanilla when players leave (see Replacement blocks and Leaves). It reads no `.properties` files at runtime: the converter turns them into rule data, and each converted pack's behavior pack sends that data to the engine.

Needs Bedrock 1.26.50 and stable `@minecraft/server` 2.10, with no beta APIs.

## Identity

The engine is one behavior pack, `BCT_BP`, built by `python converter/engine_package.py`. Its header and module UUIDs are fixed in `engine/identity.json` and never change between builds. Converted packs list the header UUID and minimum version as a manifest dependency, so Bedrock links them to the engine.

## Parts

| File | Role |
| --- | --- |
| `main.mjs` | Entry point. Passes the `@minecraft/server` objects to `engine.mjs`. |
| `engine.mjs` | One interval, one graphics-mode check, one source listener, block events and commands. |
| `settings.mjs` | Limits, the shared carrier allowance and change-only property writes. |
| `scanner.mjs` | The chunk scanner both parts share. |
| `sources.mjs`, `publisher.mjs` | Script-event transport. `publisher.mjs` runs in converted packs. |
| `connected.mjs` | Connected-texture carriers, one instance per converted pack. |
| `tiles.mjs`, `tile-template.mjs`, `overlay.mjs`, `core.mjs` | Tile selection for the OptiFine methods; `overlay.mjs` picks the overlay template tiles. |
| `terrain.mjs`, `terrain-rules.mjs`, `terrain-native.mjs`, `heights.mjs`, `tint.mjs`, `trace.mjs` | Terrain transitions; `terrain-native.mjs` also holds the overlay surfaces (`createOverlaySurfaces`). |
| `repeat-blocks.mjs` | The `bct:update_repeat` block component for native repeat blocks. |
| `replacement.mjs` | Native replacement blocks: swapping (in bulk where only the position matters), ownership, optional swap-back, stripping and ore experience. |
| `views.mjs` | Where players are and look: chunks are worked nearest first; with `replace.farDistance` set, a block or leaf only changes while no player can see it. |
| `budget.mjs` | The per-tick time budget the parts share; the part that goes first changes every tick. |
| `bulk.mjs` | Bulk block writes: one `fillBlocks` call over a `ListBlockVolume` per distinct permutation. |
| `leaves.mjs` | Leaf model blocks: bulk conversion of loaded chunks, decay and drops. |

## Sources

A converted pack's `scripts/source-data.js` holds one packet per part (`connected`, `terrain` and `replace`), named by the pack key, with a checksum. When the pack has overlay rules, its `terrain` packet ends with one `{"overlay": ...}` entry: the data of its overlay surfaces. The publisher sends packets in chunks of 750 characters until the engine acknowledges them, and again whenever the engine restarts. Several converted packs can be active together: each keeps its own entity types and data, and when two packs draw the same block the one with the higher `sourcePriority` (then key order) owns it.

## Selection

Supported methods: `ctm` (47 tiles), `ctm_compact` (expanded to 47 tiles by the converter), `horizontal`, `vertical`, `horizontal+vertical`, `vertical+horizontal`, `top`, `fixed`, weighted `random`, `repeat`, and the overlays `overlay`, `overlay_ctm`, `overlay_random`, `overlay_repeat` and `overlay_fixed`. Rules match by block (`matchBlocks`, with states), by texture (`matchTiles`), by face, height and biome. `connect` takes `block`, `state` or `tile`. `symmetry`, `randomLoops`, `linked` and `innerSeams` follow the format.

Texture rules come before block rules; a higher `weight` wins, then file order. A rule's output texture is matched again against texture rules, up to three passes, and each rule applies once. `<skip>` moves on to the next rule and `<default>` keeps the native face.

Tile numbers follow the OptiFine templates; a fully surrounded `ctm` face uses tile 26. Repeat offsets and random placement match Java, including negative coordinates. When a neighbor, a block state or a face orientation is unknown, the face waits instead of guessing.

The `overlay` method follows Continuity's overlay processor (`overlay.mjs`). For each face, the four neighbors in the plane of the face are read in texture space (left, down, right, up as seen from outside; on a top face north is up and west is left). A neighbor gives the overlay when the block in front of it is not a solid render block (a full opaque cube), it is a full block, it is one of `connectBlocks`, it shows one of `connectTiles` on that same face, and it does not connect with the block (same block, or same texture for tile rules). The overlay tile lies on that neighbor's side: a neighbor on the left draws tile 9 along the left edge, below tile 1, on the right tile 7, above tile 15; two, three or four sides draw tiles 3 to 6 and 10 to 13 and 8. A corner tile (2, 0, 14, 16) needs both of its edges free, its diagonal neighbor to give the overlay (with no solid block in front of it) and a neighbor beside it that carries the same rule (matched by `matchBlocks` and `matchTiles`; OptiFine compares the texture with the face's own). With two adjacent edges OptiFine draws the opposite corner without that last check; Continuity keeps it (`dialect`). Overlay tiles are drawn unturned; `overlay_ctm` and `overlay_repeat` follow the face orientation only for the tile choice and only when the rule sets `orient`. A grass rule with `connectTiles=grass_block_top` therefore only ever reaches top faces: a grass block shows that texture only on top.

## Carriers

Connected textures draw on passive entity carriers, one per visible face and layer, placed 0.502 blocks out from the block center. Bedrock lights an entity from the cell it stands in, so a carrier sits in front of its face; one carrier for a whole block would light its other faces from the wrong cell. Hidden faces get no carrier. Terrain transitions use native surface blocks in the empty cell above a host where possible and entity carriers otherwise. The pack's own overlay rules draw as overlay surfaces (below), never on carriers. Entity carriers cannot draw in ray tracing, so when every player uses ray tracing the engine removes them, and the blocks under them show their own faces.

## Overlay surfaces

The pack author's overlay rules (`overlay`, `overlay_ctm`, `overlay_random`, `overlay_repeat`, `overlay_fixed`) draw as native custom blocks, so they show in Classic, Vibrant Visuals and ray tracing, with the author's normal and MER/MERS maps and the Java tint as the material `tint_method` (grass and foliage take the biome color in every mode). A surface is a block in the empty cell in front of a host face: above the host for its top face, beside it for a side face, below it for its bottom face. Its geometry has one quad per rule and tile, a fraction of a pixel outside that face (later rules above earlier ones); block states pick which quads show. Bedrock lights a block from its own cell, which is the cell that lights the face behind it, so the overlay takes the face's light. Hosts are faces that are whole squares, as in Java: every face of a full block, the top of a path or farmland one pixel lower and the top of a bottom slab half a block lower; stairs, buttons and the like get none.

One cell can draw onto several hosts at once, but a block allows 16 values per state, 64 switchable bones and 64 materials, and the converter keeps each surface block under 2,400 permutations. So the converter writes a few surface types and the engine gives each cell the one that draws the most, the top face first, then the sides, then the bottom face:

- one type per face direction and top height, showing one rule there;
- top types showing two rules that meet on one block (grass and sand around a path block, for example);
- a top type that also shows, on each side, the strip a side face gets from the block above it (tile 15: grass or sand over the edge of a cliff).

A cell that needs more (a top face with three rules, a side overlay other than the top strip next to a top face, two side faces with full overlays) draws the most important part and counts the rest in `status.overlays.dropped`.

Surfaces have no collision and no selection box (clicks go to the block behind), are replaceable when a player places a block into the cell, break in flowing water and when a piston moves them, drop nothing, let all light through, do not stop rain, and count as open for replacement blocks, so a host next to one is swapped like a host next to air. They go only into air: a cell with a plant, snow, water or any other block gets no surface. A cell holding a generated terrain edge block is taken over (the pack's own overlay wins). A top surface also carries a placement filter naming every host it covers and the replacements standing for them, so it goes with the block under it even when no script sees that block go.

Hosts and neighbors read through the vanilla view: a replacement block counts as the vanilla block it stands for, as a host and as a neighbor. An `overlay_random` or `overlay_fixed` rule that a replacement block draws itself is left out on that block while it is swapped, and drawn by a surface while it is vanilla.

The overlay surfaces have their own scanner, which runs in every graphics mode. It looks within `overlay.chunkRadius` chunks and `overlay.yBand` blocks of each player (plus a top-surface pass) for blocks that give an overlay (`connectBlocks`, or blocks showing `connectTiles`) and blocks that show one by themselves (the hosts of the other methods), and queues the cells in front of the faces they reach. A placed, broken or exploded block queues the 27 cells around it; a replacement swap the six around the swapped block; the chunks are scanned again every `overlay.refreshTicks`, and four known surfaces are checked again each tick (fire, water and falling sand change blocks without events). Queued cells are drawn within `overlay.sliceMs` of each tick, nearest chunks first; a cell whose neighbors are not loaded keeps what it has. Chunks holding surfaces are indexed in world dynamic properties, so `bct:control off` removes them from every chunk as it loads near a player.

## Replacement blocks

A converted pack draws most full-cube rules with native custom blocks: repeat rules keep their place in the pattern as coordinate states, random and `overlay_random` rules as a variant state, and fixed rules as one tile. These blocks carry the author's normal and MER/MERS maps, so they draw in Classic, Vibrant Visuals and ray tracing. Each replacement carries every vanilla state of its block (for example `pillar_axis` as `bct:pillar_axis`, `minecraft:connection_east` as `bct:connection_east`, booleans as 0 or 1), so swapping back restores the exact vanilla permutation. Connected methods (`ctm`, `horizontal`, `vertical`, `horizontal+vertical`, `vertical+horizontal`, `top`) pick their tile from the neighbors; block geometry can only read the block's own states and show at most 64 bones, so those rules stay on entity carriers.

The look of a replacement follows the Java model:

- Biome tints use the material `tint_method` (`grass`, `default_foliage`), so tinted faces and decorations take the biome color in every graphics mode. A tinted Java layer over an untinted face is drawn as a separate plane with its own tint, as in Java; rules whose `matchTiles` name the layer texture pick its tile.
- Weighted Java models (random turns) become a `bct:m` state, and weighted multipart groups (a pack's decorations on a block) a `bct:g<n>` state; the engine picks both per position with Java's model random. Decoration parts are extra bones. A random rule on a decoration texture picks its tile from the block's x and z pattern cell, so it adds no state. Texture turns between looks are bones with `uv_rotation`.
- Glazed terracotta carries `facing_direction`; each face turns its texture per facing the way the Java model does.
- Panes and iron bars get a post and four arms. The arms follow the mirrored connection states: the engine copies the vanilla states on swap-in and works them out again from the neighbors (other panes and bars, walls, glass and full blocks) when a neighbor changes or the chunk is rescanned. Collision and selection are one box around the post and the connected arms (Bedrock allows one box per block): an L, T or cross-shaped pane collides as the box around it, so the inner corner of an L blocks movement where vanilla does not.
- A block with any cut-out material (layers, decorations) draws every material `alpha_test_single_sided`, because a block uses one render method.

The engine swaps a block in while it is within `replace.chunkRadius` chunks and `replace.yBand` blocks of some player (the union over all players, in every graphics mode) and it shows: a neighbor is not an opaque full cube (air, water, lava, glass, leaves, a torch, a slab, a plant) or it backs a block that shows, so digging never uncovers a vanilla face. At most `replace.maxSwapsPerTick` block changes and `replace.sliceMs` milliseconds per tick go to swapping; a log counts as 8 because of its leaf scan. Right after a swap, connected-texture carriers on that block are removed: replacements never get carriers, and rules and terrain edges read a replacement as the vanilla block it stands for.

A block stays vanilla only while a neighbor needs that exact vanilla block to stay or to be placed (`needs_vanilla` in `converter/data/native-replacement.json`, sent to the engine as `needs` with the side the needed block is on): cocoa on jungle logs and wood, chorus plants and flowers on end stone, dead bushes and dry grass on terracotta, bamboo on its soils, a creaking heart between pale oak logs, a nether portal in its obsidian frame. A replacement that comes to have such a neighbor (put there by a structure or a command) goes back to vanilla. Torches, lanterns, levers, buttons, rails, carpets, pressure plates, slabs, stairs, snow layers, ladders, vines, signs, banners, bells, item frames, paintings and redstone parts only need a solid or sturdy face, which a full-cube replacement has, so they keep nothing vanilla (`attachments` in the policy lists them); neither do mushrooms, which grow on any solid block in low light.

Blocks change as soon as their chunk loads, nearest chunk first, and a block a player places or uncovers changes in the same tick. Blocks whose replacement depends only on their position (repeat, random and model choices) are written in bulk while the chunk is scanned: one `fillBlocks` call over a `ListBlockVolume` per distinct permutation, so a chunk takes a few dozen native calls instead of one per block. Logs (which look after the leaves around them) and blocks with mirrored states (axis) are swapped one by one. Optionally (`replace.farDistance` above 0) a change waits while some player could see it: the block shows, lies within `replace.farDistance` blocks of the player's head and within half of `replace.viewAngle` of the view direction, and happens once no player can; for `replace.joinTicks` after a player arrives, `replace.nearDistance` counts instead. Leaves follow the same rule (see Leaves).

Nothing turns back to vanilla where players look, stand or dig. Players mine, use and blow up the replacement itself, and it carries the vanilla gameplay (`converter/block_gameplay.py`):

- Destroy time and tool speeds. A block that needs a tool (stone, ores, bricks, snow) takes the vanilla time without it, and only the right tool and tier (golden tools count as wooden ones) get the normal time, through `item_specific_speeds`.
- Drops from the vanilla loot: only for the right tool and tier, with explosion decay, and Fortune on ores as Java's ore bonus. Bedrock drops a custom block's own item for Silk Touch; the engine turns it into the vanilla block's item.
- Experience from ores (`xp`), given by the engine for the right tool, never for Silk Touch or in creative.
- Explosion resistance, flammability (with lava ignition for wood and wool), redstone conduction (opaque blocks conduct, see-through ones do not), the note block instrument, light, map color, sounds, and push-only glazed terracotta.
- The vanilla block tags of the block it stands for (`vanilla_tags`, from the Bedrock vanilla tag lists; only tags that exist there): `log` and `wood` on every log and wood, `wood` on every plank, the species tag (`oak`, `spruce`, `birch`, `jungle`, `acacia`, `dark_oak`) on the six classic logs, `stone` on stone, cobblestone, mossy cobblestone, granite, diorite, andesite and their polished forms and bricks, `metal` on iron bars. Tool tags come from the gameplay profile; tier tags are left out, because the loot tables decide drops.
- Logs, wood, stems and hyphae (`strip`): an axe strips a replaced log to the stripped log (its replacement when there is one) with the vanilla sound and one point of axe durability. The engine listens to `playerInteractWithBlock`, so a replaced log is not an interactable block and placing blocks against it works as in vanilla.
- Logs (`leafGuard`): leaves only run their decay check while `update_bit` is set, and a block change next to them sets it. Before a log is swapped in or back, the engine reads the leaves within 6 blocks; a log stays vanilla while a non-persistent leaf already has a check pending, and on the next tick `update_bit` is cleared again on every leaf that had it clear before the swap. Persistent leaves are left alone.
- Pick-block gives the replacement block's item.

Blocks whose vanilla behavior a custom block cannot carry are never replaced (`keep_vanilla` in `converter/data/native-replacement.json`, each with its reason; the converter lists them in `replacement-report.json`): grass, mycelium, nylium, dirt, podzol, moss and mud (spreading, plants and saplings, hoes and shovels), sand, gravel and concrete powder (falling, plants, hardening), ice (melting), living coral blocks (dying), redstone ore (lighting up), soul sand, soul soil and magma (bubble columns, Soul Speed, burning), bookshelves (enchanting power) and dripstone blocks (pointed dripstone). Their atlas variations still draw; their other rules are reported as not drawn. Infested blocks (silverfish) and chiseled bookshelves (stored books) stay on entity carriers (`carrier_fallback`).

Outside the swap range the native base pack draws the author's base textures, as Java does without a connected-texture mod. The author's own `random` rules become native atlas variations; repeat rules are never shuffled into random tiles.

Swapped blocks stay swapped, like converted leaves, so nothing changes back and forth while players move about. They are swapped back when a neighbor needs the vanilla block, when the engine is turned off, and, with `replace.swapBack` 1, when no player is within the radius plus one chunk or the band plus 8 blocks. Ownership is saved per chunk in world dynamic properties, so a later session finds every swapped block. Replacements found where the engine did not put them (pistons, structures, clones) are adopted with the pattern states of their new position.

## Leaves

Leaves whose Java blockstate shows the author's own models (not a full cube) become model blocks (`m_<leaf>`), with the models fitted to Bedrock's 30 pixel geometry limit (see CONVERTER.md). `leaves.mjs` swaps every such leaf in loaded chunks within `leaves.chunkRadius` chunks of a player whose eight neighbor chunks are loaded too, nearest first, using at most `leaves.sliceMs` of each tick; chunks at the edge of the loaded area stay vanilla. Converted leaves stay converted. Ownership is saved per chunk. Chunks next to players are looked at again every `leaves.recheckTicks` ticks for leaves that appear without an event (a grown tree, a structure).

A chunk's leaves convert all at once: vanilla leaves are found by their exact states (`includePermutations`), each gets its Java model choice and, when the pack's models depend on it, its distance group, and leaves with the same converted states are written by one `fillBlocks` call. Mangrove leaves, which generate in water, convert one by one to keep their water. With `replace.farDistance` above 0, a leaf a player could see waits instead, per chunk, as replacement blocks do. Vanilla leaves next to converted ones keep `update_bit` as it was, so no decay check is left pending. A converted leaf only decays when no log lies anywhere within 4 blocks. Converted leaves take new looks (distance groups), decay marks and persistence whenever they need them.

A model block mirrors `persistent_bit` and `update_bit` exactly and adds `bct:t` (the weighted Java model turn, picked per position with Java's model random) and `bct:look` (the model the author's blockstate shows for the leaf's Java distance and persistence; the engine works out the Java distance from logs through leaves, up to 7).

Decay follows vanilla Bedrock: removing a log marks the leaves within 4 blocks and removing a leaf the leaves next to it; on a random tick (`bct:leaf` component) a marked leaf decays if no log is within 4 blocks through leaves and is unmarked otherwise. Logs can also go without an event (pistons, other packs), so one random tick in `leaves.checkChance` on an unmarked leaf marks it when no log is within 6 blocks. Persistent leaves never decay. A decaying leaf drops the vanilla leaf's loot (when `doTileDrops` is on) and leaves water when it was waterlogged.

Breaking a leaf gives the Java vanilla drops: shears give the leaf block (the engine removes the other drops), otherwise saplings, sticks and apples by Fortune; Silk Touch gives the vanilla leaf. Hardness, hoe and shears speeds, flammability, light and map color are the vanilla leaf's.

## Limits

Defaults (`settings.mjs`):

| Setting | Default |
| --- | --- |
| `connected.chunkRadius` | 3 chunks around each player |
| `connected.yBand` | 24 blocks above and below the player |
| `connected.maxCarriers` | 1500 |
| `connected.maxCarriersPerChunk` | 96 (about 32 blocks with three visible faces) |
| `terrain.chunkRadius` | 6 chunks |
| `terrain.yBand` | 24 blocks, plus a top-surface pass with `getTopmostBlock` |
| `terrain.maxEntityCarriers` | 1500 |
| `terrain.maxNativeCarriers` | 8000 |
| `scanJobs` | 2 chunk scans at a time |
| `budgetMs` | 6 milliseconds of each tick for all of the engine's work together |
| `scanSliceMs` | 4 milliseconds of each tick for those scans together |
| `replace.chunkRadius` | 4 chunks |
| `replace.yBand` | 32 blocks |
| `replace.maxSwapsPerTick` | 512 block changes per tick |
| `replace.sliceMs` | 4 milliseconds of each tick for swapping, and as much for the replacement scan |
| `replace.refreshTicks` | 600 ticks between full rescans |
| `replace.viewAngle` | 140 degrees: the whole cone around a player's view direction in which a change waits (enough for a wide field of view); the four view settings hold for replacement blocks and leaves |
| `replace.farDistance` | 0: changes happen at once. Above 0, changes within that many blocks of a player's head and in view wait until the player looks away |
| `replace.nearDistance` | 0: with waiting on, the distance that counts instead of `farDistance` right after a player arrives |
| `replace.swapBack` | 0: swapped blocks stay swapped. 1 swaps a chunk back to vanilla once no player is near it |
| `replace.joinTicks` | 600 ticks (30 seconds) after a player joins, changes dimension or teleports |
| `leaves.chunkRadius` | 32 chunks (0 turns leaves off) |
| `leaves.sliceMs` | 6 milliseconds of each tick |
| `leaves.probesPerTick` | 64 chunks looked at per pass |
| `leaves.recheckTicks` | 20 ticks between looks for new leaves next to players |
| `leaves.checkChance` | 8 (one random tick in 8 checks an unmarked leaf) |
| `overlay.chunkRadius` | 4 chunks |
| `overlay.yBand` | 24 blocks, plus a top-surface pass with `getTopmostBlock` |
| `overlay.sliceMs` | 4 milliseconds of each tick for overlay surfaces, and as much for their scan |
| `overlay.refreshTicks` | 1200 ticks between full rescans |
| `intervalTicks` | 4 |

A chunk scan makes one `getBlocks` call with every block type that some part wants in that chunk. Scans run as jobs that together use at most their slice of each tick, however often the game resumes them. Chunks are scanned nearest first and again when a player moves more than half the band up or down. Carriers outside the radius or band are removed and drawn again on return. Entity properties are written only when their value changes. Native surface ownership is saved per chunk.

## Commands

With commands on:

```mcfunction
/scriptevent bct:control status
/scriptevent bct:control off
/scriptevent bct:control on
/scriptevent bct:config {"connected":{"chunkRadius":4,"maxCarriers":2000}}
/scriptevent bct:config {"debug":true}
/scriptevent bct:settings
```

`off` removes the engine's carriers and overlay surfaces, swaps replacement blocks back as players stop seeing them, and is saved in the world; use it before taking the engine out of a world. `bct:config` merges the given values over the defaults, rejects unknown names and out-of-range values, and saves them in the world. `bct:settings` opens the terrain edge height form. Converted blocks and leaves stay in the world; a world played with a converted pack keeps needing it. With `debug` on, the engine logs scan errors and `/scriptevent bct:probe` logs the selection for the face under the crosshair; with it off, the engine writes nothing to the log.

## Known limits

- Slopes, slabs, lowered paths and tree geometry get no generated terrain transitions (the pack's own overlay rules do reach paths, farmland and slab tops).
- Overlay surfaces only go into air, so a face behind water, snow, a plant or glass shows no overlay, where Java draws one. Snow does not settle in a cell that holds a surface, and bone meal places no grass or flowers there.
- One cell draws at most what one surface type holds (see Overlay surfaces); the rest is counted in `status.overlays.dropped`. Lava flowing into a surface, map colors and how surfaces look at a distance (alpha-tested quads fade out) need checking in game.
- Only chunks the scripts can read are drawn.
- Load time and memory on large scenes need checking in game.
- While a block is swapped, effects that look for a block type in an area rather than at the block a player uses do not see it: moss and sculk spreading onto stone, enderman pickup, and beacon beams through replaced glass. Random rules on replacement blocks use one draw per block for all faces of a rule.
- Silk Touch on a block that needs a better tier still drops the block (Bedrock drops a custom block's item for Silk Touch whatever the tool). Pick-block gives the replacement's item.
- Pane collision is one box per block (see above). Turned textures (`uv_rotation`), decoration placement, the pillar rotation, model block turns and culling, tool speeds and drops need checking in game.
- A replacement change waits on the view cone only, not on what hides the block from the player; in third-person view the camera sits behind the head, so blocks just behind the player can change in view. The engine reads where players look once per tick.
- Attachments staying on a replacement when it is swapped in or out next to them (torches, lanterns, rails, doors, signs, item frames, redstone), fences, walls and panes keeping their connections to it, and what the vanilla tags change (cocoa on a replaced jungle log, leaves next to replaced logs) need checking in game.
- A tree a player looks at while part of it is out of view (at the edge of the view cone) is partly converted for a while. As at the edge of the converted area, its vanilla leaves may decay when a nearby log is removed and their only path to a log runs through converted leaves.
