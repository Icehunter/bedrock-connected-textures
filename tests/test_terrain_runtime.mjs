import test from 'node:test';
import assert from 'node:assert/strict';
import { runtime } from './terrain_runtime_fixture.mjs';

function source(id, entity = 'bct_' + id + ':moss') {
  return {
    format_version: 1,
    id,
    effects: { moss: { kind: 'surface', entity } },
    rules: [{ id: 'moss', effect: 'moss', targets: ['minecraft:oak_log'],
      neighbors: ['minecraft:grass_block'], contacts: ['down'], faces: ['north'] }],
  };
}
const origin = { x: 0, y: 0, z: 0 };

test('terrain waits for converted pack data and draws it without commands', () => {
  const host = runtime([]);
  host.maintain();
  assert.equal(host.entities.length, 0);
  assert.equal(host.properties.size, 0);
  host.addProviders([source('example')]);
  host.update(host.dimension, origin);
  assert.equal(host.owned.size, 1);
  assert.equal(host.entities[0].typeId, 'bct_example:moss');
});

test('replacing one source keeps the carriers of another source', () => {
  const host = runtime([source('first'), source('second')]);
  host.update(host.dimension, origin);
  const first = host.entities.find(entity => entity.typeId === 'bct_first:moss');
  const second = host.entities.find(entity => entity.typeId === 'bct_second:moss');
  host.addProviders([source('first', 'bct_first:weathered_moss')]);
  assert.equal(first.getProperty('bct:active'), false);
  assert.equal(second.getProperty('bct:active'), true);
  assert.equal(host.owned.size, 1);
  host.update(host.dimension, origin);
  assert.equal(host.owned.size, 2);
  assert.equal(host.entities.filter(entity => entity.typeId === second.typeId).length, 1);
  assert.ok(host.entities.some(entity => entity.typeId === 'bct_first:weathered_moss'));
  host.setProviders([source('second')]);
  assert.deepEqual(host.providers.map(provider => provider.id), ['second']);
  assert.equal(host.owned.size, 1);
  assert.equal(second.getProperty('bct:active'), true);
});

test('an invalid replacement is rejected before existing carriers change', () => {
  const host = runtime(source('example'));
  host.update(host.dimension, origin);
  const actor = host.entities[0];
  assert.throws(() => host.setProviders([source('example'), source('example')]), /Duplicate terrain source/);
  assert.equal(host.owned.size, 1);
  assert.equal(actor.getProperty('bct:active'), true);
  assert.equal(host.providers.length, 1);
});

test('replacing a source clears only the native surfaces it owns', () => {
  const first = source('first'), second = source('second');
  first.effects.moss.native_block = 'bct_first:surface';
  second.effects.moss.native_block = 'bct_second:surface';
  second.rules[0].targets = ['minecraft:birch_log'];
  const host = runtime([first, second]);
  host.grid.blocks.set('4,0,0', 'minecraft:birch_log');
  host.grid.blocks.set('4,-1,0', 'minecraft:grass_block');
  host.update(host.dimension, origin);
  host.update(host.dimension, { x: 4, y: 0, z: 0 });
  assert.equal(host.native.size, 2);
  host.setProviders([second]);
  assert.equal(host.grid.get([0, 1, 0]).typeId, 'minecraft:air');
  assert.equal(host.grid.get([4, 1, 0]).typeId, 'bct_second:surface');
  assert.equal(host.native.size, 1);
});

test('suspending for ray tracing removes only owned carriers and resumes afterwards', () => {
  const entity = source('entity'), native = source('native');
  native.effects.moss.native_block = 'bct_native:surface';
  const host = runtime([entity, native]);
  const user = host.dimension.spawnEntity(entity.effects.moss.entity, { x: 4, y: 4, z: 4 });
  host.update(host.dimension, origin);
  assert.equal(host.owned.size, 1); assert.equal(host.native.size, 1);
  host.suspend();
  assert.equal(host.owned.size, 0); assert.equal(host.native.size, 0);
  assert.equal(host.grid.get([0, 1, 0]).typeId, 'minecraft:air');
  assert.equal(host.grid.get([0, 0, 0]).typeId, 'minecraft:oak_log');
  for (const task of host.delayed.splice(0)) task.callback();
  assert.equal(user.isValid, true, 'carriers without the engine tag are never touched');
  const count = host.entities.length;
  host.update(host.dimension, origin);
  assert.equal(host.entities.length, count);
  host.resume();
  host.update(host.dimension, origin);
  assert.equal(host.owned.size, 1); assert.equal(host.native.size, 1);
  host.setEnabled(false);
  host.update(host.dimension, origin);
  assert.equal(host.owned.size, 0, 'disabled terrain draws nothing');
});

test('the entity carrier limit admits whole hosts only', () => {
  const host = runtime(source('example'));
  host.limits.maxEntityCarriers = 0;
  host.update(host.dimension, origin);
  assert.equal(host.owned.size, 0);
  assert.equal(host.host.status.capacitySkips, 1);
  host.limits.maxEntityCarriers = 1;
  host.update(host.dimension, origin);
  assert.equal(host.owned.size, 1);
});

test('the native carrier limit stops new surface blocks', () => {
  const native = source('native');
  native.effects.moss.native_block = 'bct_native:surface';
  const host = runtime(native);
  host.limits.maxNativeCarriers = 0;
  host.update(host.dimension, origin);
  assert.equal(host.native.size, 0);
  assert.equal(host.grid.get([0, 1, 0]).typeId, 'minecraft:air');
});

test('a scan of an exposed source updates the eight hosts around it', () => {
  const host = runtime(source('example'));
  host.grid.blocks.set('5,-1,5', 'minecraft:grass_block');
  const updated = [];
  const original = host.dimension.getBlock;
  host.dimension.getBlock = location => { updated.push(location); return original(location); };
  host.found(host.dimension, { x: 5, y: -1, z: 5 }, 'minecraft:grass_block');
  assert.equal(updated.filter(location => location.y === -1 && (location.x !== 5 || location.z !== 5)).length, 8);
});

test('an entity removed before its load event reaches scripts is passed over', () => {
  const host = runtime([source('example')]);
  const gone = { typeId: 'bct_example:moss', isValid: false, hasTag() { throw new Error('InvalidEntityError: Entity being invalid'); } };
  assert.doesNotThrow(() => host.onEntityLoad(gone));
  const removedLate = { typeId: 'bct_example:moss', hasTag() { throw new Error('InvalidEntityError: Entity being invalid'); } };
  assert.doesNotThrow(() => host.onEntityLoad(removedLate), 'removed between the validity check and hasTag');
  assert.equal(host.scheduled.length, 0, 'no recovery for an entity that is gone');
});
