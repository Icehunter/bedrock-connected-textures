import test from 'node:test';
import assert from 'node:assert/strict';
import { fakeBedrock, vanillaBlocks, withDefinitions, GAME_COSTS } from './fake_bedrock.mjs';
import { startEngine } from '../engine/engine.mjs';
import { publishSources } from '../engine/publisher.mjs';
import { checksum } from '../engine/sources.mjs';
import { javaModelIndex } from '../engine/tiles.mjs';
import { resolveSettings } from '../engine/settings.mjs';

const MIRROR = { persistent_bit: 'bct:persistent_bit', update_bit: 'bct:update_bit' };
const OAK = { vanilla: 'minecraft:oak_leaves', block: 'bct_t:m_oak_leaves', mirror: MIRROR, bools: Object.keys(MIRROR),
  turn: { state: 'bct:t', weights: [1, 1, 1, 1], selection: 'java-26.2-multipart-block-position' },
  // Oak drawn with an inner model while persistent or within 4 of a log and an outer model at 5 to 7.
  look: { state: 'bct:look', table: [[0, 0, 0, 0, 1, 1, 1], [0, 0, 0, 0, 0, 0, 0]] } };
const BIRCH = { vanilla: 'minecraft:birch_leaves', block: 'bct_t:m_birch_leaves', mirror: MIRROR, bools: Object.keys(MIRROR),
  turn: { state: 'bct:t', weights: [1, 1, 1, 1], selection: 'java-26.2-block-position' } };
const leafData = { blocks: [OAK, BIRCH], leaves: ['minecraft:oak_leaves', 'minecraft:birch_leaves', 'minecraft:spruce_leaves'],
  logs: ['minecraft:oak_log', 'minecraft:birch_log'], decay: { bedrockDistance: 4, javaDistance: 6, logRadius: 4, leafRadius: 1 } };
const data = { format_version: 1, blocks: [], open: ['minecraft:air'], solid: ['minecraft:stone', 'minecraft:oak_log'], leaves: leafData };

const definition = entry => ({ format_version: '1.26.50', 'minecraft:block': { description: { identifier: entry.block, states: {
  'bct:persistent_bit': { values: { min: 0, max: 1 } }, 'bct:update_bit': { values: { min: 0, max: 1 } },
  ...(entry.turn ? { [entry.turn.state]: { values: { min: 0, max: entry.turn.weights.length - 1 } } } : {}),
  ...(entry.look ? { [entry.look.state]: { values: { min: 0, max: 1 } } } : {}) } }, components: {} } });
const registry = withDefinitions(vanillaBlocks(), leafData.blocks.map(definition));
const LEAF = { persistent_bit: false, update_bit: false };

function packet(value) {
  const text = JSON.stringify(value);
  return { engine: 'replace', provider: 'pack', digest: checksum(text), parts: text.match(/[\s\S]{1,750}/g) };
}

/**
 * An oak at (8, 61..64, 8) with a crown of leaves, a chain of leaves running east
 * from the crown (distance grows by one per block) and a stone floor.
 */
function tree() {
  const blocks = [];
  for (let y = 61; y <= 64; y++) blocks.push([`8,${y},8`, 'minecraft:oak_log', { pillar_axis: 'y' }]);
  for (let x = 6; x <= 10; x++) for (let z = 6; z <= 10; z++) for (let y = 63; y <= 65; y++)
    if (!(x === 8 && z === 8 && y <= 64)) blocks.push([`${x},${y},${z}`, 'minecraft:oak_leaves', { ...LEAF }]);
  for (let x = 11; x <= 14; x++) blocks.push([`${x},65,8`, 'minecraft:oak_leaves', { ...LEAF }]);
  return blocks;
}

const UP = { x: 0, y: 1, z: 0 }, DOWN = { x: 0, y: -1, z: 0 }, NORTH = { x: 0, y: 0, z: -1 }, SOUTH = { x: 0, y: 0, z: 1 };

/**
 * A stone floor at y=60 with the given blocks, the engine with the test pack, and a player at playerAt looking
 * along view: down at the floor unless a test says otherwise, so no leaf is in view. replace: the replace
 * settings (their view settings hold for leaves too).
 */
