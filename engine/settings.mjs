/**
 * Engine limits. Every value can be overridden with
 * `/scriptevent bct:config {"connected":{"chunkRadius":4}}`; overrides are
 * saved in the world and merged over these defaults.
 */
export const DEFAULT_SETTINGS = Object.freeze({
  debug: false,
  intervalTicks: 4,
  // Milliseconds of each tick all of the engine's work shares (scans, swaps, leaves, overlays); each
  // part's sliceMs (scanSliceMs for chunk scans) caps its own share. The game warns about a pack's scripts
  // from 10 ms a tick on average, counting more than this budget measures; blocks are written in bulk, so
  // this converts what loads within a few ticks.
  budgetMs: 6,
  // Chunk scans for connected textures and terrain: scanJobs chunks at a time, at most scanSliceMs of each tick.
  scanJobs: 2,
  scanSliceMs: 4,
  connected: Object.freeze({ chunkRadius: 3, yBand: 24, maxCarriers: 1500, maxCarriersPerChunk: 96 }),
  terrain: Object.freeze({ chunkRadius: 6, yBand: 24, maxEntityCarriers: 1500, maxNativeCarriers: 8000 }),
  // Native replacement blocks: swapped in within chunkRadius chunks and yBand
  // blocks of a player, at most maxSwapsPerTick block changes and sliceMs per
  // tick; every refreshTicks the chunks are scanned again. Blocks change as
  // soon as they load or are placed, nearest first. Setting farDistance makes a
  // block that shows and lies within viewAngle degrees (the whole cone) of a
  // player's view direction and farDistance blocks of the head wait until no
  // player looks at it; for joinTicks after a player joins, changes dimension
  // or teleports, nearDistance takes the place of farDistance for that player.
  // Leaves with the author's models wait the same way (views.mjs). Swapped
  // blocks stay swapped when players leave, like converted leaves, so nothing
  // changes back and forth while players move about; swapBack 1 swaps a chunk
  // back to vanilla once no player is near it.
  replace: Object.freeze({ chunkRadius: 4, yBand: 32, maxSwapsPerTick: 512, refreshTicks: 600, sliceMs: 4,
    viewAngle: 140, farDistance: 0, nearDistance: 0, joinTicks: 600, swapBack: 0 }),
  // Leaves with the author's models: converted in every loaded chunk within chunkRadius of a player
  // whose eight neighbors are loaded too (0 turns leaves off), using at most sliceMs of the shared budget per tick.
  // probesPerTick chunks are looked at per pass; converted chunks next to players are checked for new
  // vanilla leaves every recheckTicks; one random tick in checkChance checks an unmarked leaf.
  leaves: Object.freeze({ chunkRadius: 32, sliceMs: 6, probesPerTick: 64, recheckTicks: 20, checkChance: 8 }),
  // Overlay surfaces: cells within chunkRadius chunks and yBand blocks of a player are kept up to date,
  // using sliceMs of each tick for the cells and as much for their scan; refreshTicks between full rescans.
  overlay: Object.freeze({ chunkRadius: 4, yBand: 24, sliceMs: 4, refreshTicks: 1200 }),
  // Beyond the simulation distance (far.mjs): areas out to chunkRadius chunks from a player are loaded one at
  // a time and converted, each kept at most holdTicks; status 1 shows the areas left on the action bar.
  // chunkRadius 0 turns it off. blocks 0 converts only leaves there, the change seen from far away; blocks 1
  // also swaps blocks and draws overlay surfaces near the surface of each area. Near players everything converts.
  far: Object.freeze({ chunkRadius: 32, holdTicks: 400, status: 1, blocks: 0 }),
});

const LIMITS = {
  intervalTicks: [1, 100], budgetMs: [1, 50], scanJobs: [1, 8], scanSliceMs: [1, 20],
  'connected.chunkRadius': [0, 16], 'connected.yBand': [4, 384],
  'connected.maxCarriers': [0, 20000], 'connected.maxCarriersPerChunk': [0, 1024],
  'terrain.chunkRadius': [0, 32], 'terrain.yBand': [4, 384],
  'terrain.maxEntityCarriers': [0, 20000], 'terrain.maxNativeCarriers': [0, 65536],
  'replace.chunkRadius': [0, 16], 'replace.yBand': [4, 384], 'replace.maxSwapsPerTick': [0, 4096],
  'replace.refreshTicks': [20, 72000], 'replace.sliceMs': [1, 20],
  'replace.viewAngle': [0, 360], 'replace.farDistance': [0, 512], 'replace.nearDistance': [0, 512], 'replace.joinTicks': [0, 72000], 'replace.swapBack': [0, 1],
  'leaves.chunkRadius': [0, 64], 'leaves.sliceMs': [1, 20], 'leaves.probesPerTick': [1, 1024], 'leaves.recheckTicks': [1, 1200],
  'leaves.checkChance': [1, 1000],
  'overlay.chunkRadius': [0, 16], 'overlay.yBand': [4, 384], 'overlay.sliceMs': [1, 20], 'overlay.refreshTicks': [20, 72000],
  'far.chunkRadius': [0, 128], 'far.holdTicks': [40, 2400], 'far.status': [0, 1], 'far.blocks': [0, 1],
};

