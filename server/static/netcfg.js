/* ImageCtl — לשונית הרשת, הצד השני של הכרטיס: איך השרת עצמו מחובר
   (‏#55–#57). ‏net.js מציג מה השרת *מחלק*; כאן הכתובת שלו, השער, ה-DNS
   והנתיבים הסטטיים — כולם ל-`interfaces.d`, כלומר ששורדים אתחול.

   שני דברים שהמסך הזה עושה אחרת מכל מסך אחר בקונסולה:

   1. **הנורה אינה ההגדרה.** לכל שורה יש "מה ביקשנו" ולידו "מה
      ‏`ip addr` מראה עכשיו". כשהשניים נבדלים — מה שנקרא הוא הנכון,
      והשורה אדומה. ‏"נשמר" על כתובת שלא זזה הוא בדיוק המסך שמפיל
      מפעיל (עיקרון 5).
   2. **ספירה לאחור אחרי שינוי מסוכן.** שינוי שיכול לנתק את הקונסולה
      מוחזר תוך דקה אם לא נאמר במפורש "אני עדיין רואה אותה" (‏#56).
      הכפתור הזה הוא הראיה החיובית; היעדר ניתוק אינו ראיה, כי אולי
      המפעיל פשוט לא הצליח להגיע כדי לומר זאת. */
"use strict";

let NETCFG = null;
let NETCFG_TICK = null;

const MASKS = ["255.255.255.0", "255.255.255.128", "255.255.254.0",
               "255.255.252.0", "255.255.0.0", "255.0.0.0", "255.255.255.252"];

/* שלושה מצבים, ולא שניים: לא מנוהל (אפור) · תואם (ירוק) · לא תואם
   (אדום). כרטיס שלא הצלחנו לקרוא עליו כלום נופל ל"לא תואם" בכוונה. */
function netLight(row) {
  if (!NETCFG.live.checked) return "bad";
  if (row.mode === "manual") return "off";
  return row.mismatches.length ? "bad" : "ok";
}

function netRow(row) {
  const wanted = row.mode === "static"
    ? `<span dir="ltr">${esc(row.address)}/${esc(row.netmask)}</span>`
    : esc(row.mode_he);
  const live = (row.live_addresses || []).join(" · ");
  const gaps = row.mismatches.length
    ? `<div class="sub danger-text">${esc(row.mismatches.join(" · "))}</div>` : "";
  const routes = row.routes.length
    ? ` · ${row.routes.length} נתיבים` : "";
  return `<tr>
    <td><span class="hlight ${netLight(row)}"></span></td>
    <td><b dir="ltr">${esc(row.name)}</b>${row.present ? ""
      : ` <span class="tag warn">לא נמצא במכונה</span>`}</td>
    <td>${wanted}${routes}</td>
    <td class="mono" dir="ltr">${esc(row.gateway) || "—"}</td>
    <td class="mono" dir="ltr">${esc((row.dns || []).join(", ")) || "—"}</td>
    <td class="mono" dir="ltr">${esc(live) || "—"}${gaps}</td>
    <td>
      <button class="btn" data-net-edit="${esc(row.name)}">הגדרת כתובת</button>
      <button class="btn" data-net-preview="${esc(row.name)}">הקובץ</button>
    </td></tr>`;
}

/* --- הבאנר של ההחזרה: הדבר היחיד שחשוב יותר מהטבלה ------------------------ */

