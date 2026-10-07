import test from 'node:test';
import assert from 'node:assert/strict';
import { fakeBedrock } from './fake_bedrock.mjs';
import { startEngine } from '../engine/engine.mjs';
import { publishSources } from '../engine/publisher.mjs';
import { checksum } from '../engine/sources.mjs';
import { DEFAULT_SETTINGS, resolveSettings, writeProperties, createCarrierBudget } from '../engine/settings.mjs';

const FACES = ['north', 'east', 'south', 'west', 'up', 'down'];
const stoneRules = { rules: [{ id: 'stone', method: 'fixed', blocks: ['minecraft:stone'], faces: FACES, tiles: ['stone'], entity: 'bct:stone', spawn_structure: 'bct:stone' }] };
const grassProvider = [{ format_version: 1, id: 'grass', effects: { edge: { kind: 'surface', entity: 'bct:grass_edge' } },
  rules: [{ id: 'edge', effect: 'edge', targets: ['minecraft:dirt'], neighbors: ['minecraft:grass_block'], contacts: ['north'], faces: ['up'] }] }];

function packet(engine, provider, data) {
  const text = JSON.stringify(data);
  return { engine, provider, digest: checksum(text), parts: text.match(/[\s\S]{1,750}/g) };
}

function setup({ blocks = [], sources = [['connected', 'pack', stoneRules]], playerAt = { x: 8, y: 70, z: 8 } } = {}) {
  const fake = fakeBedrock({ blocks: new Map(blocks) });
  const engine = startEngine(fake.api);
  const publisher = publishSources({ system: fake.system, world: fake.world, sources: sources.map(([part, id, data]) => packet(part, id, data)) });
  const player = playerAt ? fake.player(playerAt) : undefined;
  fake.step(3);
  return { ...fake, engine, publisher, player };
}

test('one engine registers its block components once and listens for both data kinds', () => {
  const fake = fakeBedrock();
  const registered = [];
  startEngine(fake.api);
  fake.system.beforeEvents.startup.emit({ blockComponentRegistry: { registerCustomComponent: name => registered.push(name) } });
  assert.deepEqual(registered, ['bct:update_repeat', 'bct:leaf']);
  assert.equal(fake.intervals.size, 3, 'one engine interval, the replacement tick and the source request timer');
  assert.equal(fake.system.afterEvents.scriptEventReceive.size, 2, 'one source listener and one command listener');
});

test('published pack data reaches the engine and the scanner draws found blocks', () => {
  const env = setup({ blocks: [['8,70,8', 'minecraft:stone']] });
  assert.equal(env.engine.connected.status.sources, 1);
  assert.equal(env.entities.filter(entity => entity.isValid).length, 6);
  assert.ok(env.entities.every(entity => entity.getDynamicProperty('bct:source') === 'pack'));
});

test('connected rules limited to biomes draw in those biomes only', () => {
  for (const [biome, carriers] of [['minecraft:plains', 6], ['minecraft:desert', 0]]) {
    const rules = { rules: [{ ...stoneRules.rules[0], biomes: { ids: ['minecraft:plains'], exclude: false } }] };
    const fake = fakeBedrock({ blocks: new Map([['8,70,8', 'minecraft:stone']]) });
    fake.dimension.getBiome = () => ({ id: biome });
    startEngine(fake.api);
    publishSources({ system: fake.system, world: fake.world, sources: [packet('connected', 'pack', rules)] });
    fake.player({ x: 8, y: 70, z: 8 });
    fake.step(3);
    assert.equal(fake.entities.filter(entity => entity.isValid).length, carriers, biome);
  }
});

