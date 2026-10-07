/**
 * Native surface blocks: owned, replaceable blocks in an empty cell in front of
 * a host face; the host is never replaced.
 *
 * NativeSurfaces holds the generated terrain edges (above a host). Their
 * ownership is saved per chunk so a change rewrites only that chunk's record.
 * createOverlaySurfaces draws the pack author's overlay rules (below).
 */
import { createBulkWriter } from './bulk.mjs';
import { DIRECTIONS, vanillaView } from './core.mjs';
import { chooseTile } from './tiles.mjs';
import { overlaySelection, overlayComboIndex } from './overlay.mjs';

const INDEX = 'bct:native_chunks';
const chunkOf = (dimension, location) => dimension + ':' + Math.floor(location.x / 16) + ':' + Math.floor(location.z / 16);

export class NativeSurfaces {
  constructor(world, resolve, providers, limit = 96, clock = () => Date.now()/50, report = () => {}, heightProfile = () => 0, batchWrites = false) {
    this.world = world; this.resolve = resolve; this.providers = providers;
    this.limitOf = typeof limit === 'function' ? limit : () => limit;
    this.owners = new Map(); this.loaded = false;
    this.clock = clock; this.report = report; this.failures = new Map();
    this.heightProfile = heightProfile;
    this.batchWrites = batchWrites; this.dirtyChunks = new Set(); this.idCache = null;
  }
  get limit() { return this.limitOf(); }
  get size() { return this.owners.size; }
  ids() { return this.idCache ??= new Set([...this.owners.values()].map(entry => entry.type).concat(this.providers().flatMap(provider => Object.values(provider.effects).map(effect => effect.native_block).filter(Boolean)))); }
  offset(type) { return this.providers().flatMap(provider => Object.values(provider.effects)).find(effect => effect.native_block === type)?.native_offset ?? [...this.owners.values()].find(entry => entry.type === type)?.offset ?? 1; }
  key(dimension, location, offset = location.offset ?? 1) { return dimension.id + ':' + [location.x,location.y,location.z].join(',') + (offset === 1 ? '' : ':'+offset); }
  save(dimension, location) { this.dirtyChunks.add(chunkOf(dimension, location)); if (!this.batchWrites) this.flush(); }
  flush() {
    if (!this.dirtyChunks.size) return;
    const byChunk = new Map([...this.dirtyChunks].map(chunk => [chunk, []]));
    for (const entry of this.owners.values()) byChunk.get(chunkOf(entry.dimension, entry))?.push(entry);
    const index = new Set(this.readIndex());
    for (const [chunk, entries] of byChunk) {
      this.world.setDynamicProperty('bct:native:' + chunk, entries.length ? JSON.stringify(entries) : undefined);
      if (entries.length) index.add(chunk); else index.delete(chunk);
    }
    this.world.setDynamicProperty(INDEX, index.size ? JSON.stringify([...index]) : undefined);
    this.dirtyChunks.clear();
  }
  readIndex() {
    try { const chunks = JSON.parse(this.world.getDynamicProperty(INDEX) ?? '[]'); return Array.isArray(chunks) ? chunks : []; }
    catch { return []; }
  }
  recover() {
    if (this.loaded) return;
    this.loaded = true;
    for (const chunk of this.readIndex()) {
      let entries;
      try { entries = JSON.parse(this.world.getDynamicProperty('bct:native:' + chunk) ?? '[]'); } catch { continue; }
      if (!Array.isArray(entries)) continue;
      for (const entry of entries) {
        if (this.owners.size >= this.limit) return;
        if (!entry || typeof entry.type !== 'string' || !/^[a-z0-9_]+:[a-z0-9_]+$/.test(entry.type) || !['x','y','z'].every(axis => Number.isInteger(entry[axis])) || typeof entry.dimension !== 'string') continue;
        if (entry.offset !== undefined && ![1,2].includes(entry.offset)) continue;
        this.owners.set(this.key({id:entry.dimension},entry),entry);
        this.idCache=null;
      }
    }
  }
  sync(dimension, location, outputs, capacity = this.limit) {
    this.recover();
    const ids = this.ids();
    const base = dimension.getBlock({x:location.x,y:location.y+1,z:location.z});
    if (!base) return;
    const exposed = base.isAir || ids.has(base.typeId);
    const wanted = outputs.map(output => {
      const provider = this.providers().find(provider => provider.id === output.provider);
      const effect = provider && Object.values(provider.effects).find(effect => effect.entity === output.entity);
      return effect?.native_block && !effect.native_disabled && !effect.entity_surface ? {output,effect} : null;
    }).filter(Boolean).sort((a,b) => (b.effect.priority ?? 0)-(a.effect.priority ?? 0) || a.effect.entity.localeCompare(b.effect.entity));
    // Select once across all carrier groups so a contact has one source material.
    const edges = Array.from({length:4},(_,i) => wanted.find(item => item.output.tile & (1<<i)));
    const corners = Array.from({length:4},(_,i) => wanted.find(item => item.output.tile & (1<<(i+4))));
    const groups = new Map();
    for (const provider of this.providers()) for (const effect of Object.values(provider.effects)) {
      if (effect.native_block) groups.set(effect.native_block,effect.native_offset ?? 1);
    }
    if(!this.staleGroups) this.staleGroups=new Map([...this.owners.values()].map(entry=>[entry.type,entry.offset ?? 1]));
    for(const [type,offset] of this.staleGroups) if(!groups.has(type)) groups.set(type,offset);
    // A cell holds one block: where edges of different blocks meet one host, the highest priority one draws.
    const winners = new Map();
    for (const item of wanted) { const offset = item.effect.native_offset ?? 1; if (!winners.has(offset)) winners.set(offset, item.effect.native_block); }
    for (const [type,offset] of groups) {
      const cell = offset === 1 ? base : dimension.getBlock({x:location.x,y:location.y+offset,z:location.z});
      if (!cell) continue;
      const key = this.key(dimension,location,offset);
      const selected = winners.get(offset) === type ? wanted.filter(item => item.effect.native_block === type) : [];
      const available = cell.isAir || ids.has(cell.typeId);
      if (!exposed || !available || !selected.length) {
        if (cell.typeId === type) cell.setType('minecraft:air');
        if (this.owners.get(key)?.type === type && this.owners.delete(key)) this.save(dimension.id, location);
        continue;
      }
      let states;
      if (selected[0].effect.native_material) {
        states = {};
        for (let index=0;index<4;index++) {
          const e = edges[index]?.effect.native_block === type ? edges[index].effect.native_material : 0;
          const c = corners[index]?.effect.native_block === type ? corners[index].effect.native_material : 0;
          if (e && e === c) throw new Error('Redundant native corner');
          states['bct:quadrant'+index] = e+4*c;
        }
        if (Object.values(states).every(value => value === 0)) {
          if (cell.typeId === type) cell.setType('minecraft:air');
          if (this.owners.get(key)?.type === type && this.owners.delete(key)) this.save(dimension.id, location);
          continue;
        }
      } else states = {'bct:edges':selected[0].output.tile&15,'bct:corners':selected[0].output.tile>>4};
      if (selected.every(item => Array.isArray(item.effect.height_profiles))) {
        states['bct:height_profile'] = this.heightProfile();
      }
      if (cell.isAir && !this.owners.has(key) && this.size >= capacity) continue;
      if ((this.failures.get(type) ?? -1) > this.clock()) continue;
      try {
        if (cell.typeId !== type || Object.entries(states).some(([name,value]) => cell.permutation.getState(name) !== value)) cell.setPermutation(this.resolve(type,states));
        this.failures.delete(type);
      } catch (error) {
        this.failures.set(type,this.clock()+1200);
        this.report('native carrier '+JSON.stringify({type,host:location,cell:{x:location.x,y:location.y+offset,z:location.z},states,error:String(error)}));
        continue;
      }
      if (this.owners.get(key)?.type !== type) {
        this.owners.set(key,{dimension:dimension.id,...location,type,offset});
        this.save(dimension.id, location);
      }
    }
  }

