/**
 * Leaves drawn with the author's Java models (converter/model_blocks.py).
 *
 * Every vanilla leaf in the loaded chunks around players becomes the converted
 * pack's leaf block, so whole forests change together. A chunk is converted
 * only while the eight chunks around it are loaded: the ring at the edge of the
 * loaded area stays vanilla, so the line between converted and vanilla leaves
 * lies outside the chunks that tick. Converted leaves stay converted when
 * players leave: replacement logs stay too, and vanilla leaves don't count
 * those as logs.
 *
 * Chunks are found as they load, nearest to a player first (each player's
 * search starts again from their own chunk whenever they enter another), and
 * convert in the order views.mjs ranks them. A chunk converts all at once, in
 * bulk: its vanilla leaves are found by their exact states and every group of
 * leaves with the same converted states is written by one fillBlocks call
 * (bulk.mjs). The work runs in small steps in `run(until)`, within the engine's
 * shared time budget.
 *
 * Optionally (replace.farDistance above 0) a vanilla leaf that shows and that
 * some player could see waits, kept per 16-block section, and converts once no
 * player could; leaves then convert one by one. Converted leaves change their
 * look (distance group), update_bit and persistence whenever they need to:
 * those are not swaps.
 *
 * A converted leaf carries its vanilla persistent_bit and update_bit (as 0 or
 * 1), the Java model choice for its position (Java's model random) and, when
 * the author's model depends on it, the Java log distance group.
 *
 * Gameplay stays vanilla Bedrock:
 * - breaking works on the converted block; drops come from its loot table
 *   (shears, Fortune). Silk Touch drops the custom block's own item, which is
 *   turned into the vanilla leaf item; with shears, only the leaf item drops.
 * - decay: removing a log marks the converted leaves within 4 blocks, removing
 *   a leaf those within 1 block (update_bit); on a random tick a marked leaf
 *   decays with vanilla drops when no log is within 4 blocks through leaves
 *   (Bedrock's distance), and is unmarked otherwise. A random tick sometimes
 *   checks an unmarked leaf too and marks it when no log is within 6 blocks,
 *   which catches logs that went without an event (fire, commands). A leaf
 *   whose search reaches an unloaded chunk before a log is left as it is.
 * - the Java distance (through leaves to a log, at most 6, 7 for none) picks
 *   the model the author made for it; it is worked out per chunk on conversion
 *   and again around every change to logs and leaves.
 * Vanilla leaves next to converted ones that stay vanilla (at the edge, or
 * waiting for players to look away) have update_bit cleared again when a
 * conversion set it.
 */
import { createBulkWriter, setKeepingWater } from './bulk.mjs';
import { javaModelIndex } from './tiles.mjs';
import { NO_VIEWS, showsAt } from './views.mjs';

const INDEX = 'bct:leaves:index';
const PART_SIZE = 30000;
const SIDES = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]];
const NEIGHBOR_CHUNKS = [[-1, -1], [-1, 0], [-1, 1], [0, -1], [0, 1], [1, -1], [1, 0], [1, 1]];
const MAX_SEARCH = 400;
// A distance search's result when no model depends on distances: no leaves known, every distance 7.
const NO_DISTANCES = Object.freeze({ leaves: new Set(), list: [], result: new Map() });
// Leaves that generate in water (mangrove swamps) convert one by one, which keeps their water; the rest in bulk.
const KEEPS_WATER = new Set(['minecraft:mangrove_leaves']);
// Layers of a chunk column read at a time when looking for leaves, and of a distance search box.
const SLAB = 32, SEARCH_SLAB = 16;
// Offsets looked at per probe at most: chunks already converted are passed over without asking the game.
const PROBE_CHECKS = 8;
// Chunk records looked at per pass for players having left them, and queued chunks ranked again.
const CLEANUP_CHUNKS = 512, RERANK_CHUNKS = 128;
// Chunks ready to convert wait in buckets of BUCKET rank units (views.rank: about a block each), lowest first.
const BUCKET = 16, BUCKETS = 256;
// Ticks between two looks at the leaves that wait (while views keep changing, as in flight).
const WAIT_PASS_TICKS = 10;
// Layers above and below a player looked at again for new vanilla leaves (grown trees, structures).
const RECHECK_BAND = 32;

const key = (x, y, z) => x + ',' + y + ',' + z;
const chunkKey = (dimensionId, cx, cz) => dimensionId + '|' + cx + '|' + cz;
const chunkOf = value => Math.floor(value / 16);
const sectionKey = (dimensionId, at) => dimensionId + '|' + chunkOf(at.x) + '|' + chunkOf(at.y) + '|' + chunkOf(at.z);

/** Chunk offsets ring by ring (nearest first); starts[ring] is where each ring begins. */
function spiral(radius) {
  const offsets = [];
  for (let x = -radius; x <= radius; x++) for (let z = -radius; z <= radius; z++) offsets.push([x, z]);
  offsets.sort((a, b) => Math.max(Math.abs(a[0]), Math.abs(a[1])) - Math.max(Math.abs(b[0]), Math.abs(b[1])) || a[0] * a[0] + a[1] * a[1] - b[0] * b[0] - b[1] * b[1]);
  const starts = [];
  offsets.forEach(([x, z], index) => { const ring = Math.max(Math.abs(x), Math.abs(z)); if (starts[ring] === undefined) starts[ring] = index; });
  starts.push(offsets.length);
  return { offsets, starts };
}

/** The smallest box holding every location. */
function bounds(locations) {
  const min = { x: Infinity, y: Infinity, z: Infinity }, max = { x: -Infinity, y: -Infinity, z: -Infinity };
  for (const at of locations) {
    if (at.x < min.x) min.x = at.x; if (at.y < min.y) min.y = at.y; if (at.z < min.z) min.z = at.z;
    if (at.x > max.x) max.x = at.x; if (at.y > max.y) max.y = at.y; if (at.z > max.z) max.z = at.z;
  }
  return { min, max };
}

function validate(data) {
  if (!data || !Array.isArray(data.blocks) || !Array.isArray(data.leaves) || !Array.isArray(data.logs) || typeof data.decay !== 'object')
    throw new Error('Invalid leaf data');
  const id = /^[a-z0-9_.-]+:[a-z0-9_./-]+$/;
  for (const entry of data.blocks) {
    if (!id.test(entry.vanilla) || !id.test(entry.block) || typeof entry.mirror !== 'object') throw new Error('Invalid leaf block');
    if (entry.turn && (typeof entry.turn.state !== 'string' || !Array.isArray(entry.turn.weights) ||
      (entry.turn.byLook !== undefined && (!entry.look || !Array.isArray(entry.turn.byLook) || !entry.turn.byLook.every(Array.isArray)))))
      throw new Error('Invalid leaf turn');
    if (entry.look && (typeof entry.look.state !== 'string' || !Array.isArray(entry.look.table) || entry.look.table.length !== 2)) throw new Error('Invalid leaf look');
  }
  return data;
}

