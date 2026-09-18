// #822 — דף "מוניטור" בקונסולה החדשה אינו placeholder: הוא מציג את
// מכונות ה-build/cloner עם שם, IP, מצב "מחובר" כפי שהשרת קבע, וכפתור
// שקורא ל-monitorMachine (הקיים). ‏#954 גל 6: הרשימה היא טבלה (monitor.md) —
// עמוד own בלי לשוניות, מתג #827 בכותרת, "נראה" מ-/net, "מה על המסך" מ-prompt,
// WoL למכונה לא-מחוברת (#984). הבדיקה מריצה את console.js המשוגר ב-vm
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
  for (const f of ['progress.js', 'console.js', 'net.js']) vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), ctx);   // net.js: ago()
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
  for (const f of ['progress.js', 'console.js', 'net.js']) vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), ctx);
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
    {mac: 'aa:bb:cc:dd:ee:01', name: 'Builder', role: 'build', ip: '10.44.12.50', online: true, prompt: 'signin'},
    {mac: 'aa:bb:cc:dd:ee:02', name: 'Cloner-1', role: 'cloner', ip: null, online: false},
  ],
  '/net': [{mac: 'aa:bb:cc:dd:ee:02', ip: '10.44.12.60', last_seen: new Date(Date.now() - 3 * 3600 * 1000).toISOString()}],
};

test('monitor page is wired to a real renderer, not the placeholder; own page without tabs (#954 גל 6)', () => {
  const {run} = setup(base);
  assert.equal(run('pages.monitor.render === pagePlaceholder'), false, 'pages.monitor.render must not be pagePlaceholder');
  assert.equal(run('typeof pages.monitor.load'), 'function', 'the page needs a loader');
  assert.equal(run('pages.monitor.own'), true); assert.equal(run('pages.monitor.tabs.length'), 0, 'no tabs — one question');
});

test('renders build/cloner machines as one table: name+MAC, role, IP, server-decided state with last-seen from /net, prompt, monitor button only when online, WoL when not', async () => {
  const {run} = setup(base);
  assert.equal(run('monitorPage()'), run('pagePlaceholder()'), 'before load: placeholder');
  await run('loadMonitor()');
  const html = run('monitorPage()');
  assert.notEqual(html, run('pagePlaceholder()'));
  assert.match(html, /<table class="dg acts-on">/, 'actions always visible'); assert.match(html, /<th>מכונה<\/th><th>תפקיד<\/th><th>IP<\/th><th>מצב<\/th><th>מה על המסך<\/th>/);
  const row = (n) => { const i = html.indexOf(n); const s = html.lastIndexOf('<tr', i); return html.slice(s, html.indexOf('</tr>', i)); };
  const b = row('Builder'), c = row('Cloner-1');
  assert.match(b, /<span class="name">Builder<\/span><span class="sub"><span class="mono">aa:bb:cc:dd:ee:01/);
  assert.match(b, /<td>מחשב בנייה<\/td><td><span class="mono">10\.44\.12\.50/);
  // המצב הוא מה שהשרת אמר — לא נגזר בדפדפן
  assert.match(b, /class="st ok">מחובר</);
  assert.match(b, /class="st warn">ממתין למפעיל: כניסה</, '"what is on the screen" from prompt');
  assert.match(b, /class="btn sm primary" onclick="monitorMachine\('aa%3Abb%3Acc%3Add%3Aee%3A01'\)">פתח מוניטור</);
  assert.doesNotMatch(b, /wakeMachine/, 'online → no WoL');
  assert.match(c, /class="st ">לא מחובר · נראה לפני 3 שע'</, 'last seen from /net, not from /monitor/machines');
  assert.match(c, /<button class="btn sm" disabled title="[^"]+">פתח מוניטור</, 'not online → nothing to connect to');
  assert.doesNotMatch(c, /monitorMachine\(/);
  assert.match(c, /wakeMachine\('aa%3Abb%3Acc%3Add%3Aee%3A02'\)">WoL</, '#984: WoL for a machine that is not connected');
  assert.match(html, /2 מכונות · 1 מחוברות עכשיו/); assert.match(html, /class="pill ok">המתג דלוק</);
  assert.doesNotMatch(html, /המתג כבוי|מוניטור: כבוי \(ברירת מחדל\)/, 'switch on: no warning');
  assert.doesNotMatch(html, /role="tablist"|detail-grid|class="switch/, 'no tabs, no per-machine cards, no old switch');
});

test('a machine that never talked and /net that could not be read are two different states', async () => {
  let s = setup({...base, '/net': []});
  await s.run('loadMonitor()');
  assert.match(s.run('monitorPage()'), /class="st ">מעולם לא נראה</);
  s = setup({...base, '/net': new Error('net down')});
  await s.run('loadMonitor()');
  assert.match(s.run('monitorPage()'), /לא מחובר · נראה: לא נקרא/);
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
  assert.match(html, /note warn.*מוניטור: כבוי \(ברירת מחדל\) — הדלקה חושפת 5900/);
  assert.match(html, /class="pill warn">המתג כבוי</);
  assert.match(html, /class="empty">אין מחשבי בנייה או שיכפול רשומים/);
  assert.doesNotMatch(html, /note err/);
});

test('read failure is a distinct error state, not an empty list (principle 5)', async () => {
  const {run, toasts} = setup({...base, '/monitor/machines': new Error('DB down')});
  await run('loadMonitor()');
  const html = run('monitorPage()');
  assert.match(html, /note err/);
  assert.match(html, /DB down/); assert.match(html, /class="pill err">לא נקרא</);
  assert.doesNotMatch(html, /class="empty"|<table/);
  assert.equal(toasts.length, 1);
});

test('machine drawer has NO monitor button — monitor lives only on the monitor page (Nadav 14/09, reaffirmed 17/09 after wave 3)', async () => {
  const {run, ctx} = setup(base);
  await run('loadMonitor()');
  run('DISK_FAILURES=[]; SHRINK_RECORDS=[]');   // the stub fetch answers {} for endpoints this file does not model
  let drawer = '';
  ctx.openDrawer = (_title, body) => { drawer = body; };
  run("openMachineDetail('aa%3Abb%3Acc%3Add%3Aee%3A01')");
  assert.ok(drawer.length > 50, 'the drawer rendered');
  assert.doesNotMatch(drawer, /monitorMachine\(/, 'build machine: no monitor in the drawer');
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
  assert.match(htmlOn, /class="sw on" role="switch" aria-checked="true" aria-label="מוניטור לתחנות" onclick="monitorToggle\(false\)"/);

  const {run: run2} = setup({...base, '/monitor/settings': {port: 5900, enabled: false}});
  await run2('loadMonitor()');
  const htmlOff = run2('monitorPage()');
  assert.match(htmlOff, /class="sw" role="switch" aria-checked="false"[^>]*onclick="monitorToggle\(true\)"/);
  assert.match(htmlOff, /מוניטור לתחנות · כבוי \(ברירת מחדל\) — הדלקה חושפת 5900/);   // #1077: הכיתוב החדש, בלי 🔒 (אין הקלדה במתג הזה)
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
