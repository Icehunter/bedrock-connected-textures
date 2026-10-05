import test from 'node:test';
import assert from 'node:assert/strict';
import { fakeBedrock, vanillaBlocks, withDefinitions, definitionsFor } from './fake_bedrock.mjs';
import { startEngine } from '../engine/engine.mjs';
import { publishSources } from '../engine/publisher.mjs';
import { checksum } from '../engine/sources.mjs';
import { resolveSettings } from '../engine/settings.mjs';

const data = {
  format_version: 1,
  blocks: [
    { vanilla: 'minecraft:stone', block: 'bct_t:r_stone', mirror: {}, axes: [['bct:x', 'x', 2], ['bct:y', 'y', 2], ['bct:z', 'z', 2]], random: [] },
    { vanilla: 'minecraft:deepslate', block: 'bct_t:r_deepslate', mirror: { pillar_axis: 'bct:pillar_axis' }, axes: [['bct:x', 'x', 3]], random: [] },
    { vanilla: 'minecraft:end_stone', block: 'bct_t:r_end_stone', mirror: {}, axes: [],
      random: [{ state: 'bct:r0', count: 4, face: 'north', loops: 0, symmetry: 'none', weights: [1, 1, 1, 1] }] },
    { vanilla: 'minecraft:coal_ore', block: 'bct_t:r_coal_ore', mirror: {}, axes: [], random: [], xp: [0, 2], tool: { all: ['minecraft:is_pickaxe'], any: [] } },
    { vanilla: 'minecraft:diamond_ore', block: 'bct_t:r_diamond_ore', mirror: {}, axes: [], random: [], xp: [3, 7],
      tool: { all: ['minecraft:is_pickaxe'], any: ['minecraft:iron_tier', 'minecraft:diamond_tier', 'minecraft:netherite_tier'] } },
    { vanilla: 'minecraft:oak_log', block: 'bct_t:r_oak_log', mirror: { pillar_axis: 'bct:pillar_axis' }, axes: [], random: [], strip: 'minecraft:stripped_oak_log' },
    { vanilla: 'minecraft:stripped_oak_log', block: 'bct_t:r_stripped_oak_log', mirror: { pillar_axis: 'bct:pillar_axis' }, axes: [['bct:y', 'y', 2]], random: [] },
    { vanilla: 'minecraft:jungle_log', block: 'bct_t:r_jungle_log', mirror: { pillar_axis: 'bct:pillar_axis' }, axes: [], random: [] },
  ],
  open: ['minecraft:air', 'minecraft:water'],
  solid: ['minecraft:stone', 'minecraft:deepslate', 'minecraft:end_stone', 'minecraft:coal_ore', 'minecraft:diamond_ore', 'minecraft:oak_log',
    'minecraft:stripped_oak_log', 'minecraft:jungle_log', 'minecraft:bedrock'],
  // Cocoa hangs on the side of a jungle log and drops from anything else (the converter's needs_vanilla policy).
  needs: [{ blocks: ['minecraft:cocoa'], needs: ['minecraft:jungle_log'], side: 'side' }],
};
const vanilla = vanillaBlocks();
// The game rejects unknown block types and state values; so does the fake with this registry.
const registry = withDefinitions(vanilla, definitionsFor(data, vanilla));

function packet(engine, provider, value) {
  const text = JSON.stringify(value);
  return { engine, provider, digest: checksum(text), parts: text.match(/[\s\S]{1,750}/g) };
}

/** A 16x16 stone floor at y=60 over bedrock from y=56 to 59. */
function floor(extra = []) {
  const blocks = new Map(), states = new Map();
  for (let x = 0; x < 16; x++) for (let z = 0; z < 16; z++) {
    blocks.set(`${x},60,${z}`, 'minecraft:stone');
    for (let y = 56; y < 60; y++) blocks.set(`${x},${y},${z}`, 'minecraft:bedrock');
  }
  for (const [key, type, state] of extra) { if (type === 'minecraft:air') blocks.delete(key); else blocks.set(key, type); if (state) states.set(key, state); }
  return { blocks, states };
}

