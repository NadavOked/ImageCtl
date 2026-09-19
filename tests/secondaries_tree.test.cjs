// #936 — השרתים המשניים בעץ הניווט נבנים מ-GET /storage-nodes (כמו vCenter):
// צומת שרת לכל משני עם נקודת מצב (ירוק מחובר / אדום לא ענה / אפור מושבת),
// וארבעה ילדים — סקירה, מחשבים, אימג'ים, העברות — שמנתבים לדף `branch`
// (branches.js) עם המשני שנבחר. קליק = מציג, דאבל-קליק = פותח/סוגר (#931).
// console.js + branches.js רצים ב-vm עם fetch מזויף, כמו branches_page.test.cjs.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

const stubNode = () => ({value: '', innerHTML: '', textContent: '', className: '', attrs: {}, dataset: {}, style: {removeProperty() {}},
  classList: {add() {}, remove() {}, toggle() {}, contains() { return false; }}, addEventListener() {}, removeEventListener() {},
  querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute(k, v) { this.attrs[k] = String(v); },
  removeAttribute(k) { delete this.attrs[k]; }, hasAttribute(k) { return k in this.attrs; }, focus() {}});

function setup(fixtures, me = {}, slow = null) {
  const calls = [];
  const toasts = [];
  const registry = new Map();
  // getElementById("x") ו-querySelector("#x") הם אותו אלמנט.
  // אלמנט בתוך הדף של המשני קיים רק אם ה-HTML הנוכחי של #branch-view מכיל
  // אותו — כמו בדפדפן, שם querySelector על id מלשונית שכבר הוחלפה מחזיר null.
  const node = (sel) => {
    sel = sel.replace(/^#/, '');
    if (sel.startsWith('branch-') && sel !== 'branch-view' && registry.has('branch-view')
        && !registry.get('branch-view').innerHTML.includes(`id="${sel}"`)) return null;
    if (!registry.has(sel)) registry.set(sel, stubNode());
    return registry.get(sel);
  };
  registry.set('#login-form', {...stubNode(), addEventListener() {}});
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise,
    encodeURIComponent, decodeURIComponent,
    document: {
      hidden: false, querySelectorAll: () => [], querySelector: node, getElementById: node,
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); },
        get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {},
    },
    window: {addEventListener() {}, matchMedia: () => ({matches: false}), open() {}},
    localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: x => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async (url, options = {}) => {
      calls.push({url, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null});
      const key = url.replace('/api/console', '').split('?')[0];
      if (slow && key.includes(slow)) await new Promise((r) => setTimeout(r, 30));   // host timer, not the stubbed one
      if (fixtures[key] instanceof Error) return {status: Number(fixtures[key].message) || 500, ok: false, headers: {get: () => null}, json: async () => ({detail: 'אסור'})};
      if (options.method && options.method !== 'GET') return {status: 200, ok: true, headers: {get: () => null}, json: async () => ({ok: true})};
      return {status: 200, ok: true, headers: {get: () => null}, json: async () => fixtures[key] || {}};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'progress.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'console.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'branches.js'), 'utf8'), ctx);
  const run = s => vm.runInContext(s, ctx);
  ctx.__toasts = toasts;
  const meJson = JSON.stringify({username: 'admin', role: 'admin', idle_seconds: 300, server_name: 'imagectl-main',
    capabilities: {interbranch_transfer: true, enroll_secondary: true}, ...me});
  run(`ME=${meJson}; toast=(m)=>__toasts.push(m); current="home"; currentTab=0;`);
  return {run, calls, toasts, node};
}

