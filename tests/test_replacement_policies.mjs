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
    block('minecraft:cobblestone_slab', { shape: 'slab', mirror: { 'minecraft:vertical_half': 'bct:vertical_half' },
      axes: [['bct:x', 'x', 2]] }),
    block('minecraft:oak_fence', { shape: 'fence', bools: ['north', 'east', 'south', 'west'].map(side => 'minecraft:connection_' + side),
      mirror: Object.fromEntries(['north', 'east', 'south', 'west'].map(side => ['minecraft:connection_' + side, 'bct:connection_' + side])) }),
    block('minecraft:cobblestone_wall', { shape: 'wall', bools: ['wall_post_bit'],
      mirror: Object.fromEntries([...['north', 'east', 'south', 'west'].map(side => 'wall_connection_type_' + side), 'wall_post_bit']
        .map(name => [name, 'bct:' + name])) }),
    block('minecraft:oak_stairs', { shape: 'stairs', bools: ['upside_down_bit'],
      mirror: { weirdo_direction: 'bct:weirdo_direction', upside_down_bit: 'bct:upside_down_bit', 'minecraft:corner': 'bct:corner' } }),
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

test('slabs swap with their half and their water, and do not hide the faces next to them', () => {
  const env = setup({ extra: [['4,61,4', 'minecraft:cobblestone_slab', { 'minecraft:vertical_half': 'top' }],
    ['5,61,4', 'minecraft:cobblestone_slab', { 'minecraft:vertical_half': 'bottom' }],
    // Stone wrapped in stone on every side but one, where a slab is: the slab leaves part of its face open.
    ['10,61,10', 'minecraft:stone'], ['10,62,10', 'minecraft:stone'], ['9,61,10', 'minecraft:stone'], ['11,61,10', 'minecraft:stone'],
    ['10,61,9', 'minecraft:stone'], ['10,61,11', 'minecraft:cobblestone_slab', { 'minecraft:vertical_half': 'bottom' }]] });
  env.wet.add('5,61,4');
  settle(env);
  assert.equal(env.blocks.get('4,61,4'), 'bct_t:r_cobblestone_slab');
  assert.deepEqual(env.states.get('4,61,4'), { 'bct:vertical_half': 'top', 'bct:x': 0 }, 'the half and the pattern place');
  assert.ok(env.wet.has('5,61,4'), 'a waterlogged slab keeps its water');
  assert.equal(env.blocks.get('10,61,10'), 'bct_t:r_stone', 'a block next to a slab shows and is swapped');
  env.player.location = { x: 500, y: 61, z: 500 };
  settle(env, 30);
  assert.deepEqual(env.states.get('4,61,4'), { 'minecraft:vertical_half': 'top' }, 'swapping back restores the half');
  assert.ok(env.wet.has('5,61,4'), 'and keeps the water');
});

test('stairs take Java\'s corner from the stairs around them and change it when a neighbour goes', () => {
  // weirdo_direction 3 faces north, 0 east. A faces north with B, facing east, on its front side: an inner
  // corner. C faces north with D, facing east, on its back side: an outer corner.
  const stairs = (direction, upsideDown = false) => ({ weirdo_direction: direction, upside_down_bit: upsideDown, 'minecraft:corner': 'none' });
  const env = setup({ extra: [['6,61,6', 'minecraft:oak_stairs', stairs(3)], ['6,61,7', 'minecraft:oak_stairs', stairs(0)],
    ['10,61,6', 'minecraft:oak_stairs', stairs(3)], ['10,61,5', 'minecraft:oak_stairs', stairs(0)],
    // An upside-down stair does not turn a corner with a stair the right way up.
    ['3,61,10', 'minecraft:oak_stairs', stairs(3)], ['3,61,11', 'minecraft:oak_stairs', stairs(0, true)]] });
  settle(env);
  assert.equal(env.blocks.get('6,61,6'), 'bct_t:r_oak_stairs');
  assert.equal(env.states.get('6,61,6')['bct:corner'], 'inner_right', 'as vanilla shapes the same pair');
  assert.equal(env.states.get('10,61,6')['bct:corner'], 'outer_right');
  assert.equal(env.states.get('3,61,10')['bct:corner'], 'none');
  assert.equal(env.states.get('3,61,11')['bct:upside_down_bit'], 1);
  env.blocks.delete('6,61,7');
  env.world.afterEvents.playerBreakBlock.emit({ block: env.dimension.getBlock({ x: 6, y: 61, z: 7 }), dimension: env.dimension, player: env.player,
    brokenBlockPermutation: { type: { id: 'bct_t:r_oak_stairs' } } });
  assert.equal(env.states.get('6,61,6')['bct:corner'], 'none', 'the corner goes with the stair that made it');
  env.player.location = { x: 500, y: 61, z: 500 };
  settle(env, 30);
  assert.deepEqual(env.states.get('10,61,6'), { weirdo_direction: 3, upside_down_bit: false, 'minecraft:corner': 'outer_right' },
    'swapping back keeps the corner');
});