/** A floor with the given blocks, the engine with the test pack, and a player standing at playerAt looking along view (up by default). */
// Most tests here are about waiting while players look, which is off by default.
const WAITING = { farDistance: 48, nearDistance: 16, swapBack: 1 };

function setup({ extra = [], config = {}, playerAt = { x: 8, y: 61, z: 8 }, view, properties, registry: types = registry } = {}) {
  const fake = fakeBedrock({ ...floor(extra), registry: types });
  if (properties) for (const [name, value] of properties) fake.properties.set(name, value);
  const engine = startEngine(fake.api);
  const components = fake.startup();
  publishSources({ system: fake.system, world: fake.world, sources: [packet('replace', 'pack', data)] });
  fake.system.sendScriptEvent('bct:config', JSON.stringify({ replace: { chunkRadius: 1, yBand: 6, ...WAITING, ...config } }));
  const player = playerAt ? fake.player(playerAt) : undefined;
  if (player && view) player.view = view;
  return { ...fake, engine, player, components };
}

const settle = (env, passes = 12) => env.step(passes);
const swapped = env => [...env.blocks.values()].filter(type => type.startsWith('bct_t:')).length;
const UP = { x: 0, y: 1, z: 0 }, DOWN = { x: 0, y: -1, z: 0 }, NORTH = { x: 0, y: 0, z: -1 };
const at = key => { const [x, y, z] = key.split(',').map(Number); return { x, y, z }; };

/** Turns a player to look at the center of a block. */
function lookAt(player, { x, y, z }) {
  const head = player.getHeadLocation();
  player.view = { x: x + 0.5 - head.x, y: y + 0.5 - head.y, z: z + 0.5 - head.z };
}

/** A player places a block (the world changes first, then the after-event fires, as in the game). */
function place(env, key, type, state) {
  env.blocks.set(key, type);
  if (state) env.states.set(key, state); else env.states.delete(key);
  env.world.afterEvents.playerPlaceBlock.emit({ block: env.dimension.getBlock(at(key)), player: env.player, dimension: env.dimension });
}

/** A player breaks a block. */
function breakBlock(env, key) {
  const type = env.blocks.get(key);
  env.blocks.delete(key);
  env.world.afterEvents.playerBreakBlock.emit({ block: env.dimension.getBlock(at(key)), dimension: env.dimension, player: env.player,
    brokenBlockPermutation: { type: { id: type } } });
}

test('replace settings have defaults and validated overrides', () => {
  const settings = resolveSettings();
  assert.deepEqual(settings.replace, { chunkRadius: 4, yBand: 32, maxSwapsPerTick: 512, refreshTicks: 600, sliceMs: 4,
    viewAngle: 140, farDistance: 0, nearDistance: 0, joinTicks: 600, swapBack: 0 });
  assert.equal(resolveSettings({ replace: { maxSwapsPerTick: 64 } }).replace.maxSwapsPerTick, 64);
  assert.equal(resolveSettings({ replace: { farDistance: 0 } }).replace.farDistance, 0, 'farDistance 0 lets every change happen at once');
  assert.throws(() => resolveSettings({ replace: { maxSwapsPerTick: 100000 } }), /maxSwapsPerTick/);
  assert.throws(() => resolveSettings({ replace: { viewAngle: 400 } }), /viewAngle/);
  assert.throws(() => resolveSettings({ replace: { nearPlayer: 2 } }), /Unknown setting/, 'nothing near players turns vanilla any more');
});

test('shown blocks and the blocks behind them are swapped, with their pattern position as states', () => {
  const env = setup({ extra: [
    ['8,59,8', 'minecraft:stone'], ['8,58,8', 'minecraft:stone'], // a column under the floor
    ['3,61,3', 'minecraft:torch'],                    // stands on the stone below it
    ['100,60,100', 'minecraft:stone'],                // out of range
  ] });
  settle(env);
  assert.equal(env.blocks.get('8,60,8'), 'bct_t:r_stone');
  assert.deepEqual(env.states.get('5,60,6'), { 'bct:x': 1, 'bct:y': 0, 'bct:z': 0 });
  assert.equal(env.blocks.get('8,59,8'), 'bct_t:r_stone', 'the block behind a shown block is swapped, so digging never uncovers vanilla');
  assert.equal(env.blocks.get('8,58,8'), 'minecraft:stone', 'deeper blocks stay vanilla');
  assert.equal(env.blocks.get('3,60,3'), 'bct_t:r_stone', 'a torch only needs a sturdy face: the block under it is replaced too');
  assert.equal(env.blocks.get('100,60,100'), 'minecraft:stone', 'blocks outside the radius stay vanilla');
  assert.equal(env.engine.replacements.size, 257);
});

