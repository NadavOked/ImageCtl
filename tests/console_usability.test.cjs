// #829 S1–S6 — שוברי-השימוש בקונסולה החדשה. כל בדיקה נצמדת למדידה שבדוח
// (logs/2026-09-14/224824-front-design-audit.md): selector, מאפיין, שורת CSS.
// בלי דפדפן ובלי JSDOM: console.js רץ ב-vm מול DOM מזערי שמדמה רק את מה
// שהעץ צריך (classList/attributes/parentElement), והשאר נבדק כטקסט.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');
const read = (f) => fs.readFileSync(path.join(root, f), 'utf8');

/* DOM מזערי: אלמנט עם classList, attributes, style, ילדים והורה. */
function el(tag, attrs = {}, children = []) {
  const e = {
    tag, attrs: {...attrs}, children, parentElement: null, innerHTML: '', textContent: '', value: '',
    style: {removeProperty(k) { delete this[k]; }},
    listeners: {},
    get classList() {
      const self = this;
      const list = () => (self.attrs.class || '').split(/\s+/).filter(Boolean);
      return {
        add(c) { if (!list().includes(c)) self.attrs.class = [...list(), c].join(' '); },
        remove(c) { self.attrs.class = list().filter((x) => x !== c).join(' '); },
        toggle(c, force) { const has = list().includes(c); const want = force === undefined ? !has : !!force; if (want && !has) this.add(c); if (!want && has) this.remove(c); return want; },
        contains(c) { return list().includes(c); },
      };
    },
    get id() { return this.attrs.id || ''; },
    get dataset() { const d = {}; for (const [k, v] of Object.entries(this.attrs)) if (k.startsWith('data-')) d[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v; return d; },
    hasAttribute(k) { return k in this.attrs; },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    removeAttribute(k) { delete this.attrs[k]; },
    get previousElementSibling() { const p = this.parentElement; if (!p) return null; const i = p.children.indexOf(this); return i > 0 ? p.children[i - 1] : null; },
    querySelector(sel) { return all(this).find((x) => matches(x, sel)) || null; },
    querySelectorAll(sel) { return all(this).filter((x) => matches(x, sel)); },
    closest(sel) { for (let n = this; n; n = n.parentElement) if (matches(n, sel)) return n; return null; },
    addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); },
    removeEventListener() {}, focus() { focused = this; }, click() {},
    insertAdjacentHTML(_p, html) { this.innerHTML += html; },
  };
  for (const c of children) c.parentElement = e;
  return e;
}
let focused = null;
function all(e) { const out = []; (function walk(n) { for (const c of n.children) { out.push(c); walk(c); } })(e); return out; }
/* selector מינימלי: רשימת ".a.b[attr=v]#id" מופרדת בפסיקים, בלי צאצאים. */
function matches(e, selector) {
  return selector.split(',').some((part) => {
    part = part.trim();
    const classes = [...part.matchAll(/\.([\w-]+)/g)].map((m) => m[1]);
    const ids = [...part.matchAll(/#([\w-]+)/g)].map((m) => m[1]);
    const attrs = [...part.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)];
    return classes.every((c) => e.classList.contains(c)) && ids.every((i) => e.id === i)
      && attrs.every(([, k, v]) => v === undefined ? e.hasAttribute(k) : e.getAttribute(k) === v);
  });
}

/* עץ בצורת index.html: שרת → [סקירה, הפצה, מלאי → [אימג׳ים, מחשבים], תשתית(סגור) → [בריאות, מוניטור]]. */
function buildTree() {
  const node = (page, extra = {}) => el('div', {class: 'inventory-node', 'data-page': page, tabindex: '0', ...extra});
  const infra = node(undefined, {class: 'inventory-node'});
  delete infra.attrs['data-page'];
  const infraChildren = el('div', {id: 'infraTA', class: 'inventory-children', hidden: ''}, [node('health'), node('monitor')]);
  const invChildren = el('div', {id: 'invTA', class: 'inventory-children'}, [node('images'), node('machines')]);
  const inv = el('div', {class: 'inventory-node', tabindex: '0'}, [el('span', {class: 'tree-arrow', 'data-open': 'true'})]);
  const srvChildren = el('div', {id: 'srvTA', class: 'inventory-children'}, [node('home'), node('deploy'), inv, invChildren, infra, infraChildren]);
  const srv = el('div', {class: 'inventory-node server-node active', tabindex: '0'}, [el('span', {class: 'tree-arrow', 'data-open': 'true'})]);
  const tree = el('div', {class: 'tree'}, [srv, srvChildren]);
  const toggle = el('button', {class: 'icon-btn nav-toggle', 'aria-expanded': 'false'});
  const sidebar = el('aside', {class: 'sidebar', id: 'sidebar'}, [tree]);
  return el('div', {id: 'app'}, [el('header', {class: 'topbar'}, [toggle]), sidebar, el('div', {class: 'sidebar-backdrop', id: 'sidebarBackdrop'})]);
}

function setup() {
  focused = null;
  const tree = buildTree();
  const byId = new Map();
  const stub = (id) => { if (!byId.has(id)) byId.set(id, el('div', {id})); return byId.get(id); };
  const document = {
    hidden: false,
    querySelector: (sel) => tree.querySelector(sel) || stub('q:' + sel),
    querySelectorAll: (sel) => tree.querySelectorAll(sel),
    getElementById: (id) => tree.querySelector('#' + id) || stub(id),
    createElement: () => { let text = ''; return {set textContent(v) { text = String(v); }, get innerHTML() { return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }}; },
    documentElement: {setAttribute() {}}, removeEventListener() {},
    listeners: {}, addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); },
    body: el('body'),
  };
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise, encodeURIComponent, decodeURIComponent,
    document, window: {addEventListener() {}, matchMedia: () => ({matches: false}), open() {}},
    localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: (x) => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async () => ({status: 200, ok: true, headers: {get: () => null}, json: async () => ({})}),
  });
  vm.runInContext(read('progress.js'), ctx);
  vm.runInContext(read('console.js'), ctx);
  const run = (s) => vm.runInContext(s, ctx);
  run('ME={username:"admin",role:"admin",idle_seconds:300,capabilities:{}}; toast=()=>{};');
  return {run, tree, document};
}

