import test from 'node:test';
import assert from 'node:assert/strict';
import { fakeBedrock, vanillaBlocks, withDefinitions } from './fake_bedrock.mjs';
import { startEngine } from '../engine/engine.mjs';
import { authoredSource, publishSources } from '../engine/publisher.mjs';
import { AuthoredError, compileAuthored } from '../engine/authored.mjs';
import { LEAF_DECAY, VANILLA_LEAVES, VANILLA_LOGS, VANILLA_OPEN, VANILLA_SOLID } from '../engine/vanilla-blocks.mjs';
import { javaModelIndex } from '../engine/tiles.mjs';

const vanilla = vanillaBlocks();

/** A pack's block made from the pattern template: its pattern states and the vanilla block's states as bct:<name>. */
function patternBlock(identifier, states) {
  return { format_version: '1.26.50', 'minecraft:block': { description: { identifier, states }, components: {} } };
}
const range = max => ({ values: { min: 0, max } });
/** An edge block made from the edge template: one bit per side for edges and for corners. */
const edgeStates = { 'bct:edges': range(15), 'bct:corners': range(15) };
/** A leaf block made from the leaf template: the vanilla leaf states, the model pick and, with near and far models, the look. */
const leafStates = (models, looks) => ({ 'bct:persistent_bit': range(1), 'bct:update_bit': range(1),
  ...(models > 1 ? { 'bct:t': range(models - 1) } : {}), ...(looks ? { 'bct:look': range(1) } : {}) });
const repeatStates = (x, y) => ({ 'bct:x': range(x - 1), 'bct:y': range(y - 1), 'bct:z': range(x - 1) });
const fakeApi = fakeBedrock({ registry: withDefinitions(vanilla, [
  patternBlock('mypack:stone', repeatStates(6, 2)), patternBlock('mypack:cobble', { 'bct:r': range(3) }),
  patternBlock('mypack:deepslate', { 'bct:pillar_axis': ['y', 'x', 'z'] }), patternBlock('p:stone', {}),
  patternBlock('mypack:oak_leaves', leafStates(3, true)), patternBlock('mypack:birch_leaves', leafStates(2, false)),
  patternBlock('mypack:plain_leaves', leafStates(1, false)),
  patternBlock('mypack:grass_edge', edgeStates), patternBlock('mypack:sand_edge', edgeStates)]) }).api;

test('a pattern entry compiles to the same replace data the converter writes', () => {
  const { provider, replace } = compileAuthored(fakeApi, { format: 1, pack: 'mypack', blocks: {
    'minecraft:stone': { block: 'mypack:stone', pattern: { repeat: [3, 2] } },
    'minecraft:cobblestone': { block: 'mypack:cobble', pattern: { random: [4, 2, 1, 1] } },
    'minecraft:deepslate': { block: 'mypack:deepslate' },
  } });
  assert.equal(provider, 'mypack');
  const [stone, cobble, deepslate] = replace.blocks;
  assert.deepEqual(stone, { vanilla: 'minecraft:stone', block: 'mypack:stone', mirror: {}, random: [],
    axes: [['bct:x', 'x', 6], ['bct:y', 'y', 2], ['bct:z', 'z', 6]] });
  assert.deepEqual(cobble.random, [{ state: 'bct:r', count: 4, weights: [4, 2, 1, 1] }]);
  assert.deepEqual(deepslate.mirror, { pillar_axis: 'bct:pillar_axis' }, 'the vanilla states come from the game');
  assert.deepEqual(replace.solid, [...VANILLA_SOLID]);
  assert.deepEqual(replace.open, [...VANILLA_OPEN]);
});

