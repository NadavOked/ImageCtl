// #1213: a machine the server already gave up on (`state: "lost"`, #450) was
// falling through to the last-known percent, and its LED stayed lit because
// the row was still `joined`. Runs the shipped `machineRows` itself.
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

function machineRows(machines, withProgress) {
  const context = vm.createContext({
    esc: String,
    Progress: {view: (m) => ({label: `${m.pct || 0}%`})},
    drawerLine: () => '',
  });
  vm.runInContext(functionSource('machineRows'), context);
  return vm.runInContext(
    `machineRows(${JSON.stringify(machines)}, ${JSON.stringify(withProgress)})`, context);
}

function lostMachine(overrides) {
  return {name: 'HP1', mac: 'aa:bb', joined: true, awake: false, state: 'lost', ...overrides};
}

test('a lost machine says it vanished, not its last percent', () => {
  const html = machineRows([lostMachine({pct: 63})], true);
  assert.match(html, /נעלם · לא ידוע אם נכתב/);
  assert.doesNotMatch(html, /63%/);
  assert.match(html, /class="room-warn"/);
});

test('a lost machine keeps a custom error over the default wording', () => {
  const html = machineRows([lostMachine({error: 'לא דיווח 5 דקות'})], true);
  assert.match(html, /נעלם · לא דיווח 5 דקות/);
});

test('a lost machine\'s LED is off even though it is still joined', () => {
  const html = machineRows([lostMachine()], true);
  assert.match(html, /class="led "/);
  assert.doesNotMatch(html, /class="led on"/);
  assert.match(html, /class="room-row dim"/);
});

test('a machine that is still writing keeps its LED lit (negative control target)', () => {
  const html = machineRows(
    [{name: 'HP2', mac: 'cc:dd', joined: true, awake: true, state: 'writing', pct: 40}], true);
  assert.match(html, /class="led on"/);
});
