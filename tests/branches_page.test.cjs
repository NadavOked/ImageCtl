// #655 v1 — לשונית "סניפים" בקונסולת הראשי: כרטיס לכל שרת משני עם מצב
// חיבור (כפי שהראשי מדד מול המשני), המכונות של המשני, העברות אימג' עם
// התקדמות, וכפתור מוניטור שפותח את monitor.html **דרך המשני** (‏?node=).
// הבדיקה מריצה את console.js + branches.js המשוגרים ב-vm עם fetch מזויף;
// ו-monitor.js עם WebSocket מזויף — כדי לראות לאיזה נתיב הוא מתחבר.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

const stubNode = () => ({value: '', innerHTML: '', textContent: '', dataset: {}, style: {removeProperty() {}},
  classList: {add() {}, remove() {}, toggle() {}}, addEventListener() {}, removeEventListener() {},
  querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {}, focus() {}});

function setup(fixtures) {
  const opened = [];
  const toasts = [];
  const calls = [];
  const registry = new Map();
  const node = (sel) => {
    if (!registry.has(sel)) registry.set(sel, stubNode());
    return registry.get(sel);
  };
  const listeners = {};
  registry.set('#login-form', {...stubNode(), addEventListener(type, fn) { listeners[type] = fn; }});
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise,
    encodeURIComponent, decodeURIComponent,
    document: {
      hidden: false, querySelectorAll: () => [], querySelector: node, getElementById: node,
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); },
        get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {},
    },
    window: {addEventListener() {}, matchMedia: () => ({matches: false}), open: (...a) => opened.push(a)},
    localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: x => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async (url, options = {}) => {
      calls.push({url, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null});
      const key = url.replace('/api/console', '').split('?')[0];
      if (options.method === 'POST' && key === '/login') return {status: 200, ok: true, headers: {get: () => null}, json: async () => ({username: 'noc', role: 'admin', idle_seconds: 300})};
      if (options.method && options.method !== 'GET') return {status: 200, ok: true, headers: {get: () => null}, json: async () => ({id: 't1'})};
      if (fixtures[key] instanceof Error) return {status: 500, ok: false, headers: {get: () => null}, json: async () => ({detail: fixtures[key].message})};
      return {status: 200, ok: true, headers: {get: () => null}, json: async () => fixtures[key] || {}};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'progress.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'console.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'branches.js'), 'utf8'), ctx);
  const run = s => vm.runInContext(s, ctx);
  run('ME={username:"admin",role:"admin",idle_seconds:300,capabilities:{interbranch_transfer:true}}; toast=(m)=>__toasts.push(m); current="branches"; currentTab=0;');
  ctx.__toasts = toasts;
  return {ctx, run, opened, toasts, calls, node, listeners};
}

const NODE = 'n0de';
const base = {
  '/storage-nodes': [{id: NODE, label: 'חיפה', base_url: 'https://10.20.0.30:8443/api/interserver/v1', group_id: null, group_label: null, disabled_at: null}],
  '/storage-transfers': [
    {id: 't0', node_id: NODE, image_id: 'img_7f3a91', image_name: 'Office 365', state: 'sending', bytes_sent: 3 * 1024 ** 3, bytes_total: 12 * 1024 ** 3, error: null, created_at: '2026-09-16T10:00:00+00:00'},
    {id: 't9', node_id: NODE, image_id: 'img_2c8e04', image_name: 'Old', state: 'failed', bytes_sent: 0, bytes_total: 5, error: 'אימות נכשל: p1 אינו תואם ל-sha256', created_at: '2026-09-15T10:00:00+00:00'},
  ],
  '/images': [{id: 'img_7f3a91', name: 'Office 365', folder: 'Windows', total_compressed_bytes: 12 * 1024 ** 3}],
  [`/storage-nodes/${NODE}/machines`]: {connected: true, error: null, node_id: 'sn_abc', machines: [
    {mac: 'aa:bb:cc:dd:ee:01', name: 'Builder-H', role: 'build', ip: '10.20.0.77', online: true},
    {mac: 'aa:bb:cc:dd:ee:02', name: 'Cloner-H', role: 'cloner', ip: null, online: false},
  ]},
};

test('the branches page has "סניפים" as its first tab and the registry second', () => {
  const {run} = setup(base);
  assert.equal(JSON.stringify(run('pages.branches.tabs')), JSON.stringify(['סניפים', 'מרשם']));
  assert.match(run('tabRender("branches", 0)'), /id="branch-cards"/);
  assert.match(run('tabRender("branches", 1)'), /id="branches-body"/);
  assert.equal(run('pageAllowed("branches")'), true);
  run('ME.capabilities.interbranch_transfer=false');
  assert.equal(run('pageAllowed("branches")'), false, 'no secondaries (or a secondary server): no tab');
});