test('nothing turns vanilla where players stand, look or click', () => {
  const env = setup();
  settle(env);
  env.player.aim = { x: 7, y: 60, z: 7 };
  settle(env, 6);
  assert.equal(env.blocks.get('8,60,8'), 'bct_t:r_stone', 'the block under the feet stays replaced');
  assert.equal(env.blocks.get('7,60,7'), 'bct_t:r_stone', 'the block a player aims at stays replaced');
  for (const channel of ['playerBreakBlock', 'playerInteractWithBlock']) {
    const event = { block: env.dimension.getBlock({ x: 2, y: 60, z: 2 }), player: env.player, cancel: false };
    env.world.beforeEvents[channel].emit(event);
    assert.equal(event.cancel, false, channel + ' works on the replacement itself');
  }
  env.world.afterEvents.entitySpawn.emit({ entity: { typeId: 'minecraft:tnt', dimension: env.dimension, location: { x: 3.5, y: 61, z: 3.5 } } });
  settle(env, 2);
  assert.equal(env.blocks.get('3,60,3'), 'bct_t:r_stone', 'primed TNT changes nothing before it explodes');
});

test('breaking a replacement uncovers replaced blocks right away', () => {
  const env = setup({ extra: [['5,59,5', 'minecraft:stone'], ['5,58,5', 'minecraft:stone'], ['5,57,5', 'minecraft:stone']] });
  settle(env);
  assert.equal(env.blocks.get('5,59,5'), 'bct_t:r_stone');
  assert.equal(env.blocks.get('5,58,5'), 'minecraft:stone');
  env.blocks.delete('5,60,5');
  env.world.afterEvents.playerBreakBlock.emit({ block: env.dimension.getBlock({ x: 5, y: 60, z: 5 }), dimension: env.dimension, player: env.player,
    brokenBlockPermutation: { type: { id: 'bct_t:r_stone' } } });
  assert.equal(env.blocks.get('5,59,5'), 'bct_t:r_stone', 'the newly shown block was already replaced');
  assert.equal(env.blocks.get('5,58,5'), 'bct_t:r_stone', 'and the one behind it is swapped in the same tick');
  assert.equal(env.blocks.get('5,57,5'), 'minecraft:stone');
});

test('swap-back restores the exact vanilla permutation when players leave', () => {
  const env = setup({ extra: [['4,61,4', 'minecraft:deepslate', { pillar_axis: 'x' }], ['6,61,6', 'minecraft:end_stone']] });
  settle(env);
  assert.equal(env.blocks.get('4,61,4'), 'bct_t:r_deepslate');
  assert.deepEqual(env.states.get('4,61,4'), { 'bct:pillar_axis': 'x', 'bct:x': 1 });
  assert.ok([0, 1, 2, 3].includes(env.states.get('6,61,6')['bct:r0']));
  env.player.location = { x: 500, y: 61, z: 500 };
  settle(env, 30);
  assert.equal(env.blocks.get('4,61,4'), 'minecraft:deepslate');
  assert.deepEqual(env.states.get('4,61,4'), { pillar_axis: 'x' });
  assert.equal(env.blocks.get('8,60,8'), 'minecraft:stone');
  assert.equal(env.engine.replacements.size, 0);
  assert.equal(env.properties.get('bct:replace:index'), undefined, 'the ownership index is empty again');
});

test('by default swapped blocks stay swapped when players leave', () => {
  const env = setup({ config: { swapBack: 0 } });
  settle(env);
  assert.equal(env.blocks.get('8,60,8'), 'bct_t:r_stone');
  env.player.location = { x: 500, y: 61, z: 500 };
  settle(env, 30);
  assert.equal(env.blocks.get('8,60,8'), 'bct_t:r_stone', 'no swap-back, so nothing flips when the player returns');
  assert.equal(env.engine.replacements.status.reverts, 0);
});

