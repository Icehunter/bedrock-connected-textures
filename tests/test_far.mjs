import test from 'node:test';
import assert from 'node:assert/strict';
import { createFar } from '../engine/far.mjs';

/** A world with a ticking area manager whose areas load when `loadAll` is called, and one player. */
function setup({ maxChunkCount = 100, idle = () => true, settle = 0, key = 'aaaa1111' } = {}) {
  const properties = new Map(), areas = new Map(), pending = [], messages = [];
  const system = { currentTick: 0 };
  const dimension = { id: 'minecraft:overworld', heightRange: { min: -64, max: 320 }, getTopmostBlock: ({ x, z }) => ({ location: { x, y: 70, z } }) };
  const world = {
    getDimension: () => dimension,
    getDynamicProperty: name => properties.get(name), setDynamicProperty: (name, value) => { if (value === undefined) properties.delete(name); else properties.set(name, value); },
    tickingAreaManager: {
      maxChunkCount,
      hasCapacity: ({ from, to }) => ((to.x - from.x + 1) / 16) * ((to.z - from.z + 1) / 16) <= maxChunkCount,
      createTickingArea(identifier, options) { areas.set(identifier, options); return new Promise(resolve => pending.push(resolve)); },
      removeTickingArea(identifier) { areas.delete(identifier); },
    },
    getDynamicPropertyIds: () => [...properties.keys()],
  };
  const player = { id: 'p', dimension, location: { x: 8, y: 70, z: 8 }, onScreenDisplay: { setActionBar: text => messages.push(text) } };
  const far = createFar({ api: { world, system }, limits: () => ({ chunkRadius: 16, holdTicks: 400, status: 1 }), idle, packKey: () => key, settle });
  const loadAll = async () => { while (pending.length) pending.shift()(); await Promise.resolve(); };
  const step = (ticks = 1) => { for (let t = 0; t < ticks; t++) { system.currentTick++; far.tick([player]); } };
  return { far, areas, properties, messages, player, system, step, loadAll, setKey: value => { key = value; } };
}

test('loads the nearest area, converts it through a virtual player, then moves on and remembers it', async () => {
  const env = setup();
  env.step();
  assert.equal(env.areas.size, 1, 'one area at a time');
  const [options] = env.areas.values();
  assert.deepEqual([options.from.x, options.to.x], [-16, 143], 'the player\'s own 8 x 8 chunk area with a border chunk');
  assert.deepEqual(env.far.virtualPlayers(), [], 'no virtual player before the chunks are loaded');
  await env.loadAll();
  const [virtual] = env.far.virtualPlayers();
  assert.ok(virtual.virtual && virtual.location.y === 70, 'a virtual player on the ground in the middle of the area');
  env.step(39);
  assert.equal(env.areas.size, 1, 'kept while the scans reach it');
  env.step(2);
  assert.equal(env.far.status.done, 1);
  assert.equal(env.areas.size, 1, 'the next area is loading');
  assert.ok(env.properties.get('bct:far:done:aaaa1111:0').includes('|8|0|0'), 'done areas are saved in the world');
  env.step(20);
  const last = env.messages.at(-1);
  assert.match(last, /: 64 \/ 1,600 chunks \(4%\)/, 'players see the chunks converted so far, counting up: ' + last);
});

test('an area waits for the engine to finish its work, up to holdTicks', async () => {
  let busy = true;
  const env = setup({ idle: () => !busy });
  env.step();
  await env.loadAll();
  env.step(100);
  assert.equal(env.far.status.done, 0, 'still converting');
  busy = false;
  env.step(1);
  assert.equal(env.far.status.done, 1);
});

test('a small ticking allowance gets smaller areas; none at all turns the worker off', async () => {
  const small = setup({ maxChunkCount: 40 });
  small.step();
  const [options] = small.areas.values();
  assert.equal((options.to.x - options.from.x + 1) / 16, 6, '4 x 4 chunks and their border');
  const none = setup({ maxChunkCount: 4 });
  none.step(5);
  assert.equal(none.areas.size, 0);
});

test('switched off, the loaded area is released', async () => {
  const env = setup();
  env.step();
  await env.loadAll();
  env.step(41);
  env.far.setEnabled(false);
  assert.equal(env.areas.size, 0);
  assert.deepEqual(env.far.virtualPlayers(), []);
});

test('the progress line shows while the first area is still loading and keeps being refreshed', () => {
  const env = setup();
  env.step(60);
  assert.ok(env.messages.length >= 3, env.messages.length + ' refreshes in three seconds');
  assert.match(env.messages[0], /: 0 \/ [\d,]+ chunks \(0%\)/);
});

test('an area the game never finishes loading is skipped after a while', () => {
  const env = setup();
  env.step(1);
  const [first] = env.areas.keys();
  env.step(600);
  assert.ok(!env.areas.has(first), 'released');
  assert.equal(env.areas.size, 1, 'the next area is loading');
  assert.equal(env.far.status.failures, 1);
});

test('beyond the simulation distance blocks convert too, unless far.blocks is off', async () => {
  const { blockConverters } = await import('../engine/engine.mjs');
  const { resolveSettings } = await import('../engine/settings.mjs');
  const real = [{ id: 'p' }], all = [...real, { id: 'bct:far', virtual: true }];
  assert.deepEqual(blockConverters(all, real, resolveSettings()), all, 'by default blocks convert far away too');
  assert.deepEqual(blockConverters(all, real, resolveSettings({ far: { blocks: 0 } })), real);
});

