/* ImageCtl — דף "אחסון" תחת תשתית (#1066 שלב ב'), בדיוק לפי המוקאפ
   docs/design/console-redesign/storage-mockup-2026-09-18.html שנדב אישר:
   רשימת מיקומים · הוסף → סוג · טופס NFS · SMB · iSCSI א' (פורטל→גילוי→
   יעד ו-CHAP→התחברות) · iSCSI ב' (מה על הדיסק, שלושה מצבים) · מגירת
   "לא נגיש". ה-API: server/console_storage_locations.py (interfaces.md §24).
   "שמור ועגן" נדלק רק אחרי POST /test מוצלח (עיקרון 5: "לא נבדק" אינו
   "תקין"); הפירמוט ומחיקת מיקום מאחורי הקלדת IQN / שם (עיקרון 7); במצב
   "לא הצלחנו לקרוא" אין כפתור פירמוט כלל. ערכי הטפסים חיים ב-SV.form (לא
   ב-DOM) כדי שהבדיקות ב-vm יוכלו להניע אותם. משתמש ב-UI/api/post/sheet/
   toast/esc/openDrawer/fmtBytes/fmtClock/ltr מ-console.js. */
"use strict";

let STORAGE = null, storageError = "", STORAGE_TIMER = null;
/* מצב הדף: view = list | add | local | nfs | smb | iscsi-a | iscsi-b.
   form = ערכי הטופס הנוכחי; scan = תוצאת "סרוק"; test = תוצאת "בדוק חיבור";
   iscsi = {session, loc, disk} של הזרימה הדו-שלבית. */
const SV = { view: "list", form: {}, scan: null, test: null, iscsi: null, busy: false };
const STORAGE_STATE_CLS = { connected: "ok", unreachable: "err", disconnected: "", unchecked: "" };
const STORAGE_TYPE_ICON = {
  local: '<path d="M3 7h6l2 2h10v10H3z"/>',
  nfs: '<rect x="3" y="4" width="18" height="6" rx="1"/><rect x="3" y="14" width="18" height="6" rx="1"/><path d="M7 7h.01M7 17h.01"/>',
  smb: '<rect x="3" y="4" width="18" height="13" rx="1"/><path d="M8 21h8M12 17v4"/>',
  iscsi: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/>',
};
const storageIcon = (t) => `<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true">${STORAGE_TYPE_ICON[t] || STORAGE_TYPE_ICON.iscsi}</svg>`;
const SL = "/storage-locations";

async function loadStorage() {
  try {
    STORAGE = await api(SL);
    storageError = "";
  } catch (e) {
    STORAGE = null;   // לא נקרא ≠ אין מיקומים (עיקרון 5)
    storageError = e.message;
    toast("טעינת מיקומי האחסון נכשלה: " + e.message);
  }
  if (current === "storage") renderCurrent();
  startStorageTimer();
}
/* רענון כל 30 שניות — רק ברשימה (לא באמצע טופס), רק כשהדף פתוח וגלוי. */
function startStorageTimer() {
  clearInterval(STORAGE_TIMER);
  STORAGE_TIMER = setInterval(() => {
    if (current !== "storage") { clearInterval(STORAGE_TIMER); return; }
    if (SV.view === "list" && !document.hidden) loadStorage();
  }, 30000);
}
function storageLoc(id) { return ((STORAGE && STORAGE.locations) || []).find((l) => l.id === id) || null; }

/* ---------- ניווט בין המסכים ---------- */
function storageGo(view) {
  SV.view = view;
  if (["local", "nfs", "smb", "iscsi-a"].includes(view)) {
    SV.form = view === "nfs" ? { version: "4.1", readonly: false } : view === "iscsi-a" ? { auto: true, chap: false } : { readonly: false };
    SV.scan = null; SV.test = null; SV.iscsi = null;
  }
  if (view === "list") { SV.form = {}; SV.scan = null; SV.test = null; SV.iscsi = null; }
  renderCurrent();
  const page = document.querySelector("#content .page");
  if (page) page.scrollTop = 0;
}
/* שינוי בשדה שנבדק מבטל את תוצאת הבדיקה — "שמור ועגן" נדלק רק על מה שנבדק בפועל. */
function storageField(key, value) {
  SV.form[key] = value;
  if (SV.view === "iscsi-a") {
    if (key === "chap") renderCurrent();   // תיבת סימון — אין פוקוס לאבד
    const login = document.getElementById("st-login");
    if (login) login.disabled = !(SV.form.iqn && (SV.form.name || "").trim()) || SV.busy;
    return;
  }
  if (key === "name" || !SV.test) return;
  // בלי ציור מחדש באמצע הקלדה (הפוקוס היה נאבד): מכבים את "שמור" ומאפסים את התוצאה ב-DOM.
  SV.test = null;
  const save = document.getElementById("st-save"), result = document.getElementById("st-result");
  if (save) { save.disabled = true; save.title = "נדלק רק אחרי בדיקה מוצלחת"; }
  if (result) result.innerHTML = storageTestResultHtml("השדות השתנו אחרי הבדיקה — יש לבדוק שוב לפני שמירה.");
}
function storagePick(key, value) { SV.form[key] = value; SV.test = null; renderCurrent(); }

