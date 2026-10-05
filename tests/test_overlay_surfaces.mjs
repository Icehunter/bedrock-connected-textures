// Overlay surfaces (engine/terrain-native.mjs createOverlaySurfaces) on a made-up pack:
// tests/fixtures/overlay/engine-data.json comes from converter/overlay_surfaces.py
// (python tests/test_overlay_surfaces.py --write-fixture). Rules: java_0 grass over
// stone, cobblestone and paths (connect texture: grass_block_top), java_1 sand over
// stone and cobblestone (connect block and texture: sand), java_2 fallen leaves on sand
// tops (overlay_random 1 1 8 with <skip>).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fakeBedrock } from './fake_bedrock.mjs';
import { createOverlaySurfaces, compileOverlays } from '../engine/terrain-native.mjs';
import { OVERLAY_COMBOS, overlayTiles } from '../engine/overlay.mjs';
import { javaRandom } from '../engine/tiles.mjs';
import { startEngine } from '../engine/engine.mjs';
import { publishSources } from '../engine/publisher.mjs';
import { checksum } from '../engine/sources.mjs';
import { referenceOverlayTiles } from './overlay_reference.mjs';

const data = JSON.parse(readFileSync(new URL('./fixtures/overlay/engine-data.json', import.meta.url), 'utf8'));
const LIMITS = { chunkRadius: 4, yBand: 24, sliceMs: 1000, refreshTicks: 1200 };
const REPLACED = { 'bct_test:r_stone': 'minecraft:stone', 'bct_test:r_sand': 'minecraft:sand', 'bct_test:r_grass_block': 'minecraft:grass_block' };
const key = (x, y, z) => `${x},${y},${z}`;

function setup(blocks = [], { replaced = REPLACED, foreign = new Set(), source = data } = {}) {
  const fake = fakeBedrock({ blocks: new Map(blocks) });
  fake.dimension.getBiome = () => ({ id: 'minecraft:plains' });
  const overlays = createOverlaySurfaces({ api: fake.api, limits: () => LIMITS, vanillaType: type => replaced[type] ?? type,
    aliasesOf: type => Object.keys(replaced).filter(custom => replaced[custom] === type), foreign: () => foreign });
  overlays.setSource('made-up', source);
  return { ...fake, overlays };
}

/** Refreshes the cells around a block, as a block event does, and runs one tick. */
function touch(env, x, y, z) {
  env.overlays.changed({ dimension: env.dimension, location: { x, y, z } });
  env.overlays.tick([]);
}

/** What a cell draws: {face: {rule id: [tiles]}} decoded from its block type and states. */
function drawn(env, x, y, z) {
  const type = data.types.find(entry => entry.block === env.blocks.get(key(x, y, z)));
  if (!type) return null;
  const states = env.states.get(key(x, y, z)) ?? {}, result = {};
  for (const channel of type.channels) {
    let option = 0, scale = 1;
    for (const [name, size] of channel.states) { option += (states[name] ?? 0) * scale; scale *= size; }
    if (!option) continue;
    for (const [rule, values] of channel.options) {
      if (option > values.length) { option -= values.length; continue; }
      const value = values[option - 1], source = data.rules[rule];
      const tiles = source.method === 'overlay' ? overlayTiles(...OVERLAY_COMBOS[value]) : [value - 1];
      const face = (result[channel.face + (channel.offset ? channel.offset : '')] ??= {});
      face[source.id] = tiles;
      break;
    }
  }
  return result;
}

test('overlay data is checked before anything is drawn', () => {
  assert.doesNotThrow(() => compileOverlays(data));
  const broken = structuredClone(data);
  broken.types[0].channels[0].states = [['bct:c0a', 17]];
  assert.throws(() => compileOverlays(broken), /states/);
  const unknownFace = structuredClone(data);
  unknownFace.rules[0].faces = ['top'];
  assert.throws(() => compileOverlays(unknownFace), /faces/);
  assert.throws(() => compileOverlays({ ...data, types: [] }), /types/);
});

