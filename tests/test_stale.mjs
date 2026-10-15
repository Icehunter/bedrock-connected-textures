import test from 'node:test';
import assert from 'node:assert/strict';
import { fakeBedrock, vanillaBlocks } from './fake_bedrock.mjs';
import { createStale } from '../engine/stale.mjs';

const OLD = [['bct_old:r_oak_log', 'minecraft:oak_log'], ['bct_old:m_acacia_leaves', 'minecraft:acacia_leaves'], ['bct_old:grass_surface', null]];
const NEW = [['bct_new:r_oak_log', 'minecraft:oak_log']];

function setup({ blocks = [], states = [], known = false, ready = true } = {}) {
  const registry = new Map(vanillaBlocks());
  if (known) for (const [type] of OLD) registry.set(type, new Map());
  for (const [type] of NEW) registry.set(type, new Map());
  const fake = fakeBedrock({ blocks: new Map(blocks), registry });
  for (const [key, value] of states) fake.states.set(key, value);
  const player = fake.player({ x: 8, y: 70, z: 8 });
  const env = { fake, player, types: OLD, ready, targets: [], logs: [], chunks: [] };
  env.make = () => createStale({ api: fake.api, log: message => env.logs.push(message), now: fake.api.now, types: () => env.types, chunks: () => env.chunks,
    players: () => fake.world.players, far: { setTargets: (list, label) => env.targets.push({ list, label }) }, ready: () => env.ready, stall: 6000 });
  env.stale = env.make();
  env.go = (rounds = 3) => { for (let i = 0; i < rounds; i++) { env.stale.tick(); env.stale.run(fake.api.now() + 1); } };
  return env;
}

const swapped = [['3,70,3', 'bct_old:r_oak_log'], ['4,70,3', 'bct_old:m_acacia_leaves'], ['5,70,3', 'bct_old:grass_surface'], ['6,70,3', 'bct_new:r_oak_log']];
const swappedStates = [['3,70,3', { 'bct:pillar_axis': 'x' }], ['4,70,3', { 'bct:persistent_bit': 1, 'bct:update_bit': 0 }]];

test('blocks of a pack that is gone become their vanilla blocks, with the states they kept', () => {
  const env = setup({ blocks: swapped, states: swappedStates });
  env.go(1);
  env.types = NEW;
  env.go();
  const { blocks, states } = env.fake;
  assert.equal(blocks.get('3,70,3'), 'minecraft:oak_log');
  assert.deepEqual(states.get('3,70,3'), { pillar_axis: 'x' });
  assert.equal(blocks.get('4,70,3'), 'minecraft:acacia_leaves');
  assert.deepEqual(states.get('4,70,3'), { persistent_bit: true, update_bit: false });
  assert.equal(blocks.has('5,70,3'), false, 'an overlay surface or edge becomes air');
  assert.equal(blocks.get('6,70,3'), 'bct_new:r_oak_log', 'a block of the pack in use stays');
});

test('nothing is touched before the pack data has settled', () => {
  const env = setup({ blocks: swapped, states: swappedStates, ready: false });
  env.go(1);
  env.types = NEW;
  env.go();
  assert.equal(env.fake.blocks.get('3,70,3'), 'bct_old:r_oak_log');
});

test('a pack that is still loaded keeps its blocks', () => {
  const env = setup({ blocks: swapped, states: swappedStates });
  env.go();
  env.types = [...OLD, ...NEW];
  env.go();
  assert.equal(env.fake.blocks.get('3,70,3'), 'bct_old:r_oak_log');
});

test('a pack seen in an earlier session is remembered in the world', () => {
  const env = setup({ blocks: swapped, states: swappedStates });
  env.go(1);
  const second = env.make();
  env.types = NEW;
  second.tick();
  second.run(env.fake.api.now() + 1);
  assert.equal(env.fake.blocks.get('3,70,3'), 'minecraft:oak_log');
});

test('chunks far from the players are loaded through the far worker, then forgotten', () => {
  const env = setup({ blocks: [['803,70,803', 'bct_old:r_oak_log']] });
  env.go(1);
  env.types = NEW;
  env.chunks = [{ dimension: 'minecraft:overworld', cx: 50, cz: 50 }];
  env.stale.tick();
  const first = env.targets.at(-1);
  assert.ok(first.list, 'the far worker is given the chunks');
  assert.ok(first.list().some(chunk => chunk.cx === 50 && chunk.cz === 50));
  assert.match(first.label, /pack/i);
  env.fake.player({ x: 800, y: 70, z: 800 });
  env.go();
  assert.equal(env.fake.blocks.get('803,70,803'), 'minecraft:oak_log');
  assert.equal(env.targets.at(-1).list, undefined, 'the far worker goes back to its own work');
  env.targets.length = 0;
  env.go();
  assert.equal(env.targets.length, 0, 'done: nothing more is asked of it');
  assert.equal([...env.fake.properties.keys()].some(name => name.startsWith('bct:stale')), false, 'the saved chunk list is gone');
});

