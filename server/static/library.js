/* ImageCtl — ספריית האימג'ים: עץ תיקיות, שורות, מגירת פרטים ועריכה.
   העריכות נשמרות ל-manifest.json בשרת — הדיסק הוא מקור האמת. */
"use strict";

let LIB = { folders: [], images: [], folder: null, selected: null };

async function loadLibrary() {
  [LIB.folders, LIB.images] = await Promise.all([api("/folders"), api("/images")]);
  if (LIB.folder && !LIB.folders.some((f) => f.name === LIB.folder)) LIB.folder = null;
  drawTree();
  drawRows();
  if (ME.role === "admin") await loadCaptures();
}

function imagesIn(folder) {
  return LIB.images.filter((m) => folder === null || m.folder === folder);
}

/* ---------- עץ התיקיות ---------- */

function drawTree() {
  const all = `<div class="node ${LIB.folder === null ? "sel" : ""}" data-folder="">
    <span>הכל</span><span class="cnt">${LIB.images.length}</span></div>`;
  const items = LIB.folders.map((f) => `
    <div class="node ${LIB.folder === f.name ? "sel" : ""}" data-folder="${esc(f.name)}"
         data-reorder-id="${esc(f.name)}">
      <span>${esc(f.name)}</span><span class="cnt">${f.images}</span></div>`).join("");
  $("#folder-tree").innerHTML = all + items;
  document.querySelectorAll("#folder-tree .node").forEach((node) =>
    node.addEventListener("click", () => {
      if (node.dataset.justDragged) return;      // הקליק שסוגר גרירה אינו בחירה
      LIB.folder = node.dataset.folder || null;
      LIB.selected = null;
      drawTree(); drawRows();
    }));

  // סדר התיקיות נקבע בלחיצה ארוכה וגרירה — כמו סדר הכיתות.
  if (ME.role === "admin" && LIB.folders.length > 1) {
    enableLongPressReorder({
      container: $("#folder-tree"),
      itemSelector: "[data-reorder-id]",
      handleSelector: "[data-reorder-id]",
      onDrop: async (names) => {
        try { await post("/folders/order", { names }); toast("הסדר נשמר."); }
        catch (error) { toast(error.message); }
        await loadLibrary();
      },
    });
    $("#folder-tree").classList.add("can-reorder");
  }
}

/* ---------- קליטת אימג' ממחשב הבנייה (זרימה 13.1) ---------- */

async function loadCaptures() {
  const tasks = (await api("/tasks")).filter(
    (t) => t.state === "pending" || t.state === "running");
  const bar = $("#capture-bar");
  if (!tasks.length) { bar.innerHTML = ""; return; }
  bar.innerHTML = tasks.map((t) => {
    const pct = t.bytes_total ? Math.round((100 * t.bytes_written) / t.bytes_total) : 0;
    const waiting = t.state === "pending";
    return `<div class="upload">
      <div class="upload-line">
        <b>קולט: ${esc(t.name)}</b>
        <span>${waiting ? "ממתין שמחשב הבנייה יעלה ב-PXE" : fmtBytes(t.bytes_written) + " נקראו"}</span>
      </div>
      <div class="bar"><i style="width:${waiting ? 0 : pct}%"></i></div>
      <div class="row" style="margin-top:8px">
        <button class="btn danger" data-cancel-task="${esc(t.id)}">ביטול</button>
      </div>
    </div>`;
  }).join("");
  document.querySelectorAll("[data-cancel-task]").forEach((b) => b.onclick = () => confirmSheet(
    "ביטול הקליטה", "המשימה תבוטל והקבצים שהתקבלו יימחקו.", "בטל את הקליטה",
    async () => { await post(`/tasks/${b.dataset.cancelTask}/cancel`); await loadCaptures(); }));
}

$("#capture-image").addEventListener("click", async () => {
  /* בוחרים מחשב בנייה ואת הדיסק שהוא דיווח עליו ב-hello — כך אין
     הקלדת שם התקן, והשרת יודע מה באמת מחובר שם. */
  const machines = await api("/machines");
  const groups = await api("/groups");
  const buildIds = new Set(groups.filter((g) => g.role === "build").map((g) => g.id));
  const builders = machines.filter((m) => buildIds.has(m.group_id));
  if (!builders.length) {
    toast("אין מחשב בנייה רשום — הוסיפו אותו בלשונית המחשבים.");
    return;
  }
  const devices = await api("/net");
  const seen = new Map(devices.map((d) => [d.mac, d]));

  sheet({
    title: "קליטת אימג' חדש",
    sub: "המשימה תמתין למחשב הבנייה. הדליקו אותו ב-PXE כדי להתחיל.",
    fields: [
      {
        id: "mac", label: "מחשב בנייה", type: "select",
        options: builders.map((m) => ({
          value: m.mac,
          label: `${m.suffix} · ${m.mac}` + (seen.has(m.mac) ? "" : " (טרם נראה ברשת)"),
        })),
      },
      { id: "disk", label: "דיסק המקור", value: "sda", dir: "ltr" },
      { id: "name", label: "שם האימג'", placeholder: "למשל: Office 2024 — סטנדרט" },
      { id: "description", label: "תיאור (לא חובה)", type: "textarea" },
      {
        id: "folder", label: "תיקייה", type: "select", value: LIB.folder || "",
        options: [{ value: "", label: "ללא תיקייה" }].concat(
          LIB.folders.map((f) => ({ value: f.name, label: f.name }))),
      },
    ],
    submitLabel: "צור משימת קליטה",
    onSubmit: async (v) => { await post("/tasks/capture", v); await loadCaptures(); },
  });
});