/* ---------- הדף ---------- */
function storagePage() {
  if (STORAGE === null && !storageError && SV.view === "list") return pagePlaceholder();
  const views = { list: storageListView, add: storageAddView, local: storageLocalView, nfs: storageNfsView, smb: storageSmbView, "iscsi-a": storageIscsiAView, "iscsi-b": storageIscsiBView };
  return (views[SV.view] || storageListView)();
}
function storageCrumbs(...tail) {
  const base = [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "תשתית" }];
  if (!tail.length) return base.concat([{ label: "אחסון" }]);
  return base.concat([{ label: "אחסון", onclick: "storageGo('list')" }], tail);
}
function storageStateHtml(l) {
  const cls = STORAGE_STATE_CLS[l.state] ?? "";
  const since = (l.state === "unreachable" || l.state === "disconnected") && l.state_since ? ` <span class="muted">· מאז ${esc(fmtClock(l.state_since))}</span>` : "";
  return `<span class="st ${cls}">${esc(l.state_label || l.state)}${since}</span>`;
}
function storageSpaceHtml(l) {
  if (l.free_bytes == null) return `<span class="muted">—</span>`;
  const text = `${fmtBytes(l.free_bytes)} / ${fmtBytes(l.total_bytes)}`;
  return l.state === "connected" ? ltr(text) : `<span class="muted">— (נמדד לאחרונה ${ltr(fmtBytes(l.free_bytes))})</span>`;
}
function storageRowActs(l) {
  const id = encodeId(l.id), b = (label, fn, cls = "") => `<button class="btn sm${cls ? " " + cls : ""}" onclick="${fn}('${id}')">${label}</button>`;
  const acts = [b("בדוק", "storageCheck")];
  if (l.removable) {
    if (l.state === "disconnected") acts.push(b("חבר", "storageConnect"), b("הסר", "storageDelete", "danger"));
    else {
      if (l.type === "iscsi" && l.state === "unchecked") acts.unshift(b("המשך הגדרה", "storageResumeIscsi", "primary"));
      acts.push(b("נתק", "storageDisconnect"));
    }
  }
  return `<div class="acts">${acts.join("")}</div>`;
}
function storageRow(l) {
  const id = encodeId(l.id);
  const target = l.type === "iscsi" && l.params && l.params.portal
    ? `<span class="mono">${esc(l.target)}</span><span class="sub mono">${esc(l.params.portal)}</span>` : `<span class="mono">${esc(l.target)}</span>`;
  const images = `${l.images}${l.unavailable_images ? ` <span class="muted">${l.unavailable_images === l.images ? "לא זמינים" : `${l.unavailable_images} לא זמינים`}</span>` : ""}`;
  return { attrs: `data-loc="${esc(l.id)}"`, cells: [
    `<a role="link" tabindex="0" class="name" onclick="storageOpenDrawer('${id}')">${esc(l.name)}</a>${l.subtitle ? `<span class="sub">${esc(l.subtitle)}</span>` : ""}`,
    esc(l.type_label), target, storageStateHtml(l), `<span class="num">${storageSpaceHtml(l)}</span>`, `<span class="num">${images}</span>`, storageRowActs(l)] };
}
function storageListView() {
  const s = (STORAGE && STORAGE.summary) || {}, list = (STORAGE && STORAGE.locations) || [];
  const sub = STORAGE
    ? ["הספרייה מאחדת את כל המיקומים", `${s.images ?? 0} אימג'ים`, s.free_bytes ? `${fmtBytes(s.free_bytes)} פנוי בסך הכול` : "פנוי: לא נמדד",
       s.checked_at ? `נבדק ${fmtClock(s.checked_at)} (כל 60 שניות)` : "עדיין לא נבדק"].map(esc).join(" · ")
    : `‏/storage-locations לא נקרא: ${esc(storageError)}`;
  const pill = !STORAGE ? UI.pill("err", "לא נקרא")
    : s.unreachable ? UI.pill("err", `${s.locations} מיקומים · ${s.unreachable} לא נגיש`) : UI.pill("ok", `${s.locations} ${s.locations === 1 ? "מיקום" : "מיקומים"}`);
  const actions = `<button class="btn primary" onclick="storageGo('add')">+ הוסף מיקום</button><button class="btn" onclick="storageCheckAll()"${SV.busy ? " disabled" : ""}>בדוק הכול</button><button class="btn" onclick="loadStorage()">רענון</button>`;
  const header = UI.objHeader({ crumbs: storageCrumbs(), icon: "storage", name: "אחסון", sub, pill, actions });
  let body;
  if (!STORAGE) body = UI.note("err", `לא הצלחתי לקרוא את מיקומי האחסון: ${esc(storageError)}`);
  else {
    const bad = list.filter((l) => l.state !== "connected" && l.unavailable_images);
    const note = s.unavailable_images
      ? `<div class="c12">${UI.note("err", `<b>${s.unavailable_images} ${s.unavailable_images === 1 ? "אימג' לא זמין" : "אימג'ים לא זמינים"} כרגע</b> — ${bad.length ? `על ${bad.map((l) => `<span class="mono">${esc(l.name)}</span> (${esc(l.state_label)}${l.state_since ? ` מאז ${esc(fmtClock(l.state_since))}` : ""})`).join(", ")}. ` : ""}${s.unavailable_images === 1 ? "הוא רשום בספרייה ולא נמחק; סבב שיבחר בו יסרב להתחיל." : "הם רשומים בספרייה ולא נמחקו; סבב שיבחר בהם יסרב להתחיל."}${bad.length ? ` ${UI.link("פרטים", `storageOpenDrawer('${encodeId(bad[0].id)}')`)}` : ""}`)}</div>` : "";
    const table = UI.datagrid({ cls: "show-acts storage", columns: ["שם", "סוג", "יעד", "מצב", 'פנוי / סה"כ', "אימג'ים", ""], rows: list.map(storageRow), empty: "אין מיקומים — גם 'מקומי' חסר; השרת לא יצר אותו בעלייה" });
    const legend = `<div class="legend-states"><span class="st ok">מחובר — מעוגן ונקרא בבדיקה האחרונה</span><span class="st err">לא נגיש — מעוגן, אבל הבדיקה נכשלה; מנסים שוב כל 60 שניות</span><span class="st">מנותק — המפעיל ניתק; לא מנסים עד "חבר"</span><span class="st">לא נבדק — נשמר ועדיין לא הורצה בדיקה</span></div>`;
    // לא UI.card: ה-small מוברח, ו-"--images" בלי .mono מתהפך ל-"images--" ב-RTL.
    body = note + `<div class="c12 card"><div class="card-h"><span>מיקומים <small>"מקומי" הוא המיקום המובנה (<span class="mono">--images</span>) — תמיד ראשון, לא ניתן להסרה</small></span></div><div class="card-b${list.length ? " flush" : ""}">${table}${legend}</div></div>`;
  }
  return `<div class="page">${header}<div class="body">${body}</div></div>`;
}

