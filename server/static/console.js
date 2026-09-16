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
    [MACHINES, GROUPS, DISK_FAILURES] = await Promise.all([
      api("/machines"), api("/groups"), api("/disk-failures")]);
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
      return `<div class="inventory-node" role="treeitem" tabindex="0"><span class="tree-arrow-sp"></span><span>${uiIcon("image")}</span><span>${esc(f.name)}</span></div>`;
    }
    const rows = imgs.map((im) => {
      const idEnc = encodeId(im.id || "");
      return `<div class="inventory-node" role="treeitem" tabindex="0" onclick="openImageDetail('${idEnc}')"><span class="tree-arrow-sp"></span><span>${uiIcon("image")}</span><span>${esc(im.name)}</span></div>`;
    }).join("");
    return `<div class="inventory-node" role="treeitem" tabindex="0" aria-expanded="${open}" ondblclick="toggleInventoryGroup(this,'${fid}')"><span class="tree-arrow" data-open="${open}" onclick="event.stopPropagation();toggleInventoryGroup(this.closest('.inventory-node'),'${fid}')">${open ? "▾" : "▸"}</span><span>${uiIcon("image")}</span><span>${esc(f.name)}</span></div><div id="${fid}" class="inventory-children"${open ? "" : " hidden"}>${rows}</div>`;
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

function selectMachinesGroup(groupId) {
  try { groupId = decodeURIComponent(groupId); } catch (e) {}
  MACHINES_FILTER = groupId;
  selectPageById("machines");
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
  document.getElementById("content").innerHTML = layout(page, currentTab);
  const search = document.getElementById("globalSearch");
  if (search) search.value = searchQuery || "";
  if (current === "nic" || current === "netdeploy") wireNetPage();
  wireRestoredPage();
}

