/* ImageCtl — לשונית הרשת (אפיון סעיף 24): מה השרת ראה בפועל, ומה השרת
   מחלק — DHCP לכל כרטיס רשת, כבוי כברירת מחדל, מאחורי הקלדת שם הכרטיס. */
"use strict";

// --- כרטיסי רשת ו-DHCP -------------------------------------------------------

/* מצב proxy: מבודד לאינסטנס dnsmasq משלו (imagectl-proxy) אחרי #36 —
   ‏dnsmasq 2.91 קופא על בקשת PXE לפורט 4011, ובאינסטנס משותף הקפיאה
   הזו לוקחת איתה את ה-DHCP של וילן ההפצה. הבידוד מציל את ההפצה; הוא
   לא מתקן את ה-proxy עצמו, ולכן ההדלקה עצמה דורשת אישור מפורש נוסף.

   הטקסט מגיע מהשרת (`/net/proxy-support`) ולא נכתב כאן: זה בדיוק
   ההסבר שה-API יסרב בו, ולפי הגרסה שבאמת מותקנת על המכונה — לא לפי
   מחרוזת קבועה שתשקר ביום שבו dnsmasq יעודכן. */
let PROXY_SUPPORT = null;
//: כרטיסי הרשת האחרונים שנקראו (‏/net/interfaces), לצריכת renderNetTable
// ב-netcfg.js ולפעולות ה-DHCP בטבלה המאוחדת (‏#761).
let NICS = [];

/* ‏#762: תווית מצב DHCP חי — ‏dhcp_live_label מגיע מהשרת (אמת נקראת, לא
   ניחוש). ‏dhcp_diverged מסמן פער בין מה שנשמר לבין מה שבאמת רץ. */
function dhcpLiveClass(state) {
  if (state === "serving") return "ok";
  if (state === "configured_not_running") return "warn";
  if (state === "off") return "off";
  return "warn";
}

function nicMode(n) {
  const cls = dhcpLiveClass(n.dhcp_live.state);
  const proxyTag = n.proxy && !(PROXY_SUPPORT && PROXY_SUPPORT.verified)
    ? ` <span class="tag warn">${esc(
        PROXY_SUPPORT && PROXY_SUPPORT.version
          ? `dnsmasq ${PROXY_SUPPORT.version} — לא נבדק` : "גרסת dnsmasq לא נקראה")}</span>`
    : "";
  const diverged = n.dhcp_diverged
    ? `<br><span class="tag warn">המוגדר אינו תואם למצב הפעיל</span>` : "";
  const stored = `שמורה בקונסולה: ${n.enabled ? "מופעל" : n.proxy ? "proxy" : "כבוי"}`;
  return {
    html: `<b class="dhcp-${cls}">${esc(n.dhcp_live_label)}</b>${proxyTag}
      <br><small style="color:var(--muted)">${esc(stored)}</small>${diverged}`,
    on: n.enabled || n.proxy,
  };
}

