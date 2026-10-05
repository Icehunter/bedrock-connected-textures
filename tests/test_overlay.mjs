import test from 'node:test';
import assert from 'node:assert/strict';
import { overlayTiles, overlaySelection, overlayCorners, OVERLAY_COMBOS, overlayComboIndex } from '../engine/overlay.mjs';
import { ruleResolver } from '../engine/tiles.mjs';
import { referenceOverlayTiles as reference } from './overlay_reference.mjs';

const FACES = ['up', 'down', 'north', 'south', 'west', 'east'];

/** A small world around the origin for the engine: blocks report their kind as typeId. */
function world(kinds) {
  const block = (x, y, z) => ({ typeId: kinds.get(`${x},${y},${z}`) ?? 'air', location: { x, y, z },
    offset: delta => block(x + delta.x, y + delta.y, z + delta.z) });
  return block(0, 0, 0);
}
const engineTiles = (host, face, dialect) => {
  const around = overlaySelection(host, face, { applies: other => other.typeId === 'src', same: other => other.typeId === 'host',
    solid: other => other.typeId === 'solid', dialect });
  assert.ok(around.known);
  return overlayTiles(around.edges, around.corners);
};

function random(seed) {
  let state = seed >>> 0;
  return () => { state = (Math.imul(state, 1664525) + 1013904223) >>> 0; return state / 2 ** 32; };
}

test('the overlay tiles match Continuity on every face for random neighborhoods', () => {
  const next = random(20261005), kinds = ['air', 'src', 'host', 'solid', 'other'];
  for (let trial = 0; trial < 6000; trial++) {
    const blocks = new Map();
    for (let x = -2; x <= 2; x++) for (let y = -2; y <= 2; y++) for (let z = -2; z <= 2; z++)
      if (x || y || z) blocks.set(`${x},${y},${z}`, kinds[Math.floor(next() * kinds.length)]);
    const host = world(blocks), type = vector => blocks.get(vector.join(',')) ?? 'air';
    for (const face of FACES) for (const dialect of ['continuity', 'optifine']) {
      assert.deepEqual(engineTiles(host, face, dialect), reference(type, face, dialect), `${face} ${dialect} trial ${trial}`);
    }
  }
});

test('an overlay lies on the side of the block that gives it, on top and side faces', () => {
  const tilesWith = (face, offset) => engineTiles(world(new Map([[offset.join(','), 'src']])), face, 'optifine');
  // Top face: north is the texture's top, east its right.
  assert.deepEqual(tilesWith('up', [1, 0, 0]), [7], 'east');
  assert.deepEqual(tilesWith('up', [-1, 0, 0]), [9], 'west');
  assert.deepEqual(tilesWith('up', [0, 0, -1]), [15], 'north');
  assert.deepEqual(tilesWith('up', [0, 0, 1]), [1], 'south');
  // A side face: the block above gives the strip along the top; seen from the north, east is on the left.
  assert.deepEqual(tilesWith('north', [0, 1, 0]), [15], 'above a north face');
  assert.deepEqual(tilesWith('north', [1, 0, 0]), [9], 'east of a north face');
  assert.deepEqual(tilesWith('south', [1, 0, 0]), [7], 'east of a south face');
  assert.deepEqual(tilesWith('east', [0, 0, 1]), [9], 'south of an east face');
  assert.deepEqual(tilesWith('down', [0, 0, 1]), [15], 'south of a bottom face');
});

test('a corner needs the same rule beside it; a covered neighbor gives nothing', () => {
  const corner = new Map([['1,0,-1', 'src']]);
  assert.deepEqual(engineTiles(world(corner), 'up', 'optifine'), [], 'a lone diagonal draws nothing');
  corner.set('1,0,0', 'host');
  assert.deepEqual(engineTiles(world(corner), 'up', 'optifine'), [14], 'with the same rule to the east, the right+up corner');
  const covered = new Map([['1,0,0', 'src'], ['1,1,0', 'solid']]);
  assert.deepEqual(engineTiles(world(covered), 'up', 'optifine'), [], 'a source under a solid block gives no overlay');
  const pair = new Map([['-1,0,0', 'src'], ['0,0,1', 'src'], ['1,0,-1', 'src']]);
  assert.deepEqual(engineTiles(world(pair), 'up', 'optifine'), [4, 14], 'OptiFine draws the corner opposite two edges');
  assert.deepEqual(engineTiles(world(pair), 'up', 'continuity'), [4], 'Continuity also wants the same rule beside it');
});

