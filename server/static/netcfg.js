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

/* NETCFG מוצהר ב-console.js — כאן רק ממלאים אותו. redeclaration של let
   בין סקריפטים קלאסיים מפילה את הקובץ ב-SyntaxError. */
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

/* ‏#761: הטבלה המאוחדת — שורה אחת לכל כרטיס פיזי, ולא שני פאנלים
   נפרדים. עמודות: התקן · MAC · מצב/מהירות · כתובת IP בפועל · DHCP
   בפועל · פעולות. הכתובת/שער/DNS שהוגדרו (configured) אינם מוצגים
   בטבלה — הם live-only כאן, ומוצגים רק בדיאלוג העריכה. */
function speedLabel(n) {
  const state = n.state === "up" ? "מחובר" : n.state === "down" ? "מנותק" : "לא ידוע";
  const speed = typeof n.speed_mbps === "number" && n.speed_mbps > 0
    ? ` · ${n.speed_mbps} Mbps` : "";
  return `${state}${speed}`;
}

function unifiedRow(n, cfgRow, admin) {
  const trunk = n.trunk ? ` <span class="tag warn">רשת המכללה</span>` : "";
  const missing = n.present ? "" : ` <span class="tag warn">לא קיים במערכת</span>`;
  const desc = n.description ? `<br><small style="color:var(--muted)">${esc(n.description)}</small>` : "";
  const live = (cfgRow ? cfgRow.live_addresses : n.addresses) || [];
  const gaps = cfgRow && cfgRow.mismatches && cfgRow.mismatches.length
    ? `<br><span class="tag warn">${esc(cfgRow.mismatches.join(" · "))}</span>` : "";
  const dhcp = nicMode(n);
  return `<tr>
    <td><b dir="ltr">${esc(n.name)}</b>${trunk}${missing}${desc}</td>
    <td class="mono" dir="ltr">${esc(n.mac) || "—"}</td>
    <td>${esc(speedLabel(n))}</td>
    <td class="mono" dir="ltr">${esc(live.join(" · ")) || "—"}${gaps}</td>
    <td>${dhcp.html}</td>
    <td>${admin ? `
      <button class="btn" data-net-edit="${esc(n.name)}">עריכת הגדרות</button>
      <button class="btn" data-nic-edit="${esc(n.name)}">DHCP</button>
      <button class="btn flat" data-nic-desc="${esc(n.name)}">תיאור</button>
      <button class="btn flat" data-net-preview="${esc(n.name)}">הקובץ</button>
      <button class="btn danger flat" data-nic-forget="${esc(n.name)}">שכחה</button>` : ""}
    </td></tr>`;
}