test('a card per secondary: connection state from the server, its machines, transfers with progress', async () => {
  const {run, node} = setup(base);
  await run('loadBranchCards()');
  const cards = node('#branch-cards').innerHTML;
  assert.match(cards, /חיפה/);
  assert.match(cards, /10\.20\.0\.30:8443/);
  assert.match(cards, /data-branch-transfer="n0de"/);
  const status = node(`#branch-status-${NODE}`);
  assert.equal(status.className, 'status ok');
  assert.match(status.innerHTML, /מחובר/);
  const machines = node(`#branch-machines-${NODE}`).innerHTML;
  assert.match(machines, /Builder-H/);
  assert.match(machines, /10\.20\.0\.77/);
  assert.match(machines, /Cloner-H/);
  assert.match(machines, /class="status ok"><i><\/i>מחובר/);
  assert.match(machines, /class="status"><i><\/i>לא מחובר/);
  assert.match(machines, /data-branch-monitor="aa:bb:cc:dd:ee:02"[^>]*disabled/, 'offline machine: monitor disabled');
  const transfers = node(`#branch-transfers-${NODE}`).innerHTML;
  assert.match(transfers, /Office 365/);
  assert.match(transfers, /שולח/);
  assert.match(transfers, /width:25%/);
  assert.match(transfers, /3\.0 GB מתוך 12\.0 GB/);
  assert.match(transfers, /נכשל/);
  assert.match(transfers, /אינו תואם ל-sha256/);
});

test('an unreachable secondary is shown as disconnected with the reason, not as an empty list', async () => {
  const fixtures = {...base, [`/storage-nodes/${NODE}/machines`]: {connected: false, error: 'החיבור נסגר לפני סוף הכותרות', machines: []}};
  const {run, node} = setup(fixtures);
  await run('loadBranchCards()');
  assert.equal(node(`#branch-status-${NODE}`).className, 'status err');
  assert.match(node(`#branch-status-${NODE}`).innerHTML, /לא מחובר/);
  assert.match(node(`#branch-machines-${NODE}`).innerHTML, /לא ענה: החיבור נסגר לפני סוף הכותרות/);
  assert.doesNotMatch(node(`#branch-machines-${NODE}`).innerHTML, /אין מחשבי/);
});

test('the monitor button opens monitor.html through the secondary (node= in the URL)', () => {
  const {run, opened} = setup(base);
  run(`branchMonitor("${NODE}", "aa:bb:cc:dd:ee:01", "Builder-H")`);
  assert.equal(opened.length, 1);
  assert.match(opened[0][0], /^monitor\.html\?node=n0de&mac=aa%3Abb%3Acc%3Add%3Aee%3A01&name=Builder-H$/);
  run('ME.role="deploy"');
  run(`branchMonitor("${NODE}", "aa:bb:cc:dd:ee:01", "Builder-H")`);
  assert.equal(opened.length, 1, 'deploy: nothing opens');
});

test('monitor.js connects to the remote path when node= is present, and to the local one otherwise', () => {
  const urls = [];
  const mk = (search) => {
    const el = () => ({addEventListener() {}, getContext: () => ({fillRect() {}, putImageData() {}, createImageData: () => ({data: new Uint8ClampedArray(4)})}),
      textContent: '', className: '', style: {}, width: 0, height: 0, onclick: null, value: ''});
    const ctx = vm.createContext({
      console, URLSearchParams, Uint8Array, Uint8ClampedArray, DataView, ArrayBuffer, Math, Number, JSON, Promise, Date, String,
      setTimeout: () => 1, clearTimeout() {},
      location: {search, protocol: 'http:', host: '10.10.10.8:8080'},
      document: {getElementById: el, addEventListener() {}, removeEventListener() {}, title: ''},
      window: {addEventListener() {}},
      WebSocket: class { constructor(url) { urls.push(url); this.readyState = 0; } close() {} send() {} static get OPEN() { return 1; } },
    });
    vm.runInContext(fs.readFileSync(path.join(root, 'monitor.js'), 'utf8'), ctx);
  };
  mk('?node=n0de&mac=aa%3Abb%3Acc%3Add%3Aee%3A01&name=Builder-H');
  mk('?mac=aa%3Abb%3Acc%3Add%3Aee%3A01&name=Builder');
  assert.equal(JSON.stringify(urls), JSON.stringify([
    'ws://10.10.10.8:8080/api/console/storage-nodes/n0de/monitor/aa%3Abb%3Acc%3Add%3Aee%3A01',
    'ws://10.10.10.8:8080/api/console/monitor/aa%3Abb%3Acc%3Add%3Aee%3A01',
  ]));
});

test('after login the console holds the /me capabilities, so the branches tab is reachable without a reload', async () => {
  const fixtures = {...base, '/me': {username: 'noc', role: 'admin', idle_seconds: 300, capabilities: {interbranch_transfer: true, enroll_secondary: true, open_local_pairing: false}},
    '/overview': {session: null, pulls: [], room: null, machines: 0, images: 0, storage: null}};
  const {run, listeners, ctx} = setup(fixtures);
  run('ME=null; showApp=async()=>{};');
  await listeners.submit({preventDefault() {}});
  assert.equal(run('ME && ME.capabilities && ME.capabilities.interbranch_transfer'), true);
  assert.equal(run('pageAllowed("branches")'), true);
});