// Most tests here are about waiting while players look, which is off by default.
const WAITING = { farDistance: 48, nearDistance: 16, swapBack: 1 };

function setup({ extra = tree(), config = {}, replace = {}, playerAt = { x: 8, y: 61, z: 8 }, view = DOWN, onSet, loaded, data: source = data,
  registry: types = registry, costs } = {}) {
  const blocks = new Map(), states = new Map();
  for (let x = -16; x < 32; x++) for (let z = -16; z < 32; z++) blocks.set(`${x},60,${z}`, 'minecraft:stone');
  for (const [key, type, state] of extra) { if (type === 'minecraft:air') blocks.delete(key); else blocks.set(key, type); if (state) states.set(key, state); }
  const fake = fakeBedrock({ blocks, states, registry: types, costs, onSet: (...args) => onSet?.(fake, ...args) });
  if (loaded) fake.dimension.isChunkLoaded = location => loaded(Math.floor(location.x / 16), Math.floor(location.z / 16));
  const engine = startEngine(fake.api);
  const components = fake.startup();
  publishSources({ system: fake.system, world: fake.world, sources: [packet(source)] });
  fake.system.sendScriptEvent('bct:config', JSON.stringify({ leaves: { chunkRadius: 2, ...config }, replace: { ...WAITING, ...replace } }));
  const player = playerAt ? fake.player(playerAt) : undefined;
  if (player) player.view = view;
  return { ...fake, engine, player, components };
}
const settle = (env, passes = 16) => env.step(passes);
const tick = (env, key) => {
  const [x, y, z] = key.split(',').map(Number);
  env.components.get('bct:leaf').onRandomTick({ block: env.dimension.getBlock({ x, y, z }), dimension: env.dimension });
};
const at = key => { const [x, y, z] = key.split(',').map(Number); return { x, y, z }; };

test('leaf settings have defaults and validated overrides', () => {
  assert.deepEqual(resolveSettings().leaves, { chunkRadius: 32, sliceMs: 6, probesPerTick: 64, recheckTicks: 20, checkChance: 8 });
  assert.throws(() => resolveSettings({ leaves: { chunkRadius: 100 } }), /chunkRadius/);
});

test('every leaf in covered chunks converts, with the Java model choice for its position and the model for its distance', () => {
  const env = setup();
  settle(env);
  for (const [key, type] of env.blocks) {
    if (!type.includes('leaves')) continue;
    assert.equal(type, 'bct_t:m_oak_leaves', key + ' converted, hidden or next to the player alike');
    const states = env.states.get(key);
    assert.equal(states['bct:t'], javaModelIndex(at(key), [1, 1, 1, 1], 'java-26.2-multipart-block-position'), key);
    assert.equal(states['bct:persistent_bit'], 0);
  }
  assert.equal(env.states.get('9,65,8')['bct:look'], 0, 'distance 2: the inner model');
  assert.equal(env.states.get('11,65,8')['bct:look'], 0, 'distance 4');
  assert.equal(env.states.get('12,65,8')['bct:look'], 1, 'distance 5: the outer model');
  assert.equal(env.states.get('13,65,8')['bct:look'], 1, 'distance 6');
  assert.equal(env.states.get('14,65,8')['bct:look'], 1, 'no log within 6 (Java distance 7)');
  const first = JSON.stringify([...env.states].filter(([key]) => env.blocks.get(key)?.startsWith('bct_t:')).sort());
  env.engine.leaves.setEnabled(true);
  settle(env);
  assert.equal(JSON.stringify([...env.states].filter(([key]) => env.blocks.get(key)?.startsWith('bct_t:')).sort()), first, 'the same every time');
});

test('a chunk converts only while its eight neighbors are loaded, and stays converted when players leave', () => {
  const loaded = new Set(['-1,-1', '-1,0', '-1,1', '0,-1', '0,0', '0,1', '1,-1', '1,0', '1,1']);
  const env = setup({ loaded: (cx, cz) => loaded.has(cx + ',' + cz), extra: [...tree(), ['20,61,8', 'minecraft:birch_leaves', { ...LEAF }]] });
  settle(env);
  assert.equal(env.blocks.get('9,65,8'), 'bct_t:m_oak_leaves');
  assert.equal(env.blocks.get('20,61,8'), 'minecraft:birch_leaves', 'chunk (1, 0) is at the edge of the loaded area');
  loaded.delete('-1,-1');
  settle(env);
  // Swapped back, the vanilla leaves would not count replacement logs and would decay.
  assert.equal(env.blocks.get('9,65,8'), 'bct_t:m_oak_leaves', 'out of coverage, the converted leaf stays');
});

