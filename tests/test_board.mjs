// The test board (converter/ctm_board.py): every board face shows the tile the
// OptiFine format gives it, terrain edges reach cobblestone hosts, and both
// still hold when hosts, sources and neighbors are replacement blocks.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fakeBedrock } from './fake_bedrock.mjs';
import { createConnectedSource } from '../engine/connected.mjs';
import { createTerrain } from '../engine/terrain.mjs';
import { createCarrierBudget } from '../engine/settings.mjs';
import { referenceOverlayTiles } from './overlay_reference.mjs';

const fixture = name => JSON.parse(readFileSync(new URL('./fixtures/board/' + name, import.meta.url), 'utf8'));
const layout = fixture('layout.json'), rules = fixture('rules.json'), expected = fixture('expected.json');
const template = JSON.parse(readFileSync(new URL('../converter/data/terrain-provider.json', import.meta.url), 'utf8'));

// The board's overlay cells are checked against the Java reference (tests/overlay_reference.mjs):
// expected.json places each overlay piece on the side toward the gravel, as the engine does.
const placed = new Map(layout.map(cell => [`${cell.x},${cell.y},${cell.z}`, cell.block]));
const sectionOf = new Map(layout.map(cell => [`${cell.x},${cell.y},${cell.z},${cell.face}`, cell.section]));
function wanted(key, tile) {
  if (sectionOf.get(key) === 'overlay') {
    const [x, y, z, face] = key.split(',');
    const kind = ([dx, dy, dz]) => {
      const block = placed.get(`${+x + dx},${+y + dy},${+z + dz}`);
      // Gravel gives the overlay, mossy cobblestone carries the same rule; every other board block is opaque.
      return block === 'minecraft:gravel' ? 'src' : block === 'minecraft:mossy_cobblestone' ? 'host' : block ? 'solid' : 'air';
    };
    return referenceOverlayTiles(kind, face, 'optifine').sort((a, b) => a - b);
  }
  return tile === null ? [] : Array.isArray(tile) ? [...tile].sort((a, b) => a - b) : [tile];
}

/** Replacement types the board swaps in for some vanilla blocks, like a converted pack's replacement blocks. */
const REPLACED = { 'bct_board:r_oak_planks': 'minecraft:oak_planks', 'bct_board:r_white_wool': 'minecraft:white_wool',
  'bct_board:r_cobblestone': 'minecraft:cobblestone', 'bct_board:r_sand': 'minecraft:sand',
  'bct_board:r_grass_block': 'minecraft:grass_block', 'bct_board:r_red_sand': 'minecraft:red_sand' };
const vanillaType = type => REPLACED[type] ?? type;
const aliasesOf = type => Object.entries(REPLACED).filter(([, vanilla]) => vanilla === type).map(([custom]) => custom);

function world(swap = () => undefined) {
  const blocks = new Map();
  for (const cell of layout) blocks.set(`${cell.x},${cell.y},${cell.z}`, swap(cell) ?? cell.block);
  return fakeBedrock({ blocks });
}

function connected(fake) {
  const types = [...new Set(layout.map(cell => cell.block))];
  const configuration = { dialect: 'optifine', baseTextures: rules.baseTextures, fullCubeBlocks: types, opaqueBlocks: types,
    rules: rules.rules.map(rule => ({ ...rule, entity: 'bct_board:' + rule.id })) };
  const carriers = createCarrierBudget(() => ({ maxCarriers: 100000, maxCarriersPerChunk: 100000 }));
  const source = createConnectedSource(configuration, { api: fake.api, carriers, vanillaType, aliasesOf });
  fake.flush();
  return source;
}

/** Tiles of the carriers drawn on one face, sorted. */
function tilesOn(fake, location, face) {
  return fake.entities.filter(entity => entity.isValid && entity.getDynamicProperty('bct:anchor')).filter(entity => {
    const anchor = JSON.parse(entity.getDynamicProperty('bct:anchor'));
    return anchor.face === face && ['x', 'y', 'z'].every(axis => anchor.location[axis] === location[axis]);
  }).map(entity => entity.getProperty('bct:tile')).sort((a, b) => a - b);
}

function drawBoard(fake, source) {
  for (const cell of layout) if (cell.face && cell.section !== 'terrain') source.update(fake.dimension, cell);
  fake.flush();
}

test('every connected-texture method picks the OptiFine tile on every board face', () => {
  const fake = world(), source = connected(fake);
  drawBoard(fake, source);
  const sections = new Map(layout.map(cell => [`${cell.x},${cell.y},${cell.z},${cell.face}`, cell.section]));
  const checked = new Set();
  for (const [key, tile] of Object.entries(expected.tiles)) {
    const [x, y, z, face] = key.split(',');
    const want = wanted(key, tile);
    assert.deepEqual(tilesOn(fake, { x: +x, y: +y, z: +z }, face), want, `${sections.get(key)} at ${key}`);
    if (want.length) checked.add(sections.get(key));
  }
  assert.deepEqual([...checked].sort(), ['ctm', 'fixed', 'horizontal', 'horizontal_vertical', 'overlay', 'random', 'repeat', 'top',
    'vertical', 'vertical_horizontal'], 'every method drew on the board');
});

