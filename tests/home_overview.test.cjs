// ‏#954 גל 1: רכיבי הרינדור המשותפים (UI.*) והסקירה הכללית שנבנתה מחדש
// לפי docs/design/console-redesign/home.md. בלי דפדפן: console.js רץ ב-vm
// מול DOM מזערי ו-fetch מזויף, והפלט נבדק כטקסט — מצב מלא, מצב ריק,
// תפקיד deploy, ומה כל לשונית מציגה. עיקרון 5: "לא נקרא" אינו "אין".
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
    '/overview':{images:6,machines:12,storage:{total_bytes:1000*1024**3,free_bytes:312*1024**3},
      session:{id:'s1',group_id:'grp_303',group_label:'כיתה 303',image_name:'Office 2024',state:'open',joined:2,expected_clients:3,starts_in_seconds:252,members:[]},
      room:null,pulls:[]},
    '/tasks':[{id:'t1',name:'Office 2024 v3',machine:'בנייה 1',disk:'sda',folder:'Office',state:'pending',created_at:today+'T09:40:00Z'},
              {id:'t0',name:'Kali',machine:'בנייה 1',disk:'sda',state:'done',error:null,created_at:'2026-09-12T09:40:00Z'}],
    '/net':[{mac:'de:ad:be:ef:00:01',ip:'10.44.12.199',registered:false,last_seen:today+'T09:38:00Z'},
            {mac:'aa:aa:aa:aa:aa:aa',registered:true,last_seen:today+'T09:42:00Z'},
            {mac:'bb:bb:bb:bb:bb:bb',registered:true,last_seen:'2026-09-10T09:42:00Z'}],
    '/ports':[{id:'http_console',name:'HTTP',port:'8081',state:'ok'},{id:'tftp',name:'TFTP',port:'69',state:'ok'},{id:'multicast',name:'Multicast',port:'9000–9001',state:'warn'}],
    '/health':[{id:'server',label:'השרת בכתובת ההפצה',state:'ok',detail:'עונה'},{id:'udp_sender',label:'מולטיקאסט',state:'warn',detail:'udp-sender לא נמצא'}],
    '/journal':[{ts:today+'T09:41:00Z',user:'nadav',event:'session_open',label:'סבב הפצה נפתח לכיתה 303',severity:'info'},
                {ts:'2026-09-16T08:12:00Z',user:'server',event:'transfer_failed',label:'העברה נכשלה',severity:'err'}],
    '/ssh':{stations:{enabled:false,evidence:'closed',detail:'סגור'}},
    '/update':{current:'v0.30.0',enabled:true},
    '/storage-nodes':[{id:'haifa',label:'סניף חיפה',disabled_at:null}],
    '/storage-transfers':[{id:'x1',node_id:'haifa',node_label:'סניף חיפה',image_name:'Ubuntu 24.04',state:'failed',bytes_sent:38,bytes_total:100,error:'הסניף לא ענה',created_at:today+'T08:10:00Z',started_by:'nadav'}],
    '/images':[{id:'i1',name:'Kali 2026.2',folder:'Linux',created:'2026-09-12T10:00:00Z'},{id:'i2',name:'Office',folder:'Office',created:'2026-09-01T10:00:00Z'}],
    '/folders':[{name:'Linux'},{name:'Office'}],
    '/machines':[{mac:'cc:cc:cc:cc:cc:cc',suffix:'2',group_id:'grp_CLONERS'}],'/groups':[],
    '/disk-failures':[{id:1,mac:'cc:cc:cc:cc:cc:cc',disk_number:3,serial:'S5Y2NX0R',cause:'כבל/חריץ SATA 2'}],
    '/shrink-records':[],
  };
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false})},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options={})=>{
      const key=url.replace('/api/console','').split('?')[0]; requests.push({url,options});
      if(fixtures[key] instanceof Error) throw fixtures[key];
      if(fixtures[key]===403) return {status:403,ok:false,json:async()=>({detail:'admin only'})};
      return {status:200,ok:true,headers:{get:()=>null},json:async()=>fixtures[key] || {}};
    }});
  vm.runInContext(fs.readFileSync(path.join(root,'progress.js'),'utf8'),ctx);
  vm.runInContext(fs.readFileSync(path.join(root,'console.js'),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={username:"admin",role:"admin",server_name:"imagectl-srv",version:"v0.30.0",uptime_seconds:532802,deploy_ip:"10.44.12.1",capabilities:{enroll_secondary:true}}; current="home";');
  return {run,node,requests,fixtures};
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
async function loaded(role='admin') {
  const s=setup(); s.run(`ME.role="${role}"`);
  for(const [name,key] of [['IMAGES','/images'],['FOLDERS','/folders'],['MACHINES','/machines'],['DISK_FAILURES','/disk-failures'],['SHRINK_RECORDS','/shrink-records']]) s.run(name+'='+JSON.stringify(s.fixtures[key]));   // נטענים ב-showApp, לא ב-loadHome
  if(role==='deploy') for(const k of ['/health','/journal','/ssh','/update','/storage-nodes','/storage-transfers']) s.fixtures[k]=403;
  await s.run('loadHome()');
  return s;
}

// ---- רכיבי הרינדור המשותפים -------------------------------------------------

test('UI.kpi: class, label, value, meaning line, optional bar; empty class = grey (not green)',()=>{
  const {run}=setup();
  const html=run('UI.kpi({cls:"warn",label:"בריאות",value:"אזהרה",sub:"1 מתוך 9"})');
  assert.match(html,/<div class="kpi warn">/); assert.match(html,/בריאות/); assert.match(html,/אזהרה/); assert.match(html,/1 מתוך 9/); balanced(html);
  const bar=run('UI.kpi({cls:"ok",label:"אחסון",value:"312",unit:"GB פנוי",bar:66})');
  assert.match(bar,/aria-valuenow="66"/); assert.match(bar,/--w:66%/);
  assert.match(run('UI.kpi({label:"x",value:"לא נבדק"})'),/<div class="kpi ">/);
  assert.match(run('UI.kpi({label:"<b>",value:"<i>"})'),/&lt;b&gt;/,'text is escaped');
});
test('ltr() isolates number+unit and date+time runs so RTL text does not swap them',()=>{
  const {run}=setup();
  assert.equal(run('ltr("38.4 GB")'),'<bdi dir="ltr">38.4 GB</bdi>'); assert.equal(run('ltr("<x>")'),'<bdi dir="ltr">&lt;x&gt;</bdi>');
});
test('the header pill names faults as faults, warnings as warnings',async()=>{
  const s=await loaded(); assert.match(s.run('home(0)'),/<span class="pill warn">1 אזהרה<\/span>/);
  s.run('HEALTH=[{id:"a",state:"bad",label:"א"},{id:"b",state:"warn",label:"ב"}]'); assert.match(s.run('home(0)'),/<span class="pill err">2 תקלות<\/span>/);
});
test('UI.status / UI.pill: the empty class is grey and no class maps "not checked" to ok',()=>{
  const {run}=setup();
  assert.equal(run('UI.status("","לא נבדק")'),'<span class="st ">לא נבדק</span>');
  assert.equal(run('UI.pill("err","3")'),'<span class="pill err">3</span>');
});
test('UI.datagrid renders columns and rows, and an empty state that says what to do',()=>{
  const {run}=setup();
  const html=run('UI.datagrid({columns:["א","ב"],rows:[["1","2"],["3","4"]]})');
  assert.match(html,/<table class="dg"><thead><tr><th>א<\/th><th>ב<\/th>/); assert.equal((html.match(/<tr>/g)||[]).length,3); balanced(html);
  assert.equal(run('UI.datagrid({columns:["א"],rows:[],empty:"אין אימג\'ים — קלוט"})'),"<div class=\"empty\">אין אימג'ים — קלוט</div>");
});
test('UI.barRow: unknown progress is a dash, never 0%',()=>{
  const {run}=setup();
  assert.match(run('UI.barRow(null)'),/--w:0%.*>—</); assert.doesNotMatch(run('UI.barRow(null)'),/0%</);
  assert.match(run('UI.barRow(38,"err")'),/bar err.*--w:38%.*>38%</);
  assert.match(run('UI.barRow(50,"","2\\/3")'),/>2\/3</);
});
test('UI.objHeader: crumbs, name, sub, pill, actions and role=tab buttons with roving tabindex; no tabs when only one',()=>{
  const {run}=setup();
  const html=run('UI.objHeader({crumbs:[{label:"שרת"},{label:"סקירה"}],name:"שרת אימג\'ים",sub:"x",pill:UI.pill("ok","תקין"),actions:"<button>a</button>",tabs:["א","ב","ג"],tab:1})');
  assert.match(html,/<div class="crumbs"><span>שרת<\/span><span>\/<\/span><span>סקירה<\/span><\/div>/);
  const tabs=[...html.matchAll(/<button type="button" class="tab( on)?" role="tab" aria-selected="(true|false)" tabindex="(0|-1)"/g)];
  assert.deepEqual(tabs.map(m=>[m[1]||'',m[2],m[3]]),[['','false','-1'],[' on','true','0'],['','false','-1']]);
  assert.match(html,/role="tablist"/); balanced(html);
  assert.doesNotMatch(run('UI.objHeader({name:"x",tabs:["רק אחת"]})'),/role="tab"/,'a single question has no tabs (README §2)');
});
test('UI.kv, UI.timeline, UI.note, UI.empty, UI.soon are balanced and escape text',()=>{
  const {run}=setup();
  for(const s of ['UI.kv([["k","<i>v</i>"]])','UI.timeline([{t:"09:41",cls:"ok",text:"x",who:"nadav"}])','UI.note("warn","<b>x</b>")','UI.empty("ריק","<button>a</button>")','UI.card({title:"t",body:"<p>b</p>"})']) balanced(run(s));
  assert.match(run('UI.timeline([{t:"09:41",cls:"err",text:"x",who:"<s>"}])'),/class="d err".*&lt;s&gt;/);
  assert.equal(run('UI.soon("זמן פעילות")'),'<span class="muted" title="דורש API">זמן פעילות — בקרוב</span>');
});
test('#968: journal severity is the server\'s field, never derived from the event name; a foreign/missing value is neutral',()=>{
  const {run}=setup();
  const cls=r=>run(`journalCls(${JSON.stringify(r)})`);
  // אותו event, חומרה שונה מהשרת → הצבע עוקב אחרי השדה, לא אחרי השם
  assert.equal(cls({event:'capture_failed',severity:'err'}),'err');
  assert.equal(cls({event:'capture_failed',severity:'ok'}),'ok','the name says "failed" — the server said ok; the server wins');
  assert.equal(cls({event:'capture_done',severity:'warn'}),'warn');
  assert.equal(cls({event:'unknown_mac',severity:'info'}),'info');
  // חסר/זר = ניטרלי, לא ניחוש לפי השם
  assert.equal(cls({event:'capture_failed'}),'info','no severity field → neutral, not "err" from the name');
  assert.equal(cls({event:'x',severity:'purple'}),'info');
  assert.equal(run('typeof journalSeverity'),'undefined','the client-side heuristic is gone, not kept beside the field');
});
test('#968: uptime is formatted from /me.uptime_seconds; null is named, never 0',()=>{
  const {run}=setup();
  assert.equal(run('fmtUptime(532802)'),'6 ימים 4 שעות');
  assert.equal(run('fmtUptime(86400)'),'1 יום 0 שעות');
  assert.equal(run('fmtUptime(3660)'),'1 שעה 1 דקה');
  assert.equal(run('fmtUptime(125)'),'2 דקות');
  assert.equal(run('fmtUptime(null)'),'');
  assert.equal(run('homeUptime()'),'פעיל 6 ימים 4 שעות');
  run('ME.uptime_seconds=null');
  assert.equal(run('homeUptime()'),'<span class="muted">זמן פעילות לא נבדק</span>');
  run('ME.uptime_seconds=0');
  assert.equal(run('homeUptime()'),'פעיל 0 דקות','0 is a measurement (just booted), not "unknown"');
});
test('#968: the deploy IP comes from /me.deploy_ip; null is "not configured" (#1088), an older server without the field shows nothing',()=>{
  const {run}=setup();
  assert.equal(run('homeDeployIp()'),'<span class="mono">10.44.12.1</span>');
  run('ME.deploy_ip=null');
  assert.equal(run('homeDeployIp()'),'<span class="muted">רשת הפצה לא הוגדרה</span>');
  run('delete ME.deploy_ip');
  assert.equal(run('homeDeployIp()'),'');
});
test('the global .ok form-message rule is neutralised under .page',()=>{
  const css=fs.readFileSync(path.join(root,'console.css'),'utf8');
  assert.match(css,/\n\.ok\{color:var\(--led-ok\); font-size:12\.5px; min-height:18px; margin-top:6px\}/,'precondition: the global rule still exists for form messages');
  const pageCss=css.slice(css.indexOf('.page{--rp:4px'));
  assert.match(pageCss,/\.page \.ok\{font-size:inherit;min-height:0;margin-top:0;color:inherit\}/);
});
test('fmtDate is dd/mm/yyyy, fmtClock is hh:mm, unknown is a dash',()=>{
  const {run}=setup();
  assert.equal(run('fmtDate("2026-09-17T09:41:00Z")'),'17/09/2026'); assert.equal(run('fmtClock("2026-09-17T09:41:00Z")'),'09:41');
  assert.equal(run('fmtDate(null)'),'—'); assert.equal(run('fmtClock("")'),'—');
});

// ---- הסקירה הכללית: מצב מלא ------------------------------------------------

test('home renders its own header (no generic toolbar, no "+ פעולה", no footer) and the page is balanced',async()=>{
  const {run}=await loaded();
  const html=run('layout(pages.home,0)');
  balanced(html);
  assert.match(html,/^<div class="page"><div class="obj">/);
  assert.match(html,/<div class="obj-name">שרת אימג'ים<\/div>/);
  assert.match(html,/imagectl-srv · <span class="mono">10\.44\.12\.1<\/span> · v0\.30\.0 · פעיל 6 ימים 4 שעות/,'#968: hostname · IP · version · uptime, all from /me');
  assert.doesNotMatch(html,/זמן פעילות — בקרוב/);
  for(const gone of ['pageActionBtn','+ פעולה','footer-note','object-strip','vcenter-tabs','>Details<','אין נתונים להצגה']) assert.doesNotMatch(html,new RegExp(gone.replace(/[+]/g,'\\+')),gone);
  assert.match(html,/onclick="selectPageById\('deploy'\)">\+ סבב הפצה</); assert.match(html,/openCapture\(\)/); assert.match(html,/refreshPage\(\)/);
});
test('KPIs carry their meaning line from the existing API only',async()=>{
  const {run}=await loaded();
  const html=run('home(0)');
  assert.match(html,/<div class="kpi warn"><div class="l">בריאות השרת<\/div><div class="v"><bdi dir="auto">אזהרה<\/bdi><\/div><div class="s">1 מתוך 2 בדיקות: מולטיקאסט/);
  assert.match(html,/<div class="kpi info"><div class="l">סבב הפצה פעיל<\/div><div class="v"><bdi dir="auto">1<\/bdi><\/div><div class="s">כיתה 303 · Office 2024 — 2\/3 מחוברים · מתחיל בעוד 4:12/);
  assert.match(html,/מחשבים רשומים<\/div><div class="v"><bdi dir="auto">12<\/bdi><\/div><div class="s">2 נראו ברשת היום · 1 לא רשומים/);
  assert.match(html,/אימג'ים בספרייה<\/div><div class="v"><bdi dir="auto">6<\/bdi> <small>ב-2 תיקיות<\/small><\/div><div class="s">אחרון: Kali 2026.2 — <bdi dir="ltr">12\/09\/2026<\/bdi>/);
  assert.match(html,/אחסון אימג'ים<\/div><div class="v"><bdi dir="auto">312 GB<\/bdi> <small>פנוי<\/small><\/div>.*aria-valuenow="69".*<bdi dir="ltr">688 GB<\/bdi> מתוך <bdi dir="ltr">1000 GB<\/bdi> בשימוש/);
});
test('"what is happening now" lists the session, the pending capture and the failed transfer with progress and status',async()=>{
  const {run}=await loaded();
  const html=run('home(0)');
  assert.match(html,/מה קורה עכשיו/);
  assert.match(html,/סבב הפצה — Office 2024<\/span><span class="sub">כיתה 303 · פתוח<\/span>.*3 תחנות.*>2\/3<.*<span class="st run">ממתין להצטרפות<\/span>.*startRound\(\).*openRoundDetail\(\)/);
  assert.match(html,/קליטת אימג' — Office 2024 v3<\/span><span class="sub">בנייה 1 · <bdi dir="ltr">sda<\/bdi> · <bdi dir="ltr">\d\d\/\d\d\/\d{4} 09:40<\/bdi>.*<td>תיקיית Office<\/td>.*<span class="st warn">ממתין שהמחשב יעלה ב-PXE<\/span>/,'#968: the target column is the capture folder from /tasks');
  assert.doesNotMatch(html,/ספריית האימג'ים<\/td>/,'no generic "library" target when the folder is known');
  assert.doesNotMatch(html.slice(0,html.indexOf('אירועים אחרונים')),/קליטת אימג' — Kali/,'a finished capture is not "now"');
  assert.match(html,/העברה לסניף סניף חיפה — Ubuntu 24.04.*bar err.*--w:38%.*<span class="st err">נכשל — הסניף לא ענה<\/span>/);
});
test('"needs attention" lists the red disk, the unregistered machine, the unreachable branch and the health warning — each with an action',async()=>{
  const {run}=await loaded();
  run('SECONDARY_STATUS={haifa:{connected:false,error:"timeout"}}');
  const html=run('home(0)');
  const card=html.slice(html.indexOf('דורש טיפול'),html.indexOf('אירועים אחרונים'));
  assert.match(card,/<span class="pill err">4<\/span>/);
  assert.match(card,/<b>דיסק אדום<\/b> — 2 · דיסק 3 \(<span class="mono">S5Y2NX0R<\/span>\) נכשל בכתיבה, סיבה: כבל\/חריץ SATA 2.*selectPageById\('machines'\)/);
  assert.match(card,/<b>מכונה לא רשומה<\/b> ברשת ההפצה — <span class="mono">de:ad:be:ef:00:01<\/span> קיבלה <bdi dir="ltr">10.44.12.199<\/bdi>.*openNewMachine\(\)/);
  assert.match(card,/<b>סניף סניף חיפה לא מגיב<\/b> — timeout.*selectPageById\('branches'\)/);
  assert.match(card,/<b>מולטיקאסט<\/b> — udp-sender לא נמצא.*selectPageById\('health'\)/);
  assert.doesNotMatch(card,/השרת בכתובת ההפצה/,'ok checks are not attention items');
});
test('recent events come from /journal (6 rows, dd/mm for older days, severity from the server) and link to the journal',async()=>{
  const {run,requests}=await loaded();
  assert.ok(requests.some(r=>r.url==='/api/console/journal?limit=50'));
  const html=run('home(0)');
  assert.match(html,/<span class="t">09:41<\/span><span class="d info"><\/span><span>סבב הפצה נפתח לכיתה 303<\/span><span class="who">nadav<\/span>/);
  assert.match(html,/<span class="t">16\/09\/2026<\/span><span class="d err"><\/span><span>העברה נכשלה<\/span>/);
  assert.match(html,/selectPageById\('logs'\)">ליומן המלא/);
});
test('"the server" card reads ports, ssh, branches and update; DHCP without net.js is "not read", not off',async()=>{
  const {run}=await loaded();
  run('SECONDARY_STATUS={haifa:{connected:true}}');
  const html=run('home(0)');
  const card=html.slice(html.indexOf('>השרת<'));
  assert.match(card,/API \/ קונסולה<\/span><span class="v"><span class="st ok">תקין · 8081<\/span>/);
  assert.match(card,/מולטיקאסט<\/span><span class="v"><span class="st warn">אזהרה · 9000–9001<\/span>/);
  assert.match(card,/DHCP הפצה<\/span><span class="v"><span class="st ">לא נקרא<\/span>/);
  assert.match(card,/SSH לתחנות<\/span><span class="v"><span class="st ">כבוי<\/span>/);
  assert.match(card,/סניפים<\/span><span class="v"><span class="st ok">1 מחוברים<\/span>/);
  assert.match(card,/עדכון<\/span><span class="v">v0\.30\.0 · <a role="link" tabindex="0" onclick="selectPageById\('health'\)">בדוק<\/a>/);
});
test('tabs: "משימות" is the full 20-task table, "אירועים" is 50 events; activateTab keeps the header',async()=>{
  const {run}=await loaded();
  const tasks=run('layout(pages.home,1)');
  assert.match(tasks,/class="tab on" role="tab" aria-selected="true" tabindex="0" onclick="activateTab\(1\)">משימות/);
  assert.match(tasks,/קליטת אימג' — Kali.*<span class="st ok">הושלם<\/span>/,'the history tab shows finished captures');
  assert.doesNotMatch(tasks,/class="kpi/);
  const events=run('layout(pages.home,2)');
  assert.match(events,/>אירועים <small>50 האחרונים<\/small>/); assert.match(events,/העברה נכשלה/); balanced(events);
});
test('loadHome reads every source once and re-renders; the summary polls only /overview and /tasks',async()=>{
  const {requests,node}=await loaded();
  const urls=requests.map(r=>r.url.replace('/api/console',''));
  for(const u of ['/overview','/tasks','/net','/ports','/health','/journal?limit=50','/ssh','/update','/storage-nodes','/storage-transfers']) assert.ok(urls.includes(u),u);
  assert.match(node('#content').innerHTML,/class="kpi/);
});

test('the 2-second re-render keeps the scroll position of the page',async()=>{
  const {run,node}=await loaded();
  // הגולל המזויף: הצבת innerHTML ב-#content "מאפסת" אותו כמו דפדפן אמיתי
  const scroller=node('#content .page, #content .scroll'); scroller.scrollTop=420;
  const content=node('#content'); let html=content.innerHTML;
  Object.defineProperty(content,'innerHTML',{get(){return html;},set(v){html=v;scroller.scrollTop=0;}});
  run('renderCurrent()');
  assert.equal(scroller.scrollTop,420);
  assert.match(html,/class="kpi/);
});

// ---- מצבים ריקים ולא-נקראים ---------------------------------------------------

test('empty server: KPIs say "none"/"unknown" in grey, the now-table says what to do, attention is an honest empty',async()=>{
  const s=setup();
  Object.assign(s.fixtures,{'/overview':{images:0,machines:0,storage:null,session:null,room:null,pulls:[]},'/tasks':[],'/net':[],'/health':[],'/journal':[],'/storage-nodes':[],'/storage-transfers':[],'/disk-failures':[]});
  s.run('IMAGES=[];FOLDERS=[];DISK_FAILURES=[];SHRINK_RECORDS=[]');
  await s.run('loadHome()');
  const html=s.run('home(0)');
  assert.match(html,/<div class="kpi ok"><div class="l">בריאות השרת<\/div><div class="v"><bdi dir="auto">תקין<\/bdi><\/div><div class="s">0 בדיקות עברו/);
  assert.match(html,/<div class="kpi "><div class="l">סבב הפצה פעיל<\/div><div class="v"><bdi dir="auto">אין<\/bdi><\/div>/);
  assert.match(html,/<div class="kpi "><div class="l">אחסון אימג'ים<\/div><div class="v"><bdi dir="auto">לא ידוע<\/bdi><\/div>/);
  assert.match(html,/אין אימג'ים — קלוט ממחשב בנייה או העלה tar/);
  assert.match(html,/<div class="empty">אין פעילות עכשיו — פתח סבב הפצה או קלוט אימג'<\/div>/);
  assert.match(html,/<div class="empty">אין פריטים לטיפול<\/div>/);
  assert.match(html,/<div class="empty">אין אירועים עדיין<\/div>/);
  assert.match(html,/<span class="pill ok">תקין<\/span>/);
  balanced(html);
});
test('a source that could not be read is shown as unread — never as green or as "nothing to do" (principle 5)',async()=>{
  const s=setup();
  s.fixtures['/health']=new Error('offline'); s.fixtures['/net']=new Error('net down'); s.fixtures['/journal']=new Error('no journal');
  s.run('DISK_FAILURES=[];SHRINK_RECORDS=[];HEALTH=null;toast=()=>{}');
  await s.run('loadHome()');
  const html=s.run('home(0)');
  assert.match(html,/<div class="kpi "><div class="l">בריאות השרת<\/div><div class="v"><bdi dir="auto">לא נבדק<\/bdi><\/div>/);
  assert.match(html,/<span class="pill ">לא נבדק<\/span>/);
  assert.match(html,/רשת לא נקראה: net down/);
  assert.match(html,/לא נקראו: בריאות, רשת — לא ניתן לומר שאין מה לטפל/);
  assert.doesNotMatch(html,/אין פריטים לטיפול/);
  assert.match(html,/היומן לא נקרא: no journal/);
  assert.doesNotMatch(html,/class="kpi ok"><div class="l">בריאות/);
});
test('a failed /overview read marks the page stale instead of pretending',async()=>{
  const s=setup(); s.fixtures['/overview']=new Error('offline'); s.run('renderActivity=()=>{}');
  await s.run('refreshStatus()');
  assert.match(s.run('home(0)'),/<div class="note warn">.*Server status could not be read/);
});

// ---- תפקיד deploy --------------------------------------------------------------

test('deploy sees the summary without attention, events and server cards; two tabs; the first KPI is the round state',async()=>{
  const {run,requests}=await loaded('deploy');
  const urls=requests.map(r=>r.url.replace('/api/console',''));
  for(const u of ['/health','/journal?limit=50','/ssh','/update']) assert.ok(!urls.includes(u),u+' must not be requested by deploy');
  const html=run('layout(pages.home,0)');
  assert.equal((html.match(/role="tab"/g)||[]).length,2);
  assert.doesNotMatch(html,/דורש טיפול|אירועים אחרונים|>השרת<|openCapture/);
  assert.match(html,/<div class="kpi info"><div class="l">מצב הסבב<\/div><div class="v"><bdi dir="auto">פתוח<\/bdi><\/div><div class="s">2 מתוך 3 מחוברים/);
  assert.match(html,/<div class="c12 card"><div class="card-h"><span>מה קורה עכשיו/,'deploy has no attention card beside it — full width');
  assert.match(html,/<span class="pill info">פתוח<\/span>/);
  assert.match(run('layout(pages.home,2)'),/class="kpi/,'an events tab index falls back to the summary for deploy');
});

// ---- הסרגל: אייקון תיקייה לאימג'ים ------------------------------------------

test('the images tree node and its folders use the folder icon (Nadav, 17/09)',()=>{
  const {run,node}=setup();
  const index=fs.readFileSync(path.join(root,'index.html'),'utf8');
  const start=index.indexOf('data-page="images"');
  const imagesNode=index.slice(start,index.indexOf("אימג'ים</span>",start));
  assert.match(imagesNode,/<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"\/>/);
  assert.doesNotMatch(imagesNode,/circle cx="16"/,'the picture icon is gone from the images node');
  run('FOLDERS=[{name:"Linux"},{name:"Empty"}]; IMAGES=[{id:"i1",name:"Kali",folder:"Linux"}]; populateSidebarImages()');
  const tree=node('#imagesTree').innerHTML;
  assert.equal((tree.match(/M3 7a2 2 0 0 1 2-2h4l2 2h8/g)||[]).length,2,'both folders carry the folder icon');
  assert.equal((tree.match(/circle cx="16"/g)||[]).length,1,'the image inside keeps the picture icon');
});
