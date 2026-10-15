import test from 'node:test';
import assert from 'node:assert/strict';

import { createConnected } from '../engine/connected.mjs';
import { fakeBedrock } from './fake_bedrock.mjs';
import { createCarrierBudget } from '../engine/settings.mjs';

const vanillaType = type => type;
const aliasesOf = () => [];

test('removing a source drops its instance and frees its blocks', () => {
  const fake = fakeBedrock({ blocks: new Map() });
  const carriers = createCarrierBudget(() => ({ maxCarriers: 100000, maxCarriersPerChunk: 100000 }));

  const connected = createConnected({
    api: fake.api,
    carriers,
    inReach: () => true,
    log: () => {},
    vanillaType,
    aliasesOf,
    vanillaStates: () => undefined,
  });

  const config = {
    rules: [
      {
        id: 'a',
        method: 'fixed',
        blocks: ['minecraft:stone'],
        faces: ['north', 'east', 'south', 'west', 'up', 'down'],
        tiles: ['a'],
        entity: 'bct:a',
        spawn_structure: 'bct:a',
      },
    ],
  };

  connected.setSource('authored:p', config);
  fake.flush();

  assert.equal(connected.status.sources, 1);
  assert.ok(connected.blockTypes().includes('minecraft:stone'));

  connected.removeSource('authored:p');

  assert.equal(connected.status.sources, 0);
  assert.deepEqual(connected.blockTypes(), []);

  // Removing again must not throw.
  assert.doesNotThrow(() => connected.removeSource('authored:p'));

  // Removing leaves another provider's source alone.
  const otherConfig = {
    rules: [
      {
        id: 'b',
        method: 'fixed',
        blocks: ['minecraft:dirt'],
        faces: ['north', 'east', 'south', 'west', 'up', 'down'],
        tiles: ['b'],
        entity: 'bct:b',
        spawn_structure: 'bct:b',
      },
    ],
  };
  connected.setSource('other', otherConfig);
  fake.flush();

  assert.equal(connected.status.sources, 1);
  const types = connected.blockTypes();
  assert.ok(types.includes('minecraft:dirt'));
  assert.ok(!types.includes('minecraft:stone'));
});
