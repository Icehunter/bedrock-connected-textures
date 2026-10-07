/**
 * Data written by hand in a Bedrock pack (docs/AUTHORING.md), compiled into the
 * engine's own data: the same form the Java converter writes, so a hand-written
 * pack and a converted one draw through the same code.
 *
 * A pack's behavior pack exports its data from scripts/bct.js; its copy of
 * publisher.js sends it as an `authored` source. Everything the author would
 * otherwise have to repeat comes from the game or from the engine's built-in
 * tables: which vanilla states a block has (mirrored onto the pack's block as
 * bct:<state>), which blocks are opaque full cubes and which let a face show.
 *
 * compileAuthored throws an AuthoredError naming the entry at fault; the engine
 * shows that message to players in chat, so an author sees it at once.
 */
import { checksum } from './sources.mjs';
import { LEAF_DECAY, VANILLA_LEAVES, VANILLA_LOGS, VANILLA_OPEN, VANILLA_SOLID } from './vanilla-blocks.mjs';

const PACK_ID = /^[a-z0-9_-]{1,80}$/;
const BLOCK_ID = /^[a-z0-9_.-]+:[a-z0-9_./-]+$/;
// Sections a pack may have; later engine versions add to them.
// overlaySurfaces and carrierSurfaces are not written by hand: main.js adds what `python bct.py overlays` and
// `python bct.py carriers` built (scripts/bct-overlays.js, scripts/bct-carriers.js).
const SECTIONS = new Set(['format', 'pack', 'priority', 'blocks', 'leaves', 'edges', 'overlays', 'overlaySurfaces', 'connected',
  'carriers', 'carrierSurfaces']);
// Java's log distance runs from 1 (next to a log) to 7 (no log near); leaves within nearUpTo use the near models.
const LEAF_DISTANCES = 7, NEAR_UP_TO = 3;
// The weighted model pick converted packs use too (vanilla Java's pick by block position).
const MODEL_SELECTION = 'java-26.2-block-position';

export class AuthoredError extends Error {}

const fail = (where, message) => { throw new AuthoredError(where + ': ' + message); };
const positiveInteger = value => Number.isInteger(value) && value >= 1;
const gcd = (a, b) => (b ? gcd(b, a % b) : a);
const lcm = (a, b) => a / gcd(a, b) * b;

/** A block's states in its default permutation, from the game; undefined when the game has no such block. */
function statesOf(api, type) {
  try { return api.BlockPermutation.resolve(type).getAllStates?.() ?? {}; }
  catch { return undefined; }
}

/** One `blocks` entry: the pattern the engine gives the pack's block. */
function compileBlock(api, vanilla, entry, where) {
  if (!BLOCK_ID.test(vanilla)) fail(where, 'the key must be a block id such as minecraft:stone');
  if (!entry || typeof entry !== 'object') fail(where, 'must be an object such as { block: "mypack:stone", pattern: { repeat: [3, 3] } }');
  if (typeof entry.block !== 'string' || !BLOCK_ID.test(entry.block)) fail(where + '.block', 'must be your block id, such as mypack:stone');
  const vanillaStates = statesOf(api, vanilla);
  if (!vanillaStates) fail(where, vanilla + ' is not a block this game knows');
  const compiled = { vanilla, block: entry.block, mirror: {}, axes: [], random: [] };
  const pattern = entry.pattern;
  if (pattern !== undefined) {
    if (!pattern || typeof pattern !== 'object') fail(where + '.pattern', 'must be an object such as { repeat: [3, 3] }');
    const kinds = ['repeat', 'random'].filter(kind => pattern[kind] !== undefined);
    if (kinds.length !== 1) fail(where + '.pattern', 'needs exactly one of repeat or random');
    if (pattern.repeat !== undefined) {
      const size = pattern.repeat;
      if (!Array.isArray(size) || size.length !== 2 || !size.every(value => positiveInteger(value) && value <= 16))
        fail(where + '.pattern.repeat', 'must be [width, height], each 1 to 16');
      // A top face reads x and z against both the width and the height (its rows run along z), so x and z
      // count to the least common multiple, as converted packs do.
      const [width, height] = size, period = lcm(width, height);
      compiled.axes = [['bct:x', 'x', period], ['bct:y', 'y', height], ['bct:z', 'z', period]];
    } else {
      const weights = pattern.random;
      if (!Array.isArray(weights) || weights.length < 2 || weights.length > 16 || !weights.every(value => Number.isInteger(value) && value >= 0) ||
        !weights.some(value => value > 0))
        fail(where + '.pattern.random', 'must be 2 to 16 weights, such as [4, 2, 1, 1]');
      compiled.random = [{ state: 'bct:r', count: weights.length, weights }];
    }
  }
  const own = statesOf(api, entry.block);
  if (!own) fail(where + '.block', entry.block + ' is not a block in your packs (is its blocks/ file in the behavior pack?)');
  for (const state of [...compiled.axes.map(([name]) => name), ...compiled.random.map(item => item.state)])
    if (!(state in own)) fail(where + '.block', entry.block + ' has no ' + state + ' state, which its pattern needs');
  // The vanilla states the pack's block keeps as bct:<name>: only those it declares (the game may report
  // states a block no longer uses, such as stone's stone_type), so a pack may leave out any it does not need.
  for (const name of Object.keys(vanillaStates)) {
    const mirrored = 'bct:' + name.split(':').pop();
    if (!(mirrored in own)) continue;
    compiled.mirror[name] = mirrored;
    if (typeof vanillaStates[name] === 'boolean') (compiled.bools ??= []).push(name);
  }
  // Vanilla behaviour `python bct.py block` writes into the entry from the engine's gameplay tables.
  for (const field of ['strip', 'xp', 'tool', 'open', 'leafGuard', 'cost']) if (entry[field] !== undefined) compiled[field] = entry[field];
  return compiled;
}

