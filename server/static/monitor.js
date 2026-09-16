/* ImageCtl — לקוח RFB מרוחק (#690), vanilla JS, בלי ספרייה חיצונית.
   מדבר RFB 3.8 מול imagectl-monitor (LibVNCServer, בלי סיסמה) דרך
   ה-WebSocket האדמיני /api/console/monitor/<mac>, ומצייר ל-canvas.

   הקונסולה כולה vanilla JS בלי CDN (ראו CLAUDE.md), ולכן זהו לקוח
   קומפקטי משלנו ולא חבילת noVNC המלאה: קידודי Raw + CopyRect בלבד —
   בדיוק מה ש-LibVNCServer מגיש כברירת מחדל כשהלקוח מציע רק אותם.
   החוזה מול השרת — בייטי RFB גולמיים על מסגרות WebSocket בינאריות —
   זהה למה ש-noVNC היה מצפה לו, ולכן החלפה לספרייה מלאה בעתיד אינה
   נוגעת בשרת. איננו דורשים subprotocol (ראו connect() למה).

   view/control ואריזת ה-initramfs נבדקים על ברזל (אימות של נדב). */
"use strict";

const params = new URLSearchParams(location.search);
const MAC = params.get("mac") || "";
const NAME = params.get("name") || MAC;
/* ‏#655 v1: ``node`` = מזהה שרת משני — המוניטור עובר דרך הראשי אל המשני
   ומשם למכונה. אותו זרם RFB, אותה לחיצת-יד מול הדפדפן; רק הנתיב שונה. */
const NODE = params.get("node") || "";

const canvas = document.getElementById("screen");
const gctx = canvas.getContext("2d", { alpha: false });
const statusEl = document.getElementById("status");
const titleEl = document.getElementById("title");
titleEl.textContent = NAME ? `מוניטור — ${NAME}` : "מוניטור";
document.title = titleEl.textContent;

function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = "status " + (cls || "");
}

/* --- קורא בייטים סדרתי מעל ה-WebSocket -----------------------------------
   הפרוטוקול נכתב כקוד רציף עם await; onmessage מצרף לחוצץ ומעיר קריאה
   שממתינה. מסגרות WebSocket מתפצלות, ולכן חייבים צבירה ולא message=הודעה. */
class ByteStream {
  constructor() {
    this.chunks = [];
    this.length = 0;
    this._want = 0;
    this._resolve = null;
    this.closed = false;
  }
  push(buf) {
    this.chunks.push(new Uint8Array(buf));
    this.length += buf.byteLength;
    if (this._resolve && this.length >= this._want) {
      const r = this._resolve; this._resolve = null; r(this._take(this._want));
    }
  }
  fail() {
    this.closed = true;
    if (this._resolve) { const r = this._resolve; this._resolve = null; r(null); }
  }
  _take(n) {
    const out = new Uint8Array(n);
    let off = 0;
    while (off < n) {
      const head = this.chunks[0];
      const need = n - off;
      if (head.length <= need) {
        out.set(head, off); off += head.length;
        this.chunks.shift();
      } else {
        out.set(head.subarray(0, need), off);
        this.chunks[0] = head.subarray(need); off += need;
      }
    }
    this.length -= n;
    return out;
  }
  read(n) {
    if (this.closed) return Promise.resolve(null);
    if (this.length >= n) return Promise.resolve(this._take(n));
    this._want = n;
    return new Promise((resolve) => { this._resolve = resolve; });
  }
}

const be16 = (a, i) => (a[i] << 8) | a[i + 1];
const be32 = (a, i) => (a[i] * 0x1000000) + (a[i + 1] << 16) + (a[i + 2] << 8) + a[i + 3];

let ws = null;
let stream = null;
let fbWidth = 0;
let fbHeight = 0;
let viewOnly = false;

function wsUrl() {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  if (NODE) {
    return `${scheme}//${location.host}/api/console/storage-nodes/${encodeURIComponent(NODE)}/monitor/${encodeURIComponent(MAC)}`;
  }
  return `${scheme}//${location.host}/api/console/monitor/${encodeURIComponent(MAC)}`;
}

