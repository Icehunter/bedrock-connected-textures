/**
 * Native replacement blocks. A converted pack can ship custom blocks that draw
 * an author's rules natively (also in ray tracing). Near players, vanilla
 * blocks that show are swapped for their replacement and stay swapped while a
 * player is in range: nothing turns back to vanilla where players look, stand
 * or dig. A replacement carries every vanilla state (booleans as 0 or 1), so
 * swapping back restores the exact original permutation.
 *
 * Gameplay comes from the replacement block itself: the converter copies the
 * vanilla destroy time, tool speeds, loot (Fortune, shears), explosion
 * resistance, flammability, redstone conduction, light, map color and the
 * vanilla block tags. Silk Touch drops the custom block's own item, which
 * engine.mjs turns into the vanilla item. Ores drop their vanilla experience
 * (xp) for the right tool. An axe strips a replaced log (strip): engine.mjs
 * passes the playerInteractWithBlock before-event, so the log is not an
 * interactable block and placing blocks against it works as in vanilla. Blocks
 * whose vanilla behavior a custom block cannot carry (grass spreading, ice
 * melting, falling sand...) are never replaced; the converter lists them.
 *
 * A block is swapped when it shows (a neighbor is not an opaque full cube:
 * air, water, glass, leaves, a torch, a slab...) or when it backs a shown
 * block, so digging never uncovers a vanilla face. A block stays vanilla only
 * while a neighbor needs that exact vanilla block (`needs` in the pack data:
 * cocoa on jungle logs, chorus on end stone, dead bushes on terracotta...).
 * Torches, rails, carpets, slabs and the like only need a solid or sturdy
 * face, which the full-cube replacement has, so they change nothing.
 *
 * Blocks change as soon as they load, and a block a player places or uncovers
 * changes in the same tick. A scan writes the blocks whose replacement depends
 * only on their position in bulk, one fillBlocks call per distinct permutation
 * (bulk.mjs); logs and blocks with mirrored states go one by one. Optionally
 * (farDistance above 0) a change to a block that shows and that some player
 * could see (views.mjs) waits until no player could.
 *
 * A chunk scan (`scan`) reads little: a 16-layer section with nothing to swap,
 * or with nothing open near it (deep rock, open sky), costs one or two
 * containsBlock calls, then 4x4x4 cells the same way; only cells holding both
 * are read block by block. All changes run in `run(until)` within the engine's
 * shared time budget, in this order: update_bit clears a log swap owes the
 * leaves around it, changes that waited for players to look away, blocks a dig
 * or an explosion uncovered, the swaps scans found (in the order chunks were
 * ranked: ahead of players first), and last the chunks players left.
 *
 * Swapped blocks stay swapped. They are swapped back when the engine is turned
 * off or restored, when a neighbor comes to need the vanilla block, and, with
 * swapBack set, when no player is in range. Replacements found where the engine did not put them
 * (pistons, structures) are adopted with the pattern states of their new
 * position. Ownership is saved per chunk so a later session finds every
 * swapped block.
 *
 * Per-block policies from the pack data: leafGuard (logs: vanilla leaves keep
 * their decay state), pane (connection states refreshed when neighbors
 * change), models (weighted Java models picked per position), strip and xp.
 */
import { createBulkWriter } from './bulk.mjs';
import { javaRandom, javaModelIndex } from './tiles.mjs';
import { createViews, showsAt } from './views.mjs';

const INDEX = 'bct:replace:index';
const PART_SIZE = 30000;
const SIDES = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]];
const PANE_SIDES = { north: [0, 0, -1], south: [0, 0, 1], west: [-1, 0, 0], east: [1, 0, 0] };
const REVEAL_BUDGET = 64;
// Where a block that needs another sits, seen from the block it needs: on top of it (it needs the block
// below it), under it (the block above it), beside it, or on any side.
const NEED_OFFSETS = { below: [[0, 1, 0]], above: [[0, -1, 0]], side: [[1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1]], any: SIDES };
// Scan sections and cells (blocks).
const SECTION = 16, CELL = 4;
// update_bit clears waiting before log swaps pause (each log swap can owe a few hundred).
const RESET_BACKLOG = 512;
// Owned chunks looked at per maintenance pass for players having left them.
const MAINTAIN_CHUNKS = 64;

const modulo = (value, size) => ((value % size) + size) % size;
const chunkKey = (dimensionId, location) => dimensionId + '|' + Math.floor(location.x / 16) + '|' + Math.floor(location.z / 16);
const positionKey = (dimensionId, location) => dimensionId + '|' + location.x + '|' + location.y + '|' + location.z;
const pack = location => ((location.y + 2048) * 256 + modulo(location.z, 16) * 16 + modulo(location.x, 16));
const unpack = (cx, cz, value) => ({ x: cx * 16 + (value % 16), y: Math.floor(value / 256) - 2048, z: cz * 16 + (Math.floor(value / 16) % 16) });
const offset = (location, [dx, dy, dz]) => ({ x: location.x + dx, y: location.y + dy, z: location.z + dz });

function weighted(weights, draw) {
  for (let index = 0; index < weights.length; index++) {
    if (draw < weights[index]) return index;
    draw -= weights[index];
  }
  return weights.length - 1;
}

