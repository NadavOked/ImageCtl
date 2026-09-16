/* ImageCtl — לשונית "סניפים" (#655 v1): כרטיס לכל שרת משני — מצב חיבור,
   המכונות שלו (נשאלות מהמשני דרך הראשי), העברת אימג' עם התקדמות, ומוניטור
   למכונה של המשני (דרך המשני — הראשי לא רואה סוד של מכונה).

   רק על שרת ראשי עם משניים (capabilities.interbranch_transfer); על משני אין
   לשונית — חד-כיווני. משתמש ב-api/post/sheet/toast/esc/formatGB מ-console.js.
   מרשם המשניים (עריכה/קבוצות) נשאר ב-storage_nodes.js, בלשונית "מרשם". */
"use strict";

let BRANCH_TIMER = null;
let BRANCH_TRANSFERS = [];

const TRANSFER_STATE = {
  queued: ["ממתין", ""], sending: ["שולח", ""], verifying: ["המשני מאמת sha256", ""],
  done: ["הושלם", "ok"], failed: ["נכשל", "err"],
};

function stopBranchPolling() {
  clearInterval(BRANCH_TIMER);
  BRANCH_TIMER = null;
}

async function loadBranchCards() {
  const host = $("#branch-cards");
  if (!host) return;
  stopBranchPolling();
  const [nodes, transfers, images] = await Promise.all([
    api("/storage-nodes"), api("/storage-transfers"), api("/images"),
  ]);
  BRANCH_TRANSFERS = transfers;
  if (!nodes.length) {
    host.innerHTML = `<div class="card"><div class="card-b"><div class="empty">אין שרתים משניים רשומים.</div></div></div>`;
    return;
  }
  host.innerHTML = `<div class="grid">${nodes.map((n) => branchCard(n)).join("")}</div>`;
  for (const n of nodes) {
    renderBranchTransfers(n.id);
    wireBranchCard(n, images);
  }
  // הכרטיסים כבר על המסך; המכונות נשאלות מכל משני במקביל (כל אחד עשוי
  // לא לענות — וזה מוצג בכרטיס שלו, לא מפיל את השאר).
  await Promise.all(nodes.filter((n) => !n.disabled_at).map(
    (n) => loadBranchMachines(n.id).catch((e) => toast(e.message))));
  BRANCH_TIMER = setInterval(() => {
    if (current !== "branches" || currentTab !== 0 || document.hidden) { stopBranchPolling(); return; }
    if (!BRANCH_TRANSFERS.some((t) => ["queued", "sending", "verifying"].includes(t.state))) return;
    api("/storage-transfers").then((rows) => {
      BRANCH_TRANSFERS = rows;
      for (const n of nodes) renderBranchTransfers(n.id);
    }).catch(() => {});
  }, 2000);
}

function branchCard(n) {
  const disabled = !!n.disabled_at;
  const status = disabled
    ? `<span class="status err"><i></i>מושבת</span>`
    : `<span class="status" id="branch-status-${esc(n.id)}"><i></i>בודק חיבור…</span>`;
  return `<div class="span-6"><div class="card" data-branch="${esc(n.id)}">
    <div class="card-h"><span>${esc(n.label)} <small class="muted">${esc(n.group_label || "ללא קבוצה")}</small></span>${status}</div>
    <div class="card-b">
      <div class="detail-grid"><div class="detail-box"><span class="k">כתובת</span><span class="v mono" dir="ltr">${esc(n.base_url)}</span></div></div>
      <div class="action-strip">
        <button class="btn primary" data-branch-transfer="${esc(n.id)}" ${disabled ? "disabled" : ""}>העבר אימג'</button>
        <button class="btn" data-branch-refresh="${esc(n.id)}" ${disabled ? "disabled" : ""}>רענן מכונות</button>
      </div>
      <h4>מחשבים בסניף</h4>
      <div id="branch-machines-${esc(n.id)}" class="sub">${disabled ? "השרת מושבת — לא נשאל." : "טוען…"}</div>
      <h4>העברות אימג'ים</h4>
      <div id="branch-transfers-${esc(n.id)}"></div>
    </div></div></div>`;
}

async function loadBranchMachines(nid) {
  const box = $(`#branch-machines-${CSS.escape(nid)}`);
  const status = $(`#branch-status-${CSS.escape(nid)}`);
  if (!box) return;
  const answer = await api(`/storage-nodes/${encodeId(nid)}/machines`);
  if (!answer.connected) {
    if (status) status.innerHTML = `<i></i>לא מחובר`;
    if (status) status.className = "status err";
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

function renderBranchTransfers(nid) {
  const box = $(`#branch-transfers-${CSS.escape(nid)}`);
  if (!box) return;
  const rows = BRANCH_TRANSFERS.filter((t) => t.node_id === nid).slice(0, 8);
  if (!rows.length) { box.innerHTML = `<div class="sub">אין העברות.</div>`; return; }
  box.innerHTML = rows.map((t) => {
    const [label, cls] = TRANSFER_STATE[t.state] || [t.state, ""];
    const pct = t.bytes_total ? Math.min(100, Math.round(100 * t.bytes_sent / t.bytes_total)) : 0;
    const bar = ["sending", "verifying", "queued"].includes(t.state)
      ? `<div class="progress" style="margin-top:6px"><i style="width:${pct}%"></i></div><span class="sub">${esc(formatGB(t.bytes_sent))} מתוך ${esc(formatGB(t.bytes_total))} (${pct}%)</span>`
      : "";
    const err = t.error ? `<div class="notice warn" role="status">${esc(t.error)}</div>` : "";
    return `<div class="member"><b>${esc(t.image_name)}</b> <span class="status ${cls}"><i></i>${esc(label)}</span>
      <div class="sub mono" dir="ltr">${esc((t.created_at || "").replace("T", " ").slice(0, 19))}</div>${bar}${err}</div>`;
  }).join("");
}

function wireBranchCard(n, images) {
  const transferBtn = document.querySelector(`[data-branch-transfer="${CSS.escape(n.id)}"]`);
  if (transferBtn) transferBtn.onclick = () => {
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
        await loadBranchCards();
      },
    });
  };
  const refreshBtn = document.querySelector(`[data-branch-refresh="${CSS.escape(n.id)}"]`);
  if (refreshBtn) refreshBtn.onclick = () => {
    const box = $(`#branch-machines-${CSS.escape(n.id)}`);
    if (box) box.innerHTML = "טוען…";
    loadBranchMachines(n.id).catch((e) => toast(e.message));
  };
}