test('converted leaves count a replacement log as a log and never decay next to it', () => {
  const log = { vanilla: 'minecraft:oak_log', block: 'bct_t:r_oak_log', mirror: { pillar_axis: 'bct:pillar_axis' }, axes: [], random: [] };
  const logDefinition = { format_version: '1.26.50', 'minecraft:block': { description: { identifier: log.block,
    states: { 'bct:pillar_axis': ['x', 'y', 'z'] } }, components: {} } };
  // Leaves are see-through, so the trunk inside the crown is replaced too (as in a converted pack).
  const withLog = { ...data, blocks: [log], open: [...data.open, 'minecraft:oak_leaves', 'bct_t:m_oak_leaves'] };
  // The player stands beside the tree, not inside its trunk: a log a player sees is swapped only once nobody does.
  const env = setup({ data: withLog, registry: withDefinitions(vanillaBlocks(), [...leafData.blocks.map(definition), logDefinition]),
    playerAt: { x: 2, y: 61, z: 2 } });
  settle(env, 32);
  for (let y = 61; y <= 64; y++) assert.equal(env.blocks.get(`8,${y},8`), 'bct_t:r_oak_log', 'the whole trunk was replaced');
  assert.equal(env.blocks.get('9,65,8'), 'bct_t:m_oak_leaves');
  const random = Math.random;
  Math.random = () => 0;  // every random tick checks the leaf
  try {
    for (let round = 0; round < 4; round++) for (const [key, type] of [...env.blocks]) if (type === 'bct_t:m_oak_leaves') tick(env, key);
  } finally { Math.random = random; }
  for (const key of ['7,63,8', '9,65,8', '10,65,8', '6,65,6', '11,65,8'])
    assert.equal(env.blocks.get(key), 'bct_t:m_oak_leaves', key + ' is within 4 of the replacement log');
});

test('leaves that appear without an event (a grown tree, a structure) convert at the next recheck', () => {
  const env = setup();
  settle(env);
  assert.equal(env.blocks.get('9,65,8'), 'bct_t:m_oak_leaves');
  env.blocks.set('3,70,3', 'minecraft:birch_leaves');
  env.states.set('3,70,3', { ...LEAF });
  settle(env, 40);
  assert.equal(env.blocks.get('3,70,3'), 'bct_t:m_birch_leaves');
  assert.equal(env.states.get('3,70,3')['bct:persistent_bit'], 0);
});

test('removing the log marks the leaves within 4 blocks; random ticks decay them with their drops', () => {
  const env = setup();
  settle(env);
  env.loot.of = block => [new env.ItemStack('minecraft:oak_sapling')];
  for (let y = 61; y <= 64; y++) env.blocks.delete(`8,${y},8`);
  env.world.afterEvents.playerBreakBlock.emit({ block: env.dimension.getBlock({ x: 8, y: 64, z: 8 }), dimension: env.dimension, player: env.player,
    brokenBlockPermutation: { type: { id: 'minecraft:oak_log' } } });
  assert.equal(env.states.get('9,65,8')['bct:update_bit'], 1, 'within 4 blocks of the removed log');
  assert.equal(env.states.get('13,65,8')['bct:update_bit'], 0, 'farther away nothing is marked yet');
  tick(env, '9,65,8');
  assert.equal(env.blocks.get('9,65,8'), undefined, 'no log within 4 blocks: decayed');
  const drops = env.entities.filter(entity => entity.isValid && entity.typeId === 'minecraft:item');
  assert.deepEqual(drops.map(entity => entity.getComponent('minecraft:item').itemStack.typeId), ['minecraft:oak_sapling']);
  assert.equal(env.states.get('10,65,8')['bct:update_bit'], 1, 'a decayed leaf marks the leaves next to it');
  env.world.gameRules.doTileDrops = false;
  tick(env, '10,65,8');
  assert.equal(env.blocks.get('10,65,8'), undefined);
  assert.equal(env.entities.filter(entity => entity.isValid && entity.typeId === 'minecraft:item').length, 1, 'doTileDrops off: no drops');
});