test('blocks whose types the game does not know are found without naming them', () => {
  const env = setup({ blocks: swapped, states: swappedStates, known: false });
  env.go(1);
  env.types = NEW;
  env.go();
  const { blocks, states } = env.fake;
  assert.equal(blocks.get('3,70,3'), 'minecraft:oak_log');
  assert.deepEqual(states.get('3,70,3'), { pillar_axis: 'x' });
  assert.equal(blocks.get('4,70,3'), 'minecraft:acacia_leaves');
  assert.equal(blocks.has('5,70,3'), false, 'an overlay surface or edge becomes air');
  assert.equal(blocks.get('6,70,3'), 'bct_new:r_oak_log', 'a block of the pack in use stays');
  assert.equal(env.logs.some(message => /refus/i.test(message)), false, env.logs.join(' | '));
});

test('a block of a type that is not a removed pack block is left alone', () => {
  const env = setup({ blocks: [...swapped, ['7,70,3', 'minecraft:stone'], ['8,70,3', 'mod:other']], known: false });
  env.go(1);
  env.types = NEW;
  env.go();
  assert.equal(env.fake.blocks.get('7,70,3'), 'minecraft:stone');
  assert.equal(env.fake.blocks.get('8,70,3'), 'mod:other');
});

test('blocks of a pack this world never recorded are reverted by their names', () => {
  const env = setup({ blocks: [...swapped, ['7,70,3', 'minecraft:stone'], ['8,70,3', 'mod:other']], states: swappedStates });
  env.types = NEW;
  env.chunks = [{ dimension: 'minecraft:overworld', cx: 0, cz: 0 }];
  env.go();
  const { blocks } = env.fake;
  assert.equal(blocks.get('3,70,3'), 'minecraft:oak_log', 'r_<block> becomes the vanilla block');
  assert.equal(blocks.get('4,70,3'), 'minecraft:acacia_leaves', 'm_<leaf> becomes the vanilla leaf');
  assert.equal(blocks.has('5,70,3'), false, 'any other block of a bct_ pack becomes air');
  assert.equal(blocks.get('6,70,3'), 'bct_new:r_oak_log', 'a block of the pack in use stays');
  assert.equal(blocks.get('7,70,3'), 'minecraft:stone');
  assert.equal(blocks.get('8,70,3'), 'mod:other', 'only bct_ packs are touched');
});

test('the sweep for unrecorded packs runs once per world', () => {
  const env = setup({ blocks: swapped, states: swappedStates });
  env.types = NEW;
  env.chunks = [{ dimension: 'minecraft:overworld', cx: 0, cz: 0 }];
  env.go();
  env.fake.blocks.set('9,70,3', 'bct_old:r_oak_log');
  const second = env.make();
  second.tick();
  second.run(env.fake.api.now() + 1);
  assert.equal(env.fake.blocks.get('9,70,3'), 'bct_old:r_oak_log', 'a new session does not sweep again');
});

test('the sweep for unrecorded packs leaves the blocks of the loaded packs alone', () => {
  const env = setup({ blocks: swapped, states: swappedStates });
  env.types = [...OLD, ...NEW];
  env.chunks = [{ dimension: 'minecraft:overworld', cx: 0, cz: 0 }];
  env.go();
  assert.equal(env.fake.blocks.get('3,70,3'), 'bct_old:r_oak_log');
});

test('chunks that never load are given up on after a while', () => {
  const env = setup({ blocks: swapped });
  env.go(1);
  env.types = NEW;
  env.chunks = [{ dimension: 'minecraft:overworld', cx: 90, cz: 90 }];
  env.go(1);
  assert.deepEqual(env.targets.at(-1).list(), [{ dimension: 'minecraft:overworld', cx: 90, cz: 90 }]);
  env.fake.system.currentTick += 7000;
  env.stale.tick();
  assert.equal(env.targets.at(-1).list, undefined, 'it gave up');
});

