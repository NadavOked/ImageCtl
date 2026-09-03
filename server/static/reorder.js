/* גרירה לשינוי סדר, בהפעלה בלחיצה ארוכה.

   למה לחיצה ארוכה ולא draggable רגיל: אותה שורה גם נפתחת בקליק. אילו
   הגרירה הייתה מתחילה מיד, כל ניסיון לפתוח קבוצה היה מזיז אותה. השהיה
   קצרה מפרידה בין "לחצתי" ל"אני מזיז".

   שימוש:
     enableLongPressReorder({
       container, itemSelector, handleSelector, onDrop(idsInNewOrder)
     });
*/
"use strict";

const LONG_PRESS_MS = 400;
const MOVE_TOLERANCE = 8;      // תזוזה גדולה מזו לפני ההשהיה = גלילה, לא גרירה

function enableLongPressReorder({ container, itemSelector, handleSelector, onDrop }) {
  let timer = null;
  let item = null;          // האלמנט הנגרר
  let dragging = false;
  let startX = 0, startY = 0;

  const items = () => [...container.querySelectorAll(itemSelector)];

  function cancelPending() {
    clearTimeout(timer);
    timer = null;
  }

  function begin() {
    dragging = true;
    container.classList.add("reordering");
    item.classList.add("dragging");
    // כשגוררים, אסור שהדפדפן יבחר טקסט או יגלול במגע.
    document.body.style.userSelect = "none";
  }

  function moveTo(pointerY) {
    const others = items().filter((el) => el !== item);
    for (const other of others) {
      const box = other.getBoundingClientRect();
      if (pointerY < box.top || pointerY > box.bottom) continue;
      const before = pointerY < box.top + box.height / 2;
      if (before) other.parentNode.insertBefore(item, other);
      else other.parentNode.insertBefore(item, other.nextSibling);
      return;
    }
  }

  async function finish() {
    const wasDragging = dragging;
    cancelPending();
    dragging = false;
    document.body.style.userSelect = "";
    container.classList.remove("reordering");
    if (item) item.classList.remove("dragging");
    const dropped = item;
    item = null;
    if (!wasDragging || !dropped) return;
    // מונע את פתיחת/סגירת השורה שהקליק היה גורם לה אחרי הגרירה.
    dropped.dataset.justDragged = "1";
    setTimeout(() => delete dropped.dataset.justDragged, 0);
    await onDrop(items().map((el) => el.dataset.reorderId));
  }

  container.addEventListener("pointerdown", (event) => {
    const handle = event.target.closest(handleSelector);
    if (!handle || event.target.closest("button, a, input, textarea, select")) return;
    item = handle.closest(itemSelector);
    if (!item) return;
    startX = event.clientX;
    startY = event.clientY;
    timer = setTimeout(begin, LONG_PRESS_MS);
  });

  container.addEventListener("pointermove", (event) => {
    if (dragging) { event.preventDefault(); moveTo(event.clientY); return; }
    if (!timer) return;
    const moved = Math.hypot(event.clientX - startX, event.clientY - startY);
    if (moved > MOVE_TOLERANCE) { cancelPending(); item = null; }
  });

  container.addEventListener("pointerup", finish);
  container.addEventListener("pointercancel", finish);
  container.addEventListener("pointerleave", finish);

  // קליק שהגיע מיד אחרי גרירה נבלע, כדי שהשורה לא תיפתח בטעות.
  container.addEventListener("click", (event) => {
    const target = event.target.closest(itemSelector);
    if (target && target.dataset.justDragged) event.preventDefault();
  }, true);
}
