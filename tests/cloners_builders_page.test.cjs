// ‏#954 גל 3א: מחשבי שיכפול (חדר השיכפולים) ומחשבי בנייה כאובייקטים, לפי
// docs/design/console-redesign/cloners.md ו-builders.md — על groupPage של גל 3.
// בלי דפדפן: console.js + net.js + progress.js רצים ב-vm מול DOM מזערי ו-fetch מזויף.
// עיקרון 5: "לא נבדק" ≠ ירוק; ROOM null (לא נקרא) ≠ חדר בלי סבב; /tasks שלא נקרא ≠ אין קליטות.
// מוסכמת נדב: "דיסק N · SATA N-1", לעולם לא sd*.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');
const minutesAgo=(n)=>new Date(Date.now()-n*60000).toISOString();
const GB256=256060514304;

const B1='c8:d9:d2:0b:fe:32', B2='c8:d9:d2:0b:fe:34', C1='a0:48:1c:8a:18:40', C2='78:ac:c0:9b:11:c2', C3='78:ac:c0:9b:11:c3';
const drawer=(dev,port,serial,extra={})=>({dev,port,serial,model:'870 EVO',size_bytes:GB256,fresh:true,selected:false,state:null,error:null,bytes_written:0,bytes_total:0,stalled_s:null,crc_delta:null,smart:'unchecked',...extra});