test('chunk scans ask for the union of both block sets, one 16-block section of the band at a time', () => {
  const env = setup({ sources: [['connected', 'pack', stoneRules], ['terrain', 'pack', grassProvider]] });
  const radius = DEFAULT_SETTINGS.connected.chunkRadius, terrainRadius = DEFAULT_SETTINGS.terrain.chunkRadius;
  // Scans share a few milliseconds of each tick, so how many ticks they take depends on the machine.
  const scanned = () => new Set(env.volumes.map(volume => volume.from.x + ',' + volume.from.z)).size;
  for (let tick = 0; tick < 5000 && scanned() < (2 * terrainRadius + 1) ** 2; tick++) env.step(1);
  const playerChunk = env.volumes.filter(volume => volume.from.x === 0 && volume.from.z === 0);
  assert.deepEqual(new Set(playerChunk[0].types), new Set(['minecraft:stone', 'minecraft:grass_block']));
  assert.deepEqual([Math.min(...playerChunk.map(volume => volume.from.y)), Math.max(...playerChunk.map(volume => volume.to.y))], [70 - 24, 70 + 24]);
  assert.ok(playerChunk.every(volume => Math.floor(volume.from.y / 16) === Math.floor(volume.to.y / 16)), 'no query spans two sections');
  for (const volume of env.volumes) {
    const distance = Math.max(Math.abs(volume.from.x / 16), Math.abs(volume.from.z / 16));
    assert.ok(distance <= terrainRadius);
    assert.equal(volume.types.includes('minecraft:stone'), distance <= radius, 'connected types only inside their radius');
  }
  const chunks = new Set(env.volumes.map(volume => volume.from.x + ',' + volume.from.z));
  assert.equal(chunks.size, (2 * terrainRadius + 1) ** 2, 'every chunk in the terrain radius is scanned');
});

test('the top-surface pass finds terrain sources outside the vertical band', () => {
  const env = setup({ blocks: [['3,200,3', 'minecraft:grass_block'], ['3,200,4', 'minecraft:dirt']],
    sources: [['terrain', 'pack', grassProvider]] });
  assert.ok(env.entities.some(entity => entity.typeId === 'bct:grass_edge' && entity.isValid));
});

test('carriers stop at the per-chunk and total limits', () => {
  const blocks = [];
  for (let x = 0; x < 16; x++) for (const z of [0, 2]) blocks.push([x + ',70,' + z, 'minecraft:stone']);
  const env = setup({ blocks });
  assert.equal(env.engine.carriers.total, DEFAULT_SETTINGS.connected.maxCarriersPerChunk);
  env.system.sendScriptEvent('bct:config', JSON.stringify({ connected: { maxCarriers: 10, maxCarriersPerChunk: 64 } }));
  env.system.sendScriptEvent('bct:control', 'off');
  env.system.sendScriptEvent('bct:control', 'on');
  env.step(3);
  assert.equal(env.engine.carriers.total, 10);
  assert.equal(env.entities.filter(entity => entity.isValid).length, 10);
});

test('carriers outside the radius or band are removed and drawn again on return', () => {
  const env = setup({ blocks: [['8,70,8', 'minecraft:stone']] });
  assert.equal(env.engine.carriers.total, 6);
  env.player.location = { x: 8 + 16 * 10, y: 70, z: 8 };
  env.step(30);
  assert.equal(env.engine.carriers.total, 0);
  assert.equal(env.entities.filter(entity => entity.isValid).length, 0);
  env.player.location = { x: 8, y: 70, z: 8 };
  env.step(5);
  assert.equal(env.engine.carriers.total, 6);
  env.player.location = { x: 8, y: 170, z: 8 };
  env.step(30);
  assert.equal(env.engine.carriers.total, 0, 'the vertical band also limits carriers');
});

test('placing and breaking blocks refreshes neighbors and duplicates are removed on load', () => {
  const env = setup({ blocks: [['8,70,8', 'minecraft:stone']] });
  env.blocks.set('9,70,8', 'minecraft:stone');
  env.world.afterEvents.playerPlaceBlock.emit({ block: env.dimension.getBlock({ x: 9, y: 70, z: 8 }) });
  env.flush();
  assert.equal(env.engine.carriers.total, 10, 'the shared face between the two blocks is hidden');
  const copy = env.dimension.spawnEntity('bct:stone', { x: 8.5, y: 71.002, z: 8.5 });
  copy.addTag('bct_connected');
  copy.setDynamicProperty('bct:anchor', JSON.stringify({ location: { x: 8, y: 70, z: 8 }, face: 'up', layer: 0 }));
  env.world.afterEvents.entityLoad.emit({ entity: copy });
  env.flush();
  assert.equal(copy.isValid, false);
  env.blocks.delete('9,70,8');
  env.world.afterEvents.playerBreakBlock.emit({ block: env.dimension.getBlock({ x: 9, y: 70, z: 8 }) });
  env.flush();
  assert.equal(env.engine.carriers.total, 6);
});

