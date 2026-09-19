// ‏#954 גל 2: ספריית האימג'ים שנבנתה מחדש לפי docs/design/console-redesign/images.md
// — עץ תיקיות, טבלת התיקייה, בחירה מרובה, גרירה = העברה/סדר, מגירת אימג',
// לשוניות קליטות/אחסון. בלי דפדפן: console.js רץ ב-vm מול DOM מזערי ו-fetch
// מזויף, והפלט נבדק כטקסט. עיקרון 5: "לא נקרא"/"לא ידוע" אינם "אין"/0.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');
const today=new Date().toISOString().slice(0,10);

function setup() {
  const nodes=new Map(), requests=[];
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',dataset:{},style:{removeProperty(){}},
      classList:{add(){},remove(){},toggle(){}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.innerHTML+=html;},setAttribute(){},removeAttribute(){},focus(){}});
    return nodes.get(key);
  }
  const fixtures={
    '/images':[
      {id:'img_7f3a91',name:'Office 2024 — סטנדרט',description:'Windows 11 + Office',folder:'Office',sort:1,family:256,os:'windows',created:'2026-09-02T09:14:00+03:00',source_disk_bytes:256060514304,min_target_bytes:254908301312,used_bytes:84540833792,total_compressed_bytes:57982058496,partitions:2},
      {id:'img_a1b2c3',name:'Office 2024 — מרצה',description:'',folder:'Office',sort:2,family:256,os:'windows',created:'2026-09-10T11:02:00+03:00',source_disk_bytes:512110190592,min_target_bytes:null,used_bytes:null,total_compressed_bytes:57982058496,partitions:2},
      {id:'img_2c8e04',name:'CAD Heavy',description:'SolidWorks',folder:'',sort:1,family:500,os:'windows',created:'2026-08-20T08:00:00+03:00',source_disk_bytes:256060514304,total_compressed_bytes:57982058496,partitions:2},
      {id:'img_lnx001',name:'Ubuntu 24.04',description:'',folder:'Linux',sort:1,family:256,os:'linux',created:'2026-09-12T10:00:00+03:00',source_disk_bytes:256060514304,min_target_bytes:255000000000,total_compressed_bytes:21474836480,partitions:3}],
    '/folders':[{name:'Office',description:'אימג׳י משרד',images:2},{name:'סייבר',description:'',images:0},{name:'Linux',description:'',images:1}],
    '/overview':{images:4,machines:3,storage:{total_bytes:1000*1024**3,free_bytes:312*1024**3},
      session:{id:'s1',image_id:'img_7f3a91',image_name:'Office 2024 — סטנדרט',group_label:'כיתה 303',state:'open',joined:2,expected_clients:3,members:[]},
      room:{id:7,image_id:'img_lnx001',image_name:'Ubuntu 24.04',wave_number:2,wave_state:'running',target_drives:6,written_drives:2},pulls:[]},
    '/tasks':[{id:'t1',type:'capture',name:'Office 2024 v3',machine:'בנייה 1',mac:'cc',disk:'sda',state:'pending',bytes_written:0,bytes_total:null,image_id:'img_new',created_at:today+'T09:40:00Z',updated_at:today+'T09:40:00Z'},
              {id:'t0',type:'capture',name:'Ubuntu 24.04',machine:'בנייה 1',mac:'cc',disk:'sda',state:'done',error:null,bytes_written:21474836480,bytes_total:21474836480,image_id:'img_lnx001',created_at:'2026-09-12T09:40:00Z',updated_at:'2026-09-12T09:50:00Z'}],
    '/journal':[{ts:'2026-09-12T10:00:00Z',user:'nadav',event:'capture_done',label:'אימג\' נקלט',text:'"Ubuntu 24.04"',severity:'ok'},
                {ts:today+'T08:00:00Z',user:'nadav',event:'image_edit',label:'אימג\' עודכן',text:'"Ubuntu 24.04" — עודכנו: תיאור',severity:'info'}],
  };
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false})},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options={})=>{
      const key=url.replace('/api/console','').split('?')[0]; requests.push({url,options});
      if(fixtures[key] instanceof Error) throw fixtures[key];
      if(fixtures[key]===403) return {status:403,ok:false,json:async()=>({detail:'admin only'})};
      return {status:200,ok:true,headers:{get:()=>null},json:async()=>fixtures[key] || {ok:true}};
    }});
  vm.runInContext(fs.readFileSync(path.join(root,'progress.js'),'utf8'),ctx);
  vm.runInContext(fs.readFileSync(path.join(root,'console.js'),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={username:"admin",role:"admin",server_name:"srv",version:"v0.31.0",capabilities:{classrooms:true}}; current="images"; sheet = o => { globalThis.formOptions=o; }; confirmSheet=(t,s,l,fn)=>{ globalThis.confirmFn=fn; };');
  for(const [name,key] of [['OVERVIEW','/overview'],['IMAGES','/images'],['FOLDERS','/folders'],['CAPTURE_TASKS','/tasks']]) run(name+'='+JSON.stringify(fixtures[key]));
  return {run,node,requests,fixtures,ctx};
}
function balanced(html) {
  const stack=[]; const voids=new Set(['input','br','hr','img','meta','link','source','wbr','path','rect','circle']);
  for(const m of html.matchAll(/<\/?([a-z][\w-]*)\b[^>]*>/gi)) {
    const tag=m[1].toLowerCase();
    if(m[0].endsWith('/>') || voids.has(tag)) continue;
    if(m[0].startsWith('</')) assert.equal(stack.pop(),tag,'Unbalanced '+m[0]); else stack.push(tag);
  }
  assert.deepEqual(stack,[],'Unclosed tags');
}
const dragEvent=(data)=>({preventDefault(){},currentTarget:{classList:{add(){},remove(){}}},dataTransfer:{effectAllowed:'',setData(k,v){data[k]=v;},getData(k){return data[k]||'';}}});

test('the page is the datastore browser: header, three tabs, tree with counters, one datagrid', () => {
  const {run}=setup(); const html=run('images(0)'); balanced(html);
  assert.match(html,/class="page"/); assert.match(html,/ספריית אימג&#39;ים/);
  assert.match(html,/4 אימג'ים · 3 תיקיות/);
  assert.match(html,/688 GB<\/bdi> בשימוש · <bdi dir="ltr">312 GB<\/bdi> פנוי/);
  for(const t of ['קבצים','קליטות','אחסון']) assert.match(html,new RegExp('role="tab"[^>]*>'+t+'<'));
  assert.match(html,/\+ קליטה ממחשב בנייה/); assert.match(html,/העלאת קובץ tar/); assert.match(html,/\+ תיקייה/);
  assert.match(html,/role="tree"/);
  assert.match(html,/כל האימג&#39;ים<\/span><span class="cnt">4</); assert.match(html,/Office<\/span><span class="cnt">2</);
  assert.match(html,/סייבר<\/span><span class="cnt">0</); assert.match(html,/ללא תיקייה<\/span><span class="cnt">1</);
  assert.equal((html.match(/<table class="dg"/g)||[]).length,1);
  for(const col of ['שם','מערכת','נכנס לדיסק מ-','בשרת','נוצר','אימות','בשימוש']) assert.match(html,new RegExp('<th>'+col+'</th>'));
  assert.doesNotMatch(html,/כללי בטיחות|פעולות מהירות|מטא־נתונים|>Up<|>Down<|>Edit</);
});

test('rows: "fits from X GB" is decimal-ceil with bytes in the tooltip, null is "unknown" not 0, usage comes from /overview only', () => {
  const {run}=setup(); const html=run('images(0)');
  assert.match(html,/title="254908301312 bytes"><bdi dir="ltr">255 GB<\/bdi>/);
  assert.match(html,/title="255000000000 bytes"><bdi dir="ltr">255 GB<\/bdi>/);
  const teacher=html.slice(html.indexOf('img_a1b2c3'),html.indexOf('img_2c8e04'));
  assert.match(teacher,/לא ידוע/); assert.doesNotMatch(teacher,/>0 GB</);
  const cad=html.slice(html.indexOf('img_2c8e04'),html.indexOf('img_lnx001'));
  assert.match(cad,/לא ידוע/,'field absent (pre-#953 server) is unknown, not invented');
  assert.match(html,/סבב פתוח · כיתה 303/); assert.match(html,/סבב שיכפול · גל 2/);
  assert.equal((html.match(/st ok">sha256 אומת</g)||[]).length,4,'every image in the library was verified on entry');
  assert.doesNotMatch(html,/st ok">sha256 תקין/,'"תקין now" only after a scrub in this session');
  assert.match(html,/02\/09\/2026/); assert.match(html,/54\.0 GB/);
  assert.match(html,/onclick="deployImage\('img_7f3a91'\)">הפץ</); assert.match(html,/onclick="openImageDetail\('img_7f3a91'\)">פרטים</);
});

test('a running capture is a grey row inside the table, under "all images", with cancel — not a bar above', () => {
  const {run}=setup(); const html=run('images(0)');
  assert.match(html,/<tr class="task">/); assert.match(html,/Office 2024 v3/); assert.match(html,/ממתין שמחשב הבנייה בנייה 1 יעלה ב-PXE/);
  assert.match(html,/cancelCapture\(decodeURIComponent\('t1'\)\)">בטל קליטה/); assert.match(html,/id="capture-bar"><\/div>/,'the bar above the table carries only done+error warnings');
  assert.match(html,/indeterminate|לא ידוע/,'pending capture with no denominator is not 0%');
  run('selectImagesFolder("Office")'); assert.doesNotMatch(run('images(0)'),/<tr class="task">/,'/tasks has no folder (#968) — shown only under all images');

  run('CAPTURE_TASKS=[{id:"t9",name:"office365",state:"done",error:"sda: המקור לא הוחזר לגודלו"}]');
  const warned=run('images(0)'); assert.match(warned,/id="capture-bar"><div class="note warn">.*office365.*המקור לא הוחזר לגודלו/,'#927: a done+error capture stays visible above the table');
});

test('folder selection, breadcrumb, description, edit/delete of the folder, and the text filter', () => {
  const {run,node}=setup();
  run('selectImagesFolder("Office")'); let html=run('images(0)');
  assert.match(html,/onclick="selectImagesFolder\(null\)">כל האימג'ים<\/a><span>\/<\/span><b>Office<\/b>/);
  assert.match(html,/— אימג׳י משרד/); assert.match(html,/עריכת תיקייה/); assert.doesNotMatch(html,/מחק תיקייה/,'a folder with images cannot be deleted');
  assert.equal((html.match(/data-id="img_/g)||[]).length,2);
  run('selectImagesFolder("%D7%A1%D7%99%D7%99%D7%91%D7%A8")'); html=run('images(0)');
  assert.match(html,/התיקייה ריקה/); assert.match(html,/מחק תיקייה/);
  run('selectImagesFolder("")'); html=run('images(0)');
  assert.match(html,/<b>ללא תיקייה<\/b>/); assert.equal((html.match(/data-id="img_/g)||[]).length,1); assert.match(html,/CAD Heavy/);
  run('selectImagesFolder(null); imagesFilter("ubuntu")');
  assert.equal((node('#img-table').innerHTML.match(/data-id="img_/g)||[]).length,1);
  run('imagesFilter("zzz")'); assert.match(node('#img-table').innerHTML,/אין אימג&#39;ים שתואמים לסינון/);
  run('imagesFilter("")'); assert.equal((node('#img-table').innerHTML.match(/data-id="img_/g)||[]).length,4);
});

test('an empty library says what to do; an unread library is a placeholder, not "no images"', () => {
  const {run}=setup();
  run('IMAGES=[];FOLDERS=[];CAPTURE_TASKS=[]'); const html=run('images(0)');
  assert.match(html,/אין אימג&#39;ים בספרייה — קלוט ממחשב בנייה או העלה קובץ tar/); assert.match(html,/onclick="openCapture/);
  run('IMAGES=null'); assert.match(run('images(0)'),/טוען נתונים/); assert.doesNotMatch(run('images(0)'),/אין אימג'ים/);
});

test('multi-select: bar counts, move-to-folder PUTs every selected image, delete demands each name typed', async () => {
  const {run,node,requests}=setup();
  run('toggleImgSel("img_7f3a91",true); toggleImgSel("img_a1b2c3",true)');
  let html=run('images(0)');
  assert.match(html,/נבחרו 2 מתוך 4/); assert.match(html,/bulkMove\(\)">העבר לתיקייה…/); assert.match(html,/bulkDelete\(\)">מחיקה/);
  assert.doesNotMatch(html,/שינוי שם/,'rename is a single-image action'); assert.equal((html.match(/<tr class="sel"/g)||[]).length,2);
  run('loadImages=async()=>{}; bulkMove()');
  await run('formOptions.onSubmit({folder:"Linux"})');
  const puts=requests.filter(r=>r.options.method==='PUT');
  assert.deepEqual(puts.map(r=>r.url).sort(),['/api/console/images/img_7f3a91','/api/console/images/img_a1b2c3']);
  assert.equal(JSON.parse(puts[0].options.body).folder,'Linux'); assert.equal(run('IMG.sel.size'),0);
  run('toggleImgSel("img_7f3a91",true); toggleImgSel("img_2c8e04",true); bulkDelete()');
  assert.equal(run('formOptions.fields.length'),2); assert.equal(run('formOptions.danger'),true);
  await assert.rejects(run('formOptions.onSubmit({n0:"Office 2024 — סטנדרט",n1:"wrong"})'),/השם שהוקלד אינו זהה: CAD Heavy/);
  assert.ok(!requests.some(r=>r.url.endsWith('/delete')),'nothing deleted while a name mismatches');
  await run('formOptions.onSubmit({n0:"Office 2024 — סטנדרט",n1:"CAD Heavy"})');
  const dels=requests.filter(r=>r.url.endsWith('/delete'));
  assert.equal(dels.length,2); assert.equal(JSON.parse(dels[1].options.body).confirm_name,'CAD Heavy');
  run('toggleImgSel("img_lnx001",true); toggleImgSelAll(true)'); assert.match(run('images(0)'),/נבחרו 4 מתוך 4/);
  run('imgSelClear()'); assert.doesNotMatch(run('images(0)'),/dg-bar foot/);
});

test('drag: image onto a folder = PUT folder (selection travels), folder onto folder = /folders/order, row onto row = sort', async () => {
  const {run,requests,ctx}=setup(); run('loadImages=async()=>{}');
  const html=run('images(0)');
  assert.match(html,/draggable="true" ondragstart="imgDragStart\(event,'img_7f3a91'\)"/);
  assert.match(html,/ondrop="imgDrop\(event,'Linux'\)"/); assert.match(html,/ondrop="imgDrop\(event,''\)"/,'"no folder" is a drop target');
  assert.doesNotMatch(html,/ondrop="imgDrop\(event,null\)"/,'"all images" is not a folder');
  const data={}; ctx.EV=dragEvent(data);
  run('IMG.sel.add("img_7f3a91"); IMG.sel.add("img_a1b2c3"); imgDragStart(EV,"img_7f3a91")');
  assert.deepEqual(JSON.parse(data['application/x-imagectl-images']),['img_7f3a91','img_a1b2c3'],'dragging a selected row drags the whole selection');
  await run('imgDrop(EV,"Linux")');
  const puts=requests.filter(r=>r.options.method==='PUT').map(r=>[r.url,JSON.parse(r.options.body).folder]);
  assert.deepEqual(puts,[['/api/console/images/img_7f3a91','Linux'],['/api/console/images/img_a1b2c3','Linux']]);
  const fdata={}; ctx.EV=dragEvent(fdata);
  run('folderDragStart(EV,"Linux")'); await run('imgDrop(EV,"Office")');
  const order=requests.find(r=>r.url==='/api/console/folders/order');
  assert.deepEqual(JSON.parse(order.options.body).names,['Linux','Office','סייבר']);
  requests.length=0; const rdata={}; ctx.EV=dragEvent(rdata);
  run('IMG.sel.clear(); imgDragStart(EV,"img_a1b2c3")'); await run('imgDropOnRow(EV,"img_7f3a91")');
  const sorts=requests.filter(r=>r.options.method==='PUT').map(r=>[r.url.split('/').pop(),JSON.parse(r.options.body).sort]);
  assert.deepEqual(sorts,[['img_a1b2c3',1],['img_7f3a91',2]]);
  requests.length=0; await run('imgDropOnRow(EV,"img_lnx001")');
  assert.equal(requests.length,0,'no reorder across folders');
});

test('the image drawer: header line, actions, verification note, properties, "requires API" for partitions, history from journal + tasks', async () => {
  const {run,node,requests}=setup();
  run('openImageDetail("img_lnx001")');
  assert.equal(node('#drawerTitle').textContent,'Ubuntu 24.04');
  let html=node('#drawerBody').innerHTML; balanced(html);
  assert.match(html,/class="page drw"/); assert.match(html,/Linux \/ <span class="mono">img_lnx001<\/span> · לינוקס · נוצר <bdi dir="ltr">12\/09\/2026/);
  assert.match(html,/deployImage\('img_lnx001'\)">הפץ לכיתה…/); assert.match(html,/href="\/api\/console\/images\/img_lnx001\/download">הורדה/);
  assert.match(html,/sha256 של כל מחיצה אומת בכניסה לספרייה/); assert.match(html,/scrubImage\('img_lnx001'\)">הרץ עכשיו/);
  assert.match(html,/<span class="k">תיקייה<\/span><span class="v">Linux <a[^>]*>העבר/);
  assert.match(html,/<span class="k">מחיצות<\/span><span class="v">3</);
  assert.match(html,/<span class="k">שימוש<\/span><span class="v">סבב שיכפול · גל 2/);
  assert.match(html,/דורש API/,'partition table is not invented'); assert.doesNotMatch(html,/Secure Boot/);
  assert.match(html,/נקלט ממחשב הבנייה בנייה 1 · sda/,'capture history from /tasks');
  assert.match(html,/מחיקה \(הקלדת שם\)/);
  const j=requests.find(r=>r.url.startsWith('/api/console/journal?'));
  assert.equal(new URL(j.url,'http://x').searchParams.get('q'),'Ubuntu 24.04');
  await new Promise(r=>setImmediate(r)); await new Promise(r=>setImmediate(r));
  html=node('#drawerBody').innerHTML;
  assert.match(html,/אימג&#39; עודכן — &quot;Ubuntu 24.04&quot; — עודכנו: תיאור/); assert.match(html,/12\/09\/2026/);
  assert.ok(html.indexOf('אימג\' עודכן')<html.indexOf('נקלט ממחשב הבנייה'),'newest first');
});

test('a scrub result overrides the verification column and fills the partition table with per-file sha256 states', async () => {
  const {run,node,fixtures}=setup(); run('renderCurrent=()=>{}');
  fixtures['/images/img_7f3a91/scrub']={intact:false,images:[{id:'img_7f3a91',state:'drift',files:[{index:1,file:'p1.esp.pcl.zst',state:'ok'},{index:3,file:'p3.win.pcl.zst',state:'mismatch'}]}]};
  await run('scrubImage("img_7f3a91")');
  const html=run('images(0)');
  assert.match(html,/st err">sha256 לא תואם: p3\.win\.pcl\.zst/);
  const drawer=node('#drawerBody').innerHTML;
  assert.match(drawer,/note err/); assert.match(drawer,/אין להפיץ אותו/);
  assert.match(drawer,/<th>sha256<\/th>/); assert.match(drawer,/st ok">תקין/); assert.match(drawer,/st err">mismatch/);
  assert.match(run('images(2)'),/sha256 לא תואם/,'storage tab lists the session results');
});

test('captures tab lists /tasks with state colours; storage tab reads /overview and never shows unread as empty', () => {
  const {run}=setup();
  let html=run('images(1)'); balanced(html);
  assert.match(html,/<th>מחשב בנייה<\/th>/); assert.match(html,/st warn">ממתין שהמחשב יעלה ב-PXE/); assert.match(html,/st ok">הושלם/);
  assert.match(html,/cancelCapture\(decodeURIComponent\('t1'\)\)">ביטול/); assert.match(html,/openImageDetail\('img_lnx001'\)">פרטים/);
  run('CAPTURE_TASKS=[]'); assert.match(run('images(1)'),/אין קליטות עדיין/);
  html=run('images(2)'); balanced(html);
  assert.match(html,/פנוי בדיסק האימג&#39;ים/); assert.match(html,/312 GB/); assert.match(html,/688 GB<\/bdi> בשימוש מתוך <bdi dir="ltr">1000 GB/); assert.match(html,/aria-valuenow="69"/);
  assert.match(html,/scrubLibrary\(\)">אמת את כל הספרייה/); assert.match(html,/דורשת API/);
  run('OVERVIEW=null'); html=run('images(2)'); assert.match(html,/לא נקרא/); assert.doesNotMatch(html,/aria-valuenow/);
});

test('deploy role: browse, deploy, download and details — no capture, upload, folders, move, rename, delete, scrub, drag', () => {
  const {run,node}=setup(); run('ME.role="deploy"');
  const html=run('images(0)');
  for(const bad of ['openCapture','openImageIngest','addFolderSheet','draggable=','ondrop=','bulkMove','bulkDelete','עריכת תיקייה','cancelCapture']) assert.doesNotMatch(html,new RegExp(bad),bad);
  assert.match(html,/deployImage\(/); assert.match(html,/<input type="checkbox"/);
  run('toggleImgSel("img_7f3a91",true)'); const bar=run('images(0)');
  assert.match(bar,/bulkDownload\(\)">הורדה/); assert.doesNotMatch(bar,/מחיקה|העבר לתיקייה|שינוי שם/);
  run('openImageDetail("img_7f3a91")'); const d=node('#drawerBody').innerHTML;
  assert.match(d,/הורדה/); assert.doesNotMatch(d,/scrubImage|renameImage|deleteImage|moveImage|editImageDescription/);
  assert.match(d,/היסטוריית היומן זמינה למנהל בלבד/);
  assert.doesNotMatch(run('images(2)'),/scrubLibrary/);
});

test('the capture list re-renders the page only when the tasks actually changed (selection survives polling)', async () => {
  const {run,fixtures}=setup(); let renders=0; run('renderCurrent=()=>{globalThis.n=(globalThis.n||0)+1}; renderActivity=()=>{}');
  await run('loadCaptures()'); await run('loadCaptures()');
  renders=run('globalThis.n'); assert.equal(renders,1);
  fixtures['/tasks'][0].state='running'; await run('loadCaptures()'); assert.equal(run('globalThis.n'),2);
});

test('the pages table owns the new page and the old renderer entry is gone', () => {
  const {run}=setup();
  assert.equal(run('pages.images.own'),true); assert.equal(JSON.stringify(run('pages.images.tabs')),JSON.stringify(['קבצים','קליטות','אחסון']));
  assert.match(run('layout(pages.images,1)'),/class="page"/);
  assert.equal(run('typeof filterImagesFolder'),'undefined'); assert.equal(run('typeof reorderImage'),'undefined');
});
