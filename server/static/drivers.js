/* ImageCtl — דף "דרייברים" (#720, #959; ‏#954 גל 6 לפי
   docs/design/console-redesign/drivers.md): ספריית חבילות הדרייברים כטבלה
   אחת — שם, כלל ההתאמה (PCI / דגם / pci_any), קבצים, "מתאים ל-" בשמות —
   ולשונית "כיסוי לפי מכונה" (בנייה/שיכפול; כיתות v2) שהופכת את הטבלה: אילו
   חבילות יונחו על כל מכונה שדיווחה חומרה, ואילו בקרי PCI נשארו בלי חבילה.
   ייבוא tar (admin; השרת מאמת sha256 של כל קובץ לפני הכניסה, tools/drivers/
   build-package.py בונה אותו) ומחיקה מאחורי הקלדת שם. משתמש ב-UI/api/post/
   sheet/toast/esc מ-console.js; המכונות מ-/machines (loadMachines). */
"use strict";

let DRIVERS = null, driversError = "";

async function loadDrivers() {
  try {
    if (!MACHINES || !GROUPS) await loadMachines();   // שמות, תפקידים והמלאי (inventory) לכיסוי
    DRIVERS = await api("/drivers");
    driversError = "";
  } catch (e) {
    DRIVERS = null;   // לא נקרא ≠ ספרייה ריקה (עיקרון 5)
    driversError = e.message;
    toast("טעינת הדרייברים נכשלה: " + e.message);
  }
  if (current === "drivers") renderCurrent();
}

function driverRuleLabel(rule) {
  if (rule.pci) return `PCI ${rule.pci.join(" + ")}`;
  if (rule.pci_any) {
    // חבילת דגם (#959): מאות מזהים — מציגים כמה, ואת המספר המלא.
    const shown = rule.pci_any.slice(0, 3).join(" | ");
    return `PCI אחד מ-${rule.pci_any.length}: ${shown}${rule.pci_any.length > 3 ? " …" : ""}`;
  }
  return `${rule.vendor} · ${rule.model}`;
}
/* הכלל כ-HTML: הטקסט העברי בחוץ, המזהים ב-LTR מבודד — אחרת "אחד מ-4:" מתהפך בתוך .mono. */
function driverRuleHtml(rule) {
  const ids = (list) => `<span class="mono">${esc(list.join(" | "))}</span>`;
  if (rule.pci) return `PCI ${ids(rule.pci)}`;
  if (rule.pci_any) return `PCI אחד מ-${rule.pci_any.length}: ${ids(rule.pci_any.slice(0, 3))}${rule.pci_any.length > 3 ? " …" : ""}`;
  return `דגם: <span dir="auto">${esc(rule.vendor)}</span> · <span dir="auto">${esc(rule.model)}</span>`;
}
function driverMachineName(mac) { const m = findMachine(mac); return m ? machineName(m) || mac : mac; }
function driverMatchesCell(pkg) {
  const macs = pkg.matches || [];
  if (!macs.length) return `<span class="muted">אף מכונה שדיווחה</span>`;
  const names = macs.map(driverMachineName);
  const shown = names.slice(0, 4).join(", ") + (names.length > 4 ? ` ועוד ${names.length - 4}` : "");
  return `${macs.length} ${macs.length === 1 ? "מכונה" : "מכונות"} · <span class="cap" title="${esc(names.join(", "))}">${esc(shown)}</span>`;
}
function driverRowHtml(p, admin) {
  const enc = encodeId(p.name);
  const acts = `<div class="acts"><button class="btn sm" onclick="openDriverDetail('${enc}')">פרטים</button>${admin ? `<button class="btn sm danger" onclick="deleteDriver('${enc}')">מחיקה</button>` : ""}</div>`;
  return { attrs: `data-pkg="${esc(p.name)}"`, cells: [
    UI.name(p.name, p.description || ""),
    (p.match || []).map((r) => `<span style="display:block">${driverRuleHtml(r)}</span>`).join("") || `<span class="muted">—</span>`,
    `<span class="mono">${(p.files || []).length}</span>`,
    driverMatchesCell(p),
    UI.status("ok", "sha256 בייבוא"),   // השרת מאמת כל קובץ לפני שהחבילה נכנסת (import_tar) — חבילה בספרייה = חבילה שאומתה
    acts] };
}