function home() {
  if (!OVERVIEW) return overviewError ? `<div class="empty">Status unavailable</div>` : pagePlaceholder();
  const serverCheck = healthCheckById("server");
  const serverHealth = serverCheck ? healthStatusLabel(serverCheck.state) : "Not checked";
  const images = OVERVIEW.images || 0;
  const machines = OVERVIEW.machines || 0;
  const session = OVERVIEW.session;
  const active = session ? 1 : 0;
  const joined = session ? (session.joined || 0) : 0;
  const expected = sessionExpected(session);
  const pct = sessionProgressPct(session);
  const storage = OVERVIEW.storage;
  const roundSub = session ? `${joined} מחוברים עכשיו` : "אין סבב פעיל";
  const roundTag = session
    ? `<span class="tag green">${esc(stateLabel(session.state))}</span>`
    : `<span class="tag">אין סבב</span>`;

  let capacity = `<div class="empty">אין נתוני אחסון</div>`;
  let freeLabel = "";
  if (storage && storage.free_bytes != null && Number(storage.total_bytes) > 0) {
    const total = storage.total_bytes;
    const used = Math.max(0, total - (storage.free_bytes || 0));
    const usedPct = Math.round(100 * used / total);
    freeLabel = formatGB(storage.free_bytes) + " פנוי";
    capacity = `<div class="metric"><div><strong>אחסון אימג׳ים</strong><span>${esc(formatGB(used))} מתוך ${esc(formatGB(total))}</span><div class="progress" style="margin-top:7px"><i style="width:${usedPct}%"></i></div></div><div class="metric-side">${usedPct}%</div></div>`;
  }

  let roundBody;
  if (!session) {
    roundBody = `<div class="empty">אין סבב פעיל</div>`;
  } else {
    const members = session.members || [];
    const done = members.filter((m) => m.done || m.state === "done").length;
    const failed = members.filter((m) => m.state === "failed").length;
    const working = members.length - done - failed;
    const bar = pct == null ? "" : `<div class="progress" style="margin-top:7px"><i style="width:${pct}%"></i></div>`;
    const side = pct == null ? "—" : (pct + "%");
    const list = `<div class="list">${memberRows(members)}</div>${pullsHtml(OVERVIEW.pulls || [], false)}`;
    roundBody = `<div class="split"><div><div class="metric"><div><strong>${esc(sessionImage(session))}</strong><span>${joined} תחנות מחוברות מתוך ${expected}</span>${bar}</div><div class="metric-side">${side}</div></div><div class="statrow"><div class="statbox"><div class="n">${done}</div><div class="l">הושלמו</div></div><div class="statbox"><div class="n">${working}</div><div class="l">בתהליך</div></div><div class="statbox"><div class="n">${failed}</div><div class="l">נכשלו</div></div></div></div><div>${list}</div></div>`;
  }

  return `<div class="object-strip"><div class="object-id"><div class="object-icon">${uiIcon("server")}</div><div><strong>שרת אימג'ים</strong><small>אובייקט שרת · מחובר</small></div></div><div class="object-actions"><button class="btn" onclick="refreshPage()">רענן</button></div></div><div class="grid"><div class="span-12"><div class="kpis"><div class="kpi"><div class="label">אימג׳ים זמינים</div><div class="val">${images}</div></div><div class="kpi"><div class="label">מחשבים רשומים</div><div class="val">${machines}</div></div><div class="kpi"><div class="label">סבב פעיל</div><div class="val">${active}</div><div class="sub">${esc(roundSub)}</div></div><div class="kpi"><div class="label">בריאות שרת</div><div class="val">${esc(serverHealth)}</div></div></div></div>
<div class="span-8"><div class="card"><div class="card-h"><span>סבב הפצה פעיל</span><button class="btn" onclick="openRoundDetail()">Details</button>${roundTag}</div><div class="card-b">${roundBody}</div></div></div>
<div class="span-4"><div class="card"><div class="card-h"><span>קיבולת שרת</span><small>${esc(freeLabel)}</small></div><div class="card-b">${capacity}</div></div></div>
<div class="span-12"><div class="card"><div class="card-b"><div class="empty">אין נתונים להצגה</div></div></div></div></div>`;
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

function images() {
  if (!IMAGES) return pagePlaceholder();
  const admin = ME && ME.role === "admin";
  const list = imagesVisible();
  const title = IMAGES_FOLDER ? esc(IMAGES_FOLDER) : "כל האימג׳ים";
  const uploadBtn = admin
    ? `<button class="btn" onclick="openImageIngest()">העלאה</button>`
    : "";

  let tableBody;
  if (!IMAGES.length) {
    tableBody = `<div class="empty">אין אימג'ים בספרייה עדיין</div>`;
  } else if (!list.length) {
    tableBody = `<div class="empty">אין אימג'ים בתיקייה הזו</div>`;
  } else {
    const rows = list.map((r) => {
      const idEnc = encodeId(r.id);
      const size = `דיסק יעד: ${librarySize(r.source_disk_bytes)} · אחסון בשרת: ${librarySize(r.total_compressed_bytes)}`;
      const version = r.version || "—";
      const sha = r.sha || "—";
      const updated = r.updated || (r.created || "").slice(0, 10) || "—";
      const status = r.status || "—";
      const stClass = status === "מאומת" ? "ok" : (status === "ישן" ? "warn" : "");
      return `<tr class="clickable" onclick="openImageDetail('${idEnc}')"><td><div class="namecell">${uiIcon("image")} <strong>${esc(r.name)}</strong></div></td><td>${esc(imageOsLabel(r.os))}</td><td>${esc(size)}</td><td>${esc(version)}</td><td>${esc(sha)}</td><td>${esc(updated)}</td><td><span class="status ${stClass}"><i></i>${esc(status)}</span></td><td><button class="tool-btn" onclick="event.stopPropagation();openImageDetail('${idEnc}')">פרטים</button></td></tr>`;
    }).join("");
    tableBody = `<table class="table"><thead><tr><th>שם</th><th>מערכת</th><th>גדלים</th><th>גרסה</th><th>SHA-256</th><th>עודכן</th><th>סטטוס</th><th>פעולות</th></tr></thead><tbody>${rows}</tbody></table>`;
  }

  const folderRows = [
    `<div class="list-row clickable" onclick="filterImagesFolder('')"><div class="list-main"><strong>הכל</strong></div><div class="list-side">${IMAGES.length}${IMAGES_FOLDER === null ? " · נבחר" : ""}</div></div>`,
  ].concat((FOLDERS || []).map((f) => {
    const sel = IMAGES_FOLDER === f.name;
    return `<div class="list-row clickable" onclick="filterImagesFolder('${encodeId(f.name)}')"><div class="list-main"><strong>${esc(f.name)}</strong><small>${esc(f.description || "")}</small></div><div class="list-side">${f.images}${sel ? " · נבחר" : ""}${admin ? `<button class="tool-btn" onclick="event.stopPropagation();editFolderSheet(FOLDERS.find(f=>f.name===decodeURIComponent('${encodeId(f.name)}')))">Edit</button><button class="tool-btn" onclick="event.stopPropagation();reorderFolder('${encodeId(f.name)}',-1)" aria-label="Move folder up">Up</button><button class="tool-btn" onclick="event.stopPropagation();reorderFolder('${encodeId(f.name)}',1)" aria-label="Move folder down">Down</button>` : ""}</div></div>`;
  })).join("");

  const ingestRow = admin
    ? `<div class="list-row clickable" onclick="openImageIngest()"><div class="list-main"><strong>ייבוא אימג׳</strong><small>העלה קובץ tar עם manifest</small></div><div class="flow-arrow" aria-hidden="true">&rarr;</div></div>`
    : `<div class="list-row"><div class="list-main"><strong>ייבוא אימג׳</strong><small>העלה קובץ tar עם manifest</small></div><div class="flow-arrow" aria-hidden="true">&rarr;</div></div>`;

  return `<div class="grid"><div class="span-12"><div class="card"><div class="card-h"><span>${title}</span><div>${uploadBtn} <button class="btn primary" ${admin ? 'onclick="openCapture().catch(e => toast(e.message))"' : "disabled"}>+ אימג׳ חדש</button></div></div><div id="capture-bar"></div><div class="card-b table-wrap">${tableBody}</div></div></div>
<div class="span-4"><div class="card"><div class="card-h">תיקיות${admin ? `<button class="btn" onclick="addFolderSheet()">Add folder</button>` : ""}</div><div class="card-b"><div class="list">${folderRows}</div></div></div><div class="card"><div class="card-h">פעולות מהירות</div><div class="card-b list">${ingestRow}<div class="list-row"><div class="list-main"><strong>ייצוא אימג׳</strong><small>הורדה מפרטי האימג׳</small></div><div class="flow-arrow" aria-hidden="true">&rarr;</div></div><div class="list-row" title="בקרוב"><div class="list-main"><strong>אימות ספרייה</strong><small>בדוק checksum לכל האימג׳ים</small></div><div class="flow-arrow" aria-hidden="true">&rarr;</div></div></div></div></div>
<div class="span-8"><div class="card"><div class="card-h">כללי בטיחות</div><div class="card-b"><div class="statrow"><div class="statbox"><div class="n">0</div><div class="l">אימג׳ים ללא manifest</div></div><div class="statbox"><div class="n">—</div><div class="l">checksum שגוי</div></div><div class="statbox"><div class="n">—</div><div class="l">גרסאות ישנות</div></div></div><div class="footer-note">אי־אפשר להתחיל סבב עם אימג׳ שלא עבר אימות חיובי.</div></div></div></div></div>`;
}

function filterImagesFolder(name) {
  IMAGES_FOLDER = name ? decodeURIComponent(name) : null;
  if (current === "images") renderCurrent();
}

function openImageDetail(id) {
  const img = findImage(id);
  if (!img) { toast("אימג׳ לא נמצא"); return; }
  const admin = ME && ME.role === "admin";
  const idEnc = encodeId(img.id);
  const targetSize = librarySize(img.source_disk_bytes);
  const serverSize = librarySize(img.total_compressed_bytes);
  const version = img.version || "—";
  const status = img.status || "—";
  const sha = img.sha || "—";
  const updated = img.updated || (img.created || "").slice(0, 10) || "—";
  const folder = img.folder || "—";
  const desc = img.description || "בלי תיאור.";
  const adminBtns = admin
    ? `<button class="btn" onclick="renameImage('${idEnc}')">שינוי שם</button><button class="btn" onclick="moveImage('${idEnc}')">העברה לתיקייה</button><button class="btn" onclick="editImageDescription('${idEnc}')">Description</button><button class="btn" onclick="reorderImage('${idEnc}',-1)">Up</button><button class="btn" onclick="reorderImage('${idEnc}',1)">Down</button><button class="btn danger" onclick="deleteImage('${idEnc}')">מחיקה</button>`
    : "";
  openDrawer("פרטי אימג׳ — " + img.name, `<div class="detail-grid"><div class="detail-box"><span class="k">מערכת</span><span class="v">${esc(imageOsLabel(img.os))}</span></div><div class="detail-box"><span class="k">דיסק יעד</span><span class="v">${esc(targetSize)}</span></div><div class="detail-box"><span class="k">אחסון בשרת</span><span class="v">${esc(serverSize)}</span></div><div class="detail-box"><span class="k">גרסה</span><span class="v">${esc(version)}</span></div><div class="detail-box"><span class="k">סטטוס</span><span class="v">${esc(status)}</span></div><div class="detail-box"><span class="k">SHA-256</span><span class="v">${esc(sha)}</span></div><div class="detail-box"><span class="k">עודכן</span><span class="v">${esc(updated)}</span></div><div class="detail-box"><span class="k">תיקייה</span><span class="v">${esc(folder)}</span></div><div class="detail-box"><span class="k">מחיצות</span><span class="v">${esc(img.partitions)}</span></div><div class="detail-box"><span class="k">Disk family</span><span class="v">${img.family == null ? "-" : esc(img.family) + " GB"}</span></div></div><div class="section-title">תיאור</div><div class="notice">${esc(desc)}</div><div class="section-title">פעולות</div><div class="action-strip"><a class="btn" href="/api/console/images/${idEnc}/download">הורדה</a><button class="btn" disabled title="בקרוב">אמת</button>${adminBtns}</div>`);
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

function machines() {
  if (!MACHINES) return pagePlaceholder();
  const classCount = (GROUPS || []).filter((g) => g.role === "classroom").length;
  const list = MACHINES_FILTER
    ? MACHINES.filter((m) => machineGroupId(m) === MACHINES_FILTER)
    : MACHINES;
  // ‏#916: הסינון (לחיצה על קבוצה בעץ) נשאר בין מעברים — בלי חיווי
  // הוא נראה כמו "אין מחשבים רשומים" מול עץ מאויש. הסינון תמיד גלוי
  // וניתן לביטול, והריק מבחין בין "אין בכלל" ל"אין בקבוצה הזו".
  const filterStrip = MACHINES_FILTER
    ? `<div class="notice" role="status">מוצגת קבוצה: <strong>${esc(groupLabel(MACHINES_FILTER))}</strong> <button class="btn" onclick="clearMachinesFilter()">הצג את כל המחשבים</button></div>`
    : "";
  let tableBody;
  if (!list.length) {
    tableBody = MACHINES_FILTER
      ? `<div class="empty">אין מחשבים בקבוצה ${esc(groupLabel(MACHINES_FILTER))} (${MACHINES.length} רשומים בסך הכול)</div>`
      : `<div class="empty">אין מחשבים רשומים</div>`;
  } else {
    const rows = list.map((m) => {
      const macEnc = encodeId(m.mac);
      const name = machineName(m) || m.mac;
      const klass = groupLabel(machineGroupId(m));
      return `<tr class="clickable" onclick="openMachineDetail('${macEnc}')"><td><strong>${esc(name)}</strong></td><td>${esc(klass)}</td><td>${esc(m.mac)}</td><td>${waitingHtml(m)}</td><td><button class="tool-btn" onclick="event.stopPropagation();openMachineDetail('${macEnc}')">פרטים</button></td></tr>`;
    }).join("");
    tableBody = `<table class="table"><thead><tr><th>שם</th><th>כיתה</th><th>MAC</th><th>סטטוס</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
  }
  return `<div class="grid"><div class="span-12"><div class="card"><div class="card-h"><span>מלאי תחנות</span><div><button class="btn" onclick="importCSV()">ייבוא CSV</button> <button class="btn" onclick="exportMachines()">ייצוא CSV</button> <button class="btn primary" onclick="openNewMachine()">+ מחשב</button></div></div><div class="card-b table-wrap">${filterStrip}${tableBody}</div></div></div>
${diskFailuresCard()}
<div class="span-12"><div class="card"><div class="card-h">כיתות</div><div class="card-b"><div class="statrow"><div class="statbox"><div class="n">${classCount}</div><div class="l">כיתות</div></div><div class="statbox"><div class="n">${list.length}</div><div class="l">תחנות</div></div></div></div></div>
</div></div>`;
}

function clearMachinesFilter() {
  MACHINES_FILTER = null;
  if (current === "machines") renderCurrent();
}

/* #874: דיסקים אדומים — זיכרון כשלי הכתיבה בשרת, במקום הסימון על הדיסק
   (#845). הסיווג הוא של השרת (ata_cause.py); "נקה" = הדיסק הוחלף / הכבל
   תוקן, והרשומה מפסיקה לצבוע. אדום לפי סידורי **וגם** לפי מכונה+חריץ. */
const FAILURE_CAUSE_HE = { cable: "כבל/חריץ", disk: "הדיסק", unclassified: "לא סווג" };

function failureCauseText(f) {
  const cause = FAILURE_CAUSE_HE[f.cause] || esc(f.cause);
  if (f.cause === "cable" && f.port != null) return `${cause} SATA ${f.port - 1}`;
  return cause;
}

function diskFailuresCard() {
  const rows = (DISK_FAILURES || []).map((f) => {
    const m = findMachine(f.mac);
    const where = (m ? machineName(m) : f.mac) + (f.port != null ? ` · דיסק ${f.port}` : "");
    const log = f.ata_log || [];
    const evidence = log.length
      ? `<details><summary>${log.length} שורות קרנל</summary><pre class="ata-log">${esc(log.join("\n"))}</pre></details>` : "";
    return `<tr><td><strong>${esc(f.serial || "—")}</strong></td><td>${esc(where)}</td><td>${esc(fmtWhen(f.at))}</td><td><span class="disk-smart failed_last">${failureCauseText(f)}</span>${evidence}</td><td>${esc(f.error || "")}</td><td><button class="tool-btn" onclick="clearDiskFailure(${Number(f.id)})">נקה</button></td></tr>`;
  }).join("");
  const body = DISK_FAILURES == null
    ? `<div class="empty">רשימת הכשלים לא נטענה</div>`
    : (rows ? `<table class="table"><thead><tr><th>מספר סידורי</th><th>מכונה · חריץ</th><th>מתי</th><th>סיבה</th><th>שגיאה</th><th></th></tr></thead><tbody>${rows}</tbody></table>`
            : `<div class="empty">אין דיסקים אדומים</div>`);
  return `<div class="span-12"><div class="card"><div class="card-h"><span>דיסקים אדומים — נכשלו בכתיבה</span><small>אדום במסך המחשב לפני הסבב; "נקה" אחרי החלפת דיסק או כבל</small></div><div class="card-b table-wrap">${body}</div></div></div>`;
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
  home: { crumb: "סקירה כללית", title: "סקירה כללית", desc: "מצב שרת, ספריית האימג׳ים ופעילות ההפצה בזמן אמת", tabs: ["סיכום", "משימות אחרונות", "אירועים"], render: home, load: refreshStatus },
  images: { crumb: "ספריית אימג׳ים", title: "ספריית אימג׳ים", desc: "ניהול גרסאות, העלאה, הורדה ושמירה של אימג׳ים מוכנים להפצה", tabs: ["אימג׳ים", "מטא־נתונים"], render: images, load: loadImages },
  deploy: { crumb: "סבבי הפצה", title: "סבבי הפצה", desc: "פתיחת סבב, צירוף תחנות ומעקב אחר כתיבה לכל מחשב", tabs: ["סבבים", "הצטרפות חיה"], render: deploy, load: refreshStatus },
  machines: { crumb: "מחשבים", title: "מחשבים", desc: "מלאי תחנות, כיתות, כתובות MAC וסטטוס PXE", tabs: ["כל התחנות", "ניהול לפי סוג"], render: machines, load: loadMachines },
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
    home: [home, () => `<div class="card card-b">${pullsHtml(OVERVIEW?.pulls || [], false) || "No active pulls"}</div>`, emptyDataCard],
    images: [images, emptyDataCard],
    deploy: [deploy, emptyDataCard],
    machines: [machines, machineAdminPage],
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
    home: [["בדיקת בריאות", "soon()"], ["פתח סבב חדש", "soon()"], ["פתח התראות", "topNotifications()"]],
    images: [["אימג׳ חדש", "openCapture().catch(e => toast(e.message))"], ["קליטת אימג׳", "openImageIngest()"], ["אימות ספרייה", "soon()"]],
    deploy: [["סבב הפצה חדש", "soon()"], ["התחל סבב", "startSelectedRound()"], ["ייצוא סבבים", "soon()"]],
    machines: [["הוספת מחשב", "openNewMachine()"], ["ייבוא CSV", "importCSV()"], ["ייצוא CSV", "exportMachines()"]],
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

/* #417: "מה ראינו בפעם האחרונה שהמכונה דיברה" — מלאי הכוננים מה-hello
   האחרון, כפי שנשמר ב-net_devices. ‏`disks` הוא null/[]/רשימה משלושה
   מצבים שונים לגמרי (עיקרון 5), ואסור לקפל אותם לתצוגה אחת: מעולם לא
   דיווחה, דיווחה בפועל אפס כוננים (ממצא!), ומה שיש בה. */
function diskInventoryHtml(m) {
  const when = m.disks_reported_at ? ` (${ago(m.disks_reported_at)})` : "";
  const title = `<div class="section-title">מלאי כוננים אחרון${when}</div>`;
  if (m.disks == null) {
    return `${title}<div class="notice">המכונה מעולם לא דיווחה על כוננים.</div>`;
  }
  if (!m.disks.length) {
    return `${title}<div class="notice warn">המכונה דיווחה — ואין בה אף כונן.</div>`;
  }
  const rows = m.disks.map((d) => `<div class="detail-box">
      <span class="k">${esc(d.dev || "—")}</span>
      <span class="v">${fmtBytes(d.size_bytes)}${d.model ? " · " + esc(d.model) : ""}${d.serial ? " · " + esc(d.serial) : ""}</span>
    </div>`).join("");
  return `${title}<div class="detail-grid">${rows}</div>`;
}

/* #720: המלאי החומרתי מה-hello (schema 2) — דגם, בקרי רשת/אחסון (PCI), TPM.
   null = מעולם לא דיווחה (סוכן ישן / schema 1) — מצב משלו, לא "אין חומרה". */
function hwInventoryHtml(m) {
  const title = `<div class="section-title">חומרה (למיפוי דרייברים)${m.inventory_seen_at ? ` (${ago(m.inventory_seen_at)})` : ""}</div>`;
  const inv = m.inventory;
  if (inv == null) return `${title}<div class="notice">המכונה מעולם לא דיווחה על חומרה (סוכן ישן).</div>`;
  const dmi = inv.dmi || {};
  const model = [dmi.sys_vendor, dmi.product_name].filter(Boolean).join(" ") || "—";
  const version = dmi.product_version ? ` · ${esc(dmi.product_version)}` : "";
  const tpm = inv.tpm == null ? "לא נבדק" : inv.tpm.present ? `יש${inv.tpm.version ? " " + esc(inv.tpm.version) : ""}` : "אין";
  const pci = (inv.pci || []).map((p) => `<span class="mono" dir="ltr">${esc(p)}</span>`).join(", ") || "—";
  return `${title}<div class="detail-grid">
    <div class="detail-box"><span class="k">דגם</span><span class="v">${esc(model)}${version}</span></div>
    <div class="detail-box"><span class="k">לוח</span><span class="v">${esc(dmi.board_name || "—")}</span></div>
    <div class="detail-box"><span class="k">TPM</span><span class="v">${tpm}</span></div>
    <div class="detail-box"><span class="k">PCI רשת/אחסון</span><span class="v">${pci}</span></div>
  </div>`;
}

/* #927: אותה אזהרת shrink-restore שב-captureWarningHtml, הפעם ליד
   המחשב עצמו — זה מי שהדיסק שלו נשאר מכווץ. המשימה האחרונה של ה-MAC
   הזה בלבד (CAPTURE_TASKS ממוין created_at DESC מהשרת). */
function machineCaptureWarningHtml(mac) {
  const t = (CAPTURE_TASKS || []).find((x) => x.mac === mac && x.state === "done" && x.error);
  if (!t) return "";
  return `<div class="section-title">אזהרת קליטה אחרונה</div><div class="notice warn">${esc(t.error)}</div>`;
}

function openMachineDetail(mac) {
  if (!isAdmin()) return;
  const m = findMachine(mac);
  if (!m) { toast("מחשב לא נמצא"); return; }
  const macEnc = encodeId(m.mac);
  const name = machineName(m) || m.mac;
  const klass = groupLabel(machineGroupId(m));
  openDrawer("תחנה — " + name, `<div class="detail-grid"><div class="detail-box"><span class="k">שם</span><span class="v">${esc(name)}</span></div><div class="detail-box"><span class="k">כיתה</span><span class="v">${esc(klass)}</span></div><div class="detail-box"><span class="k">MAC</span><span class="v">${esc(m.mac)}</span></div></div>${diskInventoryHtml(m)}${hwInventoryHtml(m)}${machineCaptureWarningHtml(m.mac)}<div class="section-title">פעולות</div><div class="action-strip"><button class="btn" onclick="renameMachine('${macEnc}')">שינוי שם</button><button class="btn" disabled title="דורש endpoint — בקרוב">עריכת MAC</button><button class="btn" disabled title="בקרוב">Wake-on-LAN</button><button class="btn" disabled title="בקרוב">בדוק PXE</button><button class="btn danger" disabled title="בקרוב">אתחול</button></div>`);
}

function renameMachine(mac) {
  if (!isAdmin()) return;
  const m = findMachine(mac);
  if (!m) return;
  openModalContent(
    "שינוי שם תחנה",
    `<div class="form"><div class="field full"><label>שם</label><input id="machineNameInput" autocomplete="off"></div></div>`,
    "שמור",
    () => applyMachineRename(m.mac)
  );
  const input = document.getElementById("machineNameInput");
  if (input) { input.value = machineName(m); input.focus(); }
}

async function applyMachineRename(mac) {
  const input = document.getElementById("machineNameInput");
  const name = input ? input.value : "";
  if (!name.trim()) { toast("שם לא יכול להיות ריק"); return; }
  try {
    await put("/machines/" + encodeId(mac), { name });
    closeModal();
    toast("נשמר.");
    await loadMachines();
    openMachineDetail(mac);
  } catch (e) {
    toast(e.message);
  }
}

function openNewMachine() {
  if (!isAdmin()) return;
  openModalContent(
    "הוספת מחשב",
    `<div class="form"><div class="field"><label>שם</label><input id="newMachineName" autocomplete="off"></div><div class="field"><label>כיתה</label><select id="newMachineClass">${groupSelectOptions()}</select></div><div class="field full"><label>MAC</label><input id="newMachineMac" dir="ltr" placeholder="b4:2e:99:07:1a:c4" autocomplete="off"></div></div>`,
    "הוסף מחשב",
    createMachine
  );
}

async function createMachine() {
  const name = (document.getElementById("newMachineName") || {}).value || "";
  const group_id = (document.getElementById("newMachineClass") || {}).value || "";
  const mac = (document.getElementById("newMachineMac") || {}).value || "";
  if (!name.trim() || !mac.trim() || !group_id) {
    toast("נדרשים שם, כיתה ו-MAC");
    return;
  }
  try {
    const r = await post("/machines", { mac, name, group_id });
    closeModal();
    dhcpNotice(r) || toast("המחשב נוסף");
    await loadMachines();
  } catch (e) {
    toast(e.message);
  }
}

function importCSV() {
  if (!isAdmin()) return;
  openModalContent(
    "ייבוא מחשבים",
    `<div class="form"><div class="field full"><label>קבוצה</label><select id="importGroup">${groupSelectOptions()}</select></div><div class="field full"><label>הדבק שורות</label><textarea id="importText" rows="8" dir="ltr" placeholder="b4:2e:99:07:1a:c4 01&#10;B4-2E-99-07-1A-C5 02"></textarea></div></div>`,
    "ייבוא",
    applyMachineImport
  );
}

async function applyMachineImport() {
  const group_id = (document.getElementById("importGroup") || {}).value || "";
  const text = (document.getElementById("importText") || {}).value || "";
  if (!group_id) { toast("בחר קבוצה"); return; }
  try {
    const r = await post("/machines/import", { group_id, text });
    closeModal();
    dhcpNotice(r) || toast(`נשמרו ${r.saved}. נדחו ${(r.rejected || []).length}.`);
    await loadMachines();
  } catch (e) {
    toast(e.message);
  }
}

function exportMachines() {
  window.location = "/api/console/machines.csv";
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

function memberRow(m, session, note = "") {
  const progress = Progress.view(m);
  const cls = m.done || m.state === "done" ? "done" : m.state === "failed" ? "failed" : "";
  const err = m.error ? `<div class="err">${esc(m.error)}</div>` : "";
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
  return `<div class="notice warn"><b>${esc(t.name)}</b>: ${esc(t.error)}</div>`;
}

async function loadCaptures() {
  // ‏CAPTURE_TASKS מחזיק את כל 20 המשימות האחרונות (לא רק הפעילות) —
  // openMachineDetail ו-renderActivity מסננים כל אחד לפי מה שהוא צריך.
  const all = await api("/tasks");
  CAPTURE_TASKS = all;
  renderActivity();
  const bar = $("#capture-bar");
  if (!bar) return;
  const tasks = all.filter((t) => t.state === "pending" || t.state === "running");
  const warned = all.filter((t) => t.state === "done" && t.error);
  if (!tasks.length && !warned.length) { bar.innerHTML = ""; return; }
  bar.innerHTML = tasks.map((t) => {
    const waiting = t.state === "pending";
    return `<div class="upload">
      <div class="upload-line">
        <b>קולט: ${esc(t.name)}</b>
        <span>${waiting ? "ממתין שמחשב הבנייה יעלה ב-PXE" : fmtBytes(t.bytes_written) + " נקראו"}</span>
      </div>
      ${Progress.bar(t)}<span class="sub">${Progress.view(t).label}</span>
      <div class="row" style="margin-top:8px">
        <button class="btn danger" data-cancel-task="${esc(t.id)}">ביטול</button>
      </div>
    </div>`;
  }).join("") + warned.map(captureWarningHtml).join("");
  document.querySelectorAll("[data-cancel-task]").forEach((b) => b.onclick = () => confirmSheet(
    "ביטול הקליטה", "המשימה תבוטל והקבצים שהתקבלו יימחקו.", "בטל את הקליטה",
    async () => { await post(`/tasks/${b.dataset.cancelTask}/cancel`); await loadCaptures(); }));
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

function machineAdminPage() { return `<div class="card"><nav id="subtabs">
          <button data-role="classroom" class="on">מחשבי כיתות</button>
          <button data-role="cloner">מחשבי שיכפול</button>
          <button data-role="build">מחשב בניית אימג'ים</button>
          <button data-role="seen">נראו ברשת</button>
        </nav>
        <div id="role-body"></div></div>`; }

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
  if (isAdmin() && current === "images") loadCaptures().catch(e => toast(e.message));
  if (current === "machines" && currentTab > 0) window.loadMachinesTab().catch(e => toast(e.message));
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
    if (isAdmin()) loadCaptures().catch(e => toast(e.message));
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
  sheet({title:"Image description", fields:[{id:"description",label:"Description",type:"textarea",value:img.description}], onSubmit:async v => {
    await put("/images/"+encodeId(img.id), v); await loadImages(); openImageDetail(img.id);
  }});
}
async function reorderImage(id, step) {
  const img=findImage(id); if(!img || !isAdmin()) return;
  const list=(IMAGES || []).filter(m => m.folder === img.folder);
  const i=list.indexOf(img), j=i+step; if(j<0 || j>=list.length) return;
  [list[i],list[j]]=[list[j],list[i]];
  try { for(let n=0;n<list.length;n++) await put("/images/"+encodeId(list[n].id),{sort:n+1}); await loadImages(); }
  catch(e) { toast(e.message); }
}
async function reorderFolder(name, step) {
  if (!isAdmin()) return;
  const names=(FOLDERS || []).map(f=>f.name), i=names.indexOf(decodeURIComponent(name)), j=i+step;
  if(i<0 || j<0 || j>=names.length) return;
  [names[i],names[j]]=[names[j],names[i]];
  try { await post("/folders/order",{names}); await loadImages(); } catch(e) { toast(e.message); }
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
