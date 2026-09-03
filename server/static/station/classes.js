/* הפצה לכיתות ממחשב הבנייה — אותם שלבים כמו מסך התחנה (זרימה 13.3):
   כיתה → בחירת מחשבים → אימג' וקידומת → הסבב נפתח ומעיר את הכיתה.
   פתוח לשני התפקידים; הסבב חי בשרת, והמסך רק מציג ומזמין.

   משתמש ב-esc / fmtBytes / toast / $ / MODE / poll מ-station.js. */
"use strict";

const Classes = (() => {
  let step = "class";              // class / machines / image
  let group = null;                // הכיתה שנבחרה {id, label}
  let machines = [];               // [{mac, name}]
  let chosen = new Set();          // MACs שנבחרו
  let images = null;
  let shown = null;                // איזה שלב/מצב מצויר כרגע
  let live = false;                // סבב כיתה חי מוצג כרגע — poll מציץ בזה

  function reset() { step = "class"; group = null; shown = null; live = false; }

  /* בקשה מאומתת של האשף/הפעולות. ‏401 = ה-cookie פג (ההתנתקות
     האוטומטית של הקונסולה מוחקת אותו לכל הדפדפן) — מחזירים לכניסה
     בקול, במקום לקפוא בשקט (#34). */
  async function authed(url, options) {
    const response = await fetch(url, { credentials: "same-origin", ...options });
    if (response.status === 401) {
      SIGNED_IN = false;
      toast("החיבור פג — נדרשת כניסה מחדש.");
      if (!live) { reset(); MODE = null; }
      poll();
      throw new Error("401");
    }
    return response;
  }

  //: פסי השלבים הדקים מהסקיצה — כיתה, מחשבים, אימג', הסבב.
  function steps(current) {
    return `<div class="cls-bars">` +
      [1, 2, 3, 4].map((i) => `<i class="${i <= current ? "on" : ""}"></i>`).join("") +
      `</div>`;
  }

  async function refresh() {
    // התצוגה החיה נשענת על ה-endpoint הלא-מאומת של התחנה — קיוסק לא
    // יכול לתלות ב-cookie שמתפוגג, ותצוגה קפואה גרועה משגיאה (#34).
    let data;
    try {
      data = await fetch("/api/v1/agent/sessions/active")
        .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });
    } catch (error) {
      if (live) $("#st-class-sub").textContent = "אין קשר לשרת — מנסים שוב…";
      return;
    }
    const session = data.session;
    if (session && session.group_role === "classroom") {
      live = true;
      renderLive(session);
      return;
    }
    if (live) { live = false; shown = null; }
    if (!SIGNED_IN) { reset(); MODE = null; poll(); return; }
    try { await renderStep(); } catch (error) { /* 401 — authed כבר ניתב */ }
  }

  function isLive() { return live; }

  /* ---------- האשף: כיתה → מחשבים → אימג' ---------- */

  async function renderStep() {
    if (shown === step) return;
    shown = step;
    if (step === "class") await renderClassPick();
    else if (step === "machines") renderMachinePick();
    else await renderImagePick();
  }

  async function renderClassPick() {
    const groups = (await authed("/api/console/groups")
      .then((r) => r.json())).filter((g) => g.role === "classroom");
    $("#st-class-sub").textContent = "לאיזו כיתה נפתח הסבב";
    $("#st-class-body").innerHTML = steps(1) + (groups.length ? groups.map((g) => `
      <button type="button" class="menu-card ${g.machines ? "" : "dim"}"
              data-class="${esc(g.id)}">
        <span class="tray tray-multi"></span>
        <span><b>${esc(g.label)}</b>
          <small>${g.machines
            ? `${g.machines} מחשבים רשומים`
            : "אין מחשבים רשומים — מוסיפים בקונסולה, במסך המחשבים"}</small></span>
      </button>`).join("")
      : `<p class="sub">אין עדיין כיתות — מגדירים אותן בקונסולה, במסך המחשבים.</p>`);
    $("#st-class-foot").innerHTML = `<button class="btn" id="cls-back">חזרה</button>`;
    $("#cls-back").onclick = () => { reset(); MODE = null; poll(); };
    document.querySelectorAll("[data-class]").forEach((b) => b.onclick = async () => {
      group = groups.find((g) => g.id === b.dataset.class);
      if (!group.machines) {
        // כיתה בלי טבלת MAC אינה יעד לסבב — אין את מי להעיר ולמי לשדר.
        toast("לכיתה הזו אין עדיין מחשבים רשומים.");
        return;
      }
      machines = await fetch(
        `/api/v1/agent/groups/${encodeURIComponent(group.id)}/machines`
      ).then((r) => r.json());
      chosen = new Set(machines.map((m) => m.mac));
      step = "machines"; shown = null; await renderStep();
    });
  }

  function renderMachinePick() {
    $("#st-class-sub").textContent = `למי ההפצה מיועדת — ${group.label}`;
    $("#st-class-body").innerHTML = steps(2) + `
      <div class="machgrid">
        ${machines.map((m) => `<div class="mach on" data-mac="${esc(m.mac)}">
          ${esc(m.name)}</div>`).join("")}
      </div>
      <p class="hint sub">כברירת מחדל כל הכיתה נבחרת — אפשר לבטל מחשבים בודדים.
         רק הנבחרים יוערו ב-WoL ויצטרפו; השאר יעלו רגיל מהדיסק.</p>`;
    $("#st-class-foot").innerHTML = `
      <button class="btn primary" id="cls-next">המשך — <span id="cls-count">${chosen.size}</span> מחשבים</button>
      <button class="btn" id="cls-all">כל הכיתה</button>
      <button class="btn" id="cls-back">חזרה</button>`;
    document.querySelectorAll(".mach").forEach((el) => el.onclick = () => {
      const mac = el.dataset.mac;
      if (chosen.has(mac)) chosen.delete(mac); else chosen.add(mac);
      el.classList.toggle("on", chosen.has(mac));
      $("#cls-count").textContent = chosen.size;
    });
    $("#cls-all").onclick = () => {
      machines.forEach((m) => chosen.add(m.mac));
      document.querySelectorAll(".mach").forEach((el) => el.classList.add("on"));
      $("#cls-count").textContent = chosen.size;
    };
    $("#cls-next").onclick = () => {
      if (!chosen.size) { toast("בחרו לפחות מחשב אחד."); return; }
      step = "image"; shown = null; renderStep();
    };
    $("#cls-back").onclick = () => { step = "class"; shown = null; renderStep(); };
  }

  let chosenImage = "";

  async function renderImagePick() {
    if (images === null) {
      images = await authed("/api/console/images").then((r) => r.json());
      images.sort((a, b) => (a.folder + a.name).localeCompare(b.folder + b.name, "he"));
    }
    // הרשימה כמו בסקיצה: קבוצות לפי תיקייה, שורה לכל אימג' עם גודל.
    const byFolder = {};
    images.forEach((i) => (byFolder[i.folder || "ללא תיקייה"] ??= []).push(i));
    $("#st-class-sub").textContent = `מה יותקן — ${group.label}, ${chosen.size} מחשבים`;
    $("#st-class-body").innerHTML = steps(3) + Object.entries(byFolder).map(
      ([folder, list]) => `
      <div class="img-group"><div class="glabel">${esc(folder)}</div>
        ${list.map((i) => `<div class="img-row ${i.id === chosenImage ? "on" : ""}"
             data-img="${esc(i.id)}">
          <span class="chip"></span>
          <span><b>${esc(i.name)}</b>
            ${i.description ? `<small>${esc(i.description)}</small>` : ""}</span>
          <span class="sz mono" dir="ltr">${fmtBytes(i.total_compressed_bytes)}</span>
        </div>`).join("")}
      </div>`).join("") + `
      <div class="namewrap">
        <label>קידומת שמות לכיתה
          <input type="text" id="cls-prefix"
                 value="${esc(group.id.replace(/^grp_/, "").toUpperCase())}"></label>
        <p class="hint sub">כל מחשב יקבל את השם קידומת-סיומת לפי טבלת ה-MAC,
           ומחשב המרצה תמיד INS.</p>
      </div>
      <p class="error" id="cls-error"></p>`;
    $("#st-class-foot").innerHTML = `
      <button class="btn primary" id="cls-open">פתח סבב והער את הכיתה</button>
      <button class="btn" id="cls-back">חזרה</button>`;
    document.querySelectorAll(".img-row").forEach((row) => row.onclick = () => {
      chosenImage = row.dataset.img;
      document.querySelectorAll(".img-row").forEach((r) =>
        r.classList.toggle("on", r === row));
    });
    $("#cls-back").onclick = () => { step = "machines"; shown = null; renderStep(); };
    $("#cls-open").onclick = async () => {
      const image = chosenImage;
      if (!image) { $("#cls-error").textContent = "בחרו אימג'"; return; }
      const body = { group_id: group.id, image_id: image,
                     prefix: $("#cls-prefix").value.trim() };
      if (chosen.size < machines.length) body.macs = [...chosen];
      let response;
      try {
        response = await authed("/api/console/sessions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      } catch (error) { return; }
      if (!response.ok) {
        let detail = "שגיאה " + response.status;
        try { detail = (await response.json()).detail || detail; } catch (e) {}
        $("#cls-error").textContent = detail;
        return;
      }
      toast("הסבב נפתח — הכיתה מתעוררת.");
      shown = null; refresh();
    };
  }

  /* ---------- סבב כיתה חי ---------- */

  function renderLive(session) {
    if (shown !== "live-" + session.state) {
      shown = "live-" + session.state;
      $("#st-class-body").innerHTML = steps(4) + `
        <div class="st-prog-line">
          <b id="cls-joined"></b><span class="sub">מחשבים בסבב</span>
        </div>
        <p class="sub" id="cls-hint"></p>
        <div class="room-machines" id="cls-machines"></div>
        <div id="cls-confirm" class="hidden">
          <label>עצירת הסבב באמצע היא פעולת חירום. הקלידו <b>עצור</b> לאישור:
            <input type="text" id="cls-confirm-text"></label>
        </div>
        <p class="error" id="cls-error"></p>`;
      $("#st-class-foot").innerHTML = `
        ${session.state === "open"
          ? `<button class="btn primary" id="cls-start">התחל עכשיו</button>` : ""}
        <button class="btn danger" id="cls-close">עצור סבב</button>
        <button class="btn" id="cls-back">חזרה</button>`;
      if (session.state === "open") {
        $("#cls-start").onclick = async () => {
          let response;
          try {
            response = await authed(`/api/console/sessions/${session.id}/start`,
                                    { method: "POST" });
          } catch (error) { return; }
          if (!response.ok) { toast("ההתחלה נכשלה — ראו את הקונסולה."); return; }
          shown = null; refresh();
        };
      }
      $("#cls-close").onclick = () => closeSession(session.id);
      $("#cls-back").onclick = () => { reset(); MODE = null; poll(); };
    }

    $("#st-class-sub").textContent =
      `משדר: ${session.image_name} · ${session.prefix} — ${session.group_label}`;
    $("#cls-joined").textContent = `${session.joined} / ${session.expected_clients}`;
    $("#cls-hint").textContent = session.state === "open"
      ? `הסבב פתוח — כל מחשב שעולה מצטרף. השידור יתחיל כשכולם יגיעו,
         בעוד ${Math.floor(session.starts_in_seconds / 60)}:${String(session.starts_in_seconds % 60).padStart(2, "0")} דקות, או בלחיצה.`
      : "השידור רץ. עומדים בכיתה ורואים מי תקוע — בלי לעבור בין מסכים.";
    $("#cls-machines").innerHTML = session.members.map((m) => {
      const pct = m.bytes_total
        ? Math.round((100 * m.bytes_written) / m.bytes_total) : 0;
      const status = m.state === "failed"
        ? `<span class="room-bad">נכשל · ${esc(m.error || "")}</span>`
        : m.done || m.state === "done" ? `<span class="room-ok">הסתיים</span>`
        : m.state === "waiting" ? `<span class="sub">ממתין לשידור</span>`
        : `<span>${pct}%</span>`;
      return `<div class="room-row">
        <span class="led on"></span>
        <b>${esc(m.hostname || m.name || m.mac)}</b>
        <span class="mono dev" dir="ltr">${esc(m.mac)}</span>
        ${status}</div>`;
    }).join("") || `<p class="sub">עוד לא הצטרף אף מחשב — הכיתה מתעוררת.</p>`;
  }

  async function closeSession(id) {
    const box = $("#cls-confirm");
    if (box.classList.contains("hidden")) {
      box.classList.remove("hidden");
      $("#cls-confirm-text").focus();
      return;
    }
    if ($("#cls-confirm-text").value.trim() !== "עצור") {
      $("#cls-error").textContent = "הטקסט שהוקלד אינו זהה.";
      return;
    }
    let response;
    try {
      response = await authed(`/api/console/sessions/${id}/close`, { method: "POST" });
    } catch (error) { return; }
    if (!response.ok) { $("#cls-error").textContent = "העצירה נכשלה — ראו את הקונסולה."; return; }
    toast("הסבב נעצר.");
    reset(); refresh();
  }

  return { refresh, reset, isLive };
})();
