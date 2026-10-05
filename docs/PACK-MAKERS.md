# Converting your Java pack

The converter turns a Java texture pack into one Bedrock add-on (`.mcaddon`)
that works with the Bedrock Connected Textures engine. It runs on your own
computer. No account, upload or AI service is involved.

## What you need

- Windows 10 or 11.
- [Python](https://www.python.org) 3.10 or later and [Node.js](https://nodejs.org) 18 or later.
- An internet connection the first time. The converter downloads Mojang's
  Bedrock samples (several hundred MB, once) and, for each Java version, the
  Java game files your pack builds on (from Mojang, checked against Mojang's
  checksums).
- Free disk space: a few GB for a 256x pack.

## Convert

1. Unzip the converter.
2. Drag your Java pack ZIP onto `Convert-Java-Pack.cmd`.
   - Several ZIPs: drop them together, base pack first. Later ones win.
   - Your own Bedrock `.mcpack` can go in too. Its Vibrant Visuals lighting
     and fog are used; none of its art is. With `--bedrock-grade` the block
     textures also get the colour grade of your Bedrock pack (learned from the
     textures both packs share, such as softer contrast for Bedrock's lighting);
     without it the Java colours are kept exactly.
3. Wait. A 256x pack takes a few minutes.
4. Your pack is in `converted/<name>/<name>.mcaddon`.
   `conversion.json` next to it lists what converted, what did not and why.

## What converts

| Your Java pack | On Bedrock |
| --- | --- |
| Block textures and LabPBR `_n` / `_s` maps | Texture sets with normal and MER maps; one pack for Classic, Vibrant Visuals and ray tracing, with the game's own water. Height, ambient occlusion and porosity are kept in `conversion_channels`; Bedrock has no use for them. |
| `.mcmeta` animations | Bedrock flipbooks. |
| OptiFine / Continuity rules (`optifine/ctm`) | `repeat`, `random` and `fixed` on full blocks draw with look-alike blocks in every graphics mode. Connected methods (`ctm`, `horizontal`, `vertical`, `top`) draw in Classic and Vibrant Visuals. |
| Overlay rules (`overlay`, `overlay_random`, `overlay_repeat`, ...: grass or sand spreading over other blocks, fallen leaves) | Drawn as thin see-through blocks on the faces next to the matching blocks, in every graphics mode, following Continuity's tile choice. |
| Block models (leaves, plants, decorations) | Custom blocks with your geometry, sized to fit Bedrock's limits. |
| 3D item models | Held in hand with your display poses; inventory icons drawn from the model. |
| Item textures and item tints | Bedrock item textures with the tint baked in. |
| Fixed colormaps (`optifine/colormap`) | Colours baked into the textures. |
| Mob textures and EMF models (`.jem`) with animations | Bedrock entity textures, geometry and animations, with PBR in Vibrant Visuals. |
| `pack.png`, `pack.mcmeta` | The add-on's icon, description, credits and Java version. |

## Your name, credits and version

A converted pack is still your pack:

- **Name**: the ZIP's file name (with its version and resolution, such as
  `My Pack R4.1.0 256x`), or `--title`.
- **Description**: your `pack.mcmeta` description, unchanged.
- **Credits**: "By ..." lines in your description become the pack's authors,
  or pass `--author`.
- **Version**: a version tag in the file name (`R4.1.0`, `v4.1`), or
  `--version 4.1.0`; otherwise 1.0.0. Bedrock only replaces an installed pack
  when the version goes up, so to test the same version again, delete the old
  one (Settings > Storage) first.
- **Java version**: read from your `pack.mcmeta`. A pack made for 26.2 is
  filled in from 26.2's game files, one made for 26.3 from 26.3's.

## What does not convert yet

- Grass, dirt, sand, gravel and other soils keep vanilla blocks, because
  plants, animals and spreading depend on the real block. Their `repeat`
  mosaics draw as the game's own random tiles when the tiles join smoothly
  in any order (measured per pack; sand usually does), and a flat random
  decal on the grass top (such as a clover layer) is painted into those
  tiles. A mosaic whose tiles only fit in your order shows its first tile.
- Sands your pack has no overlay for (red sand, soul sand) get edges cut in
  the shape of your own sand overlay, filled with their own texture, under
  your sand and grass.
- Connected methods (`ctm`, `horizontal`, ...) do not draw in ray tracing;
  those blocks show your plain texture there.
- An overlay needs an empty cell in front of the face: faces behind water,
  plants, snow layers or glass get none. A cell draws at most one block's
  worth of overlays; rare busy corners lose the extra ones.
- Rules on non-cube blocks' own model textures (candles, anvils, beacons)
  and rules that work on another rule's output tiles are listed, not drawn.
- Stairs, slabs, walls and fences do not get patterns.
- Random mob textures (by name, biome or UUID) are listed, not converted.
- Shader effects: Bedrock draws with its own lighting.

`conversion.json` gives the exact list for your pack.

## Size

Very large packs can fail to load on Bedrock (12,000+ textures at 256x
crashed the game in testing). `conversion.json` → `texture_load` shows the
estimated texture memory per graphics mode and warns when a pack is in that
range. Convert a lower-resolution edition if it warns.

## Test it

1. Install the Bedrock Connected Textures add-on and your `.mcaddon`.
2. Make a new world (or a copy) and turn on the engine and both parts of
   your pack.
3. `/scriptevent bct:control status` shows what the engine is doing.
4. The same pack works in Classic, Vibrant Visuals and ray tracing; there is no setting to pick.

## Sharing

Share converted packs only if they are yours, or with their author's
permission. The files the converter writes into your pack may use any
licence you like (see `LICENSE-EXCEPTION.md`).
