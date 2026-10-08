import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

/** bedrock-samples checkout: BEDROCK_SAMPLES, else a sibling folder of the project (like converter/common.py). */
export const samplesPath = () => process.env.BEDROCK_SAMPLES ?? fileURLToPath(new URL('../../bedrock-samples/', import.meta.url));

/** Vanilla block types and the values of their states, from metadata/vanilladata_modules/mojang-blocks.json. */
export function vanillaBlocks(samples = samplesPath()) {
  const data = JSON.parse(readFileSync(join(samples, 'metadata/vanilladata_modules/mojang-blocks.json'), 'utf8'));
  const values = new Map(data.block_properties.map(property => [property.name, property.values.map(item => item.value)]));
  return new Map(data.data_items.map(item => [item.name, new Map((item.properties ?? []).map(property => [property.name, values.get(property.name)]))]));
}

/** A registry with custom block definitions (behavior pack blocks/*.json documents) added. */
export function withDefinitions(registry, definitions) {
  const result = new Map(registry);
  for (const document of definitions) {
    const block = document['minecraft:block'];
    const states = new Map();
    for (const [name, spec] of Object.entries(block.description.states ?? {})) {
      const options = Array.isArray(spec) ? spec : spec.values;
      states.set(name, Array.isArray(options) ? options : Array.from({ length: options.max - options.min + 1 }, (_, index) => options.min + index));
    }
    result.set(block.description.identifier, states);
  }
  return result;
}

/**
 * Block definitions for engine replacement data, written the way the converter
 * writes them: mirrored vanilla states (booleans as 0 or 1), pattern
 * coordinates, random picks and model choices as integer states.
 */
export function definitionsFor(data, registry) {
  const range = count => ({ values: { min: 0, max: count - 1 } });
  return data.blocks.map(entry => {
    const states = {}, vanilla = registry.get(entry.vanilla) ?? new Map();
    for (const [name, mirrored] of Object.entries(entry.mirror)) {
      const values = vanilla.get(name) ?? [];
      states[mirrored] = values.every(value => typeof value === 'boolean') ? range(2) : values;
    }
    for (const [state, , size] of entry.axes) states[state] = range(size);
    for (const random of entry.random) states[random.state] = range(random.count);
    for (const model of entry.models ?? []) states[model.state] = range(model.weights.length);
    for (const [state, values] of Object.entries(entry.states ?? {})) states[state] = values;
    return { format_version: '1.26.50', 'minecraft:block': { description: { identifier: entry.block, states }, components: {} } };
  });
}

/**
 * A small in-memory stand-in for the @minecraft/server objects the engine uses.
 * With a registry (vanillaBlocks(), withDefinitions()), BlockPermutation.resolve
 * and getBlocks filters reject block types the game does not know and state
 * values a block does not have, as the game does.
 *
 * With `costs` ({call: milliseconds}, see GAME_COSTS) every game call moves a
 * clock by what it would cost the game, the engine reads that clock as its
 * time (api.now), and `stepTimes` holds the time each step took, so tests can
 * check how much work the engine does per tick; `calls` counts calls and time
 * by kind.
 */