function editNic(nic) {
  const guess = (nic.addresses[0] || "").split("/")[0];
  const active = nic.enabled || nic.proxy;
  // כשהגרסה המותקנת נבדקה במעבדה ועבדה — אין מה לאשר, והשדה נעלם מעצמו.
  const gated = !(PROXY_SUPPORT && PROXY_SUPPORT.verified);
  sheet({
    title: `DHCP על ${nic.name}`,
    sub: "כתובות יחולקו רק על הכרטיס הזה. לרשת שיש בה DHCP קיים — בחר proxy.",
    note: (nic.trunk ? `<div class="sheet-note danger">הכרטיס מסומן כמחובר לרשת המכללה. DHCP מלא כאן ישבית את הרשת — אלא אם אתה בטוח לגמרי, השאר proxy.</div>`
      : `<div class="sheet-note">ההגדרה המסוכנת ביותר במערכת: DHCP על רשת שכבר יש בה שרת משבית אותה. השרת יבדוק לפני ההדלקה אם מישהו כבר עונה.</div>`)
      // ‏#1088: אחרי התקנה נקייה ההדלקה הראשונה היא גם הגדרת רשת ההפצה —
      // השרת משלים את ההתקנה ומאתחל את עצמו; הכרטיס חייב לשאת את הכתובת.
      + (typeof NET_DEPLOY !== "undefined" && NET_DEPLOY && NET_DEPLOY.configured === false && !active
        ? `<div class="sheet-note">רשת ההפצה מוגדרת כאן <b>בפעם הראשונה</b>: ההדלקה משלימה את ההתקנה (grub.cfg, dnsmasq, חומת אש) ומאתחלת את השרת על כתובת ההפצה — הקונסולה תחזור תוך כמה שניות. הכרטיס חייב כבר לשאת את "כתובת השרת בוילן" (עריכת כתובת → סטטית), אחרת השרת יסרב.</div>`
        : "")
      + `<div class="sheet-note danger" id="proxy-warn" hidden>${
           esc((PROXY_SUPPORT && PROXY_SUPPORT.reason) || "")}</div>`,
    fields: [
      { id: "mode", label: "מצב", type: "select",
        value: nic.enabled ? "dhcp" : nic.proxy ? "proxy" : "dhcp",
        options: [{ value: "dhcp", label: "DHCP מלא — מחלק כתובות (וילן הפצה)" },
                  { value: "proxy", label: "proxy — עונה על PXE בלבד, DHCP קיים נשאר" },
                  ...(active ? [{ value: "off", label: "כבוי — הכרטיס מפסיק לענות" }] : [])] },
      { id: "server_ip", label: "כתובת השרת בוילן", value: nic.server_ip || guess, dir: "ltr", placeholder: "10.44.9.10" },
      { id: "range_start", label: "תחילת טווח הכתובות", value: nic.range_start, dir: "ltr", placeholder: "10.44.9.50" },
      { id: "range_end", label: "סוף הטווח", value: nic.range_end, dir: "ltr", placeholder: "10.44.9.200" },
      { id: "netmask", label: "מסכת רשת", value: nic.netmask || "255.255.255.0", dir: "ltr" },
      { id: "gateway", label: "שער (לא חובה)", value: nic.gateway, dir: "ltr" },
      { id: "dns", label: "שרתי DNS (מופרדים בפסיק, לא חובה)", value: (nic.dns || []).join(", "), dir: "ltr" },
      { id: "lease", label: "זמן חכירה", value: nic.lease || "12h", dir: "ltr" },
      { id: "trunk", label: "הכרטיס הזה מחובר ל-trunk של רשת המכללה", type: "checkbox", value: nic.trunk },
      ...(nic.trunk ? [{ id: "confirm_trunk", label: "אני מבין שזו רשת המכללה ורוצה להדליק בכל זאת", type: "checkbox", value: false }] : []),
      ...(gated ? [{ id: "confirm_proxy_broken", type: "checkbox", value: false,
                     label: "אני מבין שמצב proxy עלול להקפיא את dnsmasq ורוצה להדליק בכל זאת" }] : []),
    ],
    verify: { label: `${active ? "לשמירה" : "להדלקה"} הקלד את שם הכרטיס: ${nic.name}`,
              mustEqual: nic.name },
    submitLabel: active ? "שמור" : "הדלק",
    danger: true,
    onSubmit: async (v) => {
      // המסך חוסם כאן רק כדי לומר את זה בעברית מיד; הסירוב האמיתי
      // הוא של השרת, וגם מי שיעקוף את המסך יפגוש אותו.
      if (v.mode === "proxy" && gated && v.confirm_proxy_broken !== true) {
        throw new Error("מצב proxy דורש את סימון האישור — קרא את האזהרה שמעל.");
      }
      await saveNic(nic.name, {
        enabled: v.mode === "dhcp", proxy: v.mode === "proxy",
        server_ip: v.server_ip, range_start: v.range_start, range_end: v.range_end,
        netmask: v.netmask, gateway: v.gateway, dns: v.dns, lease: v.lease,
        trunk: v.trunk, confirm: nic.name, confirm_trunk: v.confirm_trunk === true,
        confirm_proxy_broken: v.confirm_proxy_broken === true,
      });
      await loadNet();
      if (typeof refreshNetPages === "function") refreshNetPages();
    },
  });
  // האזהרה והאישור נדלקים רק כשבוחרים proxy. ‏sheet() בונה את ה-DOM
  // סינכרונית, אז השדות כבר קיימים כאן — אין צורך בקריאה חוזרת לקונסולה.
  const modeField = document.getElementById("sf-mode");
  const warning = document.getElementById("proxy-warn");
  const ack = gated ? document.getElementById("sf-confirm_proxy_broken") : null;
  const syncWarning = () => {
    const proxy = modeField.value === "proxy";
    warning.hidden = !proxy;
    if (ack) {
      // ‏style ולא התכונה hidden: ‏`label.check{display:flex}` גובר עליה.
      ack.closest("label").style.display = proxy ? "" : "none";
      if (!proxy) ack.checked = false;
    }
  };
  modeField.addEventListener("change", syncWarning);
  syncWarning();
}