/* ---------- כיסוי לפי מכונה: חישוב בלקוח, אותם כללים כמו drivers.match ----------
   מזהה עם class (3 חלקים) = זהות מלאה; בלי class = אותו vendor:device בכל class.
   כלל pci תואם כש**כל** מזהיו במלאי, pci_any כש**אחד**; כלל דגם — vendor ו-model
   מול sys_vendor / product_name / product_version (בלי רישיות ורווחי קצה).
   "בלי כיסוי" = בקר PCI שאף כלל PCI של חבילה תואמת לא מכסה; חבילת דגם מכסה
   לפי דגם, לא לפי בקר — ולכן היא נספרת אך אינה מסירה בקר מ"בלי כיסוי". */
function driverIdPresent(pci, have) {
  const p = String(pci).toLowerCase();
  return p.split(":").length === 3 ? have.includes(p) : have.some((h) => h.startsWith(p + ":"));
}
function driverNorm(v) { return String(v == null ? "" : v).trim().toLowerCase(); }
function driverRuleHits(rule, inv) {
  const have = (inv.pci || []).map((x) => String(x).toLowerCase());
  if (rule.pci) return rule.pci.every((p) => driverIdPresent(p, have)) ? { by: "pci", ids: rule.pci } : null;
  if (rule.pci_any) { const ids = rule.pci_any.filter((p) => driverIdPresent(p, have)); return ids.length ? { by: "pci", ids } : null; }
  const dmi = inv.dmi || {};
  if (driverNorm(rule.vendor) !== driverNorm(dmi.sys_vendor)) return null;
  const model = driverNorm(rule.model);
  return model && [driverNorm(dmi.product_name), driverNorm(dmi.product_version)].includes(model) ? { by: "model", ids: [] } : null;
}
function driverCoverage(m) {
  const inv = m.inventory || {}, have = (inv.pci || []).map((x) => String(x).toLowerCase());
  const covered = new Set(), pkgs = [];
  for (const p of DRIVERS || []) {
    const hits = (p.match || []).map((r) => driverRuleHits(r, inv)).filter(Boolean);
    if (!hits.length) continue;
    const by = hits.some((h) => h.by === "pci") ? "pci" : "model";
    pkgs.push({ name: p.name, by });
    for (const h of hits) for (const id of h.ids) for (const x of have) if (driverIdPresent(id, [x])) covered.add(x);
  }
  return { pkgs, uncovered: have.filter((x) => !covered.has(x)) };
}
function driverCoverageRow(m) {
  const macEnc = encodeId(m.mac), inv = m.inventory, dmi = (inv && inv.dmi) || {};
  const model = [dmi.sys_vendor, dmi.product_version || dmi.product_name].filter(Boolean).join(" ");
  const name = UI.nameHtml(machineName(m) || m.mac, `${esc(ROLE_HE[machineRole(m)] || "")}${model ? " · " + esc(model) : ""}`);
  if (inv == null) return { attrs: `data-mac="${esc(m.mac)}"`, cells: [name, `<span class="muted">לא דיווחה חומרה (סוכן ישן, או מעולם לא עלתה)</span>`, `<span class="muted">—</span>`, UI.status("unk", "לא נבדק"), UI.acts([["פרטים", `openMachineDetail('${macEnc}')`]])] };
  const { pkgs, uncovered } = driverCoverage(m);
  const pci = (inv.pci || []).length ? (inv.pci || []).map((x) => `<span class="mono" style="display:block">${esc(x)}</span>`).join("") : `<span class="muted">לא דיווחה בקרי PCI</span>`;
  const staged = pkgs.length
    ? pkgs.map((p) => `<span style="display:block">${esc(p.name)} <span class="cap">${p.by === "pci" ? "לפי PCI" : "לפי דגם"}</span></span>`).join("")
    : `<span class="muted">אין חבילה מתאימה</span>`;
  const state = !(inv.pci || []).length ? UI.status("unk", "אין מה לבדוק")
    : !uncovered.length ? UI.status("ok", "כל הבקרים מכוסים")
    : pkgs.length && pkgs.every((p) => p.by === "model") ? UI.status("warn", `חבילת דגם בלבד — ${uncovered.length} בקרים לא אומתו לפי PCI`)
    : UI.status("warn", `${uncovered.length} ${uncovered.length === 1 ? "בקר" : "בקרים"} בלי חבילה`) + `<span class="sub">${uncovered.map((x) => `<span class="mono">${esc(x)}</span>`).join(" · ")}</span>`;
  return { attrs: `data-mac="${esc(m.mac)}"`, cells: [name, pci, staged, state, UI.acts([["פרטים", `openMachineDetail('${macEnc}')`]])] };
}
function driversCoverageCard() {
  const v1 = (MACHINES || []).filter((m) => ["build", "cloner"].includes(machineRole(m)));
  const reported = v1.filter((m) => m.inventory != null);
  const rows = sortMachinesBySuffix(v1).map(driverCoverageRow);
  const table = MACHINES == null
    ? UI.note("err", "רשימת המחשבים לא נקראה — אין מה לחשב.")
    : UI.datagrid({ cls: "acts-on", columns: ["מכונה", "בקרי PCI (רשת/אחסון)", "חבילות שיונחו", "כיסוי", ""], rows, empty: "אין מחשבי בנייה או שיכפול רשומים" });
  const classroom = (MACHINES || []).filter((m) => machineRole(m) === "classroom").length;
  return UI.card({ title: "כיסוי לפי מכונה", small: `${reported.length} מתוך ${v1.length} דיווחו חומרה · חושב בקונסולה לפי אותם כללים של השרת`, cls: "c12", body: table, flush: !!MACHINES && rows.length > 0 })
    + `<div class="c12">${UI.note("info", `"הונח" אינו "הותקן": בקר אחסון שבלעדיו Windows לא עולה אינו מכוסה (v2). חבילת דגם מכסה לפי דגם ולא לפי בקר — ולכן בקר שרק היא מכסה מוצג כ"לא אומת לפי PCI".${classroom ? ` תחנות כיתה (${classroom}) — v2, לא מוצגות.` : ""}`)}</div>`;
}