export function fakeBedrock({ blocks = new Map(), states = new Map(), height = { min: -64, max: 320 }, onSet = () => {}, registry, costs,
  recordVolumes = true } = {}) {
  const clock = { ms: 0 }, calls = new Map(), stepTimes = [];
  /** Moves the clock by the cost of a game call (only with costs). */
  const charge = (name, ms = costs?.[name] ?? 0) => {
    if (!costs) return;
    clock.ms += ms;
    const tally = calls.get(name) ?? { count: 0, ms: 0 };
    tally.count++; tally.ms += ms;
    calls.set(name, tally);
  };
  const known = type => { if (registry && !registry.has(type)) throw new Error('Unknown block type: ' + type); };
  const checkStates = (type, values) => {
    if (!registry) return;
    known(type);
    const allowed = registry.get(type);
    for (const [name, value] of Object.entries(values)) {
      if (!allowed.has(name)) throw new Error(`${type} has no state ${name}`);
      if (!allowed.get(name).some(option => option === value)) throw new Error(`${type}: ${name}=${JSON.stringify(value)} is not one of ${JSON.stringify(allowed.get(name))}`);
    }
  };
  const channel = () => {
    const listeners = new Set();
    return { subscribe(fn) { listeners.add(fn); return fn; }, unsubscribe(fn) { listeners.delete(fn); },
      emit(event) { for (const fn of [...listeners]) fn(event); }, get size() { return listeners.size; } };
  };
  const entities = [], runs = new Map(), intervals = new Map(), jobs = new Map(), timeouts = [], volumes = [], properties = new Map(), writes = [];
  const sounds = [], wet = new Set(), components = new Map();
  let next = 0, ids = 0;
  /** Loot a block gives without a tool (world.getLootTableManager().generateLootFromBlock); tests replace it. */
  const loot = { of: () => [] };
  class ItemStack {
    constructor(typeId, amount = 1) { this.typeId = typeId.includes(':') ? typeId : 'minecraft:' + typeId; this.amount = amount; this.tags = new Set(); this.parts = new Map(); }
    hasTag(tag) { return this.tags.has(tag); }
    getComponent(name) { return this.parts.get(name); }
  }
  const typeAt = location => blocks.get(location.x + ',' + location.y + ',' + location.z) ?? 'minecraft:air';
  const cellsOf = volume => (volume.to.x - volume.from.x + 1) * (volume.to.y - volume.from.y + 1) * (volume.to.z - volume.from.z + 1);
  /** Every location of a volume whose block type passes `keep`, walking the cells or the block map, whichever is smaller. */
  function matching(volume, keep, first = false) {
    const found = [];
    if (!keep('minecraft:air') && blocks.size < cellsOf(volume)) {
      for (const [key, type] of blocks) {
        if (!keep(type, key)) continue;
        const [x, y, z] = key.split(',').map(Number);
        if (x < volume.from.x || x > volume.to.x || y < volume.from.y || y > volume.to.y || z < volume.from.z || z > volume.to.z) continue;
        found.push({ x, y, z });
        if (first) break;
      }
      return found;
    }
    // Air is every cell absent from the map, so walk the whole volume.
    for (let x = volume.from.x; x <= volume.to.x; x++) for (let y = volume.from.y; y <= volume.to.y; y++) for (let z = volume.from.z; z <= volume.to.z; z++)
      if (keep(typeAt({ x, y, z }), x + ',' + y + ',' + z)) { found.push({ x, y, z }); if (first) return found; }
    return found;
  }
  // A permutation filter matches a block of its type whose states include every state it names.
  const matchesPermutation = (permutation, type, key) => permutation.type === type &&
    Object.entries(permutation.states).every(([name, value]) => states.get(key)?.[name] === value);
  const filterOf = filter => {
    for (const type of [...(filter.includeTypes ?? []), ...(filter.excludeTypes ?? [])]) known(type);
    const include = filter.includeTypes && new Set(filter.includeTypes), exclude = new Set(filter.excludeTypes ?? []);
    const includePermutations = filter.includePermutations ?? [], excludePermutations = filter.excludePermutations ?? [];
    const anyInclude = Boolean(include) || includePermutations.length > 0;
    return (type, key) => (!anyInclude || include?.has(type) || includePermutations.some(permutation => matchesPermutation(permutation, type, key))) &&
      !exclude.has(type) && !excludePermutations.some(permutation => matchesPermutation(permutation, type, key));
  };
  /** Every location of a box volume or a list volume. */
  const locationsOf = volume => {
    if (volume.locations) return volume.locations.map(at => ({ x: at.x, y: at.y, z: at.z }));
    const all = [];
    for (let x = volume.from.x; x <= volume.to.x; x++) for (let y = volume.from.y; y <= volume.to.y; y++) for (let z = volume.from.z; z <= volume.to.z; z++) all.push({ x, y, z });
    return all;
  };
  const dimension = {
    id: 'minecraft:overworld', heightRange: height,
    isChunkLoaded: () => { charge('isChunkLoaded'); return true; },
    getBlock(location) {
      charge('getBlock');
      const key = location.x + ',' + location.y + ',' + location.z;
      const block = { location: { ...location }, dimension, typeId: typeAt(location), get isAir() { return this.typeId === 'minecraft:air'; },
        get isWaterlogged() { return wet.has(key); }, setWaterlogged(value) { if (value) wet.add(key); else wet.delete(key); },
        setType(type) { this.setPermutation(api.BlockPermutation.resolve(type, {})); },
        permutation: { getState: name => { charge('getState'); return states.get(key)?.[name]; },
          getAllStates: () => { charge('getState'); return { ...(states.get(key) ?? {}) }; } },
        setPermutation(value) {
          charge('setPermutation');
          writes.push({ location: { ...location }, type: value.type, states: { ...value.states } });
          if (value.type === 'minecraft:air') blocks.delete(key); else blocks.set(key, value.type);
          states.set(key, { ...value.states });
          // Like the game, setting a block drops a waterlogged block's water.
          wet.delete(key);
          this.typeId = value.type;
          onSet({ ...location }, value);
        },
        offset: delta => dimension.getBlock({ x: location.x + delta.x, y: location.y + delta.y, z: location.z + delta.z }) };
      return block;
    },
    getTopmostBlock({ x, z }) {
      charge('getTopmostBlock');
      for (let y = height.max - 1; y >= height.min; y--) if (typeAt({ x, y, z }) !== 'minecraft:air') return dimension.getBlock({ x, y, z });
      return undefined;
    },
    getBlocks(volume, filter) {
      const keep = filterOf(filter);
      if (recordVolumes) volumes.push({ from: volume.from, to: volume.to, types: [...(filter.includeTypes ?? [])], exclude: [...(filter.excludeTypes ?? [])] });
      const found = matching(volume, keep);
      charge('getBlocks', (costs?.getBlocks ?? 0) + cellsOf(volume) * (costs?.cell ?? 0) + found.length * (costs?.result ?? 0));
      const inside = new Set(found.map(at => at.x + ',' + at.y + ',' + at.z));
      return {
        getBlockLocationIterator: function* () { for (const at of found) { charge('next'); yield at; } },
        isInside: location => { charge('isInside'); return inside.has(location.x + ',' + location.y + ',' + location.z); },
        getCapacity: () => found.length,
      };
    },
    /** One native call for many blocks: much cheaper per block than setPermutation; like the game, it drops a waterlogged block's water. */
    fillBlocks(volume, permutation, options = {}) {
      const value = typeof permutation === 'string' ? api.BlockPermutation.resolve(permutation, {}) : permutation;
      const keep = options.blockFilter ? filterOf(options.blockFilter) : () => true;
      const placed = [];
      for (const location of locationsOf(volume)) {
        const key = location.x + ',' + location.y + ',' + location.z;
        if (!keep(typeAt(location), key)) continue;
        writes.push({ location, type: value.type, states: { ...value.states }, bulk: true });
        if (value.type === 'minecraft:air') blocks.delete(key); else blocks.set(key, value.type);
        states.set(key, { ...value.states });
        wet.delete(key);
        onSet({ ...location }, value);
        placed.push(location);
      }
      charge('fillBlocks', (costs?.fillBlocks ?? 0) + placed.length * (costs?.filled ?? 0));
      return new api.ListBlockVolume(placed);
    },
    containsBlock(volume, filter) {
      if (recordVolumes) volumes.push({ from: volume.from, to: volume.to, types: [...(filter.includeTypes ?? [])], exclude: [...(filter.excludeTypes ?? [])], contains: true });
      const found = matching(volume, filterOf(filter), true);
      charge('containsBlock', (costs?.containsBlock ?? 0) + cellsOf(volume) * (costs?.cell ?? 0));
      return found.length > 0;
    },
    getEntities({ type, tags = [], location, maxDistance, excludeTypes = [] } = {}) {
      charge('getEntities');
      return entities.filter(entity => entity.isValid && (!type || entity.typeId === type) && tags.every(tag => entity.hasTag(tag)) &&
        !excludeTypes.includes(entity.typeId) && (!location || maxDistance === undefined ||
          Math.hypot(entity.location.x - location.x, entity.location.y - location.y, entity.location.z - location.z) <= maxDistance));
    },
    playSound(id, location) { sounds.push({ id, location: { ...location } }); },
    spawnItem(itemStack, location) {
      const entity = dimension.spawnEntity('minecraft:item', location);
      entity.getComponent = name => name === 'minecraft:item' ? { itemStack } : undefined;
      return entity;
    },
    spawnEntity(typeId, location) {
      charge('spawnEntity');
      const dynamic = new Map(), values = new Map(), tags = new Set(), writes = [];
      const entity = { id: 'entity-' + ids++, typeId, location: { ...location }, dimension, isValid: true, writes,
        addTag: tag => tags.add(tag), removeTag: tag => tags.delete(tag), hasTag: tag => tags.has(tag), getTags: () => [...tags],
        remove() { this.isValid = false; }, teleport(value) { charge('entity'); this.location = { ...value }; }, setRotation(value) { this.rotation = value; },
        setProperty(name, value) { charge('entity'); writes.push(name); values.set(name, value); }, getProperty: name => values.get(name),
        setDynamicProperty: (name, value) => { charge('entity'); dynamic.set(name, value); }, getDynamicProperty: name => dynamic.get(name) };
      entities.push(entity);
      return entity;
    },
  };
  const world = {
    afterEvents: { playerPlaceBlock: channel(), playerBreakBlock: channel(), blockExplode: channel(), entityLoad: channel(), worldLoad: channel(),
      entityHitBlock: channel(), entitySpawn: channel(), dataDrivenEntityTrigger: channel(), pistonActivate: channel() },
    beforeEvents: { playerBreakBlock: channel(), playerInteractWithBlock: channel(), playerPlaceBlock: channel() },
    players: [],
    getAllPlayers() { return this.players; },
    sendMessage(message) { for (const player of this.players) player.sendMessage(message); },
    getDimension: () => dimension,
    getDynamicProperty: name => { charge('property'); return properties.get(name); },
    setDynamicProperty: (name, value) => { charge('property'); if (value === undefined) properties.delete(name); else properties.set(name, value); },
    structureManager: { place(name, target, location) { target.spawnEntity(name, { x: location.x + .5, y: location.y, z: location.z + .5 }); } },
    gameRules: { doTileDrops: true },
    getLootTableManager: () => ({ generateLootFromBlock: block => loot.of(block) }),
  };
  const system = {
    currentTick: 0,
    afterEvents: { scriptEventReceive: channel() },
    beforeEvents: { startup: channel(), shutdown: channel() },
    run(fn) { const id = next++; runs.set(id, fn); return id; },
    runInterval(fn) { const id = next++; intervals.set(id, fn); return id; },
    runTimeout(fn, ticks) { timeouts.push({ fn, ticks }); },
    runJob(job) { const id = next++; jobs.set(id, job); return id; },
    clearRun(id) { runs.delete(id); intervals.delete(id); },
    clearJob(id) { jobs.delete(id); },
    sendScriptEvent(id, message) { system.afterEvents.scriptEventReceive.emit({ id, message }); },
  };
  const api = { world, system, BlockVolume: class { constructor(from, to) { this.from = from; this.to = to; } },
    ListBlockVolume: class { constructor(locations) { this.locations = locations.map(at => ({ x: at.x, y: at.y, z: at.z })); }
      getBlockLocationIterator() { return this.locations[Symbol.iterator](); } },
    BlockTypes: { get: id => (!registry || registry.has(id) ? { id } : undefined) }, BlockPermutation: { resolve: (type, states = {}) => {
      checkStates(type, states);
      // Like the game: states not given take their first value, which getAllStates reports.
      const all = { ...Object.fromEntries([...(registry?.get(type) ?? new Map())].map(([name, values]) => [name, values[0]])), ...states };
      return { type, states, getAllStates: () => ({ ...all }) };
    } },
    GraphicsMode: { RayTraced: 'RayTraced', Fancy: 'Fancy', Deferred: 'Deferred', Simple: 'Simple' },
    ItemStack, ModalFormData: class {},
    // The engine's time budgets read this clock: it stands still unless a test moves it or game calls cost time (costs),
    // so runs do not depend on machine speed.
    now: () => clock.ms };
  /** Fires the startup event and keeps the block components the engine registers (components.get(name)). */
  function startup() {
    system.beforeEvents.startup.emit({ blockComponentRegistry: { registerCustomComponent: (name, component) => components.set(name, component) } });
    return components;
  }
  /** Runs deferred work, one interval pass and every queued scan to completion; stepTimes gets the clock time each pass took. */
  function step(passes = 1) {
    for (let pass = 0; pass < passes; pass++) {
      const start = clock.ms;
      flush();
      for (const fn of [...intervals.values()]) fn();
      system.currentTick += 4;
      flush();
      stepTimes.push(clock.ms - start);
    }
  }
  function flush() {
    for (let guard = 0; guard < 1000 && (runs.size || jobs.size); guard++) {
      for (const [id, fn] of [...runs]) { runs.delete(id); fn(); }
      // A job that keeps yielding without finishing (waiting for the next tick) goes on in the next flush.
      let waiting = false;
      for (const [id, job] of [...jobs]) {
        let result, count = 0;
        do { result = job.next(); } while (!result.done && ++count < 100000);
        if (result.done) jobs.delete(id); else waiting = true;
      }
      if (waiting && !runs.size) break;
    }
    for (const timeout of timeouts.splice(0)) timeout.fn();
  }
  // A player looks straight up unless a test turns them (view): nothing on the ground is in view.
  const player = (location, graphicsMode = 'Fancy') => {
    const result = { id: 'player-' + world.players.length, dimension, location: { ...location }, graphicsMode, messages: [], aim: undefined,
      view: { x: 0, y: 1, z: 0 },
      getHeadLocation() { return { x: this.location.x, y: this.location.y + 1.62, z: this.location.z }; },
      getViewDirection() { return { ...this.view }; },
      typeId: 'minecraft:player', sendMessage(message) { this.messages.push(message); },
      equipment: new Map(), gameMode: 'Survival', getGameMode() { return this.gameMode; },
      getComponent(name) {
        if (name === 'minecraft:equippable') return { getEquipment: slot => this.equipment.get(slot), setEquipment: (slot, item) => this.equipment.set(slot, item) };
        return undefined;
      },
      getBlockFromViewDirection() { return this.aim ? { block: dimension.getBlock(this.aim), face: 'Up' } : undefined; } };
    world.players.push(result);
    return result;
  };
  return { api, world, system, dimension, blocks, states, entities, volumes, properties, writes, step, flush, player, intervals, jobs,
    sounds, wet, loot, startup, components, ItemStack, clock, calls, stepTimes };
}

/**
 * Milliseconds a game call is taken to cost, for fakeBedrock({costs}): rough
 * figures for Bedrock's script bindings (a call crossing into the game, a
 * block read or write, a volume query by its cells and results).
 */
export const GAME_COSTS = Object.freeze({
  getBlock: 0.004, getState: 0.002, setPermutation: 0.03, getTopmostBlock: 0.01, isChunkLoaded: 0.002,
  getBlocks: 0.02, containsBlock: 0.01, cell: 0.00002, result: 0.0005, next: 0.0005, isInside: 0.0005,
  getEntities: 0.05, spawnEntity: 0.1, entity: 0.003, property: 0.01,
  // fillBlocks: one call, then a small native cost per block it sets.
  fillBlocks: 0.02, filled: 0.0005,
});
