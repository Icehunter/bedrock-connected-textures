export const REPO = "https://github.com/Icehunter/bedrock-connected-textures";
export const RELEASES = `${REPO}/releases`;
export const LATEST = `${REPO}/releases/latest`;
export const ISSUES = `${REPO}/issues`;
export const VERSION = "1.2.0";

export const NAV = [
  { to: "/", label: "Home", end: true },
  { to: "/players", label: "Players" },
  { to: "/pack-makers", label: "Pack makers" },
  { to: "/developers", label: "Developers" },
  { to: "/docs", label: "Docs" },
];

export const CONVERTS: [string, string][] = [
  ["Block textures and LabPBR _n / _s maps", "Texture sets with normal and MER maps. One pack for Classic, Vibrant Visuals and ray tracing, with the game's own water. Height, ambient occlusion and porosity are kept in conversion_channels; Bedrock has no use for them."],
  [".mcmeta animations", "Bedrock flipbooks."],
  ["OptiFine / Continuity rules (optifine/ctm)", "repeat, random and fixed on full blocks, slabs, stairs, fences and walls draw with look-alike blocks in every graphics mode. Connected methods (ctm, horizontal, vertical, top) draw in Classic and Vibrant Visuals."],
  ["Overlay rules (overlay, overlay_random, overlay_repeat, ...)", "Thin see-through blocks on the faces next to the matching blocks, in every graphics mode, following Continuity's tile choice. Grass or sand spreading over other blocks, fallen leaves."],
  ["Block models (leaves, plants, decorations)", "Custom blocks with your geometry, sized to fit Bedrock's limits."],
  ["3D item models", "Held in hand with your display poses. Inventory icons are drawn from the model."],
  ["Item textures and item tints", "Bedrock item textures with the tint baked in."],
  ["Fixed colormaps (optifine/colormap)", "Colours baked into the textures."],
  ["Mob textures and EMF models (.jem) with animations", "Bedrock entity textures, geometry and animations, with PBR in Vibrant Visuals."],
  ["pack.png, pack.mcmeta", "The add-on's icon, description, credits and Java version."],
];

export const NOT_YET: string[] = [
  "Grass, dirt, sand, gravel and other soils keep vanilla blocks, because plants, animals and spreading depend on the real block. Their repeat mosaics draw as the game's own random tiles when the tiles join smoothly in any order (measured per pack; sand usually does). A flat random decal on the grass top, such as a clover layer, is painted into those tiles. A mosaic whose tiles only fit in your order shows its first tile.",
  "Sands your pack has no overlay for (red sand, soul sand) get edges cut in the shape of your own sand overlay, filled with their own texture, under your sand and grass.",
  "Connected methods (ctm, horizontal, ...) do not draw in ray tracing. Those blocks show your plain texture there.",
  "An overlay needs an empty cell in front of the face. Faces behind water, plants, snow layers or glass get none. A cell draws at most one block's worth of overlays; rare busy corners lose the extra ones.",
  "Rules on non-cube blocks' own model textures (candles, anvils, beacons) and rules that work on another rule's output tiles are listed, not drawn.",
  "Slabs, stairs, fences and walls get repeat, random and fixed rules, but no connected methods or overlays. Fence gates, buttons and doors stay vanilla.",
  "A pack can have only so many block types before the game slows down. Past that, some dyed slabs, stairs, fences and walls (concrete, wool, terracotta) keep the pack's plain texture.",
  "Random mob textures (by name, biome or UUID) are listed, not converted.",
  "Shader effects. Bedrock draws with its own lighting.",
];

export const OPTIONS: [string, string][] = [
  ['--vv-scene "My Bedrock Pack.mcpack"', "Takes lighting, colour grading, atmosphere, fog, water, PBR fallback and shadow settings from an existing Bedrock pack. None of its artwork is used. Without it, the converted pack writes no scene settings and the game's own apply."],
  ["--bedrock-grade", "Used with --vv-scene. Also gives the block textures that pack's colour grade, fitted over the block textures both packs share. Without it the Java colours are kept exactly."],
  ["--title", "Sets the pack name. Otherwise it is the ZIP's file name, with its version and resolution."],
  ["--version", "Sets the pack version, for example --version 4.1.0. Otherwise a version tag in the file name (R4.1.0, v4.1) is used, or 1.0.0."],
  ["--author", 'Sets the credits. Otherwise "By ..." lines in your description become the pack\'s authors.'],
  ["--key", "Seeds the pack UUIDs and namespaces the pack's entities. Keep it the same between updates and different between packs."],
];