function driversPage(tab = 0) {
  if (DRIVERS === null && !driversError) return pagePlaceholder();
  const admin = isAdmin(), list = DRIVERS || [];
  const reported = (MACHINES || []).filter((m) => m.inventory != null).length;
  const sub = DRIVERS
    ? [`${list.length} ${list.length === 1 ? "חבילה" : "חבילות"}`, "מונחות על הדיסק אחרי שחזור Windows, מותקנות בעלייה הראשונה", MACHINES ? `${reported} מכונות דיווחו חומרה` : "מכונות: לא נקרא"].map(esc).join(" · ")
    : `‏/drivers לא נקרא: ${esc(driversError)}`;
  const actions = (admin ? `<button class="btn primary" onclick="openDriverImport()">ייבוא חבילה (tar)</button>` : "") + `<button class="btn" onclick="loadDrivers()">רענון</button>`;
  const header = UI.objHeader({ crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "תשתית" }, { label: "דרייברים" }],
    icon: "settings", name: "דרייברים", sub, pill: DRIVERS ? "" : UI.pill("err", "לא נקרא"), actions, tabs: ["חבילות", "כיסוי לפי מכונה"], tab });
  let body;
  if (!DRIVERS) body = UI.note("err", `לא הצלחתי לקרוא את רשימת הדרייברים: ${esc(driversError)}`);
  else if (tab === 1) body = driversCoverageCard();
  else {
    const table = UI.datagrid({ cls: "acts-on", columns: ["חבילה", "התאמה", "קבצים", "מתאים ל-", "אומת", ""], rows: list.map((p) => driverRowHtml(p, admin)),
      empty: "אין חבילות דרייברים — ייבוא קובץ tar שנבנה ב-tools/drivers/build-package.py (manifest.json + sha256 לכל קובץ; docs/driver-packages.md)" });
    body = `<div class="c12 card"><div class="card-b${list.length ? " flush" : ""}">${table}${!list.length && admin ? `<div style="text-align:center;padding-top:10px"><button class="btn primary" onclick="openDriverImport()">ייבוא חבילה (tar)</button></div>` : ""}</div></div>`
      + `<div class="c12">${UI.note("info", `"הונח" אינו "הותקן": הסוכן מניח את החבילות התואמות ב-<span dir="ltr">\\ImageCtl\\Drivers</span> ורושם אותן ב-DevicePath — Windows סורק אותן בעלייה הראשונה. בקר אחסון שבלעדיו Windows לא עולה אינו מכוסה (v2). הלשונית "כיסוי לפי מכונה" הופכת את הטבלה — לכל מכונה, אילו חבילות יונחו ואילו בקרים בלי כיסוי.`)}</div>`;
  }
  return `<div class="page">${header}<div class="body">${body}</div></div>`;
}

