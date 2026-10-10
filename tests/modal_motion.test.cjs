// apple-design F06: החלון נפתח/נסגר במצבי class (לא display), נקודת המוצא לפי הכפתור,
// והניקוי של sheet-mode ממתין לסוף ה-fade ובטל בפתיחה חוזרת.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../server/static/console.js'), 'utf8');
const start = source.indexOf('const MODAL_CLOSE_MS');
const end = source.indexOf('function toast(');
assert.ok(start > 0 && end > start, 'modal helpers not found');

function classSet() {
  const set = new Set();
  return {
    add: (...c) => c.forEach((x) => set.add(x)),
    remove: (...c) => c.forEach((x) => set.delete(x)),
    toggle: (c, on) => { if (on) set.add(c); else set.delete(c); },
    contains: (c) => set.has(c),
    has: (c) => set.has(c),
  };
}

function setup(trigger) {
  const timers = [];
  const props = {};
  const modal = {
    classList: classSet(), style: {
      setProperty: (k, v) => { props[k] = v; }, removeProperty: (k) => { delete props[k]; },
    }, contains: () => false,
  };
  const form = {classList: classSet(), onsubmit: () => {}};
  const box = {offsetLeft: 100, offsetTop: 50};
  const els = {modal, sheet: form, modalBox: box};
  const ctx = {
    document: {getElementById: (id) => els[id], activeElement: trigger, body: {}},
    setTimeout: (fn, ms) => { timers.push({fn, ms}); return timers.length; },
    clearTimeout: (id) => { if (id) timers[id - 1].cancelled = true; },
  };
  vm.createContext(ctx);
  vm.runInContext(source.slice(start, end) + '\n;this.api={openModalFrame,closeModal};', ctx);
  return {api: ctx.api, modal, form, props, timers};
}

test('open shows via class only and takes origin from the trigger button', () => {
  const trigger = {getBoundingClientRect: () => ({left: 300, top: 200, width: 40, height: 20})};
  const {api, modal, props} = setup(trigger);
  api.openModalFrame(modal, 'box');
  assert.ok(modal.classList.contains('show'));
  assert.equal('display' in modal.style, false);
  assert.equal(props['--origin'], '220px 160px');
});

test('no usable trigger falls back to centre (no --origin)', () => {
  const {api, modal, props} = setup(undefined);
  api.openModalFrame(modal, 'sheet-mode');
  assert.equal(props['--origin'], undefined);
  assert.ok(modal.classList.contains('sheet-mode'));
});

test('close drops show at once but defers sheet-mode cleanup until the fade ends', () => {
  const {api, modal, form, timers} = setup(undefined);
  api.openModalFrame(modal, 'sheet-mode');
  api.closeModal();
  assert.equal(modal.classList.contains('show'), false);
  assert.ok(modal.classList.contains('sheet-mode'), 'content must not swap while fading');
  assert.equal(form.onsubmit, null);
  const cleanup = timers[timers.length - 1];
  assert.ok(cleanup.ms >= 240);
  cleanup.fn();
  assert.equal(modal.classList.contains('sheet-mode'), false);
  assert.ok(form.classList.contains('hidden'));
});

test('reopening mid-close cancels the pending cleanup (direction flips, input not blocked)', () => {
  const {api, modal, timers} = setup(undefined);
  api.openModalFrame(modal, 'sheet-mode');
  api.closeModal();
  api.openModalFrame(modal, 'sheet-mode');
  assert.ok(modal.classList.contains('show'));
  assert.equal(timers[timers.length - 1].cancelled, true);
});