test('a leaves entry compiles to the leaf data the converter writes, with the vanilla leaves, logs and decay', () => {
  const { replace } = compileAuthored(fakeApi, { format: 1, pack: 'mypack', leaves: {
    'minecraft:oak_leaves': { block: 'mypack:oak_leaves', models: { near: [1, 1], far: [2, 1, 1], nearUpTo: 4 } },
    'minecraft:birch_leaves': { block: 'mypack:birch_leaves', models: [3, 1] },
    'minecraft:spruce_leaves': { block: 'mypack:plain_leaves' },
  } });
  const mirror = { persistent_bit: 'bct:persistent_bit', update_bit: 'bct:update_bit' }, bools = ['persistent_bit', 'update_bit'];
  const selection = 'java-26.2-block-position';
  assert.deepEqual(replace.leaves.blocks, [
    { vanilla: 'minecraft:oak_leaves', block: 'mypack:oak_leaves', mirror, bools,
      look: { state: 'bct:look', table: [[0, 0, 0, 0, 1, 1, 1], [0, 0, 0, 0, 1, 1, 1]] },
      turn: { state: 'bct:t', weights: [1, 1], selection, byLook: [[1, 1], [2, 1, 1]] } },
    { vanilla: 'minecraft:birch_leaves', block: 'mypack:birch_leaves', mirror, bools, turn: { state: 'bct:t', weights: [3, 1], selection } },
    { vanilla: 'minecraft:spruce_leaves', block: 'mypack:plain_leaves', mirror, bools }]);
  assert.deepEqual(replace.leaves.leaves, [...VANILLA_LEAVES]);
  assert.deepEqual(replace.leaves.logs, [...VANILLA_LOGS]);
  assert.deepEqual(replace.leaves.decay, { ...LEAF_DECAY });
  assert.equal(compileAuthored(fakeApi, { format: 1, pack: 'p' }).replace.leaves, undefined, 'no leaves section, no leaf data');
});

test('edges compile to terrain data drawn by the edge blocks of the pack, earlier edges over later ones', () => {
  const { terrain } = compileAuthored(fakeApi, { format: 1, pack: 'my-pack', edges: [
    { from: 'minecraft:grass_block', onto: ['minecraft:stone', 'minecraft:sand'], block: 'mypack:grass_edge' },
    { from: ['minecraft:sand', 'minecraft:sand'], onto: 'minecraft:stone', block: 'mypack:sand_edge' }] });
  const rule = { contacts: ['north', 'east', 'south', 'west'], faces: ['up'], selection: 'neighbor_overlay', corner_policy: 'path_corner' };
  assert.deepEqual(terrain, [{ format_version: 1, id: 'bct_my_pack',
    effects: { e0: { kind: 'surface', entity: 'mypack:grass_edge', native_block: 'mypack:grass_edge', priority: 15 },
      e1: { kind: 'surface', entity: 'mypack:sand_edge', native_block: 'mypack:sand_edge', priority: 14 } },
    rules: [{ id: 'e0', effect: 'e0', targets: ['minecraft:stone', 'minecraft:sand'], neighbors: ['minecraft:grass_block'], ...rule },
      { id: 'e1', effect: 'e1', targets: ['minecraft:stone'], neighbors: ['minecraft:sand'], ...rule }] }]);
  assert.deepEqual(compileAuthored(fakeApi, { format: 1, pack: 'p' }).terrain, [], 'no edges, no terrain data');
});