test('a replacement reads as its vanilla block, states included', async () => {
  const { vanillaView } = await import('../engine/core.mjs');
  const fake = world();
  fake.blocks.set('0,5,0', 'bct_board:r_oak_planks');
  fake.states.set('0,5,0', { 'bct:connection_east': 1, 'bct:pillar_axis': 'x' });
  const plain = vanillaView(vanillaType).wrap(fake.dimension.getBlock({ x: 0, y: 5, z: 0 }));
  assert.equal(plain.typeId, 'minecraft:oak_planks');
  assert.equal(plain.replaced, true);
  assert.equal(plain.permutation.getState('pillar_axis'), 'x', 'mirrored states drop their bct: prefix');
  assert.equal(plain.permutation.getState('minecraft:connection_east'), 1, 'and their namespace');
  assert.equal(plain.offset({ x: 0, y: -5, z: 0 }).typeId, fake.blocks.get('0,0,0') ?? 'minecraft:air', 'neighbors are views too');
  const exact = vanillaView(vanillaType, () => ({ 'minecraft:connection_east': true })).wrap(fake.dimension.getBlock({ x: 0, y: 5, z: 0 }));
  assert.equal(exact.permutation.getState('minecraft:connection_east'), true, 'the engine can supply the exact vanilla states');
});

test('connected faces join replacement neighbors, and a replacement gets no carriers', () => {
  // The middle of the oak row and one square block of the ctm wall are replacement blocks.
  const swapped = new Set(['18,1,-6', '9,2,-6']);
  const fake = world(cell => swapped.has(`${cell.x},${cell.y},${cell.z}`) ? 'bct_board:r_' + cell.block.split(':')[1] : undefined);
  const source = connected(fake);
  drawBoard(fake, source);
  for (const [key, tile] of Object.entries(expected.tiles)) {
    const [x, y, z, face] = key.split(',');
    if (swapped.has(`${x},${y},${z}`)) {
      assert.deepEqual(tilesOn(fake, { x: +x, y: +y, z: +z }, face), [], 'a replacement draws its own look: ' + key);
      continue;
    }
    assert.deepEqual(tilesOn(fake, { x: +x, y: +y, z: +z }, face), wanted(key, tile), key);
  }
  assert.ok(source.blockTypes().includes('bct_board:r_oak_planks'), 'the scanner also finds replacements of drawn blocks');
});

test('carriers left on a block that becomes a replacement are removed', () => {
  const fake = world(), source = connected(fake);
  drawBoard(fake, source);
  const at = { x: 18, y: 1, z: -6 };
  assert.deepEqual(tilesOn(fake, at, 'south'), [1]);
  fake.blocks.set('18,1,-6', 'bct_board:r_oak_planks');
  source.update(fake.dimension, at);
  assert.deepEqual(tilesOn(fake, at, 'south'), []);
});

/** The converter's terrain provider with every edge drawn as a material of the native edge surface. */
function provider() {
  const copy = structuredClone(template);
  copy.exclusive_quarters = false;
  for (const effect of Object.values(copy.effects)) { delete effect.entity_surface; delete effect.height_tuning; }
  copy.scan_sources = [...new Set(copy.rules.flatMap(rule => rule.neighbors))];
  return copy;
}

function terrainEdges(fake) {
  const host = createTerrain({ api: fake.api, limits: () => ({ maxEntityCarriers: 1500, maxNativeCarriers: 8000 }),
    providers: [provider()], vanillaType, aliasesOf });
  for (const cell of layout) if (cell.section === 'terrain') host.update(fake.dimension, cell);
  const drawn = {};
  for (const cell of layout) {
    if (cell.section !== 'terrain') continue;
    const above = `${cell.x},1,${cell.z}`;
    if (fake.blocks.get(above) === 'bct:grass_surface') drawn[`${cell.x},0,${cell.z}`] = fake.states.get(above);
  }
  return { host, drawn };
}

test('grass, sand and red sand edges reach cobblestone and dirt hosts as the provider rules give them', () => {
  const { drawn } = terrainEdges(world());
  assert.deepEqual(drawn, expected.edges);
});

test('terrain edges are the same when hosts and sources are replacement blocks', () => {
  // Every cobblestone in the even columns, the grass and the sand patches are replacement blocks.
  const fake = world(cell => {
    if (cell.section !== 'terrain') return undefined;
    if (cell.block === 'minecraft:cobblestone' && cell.x % 2 === 0) return 'bct_board:r_cobblestone';
    if (['minecraft:sand', 'minecraft:grass_block'].includes(cell.block)) return 'bct_board:r_' + cell.block.split(':')[1];
    return undefined;
  });
  const { host, drawn } = terrainEdges(fake);
  assert.deepEqual(drawn, expected.edges, 'an edge renders above a replaced host and next to a replaced source');
  const types = host.blockTypes();
  for (const type of ['bct_board:r_sand', 'bct_board:r_grass_block', 'bct_board:r_red_sand']) assert.ok(types.includes(type), type);
});