/**
 * api: @minecraft/server objects. limits(): the `leaves` settings.
 * logAliases(type): replacement block types that stand for a log (they count as logs).
 * views: where players look and travel (views.mjs, shared with the replacement blocks); without it no leaf waits.
 * solid(type): whether a block type is an opaque full cube, which hides a leaf behind it.
 */
export function createLeaves({ api, limits, log = () => {}, logAliases = () => [], views = NO_VIEWS, solid = () => false }) {
  const { world, system } = api;
  // Milliseconds for the time budget (tests pass their own clock as api.now).
  const now = () => (api.now ?? Date.now)();
  const sources = new Map();
  let byVanilla = new Map(), byCustom = new Map(), leafTypes = [], logTypes = [], leafSet = new Set(), logSet = new Set(), needsDistance = false;
  let decay = { bedrockDistance: 4, javaDistance: 6, logRadius: 4, leafRadius: 1 };
  const chunks = new Map();      // chunk key -> { dimension, cx, cz, done, ready, checkedAt } chunks players are near
  const queued = new Map();      // chunk key -> bucket: chunks ready to convert, in `buckets` by rank
  const buckets = Array.from({ length: BUCKETS }, () => new Set());
  const rechecks = new Set();    // chunk keys of converted chunks to look at again for new vanilla leaves
  const owned = new Map();       // chunk key -> { dimension (id), cx, cz }
  const cursors = new Map();     // player id -> { index, reach, farthest } where each player's probe goes on
  const work = [];               // queued generators (area refreshes, decay marks, waiting leaves, swap-backs)
  const resets = new Map();      // position key -> { dimension, location, tick } vanilla leaves whose update_bit a conversion set
  const sheared = new Map();     // position key -> tick: leaves broken with shears this tick
  const recent = new Map();      // position key -> tick of the last removal handled there
  const waiting = new Map();     // section key -> { dimension, cx, cy, cz, at: Map<position key, location> } leaves a player could see convert
  const permutations = new Map();
  let sweep = spiral(0), radius = -1, lowest = BUCKETS, enabled = true, recovered = false, dirty = false, players = [];
  let runner, cleanup, rerank, turn = 0, waitingCount = 0, checkedStamp = -1, rechecking = false, lastWaitPass = -Infinity;
  const stats = { swaps: 0, looks: 0, decays: 0, marked: 0, unmarked: 0, reverts: 0, cleared: 0, deferred: 0 };

  function rebuild() {
    byVanilla = new Map(); byCustom = new Map();
    const leaves = new Set(), logs = new Set();
    for (const data of sources.values()) {
      for (const entry of data.blocks) { if (!byVanilla.has(entry.vanilla)) byVanilla.set(entry.vanilla, entry); byCustom.set(entry.block, entry); }
      data.leaves.forEach(type => leaves.add(type));
      data.logs.forEach(type => logs.add(type));
      decay = { ...decay, ...data.decay };
    }
    for (const entry of byCustom.values()) leaves.add(entry.block);
    for (const type of [...logs]) for (const alias of logAliases(type)) logs.add(alias);
    leafTypes = [...leaves]; logTypes = [...logs]; leafSet = leaves; logSet = logs;
    needsDistance = [...byVanilla.values()].some(entry => entry.look);
    for (const [name, record] of chunks) { record.done = false; if (record.ready) enqueueChunk(name, record); }
  }

  const resolve = (type, states) => {
    const name = type + JSON.stringify(states);
    let permutation = permutations.get(name);
    if (!permutation) {
      permutation = api.BlockPermutation.resolve(type, states);
      if (permutations.size > 20000) permutations.clear();
      permutations.set(name, permutation);
    }
    return permutation;
  };
  const getBlock = (dimension, location) => { try { return dimension.getBlock(location); } catch { return undefined; } };
  const loaded = (dimension, cx, cz) => { try { return dimension.isChunkLoaded({ x: cx * 16, y: 0, z: cz * 16 }); } catch { return false; } };

  /** The model group a Java distance picks (1-6, 7 for none) for persistent or decaying leaves. */
  const lookOf = (entry, persistent, distance) => entry.look.table[persistent ? 1 : 0][Math.min(Math.max(distance, 1), 7) - 1];
  /** The weighted model pick for a position, with the weights of the leaf's look when each look has its own. */
  const turnOf = (entry, location, look) =>
    javaModelIndex(location, entry.turn.byLook?.[look] ?? entry.turn.weights, entry.turn.selection);
  /** A converted leaf's states with a new look, and the model pick that look's weights give. */
  function withLook(entry, block, states, look) {
    const next = { ...states, [entry.look.state]: look };
    if (entry.turn?.byLook) next[entry.turn.state] = turnOf(entry, block.location, look);
    return next;
  }

  /** Converted states for a vanilla leaf with these vanilla states at a location; boolean vanilla states become 0 or 1. */
  function convertedStates(entry, vanilla, location, distance) {
    const states = {};
    for (const [name, mirrored] of Object.entries(entry.mirror)) if (name in vanilla) states[mirrored] = vanilla[name] === true || vanilla[name] === 1 ? 1 : 0;
    if (entry.look) states[entry.look.state] = lookOf(entry, states[entry.mirror.persistent_bit] === 1, distance);
    if (entry.turn) states[entry.turn.state] = turnOf(entry, location, states[entry.look?.state]);
    return states;
  }

  /** Converted permutation for a vanilla leaf. */
  function convertedOf(entry, block, distance) {
    return resolve(entry.block, convertedStates(entry, block.permutation.getAllStates?.() ?? {}, block.location, distance));
  }

  /** Every combination of a vanilla leaf's boolean states (persistent_bit, update_bit), to find leaves by exact state. */
  function stateCombinations(entry) {
    let combinations = [{}];
    for (const name of Object.keys(entry.mirror))
      combinations = combinations.flatMap(states => [{ ...states, [name]: false }, { ...states, [name]: true }]);
    return combinations;
  }

  /** The vanilla permutation a converted leaf stands for. */
  function vanillaOf(entry, block) {
    const current = block.permutation.getAllStates?.() ?? {}, states = {};
    for (const [name, mirrored] of Object.entries(entry.mirror)) if (mirrored in current) states[name] = current[mirrored] === 1 || current[mirrored] === true;
    return resolve(entry.vanilla, states);
  }

  function own(dimensionId, cx, cz) {
    const name = chunkKey(dimensionId, cx, cz);
    if (!owned.has(name)) { owned.set(name, { dimension: dimensionId, cx, cz }); dirty = true; }
  }
  function disown(dimensionId, cx, cz) { if (owned.delete(chunkKey(dimensionId, cx, cz))) dirty = true; }

  /** Converts or refreshes one leaf: vanilla leaves with a model block are swapped in, converted ones get their distance group. */
  function apply(block, distance) {
    const custom = byCustom.get(block.typeId);
    if (custom) {
      if (!custom.look) return false;
      const states = block.permutation.getAllStates?.() ?? {};
      const look = lookOf(custom, states[custom.mirror.persistent_bit] === 1, distance);
      if (states[custom.look.state] === look) return false;
      try { block.setPermutation(resolve(block.typeId, withLook(custom, block, states, look))); stats.looks++; return true; }
      catch (error) { log('leaf look ' + String(error)); return false; }
    }
    const entry = byVanilla.get(block.typeId);
    if (!entry || entry.broken || !enabled) return false;
    let permutation;
    try { permutation = convertedOf(entry, block, distance); }
    catch (error) { entry.broken = true; log('leaf block ' + entry.block + ' switched off: ' + String(error)); return false; }
    try { setKeepingWater(block, permutation); stats.swaps++; return true; }
    catch (error) { log('leaf swap ' + String(error)); return false; }
  }

  // ---- chunks ready to convert, by how soon they matter ----
  function bucketOf(record) {
    const rank = views.rank(record.dimension.id, record.cx, record.cz, true);
    return Math.max(0, Math.min(BUCKETS - 1, Math.floor(rank / BUCKET) || 0));
  }
  function enqueueChunk(name, record) {
    const bucket = bucketOf(record), old = queued.get(name);
    if (old === bucket) return;
    if (old !== undefined) buckets[old].delete(name);
    buckets[bucket].add(name);
    queued.set(name, bucket);
    if (bucket < lowest) lowest = bucket;
  }
  function dequeueChunk(name) {
    const bucket = queued.get(name);
    if (bucket === undefined) return;
    buckets[bucket].delete(name);
    queued.delete(name);
  }

  /** The ready chunk that matters soonest: the first of the lowest bucket, whose rank is checked again as it is taken. */
  function pick() {
    let moves = 0;
    for (; lowest < BUCKETS; lowest++) {
      for (const name of buckets[lowest]) {
        const record = chunks.get(name);
        if (!record || record.done || !record.ready) { dequeueChunk(name); continue; }
        const bucket = bucketOf(record);
        // Players moved on since it was ranked: it waits in its new place (a few per pick).
        if (bucket > lowest && moves++ < 32) { buckets[lowest].delete(name); buckets[bucket].add(name); queued.set(name, bucket); continue; }
        dequeueChunk(name);
        return record;
      }
    }
    return undefined;
  }

  /**
   * Java distances (1-6, absent beyond) from logs through leaves inside a box, in
   * small steps. Returns { leaves: Set of position keys, list: [{x, y, z, name}], result: Map name -> distance }.
   */
  function* distanceSearch(dimension, from, to) {
    const leaves = new Set(), list = [], result = new Map(), slabs = [];
    for (let bottom = from.y; bottom <= to.y; bottom += SEARCH_SLAB) slabs.push({ bottom, top: Math.min(to.y, bottom + SEARCH_SLAB - 1), leaves: false });
    const volumeOf = slab => new api.BlockVolume({ x: from.x, y: slab.bottom, z: from.z }, { x: to.x, y: slab.top, z: to.z });
    let count = 0;
    for (const slab of slabs) {
      const found = dimension.getBlocks(volumeOf(slab), { includeTypes: leafTypes }, true);
      yield;
      for (const at of found.getBlockLocationIterator()) {
        const name = key(at.x, at.y, at.z);
        leaves.add(name); list.push({ x: at.x, y: at.y, z: at.z, name });
        slab.leaves = true;
        if (++count % 256 === 0) yield;
      }
    }
    if (!leaves.size) return { leaves, list, result };
    let frontier = [];
    // Logs next to a leaf: in a slab with leaves or next to one.
    for (let index = 0; index < slabs.length; index++) {
      if (!slabs[index].leaves && !slabs[index - 1]?.leaves && !slabs[index + 1]?.leaves) continue;
      const logs = dimension.getBlocks(volumeOf(slabs[index]), { includeTypes: logTypes }, true);
      yield;
      for (const at of logs.getBlockLocationIterator()) {
        for (const [dx, dy, dz] of SIDES) {
          const name = key(at.x + dx, at.y + dy, at.z + dz);
          if (leaves.has(name) && !result.has(name)) { result.set(name, 1); frontier.push([at.x + dx, at.y + dy, at.z + dz]); }
        }
        if (++count % 128 === 0) yield;
      }
    }
    for (let distance = 2; distance <= decay.javaDistance && frontier.length; distance++) {
      const next = [];
      for (const [x, y, z] of frontier) {
        for (const [dx, dy, dz] of SIDES) {
          const name = key(x + dx, y + dy, z + dz);
          if (leaves.has(name) && !result.has(name)) { result.set(name, distance); next.push([x + dx, y + dy, z + dz]); }
        }
        if (++count % 128 === 0) yield;
      }
      frontier = next;
    }
    return { leaves, list, result };
  }

  /** distanceSearch all at once, for small boxes. */
  function distances(dimension, from, to) {
    const search = distanceSearch(dimension, from, to);
    for (;;) { const step = search.next(); if (step.done) return step.value; }
  }

  /** distanceSearch over a box around some locations, reaching as far as a Java distance does. */
  function searchAround(dimension, locations, grow = 0) {
    const { min, max } = bounds(locations), range = dimension.heightRange, reach = decay.javaDistance + grow;
    return distanceSearch(dimension, { x: min.x - reach, y: Math.max(range.min, min.y - reach), z: min.z - reach },
      { x: max.x + reach, y: Math.min(range.max - 1, max.y + reach), z: max.z + reach });
  }

  /** Whether turning the leaf at a location into its model would be seen: a player could see it and it shows (leaves around it let it show). */
  function seen(dimension, at, leaves) {
    if (!views.sees(dimension.id, at)) return false;
    if (leaves && SIDES.some(([dx, dy, dz]) => leaves.has(key(at.x + dx, at.y + dy, at.z + dz)))) return true;
    return showsAt(dimension, at, solid);
  }

  /** Keeps a vanilla leaf a player could see for when nobody can (per section). */
  function hold(dimension, at) {
    const name = sectionKey(dimension.id, at), position = key(at.x, at.y, at.z);
    let section = waiting.get(name);
    if (!section) { section = { dimension, cx: chunkOf(at.x), cy: chunkOf(at.y), cz: chunkOf(at.z), at: new Map() }; waiting.set(name, section); }
    if (section.at.has(position)) return;
    section.at.set(position, { x: at.x, y: at.y, z: at.z });
    waitingCount++; stats.deferred++;
  }

  /** Nothing waits at a location any more (converted, gone, or never waited). */
  function release(dimension, at) {
    const name = sectionKey(dimension.id, at), section = waiting.get(name);
    if (!section?.at.delete(key(at.x, at.y, at.z))) return;
    waitingCount--;
    if (!section.at.size) waiting.delete(name);
  }
  const isWaiting = (dimensionId, at) => waiting.get(sectionKey(dimensionId, at))?.at.has(key(at.x, at.y, at.z)) ?? false;

  function clearWaiting() { waiting.clear(); waitingCount = 0; rechecking = false; checkedStamp = -1; }

  /** Clears update_bit again, on the next tick, on vanilla leaves that stay vanilla next to a conversion. */
  function keepUpdateBits(dimension, places) {
    for (const at of places) {
      const name = dimension.id + '|' + key(at.x, at.y, at.z);
      // Deleted first so the map stays in the order the clears fall due.
      resets.delete(name);
      resets.set(name, { dimension, location: at, tick: system.currentTick + 1 });
    }
  }

  /**
   * Turns the vanilla leaves among `positions` into their models where no player could see it and gives
   * converted leaves the model for their distance (`found`, from distanceSearch over a box holding the
   * positions and their neighbors); a vanilla leaf a player could see waits (hold). A conversion marks the
   * vanilla leaves around it for a decay check (update_bit): those that stay vanilla (waiting, or next to
   * the positions) get it cleared again on the next tick when it was clear before. Returns whether any of
   * the positions holds a converted leaf afterwards.
   */
  function* convertAll(dimension, positions, found) {
    // The ones that wait are picked before anything changes, so their update_bit is still their own.
    const stay = [], later = new Set(), mine = new Set();
    let count = 0;
    for (const at of positions) {
      const name = key(at.x, at.y, at.z);
      mine.add(name);
      if (++count % 32 === 0) yield;
      if (!seen(dimension, at, found.leaves)) continue;
      const block = getBlock(dimension, at);
      if (!block || !byVanilla.has(block.typeId)) continue;
      later.add(name);
      hold(dimension, at);
      if (block.permutation.getState?.('update_bit') === false) stay.push(at);
    }
    // Vanilla leaves just around the positions keep update_bit as well.
    const { min, max } = bounds(positions);
    for (const at of found.list) {
      if (++count % 64 === 0) yield;
      if (at.x < min.x - 1 || at.x > max.x + 1 || at.y < min.y - 1 || at.y > max.y + 1 || at.z < min.z - 1 || at.z > max.z + 1 || mine.has(at.name)) continue;
      const block = getBlock(dimension, at);
      if (block && byVanilla.has(block.typeId) && block.permutation.getState?.('update_bit') === false) stay.push({ x: at.x, y: at.y, z: at.z });
    }
    yield;
    let custom = false;
    for (const at of positions) {
      if (++count % 4 === 0) yield;
      const name = key(at.x, at.y, at.z);
      if (later.has(name)) continue;
      const block = getBlock(dimension, at);
      const vanilla = Boolean(block) && byVanilla.has(block.typeId);
      // The work can run over several ticks: a leaf that came into view since waits after all.
      if (vanilla && seen(dimension, at, found.leaves)) {
        hold(dimension, at);
        if (block.permutation.getState?.('update_bit') === false) stay.push(at);
        continue;
      }
      // Converted, gone, or a leaf that cannot convert: nothing waits here any more.
      release(dimension, at);
      if (!block) continue;
      if (apply(block, found.result.get(name) ?? 7) && vanilla) own(dimension.id, chunkOf(at.x), chunkOf(at.z));
      if (byCustom.has(block.typeId)) custom = true;
    }
    keepUpdateBits(dimension, stay);
    return custom;
  }

  /** Converts every leaf of one chunk no player could see change, refreshing distance groups; the others wait. */
  function* convertChunk(record) {
    const { dimension, cx, cz } = record, x0 = cx * 16, z0 = cz * 16, range = dimension.heightRange;
    try {
      // The layers holding leaves, found without reading a single block.
      let low, high;
      for (let bottom = range.min; bottom < range.max; bottom += SLAB) {
        const top = Math.min(range.max - 1, bottom + SLAB - 1);
        const slab = new api.BlockVolume({ x: x0, y: bottom, z: z0 }, { x: x0 + 15, y: top, z: z0 + 15 });
        if (dimension.containsBlock(slab, { includeTypes: leafTypes }, true)) { low ??= bottom; high = top; }
        yield;
      }
      if (low === undefined) { disown(dimension.id, cx, cz); return; }
      const box = { min: { x: x0, y: low, z: z0 }, max: { x: x0 + 15, y: high, z: z0 + 15 } };
      let converted;
      if (views.list.every(view => view.limit <= 0)) {
        // Nobody waits for players to look away (the default): the whole chunk changes at once, in bulk.
        // Distances through leaves are only worked out when a model depends on them.
        const found = needsDistance ? yield* searchAround(dimension, [box.min, box.max]) : NO_DISTANCES;
        converted = yield* convertInBulk(dimension, box, found);
      } else {
        const inside = [];
        let count = 0;
        const volume = new api.BlockVolume(box.min, box.max);
        for (const at of dimension.getBlocks(volume, { includeTypes: leafTypes }, true).getBlockLocationIterator()) {
          inside.push({ x: at.x, y: at.y, z: at.z });
          if (++count % 128 === 0) yield;
        }
        converted = yield* convertAll(dimension, inside, yield* searchAround(dimension, [box.min, box.max]));
      }
      if (converted) own(dimension.id, cx, cz);
    } catch (error) {
      // An unloaded chunk or a refused query: the chunk is looked at again once it is ready.
      record.done = false;
      log('leaf chunk ' + String(error));
    }
  }

  /**
   * Converts every vanilla leaf in a box with a few native calls: leaves are found by their exact vanilla
   * states (no block is read one by one), each gets its model choice and distance group, and all leaves
   * with the same converted states are written by one fillBlocks call. Leaves that are often waterlogged
   * (keepsWater: mangrove) go one by one, which keeps their water. Vanilla leaves just outside the box
   * keep their update_bit, as in convertAll. Returns whether the box holds converted leaves afterwards.
   */
  function* convertInBulk(dimension, box, found) {
    const volume = new api.BlockVolume(box.min, box.max), writer = createBulkWriter({ api, log });
    const oneByOne = [];
    let count = 0;
    for (const entry of new Set(byVanilla.values())) {
      if (entry.broken || !dimension.containsBlock(volume, { includeTypes: [entry.vanilla] }, true)) continue;
      yield;
      if (KEEPS_WATER.has(entry.vanilla)) {
        for (const at of dimension.getBlocks(volume, { includeTypes: [entry.vanilla] }, true).getBlockLocationIterator()) oneByOne.push({ x: at.x, y: at.y, z: at.z });
        continue;
      }
      for (const vanilla of stateCombinations(entry)) {
        const matching = dimension.getBlocks(volume, { includePermutations: [resolve(entry.vanilla, vanilla)] }, true);
        yield;
        for (const at of matching.getBlockLocationIterator()) {
          writer.set(dimension, at, entry.block, convertedStates(entry, vanilla, at, found.result.get(key(at.x, at.y, at.z)) ?? 7));
          if (++count % 512 === 0) yield;
        }
      }
    }
    // Vanilla leaves in the shell just outside the box whose update_bit is clear keep it clear: found by state.
    const stay = [], clear = [];
    for (const entry of new Set(byVanilla.values()))
      for (const vanilla of stateCombinations(entry)) if (vanilla.update_bit === false) clear.push(resolve(entry.vanilla, vanilla));
    const { min, max } = box, range = dimension.heightRange;
    const below = Math.max(range.min, min.y - 1), above = Math.min(range.max - 1, max.y + 1);
    const shell = [[{ x: min.x - 1, y: below, z: min.z - 1 }, { x: min.x - 1, y: above, z: max.z + 1 }],
      [{ x: max.x + 1, y: below, z: min.z - 1 }, { x: max.x + 1, y: above, z: max.z + 1 }],
      [{ x: min.x, y: below, z: min.z - 1 }, { x: max.x, y: above, z: min.z - 1 }],
      [{ x: min.x, y: below, z: max.z + 1 }, { x: max.x, y: above, z: max.z + 1 }]];
    // The layers under and over the box, unless the box already reaches the world's bottom or top.
    if (below < min.y) shell.push([{ x: min.x, y: below, z: min.z }, { x: max.x, y: below, z: max.z }]);
    if (above > max.y) shell.push([{ x: min.x, y: above, z: min.z }, { x: max.x, y: above, z: max.z }]);
    for (const [from, to] of shell) {
      if (!clear.length) break;
      for (const at of dimension.getBlocks(new api.BlockVolume(from, to), { includePermutations: clear }, true).getBlockLocationIterator()) {
        stay.push({ x: at.x, y: at.y, z: at.z });
        if (++count % 256 === 0) yield;
      }
      yield;
    }
    const written = writer.flush();
    stats.swaps += written.length;
    keepUpdateBits(dimension, stay);
    yield;
    if (oneByOne.length) yield* convertAll(dimension, oneByOne, found);
    return written.length > 0 || oneByOne.length > 0 || dimension.containsBlock(volume, { includeTypes: [...byCustom.keys()] }, true);
  }

  /** Works the distances out again around a change and converts any vanilla leaf there (placed, grown, moved). */
  function* refreshArea(dimension, center, size) {
    if (!inCoverage(dimension, chunkOf(center.x), chunkOf(center.z))) return;
    let found;
    try { found = yield* searchAround(dimension, [center], size); } catch (error) { log('leaf refresh ' + String(error)); return; }
    const area = found.list.filter(at => Math.abs(at.x - center.x) <= size && Math.abs(at.y - center.y) <= size && Math.abs(at.z - center.z) <= size);
    yield* convertAll(dimension, area, found);
  }

  /**
   * Converts the leaves that waited, section by section, once no player could see them (after a view moved
   * or turned). A section nobody could see any part of goes whole, one somebody sees all of keeps waiting,
   * and the others leaf by leaf.
   */
  function* convertWaiting() {
    const isLoaded = loadedOnce();
    try {
      for (const [name, section] of [...waiting]) {
        const { dimension, cx, cy, cz } = section;
        if (!waiting.has(name)) continue;
        if (!inCoverage(dimension, cx, cz, isLoaded)) {
          // Out of reach: the chunk converts again when a player comes back.
          waitingCount -= section.at.size; waiting.delete(name);
          const record = chunks.get(chunkKey(dimension.id, cx, cz));
          if (record) record.done = false;
          continue;
        }
        const min = { x: cx * 16, y: cy * 16, z: cz * 16 }, max = { x: cx * 16 + 15, y: cy * 16 + 15, z: cz * 16 + 15 };
        if (views.seesAll(dimension.id, min, max)) continue;
        const all = !views.seesBox(dimension.id, min, max), ready = [];
        let count = 0;
        for (const at of section.at.values()) {
          if (all || !views.sees(dimension.id, at)) ready.push(at);
          if (++count % 128 === 0) yield;
        }
        if (!ready.length) { yield; continue; }
        let found;
        try { found = yield* searchAround(dimension, ready); } catch (error) { log('leaf wait ' + String(error)); continue; }
        yield* convertAll(dimension, ready, found);
      }
    } finally { rechecking = false; }
  }

  /** Marks converted, non-persistent leaves around a removed log or leaf for a decay check (update_bit), in small steps. */
  function* marking(dimension, center, size) {
    let found;
    try {
      const volume = new api.BlockVolume({ x: center.x - size, y: center.y - size, z: center.z - size }, { x: center.x + size, y: center.y + size, z: center.z + size });
      found = [...dimension.getBlocks(volume, { includeTypes: [...byCustom.keys()] }, true).getBlockLocationIterator()];
    } catch (error) { log('leaf mark ' + String(error)); return; }
    let count = 0;
    for (const at of found) {
      if (++count % 8 === 0) yield;
      const block = getBlock(dimension, at), entry = block && byCustom.get(block.typeId);
      if (!entry) continue;
      const states = block.permutation.getAllStates?.() ?? {};
      if (states[entry.mirror.persistent_bit] === 1 || states[entry.mirror.update_bit] === 1) continue;
      try { block.setPermutation(resolve(block.typeId, { ...states, [entry.mirror.update_bit]: 1 })); stats.marked++; }
      catch (error) { log('leaf mark ' + String(error)); }
    }
  }
  /** marking all at once. */
  function mark(dimension, center, size) { for (const _ of marking(dimension, center, size)) { /* to the end */ } }

  /**
   * Shortest distance through leaves from a leaf to a log (getBlock search, a box query when it grows large);
   * limit + 1 for none; undefined when the search reached a place it could not read (an unloaded chunk) before a log.
   */
  function distanceFrom(dimension, location, limit) {
    const visited = new Set([key(location.x, location.y, location.z)]);
    let frontier = [location], reads = 0, unknown = false;
    for (let distance = 1; distance <= limit && frontier.length; distance++) {
      const next = [];
      for (const at of frontier) for (const [dx, dy, dz] of SIDES) {
        const neighbor = { x: at.x + dx, y: at.y + dy, z: at.z + dz }, name = key(neighbor.x, neighbor.y, neighbor.z);
        if (visited.has(name)) continue;
        visited.add(name);
        if (++reads > MAX_SEARCH) {
          const { result } = distances(dimension, { x: location.x - limit, y: location.y - limit, z: location.z - limit },
            { x: location.x + limit, y: location.y + limit, z: location.z + limit });
          return Math.min(result.get(key(location.x, location.y, location.z)) ?? limit + 1, limit + 1);
        }
        const type = getBlock(dimension, neighbor)?.typeId;
        if (type === undefined) { unknown = true; continue; }
        if (logSet.has(type)) return distance;
        if (leafSet.has(type)) next.push(neighbor);
      }
      frontier = next;
    }
    return unknown ? undefined : limit + 1;
  }

  /**
   * Whether a log (vanilla or replaced) is within `radius` steps of a location through leaves, a step reaching
   * any of the 26 blocks around (so a log touching the canopy only by an edge or corner counts, but the trunk
   * of a separate tree does not); true when the area cannot be read.
   */
  function logWithin(dimension, location, radius) {
    let logs, leafCells;
    try {
      const volume = new api.BlockVolume({ x: location.x - radius, y: location.y - radius, z: location.z - radius },
        { x: location.x + radius, y: location.y + radius, z: location.z + radius });
      const cells = types => {
        const found = new Set();
        const locations = dimension.getBlocks(volume, { includeTypes: types }, true).getBlockLocationIterator();
        for (let item = locations.next(); !item.done; item = locations.next()) found.add(key(item.value.x, item.value.y, item.value.z));
        return found;
      };
      logs = cells(logTypes);
      if (!logs.size) return false;
      leafCells = cells(leafTypes);
    } catch { return true; }
    const visited = new Set([key(location.x, location.y, location.z)]);
    let frontier = [location];
    for (let step = 1; step <= radius && frontier.length; step++) {
      const next = [];
      for (const at of frontier) for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (let dz = -1; dz <= 1; dz++) {
        const name = key(at.x + dx, at.y + dy, at.z + dz);
        if (visited.has(name)) continue;
        visited.add(name);
        if (logs.has(name)) return true;
        if (leafCells.has(name)) next.push({ x: at.x + dx, y: at.y + dy, z: at.z + dz });
      }
      frontier = next;
    }
    return false;
  }

  /** Removes a decayed leaf with the drops its loot table gives without a tool; leaves next to it check again. */
  function decayLeaf(block) {
    const { dimension } = block, location = { ...block.location };
    let wet = false, drops = [];
    try { wet = block.isWaterlogged === true; } catch { /* unknown */ }
    try { if (world.gameRules?.doTileDrops !== false) drops = world.getLootTableManager?.().generateLootFromBlock(block) ?? []; }
    catch (error) { log('leaf loot ' + String(error)); }
    try { block.setPermutation(resolve(wet ? 'minecraft:water' : 'minecraft:air', {})); }
    catch (error) { log('leaf decay ' + String(error)); return; }
    stats.decays++;
    const center = { x: location.x + 0.5, y: location.y + 0.5, z: location.z + 0.5 };
    for (const item of drops) try { dimension.spawnItem(item, center); } catch { /* the chunk unloaded */ }
    mark(dimension, location, decay.leafRadius);
    queue(refreshArea(dimension, location, decay.javaDistance));
  }

  /** Random tick of a converted leaf (custom component bct:leaf). */
  function randomTick({ block }) {
    const entry = block && byCustom.get(block.typeId);
    if (!entry || !enabled) return;
    const states = block.permutation.getAllStates?.() ?? {};
    if (states[entry.mirror.persistent_bit] === 1) return;
    const marked = states[entry.mirror.update_bit] === 1;
    if (!marked && Math.random() * Math.max(1, limits().checkChance) >= 1) return;
    const distance = distanceFrom(block.dimension, block.location, decay.javaDistance);
    // Next to an unloaded chunk the log may lie there: nothing is decided.
    if (distance === undefined) return;
    // A leaf only decays with no log within bedrockDistance through leaves counting edge and corner steps too:
    // the face-to-face path can miss a log the canopy touches only by a corner, and a wrong decay drops apples
    // and saplings onto healthy trees; a separate tree's trunk nearby keeps nothing alive, as in vanilla.
    if (marked && distance > decay.bedrockDistance && !logWithin(block.dimension, block.location, decay.bedrockDistance)) {
      decayLeaf(block);
      return;
    }
    let next = { ...states };
    if (marked) next[entry.mirror.update_bit] = 0;
    else if (distance > decay.javaDistance) next[entry.mirror.update_bit] = 1;
    if (entry.look) next = withLook(entry, block, next, lookOf(entry, false, distance));
    if (Object.keys(next).every(name => next[name] === states[name])) return;
    try { block.setPermutation(resolve(block.typeId, next)); if (marked) stats.unmarked++; else if (next[entry.mirror.update_bit] === 1) stats.marked++; }
    catch (error) { log('leaf tick ' + String(error)); }
  }

  /**
   * A log or leaf disappeared (broken, blown up, burnt, moved): mark leaves like Bedrock does and refresh
   * distances. `later` (explosions, which report many blocks at once) marks within the time budget instead of now.
   */
  function removed(dimension, location, typeId, { later = false } = {}) {
    if (!byCustom.size || !enabled) return;
    const isLog = logSet.has(typeId), isLeaf = leafSet.has(typeId);
    if (!isLog && !isLeaf) return;
    // The block component and the world events can both report one removal.
    const name = dimension.id + '|' + key(location.x, location.y, location.z);
    if (recent.get(name) === system.currentTick) return;
    recent.set(name, system.currentTick);
    const size = isLog ? decay.logRadius : decay.leafRadius;
    if (later) queue(marking(dimension, { ...location }, size)); else mark(dimension, location, size);
    queue(refreshArea(dimension, { ...location }, decay.javaDistance));
  }

  /**
   * A block was placed: a placed leaf converts right away, or once no player sees it when one could;
   * a log or leaf changes distances around it.
   */
  function placed(block) {
    if (!byCustom.size || !enabled || !block) return;
    if (!leafSet.has(block.typeId) && !logSet.has(block.typeId)) return;
    const { dimension, location } = block;
    if (byVanilla.has(block.typeId) && inCoverage(dimension, chunkOf(location.x), chunkOf(location.z))) {
      if (seen(dimension, location)) hold(dimension, location);
      else if (apply(block, distanceFrom(dimension, location, decay.javaDistance) ?? decay.javaDistance + 1))
        own(dimension.id, chunkOf(location.x), chunkOf(location.z));
    }
    queue(refreshArea(dimension, { ...location }, decay.javaDistance));
  }

  /** Shears give only the leaf block: the other drops of the same break are removed (see itemSpawned). */
  function beforeBreak(event) {
    const entry = event.block && byCustom.get(event.block.typeId);
    if (!entry || event.itemStack?.typeId !== 'minecraft:shears') return;
    const { x, y, z } = event.block.location;
    sheared.set(event.block.dimension.id + '|' + key(x, y, z), { tick: system.currentTick, item: entry.vanilla });
  }

  /** An item entity appeared: shears drops other than the leaf block are removed. Returns true when the entity was removed. */
  function itemSpawned(entity, stack) {
    if (!sheared.size || !stack) return false;
    let location;
    try { location = entity.location; } catch { return false; }
    const name = entity.dimension.id + '|' + key(Math.floor(location.x), Math.floor(location.y), Math.floor(location.z));
    const break_ = sheared.get(name);
    if (!break_ || system.currentTick - break_.tick > 2 || stack.typeId === break_.item) return false;
    try { entity.remove(); return true; } catch { return false; }
  }

  function inCoverage(dimension, cx, cz, isLoaded = loaded) {
    if (!enabled) return false;
    const size = limits().chunkRadius;
    if (size <= 0) return false;
    const near = players.some(player => player.dimension.id === dimension.id &&
      Math.max(Math.abs(cx - chunkOf(player.location.x)), Math.abs(cz - chunkOf(player.location.z))) <= size);
    if (!near || !isLoaded(dimension, cx, cz)) return false;
    return NEIGHBOR_CHUNKS.every(([dx, dz]) => isLoaded(dimension, cx + dx, cz + dz));
  }

  /** A loaded check that asks the game once per chunk (neighbors overlap). */
  function loadedOnce() {
    const known = new Map();
    return (dimension, cx, cz) => {
      const name = chunkKey(dimension.id, cx, cz);
      let value = known.get(name);
      if (value === undefined) { value = loaded(dimension, cx, cz); known.set(name, value); }
      return value;
    };
  }

  function queue(job) { work.push(job); }

  /**
   * Finds the chunks around players that are ready to convert. Each player's
   * probe goes round the chunks within the radius, nearest first, starting
   * again from the player's own chunk whenever the player enters another one,
   * and passes over converted ones without asking the game; after a round it reaches only
   * two rings beyond the farthest loaded chunk it found (the render distance).
   * Queued chunks are ranked again a slice at a time, converted chunks next to
   * players are looked at again every recheckTicks, and chunks players left are
   * forgotten a slice at a time.
   */
  function schedule() {
    const size = limits().chunkRadius, isLoaded = loadedOnce();
    if (size !== radius) { radius = size; sweep = spiral(Math.max(0, size)); cursors.clear(); }
    if (size > 0 && enabled) {
      const probes = limits().probesPerTick;
      for (const player of players) {
        const cx = chunkOf(player.location.x), cz = chunkOf(player.location.z);
        let cursor = cursors.get(player.id);
        if (!cursor) { cursor = { index: 0, reach: radius, farthest: 0 }; cursors.set(player.id, cursor); }
        // A player who moved into another chunk (flying, an elytra) gets the rings next to them first again.
        if (cursor.cx !== cx || cursor.cz !== cz || cursor.dimension !== player.dimension.id) {
          cursor.cx = cx; cursor.cz = cz; cursor.dimension = player.dimension.id; cursor.index = 0;
        }
        const end = sweep.starts[Math.min(cursor.reach, radius) + 1] ?? sweep.offsets.length;
        for (let looked = 0, probed = 0; probed < probes && looked < Math.min(end, probes * PROBE_CHECKS); looked++) {
          if (cursor.index >= end) { cursor.index = 0; cursor.reach = Math.min(radius, cursor.farthest + 2); cursor.farthest = 0; break; }
          const [dx, dz] = sweep.offsets[cursor.index++], ring = Math.max(Math.abs(dx), Math.abs(dz));
          const name = chunkKey(player.dimension.id, cx + dx, cz + dz);
          let record = chunks.get(name);
          if (record?.done) { if (ring > cursor.farthest) cursor.farthest = ring; continue; }
          probed++;
          if (!record) { record = { dimension: player.dimension, cx: cx + dx, cz: cz + dz, done: false, ready: false, checkedAt: 0 }; chunks.set(name, record); }
          record.ready = inCoverage(record.dimension, record.cx, record.cz, isLoaded);
          if (isLoaded(record.dimension, record.cx, record.cz) && ring > cursor.farthest) cursor.farthest = ring;
          if (record.ready) enqueueChunk(name, record); else dequeueChunk(name);
        }
      }
    }
    for (const id of [...cursors.keys()]) if (!players.some(player => player.id === id)) cursors.delete(id);
    // Players moved or turned since chunks were queued: a slice of them is ranked again.
    if (!rerank) rerank = queued.keys();
    for (let count = 0; count < RERANK_CHUNKS; count++) {
      const next = rerank.next();
      if (next.done) { rerank = undefined; break; }
      const record = chunks.get(next.value);
      if (record?.ready && !record.done) enqueueChunk(next.value, record); else dequeueChunk(next.value);
    }
    // Converted chunks next to players are looked at again for new vanilla leaves (a grown tree, a structure).
    for (const player of players) {
      const cx = chunkOf(player.location.x), cz = chunkOf(player.location.z);
      for (let dx = -2; dx <= 2; dx++) for (let dz = -2; dz <= 2; dz++) {
        const name = chunkKey(player.dimension.id, cx + dx, cz + dz), record = chunks.get(name);
        if (!record?.done || rechecks.has(name) || system.currentTick - record.checkedAt < limits().recheckTicks) continue;
        record.checkedAt = system.currentTick;
        record.y = player.location.y;
        rechecks.add(name);
      }
    }
    // Converted leaves stay converted when players leave, like every replacement block: replacement logs
    // stay too, and vanilla leaves don't count them as logs, so swapped-back leaves would decay.
    // Records of chunks nobody is near go, a slice per pass.
    if (!cleanup) cleanup = chunks.entries();
    for (let looked = 0; looked < CLEANUP_CHUNKS; looked++) {
      const next = cleanup.next();
      if (next.done) { cleanup = undefined; break; }
      const [name, record] = next.value;
      if (!players.some(player => player.dimension.id === record.dimension.id &&
        Math.max(Math.abs(record.cx - chunkOf(player.location.x)), Math.abs(record.cz - chunkOf(player.location.z))) <= radius + 1)) {
        chunks.delete(name); dequeueChunk(name); rechecks.delete(name);
      }
    }
  }

  /** Looks for vanilla leaves in a converted chunk near the player who asked; any found that are not waiting make the chunk convert again. */
  function* recheckChunk(record) {
    const range = record.dimension.heightRange, x0 = record.cx * 16, z0 = record.cz * 16, types = [...byVanilla.keys()];
    const low = Math.max(range.min, Math.floor(record.y ?? range.min) - RECHECK_BAND), high = Math.min(range.max - 1, Math.floor(record.y ?? range.max) + RECHECK_BAND);
    for (let y = low; y <= high; y += SLAB) {
      try {
        const volume = new api.BlockVolume({ x: x0, y, z: z0 }, { x: x0 + 15, y: Math.min(high, y + SLAB - 1), z: z0 + 15 });
        if (record.dimension.containsBlock(volume, { includeTypes: types }, true)) {
          yield;
          for (const at of record.dimension.getBlocks(volume, { includeTypes: types }, true).getBlockLocationIterator())
            if (!isWaiting(record.dimension.id, at)) {
              record.done = false;
              if (record.ready) enqueueChunk(chunkKey(record.dimension.id, record.cx, record.cz), record);
              return;
            }
        }
      } catch { return; /* unloaded */ }
      yield;
    }
  }

  /**
   * The leaf work in turn, in small steps: queued jobs first, then the ready chunk that matters soonest,
   * with a look at a converted chunk next to a player every fourth turn while any is due.
   */
  function* steps() {
    for (;;) {
      let job = work.shift();
      if (!job && enabled && rechecks.size && (turn++ % 4 === 0 || !queued.size)) {
        const name = rechecks.values().next().value, record = chunks.get(name);
        rechecks.delete(name);
        if (record?.done) job = recheckChunk(record);
      }
      if (!job && enabled) {
        const next = pick();
        if (next && !inCoverage(next.dimension, next.cx, next.cz)) { next.ready = false; yield; continue; }
        if (next) { next.done = true; job = convertChunk(next); }
      }
      if (!job) {
        if (enabled && rechecks.size) continue;
        return;
      }
      yield* job;
      yield;
    }
  }

  /** update_bit clears due this tick on vanilla leaves a conversion next to them set. Returns false when time ran out. */
  function resetEdges(until) {
    const tick = system.currentTick;
    for (const [name, item] of resets) {
      if (item.tick > tick) break;
      if (now() >= until) return false;
      resets.delete(name);
      const block = getBlock(item.dimension, item.location);
      if (!block || !byVanilla.has(block.typeId) || block.permutation.getState?.('update_bit') !== true) continue;
      try { block.setPermutation(resolve(block.typeId, { ...block.permutation.getAllStates(), update_bit: false })); stats.cleared++; }
      catch (error) { log('leaf edge ' + String(error)); }
    }
    return true;
  }

  /** Works until `until` (a now() time): update_bit clears first, then the leaf work. Returns true while work remains. */
  function run(until) {
    if (!byCustom.size && !work.length) return false;
    if (!resetEdges(until)) return true;
    runner ??= steps();
    while (now() < until) {
      let step;
      try { step = runner.next(); } catch (error) { log('leaves ' + String(error)); step = { done: true }; }
      if (step.done) { runner = undefined; return false; }
    }
    return true;
  }

  function readIndex() {
    let text = '';
    for (let part = 0; ; part++) {
      const value = world.getDynamicProperty(INDEX + ':' + part);
      if (typeof value !== 'string') break;
      text += value;
    }
    return text ? text.split(';').filter(Boolean) : [];
  }

  function flush() {
    if (!dirty) return;
    dirty = false;
    for (let part = 0; world.getDynamicProperty(INDEX + ':' + part) !== undefined; part++) world.setDynamicProperty(INDEX + ':' + part, undefined);
    const text = [...owned.keys()].join(';');
    for (let part = 0; part * PART_SIZE < text.length; part++) world.setDynamicProperty(INDEX + ':' + part, text.slice(part * PART_SIZE, (part + 1) * PART_SIZE));
  }

  /** Loads the chunks a previous session converted; they stay converted while in coverage. */
  function recover() {
    if (recovered) return;
    recovered = true;
    for (const name of readIndex()) {
      const [dimension, cx, cz] = name.split('|');
      if (Number.isInteger(Number(cx)) && Number.isInteger(Number(cz))) owned.set(name, { dimension, cx: Number(cx), cz: Number(cz) });
    }
  }

  function forgetWork() {
    work.length = 0; runner = undefined; rechecks.clear();
    for (const name of [...queued.keys()]) dequeueChunk(name);
  }

  return {
    get status() {
      return { blocks: byCustom.size, ownedChunks: owned.size, knownChunks: chunks.size, toConvert: queued.size, queued: work.length,
        running: !!runner, enabled, waiting: waitingCount, ...stats };
    },
    setSource(provider, data) {
      if (data?.leaves) sources.set(provider, validate(data.leaves)); else sources.delete(provider);
      rebuild();
    },
    vanillaOf: typeId => byCustom.get(typeId)?.vanilla,
    /** persistent_bit and update_bit of the vanilla leaf a converted leaf stands for (undefined for other blocks). */
    vanillaStatesOf: block => {
      const entry = byCustom.get(block?.typeId);
      if (!entry) return undefined;
      const current = block.permutation.getAllStates?.() ?? {}, states = {};
      for (const [name, mirrored] of Object.entries(entry.mirror)) if (mirrored in current) states[name] = current[mirrored] === 1 || current[mirrored] === true;
      return states;
    },
    customsOf: typeId => { const entry = byVanilla.get(typeId); return entry ? [entry.block] : []; },
    customTypes: () => [...byCustom.keys()],
    isLeaf: typeId => byCustom.has(typeId),
    /** Turns one converted leaf back into its vanilla leaf, keeping its water. */
    revertLeaf(block) {
      const entry = byCustom.get(block?.typeId);
      if (!entry) return false;
      setKeepingWater(block, vanillaOf(entry, block)); stats.reverts++;
      return true;
    },
    /** The chunks holding converted leaves: [{ dimension (id), cx, cz }]. */
    ownedChunks: () => { recover(); return [...owned.values()]; },
    /**
     * Restoring the world: every converted leaf in the loaded chunks that hold some goes back to its vanilla leaf
     * (keeping its water), and the chunk is forgotten. Chunks not loaded wait. Nothing happens before the pack's
     * leaf data arrives, since without it a converted leaf cannot be mapped back.
     */
    restore(until) {
      recover();
      if (!byCustom.size) return;
      const types = [...byCustom.keys()];
      for (const chunk of [...owned.values()]) {
        if (now() >= until) break;
        let dimension;
        try { dimension = world.getDimension(chunk.dimension); } catch { continue; }
        try { if (!dimension.isChunkLoaded({ x: chunk.cx * 16, y: 0, z: chunk.cz * 16 })) continue; } catch { continue; }
        const range = dimension.heightRange;
        const volume = new api.BlockVolume({ x: chunk.cx * 16, y: range.min, z: chunk.cz * 16 },
          { x: chunk.cx * 16 + 15, y: range.max - 1, z: chunk.cz * 16 + 15 });
        try {
          if (dimension.containsBlock(volume, { includeTypes: types }, true))
            for (const location of dimension.getBlocks(volume, { includeTypes: types }, true).getBlockLocationIterator()) {
              const block = dimension.getBlock(location), entry = block && byCustom.get(block.typeId);
              if (entry) { setKeepingWater(block, vanillaOf(entry, block)); stats.reverts++; }
            }
        } catch (error) { console.warn('[BCT] leaf restore ' + chunk.dimension + ' ' + chunk.cx + ',' + chunk.cz + ': ' + String(error)); continue; }
        disown(chunk.dimension, chunk.cx, chunk.cz);
      }
      flush();
    },
    /** Blocks moved (a piston): leaves around it check again as if a log went away. */
    moved(dimension, location) {
      if (!byCustom.size || !enabled) return;
      queue(marking(dimension, { ...location }, decay.logRadius));
      queue(refreshArea(dimension, { ...location }, decay.javaDistance));
    },
    /** The custom component every converted leaf block lists (bct:leaf). */
    component: { onRandomTick: randomTick, onBreak: ({ block, brokenBlockPermutation }) => {
      try { removed(block.dimension, block.location, brokenBlockPermutation?.type?.id ?? 'minecraft:oak_leaves'); } catch { /* the block is gone */ }
    } },
    removed, placed, beforeBreak, itemSpawned, run,
    /**
     * Bookkeeping once a tick: views, the last session's chunks, chunks to look at (on maintenance passes)
     * and, after a view changed, the leaves that waited.
     */
    tick(current, maintenance) {
      players = current;
      views.update(current);
      recover();
      if (!byCustom.size) return;
      for (const [name, item] of sheared) if (system.currentTick - item.tick > 2) sheared.delete(name);
      for (const [name, tick] of recent) if (tick !== system.currentTick) recent.delete(name);
      if (maintenance) { schedule(); flush(); }
      if (waitingCount && enabled && !rechecking && views.stamp !== checkedStamp && system.currentTick - lastWaitPass >= WAIT_PASS_TICKS) {
        checkedStamp = views.stamp; rechecking = true; lastWaitPass = system.currentTick;
        queue(convertWaiting());
      }
    },
    setEnabled(value) {
      enabled = value;
      if (!value) { forgetWork(); clearWaiting(); }
      for (const [name, record] of chunks) { record.done = false; if (value && record.ready) enqueueChunk(name, record); }
    },
    flush,
  };
}