function renderNetTable() {
  if (!NETCFG) return;         // עוד לא נטען — Promise.all עדיין רץ.
  const admin = ME.role === "admin";
  const byName = new Map(NETCFG.interfaces.map((r) => [r.name, r]));
  const names = [...new Set([...NICS.map((n) => n.name), ...byName.keys()])].sort();
  const rows = names.map((name) => {
    const n = NICS.find((x) => x.name === name)
      || { name, mac: "", state: "unknown", addresses: [], present: byName.has(name),
           trunk: false, description: "", enabled: false, proxy: false,
           dhcp_live: { state: "unknown" }, dhcp_live_label: "לא ידוע",
           dhcp_diverged: false, speed_mbps: null };
    return unifiedRow(n, byName.get(name), admin);
  }).join("");
  const tbody = $("#net-table tbody");
  if (tbody) tbody.innerHTML = rows
    || `<tr><td colspan="6">לא נמצאו כרטיסי רשת.</td></tr>`;

  document.querySelectorAll("[data-net-edit]").forEach((b) => b.onclick = () =>
    editAddress(byName.get(b.dataset.netEdit)
      || bodyOf({ name: b.dataset.netEdit, mode: "manual", address: "", netmask: MASKS[0],
                  gateway: "", dns: [], routes: [] }, {})));
  document.querySelectorAll("[data-net-preview]").forEach((b) => b.onclick = () =>
    showFile(byName.get(b.dataset.netPreview)
      || { name: b.dataset.netPreview, mode: "manual", address: "", netmask: MASKS[0],
           gateway: "", dns: [], routes: [] }));
  wireNicActions(NICS);
  if (typeof refreshNetPages === "function") refreshNetPages();
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
      <button class="btn primary" id="netcfg-confirm" type="button" onclick="confirmNetRollback()">אשר שהחיבור עובד</button>
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
    // כשהזמן נגמר טוענים מחדש. כשל טעינה כאן אינו "הרענון הצליח" —
    // הבאנר "ממתין לאישור" תקוע ב-0 שניות ייראה כאילו הכל תקין. מודיעים
    // שלא הצלחנו לקרוא את המצב, ולא בולעים בשקט (#517, עיקרון 5).
    if (left === 0) {
      clearInterval(NETCFG_TICK);
      loadNetcfg()
        .then(() => { if (typeof renderNetTable === "function") renderNetTable(); })
        .catch((error) =>
          toast("לא הצלחנו לרענן את מצב הרשת אחרי ההחזרה: " + error.message));
    }
  }, 1000);
}

/* --- הטבלה ---------------------------------------------------------------- */

async function loadNetcfg() {
  NETCFG = await api("/net/config");
  const live = NETCFG.live;
  const foot = live.checked
    ? `נתיבים כרגע: ${live.routes.join(" · ") || "אין"} · ‏DNS: `
      + `${live.nameservers.join(", ") || "אין"}`
    : `המצב בפועל לא נקרא (${live.reason}) — אף שורה בטבלה אינה מאומתת`;
  const notSourced = NETCFG.sourced === false
    ? `<div class="sheet-note danger">‏/etc/network/interfaces אינו טוען את
       interfaces.d — כל מה שנכתב שם לא ייקרא באתחול.</div>` : "";

  // ‏#761: אין יותר פאנל/טבלה נפרדים לכתובת השרת — רק הבאנר החשוב (ההחזרה)
  // וההודעה על interfaces.d, מעל הטבלה המאוחדת (#net-table).
  const banner = $("#net-banner");
  if (banner) banner.innerHTML = rollbackBanner(NETCFG.rollback) + notSourced
    + `<p class="pad sub" dir="ltr">${esc(foot)}</p>`;
  renderRoutes();
  startCountdown();
}

async function confirmNetRollback() {
  try {
    await post("/net/config/confirm",
               { interface: NETCFG.rollback.interface });
    toast("אושר — ההגדרה נשארת");
  } catch (error) { toast(error.message); }
  await loadNetcfg();
  renderNetTable();
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
  renderNetTable();
}

/* ‏CIDR (‏"10.44.9.10/24") ↔ כתובת+מסכה. פונקציית עזר יחידה, כדי שהצד
   שמפרק וזה שמרכיב לא ייסחפו לשני מימושים (‏#761). */
function bitsToMask(bits) {
  const n = Math.max(0, Math.min(32, Number(bits) || 0));
  const full = 0xffffffff << (32 - n) >>> 0;
  return [24, 16, 8, 0].map((s) => (full >>> s) & 255).join(".");
}

function splitCidr(text) {
  const [address, bits] = String(text || "").trim().split("/");
  return { address: address || "", netmask: bits ? bitsToMask(bits) : MASKS[0] };
}

function joinCidr(address, netmask) {
  return address ? `${address}/${maskBits(netmask)}` : "";
}

function liveReadback(row) {
  const live = NETCFG && NETCFG.live;
  if (!live || !live.checked) {
    return `<div class="live-readback">בפועל כעת: <b>לא ידוע — קריאת מצב הרשת נכשלה</b></div>`;
  }
  const addr = (row.live_addresses || []).join(" · ");
  return `<div class="live-readback">בפועל כעת: <b>${esc(addr) || "אין כתובת"}</b></div>`;
}

