/* ImageCtl — מעטפת הקונסולה: אימות, ניווט, drawer/modal.
   תוכן העמודים (נתונים אמיתיים) נכנס בשלבים הבאים. */
"use strict";

const $ = (sel) => document.querySelector(sel);
let ME = null;
let OVERVIEW = null;
let IMAGES = null, FOLDERS = null;
let IMAGES_FOLDER = null;
let MACHINES = null, GROUPS = null;
let DISK_FAILURES = null;   // #874: זיכרון כשלי הכתיבה (דיסקים אדומים)
let SHRINK_RECORDS = null;  // #926: דיסקי מקור שכווצו בקליטה ולא הוחזרו לגודלם (כתום)
let MACHINES_FILTER = null;
let HEALTH = null, NETCFG = null;
let MONITOR = null, monitorError = "";
let PORTS = null, portsError = "";
let USERS = null, JOURNAL = null;
let NIC_HIGHLIGHT = null;
let JOURNAL_FILTER = "";

async function api(path, options = {}) {
  const response = await fetch("/api/console" + path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...options,
  });
  if (response.status === 401) { showLogin(); throw new Error("לא מחובר"); }
  if (!response.ok) {
    let detail = "שגיאה " + response.status;
    try { detail = (await response.json()).detail || detail; } catch (e) {}
    const error = new Error(detail);
    error.status = response.status;   // ‏#936: הקורא מבחין בין 403/409 לכשל אמיתי
    throw error;
  }
  return response.json();
}
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });
const put = (path, body) => api(path, { method: "PUT", body: JSON.stringify(body || {}) });
const del = (path) => api(path, { method: "DELETE" });

function esc(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

/* ---------- #954: שפת העיצוב המשותפת — רכיבי רינדור לכל עמוד חדש ----------
   המקור: docs/design/console-redesign (README §"שפת העיצוב", המוקאפים
   שנדב אישר 17/09). ה-CSS: החלק ".page" בסוף console.css. כל עמוד שנבנה
   מחדש מרכיב את עצמו מכאן — כותרת אובייקט, לשוניות, KPI, כרטיס, datagrid,
   פס מצב, מפתח-ערך, ציר-זמן, הודעה וריק-מצב. הפונקציות מחזירות HTML;
   טקסט חופשי עובר esc() אצל הקורא כשהוא HTML, וכאן כשהוא מחרוזת. */
const UI = {
  /* כותרת אובייקט: breadcrumb, אייקון, שם, שורת-משנה, תג מצב, פעולות, לשוניות. */
  objHeader({ crumbs = [], icon = "server", name = "", sub = "", pill = "", actions = "", tabs = [], tab = 0 }) {
    const crumbHtml = crumbs.map((c, i) => {
      const last = i === crumbs.length - 1;
      const item = c.onclick && !last
        ? `<a role="link" tabindex="0" onclick="${c.onclick}">${esc(c.label)}</a>` : `<span>${esc(c.label)}</span>`;
      return (i ? `<span>/</span>` : "") + item;
    }).join("");
    const tabHtml = tabs.length > 1 ? `<div class="tabs" role="tablist">${tabs.map((t, i) =>
      `<button type="button" class="tab${i === tab ? " on" : ""}" role="tab" aria-selected="${i === tab}" tabindex="${i === tab ? 0 : -1}" onclick="activateTab(${i})">${esc(t)}</button>`).join("")}</div>` : "";
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
  datagrid({ columns, rows, empty = "אין נתונים" }) {
    if (!rows.length) return `<div class="empty">${esc(empty)}</div>`;
    const tr = (r) => Array.isArray(r) ? `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`
      : r.html != null ? r.html : `<tr ${r.attrs || ""}>${r.cells.map((c) => `<td>${c}</td>`).join("")}</tr>`;
    return `<table class="dg"><thead><tr>${columns.map((c) => `<th>${typeof c === "string" ? esc(c) : c.html}</th>`).join("")}</tr></thead><tbody>${rows.map(tr).join("")}</tbody></table>`;
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
/* מתג של שני מצבים: בהיר וכהה. בכניסה הראשונה מתיישרים לפי הגדרת
   המערכת, וברגע שנוגעים במתג זו בחירה מפורשת שנשמרת. אין מצב שלישי —
   "לפי המערכת" הוא התנהגות, לא אפשרות שצריך לבחור בה.
   הבחירה בדפדפן ולא בשרת: אותו אדם על מסך כיתה מואר ועל לפטופ בערב
   רוצה תשובות שונות. */
function systemPrefersDark() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function currentTheme() {
  const saved = localStorage.getItem("imagectl-theme");
  if (saved === "dark" || saved === "light") return saved;
  return systemPrefersDark() ? "dark" : "light";
}

function applyTheme(id, remember = true) {
  document.documentElement.setAttribute("data-theme", id);
  if (remember) localStorage.setItem("imagectl-theme", id);
  const button = $("#theme-toggle");
  const dark = id === "dark";
  button.innerHTML = uiIcon(dark ? "sun" : "moon");
  button.title = dark ? "מעבר למצב בהיר" : "מעבר למצב כהה";
}

$("#theme-toggle").addEventListener("click", () =>
  applyTheme(currentTheme() === "dark" ? "light" : "dark"));

/* מתגי הצגת סיסמה שמחוץ למודאל (מסך הכניסה). */
document.querySelectorAll("body > #login .pw-eye").forEach((eye) =>
  eye.addEventListener("click", () => {
    const input = document.getElementById(eye.dataset.pw);
    const showing = input.type === "text";
    input.type = showing ? "password" : "text";
    eye.textContent = showing ? "הצג" : "הסתר";
  }));

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

/* ---------- כניסה ---------- */

function showLogin() {
  ME = null;
  OVERVIEW = IMAGES = FOLDERS = MACHINES = GROUPS = HEALTH = NETCFG = USERS = JOURNAL = null;
  SESSION_MACHINES = {group:null,list:[]};
  CAPTURE_TASKS = [];
  clearInterval(pollTimer);
  closeDrawer(); closeModal();
  clearInterval(idleTimer);
  $("#login").classList.remove("hidden");
  $("#app").classList.add("hidden");
}

async function showApp() {
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
  document.querySelectorAll("[data-admin]").forEach(
    (el) => el.classList.toggle("hidden", ME.role !== "admin"));
  // רכיבים מותנים ביכולת נגזרת-שרת (#655/#723): מוצגים רק כשהדגל
  // המתאים ב-capabilities הוא true. הדגל הוא הסמכות — לא התפקיד לבדו.
  const caps = ME.capabilities || {};
  document.querySelectorAll("[data-cap]").forEach(
    (el) => el.classList.toggle("hidden", !caps[el.dataset.cap]));
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

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await post("/login", { username: $("#login-user").value, password: $("#login-pass").value });
    // ‏#655 v1: תשובת ה-login אינה נושאת capabilities — רק /me. בלי זה
    // רכיבי [data-cap] (לשונית "סניפים") נשארו נסתרים עד רענון הדף.
    ME = await api("/me");
    $("#login-error").textContent = "";
    await showApp();
  } catch (error) { $("#login-error").textContent = error.message; }
});

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
  if (roundDrawerOpen) await openRoundDetail();
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
    const [net, mon] = await Promise.allSettled([api("/net"), api("/monitor/machines")]);
    NET = net.status === "fulfilled" && Array.isArray(net.value) ? net.value : null;   // תשובה שאינה רשימה = לא נקרא
    NET_ERR = net.status === "rejected" ? (net.reason && net.reason.message) || "" : "";
    MONITOR_ROWS = mon.status === "fulfilled" && Array.isArray(mon.value) ? mon.value : null;
    populateSidebarGroups();
    updateAlertBadge();
    if (current === "machines") renderCurrent();
  } catch (e) {
    toast("טעינת המחשבים נכשלה: " + e.message);
  }
}

async function loadHealth() {
  try {
    HEALTH = await api("/health");
    updateAlertBadge();
    if (current === "home" || current === "health") renderCurrent();
  } catch (e) {
    toast("טעינת הבריאות נכשלה: " + e.message);
  }
}

