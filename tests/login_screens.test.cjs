// #1085 שלב ב': חמשת מסכי הכניסה מ-JSON קבוע + כללי נוהל חיים + 6 תאים +
// "המשך" נעול עד ☐. בלי דפדפן — console.js ב-vm.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

function setup() {
  const nodes = new Map();
  function node(key) {
    if (!nodes.has(key)) nodes.set(key, {
      value: '', innerHTML: '', textContent: '', title: '', dataset: {},
      style: {removeProperty() {}},
      classList: {add() {}, remove() {}, toggle() {}},
      addEventListener() {}, removeEventListener() {},
      querySelector() { return null; }, querySelectorAll() { return []; },
      insertAdjacentHTML(_p, html) { this.innerHTML += html; },
      setAttribute() {}, removeAttribute() {}, focus() {},
    });
    return nodes.get(key);
  }
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise,
    encodeURIComponent, decodeURIComponent, String,
    document: {
      hidden: false, querySelector: node, querySelectorAll: () => [],
      getElementById: (id) => node('#' + id),
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); }, get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}, removeAttribute() {}},
      addEventListener() {}, removeEventListener() {},
    },
    window: {addEventListener() {}, matchMedia: () => ({matches: false})},
    localStorage: {getItem: () => null, setItem() {}, removeItem() {}},
    location: {hostname: 'imagectl-srv'},
    navigator: {clipboard: {writeText: async () => {}}},
    CSS: {escape: (x) => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async () => ({status: 401, ok: false, headers: {get: () => null}, json: async () => ({})}),
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'console.js'), 'utf8'), ctx);
  const run = (s) => vm.runInContext(s, ctx);
  return {ctx, run, node};
}

const FIX = {
  login: {screen: 'login'},
  error401: {status: 401, body: {detail: 'שם משתמש או סיסמה שגויים'}},
  error403: {status: 403, body: {error: 'deploy_no_console', message_he: 'משתמש הפצה עובד ממחשב הבנייה, לא מהקונסולה'}},
  error429: {status: 429, body: {detail: 'נסה שוב בעוד 900 שניות'}, retryAfter: 900},
  change: {must_change_password: true},
  mfa: {mfa_required: true, challenge: 'chal-1'},
  enroll: {mfa_enrollment_required: true},
  ok: {username: 'nadav', role: 'admin', idle_seconds: 300},
};

