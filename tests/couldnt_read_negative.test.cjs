// #517 / #752 / #516 — "לא הצלחתי לקרוא" חייב להיות מצב נבדל, לא תשובה
// שלילית ודאית. לכל אתר: כשל חייב להוליד תצוגה/ערך שונה מהמצב הוודאי.
// הבדיקה מריצה את הפונקציות המשוגרות עצמן — כך שהחזרת הצורה הישנה
// (בקרה שלילית) מפילה את הטסט התנהגותית, לא ב-collection.
// #517: console.js / netcfg.js. #752: station/*.js. #516: כשל /logout
// בחוסר פעילות אינו "נותקת" ודאית.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

// חילוץ מקור של פונקציה בודדת לפי שמה (אותה גישה כמו operator_progress).
function functionSource(file, name) {
  const source = fs.readFileSync(path.join(root, file), 'utf8');
  const lines = source.split('\n');
  const start = lines.findIndex(line =>
    new RegExp(`^(\\s*)(async )?function ${name}\\(`).test(line));
  assert.ok(start >= 0, `${name} exists in ${file}`);
  const indent = lines[start].match(/^\s*/)[0];
  const end = lines.findIndex((line, i) => i > start && line.trimEnd() === indent + '}');
  return lines.slice(start, end + 1).join('\n');
}

function load(ctx, file, name) {
  vm.runInContext(functionSource(file, name), ctx);
  return ctx[name];
}

const tick = () => new Promise(r => setTimeout(r, 10));

/* --- מקום 1: fmtBytes — null אינו אפס אמיתי ---------------------------- */
test('site1 fmtBytes: unread (null/undefined/NaN) differs from real zero', () => {
  const ctx = vm.createContext({});
  const fmt = load(ctx, 'console.js', 'fmtBytes');
  // ראיה חיובית: null נבדל מ-0
  assert.notEqual(fmt(null), fmt(0), 'null must not render the same as real zero');
  assert.equal(fmt(null), '–');
  assert.equal(fmt(undefined), '–');
  assert.equal(fmt(NaN), '–');
  assert.doesNotMatch(fmt(0), /^–$/);      // אפס אמיתי הוא מדידה, לא "לא נקרא"
  // ערכים אמיתיים נשארים כשהיו — שומר מפני תיקון-יתר
  assert.match(fmt(1024), /1\.0 KB/);
});

/* --- מקום 2: sessionClassMachines — כשל אינו רשימה ריקה ---------------- */
test('site2 sessionClassMachines: read failure returns unread sentinel, not empty list', async () => {
  const ctx = vm.createContext({SESSION_MACHINES: {group: null, list: []}, api: null});
  const fn = load(ctx, 'console.js', 'sessionClassMachines');

  ctx.api = async () => { throw new Error('network down'); };
  const failed = await fn('g1');
  assert.strictEqual(failed, null, 'a failed read must be null (unread), not []');
  assert.notDeepEqual(failed, [], 'unread must be distinguishable from empty');

  // שומר מפני תיקון-יתר: קריאה מוצלחת אחרי כשל מחזירה את הרשימה האמיתית
  ctx.api = async () => [{mac: 'aa'}];
  const ok = await fn('g1');
  assert.deepStrictEqual(ok, [{mac: 'aa'}]);
});

/* --- מקום 3: refreshStatus — כשל /overview מסמן כרטיס ישן, לא return שקט */
test('site3 refreshStatus: /overview failure marks the card stale, not silent', async () => {
  let staleCalls = 0;
  const ctx = vm.createContext({
    api: async () => { throw new Error('overview down'); },
    markStatusStale: () => { staleCalls++; },   // ריגול; הצורה הישנה לא קוראת לזה
    markStatusFresh: () => {},
    ME: {role: 'deploy'},
    $: () => ({textContent: '', innerHTML: '', classList: {add() {}, remove() {}, toggle() {}}}),
  });
  const fn = load(ctx, 'console.js', 'refreshStatus');
  await fn();
  assert.equal(staleCalls, 1, 'a failed /overview must flag staleness, not return silently');
});

/* --- מקום 4: startCountdown — כשל טעינה אחרי ההחזרה אינו נבלע ----------- */
test('site4 startCountdown: reload failure after rollback surfaces, not swallowed', async () => {
  const toasts = [];
  let captured = null;
  const ctx = vm.createContext({
    NETCFG_TICK: null,
    clearInterval: () => {},
    setInterval: (cb) => { captured = cb; return 1; },
    document: {getElementById: () => ({textContent: '1'})},
    loadNetcfg: async () => { throw new Error('cfg down'); },
    toast: (m) => { toasts.push(m); },
  });
  const fn = load(ctx, 'netcfg.js', 'startCountdown');
  fn();
  assert.ok(captured, 'countdown scheduled an interval');
  captured();                 // מגיע ל-0 → loadNetcfg נכשל
  await tick();
  assert.equal(toasts.length, 1, 'reload failure must surface to the operator');
  assert.match(toasts[0], /cfg down/);
});

