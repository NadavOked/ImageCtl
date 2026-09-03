/* הפצה למחשבי שיכפול — הצד של המסך (Issue #9, אפיון סעיף 29).
   כל המצב מגיע מ-GET /api/console/room; המסך מציג ומזמין בלבד.

   המסך נצבע בשני שלבים: שלד שנבנה פעם אחת לכל מצב (setup / גל),
   וחלקים חיים שמתעדכנים בכל דגימה — אחרת הטופס היה נמחק למשתמש
   באמצע ההקלדה, כל שתי שניות.

   משתמש ב-esc / fmtBytes / toast / $ / MODE / ROLE מ-station.js. */
"use strict";

const Room = (() => {
  let images = null;               // נטען פעם אחת אחרי הכניסה
  let shown = null;                // "setup" / "open" / "running" — השלד הנוכחי

  async function refresh() {
    let data;
    try {
      data = await fetch("/api/console/room", { credentials: "same-origin" })
        .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });
    } catch (error) { return; }
    if (data.round) renderLive(data);
    else await renderSetup(data);
  }

  function reset() { shown = null; }

  /* ---------- שורות המכונות (חלק חי, בשני המצבים) ---------- */

  /* המגירות מוצגות לפי החריץ הפיזי, לא לפי סדר הגילוי של הקרנל (#27):
     ‏ata1 הוא המגירה העליונה, ata2 האמצעית, ata3 התחתונה. שם ההתקן
     (sdb) נשאר כפרט משני — הטכנאי שולף חריץ, לא אות. מכונה שלא דיווחה
     חריץ (סוכן ישן, VM עם SCSI) מוצגת בדיוק כמו קודם. */
  const DRAWER_STATE = {
    done: "נכתבה", failed: "נכשלה", writing: "כותבת",
    verifying: "מאמתת", waiting: "ממתינה",
  };
  const SLOTS_TITLE = "מספור לפי החריץ: 1 עליונה, 2 אמצעית, 3 תחתונה";

  function drawerLine(m, withProgress) {
    const drawers = (m.drawer_list || []).slice();
    if (!drawers.length) return "";
    drawers.sort((a, b) => (a.port ?? 99) - (b.port ?? 99)
      || String(a.dev || "").localeCompare(String(b.dev || "")));
    const chips = drawers.map((d) => {
      const live = withProgress && m.joined && d.state;
      const word = live ? (DRAWER_STATE[d.state] || d.state)
        : d.fresh ? "מוכנה" : "נכתבה";
      const tone = d.state === "failed" ? " bad"
        : d.state === "done" || (!live && !d.fresh) ? " ok" : "";
      const slot = typeof d.port === "number" ? `מגירה ${d.port}` : "מגירה נוספת";
      return `<span class="drawer${tone}">${slot}
        <span class="mono dev" dir="ltr">${esc(d.dev || "")}</span>
        · ${esc(word)}</span>`;
    }).join("");
    return `<div class="room-drawers" title="${SLOTS_TITLE}">${chips}</div>`;
  }

  function machineRows(machines, withProgress) {
    if (!machines.length) {
      return `<p class="sub">אין מחשבי שיכפול רשומים — רושמים אותם בקונסולה,
              במסך המחשבים.</p>`;
    }
    return machines.map((m) => {
      let status;
      if (withProgress && m.joined) {
        const pct = m.bytes_total
          ? Math.round((100 * m.bytes_written) / m.bytes_total) : 0;
        /* שלושה סופים, לא שניים (#67): מחשב שאיבד מגירה אחת מתוך שלוש
           אינו "הסתיים". שורת המגירות שמתחת אומרת איזו — כאן נאמר
           שהמחשב הזה עוד לא סיים את העבודה, ושאסור לשלוח אותו הלאה. */
        status = m.state === "failed"
          ? `<span class="room-bad">נכשל · ${esc(m.error || "")}</span>`
          : m.state === "partial"
          ? `<span class="room-warn">הושלם חלקית · ${
              esc(m.error || "מגירה אחת לא נכתבה")}</span>`
          : m.state === "done" ? `<span class="room-ok">הסתיים</span>`
          : m.state === "waiting" || !m.state ? `<span class="sub">מחכה לשידור</span>`
          : m.error
          ? `<span>${pct}% · <span class="room-bad">${esc(m.error)}</span></span>`
          : `<span>${pct}%</span>`;
      } else {
        status = m.awake
          ? `<span class="room-ok">ער · ${m.fresh_drawers} מגירות מוכנות</span>`
          : `<span class="sub">כבוי</span>`;
      }
      return `<div class="room-row ${m.awake || m.joined ? "" : "dim"}">
        <span class="led ${m.awake || m.joined ? "on" : ""}"></span>
        <b>${esc(m.name)}</b>
        <span class="mono dev" dir="ltr">${esc(m.mac)}</span>
        <span class="state">${status}</span>
        ${drawerLine(m, withProgress)}
      </div>`;
    }).join("");
  }

  /* ---------- לפני סבב: אימג', יעד, ומי ער ---------- */

  async function loadImages() {
    if (images !== null) return;
    images = await fetch("/api/console/images", { credentials: "same-origin" })
      .then((r) => r.json());
    images.sort((a, b) => (a.folder + a.name).localeCompare(b.folder + b.name, "he"));
  }

  async function renderSetup(data) {
    await loadImages();
    const ready = data.machines.reduce((n, m) => n + (m.awake ? m.fresh_drawers : 0), 0);

    if (shown !== "setup") {
      shown = "setup";
      $("#st-room-sub").textContent =
        "בוחרים אימג' ויעד כוננים; החדר מתעורר, וכל גל כותב למגירות שמוכנות.";
      $("#st-room-body").innerHTML = `
        <label>אימג' לשידור
          <select id="room-image">
            <option value="">בחרו אימג'…</option>
            ${images.map((i) => `<option value="${esc(i.id)}">
              ${esc(i.folder ? i.folder + " / " : "")}${esc(i.name)}</option>`).join("")}
          </select></label>
        <label>כמה כוננים צריך הפעם, סך הכל
          <input type="number" id="room-target" min="1" value="${ready || 24}"></label>
        <p class="sub" id="room-ready"></p>
        <div class="room-machines" id="room-machines"></div>
        <p class="error" id="room-error"></p>`;
      $("#st-room-foot").innerHTML = `
        <button class="btn primary" id="room-open">פתח סבב והער את החדר</button>
        <button class="btn" id="room-wake">העֵר את מחשבי השיכפול</button>
        <button class="btn" id="room-back">חזרה</button>`;
      $("#room-open").addEventListener("click", openRound);
      $("#room-wake").addEventListener("click", wake);
      $("#room-back").addEventListener("click", () => { reset(); MODE = null; poll(); });
    }
    $("#room-ready").textContent = `כרגע ערים: ${ready} מגירות מוכנות.`;
    $("#room-machines").innerHTML = machineRows(data.machines, false);
  }

  async function openRound() {
    const image = $("#room-image").value;
    const target = parseInt($("#room-target").value, 10);
    if (!image || !(target > 0)) {
      $("#room-error").textContent = "בחרו אימג' וקבעו יעד כוננים";
      return;
    }
    const response = await fetch("/api/console/room", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image_id: image, target_drives: target }),
    });
    if (!response.ok) {
      let detail = "שגיאה " + response.status;
      try { detail = (await response.json()).detail || detail; } catch (e) {}
      $("#room-error").textContent = detail;
      return;
    }
    toast("הסבב נפתח — החדר מתעורר.");
    refresh();
  }

  /* ---------- סבב חי: גלים, התקדמות, ופעולות ---------- */

  function renderLive(data) {
    const round = data.round;

    if (shown !== round.wave_state) {
      shown = round.wave_state;
      $("#st-room-body").innerHTML = `
        <div class="st-prog-line">
          <b id="room-count"></b>
          <span class="sub">כוננים שנכתבו</span>
        </div>
        <div class="bar big-bar"><i id="room-bar" style="width:0%"></i></div>
        <p class="sub" id="room-hint"></p>
        <div class="room-machines" id="room-machines"></div>
        <div id="room-confirm" class="hidden">
          <label>עצירת הסבב באמצע היא פעולת חירום. הקלידו <b>עצור</b> לאישור:
            <input type="text" id="room-confirm-text"></label>
        </div>
        <p class="error" id="room-error"></p>`;
      $("#st-room-foot").innerHTML = `
        ${round.wave_state === "open"
          ? `<button class="btn primary" id="room-start">התחל עכשיו</button>` : ""}
        <button class="btn" id="room-wake">העֵר שוב</button>
        <button class="btn danger" id="room-close">עצור סבב</button>
        <button class="btn" id="room-back">חזרה</button>`;
      if (round.wave_state === "open") {
        $("#room-start").addEventListener("click", startNow);
      }
      $("#room-wake").addEventListener("click", wake);
      $("#room-close").addEventListener("click", closeRound);
      // חזרה לתפריט בלבד — הסבב חי בשרת וממשיך בלעדינו.
      $("#room-back").addEventListener("click", () => { reset(); MODE = null; poll(); });
    }

    $("#st-room-sub").textContent =
      `משדר: ${round.image_name} · גל ${round.wave_number}`;
    $("#room-count").textContent =
      `${round.written_drives} / ${round.target_drives}`;
    $("#room-bar").style.width =
      Math.round((100 * round.written_drives) / round.target_drives) + "%";
    $("#room-hint").textContent = round.wave_state === "open"
      ? `הגל ממתין: ${round.ready_drives} מגירות מוכנות, צריך עוד
         ${Math.max(0, round.remaining_drives - round.ready_drives)}
         — או "התחל עכשיו". החלפתם מגירות? הדליקו את המכונות והן יצטרפו.`
      : "הגל משדר. מכונה שסיימה — מכבים, מחליפים מגירות, מדליקים.";
    $("#room-machines").innerHTML =
      machineRows(data.machines, round.wave_state === "running");
  }

  async function startNow() {
    const response = await fetch("/api/console/room/start",
      { method: "POST", credentials: "same-origin" });
    if (response.ok) toast("השידור יוצא לדרך.");
    refresh();
  }

  async function wake() {
    const response = await fetch("/api/console/room/wake",
      { method: "POST", credentials: "same-origin" });
    if (response.ok) {
      const result = await response.json();
      /* ‏"0 מחשבים" הוא נכון אבל לא מספיק: בלי הסיבה הטכנאי מחפש WoL
         ב-BIOS של 12 מכונות, כשהכבל בשרת מנותק (#74). */
      const reason = (result.reasons || [])[0];
      if (result.failed) {
        toast(`נשלחה הערה ל-${result.woken} מחשבים · ${result.failed} נכשלו`
              + (reason ? ` — ${reason}` : "."));
      } else {
        toast(`נשלחה הערה ל-${result.woken} מחשבים.`);
      }
    }
  }

  async function closeRound() {
    /* עצירה מאחורי הקלדה (אפיון סעיף 15) — בלי confirm() שחסום בקיוסק. */
    const box = $("#room-confirm");
    if (box.classList.contains("hidden")) {
      box.classList.remove("hidden");
      $("#room-confirm-text").focus();
      return;
    }
    if ($("#room-confirm-text").value.trim() !== "עצור") {
      $("#room-error").textContent = "הטקסט שהוקלד אינו זהה.";
      return;
    }
    const response = await fetch("/api/console/room/close",
      { method: "POST", credentials: "same-origin" });
    if (response.ok) toast("הסבב נעצר.");
    reset();
    refresh();
  }

  return { refresh, reset };
})();