// ---- S1: הדף הנוכחי מודגש בעץ ---------------------------------------------

test('S1: every navigating tree node in index.html carries data-page', () => {
  const html = read('index.html');
  const lines = html.split('\n').filter((l) => l.includes('class="inventory-node') && /selectPageById\('([a-z]+)'\)/.test(l));
  assert.ok(lines.length >= 13, 'expected the tree nodes, got ' + lines.length);   // ‏#954 גל 8: nic+netdeploy → network (אחד במקום שניים)
  for (const line of lines) {
    const page = line.match(/selectPageById\('([a-z]+)'\)/)[1];
    assert.match(line, new RegExp(`<div class="inventory-node[^"]*" data-page="${page}"`), `node for '${page}' lacks data-page on the .inventory-node div`);
  }
});

test('S1: selectPageById highlights the .inventory-node of the page, and only it', () => {
  const {run, tree} = setup();
  run('selectPageById("deploy")');
  const active = tree.querySelectorAll('.inventory-node.active');
  assert.deepEqual(active.map((n) => n.attrs['data-page']), ['deploy'], 'exactly the deploy node is .active');
  assert.equal(active[0].getAttribute('aria-current'), 'page');
  run('selectPageById("images")');
  assert.deepEqual(tree.querySelectorAll('.inventory-node.active').map((n) => n.attrs['data-page']), ['images']);
  assert.deepEqual(tree.querySelectorAll('.inventory-node[aria-current]').map((n) => n.attrs['data-page']), ['images'], 'aria-current moves with the selection');
});

test('S1: selecting a page inside a collapsed group opens the group so the highlight is visible', () => {
  const {run, tree} = setup();
  const infra = tree.querySelector('#infraTA');
  assert.ok(infra.hasAttribute('hidden'), 'precondition: infra group starts collapsed');
  run('selectPageById("health")');
  assert.ok(!infra.hasAttribute('hidden'), 'infra group was expanded');
  assert.equal(infra.previousElementSibling.querySelector('.tree-arrow'), null, 'test tree: owner without arrow tolerated');
  assert.deepEqual(tree.querySelectorAll('.inventory-node.active').map((n) => n.attrs['data-page']), ['health']);
});

// ---- S2: ניווט במסך צר — המבורגר שפותח את הסיידבר כשכבה --------------------

