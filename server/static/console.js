/* ImageCtl — מעטפת הקונסולה: אימות, טאבים, מדדים, סבב (קריאה בלבד),
   משתמשים, יומן, הגדרות. הספרייה ב-library.js, המחשבים ב-machines.js. */
"use strict";

const $ = (sel) => document.querySelector(sel);
let ME = null;
let pollTimer = null;

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
    throw new Error(detail);
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

/* ---------- מודאל אחיד — במקום prompt/confirm (שחסומים בחלק מהדפדפנים) --- */

function sheet({ title, sub = "", fields = [], submitLabel = "שמירה",
                 danger = false, verify = null, note = "", onSubmit }) {
  const modal = $("#modal");
  const form = $("#sheet");
  /* שדה סיסמה מקבל תמיד מתג הצגה, ואם התבקש — גם שדה אימות. סיסמה
     שמוקלדת בעיוורון פעם אחת היא סיסמה שנועלת מישהו בחוץ. */
  const passwordBox = (id, placeholder = "") => `
    <div class="pw-wrap">
      <input type="password" id="${id}" placeholder="${esc(placeholder)}">
      <button type="button" class="pw-eye" data-pw="${id}">הצג</button>
    </div>`;

  const fieldHtml = (f) => {
    if (f.type === "select") {
      return `<label>${esc(f.label)}
        <select id="sf-${f.id}">${f.options.map((o) =>
          `<option value="${esc(o.value)}" ${o.value === f.value ? "selected" : ""}>${esc(o.label)}</option>`).join("")}</select></label>`;
    }
    if (f.type === "checkbox") {
      return `<label class="check"><input type="checkbox" id="sf-${f.id}" ${f.value ? "checked" : ""}>
        ${esc(f.label)}</label>`;
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
  const inputs = fields.map(fieldHtml).join("");
  const verifyHtml = verify ? `
    <label>${esc(verify.label)}
      <input type="text" id="sf-verify" placeholder="${esc(verify.mustEqual)}"></label>` : "";
  form.innerHTML = `
    <div class="shead"><h3>${esc(title)}</h3>${sub ? `<p>${esc(sub)}</p>` : ""}</div>
    <div class="sbody">${note}${inputs}${verifyHtml}<p class="error" id="sf-error"></p></div>
    <div class="sfoot">
      <button class="btn ${danger ? "danger" : "primary"}" type="submit">${esc(submitLabel)}</button>
      <button class="btn" type="button" id="sf-cancel">ביטול</button>
    </div>`;
  modal.classList.add("show");
  const first = form.querySelector("input, textarea, select");
  if (first) first.focus();
  const close = () => {
    modal.classList.remove("show");
    form.onsubmit = null;
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (event) => { if (event.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  $("#sf-cancel").onclick = close;
  modal.onclick = (event) => { if (event.target === modal) close(); };

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
      const el = $("#sf-" + f.id);
      values[f.id] = f.type === "checkbox" ? el.checked : el.value;
    });
    try { await onSubmit(values); close(); }
    catch (error) { $("#sf-error").textContent = error.message; }
  };
}

function confirmSheet(title, sub, submitLabel, onConfirm) {
  sheet({ title, sub, submitLabel, danger: true, onSubmit: onConfirm });
}

let toastTimer = null;
function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 2600);
}

function fmtBytes(n) {
  if (!n) return "0";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return n.toFixed(n >= 100 ? 0 : 1) + " " + units[i];
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
  button.textContent = dark ? "☀" : "☾";
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
    marks.forEach((m) => { m.innerHTML = '<span class="mark"></span>'; });
    return false;
  }
  const url = "/api/console/branding/logo?t=" + Date.now();
  marks.forEach((m) => { m.innerHTML = `<img src="${url}" alt="לוגו">`; });
  return true;
}