test('a marked leaf with a log within 4 blocks is unmarked; persistent leaves never decay', () => {
  const env = setup({ extra: [...tree(), ['20,61,20', 'minecraft:oak_leaves', { persistent_bit: true, update_bit: false }]] });
  settle(env);
  env.blocks.delete('8,64,8');
  env.world.afterEvents.playerBreakBlock.emit({ block: env.dimension.getBlock({ x: 8, y: 64, z: 8 }), dimension: env.dimension, player: env.player,
    brokenBlockPermutation: { type: { id: 'minecraft:oak_log' } } });
  assert.equal(env.states.get('9,64,8')['bct:update_bit'], 1);
  tick(env, '9,64,8');
  assert.equal(env.blocks.get('9,64,8'), 'bct_t:m_oak_leaves', 'the log below still holds it');
  assert.equal(env.states.get('9,64,8')['bct:update_bit'], 0);
  assert.equal(env.states.get('20,61,20')['bct:persistent_bit'], 1);
  const random = Math.random;
  Math.random = () => 0;
  try { for (let pass = 0; pass < 5; pass++) tick(env, '20,61,20'); } finally { Math.random = random; }
  assert.equal(env.blocks.get('20,61,20'), 'bct_t:m_oak_leaves', 'a persistent leaf with no log anywhere stays');
});

test('a marked leaf touching a log only by a corner does not decay', () => {
  const env = setup({ extra: [...tree(), ['20,61,20', 'minecraft:oak_leaves', { persistent_bit: false, update_bit: false }],
    ['21,62,21', 'minecraft:oak_log', { pillar_axis: 'y' }]] });
  settle(env);
  assert.equal(env.blocks.get('20,61,20'), 'bct_t:m_oak_leaves');
  env.states.set('20,61,20', { ...env.states.get('20,61,20'), 'bct:update_bit': 1 });
  tick(env, '20,61,20');
  assert.equal(env.blocks.get('20,61,20'), 'bct_t:m_oak_leaves', 'no path through leaves, but a log is right there');
  assert.equal(env.engine.leaves.status.decays, 0);
});

test('a marked leaf decays beside the trunk of a separate tree its leaves do not reach, as in vanilla', () => {
  const env = setup({ extra: [...tree(), ['20,61,20', 'minecraft:oak_leaves', { persistent_bit: false, update_bit: false }],
    ...[61, 62, 63].map(y => [`23,${y},20`, 'minecraft:oak_log', { pillar_axis: 'y' }])] });
  settle(env);
  assert.equal(env.blocks.get('20,61,20'), 'bct_t:m_oak_leaves');
  env.states.set('20,61,20', { ...env.states.get('20,61,20'), 'bct:update_bit': 1 });
  tick(env, '20,61,20');
  assert.equal(env.blocks.get('20,61,20'), undefined, 'a log 3 blocks away with no leaves between keeps nothing alive');
  assert.equal(env.engine.leaves.status.decays, 1);
});

test('an unmarked leaf with no log within 6 blocks is caught by a random check (logs gone without an event)', () => {
  const env = setup();
  settle(env);
  for (let y = 61; y <= 64; y++) env.blocks.delete(`8,${y},8`);
  const random = Math.random;
  Math.random = () => 0;
  try {
    tick(env, '14,65,8');
    assert.equal(env.states.get('14,65,8')['bct:update_bit'], 1, 'marked like the log removal would have');
    tick(env, '14,65,8');
  } finally { Math.random = random; }
  assert.equal(env.blocks.get('14,65,8'), undefined, 'and decays on a later tick');
});