test('1085: five screens from fixed JSON — login, error, change, mfa, setup/codes', () => {
  const {run} = setup();
  const login = run('loginHtml(' + JSON.stringify(FIX.login) + ')');
  assert.match(login, /<h1 class="title">כניסה<\/h1>/);
  assert.match(login, /זכור את הדפדפן הזה ל-7 שעות/);
  assert.match(login, /<label for="login-user">שם משתמש<\/label>/);
  assert.match(login, />הצג</);
  assert.doesNotMatch(login, /גישה מאובטחת|זכור אותי|הזן את פרטי|קונסולת ניהול/);

  const st401 = run('loginStateFromResponse(401, ' + JSON.stringify(FIX.error401.body) + ', null, {username:"nadav"})');
  assert.equal(st401.screen, 'login');
  assert.equal(st401.error, 'שם משתמש או סיסמה שגויים');
  assert.equal(st401.username, 'nadav');
  const err = run('loginHtml(' + JSON.stringify({screen: 'login', username: 'nadav', error: st401.error, fieldErr: true}) + ')');
  assert.match(err, /שם משתמש או סיסמה שגויים/);
  assert.match(err, /value="nadav"/);
  assert.match(err, /class="f err"/);

  const st403 = run('loginStateFromResponse(403, ' + JSON.stringify(FIX.error403.body) + ', null, {username:"labtech"})');
  assert.equal(st403.error, 'משתמש הפצה עובד ממחשב הבנייה, לא מהקונסולה');

  const st429 = run('loginStateFromResponse(429, ' + JSON.stringify(FIX.error429.body) + ', 900, {username:"nadav"})');
  assert.equal(st429.error, 'החשבון נעול ל-15 דקות');

  const stCh = run('loginStateFromResponse(200, ' + JSON.stringify(FIX.change) + ', null, {username:"admin"})');
  assert.equal(stCh.screen, 'change');
  const ch = run('loginHtml(' + JSON.stringify({screen: 'change', username: 'admin'}) + ')');
  assert.match(ch, /בחר סיסמה חדשה/);
  assert.match(ch, /לפחות 8 תווים/);
  assert.match(ch, /אותיות/);
  assert.match(ch, /ספרות/);
  assert.match(ch, /תו מיוחד/);
  assert.match(ch, /id="pw-change-go" disabled/);

  const stMfa = run('loginStateFromResponse(200, ' + JSON.stringify(FIX.mfa) + ', null, {username:"nadav"})');
  assert.equal(stMfa.screen, 'mfa');
  assert.equal(stMfa.challenge, 'chal-1');
  const mfa = run('loginHtml(' + JSON.stringify({screen: 'mfa', username: 'nadav', otp: ['', '', '', '', '', '']}) + ')');
  assert.match(mfa, /הקוד מאפליקציית האימות/);
  assert.equal((mfa.match(/data-otp="/g) || []).length, 6);
  assert.match(mfa, /השתמש בקוד גיבוי/);

  const stEn = run('loginStateFromResponse(200, ' + JSON.stringify(FIX.enroll) + ', null, {username:"nadav"})');
  assert.equal(stEn.screen, 'setup');
  const setupHtml = run('loginHtml(' + JSON.stringify({screen: 'setup', username: 'nadav', secret: 'JBSWY3DPEHPK3PXP', otpauth: 'otpauth://totp/ImageCtl:nadav?secret=JBSWY3DPEHPK3PXP'}) + ')');
  assert.match(setupHtml, /הגדרת אימות דו-שלבי/);
  assert.match(setupHtml, /otpauth:\/\//);
  assert.match(setupHtml, /JBSW Y3DP EHPK 3PXP/);

  const codes = run('loginHtml(' + JSON.stringify({screen: 'codes', backupCodes: ['AAAA', 'BBBB'], savedAck: false}) + ')');
  assert.match(codes, /המשך לקונסולה/);
  assert.match(codes, /שמרתי את הקודים במקום בטוח/);
  assert.match(codes, /id="setup-done" disabled/);
  assert.match(codes, /העתק/);
  assert.match(codes, /הדפס/);

  const app = run('loginStateFromResponse(200, ' + JSON.stringify(FIX.ok) + ', null, {})');
  assert.equal(app.screen, 'app');
});

test('1085: live password policy — button lights only when all four checks and confirm match', () => {
  const {run} = setup();
  const asPlain = (v) => JSON.parse(JSON.stringify(v)); // run() חוצה realm של vm — deepEqual דורש עצם רגיל
  assert.deepEqual(asPlain(run('passwordPolicy("Aa12345!")')), {len: true, alpha: true, digit: true, special: true, all: true});
  assert.equal(run('passwordPolicy("Aa123456").special'), false);
  assert.equal(run('passwordPolicy("aa12345!").all'), true, 'lowercase letters still count');
  assert.equal(run('passwordPolicy("12345678").alpha'), false);
  assert.equal(run('passwordCanSubmit("old", "Aa12345!", "Aa12345!")'), true);
  assert.equal(run('passwordCanSubmit("old", "Aa123456", "Aa123456")'), false, 'no special → button stays off');
  assert.equal(run('passwordCanSubmit("", "Aa12345!", "Aa12345!")'), false, 'no current password');
  assert.equal(run('passwordCanSubmit("old", "Aa12345!", "Aa12345?")'), false, 'mismatch');
  const lit = run('loginHtml(' + JSON.stringify({screen: 'change', current: 'old', next: 'Aa12345!', confirm: 'Aa12345!'}) + ')');
  assert.doesNotMatch(lit, /id="pw-change-go" disabled/);
  assert.match(lit, /data-rule="len" class="ok"/);
  const dark = run('loginHtml(' + JSON.stringify({screen: 'change', current: 'old', next: 'short', confirm: 'short'}) + ')');
  assert.match(dark, /id="pw-change-go" disabled/);
});

test('1085 negative: lighting the button without all four checks must fail this test', () => {
  const {run} = setup();
  // בקרה שלילית: אם passwordCanSubmit יחזיר true בלי ✓ — הטסט נופל.
  assert.equal(run('passwordCanSubmit("x", "abcdefgh", "abcdefgh")'), false);
  assert.equal(run('passwordCanSubmit("x", "abcd1234", "abcd1234")'), false);
});

test('1085: six OTP cells — paste of 6 digits fills all; backspace helper is otpFromPaste', () => {
  const {run} = setup();
  const asPlain = (v) => JSON.parse(JSON.stringify(v)); // run() חוצה realm של vm — deepEqual דורש עצם רגיל
  assert.deepEqual(asPlain(run('otpFromPaste("123456")')), ['1', '2', '3', '4', '5', '6']);
  assert.deepEqual(asPlain(run('otpFromPaste("12 34-56")')), ['1', '2', '3', '4', '5', '6']);
  assert.deepEqual(asPlain(run('otpFromPaste("12")')), ['1', '2', '', '', '', '']);
  const mfa = run('loginHtml(' + JSON.stringify({screen: 'mfa', username: 'nadav', otp: ['1', '2', '3', '4', '5', '6']}) + ')');
  assert.match(mfa, /value="1"/);
  assert.match(mfa, /value="6"/);
  const backup = run('loginHtml(' + JSON.stringify({screen: 'mfa', username: 'nadav', otpBackup: true, backupCode: 'ABCD1234'}) + ')');
  assert.match(backup, /id="mfa-backup"/);
  assert.doesNotMatch(backup, /id="otp"/);
});

test('1085: continue stays locked until the saved-codes checkbox', () => {
  const {run} = setup();
  assert.equal(run('setupContinueEnabled(false)'), false);
  assert.equal(run('setupContinueEnabled(true)'), true);
  const off = run('loginHtml(' + JSON.stringify({screen: 'codes', backupCodes: ['A'], savedAck: false}) + ')');
  assert.match(off, /id="setup-done" disabled/);
  const on = run('loginHtml(' + JSON.stringify({screen: 'codes', backupCodes: ['A'], savedAck: true}) + ')');
  assert.doesNotMatch(on, /id="setup-done" disabled/);
});

test('1085 negative: continue without checkbox must fail this test', () => {
  const {run} = setup();
  assert.equal(run('setupContinueEnabled(0)'), false);
  assert.equal(run('setupContinueEnabled("")'), false);
});

test('1085: MFA column and my-user labels', () => {
  const {run} = setup();
  assert.equal(run('userMfaLabel({is_builtin:true})'), 'מקומי');
  assert.equal(run('userMfaLabel({mfa_enabled:true})'), '✓');
  assert.equal(run('userMfaLabel({})'), '—');
  run('globalThis.drawn=""; openDrawer=(t,b)=>{ drawn=t+"|"+b; }; closeUserMenu=()=>{};');
  run('ME={username:"nadav",role:"admin",is_builtin:false,mfa_enabled:true}');
  run('openAccount()');
  const html = run('drawn');
  assert.match(html, /המשתמש שלי/);
  assert.match(html, /צור קודי גיבוי חדשים/);
  run('ME.is_builtin=true; ME.mfa_enabled=false; openAccount()');
  assert.match(run('drawn'), /מקומי · ללא MFA/);
  run('ME.is_builtin=false; ME.mfa_enabled=false; openAccount()');
  assert.match(run('drawn'), />הפעל</);
});

test('1085: 401/403/429 mapping does not drop the typed username', () => {
  const {run} = setup();
  const prev = {username: 'nadav', remember: true};
  for (const [status, body, ra] of [
    [401, {}, null],
    [403, {error: 'deploy_no_console', message_he: 'משתמש הפצה עובד ממחשב הבנייה, לא מהקונסולה'}, null],
    [429, {}, 60],
  ]) {
    const st = run(`loginStateFromResponse(${status}, ${JSON.stringify(body)}, ${ra === null ? 'null' : ra}, ${JSON.stringify(prev)})`);
    assert.equal(st.username, 'nadav', String(status));
    assert.equal(st.screen, 'login');
  }
});

test('1120: wrong MFA code returns to the login screen with the server message and no challenge', () => {
  const {run} = setup();
  const prev = {screen: 'mfa', username: 'nadav', remember: true, challenge: 'chal-1', otp: ['1', '2', '3', '4', '5', '6']};
  const st = run('mfaRejectedState(' + JSON.stringify({detail: 'קוד שגוי — היכנס מחדש'}) + ', ' + JSON.stringify(prev) + ')');
  assert.equal(st.screen, 'login');
  assert.equal(st.username, 'nadav');
  assert.equal(st.remember, true);
  assert.equal(st.challenge, '');
  assert.equal(st.error, 'קוד שגוי — היכנס מחדש');
  const html = run('loginHtml(' + JSON.stringify(st) + ')');
  assert.match(html, /היכנס מחדש/);
  assert.equal((html.match(/data-otp="/g) || []).length, 0);
  const fallback = run('mfaRejectedState({}, {username: "x"})');
  assert.equal(fallback.error, 'קוד שגוי — היכנס מחדש');
});