async function loadLogoSettings() {
  const exists = await loadLogo();
  const preview = $("#logo-preview");
  preview.innerHTML = exists
    ? `<img src="/api/console/branding/logo?t=${Date.now()}" alt="לוגו">`
    : `<span class="mark"></span>`;
  $("#logo-clear").classList.toggle("hidden", !exists);
}

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
  idleTimer = setInterval(async () => {
    const idle = (Date.now() - lastActivity) / 1000;
    const left = ME.idle_seconds - idle;
    if (left <= 0) {
      clearInterval(idleTimer);
      try { await post("/logout"); } catch (error) {}
      ME = null;
      showLogin();
      $("#login-error").textContent = "נותקת עקב חוסר פעילות.";
    } else if (left <= 30 && !idleWarned) {
      idleWarned = true;
      toast(`ניתוק בעוד ${Math.ceil(left)} שניות עקב חוסר פעילות`);
    }
  }, 1000);
}

/* ---------- כניסה ---------- */

function showLogin() {
  clearInterval(pollTimer);
  clearInterval(idleTimer);
  $("#login").classList.remove("hidden");
  $("#app").classList.add("hidden");
}

async function showApp() {
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  $("#who-name").textContent = ME.username + (ME.role === "admin" ? " · מנהל" : " · הפצה");
  $("#who-avatar").textContent = ME.username.slice(0, 2);
  document.querySelectorAll("[data-admin]").forEach(
    (el) => el.classList.toggle("hidden", ME.role !== "admin"));
  await loadLibrary().catch((error) => toast("טעינת הספרייה נכשלה: " + error.message));
  await refreshStatus();
  pollTimer = setInterval(refreshStatus, 2000);
  startIdleWatch();
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    ME = await post("/login", { username: $("#login-user").value, password: $("#login-pass").value });
    $("#login-error").textContent = "";
    await showApp();
  } catch (error) { $("#login-error").textContent = error.message; }
});

$("#logout").addEventListener("click", async () => { await post("/logout"); ME = null; showLogin(); });

/* ---------- טאבים ---------- */

const TAB_LOADERS = {
  library: () => loadLibrary(),
  machines: () => loadMachinesTab(),
  // שני צדדים של אותו כרטיס: מה הוא מחלק, ואיך השרת עצמו מחובר דרכו
  // (‏#55–#57). מסך אחד, כי הם מגבילים זה את זה — כרטיס שמחלק DHCP
  // חייב כתובת סטטית שמתאימה לטווח.
  net: () => Promise.all([loadNet(), loadNetcfg()]),
  // הבריאות והמתגים של ה-SSH הם אותו מסך: החיווי בלי המתג הוא תלונה
  // בלי כפתור, והמתג בלי החיווי הוא בדיוק מה שהיה עד היום (#83).
  health: () => Promise.all([loadHealth(), loadSsh()]),
  users: () => loadUsers(),
  journal: () => loadJournal(),
  settings: () => loadSettings(),
};

document.querySelectorAll("#tabs button").forEach((button) =>
  button.addEventListener("click", () => {
    document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b === button));
    document.querySelectorAll(".tab").forEach((tab) =>
      tab.classList.toggle("hidden", tab.id !== "tab-" + button.dataset.tab));
    // כשל טעינה חייב להיראות. לשונית ריקה בלי הסבר נקראת כמו
    // "הנתונים נעלמו", וזה בדיוק מה שקרה בפועל.
    TAB_LOADERS[button.dataset.tab]().catch((error) =>
      toast("טעינה נכשלה: " + error.message));
  }));

/* ---------- מדדים + הסבב הפעיל (קריאה בלבד — סעיף 12 באפיון) ---------- */

/* מכונה שאתחלה שוב ושוב לאותו סבב ולא הגיעה — המשפט שמופיע לידה.
   בלי זה מחשב שהסוכן שלו נכשל נראה בדיוק כמו מחשב שלא נדלק (#64, #75). */
function stuckNote(stuck, mac) {
  const s = stuck && stuck[mac];
  if (!s) return "";
  return s.blocked
    ? `נכשל באתחול ${s.attempts} פעמים — נשלח לדיסק המקומי`
    : `אתחל ${s.attempts} פעמים ולא הצטרף`;
}