async function saveNic(name, body) {
  const result = await put(`/net/interfaces/${encodeURIComponent(name)}`, body);
  if (result.apply_error) toast("נשמר, אבל dnsmasq לא עודכן: " + result.apply_error);
  else toast("הגדרת DHCP עודכנה");
  // ‏#1088: ההדלקה הראשונה הגדירה את רשת ההפצה — מה הושלם, ומה לא.
  const d = result.deploy;
  if (d) {
    if (d.ok) toast(`רשת ההפצה הוגדרה על ${name} (${d.url}) — השרת מתאתחל, הקונסולה תחזור תוך כמה שניות`);
    else toast(`רשת ההפצה נרשמה על ${name}, אבל חלק מההשלמה נכשל: ${(d.errors || []).join(" · ")}${d.restarting ? " · השרת מתאתחל" : " · השרת לא אותחל — אתחלו ידנית"}`);
    if (typeof NET_DEPLOY !== "undefined") NET_DEPLOY = { configured: true, source: "console", interface: name, url: d.url, hint: null };
  }
}

/* --- בריאות המערכת — רמזור לכל בדיקה (health.py) ------------------------- */
/* השם loadHealthPanel כדי לא לדרוס את loadHealth של console.js. */

async function loadHealthPanel() {
  const list = $("#health-list");
  if (!list) return;
  const checks = await api("/health");
  list.innerHTML = checks.map((c) => `
    <div class="health-row">
      <span class="hlight ${c.state}"></span>
      <b>${esc(c.label)}</b>
      <span class="sub">${esc(c.detail)}</span>
    </div>`).join("");
}

const healthRefresh = $("#health-refresh");
if (healthRefresh) healthRefresh.addEventListener("click", () =>
  loadHealthPanel().catch((error) => toast("רענון נכשל: " + error.message)));

async function previewDnsmasq() {
  // שני קבצים, כי ה-proxy רץ בתהליך dnsmasq משלו (#36).
  const conf = await api("/net/dnsmasq");
  sheet({
    title: "קבצי dnsmasq שנוצרים",
    sub: "האינסטנס הראשי, ולידו אינסטנס ה-proxy המבודד",
    note: `<p class="mono" dir="ltr" style="color:var(--muted);font-size:12px;margin:0 0 4px">${esc(conf.path)}</p>
           <pre class="conf">${esc(conf.text)}</pre>
           <p class="mono" dir="ltr" style="color:var(--muted);font-size:12px;margin:10px 0 4px">${esc(conf.proxy_path)} · ${esc(conf.proxy_unit)}.service</p>
           <pre class="conf">${esc(conf.proxy_text)}</pre>`,
    submitLabel: "סגור", onSubmit: async () => {},
  });
}

const dhcpPreview = $("#dhcp-preview");
if (dhcpPreview) dhcpPreview.addEventListener("click", () =>
  previewDnsmasq().catch((error) => toast(error.message)));

