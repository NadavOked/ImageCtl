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

function nicMode(n) {
  // הטווח עטוף ב-LTR — בלעדיו ה-bidi הופך את סדר הכתובות בתא עברי.
  if (n.enabled) {
    return { html: `פעיל · <span dir="ltr">${esc(n.range_start)}–${esc(n.range_end)}</span>`,
             on: true };
  }
  if (n.proxy) {
    const tag = PROXY_SUPPORT && PROXY_SUPPORT.verified ? ""
      : ` <span class="tag warn">${esc(
          PROXY_SUPPORT && PROXY_SUPPORT.version
            ? `dnsmasq ${PROXY_SUPPORT.version} — לא נבדק`
            : "גרסת dnsmasq לא נקראה")}</span>`;
    return { html: `פעיל · proxy, PXE בלבד${tag}`, on: true };
  }
  return { html: "כבוי", on: false };
}

function nicRow(n, admin) {
  // כרטיס של השרת עצמו — שורה רגילה לכל דבר; רק עמודת ה-DHCP מבדילה.
  const trunk = n.trunk ? ` <span class="tag warn">רשת המכללה</span>` : "";
  const missing = n.present ? "" : ` <span class="tag warn">לא נמצא במכונה</span>`;
  const mode = nicMode(n);
  // כשהמערכת לא מדווחת כתובות, הכתובת שהוגדרה ל-DHCP היא כתובת הכרטיס.
  const ip = n.addresses.join(" · ") || ((n.enabled || n.proxy) && n.server_ip) || "";
  return `<tr>
    <td><b dir="ltr">${esc(n.name)}</b>${trunk}${missing}</td>
    <td class="mono" dir="ltr">${esc(n.mac) || "—"}</td>
    <td class="mono" dir="ltr">${esc(ip) || "—"}</td>
    <td>${esc(n.description) || `<span style="color:var(--muted)">—</span>`}</td>
    <td class="${mode.on ? "nic-on" : ""}">${mode.html}</td>
    <td>${admin ? `
      <button class="btn" data-nic-desc="${esc(n.name)}">תיאור</button>
      <button class="btn" data-nic-edit="${esc(n.name)}">
        ${mode.on ? "הגדרות DHCP" : "הגדרת DHCP"}</button>
      <button class="btn danger" data-nic-forget="${esc(n.name)}">הסר</button>` : ""}
    </td></tr>`;
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
      + `<div class="sheet-note danger" id="proxy-warn" hidden>${
           esc((PROXY_SUPPORT && PROXY_SUPPORT.reason) || "")}</div>`,
    fields: [
      { id: "mode", label: "מצב", type: "select",
        value: nic.enabled ? "dhcp" : nic.proxy ? "proxy" : "dhcp",
        options: [{ value: "dhcp", label: "DHCP מלא — מחלק כתובות (וילן הפצה)" },
                  { value: "proxy", label: "proxy — עונה על PXE בלבד, DHCP קיים נשאר" },
                  ...(active ? [{ value: "off", label: "כבוי — הכרטיס מפסיק לענות" }] : [])] },
      { id: "server_ip", label: "כתובת השרת בוילן", value: nic.server_ip || guess, dir: "ltr", placeholder: "10.99.9.10" },
      { id: "range_start", label: "תחילת טווח הכתובות", value: nic.range_start, dir: "ltr", placeholder: "10.99.9.50" },
      { id: "range_end", label: "סוף הטווח", value: nic.range_end, dir: "ltr", placeholder: "10.99.9.200" },
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
}

/* --- בריאות המערכת — רמזור לכל בדיקה (health.py) ------------------------- */

async function loadHealth() {
  const checks = await api("/health");
  $("#health-list").innerHTML = checks.map((c) => `
    <div class="health-row">
      <span class="hlight ${c.state}"></span>
      <b>${esc(c.label)}</b>
      <span class="sub">${esc(c.detail)}</span>
    </div>`).join("");
}

$("#health-refresh").addEventListener("click", () =>
  loadHealth().catch((error) => toast("רענון נכשל: " + error.message)));

$("#dhcp-preview").addEventListener("click", async () => {
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
});

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
  const nics = await api("/net/interfaces");
  $("#net-table tbody").innerHTML = nics.map((n) => nicRow(n, admin)).join("")
    || `<tr><td colspan="6">לא נמצאו כרטיסי רשת.</td></tr>`;

  $("#nic-add").onclick = () => {
    // המערכת מזהה לבד מה מחובר ועוד לא הוגדר — בוחרים מרשימה, לא מקלידים.
    const fresh = nics.filter((n) =>
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
      },
    });
  };
  document.querySelectorAll("[data-nic-edit]").forEach((b) => b.onclick = () =>
    editNic(nics.find((n) => n.name === b.dataset.nicEdit)));
  document.querySelectorAll("[data-nic-desc]").forEach((b) => b.onclick = () => {
    const nic = nics.find((n) => n.name === b.dataset.nicDesc);
    sheet({
      title: "תיאור הכרטיס", sub: nic.name,
      fields: [{ id: "description", label: "תיאור חופשי",
                 value: nic.description, placeholder: "למשל: וילן 700" }],
      onSubmit: async (v) => {
        await put(`/net/interfaces/${encodeURIComponent(nic.name)}/description`,
                  { description: v.description });
        await loadNet();
      },
    });
  });
  document.querySelectorAll("[data-nic-forget]").forEach((b) => b.onclick = () => confirmSheet(
    "הסרת הגדרות הכרטיס",
    `ההגדרות והתיאור של ${b.dataset.nicForget} יימחקו. אם רץ עליו DHCP — הוא ייכבה.`,
    "הסר", async () => {
      await del(`/net/interfaces/${encodeURIComponent(b.dataset.nicForget)}`);
      await loadNet();
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

async function renderSeenDevices(container, reload) {
  const devices = await api("/net");
  const rows = devices.map((d) => {
    // שמות ידידותיים קודמים לכל (אפיון סעיף 22): מי שרשום מופיע בשמו
    // ובקבוצתו; מי שלא — מסומן מיד, כי זה מה שמחפשים כאן.
    const who = d.registered
      ? `<b>${esc(d.name)}</b> <small style="color:var(--muted)">· ${esc(d.group_label)}</small>`
      : `<span class="tag warn">לא רשום</span>`;
    return `<tr>
      <td>${who}</td>
      <td class="mono" dir="ltr">${esc(d.mac)}</td>
      <td class="mono" dir="ltr">${esc(d.ip) || "—"}</td>
      <td>${esc(d.description) || `<span style="color:var(--muted)">—</span>`}</td>
      <td>${ago(d.last_seen)}</td>
      <td>
        <button class="btn" data-net-desc="${esc(d.mac)}">תיאור</button>
        <button class="btn danger" data-net-forget="${esc(d.mac)}">הסר</button>
      </td></tr>`;
  }).join("");

  container.innerHTML = `
    <div class="chead">
      <div><h2>נראו ברשת</h2>
        <p>כל מכונה שדיברה עם השרת נרשמת כאן אוטומטית, עם הכתובת שקיבלה.
           מכונה שאינה רשומה בטבלאות מסומנת — זה מה שתופס החלפת כרטיס או מחשב חדש.</p></div>
      <button class="btn" id="net-add">+ הוספה ידנית</button>
    </div>
    <table>
      <thead><tr><th>שם</th><th>MAC</th><th>כתובת IP</th><th>תיאור</th>
        <th>נראה לאחרונה</th><th></th></tr></thead>
      <tbody>${rows
        || `<tr><td colspan="6">עוד לא נראו התקנים. מכונה שתעלה ב-PXE תופיע כאן.</td></tr>`}</tbody>
    </table>`;

  container.querySelectorAll("[data-net-desc]").forEach((b) => b.onclick = () => {
    const device = devices.find((d) => d.mac === b.dataset.netDesc);
    sheet({
      title: "תיאור ההתקן", sub: device.mac,
      fields: [{ id: "description", label: "תיאור חופשי",
                 value: device.description, placeholder: "למשל: מדפסת מעבדה 2" }],
      onSubmit: async (v) => {
        await put(`/net/${device.mac}`, { description: v.description });
        await reload();
      },
    });
  });
  container.querySelectorAll("[data-net-forget]").forEach((b) => b.onclick = () => confirmSheet(
    "הסרה מהרשימה",
    `${b.dataset.netForget} יוסר. אם המכונה תדבר עם השרת שוב — היא תחזור לרשימה.`,
    "הסר", async () => { await del(`/net/${b.dataset.netForget}`); await reload(); }));
  container.querySelector("#net-add").onclick = () => sheet({
    title: "הוספת התקן ידנית",
    sub: "למכונה שעוד לא דיברה עם השרת.",
    fields: [
      { id: "mac", label: "MAC", placeholder: "00:00:5e:07:1a:c4", dir: "ltr" },
      { id: "ip", label: "כתובת IP (לא חובה)", placeholder: "10.99.12.187", dir: "ltr" },
      { id: "description", label: "תיאור", placeholder: "למשל: עמדת מרצה חדשה" },
    ],
    submitLabel: "הוסף",
    onSubmit: async (v) => { await post("/net", v); await reload(); },
  });
  return devices.length;
}