function memberRow(m, session, note = "") {
  const pct = m.bytes_total ? Math.round((100 * m.bytes_written) / m.bytes_total) : 0;
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
    <div class="bar"><i style="width:${pct}%"></i></div>
    <span class="pct">${m.state === "waiting" ? "ממתין" : pct + "%"}</span>
    ${err}
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

async function refreshStatus() {
  let data;
  try { data = await api("/overview"); } catch (error) { return; }
  if (data.storage) {
    $("#m-storage").textContent = fmtBytes(data.storage.free_bytes);
    $("#m-storage-sub").textContent = "פנוי מתוך " + fmtBytes(data.storage.total_bytes);
  }
  $("#m-images").textContent = data.images;
  $("#m-machines").textContent = data.machines;
  // קליטה שרצה מתקדמת בלי שהמשתמש עושה דבר — מרעננים איתה.
  if (ME.role === "admin" && $("#capture-bar").innerHTML) loadCaptures();

  const s = data.session;
  const pulls = data.pulls || [];
  if (!s) {
    $("#session-body").innerHTML = pulls.length
      ? pullsHtml(pulls, false) : "אין סבב פעיל.";
    return;
  }
  const stateHe = s.state === "open" ? "פתוח להצטרפות" : "משדר";
  // הרשימה כמו שהייתה — מי, איזה אימג', כמה הצטרפו — בלי פסי המחשבים:
  // לחיצה על הכרטיס פותחת חלון עם מחשבי הכיתה של הסבב הזה בלבד.
  // מי שנשאר מחוץ לבחירה שייך לסבב הבא — ומוצג מתחת לסבב הפעיל.
  const who = s.single
    ? (s.members[0] && (s.members[0].hostname || s.members[0].name || s.members[0].mac))
      || "תחנה בודדת"
    : s.group_label;
  const classMachines = s.single ? [] : await sessionClassMachines(s.group_id);
  const roster = s.roster ? new Set(s.roster) : null;
  const nextOnes = roster
    ? classMachines.filter((m) => !roster.has(m.mac)).map((m) => m.suffix) : [];
  $("#session-body").innerHTML = `
    <div class="session-line" role="button" tabindex="0"
         title="לחיצה מציגה את ההתקדמות של כל המחשבים">
      <div class="session-head">
        <span class="state ${s.state}">${stateHe}</span>
        <b>${esc(who)}</b>
      </div>
      ${s.single ? `<div class="sub">${esc(s.group_label)}</div>` : ""}
      <div class="sub">מושך: <b>${esc(s.image_name)}</b></div>
      ${s.single ? "" : `<div class="sub">הצטרפו ${s.joined} מתוך ${s.expected_clients}</div>`}
      ${s.state === "open" && s.joined > 0
        ? `<div class="sub">מתחיל בעוד ${s.starts_in_seconds} שניות, או כשכולם יצטרפו</div>` : ""}
    </div>
    ${nextOnes.length ? `<div class="divide"></div>
      <div class="sub">לסבב הבא: ${esc(nextOnes.join(", "))}</div>` : ""}
    ${pullsHtml(pulls, true)}`;
  // הפאנל הזה הוא תצוגה בלבד (סעיף 12 באפיון). עצירת סבב היא פעולה
  // הרסנית — שידור חי לכיתה שלמה — ולכן היא יושבת בהגדרות, מאחורי
  // הקלדת שם הכיתה, ולא כפתור חשוף ליד פס ההתקדמות.
  $("#session-body .session-line").onclick = () => {
    // כל מחשבי הסבב, גם מי שעוד לא הצטרף — הסבבים הבאים לא כאן.
    const inRound = s.single ? [] : classMachines.filter((m) => !roster || roster.has(m.mac));
    const byMac = Object.fromEntries(s.members.map((m) => [m.mac, m]));
    const stuck = s.stuck || {};
    const rows = s.single
      ? s.members.map((m) => memberRow(m, s, stuckNote(stuck, m.mac))).join("")
      : inRound.map((m) => byMac[m.mac]
          ? memberRow(byMac[m.mac], s, stuckNote(stuck, m.mac))
          : `<div class="member ${stuck[m.mac] ? "failed" : ""}"><div class="who-line">
               <b>${esc(s.prefix)}-${esc(m.suffix)}</b>
               <span class="sub-mac">${esc(stuckNote(stuck, m.mac) || "עוד לא הצטרף")}</span>
             </div></div>`).join("");
    sheet({
      title: who,
      sub: `${esc(s.image_name)} · ${stateHe}`
        + (s.single ? "" : ` · הצטרפו ${s.joined} מתוך ${s.expected_clients}`),
      note: `<div class="session-members">${rows
        || `<p class="sub">עוד לא הצטרף אף מחשב.</p>`}</div>`,
      submitLabel: "סגור",
      onSubmit: async () => {},
    });
  };
}

/* מחשבי הכיתה של הסבב — נטענים פעם אחת לקבוצה ונשמרים בין רענונים. */
let SESSION_MACHINES = { group: null, list: [] };
async function sessionClassMachines(groupId) {
  if (SESSION_MACHINES.group !== groupId) {
    try {
      SESSION_MACHINES = {
        group: groupId,
        list: await api("/machines?group=" + encodeURIComponent(groupId)),
      };
    } catch (error) { return []; }
  }
  return SESSION_MACHINES.list;
}

/* ---------- משתמשים ---------- */

async function loadUsers() {
  const list = await api("/users");
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
        await put(`/users/${u.username}`, body);
        await loadUsers();
        toast("נשמר.");
      },
    });
  });

  document.querySelectorAll("[data-del-user]").forEach((b) => b.onclick = () => confirmSheet(
    "מחיקת משתמש", `המשתמש ${b.dataset.delUser} יאבד גישה מיידית.`, "מחק",
    async () => { await del(`/users/${b.dataset.delUser}`); await loadUsers(); }));
}