/* ---------- 2. הוסף מיקום — בחירת סוג ---------- */
function storageAddView() {
  const header = UI.objHeader({ crumbs: storageCrumbs({ label: "הוסף מיקום" }), icon: "plus", name: "הוסף מיקום", sub: "שלב 1 מתוך 2 — איזה סוג אחסון מחברים לספרייה",
    actions: `<button class="btn" onclick="storageGo('list')">ביטול</button>` });
  const type = (t, title, p, when, view, primary) => `<div class="type"><div class="th"><span class="obj-icon">${storageIcon(t)}</span>${esc(title)}</div><p>${p}</p><div class="when">${esc(when)}</div><button class="btn${primary ? " primary" : ""}" onclick="storageGo('${view}')">בחר</button></div>`;
  const types = `<div class="c12 types">`
    + type("local", "תיקייה מקומית", "נתיב בשרת עצמו — דיסק נוסף או תיקייה שכבר מעוגנת במערכת.", "כשהאימג'ים יושבים על דיסק בשרת.", "local", false)
    + type("nfs", "NFS", "ייצוא משרת לינוקס, TrueNAS או Synology. בלי סיסמה — ההרשאה לפי כתובת השרת. גרסה 4.1.", "ברירת המחדל כשיש NAS במכללה.", "nfs", true)
    + type("smb", "SMB", "שיתוף Windows או Synology עם משתמש וסיסמה. ‏SMB2 ומעלה בלבד.", "כשה-NAS או שרת הקבצים מציעים רק שיתוף Windows.", "smb", false)
    + type("iscsi", "iSCSI", "דיסק בלוק (LUN) מ-NAS או SAN. השרת מתחבר כיוזם ומעגן מערכת קבצים שכבר על הדיסק.", "כשהשרת הוא VM והאחסון מגיע מ-SAN. שני שלבים.", "iscsi-a", false)
    + `</div>`;
  const note = `<div class="c12">${UI.note("info", `כל מיקום נבדק לפני שמירה, ונשמר עם <span class="mono">_netdev</span> כך שהוא חוזר לבד אחרי אתחול השרת. אחסון על כרטיס ההפצה מאט את המולטיקאסט (‏R10: כ-46 MB/s על NIC משותף) — הבדיקה מזהירה על כך.`)}</div>`;
  return `<div class="page">${header}<div class="body">${types}${note}</div></div>`;
}

/* ---------- טפסים: שדות, סריקה, בדיקה, שמירה ---------- */
function storageInput(key, label, { mono = false, type = "text", placeholder = "" } = {}) {
  const v = SV.form[key] == null ? "" : SV.form[key];
  return `<div class="field"><label for="st-${key}">${label}</label><input id="st-${key}" type="${type}" class="${mono ? "mono" : ""}" value="${esc(v)}" placeholder="${esc(placeholder)}" autocomplete="off" oninput="storageField('${key}',this.value)"></div>`;
}
function storageCheckbox(key, label, onchange = "") {
  return `<label class="check"><input type="checkbox" id="st-${key}" ${SV.form[key] ? "checked" : ""} onchange="storageField('${key}',this.checked)${onchange}"><span>${label}</span></label>`;
}
function storagePicklist(key, items, mapItem, emptyText) {
  if (SV.scan && SV.scan.status === "running") return `<div class="empty" style="padding:14px"><span class="st run">סורק…</span></div>`;
  if (SV.scan && SV.scan.status === "failed") return `<div class="empty" style="padding:14px"><span class="st err">הסריקה נכשלה</span><div class="cap" style="margin-top:6px">${esc(SV.scan.reason)}</div></div>`;
  if (!items) return `<div class="empty" style="padding:14px">${esc(emptyText)}</div>`;
  if (!items.length) return `<div class="empty" style="padding:14px">הסריקה הצליחה אבל לא חזר דבר — אפשר להקליד ידנית</div>`;
  return `<div class="picklist">${items.map((it) => { const { value, cap } = mapItem(it); return `<label><input type="radio" name="st-pick-${key}" value="${esc(value)}" ${SV.form[key] === value ? "checked" : ""} onchange="storagePick('${key}',this.value)"><span class="mono">${esc(value)}</span>${cap ? `<span class="cap">${esc(cap)}</span>` : ""}</label>`; }).join("")}</div>`;
}
function storageTestResultHtml(cap) {
  const t = SV.test;
  if (!t) return `<span class="st">לא נבדק</span><div class="cap" style="margin-top:6px">${cap}</div>`;
  if (t.status === "running") return `<span class="st run">בודק… (עיגון זמני, כתיבה, קריאת מקום פנוי)</span>`;
  const warns = (t.warnings || []).map((w) => UI.note("warn", esc(w))).join("");
  if (t.ok) {
    const kv = UI.kv([["פנוי", t.free_bytes == null ? `<span class="muted">לא נמדד</span>` : `${ltr(fmtBytes(t.free_bytes))} מתוך ${ltr(fmtBytes(t.total_bytes))}`], ["כתיבה", SV.form.readonly ? "לקריאה בלבד — לא נבדקה" : "אפשרית (קובץ בדיקה נכתב ונמחק)"]]);
    return `<span class="st ok">הצליח</span><div style="margin-top:8px">${kv}</div>${warns ? `<div style="margin-top:8px">${warns}</div>` : ""}`;
  }
  return `<span class="st err">נכשל</span><div style="margin-top:8px">${UI.note("err", `<span class="mono">${esc(t.reason || "בלי סיבה")}</span>`)}</div>${warns ? `<div style="margin-top:8px">${warns}</div>` : ""}`;
}
function storageFormFoot() {
  const ok = !!(SV.test && SV.test.ok) && !SV.busy;
  return `<div class="form-f"><button class="btn" onclick="storageTest()"${SV.busy ? " disabled" : ""}>בדוק חיבור</button><span class="sp"></span><button class="btn primary" id="st-save" onclick="storageSave()" ${ok ? "" : 'disabled title="נדלק רק אחרי בדיקה מוצלחת"'}>שמור ועגן</button></div>`;
}
function storageFormHeader(type, name, sub, back = "add") {
  return UI.objHeader({ crumbs: storageCrumbs({ label: "הוסף מיקום", onclick: "storageGo('add')" }, { label: name.replace("מיקום ", "").replace(" חדש", "") }), icon: "storage", name, sub,
    actions: `<button class="btn" onclick="storageGo('${back}')">חזרה</button><button class="btn" onclick="storageGo('list')">ביטול</button>` })
    .replace('<div class="obj-icon">' + uiIcon("storage") + "</div>", `<div class="obj-icon">${storageIcon(type)}</div>`);
}
/* הפרמטרים לפי סוג — אותם שמות כמו interfaces.md §24. */
function storageParams() {
  const f = SV.form, v = SV.view;
  if (v === "local") return { path: (f.path || "").trim() };
  if (v === "nfs") return { server: (f.server || "").trim(), export: (f.export || "").trim(), version: f.version || "4.1", readonly: !!f.readonly };
  if (v === "smb") return { server: (f.server || "").trim(), share: (f.share || "").trim(), username: (f.username || "").trim(), secret: f.secret || "", domain: (f.domain || "").trim(), readonly: !!f.readonly };
  const p = { portal: (f.portal || "").trim(), iqn: f.iqn || "", auto: f.auto !== false };
  if (f.chap) { p.chap_user = (f.chap_user || "").trim(); p.chap_secret = f.chap_secret || ""; }
  return p;
}
function storageType() { return SV.view === "iscsi-a" || SV.view === "iscsi-b" ? "iscsi" : SV.view; }

