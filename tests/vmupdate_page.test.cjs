// #1334: the VM-update console UI (vmupdate.js) -- the global "VM רץ" strip, the
// view-only/take-control strip, the "גם X צופה ב-VM" presence strip, the mandatory
// save decision, the controller's approve/deny dialog, the image ⋯ item, and the
// paste-as-typing guard. Runs the shipped console.js + vmupdate.js, not copies.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');
const read = (f) => fs.readFileSync(path.join(root, f), 'utf8');

function setup({user = 'nadav', role = 'admin'} = {}) {
  const els = new Map();
  const node = (id) => {
    if (!els.has(id)) els.set(id, {id, value: '', innerHTML: '', textContent: '', dataset: {}, style: {removeProperty() {}},
      classList: {add() {}, remove() {}, toggle() {}, contains() { return false; }}, addEventListener() {}, removeEventListener() {},
      querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {}, focus() {}});
    return els.get(id);
  };
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise, String, Array, Object, Error,
    encodeURIComponent, decodeURIComponent,
    document: {hidden: false, querySelector: (s) => node(s), querySelectorAll: () => [], getElementById: node,
      createElement: () => node('tmp' + Math.random()), documentElement: {setAttribute() {}},
      addEventListener() {}, removeEventListener() {}},
    window: {addEventListener() {}, matchMedia: () => ({matches: false})},
    localStorage: {getItem: () => null, setItem() {}}, sessionStorage: {getItem: () => null, setItem() {}},
    CSS: {escape: (x) => x}, location: {protocol: 'https:', host: 'srv:8081'},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async () => ({status: 200, ok: true, headers: {get: () => null}, json: async () => ({})}),
  });
  vm.runInContext(read('progress.js'), ctx);
  vm.runInContext(read('console.js'), ctx);
  vm.runInContext(read('vmupdate.js'), ctx);
  const run = (s) => vm.runInContext(s, ctx);
  run(`ME={username:${JSON.stringify(user)},role:${JSON.stringify(role)},capabilities:{}};`);
  return {run, node};
}

const BASE = {state: 'running', image_id: 'img_7f3a91', image_name: 'Office 2024', image_version: 3, lineage: 'img_7f3a91',
  controller: 'nadav', controller_since: '2026-10-13T09:00:00+03:00', you_control: true, runtime_seconds: 1394,
  egress_interface: 'eno1', resources: {cpus: 4, memory_mib: 16384, accel: 'kvm'}, viewers: [], control_request: null};
const st = (o) => JSON.stringify({...BASE, ...o});

test('the global strip shows an active VM on every page except the VM page', () => {
  const {run} = setup();
  run('current = "home"');
  const html = run(`vmuGlobalHtml(${st({})})`);
  assert.match(html, /VM רץ:/);
  assert.match(html, /Office 2024/);
  assert.match(html, /vmuOpen\(\)/);
  assert.equal(run(`vmuGlobalHtml(${st({state: 'idle'})})`), '');
  assert.equal(run(`vmuGlobalHtml(${st({state: 'saved'})})`), '');
  assert.match(run(`vmuGlobalHtml(${st({state: 'stopped', expires_in_seconds: 3 * 86400})})`), /ממתין להחלטה.*בעוד 3 ימים/);
  run('current = "vmupdate"');
  assert.equal(run(`vmuGlobalHtml(${st({})})`), '');
});

test('a viewer who does not control sees a view-only strip, disabled input tools, and no power buttons', () => {
  const {run} = setup({user: 'dana'});
  const s = st({you_control: false});
  const strip = run(`vmuControlHtml(${s})`);
  assert.match(strip, /<b>nadav<\/b> עובד ב-VM מאז \d\d:\d\d — אתם בצפייה בלבד/);
  assert.match(strip, /vmuRequestControl\(\)/);
  const tool = run(`vmuToolHtml(${s})`);
  assert.match(tool, /disabled onclick="vmuCtrlAltDel\(\)"/);
  assert.match(tool, /disabled onclick="vmuPasteDialog\(\)"/);
  assert.doesNotMatch(tool, /vmuShutdown|vmuForceDialog/);
  // the controller has them, and no view-only strip
  assert.equal(run(`vmuControlHtml(${st({})})`), '');
  assert.match(run(`vmuToolHtml(${st({})})`), /vmuShutdown\(\)[\s\S]*vmuForceDialog\(\)/);
});

