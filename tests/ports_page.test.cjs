// #822 — דף "פורטים" בקונסולה מציג את מה שהשרת קורא (`/api/console/ports`),
// לא רשימה קבועה של חמישה. הבדיקה מריצה את console.js המשוגר ב-vm עם
// fetch מזויף, כמו monitor_page.test.cjs.
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
      if (fixtures[key] instanceof Error) return {status: 500, ok: false, headers: {get: () => null}, json: async () => ({detail: fixtures[key].message})};
      return {status: 200, ok: true, headers: {get: () => null}, json: async () => fixtures[key] || {}};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'progress.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'console.js'), 'utf8'), ctx);
  const run = s => vm.runInContext(s, ctx);
  run('ME={username:"admin",role:"admin",idle_seconds:300,capabilities:{}}; toast=(m)=>__toasts.push(m); current="ports";');
  ctx.__toasts = toasts;
  return {ctx, run, toasts};
}

const monitorRow = {id: 'monitor', name: 'Monitor (RFB)', port: '5900', proto: 'tcp',
  desc: 'צפייה מרחוק', target: 'תחנות (build/cloner)', state: 'off',
  detail: 'המתג monitor:stations כבוי', note: 'לפתוח ב-FW: TCP 5900 מהשרת לוילן ההפצה'};
const kioskRow = {id: 'kiosk', name: 'HTTP', port: '8082', proto: 'tcp',
  desc: 'אפליקציית הקיוסק', target: 'דפדפן (קלונרים)', state: 'off',
  detail: 'אין hook שקורא את ההאזנה על הפורט הזה — לא אומת', note: 'לפתוח ב-FW: TCP 8082 מהקלונרים לשרת'};
const sshRow = {id: 'ssh_stations', name: 'SSH', port: '22', proto: 'tcp',
  desc: 'מעטפת טכנאי + dropbear', target: 'תחנות', state: 'ok',
  detail: 'שורת הקרנל שמוגשת ל-aa אינה נושאת imagectl.debug', note: 'לפתוח ב-FW: TCP 22 מתחנת הטכנאי לתחנות'};
const tftpRow = {id: 'tftp', name: 'TFTP', port: '69', proto: 'udp',
  desc: 'bootloader', target: 'תחנות (PXE)', state: 'ok', detail: 'dnsmasq מגיש',
  note: 'לפתוח ב-FW: UDP 69 מוילן ההפצה לשרת'};

const base = {'/ports': [tftpRow, monitorRow, kioskRow, sshRow]};

test('ports page is wired to a real renderer that reads server data, not a fixed JS list', () => {
  const {run} = setup(base);
  assert.equal(run('pages.ports.load === loadPorts'), true);
});

test('the page is a placeholder before load, then shows the server rows', async () => {
  const {run} = setup(base);
  assert.equal(run('ports()'), run('pagePlaceholder()'), 'before load: placeholder');
  await run('loadPorts()');
  const html = run('ports()');
  assert.notEqual(html, run('pagePlaceholder()'));
  assert.match(html, /5900\/tcp/, 'monitor port 5900 must appear');
  assert.match(html, /8082\/tcp/, 'kiosk port 8082 must appear');
  assert.match(html, /22\/tcp/, 'ssh port 22 must appear');
  assert.match(html, /69\/udp/, 'tftp still appears');
});

test('a station with 5900 closed (switch off) renders in an off/grey state, not green', async () => {
  const {run} = setup(base);
  await run('loadPorts()');
  const html = run('ports()');
  // המתג כבוי -> class "off" ולא "status ok"
  const monitorBlockIdx = html.indexOf('Monitor (RFB)');
  const nearby = html.slice(monitorBlockIdx, monitorBlockIdx + 400);
  assert.match(nearby, /class="status "/);   // healthStatusClass("off") === ""
  assert.doesNotMatch(nearby, /class="status ok"/);
  assert.match(nearby, /כבוי/);
});

test('an enabled monitor switch renders as a warning, not green', async () => {
  const on = {...monitorRow, state: 'warn', detail: 'המתג monitor:stations דלוק'};
  const {run} = setup({'/ports': [tftpRow, on, kioskRow, sshRow]});
  await run('loadPorts()');
  const html = run('ports()');
  const idx = html.indexOf('Monitor (RFB)');
  assert.match(html.slice(idx, idx + 400), /class="status warn"/);
});

test('every row shows its firewall note', async () => {
  const {run} = setup(base);
  await run('loadPorts()');
  const html = run('ports()');
  assert.match(html, /לפתוח ב-FW: TCP 5900/);
  assert.match(html, /לפתוח ב-FW: TCP 8082/);
});

test('read failure is a distinct error state, not an empty/placeholder page (principle 5)', async () => {
  const {run, toasts} = setup({'/ports': new Error('DB down')});
  await run('loadPorts()');
  const html = run('ports()');
  assert.match(html, /notice err/);
  assert.match(html, /DB down/);
  assert.notEqual(html, run('pagePlaceholder()'));
  assert.equal(toasts.length, 1);
});
