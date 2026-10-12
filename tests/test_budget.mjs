import test from 'node:test';
import assert from 'node:assert/strict';
import { createBudget } from '../engine/budget.mjs';

test('every part gets turns even when the parts before it always overrun', () => {
  const system = { currentTick: 0 };
  let clock = 0;
  const budget = createBudget({ system, now: () => clock, limit: () => 4 });
  const runs = [0, 0, 0];
  // Each part always has work and spends 5 ms per step, more than the whole tick's budget.
  const parts = runs.map((_, index) => ({ run: () => { runs[index]++; clock += 5; return true; } }));
  for (let tick = 0; tick < 30; tick++) { system.currentTick = tick; budget.share(parts); }
  assert.deepEqual(runs, [10, 10, 10]);
});

test('a part with work next to players runs first, up to its cap', () => {
  const system = { currentTick: 0 };
  let clock = 0;
  const budget = createBudget({ system, now: () => clock, limit: () => 6 });
  const order = [];
  // Each part works in 1 ms steps until its time is up, and always has more.
  const step = name => until => { while (clock < until) { order.push(name); clock += 1; } return true; };
  budget.share([{ run: step('leaves'), cap: () => 6 }, { run: step('near'), cap: () => 5, first: () => true }]);
  assert.equal(order[0], 'near');
  assert.equal(order.slice(0, 3).join(), 'near,near,near', 'the near part goes first, within half the tick');
  assert.ok(order.includes('leaves'), 'the other parts still get time');
});
