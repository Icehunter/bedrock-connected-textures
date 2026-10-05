import test from 'node:test';
import assert from 'node:assert/strict';
import { fakeBedrock, vanillaBlocks, withDefinitions, definitionsFor } from './fake_bedrock.mjs';
import { startEngine } from '../engine/engine.mjs';
import { publishSources } from '../engine/publisher.mjs';
import { checksum } from '../engine/sources.mjs';
import { javaModelIndex } from '../engine/tiles.mjs';

const LEAVES = ['minecraft:oak_leaves'];
const PANE = { north: 'bct:connection_north', south: 'bct:connection_south', west: 'bct:connection_west', east: 'bct:connection_east' };
const block = (vanilla, extra = {}) => ({ vanilla, block: 'bct_t:r_' + vanilla.split(':')[1], mirror: {}, axes: [], random: [], ...extra });
const data = {
  format_version: 1,
  blocks: [
    block('minecraft:stone'),
    block('minecraft:oak_log', { mirror: { pillar_axis: 'bct:pillar_axis' }, leafGuard: { radius: 6, leaves: LEAVES }, cost: 8 }),
    block('minecraft:packed_mud', { models: [{ state: 'bct:g0', weights: [2, 2, 1, 4], selection: 'java-26.2-multipart-block-position' }] }),
    block('minecraft:glass_pane', { open: true, pane: PANE, bools: Object.keys(PANE).map(side => 'minecraft:connection_' + side),
      mirror: Object.fromEntries(Object.entries(PANE).map(([side, state]) => ['minecraft:connection_' + side, state])) }),
  ],
  open: ['minecraft:air', 'minecraft:water', 'minecraft:oak_leaves', 'minecraft:glass_pane'],
  solid: ['minecraft:stone', 'minecraft:bedrock', 'minecraft:oak_log', 'minecraft:packed_mud'],
  paneConnect: ['minecraft:glass_pane'],
};
const vanilla = vanillaBlocks();
const registry = withDefinitions(vanilla, definitionsFor(data, vanilla));

function packet(value) {
  const text = JSON.stringify(value);
  return { engine: 'replace', provider: 'pack', digest: checksum(text), parts: text.match(/[\s\S]{1,750}/g) };
}

/** A 16x16 stone floor at y=60 over bedrock, the given blocks, a player standing on it and replace settings for the test. */
function setup({ extra = [], config = {}, playerAt = { x: 8, y: 61, z: 8 }, onSet } = {}) {
  const blocks = new Map(), states = new Map();
  for (let x = 0; x < 16; x++) for (let z = 0; z < 16; z++) { blocks.set(`${x},60,${z}`, 'minecraft:stone'); blocks.set(`${x},59,${z}`, 'minecraft:bedrock'); }
  for (const [key, type, state] of extra) { if (type === 'minecraft:air') blocks.delete(key); else blocks.set(key, type); if (state) states.set(key, state); }
  const fake = fakeBedrock({ blocks, states, registry, onSet: (...args) => onSet?.(fake, ...args) });
  const engine = startEngine(fake.api);
  publishSources({ system: fake.system, world: fake.world, sources: [packet(data)] });
  fake.system.sendScriptEvent('bct:config', JSON.stringify({ replace: { chunkRadius: 1, yBand: 6, swapBack: 1, ...config } }));
  const player = playerAt ? fake.player(playerAt) : undefined;
  return { ...fake, engine, player };
}
const settle = (env, passes = 12) => env.step(passes);
const rescan = env => { env.engine.replaceScanner.reset(); settle(env, 4); };

test('logs keep vanilla leaves from decaying: update_bit is cleared again after a swap', () => {
  const leaves = ['5,63,5', '6,63,5', '4,63,5', '5,63,6'];
  const env = setup({
    extra: [['5,61,5', 'minecraft:oak_log', { pillar_axis: 'y' }], ['5,62,5', 'minecraft:oak_log', { pillar_axis: 'y' }],
      ...leaves.map(key => [key, 'minecraft:oak_leaves', { persistent_bit: false, update_bit: false }]),
      ['5,64,5', 'minecraft:oak_leaves', { persistent_bit: true, update_bit: false }]],
    // The game sets update_bit on leaves next to a log that changes.
    onSet(fake, location, value) {
      if (!value.type.includes('oak_log')) return;
      for (const key of [...leaves, '5,64,5']) fake.states.set(key, { ...fake.states.get(key), update_bit: true });
    },
  });
  settle(env);
  assert.equal(env.blocks.get('5,62,5'), 'bct_t:r_oak_log');
  assert.deepEqual(env.states.get('5,62,5'), { 'bct:pillar_axis': 'y' }, 'pillar_axis is kept exactly');
  for (const key of leaves) assert.equal(env.states.get(key).update_bit, false, key + ' has no decay check pending');
  assert.equal(env.states.get('5,64,5').update_bit, true, 'persistent leaves are left alone');
  env.player.location = { x: 500, y: 61, z: 500 };
  settle(env, 30);
  assert.deepEqual(env.states.get('5,62,5'), { pillar_axis: 'y' });
  for (const key of leaves) assert.equal(env.states.get(key).update_bit, false, 'swapping back also clears it');
});