test('the top face shows the tiles Java picks around the block below', () => {
  // Stone hosts, grass sources (their top is grass_block_top) and cobblestone around a stone block;
  // stone, cobblestone and grass above a neighbor cover it. Compared with Continuity's processor.
  let state = 2026;
  const next = () => (state = (Math.imul(state, 1664525) + 1013904223) >>> 0) / 2 ** 32;
  const plane = ['minecraft:stone', 'minecraft:grass_block', 'minecraft:cobblestone', 'minecraft:air'];
  const above = ['minecraft:air', 'minecraft:air', 'minecraft:stone'];
  let drawnCases = 0;
  for (let trial = 0; trial < 300; trial++) {
    const blocks = [[key(0, 64, 0), 'minecraft:stone']];
    for (let x = -1; x <= 1; x++) for (let z = -1; z <= 1; z++) {
      if (!x && !z) continue;
      blocks.push([key(x, 64, z), plane[Math.floor(next() * plane.length)]]);
      blocks.push([key(x, 65, z), above[Math.floor(next() * above.length)]]);
    }
    const env = setup(blocks.filter(([, type]) => type !== 'minecraft:air'));
    // Surfaces the refresh puts above the neighbors are not solid, so the reference reads the world first.
    const kind = ([dx, dy, dz]) => {
      const type = env.blocks.get(key(dx, 64 + dy, dz)) ?? 'minecraft:air';
      if (dy === 1) return type === 'minecraft:air' ? 'air' : 'solid';
      return type === 'minecraft:grass_block' ? 'src' : type === 'minecraft:stone' ? 'host' : 'other';
    };
    const want = referenceOverlayTiles(kind, 'up', 'optifine');
    touch(env, 0, 65, 0);
    const got = drawn(env, 0, 65, 0);
    assert.deepEqual(got?.up?.java_0 ?? [], want, `trial ${trial}`);
    if (want.length) drawnCases++;
  }
  assert.ok(drawnCases > 100, 'most neighborhoods draw something');
});

test('a side face gets the strip from the block above it, from the cell in front of it', () => {
  // Sand on top of a stone block: the stone's north face shows the sand strip along its top (tile 15).
  const env = setup([[key(0, 64, 0), 'minecraft:stone'], [key(0, 65, 0), 'minecraft:sand']]);
  touch(env, 0, 64, -1);
  assert.deepEqual(drawn(env, 0, 64, -1), { north: { java_1: [15] } });
  const kind = ([dx, dy, dz]) => ({ [key(0, 1, 0)]: 'src' })[key(dx, dy, dz)] ?? 'air';
  assert.deepEqual(referenceOverlayTiles(kind, 'north', 'optifine'), [15]);
  // Sand beside the stone in the plane of its east face: seen from the east, south is on the left.
  const side = setup([[key(0, 64, 0), 'minecraft:stone'], [key(0, 64, 1), 'minecraft:sand']]);
  touch(side, 1, 64, 0);
  assert.deepEqual(drawn(side, 1, 64, 0), { east: { java_1: [9] } });
});

test('one cell draws the top face below it and the strips of the faces beside it', () => {
  const env = setup([
    [key(0, 64, 0), 'minecraft:stone'], [key(1, 64, 0), 'minecraft:grass_block'],      // grass east of the stone below the cell
    [key(-1, 65, 0), 'minecraft:stone'], [key(-1, 66, 0), 'minecraft:sand'],           // a stone west of the cell with sand on top
    [key(0, 65, 1), 'minecraft:cobblestone'], [key(0, 66, 1), 'minecraft:sand'],       // and a cobblestone south of it with sand on top
  ]);
  touch(env, 0, 65, 0);
  assert.deepEqual(drawn(env, 0, 65, 0), { up: { java_0: [7] }, east: { java_1: [15] }, north: { java_1: [15] } });
  assert.equal(env.overlays.status.dropped, 0);
});

test('two rules on one top face draw together; a third face that does not fit is counted', () => {
  const env = setup([[key(0, 64, 0), 'minecraft:stone'], [key(1, 64, 0), 'minecraft:grass_block'], [key(-1, 64, 0), 'minecraft:sand']]);
  touch(env, 0, 65, 0);
  assert.deepEqual(drawn(env, 0, 65, 0), { up: { java_0: [7], java_1: [9] } });
  // With a full side overlay (not a strip) next to two top rules, the top wins and the side is left out.
  const crowded = setup([[key(0, 64, 0), 'minecraft:stone'], [key(1, 64, 0), 'minecraft:grass_block'], [key(-1, 64, 0), 'minecraft:sand'],
    [key(0, 65, 1), 'minecraft:stone'], [key(1, 65, 1), 'minecraft:sand']]);
  touch(crowded, 0, 65, 0);
  assert.deepEqual(drawn(crowded, 0, 65, 0), { up: { java_0: [7], java_1: [9] } });
  assert.ok(crowded.overlays.status.dropped > 0);
});

