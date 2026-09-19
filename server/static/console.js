/* ImageCtl — מעטפת הקונסולה: אימות, ניווט, drawer/modal.
   תוכן העמודים (נתונים אמיתיים) נכנס בשלבים הבאים. */
"use strict";

const $ = (sel) => document.querySelector(sel);
let ME = null;
/* #1081: v1 hides classrooms. v2 turns ME.capabilities.classrooms on. */
function classroomsOn() {
  return !!(ME && ME.capabilities && ME.capabilities.classrooms);
}
/* v1 ships without the toolbox (Nadav, 19/09). v1.1 turns ME.capabilities.tools on. */
function toolsOn() {
  return !!(ME && ME.capabilities && ME.capabilities.tools);
}
let OVERVIEW = null;
let IMAGES = null, FOLDERS = null;
let IMAGES_FOLDER = null;
let MACHINES = null, GROUPS = null;
let DISK_FAILURES = null;   // #874: זיכרון כשלי הכתיבה (דיסקים אדומים)
let SHRINK_RECORDS = null;  // #926: דיסקי מקור שכווצו בקליטה ולא הוחזרו לגודלם (כתום)
let MACHINES_FILTER = null;
/* צומת-אב בעץ (כיתות / מחשבי בנייה / מחשבי שיכפול) — דף המחשבים ממוקד לתפקיד
   אחד, בלי לשונית "נראו ברשת" (נדב 17/09). */
let MACHINES_ROLE = null;
const ROLE_PAGE_HE = { classroom: "כיתות", build: "מחשבי בנייה", cloner: "מחשבי שיכפול" };
let HEALTH = null, NETCFG = null;
let MONITOR = null, monitorError = "";
let PORTS = null, portsError = "";
let USERS = null, JOURNAL = null;   // ‏#954 גל 6: JOURNAL = השורות שנטענו (LOG.rows), USERS = /users

async function api(path, options = {}) {
  const response = await fetch("/api/console" + path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...options,
  });
  if (response.status === 401) { showLogin(); throw new Error("לא מחובר"); }
  if (!response.ok) {
    let detail = "שגיאה " + response.status;
    // ‏#1073: `message_he` הוא החוזה של סירובים עם קוד (`deploy_no_console`
    // בכניסה) — מוצג כלשונו; `detail` הוא הצורה של HTTPException.
    try { const body = await response.json(); detail = body.message_he || body.detail || detail; } catch (e) {}
    const error = new Error(detail);
    error.status = response.status;   // ‏#936: הקורא מבחין בין 403/409 לכשל אמיתי
    throw error;
  }
  return response.json();
}
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
const put = (path, body) => api(path, { method: "PUT", body: JSON.stringify(body || {}) });
const del = (path) => api(path, { method: "DELETE" });

/* ‏#1129: הברחה מפורשת של ששת התווים, לא textContent→innerHTML — הסריאליזציה
   של הדפדפן ממירה רק `&<>` בצומת טקסט, ו-esc() משמש ב-67 attributes
   (`value="…"`, `title="…"`, `data-*`). ערך עם `"` היה סוגר את ה-attribute
   ומזריק `onfocus=`; המקורות אינם בשליטת המפעיל (שמות שיתופי SMB/NFS
   מסריקת רשת, IQN, ‏switch_name מ-LLDP, dmidecode). בתוך `onclick="fn('…')"`
   זה עדיין לא מספיק — שם משתמשים ב-encodeId + decodeURIComponent. */
const ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "`": "&#96;" };
function esc(text) {
  return (text == null ? "" : String(text)).replace(/[&<>"'`]/g, (c) => ESC_MAP[c]);
}

/* ---------- #954: שפת העיצוב המשותפת — רכיבי רינדור לכל עמוד חדש ----------
   המקור: docs/design/console-redesign (README §"שפת העיצוב", המוקאפים
   שנדב אישר 17/09). ה-CSS: החלק ".page" בסוף console.css. כל עמוד שנבנה
   מחדש מרכיב את עצמו מכאן — כותרת אובייקט, לשוניות, KPI, כרטיס, datagrid,
   פס מצב, מפתח-ערך, ציר-זמן, הודעה וריק-מצב. הפונקציות מחזירות HTML;
   טקסט חופשי עובר esc() אצל הקורא כשהוא HTML, וכאן כשהוא מחרוזת. */
const UI = {
  /* כותרת אובייקט: breadcrumb, אייקון, שם, שורת-משנה, תג מצב, פעולות, לשוניות. */
  objHeader({ crumbs = [], icon = "server", name = "", sub = "", pill = "", actions = "", tabs = [], tab = 0, tabClick = (i) => `activateTab(${i})` }) {
    const crumbHtml = crumbs.map((c, i) => {
      const last = i === crumbs.length - 1;
      const item = c.onclick && !last
        ? `<a role="link" tabindex="0" onclick="${c.onclick}">${esc(c.label)}</a>` : `<span>${esc(c.label)}</span>`;
      return (i ? `<span>/</span>` : "") + item;
    }).join("");
    const tabHtml = tabs.length > 1 ? `<div class="tabs" role="tablist">${tabs.map((t, i) =>
      `<button type="button" class="tab${i === tab ? " on" : ""}" role="tab" aria-selected="${i === tab}" tabindex="${i === tab ? 0 : -1}" onclick="${tabClick(i)}">${esc(t)}</button>`).join("")}</div>` : "";
    return `<div class="obj"><div class="crumbs">${crumbHtml}</div><div class="obj-row"><div class="obj-icon">${uiIcon(icon)}</div><div><div class="obj-name">${esc(name)}</div>${sub ? `<div class="obj-sub">${sub}</div>` : ""}</div>${pill}<div class="obj-actions">${actions}</div></div>${tabHtml}</div>`;
  },
  /* KPI: מספר גדול + תווית + שורת משמעות. cls: ok/warn/err/info/"" (אפור = אין/לא נבדק). */
  kpi({ cls = "", label, value, unit = "", sub = "", bar = null }) {
    const barHtml = bar == null ? "" : `<div class="bar" style="margin-top:6px" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${bar}"><i style="--w:${bar}%"></i></div>`;
    return `<div class="kpi ${cls}"><div class="l">${esc(label)}</div><div class="v"><bdi dir="auto">${esc(value)}</bdi>${unit ? ` <small>${esc(unit)}</small>` : ""}</div>${barHtml}${sub ? `<div class="s">${sub}</div>` : ""}</div>`;
  },
  card({ title, small = "", acts = "", body, cls = "c12", flush = false }) {
    return `<div class="${cls} card"><div class="card-h"><span>${esc(title)}${small ? ` <small>${esc(small)}</small>` : ""}</span>${acts ? `<div class="acts">${acts}</div>` : ""}</div><div class="card-b${flush ? " flush" : ""}">${body}</div></div>`;
  },
  /* מצב עם נקודה: ok/warn/err/run/"" (אפור = כבוי/לא נבדק). */
  status(cls, text) { return `<span class="st ${cls}">${esc(text)}</span>`; },
  pill(cls, text) { return `<span class="pill ${cls}">${esc(text)}</span>`; },
  /* datagrid: columns = תוויות; rows = מערכי HTML לתאים (מוברחים ע"י הקורא). */
  /* שורה: מערך תאים, או {cells, attrs} (תכונות ל-<tr>), או {html} (שורה מוכנה).
     עמודה: מחרוזת (מוברחת) או {html}. */
  datagrid({ columns, rows, empty = "אין נתונים", cls = "" }) {
    if (!rows.length) return `<div class="empty">${esc(empty)}</div>`;
    const tr = (r) => Array.isArray(r) ? `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`
      : r.html != null ? r.html : `<tr ${r.attrs || ""}>${r.cells.map((c) => `<td>${c}</td>`).join("")}</tr>`;
    return `<table class="dg${cls ? " " + cls : ""}"><thead><tr>${columns.map((c) => `<th>${typeof c === "string" ? esc(c) : c.html}</th>`).join("")}</tr></thead><tbody>${rows.map(tr).join("")}</tbody></table>`;
  },
  name(main, sub = "") { return `<span class="name">${esc(main)}</span>${sub ? `<span class="sub">${esc(sub)}</span>` : ""}`; },
  nameHtml(main, subHtml) { return `<span class="name">${esc(main)}</span>${subHtml ? `<span class="sub">${subHtml}</span>` : ""}`; },
  acts(buttons) { return `<div class="acts">${buttons.map(([label, onclick]) => `<button class="btn sm" onclick="${onclick}">${esc(label)}</button>`).join("")}</div>`; },
  /* פס התקדמות + אחוז. pct=null → פס ריק ו"—" (לא 0%: עיקרון 5). */
  barRow(pct, cls = "", text = null) {
    const known = typeof pct === "number" && Number.isFinite(pct);
    const w = known ? Math.max(0, Math.min(100, Math.round(pct))) : 0;
    const label = text != null ? text : (known ? w + "%" : "—");
    return `<div class="bar-row"><div class="bar ${cls}"><i style="--w:${w}%"></i></div><span class="pct">${esc(label)}</span></div>`;
  },
  kv(pairs) { return `<div class="kv">${pairs.map(([k, v]) => `<span class="k">${esc(k)}</span><span class="v">${v}</span>`).join("")}</div>`; },
  note(cls, html) { return `<div class="note ${cls}"><span aria-hidden="true">●</span><span>${html}</span></div>`; },
  empty(text, action = "") { return `<div class="empty">${esc(text)}${action ? `<div>${action}</div>` : ""}</div>`; },
  /* ציר-זמן: [{t, cls, text(html), who}] */
  timeline(events) {
    return `<div class="timeline">${events.map((e) => `<div class="ev"><span class="t">${esc(e.t)}</span><span class="d ${e.cls || ""}"></span><span>${e.text}</span><span class="who">${esc(e.who || "")}</span></div>`).join("")}</div>`;
  },
  /* מה שאין לו API: הודעה ולא כפתור מנוטרל (README §8). */
  soon(label) { return `<span class="muted" title="דורש API">${esc(label)} — בקרוב</span>`; },
  link(label, onclick) { return `<a role="link" tabindex="0" onclick="${onclick}">${esc(label)}</a>`; },
};

/* תאריך dd/mm/yyyy ושעה hh:mm מחותמת ISO (#829 M11). */
/* רצף מספר+יחידה או תאריך+שעה בתוך משפט עברי: הרווח שביניהם נפתר RTL
   (UBA N1 — מספר נחשב R) והרצף מתהפך ("GB 38.4"). מבודדים אותו כ-LTR. */
function ltr(text) { return `<bdi dir="ltr">${esc(text)}</bdi>`; }
function fmtDate(ts) {
  const m = String(ts || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : "—";
}
function fmtClock(ts) {
  const m = String(ts || "").match(/T(\d{2}:\d{2})/);
  return m ? m[1] : "—";
}
/* "היום" = אותו יום UTC כמו חותמות השרת (now_iso). */
function isToday(ts) {
  return String(ts || "").slice(0, 10) === new Date().toISOString().slice(0, 10);
}

/* מודאל אחיד במקום prompt/confirm — net.js/netcfg.js קוראים ל-sheet(). */
function sheet({ title, sub = "", fields = [], submitLabel = "שמירה",
                 danger = false, verify = null, note = "", onSubmit }) {
  const modal = $("#modal");
  const form = $("#sheet");
  const passwordBox = (id, placeholder = "") => `
    <div class="pw-wrap">
      <input type="password" id="${id}" placeholder="${esc(placeholder)}">
      <button type="button" class="pw-eye" data-pw="${id}">הצג</button>
    </div>`;
  const fieldHtml = (f) => {
    if (f.type === "select") {
      return `<label>${esc(f.label)}
        <select id="sf-${f.id}">${(f.options || []).map((o) =>
          `<option value="${esc(o.value)}" ${o.value === f.value ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select></label>`;
    }
    if (f.type === "checkbox") {
      return `<label class="check"><input type="checkbox" id="sf-${f.id}" ${f.value ? "checked" : ""}>
        ${esc(f.label)}</label>`;
    }
    if (f.type === "radio") {
      return `<fieldset class="radio-group" id="sf-${f.id}">
        ${f.label ? `<legend>${esc(f.label)}</legend>` : ""}
        ${(f.options || []).map((o) =>
          `<label class="check"><input type="radio" name="sf-${f.id}" value="${esc(o.value)}" ${o.value === f.value ? "checked" : ""}>
            ${esc(o.label)}</label>`).join("")}
      </fieldset>`;
    }
    if (f.type === "textarea") {
      return `<label>${esc(f.label)}
        <textarea id="sf-${f.id}" rows="3">${esc(f.value || "")}</textarea></label>`;
    }
    if (f.type === "password") {
      return `<label>${esc(f.label)}</label>${passwordBox("sf-" + f.id, f.placeholder)}`
        + (f.confirm
          ? `<label>${esc(f.confirm)}</label>${passwordBox("sf-" + f.id + "-confirm")}`
          : "");
    }
    return `<label>${esc(f.label)}
      <input type="${f.type || "text"}" id="sf-${f.id}" value="${esc(f.value || "")}"
        placeholder="${esc(f.placeholder || "")}" ${f.dir ? `dir="${f.dir}"` : ""}></label>`;
  };
  const verifyHtml = verify ? `
    <label>${esc(verify.label)}
      <input type="text" id="sf-verify" placeholder="${esc(verify.mustEqual)}"></label>` : "";
  form.innerHTML = `
    <div class="shead"><h3>${esc(title)}</h3>${sub ? `<p>${esc(sub)}</p>` : ""}</div>
    <div class="sbody">${note}${fields.map(fieldHtml).join("")}${verifyHtml}<p class="error" id="sf-error"></p></div>
    <div class="sfoot">
      <button class="btn ${danger ? "danger" : "primary"}" type="submit">${esc(submitLabel)}</button>
      <button class="btn" type="button" id="sf-cancel">ביטול</button>
    </div>`;
  form.classList.remove("hidden");
  modal.classList.add("show", "sheet-mode");
  modal.style.display = "flex";
  const first = form.querySelector("input, textarea, select");
  if (first) first.focus();
  const close = () => {
    form.onsubmit = null;
    document.removeEventListener("keydown", onKey);
    closeModal();
  };
  const onKey = (event) => { if (event.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  $("#sf-cancel").onclick = close;
  form.querySelectorAll(".pw-eye").forEach((eye) => eye.onclick = () => {
    const input = document.getElementById(eye.dataset.pw);
    const showing = input.type === "text";
    input.type = showing ? "password" : "text";
    eye.textContent = showing ? "הצג" : "הסתר";
  });
  form.onsubmit = async (event) => {
    event.preventDefault();
    if (verify && $("#sf-verify").value !== verify.mustEqual) {
      $("#sf-error").textContent = "הטקסט שהוקלד אינו זהה";
      return;
    }
    for (const f of fields) {
      if (f.type !== "password" || !f.confirm) continue;
      if ($("#sf-" + f.id).value !== $(`#sf-${f.id}-confirm`).value) {
        $("#sf-error").textContent = "הסיסמאות אינן זהות";
        return;
      }
    }
    const values = {};
    fields.forEach((f) => {
      if (f.type === "radio") {
        const checked = form.querySelector(`input[name="sf-${f.id}"]:checked`);
        values[f.id] = checked ? checked.value : "";
        return;
      }
      const el = $("#sf-" + f.id);
      values[f.id] = f.type === "checkbox" ? !!(el && el.checked) : (el ? el.value : "");
    });
    try { await onSubmit(values); close(); }
    catch (error) { $("#sf-error").textContent = error.message; }
  };
}

function confirmSheet(title, sub, submitLabel, onConfirm) {
  sheet({ title, sub, submitLabel, danger: true, onSubmit: onConfirm });
}

/* ---------- ערכת צבעים ---------- */
/* #1093: שלושה מצבים שנשמרים בשרת (users.theme): auto / light / dark.
   auto עוקב אחרי prefers-color-scheme בחי (כולל שינוי). light/dark
   מנצחים את המערכת. localStorage הוא מטמון בלבד — מונע הבהוב בטעינה
   כשהבחירה אינה auto, ונמחק ב-logout וב-auto. */
const THEME_KEY = "imagectl-theme";
const THEME_CYCLE = ["auto", "light", "dark"];
const THEME_TITLE = { auto: "לפי המערכת", light: "בהיר", dark: "כהה" };
let themePref = "auto";
let themeMql = null;
let themeMqlHandler = null;

function systemPrefersDark() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function currentTheme() {
  if (themePref === "dark" || themePref === "light") return themePref;
  return systemPrefersDark() ? "dark" : "light";
}

function unfollowSystemTheme() {
  if (!themeMql || !themeMqlHandler) {
    themeMql = null;
    themeMqlHandler = null;
    return;
  }
  if (typeof themeMql.removeEventListener === "function")
    themeMql.removeEventListener("change", themeMqlHandler);
  else if (typeof themeMql.removeListener === "function")
    themeMql.removeListener(themeMqlHandler);
  themeMql = null;
  themeMqlHandler = null;
}

function followSystemTheme() {
  unfollowSystemTheme();
  const mql = window.matchMedia("(prefers-color-scheme: dark)");
  themeMql = mql;
  themeMqlHandler = () => { if (themePref === "auto") paintTheme(); };
  if (mql && typeof mql.addEventListener === "function")
    mql.addEventListener("change", themeMqlHandler);
  else if (mql && typeof mql.addListener === "function")
    mql.addListener(themeMqlHandler);
}

function paintTheme() {
  const id = currentTheme();
  document.documentElement.setAttribute("data-theme", id);
  const button = $("#theme-toggle");
  if (!button) return;
  button.innerHTML = uiIcon(id === "dark" ? "sun" : "moon");
  button.title = THEME_TITLE[themePref] || THEME_TITLE.auto;
}

function applyTheme(pref) {
  themePref = pref === "dark" || pref === "light" ? pref : "auto";
  if (themePref === "auto") {
    if (typeof localStorage.removeItem === "function") localStorage.removeItem(THEME_KEY);
    followSystemTheme();
  } else {
    localStorage.setItem(THEME_KEY, themePref);
    unfollowSystemTheme();
  }
  paintTheme();
}

function applyServerTheme(me) {
  const t = me && me.theme;
  applyTheme(t === "light" || t === "dark" ? t : "auto");
}

function toggleTheme() {
  const i = Math.max(0, THEME_CYCLE.indexOf(themePref));
  const next = THEME_CYCLE[(i + 1) % THEME_CYCLE.length];
  applyTheme(next);
  if (!ME) return;
  ME.theme = next;
  return put("/me/theme", { theme: next }).catch((e) => toast(e.message));
}

$("#theme-toggle").addEventListener("click", toggleTheme);

/* מתגי הצגת סיסמה במסך הכניסה — delegation ב-bindLogin. */

/* ---------- לוגו המוסד ---------- */
/* מוחלף בכל מקום שבו מופיע הסמל: הכותרת ומסך הכניסה. הבדיקה נעשית
   פעם אחת בטעינה — הלוגו מוגש בלי כניסה, כי מסך הכניסה מציג אותו. */
async function loadLogo() {
  const response = await fetch("/api/console/branding/logo", { cache: "no-cache" });
  const marks = document.querySelectorAll(".brandmark");
  if (response.status !== 200) {
    // ברירת מחדל: לוגו ImageCtl הווקטורי (שקוף, קריא על כהה ובהיר).
    // לוגו שהמשתמש מעלה גובר עליו (התנאי למעלה).
    marks.forEach((m) => { m.innerHTML = '<img src="logo.svg?v=4.0" alt="ImageCtl">'; });
    return false;
  }
  const url = "/api/console/branding/logo?t=" + Date.now();
  marks.forEach((m) => { m.innerHTML = `<img src="${url}" alt="לוגו">`; });
  return true;
}

async function loadLogoSettings() {
  const exists = await loadLogo();
  const preview = $("#logo-preview");
  if (!preview) return;
  preview.innerHTML = exists
    ? `<img src="/api/console/branding/logo?t=${Date.now()}" alt="לוגו">`
    : `<img src="logo.svg?v=4.0" alt="ImageCtl">`;
  $("#logo-clear").classList.toggle("hidden", !exists);
}

/* ---------- ניתוק אוטומטי בחוסר פעילות ---------- */
/* פעילות = עכבר/מקלדת/מגע של אדם. לא בקשות רשת: הלוח מתשאל את השרת
   כל 2 שניות, ואילו זה נחשב פעילות — אף אחד לא היה מנותק לעולם.
   המסך עומד בכיתה; מי שקם והלך לא משאיר קונסולה פתוחה. */
let lastActivity = Date.now();
let idleTimer = null;
let idleWarned = false;

function noteActivity() {
  lastActivity = Date.now();
  idleWarned = false;
}

["pointerdown", "keydown", "wheel", "touchstart", "mousemove"].forEach((type) =>
  document.addEventListener(type, noteActivity, { passive: true }));

function startIdleWatch() {
  clearInterval(idleTimer);
  if (!ME || !ME.idle_seconds) return;
  noteActivity();
  let inFlight = false;
  idleTimer = setInterval(async () => {
    const idle = (Date.now() - lastActivity) / 1000;
    const left = ME.idle_seconds - idle;
    if (left <= 0) {
      if (inFlight) return;
      inFlight = true;
      try {
        await post("/logout");
        clearInterval(idleTimer);
        ME = null;
        showLogin();
        $("#login-error").textContent = "נותקת עקב חוסר פעילות.";
      } catch (error) {
        toast("הניתוק לא אושר (" + error.message + ") — הסשן עדיין פעיל. מנסה שוב.");
      } finally {
        inFlight = false;
      }
    } else if (left <= 30 && !idleWarned) {
      idleWarned = true;
      toast(`ניתוק בעוד ${Math.ceil(left)} שניות עקב חוסר פעילות`);
    }
  }, 1000);
}

/* ---------- כניסה (#1085 שלב ב') ----------
   חמשת המסכים מ-loginHtml(state) לפי המוקאפ. ה-API לא משתנה. */

const PW_SPECIAL_RE = /[!@#$%^&*(),.?":{}|<>]/;
const LOGIN_ALERT_ICON = '<svg class="ui-icon" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/></svg>';
const LOGIN_CHECK_ICON = '<i><svg class="ui-icon" viewBox="0 0 24 24"><path d="M5 13l4 4L19 7"/></svg></i>';

let LOGIN = { screen: "login", username: "", remember: false, error: "",
              challenge: "", secret: "", otpauth: "", svg: "", backupCodes: [],
              savedAck: false, otpBackup: false, otp: ["", "", "", "", "", ""] };

function passwordPolicy(pw) {
  const v = String(pw || "");
  const len = v.length >= 8;
  const alpha = /[A-Za-z]/.test(v);
  const digit = /\d/.test(v);
  const special = PW_SPECIAL_RE.test(v);
  return { len, alpha, digit, special, all: len && alpha && digit && special };
}

function passwordCanSubmit(current, next, confirm) {
  return !!(passwordPolicy(next).all && String(current || "").length > 0
            && next === confirm && String(next).length > 0);
}

function setupContinueEnabled(ack) { return !!ack; }

function secretBlocks(secret) {
  return String(secret || "").replace(/\s+/g, "").replace(/(.{4})/g, "$1 ").trim();
}

function otpFromPaste(text) {
  const d = String(text || "").replace(/\D/g, "").slice(0, 6).split("");
  while (d.length < 6) d.push("");
  return d;
}

function loginErrorMessage(status, body, retryAfter) {
  body = body || {};
  if (status === 401) return "שם משתמש או סיסמה שגויים";
  if (status === 403 && body.error === "deploy_no_console")
    return body.message_he || "משתמש הפצה עובד ממחשב הבנייה, לא מהקונסולה";
  if (status === 429) {
    const sec = Number(retryAfter);
    const mins = Number.isFinite(sec) && sec > 0 ? Math.max(1, Math.ceil(sec / 60)) : 15;
    return "החשבון נעול ל-" + mins + " דקות";
  }
  if (status === 403 && retryAfter) {
    const sec = Number(retryAfter);
    const mins = Number.isFinite(sec) && sec > 0 ? Math.max(1, Math.ceil(sec / 60)) : 15;
    return "החשבון נעול ל-" + mins + " דקות";
  }
  return body.message_he || body.detail || ("שגיאה " + status);
}

function loginStateFromResponse(status, body, retryAfter, prev) {
  prev = prev || {};
  body = body || {};
  if (status === 200 && body.must_change_password)
    return { screen: "change", username: prev.username || "", error: "" };
  if (status === 200 && body.mfa_required)
    return { screen: "mfa", username: prev.username || "", challenge: body.challenge,
             remember: !!prev.remember, otp: ["", "", "", "", "", ""], otpBackup: false, error: "" };
  if (status === 200 && body.mfa_enrollment_required)
    return { screen: "setup", username: prev.username || "", error: "" };
  if (status === 200 && (body.username || body.ok || body.mfa_enabled))
    return { screen: "app" };
  return { screen: "login", username: prev.username || "", remember: !!prev.remember,
           error: loginErrorMessage(status, body, retryAfter), fieldErr: true };
}

function mfaRejectedState(body, prev) {
  // ‏#1120: קוד שגוי צורך את ה-challenge בשרת — אין למה להישאר במסך
  // הקוד. חזרה לכניסה עם השם שהוקלד ועם הודעת השרת כלשונה.
  prev = prev || {};
  return { screen: "login", username: prev.username || "", remember: !!prev.remember,
           challenge: "", otp: ["", "", "", "", "", ""], otpBackup: false, fieldErr: false,
           error: (body && body.detail) || "קוד שגוי — היכנס מחדש" };
}

function loginMsg(text) {
  if (!text) return '<p class="msg" id="login-error" role="alert"></p>';
  return `<p class="msg" id="login-error" role="alert">${LOGIN_ALERT_ICON}<span>${esc(text)}</span></p>`;
}

function loginEye(id) {
  return `<button type="button" class="eye pw-eye" data-pw="${id}">הצג</button>`;
}

function loginFormHtml(state) {
  const err = state.fieldErr ? " err" : "";
  const user = esc(state.username || "");
  return `<form id="login-form">
    <h1 class="title">כניסה</h1>
    <div class="f${err}"><label for="login-user">שם משתמש</label>
      <input id="login-user" type="text" autocomplete="username" value="${user}" autofocus></div>
    <div class="f pw${err}"><label for="login-pass">סיסמה</label>
      <input id="login-pass" type="password" autocomplete="current-password">${loginEye("login-pass")}</div>
    ${loginMsg(state.error)}
    <label class="check"><input type="checkbox" id="login-remember"${state.remember ? " checked" : ""}> זכור את הדפדפן הזה ל-7 שעות</label>
    <button class="btn" type="submit">כניסה</button>
  </form>`;
}

function loginChangeHtml(state) {
  const rules = passwordPolicy(state.next || "");
  const mismatch = !!(state.confirm && state.confirm !== state.next);
  const ready = passwordCanSubmit(state.current, state.next, state.confirm);
  const row = (key, label) =>
    `<li data-rule="${key}" class="${rules[key] ? "ok" : ""}">${LOGIN_CHECK_ICON}${label}</li>`;
  return `<form id="pw-change-form">
    <h1 class="title sub">בחר סיסמה חדשה</h1>
    <p class="lead">הסיסמה הראשונית של <span class="mono">${esc(state.username || "")}</span> חייבת להתחלף לפני הכניסה.</p>
    <div class="f pw"><label for="pw-change-current">סיסמה נוכחית</label>
      <input id="pw-change-current" type="password" autocomplete="current-password" value="${esc(state.current || "")}">${loginEye("pw-change-current")}</div>
    <div class="f pw"><label for="pw-change-new">סיסמה חדשה</label>
      <input id="pw-change-new" type="password" autocomplete="new-password" value="${esc(state.next || "")}">${loginEye("pw-change-new")}</div>
    <ul class="rules" id="pw-rules" aria-live="polite">
      ${row("len", "לפחות 8 תווים")}${row("alpha", "אותיות")}${row("digit", "ספרות")}${row("special", "תו מיוחד (‏!@#$…)")}
    </ul>
    <div class="f pw${mismatch ? " err" : ""}" id="pw-change-confirm-wrap"><label for="pw-change-confirm">אימות הסיסמה החדשה</label>
      <input id="pw-change-confirm" type="password" autocomplete="new-password" value="${esc(state.confirm || "")}">${loginEye("pw-change-confirm")}</div>
    ${mismatch ? `<p class="msg" role="alert">${LOGIN_ALERT_ICON}<span>הסיסמאות אינן זהות</span></p>` : ""}
    ${loginMsg(state.error)}
    <button class="btn" type="submit" id="pw-change-go"${ready ? "" : " disabled"}>שמור והמשך</button>
  </form>`;
}

function loginMfaHtml(state) {
  const otp = (state.otp || otpFromPaste("")).slice(0, 6);
  const cells = otp.map((d, i) =>
    `<input inputmode="numeric" maxlength="1" aria-label="ספרה ${i + 1}" value="${esc(d)}" data-otp="${i}">`).join("");
  const backup = state.otpBackup
    ? `<div class="f"><label for="mfa-backup">קוד גיבוי</label>
         <input id="mfa-backup" class="mono" type="text" autocomplete="one-time-code" value="${esc(state.backupCode || "")}"></div>`
    : `<div class="otp" id="otp" aria-label="קוד אימות בן 6 ספרות">${cells}</div>`;
  return `<form id="mfa-form">
    <h1 class="title sub">הקוד מאפליקציית האימות</h1>
    <p class="lead"><span class="mono">${esc(state.username || "")}</span> · שישה תווים, מתחלף כל 30 שניות</p>
    ${backup}
    ${loginMsg(state.error)}
    <label class="check"><input type="checkbox" id="mfa-remember"${state.remember ? " checked" : ""}> זכור את הדפדפן הזה ל-7 שעות</label>
    <button class="btn" type="submit">אימות</button>
    ${state.otpBackup ? "" : `<button type="button" class="link" id="mfa-use-backup">השתמש בקוד גיבוי</button>`}
  </form>`;
}

function loginSetupHtml(state) {
  // ‏#1150: בלי svg = python3-qrcode חסר בשרת. אומרים זאת בשם, לא מסתירים.
  const qr = state.svg
    ? `<div class="qr" aria-label="קוד QR להגדרת האפליקציה">${state.svg}</div>`
    : `<p class="msg" role="alert">${LOGIN_ALERT_ICON}<span>אין QR — החבילה python3-qrcode חסרה בשרת; הקלד את הסוד ידנית</span></p>`;
  const url = state.otpauth || "";
  return `<form id="setup-form">
    <h1 class="title sub">הגדרת אימות דו-שלבי</h1>
    <p class="lead">סרוק באפליקציית אימות (Google Authenticator, Microsoft Authenticator, Aegis) והקלד את הקוד שהיא מציגה.</p>
    <div class="qr-row">${qr}<div class="secret">או הקלד את הסוד ידנית:<span class="mono">${esc(secretBlocks(state.secret))}</span>
      ${url ? `<span style="display:block;margin-top:6px">חשבון: <a class="mono" href="${esc(url)}">${esc(url)}</a></span>` : ""}
    </div></div>
    <div class="f"><label for="setup-code">הקוד מהאפליקציה</label>
      <input id="setup-code" class="mono" inputmode="numeric" maxlength="6" autocomplete="one-time-code" value="${esc(state.setupCode || "")}"></div>
    ${loginMsg(state.error)}
    <button class="btn" type="submit" id="setup-verify">אימות והפעלה</button>
  </form>`;
}

function loginCodesHtml(state) {
  const codes = state.backupCodes || [];
  const ready = setupContinueEnabled(state.savedAck);
  return `<form id="codes-form">
    <h1 class="title sub">האימות הדו-שלבי פעיל</h1>
    <p class="lead">מעכשיו כל כניסה תבקש קוד. אם הטלפון אובד, קוד גיבוי הוא הדרך היחידה להיכנס.</p>
    <div class="backup">
      <h2>8 קודי גיבוי — שמור אותם</h2>
      <p>כל קוד עובד פעם אחת. הם לא יוצגו שוב.</p>
      <div class="codes">${codes.map((c) => `<span>${esc(c)}</span>`).join("")}</div>
    </div>
    <div class="actions2">
      <button class="btn sec" type="button" id="codes-print">הדפס</button>
      <button class="btn sec" type="button" id="codes-copy">העתק</button>
    </div>
    <label class="check" style="margin-top:18px"><input type="checkbox" id="saved-ack"${state.savedAck ? " checked" : ""}> שמרתי את הקודים במקום בטוח</label>
    <button class="btn" type="submit" id="setup-done"${ready ? "" : " disabled"}>המשך לקונסולה</button>
  </form>`;
}

function loginHtml(state) {
  const s = state || LOGIN;
  if (s.screen === "change") return loginChangeHtml(s);
  if (s.screen === "mfa") return loginMfaHtml(s);
  if (s.screen === "setup") return loginSetupHtml(s);
  if (s.screen === "codes") return loginCodesHtml(s);
  return loginFormHtml(s);
}

function paintLoginFoot() {
  const host = $("#login-foot-host");
  if (host) host.textContent = (ME && ME.server_name) || (typeof location !== "undefined" && location.hostname) || "";
  const ver = $("#login-foot-ver");
  if (ver) ver.textContent = (ME && ME.version) || "";
  const label = $("#login-foot-tls-label");
  const tls = $("#login-foot-tls");
  const fp = ME && ME.tls && ME.tls.fingerprint_sha256;
  if (label) label.textContent = fp ? "TLS תעודה עצמית" : "";
  if (tls) {
    tls.textContent = fp ? fp.slice(0, 23) + "…" : "";
    tls.title = fp ? "SHA-256 " + fp : "";
  }
}

function loginRender(state) {
  if (state) Object.assign(LOGIN, state);
  const body = $("#login-body");
  if (body) body.innerHTML = loginHtml(LOGIN);
  paintLoginFoot();
  const first = body && body.querySelector("input:not([type=checkbox])");
  if (first && typeof first.focus === "function") first.focus();
}

function showLogin() {
  ME = null;
  OVERVIEW = IMAGES = FOLDERS = MACHINES = GROUPS = HEALTH = NETCFG = USERS = JOURNAL = SETTINGS = null; SETTINGS_DRAFT = {}; LOG.rows = null;
  SESSION_MACHINES = {group:null,list:[]};
  CAPTURE_TASKS = [];
  clearInterval(pollTimer);
  closeDrawer(); closeModal();
  clearInterval(idleTimer);
  $("#login").classList.remove("hidden");
  $("#app").classList.add("hidden");
  applyTheme("auto");   // #1093: מסך הכניסה לפי המערכת; המטמון נמחק
  const keptUser = LOGIN.username || "";
  const keptErr = LOGIN.error || "";
  LOGIN = { screen: "login", username: keptUser, remember: false, error: keptErr,
            challenge: "", secret: "", otpauth: "", svg: "", backupCodes: [],
            savedAck: false, otpBackup: false, otp: ["", "", "", "", "", ""] };
  loginRender();
}

async function enterApp() {
  ME = await api("/me");
  LOGIN = { screen: "login", username: "", remember: false, error: "",
            challenge: "", secret: "", otpauth: "", svg: "", backupCodes: [],
            savedAck: false, otpBackup: false, otp: ["", "", "", "", "", ""] };
  await showApp();
}

async function showApp() {
  applyServerTheme(ME);
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  closeUserMenu();
  closeDrawer();
  closeModal();
  const userBtn = document.querySelector(".topbar .user");
  if (userBtn) {
    const avatar = userBtn.querySelector(".avatar");
    const label = userBtn.querySelector("span");
    if (avatar) avatar.textContent = ME.username.slice(0, 2);
    if (label) label.textContent = ME.username;
  }
  const menuHead = document.querySelector("#userMenu .user-menu-head strong");
  const menuSub = document.querySelector("#userMenu .user-menu-head small");
  if (menuHead) menuHead.textContent = ME.username;
  if (menuSub) menuSub.textContent = ME.role === "admin" ? "מנהל" : "הפצה";
  // ‏#915: הגרסה מגיעה מ-/me (תג git של עץ השרת) — לא מחרוזת ב-HTML.
  // ‏null = אין תג על העץ, ואז לא מציגים מספר שאין לו מקור.
  const versionEl = document.getElementById("serverVersion");
  if (versionEl) versionEl.textContent = ME.version || "";
  // ‏#703 (tracer 5): מצב ה-TLS של הקונסולה מ-/me. טביעת האצבע (SHA-256
  // של התעודה, כמו בדפדפן) — מקוצרת בשורת הסטטוס, מלאה ב-title
  // וב"פרטי Session" — כדי שהאישור החד-פעמי של התעודה יהיה השוואה.
  const tlsEl = document.getElementById("serverTls");
  if (tlsEl) {
    const fp = ME.tls && ME.tls.fingerprint_sha256;
    tlsEl.innerHTML = fp ? `TLS תעודה עצמית · <span dir="ltr">${esc(fp.slice(0, 23))}…</span>` : "";
    tlsEl.title = fp ? `SHA-256 ${fp}` : "";
  }
  document.querySelectorAll("[data-admin]").forEach(
    (el) => el.classList.toggle("hidden", ME.role !== "admin"));
  // רכיבים מותנים ביכולת נגזרת-שרת (#655/#723): מוצגים רק כשהדגל
  // המתאים ב-capabilities הוא true. הדגל הוא הסמכות — לא התפקיד לבדו.
  const caps = ME.capabilities || {};
  document.querySelectorAll("[data-cap]").forEach(
    (el) => el.classList.toggle("hidden", !caps[el.dataset.cap]));
  pages.deploy.tabs = deployTabs();
  await loadLogo();
  applyServerName();
  renderActivity();
  selectPageById("home");
  if (isAdmin()) { loadMachines(); loadHealth(); }
  loadImages();
  populateSidebarImages();
  populateSidebarGroups();
  populateSidebarSecondaries().catch((e) => toast("טעינת השרתים המשניים נכשלה: " + e.message));
  if (isAdmin() && typeof loadNet === "function") {
    loadNet().catch((e) => toast("טעינת כרטיסי הרשת נכשלה: " + e.message));
  }
  updateAlertBadge();
  startIdleWatch();
  startStatusWatch();
}

async function loginFetch(path, body) {
  const response = await fetch("/api/console" + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body || {}),
  });
  let data = {};
  try { data = await response.json(); } catch (e) {}
  return { ok: response.ok, status: response.status, body: data,
           retryAfter: response.headers.get("Retry-After") };
}

async function loginSubmitCredentials() {
  const username = ($("#login-user") && $("#login-user").value || "").trim();
  const password = ($("#login-pass") && $("#login-pass").value) || "";
  const remember = !!( $("#login-remember") && $("#login-remember").checked );
  LOGIN.username = username;
  LOGIN.remember = remember;
  const r = await loginFetch("/login", { username, password });
  const next = loginStateFromResponse(r.status, r.body, r.retryAfter, LOGIN);
  if (next.screen === "app") { await enterApp(); return; }
  if (next.screen === "setup") { await loginStartSetup(next); return; }
  loginRender(next);
}

async function loginSubmitPassword() {
  const current = ($("#pw-change-current") && $("#pw-change-current").value) || "";
  const next = ($("#pw-change-new") && $("#pw-change-new").value) || "";
  const confirm = ($("#pw-change-confirm") && $("#pw-change-confirm").value) || "";
  if (!passwordCanSubmit(current, next, confirm)) return;
  const r = await loginFetch("/me/password", { current_password: current, new_password: next });
  if (!r.ok) { loginRender({ error: r.body.detail || r.body.message_he || "החלפת הסיסמה נכשלה" }); return; }
  if (r.body.mfa_enrollment_required) { await loginStartSetup({ screen: "setup", username: LOGIN.username }); return; }
  await enterApp();
}

function loginReadOtp() {
  if (LOGIN.otpBackup) return (($("#mfa-backup") && $("#mfa-backup").value) || "").trim();
  const cells = document.querySelectorAll("#otp input");
  let code = "";
  cells.forEach((c) => { code += (c.value || "").replace(/\D/g, "").slice(0, 1); });
  return code;
}

async function loginSubmitMfa() {
  const remember = !!( $("#mfa-remember") && $("#mfa-remember").checked );
  LOGIN.remember = remember;
  const code = loginReadOtp();
  const r = await loginFetch("/login/mfa", {
    challenge: LOGIN.challenge, code, remember_browser: remember,
  });
  if (r.status === 401) {
    loginRender(mfaRejectedState(r.body, LOGIN));
    return;
  }
  const next = loginStateFromResponse(r.status, r.body, r.retryAfter, LOGIN);
  if (next.screen === "app") { await enterApp(); return; }
  loginRender(next);
}

async function loginStartSetup(base) {
  const r = await loginFetch("/me/mfa/setup", {});
  if (!r.ok) { loginRender({ screen: "setup", username: (base && base.username) || LOGIN.username,
    error: r.body.detail || "הגדרת MFA נכשלה" }); return; }
  loginRender({
    screen: "setup", username: (base && base.username) || LOGIN.username, error: "",
    secret: r.body.secret || "", otpauth: r.body.otpauth_url || "", svg: r.body.svg || "",
    setupCode: "",
  });
}

async function loginSubmitSetup() {
  const code = (($("#setup-code") && $("#setup-code").value) || "").trim();
  const v = await loginFetch("/me/mfa/verify", { code });
  if (!v.ok) { loginRender({ error: "קוד שגוי", setupCode: code }); return; }
  const en = await loginFetch("/me/mfa/enable", { code });
  if (!en.ok) { loginRender({ error: en.body.detail || "קוד שגוי", setupCode: code }); return; }
  loginRender({ screen: "codes", backupCodes: en.body.backup_codes || [], savedAck: false, error: "" });
}

async function loginOnSubmit(event) {
  event.preventDefault();
  const id = event.target && event.target.id;
  try {
    if (id === "login-form") await loginSubmitCredentials();
    else if (id === "pw-change-form") await loginSubmitPassword();
    else if (id === "mfa-form") await loginSubmitMfa();
    else if (id === "setup-form") await loginSubmitSetup();
    else if (id === "codes-form") {
      if (!setupContinueEnabled(LOGIN.savedAck)) return;
      await enterApp();
    }
  } catch (error) {
    loginRender({ error: error.message });
  }
}

function loginToggleEye(eye) {
  const input = document.getElementById(eye.dataset.pw);
  if (!input) return;
  const showing = input.type === "text";
  input.type = showing ? "password" : "text";
  eye.textContent = showing ? "הצג" : "הסתר";
}

function loginOnClick(event) {
  const eye = event.target.closest && event.target.closest("[data-pw]");
  if (eye) { loginToggleEye(eye); return; }
  if (event.target.id === "mfa-use-backup") {
    loginRender({ otpBackup: true, error: "" });
    return;
  }
  if (event.target.id === "codes-copy") {
    const text = (LOGIN.backupCodes || []).join("\n");
    if (navigator.clipboard && navigator.clipboard.writeText)
      navigator.clipboard.writeText(text).then(() => toast("הקודים הועתקו"), () => toast("ההעתקה נכשלה"));
    else toast("ההעתקה אינה זמינה");
    return;
  }
  if (event.target.id === "codes-print") {
    printBackupCodes(LOGIN.backupCodes || []);
  }
}

function printBackupCodes(codes) {
  const w = window.open("", "_blank");
  if (!w) { toast("הדפדפן חסם את חלון ההדפסה"); return; }
  w.document.write("<!doctype html><html dir=\"rtl\"><head><meta charset=\"utf-8\"><title>קודי גיבוי ImageCtl</title></head><body><h1>קודי גיבוי</h1><pre dir=\"ltr\">"
    + codes.map((c) => String(c)).join("\n") + "</pre></body></html>");
  w.document.close();
  w.print();
}

function loginOnInput(event) {
  const t = event.target;
  if (!t) return;
  if (t.id === "pw-change-current" || t.id === "pw-change-new" || t.id === "pw-change-confirm") {
    LOGIN.current = ($("#pw-change-current") && $("#pw-change-current").value) || "";
    LOGIN.next = ($("#pw-change-new") && $("#pw-change-new").value) || "";
    LOGIN.confirm = ($("#pw-change-confirm") && $("#pw-change-confirm").value) || "";
    const focused = t.id;
    loginRender();
    const again = document.getElementById(focused);
    if (again) { again.focus(); if (typeof again.setSelectionRange === "function") again.setSelectionRange(again.value.length, again.value.length); }
    return;
  }
  if (t.id === "saved-ack") {
    LOGIN.savedAck = !!t.checked;
    const btn = $("#setup-done");
    if (btn) btn.disabled = !setupContinueEnabled(LOGIN.savedAck);
    return;
  }
  if (t.dataset && t.dataset.otp != null) {
    t.value = t.value.replace(/\D/g, "").slice(0, 1);
    const i = Number(t.dataset.otp);
    if (t.value && t.parentNode) {
      const next = t.parentNode.querySelector(`[data-otp="${i + 1}"]`);
      if (next) next.focus();
    }
  }
}

function loginOnKeydown(event) {
  const t = event.target;
  if (!t || t.dataset.otp == null) return;
  if (event.key === "Backspace" && !t.value && t.parentNode) {
    const i = Number(t.dataset.otp);
    const prev = t.parentNode.querySelector(`[data-otp="${i - 1}"]`);
    if (prev) prev.focus();
  }
}

function loginOnPaste(event) {
  const t = event.target;
  if (!t || t.dataset.otp == null) return;
  const d = otpFromPaste((event.clipboardData && event.clipboardData.getData("text")) || "");
  if (d.filter(Boolean).length < 2) return;
  event.preventDefault();
  const cells = t.parentNode.querySelectorAll("[data-otp]");
  cells.forEach((c, k) => { c.value = d[k] || ""; });
  const last = Math.min(d.filter(Boolean).length, 5);
  if (cells[last]) cells[last].focus();
}

(function bindLogin() {
  const root = $("#login");
  if (!root || root.dataset.loginBound) return;
  root.dataset.loginBound = "1";
  root.addEventListener("submit", loginOnSubmit);
  root.addEventListener("click", loginOnClick);
  root.addEventListener("input", loginOnInput);
  root.addEventListener("keydown", loginOnKeydown);
  root.addEventListener("paste", loginOnPaste);
  loginRender({ screen: "login" });
})();

$("#logout").addEventListener("click", async () => { await post("/logout"); ME = null; showLogin(); });

/* ---------- shell: עמודים, ניווט, drawer, modal ---------- */

function pagePlaceholder() {
  return '<div class="card"><div class="card-b">טוען נתונים…</div></div>';
}

function emptyDataCard() {
  return `<div class="grid"><div class="span-12"><div class="card"><div class="card-b"><div class="empty">אין נתונים להצגה</div></div></div></div></div>`;
}

function formatGB(bytes) {
  if (bytes == null) return "-";
  const n = Number(bytes);
  if (!Number.isFinite(n) || n < 0) return "—";
  return (n / (1024 ** 3)).toFixed(1) + " GB";
}

function fmtBytes(n) {
  if (n == null) return "–";
  let v = Number(n);
  if (!Number.isFinite(v) || v < 0) return "–";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return v.toFixed(v >= 100 || i === 0 ? 0 : 1) + " " + units[i];
}

function librarySize(bytes) {
  return bytes == null ? "לא ידוע" : fmtBytes(bytes);
}

/* #953: "נכנס לדיסק מ-X GB" — GB עשרוני (כמו על מדבקת הכונן: 256GB =
   256×10⁹), מעוגל כלפי **מעלה** — הרצפה, לא קירוב. null = לא ניתן לקבוע. */
function fitGB(bytes) {
  const n = Number(bytes);
  if (bytes == null || !Number.isFinite(n) || n < 0) return null;
  return Math.ceil(n / 1e9) + " GB";
}

/* #953: שלושת המספרים של אימג' — לאיזה דיסק הוא נכנס (מ-`min_target_bytes`
   הנגזר, tooltip בבייטים), כמה תפוס בו (`used_bytes`; null = "לא ידוע",
   לא 0 — מחיצה שלא נעגנה, #84), וכמה הוא שוקל בשרת. פונקציית רינדור
   קטנה כדי שתעבור כמו שהיא למסך של #954. */
function imageSizeCells(img) {
  const fit = fitGB(img.min_target_bytes);
  const fitHtml = fit
    ? `<span title="${esc(String(img.min_target_bytes))} בייט">${esc(fit)}</span>`
    : "לא ידוע";
  return `נכנס לדיסק מ-${fitHtml} · בשימוש ${esc(librarySize(img.used_bytes))} · בשרת ${esc(librarySize(img.total_compressed_bytes))}`;
}

function imageOsLabel(os) {
  if (os === "windows") return "Windows";
  if (os === "linux") return "לינוקס";
  return os && os !== "unknown" ? os : "—";
}

function findImage(id) {
  if (id == null || id === "") return null;
  try { id = decodeURIComponent(id); } catch (e) {}
  return (IMAGES || []).find((x) => x.id === id) || null;
}

function imagesVisible() {
  const all = IMAGES || [];
  if (!IMAGES_FOLDER) return all;
  return all.filter((m) => m.folder === IMAGES_FOLDER);
}

function sessionImage(s) {
  return (s && (s.image_name || s.label || s.image_id)) || "";
}

function sessionGroup(s) {
  return (s && (s.group_label || s.group || s.class || s.group_id)) || "";
}

function sessionExpected(s) {
  if (!s) return 0;
  const n = s.expected_clients ?? s.expected;
  return Number(n) || 0;
}

function sessionProgressPct(s) {
  if (!s) return null;
  if (typeof s.progress === "number" && Number.isFinite(s.progress)) {
    return Math.max(0, Math.min(100, Math.round(s.progress)));
  }
  const members = s.members || [];
  let known = 0, sum = 0;
  for (const m of members) {
    if (m.done || m.state === "done") { sum += 100; known++; continue; }
    const p = Progress.view(m);
    if (p.percent != null) { sum += p.percent; known++; }
  }
  if (known) return Math.round(sum / known);
  return null;
}

function stateLabel(state) {
  if (state === "running") return "פעיל";
  if (state === "open") return "פתוח";
  if (state === "closed") return "נסגר";
  return state || "—";
}

function stateClass(state) {
  if (state === "running") return "ok";
  if (state === "open") return "warn";
  if (state === "closed") return "err";
  return "";
}

function memberRows(members) {
  if (!members?.length) return '<div class="empty">No joined machines</div>';
  return members.map(m => memberRow(m, OVERVIEW?.session || {}, stuckNote(OVERVIEW?.session?.stuck,m.mac))).join("");
}

/* עיקרון 5 (#517): קריאת /overview שנכשלה מסמנת את הכרטיס כישן — ולא
   חוזרת בשקט. "לא נקרא" ו"נקרא, ריק" הם שני מצבים נבדלים. */
function markStatusStale() {
  overviewError = "Server status could not be read. Last update: " + (overviewLastOk || "never");
  if (ME && (current === "home" || current === "deploy")) renderCurrent();
}
function markStatusFresh() {
  overviewError = ""; overviewLastOk = new Date().toLocaleTimeString();
}
async function refreshStatus() {
  let data;
  try { data = await api("/overview"); } catch (e) {
    // #826: the task panel must show the failed read too — the old loadOverview
    // did this in `finally`, and the rewrite dropped it on the failure path.
    markStatusStale(); if (typeof renderActivity === "function") renderActivity(); return;
  }
  if (!ME) return;
  OVERVIEW = data; markStatusFresh();
  if (current === "home" || current === "deploy") renderCurrent();
  if (typeof renderActivity === "function") renderActivity();
}

async function loadImages() {
  try {
    [IMAGES, FOLDERS] = await Promise.all([api("/images"), api("/folders")]);
    if (IMAGES_FOLDER && !(FOLDERS || []).some((f) => f.name === IMAGES_FOLDER)) {
      IMAGES_FOLDER = null;
    }
    populateSidebarImages();
    if (current === "images") renderCurrent();
  } catch (e) {
    toast("טעינת הספרייה נכשלה: " + e.message);
  }
}

async function loadMachines() {
  try {
    [MACHINES, GROUPS, DISK_FAILURES, SHRINK_RECORDS] = await Promise.all([
      api("/machines"), api("/groups"), api("/disk-failures"), api("/shrink-records")]);
    // ‏#954 גל 3: IP / נראה לאחרונה / שלב אתחול מ-/net, "מחובר" מ-/monitor/machines.
    // קריאה שנכשלה = "לא נקרא" (null), לא רשימה ריקה (עיקרון 5).
    // ‏גל 3א: מצב המגירות בזמן סבב מ-/room (אובייקט המשכפלים); הקליטות מ-/tasks (אובייקט הבנייה).
    const [net, mon, room] = await Promise.allSettled([api("/net"), api("/monitor/machines"), api("/room")]);
    NET = net.status === "fulfilled" && Array.isArray(net.value) ? net.value : null;   // תשובה שאינה רשימה = לא נקרא
    NET_ERR = net.status === "rejected" ? (net.reason && net.reason.message) || "" : "";
    MONITOR_ROWS = mon.status === "fulfilled" && Array.isArray(mon.value) ? mon.value : null;
    ROOM = room.status === "fulfilled" && room.value && Array.isArray(room.value.machines) ? room.value : null;
    ROOM_KEY = JSON.stringify(ROOM);
    if (isAdmin()) await loadCaptures().catch(() => {});   // כשל = CAPTURE_TASKS_READ נשאר כפי שהיה
    populateSidebarGroups();
    updateAlertBadge();
    if (current === "machines" || current === "network") renderCurrent();   // ‏#954 גל 8: "רשום"/"הסר" ברשת ההפצה
  } catch (e) {
    toast("טעינת המחשבים נכשלה: " + e.message);
  }
}

async function loadHealth() {
  try {
    // ‏#1000: fetch ישיר ולא api() — זמן המדידה מגיע בכותרת X-Health-Checked-At
    // (שעון השרת, ISO); הגוף נשאר המערך שכל הקוראים תלויים בו.
    const response = await fetch("/api/console/health", { credentials: "same-origin" });
    if (response.status === 401) { showLogin(); throw new Error("לא מחובר"); }
    if (!response.ok) {
      let detail = "שגיאה " + response.status;
      try { detail = (await response.json()).detail || detail; } catch (e) {}
      const error = new Error(detail); error.status = response.status; throw error;
    }
    const checkedAt = response.headers.get("X-Health-Checked-At");
    HEALTH = await response.json();
    healthError = "";
    // חותמת שלא הגיעה = "זמן המדידה לא נמסר", לא שעון הדפדפן (עיקרון 5).
    HEALTH_AT = checkedAt ? fmtWhenShort(checkedAt) : "";
    updateAlertBadge();
  } catch (e) {
    HEALTH = null;
    healthError = e.message;
    toast("טעינת הבריאות נכשלה: " + e.message);
  }
  if (current === "health") await loadHealthUpdate();
  if (current === "home" || current === "health") renderCurrent();
}

/* ‏#954 גל 5: כרטיס "גרסה ועדכון" בדף הבריאות — /update ו-/update/status
   (admin). ‏UPDATE_INFO משותף עם confirmUpdateAction (שם השרת להקלדה). */
async function loadHealthUpdate() {
  try {
    const info = await api("/update");
    HEALTH_UPDATE = info;
    UPDATE_INFO = info;
    UPDATE_STATUS = info.enabled ? await api("/update/status") : null;
    updateError = "";
  } catch (e) {
    HEALTH_UPDATE = null;
    updateError = e.message;
  }
}

async function loadPorts() {
  // #822: כשל קריאה הוא מצב משלו (portsError) ולא "אין פורטים" (עיקרון 5).
  try {
    PORTS = await api("/ports");
    portsError = "";
    PORTS_AT = clockNow();
  } catch (e) {
    PORTS = null;
    portsError = e.message;
    toast("טעינת הפורטים נכשלה: " + e.message);
  }
  // ‏#954 גל 5: המתגים — SSH (/ssh), מוניטור (/monitor/settings) ו-DHCP
  // (/net/interfaces דרך loadNet של net.js). שלוש קריאות, שלושה כשלים
  // נפרדים: מה שלא נקרא מוצג "לא נקרא", לא "כבוי" (עיקרון 5).
  if (isAdmin()) {
    const [ssh, mon, net] = await Promise.allSettled([api("/ssh"), api("/monitor/settings"), loadNet()]);
    SSH_STATE = ssh.status === "fulfilled" ? ssh.value : null;
    sshError = ssh.status === "fulfilled" ? "" : ssh.reason.message;
    PORTS_MONITOR = mon.status === "fulfilled" ? mon.value : null;
    portsMonitorError = mon.status === "fulfilled" ? "" : mon.reason.message;
    PORTS_NICS = net.status === "fulfilled" ? NICS : null;
    portsNicsError = net.status === "fulfilled" ? "" : net.reason.message;
  }
  if (current === "ports") renderCurrent();
  if (current === "network") renderCurrent();   // ‏#954 גל 8: מתג SSH לשרת גם בחיבורים פיזיים
}

async function loadMonitor() {
  // #822: "מחובר" מגיע מהשרת (monitor.py, חלון 90ש'), לא מחישוב בדפדפן.
  // כשל קריאה הוא מצב משלו (monitorError) ולא "אין מכונות" (עיקרון 5).
  try {
    if (!MACHINES || !GROUPS) await loadMachines();   // monitorMachine() נשען עליהם
    const [settings, machines] = await Promise.all([api("/monitor/settings"), api("/monitor/machines")]);
    MONITOR = { settings, machines };
    monitorError = "";
  } catch (e) {
    MONITOR = null;
    monitorError = e.message;
    toast("טעינת המוניטור נכשלה: " + e.message);
  }
  if (current === "monitor") renderCurrent();
}

async function loadUsersData() {
  // ‏#954 גל 6: כשל קריאה הוא מצב משלו (usersError), לא "אין משתמשים" (עיקרון 5).
  try { USERS = await api("/users"); usersError = ""; }
  catch (e) { USERS = null; usersError = e.message; toast("טעינת המשתמשים נכשלה: " + e.message); }
  if (current === "permissions") renderCurrent();
}

function machineName(m) {
  return (m && (m.suffix || m.name)) || "";
}

function machineGroupId(m) {
  return (m && (m.group || m.group_id)) || "";
}

function groupLabel(id) {
  const g = (GROUPS || []).find((x) => x.id === id);
  return g ? g.label : (id || "—");
}

function findMachine(mac) {
  if (mac == null || mac === "") return null;
  try { mac = decodeURIComponent(mac); } catch (e) {}
  const want = String(mac).toLowerCase();
  return (MACHINES || []).find((m) => String(m.mac || "").toLowerCase() === want) || null;
}

function sortMachinesBySuffix(list) {
  return list.slice().sort((a, b) => {
    const sa = String(machineName(a));
    const sb = String(machineName(b));
    const na = /^\d+$/.test(sa);
    const nb = /^\d+$/.test(sb);
    if (na !== nb) return na ? 1 : -1;
    if (na && nb) return Number(sa) - Number(sb);
    return sa.localeCompare(sb, "en", { sensitivity: "base" });
  });
}

function groupSelectOptions() {
  return (GROUPS || []).map((g) =>
    `<option value="${esc(g.id)}">${esc(g.label)}</option>`).join("");
}

function sidebarChildOpen(tree) {
  const wasOpen = {};
  tree.querySelectorAll(":scope > .inventory-children").forEach((el) => {
    wasOpen[el.id] = !(el.hasAttribute("hidden") || el.style.display === "none");
  });
  return wasOpen;
}

function populateSidebarGroups() {
  const groups = GROUPS || [];
  const machines = MACHINES || [];
  fillSidebarTree("classesTree", classroomsOn() ? groups.filter((g) => g.role === "classroom") : [], machines, "group");
  fillSidebarTree("buildTree", groups.filter((g) => g.role === "build"), machines, "build");
  fillSidebarTree("clonerTree", groups.filter((g) => g.role === "cloner"), machines, "copy");
}

function populateSidebarImages() {
  const tree = document.getElementById("imagesTree");
  if (!tree || !FOLDERS) return;
  if (!FOLDERS.length) {
    tree.innerHTML = '<div class="inventory-node muted">אין תיקיות</div>';
    return;
  }
  const wasOpen = sidebarChildOpen(tree);
  tree.innerHTML = FOLDERS.map((f, i) => {
    const fid = "imgfolder" + i;
    const imgs = (IMAGES || []).filter((im) => im.folder === f.name);
    const open = !!wasOpen[fid];
    if (!imgs.length) {
      return `<div class="inventory-node" role="treeitem" tabindex="0"><span class="tree-arrow-sp"></span><span>${uiIcon("folder")}</span><span>${esc(f.name)}</span></div>`;
    }
    const rows = imgs.map((im) => {
      const idEnc = encodeId(im.id || "");
      return `<div class="inventory-node" role="treeitem" tabindex="0" onclick="openImageDetail('${idEnc}')"><span class="tree-arrow-sp"></span><span>${uiIcon("image")}</span><span>${esc(im.name)}</span></div>`;
    }).join("");
    return `<div class="inventory-node" role="treeitem" tabindex="0" aria-expanded="${open}" ondblclick="toggleInventoryGroup(this,'${fid}')"><span class="tree-arrow" data-open="${open}" onclick="event.stopPropagation();toggleInventoryGroup(this.closest('.inventory-node'),'${fid}')">${open ? "▾" : "▸"}</span><span>${uiIcon("folder")}</span><span>${esc(f.name)}</span></div><div id="${fid}" class="inventory-children"${open ? "" : " hidden"}>${rows}</div>`;
  }).join("");
}

function fillSidebarTree(treeId, groups, machines, icon) {
  const tree = document.getElementById(treeId);
  if (!tree) return;
  const wasOpen = sidebarChildOpen(tree);
  tree.innerHTML = groups.map((g) => {
    const boxId = "sg-" + g.id;
    const open = !!wasOpen[boxId];
    const kids = sortMachinesBySuffix(machines.filter((m) => machineGroupId(m) === g.id));
    const gidEnc = encodeId(g.id);
    if (!kids.length) {
      return `<div class="inventory-node" role="treeitem" tabindex="0" onclick="selectMachinesGroup('${gidEnc}')"><span class="tree-arrow-sp"></span><span>${uiIcon(icon)}</span><span>${esc(g.label)}</span></div>`;
    }
    const childHtml = kids.map((m) => {
      const macEnc = encodeId(m.mac);
      const label = machineName(m) || m.mac;
      return `<div class="inventory-node" role="treeitem" tabindex="0" onclick="openMachineDetail('${macEnc}')"><span class="tree-arrow-sp"></span><span>${uiIcon("machine")}</span><span>${esc(label)}</span></div>`;
    }).join("");
    return `<div class="inventory-node" role="treeitem" tabindex="0" aria-expanded="${open}" onclick="selectMachinesGroup('${gidEnc}')" ondblclick="toggleInventoryGroup(this,'${esc(boxId)}')"><span class="tree-arrow" data-open="${open}" onclick="event.stopPropagation();toggleInventoryGroup(this.closest('.inventory-node'),'${esc(boxId)}')">${open ? "▾" : "▸"}</span><span style="display:contents"><span>${uiIcon(icon)}</span><span>${esc(g.label)}</span></span></div><div id="${esc(boxId)}" class="inventory-children"${open ? "" : " hidden"}>${childHtml}</div>`;
  }).join("");
}

function machineIsFailed(m) {
  const s = String((m && (m.status || m.state)) || "").toLowerCase();
  return s === "failed" || s === "fail" || s === "error" || s === "err" || s === "bad" || s === "נכשל";
}

function updateAlertBadge() {
  const badge = document.getElementById("alertBadge");
  if (!badge) return;
  let n = 0;
  for (const m of MACHINES || []) {
    if (machineIsFailed(m)) n++;
  }
  if (Array.isArray(HEALTH)) {
    for (const c of HEALTH) {
      if (c.state === "warn" || c.state === "bad") n++;
    }
  }
  if (n > 0) {
    badge.textContent = String(n);
    badge.classList.add("has-alerts");
    badge.removeAttribute("hidden");
  } else {
    badge.textContent = "";
    badge.classList.remove("has-alerts");
    badge.setAttribute("hidden", "");
  }
}

/* ‏#936: שם השרת הראשי בצומת העליון מגיע מ-/me (‏`server_name`: ההגדרה
   אם המנהל קבע, אחרת שם המארח) — לא מחרוזת דמו ב-HTML ולא localStorage. */
function applyServerName() {
  const el = document.getElementById("primaryServerName");
  if (el && ME && ME.server_name) el.textContent = ME.server_name;
}

function renameServer(el) {
  if (!el || !isAdmin()) return;
  const currentName = el.textContent || "";
  openModalContent(
    "שינוי שם שרת",
    `<div class="form"><div class="field full"><label>שם השרת</label><input id="serverNameInput" autocomplete="off"></div></div>`,
    "שמור",
    async () => {
      const input = document.getElementById("serverNameInput");
      const name = input ? input.value.trim() : "";
      if (!name) { toast("שם לא יכול להיות ריק"); return; }
      try {
        await post("/settings", { server_name: name });
      } catch (e) { toast("שמירת השם נכשלה: " + e.message); return; }
      el.textContent = name;
      if (ME) ME.server_name = name;
      closeModal();
    }
  );
  const input = document.getElementById("serverNameInput");
  if (input) { input.value = currentName; input.focus(); }
}

/* ‏#936: צומת שרת לכל משני מ-GET /storage-nodes, כמו vCenter. הנתיב הוא
   admin+standalone (deploy → 403, משני → 409): במקרים האלה אין צמתים —
   לא שגיאה אדומה. מצב החיבור נמדד מול המשני (‏/machines, ‏connected) אחרי
   שהצמתים כבר על המסך; משני שלא ענה נשאר בעץ עם נקודה אדומה. */
const BRANCH_VIEWS = [
  ["overview", "סקירה", "home"], ["machines", "מחשבים", "machine"],
  ["images", "אימג'ים", "image"], ["transfers", "העברות", "deploy"],
];
let BRANCH_NODE = null;

function secondaryDotClass(n, connected) {
  if (n.disabled_at) return "status";
  if (connected === true) return "status ok";
  if (connected === false) return "status err";
  return "status";
}

async function populateSidebarSecondaries() {
  const host = document.getElementById("secondaryServers");
  if (!host) return;
  const caps = (ME && ME.capabilities) || {};
  if (!caps.enroll_secondary) { host.innerHTML = ""; return; }
  let nodes = [];
  try { nodes = await api("/storage-nodes"); }
  catch (e) {
    if (e.status === 403 || e.status === 409) { host.innerHTML = ""; return; }
    throw e;
  }
  const wasOpen = sidebarChildOpen(host);
  host.innerHTML = nodes.map((n) => {
    const boxId = "srv-" + n.id;
    const open = !!wasOpen[boxId];
    const idEnc = encodeId(n.id);
    const kids = BRANCH_VIEWS.map(([view, label, icon], i) =>
      `<div class="inventory-node" data-branch-view="${esc(view)}" role="treeitem" tabindex="0" onclick="openBranchView('${idEnc}',${i},this)"><span class="tree-arrow-sp"></span><span>${uiIcon(icon)}</span><span>${esc(label)}</span></div>`).join("");
    const title = n.disabled_at ? "מושבת" : "מצב החיבור נבדק…";
    return `<div class="inventory-node server-node" data-secondary="${esc(n.id)}" role="treeitem" tabindex="0" aria-expanded="${open}" onclick="openBranchView('${idEnc}',0,this)" ondblclick="toggleInventoryGroup(this,'${esc(boxId)}')"><span class="tree-arrow" data-open="${open}" onclick="event.stopPropagation();toggleInventoryGroup(this.closest('.inventory-node'),'${esc(boxId)}')">${open ? "▾" : "▸"}</span><span>${uiIcon("server")}</span><strong class="server-name">${esc(n.label)}</strong><span class="${secondaryDotClass(n, null)}" data-secondary-status="${esc(n.id)}" title="${esc(title)}" aria-label="${esc(title)}"><i></i></span></div><div id="${esc(boxId)}" class="inventory-children" role="group"${open ? "" : " hidden"}>${kids}</div>`;
  }).join("");
  await Promise.all(nodes.filter((n) => !n.disabled_at).map((n) =>
    api(`/storage-nodes/${encodeId(n.id)}/machines`)
      .then((a) => markSecondaryStatus(n, !!a.connected, a.error))
      .catch((e) => markSecondaryStatus(n, false, e.message))));
}

function markSecondaryStatus(n, connected, error) {
  SECONDARY_STATUS[n.id] = { connected, error };   // ‏#954: "דורש טיפול" בסקירה קורא מכאן
  const dot = document.querySelector(`[data-secondary-status="${CSS.escape(n.id)}"]`);
  if (!dot) return;
  dot.className = secondaryDotClass(n, connected);
  const title = connected ? "מחובר" : ("לא מחובר" + (error ? ": " + error : ""));
  dot.setAttribute("title", title);
  dot.setAttribute("aria-label", title);
}

function openBranchView(nid, tabIndex, el) {
  try { nid = decodeURIComponent(nid); } catch (e) {}
  BRANCH_NODE = nid;
  selectPage(el || null, "branch");
  if (tabIndex) activateTab(tabIndex);
}

function renderCurrent() {
  const page = pages[current];
  if (!page || !pageAllowed(current)) return;
  // ‏#954: הסקירה נצבעת מחדש כל 2 שניות (refreshStatus) — בלי זה הגלילה
  // קופצת לראש הדף בכל דגימה. הגולל הוא .page (עמוד חדש) או .scroll (ישן).
  const scroller = document.querySelector("#content .page, #content .scroll");
  const scrollTop = scroller ? scroller.scrollTop : 0;
  document.getElementById("content").innerHTML = layout(page, currentTab);
  if (scrollTop) {
    const again = document.querySelector("#content .page, #content .scroll");
    if (again) again.scrollTop = scrollTop;
  }
  const search = document.getElementById("globalSearch");
  if (search) search.value = searchQuery || "";
  if (current === "network") wireNetPage();
  wireRestoredPage();
}

/* ---------- סקירה כללית (#954 גל 1) ----------
   נבנה לפי docs/design/console-redesign/home.md: שלוש שאלות — השרת בסדר?
   מה רץ עכשיו? מה דורש אותי? — מה-API הקיים בלבד. ‏#968 סגר את הפערים:
   uptime_seconds ו-deploy_ip ב-/me, severity ב-/journal, folder ב-/tasks —
   ומה שלא נמדד (null) מוצג בשם ("לא נבדק"), לא כנתון מומצא. הנתונים המתחלפים (‏/overview,
   ‏/tasks) מגיעים מה-polling הקיים; השאר נקרא בכניסה לדף וברענון.
   כל קריאה שנכשלה נשמרת בשם (HOME.err) ומוצגת — לא נקראה ≠ ריקה. */
let HOME = { net: null, journal: null, nodes: null, transfers: null, ports: null,
             ssh: null, update: null, err: {} };
let SECONDARY_STATUS = {};   // ‏id → {connected, error}, נמדד ב-markSecondaryStatus

function homeTabs() { return isAdmin() ? ["סיכום", "משימות", "אירועים"] : ["סיכום", "משימות"]; }

async function loadHome() {
  const jobs = [];
  const grab = (key, path) => jobs.push(api(path)
    .then((v) => { HOME[key] = v; delete HOME.err[key]; })
    .catch((e) => { HOME[key] = null; HOME.err[key] = e.message; }));
  jobs.push(refreshStatus());
  jobs.push(loadCaptures().catch((e) => { HOME.err.tasks = e.message; }));
  grab("net", "/net");
  grab("ports", "/ports");
  if (isAdmin()) {
    jobs.push(loadHealth());
    grab("journal", "/journal?limit=50");
    grab("ssh", "/ssh");
    grab("update", "/update");
  }
  if (ME && ME.capabilities && ME.capabilities.enroll_secondary) {
    grab("nodes", "/storage-nodes");
    grab("transfers", "/storage-transfers");
  }
  await Promise.all(jobs);
  if (current === "home") renderCurrent();
}

function homeHealth() {
  // ‏null = לא נקרא (deploy: 403; admin: עוד לא/נכשל) — אפור, לא ירוק.
  if (!Array.isArray(HEALTH)) return { cls: "", label: "לא נבדק", failing: [], total: 0 };
  const failing = HEALTH.filter((c) => c.state === "bad" || c.state === "warn");
  const bad = failing.some((c) => c.state === "bad");
  return { cls: bad ? "err" : (failing.length ? "warn" : "ok"),
           label: bad ? "תקלה" : (failing.length ? "אזהרה" : "תקין"), failing, total: HEALTH.length };
}

function homeSessionKpi(session, room) {
  if (session) {
    const wait = session.state === "open" && session.starts_in_seconds != null
      ? ` · מתחיל בעוד ${Math.floor(session.starts_in_seconds / 60)}:${String(session.starts_in_seconds % 60).padStart(2, "0")}` : "";
    return UI.kpi({ cls: "info", label: "סבב הפצה פעיל", value: 1,
      sub: esc(`${sessionGroup(session)} · ${sessionImage(session)} — ${session.joined || 0}/${sessionExpected(session)} מחוברים${wait}`) });
  }
  if (room) {
    return UI.kpi({ cls: "info", label: "סבב שיכפול פעיל", value: 1,
      sub: esc(`${room.image_name || ""} — ${room.written_drives || 0}/${room.target_drives || 0} דיסקים נכתבו · גל ${room.wave_number || 1}`) });
  }
  return UI.kpi({ cls: "", label: "סבב הפצה פעיל", value: "אין", sub: UI.link("פתח סבב מדף ההפצה", "selectPageById('deploy')") });
}

function homeKpis() {
  const o = OVERVIEW || {};
  const net = Array.isArray(HOME.net) ? HOME.net : null;
  const seen = net ? net.filter((d) => isToday(d.last_seen)).length : null;
  const unknown = net ? net.filter((d) => !d.registered).length : null;
  const folders = Array.isArray(FOLDERS) ? FOLDERS.length : null;
  const latest = (IMAGES || []).slice().sort((a, b) => String(b.created || "").localeCompare(String(a.created || "")))[0];
  const st = o.storage;
  const hasStorage = st && st.free_bytes != null && Number(st.total_bytes) > 0;
  const used = hasStorage ? Math.max(0, st.total_bytes - st.free_bytes) : 0;
  const first = isAdmin()
    ? (() => { const h = homeHealth();
        const sub = !Array.isArray(HEALTH) ? esc(HOME.err.health || "‏/health לא נקרא עדיין")
          : h.failing.length ? `${h.failing.length} מתוך ${h.total} בדיקות: ${esc(h.failing.map((c) => c.label).join(", "))} — ${UI.link("לבריאות", "selectPageById('health')")}`
          : esc(`${h.total} בדיקות עברו`);
        return UI.kpi({ cls: h.cls, label: "בריאות השרת", value: h.label, sub }); })()
    : UI.kpi({ cls: o.session ? "info" : "", label: "מצב הסבב",
        value: o.session ? stateLabel(o.session.state) : "אין",
        sub: o.session ? esc(`${o.session.joined || 0} מתוך ${sessionExpected(o.session)} מחוברים`) : "אין סבב פתוח" });
  return `<div class="c12 kpis">${first}${homeSessionKpi(o.session, o.room)}
${UI.kpi({ cls: o.machines == null ? "" : "ok", label: "מחשבים רשומים", value: o.machines == null ? "—" : o.machines,
    sub: net ? esc(`${seen} נראו ברשת היום · ${unknown} לא רשומים`) : esc(HOME.err.net ? "רשת לא נקראה: " + HOME.err.net : "רשת לא נקראה עדיין") })}
${UI.kpi({ cls: o.images == null ? "" : "ok", label: "אימג'ים בספרייה", value: o.images == null ? "—" : o.images,
    unit: folders == null ? "" : `ב-${folders} תיקיות`,
    sub: latest ? `אחרון: ${esc(latest.name)} — ${ltr(fmtDate(latest.created))}` : "אין אימג'ים — קלוט ממחשב בנייה או העלה tar" })}
${hasStorage
    ? UI.kpi({ cls: st.free_bytes / st.total_bytes < 0.1 ? "warn" : "ok", label: "אחסון אימג'ים", value: fmtBytes(st.free_bytes), unit: "פנוי",
        bar: Math.round(100 * used / st.total_bytes), sub: `${ltr(fmtBytes(used))} מתוך ${ltr(fmtBytes(st.total_bytes))} בשימוש` })
    : UI.kpi({ cls: "", label: "אחסון אימג'ים", value: "לא ידוע", sub: "השרת לא החזיר נתוני אחסון" })}</div>`;
}

/* שורות "מה קורה עכשיו": סבב, סבב שיכפול, משיכות, קליטות, העברות לסניפים. */
function homeNowRows(all = false) {
  const rows = [];
  const o = OVERVIEW || {};
  const s = o.session;
  if (s) {
    const open = s.state === "open";
    rows.push([UI.name(`סבב הפצה — ${sessionImage(s)}`, `${sessionGroup(s)} · ${stateLabel(s.state)}`),
      esc(`${sessionExpected(s)} תחנות`), UI.barRow(sessionProgressPct(s), "", open ? `${s.joined || 0}/${sessionExpected(s)}` : null),
      UI.status(open ? "run" : "ok", open ? "ממתין להצטרפות" : "משדר"),
      UI.acts(open ? [["התחל עכשיו", "startRound()"], ["פרטים", "openRoundDetail()"]] : [["פרטים", "openRoundDetail()"]])]);
  }
  if (o.room) {
    const r = o.room;
    const pct = r.target_drives ? Math.round(100 * (r.written_drives || 0) / r.target_drives) : null;
    rows.push([UI.name(`סבב שיכפול — ${r.image_name || ""}`, `גל ${r.wave_number || 1} · פתח ${r.opened_by || ""}`),
      esc(`${r.target_drives || 0} דיסקים`), UI.barRow(pct, "", `${r.written_drives || 0}/${r.target_drives || 0}`),
      UI.status(r.wave_state === "running" ? "run" : "warn", stateLabel(r.wave_state)),
      UI.acts([["לחדר המשכפלים", "selectPageById('deploy')"]])]);
  }
  for (const p of o.pulls || []) for (const m of p.members || []) {
    const v = Progress.view(m);
    rows.push([UI.name(`משיכה — ${p.image_name || ""}`, m.hostname || m.name || m.mac || ""), "יוניקאסט",
      UI.barRow(v.percent, "", v.label), UI.status("run", "מושכת"), ""]);
  }
  for (const t of CAPTURE_TASKS || []) {
    const active = t.state === "pending" || t.state === "running";
    if (!all && !active) continue;
    const v = Progress.view(t);
    const cls = t.state === "failed" ? "err" : t.state === "done" ? (t.error ? "warn" : "ok") : t.state === "pending" ? "warn" : "run";
    const label = t.state === "pending" ? "ממתין שהמחשב יעלה ב-PXE" : t.state === "running" ? "קולט"
      : t.state === "done" ? (t.error ? "הושלם עם אזהרה" : "הושלם") : t.state === "failed" ? "נכשל" : t.state;
    rows.push([UI.nameHtml(`קליטת אימג' — ${t.name || ""}`, `${esc(t.machine || t.mac || "")}${t.disk ? " · " + ltr(t.disk) : ""} · ${ltr(fmtDate(t.created_at) + " " + fmtClock(t.created_at))}`),
      t.folder ? esc(`תיקיית ${t.folder}`) : "שורש הספרייה", UI.barRow(v.percent, cls === "err" ? "err" : ""),
      UI.status(cls, label + (t.error ? " — " + t.error : "")), UI.acts([["לספרייה", "selectPageById('images')"]])]);
  }
  for (const t of HOME.transfers || []) {
    const active = ["queued", "sending", "verifying"].includes(t.state);
    if (!all && !active && t.state !== "failed") continue;
    const pct = t.bytes_total ? Math.round(100 * (t.bytes_sent || 0) / t.bytes_total) : null;
    const cls = t.state === "failed" ? "err" : t.state === "done" ? "ok" : t.state === "queued" ? "warn" : "run";
    const label = { queued: "ממתין בתור", sending: "שולח", verifying: "מאמת", done: "הושלם", failed: "נכשל" }[t.state] || t.state;
    const primary = ME.server_name || "ראשי";
    const title = t.direction === "pull"
      ? `העברה ${t.node_label || t.node_id} → ${primary} — ${t.image_name || t.image_id}`
      : `העברה לסניף ${t.node_label || t.node_id} — ${t.image_name || t.image_id}`;
    rows.push([UI.nameHtml(title, `${ltr(fmtDate(t.created_at) + " " + fmtClock(t.created_at))} · ${esc(t.started_by || "")}`),
      esc(t.node_label || t.node_id || ""), UI.barRow(pct, cls === "err" ? "err" : ""),
      UI.status(cls, label + (t.error ? " — " + t.error : "")), UI.acts([["לסניפים", "selectPageById('branches')"]])]);
  }
  return rows;
}

function homeNowCard(all = false) {
  const unread = [HOME.err.tasks && "משימות", HOME.err.transfers && "העברות"].filter(Boolean);
  const warn = unread.length ? UI.note("warn", esc(`לא נקראו: ${unread.join(", ")} — ${HOME.err.tasks || HOME.err.transfers}`)) : "";
  const grid = UI.datagrid({ columns: ["פעילות", "יעד", "התקדמות", "מצב", ""], rows: homeNowRows(all),
    empty: all ? "אין משימות עדיין" : "אין פעילות עכשיו — פתח סבב הפצה או קלוט אימג'" });
  return UI.card({ title: all ? "משימות" : "מה קורה עכשיו", small: all ? "20 הקליטות האחרונות וכל ההעברות" : "מתעדכן כל 2 שניות",
    cls: all || !isAdmin() ? "c12" : "c8", flush: true, body: warn + grid });   // ‏deploy: בלי "דורש טיפול" לצידו
}

/* "דורש טיפול": דיסקים אדומים, דיסקים מכווצים, מכונות לא רשומות, סניפים
   שלא מגיבים, בדיקות בריאות warn/bad. ריק אמיתי ≠ "לא נקרא" (עיקרון 5). */
function homeAttention() {
  const items = [];
  for (const f of DISK_FAILURES || []) {
    const m = findMachine(f.mac);
    items.push(UI.note("err", `<b>דיסק אדום</b> — ${esc(machineName(m) || f.mac)} · דיסק ${esc(f.disk_number ?? "?")}${f.serial ? ` (<span class="mono">${esc(f.serial)}</span>)` : ""} נכשל בכתיבה${f.cause ? ", סיבה: " + esc(f.cause) : ""}. ${UI.link("נקה אחרי החלפה", "selectPageById('machines')")}`));
  }
  for (const r of SHRINK_RECORDS || []) {
    const m = findMachine(r.mac);
    items.push(UI.note("warn", `<b>דיסק מכווץ</b> — ${esc(machineName(m) || r.mac)} · דיסק ${esc(r.port ?? "?")} כווץ לקליטה ולא הוחזר לגודלו. ${UI.link("למחשבים", "selectPageById('machines')")}`));
  }
  for (const d of HOME.net || []) {
    if (d.registered) continue;
    items.push(UI.note("warn", `<b>מכונה לא רשומה</b> ברשת ההפצה — <span class="mono">${esc(d.mac)}</span>${d.ip ? ` קיבלה ${ltr(d.ip)}` : ""} · ${ltr(fmtDate(d.last_seen) + " " + fmtClock(d.last_seen))}. ${UI.link("רשום", "openNewMachine()")}`));
  }
  for (const n of HOME.nodes || []) {
    const st = SECONDARY_STATUS[n.id];
    if (n.disabled_at || !st || st.connected) continue;
    items.push(UI.note("err", `<b>סניף ${esc(n.label)} לא מגיב</b>${st.error ? " — " + esc(st.error) : ""}. ${UI.link("פרטי הסניף", "selectPageById('branches')")}`));
  }
  for (const c of Array.isArray(HEALTH) ? HEALTH : []) {
    if (c.state !== "warn" && c.state !== "bad") continue;
    items.push(UI.note(c.state === "bad" ? "err" : "warn", `<b>${esc(c.label)}</b> — ${esc(c.detail || "")}. ${UI.link("לבריאות", "selectPageById('health')")}`));
  }
  // ‏#1049 שלב ב': שערי בדיקת המכונה (סוללה, שעון, NVMe, קריסה, כפילות IP, CRC)
  // — מוצגים, אינם עוצרים סבב. רשימה ריקה = לא נמצא במה שנמדד, לא "הכול תקין".
  for (const m of MACHINES || []) {
    for (const v of Array.isArray(m.probe_verdicts) ? m.probe_verdicts : []) {
      items.push(UI.note(v.level === "err" ? "err" : "warn", `<b>${esc(machineName(m) || m.mac)}</b> — ${esc(v.text_he || v.key || "")}. ${UI.link("פרטי המכונה", `openMachineDetail('${encodeId(m.mac)}')`)}`));
    }
  }
  const unread = [!Array.isArray(HEALTH) && "בריאות", HOME.err.net && "רשת", HOME.err.nodes && "סניפים"].filter(Boolean);
  let body;
  if (items.length) body = `<div class="stack">${items.join("")}</div>`;
  else if (unread.length) body = UI.note("warn", esc(`לא נקראו: ${unread.join(", ")} — לא ניתן לומר שאין מה לטפל`));
  else body = UI.empty("אין פריטים לטיפול");
  const pill = items.length ? UI.pill("err", String(items.length)) : "";
  return UI.card({ title: "דורש טיפול", acts: pill, cls: "c4", body });
}

/* ‏#968: החומרה מגיעה מהשרת (severity ב-/journal, נקבעת ב-journal_he.py לצד
   התרגום) — לא היוריסטיקה על שם ה-event. כאן רק מיפוי לשם המחלקה בציר-הזמן;
   ערך זר/חסר = "info" (ניטרלי), לא ניחוש. */
const JOURNAL_CLS = { ok: "ok", warn: "warn", err: "err", info: "info" };
function journalCls(row) { return JOURNAL_CLS[row && row.severity] || "info"; }

function homeEvents(limit) {
  const journal = Array.isArray(HOME.journal) ? HOME.journal : null;
  const acts = `<button class="btn sm flat" onclick="selectPageById('logs')">ליומן המלא ←</button>`;
  let body;
  if (!journal) body = UI.note("warn", esc("היומן לא נקרא" + (HOME.err.journal ? ": " + HOME.err.journal : " עדיין")));
  else if (!journal.length) body = UI.empty("אין אירועים עדיין");
  else body = UI.timeline(journal.slice(0, limit).map((r) => ({
    t: isToday(r.ts) ? fmtClock(r.ts) : fmtDate(r.ts), cls: journalCls(r),
    text: esc(r.label || r.text || r.event || ""), who: r.user || "" })));
  return UI.card({ title: limit >= 50 ? "אירועים" : "אירועים אחרונים", small: limit >= 50 ? "50 האחרונים" : "", acts, cls: limit >= 50 ? "c12" : "c8", body });
}

function homeServerCard() {
  const ports = Array.isArray(HOME.ports) ? HOME.ports : null;
  const port = (id) => {
    if (!ports) return UI.status("", HOME.err.ports ? "לא נקרא: " + HOME.err.ports : "לא נקרא עדיין");
    const p = ports.find((x) => x.id === id);
    return p ? UI.status(healthStatusClass(p.state), `${healthStatusLabel(p.state)} · ${p.port}`) : UI.status("", "לא מדווח");
  };
  const nic = (typeof NICS !== "undefined" && Array.isArray(NICS)) ? NICS.find((n) => n.enabled) : undefined;
  const dcls = nic ? dhcpLiveClass(nic.dhcp_live && nic.dhcp_live.state) : "";
  const dhcp = nic ? UI.status(dcls === "ok" ? "ok" : dcls === "warn" ? "warn" : "", `${nic.name} · ${nic.dhcp_live_label || ""}`)
    : (nic === undefined ? UI.status("", "לא נקרא") : UI.status("", "לא מוגדר"));
  const ssh = HOME.ssh && HOME.ssh.stations
    ? UI.status(HOME.ssh.stations.enabled ? "warn" : "", HOME.ssh.stations.enabled ? "דלוק" : "כבוי")
    : UI.status("", HOME.err.ssh ? "לא נקרא" : "לא נקרא עדיין");
  const pairs = [["API / קונסולה", port("http_console")], ["PXE / TFTP", port("tftp")], ["DHCP הפצה", dhcp],
    ["מולטיקאסט", port("multicast")], ["SSH לתחנות", ssh]];
  if (HOME.nodes) {
    const live = HOME.nodes.filter((n) => !n.disabled_at);
    const down = live.filter((n) => SECONDARY_STATUS[n.id] && !SECONDARY_STATUS[n.id].connected).length;
    pairs.push(["סניפים", live.length ? UI.status(down ? "err" : "ok", down ? `${down} מתוך ${live.length} לא מגיב` : `${live.length} מחוברים`) : UI.status("", "אין סניפים")]);
  }
  pairs.push(["עדכון", HOME.update ? `${esc(HOME.update.current || "ללא תג")} · ${UI.link("בדוק", "selectPageById('health')")}` : UI.status("", "לא נקרא")]);
  const acts = `<button class="btn sm flat" onclick="selectPageById('health')">בריאות ←</button>`;
  return UI.card({ title: "השרת", acts, cls: "c4", body: UI.kv(pairs) });
}

/* ‏#968: "פעיל 6 ימים 3 שעות" מ-/me.uptime_seconds (המכונה, /proc/uptime);
   ‏null = השרת לא הצליח לקרוא → "זמן פעילות לא נבדק" — בשם, לא 0 (עיקרון 5). */
function fmtUptime(seconds) {
  const s = Number(seconds);
  if (seconds == null || !Number.isFinite(s) || s < 0) return "";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d} ${d === 1 ? "יום" : "ימים"} ${h} ${h === 1 ? "שעה" : "שעות"}`;
  if (h) return `${h} ${h === 1 ? "שעה" : "שעות"} ${m} ${m === 1 ? "דקה" : "דקות"}`;
  return `${m} ${m === 1 ? "דקה" : "דקות"}`;
}
function homeUptime() {
  if (!ME || ME.uptime_seconds == null) return `<span class="muted">זמן פעילות לא נבדק</span>`;
  return esc(`פעיל ${fmtUptime(ME.uptime_seconds)}`);
}
/* ‏#968: כתובת השרת בעיני התחנות (/me.deploy_ip); ‏null = רשת ההפצה לא הוגדרה (#1088). */
function homeDeployIp() {
  if (!ME || !("deploy_ip" in ME)) return "";
  return ME.deploy_ip ? `<span class="mono">${esc(ME.deploy_ip)}</span>` : `<span class="muted">רשת הפצה לא הוגדרה</span>`;
}

function home(tab = 0) {
  const tabs = homeTabs();
  const o = OVERVIEW;
  const h = homeHealth();
  const pill = isAdmin()
    ? (Array.isArray(HEALTH) ? UI.pill(h.cls, !h.failing.length ? "תקין" : h.cls === "err" ? `${h.failing.length} ${h.failing.length === 1 ? "תקלה" : "תקלות"}` : `${h.failing.length} ${h.failing.length === 1 ? "אזהרה" : "אזהרות"}`) : UI.pill("", "לא נבדק"))
    : (o && o.session ? UI.pill("info", stateLabel(o.session.state)) : UI.pill("", "אין סבב"));
  const actions = `<button class="btn primary" onclick="selectPageById('deploy')">+ סבב הפצה</button>`
    + (isAdmin() ? `<button class="btn" onclick="openCapture().catch(e => toast(e.message))">+ קליטת אימג'</button>` : "")
    + `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>`;
  const sub = [esc(ME && ME.server_name || ""), homeDeployIp(), ME && ME.version ? esc(ME.version) : "", homeUptime()].filter(Boolean).join(" · ");
  const header = UI.objHeader({ crumbs: [{ label: "שרת אימג'ים" }, { label: "סקירה כללית" }], icon: "server",
    name: "שרת אימג'ים", sub, pill, actions, tabs, tab });
  const stale = overviewError ? `<div class="c12">${UI.note("warn", esc(overviewError))}</div>` : "";
  let body;
  if (tab === 1) body = homeNowCard(true);
  else if (tab === 2 && isAdmin()) body = homeEvents(50);
  else if (!o && !overviewError) body = `<div class="c12">${pagePlaceholder()}</div>`;
  else body = homeKpis() + homeNowCard(false) + (isAdmin() ? homeAttention() + homeEvents(6) + homeServerCard() : "");
  return `<div class="page">${header}<div class="body">${stale}${body}</div></div>`;
}

/* ---------- #954 גל 4: סבב הפצה — חדר המשכפלים (#695) ----------
   נבנה לפי docs/design/console-redesign/deploy.md (§"המבנה — חדר המשכפלים", מוקאפ
   deploy-room.png שנדב אישר 17/09). v1 = משכפלים בלבד (הכרעת נדב 17/09 10:20): הלשונית
   הראשונה היא החדר; "כיתה" = קוד ה-session הקיים כמו שהוא, עטוף בכרטיס; "היסטוריה" =
   דורש API. הגריד הוא **אותו גריד** של clonersView (גל 3א) — machineSlots / slotHtml /
   clonerCardHtml במצב חדר — לא עותק. מקורות: /room (round, machines[].drawer_list,
   stream_stalled, disk_floor #953), /images (min_target_bytes #953), /machines (prompt —
   השאלה למפעיל #906), /disk-failures (אדום), /net (IP). כתיבה — רק ה-endpoints הקיימים:
   POST /room, /room/start, /room/wake, /room/close (הקלדת שם, עיקרון 7); בלי שינוי שרת.
   ההכרעה על SMART (נדב 16/09): כתום = בלי תשובה ממשיך לכתוב, אדום = בלי תשובה מדלג —
   לעולם לא דילוג בשני המקרים; היא נענית ליד המכונה (או במוניטור), ותשובה מהקונסולה — דורש
   API. מה שאין לו API — קצב/איבוד, מספר הגלים הכולל, היסטוריה, דילוג מרחוק — טקסט,
   לא נתון מומצא (README §8, עיקרון 5). */
const DEPLOY = { big: false, err: "", roomErr: "", busy: false, form: { image: "", src: "library", builder: "", disk: "", target: "" } };
const WAVE_HE = { open: "ממתין להצטרפות", running: "משדר", closed: "הגל נסגר" };

function deployTabs() {
  return classroomsOn() ? ["חדר המשכפלים", "כיתה", "היסטוריה"] : ["חדר המשכפלים", "היסטוריה"];
}
function roomOperator() { return !!ME && ["admin", "deploy"].includes(ME.role); }   // ‏room.ROOM_OPERATOR_ROLES
/* רשומות המחשבים לגריד: המגירות תמיד מ-/room (‏drawer_list נושא port/serial/model/size/smart
   גם בלי סבב) — כך גם deploy, שאינו מגיע לדף המחשבים, רואה את החדר במלואו; prompt
   ו-drawer_count המוגדר מ-/machines כשנקרא. */
function roomMachines() {
  return sortMachinesBySuffix((ROOM && ROOM.machines || []).map((rm) => {
    const m = findMachine(rm.mac) || {};
    return { ...m, mac: rm.mac, suffix: machineName(m) || rm.name || rm.mac, group_id: m.group_id || "grp_CLONERS",
      drawer_count: rm.drawer_count != null ? rm.drawer_count : (m.drawer_count ?? null),
      disks: Array.isArray(rm.drawer_list) ? rm.drawer_list : (m.disks ?? null), prompt: m.prompt || null,
      disk_probe: rm.disk_probe ?? m.disk_probe ?? null };
  }));
}
/* אחוז הגל: ממוצע המגירות שהצטרפו ומדווחות (‏done = 100). ‏null = אין דיווח, לא 0 (עיקרון 5). */
function wavePct() {
  const ds = (ROOM && ROOM.machines || []).filter((rm) => rm.joined).flatMap((rm) => rm.drawer_list || [])
    .filter((d) => d.state === "done" || (d.state && d.bytes_total > 0));
  if (!ds.length) return null;
  return Math.round(ds.reduce((s, d) => s + (d.state === "done" ? 100 : 100 * (d.bytes_written || 0) / d.bytes_total), 0) / ds.length);
}
function roomStats(kids) {
  const all = kids.map((m) => ({ m, slots: machineSlots(m).slots }));
  const pick = (fn) => all.flatMap(({ m, slots }) => slots.filter((s) => fn(s, m)).map((s) => ({ m, s })));
  return {
    slots: all.reduce((n, x) => n + x.slots.length, 0), filled: pick((s) => s.disk).length,
    writing: pick((s) => s.live && (s.live.state === "writing" || s.live.state === "verifying")),
    red: pick((s) => s.disk && slotClass(s) === "err"), warn: pick((s) => s.disk && slotClass(s) === "warn"),
    empty: pick((s, m) => !s.disk && !!(roomMachine(m.mac) || {}).awake),   // חריץ פנוי = במכונה מחוברת; מכונה כבויה נספרת פעם אחת
    off: kids.filter((m) => { const rm = roomMachine(m.mac); return rm && !rm.awake; }),
    awake: kids.filter((m) => { const rm = roomMachine(m.mac); return rm && rm.awake; }).length,
    prompts: kids.filter((m) => m.prompt),
  };
}
/* "מחשב 1 · דיסק 1,2 — מחשב 2 · דיסק 1" */
function whereList(items) {
  const by = new Map();
  items.forEach(({ m, s }) => { const k = machineName(m); if (!by.has(k)) by.set(k, []); by.get(k).push(s.n); });
  return [...by].map(([k, ns]) => `${k} · דיסק ${ns.join(",")}`).join(" — ");
}
/* מצב כרטיס בחדר: ‏awake מ-/room (דיברה עם השרת ב-30 השניות האחרונות — ראיה חיובית),
   "בסבב · N%" כשהצטרפה, השאלה למפעיל כשיש. */
function roomCardState(m) {
  const rm = roomMachine(m.mac);
  if (!rm) return { cls: "", text: "לא בחדר" };
  if (roomRound() && rm.joined) return clonerState(m);
  if (!rm.awake) return { cls: "", text: "לא מחובר", off: true };
  if (m.prompt) return { cls: "warn", text: waitingText(m) };
  return { cls: "ok", text: "מחובר" };
}
/* ‏#953: הסיבה שאימג' אינו נכנס למגירה הקטנה ביותר (‏disk_floor מ-GET /room) — **אותה
   מחרוזת בדיוק** ש-`room.fit_refusal` בשרת מחזיר ב-409, כדי שהמפעיל יראה סיבה אחת משני
   הצדדים. null = נכנס, או אין רצפה/דרישה ידועה. GB עשרוני כמו על מדבקת הכונן. */
function imageFitReason(img, floor) {
  const need = Number(img.min_target_bytes);
  if (!floor || img.min_target_bytes == null || !Number.isFinite(need) || need < 0 || need <= floor.size_bytes) return null;
  const slot = floor.port != null ? `דיסק ${floor.port}` : `דיסק ${floor.dev || "?"}`;
  return `${slot} במחשב ${floor.name} הוא ${Math.round(floor.size_bytes / 1e9)}GB, האימג' צריך ${Math.ceil(need / 1e9)}GB`;
}
function roomImageLabel(r) {
  const direct = r.source && r.source.kind === "build_disk";
  if (direct) return esc(`מקור: מחשב הבנייה (${r.source.name || r.source.mac}:${r.source.disk})`);
  const img = (IMAGES || []).find((x) => x.id === r.image_id);
  return esc(r.image_name || r.image_id || "—") + (img && img.total_compressed_bytes ? ` · ${ltr(fmtBytes(img.total_compressed_bytes))} בשרת` : "");
}

function deploy(tab = 0) {
  const tabs = deployTabs(), r = roomRound(), kids = roomMachines(), st = roomStats(kids), pct = wavePct();
  const stalled = !!(ROOM && ROOM.stream_stalled), op = roomOperator();
  const classTab = classroomsOn() ? 1 : -1, histTab = classroomsOn() ? 2 : 1;
  let name, sub, pill;
  if (r) {
    name = `חדר המשכפלים — גל ${r.wave_number || 1}`;
    sub = [roomImageLabel(r), `יעד: ${r.target_drives || 0} דיסקים`, `נכתבו ${r.written_drives || 0}`, `נשארו ${r.remaining_drives != null ? r.remaining_drives : "?"}`, r.opened_by ? `פתח ${esc(r.opened_by)}` : ""].filter(Boolean).join(" · ");
    pill = stalled ? UI.pill("warn", "הזרם עצר") : r.wave_state === "running" ? UI.pill("info", `משדר${pct != null ? ` — ${pct}%` : ""}`)
      : r.wave_state === "open" ? UI.pill("info", `ממתין להצטרפות · ${r.ready_drives || 0} מוכנים`) : UI.pill("", WAVE_HE[r.wave_state] || r.wave_state || "—");
  } else {
    name = "חדר המשכפלים";
    sub = ROOM == null ? "החדר לא נקרא" : [`${kids.length} ${kids.length === 1 ? "משכפל" : "משכפלים"}`, `${st.slots} חריצים`, `${st.awake} מחוברים`, `${st.filled} דיסקים בחריצים`].join(" · ");
    pill = ROOM == null ? UI.pill("", "החדר לא נקרא") : UI.pill("", "אין סבב");
  }
  const actions = (tab === 0 && ROOM ? (op && r && r.wave_state === "open" ? `<button class="btn primary" onclick="startWave()">התחל גל (${r.ready_drives || 0} מוכנים)</button>` : "")
    + (op ? `<button class="btn" onclick="wakeRoom()">הער משכפלים (WoL)</button>` : "")
    + (op && r ? `<button class="btn danger" onclick="stopRoom()">עצור סבב (הקלדת שם)</button>` : "")
    + `<button class="btn" onclick="toggleRoomBig()">${DEPLOY.big ? "חזרה לתצוגה המלאה" : "תצוגה גדולה"}</button>` : "")
    + `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>`;
  const header = UI.objHeader({ crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "סבב הפצה" }], icon: "copy",
    name: tab === 0 ? name : tab === classTab ? "סבב כיתה" : "היסטוריית סבבים", sub: tab === 0 ? sub : tab === classTab ? "מחשבי כיתה — v2 (הכרעת נדב 17/09); מוצג כפי שקיים היום" : "סבבים קודמים", pill: tab === 0 ? pill : "", actions, tabs, tab });
  const stale = overviewError ? `<div class="c12">${UI.note("warn", esc(overviewError))}</div>` : "";
  let body;
  if (tab === classTab) body = deployClassView();
  else if (tab === histTab) body = UI.card({ title: "היסטוריית סבבים", cls: "c12", body: UI.note("info", `סבבים קודמים בחדר${classroomsOn() ? " ובכיתות" : ""} — <b title="אין endpoint לסבבים סגורים (#980 §3)">דורש API</b>. היום רק ${isAdmin() ? UI.link("ביומן", "selectPageById('logs')") : "ביומן (מנהל)"}.`) });
  else if (ROOM == null) body = `<div class="c12">${UI.note("warn", `החדר לא נקרא${DEPLOY.roomErr ? ": " + esc(DEPLOY.roomErr) : ""} — הגריד והסבב לא ידועים.`)}</div>`;
  else if (DEPLOY.big) body = roomGridCard(kids, st, true);
  else if (r) body = `<div class="c12 kpis">${roomKpis(r, st, pct)}</div>` + roomGridCard(kids, st, false) + roomNotes(kids, st);
  else body = roomNewCard(kids, st) + roomGridCard(kids, st, false) + roomNotes(kids, st);
  return `<div class="page">${header}<div class="body">${stale}${body}</div></div>`;
}

function roomKpis(r, st, pct) {
  const stalled = !!ROOM.stream_stalled, open = r.wave_state === "open";
  const k1 = UI.kpi({ cls: stalled ? "warn" : "info", label: "הגל הנוכחי", value: `גל ${r.wave_number || 1}`, bar: pct,
    sub: (open ? `${r.ready_drives || 0} מגירות מוכנות · הגל טרם התחיל` : pct == null ? "עוד אין דיווח כתיבה" : `${pct}% בממוצע על המגירות שהצטרפו`)
      + (stalled ? " · <b>הזרם עצר</b>" : "") + ` · קצב ואיבוד — <b title="‏/room אינו מחזיר קצב או איבוד חבילות">דורש API</b>` });
  const k2 = UI.kpi({ cls: r.written_drives ? "ok" : "", label: "נכתבו ואומתו", value: r.written_drives || 0, unit: `/ ${r.target_drives || 0}`,
    sub: r.remaining_drives === 0 ? "היעד לסבב הושלם" : `${r.remaining_drives != null ? r.remaining_drives : "?"} נשארו · מכל הגלים` });
  const k3 = UI.kpi({ cls: st.writing.length ? "info" : "", label: "כותבים עכשיו", value: st.writing.length,
    sub: st.writing.length ? esc(whereList(st.writing)) : open ? "הגל טרם התחיל" : "אף מגירה לא כותבת כרגע" });
  const care = st.prompts.length + st.red.length + (stalled ? 1 : 0);
  const careSub = [...st.prompts.map((m) => `${esc(machineName(m))} ממתין לתשובה`), st.red.length ? `${esc(whereList(st.red))} אדום — מדלג` : "", stalled ? "הזרם עצר" : ""].filter(Boolean).join(" · ");
  const k4 = UI.kpi({ cls: st.red.length ? "err" : care ? "warn" : "", label: "דורש מפעיל", value: care, sub: careSub || "אין שאלות פתוחות ואין אדומים" });
  const k5 = UI.kpi({ cls: "", label: "ריקים / לא מחוברים", value: st.empty.length + st.off.length,
    sub: [st.off.length ? `${esc(st.off.map(machineName).join(", "))} ${st.off.length === 1 ? "לא מחובר" : "לא מחוברים"}` : "כל המשכפלים מחוברים", `${st.empty.length} חריצים פנויים`].join(" · ") });
  return k1 + k2 + k3 + k4 + k5;
}
function roomGridCard(kids, st, big) {
  const r = roomRound(), admin = isAdmin();
  const small = r ? "בסבב — מצב כל מגירה מ-/room" : "אין סבב — הדיסקים לפי הדיווח האחרון ב-hello";
  const body = kids.length ? `<div class="mgrid${big ? " big" : ""}">${kids.map((m) => clonerCardHtml(m, admin, true)).join("")}</div>`
    : UI.empty("אין מחשבי שיכפול רשומים — הוסיפו אותם במחשבים (MAC + שם) לפני שפותחים סבב.", admin ? `<button class="btn primary" onclick="openClass('grp_CLONERS')">למחשבי השיכפול</button>` : "");
  return UI.card({ title: "המחשבים והדיסקים", small, acts: clonerLegend(), cls: "c12", body });
}
/* השאלה הפתוחה למפעיל — בדף, לא במודאל. הצבע = מה קורה בלי תשובה (הכרעת נדב 16/09):
   כתום ממשיך לכתוב, אדום מדלג. הפעולות: מוניטור (לענות ליד המסך), "נקה" על רשומת אדום
   (אחרי החלפת דיסק/כבל); תשובה מהקונסולה — דורש API. */
function roomNotes(kids, st) {
  const admin = isAdmin(), out = [], asked = new Set(st.prompts.map((m) => m.mac));
  const clears = (items) => admin ? items.filter((x) => x.s.fail).map((x) => UI.link(`נקה אדום — ${machineName(x.m)} · דיסק ${x.s.n}`, `clearDiskFailure(${Number(x.s.fail.id)})`)) : [];
  for (const m of st.prompts) {
    const red = st.red.filter((x) => x.m.mac === m.mac), warn = st.warn.filter((x) => x.m.mac === m.mac);
    const fate = red.length ? `<b>אדום — בלי תשובה הסוכן מדלג</b> על ${esc(whereList(red))}` : warn.length ? `<b>כתום — בלי תשובה הסוכן ממשיך לכתוב</b> על ${esc(whereList(warn))}` : "<b>ממתין לתשובה ליד המכונה</b>";
    const acts = [admin ? UI.link("לענות ליד המסך — דף המוניטור", `selectPageById('monitor')`) : "", ...clears(red),
      `תשובה מהקונסולה — <b title="אין endpoint לתשובת מפעיל (#906: השאלה נענית ליד המכונה)">דורש API</b>`].filter(Boolean).join(" · ");
    out.push(UI.note(red.length ? "err" : "warn", `<b>${esc(machineName(m))}</b> ממתין למפעיל: <span dir="ltr">${esc(PROMPT_HE[m.prompt] || m.prompt)}</span>. ${fate}. ${acts}. השאלה חייבת תשובה מאדם — שיכפול תמיד עם מישהו ליד המחשבים.`));
  }
  if (roomRound()) {
    const warn = st.warn.filter((x) => !asked.has(x.m.mac)), red = st.red.filter((x) => !asked.has(x.m.mac));
    if (warn.length) out.push(UI.note("warn", `<b>${esc(whereList(warn))}</b> — SMART אזהרה / CRC: <b>הסוכן ממשיך לכתוב</b> (כתום = כותב), והדיסק ייבדק שוב באימות. דילוג מרחוק — <b title="אין endpoint לדילוג על מגירה">דורש API</b>.`));
    if (red.length) out.push(UI.note("err", `<b>${esc(whereList(red))}</b> — אדום: <b>הסוכן מדלג</b> (אדום = מדלג). ${[...clears(red), "החלפת דיסק או כבל — ליד המכונה"].join(" · ")}.`));
  }
  return out.length ? `<div class="c12 rnotes">${out.join("")}</div>` : "";
}
/* סבב חדר חדש — inline, לא מודאל: אימג' ("נכנס לדיסק מ-X GB"; #953: מה שלא נכנס למגירה
   הקטנה מושבת עם הסיבה — לא מוסתר) → מקור (ספרייה / דיסק מחשב בנייה, #715) → פתח סבב
   (‏POST /room) → ואז "התחל גל". הערכים נשמרים ב-DEPLOY.form כי הדף מצטייר מחדש כל 2 ש'. */
function roomNewCard(kids, st) {
  const f = DEPLOY.form, floor = ROOM.disk_floor || null, op = roomOperator();
  const images = (IMAGES || []).slice().sort((a, b) => String(a.folder || "").localeCompare(String(b.folder || "")) || String(a.name).localeCompare(String(b.name)));
  const opts = `<option value="">— בחר אימג' —</option>` + images.map((img) => {
    const reason = imageFitReason(img, floor), g = gbCeil(img.min_target_bytes);
    return `<option value="${esc(img.id)}"${reason ? " disabled" : ""}${img.id === f.image ? " selected" : ""}>${esc((img.folder ? img.folder + " / " : "") + img.name)}${g != null ? ` · נכנס לדיסק מ-${g} GB` : " · גודל נדרש לא ידוע"}${reason ? " — לא נכנס: " + esc(reason) : ""}</option>`;
  }).join("");
  const floorNote = floor ? `המגירה הקטנה ביותר בחדר: ${Math.round(floor.size_bytes / 1e9)}GB (דיסק ${floor.port != null ? floor.port : floor.dev} במחשב ${esc(floor.name)}).` : "גודל הדיסקים לא ידוע — יסורב במכונה אם לא ייכנס.";
  const builders = (MACHINES || []).filter((m) => machineRole(m) === "build" && Array.isArray(m.disks) && m.disks.length);
  const builder = builders.find((m) => m.mac === f.builder) || builders[0] || null;
  const direct = f.src === "build_disk";
  const declared = kids.reduce((n, m) => n + (Number(m.drawer_count) || 0), 0);
  const target = f.target !== "" ? f.target : String(Math.max(1, declared));
  const live = kids.flatMap((m) => (m.disks || []).filter((d) => d.serial && d.port != null)).length;
  const source = `<div class="radios" role="radiogroup" aria-label="מקור"><label><input type="radio" name="rn-src" value="library"${direct ? "" : " checked"} onchange="roomFormSet('src',this.value)">מהשרת (הספרייה)</label>`
    + (builders.length ? `<label><input type="radio" name="rn-src" value="build_disk"${direct ? " checked" : ""} onchange="roomFormSet('src',this.value)">מדיסק מחשב בנייה (הפצה ישירה)</label>` : `<span class="muted">מדיסק מחשב בנייה — אין מחשב בנייה שדיווח על דיסקים</span>`) + `</div>`;
  const fields = direct && builder
    ? `<label>מחשב בנייה<select id="rn-builder" onchange="roomFormSet('builder',this.value)">${builders.map((m) => `<option value="${esc(m.mac)}"${m.mac === builder.mac ? " selected" : ""}>${esc(machineName(m))}</option>`).join("")}</select></label>
       <label>דיסק המקור<select id="rn-disk" onchange="roomFormSet('disk',this.value)">${builder.disks.map((d, i) => `<option value="${esc(d.dev)}"${(f.disk || builder.disks[0].dev) === d.dev ? " selected" : ""}>דיסק ${diskSlot(d, i)} · ${fmtBytes(d.size_bytes)} · ${esc(d.model || "")}</option>`).join("")}</select></label>
       <div class="full muted">היעד: כל המגירות המחוברות כרגע (${live}) — הפצה ישירה דורשת בחירה מפורשת, ונשלחות כולן. סבב יחיד, לא מצטבר.</div>`
    : `<label>אימג'<select id="rn-image" onchange="roomFormSet('image',this.value)">${opts}</select></label>
       <label>יעד — כמה דיסקים בסך הכול (בכל הגלים)<input type="number" id="rn-target" min="1" value="${esc(target)}" oninput="roomFormSet('target',this.value)"></label>
       <div class="full muted">${floorNote}${declared ? ` ברירת המחדל = ${declared} חריצים שהוגדרו בחדר.` : ""}</div>`;
  const err = DEPLOY.err ? UI.note("err", `הסבב לא נפתח: ${esc(DEPLOY.err)}`) : "";
  const submit = op ? `<div class="full"><button class="btn primary" onclick="openRoomRoundSubmit()"${DEPLOY.busy ? " aria-busy=\"true\"" : ""}>פתח סבב</button> <span class="muted">הפתיחה מעירה את החדר ב-WoL; "התחל גל" אחרי שהמשכפלים הצטרפו.</span></div>` : `<div class="full muted">פתיחת סבב — למנהל או למפעיל הפצה.</div>`;
  return UI.card({ title: "סבב חדר חדש", small: "אין סבב פעיל", cls: "c12", body: `<div class="rnew">${err ? `<div class="full">${err}</div>` : ""}<div class="full">${source}</div>${fields}${submit}</div>` });
}
function roomFormSet(key, value) { DEPLOY.form[key] = value; if (key === "src" || key === "builder") renderCurrent(); }
function toggleRoomBig() { DEPLOY.big = !DEPLOY.big; renderCurrent(); }
async function openRoomRoundSubmit() {
  if (!roomOperator() || DEPLOY.busy) return;
  const f = DEPLOY.form; DEPLOY.err = "";
  let body;
  if (f.src === "build_disk") {
    const builders = (MACHINES || []).filter((m) => machineRole(m) === "build" && Array.isArray(m.disks) && m.disks.length);
    const builder = builders.find((m) => m.mac === f.builder) || builders[0];
    const by = new Map();
    roomMachines().forEach((m) => (m.disks || []).forEach((d) => { if (d.serial && d.port != null) { if (!by.has(m.mac)) by.set(m.mac, []); by.get(m.mac).push(d.port); } }));
    const target_slots = [...by].map(([mac, ports]) => ({ mac, ports }));
    if (!builder) DEPLOY.err = "אין מחשב בנייה שדיווח על דיסקים";
    else if (!target_slots.length) DEPLOY.err = "אין מגירה מחוברת שאפשר לבחור כיעד";
    body = { source: { kind: "build_disk", mac: builder ? builder.mac : "", disk: f.disk || (builder ? builder.disks[0].dev : "") }, target_slots };
  } else {
    if (!f.image) DEPLOY.err = "בחר אימג'";
    body = { image_id: f.image, target_drives: Number(f.target) || 0 };
  }
  if (DEPLOY.err) { renderCurrent(); return; }
  DEPLOY.busy = true;
  try {
    await post("/room", body);
    toast("הסבב נפתח — החדר הוער ב-WoL");
    DEPLOY.form.image = ""; DEPLOY.form.target = "";
    await loadDeploy();
  } catch (e) {
    DEPLOY.err = e.message;   // ‏409: "כבר יש סבב" / fit_refusal (#953) — אותה מחרוזת שהאפשרות המושבתת מציגה
  } finally { DEPLOY.busy = false; }
  if (current === "deploy") renderCurrent();
}
async function startWave() {
  if (!roomOperator()) return;
  try { await post("/room/start"); toast("הגל התחיל"); await loadDeploy(); }
  catch (e) { toast("הגל לא התחיל: " + e.message, 6000); }   // ‏409 — אף משכפל לא הצטרף / המניפסט טרם הגיע
}
function stopRoom() {
  const r = roomRound();
  if (!roomOperator() || !r) return;
  const name = r.image_name || r.image_id || "";
  sheet({ title: "עצירת סבב החדר", sub: `הסבב על "${name}" ייעצר: השידור נפסק, הגל נסגר ומגירות שלא נכתבו נשארות טריות. פעולה הרסנית — הקלדת שם.`,
    danger: true, submitLabel: "עצור סבב", verify: { label: "הקלד את שם האימג'", mustEqual: name },
    onSubmit: async () => { await post("/room/close", { confirm_name: name }); toast("הסבב נעצר"); await loadDeploy(); } });
}
/* לשונית "כיתה": קוד ה-session הקיים כמו שהוא (סבב כיתה = v2) — שורת הסבב, התחלה/עצירה,
   התחנות (memberRow + stuckNote), החסרות מרשימת הכיתה (נקראה בכניסה לדף), משיכות unicast. */
function deployClassView() {
  if (!OVERVIEW) return UI.card({ title: "סבב כיתה", cls: "c12", body: UI.note("warn", "‏/overview לא נקרא — הסבב לא ידוע.") });
  const s = OVERVIEW.session, pulls = OVERVIEW.pulls || [];
  // הגל של חדר המשכפלים הוא session על grp_CLONERS (‏/overview.session) — הוא שייך ללשונית החדר, לא לכיתה.
  const wave = s && (s.group_id === "grp_CLONERS" || (OVERVIEW.room && s.prefix === "ROOM"));
  if (!s || wave) return UI.card({ title: "סבב כיתה", small: "v2", cls: "c12", body: UI.empty(wave ? "אין סבב כיתה — הסבב הפעיל הוא גל בחדר המשכפלים (הלשונית הראשונה)." : "אין סבב כיתה פתוח. סבב כיתה נפתח ממסך התחנה; מחשבי כיתה בקונסולה — v2.") + (pulls.length ? `<div style="margin-top:12px">${pullsHtml(pulls, false)}</div>` : "") });
  const img = sessionImage(s), pct = sessionProgressPct(s);
  const bar = pct == null ? "—" : `<div class="progress" style="width:150px"><i style="width:${pct}%"></i></div>`;
  const table = `<table class="table"><thead><tr><th>סבב</th><th>אימג׳</th><th>כיתה</th><th>מחוברים</th><th>התקדמות</th><th>סטטוס</th></tr></thead><tbody><tr><td><strong>${esc(s.group_label || s.prefix || img)}</strong></td><td>${esc(img)}</td><td>${esc(sessionGroup(s))}</td><td>${s.joined || 0} / ${sessionExpected(s)}</td><td>${bar}</td><td><span class="status ${stateClass(s.state)}"><i></i>${esc(stateLabel(s.state))}${s.state === "open" && s.starts_in_seconds != null ? ` · מתחיל בעוד ${s.starts_in_seconds} ש'` : ""}</span></td></tr></tbody></table>`;
  const op = roomOperator();
  const acts = op ? `<div class="acts">${s.state === "open" ? `<button class="btn primary" onclick="startRound()">התחל עכשיו</button>` : ""}<button class="btn danger" onclick="stopRound()">עצור סבב (הקלדת שם)</button></div>` : "";
  const roster = s.roster ? new Set(s.roster) : null, byMac = new Map((s.members || []).map((m) => [m.mac, m]));
  let rows = (s.members || []).map((m) => memberRow(m, s, stuckNote(s.stuck, m.mac))).join("");
  const machines = s.single ? [] : (SESSION_MACHINES.group === s.group_id ? SESSION_MACHINES.list : null);
  for (const m of machines || []) if ((!roster || roster.has(m.mac)) && !byMac.has(m.mac)) rows += `<div class="member"><b>${esc((s.prefix || "") + "-" + m.suffix)}</b><div class="sub">${esc(stuckNote(s.stuck, m.mac) || "טרם הצטרפה")}</div></div>`;
  const next = (machines || []).filter((m) => roster && !roster.has(m.mac)).map((m) => m.suffix);
  const note = machines === null && !s.single ? UI.note("warn", "רשימת הכיתה לא נקראה — תחנות חסרות אינן מוצגות.") : "";
  return UI.card({ title: "סבב כיתה", small: "v2 — כפי שקיים היום", cls: "c12", body: `<div class="table-wrap">${table}</div>${acts}` })
    + UI.card({ title: "תחנות בסבב", cls: "c12", body: note + (rows ? `<div class="list">${rows}</div>` : `<div class="empty">אין תחנות מצורפות</div>`) + (next.length ? `<div class="notice">הסבב הבא: ${esc(next.join(", "))}</div>` : "") + pullsHtml(pulls, true) });
}
/* טעינת הדף: /overview (הפולינג הקיים), /room, /images, ורשומות המחשבים (שם/prompt/דיסקים
   אדומים/IP) — deploy קורא את כולם (GET בלבד; הכתיבה היא של room_operator). */
async function loadDeploy() {
  const jobs = [refreshStatus()];
  jobs.push(api("/room").then((v) => { ROOM = v && Array.isArray(v.machines) ? v : null; DEPLOY.roomErr = ROOM ? "" : "תשובה שאינה חדר"; })
    .catch((e) => { ROOM = null; DEPLOY.roomErr = e.message; }));
  jobs.push(api("/images").then((v) => { if (Array.isArray(v)) IMAGES = v; }).catch(() => {}));
  jobs.push(Promise.allSettled([api("/machines"), api("/groups"), api("/disk-failures"), api("/net")]).then(([m, g, f, n]) => {
    if (m.status === "fulfilled" && Array.isArray(m.value)) MACHINES = m.value;
    if (g.status === "fulfilled" && Array.isArray(g.value)) GROUPS = g.value;
    if (f.status === "fulfilled" && Array.isArray(f.value)) DISK_FAILURES = f.value;
    if (n.status === "fulfilled" && Array.isArray(n.value)) NET = n.value;
  }));
  await Promise.all(jobs);
  const s = OVERVIEW && OVERVIEW.session;
  if (s && !s.single && s.group_id) await sessionClassMachines(s.group_id);
  ROOM_KEY = JSON.stringify([ROOM, (MACHINES || []).map((m) => [m.mac, m.prompt])]);
  if (current === "deploy") renderCurrent();
}

/* ---------- ספריית אימג'ים (#954 גל 2) ----------
   נבנה לפי docs/design/console-redesign/images.md — דפדפן datastore: עץ
   תיקיות, טבלת התיקייה, מגירת אימג'. מה-API הקיים בלבד: ‏/images, ‏/folders,
   ‏/overview (אחסון, "בשימוש"), ‏/tasks (קליטות), ‏/journal (היסטוריה, admin),
   ‏POST /images/{id}/scrub (אימות חוזר). המניפסט המלא (מחיצות, כיווץ, boot_ca)
   אינו חשוף לקונסולה — "דורש API", לא מומצא (README §8).
   ‏IMAGES_FOLDER: ‏null = כל האימג'ים · "" = ללא תיקייה · "שם" = תיקייה. */
let IMG = { sel: new Set(), filter: "", scrub: {}, history: {}, tasksKey: "" };

function imagesTabs() { return ["קבצים", "קליטות", "אחסון"]; }

/* ‏GB עשרוני כלפי מעלה (#953) — "נכנס לדיסק מ-X GB"; הבייטים ב-tooltip. */
function gbCeil(bytes) {
  const n = Number(bytes);
  return bytes == null || !Number.isFinite(n) || n <= 0 ? null : Math.ceil(n / 1e9);
}
function imgFits(r) {
  const g = gbCeil(r.min_target_bytes);
  return g == null ? `<span class="muted">לא ידוע</span>` : `<span title="${esc(r.min_target_bytes)} bytes">${ltr(g + " GB")}</span>`;
}
/* "בשימוש" — סבב פעיל / סבב שיכפול מ-/overview בלבד (אין היסטוריית סבבים לאימג' — דורש API). */
function imgUsage(id) {
  const o = OVERVIEW || {}, out = [];
  if (o.session && o.session.image_id === id) out.push(`סבב ${stateLabel(o.session.state)} · ${sessionGroup(o.session)}`);
  if (o.room && o.room.image_id === id) out.push(`סבב שיכפול · גל ${o.room.wave_number || 1}`);
  return out;
}
/* אימות: אימג' בספרייה = כל sha256 אומת בכניסה (עיקרון 6; מניפסט נכנס רק
   אחרי אימות). תוצאת scrub מהישיבה הזו גוברת; תוצאה שמורה — דורש API. */
function imgVerify(r) {
  const s = IMG.scrub[r.id];
  if (s) {
    const bad = (s.files || []).filter((f) => f.state !== "ok").map((f) => f.file);
    return s.state === "intact" ? UI.status("ok", "sha256 תקין · אומת עכשיו") : UI.status("err", "sha256 לא תואם: " + bad.join(", "));
  }
  return `<span title="כל sha256 אומת בכניסה לספרייה (קליטה או העלאה)">${UI.status("ok", "sha256 אומת")}</span>`;
}

function imagesVisible() {
  const all = IMAGES || [];
  let list = IMAGES_FOLDER == null ? all : all.filter((m) => (m.folder || "") === IMAGES_FOLDER);
  const q = IMG.filter.trim().toLowerCase();
  if (q) list = list.filter((m) => [m.name, m.description, m.folder].some((v) => String(v || "").toLowerCase().includes(q)));
  return list;
}
function selectImagesFolder(key) {
  IMAGES_FOLDER = key == null ? null : decodeURIComponent(key);
  IMG.sel.clear();
  if (current === "images") renderCurrent();
}
function imagesFilter(value) {
  IMG.filter = value || "";
  const box = document.getElementById("img-table");
  if (box) box.innerHTML = imagesTableHtml();
}

/* עץ התיקיות (220px): כל האימג'ים → תיקיות (מונה) → ללא תיקייה. גרירת אימג'
   על תיקייה = העברה; גרירת תיקייה על תיקייה = סדר. Enter/רווח בוחרים. */
function imagesTree() {
  const all = IMAGES || [], admin = isAdmin();
  const node = (label, key, cnt, opts = {}) => {
    const on = IMAGES_FOLDER === key;
    const keyJs = key == null ? "null" : `'${encodeId(key)}'`;
    const drag = opts.drag ? ` draggable="true" ondragstart="folderDragStart(event,'${encodeId(key)}')"` : "";
    const drop = opts.drop ? ` ondragover="imgDragOver(event)" ondragleave="this.classList.remove('over')" ondrop="imgDrop(event,${keyJs})"` : "";
    return `<div class="f${on ? " on" : ""}${opts.muted ? " muted" : ""}" role="treeitem" tabindex="0" aria-selected="${on}" onclick="selectImagesFolder(${keyJs})" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();selectImagesFolder(${keyJs})}"${drag}${drop}>${uiIcon("folder")}<span>${esc(label)}</span><span class="cnt">${cnt}</span></div>`;
  };
  const folders = (FOLDERS || []).map((f) => node(f.name, f.name, f.images || 0, { drag: admin, drop: admin })).join("");
  const none = all.filter((m) => !m.folder).length;
  return `<div class="ftree" role="tree" aria-label="תיקיות">${node("כל האימג'ים", null, all.length)}<div class="kids">${folders}${node("ללא תיקייה", "", none, { muted: true, drop: admin })}</div></div>`
    + (admin ? `<div class="muted" style="margin-top:14px;font-size:12px">גרירת אימג' על תיקייה = העברה. סדר התיקיות והאימג'ים נקבע בגרירה (סדר התצוגה בתחנה).</div>` : "");
}

function imageRow(r) {
  const idEnc = encodeId(r.id), admin = isAdmin(), sel = IMG.sel.has(r.id);
  const use = imgUsage(r.id);
  const drag = admin ? ` draggable="true" ondragstart="imgDragStart(event,'${idEnc}')" ondragover="imgDragOver(event)" ondragleave="this.classList.remove('over')" ondrop="imgDropOnRow(event,'${idEnc}')"` : "";
  return {
    attrs: `class="${sel ? "sel" : ""}" data-id="${esc(r.id)}"${drag}`,
    cells: [
      `<input type="checkbox" aria-label="בחירת ${esc(r.name)}" ${sel ? "checked" : ""} onchange="toggleImgSel('${idEnc}',this.checked)">`,
      `<a role="link" tabindex="0" class="name" onclick="openImageDetail('${idEnc}')">${esc(r.name)}</a>${r.available === false ? ` ${UI.pill("err", "לא זמין")}` : ""}${r.description ? `<span class="sub">${esc(r.description)}</span>` : ""}`,   // ‏#1066: המיקום לא נגיש — רשום, לא נמחק
      esc(imageOsLabel(r.os)), imgFits(r), ltr(fmtBytes(r.total_compressed_bytes)), ltr(fmtDate(r.created)), imgVerify(r),
      use.length ? esc(use.join(" · ")) : `<span class="muted">—</span>`,
      UI.acts([["הפץ", `deployImage('${idEnc}')`], ["פרטים", `openImageDetail('${idEnc}')`]]),
    ],
  };
}
/* התקדמות קליטה — Progress בלבד (‏#435: מכנה אפס = "לא ידוע", לא 0%). */
function captureProgressCell(t) {
  return Progress.bar(t) + `<span class="sub">${esc(Progress.view(t).label)}</span>`;
}
/* קליטה בתהליך = שורה אפורה בטבלה (images.md), לא בר מעל. ‏/tasks אינו
   מחזיר תיקייה (#968) — מוצגת תחת "כל האימג'ים". */
function captureRowHtml(t) {
  const waiting = t.state === "pending";
  const who = t.machine || t.mac || "";
  const sub = waiting ? `קליטה בתהליך — ממתין שמחשב הבנייה ${who} יעלה ב-PXE` : `קליטה בתהליך — ${who}${t.disk ? " · " + t.disk : ""}`;
  const cancel = isAdmin() ? `<div class="acts"><button class="btn sm danger" onclick="cancelCapture(decodeURIComponent('${encodeId(t.id)}'))">בטל קליטה</button></div>` : "";
  return `<tr class="task"><td></td><td>${UI.name(t.name || "", sub)}</td><td>—</td><td>—</td><td>${ltr(fmtBytes(t.bytes_written))}</td><td>${ltr(fmtDate(t.created_at))}</td><td>${captureProgressCell(t)}</td><td>${UI.status("run", waiting ? "ממתין" : "נקלט…")}</td><td>${cancel}</td></tr>`;
}
function imagesSelBar(list) {
  const n = IMG.sel.size;
  if (!n) return "";
  const admin = isAdmin(), one = n === 1 ? encodeId([...IMG.sel][0]) : null;
  return `<div class="dg-bar foot"><span class="n">${n === 1 ? "נבחר 1" : `נבחרו ${n}`} מתוך ${list.length}</span><span class="sp"></span>`
    + (admin ? `<button class="btn sm" onclick="bulkMove()">העבר לתיקייה…</button>` : "")
    + `<button class="btn sm" onclick="bulkDownload()">הורדה</button>`
    + (admin && one ? `<button class="btn sm" onclick="renameImage('${one}')">שינוי שם</button>` : "")
    + (admin ? `<button class="btn sm danger" onclick="bulkDelete()">מחיקה</button>` : "")
    + `<button class="btn sm flat" onclick="imgSelClear()">נקה בחירה</button></div>`;
}
function imagesTableHtml() {
  const list = imagesVisible(), admin = isAdmin(), all = IMAGES || [];
  const q = IMG.filter.trim().toLowerCase();
  const tasks = IMAGES_FOLDER == null
    ? (CAPTURE_TASKS || []).filter((t) => (t.state === "pending" || t.state === "running") && (!q || String(t.name || "").toLowerCase().includes(q)))
    : [];
  if (!all.length && !tasks.length) {
    return UI.empty("אין אימג'ים בספרייה — קלוט ממחשב בנייה או העלה קובץ tar.",
      admin ? `<button class="btn primary" onclick="openCapture().catch(e => toast(e.message))">+ קליטה ממחשב בנייה</button> <button class="btn" onclick="openImageIngest()">העלאת קובץ tar</button>` : "");
  }
  if (!list.length && !tasks.length) {
    return UI.empty(IMG.filter ? "אין אימג'ים שתואמים לסינון" : "התיקייה ריקה — גררו אליה אימג', או בחרו אותה בקליטה הבאה");
  }
  const allSel = list.length > 0 && list.every((r) => IMG.sel.has(r.id));
  const columns = [{ html: `<input type="checkbox" aria-label="בחירת הכול" ${allSel ? "checked" : ""} onchange="toggleImgSelAll(this.checked)">` },
    "שם", "מערכת", "נכנס לדיסק מ-", "בשרת", "נוצר", "אימות", "בשימוש", ""];
  return UI.datagrid({ columns, rows: list.map(imageRow).concat(tasks.map((t) => ({ html: captureRowHtml(t) }))) }) + imagesSelBar(list);
}
/* ‏#954 גל 2: הדף נצבע מחדש רק כשרשימת /tasks השתנתה — אחרת הבחירה
   והסינון היו נמחקים בכל דגימה (2 שניות). */
function imagesTasksChanged(all) {
  const key = JSON.stringify(all.map((t) => [t.id, t.state, t.bytes_written, t.error]));
  if (key === IMG.tasksKey) return false;
  IMG.tasksKey = key;
  return current === "images";
}
function imagesFilesCard() {
  const admin = isAdmin();
  const folder = IMAGES_FOLDER ? (FOLDERS || []).find((f) => f.name === IMAGES_FOLDER) : null;
  const crumb = IMAGES_FOLDER == null ? `<b>כל האימג'ים</b>`
    : `<a role="link" tabindex="0" onclick="selectImagesFolder(null)">כל האימג'ים</a><span>/</span><b>${esc(IMAGES_FOLDER === "" ? "ללא תיקייה" : IMAGES_FOLDER)}</b>`;
  const desc = folder && folder.description ? `<span class="muted">— ${esc(folder.description)}</span>` : "";
  const edit = admin && folder
    ? `<button class="btn sm" onclick="editFolderSheet(FOLDERS.find(f=>f.name===decodeURIComponent('${encodeId(folder.name)}')))">עריכת תיקייה</button>`
      + (folder.images ? "" : `<button class="btn sm danger" onclick="deleteFolder('${encodeId(folder.name)}')">מחק תיקייה</button>`)
    : "";
  const bar = `<div class="dg-bar"><span class="crumbs">${crumb}</span>${desc}<span class="sp"></span><input type="search" value="${esc(IMG.filter)}" placeholder="סינון…" aria-label="סינון אימג'ים" oninput="imagesFilter(this.value)" style="width:180px">${edit}</div>`;
  const warned = (CAPTURE_TASKS || []).filter((t) => t.state === "done" && t.error).map(captureWarningHtml).join("");
  return `<div class="c12 card"><div class="split"><div class="side">${imagesTree()}</div><div><div id="capture-bar">${warned}</div>${bar}<div id="img-table">${imagesTableHtml()}</div></div></div></div>`;
}

/* לשונית "קליטות": 20 המשימות האחרונות מ-/tasks. */
function captureStateView(t) {
  const cls = t.state === "failed" ? "err" : t.state === "done" ? (t.error ? "warn" : "ok") : t.state === "pending" ? "warn" : t.state === "cancelled" ? "" : "run";
  const label = t.state === "pending" ? "ממתין שהמחשב יעלה ב-PXE" : t.state === "running" ? "קולט"
    : t.state === "done" ? (t.error ? "הושלם עם אזהרה" : "הושלם") : t.state === "failed" ? "נכשל" : t.state === "cancelled" ? "בוטל" : t.state;
  return { cls, label };
}
function imagesCapturesCard() {
  const tasks = CAPTURE_TASKS || [];
  const rows = tasks.map((t) => {
    const v = captureStateView(t);
    const active = t.state === "pending" || t.state === "running";
    const acts = [];
    if (t.image_id && findImage(t.image_id)) acts.push(["פרטים", `openImageDetail('${encodeId(t.image_id)}')`]);
    const cancel = isAdmin() && active ? `<div class="acts"><button class="btn sm danger" onclick="cancelCapture(decodeURIComponent('${encodeId(t.id)}'))">ביטול</button></div>` : "";
    return [UI.name(t.machine || t.mac || "", t.group_label || ""), `<span class="mono">${esc(t.disk || "—")}</span>`, UI.nameHtml(t.name || "", ltr(fmtDate(t.created_at) + " " + fmtClock(t.created_at))),
      UI.status(v.cls, v.label), active ? captureProgressCell(t) : ltr(fmtBytes(t.bytes_written)),
      t.error ? `<span class="${t.state === "failed" ? "muted" : "muted"}">${esc(t.error)}</span>` : "", (acts.length ? UI.acts(acts) : "") + cancel];
  });
  return UI.card({ title: "קליטות", small: "20 האחרונות", flush: true,
    body: UI.datagrid({ columns: ["מחשב בנייה", "דיסק", "אימג'", "מצב", "התקדמות", "הערה", ""], rows, empty: "אין קליטות עדיין — \"+ קליטה ממחשב בנייה\" פותח את הראשונה" }) });
}

/* לשונית "אחסון": storage מ-/overview + אימות (scrub) של הספרייה. */
function imagesStorageCard() {
  const st = OVERVIEW && OVERVIEW.storage;
  const read = !!(OVERVIEW && st);
  const total = read ? st.total_bytes : null, free = read ? st.free_bytes : null;
  const used = total != null && free != null ? total - free : null;
  const pct = total ? Math.round(100 * used / total) : null;
  const all = IMAGES || [];
  const lib = all.reduce((s, r) => s + (Number(r.total_compressed_bytes) || 0), 0);
  const kpis = `<div class="c12 kpis">`
    + UI.kpi({ cls: read ? (pct != null && pct >= 90 ? "warn" : "ok") : "", label: "פנוי בדיסק האימג'ים", value: read ? fmtBytes(free) : "—", sub: read ? `${ltr(fmtBytes(used))} בשימוש מתוך ${ltr(fmtBytes(total))}` : (OVERVIEW ? "אין נתון אחסון" : "לא נקרא"), bar: pct })
    + UI.kpi({ cls: "info", label: "הספרייה", value: fmtBytes(lib), sub: `${all.length} ${all.length === 1 ? "אימג'" : "אימג'ים"} · ${(FOLDERS || []).length} תיקיות` })
    + `</div>`;
  const results = Object.entries(IMG.scrub);
  const rows = results.map(([id, s]) => {
    const img = findImage(id);
    const bad = (s.files || []).filter((f) => f.state !== "ok");
    return [UI.name(img ? img.name : id, id), UI.status(s.state === "intact" ? "ok" : "err", s.state === "intact" ? "sha256 תקין" : "sha256 לא תואם"),
      esc(`${(s.files || []).length} קבצים`), bad.length ? esc(bad.map((f) => `${f.file}: ${f.state}${f.error ? " — " + f.error : ""}`).join(" · ")) : ""];
  });
  const scrub = isAdmin()
    ? UI.card({ title: "אימות הספרייה", small: "sha256 של כל קובץ מחיצה מול המניפסט", acts: `<button class="btn sm" onclick="scrubLibrary()">אמת את כל הספרייה</button>`, flush: true,
      body: UI.datagrid({ columns: ["אימג'", "תוצאה", "קבצים", "פרטים"], rows, empty: "לא הורץ אימות בישיבה הזו. תוצאות אימות נשמרות רק לישיבה — תוצאה אחרונה לכל אימג' דורשת API." }) })
    : "";
  return kpis + scrub;
}

function images(tab = 0) {
  const tabs = imagesTabs(), admin = isAdmin(), all = IMAGES || [];
  const st = OVERVIEW && OVERVIEW.storage;
  const sub = [`${all.length} ${all.length === 1 ? "אימג'" : "אימג'ים"}`, `${(FOLDERS || []).length} תיקיות`]
    .concat(st ? [`${ltr(fmtBytes(st.total_bytes - st.free_bytes))} בשימוש`, `${ltr(fmtBytes(st.free_bytes))} פנוי`] : []).join(" · ");
  const actions = (admin
    ? `<button class="btn primary" onclick="openCapture().catch(e => toast(e.message))">+ קליטה ממחשב בנייה</button><button class="btn" onclick="openImageIngest()">העלאת קובץ tar</button><button class="btn" onclick="addFolderSheet()">+ תיקייה</button>`
    : "") + `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>`;
  const header = UI.objHeader({ crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "מלאי" }, { label: "אימג'ים" }],
    icon: "image", name: "ספריית אימג'ים", sub, actions, tabs, tab });
  let body;
  if (!IMAGES) body = `<div class="c12">${pagePlaceholder()}</div>`;
  else if (tab === 1) body = imagesCapturesCard();
  else if (tab === 2) body = imagesStorageCard();
  else body = imagesFilesCard();
  return `<div class="page">${header}<div class="body">${body}</div></div>`;
}

/* --- בחירה מרובה --- */
function toggleImgSel(id, on) {
  id = decodeURIComponent(id);
  if (on) IMG.sel.add(id); else IMG.sel.delete(id);
  imagesFilter(IMG.filter);
}
function toggleImgSelAll(on) {
  for (const r of imagesVisible()) { if (on) IMG.sel.add(r.id); else IMG.sel.delete(r.id); }
  imagesFilter(IMG.filter);
}
function imgSelClear() { IMG.sel.clear(); imagesFilter(IMG.filter); }
function bulkMove() {
  if (!isAdmin() || !IMG.sel.size) return;
  const ids = [...IMG.sel];
  sheet({
    title: "העברה לתיקייה", sub: ids.length === 1 ? "" : `${ids.length} אימג'ים נבחרו.`,
    fields: [{ id: "folder", label: "תיקייה", type: "select", value: IMAGES_FOLDER || "",
      options: [{ value: "", label: "ללא תיקייה" }].concat((FOLDERS || []).map((f) => ({ value: f.name, label: f.name }))) }],
    submitLabel: "העבר",
    onSubmit: async (v) => { await moveImages(ids, v.folder); },
  });
}
function bulkDownload() {
  for (const id of IMG.sel) {
    const a = document.createElement("a");
    a.href = "/api/console/images/" + encodeId(id) + "/download";
    a.download = id + ".tar";
    document.body.appendChild(a); a.click(); a.remove();
  }
}
/* מחיקה מרובה: הקלדת שם לכל אימג' (עיקרון 7) — השרת מאמת כל שם בנפרד. */
function bulkDelete() {
  if (!isAdmin() || !IMG.sel.size) return;
  const imgs = [...IMG.sel].map((id) => findImage(id)).filter(Boolean);
  if (imgs.length === 1) { deleteImage(encodeId(imgs[0].id)); return; }
  sheet({
    title: `מחיקת ${imgs.length} אימג'ים`, danger: true, submitLabel: "מחק",
    sub: "האימג'ים וכל קבציהם יימחקו לצמיתות מהשרת. אין דרך חזרה. הקלידו את שם כל אימג' לאישור.",
    fields: imgs.map((m, i) => ({ id: "n" + i, label: m.name, placeholder: m.name })),
    onSubmit: async (v) => {
      imgs.forEach((m, i) => { if ((v["n" + i] || "").trim() !== m.name) throw new Error(`השם שהוקלד אינו זהה: ${m.name}`); });
      for (const [i, m] of imgs.entries()) await post("/images/" + encodeId(m.id) + "/delete", { confirm_name: v["n" + i].trim() });
      IMG.sel.clear(); closeDrawer(); toast(`${imgs.length} אימג'ים נמחקו`); await loadImages();
    },
  });
}

/* --- גרירה: אימג' → תיקייה = העברה; תיקייה → תיקייה = סדר; אימג' → אימג' = סדר בתיקייה --- */
function imgDragStart(ev, id) {
  id = decodeURIComponent(id);
  const ids = IMG.sel.has(id) ? [...IMG.sel] : [id];
  ev.dataTransfer.setData("application/x-imagectl-images", JSON.stringify(ids));
  ev.dataTransfer.effectAllowed = "move";
}
function folderDragStart(ev, name) {
  ev.dataTransfer.setData("application/x-imagectl-folder", decodeURIComponent(name));
  ev.dataTransfer.effectAllowed = "move";
}
function imgDragOver(ev) {
  if (!isAdmin()) return;
  ev.preventDefault();
  ev.currentTarget.classList.add("over");
}
async function imgDrop(ev, folder) {
  ev.preventDefault(); ev.currentTarget.classList.remove("over");
  folder = folder == null ? null : decodeURIComponent(folder);
  const ids = ev.dataTransfer.getData("application/x-imagectl-images");
  if (ids) { await moveImages(JSON.parse(ids), folder); return; }
  const name = ev.dataTransfer.getData("application/x-imagectl-folder");
  if (name && folder && name !== folder) await reorderFolderBefore(name, folder);
}
async function imgDropOnRow(ev, targetId) {
  ev.preventDefault(); ev.currentTarget.classList.remove("over");
  const ids = ev.dataTransfer.getData("application/x-imagectl-images");
  if (!ids) return;
  const [id] = JSON.parse(ids);
  await reorderImageBefore(id, decodeURIComponent(targetId));
}
async function moveImages(ids, folder) {
  try {
    for (const id of ids) await put("/images/" + encodeId(id), { folder: folder || "" });
    IMG.sel.clear();
    toast(ids.length === 1 ? "הועבר." : `${ids.length} אימג'ים הועברו.`);
    await loadImages();
  } catch (e) { toast(e.message); }
}
async function reorderFolderBefore(name, before) {
  const names = (FOLDERS || []).map((f) => f.name).filter((n) => n !== name);
  const i = names.indexOf(before);
  names.splice(i < 0 ? names.length : i, 0, name);
  try { await post("/folders/order", { names }); await loadImages(); } catch (e) { toast(e.message); }
}
async function reorderImageBefore(id, beforeId) {
  const img = findImage(id), target = findImage(beforeId);
  if (!img || !target || img === target || (img.folder || "") !== (target.folder || "")) return;
  const list = (IMAGES || []).filter((m) => (m.folder || "") === (img.folder || "") && m !== img);
  list.splice(list.indexOf(target), 0, img);
  try {
    for (let n = 0; n < list.length; n++) await put("/images/" + encodeId(list[n].id), { sort: n + 1 });
    await loadImages();
  } catch (e) { toast(e.message); }
}

/* --- פעולות --- */
function deployImage(id) {
  const img = findImage(id);
  selectPageById("deploy");
  if (img) toast(`סבב הפצה נפתח ממסך התחנה או מחדר המשכפלים — "${img.name}"`);
}
function cancelCapture(taskId) {
  confirmSheet("ביטול הקליטה", "המשימה תבוטל והקבצים שהתקבלו יימחקו.", "בטל את הקליטה",
    async () => { await post(`/tasks/${encodeId(taskId)}/cancel`); await loadCaptures(); });
}
function deleteFolder(name) {
  if (!isAdmin()) return;
  name = decodeURIComponent(name);
  confirmSheet("מחיקת תיקייה", `התיקייה "${name}" ריקה ותימחק. האימג'ים אינם נמחקים לעולם מכאן.`, "מחק תיקייה",
    async () => { await post(`/folders/${encodeId(name)}/delete`); IMAGES_FOLDER = null; await loadImages(); });
}
async function scrubImage(id) {
  if (!isAdmin()) return;
  const img = findImage(id); if (!img) return;
  toast(`מאמת sha256 של "${img.name}"…`, 6000);
  try {
    const r = await post("/images/" + encodeId(img.id) + "/scrub");
    for (const one of r.images || []) IMG.scrub[one.id] = one;
    toast(r.intact ? "sha256 תקין." : "sha256 לא תואם — האימג' פגום!", 6000);
    if (current === "images") renderCurrent();
    openImageDetail(img.id);
  } catch (e) { toast(e.message); }
}
async function scrubLibrary() {
  if (!isAdmin()) return;
  toast("מאמת את כל הספרייה…", 8000);
  try {
    const r = await post("/images/scrub");
    for (const one of r.images || []) IMG.scrub[one.id] = one;
    toast(r.intact ? "כל הספרייה תקינה." : "נמצא אימג' פגום — ראו את הטבלה.", 6000);
    if (current === "images") renderCurrent();
  } catch (e) { toast(e.message); }
}

/* --- מגירת אימג' (image-drawer): כותרת → פעולות → אימות → מאפיינים →
   מחיצות (מתוצאת scrub) → היסטוריה (יומן + קליטות) → פעולות משניות. --- */
function imageHistory(img) {
  const events = [];
  for (const t of CAPTURE_TASKS || []) {
    if (t.image_id !== img.id || t.state !== "done") continue;
    events.push({ ts: t.updated_at || t.created_at, cls: t.error ? "warn" : "ok",
      text: esc(`נקלט ממחשב הבנייה ${t.machine || t.mac || ""}${t.disk ? " · " + t.disk : ""}`) + (t.error ? ` — ${esc(t.error)}` : ""), who: "" });
  }
  const j = IMG.history[img.id];
  if (Array.isArray(j)) for (const r of j) events.push({ ts: r.ts, cls: journalCls(r), text: esc(r.label || r.event) + (r.text ? ` — ${esc(r.text)}` : ""), who: r.user });
  events.sort((a, b) => String(b.ts).localeCompare(String(a.ts)));
  return events.map((e) => ({ t: isToday(e.ts) ? fmtClock(e.ts) : fmtDate(e.ts), cls: e.cls, text: e.text, who: e.who }));
}
function imageDrawerHtml(img) {
  const admin = isAdmin(), idEnc = encodeId(img.id);
  const use = imgUsage(img.id);
  const s = IMG.scrub[img.id];
  const actions = `<div class="acts" style="display:flex;gap:6px;flex-wrap:wrap">${classroomsOn() ? `<button class="btn primary" onclick="deployImage('${idEnc}')">הפץ לכיתה…</button>` : ""}<a class="btn" href="/api/console/images/${idEnc}/download">הורדה</a>${admin ? `<button class="btn" onclick="scrubImage('${idEnc}')">אימות חוזר</button>` : ""}</div>`;
  const verify = s
    ? (s.state === "intact" ? UI.note("ok", "sha256 של כל קובץ מחיצה תואם למניפסט — אומת עכשיו.") : UI.note("err", "sha256 לא תואם: " + esc((s.files || []).filter((f) => f.state !== "ok").map((f) => `${f.file} (${f.state})`).join(", ")) + " — האימג' פגום, אין להפיץ אותו."))
    : UI.note("ok", `sha256 של כל מחיצה אומת בכניסה לספרייה.${admin ? ` אימות חוזר: ${UI.link("הרץ עכשיו", `scrubImage('${idEnc}')`)}` : ""}`);
  const kv = UI.kv([
    ["תיאור", esc(img.description || "—") + (admin ? ` ${UI.link("עריכה", `editImageDescription('${idEnc}')`)}` : "")],
    ["תיקייה", esc(img.folder || "ללא תיקייה") + (admin ? ` ${UI.link("העבר", `moveImage('${idEnc}')`)}` : "")],
    ["מערכת", esc(imageOsLabel(img.os))],
    ["דיסק מקור", img.source_disk_bytes == null ? `<span class="muted">לא ידוע</span>` : `${ltr(fmtBytes(img.source_disk_bytes))} · family ${esc(img.family)}`],
    ["נכנס לדיסק מ-", imgFits(img)],
    ["בשימוש במקור", img.used_bytes == null ? `<span class="muted">לא ידוע</span>` : ltr(fmtBytes(img.used_bytes))],
    ["גודל בשרת", `${ltr(fmtBytes(img.total_compressed_bytes))} דחוס`],
    ["מחיצות", esc(String(img.partitions ?? "—"))],
    ["שימוש", use.length ? esc(use.join(" · ")) : `<span class="muted">לא בסבב פעיל</span>`],
  ]);
  const parts = s && (s.files || []).length
    ? UI.datagrid({ columns: ["#", "קובץ", "sha256"], rows: s.files.map((f) => [esc(f.index), `<span class="mono">${esc(f.file)}</span>`, UI.status(f.state === "ok" ? "ok" : "err", f.state === "ok" ? "תקין" : f.state)]) })
    : UI.note("", `טבלת המחיצות (תפקיד, מערכת קבצים, גדלים, כיווץ המקור, רשויות האתחול) — <span title="המניפסט המלא אינו חשוף ב-/api/console">דורש API</span>.${admin ? ` ‏sha256 לכל קובץ מחיצה: ${UI.link("אימות חוזר", `scrubImage('${idEnc}')`)}.` : ""}`);
  const hist = imageHistory(img);
  const jErr = IMG.history[img.id] && IMG.history[img.id].error;
  const history = hist.length ? UI.timeline(hist)
    : jErr ? UI.note("warn", "היומן לא נקרא: " + esc(jErr))
    : `<div class="muted">${admin ? "אין אירועים ביומן לאימג' הזה (20 האחרונים)." : "היסטוריית היומן זמינה למנהל בלבד."}</div>`;
  const foot = admin
    ? `<div style="border-top:1px solid var(--hair);padding-top:12px;display:flex;gap:6px;flex-wrap:wrap"><button class="btn sm" onclick="renameImage('${idEnc}')">שינוי שם</button><button class="btn sm" onclick="moveImage('${idEnc}')">העבר לתיקייה</button><button class="btn sm danger" onclick="deleteImage('${idEnc}')">מחיקה (הקלדת שם)</button></div>`
    : "";
  return `<div class="page drw"><div class="obj-sub">${esc(img.folder || "ללא תיקייה")} / <span class="mono">${esc(img.id)}</span> · ${esc(imageOsLabel(img.os))} · נוצר ${ltr(fmtDate(img.created))}</div>${actions}${verify}<div><div class="sec">מאפיינים</div>${kv}</div><div><div class="sec">מחיצות</div>${parts}</div><div><div class="sec">היסטוריה</div>${history}</div>${foot}</div>`;
}
function openImageDetail(id) {
  const img = findImage(id);
  if (!img) { toast("אימג' לא נמצא"); return; }
  openDrawer(img.name, imageDrawerHtml(img));
  if (!isAdmin() || IMG.history[img.id]) return;
  const gen = drawerGeneration;
  api("/journal?q=" + encodeURIComponent(img.name) + "&limit=20")
    .then((rows) => { IMG.history[img.id] = rows; })
    .catch((e) => { IMG.history[img.id] = { error: e.message }; })
    .then(() => { if (gen === drawerGeneration) document.getElementById("drawerBody").innerHTML = imageDrawerHtml(img); });
}

function renameImage(id) {
  if (!isAdmin()) return;
  const img = findImage(id);
  if (!img) return;
  openModalContent(
    "שינוי שם",
    `<div class="form"><div class="field full"><label>השם החדש</label><input id="renameInput" autocomplete="off"></div></div>`,
    "שמור",
    () => applyRename(img.id)
  );
  const input = document.getElementById("renameInput");
  if (input) { input.value = img.name; input.focus(); }
}

async function applyRename(id) {
  const input = document.getElementById("renameInput");
  const name = input ? input.value : "";
  if (!name) { toast("שם לא יכול להיות ריק"); return; }
  try {
    await put("/images/" + encodeId(id), { name });
    closeModal();
    toast("נשמר.");
    await loadImages();
    openImageDetail(id);
  } catch (e) {
    toast(e.message);
  }
}

function moveImage(id) {
  if (!isAdmin()) return;
  const img = findImage(id);
  if (!img) return;
  const options = [`<option value="">ללא תיקייה</option>`]
    .concat((FOLDERS || []).map((f) => `<option value="${esc(f.name)}">${esc(f.name)}</option>`))
    .join("");
  openModalContent(
    "העברה לתיקייה",
    `<div class="form"><div class="field full"><label>תיקייה</label><select id="moveFolder">${options}</select></div></div>`,
    "העבר",
    () => applyMove(img.id)
  );
  const sel = document.getElementById("moveFolder");
  if (sel) sel.value = img.folder || "";
}

async function applyMove(id) {
  const sel = document.getElementById("moveFolder");
  const folder = sel ? sel.value : "";
  try {
    await put("/images/" + encodeId(id), { folder });
    closeModal();
    toast("נשמר.");
    await loadImages();
    openImageDetail(id);
  } catch (e) {
    toast(e.message);
  }
}

function deleteImage(id) {
  if (!isAdmin()) return;
  const img = findImage(id);
  if (!img) return;
  openModalContent(
    "מחיקת אימג׳",
    `<div class="confirm-box">האימג׳ וכל קבציו יימחקו לצמיתות מהשרת. אין דרך חזרה.</div><div class="danger-confirm">הקלידו את שם האימג׳ <b>${esc(img.name)}</b> לאישור.</div><div class="form"><div class="field full"><label>שם האימג׳</label><input id="confirmName" autocomplete="off"></div></div>`,
    "מחק",
    () => confirmDeleteImage(img.id)
  );
  const input = document.getElementById("confirmName");
  if (input) input.focus();
}

async function confirmDeleteImage(id) {
  const input = document.getElementById("confirmName");
  const typed = input ? input.value.trim() : "";
  try {
    await post("/images/" + encodeId(id) + "/delete", { confirm_name: typed });
    closeModal();
    closeDrawer();
    toast("האימג׳ נמחק");
    loadImages();
  } catch (e) {
    toast(e.message);
  }
}

function openImageIngest() {
  if (!(ME && ME.role === "admin")) { toast("אין הרשאה"); return; }
  openModalContent(
    "קליטת אימג׳",
    `<div class="form"><div class="field full"><label>קובץ tar</label><input type="file" id="imageFile" accept=".tar,application/x-tar"></div><div class="field full"><div class="form-note">הקובץ יועלה ויאומת מול ה-manifest. אימג׳ פגום לא ייכנס לספרייה.</div></div></div>`,
    "העלה",
    startImageUpload
  );
}

function startImageUpload() {
  const input = document.getElementById("imageFile");
  const file = input && input.files && input.files[0];
  if (!file) { toast("בחר קובץ לפני העלאה"); return; }
  uploadImage(file);
}

function uploadImage(file) {
  const body = document.getElementById("modalBody");
  const ok = document.getElementById("modalOk");
  if (ok) ok.disabled = true;
  const draw = (pct, text) => {
    body.innerHTML = `<div><div class="list-row"><div class="list-main"><strong>${esc(file.name)}</strong><small>${esc(text)}</small></div><div class="list-side">${pct}%</div></div><div class="progress" style="margin-top:7px"><i style="width:${pct}%"></i></div></div>`;
  };
  draw(0, "מתחיל…");

  const request = new XMLHttpRequest();
  request.open("POST", "/api/console/images/upload");
  request.setRequestHeader("Content-Type", "application/x-tar");
  request.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const pct = Math.round((100 * e.loaded) / e.total);
    draw(pct, `${pct}% · ${fmtBytes(e.loaded)} מתוך ${fmtBytes(e.total)}`);
  };
  request.onload = () => {
    if (request.status === 200) {
      let name = file.name;
      try { name = JSON.parse(request.responseText).name || name; } catch (e) {}
      closeModal();
      toast(`"${name}" נוסף לספרייה.`);
      loadImages();
    } else {
      let message = "שגיאה " + request.status;
      try { message = JSON.parse(request.responseText).detail || message; } catch (e) {}
      body.innerHTML = `<div class="notice err">ההעלאה נכשלה: ${esc(message)}</div>`;
      if (ok) { ok.disabled = false; ok.textContent = "סגור"; ok.onclick = closeModal; }
    }
  };
  request.onerror = () => {
    body.innerHTML = `<div class="notice err">ההעלאה נכשלה — החיבור נותק.</div>`;
    if (ok) { ok.disabled = false; ok.textContent = "סגור"; ok.onclick = closeModal; }
  };
  request.send(file);
}

async function startRound() {
  if (!ME || !["admin", "deploy"].includes(ME.role)) return;
  const session = OVERVIEW && OVERVIEW.session;
  if (!session || !session.id) return;
  try {
    await post("/sessions/" + encodeId(session.id) + "/start");
    toast("הסבב התחיל");
    refreshStatus();
  } catch (e) {
    toast(e.message);
  }
}

function stopRound() {
  if (!ME || !["admin", "deploy"].includes(ME.role)) return;
  const session = OVERVIEW && OVERVIEW.session;
  if (!session || !session.id) return;
  const sessionId = session.id;
  const name = sessionImage(session);
  openModalContent(
    "עצירת סבב",
    `<div class="confirm-box">עצירת הסבב תמנע הצטרפות חדשה ותעצור את השידור.</div><div class="danger-confirm">הקלידו את שם האימג׳ <b>${esc(name)}</b> לאישור.</div><div class="form"><div class="field full"><label>שם האימג׳</label><input id="confirmName" autocomplete="off"></div></div>`,
    "עצור",
    () => confirmStopRound(sessionId)
  );
  const input = document.getElementById("confirmName");
  if (input) input.focus();
}

async function confirmStopRound(sessionId) {
  const input = document.getElementById("confirmName");
  const typed = input ? input.value.trim() : "";
  try {
    await post("/sessions/" + encodeId(sessionId) + "/close", { confirm_name: typed });
    closeModal();
    toast("הסבב נעצר");
    refreshStatus();
  } catch (e) {
    toast(e.message);
  }
}

/* ---------- #954 גל 3: מחשבים — טבלה אחת מקובצת, מגירת מחשב, כיתה כאובייקט ----------
   נבנה לפי docs/design/console-redesign/machines.md (+ class.md, machine-drawer.md).
   תיקון נדב (17/09): מחשבי כיתה מחולקים **לפי כיתה** — כל כיתה קבוצה מתקפלת
   ושורת roll-up, כמו Hosts & Clusters. בנייה ושיכפול = הקבוצות הקבועות.
   מקורות: /machines, /groups, /net (join לפי MAC: IP, נראה לאחרונה, שלב אתחול),
   /disk-failures, /shrink-records, /monitor/machines ("מחובר" — בנייה/שיכפול
   בלבד, נקבע בשרת), /overview.session (סבב פעיל לכיתה). מה שאין לו API —
   "מחובר עכשיו" לתחנת כיתה, WoL למחשב יחיד, אתחול מרחוק, עריכת MAC, קידומת
   קבועה לכיתה, היסטוריית סבבים — מוצג כ"דורש API", לא מומצא (Issue מרוכז).
   עיקרון 5: `disks` null/[]/רשימה הם שלושה מצבים; "לא נקרא" ≠ ריק; "לא נבדק"
   לעולם לא ירוק. "דיסק 1/2/3 · SATA 0/1/2" — לעולם לא sd*. */
let NET = null, NET_ERR = "";            // ‏/net — null = לא נקרא (≠ רשימה ריקה)
let MONITOR_ROWS = null;                 // ‏/monitor/machines — online מהשרת; null = לא נקרא
let MACHINES_CLASS = null;               // הקבוצה הפתוחה כאובייקט (כיתה) — id
const MCH = { q: "", role: "", group: "", state: "", toggled: new Set(), sel: new Set(), history: {}, amTab: 0 };
const ROLE_HE = { classroom: "תחנת כיתה", build: "מחשב בנייה", cloner: "מחשב שיכפול", unknown: "ללא קבוצה" };
const ROLE_GROUP_HE = { classroom: "כיתות", build: "מחשבי בנייה", cloner: "מחשבי שיכפול" };
const ROLE_ORDER = { build: 0, cloner: 1, classroom: 2 };
const FOLD_ABOVE = 50;                   // מעל כך שורות — הקבוצות מקופלות כברירת מחדל

function machineGroup(m) { const id = machineGroupId(m); return (GROUPS || []).find((g) => g.id === id) || null; }
function machineRole(m) { const g = machineGroup(m); return g ? g.role : "unknown"; }
function netFor(mac) { return (NET || []).find((d) => d.mac === mac) || null; }
function monitorRow(mac) { return (MONITOR_ROWS || []).find((r) => r.mac === mac) || null; }
function seenAgo(iso) { return iso ? ago(iso) : "מעולם לא"; }
function groupSession(gid) { const s = OVERVIEW && OVERVIEW.session; return s && gid && s.group_id === gid ? s : null; }
function sessionMember(m) { const s = groupSession(machineGroupId(m)); return s ? (s.members || []).find((x) => x.mac === m.mac) || null : null; }
function machineGroupsInOrder() {
  return (GROUPS || []).slice()
    .filter((g) => classroomsOn() || g.role !== "classroom")
    .sort((a, b) => (ROLE_ORDER[a.role] ?? 9) - (ROLE_ORDER[b.role] ?? 9));
}
function machineModel(m) {
  const dmi = m.inventory && m.inventory.dmi;
  return dmi ? [dmi.sys_vendor, dmi.product_version || dmi.product_name].filter(Boolean).join(" ") : "";
}
function machineSub(m) {
  const parts = [machineModel(m)];
  const tpm = m.inventory && m.inventory.tpm;
  if (tpm && tpm.present) parts.push(`TPM ${tpm.version || ""}`.trim());
  if (machineRole(m) === "cloner" && m.drawer_count != null) parts.push(`${m.drawer_count} חריצי SATA`);
  return parts.filter(Boolean).join(" · ");
}
/* דיסק N = החריץ שהסוכן דיווח (`port`, 1-based); בלי `port` — המקום ברשימה. SATA = N-1. */
function diskSlot(d, i) { return d.port != null ? Number(d.port) : i + 1; }
/* #874: אדום לפי סידורי (הדיסק נודד) **וגם** לפי מכונה+חריץ (לפעמים הפורט אשם). */
function machineDiskFailures(m) {
  const serials = new Set((m.disks || []).map((d) => d.serial).filter(Boolean));
  return (DISK_FAILURES || []).filter((f) => (f.mac === m.mac && f.port != null) || (f.serial && serials.has(f.serial)));
}
function failureSlot(m, f) {
  if (f.mac === m.mac && f.port != null) return Number(f.port);
  const i = (m.disks || []).findIndex((d) => d.serial && d.serial === f.serial);
  return i >= 0 ? diskSlot(m.disks[i], i) : null;
}
function failureCauseText(f) {
  const cause = FAILURE_CAUSE_HE[f.cause] || f.cause || "";
  if (f.cause === "cable" && f.port != null) return `${cause} SATA ${f.port - 1}`;
  return cause;
}
/* ‏#402: "0 דיסקים" הם שלושה ממצאים שהפעולה עליהם הפוכה, ו-`disk_probe` מה-hello (ממשק 2) מבחין:
   no_disks = יש פורטי SATA, לא חובר כונן (חברו כונן); no_ports = בקר ה-SATA מדווח 0 פורטים — מנוטרל
   בקושחה (געו בביוס, כבל לא יעזור); unchecked = לא נספר (אין שורת ahci ב-dmesg) ≠ no_disks (עיקרון 5);
   null = סוכן ישן שלא שלח — הטקסט הישן, לא ניחוש. */
const DISK_PROBE_HE = { no_disks: "לא חוברו דיסקים", no_ports: "אין פורטי SATA בקושחה", unchecked: "0 דיסקים · לא נבדק" };
function zeroDisksText(m) { return DISK_PROBE_HE[m.disk_probe] || "דיווח 0 דיסקים"; }
function disksCell(m) {
  if (m.disks == null) return `<span class="muted">לא דיווח</span>`;
  if (!m.disks.length) return UI.status("warn", zeroDisksText(m));
  const sizes = [...new Set(m.disks.map((d) => fmtBytes(d.size_bytes)))].map(ltr).join(" / ");
  const red = [...new Set(machineDiskFailures(m).map((f) => failureSlot(m, f)).filter((n) => n != null))].sort();
  return `${m.disks.length} · ${sizes}${red.map((n) => " " + UI.pill("err", `דיסק ${n} אדום`)).join("")}`;
}
function waitingText(m) { return "ממתין למפעיל: " + (PROMPT_HE[m.prompt] || m.prompt); }
/* המצב: נקודה צבעונית + מילים. ירוק רק על ראיה חיובית (השרת אמר "מחובר",
   הסבב הושלם); "לא נבדק"/"לא נקרא" אפורים. */
function machineState(m) {
  if (m.prompt) return { cls: "warn", text: waitingText(m) };
  const role = machineRole(m), net = netFor(m.mac);
  if (role === "build" || role === "cloner") {
    const mon = monitorRow(m.mac);
    if (!mon) return { cls: "", text: MONITOR_ROWS ? "לא נבדק" : "לא נקרא" };
    return mon.online ? { cls: "ok", text: "מחובר" } : { cls: "", text: net && net.last_seen ? "לא מחובר" : "לא נראה" };
  }
  const member = sessionMember(m);
  if (member) {
    if (member.state === "failed") return { cls: "err", text: "נכשל בסבב" + (member.error ? " — " + member.error : "") };
    if (member.done) return { cls: member.error ? "warn" : "ok", text: member.error ? "הסבב הושלם עם אזהרה" : "הסבב הושלם" };
    return { cls: "run", text: "בסבב · " + (member.state === "waiting" ? "ממתין להתחלה" : Progress.view(member).label) };
  }
  if (groupSession(machineGroupId(m))) return { cls: "warn", text: "חסר בסבב" };
  if (net && net.last_seen) return { cls: "", text: "נראה " + ago(net.last_seen) };
  return { cls: "", text: NET ? "לא נראה" : "לא נקרא" };
}
function stale30(mac) {
  const net = netFor(mac);
  return !net || !net.last_seen || Date.now() - new Date(net.last_seen).getTime() > 30 * 86400e3;
}
function machineMatches(m) {
  const q = MCH.q.trim().toLowerCase();
  if (q) {
    const net = netFor(m.mac);
    const hay = [machineName(m), m.mac, net && net.ip, machineModel(m)].filter(Boolean).join(" ").toLowerCase();
    if (!hay.includes(q)) return false;
  }
  if (MCH.role && machineRole(m) !== MCH.role) return false;
  if (MCH.group && machineGroupId(m) !== MCH.group) return false;
  if (MCH.state === "ok" && machineState(m).cls !== "ok") return false;
  if (MCH.state === "warn" && !m.prompt) return false;
  if (MCH.state === "err" && !machineDiskFailures(m).length) return false;
  if (MCH.state === "stale" && !stale30(m.mac)) return false;
  return true;
}
function machinesScope() {
  const gid = MACHINES_CLASS || MACHINES_FILTER;
  if (gid) return (MACHINES || []).filter((m) => machineGroupId(m) === gid);
  if (MACHINES_ROLE) return (MACHINES || []).filter((m) => machineRole(m) === MACHINES_ROLE);
  const all = MACHINES || [];
  if (classroomsOn()) return all;
  return all.filter((m) => machineRole(m) !== "classroom");
}
function machinesVisible() { return machinesScope().filter(machineMatches); }
function groupOpen(gid) {
  const def = (MACHINES || []).length <= FOLD_ABOVE;
  return MCH.toggled.has(gid) ? !def : def;
}
function toggleMchGroup(gidEnc) {
  let gid = gidEnc; try { gid = decodeURIComponent(gidEnc); } catch (e) {}
  if (MCH.toggled.has(gid)) MCH.toggled.delete(gid); else MCH.toggled.add(gid);
  redrawMachinesTable();
}
function redrawMachinesTable() {
  const el = document.getElementById("mch-table");
  if (el) el.innerHTML = machinesTableHtml();
  const n = document.getElementById("mch-count");
  if (n) n.textContent = machinesCountText();
}
function machinesCountText() {
  const shown = machinesVisible().length, scope = machinesScope().length;
  return shown === scope ? `${scope} מחשבים` : `${shown} מתוך ${scope} מחשבים`;
}
function mchFilter(key, value) { MCH[key] = value; redrawMachinesTable(); }

function machineRowHtml(m) {
  const macEnc = encodeId(m.mac), net = netFor(m.mac), st = machineState(m), role = machineRole(m);
  const name = machineName(m) || m.mac, sub = machineSub(m), sel = MCH.sel.has(m.mac);
  const acts = [];
  acts.push(["פרטים", `openMachineDetail('${macEnc}')`]);
  return { attrs: `data-mac="${esc(m.mac)}"${sel ? ' class="sel"' : ""}`, cells: [
    `<input type="checkbox" aria-label="בחר ${esc(name)}"${sel ? " checked" : ""} onchange="toggleMchSel('${macEnc}',this.checked)">`,
    `<a class="name" role="link" tabindex="0" onclick="openMachineDetail('${macEnc}')">${esc(name)}</a>${sub ? `<span class="sub">${esc(sub)}</span>` : ""}`,
    `<span class="mono">${esc(m.mac)}</span>`,
    net && net.ip ? `<span class="mono">${esc(net.ip)}</span>` : `<span class="muted">—</span>`,
    net && net.last_seen ? esc(ago(net.last_seen)) : `<span class="muted">${NET ? "מעולם לא" : "לא נקרא"}</span>`,
    disksCell(m), UI.status(st.cls, st.text), UI.acts(acts)] };
}
/* שורת הקבוצה — roll-up: שם (כיתה → אובייקט), מונה, מחוברים/סבב רק כשיש נתון, פעולות. */
function groupRowHtml(g, kids) {
  const open = groupOpen(g.id), gidEnc = encodeId(g.id), fixed = g.role !== "classroom";
  const arrow = `<button type="button" class="btn sm flat" aria-expanded="${open}" aria-label="${open ? "קפל" : "פתח"} ${esc(g.label)}" onclick="toggleMchGroup('${gidEnc}')">${open ? "▾" : "▸"}</button>`;
  const title = `<a role="link" tabindex="0" onclick="openClass('${gidEnc}')">${esc(g.label)}</a>`;
  const s = groupSession(g.id);
  const meta = [fixed ? "קבוצה קבועה" : "כיתה", `${kids.length} ${kids.length === 1 ? "מחשב" : "מחשבים"}`];
  if (s && s.prefix) meta.push(`קידומת ${s.prefix}`);
  let live = "";
  if (fixed && MONITOR_ROWS) {
    const on = kids.filter((m) => (monitorRow(m.mac) || {}).online).length;
    live += UI.status(on ? "ok" : "", `${on} מחוברים`);
  }
  if (s) live += UI.status("run", `סבב ${stateLabel(s.state)} — ${sessionImage(s)}, ${s.joined || 0}/${sessionExpected(s)}`);
  const acts = fixed
    ? [["פתח", `openClass('${gidEnc}')`], ["+ מחשב", `openAddMachine({group:'${gidEnc}'})`]]
    : [["פתח כיתה", `openClass('${gidEnc}')`], ["+ מחשב לכיתה", `openAddMachine({group:'${gidEnc}'})`]];
  if (g.role === "cloner") acts.push(["WoL לחדר", "wakeRoom()"]);
  if (g.role === "build") acts.push(["WoL לבנייה", `wakeGroup('${gidEnc}')`]);   // ‏#984
  return { html: `<tr class="group" data-group="${esc(g.id)}"><td colspan="8"><div class="grp">${arrow}${title}<span class="muted">${esc(meta.join(" · "))}</span>${live}<span class="sp"></span>${UI.acts(acts)}</div></td></tr>` };
}
function machinesTableHtml() {
  if (!(MACHINES || []).length) {
    return UI.empty("אין מחשבים רשומים — הוסיפו את הראשון, או הדביקו רשימת MAC.",
      isAdmin() ? `<button class="btn primary" onclick="openAddMachine({})">+ מחשב</button>` : "");
  }
  const list = machinesVisible();
  const gid = MACHINES_CLASS || MACHINES_FILTER;
  if (!list.length) {
    const filtered = MCH.q || MCH.role || MCH.group || MCH.state;
    return UI.empty(filtered ? "אין מחשבים שתואמים לסינון"
      : gid ? `אין מחשבים בקבוצה ${groupLabel(gid)} (${MACHINES.length} רשומים בסך הכול)` : "אין מחשבים רשומים");
  }
  const columns = [{ html: `<input type="checkbox" aria-label="בחר את כל המוצגים" onchange="toggleMchSelAll(this.checked)">` },
    "שם", "MAC", "IP אחרון", "נראה לאחרונה", "דיסקים", "מצב", ""];
  let rows;
  if (gid) rows = sortMachinesBySuffix(list).map(machineRowHtml);
  else {
    rows = [];
    const groups = machineGroupsInOrder();
    const orphan = list.filter((m) => !groups.some((g) => g.id === machineGroupId(m)));
    for (const g of groups.concat(orphan.length ? [{ id: "", label: "ללא קבוצה", role: "unknown" }] : [])) {
      const kids = sortMachinesBySuffix(g.id ? list.filter((m) => machineGroupId(m) === g.id) : orphan);
      if (!kids.length) continue;
      rows.push(groupRowHtml(g, kids));
      if (groupOpen(g.id)) rows.push(...kids.map(machineRowHtml));
    }
  }
  return UI.datagrid({ columns, rows }) + mchSelBar(list);
}
function mchSelBar(list) {
  const n = list.filter((m) => MCH.sel.has(m.mac)).length;
  if (!n) return "";
  return `<div class="dg-bar foot"><span class="n">נבחרו ${n} מתוך ${list.length}</span><span class="sp"></span><button class="btn sm" onclick="bulkMoveMachines()">העבר לקבוצה…</button><button class="btn sm danger" onclick="bulkRemoveMachines()">הסרה (הקלדת שם)</button><button class="btn sm" onclick="mchSelClear()">נקה בחירה</button></div>`;
}
function toggleMchSel(macEnc, on) {
  let mac = macEnc; try { mac = decodeURIComponent(macEnc); } catch (e) {}
  if (on) MCH.sel.add(mac); else MCH.sel.delete(mac);
  redrawMachinesTable();
}
function toggleMchSelAll(on) {
  for (const m of machinesVisible()) { if (on) MCH.sel.add(m.mac); else MCH.sel.delete(m.mac); }
  redrawMachinesTable();
}
function mchSelClear() { MCH.sel.clear(); redrawMachinesTable(); }

function machinesBar(scoped) {
  const opt = (value, label, cur) => `<option value="${esc(value)}"${value === cur ? " selected" : ""}>${esc(label)}</option>`;
  const scopeSelects = scoped ? "" :
    `<select aria-label="תפקיד" onchange="mchFilter('role',this.value)">${opt("", "כל התפקידים", MCH.role)}${(classroomsOn() ? ["classroom", "build", "cloner"] : ["build", "cloner"]).map((r) => opt(r, ROLE_GROUP_HE[r], MCH.role)).join("")}</select>`
    + `<select aria-label="קבוצה" onchange="mchFilter('group',this.value)">${opt("", "כל הקבוצות", MCH.group)}${machineGroupsInOrder().map((g) => opt(g.id, g.label, MCH.group)).join("")}</select>`;
  const states = [["", "כל המצבים"], ["ok", "מחובר עכשיו"], ["warn", "ממתין למפעיל"], ["err", "דיסק אדום"], ["stale", "לא נראה 30 יום"]];
  return `<div class="dg-bar"><input type="search" aria-label="חיפוש מחשב" placeholder="חיפוש: שם, MAC, IP…" value="${esc(MCH.q)}" oninput="mchFilter('q',this.value)" style="width:220px">${scopeSelects}<select aria-label="מצב" onchange="mchFilter('state',this.value)">${states.map(([v, l]) => opt(v, l, MCH.state)).join("")}</select><span class="sp"></span><span class="n" id="mch-count">${esc(machinesCountText())}</span></div>`;
}
/* הכרטיס עם הטבלה — בדף "מחשבים" (קבוצות) ובאובייקט הקבוצה (שטוח). */
function machinesTableCard(g = null, cls = "c12") {
  const gidEnc = g ? encodeId(g.id) : "";
  const foot = g && isAdmin()
    ? `<div class="dg-bar foot"><span class="n">${g.role === "classroom" ? "הוספה: מספר 01–99 או INS · " : ""}ייבוא בהדבקה (MAC + ${g.role === "classroom" ? "סיומת" : "שם"} בכל שורה)</span><span class="sp"></span><button class="btn sm" onclick="openAddMachine({group:'${gidEnc}'})">+ מחשב</button><button class="btn sm" onclick="openAddMachine({group:'${gidEnc}',tab:1})">ייבוא בהדבקה</button><a class="btn sm" href="/api/console/machines.csv" download>ייצוא CSV</a></div>`
    : "";
  const strip = !g && MACHINES_FILTER
    ? `<div class="c12" role="status">${UI.note("info", `מוצגת קבוצה: <b>${esc(groupLabel(MACHINES_FILTER))}</b> · ${UI.link("הצג את כל המחשבים", "clearMachinesFilter()")}`)}</div>` : "";
  const title = g ? (g.role === "classroom" ? "המחשבים בכיתה" : "המחשבים בקבוצה") : MACHINES_FILTER ? groupLabel(MACHINES_FILTER) : "כל המחשבים";
  const small = g ? 'אותה טבלה כמו ב"מחשבים", מסוננת' : (classroomsOn() ? "בנייה ושיכפול = קבוצה קבועה · כל כיתה = קבוצה משלה" : "בנייה ושיכפול = קבוצה קבועה");
  return strip + UI.card({ title, small, cls, flush: true, body: machinesBar(!!g || !!MACHINES_FILTER) + `<div id="mch-table">${machinesTableHtml()}</div>` + foot });
}
function machinesTabs() {
  const unreg = NET ? NET.filter((d) => !d.registered).length : null;
  const red = DISK_FAILURES ? `דיסקים אדומים (${DISK_FAILURES.length})` : "דיסקים אדומים";
  if (MACHINES_ROLE) return [ROLE_PAGE_HE[MACHINES_ROLE], red];   // בלי "נראו ברשת"
  return ["כל המחשבים", unreg == null ? "נראו ברשת" : `נראו ברשת (${unreg})`, red];
}
function machines(tab = 0) {
  if (!classroomsOn()) {
    if (MACHINES_ROLE === "classroom") MACHINES_ROLE = null;
    if (MACHINES_CLASS) {
      const cg = (GROUPS || []).find((x) => x.id === MACHINES_CLASS);
      if (cg && cg.role === "classroom") MACHINES_CLASS = null;
    }
  }
  const crumbs = [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "מלאי" }, { label: "מחשבים" }];
  if (!MACHINES || !GROUPS) {
    return `<div class="page">${UI.objHeader({ crumbs, icon: "machine", name: "מחשבים", tabs: machinesTabs(), tab })}<div class="body"><div class="c12">${pagePlaceholder()}</div></div></div>`;
  }
  if (MACHINES_CLASS) {
    const g = GROUPS.find((x) => x.id === MACHINES_CLASS);
    if (g) return groupPage(g, tab);
    MACHINES_CLASS = null;
  }
  const all = machinesScope(), count = (role) => all.filter((m) => machineRole(m) === role).length;
  const classN = classroomsOn() ? GROUPS.filter((g) => g.role === "classroom").length : 0;
  const sub = MACHINES_ROLE
    ? [`${all.length} רשומים`, MACHINES_ROLE === "classroom" ? `${classN} כיתות` : ""].filter(Boolean)
    : [`${all.length} רשומים`, classroomsOn() ? `${classN} כיתות` : "", `${count("build")} מחשבי בנייה`, `${count("cloner")} משכפלים`].filter(Boolean);
  if (NET) {
    const macs = new Set(all.map((m) => m.mac));
    sub.push(`${NET.filter((d) => d.registered && isToday(d.last_seen) && (!MACHINES_ROLE || macs.has(d.mac))).length} נראו ברשת היום`);
  }
  const red = (DISK_FAILURES || []).length, unreg = NET ? NET.filter((d) => !d.registered).length : 0;
  const pill = (red ? UI.pill("err", `${red} ${red === 1 ? "דיסק אדום" : "דיסקים אדומים"}`) : "")
    + (unreg ? UI.pill("warn", `${unreg} ${unreg === 1 ? "לא רשום" : "לא רשומים"}`) : "");
  const actions = (isAdmin()
    ? `<button class="btn primary" onclick="openAddMachine({})">+ מחשב</button>${classroomsOn() ? `<button class="btn" onclick="addGroupSheet()">+ כיתה</button>` : ""}<button class="btn" onclick="openAddMachine({tab:1})">ייבוא בהדבקה</button><a class="btn" href="/api/console/machines.csv" download>ייצוא CSV</a>`
    : "") + `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>`;
  const name = MACHINES_ROLE ? ROLE_PAGE_HE[MACHINES_ROLE] : "מחשבים";
  const header = UI.objHeader({ crumbs, icon: "machine", name, sub: esc(sub.join(" · ")), pill, actions, tabs: machinesTabs(), tab });
  const redTab = MACHINES_ROLE ? 1 : 2;
  const body = (!MACHINES_ROLE && tab === 1) ? seenDevicesCard() : tab === redTab ? diskFailuresCard() + shrinkRecordsCard() : machinesTableCard();
  return `<div class="page">${header}<div class="body">${body}</div></div>`;
}
function clearMachinesFilter() {
  MACHINES_FILTER = null;
  MACHINES_CLASS = null;
  MACHINES_ROLE = null;
  if (current === "machines") renderCurrent();
}
function openMachinesPage() { MACHINES_FILTER = null; MACHINES_CLASS = null; MACHINES_ROLE = null; selectPageById("machines"); }
/* צומת-אב בעץ: אותו דף מחשבים, ממוקד לתפקיד אחד (נדב 17/09: "חלון כמו של המחשבים
   אבל רק של כיתות / שיכפול / בנייה, בלי לשונית נראו ברשת"). */
function selectMachinesRole(role) {
  if (role === "classroom" && !classroomsOn()) return;
  MACHINES_ROLE = role; MACHINES_FILTER = null; MACHINES_CLASS = null; MCH.sel.clear();
  selectPageById("machines");
}
/* לחיצה על קבוצה בעץ: כל קבוצה → האובייקט שלה (כיתה — גל 3; בנייה/שיכפול — גל 3א). קבוצה
   שאינה רשומה ב-/groups → הטבלה מסוננת (#916). */
function selectMachinesGroup(groupId) {
  try { groupId = decodeURIComponent(groupId); } catch (e) {}
  const g = (GROUPS || []).find((x) => x.id === groupId);
  MACHINES_ROLE = null;
  if (g) { MACHINES_CLASS = groupId; MACHINES_FILTER = null; }
  else { MACHINES_FILTER = groupId; MACHINES_CLASS = null; }
  MCH.sel.clear();
  selectPageById("machines");
}
function openClass(gidEnc) {
  let gid = gidEnc; try { gid = decodeURIComponent(gidEnc); } catch (e) {}
  MACHINES_CLASS = gid; MACHINES_FILTER = null; MACHINES_ROLE = null; MCH.sel.clear();
  selectPageById("machines");
}

/* ---------- הקבוצה כאובייקט (class.md, cloners.md, builders.md) — groupPage מקבל קבוצה, והתוכן לפי role ---------- */
function groupTabs(g) {
  if (g.role === "classroom") return ["סיכום", "מחשבים", "סבבים"];
  if (g.role === "cloner") return ["סיכום", "מגירות", "סבבים"];
  if (g.role === "build") return ["סיכום", "קליטות"];
  return ["סיכום", "מחשבים"];
}
function groupKpis(g, kids) {
  const s = groupSession(g.id);
  const seen = NET ? kids.filter((m) => { const n = netFor(m.mac); return n && n.last_seen && isToday(n.last_seen); }).length : null;
  const red = kids.reduce((n, m) => n + machineDiskFailures(m).length, 0);
  const reported = kids.filter((m) => Array.isArray(m.disks)).length;
  const waiting = kids.filter((m) => m.prompt), failed = s ? (s.members || []).filter((x) => x.state === "failed") : [];
  const care = waiting.length + red + failed.length;
  const careSub = [waiting.length ? `${waiting.length} ממתינים למפעיל` : "", red ? `${red} דיסקים אדומים` : "", failed.length ? `${failed.length} נכשלו בסבב` : ""].filter(Boolean).join(" · ");
  const disks = DISK_FAILURES == null ? UI.kpi({ cls: "", label: "דיסקים", value: "לא נקרא", sub: "רשימת הכשלים לא נטענה" })
    : red ? UI.kpi({ cls: "err", label: "דיסקים", value: red, unit: "אדומים", sub: esc(kids.filter((m) => machineDiskFailures(m).length).map(machineName).join(", ")) })
    : UI.kpi({ cls: reported ? "ok" : "", label: "דיסקים", value: reported ? "אין אדומים" : "לא דווחו", sub: `${reported} מתוך ${kids.length} דיווחו על דיסקים` });
  return UI.kpi({ cls: "", label: "מחשבים", value: kids.length, sub: seen == null ? "הרשת לא נקראה" : `${seen} נראו ברשת היום` })
    + (s ? UI.kpi({ cls: "info", label: "סבב פעיל", value: s.joined || 0, unit: `/ ${sessionExpected(s)}`, sub: `${esc(sessionImage(s))} · ${esc(stateLabel(s.state))} · ${UI.link("לסבב", "selectPageById('deploy')")}` })
         : UI.kpi({ cls: "", label: "סבב פעיל", value: "אין", sub: g.role === "classroom" ? UI.link("הפץ לכיתה…", `deployToGroup('${encodeId(g.id)}')`) : "" }))
    + disks
    + UI.kpi({ cls: care ? "warn" : "", label: "דורש טיפול", value: care, sub: careSub || "אין ממתינים, אדומים או כשלים" });
}
/* ציר הסבבים: הסבב הפעיל (כיתה — /overview.session; משכפלים — /room.round) + "דורש API" להיסטוריה. */
function groupRoundsCard(g, cls) {
  const s = groupSession(g.id), r = g.role === "cloner" ? roomRound() : null;
  const events = [];
  if (s) events.push({ t: "עכשיו", cls: "info", text: `${esc(sessionImage(s))} · ${esc(stateLabel(s.state))}, ${s.joined || 0}/${sessionExpected(s)} הצטרפו · ${UI.link("לסבב", "selectPageById('deploy')")}`, who: "" });
  if (r) events.push({ t: "עכשיו", cls: ROOM.stream_stalled ? "warn" : "info", text: `${esc(r.image_name || r.image_id || "")} · גל ${r.wave_number || 1} · ${r.written_drives || 0}/${r.target_drives || 0} נכתבו${ROOM.stream_stalled ? " · הזרם עצר" : ""} · ${UI.link("לסבב", "selectPageById('deploy')")}`, who: r.opened_by || "" });
  const now = events.length ? UI.timeline(events) + `<div style="margin-top:12px"></div>` : "";
  const unread = g.role === "cloner" && ROOM == null ? UI.note("warn", "החדר לא נקרא — הסבב הפעיל לא ידוע.") + `<div style="margin-top:12px"></div>` : "";
  const what = g.role === "classroom" ? "לכיתה" : g.role === "cloner" ? "בחדר" : "לקבוצה";
  const today = g.role === "cloner" ? "‏/room מחזיק סבב אחד; היסטוריה רק " : "היום רק ";
  const note = UI.note("info", `היסטוריית סבבים ${what} — <b title="אין endpoint לסבבים סגורים לפי קבוצה">דורש API</b> (${today}${UI.link("ביומן", "selectPageById('logs')")}).`);
  return UI.card({ title: g.role === "classroom" ? "סבבים של הכיתה" : g.role === "cloner" ? "סבבים בחדר" : "סבבים", cls, body: unread + now + note });
}
/* הכיתה (class.md): KPI, הטבלה שטוחה, סבבים. */
function classView(g, kids, tab) {
  const gidEnc = encodeId(g.id), s = groupSession(g.id), classroom = g.role === "classroom";
  const seen = NET ? kids.filter((m) => { const n = netFor(m.mac); return n && n.last_seen && isToday(n.last_seen); }).length : null;
  const sub = [classroom ? "קבוצת כיתה" : "קבוצה קבועה", s && s.prefix ? `קידומת ${s.prefix}` : "", `${kids.length} ${kids.length === 1 ? "מחשב" : "מחשבים"}`, seen == null ? "" : `${seen} נראו היום`].filter(Boolean).join(" · ");
  const pill = s ? UI.pill("info", `סבב ${stateLabel(s.state)}`) : "";
  const actions = isAdmin() ? (classroom ? `<button class="btn primary" onclick="deployToGroup('${gidEnc}')">הפץ לכיתה…</button>` : "")
    + `<button class="btn" onclick="openAddMachine({group:'${gidEnc}'})">+ מחשב${classroom ? " לכיתה" : ""}</button>`
    + (classroom ? `<button class="btn" onclick="renameGroup('${gidEnc}')">שינוי שם</button><button class="btn danger" onclick="deleteGroup('${gidEnc}')">מחיקה (הקלדת שם)</button>` : "") : "";
  let body;
  if (tab === 1) body = machinesTableCard(g);
  else if (tab === 2) body = groupRoundsCard(g, "c12");
  else body = `<div class="c12 kpis">${groupKpis(g, kids)}</div>` + machinesTableCard(g, "c8") + groupRoundsCard(g, "c4");
  return { sub, pill, actions, body };
}
function groupPage(g, tab = 0) {
  const kids = (MACHINES || []).filter((m) => machineGroupId(m) === g.id);
  const view = g.role === "cloner" ? clonersView(g, kids, tab) : g.role === "build" ? buildersView(g, kids, tab) : classView(g, kids, tab);
  const header = UI.objHeader({
    crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "מלאי" }, { label: "מחשבים", onclick: "openMachinesPage()" }, { label: ROLE_GROUP_HE[g.role] || "קבוצות" }, { label: g.label }],
    icon: g.role === "classroom" ? "group" : g.role === "build" ? "build" : "copy", name: g.label, sub: esc(view.sub), pill: view.pill,
    actions: view.actions + `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>`, tabs: groupTabs(g), tab });
  return `<div class="page">${header}<div class="body">${view.body}</div></div>`;
}
function deployToGroup(gidEnc) {
  let gid = gidEnc; try { gid = decodeURIComponent(gidEnc); } catch (e) {}
  selectPageById("deploy");
  toast(`סבב הפצה ל"${groupLabel(gid)}" נפתח ממסך התחנה או מדף ההפצה`);
}
async function wakeRoom() {
  try {
    const r = await post("/room/wake");
    toast(`WoL נשלח ל-${r.sent} מחשבים${r.failed ? `, ${r.failed} נכשלו` : ""}${(r.reasons || []).length ? " — " + r.reasons.join("; ") : ""}`, 6000);
  } catch (e) { toast("WoL נכשל: " + e.message); }
}

/* ---------- #954 גל 3א: מחשבי שיכפול (חדר השיכפולים) ומחשבי בנייה — הקבוצות הקבועות כאובייקטים ----------
   נבנה לפי docs/design/console-redesign/cloners.md ו-builders.md (מוקאפים מאושרים 17/09),
   על groupPage של גל 3. מקורות: /groups, /machines (drawer_count, disks, inventory, prompt),
   /room (round + machines[].drawer_list — מצב כל מגירה בזמן סבב), /disk-failures
   (#874/#890), /tasks (קליטות), /monitor/machines ("מחובר"), /net (IP, נראה).
   חריץ = "דיסק N · SATA N-1" (מוסכמת נדב: SATA 0 → דיסק 1; לעולם לא sd*). צבע:
   ירוק רק על SMART `ok` או "נכתב"; "לא נבדק" אפור, לא ירוק (עיקרון 5). מה שאין לו
   API — כיבוי כולם, היסטוריית סבבים, שלבי הקליטה, WoL למחשב יחיד, תיקיית הקליטה —
   "דורש API" בטקסט (Issue מרוכז), לא כפתור מנוטרל ולא נתון מומצא. */
let ROOM = null;                          // ‏GET /room — null = לא נקרא (≠ חדר בלי סבב)
let ROOM_KEY = "";                        // חתימת התשובה האחרונה — רינדור מחדש רק על שינוי
let CAPTURE_TASKS_READ = false;           // ‏/tasks נקרא לפחות פעם אחת (‏[] ריק ≠ לא נקרא)
const DRAWER_STATE_HE = { writing: "כותב", done: "נכתב", failed: "נכשל", verifying: "מאמת", waiting: "ממתין" };

function roomRound() { return ROOM && ROOM.round ? ROOM.round : null; }
function roomMachine(mac) { return ROOM && Array.isArray(ROOM.machines) ? ROOM.machines.find((x) => x.mac === mac) || null : null; }
function smartText(d) {
  if (d.smart === "ok") return "SMART תקין";
  if (d.smart === "warn") return "SMART אזהרה";
  if (d.smart === "fail") return "SMART תקלה";
  if (d.smart === "failed_last") return "נכשל בשיכפול הקודם";
  return "SMART לא נבדק";
}
/* מכונה · דיסק N · SATA N-1 של רשומת כשל (#874). */
function failureWhere(f) {
  const m = findMachine(f.mac);
  return (m ? machineName(m) : f.mac) + (f.port != null ? ` · דיסק ${f.port} · SATA ${f.port - 1}` : "");
}
function groupFailures(kids) {
  const macs = new Set(kids.map((m) => m.mac));
  const serials = new Set(kids.flatMap((m) => (m.disks || []).map((d) => d.serial).filter(Boolean)));
  return (DISK_FAILURES || []).filter((f) => macs.has(f.mac) || (f.serial && serials.has(f.serial)));
}
/* החריצים של משכפל, לפי "דיסק N". בסבב — drawer_list מ-/room (מצב, בייטים, SMART,
   CRC לכל מגירה); בלי סבב — /machines[].disks[] (dev, size, model, serial, port, smart).
   מספר החריצים: drawer_count שהוגדר; ואם לא — הפורט הגבוה ביותר או מספר הדיסקים
   (ברירת המחדל של השרת, #710). דיסק בפורט גבוה מהמוגדר אינו מוסתר. */
function machineSlots(m) {
  const rm = roomMachine(m.mac);
  const live = !!(roomRound() && rm && Array.isArray(rm.drawer_list));
  const src = live ? rm.drawer_list : (m.disks || []);
  const byN = new Map();
  src.forEach((d, i) => { const n = diskSlot(d, i); if (!byN.has(n)) byN.set(n, d); });
  const ports = [...byN.keys()];
  const declared = m.drawer_count != null ? Number(m.drawer_count) : 0;   // לא ברירת המחדל של השרת (1) — מכונה שלא דיווחה ולא הוגדרה מקבלת הודעה, לא חריץ מומצא
  const total = Math.max(declared, ports.length ? Math.max(...ports) : 0);
  const fails = machineDiskFailures(m);
  const slots = [];
  for (let n = 1; n <= total; n++) {
    const d = byN.get(n) || null;
    const fail = fails.find((f) => failureSlot(m, f) === n || (d && f.serial && f.serial === d.serial)) || null;
    slots.push({ n, disk: d, live: live && d ? d : null, fail });
  }
  return { slots, live, declared: m.drawer_count != null };
}
/* צבע החריץ: אדום (נכשל בעבר / נכשל בסבב / SMART תקלה) > כתום (SMART אזהרה, CRC עלה)
   > כחול (כותב/מאמת) > ירוק (SMART תקין / נכתב) > אפור (לא נבדק) · מקווקו = ריק. */
function slotClass(s) {
  if (!s.disk) return "empty";
  const d = s.disk;
  if (s.fail || d.state === "failed" || d.smart === "fail" || d.smart === "failed_last") return "err";
  if (d.smart === "warn" || (d.crc_delta != null && d.crc_delta > 0)) return "warn";
  if (d.state === "writing" || d.state === "verifying") return "run";
  if (d.smart === "ok" || d.state === "done") return "ok";
  return "";
}
/* #1033: מלבן הדיסק בגריד מציג רק שתי שורות — "דיסק N" והקיבולת. כל
   השאר (דגם, מספר סידורי, SATA, SMART, סיבת כשל) עובר ל-title (tooltip)
   ולמגירה (disksHtml) שלא השתנתה. */
function diskTitle(m, s, d) {
  if (!d) return `דיסק ${s.n} · SATA ${s.n - 1} · ${m.disks == null ? "לא דווח" : "ריק"}`;
  const parts = [`SATA ${s.n - 1}`, d.model || "", d.serial || ""].filter(Boolean);
  if (s.fail) parts.push(`אדום ${fmtDate(s.fail.at)} · ${failureCauseText(s.fail)}`);
  else if (d.state === "failed") parts.push(`נכשל בסבב${d.error ? " — " + d.error : ""}`);
  if (s.live && d.state && d.state !== "failed") parts.push(DRAWER_STATE_HE[d.state] || d.state);
  if (d.crc_delta != null && d.crc_delta > 0) parts.push(`CRC +${d.crc_delta} · לבדוק כבל`);
  if (s.live && d.state === "writing" && d.stalled_s != null && d.stalled_s >= 60) parts.push(`ללא תזוזה ${d.stalled_s} ש'`);
  if (!s.fail) parts.push(smartText(d));
  return parts.join(" · ");
}
function slotHtml(m, s, admin) {
  const cls = slotClass(s), d = s.disk, title = esc(diskTitle(m, s, d));
  if (!d) return `<div class="disk empty" data-slot="${s.n}" title="${title}"><b>דיסק ${s.n}</b><span class="cap">${m.disks == null ? "לא דווח" : "ריק"}</span></div>`;
  const cap = d.size_bytes ? `<span class="cap mono">${ltr(fmtBytes(d.size_bytes))}</span>` : `<span class="cap">—</span>`;
  let bar = "";
  if (s.live && d.state) {
    const pct = d.bytes_total > 0 ? 100 * (d.bytes_written || 0) / d.bytes_total : d.state === "done" ? 100 : null;
    bar = UI.barRow(pct, d.state === "failed" ? "err" : d.state === "done" ? "ok" : cls === "warn" ? "warn" : "");
  }
  const clear = s.fail && admin ? `<button class="btn sm" onclick="clearDiskFailure(${Number(s.fail.id)})">נקה</button>` : "";
  return `<div class="disk ${cls}" data-slot="${s.n}" title="${title}"><b>דיסק ${s.n}</b>${cap}${bar}${clear}</div>`;
}
function clonerState(m) {
  const rm = roomMachine(m.mac);
  if (roomRound() && rm && rm.joined) {
    if (rm.state === "failed") return { cls: "err", text: "נכשל בסבב" + (rm.error ? " — " + rm.error : "") };
    return { cls: "run", text: "בסבב · " + (rm.state === "waiting" ? "ממתין להתחלה" : Progress.view(rm).label) };
  }
  return machineState(m);
}
/* ‏`room` (גל 4): אותו כרטיס בדף ההפצה — המצב מ-`awake` של /room, WoL כשהמכונה לא מחוברת. */
function clonerCardHtml(m, admin, room = false) {
  const macEnc = encodeId(m.mac), net = netFor(m.mac), st = room ? roomCardState(m) : clonerState(m), { slots, declared } = machineSlots(m);
  const meta = [esc(st.text), `${slots.length} ${slots.length === 1 ? "חריץ" : "חריצים"}${declared || !slots.length ? "" : " (לפי הדיווח)"}`,
    net && net.ip ? `<span class="mono">${esc(net.ip)}</span>` : "", esc(NET ? seenAgo(net && net.last_seen) : "לא נקרא")].filter(Boolean).join(" · ");
  const body = slots.length ? `<div class="slots">${slots.map((s) => slotHtml(m, s, admin)).join("")}</div>`
    : UI.note("", `מספר החריצים לא הוגדר והמכונה ${m.disks == null ? "מעולם לא דיווחה על דיסקים" : "דיווחה: " + zeroDisksText(m)}.${admin ? ` ${UI.link("הגדר חריצים", `editDrawerCount('${macEnc}')`)}` : ""}`);
  const wol = `<button class="btn sm" onclick="wakeMachine('${macEnc}')" title="WoL למכונה הזו בלבד (#984); לכל החדר — בכותרת">WoL</button>`;
  const acts = room
    ? (st.off && roomOperator() ? wol : "")
    : admin ? `<button class="btn sm" onclick="openMachineDetail('${macEnc}')">פרטים</button>${wol}` : "";
  return `<div class="mcard${st.cls ? "" : " off"}" data-mac="${esc(m.mac)}"><div class="mcard-h"><span class="st ${st.cls}"><b>${esc(machineName(m) || m.mac)}</b></span><span class="muted">${meta}</span></div>${body}${acts ? `<div class="acts">${acts}</div>` : ""}</div>`;
}
function clonerLegend() {
  return `<div class="legend"><span><i class="sw-ok"></i>SMART תקין / נכתב</span><span><i class="sw-run"></i>כותב</span><span><i class="sw-warn"></i>אזהרה — כותב</span><span><i class="sw-err"></i>אדום — נכשל, מדלג</span><span><i class="sw-empty"></i>חריץ ריק</span></div>`;
}
function clonerGridCard(g, kids, big) {
  const admin = isAdmin(), r = roomRound();
  const small = ROOM == null ? "החדר לא נקרא — הדיסקים לפי הדיווח האחרון ב-hello" : r ? "בסבב — מצב כל מגירה מ-/room" : "אין סבב — הדיסקים לפי הדיווח האחרון ב-hello";
  const body = kids.length ? `<div class="mgrid${big ? " big" : ""}">${sortMachinesBySuffix(kids).map((m) => clonerCardHtml(m, admin)).join("")}</div>`
    : UI.empty("אין מחשבי שיכפול רשומים — הוסיפו את הראשון (MAC + שם).", admin ? `<button class="btn primary" onclick="openAddMachine({group:'${encodeId(g.id)}'})">+ מחשב שיכפול</button>` : "");
  return UI.card({ title: "המחשבים והחריצים", small, acts: clonerLegend(), cls: "c12", body });
}
function clonerStats(kids) {
  const all = kids.map((m) => machineSlots(m).slots);
  const count = (fn) => all.reduce((n, slots) => n + slots.filter(fn).length, 0);
  return {
    slots: count(() => true), filled: count((s) => s.disk), redInSlot: count((s) => s.fail),
    writing: count((s) => s.live && (s.live.state === "writing" || s.live.state === "verifying")),
    failedNow: count((s) => s.live && s.live.state === "failed"),
    on: MONITOR_ROWS ? kids.filter((m) => (monitorRow(m.mac) || {}).online).length : null,
  };
}
function clonerKpis(kids, st, fails) {
  const r = roomRound(), admin = isAdmin();
  const off = MONITOR_ROWS ? kids.filter((m) => !(monitorRow(m.mac) || {}).online).map(machineName) : [];
  const k1 = st.on == null ? UI.kpi({ label: "מחוברים", value: "לא נקרא", sub: "‏/monitor/machines לא נטען" })
    : UI.kpi({ cls: st.on ? "ok" : "", label: "מחוברים", value: st.on, unit: `/ ${kids.length}`, sub: !kids.length ? "אין מחשבי שיכפול" : off.length ? `${esc(off.join(", "))} — ${off.length === 1 ? "לא מחובר" : "לא מחוברים"}` : "כל המשכפלים מחוברים" });
  const k2 = ROOM == null ? UI.kpi({ label: "סבב פעיל", value: "לא נקרא", sub: "‏/room לא נטען" })
    : r ? UI.kpi({ cls: ROOM.stream_stalled ? "warn" : "info", label: "סבב פעיל", value: `גל ${r.wave_number || 1}`, sub: `${esc(r.image_name || r.image_id || "")} · ${r.written_drives || 0} נכתבו · ${r.remaining_drives != null ? r.remaining_drives : "?"} נשארו${ROOM.stream_stalled ? " · הזרם עצר" : ""} · ${UI.link("לסבב", "selectPageById('deploy')")}` })
    : UI.kpi({ label: "סבב פעיל", value: "אין", sub: admin ? UI.link("פתח סבב במשכפלים…", "openRoomRound()") : "" });
  const k3 = UI.kpi({ cls: st.redInSlot ? "warn" : st.filled ? "ok" : "", label: "דיסקים בחריצים", value: st.filled, unit: `/ ${st.slots}`,
    sub: st.slots ? `${st.writing} כותבים · ${st.redInSlot} אדומים · ${st.slots - st.filled} ריקים` : "לא הוגדרו חריצים ולא דווחו דיסקים" });
  const k4 = DISK_FAILURES == null ? UI.kpi({ label: "דיסקים אדומים", value: "לא נקרא", sub: "רשימת הכשלים לא נטענה" })
    : fails.length ? UI.kpi({ cls: "err", label: "דיסקים אדומים", value: fails.length, sub: esc(fails.map(failureWhere).join(" · ")) })
    : UI.kpi({ cls: st.filled ? "ok" : "", label: "דיסקים אדומים", value: st.filled ? "אין" : "לא דווחו", sub: st.filled ? "אף דיסק בחדר לא נכשל בכתיבה" : "אף משכפל לא דיווח על דיסקים" });
  const waiting = kids.filter((m) => m.prompt).length, stalled = ROOM && ROOM.stream_stalled ? 1 : 0;
  const care = waiting + fails.length + st.failedNow + stalled;
  const careSub = [waiting ? `${waiting} ממתינים למפעיל` : "", fails.length ? `${fails.length} אדומים` : "", st.failedNow ? `${st.failedNow} נכשלו בסבב` : "", stalled ? "הזרם עצר" : ""].filter(Boolean).join(" · ");
  return k1 + k2 + k3 + k4 + UI.kpi({ cls: care ? "warn" : "", label: "דורש טיפול", value: care, sub: careSub || "אין ממתינים, אדומים או כשלים" });
}
function clonersView(g, kids, tab) {
  const gidEnc = encodeId(g.id), admin = isAdmin(), r = roomRound(), st = clonerStats(kids), fails = groupFailures(kids);
  const sub = ["קבוצה קבועה", `${kids.length} ${kids.length === 1 ? "מחשב" : "מחשבים"}`, `${st.slots} חריצים`, st.on == null ? "מחוברים: לא נקרא" : `${st.on} מחוברים`,
    `${st.filled} דיסקים בחריצים`, DISK_FAILURES ? `${fails.length} אדומים` : "אדומים: לא נקרא"].join(" · ");
  const pill = r ? UI.pill(ROOM.stream_stalled ? "warn" : "info", `סבב פעיל — גל ${r.wave_number || 1}, ${r.written_drives || 0}/${r.target_drives || 0}${ROOM.stream_stalled ? " · הזרם עצר" : ""}`)
    : ROOM == null ? UI.pill("", "החדר לא נקרא") : "";
  const actions = admin ? `<button class="btn primary" onclick="openRoomRound()">פתח סבב במשכפלים…</button><button class="btn" onclick="wakeRoom()">הער את כולם (WoL)</button><button class="btn" onclick="openAddMachine({group:'${gidEnc}'})">+ מחשב שיכפול</button>${UI.soon("כיבוי כולם")}` : "";
  const body = tab === 1 ? clonerGridCard(g, kids, true)
    : tab === 2 ? groupRoundsCard(g, "c12")
    : `<div class="c12 kpis">${clonerKpis(kids, st, fails)}</div>` + clonerGridCard(g, kids, false) + diskFailuresCard(kids, "c8") + groupRoundsCard(g, "c4");
  return { sub, pill, actions, body };
}
/* הכתיבה ל-/room נשארת בדף ההפצה (גל 6) ובמסך החדר — כאן רק ניווט. */
function openRoomRound() {
  selectPageById("deploy");
  toast("סבב במשכפלים נפתח ממסך החדר או מדף ההפצה");
}
/* רענון חי: /room כל 2 שניות כשאובייקט המשכפלים פתוח (כמו /overview בדף ההפצה); רינדור רק על שינוי. */
async function refreshGroupLive() {
  const g = (GROUPS || []).find((x) => x.id === MACHINES_CLASS), room = current === "deploy";
  if (!room && (!g || g.role !== "cloner" || current !== "machines")) return;
  // ‏גל 4: בחדר גם /machines — השאלה למפעיל (prompt, #906) משתנה בין דגימות.
  const [view, machines] = await Promise.all([api("/room"), room ? api("/machines").catch(() => null) : null]);
  ROOM = view && Array.isArray(view.machines) ? view : null;
  if (Array.isArray(machines)) MACHINES = machines;
  const key = room ? JSON.stringify([ROOM, (MACHINES || []).map((m) => [m.mac, m.prompt])]) : JSON.stringify(ROOM);
  if (key === ROOM_KEY) return;
  ROOM_KEY = key;
  renderCurrent();
}
/* קליטות שהשתנו (‏/tasks נדגם כל 2 שניות) — מרנדר את אובייקט הבנייה הפתוח. */
function groupTasksChanged(all) {
  const key = JSON.stringify(all.map((t) => [t.id, t.state, t.bytes_written, t.error, t.updated_at]));
  if (key === MCH.tasksKey) return false;
  MCH.tasksKey = key;
  return current === "machines" && !!MACHINES_CLASS;
}

/* ---------- מחשבי בנייה (builders.md): קליטה בתהליך, כרטיסי מחשבים, קליטות אחרונות ---------- */
function groupTasks(kids) { const macs = new Set(kids.map((m) => m.mac)); return (CAPTURE_TASKS || []).filter((t) => macs.has(t.mac)); }
function activeTasks(kids) { return groupTasks(kids).filter((t) => t.state === "pending" || t.state === "running"); }
/* "דיסק N" לפי הדיווח האחרון של המכונה; כשהמכונה לא דיווחה — שם ההתקן שנבחר בקליטה, כמו שהוא. */
function taskDisk(t) {
  const m = findMachine(t.mac);
  const i = m && Array.isArray(m.disks) ? m.disks.findIndex((d) => d.dev === t.disk) : -1;
  return i >= 0 ? `דיסק ${diskSlot(m.disks[i], i)}` : t.disk ? `<span class="mono">${esc(t.disk)}</span>` : "—";
}
function taskWho(t) { const m = findMachine(t.mac); return m ? machineName(m) || m.mac : t.machine || t.mac || ""; }
function fmtDuration(from, to) {
  const s = (new Date(to).getTime() - new Date(from).getTime()) / 1000;
  if (!from || !to || !Number.isFinite(s) || s < 0) return "—";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return (h ? `${h}:${String(m).padStart(2, "0")}` : String(m)) + ":" + String(sec).padStart(2, "0");
}
function taskWhen(t) { return isToday(t.created_at) ? `היום ${fmtClock(t.created_at)}` : `${fmtDate(t.created_at)} ${fmtClock(t.created_at)}`; }
function taskStatus(t) {
  if (t.state === "pending") return UI.status("run", "ממתין ל-PXE");
  if (t.state === "running") return UI.status("run", "נקלט · " + Progress.view(t).label);
  if (t.state === "done") return t.error ? UI.status("warn", "הושלם — אזהרה") : UI.status("ok", "הושלם");
  if (t.state === "failed") return UI.status("err", "נכשל");
  if (t.state === "cancelled") return UI.status("", "בוטל");
  return UI.status("warn", t.state || "—");
}
function taskWarning(t) {
  if (t.state === "done" && t.error) return UI.status("warn", t.error);
  if (t.state === "failed") return t.error ? esc(t.error) : `<span class="muted">בלי הודעה</span>`;
  return `<span class="muted">—</span>`;
}
function cancelCaptureVerified(id) {
  let tid = id; try { tid = decodeURIComponent(id); } catch (e) {}
  const t = (CAPTURE_TASKS || []).find((x) => x.id === tid);
  if (!t || !isAdmin()) return;
  sheet({ title: "ביטול הקליטה", sub: `"${t.name}" תבוטל והקבצים שהתקבלו יימחקו; מחשב הבנייה שיעלה ב-PXE לא יקבל אותה.`, danger: true, submitLabel: "בטל את הקליטה",
    verify: { label: "הקלד את שם האימג'", mustEqual: t.name || "" },
    onSubmit: async () => { await post(`/tasks/${encodeId(t.id)}/cancel`); await loadCaptures(); if (current === "machines") renderCurrent(); } });
}
function captureNowCard(kids) {
  const admin = isAdmin(), active = activeTasks(kids);
  if (!CAPTURE_TASKS_READ) return UI.card({ title: "קליטה בתהליך", cls: "c12", body: UI.note("warn", "רשימת הקליטות לא נקראה") });
  if (!active.length) return UI.card({ title: "קליטה בתהליך", cls: "c12", body: UI.empty('אין קליטה בתהליך — "+ קליטת אימג\'…" פותחת אחת; המשימה ממתינה שמחשב הבנייה יעלה ב-PXE.',
    admin ? `<button class="btn primary" onclick="openCapture().catch(e => toast(e.message))">+ קליטת אימג'…</button>` : "") });
  const items = active.map((t) => {
    const who = taskWho(t), waiting = t.state === "pending", v = Progress.view(t);
    const head = waiting ? UI.status("warn", `ממתין ש${who} יעלה ב-PXE`) : UI.status("run", `נקלט מ${who} · ${v.label}`);
    const kv = UI.kv([
      ["מחשב · דיסק", `${esc(who)} · ${taskDisk(t)}`],
      ["התקדמות", waiting ? `<span class="muted">טרם החלה</span>` : Progress.bar(t) + `<span class="cap">${esc(v.label)}</span>`],
      ["נקראו", waiting ? `<span class="muted">—</span>` : ltr(fmtBytes(t.bytes_written))],
      ["משך", waiting ? `<span class="muted">—</span>` : esc(fmtDuration(t.created_at, t.updated_at))],
      ["נוצרה", `${ltr(fmtWhen(t.created_at))} (${esc(ago(t.created_at))})`],
      ["שלבים", `<span class="muted">בדיקת NTFS → כיווץ → זרם + sha256 → החזרת הגודל → אימות — <b title="‏/tasks מחזיר state ו-source_progress בלבד">דורש API</b></span>`],
    ]);
    const acts = admin ? `<div class="acts"><button class="btn danger" onclick="cancelCaptureVerified('${encodeId(t.id)}')">בטל קליטה (הקלדת שם)</button><button class="btn" onclick="wakeMachine('${encodeId(t.mac)}')">WoL ל-${esc(who)}</button></div>` : "";
    return `<div class="cap-now" data-task="${esc(t.id)}"><div class="mcard-h"><b>${esc(t.name || "")}</b>${head}</div>${kv}${acts}</div>`;
  });
  return UI.card({ title: active.length === 1 ? "קליטה בתהליך" : `${active.length} קליטות בתהליך`, cls: "c12", body: items.join("") });
}
function builderCardHtml(m, admin) {
  const macEnc = encodeId(m.mac), net = netFor(m.mac), st = machineState(m);
  let d0 = null, n0 = 0;
  (m.disks || []).forEach((d, i) => { const n = diskSlot(d, i); if (!d0 || n < n0) { d0 = d; n0 = n; } });
  const inv = m.inventory, tpm = inv && inv.tpm, model = machineModel(m);
  const task = activeTasks([m])[0] || null;
  const state = UI.status(st.cls, st.text) + (task ? " " + UI.pill(task.state === "pending" ? "warn" : "info", task.state === "pending" ? "קליטה ממתינה" : "קליטה רצה") : "");
  const kv = UI.kv([
    ["דגם", inv == null ? `<span class="muted">לא דיווח</span>` : esc(model || "—") + (tpm && tpm.present ? ` · TPM ${esc(tpm.version || "")}`.trimEnd() : "")],
    [d0 ? `דיסק ${n0}` : "דיסק", m.disks == null ? `<span class="muted">לא דיווח</span>` : !d0 ? UI.status("warn", zeroDisksText(m))
      : `${ltr(fmtBytes(d0.size_bytes))} · ${esc(d0.model || "—")}${d0.serial ? ` · <span class="mono">${esc(d0.serial)}</span>` : ""}${m.disks.length > 1 ? ` <span class="cap">(+${m.disks.length - 1})</span>` : ""}`],
    ["לפני קליטה", `<span class="muted">NTFS / בשימוש — נבדקים בקליטה (<b title="לא מדווח ב-hello">דורש API</b>)</span>`],
    ["מצב", state],
  ]);
  const acts = admin ? `<div class="acts">${task ? "" : `<button class="btn sm primary" onclick="openCapture('${macEnc}').catch(e => toast(e.message))">קלוט מכאן</button>`}<button class="btn sm" onclick="openMachineDetail('${macEnc}')">פרטים</button><button class="btn sm" onclick="wakeMachine('${macEnc}')">WoL</button></div>` : "";
  const meta = [net && net.ip ? `<span class="mono">${esc(net.ip)}</span>` : "", esc(NET ? seenAgo(net && net.last_seen) : "לא נקרא")].filter(Boolean).join(" · ");
  return `<div class="mcard${st.cls ? "" : " off"}" data-mac="${esc(m.mac)}"><div class="mcard-h"><span class="st ${st.cls}"><b>${esc(machineName(m) || m.mac)}</b></span><span class="muted">${meta}</span></div>${kv}${acts}</div>`;
}
function buildersCard(g, kids) {
  const admin = isAdmin();
  const body = kids.length ? `<div class="mgrid">${sortMachinesBySuffix(kids).map((m) => builderCardHtml(m, admin)).join("")}</div>`
    : UI.empty("אין מחשב בנייה רשום — הוסיפו אותו (MAC + שם) כדי לקלוט ממנו אימג'.", admin ? `<button class="btn primary" onclick="openAddMachine({group:'${encodeId(g.id)}'})">+ מחשב בנייה</button>` : "");
  return UI.card({ title: "המחשבים", small: '"מחובר" מ-/monitor/machines · מה על המסך מ-hello', cls: "c12", body });
}
function capturesTableCard(kids, cls) {
  const rows = groupTasks(kids).map((t) => ({ attrs: `data-task="${esc(t.id)}"`, cells: [
    UI.name(t.name || "", t.image_id || ""), `${esc(taskWho(t))} · ${taskDisk(t)}`, taskStatus(t),
    t.state === "pending" ? `<span class="muted">—</span>` : ltr(fmtBytes(t.bytes_written)),
    t.state === "pending" ? `<span class="muted">—</span>` : esc(fmtDuration(t.created_at, t.updated_at)),
    taskWarning(t), esc(taskWhen(t))] }));
  const body = !CAPTURE_TASKS_READ ? UI.note("warn", "רשימת הקליטות לא נקראה")
    : UI.datagrid({ columns: ["אימג'", "מחשב · דיסק", "מצב", "נקראו", "משך", "אזהרה / שגיאה", "מתי"], rows, empty: "אין קליטות עדיין — \"+ קליטת אימג'…\" פותחת את הראשונה" });
  return UI.card({ title: "קליטות אחרונות", small: "עד 20 האחרונות מ-/tasks · תיקייה — דורש API (#968)", cls, flush: true, body });
}
function buildersView(g, kids, tab) {
  const gidEnc = encodeId(g.id), admin = isAdmin(), active = activeTasks(kids);
  const on = MONITOR_ROWS ? kids.filter((m) => (monitorRow(m.mac) || {}).online).length : null;
  const last = groupTasks(kids).filter((t) => t.state === "done").sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)))[0] || null;
  const pending = active.filter((t) => t.state === "pending").length, running = active.length - pending;
  const sub = ["קבוצה קבועה", `${kids.length} ${kids.length === 1 ? "מחשב" : "מחשבים"}`, on == null ? "מחוברים: לא נקרא" : `${on} מחוברים`,
    !CAPTURE_TASKS_READ ? "קליטות: לא נקרא" : active.length ? [pending ? `${pending === 1 ? "קליטה אחת ממתינה" : `${pending} קליטות ממתינות`}` : "", running ? `${running === 1 ? "קליטה אחת רצה" : `${running} קליטות רצות`}` : ""].filter(Boolean).join(" · ") : "אין קליטה בתהליך",
    last ? `אימג' אחרון שנקלט: ${last.name} (${fmtDate(last.updated_at)})` : ""].filter(Boolean).join(" · ");
  const pill = !active.length ? "" : active.length > 1 ? UI.pill("info", `${active.length} קליטות בתהליך`) : UI.pill(pending ? "warn" : "info", pending ? "קליטה ממתינה" : "קליטה רצה");
  const actions = admin ? `<button class="btn primary" onclick="openCapture().catch(e => toast(e.message))">+ קליטת אימג'…</button><button class="btn" onclick="openDirectRound()">הפצה ישירה למשכפלים…</button><button class="btn" onclick="openAddMachine({group:'${gidEnc}'})">+ מחשב בנייה</button><button class="btn" onclick="wakeGroup('${gidEnc}')">הער את כולם (WoL)</button>` : "";
  const body = tab === 1 ? capturesTableCard(kids, "c12") : captureNowCard(kids) + buildersCard(g, kids) + capturesTableCard(kids, "c12");
  return { sub, pill, actions, body };
}
/* ‏#715 קיים ב-API (‏POST /room עם source.kind=build_disk) — הפתיחה נשארת בדף ההפצה ובמסך מחשב הבנייה. */
function openDirectRound() {
  selectPageById("deploy");
  toast("הפצה ישירה מדיסק הבנייה נפתחת ממסך מחשב הבנייה או מדף ההפצה (#715)");
}

/* ---------- לשונית "נראו ברשת" — /net כ-datagrid; "רשום" הוא הפעולה הראשית לשורה לא רשומה ---------- */
function bootCell(b) {
  if (!b) return `<span class="muted">—</span>`;
  const since = b.seconds == null ? "זמן לא נקרא" : sinceSeconds(b.seconds);
  return UI.status(b.stalled ? "warn" : "ok", `${b.label} (${b.index}/${b.total}) · ${since}`)
    + (b.stalled ? `<span class="sub">נעצר לפני: ${esc(b.next_label)}</span>` : "");
}
function seenDevicesCard() {
  const acts = isAdmin() ? `<button class="btn sm" onclick="netDeviceAdd()">+ הוספה ידנית</button>` : "";
  if (!NET) return UI.card({ title: "נראו ברשת", acts, body: UI.note("warn", `רשימת ההתקנים לא נקראה${NET_ERR ? ": " + esc(NET_ERR) : ""}`) });
  const rows = NET.filter((d) => classroomsOn() || d.role !== "classroom").map((d) => {
    const macEnc = encodeId(d.mac);
    const who = d.registered ? UI.name(d.name || "", d.group_label || "") : UI.pill("warn", "לא רשום");
    const primary = d.registered
      ? `<button class="btn sm" onclick="openMachineDetail('${macEnc}')">פרטים</button>`
      : `<button class="btn sm primary" onclick="openAddMachine({mac:'${macEnc}'})">רשום</button>`;
    const more = isAdmin() ? `<button class="btn sm" onclick="netDeviceDescribe('${macEnc}')">תיאור</button><button class="btn sm danger" onclick="netDeviceForget('${macEnc}')">הסר</button>` : "";
    return { attrs: `data-mac="${esc(d.mac)}"`, cells: [who, `<span class="mono">${esc(d.mac)}</span>`, d.ip ? `<span class="mono">${esc(d.ip)}</span>` : `<span class="muted">—</span>`,
      d.description ? esc(d.description) : `<span class="muted">—</span>`, esc(ago(d.last_seen)), bootCell(d.boot), `<div class="acts">${primary}${more}</div>`] };
  });
  return UI.card({ title: "נראו ברשת", small: "כל מכונה שדיברה עם השרת; לא רשומה = כרטיס שהוחלף או מחשב חדש", acts, flush: true,
    body: UI.datagrid({ columns: ["שם", "MAC", "IP", "תיאור", "נראה לאחרונה", "שלב האתחול", ""], rows, empty: "עוד לא נראו התקנים. מכונה שתעלה ב-PXE תופיע כאן." }) });
}

/* ---------- לשונית "דיסקים אדומים" — #874 (אדום) ו-#926 (כתום, כווץ) ---------- */
/* #874: דיסקים אדומים — זיכרון כשלי הכתיבה בשרת, במקום הסימון על הדיסק
   (#845). הסיווג הוא של השרת (ata_cause.py); "נקה" = הדיסק הוחלף / הכבל
   תוקן, והרשומה מפסיקה לצבוע. אדום לפי סידורי **וגם** לפי מכונה+חריץ. */
const FAILURE_CAUSE_HE = { cable: "כבל/חריץ", disk: "הדיסק", unclassified: "לא סווג" };
/* ‏`only` (גל 3א): רק הכשלים של המכונות האלה (לפי MAC או סידורי) — לאובייקט המשכפלים. */
function diskFailuresCard(only = null, cls = "c12") {
  const rows = (only ? groupFailures(only) : DISK_FAILURES || []).map((f) => {
    const log = f.ata_log || [];
    const evidence = log.length ? `<details><summary>${log.length} שורות קרנל</summary><pre class="ata-log">${esc(log.join("\n"))}</pre></details>` : `<span class="muted">—</span>`;
    return [`<span class="mono">${esc(f.serial || "—")}</span>`, esc(failureWhere(f)), esc(fmtWhen(f.at)), UI.status("err", failureCauseText(f)), esc(f.error || ""), evidence,
      isAdmin() ? UI.acts([["נקה", `clearDiskFailure(${Number(f.id)})`]]) : ""];
  });
  const body = DISK_FAILURES == null ? UI.note("warn", "רשימת הכשלים לא נטענה")
    : UI.datagrid({ columns: ["מספר סידורי", "מכונה · דיסק · חריץ", "מתי", "סיבה", "שגיאה", "ראיה", ""], rows, empty: "אין דיסקים אדומים" });
  return UI.card({ title: "דיסקים אדומים — נכשלו בכתיבה", small: 'אדום במסך המחשב לפני הסבב; "נקה" אחרי החלפת דיסק או כבל', cls, flush: true, body });
}
async function clearDiskFailure(id) {
  try {
    await post(`/disk-failures/${id}/clear`);
    toast("הרשומה נוקתה");
    await loadMachines();
  } catch (e) {
    toast("הניקוי נכשל: " + e.message);
  }
}
/* #926: דיסקים מכווצים — מחיצת המקור כווצה לקליטה (#87) ולא הוחזרה לגודלה
   (אובדן חשמל / אתחול לפני סוף הקליטה). הרשומה נפתחת בשרת **לפני** הכיווץ
   ונסגרת אחרי ההחזרה; מה שנשאר פתוח הוא דיסק שעדיין מכווץ. כתום, לא אדום:
   ווינדוס עולה ממנו. ההחזרה נעשית ליד המחשב (מחשב הבנייה מציע באתחול);
   "נקה" = המפעיל הרחיב בעצמו / הדיסק הוחלף. */
function shrinkRecordsCard() {
  const rows = (SHRINK_RECORDS || []).map((r) => {
    const m = findMachine(r.mac);
    const where = (m ? machineName(m) : r.mac) + (r.port != null ? ` · דיסק ${r.port} · SATA ${r.port - 1}` : "");
    const gb = (sectors) => sectors != null ? (sectors * 512 / 1e9).toFixed(1) + " GB" : "—";
    // ‏#929: רשומה אחת לדיסק עם `partitions`; רשומה מלפני — המחיצה שבשדות העליונים.
    const text = (r.partitions || [r]).map((p) => `מחיצה ${p.idx} כווצה לקליטה ולא הוחזרה לגודלה המקורי (${gb(p.size_sectors)})`).join("; ");
    // הסיבה מהסוכן (shrink-note): "הטבלה הוחזרה, מערכת הקבצים לא נמתחה" — למה עדיין פתוח.
    const note = r.note ? `<span class="sub">${esc(r.note)}</span>` : "";
    return [UI.name(r.serial || "—", r.model || ""), esc(where), esc(fmtWhen(r.opened_at)), `<span class="disk-smart">${esc(text)}</span>${note}`, esc(r.image_name || ""),
      isAdmin() ? UI.acts([["נקה", `clearShrinkRecord(${Number(r.id)})`]]) : ""];
  });
  const body = SHRINK_RECORDS == null ? UI.note("warn", "רשימת הדיסקים המכווצים לא נטענה")
    : UI.datagrid({ columns: ["מספר סידורי", "מכונה · דיסק · חריץ", "מתי", "מצב", "אימג'", ""], rows, empty: "אין דיסקים מכווצים" });
  return UI.card({ title: "דיסקים מכווצים — מחיצת המקור לא הוחזרה לגודלה", small: 'מחשב הבנייה מציע להחזיר באתחול; "נקה" אחרי הרחבה ידנית או החלפת דיסק', flush: true, body });
}
async function clearShrinkRecord(id) {
  try {
    await post(`/shrink-records/${id}/clear`);
    toast("הרשומה נוקתה");
    await loadMachines();
  } catch (e) {
    toast("הניקוי נכשל: " + e.message);
  }
}

/* ---------- מגירת מחשב (machine-drawer.md) ---------- */
function diskBoxesHtml(m) {
  if (m.disks == null) return UI.note("", "המכונה מעולם לא דיווחה על כוננים.");
  if (!m.disks.length) return UI.note("warn", `המכונה דיווחה — ואין בה אף כונן: ${zeroDisksText(m)}.`);
  const fails = machineDiskFailures(m);
  const boxes = m.disks.map((d, i) => {
    const n = diskSlot(d, i);
    const red = fails.find((f) => failureSlot(m, f) === n);
    const cls = red ? "err" : d.smart === "ok" ? "ok" : d.smart === "warn" ? "warn" : d.smart === "fail" ? "err" : "";
    const smart = d.smart ? `SMART ${SMART_HE[d.smart] || d.smart}` : "SMART לא נבדק";
    return `<div class="disk ${cls}"><b>דיסק ${n}</b><span>SATA ${n - 1}</span><span>${ltr(fmtBytes(d.size_bytes))}</span><span class="cap">${esc(d.model || "—")}${d.serial ? ` · <span class="mono">${esc(d.serial)}</span>` : ""}</span><span class="cap">${red ? `אדום — ${esc(failureCauseText(red))}` : esc(smart)}</span></div>`;
  }).join("");
  return `<div class="disks">${boxes}</div>`;
}
/* ‏#1049 שלב ב': "בריאות המכונה" — מה שהסוכן מדד ב-hello (probe). כל שדה
   בשלושה מצבים (עיקרון 5): נמדד · null = "לא נבדק" (אפור, לא "תקין") ·
   {error} = "לא הצלחנו לבדוק: …" (כתום). השערים (probe_verdicts) מהשרת
   מוצגים מעל הקבוצה ואינם עוצרים סבב — עצירה היא הכרעה נפרדת. */
function probeCell(value, render, unchecked = "לא נבדק") {
  if (value == null) return `<span class="muted">${esc(unchecked)}</span>`;
  if (typeof value === "object" && !Array.isArray(value) && value.error != null) return UI.status("warn", `לא הצלחנו לבדוק: ${value.error}`);
  return render(value);
}
/* #1048: מתג/פורט (LLDP) וכבל (ethtool) — שלושת המצבים, בלי לקפל unheard לתקין. */
function netprobeLldpHtml(np) {
  if (np == null) return `<span class="muted">לא נבדק</span>`;
  if (np.pending) return `<span class="muted">ממתין</span>`;
  const v = np.lldp;
  if (v == null) return `<span class="muted">לא נבדק</span>`;
  if (v.error != null) return UI.status("warn", "לא הצלחנו להאזין");
  if (v.unheard) return `<span class="muted">לא נקלט (35ש')</span>`;
  const sw = v.switch || "", port = v.port || "";
  if (!sw && !port) return `<span class="muted">לא נבדק</span>`;
  const tip = [v.port_desc, v.chassis].filter(Boolean).join(" · ");
  return `<span class="mono" dir="ltr"${tip ? ` title="${esc(tip)}"` : ""}>${esc(sw)}${sw && port ? " · " : ""}${esc(port)}</span>`;
}
function netprobeCableHtml(np) {
  if (np == null) return `<span class="muted">לא נבדק</span>`;
  if (np.pending) return `<span class="muted">ממתין</span>`;
  const v = np.cable;
  if (v == null) return `<span class="muted">לא נבדק</span>`;
  if (v.error != null) return UI.status("warn", `לא הצלחנו לבדוק: ${v.error}`);
  if (v.skipped) return `<span class="muted">לא נבדק — יש קישור</span>`;
  if (v.status === "ok") return UI.status("ok", "תקין");
  if (v.status === "open" || v.status === "short") {
    const he = v.status === "open" ? "פתוח" : "קצר";
    const bad = (Array.isArray(v.pairs) ? v.pairs : []).find((p) => p && String(p.code || "").toLowerCase() === v.status);
    const pair = (bad && bad.pair) || "?", n = bad && bad.length_m != null ? bad.length_m : null;
    return UI.status("err", n != null ? `זוג ${pair} ${he} ב-${n} מ'` : `זוג ${pair} ${he}`);
  }
  return `<span class="muted">לא נבדק</span>`;
}
function probeSkewText(seconds) {
  const s = Math.abs(Number(seconds) || 0);
  return s >= 86400 ? `${Math.floor(s / 86400)} ימים` : s >= 3600 ? `${Math.floor(s / 3600)} שעות` : s >= 60 ? `${Math.floor(s / 60)} דק'` : `${s} שנ'`;
}
function machineVerdictsHtml(m) {
  return (Array.isArray(m.probe_verdicts) ? m.probe_verdicts : []).map((v) => UI.note(v.level === "err" ? "err" : "warn", esc(v.text_he || v.key || ""))).join("");
}
function machineHealthHtml(m) {
  const p = m.probe;
  if (p == null) return UI.note("", "המכונה מעולם לא דיווחה בדיקת מכונה (סוכן ישן).");
  const nvme = Array.isArray(p.disks) ? p.disks.filter((d) => d && d.nvme_smart !== null && d.nvme_smart !== undefined) : [];
  const rows = [
    ["חשמל", probeCell(p.power, (v) => v.on_battery ? UI.status("err", `על סוללה${v.supply ? ` (${v.supply})` : ""}`) : UI.status("ok", `חשמל${v.supply ? ` (${v.supply})` : ""}`))],
    ["שעון", probeCell(p.rtc, (v) => v.skew_seconds == null ? `<span class="muted">לא נבדק</span>`
      : Math.abs(v.skew_seconds) > 300 ? UI.status("warn", `סוטה ב-${probeSkewText(v.skew_seconds)} — סוללת BIOS חשודה`) : UI.status("ok", `סטייה ${probeSkewText(v.skew_seconds)}`))],
    ["מעבד", probeCell(p.cpu, (v) => `${esc(v.model || "—")}${v.cores != null ? ` · ${esc(v.cores)} ליבות` : ""}${v.microcode ? ` · מיקרוקוד <span class="mono">${esc(v.microcode)}</span>` : ""}`)],
    ["זיכרון", probeCell(p.memory, (v) => [
      v.total_bytes != null ? ltr(fmtBytes(v.total_bytes)) : `<span class="muted">סה"כ לא נבדק</span>`,
      probeCell(v.dimms, (d) => Array.isArray(d) ? `${d.length} DIMMs` : `<span class="muted">DIMMs לא נבדקו</span>`, "DIMMs לא נבדקו"),
      probeCell(v.ecc, (e) => e.ue_count > 0 ? UI.status("err", `ECC: ${e.ue_count} לא-מתוקנות`) : e.ce_count > 0 ? UI.status("warn", `ECC: ${e.ce_count} מתוקנות`) : UI.status("ok", "ECC תקין"), "ECC לא נבדק"),
    ].join(" · "))],
    ["טמפ' מקס'", probeCell(p.thermal, (v) => {
      const temps = (Array.isArray(v) ? v : []).map((z) => z && Number(z.temp_c)).filter((t) => Number.isFinite(t));
      if (!temps.length) return `<span class="muted">אין אזור תרמי</span>`;
      const max = Math.max(...temps);
      return UI.status(max >= 90 ? "err" : max >= 80 ? "warn" : "ok", `${max}°C`);
    })],
    ["רשת", probeCell(p.nic, (v) => {
      if (!Array.isArray(v) || !v.length) return `<span class="muted">אין כרטיס עם קישור</span>`;
      return v.map((n) => {
        const link = `<span class="mono">${esc(n.name || "?")}</span> ${n.speed_mbps != null ? `${esc(n.speed_mbps)}Mb/s` : "—"}${n.duplex ? ` ${esc(n.duplex)}` : ""}`;
        const cnt = probeCell(n.stats, (x) => (x.rx_crc_errors > 0 ? UI.status("warn", `CRC ${x.rx_crc_errors}`) : `CRC ${esc(x.rx_crc_errors ?? "—")}`) + ` · dropped ${esc(x.rx_dropped ?? "—")}`, "מונים לא נבדקו");
        const dup = probeCell(n.ip_conflict, (x) => x.duplicate ? UI.status("err", "כפילות IP") : `IP ללא כפילות`, "כפילות IP לא נבדקה");
        return `${link} · ${cnt} · ${dup}`;
      }).join("<br>");
    })],
    ["מתג ופורט", netprobeLldpHtml(p.netprobe)],
    ["כבל", netprobeCableHtml(p.netprobe)],
    ["NVMe", p.disks == null ? `<span class="muted">לא נבדק</span>` : !nvme.length ? `<span class="muted">לא נבדק (אין NVMe, או nvme לא ארוז)</span>`
      : nvme.map((d) => `<span class="mono">${esc(d.name || "?")}</span>: ` + probeCell(d.nvme_smart, (x) => {
        const bad = (x.critical_warning != null && x.critical_warning !== 0) || x.media_errors > 0;
        const text = `${x.percentage_used != null ? `${x.percentage_used}% בלאי` : "בלאי לא דווח"} · ${x.media_errors != null ? `${x.media_errors} שגיאות מדיה` : "שגיאות מדיה לא דווחו"}${x.critical_warning ? ` · critical_warning=${x.critical_warning}` : ""}`;
        return UI.status(bad ? "err" : x.percentage_used >= 90 ? "warn" : "ok", text);
      })).join("<br>")],
    ["קריסה קודמת", probeCell(p.pstore, (v) => v.crashed
      ? UI.status("warn", "קרס לפני האתחול הזה") + (Array.isArray(v.files) && v.files.length ? ` <span class="mono">${esc(v.files.join(", "))}</span>` : "") + (v.excerpt ? ` <details class="inline"><summary>קטע</summary><pre class="ata-log">${esc(v.excerpt)}</pre></details>` : "")
      : `<span class="muted">לא</span>`)],
    ["מפתח OEM", probeCell(p.oem_key, (v) => `<span class="mono" dir="ltr">${esc(v)}</span>`)],
    ["PCI בלי דרייבר", probeCell(p.pci_without_driver, (v) => !Array.isArray(v) || !v.length ? `<span class="muted">אין</span>`
      : `${UI.status("warn", `${v.length} ${v.length === 1 ? "התקן" : "התקנים"}`)} <span class="mono" title="${esc(v.join(" "))}">${esc(v.slice(0, 3).join(" · "))}${v.length > 3 ? " …" : ""}</span>${isAdmin() ? ` · ${UI.link("לדף הדרייברים", "selectPageById('drivers')")}` : ""}`)],
    ["הצפנה", probeCell(p.encryption, (v) => !Array.isArray(v) || !v.length ? `<span class="muted">אין</span>`
      : v.map((e) => `${esc(e.type === "crypto_LUKS" ? "LUKS" : e.type || "?")} <span class="mono">${esc(e.node || "")}</span>`).join(" · "))],
  ];
  return UI.kv(rows);
}
function sshHostkeyHtml(m) {
  /* #1080: fingerprint from hello. 12 chars of the hash + full tooltip.
     Orange only when the key changed inside the same boot_id; a reboot
     is grey "מפתח חדש מאתחול". Missing field (old agent) is muted. */
  const k = m.ssh_hostkey;
  if (k == null) return `<span class="muted">לא דווח</span>`;
  const fp = String(k.fingerprint || "");
  const hash = fp.startsWith("SHA256:") ? fp.slice(7) : fp;
  const short = hash.slice(0, 12);
  const shown = `<span class="mono" dir="ltr" title="${esc(fp)}">SHA256:${esc(short)}${hash.length > 12 ? "…" : ""}</span>`;
  if (m.ssh_hostkey_changed_in_boot) return shown + " " + UI.status("warn", "השתנה באתחול הזה");
  if (m.ssh_hostkey_new_from_reboot) return shown + ` <span class="muted">מפתח חדש מאתחול</span>`;
  return shown;
}
function machineDrawerHtml(m) {
  const macEnc = encodeId(m.mac), role = machineRole(m), g = machineGroup(m), net = netFor(m.mac), st = machineState(m), admin = isAdmin();
  const sub = [esc(ROLE_HE[role] || role), g ? esc(g.label) : "", `<span class="mono">${esc(m.mac)}</span>`, net && net.ip ? `<span class="mono">${esc(net.ip)}</span>` : "",
    `נראה ${esc(NET ? seenAgo(net && net.last_seen) : "לא נקרא")}`].filter(Boolean).join(" · ");
  const actions = `<div class="acts" style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">`
    + (admin && ["build", "cloner"].includes(role) ? `<button class="btn" onclick="wakeMachine('${macEnc}')">WoL</button>` : `<span class="muted" title="v2">WoL — לתחנות כיתה ב-v2</span>`)
    + (admin ? `<button class="btn" onclick="renameMachine('${macEnc}')">שינוי שם</button>` : "")
    + UI.soon("אתחול מרחוק") + `</div>`;
  const red = machineDiskFailures(m).map((f) => {
    const n = failureSlot(m, f), log = f.ata_log || [];
    return UI.note("err", `<b>דיסק ${n != null ? n : "?"} אדום</b> — <span class="mono">${esc(f.serial || "—")}</span> נכשל בכתיבה ${esc(fmtWhen(f.at))}, סיבה: ${esc(failureCauseText(f))}${f.error ? ` (${esc(f.error)})` : ""}. התחנה תסמן אותו באדום לפני כל סבב.${admin ? ` ${UI.link("נקה אחרי החלפה", `clearDiskFailure(${Number(f.id)})`)}` : ""}${log.length ? ` <details class="inline"><summary>${log.length} שורות קרנל</summary><pre class="ata-log">${esc(log.join("\n"))}</pre></details>` : ""}`);
  }).join("");
  const now = UI.kv([
    ["מה המכונה עושה", UI.status(st.cls, st.text)],
    ["שלב אתחול אחרון", net && net.boot ? bootCell(net.boot) : `<span class="muted">${NET ? "לא נרשם פירור" : "לא נקרא"}</span>`],
    ["IP אחרון", net && net.ip ? `<span class="mono">${esc(net.ip)}</span>` : `<span class="muted">—</span>`],
    ["SSH", sshHostkeyHtml(m)],
    ["נראה לאחרונה", esc(NET ? seenAgo(net && net.last_seen) : "לא נקרא")],
  ].concat(m.note ? [["הערה", esc(m.note)]] : []));
  const slots = role === "cloner"
    ? `<div class="cap" style="margin-top:6px">${m.drawer_count != null ? `${m.drawer_count} חריצים מוגדרים` : "מספר החריצים לא הוגדר"}${admin ? ` · ${UI.link("שנה", `editDrawerCount('${macEnc}')`)}` : ""}</div>` : "";
  const inv = m.inventory, dmi = (inv && inv.dmi) || {};
  let drivers = `<span class="muted">דף הדרייברים לא נטען</span>`;
  if (typeof DRIVERS !== "undefined" && Array.isArray(DRIVERS)) {
    const pk = DRIVERS.filter((p) => (p.matches || []).includes(m.mac));
    drivers = pk.length ? `${pk.length} ${pk.length === 1 ? "חבילה" : "חבילות"} — ${esc(pk.map((p) => p.name).join(", "))}` : `<span class="muted">אין חבילה מתאימה</span>`;
  }
  const hw = inv == null ? UI.note("", "המכונה מעולם לא דיווחה על חומרה (סוכן ישן).")
    : UI.kv([
      ["דגם", esc([dmi.sys_vendor, dmi.product_name].filter(Boolean).join(" ") || "—") + (dmi.product_version ? ` · ${esc(dmi.product_version)}` : "")],
      ["לוח", esc(dmi.board_name || "—")],
      ["TPM", inv.tpm == null ? "לא נבדק" : inv.tpm.present ? `יש${inv.tpm.version ? " · " + esc(inv.tpm.version) : ""}` : "אין"],
      ["PCI רשת/אחסון", (inv.pci || []).map((p) => `<span class="mono">${esc(p)}</span>`).join(" · ") || "—"],
      ["דרייברים מתאימים", drivers],
    ]);
  const h = MCH.history[m.mac];
  const history = Array.isArray(h) && h.length
    ? UI.timeline(h.slice(0, 20).map((r) => ({ t: isToday(r.ts) ? fmtClock(r.ts) : fmtDate(r.ts), cls: journalCls(r), text: esc(r.label || r.event || "") + (r.text ? " — " + esc(r.text) : ""), who: r.user || "" })))
    : h && h.error ? UI.note("warn", "היומן לא נקרא: " + esc(h.error))
    : `<div class="muted">${Array.isArray(h) ? "אין אירועים ביומן למחשב הזה (20 האחרונים)." : "טוען את היומן…"}</div>`;
  const foot = admin
    ? `<div style="border-top:1px solid var(--hair);padding-top:12px;display:flex;gap:6px;flex-wrap:wrap;align-items:center"><button class="btn sm" onclick="moveMachineSheet('${macEnc}')">העבר לקבוצה</button>${UI.soon("עריכת MAC")}<button class="btn sm danger" onclick="removeMachine('${macEnc}')">הסרה מהרישום (הקלדת שם)</button></div>`
    : "";
  return `<div class="page drw"><div class="obj-sub">${sub}</div>${actions}${red}${machineCaptureWarningHtml(m.mac)}${machineRestoreWarningHtml(m.mac)}<div><div class="sec">מצב עכשיו</div>${now}</div><div><div class="sec">דיסקים${m.disks_reported_at ? ` (דיווח אחרון, ${esc(ago(m.disks_reported_at))})` : ""}</div>${diskBoxesHtml(m)}${slots}</div><div><div class="sec">חומרה (למיפוי דרייברים)${m.inventory_seen_at ? ` (${esc(ago(m.inventory_seen_at))})` : ""}</div>${hw}</div><div><div class="sec">בריאות המכונה${m.probe_seen_at ? ` (נדגם ${esc(ago(m.probe_seen_at))})` : ""}</div>${machineVerdictsHtml(m)}${machineHealthHtml(m)}</div><div><div class="sec">היסטוריה</div>${history}</div>${foot}</div>`;
}
function openMachineDetail(mac) {
  if (!isAdmin()) return;
  const m = findMachine(mac);
  if (!m) { toast("מחשב לא נמצא"); return; }
  openDrawer(machineName(m) || m.mac, machineDrawerHtml(m));
  if (MCH.history[m.mac]) return;
  const gen = drawerGeneration;
  api("/journal?machine=" + encodeURIComponent(machineName(m) || m.mac) + "&limit=20")
    .then((rows) => { MCH.history[m.mac] = rows; })
    .catch((e) => { MCH.history[m.mac] = { error: e.message }; })
    .then(() => { if (gen === drawerGeneration) document.getElementById("drawerBody").innerHTML = machineDrawerHtml(m); });
}
function groupOptions(selected) {
  return machineGroupsInOrder().map((g) => ({ value: g.id, label: g.label, selected: g.id === selected }));
}
function renameMachine(mac) {
  const m = findMachine(mac); if (!m || !isAdmin()) return;
  sheet({ title: "שינוי שם", sub: m.mac, fields: [{ id: "name", label: machineRole(m) === "classroom" ? "סיומת (01–99 או INS)" : "שם", value: machineName(m) }],
    onSubmit: async (v) => { await put("/machines/" + encodeId(m.mac), { name: v.name }); MCH.history = {}; await loadMachines(); openMachineDetail(m.mac); } });
}
function moveMachineSheet(mac) {
  const m = findMachine(mac); if (!m || !isAdmin()) return;
  sheet({ title: "העבר לקבוצה", sub: machineName(m) || m.mac, fields: [{ id: "group_id", type: "select", label: "קבוצה", options: groupOptions(machineGroupId(m)), value: machineGroupId(m) }],
    onSubmit: async (v) => { await put("/machines/" + encodeId(m.mac), { group_id: v.group_id }); await loadMachines(); openMachineDetail(m.mac); } });
}
function editDrawerCount(mac) {
  const m = findMachine(mac); if (!m || !isAdmin()) return;
  sheet({ title: "מספר חריצי SATA", sub: `${machineName(m) || m.mac} — כמה דיסקים המחשב אמור לקבל בסבב (#695)`,
    fields: [{ id: "drawer_count", type: "number", label: "חריצים (1–8)", value: m.drawer_count == null ? "" : String(m.drawer_count) }],
    onSubmit: async (v) => { await put("/machines/" + encodeId(m.mac), { drawer_count: Number(v.drawer_count) }); await loadMachines(); openMachineDetail(m.mac); } });
}
function removeMachine(mac) {
  const m = findMachine(mac); if (!m || !isAdmin()) return;
  const name = machineName(m) || m.mac;
  sheet({ title: "הסרה מהרישום", sub: `${name} (${m.mac}) תוסר מהטבלה. באתחול הבא היא תדווח כלא רשומה.`, danger: true, submitLabel: "הסר",
    verify: { label: "הקלד את שם המחשב", mustEqual: name },
    onSubmit: async () => { dhcpNotice(await del("/machines/" + encodeId(m.mac))); closeDrawer(); MCH.sel.delete(m.mac); await loadMachines(); } });
}
function bulkMoveMachines() {
  const macs = [...MCH.sel]; if (!macs.length || !isAdmin()) return;
  sheet({ title: "העבר לקבוצה", sub: `${macs.length} מחשבים`, fields: [{ id: "group_id", type: "select", label: "קבוצה", options: groupOptions(""), value: "" }],
    onSubmit: async (v) => { for (const mac of macs) await put("/machines/" + encodeId(mac), { group_id: v.group_id }); MCH.sel.clear(); await loadMachines(); } });
}
/* פעולה הרסנית מאחורי הקלדת שם (עיקרון 7) — שם לכל מחשב, כמו מחיקה מרובה בספרייה. */
function bulkRemoveMachines() {
  const macs = [...MCH.sel]; if (!macs.length || !isAdmin()) return;
  const names = macs.map((mac) => { const m = findMachine(mac); return m ? machineName(m) || mac : mac; });
  sheet({ title: `הסרה מהרישום — ${macs.length} מחשבים`, sub: "כל מחשב יוסר מהטבלה ובאתחול הבא ידווח כלא רשום. הקלידו את שמו של כל אחד.", danger: true, submitLabel: "הסר את כולם",
    fields: macs.map((mac, i) => ({ id: "n" + i, label: `הקלד "${names[i]}" (${mac})`, dir: "ltr" })),
    onSubmit: async (v) => {
      macs.forEach((mac, i) => { if (v["n" + i] !== names[i]) throw new Error("השם שהוקלד אינו זהה: " + names[i]); });
      for (const mac of macs) dhcpNotice(await del("/machines/" + encodeId(mac)));
      MCH.sel.clear(); await loadMachines();
    } });
}

/* ---------- + מחשב: מודאל עם שתי לשוניות — אחד / הדבקה עם תצוגה מקדימה (dry_run) ---------- */
function groupOptionsHtml(selected) {
  return machineGroupsInOrder().map((g) => `<option value="${esc(g.id)}"${g.id === selected ? " selected" : ""}>${esc(g.label)}</option>`).join("");
}
function openAddMachine({ group = "", mac = "", tab = 0 } = {}) {
  if (!isAdmin()) return;
  try { group = decodeURIComponent(group); } catch (e) {}
  try { mac = decodeURIComponent(mac); } catch (e) {}
  MCH.amTab = tab;
  const tabBtn = (i, label) => `<button type="button" class="tab${tab === i ? " on" : ""}" role="tab" aria-selected="${tab === i}" onclick="amTab(${i})">${label}</button>`;
  const body = `<div class="page drw am"><div class="tabs" role="tablist">${tabBtn(0, "מחשב אחד")}${tabBtn(1, "הדבקה — הרבה בבת אחת")}</div>
<div id="am-one" class="form"${tab === 1 ? " hidden" : ""}><div class="field"><label for="am-group">קבוצה</label><select id="am-group">${groupOptionsHtml(group)}</select></div><div class="field"><label for="am-name">שם / סיומת</label><input id="am-name" autocomplete="off" placeholder="${classroomsOn() ? "01–99 או INS לכיתה; שם חופשי לבנייה ולשיכפול" : "שם חופשי לבנייה ולשיכפול"}"></div><div class="field full"><label for="am-mac">MAC</label><input id="am-mac" dir="ltr" value="${esc(mac)}" placeholder="b4:2e:99:07:1a:c4" autocomplete="off"></div></div>
<div id="am-paste" class="form"${tab === 0 ? " hidden" : ""}><div class="field full"><label for="am-pgroup">קבוצה</label><select id="am-pgroup">${groupOptionsHtml(group)}</select></div><div class="field full"><label for="am-text">שורה לכל מחשב: MAC ואז סיומת/שם</label><textarea id="am-text" rows="7" dir="ltr" placeholder="b4:2e:99:07:1a:c4 01&#10;B4-2E-99-07-1A-C5 02"></textarea></div><div class="field full"><button type="button" class="btn sm" onclick="previewMachineImport()">תצוגה מקדימה</button></div><div id="am-preview" class="stack"></div></div></div>`;
  openModalContent("+ מחשב", body, "הוסף", submitAddMachine);
}
function amTab(i) {
  MCH.amTab = i;
  const one = document.getElementById("am-one"), paste = document.getElementById("am-paste");
  if (one) one.hidden = i !== 0;
  if (paste) paste.hidden = i !== 1;
  document.querySelectorAll("#modalBody .tab").forEach((b, j) => { b.classList.toggle("on", j === i); b.setAttribute("aria-selected", String(j === i)); });
}
function importPreviewHtml(lines) {
  if (!lines.length) return UI.note("", "אין שורות לייבוא");
  return lines.map((l) => l.error ? UI.status("err", `${l.raw} ← ${l.error}`) : UI.status("ok", `${l.mac} ${l.suffix}`)).join("");
}
async function previewMachineImport() {
  const body = { group_id: $("#am-pgroup").value, text: $("#am-text").value, dry_run: true };
  try { const r = await post("/machines/import", body); $("#am-preview").innerHTML = importPreviewHtml(r.preview || []); }
  catch (e) { $("#am-preview").innerHTML = UI.note("err", esc(e.message)); }
}
async function submitAddMachine() {
  try {
    if (MCH.amTab === 1) {
      const r = await post("/machines/import", { group_id: $("#am-pgroup").value, text: $("#am-text").value });
      closeModal();
      dhcpNotice(r) || toast(`נשמרו ${r.saved}. נדחו ${(r.rejected || []).length}.`);
    } else {
      const name = $("#am-name").value, mac = $("#am-mac").value, group_id = $("#am-group").value;
      if (!name.trim() || !mac.trim() || !group_id) { toast("נדרשים שם, קבוצה ו-MAC"); return; }
      const r = await post("/machines", { mac, name, group_id });
      closeModal();
      dhcpNotice(r) || toast("המחשב נוסף");
    }
    await loadMachines();
  } catch (e) { toast(e.message); }
}
function openNewMachine() { openAddMachine({}); }   // הסקירה (homeAttention) קוראת לזה
function addGroupSheet() {
  if (!isAdmin() || !classroomsOn()) return;
  sheet({
    title: "כיתה חדשה", sub: "כל כיתה היא קבוצה. השם חופשי — עברית, אנגלית או מספרים.",
    fields: [
      { id: "label", label: "שם הכיתה", placeholder: "למשל: כיתה 303 — סייבר" },
      { id: "id", label: "מזהה קצר באנגלית (לא חובה — נגזר מהשם)", placeholder: "LAB303", dir: "ltr" },
    ],
    submitLabel: "צור כיתה",
    onSubmit: async (v) => { await post("/groups", { id: v.id.trim() ? "grp_" + v.id.trim() : "", label: v.label, role: "classroom" }); await loadMachines(); },
  });
}
function renameGroup(gidEnc) {
  let gid = gidEnc; try { gid = decodeURIComponent(gidEnc); } catch (e) {}
  const g = (GROUPS || []).find((x) => x.id === gid); if (!g || !isAdmin()) return;
  sheet({ title: "שינוי שם הכיתה", fields: [{ id: "label", label: "השם החדש", value: g.label }],
    onSubmit: async (v) => { await put(`/groups/${encodeId(g.id)}`, { label: v.label }); await loadMachines(); } });
}
function deleteGroup(gidEnc) {
  let gid = gidEnc; try { gid = decodeURIComponent(gidEnc); } catch (e) {}
  const g = (GROUPS || []).find((x) => x.id === gid); if (!g || !isAdmin()) return;
  const n = (MACHINES || []).filter((m) => machineGroupId(m) === g.id).length;
  sheet({ title: "מחיקת כיתה", sub: `"${g.label}" תימחק על כל ${n} המחשבים שבה.`, danger: true, submitLabel: "מחק את הכיתה",
    verify: { label: "הקלד את שם הכיתה", mustEqual: g.label },
    onSubmit: async () => { await del(`/groups/${encodeId(g.id)}`); MACHINES_CLASS = null; await loadMachines(); } });
}

/* ---------- #954 גל 5: בריאות ושירותים — טבלת בדיקות + גרסה ועדכון ----------
   docs/design/console-redesign/health.md. בדיקות ועדכון בלבד: המתגים (SSH,
   מוניטור, DHCP) עברו לדף הפורטים (הכרעת נדב 17/09 06:12). חמישה מצבים,
   חמישה צבעים — "לא נבדק" לעולם לא ירוק (עיקרון 5). */
const HEALTH_STATES = { ok: ["ok", "תקין"], warn: ["warn", "אזהרה"], bad: ["err", "תקלה"], off: ["", "כבוי"], unknown: ["unk", "לא נבדק"] };
function healthStatusClass(state) { return (HEALTH_STATES[state] || HEALTH_STATES.unknown)[0]; }
function healthStatusLabel(state) { return HEALTH_STATES[state] ? HEALTH_STATES[state][1] : (state || "—"); }
/* שורות דינמיות למכונה (agent_loop:<mac>, off_vlan:<mac>) מקובצות תחת שורת-קבוצה
   מיד אחרי שורת הסיכום שלהן (interfaces.md §10–11). */
const HEALTH_GROUPS = [["agent_loop:", "לולאות אתחול", "agent_loops"], ["off_vlan:", "מכונות בוילן זר", "off_vlan"],
  ["storage_", "אחסון — מיקומים", "storage_summary"]];   // ‏#1066: שורה לכל מיקום (storage_<id>), בלי שורת סיכום → קבוצה בסוף
/* עמודת "פעולה": לאן הולכים לטפל — הדף שבו יושב מה שנבדק. אין ב-/health שדה
   "איך מתקנים"; ה-detail של השרת כבר נושא את ההוראה ("הריצו את המתקין"). */
const HEALTH_GOTO = { dhcp_port: ["רשת הפצה", "openNetwork(2)"], tftp_port: ["פורטים", "selectPageById('ports')"], dnsmasq: ["רשת הפצה", "openNetwork(2)"],
  server: ["פורטים", "selectPageById('ports')"], udp_sender: ["פורטים", "selectPageById('ports')"], nics: ["חיבורים פיזיים", "openNetwork(1)"],
  ssh_stations: ["פורטים", "ports"], ssh_server: ["פורטים", "ports"], agent_loops: ["מחשבים", "machines"], off_vlan: ["מחשבים", "machines"] };
let healthError = "", HEALTH_AT = "";
let HEALTH_UPDATE = null, UPDATE_STATUS = null, UPDATE_CHECK = null, updateError = "";
function clockNow() { const d = new Date(); return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; }

function healthRow(c, grouped = false) {
  let act = "";
  if (grouped && String(c.id).startsWith("storage_")) {
    act = UI.acts([["פתח באחסון", "selectPageById('storage')"]]);
  } else if (grouped) {
    const mac = String(c.id).slice(String(c.id).indexOf(":") + 1);
    if (findMachine(mac)) act = UI.acts([["פרטים", `openMachineDetail('${encodeId(mac)}')`]]);
  } else if (HEALTH_GOTO[c.id]) {
    act = UI.acts([[HEALTH_GOTO[c.id][0] + " ←", HEALTH_GOTO[c.id][1]]]);
  }
  return [`<span class="name${grouped ? " grp-kid" : ""}">${esc(c.label)}</span>`,
    UI.status(healthStatusClass(c.state), healthStatusLabel(c.state)),
    `<span class="wrap">${esc(c.detail || "")}</span>`, act];
}

function healthRows(checks) {
  const kids = new Map(HEALTH_GROUPS.map(([p]) => [p, []]));
  const prefixOf = (c) => (HEALTH_GROUPS.find(([p]) => String(c.id).startsWith(p)) || [null])[0];
  for (const c of checks) if (prefixOf(c)) kids.get(prefixOf(c)).push(c);
  const rows = [];
  const groupBlock = ([p, label]) => {
    const list = kids.get(p);
    if (!list.length) return;
    rows.push({ html: `<tr class="group"><td colspan="4">${esc(label)} · ${list.length}</td></tr>` });
    list.forEach((c) => rows.push(healthRow(c, true)));
    kids.set(p, []);
  };
  for (const c of checks) {
    if (prefixOf(c)) continue;
    rows.push(healthRow(c));
    const g = HEALTH_GROUPS.find(([, , summary]) => summary === c.id);
    if (g) groupBlock(g);
  }
  HEALTH_GROUPS.forEach(groupBlock);   // ילדים בלי שורת סיכום — בסוף, עדיין מקובצים
  return rows;
}

function updateStatusHtml(status) {
  if (!status || !status.state || status.state === "idle") return "";
  if (status.state === "failed") return UI.status("err", `העדכון ל-${status.tag} נכשל: ${status.error || ""}`);
  if (status.state === "applying" && !status.verified) return UI.status("run", `העדכון ל-${status.tag} הופעל — ממתין לאתחול השרת. הראיה החיובית: גרסת השרת אחרי האתחול תואמת את התג.`);
  if (status.state === "done" && status.verified) return UI.status("ok", `אומת: השרת רץ על ${status.tag}.`);
  return "";
}

/* ‏#1000: "היום HH:MM" / "dd/mm/yyyy HH:MM" מחותמת ISO של השרת. */
function fmtWhenShort(ts) {
  if (!ts) return "";
  return isToday(ts) ? `היום ${fmtClock(ts)}` : `${fmtDate(ts)} ${fmtClock(ts)}`;
}
/* ‏#1000: שורת "בדיקה אחרונה" — מ-/update.last_check (השרת זוכר), לא מהסשן.
   ‏null = מעולם לא נבדק בשרת הזה. "הבדיקה נכשלה" (reason) ≠ "אין חדש". */
function updateLastCheckText(u) {
  if (UPDATE_CHECK && UPDATE_CHECK.failed) return `${UPDATE_CHECK.at} — ${UPDATE_CHECK.reason}`;   // הבקשה עצמה נכשלה — לא נשמרה בשרת
  const c = u && u.last_check;
  if (!c) return "מעולם לא נבדק בשרת הזה";
  const what = c.available ? `יש עדכון: ${c.current || "?"} → ${c.latest}`
    : c.latest ? `כבר על הגרסה העדכנית (${c.current})` : (c.reason ? `הבדיקה נכשלה: ${c.reason}` : "לא נמצאה גרסה חדשה יותר");
  return `${fmtWhenShort(c.at)} — ${what}`;
}

function healthUpdateCard() {
  let body;
  if (updateError) body = UI.note("err", `‏/update לא נקרא: ${esc(updateError)}`);
  else if (!HEALTH_UPDATE) body = UI.empty("‏/update לא נקרא עדיין");
  else {
    const u = HEALTH_UPDATE;
    const last = updateLastCheckText(u);
    const pairs = [["מותקן", u.current ? `<span class="mono">${esc(u.current)}</span>` : "לא ידועה (אין תגית git על העץ)"],
      ["קודם", u.previous ? `<span class="mono">${esc(u.previous)}</span> (חזרה זמינה)` : "—"],
      ["בדיקה אחרונה", esc(last)]];
    const st = updateStatusHtml(UPDATE_STATUS);
    if (st) pairs.push(["מצב", st]);
    const canApply = UPDATE_CHECK && UPDATE_CHECK.available && UPDATE_CHECK.latest;
    const btns = !u.enabled ? "" : [
      `<button class="btn" onclick="healthUpdateCheck()">בדוק עדכון</button>`,
      canApply ? `<button class="btn primary" onclick="confirmUpdateAction('עדכון שרת', UPDATE_INFO.latest, 'apply', UPDATE_INFO.latest)">החל עדכון ${esc(UPDATE_CHECK.latest)} (הקלדת שם השרת)</button>` : "",
      u.previous ? `<button class="btn danger" onclick="confirmUpdateAction('חזרה לגרסה הקודמת', UPDATE_INFO.previous, 'revert', UPDATE_INFO.previous)">חזור ל-${esc(u.previous)}</button>` : "",
    ].join("");
    const off = u.enabled ? "" : UI.note("warn", `העדכון כבוי (update_enabled) — ${UI.link("הדלקה בהגדרות", "selectPageById('settings')")}`);
    body = `<div class="upd">${UI.kv(pairs)}${btns ? `<div class="acts">${btns}</div>` : ""}</div>${off}`;
  }
  return UI.card({ title: "גרסה ועדכון", small: "מהתג של עץ השרת (git describe)", body });
}

async function healthUpdateCheck() {
  try {
    const r = await post("/update/check", {});
    // ‏#1000: התשובה היא המסמך שהשרת שמר (עם `at` שלו) — אותו דבר ש-GET /update יחזיר.
    UPDATE_CHECK = r;
    if (HEALTH_UPDATE) HEALTH_UPDATE.last_check = r;
    UPDATE_INFO.latest = r.latest;
  } catch (e) {
    // הבקשה לשרת נכשלה (404 מתג כבוי / רשת): לא נשמר בשרת, ולכן שעון הדפדפן
    // ובשם — "הבדיקה נכשלה" ≠ "אין חדש" (עיקרון 5)
    UPDATE_CHECK = { at: clockNow(), available: false, latest: null, failed: true, reason: "הבדיקה נכשלה: " + e.message };
    toast(e.message);
  }
  if (current === "health") renderCurrent();
}

function health() {
  if (!HEALTH && !healthError) return pagePlaceholder();
  const checks = Array.isArray(HEALTH) ? HEALTH : [];
  const n = (s) => checks.filter((c) => c.state === s).length;
  const counts = [["ok", "תקינות"], ["warn", "אזהרות"], ["bad", "תקלות"], ["off", "כבויות"], ["unknown", "לא נבדקו"]]
    .filter(([s]) => n(s)).map(([s, l]) => `${n(s)} ${l}`);
  let pill;
  if (!HEALTH) pill = UI.pill("err", "לא נקרא");
  else if (!checks.length) pill = UI.pill("", "אין בדיקות");
  else if (n("bad")) pill = UI.pill("err", "תקלה");
  else if (n("warn")) pill = UI.pill("warn", "אזהרה");
  else if (n("unknown")) pill = UI.pill("warn", "לא נבדק");
  else if (n("off")) pill = UI.pill("", "חלק כבוי");
  else pill = UI.pill("ok", "הכל תקין");
  const sub = HEALTH
    ? [`${checks.length} בדיקות`, HEALTH_AT ? `נבדק ${HEALTH_AT}` : "זמן המדידה לא נמסר", ...counts, "המתגים (SSH, מוניטור, DHCP) — בעמוד הפורטים"].map(esc).join(" · ")
    : `‏/health לא נקרא: ${esc(healthError)}`;
  const actions = `<button class="btn primary" onclick="loadHealth()">בדוק עכשיו</button>`;
  const header = UI.objHeader({ crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "תשתית" }, { label: "בריאות ושירותים" }],
    icon: "health", name: "בריאות ושירותים", sub, pill, actions });
  const body = !HEALTH
    ? UI.note("err", `לא הצלחתי לקרוא את הבדיקות: ${esc(healthError)}`)
    : UI.datagrid({ columns: ["בדיקה", "מצב", "מה נמצא", ""], rows: healthRows(checks), empty: "אין בדיקות — השרת החזיר רשימה ריקה" });
  const checksCard = UI.card({ title: "בדיקות חיוניות", small: "מה שנמדד, לא מה שמוגדר — \"לא נבדק\" הוא מצב משלו", body, flush: !!HEALTH && checks.length > 0 });
  return `<div class="page">${header}<div class="body">${checksCard}${healthUpdateCard()}</div></div>`;
}

function roleLabel(role) {
  if (role === "admin") return "מנהל";
  if (role === "deploy") return "מפעיל הפצה";
  return role || "—";
}

function fmtWhen(ts) {
  const s = String(ts || "");
  if (!s) return "—";
  return s.replace("T", " ").replace(/\+00:00$/, "").replace(/Z$/, "").slice(0, 19);
}

function fmtHour(ts) {
  const s = String(ts || "");
  const m = s.match(/T(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : (s || "—");
}

/* ---------- #954 גל 6: הרשאות ----------
   נבנה לפי docs/design/console-redesign/permissions.md: טבלת משתמשים אחת
   (‏/users) במקום שתיים, ומטריצת "מה כל תפקיד רואה" שנגזרת מהקוד — מה
   מחזיר 403 בשרת (‏auth.dependencies: current_user / admin_only, ו-round_operator
   / room_operator = admin+deploy). "כניסה אחרונה" אין ב-/users — לא מומצא.
   המנהל הפעיל האחרון: בלי "תפקיד/השבת/מחיקה" (השרת מסרב; users.py). */
let usersError = "";

/* שורה = אזור; [מנהל], [הפצה] כ-[cls, טקסט]; המקור = ה-Depends של ה-endpoint
   (server/*.py). ‏#1073 (הכרעת נדב 18/09): למשתמש הפצה אין כניסה לקונסולה —
   הכניסה מסרבת לו (deploy_no_console) וכל נתיב ניהול מחזיר לו 403. מה
   שמסומן "ממחשב הבנייה" פתוח לו רק שם — דרך allowlist הקיוסק (kiosk.py). */
const ROLE_MATRIX = [
  ["כניסה לקונסולה (ניהול וובי)", ["ok", "כן"], ["", "לא"], "POST /login — deploy → 403 deploy_no_console; כל /api/console/* — auth.console_only (#1073)"],
  ["אימג'ים, תיקיות, קבוצות — צפייה", ["ok", "כן"], ["ok", "ממחשב הבנייה"], "GET /images, /folders, /groups — allowlist הקיוסק; /overview, /net — קונסולה בלבד"],
  ["חדר המשכפלים: סבב, גל, WoL לחדר; שחזור לדיסק מחשב הבנייה", ["ok", "כן"], ["ok", "ממחשב הבנייה"], "POST /room, /room/start|wake|close — room_operator, allowlist הקיוסק; single_restore (#706/#1073)"],
  ["קליטה, העלאה, מחיקה ועריכה של אימג'ים ותיקיות", ["ok", "כן"], ["", "לא"], "POST /tasks/capture, /images/upload, /images/{id}/delete, PUT /images/{id}, /folders — admin_only"],
  ["מחשבים: צפייה, הוספה, עריכה, הסרה, WoL למחשב, ניקוי דיסק אדום", ["ok", "כן"], ["", "לא"], "GET/POST/PUT/DELETE /machines, /machines/{mac}/wake, /groups/{gid}/wake, /disk-failures — קונסולה בלבד"],
  ["מוניטור", ["ok", "כן"], ["", "לא"], "GET/PUT /monitor/settings, GET /monitor/machines — admin_only"],
  ["דרייברים: צפייה, ייבוא ומחיקה", ["ok", "כן"], ["", "לא"], "GET /drivers, POST /drivers/upload, /drivers/{name}/delete — קונסולה בלבד"],
  ["רשת, פורטים, DHCP, SSH, בריאות, עדכון", ["ok", "כן"], ["", "לא"], "/net/config, PUT /net/interfaces/{n}, /ssh, /health, /update — admin_only"],
  ["הרשאות, הגדרות, יומן, לוגו", ["ok", "כן"], ["", "לא"], "/users, /settings, /journal, POST/DELETE /branding/logo — admin_only"],
  ["סניפים (שרתים משניים)", ["ok", "כן"], ["", "לא"], "/storage-nodes… — require_standalone (admin_only)"],
];

function findUser(name) {
  try { name = decodeURIComponent(name); } catch (e) {}
  return (USERS || []).find((u) => u.username === name) || null;
}
function activeAdminCount() { return (USERS || []).filter((u) => u.role === "admin" && !u.disabled).length; }

function userMfaLabel(u) {
  if (u.is_builtin) return "מקומי";
  if (u.mfa_enabled) return "✓";
  return "—";
}

function userRowHtml(u) {
  const enc = encodeId(u.username), self = !!ME && u.username === ME.username;
  const lastAdmin = u.role === "admin" && !u.disabled && activeAdminCount() <= 1;
  const btn = (label, onclick, cls = "") => `<button class="btn sm${cls ? " " + cls : ""}" onclick="${onclick}">${esc(label)}</button>`;
  let acts = btn("סיסמה", `userPasswordSheet('${enc}')`);
  if (!self && !lastAdmin) {
    acts += btn("תפקיד", `userRoleSheet('${enc}')`)
      + btn(u.disabled ? "הפעל" : "השבת", `userDisable('${enc}', ${u.disabled ? "false" : "true"})`)
      + btn("מחיקה", `userDeleteSheet('${enc}')`, "danger");
  }
  if (!self && !u.is_builtin && u.mfa_enabled)
    acts += btn("נתק MFA", `userMfaDisable('${enc}')`);
  if (!self)
    acts += btn("נתק את כל ההפעלות", `userRevokeSessions('${enc}')`);
  const note = self ? "זה אתה" : lastAdmin ? "המנהל הפעיל האחרון — לא ניתן למחיקה, להורדה או להשבתה" : (u.is_builtin ? "מקומי · ללא MFA" : "");
  return { attrs: `data-user="${esc(u.username)}"`, cells: [
    UI.name(u.username, note),
    u.role === "admin" ? UI.pill("info", "מנהל") : UI.pill("", "הפצה"),
    u.disabled ? UI.status("warn", "מושבת") : UI.status("ok", "פעיל"),
    esc(userMfaLabel(u)),
    `<span class="mono">${esc(fmtDate(u.created_at))}</span>`,
    `<div class="acts">${acts}</div>`] };
}

function permissions() {
  if (!USERS && !usersError) return pagePlaceholder();
  const list = USERS || [];
  const admins = activeAdminCount(), deployers = list.filter((u) => u.role === "deploy").length;
  const sub = USERS
    ? [`${list.length} משתמשים`, `${admins} מנהלים פעילים`, `${deployers} מפעילי הפצה`, "המנהל האחרון אינו ניתן למחיקה או להורדה"].map(esc).join(" · ")
    : `‏/users לא נקרא: ${esc(usersError)}`;
  const header = UI.objHeader({ crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "ניהול" }, { label: "הרשאות" }],
    icon: "user", name: "הרשאות", sub, pill: USERS ? "" : UI.pill("err", "לא נקרא"),
    actions: `<button class="btn primary" onclick="openNewUser()">+ משתמש</button>` });
  const table = !USERS
    ? UI.note("err", `לא הצלחתי לקרוא את המשתמשים: ${esc(usersError)}`)
    : UI.datagrid({ cls: "acts-on", columns: ["משתמש", "תפקיד", "מצב", "MFA", "נוצר", ""], rows: list.map(userRowHtml), empty: "אין משתמשים — השרת החזיר רשימה ריקה" });
  const usersCard = UI.card({ title: "משתמשים", small: "כניסה אחרונה ומאיפה — דורש API (אין ב-/users)", cls: "c8", body: table, flush: !!USERS && list.length > 0 });
  const matrix = UI.datagrid({ columns: ["", "מנהל", "הפצה"],
    rows: ROLE_MATRIX.map(([area, a, d, src]) => [`<span title="${esc(src)}">${esc(area)}</span>`, UI.status(a[0], a[1]), UI.status(d[0], d[1])]) })
    + `<div class="cap" style="padding:10px 14px">אין הרשאה שקטה: מה שאינו "כן" מחזיר 403 בשרת, לא רק מוסתר בממשק. למשתמש הפצה אין כניסה לקונסולה (#1073) — הוא עובד ממחשב הבנייה: שיכפול מאימג' בשרת, שיכפול ישיר מהדיסק, ושחזור לדיסק שלו. ריחוף על אזור מציג את ה-endpoint.</div>`;
  const matrixCard = UI.card({ title: "מה כל תפקיד רואה", small: "נגזר מהקוד — Depends של כל endpoint", cls: "c4", body: matrix, flush: true });
  return `<div class="page">${header}<div class="body">${usersCard}${matrixCard}</div></div>`;
}

function userPasswordSheet(name) {
  const u = findUser(name); if (!u || !isAdmin()) return;
  sheet({ title: "שינוי סיסמה", sub: u.username,
    fields: [{ id: "password", label: "סיסמה חדשה (8 תווים לפחות)", type: "password", confirm: "אימות הסיסמה החדשה" }],
    onSubmit: async (v) => { await put(`/users/${encodeId(u.username)}`, { password: v.password }); toast("הסיסמה שונתה — נרשם ביומן"); await loadUsersData(); } });
}
function userRoleSheet(name) {
  const u = findUser(name); if (!u || !isAdmin()) return;
  sheet({ title: "שינוי תפקיד", sub: u.username,
    fields: [{ id: "role", label: "תפקיד", type: "select", value: u.role,
      options: [{ value: "deploy", label: "הפצה בלבד" }, { value: "admin", label: "מנהל" }] }],
    onSubmit: async (v) => { await put(`/users/${encodeId(u.username)}`, { role: v.role }); toast("התפקיד שונה — נרשם ביומן"); await loadUsersData(); } });
}
/* חסימה אינה מחיקה: הפיכה, ושורות היומן נשארות מצביעות על מישהו. */
function userDisable(name, disable) {
  const u = findUser(name); if (!u || !isAdmin()) return;
  confirmSheet(disable ? "השבתת משתמש" : "הפעלת משתמש",
    disable ? `${u.username} לא יוכל להיכנס, וסשן פתוח שלו נסגר מיד.` : `${u.username} יוכל להיכנס שוב.`,
    disable ? "השבת" : "הפעל",
    async () => { await put(`/users/${encodeId(u.username)}`, { disabled: !!disable }); toast(disable ? "המשתמש הושבת" : "המשתמש הופעל"); await loadUsersData(); });
}
function userDeleteSheet(name) {
  const u = findUser(name); if (!u || !isAdmin()) return;
  sheet({ title: "מחיקת משתמש", sub: `${u.username} יאבד גישה מיידית. שורות היומן שלו נשארות.`,
    danger: true, submitLabel: "מחק",
    verify: { label: "להמשך יש להקליד את שם המשתמש:", mustEqual: u.username },
    onSubmit: async () => { await del(`/users/${encodeId(u.username)}`); toast(`המשתמש ${u.username} נמחק`); await loadUsersData(); } });
}
function userMfaDisable(name) {
  const u = findUser(name); if (!u || !isAdmin()) return;
  confirmSheet("נתק MFA", `${u.username} יידרש להגדיר אימות דו-שלבי מחדש בכניסה הבאה.`,
    "נתק MFA",
    async () => { await post(`/users/${encodeId(u.username)}/mfa/disable`); toast("MFA נוטרל"); await loadUsersData(); });
}
function userRevokeSessions(name) {
  const u = findUser(name); if (!u || !isAdmin()) return;
  confirmSheet("נתק את כל ההפעלות", `כל הסשנים והדפדפנים הזכורים של ${u.username} יבוטלו מיד.`,
    "נתק",
    async () => { await post("/sessions/revoke", { username: u.username }); toast("ההפעלות נותקו"); await loadUsersData(); });
}

/* ---------- #954 גל 6: יומן ----------
   נבנה לפי docs/design/console-redesign/logs.md: יומן אחד (לא "אירועים/Audit"),
   שורת סינון בדף (q · סוג · משתמש · טווח) — לא במודאל — מול
   ‏/journal?q&event&user&from&to&limit; "עוד" מגדיל limit (השרת: עד 1,000;
   אין offset — דורש API). חומרה = severity מהשרת (#968; ok/info/warn/err);
   "יעד" כעמודה מבנית — דורש API, ולכן text מוצג מתחת למשפט. */
let LOG = { q: "", event: "", user: "", range: "", since: "", until: "", limit: 200,
            rows: null, err: "", truncated: false, events: null };
const LOG_RANGES = [["", "כל הזמן"], ["today", "היום"], ["7d", "7 ימים"], ["30d", "30 יום"], ["custom", "טווח…"]];
const LOG_SEV_HE = { ok: "הושלם / אושר", info: "מידע", warn: "דורש תשומת לב", err: "כשל / סירוב" };

/* חותמות השרת הן UTC ISO (now_iso) — הטווח מחושב באותו ציר. */
function logSince(range) {
  if (range === "today") return new Date().toISOString().slice(0, 10);
  const days = range === "7d" ? 7 : range === "30d" ? 30 : 0;
  return days ? new Date(Date.now() - days * 86400000).toISOString().slice(0, 19) : "";
}
function logQuery() {
  const p = new URLSearchParams();
  if (LOG.q) p.set("q", LOG.q);
  if (LOG.event) p.set("event", LOG.event);
  if (LOG.user) p.set("user", LOG.user);
  const since = LOG.range === "custom" ? LOG.since : logSince(LOG.range);
  if (since) p.set("from", since);
  // "עד" הוא דקה שלמה (datetime-local): בלי :59, "10:00" כמחרוזת פוסל 10:00:15.
  if (LOG.range === "custom" && LOG.until) p.set("to", LOG.until + ":59");
  p.set("limit", String(LOG.limit));
  return "?" + p.toString();
}
async function loadJournalData() {
  try {
    if (!LOG.events) LOG.events = await api("/journal/events");
  } catch (e) { LOG.events = null; }   // הסינון לפי סוג לא זמין — הרשימה עצמה עדיין נקראת
  try {
    // fetch ישיר ולא api(): הכותרת X-Journal-Search-Truncated — "לא בדקנו
    // הכל" אינו "אין תוצאות" (עיקרון 5).
    const response = await fetch("/api/console/journal" + logQuery(), { credentials: "same-origin" });
    if (response.status === 401) { showLogin(); return; }
    if (!response.ok) {
      let detail = "שגיאה " + response.status;
      try { detail = (await response.json()).detail || detail; } catch (e) {}
      throw new Error(detail);
    }
    LOG.truncated = response.headers.get("X-Journal-Search-Truncated") === "true";
    LOG.rows = await response.json();
    JOURNAL = LOG.rows;
    LOG.err = "";
  } catch (e) {
    LOG.rows = null; LOG.err = e.message;
    toast("טעינת היומן נכשלה: " + e.message);
  }
  if (current === "logs") renderCurrent();
}
function logFilter(key, value) {
  LOG[key] = value;
  if (key !== "limit") LOG.limit = 200;
  if (key === "range" && value === "custom" && !LOG.since && !LOG.until) { renderCurrent(); return; }   // קודם התאריכים
  loadJournalData();
}
function logMore() { LOG.limit = Math.min(1000, LOG.limit + 200); loadJournalData(); }
function logEventLabel(ev) { const e = (LOG.events || []).find((x) => x.event === ev); return e ? e.label : ev; }

function logRowHtml(r, i) {
  const cls = journalCls(r);
  const t = isToday(r.ts) ? fmtHour(r.ts) : `${fmtDate(r.ts)} ${fmtHour(r.ts)}`;
  return { attrs: `onclick="openLogDetail(${i})" tabindex="0"`, cells: [
    `<span class="mono">${esc(t)}</span>`,
    `<span class="st ${cls}" title="${esc(LOG_SEV_HE[cls] || "")}" aria-label="${esc(LOG_SEV_HE[cls] || "")}"></span>`,
    `<span class="name">${esc(r.label || r.event || "")}</span>${r.text ? `<span class="sub">${esc(r.text)}</span>` : ""}`,
    esc(r.user || "המערכת"),
    `<div class="acts"><button class="btn sm" onclick="event.stopPropagation();openLogDetail(${i})">פרטים</button></div>`] };
}
function logBarHtml(rows) {
  const users = new Set((USERS || []).map((u) => u.username));
  for (const r of rows) if (r.user) users.add(r.user);
  if (LOG.user) users.add(LOG.user);
  const opt = (v, label, cur) => `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(label)}</option>`;
  const events = LOG.events
    ? `<select aria-label="סוג אירוע" onchange="logFilter('event', this.value)">${opt("", "כל סוגי האירועים", LOG.event)}${LOG.events.map((e) => opt(e.event, e.label, LOG.event)).join("")}</select>`
    : `<span class="cap" title="‏/journal/events לא נקרא">סוג: לא נקרא</span>`;
  const custom = LOG.range === "custom"
    ? `<input type="datetime-local" aria-label="מתאריך" value="${esc(LOG.since)}" onchange="logFilter('since', this.value)"><input type="datetime-local" aria-label="עד תאריך" value="${esc(LOG.until)}" onchange="logFilter('until', this.value)">`
    : "";
  return `<div class="dg-bar"><input type="search" value="${esc(LOG.q)}" placeholder="חיפוש בטקסט (מחשב, כיתה, אימג')…" aria-label="חיפוש ביומן" onchange="logFilter('q', this.value.trim())" style="width:240px">${events}<select aria-label="משתמש" onchange="logFilter('user', this.value)">${opt("", "כל המשתמשים", LOG.user)}${[...users].sort().map((u) => opt(u, u, LOG.user)).join("")}</select><select aria-label="טווח זמן" onchange="logFilter('range', this.value)">${LOG_RANGES.map(([v, l]) => opt(v, l, LOG.range)).join("")}</select>${custom}<span class="sp"></span><span class="n">${rows.length} אירועים</span></div>`;
}

function logs() {
  if (!LOG.rows && !LOG.err) return pagePlaceholder();
  const rows = LOG.rows || [];
  const filters = [LOG.q ? `"${LOG.q}"` : "", LOG.event ? logEventLabel(LOG.event) : "", LOG.user,
    LOG.range ? (LOG.range === "custom" ? `${LOG.since || "…"} – ${LOG.until || "…"}` : LOG_RANGES.find(([v]) => v === LOG.range)[1]) : ""].filter(Boolean);
  const sub = LOG.rows
    ? [`${rows.length} אירועים מוצגים (מגבלה ${LOG.limit})`, filters.length ? "מסונן: " + filters.join(" · ") : "בלי סינון — האחרונים"].map(esc).join(" · ")
    : `‏/journal לא נקרא: ${esc(LOG.err)}`;
  const pill = !LOG.rows ? UI.pill("err", "לא נקרא") : LOG.truncated ? UI.pill("warn", "חיפוש חלקי") : "";
  const header = UI.objHeader({ crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "ניהול" }, { label: "יומן" }],
    icon: "list", name: "יומן", sub, pill,
    actions: `<button class="btn" onclick="loadJournalData()">${uiIcon("refresh")} רענון</button>${UI.soon("ייצוא CSV")}` });
  let body;
  if (!LOG.rows) body = UI.note("err", `לא הצלחתי לקרוא את היומן: ${esc(LOG.err)}`);
  else {
    const truncated = LOG.truncated ? UI.note("warn", "החיפוש כיסה רק את השורות האחרונות ביומן — צמצמו עם טווח תאריכים או סוג אירוע.") : "";
    const table = UI.datagrid({ cls: "acts-on", columns: ["זמן", "", "מה קרה", "מי", ""], rows: rows.map(logRowHtml),
      empty: filters.length ? "אין אירועים שתואמים לסינון" : "היומן ריק — עדיין לא נרשם אירוע" });
    const more = rows.length >= LOG.limit && LOG.limit < 1000
      ? `<button class="btn sm" onclick="logMore()">עוד (${LOG.limit + 200 > 1000 ? 1000 : LOG.limit + 200})</button>`
      : `<span class="cap">${rows.length >= 1000 ? "מגבלת השרת: 1,000 שורות — צמצמו את הסינון" : "זה הכול לסינון הזה"}</span>`;
    body = `<div class="c12 card">${logBarHtml(rows)}${truncated ? `<div style="padding:10px 12px 0">${truncated}</div>` : ""}<div class="card-b flush">${table}</div><div class="dg-bar foot"><span class="n">מוצגים ${rows.length}</span><span class="sp"></span>${more}</div></div>`;
  }
  const cap = `<div class="c12 cap">לחיצה על שורה פותחת את הפרטים הטכניים (event, המזהים). הצבע = חומרת האירוע כפי שהשרת קבע (severity ב-/journal). ייצוא CSV ועמודת "יעד" מבנית — דורש API.</div>`;
  return `<div class="page">${header}<div class="body">${body}${cap}</div></div>`;
}
function openLogDetail(i) {
  const row = (LOG.rows || [])[i];
  if (!row) { toast("אירוע לא נמצא"); return; }
  const cls = journalCls(row);
  openDrawer("פרטי אירוע", `<div class="page drw">${UI.kv([
    ["זמן", `<span class="mono">${esc(fmtDate(row.ts))} ${esc(fmtHour(row.ts))}</span>`],
    ["חומרה", UI.status(cls, LOG_SEV_HE[cls] || cls)],
    ["מה קרה", esc(row.label || "")],
    ["פירוט", esc(row.text || "") || `<span class="muted">—</span>`],
    ["event", `<span class="mono">${esc(row.event || "")}</span>`],
    ["מי", esc(row.user || "המערכת")]])}</div>`);
}

/* ---------- #954 גל 8: רשת כאובייקט — תרשים · חיבורים פיזיים · רשת הפצה · פורטים ----------
   docs/design/console-redesign/network.md, network-nics.md, network-deploy.md.
   "רשת" בעץ הוא דף (הכרעת נדב על העץ: קליק = הדף, דאבל = פותח/סוגר), והצמתים
   "חיבורים פיזיים / רשת הפצה" פותחים את אותו אובייקט בלשונית. "פורטים" =
   הדף הקיים של גל 5 (pages.ports) — לא נגענו, רק מקושר כלשונית.
   מקורות (docs/interfaces.md, לא ממציאים שדות): /net/interfaces (net.js:
   loadNet → NICS), /net/config (NETCFG), /net (NET — מי נראה ברשת), /ports
   (bind — "מה מותר על כל וילן" עד מודל וילן, #705), /ssh (SSH_STATE — SSH
   לשרת × כרטיס), /monitor/machines (משכפלים/בנייה, online), /storage-nodes
   (+ …/machines.connected — סניפים), /net/interfaces/{n}/probe (מי עוד עונה).
   כל מקור נכשל בנפרד (NETW.err) ומוצג "לא נקרא" — לא "אין" (עיקרון 5).
   וילן = הכרטיס (1:1, "לפי הגדרה") — מודל וילן אמיתי דורש API (#705).
   כיתות = תיבה סטטית "v2" — לא נבנה מעבר לזה (v1 = בנייה/שיכפול/שרתים).
   כל שינוי DHCP/כתובת עובר בטפסים הקיימים של net.js/netcfg.js (editNic,
   editAddress, הקלדת שם הכרטיס, rollback) — לא נבנתה זרימת שמירה חדשה (#53). */
let NETW = { nics: null, cfg: null, ports: null, mon: null, nodes: null, probe: {}, sel: null, at: "", err: {} };
// ‏#1088: מקור כתובת ההפצה — /net/deploy: {configured, source, interface, url, hint}. null = לא נקרא.
let NET_DEPLOY = null;
const NET_ONLINE_SECONDS = 90;   // כמו monitor.py: "מחובר" = נראה ב-90 השניות האחרונות

async function loadNetwork() {
  if (!isAdmin()) return;
  const err = {};
  const grab = async (key, fn) => { try { return await fn(); } catch (e) { err[key] = e.message; return null; } };
  const [nics, cfg, net, ports, ssh, mon, nodes, deploy] = await Promise.all([
    grab("interfaces", async () => { await loadNet(); return Array.isArray(NICS) ? NICS : null; }),
    grab("config", () => api("/net/config")),
    grab("net", () => api("/net")),
    grab("ports", () => api("/ports")),
    grab("ssh", () => api("/ssh")),
    grab("monitor", () => api("/monitor/machines")),
    grab("nodes", loadNetworkNodes),
    grab("deploy", () => api("/net/deploy")),
  ]);
  NETW = { ...NETW, nics, cfg, ports: Array.isArray(ports) ? ports : null, mon: Array.isArray(mon) ? mon : null,
           nodes, at: clockNow(), err };
  NET_DEPLOY = deploy && typeof deploy === "object" ? deploy : null;
  if (cfg) NETCFG = cfg;                                   // netcfg.js (editAddress, נתיבים, rollback) קורא מכאן
  if (Array.isArray(net)) { NET = net; NET_ERR = ""; } else { NET = null; NET_ERR = err.net || "תשובה שאינה רשימה"; }
  SSH_STATE = ssh; sshError = err.ssh || "";               // portSshNic/sshToggle (גל 5) קוראים מכאן
  PORTS_NICS = nics; portsNicsError = err.interfaces || "";
  if (!NETW.sel || !(nics || []).some((n) => n.name === NETW.sel)) NETW.sel = (netDeployNic() || (nics || [])[0] || {}).name || null;
  populateSidebarNics();
  if (current === "network") renderCurrent();
}

/* סניפים: /storage-nodes (403/409 = אין סניפים, לא שגיאה — כמו העץ) + מצב חיבור נמדד לכל אחד. */
async function loadNetworkNodes() {
  if (!(ME && ME.capabilities && ME.capabilities.enroll_secondary)) return [];
  let nodes;
  try { nodes = await api("/storage-nodes"); }
  catch (e) { if (e.status === 403 || e.status === 409) return []; throw e; }
  return Promise.all(nodes.map(async (n) => {
    if (n.disabled_at) return { ...n, connected: null, error: "" };
    try { const a = await api(`/storage-nodes/${encodeId(n.id)}/machines`); return { ...n, connected: !!a.connected, error: a.error || "" }; }
    catch (e) { return { ...n, connected: false, error: e.message }; }
  }));
}

/* --- עזרי נתונים (טהורים — נבדקים ב-node) --- */
function netCfgRow(name) { return ((NETCFG && NETCFG.interfaces) || []).find((r) => r.name === name) || null; }
function netLiveChecked() { return !!(NETCFG && NETCFG.live && NETCFG.live.checked); }
function netLiveAddrs(n) { const cfg = netCfgRow(n.name); return ((cfg && netLiveChecked()) ? cfg.live_addresses : n.addresses) || []; }
function ipInt(ip) { const m = String(ip || "").match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/); return m ? (((+m[1] << 24) | (+m[2] << 16) | (+m[3] << 8) | +m[4]) >>> 0) : null; }
function cidrParts(cidr) {
  const m = String(cidr || "").match(/^(\d+\.\d+\.\d+\.\d+)\/(\d+)$/);
  if (!m) return null;
  const bits = Math.min(32, +m[2]), mask = bits ? (0xffffffff << (32 - bits)) >>> 0 : 0;
  return { net: (ipInt(m[1]) & mask) >>> 0, mask, bits };
}
function nicHasIp(n, ip) { const x = ipInt(ip); return x !== null && netLiveAddrs(n).some((c) => { const p = cidrParts(c); return !!p && ((x & p.mask) >>> 0) === p.net; }); }
function netNicFor(ip) { return (NETW.nics || []).find((n) => nicHasIp(n, ip)) || null; }
function netDeployNic() {
  const l = (NETW.nics || []).filter((n) => classroomsOn() || !n.proxy);
  return l.find((n) => n.enabled) || l.find((n) => (n.dhcp_live || {}).state === "serving") || null;
}
function netNetworkOf(n) { const c = netLiveAddrs(n)[0]; const p = cidrParts(c); if (!p) return c || ""; return `${[24, 16, 8, 0].map((s) => (p.net >>> s) & 255).join(".")}/${p.bits}`; }
function urlHost(u) { const m = String(u || "").match(/^[a-z]+:\/\/\[?([^\]/:]+)/i); return m ? m[1] : ""; }
function secondsSince(iso) { const t = iso ? new Date(iso).getTime() : NaN; return Number.isFinite(t) ? Math.max(0, (Date.now() - t) / 1000) : null; }

/* "מה מותר על כל וילן" — רק מה ש-/ports.bind אומר (#996); בלי bind בכלל = דורש API (#705). null = לא ידוע. */
function netServicesOn(n) {
  const ports = NETW.ports;
  // ‏bind בשרת: רשימת "כתובת[/bits][:port]" (ריקה = לא נקרא / לא מאזין), או מחרוזת. בלי אף bind — לא ידוע.
  const bindsOf = (p) => (Array.isArray(p.bind) ? p.bind : p.bind ? [p.bind] : []).map((b) => String(b).replace(/:\d+$/, "").replace(/\/\d+$/, ""));
  if (!Array.isArray(ports) || !ports.some((p) => bindsOf(p).length)) return null;
  const ips = netLiveAddrs(n).map((c) => c.split("/")[0]);
  const out = [];
  for (const p of ports) {
    const binds = bindsOf(p);
    if (binds.some((b) => b === "0.0.0.0" || b === "::" || b === "[::]" || b === "*" || ips.includes(b))) out.push(`${p.name} ${p.port}`);
  }
  return out;
}
/* סוג הוילן "לפי הגדרה" (1:1 עם הכרטיס; מודל וילן = #705): הפצה / proxy / רשת המכללה / שרתים / לא מוגדר.
   מודל האתרים (נדב 18/09): לכל אתר שלושה וילנים — שרתים / הפצה / כיתות. אין וילן "ניהול" נפרד: הקונסולה, הערוץ
   הבין-שרתי (8443) ו-DHCP המשרדים כולם על וילן השרתים; כרטיס שנושא רק 8443 הוא עדיין וילן השרתים. */
function netVlanOf(n) {
  const svc = netServicesOn(n) || [];
  if (n.enabled) return { kind: "deploy", label: "וילן ההפצה" };
  if (n.proxy) return { kind: "proxy", label: "PXE proxy — DHCP של רשת אחרת" };
  if (n.trunk) return { kind: "trunk", label: "רשת המכללה (trunk)" };
  if (svc.some((s) => /8081/.test(s))) return { kind: "mgmt", label: "שרתים — קונסולה" };
  if (svc.some((s) => /8443/.test(s))) return { kind: "inter", label: "שרתים — בין-שרתי" };
  return { kind: "none", label: "לא מוגדר" };
}
function netRoleLabel(n) {
  const parts = [];
  if (n.enabled) parts.push("הפצה · DHCP");
  if (n.proxy) parts.push("PXE proxy");
  if (n.trunk) parts.push("רשת המכללה");
  const svc = netServicesOn(n);
  if (svc && svc.length) parts.push(svc.join(" · "));
  if (parts.length) return esc(parts.join(" · "));
  return svc === null ? `<span class="muted" title="דורש API (#705): מודל וילן / bind">— דורש API</span>` : `<span class="muted">—</span>`;
}
function netLinkStatus(n) {
  if (!n.present || n.state === "missing") return UI.status("", "לא קיים במערכת");
  if (n.state === "up") return UI.status("ok", `מחובר${typeof n.speed_mbps === "number" && n.speed_mbps > 0 ? ` · ${n.speed_mbps} Mbps` : ""}`);
  if (n.state === "down") return UI.status("", "מנותק");
  return UI.status("unk", "לא נקרא");
}
/* פער = מצב, לא הערה: לא נקרא (unk) ≠ לא מנוהל (אפור) ≠ תואם (ירוק) ≠ לא תואם (אדום). */
function netGapStatus(n) {
  if (!NETCFG) return UI.status("unk", "לא נקרא");
  const cfg = netCfgRow(n.name);
  if (!netLiveChecked()) return UI.status("unk", "לא נקרא");
  if (!cfg || cfg.mode === "manual") return UI.status("", "לא מנוהל");
  return (cfg.mismatches || []).length ? UI.status("err", cfg.mismatches.join(" · ")) : UI.status("ok", "תואם");
}
function netConfiguredText(n) {
  if (!NETCFG) return "לא נקרא";
  const cfg = netCfgRow(n.name);
  if (!cfg || cfg.mode === "manual") return "לא מנוהל";
  if (cfg.mode === "dhcp") return "DHCP";
  const bits = typeof maskBits === "function" && cfg.netmask ? `/${maskBits(cfg.netmask)}` : "";
  return `static ${cfg.address || "—"}${bits}${cfg.gateway ? ` · gw ${cfg.gateway}` : ""}`;   // mono LTR — בלי מילה עברית שתהפוך את הסדר
}
function netDhcpCell(n, withRange = true) {
  const live = n.dhcp_live || { state: "unknown" };
  const cls = dhcpLiveClass(live.state);
  const st = UI.status(cls === "ok" ? "ok" : cls === "warn" ? (live.state === "unknown" ? "unk" : "warn") : "", n.dhcp_live_label || "לא ידוע");
  const stored = `שמור: ${n.enabled ? "מופעל" : n.proxy ? "proxy" : "כבוי"}`;
  const range = withRange && n.enabled && n.range_start ? ` · ${n.range_start}–${n.range_end}` : "";
  const div = n.dhcp_diverged ? ` ${UI.pill("warn", "לא תואם למצב הפעיל")}` : "";
  return `${st}<span class="sub">${esc(stored)}${range ? ` · ${ltr(range.slice(3))}` : ""}${div}</span>`;   // הטווח LTR מבודד — אחרת "200–100"
}
/* SSH לשרת לכרטיס — שלושה מצבים נמדדים (כמו גל 5) + מתג לאותו endpoint (portSshNic). */
function netSshOf(name) {
  if (!SSH_STATE) return { on: null, cls: "unk", text: `לא נקרא${sshError ? `: ${sshError}` : ""}` };
  const s = (SSH_STATE.interfaces || []).find((i) => i.name === name);
  if (!s) return { on: null, cls: "unk", text: "לא ברשימת /ssh" };
  if (s.listening === null || s.listening === undefined) return { on: null, cls: "unk", text: s.enabled ? "נשמר פתוח · לא נקרא" : "סגור · לא נקרא" };
  if (s.enabled && s.listening) return { on: true, cls: "ok", text: "פתוח · מאזין" };
  if (s.enabled) return { on: true, cls: "err", text: "נשמר פתוח — לא מאזין" };
  if (s.listening) return { on: false, cls: "warn", text: "מאזין למרות שסגור" };
  return { on: false, cls: "", text: "סגור" };
}
function netSshSwitch(name) {
  const s = netSshOf(name);
  PORT_ACTIONS.set(`ssh_nic:${name}`, () => SSH_STATE ? portSshNic(name) : toast(`‏/ssh לא נקרא${sshError ? `: ${sshError}` : ""}`));
  const open = SSH_STATE ? (SSH_STATE.interfaces || []).filter((i) => i.enabled).length : 0;
  const lock = s.on === null ? false : (!s.on || open <= 1);   // פתיחה = הקלדת שם הכרטיס; סגירת הדלת האחרונה = הקלדה (גל 5)
  return portSwitchHtml(`ssh_nic:${name}`, s.on, lock, s.text, `SSH לשרת ${name}`);
}
function netProbeStatus(name) {
  const p = NETW.probe[name];
  if (!p) return { cls: "", text: "לא נבדק" };
  if (p.running) return { cls: "run", text: "בודק…" };
  if (p.error) return { cls: "unk", text: `הבדיקה נכשלה (${p.at}): ${p.error}` };
  if (!p.checked) return { cls: "unk", text: `הבדיקה לא רצה (${p.at}) — לא ידוע מי עונה` };
  return p.servers.length ? { cls: "err", text: `עונים: ${p.servers.join(", ")} (נבדק ${p.at})` } : { cls: "ok", text: `אף שרת אחר לא עונה (נבדק ${p.at})` };
}
async function netProbe(nameEnc) {
  let name = nameEnc; try { name = decodeURIComponent(nameEnc); } catch (e) {}
  NETW.probe[name] = { running: true };
  if (current === "network") renderCurrent();
  try {
    const r = await api(`/net/interfaces/${encodeId(name)}/probe`);
    NETW.probe[name] = { checked: r.checked === true, servers: Array.isArray(r.servers) ? r.servers : [], at: clockNow() };
  } catch (e) { NETW.probe[name] = { error: e.message, at: clockNow() }; toast("בדיקת DHCP נכשלה: " + e.message); }
  if (current === "network") renderCurrent();
}
function netSelect(nameEnc) { let name = nameEnc; try { name = decodeURIComponent(nameEnc); } catch (e) {} NETW.sel = name; populateSidebarNics(); if (current === "network") renderCurrent(); }
function netNic(nameEnc) { let name = nameEnc; try { name = decodeURIComponent(nameEnc); } catch (e) {} return (NETW.nics || []).find((n) => n.name === name) || null; }
function netEditAddress(nameEnc) {
  const n = netNic(nameEnc); if (!n) return;
  editAddress(netCfgRow(n.name) || bodyOf({ name: n.name, mode: "manual", address: "", netmask: MASKS[0], gateway: "", dns: [], routes: [] }, {}));
}
function netEditDhcp(nameEnc) { const n = netNic(nameEnc); if (n) editNic(n); else toast("כרטיס לא נמצא"); }
function netShowFile(nameEnc) {
  const n = netNic(nameEnc); if (!n) return;
  showFile(netCfgRow(n.name) || { name: n.name, mode: "manual", address: "", netmask: MASKS[0], gateway: "", dns: [], routes: [] }).catch((e) => toast(e.message));
}
function netDescribe(nameEnc) {
  const n = netNic(nameEnc); if (!n) return;
  sheet({ title: "תיאור הכרטיס", sub: n.name, fields: [{ id: "description", label: "תיאור חופשי", value: n.description, placeholder: "למשל: וילן 700" }],
    onSubmit: async (v) => { await put(`/net/interfaces/${encodeId(n.name)}/description`, { description: v.description }); await loadNetwork(); } });
}
function netForget(nameEnc) {
  const n = netNic(nameEnc); if (!n) return;
  confirmSheet("הסרת הגדרות הכרטיס", `ההגדרות והתיאור של ${n.name} יימחקו. אם רץ עליו DHCP — הוא ייכבה.`, "הסר",
    async () => { await del(`/net/interfaces/${encodeId(n.name)}`); await loadNetwork(); });
}
function netRegister(macEnc) { let mac = macEnc; try { mac = decodeURIComponent(macEnc); } catch (e) {} openAddMachine({ mac }); }

/* --- מודל התרשים (טהור): מסלולים = כרטיס → וילן (לפי הגדרה) → מי מחובר --- */
/* רשימת כתובות בתיבה: עד שתיים ו-"+N" — הרשימה המלאה ב-tip (title). תיבה ברוחב קבוע, והטקסט חייב להישאר בתוכה (18/09).
   כל כתובת מבודדת לבדה (bidi): " · " אינו ASCII, ורצף של שתי כתובות סביבו היה מתהפך בשורה עברית. */
function netAddrList(ips) {
  if (!ips.length) return "ללא כתובת";
  return ips.slice(0, 2).map(bidi).join(" · ") + (ips.length > 2 ? ` · ${bidi(`+${ips.length - 2}`)}` : "");
}
/* שורת "מותר" קצרה: שירותים באותו שם מתקפלים לפורט אחד — "HTTP 8080/8081". הרשימה הגולמית (netServicesOn) נשארת בכרטיס הנבחר. */
function netAllowedShort(list) {
  const byName = new Map();
  for (const item of list) {
    const i = item.lastIndexOf(" "), name = i > 0 ? item.slice(0, i) : item, port = i > 0 ? item.slice(i + 1) : "";
    if (!byName.has(name)) byName.set(name, []);
    if (port) byName.get(name).push(port);
  }
  return [...byName].map(([name, ports]) => (ports.length ? `${name} ${ports.join("/")}` : name)).join(" · ");
}
function netDiagramModel() {
  const nics = NETW.nics || [];
  const lanes = nics.map((n) => {
    const led = !n.present ? "off" : n.state === "up" ? "ok" : n.state === "down" ? "off" : "warn";
    return { nic: n, vlan: netVlanOf(n), allowed: netServicesOn(n), led, clients: [] };
  });
  const orphans = [];
  const laneOf = (kinds) => kinds.map((k) => lanes.find((l) => l.vlan.kind === k)).find(Boolean) || null;
  const place = (client, ip, fallback) => {
    const nic = ip ? netNicFor(ip) : null;
    const lane = (nic && lanes.find((l) => l.nic === nic)) || (fallback ? fallback() : null);
    if (lane) lane.clients.push(client); else orphans.push(client);
  };
  // משכפלים ובנייה — /monitor/machines (online נקבע בשרת); בלי כתובת → וילן ההפצה (שם הם יופיעו כשיעלו)
  const roles = [["cloner", "משכפלים"], ["build", "מחשבי בנייה"]];
  for (const [role, title] of roles) {
    const list = (NETW.mon || []).filter((m) => m.role === role);
    const byLane = new Map();
    for (const m of list) {
      const nic = m.ip ? netNicFor(m.ip) : null;
      const lane = (nic && lanes.find((l) => l.nic === nic)) || laneOf(["deploy"]) || null;
      if (!lane) { orphans.push({ title: `${title} — ${m.name || m.mac}`, sub: m.ip || "ללא כתובת", led: "", dashed: true }); continue; }
      if (!byLane.has(lane)) byLane.set(lane, []);
      byLane.get(lane).push(m);
    }
    for (const [lane, ms] of byLane) {
      const on = ms.filter((m) => m.online).length;
      const ips = ms.map((m) => m.ip).filter(Boolean), tail = ` · ${on} מתוך ${ms.length} מחוברים`;
      lane.clients.push({ kind: role, title: `${title} — ${ms.map((m) => m.name || m.mac).join(", ")}`,
        sub: `${netAddrList(ips)}${tail}`, tip: `${ips.map(bidi).join(" · ") || "ללא כתובת"}${tail}`,
        led: on === ms.length ? "ok" : on ? "warn" : "", dashed: on === 0 });
    }
  }
  // הקונסולה — אנחנו (הדף הזה נטען = ראיה); מי עוד מחובר דורש API (sessions)
  place({ kind: "console", title: `קונסולה — ${ME ? ME.username : ""}`, sub: "session פעיל · מי עוד מחובר — דורש API", led: "ok" }, null,
    () => laneOf(["mgmt", "trunk", "inter", "none", "proxy", "deploy"]));
  // סניפים — /storage-nodes + connected נמדד. המשני יושב מאחורי FW משלו (interfaces.md §18, נדב 18/09): הראשי יוזם,
  // ורק 8443 פתוח ביניהם — ולכן הקו מקווקו (מעבר ל-FW, לא לקוח על הוילן), והתיבה נשארת לפי connected.
  for (const n of NETW.nodes || []) {
    const off = !!n.disabled_at;
    const state = off ? "מושבת" : n.connected ? "מחובר" : `לא ענה${n.error ? `: ${n.error}` : ""}`;
    place({ kind: "branch", title: `${n.label} — שרת משני`, led: off ? "" : n.connected ? "ok" : "err", dashed: off, viaFw: true,
      sub: `דרך FW · 8443 בלבד · ${bidi(n.base_url || "")} · ${state}`,
      tip: `המשני מאחורי FW משלו; הראשי יוזם, רק 8443 · ${bidi(n.base_url || "")} · ${state}` },
      urlHost(n.base_url), () => laneOf(["inter", "mgmt", "trunk", "none"]));
  }
  // לא רשומים — /net registered=false, לפי הכתובת שקיבלו
  const unreg = (NET || []).filter((d) => d.registered === false);
  const unregByLane = new Map();
  for (const d of unreg) {
    const nic = d.ip ? netNicFor(d.ip) : null;
    const lane = (nic && lanes.find((l) => l.nic === nic)) || laneOf(["deploy"]);
    if (!lane) { orphans.push({ title: `לא רשום — ${d.mac}`, sub: d.ip || "ללא כתובת", led: "warn" }); continue; }
    if (!unregByLane.has(lane)) unregByLane.set(lane, []);
    unregByLane.get(lane).push(d);
  }
  for (const [lane, ds] of unregByLane) {
    lane.unreg = ds.length;
    const last = ds[0];
    lane.clients.push({ kind: "unreg", led: "warn",
      title: ds.length === 1 ? `לא רשום — ${bidi(last.mac)}` : `לא רשומים — ${ds.length} (${bidi(ds.slice(0, 2).map((d) => d.mac).join(", "))}…)`,
      sub: `${netAddrList(ds.map((d) => d.ip).filter(Boolean))}${last.boot && last.boot.label ? ` · ${last.boot.label}` : ""} · ${ago(last.last_seen)}`,
      tip: `${ds.map((d) => d.ip).filter(Boolean).map(bidi).join(" · ") || "ללא כתובת"}${last.boot && last.boot.label ? ` · ${last.boot.label}` : ""} · ${ago(last.last_seen)}` });
  }
  // כיתות — v2: תיבה סטטית בלבד. #1081: v1 לא מצייר אותה.
  if (classroomsOn()) {
    const classLane = laneOf(["proxy", "trunk", "none", "mgmt"]);
    if (classLane) classLane.clients.push({ kind: "class", title: "כיתות — v2", sub: "מחשבי כיתה אינם במהדורה זו", led: "", dashed: true });
  }
  return { lanes, orphans };
}

/* --- ציור ה-SVG: ימין השרת → אמצע וילנים → שמאל מחוברים; RTL בתרשים = טקסט מיושר לימין (text-anchor=end) --- */
const ND = { W: 1100, srvX: 820, srvW: 250, nicX: 840, nicW: 210, nicH: 72, vlX: 470, vlW: 250, vlH: 100, clX: 60, clW: 330, clH: 46, gap: 8, top: 100 };
/* פסקה RTL: direction=rtl + text-anchor=start = הקצה הימני ב-x, ומונחים לטיניים (DHCP, SSH, ens19) נשארים במקומם במשפט העברי. */
/* רצף ASCII (כתובת, MAC, מהירות) בתוך משפט עברי מתהפך ב-RTL ("Mb/s 1000") — מבודדים אותו ב-LRI…PDI (כמו ltr() ב-HTML). */
function bidi(text) { const t = String(text); return /^[\x20-\x7e]+$/.test(t) ? `⁦${t}⁩` : t; }
function svgText(x, y, cls, text) { return `<text class="${cls}" x="${x}" y="${y}" direction="rtl" text-anchor="start">${esc(text)}</text>`; }
/* טקסט התיבות: <foreignObject> עם div — ‏<text> ב-SVG אינו נשבר ואינו נחתך, ובמעבדה (v0.41.1) שורות ארוכות רכבו על הקווים.
   כל שורה: nowrap + ellipsis (‏.w2 = עד שתי שורות), ו-title עם הטקסט המלא. הרוחב = רוחב התיבה פחות שוליים — הטסט בודק זאת סטטית. */
function svgLines(x, y, w, h, lines, cls = "") {
  const inner = lines.map((l) => `<div class="fl ${l.cls}" title="${esc(l.tip || l.text)}">${esc(l.text)}</div>`).join("");
  return `<foreignObject x="${x}" y="${y}" width="${w}" height="${h}"><div xmlns="http://www.w3.org/1999/xhtml" class="fo${cls ? " " + cls : ""}" dir="rtl">${inner}</div></foreignObject>`;
}
function netDiagramSvg(model) {
  const { lanes, orphans } = model;
  let y = ND.top;
  const parts = [];
  for (const lane of lanes) {
    const n = lane.nic, enc = encodeId(n.name), sel = NETW.sel === n.name;
    const bodyH = Math.max(ND.nicH, ND.vlH, lane.clients.length * (ND.clH + ND.gap) - ND.gap);
    const mid = y + bodyH / 2;
    const addr = bidi(netLiveAddrs(n).join(" · ") || "אין כתובת");
    const link = !n.present ? "לא קיים במערכת" : n.state === "up" ? bidi(n.speed_mbps ? n.speed_mbps + " Mb/s" : "מחובר") : n.state === "down" ? "אין קישור" : "קישור לא נקרא";
    const ssh = netSshOf(n.name).text;
    parts.push(`<g class="hit" role="button" tabindex="0" aria-label="${esc(n.name)}" onclick="netSelect('${enc}')" onkeydown="if(event.key==='Enter'||event.key===' ')netSelect('${enc}')">`
      + `<rect class="box${sel ? " sel" : ""}" x="${ND.nicX}" y="${mid - ND.nicH / 2}" width="${ND.nicW}" height="${ND.nicH}" rx="4"${lane.led === "off" ? ' stroke-dasharray="4 4"' : ""}/>`
      + svgLines(ND.nicX + 8, mid - ND.nicH / 2 + 6, ND.nicW - 18, ND.nicH - 12, [{ cls: "t", text: `${n.name} — ${n.description || "ללא תיאור"}` },
        { cls: "m", text: `${addr} · ${link}` }, { cls: "m", text: `DHCP: ${n.dhcp_live_label || "לא ידוע"} · SSH לשרת: ${ssh}` }], "nic")
      + `<circle class="led ${lane.led}" cx="${ND.nicX + 12}" cy="${mid - ND.nicH / 2 + 12}" r="6"/></g>`);
    const allowed = lane.allowed === null ? "מה מותר — דורש API (#705)" : `bind: ${lane.allowed.length ? netAllowedShort(lane.allowed) : "אף שירות לא מאזין כאן"}`;
    parts.push(`<rect class="vlan" x="${ND.vlX}" y="${mid - ND.vlH / 2}" width="${ND.vlW}" height="${ND.vlH}" rx="6"/>`
      + svgLines(ND.vlX + 10, mid - ND.vlH / 2 + 6, ND.vlW - 20, ND.vlH - 12, [{ cls: "t", text: `${lane.vlan.label} — לפי הגדרה` },
        { cls: "m w2", text: allowed, tip: lane.allowed === null ? allowed : `מותר (לפי bind): ${lane.allowed.join(" · ") || "אף שירות לא מאזין כאן"}` },
        { cls: "m", text: `רשת ${bidi(netNetworkOf(n) || "—")}` },
        { cls: "m", text: `${lane.clients.filter((c) => c.kind !== "class").length} מחוברים · ${lane.unreg || 0} לא רשומים` }]));
    const lnCls = lane.led === "ok" ? "ok" : lane.led === "off" ? "off" : "warn";
    parts.push(`<path class="ln ${lnCls}" d="M${ND.nicX} ${mid} H${ND.vlX + ND.vlW}"/><circle class="led ${lane.led}" cx="${(ND.nicX + ND.vlX + ND.vlW) / 2}" cy="${mid}" r="5"/>`);
    let cy = y + (bodyH - (lane.clients.length * (ND.clH + ND.gap) - ND.gap)) / 2;
    for (const c of lane.clients) {
      const cmid = cy + ND.clH / 2, lc = c.led || "off";   // אדום/כתום גם כשהתיבה מקווקוה; "off" = אין ראיה
      parts.push(`<rect class="box ${c.led || ""}" x="${ND.clX}" y="${cy}" width="${ND.clW}" height="${ND.clH}" rx="4"${c.dashed ? ' stroke-dasharray="4 4"' : ""}/>`
        + svgLines(ND.clX + 10, cy + 6, ND.clW - 20, ND.clH - 12, [{ cls: "t", text: c.title }, { cls: "m", text: c.sub, tip: c.tip }])
        + `<path class="ln ${lc}" d="M${ND.vlX} ${mid} H430 V${cmid} H${ND.clX + ND.clW}"${c.viaFw ? ' stroke-dasharray="4 4"' : ""}/>`
        + (c.led ? `<circle class="led ${c.led}" cx="430" cy="${(mid + cmid) / 2}" r="5"/>` : ""));
      cy += ND.clH + ND.gap;
    }
    y += bodyH + 24;
  }
  const H = y + 30;
  const srv = `<rect class="server" x="${ND.srvX}" y="40" width="${ND.srvW}" height="${H - 70}" rx="6"/>`
    + svgText(ND.srvX + ND.srvW - 20, 68, "t", ME && ME.server_name ? ME.server_name : "שרת אימג'ים")
    + svgText(ND.srvX + ND.srvW - 20, 86, "m", `${ME && ME.version ? ME.version + " · " : ""}${lanes.length} כרטיסים · נקרא ${NETW.at}`);
  // ‏gotcha (CLAUDE.md): SVG עם viewBox בלבד קורס ל-0 תחת max-height — width/height מפורשים.
  return `<svg class="netdiag-svg" xmlns="http://www.w3.org/2000/svg" width="${ND.W}" height="${H}" viewBox="0 0 ${ND.W} ${H}" role="img" aria-label="תרשים הרשת: ${lanes.length} כרטיסים">${srv}${parts.join("")}</svg>`
    + (orphans.length ? UI.note("warn", `${orphans.length} מחוברים עם כתובת מחוץ לרשתות הכרטיסים: ${esc(orphans.map((o) => o.title).join(" · "))}`) : "");
}

function netLegend() {
  return `<div class="legend"><span><i class="sw-ok"></i>תקין / מחובר</span><span><i class="sw-warn"></i>אזהרה / לא רשום</span><span><i class="sw-err"></i>כשל / לא עונה</span><span><i class="sw-empty"></i>לא מחובר / כבוי</span></div>`;
}
function netUnreadNotes() {
  const e = NETW.err, out = [];
  // ‏#1088: אחרי התקנה נקייה — מה שהמנהל צריך לעשות, לפני כל אזהרה אחרת.
  if (NET_DEPLOY && NET_DEPLOY.configured === false) out.push(UI.note("warn", `${esc(NET_DEPLOY.hint || "רשת ההפצה לא הוגדרה")} — <a href="#" onclick="openNetwork(2);return false;">לשונית רשת הפצה</a>`));
  else if (e.deploy) out.push(UI.note("warn", `‏/net/deploy לא נקרא: ${esc(e.deploy)} — לא ידוע אם רשת ההפצה הוגדרה`));
  if (e.config) out.push(UI.note("warn", `‏/net/config לא נקרא: ${esc(e.config)} — "מוגדר" ו"פער" אינם ידועים`));
  else if (NETCFG && NETCFG.live && !NETCFG.live.checked) out.push(UI.note("warn", `המצב בפועל לא נקרא (${esc(NETCFG.live.reason || "")}) — אף כתובת אינה מאומתת`));
  if (NETCFG && NETCFG.sourced === false) out.push(UI.note("err", "‏/etc/network/interfaces אינו טוען את interfaces.d — כל מה שנכתב שם לא ייקרא באתחול"));
  if (e.net) out.push(UI.note("warn", `‏/net לא נקרא: ${esc(e.net)} — "מי נראה ברשת" ו"לא רשומים" אינם ידועים`));
  if (e.ports) out.push(UI.note("warn", `‏/ports לא נקרא: ${esc(e.ports)} — "מה מותר על כל וילן" אינו ידוע`));
  if (e.ssh) out.push(UI.note("warn", `‏/ssh לא נקרא: ${esc(e.ssh)}`));
  if (e.monitor) out.push(UI.note("warn", `‏/monitor/machines לא נקרא: ${esc(e.monitor)} — משכפלים ובנייה אינם בתרשים`));
  if (e.nodes) out.push(UI.note("warn", `‏/storage-nodes לא נקרא: ${esc(e.nodes)} — סניפים אינם בתרשים`));
  const rb = NETCFG && NETCFG.rollback && typeof rollbackBanner === "function" ? rollbackBanner(NETCFG.rollback) : "";
  return (rb ? `<div class="c12">${rb}</div>` : "") + out.map((h) => `<div class="c12">${h}</div>`).join("");
}
function netWarnCount() {
  const nics = NETW.nics || [];
  let k = Object.keys(NETW.err).length;
  k += (NET || []).filter((d) => d.registered === false).length;
  k += (NETW.nodes || []).filter((n) => !n.disabled_at && n.connected === false).length;
  k += nics.filter((n) => n.dhcp_diverged || (netCfgRow(n.name) && (netCfgRow(n.name).mismatches || []).length)).length;
  if (NETCFG && NETCFG.sourced === false) k += 1;
  return k;
}
const NET_TABS = ["תרשים", "חיבורים פיזיים", "רשת הפצה", "פורטים"];
function netTabClick(i) { return i === 3 ? "selectPageById('ports')" : `openNetwork(${i})`; }
function netHeader(tab, { name, sub, pill, actions, icon = "network" }) {
  const crumbs = [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "תשתית" }, { label: "רשת", onclick: "openNetwork(0)" }];
  if (tab) crumbs.push({ label: NET_TABS[tab] });
  return UI.objHeader({ crumbs, icon, name, sub, pill, actions: actions + `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>`, tabs: NET_TABS, tab, tabClick: netTabClick });
}

/* --- לשונית 0: התרשים --- */
function netSelectedCard() {
  const n = (NETW.nics || []).find((x) => x.name === NETW.sel);
  if (!n) return "";
  const enc = encodeId(n.name), cfg = netCfgRow(n.name), svc = netServicesOn(n), pr = netProbeStatus(n.name), ssh = netSshOf(n.name);
  const live = netLiveAddrs(n);
  const kv1 = UI.kv([["בפועל", netLiveChecked() || !cfg ? `<span class="mono">${esc(live.join(" · ") || "—")}</span> · ${netLinkStatus(n)}` : UI.status("unk", "לא נקרא")],
    ["מוגדר", `<span class="mono">${esc(netConfiguredText(n))}</span>`], ["פער", netGapStatus(n)]]);
  const kv2 = UI.kv([["DHCP", netDhcpCell(n)],
    ["חכירה", esc(n.enabled || n.proxy ? `${n.lease || "—"} · שער ${n.gateway || "—"} · DNS ${(n.dns || []).join(", ") || "—"}` : "—")],
    ["מי עוד עונה", UI.status(pr.cls, pr.text)]]);
  const kv3 = UI.kv([["שירותים כאן", svc === null ? `<span class="muted" title="דורש API (#705)">דורש API (#705)</span>` : esc(svc.join(" · ") || "אף שירות לא מאזין כאן")],
    ["SSH לשרת", UI.status(ssh.cls, ssh.text)],
    ["פעולות", `<div class="acts on"><button class="btn sm" onclick="netEditAddress('${enc}')">עריכת כתובת</button><button class="btn sm" onclick="netEditDhcp('${enc}')">עריכת DHCP</button><button class="btn sm" onclick="netProbe('${enc}')">בדוק מי עונה</button></div>`]]);
  return UI.card({ title: `${n.name} — ${n.description || "ללא תיאור"}`, small: "לחיצה על כרטיס בתרשים מחליפה", body: `<div class="kv3">${kv1}${kv2}${kv3}</div>` });
}
function networkDiagramTab() {
  const nics = NETW.nics;
  const deploy = netDeployNic();
  const unreg = NET ? (NET || []).filter((d) => d.registered === false).length : null;
  const nodes = NETW.nodes || [], down = nodes.filter((n) => !n.disabled_at && n.connected === false).length;
  const sub = nics === null ? `‏/net/interfaces לא נקרא: ${esc(NETW.err.interfaces || "")}`
    : esc([`${nics.length} כרטיסים`, `${nics.length} וילנים (לפי הגדרה — מודל וילן דורש API (#705))`, `DHCP הפצה: ${deploy ? `${deploy.name} · ${deploy.dhcp_live_label}` : "כבוי"}`,
      unreg === null ? "לא רשומים: לא נקרא" : `${unreg} לא רשומים`, nodes.length ? `${nodes.length} סניפים${down ? ` · ${down} לא מגיבים` : ""}` : "אין סניפים"].join(" · "));
  const warn = netWarnCount();
  const pill = nics === null ? UI.pill("err", "לא נקרא") : warn ? UI.pill("warn", `${warn} אזהרות`) : UI.pill("ok", "אין אזהרות");
  const actions = `<button class="btn" onclick="addNic()">+ כרטיס</button><button class="btn" onclick="previewDnsmasq().catch(e=>toast(e.message))">קבצי dnsmasq</button>`;
  const header = netHeader(0, { name: "רשת", sub, pill, actions });
  let body;
  if (nics === null) body = `<div class="c12">${UI.note("err", `לא הצלחתי לקרוא את כרטיסי הרשת: ${esc(NETW.err.interfaces || "")} — אין מה לצייר`)}</div>`;
  else if (!nics.length) body = `<div class="c12">${UI.empty("לא נמצאו כרטיסי רשת — השרת לא רואה אף כרטיס ב-/sys/class/net, ואין הגדרה שמורה", `<button class="btn" onclick="refreshPage()">רענון</button>`)}</div>`;
  else {
    const model = netDiagramModel();
    body = netUnreadNotes()
      + `<div class="c12 card"><div class="card-h"><span>מה מחובר לאן</span>${netLegend()}</div><div class="card-b netdiag">${netDiagramSvg(model)}</div></div>`
      + netSelectedCard()
      + `<div class="c12 cap">נמדד: כתובות ופער — /net/config · DHCP חי — /net/interfaces · מחוברים — /monitor/machines, /net, /storage-nodes · "מה מותר" — bind של /ports בלבד. וילנים ומדיניות — דורש API (#705).${classroomsOn() ? " כיתות — v2." : ""}</div>`;
  }
  return `<div class="page">${header}<div class="body">${body}</div></div>`;
}

/* --- לשונית 1: חיבורים פיזיים --- */
function netNicRow(n) {
  const enc = encodeId(n.name), sel = NETW.sel === n.name;
  const tags = (n.trunk ? ` ${UI.pill("warn", "רשת המכללה")}` : "") + (!n.present ? ` ${UI.pill("warn", "לא קיים במערכת")}` : "");
  const name = `${UI.nameHtml(n.name, `${esc(n.description || "ללא תיאור")} · <span class="mono">${esc(n.mac || "—")}</span>`)}${tags}`;
  const live = netLiveAddrs(n);
  const actual = (NETCFG && !netLiveChecked()) ? UI.status("unk", "לא נקרא") : `<span class="mono">${esc(live.join(" · ") || "—")}</span>`;
  const stop = "event.stopPropagation();";
  // תוויות קצרות: 9 עמודות ב-1100px; "עריכת כתובת" המלא — בכרטיס הנבחר
  const acts = `<div class="acts"><button class="btn sm" onclick="${stop}netEditAddress('${enc}')">כתובת</button><button class="btn sm" onclick="${stop}netEditDhcp('${enc}')">DHCP</button></div>`;
  return { attrs: `data-nic="${esc(n.name)}"${sel ? ' class="sel"' : ""} onclick="netSelect('${enc}')" tabindex="0" aria-selected="${sel}"`,
    cells: [name, netLinkStatus(n), actual, `<span class="mono">${esc(netConfiguredText(n))}</span>`, netGapStatus(n), netDhcpCell(n, false), netRoleLabel(n), netSshSwitch(n.name), acts] };
}
function netRoutesCard() {
  if (!NETCFG) return UI.card({ title: "נתיבים סטטיים", cls: "c4", body: UI.note("warn", `‏/net/config לא נקרא${NETW.err.config ? `: ${esc(NETW.err.config)}` : ""}`) });
  const live = new Set((NETCFG.live && NETCFG.live.routes) || []), checked = netLiveChecked();
  const rows = [];
  for (const nic of NETCFG.interfaces || []) (nic.routes || []).forEach((r, i) => {
    const seen = live.has(`${r.destination}/${maskBits(r.netmask)} via ${r.gateway}`);
    rows.push([`${UI.status(!checked ? "unk" : seen ? "ok" : "err", "")}<span class="mono">${esc(r.destination)}/${maskBits(r.netmask)}</span>`, `<span class="mono">${esc(r.gateway)}</span>`, `<span class="mono">${esc(nic.name)}</span>`,
      `<div class="acts"><button class="btn sm danger" onclick="routeDeleteSheet('${encodeId(nic.name)}',${i})">מחק</button></div>`]);
  });
  const body = UI.datagrid({ columns: ["יעד", "דרך", "כרטיס", ""], rows, cls: "stable", empty: "אין נתיבים סטטיים — נתיב שנוסף כאן נשאר גם אחרי אתחול" });
  return UI.card({ title: "נתיבים סטטיים", small: checked ? "נקודה ירוקה = בטבלת הניתוב" : "המצב בפועל לא נקרא", cls: "c4", acts: `<button class="btn sm" onclick="addRoute()">+ נתיב</button>`, body, flush: rows.length > 0 });
}
function netNicSelectedCard() {
  const n = (NETW.nics || []).find((x) => x.name === NETW.sel);
  if (!n) return UI.card({ title: "כרטיס", cls: "c8", body: UI.empty("בחר כרטיס בטבלה — הפרטים, הקובץ וההסבר על החזרה יופיעו כאן") });
  const enc = encodeId(n.name), cfg = netCfgRow(n.name), rb = NETCFG && NETCFG.rollback;
  const live = netLiveAddrs(n);
  const kv = UI.kv([
    ["כתובת בפועל", NETCFG && !netLiveChecked() ? UI.status("unk", "לא נקרא") : `<span class="mono">${esc(live.join(" · ") || "אין כתובת")}</span> <span class="cap">(נקרא ${esc(NETW.at)})</span>`],
    ["מוגדר בקובץ", `<span class="mono">${esc(netConfiguredText(n))}</span>${cfg && cfg.mode_he ? ` <span class="cap">${esc(cfg.mode_he)}</span>` : ""}`],
    ["שער", `<span class="mono">${esc((cfg && cfg.gateway) || "—")}</span>`],
    ["DNS", `<span class="mono">${esc((cfg && (cfg.dns || []).join(", ")) || "—")}</span>`],
    ["תיאור", `${esc(n.description || "ללא תיאור")} ${UI.link("עריכה", `netDescribe('${enc}')`)}`],
    ["DHCP", netDhcpCell(n)],
    ["SSH לשרת", UI.status(netSshOf(n.name).cls, netSshOf(n.name).text)],
  ]);
  const rbNote = !rb ? UI.note("warn", "‏/net/config לא נקרא — לא ידוע אם ההחזרה האוטומטית פעילה")
    : rb.armed ? UI.note("info", `שינוי כתובת מוחל עם חלון חזרה של ${esc(rb.window_seconds)} שניות: אם הקונסולה לא מאשרת "אני עדיין רואה", ההגדרה הקודמת חוזרת לבד (${esc(rb.unit)}).`)
    : UI.note("err", `ההחזרה האוטומטית (${esc(rb.unit)}) אינה פעילה: ${esc(rb.armed_detail)} — שינוי שיכול לנתק את הקונסולה ייחסם`);
  const btns = `<div class="acts on" style="margin-top:10px"><button class="btn sm" onclick="netShowFile('${enc}')">תצוגה מקדימה של הקובץ</button><button class="btn sm primary" onclick="netEditAddress('${enc}')">עריכת כתובת</button><button class="btn sm" onclick="netEditDhcp('${enc}')">DHCP</button><button class="btn sm danger" onclick="netForget('${enc}')">שכחה</button></div>`;
  return UI.card({ title: `${n.name} — ${n.description || "ללא תיאור"}`, small: "נבחר", cls: "c8", body: `<div class="kv2">${kv}<div>${rbNote}${btns}</div></div>` });
}
function networkNicsTab() {
  const nics = NETW.nics;
  const live = NETCFG && NETCFG.live;
  const configured = (NETCFG ? NETCFG.interfaces || [] : []).filter((r) => r.mode !== "manual").length;
  const sub = nics === null ? `‏/net/interfaces לא נקרא: ${esc(NETW.err.interfaces || "")}`
    : esc([`${nics.length} כרטיסים`, NETCFG ? `${configured} מוגדרים` : "מוגדרים: לא נקרא",
      !live ? "מצב בפועל: לא נקרא" : live.checked ? `מצב בפועל נקרא ${NETW.at}` : `מצב בפועל לא נקרא (${live.reason || ""})`,
      live && live.checked ? `נתיבים: ${(live.routes || []).join(" · ") || "אין"} · DNS ${(live.nameservers || []).join(", ") || "אין"}` : ""].filter(Boolean).join(" · "));
  const gaps = (nics || []).filter((n) => { const c = netCfgRow(n.name); return c && (c.mismatches || []).length; }).length;
  const pill = nics === null ? UI.pill("err", "לא נקרא") : !NETCFG || !netLiveChecked() ? UI.pill("warn", "לא מאומת") : gaps ? UI.pill("err", `${gaps} לא תואמים`) : UI.pill("ok", "מוגדר = בפועל");
  const header = netHeader(1, { name: "חיבורים פיזיים", sub, pill, actions: `<button class="btn primary" onclick="addNic()">+ כרטיס</button><button class="btn" onclick="addRoute()">+ נתיב סטטי</button>` });
  let body;
  if (nics === null) body = `<div class="c12">${UI.note("err", `לא הצלחתי לקרוא את כרטיסי הרשת: ${esc(NETW.err.interfaces || "")}`)}</div>`;
  else {
    const table = UI.datagrid({ columns: ["כרטיס", "קישור", "בפועל", "מוגדר", "פער", "DHCP", "תפקיד", "SSH לשרת", ""], rows: nics.map(netNicRow), cls: "stable",
      empty: "לא נמצאו כרטיסי רשת — השרת לא רואה אף כרטיס, ואין הגדרה שמורה" });
    body = netUnreadNotes() + `<div class="c12 card"><div class="card-b${nics.length ? " flush" : ""}">${table}</div></div>` + netNicSelectedCard() + netRoutesCard();
  }
  return `<div class="page">${header}<div class="body">${body}</div></div>`;
}

/* --- לשונית 2: רשת הפצה (DHCP) — קודם מה מחולק בפועל ומי עוד עונה, ורק אז עריכה --- */
function netSeenRow(d) {
  const macEnc = encodeId(d.mac), reg = d.registered !== false;
  const secs = secondsSince(d.last_seen);
  const seen = secs === null ? UI.status("unk", "זמן לא נקרא") : UI.status(secs < NET_ONLINE_SECONDS ? "ok" : "", secs < NET_ONLINE_SECONDS ? "מחובר" : ago(d.last_seen));
  const who = reg ? UI.nameHtml(d.name || d.mac, esc([ROLE_HE[d.role] || d.role || "", d.group_label || ""].filter(Boolean).join(" · ")))
    : `${UI.pill("warn", "לא רשום")}${d.description ? `<span class="sub">${esc(d.description)}</span>` : ""}`;
  const acts = reg ? `<div class="acts"><button class="btn sm" onclick="openMachineDetail('${macEnc}')">פרטים</button></div>`
    : `<div class="acts on"><button class="btn sm primary" onclick="netRegister('${macEnc}')">רשום</button><button class="btn sm" onclick="netDeviceDescribe('${macEnc}')">תיאור</button><button class="btn sm danger" onclick="netDeviceForget('${macEnc}')">הסר</button></div>`;
  return { attrs: `data-mac="${esc(d.mac)}"${reg ? "" : ' class="unreg"'}`, cells: [who, `<span class="mono">${esc(d.mac)}</span>`, d.ip ? `<span class="mono">${esc(d.ip)}</span>` : `<span class="muted">—</span>`, seen, bootWhere(d.boot), acts] };
}
function netOtherNicRow(n) {
  const enc = encodeId(n.name), pr = netProbeStatus(n.name);
  return [`${UI.nameHtml(n.name, esc(n.description || "ללא תיאור"))}${n.trunk ? ` ${UI.pill("warn", "רשת המכללה")}` : ""}`, netDhcpCell(n), esc(n.enabled ? "מופעל" : n.proxy ? "proxy" : "כבוי"),
    `${UI.status(pr.cls, pr.text)}`, `<div class="acts on"><button class="btn sm" onclick="netProbe('${enc}')">בדוק</button><button class="btn sm" onclick="netEditDhcp('${enc}')">${n.proxy ? "עריכה" : "הגדר כרשת הפצה"}</button></div>`];
}
function networkDeployTab() {
  const nics = NETW.nics, focus = netDeployNic();
  const pr = focus ? netProbeStatus(focus.name) : null;
  const inNet = focus && NET ? NET.filter((d) => d.ip && nicHasIp(focus, d.ip)).length : null;
  const liveState = focus ? (focus.dhcp_live || {}).state : "";
  const sub = nics === null ? `‏/net/interfaces לא נקרא: ${esc(NETW.err.interfaces || "")}`
    : !focus ? (NET_DEPLOY && NET_DEPLOY.configured === false ? "רשת ההפצה לא הוגדרה — בחר כרטיס למטה, קבע לו כתובת סטטית (עריכת כתובת) ואז \"הגדר כרשת הפצה\"" : "אין כרטיס מוגדר כרשת הפצה, ואין כרטיס שמשרת DHCP כרגע — בחר כרטיס למטה")
    : esc([focus.name, netNetworkOf(focus) || "ללא כתובת", `dnsmasq: ${focus.dhcp_live_label}`, NET === null ? "נראו ברשת: לא נקרא" : `${inNet} נראו ברשת ההפצה`, `מי עוד עונה: ${pr.text}`].join(" · "));
  const pill = nics === null ? UI.pill("err", "לא נקרא") : !focus ? UI.pill("", "כבוי")
    : focus.dhcp_diverged ? UI.pill("warn", "לא תואם למצב הפעיל") : liveState === "serving" ? UI.pill("ok", "משרת") : liveState === "unknown" ? UI.pill("err", "לא נקרא") : UI.pill("warn", focus.dhcp_live_label);
  const editTarget = focus || (nics || []).find((n) => n.present && !n.trunk) || (nics || [])[0];
  const actions = (editTarget ? `<button class="btn primary" onclick="netEditDhcp('${encodeId(editTarget.name)}')">${focus ? "עריכת DHCP" : "הגדר כרשת הפצה"}</button>` : "")
    + (focus ? `<button class="btn" onclick="netProbe('${encodeId(focus.name)}')">בדוק מי עונה</button>` : "")
    + `<button class="btn" onclick="previewDnsmasq().catch(e=>toast(e.message))">קבצי dnsmasq</button>`;
  const header = netHeader(2, { name: "רשת הפצה — DHCP", sub, pill, actions, icon: "deploy" });
  let body;
  if (nics === null) body = `<div class="c12">${UI.note("err", `לא הצלחתי לקרוא את כרטיסי הרשת: ${esc(NETW.err.interfaces || "")}`)}</div>`;
  else {
    const stored = focus ? `${focus.enabled ? "מופעל" : focus.proxy ? "proxy" : "כבוי"} — ` : "";
    const match = !focus ? UI.status("", "אין") : !(focus.dhcp_live || {}).checked ? UI.status("unk", `${stored}המצב הפעיל לא נקרא`)
      : focus.dhcp_diverged ? UI.status("warn", `${stored}לא תואם למצב הפעיל`) : UI.status("ok", `${stored}תואם למצב הפעיל`);
    const serves = !focus ? UI.empty("אין כרטיס מוגדר כרשת הפצה — \"הגדר כרשת הפצה\" על אחד הכרטיסים למטה. DHCP הוא ההגדרה המסוכנת ביותר במערכת (#53): השרת בודק לפני ההדלקה מי כבר עונה.")
      : UI.kv([["כרטיס", `<span class="mono">${esc(focus.name)}</span> ${esc(focus.description ? `— ${focus.description}` : "")}`], ["כתובת השרת", `<span class="mono">${esc(focus.server_ip || "—")}</span>`],
        ["טווח", `<span class="mono">${esc(focus.range_start && focus.range_end ? `${focus.range_start} – ${focus.range_end}` : "—")}</span>`], ["מסכה", `<span class="mono">${esc(focus.netmask || "—")}</span>`],
        ["שער", `<span class="mono">${esc(focus.gateway || "—")}</span>`], ["DNS", `<span class="mono">${esc((focus.dns || []).join(", ") || "—")}</span>`], ["חכירה", esc(focus.lease || "—")],
        ["בפועל", netDhcpCell(focus)], ["שמור בקונסולה", match], ["מי עוד עונה", UI.status(pr.cls, pr.text)]]);
    const seenRows = (NET || []).map(netSeenRow);
    const seenBody = NET === null ? UI.note("err", `‏/net לא נקרא: ${esc(NET_ERR || NETW.err.net || "")} — אין לדעת מי קיבל כתובת`)
      : UI.datagrid({ columns: ["מכונה", "MAC", "IP", "נראה", "שלב אתחול", ""], rows: seenRows, cls: "stable", empty: "אף מכונה עוד לא דיברה עם השרת — כשמחשב יעלה ב-PXE הוא יופיע כאן" });
    const others = (nics || []).filter((n) => n !== focus && (classroomsOn() || !n.proxy));
    const othersTable = UI.datagrid({ columns: ["כרטיס", "מצב DHCP חי", "שמור בקונסולה", "מי עוד עונה", ""], rows: others.map(netOtherNicRow), cls: "stable", empty: "אין כרטיסים נוספים" });
    body = netUnreadNotes()
      + UI.card({ title: "מה השרת מחלק", small: focus ? "כפי שנקרא מקובץ dnsmasq ומהשירות" : "", cls: "c4", body: serves })
      + UI.card({ title: "מי קיבל כתובת", small: `מה-hello ומ-net_devices · ${NET_ONLINE_SECONDS} שניות = "מחובר" · חכירות dnsmasq עצמן — דורש API`, cls: "c8", body: seenBody, flush: NET !== null && seenRows.length > 0 })
      + UI.card({ title: "כרטיסים אחרים", small: "DHCP הוא בדיוק על כרטיס אחד — ההפצה", cls: "c12", body: othersTable, flush: others.length > 0 });
  }
  return `<div class="page">${header}<div class="body">${body}</div></div>`;
}

function networkPage(tab = 0) {
  if (NETW.nics === null && !Object.keys(NETW.err).length) return pagePlaceholder();
  PORT_ACTIONS.clear();
  if (tab === 1) return networkNicsTab();
  if (tab === 2) return networkDeployTab();
  return networkDiagramTab();
}

/* ניווט: מהעץ (הצומת "רשת" = הדף; הילדים = לשונית; כרטיס בעץ = לשונית 1 עם הכרטיס נבחר), ומדף הפורטים. */
function openNetwork(tab = 0, nicEnc = null, el = null) {
  if (nicEnc) { let name = nicEnc; try { name = decodeURIComponent(nicEnc); } catch (e) {} NETW.sel = name; }
  if (!pageAllowed("network")) return;
  const t = Number(tab) || 0;
  if (current !== "network") selectPage(el || document.querySelector('.inventory-node[data-page="network"]'), "network");
  else if (el) markTreeSelection(el, "network");
  if (t !== currentTab) activateTab(t); else renderCurrent();
}
function populateSidebarNics() {
  const tree = document.getElementById("nicTree");
  if (!tree) return;
  const nics = NETW.nics || [];
  const parent = document.getElementById("nicNode") || tree.previousElementSibling;
  const arrow = document.getElementById("nicTreeArrow") || (parent && parent.querySelector(".tree-arrow, .tree-arrow-sp"));
  if (!nics.length) {
    tree.innerHTML = ""; tree.setAttribute("hidden", "");
    if (arrow) { arrow.className = "tree-arrow-sp"; arrow.textContent = ""; arrow.onclick = null; }
    return;
  }
  if (arrow) {
    const open = !tree.hasAttribute("hidden");
    arrow.className = "tree-arrow"; arrow.dataset.open = String(open); arrow.textContent = open ? "▾" : "▸";
    arrow.onclick = (event) => { event.stopPropagation(); toggleInventoryGroup(parent, "nicTree"); };
  }
  tree.innerHTML = nics.map((n) => {
    const on = (current === "network" && currentTab === 1 && NETW.sel === n.name) ? " active" : "";
    return `<div class="inventory-node${on}" role="treeitem" tabindex="0" onclick="openNetwork(1,'${encodeId(n.name)}',this)"><span class="tree-arrow-sp"></span><span>${uiIcon("network")}</span><span dir="ltr">${esc(n.name)}</span></div>`;
  }).join("");
}
/* net.js (אחרי שמירת DHCP) ו-netcfg.js (אחרי כתובת/נתיב/אישור החזרה) קוראים לזה. */
function refreshNetPages() {
  populateSidebarNics();
  if (current !== "network") return;
  if (refreshNetPages._busy) return;
  refreshNetPages._busy = true;
  try { loadNetwork().catch((e) => toast("רענון הרשת נכשל: " + e.message)); }
  finally { refreshNetPages._busy = false; }
}
/* אחרי כל ציור של דף הרשת: הספירה לאחור של ההחזרה (netcfg.js) — היא חיה ב-DOM. */
function wireNetPage() {
  if (current !== "network") return;
  if (typeof startCountdown === "function") startCountdown();
}

function healthCheckById(id) {
  return (Array.isArray(HEALTH) ? HEALTH : []).find((c) => c.id === id) || null;
}

/* ---------- #954 גל 5/#996: רשת › פורטים — טבלה אחת, מתג בכל שורה ----------
   docs/design/console-redesign/network-ports.md + docs/interfaces.md §16.
   מקור אחד לכל שורה: הכול מגיע מ-/ports (id, state/detail/bind, ו-#996:
   enabled/listening/toggle/toggle_url/confirm_word/confirm_when/off_means;
   #1013: warning_he — TFTP 69 הוא מתג "confirm" רגיל, dnsmasq בצד השרת).
   DHCP 67, PXE proxy 4011, מוניטור 5900 ו-SSH לתחנות ממשיכים לפנות לאותם
   endpoints כמו קודם (net.js / monitorToggle / sshToggle) — toggle_url של
   השורה מצביע לשם ואינו /ports/{id}, ו-PUT /ports/{id} עליהן היה מקבל 409.
   ‏SSH לשרת × כרטיס בא היום כשורה מוכנה לכל כרטיס (id `ssh_server:<nic>`)
   עם toggle_url אמיתי (לא תבנית) — בלי fetch נוסף. שרת ישן שלא שולח שורת
   ‏`dhcp`/`ssh_server:*` בכלל נופל לבנייה מקומית מ-/net/interfaces ו-/ssh,
   כמו בגל 5 (`havePortsDhcp`/`havePortsSshServer` למטה). כשהשדות חסרים
   המתג מוצג במצב "לא ידוע" ולחיצה מסבירה — לא כפתור אפור (README §8).
   🔒 = הקלדת שם (עיקרון 7); ‏confirm_when קובע כיוון ההקלדה — לא ניחוש
   בלקוח (#1015). "מה קורה אם מכבים" מהשרת (`off_means`) כשקיים. */
let SSH_STATE = null, sshError = "";
let PORTS_MONITOR = null, portsMonitorError = "";
let PORTS_NICS = null, portsNicsError = "";
let PORTS_AT = "";
const PORT_ACTIONS = new Map();
const PORT_OFF_MEANS = {
  dhcp: "אף מכונה בוילן ההפצה לא מקבלת כתובת — אין PXE, אין סבב",
  pxe_proxy: "תחנות בוילן שה-DHCP בו אינו שלנו לא רואות את התפריט — עולות מהדיסק",
  monitor: "תופס באתחול הבא של כל תחנה — מכונה שעולה לא מפעילה שירות צפייה",
  ssh_stations: "תופס באתחול הבא — תחנות עולות בלי dropbear ובלי מעטפת טכנאי",
};
const PORT_API_NEEDED = "דורש API (#996)";
const offMeansNote = (text) => `<div class="sheet-note danger">מה קורה אם מכבים: ${esc(text)}</div>`;
/* אזהרה לפני כל כיבוי (הכרעת נדב 19/09 — "על כל פורט אזהרה", לא רק 69):
   `warning_he` מהשרת כשיש (#1013), אחרת "מה קורה אם מכבים" מה-`off_means` —
   אותו בלוק אדום, לפני שדה ההקלדה או כפתור האישור. */
const warningNote = (p) => {
  const text = p.warning_he || (p.off_means ? `מה קורה אם מכבים: ${p.off_means}` : "");
  return text ? `<div class="sheet-note danger" data-testid="port-warning">${esc(text)}</div>` : "";
};

function nicBody(n, over) {
  return { enabled: n.enabled, proxy: n.proxy, trunk: n.trunk, range_start: n.range_start, range_end: n.range_end,
    netmask: n.netmask, gateway: n.gateway, dns: n.dns, lease: n.lease, server_ip: n.server_ip, confirm: n.name, ...over };
}

function portSwitch(key) { const fn = PORT_ACTIONS.get(key); if (fn) fn(); }

function portSwitchHtml(key, on, lock, cap, label) {
  const cls = on === null ? "unk" : on ? "on" : "";
  return `<span class="swrow"><button type="button" class="sw ${cls}" role="switch" aria-checked="${on === null ? "mixed" : String(!!on)}" aria-label="${esc(label)}" onclick="portSwitch(decodeURIComponent('${encodeId(key)}'))"></button><span class="cap">${lock ? "🔒 " : ""}${esc(cap)}</span></span>`;
}

/* DHCP: הדלקה = הטופס הקיים של net.js (מצב/טווח/הקלדת שם הכרטיס); אחרי
   שמירה מרעננים את הטבלה הזו (ה-sheet של net.js מרענן רק את דפי הרשת). */
function portDhcpOn() {
  const list = PORTS_NICS || [];
  const nic = list.find((n) => n.present && !n.trunk) || list[0];
  if (!nic) { toast("אין כרטיס רשת להדליק עליו DHCP — ראו חיבורים פיזיים"); return; }
  editNic(nic);
  const form = $("#sheet");
  const orig = form && form.onsubmit;
  if (typeof orig === "function") form.onsubmit = async (event) => { await orig(event); await loadPorts(); };
}

function portDhcpOff(n) {
  sheet({ title: `כיבוי DHCP על ${n.name}`, sub: "הכרטיס מפסיק לחלק כתובות ולענות ל-PXE.", danger: true, submitLabel: "כבה",
    note: offMeansNote(PORT_OFF_MEANS.dhcp),
    verify: { label: `להמשך הקלד את שם הכרטיס: ${n.name}`, mustEqual: n.name },
    onSubmit: async () => { await saveNic(n.name, nicBody(n, { enabled: false, proxy: false })); await loadPorts(); } });
}

function portProxyOff(n) {
  confirmSheet(`כיבוי PXE proxy על ${n.name}`, PORT_OFF_MEANS.pxe_proxy, "כבה",
    async () => { await saveNic(n.name, nicBody(n, { enabled: false, proxy: false })); await loadPorts(); });
}

function portSshNic(name) {
  const nic = (SSH_STATE && SSH_STATE.interfaces || []).find((n) => n.name === name);
  if (!nic) return;
  const open = SSH_STATE.interfaces.filter((n) => n.enabled).map((n) => n.name);
  // סגירה היא הכיוון הבטוח ולכן לחיצה אחת — חוץ מהדלת האחרונה,
  // שאחריה אין SSH לשרת בכלל.
  const last = nic.enabled && open.length === 1 && open[0] === nic.name;
  sshToggle(`/ssh/interfaces/${encodeId(nic.name)}`, !nic.enabled, nic.name, !nic.enabled || last,
    nic.enabled ? `סגירת SSH לשרת על ${nic.name}` : `פתיחת SSH לשרת על ${nic.name}`,
    nic.enabled ? "זו הדלת האחרונה שפתוחה — אחריה אין SSH לשרת מאף רשת."
      : (classroomsOn() ? "‏sshd יאזין בוילן הזה. אם זה וילן הכיתות — הוא ייפתח לסטודנטים." : "‏sshd יאזין בוילן הזה."),
    nic.enabled ? "אין SSH לשרת מאף רשת — פתיחה מחדש רק ממסך השרת" : "");
}

function portSshStations() {
  const st = SSH_STATE && SSH_STATE.stations;
  if (!st) return;
  sshToggle("/ssh/stations", !st.enabled, st.confirm_word, !st.enabled,
    "פתיחת SSH ומעטפת טכנאי בכל התחנות",
    "כל מחשב שיעלה יריץ dropbear. המפתח הציבורי ארוז ב-initramfs, שנמשך ב-HTTP פתוח מווילן ההפצה.",
    "");
}

/* #996: מתג פר-פורט בשרת. "confirm" = הקלדת שם השרת; "api" = אישור בלחיצה;
   "none" = אין מתג, הלחיצה מסבירה למה. `confirm_when` קובע את הכיוון
   שדורש הקלדה (השרת, לא ניחוש כאן): "off"/"on"/"on_or_last_off"; שרת ישן
   בלי השדה — נשמר הכיוון הישן (הקלדה בכל כיוון, כמו לפני #1015). */
function portConfirmDirection(p, enabling) {
  if (p.toggle !== "confirm") return false;
  if (p.confirm_when === "on") return enabling;
  if (p.confirm_when === "off") return !enabling;
  return true;   // "on_or_last_off" מטופל ייעודית ב-ssh_server:<nic>; שדה חסר = ברירת המחדל הישנה
}

function portToggleUrl(p) {
  return p.toggle_url ? p.toggle_url.replace(/^\/api\/console/, "") : `/ports/${encodeId(p.id)}`;
}

function portServerToggle(p) {
  const enabling = !p.enabled;
  const word = p.confirm_word || ME.server_name;
  const send = async (extra) => {
    await put(portToggleUrl(p), { enabled: enabling, ...extra });
    toast(enabling ? `${p.name} ${p.port} — הודלק` : `${p.name} ${p.port} — כובה`);
    await loadPorts();
  };
  const title = `${enabling ? "הדלקת" : "כיבוי"} ${p.name} ${p.port}/${p.proto}`;
  if (p.toggle === "none") { toast(`אין מתג לפורט הזה: ${p.off_means || p.detail || ""}`); return; }
  if (p.toggle === "confirm" && portConfirmDirection(p, enabling)) {
    sheet({ title, sub: p.desc || "", danger: true, submitLabel: enabling ? "הדלק" : "כבה",
      note: enabling ? "" : warningNote(p),
      verify: { label: "להמשך יש להקליד את שם השרת:", mustEqual: word },
      onSubmit: () => send({ confirm: word }) });
    return;
  }
  if (!enabling) {
    sheet({ title, sub: p.desc || "", danger: true, submitLabel: "כבה", note: warningNote(p),
      onSubmit: () => send({}) });
    return;
  }
  confirmSheet(title, p.desc || "", "הדלק", () => send({}));
}

function portRows() {
  const rows = [];
  const havePortsDhcp = PORTS.some((p) => p.id === "dhcp");
  const havePortsSshServer = PORTS.some((p) => p.id && p.id.startsWith("ssh_server:"));

  // שרת ישן (לפני #996/#1015): /ports אינו שולח שורת dhcp כלל — נבנה
  // מ-/net/interfaces כמו בגל 5, שורה לכל כרטיס שמשרת בפועל.
  if (!havePortsDhcp) {
    const dhcpBase = { name: "DHCP הפצה", desc: "dnsmasq · כתובות לוילן ההפצה", port: "67", proto: "udp",
      who: "תחנות · משכפלים · בנייה", off: PORT_OFF_MEANS.dhcp, api: ["ok", "קיים", "PUT /net/interfaces/{n}"] };
    const serving = (PORTS_NICS || []).filter((n) => n.enabled);
    if (PORTS_NICS === null) {
      rows.push({ ...dhcpBase, key: "dhcp", nic: "", state: "unknown", detail: `‏/net/interfaces לא נקרא: ${portsNicsError}`,
        on: null, lock: true, cap: "לא נקרא", act: () => toast("רשימת הכרטיסים לא נקראה — אין על מה להפעיל את המתג: " + portsNicsError) });
    } else if (!serving.length) {
      rows.push({ ...dhcpBase, key: "dhcp", nic: "", state: "off", detail: "לא הודלק על אף כרטיס",
        on: false, lock: true, cap: "כבוי · הדלקה = הקלדת שם הכרטיס", act: portDhcpOn });
    } else {
      for (const n of serving) {
        const live = n.dhcp_live ? dhcpLiveClass(n.dhcp_live.state) : "unknown";
        rows.push({ ...dhcpBase, key: `dhcp:${n.name}`, name: `DHCP הפצה — ${n.name}`, nic: n.name,
          state: { ok: "ok", warn: "warn", off: "off" }[live] || "unknown",
          detail: `${n.dhcp_live_label || ""}${n.range_start ? ` · ${n.range_start}–${n.range_end}` : ""}`,
          on: true, lock: true, cap: `דלוק · כיבוי = הקלדת ${n.name}`, act: () => portDhcpOff(n) });
      }
    }
  }

  for (const p of PORTS) {
    // #1015: bind הוא מערך כתובות (גם ריק כשקוראים ולא מוצאים). שרת ישן
    // בלי השדה כלל (undefined) — "" כמו קודם, שמראה "דורש API".
    const r = { key: p.id, name: p.name, desc: p.desc, port: p.port, proto: p.proto, who: p.target,
      nic: Array.isArray(p.bind) ? p.bind : (p.bind || ""),
      state: p.state, detail: p.detail, note: p.note, off: p.off_means || "", api: null, on: null, lock: false, cap: "", act: null };
    if (p.id === "monitor") {
      const m = PORTS_MONITOR;
      Object.assign(r, { nic: "בתחנה, לא בשרת", off: r.off || PORT_OFF_MEANS.monitor, api: ["ok", "קיים", "PUT /monitor/settings"],
        on: m ? !!m.enabled : null, lock: !(m && m.enabled),
        cap: !m ? `לא נקרא: ${portsMonitorError}` : m.enabled ? "דלוק · כיבוי בלחיצה" : "מוניטור: כבוי (ברירת מחדל) — הדלקה חושפת 5900 על וילן ההפצה",
        act: m ? () => monitorToggle(!m.enabled) : () => toast("‏/monitor/settings לא נקרא: " + portsMonitorError) });
    } else if (p.id === "ssh_stations") {
      const st = SSH_STATE && SSH_STATE.stations;
      Object.assign(r, { nic: "בתחנה, לא בשרת", off: r.off || PORT_OFF_MEANS.ssh_stations, api: ["ok", "קיים", "PUT /ssh/stations"],
        on: st ? !!st.enabled : null, lock: !(st && st.enabled),
        cap: !st ? `לא נקרא: ${sshError}` : st.enabled ? "דלוק · כיבוי בלחיצה" : `כבוי · הדלקה = הקלדת ${st.confirm_word}`,
        act: st ? portSshStations : () => toast("‏/ssh לא נקרא: " + sshError) });
    } else if (p.id === "pxe_proxy") {
      const nic = PORTS_NICS ? PORTS_NICS.find((n) => n.proxy) : undefined;
      Object.assign(r, { nic: nic ? nic.name : r.nic, off: r.off || PORT_OFF_MEANS.pxe_proxy, api: ["ok", "קיים", "PUT /net/interfaces/{n} proxy"],
        on: PORTS_NICS === null ? null : !!nic,
        cap: PORTS_NICS === null ? `לא נקרא: ${portsNicsError}` : nic ? `דלוק על ${nic.name}` : "כבוי · הדלקה = בחירת proxy בטופס ה-DHCP של הכרטיס",
        act: PORTS_NICS === null ? () => toast("‏/net/interfaces לא נקרא: " + portsNicsError) : nic ? () => portProxyOff(nic) : portDhcpOn });
    } else if (p.id === "dhcp") {
      // #1015: שורה אחת מרוכזת מ-/ports; המתג עדיין פונה ל-/net/interfaces/{n},
      // ולכן צריך לדעת על איזה כרטיס — מ-/net/interfaces (כמו קודם).
      const serving = PORTS_NICS === null ? null : PORTS_NICS.filter((n) => n.enabled);
      Object.assign(r, { off: r.off || PORT_OFF_MEANS.dhcp, api: ["ok", "קיים", "PUT /net/interfaces/{n}"],
        nic: serving && serving.length === 1 ? serving[0].name : r.nic,
        on: serving === null ? null : !!p.enabled, lock: true,
        cap: serving === null ? `לא נקרא: ${portsNicsError}`
          : !p.enabled ? "כבוי · הדלקה = הקלדת שם הכרטיס"
          : serving.length === 1 ? `דלוק · כיבוי = הקלדת ${serving[0].name}`
          : "דלוק · כיבוי דרך חיבורים פיזיים (כמה כרטיסים משרתים)",
        act: serving === null ? () => toast("‏/net/interfaces לא נקרא: " + portsNicsError)
          : !p.enabled ? portDhcpOn
          : serving.length === 1 ? () => portDhcpOff(serving[0])
          : () => openNetwork(1) });
    } else if (p.id && p.id.startsWith("ssh_server:")) {
      // #1015: שורה מוכנה לכל כרטיס — toggle_url אמיתי (לא תבנית), בלי fetch נוסף.
      const name = p.interface || p.id.slice("ssh_server:".length);
      const open = PORTS.filter((x) => x.id && x.id.startsWith("ssh_server:") && x.enabled)
        .map((x) => x.interface || x.id.slice("ssh_server:".length));
      const last = !!p.enabled && open.length === 1 && open[0] === name;
      const needsConfirm = !p.enabled || last;
      Object.assign(r, { who: "טכנאי → השרת", off: r.off || (last ? "אין SSH לשרת מאף רשת — פתיחה מחדש רק ממסך השרת" : "אין SSH לשרת דרך הכרטיס הזה"),
        api: ["ok", "קיים", `PUT ${portToggleUrl(p)}`], on: !!p.enabled, lock: needsConfirm,
        cap: p.enabled ? (last ? `הדלת האחרונה · סגירה = הקלדת ${name}` : "פתוח · סגירה בלחיצה") : `סגור · פתיחה = הקלדת ${name}`,
        act: () => sshToggle(portToggleUrl(p), !p.enabled, p.confirm_word || name, needsConfirm,
          p.enabled ? `סגירת SSH לשרת על ${name}` : `פתיחת SSH לשרת על ${name}`,
          p.enabled ? (last ? "זו הדלת האחרונה שפתוחה — אחריה אין SSH לשרת מאף רשת." : "")
            : (classroomsOn() ? "‏sshd יאזין בוילן הזה. אם זה וילן הכיתות — הוא ייפתח לסטודנטים." : "‏sshd יאזין בוילן הזה."),
          p.enabled && last ? "אין SSH לשרת מאף רשת — פתיחה מחדש רק ממסך השרת" : "") });
    } else if (p.toggle) {
      // שאר שורות /ports עם toggle ("api"/"confirm"/"none") — http_boot/
      // http_console/kiosk/interserver: portToggleUrl(p) הולך ל-toggle_url
      // כשהוא קיים (#1015, /api/console/ports/{id}) ואחרת ל-/ports/{id}
      // (המסלול היחיד לפני #1015).
      Object.assign(r, { api: ["ok", "קיים", `PUT ${portToggleUrl(p)}`], on: !!p.enabled, lock: p.toggle === "confirm",
        cap: p.toggle === "none" ? "אין מתג — לחיצה מסבירה" : p.enabled ? (p.toggle === "confirm" ? "דלוק · כיבוי = הקלדת שם השרת" : "דלוק") : "כבוי",
        act: () => portServerToggle(p) });
    } else {
      Object.assign(r, { api: ["warn", "דורש API", "#996"], on: null, lock: false, cap: PORT_API_NEEDED, off: r.off || PORT_API_NEEDED,
        act: () => toast(`מתג לפורט ${p.port} ${PORT_API_NEEDED} — השרת הזה עדיין לא מחזיר toggle ב-/ports`) });
    }
    rows.push(r);
  }

  // שרת ישן: /ports אינו שולח שורת ssh_server:<nic> כלל — נבנה מ-/ssh, כמו בגל 5.
  if (!havePortsSshServer) {
    const sshBase = { port: "22", proto: "tcp", who: "טכנאי → השרת" };
    if (!SSH_STATE) {
      rows.push({ ...sshBase, key: "ssh_server", name: "SSH לשרת", desc: "sshd", nic: "", state: "unknown", detail: `‏/ssh לא נקרא: ${sshError}`,
        on: null, lock: true, cap: "לא נקרא", off: "אין SSH לשרת מאף רשת", api: ["ok", "קיים", "PUT /ssh/interfaces/{n}"],
        act: () => toast("‏/ssh לא נקרא: " + sshError) });
    } else {
      const open = SSH_STATE.interfaces.filter((n) => n.enabled).map((n) => n.name);
      for (const n of SSH_STATE.interfaces) {
        const last = n.enabled && open.length === 1 && open[0] === n.name;
        const addr = (n.addresses || []).map((a) => String(a).split("/")[0]).join(", ");
        let state, detail;
        if (n.listening === null) { state = "unknown"; detail = "טבלת הסוקטים לא נקראה"; }
        else if (n.enabled && n.listening) { state = "ok"; detail = `מאזין ${addr || "ללא כתובת IPv4"}:22 · אומת`; }
        else if (n.enabled) { state = "bad"; detail = "נשמר כפתוח אבל sshd לא מאזין"; }
        else if (n.listening) { state = "warn"; detail = "מאזין למרות שהמתג כבוי"; }
        else { state = "off"; detail = "סגור · אומת"; }
        rows.push({ ...sshBase, key: `ssh_nic:${n.name}`, name: `SSH לשרת — ${n.name}`, desc: `sshd · ${addr || "ללא כתובת IPv4"}`, nic: n.name,
          state, detail, on: !!n.enabled, lock: !n.enabled || last,
          cap: n.enabled ? (last ? `הדלת האחרונה · סגירה = הקלדת ${n.name}` : "פתוח · סגירה בלחיצה") : `סגור · פתיחה = הקלדת ${n.name}`,
          off: last ? "אין SSH לשרת מאף רשת — פתיחה מחדש רק ממסך השרת" : "אין SSH לשרת דרך הכרטיס הזה",
          api: ["ok", "קיים", `PUT /ssh/interfaces/${n.name}`], act: () => portSshNic(n.name) });
      }
    }
  }
  return rows;
}

// שם כרטיס/כתובת בודדת (מחרוזת) — mono LTR; טקסט עברי ("בתחנה, לא בשרת") —
// רגיל. רשימת bind מהשרת (#1015, תמיד מערך) — כל הכתובות, mono LTR; ריק = "—".
/* רשימת כתובות ההאזנה (#1015 `bind`): שורה לכתובת, IPv4 קודם, link-local של IPv6
   מקופל ל-"+N" עם tooltip — אחרת שורת TFTP (8 כתובות) מתחה את הטבלה מעבר למסך
   והמתגים נעלמו מימין (נדב 17/09). */
function portNicHtml(nic) {
  if (Array.isArray(nic)) {
    if (!nic.length) return `<span class="muted">—</span>`;
    const main = nic.filter((a) => !a.startsWith("[fe80") && !a.startsWith("[::1]"));
    const rest = nic.filter((a) => !main.includes(a));
    const shown = (main.length ? main : nic).slice(0, 4);
    const hidden = nic.filter((a) => !shown.includes(a));
    const more = hidden.length
      ? ` <span class="muted" title="${esc(hidden.join(" · "))}">+${hidden.length}</span>` : "";
    return `<span class="mono bindlist" dir="ltr">${shown.map((a) => esc(a)).join("<br>")}</span>${more}`;
  }
  if (!nic) return `<span class="muted" title="${PORT_API_NEEDED}: כתובת ההאזנה">—</span>`;
  return /^[ -~]+$/.test(nic) ? `<span class="mono">${esc(nic)}</span>` : esc(nic);
}

function portRow(r) {
  PORT_ACTIONS.set(r.key, r.act);
  const nic = portNicHtml(r.nic);
  const api = r.api ? `${UI.pill(r.api[0], r.api[1])}${r.api[2] ? ` <span class="cap mono">${esc(r.api[2])}</span>` : ""}` : "";
  // ה-note של השרת ("לפתוח ב-FW: …") — tooltip על שם השירות, לא שורה שלישית בכל תא.
  return [`<span${r.note ? ` title="${esc(r.note)}"` : ""}>${UI.nameHtml(r.name, esc(r.desc || ""))}</span>`,
    `<span class="mono">${esc(r.port)}/${esc(r.proto)}</span>`, esc(r.who || "—"), nic,
    `${UI.status(healthStatusClass(r.state), healthStatusLabel(r.state))}<span class="sub">${esc(r.detail || "")}</span>`,
    portSwitchHtml(r.key, r.on, r.lock, r.cap, `${r.name} ${r.port}`),
    `<span class="wrap">${esc(r.off || "")}</span>`, api];
}

function ports() {
  if (!PORTS && !portsError) return pagePlaceholder();
  PORT_ACTIONS.clear();
  const rows = PORTS ? portRows() : [];
  const n = (s) => rows.filter((r) => r.state === s).length;
  let pill;
  if (!PORTS) pill = UI.pill("err", "לא נקרא");
  else if (n("bad")) pill = UI.pill("err", `${n("bad")} לא מאזין`);
  else if (n("unknown")) pill = UI.pill("warn", `${n("unknown")} לא נקרא`);
  else pill = UI.pill("ok", "כל השורות נמדדו");
  const sub = PORTS
    ? esc(`${rows.length} שורות · מצב האזנה כפי שנקרא מהשרת ב-${PORTS_AT} · מתג הדלקה/כיבוי לכל פורט · 🔒 = כיבוי ששובר את המערכת, מאחורי הקלדת שם`)
    : `‏/ports לא נקרא: ${esc(portsError)}`;
  const header = UI.objHeader({
    crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "תשתית" }, { label: "רשת" }, { label: "פורטים" }],
    icon: "network", name: "פורטים", sub, pill, actions: `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>`,
    tabs: NET_TABS, tab: 3, tabClick: (i) => i === 3 ? "activateTab(3)" : `openNetwork(${i})` });   // ‏#954 גל 8: לשונית של אובייקט הרשת
  const body = !PORTS
    ? UI.note("err", `לא הצלחתי לקרוא את רשימת הפורטים: ${esc(portsError)}`)
    : UI.datagrid({ columns: ["שירות", "פורט", "מי מתחבר", "על איזה כרטיס", "מצב נמדד", "מתג", "מה קורה אם מכבים", "API"], rows: rows.map(portRow), cls: "dense" });
  const table = UI.card({ title: "שירותים ופורטים", small: "מצב נמדד — \"כבוי\" ≠ \"לא מאזין\" ≠ \"לא נקרא\"", body, flush: !!PORTS });
  let foot = "";
  if (PORTS && SSH_STATE && SSH_STATE.listeners) {
    const l = SSH_STATE.listeners;
    foot = l.checked
      ? `<div class="c12">${UI.note("", esc(`מאזינים בפורט ${l.port}: ${(l.addresses || []).join(", ") || "אף אחד"}`))}</div>`
      : `<div class="c12">${UI.note("warn", esc(`טבלת הסוקטים לא נקראה (${l.reason}) — אין לדעת מה פתוח`))}</div>`;
  }
  const legend = `<div class="c12">${UI.note("info", `<b>כל מתג</b> = מודאל עם מה יקרה, ו-🔒 = הקלדת שם (עיקרון 7) לפני שהוא זז. ${UI.pill("ok", "קיים")} — מחובר ל-API של היום; ${UI.pill("warn", "דורש API")} — הדלקה/כיבוי פר-פורט בשרת (#996): המתג מוצג, לחיצה עליו מסבירה — לא כפתור אפור.`)}</div>`;
  return `<div class="page">${header}<div class="body">${table}${foot}${legend}</div></div>`;
}

/* ---------- #954 גל 6: מוניטור — הרשימה ----------
   נבנה לפי docs/design/console-redesign/monitor.md: טבלה אחת (בלי לשוניות),
   מתג "מוניטור לתחנות" (#827) בכותרת, "מחובר" כפי שהשרת קבע (‏/monitor/machines,
   חלון 90ש'), "נראה" מ-/net (loadMachines), "מה על המסך" מ-prompt.
   זה המקום היחיד לכפתור המוניטור (נדב 17/09); monitor.html/.js — לא נגענו. */
function monitorRowHtml(m) {
  const macEnc = encodeId(m.mac), net = netFor(m.mac);
  let state;
  if (m.online) state = UI.status("ok", "מחובר");
  else if (NET == null) state = UI.status("", "לא מחובר · נראה: לא נקרא");
  else if (net && net.last_seen) state = UI.status("", `לא מחובר · נראה ${ago(net.last_seen)}`);
  else state = UI.status("", "מעולם לא נראה");
  const screen = m.prompt ? UI.status("warn", waitingText(m)) : `<span class="muted">—</span>`;
  const open = m.online
    ? `<button class="btn sm primary" onclick="monitorMachine('${macEnc}')">פתח מוניטור</button>`
    : `<button class="btn sm" disabled title="המכונה אינה מחוברת — אין למה להתחבר">פתח מוניטור</button>`;
  const wol = m.online ? "" : `<button class="btn sm" onclick="wakeMachine('${macEnc}')">WoL</button>`;
  return { attrs: `data-mac="${esc(m.mac)}"`, cells: [
    UI.nameHtml(m.name || m.mac, `<span class="mono">${esc(m.mac)}</span>`),
    esc(ROLE_HE[m.role] || m.role),
    m.ip ? `<span class="mono">${esc(m.ip)}</span>` : `<span class="muted">—</span>`,
    state, screen, `<div class="acts">${open}${wol}</div>`] };
}

function monitorPage() {
  if (!MONITOR && !monitorError) return pagePlaceholder();
  const list = MONITOR ? MONITOR.machines || [] : [];
  const enabled = MONITOR && MONITOR.settings ? MONITOR.settings.enabled === true : null;
  const online = list.filter((m) => m.online).length;
  const sub = MONITOR
    ? ["צפייה ושליטה במסך של מחשבי הבנייה והשיכפול", `${list.length} מכונות`, `${online} מחוברות עכשיו`].map(esc).join(" · ")
    : `‏/monitor לא נקרא: ${esc(monitorError)}`;
  const pill = !MONITOR ? UI.pill("err", "לא נקרא") : enabled ? UI.pill("ok", "המתג דלוק") : UI.pill("warn", "המתג כבוי");
  const sw = !MONITOR ? "" : `<span class="swrow"><button type="button" class="sw${enabled ? " on" : ""}" role="switch" aria-checked="${enabled ? "true" : "false"}" aria-label="מוניטור לתחנות" onclick="monitorToggle(${enabled ? "false" : "true"})"></button><span class="cap">מוניטור לתחנות${enabled ? "" : " · כבוי (ברירת מחדל) — הדלקה חושפת 5900 על וילן ההפצה"}</span></span>`;
  const header = UI.objHeader({ crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "תשתית" }, { label: "מוניטור" }],
    icon: "machine", name: "מוניטור", sub, pill, actions: sw + `<button class="btn" onclick="loadMonitor()">${uiIcon("refresh")} רענון</button>` });
  let body;
  if (!MONITOR) body = UI.note("err", `לא הצלחתי לקרוא את רשימת המוניטור: ${esc(monitorError)}`);
  else {
    const off = enabled ? "" : UI.note("warn", `מוניטור: כבוי (ברירת מחדל) — הדלקה חושפת 5900 על וילן ההפצה. מכונה שעולה עכשיו אינה מפעילה שירות צפייה, ו"פתח מוניטור" ייכשל. הדלקה תופסת באתחול הבא של כל מכונה, לא במכונות שכבר רצות.`);
    const table = UI.datagrid({ cls: "acts-on", columns: ["מכונה", "תפקיד", "IP", "מצב", "מה על המסך", ""], rows: list.map(monitorRowHtml),
      empty: "אין מחשבי בנייה או שיכפול רשומים — הוסיפו אותם בדף המחשבים" });
    body = (off ? `<div class="c12">${off}</div>` : "") + `<div class="c12 card"><div class="card-b${list.length ? " flush" : ""}">${table}</div></div>`
      + `<div class="c12 cap">"מחובר" = השרת ראה את המכונה ב-90 השניות האחרונות עם כתובת; "מה על המסך" נגזר מ-prompt של המכונה, לא מצילום. המוניטור נפתח בחלון נפרד (monitor.html).</div>`;
  }
  return `<div class="page">${header}<div class="body">${body}</div></div>`;
}

/* ---------- #954 גל 6: הגדרות ----------
   נבנה לפי docs/design/console-redesign/settings.md: עברית, שורה לכל הגדרה
   (שם + מה זה אומר | שליטה), שמור/בטל בכותרת — מנוטרלים עד שינוי, שורה
   שהשתנתה מסומנת. ‏POST /settings עם המפתחות ששונו בלבד. כרטיס העדכון
   עצמו — בבריאות (גל 5); כאן המתג וקישור. מתג זהות המכונה (#855) נשאר. */
let SETTINGS = null, settingsError = "", SETTINGS_DRAFT = {}, LOGO_EXISTS = null;
const SETTING_ROWS = [
  { key: "server_name", label: "שם השרת", cap: "מופיע בעץ הניווט, בכותרת ובמסכי התחנה", type: "text" },
  { key: "console_idle_seconds", label: "ניתוק אוטומטי בחוסר פעילות", cap: "קונסולה פתוחה בלי מגע (עכבר/מקלדת) — מתנתקת; תקף מיד לסשן הזה", type: "number", min: 60, unit: "שניות (מינימום 60)" },
  { key: "recovery_require_login", label: "שחזור תחנה בודדת דורש כניסה", cap: "מומלץ דלוק; כיבוי רק להדגמה", type: "switch" },
  { key: "class_deploy_enabled", label: "הפצה לכיתה ממסך התחנה", cap: "כבוי במהדורת השיכפול — הכרטיס יורד מהתפריט והשרת מסרב לסבב כיתה", type: "switch" },
  { key: "session_wait_seconds", label: "המתנה מהמצטרף האחרון", cap: "סבב מתחיל לבד כשעברו כך וכך שניות בלי מצטרף חדש", type: "number", min: 30, unit: "שניות (מינימום 30)" },
  { key: "update_enabled", label: "עדכון גרסה מהקונסולה", cap: "כבוי כברירת מחדל; החיבור היוצא לריפו הציבורי נפתח רק בזמן בדיקה/עדכון", type: "switch", link: ["כרטיס העדכון — בבריאות ושירותים", "selectPageById('health')"] },
  { key: "identity_check", label: "בדיקת זהות מכונה", cap: "כתובת המקור של hello/דיווח חייבת להתאים לחכירת ה-DHCP של ה-MAC. דלוק כברירת מחדל; לכבות רק כשה-DHCP של וילן ההפצה אינו השרת הזה — הכיבוי נרשם ביומן", type: "switch", defaultOn: true },
];
function settingRow(key) { return SETTING_ROWS.find((r) => r.key === key); }
/* הערך כפי שהשרת החזיר, מנורמל: מתג → boolean (#855: חסר = דלוק), השאר מחרוזת. */
function settingCurrent(key) {
  const r = settingRow(key), v = SETTINGS ? SETTINGS[key] : undefined;
  if (r.type === "switch") return r.defaultOn ? v !== "false" : v === "true";
  return v == null ? "" : String(v);
}
function settingValue(key) { return key in SETTINGS_DRAFT ? SETTINGS_DRAFT[key] : settingCurrent(key); }
function settingsDirty() { return Object.keys(SETTINGS_DRAFT).length > 0; }
function settingsSet(key, value) {
  if (value === settingCurrent(key)) delete SETTINGS_DRAFT[key]; else SETTINGS_DRAFT[key] = value;
  settingsMarkDirty();
}
/* מתג: הופך את הערך ומצייר את הכפתור עצמו — בלי לצבוע את הדף מחדש (השדות
   האחרים לא מאבדים פוקוס). */
function settingsToggle(key, btn) {
  const on = !settingValue(key);
  settingsSet(key, on);
  if (!btn) return;
  btn.classList.toggle("on", on);
  btn.setAttribute("aria-checked", String(on));
  if (btn.nextElementSibling) btn.nextElementSibling.textContent = on ? "דלוק" : "כבוי";
}
function settingsMarkDirty() {
  const dirty = settingsDirty();
  for (const id of ["set-save", "set-cancel"]) { const b = document.getElementById(id); if (b) b.disabled = !dirty; }
  for (const r of SETTING_ROWS) { const el = document.getElementById("srow-" + r.key); if (el) el.classList.toggle("changed", r.key in SETTINGS_DRAFT); }
  const pill = document.getElementById("set-pill"); if (pill) pill.innerHTML = dirty ? UI.pill("warn", "שינויים לא נשמרו") : "";
}
function settingRowHtml(r) {
  const v = settingValue(r.key), changed = r.key in SETTINGS_DRAFT;
  let ctl;
  if (r.type === "switch") ctl = `<span class="swrow"><button type="button" class="sw${v ? " on" : ""}" role="switch" aria-checked="${v ? "true" : "false"}" aria-label="${esc(r.label)}" onclick="settingsToggle('${r.key}', this)"></button><span class="cap">${v ? "דלוק" : "כבוי"}</span></span>`;
  else if (r.type === "number") ctl = `<span class="swrow"><input type="number" id="set-${r.key}" value="${esc(v)}" min="${r.min}" step="1" aria-label="${esc(r.label)}" oninput="settingsSet('${r.key}', this.value)"><span class="cap">${esc(r.unit)}</span></span>`;
  else ctl = `<input type="text" id="set-${r.key}" value="${esc(v)}" aria-label="${esc(r.label)}" oninput="settingsSet('${r.key}', this.value)">`;
  const cap = esc(r.cap) + (r.link ? ` · ${UI.link(r.link[0], r.link[1])}` : "");
  return `<div class="srow${changed ? " changed" : ""}" id="srow-${r.key}"><div><b>${esc(r.label)}</b><div class="cap">${cap}</div></div><div class="ctl">${ctl}</div></div>`;
}
function brandingCardHtml() {
  const img = LOGO_EXISTS ? `<img src="/api/console/branding/logo?t=${Date.now()}" alt="לוגו">` : `<img src="logo.svg?v=4.0" alt="ImageCtl">`;
  const state = LOGO_EXISTS == null ? UI.status("unk", "לא נקרא") : LOGO_EXISTS ? UI.status("ok", "לוגו מותאם") : UI.status("", "ברירת המחדל");
  const btns = `<button class="btn sm" onclick="document.getElementById('logo-input').click()">העלאה</button>`
    + (LOGO_EXISTS ? `<button class="btn sm danger" onclick="settingsLogoClear()">הסרה</button>` : "")
    + `<input type="file" id="logo-input" class="hidden" accept="image/png,image/jpeg,image/webp,image/svg+xml" aria-label="קובץ לוגו" onchange="settingsLogoUpload(this)">`;
  const body = `<div class="brand-row"><div class="logo-box">${img}</div><div><b>לוגו</b> ${state}<div class="cap">PNG / JPG / WEBP / SVG עד 2MB · מוצג בכותרת ובמסך הכניסה · SVG נבדק לפני הקבלה</div><div class="acts" style="margin-top:6px">${btns}</div></div></div>`
    + `<div style="margin-top:12px">${UI.note("", "ערכת הצבעים (בהיר/כהה) היא בחירה של כל משתמש בכפתור שבכותרת — לא הגדרת שרת.")}</div>`;
  return UI.card({ title: "מיתוג", cls: "c4", body });
}
function settings() {
  if (!SETTINGS && !settingsError) return pagePlaceholder();
  const dirty = settingsDirty();
  const actions = `<button class="btn primary" id="set-save" onclick="saveSettings()"${dirty ? "" : " disabled"}>שמור</button><button class="btn" id="set-cancel" onclick="cancelSettings()"${dirty ? "" : " disabled"}>בטל שינויים</button>`;
  const sub = SETTINGS ? "מדיניות הקונסולה ומיתוג · נשמר ב-DB של השרת · כל שינוי נרשם ביומן" : `‏/settings לא נקרא: ${esc(settingsError)}`;
  const pill = `<span id="set-pill">${!SETTINGS ? UI.pill("err", "לא נקרא") : dirty ? UI.pill("warn", "שינויים לא נשמרו") : ""}</span>`;
  const header = UI.objHeader({ crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "ניהול" }, { label: "הגדרות" }],
    icon: "settings", name: "הגדרות", sub, pill, actions: SETTINGS ? actions : "" });
  const body = !SETTINGS
    ? UI.note("err", `לא הצלחתי לקרוא את ההגדרות: ${esc(settingsError)}`)
    : UI.card({ title: "כללי", small: "שורה שהשתנתה מסומנת עד השמירה", cls: "c8", body: `<div class="srows">${SETTING_ROWS.filter((r) => r.key !== "class_deploy_enabled" || classroomsOn()).map(settingRowHtml).join("")}</div>` }) + brandingCardHtml();
  return `<div class="page">${header}<div class="body">${body}</div></div>`;
}
async function saveSettings() {
  const body = {};
  for (const r of SETTING_ROWS) {
    if (!(r.key in SETTINGS_DRAFT)) continue;
    const v = SETTINGS_DRAFT[r.key];
    if (r.type === "number") {
      const n = Number(v);
      if (!Number.isInteger(n) || n < r.min) { toast(`${r.label}: מספר שלם, ${r.min} לפחות`); return; }
      body[r.key] = String(n);
    } else if (r.type === "switch") body[r.key] = v ? "true" : "false";
    else {
      const s = String(v).trim();
      if (!s) { toast(`${r.label}: לא יכול להיות ריק`); return; }
      body[r.key] = s;
    }
  }
  if (!Object.keys(body).length) return;
  try {
    await post("/settings", body);
    if (body.console_idle_seconds) { ME.idle_seconds = Number(body.console_idle_seconds); startIdleWatch(); }   // תקף מיד, בלי כניסה מחדש
    if (body.server_name) { ME.server_name = body.server_name; applyServerName(); }
    SETTINGS_DRAFT = {};
    toast(`נשמר — ${Object.keys(body).length} ${Object.keys(body).length === 1 ? "הגדרה" : "הגדרות"}, נרשם ביומן`);
    await loadSettingsData();
  } catch (e) { toast(e.message); }
}
function cancelSettings() { SETTINGS_DRAFT = {}; renderCurrent(); }
async function loadSettingsData() {
  try { SETTINGS = await api("/settings"); settingsError = ""; }
  catch (e) { SETTINGS = null; settingsError = e.message; toast("טעינת ההגדרות נכשלה: " + e.message); }
  try { LOGO_EXISTS = await loadLogo(); } catch (e) { LOGO_EXISTS = null; }   // לא נקרא ≠ אין לוגו
  if (current === "settings") renderCurrent();
}
async function settingsLogoUpload(input) {
  const file = input.files && input.files[0];
  input.value = "";
  if (!file) return;
  const response = await fetch("/api/console/branding/logo", { method: "POST", credentials: "same-origin", headers: { "Content-Type": file.type }, body: file });
  if (!response.ok) {
    let message = "שגיאה " + response.status;
    try { message = (await response.json()).detail || message; } catch (e) {}
    toast("הלוגו נדחה: " + message, 6000);
    return;
  }
  toast("הלוגו הוחלף — נרשם ביומן");
  await loadSettingsData();
}
function settingsLogoClear() {
  confirmSheet("הסרת הלוגו", "הקונסולה תחזור לסמל ברירת המחדל.", "הסר",
    async () => { await del("/branding/logo"); toast("הלוגו הוסר"); await loadSettingsData(); });
}

/* ---------- #649 שלב 1: ארגז כלים ----------
   הקטלוג (server/tools_catalog.json ← docs/tools/TOOLS-CHOICE.md + CATALOG.md)
   כקבוצה = כרטיס, טבלה אחת לכל קבוצה: ☐ בנייה/שיכפול | ☐ תלמיד | כלי | בינארי |
   סיכון | גודל. שתי בחירות נפרדות (הכרעת נדב 17/09 — תלמיד = v2, הבחירה
   נשמרת כבר עכשיו); מה שמסומן ייארז ל-initrd בשלב 2. הסימון חי ב-TOOLS_DRAFT
   (Set לכל יעד) עד "שמור" (PUT /tools/selection); הכותרת, כפתור השמירה ומוני
   הקבוצות מתעדכנים במקום — בלי לצבוע את הדף מחדש בכל ☐ (הסינון לא מאבד פוקוס). */
let TOOLS = null, toolsError = "", TOOLS_DRAFT = null;
const TOOLS_FILTER = { q: "", risk: "", rec: false, packed: false, scope: "build" };
const TOOLS_TARGETS = ["build", "student"];
const TOOLS_RISK = { ro: ["", "קריאה", "ro — קריאה בלבד: מציג מידע ולא משנה כלום במחשב"],
                     rw: ["warn", "משנה", "rw — משנה משהו הפיך (סדר אתחול, איפוס USB, הערת מחשב)"],
                     destroy: ["err", "מוחק", "destroy — מוחק נתונים: דורש הקלדת שם המחשב לפני ההרצה"] };

async function loadTools() {
  try {
    TOOLS = await api("/tools/catalog");
    toolsError = "";
    TOOLS_DRAFT = { build: new Set(TOOLS.selection.build), student: new Set(TOOLS.selection.student) };
  } catch (e) {
    TOOLS = null; TOOLS_DRAFT = null;   // לא נקרא ≠ קטלוג ריק (עיקרון 5)
    toolsError = e.message;
    toast("טעינת ארגז הכלים נכשלה: " + e.message);
  }
  if (current === "tools") renderCurrent();
}

function toolsDirty() {
  if (!TOOLS || !TOOLS_DRAFT) return false;
  return TOOLS_TARGETS.some((t) => {
    const saved = TOOLS.selection[t] || [];
    return saved.length !== TOOLS_DRAFT[t].size || saved.some((id) => !TOOLS_DRAFT[t].has(id));
  });
}
/* ספירה וגודל משוער של הסימון — כמו summarize() בשרת: ארוז = 0, בלי מספר = "לא נמדד" (נספר, לא נסכם). */
function toolsSummary(target) {
  const chosen = (TOOLS.tools || []).filter((t) => TOOLS_DRAFT[target].has(t.id));
  const known = chosen.reduce((s, t) => s + (!t.packed && t.size_kb != null ? t.size_kb : 0), 0);
  return { count: chosen.length, size_kb_known: known, size_unknown: chosen.filter((t) => !t.packed && t.size_kb == null).length };
}
function toolsSizeText(kb) { return kb >= 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb} kB`; }
function toolsPillText() {
  const b = toolsSummary("build"), s = toolsSummary("student");
  const kb = b.size_kb_known + s.size_kb_known, unknown = b.size_unknown + s.size_unknown;
  return `${b.count} לבנייה · ${s.count} לתלמיד · תוספת משוערת ~${toolsSizeText(kb)}${unknown ? ` (+${unknown} לא נמדדו)` : ""}`;
}
function toolsVisible(t) {
  const f = TOOLS_FILTER, q = f.q.trim().toLowerCase();
  if (q && !`${t.title_he} ${t.what} ${t.binary} ${t.id}`.toLowerCase().includes(q)) return false;
  if (f.risk && t.risk !== f.risk) return false;
  if (f.rec && !t.recommended) return false;
  if (f.packed && !t.packed) return false;
  return true;
}
function toolsFilter(key, value) {
  TOOLS_FILTER[key] = value;
  if (key === "scope") return;
  const box = document.getElementById("tools-groups");
  if (box) box.innerHTML = toolsGroupsHtml();
  const n = document.getElementById("tools-count");
  if (n) n.textContent = toolsCountText();
}
function toolsCountText() {
  const all = TOOLS.tools.length, shown = TOOLS.tools.filter(toolsVisible).length;
  return shown === all ? `${all} כלים` : `${shown} מתוך ${all} כלים`;
}
/* ☐ אחד: מעדכן את הטיוטה ואת מה שתלוי בה — בלי לצבוע מחדש. */
function toolsToggle(id, target, on) {
  if (!TOOLS_DRAFT) return;
  if (on) TOOLS_DRAFT[target].add(id); else TOOLS_DRAFT[target].delete(id);
  toolsRefreshHeader();
}
function toolsRefreshHeader() {
  const pill = document.getElementById("tools-pill"); if (pill) pill.textContent = toolsPillText();
  const dirty = toolsDirty();
  const save = document.getElementById("tools-save"); if (save) save.disabled = !dirty;
  const cancel = document.getElementById("tools-cancel"); if (cancel) cancel.hidden = !dirty;
  const state = document.getElementById("tools-state"); if (state) state.innerHTML = dirty ? UI.pill("warn", "שינויים לא נשמרו") : "";
  (TOOLS.groups || []).forEach((g, i) => { const el = document.getElementById(`tools-grp-${i}`); if (el) el.textContent = toolsGroupCaption(g); });
}
function toolsGroupCaption(group) {
  const list = TOOLS.tools.filter((t) => t.group === group);
  const n = (target) => list.filter((t) => TOOLS_DRAFT[target].has(t.id)).length;
  return `${list.length} כלים · ${n("build")} לבנייה · ${n("student")} לתלמיד`;
}
/* "סמן את המומלצים" / "נקה" לקבוצה — על היעד שנבחר בשורת הסינון (בנייה/תלמיד/שניהם).
   "סמן את המומלצים" מוסיף ואינו מוריד סימון קיים; "נקה" מוריד את כל הקבוצה. */
function toolsGroupMark(groupIndex, mode) {
  const group = TOOLS.groups[groupIndex];
  const targets = TOOLS_FILTER.scope === "both" ? TOOLS_TARGETS : [TOOLS_FILTER.scope];
  for (const t of TOOLS.tools.filter((x) => x.group === group)) {
    for (const target of targets) {
      if (mode === "clear") TOOLS_DRAFT[target].delete(t.id);
      else if (t.recommended) TOOLS_DRAFT[target].add(t.id);
      const box = document.getElementById(`tool-${target}-${t.id}`);
      if (box) box.checked = TOOLS_DRAFT[target].has(t.id);
    }
  }
  toolsRefreshHeader();
}
function toolsCancel() {
  TOOLS_DRAFT = { build: new Set(TOOLS.selection.build), student: new Set(TOOLS.selection.student) };
  renderCurrent();
}
async function toolsSave() {
  if (!TOOLS_DRAFT || !toolsDirty()) return;
  const body = { build: [...TOOLS_DRAFT.build], student: [...TOOLS_DRAFT.student] };
  try {
    const r = await put("/tools/selection", body);
    toast(`הבחירה נשמרה: ${r.summary.build.count} לבנייה · ${r.summary.student.count} לתלמיד`);
  } catch (e) { toast("השמירה נכשלה: " + e.message); return; }
  await loadTools();
}

function toolRowHtml(t) {
  const box = (target, label) => `<input type="checkbox" id="tool-${target}-${esc(t.id)}" aria-label="${esc(label)}: ${esc(t.title_he)}"${TOOLS_DRAFT[target].has(t.id) ? " checked" : ""} onchange="toolsToggle('${esc(t.id)}','${target}',this.checked)">`;
  const [cls, label, title] = TOOLS_RISK[t.risk] || ["", t.risk, ""];
  const risk = `<span title="${esc(title)}">${UI.pill(cls, label)}</span>`;
  let size;
  if (t.packed) size = `<span title="${esc(t.size_source)}">${UI.status("ok", "ארוז")}</span>`;
  else if (t.size_kb != null) size = `<span title="${esc(t.size_source)}">${esc(toolsSizeText(t.size_kb))}</span>`;
  else size = `<span class="muted" title="${esc(t.size_source)}">לא נמדד</span>`;
  const rec = t.recommended ? ` <span class="pill info" title="בשורת 'ההמלצה שלי' של הקבוצה">מומלץ</span>` : "";
  const moved = t.moved_from ? `<span class="sub">מקבוצת "${esc(t.moved_from)}" במסמך (הכרעת נדב 17/09)</span>` : "";
  return { attrs: `data-tool="${esc(t.id)}"`, cells: [
    box("build", "בנייה/שיכפול"), box("student", "לתלמיד (v2)"),
    `<span class="name">${esc(t.title_he)}${rec}</span><span class="sub">${esc(t.what)}</span>${moved}`,
    `<span class="mono">${esc(t.binary)}</span>`, risk, size] };
}
function toolsGroupsHtml() {
  const cols = [{ html: `<span title="ייארז ל-initrd של מחשבי הבנייה והשיכפול">בנייה/שיכפול</span>` },
                { html: `<span title="מחשב תלמיד = v2; הבחירה נשמרת כבר עכשיו">לתלמיד (v2)</span>` }, "כלי", "בינארי", "סיכון", "גודל"];
  return TOOLS.groups.map((g, i) => {
    const rows = TOOLS.tools.filter((t) => t.group === g && toolsVisible(t)).map(toolRowHtml);
    const table = UI.datagrid({ cls: "tools", columns: cols, rows, empty: "אין כלים בקבוצה הזו שמתאימים לסינון" });
    const acts = `<button class="btn sm" onclick="toolsGroupMark(${i},'recommended')">סמן את המומלצים</button><button class="btn sm" onclick="toolsGroupMark(${i},'clear')">נקה</button>`;
    return `<div class="c12 card"><div class="card-h"><span>${esc(g)} <small id="tools-grp-${i}">${esc(toolsGroupCaption(g))}</small></span><div class="acts">${acts}</div></div><div class="card-b${rows.length ? " flush" : ""}">${table}</div></div>`;
  }).join("");
}
function toolsBarHtml() {
  const f = TOOLS_FILTER, opt = (v, l, cur) => `<option value="${v}"${v === cur ? " selected" : ""}>${esc(l)}</option>`;
  const risks = [["", "כל הסיכונים"], ["ro", "קריאה בלבד (ro)"], ["rw", "משנה (rw)"], ["destroy", "מוחק (destroy)"]];
  const scopes = [["build", "בנייה/שיכפול"], ["student", "תלמיד"], ["both", "שניהם"]];
  return `<div class="dg-bar"><input type="search" value="${esc(f.q)}" placeholder="חיפוש: שם, מה זה עושה, בינארי…" aria-label="חיפוש כלי" oninput="toolsFilter('q',this.value)" style="width:240px"><select aria-label="סיכון" onchange="toolsFilter('risk',this.value)">${risks.map(([v, l]) => opt(v, l, f.risk)).join("")}</select><label class="chk"><input type="checkbox"${f.rec ? " checked" : ""} onchange="toolsFilter('rec',this.checked)"> רק מומלצים</label><label class="chk"><input type="checkbox"${f.packed ? " checked" : ""} onchange="toolsFilter('packed',this.checked)"> רק ארוזים</label><span class="sp"></span><label class="chk">סמן/נקה קבוצה עבור: <select aria-label="היעד של פעולות הקבוצה" onchange="toolsFilter('scope',this.value)">${scopes.map(([v, l]) => opt(v, l, f.scope)).join("")}</select></label><span class="n" id="tools-count">${esc(toolsCountText())}</span></div>`;
}
function toolsPage() {
  if (!TOOLS && !toolsError) return pagePlaceholder();
  const dirty = toolsDirty();
  const sub = TOOLS
    ? esc(`${TOOLS.tools.length} כלים ב-${TOOLS.groups.length} קבוצות לפי מצב שימוש · מה שמסומן ייארז ל-initrd (שלב 2) · תלמיד = v2, הבחירה נשמרת כבר עכשיו`)
    : `‏/tools/catalog לא נקרא: ${esc(toolsError)}`;
  const pill = TOOLS ? `<span class="pill info" id="tools-pill">${esc(toolsPillText())}</span><span id="tools-state">${dirty ? UI.pill("warn", "שינויים לא נשמרו") : ""}</span>` : UI.pill("err", "לא נקרא");
  const actions = (TOOLS ? `<button class="btn primary" id="tools-save" onclick="toolsSave()"${dirty ? "" : " disabled"}>שמור</button><button class="btn" id="tools-cancel" onclick="toolsCancel()"${dirty ? "" : " hidden"}>בטל שינויים</button>` : "")
    + `<button class="btn" onclick="loadTools()">${uiIcon("refresh")} רענון</button>`;
  const header = UI.objHeader({ crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "תשתית" }, { label: "ארגז כלים" }],
    icon: "tools", name: "ארגז כלים", sub, pill, actions });
  let body;
  if (!TOOLS) body = UI.note("err", `לא הצלחתי לקרוא את קטלוג הכלים: ${esc(toolsError)}`);
  else body = `<div class="c12 card">${toolsBarHtml()}</div><div id="tools-groups" style="display:contents">${toolsGroupsHtml()}</div>`
    + `<div class="c12">${UI.note("info", `${UI.pill("", "קריאה")} מציג ולא משנה · ${UI.pill("warn", "משנה")} שינוי הפיך · ${UI.pill("err", "מוחק")} מוחק נתונים — יופעל רק אחרי הקלדת שם המחשב (עיקרון 7). ${UI.status("ok", "ארוז")} = הבינארי כבר ב-initrd היום (תוספת 0). הגדלים הם גודל חבילה מותקנת <b>עם המקור ב-tooltip</b> — לא תוספת נמדדת ל-initrd הדחוס; "לא נמדד" נספר בנפרד ואינו מקופל ל-0.`)}</div>`;
  return `<div class="page">${header}<div class="body">${body}</div></div>`;
}

const pages = {
  settings: { crumb: "הגדרות", title: "הגדרות", tabs: [], render: settings, load: loadSettingsData, own: true },   // ‏#954 גל 6
  // ‏#954 גל 7: כותרת אובייקט + datagrid (UI.*, כמו drivers.js) — שרתים/העברות/קבוצות.
  branches: { crumb: "סניפים", title: "סניפים", tabs: ["שרתים", "העברות", "קבוצות"], render: (i) => branchesPage(i), load: () => loadBranchesData(), own: true },
  // ‏#936: הדף של משני אחד (נבחר בעץ, BRANCH_NODE) — אותם נתונים ואותן
  // פונקציות של "סניפים" (branches.js), לפי לשונית.
  branch: {crumb:"שרת משני", title:"שרת משני", desc:"מצב חיבור, המחשבים שלו, ספריית האימג'ים של המשני וההעברות בשני הכיוונים — כפי שהראשי מדד מולו", tabs: BRANCH_VIEWS.map((v) => v[1]), render:pagePlaceholder},
  // ‏#954: הדף מצייר את הכותרת והלשוניות שלו (own) לפי שפת העיצוב החדשה; הלשוניות בפועל לפי תפקיד (homeTabs).
  home: { crumb: "סקירה כללית", title: "סקירה כללית", tabs: ["סיכום", "משימות", "אירועים"], render: home, load: loadHome, own: true },
  images: { crumb: "אימג'ים", title: "ספריית אימג'ים", tabs: imagesTabs(), render: images, load: loadImages, own: true },
  deploy: { crumb: "סבב הפצה", title: "סבב הפצה", tabs: deployTabs(), render: deploy, load: loadDeploy, own: true },
  machines: { crumb: "מחשבים", title: "מחשבים", tabs: ["כל המחשבים", "נראו ברשת", "דיסקים אדומים"], render: machines, load: loadMachines, own: true },
  health: { crumb: "בריאות ושירותים", title: "בריאות ושירותים", tabs: [], render: health, load: loadHealth, own: true },   // ‏#954 גל 5: בלי לשוניות — בדיקות + עדכון
  // ‏#954 גל 8: רשת כאובייקט — תרשים · חיבורים פיזיים · רשת הפצה · פורטים (=pages.ports). deploy: לא מוצג (pageAllowed).
  network: { crumb: "רשת", title: "רשת", tabs: NET_TABS, render: (i) => networkPage(i), load: loadNetwork, own: true },
  permissions: { crumb: "הרשאות", title: "הרשאות", tabs: [], render: permissions, load: loadUsersData, own: true },   // ‏#954 גל 6: טבלה אחת + מטריצה
  logs: { crumb: "יומן", title: "יומן", tabs: [], render: logs, load: loadJournalData, own: true },   // ‏#954 גל 6: יומן אחד עם סינון בדף
  monitor: { crumb: "מוניטור", title: "מוניטור", tabs: [], render: monitorPage, load: loadMonitor, own: true },   // ‏#954 גל 6: הרשימה כטבלה
  drivers: { crumb: "דרייברים", title: "דרייברים", tabs: ["חבילות", "כיסוי לפי מכונה"], render: (i) => driversPage(i), load: () => loadDrivers(), own: true },   // ‏#954 גל 6; lazily: drivers.js loads after this file
  tools: { crumb: "ארגז כלים", title: "ארגז כלים", tabs: [], render: toolsPage, load: loadTools, own: true },   // ‏#649 שלב 1: קבוצה = כרטיס, ☐ בנייה/שיכפול | ☐ תלמיד
  storage: { crumb: "אחסון", title: "אחסון", tabs: [], render: () => storagePage(), load: () => loadStorage(), own: true },   // ‏#1066 שלב ב': מיקומי הספרייה (storage.js, נטען אחרי הקובץ הזה)
  ports: { crumb: "פורטים", title: "פורטים", tabs: ["חיבורים פיזיים", "רשת הפצה", "פורטים"], render: ports, load: loadPorts, own: true },   // ‏#954 גל 5: מתג בכל שורה
};
let current = "home";
let currentTab = 0;
let searchQuery = "";

function tabRender(pageId, index) {
  const renderers = {
    branch: BRANCH_VIEWS.map(([view]) => () => `<div id="branch-view" class="stack" data-view="${view}">${pagePlaceholder()}</div>`),
  };
  const fn = renderers[pageId]?.[index];
  return fn ? fn() : `<div class="card"><div class="card-b">אין תוכן עבור הכרטיס הזה.</div></div>`;
}

function layout(page, index = 0) {
  if (page.own) return page.render(index);   // ‏#954: עמוד שנבנה מחדש מצייר כותרת+לשוניות בעצמו
  const stale = overviewError && ["home","deploy"].includes(current) ? `<div class="notice warn" role="status">${esc(overviewError)}</div>` : "";
  return `<div class="breadcrumbs"><span>שרת אימג'ים</span><span class="sep">/</span><strong>${page.crumb}</strong></div><div class="toolbar"><div class="title-block"><h1>${page.title}</h1><p>${page.desc}</p></div><div class="toolbar-actions"><button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button><div class="rel"><button class="btn primary" id="pageActionBtn" aria-haspopup="menu" aria-expanded="false" onclick="openAction()">+ פעולה</button><div id="pageActionMenu" class="action-menu" role="menu"></div></div></div></div><div class="vcenter-tabs" role="tablist">${page.tabs.map((t, i) => `<button type="button" class="vcenter-tab ${i === index ? "active" : ""}" role="tab" aria-selected="${i === index}" tabindex="${i === index ? 0 : -1}" onclick="activateTab(${i})">${t}</button>`).join("")}</div><div class="scroll">${stale}${tabRender(current, index)}<div class="footer-note">ImageCtl Console</div></div>`;
}

/* S1 (#829): הצומת של הדף הנוכחי מודגש בעץ. הצמתים הם .inventory-node
   עם data-page; הורים סגורים נפתחים כדי שההדגשה תיראה. */
function markTreeSelection(el, id) {
  document.querySelectorAll(".inventory-node.active, .inventory-node[aria-current]").forEach((x) => {
    x.classList.remove("active");
    x.removeAttribute("aria-current");
  });
  const node = el || document.querySelector(`.inventory-node[data-page="${id}"]`);
  if (!node) return;
  node.classList.add("active");
  node.setAttribute("aria-current", "page");
  for (let box = node.parentElement; box && box.classList; box = box.parentElement) {
    if (!box.classList.contains("inventory-children")) continue;
    if (box.hasAttribute("hidden") || box.style.display === "none") {
      const owner = box.previousElementSibling;
      toggleInventoryGroup(owner && owner.classList.contains("inventory-node") ? owner : null, box.id);
    }
  }
}

function selectPage(el, id) {
  if (!pageAllowed(id)) return;
  markTreeSelection(el, id);
  const page = pages[id];
  if (!page) return;
  current = id;
  currentTab = 0;
  document.getElementById("content").innerHTML = layout(page, 0);
  const search = document.getElementById("globalSearch");
  if (search) search.value = searchQuery || "";
  if (id === "network") wireNetPage();
  wireRestoredPage();
  if (page.load) page.load();
  closeSidebar();
}

function selectPageById(id) { selectPage(document.querySelector(`.inventory-node[data-page="${id}"]`), id); }

function selectInventory(el, id) {
  if (!pageAllowed(id)) return;
  document.querySelectorAll(".inventory-node").forEach((x) => x.classList.remove("active"));
  if (el) el.classList.add("active");
  const page = pages[id];
  if (!page) return;
  current = id;
  currentTab = 0;
  document.getElementById("content").innerHTML = layout(page, 0);
  const search = document.getElementById("globalSearch");
  if (search) search.value = searchQuery || "";
  if (id === "network") wireNetPage();
  wireRestoredPage();
  if (page.load) page.load();
}

function toggleInventoryGroup(nodeEl, childId) {
  const box = childId ? document.getElementById(childId) : null;
  const arrow = nodeEl && nodeEl.querySelector(".tree-arrow");
  const opening = box
    ? (box.hasAttribute("hidden") || box.style.display === "none")
    : !!(arrow && (arrow.textContent === "▸" || arrow.textContent === "›"));
  if (box) {
    if (opening) {
      box.removeAttribute("hidden");
      box.style.removeProperty("display");
    } else {
      box.setAttribute("hidden", "");
      box.style.removeProperty("display");
    }
  }
  if (arrow) arrow.dataset.open = String(opening);
  if (arrow) arrow.textContent = opening ? "▾" : "▸";
  if (nodeEl && nodeEl.setAttribute) nodeEl.setAttribute("aria-expanded", String(opening));
}

/* S4 (#829): ניווט מקלדת. הלשוניות: חצים (RTL — ימינה=הקודמת, שמאלה=הבאה),
   Home/End. העץ: Enter/Space מפעילים את מה שהעכבר היה מפעיל, חץ למטה/למעלה
   בין צמתים גלויים, שמאלה פותח קבוצה, ימינה סוגר או עולה להורה. */
function tablistKeydown(e) {
  const tab = e.target.closest && e.target.closest('[role="tab"]');
  if (!tab || !tab.parentElement) return;
  const tabs = [...tab.parentElement.querySelectorAll('[role="tab"]')];
  const i = tabs.indexOf(tab);
  const next = {ArrowRight: i - 1, ArrowLeft: i + 1, Home: 0, End: tabs.length - 1}[e.key];
  if (next === undefined || i < 0) return;
  e.preventDefault();
  const target = (next + tabs.length) % tabs.length;
  activateTab(target);
  const focusTo = document.querySelectorAll('[role="tab"]')[target];
  if (focusTo && focusTo.focus) focusTo.focus();
}

function treeVisibleNodes(tree) {
  return [...tree.querySelectorAll('.inventory-node[tabindex]')].filter((n) => {
    for (let p = n.parentElement; p && p !== tree; p = p.parentElement) {
      if (p.hasAttribute("hidden") || (p.classList && p.classList.contains("hidden"))) return false;
    }
    return !n.classList.contains("hidden");
  });
}

function treeKeydown(e) {
  const node = e.target.closest && e.target.closest(".inventory-node");
  if (!node) return;
  const tree = e.currentTarget;
  const arrow = node.querySelector(".tree-arrow");
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    const clickable = node.getAttribute("onclick") ? node
      : (node.querySelector("span[onclick]:not(.tree-arrow)") || arrow || node);
    clickable.click();
    return;
  }
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const nodes = treeVisibleNodes(tree);
    const to = nodes[nodes.indexOf(node) + (e.key === "ArrowDown" ? 1 : -1)];
    if (to) to.focus();
    return;
  }
  if (e.key === "ArrowLeft" && arrow && node.getAttribute("aria-expanded") === "false") {
    e.preventDefault();
    arrow.click();
    return;
  }
  if (e.key === "ArrowRight") {
    e.preventDefault();
    if (arrow && node.getAttribute("aria-expanded") === "true") { arrow.click(); return; }
    const box = node.parentElement && node.parentElement.classList.contains("inventory-children") ? node.parentElement : null;
    const owner = box && box.previousElementSibling;
    if (owner && owner.classList.contains("inventory-node") && owner.focus) owner.focus();
  }
}

/* S2 (#829): הסיידבר כשכבה במסך צר. aria-expanded על ההמבורגר, הפוקוס
   עובר לעץ בפתיחה וחוזר לכפתור בסגירה. במסך רחב .open אינו משנה דבר. */
function toggleSidebar(force) {
  const side = document.getElementById("sidebar");
  if (!side) return;
  const wasOpen = side.classList.contains("open");
  const open = force === undefined ? !wasOpen : !!force;
  if (open === wasOpen) return;
  side.classList.toggle("open", open);
  const backdrop = document.getElementById("sidebarBackdrop");
  if (backdrop) backdrop.classList.toggle("open", open);
  const btn = document.querySelector(".nav-toggle");
  if (btn) btn.setAttribute("aria-expanded", String(open));
  if (open) {
    const first = side.querySelector(".inventory-node.active") || side.querySelector(".inventory-node");
    if (first && first.focus) first.focus();
  } else if (btn && btn.focus && document.activeElement && side.contains && side.contains(document.activeElement)) {
    btn.focus();  // רק כשהשכבה באמת הייתה פתוחה — במסך רחב לא מגיעים לכאן
  }
}
function closeSidebar() { toggleSidebar(false); }

function toggleTaskPanel() { document.getElementById("taskPanel").classList.toggle("open"); }

function activateTab(index) {
  currentTab = Number(index) || 0;
  const page = pages[current];
  if (!page) return;
  document.getElementById("content").innerHTML = layout(page, currentTab);
  const search = document.getElementById("globalSearch");
  if (search) search.value = searchQuery || "";
  if (current === "network") wireNetPage();
  wireRestoredPage();
}

function refreshPage() {
  const page = pages[current];
  if (!page) return;
  if (page.load) { wireRestoredPage(); page.load(); }
  else renderCurrent();
  const search = document.getElementById("globalSearch");
  if (search) search.value = searchQuery || "";
  toast("הנתונים עודכנו");
}

function openAction() {
  if (!isAdmin()) { selectPageById("images"); return; }
  const menu = document.getElementById("pageActionMenu");
  if (!menu) return;
  const actions = {
    images: [["אימג׳ חדש", "openCapture().catch(e => toast(e.message))"], ["קליטת אימג׳", "openImageIngest()"], ["אימות ספרייה", "soon()"]],
  };
  menu.innerHTML = (actions[current] || []).map(([label, fn]) =>
    `<button type="button" role="menuitem" onclick="${fn};closeActionMenu()">${label}</button>`).join("");
  menu.style.display = "block";
  const btn = document.getElementById("pageActionBtn");
  if (btn) btn.setAttribute("aria-expanded", "true");
  const first = menu.querySelector("button");
  if (first && first.focus) first.focus();
}

function closeActionMenu() {
  const menu = document.getElementById("pageActionMenu");
  if (menu) menu.style.display = "none";
  const btn = document.getElementById("pageActionBtn");
  if (btn) btn.setAttribute("aria-expanded", "false");
}

function openDrawer(title, body) {
  drawerGeneration++;
  document.getElementById("drawerTitle").textContent = title;
  document.getElementById("drawerBody").innerHTML = body;
  document.getElementById("drawer").classList.add("open");
  document.getElementById("detailBackdrop").style.display = "block";
}

function closeDrawer() {
  drawerGeneration++;
  document.getElementById("drawer").classList.remove("open");
  document.getElementById("detailBackdrop").style.display = "none";
}

function closeModal() {
  const modal = document.getElementById("modal");
  modal.style.display = "none";
  modal.classList.remove("show", "sheet-mode");
  const form = document.getElementById("sheet");
  if (form) {
    form.classList.add("hidden");
    form.onsubmit = null;
  }
}

function toast(message, ms = 2600) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.classList.add("show");
  el.style.display = "block";
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => {
    el.classList.remove("show");
    el.style.display = "none";
  }, ms);
}

// #857: הוספה/מחיקה/ייבוא של מכונה מחזירים `network` — האם known-macs
// ו-dnsmasq עודכנו. ה-DB נשמר גם כשלא; המפעיל חייב לדעת שהמכונה לא
// תקבל GRUB (fail-closed של #141) עד שהרשת תתוקן. מחזיר true כשהוצג.
function dhcpNotice(r) {
  if (!r || !r.network || r.network.applied !== false) return false;
  toast("המכונה נשמרה, אבל ה-DHCP לא עודכן — לא תקבל GRUB עד תיקון: "
    + (r.network.error || "סיבה לא ידועה"), 9000);
  return true;
}

function openModalContent(title, body, okLabel = "אישור", okFn = "closeModal()") {
  document.getElementById("modalTitle").textContent = title;
  document.getElementById("modalBody").innerHTML = body;
  const ok = document.getElementById("modalOk");
  ok.textContent = okLabel;
  ok.disabled = false;
  ok.onclick = typeof okFn === "function" ? okFn : new Function(okFn);
  const modal = document.getElementById("modal");
  const form = document.getElementById("sheet");
  if (form) form.classList.add("hidden");
  modal.classList.remove("sheet-mode");
  modal.classList.add("show");
  modal.style.display = "flex";
}

function downloadFile(name, text, type) {
  const blob = new Blob([text], { type });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function doGlobalSearch(value) {
  searchQuery = value;
  const q = value.trim().toLowerCase();
  if (!q) {
    document.querySelectorAll(".table tbody tr").forEach((r) => { r.style.display = ""; });
    return;
  }
  let hits = 0;
  document.querySelectorAll(".table tbody tr").forEach((r) => {
    const match = r.textContent.toLowerCase().includes(q);
    r.style.display = match ? "" : "none";
    if (match) hits++;
  });
  toast("חיפוש: " + value + " · " + hits + " תוצאות בעמוד");
}

function openUserMenu() { document.getElementById("userMenu").classList.toggle("open"); }
function closeUserMenu() { document.getElementById("userMenu").classList.remove("open"); }

function toggleSidebarInfo() {
  openDrawer("ניווט ImageCtl", `<div class="notice">הניווט מחולק לפי משימות תפעוליות: ספרייה → הפצה → מחשבים → בריאות → רשת → הרשאות → יומן.</div>`);
}

/* ‏#915: המגירה מציגה בדיוק את מה שהתג בסרגל סופר (updateAlertBadge):
   מכונות שנכשלו ובדיקות בריאות במצב אזהרה/תקלה. תג בלי רשימה שמסבירה
   אותו הוא מספר שאי אפשר לאמת. */
function topNotifications() {
  const rows = [];
  for (const m of MACHINES || []) {
    if (!machineIsFailed(m)) continue;
    rows.push(`<div class="list-row"><div class="list-main"><strong>${esc(machineName(m) || m.mac)}</strong><small>מכונה במצב כשל</small></div><span class="status err"><i></i>נכשל</span></div>`);
  }
  for (const c of (Array.isArray(HEALTH) ? HEALTH : [])) {
    if (c.state !== "warn" && c.state !== "bad") continue;
    rows.push(`<div class="list-row"><div class="list-main"><strong>${esc(c.label)}</strong><small>${esc(c.detail || "")}</small></div><span class="status ${healthStatusClass(c.state)}"><i></i>${esc(healthStatusLabel(c.state))}</span></div>`);
  }
  const body = rows.length
    ? `<div class="list">${rows.join("")}</div>`
    : `<div class="empty">אין התראות</div>`;
  openDrawer("התראות", body);
}

function help() {
  openDrawer("עזרה מהירה", `<div class="section-title">איך משתמשים במערכת?</div><div class="list"><div class="list-row"><div class="list-main"><strong>1. קליטת אימג׳</strong><small>העלה אימג׳ ואמת manifest + SHA-256.</small></div></div><div class="list-row"><div class="list-main"><strong>2. פתיחת סבב</strong><small>בחר אימג׳, כיתה וכלל התחלה.</small></div></div><div class="list-row"><div class="list-main"><strong>3. מעקב</strong><small>עקוב אחרי הצטרפות, כתיבה וכשלים לכל תחנה.</small></div></div><div class="list-row"><div class="list-main"><strong>4. מחשב שנכשל</strong><small>בודד אותו, בדוק PXE/WoL, וטפל בו בלי לעצור את הסבב.</small></div></div></div>`);
}

function openAccount() {
  closeUserMenu();
  const name = esc(ME && ME.username || "");
  const role = ME && ME.role === "admin" ? "מנהל" : "הפצה";
  const builtin = !!(ME && ME.is_builtin);
  const mfaOn = !!(ME && ME.mfa_enabled);
  let mfa;
  if (builtin) mfa = `<span class="pill">מקומי · ללא MFA</span>`;
  else if (mfaOn) mfa = UI.status("ok", "פעיל")
    + ` <button class="btn sm" onclick="accountNewBackupCodes()">צור קודי גיבוי חדשים</button>`;
  else if (ME && ME.role === "admin")
    mfa = UI.status("", "כבוי") + ` <button class="btn sm" onclick="accountEnableMfa()">הפעל</button>`;
  else mfa = UI.status("", "כבוי");
  openDrawer("המשתמש שלי", `<div class="detail-grid"><div class="detail-box"><span class="k">משתמש</span><span class="v">${name}</span></div><div class="detail-box"><span class="k">תפקיד</span><span class="v">${role}</span></div><div class="detail-box"><span class="k">MFA</span><span class="v">${mfa}</span></div><div class="detail-box"><span class="k">מצב</span><span class="v success-text">מחובר</span></div></div>`);
}

async function accountEnableMfa() {
  try {
    const setup = await post("/me/mfa/setup");
    sheet({
      title: "הגדרת אימות דו-שלבי",
      sub: "הקלד את הסוד באפליקציה ואז את הקוד שהיא מציגה.",
      note: `<p class="mono" dir="ltr">${esc(secretBlocks(setup.secret || ""))}</p>`
        + (setup.otpauth_url ? `<p><a class="mono" href="${esc(setup.otpauth_url)}">${esc(setup.otpauth_url)}</a></p>` : ""),
      fields: [{ id: "code", label: "הקוד מהאפליקציה" }],
      submitLabel: "אימות והפעלה",
      onSubmit: async (v) => {
        await post("/me/mfa/verify", { code: v.code });
        const en = await post("/me/mfa/enable", { code: v.code });
        if (ME) ME.mfa_enabled = true;
        showBackupCodesDrawer(en.backup_codes || []);
      },
    });
  } catch (e) { toast(e.message); }
}

function accountNewBackupCodes() {
  sheet({
    title: "קודי גיבוי חדשים",
    sub: "הקוד מאפליקציית האימות. הקודים הישנים יבוטלו.",
    fields: [{ id: "code", label: "הקוד מהאפליקציה" }],
    submitLabel: "צור קודים",
    onSubmit: async (v) => {
      const en = await post("/me/mfa/enable", { code: v.code });
      showBackupCodesDrawer(en.backup_codes || []);
    },
  });
}

function showBackupCodesDrawer(codes) {
  const list = (codes || []).map((c) => `<span class="mono">${esc(c)}</span>`).join("<br>");
  openDrawer("קודי גיבוי", `<p>כל קוד עובד פעם אחת. הם לא יוצגו שוב.</p><div class="backup" style="margin-top:10px">${list}</div>`
    + `<div class="action-strip"><button class="btn sm" onclick='copyBackupCodes(${JSON.stringify(codes || [])})'>העתק</button>`
    + `<button class="btn sm" onclick='printBackupCodes(${JSON.stringify(codes || [])})'>הדפס</button></div>`);
}

function copyBackupCodes(codes) {
  const text = (codes || []).join("\n");
  if (navigator.clipboard && navigator.clipboard.writeText)
    navigator.clipboard.writeText(text).then(() => toast("הקודים הועתקו"), () => toast("ההעתקה נכשלה"));
  else toast("ההעתקה אינה זמינה");
}

function openSessionInfo() {
  closeUserMenu();
  const name = esc(ME && ME.username || "");
  // ‏#703: טביעת האצבע המלאה של תעודת הקונסולה — להשוואה מול "פרטי תעודה" בדפדפן.
  const fp = ME && ME.tls && ME.tls.fingerprint_sha256;
  const tls = fp
    ? `<div class="detail-box"><span class="k">TLS</span><span class="v">תעודה עצמית · SHA-256 <span dir="ltr" style="word-break:break-all">${esc(fp)}</span></span></div>`
    : `<div class="detail-box"><span class="k">TLS</span><span class="v">כבוי (loopback)</span></div>`;
  openDrawer("פרטי Session", `<div class="detail-grid"><div class="detail-box"><span class="k">משתמש</span><span class="v">${name}</span></div><div class="detail-box"><span class="k">Session</span><span class="v">Authenticated</span></div>${tls}</div>`);
}

function logout() {
  closeUserMenu();
  post("/logout").then(() => { ME = null; showLogin(); }).catch(() => { ME = null; showLogin(); });
}

function soon() { toast("בקרוב"); }

/* #906: המכונה עומדת על שאלה לאדם (SMART/אדום, מסך FAILED) — ה-hello
   ממשיך עם `prompt`, והשאלה מוצגת כאן במקום "לא נראתה". null = לא ממתינה.
   #908/#912: המסכים של מחשב הבנייה שולחים מילה קבועה (הסוכן מדפיס ASCII
   בלבד) — כאן היא מתורגמת; כל טקסט אחר הוא השורה שעל המסך, ומוצג כמו שהוא. */
const PROMPT_HE = { menu: "תפריט", signin: "כניסה" };
function waitingHtml(m) {
  if (!m.prompt) return "—";
  const he = PROMPT_HE[m.prompt];
  return he ? `ממתין למפעיל: ${he}` : `ממתין למפעיל: <span dir="ltr">${esc(m.prompt)}</span>`;
}

/* #927: אותה אזהרת shrink-restore שב-captureWarningHtml, הפעם ליד
   המחשב עצמו — זה מי שהדיסק שלו נשאר מכווץ. המשימה האחרונה של ה-MAC
   הזה בלבד (CAPTURE_TASKS ממוין created_at DESC מהשרת). */
function machineCaptureWarningHtml(mac) {
  const t = (CAPTURE_TASKS || []).find((x) => x.mac === mac && x.state === "done" && x.error);
  if (!t) return "";
  return `<div class="section-title">אזהרת קליטה אחרונה</div><div class="notice warn">${esc(t.error)}</div>`;
}



function createImage() { soon(); }
function openNewImage() { soon(); }
function verifyLibrary() { soon(); }
function exportRounds() { soon(); }
function runHealthCheck() { soon(); }
function openServicesDrawer() { soon(); }
function testNetwork() { soon(); }
function rollbackNetwork() { soon(); }

function init() {
  const search = document.getElementById("globalSearch");
  if (search) search.addEventListener("input", (e) => doGlobalSearch(e.target.value));
  document.getElementById("modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  document.getElementById("detailBackdrop").addEventListener("click", closeDrawer);
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".user-menu") && !e.target.closest(".user")) closeUserMenu();
    if (!e.target.closest("#pageActionMenu") && !e.target.closest("#pageActionBtn")) closeActionMenu();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeModal(); closeDrawer(); closeUserMenu(); closeSidebar(); closeActionMenu(); }
  });
  document.getElementById("content").addEventListener("keydown", tablistKeydown);
  const tree = document.querySelector(".tree");
  if (tree) tree.addEventListener("keydown", treeKeydown);
}

window.addEventListener("DOMContentLoaded", () => {
  // המטמון (אם יש) כבר הוחל ב-inline שב-index.html. כאן מעדכנים את
  // הכפתור, ואחרי /me — את העדפת השרת (auto מוחק את המטמון).
  const cached = localStorage.getItem(THEME_KEY);
  applyTheme(cached === "dark" || cached === "light" ? cached : "auto");
  loadLogo();
  init();
  api("/me").then((me) => { ME = me; return showApp(); }).catch((e) => {
    showLogin();
    if (e && e.status === 403 && e.message === "password_change_required")
      loginRender({ screen: "change", error: "" });
    else if (e && e.status === 403 && e.message === "mfa_enrollment_required")
      loginStartSetup({ screen: "setup" });
  });
});

/* Restored baseline actions; existing API contracts and sheet confirmations. */
function stuckNote(stuck, mac) {
  const s = stuck && stuck[mac];
  if (!s) return "";
  return s.blocked
    ? `נכשל באתחול ${s.attempts} פעמים — נשלח לדיסק המקומי`
    : `אתחל ${s.attempts} פעמים ולא הצטרף`;
}

/* בריאות SMART וההכרעה על דיסק פגום (#652), לצפייה בלבד. הכותרת היא
   "דיסק N" לפי החריץ (port) ולא שם ההתקן — אותה מוסכמה של הסוכן. רק
   דיסק שאיננו תקין או שנעשתה עליו הכרעה מוצג, כדי לא להציף.
   ‏#872: שלושה צבעים — failed_last (נכשל בשיכפול הקודם) אדום, fail
   (בריאות FAILED) כתום, ok/unchecked ירוק; warn מסוכן ישן — כתום. */
const SMART_HE = {ok: "תקין", warn: "SMART אזהרה", fail: "SMART תקלה",
                  failed_last: "נכשל בשיכפול הקודם", unchecked: "לא נבדק"};
const DECISION_HE = {replace: "להחלפה", rescue: "נכתב בכוח", skip: "דולג"};
function disksHtml(disks) {
  if (!Array.isArray(disks) || !disks.length) return "";
  const chips = disks
    .filter((d) => d.verdict !== "ok" || d.decision)
    .map((d) => {
      const name = d.disk_number != null ? `דיסק ${d.disk_number}` : esc(d.disk);
      const health = SMART_HE[d.verdict] || esc(d.verdict);
      const hint = d.reason === "crc" ? " (בדוק כבל)" : "";
      const dec = d.decision ? ` · ${DECISION_HE[d.decision] || esc(d.decision)}` : "";
      return `<span class="disk-smart ${esc(d.verdict)}">${name}: ${health}${hint}${dec}</span>`;
    });
  return chips.length ? `<div class="disks">${chips.join("")}</div>` : "";
}

/* #720: תוצאת ה-staging של הדרייברים בדיווח הסיום. null = השלב לא דיווח
   (סוכן ישן) — לא מוצג; `failed` הוא "הושלם, דרייברים לא הונחו" — מצב
   גלוי ליד המחשב, לא שורת יומן אבודה (עיקרון 5). */
function driversHtml(d) {
  if (!d) return "";
  if (d.state === "staged") return `<div class="sub">דרייברים הונחו: ${esc((d.packages || []).join(", "))}</div>`;
  if (d.state === "no_match") return `<div class="sub">דרייברים: אין חבילה תואמת לחומרה</div>`;
  if (d.state === "skipped") return "";
  return `<div class="err">דרייברים לא הונחו: ${esc(d.error || d.state)}</div>`;
}

/* #856: החבר של ה-MAC הזה בסבב המוצג שהסתיים `done` ועדיין נושא `error`
   — "הושלם, עם אזהרה" — במגירת התחנה. אותו .notice.warn כמו בכרטיס. */
function machineRestoreWarningHtml(mac) {
  const m = (OVERVIEW?.session?.members || []).find((x) => x.mac === mac && (x.done || x.state === "done") && x.error);
  if (!m) return "";
  return `<div class="section-title">אזהרת שחזור אחרונה</div><div class="notice warn">${esc(m.error)}</div>`;
}

function memberRow(m, session, note = "") {
  const progress = Progress.view(m);
  const cls = m.done || m.state === "done" ? "done" : m.state === "failed" ? "failed" : "";
  // ‏#856: `done` שעדיין נושא `error` הוא "הושלם, עם אזהרה" — שם המחשב לא
  // נכתב (hostname.sh), ההרחבה נדחתה (#648) — לא כשל. עד כאן כל `error`
  // נצבע אדום (.err) גם מתחת לכרטיס ירוק, כמו כונן שנכשל. כתום — אותו
  // .notice.warn של captureWarningHtml (#927); אדום נשאר לכשל בלבד.
  const err = !m.error ? "" : cls === "done"
    ? `<div class="notice warn">${esc(m.error)}</div>` : `<div class="err">${esc(m.error)}</div>`;
  // מזוהה בשם המחשב שייכתב לו; מכונה שאינה רשומה נופלת חזרה ל-MAC.
  const label = m.hostname || m.name || m.mac;
  const single = session.kind === "unicast"
    ? `<span class="tag">יוניקאסט</span>`
    : session.single ? `<span class="tag">תחנה בודדת</span>` : "";
  return `<div class="member ${cls}">
    <div class="who-line" title="${esc(m.mac)}">
      <b>${esc(label)}</b>${single}
      ${m.hostname ? "" : `<span class="mono sub-mac">${esc(m.mac)}</span>`}
    </div>
    ${Progress.bar(m)}
    <span class="pct">${m.state === "waiting" ? "ממתין" : progress.label}</span>
    ${disksHtml(m.disks)}
    ${err}
    ${driversHtml(m.drivers)}
    ${note && !m.done ? `<div class="err">${esc(note)}</div>` : ""}
  </div>`;
}

/* משיכות יוניקאסט — תחנות שמושכות אימג' ב-HTTP, במקביל לשידור (#60).

   הן אינן "הסבב": אין להן קידומת, אין מי שמצטרף אליהן, והן אינן תופסות
   את חריץ השידור. הן כן עבודה אמיתית על השרת — ולכן הן מוצגות. שרת
   ששתי תחנות מושכות ממנו לא ייראה פנוי. */
function pullsHtml(pulls, afterRound) {
  if (!pulls.length) return "";
  const rows = pulls.map((p) => `<div class="pull">
      <div class="sub">מושכת: <b>${esc(p.image_name)}</b></div>
      ${p.members.map((m) => memberRow(m, p)).join("")}
    </div>`).join("");
  return `${afterRound ? `<div class="divide"></div>` : ""}
    <div class="sub">משיכות יוניקאסט (${pulls.length}) — אינן תופסות את חריץ השידור</div>
    <div class="session-members">${rows}</div>`;
}

let SESSION_MACHINES = { group: null, list: [] };
async function sessionClassMachines(groupId) {
  if (SESSION_MACHINES.group !== groupId) {
    try {
      SESSION_MACHINES = {
        group: groupId,
        list: await api("/machines?group=" + encodeURIComponent(groupId)),
      };
    } catch (error) { return null; }   // null = לא נקרא, נבדל מרשימה ריקה (#517)
  }
  return SESSION_MACHINES.list;
}

/* #927: קליטה שהצליחה אבל שההחזרה של דיסק המקור לגודלו נכשלה (#87,
   ‏agent/lib/shrink.sh) מגיעה לשרת כמשימה `done` עם `error` לא-ריק
   (‏reports.py: ה-COALESCE שומר את אזהרת ה-shrink גם אחרי שהמניפסט
   סגר את המשימה). עד כאן `loadCaptures` סינן רק pending/running, אז
   ברגע שהמשימה עברה ל-done האזהרה נעלמה בלי שהמפעיל ראה שדיסק הבנייה
   נשאר מכווץ. כתום ולא אדום (#874: אזהרה, לא כשל קליטה) — אותם
   ‏var(--warn-*) של `.notice.warn` הקיים, בלי CSS חדש. */
function captureWarningHtml(t) {
  if (t.state !== "done" || !t.error) return "";
  return `<div class="note warn"><span aria-hidden="true">●</span><span><b>${esc(t.name)}</b>: ${esc(t.error)}</span></div>`;
}

async function loadCaptures() {
  // ‏CAPTURE_TASKS מחזיק את כל 20 המשימות האחרונות (לא רק הפעילות) —
  // openMachineDetail, renderActivity והספרייה מסננים כל אחד לפי מה שהוא צריך.
  const all = await api("/tasks");
  if (!Array.isArray(all)) throw new Error("/tasks: תשובה שאינה רשימה");   // לא נקרא ≠ אין קליטות (עיקרון 5)
  CAPTURE_TASKS = all;
  CAPTURE_TASKS_READ = true;
  renderActivity();
  // ‏#954 גל 3א: אובייקט הבנייה/השיכפול הפתוח מתרענן כשקליטה משתנה.
  if (typeof groupTasksChanged === "function" && groupTasksChanged(all)) renderCurrent();
  // ‏#927: קליטה שהסתיימה עם אזהרה (המקור לא הוחזר לגודלו) אינה נעלמת —
  // הודעה מעל טבלת הספרייה. קליטה בתהליך היא שורה בטבלה (#954 גל 2), לא כאן.
  const bar = $("#capture-bar");
  if (bar) bar.innerHTML = all.filter((t) => t.state === "done" && t.error).map(captureWarningHtml).join("");
  if (typeof imagesTasksChanged === "function" && imagesTasksChanged(all)) renderCurrent();
}

async function openCapture(preMac = "") {
  if (!isAdmin()) return;
  try { preMac = decodeURIComponent(preMac); } catch (e) {}   // ‏גל 3א: "קלוט מכאן" מכרטיס מחשב בנייה
  /* בוחרים מחשב בנייה ואת הדיסק שהוא דיווח עליו ב-hello — כך אין
     הקלדת שם התקן, והשרת יודע מה באמת מחובר שם. */
  const machines = await api("/machines");
  const groups = await api("/groups");
  const buildIds = new Set(groups.filter((g) => g.role === "build").map((g) => g.id));
  const builders = machines.filter((m) => buildIds.has(m.group_id));
  if (!builders.length) {
    toast("אין מחשב בנייה רשום — הוסיפו אותו בלשונית המחשבים.");
    return;
  }
  const devices = await api("/net");
  const seen = new Map(devices.map((d) => [d.mac, d]));

  sheet({
    title: "קליטת אימג' חדש",
    sub: "המשימה תמתין למחשב הבנייה. הדליקו אותו ב-PXE כדי להתחיל.",
    fields: [
      {
        id: "mac", label: "מחשב בנייה", type: "select", value: preMac || undefined,
        options: builders.map((m) => ({
          value: m.mac,
          label: `${m.suffix} · ${m.mac}` + (seen.has(m.mac) ? "" : " (טרם נראה ברשת)"),
        })),
      },
      { id: "disk", label: "דיסק המקור", value: "sda", dir: "ltr" },
      { id: "name", label: "שם האימג'", placeholder: "למשל: Office 2024 — סטנדרט" },
      { id: "description", label: "תיאור (לא חובה)", type: "textarea" },
      {
        id: "folder", label: "תיקייה", type: "select", value: IMAGES_FOLDER || "",
        options: [{ value: "", label: "ללא תיקייה" }].concat(
          (FOLDERS || []).map((f) => ({ value: f.name, label: f.name }))),
      },
    ],
    submitLabel: "צור משימת קליטה",
    onSubmit: async (v) => { await post("/tasks/capture", v); await loadCaptures(); },
  });
}
function editFolderSheet(folder) {
  sheet({
    title: "עריכת תיקייה",
    sub: folder.images
      ? `שינוי השם יעדכן גם את ${folder.images} האימג'ים שבתוכה.`
      : "התיקייה ריקה.",
    fields: [
      { id: "name", label: "שם התיקייה", value: folder.name },
      { id: "description", label: "תיאור", type: "textarea", value: folder.description },
    ],
    onSubmit: async (v) => {
      const result = await put(`/folders/${encodeId(folder.name)}`,
                               { name: v.name, description: v.description });
      IMAGES_FOLDER = result.name;
      await loadImages();
      toast("נשמר.");
    },
  });
}

function addFolderSheet() {
  if (!isAdmin()) return;
  if (!isAdmin()) return;
  sheet({
  title: "תיקייה חדשה",
  fields: [
    { id: "name", label: "שם התיקייה", placeholder: "למשל: סייבר" },
    { id: "description", label: "תיאור קצר (לא חובה)", type: "textarea" },
  ],
  submitLabel: "צור תיקייה",
  onSubmit: async (v) => {
    await post("/folders", { name: v.name, description: v.description });
    await loadImages();
  },
});
}
/* ‏#954 גל 5: מתגי ה-SSH יושבים בדף הפורטים (portSshStations / portSshNic);
   הלוגיקה — confirm בפתיחה ובדלת האחרונה, ראיה חיובית (verified) — לא השתנתה. */
function sshToggle(path, enabled, word, needsConfirm, title, sub, offMeans = "") {
  const send = async (extra) => {
    const result = await put(path, { enabled, ...extra });
    if (result.apply_error) toast("ההחלה נכשלה: " + result.apply_error);
    else if (!result.verified)
      toast("נשמר — אבל מה שמאזין לא תואם. ראו את שורות ה-SSH בטבלת הפורטים.");
    else toast(enabled ? "נפתח, ואומת מול המצב בפועל" : "נסגר, ואומת מול המצב בפועל");
    await loadPorts();
  };
  if (!needsConfirm) {
    send({}).catch((error) => toast(error.message));
    return;
  }
  sheet({
    title, sub, danger: true, submitLabel: enabled ? "פתח" : "סגור",
    note: offMeans ? offMeansNote(offMeans) : "",
    verify: { label: "להמשך יש להקליד בדיוק:", mustEqual: word },
    onSubmit: () => send({ confirm: word }),
  });
}


let UPDATE_INFO = {};

function confirmUpdateAction(title, tag, path, applyTag) {
  if (!tag) return;
  sheet({
    title,
    sub: path === "apply"
      ? `השרת יעבור ל-${tag} ויופעל מחדש. הפעולה אינה הפיכה בקלות.`
      : `השרת יחזור ל-${tag} ויופעל מחדש.`,
    danger: true, submitLabel: "אישור",
    verify: { label: "להמשך יש להקליד את שם השרת:", mustEqual: UPDATE_INFO.server_name },
    onSubmit: async () => {
      const body = { confirm_name: UPDATE_INFO.server_name };
      if (path === "apply") body.tag = applyTag;
      await post("/update/" + path, body);
      toast("העדכון הופעל — עוקבים אחרי סטטוס.");
      if (current === "health") { await loadHealthUpdate(); renderCurrent(); }
    },
  });
}
function openNewUser() { sheet({
  title: "משתמש חדש",
  fields: [
    { id: "username", label: "שם משתמש" },
    {
      id: "password", label: "סיסמה (8 תווים לפחות)", type: "password",
      confirm: "אימות סיסמה",
    },
    {
      id: "role", label: "תפקיד", type: "select", value: "deploy",
      options: [
        { value: "deploy", label: "הפצה בלבד" },
        { value: "admin", label: "מנהל" },
      ],
    },
  ],
  submitLabel: "צור משתמש",
  onSubmit: async (v) => { await post("/users", v); toast(`המשתמש ${v.username} נוצר`); await loadUsersData(); },
}); }

/* ---------- #984: WoL למחשב בודד ולקבוצת הבנייה (#954 גל 6) ----------
   POST /machines/{mac}/wake · POST /groups/{gid}/wake → {sent, failed, reasons},
   כמו /room/wake (החדר נשאר wakeRoom). ‏build/cloner בלבד — תחנות כיתה = v2
   (השרת מחזיר 403). **"נשלח" ≠ "התעוררה":** חבילת WoL היא UDP בלי ACK (#528),
   ולכן הטקסט אומר נשלח. שרת ישן (אין את המסלול) = 404 "Not Found". */
function wolResultText(r, who) {
  const reasons = (r.reasons || []).length ? " — " + r.reasons.join("; ") : "";
  return `WoL נשלח ל-${who} (${r.sent})${r.failed ? `, ${r.failed} נכשלו` : ""}${reasons}`;
}
function wolErrorText(e) {
  if (e.status === 404 && /not found/i.test(e.message)) return "WoL למחשב יחיד דורש שרת חדש יותר (#984) — השרת הזה מכיר רק את WoL לחדר";
  return "WoL נכשל: " + e.message;
}
async function wakeMachine(macEnc) {
  const m = findMachine(macEnc);
  if (!m) { toast("מכונה לא נמצאה"); return; }
  if (!["build", "cloner"].includes(machineRole(m))) { toast("WoL לתחנות כיתה — v2"); return; }
  try {
    const r = await post(`/machines/${encodeId(m.mac)}/wake`);
    toast(wolResultText(r, machineName(m) || m.mac), 6000);
  } catch (e) { toast(wolErrorText(e), 6000); }
}
async function wakeGroup(gidEnc) {
  let gid = gidEnc; try { gid = decodeURIComponent(gidEnc); } catch (e) {}
  try {
    const r = await post(`/groups/${encodeId(gid)}/wake`);
    toast(wolResultText(r, groupLabel(gid)), 6000);
  } catch (e) { toast(wolErrorText(e), 6000); }
}

function isAdmin() { return !!ME && ME.role === "admin"; }
function pageAllowed(id) {
  if (!ME) return false;
  if (id === "branches") return isAdmin() && !!ME.capabilities?.interbranch_transfer;
  if (id === "branch") return isAdmin() && !!ME.capabilities?.enroll_secondary;
  if (id === "tools") return isAdmin() && toolsOn();   // v1 בלי ארגז הכלים; v1.1 מדליק
  return ["home", "images", "deploy"].includes(id) || isAdmin();
}
function wireRestoredPage() {
  if (current === "images") loadCaptures().catch(e => toast(e.message));
  if (current === "branches") wireBranchesGroupsTab();   // ‏own page: הטעינה עצמה דרך page.load (loadBranchesData)
  if (current === "branch") loadBranchView(BRANCH_VIEWS[currentTab][0]).catch(e => toast(e.message));
}
let pollTimer = null, overviewBusy = false, overviewError = "", overviewLastOk = null;
let CAPTURE_TASKS = [];
let drawerGeneration = 0;
function startStatusWatch() {
  clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    if (!ME || document.hidden) return;
    refreshStatus();
    if (isAdmin() || current === "images") loadCaptures().catch(e => toast(e.message));
    if ((current === "machines" && MACHINES_CLASS) || current === "deploy") refreshGroupLive().catch(() => {});   // ‏גל 3א/4: /room חי באובייקט המשכפלים ובחדר
  }, 2000);
}
function renderActivity() {
  const rows = [];
  const s = OVERVIEW?.session;
  if (s) rows.push([sessionImage(s), sessionGroup(s), s.state]);
  for (const p of OVERVIEW?.pulls || []) for (const m of p.members || []) rows.push([p.image_name, m.hostname || m.name || m.mac, Progress.view(m).label]);
  // ‏#927: CAPTURE_TASKS מחזיק גם משימות שהסתיימו (לאזהרת ה-shrink) —
  // כאן, כמו קודם, רק הפעילות באמת נחשבות "העברה".
  if (isAdmin()) for (const t of CAPTURE_TASKS) {
    if (t.state === "pending" || t.state === "running") rows.push([t.name, "Capture", Progress.view(t).label]);
  }
  const body = document.querySelector("#taskPanel tbody");
  if (body) body.innerHTML = rows.length ? rows.map(r => '<tr>'+[r[0],r[1],"-","-",r[2],"-"].map(x => '<td>'+esc(x)+'</td>').join('')+'</tr>').join('') : '<tr><td colspan="6">אין העברות פעילות</td></tr>';
  const count = $(".task-count"); if (count) count.textContent = String(rows.length);
  const summary = $(".task-summary"); if (summary) summary.textContent = overviewError || (rows.length ? rows.length + " העברות פעילות" : "אין העברות פעילות");
}
function editImageDescription(id) {
  const img = findImage(id); if (!img || !isAdmin()) return;
  sheet({title:"תיאור האימג'", fields:[{id:"description",label:"תיאור",type:"textarea",value:img.description}], onSubmit:async v => {
    await put("/images/"+encodeId(img.id), v); await loadImages(); openImageDetail(img.id);
  }});
}
function monitorMachine(mac) {
  const m=findMachine(mac), g=(GROUPS || []).find(g=>g.id===machineGroupId(m));
  if (!isAdmin() || !m || !["build","cloner"].includes(g?.role)) return;
  window.open("monitor.html?mac="+encodeId(m.mac)+"&name="+encodeId(machineName(m)),"imagectl-monitor-"+m.mac);
}

/* #827: אותה זרימה כמו מתג ה-SSH לתחנות (sshToggle) — הדלקה דורשת הקלדת
   מילת האישור, כיבוי לא. ‏confirm חייב לתאום בדיוק את מה שהשרת דורש
   (imagectl.monitor, #690). */
function monitorToggle(enabling) {
  if (!isAdmin()) return;
  const send = async (extra) => {
    const result = await put("/monitor/settings", { enabled: enabling, ...extra });
    toast(enabling
      ? "המוניטור הודלק — תופס באתחול הבא של כל תחנה"
      : "המוניטור כובה — תופס באתחול הבא של כל תחנה");
    if (current === "ports") await loadPorts(); else await loadMonitor();
  };
  if (!enabling) {
    send({}).catch((error) => toast(error.message));
    return;
  }
  sheet({
    title: "הדלקת מוניטור לתחנות",
    sub: "כל מחשב בנייה/שיכפול שיעלה מעכשיו יריץ שירות צפייה מרחוק (RFB, בלי סיסמה). "
      + "תופס באתחול הבא של כל תחנה — לא במכונות שכבר רצות.",
    danger: true, submitLabel: "הדלק",
    note: offMeansNote(PORT_OFF_MEANS.monitor),
    verify: { label: "להמשך יש להקליד בדיוק:", mustEqual: "imagectl.monitor" },
    onSubmit: () => send({ confirm: "imagectl.monitor" }),
  });
}
/* ‏#954 גל 4: אין עוד מגירת סבב — "פרטים" מוביל ללשונית "כיתה" בדף ההפצה. */
function openRoundDetail() { selectPageById("deploy"); activateTab(classroomsOn() ? 1 : 0); }

const UI_ICON_PATHS = {
  tools: '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18v3h3l6.3-6.3a4 4 0 0 0 5.4-5.4l-2.4 2.4-2.1-.6-.6-2.1Z"/>',
  "server": "<rect x=\"3\" y=\"4\" width=\"18\" height=\"6\" rx=\"1\"/><rect x=\"3\" y=\"14\" width=\"18\" height=\"6\" rx=\"1\"/><path d=\"M7 7h.01M7 17h.01M11 7h6M11 17h6\"/>",
  "home": "<path d=\"m3 11 9-8 9 8M5 9v12h5v-7h4v7h5V9\"/>",
  "image": "<rect x=\"3\" y=\"3\" width=\"18\" height=\"18\" rx=\"2\"/><path d=\"m3 17 6-6 4 4 3-3 5 5\"/><circle cx=\"16\" cy=\"8\" r=\"1\"/>",
  "folder": "<path d=\"M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z\"/>",
  "group": "<rect x=\"3\" y=\"4\" width=\"18\" height=\"16\" rx=\"1\"/><path d=\"M3 10h18M9 4v16M15 4v16\"/>",
  "build": "<path d=\"m14 5 5 5M3 21l8-8M9 3l12 12-6 6L3 9Z\"/>",
  "copy": "<rect x=\"8\" y=\"8\" width=\"13\" height=\"13\" rx=\"1\"/><path d=\"M16 8V3H3v13h5\"/>",
  "machine": "<rect x=\"3\" y=\"4\" width=\"18\" height=\"13\" rx=\"1\"/><path d=\"M8 21h8M12 17v4\"/>",
  "health": "<path d=\"M3 12h4l3-8 4 16 3-8h4\"/>",
  "network": "<path d=\"M12 7v6M5 17v-4h14v4\"/><rect x=\"9\" y=\"2\" width=\"6\" height=\"5\" rx=\"1\"/><rect x=\"2\" y=\"17\" width=\"6\" height=\"5\" rx=\"1\"/><rect x=\"16\" y=\"17\" width=\"6\" height=\"5\" rx=\"1\"/>",
  "deploy": "<path d=\"M4 6h15l-4-4M20 18H5l4 4M20 6v6M4 18v-6\"/>",
  "user": "<circle cx=\"12\" cy=\"7\" r=\"4\"/><path d=\"M4 21v-2a8 8 0 0 1 16 0v2\"/>",
  "list": "<path d=\"M8 5h13M8 12h13M8 19h13M3 5h.01M3 12h.01M3 19h.01\"/>",
  "settings": "<circle cx=\"12\" cy=\"12\" r=\"7\"/><circle cx=\"12\" cy=\"12\" r=\"2\"/><path d=\"M12 2v3M12 19v3M2 12h3M19 12h3\"/>",
  "refresh": "<path d=\"M20 7a9 9 0 1 0 1 7M20 2v6h-6\"/>",
  "search": "<circle cx=\"10\" cy=\"10\" r=\"7\"/><path d=\"m15 15 6 6\"/>",
  "bell": "<path d=\"M4 17h16l-2-3V8a6 6 0 0 0-12 0v6ZM9 21h6\"/>",
  "close": "<path d=\"m6 6 12 12M18 6 6 18\"/>",
  "help": "<circle cx=\"12\" cy=\"12\" r=\"9\"/><path d=\"M9 8a3 3 0 0 1 6 0c0 2-3 2-3 5M12 17h.01\"/>",
  "sun": "<circle cx=\"12\" cy=\"12\" r=\"4\"/><path d=\"M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1 1M18 18l1 1M5 19l1-1M18 6l1-1\"/>",
  "moon": "<path d=\"M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11Z\"/>",
  "storage": "<ellipse cx=\"12\" cy=\"5\" rx=\"8\" ry=\"3\"/><path d=\"M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3\"/>",
  "plus": "<path d=\"M12 5v14M5 12h14\"/>"
};
function uiIcon(name) { return '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">'+(UI_ICON_PATHS[name] || UI_ICON_PATHS.list)+'</svg>'; }

function encodeId(value) { return encodeURIComponent(value).replaceAll("'", "%27"); }
