/* מסך מחשב הבנייה — קיוסק. ה-MAC מגיע ב-URL מהסוכן שהריץ את הדפדפן.
   כל המצב מהשרת: הדף רק מציג ומזמין, העבודה עצמה נעשית בסוכן.

   שני מסלולים לפי תפקיד (Issue #9): מנהל בוחר בין קליטה להפצה,
   משתמש הפצה מגיע ישר להפצה לחדר. ההפצה עצמה — ב-room.js. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const MAC = new URLSearchParams(location.search).get("mac") || "";
let SIGNED_IN = false;
let ROLE = null;                 // "admin" / "deploy"
let MODE = null;                 // null (תפריט) / "capture" / "room"
let CHOSEN_DISK = null;
let LAST_TASK_STATE = null;

function esc(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

function fmtBytes(n) {
  if (!n) return "0";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return n.toFixed(n >= 100 ? 0 : 1) + " " + units[i];
}

let toastTimer = null;
function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 2600);
}

function show(cardId) {
  ["st-login", "st-menu", "st-pick", "st-room", "st-class", "st-progress",
   "st-done", "st-message"]
    .forEach((id) => $("#" + id).classList.toggle("hidden", id !== cardId));
}

function message(title, sub) {
  $("#st-msg-title").textContent = title;
  $("#st-msg-sub").textContent = sub;
  show("st-message");
}

function afterLogin(info) {
  SIGNED_IN = true;
  ROLE = info.role;
  MODE = null;                               // שני התפקידים מתחילים מהתפריט
  $("#st-menu-sub").textContent = "מחוברים כ־" + info.username;
  // קליטה היא פעולת מנהל; הפצה — לחדר או לכיתות — פתוחה לשניהם.
  $("#st-menu-capture").classList.toggle("hidden", ROLE !== "admin");
  $("#st-back-capture").classList.remove("hidden");
  loadFolders();
}

/* ---------- המצב מהשרת, כל 2 שניות ---------- */

async function poll() {
  let state;
  try {
    state = await fetch(`/api/v1/agent/state?mac=${encodeURIComponent(MAC)}`)
      .then((r) => r.json());
  } catch (error) {
    // אין קשר לשרת — אומרים את זה על המסך במקום לקפוא על מצב ישן (#34)
    if (MODE === "classes" && Classes.isLive())
      $("#st-class-sub").textContent = "אין קשר לשרת — מנסים שוב…";
    return;
  }

  $("#st-mac").textContent = state.mac || MAC;
  if (!state.known) {
    message("המחשב אינו רשום", "רשמו אותו בקונסולה כמחשב בניית אימג'ים ורעננו.");
    return;
  }
  if (state.role !== "build") {
    message("המסך הזה מיועד למחשב בניית אימג'ים",
            `המכונה רשומה כ: ${state.group_label}`);
    return;
  }

  const task = state.task;
  const watchingProgress = !$("#st-progress").classList.contains("hidden");
  const showingDone = !$("#st-done").classList.contains("hidden");

  if (task && (task.state === "pending" || task.state === "running")) {
    drawProgress(task);                      // משימה רצה — תמיד גוברת
  } else if (watchingProgress && task) {
    drawDone(task);                          // הסתיימה בזמן שצפינו בה
  } else if (showingDone) {
    /* נשארים על מסך הסיום עד "קליטה נוספת" */
  } else if (MODE === "classes" && (SIGNED_IN || Classes.isLive())) {
    // תצוגת סבב חי ממשיכה גם כשה-cookie פג (#34): המסך עומד בכיתה,
    // והתצוגה מגיעה מ-endpoint ללא כניסה. פעולות יחזירו לכניסה בקול.
    show("st-class");
    Classes.refresh();
  } else if (!SIGNED_IN) {
    show("st-login");
  } else if (MODE === "room") {
    show("st-room");
    Room.refresh();
  } else if (MODE === "capture") {
    show("st-pick");
    drawDisks(state.disks);
  } else {
    show("st-menu");
  }
  LAST_TASK_STATE = task && task.state;
}

function drawProgress(task) {
  const pct = task.bytes_total
    ? Math.round((100 * task.bytes_written) / task.bytes_total) : 0;
  $("#st-prog-title").textContent = `קולט: ${task.name}`;
  $("#st-prog-sub").textContent = task.state === "pending"
    ? "ממתין לסוכן — ודאו שהמחשב עלה ב-PXE" : `כונן המקור: ${task.disk}`;
  $("#st-bar").style.width = pct + "%";
  $("#st-pct").textContent = pct + "%";
  $("#st-bytes").textContent = task.bytes_written
    ? `${fmtBytes(task.bytes_written)} נקראו` : "";
  show("st-progress");
}

function drawDone(task) {
  if (task.state === "done") {
    $("#st-done-title").textContent = "הקליטה הושלמה";
    $("#st-done-sub").textContent =
      `"${task.name}" נכנס לספרייה. אפשר לכבות ולשלוף את הכונן.`;
  } else {
    $("#st-done-title").textContent = "הקליטה נכשלה";
    $("#st-done-sub").textContent = task.error || "ראו את היומן בקונסולה.";
  }
  show("st-done");
}

/* ---------- הכוננים ---------- */