test('giving up on chunks that never load does not mark the world clean; the next session sweeps them again', () => {
  const env = setup({ blocks: swapped });
  env.go(1);
  env.types = NEW;
  env.chunks = [{ dimension: 'minecraft:overworld', cx: 90, cz: 90 }];
  env.go(1);
  env.fake.system.currentTick += 7000;
  env.stale.tick();
  assert.equal(env.stale.status.active, false, 'it gave up');
  assert.equal(env.player.messages.filter(message => /could not be updated/.test(message)).length, 1, 'the players are told once');
  assert.match(env.player.messages.at(-1), /rejoin/i);
  assert.match(env.fake.world.getDynamicProperty('bct:made:0') ?? '', /bct_old:r_oak_log/, 'the removed pack is still remembered');
  env.stale.tick();
  env.stale.tick();
  assert.equal(env.stale.status.active, false, 'it does not start over in the same session');
  assert.equal(env.player.messages.filter(message => /could not be updated/.test(message)).length, 1, 'and does not tell them again');
  const next = env.make();
  next.tick();
  assert.equal(next.status.active, true, 'a later session sweeps the chunks again');
  assert.ok(next.status.pending >= 1, 'the chunks that never loaded are pending again');
});

test('a chunk that is not loaded is skipped without stopping the others', () => {
  const env = setup({ blocks: [['3,70,3', 'bct_old:r_oak_log'], ['35,70,3', 'bct_old:r_oak_log']] });
  env.go(1);
  env.types = NEW;
  const { dimension } = env.fake;
  const loadedBefore = dimension.isChunkLoaded;
  dimension.isChunkLoaded = ({ x }) => x < 16;
  const containsBefore = dimension.containsBlock;
  dimension.containsBlock = (volume, filter, first) => { if (volume.from.x >= 16) throw new Error('chunk not loaded'); return containsBefore(volume, filter, first); };
  env.go(3);
  assert.equal(env.fake.blocks.get('3,70,3'), 'minecraft:oak_log', 'the loaded chunk is converted');
  assert.equal(env.fake.blocks.get('35,70,3'), 'bct_old:r_oak_log', 'the unloaded chunk waits');
  assert.equal(env.logs.filter(message => /stale /.test(message) && !/gave up/.test(message)).length, 0, 'no errors: ' + env.logs.join(' | '));
  dimension.isChunkLoaded = loadedBefore;
  dimension.containsBlock = containsBefore;
  env.go(3);
  assert.equal(env.fake.blocks.get('35,70,3'), 'minecraft:oak_log', 'it is converted once it loads');
});

test('the world is not rewritten every tick while chunks are pending', () => {
  const env = setup({ blocks: swapped, states: swappedStates });
  env.go(1);
  env.fake.system.currentTick += 200;
  env.types = NEW;
  env.fake.dimension.isChunkLoaded = () => false;
  env.stale.tick();
  const before = env.fake.properties.get('bct:stale:0');
  let writes = 0;
  const set = env.fake.world.setDynamicProperty;
  env.fake.world.setDynamicProperty = (name, value) => { if (name.startsWith('bct:stale')) writes++; set(name, value); };
  for (let i = 0; i < 20; i++) env.stale.tick();
  assert.ok(before !== undefined);
  assert.equal(writes, 0, 'nothing new to save: ' + writes + ' writes');
});

test('a sweep that gave up starts over, with a fresh look at the registered types, when it is needed again', () => {
  const env = setup({ blocks: swapped });
  const api = env.fake.api;
  env.go(1);
  let lookups = 0, clock = 0;
  const getAll = api.BlockTypes.getAll;
  api.BlockTypes.getAll = () => { lookups++; return getAll(); };
  const stale = createStale({ api, log: () => {}, now: () => clock++, types: () => env.types, chunks: () => env.chunks,
    players: () => env.fake.world.players, far: { setTargets() {} }, ready: () => true, stall: 6000 });
  env.types = NEW;
  env.chunks = [{ dimension: 'minecraft:overworld', cx: 0, cz: 0 }];
  stale.tick();
  assert.equal(stale.run(clock + 2), true, 'the walk is suspended with chunks left');
  assert.equal(lookups, 1);
  env.fake.system.currentTick += 7000;
  stale.tick();
  assert.equal(stale.status.active, false, 'it gave up');
  env.types = [];
  stale.tick();
  assert.equal(stale.status.active, true, 'a removed pack needs the sweep again');
  stale.run(clock + 2);
  assert.equal(lookups, 2, 'a new walk looked at the registered types again');
});