function send(bytes) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(new Uint8Array(bytes));
}

/* --- לחיצת יד RFB 3.8 (None auth) ---------------------------------------- */
async function handshake() {
  const version = await stream.read(12);
  if (!version) throw new Error("החיבור נסגר לפני לחיצת היד");
  send(new TextEncoder().encode("RFB 003.008\n"));

  const nTypes = (await stream.read(1))[0];
  if (nTypes === 0) {
    const len = be32(await stream.read(4), 0);
    const reason = new TextDecoder().decode(await stream.read(len));
    throw new Error("השרת דחה: " + reason);
  }
  const types = await stream.read(nTypes);
  if (!Array.from(types).includes(1))
    throw new Error("השרת דורש אימות שאינו נתמך");
  send([1]);                                   // None
  const result = be32(await stream.read(4), 0);
  if (result !== 0) throw new Error("אימות ה-RFB נכשל");

  send([1]);                                   // ClientInit: shared
  const init = await stream.read(24);
  fbWidth = be16(init, 0);
  fbHeight = be16(init, 2);
  const nameLen = be32(init, 20);
  if (nameLen) await stream.read(nameLen);
  resizeTo(fbWidth, fbHeight);
}

function resizeTo(w, h) {
  fbWidth = w; fbHeight = h;
  canvas.width = w; canvas.height = h;
  gctx.fillStyle = "#000"; gctx.fillRect(0, 0, w, h);
}

/* פורמט פיקסל שאנחנו קובעים: 32bpp, little-endian, true-colour, B,G,R,X. */
function setPixelFormat() {
  send([0, 0, 0, 0,
        32, 24, 0, 1,
        0, 255, 0, 255, 0, 255,
        16, 8, 0, 0, 0, 0]);
}

function setEncodings() {
  const encs = [1, 0, -223];                   // CopyRect, Raw, DesktopSize
  const msg = [2, 0, (encs.length >> 8) & 255, encs.length & 255];
  for (const e of encs) msg.push((e >>> 24) & 255, (e >>> 16) & 255, (e >>> 8) & 255, e & 255);
  send(msg);
}

function requestUpdate(incremental) {
  send([3, incremental ? 1 : 0,
        (0 >> 8) & 255, 0 & 255, 0, 0,
        (fbWidth >> 8) & 255, fbWidth & 255,
        (fbHeight >> 8) & 255, fbHeight & 255]);
}

/* --- לולאת ההודעות מהשרת -------------------------------------------------- */
async function pump() {
  await handshake();
  setPixelFormat();
  setEncodings();
  setStatus("מחובר", "ok");
  requestUpdate(false);

  while (!stream.closed) {
    const head = await stream.read(1);
    if (!head) break;
    if (head[0] === 0) await framebufferUpdate();
    else if (head[0] === 2) { /* Bell */ }
    else if (head[0] === 3) await serverCutText();
    else break;                                // הודעה לא מוכרת — נסגר בגלוי
  }
}

async function framebufferUpdate() {
  const h = await stream.read(3);              // padding(1) + n-rects(2)
  const rects = be16(h, 1);
  for (let i = 0; i < rects; i++) {
    const r = await stream.read(12);
    const x = be16(r, 0), y = be16(r, 2), w = be16(r, 4), hgt = be16(r, 6);
    const enc = be32(r, 8) | 0;
    if (enc === 0) await rawRect(x, y, w, hgt);
    else if (enc === 1) await copyRect(x, y, w, hgt);
    else if (enc === -223) resizeTo(w, hgt);
    else throw new Error("קידוד לא נתמך: " + enc);
  }
  requestUpdate(true);
}

async function rawRect(x, y, w, h) {
  const data = await stream.read(w * h * 4);   // B,G,R,X
  const img = gctx.createImageData(w, h);
  const px = img.data;
  for (let i = 0, j = 0; i < data.length; i += 4, j += 4) {
    px[j] = data[i + 2]; px[j + 1] = data[i + 1]; px[j + 2] = data[i]; px[j + 3] = 255;
  }
  gctx.putImageData(img, x, y);
}

