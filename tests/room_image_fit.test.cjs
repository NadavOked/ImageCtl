// #953: on the room screen an image that does not fit the smallest reported
// drawer is offered disabled, with the reason -- the exact string the server
// refuses with (room.fit_refusal), so the operator reads one reason from both
// sides. No report at all (machines off) keeps every image enabled and says
// so. Runs the shipped `imageFitReason` / `imageOption` themselves.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const FILE = path.resolve(__dirname, '../server/static/station/room.js');

function functionSource(name) {
  const source = fs.readFileSync(FILE, 'utf8');
  const lines = source.split('\n');
  const start = lines.findIndex(
    (line) => new RegExp(`^(\\s*)(async )?function ${name}\\(`).test(line));
  assert.ok(start >= 0, `renderer ${name} exists in room.js`);
  const indent = lines[start].match(/^\s*/)[0];
  const end = lines.findIndex((line, i) => i > start && line.trimEnd() === indent + '}');
  return lines.slice(start, end + 1).join('\n');
}

function context() {
  const ctx = vm.createContext({});
  vm.runInContext(functionSource('requirementBytes'), ctx);
  vm.runInContext(functionSource('imageFitReason'), ctx);
  vm.runInContext(functionSource('imageOption'), ctx);
  return ctx;
}

function call(name, ...args) {
  return vm.runInContext(
    `${name}(${args.map((a) => JSON.stringify(a)).join(', ')})`, context());
}

const GB = 1e9;
const DISK_256 = 256060514304;                      // a real "256" drive, 17/09
const FLOOR = {mac: 'aa:bb:cc:00:00:32', name: '2', port: 2, dev: 'sdb',
               size_bytes: DISK_256};
const NEEDS_300 = {id: 'img_fit300', name: 'Needs 300', folder: '',
                   min_target_bytes: 299556 * 1e6};
const FITS_230 = {id: 'img_fit230', name: 'Fits 256', folder: 'Office',
                  min_target_bytes: 229556 * 1e6};

test('the reason names the drawer, the machine and both sizes -- the server string', () => {
  assert.equal(call('imageFitReason', NEEDS_300, FLOOR),
               "דיסק 2 במחשב 2 הוא 256GB, האימג' צריך 300GB");
});

test('an image that fits, or no floor at all, has no reason', () => {
  assert.equal(call('imageFitReason', FITS_230, FLOOR), null);
  assert.equal(call('imageFitReason', NEEDS_300, null), null);
});

test('a drawer without a port is named by its device', () => {
  assert.match(call('imageFitReason', NEEDS_300, {...FLOOR, port: null}),
               /^דיסק sdb במחשב 2 הוא 256GB/);
});

test('the option is disabled with the reason; a fitting one stays enabled and shows its floor', () => {
  const blocked = call('imageOption', NEEDS_300, FLOOR);
  assert.equal(blocked.disabled, true);
  assert.equal(blocked.text,
               "Needs 300 · מ-300GB — לא נכנס: דיסק 2 במחשב 2 הוא 256GB, האימג' צריך 300GB");

  const ok = call('imageOption', FITS_230, FLOOR);
  assert.equal(ok.disabled, false);
  assert.equal(ok.text, 'Office / Fits 256 · מ-230GB');
});

test('with no disk report every image is offered (the machine refuses, not the screen)', () => {
  assert.equal(call('imageOption', NEEDS_300, null).disabled, false);
});

test('an unknown requirement is neither blocked nor labelled with a number', () => {
  const view = call('imageOption', {id: 'x', name: 'Old', folder: '', min_target_bytes: null}, FLOOR);
  assert.equal(view.disabled, false);
  assert.equal(view.text, 'Old');
});