  remove(entry) {
    try {
      const dimension = this.world.getDimension(entry.dimension);
      const cell = dimension.getBlock({x:entry.x,y:entry.y+(entry.offset ?? 1),z:entry.z});
      if (!cell) return false;
      if (cell.typeId === entry.type) cell.setType('minecraft:air');
      this.owners.delete(this.key(dimension,entry));
      this.save(entry.dimension, entry);
      return true;
    } catch { return false; }
  }
  clear() { this.recover(); for (const entry of [...this.owners.values()]) this.remove(entry); }
}

/**
 * Overlay surfaces: the pack author's overlay rules (overlay, overlay_ctm,
 * overlay_random, overlay_repeat, overlay_fixed) drawn with native blocks, so
 * they show in Classic, Vibrant Visuals and ray tracing.
 *
 * A surface is a custom block in the empty cell in front of a face of a host
 * block: above the host for its top face, beside it for a side face, below it
 * for its bottom face. Its geometry is a quad just outside each host face it
 * draws, showing the overlay tiles the rule picks for that face. One cell can
 * draw onto every block around it. The converter writes surface types, each a
 * set of channels (one host face and the rules and tiles it can show); every
 * cell gets the type that draws the most, the top face first, then the sides,
 * then the bottom face. Faces left out are counted in `status.dropped`.
 *
 * Surfaces only go into air or into a cell that holds a surface; a cell with
 * any other block (plants, snow, water) gets none. A cell that holds a
 * generated terrain edge block (`foreign()`) is taken over: the pack's own
 * overlay wins. Hosts and neighbors are read through the vanilla view, so a
 * replacement block reads as the vanilla block it stands for; a rule its
 * replacement already draws (`replaced`) is left out on it while it is swapped.
 *
 * Cells are refreshed when a block near them changes, when the scanner finds a
 * block that gives or receives an overlay, when the area is scanned again and
 * when a replacement is swapped in. Work runs in `tick`, until the time the
 * engine's shared budget gives it (sliceMs at most).
 * Chunks holding surfaces are indexed in world dynamic properties, so turning
 * the engine off removes them from every chunk as it loads.
 */

