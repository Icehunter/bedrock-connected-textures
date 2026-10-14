import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const qjs = process.env.QJS ?? 'qjs';
const available = !spawnSync(qjs, ['-e', '0']).error;

test('a burst of exploding blocks stays within the tick budget in QuickJS', { skip: !available && 'qjs is not installed' }, () => {
  const run = spawnSync(qjs, ['-m', fileURLToPath(new URL('./quickjs/explosion_perf.mjs', import.meta.url))], { encoding: 'utf8' });
  for (const line of (run.stdout ?? '').split('\n')) if (line.startsWith('RESULT ')) console.log(line);
  assert.equal(run.status, 0, run.stdout + run.stderr);
});