// חילוץ השמה של onclick — ה-handlers ב-classes.js הם פונקציות חסרות-שם
// שכבר היו לפני התיקון; מריצים אותן עצמן, לא פונקציה חדשה שנעלמת ב-revert.
function assignmentSource(file, needle) {
  const source = fs.readFileSync(path.join(root, file), 'utf8');
  const lines = source.split('\n');
  const start = lines.findIndex(line => line.includes(needle));
  assert.ok(start >= 0, `${needle} exists in ${file}`);
  const indent = lines[start].match(/^\s*/)[0];
  const end = lines.findIndex((line, i) => i > start && line.trimEnd() === indent + '};');
  assert.ok(end > start, `${needle} has a closing };`);
  return lines.slice(start, end + 1).join('\n');
}

function elStub(extra) {
  return {
    textContent: '', value: 'X', onclick: null, focus() {},
    classList: { contains: () => false, add() {}, remove() {}, toggle() {} },
    ...extra,
  };
}

/* --- מקום 5: station fmtBytes — אותו סמן "לא נקרא" כמו ב-console.js ------ */
test('site5 station fmtBytes: unread (null/NaN/Infinity) differs from real zero', () => {
  const stationFmt = load(vm.createContext({}), 'station/station.js', 'fmtBytes');
  const consoleFmt = load(vm.createContext({}), 'console.js', 'fmtBytes');
  assert.notEqual(stationFmt(null), stationFmt(0), 'null must not render the same as real zero');
  assert.equal(stationFmt(null), '–');
  assert.equal(stationFmt(undefined), '–');
  assert.equal(stationFmt(NaN), '–');
  assert.equal(stationFmt(Infinity), '–');
  assert.doesNotMatch(stationFmt(0), /^–$/);
  assert.match(stationFmt(1024), /1\.0 KB/);
  // אותו fmtBytes כמו בקונסולה — לא עותק שנסחף
  for (const v of [null, undefined, NaN, Infinity, -1, 0, 50, 1024]) {
    assert.equal(stationFmt(v), consoleFmt(v), `station fmtBytes drifted from console for ${String(v)}`);
  }
});

/* --- מקום 6: room.refresh — כשל /room מסמן כרטיס ישן, לא return שקט ------ */
test('site6 room refresh: /room failure marks the card stale, not silent', async () => {
  let staleCalls = 0, freshCalls = 0;
  const ctx = vm.createContext({
    fetch: async () => { throw new Error('room down'); },
    markRoomStale: () => { staleCalls++; },
    markRoomFresh: () => { freshCalls++; },
    renderLive() {},
    renderSetup: async () => {},
  });
  const fn = load(ctx, 'station/room.js', 'refresh');
  await fn();
  assert.equal(staleCalls, 1, 'a failed /room must flag staleness, not return silently');
  assert.equal(freshCalls, 0);

  ctx.fetch = async () => ({ ok: true, json: async () => ({ round: null }) });
  await fn();
  assert.equal(freshCalls, 1, 'a successful read must clear staleness');
  assert.equal(staleCalls, 1);
});

/* --- מקום 7: classes closeSession — כשל authed מדווח, לא נבלע ------------ */
test('site7 classes closeSession: authed failure toasts, not swallowed', async () => {
  const toasts = [];
  const ctx = vm.createContext({
    $: () => elStub(),
    authed: async () => { throw new Error('network down'); },
    toast: (m) => toasts.push(m),
    reset() {},
    refresh() {},
  });
  const fn = load(ctx, 'station/classes.js', 'closeSession');
  await fn('sid');
  assert.equal(toasts.length, 1, 'close failure must surface to the operator');
  assert.match(toasts[0], /network down/);
  assert.match(toasts[0], /לא הצלחנו/);
});

/* --- מקום 8: classes cls-open — כשל פתיחת סבב מדווח, לא נבלע ------------ */
test('site8 classes cls-open: session POST failure toasts, not swallowed', async () => {
  const toasts = [];
  const store = {};
  const $ = (sel) => { if (!store[sel]) store[sel] = elStub(); return store[sel]; };
  const ctx = vm.createContext({
    $,
    chosenImage: 'img1',
    group: { id: 'g1' },
    chosen: { size: 2 },
    machines: [{}, {}],
    authed: async () => { throw new Error('network down'); },
    toast: (m) => toasts.push(m),
    shown: 'image',
    refresh() {},
    expandBlockValue: () => 'auto',   // #59: הבחירה חיה ב-station.js, לא כאן
  });
  vm.runInContext(assignmentSource('station/classes.js', '$("#cls-open").onclick'), ctx);
  assert.ok(store['#cls-open'].onclick, 'open handler assigned');
  await store['#cls-open'].onclick();
  assert.equal(toasts.length, 1, 'open failure must surface to the operator');
  assert.match(toasts[0], /network down/);
  assert.match(toasts[0], /לא הצלחנו/);
});

