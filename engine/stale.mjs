// Stale block cleanup: when a pack is unloaded, its blocks are not made any more, so this reverts the chunks that held them to vanilla (air where there was none). It runs while new packs settle, sweeping nearby chunks first, then asking the far worker for the rest.
import { setKeepingWater } from './bulk.mjs';

const PART_SIZE = 30000;

export function createStale({ api, log = () => {}, now, types, chunks, players, far, ready, stall = 6000 }) {
  const { world, system } = api;
  const made = new Map();
  let runner;
  let pending = new Map();
  let swept = new Set();
  let stale = new Map();
  let converted = 0;
  let active = false;
  let loaded = false;
  let lastProgress = -1;
  let refusedLogged = false;
  let targetsSet = false;
  let currentTypes = new Set();
  let signature;
  let dirty = false;
  let savedTick = -Infinity;

  function readParts(name) {
    let text = '';
    for (let part = 0; ; part++) {
      const value = world.getDynamicProperty(name + ':' + part);
      if (typeof value !== 'string') break;
      text += value;
    }
    return text;
  }
  function writeParts(name, text) {
    for (let part = 0; world.getDynamicProperty(name + ':' + part) !== undefined; part++) world.setDynamicProperty(name + ':' + part, undefined);
    for (let part = 0; part * PART_SIZE < text.length; part++) world.setDynamicProperty(name + ':' + part, text.slice(part * PART_SIZE, (part + 1) * PART_SIZE));
  }

  function load() {
    if (loaded) return;
    loaded = true;
    const madeText = readParts('bct:made');
    if (madeText) for (const entry of madeText.split(';')) {
      const [type, vanilla] = entry.split('>');
      made.set(type, vanilla === undefined ? null : vanilla);
    }
    const staleText = readParts('bct:stale');
    if (staleText) for (const key of staleText.split(';')) if (key) {
      const [dimensionId, cx, cz] = key.split('|');
      pending.set(key, { dimension: dimensionId, cx: Number(cx), cz: Number(cz) });
    }
  }

  function save() {
    const madeParts = [...made.entries()].map(([type, vanilla]) => (vanilla === null ? type : type + '>' + vanilla)).join(';');
    writeParts('bct:made', madeParts);
  }

  function savePending() {
    if (pending.size === 0) { writeParts('bct:stale', ''); return; }
    const keys = [...pending.keys()].join(';');
    writeParts('bct:stale', keys);
  }

  function chunkKey(chunk) { return chunk.dimension + '|' + chunk.cx + '|' + chunk.cz; }

  function activate() {
    active = true;
    for (const chunk of chunks()) pending.set(chunkKey(chunk), { dimension: chunk.dimension, cx: chunk.cx, cz: chunk.cz });
    swept.clear();
    lastProgress = system.currentTick;
    dirty = true;
    save();
    savePending();
  }

  function finish() {
    world.setDynamicProperty('bct:legacy', true);
    for (const type of stale.keys()) made.delete(type);
    save();
    pending.clear();
    swept.clear();
    lastProgress = -1;
    writeParts('bct:stale', '');
    if (targetsSet) { far.setTargets(undefined); targetsSet = false; }
    active = false;
    signature = undefined;
    log('stale: the blocks of removed packs are vanilla again (' + converted + ' converted)');
  }

  function* walk() {
    // removed-pack types cannot be named in a filter, so ask for everything the game does not know
    const registered = api.BlockTypes.getAll().map(type => type.id);
    const playerChunks = players().map(player => ({
      dimension: player.dimension.id,
      cx: Math.floor(player.location.x / 16),
      cz: Math.floor(player.location.z / 16),
    }));
    for (const chunk of pending.keys()) {
      const [dimensionId, cx, cz] = chunk.split('|');
      const ccx = Number(cx), ccz = Number(cz);
      if (!playerChunks.some(p => p.dimension === dimensionId && Math.max(Math.abs(p.cx - ccx), Math.abs(p.cz - ccz)) <= 12)) continue;
      let dim;
      try { dim = world.getDimension(dimensionId); } catch (error) { dim = null; }
      if (!dim) continue;
      try { if (!dim.isChunkLoaded({ x: ccx * 16, y: 0, z: ccz * 16 })) continue; } catch (error) { continue; }
      const range = dim.heightRange;
      for (let section = range.min; section < range.max; section += 64) {
        const from = { x: ccx * 16, y: section, z: ccz * 16 };
        const to = { x: ccx * 16 + 15, y: Math.min(range.max - 1, section + 63), z: ccz * 16 + 15 };
        let volume;
        try { volume = new api.BlockVolume(from, to); } catch (error) { break; }
        let found;
        try {
          if (!dim.containsBlock(volume, { excludeTypes: registered }, true)) continue;
          found = [...dim.getBlocks(volume, { excludeTypes: registered }, true).getBlockLocationIterator()];
        } catch (error) {
          if (!refusedLogged) { refusedLogged = true; log('stale: the game refused to look for the removed blocks: ' + String(error)); }
          break;
        }
        for (const location of found) {
          let block;
          try { block = dim.getBlock(location); } catch (error) { continue; }
          const type = block.typeId;
          const vanilla = stale.has(type) ? stale.get(type) : vanillaByName(type);
          if (vanilla === undefined) continue;
          try { fix(block, type, vanilla); } catch (error) { log('stale: could not revert ' + type + ': ' + String(error)); }
        }
        yield;
      }
      pending.delete(chunk);
      swept.add(chunk);
      lastProgress = system.currentTick;
      dirty = true;
      yield;
    }
  }

  // Blocks of a pack the world never recorded: bct_<pack>:r_<block> and m_<leaf> stand for the vanilla block, any other bct_ block for air.
  function vanillaByName(type) {
    if (currentTypes.has(type)) return undefined;
    const match = /^bct_[a-z0-9_]+:(.+)$/.exec(type);
    if (!match) return undefined;
    const named = /^[rm]_(.+)$/.exec(match[1]);
    return named ? 'minecraft:' + named[1] : null;
  }

  function fix(block, type, vanilla) {
    if (vanilla === null) { block.setType('minecraft:air'); converted++; return; }
    const old = (() => { try { return block.permutation.getAllStates() ?? {}; } catch { return {}; } })();
    let schema;
    try { schema = api.BlockPermutation.resolve(vanilla).getAllStates(); } catch { schema = {}; }
    const kept = {};
    for (const [stateName, value] of Object.entries(old)) {
      const base = stateName.includes(':') ? stateName.slice(stateName.indexOf(':') + 1) : stateName;
      for (const candidate of [base, 'minecraft:' + base]) {
        if (!(candidate in schema)) continue;
        kept[candidate] = typeof schema[candidate] === 'boolean' && typeof value === 'number' ? value === 1 : value;
        break;
      }
    }
    let permutation;
    try { permutation = api.BlockPermutation.resolve(vanilla, kept); } catch { permutation = api.BlockPermutation.resolve(vanilla); }
    setKeepingWater(block, permutation);
    converted++;
  }

  function* steps() {
    yield* walk();
  }

  return {
    tick() {
      if (!ready()) return;
      load();
      const legacy = world.getDynamicProperty('bct:legacy') !== true;
      const current = types();
      currentTypes = new Set(current.map(([type]) => type));
      let changed = false;
      for (const [type, vanilla] of current) { if (!made.has(type) || made.get(type) !== vanilla) { changed = true; } made.set(type, vanilla); }
      if (changed) save();
      stale = new Map([...made].filter(([type]) => !currentTypes.has(type)));
      if (stale.size === 0 && !legacy) { if (active) finish(); return; }
      const next = [...stale.keys()].sort().join(';');
      if (!active || next !== signature) activate();
      signature = next;
      for (const player of players()) {
        const px = Math.floor(player.location.x / 16), pz = Math.floor(player.location.z / 16);
        for (let dx = -3; dx <= 3; dx++) for (let dz = -3; dz <= 3; dz++) {
          const key = chunkKey({ dimension: player.dimension.id, cx: px + dx, cz: pz + dz });
          if (!pending.has(key) && !swept.has(key)) { pending.set(key, { dimension: player.dimension.id, cx: px + dx, cz: pz + dz }); dirty = true; }
        }
      }
      if (pending.size === 0) { finish(); return; }
      if (!targetsSet) { far.setTargets(() => [...pending.values()], 'Updating the world to the new pack'); targetsSet = true; }
      const tick = system.currentTick;
      if (tick - lastProgress > stall) { log('stale: gave up on ' + pending.size + ' chunks that did not load'); pending.clear(); finish(); return; }
      if (dirty && tick - savedTick >= 100) { savePending(); savedTick = tick; dirty = false; }
    },
    run(until) {
      if (!active || !pending.size) return false;
      runner ??= steps();
      while (now() < until) {
        let step;
        try { step = runner.next(); } catch (error) { log('stale ' + String(error)); step = { done: true }; }
        if (step.done) { runner = undefined; return false; }
      }
      return true;
    },
    get status() { return { active, pending: pending.size, stale: stale.size, converted }; },
  };
}