async function storageScan() {
  const f = SV.form, type = storageType();
  const body = { type, server: (f.server || "").trim() };
  if (type === "smb") body.creds = { username: (f.username || "").trim(), secret: f.secret || "", domain: (f.domain || "").trim() };
  if (type === "iscsi") { body.portal = (f.portal || "").trim(); if (f.chap) body.creds = { chap_user: (f.chap_user || "").trim(), chap_secret: f.chap_secret || "" }; }
  SV.scan = { status: "running" }; renderCurrent();
  try {
    const r = await post(SL + "/scan", body);
    SV.scan = { status: "ok", items: r.items || [] };
    const key = type === "nfs" ? "export" : type === "smb" ? "share" : "iqn";
    if (SV.scan.items.length === 1) SV.form[key] = SV.scan.items[0][key];
  } catch (e) { SV.scan = { status: "failed", reason: e.message }; }
  renderCurrent();
}
async function storageTest() {
  SV.test = { status: "running" }; SV.busy = true; renderCurrent();
  try {
    const r = await post(SL + "/test", { type: storageType(), params: storageParams() });
    SV.test = { status: "done", ok: !!r.ok, free_bytes: r.free_bytes, total_bytes: r.total_bytes, warnings: r.warnings || [], reason: r.reason || "", targets: r.targets };
  } catch (e) { SV.test = { status: "done", ok: false, reason: e.message, warnings: [] }; }
  SV.busy = false; renderCurrent();
}
async function storageSave() {
  if (!SV.test || !SV.test.ok) { toast("שמירה רק אחרי בדיקת חיבור מוצלחת"); return; }
  const name = (SV.form.name || "").trim();
  if (!name) { toast("חסר שם תצוגה"); return; }
  SV.busy = true; renderCurrent();
  try {
    const row = await post(SL, { name, type: storageType(), params: storageParams(), tested: true });
    toast(`המיקום ${row.name} נשמר ועוגן`);
    SV.busy = false; storageGo("list"); await loadStorage();
  } catch (e) {
    SV.busy = false; SV.test = { status: "done", ok: false, reason: e.message, warnings: [] }; renderCurrent();
  }
}

/* ---------- טופס תיקייה מקומית (אין מסך במוקאפ — מבנה ה-NFS בלי סריקה) ---------- */
function storageLocalView() {
  const header = storageFormHeader("local", "מיקום תיקייה חדש", "שלב 2 מתוך 2 — נתיב בשרת שכבר מעוגן, ובדיקה לפני שמירה");
  const form = `<div class="card-b form1">${storageInput("path", "נתיב בשרת", { mono: true, placeholder: "/mnt/disk2/images" })}${storageInput("name", "שם תצוגה")}${storageFormFoot()}</div>`;
  const result = UI.card({ title: "תוצאת הבדיקה", cls: "c4", body: `<div class="result" id="st-result">${storageTestResultHtml('"לא נבדק" אינו "תקין". הכפתור "שמור ועגן" נדלק רק אחרי ראיה חיובית: התיקייה קיימת והמקום הפנוי נקרא.')}</div>` });
  return `<div class="page">${header}<div class="body"><div class="c8 card"><div class="card-h"><span>פרטי החיבור</span></div>${form}</div>${result}</div></div>`;
}

/* ---------- 3. טופס NFS ---------- */
function storageNfsView() {
  const header = storageFormHeader("nfs", "מיקום NFS חדש", "שלב 2 מתוך 2 — שרת, ייצוא, ובדיקה לפני שמירה");
  const exports = SV.scan && SV.scan.status === "ok" ? SV.scan.items : null;
  const form = `<div class="card-b form1">
    <div class="inline">${storageInput("server", "שרת (כתובת או שם DNS)", { mono: true })}<button class="btn" onclick="storageScan()">סרוק ייצואים</button></div>
    <div class="field"><label for="st-export">ייצוא <span class="muted">(<span class="mono">showmount -e</span> — נבחר אחד, או הקלדה ידנית)</span></label><input id="st-export" class="mono" value="${esc(SV.form.export || "")}" placeholder="/mnt/pool/images" autocomplete="off" oninput="storageField('export',this.value)">
      <div style="margin-top:8px">${storagePicklist("export", exports, (it) => ({ value: it.export, cap: it.clients ? `מורשה: ${it.clients}` : "" }), 'עדיין לא נסרק — לחץ "סרוק ייצואים", או הקלד ייצוא ידנית')}</div></div>
    <div class="form2"><div class="field"><label for="st-version">גרסת NFS</label><select id="st-version" onchange="storageField('version',this.value)">${["4.1", "4.0", "3"].map((v) => `<option value="${v}" ${(SV.form.version || "4.1") === v ? "selected" : ""}>${v}${v === "4.1" ? " (ברירת מחדל)" : ""}</option>`).join("")}</select></div>${storageInput("name", "שם תצוגה")}</div>
    ${storageCheckbox("readonly", "לקריאה בלבד — הפצה מהמיקום, בלי קליטה אליו")}
    ${storageFormFoot()}</div>`;
  const result = UI.card({ title: "תוצאת הבדיקה", cls: "c4", body: `<div class="result" id="st-result">${storageTestResultHtml('"לא נבדק" אינו "תקין". הכפתור "שמור ועגן" נדלק רק אחרי ראיה חיובית: עיגון זמני, כתיבת קובץ בדיקה, וקריאה של המקום הפנוי.')}</div>` });
  return `<div class="page">${header}<div class="body"><div class="c8 card"><div class="card-h"><span>פרטי החיבור</span></div>${form}</div>${result}</div></div>`;
}