/* כל הכללים בתוך בלוקי @media(max-width:740px) של console.css, כמחרוזת אחת. */
function narrowCss() {
  const css = read('console.css');
  return [...css.matchAll(/@media\s*\(max-width:\s*740px\)\s*\{((?:[^{}]*\{[^{}]*\})*)\s*\}/g)].map((m) => m[1]).join('\n');
}

test('S2: the topbar has a hamburger that controls the sidebar, shown only at <=740px', () => {
  const html = read('index.html');
  assert.match(html, /<button class="icon-btn nav-toggle"[^>]*aria-controls="sidebar"[^>]*aria-expanded="false"/, 'hamburger with aria-controls/aria-expanded');
  assert.match(html, /<aside class="sidebar" id="sidebar">/);
  const css = read('console.css');
  assert.match(css, /\.nav-toggle\{display:none\}/, 'hidden on wide screens');
  const narrow = narrowCss();
  assert.match(narrow, /\.nav-toggle\{display:inline-flex\}/, 'visible at <=740px');
  assert.match(narrow, /\.sidebar\.open\{[^}]*display:flex[^}]*position:fixed/, 'the open sidebar is a fixed overlay, overriding .sidebar{display:none}');
});

test('S2: toggleSidebar opens/closes with aria-expanded; navigation and Escape close it', () => {
  const {run, tree, document} = setup();
  const side = tree.querySelector('#sidebar'), btn = tree.querySelector('.nav-toggle');
  run('toggleSidebar()');
  assert.ok(side.classList.contains('open'));
  assert.equal(btn.getAttribute('aria-expanded'), 'true');
  run('selectPageById("deploy")');
  assert.ok(!side.classList.contains('open'), 'navigating from the overlay closes it');
  assert.equal(btn.getAttribute('aria-expanded'), 'false');
  run('init()');
  run('toggleSidebar()');
  for (const fn of document.listeners.keydown) fn({key: 'Escape', target: side});
  assert.ok(!side.classList.contains('open'), 'Escape closes the overlay');
});

// ---- S3: שורת הסטטוס אינה מכוסה ע"י ה-dock הקבוע --------------------------