test('property writes are skipped when the value has not changed', () => {
  const env = setup({ blocks: [['8,70,8', 'minecraft:stone']] });
  const counts = env.entities.map(entity => entity.writes.length);
  env.step(20);
  assert.deepEqual(env.entities.map(entity => entity.writes.length), counts);
  const entity = env.entities[0];
  writeProperties(entity, { 'bct:tile': 3 });
  writeProperties(entity, { 'bct:tile': 3 });
  assert.equal(entity.writes.filter(name => name === 'bct:tile').length, 2);
});

test('off and on clear and redraw; status replies to the player', () => {
  const env = setup({ blocks: [['8,70,8', 'minecraft:stone']] });
  env.system.afterEvents.scriptEventReceive.emit({ id: 'bct:control', message: 'off', sourceEntity: env.player });
  assert.equal(env.entities.filter(entity => entity.isValid).length, 0);
  assert.equal(env.properties.get('bct:enabled'), false);
  env.step(5);
  assert.equal(env.entities.filter(entity => entity.isValid).length, 0);
  env.system.afterEvents.scriptEventReceive.emit({ id: 'bct:control', message: 'on' });
  env.step(3);
  assert.equal(env.entities.filter(entity => entity.isValid).length, 6);
  env.system.afterEvents.scriptEventReceive.emit({ id: 'bct:control', message: 'status', sourceEntity: env.player });
  assert.match(env.player.messages.at(-1), /"carriers":6/);
});

test('ray tracing suspends carriers without touching other entities or the saved setting', () => {
  const env = setup({ blocks: [['8,70,8', 'minecraft:stone']] });
  const other = env.dimension.spawnEntity('bct:stone', { x: 0, y: 0, z: 0 });
  env.player.graphicsMode = 'RayTraced';
  env.step();
  assert.equal(env.engine.carriers.total, 0);
  assert.equal(other.isValid, true);
  assert.equal(env.properties.has('bct:enabled'), false);
  env.fakeSecond = env.player;
  env.player.graphicsMode = 'Deferred';
  env.step(3);
  assert.equal(env.engine.carriers.total, 6);
});

test('logs stay silent unless debug is on', () => {
  const messages = [], warn = console.warn;
  console.warn = message => messages.push(message);
  try {
    const env = setup({ blocks: [['8,70,8', 'minecraft:stone']] });
    env.step(60);
    assert.deepEqual(messages, []);
  } finally { console.warn = warn; }
});

test('settings reject unknown names and out-of-range values', () => {
  assert.equal(resolveSettings({ connected: { chunkRadius: 5 } }).connected.chunkRadius, 5);
  assert.equal(resolveSettings({}).terrain.maxNativeCarriers, 8000);
  assert.throws(() => resolveSettings({ connected: { chunkRadius: 99 } }), /chunkRadius/);
  assert.throws(() => resolveSettings({ speed: 1 }), /Unknown setting/);
  assert.throws(() => resolveSettings({ debug: 'yes' }), /debug/);
  const budget = createCarrierBudget(() => ({ maxCarriers: 2, maxCarriersPerChunk: 1 }));
  assert.equal(budget.claim('d', { x: 0, z: 0 }), true);
  assert.equal(budget.claim('d', { x: 1, z: 1 }), false, 'same chunk');
  assert.equal(budget.claim('d', { x: 16, z: 0 }), true);
  assert.equal(budget.claim('d', { x: 32, z: 0 }), false, 'total');
  budget.release('d', { x: 0, z: 0 });
  assert.equal(budget.claim('d', { x: 0, z: 0 }), true);
});

test('the scanner starts with the player chunk and works outward', () => {
  const env = setup({ playerAt: { x: 40, y: 70, z: -24 } });
  assert.deepEqual([env.volumes[0].from.x, env.volumes[0].from.z], [32, -32]);
  env.step(60);
  const distances = env.volumes.map(volume => Math.max(Math.abs(volume.from.x / 16 - 2), Math.abs(volume.from.z / 16 + 2)));
  assert.ok(distances.every((distance, index) => index === 0 || distance >= distances[index - 1] - 1), 'nearer chunks first');
  assert.ok(Math.max(...distances) <= DEFAULT_SETTINGS.connected.chunkRadius);
});