/* --- מקום 9: classes cls-start — כשל התחלה מדווח, לא נבלע --------------- */
test('site9 classes cls-start: start POST failure toasts, not swallowed', async () => {
  const toasts = [];
  const store = {};
  const $ = (sel) => { if (!store[sel]) store[sel] = elStub(); return store[sel]; };
  const ctx = vm.createContext({
    $,
    session: { id: 's1' },
    authed: async () => { throw new Error('network down'); },
    toast: (m) => toasts.push(m),
    shown: 'live-open',
    refresh() {},
  });
  vm.runInContext(assignmentSource('station/classes.js', '$("#cls-start").onclick'), ctx);
  assert.ok(store['#cls-start'].onclick, 'start handler assigned');
  await store['#cls-start'].onclick();
  assert.equal(toasts.length, 1, 'start failure must surface to the operator');
  assert.match(toasts[0], /network down/);
  assert.match(toasts[0], /לא הצלחנו/);
});
/* --- מקום 5: startIdleWatch — כשל /logout אינו ניתוק ודאי (#516) ------ */
function idleWatchContext(postImpl) {
  const loginError = {textContent: ''};
  const toasts = [];
  let showLoginCalls = 0;
  let intervalCb = null;
  let intervalCalls = 0;
  let clearCalls = 0;
  const ctx = vm.createContext({
    ME: {username: 'op', role: 'admin', idle_seconds: 60},
    lastActivity: 0,
    idleTimer: null,
    idleWarned: false,
    noteActivity: () => {},
    post: postImpl,
    showLogin: () => { showLoginCalls++; },
    $: (sel) => sel === '#login-error' ? loginError : {textContent: ''},
    toast: (m) => toasts.push(m),
    clearInterval: () => { clearCalls++; },
    setInterval: (cb) => { intervalCalls++; intervalCb = cb; return 1; },
  });
  return {
    ctx, loginError, toasts,
    showLoginCalls: () => showLoginCalls,
    intervalCb: () => intervalCb,
    intervalCalls: () => intervalCalls,
    clearCalls: () => clearCalls,
  };
}

test('site5 startIdleWatch: failed /logout does not declare a certain disconnect', async () => {
  const env = idleWatchContext(async () => { throw new Error('שגיאה 500'); });
  const fn = load(env.ctx, 'console.js', 'startIdleWatch');
  fn();
  const tickFn = env.intervalCb();
  assert.ok(tickFn, 'idle watch scheduled a timer');
  const clearsBeforeTick = env.clearCalls();
  const intervalsBeforeTick = env.intervalCalls();
  await tickFn();
  // הסשן המקומי לא מבוטל בלי ראיה שהשרת אישר — החזרת ה-catch הריק מפילה כאן
  assert.notEqual(env.ctx.ME, null, 'failed logout must not clear the local session');
  assert.equal(env.showLoginCalls(), 0, 'failed logout must not show the login screen');
  assert.notEqual(env.loginError.textContent, 'נותקת עקב חוסר פעילות.');
  assert.doesNotMatch(env.loginError.textContent, /נותקת/);
  assert.equal(env.toasts.length, 1, 'operator must see that logout was not confirmed');
  assert.match(env.toasts[0], /לא אושר|עדיין פעיל/);
  assert.doesNotMatch(env.toasts[0], /נותקת עקב חוסר פעילות/);
  // ניסיון חוזר: הטיימר ממשיך או משובץ מחדש. clear בלי reschedule הוא הבאג.
  assert.ok(env.clearCalls() === clearsBeforeTick || env.intervalCalls() > intervalsBeforeTick,
    'failed logout must keep trying (timer still running or rescheduled)');
});

test('site5 startIdleWatch: successful /logout still declares idle disconnect', async () => {
  const env = idleWatchContext(async () => ({}));
  const fn = load(env.ctx, 'console.js', 'startIdleWatch');
  fn();
  await env.intervalCb()();
  assert.equal(env.ctx.ME, null, 'confirmed logout still clears the local session');
  assert.equal(env.showLoginCalls(), 1);
  assert.equal(env.loginError.textContent, 'נותקת עקב חוסר פעילות.');
});
