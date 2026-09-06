/* One presentation for known, unknown-moving, and unknown-idle progress. */
"use strict";
const Progress = (() => {
  const count = n => typeof n === "number" && Number.isFinite(n) && n >= 0;
  function view(task) {
    let read = task.bytes_written, total = task.bytes_total, suffix = "";
    const source = task.source_progress;
    if (source && Number.isInteger(source.partition) && source.partition > 0 &&
        count(source.blocks_read) && count(source.blocks_total) && source.blocks_total > 0 &&
        source.blocks_read <= source.blocks_total) {
      read = source.blocks_read;
      total = source.blocks_total;
      suffix = ` · מחיצה ${source.partition} (בלוקים)`;
    }
    const known = count(read) && count(total) && total > 0;
    const percent = known ? Math.min(100, Math.round(100 * read / total)) : null;
    const moving = count(task.bytes_written) && task.bytes_written > 0;
    const terminal = ["done", "failed", "partial", "cancelled"].includes(task.state);
    const state = known ? "determinate" : moving ? "indeterminate" : "unknown-idle";
    const label = known ? percent + "%" + suffix
      : moving ? "נקראו בייטים · הסך לא ידוע" : "טרם נקראו בייטים · הסך לא ידוע";
    const cls = state + (terminal ? " stopped" : "");
    const width = known ? percent : moving ? 40 : 100;
    return {percent, label, cls, width};
  }
  function bar(task) {
    const p = view(task);
    return `<div class="bar ${p.cls}" role="progressbar" aria-label="${p.label}"` +
      (p.percent === null ? "" : ` aria-valuemin="0" aria-valuemax="100" aria-valuenow="${p.percent}"`) +
      `><i style="width:${p.width}%"></i></div>`;
  }
  function apply(fill, label, task) {
    const p = view(task);
    fill.style.width = p.width + "%";
    fill.className = p.cls;
    label.textContent = p.label;
    fill.setAttribute("role", "progressbar");
    fill.setAttribute("aria-label", p.label);
    if (p.percent === null) fill.removeAttribute("aria-valuenow");
    else fill.setAttribute("aria-valuenow", p.percent);
  }
  return {view, bar, apply};
})();
