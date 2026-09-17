// #856: a station whose restore finished but whose computer name could not
// be written reports `done` with an `error` on the target ("שם המחשב לא
// נכתב: <reason>"). Before this fix memberRow painted any `error` red
// (.err) under a green "done" card -- the same look as a failed drive --
// and the machine drawer showed nothing at all. Same pattern as #927
// (captureWarningHtml): done + error = orange `.notice.warn`, never red.
// Runs the shipped console.js in a vm, not a copy of the functions.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

const stubNode = () => ({value: '', innerHTML: '', textContent: '', dataset: {}, style: {removeProperty() {}},
  classList: {add() {}, remove() {}, toggle() {}}, addEventListener() {}, removeEventListener() {},
  querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {}, focus() {}});

function setup() {
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise,
    encodeURIComponent, decodeURIComponent,
    document: {
      hidden: false, querySelectorAll: () => [], querySelector: stubNode, getElementById: stubNode,
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); },
        get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {},
    },
    window: {addEventListener() {}, matchMedia: () => ({matches: false}), open() {}},
    localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: x => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async () => ({status: 200, ok: true, headers: {get: () => null}, json: async () => ({})}),
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'progress.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'console.js'), 'utf8'), ctx);
  const run = s => vm.runInContext(s, ctx);
  run('ME={username:"u",role:"admin",idle_seconds:300,capabilities:{}}; toast=()=>{};');
  return run;
}

const MAC = 'b4:2e:99:07:1a:c4';
const WARNING = 'שם המחשב לא נכתב: registry edit <b>failed</b>';
const member = (over) => ({mac: MAC, hostname: 'LAB1-05', state: 'done', done: true,
  bytes_written: 1, bytes_total: 1, disks: [], drivers: null, ...over});

test('a done member with a hostname warning is orange, not red, and says why', () => {
  const run = setup();
  const html = run(`memberRow(${JSON.stringify(member({error: WARNING}))}, {})`);
  assert.match(html, /class="member done"/, 'the restore itself completed');
  assert.match(html, /notice warn/, 'done + error = warning');
  assert.match(html, /שם המחשב לא נכתב: registry edit &lt;b&gt;failed&lt;\/b&gt;/, 'escaped reason shown');
  assert.doesNotMatch(html, /class="err"/, 'not the red failed-drive look');
});

test('a failed member keeps the red error -- the fix does not soften failures', () => {
  const run = setup();
  const html = run(`memberRow(${JSON.stringify(member({state: 'failed', done: false, error: 'sda: fsync errno=5'}))}, {})`);
  assert.match(html, /class="err">sda: fsync errno=5/);
  assert.doesNotMatch(html, /notice warn/);
});

test('a clean done member shows no warning box at all', () => {
  const run = setup();
  const html = run(`memberRow(${JSON.stringify(member({}))}, {})`);
  assert.doesNotMatch(html, /notice warn/);
  assert.doesNotMatch(html, /class="err"/);
});

test('the machine drawer shows the last restore warning of this MAC', () => {
  const run = setup();
  run(`OVERVIEW = {session: {members: [${JSON.stringify(member({error: WARNING}))}]}}`);
  const html = run(`machineRestoreWarningHtml(${JSON.stringify(MAC)})`);
  assert.match(html, /notice warn/);
  assert.match(html, /שם המחשב לא נכתב/);
  assert.equal(run(`machineRestoreWarningHtml("00:00:00:00:00:01")`), '', 'another machine: nothing');
  run(`OVERVIEW = {session: {members: [${JSON.stringify(member({state: 'failed', done: false, error: 'x'}))}]}}`);
  assert.equal(run(`machineRestoreWarningHtml(${JSON.stringify(MAC)})`), '', 'a failure is not a warning');
  run('OVERVIEW = {session: null}');
  assert.equal(run(`machineRestoreWarningHtml(${JSON.stringify(MAC)})`), '', 'no session: nothing');
  assert.match(run('machineDrawerHtml.toString()'), /machineRestoreWarningHtml/, 'wired into the drawer (#954 wave 3)');
});

test('index.html was bumped so the browser does not run the old console.js', () => {
  const page = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const versions = new Set([...page.matchAll(/\?v=([0-9.]+)/g)].map((m) => m[1]));
  assert.equal(versions.size, 1, [...versions].join(','));
  assert.ok([...versions][0] >= '6.6', `?v=${[...versions][0]} — #856 ships as 6.6`);
});