/* ---------- העלאה מהמחשב ---------- */

$("#upload-image").addEventListener("click", () => $("#upload-input").click());

$("#upload-input").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (file) uploadImage(file);
  event.target.value = "";            // כדי שאפשר יהיה לבחור שוב אותו קובץ
});

function uploadImage(file) {
  /* XHR ולא fetch: רק הוא מדווח על התקדמות ההעלאה, וקובץ אימג' שוקל
     עשרות ג'יגה — פס התקדמות כאן הוא ההבדל בין "עובד" ל"נתקע". */
  const bar = $("#upload-bar");
  bar.classList.remove("hidden");
  const draw = (pct, text) => {
    bar.innerHTML = `<div class="upload">
      <div class="upload-line"><b>${esc(file.name)}</b><span>${esc(text)}</span></div>
      <div class="bar"><i style="width:${pct}%"></i></div></div>`;
  };
  draw(0, "מתחיל…");

  const request = new XMLHttpRequest();
  request.open("POST", "/api/console/images/upload");
  request.setRequestHeader("Content-Type", "application/x-tar");
  request.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const pct = Math.round((100 * e.loaded) / e.total);
    draw(pct, `${pct}% · ${fmtBytes(e.loaded)} מתוך ${fmtBytes(e.total)}`);
  };
  request.onload = async () => {
    if (request.status === 200) {
      draw(100, "נקלט. מאמת…");
      await loadLibrary();
      bar.classList.add("hidden");
      toast(`"${JSON.parse(request.responseText).name}" נוסף לספרייה.`);
    } else {
      let message = "שגיאה " + request.status;
      try { message = JSON.parse(request.responseText).detail || message; } catch (e) {}
      bar.innerHTML = `<div class="upload badline">ההעלאה נכשלה: ${esc(message)}</div>`;
    }
  };
  request.onerror = () => {
    bar.innerHTML = `<div class="upload badline">ההעלאה נכשלה — החיבור נותק.</div>`;
  };
  request.send(file);
}

function editFolderSheet(folder) {
  sheet({
    title: "עריכת תיקייה",
    sub: folder.images
      ? `שינוי השם יעדכן גם את ${folder.images} האימג'ים שבתוכה.`
      : "התיקייה ריקה.",
    fields: [
      { id: "name", label: "שם התיקייה", value: folder.name },
      { id: "description", label: "תיאור", type: "textarea", value: folder.description },
    ],
    onSubmit: async (v) => {
      const result = await put(`/folders/${encodeURIComponent(folder.name)}`,
                               { name: v.name, description: v.description });
      LIB.folder = result.name;
      await loadLibrary();
      toast("נשמר.");
    },
  });
}

$("#add-folder").addEventListener("click", () => sheet({
  title: "תיקייה חדשה",
  fields: [
    { id: "name", label: "שם התיקייה", placeholder: "למשל: סייבר" },
    { id: "description", label: "תיאור קצר (לא חובה)", type: "textarea" },
  ],
  submitLabel: "צור תיקייה",
  onSubmit: async (v) => {
    await post("/folders", { name: v.name, description: v.description });
    await loadLibrary();
  },
}));

/* ---------- שורות האימג'ים ---------- */

function drawRows() {
  const folder = LIB.folders.find((f) => f.name === LIB.folder);
  $("#folder-name").textContent = LIB.folder === null ? "הכל" : LIB.folder;
  $("#folder-desc").textContent = folder ? folder.description : "";

  // "עריכת תיקייה" רלוונטי רק לתיקייה אמיתית, ורק למנהל.
  const editButton = $("#edit-folder");
  editButton.classList.toggle("hidden", !folder || ME.role !== "admin");
  if (folder) editButton.onclick = () => editFolderSheet(folder);

  const list = imagesIn(LIB.folder);
  $("#image-rows").innerHTML = list.length ? list.map((m) => `
    <div class="irow ${LIB.selected === m.id ? "sel" : ""}" data-image="${esc(m.id)}">
      <div class="chip"></div>
      <div><b>${esc(m.name)}</b><small>${esc(m.description)}</small></div>
      <div class="size">${fmtBytes(m.total_compressed_bytes)}</div>
      <div class="date">${(m.created || "").slice(0, 10)}</div>
    </div>`).join("")
    : `<div class="lib-empty">אין אימג'ים בתיקייה הזו.<br>קליטת אימג' נעשית ממחשב הבנייה בחדר השיכפולים.</div>`;
  document.querySelectorAll("#image-rows .irow").forEach((row) =>
    row.addEventListener("click", () => {
      LIB.selected = LIB.selected === row.dataset.image ? null : row.dataset.image;
      drawRows();
    }));
  drawDetail();
}

