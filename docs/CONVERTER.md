# Converter guide

The converter turns a Java block texture pack into one Bedrock add-on for the Bedrock Connected Textures engine. It runs on your machine, needs no account or API key, and never changes the source archives.

## Who may share a converted pack

A converted pack contains the original author's art. Convert packs you are allowed to use;
share or sell a conversion only if you are the pack's author or have the author's
permission. The converter's own output (scripts, data, definitions) carries the Converter
Output Exception in `LICENSE-EXCEPTION.md`, so authors can distribute their converted pack
on any terms.

## What it needs

- [Python](https://www.python.org/downloads/) 3.10 or later, with Pillow and NumPy (`python -m pip install -r requirements.txt`).
- [Node.js](https://nodejs.org/) 18 or later. Node runs the tile selector; no npm packages are needed.
- Mojang's [bedrock-samples](https://github.com/Mojang/bedrock-samples). Download and extract them, then do one of:
  - put the folder next to the converter as `bedrock-samples`,
  - set `BEDROCK_SAMPLES` to its path,
  - pass `--samples <path>` on each command.
- A Mojang Java client reference. The converter downloads it on first use and checks it against Mojang's published hash. For offline use, pass `--vanilla "path/to/client-26.2.jar"`.

## Install

1. Install the **Bedrock Connected Textures** engine add-on once.
2. Convert a pack and import its `.mcaddon`.
3. Turn the converted pack on in the world. Its behavior pack depends on the engine, so Bedrock turns the engine on with it.

Several converted packs can be on at the same time; the engine draws them all under one set of limits.

## Convert a Java pack

On Windows, extract the converter ZIP and drag the Java pack ZIP onto `Convert-Java-Pack.cmd`. For a stack, run `Convert-Java-Pack.cmd "Basic.zip" "Addon.zip"` with the base first and overrides last. The launcher makes a private Python environment on first use. Output goes to the `converted` folder.

From a terminal:

```powershell
python converter/convert_java_author_pack.py --java "My Java Pack.zip" --output "converted/my-pack" --key my-pack --title "My Pack" --samples "C:\Tools\bedrock-samples"
```

List archives after `--java` from lowest to highest priority. `--key` seeds the pack UUIDs and namespaces the pack's entities, so keep it the same between updates and different between packs.

| Output | Use |
| --- | --- |
| `<key>.mcaddon` | The converted pack: artwork, PBR maps, rules and terrain data, with Classic, Vibrant Visuals and RTX modes. It contains no engine code. |
| `conversion.json` | Coverage, limits and the carrier budget report. |
| `carrier-budget.json` | Which rules use entity carriers and which stay native. |
| `replacement-report.json` | Replacement and leaf model blocks: what each rule became, blocks kept vanilla and why, the neighbors that keep a replaced block vanilla, the vanilla tags each replacement carries, model fit scales, everything not built with a reason. |
| `overlay/overlay-report.json` | Overlay surfaces: each overlay rule with the faces, top heights and blocks it draws on, the surface blocks and their permutations, what a cell cannot show, and the rules not drawn with a reason. `conversion.json` → `overlay_surfaces` repeats it with the transitions. |
| `block-ids.json` | Java and legacy block ids with no Bedrock block, and rules left without a block. |
| `schema-check.json` | Every JSON file of the add-on checked against Mojang's schemas: counts, kinds without a schema, problems. |

`convert_java_author_pack.convert(archives, destination, ...)` runs the same conversion from Python.

## Carrier budget

Entity carriers cost frame time and memory, so the converter keeps them for building blocks and leaves terrain native. `converter/data/carrier-budget.json` sets:

- `entity_allowlist`: blocks that may use carriers, such as glass and panes, planks, bookshelves, bricks, sandstone, cobblestone, quartz and polished stone.
- `native_only`: blocks that never use carriers: stone, deepslate, dirt, grass, sand, gravel, logs, leaves, wool, carpet, terracotta, concrete, ores and similar.
- `carrier_types`: the number of carrier rules a pack may have before the converter warns.

Rules for other blocks are dropped from the runtime data. Unconditional `random` rules on those blocks become native terrain-atlas variations, and other rules keep the native preview the converter exports. Pass `--carrier-budget <file>` to use a different budget.

## What gets converted

- Block textures, connected-texture rules, material maps, colormaps and texture animations.
- Java block model parts, with their texture layers, UVs, pivots and weighted choices.
- Items, paintings and particles where the Bedrock layout is known.
- 3D item models the author made for items the game draws as flat sprites (see [Held items](#held-items)).

Custom entity models, item models Bedrock can't show, unsupported state checks and unmapped files are listed in `conversion.json` and the receipts.

### Graphics modes

One pack serves Classic, Vibrant Visuals and ray tracing, like an author's own Bedrock pack: it declares `pbr` and `raytraced` and has no setting to pick a mode. Every block material uses RGB MER maps, which ray tracing needs and Vibrant Visuals reads too.

Entity carriers draw in Classic and VV. Ray tracing cannot draw them, so the blocks they draw over keep their own faces in the pack (connected glass shows the author's plain glass in ray tracing), and the engine removes carriers while every player uses ray tracing.

### Materials

Java LabPBR 1.3 `_n` and `_s` maps are decoded with DirectX normals and perceptual roughness into RGB MER maps (subsurface is not kept). Missing maps stay missing: no relief or roughness is guessed from color.

`conversion_channels` keeps every LabPBR channel with notes on what each renderer loses. Height is kept but Bedrock has no Java-style parallax. A texture set holds either a normal map or a height map, not both.

Java model and rule declarations decide what alpha means. Grass sides store the vegetation overlay alpha as Bedrock's tint mask, so soil RGB stays untinted.

Java tints are baked into extra albedo variants. Fixed tint palettes keep their RGB; climate palettes use the Java colormap and biome climate. Swamp noise, biome edge blending and height-based climate still differ.

### VV scene settings

`--vv-scene "My Bedrock Pack.mcpack"` takes lighting, color grading, atmosphere, fog, water, PBR fallback and shadow settings from an existing Bedrock pack; none of its artwork. Without it, the converted pack writes no scene settings and the game's own apply.

`--bedrock-grade` (with `--vv-scene`) also gives the block textures that pack's colour grade: one least-squares fit over the block textures both packs share, applied to every block colour texture (rule tiles, variations and edges included; maps untouched). Without it the Java colours are kept exactly.

### Water

The converted pack ships no water textures: the game's own water draws in every mode. Java water textures are made for Java's renderer (many packs ship them opaque and leave water to shaders), and in ray tracing an author's water looks milky.

### Held items

Java draws an item with its item model. When the author's model is 3D where the game draws a flat sprite (a lantern, torch or rail, for example), the converted pack:

- holds the author's model with an attachable for the Bedrock item. The model's `thirdperson_righthand` and `firstperson_righthand` display transforms pose it on fixed hand frames.
- draws the inventory icon from the same model with its `gui` transform and Java's inventory lighting. The icon replaces the item's sprite, or becomes the block's carried texture when the sprite is the block's own texture.

Items the author didn't change keep the game's sprite, as in Java. Item definitions that switch models (`select`, `condition`, `range_dispatch`) and tinted model faces are listed under `heldItemsSkipped` in `bindings.json`.

### Load report

`conversion.json` → `texture_load` gives, per graphics mode, the number of images and their estimated GPU memory with mipmaps (the mode's base pack plus the replacement blocks and overlay surfaces), and the number of custom block permutations, counted as the game counts them: every combination of each custom block's states (`overlay_surfaces` lists the share of the overlay surfaces). The converter warns above 2 GiB of textures (packs that large have failed to load) or 65,536 permutations (the number the game warns about). Convert a lower-resolution edition instead. A pack can only have 65,536 permutations before the game slows down. When slabs, stairs, fences and walls would take a pack past that, the converter leaves some of them out. Dyed ones go first (concrete, wool and terracotta), then the ones that cost the most (stairs with large patterns first). `replacement-report.json` lists them under `unsupported`. They stay vanilla blocks with the author's base texture. `--max-permutations` changes the number.

### Replacement blocks

Rules on full cubes, slabs, stairs, fences and walls that carriers don't draw become native custom blocks (see `docs/ENGINE.md`), so they also draw in ray tracing. A slab, stair, fence or wall gets the rule's tiles of its full block's place on its own shape; a double slab counts as a full cube when the Java slab draws its `type=double` as one. Each one copies the vanilla block's gameplay from `converter/data/native-replacement.json`: hardness and tool speeds (slower without the tool a block needs, through `item_specific_speeds`), the vanilla drops for the right tool and tier with Fortune on ores, explosion resistance, flammability, redstone conduction, note block instrument, light, map colour and sound. It also carries the vanilla block tags of its block (`vanilla_tags`, checked against the list of vanilla Bedrock block tags): `log` and `wood` on logs and wood, `wood` on planks, `stone` on stone, cobblestone, granite, diorite, andesite and bricks, and so on. `replacement-report.json` lists them per block under `vanilla_tags`.

Blocks with vanilla behavior a custom block cannot carry are never replaced (`keep_vanilla`): grass, mycelium, nylium, dirt and the like spread and grow plants, sand and gravel fall, ice melts, coral dies, redstone ore lights up, soul sand and magma feed bubble columns, bookshelves count for enchanting. `replacement-report.json` lists them under `kept_vanilla` with the reason.

A replaced block goes back to vanilla only next to a block that needs the exact vanilla block (`needs_vanilla`): cocoa on jungle logs, chorus on end stone, dead bushes and dry grass on terracotta, bamboo on its soils, a creaking heart between pale oak logs, a nether portal in its obsidian frame. The engine data gets these as `needs`, limited to the blocks the pack replaces; the report lists every entry with the replaced blocks it keeps vanilla (`keeps_vanilla`). Torches, rails, carpets, slabs, signs, redstone and the other blocks under `attachments` only need a sturdy face and keep nothing vanilla; the report lists them too.

### Overlay surfaces

The author's overlay rules (`overlay`, `overlay_ctm`, `overlay_random`, `overlay_repeat`, `overlay_fixed`) become overlay surface blocks (`converter/overlay_surfaces.py`, see `docs/ENGINE.md`), so they draw natively in Classic, Vibrant Visuals and ray tracing; entity carriers leave them out. For each rule the converter works out, from the base textures and models:

- the hosts: blocks showing a `matchTiles` texture (or named by `matchBlocks`) on a face that is a whole square: every face of a full block, the top of paths and farmland (one pixel lower) and the top of slabs (half a block lower for bottom slabs);
- the sources of an `overlay` rule on each face: full blocks that show a `connectTiles` texture on that same face and are `connectBlocks` when the rule names any. Java reads the neighbor's texture on the face being drawn, so a face no block can give the overlay on is left out and reported (a grass rule with `connectTiles=grass_block_top` reaches top faces only);
- each tile as a terrain texture with the author's normal and MER/MERS maps (RGB MER in the published pack), alpha cut where Java's cutout cuts it (at 0.1), and the tint as the material `tint_method`: `tintBlock=grass_block` with a tint index draws with the biome grass color, leaves with their foliage color, a fixed color is baked in;
- `faces`, `biomes`, `heights`, state matchers, weights with `<skip>`, `randomLoops`, `symmetry`, repeat size and `orient`, which the engine applies per block.

The surface blocks are written within Bedrock's limits (16 values per state, 64 switchable bones, 64 materials) and each under 2,400 permutations, at most 8,000 in all: one block per face direction and top height, top blocks for two rules that meet on one block, and a top block that also shows the strip each side gets from the block above it. `overlay-report.json` lists the blocks, what each holds and what a cell cannot show. Every surface block and geometry passes the same schema checks as replacement blocks. The surfaces travel in the converted pack with the terrain edges: their blocks join its terrain packs and their data the end of its `terrain` packet.

Where the pack's own unconditional overlay rule draws a transition that BCT's generated terrain edges also draw (grass over cobblestone, sand over stone...), the generated edge is left out on those blocks; transitions the author did not draw keep their generated edges. `conversion.json` → `overlay_surfaces` → `transitions` lists both: `authored_overlays` (source, target, the generated rule left out and the author's rule) and `generated_edges` (what still draws generated).

### Leaves with the author's models

When the author's blockstate gives a leaf models that are not a full cube (bushy leaves), the converter writes a model block with those models:

- models are converted like Blockbench and shrunk only as far as Bedrock's 30 pixel geometry limit needs, each on its own (`fit_scale` in the report);
- the model the blockstate picks for each Java distance and persistence is kept (an inner and an outer model, for example), with its weighted random turns;
- tinted faces use the Bedrock foliage tint of that leaf (birch and spruce have their own);
- drops come from the Java vanilla loot table (shears, Fortune), hardness and the rest from the policy's `model_blocks` entry.

Leaves whose blockstate shows full cubes stay vanilla, drawn by the base pack. The report lists every leaf not built and why.

### Checks

Every replacement block, overlay surface block, geometry and culling file is checked against Mojang's JSON schemas in bedrock-samples (`metadata/json_schemas`) and against game rules the schemas don't state (custom components the engine registers, state-only conditions, bone and material limits, one render method per block, no `*` material in geometry); a file that fails stops the conversion with the problems listed. The finished add-on is checked again file by file: blocks, block culling, entities and client biomes against the schema version that matches their `format_version`, block and entity geometry against the structure the game reads. `schema-check.json` lists what was checked, the kinds of file Mojang publishes no schema for (client entities, render controllers, loot tables, texture lists...) and any problems; the converter warns when there are some. Java block ids are mapped to their Bedrock ids; ids with no Bedrock block are left out and listed in `block-ids.json`.

## Rules

The OptiFine `ctm.properties` methods are supported; `docs/ENGINE.md` lists them and the matching order. Biome and block state conditions use checked mappings. Nonzero custom `seed` values are rejected.

### Importing rules only

`converter/import_java_ctm.py --pack <folder> --base-textures <json> --output <folder>` imports the rules from an extracted Java pack. The base texture JSON maps Bedrock block IDs to Java face texture paths. Java state filters and block renames need explicit maps:

- `--biome-map`: Java biome name to Bedrock biome ID or list. A leading `!` keeps exclusion.
- `--state-map`: per-block property translation, for example `{"minecraft:oak_log":{"axis":{"name":"pillar_axis","values":{"x":"x","y":"y","z":"z"}}}}`.

The import writes its output only when every rule passes; an unsupported method or property fails the whole import. For `ctm_compact`, add `--compiled-pack <folder>`: each quadrant is copied without resampling and `ctm.N` overrides are applied. To decode Java maps during import, pass `--material-encoding labpbr-1.3`, `--normal-y directx|opengl` and `--roughness linear|perceptual`.

### Auditing a pack first

```powershell
python converter/audit_java_ctm.py --java "MyPack.zip" --bedrock "MyPack.mcpack" --report "conversion-audit.json"
```

The audit lists known gaps per rule without changing either input.

## Terrain transitions only

`converter/terrain_pack.py` builds grass and sand edges for an existing Bedrock pack:

```powershell
python converter/terrain_pack.py --resource-pack "C:\Packs\MyPack.mcpack" --samples "C:\Tools\bedrock-samples" --key my-pack --title "My Pack edges"
```

The input can be a folder, an `.mcpack` or a ZIP holding one resource pack. The result is `dist/development/terrain-my-pack-<version>.mcaddon`, which also needs the engine. Turn on both its packs and put its resource pack above the source pack.

For unusual layouts, pass `--textures textures.json` with keys `grass_top`, `sand`, `red_sand`, `suspicious_sand_0` and `soul_sand`:

```json
{
  "grass_top": ["textures/custom/turf", "textures/custom/turf_variant"],
  "sand": "textures/custom/dunes"
}
```

## Native block exports

These build separate packs with native custom blocks instead of entity carriers. The blocks have to be placed or swapped in by a separate world conversion.

- `converter/native_connected.py`: 47-tile, horizontal, vertical and fixed rules, each connection case picked by neighbor tags.
- `converter/native_random.py`: random block materials through Bedrock terrain variations.
- `converter/native_repeat.py`: repeat patterns on full-cube blocks; the engine's `bct:update_repeat` component keeps each block's place in the pattern.

`native_connected.py` and `native_random.py` take `--renderer classic|vv|rtx`; `native_repeat.build_native_repeat` is called from Python.

## Texture animations

`converter/java_texture_animation.py` reads frame size, sprite grids, frame order, per-frame time and interpolation. Native blocks use `textures/flipbook_textures.json`; carriers use the `USE_UV_ANIM` material with `q.time_stamp` as the clock. Blending between ticks does not match Java exactly.

## Demo

`python converter/demo_pack.py --samples <path>` builds labeled test tiles: sandstone with the 47-tile method, bricks as a 3 by 2 repeat, oak planks horizontal, cobblestone vertical and stone bricks random. Use a separate test world.
