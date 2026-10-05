import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { evaluate, validateProvider, descriptorKey } from '../engine/terrain-rules.mjs';
import { grassTint } from '../engine/tint.mjs';
import { NativeSurfaces } from '../engine/terrain-native.mjs';
import { createHeightSettings, DEFAULT_HEIGHTS } from '../engine/heights.mjs';

const provider = JSON.parse(fs.readFileSync(new URL('./fixtures/terrain-provider.json', import.meta.url)));
const releaseProvider = JSON.parse(fs.readFileSync(new URL('./fixtures/release-provider.json', import.meta.url)));
const ground = structuredClone(releaseProvider);
// Covers providers that draw entity carriers instead of native blocks.
for(const effect of Object.values(ground.effects)) { delete effect.native_block; delete effect.native_material; delete effect.native_offset; delete effect.entity_surface; delete effect.height_profiles; delete effect.height_tuning; }

function fixture(entries, unknown = []) {
  const blocks = new Map(entries.map(([location, type]) => [location.join(','), type]));
  const unloaded = new Set(unknown.map(location => location.join(',')));
  const tints = new Map(), states = new Map(), writes = [], biomes = new Map();
  const dimension = {getBiome: location => ({id:biomes.get([location.x,location.y,location.z].join(',')) ?? 'minecraft:plains'})};
  const get = location => ({
    dimension,
    typeId: blocks.get(location.join(',')) ?? 'minecraft:air',
    get isAir() { return this.typeId === 'minecraft:air'; },
    get permutation() {
      const type=blocks.get(location.join(',')), snapshot={...states.get(location.join(','))};
      return {type,states:snapshot,
        getState: name => snapshot[name],
        getAllStates: () => ({...snapshot}),
        withState: (name,value) => ({type,states:{...snapshot,[name]:value}}),
      };
    },
    setType(type) { blocks.set(location.join(','),type);states.delete(location.join(',')); },
    setPermutation(value) { writes.push(value);blocks.set(location.join(','),value.type);states.set(location.join(','),value.states); },
    location: { x: location[0], y: location[1], z: location[2] },
    getComponent(name) {
      assert.equal(name, 'minecraft:map_color');
      const tint = tints.get(location.join(',')) ?? [.6,.78,.55];
      const color = {red: .4, green: .8, blue: .2, alpha: 1};
      return {color, tintedColor: {red: .4*tint[0], green: .8*tint[1], blue: .2*tint[2], alpha: 1}};
    },
    offset({ x, y, z }) {
      const next = location.map((value, axis) => value + [x, y, z][axis]);
      if (unloaded.has(next.join(','))) throw new Error('Chunk unavailable');
      return get(next);
    },
  });
  return { get, blocks, tints, states, writes, biomes, dimension };
}

test('provider rejects invalid directions, missing effects and duplicate rules', () => {
  assert.equal(validateProvider(provider), provider);
  for (const mutate of [p => p.rules[0].contacts.push('diagonal'), p => p.rules[0].effect = 'missing',
    p => p.rules.push(p.rules[0]), p => p.rules[1].neighbor_y = 100]) {
    const copy = structuredClone(provider); mutate(copy);
    assert.throws(() => validateProvider(copy));
  }
});

test('entity surface carrier flags must be boolean and belong to surfaces', () => {
  validateProvider(releaseProvider);
  for (const value of ['true',1,null]) {
    const copy=structuredClone(releaseProvider);
    copy.effects.soul_sand.entity_surface=value;
    assert.throws(()=>validateProvider(copy),/Invalid entity surface flag/);
  }
  const copy=structuredClone(releaseProvider);
  copy.effects.soul_sand.kind='model';
  assert.throws(()=>validateProvider(copy),/Invalid entity surface flag/);
});

test('ground contact adds moss to exposed log sides and hides buried faces', () => {
  const grid = fixture([[[0, 0, 0], 'minecraft:oak_log'], [[0, -1, 0], 'minecraft:grass_block'],
    [[1, 0, 0], 'minecraft:stone']]);
  const outputs = evaluate(provider, grid.get([0, 0, 0]));
  assert.deepEqual(outputs.map(output => output.face), ['north', 'south', 'west']);
  assert.ok(outputs.every(output => output.entity === 'bct:moss'));
  grid.blocks.delete('0,-1,0');
  assert.deepEqual(evaluate(provider, grid.get([0, 0, 0])), []);
});

test('roots use a log one level above neighboring grass, as at a natural tree base', () => {
  const grid = fixture([[[0, 0, 0], 'minecraft:grass_block'], [[0, 1, -1], 'minecraft:oak_log']]);
  const outputs = evaluate(provider, grid.get([0, 0, 0]));
  assert.equal(outputs.length, 1);
  assert.equal(outputs[0].face, 'north');
  assert.equal(outputs[0].kind, 'model');
  grid.blocks.set('0,1,0', 'minecraft:stone');
  assert.deepEqual(evaluate(provider, grid.get([0, 0, 0])), []);
});

