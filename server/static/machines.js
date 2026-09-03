/* ImageCtl — לשונית המחשבים: תת-לשונית לכל אחד משלושת סוגי המחשבים.
   כיתות = הרבה קבוצות. שיכפול ובנייה = קבוצה קבועה אחת, בלי ניהול קבוצות. */
"use strict";

const TYPES = {
  classroom: {
    title: "מחשבי כיתות", grouped: true,
    nameLabel: "מספר (01-99) או INS",
    hint: "כל כיתה היא קבוצה. הסיומת נכנסת לשם המחשב: קידומת-סיומת.",
  },
  cloner: {
    title: "מחשבי שיכפול", grouped: false, fixed: "grp_CLONERS",
    nameLabel: "שם, למשל: עמדה 3",
    hint: "12 מחשבי חדר השיכפולים. אין להם שם מחשב — השם כאן הוא לזיהוי בקונסולה.",
  },
  build: {
    title: "מחשב בניית אימג'ים", grouped: false, fixed: "grp_BUILD",
    nameLabel: "שם, למשל: מחשב בנייה",
    hint: "המחשב שקולט אימג'ים חדשים. בדרך כלל אחד.",
  },
  // "מה חי ברשת" — הרינדור עצמו יושב ב-net.js (renderSeenDevices).
  seen: { title: "נראו ברשת", seen: true },
};

/* אילו קבוצות פתוחות — נשמר בין רינדורים, אחרת כל הוספת מכונה
   הייתה מקפלת את הקבוצה שעובדים עליה. */
let MACHINES = { role: "classroom", groups: [], open: new Set() };

document.querySelectorAll("#subtabs button").forEach((button) =>
  button.addEventListener("click", () => {
    document.querySelectorAll("#subtabs button").forEach(
      (b) => b.classList.toggle("on", b === button));
    MACHINES.role = button.dataset.role;
    loadMachinesTab();
  }));

function drawSubtabCounts() {
  /* מונה מכונות לכל סוג — כדי שלשונית ריקה תיקרא כ"אין כאן", ולא
     כ"הנתונים נעלמו": רואים מיד אם המכונות יושבות בסוג אחר. */
  document.querySelectorAll("#subtabs button").forEach((button) => {
    const type = TYPES[button.dataset.role];
    if (type.seen) return;                       // הספירה נקבעת כשהיא נטענת
    const groups = MACHINES.groups.filter((g) => g.role === button.dataset.role);
    const machines = groups.reduce((sum, g) => sum + g.machines, 0);
    button.textContent = type.title + ` (${machines})`;
  });
}

async function loadMachinesTab() {
  MACHINES.groups = await api("/groups");
  drawSubtabCounts();
  const type = TYPES[MACHINES.role];
  if (type.seen) {
    const count = await renderSeenDevices($("#role-body"), loadMachinesTab);
    document.querySelector('#subtabs [data-role="seen"]').textContent =
      `${type.title} (${count})`;
    return;
  }
  const groups = MACHINES.groups.filter((g) => g.role === MACHINES.role);
  // קבוצה יחידה (שיכפול, בנייה) נפתחת לבד — אין מה לבחור בינה לבין מה.
  if (groups.length === 1) MACHINES.open.add(groups[0].id);
  // קבוצה שהטעינה שלה נכשלה לא מפילה את כל הלשונית — היא מוצגת עם שגיאה.
  const blocks = await Promise.all(groups.map((g) => groupBlock(g, type).catch((error) =>
    `<div class="group-block"><div class="group-head"><h3>${esc(g.label)}</h3>
      <span class="badline">טעינת המכונות נכשלה: ${esc(error.message)}</span></div></div>`)));

  const empty = type.grouped
    ? `<div class="lib-empty">אין עדיין קבוצות כיתה.<br>
         כל כיתה היא קבוצה — צרו את הראשונה מהכפתור למעלה.</div>`
    : `<div class="lib-empty">הקבוצה הקבועה חסרה. הפעילו את השרת מחדש כדי ליצור אותה.</div>`;

  $("#role-body").innerHTML = `
    <div class="chead"><div><h2>${esc(type.title)}</h2><p>${esc(type.hint)}</p></div>
      ${type.grouped ? `<button class="btn" id="add-group">+ קבוצה חדשה</button>` : ""}
    </div>
    ${blocks.join("") || empty}`;

  if (type.grouped) $("#add-group").onclick = () => addGroupSheet();
  groups.forEach((g) => wireGroup(g, type));

  // סדר הקבוצות נקבע בגרירה — רלוונטי רק היכן שיש יותר מאחת.
  if (type.grouped && groups.length > 1) {
    enableLongPressReorder({
      container: $("#role-body"),
      itemSelector: ".group-block",
      handleSelector: ".group-head",
      onDrop: async (ids) => {
        try { await post("/groups/order", { ids }); toast("הסדר נשמר."); }
        catch (error) { toast(error.message); }
        await loadMachinesTab();
      },
    });
    $("#role-body").classList.add("can-reorder");
  }
}