test('fences join wooden fences, gates turned across them and full blocks, as Java does', () => {
  const none = { 'minecraft:connection_north': false, 'minecraft:connection_east': false, 'minecraft:connection_south': false, 'minecraft:connection_west': false };
  const env = setup({ extra: [['5,61,12', 'minecraft:oak_fence', none], ['4,61,12', 'minecraft:stone'],
    ['6,61,12', 'minecraft:spruce_fence', none], ['5,61,13', 'minecraft:nether_brick_fence', none],
    // A gate facing east spans north to south, so the fence south of it joins; one facing north would not.
    ['5,61,11', 'minecraft:fence_gate', { 'minecraft:cardinal_direction': 'east', open_bit: false, in_wall_bit: false }]] });
  settle(env);
  assert.equal(env.blocks.get('5,61,12'), 'bct_t:r_oak_fence');
  assert.deepEqual(env.states.get('5,61,12'), { 'bct:connection_north': 1, 'bct:connection_east': 1, 'bct:connection_south': 0, 'bct:connection_west': 1 },
    'gate, spruce fence and stone join; the nether brick fence does not');
  env.blocks.delete('4,61,12');
  env.world.afterEvents.playerBreakBlock.emit({ block: env.dimension.getBlock({ x: 4, y: 61, z: 12 }), dimension: env.dimension, player: env.player,
    brokenBlockPermutation: { type: { id: 'bct_t:r_stone' } } });
  assert.equal(env.states.get('5,61,12')['bct:connection_west'], 0, 'the side lets go when the block goes');
});

test('walls join walls and full blocks, drop their post on a straight run and grow tall under a block, as Java does', () => {
  const wall = { wall_connection_type_north: 'none', wall_connection_type_east: 'none', wall_connection_type_south: 'none',
    wall_connection_type_west: 'none', wall_post_bit: true };
  const env = setup({ extra: [['3,61,14', 'minecraft:cobblestone_wall', wall], ['4,61,14', 'minecraft:cobblestone_wall', wall],
    ['5,61,14', 'minecraft:cobblestone_wall', wall], ['8,61,14', 'minecraft:cobblestone_wall', wall], ['9,61,14', 'minecraft:stone']] });
  settle(env);
  const sides = key => ['north', 'east', 'south', 'west'].map(side => env.states.get(key)['bct:wall_connection_type_' + side]).join(' ');
  assert.equal(env.blocks.get('4,61,14'), 'bct_t:r_cobblestone_wall');
  assert.equal(sides('4,61,14'), 'none short none short');
  assert.equal(env.states.get('4,61,14')['bct:wall_post_bit'], 0, 'the middle of a straight run has no post');
  assert.equal(env.states.get('3,61,14')['bct:wall_post_bit'], 1, 'the end of a run has one');
  assert.equal(sides('8,61,14'), 'none short none none', 'a wall joins the stone beside it');
  env.blocks.set('4,62,14', 'minecraft:stone');
  env.world.afterEvents.playerPlaceBlock.emit({ block: env.dimension.getBlock({ x: 4, y: 62, z: 14 }) });
  assert.equal(sides('4,61,14'), 'none tall none tall', 'a block on top makes the joined sides tall');
  assert.equal(env.states.get('4,61,14')['bct:wall_post_bit'], 0);
});

test('restore puts every swapped block back, stops converting for good and says when it is done', () => {
  const env = setup({ extra: [['4,61,4', 'minecraft:cobblestone_slab', { 'minecraft:vertical_half': 'top' }],
    ['6,61,6', 'minecraft:oak_stairs', { weirdo_direction: 1, upside_down_bit: true, 'minecraft:corner': 'none' }]] });
  settle(env);
  assert.equal(env.blocks.get('4,61,4'), 'bct_t:r_cobblestone_slab');
  assert.equal(env.blocks.get('5,60,5'), 'bct_t:r_stone');
  // A replacement no chunk list knows about (moved by something the engine did not see) is found by the sweep.
  env.blocks.set('12,61,12', 'bct_t:r_stone'); env.states.set('12,61,12', {});
  env.system.sendScriptEvent('bct:control', 'restore');
  settle(env, 40);
  assert.equal(env.blocks.get('12,61,12'), 'minecraft:stone', 'a stray replacement is put back too');
  assert.equal(env.blocks.get('4,61,4'), 'minecraft:cobblestone_slab');
  assert.deepEqual(env.states.get('4,61,4'), { 'minecraft:vertical_half': 'top' });
  assert.equal(env.blocks.get('6,61,6'), 'minecraft:oak_stairs');
  assert.ok(![...env.blocks.values()].some(type => type.startsWith('bct_t:')), 'no replacement block is left');
  assert.ok(env.player.messages.some(message => message.includes('back to vanilla')), 'the players are told when it is done');
  assert.equal(env.world.getDynamicProperty('bct:enabled'), false, 'the engine stays off, so nothing converts again');
  env.blocks.set('9,61,9', 'minecraft:cobblestone_slab');
  env.world.afterEvents.playerPlaceBlock.emit({ block: env.dimension.getBlock({ x: 9, y: 61, z: 9 }) });
  settle(env);
  assert.equal(env.blocks.get('9,61,9'), 'minecraft:cobblestone_slab', 'a block placed afterwards stays vanilla');
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