$("#add-user").addEventListener("click", () => sheet({
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
  onSubmit: async (v) => { await post("/users", v); await loadUsers(); },
}));

/* ---------- יומן — עברית, שמות במקום מזהים, סינון וחיפוש (#115) ---------- */

let JOURNAL_EVENTS_LOADED = false;

// המפתחות שהשרת מצפה להם ב-query string, כל אחד מקושר לשדה שלו במסך.
const JOURNAL_FILTER_FIELDS = {
  event: "#jf-event", machine: "#jf-machine", q: "#jf-q",
  from: "#jf-from", to: "#jf-to",
};

async function loadJournalEvents() {
  if (JOURNAL_EVENTS_LOADED) return;
  const events = await api("/journal/events");
  const select = $("#jf-event");
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
  await loadJournalEvents();
  // fetch ישיר, לא api(): צריך את כותרת האזהרה על סינון חלקי, לא רק
  // את הגוף (עיקרון 5 — "לא בדקנו הכל" אינו "אין תוצאות").
  const response = await fetch("/api/console/journal" + journalFilterQuery(), { credentials: "same-origin" });
  if (response.status === 401) { showLogin(); throw new Error("לא מחובר"); }
  if (!response.ok) throw new Error("שגיאה " + response.status);
  if (response.headers.get("X-Journal-Search-Truncated") === "true") {
    toast("החיפוש מכסה רק את השורות האחרונות ביומן — נסו לצמצם עם טווח תאריכים");
  }
  const rows = await response.json();
  $("#journal-table tbody").innerHTML = rows.length
    ? rows.map((r) => `<tr>
        <td class="mono" dir="ltr">${r.ts.replace("T", " ").slice(0, 19)}</td>
        <td>${esc(r.user) || "המערכת"}</td><td><b>${esc(r.label)}</b></td>
        <td>${esc(r.text)}</td>
      </tr>`).join("")
    : `<tr><td colspan="4" class="lib-empty">אין רשומות שתואמות לסינון.</td></tr>`;
}