function validate(data) {
  if (!data || data.format_version !== 1 || !Array.isArray(data.blocks) || !Array.isArray(data.open) || !Array.isArray(data.solid))
    throw new Error('Invalid replacement data');
  const id = /^[a-z0-9_.-]+:[a-z0-9_./-]+$/;
  for (const entry of data.blocks) {
    if (!id.test(entry.vanilla) || !id.test(entry.block) || typeof entry.mirror !== 'object' || !Array.isArray(entry.axes) || !Array.isArray(entry.random))
      throw new Error('Invalid replacement block');
    if (entry.bools && (!Array.isArray(entry.bools) || entry.bools.some(name => typeof name !== 'string'))) throw new Error('Invalid boolean states');
    for (const [state, axis, size] of entry.axes)
      if (typeof state !== 'string' || !['x', 'y', 'z'].includes(axis) || !Number.isInteger(size) || size < 1 || size > 16) throw new Error('Invalid replacement axis');
    for (const random of entry.random)
      if (typeof random.state !== 'string' || !Number.isInteger(random.count) || random.count < 1 || !Array.isArray(random.weights)) throw new Error('Invalid replacement random state');
    for (const model of entry.models ?? [])
      if (typeof model.state !== 'string' || !Array.isArray(model.weights) || !model.weights.length) throw new Error('Invalid replacement model state');
    if (entry.leafGuard && (!Number.isInteger(entry.leafGuard.radius) || !Array.isArray(entry.leafGuard.leaves))) throw new Error('Invalid leaf guard');
    if (entry.pane && Object.keys(PANE_SIDES).some(side => typeof entry.pane[side] !== 'string')) throw new Error('Invalid pane states');
    if (entry.strip && !id.test(entry.strip)) throw new Error('Invalid strip target');
    if (entry.xp && (!Array.isArray(entry.xp) || entry.xp.length !== 2 || !entry.xp.every(Number.isInteger))) throw new Error('Invalid experience range');
    if (entry.tool && (!Array.isArray(entry.tool.all) || !Array.isArray(entry.tool.any) || ![...entry.tool.all, ...entry.tool.any].every(tag => id.test(tag))))
      throw new Error('Invalid tool');
  }
  if (data.needs !== undefined && (!Array.isArray(data.needs) || data.needs.some(need => !need || !Array.isArray(need.blocks) ||
    !Array.isArray(need.needs) || !Object.hasOwn(NEED_OFFSETS, need.side) || ![...need.blocks, ...need.needs].every(type => id.test(type)))))
    throw new Error('Invalid needs');
  return data;
}

/**
 * api: @minecraft/server objects. limits(): the `replace` settings.
 * rescan(dimension, location): asks the scanner to look at a chunk again.
 * openExtras(): more block types that show their neighbors (converted leaves).
 * swapped(dimension, location): a vanilla block was just replaced there (its carriers can go).
 * views: where players look (views.mjs), shared with the leaves; its own from the `replace` settings otherwise.
 * Returns the scanner consumer (scan) and the hooks engine.mjs wires to world events and its budget (run).
 */