test('swaps are spread across ticks within the per-tick budget', () => {
  const env = setup({ config: { maxSwapsPerTick: 10 } });
  let before = 0, passes = 0;
  while (env.engine.replacements.size < 256 && passes++ < 60) {
    env.step(1);
    const done = env.writes.length;
    assert.ok(done - before <= 10, `${done - before} swaps in one tick`);
    before = done;
  }
  assert.equal(env.engine.replacements.size, 256);
});

test('a new session keeps what a previous one swapped near players and swaps back the rest', () => {
  const first = setup({ extra: [['56,60,56', 'minecraft:stone'], ['56,59,56', 'minecraft:bedrock']] });
  settle(first);
  first.player.location = { x: 40, y: 61, z: 40 };
  settle(first, 12);
  assert.equal(first.blocks.get('56,60,56'), 'bct_t:r_stone');
  assert.equal(first.blocks.get('8,60,8'), 'bct_t:r_stone', 'still within range plus one chunk');
  first.engine.replacements.flush();
  const second = fakeBedrock({ blocks: new Map(first.blocks), states: new Map(first.states), registry });
  for (const [name, value] of first.properties) second.properties.set(name, value);
  const engine = startEngine(second.api);
  publishSources({ system: second.system, world: second.world, sources: [packet('replace', 'pack', data)] });
  second.system.sendScriptEvent('bct:config', JSON.stringify({ replace: { chunkRadius: 1, yBand: 6, swapBack: 1 } }));
  second.player({ x: 8, y: 61, z: 8 });
  let changedNear = false;
  for (let pass = 0; pass < 30; pass++) {
    second.step(1);
    if (second.blocks.get('8,60,8') !== 'bct_t:r_stone') changedNear = true;
  }
  assert.equal(changedNear, false, 'blocks near the player never flip at load');
  assert.equal(second.blocks.get('56,60,56'), 'minecraft:stone', 'blocks away from players go back to vanilla');
  assert.equal(engine.replacements.size, 256);
});

test('replacements moved by pistons or placed by structures are adopted with the pattern of their place', () => {
  // The player stands away from the moved blocks: a pattern fix where a player looks waits like any other change.
  const env = setup({ extra: [['8,62,8', 'bct_t:r_stone', { 'bct:x': 1, 'bct:y': 1, 'bct:z': 1 }]], playerAt: { x: 2, y: 61, z: 2 } });
  settle(env);
  assert.equal(env.blocks.get('8,62,8'), 'bct_t:r_stone', 'not swapped back');
  assert.deepEqual(env.states.get('8,62,8'), { 'bct:x': 0, 'bct:y': 0, 'bct:z': 0 }, 'x 8, y 62 and z 8 are all even');
  env.blocks.set('9,62,8', 'bct_t:r_stone'); env.states.set('9,62,8', { 'bct:x': 0, 'bct:y': 0, 'bct:z': 0 });
  env.world.afterEvents.pistonActivate.emit({ block: env.dimension.getBlock({ x: 10, y: 62, z: 8 }), dimension: env.dimension });
  settle(env, 4);
  assert.equal(env.blocks.get('9,62,8'), 'bct_t:r_stone');
  assert.deepEqual(env.states.get('9,62,8'), { 'bct:x': 1, 'bct:y': 0, 'bct:z': 0 }, 'the pattern follows the new position');
});

test('Silk Touch drops of a replacement become the vanilla item', () => {
  const env = setup();
  settle(env);
  const item = env.dimension.spawnItem(new env.ItemStack('bct_t:r_stone', 1), { x: 2.5, y: 60.5, z: 2.5 });
  env.world.afterEvents.entitySpawn.emit({ entity: item });
  assert.equal(item.isValid, false);
  const items = env.entities.filter(entity => entity.isValid && entity.typeId === 'minecraft:item');
  assert.deepEqual(items.map(entity => [entity.getComponent('minecraft:item').itemStack.typeId, entity.getComponent('minecraft:item').itemStack.amount]),
    [['minecraft:stone', 1]]);
});