/* ---------- 4. טופס SMB ---------- */
function storageSmbView() {
  const header = storageFormHeader("smb", "מיקום SMB חדש", "שלב 2 מתוך 2 — שרת, שיתוף, זהות, ובדיקה לפני שמירה");
  const shares = SV.scan && SV.scan.status === "ok" ? SV.scan.items : null;
  const form = `<div class="card-b form1">
    <div class="form2">${storageInput("username", "משתמש", { mono: true })}${storageInput("secret", "סיסמה", { type: "password" })}${storageInput("domain", 'דומיין <span class="muted">(אופציונלי)</span>', { mono: true })}${storageInput("name", "שם תצוגה")}</div>
    <div class="inline">${storageInput("server", "שרת", { mono: true })}<button class="btn" onclick="storageScan()">סרוק שיתופים</button></div>
    <div class="field"><label for="st-share">שיתוף <span class="muted">(הסריקה משתמשת במשתמש ובסיסמה שלמעלה)</span></label><input id="st-share" class="mono" value="${esc(SV.form.share || "")}" placeholder="images" autocomplete="off" oninput="storageField('share',this.value)">
      <div style="margin-top:8px">${storagePicklist("share", shares, (it) => ({ value: it.share, cap: it.type || "" }), 'עדיין לא נסרק — לחץ "סרוק שיתופים", או הקלד שם שיתוף ידנית')}</div></div>
    ${storageCheckbox("readonly", "לקריאה בלבד — הפצה מהמיקום, בלי קליטה אליו")}
    ${storageFormFoot()}</div>`;
  const result = UI.card({ title: "תוצאת הבדיקה", cls: "c4", body: `<div class="result" id="st-result">${storageTestResultHtml("הסיסמה נשמרת בשרת ולא מוצגת שוב בקונסולה (GET מחזיר את המיקום בלי הסוד).")}</div>` });
  return `<div class="page">${header}<div class="body"><div class="c8 card"><div class="card-h"><span>פרטי החיבור</span><small>SMB2 ומעלה · SMB1 אינו מוצע</small></div>${form}</div>${result}</div></div>`;
}

/* ---------- 5. iSCSI שלב א' — התחברות ליעד ---------- */
function storageSteps(done, cur) {
  const names = ["1 פורטל", "2 גילוי", "3 יעד ו-CHAP", "4 התחברות", "5 הדיסק"];
  return `<div class="steps">${names.map((n, i) => `<span class="step${i < done ? " done" : i === cur ? " cur" : ""}">${esc(n)}</span>`).join('<span class="sep"></span>')}</div>`;
}
function storageWithSteps(header, done, cur) { return header.replace(/<\/div>$/, storageSteps(done, cur) + "</div>"); }
function storageIscsiAView() {
  const f = SV.form, s = SV.iscsi;
  const targets = SV.scan && SV.scan.status === "ok" ? SV.scan.items : null;
  const done = s && s.session === "ok" ? 4 : f.iqn ? 3 : targets ? 2 : (f.portal || "").trim() ? 1 : 0;
  const header = storageWithSteps(storageFormHeader("iscsi", "מיקום iSCSI חדש", "שלב א' — התחברות ליעד. ההתחברות עצמה אינה מפרמטת ואינה מעגנת"), done, done >= 4 ? -1 : done);
  const canLogin = !!f.iqn && !!(f.name || "").trim() && !SV.busy && !(s && s.session === "ok");
  const form = `<div class="card-b form1">
    <div class="inline">${storageInput("portal", 'פורטל <span class="muted">(כתובת:פורט)</span>', { mono: true, placeholder: "10.44.3.75:3260" })}<button class="btn" onclick="storageScan()">גלה יעדים</button></div>
    <div class="field"><label>יעדים שנמצאו <span class="muted">(SendTargets)</span></label>${storagePicklist("iqn", targets, (it) => ({ value: it.iqn, cap: it.portal || "" }), 'עדיין לא בוצע גילוי — לחץ "גלה יעדים"')}</div>
    ${storageCheckbox("chap", "‏CHAP — היעד דורש אימות")}
    ${f.chap ? `<div class="form2">${storageInput("chap_user", "משתמש CHAP", { mono: true })}${storageInput("chap_secret", 'סוד CHAP <span class="muted">(12–16 תווים)</span>', { type: "password" })}</div>` : ""}
    ${storageCheckbox("auto", 'שמור בהתחברות אוטומטית — היעד חוזר לבד אחרי אתחול השרת (<span class="mono">node.startup=automatic</span>)')}
    ${storageInput("name", "שם תצוגה")}
    <div class="form-f"><span class="sp"></span><button class="btn primary" id="st-login" onclick="storageIscsiLogin()" ${canLogin ? "" : 'disabled title="נדלק אחרי גילוי, בחירת יעד ושם תצוגה"'}>התחבר</button></div></div>`;
  let session;
  if (!s || !s.session) session = `<span class="st">לא מחובר</span><div class="cap" style="margin-top:6px">ההתחברות עצמה אינה מפרמטת ואינה מעגנת. אחריה ה-LUN מופיע כדיסק, ובשלב ב' מחליטים מה לעשות איתו.</div>`;
  else if (s.session === "running") session = `<span class="st run">${esc(s.step || "מתחבר…")}</span>`;
  else if (s.session === "ok") session = `<span class="st ok">session פעילה</span><div style="margin-top:8px">${UI.kv([["דיסק", s.device ? `<span class="mono">${esc(s.device)}</span>` : `<span class="muted">by-path לא נמצא עדיין</span>`], ["יעד", `<span class="mono">${esc(s.loc.params.iqn || "")}</span>`], ["אתחול", f.auto !== false ? "automatic — שורד אתחול שרת" : "manual"]])}</div><div style="margin-top:10px"><button class="btn primary" onclick="storageIscsiDisk()">המשך לשלב ב' — הדיסק</button></div>`;
  else session = `<span class="st err">ההתחברות נכשלה</span><div style="margin-top:8px">${UI.note("err", `<span class="mono">${esc(s.reason)}</span>`)}</div>`;
  const side = UI.card({ title: "מצב ה-session", cls: "c4", body: `<div class="result">${session}</div>` });
  return `<div class="page">${header}<div class="body"><div class="c8 card"><div class="card-h"><span>פורטל וגילוי</span></div>${form}</div>${side}</div></div>`;
}
/* התחברות = בדיקה (גילוי) → יצירת המיקום (state=unchecked) → login. */
async function storageIscsiLogin() {
  const name = (SV.form.name || "").trim();
  if (!SV.form.iqn || !name) { toast("צריך יעד שנבחר ושם תצוגה"); return; }
  SV.busy = true; SV.iscsi = { session: "running", step: "מגלה את היעד…" }; renderCurrent();
  try {
    const t = await post(SL + "/test", { type: "iscsi", params: storageParams() });
    if (!t.ok) throw new Error(t.reason || "היעד לא הופיע בגילוי");
    SV.iscsi.step = "שומר את המיקום…"; renderCurrent();
    const loc = await post(SL, { name, type: "iscsi", params: storageParams(), tested: true });
    SV.iscsi = { session: "running", step: "מתחבר ליעד…", loc }; renderCurrent();
    const r = await post(`${SL}/${encodeURIComponent(loc.id)}/iscsi-login`, {});
    SV.iscsi = { session: "ok", loc, device: r.device_by_path || "", warnings: t.warnings || [] };
    if ((t.warnings || []).length) toast(t.warnings[0], 6000);
  } catch (e) { SV.iscsi = { session: "failed", reason: e.message, loc: SV.iscsi && SV.iscsi.loc }; }
  SV.busy = false; renderCurrent();
}
/* חזרה לזרימה של מיקום iSCSI שנשמר ולא הושלם (unchecked ברשימה). */
async function storageResumeIscsi(id) {
  try { id = decodeURIComponent(id); } catch (e) {}
  const loc = storageLoc(id);
  if (!loc) { toast("מיקום לא נמצא"); return; }
  SV.view = "iscsi-a"; SV.form = { portal: loc.params.portal, iqn: loc.params.iqn, name: loc.name, auto: loc.params.auto !== false, chap: !!loc.params.chap_user, chap_user: loc.params.chap_user || "" };
  SV.scan = { status: "ok", items: [{ iqn: loc.params.iqn, portal: loc.params.portal }] };
  SV.iscsi = { session: "running", step: "מתחבר ליעד…", loc }; SV.busy = true; renderCurrent();
  try {
    const r = await post(`${SL}/${encodeURIComponent(loc.id)}/iscsi-login`, {});
    SV.iscsi = { session: "ok", loc, device: r.device_by_path || "" };
  } catch (e) { SV.iscsi = { session: "failed", reason: e.message, loc }; }
  SV.busy = false; renderCurrent();
}
async function storageIscsiDisk() {
  const s = SV.iscsi;
  if (!s || !s.loc) return;
  SV.view = "iscsi-b"; s.disk = { disk_status: "reading" }; renderCurrent();
  try { s.disk = await api(`${SL}/${encodeURIComponent(s.loc.id)}/disk`); }
  catch (e) { s.disk = { disk_status: "unknown", reason: e.message, device: s.device }; }
  renderCurrent();
}