const NODE = 'n0de';
const base = {
  '/storage-nodes': [
    {id: NODE, label: 'חיפה', base_url: 'https://10.30.0.8:8443/api/interserver/v1', group_id: null, group_label: null, enrolled_at: '2026-09-16T09:00:00+00:00', disabled_at: null},
    {id: 'off1', label: 'באר שבע', base_url: 'https://10.40.0.8:8443/api/interserver/v1', group_id: null, group_label: 'דרום', enrolled_at: '2026-09-10T09:00:00+00:00', disabled_at: '2026-09-12T09:00:00+00:00'},
  ],
  [`/storage-nodes/${NODE}/machines`]: {connected: true, error: null, node_id: 'sn_0123456789abcdef', machines: [
    {mac: 'aa:bb:cc:dd:ee:01', name: 'Builder-H', role: 'build', ip: '10.30.0.77', online: true},
    {mac: 'aa:bb:cc:dd:ee:02', name: 'Cloner-H', role: 'cloner', ip: null, online: false},
  ]},
  '/storage-transfers': [
    {id: 't1', node_id: NODE, image_id: 'img_7f3a91', image_name: 'Office 365', state: 'done', bytes_sent: 5, bytes_total: 5, error: null, created_at: '2026-09-16T10:00:00+00:00', updated_at: '2026-09-16T10:20:00+00:00'},
    {id: 't2', node_id: NODE, image_id: 'img_7f3a91', image_name: 'Office 365', state: 'done', bytes_sent: 5, bytes_total: 5, error: null, created_at: '2026-09-15T10:00:00+00:00', updated_at: '2026-09-15T10:20:00+00:00'},
    {id: 't3', node_id: NODE, image_id: 'img_2c8e04', image_name: 'Old', state: 'failed', bytes_sent: 0, bytes_total: 5, error: 'sha256 לא תואם', created_at: '2026-09-14T10:00:00+00:00'},
  ],
  '/images': [{id: 'img_7f3a91', name: 'Office 365', folder: 'Windows', total_compressed_bytes: 12 * 1024 ** 3}],
  [`/storage-nodes/${NODE}/images`]: {connected: true, error: null, images: [
    {id: 'img_c0ffee', name: 'Haifa Local', size_bytes: 40, created_at: '2026-09-18T08:00:00+00:00', sha256: {}},
    {id: 'img_7f3a91', name: 'Office 365', size_bytes: 40, created_at: '2026-09-16T10:00:00+00:00', sha256: {}},
  ]},
};