// A connected block's neighbour states, by side.
const CONNECT_STATES = { north: 'bct:n', south: 'bct:s', west: 'bct:w', east: 'bct:e', up: 'bct:u', down: 'bct:d' };

/** One `connected` entry: the pack's block, which draws its own borders from six neighbour states the engine keeps up to date. */
function compileConnected(api, vanilla, entry, where) {
  if (entry?.pattern !== undefined) fail(where + '.pattern', 'a connected block has no pattern');
  const compiled = compileBlock(api, vanilla, entry, where);
  const connect = entry.connect ?? 'same';
  let joins;
  if (connect === 'same') joins = [vanilla];
  else if (Array.isArray(connect)) joins = blockList(api, connect, where + '.connect');
  else fail(where + '.connect', 'must be "same" or a list of block ids');
  const own = statesOf(api, entry.block);
  for (const state of Object.values(CONNECT_STATES))
    if (!(state in own)) fail(where + '.block', entry.block + ' has no ' + state + ' state, which a connected block needs');
  compiled.connect = { states: { ...CONNECT_STATES }, with: joins };
  return compiled;
}

/** Model weights: 1 to 16 whole numbers, at least one above 0. */
function checkWeights(weights, where) {
  if (!Array.isArray(weights) || weights.length < 1 || weights.length > 16 || !weights.every(value => Number.isInteger(value) && value >= 0) ||
    !weights.some(value => value > 0))
    fail(where, 'must be 1 to 16 weights, such as [2, 1, 1]');
  return weights;
}

