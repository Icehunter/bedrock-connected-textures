// Golden cases from the OptiFine ctm.properties format: tile numbers of the
// standard templates for each method.
import test from 'node:test';
import assert from 'node:assert/strict';
import { chooseTile, ruleResolver } from '../engine/tiles.mjs';

function world(points) {
  const filled = new Set(points.map(point => point.join(',')));
  const get = (x, y, z) => ({ location: { x, y, z }, typeId: filled.has([x, y, z].join(',')) ? 'minecraft:glass' : 'minecraft:air',
    offset: delta => get(x + delta.x, y + delta.y, z + delta.z) });
  return get;
}
// A flat 3x3 sheet on y=0; the face looked at is `up` at the origin.
const sheet = cells => world(cells.map(([x, z]) => [x, 0, z]))(0, 0, 0);
const up = (method, cells, rule = {}) => chooseTile({ method, ...rule }, sheet(cells), 'up');

test('ctm: isolated, full and edge-only neighborhoods use the 47-tile template', () => {
  assert.equal(up('ctm', [[0, 0]]).tile, 0);
  const all = [];
  for (let x = -1; x <= 1; x++) for (let z = -1; z <= 1; z++) all.push([x, z]);
  assert.equal(up('ctm', all).tile, 26, 'surrounded on all sides and corners');
  assert.equal(up('ctm', [[0, 0], [1, 0], [-1, 0], [0, 1], [0, -1]]).tile, 46, 'four edges, no corners');
});

test('ctm: rows and columns use the template ends and middles', () => {
  // On the up face, texture right is east (+x) and texture down is south (+z).
  assert.deepEqual([up('ctm', [[0, 0], [1, 0]]).tile, up('ctm', [[0, 0], [1, 0], [-1, 0]]).tile, up('ctm', [[0, 0], [-1, 0]]).tile], [1, 2, 3]);
  assert.deepEqual([up('ctm', [[0, 0], [0, 1]]).tile, up('ctm', [[0, 0], [0, 1], [0, -1]]).tile, up('ctm', [[0, 0], [0, -1]]).tile], [12, 24, 36]);
});

test('ctm: a corner only counts when both edges beside it connect', () => {
  assert.equal(up('ctm', [[0, 0], [1, 1]]).tile, 0);
  assert.notEqual(up('ctm', [[0, 0], [1, 0], [0, 1], [1, 1]]).tile, up('ctm', [[0, 0], [1, 0], [0, 1]]).tile);
});

test('horizontal and vertical: four tiles for left end, middle, right end and alone', () => {
  assert.deepEqual([[[0, 0], [1, 0]], [[0, 0], [1, 0], [-1, 0]], [[0, 0], [-1, 0]], [[0, 0]]].map(cells => up('horizontal', cells).tile), [0, 1, 2, 3]);
  assert.deepEqual([[[0, 0], [0, -1]], [[0, 0], [0, 1], [0, -1]], [[0, 0], [0, 1]], [[0, 0]]].map(cells => up('vertical', cells).tile), [0, 1, 2, 3]);
});

test('horizontal+vertical falls back to vertical tiles 4-6 only for a lone column', () => {
  assert.equal(up('horizontal+vertical', [[0, 0], [1, 0]]).tile, 0);
  assert.deepEqual([[[0, 0], [0, -1]], [[0, 0], [0, -1], [0, 1]], [[0, 0], [0, 1]]].map(cells => up('horizontal+vertical', cells).tile), [4, 5, 6]);
  assert.equal(up('horizontal+vertical', [[0, 0], [0, -1], [1, -1]]).tile, 3, 'the neighbor above is part of a row');
  assert.deepEqual([[[0, 0], [1, 0]], [[0, 0], [1, 0], [-1, 0]], [[0, 0], [-1, 0]]].map(cells => up('vertical+horizontal', cells).tile), [4, 5, 6]);
});

test('top: side faces show the tile only under the same block', () => {
  const get = world([[0, 0, 0], [0, 1, 0]]);
  assert.deepEqual(chooseTile({ method: 'top' }, get(0, 0, 0), 'north'), { known: true, visible: true, tile: 0, orientation: 0 });
  assert.equal(chooseTile({ method: 'top' }, get(0, 1, 0), 'north').visible, false);
  assert.equal(chooseTile({ method: 'top' }, get(0, 0, 0), 'down').visible, false);
});

test('fixed, repeat and weighted random', () => {
  assert.equal(up('fixed', [[0, 0]]).tile, 0);
  const tiles = [];
  for (const [x, z] of [[0, 0], [1, 0], [0, 1], [1, 1]]) tiles.push(chooseTile({ method: 'repeat', width: 2, height: 2 }, world([[x, 0, z]])(x, 0, z), 'up').tile);
  assert.deepEqual(tiles, [0, 1, 2, 3], 'repeat steps right along +x and down along +z on the top face');
  for (let x = 0; x < 50; x++) assert.equal(chooseTile({ method: 'random', tiles: ['a', 'b', 'c'], weights: [0, 5, 0] }, world([[x, 0, 0]])(x, 0, 0), 'up').tile, 1);
});

test('matchBlocks and faces limit where a rule applies', () => {
  const rules = [{ id: 'top_only', method: 'fixed', blocks: ['minecraft:glass'], faces: ['up'], tiles: ['glass_top'] }];
  const block = world([[0, 0, 0]])(0, 0, 0);
  assert.equal(ruleResolver(rules)(block, 'up').texture, 'glass_top');
  assert.equal(ruleResolver(rules)(block, 'north').visible, false);
  assert.equal(ruleResolver([{ ...rules[0], blocks: ['minecraft:stone'] }])(block, 'up').visible, false);
});

test('connect=tile joins blocks that show the same texture', () => {
  const get = (x, y, z) => ({ location: { x, y, z }, typeId: x === 0 && y === 0 && z === 0 ? 'minecraft:glass' : x === 1 && y === 0 && z === 0 ? 'minecraft:white_stained_glass' : 'minecraft:air',
    offset: delta => get(x + delta.x, y + delta.y, z + delta.z) });
  const texture = block => block.typeId === 'minecraft:air' ? 'air' : 'glass';
  assert.equal(chooseTile({ method: 'horizontal', connect: 'tile' }, get(0, 0, 0), 'up', texture).tile, 0);
  assert.equal(chooseTile({ method: 'horizontal', connect: 'block' }, get(0, 0, 0), 'up', texture).tile, 3);
});