function rollbackBanner(rb) {
  if (rb.corrupt) {
    return `<div class="sheet-note danger">סמן ההחזרה על הדיסק פגום ולא ניתן
      לפענוח. הוא לא יפעיל החזרה — בדקו את הרשת ידנית.</div>`;
  }
  if (rb.pending) {
    return `<div class="sheet-note danger" id="netcfg-pending">
      <b>ההגדרה של ${esc(rb.interface)} ממתינה לאישור.</b>
      אם לא תאשרו שהקונסולה עדיין נגישה, ההגדרה הקודמת תחזור בעוד
      <b id="netcfg-count">${rb.seconds_left}</b> שניות — גם אם השרת ייפול,
      וגם אם המכונה תאותחל.
      <button class="btn primary" id="netcfg-confirm">אשר שהחיבור עובד</button>
      </div>`;
  }
  if (!rb.armed) {
    return `<div class="sheet-note danger">ההחזרה האוטומטית
      (${esc(rb.unit)}) אינה פעילה: ${esc(rb.armed_detail)}. עד שתותקן,
      כל שינוי שיכול לנתק את הקונסולה ייחסם — ראו docs/server-install.md.</div>`;
  }
  return "";
}

function startCountdown() {
  clearInterval(NETCFG_TICK);
  const el = document.getElementById("netcfg-count");
  if (!el) return;
  NETCFG_TICK = setInterval(() => {
    const left = Math.max(0, Number(el.textContent) - 1);
    el.textContent = left;
    // כשהזמן נגמר טוענים מחדש: הזרוע כבר החזירה, והמסך חייב להראות
    // את מה שיש עכשיו ולא את מה שביקשנו.
    if (left === 0) { clearInterval(NETCFG_TICK); loadNetcfg().catch(() => {}); }
  }, 1000);
}

/* --- הטבלה ---------------------------------------------------------------- */

async function loadNetcfg() {
  NETCFG = await api("/net/config");
  const rows = NETCFG.interfaces.map(netRow).join("");
  const live = NETCFG.live;
  const foot = live.checked
    ? `נתיבים כרגע: ${live.routes.join(" · ") || "אין"} · ‏DNS: `
      + `${live.nameservers.join(", ") || "אין"}`
    : `המצב בפועל לא נקרא (${live.reason}) — אף שורה כאן אינה מאומתת`;
  const notSourced = NETCFG.sourced === false
    ? `<div class="sheet-note danger">‏/etc/network/interfaces אינו טוען את
       interfaces.d — כל מה שנכתב שם לא ייקרא באתחול.</div>` : "";

  $("#netcfg-body").innerHTML = rollbackBanner(NETCFG.rollback) + notSourced + `
    <table id="netcfg-table">
      <thead><tr><th></th><th>כרטיס</th><th>מה הוגדר</th><th>שער</th>
        <th>DNS</th><th>מה יש בפועל</th><th></th></tr></thead>
      <tbody>${rows || `<tr><td colspan="7">לא נמצאו כרטיסים.</td></tr>`}</tbody>
    </table>
    <p class="pad sub" dir="ltr">${esc(foot)}</p>`;
  renderRoutes();
  startCountdown();

  const confirmButton = document.getElementById("netcfg-confirm");
  if (confirmButton) confirmButton.onclick = async () => {
    try {
      await post("/net/config/confirm",
                 { interface: NETCFG.rollback.interface });
      toast("אושר — ההגדרה נשארת");
    } catch (error) { toast(error.message); }
    await loadNetcfg();
  };
  document.querySelectorAll("[data-net-edit]").forEach((b) => b.onclick = () =>
    editAddress(NETCFG.interfaces.find((n) => n.name === b.dataset.netEdit)));
  document.querySelectorAll("[data-net-preview]").forEach((b) => b.onclick = () =>
    showFile(NETCFG.interfaces.find((n) => n.name === b.dataset.netPreview)));
}

/* --- עריכת הכתובת --------------------------------------------------------- */

function bodyOf(row, over) {
  return { mode: row.mode, address: row.address, netmask: row.netmask,
           gateway: row.gateway, dns: row.dns, routes: row.routes,
           confirm: row.name, ...over };
}

async function saveAddress(name, body) {
  const result = await put(`/net/config/${encodeURIComponent(name)}`, body);
  if (result.apply_error) toast("ההחלה נכשלה: " + result.apply_error);
  else if (!result.verified)
    toast("נכתב — אבל המצב בפועל לא תואם: " + result.mismatches.join(" · "));
  else if (result.rollback.pending)
    toast("הוחל. אשרו תוך דקה שהקונסולה עדיין נגישה, אחרת יוחזר.");
  else toast("הוחל, ואומת מול ip addr");
  await loadNetcfg();
}

