// #953: every image in the library says which disk it fits from
// ("נכנס לדיסק מ-X GB", from the derived min_target_bytes, rounded UP to a
// decimal GB, bytes in the tooltip), how much is used (null = "לא ידוע",
// never 0 -- a partition that would not mount, #84), and how much it weighs
// on the server. Runs the shipped console.js in a vm, not a copy.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../server/static');

const stubNode = () => ({value: '', innerHTML: '', textContent: '', dataset: {}, style: {removeProperty() {}},
  classList: {add() {}, remove() {}, toggle() {}}, addEventListener() {}, removeEventListener() {},
  querySelector() { return null; }, querySelectorAll() { return []; }, setAttribute() {}, removeAttribute() {}, focus() {}});

function setup() {
  const ctx = vm.createContext({
    console, URLSearchParams, URL, Date, Set, Map, Number, Math, JSON, Promise,
    encodeURIComponent, decodeURIComponent,
    document: {
      hidden: false, querySelectorAll: () => [], querySelector: stubNode, getElementById: stubNode,
      createElement: () => { let text = ''; return {set textContent(v) { text = String(v); },
        get innerHTML() { return text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }}; },
      documentElement: {setAttribute() {}}, addEventListener() {}, removeEventListener() {},
    },
    window: {addEventListener() {}, matchMedia: () => ({matches: false}), open() {}},
    localStorage: {getItem: () => null, setItem() {}}, CSS: {escape: x => x},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async () => ({status: 200, ok: true, headers: {get: () => null}, json: async () => ({})}),
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'progress.js'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'console.js'), 'utf8'), ctx);
  const run = s => vm.runInContext(s, ctx);
  run('ME={username:"u",role:"admin",idle_seconds:300,capabilities:{}}; toast=()=>{};');
  return run;
}

const IMAGE = {id: 'img_fit230', name: 'Fits 256', min_target_bytes: 229556000000,
               used_bytes: 84540833792, total_compressed_bytes: 57982058496};

test('fits-from is rounded up to a decimal GB with the bytes in the tooltip', () => {
  const run = setup();
  const html = run(`imageSizeCells(${JSON.stringify(IMAGE)})`);
  assert.match(html, /נכנס לדיסק מ-<span title="229556000000 בייט">230 GB<\/span>/);
  assert.match(html, /בשימוש 78\.7 GB/);
  assert.match(html, /בשרת 54\.0 GB/);
});

test('an unmeasured used_bytes reads "לא ידוע", not 0', () => {
  const run = setup();
  const html = run(`imageSizeCells(${JSON.stringify({...IMAGE, used_bytes: null})})`);
  assert.match(html, /בשימוש לא ידוע/);
  assert.doesNotMatch(html, /בשימוש 0/);
});

test('an unknown requirement is "לא ידוע" and never a number', () => {
  const run = setup();
  assert.equal(run('fitGB(null)'), null);
  assert.equal(run('fitGB(-1)'), null);
  assert.match(run(`imageSizeCells(${JSON.stringify({...IMAGE, min_target_bytes: null})})`),
               /נכנס לדיסק מ-לא ידוע/);
});

test('the library table and the image drawer both use it', () => {
  const run = setup();
  run(`IMAGES=[${JSON.stringify(IMAGE)}]; IMAGES_FOLDER=null; FOLDERS=[];`);
  // ‏#954 גל 2: הטבלה החדשה — העמודה "נכנס לדיסק מ-", הבייטים ב-title, הערך LTR.
  const table = run('images()');
  assert.match(table, /<th>נכנס לדיסק מ-<\/th>/);
  assert.match(table, /<span title="229556000000 bytes"><bdi dir="ltr">230 GB<\/bdi><\/span>/);
  const drawer = run(`imageDrawerHtml(${JSON.stringify(IMAGE)})`);
  assert.match(drawer, /נכנס לדיסק מ-/);
  assert.match(drawer, /230 GB/);
  assert.match(drawer, /בשימוש במקור/);
  assert.match(run(`imageDrawerHtml(${JSON.stringify({...IMAGE, used_bytes: null})})`), /בשימוש במקור<\/[^>]+><[^>]+><span class="muted">לא ידוע/);
});