test('mistakes are refused with a message naming the entry', () => {
  const refused = (data, pattern) => assert.throws(() => compileAuthored(fakeApi, data),
    error => error instanceof AuthoredError && pattern.test(error.message));
  refused({ format: 2, pack: 'p' }, /^format: must be 1/);
  refused({ format: 1, pack: 'My Pack' }, /^pack:/);
  refused({ format: 1, pack: 'p', leafs: {} }, /^leafs: is not a section/);
  refused({ format: 1, pack: 'p', blocks: { 'minecraft:stone': { block: 'stone' } } }, /blocks\["minecraft:stone"\]\.block/);
  refused({ format: 1, pack: 'p', blocks: { 'minecraft:stone': { block: 'p:stone', pattern: { repeat: [3] } } } }, /pattern\.repeat: must be \[width, height\]/);
  refused({ format: 1, pack: 'p', blocks: { 'minecraft:stone': { block: 'p:stone', pattern: { repeat: [2, 2], random: [1, 1] } } } }, /exactly one of repeat or random/);
  refused({ format: 1, pack: 'p', blocks: { 'minecraft:nope': { block: 'p:nope' } } }, /not a block this game knows/);
  refused({ format: 1, pack: 'p', blocks: { 'minecraft:stone': { block: 'p:missing' } } }, /p:missing is not a block in your packs/);
  refused({ format: 1, pack: 'p', blocks: { 'minecraft:stone': { block: 'p:stone', pattern: { random: [1, 1] } } } }, /p:stone has no bct:r state/);
  refused({ format: 1, pack: 'p', leaves: { 'minecraft:stone': { block: 'mypack:oak_leaves' } } }, /minecraft:stone is not a vanilla leaf block/);
  refused({ format: 1, pack: 'p', leaves: { 'minecraft:oak_leaves': { block: 'mypack:birch_leaves', models: { near: [1, 1], far: [1] } } } },
    /mypack:birch_leaves has no bct:look state/);
  refused({ format: 1, pack: 'p', leaves: { 'minecraft:oak_leaves': { block: 'mypack:oak_leaves', models: { near: [1], far: [1], nearUpTo: 6 } } } },
    /models\.nearUpTo: must be a log distance from 1 to 5/);
  refused({ format: 1, pack: 'p', leaves: { 'minecraft:oak_leaves': { block: 'mypack:oak_leaves', models: { inner: [1] } } } }, /models\.inner: is not near, far or nearUpTo/);
  refused({ format: 1, pack: 'p', leaves: { 'minecraft:oak_leaves': { block: 'mypack:oak_leaves', models: [] } } }, /models: must be 1 to 16 weights/);
  const edge = { from: 'minecraft:grass_block', onto: ['minecraft:stone'], block: 'mypack:grass_edge' };
  refused({ format: 1, pack: 'p', edges: { grass: edge } }, /^edges: must be a list/);
  refused({ format: 1, pack: 'p', edges: [{ ...edge, block: 'mypack:oak_leaves' }] }, /edges\[0\]\.block: mypack:oak_leaves has no bct:edges state/);
  refused({ format: 1, pack: 'p', edges: [{ ...edge, block: 'mypack:grass-edge' }] }, /edges\[0\]\.block: must be your edge block id/);
  refused({ format: 1, pack: 'p', edges: [edge, edge] }, /edges\[1\]\.block: mypack:grass_edge is already the block of another edge/);
  refused({ format: 1, pack: 'p', edges: [{ ...edge, onto: ['minecraft:nope'] }] }, /edges\[0\]\.onto: minecraft:nope is not a block this game knows/);
  refused({ format: 1, pack: 'p', edges: [{ ...edge, onto: [] }] }, /edges\[0\]\.onto: must be a block id or a list/);
  refused({ format: 1, pack: 'p', edges: [{ ...edge, over: [] }] }, /edges\[0\]\.over: is not from, onto or block/);
});

test('a hand-written pack swaps its pattern blocks in the world, through the copied publisher', () => {
  // The game reports states a block no longer uses (stone_type on stone); the pack's block does not declare them.
  const registry = withDefinitions(vanilla, [patternBlock('mypack:stone', repeatStates(6, 2))]);
  registry.set('minecraft:stone', new Map([['stone_type', ['stone', 'granite']]]));
  const blocks = new Map(), states = new Map();
  for (let x = 0; x < 16; x++) for (let z = 0; z < 16; z++) {
    blocks.set(`${x},60,${z}`, 'minecraft:stone');
    states.set(`${x},60,${z}`, { stone_type: 'stone' });
    for (let y = 56; y < 60; y++) blocks.set(`${x},${y},${z}`, 'minecraft:bedrock');
  }
  const fake = fakeBedrock({ blocks, states, registry });
  const messages = [];
  fake.world.sendMessage = message => messages.push(message);
  startEngine(fake.api);
  const data = { format: 1, pack: 'mypack', blocks: { 'minecraft:stone': { block: 'mypack:stone', pattern: { repeat: [3, 2] } } } };
  publishSources({ system: fake.system, world: fake.world, sources: [authoredSource(data)] });
  fake.system.sendScriptEvent('bct:config', JSON.stringify({ replace: { chunkRadius: 1, yBand: 6 } }));
  fake.player({ x: 8, y: 61, z: 8 });
  fake.step(20);
  assert.equal(fake.blocks.get('7,60,5'), 'mypack:stone');
  assert.deepEqual(fake.states.get('7,60,5'), { 'bct:x': 1, 'bct:y': 0, 'bct:z': 5 }, 'x and z mod 6 (3 and 2), y mod 2');
  assert.deepEqual(messages, []);
});

