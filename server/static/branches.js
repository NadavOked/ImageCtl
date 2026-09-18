/* ImageCtl — #954 גל 7: דף "סניפים" (רשימת המשניים) ודף "שרת משני" (הצומת
   בעץ, #936). לפי docs/design/console-redesign/branches.md.

   "סניפים" הוא דף "own" (UI.objHeader/datagrid כמו drivers.js): כותרת עם
   מונים ותג "N לא מגיב/ים" · לשוניות שרתים/העברות/קבוצות. חיבור כל שרת
   **נמדד** בפועל מול ה-machines endpoint של המשני, ולא נשאר "בודק
   חיבור…" קפוא (עיקרון 5) — אחרי שהטבלה על המסך, כל שרת נבדק במקביל
   ותא ה"חיבור" שלו מתעדכן במקום.

   "שרת משני" (branch, BRANCH_NODE) נשאר בדפוס הישן (לא "own" — הלשוניות
   שלו טוענות נתונים שונים כל אחת) עם כותרת אובייקט/KPI/note שנוספו כאן;
   ‏loadBranchMachines/renderBranchTransfers/openTransferSheet משותפים
   לשני הדפים. קבוצות הסניפים (יצירה/שם/סדר/מחיקה) נשארות ב-storage_nodes.js
   ומוצגות כלשונית "קבוצות" כאן. משתמש ב-UI/api/post/put/del/sheet/toast/
   esc/encodeId/fmtDate/fmtClock/formatGB/confirmSheet מ-console.js. */
"use strict";

let BRANCH_TIMER = null;          // פולינג העברות בדף שרת משני יחיד (branch)
let BRANCHES_LIST_TIMER = null;   // פולינג לשונית "העברות" בדף הסניפים
let BRANCH_TRANSFERS = [];
let BRANCH_GROUPS = [];
let BRANCH_NODES = [];
let BRANCH_CONN = {};   // id → {state:"checking"|"ok"|"err"|"disabled", error, machines, checkedAt}

const TRANSFER_STATE = {
  queued: ["ממתין", ""], sending: ["שולח", ""], verifying: ["המשני מאמת sha256", ""],
  done: ["הושלם", "ok"], failed: ["נכשל", "err"],
};

function stopBranchPolling() { clearInterval(BRANCH_TIMER); BRANCH_TIMER = null; }
function stopBranchesListPolling() { clearInterval(BRANCHES_LIST_TIMER); BRANCHES_LIST_TIMER = null; }

/* ---------- דף "סניפים" (רשימת המשניים, own) ---------- */

async function loadBranchesData() {
  stopBranchesListPolling();
  const [groups, nodes, transfers] = await Promise.all([
    api("/storage-node-groups"), api("/storage-nodes"), api("/storage-transfers"),
  ]);
  BRANCH_GROUPS = groups;
  BRANCH_NODES = nodes;
  BRANCH_TRANSFERS = transfers;
  for (const n of nodes) {
    BRANCH_CONN[n.id] = n.disabled_at ? { state: "disabled" } : { state: "checking" };
  }
  if (current === "branches") renderCurrent();
  await Promise.all(nodes.filter((n) => !n.disabled_at).map((n) =>
    checkBranchConnection(n).catch(() => {})));
  maybeStartBranchesListPolling();
}

async function checkBranchConnection(n) {
  let result;
  try {
    const answer = await api(`/storage-nodes/${encodeId(n.id)}/machines`);
    result = answer.connected
      ? { state: "ok", machines: answer.machines, checkedAt: new Date() }
      : { state: "err", error: answer.error || "", checkedAt: new Date() };
  } catch (e) {
    result = { state: "err", error: e.message, checkedAt: new Date() };
  }
  BRANCH_CONN[n.id] = result;
  markSecondaryStatus(n, result.state === "ok", result.error);
  const connCell = $(`#branch-row-conn-${CSS.escape(n.id)}`);
  const machinesCell = $(`#branch-row-machines-${CSS.escape(n.id)}`);
  if (connCell) { const st = branchStatusHtml(n.id); connCell.className = st.cls; connCell.innerHTML = st.html; }
  if (machinesCell) machinesCell.textContent = result.state === "ok" ? String(result.machines.length) : "—";
  if (current === "branches" && currentTab === 0) updateAlertBadge();
}

/* "בודק חיבור…" רק לפני המדידה הראשונה; אחריה — "מחובר" או "לא ענה" +
   הסיבה ושעת המדידה (HH:MM, שעון הדפדפן) — לא "בודק…" שלעולם לא מתעדכן. */
