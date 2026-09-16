// #822 — דף "מוניטור" בקונסולה החדשה אינו placeholder: הוא מציג את
// מכונות ה-build/cloner עם שם, IP, מצב "מחובר" כפי שהשרת קבע, וכפתור
// שקורא ל-monitorMachine (הקיים). הבדיקה מריצה את console.js המשוגר ב-vm
// עם fetch מזויף — החזרת render:pagePlaceholder מפילה אותה התנהגותית.
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
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise,
    encodeURIComponent, decodeURIComponent,
    document: {
      hidden: false, querySelectorAll: () => [], querySelector: stubNode, getElementById: stubNode,
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); },
        get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {},
    },
    window: {addEventListener() {}, matchMedia: () => ({matches: false}), open: (...a) => opened.push(a)},
    localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: x => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async (url) => {
      const key = url.replace('/api/console', '').split('?')[0];
      if (fixtures[key] instanceof Error) return {status: 500, ok: false, headers: {get: () => null}, json: async () => ({detail: fixtures[key].message})};
      return {status: 200, ok: true, headers: {get: () => null}, json: async () => fixtures[key] || {}};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'progress.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'console.js'), 'utf8'), ctx);
  const run = s => vm.runInContext(s, ctx);
  run('ME={username:"admin",role:"admin",idle_seconds:300,capabilities:{}}; toast=(m)=>__toasts.push(m); current="monitor";');
  ctx.__toasts = toasts;
  return {ctx, run, opened, toasts};
}

/* #827: המתג להדלקה/כיבוי דורש להריץ בפועל את sheet() (מודאל האישור
   האמיתי, אותו רכיב כמו מתג ה-SSH) — כדי לבדוק "הקלדה שגויה => אין
   fetch" צריך DOM שמחזיק זהות עקבית לאלמנט (#sf-verify) בין הרינדור
   לבין ה-submit, ולא צומת חד-פעמי כמו ב-stubNode של setup() הרגיל. */
function setupWithDom(fixtures) {
  const opened = [];
  const toasts = [];
  const calls = [];
  const registry = new Map();
  const node = (sel) => {
    if (!registry.has(sel)) registry.set(sel, stubNode());
    return registry.get(sel);
  };
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
      if (options.method && options.method !== 'GET') return {status: 200, ok: true, headers: {get: () => null}, json: async () => ({enabled: true})};
      if (fixtures[key] instanceof Error) return {status: 500, ok: false, headers: {get: () => null}, json: async () => ({detail: fixtures[key].message})};
      return {status: 200, ok: true, headers: {get: () => null}, json: async () => fixtures[key] || {}};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'progress.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'console.js'), 'utf8'), ctx);
  const run = s => vm.runInContext(s, ctx);
  run('ME={username:"admin",role:"admin",idle_seconds:300,capabilities:{}}; toast=(m)=>__toasts.push(m); current="monitor";');
  ctx.__toasts = toasts;
  return {ctx, run, opened, toasts, calls, node};
}

const base = {
  '/groups': [{id: 'grp_BUILD', role: 'build', label: 'בנייה', machines: 1}, {id: 'grp_CLONERS', role: 'cloner', label: 'שיכפול', machines: 1}],
  '/machines': [{mac: 'aa:bb:cc:dd:ee:01', suffix: 'Builder', group_id: 'grp_BUILD'}, {mac: 'aa:bb:cc:dd:ee:02', suffix: 'Cloner-1', group_id: 'grp_CLONERS'}],
  '/monitor/settings': {port: 5900, enabled: true},
  '/monitor/machines': [
    {mac: 'aa:bb:cc:dd:ee:01', name: 'Builder', role: 'build', ip: '10.44.12.50', online: true},
    {mac: 'aa:bb:cc:dd:ee:02', name: 'Cloner-1', role: 'cloner', ip: null, online: false},
  ],
};

test('monitor page is wired to a real renderer, not the placeholder', () => {
  const {run} = setup(base);
  assert.equal(run('pages.monitor.render === pagePlaceholder'), false, 'pages.monitor.render must not be pagePlaceholder');
  assert.equal(run('typeof pages.monitor.load'), 'function', 'the page needs a loader');
});

test('renders build/cloner machines with name, IP, server-decided state and a monitor button', async () => {
  const {run} = setup(base);
  assert.equal(run('monitorPage()'), run('pagePlaceholder()'), 'before load: placeholder');
  await run('loadMonitor()');
  const html = run('monitorPage()');
  assert.notEqual(html, run('pagePlaceholder()'));
  assert.match(html, /Builder/);
  assert.match(html, /10\.44\.12\.50/);
  assert.match(html, /Cloner-1/);
  // המצב הוא מה שהשרת אמר — לא נגזר בדפדפן
  assert.match(html, /class="status ok"><i><\/i>מחובר/);
  assert.match(html, /class="status"><i><\/i>לא מחובר/);
  // כפתור מוניטור לכל מכונה, קורא ל-monitorMachine הקיים
  const buttons = [...html.matchAll(/onclick="monitorMachine\('([^']+)'\)"/g)].map(m => decodeURIComponent(m[1]));
  assert.deepEqual(buttons, ['aa:bb:cc:dd:ee:01', 'aa:bb:cc:dd:ee:02']);
  assert.doesNotMatch(html, /המתג כבוי|מתג המוניטור לתחנות כבוי/, 'switch on: no warning');
});

