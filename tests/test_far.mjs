import test from 'node:test';
import assert from 'node:assert/strict';
import { createFar } from '../engine/far.mjs';

/** A world with a ticking area manager whose areas load when `loadAll` is called, and one player. */
function setup({ maxChunkCount = 100, idle = () => true } = {}) {
  const properties = new Map(), areas = new Map(), pending = [], messages = [];
  const system = { currentTick: 0 };
  const dimension = { id: 'minecraft:overworld', heightRange: { min: -64, max: 320 }, getTopmostBlock: ({ x, z }) => ({ location: { x, y: 70, z } }) };
  const world = {
    getDynamicProperty: name => properties.get(name), setDynamicProperty: (name, value) => { if (value === undefined) properties.delete(name); else properties.set(name, value); },
    tickingAreaManager: {
      maxChunkCount,
      hasCapacity: ({ from, to }) => ((to.x - from.x + 1) / 16) * ((to.z - from.z + 1) / 16) <= maxChunkCount,
      createTickingArea(identifier, options) { areas.set(identifier, options); return new Promise(resolve => pending.push(resolve)); },
      removeTickingArea(identifier) { areas.delete(identifier); },
    },
  };
  const player = { id: 'p', dimension, location: { x: 8, y: 70, z: 8 }, onScreenDisplay: { setActionBar: text => messages.push(text) } };
  const far = createFar({ api: { world, system }, limits: () => ({ chunkRadius: 16, holdTicks: 400, status: 1 }), idle });
  const loadAll = async () => { while (pending.length) pending.shift()(); await Promise.resolve(); };
  const step = (ticks = 1) => { for (let t = 0; t < ticks; t++) { system.currentTick++; far.tick([player]); } };
  return { far, areas, properties, messages, player, system, step, loadAll };
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
  assert.ok(env.properties.get('bct:far:done:0').includes('|8|0|0'), 'done areas are saved in the world');
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
