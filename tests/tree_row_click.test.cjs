// הכרעת נדב 11/10: פתיחה/סגירה בעץ כמו המוקאפ — לחיצה על שורה (לא דאבל-קליק), חץ רק מחליף מצב.
// ועוד: תפקיד עם קבוצה אחת מציג את המחשבים ישירות תחתיו; עם שתיים — צמתי קבוצה כמו קודם.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../server/static');
const source = fs.readFileSync(path.join(root, 'console.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const cut = (a, b) => { const i = source.indexOf(a), j = source.indexOf(b); assert.ok(i > 0 && j > i, a); return source.slice(i, j); };

function node(cls = []) {
  const set = new Set(cls), attrs = new Map();
  return {set, attrs, classList: {contains: (c) => set.has(c), add: (c) => set.add(c)}, setAttribute: (k, v) => attrs.set(k, v),
    querySelector: () => null};
}
function box(hidden) {
  return {hidden, style: {removeProperty() {}}, hasAttribute(k) { return k === 'hidden' && this.hidden; },
    setAttribute(k) { if (k === 'hidden') this.hidden = true; }, removeAttribute(k) { if (k === 'hidden') this.hidden = false; },
    querySelectorAll: () => []};
}
function navSetup(boxes, hereNode) {
  const ctx = vm.createContext({document: {getElementById: (id) => boxes[id] || null, querySelector: () => hereNode || null}});
  vm.runInContext(cut('function collapseInventoryDescendants(', '/* S4 (#829)') + '\n;this.api={treeNavToggle};', ctx);
  return ctx.api;
}

test('a branch that is also a page: click when elsewhere = navigate and open (not toggle)', () => {
  const n = node(), b = box(true); let navs = 0, self = null;
  navSetup({machineTree: b}).treeNavToggle(n, 'machineTree', function () { navs++; self = this; });
  assert.equal(navs, 1);
  assert.equal(self, n, '`this` in the inline handler is the row (openBranchView(…,this) needs it)');
  assert.equal(b.hidden, false, 'opened by the click');
  assert.equal(n.attrs.get('aria-expanded'), 'true');
});

test('a branch that is also a page: click when already on it = toggle only, no navigation', () => {
  const n = node(['active']), b = box(false); let navs = 0;
  navSetup({machineTree: b}).treeNavToggle(n, 'machineTree', () => { navs++; });
  assert.equal(navs, 0);
  assert.equal(b.hidden, true, 'closed by the click');
});

test('navigating to an already-open branch keeps it open (a click never closes a branch you are leaving for)', () => {
  const n = node(), b = box(false);
  navSetup({buildTree: b}).treeNavToggle(n, 'buildTree', () => {});
  assert.equal(b.hidden, false);
  assert.ok(n.set.has('here'), 'the role node remembers it is the current target');
});

test('a role node already targeted ("here") toggles; the page node above it navigates again', () => {
  const role = node(['here']), b = box(false); let navs = 0;
  navSetup({buildTree: b}).treeNavToggle(role, 'buildTree', () => { navs++; });
  assert.equal(navs, 0); assert.equal(b.hidden, true);
  const machines = node(['active']), mb = box(false);   // הדף "מחשבים" פעיל, אבל היעד הנוכחי הוא תפקיד
  navSetup({machineTree: mb}, role).treeNavToggle(machines, 'machineTree', () => { navs++; });
  assert.equal(navs, 1, 'leaving the role view for all machines is a navigation');
});

test('no tree row toggles on dblclick any more (only the server-name rename keeps it)', () => {
  const left = (html + source).match(/ondblclick="[^"]*toggleInventoryGroup/g);
  assert.equal(left, null, 'ondblclick must not toggle branches');
  assert.match(html, /class="inventory-node server-node active" onclick="toggleInventoryGroup\(this,'srvTA'\)"/, 'TA root toggles on a single click');
  for (const id of ['invTA', 'infraTA', 'adminTA']) assert.match(html, new RegExp(`onclick="toggleInventoryGroup\\(this,'${id}'\\)"`), id);
});

function fillSetup() {
  const trees = {buildTree: {innerHTML: '', querySelectorAll: () => []}};
  const ctx = vm.createContext({document: {getElementById: (id) => trees[id] || null}, trees,
    uiIcon: () => '', esc: (x) => String(x), encodeId: (x) => String(x), machineName: (m) => m.name, sortMachinesBySuffix: (a) => a,
    machineGroupId: (m) => m.group, sidebarChildOpen: () => ({}),
    // #1179: the primary's context — sideId/sideClick are the identity (secondary_context.test.cjs covers the other)
    sideId: (x) => x, sideClick: (h) => h});
  vm.runInContext(cut('function fillSidebarTree(', 'function machineIsFailed(') + '\n;this.fill=fillSidebarTree;', ctx);
  return ctx;
}
const machines = [{mac: 'a', name: 'HP-1', group: 'g1'}, {mac: 'b', name: 'HP-2', group: 'g1'}, {mac: 'c', name: 'HP-3', group: 'g2'}];

test('role with ONE group: machines are listed directly under the role, no group node', () => {
  const ctx = fillSetup();
  ctx.fill('buildTree', [{id: 'g1', label: 'מחשב בניית אימג\'ים'}], machines, 'build');
  const out = ctx.trees.buildTree.innerHTML;
  assert.ok(out.includes('HP-1') && out.includes('HP-2'));
  assert.ok(!out.includes('HP-3'), 'only that group\'s machines');
  assert.ok(!out.includes('בניית אימג'), 'no group level');
  assert.ok(!out.includes('aria-expanded'), 'nothing to expand below the role');
});

test('role with TWO groups: group nodes, each with its machines, as before', () => {
  const ctx = fillSetup();
  ctx.fill('buildTree', [{id: 'g1', label: 'קבוצה א'}, {id: 'g2', label: 'קבוצה ב'}], machines, 'build');
  const out = ctx.trees.buildTree.innerHTML;
  assert.ok(out.includes('קבוצה א') && out.includes('קבוצה ב'));
  assert.ok(out.includes('id="sg-g1"') && out.includes('id="sg-g2"'));
  assert.ok(out.includes('HP-3'));
});
