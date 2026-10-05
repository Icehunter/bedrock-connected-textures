import test from 'node:test';
import assert from 'node:assert/strict';
import {DEFAULT_HEIGHTS, HEIGHT_MATERIALS, createHeightSettings} from '../engine/heights.mjs';

const TUNED = [.5, 1.25, 1.3, 2.5, 3.75];
const responseValues = (heights = TUNED, reset = false) => [...heights.map(String), reset];

function fixture() {
  const forms = [], applied = [], reports = [], messages = [];
  const player = {id: 'player', sendMessage: message => messages.push(message)};
  class ModalFormData {
    constructor() { this.controls = []; forms.push(this); }
    title(value) { this.formTitle = value; return this; }
    textField(...args) { this.controls.push(['textField', ...args]); return this; }
    toggle(...args) { this.controls.push(['toggle', ...args]); return this; }
    label(value) { this.controls.push(['label', value]); return this; }
    submitButton(value) { this.submit = value; return this; }
    show(value) {
      this.player = value;
      return new Promise((resolve, reject) => { this.resolve = resolve; this.reject = reject; });
    }
  }
  const options = {ModalFormData, applyHeights: heights => applied.push(heights), report: message => reports.push(message)};
  const settings = createHeightSettings(options);
  async function submit(formValues, actor = player) {
    const pending = settings.open(actor);
    forms.at(-1).resolve({canceled: false, formValues});
    return pending;
  }
  return {settings, options, forms, player, applied, reports, messages, submit};
}

test('default heights and material order are immutable', () => {
  assert.deepEqual(DEFAULT_HEIGHTS, [1, 2, 3, 4, 5]);
  assert.deepEqual(HEIGHT_MATERIALS.map(material => material.key), ['soul_sand', 'red_sand', 'suspicious_sand', 'sand', 'grass']);
  assert.ok(Object.isFrozen(DEFAULT_HEIGHTS));
  assert.ok(Object.isFrozen(HEIGHT_MATERIALS));
  assert.ok(HEIGHT_MATERIALS.every(Object.isFrozen));
});

test('five absolute height fields apply together only after submission', async () => {
  const host = fixture(), pending = host.settings.open(host.player);
  assert.deepEqual(host.settings.heights, DEFAULT_HEIGHTS);
  assert.deepEqual(host.applied, []);
  const form = host.forms[0];
  assert.equal(form.formTitle, 'Terrain edge heights');
  assert.equal(form.submit, 'Apply');
  assert.equal(form.player, host.player);
  for (const [index, material] of HEIGHT_MATERIALS.entries()) {
    assert.deepEqual(form.controls[index], ['textField', material.label + ' height (texture px)', '0.25 to 16', {defaultValue: String(DEFAULT_HEIGHTS[index])}]);
  }
  assert.deepEqual(form.controls[5], ['toggle', 'Reset to 1 / 2 / 3 / 4 / 5 px', {defaultValue: false}]);
  assert.match(form.controls[6][1], /Absolute heights.*Temporary.*resets on reload/);
  form.resolve({canceled: false, formValues: [...responseValues(), undefined]});
  assert.equal(await pending, true);
  assert.deepEqual(host.settings.heights, TUNED);
  assert.deepEqual(host.applied, [TUNED]);
  assert.ok(Object.isFrozen(host.settings.heights));
  assert.equal(host.applied[0], host.settings.heights);
  assert.match(host.messages.at(-1), /soul sand 0.5, red sand 1.25, suspicious sand 1.3, sand 2.5, grass 3.75 px above terrain/);
  assert.match(host.reports.at(-1), /Resets on reload/);
  const reopened = host.settings.open(host.player);
  assert.deepEqual(host.forms[1].controls.slice(0, 5).map(control => control[3].defaultValue), TUNED.map(String));
  host.forms[1].resolve({canceled: true});
  await reopened;
});

test('height tuning accepts both boundaries and gaps smaller than one pixel', async () => {
  const host = fixture();
  assert.equal(await host.submit([' .25 ', '+.3', '0.35', '15.99', '16.', false]), true);
  assert.deepEqual(host.settings.heights, [.25, .3, .35, 15.99, 16]);
  assert.equal(host.applied.length, 1);
});

test('cancel and malformed form responses do not change active heights', async () => {
  const host = fixture();
  await host.submit(responseValues());
  const canceled = host.settings.open(host.player);
  host.forms.at(-1).resolve({canceled: true, formValues: responseValues(DEFAULT_HEIGHTS, true)});
  assert.equal(await canceled, false);
  for (const values of [undefined, [], ['1'], [...responseValues(), 'extra'], [...responseValues(), undefined, undefined], ['1', '2', '3', '4', '5', 'true']]) {
    assert.equal(await host.submit(values), false);
    assert.deepEqual(host.settings.heights, TUNED);
  }
  assert.deepEqual(host.applied, [TUNED]);
});