test('replaced ores give their vanilla experience for the right tool, never with Silk Touch or in creative', () => {
  const env = setup({ extra: [['6,61,6', 'minecraft:coal_ore']] });
  settle(env);
  const random = Math.random;
  Math.random = () => 0.99;
  try {
    const tool = (id, ...tags) => { const stack = new env.ItemStack(id); tags.forEach(tag => stack.tags.add(tag)); return stack; };
    const pickaxe = tool('minecraft:iron_pickaxe', 'minecraft:is_pickaxe', 'minecraft:iron_tier');
    const orbs = () => env.entities.filter(entity => entity.isValid && entity.typeId === 'minecraft:xp_orb').length;
    const breakOre = (item, type = 'bct_t:r_coal_ore') => env.world.afterEvents.playerBreakBlock.emit({ block: env.dimension.getBlock({ x: 6, y: 61, z: 6 }),
      dimension: env.dimension, player: env.player, itemStackBeforeBreak: item, brokenBlockPermutation: { type: { id: type } } });
    breakOre(pickaxe);
    assert.equal(orbs(), 2, 'coal ore gives 0 to 2 points');
    breakOre(undefined);
    breakOre(tool('minecraft:iron_shovel', 'minecraft:is_shovel', 'minecraft:iron_tier'));
    assert.equal(orbs(), 2, 'no experience by hand or with the wrong tool: the ore drops nothing either');
    breakOre(tool('minecraft:stone_pickaxe', 'minecraft:is_pickaxe', 'minecraft:stone_tier'), 'bct_t:r_diamond_ore');
    assert.equal(orbs(), 2, 'no experience below the tier the ore needs');
    breakOre(pickaxe, 'bct_t:r_diamond_ore');
    assert.equal(orbs(), 9, 'diamond ore gives 3 to 7 points for an iron pickaxe');
    const silk = tool('minecraft:iron_pickaxe', 'minecraft:is_pickaxe', 'minecraft:iron_tier');
    silk.parts.set('minecraft:enchantable', { getEnchantment: name => name === 'silk_touch' ? { level: 1 } : undefined });
    breakOre(silk);
    assert.equal(orbs(), 9, 'no experience with Silk Touch');
    env.player.gameMode = 'Creative';
    breakOre(pickaxe);
    assert.equal(orbs(), 9, 'no experience in creative');
  } finally { Math.random = random; }
});

test('an axe strips a replaced log straight to the stripped log replacement', () => {
  const env = setup({ extra: [['6,61,6', 'minecraft:oak_log', { pillar_axis: 'y' }]] });
  settle(env);
  assert.equal(env.blocks.get('6,61,6'), 'bct_t:r_oak_log');
  const axe = new env.ItemStack('minecraft:iron_axe');
  axe.tags.add('minecraft:is_axe');
  const durability = { damage: 0, maxDurability: 250 };
  axe.parts.set('minecraft:durability', durability);
  env.player.equipment.set('Mainhand', axe);
  assert.ok([...env.components.values()].every(component => !component.onPlayerInteract),
    'no block gets an interact component: building against a replaced log works as in vanilla');
  const use = item => {
    const event = { block: env.dimension.getBlock({ x: 6, y: 61, z: 6 }), player: env.player, itemStack: item, isFirstEvent: true, cancel: false };
    env.world.beforeEvents.playerInteractWithBlock.emit(event);
    return event;
  };
  const random = Math.random;
  Math.random = () => 0;
  try {
    assert.equal(use(axe).cancel, false);
    assert.equal(env.blocks.get('6,61,6'), 'bct_t:r_oak_log', 'nothing changes inside the before-event');
    env.step(1);
  } finally { Math.random = random; }
  assert.equal(env.blocks.get('6,61,6'), 'bct_t:r_stripped_oak_log');
  assert.deepEqual(env.states.get('6,61,6'), { 'bct:pillar_axis': 'y', 'bct:y': 1 });
  assert.deepEqual(env.sounds.map(sound => sound.id), ['use.wood']);
  assert.equal(durability.damage, 1, 'the axe loses one point of durability');
  const stick = new env.ItemStack('minecraft:stick');
  env.player.equipment.set('Mainhand', stick);
  use(stick);
  env.step(1);
  assert.equal(env.blocks.get('6,61,6'), 'bct_t:r_stripped_oak_log', 'only axes strip');
});

