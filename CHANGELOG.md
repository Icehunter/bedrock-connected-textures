# Changelog

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