test('lowered tops (paths, bottom slabs) get no overlay; a top slab draws like a full block', () => {
  // A quad on a lowered top would sit below the surface block's own cell, which the game rejects.
  const path = setup([[key(0, 64, 0), 'minecraft:grass_path'], [key(0, 64, -1), 'minecraft:grass_block']]);
  touch(path, 0, 65, 0);
  assert.equal(drawn(path, 0, 65, 0), null);
  const slab = setup([[key(0, 64, 0), 'minecraft:cobblestone_slab'], [key(0, 64, -1), 'minecraft:grass_block']]);
  slab.states.set(key(0, 64, 0), { 'minecraft:vertical_half': 'top' });
  touch(slab, 0, 65, 0);
  assert.deepEqual(drawn(slab, 0, 65, 0), { up: { java_0: [15] } });
});

test('random overlays follow the Java draw, weights and <skip>', () => {
  const blocks = Array.from({ length: 48 }, (_, x) => [key(x, 64, 0), 'minecraft:sand']);
  const env = setup(blocks);
  for (let x = 0; x < 48; x++) env.overlays.changed({ dimension: env.dimension, location: { x, y: 65, z: 0 } });
  env.overlays.tick([]);
  let skipped = 0, shown = 0;
  for (let x = 0; x < 48; x++) {
    // Continuity's random index with weights 1 1 8 and randomLoops 1; tile 2 is <skip>.
    let draw = javaRandom({ x, y: 64, z: 0 }, 'up', 1) % 10, tile = 0;
    for (const weight of [1, 1, 8]) { if (draw < weight) break; draw -= weight; tile++; }
    const got = drawn(env, x, 65, 0);
    if (tile === 2) { assert.equal(got, null, 'skip draws nothing at ' + x); skipped++; }
    else { assert.deepEqual(got, { up: { java_2: [tile] } }, 'tile at ' + x); shown++; }
  }
  assert.ok(skipped > 0 && shown > 0);
});

test('a surface never replaces a block that is not air', () => {
  for (const type of ['minecraft:tall_grass', 'minecraft:water', 'minecraft:snow_layer', 'minecraft:torch']) {
    const env = setup([[key(0, 64, 0), 'minecraft:stone'], [key(1, 64, 0), 'minecraft:grass_block'], [key(0, 65, 0), type]]);
    touch(env, 0, 65, 0);
    assert.equal(env.blocks.get(key(0, 65, 0)), type);
    assert.ok(!env.writes.some(write => write.location.x === 0 && write.location.y === 65 && write.location.z === 0), type);
  }
});

test('a player placing a block gets its overlay in the same tick', () => {
  const env = setup([[key(0, 64, 0), 'minecraft:stone'], [key(1, 64, 0), 'minecraft:grass_block']]);
  env.overlays.changed({ dimension: env.dimension, location: { x: 1, y: 64, z: 0 } }, { immediate: true });
  assert.ok(env.overlays.surfaceTypes().has(env.blocks.get(key(0, 65, 0))), 'drawn before any tick');
  assert.equal(env.overlays.status.queued, 0);
});

test('a surface goes when its host or the block giving the overlay changes', () => {
  const env = setup([[key(0, 64, 0), 'minecraft:stone'], [key(1, 64, 0), 'minecraft:grass_block']]);
  touch(env, 0, 65, 0);
  assert.ok(drawn(env, 0, 65, 0));
  env.blocks.delete(key(1, 64, 0));                       // the grass is dug out
  touch(env, 1, 64, 0);
  assert.equal(env.blocks.get(key(0, 65, 0)), undefined, 'no grass, no edge');
  env.blocks.set(key(1, 64, 0), 'minecraft:grass_block');
  touch(env, 1, 64, 0);
  assert.ok(drawn(env, 0, 65, 0));
  env.blocks.set(key(0, 64, 0), 'minecraft:oak_planks');  // the host becomes a block the rule does not match
  touch(env, 0, 64, 0);
  assert.equal(env.blocks.get(key(0, 65, 0)), undefined);
  // A change nobody reports (fire, water) is found by the surfaces' own checks.
  env.blocks.set(key(0, 64, 0), 'minecraft:stone');
  touch(env, 0, 64, 0);
  assert.ok(drawn(env, 0, 65, 0));
  env.blocks.delete(key(0, 64, 0));
  for (let tick = 0; tick < 4; tick++) env.overlays.tick([]);
  assert.equal(env.blocks.get(key(0, 65, 0)), undefined);
  // A player placing a block into the cell replaces the surface; it is forgotten, not redrawn over the block.
  env.blocks.set(key(0, 64, 0), 'minecraft:stone');
  touch(env, 0, 64, 0);
  env.blocks.set(key(0, 65, 0), 'minecraft:stone');
  touch(env, 0, 65, 0);
  assert.equal(env.blocks.get(key(0, 65, 0)), 'minecraft:stone');
});