/* הערך האחרון של מאפיין עבור סלקטור, מחוץ ל-@media (הסדר ב-CSS מכריע). */
function lastDecl(css, selector, prop) {
  const noMedia = css.replace(/@media[^{]*\{(?:[^{}]*\{[^{}]*\})*\s*\}/g, '');
  const esc = selector.replace(/[.#]/g, (c) => '\\' + c);
  let value = null;
  for (const m of noMedia.matchAll(new RegExp('(?:^|[}\\s,])' + esc + '\\{([^}]*)\\}', 'g'))) {
    const d = m[1].match(new RegExp('(?:^|;)\\s*' + prop + ':\\s*([^;]+)'));
    if (d) value = d[1].trim();
  }
  return value;
}
const px = (v) => { assert.match(String(v), /^(0|-?\d+(\.\d+)?px)$/, 'px value, got ' + v); return parseFloat(v); };

test('S3: the fixed task dock starts above the in-flow statusbar, and the task panel above the dock', () => {
  const css = read('console.css');
  assert.equal(lastDecl(css, '.task-dock', 'position'), 'fixed');
  const statusH = px(lastDecl(css, '.statusbar', 'height'));
  const dockBottom = px(lastDecl(css, '.task-dock', 'bottom'));
  const dockH = px(lastDecl(css, '.task-dock', 'height'));
  assert.ok(dockBottom >= statusH, `dock bottom ${dockBottom}px must clear the ${statusH}px statusbar (was 0 → statusbar covered)`);
  assert.equal(px(lastDecl(css, '.task-panel', 'bottom')), dockBottom + dockH, 'task panel rests on the dock');
});

test('S3: at <=740px, where the statusbar is hidden, the dock returns to the bottom edge', () => {
  const narrow = narrowCss();
  assert.match(narrow, /\.statusbar\{display:none\}/, 'precondition: statusbar hidden when narrow');
  assert.match(narrow, /\.task-dock\{bottom:0\}/);
  assert.match(narrow, /\.task-panel\{bottom:34px\}/);
});

// ---- S4: ניווט מקלדת — role/tabindex/aria בפלט הרינדור, ומקשים שמפעילים ----

test('S4: tabs render as <button role="tab"> inside role="tablist" with aria-selected and roving tabindex', () => {
  const {run} = setup();
  // ‏#954: העמודים החדשים (page.own) מציירים לשוניות משלהן — הבדיקה על המסגרת הגנרית עוברת לשרת המשני (גל 8: גם הרשת own; branch = 4 לשוניות)
  const html = run('layout(pages.branch, 1)');
  assert.match(html, /<div class="vcenter-tabs" role="tablist">/);
  const tabs = [...html.matchAll(/<button type="button" class="vcenter-tab ?(active)?" role="tab" aria-selected="(true|false)" tabindex="(0|-1)"/g)];
  assert.equal(tabs.length, 4, 'four branch tabs as buttons');
  assert.deepEqual(tabs.map((m) => [m[1] || '', m[2], m[3]]), [['', 'false', '-1'], ['active', 'true', '0'], ['', 'false', '-1'], ['', 'false', '-1']]);
  assert.doesNotMatch(html, /<div class="vcenter-tab[ "]/, 'no div tabs left');
  assert.match(html, /id="pageActionBtn" aria-haspopup="menu" aria-expanded="false"/);
  assert.match(html, /id="pageActionMenu" class="action-menu" role="menu"/);
});

test('S4: static tree nodes in index.html carry role="treeitem" tabindex="0"; groups carry aria-expanded', () => {
  const html = read('index.html');
  assert.match(html, /<div class="tree" role="tree"/);
  const nodes = [...html.matchAll(/<div class="inventory-node[^"]*"[^>]*>/g)].map((m) => m[0]).filter((t) => !t.includes('muted'));
  assert.ok(nodes.length >= 22, 'got ' + nodes.length);   // 25 עד #936: הצומת הסטטי של המשני ושני ילדיו נמחקו
  for (const t of nodes) assert.match(t, /role="treeitem" tabindex="0"/, t.slice(0, 80));
  const arrows = html.match(/<span class="tree-arrow"/g).length;
  const expanded = nodes.filter((t) => /aria-expanded="(true|false)"/.test(t)).length;
  assert.equal(expanded, arrows, 'every node with an arrow declares aria-expanded');
});

test('S4: dynamically rendered tree nodes (folders, groups, machines) are focusable treeitems', () => {
  const {run, document} = setup();
  run('FOLDERS=[{name:"F"}]; IMAGES=[{id:"i1",name:"Img",folder:"F"}]; populateSidebarImages()');
  const images = document.getElementById('imagesTree').innerHTML;
  const nodes = images.match(/<div class="inventory-node[^>]*>/g);
  assert.equal(nodes.length, 2, 'folder + image');
  for (const t of nodes) assert.match(t, /role="treeitem" tabindex="0"/, t);
  assert.match(nodes[0], /aria-expanded="false"/, 'folder group declares expansion');
  run('ME.capabilities.classrooms=true; GROUPS=[{id:"g1",label:"Class",role:"classroom"}]; MACHINES=[{mac:"aa",suffix:"01",group_id:"g1"}]; populateSidebarGroups()');
  const classes = document.getElementById('classesTree').innerHTML;
  for (const t of classes.match(/<div class="inventory-node[^>]*>/g)) assert.match(t, /role="treeitem" tabindex="0"/, t);
});

test('S4: Enter/Space activate a tree node, arrows move between visible nodes, aria-expanded follows toggles', () => {
  const {run, tree, document} = setup();
  run('init()');
  const keydown = (target, key) => { let prevented = false; for (const fn of tree.querySelector('.tree').listeners.keydown) fn({key, target, currentTarget: tree.querySelector('.tree'), preventDefault() { prevented = true; }}); return prevented; };
  const deploy = tree.querySelector('.inventory-node[data-page="deploy"]');
  let clicked = 0; deploy.click = () => { clicked++; }; deploy.attrs.onclick = 'selectPageById("deploy")';
  assert.equal(keydown(deploy, 'Enter'), true, 'Enter is consumed');
  assert.equal(keydown(deploy, ' '), true, 'Space is consumed');
  assert.equal(clicked, 2, 'both activate the node the way a click would');
  // ArrowDown from deploy: next visible node is "מלאי" (the group), not the hidden infra children
  const home = tree.querySelector('.inventory-node[data-page="home"]');
  keydown(home, 'ArrowDown');
  assert.equal(focused, deploy);
  const images = tree.querySelector('.inventory-node[data-page="images"]');
  keydown(images, 'ArrowDown');
  assert.equal(focused.attrs['data-page'], 'machines');
  keydown(focused, 'ArrowDown');
  assert.ok(!focused.attrs['data-page'], 'after machines comes the collapsed infra group itself, not its hidden children');
  // aria-expanded tracks toggleInventoryGroup
  const inv = tree.querySelector('#invTA').previousElementSibling;
  inv.setAttribute('aria-expanded', 'true');
  run('toggleInventoryGroup(document.getElementById("invTA").previousElementSibling, "invTA")');
  assert.equal(inv.getAttribute('aria-expanded'), 'false');
  assert.ok(tree.querySelector('#invTA').hasAttribute('hidden'));
});

test('S4: tablist arrows move the active tab (RTL: ArrowLeft = next) and Escape closes the action menu', () => {
  const {run, tree, document} = setup();
  run('init()');
  const content = document.getElementById('content');
  // לשוניות מדומות: הרינדור מייצר innerHTML בלבד, לכן הקשר [role=tab] נבנה ידנית
  const tabs = [0, 1, 2].map((i) => el('button', {role: 'tab', 'data-i': String(i)}));
  const list = el('div', {role: 'tablist'}, tabs);
  for (const t of tabs) t.closest = (sel) => (sel === '[role="tab"]' ? t : null);
  document.querySelectorAll = (sel) => (sel === '[role="tab"]' ? tabs : tree.querySelectorAll(sel));
  run('current="home"; currentTab=0');
  for (const fn of content.listeners.keydown) fn({key: 'ArrowLeft', target: tabs[0], preventDefault() {}});
  assert.equal(run('currentTab'), 1, 'ArrowLeft advanced to the next tab');
  assert.equal(focused, tabs[1], 'focus followed the active tab');
  for (const fn of content.listeners.keydown) fn({key: 'ArrowRight', target: tabs[1], preventDefault() {}});
  assert.equal(run('currentTab'), 0);
  for (const fn of content.listeners.keydown) fn({key: 'End', target: tabs[0], preventDefault() {}});
  assert.equal(run('currentTab'), 2);
  // Escape closes the action menu and resets aria-expanded on its button
  const menu = document.getElementById('pageActionMenu'), btn = document.getElementById('pageActionBtn');
  menu.style.display = 'block'; btn.setAttribute('aria-expanded', 'true');
  for (const fn of document.listeners.keydown) fn({key: 'Escape', target: menu});
  assert.equal(menu.style.display, 'none');
  assert.equal(btn.getAttribute('aria-expanded'), 'false');
});

// ---- S5: תפריט "+ פעולה" נפתח פנימה ולא נחתך ב-overflow:hidden ------------

test('S5: .action-menu anchors to inline-end (into the content), not inline-start (out of it)', () => {
  const css = read('console.css');
  assert.equal(lastDecl(css, '.content', 'overflow'), 'hidden', 'precondition: the content clips — that is what made it a breaker');
  assert.equal(lastDecl(css, '.action-menu', 'position'), 'absolute');
  assert.equal(lastDecl(css, '.action-menu', 'inset-inline-end'), '0', 'menu edge on the inner side of the button');
  assert.equal(lastDecl(css, '.action-menu', 'inset-inline-start'), 'auto', 'the original inset-inline-start:0 is neutralised');
});

// ---- S6: תפריט המשתמש נפתח מתחת לכפתור שלו ---------------------------------

test('S6: .user-menu is fixed at inline-end, the same side as .user (margin-inline-start:auto pushes it there)', () => {
  const css = read('console.css');
  assert.equal(lastDecl(css, '.top-actions', 'margin-inline-start'), 'auto', 'precondition: the user button sits at inline-end of the topbar');
  assert.equal(lastDecl(css, '.user-menu', 'position'), 'fixed');
  assert.equal(lastDecl(css, '.user-menu', 'inset-inline-end'), '10px');
  assert.equal(lastDecl(css, '.user-menu', 'inset-inline-start'), 'auto', 'the original inset-inline-start:10px (the far side in RTL) is neutralised');
});

// ---- S7 (#931): הסרגל נגלל, וקליק/דאבל-קליק על צומת עובד כמצופה -----------

/* #931: `.main` הוא grid item עם גובה קבוע מ-.app (48px + 1fr), אבל
   `.sidebar` עצמו לא היה חתוך אליו — התוכן פשוט גדל מעבר לגבול (overflow
   גלוי כברירת מחדל) ודחף את הגוף כולו, בלי scrollbar ובלי שה-wheel יגיע
   לאף אלמנט שבאמת גולל. נמדד בדפדפן אמיתי (127.0.0.1:8081, 1366×768):
   .sidebar.clientHeight גדל יחד עם .scrollHeight (937 שניהם) לפני התיקון,
   ואחריו .clientHeight נשאר 698 (קבוע) בעוד .scrollHeight גדל ל-847 —
   בדיוק ההפרש שמאפשר ל-`.tree{overflow-y:auto}` הקיים לעבוד.
   התיקון עצמו: overflow:hidden על .sidebar (לא height — ראו למטה). */
test('S7: .sidebar clips to its grid row (overflow:hidden), so .tree\'s own vertical scroll can bound it', () => {
  const css = read('console.css');
  assert.equal(lastDecl(css, '.sidebar', 'overflow'), 'hidden',
    'without this the sidebar grows past .main\'s fixed height instead of scrolling internally');
  assert.equal(lastDecl(css, '.tree', 'overflow-y'), 'auto');
  assert.equal(lastDecl(css, '.tree', 'overflow-x'), 'hidden', 'no horizontal scroll (גמור #931)');
  // height:100% on .sidebar was tried and rejected: it over-constrains the
  // <=740px overlay (.sidebar.open{position:fixed;top:48px;bottom:0}),
  // which would push the overlay 48px past the viewport bottom.
  const narrow = narrowCss();
  assert.match(narrow, /\.sidebar\.open\{[^}]*bottom:0/, 'precondition: the mobile overlay still pins to the viewport bottom');
});

/* #931 — תיקון ספק מנדב אחרי בדיקה במעבדה (16/09 21:30): הסמנטיקה
   ההפוכה ממה שבנתי קודם. "קליק אחד מראה את מה שלחצתי — נגיד לחצתי
   'מחשבים' אז את המחשבים; שני קליקים פותח וסוגר". כלומר קליק בודד על
   שורת צומת אף פעם לא מטגל (רק בורר/מנווט אם יש לו יעד — אחרת לא עושה
   כלום); דאבל-קליק על השורה מטגל בדיוק פעם אחת; החץ הקטן ממשיך לטגל
   בקליק בודד כמו קודם. toggleInventoryGroup עצמה חזרה לחתימה הפשוטה
   (nodeEl, childId) בלי שמירת event.detail — אין יותר צורך בה: קליק
   בודד על שורה כבר לא קורא לפונקציה בכלל, ודאבל-קליק native קורא לה
   פעם אחת בדיוק (הדפדפן מבטיח את זה, לא הקוד). */
test('S7 (follow-up): the tree scrolls without a visible scrollbar, and a double-click on a row does not select the word', () => {
  // נדב 16/09 22:22: "אני רוצה גלילה בלי שיהיה את זה בתצוגה" — פס הגלילה
  // הנייטיבי נראה כמו רכיב UI; והדאבל-קליק שפותח/סוגר סימן את שם התיקייה.
  const css = read('console.css');
  assert.equal(lastDecl(css, '.tree', 'scrollbar-width'), 'none', 'Firefox: hidden scrollbar, still scrollable');
  assert.match(css, /\.tree::-webkit-scrollbar\{[^}]*display:none/, 'Chromium: hidden scrollbar, still scrollable');
  assert.equal(lastDecl(css, '.inventory-node', 'user-select'), 'none', 'dblclick toggles the node, it must not select its label');
});

test('S7: a single click on a group row never toggles — only navigates (or does nothing); the arrow and the row\'s dblclick still toggle', () => {
  const html = read('index.html');
  // כל onclick על .inventory-node (רמת השורה, לא על .tree-arrow הפנימי)
  // אסור שיקרא ל-toggleInventoryGroup — מותר לו לנווט/לבחור, או להיעדר.
  const rowOnclicks = [...html.matchAll(/<div class="inventory-node[^>]*\bonclick="([^"]*)"/g)].map((m) => m[1]);
  assert.ok(rowOnclicks.length >= 10, 'expected several row-level onclick handlers, got ' + rowOnclicks.length);
  for (const onclick of rowOnclicks) {
    assert.doesNotMatch(onclick, /toggleInventoryGroup/, `row onclick "${onclick}" must not toggle — single click only navigates/selects`);
  }
  // כל שורה שיש לה חץ (כלומר יש לה ילדים לטגל) חייבת ondblclick שמטגל אותה
  const arrowRows = [...html.matchAll(/<div class="inventory-node[^>]*>(?:(?!<\/div>).)*?<span class="tree-arrow"[^>]*>/g)];
  assert.ok(arrowRows.length >= 10, 'expected group rows with an arrow, got ' + arrowRows.length);
  for (const [rowHtml] of arrowRows) {
    assert.match(rowHtml, /ondblclick="toggleInventoryGroup\(this,'\w+'\)"/, `row must toggle on dblclick: ${rowHtml.slice(0, 90)}`);
  }
  // והחץ עצמו עדיין מטגל בקליק בודד, כמו לפני התיקון הזה
  assert.doesNotMatch(html, /<span class="tree-arrow"(?![^>]*onclick=)[^>]*>/,
    'every .tree-arrow keeps its own single-click toggle');
});

/* #931: toggleInventoryGroup חזרה לחתימה הפשוטה — קריאה בודדת = טוגל
   בודד, תמיד (בלי dedup פנימי). זה נכון כי עכשיו רק שני מקומות קוראים
   לה: החץ (קליק בודד, קורה פעם אחת) והשורה (dblclick, שהדפדפן מבטיח
   שיורה פעם אחת לכל מחווה) — אין יותר "שני click לפני דאבל-קליק" שצריך
   סינון, כי קליק בודד על שורה כבר לא קורא לפונקציה כלל. */
test('S7: toggleInventoryGroup has no built-in de-dup — each call toggles once, matching "arrow-click or row-dblclick, called exactly once per gesture"', () => {
  const {run, tree} = setup();
  const box = tree.querySelector('#invTA');
  assert.ok(!box.hasAttribute('hidden'), 'precondition: invTA starts open');
  run('toggleInventoryGroup(document.getElementById("invTA").previousElementSibling, "invTA")');
  assert.ok(box.hasAttribute('hidden'), 'one call (the dblclick, or the arrow click) closes it');
  run('toggleInventoryGroup(document.getElementById("invTA").previousElementSibling, "invTA")');
  assert.ok(!box.hasAttribute('hidden'), 'a second call re-opens it — no hidden guard eating it');
});

/* #931 גמור: "renameServer נשאר על שם השרת בלבד" — דאבל-קליק על שאר
   השורה מטגל; דאבל-קליק מדויק על הטקסט קורא ל-renameServer בלבד
   (עוצר propagation כדי שלא יגיע גם ל-ondblclick של השורה). קליק בודד
   בשום מקום בשורה הזו לא עושה כלום (לשורת שרת אין יעד ניווט).
   נבדק חי בדפדפן (127.0.0.1:8081): דאבל-קליק על אזור האייקון טגל את
   srvTA; דאבל-קליק מדויק על הטקסט קרא ל-renameServer פעם אחת ו-srvTA
   נשאר פתוח (renameCalls:1, srvTAHidden:false). */
test('S7: the primary server row (the only static one since #936) has no click-to-anything; dblclick toggles the row, and .server-name\'s own dblclick stops it from also reaching the row', () => {
  const html = read('index.html');
  const rows = [...html.matchAll(/<div class="inventory-node server-node[^>]*>/g)];
  assert.equal(rows.length, 1, '#936: only the primary is static; secondaries come from /storage-nodes');
  for (const [rowTag] of rows) {
    assert.doesNotMatch(rowTag, /\bonclick=/, 'the primary row has no navigate target — single click must do nothing');
    assert.match(rowTag, /ondblclick="toggleInventoryGroup\(this,'\w+'\)"/, 'dblclick on the row toggles it');
  }
  const nameHandlers = [...html.matchAll(/<strong class="server-name"[^>]* ondblclick="([^"]*)">/g)].map((m) => m[1]);
  assert.equal(nameHandlers.length, 1, 'renameServer is wired on the primary .server-name only');
  for (const handler of nameHandlers) {
    assert.match(handler, /event\.stopPropagation\(\)/, 'must stop the dblclick from also reaching the row\'s toggle');
    assert.match(handler, /renameServer\(this\)/);
  }
});
