/**
 * Bedrock Connected Textures engine: one source listener, one interval, one
 * graphics-mode check and one chunk scanner shared by connected textures and
 * terrain transitions. Native replacement blocks and overlay surfaces have
 * their own scanners because they also draw in ray tracing. `api` holds the
 * @minecraft/server objects (main.mjs).
 *
 * All of the engine's work in a tick shares one time budget (budgetMs,
 * budget.mjs): every part works in small steps until the time it was given is
 * up, so no part's slice adds to another's. Chunk scans and conversions go
 * where players head first (views.mjs rank).
 */
import { resolveSettings, createLog, createCarrierBudget, withinReach } from './settings.mjs';
import { createConnected } from './connected.mjs';
import { createTerrain } from './terrain.mjs';
import { Scanner } from './scanner.mjs';
import { listenSources } from './sources.mjs';
import { installRepeatBlocks } from './repeat-blocks.mjs';
import { createReplacements } from './replacement.mjs';
import { createViews } from './views.mjs';
import { createBudget } from './budget.mjs';
import { createLeaves } from './leaves.mjs';
import { createOverlaySurfaces } from './terrain-native.mjs';
import { createFar } from './far.mjs';
import { AuthoredError, compileAuthored } from './authored.mjs';

export function startEngine(api) {
  const { world, system } = api;
  let settings = resolveSettings();
  const log = createLog({ get debug() { return settings.debug; } });
  let players = [], realPlayers = [], playersTick = -1, far;
  // The real players, and the virtual player standing in an area loaded beyond them (far.mjs): every part
  // that converts blocks works around both.
  const currentPlayers = () => {
    if (playersTick !== system.currentTick) {
      realPlayers = world.getAllPlayers();
      players = [...realPlayers, ...(far?.virtualPlayers() ?? [])];
      playersTick = system.currentTick;
    }
    return players;
  };
  const reach = part => (dimension, location) =>
    withinReach(currentPlayers(), dimension, location, settings[part].chunkRadius + 1, settings[part].yBand + 8);

  // A replaced block stands for its vanilla block wherever a rule or a terrain edge looks at blocks:
  // vanillaType maps a replacement type to its vanilla type, aliasesOf a vanilla type to its replacement types.
  let replacements, leaves;
  const vanillaType = typeId => replacements?.vanillaOf(typeId) ?? leaves?.vanillaOf(typeId) ?? typeId;
  const aliasesOf = typeId => [...(replacements?.customsOf(typeId) ?? []), ...(leaves?.customsOf(typeId) ?? [])];
  const vanillaStates = block => replacements?.vanillaStatesOf(block) ?? leaves?.vanillaStatesOf(block);
  const carriers = createCarrierBudget(() => settings.connected);
  const connected = createConnected({ api, carriers, inReach: reach('connected'), log, vanillaType, aliasesOf, vanillaStates });
  // The pack's own overlay rules are native blocks in the cells in front of faces; they take cells from generated edges.
  let overlays;
  const terrain = createTerrain({ api, limits: () => settings.terrain, inReach: reach('terrain'), log, vanillaType, aliasesOf, vanillaStates,
    passable: () => overlays.surfaceTypes() });
  overlays = createOverlaySurfaces({ api, limits: () => settings.overlay, log, vanillaType, vanillaStates, aliasesOf,
    foreign: () => terrain.native.ids() });
  const now = () => (api.now ?? Date.now)();
  const budget = createBudget({ system, now, limit: () => settings.budgetMs });
  // An area beyond the simulation distance is released once nothing is left to convert.
  far = createFar({ api, limits: () => settings.far, log, idle: () => {
    const leaf = leaves.status, swap = replacements.status, overlay = overlays.status;
    return leaf.toConvert === 0 && leaf.queued === 0 && swap.queued === 0 && overlay.queued === 0;
  } });
  // Where players look and travel: replacement blocks and leaf models swap only where no player sees it (the
  // replace view settings), and chunks are scanned and converted where players head first.
  const views = createViews({ system, limits: () => settings.replace });
  const ahead = waits => record => views.rank(record.dimension.id, record.x, record.z, waits);
  const scanner = new Scanner({ system, BlockVolume: api.BlockVolume, jobs: () => settings.scanJobs, now, log, rank: ahead(false), consumers: [
    { radius: () => settings.connected.chunkRadius, band: () => settings.connected.yBand,
      types: () => connected.blockTypes(), found: (dimension, location) => connected.update(dimension, location) },
    { radius: () => settings.terrain.chunkRadius, band: () => settings.terrain.yBand, types: () => terrain.blockTypes(),
      found: (dimension, location, typeId) => terrain.found(dimension, location, typeId),
      surface: (dimension, location) => terrain.surface(dimension, location) },
  ] });

  let replaceScanner;
  leaves = createLeaves({ api, limits: () => settings.leaves, log, logAliases: type => replacements?.customsOf(type) ?? [], views,
    solid: type => replacements?.isSolid(type) ?? false });
  replacements = createReplacements({ api, limits: () => settings.replace, log, views,
    rescan: (dimension, location) => replaceScanner.markDirty(dimension.id, location),
    // Overlay surfaces show the block behind them like air does.
    openExtras: () => [...leaves.customTypes(), ...overlays.surfaceTypes()],
    // Replacements never get carriers: the ones a block had while vanilla go right away.
    swapped: (dimension, location) => { if (connected.status.actors) connected.update(dimension, location); overlays.swapped(dimension, location); } });
  replaceScanner = new Scanner({ system, BlockVolume: api.BlockVolume, jobs: () => 1, now, log, rank: ahead(true), consumers: [
    { radius: () => settings.replace.chunkRadius, band: () => settings.replace.yBand, types: () => replacements.blockTypes(), scan: replacements.scan },
  ] });
  const overlayScanner = new Scanner({ system, BlockVolume: api.BlockVolume, jobs: () => 1, now, log, rank: ahead(false), consumers: [
    { radius: () => settings.overlay.chunkRadius, band: () => settings.overlay.yBand, types: () => overlays.blockTypes(),
      prepare: overlays.prepare, found: overlays.found, surface: overlays.surface },
  ] });

  const terrainSources = new Map();
  /** A pack's terrain data: generated edges (terrain providers) and its overlay surfaces as one {overlay} entry. */
  function setTerrain(provider, data) {
    if (!Array.isArray(data)) throw new Error('Invalid terrain data');
    const edges = data.filter(entry => !entry?.overlay);
    const next = new Map(terrainSources);
    next.set(provider, edges);
    terrain.setProviders([...next.values()].flat());
    terrainSources.set(provider, edges);
    overlays.setSource(provider, data.find(entry => entry?.overlay)?.overlay);
    replacements.refreshTypes();
    overlayScanner.reset();
  }
  const sources = listenSources({ system, log, onSource(part, provider, data) {
    if (part === 'authored') {
      // A hand-written pack's data compiles into the same replace data the converter writes.
      let compiled;
      try { compiled = compileAuthored(api, data); }
      catch (error) {
        if (error instanceof AuthoredError) {
          try { world.sendMessage('§c[BCT] ' + provider + ' (scripts/bct.js): ' + error.message); } catch { /* no players yet */ }
        }
        throw error;
      }
      replacements.setSource(provider, compiled.replace); leaves.setSource(provider, compiled.replace); replaceScanner.reset();
      // Its edges join the converted packs' terrain data, under a name of their own.
      if (compiled.terrain.length || terrainSources.has('authored:' + provider)) setTerrain('authored:' + provider, compiled.terrain);
      if (compiled.connected) connected.setSource('authored:' + provider, compiled.connected);
    }
    else if (part === 'connected') connected.setSource(provider, data);
    // Replacements first: the leaves count the replacement logs as logs, so they must exist when the leaves read them.
    else if (part === 'replace') { replacements.setSource(provider, data); leaves.setSource(provider, data); replaceScanner.reset(); }
    else setTerrain(provider, data);
    scanner.reset();
  } });
  installRepeatBlocks(system);

  let enabled = true, started = false, suspended = false, passes = 0, refreshPasses = 0, overlayPasses = 0;

  function changed({ block }, { byPlayer = false } = {}) {
    if (!block) return;
    replacements.changed(block);
    replaceScanner.markDirty(block.dimension.id, block.location);
    if (enabled) overlays.changed(block, { immediate: byPlayer });
    if (!enabled || suspended) return;
    terrain.changed(block);
    connected.invalidate();
    const { dimension, location } = block;
    system.run(() => {
      for (let x = -1; x <= 1; x++) for (let y = -1; y <= 1; y++) for (let z = -1; z <= 1; z++)
        connected.update(dimension, { x: location.x + x, y: location.y + y, z: location.z + z });
    });
  }

  // Replaced blocks stay replaced while players are near: breaking, using and
  // exploding work on the replacement itself (its loot, hardness, resistance).
  // A hole shows the replaced blocks behind it right away.
  // A player's own placing and breaking takes effect in the same tick, swap first, then the edges.
  world.afterEvents.playerPlaceBlock.subscribe(event => {
    if (event.block) replacements.placed(event.block);
    changed(event, { byPlayer: true });
    leaves.placed(event.block);
  });
  world.afterEvents.playerBreakBlock.subscribe(event => {
    if (event.block) replacements.revealed(event.block.dimension, event.block.location, { immediate: true });
    changed(event, { byPlayer: true });
    if (!event.block) return;
    const { dimension, location } = event.block;
    replacements.experience(event);
    leaves.removed(dimension, location, event.brokenBlockPermutation?.type?.id);
  });
  world.afterEvents.blockExplode?.subscribe(event => {
    changed(event);
    if (!event.block) return;
    replacements.revealed(event.block.dimension, event.block.location);
    leaves.removed(event.block.dimension, event.block.location, event.explodedBlockPermutation?.type?.id, { later: true });
  });
  world.beforeEvents.playerBreakBlock?.subscribe(event => leaves.beforeBreak(event));
  // An axe strips a replaced log; the log has no interact component, so building against it works as in vanilla.
  world.beforeEvents.playerInteractWithBlock?.subscribe(event => replacements.interact(event));
  // Bedrock drops a custom block's own item for Silk Touch: it becomes the vanilla block's item.
  world.afterEvents.entitySpawn?.subscribe(({ entity }) => {
    try {
      if (entity.typeId !== 'minecraft:item') return;
      const stack = entity.getComponent('minecraft:item')?.itemStack;
      if (!stack || leaves.itemSpawned(entity, stack)) return;
      const vanilla = vanillaType(stack.typeId);
      if (vanilla === stack.typeId || !api.ItemStack) return;
      const location = entity.location, dimension = entity.dimension;
      entity.remove();
      dimension.spawnItem(new api.ItemStack(vanilla, stack.amount), location);
    } catch (error) { log('item ' + String(error)); }
  });
  // Pistons move replacements with their states; the rescan adopts them with the pattern of their new place.
  world.afterEvents.pistonActivate?.subscribe(({ block, dimension }) => {
    system.runTimeout(() => {
      replaceScanner.markDirty(dimension.id, block.location);
      overlayScanner.markDirty(dimension.id, block.location);
      leaves.moved(dimension, block.location);
    }, 3);
  });
  system.beforeEvents.startup?.subscribe(event => {
    event.blockComponentRegistry.registerCustomComponent('bct:leaf', leaves.component);
  });

  world.afterEvents.entityLoad.subscribe(({ entity }) => {
    connected.recover(entity);
    terrain.onEntityLoad(entity);
  });

  function setEnabled(value, save = true) {
    enabled = value;
    if (save) world.setDynamicProperty('bct:enabled', value);
    connected.setEnabled(value);
    terrain.setEnabled(value);
    replacements.setEnabled(value);
    leaves.setEnabled(value);
    // Turned off, overlay surfaces are removed from every chunk that holds them as it loads.
    overlays.setEnabled(value);
    far.setEnabled(value);
    if (value) { scanner.reset(); replaceScanner.reset(); overlayScanner.reset(); }
    else { connected.clear(); scanner.stop(); replaceScanner.stop(); overlayScanner.stop(); }
  }

  /** Entity carriers cannot draw in ray tracing: with every player in ray tracing they are removed and blocks show their own faces. */
  function refreshGraphics(all) {
    // Entity carriers draw for real players only, never around a virtual one far away.
    const real = all.filter(player => !player.virtual);
    const drawn = real.filter(player => { try { return player.graphicsMode !== api.GraphicsMode.RayTraced; } catch { return true; } });
    const next = real.length > 0 && !drawn.length;
    if (next !== suspended) {
      suspended = next;
      if (suspended) { scanner.stop(); connected.clear(); connected.setEnabled(false); terrain.suspend(); }
      else { connected.setEnabled(enabled); terrain.resume(); scanner.reset(); }
    }
    return drawn;
  }

  function status() {
    return { enabled, suspended, settings, connected: connected.status, carriers: carriers.total, terrain: terrain.status, chunks: scanner.chunks.size,
      replacements: replacements.status, leaves: leaves.status, overlays: overlays.status, far: far.status };
  }

  system.afterEvents.scriptEventReceive.subscribe(event => {
    const reply = message => { try { event.sourceEntity?.sendMessage?.(message); } catch { /* The player may have left. */ } };
    if (event.id === 'bct:control') {
      const command = (event.message ?? '').trim();
      if (command === 'off' || command === 'clear') setEnabled(false);
      else if (command === 'on') setEnabled(true);
      else if (command === 'status') reply('[BCT] ' + JSON.stringify(status()));
    } else if (event.id === 'bct:config') {
      try {
        const overrides = JSON.parse(event.message || '{}');
        settings = resolveSettings(overrides);
        world.setDynamicProperty('bct:settings', JSON.stringify(overrides));
        scanner.reset();
        reply('[BCT] settings ' + JSON.stringify(settings));
      } catch (error) { reply('[BCT] ' + String(error.message ?? error)); }
    } else if (event.id === 'bct:settings' && event.sourceEntity?.typeId === 'minecraft:player') {
      const player = event.sourceEntity;
      if (terrain.providers.some(provider => provider.exclusive_quarters)) { player.sendMessage('Priority-masked surfaces are fixed at 1 texture pixel above terrain.'); return; }
      system.run(() => { if ((event.message ?? '').trim() === 'reset') terrain.settings.reset(player); else void terrain.settings.open(player); });
    } else if (event.id === 'bct:probe' && settings.debug && event.sourceEntity?.typeId === 'minecraft:player') {
      const hit = event.sourceEntity.getBlockFromViewDirection({ maxDistance: 12 });
      if (hit) log('probe ' + JSON.stringify({ block: hit.block.typeId, location: hit.block.location, face: hit.face, connected: connected.probe(hit.block),
        overlays: overlays.probe(hit.block) }));
      try { log('terrain trace ' + JSON.stringify(terrain.trace(event.sourceEntity))); } catch (error) { log('terrain trace ' + String(error)); }
    }
  });

  function start() {
    started = true;
    try { settings = resolveSettings(JSON.parse(world.getDynamicProperty('bct:settings') ?? '{}')); }
    catch (error) { log('saved settings ignored: ' + String(error)); }
    if (world.getDynamicProperty('bct:enabled') === false) setEnabled(false);
  }

  function tick() { budget.measure(poll); }
  function poll() {
    if (!started) start();
    const all = currentPlayers(), drawn = refreshGraphics(all);
    // Replacement blocks and overlay surfaces also draw in ray tracing, so they follow every player.
    if (enabled) { replaceScanner.poll(all); overlayScanner.poll(all); }
    // Water, mobs and other packs change blocks without events; rescan now and then.
    if (++refreshPasses % Math.max(1, Math.round(settings.replace.refreshTicks / settings.intervalTicks)) === 0) replaceScanner.reset();
    if (++overlayPasses % Math.max(1, Math.round(settings.overlay.refreshTicks / settings.intervalTicks)) === 0) overlayScanner.reset();
    if (enabled) far.tick(realPlayers);
    terrain.maintain();
    if (!enabled || suspended || !drawn.length) return;
    connected.maintain();
    scanner.poll(drawn);
    if (++passes % 25 === 0) { connected.despawn(); terrain.despawn(); }
  }
  system.runInterval(tick, settings.intervalTicks);
  // The parts that share each tick's budget, each up to its own cap.
  const parts = [
    { run: until => replacements.run(until), cap: () => settings.replace.sliceMs },
    { run: until => leaves.run(until), cap: () => settings.leaves.sliceMs },
    { run: until => replaceScanner.run(until), cap: () => settings.scanSliceMs },
    { run: until => scanner.run(until), cap: () => settings.scanSliceMs },
    { run: until => overlayScanner.run(until), cap: () => settings.overlay.sliceMs },
    { run: until => overlays.tick(currentPlayers(), until), cap: () => settings.overlay.sliceMs },
  ];
  let replaceTicks = 0;
  system.runInterval(() => {
    if (!started) return;
    budget.measure(() => {
      const players = currentPlayers(), maintenance = ++replaceTicks % 10 === 0;
      views.update(players);
      replacements.tick(players, maintenance);
      leaves.tick(players, replaceTicks % 4 === 0);
    });
    budget.share(parts);
  }, 1);

  return { settings: () => settings, connected, terrain, scanner, sources, carriers, replacements, leaves, replaceScanner,
    overlays, overlayScanner, views, budget, tick, setEnabled, status };
}