async function loadNet() {
  // הלשונית כולה היא הכרטיסים: כל שורה היא כרטיס רשת של השרת,
  // שיכול להיות DHCP או לא. מכונות שנראו ברשת — בלשונית המחשבים.
  const admin = ME.role === "admin";
  // ‏#36: לפני שמציירים שורות, שואלים את השרת מה גרסת ה-dnsmasq שמותקנת
  // אומרת על מצב proxy. כשל בקריאה משאיר null — ו-null נחשב "לא נבדק",
  // כלומר הצד החוסם. ‏admin בלבד, כמו ה-endpoint עצמו.
  if (admin) {
    try { PROXY_SUPPORT = await api("/net/proxy-support"); }
    catch (error) { PROXY_SUPPORT = null; }
  }
  // ‏#761: הטבלה המאוחדת נבנית ב-netcfg.js (renderNetTable), אחרי שגם
  // הכתובות המוגדרות (loadNetcfg) נטענו — כדי שלא יהיה join שביר של שתי
  // תשובות שמגיעות בזמנים שונים. כאן רק שולפים ושומרים.
  NICS = await api("/net/interfaces");
  const addBtn = $("#nic-add");
  if (addBtn) addBtn.onclick = addNic;
  if (typeof populateSidebarNics === "function") populateSidebarNics();
}

function addNic() {
  // המערכת מזהה לבד מה מחובר ועוד לא הוגדר — בוחרים מרשימה, לא מקלידים.
  const fresh = NICS.filter((n) =>
    n.present && !n.enabled && !n.proxy && !n.description);
  if (!fresh.length) {
    toast("כל הכרטיסים שמחוברים כבר מוגדרים.");
    return;
  }
  sheet({
    title: "הוספת כרטיס",
    sub: "אלה הכרטיסים שמחוברים ועוד לא הוגדרו — בוחרים ונותנים תיאור.",
    fields: [
      { id: "name", label: "כרטיס שזוהה", type: "select",
        value: fresh[0].name,
        options: fresh.map((n) => ({
          value: n.name,
          label: `${n.name}${n.mac ? " · " + n.mac : ""}`,
        })) },
      { id: "description", label: "תיאור", placeholder: "למשל: וילן 700" },
    ],
    submitLabel: "הוסף",
    onSubmit: async (v) => {
      await post("/net/interfaces", { name: v.name, description: v.description });
      await loadNet();
      await loadNetcfg();
      renderNetTable();
    },
  });
}

/* מחוברים לטבלה המאוחדת מ-netcfg.js, אחרי שהיא נבנתה — שלוש הפעולות
   שנשארות ספציפיות לצד ה-DHCP (הגדרת DHCP / תיאור / הסרה). */
function wireNicActions(nics) {
  document.querySelectorAll("[data-nic-edit]").forEach((b) => b.onclick = () => {
    const nic = nics.find((n) => n.name === b.dataset.nicEdit);
    if (!nic) { toast("כרטיס לא נמצא"); return; }
    editNic(nic);
  });
  document.querySelectorAll("[data-nic-desc]").forEach((b) => b.onclick = () => {
    const nic = nics.find((n) => n.name === b.dataset.nicDesc);
    if (!nic) { toast("כרטיס לא נמצא"); return; }
    sheet({
      title: "תיאור הכרטיס", sub: nic.name,
      fields: [{ id: "description", label: "תיאור חופשי",
                 value: nic.description, placeholder: "למשל: וילן 700" }],
      onSubmit: async (v) => {
        await put(`/net/interfaces/${encodeURIComponent(nic.name)}/description`,
                  { description: v.description });
        await loadNet();
        renderNetTable();
      },
    });
  });
  document.querySelectorAll("[data-nic-forget]").forEach((b) => b.onclick = () => confirmSheet(
    "הסרת הגדרות הכרטיס",
    `ההגדרות והתיאור של ${b.dataset.nicForget} יימחקו. אם רץ עליו DHCP — הוא ייכבה.`,
    "הסר", async () => {
      await del(`/net/interfaces/${encodeURIComponent(b.dataset.nicForget)}`);
      await loadNet();
      renderNetTable();
    }));
}