async function copyRect(x, y, w, h) {
  const s = await stream.read(4);
  gctx.drawImage(canvas, be16(s, 0), be16(s, 2), w, h, x, y, w, h);
}

async function serverCutText() {
  const h = await stream.read(7);              // padding(3) + length(4)
  const len = be32(h, 3);
  if (len) await stream.read(len);
}

/* --- קלט: עכבר ומקלדת (רק כשלא view-only) --------------------------------- */
function fbCoords(event) {
  const rect = canvas.getBoundingClientRect();
  const x = Math.round((event.clientX - rect.left) / rect.width * fbWidth);
  const y = Math.round((event.clientY - rect.top) / rect.height * fbHeight);
  return [Math.max(0, Math.min(fbWidth - 1, x)), Math.max(0, Math.min(fbHeight - 1, y))];
}

let buttonMask = 0;
function pointer(event) {
  if (viewOnly) return;
  const [x, y] = fbCoords(event);
  send([5, buttonMask, (x >> 8) & 255, x & 255, (y >> 8) & 255, y & 255]);
}

function keyEvent(keysym, down) {
  if (viewOnly || !keysym) return;
  send([4, down ? 1 : 0, 0, 0,
        (keysym >>> 24) & 255, (keysym >>> 16) & 255, (keysym >>> 8) & 255, keysym & 255]);
}

/* מיפוי אירוע מקלדת ל-keysym של X11 (מה ש-monitor.c מכיר ב-keycode_for). */
const SPECIAL = {
  Enter: 0xff0d, Backspace: 0xff08, Tab: 0xff09, Escape: 0xff1b, " ": 0x20,
  ArrowUp: 0xff52, ArrowDown: 0xff54, ArrowLeft: 0xff51, ArrowRight: 0xff53,
  Home: 0xff50, End: 0xff57, PageUp: 0xff55, PageDown: 0xff56,
  Insert: 0xff63, Delete: 0xffff,
  Shift: 0xffe1, Control: 0xffe3, Alt: 0xffe9, Meta: 0xffeb,
  F1: 0xffbe, F2: 0xffbf, F3: 0xffc0, F4: 0xffc1, F5: 0xffc2, F6: 0xffc3,
  F7: 0xffc4, F8: 0xffc5, F9: 0xffc6, F10: 0xffc7, F11: 0xffc8, F12: 0xffc9,
};
function keysymFor(event) {
  if (event.key.length === 1) return event.key.charCodeAt(0);  // Latin-1
  return SPECIAL[event.key] || 0;
}

/* --- כוח: הפעלה מחדש / כיבוי של המחשב המנוטר (#781) -----------------------
   הפקודה נוסעת על אותו ערוץ RFB כמו כל השאר — ClientCutText (msg type 6),
   שה-WS מעביר כמות שהוא ל-imagectl-monitor. ‏monitor.c (root) מזהה את
   האסימון וקורא ל-`reboot -f`/`poweroff -f`. אין fetch לנתיב חדש, ואין
   תלות ב-view-only: כוח אינו קלט מסך, והשער הוא ה-WS האדמיני של השרת. */
const POWER_PREFIX = "imagectl-power:";

function clientCutText(text) {
  const body = new TextEncoder().encode(text);
  const msg = [6, 0, 0, 0,
               (body.length >>> 24) & 255, (body.length >>> 16) & 255,
               (body.length >>> 8) & 255, body.length & 255,
               ...body];
  send(msg);
}

function sendPower(action) {
  clientCutText(POWER_PREFIX + action);
}