function branchStatusHtml(nid) {
  const c = BRANCH_CONN[nid] || { state: "checking" };
  if (c.state === "disabled") return { cls: "status", html: "<i></i>מושבת" };
  if (c.state === "checking") return { cls: "status", html: "<i></i>בודק חיבור…" };
  if (c.state === "ok") return { cls: "status ok", html: "<i></i>מחובר" };
  const hhmm = c.checkedAt instanceof Date
    ? String(c.checkedAt.getHours()).padStart(2, "0") + ":" + String(c.checkedAt.getMinutes()).padStart(2, "0") : "";
  return { cls: "status err", html: `<i></i>לא ענה${hhmm ? " — " + esc(hhmm) : ""}${c.error ? `<span class="sub">${esc(c.error)}</span>` : ""}` };
}

function branchNodeRow(n) {
  const disabled = !!n.disabled_at;
  const st = branchStatusHtml(n.id);
  const c = BRANCH_CONN[n.id] || {};
  const machinesCell = c.state === "ok" ? String(c.machines.length) : "—";
  const done = BRANCH_TRANSFERS.filter((t) => t.node_id === n.id && t.state === "done");
  const seen = new Set();
  const transferred = done.filter((t) => !seen.has(t.image_id) && seen.add(t.image_id)).length;
  const last = BRANCH_TRANSFERS.find((t) => t.node_id === n.id);   // כבר ממוין חדש→ישן
  const lastCell = last
    ? `${esc(last.image_name)}<span class="sub mono" dir="ltr">${esc(fmtDate(last.created_at))} ${esc(fmtClock(last.created_at))}</span>`
    : `<span class="muted">—</span>`;
  const idEnc = encodeId(n.id);
  const acts = UI.acts([
    ["פתח", `openBranchView('${idEnc}',0,null)`],
    ["העבר אימג'", `openTransferSheetForNode('${idEnc}')`],
    ["עריכה", `editBranchNode('${idEnc}')`],
    [disabled ? "הפעל" : "השבת", `toggleBranchNode('${idEnc}')`],
    ["הסר", `removeBranchNode('${idEnc}')`],
  ]);
  return { attrs: `data-node-row="${esc(n.id)}"${disabled ? ' class="row-off"' : ""}`, cells: [
    UI.nameHtml(n.label, `<span class="mono muted" dir="ltr">${esc(n.node_id || "—")}</span> · נרשם ${esc(fmtDate(n.enrolled_at))}`),
    `<span class="mono" dir="ltr">${esc(n.base_url)}</span>`,
    esc(n.group_label || "ללא קבוצה"),
    `<span class="${st.cls}" id="branch-row-conn-${esc(n.id)}">${st.html}</span>`,
    `<span id="branch-row-machines-${esc(n.id)}">${machinesCell}</span>`,
    String(transferred),
    lastCell,
    acts,
  ] };
}

function branchesServersTab() {
  const table = UI.datagrid({
    cls: "acts-on",
    columns: ["שרת", "כתובת", "קבוצה", "חיבור", "מחשבים", "אימג'ים שהועברו", "העברה אחרונה", ""],
    rows: BRANCH_NODES.map(branchNodeRow),
    empty: "אין שרתים משניים רשומים.",
  });
  return `<div class="c12 card"><div class="card-b${BRANCH_NODES.length ? " flush" : ""}">${table}</div></div>`;
}

function transferDirectionHtml(t) {
  const primary = (typeof ME !== "undefined" && ME.server_name) ? ME.server_name : "ראשי";
  const node = t.node_label || t.node_id || "";
  if (t.direction === "pull") return `${esc(node)} → ${esc(primary)}`;
  return esc(node);
}

function branchTransferRow(t) {
  const [label, cls] = TRANSFER_STATE[t.state] || [t.state, ""];
  const active = ["sending", "verifying", "queued"].includes(t.state);
  const pct = t.bytes_total ? Math.min(100, Math.round(100 * t.bytes_sent / t.bytes_total)) : 0;
  const progress = active
    ? UI.barRow(pct, "", `${esc(formatGB(t.bytes_sent))} / ${esc(formatGB(t.bytes_total))}`)
    : (t.state === "done" ? UI.barRow(100) : UI.barRow(null));
  const state = UI.status(cls, label) + (t.error ? `<div class="sub">${esc(t.error)}</div>` : "");
  const retry = t.state === "failed"
    ? `<button class="btn sm" onclick="retryBranchTransfer('${encodeId(t.node_id)}','${encodeId(t.image_id)}')">נסה שוב</button>` : "";
  const cancel = t.direction === "pull" && active
    ? `<button class="btn sm" onclick="cancelBranchTransfer('${encodeId(t.id)}')">ביטול</button>` : "";
  return { cells: [
    UI.name(t.image_name, t.image_id),
    transferDirectionHtml(t),
    progress,
    state,
    `<span class="mono" dir="ltr">${esc(fmtDate(t.created_at))} ${esc(fmtClock(t.created_at))}</span>`,
    esc(t.started_by || "—"),
    retry + cancel,
  ] };
}

