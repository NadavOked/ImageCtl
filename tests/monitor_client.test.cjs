// #1129 (R48): לקוח ה-RFB בדפדפן (monitor.js) עם תקרות — תחנה עוינת או
// מוניטור שהשתבש אינם מפילים את הטאב: מלבן של w*h*4 מעל 32MB, מלבן מחוץ
// ל-framebuffer, DesktopSize לא סביר, ServerCutText מעל 64KB וחוצץ קלט
// מעל 64MB נסגרים בשגיאה בשם. ‏monitor.js המשוגר רץ ב-vm עם DOM/WebSocket
// מזויפים; מזרימים בייטים ל-ByteStream ומריצים את הפרסור עצמו.
// וגם: פעולת כוח היא POST לשרת עם השם שהוקלד — לא ClientCutText על ה-WS.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

function setup(search = '?mac=aa:bb:cc:dd:ee:ff&name=Cloner01') {
  const listeners = {};
  const stub = () => ({textContent: '', className: '', value: '', style: {}, onclick: null,
    addEventListener(name, fn) { (listeners[name] ||= []).push(fn); }, focus() {}, getBoundingClientRect() { return {left: 0, top: 0, width: 1, height: 1}; },
    getContext() { return {fillRect() {}, createImageData(w, h) { return {data: new Uint8ClampedArray(w * h * 4)}; }, putImageData() {}, drawImage() {}}; }});
  const sockets = [];
  const fetches = [];
  class FakeWebSocket {
    constructor(url) { this.url = url; this.sent = []; this.readyState = 1; this.closed = false; sockets.push(this); }
    send(b) { this.sent.push(b); }
    close() { this.closed = true; }
  }
  FakeWebSocket.OPEN = 1;
  const ctx = vm.createContext({
    console, URLSearchParams, Uint8Array, Uint8ClampedArray, TextEncoder, TextDecoder, Promise, Math, Error, Array, JSON, Number,
    encodeURIComponent, location: {search, protocol: 'http:', host: 'srv:8081'},
    document: {getElementById: stub, addEventListener() {}, removeEventListener() {}, title: ''},
    WebSocket: FakeWebSocket,
    fetch: async (url, options) => { fetches.push({url, options}); return ctx.__fetchReply || {ok: true, json: async () => ({})}; },
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'monitor.js'), 'utf8'), ctx);
  const run = (s) => vm.runInContext(s, ctx);
  return {ctx, run, sockets, fetches, listeners};
}

/* מזרים למחלקת ByteStream של הדף בייטים ומריץ קורוטינה של הפרסור. */
function feed(run, bytes) {
  run('stream = new ByteStream(); fbWidth = 1024; fbHeight = 768;');
  run(`stream.push(new Uint8Array(${JSON.stringify(Array.from(bytes))}).buffer)`);
}
const u16 = (n) => [(n >> 8) & 255, n & 255];
const u32 = (n) => [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255];
const rect = (x, y, w, h, enc) => [...u16(x), ...u16(y), ...u16(w), ...u16(h), ...u32(enc >>> 0)];

test('a Raw rect larger than 32MB fails by name instead of allocating', async () => {
  const {run} = setup();
  feed(run, [0, 0, 1, ...rect(0, 0, 4096, 4096, 0)]);         // 64MB
  await assert.rejects(run('framebufferUpdate()'), /גדול מדי: 4096×4096/);
});

test('a rect outside the framebuffer fails by name (Raw and CopyRect)', async () => {
  const {run} = setup();
  feed(run, [0, 0, 1, ...rect(1000, 0, 100, 10, 0)]);
  await assert.rejects(run('framebufferUpdate()'), /מחוץ למסך: 1000,0 100×10 במסך 1024×768/);
  feed(run, [0, 0, 1, ...rect(0, 700, 10, 100, 1)]);
  await assert.rejects(run('framebufferUpdate()'), /CopyRect מחוץ למסך/);
  feed(run, [0, 0, 1, ...rect(0, 0, 10, 10, 1), ...u16(1020), ...u16(0)]);   // מקור מחוץ למסך
  await assert.rejects(run('framebufferUpdate()'), /מקור CopyRect מחוץ למסך/);
});