/* ---------- קבוצה אחת: כותרת, טבלה, הוספה, ייבוא ---------- */

async function groupBlock(group, type) {
  const machines = await api("/machines?group=" + encodeURIComponent(group.id));
  const rows = machines.map((m) => `<tr>
    <td class="mono" dir="ltr">${esc(m.mac)}</td>
    <td><b>${esc(m.suffix)}</b></td>
    <td>
      <button class="btn" data-edit="${esc(m.mac)}">עריכה</button>
      <button class="btn danger" data-remove="${esc(m.mac)}">מחק</button>
    </td></tr>`).join("");

  return `<details class="group-block" data-block="${esc(group.id)}"
                   data-reorder-id="${esc(group.id)}"
                   ${MACHINES.open.has(group.id) ? "open" : ""}>
    <summary class="group-head">
      <span class="caret">▾</span>
      <h3>${esc(group.label)}</h3>
      <span class="pill">${group.machines} מכונות</span>
      <div class="spacer">
        ${type.grouped ? `
          <button class="btn" data-rename-group="${esc(group.id)}">שינוי שם</button>
          <button class="btn danger" data-del-group="${esc(group.id)}">מחיקה</button>` : ""}
      </div>
    </summary>
    <table>
      <thead><tr><th>MAC</th><th>השם שניתן</th><th></th></tr></thead>
      <tbody>${rows || `<tr><td colspan="3">אין מכונות עדיין.</td></tr>`}</tbody>
    </table>
    <form class="pad form-grid" data-add-form="${esc(group.id)}">
      <div class="row">
        <input type="text" class="mono a-mac" dir="ltr" placeholder="00:00:5e:07:1a:c4" required>
        <input type="text" class="a-name" placeholder="${esc(type.nameLabel)}" required>
        <button class="btn primary" type="submit">הוסף מכונה</button>
      </div>
      <p class="error"></p>
    </form>
    <details class="paste">
      <summary>ייבוא בהדבקה — הרבה שורות בבת אחת, וייצוא CSV</summary>
      <div class="inner">
        <textarea rows="6" class="mono i-text" dir="ltr"
          placeholder="00:00:5e:07:1a:c4 01&#10;00-00-5E-07-1A-C5 02"></textarea>
        <div class="row">
          <button class="btn" type="button" data-preview="${esc(group.id)}">תצוגה מקדימה</button>
          <button class="btn primary" type="button" data-save="${esc(group.id)}">שמור</button>
          <a class="btn" href="/api/console/machines.csv" download>ייצוא CSV</a>
        </div>
        <div class="i-result"></div>
      </div>
    </details>
  </details>`;
}

