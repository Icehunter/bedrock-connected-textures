import test from 'node:test';
import assert from 'node:assert/strict';
import { canonicalMask, MASKS, tileIndex, selectFace, selectBlock } from '../engine/core.mjs';

function world(entries, unknown = []) {
  const positions = new Map(entries.map(([location, type]) => [location.join(','), type]));
  const unloaded = new Set(unknown.map(location => location.join(',')));
  const get = location => ({
    typeId: positions.get(location.join(',')) ?? 'minecraft:air',
    offset({ x, y, z }) {
      const next = location.map((value, axis) => value + [x, y, z][axis]);
      if (unloaded.has(next.join(','))) throw new Error('Chunk unloaded');
      return get(next);
    },
  });
  return get([0, 0, 0]);
}

test('256 raw neighborhoods reduce to 47 valid corner-aware selections', () => {
  assert.equal(MASKS.length, 47);
  for (let raw = 0; raw < 256; raw++) {
    const mask = canonicalMask(raw);
    assert.equal(canonicalMask(mask), mask);
    assert.equal(MASKS[tileIndex(raw)], mask);
    for (let corner = 0; corner < 4; corner++) {
      const edges = (1 << corner) | (1 << ((corner + 1) % 4));
      if (mask & (1 << (corner + 4))) assert.equal(mask & edges, edges);
    }
  }
});

test('an isolated vanilla glass block retains every outer edge', () => {
  const result = selectBlock(world([[[0, 0, 0], 'minecraft:glass']]));
  for (const face of Object.values(result)) {
    assert.deepEqual(face, { known: true, visible: true, mask: 0, tile: tileIndex(0) });
  }
});

test('two vanilla glass blocks join without changing either block', () => {
  const block = world([[[0, 0, 0], 'minecraft:glass'], [[1, 0, 0], 'minecraft:glass']]);
  assert.equal(selectFace(block, 'north').mask, 8);
  assert.equal(selectFace(block, 'south').mask, 2);
  assert.deepEqual(selectFace(block, 'east'), { known: true, visible: false });
  assert.equal(block.typeId, 'minecraft:glass');
});

test('a filled 2x2 corner differs from an L-shaped opening', () => {
  const entries = [[[0, 0, 0], 'minecraft:glass'], [[0, 1, 0], 'minecraft:glass'], [[-1, 0, 0], 'minecraft:glass']];
  assert.equal(selectFace(world(entries), 'north').mask, 3);
  entries.push([[-1, 1, 0], 'minecraft:glass']);
  assert.equal(selectFace(world(entries), 'north').mask, 19);
});

test('unloaded relevant neighbors defer selection rather than inventing a seam', () => {
  const entries = [[[0, 0, 0], 'minecraft:glass'], [[0, 1, 0], 'minecraft:glass'], [[-1, 0, 0], 'minecraft:glass']];
  assert.deepEqual(selectFace(world(entries, [[-1, 1, 0]]), 'north'), { known: false });
  assert.equal(selectFace(world(entries), 'north').known, true);
});

test('different vanilla materials do not join unless a rule explicitly groups them', () => {
  const block = world([[[0, 0, 0], 'minecraft:sandstone'], [[1, 0, 0], 'minecraft:red_sandstone']]);
  assert.equal(selectFace(block, 'north').mask, 0);
  const group = new Set(['minecraft:sandstone', 'minecraft:red_sandstone']);
  assert.equal(selectFace(block, 'north', (first, second) => group.has(first.typeId) && group.has(second?.typeId)).mask, 8);
});
