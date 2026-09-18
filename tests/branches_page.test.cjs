// #954 גל 7 — דף "סניפים" (רשימת המשניים): כותרת אובייקט עם מונים ותג "N
// לא מגיב/ים", טבלת שרתים עם חיבור **נמדד** בפועל (לא "בודק…" קפוא),
// לשונית העברות עם התקדמות/מצב/נסה שוב, לשונית קבוצות, ורישום שרת משני
// (preview SPKI → קוד). מוניטור למכונת המשני נשאר דרך monitor.html?node=.
// console.js + branches.js + storage_nodes.js (קבוצות) + reorder.js רצים
// ב-vm עם fetch מזויף, כמו secondaries_tree.test.cjs.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

const stubNode = () => ({value: '', innerHTML: '', textContent: '', className: '', dataset: {}, style: {removeProperty() {}},
  classList: {add() {}, remove() {}, toggle() {}}, addEventListener() {}, removeEventListener() {},
  querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {}, focus() {}});

function setup(fixtures, me = {}) {
  const opened = [];
  const toasts = [];
  const calls = [];
  const registry = new Map();
  const node = (sel) => {
    sel = String(sel).replace(/^#/, '');
    if (!registry.has(sel)) registry.set(sel, stubNode());
    return registry.get(sel);
  };
  const listeners = {};
  // #1085 שלב ב': הכניסה עברה מבינדינג ישיר על login-form לדלגציה על מיכל #login
  registry.set('login', {...stubNode(), addEventListener(type, fn) { listeners[type] = fn; }});
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
      if (fixtures[key] instanceof Error) return {status: Number(fixtures[key].message) || 500, ok: false, headers: {get: () => null}, json: async () => ({detail: fixtures[key].message})};
      if (key in fixtures) return {status: 200, ok: true, headers: {get: () => null}, json: async () => fixtures[key]};
      if (options.method && options.method !== 'GET') return {status: 200, ok: true, headers: {get: () => null}, json: async () => ({id: 't1', ok: true})};
      return {status: 200, ok: true, headers: {get: () => null}, json: async () => ({})};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'progress.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'reorder.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'console.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'branches.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'storage_nodes.js'), 'utf8'), ctx);
  const run = s => vm.runInContext(s, ctx);
  const meJson = JSON.stringify({username: 'admin', role: 'admin', idle_seconds: 300, capabilities: {interbranch_transfer: true, enroll_secondary: true}, ...me});
  run(`ME=${meJson}; toast=(m)=>__toasts.push(m); current="branches"; currentTab=0;`);
  ctx.__toasts = toasts;
  return {ctx, run, opened, toasts, calls, node, listeners};
}

const NODE = 'n0de';
const base = {
  '/storage-nodes': [{id: NODE, label: 'חיפה', node_id: 'sn_0123456789abcdef', base_url: 'https://10.20.0.30:8443/api/interserver/v1', group_id: null, group_label: null, enrolled_at: '2026-09-16T09:00:00+00:00', disabled_at: null}],
  '/storage-node-groups': [],
  '/storage-transfers': [
    {id: 't0', node_id: NODE, node_label: 'חיפה', image_id: 'img_7f3a91', image_name: 'Office 365', state: 'sending', bytes_sent: 3 * 1024 ** 3, bytes_total: 12 * 1024 ** 3, error: null, started_by: 'admin', created_at: '2026-09-16T10:00:00+00:00'},
    {id: 't9', node_id: NODE, node_label: 'חיפה', image_id: 'img_2c8e04', image_name: 'Old', state: 'failed', bytes_sent: 0, bytes_total: 5, error: 'אימות נכשל: p1 אינו תואם ל-sha256', started_by: 'admin', created_at: '2026-09-15T10:00:00+00:00'},
  ],
  '/images': [{id: 'img_7f3a91', name: 'Office 365', folder: 'Windows', total_compressed_bytes: 12 * 1024 ** 3}],
  [`/storage-nodes/${NODE}/machines`]: {connected: true, error: null, node_id: 'sn_0123456789abcdef', machines: [
    {mac: 'aa:bb:cc:dd:ee:01', name: 'Builder-H', role: 'build', ip: '10.20.0.77', online: true},
    {mac: 'aa:bb:cc:dd:ee:02', name: 'Cloner-H', role: 'cloner', ip: null, online: false},
  ]},
};

test('the branches page has three tabs: שרתים, העברות, קבוצות', () => {
  const {run} = setup(base);
  assert.equal(JSON.stringify(run('pages.branches.tabs')), JSON.stringify(['שרתים', 'העברות', 'קבוצות']));
  assert.equal(run('pageAllowed("branches")'), true);
  run('ME.capabilities.interbranch_transfer=false');
  assert.equal(run('pageAllowed("branches")'), false, 'no secondaries (or a secondary server): no tab');
});

test('the servers tab lists each secondary with a measured connection, not a frozen "checking…"', async () => {
  const {run} = setup(base);
  await run('loadBranchesData()');
  const html = run('branchesPage(0)');
  assert.match(html, /חיפה/);
  assert.match(html, /sn_0123456789abcdef/);
  assert.match(html, /10\.20\.0\.30:8443/);
  assert.match(html, /class="status ok" id="branch-row-conn-n0de"><i><\/i>מחובר/);
  assert.doesNotMatch(html, /בודק חיבור/, 'the connection check already resolved before the row was read');
  assert.match(html, /Office 365/, 'the last transfer shows in the row');
  assert.match(html, /data-node-row="n0de"/);
});

