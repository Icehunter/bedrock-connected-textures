import { fakeBedrock, GAME_COSTS } from '../fake_bedrock.mjs';
import { startEngine } from '../../engine/engine.mjs';
import { publishSources } from '../../engine/publisher.mjs';
import { checksum } from '../../engine/sources.mjs';

const FACES = ['north', 'east', 'south', 'west', 'up', 'down'];
const stoneRules = { rules: [{ id: 'stone', method: 'fixed', blocks: ['minecraft:stone'], faces: FACES, tiles: ['stone'], entity: 'bct:stone', spawn_structure: 'bct:stone' }] };

function packet(engine, provider, data) {
  const text = JSON.stringify(data);
  return { engine, provider, digest: checksum(text), parts: text.match(/[\s\S]{1,750}/g) };
}

const wall = () => (globalThis.performance ? globalThis.performance.now() : Date.now());

const blocks = [];
for (let x = 4; x <= 13; x++) for (let y = 60; y <= 69; y++) for (let z = 4; z <= 13; z++) blocks.push([x + ',' + y + ',' + z, 'minecraft:dirt']);
blocks.push(['3,65,8', 'minecraft:stone'], ['14,65,8', 'minecraft:stone'], ['8,59,8', 'minecraft:stone'], ['8,70,8', 'minecraft:stone'], ['8,65,3', 'minecraft:stone'], ['8,65,14', 'minecraft:stone']);

const fake = fakeBedrock({ blocks: new Map(blocks), costs: GAME_COSTS });
const engine = startEngine(fake.api);
publishSources({ system: fake.system, world: fake.world, sources: [packet('connected', 'pack', stoneRules)] });
fake.player({ x: 8, y: 65, z: 8 });

let stable = 0, previous = -1;
for (let step = 0; step < 400 && stable < 10; step++) {
  fake.step(1);
  const count = fake.entities.filter(entity => entity.isValid).length;
  if (count === previous) stable++; else stable = 0;
  previous = count;
}
const carriersBefore = fake.entities.filter(entity => entity.isValid).length;

const original = engine.connected.update;
const updated = new Set();
let updateCalls = 0;
engine.connected.update = (dimension, location) => {
  updated.add(location.x + ',' + location.y + ',' + location.z);
  updateCalls++;
  return original(dimension, location);
};

for (let x = 5; x <= 12; x++) for (let y = 61; y <= 68; y++) for (let z = 5; z <= 12; z++) fake.blocks.delete(x + ',' + y + ',' + z);
for (let x = 5; x <= 12; x++) for (let y = 61; y <= 68; y++) for (let z = 5; z <= 12; z++) fake.world.afterEvents.blockExplode.emit({ block: fake.dimension.getBlock({ x, y, z }), explodedBlockPermutation: { type: { id: 'minecraft:dirt' } } });

const expected = new Set();
for (let x = 5; x <= 12; x++) for (let y = 61; y <= 68; y++) for (let z = 5; z <= 12; z++) for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (let dz = -1; dz <= 1; dz++) expected.add((x + dx) + ',' + (y + dy) + ',' + (z + dz));

fake.stepTimes.length = 0;
let drainSteps = 0, maxWallStepMs = 0;
for (; drainSteps < 300 && updated.size < expected.size; drainSteps++) {
  const start = wall();
  fake.step(1);
  const elapsed = wall() - start;
  if (elapsed > maxWallStepMs) maxWallStepMs = elapsed;
}

const maxModelledStepMs = Math.max(...fake.stepTimes);
const carriersAfter = fake.entities.filter(entity => entity.isValid).length;
const budgetMs = Math.max(engine.settings().budgetMs, engine.settings().nearBudgetMs);
const missingCells = [...expected].filter(key => !updated.has(key)).length;

const result = { maxModelledStepMs: Math.round(maxModelledStepMs * 100) / 100, maxWallStepMs: Math.round(maxWallStepMs * 100) / 100, drainSteps, updateCalls, expectedCells: expected.size, missingCells, carriersBefore, carriersAfter, budgetMs };
console.log('RESULT ' + JSON.stringify(result));

const failures = [];
if (carriersBefore !== 36) failures.push('carriersBefore ' + carriersBefore + ' != 36');
if (missingCells !== 0) failures.push('missingCells ' + missingCells + ' != 0');
if (carriersAfter !== carriersBefore) failures.push('carriersAfter ' + carriersAfter + ' != carriersBefore ' + carriersBefore);
if (!(maxModelledStepMs <= budgetMs + 1)) failures.push('maxModelledStepMs ' + Math.round(maxModelledStepMs * 100) / 100 + ' > budgetMs ' + budgetMs + ' + 1');
if (!(maxWallStepMs < 50)) failures.push('maxWallStepMs ' + Math.round(maxWallStepMs * 100) / 100 + ' >= 50');
if (!(updateCalls <= expected.size + 36)) failures.push('updateCalls ' + updateCalls + ' > expectedCells ' + expected.size + ' + 36');
if (failures.length) throw new Error(failures.join('; '));
