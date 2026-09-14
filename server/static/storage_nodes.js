/* ImageCtl — לשונית "העברה בין סניפים" (#655/#727): ניהול מרשם המשניים.
   קבוצות (יצירה/שם/סדר/מחיקה) והמשניים הרשומים (שם, שיוך לקבוצה,
   השבתה/הפעלה, מחיקה). הוספת משני היא enrollment — שלב נפרד בשרשרת,
   ולכן אין כאן "הוסף משני". משתמש ב-api/post/put/del/sheet/toast מ-console.js. */
"use strict";

async function loadBranches() {
  const [groups, nodes] = await Promise.all([
    api("/storage-node-groups"),
    api("/storage-nodes"),
  ]);
  $("#branches-body").innerHTML = `
    <div class="panel wide">
      <div class="chead">
        <div>
          <h2>שרתים משניים</h2>
          <p>השרתים שהראשי הזה מחזיק. הוספת שרת משני נעשית ברישום
            (כתובת ואישורי מנהל) — כאן מנהלים את הרשומות הקיימות.</p>
        </div>
      </div>
      <div id="branch-nodes"></div>
    </div>
    <div class="panel wide">
      <div class="chead">
        <div><h2>קבוצות סניפים</h2><p>ארגון המשניים לקבוצות תצוגה.</p></div>
        <button class="btn" id="add-branch-group">+ קבוצה חדשה</button>
      </div>
      <div id="branch-groups"></div>
    </div>`;
  renderBranchNodes(nodes, groups);
  renderBranchGroups(groups);
}

function branchGroupName(nodes, groups) {
  const byId = Object.fromEntries(groups.map((g) => [g.id, g.label]));
  return (id) => (id && byId[id]) || "ללא קבוצה";
}

/* ---------- המשניים הרשומים ---------- */

function renderBranchNodes(nodes, groups) {
  const nameOf = branchGroupName(nodes, groups);
  const rows = nodes.map((n) => {
    const disabled = !!n.disabled_at;
    return `<tr class="${disabled ? "row-off" : ""}">
      <td><b>${esc(n.label)}</b>${disabled
        ? ' <span class="tag danger">מושבת</span>' : ""}</td>
      <td class="mono" dir="ltr">${esc(n.base_url)}</td>
      <td>${esc(nameOf(n.group_id))}</td>
      <td>
        <button class="btn" data-node-edit="${esc(n.id)}">עריכה</button>
        <button class="btn" data-node-toggle="${esc(n.id)}"
          data-off="${disabled ? "1" : ""}">${disabled ? "הפעל" : "השבת"}</button>
        <button class="btn danger" data-node-del="${esc(n.id)}"
          data-name="${esc(n.label)}">מחק</button>
      </td></tr>`;
  }).join("");

  $("#branch-nodes").innerHTML = nodes.length ? `<table>
      <thead><tr><th>שם</th><th>כתובת</th><th>קבוצה</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table>`
    : `<div class="pad sub">אין עדיין שרתים משניים רשומים.</div>`;

  const options = [{ value: "", label: "ללא קבוצה" }]
    .concat(groups.map((g) => ({ value: g.id, label: g.label })));

  document.querySelectorAll("[data-node-edit]").forEach((b) => b.onclick = () => {
    const node = nodes.find((n) => n.id === b.dataset.nodeEdit);
    sheet({
      title: "עריכת שרת משני", sub: node.base_url,
      fields: [
        { id: "label", label: "שם", value: node.label },
        { id: "group_id", label: "קבוצה", type: "select",
          value: node.group_id || "", options },
      ],
      onSubmit: async (v) => {
        await put(`/storage-nodes/${node.id}`,
                  { label: v.label, group_id: v.group_id || null });
        await loadBranches();
        toast("נשמר.");
      },
    });
  });

  document.querySelectorAll("[data-node-toggle]").forEach((b) => b.onclick =
    async () => {
      try {
        await post(`/storage-nodes/${b.dataset.nodeToggle}/disabled`,
                   { disabled: !b.dataset.off });
        await loadBranches();
      } catch (error) { toast(error.message); }
    });

  document.querySelectorAll("[data-node-del]").forEach((b) => b.onclick = () =>
    confirmSheet("הסרת שרת משני",
      `"${b.dataset.name}" יוסר מהמרשם. הרישום מחדש דורש כתובת ואישורים.`,
      "הסר",
      async () => { await del(`/storage-nodes/${b.dataset.nodeDel}`); await loadBranches(); }));
}

/* ---------- קבוצות הסניפים ---------- */

function renderBranchGroups(groups) {
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

  $("#add-branch-group").onclick = () => sheet({
    title: "קבוצת סניפים חדשה",
    fields: [{ id: "label", label: "שם הקבוצה", placeholder: "למשל: צפון" }],
    submitLabel: "צור קבוצה",
    onSubmit: async (v) => { await post("/storage-node-groups", { label: v.label }); await loadBranches(); },
  });

  document.querySelectorAll("[data-grp-rename]").forEach((b) => b.onclick = () => {
    const g = groups.find((x) => x.id === b.dataset.grpRename);
    sheet({
      title: "שינוי שם הקבוצה",
      fields: [{ id: "label", label: "השם החדש", value: g.label }],
      onSubmit: async (v) => { await put(`/storage-node-groups/${g.id}`, { label: v.label }); await loadBranches(); },
    });
  });

  document.querySelectorAll("[data-grp-del]").forEach((b) => b.onclick = () =>
    confirmSheet("מחיקת קבוצה",
      `"${b.dataset.name}" תימחק. ${b.dataset.count} המשניים שבה יישארו, בלי קבוצה.`,
      "מחק את הקבוצה",
      async () => { await del(`/storage-node-groups/${b.dataset.grpDel}`); await loadBranches(); }));

  // סדר הקבוצות בגרירה — רק כשיש יותר מאחת.
  if (groups.length > 1) {
    enableLongPressReorder({
      container: $("#branch-groups"),
      itemSelector: ".group-block",
      handleSelector: ".group-head",
      onDrop: async (ids) => {
        try { await post("/storage-node-groups/order", { ids }); toast("הסדר נשמר."); }
        catch (error) { toast(error.message); }
        await loadBranches();
      },
    });
    $("#branch-groups").classList.add("can-reorder");
  }
}