function branchesTransfersTab() {
  const table = UI.datagrid({
    columns: ["אימג'", "אל", "התקדמות", "מצב", "התחיל", "מי", ""],
    rows: BRANCH_TRANSFERS.map(branchTransferRow),
    empty: "אין העברות.",
  });
  return `<div class="c12 card"><div class="card-b${BRANCH_TRANSFERS.length ? " flush" : ""}">${table}</div></div>`;
}

function maybeStartBranchesListPolling() {
  if (!BRANCH_TRANSFERS.some((t) => ["queued", "sending", "verifying"].includes(t.state))) return;
  stopBranchesListPolling();
  BRANCHES_LIST_TIMER = setInterval(() => {
    if (current !== "branches" || currentTab !== 1 || document.hidden) { stopBranchesListPolling(); return; }
    api("/storage-transfers").then((rows) => {
      BRANCH_TRANSFERS = rows;
      renderCurrent();
      if (!rows.some((t) => ["queued", "sending", "verifying"].includes(t.state))) stopBranchesListPolling();
    }).catch(() => {});
  }, 2000);
}

async function retryBranchTransfer(nidEnc, imageIdEnc) {
  let nid = nidEnc, image_id = imageIdEnc;
  try { nid = decodeURIComponent(nid); image_id = decodeURIComponent(image_id); } catch (e) {}
  try {
    const row = BRANCH_TRANSFERS.find((t) => t.node_id === nid && t.image_id === image_id);
    const kind = row && row.direction === "pull" ? "pull" : "transfer";
    await post(`/storage-nodes/${encodeId(nid)}/${kind}`, { image_id });
    toast("ההעברה הופעלה מחדש");
    BRANCH_TRANSFERS = await api("/storage-transfers");
    renderCurrent();
    maybeStartBranchesListPolling();
  } catch (e) { toast(e.message); }
}

async function cancelBranchTransfer(tidEnc) {
  let tid = tidEnc;
  try { tid = decodeURIComponent(tid); } catch (e) {}
  try {
    await post(`/storage-transfers/${encodeId(tid)}/cancel`, {});
    toast("ההעברה בוטלה");
    BRANCH_TRANSFERS = await api("/storage-transfers");
    renderCurrent();
  } catch (e) { toast(e.message); }
}

async function startPullFromSecondary(nidEnc, imageIdEnc) {
  let nid = nidEnc, image_id = imageIdEnc;
  try { nid = decodeURIComponent(nid); image_id = decodeURIComponent(image_id); } catch (e) {}
  try {
    await post(`/storage-nodes/${encodeId(nid)}/pull`, { image_id });
    toast("ההעברה לראשי התחילה");
    if (current === "branch") activateTab(BRANCH_VIEWS.findIndex((v) => v[0] === "transfers"));
    else {
      BRANCH_TRANSFERS = await api("/storage-transfers");
      renderCurrent();
      maybeStartBranchesListPolling();
    }
  } catch (e) { toast(e.message); }
}

function branchesGroupsTab() {
  return `<div class="c12 card"><div class="chead"><div><h2>קבוצות סניפים</h2><p>ארגון המשניים לקבוצות תצוגה.</p></div>${isAdmin() ? `<button class="btn" id="add-branch-group">+ קבוצה חדשה</button>` : ""}</div><div id="branch-groups"></div></div>`;
}

/* מתעדכנת לאחר כל רינדור של לשונית "קבוצות" — renderBranchGroups
   (storage_nodes.js) כותבת ל-#branch-groups ומחברת את הכפתורים/הגרירה. */
function wireBranchesGroupsTab() {
  if (current !== "branches" || currentTab !== 2) return;
  renderBranchGroups(BRANCH_GROUPS, loadBranchesData);
}