test('the 47 overlay cases cover every result exactly once', () => {
  assert.equal(OVERLAY_COMBOS.length, 47);
  assert.deepEqual(OVERLAY_COMBOS[0], [0, 0]);
  assert.equal(new Set(OVERLAY_COMBOS.map(([edges, corners]) => edges * 16 + corners)).size, 47);
  for (let edges = 0; edges < 16; edges++) for (let same = 0; same < 16; same++) for (let diagonals = 0; diagonals < 16; diagonals++)
    for (const dialect of ['optifine', 'continuity']) {
      const corners = overlayCorners(edges, same, corner => !!(diagonals & (1 << corner)), dialect);
      const index = overlayComboIndex(edges, corners);
      assert.ok(Number.isInteger(index), `${edges} ${corners}`);
      const tiles = overlayTiles(edges, corners);
      assert.ok(tiles.length <= 4 && new Set(tiles).size === tiles.length && tiles.every(tile => tile >= 0 && tile < 17));
    }
});

test('an unloaded neighbor defers the face', () => {
  const host = { location: { x: 0, y: 0, z: 0 }, offset: () => undefined };
  assert.deepEqual(overlaySelection(host, 'up', { applies: () => true }), { known: false });
});

test('overlay passes preserve the base texture and render every authored side layer', () => {
  const get = (x, y, z) => ({ typeId: y === 0 ? (x === 0 ? 'minecraft:stone' : 'minecraft:dirt') : 'minecraft:air', location: { x, y, z },
    offset: delta => get(x + delta.x, y + delta.y, z + delta.z) });
  const rules = [{ id: 'overlay', blocks: ['minecraft:stone'], method: 'overlay', faces: ['up'], tiles: Array.from({ length: 17 }, (_, n) => 'overlay/' + n), connect: 'block', connectBlocks: ['minecraft:dirt'] },
    { id: 'base', blocks: ['minecraft:stone'], method: 'fixed', faces: ['up'], tiles: ['base'] }];
  const selected = ruleResolver(rules, { fullCube: block => block.typeId !== 'minecraft:air', opaque: block => block.typeId !== 'minecraft:air' })(get(0, 0, 0), 'up');
  assert.equal(selected.texture, 'base'); assert.equal(selected.rule.id, 'base');
  // Dirt runs east and west along x: west is the left edge (9), east the right (7).
  assert.deepEqual(selected.overlays.map(layer => layer.tile), [9, 7]);
  assert.equal(ruleResolver(rules)(get(0, 0, 0), 'up').reason, 'overlay_geometry_provider');
});

test('overlay random keeps skip and tint; overlay tiles are never turned', () => {
  const block = { location: { x: 0, y: 0, z: 0 }, typeId: 'minecraft:stone', offset: () => ({ typeId: 'minecraft:air' }) };
  const rules = [{ id: 'overlay', blocks: ['minecraft:stone'], method: 'overlay_random', faces: ['up'], tiles: ['<skip>', 'tinted'], weights: [0, 1], tintIndex: 0, tintBlock: 'minecraft:grass_block' }];
  const resolve = ruleResolver(rules, { tintOf: () => [.3, .6, .2], orientationOf: () => 1 });
  const selected = resolve(block, 'up');
  assert.equal(selected.visible, false); assert.equal(selected.overlays.length, 1);
  assert.deepEqual(selected.overlays[0].tint, [.3, .6, .2]); assert.equal(selected.overlays[0].orientation, 0);
  assert.equal(ruleResolver(rules)(block, 'up').reason, 'overlay_tint_provider');
});