async function loadPorts() {
  // #822: כשל קריאה הוא מצב משלו (portsError) ולא "אין פורטים" (עיקרון 5).
  try {
    PORTS = await api("/ports");
    portsError = "";
  } catch (e) {
    PORTS = null;
    portsError = e.message;
    toast("טעינת הפורטים נכשלה: " + e.message);
  }
  if (current === "ports") renderCurrent();
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

async function loadNetcfgData() {
  try {
    NETCFG = await api("/net/config");
    if (current === "network") renderCurrent();
  } catch (e) {
    toast("טעינת הרשת נכשלה: " + e.message);
  }
}

async function loadUsersData() {
  try {
    USERS = await api("/users");
    if (current === "permissions") renderCurrent();
  } catch (e) {
    toast("טעינת המשתמשים נכשלה: " + e.message);
  }
}

async function loadJournalData() {
  try {
    JOURNAL = await api("/journal");
    if (current === "logs") renderCurrent();
  } catch (e) {
    toast("טעינת היומן נכשלה: " + e.message);
  }
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
  fillSidebarTree("classesTree", groups.filter((g) => g.role === "classroom"), machines, "group");
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
  if (current === "nic" || current === "netdeploy") wireNetPage();
  wireRestoredPage();
}

/* ---------- סקירה כללית (#954 גל 1) ----------
   נבנה לפי docs/design/console-redesign/home.md: שלוש שאלות — השרת בסדר?
   מה רץ עכשיו? מה דורש אותי? — מה-API הקיים בלבד. מה שאין לו API (זמן
   פעילות) מוצג כ"בקרוב", לא כנתון מומצא. הנתונים המתחלפים (‏/overview,
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
      "ספריית האימג'ים", UI.barRow(v.percent, cls === "err" ? "err" : ""),
      UI.status(cls, label + (t.error ? " — " + t.error : "")), UI.acts([["לספרייה", "selectPageById('images')"]])]);
  }
  for (const t of HOME.transfers || []) {
    const active = ["queued", "sending", "verifying"].includes(t.state);
    if (!all && !active && t.state !== "failed") continue;
    const pct = t.bytes_total ? Math.round(100 * (t.bytes_sent || 0) / t.bytes_total) : null;
    const cls = t.state === "failed" ? "err" : t.state === "done" ? "ok" : t.state === "queued" ? "warn" : "run";
    const label = { queued: "ממתין בתור", sending: "שולח", verifying: "מאמת", done: "הושלם", failed: "נכשל" }[t.state] || t.state;
    rows.push([UI.nameHtml(`העברה לסניף ${t.node_label || t.node_id} — ${t.image_name || t.image_id}`, `${ltr(fmtDate(t.created_at) + " " + fmtClock(t.created_at))} · ${esc(t.started_by || "")}`),
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
  const unread = [!Array.isArray(HEALTH) && "בריאות", HOME.err.net && "רשת", HOME.err.nodes && "סניפים"].filter(Boolean);
  let body;
  if (items.length) body = `<div class="stack">${items.join("")}</div>`;
  else if (unread.length) body = UI.note("warn", esc(`לא נקראו: ${unread.join(", ")} — לא ניתן לומר שאין מה לטפל`));
  else body = UI.empty("אין פריטים לטיפול");
  const pill = items.length ? UI.pill("err", String(items.length)) : "";
  return UI.card({ title: "דורש טיפול", acts: pill, cls: "c4", body });
}

/* ל-/journal אין שדה חומרה (Issue פערי ה-API של הסקירה) — עד אז לפי שם
   ה-event (journal_he.py): כשל/סירוב → אדום; לא-מוכר/לא-מאומת/ביטול/סיכון
   → כתום; הושלם/אושר → ירוק; השאר מידע. */
function journalSeverity(row) {
  const e = String(row.event || "").toLowerCase();
  if (/_done$|_confirmed$|_cleared$|^login$|_staged$|_received$|_sent$/.test(e)) return "ok";   // לפני "fail": disk_failure_cleared
  if (/fail|refused|denied|error|abort|lost|disk_failure$/.test(e)) return "err";
  if (/unknown|unverified|unreadable|nonmember|cancel|risk|stopped|loop_local/.test(e)) return "warn";
  return "info";
}

function homeEvents(limit) {
  const journal = Array.isArray(HOME.journal) ? HOME.journal : null;
  const acts = `<button class="btn sm flat" onclick="selectPageById('logs')">ליומן המלא ←</button>`;
  let body;
  if (!journal) body = UI.note("warn", esc("היומן לא נקרא" + (HOME.err.journal ? ": " + HOME.err.journal : " עדיין")));
  else if (!journal.length) body = UI.empty("אין אירועים עדיין");
  else body = UI.timeline(journal.slice(0, limit).map((r) => ({
    t: isToday(r.ts) ? fmtClock(r.ts) : fmtDate(r.ts), cls: journalSeverity(r),
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
  const sub = [esc(ME && ME.server_name || ""), ME && ME.version ? esc(ME.version) : "", UI.soon("זמן פעילות")].filter(Boolean).join(" · ");
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

function deploy() {
  if (!OVERVIEW) return pagePlaceholder();
  const session = OVERVIEW.session;
  const room = OVERVIEW.room;
  const round = session || room;
  const tag = round
    ? `<span class="tag green">1 פעיל</span>`
    : `<span class="tag">0 פעיל</span>`;

  let tableBody;
  if (!round) {
    tableBody = `<div class="empty">אין סבבים פעילים</div>`;
  } else {
    // #715: a room round fed from the build machine's own disk has no library
    // image — the console says where the bytes come from instead. The active
    // session here IS the room's wave, so the source is read off the round.
    const direct = room && room.source && room.source.kind === "build_disk";
    const img = direct
      ? `מקור: מחשב הבנייה (${room.source.name || room.source.mac}:${room.source.disk})`
      : sessionImage(round);
    const group = session ? sessionGroup(session) : (room.group_label || "—");
    const joined = session ? (session.joined || 0) : (room.written_drives || 0);
    const expected = session ? sessionExpected(session) : (room.target_drives || 0);
    const pct = session
      ? sessionProgressPct(session)
      : (room.target_drives ? Math.round(100 * (room.written_drives || 0) / room.target_drives) : null);
    const state = session ? session.state : (room.wave_state || "");
    const bar = pct == null ? "—" : `<div class="progress" style="width:150px"><i style="width:${pct}%"></i></div>`;
    const idLabel = session
      ? (session.group_label || session.prefix || img)
      : (room.image_name || "—");
    tableBody = `<table class="table"><thead><tr><th>סבב</th><th>אימג׳</th><th>כיתה</th><th>מחוברים</th><th>התקדמות</th><th>סטטוס</th><th>פעולות</th></tr></thead><tbody><tr><td><strong>${esc(idLabel)}</strong></td><td>${esc(img)}</td><td>${esc(group)}</td><td>${joined} / ${expected}</td><td>${bar}</td><td><span class="status ${stateClass(state)}"><i></i>${esc(stateLabel(state))}</span></td><td><button class="tool-btn" onclick="openRoundDetail()">פרטים</button></td></tr></tbody></table>`;
  }

  const sid = session && session.id;
  const startAttr = sid && ME && session.state === "open" ? ` onclick="startRound()"` : " disabled";
  const stopAttr = sid && ME ? ` onclick="stopRound()"` : " disabled";
  const members = (session && session.members) || [];
  const membersHtml = members.length
    ? `<div class="list">${memberRows(members)}</div>`
    : `<div class="empty">אין תחנות מצורפות</div>`;

  return `<div class="grid"><div class="span-12"><div class="card"><div class="card-h"><span>סבבים</span>${tag}</div><div class="card-b table-wrap">${tableBody}</div></div></div>
<div class="span-8"><div class="card"><div class="card-h">תחנות בסבב</div><div class="card-b">${membersHtml}</div></div></div>
<div class="span-4"><div class="card"><div class="card-h">בקרת סבב</div><div class="card-b"><div class="list"><div class="list-row"><div class="list-main"><strong>התחלה ידנית</strong><small>מקדים את הטיימר אם הסבב פתוח</small></div><button class="btn"${startAttr}>התחל</button></div><div class="list-row"><div class="list-main"><strong>עצירת סבב</strong><small>מונע הצטרפות חדשה</small></div><button class="btn danger"${stopAttr}>עצור</button></div></div></div></div></div></div>`;
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
      `<a role="link" tabindex="0" class="name" onclick="openImageDetail('${idEnc}')">${esc(r.name)}</a>${r.description ? `<span class="sub">${esc(r.description)}</span>` : ""}`,
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
  const cancel = isAdmin() ? `<div class="acts"><button class="btn sm danger" onclick="cancelCapture('${esc(t.id)}')">בטל קליטה</button></div>` : "";
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
    const cancel = isAdmin() && active ? `<div class="acts"><button class="btn sm danger" onclick="cancelCapture('${esc(t.id)}')">ביטול</button></div>` : "";
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
  if (Array.isArray(j)) for (const r of j) events.push({ ts: r.ts, cls: journalSeverity(r), text: esc(r.label || r.event) + (r.text ? ` — ${esc(r.text)}` : ""), who: r.user });
  events.sort((a, b) => String(b.ts).localeCompare(String(a.ts)));
  return events.map((e) => ({ t: isToday(e.ts) ? fmtClock(e.ts) : fmtDate(e.ts), cls: e.cls, text: e.text, who: e.who }));
}
function imageDrawerHtml(img) {
  const admin = isAdmin(), idEnc = encodeId(img.id);
  const use = imgUsage(img.id);
  const s = IMG.scrub[img.id];
  const actions = `<div class="acts" style="display:flex;gap:6px;flex-wrap:wrap"><button class="btn primary" onclick="deployImage('${idEnc}')">הפץ לכיתה…</button><a class="btn" href="/api/console/images/${idEnc}/download">הורדה</a>${admin ? `<button class="btn" onclick="scrubImage('${idEnc}')">אימות חוזר</button>` : ""}</div>`;
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
  return (GROUPS || []).slice().sort((a, b) => (ROLE_ORDER[a.role] ?? 9) - (ROLE_ORDER[b.role] ?? 9));
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
function disksCell(m) {
  if (m.disks == null) return `<span class="muted">לא דיווח</span>`;
  if (!m.disks.length) return UI.status("warn", "דיווח 0 דיסקים");
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
  return gid ? (MACHINES || []).filter((m) => machineGroupId(m) === gid) : (MACHINES || []);
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
  if (role === "build" || role === "cloner") acts.push(["מוניטור", `monitorMachine('${macEnc}')`]);
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
  const title = fixed ? `<span>${esc(g.label)}</span>` : `<a role="link" tabindex="0" onclick="openClass('${gidEnc}')">${esc(g.label)}</a>`;
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
    ? [["+ מחשב", `openAddMachine({group:'${gidEnc}'})`]]
    : [["פתח כיתה", `openClass('${gidEnc}')`], ["+ מחשב לכיתה", `openAddMachine({group:'${gidEnc}'})`]];
  if (g.role === "cloner") acts.push(["WoL לחדר", "wakeRoom()"]);
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
    `<select aria-label="תפקיד" onchange="mchFilter('role',this.value)">${opt("", "כל התפקידים", MCH.role)}${["classroom", "build", "cloner"].map((r) => opt(r, ROLE_GROUP_HE[r], MCH.role)).join("")}</select>`
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
  const small = g ? 'אותה טבלה כמו ב"מחשבים", מסוננת' : "בנייה ושיכפול = קבוצה קבועה · כל כיתה = קבוצה משלה";
  return strip + UI.card({ title, small, cls, flush: true, body: machinesBar(!!g || !!MACHINES_FILTER) + `<div id="mch-table">${machinesTableHtml()}</div>` + foot });
}
function machinesTabs() {
  const unreg = NET ? NET.filter((d) => !d.registered).length : null;
  return ["כל המחשבים", unreg == null ? "נראו ברשת" : `נראו ברשת (${unreg})`,
    DISK_FAILURES ? `דיסקים אדומים (${DISK_FAILURES.length})` : "דיסקים אדומים"];
}
function machines(tab = 0) {
  const crumbs = [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "מלאי" }, { label: "מחשבים" }];
  if (!MACHINES || !GROUPS) {
    return `<div class="page">${UI.objHeader({ crumbs, icon: "machine", name: "מחשבים", tabs: machinesTabs(), tab })}<div class="body"><div class="c12">${pagePlaceholder()}</div></div></div>`;
  }
  if (MACHINES_CLASS) {
    const g = GROUPS.find((x) => x.id === MACHINES_CLASS);
    if (g) return groupPage(g, tab);
    MACHINES_CLASS = null;
  }
  const all = MACHINES, count = (role) => all.filter((m) => machineRole(m) === role).length;
  const sub = [`${all.length} רשומים`, `${GROUPS.filter((g) => g.role === "classroom").length} כיתות`, `${count("build")} מחשבי בנייה`, `${count("cloner")} משכפלים`];
  if (NET) sub.push(`${NET.filter((d) => d.registered && isToday(d.last_seen)).length} נראו ברשת היום`);
  const red = (DISK_FAILURES || []).length, unreg = NET ? NET.filter((d) => !d.registered).length : 0;
  const pill = (red ? UI.pill("err", `${red} ${red === 1 ? "דיסק אדום" : "דיסקים אדומים"}`) : "")
    + (unreg ? UI.pill("warn", `${unreg} ${unreg === 1 ? "לא רשום" : "לא רשומים"}`) : "");
  const actions = (isAdmin()
    ? `<button class="btn primary" onclick="openAddMachine({})">+ מחשב</button><button class="btn" onclick="addGroupSheet()">+ כיתה</button><button class="btn" onclick="openAddMachine({tab:1})">ייבוא בהדבקה</button><a class="btn" href="/api/console/machines.csv" download>ייצוא CSV</a>`
    : "") + `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>`;
  const header = UI.objHeader({ crumbs, icon: "machine", name: "מחשבים", sub: esc(sub.join(" · ")), pill, actions, tabs: machinesTabs(), tab });
  const body = tab === 1 ? seenDevicesCard() : tab === 2 ? diskFailuresCard() + shrinkRecordsCard() : machinesTableCard();
  return `<div class="page">${header}<div class="body">${body}</div></div>`;
}
function clearMachinesFilter() {
  MACHINES_FILTER = null;
  MACHINES_CLASS = null;
  if (current === "machines") renderCurrent();
}
function openMachinesPage() { MACHINES_FILTER = null; MACHINES_CLASS = null; selectPageById("machines"); }
/* לחיצה על קבוצה בעץ: כיתה → האובייקט שלה; בנייה/שיכפול → הטבלה מסוננת (#916). */
function selectMachinesGroup(groupId) {
  try { groupId = decodeURIComponent(groupId); } catch (e) {}
  const g = (GROUPS || []).find((x) => x.id === groupId);
  if (g && g.role === "classroom") { MACHINES_CLASS = groupId; MACHINES_FILTER = null; }
  else { MACHINES_FILTER = groupId; MACHINES_CLASS = null; }
  MCH.sel.clear();
  selectPageById("machines");
}
function openClass(gidEnc) {
  let gid = gidEnc; try { gid = decodeURIComponent(gidEnc); } catch (e) {}
  MACHINES_CLASS = gid; MACHINES_FILTER = null; MCH.sel.clear();
  selectPageById("machines");
}

/* ---------- הקבוצה כאובייקט (class.md) — כיתה; אותו דפוס ישמש למשכפלים/בנייה בגל 3א ---------- */
function groupTabs(g) { return g.role === "classroom" ? ["סיכום", "מחשבים", "סבבים"] : ["סיכום", "מחשבים"]; }
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
function groupRoundsCard(g, cls) {
  const s = groupSession(g.id);
  const now = s ? UI.timeline([{ t: "עכשיו", cls: "info", text: `${esc(sessionImage(s))} · ${esc(stateLabel(s.state))}, ${s.joined || 0}/${sessionExpected(s)} הצטרפו · ${UI.link("לסבב", "selectPageById('deploy')")}`, who: "" }]) : "";
  const note = UI.note("info", `היסטוריית סבבים ל${g.role === "classroom" ? "כיתה" : "קבוצה"} — <b title="אין endpoint לסבבים סגורים לפי קבוצה">דורש API</b> (היום רק ${UI.link("ביומן", "selectPageById('logs')")}).`);
  return UI.card({ title: g.role === "classroom" ? "סבבים של הכיתה" : "סבבים", cls, body: now + (now ? `<div style="margin-top:12px"></div>` : "") + note });
}
function groupPage(g, tab = 0) {
  const gidEnc = encodeId(g.id), kids = (MACHINES || []).filter((m) => machineGroupId(m) === g.id), s = groupSession(g.id);
  const classroom = g.role === "classroom";
  const seen = NET ? kids.filter((m) => { const n = netFor(m.mac); return n && n.last_seen && isToday(n.last_seen); }).length : null;
  const sub = [classroom ? "קבוצת כיתה" : "קבוצה קבועה", s && s.prefix ? `קידומת ${s.prefix}` : "", `${kids.length} ${kids.length === 1 ? "מחשב" : "מחשבים"}`, seen == null ? "" : `${seen} נראו היום`].filter(Boolean).join(" · ");
  const pill = s ? UI.pill("info", `סבב ${stateLabel(s.state)}`) : "";
  const actions = (isAdmin() ? (classroom ? `<button class="btn primary" onclick="deployToGroup('${gidEnc}')">הפץ לכיתה…</button>` : "")
    + `<button class="btn" onclick="openAddMachine({group:'${gidEnc}'})">+ מחשב${classroom ? " לכיתה" : ""}</button>`
    + (g.role === "cloner" ? `<button class="btn" onclick="wakeRoom()">הער את כולם (WoL)</button>` : "")
    + (classroom ? `<button class="btn" onclick="renameGroup('${gidEnc}')">שינוי שם</button><button class="btn danger" onclick="deleteGroup('${gidEnc}')">מחיקה (הקלדת שם)</button>` : "") : "")
    + `<button class="btn" onclick="refreshPage()">${uiIcon("refresh")} רענון</button>`;
  const header = UI.objHeader({
    crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "מלאי" }, { label: "מחשבים", onclick: "openMachinesPage()" }, { label: ROLE_GROUP_HE[g.role] || "קבוצות" }, { label: g.label }],
    icon: classroom ? "group" : g.role === "build" ? "build" : "copy", name: g.label, sub: esc(sub), pill, actions, tabs: groupTabs(g), tab });
  let body;
  if (tab === 1) body = machinesTableCard(g);
  else if (tab === 2) body = groupRoundsCard(g, "c12");
  else body = `<div class="c12 kpis">${groupKpis(g, kids)}</div>` + machinesTableCard(g, "c8") + groupRoundsCard(g, "c4");
  return `<div class="page">${header}<div class="body">${body}</div></div>`;
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
  const rows = NET.map((d) => {
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
function diskFailuresCard() {
  const rows = (DISK_FAILURES || []).map((f) => {
    const m = findMachine(f.mac);
    const where = (m ? machineName(m) : f.mac) + (f.port != null ? ` · דיסק ${f.port} · SATA ${f.port - 1}` : "");
    const log = f.ata_log || [];
    const evidence = log.length ? `<details><summary>${log.length} שורות קרנל</summary><pre class="ata-log">${esc(log.join("\n"))}</pre></details>` : `<span class="muted">—</span>`;
    return [`<span class="mono">${esc(f.serial || "—")}</span>`, esc(where), esc(fmtWhen(f.at)), UI.status("err", failureCauseText(f)), esc(f.error || ""), evidence,
      isAdmin() ? UI.acts([["נקה", `clearDiskFailure(${Number(f.id)})`]]) : ""];
  });
  const body = DISK_FAILURES == null ? UI.note("warn", "רשימת הכשלים לא נטענה")
    : UI.datagrid({ columns: ["מספר סידורי", "מכונה · דיסק · חריץ", "מתי", "סיבה", "שגיאה", "ראיה", ""], rows, empty: "אין דיסקים אדומים" });
  return UI.card({ title: "דיסקים אדומים — נכשלו בכתיבה", small: 'אדום במסך המחשב לפני הסבב; "נקה" אחרי החלפת דיסק או כבל', flush: true, body });
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
    const text = `מחיצה ${r.idx} כווצה לקליטה ולא הוחזרה לגודלה המקורי (${gb(r.size_sectors)})`;
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
  if (!m.disks.length) return UI.note("warn", "המכונה דיווחה — ואין בה אף כונן.");
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
function machineDrawerHtml(m) {
  const macEnc = encodeId(m.mac), role = machineRole(m), g = machineGroup(m), net = netFor(m.mac), st = machineState(m), admin = isAdmin();
  const sub = [esc(ROLE_HE[role] || role), g ? esc(g.label) : "", `<span class="mono">${esc(m.mac)}</span>`, net && net.ip ? `<span class="mono">${esc(net.ip)}</span>` : "",
    `נראה ${esc(NET ? seenAgo(net && net.last_seen) : "לא נקרא")}`].filter(Boolean).join(" · ");
  const actions = `<div class="acts" style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">`
    + (admin && (role === "build" || role === "cloner") ? `<button class="btn primary" onclick="monitorMachine('${macEnc}')">מוניטור</button>` : "")
    + (admin && role === "cloner" ? `<button class="btn" onclick="wakeRoom()" title="WoL נשלח לכל חדר השיכפולים — אין WoL למחשב יחיד">Wake-on-LAN (כל החדר)</button>` : UI.soon("Wake-on-LAN"))
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
    ? UI.timeline(h.slice(0, 20).map((r) => ({ t: isToday(r.ts) ? fmtClock(r.ts) : fmtDate(r.ts), cls: journalSeverity(r), text: esc(r.label || r.event || "") + (r.text ? " — " + esc(r.text) : ""), who: r.user || "" })))
    : h && h.error ? UI.note("warn", "היומן לא נקרא: " + esc(h.error))
    : `<div class="muted">${Array.isArray(h) ? "אין אירועים ביומן למחשב הזה (20 האחרונים)." : "טוען את היומן…"}</div>`;
  const foot = admin
    ? `<div style="border-top:1px solid var(--hair);padding-top:12px;display:flex;gap:6px;flex-wrap:wrap;align-items:center"><button class="btn sm" onclick="moveMachineSheet('${macEnc}')">העבר לקבוצה</button>${UI.soon("עריכת MAC")}<button class="btn sm danger" onclick="removeMachine('${macEnc}')">הסרה מהרישום (הקלדת שם)</button></div>`
    : "";
  return `<div class="page drw"><div class="obj-sub">${sub}</div>${actions}${red}${machineCaptureWarningHtml(m.mac)}${machineRestoreWarningHtml(m.mac)}<div><div class="sec">מצב עכשיו</div>${now}</div><div><div class="sec">דיסקים${m.disks_reported_at ? ` (דיווח אחרון, ${esc(ago(m.disks_reported_at))})` : ""}</div>${diskBoxesHtml(m)}${slots}</div><div><div class="sec">חומרה (למיפוי דרייברים)${m.inventory_seen_at ? ` (${esc(ago(m.inventory_seen_at))})` : ""}</div>${hw}</div><div><div class="sec">היסטוריה</div>${history}</div>${foot}</div>`;
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
<div id="am-one" class="form"${tab === 1 ? " hidden" : ""}><div class="field"><label for="am-group">קבוצה</label><select id="am-group">${groupOptionsHtml(group)}</select></div><div class="field"><label for="am-name">שם / סיומת</label><input id="am-name" autocomplete="off" placeholder="01–99 או INS לכיתה; שם חופשי לבנייה ולשיכפול"></div><div class="field full"><label for="am-mac">MAC</label><input id="am-mac" dir="ltr" value="${esc(mac)}" placeholder="b4:2e:99:07:1a:c4" autocomplete="off"></div></div>
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
  if (!isAdmin()) return;
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

function healthStatusClass(state) {
  if (state === "ok") return "ok";
  if (state === "warn") return "warn";
  if (state === "off") return "";
  return "err";
}

function healthStatusLabel(state) {
  if (state === "ok") return "תקין";
  if (state === "warn") return "אזהרה";
  if (state === "off") return "כבוי";
  if (state === "bad") return "תקלה";
  return state || "—";
}

function health() {
  if (!HEALTH) return pagePlaceholder();
  const checks = Array.isArray(HEALTH) ? HEALTH : [];
  const hasBad = checks.some((c) => c.state === "bad");
  const hasWarn = checks.some((c) => c.state === "warn");
  const hasOff = checks.some((c) => c.state === "off");
  let overallClass = "ok", overallLabel = "הכל תקין";
  if (!checks.length) { overallClass = ""; overallLabel = "אין בדיקות"; }
  else if (hasBad) { overallClass = "err"; overallLabel = "תקלה"; }
  else if (hasWarn) { overallClass = "warn"; overallLabel = "אזהרה"; }
  else if (hasOff) { overallClass = ""; overallLabel = "חלק כבוי"; }

  let body;
  if (!checks.length) {
    body = `<div class="empty">אין נתונים להצגה</div>`;
  } else {
    body = checks.map((c) => {
      const cls = healthStatusClass(c.state);
      return `<div class="metric"><div><strong>${esc(c.label)}</strong><span>${esc(c.detail)}</span></div><div class="status ${cls}"><i></i>${esc(healthStatusLabel(c.state))}</div></div>`;
    }).join("");
  }

  return `<div class="grid"><div class="span-12"><div class="card"><div class="card-h"><span>בדיקות חיוניות</span><div><span class="status ${overallClass}"><i></i>${esc(overallLabel)}</span> <button class="btn" onclick="loadHealth()">בדוק עכשיו</button></div></div><div class="card-b">${body}</div></div></div></div>`;
}

function netIface() {
  const list = NETCFG && NETCFG.interfaces;
  return (list && list[0]) || null;
}

function network() {
  if (!NETCFG) return pagePlaceholder();
  const iface = netIface();
  let formBody;
  if (!iface) {
    formBody = `<div class="empty">אין נתונים להצגה</div>`;
  } else {
    const dns = (iface.dns || []).join(", ");
    const gaps = iface.mismatches || [];
    const gapNote = gaps.length
      ? `<div class="field full"><div class="notice warn">${esc(gaps.join(" · "))}</div></div>`
      : "";
    const modeBit = iface.mode_he ? " — " + iface.mode_he : "";
    formBody = `<div class="form"><div class="field"><label>כתובת IP</label><input id="net-address" dir="ltr" value="${esc(iface.address || "")}"></div><div class="field"><label>מסכת רשת</label><input id="net-netmask" dir="ltr" value="${esc(iface.netmask || "")}"></div><div class="field"><label>Gateway</label><input id="net-gateway" dir="ltr" value="${esc(iface.gateway || "")}"></div><div class="field"><label>DNS</label><input id="net-dns" dir="ltr" value="${esc(dns)}"></div><div class="field full"><label>ממשק</label><input value="${esc(iface.name + modeBit)}" disabled></div>${gapNote}<div class="field full"><button class="btn" disabled title="בקרוב">בדיקת קישוריות לפני החלה</button> <button class="btn" disabled title="בקרוב">Rollback</button></div></div>`;
  }

  return `<div class="grid"><div class="span-8"><div class="card"><div class="card-h">הגדרות ממשק <button class="btn primary" onclick="saveNetwork()">שמור</button></div><div class="card-b">${formBody}</div></div></div><div class="span-4"><div class="card"><div class="card-h">פורטיים</div><div class="card-b"><div class="list"><div class="list-row"><div class="list-main"><strong>HTTP</strong><small>קונסולה + boot + API</small></div><span class="tag">8080</span></div><div class="list-row"><div class="list-main"><strong>TFTP</strong><small>bootloader</small></div><span class="tag">69/udp</span></div><div class="list-row"><div class="list-main"><strong>PXE Proxy</strong><small>DHCP שאינו שלנו</small></div><span class="tag">4011</span></div><div class="list-row"><div class="list-main"><strong>Multicast</strong><small>שידור אימג׳</small></div><span class="tag">9000–9001</span></div></div></div></div></div><div class="span-12"><div class="card"><div class="card-h">רשת השידור <button class="tool-btn" disabled title="בקרוב">בדוק →</button></div><div class="card-b"><div class="statrow"><div class="statbox"><div class="n">10.44.12.0/24</div><div class="l">רשת שרת</div></div><div class="statbox"><div class="n">UDP</div><div class="l">פרוטוקול שידור</div></div><div class="statbox"><div class="n">TTL 1</div><div class="l">תחום מקומי</div></div></div></div></div></div></div>`;
}

function saveNetwork() {
  if (!(ME && ME.role === "admin")) { toast("אין הרשאה"); return; }
  const iface = netIface();
  if (!iface) { toast("אין ממשק לשמירה"); return; }
  openModalContent(
    "שמירת הגדרות רשת",
    `<div class="confirm-box">שינוי כתובת, שער או DNS עלול לנתק את הקונסולה.</div><div class="danger-confirm">אם זה הכרטיס שהקונסולה מגיעה דרכו, החיבור ייפול. ההגדרה תוחזר אוטומטית תוך דקה אלא אם תאשרו שהחיבור עדיין חי.</div>`,
    "שמור",
    confirmSaveNetwork
  );
}

async function confirmSaveNetwork() {
  const iface = netIface();
  if (!iface) { closeModal(); return; }
  const addressEl = $("#net-address");
  const maskEl = $("#net-netmask");
  const gwEl = $("#net-gateway");
  const dnsEl = $("#net-dns");
  const dns = ((dnsEl && dnsEl.value) || "").split(/[,;]/).map((s) => s.trim()).filter(Boolean);
  // confirm = שם הכרטיס: ה-API דורש אותו תמיד (409 בלעדיו).
  // mode/routes נשלחים כפי שהם כדי שלא יימחקו בשמירת הכתובת.
  const body = {
    address: ((addressEl && addressEl.value) || "").trim(),
    netmask: ((maskEl && maskEl.value) || "").trim(),
    gateway: ((gwEl && gwEl.value) || "").trim(),
    dns,
    mode: iface.mode,
    routes: iface.routes || [],
    confirm: iface.name,
  };
  try {
    const result = await put("/net/config/" + encodeId(iface.name), body);
    closeModal();
    if (result.apply_error) toast("ההחלה נכשלה: " + result.apply_error);
    else if (!result.verified) toast("נכתב — אבל המצב בפועל לא תואם: " + (result.mismatches || []).join(" · "));
    else if (result.rollback && result.rollback.pending) toast("הוחל. אשרו תוך דקה שהקונסולה עדיין נגישה, אחרת יוחזר.");
    else toast("הוחל, ואומת מול ip addr");
    loadNetcfgData();
  } catch (e) {
    toast(e.message);
  }
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

function permissions() {
  if (!USERS) return pagePlaceholder();
  const list = USERS;
  let tableBody;
  if (!list.length) {
    tableBody = `<div class="empty">אין משתמשים</div>`;
  } else {
    const rows = list.map((u) => {
      const enc = encodeId(u.username);
      const disabled = !!u.disabled;
      const stClass = disabled ? "warn" : "ok";
      const stLabel = disabled ? "מושבת" : "פעיל";
      return `<tr><td><strong>${esc(u.username)}</strong></td><td>${esc(roleLabel(u.role))}</td><td><span class="status ${stClass}"><i></i>${stLabel}</span></td><td>${esc(fmtWhen(u.created_at))}</td><td><button class="tool-btn" onclick="editUser('${enc}')">עריכה</button></td></tr>`;
    }).join("");
    tableBody = `<table class="table"><thead><tr><th>משתמש</th><th>תפקיד</th><th>מצב</th><th>נוצר</th><th>פעולה</th></tr></thead><tbody>${rows}</tbody></table>`;
  }
  return `<div class="grid"><div class="span-12"><div class="card"><div class="card-h"><span>משתמשים</span><button class="btn primary" onclick="openNewUser()">+ משתמש</button></div><div class="card-b table-wrap">${tableBody}</div></div></div>
<div class="span-4"><div class="card"><div class="card-h">מנהל <button class="tool-btn" onclick="openRolesDrawer()">פרטים</button></div><div class="card-b">גישה מלאה לספרייה, סבבים, מחשבים, רשת והרשאות.</div></div></div>
<div class="span-4"><div class="card"><div class="card-h">מפעיל הפצה <button class="tool-btn" onclick="openRolesDrawer()">פרטים</button></div><div class="card-b">בחירת אימג׳ והפצה בלבד — ללא שינוי רשת או הרשאות.</div></div></div>
<div class="span-4"><div class="card"><div class="card-h">עקרון ברירת מחדל</div><div class="card-b">כל פעולה מופרדת לפי הרשאה מפורשת; אין הרשאה שקטה.</div></div></div></div>`;
}



async function createUser() {
  const username = ((document.getElementById("userName") || {}).value || "").trim();
  const password = (document.getElementById("userPass") || {}).value || "";
  const role = (document.getElementById("userRole") || {}).value || "deploy";
  if (!username || !password) { toast("נדרשים שם משתמש וסיסמה"); return; }
  try {
    await post("/users", { username, password, role });
    closeModal();
    toast("המשתמש " + username + " נוצר");
    await loadUsersData();
  } catch (e) {
    toast(e.message);
  }
}

function findUser(name) {
  return (USERS || []).find((u) => u.username === name) || null;
}

function editUser(name) {
  try { name = decodeURIComponent(name); } catch (e) {}
  const u = findUser(name); if (!u || !isAdmin()) return;
  sheet({title:"Edit user", fields:[
    {id:"role",label:"Role",type:"select",value:u.role,options:(u.username===ME.username ? [u.role] : ["deploy","admin"]).map(value=>({value,label:value}))},
    {id:"password",label:"New password (optional)",type:"password",confirm:"Confirm new password"},
    ...(u.username===ME.username ? [] : [{id:"disabled",label:"Disabled",type:"checkbox",value:!!u.disabled}])
  ],onSubmit:async v=>{await put("/users/"+encodeId(u.username),v);await loadUsersData();}});
}

async function saveUser(name) {
  try { name = decodeURIComponent(name); } catch (e) {}
  const role = (document.getElementById("editUserRole") || {}).value || "";
  const disabledRaw = (document.getElementById("editUserDisabled") || {}).value;
  const password = (document.getElementById("editUserPass") || {}).value || "";
  const body = { role, disabled: disabledRaw === "true" };
  if (password) body.password = password;
  try {
    await put("/users/" + encodeId(name), body);
    closeModal();
    toast("פרטי המשתמש נשמרו");
    await loadUsersData();
  } catch (e) {
    toast(e.message);
  }
}

function openRolesDrawer() {
  openDrawer("תפקידים והרשאות", `<div class="detail-box"><strong>מנהל</strong><div class="muted">גישה מלאה לספרייה, סבבים, מחשבים, רשת והרשאות.</div></div><div style="height:8px"></div><div class="detail-box"><strong>מפעיל הפצה</strong><div class="muted">בחירת אימג׳ והפצה בלבד — ללא שינוי רשת או הרשאות.</div></div>`);
}

function logs() {
  if (!JOURNAL) return pagePlaceholder();
  const q = (JOURNAL_FILTER || "").trim().toLowerCase();
  const visible = (JOURNAL || []).map((row, i) => ({ row, i })).filter(({ row }) => {
    if (!q) return true;
    return String(row.label || "").toLowerCase().includes(q)
        || String(row.text || "").toLowerCase().includes(q);
  });
  let tableBody;
  if (!visible.length) {
    tableBody = `<div class="empty">אין אירועים להצגה</div>`;
  } else {
    const rows = visible.map(({ row, i }) => {
      const label = row.label || row.text || row.event || "";
      return `<tr class="clickable" onclick="openLogDetail(${i})"><td>${esc(fmtHour(row.ts))}</td><td>${esc(label)}</td><td>${esc(row.user || "")}</td><td>${esc(row.event || "")}</td></tr>`;
    }).join("");
    tableBody = `<table class="table"><thead><tr><th>זמן</th><th>אירוע</th><th>יעד/מקור</th><th>event</th></tr></thead><tbody>${rows}</tbody></table>`;
  }
  return `<div class="grid"><div class="span-12"><div class="card"><div class="card-h"><span>אירועים</span><div><button class="btn" onclick="openLogFilter()">סינון</button> <button class="btn" disabled title="בקרוב">ייצוא CSV</button></div></div><div class="card-b table-wrap">${tableBody}</div></div></div>
<div class="span-12"><div class="card"><div class="card-h">עקרון התצוגה</div><div class="card-b"><p style="margin:0;color:var(--muted);font-size:11px">היומן מיועד למפעיל: משפט בעברית, יעד ברור, וקונטקסט תפעולי. לחיצה על אירוע מציגה את המשמעות והפרטים הטכניים.</p></div></div></div></div>`;
}

function openLogFilter() {
  openModalContent(
    "סינון יומן",
    `<div class="form"><div class="field full"><label>חיפוש בטקסט</label><input id="logQuery" placeholder="מחשב, כיתה, אימג׳…"></div></div>`,
    "החל סינון",
    applyLogFilter
  );
  const input = document.getElementById("logQuery");
  if (input) { input.value = JOURNAL_FILTER || ""; input.focus(); }
}

function applyLogFilter() {
  const input = document.getElementById("logQuery");
  JOURNAL_FILTER = input ? input.value.trim() : "";
  closeModal();
  if (current === "logs") renderCurrent();
}

function openLogDetail(i) {
  const row = (JOURNAL || [])[i];
  if (!row) { toast("אירוע לא נמצא"); return; }
  openDrawer("פרטי אירוע", `<div class="detail-grid"><div class="detail-box"><span class="k">אירוע</span><span class="v">${esc(row.label || "")}</span></div><div class="detail-box"><span class="k">פירוט</span><span class="v">${esc(row.text || "")}</span></div><div class="detail-box"><span class="k">משתמש</span><span class="v">${esc(row.user || "")}</span></div><div class="detail-box"><span class="k">זמן</span><span class="v">${esc(row.ts || "")}</span></div></div>`);
}

/* ---------- כרטיסי רשת / רשת הפצה / פורטים ---------- */

function nicUnion() {
  const byName = new Map(((NETCFG && NETCFG.interfaces) || []).map((r) => [r.name, r]));
  const nics = typeof NICS !== "undefined" && NICS ? NICS : [];
  const names = [...new Set([...nics.map((n) => n.name), ...byName.keys()])].sort();
  return names.map((name) => {
    const found = nics.find((x) => x.name === name);
    let n = found || {
      name, mac: "", state: "unknown", addresses: [], present: byName.has(name),
      trunk: false, description: "", enabled: false, proxy: false,
      dhcp_live: { state: "unknown" }, dhcp_live_label: "לא ידוע",
      dhcp_diverged: false, speed_mbps: null, server_ip: "",
    };
    if (!n.dhcp_live || !n.dhcp_live.state) {
      n = Object.assign({}, n, {
        dhcp_live: { state: "unknown" },
        dhcp_live_label: n.dhcp_live_label || "לא ידוע",
      });
    }
    return { n, cfgRow: byName.get(name) };
  });
}

function netBannerHtml(actions) {
  const actionsHtml = actions || "";
  if (!NETCFG) {
    if (!actionsHtml) return "";
    return `<div class="span-12"><div class="card"><div class="card-h"><span>כרטיסי רשת</span><div>${actionsHtml}</div></div></div></div>`;
  }
  const live = NETCFG.live || {};
  const foot = live.checked
    ? `נתיבים כרגע: ${(live.routes || []).join(" · ") || "אין"} · ‏DNS: `
      + `${(live.nameservers || []).join(", ") || "אין"}`
    : `המצב בפועל לא נקרא (${live.reason || ""}) — אף שורה אינה מאומתת`;
  const notSourced = NETCFG.sourced === false
    ? `<div class="sheet-note danger">‏/etc/network/interfaces אינו טוען את
       interfaces.d — כל מה שנכתב שם לא ייקרא באתחול.</div>` : "";
  const rb = (typeof rollbackBanner === "function" && NETCFG.rollback)
    ? rollbackBanner(NETCFG.rollback) : "";
  return `<div class="span-12"><div class="card"><div class="card-h"><span>מצב הרשת</span><div>${actionsHtml}</div></div><div class="card-b">${rb}${notSourced}<p style="margin:0;color:var(--muted);font-size:11px" dir="ltr">${esc(foot)}</p></div></div></div>`;
}

function renderNicCard(n, cfgRow, admin) {
  const trunk = n.trunk ? ` <span class="tag warn">רשת המכללה</span>` : "";
  const missing = n.present ? "" : ` <span class="tag warn">לא קיים במערכת</span>`;
  const desc = n.description
    ? `<p style="margin:0 0 8px;color:var(--muted);font-size:11px">${esc(n.description)}</p>` : "";
  const live = (cfgRow ? cfgRow.live_addresses : n.addresses) || [];
  const gaps = cfgRow && cfgRow.mismatches && cfgRow.mismatches.length
    ? `<div class="notice warn">${esc(cfgRow.mismatches.join(" · "))}</div>` : "";
  const dhcp = typeof nicMode === "function" ? nicMode(n) : { html: "—" };
  const hl = NIC_HIGHLIGHT === n.name ? " sel" : "";
  let cfgText = "—";
  if (cfgRow) {
    if (cfgRow.mode === "static") {
      cfgText = [cfgRow.address, cfgRow.netmask].filter(Boolean).join(" / ") || "—";
      if (cfgRow.gateway) cfgText += " · שער " + cfgRow.gateway;
      if ((cfgRow.dns || []).length) cfgText += " · DNS " + cfgRow.dns.join(", ");
    } else {
      cfgText = cfgRow.mode_he || cfgRow.mode || "—";
    }
  }
  const light = (cfgRow && typeof netLight === "function") ? netLight(cfgRow) : "bad";
  const serverIp = n.server_ip || (cfgRow && cfgRow.address) || (live[0] || "").split("/")[0] || "—";
  const speed = typeof speedLabel === "function" ? speedLabel(n) : (n.state || "");
  const actions = admin ? `<div class="action-strip">
      <button class="btn" data-net-edit="${esc(n.name)}">עריכת כתובת</button>
      <button class="btn" data-nic-edit="${esc(n.name)}">DHCP</button>
      <button class="btn flat" data-nic-desc="${esc(n.name)}">תיאור</button>
      <button class="btn flat" data-net-preview="${esc(n.name)}">הקובץ</button>
      <button class="btn danger flat" data-nic-forget="${esc(n.name)}">שכחה</button>
    </div>` : "";
  return `<div class="span-6"><div class="card${hl}" data-nic="${esc(n.name)}">
    <div class="card-h"><span dir="ltr">${esc(n.name)}${trunk}${missing}</span><small>${esc(speed)}${n.mac ? " · " + esc(n.mac) : ""}</small></div>
    <div class="card-b">${desc}
      <div class="list">
        <div class="list-row"><div class="list-main"><strong>בפועל</strong><small class="mono" dir="ltr">${esc(live.join(" · ")) || "—"}</small></div><span class="hlight ${light}"></span></div>
        <div class="list-row"><div class="list-main"><strong>מוגדר</strong><small class="mono" dir="ltr">${esc(cfgText)}</small></div></div>
        <div class="list-row"><div class="list-main"><strong>כתובת השרת</strong><small class="mono" dir="ltr">${esc(serverIp)}</small></div></div>
        <div class="list-row"><div class="list-main"><strong>DHCP</strong><small>${dhcp.html}</small></div></div>
      </div>
      ${gaps}${actions}
    </div></div></div>`;
}

function nic() {
  const nics = typeof NICS !== "undefined" ? NICS : null;
  if (!NETCFG && (!nics || !nics.length)) return pagePlaceholder();
  const admin = ME && ME.role === "admin";
  const rows = nicUnion();
  const cards = rows.length
    ? rows.map(({ n, cfgRow }) => renderNicCard(n, cfgRow, admin)).join("")
    : `<div class="span-12"><div class="card"><div class="card-b"><div class="empty">לא נמצאו כרטיסי רשת.</div></div></div>`;
  const addBtn = admin
    ? `<button class="btn primary" id="nic-add" onclick="addNic()">+ כרטיס</button>` : "";
  const routes = NETCFG
    ? `<div class="span-12"><div class="card"><div class="card-h"><span>נתיבים סטטיים</span>${admin ? `<button class="btn" onclick="addRoute()">+ נתיב</button>` : ""}</div><div class="card-b" id="netroutes-body"></div></div></div>`
    : "";
  return `<div class="grid">${netBannerHtml(addBtn)}${cards}${routes}</div>`;
}

function populateSidebarNics() {
  const tree = document.getElementById("nicTree");
  if (!tree) return;
  const nics = (typeof NICS !== "undefined" && NICS) ? NICS : [];
  const parent = document.getElementById("nicNode") || tree.previousElementSibling;
  const arrow = document.getElementById("nicTreeArrow")
    || (parent && parent.querySelector(".tree-arrow, .tree-arrow-sp"));
  if (!nics.length) {
    tree.innerHTML = "";
    tree.setAttribute("hidden", "");
    if (arrow) {
      arrow.className = "tree-arrow-sp";
      arrow.textContent = "";
      arrow.onclick = null;
    }
    return;
  }
  if (arrow) {
    const open = !tree.hasAttribute("hidden");
    arrow.className = "tree-arrow";
    arrow.dataset.open = String(open);
    arrow.textContent = open ? "▾" : "▸";
    arrow.onclick = (event) => {
      event.stopPropagation();
      toggleInventoryGroup(parent, "nicTree");
    };
  }
  tree.innerHTML = nics.map((n) => {
    const on = (current === "nic" && NIC_HIGHLIGHT === n.name) ? " active" : "";
    return `<div class="inventory-node${on}" role="treeitem" tabindex="0" onclick="selectSidebarNic('${esc(n.name)}')"><span class="tree-arrow-sp"></span><span>${uiIcon("network")}</span><span dir="ltr">${esc(n.name)}</span></div>`;
  }).join("");
}

function selectSidebarNic(name) {
  NIC_HIGHLIGHT = name;
  if (current !== "nic") {
    selectPageById("nic");
    return;
  }
  renderCurrent();
  const card = document.querySelector(`#content .card[data-nic="${CSS.escape(name)}"]`);
  if (card) card.scrollIntoView({ block: "nearest" });
}

async function loadNetPages() {
  try {
    await loadNet();
  } catch (e) {
    toast("טעינת כרטיסי הרשת נכשלה: " + e.message);
    return;
  }
  try {
    await loadNetcfg();
  } catch (e) {
    if (ME && ME.role === "admin") toast("טעינת הגדרות הרשת נכשלה: " + e.message);
  }
  populateSidebarNics();
  if (current === "nic" || current === "netdeploy") renderCurrent();
  if (NIC_HIGHLIGHT) {
    const card = document.querySelector(`#content .card[data-nic="${CSS.escape(NIC_HIGHLIGHT)}"]`);
    if (card) card.scrollIntoView({ block: "nearest" });
  }
}

function refreshNetPages() {
  populateSidebarNics();
  if (current !== "nic" && current !== "netdeploy") return;
  if (refreshNetPages._busy) return;
  refreshNetPages._busy = true;
  try { renderCurrent(); }
  finally { refreshNetPages._busy = false; }
}

function wireNetPage() {
  const list = (NETCFG && NETCFG.interfaces) || [];
  const byName = new Map(list.map((r) => [r.name, r]));
  const fallback = (name) => (typeof bodyOf === "function"
    ? bodyOf({ name, mode: "manual", address: "", netmask: (typeof MASKS !== "undefined" && MASKS[0]) || "255.255.255.0",
               gateway: "", dns: [], routes: [] }, {})
    : { name, mode: "manual", address: "", netmask: "255.255.255.0", gateway: "", dns: [], routes: [] });
  document.querySelectorAll("[data-net-edit]").forEach((b) => {
    b.onclick = () => editAddress(byName.get(b.dataset.netEdit) || fallback(b.dataset.netEdit));
  });
  document.querySelectorAll("[data-net-preview]").forEach((b) => {
    b.onclick = () => showFile(byName.get(b.dataset.netPreview)
      || { name: b.dataset.netPreview, mode: "manual", address: "",
           netmask: (typeof MASKS !== "undefined" && MASKS[0]) || "255.255.255.0",
           gateway: "", dns: [], routes: [] });
  });
  if (typeof wireNicActions === "function") wireNicActions(typeof NICS !== "undefined" ? NICS : []);
  if (typeof startCountdown === "function") startCountdown();
  if (typeof renderRoutes === "function") renderRoutes();
  populateSidebarNics();
}

function editDeployNic() {
  const rows = nicUnion();
  const enabled = rows.filter(({ n }) => n.enabled);
  const serving = rows.filter(({ n }) => (n.dhcp_live || {}).state === "serving");
  const focus = enabled[0] || serving[0] || rows[0];
  if (!focus) { toast("אין כרטיס לעריכה"); return; }
  editNic(focus.n);
}

function dhcpStateLine(n) {
  const live = (n && n.dhcp_live) || { state: "unknown" };
  const state = live.state || "unknown";
  const cls = dhcpLiveClass(state);
  const statusCls = cls === "ok" ? "ok" : cls === "warn" ? "warn" : "";
  const label = n.dhcp_live_label
    || (state === "serving" ? "משרת"
      : state === "configured_not_running" ? "מוגדר, השירות אינו פועל"
      : state === "off" ? "כבוי" : "לא ידוע");
  const detail = live.detail ? ` — ${esc(live.detail)}` : "";
  const stored = `שמורה בקונסולה: ${n.enabled ? "מופעל" : n.proxy ? "proxy" : "כבוי"}`;
  const diverged = n.dhcp_diverged
    ? `<div class="notice warn">המוגדר אינו תואם למצב הפעיל</div>` : "";
  return `<div class="metric"><div><strong>מצב DHCP</strong><span>${esc(stored)}</span></div><div class="status ${statusCls}"><i></i>${esc(label)}${detail}</div></div>${diverged}`;
}

function netdeploy() {
  const nics = typeof NICS !== "undefined" ? NICS : null;
  if (nics == null || (!nics.length && !NETCFG)) return pagePlaceholder();
  const admin = ME && ME.role === "admin";
  const rows = nicUnion();
  const enabled = rows.filter(({ n }) => n.enabled);
  const serving = rows.filter(({ n }) => (n.dhcp_live || {}).state === "serving");
  const focus = enabled[0] || serving[0] || null;
  const picker = rows.map(({ n }) => {
    const live = n.dhcp_live || { state: "unknown" };
    const cls = dhcpLiveClass(live.state);
    const statusCls = cls === "ok" ? "ok" : cls === "warn" ? "warn" : "";
    const mark = n.enabled ? " <span class=\"tag green\">רשת הפצה</span>" : "";
    const btn = admin
      ? `<button class="btn" data-nic-edit="${esc(n.name)}">${n.enabled ? "עריכת DHCP" : "הגדר כרשת הפצה"}</button>`
      : "";
    return `<div class="list-row"><div class="list-main"><strong dir="ltr">${esc(n.name)}</strong><small>${esc(n.dhcp_live_label || live.state)}${mark}</small></div><div class="list-side"><span class="status ${statusCls}"><i></i>${esc(n.dhcp_live_label || "לא ידוע")}</span> ${btn}</div></div>`;
  }).join("") || `<div class="empty">לא נמצאו כרטיסי רשת.</div>`;

  let detail;
  if (!focus) {
    detail = `<div class="empty">אין כרטיס מוגדר כרשת הפצה, ואין כרטיס שמשרת DHCP כרגע.</div>`;
  } else {
    const n = focus.n;
    const range = (n.range_start && n.range_end)
      ? `${n.range_start}–${n.range_end}` : "—";
    detail = `${dhcpStateLine(n)}
      <div class="statrow">
        <div class="statbox"><div class="n mono" dir="ltr">${esc(n.name)}</div><div class="l">כרטיס</div></div>
        <div class="statbox"><div class="n mono" dir="ltr">${esc(n.server_ip || "—")}</div><div class="l">כתובת השרת</div></div>
        <div class="statbox"><div class="n mono" dir="ltr">${esc(range)}</div><div class="l">טווח DHCP</div></div>
      </div>
      <div class="list" style="margin-top:12px">
        <div class="list-row"><div class="list-main"><strong>מסכה</strong></div><div class="list-side mono" dir="ltr">${esc(n.netmask || "—")}</div></div>
        <div class="list-row"><div class="list-main"><strong>שער</strong></div><div class="list-side mono" dir="ltr">${esc(n.gateway || "—")}</div></div>
        <div class="list-row"><div class="list-main"><strong>DNS</strong></div><div class="list-side mono" dir="ltr">${esc((n.dns || []).join(", ") || "—")}</div></div>
        <div class="list-row"><div class="list-main"><strong>חכירה</strong></div><div class="list-side mono" dir="ltr">${esc(n.lease || "—")}</div></div>
      </div>
      ${admin ? `<div class="action-strip"><button class="btn primary" data-nic-edit="${esc(n.name)}">עריכת DHCP</button><button class="btn" onclick="previewDnsmasq()">קבצי dnsmasq</button></div>` : ""}`;
  }

  return `<div class="grid">${netBannerHtml()}
<div class="span-8"><div class="card"><div class="card-h">רשת הפצה</div><div class="card-b">${detail}</div></div></div>
<div class="span-4"><div class="card"><div class="card-h">כרטיסים</div><div class="card-b"><div class="list">${picker}</div></div></div></div>`;
}

function healthCheckById(id) {
  return (Array.isArray(HEALTH) ? HEALTH : []).find((c) => c.id === id) || null;
}

function ports() {
  // #822: הרשימה עצמה מגיעה מהשרת (`/api/console/ports`) — לא defs
  // קבוע ב-JS — כדי שפורט חדש (5900) או מצב אמיתי (5900 סגור על מכונה
  // ספציפית) לא ייעלמו מאחורי רשימה שכוחה מ-2026-09-13.
  if (portsError) {
    return `<div class="grid"><div class="span-12"><div class="notice err" role="alert">לא הצלחתי לקרוא את רשימת הפורטים: ${esc(portsError)}</div></div></div>`;
  }
  if (!PORTS) return pagePlaceholder();
  const soon = `title="בקרוב (נדרש endpoint)"`;
  const cards = PORTS.map((p) => {
    const cls = healthStatusClass(p.state);
    const label = healthStatusLabel(p.state);
    return `<div class="span-6"><div class="card">
      <div class="card-h"><span>${esc(p.name)} <small class="mono" dir="ltr">${esc(p.port)}/${esc(p.proto)}</small></span><span class="status ${cls}"><i></i>${esc(label)}</span></div>
      <div class="card-b">
        <p style="margin:0 0 6px;color:var(--muted);font-size:11px">${esc(p.desc)} — יעד: ${esc(p.target)}</p>
        <p style="margin:0 0 10px;font-size:11px">${esc(p.detail)}</p>
        <p style="margin:0 0 10px;color:var(--muted);font-size:11px">${esc(p.note)}</p>
        <div class="action-strip">
          <button class="btn" disabled ${soon}>פתיחה</button>
          <button class="btn" disabled ${soon}>סגירה</button>
          <button class="btn" disabled ${soon}>שינוי</button>
        </div>
      </div></div></div>`;
  }).join("");
  return `<div class="grid">${cards}</div>`;
}

function monitorPage() {
  if (!MONITOR) {
    return monitorError
      ? `<div class="grid"><div class="span-12"><div class="notice err" role="alert">לא הצלחתי לקרוא את רשימת המוניטור: ${esc(monitorError)}</div></div></div>`
      : pagePlaceholder();
  }
  const enabled = MONITOR.settings && MONITOR.settings.enabled === true;
  const toggle = `<div class="span-12"><div class="card"><div class="health-row">
      <b>מוניטור לתחנות</b>
      <span class="switch ${enabled ? "on" : ""}" onclick="monitorToggle(${!enabled})"></span>
      <span class="sub">${enabled ? "דלוק" : "כבוי"} — שינוי תופס באתחול הבא של כל תחנה, לא במכונות שכבר רצות</span>
    </div></div></div>`;
  const off = enabled
    ? ""
    : `<div class="span-12"><div class="notice warn" role="status">מתג המוניטור לתחנות כבוי — מכונה שעולה עכשיו אינה מפעילה שירות צפייה, וחיבור אליה ייכשל.</div></div>`;
  const list = MONITOR.machines || [];
  if (!list.length) {
    return `<div class="grid">${toggle}${off}<div class="span-12"><div class="card"><div class="card-b"><div class="empty">אין מחשבי בנייה או שיכפול רשומים</div></div></div></div></div>`;
  }
  const roleLabel = { build: "מחשב בנייה", cloner: "מחשב שיכפול" };
  const cards = list.map((m) => {
    const macEnc = encodeId(m.mac);
    const status = m.online
      ? `<span class="status ok"><i></i>מחובר</span>`
      : `<span class="status"><i></i>לא מחובר</span>`;
    return `<div class="span-6"><div class="card">
      <div class="card-h"><span>${esc(m.name || m.mac)} <small class="muted">${esc(roleLabel[m.role] || m.role)}</small></span>${status}</div>
      <div class="card-b">
        ${m.prompt ? `<div class="notice warn" role="status">${waitingHtml(m)}</div>` : ""}
        <div class="detail-grid"><div class="detail-box"><span class="k">MAC</span><span class="v mono" dir="ltr">${esc(m.mac)}</span></div><div class="detail-box"><span class="k">כתובת IP</span><span class="v mono" dir="ltr">${m.ip ? esc(m.ip) : "—"}</span></div></div>
        <div class="action-strip"><button class="btn primary" onclick="monitorMachine('${macEnc}')" ${m.online ? "" : `disabled title="המכונה אינה מחוברת"`}>מוניטור</button></div>
      </div></div></div>`;
  }).join("");
  return `<div class="grid">${toggle}${off}${cards}</div>`;
}

const pages = {
  settings: {crumb:"Settings", title:"Settings", desc:"Console policy and branding", tabs:["Settings"], render:settingsPage},
  branches: {crumb:"סניפים", title:"סניפים", desc:"השרתים המשניים של הראשי הזה: מצב חיבור, המחשבים שלהם, העברת אימג'ים ומוניטור", tabs:["סניפים", "מרשם"], render:pagePlaceholder},
  // ‏#936: הדף של משני אחד (נבחר בעץ, BRANCH_NODE) — אותם נתונים ואותן
  // פונקציות של "סניפים" (branches.js), לפי לשונית.
  branch: {crumb:"שרת משני", title:"שרת משני", desc:"מצב חיבור, המחשבים שלו, האימג'ים שהועברו אליו וההעברות — כפי שהראשי מדד מולו", tabs: BRANCH_VIEWS.map((v) => v[1]), render:pagePlaceholder},
  // ‏#954: הדף מצייר את הכותרת והלשוניות שלו (own) לפי שפת העיצוב החדשה; הלשוניות בפועל לפי תפקיד (homeTabs).
  home: { crumb: "סקירה כללית", title: "סקירה כללית", tabs: ["סיכום", "משימות", "אירועים"], render: home, load: loadHome, own: true },
  images: { crumb: "אימג'ים", title: "ספריית אימג'ים", tabs: imagesTabs(), render: images, load: loadImages, own: true },
  deploy: { crumb: "סבבי הפצה", title: "סבבי הפצה", desc: "פתיחת סבב, צירוף תחנות ומעקב אחר כתיבה לכל מחשב", tabs: ["סבבים", "הצטרפות חיה"], render: deploy, load: refreshStatus },
  machines: { crumb: "מחשבים", title: "מחשבים", tabs: ["כל המחשבים", "נראו ברשת", "דיסקים אדומים"], render: machines, load: loadMachines, own: true },
  health: { crumb: "בריאות שרת", title: "בריאות שרת", desc: "שירותים, מאזינים ותהליכים המשרתים את תהליך הפריסה", tabs: ["סקירה", "שירותים", "בדיקות"], render: health, load: loadHealth },
  network: { crumb: "רשת", title: "רשת", desc: "הגדרות כתובת, gateway, DNS וממשק שידור", tabs: ["הגדרות", "פורטיים", "מולטיקאסט"], render: network, load: loadNetcfgData },
  permissions: { crumb: "הרשאות", title: "הרשאות", desc: "ניהול משתמשים ותפקידי גישה לקונסולה", tabs: ["משתמשים", "תפקידים"], render: permissions, load: loadUsersData },
  logs: { crumb: "יומן", title: "יומן מערכת", desc: "אירועים תפעוליים בשפה טבעית עם הקשר של מחשב וכיתה", tabs: ["אירועים", "Audit"], render: logs, load: loadJournalData },
  monitor: { crumb: "מוניטור", title: "מוניטור", desc: "צפייה ושליטה מרחוק במחשבי הבנייה והשיכפול", tabs: ["מכונות"], render: monitorPage, load: loadMonitor },
  drivers: { crumb: "דרייברים", title: "דרייברים", desc: "חבילות דרייברים לפי חומרה (PCI/דגם) — מונחות על הדיסק אחרי השחזור, מותקנות בעלייה הראשונה", tabs: ["חבילות"], render: () => driversPage(), load: () => loadDrivers() },   // lazily: drivers.js loads after this file
  netdeploy: { crumb: "רשת הפצה", title: "רשת הפצה", desc: "איזה כרטיס משרת את וילן ההפצה, ומצב ה-DHCP כפי שנקרא בפועל", tabs: ["סקירה"], render: netdeploy, load: loadNetPages },
  ports: { crumb: "פורטים", title: "פורטים", desc: "פורטי HTTP, TFTP, PXE, מולטיקאסט, מוניטור, קיוסק ו-SSH — מצב האזנה כפי שנקרא מהשרת", tabs: ["סקירה"], render: ports, load: loadPorts },
  nic: { crumb: "חיבורים פיזיים", title: "חיבורים פיזיים", desc: "כרטיסים, כתובות חיות מול מוגדרות, וכתובת השרת בכל כרטיס", tabs: ["סקירה"], render: nic, load: loadNetPages },
};
let current = "home";
let currentTab = 0;
let searchQuery = "";

function tabRender(pageId, index) {
  const renderers = {
    deploy: [deploy, emptyDataCard],
    health: [health, () => `<div class="card"><div id="ssh-body"></div></div>`, health],
    network: [network, emptyDataCard, emptyDataCard],
    permissions: [usersAdminPage, permissions],
    logs: [journalPage, logs],
    settings: [settingsPage],
    branches: [() => `<div id="branch-cards" class="stack"></div>`, () => `<div id="branches-body" class="stack"></div>`],
    branch: BRANCH_VIEWS.map(([view]) => () => `<div id="branch-view" class="stack" data-view="${view}">${pagePlaceholder()}</div>`),
    monitor: [monitorPage],
    drivers: [() => driversPage()],   // #720 — drivers.js
    netdeploy: [netdeploy],
    ports: [ports],
    nic: [nic],
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
  if (id === "nic" || id === "netdeploy") wireNetPage();
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
  if (id === "nic" || id === "netdeploy") wireNetPage();
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
  if (current === "nic" || current === "netdeploy") wireNetPage();
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
    deploy: [["סבב הפצה חדש", "soon()"], ["התחל סבב", "startSelectedRound()"], ["ייצוא סבבים", "soon()"]],
    health: [["בדיקת בריאות", "loadHealth()"], ["פרטי שירותים", "soon()"]],
    network: [["בדיקת קישוריות", "soon()"], ["שמור הגדרות", "saveNetwork()"], ["Rollback", "soon()"]],
    permissions: [["משתמש חדש", "openNewUser()"], ["תפקידי מערכת", "openRolesDrawer()"]],
    logs: [["סינון יומן", "openLogFilter()"], ["ייצוא CSV", "soon()"]],
    monitor: [["רענון", "refreshPage()"]],
    drivers: [["ייבוא חבילה", "openDriverImport()"], ["רענון", "refreshPage()"]],
    nic: [["הוספת כרטיס", "addNic()"], ["רענון", "refreshPage()"]],
    netdeploy: [["עריכת DHCP", "editDeployNic()"], ["קבצי dnsmasq", "previewDnsmasq()"]],
    ports: [["רענון", "refreshPage()"]],
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
  roundDrawerOpen = false;
  document.getElementById("drawerTitle").textContent = title;
  document.getElementById("drawerBody").innerHTML = body;
  document.getElementById("drawer").classList.add("open");
  document.getElementById("detailBackdrop").style.display = "block";
}

function closeDrawer() {
  drawerGeneration++;
  roundDrawerOpen = false;
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
  openDrawer("החשבון שלי", `<div class="detail-grid"><div class="detail-box"><span class="k">משתמש</span><span class="v">${name}</span></div><div class="detail-box"><span class="k">תפקיד</span><span class="v">${role}</span></div><div class="detail-box"><span class="k">סביבה</span><span class="v">ImageCtl Console</span></div><div class="detail-box"><span class="k">מצב</span><span class="v success-text">מחובר</span></div></div>`);
}

function openSessionInfo() {
  closeUserMenu();
  const name = esc(ME && ME.username || "");
  openDrawer("פרטי Session", `<div class="detail-grid"><div class="detail-box"><span class="k">משתמש</span><span class="v">${name}</span></div><div class="detail-box"><span class="k">Session</span><span class="v">Authenticated</span></div></div>`);
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
function wakeMachine() { soon(); }
function openNewImage() { soon(); }
function verifyLibrary() { soon(); }
function openNewDeployment() { soon(); }
function startSelectedRound() { startRound(); }
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
  // בכניסה הראשונה לא "זוכרים" את מה שהמערכת הכתיבה — רק אם נגעו במתג.
  applyTheme(currentTheme(), localStorage.getItem("imagectl-theme") !== null);
  loadLogo();
  init();
  api("/me").then((me) => { ME = me; return showApp(); }).catch(() => showLogin());
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
  CAPTURE_TASKS = all;
  renderActivity();
  // ‏#927: קליטה שהסתיימה עם אזהרה (המקור לא הוחזר לגודלו) אינה נעלמת —
  // הודעה מעל טבלת הספרייה. קליטה בתהליך היא שורה בטבלה (#954 גל 2), לא כאן.
  const bar = $("#capture-bar");
  if (bar) bar.innerHTML = all.filter((t) => t.state === "done" && t.error).map(captureWarningHtml).join("");
  if (typeof imagesTasksChanged === "function" && imagesTasksChanged(all)) renderCurrent();
}

async function openCapture() {
  if (!isAdmin()) return;
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
        id: "mac", label: "מחשב בנייה", type: "select",
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
const SSH_LIGHT = {
  open: { cls: "warn", text: "פתוח בפועל" },
  closed: { cls: "ok", text: "סגור בפועל" },
  unknown: { cls: "bad", text: "לא ניתן לאמת" },
};

function sshLight(kind) {
  const light = SSH_LIGHT[kind] || SSH_LIGHT.unknown;
  return `<span class="hlight ${light.cls}" title="${esc(light.text)}"></span>
    <span class="sub">${esc(light.text)}</span>`;
}

/* שלושה מצבים: null = לא נבדק, ואסור שייראה כמו "סגור". */
const nicLight = (nic) =>
  nic.listening === null ? "unknown" : nic.listening ? "open" : "closed";

let SSH_STATE = null;

async function loadSsh() {
  const host = $("#ssh-body");
  if (!host || !isAdmin()) return;
  SSH_STATE = await api("/ssh");
  if (host !== $("#ssh-body")) return;
  const stations = SSH_STATE.stations;
  const rows = SSH_STATE.interfaces.map((nic) => `
    <div class="health-row">
      <b>${esc(nic.name)}</b>
      <span class="switch ${nic.enabled ? "on" : ""}" data-ssh-nic="${esc(nic.name)}"></span>
      ${sshLight(nicLight(nic))}
      <span class="sub">${esc((nic.addresses || []).join(", ") || "ללא כתובת IPv4")}</span>
    </div>`).join("");
  const listeners = SSH_STATE.listeners;
  const foot = listeners.checked
    ? `מאזינים בפורט ${listeners.port}: ${listeners.addresses.join(", ") || "אף אחד"}`
    : `טבלת הסוקטים לא נקראה (${listeners.reason}) — אין לדעת מה פתוח`;
  $("#ssh-body").innerHTML = `
    <div class="health-row">
      <b>תחנות (imagectl.debug)</b>
      <span class="switch ${stations.enabled ? "on" : ""}" data-ssh-stations="1"></span>
      ${sshLight(stations.evidence)}
      <span class="sub">${esc(stations.detail)}</span>
    </div>
    ${rows || `<div class="health-row"><span class="sub">אין כרטיסי רשת</span></div>`}
    <p class="pad sub">${esc(foot)}</p>`;

  $("#ssh-body").querySelectorAll("[data-ssh-stations]").forEach((el) =>
    el.onclick = () => sshToggle(
      "/ssh/stations", !stations.enabled, stations.confirm_word,
      !stations.enabled,
      "פתיחת SSH ומעטפת טכנאי בכל התחנות",
      "כל מחשב שיעלה יריץ dropbear. המפתח הציבורי ארוז ב-initramfs, "
      + "שנמשך ב-HTTP פתוח מווילן ההפצה."));

  $("#ssh-body").querySelectorAll("[data-ssh-nic]").forEach((el) => {
    const nic = SSH_STATE.interfaces.find((n) => n.name === el.dataset.sshNic);
    const open = SSH_STATE.interfaces.filter((n) => n.enabled).map((n) => n.name);
    // סגירה היא הכיוון הבטוח ולכן לחיצה אחת — חוץ מהדלת האחרונה,
    // שאחריה אין SSH לשרת בכלל.
    const last = !nic.enabled ? false : open.length === 1 && open[0] === nic.name;
    el.onclick = () => sshToggle(
      `/ssh/interfaces/${encodeId(nic.name)}`, !nic.enabled, nic.name,
      !nic.enabled || last,
      nic.enabled ? `סגירת SSH לשרת על ${nic.name}`
        : `פתיחת SSH לשרת על ${nic.name}`,
      nic.enabled
        ? "זו הדלת האחרונה שפתוחה — אחריה אין SSH לשרת מאף רשת."
        : "‏sshd יאזין בוילן הזה. אם זה וילן הכיתות — הוא ייפתח לסטודנטים.");
  });
}

function sshToggle(path, enabled, word, needsConfirm, title, sub) {
  const send = async (extra) => {
    const result = await put(path, { enabled, ...extra });
    if (result.apply_error) toast("ההחלה נכשלה: " + result.apply_error);
    else if (!result.verified)
      toast("נשמר — אבל מה שמאזין לא תואם. ראו את שורות ה-SSH בבריאות.");
    else toast(enabled ? "נפתח, ואומת מול המצב בפועל" : "נסגר, ואומת מול המצב בפועל");
    await loadSsh();
  };
  if (!needsConfirm) {
    send({}).catch((error) => toast(error.message));
    return;
  }
  sheet({
    title, sub, danger: true, submitLabel: enabled ? "פתח" : "סגור",
    verify: { label: "להמשך יש להקליד בדיוק:", mustEqual: word },
    onSubmit: () => send({ confirm: word }),
  });
}


function settingsPage() { return `<div class="grid"><div class="span-6"><div class="card">        <form id="settings-form" class="pad form-grid">
          <label class="check">
            <input type="checkbox" id="set-login">
            שחזור תחנה בודדת דורש כניסה (מומלץ; כבו רק להדגמה)
          </label>
          <label>המתנה מהמצטרף האחרון (שניות)
            <input type="number" id="set-wait" min="30" step="30">
          </label>
          <label>ניתוק אוטומטי בחוסר פעילות (שניות)
            <input type="number" id="set-idle" min="60" step="30">
          </label>
          <label class="check">
            <input type="checkbox" id="set-class-deploy">
            הפצה לכיתות ממחשב הבנייה (כבוי במהדורת השיכפול; הכרטיס יורד מהתפריט והשרת מסרב לסבב)
          </label>
          <label class="check">
            <input type="checkbox" id="set-update-enabled">
            אפשר עדכון השרת מול הריפו הציבורי (כבוי כברירת מחדל; החיבור היוצא נפתח רק בזמן בדיקה/עדכון)
          </label>
          <button class="btn primary" type="submit">שמור</button>
          <p id="settings-saved" class="ok"></p>
        </form>
      </div></div><div class="span-6"><div class="card">
        <div class="ptitle">עדכון שרת</div>
        <div class="pad">
          <p class="sub">גרסה נוכחית: <b id="update-current">—</b></p>
          <p class="sub hidden" id="update-previous-row">גרסה קודמת (לחזרה): <b id="update-previous"></b></p>
          <div id="update-disabled-note" class="sub">העדכון כבוי. הדליקו את המתג משמאל כדי לבדוק ולעדכן.</div>
          <div id="update-active-block" class="hidden">
            <div class="row" style="margin-top:8px">
              <button class="btn" id="update-check-btn">בדוק עדכון</button>
              <button class="btn primary hidden" id="update-apply-btn">עדכן</button>
              <button class="btn danger hidden" id="update-revert-btn">חזור לגרסה הקודמת</button>
            </div>
            <p id="update-check-result" class="sub"></p>
            <p id="update-status-line" class="sub"></p>
          </div>
        </div>
      </div></div><div class="span-6"><div class="card">
        <div class="ptitle">לוגו</div>
        <div class="pad">
          <div class="logo-row">
            <div class="logo-preview" id="logo-preview"></div>
            <div>
              <p class="sub">מחליף את הסמל בכותרת ובמסך הכניסה.</p>
              <p class="sub">PNG, JPG, WEBP או SVG · עד 2MB.</p>
            </div>
          </div>
          <div class="row" style="margin-top:12px">
            <button class="btn" id="logo-pick">בחירת קובץ</button>
            <button class="btn danger hidden" id="logo-clear">הסרה</button>
          </div>
          <p class="error" id="logo-error"></p>
          <input type="file" id="logo-input" class="hidden"
                 accept="image/png,image/jpeg,image/webp,image/svg+xml">
        </div>
      </div></div></div>`; }
async function loadSettings() {
  const host = $("#settings-form");
  const s = await api("/settings");
  if (!host || host !== $("#settings-form")) return;
  $("#set-login").checked = s.recovery_require_login === "true";
  $("#set-wait").value = Number(s.session_wait_seconds);
  $("#set-idle").value = Number(s.console_idle_seconds);
  $("#set-class-deploy").checked = s.class_deploy_enabled === "true";
  $("#set-update-enabled").checked = s.update_enabled === "true";
  await loadLogoSettings();
  await loadUpdateInfo();

  if (host === $("#settings-form")) wireSettings();
}
function wireSettings() {
$("#settings-form").onsubmit = async (event) => {
  try {
  event.preventDefault();
  await post("/settings", {
    recovery_require_login: $("#set-login").checked ? "true" : "false",
    session_wait_seconds: String($("#set-wait").value),
    console_idle_seconds: String($("#set-idle").value),
    class_deploy_enabled: $("#set-class-deploy").checked ? "true" : "false",
    update_enabled: $("#set-update-enabled").checked ? "true" : "false",
  });
  ME.idle_seconds = Number($("#set-idle").value);   // תקף מיידית, בלי כניסה מחדש
  startIdleWatch();
  await loadUpdateInfo();   // המתג יכול היה להידלק/לכבות כרגע
  $("#settings-saved").textContent = "נשמר.";
  setTimeout(() => { if ($("#settings-saved")) $("#settings-saved").textContent = ""; }, 2000);
} catch (error) { toast(error.message); }
};
$("#logo-pick").addEventListener("click", () => $("#logo-input").click());

$("#logo-input").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  if (!file) return;
  const response = await fetch("/api/console/branding/logo", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": file.type }, body: file,
  });
  if (!response.ok) {
    let message = "שגיאה " + response.status;
    try { message = (await response.json()).detail || message; } catch (e) {}
    $("#logo-error").textContent = message;
    return;
  }
  $("#logo-error").textContent = "";
  await loadLogoSettings();
  toast("הלוגו הוחלף.");
});

$("#logo-clear").addEventListener("click", () => confirmSheet(
  "הסרת הלוגו", "הקונסולה תחזור לסמל ברירת המחדל.", "הסר",
  async () => { await del("/branding/logo"); await loadLogoSettings(); }));

$("#update-check-btn").onclick = checkForUpdate;
$("#update-apply-btn").onclick = () => confirmUpdateAction(
  "עדכון שרת", UPDATE_INFO.latest, "apply", UPDATE_INFO.latest);
$("#update-revert-btn").onclick = () => confirmUpdateAction(
  "חזרה לגרסה הקודמת", UPDATE_INFO.previous, "revert", UPDATE_INFO.previous);
}

let UPDATE_INFO = {};

async function loadUpdateInfo() {
  if (!$("#update-current")) return;
  const info = await api("/update");
  UPDATE_INFO = info;
  $("#update-current").textContent = info.current || "לא ידועה (אין תגית git על העץ)";
  if (info.previous) {
    $("#update-previous-row").classList.remove("hidden");
    $("#update-previous").textContent = info.previous;
  } else {
    $("#update-previous-row").classList.add("hidden");
  }
  $("#update-disabled-note").classList.toggle("hidden", info.enabled);
  $("#update-active-block").classList.toggle("hidden", !info.enabled);
  $("#update-apply-btn").classList.add("hidden");
  $("#update-revert-btn").classList.toggle("hidden", !info.previous);
  $("#update-check-result").textContent = "";
  if (info.enabled) await loadUpdateStatus();
}

async function loadUpdateStatus() {
  const status = await api("/update/status");
  const line = $("#update-status-line");
  if (!line) return;
  if (status.state === "idle" || !status.state) { line.textContent = ""; return; }
  if (status.state === "failed") {
    line.textContent = `העדכון ל-${status.tag} נכשל: ${status.error || ""}`;
    line.className = "sub error";
  } else if (status.state === "applying" && !status.verified) {
    line.textContent = `העדכון ל-${status.tag} הופעל — ממתין לאתחול השרת. `
      + "הראיה החיובית: גרסת השרת אחרי האתחול תואמת את התג.";
    line.className = "sub";
  } else if (status.state === "done" && status.verified) {
    line.textContent = `אומת: השרת רץ על ${status.tag}.`;
    line.className = "sub ok";
  } else {
    line.textContent = "";
  }
}

async function checkForUpdate() {
  try {
    const result = await post("/update/check", {});
    UPDATE_INFO.latest = result.latest;
    if (!result.latest) {
      $("#update-check-result").textContent = result.reason || "לא נמצאה גרסה חדשה יותר.";
      $("#update-apply-btn").classList.add("hidden");
    } else if (result.available) {
      $("#update-check-result").textContent = `יש עדכון: ${result.current || "?"} → ${result.latest}`;
      $("#update-apply-btn").textContent = `עדכן ל-${result.latest}`;
      $("#update-apply-btn").classList.remove("hidden");
    } else {
      $("#update-check-result").textContent = `כבר על הגרסה העדכנית (${result.current}).`;
      $("#update-apply-btn").classList.add("hidden");
    }
  } catch (e) { toast(e.message); }
}

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
      await loadUpdateInfo();
    },
  });
}
async function loadUsersAdmin() {
  const host = $("#users-table tbody");
  const list = await api("/users");
  if (!host || host !== $("#users-table tbody")) return;
  USERS = list;
  $("#users-table tbody").innerHTML = list.map((u) => `<tr>
    <td>${esc(u.username)}${u.username === ME.username ? ' <span class="tag">אתם</span>' : ""}${u.disabled ? ' <span class="tag danger">חסום</span>' : ""}</td>
    <td>${u.role === "admin" ? "מנהל" : "הפצה"}</td>
    <td>${u.created_at.slice(0, 10)}</td>
    <td>
      <button class="btn" data-edit-user="${esc(u.username)}">עריכה</button>
      <button class="btn danger" data-del-user="${esc(u.username)}">מחק</button>
    </td>
  </tr>`).join("");

  document.querySelectorAll("[data-edit-user]").forEach((b) => b.onclick = () => {
    const u = list.find((x) => x.username === b.dataset.editUser);
    const self = u.username === ME.username;
    sheet({
      title: "עריכת משתמש", sub: u.username,
      fields: [
        {
          id: "role", label: self ? "תפקיד (אי אפשר לשנות את שלכם)" : "תפקיד",
          type: "select", value: u.role,
          options: self
            ? [{ value: u.role, label: u.role === "admin" ? "מנהל" : "הפצה בלבד" }]
            : [{ value: "deploy", label: "הפצה בלבד" }, { value: "admin", label: "מנהל" }],
        },
        {
          id: "password", label: "סיסמה חדשה (ריק = בלי שינוי)", type: "password",
          confirm: "אימות הסיסמה החדשה",
        },
        // חסימה אינה מחיקה: היא הפיכה, והיא משאירה את שורות היומן
        // מצביעות על מישהו. המתג מוסתר למשתמש המחובר — חסימה עצמית
        // היא נעילה מיידית מחוץ למסך, כי `auth.check` קורא אותה בכל
        // בקשה, כולל בזו שתשחרר אותה.
        ...(self ? [] : [{
          id: "disabled", label: "חסום — לא יוכל להיכנס, וסשן פתוח נסגר מיד",
          type: "checkbox", value: !!u.disabled,
        }]),
      ],
      onSubmit: async (v) => {
        const body = { role: v.role, password: v.password };
        if (!self) body.disabled = !!v.disabled;
        await put(`/users/${encodeId(u.username)}`, body);
        await loadUsersAdmin();
        toast("נשמר.");
      },
    });
  });

  document.querySelectorAll("[data-del-user]").forEach((b) => b.onclick = () => confirmSheet(
    "מחיקת משתמש", `המשתמש ${b.dataset.delUser} יאבד גישה מיידית.`, "מחק",
    async () => { await del(`/users/${encodeId(b.dataset.delUser)}`); await loadUsersAdmin(); }));
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
  onSubmit: async (v) => { await post("/users", v); await loadUsersAdmin(); },
}); }

function usersAdminPage() { return `<div class="card"><div class="card-h"><button class="btn primary" onclick="openNewUser()">Add user</button></div><div class="card-b table-wrap"><table id="users-table"><thead><tr><th>User</th><th>Role</th><th>Created</th><th>Actions</th></tr></thead><tbody></tbody></table></div></div>`; }

let JOURNAL_EVENTS_LOADED = false;

// המפתחות שהשרת מצפה להם ב-query string, כל אחד מקושר לשדה שלו במסך.
const JOURNAL_FILTER_FIELDS = {
  event: "#jf-event", machine: "#jf-machine", q: "#jf-q",
  from: "#jf-from", to: "#jf-to",
};

async function loadJournalEvents() {
  if (JOURNAL_EVENTS_LOADED) return;
  const select = $("#jf-event");
  const events = await api("/journal/events");
  if (!select || select !== $("#jf-event")) return;
  select.insertAdjacentHTML("beforeend", events.map((e) =>
    `<option value="${esc(e.event)}">${esc(e.label)}</option>`).join(""));
  JOURNAL_EVENTS_LOADED = true;
}

function journalFilterQuery() {
  const params = new URLSearchParams();
  for (const [key, sel] of Object.entries(JOURNAL_FILTER_FIELDS)) {
    let value = $(sel).value.trim();
    if (!value) continue;
    // "עד תאריך" הוא דקה שלמה (datetime-local); בלי שניות, "10:00"
    // כהשוואת מחרוזות היה פוסל אירוע ב-"10:00:15" — לפני הדקה הבאה
    // אבל אחרי המחרוזת עצמה.
    if (key === "to") value += ":59";
    params.set(key, value);
  }
  const qs = params.toString();
  return qs ? "?" + qs : "";
}

async function loadJournal() {
  const host = $("#journal-table tbody");
  if (!host) return;
  await loadJournalEvents();
  if (host !== $("#journal-table tbody")) return;
  // fetch ישיר, לא api(): צריך את כותרת האזהרה על סינון חלקי, לא רק
  // את הגוף (עיקרון 5 — "לא בדקנו הכל" אינו "אין תוצאות").
  const response = await fetch("/api/console/journal" + journalFilterQuery(), { credentials: "same-origin" });
  if (response.status === 401) { showLogin(); throw new Error("לא מחובר"); }
  if (!response.ok) throw new Error("שגיאה " + response.status);
  if (host !== $("#journal-table tbody")) return;
  const truncated = response.headers.get("X-Journal-Search-Truncated") === "true";
  $("#journal-truncated").classList.toggle("hidden", !truncated);
  if (truncated) {
    toast("החיפוש מכסה רק את השורות האחרונות ביומן — נסו לצמצם עם טווח תאריכים");
  }
  const rows = await response.json();
  if (host !== $("#journal-table tbody")) return;
  JOURNAL = rows;
  $("#journal-table tbody").innerHTML = rows.length
    ? rows.map((r) => `<tr>
        <td class="mono" dir="ltr">${r.ts.replace("T", " ").slice(0, 19)}</td>
        <td>${esc(r.user) || "המערכת"}</td><td><b>${esc(r.label)}</b></td>
        <td>${esc(r.text)}</td>
      </tr>`).join("")
    : `<tr><td colspan="4" class="lib-empty">אין רשומות שתואמות לסינון.</td></tr>`;
}


function journalPage() { return `<div class="card"><p id="journal-truncated" class="notice warn hidden">Search covers only recent journal rows. Narrow the date range.</p><div class="pad form-grid" id="journal-filters">
          <div class="row">
            <select id="jf-event" aria-label="סוג אירוע"><option value="">כל סוגי האירועים</option></select>
            <input type="text" id="jf-machine" placeholder="מכונה או MAC" aria-label="סינון לפי מכונה או MAC">
            <input type="text" id="jf-q" placeholder="חיפוש חופשי" aria-label="חיפוש חופשי ביומן">
          </div>
          <div class="row">
            <label class="jf-date">מ-תאריך
              <input type="datetime-local" id="jf-from">
            </label>
            <label class="jf-date">עד תאריך
              <input type="datetime-local" id="jf-to">
            </label>
            <button class="btn" id="jf-clear" type="button">איפוס סינון</button>
          </div>
        </div>
        <table id="journal-table">
          <thead><tr><th>זמן</th><th>מי</th><th>מה קרה</th><th>פירוט</th></tr></thead>
          <tbody></tbody>
        </table></div>`; }

function isAdmin() { return !!ME && ME.role === "admin"; }
function pageAllowed(id) {
  if (!ME) return false;
  if (id === "branches") return isAdmin() && !!ME.capabilities?.interbranch_transfer;
  if (id === "branch") return isAdmin() && !!ME.capabilities?.enroll_secondary;
  return ["home", "images", "deploy"].includes(id) || isAdmin();
}
function wireRestoredPage() {
  if (current === "images") loadCaptures().catch(e => toast(e.message));
  if (current === "health" && currentTab === 1) loadSsh().catch(e => toast(e.message));
  if (current === "settings") loadSettings().catch(e => toast(e.message));
  if (current === "branches") (currentTab === 0 ? loadBranchCards() : loadBranches()).catch(e => toast(e.message));
  if (current === "branch") loadBranchView(BRANCH_VIEWS[currentTab][0]).catch(e => toast(e.message));
  if (current === "permissions" && currentTab === 0) loadUsersAdmin().catch(e => toast(e.message));
  if (current === "logs" && currentTab === 0) {
    JOURNAL_EVENTS_LOADED = false;
    const reload = () => loadJournal().catch(e => toast(e.message));
    for (const [key, sel] of Object.entries(JOURNAL_FILTER_FIELDS)) {
      $(sel).value = journalFilters[key] || "";
      $(sel).onchange = () => { journalFilters[key] = $(sel).value; reload(); };
    }
    $("#jf-clear").onclick = () => {
      journalFilters = {};
      for (const sel of Object.values(JOURNAL_FILTER_FIELDS)) $(sel).value = "";
      reload();
    };
    reload().then(() => { if ($("#jf-event")) $("#jf-event").value = journalFilters.event || ""; });
  }
}
let journalFilters = {};
let pollTimer = null, overviewBusy = false, overviewError = "", overviewLastOk = null;
let CAPTURE_TASKS = [];
let roundDrawerOpen = false;
let drawerGeneration = 0;
function startStatusWatch() {
  clearInterval(pollTimer);
  pollTimer = setInterval(() => {
    if (!ME || document.hidden) return;
    refreshStatus();
    if (isAdmin() || current === "images") loadCaptures().catch(e => toast(e.message));
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
    await loadMonitor();
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
    verify: { label: "להמשך יש להקליד בדיוק:", mustEqual: "imagectl.monitor" },
    onSubmit: () => send({ confirm: "imagectl.monitor" }),
  });
}
async function openRoundDetail() {
  const generation=drawerGeneration;
  const s=OVERVIEW?.session;
  if(!s) { openDrawer("Active transfers", pullsHtml(OVERVIEW?.pulls || [], false) || '<div class="empty">No active session</div>'); roundDrawerOpen=true; return; }
  const machines=s.single ? [] : await sessionClassMachines(s.group_id);
  if(!ME || generation !== drawerGeneration) return;
  const roster=s.roster ? new Set(s.roster) : null;
  const byMac=new Map((s.members || []).map(m=>[m.mac,m]));
  let rows=(s.members || []).map(m=>memberRow(m,s,stuckNote(s.stuck,m.mac))).join("");
  for(const m of machines || []) if((!roster || roster.has(m.mac)) && !byMac.has(m.mac)) rows+='<div class="member"><b>'+esc(s.prefix+"-"+m.suffix)+'</b><div class="sub">'+esc(stuckNote(s.stuck,m.mac)||"Not joined yet")+'</div></div>';
  const next=(machines || []).filter(m=>roster && !roster.has(m.mac)).map(m=>m.suffix);
  const note=machines===null ? '<div class="notice warn">Class roster could not be read; machines may be missing.</div>' : '';
  openDrawer(sessionImage(s), '<div class="notice">'+esc(stateLabel(s.state))+' | '+(s.joined || 0)+' / '+sessionExpected(s)+(s.state==='open' && s.starts_in_seconds!=null ? ' | Starts in '+s.starts_in_seconds+'s' : '')+'</div>'+note+rows+(next.length ? '<div class="notice">Next round: '+esc(next.join(', '))+'</div>' : '')+pullsHtml(OVERVIEW?.pulls || [],true));
  roundDrawerOpen=true;
}

const UI_ICON_PATHS = {
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
  "moon": "<path d=\"M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11Z\"/>"
};
function uiIcon(name) { return '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">'+(UI_ICON_PATHS[name] || UI_ICON_PATHS.list)+'</svg>'; }

function encodeId(value) { return encodeURIComponent(value).replaceAll("'", "%27"); }