function branchesPage(tab = 0) {
  const nodes = BRANCH_NODES || [];
  const groups = BRANCH_GROUPS || [];
  const notResponding = nodes.filter((n) => (BRANCH_CONN[n.id] || {}).state === "err").length;
  const sub = `${nodes.length} ${nodes.length === 1 ? "שרת משני" : "שרתים משניים"} · ${groups.length} ${groups.length === 1 ? "קבוצה" : "קבוצות"}`;
  const pill = notResponding ? UI.pill("err", `${notResponding} ${notResponding === 1 ? "לא מגיב" : "לא מגיבים"}`) : "";
  const actions = isAdmin()
    ? `<button class="btn primary" onclick="openEnrollSheet()">+ רישום שרת משני</button><button class="btn" onclick="openBranchGroupSheet()">+ קבוצה</button>`
    : "";
  const header = UI.objHeader({
    crumbs: [{ label: "שרת אימג'ים", onclick: "selectPageById('home')" }, { label: "סניפים" }],
    icon: "server", name: "סניפים", sub, pill, actions,
    tabs: ["שרתים", "העברות", "קבוצות"], tab,
  });
  const body = tab === 1 ? branchesTransfersTab() : tab === 2 ? branchesGroupsTab() : branchesServersTab();
  return `<div class="page">${header}<div class="body">${body}</div></div>`;
}

/* ---------- פעולות על משני מהטבלה (עריכה/השבתה/הסרה) ---------- */

function editBranchNode(idEnc) {
  let id = idEnc; try { id = decodeURIComponent(id); } catch (e) {}
  const node = BRANCH_NODES.find((n) => n.id === id);
  if (!node) return;
  const options = [{ value: "", label: "ללא קבוצה" }].concat(BRANCH_GROUPS.map((g) => ({ value: g.id, label: g.label })));
  sheet({
    title: "עריכת שרת משני", sub: node.base_url,
    fields: [
      { id: "label", label: "שם", value: node.label },
      { id: "group_id", label: "קבוצה", type: "select", value: node.group_id || "", options },
    ],
    onSubmit: async (v) => {
      await put(`/storage-nodes/${encodeId(node.id)}`, { label: v.label, group_id: v.group_id || null });
      toast("נשמר.");
      await loadBranchesData();
    },
  });
}

async function toggleBranchNode(idEnc) {
  let id = idEnc; try { id = decodeURIComponent(id); } catch (e) {}
  const node = BRANCH_NODES.find((n) => n.id === id);
  if (!node) return;
  try {
    await post(`/storage-nodes/${encodeId(id)}/disabled`, { disabled: !node.disabled_at });
    await loadBranchesData();
  } catch (e) { toast(e.message); }
}

function removeBranchNode(idEnc) {
  let id = idEnc; try { id = decodeURIComponent(id); } catch (e) {}
  const node = BRANCH_NODES.find((n) => n.id === id);
  if (!node) return;
  sheet({
    title: "הסרת שרת משני",
    sub: `"${node.label}" יוסר מהמרשם. הרישום מחדש דורש כתובת ואישורים.`,
    danger: true, submitLabel: "הסר",
    verify: { label: "להמשך יש להקליד את שם השרת:", mustEqual: node.label },
    onSubmit: async () => { await del(`/storage-nodes/${encodeId(id)}`); await loadBranchesData(); },
  });
}

function openTransferSheetForNode(idEnc) {
  let id = idEnc; try { id = decodeURIComponent(id); } catch (e) {}
  const node = BRANCH_NODES.find((n) => n.id === id);
  if (!node) return;
  api("/images").then((images) => openTransferSheet(node, images, loadBranchesData)).catch((e) => toast(e.message));
}

/* ---------- רישום שרת משני (enroll): כתובת → preview SPKI → קוד ----------
   ‏POST /storage-nodes/enroll/preview קורא את ה-SPKI של המשני שבכתובת,
   ורק אחרי שהמנהל השווה אותו למה שמוצג על המשני עצמו הוא מקליד את הקוד
   החד-פעמי; ‏POST /storage-nodes/enroll מצמיד את ה-SPKI מהשלב הקודם
   (לא מהצהרת ה-JSON של המשני — #883). גרסת הפרוטוקול קבועה כמו בשרת
   (interserver_auth.PROTOCOL_VERSION). */
const ENROLL_PROTOCOL_VERSION = "2.1";