test('replacement blocks read as their vanilla block; rules a replacement draws itself are left out', () => {
  const plain = setup([[key(0, 64, 0), 'minecraft:stone'], [key(1, 64, 0), 'minecraft:grass_block'], [key(0, 64, -1), 'minecraft:sand']]);
  touch(plain, 0, 65, 0);
  const swapped = setup([[key(0, 64, 0), 'bct_test:r_stone'], [key(1, 64, 0), 'bct_test:r_grass_block'], [key(0, 64, -1), 'bct_test:r_sand']]);
  touch(swapped, 0, 65, 0);
  assert.deepEqual(drawn(swapped, 0, 65, 0), drawn(plain, 0, 65, 0));
  assert.deepEqual(drawn(plain, 0, 65, 0), { up: { java_0: [7], java_1: [15] } });
  // The fallen leaves rule is drawn by the sand replacement itself: vanilla sand gets it as a surface, the replacement does not.
  const sandAt = Array.from({ length: 48 }, (_, x) => x).find(x => {
    let draw = javaRandom({ x, y: 64, z: 0 }, 'up', 1) % 10;
    return draw < 2;
  });
  const vanilla = setup([[key(sandAt, 64, 0), 'minecraft:sand']]);
  touch(vanilla, sandAt, 65, 0);
  assert.ok(drawn(vanilla, sandAt, 65, 0)?.up?.java_2);
  const replaced = setup([[key(sandAt, 64, 0), 'bct_test:r_sand']]);
  touch(replaced, sandAt, 65, 0);
  assert.equal(drawn(replaced, sandAt, 65, 0), null);
});

test('a generated edge block in the cell gives way to the pack overlay; an unloaded neighbor waits', () => {
  const foreign = new Set(['bct_test:grass_surface']);
  const env = setup([[key(0, 64, 0), 'minecraft:stone'], [key(1, 64, 0), 'minecraft:grass_block'], [key(0, 65, 0), 'bct_test:grass_surface']], { foreign });
  touch(env, 0, 65, 0);
  assert.ok(drawn(env, 0, 65, 0)?.up?.java_0);
  const kept = setup([[key(0, 64, 0), 'minecraft:oak_planks'], [key(0, 65, 0), 'bct_test:grass_surface']], { foreign });
  touch(kept, 0, 65, 0);
  assert.equal(kept.blocks.get(key(0, 65, 0)), 'bct_test:grass_surface', 'nothing to draw: the generated edge stays');
  // The block north of the host is in a chunk that is not loaded: the cell keeps what it has.
  const waiting = setup([[key(0, 64, 0), 'minecraft:stone'], [key(1, 64, 0), 'minecraft:grass_block']]);
  const getBlock = waiting.dimension.getBlock;
  waiting.dimension.getBlock = location => location.x === 0 && location.y === 64 && location.z === -1 ? undefined : getBlock(location);
  touch(waiting, 0, 65, 0);
  assert.equal(waiting.blocks.get(key(0, 65, 0)), undefined);
  assert.ok(waiting.overlays.status.unknown > 0);
});

test('biome and height filters apply to the block under the overlay', () => {
  const filtered = structuredClone(data);
  filtered.rules[0].biomes = { ids: ['minecraft:desert'], exclude: true };
  filtered.rules[0].heights = [[0, 100]];
  const env = setup([[key(0, 64, 0), 'minecraft:stone'], [key(1, 64, 0), 'minecraft:grass_block'],
    [key(0, 120, 0), 'minecraft:stone'], [key(1, 120, 0), 'minecraft:grass_block']], { source: filtered });
  touch(env, 0, 65, 0);
  touch(env, 0, 121, 0);
  assert.ok(drawn(env, 0, 65, 0)?.up?.java_0, 'plains at y 64');
  assert.equal(drawn(env, 0, 121, 0), null, 'above the height range');
  env.dimension.getBiome = () => ({ id: 'minecraft:desert' });
  touch(env, 0, 64, 0);
  assert.equal(drawn(env, 0, 65, 0), null, 'excluded biome');
});