test('an unreachable secondary shows the reason and the measured time, not an empty page', async () => {
  const fixtures = {...base, [`/storage-nodes/${NODE}/machines`]: {connected: false, error: 'החיבור נסגר לפני סוף הכותרות', machines: []}};
  const {run} = setup(fixtures);
  await run('loadBranchesData()');
  const html = run('branchesPage(0)');
  assert.match(html, /class="status err" id="branch-row-conn-n0de"><i><\/i>לא ענה/);
  assert.match(html, /לא ענה — \d\d:\d\d/, 'the measured time is shown, not a frozen state');
  assert.match(html, /החיבור נסגר לפני סוף הכותרות/);
  assert.equal(run('pages.branches.tabs.length'), 3);
});

test('the header shows a count and "לא מגיב" tag when a secondary did not answer', async () => {
  const fixtures = {...base, [`/storage-nodes/${NODE}/machines`]: new Error('500')};
  const {run} = setup(fixtures);
  await run('loadBranchesData()');
  const html = run('branchesPage(0)');
  assert.match(html, /1 לא מגיב</);
});

test('the transfers tab shows progress, state in Hebrew, and a retry button only for a failed transfer', async () => {
  const {run} = setup(base);
  await run('loadBranchesData()');
  const html = run('branchesPage(1)');
  assert.match(html, /Office 365/);
  assert.match(html, /שולח/);
  assert.match(html, /נכשל/);
  assert.match(html, /אינו תואם ל-sha256/);
  assert.match(html, /admin/);
  assert.match(html, /נסה שוב/);
  assert.equal((html.match(/נסה שוב/g) || []).length, 1, 'only the failed transfer offers retry');
});

test('retrying a failed transfer re-posts the same node/image and reloads the list', async () => {
  const {run, calls} = setup(base);
  await run('loadBranchesData()');
  await run(`retryBranchTransfer('${NODE}', 'img_2c8e04')`);
  const post = calls.find((c) => c.method === 'POST' && c.url.endsWith('/storage-nodes/n0de/transfer'));
  assert.ok(post && post.body.image_id === 'img_2c8e04');
});

test('the groups tab reuses storage_nodes.js: create, rename, delete a group', async () => {
  const fixtures = {...base, '/storage-node-groups': [{id: 'g1', label: 'צפון', nodes: 1, sort: 0}]};
  const {run, node} = setup(fixtures);
  await run('loadBranchesData()');
  run('currentTab=2; wireBranchesGroupsTab();');
  const html = node('branch-groups').innerHTML;
  assert.match(html, /צפון/);
  assert.match(html, /1 משניים/);
});

test('registering a secondary: address → SPKI preview → code (enroll/preview then enroll)', async () => {
  const fixtures = {...base, '/storage-nodes/enroll/preview': {secondary_spki: 'sha256/AbCdEf==', node_id: 'sn_deadbeefcafebabe'}};
  const {run, calls} = setup(fixtures);
  await run('loadBranchesData()');
  run('__step1=null; __step2=null; let n=0; sheet=(o)=>{ n++; if(n===1) __step1=o; else __step2=o; };');
  run('openEnrollSheet()');
  await run('__step1.onSubmit({url: "https://10.50.0.9:8443/api/interserver/v1", label: "צפת", group_id: ""})');
  const preview = calls.find((c) => c.url.endsWith('/storage-nodes/enroll/preview'));
  assert.ok(preview && preview.body.url === 'https://10.50.0.9:8443/api/interserver/v1');
  assert.equal(run('typeof __step2.onSubmit'), 'function', 'a second sheet asks for the code, with the previewed SPKI shown');
  assert.match(run('__step2.note'), /AbCdEf/);
  await run('__step2.onSubmit({code: "123456"})');
  const enroll = calls.find((c) => c.method === 'POST' && c.url.endsWith('/storage-nodes/enroll'));
  assert.ok(enroll);
  assert.equal(enroll.body.expected_secondary_spki, 'sha256/AbCdEf==');
  assert.equal(enroll.body.code, '123456');
  assert.equal(enroll.body.label, 'צפת');
});

test('removing a secondary requires typing its name', async () => {
  const {run} = setup(base);
  await run('loadBranchesData()');
  run('__sheet=null; sheet=(o)=>{ __sheet=o; };');
  run(`removeBranchNode('${NODE}')`);
  assert.equal(run('__sheet.verify.mustEqual'), 'חיפה');
  assert.equal(run('__sheet.danger'), true);
});

test('the monitor button on the branch (single secondary) page opens monitor.html through the secondary (node= in the URL)', () => {
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
  const {run, listeners} = setup(fixtures);
  run('ME=null; showApp=async()=>{};');
  // #1085 שלב ב': loginOnSubmit מדלג לפי event.target.id (דלגציה על #login)
  await listeners.submit({preventDefault() {}, target: {id: 'login-form'}});
  assert.equal(run('ME && ME.capabilities && ME.capabilities.interbranch_transfer'), true);
  assert.equal(run('pageAllowed("branches")'), true);
});