function openEnrollSheet() {
  const options = [{ value: "", label: "ללא קבוצה" }].concat(BRANCH_GROUPS.map((g) => ({ value: g.id, label: g.label })));
  sheet({
    title: "רישום שרת משני — שלב 1 מתוך 2: כתובת",
    sub: "כתובת ה-API הבין-שרתי של המשני (mTLS). השלב הבא יציג את טביעת ה-TLS שלו (SPKI) — יש להשוות אותה למה שהמשני עצמו מציג, לפני שליחת הקוד.",
    fields: [
      { id: "url", label: "כתובת (base_url)", value: "https://", dir: "ltr" },
      { id: "label", label: "שם" },
      { id: "group_id", label: "קבוצה", type: "select", value: "", options },
    ],
    submitLabel: "בדוק SPKI",
    onSubmit: async (v) => {
      const preview = await post("/storage-nodes/enroll/preview", { url: v.url });
      openEnrollConfirmSheet(v, preview);
    },
  });
}

function openEnrollConfirmSheet(step1, preview) {
  sheet({
    title: "רישום שרת משני — שלב 2 מתוך 2: אישור והקוד",
    sub: "השווה את ה-SPKI מול מה שמוצג על המשני, ורק אז הקלד את הקוד החד-פעמי שהוצג שם.",
    note: `<div class="detail-grid"><div class="detail-box"><span class="k">SPKI</span><span class="v mono" dir="ltr">${esc(preview.secondary_spki)}</span></div><div class="detail-box"><span class="k">מזהה משני</span><span class="v mono" dir="ltr">${esc(preview.node_id)}</span></div></div>`,
    fields: [{ id: "code", label: "קוד חד-פעמי (מוצג על המשני)" }],
    submitLabel: "רשום שרת משני",
    onSubmit: async (v) => {
      await post("/storage-nodes/enroll", {
        url: step1.url, protocol_version: ENROLL_PROTOCOL_VERSION, code: v.code,
        expected_secondary_spki: preview.secondary_spki,
        label: step1.label, group_id: step1.group_id || null,
      });
      toast(`השרת המשני "${step1.label}" נרשם.`);
      await loadBranchesData();
    },
  });
}

function openBranchGroupSheet() {
  sheet({
    title: "קבוצת סניפים חדשה",
    fields: [{ id: "label", label: "שם הקבוצה", placeholder: "למשל: צפון" }],
    submitLabel: "צור קבוצה",
    onSubmit: async (v) => { await post("/storage-node-groups", { label: v.label }); await loadBranchesData(); },
  });
}

/* ---------- משותף לשני הדפים: מכונות המשני, העברת אימג' ---------- */

async function loadBranchMachines(nid) {
  const box = $(`#branch-machines-${CSS.escape(nid)}`);
  const status = $(`#branch-status-${CSS.escape(nid)}`);
  if (!box) return;
  const answer = await api(`/storage-nodes/${encodeId(nid)}/machines`);
  renderBranchMachines(nid, answer, box, status);
}

function renderBranchMachines(nid, answer, box, status) {
  if (!answer.connected) {
    if (status) { status.innerHTML = `<i></i>לא מחובר`; status.className = "status err"; }
    box.innerHTML = `<div class="notice warn" role="status">השרת המשני לא ענה: ${esc(answer.error || "")}</div>`;
    return;
  }
  if (status) { status.innerHTML = `<i></i>מחובר`; status.className = "status ok"; }
  const roleLabel = { build: "מחשב בנייה", cloner: "מחשב שיכפול" };
  if (!answer.machines.length) {
    box.innerHTML = `<div class="empty">אין מחשבי בנייה או שיכפול רשומים בסניף.</div>`;
    return;
  }
  box.innerHTML = `<table><thead><tr><th>שם</th><th>תפקיד</th><th>IP</th><th>מצב</th><th></th></tr></thead><tbody>${
    answer.machines.map((m) => `<tr>
      <td><b>${esc(m.name || m.mac)}</b><br><span class="mono muted" dir="ltr">${esc(m.mac)}</span></td>
      <td>${esc(roleLabel[m.role] || m.role)}</td>
      <td class="mono" dir="ltr">${m.ip ? esc(m.ip) : "—"}</td>
      <td>${m.online ? `<span class="status ok"><i></i>מחובר</span>` : `<span class="status"><i></i>לא מחובר</span>`}</td>
      <td><button class="btn" data-branch-monitor="${esc(m.mac)}" data-name="${esc(m.name || m.mac)}" ${m.online ? "" : `disabled title="המכונה אינה מחוברת"`}>מוניטור</button></td>
    </tr>`).join("")}</tbody></table>`;
  box.querySelectorAll("[data-branch-monitor]").forEach((b) => b.onclick = () =>
    branchMonitor(nid, b.dataset.branchMonitor, b.dataset.name));
}