test('a server node per secondary from /storage-nodes, with four children; click shows, dblclick toggles', async () => {
  const {run, node, calls} = setup(base);
  await run('populateSidebarSecondaries()');
  const html = node('#secondaryServers').innerHTML;
  assert.ok(calls.some((c) => c.url.endsWith('/storage-nodes')));
  assert.match(html, /class="inventory-node server-node" data-secondary="n0de"/);
  assert.match(html, /<strong class="server-name">חיפה<\/strong>/);
  assert.match(html, /<strong class="server-name">באר שבע<\/strong>/, 'a disabled secondary is still in the tree');
  assert.doesNotMatch(html, /יוגדר בהמשך|תל אביב/);
  // #931: the row's single click navigates (overview), dblclick toggles the group, the arrow toggles alone.
  assert.match(html, /data-secondary="n0de"[^>]*onclick="openBranchView\('n0de',0,this\)"[^>]*ondblclick="toggleInventoryGroup\(this,'srv-n0de'\)"/);
  assert.match(html, /<span class="tree-arrow" data-open="false" onclick="event\.stopPropagation\(\);toggleInventoryGroup\(this\.closest\('\.inventory-node'\),'srv-n0de'\)">▸<\/span>/);
  assert.match(html, /<div id="srv-n0de" class="inventory-children" role="group" hidden>/, 'closed by default');
  for (const [i, [view, label]] of [['overview', 'סקירה'], ['machines', 'מחשבים'], ['images', "אימג&#39;ים"], ['transfers', 'העברות']].entries()) {
    const re = new RegExp(`data-branch-view="${view}" role="treeitem" tabindex="0" onclick="openBranchView\\('n0de',${i},this\\)">.*?<span>${label}</span>`);
    assert.match(html, re, `child ${view}`);
  }
  assert.match(html, /role="treeitem" tabindex="0" aria-expanded="false" onclick="openBranchView\('n0de'/, 'server node is a focusable treeitem');
});

test('the status dot is measured against the secondary: green when it answered, red with the reason when not, grey when disabled', async () => {
  const {run, node, calls} = setup(base);
  await run('populateSidebarSecondaries()');
  const dot = node('[data-secondary-status="n0de"]');
  assert.equal(dot.className, 'status ok');
  assert.equal(dot.attrs.title, 'מחובר');
  assert.ok(!calls.some((c) => c.url.includes('/storage-nodes/off1/machines')), 'a disabled secondary is not asked');
  assert.match(node('#secondaryServers').innerHTML, /data-secondary-status="off1" title="מושבת"/);

  const down = setup({...base, [`/storage-nodes/${NODE}/machines`]: {connected: false, error: 'החיבור נסגר לפני סוף הכותרות', machines: []}});
  await down.run('populateSidebarSecondaries()');
  assert.match(down.node('#secondaryServers').innerHTML, /חיפה/, 'an unreachable secondary does not vanish');
  const redDot = down.node('[data-secondary-status="n0de"]');
  assert.equal(redDot.className, 'status err');
  assert.equal(redDot.attrs.title, 'לא מחובר: החיבור נסגר לפני סוף הכותרות');
  assert.equal(redDot.attrs['aria-label'], redDot.attrs.title);
  assert.equal(down.toasts.length, 0, 'not a red error toast');
});

test('without the capability (deploy, or a secondary server) no node is built and /storage-nodes is not called; a 403 is not a red error', async () => {
  const deploy = setup(base, {role: 'deploy', capabilities: {interbranch_transfer: false, enroll_secondary: false}});
  await deploy.run('populateSidebarSecondaries()');
  assert.equal(deploy.node('#secondaryServers').innerHTML, '');
  assert.ok(!deploy.calls.some((c) => c.url.includes('/storage-nodes')));
  assert.equal(deploy.run('pageAllowed("branch")'), false);

  const forbidden = setup({...base, '/storage-nodes': new Error('403')});
  await forbidden.run('populateSidebarSecondaries()');
  assert.equal(forbidden.node('#secondaryServers').innerHTML, '');
  assert.equal(forbidden.toasts.length, 0);

  const broken = setup({...base, '/storage-nodes': new Error('500')});
  await assert.rejects(broken.run('populateSidebarSecondaries()'), /אסור/, 'a real failure still surfaces');
});

test('the primary server node takes its name from /me (server_name), and renameServer saves it via POST /settings', async () => {
  const {run, node, calls} = setup(base);
  run('applyServerName()');
  assert.equal(node('#primaryServerName').textContent, 'imagectl-main');
  run('openModalContent=(t,h,ok,fn)=>{__save=fn;}; closeModal=()=>{}; renameServer(document.getElementById("primaryServerName"))');
  node('#serverNameInput').value = '  שרת ראשי — קמפוס  ';
  await run('__save()');
  const save = calls.find((c) => c.method === 'POST' && c.url.endsWith('/settings'));
  assert.ok(save, 'the name was saved on the server via POST /settings, not only in the DOM');
  assert.equal(JSON.stringify(save.body), JSON.stringify({server_name: 'שרת ראשי — קמפוס'}));
  assert.equal(node('#primaryServerName').textContent, 'שרת ראשי — קמפוס');
  assert.equal(run('ME.server_name'), 'שרת ראשי — קמפוס');
  assert.doesNotMatch(fs.readFileSync(path.join(root, 'console.js'), 'utf8'), /imagectl-server-names/, 'no localStorage-only rename');
});

test('openBranchView routes to the branch page with the chosen secondary and tab', async () => {
  const {run} = setup(base);
  assert.equal(run('pageAllowed("branch")'), true);
  assert.equal(JSON.stringify(run('pages.branch.tabs')), JSON.stringify(['סקירה', 'מחשבים', "אימג'ים", 'העברות']));
  run('closeSidebar=()=>{}; openBranchView("n0de", 2, null)');
  assert.equal(run('current'), 'branch');
  assert.equal(run('currentTab'), 2);
  assert.equal(run('BRANCH_NODE'), 'n0de');
  assert.match(run('tabRender("branch", 1)'), /id="branch-view" class="stack" data-view="machines"/);
});

test('the branch page tabs reuse branches.js: overview measures the connection, machines lists them with monitor, images = secondary library + pull + completed transfers, transfers = the log', async () => {
  const {run, node} = setup(base);
  run('BRANCH_NODE="n0de"; current="branch"');
  await run('loadBranchView("overview")');
  let html = node('#branch-view').innerHTML;
  assert.match(html, /חיפה/);
  assert.match(html, /10\.30\.0\.8:8443/);
  assert.match(html, /2026-09-16 09:00:00/);
  assert.equal(node(`#branch-status-${NODE}`).className, 'status ok');
  assert.equal(node('#branch-node-id').textContent, 'sn_0123456789abcdef');
  assert.equal(node('#branch-online-count').textContent, '1 מתוך 2');
  assert.equal(node('[data-secondary-status="n0de"]').className, 'status ok', 'the tree dot follows the measurement');

  await run('loadBranchView("machines")');
  const machines = node(`#branch-machines-${NODE}`).innerHTML;
  assert.match(machines, /Builder-H/);
  assert.match(machines, /data-branch-monitor="aa:bb:cc:dd:ee:01"/);
  assert.match(machines, /data-branch-monitor="aa:bb:cc:dd:ee:02"[^>]*disabled/);

  await run('loadBranchView("images")');
  html = node('#branch-view').innerHTML;
  assert.match(html, /id="branch-view-transfer"/);
  assert.match(html, /ספריית האימג'ים של המשני/);
  assert.match(html, /Haifa Local/);
  assert.match(html, /העבר לראשי/);
  assert.match(html, /קיים בראשי/);
  assert.equal((html.match(/Office 365/g) || []).length, 2, 'push history once + library row once');
  assert.doesNotMatch(html, /\bOld\b/, 'a failed transfer is not an image the secondary has');
  assert.doesNotMatch(html, /בודק חיבור/, 'no unmeasured connection state on this tab');

  await run('loadBranchView("transfers")');
  const log = node(`#branch-transfers-${NODE}`).innerHTML;
  assert.match(log, /Office 365/);
  assert.match(log, /נכשל/);
  assert.match(log, /sha256 לא תואם/);
});

test('a disconnected secondary on the overview shows the reason, not an empty page', async () => {
  const {run, node} = setup({...base, [`/storage-nodes/${NODE}/machines`]: {connected: false, error: 'timed out', machines: []}});
  run('BRANCH_NODE="n0de"; current="branch"');
  await run('loadBranchView("overview")');
  assert.equal(node(`#branch-status-${NODE}`).className, 'status err');
  assert.match(node('#branch-connect-note').innerHTML, /לא ענה: timed out/);
  assert.equal(node('[data-secondary-status="n0de"]').className, 'status err');
});

test('a stale load (tab switched during the await) does not write into a DOM that is gone — measured live: "Cannot set properties of null"', async () => {
  // the secondary answers /machines slowly, so the overview's write lands after the transfers tab rendered.
  const {run, node, toasts} = setup(base, {}, '/machines');
  run('closeSidebar=()=>{}');
  // openBranchView renders the page (tab 0 → loadBranchView("overview")) and then the tab
  // (→ loadBranchView("transfers")); both are in flight, and the overview must give up.
  run('openBranchView("n0de", 3, null)');
  await new Promise((r) => setTimeout(r, 120));
  assert.equal(toasts.length, 0, 'no error toast: ' + JSON.stringify(toasts));
  assert.equal(run('currentTab'), 3);
  assert.doesNotMatch(node('#branch-view').innerHTML, /מחשבים מחוברים/, 'the overview did not overwrite the transfers tab');
  assert.match(node('#branch-view').innerHTML, /branch-transfers-n0de/);
});

test('"התחל העברה" from the images tab lands on the transfers tab (which polls) — measured live: the images tab re-rendered "עוד לא הועבר" while the transfer was running', async () => {
  const {run, node, calls} = setup(base);
  run('BRANCH_NODE="n0de"; current="branch"; currentTab=2; closeSidebar=()=>{}; __submit=null; sheet=(o)=>{ __submit=o.onSubmit; }');
  await run('loadBranchView("images")');
  run('document.querySelector("#branch-view-transfer").onclick()');
  assert.equal(run('typeof __submit'), 'function');
  await run('__submit({image_id: "img_7f3a91"})');
  const post = calls.find((c) => c.method === 'POST' && c.url.endsWith('/storage-nodes/n0de/transfer'));
  assert.ok(post && post.body.image_id === 'img_7f3a91', 'the transfer was started');
  assert.equal(run('currentTab'), 3, 'the transfers tab is now active');
  await new Promise((r) => setTimeout(r, 20));
  assert.match(node('#branch-view').innerHTML, /branch-transfers-n0de/, 'the transfers log is on screen, not the images list');
});

test('"העבר לראשי" from the secondary library starts a pull and lands on the transfers tab', async () => {
  const {run, node, calls} = setup(base);
  run('BRANCH_NODE="n0de"; current="branch"; currentTab=2; closeSidebar=()=>{};');
  await run('loadBranchView("images")');
  await run('startPullFromSecondary("n0de","img_c0ffee")');
  const post = calls.find((c) => c.method === 'POST' && c.url.endsWith('/storage-nodes/n0de/pull'));
  assert.ok(post && post.body.image_id === 'img_c0ffee', 'the pull was started');
  assert.equal(run('currentTab'), 3, 'the transfers tab is now active');
  await new Promise((r) => setTimeout(r, 20));
  assert.match(node('#branch-view').innerHTML, /branch-transfers-n0de/);
});