test('a replacement block the game does not know is switched off instead of retried every tick', () => {
  const missing = withDefinitions(vanilla, definitionsFor(data, vanilla).filter(document => document['minecraft:block'].description.identifier !== 'bct_t:r_end_stone'));
  const env = setup({ extra: [['6,61,6', 'minecraft:end_stone']], registry: missing });
  settle(env);
  assert.equal(env.blocks.get('6,61,6'), 'minecraft:end_stone');
  assert.deepEqual(env.engine.replacements.status.broken, ['bct_t:r_end_stone']);
  assert.ok(!env.engine.replacements.blockTypes().includes('minecraft:end_stone'), 'its vanilla block is no longer scanned for');
  assert.equal(env.blocks.get('8,60,8'), 'bct_t:r_stone', 'the other replacements keep working');
});

test('a block placed where a player looks stays vanilla until no player sees it', () => {
  const env = setup({ config: { joinTicks: 0 } });
  settle(env);
  lookAt(env.player, at('8,61,11'));
  place(env, '8,61,11', 'minecraft:stone');
  settle(env, 8);
  assert.equal(env.blocks.get('8,61,11'), 'minecraft:stone', 'no flip while the player looks at it');
  assert.equal(env.engine.replacements.status.waiting, 1);
  env.player.view = NORTH; // turns round: the block is behind the player
  settle(env, 1);
  assert.equal(env.blocks.get('8,61,11'), 'bct_t:r_stone', 'swapped once nobody sees it');
  assert.deepEqual(env.states.get('8,61,11'), { 'bct:x': 0, 'bct:y': 1, 'bct:z': 1 });
  assert.equal(env.engine.replacements.status.waiting, 0);
});

test('by default a scan writes pattern-only replacements in bulk, the logs one by one', () => {
  const env = setup({ config: { farDistance: 0, nearDistance: 0 } });
  settle(env);
  const bulk = env.writes.filter(write => write.bulk), single = env.writes.filter(write => !write.bulk);
  assert.ok(bulk.length > 100, bulk.length + ' blocks written in bulk');
  assert.ok(bulk.every(write => write.type === 'bct_t:r_stone' || !write.type.includes('log')), 'no log in bulk');
  assert.equal(env.blocks.get('8,60,8'), 'bct_t:r_stone');
  assert.ok(single.length < bulk.length, single.length + ' single writes');
});

test('by default a block placed where a player looks is swapped at once', () => {
  const env = setup({ config: { farDistance: 0, nearDistance: 0 } });
  settle(env);
  lookAt(env.player, at('8,61,14'));
  place(env, '8,61,14', 'minecraft:stone');
  assert.equal(env.blocks.get('8,61,14'), 'bct_t:r_stone', 'swapped in the place event itself, before the next tick');
  assert.equal(env.engine.replacements.status.waiting, 0);
});

test('a block placed where no player looks, or beyond farDistance, is swapped right away', () => {
  const env = setup({ config: { joinTicks: 0, farDistance: 5 } });
  settle(env);
  env.player.view = NORTH;
  place(env, '8,61,11', 'minecraft:stone'); // behind the player
  settle(env, 2);
  assert.equal(env.blocks.get('8,61,11'), 'bct_t:r_stone');
  lookAt(env.player, at('8,61,14'));
  place(env, '8,61,14', 'minecraft:stone'); // in view, 6.6 blocks from the head
  settle(env, 2);
  assert.equal(env.blocks.get('8,61,14'), 'bct_t:r_stone', 'a change that far away is not noticed');
  assert.equal(env.engine.replacements.status.waiting, 0);
});