function branchMonitor(nid, mac, name) {
  if (!isAdmin()) return;
  window.open(`monitor.html?node=${encodeId(nid)}&mac=${encodeId(mac)}&name=${encodeId(name)}`,
              `imagectl-monitor-${nid}-${mac}`);
}

function renderBranchTransfers(nid, target = null, limit = 8) {
  const box = target || $(`#branch-transfers-${CSS.escape(nid)}`);
  if (!box) return;
  const rows = BRANCH_TRANSFERS.filter((t) => t.node_id === nid).slice(0, limit);
  if (!rows.length) { box.innerHTML = `<div class="sub">אין העברות.</div>`; return; }
  box.innerHTML = rows.map((t) => {
    const [label, cls] = TRANSFER_STATE[t.state] || [t.state, ""];
    const pct = t.bytes_total ? Math.min(100, Math.round(100 * t.bytes_sent / t.bytes_total)) : 0;
    const bar = ["sending", "verifying", "queued"].includes(t.state)
      ? `<div class="progress" style="margin-top:6px"><i style="width:${pct}%"></i></div><span class="sub">${esc(formatGB(t.bytes_sent))} מתוך ${esc(formatGB(t.bytes_total))} (${pct}%)</span>`
      : "";
    const err = t.error ? `<div class="notice warn" role="status">${esc(t.error)}</div>` : "";
    const dir = t.direction === "pull" ? transferDirectionHtml(t) : "";
    const cancel = t.direction === "pull" && ["sending", "verifying", "queued"].includes(t.state)
      ? `<button class="btn sm" onclick="cancelBranchTransfer('${encodeId(t.id)}')">ביטול</button>` : "";
    return `<div class="member"><b>${esc(t.image_name)}</b> <span class="status ${cls}"><i></i>${esc(label)}</span>
      ${dir ? `<div class="sub">${dir}</div>` : ""}
      <div class="sub mono" dir="ltr">${esc((t.created_at || "").replace("T", " ").slice(0, 19))}</div>${bar}${err}${cancel}</div>`;
  }).join("");
}

function openTransferSheet(n, images, afterSubmit) {
  if (!images.length) { toast("אין אימג'ים בספרייה להעברה"); return; }
  const options = images.map((m) => ({
    value: m.id, label: `${m.folder ? m.folder + " / " : ""}${m.name} (${formatGB(m.total_compressed_bytes)})`,
  }));
  sheet({
    title: `העברת אימג' אל ${n.label}`,
    sub: "האימג' ייכנס לספריית הסניף רק אחרי שהסניף אימת sha256 של כל מחיצה. ניתוק באמצע = כשל גלוי, ומתחילים מחדש.",
    fields: [{ id: "image_id", label: "אימג'", type: "select", value: options[0].value, options }],
    submitLabel: "התחל העברה",
    onSubmit: async (v) => {
      await post(`/storage-nodes/${encodeId(n.id)}/transfer`, { image_id: v.image_id });
      toast("ההעברה התחילה");
      await afterSubmit();
    },
  });
}

/* ---------- #936: הדף של משני אחד (צומת השרת בעץ) ---------- */

let BRANCH_VIEW_GEN = 0;