test('areas in front of the player go before the ones behind', async () => {
  const env = setup();
  env.player.getViewDirection = () => ({ x: 0, y: 0, z: -1 });   // looking north
  env.step();
  await env.loadAll();
  env.step(41);   // the player's own area is done; the next one loads
  const options = [...env.areas.values()].at(-1);
  assert.ok(options.from.z + options.to.z < 0, 'the second area is north of the player, where they look: ' + JSON.stringify(options));
});

test('restoring, it loads the areas holding changed chunks, wherever they are, until none are left', async () => {
  const env = setup();
  const left = [{ dimension: 'minecraft:overworld', cx: 200, cz: 3 }];   // far beyond chunkRadius
  env.far.setTargets(() => left);
  env.step();
  const [options] = env.areas.values();
  assert.ok(options.from.x <= 200 * 16 && options.to.x >= 200 * 16, 'the area holding the changed chunk loads: ' + JSON.stringify(options));
  await env.loadAll();
  env.step(41);
  assert.equal(env.areas.size, 1, 'held while the chunk still holds changes');
  left.length = 0;
  env.step(1);
  assert.equal(env.areas.size, 0, 'released once the chunk is back to vanilla, and nothing else to load');
  assert.match(env.messages.at(-1) ?? '', /Restoring the world/);
});

test('a different pack is converted again and the first pack\'s list is deleted', async () => {
  const env = setup();
  env.step();
  await env.loadAll();
  env.step(41);
  assert.equal(env.far.status.done, 1);
  env.setKey('bbbb2222');
  env.step();
  assert.equal(env.far.status.done, 0);
  assert.equal(env.areas.size, 1);
  const [options] = env.areas.values();
  assert.equal(options.from.x, -16, 'exactly one ticking area is loading, the player\'s own area again');
  assert.equal([...env.properties.keys()].filter(name => name.startsWith('bct:far:done:aaaa1111')).length, 0, 'the old pack\'s list is deleted');
  env.setKey('aaaa1111');
  env.step();
  assert.equal(env.far.status.done, 0, 'switching back starts over');
});

test('a list kept from an earlier session for another pack is deleted when this pack starts', async () => {
  const env = setup();
  env.properties.set('bct:far:done:cccc3333:0', '0|8|0|0;');
  env.properties.set('bct:far:done:cccc3333:1', 'x;');
  env.step(1);
  assert.equal([...env.properties.keys()].some(name => name.startsWith('bct:far:done:cccc3333')), false, 'the other pack\'s list is deleted');
});

test('rescan forgets what is done, so the far pass starts over', async () => {
  const env = setup();
  env.step();
  await env.loadAll();
  env.step(41);
  assert.equal(env.far.status.done, 1);
  assert.ok(env.properties.has('bct:far:done:aaaa1111:0'));
  env.far.rescan();
  assert.equal(env.far.status.done, 0);
  assert.equal([...env.properties.keys()].some(name => name.startsWith('bct:far:done:aaaa1111')), false, 'the pack\'s saved list is deleted');
  assert.equal(env.areas.size, 0, 'the loaded area is released');
  env.step();
  assert.equal(env.areas.size, 1);
  const [options] = env.areas.values();
  assert.equal(options.from.x, -16, 'the first area loads again');
});

test('rescan before the pack key has settled still clears the saved list', async () => {
  const env = setup({ settle: 200 });
  env.properties.set('bct:far:done:aaaa1111:0', '0|8|0|0;');
  env.far.rescan();
  assert.equal(env.properties.has('bct:far:done:aaaa1111:0'), false);
});

test('nothing loads until a pack is loaded', async () => {
  const env = setup();
  env.setKey(undefined);
  env.step(5);
  assert.equal(env.areas.size, 0);
  env.setKey('aaaa1111');
  env.step(1);
  assert.equal(env.areas.size, 1);
});

test('the pack key must hold still before the far pass starts', async () => {
  const env = setup({ settle: 200 });
  env.step(199);
  assert.equal(env.areas.size, 0);
  env.step(2);
  assert.equal(env.areas.size, 1);
});

test('settled tells whether the pack key has held still long enough', () => {
  const env = setup({ settle: 200 });
  env.step(199);
  assert.equal(env.far.settled, false);
  env.step(2);
  assert.equal(env.far.settled, true);
  env.setKey('bbbb2222');
  env.step(1);
  assert.equal(env.far.settled, false, 'a new key starts the wait again');
  env.setKey(undefined);
  env.step(1);
  assert.equal(env.far.settled, false, 'no pack, nothing settled');
});

test('settled keeps being tracked while it loads chunks it was given', () => {
  const env = setup({ settle: 10 });
  env.far.setTargets(() => [{ dimension: 'minecraft:overworld', cx: 3, cz: 3 }]);
  env.step(11);
  assert.equal(env.far.settled, true);
});

test('chunks it was given show their own label on the action bar', () => {
  const env = setup();
  env.far.setTargets(() => [{ dimension: 'minecraft:overworld', cx: 3, cz: 3 }], 'Updating the world to the new pack');
  env.step(25);
  assert.match(env.messages.at(-1), /Updating the world to the new pack: 1 chunks to go/);
});

test('the done list of an earlier version, kept for no pack in particular, is dropped', async () => {
  const env = setup();
  env.properties.set('bct:far:done:0', '0|8|0|0;');
  env.step(1);
  assert.equal(env.properties.has('bct:far:done:0'), false, 'the old unkeyed list is gone');
  assert.equal(env.far.status.done, 0);
});