test('right after joining, blocks in view swap beyond nearDistance and nearer ones wait until the player turns', () => {
  const env = setup({ config: { nearDistance: 4, joinTicks: 40 }, view: DOWN });
  settle(env, 4);
  assert.equal(env.blocks.get('8,60,8'), 'minecraft:stone', 'in view and near: waits');
  assert.equal(env.blocks.get('8,60,13'), 'bct_t:r_stone', 'in view beyond the near distance: swapped while the area loads');
  assert.equal(env.blocks.get('0,60,0'), 'bct_t:r_stone', 'out of view');
  settle(env, 16);
  assert.equal(env.blocks.get('8,60,8'), 'minecraft:stone', 'no flip after the join window either');
  lookAt(env.player, at('8,61,14'));
  place(env, '8,61,14', 'minecraft:stone');
  settle(env, 4);
  assert.equal(env.blocks.get('8,61,14'), 'minecraft:stone', 'after the window, farDistance counts again');
  env.player.view = UP;
  settle(env, 1);
  assert.equal(env.blocks.get('8,60,8'), 'bct_t:r_stone');
  assert.equal(env.blocks.get('8,61,14'), 'bct_t:r_stone');
});

test('digging in view: hidden blocks behind the hole swap at once, a vanilla face the hole shows waits', () => {
  const env = setup({ extra: [['5,59,5', 'minecraft:stone'], ['5,58,5', 'minecraft:stone'], ['5,57,5', 'minecraft:stone']] });
  settle(env);
  // A vanilla block under the one about to be dug, changed without an event, so nothing swapped it yet.
  env.blocks.set('5,59,5', 'minecraft:stone'); env.states.delete('5,59,5');
  lookAt(env.player, at('5,60,5'));
  settle(env, 1); // the engine reads where players look once per tick
  breakBlock(env, '5,60,5');
  assert.equal(env.blocks.get('5,59,5'), 'minecraft:stone', 'the face the hole shows waits while the player looks');
  assert.equal(env.blocks.get('5,58,5'), 'bct_t:r_stone', 'the hidden block behind it is swapped in the same tick');
  settle(env, 4);
  assert.equal(env.blocks.get('5,59,5'), 'minecraft:stone');
  env.player.view = UP;
  settle(env, 1);
  assert.equal(env.blocks.get('5,59,5'), 'bct_t:r_stone');
});

test('swap-backs a player could see wait until nobody sees them', () => {
  const env = setup({ extra: [['8,59,8', 'minecraft:stone']], config: { joinTicks: 0 } });
  settle(env);
  assert.equal(env.blocks.get('8,59,8'), 'bct_t:r_stone');
  env.player.view = DOWN;
  env.system.sendScriptEvent('bct:control', 'off');
  settle(env, 12);
  assert.equal(env.blocks.get('8,60,8'), 'bct_t:r_stone', 'the block under the feet waits while the player looks at it');
  assert.equal(env.blocks.get('0,60,0'), 'minecraft:stone', 'a block out of view goes back');
  assert.equal(env.blocks.get('8,59,8'), 'minecraft:stone', 'a hidden block goes back even in view');
  assert.ok(env.engine.replacements.status.waiting > 0);
  env.player.view = UP;
  settle(env, 1);
  assert.equal(env.blocks.get('8,60,8'), 'minecraft:stone');
  assert.equal(env.engine.replacements.size, 0);
});

test('a torch, rail or carpet on a replaced block leaves it replaced, placed or broken', () => {
  const env = setup({ config: { joinTicks: 0 } });
  settle(env);
  env.player.view = DOWN; // looking at the blocks the torch, rail and carpet go on
  for (const [key, type] of [['7,61,8', 'minecraft:torch'], ['9,61,8', 'minecraft:rail'], ['8,61,9', 'minecraft:red_carpet']]) place(env, key, type);
  settle(env, 12);
  for (const key of ['7,60,8', '9,60,8', '8,60,9']) assert.equal(env.blocks.get(key), 'bct_t:r_stone', key + ' stays replaced');
  breakBlock(env, '7,61,8');
  settle(env, 12);
  assert.equal(env.blocks.get('7,60,8'), 'bct_t:r_stone', 'and stays replaced once the torch is gone');
  assert.equal(env.engine.replacements.status.reverts, 0, 'nothing went back to vanilla');
});