/** Defaults merged with validated overrides; unknown or out-of-range values are rejected. */
export function resolveSettings(overrides = {}) {
  const result = { ...DEFAULT_SETTINGS, connected: { ...DEFAULT_SETTINGS.connected }, terrain: { ...DEFAULT_SETTINGS.terrain },
    replace: { ...DEFAULT_SETTINGS.replace }, leaves: { ...DEFAULT_SETTINGS.leaves }, overlay: { ...DEFAULT_SETTINGS.overlay },
    far: { ...DEFAULT_SETTINGS.far } };
  if (overrides === null || typeof overrides !== 'object') throw new Error('Settings must be an object');
  for (const [key, value] of Object.entries(overrides)) {
    if (key === 'debug') {
      if (typeof value !== 'boolean') throw new Error('debug must be true or false');
      result.debug = value;
    } else if (['connected', 'terrain', 'replace', 'leaves', 'overlay', 'far'].includes(key)) {
      if (value === null || typeof value !== 'object') throw new Error(key + ' must be an object');
      for (const [name, number] of Object.entries(value)) result[key][name] = checked(key + '.' + name, number);
    } else result[key] = checked(key, value);
  }
  return result;
}

function checked(name, value) {
  const range = LIMITS[name];
  if (!range) throw new Error('Unknown setting: ' + name);
  if (!Number.isInteger(value) || value < range[0] || value > range[1]) throw new Error(`${name} must be an integer from ${range[0]} to ${range[1]}`);
  return value;
}

/** Console output only when debugging. */
export function createLog(settings) {
  return message => { if (settings.debug) console.warn('[BCT] ' + message); };
}

const written = new WeakMap();

/** Sends only property values that differ from the last value written to this entity. */
export function writeProperties(entity, values) {
  let cache = written.get(entity);
  if (!cache) { cache = new Map(); written.set(entity, cache); }
  for (const [name, value] of Object.entries(values)) {
    if (cache.get(name) === value) continue;
    if (!cache.has(name) && entity.getProperty?.(name) === value) { cache.set(name, value); continue; }
    entity.setProperty(name, value);
    cache.set(name, value);
  }
}

/** Shared entity carrier allowance with a total and a per-chunk limit. */
export function createCarrierBudget(limits) {
  const chunks = new Map();
  let total = 0;
  const key = (dimension, location) => dimension + ':' + Math.floor(location.x / 16) + ':' + Math.floor(location.z / 16);
  return {
    get total() { return total; },
    perChunk(dimension, location) { return chunks.get(key(dimension, location)) ?? 0; },
    claim(dimension, location) {
      const id = key(dimension, location), count = chunks.get(id) ?? 0;
      if (total >= limits().maxCarriers || count >= limits().maxCarriersPerChunk) return false;
      chunks.set(id, count + 1); total++;
      return true;
    },
    release(dimension, location) {
      const id = key(dimension, location), count = chunks.get(id) ?? 0;
      if (!count) return;
      if (count === 1) chunks.delete(id); else chunks.set(id, count - 1);
      total--;
    },
    clear() { chunks.clear(); total = 0; },
  };
}

/** Chebyshev chunk distance from a location to the nearest player in the same dimension. */
export function chunkDistance(players, dimensionId, location) {
  let best = Infinity;
  const cx = Math.floor(location.x / 16), cz = Math.floor(location.z / 16);
  for (const player of players) {
    if (player.dimension.id !== dimensionId) continue;
    const distance = Math.max(Math.abs(cx - Math.floor(player.location.x / 16)), Math.abs(cz - Math.floor(player.location.z / 16)));
    if (distance < best) best = distance;
  }
  return best;
}

/** Whether a location is inside some player's chunk radius and vertical band. */
export function withinReach(players, dimensionId, location, radius, band) {
  const cx = Math.floor(location.x / 16), cz = Math.floor(location.z / 16);
  return players.some(player => player.dimension.id === dimensionId &&
    Math.abs(cx - Math.floor(player.location.x / 16)) <= radius &&
    Math.abs(cz - Math.floor(player.location.z / 16)) <= radius &&
    (band === undefined || Math.abs(location.y - player.location.y) <= band));
}
