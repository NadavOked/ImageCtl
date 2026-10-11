// #153 — כרטיסי "תעודת TLS" ו"שם המארח" בדף ההגדרות. הפונקציות המשוגרות
// עצמן רצות ב-vm (כמו couldnt_read_negative): "לא נקרא" חייב להיראות שונה
// מ"אין TLS", תוקף שפג מסומן, והשדות של חומר אמון/שם מארח הם LTR.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');
const js = fs.readFileSync(path.join(root, 'console.js'), 'utf8');

function functionSource(name) {
  const lines = js.split('\n');
  const start = lines.findIndex(line => new RegExp(`^(async )?function ${name}\\(`).test(line));
  assert.ok(start >= 0, `${name} exists in console.js`);
  if (lines[start].trimEnd().endsWith('}')) return lines[start];
  const end = lines.findIndex((line, i) => i > start && line.trimEnd() === '}');
  return lines.slice(start, end + 1).join('\n');
}

function ctxWith(state) {
  const UI = {
    card: ({title, body}) => `<card ${title}>${body}</card>`,
    kv: (pairs) => pairs.map(([k, v]) => `[${k}=${v}]`).join(''),
    note: (cls, html) => `<note ${cls}>${html}</note>`,
    pill: (cls, text) => `<pill ${cls}>${text}</pill>`,
  };
  const ctx = vm.createContext({UI, Date, Math, ...state});
  for (const name of ['esc', 'fmtDate', 'tlsModeHe', 'tlsExpiryHtml', 'tlsCardHtml', 'hostnameCardHtml']) {
    vm.runInContext(functionSource(name), ctx);
  }
  vm.runInContext('const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", \'"\': "&quot;", "\'": "&#39;", "`": "&#96;" };', ctx);
  return ctx;
}

const DAY = 86400000;
const cert = (overrides = {}) => ({
  subject: 'imagectl.college.ac.il', issuer: 'College CA', self_signed: false,
  sans: ['imagectl.college.ac.il'], not_after: new Date(Date.now() + 400 * DAY).toISOString(),
  fingerprint_sha256: Array(32).fill('AB').join(':'), ...overrides,
});

test('unread /tls differs from a console that runs without TLS', () => {
  const unread = ctxWith({TLS_STATUS: null, tlsError: 'שגיאה 500'}).tlsCardHtml();
  const off = ctxWith({TLS_STATUS: {enabled: false}, tlsError: ''}).tlsCardHtml();
  assert.match(unread, /<note err>.*לא נקרא/);
  assert.match(off, /<note warn>.*בלי TLS/);
  assert.notEqual(unread, off);
  assert.doesNotMatch(off, /tlsUploadSheet/, 'no upload button when there is no TLS listener');
});

test('the card shows the expiry date, the issuer and an upload button', () => {
  const html = ctxWith({TLS_STATUS: {enabled: true, on_disk: cert(), pending_restart: false,
    covers_hostname: true, hostname: 'imagectl'}}).tlsCardHtml();
  assert.match(html, /\[בתוקף עד=<span dir="ltr">\d\d\/\d\d\/\d{4}<\/span>\]/);
  assert.match(html, /חתומה בידי College CA/);
  assert.match(html, /onclick="tlsUploadSheet\(\)"/);
  assert.doesNotMatch(html, /<note/);
});

test('expired and soon-to-expire certificates are flagged', () => {
  const expired = ctxWith({TLS_STATUS: {enabled: true, on_disk: cert({not_after: new Date(Date.now() - 2 * DAY).toISOString()}),
    pending_restart: false, covers_hostname: true, hostname: 'x'}}).tlsCardHtml();
  assert.match(expired, /<pill err>פג תוקף/);
  const soon = ctxWith({TLS_STATUS: {enabled: true, on_disk: cert({not_after: new Date(Date.now() + 10.5 * DAY).toISOString()}),
    pending_restart: false, covers_hostname: true, hostname: 'x'}}).tlsCardHtml();
  assert.match(soon, /<pill warn>עוד 10 ימים/);
});

test('pending restart and a hostname missing from the cert are said, not hidden', () => {
  const html = ctxWith({TLS_STATUS: {enabled: true, on_disk: cert(), pending_restart: true,
    covers_hostname: false, hostname: 'imagectl-ta'}}).tlsCardHtml();
  assert.match(html, /באתחול השרת/);
  assert.match(html, /<span class="mono" dir="ltr">imagectl-ta<\/span> אינו בתעודה/);
});

test('hostname card: unread differs from a value, and the value is LTR', () => {
  const unread = ctxWith({HOSTNAME: null, hostnameError: 'שגיאה 500'}).hostnameCardHtml();
  const ok = ctxWith({HOSTNAME: 'imagectl-ta', hostnameError: ''}).hostnameCardHtml();
  assert.match(unread, /<note err>.*לא נקרא/);
  assert.match(ok, /<span class="mono" dir="ltr">imagectl-ta<\/span>/);
  assert.match(ok, /onclick="hostnameSheet\(\)"/);
});

test('the footer names a CA-signed certificate as such', () => {
  const ctx = ctxWith({});
  assert.equal(ctx.tlsModeHe({mode: 'ca-signed'}), 'תעודה חתומה');
  assert.equal(ctx.tlsModeHe({mode: 'self-signed'}), 'תעודה עצמית');
  assert.doesNotMatch(js, /"TLS תעודה עצמית"/, 'no hard-coded self-signed label left');
});

test('settings page composes both cards and loads them', () => {
  assert.match(functionSource('settings'), /tlsCardHtml\(\) \+ hostnameCardHtml\(\)/);
  assert.match(functionSource('loadSettingsData'), /await loadHostCfg\(\)/);
});

test('credential files and the hostname field are LTR; no typed confirmation word', () => {
  const upload = functionSource('tlsUploadSheet'), rename = functionSource('hostnameSheet');
  assert.equal((upload.match(/type: "file", dir: "ltr"/g) || []).length, 2);
  assert.match(rename, /id: "hostname", label: "שם חדש", value: HOSTNAME \|\| "", dir: "ltr"/);
  for (const src of [upload, rename]) {
    assert.match(src, /danger: true/);
    assert.match(src, /impactList\(/);
    assert.doesNotMatch(src, /confirm_name|הקלד/);
  }
});
