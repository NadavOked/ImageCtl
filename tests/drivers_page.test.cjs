// #720 — דף "דרייברים" בקונסולה אינו placeholder: הוא מציג את חבילות
// הדרייברים (שם, כללי התאמה, קבצים, "מתאים ל-N מכונות") עם ייבוא ומחיקה
// ל-admin בלבד, ו-memberRow מציג את תוצאת ה-staging ליד המחשב. הבדיקה
// מריצה את console.js + drivers.js המשוגרים ב-vm עם fetch מזויף — החזרת
// render:pagePlaceholder מפילה אותה התנהגותית.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

const stubNode = () => ({value: '', innerHTML: '', textContent: '', dataset: {}, style: {removeProperty() {}},
  classList: {add() {}, remove() {}, toggle() {}}, addEventListener() {}, removeEventListener() {},
  querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {}, focus() {}});

function setup(fixtures, role = 'admin') {
  const toasts = [];
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
    fetch: async (url) => {
      const key = url.replace('/api/console', '').split('?')[0];
      return {status: 200, ok: true, headers: {get: () => null}, json: async () => fixtures[key] || {}};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'progress.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'console.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'drivers.js'), 'utf8'), ctx);
  const run = s => vm.runInContext(s, ctx);
  run(`ME={username:"u",role:"${role}",idle_seconds:300,capabilities:{}}; toast=(m)=>__toasts.push(m); current="drivers";`);
  ctx.__toasts = toasts;
  return {run, toasts};
}

const PKG = {
  name: 'intel-nic', description: 'Intel I219 <b>LM</b>',
  match: [{pci: ['8086:15bc:020000']}, {vendor: 'LENOVO', model: 'ThinkCentre M720q'}],
  files: [{path: 'e1d68x64.inf', sha256: 'a'.repeat(64)}, {path: 'x64/e1d68x64.sys', sha256: 'b'.repeat(64)}],
  matches: ['b4:2e:99:07:1a:c4'],
};

test('the drivers page is wired: load + render, not a placeholder', () => {
  const {run} = setup({});
  assert.equal(run('typeof pages.drivers.load'), 'function');
  assert.match(run('pages.drivers.render.toString()'), /driversPage/);
  assert.notEqual(run('tabRender("drivers", 0)'), run('tabRender("no-such-page", 0)'));
});

test('packages render with rules, file count, matches and escaping', async () => {
  const {run} = setup({'/drivers': [PKG]});
  run('MACHINES=[{mac:"b4:2e:99:07:1a:c4", suffix:"05", group_id:"grp_LAB1"}]');
  await run('loadDrivers()');
  const html = run('driversPage()');
  assert.match(html, /intel-nic/);
  assert.match(html, /&lt;b&gt;LM&lt;\/b&gt;/, 'description is escaped');
  assert.match(html, /PCI 8086:15bc:020000/);
  assert.match(html, /LENOVO · ThinkCentre M720q/);
  assert.match(html, /<td>2<\/td>/, 'file count');
  assert.match(html, /מתאים ל-1 מכונות/);
  assert.match(html, /title="05"/, 'the machine is named, not its MAC');
  assert.match(html, /deleteDriver\('intel-nic'\)/);
  assert.match(html, /openDriverImport\(\)/);
});

test('an empty library says so, and a failed read is not an empty library', async () => {
  const {run} = setup({'/drivers': []});
  await run('loadDrivers()');
  assert.match(run('driversPage()'), /אין חבילות דרייברים/);
  run('DRIVERS=null');
  assert.match(run('driversPage()'), /לא נקראה/);
});

test('a deploy user sees the list but no import or delete', async () => {
  const {run} = setup({'/drivers': [{...PKG, matches: []}]}, 'deploy');
  await run('loadDrivers()');
  const html = run('driversPage()');
  assert.match(html, /intel-nic/);
  assert.match(html, /לא מתאים לאף מכונה/);
  assert.doesNotMatch(html, /deleteDriver/);
  assert.doesNotMatch(html, /openDriverImport/);
});

test('memberRow shows the staging outcome — and failed is visible, not silent', () => {
  const {run} = setup({});
  const row = (drivers) => run(`memberRow(${JSON.stringify({mac: 'b4:2e:99:07:1a:c4', state: 'done', done: true,
    bytes_written: 1, bytes_total: 1, disks: [], drivers})}, {})`);
  assert.match(row({state: 'staged', packages: ['intel-nic']}), /דרייברים הונחו: intel-nic/);
  assert.match(row({state: 'no_match', packages: []}), /אין חבילה תואמת/);
  assert.match(row({state: 'failed', packages: [], error: 'sha256 mismatch: x <script>'}),
    /דרייברים לא הונחו: sha256 mismatch: x &lt;script&gt;/);
  assert.doesNotMatch(row({state: 'skipped', packages: [], reason: 'not_windows'}), /דרייברים/);
  assert.doesNotMatch(row(null), /דרייברים/, 'null = the stage did not report');
});

test('index.html loads drivers.js with the same cache version as the rest', () => {
  const page = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const versions = new Set([...page.matchAll(/\?v=([0-9.]+)/g)].map((m) => m[1]));
  assert.equal(versions.size, 1, [...versions].join(','));
  assert.match(page, /drivers\.js\?v=/);
  assert.match(page, /data-page="drivers"/);
});