// --- "נראו ברשת" — מוצג בלשונית המחשבים (machines.js קורא לזה) -------------

function ago(iso) {
  if (!iso) return "—";
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 90) return "עכשיו";
  if (seconds < 3600) return `לפני ${Math.round(seconds / 60)} דק'`;
  if (seconds < 86400) return `לפני ${Math.round(seconds / 3600)} שע'`;
  return iso.slice(0, 10);
}

/* #400 — "איפה המכונה הזאת עכשיו באתחול".

   מחשב שיכפול חסר-ראש שנתקע בין תפריט ה-GRUB ל-hello לא השאיר עד היום
   שום עקבה, ו"נראה לאחרונה" לבדו אמר רק שהוא דיבר פעם. העמודה הזאת
   נוקבת ב**שלב האחרון שהגיע**, וכשהוא תקוע — בשם השלב ש**לא** הגיע. */
function bootWhere(b) {
  if (!b) return `<span style="color:var(--muted)">—</span>`;
  const when = b.seconds === null
    ? ` <span class="tag warn">זמן לא נקרא</span>`
    : ` <small style="color:var(--muted)">· ${esc(sinceSeconds(b.seconds))}</small>`;
  const stuck = b.stalled
    ? `<br><span class="tag warn">נעצר לפני: ${esc(b.next_label)}</span>` : "";
  return `<b>${esc(b.label)}</b>
    <small style="color:var(--muted)">(${b.index}/${b.total})</small>${when}${stuck}`;
}

function sinceSeconds(seconds) {
  if (seconds < 90) return `לפני ${Math.round(seconds)} שנ'`;
  if (seconds < 3600) return `לפני ${Math.round(seconds / 60)} דק'`;
  if (seconds < 86400) return `לפני ${Math.round(seconds / 3600)} שע'`;
  return `לפני ${Math.round(seconds / 86400)} ימים`;
}

/* ‏#954 גל 3: הרשימה עצמה מצוירת ב-console.js (seenDevicesCard, מ-NET); כאן
   נשארו שלושת הטפסים — תיאור, הסרה, הוספה ידנית — וכולם טוענים מחדש דרך
   loadMachines (שקורא גם /net). */
function netDeviceDescribe(macEnc) {
  let mac = macEnc; try { mac = decodeURIComponent(macEnc); } catch (e) {}
  const device = (NET || []).find((d) => d.mac === mac);
  if (!device) return;
  sheet({
    title: "תיאור ההתקן", sub: device.mac,
    fields: [{ id: "description", label: "תיאור חופשי",
               value: device.description, placeholder: "למשל: מדפסת מעבדה 2" }],
    onSubmit: async (v) => {
      await put(`/net/${encodeId(device.mac)}`, { description: v.description });
      await loadMachines();
    },
  });
}
function netDeviceForget(macEnc) {
  let mac = macEnc; try { mac = decodeURIComponent(macEnc); } catch (e) {}
  confirmSheet(
    "הסרה מהרשימה",
    `${mac} יוסר. אם המכונה תדבר עם השרת שוב — היא תחזור לרשימה.`,
    "הסר", async () => { await del(`/net/${encodeId(mac)}`); await loadMachines(); });
}
function netDeviceAdd() {
  sheet({
    title: "הוספת התקן ידנית",
    sub: "למכונה שעוד לא דיברה עם השרת.",
    fields: [
      { id: "mac", label: "MAC", placeholder: "b4:2e:99:07:1a:c4", dir: "ltr" },
      { id: "ip", label: "כתובת IP (לא חובה)", placeholder: "10.44.12.187", dir: "ltr" },
      { id: "description", label: "תיאור", placeholder: "למשל: עמדת מרצה חדשה" },
    ],
    submitLabel: "הוסף",
    onSubmit: async (v) => { await post("/net", v); await loadMachines(); },
  });
}