function editAddress(row) {
  sheet({
    title: `כתובת השרת על ${row.name}`,
    sub: "נכתב ל-/etc/network/interfaces.d, ולכן שורד אתחול.",
    danger: true,
    note: `<div class="sheet-note danger">שינוי כתובת מנתק את מי שמחובר
      דרך הכרטיס הזה. אם זה הכרטיס שהקונסולה מגיעה דרכו, ההגדרה תוחזר
      אוטומטית תוך דקה אלא אם תאשרו שהחיבור עדיין חי.</div>`,
    fields: [
      { id: "mode", label: "מצב", type: "select", value: row.mode,
        options: [{ value: "manual", label: "לא מנוהל מהקונסולה (ברירת מחדל)" },
                  { value: "static", label: "כתובת סטטית" },
                  { value: "dhcp", label: "לקוח DHCP" }] },
      { id: "address", label: "כתובת", value: row.address, dir: "ltr",
        placeholder: "10.44.9.10" },
      { id: "netmask", label: "מסכת רשת", type: "select",
        value: row.netmask || MASKS[0],
        options: MASKS.map((m) => ({ value: m, label: m })) },
      { id: "gateway", label: "שער (לא חובה)", value: row.gateway, dir: "ltr" },
      { id: "dns", label: "שרתי DNS (מופרדים בפסיק, לא חובה)",
        value: (row.dns || []).join(", "), dir: "ltr" },
    ],
    verify: { label: `לשמירה הקלד את שם הכרטיס: ${row.name}`,
              mustEqual: row.name },
    submitLabel: "החל",
    onSubmit: async (v) => {
      // תצוגה מקדימה לפני החלה: מי שרואה את הטקסט תופס טעות כשהיא
      // עדיין טקסט. ‏השרת מסרב שוב על אותן בעיות — זה לא מסך שמחליף
      // את הבדיקה, אלא שמראה אותה מוקדם.
      const body = bodyOf(row, { mode: v.mode, address: v.address,
                                 netmask: v.netmask, gateway: v.gateway,
                                 dns: v.dns.split(",").map((s) => s.trim()) });
      const preview = await post(
        `/net/config/${encodeURIComponent(row.name)}/preview`, body);
      if (preview.problems.length) throw new Error(preview.problems.join(" · "));
      await saveAddress(row.name, body);
    },
  });
}

async function showFile(row) {
  const preview = await post(
    `/net/config/${encodeURIComponent(row.name)}/preview`, bodyOf(row, {}));
  sheet({
    title: `הקובץ של ${row.name}`,
    sub: "מה שנכתב היום, ומה שייכתב בשמירה הבאה",
    note: `<p class="mono" dir="ltr" style="color:var(--muted);font-size:12px;margin:0 0 4px">${esc(preview.path)}</p>
           <pre class="conf">${esc(preview.before || "— אין קובץ —")}</pre>
           <p class="mono" dir="ltr" style="color:var(--muted);font-size:12px;margin:10px 0 4px">${esc(preview.resolv_path)}</p>
           <pre class="conf">${esc(preview.resolv_after)}</pre>`,
    submitLabel: "סגור", onSubmit: async () => {},
  });
}

/* --- נתיבים סטטיים (‏#57): רשימה, הוספה, ומחיקה לכל שורה ------------------ */