test('a sane Raw rect still draws and a DesktopSize within limits resizes', async () => {
  const {run} = setup();
  feed(run, [0, 0, 2, ...rect(10, 10, 2, 2, 0), ...new Array(16).fill(7), ...rect(0, 0, 1280, 720, -223)]);
  await run('framebufferUpdate()');
  assert.equal(run('fbWidth'), 1280); assert.equal(run('fbHeight'), 720);
});

test('DesktopSize beyond 32MB is refused by name', async () => {
  const {run} = setup();
  feed(run, [0, 0, 1, ...rect(0, 0, 65535, 65535, -223)]);
  await assert.rejects(run('framebufferUpdate()'), /גודל מסך לא סביר: 65535×65535/);
});

test('ServerCutText above 64KB is refused by name', async () => {
  const {run} = setup();
  feed(run, [0, 0, 0, ...u32(64 * 1024 + 1)]);
  await assert.rejects(run('serverCutText()'), /ServerCutText ארוך מדי: 65537 בייט/);
  feed(run, [0, 0, 0, ...u32(3), 65, 66, 67]);
  await run('serverCutText()');                                 // בגבול — עובר
});

test('ByteStream refuses to buffer more than 64MB and fails the pending read by name', async () => {
  const {run} = setup();
  run('stream = new ByteStream();');
  const pending = run('stream.read(1)');                        // ממתין
  for (let i = 0; i < 65; i++) run('stream.push(new ArrayBuffer(1024 * 1024))');   // 65MB: הראשון משחרר את הקריאה, השאר נצברים עד התקרה
  // הקריאה הממתינה קיבלה את הבייט הראשון; הבאה נכשלת בשם.
  await pending;
  await assert.rejects(run('stream.read(1)'), /חוצץ הקלט עבר 64MB/);
  assert.equal(run('stream.closed'), true);
});

test('handshake refuses an oversized reject-reason and server name', async () => {
  const {run} = setup();
  const enc = (s) => Array.from(new TextEncoder().encode(s));
  feed(run, [...enc('RFB 003.008\n'), 0, ...u32(64 * 1024 + 1)]);
  await assert.rejects(run('handshake()'), /סיבה ארוכה מדי/);
  feed(run, [...enc('RFB 003.008\n'), 1, 1, ...u32(0), ...u16(800), ...u16(600), ...new Array(16).fill(0), ...u32(1 << 20)]);
  await assert.rejects(run('handshake()'), /שם המסך ארוך מדי/);
});

test('power goes to POST …/power with the typed name; the client never builds ClientCutText', async () => {
  const {run, sockets, fetches} = setup();
  assert.equal(sockets.length, 1, 'connected on load');
  await run('sendPower("reboot", "typed-name")');
  assert.equal(fetches.length, 1);
  assert.equal(fetches[0].url, '/api/console/monitor/aa%3Abb%3Acc%3Add%3Aee%3Aff/power');
  assert.equal(fetches[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(fetches[0].options.body), {action: 'reboot', confirm: 'typed-name'});
  assert.equal(sockets[0].sent.length, 0, 'nothing on the RFB channel');
  const js = fs.readFileSync(path.join(root, 'monitor.js'), 'utf8');
  assert.doesNotMatch(js, /clientCutText|imagectl-power/);
  // דרך משני: הנתיב של המשני, אותו גוף.
  const remote = setup('?mac=aa:bb:cc:dd:ee:ff&node=n0de');
  await remote.run('sendPower("poweroff", "x")');
  assert.equal(remote.fetches[0].url, '/api/console/storage-nodes/n0de/monitor/aa%3Abb%3Acc%3Add%3Aee%3Aff/power');
});

test('a 403 from the server surfaces its detail — the name check is the server’s, not the page’s', async () => {
  const {run, ctx} = setup();
  ctx.__fetchReply = {ok: false, status: 403, json: async () => ({detail: 'השם שהוקלד אינו זהה לשם המחשב'})};
  await assert.rejects(run('sendPower("reboot", "wrong")'), /השם שהוקלד אינו זהה לשם המחשב/);
});