function wireGroup(group, type) {
  const block = document.querySelector(`[data-block="${group.id}"]`);
  if (!block) return;

  block.addEventListener("toggle", () => {
    if (block.open) MACHINES.open.add(group.id);
    else MACHINES.open.delete(group.id);
  });
  // כפתורי הכותרת יושבים בתוך ה-summary, ולכן קליק עליהם היה
  // מקפל/פותח את הקבוצה בדרך למודאל.
  block.querySelectorAll("summary .btn").forEach((b) =>
    b.addEventListener("click", (event) => event.preventDefault()));

  block.querySelectorAll("[data-edit]").forEach((b) => b.onclick = () => sheet({
    title: "עריכת מכונה", sub: b.dataset.edit,
    fields: [{ id: "name", label: type.nameLabel }],
    onSubmit: async (v) => {
      await put(`/machines/${b.dataset.edit}`, { name: v.name });
      await loadMachinesTab();
    },
  }));
  block.querySelectorAll("[data-remove]").forEach((b) => b.onclick = () => confirmSheet(
    "מחיקת מכונה", `${b.dataset.remove} תוסר מהטבלה. באתחול הבא היא תדווח כלא רשומה.`,
    "מחק", async () => { await del(`/machines/${b.dataset.remove}`); await loadMachinesTab(); }));

  const renameButton = block.querySelector("[data-rename-group]");
  if (renameButton) renameButton.onclick = () => sheet({
    title: "שינוי שם הקבוצה",
    fields: [{ id: "label", label: "השם החדש", value: group.label }],
    onSubmit: async (v) => { await put(`/groups/${group.id}`, { label: v.label }); await loadMachinesTab(); },
  });
  const deleteButton = block.querySelector("[data-del-group]");
  if (deleteButton) deleteButton.onclick = () => confirmSheet(
    "מחיקת קבוצה", `"${group.label}" תימחק על כל ${group.machines} המכונות שבה.`,
    "מחק את הקבוצה",
    async () => { await del(`/groups/${group.id}`); await loadMachinesTab(); });

  block.querySelector("[data-add-form]").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    try {
      await post("/machines", {
        mac: form.querySelector(".a-mac").value,
        name: form.querySelector(".a-name").value,
        group_id: group.id,
      });
      await loadMachinesTab();
    } catch (error) { form.querySelector(".error").textContent = error.message; }
  });

  const resultHtml = (lines) => lines.map((l) => l.error
    ? `<div class="badline mono" dir="ltr">${esc(l.raw)} ← ${esc(l.error)}</div>`
    : `<div class="goodline mono" dir="ltr">${esc(l.mac)} ${esc(l.suffix)} ✓</div>`).join("");
  const text = () => block.querySelector(".i-text").value;
  const result = block.querySelector(".i-result");

  block.querySelector("[data-preview]").onclick = async () => {
    const r = await post("/machines/import", { group_id: group.id, text: text(), dry_run: true });
    result.innerHTML = resultHtml(r.preview);
  };
  block.querySelector("[data-save]").onclick = async () => {
    const r = await post("/machines/import", { group_id: group.id, text: text() });
    result.innerHTML = `<div>נשמרו ${r.saved}. נדחו ${r.rejected.length}.</div>` + resultHtml(r.rejected);
    await loadMachinesTab();
  };
}

function addGroupSheet() {
  sheet({
    title: "קבוצת כיתה חדשה",
    sub: "השם חופשי — עברית, אנגלית או מספרים.",
    fields: [
      { id: "label", label: "שם הקבוצה", placeholder: "למשל: כיתה 303 סייבר" },
      { id: "id", label: "מזהה קצר באנגלית (לא חובה — נגזר מהשם)",
        placeholder: "LAB1", dir: "ltr" },
    ],
    submitLabel: "צור קבוצה",
    onSubmit: async (v) => {
      await post("/groups", {
        id: v.id.trim() ? "grp_" + v.id.trim() : "",
        label: v.label, role: "classroom",
      });
      await loadMachinesTab();
    },
  });
}