/* ---------- 6. iSCSI שלב ב' — ה-LUN כדיסק, שלושה מצבים ---------- */
function storageIscsiBView() {
  const s = SV.iscsi || {}, loc = s.loc || { params: {}, name: "", mount_point: "" }, d = s.disk || { disk_status: "reading" };
  const iqn = loc.params.iqn || "", device = d.device || s.device || "", size = d.fs_size != null ? ltr(fmtBytes(d.fs_size)) : "";
  const sub = `שלב ב' — ה-LUN הוא עכשיו דיסק בשרת: <span class="mono">${esc(device || "?")}</span>${size ? ` · ${size}` : ""} · <span class="mono">${esc(iqn)}</span>`;
  const header = storageWithSteps(UI.objHeader({ crumbs: storageCrumbs({ label: "הוסף מיקום", onclick: "storageGo('add')" }, { label: "iSCSI" }), icon: "storage", name: "מיקום iSCSI חדש", sub,
    pill: UI.pill("ok", "session פעילה"), actions: `<button class="btn" onclick="storageGo('iscsi-a')">חזרה</button><button class="btn" onclick="storageGo('list')">ביטול</button>` }), 4, 4);
  const idEnc = encodeId(loc.id || "");
  const nameCard = UI.card({ title: "שם ונקודת עיגון", cls: "c4", body: `<div class="form1"><div class="field"><label>שם תצוגה</label><input value="${esc(loc.name)}" disabled></div><div class="field"><label>נקודת עיגון</label><input class="mono" value="${esc(loc.mount_point)}" disabled></div><div class="cap">נקבעת מהשם. נרשם ב-fstab עם <span class="mono">_netdev</span>.</div></div>` });
  let main, side = nameCard;
  const diskKv = [["דיסק", `<span class="mono">${esc(device || "—")}${size ? ` · ${size}` : ""}</span>`]];
  if (d.disk_status === "reading") main = `<div class="c8 card"><div class="card-h"><span>מה נמצא על הדיסק</span></div><div class="card-b"><span class="st run">קורא את הדיסק… (blkid, lsblk)</span></div></div>`;
  else if (d.disk_status === "fs") {
    const kv = UI.kv(diskKv.concat([["מערכת קבצים", `<span class="st ok">${esc(d.fs_type)}</span>${d.fs_label ? ` · תווית <span class="mono">${esc(d.fs_label)}</span>` : ""}${d.fs_uuid ? ` · UUID <span class="mono">${esc(String(d.fs_uuid).slice(0, 4))}…${esc(String(d.fs_uuid).slice(-4))}</span>` : ""}`]]));
    main = `<div class="c8 card"><div class="card-h"><span>מה נמצא על הדיסק</span><small>נקרא עם blkid ו-lsblk · לא שונה דבר</small></div><div class="card-b disk-state">${kv}<div class="big-choice"><button class="btn primary" onclick="storageMount('${idEnc}')"${SV.busy ? " disabled" : ""}>עגן בלי פירמוט</button><button class="btn danger" onclick="storageFormatSheet('${idEnc}')"${SV.busy ? " disabled" : ""}>פרמט ועגן…</button><div class="cap">ברירת המחדל שומרת על מה שיש. פירמוט משמיד את מערכת הקבצים ודורש הקלדת ה-IQN.</div></div></div></div>`;
  } else if (d.disk_status === "empty") {
    const kv = UI.kv(diskKv.concat([["מערכת קבצים", `<span class="st">אין</span> — ‏blkid לא זיהה חתימה`], ["הראיה", "1 MB הראשונים והאחרונים — אפסים. זו ראיה חיובית לריק, לא היעדר שגיאה."]]));
    main = `<div class="c8 card"><div class="card-h"><span>מה נמצא על הדיסק</span><small>נקרא עם blkid ו-lsblk · לא שונה דבר</small></div><div class="card-b disk-state">${kv}<div class="big-choice"><button class="btn primary" onclick="storageFormatSheet('${idEnc}')"${SV.busy ? " disabled" : ""}>פרמט ועגן (ext4)…</button><div class="cap">גם בדיסק ריק הפירמוט עובר את אותו מודאל — כי "ריק" הוא קריאה שלנו, והדיסק שייך ל-NAS.</div></div></div></div>`;
  } else {
    // unknown: אין כפתור פירמוט כלל — "לא הצלחנו לקרוא" אינו "ריק" (עיקרון 5)
    main = `<div class="c8 card"><div class="card-h"><span>מה נמצא על הדיסק</span></div><div class="card-b disk-state"><div>${UI.note("err", `<b>לא הצלחנו לקרוא את הדיסק.</b> <span class="mono">${esc(d.reason || "בלי סיבה")}</span> ה-session פעילה, אבל ה-LUN לא עונה לקריאה.`)}<div class="cap" style="margin-top:10px">"לא הצלחנו לקרוא" אינו "ריק". לכן אין כאן כפתור פירמוט — קודם מבררים מול ה-NAS (LUN מוגדר? ACL של היוזם? גודל בלוק?), ואז מנסים שוב.</div></div><div class="big-choice"><button class="btn primary" onclick="storageIscsiDisk()">נסה לקרוא שוב</button><button class="btn" onclick="storageDisconnect('${idEnc}')">התנתק מהיעד</button></div></div></div>`;
    side = UI.card({ title: "מה לבדוק ב-NAS", cls: "c4", body: UI.kv([["פורטל", `<span class="mono">${esc(loc.params.portal || "")}</span>`], ["יעד", `<span class="mono">${esc(iqn)}</span>`]]) + `<div class="cap" style="margin-top:8px">ב-TrueNAS: ‏Shares → Block (iSCSI) → Initiators — האם היוזם של השרת מורשה ל-Target.</div>` });
  }
  return `<div class="page">${header}<div class="body">${main}${side}</div></div>`;
}
async function storageMount(id) {
  try { id = decodeURIComponent(id); } catch (e) {}
  SV.busy = true; renderCurrent();
  try {
    const row = await post(`${SL}/${encodeURIComponent(id)}/mount`, { format: false });
    toast(`${row.name} עוגן בלי פירמוט`);
    SV.busy = false; storageGo("list"); await loadStorage();
  } catch (e) { SV.busy = false; toast("העיגון נכשל: " + e.message); renderCurrent(); }
}
/* פירמוט — הקלדת ה-IQN המלא (עיקרון 7). הקלדה שגויה לא שולחת דבר. */
function storageFormatSheet(id) {
  try { id = decodeURIComponent(id); } catch (e) {}
  const s = SV.iscsi || {}, loc = (s.loc && s.loc.id === id) ? s.loc : storageLoc(id);
  if (!loc) { toast("מיקום לא נמצא"); return; }
  const d = s.disk || {}, iqn = (loc.params && loc.params.iqn) || "", device = d.device || s.device || "";
  if (d.disk_status !== "fs" && d.disk_status !== "empty") { toast("לא הצלחנו לקרוא את הדיסק — לא מפרמטים"); return; }
  const content = d.disk_status === "fs" ? `${esc(d.fs_type)}${d.fs_label ? ` עם התווית ${esc(d.fs_label)}` : ""}` : "דיסק ריק (נקרא כריק — אך שייך ל-NAS)";
  const note = UI.note("err", `<b>כל מערכות הקבצים והנתונים על הדיסק הזה יימחקו לצמיתות.</b><br><span class="mono">${esc(iqn)}</span>${d.fs_size != null ? ` · ${ltr(fmtBytes(d.fs_size))}` : ""} · ${content}. הפעולה בלתי הפיכה, וגם ה-NAS לא ישחזר אותה.`)
    + `<div style="margin:10px 0">${UI.kv([["מה ייעשה", `<span class="mono">mkfs.ext4 -L ${esc(loc.name.slice(0, 16))} ${esc(device)}</span>, ואז עיגון ב-<span class="mono">${esc(loc.mount_point)}</span>`]])}</div>`;
  sheet({
    title: "פירמוט הדיסק — השמדת נתונים", danger: true, submitLabel: "פרמט ועגן", note: `<div class="page drw">${note}</div>`,
    fields: [{ id: "confirm", label: "לאישור, הקלד את ה-IQN המלא של היעד", placeholder: iqn, dir: "ltr" }],
    onSubmit: async (v) => {
      if ((v.confirm || "").trim() !== iqn) throw new Error("ה-IQN שהוקלד אינו זהה ליעד");
      const row = await post(`${SL}/${encodeURIComponent(loc.id)}/format`, { confirm: iqn, wipe: d.disk_status === "fs" });
      toast(`${row.name} פורמט ועוגן`);
      storageGo("list"); await loadStorage();
    },
  });
}