const FACES = ['up', 'north', 'south', 'west', 'east', 'down'];
const vector = ([x, y, z]) => ({ x, y, z });
const NORMAL = Object.fromEntries(FACES.map(face => [face, vector(DIRECTIONS[face])]));
// A cell's host for a face lies behind that face's normal: the 'up' face is the top of the block below.
const HOST = Object.fromEntries(FACES.map(face => [face, vector(DIRECTIONS[face].map(value => -value))]));
// In-plane neighbors (edges and diagonals) of each face.
const PLANE = Object.fromEntries(FACES.map(face => {
  const axes = ['x', 'y', 'z'].filter(axis => NORMAL[face][axis] === 0), result = [];
  for (const a of [-1, 0, 1]) for (const b of [-1, 0, 1]) if (a || b) result.push({ x: 0, y: 0, z: 0, [axes[0]]: a, [axes[1]]: b });
  return [face, result];
}));
const WEIGHT = { up: 1000, north: 10, south: 10, west: 10, east: 10, down: 1 };
const METHODS = new Set(['overlay', 'overlay_ctm', 'overlay_random', 'overlay_repeat', 'overlay_fixed']);
const BLOCK_ID = /^[a-z][a-z0-9_]*:[a-z0-9_.]+$/;
const STATE_NAME = /^[a-z][a-z0-9_]*:[a-z0-9_]+$/;
const SNOW = new Set(['minecraft:snow', 'minecraft:snow_layer']);
const OVERLAY_INDEX = 'bct:overlay_index';
const regionOf = (dimension, x, z) => dimension + ':' + (Math.floor(x / 16) >> 5) + ':' + (Math.floor(z / 16) >> 5);
const overlayChunkOf = (x, z) => Math.floor(x / 16) + ',' + Math.floor(z / 16);

function fail(message) { throw new Error('Invalid overlay data: ' + message); }
const isIndexList = value => Array.isArray(value) && value.every(item => Number.isInteger(item) && item >= 0);
const isIdList = value => Array.isArray(value) && value.every(item => typeof item === 'string' && BLOCK_ID.test(item));

function faceTable(faces, where) {
  if (!faces || typeof faces !== 'object') fail(where + ' faces');
  const result = {};
  for (const [face, entry] of Object.entries(faces)) {
    if (!FACES.includes(face) || !Array.isArray(entry) || !entry.length || !Number.isInteger(entry[0]) || entry[0] < 0 || entry[0] > 7 ||
      !entry.slice(1).every(item => Number.isInteger(item) && item >= -1)) fail(where + ' face ' + face);
    result[face] = { orientation: entry[0], base: entry[1] ?? -1, textures: entry.slice(1).filter(item => item >= 0) };
  }
  return result;
}

function stateTable(states, where) {
  if (states === undefined) return undefined;
  if (!states || typeof states !== 'object') fail(where + ' states');
  return Object.fromEntries(Object.entries(states).map(([name, value]) => [name, (Array.isArray(value) ? value : [value]).map(String)]));
}