async function loadBranchView(view) {
  const host = $("#branch-view");
  if (!host) return;
  stopBranchPolling();
  // מעבר מהיר בין לשוניות/משניים (openBranchView מרנדר פעמיים: הדף ואז
  // הלשונית) — טעינה שהתיישנה בזמן ה-await לא כותבת ל-DOM שכבר הוחלף.
  const gen = ++BRANCH_VIEW_GEN;
  const stale = () => gen !== BRANCH_VIEW_GEN || $("#branch-view") !== host;
  const nid = BRANCH_NODE;
  const nodes = await api("/storage-nodes");
  if (stale()) return;
  const n = nodes.find((x) => x.id === nid);
  if (!n) {
    host.innerHTML = `<div class="card"><div class="card-b"><div class="empty">השרת המשני אינו רשום עוד. בחר שרת אחר בעץ.</div></div></div>`;
    return;
  }
  const disabled = !!n.disabled_at;
  const status = disabled
    ? `<span class="status"><i></i>מושבת</span>`
    : `<span class="status" id="branch-status-${esc(n.id)}"><i></i>בודק חיבור…</span>`;
  // מצב החיבור מוצג רק בלשוניות שמודדות אותו (סקירה, מחשבים) — לא "בודק
  // חיבור…" שלעולם לא מתעדכן (עיקרון 5).
  const measured = view === "overview" || view === "machines";
  const idEnc = encodeId(n.id);
  const headActs = view === "overview" ? `<div class="action-strip">
      <button class="btn primary" ${disabled ? "disabled" : ""} onclick="openTransferSheetForNode('${idEnc}')">העבר אימג'</button>
      <button class="btn" ${disabled ? "disabled" : ""} onclick="loadBranchMachines('${esc(n.id)}').catch((e)=>toast(e.message))">בדוק חיבור</button>
      <button class="btn" onclick="toggleBranchNode('${idEnc}').then(()=>loadBranchView('overview'))">${disabled ? "הפעל" : "השבת"}</button>
    </div>` : "";
  const head = `<div class="card-h"><span>${esc(n.label)} <small class="muted">${esc(n.group_label || "ללא קבוצה")}</small></span>${measured ? status : ""}</div>`;
  if (view === "overview") {
    const [answer, transfers] = await Promise.all([
      disabled ? Promise.resolve({ connected: false, error: "השרת המשני מושבת", machines: [] }) : api(`/storage-nodes/${encodeId(n.id)}/machines`),
      api("/storage-transfers"),
    ]);
    if (stale()) return;
    BRANCH_TRANSFERS = transfers;
    markSecondaryStatus(n, !!answer.connected, answer.error);
    const done = transfers.filter((t) => t.node_id === n.id && t.state === "done");
    const seenImg = new Set();
    const transferredCount = done.filter((t) => !seenImg.has(t.image_id) && seenImg.add(t.image_id)).length;
    const nodeTransfers = transfers.filter((t) => t.node_id === n.id);
    const activeCount = nodeTransfers.filter((t) => ["queued", "sending", "verifying"].includes(t.state)).length;
    const kpis = [
      UI.kpi({ cls: answer.connected ? "ok" : "err", label: "חיבור", value: answer.connected ? "מחובר" : "לא מחובר" }),
      UI.kpi({ cls: "", label: "מחשבים בסניף", value: answer.connected ? `${answer.machines.filter((m) => m.online).length} מתוך ${answer.machines.length}` : "—", sub: "מחוברים מתוך רשומים" }),
      UI.kpi({ cls: "", label: "אימג'ים שהועברו", value: String(transferredCount) }),
      UI.kpi({ cls: activeCount ? "info" : "", label: "העברות", value: String(nodeTransfers.length), sub: activeCount ? `${activeCount} פעילות עכשיו` : "" }),
    ].join("");
    host.innerHTML = `<div class="card">${head}<div class="card-b">
      ${headActs}
      <div id="branch-connect-note"></div>
      <div class="kpis">${kpis}</div>
      <div class="detail-grid" style="margin-top:12px">
        <div class="detail-box"><span class="k">כתובת</span><span class="v mono" dir="ltr">${esc(n.base_url)}</span></div>
        <div class="detail-box"><span class="k">נרשם</span><span class="v mono" dir="ltr">${esc((n.enrolled_at || "").replace("T", " ").slice(0, 19) || "—")}</span></div>
        <div class="detail-box"><span class="k">מזהה</span><span class="v mono" dir="ltr" id="branch-node-id">—</span></div>
        <div class="detail-box"><span class="k">מחשבים מחוברים</span><span class="v" id="branch-online-count">—</span></div>
      </div>
    </div></div>`;
    if (disabled) {
      $("#branch-connect-note").innerHTML = `<div class="notice err" role="status">השרת מושבת — לא נשאל.</div>`;
      return;
    }
    const st = $(`#branch-status-${CSS.escape(n.id)}`);
    if (answer.connected) {
      if (st) { st.innerHTML = `<i></i>מחובר`; st.className = "status ok"; }
      $("#branch-node-id").textContent = answer.node_id || "—";
      $("#branch-online-count").textContent = `${answer.machines.filter((m) => m.online).length} מתוך ${answer.machines.length}`;
    } else {
      if (st) { st.innerHTML = `<i></i>לא מחובר`; st.className = "status err"; }
      $("#branch-connect-note").innerHTML = `<div class="notice err" role="status">השרת המשני לא ענה: ${esc(answer.error || "")}</div>`;
    }
    return;
  }
  if (view === "machines") {
    host.innerHTML = `<div class="card">${head}<div class="card-b">
      <div class="action-strip"><button class="btn" id="branch-view-refresh" ${disabled ? "disabled" : ""}>רענן מכונות</button></div>
      <div id="branch-machines-${esc(n.id)}" class="sub">${disabled ? "השרת מושבת — לא נשאל." : "טוען…"}</div>
    </div></div>`;
    const load = () => loadBranchMachines(n.id).catch((e) => toast(e.message));
    $("#branch-view-refresh").onclick = load;
    if (!disabled) await load();
    return;
  }
  if (view === "images") {
    const remoteP = disabled
      ? Promise.resolve({ connected: false, error: "השרת המשני מושבת", images: [] })
      : api(`/storage-nodes/${encodeId(n.id)}/images`).catch((e) => (
        { connected: false, error: e.message, images: [] }));
    const [transfers, images, remote] = await Promise.all([
      api("/storage-transfers"), api("/images"), remoteP,
    ]);
    if (stale()) return;
    BRANCH_TRANSFERS = transfers;
    const done = transfers.filter((t) => t.node_id === n.id && t.state === "done");
    const seen = new Set();
    const rows = done.filter((t) => !seen.has(t.image_id) && seen.add(t.image_id));
    const localIds = new Set(images.map((m) => m.id));
    const libRows = (remote.images || []).map((img) => {
      const present = localIds.has(img.id);
      const pullBtn = `<button class="btn sm primary" ${present || disabled ? "disabled" : ""} onclick="startPullFromSecondary('${encodeId(n.id)}','${encodeId(img.id)}')">העבר לראשי</button>`;
      return `<tr>
        <td><b>${esc(img.name)}</b> <span class="mono muted" dir="ltr">${esc(img.id)}</span></td>
        <td>${esc(formatGB(img.size_bytes || 0))}</td>
        <td class="mono" dir="ltr">${esc((img.created_at || "").replace("T", " ").slice(0, 19) || "—")}</td>
        <td>${present ? "✓" : "—"}</td>
        <td>${pullBtn}</td>
      </tr>`;
    }).join("");
    const libBody = !remote.connected
      ? `<div class="notice warn" role="status">לא ניתן לקרוא את ספריית המשני: ${esc(remote.error || "")}</div>`
      : (libRows
        ? `<table><thead><tr><th>שם</th><th>גודל</th><th>תאריך</th><th>קיים בראשי</th><th></th></tr></thead><tbody>${libRows}</tbody></table>`
        : `<div class="empty">אין אימג'ים בספריית המשני.</div>`);
    host.innerHTML = `<div class="card">${head}<div class="card-b">
      <div class="action-strip"><button class="btn primary" id="branch-view-transfer" ${disabled ? "disabled" : ""}>העבר אימג'</button></div>
      <h4>ספריית האימג'ים של המשני</h4>
      ${libBody}
      <h4>אימג'ים שהועברו מכאן</h4>
      ${rows.length ? `<table><thead><tr><th>אימג'</th><th>הועבר</th></tr></thead><tbody>${rows.map((t) =>
        `<tr><td><b>${esc(t.image_name)}</b> <span class="mono muted" dir="ltr">${esc(t.image_id)}</span></td><td class="mono" dir="ltr">${esc((t.updated_at || t.created_at || "").replace("T", " ").slice(0, 19))}</td></tr>`).join("")}</tbody></table>`
        : `<div class="empty">עוד לא הועבר אימג' לשרת הזה.</div>`}
    </div></div>`;
    $("#branch-view-transfer").onclick = () => openTransferSheet(n, images, async () => activateTab(BRANCH_VIEWS.findIndex((v) => v[0] === "transfers")));
    return;
  }
  const transfers = await api("/storage-transfers");
  if (stale()) return;
  BRANCH_TRANSFERS = transfers;
  host.innerHTML = `<div class="card">${head}<div class="card-b"><div id="branch-transfers-${esc(n.id)}"></div></div></div>`;
  renderBranchTransfers(n.id, null, 20);
  BRANCH_TIMER = setInterval(() => {
    if (current !== "branch" || BRANCH_NODE !== n.id || document.hidden) { stopBranchPolling(); return; }
    if (!BRANCH_TRANSFERS.some((t) => ["queued", "sending", "verifying"].includes(t.state))) return;
    api("/storage-transfers").then((rows) => {
      BRANCH_TRANSFERS = rows;
      renderBranchTransfers(n.id, null, 20);
    }).catch(() => {});
  }, 2000);
}