test('a pending request shows a countdown to the requester; a denial is said out loud', () => {
  const {run} = setup({user: 'dana'});
  const pending = run(`vmuControlHtml(${st({you_control: false, control_request: {by: 'dana', seconds_left: 42}})})`);
  assert.match(pending, /ממתין לאישור של nadav · 42 שנ'/);
  assert.doesNotMatch(pending, /vmuRequestControl/);
  const denied = run(`vmuControlHtml(${st({you_control: false, control_last: {by: 'dana', outcome: 'denied'}})})`);
  assert.match(denied, /הבקשה שלך נדחתה/);
});

test('the presence strip names the other viewers, address and time, with +N and a list', () => {
  const {run} = setup();
  const viewers = [
    {id: 'a', user: 'nadav', address: '10.20.0.15', since: '2026-10-13T09:00:00+03:00', controller: true, you: true},
    {id: 'b', user: 'dana', address: '10.20.0.31', since: '2026-10-13T09:20:00+03:00', controller: false, you: false},
    {id: 'c', user: 'yossi', address: '10.20.0.44', since: '2026-10-13T09:25:00+03:00', controller: false, you: false},
  ];
  const html = run(`vmuPresenceHtml(${st({viewers})})`);
  assert.match(html, /גם <b>dana<\/b> צופה ב-VM · מ-<span class="mono">10\.20\.0\.31<\/span> · מאז \d\d:\d\d/);
  assert.match(html, />\+1</);
  run('VMU.prs.pop = true');
  const pop = run(`vmuPresenceHtml(${st({viewers})})`);
  assert.match(pop, /צופים ב-VM · 3/);
  assert.match(pop, /nadav<\/span><span class="prs-you">אתה<\/span><span class="prs-you">שולט<\/span>/);
  // alone: no strip
  assert.equal(run(`vmuPresenceHtml(${st({viewers: [viewers[0]]})})`), '');
});

test('names and addresses are escaped', () => {
  const {run} = setup();
  const html = run(`vmuPresenceHtml(${st({viewers: [{id: 'x', user: '<img src=x onerror=alert(1)>', address: '"><b>', since: '', controller: false, you: false}]})})`);
  assert.doesNotMatch(html, /<img src=x/);
  assert.match(html, /&lt;img src=x/);
});

test('when the VM stops, the controller gets the save decision once -- locked, no X', () => {
  const {run, node} = setup();
  run('current = "vmupdate"');
  run('openModalFrame = () => {}');
  run(`VMU.st = ${st({state: 'stopped'})}; vmuDialogs()`);
  assert.equal(run('VMU.dlg'), 'save');
  assert.equal(node('modal').dataset.locked, '1');
  assert.doesNotMatch(node('sheet').innerHTML, /vmuCloseWin\(\)"[^>]*aria-label="סגור"|class="n-x"/);
  assert.match(node('sheet').innerHTML, /שמור כ-v4/);
  assert.equal(run('modalLocked()'), true);
  // closed by a decision, it does not pop again for the same stop
  run('vmuCloseWin(); vmuDialogs()');
  assert.equal(run('VMU.dlg'), '');
  // a viewer never gets it
  const viewer = setup({user: 'dana'});
  viewer.run('current = "vmupdate"; openModalFrame = () => {}');
  viewer.run(`VMU.st = ${st({state: 'stopped', you_control: false})}; vmuDialogs()`);
  assert.equal(viewer.run('VMU.dlg'), '');
});

test('the controller is asked to approve a take-control request, and the dialog closes when it is gone', () => {
  const {run, node} = setup();
  run('openModalFrame = () => {}; closeModal = () => {}');
  run(`VMU.st = ${st({control_request: {by: 'dana', seconds_left: 51}})}; vmuDialogs()`);
  assert.equal(run('VMU.dlg'), 'control');
  assert.match(node('sheet').innerHTML, /dana מבקש שליטה ב-VM/);
  assert.match(node('sheet').innerHTML, /vmuAnswer\(true\)[\s\S]*|vmuAnswer\(false\)/);
  run(`VMU.st = ${st({control_request: null})}; vmuDialogs()`);
  assert.equal(run('VMU.dlg'), '');
});

test('the image ⋯ item: Windows opens the start window; Linux and a drifted image are disabled with the reason', () => {
  const {run} = setup();
  const win = run(`vmuImageMenuItem({id: "img_7f3a91", os: "windows", last_scrub: null})`);
  assert.equal(win[0], 'עדכן ב-VM…');
  assert.match(win[1], /vmuStartDialog\('img_7f3a91'\)/);
  const linux = run(`vmuImageMenuItem({id: "img_2c8e04", os: "linux"})`);
  assert.equal(linux[2], 'disabled');
  assert.match(linux[0], /רק לאימג' Windows/);
  const drift = run(`vmuImageMenuItem({id: "img_2", os: "windows", last_scrub: {state: "drift"}})`);
  assert.equal(drift[2], 'disabled');
  assert.match(run(`rowMenuHtml([["x (רק לאימג' Windows)", "", "disabled"]])`), /aria-disabled="true" disabled/);
  // deploy users never see it
  assert.equal(setup({role: 'deploy'}).run(`vmuImageMenuItem({id: "a", os: "windows"})`), null);
});

test('paste is typing on a US layout: ASCII and Enter/Tab only', () => {
  const {run} = setup();
  assert.deepEqual(Array.from(run('vmuKeysyms("ab C\\n\\t1")')), [97, 98, 32, 67, 0xff0d, 0xff09, 49]);
  assert.equal(run('vmuKeysyms("שלום")'), null);
  assert.throws(() => run('vmuType("שלום")'), /רק תווים באנגלית/);
});

test('a stage card of the same step is updated in place, not rebuilt (the flicker fix)', () => {
  const {run} = setup();
  const prep = (done) => run(`vmuPrepCard(${st({state: 'preparing', progress: {stage: 'disk', done_bytes: done, total_bytes: 100e9, partitions: 3}})})`);
  assert.match(prep(10e9), /data-card="prep"/);
  assert.match(prep(10e9), /data-k="prepbar" style="width:10%"/);
  assert.match(prep(55e9), /data-k="prepbar" style="width:55%"/);
});
