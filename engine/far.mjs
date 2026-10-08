/**
 * Converting the world beyond the simulation distance, a piece at a time.
 *
 * Scripts can only change blocks in chunks the game keeps loaded, which are
 * the chunks within the simulation distance of a player; trees and walls the
 * player sees farther out would stay vanilla until the player came near. This
 * worker loads one area of chunks at a time (8 x 8, or smaller when the pack's
 * ticking chunk allowance is smaller) with a ticking area
 * (world.tickingAreaManager, which only loads this pack's own areas), nearest
 * to a player first, out to `chunkRadius` chunks. While an area is loaded, a
 * virtual player stands at its centre, so the leaves, the replacement blocks
 * and the overlay surfaces convert it exactly as they convert the area around
 * a real player. Once their work there is done (or holdTicks have passed) the
 * area is released, recorded as done in the world, and the next one is loaded.
 * Converted blocks stay converted, so over time the whole explored world is.
 *
 * While work remains, players see on the action bar how many of the chunks
 * around them are converted (`status` 1). An area the game does not finish
 * loading is skipped. Without a ticking area manager nothing happens.
 */

// Chunks along each side of an area, the largest of these the pack's ticking chunk allowance holds. An
// area is loaded with one more chunk on each side, so its edge chunks have loaded neighbors (leaves
// convert a chunk only when the eight around it are loaded).
const AREA_SIZES = [8, 4, 2];
// Ticks an area stays loaded at least, so the scans reach all of it.
const MIN_HOLD_TICKS = 40;
// Done areas are saved in parts of this many characters (world dynamic properties).
const DONE_KEY = 'bct:far:done', PART_SIZE = 30000;
// How often (ticks) the progress line is shown again; the game fades action bar text after a few seconds.
const STATUS_TICKS = 20;
// Ticks an area may take to load before it is skipped.
const LOAD_TIMEOUT_TICKS = 600;
const IDENTIFIER = 'bct_far';

const areaKey = (dimensionId, size, ax, az) => dimensionId + '|' + size + '|' + ax + '|' + az;

/**
 * api: @minecraft/server objects. limits(): the `far` settings { chunkRadius, holdTicks, status }.
 * idle(): whether the engine has no conversion work left (leaves, replacements, overlays).
 */