/* ---------- פעולות שורה ---------- */
async function storageCheck(id) {
  try { id = decodeURIComponent(id); } catch (e) {}
  try {
    const row = await post(`${SL}/${encodeURIComponent(id)}/check`, {});
    storageReplace(row);
    toast(row.state === "connected" ? `${row.name}: מחובר — ${fmtBytes(row.free_bytes)} פנוי` : `${row.name}: ${row.state_label}${row.state_detail ? " — " + row.state_detail : ""}`);
    if (row.state !== "connected") storageOpenDrawer(encodeId(row.id));
  } catch (e) { toast("הבדיקה לא רצה: " + e.message); }
}
async function storageCheckAll() {
  if (!STORAGE || SV.busy) return;
  SV.busy = true; renderCurrent();
  let failed = 0;
  for (const l of STORAGE.locations) {
    try { storageReplace(await post(`${SL}/${encodeURIComponent(l.id)}/check`, {})); } catch (e) { failed++; }
  }
  SV.busy = false;
  await loadStorage();
  toast(failed ? `נבדקו ${STORAGE ? STORAGE.locations.length : "?"} מיקומים · ${failed} בדיקות לא רצו` : "כל המיקומים נבדקו");
}
function storageReplace(row) {
  if (!STORAGE) return;
  const i = STORAGE.locations.findIndex((l) => l.id === row.id);
  if (i >= 0) STORAGE.locations[i] = row; else STORAGE.locations.push(row);
  if (current === "storage") renderCurrent();
}
async function storageConnect(id) {
  try { id = decodeURIComponent(id); } catch (e) {}
  try {
    const row = await post(`${SL}/${encodeURIComponent(id)}/connect`, {});
    toast(`${row.name}: ${row.state_label}`); closeDrawer(); await loadStorage();
  } catch (e) { toast("החיבור נכשל: " + e.message); }
}
function storageDisconnect(id) {
  try { id = decodeURIComponent(id); } catch (e) {}
  const loc = storageLoc(id) || (SV.iscsi && SV.iscsi.loc && SV.iscsi.loc.id === id ? SV.iscsi.loc : null);
  if (!loc) { toast("מיקום לא נמצא"); return; }
  confirmSheet("ניתוק מיקום", `${loc.name} יסומן "מנותק": לא מנסים לבד עד "חבר". אימג'ים עליו יהיו "לא זמינים" (לא נמחקים).`, "נתק", async () => {
    const row = await post(`${SL}/${encodeURIComponent(id)}/disconnect`, {});
    toast(`${row.name}: ${row.state_label}`); closeDrawer();
    if (SV.view !== "list") storageGo("list");
    await loadStorage();
  });
}
/* הסרה — רק מיקום מנותק, מאחורי הקלדת השם (עיקרון 7). הדיסק ב-NAS לא נמחק. */
function storageDelete(id) {
  try { id = decodeURIComponent(id); } catch (e) {}
  const loc = storageLoc(id);
  if (!loc) { toast("מיקום לא נמצא"); return; }
  if (loc.state !== "disconnected") { toast("הסרה רק אחרי ניתוק"); return; }
  sheet({
    title: "הסרת מיקום", sub: "המיקום יורד מהרשימה. הדיסק ב-NAS לא נמחק ולא מפורמט. השרת מסרב אם יש עליו אימג'ים.", danger: true, submitLabel: "הסר",
    fields: [{ id: "confirm", label: "לאישור, הקלד את שם המיקום", placeholder: loc.name }],
    onSubmit: async (v) => {
      if ((v.confirm || "").trim() !== loc.name) throw new Error("השם שהוקלד אינו זהה לשם המיקום");
      await api(`${SL}/${encodeURIComponent(id)}`, { method: "DELETE", body: JSON.stringify({ confirm: loc.name }) });
      toast(`${loc.name} הוסר`); closeDrawer(); await loadStorage();
    },
  });
}

