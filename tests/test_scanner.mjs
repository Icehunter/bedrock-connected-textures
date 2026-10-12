import test from 'node:test';
import assert from 'node:assert/strict';
import { Scanner } from '../engine/scanner.mjs';

function setup() {
  let clock = 0;
  const scanned = [];
  const dimension = { id: 'minecraft:overworld', heightRange: { min: -64, max: 320 }, isChunkLoaded: () => true };
  const scanner = new Scanner({
    system: {}, BlockVolume: class {}, jobs: () => 1, now: () => clock,
    consumers: [{ radius: () => 1, band: () => 8, types: () => [], *scan(_, record) { scanned.push(record.key); yield; } }],
  });
  const player = { id: 'p', dimension, location: { x: 8, y: 0, z: 8 } };
  return { scanner, scanned, player, advance: ms => { clock += ms; }, now: () => clock };
}

test('the next chunk starts as soon as a scan ends, not at the next poll', () => {
  const env = setup();
  env.scanner.poll([env.player]);
  env.scanner.run(Infinity);
  assert.equal(env.scanned.length, 9, 'every chunk within the radius in one run');
});

test('chunks next to a player are scanned again shortly after, a few times', () => {
  const env = setup();
  env.scanner.poll([env.player]);
  env.scanner.run(Infinity);
  const own = 'minecraft:overworld:0:0';
  const count = () => env.scanned.filter(key => key === own).length;
  assert.equal(count(), 1);
  for (let pass = 0; pass < 6; pass++) {
    env.advance(7000);
    env.scanner.poll([env.player]);
    env.scanner.run(Infinity);
  }
  assert.equal(count(), 3, 'scanned again twice, then only when something marks it dirty');
});