function editAddress(row) {
  const gaps = (row.mismatches || []).length
    ? `<div class="sheet-note">מוגדר ≠ בפועל: ${esc(row.mismatches.join(" · "))}</div>` : "";
  sheet({
    title: `עריכת הגדרות — ${row.name}`,
    sub: "נכתב ל-/etc/network/interfaces.d, ולכן שורד אתחול.",
    danger: true,
    note: liveReadback(row) + gaps + `<div class="sheet-note danger">שינוי כתובת
      מנתק את מי שמחובר דרך הכרטיס הזה. אם זה הכרטיס שהקונסולה מגיעה
      דרכו, ההגדרה תוחזר אוטומטית תוך דקה אלא אם תאשרו שהחיבור עדיין חי.</div>`,
    fields: [
      { id: "mode", label: "", type: "radio", value: row.mode,
        options: [{ value: "dhcp", label: "קבלת כתובת אוטומטית (DHCP)" },
                  { value: "static", label: "כתובת סטטית" },
                  { value: "manual", label: "לא מנוהל מהקונסולה (ברירת מחדל)" }] },
      { id: "cidr", label: "כתובת / CIDR", dir: "ltr",
        value: joinCidr(row.address, row.netmask || MASKS[0]),
        placeholder: "10.44.9.10/24" },
      { id: "gateway", label: "שער ברירת מחדל (לא חובה)", value: row.gateway, dir: "ltr" },
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
      const parts = v.mode === "static" ? splitCidr(v.cidr) : { address: "", netmask: MASKS[0] };
      const body = bodyOf(row, { mode: v.mode, address: parts.address,
                                 netmask: parts.netmask, gateway: v.gateway,
                                 dns: v.dns.split(",").map((s) => s.trim()).filter(Boolean) });
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
  const mount = $("#netroutes-body");
  if (!mount || !NETCFG) return;
  const all = [];
  NETCFG.interfaces.forEach((nic) =>
    (nic.routes || []).forEach((r, index) => all.push({ nic, r, index })));
  const live = new Set(NETCFG.live.routes || []);
  mount.innerHTML = `
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

  document.querySelectorAll("[data-route-del]").forEach((b) => b.onclick = () =>
    routeDeleteSheet(b.dataset.routeDel, Number(b.dataset.routeIndex)));
}

/* ‏#954 גל 8: גם דף הרשת (console.js, כרטיס "נתיבים סטטיים") קורא לזה בשם. */
function routeDeleteSheet(nameEnc, index) {
  let name = nameEnc; try { name = decodeURIComponent(nameEnc); } catch (e) {}
  const nic = NETCFG.interfaces.find((n) => n.name === name);
  if (!nic || !nic.routes[index]) { toast("הנתיב לא נמצא"); return; }
  const gone = nic.routes.filter((_, i) => i !== index);
  sheet({
    title: "מחיקת נתיב סטטי",
    sub: `${nic.routes[index].destination} · ${nic.name}`,
    danger: true, submitLabel: "מחק",
    note: `<div class="sheet-note">הנתיב יוסר מהקובץ ומטבלת הניתוב.</div>`,
    verify: { label: `להמשך הקלד את שם הכרטיס: ${nic.name}`,
              mustEqual: nic.name },
    onSubmit: () => saveAddress(nic.name, bodyOf(nic, { routes: gone })),
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

const netrouteAdd = $("#netroute-add");
if (netrouteAdd) netrouteAdd.addEventListener("click", () => addRoute());
const netRefresh = $("#net-refresh");
if (netRefresh) netRefresh.addEventListener("click", () =>
  Promise.all([loadNet(), loadNetcfg()])
    .then(renderNetTable)
    .catch((error) => toast("רענון נכשל: " + error.message)));
