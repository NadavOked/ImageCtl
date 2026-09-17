/* ImageCtl — קבוצות הסניפים (#655/#727), לשונית "קבוצות" בדף "סניפים"
   (#954 גל 7, branches.js). ניהול הקבוצות עצמן (יצירה/שם/סדר/מחיקה)
   בלבד — רשומות המשניים (שם/קבוצה/השבתה/מחיקה) עברו לטבלה בלשונית
   "שרתים" של branches.js, ואין כאן עוד "הוספת משני": הוספה היא
   enrollment (openEnrollSheet ב-branches.js). משתמש ב-api/post/put/del/
   sheet/toast/esc/confirmSheet/enableLongPressReorder מ-console.js. */
"use strict";

function renderBranchGroups(groups, afterChange) {
  const rows = groups.map((g) => `<div class="group-block" data-reorder-id="${esc(g.id)}">
    <div class="group-head">
      <span class="caret">≡</span>
      <h3>${esc(g.label)}</h3>
      <span class="pill">${g.nodes} משניים</span>
      <div class="spacer">
        <button class="btn" data-grp-rename="${esc(g.id)}">שינוי שם</button>
        <button class="btn danger" data-grp-del="${esc(g.id)}"
          data-name="${esc(g.label)}" data-count="${g.nodes}">מחיקה</button>
      </div>
    </div></div>`).join("");
  $("#branch-groups").innerHTML = groups.length ? rows
    : `<div class="pad sub">אין עדיין קבוצות.</div>`;

  const addBtn = $("#add-branch-group");
  if (addBtn) addBtn.onclick = () => sheet({
    title: "קבוצת סניפים חדשה",
    fields: [{ id: "label", label: "שם הקבוצה", placeholder: "למשל: צפון" }],
    submitLabel: "צור קבוצה",
    onSubmit: async (v) => { await post("/storage-node-groups", { label: v.label }); await afterChange(); },
  });

  document.querySelectorAll("[data-grp-rename]").forEach((b) => b.onclick = () => {
    const g = groups.find((x) => x.id === b.dataset.grpRename);
    sheet({
      title: "שינוי שם הקבוצה",
      fields: [{ id: "label", label: "השם החדש", value: g.label }],
      onSubmit: async (v) => { await put(`/storage-node-groups/${g.id}`, { label: v.label }); await afterChange(); },
    });
  });

  document.querySelectorAll("[data-grp-del]").forEach((b) => b.onclick = () =>
    confirmSheet("מחיקת קבוצה",
      `"${b.dataset.name}" תימחק. ${b.dataset.count} המשניים שבה יישארו, בלי קבוצה.`,
      "מחק את הקבוצה",
      async () => { await del(`/storage-node-groups/${b.dataset.grpDel}`); await afterChange(); }));

  // סדר הקבוצות בגרירה — רק כשיש יותר מאחת.
  if (groups.length > 1) {
    enableLongPressReorder({
      container: $("#branch-groups"),
      itemSelector: ".group-block",
      handleSelector: ".group-head",
      onDrop: async (ids) => {
        try { await post("/storage-node-groups/order", { ids }); toast("הסדר נשמר."); }
        catch (error) { toast(error.message); }
        await afterChange();
      },
    });
    $("#branch-groups").classList.add("can-reorder");
  }
}