test('blank, nonfinite, nondecimal and out-of-range heights are rejected with the material name', async () => {
  const host = fixture();
  await host.submit(responseValues());
  for (const invalid of ['', ' ', 'NaN', 'Infinity', '-Infinity', '0x2', 'no', '-1', '0.2499', '16.01', 2, null, undefined]) {
    const values = responseValues();
    values[2] = invalid;
    assert.equal(await host.submit(values), false);
    assert.deepEqual(host.settings.heights, TUNED);
    assert.match(host.messages.at(-1), /Suspicious sand.*Settings unchanged/);
  }
  assert.deepEqual(host.applied, [TUNED]);
});

test('equal or inverted material heights are rejected without a partial application', async () => {
  const host = fixture();
  for (let index = 1; index < 5; index++) {
    for (const delta of [0, -.1]) {
      const values = [...DEFAULT_HEIGHTS];
      values[index] = values[index - 1] + delta;
      assert.equal(await host.submit(responseValues(values)), false);
      assert.match(host.messages.at(-1), /must be higher than/);
      assert.deepEqual(host.settings.heights, DEFAULT_HEIGHTS);
    }
  }
  assert.deepEqual(host.applied, []);
});

test('reset toggle and reset command restore all five default heights', async () => {
  const host = fixture();
  await host.submit(responseValues());
  assert.equal(await host.submit(responseValues(TUNED, true)), true);
  assert.deepEqual(host.settings.heights, DEFAULT_HEIGHTS);
  await host.submit(responseValues());
  assert.equal(host.settings.reset(host.player), true);
  assert.deepEqual(host.settings.heights, DEFAULT_HEIGHTS);
  assert.deepEqual(host.applied, [TUNED, DEFAULT_HEIGHTS, TUNED, DEFAULT_HEIGHTS]);
});

test('a player cannot open two forms and can reopen after a UI failure', async () => {
  const host = fixture(), first = host.settings.open(host.player);
  assert.equal(await host.settings.open({...host.player}), false);
  assert.equal(host.forms.length, 1);
  host.forms[0].reject(new Error('Player is busy'));
  assert.equal(await first, false);
  assert.deepEqual(host.settings.heights, DEFAULT_HEIGHTS);
  assert.deepEqual(host.applied, []);
  assert.equal(await host.submit(responseValues()), true);
});

test('a stale form cannot override a newer selection from another player', async () => {
  const host = fixture(), first = host.settings.open(host.player);
  const second = host.settings.open({id: 'second', sendMessage: () => {}});
  host.forms[1].resolve({canceled: false, formValues: responseValues()});
  assert.equal(await second, true);
  host.forms[0].resolve({canceled: false, formValues: responseValues(DEFAULT_HEIGHTS)});
  assert.equal(await first, false);
  assert.deepEqual(host.settings.heights, TUNED);
  assert.deepEqual(host.applied, [TUNED]);
  assert.match(host.messages.at(-1), /changed while this form was open/);
});

test('reset invalidates pending forms even when default heights were already active', async () => {
  const host = fixture(), pending = host.settings.open(host.player);
  assert.equal(host.settings.reset(host.player), true);
  host.forms[0].resolve({canceled: false, formValues: responseValues()});
  assert.equal(await pending, false);
  assert.deepEqual(host.settings.heights, DEFAULT_HEIGHTS);
  assert.deepEqual(host.applied, [DEFAULT_HEIGHTS]);
});

test('an apply failure keeps active heights and does not stale another form', async () => {
  const host = fixture();
  let fail = false;
  const settings = createHeightSettings({...host.options, applyHeights: heights => {
    if (fail) throw new Error('Renderer unavailable');
    host.applied.push(heights);
  }});
  const first = settings.open(host.player);
  const second = settings.open({id: 'second', sendMessage: () => {}});
  fail = true;
  host.forms[0].resolve({canceled: false, formValues: responseValues()});
  assert.equal(await first, false);
  assert.deepEqual(settings.heights, DEFAULT_HEIGHTS);
  assert.deepEqual(host.applied, []);
  fail = false;
  host.forms[1].resolve({canceled: false, formValues: responseValues()});
  assert.equal(await second, true);
  assert.deepEqual(settings.heights, TUNED);
  fail = true;
  assert.equal(settings.reset(host.player), false);
  assert.deepEqual(settings.heights, TUNED);
});

test('a new session starts at default heights without reading or writing persistence', async () => {
  const host = fixture();
  host.player.getDynamicProperty = host.player.setDynamicProperty = () => assert.fail('Settings must not use persistence');
  await host.submit(responseValues());
  assert.deepEqual(host.settings.heights, TUNED);
  const reloaded = createHeightSettings(host.options);
  assert.deepEqual(reloaded.heights, DEFAULT_HEIGHTS);
  assert.deepEqual(host.applied, [TUNED]);
});

test('reporting failures cannot roll back successfully applied heights', async () => {
  const host = fixture();
  const settings = createHeightSettings({...host.options, report: () => { throw new Error('Logger unavailable'); }});
  const pending = settings.open({id: 'disconnected', sendMessage() { throw new Error('Player left'); }});
  host.forms[0].resolve({canceled: false, formValues: responseValues()});
  assert.equal(await pending, true);
  assert.deepEqual(settings.heights, TUNED);
  assert.deepEqual(host.applied, [TUNED]);
});
