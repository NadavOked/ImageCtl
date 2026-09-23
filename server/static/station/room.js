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

  /* כרטיס החדר מתרענן כל 2 שניות. כשל קריאה אינו "אין סבב" ואינו נתונים
     עדכניים — לכן במקום return שקט שמשאיר כרטיס קפוא שנראה חי, מסמנים את
     הכרטיס כלא-מעודכן, עם השעה של הקריאה המוצלחת האחרונה (‏#752, עיקרון 5). */
  let lastRoomOk = null;

  function markRoomStale() {
    const el = $("#room-stale");
    if (!el) return;
    el.textContent = "לא הצלחנו לקרוא את מצב החדר — הנתונים שמוצגים אולי אינם "
      + "עדכניים (" + (lastRoomOk ? "עודכן לאחרונה " + lastRoomOk
                                  : "טרם נקרא מהשרת") + ").";
    el.classList.remove("hidden");
  }

  function markRoomFresh() {
    lastRoomOk = new Date().toLocaleTimeString("he-IL");
    const el = $("#room-stale");
    if (el) el.classList.add("hidden");
  }

  async function refresh() {
    let data;
    try {
      data = await fetch("/api/console/room", { credentials: "same-origin" })
        .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });
    } catch (error) { markRoomStale(); return; }
    markRoomFresh();
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
  /* #872: fail (בריאות FAILED) כתום; failed_last (נכשל בשיכפול הקודם) אדום;
     ‏ok/unchecked ירוק ואינם מוצגים. warn מסוכן ישן — כתום. */
  const SMART_HE = {warn: "SMART אזהרה", fail: "SMART תקלה",
                    failed_last: "נכשל בשיכפול הקודם"};
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
      /* בריאות SMART לפני start (#652): רק ממצא שאיננו תקין מוצג, כדי
         לא להציף. ‏unchecked (אין SMART / VM) אינו כשל — לא מוצג. */
      const smart = SMART_HE[d.smart]
        ? ` <span class="smart ${esc(d.smart)}">${SMART_HE[d.smart]}</span>` : "";
      /* #418: הפורט אומר לאן ללכת; הסריאל אומר איזה כונן זה כשמחזיקים
         אותו ביד — בדיוק כמו ברשימת הכשלים (#553). בלעדיו "מגירה 2 ·
         נכתבה" הוא עדיין ניחוש כשיש כמה מגירות שהצליחו ורק אחת נדרשת. */
      const serial = d.serial
        ? ` <span class="mono serial" dir="ltr">${esc(d.serial)}</span>` : "";
      /* #872: CRC שעלה בסבב הזה (הפרש, לא המונה המצטבר) — מידע לטכנאי על
         הכבל, לא צבע של הדיסק. חסר/0 = לא מוצג. */
      const crc = typeof d.crc_delta === "number" && d.crc_delta > 0
        ? ` <span class="crc">CRC +${d.crc_delta} · לבדוק כבל</span>` : "";
      return `<span class="drawer${tone}">${slot}
        <span class="mono dev" dir="ltr">${esc(d.dev || "")}</span>${serial}
        · ${esc(word)}${smart}${crc}</span>`;
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
        const progress = Progress.view(m);
        /* שלושה סופים, לא שניים (#67): מחשב שאיבד מגירה אחת מתוך שלוש
           אינו "הסתיים". שורת המגירות שמתחת אומרת איזו — כאן נאמר
           שהמחשב הזה עוד לא סיים את העבודה, ושאסור לשלוח אותו הלאה. */
        status = m.state === "failed"
          ? `<span class="room-bad">נכשל · ${esc(m.error || "")}</span>`
          : m.state === "partial"
          ? `<span class="room-warn">הושלם חלקית · ${
              esc(m.error || "מגירה אחת לא נכתבה")}</span>`
          : m.state === "done" ? `<span class="room-ok">הסתיים</span>`
          : m.state === "lost"
          ? `<span class="room-warn">נעלם · ${
              esc(m.error || "לא ידוע אם נכתב")}</span>`
          : m.state === "waiting" || !m.state ? `<span class="sub">מחכה לשידור</span>`
          : m.error
          ? `<span>${progress.label} · <span class="room-bad">${esc(m.error)}</span></span>`
          : `<span>${progress.label}</span>`;
      } else {
        status = m.awake
          ? `<span class="room-ok">ער · ${m.fresh_drawers} מגירות מוכנות</span>`
          : `<span class="sub">כבוי</span>`;
      }
      /* #1213: מכונה ב-lost כבר ויתרנו עליה — הנורית לא דולקת בשבילה גם
         כשהיא עדיין joined בסבב הישן, בניגוד לכל מצב אחר. */
      const lit = m.state !== "lost" && (m.awake || m.joined);
      return `<div class="room-row ${lit ? "" : "dim"}">
        <span class="led ${lit ? "on" : ""}"></span>
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

  /* ---------- #953: האם האימג' נכנס למגירה הקטנה ביותר בחדר ---------- */

  /* null = לא ניתן לקבוע (מניפסט בלי גיאומטריה). `Number(null)` הוא 0 —
     ולכן הבדיקה מפורשת, אחרת "לא ידוע" היה מוצג "מ-0GB" (עיקרון 5). */
  function requirementBytes(image) {
    const need = Number(image.min_target_bytes);
    return image.min_target_bytes == null || !Number.isFinite(need) || need < 0
      ? null : need;
  }

  /* הסיבה שאימג' אינו נכנס לרצפה (`disk_floor` מ-GET /room — המגירה הקטנה
     ביותר שדווחה ב-hello האחרון), או null כשהוא נכנס / כשאין דיווח.
     **אותה מחרוזת בדיוק** ש-`room.fit_refusal` בשרת מחזיר ב-409 — המסך
     מציג אותה על האפשרות המושבתת, והשרת אוכף אותה (עיקרון 5). GB עשרוני
     כמו על מדבקת הכונן; האימג' מעוגל כלפי מעלה, הדיסק לקרוב. */
  function imageFitReason(image, floor) {
    const need = requirementBytes(image);
    if (!floor || need === null || need <= floor.size_bytes) return null;
    const slot = floor.port != null ? `דיסק ${floor.port}` : `דיסק ${floor.dev || "?"}`;
    return `${slot} במחשב ${floor.name} הוא ${Math.round(floor.size_bytes / 1e9)}GB, ` +
      `האימג' צריך ${Math.ceil(need / 1e9)}GB`;
  }

  /* הטקסט של אפשרות אחת ברשימה, ואם היא מושבתת. הרצפה מוצגת לכל אימג'
     ("מ-230GB") גם כשהוא נכנס — זו המגבלה שנדב ביקש לראות. */
  function imageOption(image, floor) {
    const reason = imageFitReason(image, floor);
    const need = requirementBytes(image);
    const from = need === null ? "" : ` · מ-${Math.ceil(need / 1e9)}GB`;
    const name = `${image.folder ? image.folder + " / " : ""}${image.name}${from}`;
    return { disabled: reason !== null,
             text: reason ? `${name} — לא נכנס: ${reason}` : name };
  }

  /* מעדכן את הרשימה הקיימת במקום לבנות אותה מחדש — הטופס מתרענן כל
     2 שניות, ובנייה מחדש הייתה מוחקת את הבחירה באמצע. */
  function applyImageFit(floor) {
    const select = $("#room-image");
    if (!select || !images) return;
    const byId = new Map(images.map((i) => [i.id, i]));
    let selectedLost = false;
    for (const option of select.options) {
      const image = byId.get(option.value);
      if (!image) continue;
      const view = imageOption(image, floor);
      option.disabled = view.disabled;
      option.textContent = view.text;
      if (view.disabled && option.selected) selectedLost = true;
    }
    if (selectedLost) { select.value = ""; loadRoomExpand(""); }
    $("#room-fit").textContent = floor
      ? `המגירה הקטנה ביותר בחדר: ${Math.round(floor.size_bytes / 1e9)}GB ` +
        `(דיסק ${floor.port != null ? floor.port : floor.dev} במחשב ${floor.name}).`
      : "גודל הדיסקים לא ידוע — יסורב במכונה אם לא ייכנס.";
  }

  /* #59/#1012: פרטי ההרחבה מגיעים מרשימת האימג'ים המאומתת; אין בקשת
     manifest ציבורית מן הקיוסק. הבחירה חלה על **כל** הגלים בסבב. */
  function loadRoomExpand(imageId) {
    const box = $("#room-expand");
    if (!box) return;
    if (!imageId) { box.innerHTML = ""; return; }
    const image = (images || []).find((item) => item.id === imageId);
    if (!image || !Array.isArray(image.expand_partitions)) {
      box.innerHTML = `<p class="sub">לא הצלחנו לקרוא את פרטי המחיצות — ` +
        `ברירת המחדל האוטומטית תופעל.</p>`;
      return;
    }
    if ($("#room-image").value !== imageId) return;   // נבחר אימג' אחר בינתיים
    box.innerHTML = expandBlockHtml("room", { partitions: image.expand_partitions }, "auto");
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
        <p class="sub" id="room-fit"></p>
        <div id="room-expand"></div>
        <label>כמה כוננים צריך הפעם, סך הכל
          <input type="number" id="room-target" min="1" value="${ready || 24}"></label>
        <p class="sub" id="room-ready"></p>
        <div class="room-machines" id="room-machines"></div>
        <p class="error" id="room-error"></p>`;
      $("#st-room-foot").innerHTML = `
        <button class="btn primary" id="room-open">פתח סבב והער את החדר</button>
        <button class="btn" id="room-wake">העֵר את מחשבי השיכפול</button>
        <button class="btn" id="room-back">חזרה</button>`;
      $("#room-image").addEventListener("change", (event) =>
        loadRoomExpand(event.target.value));
      $("#room-open").addEventListener("click", openRound);
      $("#room-wake").addEventListener("click", wake);
      $("#room-back").addEventListener("click", () => { reset(); MODE = null; poll(); });
    }
    $("#room-ready").textContent = `כרגע ערים: ${ready} מגירות מוכנות.`;
    applyImageFit(data.disk_floor || null);
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
      body: JSON.stringify({ image_id: image, target_drives: target,
                             expand_partition: expandBlockValue("room") }),
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
          <label>עצירת הסבב באמצע היא פעולת חירום. הקלידו את שם האימג'
            המשודר — <b id="room-confirm-name"></b> — לאישור:
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
    $("#room-confirm-name").textContent = round.image_name;
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
    if (response.ok) {
      toast("השידור יוצא לדרך.");
    } else {
      /* ‏#843: 409 "אין מכונות בסבב" — לחיצה שנדחתה בשקט נראית כמו
         כפתור שבור, והמפעיל לוחץ שוב במקום להמתין שיצטרפו. */
      let detail = "שגיאה " + response.status;
      try { detail = (await response.json()).detail || detail; } catch (e) {}
      toast(detail);
    }
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
        toast(`נשלחה בקשת הערה ל-${result.sent} מחשבים · ${result.failed} נכשלו`
              + (reason ? ` — ${reason}` : "."));
      } else {
        toast(`נשלחה בקשת הערה ל-${result.sent} מחשבים.`);
      }
    }
  }

  async function closeRound() {
    /* עצירה מאחורי הקלדת שם האימג' המשודר (עיקרון 7) — בלי confirm()
       שחסום בקיוסק. ההכרעה עברה לשרת ב-#533: המסך שולח את מה שהוקלד
       ומציג את מה שהשרת ענה, במקום לאכוף לבדו. */
    const box = $("#room-confirm");
    if (box.classList.contains("hidden")) {
      box.classList.remove("hidden");
      $("#room-confirm-text").focus();
      return;
    }
    const response = await fetch("/api/console/room/close", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm_name: $("#room-confirm-text").value.trim() }),
    });
    if (!response.ok) {
      let detail = "שגיאה " + response.status;
      try { detail = (await response.json()).detail || detail; } catch (e) {}
      $("#room-error").textContent = detail;
      return;
    }
    toast("הסבב נעצר.");
    reset();
    refresh();
  }

  return { refresh, reset };
})();