export function createFar({ api, limits, log = () => {}, idle = () => true }) {
  const { world, system } = api;
  const done = new Set();
  let current, loadedDone = false, enabled = true, statusTick = 0, failures = 0, size;
  // While restoring: () => the chunks still to bring back [{ dimension (id), cx, cz }], and how often each area was loaded.
  let targets;
  const tries = new Map();

  function loadDone() {
    if (loadedDone) return;
    loadedDone = true;
    let text = '';
    for (let part = 0; ; part++) {
      const value = world.getDynamicProperty(DONE_KEY + ':' + part);
      if (typeof value !== 'string') break;
      text += value;
    }
    for (const name of text.split(';')) if (name) done.add(name);
  }
  function saveDone() {
    for (let part = 0; world.getDynamicProperty(DONE_KEY + ':' + part) !== undefined; part++) world.setDynamicProperty(DONE_KEY + ':' + part, undefined);
    const text = [...done].join(';');
    for (let part = 0; part * PART_SIZE < text.length; part++) world.setDynamicProperty(DONE_KEY + ':' + part, text.slice(part * PART_SIZE, (part + 1) * PART_SIZE));
  }

  const manager = () => { try { return world.tickingAreaManager; } catch { return undefined; } };
  /** The area size the allowance holds (with its border), once known. */
  function areaSize() {
    if (size !== undefined) return size;
    // When the allowance cannot be read, assume room for a small area; hasCapacity still checks each one.
    let allowed = 36;
    try { allowed = manager()?.maxChunkCount ?? allowed; } catch { /* keep the assumption */ }
    size = AREA_SIZES.find(side => (side + 2) * (side + 2) <= allowed) ?? 0;
    return size;
  }

  /**
   * The areas within chunkRadius of a player that are not done, nearest first, and the ones in front of the
   * player before the ones beside and behind: an area straight behind counts as one and a half rings farther.
   */
  function areasAround(player) {
    const area = areaSize(), radius = Math.ceil(limits().chunkRadius / area);
    const ax = Math.floor(Math.floor(player.location.x / 16) / area), az = Math.floor(Math.floor(player.location.z / 16) / area);
    let view;
    try { view = player.getViewDirection?.(); } catch { /* not a real player */ }
    const length = view ? Math.hypot(view.x, view.z) : 0;
    const dimensionId = player.dimension.id, found = [];
    for (let dx = -radius; dx <= radius; dx++) for (let dz = -radius; dz <= radius; dz++) {
      const name = areaKey(dimensionId, area, ax + dx, az + dz);
      if (done.has(name)) continue;
      const away = Math.hypot(dx, dz);
      const facing = length > 0.01 && away > 0 ? (dx * view.x + dz * view.z) / (away * length) : 1;
      const distance = Math.max(Math.abs(dx), Math.abs(dz)) * 2 + (dx * dx + dz * dz) / 100 + (1 - facing) * 1.5;
      found.push({ name, dimension: player.dimension, ax: ax + dx, az: az + dz, distance });
    }
    return found.sort((a, b) => a.distance - b.distance);
  }

  /**
   * While restoring: the areas holding chunks still to bring back, in any dimension, the ones nearest a player
   * first. An area loaded three times without finishing is left.
   */
  function restoreAreas(players) {
    const side = areaSize(), byName = new Map();
    for (const chunk of targets()) {
      const ax = Math.floor(chunk.cx / side), az = Math.floor(chunk.cz / side), name = areaKey(chunk.dimension, side, ax, az);
      if ((tries.get(name) ?? 0) >= 3) continue;
      if (!byName.has(name)) byName.set(name, { name, dimensionId: chunk.dimension, ax, az, chunks: 0 });
      byName.get(name).chunks++;
    }
    const areas = [...byName.values()];
    for (const area of areas) {
      area.distance = Infinity;
      for (const player of players) {
        if (player.dimension.id !== area.dimensionId) continue;
        const dx = area.ax - Math.floor(Math.floor(player.location.x / 16) / side), dz = area.az - Math.floor(Math.floor(player.location.z / 16) / side);
        area.distance = Math.min(area.distance, Math.max(Math.abs(dx), Math.abs(dz)));
      }
      try { area.dimension = world.getDimension(area.dimensionId); } catch { area.dimension = undefined; }
    }
    return areas.filter(area => area.dimension).sort((a, b) => a.distance - b.distance);
  }

  /** Whether the loaded area has nothing left to bring back. */
  const restored = area => !targets().some(chunk => chunk.dimension === area.dimensionId &&
    Math.floor(chunk.cx / size) === area.ax && Math.floor(chunk.cz / size) === area.az);

  /**
   * The action bar line, like Distant Horizons shows its work: the chunks converted so far out of all the
   * chunks within reach of a player, counting up with every area done. Nothing when all of it is done.
   */
  function progressText(player) {
    if (targets) {
      const chunks = targets().length;
      return chunks ? `§7Restoring the world: ${chunks.toLocaleString('en-US')} chunks to go` : undefined;
    }
    const left = areasAround(player).length;
    if (!left) return undefined;
    const side = areaSize(), across = 2 * Math.ceil(limits().chunkRadius / side) + 1;
    const total = across * across * side * side, converted = total - left * side * side;
    const percent = Math.floor(converted * 100 / total);
    return `§7Loading the pack in the distance: ${converted.toLocaleString('en-US')} / ${total.toLocaleString('en-US')} chunks (${percent}%)`;
  }

  function release() {
    if (!current) return;
    try { manager()?.removeTickingArea(current.identifier); } catch (error) { log('far area ' + String(error)); }
    current = undefined;
  }

  /** Loads the next area; the virtual player appears once the game says its chunks are loaded and ticking. */
  function start(area) {
    const tickingAreas = manager();
    if (!tickingAreas) return;
    const range = area.dimension.heightRange, side = areaSize();
    const from = { x: (area.ax * side - 1) * 16, y: range.min, z: (area.az * side - 1) * 16 };
    const to = { x: ((area.ax + 1) * side + 1) * 16 - 1, y: range.max - 1, z: ((area.az + 1) * side + 1) * 16 - 1 };
    const options = { dimension: area.dimension, from, to };
    if (!tickingAreas.hasCapacity(options)) { log('far: no ticking area capacity'); return; }
    const identifier = IDENTIFIER + '_' + system.currentTick;
    current = { ...area, identifier, ready: false, startTick: system.currentTick };
    const mine = current;
    tickingAreas.createTickingArea(identifier, options).then(() => {
      if (current !== mine) return;
      const x = (area.ax * side + side / 2) * 16, z = (area.az * side + side / 2) * 16;
      let y = 64;
      try { y = area.dimension.getTopmostBlock({ x, z })?.location.y ?? y; } catch { /* keep sea level */ }
      mine.virtual = { id: 'bct:far', virtual: true, dimension: area.dimension, location: { x, y, z } };
      mine.ready = true;
      mine.readyTick = system.currentTick;
    }, error => {
      log('far area ' + String(error));
      failures++;
      if (current === mine) { release(); done.add(mine.name); }
    });
  }

  return {
    /** The virtual players standing in loaded areas (none or one): the engine's parts treat them as players. */
    virtualPlayers: () => (enabled && current?.ready ? [current.virtual] : []),
    /** Once per engine pass with the real players. */
    tick(players) {
      const { chunkRadius, holdTicks, status } = limits();
      if (!enabled || (!chunkRadius && !targets) || !players.length || !manager() || !areaSize()) { release(); return; }
      loadDone();
      // The progress line first, refreshed before the game fades it, so it stays up while work remains.
      if (status && system.currentTick - statusTick >= STATUS_TICKS) {
        statusTick = system.currentTick;
        for (const player of players) {
          const text = progressText(player);
          if (!text) continue;
          try { player.onScreenDisplay?.setActionBar(text); }
          catch { /* the player left */ }
        }
      }
      if (current) {
        if (!current.ready) {
          // An area the game never finishes loading is skipped rather than holding up the rest.
          if (system.currentTick - current.startTick < LOAD_TIMEOUT_TICKS) return;
          failures++;
          done.add(current.name);
          release();
        } else {
          const held = system.currentTick - current.readyTick;
          if (targets) {
            if (held < MIN_HOLD_TICKS || (held < holdTicks && !restored(current))) return;
            tries.set(current.name, (tries.get(current.name) ?? 0) + 1);
            release();
          } else {
            if (held < MIN_HOLD_TICKS || (held < holdTicks && !idle())) return;
            done.add(current.name);
            saveDone();
            release();
          }
        }
      }
      if (targets) {
        const [next] = restoreAreas(players);
        if (next) start(next);
        return;
      }
      let next;
      for (const player of players) {
        const [nearest] = areasAround(player);
        if (nearest && (!next || nearest.distance < next.distance)) next = nearest;
      }
      if (next) start(next);
    },
    setEnabled(value) { enabled = value; if (!value) release(); },
    /** Restoring: load the areas holding these chunks (a function giving the chunks still to bring back) until none
     * are left, ignoring chunkRadius and the areas already done. Without one the worker converts again. */
    setTargets(list) { targets = list; tries.clear(); release(); },
    get status() {
      loadDone();
      return { areaChunks: size ?? null, done: done.size, loading: current ? current.name : null, ready: Boolean(current?.ready), failures };
    },
  };
}