test('a pack with a mistake tells players in chat and draws nothing', () => {
  const fake = fakeBedrock({ blocks: new Map([['8,60,8', 'minecraft:stone']]), registry: vanilla });
  const messages = [];
  fake.world.sendMessage = message => messages.push(message);
  startEngine(fake.api);
  publishSources({ system: fake.system, world: fake.world,
    sources: [authoredSource({ format: 1, pack: 'mypack', blocks: { 'minecraft:stone': { block: 'stone' } } })] });
  fake.player({ x: 8, y: 61, z: 8 });
  fake.step(5);
  assert.ok(messages.some(message => message.includes('mypack (scripts/bct.js): blocks["minecraft:stone"].block')), messages.join('\n'));
  assert.equal(fake.blocks.get('8,60,8'), 'minecraft:stone');
});

test('hand-written leaves take its near models by logs and its far models away from them', () => {
  const blocks = new Map(), states = new Map(), leaf = { persistent_bit: false, update_bit: false };
  for (let x = -16; x < 32; x++) for (let z = -16; z < 32; z++) blocks.set(`${x},60,${z}`, 'minecraft:stone');
  for (let y = 61; y <= 64; y++) { blocks.set(`8,${y},8`, 'minecraft:oak_log'); states.set(`8,${y},8`, { pillar_axis: 'y' }); }
  // A leaf beside the log (distance 1) and a chain running east to distance 7 (no log within 6).
  for (let x = 9; x <= 15; x++) { blocks.set(`${x},64,8`, 'minecraft:oak_leaves'); states.set(`${x},64,8`, { ...leaf }); }
  const registry = withDefinitions(vanilla, [patternBlock('mypack:oak_leaves', leafStates(3, true))]);
  const fake = fakeBedrock({ blocks, states, registry });
  startEngine(fake.api);
  fake.startup();
  const data = { format: 1, pack: 'mypack', leaves: { 'minecraft:oak_leaves': { block: 'mypack:oak_leaves',
    models: { near: [1, 1, 1], far: [1, 2], nearUpTo: 4 } } } };
  publishSources({ system: fake.system, world: fake.world, sources: [authoredSource(data)] });
  fake.system.sendScriptEvent('bct:config', JSON.stringify({ leaves: { chunkRadius: 2 } }));
  fake.player({ x: 8, y: 61, z: 8 });
  fake.step(30);
  const selection = 'java-26.2-block-position';
  for (const [x, look, weights] of [[9, 0, [1, 1, 1]], [12, 0, [1, 1, 1]], [13, 1, [1, 2]], [15, 1, [1, 2]]]) {
    assert.equal(fake.blocks.get(`${x},64,8`), 'mypack:oak_leaves', 'leaf at x ' + x);
    assert.deepEqual(fake.states.get(`${x},64,8`), { 'bct:persistent_bit': 0, 'bct:update_bit': 0, 'bct:look': look,
      'bct:t': javaModelIndex({ x, y: 64, z: 8 }, weights, selection) }, 'leaf at log distance ' + (x - 8));
  }
});