/** One `leaves` entry: the pack's leaf block, its weighted models and, optionally, other models far from logs. */
function compileLeaf(api, vanilla, entry, where) {
  if (!VANILLA_LEAVES.includes(vanilla)) fail(where, vanilla + ' is not a vanilla leaf block');
  if (!entry || typeof entry !== 'object') fail(where, 'must be an object such as { block: "mypack:oak_leaves", models: [2, 1, 1] }');
  if (typeof entry.block !== 'string' || !BLOCK_ID.test(entry.block)) fail(where + '.block', 'must be your block id, such as mypack:oak_leaves');
  const compiled = { vanilla, block: entry.block, mirror: { persistent_bit: 'bct:persistent_bit', update_bit: 'bct:update_bit' },
    bools: ['persistent_bit', 'update_bit'] };
  const models = entry.models ?? [1];
  let looks;
  if (Array.isArray(models)) looks = [checkWeights(models, where + '.models')];
  else if (models && typeof models === 'object') {
    for (const key of Object.keys(models))
      if (!['near', 'far', 'nearUpTo'].includes(key)) fail(where + '.models.' + key, 'is not near, far or nearUpTo');
    const nearUpTo = models.nearUpTo ?? NEAR_UP_TO;
    if (!Number.isInteger(nearUpTo) || nearUpTo < 1 || nearUpTo > LEAF_DISTANCES - 2)
      fail(where + '.models.nearUpTo', 'must be a log distance from 1 to ' + (LEAF_DISTANCES - 2));
    looks = [checkWeights(models.near, where + '.models.near'), checkWeights(models.far, where + '.models.far')];
    // Rows: not persistent, then persistent (placed leaves count their distance the same way); columns: distance 1 to 7.
    const row = Array.from({ length: LEAF_DISTANCES }, (_, index) => (index + 1 <= nearUpTo ? 0 : 1));
    compiled.look = { state: 'bct:look', table: [row, [...row]] };
  } else fail(where + '.models', 'must be weights such as [2, 1, 1] or { near: [...], far: [...] }');
  if (looks.some(weights => weights.length > 1)) {
    compiled.turn = { state: 'bct:t', weights: looks[0], selection: MODEL_SELECTION };
    if (looks.length > 1) compiled.turn.byLook = looks;
  }
  const own = statesOf(api, entry.block);
  if (!own) fail(where + '.block', entry.block + ' is not a block in your packs (is its blocks/ file in the behavior pack?)');
  const needed = ['bct:persistent_bit', 'bct:update_bit', ...(compiled.turn ? ['bct:t'] : []), ...(compiled.look ? ['bct:look'] : [])];
  for (const state of needed) if (!(state in own)) fail(where + '.block', entry.block + ' has no ' + state + ' state, which a leaf block needs');
  return compiled;
}

// Edge blocks: the terrain data's ids are stricter than block ids in general.
const EDGE_BLOCK_ID = /^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$/;
// A pack may have this many edges (the terrain data's effect limit).
const MAX_EDGES = 16;
const HORIZONTAL = ['north', 'east', 'south', 'west'];

/** A list of block ids the game knows, or one id. */
function blockList(api, value, where) {
  const list = typeof value === 'string' ? [value] : value;
  if (!Array.isArray(list) || !list.length || list.length > 128) fail(where, 'must be a block id or a list of 1 to 128 of them');
  for (const id of list) {
    if (typeof id !== 'string' || !BLOCK_ID.test(id)) fail(where, JSON.stringify(id) + ' is not a block id');
    if (!statesOf(api, id)) fail(where, id + ' is not a block this game knows');
  }
  return [...new Set(list)];
}

/**
 * The pack's edges as terrain data: one surface effect per edge, drawn by the pack's edge block in the
 * empty cell above each block it spreads onto, with its edges and corners as bct:edges and bct:corners.
 * Earlier edges lie over later ones.
 */
function compileEdges(api, pack, edges) {
  if (!Array.isArray(edges)) fail('edges', 'must be a list such as [{ from: "minecraft:grass_block", onto: ["minecraft:stone"], block: "mypack:grass_edge" }]');
  if (edges.length > MAX_EDGES) fail('edges', 'may hold at most ' + MAX_EDGES + ' edges');
  if (!edges.length) return [];
  const effects = {}, rules = [], blocks = new Set();
  edges.forEach((edge, index) => {
    const where = 'edges[' + index + ']';
    if (!edge || typeof edge !== 'object' || Array.isArray(edge)) fail(where, 'must be an object with from, onto and block');
    for (const key of Object.keys(edge)) if (!['from', 'onto', 'block'].includes(key)) fail(where + '.' + key, 'is not from, onto or block');
    if (typeof edge.block !== 'string' || !EDGE_BLOCK_ID.test(edge.block))
      fail(where + '.block', 'must be your edge block id: letters, digits and _ only, such as mypack:grass_edge');
    if (blocks.has(edge.block)) fail(where + '.block', edge.block + ' is already the block of another edge');
    blocks.add(edge.block);
    const own = statesOf(api, edge.block);
    if (!own) fail(where + '.block', edge.block + ' is not a block in your packs (is its blocks/ file in the behavior pack?)');
    for (const state of ['bct:edges', 'bct:corners'])
      if (!(state in own)) fail(where + '.block', edge.block + ' has no ' + state + ' state, which an edge block needs');
    const from = blockList(api, edge.from, where + '.from'), onto = blockList(api, edge.onto, where + '.onto');
    const name = 'e' + index;
    effects[name] = { kind: 'surface', entity: edge.block, native_block: edge.block, priority: Math.max(0, MAX_EDGES - 1 - index) };
    rules.push({ id: name, effect: name, targets: onto, neighbors: from, contacts: [...HORIZONTAL], faces: ['up'],
      selection: 'neighbor_overlay', corner_policy: 'path_corner' });
  });
  return [{ format_version: 1, id: 'bct_' + pack.replace(/-/g, '_'), effects, rules }];
}