test('a placed leaf converts at once; shears give only the leaf block; Silk Touch drops become the vanilla leaf', () => {
  const env = setup();
  settle(env);
  env.blocks.set('3,61,3', 'minecraft:oak_leaves'); env.states.set('3,61,3', { persistent_bit: true, update_bit: false });
  env.world.afterEvents.playerPlaceBlock.emit({ block: env.dimension.getBlock({ x: 3, y: 61, z: 3 }) });
  assert.equal(env.blocks.get('3,61,3'), 'bct_t:m_oak_leaves');
  assert.equal(env.states.get('3,61,3')['bct:persistent_bit'], 1);
  const shears = new env.ItemStack('minecraft:shears');
  env.world.beforeEvents.playerBreakBlock.emit({ block: env.dimension.getBlock({ x: 3, y: 61, z: 3 }), itemStack: shears, player: env.player, cancel: false });
  const sapling = env.dimension.spawnItem(new env.ItemStack('minecraft:oak_sapling'), { x: 3.4, y: 61.2, z: 3.6 });
  const leaf = env.dimension.spawnItem(new env.ItemStack('minecraft:oak_leaves'), { x: 3.5, y: 61.1, z: 3.5 });
  env.world.afterEvents.entitySpawn.emit({ entity: sapling });
  env.world.afterEvents.entitySpawn.emit({ entity: leaf });
  assert.equal(sapling.isValid, false, 'shears drop only the leaf block');
  assert.equal(leaf.isValid, true);
  const silk = env.dimension.spawnItem(new env.ItemStack('bct_t:m_oak_leaves'), { x: 9.5, y: 65.5, z: 8.5 });
  env.world.afterEvents.entitySpawn.emit({ entity: silk });
  assert.equal(silk.isValid, false);
  assert.ok(env.entities.some(entity => entity.isValid && entity.getComponent?.('minecraft:item')?.itemStack.typeId === 'minecraft:oak_leaves' &&
    entity.location.x === 9.5), 'the custom leaf item became oak leaves');
});

test('vanilla leaves next to a conversion that stay vanilla get update_bit cleared again', () => {
  // Chunk (1, 0) is left at the edge: its leaf at x 16 touches converted leaves at x 15.
  const loaded = new Set(['-1,-1', '-1,0', '-1,1', '0,-1', '0,0', '0,1', '1,-1', '1,0', '1,1']);
  const extra = [...tree(), ['15,65,8', 'minecraft:oak_leaves', { ...LEAF }], ['16,65,8', 'minecraft:oak_leaves', { ...LEAF }]];
  const env = setup({ extra, loaded: (cx, cz) => loaded.has(cx + ',' + cz), onSet(fake, location, value) {
    // The game marks vanilla leaves around a leaf that is replaced.
    if (!value.type.startsWith('bct_t:')) return;
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (let dz = -1; dz <= 1; dz++) {
      const key = `${location.x + dx},${location.y + dy},${location.z + dz}`;
      if (fake.blocks.get(key) === 'minecraft:oak_leaves') fake.states.set(key, { ...fake.states.get(key), update_bit: true });
    }
  } });
  settle(env);
  assert.equal(env.blocks.get('15,65,8'), 'bct_t:m_oak_leaves');
  assert.equal(env.blocks.get('16,65,8'), 'minecraft:oak_leaves');
  assert.equal(env.states.get('16,65,8').update_bit, false, 'no decay check is left pending at the edge');
});

test('when each look has its own weights, the model pick uses the weights of that look', () => {
  // Look 0 (near a log) can only pick turn 0, look 1 (5 to 7 away) only turn 3.
  const oak = { ...leafData.blocks[0], turn: { ...leafData.blocks[0].turn, byLook: [[1, 0, 0, 0], [0, 0, 0, 1]] } };
  const env = setup({ data: { ...data, leaves: { ...leafData, blocks: [oak, leafData.blocks[1]] } }, replace: { farDistance: 0 } });
  settle(env);
  const converted = [...env.blocks].filter(([, type]) => type === 'bct_t:m_oak_leaves');
  assert.ok(converted.length > 0);
  for (const [key] of converted) {
    const states = env.states.get(key);
    assert.equal(states['bct:t'], states['bct:look'] === 1 ? 3 : 0, key + ' look ' + states['bct:look']);
  }
});