test('logs stay vanilla while a nearby leaf has a decay check pending', () => {
  const env = setup({ extra: [['5,61,5', 'minecraft:oak_log', { pillar_axis: 'x' }], ['5,62,5', 'minecraft:oak_leaves', { persistent_bit: false, update_bit: true }]] });
  settle(env);
  assert.equal(env.blocks.get('5,61,5'), 'minecraft:oak_log');
});

test('the swap budget charges logs for their leaf scan', () => {
  const extra = [];
  for (let x = 0; x < 8; x++) extra.push([`${x},61,0`, 'minecraft:oak_log', { pillar_axis: 'y' }]);
  const env = setup({ extra, config: { maxSwapsPerTick: 8 } });
  for (let pass = 0; pass < 3; pass++) env.step(1);
  const logs = [...env.blocks.values()].filter(type => type === 'bct_t:r_oak_log').length;
  assert.ok(logs <= 3, logs + ' logs in three ticks');
});

test('weighted Java models are picked from the position like Java does', () => {
  const env = setup({ extra: [['13,61,6', 'minecraft:packed_mud']] });
  settle(env);
  assert.equal(env.blocks.get('13,61,6'), 'bct_t:r_packed_mud');
  assert.equal(env.states.get('13,61,6')['bct:g0'], javaModelIndex({ x: 13, y: 61, z: 6 }, [2, 2, 1, 4], 'java-26.2-multipart-block-position'));
});

test('a torch only needs a sturdy face: the block under it is replaced and stays so when the torch goes', () => {
  const env = setup({ extra: [['3,61,3', 'minecraft:torch']] });
  settle(env);
  assert.equal(env.blocks.get('3,60,3'), 'bct_t:r_stone');
  env.blocks.delete('3,61,3');
  env.world.afterEvents.playerBreakBlock.emit({ block: env.dimension.getBlock({ x: 3, y: 61, z: 3 }), dimension: env.dimension, player: env.player,
    brokenBlockPermutation: { type: { id: 'minecraft:torch' } } });
  rescan(env);
  assert.equal(env.blocks.get('3,60,3'), 'bct_t:r_stone', 'no flip either way');
  const event = { block: env.dimension.getBlock({ x: 9, y: 61, z: 9 }), permutationToPlace: { type: 'minecraft:torch' }, cancel: false };
  env.world.beforeEvents.playerPlaceBlock.emit(event);
  assert.equal(event.cancel, false, 'placing on a replacement is never cancelled');
});

test('panes keep the vanilla connections and refresh them when a neighbor changes', () => {
  const sides = { 'minecraft:connection_north': false, 'minecraft:connection_south': false, 'minecraft:connection_west': true, 'minecraft:connection_east': false };
  const env = setup({ extra: [['6,61,6', 'minecraft:glass_pane', sides], ['5,61,6', 'minecraft:stone']] });
  settle(env);
  assert.equal(env.blocks.get('6,61,6'), 'bct_t:r_glass_pane');
  assert.deepEqual(env.states.get('6,61,6'), { 'bct:connection_north': 0, 'bct:connection_south': 0, 'bct:connection_west': 1, 'bct:connection_east': 0 },
    'boolean vanilla states are mirrored as 0 or 1');
  env.blocks.set('7,61,6', 'minecraft:glass_pane');
  env.world.afterEvents.playerPlaceBlock.emit({ block: env.dimension.getBlock({ x: 7, y: 61, z: 6 }) });
  assert.equal(env.states.get('6,61,6')['bct:connection_east'], 1, 'a pane placed next to it joins');
  env.player.location = { x: 500, y: 61, z: 500 };
  settle(env, 30);
  assert.equal(env.blocks.get('6,61,6'), 'minecraft:glass_pane');
  assert.equal(env.states.get('6,61,6')['minecraft:connection_east'], true, 'swapping back keeps the refreshed connections');
});