test('a scan finds sources under plants but not under solid blocks', () => {
  const env = setup([[key(0, 64, 0), 'minecraft:stone'], [key(1, 64, 0), 'minecraft:grass_block'], [key(1, 65, 0), 'minecraft:tall_grass']]);
  const record = { key: 'overworld:0:0', x: 0, z: 0 };
  // prepare takes its steps as a generator (the scanner runs it within the time budget).
  const prepare = (overlays, ...args) => { const steps = overlays.prepare(...args); for (;;) { const step = steps.next(); if (step.done) return step.value; } };
  const context = prepare(env.overlays, env.dimension, record, 60, 70);
  env.overlays.found(env.dimension, { x: 1, y: 64, z: 0 }, 'minecraft:grass_block', context);
  env.overlays.tick([]);
  assert.deepEqual(drawn(env, 0, 65, 0), { up: { java_0: [7] } }, 'Java draws the edge from a grass block under a plant');
  const buried = setup([[key(0, 64, 0), 'minecraft:stone'], [key(1, 64, 0), 'minecraft:grass_block'], [key(1, 65, 0), 'minecraft:stone']]);
  const covered = prepare(buried.overlays, buried.dimension, record, 60, 70);
  buried.overlays.found(buried.dimension, { x: 1, y: 64, z: 0 }, 'minecraft:grass_block', covered);
  assert.equal(buried.overlays.status.queued, 0, 'a grass block under stone gives nothing');
});

test('generated terrain edges read an overlay surface like air', async () => {
  const { evaluate } = await import('../engine/terrain-rules.mjs');
  const provider = JSON.parse(readFileSync(new URL('./fixtures/release-provider.json', import.meta.url), 'utf8'));
  // Grass north of a cobblestone, with an overlay surface (drawing some other face) in the cell above the grass.
  const env = setup([[key(0, 64, 0), 'minecraft:cobblestone'], [key(0, 64, -1), 'minecraft:grass_block'], [key(0, 65, -1), 'bct_made_up:overlay_3']]);
  const host = env.dimension.getBlock({ x: 0, y: 64, z: 0 });
  const edges = open => evaluate(provider, host, open).filter(output => output.rule === 'grass_over_surfaces').map(output => output.tile);
  assert.deepEqual(edges(undefined), [], 'without the surface types the grass looks covered');
  assert.deepEqual(edges(env.overlays.surfaceTypes()), [1], 'the grass to the north gives its edge');
});

function packet(engine, provider, value) {
  const text = JSON.stringify(value);
  return { engine, provider, digest: checksum(text), parts: text.match(/[\s\S]{1,750}/g) };
}

test('the engine takes overlay data from the terrain packet, draws near players in every graphics mode and cleans up when off', () => {
  const fake = fakeBedrock({ blocks: new Map([[key(8, 70, 8), 'minecraft:stone'], [key(9, 70, 8), 'minecraft:grass_block']]) });
  fake.dimension.getBiome = () => ({ id: 'minecraft:plains' });
  const engine = startEngine(fake.api);
  publishSources({ system: fake.system, world: fake.world, sources: [packet('connected', 'made-up', { rules: [] }), packet('terrain', 'made-up', [{ overlay: data }])] });
  fake.player({ x: 8, y: 71, z: 8 }, 'RayTraced');
  for (let step = 0; step < 40 && !fake.blocks.get(key(8, 71, 8))?.startsWith('bct_made_up:'); step++) fake.step(1);
  assert.equal(engine.status().overlays.packs, 1);
  assert.equal(engine.status().suspended, true, 'every player uses ray tracing');
  assert.match(fake.blocks.get(key(8, 71, 8)) ?? '', /^bct_made_up:overlay_/, 'the surface still draws in ray tracing');
  fake.system.sendScriptEvent('bct:control', 'off');
  fake.step(2);
  assert.equal(fake.blocks.get(key(8, 71, 8)), undefined, 'turned off, the engine removes its surfaces');
});
