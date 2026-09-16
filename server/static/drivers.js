/* ImageCtl — דף "דרייברים" (#720): ספריית חבילות הדרייברים.
   רשימה עם "מתאים ל-N מכונות" (לפי המלאי האחרון שכל מכונה רשומה דיווחה
   ב-hello), ייבוא tar (admin; השרת מאמת sha256 של כל קובץ לפני הכניסה),
   ומחיקה מאחורי הקלדת שם. משתמש ב-api/post/sheet/toast/esc מ-console.js. */
"use strict";

let DRIVERS = null;

async function loadDrivers() {
  try {
    DRIVERS = await api("/drivers");
  } catch (e) {
    DRIVERS = null;
    toast("טעינת הדרייברים נכשלה: " + e.message);
  }
  if (current === "drivers") renderCurrent();
}

function driverRuleLabel(rule) {
  if (rule.pci) return `PCI ${rule.pci.join(" + ")}`;
  return `${rule.vendor} · ${rule.model}`;
}

function driverMatchesLabel(pkg) {
  const macs = pkg.matches || [];
  if (!macs.length) return `<span class="muted">לא מתאים לאף מכונה שדיווחה</span>`;
  const names = macs.map((mac) => { const m = findMachine(mac); return m ? machineName(m) || mac : mac; });
  return `<span title="${esc(names.join(", "))}">מתאים ל-${macs.length} מכונות</span>`;
}

function driversPage() {
  const admin = isAdmin();
  if (DRIVERS === null) {
    return `<div class="card"><div class="card-b"><div class="notice">רשימת הדרייברים לא נקראה.</div></div></div>`;
  }
  const rows = DRIVERS.map((p) => `<tr>
      <td><b>${esc(p.name)}</b>${p.description ? `<div class="sub">${esc(p.description)}</div>` : ""}</td>
      <td>${(p.match || []).map((r) => `<div class="mono" dir="ltr">${esc(driverRuleLabel(r))}</div>`).join("")}</td>
      <td>${(p.files || []).length}</td>
      <td>${driverMatchesLabel(p)}</td>
      <td>${admin ? `<button class="btn danger" onclick="deleteDriver('${esc(p.name)}')">מחיקה</button>` : ""}</td>
    </tr>`).join("");
  const table = DRIVERS.length
    ? `<table class="table"><thead><tr><th>חבילה</th><th>התאמה</th><th>קבצים</th><th>מכונות</th><th></th></tr></thead><tbody>${rows}</tbody></table>`
    : `<div class="empty">אין חבילות דרייברים. ייבוא: קובץ tar עם manifest.json (שם, כללי התאמה לפי PCI או דגם, ו-sha256 לכל קובץ).</div>`;
  return `<div class="grid"><div class="span-12"><div class="card">
    <div class="card-h"><span>חבילות דרייברים</span><div>${admin ? `<button class="btn primary" onclick="openDriverImport()">ייבוא חבילה</button>` : ""}</div></div>
    <div class="card-b">
      <p class="sub">אחרי שחזור של אימג׳ Windows, הסוכן מניח את החבילות התואמות לחומרת המכונה ב-<span class="mono" dir="ltr">\\ImageCtl\\Drivers</span> ורושם אותן ב-DevicePath — Windows סורק אותן בעלייה הראשונה. "הונח" אינו "הותקן"; בקר אחסון שבלעדיו Windows לא עולה אינו מכוסה (v2).</p>
      <div class="table-wrap">${table}</div>
    </div></div></div></div>`;
}

function openDriverImport() {
  if (!isAdmin()) { toast("אין הרשאה"); return; }
  openModalContent(
    "ייבוא חבילת דרייברים",
    `<div class="form"><div class="field full"><label>קובץ tar</label><input type="file" id="driverFile" accept=".tar,application/x-tar"></div><div class="field full"><div class="form-note">תיקייה אחת עם manifest.json והקבצים. כל קובץ מאומת מול ה-sha256 שבמניפסט; חבילה עם קובץ חסר, עודף או שונה לא תיכנס.</div></div></div>`,
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
      toast(`החבילה "${name}" נקלטה.`);
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
  if (!isAdmin()) return;
  sheet({
    title: "מחיקת חבילת דרייברים",
    sub: `החבילה "${name}" תימחק מהספרייה. מכונות שכבר קיבלו אותה אינן מושפעות.`,
    danger: true, submitLabel: "מחק",
    verify: { label: "להמשך יש להקליד את שם החבילה:", mustEqual: name },
    onSubmit: async () => {
      await post("/drivers/" + encodeURIComponent(name) + "/delete", { confirm_name: name });
      toast("החבילה נמחקה.");
      await loadDrivers();
    },
  });
}