// דיבאונס על שדות הטקסט — לא לירות בקשה על כל תו; הפתרון והלחיצות
// (הסוג, האיפוס) רצות מיד.
function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

const journalReload = () => loadJournal().catch((error) => toast("סינון היומן נכשל: " + error.message));
const journalReloadDebounced = debounce(journalReload, 300);

$("#jf-event").addEventListener("change", journalReload);
$("#jf-from").addEventListener("change", journalReload);
$("#jf-to").addEventListener("change", journalReload);
$("#jf-machine").addEventListener("input", journalReloadDebounced);
$("#jf-q").addEventListener("input", journalReloadDebounced);
$("#jf-clear").addEventListener("click", () => {
  for (const sel of Object.values(JOURNAL_FILTER_FIELDS)) $(sel).value = "";
  journalReload();
});

/* ---------- הגדרות ---------- */

async function loadSettings() {
  const s = await api("/settings");
  $("#set-login").checked = s.recovery_require_login === "true";
  $("#set-wait").value = Number(s.session_wait_seconds);
  $("#set-idle").value = Number(s.console_idle_seconds);
  await loadLogoSettings();

  // עצירת החירום מופיעה רק כשיש מה לעצור, ורק אחרי הקלדת שם הכיתה.
  const session = (await api("/overview")).session;
  $("#stop-panel").classList.toggle("hidden", !session);
  if (!session) return;
  const what = session.single ? "תחנה בודדת" : session.group_label;
  $("#stop-what").innerHTML =
    `סבב פעיל: <b>${esc(what)}</b> — ${esc(session.image_name)}`;
  $("#stop-round").onclick = () => sheet({
    title: "עצירת הסבב",
    sub: `${what} · הצטרפו ${session.joined} מתוך ${session.expected_clients}`,
    verify: { label: "להמשך, הקלידו את שם הכיתה", mustEqual: what },
    submitLabel: "עצור את הסבב", danger: true,
    onSubmit: async () => {
      await post(`/sessions/${session.id}/close`);
      await loadSettings();
      await refreshStatus();
      toast("הסבב נעצר.");
    },
  });
}

$("#settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await post("/settings", {
    recovery_require_login: $("#set-login").checked ? "true" : "false",
    session_wait_seconds: String($("#set-wait").value),
    console_idle_seconds: String($("#set-idle").value),
  });
  ME.idle_seconds = Number($("#set-idle").value);   // תקף מיידית, בלי כניסה מחדש
  startIdleWatch();
  $("#settings-saved").textContent = "נשמר.";
  setTimeout(() => { $("#settings-saved").textContent = ""; }, 2000);
});

/* ---------- SSH — שתי דלתות, וחיווי לפי מה שמאזין באמת (#83) ----------

   הנורה ליד כל מתג אינה משקפת את המתג. היא משקפת קריאה חוזרת: טבלת
   הסוקטים של הקרנל לשרת, והתפריט שהשרת מגיש בפועל לתחנות. מתג שנכשל
   ומצייר "כבוי" הוא המצב המסוכן — מפעיל שמאמין שסגר ולא סגר. */

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
  SSH_STATE = await api("/ssh");
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
      `/ssh/interfaces/${encodeURIComponent(nic.name)}`, !nic.enabled, nic.name,
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

$("#ssh-refresh").addEventListener("click", () =>
  loadSsh().catch((error) => toast("רענון נכשל: " + error.message)));

/* ---------- אתחול ---------- */

window.addEventListener("DOMContentLoaded", () => {
  // בכניסה הראשונה לא "זוכרים" את מה שהמערכת הכתיבה — רק אם נגעו במתג.
  applyTheme(currentTheme(), localStorage.getItem("imagectl-theme") !== null);
  loadLogo();
  api("/me").then((me) => { ME = me; return showApp(); }).catch(() => showLogin());
});