test('hand-written edges draw above the blocks they spread onto; where two meet, the earlier edge draws', () => {
  const blocks = new Map();
  for (let x = 0; x < 16; x++) for (let z = 0; z < 16; z++) blocks.set(`${x},60,${z}`, 'minecraft:stone');
  blocks.set('8,60,7', 'minecraft:grass_block');   // north of 8,60,8
  blocks.set('9,60,8', 'minecraft:sand');          // east of 8,60,8 and north of 9,60,9
  const registry = withDefinitions(vanilla, [patternBlock('mypack:grass_edge', edgeStates), patternBlock('mypack:sand_edge', edgeStates)]);
  const fake = fakeBedrock({ blocks, registry });
  startEngine(fake.api);
  const data = { format: 1, pack: 'mypack', edges: [
    { from: 'minecraft:grass_block', onto: ['minecraft:stone'], block: 'mypack:grass_edge' },
    { from: 'minecraft:sand', onto: ['minecraft:stone'], block: 'mypack:sand_edge' }] };
  publishSources({ system: fake.system, world: fake.world, sources: [authoredSource(data)] });
  fake.player({ x: 8, y: 61, z: 8 });
  fake.step(40);
  assert.equal(fake.blocks.get('8,61,8'), 'mypack:grass_edge', 'grass and sand both border this stone: the first edge draws');
  assert.deepEqual(fake.states.get('8,61,8'), { 'bct:edges': 1, 'bct:corners': 0 }, 'its north side');
  assert.equal(fake.blocks.get('9,61,9'), 'mypack:sand_edge', 'sand alone to the north');
  assert.deepEqual(fake.states.get('9,61,9'), { 'bct:edges': 1, 'bct:corners': 0 });
  assert.equal(fake.blocks.get('8,61,6'), 'mypack:grass_edge', 'grass to the south');
  assert.deepEqual(fake.states.get('8,61,6'), { 'bct:edges': 4, 'bct:corners': 0 });
  assert.equal(fake.blocks.get('8,61,7'), undefined, 'nothing above the grass itself');
});

test('built overlays join the terrain data; overlays changed since they were built are refused', async () => {
  const { checksum } = await import('../engine/sources.mjs');
  const overlays = [{ tiles: 'textures/blocks/grass_overlay', onto: ['minecraft:stone'], from: 'minecraft:grass_block' }];
  const built = { format_version: 1, rules: [], types: [] };
  const data = { format: 1, pack: 'mypack', overlays, overlaySurfaces: { digest: checksum(JSON.stringify(overlays)), data: built } };
  assert.deepEqual(compileAuthored(fakeApi, data).terrain, [{ overlay: built }]);
  const changed = { ...data, overlays: [{ ...overlays[0], onto: ['minecraft:dirt'] }] };
  assert.throws(() => compileAuthored(fakeApi, changed), error => error instanceof AuthoredError && /^overlays: changed since their surface blocks were built: run python bct.py overlays again$/.test(error.message));
  assert.throws(() => compileAuthored(fakeApi, { ...data, overlaySurfaces: null }), /run python bct.py overlays again/);
  assert.deepEqual(compileAuthored(fakeApi, { format: 1, pack: 'mypack', overlaySurfaces: null }).terrain, [], 'no overlays, nothing to build');
});

const connectStates = { 'bct:n': range(1), 'bct:s': range(1), 'bct:w': range(1), 'bct:e': range(1), 'bct:u': range(1), 'bct:d': range(1) };

test('a connected entry compiles to a replacement that keeps six neighbour states', () => {
  const api = fakeBedrock({ registry: withDefinitions(vanilla, [patternBlock('mypack:glass', connectStates), patternBlock('mypack:shelf', connectStates), patternBlock('p:plain', {})]) }).api;
  const { replace } = compileAuthored(api, { format: 1, pack: 'mypack', connected: {
    'minecraft:glass': { block: 'mypack:glass', open: true },
    'minecraft:bookshelf': { block: 'mypack:shelf', connect: ['minecraft:bookshelf', 'minecraft:chiseled_bookshelf'] } } });
  const states = { north: 'bct:n', south: 'bct:s', west: 'bct:w', east: 'bct:e', up: 'bct:u', down: 'bct:d' };
  assert.deepEqual(replace.blocks, [
    { vanilla: 'minecraft:glass', block: 'mypack:glass', mirror: {}, axes: [], random: [], open: true, connect: { states, with: ['minecraft:glass'] } },
    { vanilla: 'minecraft:bookshelf', block: 'mypack:shelf', mirror: {}, axes: [], random: [],
      connect: { states, with: ['minecraft:bookshelf', 'minecraft:chiseled_bookshelf'] } }]);
  const refused = (data, pattern) => assert.throws(() => compileAuthored(api, data), error => error instanceof AuthoredError && pattern.test(error.message));
  refused({ format: 1, pack: 'p', connected: { 'minecraft:glass': { block: 'mypack:oak' } } }, /mypack:oak is not a block in your packs/);
  refused({ format: 1, pack: 'p', connected: { 'minecraft:glass': { block: 'p:plain' } } }, /p:plain has no bct:n state/);
  refused({ format: 1, pack: 'p', connected: { 'minecraft:glass': { block: 'mypack:glass', connect: 'other' } } }, /connect: must be "same" or a list/);
  refused({ format: 1, pack: 'p', connected: { 'minecraft:glass': { block: 'mypack:glass', pattern: { repeat: [2, 2] } } } }, /a connected block has no pattern/);
  refused({ format: 1, pack: 'p', blocks: { 'minecraft:glass': { block: 'mypack:glass' } }, connected: { 'minecraft:glass': { block: 'mypack:glass' } } },
    /is in blocks too/);
});