/* ---------- מגירת הפרטים והעריכה ---------- */

function drawDetail() {
  const box = $("#image-detail");
  const m = LIB.images.find((x) => x.id === LIB.selected);
  if (!m) { box.innerHTML = ""; return; }
  const admin = ME.role === "admin";
  box.innerHTML = `<div class="detail">
    <h3>${esc(m.name)}</h3><p class="desc">${esc(m.description) || "בלי תיאור."}</p>
    <div class="meta">
      <div><small>גודל דחוס</small><b>${fmtBytes(m.total_compressed_bytes)}</b></div>
      <div><small>משפחה</small><b>${m.family} GB</b></div>
      <div><small>מערכת</small><b>${{windows: "Windows", linux: "לינוקס"}[m.os] || "—"}</b></div>
      <div><small>מחיצות</small><b>${m.partitions}</b></div>
      <div><small>נקלט</small><b>${(m.created || "").slice(0, 10)}</b></div>
      <div><small>תיקייה</small><b style="font-family:inherit">${esc(m.folder) || "—"}</b></div>
    </div>
    <div class="dbtns">
      <a class="btn" href="/api/console/images/${encodeURIComponent(m.id)}/download"
         download="${esc(m.id)}.tar">↓ הורדה למחשב</a>
      ${admin ? `
        <button class="btn" id="d-rename">שינוי שם</button>
        <button class="btn" id="d-desc">עריכת תיאור</button>
        <button class="btn" id="d-move">העברה לתיקייה</button>
        <button class="btn" id="d-up">▲ בסדר</button>
        <button class="btn" id="d-down">▼ בסדר</button>
        <button class="btn danger" id="d-delete">מחיקה</button>` : ""}
    </div>
  </div>`;
  if (!admin) return;

  $("#d-rename").onclick = () => sheet({
    title: "שינוי שם", fields: [{ id: "name", label: "השם החדש", value: m.name }],
    onSubmit: (v) => saveImage(m.id, { name: v.name }),
  });
  $("#d-desc").onclick = () => sheet({
    title: "עריכת תיאור",
    fields: [{ id: "description", label: "התיאור", type: "textarea", value: m.description }],
    onSubmit: (v) => saveImage(m.id, { description: v.description }),
  });
  $("#d-move").onclick = () => sheet({
    title: "העברה לתיקייה", sub: `לאן להעביר את "${m.name}"?`,
    fields: [{
      id: "folder", label: "תיקייה", type: "select", value: m.folder,
      options: [{ value: "", label: "ללא תיקייה" }].concat(
        LIB.folders.map((f) => ({ value: f.name, label: f.name }))),
    }],
    submitLabel: "העבר",
    onSubmit: (v) => saveImage(m.id, { folder: v.folder }),
  });
  $("#d-up").onclick = () => nudge(m, -1);
  $("#d-down").onclick = () => nudge(m, +1);
  $("#d-delete").onclick = () => sheet({
    title: "מחיקת אימג'",
    sub: "האימג' וכל קבציו יימחקו לצמיתות מהשרת. אין דרך חזרה.",
    verify: { label: "הקלידו את שם האימג' המדויק לאישור", mustEqual: m.name },
    submitLabel: "מחק לצמיתות", danger: true,
    onSubmit: async () => {
      await post(`/images/${encodeURIComponent(m.id)}/delete`, { confirm_name: m.name });
      LIB.selected = null;
      await loadLibrary();
    },
  });
}

async function nudge(m, direction) {
  /* החלפת מקומות עם השכן — הסדר נשמר כמספרים במניפסטים. */
  const siblings = imagesIn(m.folder || LIB.folder);
  const index = siblings.findIndex((x) => x.id === m.id);
  const other = siblings[index + direction];
  if (!other) return;
  siblings.forEach((img, i) => { img.sort = i + 1; });     // נירמול לפני החלפה
  const mine = siblings[index].sort;
  siblings[index].sort = other.sort;
  other.sort = mine;
  try {
    await Promise.all(siblings.map(
      (img) => put(`/images/${encodeURIComponent(img.id)}`, { sort: img.sort })));
  } catch (error) { toast(error.message); }
  await loadLibrary();
  LIB.selected = m.id;
  drawRows();
}

async function saveImage(id, changes) {
  /* המזהה נכנס לנתיב של הבקשה — `encodeURIComponent` כדי שהוא יישאר
     רכיב נתיב אחד, ולא יוכל להצביע על endpoint אחר (‏#110). */
  await put(`/images/${encodeURIComponent(id)}`, changes);
  await loadLibrary();
  toast("נשמר.");
}
