// apple-design F11: long-press של עכבר 150ms, מגע נשאר 400ms (גלילה לא הופכת לגרירה).
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.resolve(__dirname, '../server/static/reorder.js'), 'utf8');

function load(extra = {}) {
  const ctx = {...extra};
  vm.createContext(ctx);
  vm.runInContext(source, ctx);
  return ctx;
}

test('mouse long-press is 150ms', () => {
  assert.equal(load().longPressDelay('mouse'), 150);
});

test('touch, pen and unknown keep 400ms', () => {
  const ctx = load();
  assert.equal(ctx.longPressDelay('touch'), 400);
  assert.equal(ctx.longPressDelay('pen'), 400);
  assert.equal(ctx.longPressDelay(undefined), 400);
});

test('pointerdown schedules begin with the delay of its pointerType', () => {
  const timeouts = [];
  const handlers = {};
  const item = {dataset: {}, classList: {add() {}, remove() {}}};
  const handle = {closest: () => item};
  const container = {
    addEventListener: (type, fn) => { handlers[type] = fn; },
    querySelectorAll: () => [],
    classList: {add() {}, remove() {}},
  };
  const ctx = load({
    setTimeout: (fn, ms) => { timeouts.push(ms); return 1; },
    clearTimeout() {},
    document: {body: {style: {}}},
  });
  ctx.enableLongPressReorder({container, itemSelector: '.item', handleSelector: '.h', onDrop() {}});
  const down = (pointerType) => handlers.pointerdown({
    pointerType, clientX: 0, clientY: 0,
    target: {closest: (sel) => (sel === '.h' ? handle : null)},
  });
  down('mouse');
  down('touch');
  assert.deepEqual(timeouts, [150, 400]);
});