test('connected blocks join their neighbours, and rejoin when a neighbour is broken or placed', () => {
  const blocks = new Map();
  for (let x = 0; x < 16; x++) for (let z = 0; z < 16; z++) blocks.set(`${x},60,${z}`, 'minecraft:stone');
  for (const x of [7, 8, 9]) blocks.set(`${x},61,8`, 'minecraft:glass');
  blocks.set('8,62,8', 'minecraft:glass');
  const registry = withDefinitions(vanilla, [patternBlock('mypack:glass', connectStates)]);
  const fake = fakeBedrock({ blocks, registry });
  startEngine(fake.api);
  const data = { format: 1, pack: 'mypack', connected: { 'minecraft:glass': { block: 'mypack:glass', open: true } } };
  publishSources({ system: fake.system, world: fake.world, sources: [authoredSource(data)] });
  fake.system.sendScriptEvent('bct:config', JSON.stringify({ replace: { chunkRadius: 1, yBand: 6 } }));
  fake.player({ x: 8, y: 63, z: 12 });
  fake.step(30);
  const sides = key => { const s = fake.states.get(key); return ['bct:n', 'bct:s', 'bct:w', 'bct:e', 'bct:u', 'bct:d'].filter(name => s[name]).join(' '); };
  assert.equal(fake.blocks.get('8,61,8'), 'mypack:glass');
  assert.equal(sides('8,61,8'), 'bct:w bct:e bct:u', 'joined west, east and up');
  assert.equal(sides('7,61,8'), 'bct:e');
  fake.blocks.delete('9,61,8');
  fake.world.afterEvents.playerBreakBlock.emit({ block: fake.dimension.getBlock({ x: 9, y: 61, z: 8 }), brokenBlockPermutation: { type: { id: 'mypack:glass' } } });
  fake.step(2);
  assert.equal(sides('8,61,8'), 'bct:w bct:u', 'its east neighbour is gone');
  fake.blocks.set('8,61,7', 'minecraft:glass');
  fake.world.afterEvents.playerPlaceBlock.emit({ block: fake.dimension.getBlock({ x: 8, y: 61, z: 7 }) });
  fake.step(2);
  assert.equal(fake.blocks.get('8,61,7'), 'mypack:glass', 'the placed glass swaps in');
  assert.equal(sides('8,61,7'), 'bct:s', 'and joins the glass to its south');
  assert.equal(sides('8,61,8'), 'bct:n bct:w bct:u', 'which joins it back');
});

test('built carriers become the pack connected-texture data; carriers changed since they were built are refused', async () => {
  const { checksum } = await import('../engine/sources.mjs');
  const carriers = [{ tiles: 'textures/blocks/glass_ctm', blocks: ['minecraft:glass'] }];
  const built = { rules: [{ id: 'c0', method: 'ctm', blocks: ['minecraft:glass'] }] };
  const data = { format: 1, pack: 'mypack', carriers, carrierSurfaces: { digest: checksum(JSON.stringify(carriers)), data: built } };
  assert.deepEqual(compileAuthored(fakeApi, data).connected, built);
  assert.throws(() => compileAuthored(fakeApi, { ...data, carriers: [{ ...carriers[0], faces: ['up'] }] }),
    error => error instanceof AuthoredError && /^carriers: changed since they were built: run python bct.py carriers again$/.test(error.message));
  assert.equal(compileAuthored(fakeApi, { format: 1, pack: 'mypack', carrierSurfaces: null }).connected, undefined);
});