/* --- חיבור וניתוק -------------------------------------------------------- */
function connect() {
  setStatus("מתחבר…", "");
  stream = new ByteStream();
  /* בלי לבקש subprotocol: ‏`new WebSocket(url, "binary")` היה מחייב את
     השרת להחזיר "binary" ב-101, אחרת Chrome סוגר מיד (1006 → "החיבור
     נסגר", מסך ריק). ‏uvicorn עם מימוש ה-WS ‎wsproto **אינו** מהדהד את
     ה-subprotocol (‎websockets כן) — נמדד על הברזל. ‏RFB עובד על מסגרות
     בינאריות גולמיות בלי שום subprotocol, אז פשוט לא דורשים אותו וזה
     חסין לשני המימושים. */
  ws = new WebSocket(wsUrl());
  ws.binaryType = "arraybuffer";
  ws.onmessage = (event) => stream.push(event.data);
  ws.onopen = () => pump().catch((error) => {
    setStatus("שגיאה: " + error.message, "bad");
    if (ws) ws.close();
  });
  ws.onclose = (event) => {
    stream.fail();
    /* ‏#904: השרת מקבל ואז סוגר, ולכן `event.reason` העברית שלו מגיעה
       (לפני כן הסגירה קדמה ל-accept → HTTP 403 → 1006 בלי קוד ובלי
       סיבה). הטבלה היא גיבוי לסיבה ריקה בלבד. */
    const reasons = { 4401: "נדרשת התחברות", 4403: "פעולה למנהל בלבד",
      4404: "מכונה לא מוכרת", 4409: "מוניטור כבר פתוח למכונה זו",
      4502: "שירות המוניטור במכונה אינו זמין",
      4512: "הפרוקסי לא הזדהה מול המוניטור במכונה" };
    setStatus(event.reason || reasons[event.code] || "החיבור נסגר", "bad");
  };
  ws.onerror = () => setStatus("החיבור נכשל", "bad");
}

/* קלט המסך: קליק תופס פוקוס, המקלדת נלכדת כל עוד ה-canvas ממוקד. */
canvas.addEventListener("mousemove", pointer);
canvas.addEventListener("mousedown", (e) => {
  e.preventDefault(); canvas.focus();
  buttonMask |= (1 << e.button); pointer(e);
});
canvas.addEventListener("mouseup", (e) => { buttonMask &= ~(1 << e.button); pointer(e); });
canvas.addEventListener("contextmenu", (e) => e.preventDefault());
canvas.addEventListener("keydown", (e) => { e.preventDefault(); keyEvent(keysymFor(e), true); });
canvas.addEventListener("keyup", (e) => { e.preventDefault(); keyEvent(keysymFor(e), false); });

/* מודאל אישור לפעולת כוח: הקלדת שם המחשב (עיקרון 7), במקום
   prompt/confirm החסומים. משתמש ב-DOM ובמחלקות של console.css. */
function powerModal(action, label) {
  const modal = document.getElementById("pmodal");
  const input = document.getElementById("pmodal-input");
  const error = document.getElementById("pmodal-error");
  document.getElementById("pmodal-title").textContent = label + " — אישור";
  document.getElementById("pmodal-msg").textContent =
    `הפעולה "${label}" תתבצע על המחשב המנוטר עכשיו.`;
  document.getElementById("pmodal-name").textContent = NAME;
  document.getElementById("pmodal-ok").textContent = label;
  error.textContent = "";
  input.value = "";
  modal.style.display = "flex";
  input.focus();

  const close = () => {
    modal.style.display = "none";
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (event) => { if (event.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  document.getElementById("pmodal-cancel").onclick = close;
  document.getElementById("pmodal-ok").onclick = () => {
    if (input.value !== NAME) {
      error.textContent = "השם שהוקלד אינו זהה לשם המחשב";
      return;
    }
    sendPower(action);
    close();
  };
}

document.getElementById("reboot-btn").addEventListener(
  "click", () => powerModal("reboot", "הפעלה מחדש"));
document.getElementById("poweroff-btn").addEventListener(
  "click", () => powerModal("poweroff", "כיבוי"));
document.getElementById("reconnect").addEventListener("click", () => {
  if (ws) ws.close(); connect();
});

if (!MAC) setStatus("חסר MAC בכתובת", "bad");
else connect();