test('unknown contacts retain visuals until the neighborhood can be read', () => {
  const grid = fixture([[[0, 0, 0], 'minecraft:oak_log']], [[0, -1, 0]]);
  assert.equal(evaluate(provider, grid.get([0, 0, 0])), null);
});

test('ground overlays require edge contact and retain supported rounded corners', () => {
  validateProvider(ground);
  const grid = fixture([[[0,0,0], 'minecraft:oak_planks'], [[0,0,-1], 'minecraft:sand'], [[1,0,0], 'minecraft:oak_planks']]);
  assert.equal(evaluate(ground, grid.get([0,0,0]))[0].tile, 1);
  grid.blocks.delete('0,0,-1');
  grid.blocks.set('1,0,-1', 'minecraft:sand');
  assert.deepEqual(evaluate(ground, grid.get([0,0,0])), []);
  grid.blocks.set('0,0,-1', 'minecraft:sand');
  const output = evaluate(ground, grid.get([0,0,0]))[0];
  assert.equal(output.tile, 1);
  assert.equal(output.face, 'up');
  assert.equal(output.entity, 'bct:sand');
  grid.blocks.set('0,1,0', 'minecraft:stone');
  assert.deepEqual(evaluate(ground, grid.get([0,0,0])), []);
});

test('client grass tint takes precedence over the map tint', () => {
  const block = {location: {x:0,y:0,z:0}, dimension: {getBiome: () => ({id: 'minecraft:taiga'})},
    getComponent() { throw new Error('Map tint must not be read for a resolved client biome'); }};
  const colors = {'minecraft:taiga': [.4,.7,.5]};
  assert.deepEqual(grassTint(block, colors), [.4,.7,.5]);
  block.dimension.getBiome = () => ({id: 'custom:unregistered'});
  assert.equal(grassTint(block, colors), null);
});

test('diagonal grass rounds a supported path corner without extending over an air gap', () => {
  const grid = fixture([[[0,0,0], 'minecraft:sand'], [[0,0,-1], 'minecraft:sand'], [[1,0,0], 'minecraft:sand'], [[1,0,-1], 'minecraft:grass_block']]);
  assert.equal(evaluate(ground, grid.get([0,0,0]))[0].tile, 16);
  grid.blocks.delete('1,0,0');
  assert.deepEqual(evaluate(ground, grid.get([0,0,0])), []);
});

test('ground overlays retain independent materials and unknown corners defer updates', () => {
  const grid = fixture([[[0,0,0], 'minecraft:cobblestone'], [[0,0,-1], 'minecraft:sand'], [[1,0,0], 'minecraft:grass_block']]);
  const outputs = evaluate(ground, grid.get([0,0,0]));
  assert.equal(outputs.length, 2);
  assert.equal(outputs[1].entity, 'bct:sand');
  assert.equal(outputs[0].entity, 'bct:grass');
  const unknown = fixture([[[0,0,0], 'minecraft:oak_planks']], [[1,0,-1]]);
  assert.equal(evaluate(ground, unknown.get([0,0,0])), null);
  assert.deepEqual(evaluate(ground, fixture([[[0,0,0], 'minecraft:oak_log'], [[0,-1,0], 'minecraft:grass_block']]).get([0,0,0])), []);
});

test('grass tint follows engine values at source neighbors and updates across biome boundaries', () => {
  const grid = fixture([[[0,0,0], 'minecraft:cobblestone'], [[0,0,-1], 'minecraft:grass_block'], [[1,0,0], 'minecraft:grass_block']]);
  grid.tints.set('0,0,-1', [.2,.6,.3]);
  grid.tints.set('1,0,0', [.8,.4,.1]);
  const close = (actual, expected) => actual.forEach((value, index) => assert.ok(Math.abs(value - expected[index]) < 1e-10));
  close(evaluate(ground, grid.get([0,0,0]))[0].tint, [.5,.5,.2]);
  grid.tints.set('1,0,0', [.2,.6,.3]);
  close(evaluate(ground, grid.get([0,0,0]))[0].tint, [.2,.6,.3]);
  grid.blocks.set('-1,0,1', 'minecraft:grass_block');
  grid.tints.set('-1,0,1', [1,0,0]);
  close(evaluate(ground, grid.get([0,0,0]))[0].tint, [.2,.6,.3]);
  assert.equal(grassTint({getComponent: () => undefined}), null);
  assert.equal(grassTint({getComponent: () => ({color: {red: 0, green: 1, blue: 1}, tintedColor: {red: 0, green: 1, blue: 1}})}), null);
});