function findDriver(name) {
  try { name = decodeURIComponent(name); } catch (e) {}
  return (DRIVERS || []).find((p) => p.name === name) || null;
}
function openDriverDetail(name) {
  const p = findDriver(name);
  if (!p) { toast("חבילה לא נמצאה"); return; }
  const files = (p.files || []).map((f) => `<span style="display:block"><span class="mono">${esc(f.path)}</span> <span class="cap mono">${esc(String(f.sha256 || "").slice(0, 12))}…</span></span>`).join("") || `<span class="muted">—</span>`;
  const macs = p.matches || [];
  openDrawer(`חבילה — ${p.name}`, `<div class="page drw">${UI.kv([
    ["תיאור", esc(p.description || "") || `<span class="muted">—</span>`],
    ["כללי התאמה", (p.match || []).map((r) => `<span style="display:block">${driverRuleHtml(r)}</span>`).join("") || `<span class="muted">—</span>`],
    ["מתאים ל-", macs.length ? esc(macs.map(driverMachineName).join(", ")) : `<span class="muted">אף מכונה שדיווחה</span>`],
    ["אומת", UI.status("ok", "sha256 של כל קובץ נבדק בייבוא")],
    [`קבצים (${(p.files || []).length})`, files]])}</div>`);
}

function openDriverImport() {
  if (!isAdmin()) { toast("אין הרשאה"); return; }
  openModalContent(
    "ייבוא חבילת דרייברים",
    `<div class="form"><div class="field full"><label for="driverFile">קובץ tar</label><input type="file" id="driverFile" accept=".tar,application/x-tar"></div><div class="field full"><div class="form-note">חבילה שנבנתה ב-<span dir="ltr" class="mono">tools/drivers/build-package.py</span> (‏<span dir="ltr" class="mono">docs/driver-packages.md</span>): תיקייה אחת עם manifest.json והקבצים. כל קובץ מאומת מול ה-sha256 שבמניפסט; חבילה עם קובץ חסר, עודף או שונה לא תיכנס.</div></div></div>`,
    "ייבוא",
    startDriverUpload
  );
}

function startDriverUpload() {
  const input = document.getElementById("driverFile");
  const file = input && input.files && input.files[0];
  if (!file) { toast("בחר קובץ לפני הייבוא"); return; }
  const body = document.getElementById("modalBody");
  const ok = document.getElementById("modalOk");
  if (ok) ok.disabled = true;
  body.innerHTML = `<div class="notice">מעלה ומאמת את "${esc(file.name)}"…</div>`;
  const request = new XMLHttpRequest();
  request.open("POST", "/api/console/drivers/upload");
  request.setRequestHeader("Content-Type", "application/x-tar");
  request.onload = () => {
    if (request.status === 200) {
      let name = file.name;
      try { name = JSON.parse(request.responseText).name || name; } catch (e) {}
      closeModal();
      toast(`החבילה "${name}" נקלטה — sha256 אומת לכל קובץ.`);
      loadDrivers();
    } else {
      let message = "שגיאה " + request.status;
      try { message = JSON.parse(request.responseText).detail || message; } catch (e) {}
      body.innerHTML = `<div class="notice err">הייבוא נדחה: ${esc(message)}</div>`;
      if (ok) { ok.disabled = false; ok.textContent = "סגור"; ok.onclick = closeModal; }
    }
  };
  request.onerror = () => {
    body.innerHTML = `<div class="notice err">הייבוא נכשל — החיבור נותק.</div>`;
    if (ok) { ok.disabled = false; ok.textContent = "סגור"; ok.onclick = closeModal; }
  };
  request.send(file);
}

function deleteDriver(name) {
  const p = findDriver(name);
  if (!isAdmin() || !p) return;
  sheet({
    title: "מחיקת חבילת דרייברים",
    sub: `החבילה "${p.name}" תימחק מהספרייה. מכונות שכבר קיבלו אותה אינן מושפעות.`,
    danger: true, submitLabel: "מחק",
    verify: { label: "להמשך יש להקליד את שם החבילה:", mustEqual: p.name },
    onSubmit: async () => {
      await post("/drivers/" + encodeURIComponent(p.name) + "/delete", { confirm_name: p.name });
      toast("החבילה נמחקה.");
      await loadDrivers();
    },
  });
}