test('cocoa keeps the jungle log it hangs on vanilla, and a replaced log it appears next to goes back', () => {
  const env = setup({ extra: [['6,61,6', 'minecraft:jungle_log', { pillar_axis: 'y' }], ['7,61,6', 'minecraft:cocoa', { direction: 3, age: 0 }],
    ['10,61,10', 'minecraft:jungle_log', { pillar_axis: 'y' }]] });
  settle(env);
  assert.equal(env.blocks.get('6,61,6'), 'minecraft:jungle_log', 'the cocoa needs the vanilla jungle log');
  assert.equal(env.blocks.get('10,61,10'), 'bct_t:r_jungle_log', 'a jungle log without cocoa is replaced');
  // A structure puts cocoa next to the replaced log: the log goes back to vanilla (nobody looks at it).
  env.blocks.set('10,61,11', 'minecraft:cocoa'); env.states.set('10,61,11', { direction: 2, age: 0 });
  env.engine.replaceScanner.reset();
  settle(env, 4);
  assert.equal(env.blocks.get('10,61,10'), 'minecraft:jungle_log');
  assert.deepEqual(env.states.get('10,61,10'), { pillar_axis: 'y' });
  // Once the cocoa is picked, the log can be replaced.
  breakBlock(env, '7,61,6');
  assert.equal(env.blocks.get('6,61,6'), 'bct_t:r_jungle_log');
});

test('a teleport starts a new join window: the new area converts beyond nearDistance while nearer blocks wait', () => {
  const away = [];
  for (let x = 196; x < 212; x++) for (let z = 0; z < 16; z++) away.push([`${x},60,${z}`, 'minecraft:stone']);
  const env = setup({ extra: away, config: { nearDistance: 4, joinTicks: 40 } });
  settle(env, 16); // the window of the first arrival is over
  env.player.view = DOWN;
  env.player.location = { x: 204, y: 61, z: 8 };
  settle(env, 8);
  assert.equal(env.blocks.get('204,60,8'), 'minecraft:stone', 'in view and near: waits');
  assert.equal(env.blocks.get('204,60,13'), 'bct_t:r_stone', 'in view beyond the near distance, while the new area loads');
});

test('a swap-back that waited for the player to look away is dropped when the player comes back into range', () => {
  const env = setup({ config: { joinTicks: 0 } });
  settle(env);
  env.player.view = DOWN;
  env.player.location = { x: 8, y: 80, z: 8 }; // above the band: the floor would go back to vanilla, but the player looks at it
  settle(env, 12);
  assert.equal(env.blocks.get('8,60,8'), 'bct_t:r_stone');
  assert.ok(env.engine.replacements.status.waiting > 0);
  env.player.location = { x: 8, y: 61, z: 8 };
  settle(env, 2);
  env.player.view = UP;
  settle(env, 12);
  assert.equal(env.blocks.get('8,60,8'), 'bct_t:r_stone');
  assert.equal(env.engine.replacements.status.reverts, 0, 'nothing flipped back and forth');
  assert.equal(env.engine.replacements.status.waiting, 0);
});

test('a replacement broken and placed again as vanilla before a rescan is still swapped', () => {
  const env = setup({ config: { joinTicks: 0 } });
  settle(env);
  env.player.view = NORTH;
  breakBlock(env, '8,60,11');
  place(env, '8,60,11', 'minecraft:stone'); // the ownership record of the broken replacement is still there
  settle(env, 3);
  assert.equal(env.blocks.get('8,60,11'), 'bct_t:r_stone');
});

test('needs in the pack data are checked', () => {
  const env = setup({ playerAt: undefined });
  const bad = side => ({ ...data, needs: [{ blocks: ['minecraft:cocoa'], needs: ['minecraft:jungle_log'], side }] });
  assert.throws(() => env.engine.replacements.setSource('other', bad('diagonal')), /Invalid needs/);
  assert.throws(() => env.engine.replacements.setSource('other', { ...data, needs: [{ blocks: 'minecraft:cocoa', needs: [], side: 'side' }] }), /Invalid needs/);
  assert.doesNotThrow(() => env.engine.replacements.setSource('other', bad('below')));
});