test('monitor button opens monitor.html for the machine through monitorMachine', async () => {
  const {run, opened} = setup(base);
  await run('loadMonitor()');
  run("monitorMachine('aa%3Abb%3Acc%3Add%3Aee%3A01')");
  assert.equal(opened.length, 1);
  assert.match(opened[0][0], /^monitor\.html\?mac=aa%3Abb%3Acc%3Add%3Aee%3A01&name=Builder$/);
});

test('switch off shows the warning; empty list is an empty state, not an error', async () => {
  const {run} = setup({...base, '/monitor/settings': {port: 5900, enabled: false}, '/monitor/machines': []});
  await run('loadMonitor()');
  const html = run('monitorPage()');
  assert.match(html, /מתג המוניטור לתחנות כבוי/);
  assert.match(html, /class="empty"/);
  assert.doesNotMatch(html, /notice err/);
});

test('read failure is a distinct error state, not an empty list (principle 5)', async () => {
  const {run, toasts} = setup({...base, '/monitor/machines': new Error('DB down')});
  await run('loadMonitor()');
  const html = run('monitorPage()');
  assert.match(html, /notice err/);
  assert.match(html, /DB down/);
  assert.doesNotMatch(html, /class="empty"/);
  assert.equal(toasts.length, 1);
});

test('machine drawer no longer carries a monitor button (Nadav, 14/09: monitor lives only on the monitor page)', async () => {
  const {run, ctx} = setup(base);
  await run('loadMonitor()');
  let drawer = '';
  ctx.openDrawer = (_title, body) => { drawer = body; };
  run("openMachineDetail('aa%3Abb%3Acc%3Add%3Aee%3A01')");
  assert.ok(drawer.length > 50, 'the drawer rendered');
  assert.doesNotMatch(drawer, /monitorMachine\(/, 'no monitor button in the machine drawer');
  // ועדיין — בדף המוניטור הכפתור קיים
  assert.match(run('monitorPage()'), /monitorMachine\(/);
});

// --- #827: מתג "מוניטור לתחנות" בראש דף המוניטור --------------------------

test('the switch renders at the top of the page, reflecting server state', async () => {
  const {run} = setup(base); // enabled: true
  await run('loadMonitor()');
  const htmlOn = run('monitorPage()');
  const toggleIdx = htmlOn.indexOf('מוניטור לתחנות');
  const listIdx = htmlOn.indexOf('Builder');
  assert.ok(toggleIdx >= 0 && listIdx >= 0 && toggleIdx < listIdx, 'the switch must render before the machine list');
  assert.match(htmlOn, /class="switch on"/);
  assert.match(htmlOn, />דלוק —/);

  const {run: run2} = setup({...base, '/monitor/settings': {port: 5900, enabled: false}});
  await run2('loadMonitor()');
  const htmlOff = run2('monitorPage()');
  assert.doesNotMatch(htmlOff, /class="switch on"/);
  assert.match(htmlOff, />כבוי —/);
});

test('turning off sends PUT with no confirm word and no sheet() prompt', async () => {
  const {run, calls} = setupWithDom(base); // enabled: true
  await run('loadMonitor()');
  await run('monitorToggle(false)');
  const puts = calls.filter(c => c.method === 'PUT');
  assert.equal(puts.length, 1);
  assert.deepEqual(puts[0].body, {enabled: false});
  assert.equal(puts[0].url, '/api/console/monitor/settings');
});

test('turning on requires typing the exact confirm word — wrong/empty input sends no PUT', async () => {
  const {run, calls, node} = setupWithDom({...base, '/monitor/settings': {port: 5900, enabled: false}});
  await run('loadMonitor()');
  run('monitorToggle(true)'); // opens the sheet() — does not itself call fetch
  assert.equal(calls.filter(c => c.method === 'PUT').length, 0, 'opening the sheet must not send a PUT by itself');

  // הקלדה שגויה: לא מריקים את השדה (ברירת המחדל '') ולא כותבים את המילה הנכונה
  node('#sf-verify').value = 'imagectl.monito'; // חסר תו — לא זהה
  const form = node('#sheet');
  await form.onsubmit({preventDefault() {}});
  assert.equal(calls.filter(c => c.method === 'PUT').length, 0, 'wrong confirm word must not send a PUT');
  assert.match(node('#sf-error').textContent, /אינו זהה/);
});

test('turning on with the exact confirm word sends PUT with confirm, then reloads', async () => {
  const {run, calls, node} = setupWithDom({...base, '/monitor/settings': {port: 5900, enabled: false}});
  await run('loadMonitor()');
  run('monitorToggle(true)');
  node('#sf-verify').value = 'imagectl.monitor';
  const form = node('#sheet');
  await form.onsubmit({preventDefault() {}});
  const puts = calls.filter(c => c.method === 'PUT');
  assert.equal(puts.length, 1);
  assert.deepEqual(puts[0].body, {enabled: true, confirm: 'imagectl.monitor'});
  // אחרי ה-PUT נטען המצב מחדש מהשרת
  assert.ok(calls.some(c => c.method === 'GET' && c.url === '/api/console/monitor/settings' && calls.indexOf(c) > calls.indexOf(puts[0])));
});
