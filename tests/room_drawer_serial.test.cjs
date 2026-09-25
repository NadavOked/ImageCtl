// #418: the drawer chip is the only thing shown near the physical machine.
// Before this it carried the slot (#27) and the device name, but not the
// serial -- so two written drawers on the same machine both read "מגירה N
// · sd? · נכתבה" and a technician still cannot tell them apart by anything
// that survives pulling the drive out of the slot. Runs the shipped
// `drawerLine` renderer itself, not a copy of it.
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

function drawerLine(machine, withProgress) {
  const context = vm.createContext({
    esc: String,
    DRAWER_STATE: {done: 'נכתבה', failed: 'נכשלה', writing: 'כותבת',
                   verifying: 'מאמתת', waiting: 'ממתינה'},
    SMART_HE: {warn: 'SMART אזהרה', fail: 'SMART תקלה'},
    SLOTS_TITLE: 'slots',
  });
  vm.runInContext(functionSource('drawerLine'), context);
  return vm.runInContext(
    `drawerLine(${JSON.stringify(machine)}, ${JSON.stringify(withProgress)})`, context);
}

function machine(drawers) {
  return {name: 'HP1', joined: false, drawer_list: drawers};
}

test('a written drawer carries its serial, not only its slot', () => {
  const html = drawerLine(machine([
    {dev: 'sda', port: 1, state: null, fresh: false, serial: 'naa.111'},
  ]), false);
  assert.match(html, /naa\.111/);
});

test('two written drawers on the same machine are told apart by serial', () => {
  // Both read "מגירה N · sd? · נכתבה" without this -- the exact confusion
  // that sent Nadav to the wrong drive (issue #418).
  const html = drawerLine(machine([
    {dev: 'sda', port: 1, state: null, fresh: false, serial: 'naa.111'},
    {dev: 'sdb', port: 2, state: null, fresh: false, serial: 'naa.222'},
  ]), false);
  assert.match(html, /naa\.111/);
  assert.match(html, /naa\.222/);
});

test('a disk the agent never reported a serial for shows no invented tag', () => {
  // Rule 5: a missing serial must not be filled in with anything that
  // looks like one, and must not crash the render either.
  const html = drawerLine(machine([
    {dev: 'sda', port: 1, state: null, fresh: false, serial: null},
  ]), false);
  assert.doesNotMatch(html, /class="mono serial"/);
});

// #105: a drive with no serial cannot be in the round's written_serials --
// it has no identity to count. An old server sent it `fresh: false`, the
// same value as "written", and this chip said "נכתבה" over an EMPTY drive.
test('a drive with no serial is never labelled written', () => {
  for (const fresh of [false, null]) {
    const html = drawerLine(machine([
      {dev: 'sdb', port: 2, state: null, fresh, serial: null},
    ]), false);
    assert.doesNotMatch(html, /נכתבה/);
    assert.doesNotMatch(html, /class="drawer ok"/);
    assert.match(html, /מגירה 2/);
    assert.match(html, /לא ניתן לזהות את הכונן/);
  }
});

test('a drive with no serial shows this wave\'s own report beside it', () => {
  // The agent's report for this wave is evidence the round has no other
  // way to get; it is shown as it is, and the drive still says it cannot
  // be identified.
  const html = drawerLine({name: 'HP1', joined: true, drawer_list: [
    {dev: 'sdb', port: 2, state: 'done', fresh: null, serial: null},
  ]}, true);
  assert.match(html, /נכתבה · לא ניתן לזהות את הכונן/);
});

test('an identified drive keeps its two states', () => {
  const html = drawerLine(machine([
    {dev: 'sda', port: 1, state: null, fresh: true, serial: 'naa.1'},
    {dev: 'sdb', port: 2, state: null, fresh: false, serial: 'naa.2'},
  ]), false);
  assert.match(html, /מוכנה/);
  assert.match(html, /נכתבה/);
  assert.doesNotMatch(html, /לא ניתן לזהות/);
});