export const SETTINGS: [string, string][] = [
  ["connected.chunkRadius", "3 chunks around each player"],
  ["connected.yBand", "24 blocks above and below the player"],
  ["connected.maxCarriers", "1500"],
  ["connected.maxCarriersPerChunk", "96 (about 32 blocks with three visible faces)"],
  ["terrain.chunkRadius", "6 chunks"],
  ["terrain.yBand", "24 blocks, plus a top-surface pass"],
  ["terrain.maxEntityCarriers", "1500"],
  ["terrain.maxNativeCarriers", "8000"],
  ["scanJobs", "2 chunk scans at a time"],
  ["budgetMs", "6 milliseconds of each tick for all of the engine's work together"],
  ["scanSliceMs", "4 milliseconds of each tick for those scans together"],
  ["replace.chunkRadius", "4 chunks"],
  ["replace.yBand", "32 blocks"],
  ["replace.maxSwapsPerTick", "512 block changes per tick"],
  ["replace.sliceMs", "4 milliseconds of each tick for swapping, and as much for the replacement scan"],
  ["replace.refreshTicks", "600 ticks between full rescans"],
  ["replace.viewAngle", "140 degrees: the cone around a player's view direction in which a change waits"],
  ["replace.farDistance", "0: changes happen at once. Above 0, changes within that many blocks of a player's head and in view wait until the player looks away"],
  ["replace.nearDistance", "0: with waiting on, the distance that counts instead of farDistance right after a player arrives"],
  ["far.chunkRadius", "32 chunks: beyond the simulation distance, areas out to this far from a player are converted one at a time (0 turns it off)"],
  ["far.blocks", "0: only leaves convert out there, the change seen from far away. 1 also swaps blocks near the surface. Near players everything converts"],
  ["replace.swapBack", "0: swapped blocks stay swapped. 1 swaps a chunk back to vanilla once no player is near it"],
  ["replace.joinTicks", "600 ticks (30 seconds) after a player joins, changes dimension or teleports"],
  ["leaves.chunkRadius", "32 chunks (0 turns leaves off)"],
  ["leaves.sliceMs", "6 milliseconds of each tick"],
  ["leaves.probesPerTick", "64 chunks looked at per pass"],
  ["leaves.recheckTicks", "20 ticks between looks for new leaves next to players"],
  ["leaves.checkChance", "8 (one random tick in 8 checks an unmarked leaf)"],
  ["overlay.chunkRadius", "4 chunks"],
  ["overlay.yBand", "24 blocks, plus a top-surface pass"],
  ["overlay.sliceMs", "4 milliseconds of each tick for overlay surfaces, and as much for their scan"],
  ["overlay.refreshTicks", "1200 ticks between full rescans"],
  ["intervalTicks", "4"],
];

export const COMMANDS: [string, string][] = [
  ["/scriptevent bct:control status", "Shows what the engine is doing."],
  ["/scriptevent bct:control off", "Stops converting and removes carriers and overlay surfaces near players. Blocks far from players stay converted. Saved in the world."],
  ["/scriptevent bct:control restore", "Puts the whole world back to vanilla blocks: it visits every chunk the engine changed, one area at a time, and says when it is done. Then the packs can be removed. on cancels it."],
  ["/scriptevent bct:control on", "Turns the engine back on."],
  ['/scriptevent bct:config {"connected":{"chunkRadius":4,"maxCarriers":2000}}', "Merges the given values over the defaults. Unknown names and out-of-range values are rejected. Saved in the world."],
  ['/scriptevent bct:config {"debug":true}', "Turns on logging of scan errors. With debug on, /scriptevent bct:probe logs the selection for the face under the crosshair."],
  ["/scriptevent bct:settings", "Opens the terrain edge height form."],
];