function renderRoutes() {
  const all = [];
  NETCFG.interfaces.forEach((nic) =>
    (nic.routes || []).forEach((r, index) => all.push({ nic, r, index })));
  const live = new Set(NETCFG.live.routes || []);
  $("#netroutes-body").innerHTML = `
    <table>
      <thead><tr><th></th><th>יעד</th><th>מסכה</th><th>שער</th><th>כרטיס</th>
        <th></th></tr></thead>
      <tbody>${all.map(({ nic, r, index }) => {
        // אותה ראיה חיובית: הנתיב נחשב קיים רק אם הוא בטבלת הניתוב.
        const seen = live.has(`${r.destination}/${maskBits(r.netmask)} via ${r.gateway}`);
        return `<tr>
          <td><span class="hlight ${NETCFG.live.checked ? (seen ? "ok" : "bad") : "bad"}"></span></td>
          <td class="mono" dir="ltr">${esc(r.destination)}</td>
          <td class="mono" dir="ltr">${esc(r.netmask)}</td>
          <td class="mono" dir="ltr">${esc(r.gateway)}</td>
          <td dir="ltr">${esc(nic.name)}</td>
          <td><button class="btn danger" data-route-del="${esc(nic.name)}"
                data-route-index="${index}">מחק</button></td></tr>`;
      }).join("") || `<tr><td colspan="6">אין נתיבים סטטיים.
        נתיב שנוסף כאן נשאר גם אחרי אתחול.</td></tr>`}</tbody>
    </table>`;

  document.querySelectorAll("[data-route-del]").forEach((b) => b.onclick = () => {
    const nic = NETCFG.interfaces.find((n) => n.name === b.dataset.routeDel);
    const gone = nic.routes.filter((_, i) => i !== Number(b.dataset.routeIndex));
    sheet({
      title: "מחיקת נתיב סטטי",
      sub: `${nic.routes[Number(b.dataset.routeIndex)].destination} · ${nic.name}`,
      danger: true, submitLabel: "מחק",
      note: `<div class="sheet-note">הנתיב יוסר מהקובץ ומטבלת הניתוב.</div>`,
      verify: { label: `להמשך הקלד את שם הכרטיס: ${nic.name}`,
                mustEqual: nic.name },
      onSubmit: () => saveAddress(nic.name, bodyOf(nic, { routes: gone })),
    });
  });
}

function maskBits(netmask) {
  return (netmask || "").split(".")
    .reduce((bits, part) => bits + ((Number(part) >>> 0).toString(2).match(/1/g) || []).length, 0);
}

function addRoute() {
  const usable = NETCFG.interfaces.filter((n) => n.mode === "static");
  if (!usable.length) {
    toast("נתיב סטטי דורש כרטיס עם כתובת סטטית. הגדירו כתובת קודם.");
    return;
  }
  sheet({
    title: "הוספת נתיב סטטי",
    sub: "נכתב לקובץ של הכרטיס, ולכן נשאר גם אחרי אתחול.",
    fields: [
      { id: "destination", label: "רשת היעד", dir: "ltr",
        placeholder: "10.20.0.0" },
      { id: "netmask", label: "מסכה", type: "select", value: MASKS[0],
        options: MASKS.map((m) => ({ value: m, label: m })) },
      { id: "gateway", label: "דרך מי (שער)", dir: "ltr",
        placeholder: "10.10.10.9" },
      { id: "name", label: "על איזה כרטיס", type: "select",
        value: usable[0].name,
        options: usable.map((n) => ({ value: n.name,
                                      label: `${n.name} · ${n.address}` })) },
    ],
    submitLabel: "הוסף",
    onSubmit: async (v) => {
      const nic = NETCFG.interfaces.find((n) => n.name === v.name);
      const body = bodyOf(nic, {
        routes: [...nic.routes, { destination: v.destination,
                                  netmask: v.netmask, gateway: v.gateway }],
      });
      const preview = await post(
        `/net/config/${encodeURIComponent(nic.name)}/preview`, body);
      if (preview.problems.length) throw new Error(preview.problems.join(" · "));
      await saveAddress(nic.name, body);
    },
  });
}

$("#netroute-add").addEventListener("click", () => addRoute());
$("#netcfg-refresh").addEventListener("click", () =>
  loadNetcfg().catch((error) => toast("רענון נכשל: " + error.message)));