export function createReplacements({ api, limits, log = () => {}, rescan = () => {}, openExtras = () => [], swapped = () => {},
  views = createViews({ system: api.system, limits }) }) {
  const { world, system } = api;
  // Milliseconds for the time budget (tests pass their own clock as api.now).
  const now = () => (api.now ?? Date.now)();
  const sources = new Map();
  let byVanilla = new Map(), byCustom = new Map(), open = [], solid = [], solidSet = new Set(), paneConnect = new Set();
  let needers = new Map(), needyRules = new Map(), needyTypes = [];  // needed vanilla type -> rules, needy type -> rules, every needy type
  let vanillaTypes = [], customTypes = [], paneTypes = [];             // the types a scan asks for
  const owned = new Map();          // chunk key -> { dimension, cx, cz, positions: Set<packed> }
  const urgent = new Map();         // position key -> swap of a block a dig or an explosion uncovered
  const queue = new Map();          // position key -> { swap: 'in' | 'out' | 'adopt', dimension, location, shown?, need? }
  const waiting = new Map();        // position key -> a queued change some player could see; applied once none can
  const parked = new Map();         // position key -> a log swap waiting for leaf update_bit clears to catch up (next tick)
  const leaving = new Map();        // chunk key -> { dimension, cx, cz, positions } chunks players left, swapped back in turn
  const leafReset = new Map();      // position key -> { dimension, location, tick } leaves whose update_bit was clear
  const dirty = new Set();
  const permutations = new Map();
  const broken = [];                // replacement blocks the game could not resolve
  let players = [], passStamp = -1, passStart = 0, pass, tickSeen = -1, changes = 0;
  let enabled = true, recovered = false, maintainCursor = 0, swaps = 0, reverts = 0, adopted = 0, leafResets = 0, deferred = 0;

  function rebuild() {
    byVanilla = new Map(); byCustom = new Map(); paneConnect = new Set(); needers = new Map(); needyRules = new Map();
    const openTypes = new Set(openExtras()), solidTypes = new Set(), needy = new Set();
    // A block type the game does not know is left out of every block filter.
    const exists = type => !api.BlockTypes?.get || Boolean(api.BlockTypes.get(type));
    for (const data of sources.values()) {
      for (const entry of data.blocks) {
        if (!byVanilla.has(entry.vanilla)) byVanilla.set(entry.vanilla, entry);
        byCustom.set(entry.block, entry);
      }
      data.open.forEach(type => openTypes.add(type));
      data.solid.forEach(type => solidTypes.add(type));
      (data.paneConnect ?? []).forEach(type => paneConnect.add(type));
      for (const need of data.needs ?? []) {
        const types = new Set(need.blocks.filter(exists)), needs = new Set(need.needs), offsets = NEED_OFFSETS[need.side];
        if (!types.size) continue;
        for (const type of types) {
          needy.add(type);
          if (!needyRules.has(type)) needyRules.set(type, []);
          needyRules.get(type).push({ needs, offsets });
        }
        for (const type of needs) {
          if (!needers.has(type)) needers.set(type, []);
          needers.get(type).push({ types, offsets });
        }
      }
    }
    // A block whose definition did not load is unknown to the game.
    for (const entry of byCustom.values()) if (!exists(entry.block)) disable(entry, new Error('the game has no block ' + entry.block));
    // See-through replacements (glass) show their neighbors just like the vanilla block.
    for (const entry of byCustom.values()) {
      if (entry.broken) { if (entry.open || openTypes.has(entry.vanilla)) openTypes.add(entry.vanilla); else solidTypes.add(entry.vanilla); continue; }
      if (entry.open || openTypes.has(entry.vanilla)) openTypes.add(entry.block);
      else { solidTypes.add(entry.block); solidTypes.add(entry.vanilla); }
      if (paneConnect.has(entry.vanilla)) paneConnect.add(entry.block);
    }
    for (const type of openTypes) solidTypes.delete(type);
    open = [...openTypes]; solid = [...solidTypes]; solidSet = solidTypes; needyTypes = [...needy];
    refreshScanTypes();
  }

  /** The block types a scan asks for: working replacements and the vanilla blocks they stand for. */
  function refreshScanTypes() {
    vanillaTypes = [...byVanilla].filter(([, entry]) => !entry.broken).map(([type]) => type);
    customTypes = [...byCustom].filter(([, entry]) => !entry.broken).map(([type]) => type);
    paneTypes = [...byCustom].filter(([, entry]) => entry.pane && !entry.broken).map(([type]) => type);
  }

  const resolve = (type, states) => {
    const key = type + JSON.stringify(states);
    let permutation = permutations.get(key);
    if (!permutation) {
      permutation = api.BlockPermutation.resolve(type, states);
      if (permutations.size > 50000) permutations.clear();
      permutations.set(key, permutation);
    }
    return permutation;
  };

  const getBlock = (dimension, location) => { try { return dimension.getBlock(location); } catch { return undefined; } };

  /**
   * A replacement whose block the game does not know (its definition failed to
   * load) is switched off after the first failure instead of being retried
   * every tick; swapping back still works because it resolves vanilla blocks.
   */
  function disable(entry, error) {
    if (entry.broken) return;
    entry.broken = true;
    broken.push(entry.block);
    log('replacement ' + entry.block + ' switched off: ' + String(error));
    refreshScanTypes();
  }

  /** Pattern, random and model states for a position. */
  function positionStates(entry, location, states) {
    for (const [state, axis, size] of entry.axes) states[state] = modulo(location[axis], size);
    for (const random of entry.random) {
      const value = javaRandom(location, random.face ?? 'north', random.loops ?? 0, random.symmetry ?? 'none');
      const total = random.weights.reduce((sum, weight) => sum + weight, 0);
      states[random.state] = Math.min(random.count - 1, weighted(random.weights, value % total));
    }
    for (const model of entry.models ?? []) states[model.state] = javaModelIndex(location, model.weights, model.selection ?? 'java-26.2-block-position');
    return states;
  }

  /** Replacement permutation for a vanilla block (or vanilla states) at a location. Boolean vanilla states are mirrored as 0 or 1. */
  function replacementOf(entry, block, vanilla = block.permutation.getAllStates?.() ?? {}, location = block.location) {
    const states = {};
    for (const [name, mirrored] of Object.entries(entry.mirror)) {
      if (!(name in vanilla)) continue;
      const value = vanilla[name];
      states[mirrored] = typeof value === 'boolean' ? (value ? 1 : 0) : value;
    }
    return resolve(entry.block, positionStates(entry, location, states));
  }

  /** The vanilla states a replacement stands for, from its mirrored states. */
  function vanillaStates(entry, block) {
    const current = block.permutation.getAllStates?.() ?? {}, states = {}, bools = entry.bools ?? [];
    for (const [name, mirrored] of Object.entries(entry.mirror)) {
      if (!(mirrored in current)) continue;
      states[name] = bools.includes(name) ? current[mirrored] === 1 || current[mirrored] === true : current[mirrored];
    }
    return states;
  }
  const originalOf = (entry, block) => resolve(entry.vanilla, vanillaStates(entry, block));

  function record(dimensionId, location, add) {
    const key = chunkKey(dimensionId, location);
    let chunk = owned.get(key);
    if (!chunk && add) {
      chunk = { dimension: dimensionId, cx: Math.floor(location.x / 16), cz: Math.floor(location.z / 16), positions: new Set() };
      owned.set(key, chunk);
    }
    if (!chunk) return;
    const value = pack(location);
    if (add ? !chunk.positions.has(value) : chunk.positions.has(value)) {
      if (add) { chunk.positions.add(value); spans(chunk, location.y); } else chunk.positions.delete(value);
      if (!chunk.positions.size) owned.delete(key);
      dirty.add(key);
    }
  }
  const isOwned = (dimensionId, location) => owned.get(chunkKey(dimensionId, location))?.positions.has(pack(location)) ?? false;
  /** Whether a block type is an opaque full cube that hides the faces next to it. */
  const isSolid = type => solidSet.has(type);
  /** The lowest and highest owned y of a chunk (never shrinks; it only lets maintenance skip chunks quickly). */
  function spans(chunk, y) {
    if (!(chunk.low <= y)) chunk.low = y;
    if (!(chunk.high >= y)) chunk.high = y;
  }

  /** Whether a neighbor needs the exact vanilla block `type` at a location (cocoa on a jungle log): only block types the pack data lists do. */
  function neededAt(dimension, location, type) {
    const rules = needers.get(type);
    if (!rules) return false;
    for (const rule of rules) for (const delta of rule.offsets)
      if (rule.types.has(getBlock(dimension, offset(location, delta))?.typeId)) return true;
    return false;
  }

  /** Whether a queued change would be seen: some player looks at the block and it shows (worked out only then). */
  function visible(item) {
    if (!views.sees(item.dimension.id, item.location)) return false;
    if (item.shown === undefined) item.shown = showsAt(item.dimension, item.location, isSolid);
    return item.shown;
  }

  /** Queues a change. One that already waits for players to look away keeps waiting, with the newer details, while it would still be seen. */
  function enqueue(key, item) {
    if (!waiting.has(key)) { queue.set(key, item); return; }
    if (visible(item)) waiting.set(key, item);
    else { waiting.delete(key); queue.set(key, item); }
  }
  const forget = key => { urgent.delete(key); queue.delete(key); waiting.delete(key); parked.delete(key); };

  /** Whether some player is still within the swap range of a block (maintenance swaps the rest back). */
  function held(dimensionId, location) {
    const { chunkRadius, yBand } = limits(), cx = Math.floor(location.x / 16), cz = Math.floor(location.z / 16);
    return players.some(player => player.dimension.id === dimensionId &&
      Math.max(Math.abs(cx - Math.floor(player.location.x / 16)), Math.abs(cz - Math.floor(player.location.z / 16))) <= chunkRadius + 1 &&
      Math.abs(location.y - player.location.y) <= yBand + 8);
  }

  /**
   * Leaves only run their decay check while update_bit is set, and a block
   * change next to them sets it. Returns false when a nearby leaf already has
   * a check pending (a log swapped in now could make it decay); otherwise
   * remembers the leaves whose update_bit is clear, to clear it again after
   * the swap.
   */
  function guardLeaves(entry, dimension, location, swapIn) {
    const guard = entry.leafGuard;
    if (!guard || !guard.leaves.length) return true;
    const r = guard.radius, found = [];
    try {
      const volume = new api.BlockVolume(offset(location, [-r, -r, -r]), offset(location, [r, r, r]));
      for (const at of dimension.getBlocks(volume, { includeTypes: guard.leaves }, true).getBlockLocationIterator()) found.push({ ...at });
    } catch (error) { log('leaf guard ' + String(error)); return !swapIn; }
    const clear = [];
    for (const at of found) {
      const states = getBlock(dimension, at)?.permutation.getAllStates?.() ?? {};
      if (states.persistent_bit) continue;
      // A bit set by one of this engine's own swaps (still waiting to be cleared) is not a pending check.
      if (states.update_bit && !leafReset.has(positionKey(dimension.id, at))) { if (swapIn) return false; continue; }
      clear.push(at);
    }
    // Deleted first so the map stays in the order the clears fall due.
    for (const at of clear) {
      const key = positionKey(dimension.id, at);
      leafReset.delete(key);
      leafReset.set(key, { dimension, location: at, tick: system.currentTick + 1 });
    }
    return true;
  }

  /** Clears update_bit again on one leaf a swap set it on. Returns the block changes made (0 or 1). */
  function resetLeaf(item) {
    const block = getBlock(item.dimension, item.location);
    const states = block?.permutation.getAllStates?.();
    if (!states?.update_bit || states.persistent_bit) return 0;
    try { block.setPermutation(resolve(block.typeId, { ...states, update_bit: false })); leafResets++; return 1; }
    catch (error) { log('leaf reset ' + String(error)); return 0; }
  }

  /** Swaps one replacement back right now. Returns true when the block changed. */
  function revert(block) {
    if (!block) return false;
    const dimensionId = block.dimension.id;
    forget(positionKey(dimensionId, block.location));
    const entry = byCustom.get(block.typeId);
    record(dimensionId, block.location, false);
    if (!entry) return false;
    if (entry.leafGuard) guardLeaves(entry, block.dimension, block.location, false);
    try { block.setPermutation(originalOf(entry, block)); reverts++; return true; }
    catch (error) { log('replacement revert ' + JSON.stringify(block.location) + ': ' + String(error)); return false; }
  }

  function revertAt(dimension, location) {
    const block = getBlock(dimension, location);
    if (!block) return false;
    if (!byCustom.has(block.typeId)) { record(dimension.id, location, false); return false; }
    return revert(block);
  }

  /** Swaps a vanilla block in right now; false when it is not a replaceable block, a neighbor needs it, or the game refuses. */
  function swapIn(block) {
    const entry = byVanilla.get(block.typeId);
    if (!entry || entry.broken || !enabled) return false;
    if (neededAt(block.dimension, block.location, entry.vanilla)) return false;
    let permutation;
    try { permutation = replacementOf(entry, block); }
    catch (error) { disable(entry, error); return false; }
    if (!guardLeaves(entry, block.dimension, block.location, true)) { rescan(block.dimension, block.location); return false; }
    try { block.setPermutation(permutation); swaps++; record(block.dimension.id, block.location, true); }
    catch (error) { log('replacement swap ' + JSON.stringify(block.location) + ': ' + String(error)); return false; }
    try { swapped(block.dimension, block.location); } catch (error) { log('after swap ' + String(error)); }
    return true;
  }

  /** Gives an owned replacement the pattern states of its position (it may have been moved there). */
  function adopt(block) {
    const entry = byCustom.get(block.typeId);
    if (!entry) return false;
    record(block.dimension.id, block.location, true);
    const current = block.permutation.getAllStates?.() ?? {};
    const next = positionStates(entry, block.location, { ...current });
    if (Object.keys(next).every(name => next[name] === current[name])) return true;
    try { block.setPermutation(resolve(block.typeId, next)); return true; }
    catch (error) { log('replacement adopt ' + String(error)); return false; }
  }

  const volumeOf = (min, max, grow = 0) => new api.BlockVolume({ x: min.x - grow, y: min.y - grow, z: min.z - grow }, { x: max.x + grow, y: max.y + grow, z: max.z + grow });

  /**
   * Scanner consumer: one chunk between `low` and `high`, in small steps. The
   * replacements in it keep their ownership (unowned ones are adopted, panes
   * reconnected); owned places it no longer holds are forgotten; blocks that
   * need an exact vanilla neighbor keep it vanilla; vanilla blocks that show
   * are queued, with the blocks behind them.
   */
  function* scan(dimension, chunk, low, high) {
    if (!enabled || !byCustom.size) return;
    const dimensionId = dimension.id, x0 = chunk.x * 16, z0 = chunk.z * 16, seen = new Set();
    for (let bottom = low; bottom <= high; bottom += SECTION) {
      const min = { x: x0, y: bottom, z: z0 }, max = { x: x0 + 15, y: Math.min(high, bottom + SECTION - 1), z: z0 + 15 }, section = volumeOf(min, max);
      if (!customTypes.length || !dimension.containsBlock(section, { includeTypes: customTypes }, true)) { yield; continue; }
      const list = dimension.getBlocks(section, { includeTypes: customTypes }, true);
      yield;
      let reads = 0;
      for (const at of list.getBlockLocationIterator()) {
        const location = { x: at.x, y: at.y, z: at.z };
        seen.add(pack(location));
        if (!isOwned(dimensionId, location)) {
          // Owned at once, so a later swap-back finds it; its pattern is fixed once no player sees it.
          record(dimensionId, location, true);
          adopted++;
          enqueue(positionKey(dimensionId, location), { swap: 'adopt', dimension, location });
        }
        if (++reads % 16 === 0) yield;
      }
      if (paneTypes.length && dimension.containsBlock(section, { includeTypes: paneTypes }, true))
        for (const at of dimension.getBlocks(section, { includeTypes: paneTypes }, true).getBlockLocationIterator()) {
          refreshPane(getBlock(dimension, at));
          if (++reads % 4 === 0) yield;
        }
    }
    // Owned places this scan no longer found hold something else now (broken, moved, a command).
    const mine = owned.get(dimensionId + '|' + chunk.x + '|' + chunk.z);
    if (mine) for (const value of [...mine.positions]) {
      const location = unpack(mine.cx, mine.cz, value);
      if (location.y >= low && location.y <= high && !seen.has(value)) record(dimensionId, location, false);
    }
    yield;
    // A replacement a neighbor needs goes back (cocoa put next to it by a structure); a vanilla one stays.
    const needed = new Set();
    if (needyTypes.length) for (let bottom = low - 1; bottom <= high + 1; bottom += SECTION) {
      const area = new api.BlockVolume({ x: x0 - 1, y: bottom, z: z0 - 1 }, { x: x0 + 16, y: Math.min(high + 1, bottom + SECTION - 1), z: z0 + 16 });
      if (dimension.containsBlock(area, { includeTypes: needyTypes }, true))
        for (const at of dimension.getBlocks(area, { includeTypes: needyTypes }, true).getBlockLocationIterator()) {
          for (const rule of needyRules.get(getBlock(dimension, at)?.typeId) ?? []) for (const [dx, dy, dz] of rule.offsets) {
            const target = { x: at.x - dx, y: at.y - dy, z: at.z - dz }, block = getBlock(dimension, target);
            if (!block) continue;
            const custom = byCustom.get(block.typeId), key = positionKey(dimensionId, target);
            if (custom && rule.needs.has(custom.vanilla)) enqueue(key, { swap: 'out', dimension, location: target, shown: true, need: true });
            else if (rule.needs.has(block.typeId)) { needed.add(key); forget(key); }
          }
          yield;
        }
      yield;
    }
    if (!vanillaTypes.length) return;
    // Nobody waits for players to look away (the default): blocks whose replacement depends only on
    // their position are written in bulk, one native call per distinct tile, as the scan finds them.
    const writer = views.list.every(view => view.limit <= 0) ? createBulkWriter({ api, log }) : undefined;
    const swapNow = (location, key) => {
      const block = writer && getBlock(dimension, location), entry = block && byVanilla.get(block.typeId);
      if (!entry || !bulkable(entry)) return false;
      forget(key);
      writer.set(dimension, location, entry.block, positionStates(entry, location, {}));
      return true;
    };
    for (let bottom = low; bottom <= high; bottom += SECTION) {
      const min = { x: x0, y: bottom, z: z0 }, max = { x: x0 + 15, y: Math.min(high, bottom + SECTION - 1), z: z0 + 15 };
      // Nothing to swap, or nothing open near it (deep rock, open sky): the whole section is skipped.
      if (!dimension.containsBlock(volumeOf(min, max), { includeTypes: vanillaTypes }, true) ||
        !dimension.containsBlock(volumeOf(min, max, 2), { includeTypes: open }, true)) { yield; continue; }
      yield;
      for (let y = min.y; y <= max.y; y += CELL) for (let x = x0; x < x0 + 16; x += CELL) for (let z = z0; z < z0 + 16; z += CELL) {
        const cellMin = { x, y, z }, cellMax = { x: x + CELL - 1, y: Math.min(max.y, y + CELL - 1), z: z + CELL - 1 }, cell = volumeOf(cellMin, cellMax);
        if (!dimension.containsBlock(cell, { includeTypes: vanillaTypes }, true) ||
          !dimension.containsBlock(volumeOf(cellMin, cellMax, 2), { includeTypes: open }, true)) { yield; continue; }
        // What lets a block show (anything but an opaque full cube) and what can be swapped, around this cell.
        const shows = dimension.getBlocks(volumeOf(cellMin, cellMax, 1), { excludeTypes: solid }, true);
        yield;
        const swappable = dimension.getBlocks(volumeOf(cellMin, cellMax, 1), { includeTypes: vanillaTypes }, true);
        yield;
        let reads = 0;
        for (const at of swappable.getBlockLocationIterator()) {
          if (at.x < cellMin.x || at.x > cellMax.x || at.y < cellMin.y || at.y > cellMax.y || at.z < cellMin.z || at.z > cellMax.z) continue;
          const location = { x: at.x, y: at.y, z: at.z }, key = positionKey(dimensionId, location);
          if (needed.has(key) || !SIDES.some(side => shows.isInside(offset(location, side)))) { if (++reads % 8 === 0) yield; continue; }
          if (!swapNow(location, key)) enqueue(key, { swap: 'in', dimension, location, shown: true });
          // The blocks behind a shown block are swapped as well, so digging never uncovers a vanilla face.
          for (const side of SIDES) {
            const behind = offset(location, side), behindKey = positionKey(dimensionId, behind);
            if (needed.has(behindKey) || shows.isInside(behind) || !swappable.isInside(behind) || isOwned(dimensionId, behind)) continue;
            if (!swapNow(behind, behindKey)) enqueue(behindKey, { swap: 'in', dimension, location: behind });
          }
          if (++reads % 4 === 0) yield;
        }
      }
      if (writer?.pending) { finishBulk(writer); yield; }
    }
  }

  /** A replacement that is only its position's pattern: no mirrored vanilla states and no leaves to look after. */
  const bulkable = entry => !entry.broken && !entry.leafGuard && Object.keys(entry.mirror).length === 0;

  /** Sends a scan's bulk writes and does for each block what swapIn does for one. */
  function finishBulk(writer) {
    for (const { dimension, location } of writer.flush()) {
      swaps++;
      record(dimension.id, location, true);
      try { swapped(dimension, location); } catch (error) { log('after swap ' + String(error)); }
    }
  }

  /** Applies one queued change; returns the block changes it cost (logs cost more because of their leaf scan), or -1 for a log swap that has to wait. */
  function apply(item) {
    const block = getBlock(item.dimension, item.location);
    if (!block) return 0;
    if (item.swap === 'out') {
      if (!byCustom.has(block.typeId)) { record(item.dimension.id, item.location, false); return 0; }
      // A player came back into range, or the neighbor that needed the vanilla block is gone.
      if (enabled && ((item.far && held(item.dimension.id, item.location)) ||
        (item.need && !neededAt(item.dimension, item.location, byCustom.get(block.typeId).vanilla)))) return 0;
      revert(block);
      return 1;
    }
    if (item.swap === 'adopt') return byCustom.has(block.typeId) && adopt(block) ? 1 : 0;
    // A vanilla block where an ownership record says a replacement was (broken and placed again
    // before a rescan, or set by a command) is swapped like any other.
    const entry = byVanilla.get(block.typeId);
    if (!entry) return 0;
    // Each log swap can owe the leaves around it a few hundred update_bit clears: logs wait while those pile up.
    if (entry.leafGuard && leafReset.size > RESET_BACKLOG) return -1;
    swapIn(block);
    return entry.cost ?? 1;
  }

  /** Whether there is work this tick could do. */
  function busy() {
    const first = leafReset.values().next().value;
    return (first && first.tick <= system.currentTick) || urgent.size > 0 || queue.size > 0 || leaving.size > 0 ||
      (waiting.size > 0 && passStamp !== views.stamp);
  }

  /**
   * Works until `until` (a now() time) or maxSwapsPerTick block changes this
   * tick: update_bit clears owed to leaves, changes that waited while seen
   * (after a view changed, with at most half the time while other changes are
   * queued), uncovered blocks, the swap queue, then chunks players left.
   * Returns true while work remains.
   */
  function run(until) {
    if (!byCustom.size) return false;
    if (tickSeen !== system.currentTick) {
      tickSeen = system.currentTick; changes = 0;
      for (const [key, item] of parked) { parked.delete(key); queue.set(key, item); }
    }
    const limit = limits().maxSwapsPerTick;
    const over = (end = until) => now() >= end || changes >= limit;
    for (const [key, item] of leafReset) {
      if (item.tick > system.currentTick) break;
      if (over()) return true;
      leafReset.delete(key);
      changes += resetLeaf(item);
    }
    if (waiting.size && passStamp !== views.stamp) {
      const end = urgent.size || queue.size ? now() + (until - now()) / 2 : until;
      if (!pass) { pass = waiting.entries(); passStart = views.stamp; }
      for (;;) {
        if (over(end)) break;
        const next = pass.next();
        if (next.done) { pass = undefined; passStamp = passStart; break; }
        const [key, item] = next.value;
        if (visible(item)) continue;
        waiting.delete(key);
        const cost = apply(item);
        if (cost < 0) parked.set(key, item); else changes += cost;
      }
    }
    for (const source of [urgent, queue]) for (const [key, item] of source) {
      if (over()) return true;
      source.delete(key);
      if (visible(item)) { waiting.set(key, item); deferred++; continue; }
      const cost = apply(item);
      if (cost < 0) parked.set(key, item); else changes += cost;
    }
    for (const [name, leave] of leaving) {
      for (;;) {
        if (over()) return true;
        const next = leave.positions.next();
        if (next.done) { leaving.delete(name); break; }
        const location = unpack(leave.cx, leave.cz, next.value);
        if (enabled && held(leave.dimension.id, location)) continue;
        const item = { swap: 'out', dimension: leave.dimension, location, far: true };
        if (visible(item)) { waiting.set(positionKey(leave.dimension.id, location), item); deferred++; continue; }
        changes += apply(item);
      }
    }
    return busy();
  }

  /** Whether a pane or bars block would join its neighbor on one side. */
  const paneJoins = type => paneConnect.has(type) || solidSet.has(type);

  /** Works out a replaced pane's connections from its neighbors and writes them when they changed. */
  function refreshPane(block) {
    const entry = block && byCustom.get(block.typeId);
    if (!entry?.pane) return false;
    const states = block.permutation.getAllStates?.() ?? {}, next = { ...states };
    for (const [side, delta] of Object.entries(PANE_SIDES)) {
      const neighbor = getBlock(block.dimension, offset(block.location, delta));
      // Connection states are vanilla booleans mirrored as 0 or 1.
      if (neighbor) next[entry.pane[side]] = paneJoins(neighbor.typeId) ? 1 : 0;
    }
    if (Object.keys(PANE_SIDES).every(side => next[entry.pane[side]] === states[entry.pane[side]])) return false;
    try { block.setPermutation(resolve(block.typeId, next)); return true; }
    catch (error) { log('pane refresh ' + String(error)); return false; }
  }

  /** With swapBack (or while switched off): finds owned chunks no player is near any more (far away, or every block beyond the band) and lines them up to swap back. */
  function maintain() {
    const { chunkRadius, yBand, swapBack } = limits();
    const keys = [...owned.keys()];
    // Swapped blocks stay swapped unless swapBack is set; while switched off everything goes back.
    if (!keys.length || (enabled && !swapBack)) return;
    let steps = 0;
    for (; steps < Math.min(keys.length, MAINTAIN_CHUNKS); steps++) {
      const key = keys[(maintainCursor + steps) % keys.length], chunk = owned.get(key);
      if (!chunk || leaving.has(key)) continue;
      const near = players.filter(player => player.dimension.id === chunk.dimension &&
        Math.max(Math.abs(chunk.cx - Math.floor(player.location.x / 16)), Math.abs(chunk.cz - Math.floor(player.location.z / 16))) <= chunkRadius + 1);
      // Every owned block is within the band of one near player: nothing to look at.
      if (enabled && near.some(player => player.location.y - yBand - 8 <= chunk.low && chunk.high <= player.location.y + yBand + 8)) continue;
      let dimension;
      try { dimension = world.getDimension(chunk.dimension); } catch { continue; }
      let loaded = true;
      try { loaded = dimension.isChunkLoaded?.({ x: chunk.cx * 16, y: 0, z: chunk.cz * 16 }) ?? true; } catch { loaded = false; }
      if (!loaded) continue;
      leaving.set(key, { dimension, cx: chunk.cx, cz: chunk.cz, positions: chunk.positions.values() });
    }
    maintainCursor = (maintainCursor + Math.max(1, steps)) % Math.max(1, keys.length);
  }

  function readParts(key) {
    let text = '';
    for (let part = 0; ; part++) {
      const value = world.getDynamicProperty('bct:replace:' + key + ':' + part);
      if (typeof value !== 'string') break;
      text += value;
    }
    return text;
  }

  function readIndex() {
    try { const keys = JSON.parse(world.getDynamicProperty(INDEX) ?? '[]'); return Array.isArray(keys) ? keys.filter(key => typeof key === 'string') : []; }
    catch { return []; }
  }

  /** Writes changed chunk records and the chunk index. */
  function flush() {
    if (!dirty.size) return;
    const index = new Set(readIndex());
    for (const key of dirty) {
      for (let part = 0; world.getDynamicProperty('bct:replace:' + key + ':' + part) !== undefined; part++)
        world.setDynamicProperty('bct:replace:' + key + ':' + part, undefined);
      const chunk = owned.get(key);
      if (!chunk?.positions.size) { index.delete(key); continue; }
      const text = [...chunk.positions].map(value => value.toString(36)).join(',');
      for (let part = 0; part * PART_SIZE < text.length; part++)
        world.setDynamicProperty('bct:replace:' + key + ':' + part, text.slice(part * PART_SIZE, (part + 1) * PART_SIZE));
      index.add(key);
    }
    world.setDynamicProperty(INDEX, index.size ? JSON.stringify([...index]) : undefined);
    dirty.clear();
  }

  /** Loads what a previous session owned. Blocks still in range stay swapped; maintenance swaps back the rest. */
  function recover() {
    if (recovered) return;
    recovered = true;
    for (const key of readIndex()) {
      const [dimension, cx, cz] = key.split('|');
      const chunk = { dimension, cx: Number(cx), cz: Number(cz), positions: new Set() };
      if (!Number.isInteger(chunk.cx) || !Number.isInteger(chunk.cz)) continue;
      for (const item of readParts(key).split(',')) {
        const value = parseInt(item, 36);
        if (Number.isInteger(value) && value >= 0) { chunk.positions.add(value); spans(chunk, Math.floor(value / 256) - 2048); }
      }
      if (chunk.positions.size) owned.set(key, chunk);
    }
  }

  /** Forgets every queued and waiting change. */
  function clearQueues() { urgent.clear(); queue.clear(); waiting.clear(); parked.clear(); leaving.clear(); pass = undefined; passStamp = -1; }

  /**
   * A block was broken or blown up: its neighbors now show. They and the blocks
   * backing them are swapped (hidden ones always, shown ones while no player
   * sees them) so the hole never shows a vanilla face: right away for a player
   * digging (`immediate`, at most a few dozen blocks), first thing in the next
   * tick's work for explosions, which report many blocks at once. The chunk is
   * rescanned for the rest.
   */
  function revealed(dimension, location, { immediate = false } = {}) {
    if (!enabled || !byVanilla.size) return;
    let budget = REVEAL_BUDGET;
    const urge = (at, shown) => {
      const key = positionKey(dimension.id, at);
      if (waiting.has(key)) return;
      queue.delete(key);
      const item = { swap: 'in', dimension, location: at, shown };
      if (!immediate) { urgent.set(key, item); return; }
      if (visible(item)) { waiting.set(key, item); deferred++; return; }
      if (apply(item) < 0) parked.set(key, item);
    };
    for (const side of SIDES) {
      const neighbor = offset(location, side), block = getBlock(dimension, neighbor);
      if (!block) continue;
      if (byVanilla.has(block.typeId) && budget-- > 0) urge(neighbor, true);
      if (!solidSet.has(block.typeId)) continue;
      for (const deeper of SIDES) {
        const behind = offset(neighbor, deeper);
        if (behind.x === location.x && behind.y === location.y && behind.z === location.z) continue;
        const next = getBlock(dimension, behind);
        if (next && byVanilla.has(next.typeId) && budget-- > 0) urge(behind);
      }
    }
    rescan(dimension, location);
  }

  /** playerInteractWithBlock before-event: an axe used on a replaced log strips it on the next tick (the world is read-only here). */
  function interact(event) {
    const { block, player, itemStack } = event ?? {};
    if (event?.isFirstEvent === false || !block || !player) return;
    const entry = byCustom.get(block.typeId);
    if (!entry?.strip || !itemStack?.hasTag?.('minecraft:is_axe')) return;
    const { dimension, location } = block;
    system.run(() => strip({ block: getBlock(dimension, location), dimension, player }));
  }

  /** An axe on a replaced log strips it like vanilla, straight to the stripped block's replacement when there is one. */
  function strip({ block, dimension, player }) {
    const entry = block && byCustom.get(block.typeId);
    if (!entry?.strip || !player) return;
    let item;
    try { item = player.getComponent('minecraft:equippable')?.getEquipment('Mainhand'); } catch { return; }
    if (!item?.hasTag?.('minecraft:is_axe')) return;
    const states = vanillaStates(entry, block), target = byVanilla.get(entry.strip);
    try {
      if (target && !target.broken) {
        block.setPermutation(replacementOf(target, block, states));
        record(dimension.id, block.location, true);
      } else {
        block.setPermutation(resolve(entry.strip, states));
        record(dimension.id, block.location, false);
      }
    } catch (error) { log('strip ' + String(error)); return; }
    try { dimension.playSound(entry.strip.includes('stem') || entry.strip.includes('hyphae') ? 'use.stem' : 'use.wood', block.location); } catch { /* no sound */ }
    damage(player, item);
  }

  /** One point of durability, with Unbreaking's chance to keep it, like vanilla tool use; survival players only. */
  function damage(player, item) {
    try {
      if (String(player.getGameMode?.()).toLowerCase() === 'creative') return;
      const durability = item.getComponent('minecraft:durability');
      if (!durability) return;
      const unbreaking = item.getComponent('minecraft:enchantable')?.getEnchantment('unbreaking')?.level ?? 0;
      if (Math.random() * (unbreaking + 1) >= 1) return;
      const equipment = player.getComponent('minecraft:equippable');
      if (durability.damage + 1 >= durability.maxDurability) { equipment.setEquipment('Mainhand', undefined); return; }
      durability.damage += 1;
      equipment.setEquipment('Mainhand', item);
    } catch (error) { log('tool damage ' + String(error)); }
  }

  /** Experience a broken replaced ore gives (vanilla range): only for the right tool, never for Silk Touch or in creative. */
  function experience({ brokenBlockPermutation, itemStackBeforeBreak, player, dimension, block }) {
    const entry = byCustom.get(brokenBlockPermutation?.type?.id);
    if (!entry?.xp) return 0;
    try {
      if (String(player?.getGameMode?.()).toLowerCase() === 'creative') return 0;
      if (itemStackBeforeBreak?.getComponent?.('minecraft:enchantable')?.getEnchantment('silk_touch')) return 0;
      const has = tag => Boolean(itemStackBeforeBreak?.hasTag?.(tag));
      if (entry.tool && (!entry.tool.all.every(has) || (entry.tool.any.length && !entry.tool.any.some(has)))) return 0;
    } catch { return 0; }
    const [low, high] = entry.xp, amount = low + Math.floor(Math.random() * (high - low + 1));
    const center = { x: block.location.x + 0.5, y: block.location.y + 0.5, z: block.location.z + 0.5 };
    for (let orb = 0; orb < amount; orb++) try { dimension.spawnEntity('minecraft:xp_orb', center); } catch { break; }
    return amount;
  }

  return {
    get size() { let total = 0; for (const chunk of owned.values()) total += chunk.positions.size; return total; },
    get status() {
      return { blocks: byCustom.size, owned: this.size, queued: urgent.size + queue.size, waiting: waiting.size, leaving: leaving.size,
        leafResetsPending: leafReset.size, swaps, reverts, adopted, deferred, leafResets, enabled, broken: [...broken] };
    },
    setSource(provider, data) { sources.set(provider, validate(data)); rebuild(); },
    /** Re-reads the extra open types (converted leaves) after they change. */
    refreshTypes: rebuild,
    isReplacement: typeId => byCustom.has(typeId),
    /** Whether a block type is an opaque full cube (vanilla or replacement) that hides the faces next to it. */
    isSolid,
    /** The vanilla block a replacement stands for (undefined for other blocks). */
    vanillaOf: typeId => byCustom.get(typeId)?.vanilla,
    /** The vanilla states a replacement stands for (undefined for other blocks). */
    vanillaStatesOf: block => { const entry = byCustom.get(block?.typeId); return entry ? vanillaStates(entry, block) : undefined; },
    /** Replacement block types standing for a vanilla block. */
    customsOf: typeId => { const entry = byVanilla.get(typeId); return entry ? [entry.block] : []; },
    blockTypes: () => [...vanillaTypes, ...customTypes],
    scan, run,
    /** Bookkeeping once a tick: player views, ownership from the last session, and every 10 ticks chunks players left and saving. */
    tick(current, maintenance) {
      players = current;
      views.update(current);
      recover();
      // Without pack data nothing can be mapped back yet; keep ownership until it arrives.
      if (!byCustom.size) return;
      if (maintenance) { maintain(); flush(); }
    },
    /** While disabled nothing is swapped in and maintenance swaps every owned block back (once no player sees it). */
    setEnabled(value) { enabled = value; clearQueues(); },
    flush,
    revert, revertAt, refreshPane, revealed, interact, strip, experience,
    /**
     * A player placed a block: a replaceable one is swapped at once, in the same tick, so it never
     * shows its vanilla look first (with waiting while seen switched on, the scan swaps it instead).
     */
    placed(block) {
      if (!enabled || !block || !byVanilla.has(block.typeId)) return false;
      // Waiting while seen needs this tick's views, which the tick reads later: the scan swaps it then.
      if (limits().farDistance > 0) return false;
      const key = positionKey(block.dimension.id, block.location);
      queue.delete(key); urgent.delete(key);
      const item = { swap: 'in', dimension: block.dimension, location: { ...block.location }, shown: true };
      const cost = apply(item);
      if (cost < 0) parked.set(key, item);
      return cost > 0;
    },
    /** A block changed next to these positions: panes around it reconnect. */
    changed(block) {
      for (const delta of Object.values(PANE_SIDES)) {
        const neighbor = getBlock(block.dimension, offset(block.location, delta));
        if (neighbor && byCustom.get(neighbor.typeId)?.pane) refreshPane(neighbor);
      }
    },
  };
}