/** Checks one converted pack's overlay data and builds its lookups. */
export function compileOverlays(data, id = 'pack') {
  if (!data || data.format_version !== 1) fail('format_version');
  if (!['optifine', 'continuity'].includes(data.dialect ?? 'optifine')) fail('dialect');
  for (const field of ['cubes', 'solid', 'scan']) if (!isIdList(data[field] ?? [])) fail(field);
  if (!Array.isArray(data.rules) || data.rules.length > 256) fail('rules');
  if (!Array.isArray(data.types) || !data.types.length || data.types.length > 64) fail('types');
  const blocks = new Map();
  for (const [type, entry] of Object.entries(data.blocks ?? {})) {
    if (!BLOCK_ID.test(type) || !entry || typeof entry !== 'object') fail('block ' + type);
    blocks.set(type, { faces: faceTable(entry.faces ?? {}, type),
      variants: (entry.variants ?? []).map((variant, index) => ({ states: stateTable(variant.states ?? {}, type + ' variant ' + index),
        faces: faceTable(variant.faces ?? {}, type + ' variant ' + index) })) });
  }
  const shapes = new Map();
  for (const [type, entries] of Object.entries(data.shapes ?? {})) {
    if (!BLOCK_ID.test(type) || !Array.isArray(entries)) fail('shape ' + type);
    shapes.set(type, entries.map(entry => {
      const faces = entry.faces ?? {};
      if (Object.entries(faces).some(([face, offset]) => !FACES.includes(face) || !Number.isInteger(offset) || offset < -15 || offset > 0)) fail('shape ' + type);
      return { states: stateTable(entry.states, type), faces };
    }));
  }
  const rules = data.rules.map((rule, index) => {
    const where = 'rule ' + index;
    if (!rule || typeof rule.id !== 'string' || !METHODS.has(rule.method)) fail(where + ' method');
    if (!Array.isArray(rule.faces) || !rule.faces.length || rule.faces.some(face => !FACES.includes(face))) fail(where + ' faces');
    if (!Number.isInteger(rule.tiles) || rule.tiles < 1 || rule.tiles > 256 || !isIndexList(rule.skip ?? [])) fail(where + ' tiles');
    for (const field of ['matchTiles', 'connectTiles']) if (rule[field] !== undefined && !isIndexList(rule[field])) fail(where + ' ' + field);
    for (const field of ['blocks', 'connectBlocks', 'replaced']) if (rule[field] !== undefined && !isIdList(rule[field])) fail(where + ' ' + field);
    if (rule.weights !== undefined && (!Array.isArray(rule.weights) || rule.weights.some(weight => !Number.isInteger(weight) || weight < 0))) fail(where + ' weights');
    if (rule.method === 'overlay' && rule.tiles !== 17) fail(where + ' needs 17 tiles');
    if (rule.method === 'overlay_repeat' && (!Number.isInteger(rule.width) || !Number.isInteger(rule.height) || rule.width * rule.height !== rule.tiles)) fail(where + ' repeat size');
    if (rule.connect !== undefined && !['block', 'tile', 'state'].includes(rule.connect)) fail(where + ' connect');
    if (rule.heights !== undefined && (!Array.isArray(rule.heights) || rule.heights.some(range => !Array.isArray(range) || range.length !== 2 || !range.every(Number.isInteger)))) fail(where + ' heights');
    if (rule.biomes !== undefined && (!rule.biomes || !Array.isArray(rule.biomes.ids) || typeof rule.biomes.exclude !== 'boolean')) fail(where + ' biomes');
    const skip = new Set(rule.skip ?? []);
    return { ...rule, index, faces: new Set(rule.faces), skipSet: skip,
      matchTiles: rule.matchTiles && new Set(rule.matchTiles), connectTiles: rule.connectTiles && new Set(rule.connectTiles),
      blocks: rule.blocks?.length ? new Set(rule.blocks) : undefined, connectBlocks: rule.connectBlocks && new Set(rule.connectBlocks),
      matchers: (rule.matchers ?? []).map(clause => ({ block: clause.block, states: stateTable(clause.states, where) })),
      replaced: new Set(rule.replaced ?? []), biomes: rule.biomes && { ids: new Set(rule.biomes.ids), exclude: rule.biomes.exclude },
      connectMode: rule.connect ?? (rule.blocks?.length || rule.matchers?.length ? 'block' : rule.matchTiles ? 'tile' : 'block'),
      // chooseTile reads the tile list, weights and repeat size; tiles of overlays are never turned.
      tileRule: { method: rule.method.slice('overlay_'.length), tiles: Array.from({ length: rule.tiles }, (_, tile) => skip.has(tile) ? '<skip>' : 'tile'),
        weights: rule.weights, randomLoops: rule.randomLoops, symmetry: rule.symmetry, linked: rule.linked, width: rule.width, height: rule.height,
        orient: rule.orient ?? 'none', connect: rule.connect, innerSeams: rule.innerSeams, matchTiles: rule.matchTiles } };
  });
  const surfaceTypes = new Set();
  const types = data.types.map((type, index) => {
    if (!type || !BLOCK_ID.test(type.block) || surfaceTypes.has(type.block)) fail('type ' + index);
    surfaceTypes.add(type.block);
    if (!Array.isArray(type.channels) || !type.channels.length || type.channels.length > 12) fail('type ' + index + ' channels');
    const channels = type.channels.map((channel, number) => {
      const where = 'type ' + index + ' channel ' + number;
      if (!FACES.includes(channel.face) || !Number.isInteger(channel.offset ?? 0) || !Array.isArray(channel.options) || !Array.isArray(channel.states)) fail(where);
      const accepts = new Map();
      let size = 1;
      for (const [rule, values] of channel.options) {
        if (!rules[rule] || !isIndexList(values)) fail(where + ' options');
        for (const value of values) accepts.set(rule * 4096 + value, size++);
      }
      let capacity = 1;
      for (const [name, count] of channel.states) {
        if (typeof name !== 'string' || !STATE_NAME.test(name) || !Number.isInteger(count) || count < 1 || count > 16) fail(where + ' states');
        capacity *= count;
      }
      if (capacity < size) fail(where + ' states hold fewer values than the channel needs');
      return { face: channel.face, offset: channel.offset ?? 0, accepts, states: channel.states };
    });
    return { block: type.block, channels, broken: false };
  });
  // Blocks that give an overlay to their neighbors on a face (sources) and blocks that show one by themselves (hosts).
  const faceLists = (table, name) => new Map(Object.entries(table ?? {}).map(([type, faces]) => {
    if (!BLOCK_ID.test(type) || !Array.isArray(faces) || faces.some(face => !FACES.includes(face))) fail(name + ' ' + type);
    return [type, faces];
  }));
  return { id, dialect: data.dialect ?? 'optifine', blocks, shapes, rules, types, surfaceTypes,
    cubes: new Set(data.cubes ?? []), solid: new Set(data.solid ?? []), scan: data.scan ?? [],
    sourceFaces: faceLists(data.sources, 'source'), hostFaces: faceLists(data.hosts, 'host') };
}

/** A block read for one refresh: replacements read as their vanilla block; neighbors come from the same cache. */
class Cell {
  constructor(reader, raw) {
    this.reader = reader; this.raw = raw;
    const { x, y, z } = raw.location;
    this.x = x; this.y = y; this.z = z;
    this.typeId = reader.view.vanillaType(raw.typeId);
    this.states = undefined;
  }
  get location() { return { x: this.x, y: this.y, z: this.z }; }
  get dimension() { return this.raw.dimension; }
  get isAir() { return this.raw.isAir; }
  get replaced() { return this.typeId !== this.raw.typeId; }
  get permutation() {
    if (!this.replaced) return this.raw.permutation;
    const states = this.states ??= this.reader.view.statesOf(this.raw);
    return { getState: name => states[name] ?? states[name.split(':').pop()], getAllStates: () => ({ ...states }) };
  }
  offset(delta) { return this.reader.get(this.x + delta.x, this.y + delta.y, this.z + delta.z); }
}

/** Cached block reads within one tick: undefined while a chunk is not loaded, null outside the world. */
function createReader(dimension, view) {
  const cache = new Map();
  const reader = { view, dimension,
    get(x, y, z) {
      const key = x + ',' + y + ',' + z;
      if (cache.has(key)) return cache.get(key);
      let value;
      try {
        const raw = dimension.getBlock({ x, y, z });
        value = raw ? new Cell(reader, raw) : undefined;
      } catch (error) { value = error?.name === 'LocationOutOfWorldBoundariesError' ? null : undefined; }
      cache.set(key, value);
      return value;
    },
    forget(x, y, z) { cache.delete(x + ',' + y + ',' + z); } };
  return reader;
}