test('after a fast flight the trees around the player convert within two seconds', () => {
  const extra = [];
  // One small crown in every chunk along a 400-block strip.
  for (let cx = -1; cx <= 26; cx++) for (let cz = -1; cz <= 1; cz++) for (let x = 6; x <= 9; x++) for (let z = 6; z <= 9; z++)
    extra.push([`${cx * 16 + x},70,${cz * 16 + z}`, 'minecraft:birch_leaves', { ...LEAF }]);
  const env = setup({ extra, playerAt: { x: 8, y: 72, z: 8 }, costs: GAME_COSTS, config: { chunkRadius: 32 }, replace: { farDistance: 0 } });
  for (let tick = 0; tick < 200; tick++) { env.player.location = { x: 8 + tick * 2, y: 72, z: 8 }; env.step(1); }
  env.step(40);
  const end = Math.floor(env.player.location.x / 16);
  for (let cx = end - 2; cx <= Math.min(end + 2, 26); cx++)
    assert.equal(env.blocks.get(`${cx * 16 + 7},70,7`), 'bct_t:m_birch_leaves', 'chunk ' + cx + ' next to the player');
});

test('conversion goes nearest chunk first, in bulk, within the time budget', () => {
  const extra = [];
  for (let cx = -2; cx <= 2; cx++) for (let x = 0; x < 16; x++) for (let z = 0; z < 16; z++) extra.push([`${cx * 16 + x},70,${z}`, 'minecraft:birch_leaves', { ...LEAF }]);
  const order = [];
  // Game calls cost time (GAME_COSTS), so the engine's budget spreads the work over ticks.
  // Waiting off (farDistance 0): no chunk is held back for being in view, so the order is by distance alone.
  const env = setup({ extra, playerAt: { x: 8, y: 61, z: 8 }, costs: GAME_COSTS, replace: { farDistance: 0 },
    onSet(fake, location) { const chunk = Math.floor(location.x / 16); if (!order.includes(chunk)) order.push(chunk); } });
  // Written in bulk, the five chunks convert within a dozen ticks (a third of a second).
  env.step(12);
  assert.equal([...env.blocks.values()].filter(type => type === 'bct_t:m_birch_leaves').length, 5 * 256, 'every leaf after twelve ticks');
  const budget = env.engine.settings().budgetMs;
  assert.ok(Math.max(...env.stepTimes) < budget + 1, 'every tick within the budget: ' + Math.max(...env.stepTimes).toFixed(2) + ' ms');
  assert.equal(order[0], 0, 'the player\'s own chunk first');
  assert.ok(order.indexOf(2) > order.indexOf(1) && order.indexOf(-2) > order.indexOf(-1), 'nearer chunks before farther ones: ' + order);
});

/** The game marks the vanilla leaves around a leaf that turns into another block (onSet hook). */
function marksAround(fake, location, value) {
  if (!value.type.startsWith('bct_t:')) return;
  for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (let dz = -1; dz <= 1; dz++) {
    const key = `${location.x + dx},${location.y + dy},${location.z + dz}`;
    if (fake.blocks.get(key) === 'minecraft:oak_leaves') fake.states.set(key, { ...fake.states.get(key), update_bit: true });
  }
}

