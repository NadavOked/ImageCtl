// #872: CRC (attribute 199) as a per-round delta, shown next to the drawer as
// a note about the cable -- never as the disk's colour. The cumulative counter
// is 137-55,242 on every classroom disk (15/09) and says nothing about this
// round; the rise during the round does. Absent = not measured, and a
// measured 0 shows nothing either. Runs the shipped `drawerLine` itself.
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
    SMART_HE: {fail: 'SMART תקלה'},
    SLOTS_TITLE: 'slots',
  });
  vm.runInContext(functionSource('drawerLine'), context);
  return vm.runInContext(
    `drawerLine(${JSON.stringify(machine)}, ${JSON.stringify(withProgress)})`, context);
}

function live(drawer) {
  return {name: 'HP1', joined: true, drawer_list: [drawer]};
}

test('a CRC rise during the round is said next to the drawer, with the cable hint', () => {
  const html = drawerLine(live(
    {dev: 'sda', port: 1, state: 'done', fresh: false, smart: 'ok', crc_delta: 5}), true);
  assert.match(html, /CRC \+5 · לבדוק כבל/);
  // it is a note, not the disk's tone: the drawer itself stays "ok"
  assert.match(html, /class="drawer ok"/);
});

test('a measured zero and a missing measurement both show nothing', () => {
  for (const drawer of [
    {dev: 'sda', port: 1, state: 'done', fresh: false, smart: 'ok', crc_delta: 0},
    {dev: 'sda', port: 1, state: 'done', fresh: false, smart: 'ok', crc_delta: null},
    {dev: 'sda', port: 1, state: 'done', fresh: false, smart: 'ok'},
  ]) {
    assert.doesNotMatch(drawerLine(live(drawer), true), /CRC/);
  }
});