/**
 * api: @minecraft/server objects. limits(): the `overlay` settings.
 * vanillaType/vanillaStates: how replacement blocks read (see core.mjs vanillaView);
 * aliasesOf(type): replacement types the scanner also looks for.
 * foreign(): block types of other surfaces a cell may hold (generated terrain edges); an overlay replaces them.
 */
export function createOverlaySurfaces({ api, limits, log = () => {}, vanillaType = typeId => typeId, vanillaStates = undefined,
  aliasesOf = () => [], foreign = () => new Set() }) {
  const { world } = api;
  const now = () => (api.now ?? Date.now)();
  const view = vanillaView(vanillaType, vanillaStates);
  const sources = new Map();
  let providers = [], surfaceTypes = new Set();
  const events = new Map(), scans = new Map(), known = new Map();
  const index = new Map(), dirtyRegions = new Set();
  let indexLoaded = false, enabled = true, cleanup = null, validator = known.values();
  const counters = { placed: 0, updated: 0, removed: 0, dropped: 0, unknown: 0, failed: 0, cleaned: 0 };

  function rebuild() {
    providers = [...sources.values()].sort((a, b) => a.id.localeCompare(b.id));
    surfaceTypes = new Set(providers.flatMap(provider => [...provider.surfaceTypes]));
  }

  // ---- ownership index (chunks that may hold surfaces) ----
  function loadIndex() {
    if (indexLoaded) return;
    indexLoaded = true;
    let regions = [];
    try { regions = JSON.parse(world.getDynamicProperty(OVERLAY_INDEX) ?? '[]'); } catch { regions = []; }
    for (const region of Array.isArray(regions) ? regions : []) {
      let chunks = [];
      try { chunks = JSON.parse(world.getDynamicProperty('bct:overlay:' + region) ?? '[]'); } catch { chunks = []; }
      if (Array.isArray(chunks) && chunks.length) index.set(region, new Set(chunks.filter(chunk => typeof chunk === 'string')));
    }
  }
  function remember(dimensionId, x, z) {
    loadIndex();
    const region = regionOf(dimensionId, x, z), chunk = overlayChunkOf(x, z);
    if (!index.has(region)) index.set(region, new Set());
    if (index.get(region).has(chunk)) return;
    index.get(region).add(chunk);
    dirtyRegions.add(region);
  }
  function saveIndex() {
    if (!dirtyRegions.size) return;
    for (const region of dirtyRegions) {
      const chunks = index.get(region);
      if (!chunks?.size) index.delete(region);
      world.setDynamicProperty('bct:overlay:' + region, chunks?.size ? JSON.stringify([...chunks]) : undefined);
    }
    world.setDynamicProperty(OVERLAY_INDEX, index.size ? JSON.stringify([...index.keys()]) : undefined);
    dirtyRegions.clear();
  }

  // ---- what a host face shows ----
  function stateValue(block, name) {
    if (name === 'bct:snowy') {
      const above = block.offset({ x: 0, y: 1, z: 0 });
      return above === undefined ? undefined : SNOW.has(above?.typeId);
    }
    return block.permutation?.getState?.(name);
  }
  const statesMatch = (states, block) => Object.entries(states ?? {}).every(([name, values]) => {
    const value = stateValue(block, name);
    return value !== undefined && values.includes(String(value));
  });
  function faceOf(provider, block, face) {
    const entry = provider.blocks.get(block.typeId);
    if (!entry) return undefined;
    const variant = entry.variants.find(item => statesMatch(item.states, block));
    return (variant ?? entry).faces[face];
  }
  /** Pixel offset of a host face that is a whole square (full cubes 0, a path top -1); undefined when it is not. */
  function hostOffset(provider, block, face) {
    if (provider.cubes.has(block.typeId)) return 0;
    const shape = provider.shapes.get(block.typeId)?.find(entry => statesMatch(entry.states, block));
    return shape?.faces[face];
  }
  const matchesBlock = (rule, block) => (!rule.blocks && !rule.matchers.length) || rule.blocks?.has(block.typeId) ||
    rule.matchers.some(clause => clause.block === block.typeId && statesMatch(clause.states, block));
  /** The host texture a rule matched on a face, or null when the rule does not draw there. */
  function matchedTexture(rule, block, face, textures) {
    if (!matchesBlock(rule, block)) return null;
    if (!rule.matchTiles) return textures?.base ?? -1;
    if (!textures) return null;
    const texture = textures.textures.find(item => rule.matchTiles.has(item));
    return texture === undefined ? null : texture;
  }

  function overlayValue(provider, rule, host, face, hostTexture) {
    const dialect = provider.dialect;
    const textures = other => faceOf(provider, other, face);
    const connects = other => {
      if (rule.connectMode === 'block') return other.typeId === host.typeId;
      if (rule.connectMode === 'state') return other.typeId === host.typeId &&
        JSON.stringify(other.permutation?.getAllStates?.() ?? {}) === JSON.stringify(host.permutation?.getAllStates?.() ?? {});
      const found = textures(other);
      return dialect === 'optifine' ? found?.base === hostTexture : !!found?.textures.includes(hostTexture);
    };
    const applies = other => {
      if (!provider.cubes.has(other.typeId) || connects(other)) return false;
      if (rule.connectBlocks && !rule.connectBlocks.has(other.typeId)) return false;
      if (rule.connectTiles && !textures(other)?.textures.some(texture => rule.connectTiles.has(texture))) return false;
      return true;
    };
    const same = other => {
      if (!matchesBlock(rule, other)) return false;
      if (!rule.matchTiles) return true;
      const found = textures(other);
      return dialect === 'optifine' ? found?.base === hostTexture : !!found?.textures.some(texture => rule.matchTiles.has(texture));
    };
    const around = overlaySelection(host, face, { applies, same, solid: other => provider.solid.has(other.typeId), dialect });
    return around.known ? overlayComboIndex(around.edges, around.corners) : undefined;
  }

  function tileValue(provider, rule, host, face, hostTexture) {
    const textureOf = (block, side) => {
      const found = faceOf(provider, block, side);
      return found && found.base >= 0 ? found.base : '?' + block.typeId;
    };
    const providers = { opaque: other => provider.solid.has(other.typeId), logicalBlockOf: block => block.typeId,
      orientationOf: (block, side) => faceOf(provider, block, side)?.orientation ?? 0 };
    const selected = chooseTile(rule.tileRule, host, face, textureOf, hostTexture >= 0 ? hostTexture : textureOf(host, face), providers);
    if (!selected.known) return undefined;
    if (!selected.visible || rule.skipSet.has(selected.tile)) return 0;
    return selected.tile + 1;
  }

  /**
   * The overlay layers one host face shows, in rule order: [{rule, value}],
   * where value is the overlay case (OVERLAY_COMBOS index) or tile + 1. null while a neighbor is unloaded.
   */
  function faceLayers(provider, host, face) {
    const offset = hostOffset(provider, host, face);
    if (offset === undefined) return { offset, layers: [] };
    const textures = faceOf(provider, host, face);
    const layers = [];
    for (const rule of provider.rules) {
      if (!rule.faces.has(face)) continue;
      const hostTexture = matchedTexture(rule, host, face, textures);
      if (hostTexture === null) continue;
      if (rule.heights && !rule.heights.some(([low, high]) => host.y >= low && host.y <= high)) continue;
      if (rule.replaced.size && host.replaced && rule.replaced.has(host.typeId)) continue;
      if (rule.biomes) {
        let biome;
        try { biome = host.dimension.getBiome(host.location)?.id; } catch { biome = undefined; }
        if (biome === undefined) return null;
        if (rule.biomes.ids.has(biome) === rule.biomes.exclude) continue;
      }
      const value = rule.method === 'overlay' ? overlayValue(provider, rule, host, face, hostTexture) : tileValue(provider, rule, host, face, hostTexture);
      if (value === undefined) return null;
      if (value) layers.push({ rule: rule.index, value });
    }
    return { offset, layers };
  }

  /** Fits every face's layers into one surface type's channels; most important faces first. */
  function choose(provider, needs) {
    let best = null;
    for (const type of provider.types) {
      if (type.broken) continue;
      const values = new Array(type.channels.length).fill(0);
      let score = 0, placed = 0;
      for (const [face, need] of needs) {
        const free = type.channels.map((channel, index) => index).filter(index => type.channels[index].face === face && type.channels[index].offset === need.offset);
        // Small search: each layer takes a free channel that accepts it, as many layers as possible.
        let bestSet = [];
        const search = (layer, used, chosen) => {
          if (chosen.length > bestSet.length) bestSet = [...chosen];
          if (layer >= need.layers.length || bestSet.length === need.layers.length) return;
          const { rule, value } = need.layers[layer];
          for (const index of free) {
            if (used.has(index)) continue;
            const option = type.channels[index].accepts.get(rule * 4096 + value);
            if (!option) continue;
            used.add(index); chosen.push([index, option]);
            search(layer + 1, used, chosen);
            used.delete(index); chosen.pop();
          }
          search(layer + 1, used, chosen);
        };
        search(0, new Set(), []);
        for (const [index, option] of bestSet) values[index] = option;
        score += WEIGHT[face] * bestSet.length;
        placed += bestSet.length;
      }
      if (placed && (!best || score > best.score)) best = { type, values, score, placed };
    }
    return best;
  }

  function statesOf(type, values) {
    const states = {};
    type.channels.forEach((channel, index) => {
      let rest = values[index];
      for (const [name, count] of channel.states) { states[name] = rest % count; rest = Math.floor(rest / count); }
    });
    return states;
  }

  /**
   * Brings one cell up to date. false when a needed block is not loaded (the cell keeps what it has).
   * With a bulk writer the new surface is queued there (sent once per tick) instead of set at once.
   */
  function refresh(reader, x, y, z, writer) {
    const cell = reader.get(x, y, z);
    if (cell === undefined) return false;
    if (cell === null) return true;
    const raw = cell.raw, current = raw.typeId, ours = surfaceTypes.has(current);
    const key = reader.dimension.id + ':' + x + ',' + y + ',' + z;
    if (!ours && !raw.isAir && !foreign().has(current)) { known.delete(key); return true; }
    // The first pack (by key) with anything to draw here owns the cell.
    let chosen = null;
    for (const provider of providers) {
      const needs = new Map();
      let layerCount = 0;
      for (const face of FACES) {
        const delta = HOST[face];
        const host = reader.get(x + delta.x, y + delta.y, z + delta.z);
        if (host === undefined) return false;
        if (!host || host.isAir) continue;
        const found = faceLayers(provider, host, face);
        if (found === null) return false;
        if (found.layers.length) { needs.set(face, found); layerCount += found.layers.length; }
      }
      if (!needs.size) continue;
      chosen = choose(provider, needs);
      if (chosen) { counters.dropped += layerCount - chosen.placed; break; }
      counters.dropped += layerCount;
    }
    try {
      if (!chosen) {
        if (ours) { raw.setType('minecraft:air'); counters.removed++; reader.forget(x, y, z); }
        known.delete(key);
        return true;
      }
      const states = statesOf(chosen.type, chosen.values);
      const same = current === chosen.type.block && Object.entries(states).every(([name, value]) => raw.permutation?.getState?.(name) === value);
      if (!same) {
        if (writer) writer.set(reader.dimension, { x, y, z }, chosen.type.block, states);
        else raw.setPermutation(api.BlockPermutation.resolve(chosen.type.block, states));
        if (ours) counters.updated++; else counters.placed++;
        reader.forget(x, y, z);
        remember(reader.dimension.id, x, z);
      }
      known.set(key, { dimension: reader.dimension, x, y, z });
    } catch (error) {
      counters.failed++;
      if (chosen && !surfaceTypes.has(raw.typeId)) {
        // A type the game does not know (its definition failed to load) is not tried again.
        try { if (!api.BlockTypes?.get?.(chosen.type.block)) chosen.type.broken = true; } catch { chosen.type.broken = true; }
      }
      log('overlay surface ' + JSON.stringify({ x, y, z }) + ': ' + String(error));
    }
    return true;
  }

  function enqueue(queue, dimension, x, y, z) {
    if (queue.size >= 65536) return;
    const key = dimension.id + ':' + x + ',' + y + ',' + z;
    if (!queue.has(key)) queue.set(key, { dimension, x, y, z });
  }

  // ---- engine hooks ----
  /** A block changed: the 27 cells around it may gain, change or lose a surface (at once with `immediate`). */
  function changed(block, { immediate = false } = {}) {
    if (!providers.length || !enabled || !block) return;
    const { x, y, z } = block.location;
    // A player's own block: the 27 cells update in the same tick, like Java redraws the neighbors at once.
    const reader = immediate ? createReader(block.dimension, view) : undefined;
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (let dz = -1; dz <= 1; dz++) {
      if (reader) {
        events.delete(block.dimension.id + ':' + (x + dx) + ',' + (y + dy) + ',' + (z + dz));
        try { if (refresh(reader, x + dx, y + dy, z + dz)) continue; } catch (error) { log('overlay ' + String(error)); }
      }
      enqueue(events, block.dimension, x + dx, y + dy, z + dz);
    }
  }
  /** A replacement was swapped in: the six cells around it draw or leave out the rules it draws itself. */
  function swapped(dimension, location) {
    if (!providers.length || !enabled) return;
    for (const face of FACES) enqueue(events, dimension, location.x + NORMAL[face].x, location.y + NORMAL[face].y, location.z + NORMAL[face].z);
  }

  /**
   * Scanner: a source, a host or a surface was found. A scan's context holds its
   * open cells (air and surfaces) and its covered ones (solid blocks): a source
   * under a plant still gives its overlay, a source under a solid block does not.
   */
  function found(dimension, location, typeId, context, topOnly = false) {
    if (!providers.length || !enabled) return;
    const { x, y, z } = location;
    const open = (px, py, pz) => !context?.open || context.open.isInside({ x: px, y: py, z: pz });
    const covered = (px, py, pz) => !!context?.covered && context.covered.isInside({ x: px, y: py, z: pz });
    if (surfaceTypes.has(typeId)) { enqueue(scans, dimension, x, y, z); return; }
    const vanilla = vanillaType(typeId);
    for (const provider of providers) {
      for (const face of provider.sourceFaces.get(vanilla) ?? []) {
        if (topOnly && face !== 'up') continue;
        const n = NORMAL[face];
        if (covered(x + n.x, y + n.y, z + n.z)) continue;
        for (const e of PLANE[face]) {
          const cx = x + e.x + n.x, cy = y + e.y + n.y, cz = z + e.z + n.z;
          if (open(cx, cy, cz)) enqueue(scans, dimension, cx, cy, cz);
        }
      }
      for (const face of provider.hostFaces.get(vanilla) ?? []) {
        if (topOnly && face !== 'up') continue;
        const n = NORMAL[face];
        if (open(x + n.x, y + n.y, z + n.z)) enqueue(scans, dimension, x + n.x, y + n.y, z + n.z);
      }
    }
  }
  /** A slab's context for found, in two steps (the scanner runs it as a generator). */
  function* prepare(dimension, record, low, high) {
    if (!providers.length || !enabled) return undefined;
    const x0 = record.x * 16 - 2, z0 = record.z * 16 - 2;
    const volume = new api.BlockVolume({ x: x0, y: low - 2, z: z0 }, { x: x0 + 19, y: high + 2, z: z0 + 19 });
    const solid = [...new Set(providers.flatMap(provider => [...provider.solid]))];
    try {
      const open = dimension.getBlocks(volume, { includeTypes: ['minecraft:air', ...surfaceTypes, ...foreign()] }, true);
      yield;
      return { open, covered: solid.length ? dimension.getBlocks(volume, { includeTypes: solid.flatMap(type => [type, ...aliasesOf(type)]) }, true) : undefined };
    } catch (error) { log('overlay scan ' + record.key + ': ' + String(error)); return undefined; }
  }

  // ---- cleanup after the engine is turned off ----
  function cleanChunk(dimension, cx, cz) {
    const range = dimension.heightRange;
    const volume = new api.BlockVolume({ x: cx * 16, y: range.min, z: cz * 16 }, { x: cx * 16 + 15, y: range.max - 1, z: cz * 16 + 15 });
    const types = [...surfaceTypes];
    if (types.length) {
      for (const location of dimension.getBlocks(volume, { includeTypes: types }, true).getBlockLocationIterator()) {
        const block = dimension.getBlock(location);
        if (block && surfaceTypes.has(block.typeId)) { block.setType('minecraft:air'); counters.cleaned++; }
      }
    }
  }
  /** Removes the surfaces of indexed chunks near players; chunks nobody is near wait until someone is. */
  function runCleanup(players, until) {
    loadIndex();
    if (!surfaceTypes.size || !players.length) return;
    // Chunk positions of the players, by dimension: only chunks within 16 chunks of one are looked at.
    const near = players.map(player => ({ dimension: player.dimension.id, cx: Math.floor(player.location.x / 16), cz: Math.floor(player.location.z / 16) }));
    for (const [region, chunks] of index) {
      const [namespace, name, rx, rz] = region.split(':');
      const dimensionId = namespace + ':' + name;
      // A region is 32 by 32 chunks; skip it when no player is near any of them.
      if (!near.some(player => player.dimension === dimensionId && player.cx >= +rx * 32 - 16 && player.cx < +rx * 32 + 48 &&
        player.cz >= +rz * 32 - 16 && player.cz < +rz * 32 + 48)) continue;
      let dimension;
      try { dimension = world.getDimension(dimensionId); } catch { continue; }
      for (const chunk of [...chunks]) {
        if (now() >= until) { saveIndex(); return; }
        const [cx, cz] = chunk.split(',').map(Number);
        if (!near.some(player => player.dimension === dimensionId && Math.max(Math.abs(player.cx - cx), Math.abs(player.cz - cz)) <= 16)) continue;
        try { if (!dimension.isChunkLoaded({ x: cx * 16, y: 0, z: cz * 16 })) continue; } catch { continue; }
        try { cleanChunk(dimension, cx, cz); chunks.delete(chunk); dirtyRegions.add(region); }
        catch (error) { log('overlay cleanup ' + chunk + ': ' + String(error)); }
      }
    }
    saveIndex();
  }

  /**
   * Per-tick work until `until` (a now() time; sliceMs from now by default): queued cells, a few known
   * surfaces checked again, and cleanup while off. Returns true while cells wait.
   */
  function tick(players = [], until = now() + limits().sliceMs) {
    if (!providers.length) return false;
    if (!enabled) { runCleanup(players, until); return false; }
    const readers = new Map();
    const readerOf = dimension => { if (!readers.has(dimension.id)) readers.set(dimension.id, createReader(dimension, view)); return readers.get(dimension.id); };
    let work = 0;
    // New and changed surfaces are written together at the end, one native call per distinct surface.
    const writer = createBulkWriter({ api, log });
    for (const queue of [events, scans]) {
      for (const [key, entry] of queue) {
        if (work >= 4096 || now() >= until) break;
        queue.delete(key);
        work++;
        if (!refresh(readerOf(entry.dimension), entry.x, entry.y, entry.z, writer)) counters.unknown++;
      }
    }
    writer.flush();
    // Surfaces can lose their host without an event (fire, water, falling sand); look at a few each tick.
    for (let step = 0; step < 4 && known.size && now() < until; step++) {
      let next = validator.next();
      if (next.done) { validator = known.values(); next = validator.next(); }
      if (next.done) break;
      refresh(readerOf(next.value.dimension), next.value.x, next.value.y, next.value.z);
    }
    if (known.size > 65536) { known.clear(); validator = known.values(); }
    saveIndex();
    return events.size + scans.size > 0;
  }

  /** A pack's overlay data; without data the pack's overlays are dropped. */
  function setSource(provider, data) {
    if (data === undefined || data === null) sources.delete(provider);
    else sources.set(provider, compileOverlays(data, provider));
    rebuild();
  }

  function probe(block) {
    if (!block) return {};
    const reader = createReader(block.dimension, view);
    const host = reader.get(block.location.x, block.location.y, block.location.z);
    if (!host) return {};
    return Object.fromEntries(providers.map(provider => [provider.id, Object.fromEntries(FACES.map(face => {
      const found = faceLayers(provider, host, face);
      return [face, found === null ? 'unknown' : found.layers.map(layer => ({ rule: provider.rules[layer.rule].id, value: layer.value, offset: found.offset }))];
    }))]));
  }

  return {
    setSource, changed, swapped, found, prepare, tick, probe,
    /** Block types the overlay scanner looks for: sources, hosts, the replacements standing for them, and surfaces. */
    blockTypes: () => [...new Set([...providers.flatMap(provider => provider.scan).flatMap(type => [type, ...aliasesOf(type)]), ...surfaceTypes])],
    surfaceTypes: () => surfaceTypes,
    /** Scanner top-surface pass: the topmost block of a column (above the band); only its top face is sure to show. */
    surface: (dimension, location) => found(dimension, location, dimension.getBlock(location)?.typeId, undefined, true),
    setEnabled(value) {
      enabled = value;
      events.clear(); scans.clear();
      if (!value) { known.clear(); cleanup = true; }
      else cleanup = null;
    },
    get enabled() { return enabled; },
    get status() {
      loadIndex();
      return { packs: providers.length, rules: providers.reduce((sum, provider) => sum + provider.rules.length, 0),
        types: surfaceTypes.size, queued: events.size + scans.size, known: known.size,
        indexedChunks: [...index.values()].reduce((sum, chunks) => sum + chunks.size, 0), cleaning: !!cleanup, ...counters };
    },
  };
}