function setup({roundOpen=false}={}) {
  const nodes=new Map(), requests=[], opened=[];
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},style:{removeProperty(){}},
      classList:{add(){},remove(){},toggle(){}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.innerHTML+=html;},setAttribute(){},removeAttribute(){},focus(){}});
    return nodes.get(key);
  }
  const roomIdle={round:null,stream_stalled:false,machines:[
    {mac:C1,name:'מחשב 1',drawer_count:2,awake:true,drawers:0,fresh_drawers:0,drawer_list:[],joined:false,state:null,bytes_written:0,bytes_total:0,error:null},
    {mac:C2,name:'מחשב 2',drawer_count:3,awake:true,drawers:3,fresh_drawers:3,joined:false,state:null,bytes_written:0,bytes_total:0,error:null,
      drawer_list:[drawer('sda',1,'S5Y30',{smart:'ok'}),drawer('sdb',2,'S5Y31'),drawer('sdc',3,'S5Y2NX0R12345',{smart:'ok'})]},
    {mac:C3,name:'מחשב 3',drawer_count:1,awake:false,drawers:0,fresh_drawers:0,drawer_list:[],joined:false,state:null,bytes_written:0,bytes_total:0,error:null}]};
  const roomLive={round:{id:'rnd_1',image_id:'img_1',image_name:'office365',target_drives:5,written_drives:2,remaining_drives:3,wave_number:2,wave_state:'running',ready_drives:3,opened_by:'nadav',source:null},
    stream_stalled:false,machines:[
    {...roomIdle.machines[0]},
    {...roomIdle.machines[1],joined:true,state:'running',bytes_written:38,bytes_total:100,
      drawer_list:[drawer('sda',1,'S5Y30',{smart:'ok',state:'writing',bytes_written:38,bytes_total:100,stalled_s:2}),
                   drawer('sdb',2,'S5Y31',{state:'writing',bytes_written:37,bytes_total:100,crc_delta:3,stalled_s:90}),
                   drawer('sdc',3,'S5Y2NX0R12345',{smart:'ok',state:'failed',error:'queue overflow: target lost bytes',bytes_written:4,bytes_total:100})]},
    {...roomIdle.machines[2]}]};
  const fixtures={
    '/groups':[
      {id:'grp_BUILD',label:'מחשבי בנייה',role:'build',sort:1,machines:2},
      {id:'grp_CLONERS',label:'מחשבי שיכפול',role:'cloner',sort:2,machines:3},
      {id:'grp_LAB303',label:'כיתה 303 — סייבר',role:'classroom',sort:3,machines:1}],
    '/machines':[
      {mac:B1,suffix:'בנייה 1',group_id:'grp_BUILD',note:null,drawer_count:null,prompt:'menu',disks:[{dev:'sda',size_bytes:GB256,model:'870 EVO',serial:'S5YB1',port:1,smart:'ok'}],disks_reported_at:minutesAgo(180),
        inventory:{dmi:{sys_vendor:'LENOVO',product_name:'10SQS0AK00',product_version:'ThinkCentre M720q',board_name:'3132'},pci:[],tpm:{present:true,version:'2.0'}},inventory_seen_at:minutesAgo(180)},
      {mac:B2,suffix:'בנייה 2',group_id:'grp_BUILD',note:null,drawer_count:null,prompt:null,disks:null,disks_reported_at:null,inventory:null,inventory_seen_at:null},
      {mac:C1,suffix:'מחשב 1',group_id:'grp_CLONERS',note:null,drawer_count:2,prompt:null,disks:[],disks_reported_at:minutesAgo(9),inventory:null,inventory_seen_at:null},
      {mac:C2,suffix:'מחשב 2',group_id:'grp_CLONERS',note:null,drawer_count:3,prompt:null,disks_reported_at:minutesAgo(9),inventory:null,inventory_seen_at:null,
        disks:[{dev:'sda',size_bytes:GB256,model:'870 EVO',serial:'S5Y30',port:1,smart:'ok'},{dev:'sdb',size_bytes:GB256,model:'870 EVO',serial:'S5Y31',port:2},{dev:'sdc',size_bytes:GB256,model:'870 EVO',serial:'S5Y2NX0R12345',port:3,smart:'ok'}]},
      {mac:C3,suffix:'מחשב 3',group_id:'grp_CLONERS',note:null,drawer_count:null,prompt:null,disks:null,disks_reported_at:null,inventory:null,inventory_seen_at:null},
      {mac:'b4:2e:99:07:1a:c1',suffix:'01',group_id:'grp_LAB303',note:null,drawer_count:null,prompt:null,disks:null,disks_reported_at:null,inventory:null,inventory_seen_at:null}],
    '/net':[
      {mac:B1,ip:'10.44.12.20',description:'',first_seen:'2026-09-01T08:00:00Z',last_seen:minutesAgo(180),registered:true,name:'בנייה 1',group_id:'grp_BUILD',group_label:'מחשבי בנייה',role:'build',boot:null},
      {mac:B2,ip:'10.44.12.21',description:'',first_seen:'2026-09-01T08:00:00Z',last_seen:minutesAgo(4),registered:true,name:'בנייה 2',group_id:'grp_BUILD',group_label:'מחשבי בנייה',role:'build',boot:null},
      {mac:C1,ip:'10.44.12.118',description:'',first_seen:'2026-09-01T08:00:00Z',last_seen:minutesAgo(9),registered:true,name:'מחשב 1',group_id:'grp_CLONERS',group_label:'מחשבי שיכפול',role:'cloner',boot:null},
      {mac:C2,ip:'10.44.12.59',description:'',first_seen:'2026-09-01T08:00:00Z',last_seen:minutesAgo(9),registered:true,name:'מחשב 2',group_id:'grp_CLONERS',group_label:'מחשבי שיכפול',role:'cloner',boot:null}],
    '/disk-failures':[{id:7,session_id:'r15',mac:C2,dev:'sdc',serial:'S5Y2NX0R12345',port:3,disk_number:3,ata_port:'ata3',at:'2026-09-15T14:22:00Z',cause:'cable',error:'ATA bus error: SError: CommWake',ata_log:['ata3.00: exception Emask','ata3: hard resetting link','ata3.00: failed command: WRITE FPDMA QUEUED']}],
    '/shrink-records':[],
    '/monitor/machines':[
      {mac:B1,name:'בנייה 1',role:'build',ip:'10.44.12.20',online:false,prompt:'menu'},
      {mac:B2,name:'בנייה 2',role:'build',ip:'10.44.12.21',online:true,prompt:null},
      {mac:C1,name:'מחשב 1',role:'cloner',ip:'10.44.12.118',online:true,prompt:null},
      {mac:C2,name:'מחשב 2',role:'cloner',ip:'10.44.12.59',online:true,prompt:null},
      {mac:C3,name:'מחשב 3',role:'cloner',ip:null,online:false,prompt:null}],
    '/room':roundOpen?roomLive:roomIdle,
    '/tasks':[
      {id:'tsk_a1',mac:B1,machine:'בנייה 1',group_label:'מחשבי בנייה',type:'capture',disk:'sda',image_id:'img_a1',name:'Office 2024 v3',state:'pending',error:null,bytes_written:0,bytes_total:null,source_progress:null,created_at:minutesAgo(3),updated_at:minutesAgo(3)},
      {id:'tsk_b2',mac:B2,machine:'בנייה 2',group_label:'מחשבי בנייה',type:'capture',disk:'sda',image_id:'img_b2',name:'Kali 2026.2 — סייבר',state:'running',error:null,bytes_written:20000000000,bytes_total:null,source_progress:{partition:3,blocks_read:40,blocks_total:100},created_at:minutesAgo(10),updated_at:minutesAgo(2)},
      {id:'tsk_c3',mac:B1,machine:'בנייה 1',group_label:'מחשבי בנייה',type:'capture',disk:'sda',image_id:'img_c3',name:'Office 2024 — מרצה',state:'done',error:'המקור לא הוחזר לגודלו — להרחיב ליד המחשב',bytes_written:54000000000,bytes_total:54000000000,source_progress:null,created_at:'2026-09-10T11:00:00Z',updated_at:'2026-09-10T11:08:15Z'},
      {id:'tsk_d4',mac:B1,machine:'בנייה 1',group_label:'מחשבי בנייה',type:'capture',disk:'sda',image_id:'img_d4',name:'CAD Heavy',state:'failed',error:'ntfsresize נכשל — chkdsk',bytes_written:0,bytes_total:null,source_progress:null,created_at:'2026-08-19T14:02:00Z',updated_at:'2026-08-19T14:02:12Z'},
      {id:'tsk_e5',mac:'b4:2e:99:07:1a:c1',machine:'01',group_label:'כיתה 303 — סייבר',type:'capture',disk:'sda',image_id:'img_e5',name:'מכיתה',state:'done',error:null,bytes_written:1,bytes_total:1,source_progress:null,created_at:'2026-08-01T10:00:00Z',updated_at:'2026-08-01T10:30:00Z'}],
    '/overview':{images:2,machines:6,storage:null,pulls:[],room:null,session:null},
    '/journal':[],
    '/room/wake':{sent:3,failed:0,reasons:[]},
  };
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(...a){opened.push(a);}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options={})=>{
      const key=url.replace('/api/console','').split('?')[0]; requests.push({url,options});
      if(fixtures[key]===403) return {status:403,ok:false,json:async()=>({detail:'admin only'})};
      return {status:200,ok:true,headers:{get:()=>null},json:async()=>fixtures[key] ?? {ok:true}};
    }});
  for(const f of ['progress.js','console.js','net.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={username:"admin",role:"admin",server_name:"srv",version:"v0.32.1",capabilities:{}}; current="machines"; sheet = o => { globalThis.formOptions=o; }; confirmSheet=(t,s,l,fn)=>{ globalThis.confirmFn=fn; }; selectPageById=(id)=>{ globalThis.selected=id; current=id; }; toast=(m)=>{ globalThis.toasted=m; }; renderCurrent=()=>{ globalThis.rendered=(globalThis.rendered||0)+1; };');
  for(const [name,key] of [['OVERVIEW','/overview'],['MACHINES','/machines'],['GROUPS','/groups'],['DISK_FAILURES','/disk-failures'],['SHRINK_RECORDS','/shrink-records'],['NET','/net'],['MONITOR_ROWS','/monitor/machines'],['ROOM','/room'],['CAPTURE_TASKS','/tasks']]) run(name+'='+JSON.stringify(fixtures[key]));
  run('CAPTURE_TASKS_READ=true; ROOM_KEY=JSON.stringify(ROOM);');
  return {run,node,requests,fixtures,ctx,opened};
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
const between=(html,a,b)=>html.slice(html.indexOf(a),b?html.indexOf(b,html.indexOf(a)):undefined);
const CARD=/<div class="mcard(?: off)?"/g;
const card=(html,mac)=>html.slice(html.indexOf(`data-mac="${mac}"`)).split(/<div class="mcard(?: off)?"/)[0];

test('cloners object from the tree: header counters, round pill, actions, three tabs, KPIs with meaning', () => {
  const {run}=setup();
  run("selectMachinesGroup('grp_CLONERS')"); assert.equal(run('MACHINES_CLASS'),'grp_CLONERS'); assert.equal(run('MACHINES_FILTER'),null);
  const html=run('machines(0)'); balanced(html);
  assert.match(html,/obj-name">מחשבי שיכפול</);
  assert.match(html,/קבוצה קבועה · 3 מחשבים · 5 חריצים · 2 מחוברים · 3 דיסקים בחריצים · 1 אדומים/,'slots = drawer_count (2+3); a machine with neither drawer_count nor a report adds none');
  assert.doesNotMatch(html,/class="pill[^>]*>סבב/,'no round → no round pill');
  assert.match(html,/openRoomRound\(\)">פתח סבב במשכפלים…</); assert.match(html,/wakeRoom\(\)">הער את כולם \(WoL\)</);
  assert.match(html,/openAddMachine\(\{group:'grp_CLONERS'\}\)">\+ מחשב שיכפול</);
  assert.match(html,/title="דורש API">כיבוי כולם — בקרוב</,'shutdown has no endpoint → text, not a disabled button');
  assert.doesNotMatch(html,/disabled/);
  for(const t of ['סיכום','מגירות','סבבים']) assert.match(html,new RegExp('role="tab"[^>]*>'+t+'<'));
  assert.match(html,/<div class="kpi ok"><div class="l">מחוברים<\/div><div class="v"><bdi dir="auto">2<\/bdi> <small>\/ 3<\/small><\/div><div class="s">מחשב 3 — לא מחובר/);
  assert.match(html,/<div class="kpi "><div class="l">סבב פעיל<\/div><div class="v"><bdi dir="auto">אין<\/bdi><\/div><div class="s"><a[^>]*openRoomRound\(\)">פתח סבב במשכפלים…/);
  assert.match(html,/<div class="kpi warn"><div class="l">דיסקים בחריצים<\/div><div class="v"><bdi dir="auto">3<\/bdi> <small>\/ 5<\/small><\/div><div class="s">0 כותבים · 1 אדומים · 2 ריקים/);
  assert.match(html,/<div class="kpi err"><div class="l">דיסקים אדומים<\/div><div class="v"><bdi dir="auto">1<\/bdi><\/div><div class="s">מחשב 2 · דיסק 3 · SATA 2/);
  assert.match(html,/<div class="kpi warn"><div class="l">דורש טיפול<\/div><div class="v"><bdi dir="auto">1<\/bdi><\/div><div class="s">1 אדומים/);
  assert.doesNotMatch(html,/סבב אחרון שהושלם/,'no KPI without data behind it');
  run("openClass('grp_CLONERS')"); assert.equal(run('selected'),'machines');
  run('openRoomRound()'); assert.equal(run('selected'),'deploy'); assert.match(run('toasted'),/מדף ההפצה/);
});

test('grid without a round: slots from /machines[].disks[] by drawer_count — disk N / SATA N-1, never sd*; green only on SMART ok; unchecked is grey; red from /disk-failures with clear; empty is dashed', () => {
  const {run}=setup();
  run("openClass('grp_CLONERS')"); const html=run('machines(0)');
  const grid=between(html,'<div class="mgrid">','<div class="c8 card">');
  assert.doesNotMatch(grid,/sd[abc]/,'never sd*');
  assert.match(html,/אין סבב — הדיסקים לפי הדיווח האחרון ב-hello/);
  assert.equal((grid.match(CARD)||[]).length,3);
  const c1=card(grid,C1), c2=card(grid,C2), c3=card(grid,C3);
  assert.match(c1,/<b>מחשב 1<\/b><\/span><span class="muted">מחובר · 2 חריצים · <span class="mono">10.44.12.118<\/span> · לפני 9 דק'/);
  assert.equal((c1.match(/<div class="disk empty" data-slot="\d"><b>דיסק \d<\/b><span>SATA \d<\/span><span class="cap">ריק<\/span>/g)||[]).length,2,'drawer_count=2 with 0 disks reported → two dashed empty slots');
  assert.match(c2,/<div class="disk ok" data-slot="1"><b>דיסק 1<\/b><span>SATA 0<\/span><span><bdi dir="ltr">238 GB<\/bdi> · 870 EVO<\/span><span class="cap mono">S5Y30<\/span><span class="cap">SMART תקין<\/span><\/div>/);
  assert.match(c2,/<div class="disk " data-slot="2"><b>דיסק 2<\/b><span>SATA 1<\/span>[^]*?<span class="cap">SMART לא נבדק<\/span><\/div>/,'unchecked is grey, not green');
  assert.match(c2,/<div class="disk err" data-slot="3"><b>דיסק 3<\/b><span>SATA 2<\/span>[^]*?<span class="cap">אדום 15\/09\/2026 · כבל\/חריץ SATA 2<\/span><button class="btn sm" onclick="clearDiskFailure\(7\)">נקה<\/button><\/div>/);
  assert.doesNotMatch(c2,/data-slot="3"[^]*?SMART תקין[^]*?data-slot="3"/,'a red slot never shows its SMART as green');
  assert.doesNotMatch(c2,/monitorMachine\(/,'monitor only on the monitor page'); assert.match(c2,/openMachineDetail\(/); assert.match(c2,new RegExp("wakeMachine\\('"+encodeURIComponent(C2)+"'\\)\"[^>]*>WoL<"),'#984: per-machine WoL on the cloner card');
  assert.match(c3,/מספר החריצים לא הוגדר והמכונה מעולם לא דיווחה על דיסקים/); assert.match(c3,/editDrawerCount\(/);
  assert.match(c3,/<span class="st "><b>מחשב 3<\/b>/,'not connected → grey dot');
  const red=between(html,'<div class="c8 card">','<div class="c4 card">');
  assert.match(red,/דיסקים אדומים — נכשלו בכתיבה/); assert.match(red,/<td><span class="mono">S5Y2NX0R12345<\/span><\/td><td>מחשב 2 · דיסק 3 · SATA 2<\/td><td>2026-09-15 14:22:00<\/td><td><span class="st err">כבל\/חריץ SATA 2<\/span><\/td><td>ATA bus error: SError: CommWake<\/td><td><details><summary>3 שורות קרנל/);
  assert.match(red,/clearDiskFailure\(7\)">נקה</);
  const rounds=between(html,'<div class="c4 card">');
  assert.match(rounds,/סבבים בחדר/); assert.match(rounds,/היסטוריית סבבים בחדר — <b[^>]*>דורש API<\/b>/); assert.doesNotMatch(rounds,/class="ev"/);
});

test('grid during a round: slots from /room.machines[].drawer_list — write bar per drawer, CRC warning, failed drawer red, machine "in round"; pill and KPIs from /room.round', () => {
  const {run}=setup({roundOpen:true});
  run("openClass('grp_CLONERS')"); const html=run('machines(0)'); balanced(html);
  assert.match(html,/pill info">סבב פעיל — גל 2, 2\/5</);
  assert.match(html,/<div class="kpi info"><div class="l">סבב פעיל<\/div><div class="v"><bdi dir="auto">גל 2<\/bdi><\/div><div class="s">office365 · 2 נכתבו · 3 נשארו · <a[^>]*selectPageById\('deploy'\)">לסבב/);
  assert.match(html,/דיסקים בחריצים<\/div><div class="v"><bdi dir="auto">3<\/bdi> <small>\/ 5<\/small><\/div><div class="s">2 כותבים · 1 אדומים · 2 ריקים/);
  assert.match(html,/דורש טיפול<\/div><div class="v"><bdi dir="auto">2<\/bdi><\/div><div class="s">1 אדומים · 1 נכשלו בסבב/);
  const grid=between(html,'<div class="mgrid">','<div class="c8 card">');
  assert.match(html,/בסבב — מצב כל מגירה מ-\/room/);
  assert.doesNotMatch(grid,/sd[abc]/,'never sd*');
  const c2=card(grid,C2);
  assert.match(c2,/<span class="st run"><b>מחשב 2<\/b><\/span><span class="muted">בסבב · 38%/);
  assert.match(c2,/<div class="disk run" data-slot="1">[^]*?<div class="bar-row"><div class="bar "><i style="--w:38%"><\/i><\/div><span class="pct">38%<\/span><\/div><span class="cap">כותב · SMART תקין<\/span>/);
  assert.match(c2,/<div class="disk warn" data-slot="2">[^]*?<div class="bar warn">[^]*?<span class="cap">כותב · CRC \+3 · לבדוק כבל · ללא תזוזה 90 ש' · SMART לא נבדק<\/span>/,'CRC delta and stall are warnings, and never colour SMART green');
  assert.match(c2,/<div class="disk err" data-slot="3">[^]*?<div class="bar err">[^]*?<span class="cap">אדום 15\/09\/2026 · כבל\/חריץ SATA 2<\/span><button[^>]*clearDiskFailure\(7\)/,'red from the failure memory wins over the live state');
  const c1=card(grid,C1);
  assert.match(c1,/<b>מחשב 1<\/b><\/span><span class="muted">מחובר/,'not joined → the ordinary state');
  const rounds=between(html,'<div class="c4 card">');
  assert.match(rounds,/<span class="t">עכשיו<\/span><span class="d info"><\/span><span>office365 · גל 2 · 2\/5 נכתבו · <a[^>]*>לסבב<\/a><\/span><span class="who">nadav<\/span>/);
  run('ROOM.stream_stalled=true'); const stalled=run('machines(0)');
  assert.match(stalled,/pill warn">סבב פעיל — גל 2, 2\/5 · הזרם עצר</); assert.match(stalled,/<div class="kpi warn"><div class="l">סבב פעיל/);
});

test('cloners tabs: "drawers" is the grid alone and larger (4:3 screen next to the machines); "rounds" is the active round + API note; ROOM unread ≠ no round', () => {
  const {run}=setup({roundOpen:true});
  run("openClass('grp_CLONERS')");
  let html=run('machines(1)'); balanced(html);
  assert.match(html,/<div class="mgrid big">/); assert.doesNotMatch(html,/class="kpi/); assert.doesNotMatch(html,/<table class="dg"/); assert.equal((html.match(/class="c12 card"/g)||[]).length,1);
  html=run('machines(2)'); assert.match(html,/class="c12 card"><div class="card-h"><span>סבבים בחדר/); assert.match(html,/office365 · גל 2/); assert.match(html,/דורש API/); assert.doesNotMatch(html,/mgrid/);
  run('ROOM=null'); html=run('machines(0)');
  assert.match(html,/pill ">החדר לא נקרא</); assert.match(html,/<div class="kpi "><div class="l">סבב פעיל<\/div><div class="v"><bdi dir="auto">לא נקרא/);
  assert.match(html,/החדר לא נקרא — הדיסקים לפי הדיווח האחרון ב-hello/); assert.match(html,/החדר לא נקרא — הסבב הפעיל לא ידוע/);
  assert.match(card(between(html,'<div class="mgrid">','<div class="c8 card">'),C2),/<div class="disk ok" data-slot="1">/,'unread room falls back to the hello report, not to "nothing"');
});

test('cloners: empty group, no failures read, and the deploy role', () => {
  const {run}=setup();
  run("openClass('grp_CLONERS'); DISK_FAILURES=null; MONITOR_ROWS=null");
  let html=run('machines(0)');
  assert.match(html,/מחוברים: לא נקרא · 3 דיסקים בחריצים · אדומים: לא נקרא/);
  assert.match(html,/<div class="kpi "><div class="l">מחוברים<\/div><div class="v"><bdi dir="auto">לא נקרא/); assert.match(html,/<div class="kpi "><div class="l">דיסקים אדומים<\/div><div class="v"><bdi dir="auto">לא נקרא/);
  run("MACHINES=MACHINES.filter(m=>m.group_id!=='grp_CLONERS'); DISK_FAILURES=[]"); html=run('machines(0)'); balanced(html);
  assert.match(html,/0 מחשבים · 0 חריצים/); assert.match(html,/אין מחשבי שיכפול רשומים — הוסיפו את הראשון/); assert.match(html,/openAddMachine\(\{group:'grp_CLONERS'\}\)">\+ מחשב שיכפול</);
  assert.match(html,/דיסקים אדומים<\/div><div class="v"><bdi dir="auto">לא דווחו/,'no disks reported ≠ "none red"');
  run('ME.role="deploy"'); assert.equal(run("pageAllowed('machines')"),false,'deploy never reaches the machines page');
  html=run('machines(0)'); assert.doesNotMatch(html,/openRoomRound|wakeRoom|openAddMachine|clearDiskFailure|monitorMachine/,'no admin actions rendered for deploy even if the page were reached');
});

test('builders object: header, capture pill, actions (capture, direct #715 → deploy page, add, WoL as API note), two tabs', () => {
  const {run}=setup();
  run("selectMachinesGroup('grp_BUILD')"); assert.equal(run('MACHINES_CLASS'),'grp_BUILD');
  const html=run('machines(0)'); balanced(html);
  assert.match(html,/obj-name">מחשבי בנייה</);
  assert.match(html,/קבוצה קבועה · 2 מחשבים · 1 מחוברים · קליטה אחת ממתינה · קליטה אחת רצה · אימג' אחרון שנקלט: Office 2024 — מרצה \(10\/09\/2026\)/);
  assert.match(html,/pill info">2 קליטות בתהליך</);
  assert.match(html,/openCapture\(\)\.catch\(e => toast\(e\.message\)\)">\+ קליטת אימג'…</); assert.match(html,/openDirectRound\(\)">הפצה ישירה למשכפלים…</);
  assert.match(html,/openAddMachine\(\{group:'grp_BUILD'\}\)">\+ מחשב בנייה</); assert.match(html,/wakeGroup\('grp_BUILD'\)">הער את כולם \(WoL\)</,'#984: POST /groups/grp_BUILD/wake');
  for(const t of ['סיכום','קליטות']) assert.match(html,new RegExp('role="tab"[^>]*>'+t+'<'));
  assert.doesNotMatch(html,/role="tab"[^>]*>מחשבים</); assert.doesNotMatch(html,/disabled/);
  run('openDirectRound()'); assert.equal(run('selected'),'deploy'); assert.match(run('toasted'),/#715/);
});

test('builders: "capture in progress" lists both active captures with progress, duration, stages as an API note, and cancel behind the typed image name', () => {
  const {run,requests}=setup();
  run("openClass('grp_BUILD')"); const html=run('machines(0)');
  const now=between(html,'2 קליטות בתהליך','<div class="c12 card"><div class="card-h"><span>המחשבים');
  assert.equal((now.match(/<div class="cap-now"/g)||[]).length,2);
  const a=between(now,'data-task="tsk_a1"','data-task="tsk_b2"'), b=between(now,'data-task="tsk_b2"');
  assert.match(a,/<b>Office 2024 v3<\/b><span class="st warn">ממתין שבנייה 1 יעלה ב-PXE<\/span>/);
  assert.match(a,/<span class="k">מחשב · דיסק<\/span><span class="v">בנייה 1 · דיסק 1<\/span>/,'device → "disk N" via the machine report');
  assert.match(a,/<span class="k">התקדמות<\/span><span class="v"><span class="muted">טרם החלה/); assert.match(a,/<span class="k">משך<\/span><span class="v"><span class="muted">—/);
  assert.match(b,/<b>Kali 2026.2 — סייבר<\/b><span class="st run">נקלט מבנייה 2 · 40% · מחיצה 3 \(בלוקים\)<\/span>/);
  assert.match(b,/<span class="k">מחשב · דיסק<\/span><span class="v">בנייה 2 · <span class="mono">sda<\/span><\/span>/,'machine never reported disks → the chosen device as-is, not an invented slot');
  assert.match(b,/role="progressbar"[^>]*aria-valuenow="40"/); assert.match(b,/<span class="k">נקראו<\/span><span class="v"><bdi dir="ltr">18.6 GB<\/bdi>/); assert.match(b,/<span class="k">משך<\/span><span class="v">8:00<\/span>/);
  assert.match(b,/בדיקת NTFS → כיווץ → זרם \+ sha256 → החזרת הגודל → אימות — <b[^>]*>דורש API<\/b>/);
  assert.match(b,/cancelCaptureVerified\('tsk_b2'\)">בטל קליטה \(הקלדת שם\)</); assert.match(b,/wakeMachine\('[^']+'\)">WoL ל-בנייה 2</,'#984');
  run("cancelCaptureVerified('tsk_b2')"); assert.equal(run('formOptions.verify.mustEqual'),'Kali 2026.2 — סייבר'); assert.equal(run('formOptions.danger'),true);
  return run('formOptions.onSubmit({})').then(()=>{
    assert.ok(requests.some(r=>r.url.endsWith('/tasks/tsk_b2/cancel')&&r.options.method==='POST'),'cancel is POST /tasks/{id}/cancel');
    assert.ok(requests.some(r=>r.url.endsWith('/tasks')),'and the list is re-read');
  });
});

test('builders: machine cards from /machines + /monitor + /net (model/TPM, disk 1, prompt, capture badge), "capture from here" preselects the machine; empty and unread states', () => {
  const {run}=setup();
  run("openClass('grp_BUILD')"); let html=run('machines(0)');
  const cards=between(html,'<div class="card-h"><span>המחשבים','קליטות אחרונות');
  assert.equal((cards.match(CARD)||[]).length,2);
  const b1=card(cards,B1), b2=card(cards,B2);
  assert.match(b1,/<span class="st warn"><b>בנייה 1<\/b><\/span><span class="muted"><span class="mono">10.44.12.20<\/span> · לפני 3 שע'/);
  assert.match(b1,/<span class="k">דגם<\/span><span class="v">LENOVO ThinkCentre M720q · TPM 2.0<\/span>/);
  assert.match(b1,/<span class="k">דיסק 1<\/span><span class="v"><bdi dir="ltr">238 GB<\/bdi> · 870 EVO · <span class="mono">S5YB1<\/span><\/span>/);
  assert.match(b1,/<span class="k">מצב<\/span><span class="v"><span class="st warn">ממתין למפעיל: תפריט<\/span> <span class="pill warn">קליטה ממתינה<\/span>/);
  assert.match(b1,/לפני קליטה<\/span><span class="v"><span class="muted">NTFS \/ בשימוש — נבדקים בקליטה \(<b[^>]*>דורש API<\/b>\)/);
  assert.doesNotMatch(b1,/קלוט מכאן/,'a machine with an open capture gets no second one (server would 409)');
  assert.doesNotMatch(b1,/monitorMachine\(/,'monitor only on the monitor page'); assert.match(b1,/openMachineDetail\(/); assert.match(b1,/wakeMachine\('[^']+'\)">WoL</,'#984: per-machine WoL on the builder card');
  assert.match(b2,/<span class="st ok"><b>בנייה 2<\/b>/); assert.match(b2,/<span class="k">דגם<\/span><span class="v"><span class="muted">לא דיווח/); assert.match(b2,/<span class="k">דיסק<\/span><span class="v"><span class="muted">לא דיווח/);
  assert.match(b2,/<span class="pill info">קליטה רצה<\/span>/);
  run("CAPTURE_TASKS=[]"); html=run('machines(0)'); balanced(html);
  assert.match(html,/אין קליטה בתהליך — &quot;\+ קליטת אימג'…&quot; פותחת אחת/); assert.match(html,/אין קליטות עדיין/); assert.doesNotMatch(html,/class="pill/);
  assert.match(card(html,B2),/openCapture\('c8%3Ad9%3Ad2%3A0b%3Afe%3A34'\)\.catch\(e => toast\(e\.message\)\)">קלוט מכאן</);
  return run("openCapture('c8%3Ad9%3Ad2%3A0b%3Afe%3A34')").then(()=>{
    const f=run('formOptions.fields').find(x=>x.id==='mac'); assert.equal(f.value,B2,'the card preselects its machine in the capture sheet');
    run("CAPTURE_TASKS_READ=false"); const unread=run('machines(0)');
    assert.match(unread,/קליטות: לא נקרא/); assert.match(unread,/רשימת הקליטות לא נקראה/); assert.doesNotMatch(unread,/אין קליטות עדיין/,'unread ≠ empty');
    run("MACHINES=MACHINES.filter(m=>m.group_id!=='grp_BUILD')"); const empty=run('machines(0)');
    assert.match(empty,/אין מחשב בנייה רשום — הוסיפו אותו/); assert.match(empty,/openAddMachine\(\{group:'grp_BUILD'\}\)">\+ מחשב בנייה</);
  });
});

test('builders: recent captures table (group tasks only, 20 from /tasks) — state colours, bytes, duration, #927 warning, when; "captures" tab is the table alone', () => {
  const {run}=setup();
  run("openClass('grp_BUILD')"); let html=run('machines(1)'); balanced(html);
  assert.doesNotMatch(html,/cap-now|mcard/); assert.equal((html.match(/<table class="dg"/g)||[]).length,1);
  for(const col of ["אימג'",'מחשב · דיסק','מצב','נקראו','משך','אזהרה / שגיאה','מתי']) assert.match(html,new RegExp('<th>'+col+'</th>'));
  assert.match(html,/תיקייה — דורש API \(#968\)/,'/tasks has no folder → said, not invented');
  const ids=[...html.matchAll(/<tr data-task="([^"]+)"/g)].map(m=>m[1]);
  assert.deepEqual(ids,['tsk_a1','tsk_b2','tsk_c3','tsk_d4'],'the classroom capture is not a builders capture');
  const r=(id)=>between(html,`data-task="${id}"`,'</tr>');
  assert.match(r('tsk_a1'),/<span class="st run">ממתין ל-PXE<\/span><\/td><td><span class="muted">—<\/span><\/td><td><span class="muted">—<\/span><\/td><td><span class="muted">—<\/span><\/td><td>היום \d\d:\d\d<\/td>/);
  assert.match(r('tsk_b2'),/<span class="st run">נקלט · 40% · מחיצה 3 \(בלוקים\)<\/span><\/td><td><bdi dir="ltr">18.6 GB<\/bdi><\/td><td>8:00<\/td>/);
  assert.match(r('tsk_c3'),/<span class="st warn">הושלם — אזהרה<\/span><\/td><td><bdi dir="ltr">50.3 GB<\/bdi><\/td><td>8:15<\/td><td><span class="st warn">המקור לא הוחזר לגודלו — להרחיב ליד המחשב<\/span><\/td><td>10\/09\/2026 11:00<\/td>/);
  assert.match(r('tsk_d4'),/<span class="st err">נכשל<\/span><\/td><td><bdi dir="ltr">0 B<\/bdi><\/td><td>0:12<\/td><td>ntfsresize נכשל — chkdsk<\/td><td>19\/08\/2026 14:02<\/td>/);
  assert.doesNotMatch(r('tsk_c3'),/st ok/,'done with a warning is never green');
});

test('data: loadMachines reads /room and /tasks; the 2s poll re-reads /room only for the open cloners object and re-renders only on change; task changes re-render the open build object', async () => {
  const {run,requests,fixtures}=setup();
  run('CAPTURE_TASKS_READ=false; ROOM=null; populateSidebarGroups=()=>{}; updateAlertBadge=()=>{}; renderActivity=()=>{};');
  await run('loadMachines()');
  assert.ok(requests.some(r=>r.url==='/api/console/room')); assert.ok(requests.some(r=>r.url==='/api/console/tasks'));
  assert.equal(run('ROOM.machines.length'),3); assert.equal(run('CAPTURE_TASKS_READ'),true);
  run("MACHINES_CLASS='grp_BUILD'; current='machines'; rendered=0"); requests.length=0;
  await run('refreshGroupLive()'); assert.equal(requests.length,0,'build object: no /room poll');
  run("MACHINES_CLASS='grp_CLONERS'");
  await run('refreshGroupLive()'); assert.equal(requests.filter(r=>r.url==='/api/console/room').length,1); assert.equal(run('rendered'),0,'same answer → no re-render');
  fixtures['/room']={...fixtures['/room'],round:{id:'r2',image_id:'i',image_name:'x',target_drives:1,written_drives:0,remaining_drives:1,wave_number:1,wave_state:'open',ready_drives:0,opened_by:'n',source:null}};
  await run('refreshGroupLive()'); assert.equal(run('rendered'),1,'changed answer → re-render'); assert.equal(run('ROOM.round.id'),'r2');
  run("current='images'"); await run('refreshGroupLive()'); assert.equal(requests.filter(r=>r.url==='/api/console/room').length,2,'not on the machines page → no poll');
  run("current='machines'; MACHINES_CLASS='grp_BUILD'; rendered=0; MCH.tasksKey=''");
  await run('loadCaptures()'); assert.equal(run('rendered'),1,'new task list → build object re-rendered');
  await run('loadCaptures()'); assert.equal(run('rendered'),1,'same task list → not again');
});

test('the machines table links every group row to its object, fixed groups included', () => {
  const {run}=setup();
  run('MACHINES_CLASS=null'); const html=run('machines(0)');
  assert.match(html,/<tr class="group" data-group="grp_CLONERS">[^]*?openClass\('grp_CLONERS'\)">מחשבי שיכפול<\/a>/);
  assert.match(html,/<tr class="group" data-group="grp_BUILD">[^]*?openClass\('grp_BUILD'\)">פתח</);
});