/**
 * The engine data a hand-written pack's data gives: { provider, priority, replace, terrain, connected }.
 * api: @minecraft/server (BlockPermutation).
 */
export function compileAuthored(api, data) {
  if (!data || typeof data !== 'object') fail('bct.js', 'must export an object');
  if (data.format !== 1) fail('format', 'must be 1');
  if (typeof data.pack !== 'string' || !PACK_ID.test(data.pack)) fail('pack', 'must be a short id of letters, digits, - and _');
  for (const key of Object.keys(data)) if (!SECTIONS.has(key)) fail(key, 'is not a section this engine version knows');
  if (data.priority !== undefined && !Number.isInteger(data.priority)) fail('priority', 'must be a whole number');
  const blocks = [];
  if (data.blocks !== undefined) {
    if (!data.blocks || typeof data.blocks !== 'object' || Array.isArray(data.blocks)) fail('blocks', 'must be an object keyed by vanilla block id');
    for (const [vanilla, entry] of Object.entries(data.blocks)) blocks.push(compileBlock(api, vanilla, entry, 'blocks["' + vanilla + '"]'));
  }
  if (data.connected !== undefined) {
    if (!data.connected || typeof data.connected !== 'object' || Array.isArray(data.connected)) fail('connected', 'must be an object keyed by vanilla block id');
    for (const [vanilla, entry] of Object.entries(data.connected)) {
      if (data.blocks?.[vanilla] !== undefined) fail('connected["' + vanilla + '"]', vanilla + ' is in blocks too: a block is either patterned or connected');
      blocks.push(compileConnected(api, vanilla, entry, 'connected["' + vanilla + '"]'));
    }
  }
  const replace = { format_version: 1, blocks, open: [...VANILLA_OPEN], solid: [...VANILLA_SOLID] };
  if (data.leaves !== undefined) {
    if (!data.leaves || typeof data.leaves !== 'object' || Array.isArray(data.leaves)) fail('leaves', 'must be an object keyed by vanilla leaf id');
    const leafBlocks = Object.entries(data.leaves).map(([vanilla, entry]) => compileLeaf(api, vanilla, entry, 'leaves["' + vanilla + '"]'));
    if (leafBlocks.length) replace.leaves = { blocks: leafBlocks, leaves: [...VANILLA_LEAVES], logs: [...VANILLA_LOGS], decay: { ...LEAF_DECAY } };
  }
  const terrain = data.edges === undefined ? [] : compileEdges(api, data.pack, data.edges);
  // Overlays draw with the surface blocks `python bct.py overlays` builds from them; its data says which overlays it built.
  if (data.overlays !== undefined && !Array.isArray(data.overlays)) fail('overlays', 'must be a list');
  if (data.overlays?.length) {
    const built = data.overlaySurfaces;
    if (!built || built.digest !== checksum(JSON.stringify(data.overlays)))
      fail('overlays', 'changed since their surface blocks were built: run python bct.py overlays again');
    terrain.push({ overlay: built.data });
  }
  // Carriers draw with the entities `python bct.py carriers` builds, as converted packs' connected textures do.
  if (data.carriers !== undefined && !Array.isArray(data.carriers)) fail('carriers', 'must be a list');
  let connected;
  if (data.carriers?.length) {
    const built = data.carrierSurfaces;
    if (!built || built.digest !== checksum(JSON.stringify(data.carriers)))
      fail('carriers', 'changed since they were built: run python bct.py carriers again');
    connected = built.data;
  }
  return { provider: data.pack, priority: data.priority ?? 0, replace, terrain, connected };
}