test('a leaf a player could see waits until no player could; hidden leaves and leaves out of view convert at once', () => {
  // The player stands south of the tree looking at it; a leaf walled in by stone in front, another behind the player.
  const walled = ['7,61,14', '9,61,14', '8,61,13', '8,61,15', '8,62,14'].map(key => [key, 'minecraft:stone']);
  const env = setup({ playerAt: { x: 8, y: 61, z: 18 }, view: NORTH,
    extra: [...tree(), ...walled, ['8,61,14', 'minecraft:oak_leaves', { ...LEAF }], ['8,63,24', 'minecraft:oak_leaves', { ...LEAF }]] });
  settle(env);
  assert.equal(env.blocks.get('9,65,8'), 'minecraft:oak_leaves', 'the tree in front of the player waits');
  assert.equal(env.blocks.get('14,65,8'), 'minecraft:oak_leaves');
  assert.equal(env.blocks.get('8,61,14'), 'bct_t:m_oak_leaves', 'a leaf nothing shows converts, even in view');
  assert.equal(env.blocks.get('8,63,24'), 'bct_t:m_oak_leaves', 'a leaf behind the player converts');
  assert.ok(env.engine.leaves.status.waiting > 70, 'the tree waits');
  env.player.view = SOUTH; // turns round
  settle(env, 4);
  assert.equal(env.blocks.get('9,65,8'), 'bct_t:m_oak_leaves');
  assert.equal(env.states.get('9,65,8')['bct:look'], 0, 'distance 2: the inner model, worked out as it converts');
  assert.equal(env.states.get('12,65,8')['bct:look'], 1, 'distance 5: the outer model');
  assert.equal(env.engine.leaves.status.waiting, 0);
  // Converted leaves in view still take decay marks and new looks: those are not swaps.
  env.player.view = NORTH;
  settle(env, 1);
  env.blocks.delete('8,64,8');
  env.world.afterEvents.playerBreakBlock.emit({ block: env.dimension.getBlock({ x: 8, y: 64, z: 8 }), dimension: env.dimension, player: env.player,
    brokenBlockPermutation: { type: { id: 'minecraft:oak_log' } } });
  assert.equal(env.states.get('9,64,8')['bct:update_bit'], 1);
  tick(env, '9,64,8');
  assert.equal(env.blocks.get('9,64,8'), 'bct_t:m_oak_leaves', 'the log below still holds it');
  assert.equal(env.states.get('9,64,8')['bct:update_bit'], 0);
});

test('right after joining, leaves in view beyond nearDistance convert and nearer ones wait until the player turns', () => {
  const env = setup({ playerAt: { x: 8, y: 61, z: 18 }, view: NORTH, replace: { nearDistance: 9, joinTicks: 40 } });
  settle(env, 8);
  assert.equal(env.blocks.get('8,63,10'), 'minecraft:oak_leaves', 'in view and near: waits');
  assert.equal(env.blocks.get('8,63,6'), 'bct_t:m_oak_leaves', 'in view beyond the near distance: converted while the area loads');
  assert.equal(env.blocks.get('14,65,8'), 'bct_t:m_oak_leaves');
  settle(env, 16);
  assert.equal(env.blocks.get('8,63,10'), 'minecraft:oak_leaves', 'no flip after the window either');
  env.player.view = SOUTH;
  settle(env, 4);
  assert.equal(env.blocks.get('8,63,10'), 'bct_t:m_oak_leaves');
});

test('waiting leaves keep update_bit as it was while the leaves next to them convert', () => {
  const env = setup({ playerAt: { x: 8, y: 61, z: 18 }, view: NORTH, replace: { nearDistance: 9, joinTicks: 40 }, onSet: marksAround });
  settle(env, 8);
  const left = [...env.blocks].filter(([, type]) => type === 'minecraft:oak_leaves').map(([key]) => key);
  assert.ok(left.length > 0 && left.length < 77, left.length + ' leaves wait, the rest of the tree converted');
  for (const key of left) assert.equal(env.states.get(key).update_bit, false, key + ' has no decay check pending');
  env.player.view = SOUTH;
  settle(env, 4);
  assert.equal([...env.blocks.values()].filter(type => type === 'minecraft:oak_leaves').length, 0);
});

test('a leaf placed where a player looks waits until no player could see it', () => {
  const env = setup();
  settle(env);
  env.blocks.set('9,61,9', 'minecraft:oak_leaves'); env.states.set('9,61,9', { persistent_bit: true, update_bit: false });
  env.world.afterEvents.playerPlaceBlock.emit({ block: env.dimension.getBlock({ x: 9, y: 61, z: 9 }) });
  settle(env, 4);
  assert.equal(env.blocks.get('9,61,9'), 'minecraft:oak_leaves', 'the player looks down at it');
  env.player.view = UP;
  settle(env, 4);
  assert.equal(env.blocks.get('9,61,9'), 'bct_t:m_oak_leaves');
  assert.equal(env.states.get('9,61,9')['bct:persistent_bit'], 1);
});

test('replace.farDistance 0 lets leaves convert in view too', () => {
  const env = setup({ playerAt: { x: 8, y: 61, z: 18 }, view: NORTH, replace: { farDistance: 0 } });
  settle(env);
  assert.equal(env.blocks.get('9,65,8'), 'bct_t:m_oak_leaves');
  assert.equal(env.engine.leaves.status.waiting, 0);
});