/* ---------- 7. מגירת מיקום ("לא נגיש") ---------- */
function storageOpenDrawer(id) {
  try { id = decodeURIComponent(id); } catch (e) {}
  const loc = storageLoc(id);
  if (!loc) { toast("מיקום לא נמצא"); return; }
  openDrawer(loc.name, storageDrawerHtml(loc));
  if (IMAGES == null) api("/images").then((rows) => { IMAGES = rows; if (storageLoc(id)) document.getElementById("drawerBody").innerHTML = storageDrawerHtml(loc); }).catch(() => {});
}
function storageDrawerHtml(loc) {
  const idEnc = encodeId(loc.id), cls = STORAGE_STATE_CLS[loc.state] ?? "";
  const head = `<div class="obj-sub">${esc(loc.type_label)} · <span class="mono">${esc(loc.target)}</span> ${UI.pill(cls === "ok" ? "ok" : cls === "err" ? "err" : "", loc.state_label)}</div>`;
  let reason;
  if (loc.state === "connected") reason = UI.note("", `מחובר · ${loc.free_bytes != null ? `${ltr(fmtBytes(loc.free_bytes))} פנוי מתוך ${ltr(fmtBytes(loc.total_bytes))}` : "מקום פנוי לא נמדד"}${loc.state_since ? ` · מאז ${esc(fmtDate(loc.state_since))} ${esc(fmtClock(loc.state_since))}` : ""}`);
  else reason = UI.note(loc.state === "unreachable" ? "err" : "", `<span class="mono">${esc(loc.state_detail || "בלי פירוט")}</span><br>מאז ${esc(fmtDate(loc.state_since))} ${esc(fmtClock(loc.state_since))}${loc.state === "unreachable" ? " · המנטר מנסה שוב כל 60 שניות" : " · לא מנסים עד \"חבר\""}`);
  const imgs = IMAGES == null ? null : (IMAGES || []).filter((r) => r.location_id === loc.id);
  const affected = imgs == null ? `<div class="cap">${loc.images} אימג'ים · ${loc.unavailable_images} לא זמינים — טוען שמות…</div>`
    : imgs.length ? UI.datagrid({ columns: ["אימג'", "זמינות"], rows: imgs.map((r) => [UI.name(r.name, `${fmtBytes(r.total_compressed_bytes)} · נקלט ${fmtDate(r.created)}`), r.available === false ? UI.pill("err", "לא זמין") : UI.pill("ok", "זמין")]) })
    : `<div class="cap">אין אימג'ים על המיקום הזה.</div>`;
  const round = loc.state === "connected" ? "" : `<div><div class="sec">מה קורה בסבב</div>${UI.note("warn", `<b>סבב שיבחר באחד מהם מסרב להתחיל:</b> "האימג' לא זמין — <span class="mono">${esc(loc.name)}</span> לא נגיש מאז ${esc(fmtClock(loc.state_since))}". סבב שכבר רץ מהמיקום הזה — נכשל בגלוי בכל המשכפלים, לא נתקע בהמתנה.`)}</div>`;
  const kv = UI.kv([["נסה שוב", "גילוי + התחברות + עיגון עכשיו, במקום לחכות לניסיון הבא. הצלחה = \"מחובר\"."], ["נתק", "מסמן \"מנותק\": לא מנסים לבד עד \"חבר\". האימג'ים נשארים \"לא זמינים\"."], ["הסר", "מוריד את המיקום מהרשימה (רק מנותק, בלי אימג'ים). הדיסק ב-NAS לא נמחק ולא מפורמט. דורש הקלדת השם."]]);
  const buttons = [`<button class="btn primary" onclick="storageCheck('${idEnc}')">בדוק עכשיו</button>`];
  if (loc.removable) {
    if (loc.state === "disconnected") buttons.push(`<button class="btn primary" onclick="storageConnect('${idEnc}')">חבר</button>`, `<button class="btn danger" onclick="storageDelete('${idEnc}')">הסר…</button>`);
    else buttons.push(`<button class="btn" onclick="storageConnect('${idEnc}')">נסה שוב</button>`, `<button class="btn" onclick="storageDisconnect('${idEnc}')">נתק</button>`);
  }
  buttons.push(`<button class="btn flat" onclick="selectPageById('logs')">פתח ביומן</button>`);
  return `<div class="page drw">${head}<div><div class="sec">${loc.state === "connected" ? "המצב" : "הסיבה"}</div>${reason}</div><div><div class="sec">אימג'ים על המיקום (${loc.images}${loc.unavailable_images ? ` · ${loc.unavailable_images} לא זמינים` : ""})</div>${affected}${loc.unavailable_images ? `<div class="cap" style="margin-top:6px">הם רשומים בספרייה ולא נמחקו. כשהמיקום יחזור — יחזרו להיות זמינים בלי פעולה.</div>` : ""}</div>${round}${loc.removable ? `<div><div class="sec">ההבדל בין הכפתורים</div>${kv}</div>` : ""}<div style="display:flex;gap:6px;flex-wrap:wrap">${buttons.join("")}</div></div>`;
}