function drawDisks(disks) {
  const internal = disks.filter((d) => !d.removable);
  if (!internal.length) {
    $("#st-disks").innerHTML =
      `<p class="sub">עוד לא דווחו כוננים — המידע מגיע כשהמחשב עולה ב-PXE.</p>`;
    return;
  }
  $("#st-disks").innerHTML = internal.map((d) => `
    <button type="button" class="disk-card ${CHOSEN_DISK === d.dev ? "sel" : ""}"
            data-dev="${esc(d.dev)}">
      <span class="tray"></span>
      <span><b>${esc(d.model) || "כונן"}</b>
        <small>${fmtBytes(d.size_bytes)} · ${d.has_data ? "יש מערכת על הכונן" : "ריק"}</small></span>
      <span class="dev">${esc(d.dev)}</span>
    </button>`).join("");
  document.querySelectorAll(".disk-card").forEach((card) =>
    card.addEventListener("click", () => {
      CHOSEN_DISK = card.dataset.dev;
      document.querySelectorAll(".disk-card").forEach((c) =>
        c.classList.toggle("sel", c === card));
      $("#st-form").classList.remove("hidden");
      $("#st-start").classList.remove("hidden");
      $("#st-name").focus();
    }));
}

/* ---------- תיקיות ---------- */

async function loadFolders() {
  if (ROLE !== "admin") return;
  try {
    const folders = await fetch("/api/console/folders",
                                { credentials: "same-origin" }).then((r) => r.json());
    $("#st-folder").innerHTML = `<option value="">ללא תיקייה</option>` +
      folders.map((f) => `<option value="${esc(f.name)}">${esc(f.name)}</option>`).join("");
  } catch (error) { /* הרשימה תישאר ריקה — הקליטה עדיין אפשרית */ }
}

$("#st-newfolder").addEventListener("click", () => {
  const input = $("#st-folder-new");
  input.classList.toggle("hidden");
  if (!input.classList.contains("hidden")) input.focus();
});

async function resolveFolder() {
  /* התיקייה שנבחרה, או זו שהוקלדה — נוצרת בשרת לפני הקליטה. */
  const typed = $("#st-folder-new").classList.contains("hidden")
    ? "" : $("#st-folder-new").value.trim();
  if (!typed) return $("#st-folder").value;
  const response = await fetch("/api/console/folders", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: typed }),
  });
  if (!response.ok) throw new Error("יצירת התיקייה נכשלה");
  return typed;
}

/* ---------- כניסה ---------- */

$("#st-eye").addEventListener("click", () => {
  const input = $("#st-pass");
  const showing = input.type === "text";
  input.type = showing ? "password" : "text";
  $("#st-eye").textContent = showing ? "הצג" : "הסתר";
});

$("#st-login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const response = await fetch("/api/console/login", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: $("#st-user").value, password: $("#st-pass").value }),
  });
  if (!response.ok) {
    $("#st-login-error").textContent = "שם משתמש או סיסמה שגויים";
    return;
  }
  $("#st-login-error").textContent = "";
  afterLogin(await response.json());
  await poll();
});

/* ---------- יצירת הקליטה ---------- */

$("#st-start").addEventListener("click", async () => {
  if (!CHOSEN_DISK || !$("#st-name").value.trim()) {
    $("#st-form-error").textContent = "בחרו כונן ותנו שם לאימג'";
    return;
  }
  let folder;
  try {
    folder = await resolveFolder();
  } catch (error) {
    $("#st-form-error").textContent = error.message;
    return;
  }
  const response = await fetch("/api/console/tasks/capture", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mac: MAC, disk: CHOSEN_DISK, folder,
      name: $("#st-name").value, description: $("#st-desc").value,
    }),
  });
  if (!response.ok) {
    let detail = "שגיאה " + response.status;
    try { detail = (await response.json()).detail || detail; } catch (e) {}
    $("#st-form-error").textContent = detail;
    return;
  }
  $("#st-form-error").textContent = "";
  toast("הקליטה הוזמנה.");
  await poll();
});

/* ---------- ניווט ---------- */

$("#st-menu-capture").addEventListener("click", () => { MODE = "capture"; poll(); });
$("#st-menu-room").addEventListener("click", () => { MODE = "room"; poll(); });
$("#st-menu-classes").addEventListener("click", () => {
  MODE = "classes"; Classes.reset(); poll();
});
$("#st-back-capture").addEventListener("click", () => { MODE = null; poll(); });
$("#st-again").addEventListener("click", () => {
  CHOSEN_DISK = null;
  MODE = null;
  show("st-menu");
  poll();
});

/* ---------- ערכת צבעים — אותו מתג ואותו זיכרון כמו בקונסולה ---------- */

function themeNow() {
  const saved = localStorage.getItem("imagectl-theme");
  if (saved === "dark" || saved === "light") return saved;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(id, remember = true) {
  document.documentElement.setAttribute("data-theme", id);
  if (remember) localStorage.setItem("imagectl-theme", id);
  const button = $("#st-theme");
  button.textContent = id === "dark" ? "☀" : "☾";
  button.title = id === "dark" ? "מעבר למצב בהיר" : "מעבר למצב כהה";
}

$("#st-theme").addEventListener("click", () =>
  applyTheme(themeNow() === "dark" ? "light" : "dark"));
applyTheme(themeNow(), localStorage.getItem("imagectl-theme") !== null);

/* ---------- אתחול ---------- */

if (!MAC) {
  message("חסר מזהה מכונה", "הדף נפתח בלי ?mac= — הסוכן אמור לספק אותו.");
} else {
  // תמיד מתחילים מכניסה, גם אם בדפדפן יש כבר session של הקונסולה:
  // מי שעומד מול המכונה מזדהה בעצמו — התפקיד שלו קובע מה מותר
  // (מנהל קולט ומפיץ; משתמש הפצה מפיץ בלבד).
  poll();
  setInterval(poll, 2000);
}
