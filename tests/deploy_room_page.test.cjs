// ‏#954 גל 4: סבב הפצה — חדר המשכפלים (#695), לפי docs/design/console-redesign/deploy.md
// §"המבנה — חדר המשכפלים". בלי דפדפן: console.js + net.js + progress.js ב-vm מול DOM מזערי
// ו-fetch מזויף. עיקרון 5: "לא נבדק" ≠ ירוק; ROOM null (לא נקרא) ≠ חדר בלי סבב.
// הכרעת נדב 16/09: כתום = בלי תשובה ממשיך לכתוב, אדום = בלי תשובה מדלג — לעולם לא "דלג בשניהם".
// עיקרון 7: עצירת סבב מאחורי הקלדת שם. "דיסק N · SATA N-1", לעולם לא sd*.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');
const minutesAgo=(n)=>new Date(Date.now()-n*60000).toISOString();
const GB256=256060514304, GB512=512110190592;

const B1='c8:d9:d2:0b:fe:32', C1='a0:48:1c:8a:18:40', C2='78:ac:c0:9b:11:c2', C3='78:ac:c0:9b:11:c3';
const drawer=(dev,port,serial,extra={})=>({dev,port,serial,model:'870 EVO',size_bytes:GB256,fresh:true,selected:false,state:null,error:null,bytes_written:0,bytes_total:0,stalled_s:null,crc_delta:null,smart:'unchecked',...extra});
const round=(extra={})=>({id:'rnd_1',image_id:'img_1',image_name:'office365',target_drives:12,written_drives:5,remaining_drives:7,wave_number:2,wave_state:'running',ready_drives:5,opened_by:'nadav',source:{kind:'library'},...extra});
const floor={mac:C2,name:'מחשב 2',port:3,dev:'sdc',size_bytes:GB256};

function roomIdle() {
  return {round:null,stream_stalled:false,disk_floor:floor,machines:[
    {mac:C1,name:'מחשב 1',drawer_count:2,awake:true,drawers:2,fresh_drawers:2,joined:false,state:null,bytes_written:0,bytes_total:0,error:null,
      drawer_list:[drawer('sda',1,'S5Y10',{smart:'ok'}),drawer('sdb',2,'S5Y11',{smart:'ok'})]},
    {mac:C2,name:'מחשב 2',drawer_count:3,awake:true,drawers:3,fresh_drawers:3,joined:false,state:null,bytes_written:0,bytes_total:0,error:null,
      drawer_list:[drawer('sda',1,'S5Y30',{smart:'ok'}),drawer('sdb',2,'S5Y31',{smart:'warn'}),drawer('sdc',3,'S5Y2NX0R12345',{smart:'ok'})]},
    {mac:C3,name:'מחשב 3',drawer_count:3,awake:false,drawers:0,fresh_drawers:0,drawer_list:[],joined:false,state:null,bytes_written:0,bytes_total:0,error:null}]};
}
/* גל רץ: מחשב 1 כותב על שתי מגירות, מחשב 2 — אחת כותבת, אחת כתומה (CRC), אחת נכשלה; מחשב 3 לא מחובר. */
function roomRunning(wave={}) {
  const r=roomIdle(); r.round=round(wave);
  r.machines[0]={...r.machines[0],joined:true,state:'running',bytes_written:38,bytes_total:100,
    drawer_list:[drawer('sda',1,'S5Y10',{smart:'ok',state:'writing',bytes_written:38,bytes_total:100,stalled_s:2}),drawer('sdb',2,'S5Y11',{smart:'ok',state:'writing',bytes_written:38,bytes_total:100,stalled_s:3})]};
  r.machines[1]={...r.machines[1],joined:true,state:'running',bytes_written:37,bytes_total:100,
    drawer_list:[drawer('sda',1,'S5Y30',{smart:'ok',state:'writing',bytes_written:37,bytes_total:100,stalled_s:2}),
                 drawer('sdb',2,'S5Y31',{smart:'warn',state:'writing',bytes_written:37,bytes_total:100,crc_delta:3,stalled_s:4}),
                 drawer('sdc',3,'S5Y2NX0R12345',{smart:'ok',state:'failed',error:'queue overflow: target lost bytes',bytes_written:4,bytes_total:100})]};
  return r;
}

function setup({room=roomIdle(),role='admin',session=null,prompts={}}={}) {
  const nodes=new Map(), requests=[], opened=[];
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},style:{removeProperty(){}},
      classList:{add(){},remove(){},toggle(){}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.innerHTML+=html;},setAttribute(){},removeAttribute(){},focus(){}});
    return nodes.get(key);
  }
  const fixtures={
    '/groups':[{id:'grp_BUILD',label:'מחשבי בנייה',role:'build',sort:1,machines:1},{id:'grp_CLONERS',label:'מחשבי שיכפול',role:'cloner',sort:2,machines:3},{id:'grp_LAB303',label:'כיתה 303 — סייבר',role:'classroom',sort:3,machines:2}],
    '/machines':[
      {mac:B1,suffix:'בנייה 1',group_id:'grp_BUILD',note:null,drawer_count:null,prompt:null,disks:[{dev:'sda',size_bytes:GB512,model:'870 EVO',serial:'S5YB1',port:1,smart:'ok'}],disks_reported_at:minutesAgo(3),inventory:null,inventory_seen_at:null},
      {mac:C1,suffix:'מחשב 1',group_id:'grp_CLONERS',note:null,drawer_count:2,prompt:prompts[C1]||null,disks:[{dev:'sda',size_bytes:GB256,model:'870 EVO',serial:'S5Y10',port:1,smart:'ok'},{dev:'sdb',size_bytes:GB256,model:'870 EVO',serial:'S5Y11',port:2,smart:'ok'}],disks_reported_at:minutesAgo(1),inventory:null,inventory_seen_at:null},
      {mac:C2,suffix:'מחשב 2',group_id:'grp_CLONERS',note:null,drawer_count:3,prompt:prompts[C2]||null,disks:[{dev:'sda',size_bytes:GB256,model:'870 EVO',serial:'S5Y30',port:1,smart:'ok'},{dev:'sdb',size_bytes:GB256,model:'870 EVO',serial:'S5Y31',port:2,smart:'warn'},{dev:'sdc',size_bytes:GB256,model:'870 EVO',serial:'S5Y2NX0R12345',port:3,smart:'ok'}],disks_reported_at:minutesAgo(1),inventory:null,inventory_seen_at:null},
      {mac:C3,suffix:'מחשב 3',group_id:'grp_CLONERS',note:null,drawer_count:3,prompt:null,disks:null,disks_reported_at:null,inventory:null,inventory_seen_at:null},
      {mac:'b4:2e:99:07:1a:c1',suffix:'01',group_id:'grp_LAB303',note:null,drawer_count:null,prompt:null,disks:null,disks_reported_at:null,inventory:null,inventory_seen_at:null},
      {mac:'b4:2e:99:07:1a:c2',suffix:'02',group_id:'grp_LAB303',note:null,drawer_count:null,prompt:null,disks:null,disks_reported_at:null,inventory:null,inventory_seen_at:null}],
    '/net':[
      {mac:C1,ip:'10.44.12.118',description:'',first_seen:'2026-09-01T08:00:00Z',last_seen:minutesAgo(1),registered:true,name:'מחשב 1',group_id:'grp_CLONERS',group_label:'מחשבי שיכפול',role:'cloner',boot:null},
      {mac:C2,ip:'10.44.12.59',description:'',first_seen:'2026-09-01T08:00:00Z',last_seen:minutesAgo(1),registered:true,name:'מחשב 2',group_id:'grp_CLONERS',group_label:'מחשבי שיכפול',role:'cloner',boot:null}],
    '/disk-failures':[],
    '/images':[
      {id:'img_1',name:'office365',description:'',folder:'',sort:1,family:'windows',os:'windows',created:'2026-09-12T10:00:00Z',source_disk_bytes:GB256,total_compressed_bytes:20e9,partitions:4,min_target_bytes:53005516800},
      {id:'img_2',name:'win512-full',description:'',folder:'מרצים',sort:2,family:'windows',os:'windows',created:'2026-09-16T10:00:00Z',source_disk_bytes:GB512,total_compressed_bytes:210e9,partitions:4,min_target_bytes:300e9},
      {id:'img_3',name:'kali',description:'',folder:'',sort:3,family:'linux',os:'linux',created:'2026-09-10T10:00:00Z',source_disk_bytes:GB256,total_compressed_bytes:9e9,partitions:2,min_target_bytes:null}],
    '/overview':{images:3,machines:6,storage:null,pulls:[],room:room.round,session},
    '/room':room,
    '/room/wake':{sent:3,failed:0,reasons:[]},
    '/room/start':{ok:true},
    '/room/close':{ok:true},
  };
  if(role==='deploy') for(const k of ['/health','/journal','/ssh','/update','/monitor/machines','/tasks']) fixtures[k]=403;
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Error,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(...a){opened.push(a);}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options={})=>{
      const key=url.replace('/api/console','').split('?')[0]; requests.push({url,options});
      const v=fixtures[key];
      if(v===403) return {status:403,ok:false,json:async()=>({detail:'admin only'})};
      if(v&&v.__status) return {status:v.__status,ok:false,json:async()=>({detail:v.detail})};
      return {status:200,ok:true,headers:{get:()=>null},json:async()=>v ?? {ok:true}};
    }});
  for(const f of ['progress.js','console.js','net.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run(`ME={username:"${role}",role:"${role}",server_name:"srv",version:"v0.32.1",capabilities:{}}; current="deploy"; sheet = o => { globalThis.formOptions=o; }; selectPageById=(id)=>{ globalThis.selected=id; current=id; }; toast=(m)=>{ globalThis.toasted=m; }; renderCurrent=()=>{ globalThis.rendered=(globalThis.rendered||0)+1; }; activateTab=(i)=>{ globalThis.activated=i; }; monitorMachine=(mac)=>{ globalThis.monitored=mac; };`);
  for(const [name,key] of [['OVERVIEW','/overview'],['MACHINES','/machines'],['GROUPS','/groups'],['DISK_FAILURES','/disk-failures'],['NET','/net'],['ROOM','/room'],['IMAGES','/images']]) run(name+'='+JSON.stringify(fixtures[key]));
  run('ROOM_KEY=JSON.stringify([ROOM,MACHINES.map(m=>[m.mac,m.prompt])]);');
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
const card=(html,mac)=>html.slice(html.indexOf(`data-mac="${mac}"`)).split(/<div class="mcard(?: off)?"/)[0];
const kpi=(html,label)=>between(html,`<div class="l">${label}</div>`,'</div><div class="s">')+between(html,`<div class="l">${label}</div>`).slice(0,600);

test('page registry: deploy is an own page with three tabs, the room first; the old renderer, drawer and "+ action" entries are gone', () => {
  const {run}=setup();
  assert.equal(run('pages.deploy.own'),true); assert.equal(run('pages.deploy.load===loadDeploy'),true);
  assert.equal(JSON.stringify(run('deployTabs()')),JSON.stringify(['חדר המשכפלים','כיתה','היסטוריה']));
  assert.equal(run('typeof emptyDataCard'),'function','still used by the network page');
  assert.equal(run('typeof startSelectedRound'),'undefined'); assert.equal(run('typeof openNewDeployment'),'undefined');
  assert.equal(run('typeof roundDrawerOpen'),'undefined','no round drawer any more');
  run('openRoundDetail()'); assert.equal(run('selected'),'deploy'); assert.equal(run('activated'),1,'"details" opens the class tab, not a drawer');
  for(const r of ['admin','deploy']) { const s=setup({role:r}); assert.equal(s.run("pageAllowed('deploy')"),true,r); }
});

test('no round: header counters, "no round" pill, WoL + big-view actions, the inline new-round card (not a sheet) and the idle grid from /room drawer_list', () => {
  const {run}=setup();
  const html=run('deploy(0)'); balanced(html);
  assert.match(html,/obj-name">חדר המשכפלים</); assert.match(html,/obj-sub">3 משכפלים · 8 חריצים · 2 מחוברים · 5 דיסקים בחריצים</);
  assert.match(html,/<span class="pill ">אין סבב</);
  assert.match(html,/wakeRoom\(\)">הער משכפלים \(WoL\)</); assert.match(html,/toggleRoomBig\(\)">תצוגה גדולה</);
  assert.doesNotMatch(html,/startWave|stopRoom/,'no wave to start or stop');
  assert.doesNotMatch(html,/class="kpi/,'no KPI row without a round');
  const form=between(html,'<div class="rnew">','המחשבים והדיסקים');
  assert.match(form,/name="rn-src" value="library" checked/); assert.match(form,/name="rn-src" value="build_disk"/,'a builder that reported disks → #715 offered');
  assert.match(form,/<select id="rn-image"/); assert.match(form,/<input type="number" id="rn-target" min="1" value="8"/,'default target = declared drawers');
  assert.match(form,/openRoomRoundSubmit\(\)"[^>]*>פתח סבב</); assert.match(form,/ברירת המחדל = 8 חריצים/);
  assert.equal(run('typeof formOptions'),'undefined','inline — no sheet was opened');
  // הגריד עד סוף הכרטיס (`UI.card`) — לא לפי ספירת </div>: כרטיס בלי פעולות נסגר מוקדם יותר
  const grid=between(html,'<div class="mgrid">','<div class="c12 rnotes">')||between(html,'<div class="mgrid">');
  assert.match(html,/אין סבב — הדיסקים לפי הדיווח האחרון ב-hello/);
  assert.doesNotMatch(grid,/sd[abc]/,'never sd*');
  const c1=card(grid,C1), c2=card(grid,C2), c3=card(grid,C3);
  assert.match(c1,/<span class="st ok"><b>מחשב 1<\/b><\/span><span class="muted">מחובר · 2 חריצים · <span class="mono">10.44.12.118<\/span>/);
  assert.match(c1,/<div class="disk ok" data-slot="1"><b>דיסק 1<\/b><span>SATA 0<\/span>/);
  assert.match(c2,/<div class="disk warn" data-slot="2"><b>דיסק 2<\/b><span>SATA 1<\/span>[^]*?SMART אזהרה/);
  assert.match(grid,/<div class="mcard off" data-mac="78:ac:c0:9b:11:c3"><div class="mcard-h"><span class="st "><b>מחשב 3<\/b><\/span><span class="muted">לא מחובר · 3 חריצים · מעולם לא</);
  assert.equal((c3.match(/<div class="disk empty" data-slot="\d"><b>דיסק \d<\/b><span>SATA \d<\/span><span class="cap">ריק<\/span>/g)||[]).length,3);
  assert.match(c3,/wakeRoom\(\)"[^>]*>WoL \(כל החדר\)</,'not connected → WoL on the card');
  assert.doesNotMatch(c1,/WoL/,'connected → no WoL');
  assert.doesNotMatch(html,/openMachineDetail/,'no machine drawer from the room');
});

test('#953: every image says "fits a disk from X GB"; one that does not fit the smallest drawer is disabled with the exact server refusal string; unknown floor → note, nothing disabled', () => {
  const {run}=setup();
  let html=run('deploy(0)');
  assert.match(html,/<option value="img_1">office365 · נכנס לדיסק מ-54 GB<\/option>/);
  assert.match(html,/<option value="img_2" disabled>מרצים \/ win512-full · נכנס לדיסק מ-300 GB — לא נכנס: דיסק 3 במחשב מחשב 2 הוא 256GB, האימג' צריך 300GB<\/option>/);
  assert.match(html,/<option value="img_3">kali · גודל נדרש לא ידוע<\/option>/,'min_target_bytes null ≠ 0');
  assert.match(html,/המגירה הקטנה ביותר בחדר: 256GB \(דיסק 3 במחשב מחשב 2\)/);
  assert.equal(run("imageFitReason(IMAGES[1],ROOM.disk_floor)"),"דיסק 3 במחשב מחשב 2 הוא 256GB, האימג' צריך 300GB");
  assert.equal(run("imageFitReason(IMAGES[0],ROOM.disk_floor)"),null); assert.equal(run("imageFitReason(IMAGES[1],null)"),null);
  assert.equal(run("imageFitReason(IMAGES[1],{name:'מחשב 2',port:null,dev:'nvme0n1',size_bytes:256e9})"),"דיסק nvme0n1 במחשב מחשב 2 הוא 256GB, האימג' צריך 300GB",'no port → device name, as the server does');
  run('delete ROOM.disk_floor'); html=run('deploy(0)');
  assert.match(html,/גודל הדיסקים לא ידוע — יסורב במכונה אם לא ייכנס/); assert.doesNotMatch(html,/<option[^>]*disabled/);
});

test('new round: POST /room {image_id, target_drives}; a 409 fit refusal is shown in the card verbatim; #715 source sends target_slots grouped by machine from the connected drawers', async () => {
  const {run,requests,fixtures}=setup();
  run("roomFormSet('image','img_1'); roomFormSet('target','6')");
  await run('openRoomRoundSubmit()');
  let r=requests.find(x=>x.url==='/api/console/room'&&x.options.method==='POST');
  assert.deepEqual(JSON.parse(r.options.body),{image_id:'img_1',target_drives:6});
  assert.match(run('toasted'),/הסבב נפתח/); assert.equal(run('DEPLOY.err'),'');
  assert.ok(requests.some(x=>x.url==='/api/console/images')&&requests.some(x=>x.url==='/api/console/overview'),'loadDeploy re-reads after opening');
  fixtures['/room']={__status:409,detail:"דיסק 3 במחשב מחשב 2 הוא 256GB, האימג' צריך 300GB"};
  run("roomFormSet('image','img_2')"); await run('openRoomRoundSubmit()');
  assert.equal(run('DEPLOY.err'),"דיסק 3 במחשב מחשב 2 הוא 256GB, האימג' צריך 300GB");
  fixtures['/room']=roomIdle(); const html=run('deploy(0)');
  assert.match(html,/<div class="note err"><span aria-hidden="true">●<\/span><span>הסבב לא נפתח: דיסק 3 במחשב מחשב 2 הוא 256GB, האימג' צריך 300GB<\/span><\/div>/);
  run("DEPLOY.err=''; roomFormSet('image','')"); await run('openRoomRoundSubmit()'); assert.equal(run('DEPLOY.err'),"בחר אימג'");
  requests.length=0; run("roomFormSet('src','build_disk'); roomFormSet('disk','sda')");
  assert.match(run('deploy(0)'),/היעד: כל המגירות המחוברות כרגע \(5\)/);
  await run('openRoomRoundSubmit()');
  r=requests.find(x=>x.url==='/api/console/room'&&x.options.method==='POST');
  assert.deepEqual(JSON.parse(r.options.body),{source:{kind:'build_disk',mac:B1,disk:'sda'},target_slots:[{mac:C1,ports:[1,2]},{mac:C2,ports:[1,2,3]}]});
  const d=setup({role:'deploy'}); d.run("roomFormSet('image','img_1')"); await d.run('openRoomRoundSubmit()');
  assert.ok(d.requests.some(x=>x.url==='/api/console/room'&&x.options.method==='POST'),'deploy is a room operator');
});

test('running wave: header "wave N", transmitting pill with the wave percent, start/stop actions, five KPIs with meaning, rate/loss as an API note', () => {
  const {run}=setup({room:roomRunning()});
  const html=run('deploy(0)'); balanced(html);
  assert.match(html,/obj-name">חדר המשכפלים — גל 2</);
  assert.match(html,/obj-sub">office365 · <bdi dir="ltr">18.6 GB<\/bdi> בשרת · יעד: 12 דיסקים · נכתבו 5 · נשארו 7 · פתח nadav</);
  assert.match(html,/<span class="pill info">משדר — 31%</,'mean of the joined drawers: (38+38+37+37+4)/5');
  assert.doesNotMatch(html,/startWave/,'running → nothing to start'); assert.match(html,/stopRoom\(\)">עצור סבב \(הקלדת שם\)</); assert.match(html,/wakeRoom\(\)">הער משכפלים \(WoL\)</);
  assert.doesNotMatch(html,/rnew/,'no new-round form while a round is active');
  assert.match(html,/<div class="kpi info"><div class="l">הגל הנוכחי<\/div><div class="v"><bdi dir="auto">גל 2<\/bdi><\/div><div class="bar" style="margin-top:6px" role="progressbar"[^>]*aria-valuenow="31"><i style="--w:31%"><\/i><\/div><div class="s">31% בממוצע על המגירות שהצטרפו · קצב ואיבוד — <b[^>]*>דורש API<\/b>/);
  assert.match(html,/<div class="kpi ok"><div class="l">נכתבו ואומתו<\/div><div class="v"><bdi dir="auto">5<\/bdi> <small>\/ 12<\/small><\/div><div class="s">7 נשארו · מכל הגלים/);
  assert.match(html,/<div class="kpi info"><div class="l">כותבים עכשיו<\/div><div class="v"><bdi dir="auto">4<\/bdi><\/div><div class="s">מחשב 1 · דיסק 1,2 — מחשב 2 · דיסק 1,2/);
  assert.match(html,/<div class="kpi err"><div class="l">דורש מפעיל<\/div><div class="v"><bdi dir="auto">1<\/bdi><\/div><div class="s">מחשב 2 · דיסק 3 אדום — מדלג/);
  assert.match(html,/<div class="kpi "><div class="l">ריקים \/ לא מחוברים<\/div><div class="v"><bdi dir="auto">1<\/bdi><\/div><div class="s">מחשב 3 לא מחובר · 0 חריצים פנויים/,'a machine that is off counts once, not per empty slot');
  const s2=setup({room:roomRunning()}); s2.run("ROOM.machines[0].drawer_list.pop()"); assert.match(s2.run('deploy(0)'),/ריקים \/ לא מחוברים<\/div><div class="v"><bdi dir="auto">2<\/bdi><\/div><div class="s">מחשב 3 לא מחובר · 1 חריצים פנויים/);
  assert.doesNotMatch(html,/Gb\/s|איבוד 0|מתוך 3 גלים/,'no invented rate, loss or wave total');
});

test('running wave: the grid is the cloners grid in round mode — write bars, blue writing, orange CRC, red failed drawer; machine "in round · N%"; the notes say what happens without an answer', () => {
  const {run}=setup({room:roomRunning()});
  const html=run('deploy(0)');
  assert.match(html,/בסבב — מצב כל מגירה מ-\/room/);
  const grid=between(html,'<div class="mgrid">','<div class="c12 rnotes">');
  assert.doesNotMatch(grid,/sd[abc]/,'never sd*');
  const c1=card(grid,C1), c2=card(grid,C2);
  assert.match(c1,/<span class="st run"><b>מחשב 1<\/b><\/span><span class="muted">בסבב · 38%/);
  assert.match(c1,/<div class="disk run" data-slot="1">[^]*?<div class="bar "><i style="--w:38%"><\/i><\/div><span class="pct">38%<\/span><\/div><span class="cap">כותב · SMART תקין<\/span>/);
  assert.match(c2,/<div class="disk warn" data-slot="2">[^]*?<div class="bar warn">[^]*?<span class="cap">כותב · CRC \+3 · לבדוק כבל · SMART אזהרה<\/span>/);
  assert.match(c2,/<div class="disk err" data-slot="3">[^]*?<div class="bar err">[^]*?<span class="cap">נכשל בסבב — queue overflow: target lost bytes/);
  assert.doesNotMatch(c2,/<div class="disk ok" data-slot="3"/,'a failed drawer is never green');
  const notes=between(html,'<div class="c12 rnotes">');
  assert.match(notes,/<div class="note warn">[^]*?<b>מחשב 2 · דיסק 2<\/b> — SMART אזהרה \/ CRC: <b>הסוכן ממשיך לכתוב<\/b> \(כתום = כותב\)[^]*?דילוג מרחוק — <b[^>]*>דורש API<\/b>/);
  assert.match(notes,/<div class="note err">[^]*?<b>מחשב 2 · דיסק 3<\/b> — אדום: <b>הסוכן מדלג<\/b> \(אדום = מדלג\)/);
  assert.doesNotMatch(notes,/דלג בשניהם|onclick="[^"]*skip/,'never "skip both", never an invented skip endpoint');
});

test('the operator question (prompt) is a note in the page: orange machine → "keeps writing", red machine → "skips"; monitor and clear links for admin, answer from console = API note', () => {
  const room=roomRunning();
  const prompts={[C1]:'Disk 2: SMART warn - write anyway? [y/n]',[C2]:'Disk 3: failed the previous clone - skip? [y/n]'};
  const {run}=setup({room,prompts});
  run(`DISK_FAILURES=[{id:7,session_id:'r15',mac:'${C2}',dev:'sdc',serial:'S5Y2NX0R12345',port:3,disk_number:3,ata_port:'ata3',at:'2026-09-15T14:22:00Z',cause:'cable',error:'ATA bus error',ata_log:[]}]; ROOM.machines[0].drawer_list[1].smart='warn';`);
  const html=run('deploy(0)'); balanced(html);
  assert.match(html,/דורש מפעיל<\/div><div class="v"><bdi dir="auto">3<\/bdi><\/div><div class="s">מחשב 1 ממתין לתשובה · מחשב 2 ממתין לתשובה · מחשב 2 · דיסק 3 אדום — מדלג/);
  const notes=between(html,'<div class="c12 rnotes">');
  const n1=between(notes,'<b>מחשב 1</b>','</div>'), n2=between(notes,'<b>מחשב 2</b>','</div>');
  assert.match(notes,/<div class="note warn"><span aria-hidden="true">●<\/span><span><b>מחשב 1<\/b> ממתין למפעיל: <span dir="ltr">Disk 2: SMART warn - write anyway\? \[y\/n\]<\/span>\. <b>כתום — בלי תשובה הסוכן ממשיך לכתוב<\/b> על מחשב 1 · דיסק 2\./);
  assert.match(n1,/selectPageById\('monitor'\)">לענות ליד המסך — דף המוניטור</,'monitor only on the monitor page (Nadav 17/09)'); assert.doesNotMatch(n1,/monitorMachine\(/); assert.match(n1,/תשובה מהקונסולה — <b[^>]*>דורש API<\/b>/); assert.match(n1,/שיכפול תמיד עם מישהו ליד המחשבים/);
  assert.doesNotMatch(n1,/מדלג/,'orange never says "skips"');
  assert.match(notes,/<div class="note err"><span aria-hidden="true">●<\/span><span><b>מחשב 2<\/b> ממתין למפעיל: <span dir="ltr">Disk 3: failed the previous clone - skip\? \[y\/n\]<\/span>\. <b>אדום — בלי תשובה הסוכן מדלג<\/b> על מחשב 2 · דיסק 3\./);
  assert.match(n2,/clearDiskFailure\(7\)">נקה אדום — מחשב 2 · דיסק 3</);
  assert.equal((notes.match(/<div class="note (?:warn|err)">/g)||[]).length,2,'a machine with a question gets one note; its drawers are not repeated in the aggregate notes');
  assert.match(card(html,C1),/<span class="st run"><b>מחשב 1<\/b>/,'in the round the card keeps the round state; the question is in the note');
  const d=setup({room:roomRunning(),role:'deploy',prompts}); const dh=d.run('deploy(0)');
  assert.doesNotMatch(dh,/monitorMachine|clearDiskFailure/,'deploy: no admin links'); assert.match(dh,/ממתין למפעיל/);
});

test('wave states: open → "waiting to join" pill and "start wave (N ready)"; verifying counts as writing; stalled stream → orange everywhere; closed wave / target done', () => {
  let s=setup({room:roomRunning({wave_state:'open',ready_drives:4,wave_number:1,written_drives:0,remaining_drives:12})});
  s.run("ROOM.machines.forEach(m=>{m.joined=false;m.state=null;(m.drawer_list||[]).forEach(d=>{d.state=null;d.bytes_written=0;d.bytes_total=0;d.crc_delta=null;})})");
  let html=s.run('deploy(0)'); balanced(html);
  assert.match(html,/obj-name">חדר המשכפלים — גל 1</); assert.match(html,/<span class="pill info">ממתין להצטרפות · 4 מוכנים</);
  assert.match(html,/startWave\(\)">התחל גל \(4 מוכנים\)</);
  assert.match(html,/הגל הנוכחי<\/div><div class="v"><bdi dir="auto">גל 1<\/bdi><\/div><div class="s">4 מגירות מוכנות · הגל טרם התחיל/,'no bar without a report — not 0%');
  assert.match(html,/<div class="kpi "><div class="l">נכתבו ואומתו<\/div><div class="v"><bdi dir="auto">0<\/bdi> <small>\/ 12<\/small>/);
  assert.match(html,/כותבים עכשיו<\/div><div class="v"><bdi dir="auto">0<\/bdi><\/div><div class="s">הגל טרם התחיל/);
  s=setup({room:roomRunning()}); s.run("ROOM.machines[0].drawer_list[0].state='verifying'");
  html=s.run('deploy(0)'); assert.match(html,/כותבים עכשיו<\/div><div class="v"><bdi dir="auto">4</); assert.match(card(html,C1),/<div class="disk run" data-slot="1">[^]*?מאמת · SMART תקין/);
  s.run('ROOM.stream_stalled=true'); html=s.run('deploy(0)');
  assert.match(html,/<span class="pill warn">הזרם עצר</); assert.match(html,/<div class="kpi warn"><div class="l">הגל הנוכחי[^]*?<b>הזרם עצר<\/b>/); assert.match(html,/דורש מפעיל<\/div><div class="v"><bdi dir="auto">2<\/bdi><\/div><div class="s">מחשב 2 · דיסק 3 אדום — מדלג · הזרם עצר/);
  s=setup({room:roomRunning({wave_state:'closed',written_drives:12,remaining_drives:0})});
  s.run("ROOM.machines.forEach(m=>(m.drawer_list||[]).forEach(d=>{if(d.state==='writing'){d.state='done';d.bytes_written=100;}}))");
  html=s.run('deploy(0)'); assert.match(html,/<span class="pill ">הגל נסגר</); assert.match(html,/נכתבו ואומתו<\/div><div class="v"><bdi dir="auto">12<\/bdi> <small>\/ 12<\/small><\/div><div class="s">היעד לסבב הושלם/);
  assert.match(card(html,C1),/<div class="disk ok" data-slot="1">[^]*?<div class="bar ok"><i style="--w:100%">[^]*?נכתב · SMART תקין/);
  assert.match(html,/<span class="pill ">הגל נסגר<\/span>/); assert.doesNotMatch(html,/startWave/);
});

test('start wave posts /room/start and a 409 is a toast with the reason; stop is behind the typed image name and posts /room/close with confirm_name; WoL posts /room/wake', async () => {
  const {run,requests,fixtures}=setup({room:roomRunning({wave_state:'open'})});
  await run('startWave()'); assert.ok(requests.some(r=>r.url==='/api/console/room/start'&&r.options.method==='POST')); assert.match(run('toasted'),/הגל התחיל/);
  fixtures['/room/start']={__status:409,detail:'אף מכונת שיכפול עוד לא הצטרפה לגל'};
  await run('startWave()'); assert.match(run('toasted'),/הגל לא התחיל: אף מכונת שיכפול עוד לא הצטרפה לגל/);
  run('stopRoom()');
  assert.equal(run('formOptions.danger'),true); assert.equal(JSON.stringify(run('formOptions.verify')),JSON.stringify({label:"הקלד את שם האימג'",mustEqual:'office365'})); assert.equal(run('formOptions.submitLabel'),'עצור סבב');
  assert.ok(!requests.some(r=>r.url==='/api/console/room/close'),'nothing posted before the sheet is confirmed');
  await run('formOptions.onSubmit({})');
  const close=requests.find(r=>r.url==='/api/console/room/close'); assert.deepEqual(JSON.parse(close.options.body),{confirm_name:'office365'});
  await run('wakeRoom()'); assert.ok(requests.some(r=>r.url==='/api/console/room/wake')); assert.match(run('toasted'),/WoL נשלח ל-3 מחשבים/);
  run("ROOM.round.source={kind:'build_disk',mac:'"+B1+"',disk:'sda',name:'בנייה 1',task_state:'running',manifest_ready:true}; ROOM.round.image_name='בנייה 1:sda'");
  assert.match(run('deploy(0)'),/obj-sub">מקור: מחשב הבנייה \(בנייה 1:sda\)/); run('stopRoom()'); assert.equal(run('formOptions.verify.mustEqual'),'בנייה 1:sda','#715: the typed name is what GET returns');
  run('ME.role="deploy"'); run('formOptions=undefined; stopRoom()'); assert.equal(run('formOptions.danger'),true,'deploy is a room operator');
  run('ME=null'); run('formOptions=undefined; stopRoom()'); assert.equal(run('typeof formOptions'),'undefined');
});

test('big view: the grid alone, larger (4:3 screen next to the machines); ROOM unread ≠ no round; empty room', () => {
  const {run}=setup({room:roomRunning()});
  run('toggleRoomBig()'); let html=run('deploy(0)'); balanced(html);
  assert.match(html,/<div class="mgrid big">/); assert.doesNotMatch(html,/class="kpi|rnotes|rnew/); assert.match(html,/toggleRoomBig\(\)">חזרה לתצוגה המלאה</);
  run('toggleRoomBig()'); html=run('deploy(0)'); assert.match(html,/<div class="mgrid">/); assert.match(html,/class="kpi/);
  run("ROOM=null; DEPLOY.roomErr='שגיאה 500'"); html=run('deploy(0)'); balanced(html);
  assert.match(html,/<span class="pill ">החדר לא נקרא</); assert.match(html,/החדר לא נקרא: שגיאה 500 — הגריד והסבב לא ידועים/); assert.doesNotMatch(html,/mgrid|rnew|אין סבב/);
  run("ROOM={round:null,stream_stalled:false,disk_floor:null,machines:[]}"); html=run('deploy(0)'); balanced(html);
  assert.match(html,/0 משכפלים · 0 חריצים · 0 מחוברים/); assert.match(html,/אין מחשבי שיכפול רשומים — הוסיפו אותם במחשבים/); assert.match(html,/openClass\('grp_CLONERS'\)">למחשבי השיכפול</);
});

test('class tab: the existing session code as-is inside cards (row, start/stop, members with SMART/error, absent roster, next round, pulls); history tab = API note', () => {
  const session={id:'s1',image_id:'img_1',image_name:'Office 2024',group_id:'grp_LAB303',group_label:'כיתה 303',prefix:'LAB',expected_clients:2,joined:1,state:'open',starts_in_seconds:252,kind:'multicast',
    members:[{mac:'b4:2e:99:07:1a:c1',hostname:'LAB-01',state:'waiting',bytes_written:0,bytes_total:0,error:'disk error',disks:[{disk_number:1,verdict:'fail',decision:'replace'}]}],roster:['b4:2e:99:07:1a:c1','b4:2e:99:07:1a:c2'],stuck:{}};
  const {run}=setup({session});
  run("SESSION_MACHINES={group:'grp_LAB303',list:[{mac:'b4:2e:99:07:1a:c1',suffix:'01'},{mac:'b4:2e:99:07:1a:c2',suffix:'02'},{mac:'b4:2e:99:07:1a:c3',suffix:'03'}]}");
  let html=run('deploy(1)'); balanced(html);
  assert.match(html,/obj-name">סבב כיתה</); assert.match(html,/v2/);
  assert.match(html,/<td><strong>כיתה 303<\/strong><\/td><td>Office 2024<\/td><td>כיתה 303<\/td><td>1 \/ 2<\/td>/); assert.match(html,/פתוח · מתחיל בעוד 252 ש'/);
  assert.match(html,/startRound\(\)">התחל עכשיו</); assert.match(html,/stopRound\(\)">עצור סבב \(הקלדת שם\)</);
  assert.match(html,/<b>LAB-01<\/b>/); assert.match(html,/disk error/); assert.match(html,/disk-smart fail/); assert.match(html,/<b>LAB-02<\/b><div class="sub">טרם הצטרפה/); assert.match(html,/הסבב הבא: 03/);
  assert.doesNotMatch(html,/mgrid|kpi|rnew/,'no room content on the class tab');
  run('ME.role="deploy"'); assert.match(run('deploy(1)'),/startRound\(\)/,'deploy operates class rounds too'); run('ME.role="admin"');
  run('OVERVIEW.session=null; OVERVIEW.pulls=[{image_name:"kali",kind:"unicast",members:[{mac:"b4:2e:99:07:1a:c3",hostname:"LAB-03",state:"running",bytes_written:30,bytes_total:100}]}]');
  html=run('deploy(1)'); assert.match(html,/אין סבב כיתה פתוח/); assert.match(html,/משיכות יוניקאסט \(1\)/); assert.match(html,/<b>LAB-03<\/b><span class="tag">יוניקאסט<\/span>/);
  run('OVERVIEW.session={id:"w1",image_id:"img_1",image_name:"office365",group_id:"grp_CLONERS",prefix:"ROOM",expected_clients:3,joined:2,state:"running",members:[{mac:"'+C1+'",state:"running"}],roster:null,stuck:{}}; OVERVIEW.room={id:"rnd_1"}');
  html=run('deploy(1)'); assert.match(html,/אין סבב כיתה — הסבב הפעיל הוא גל בחדר המשכפלים/); assert.doesNotMatch(html,/stopRound|<table class="table"/,'the room wave is not a class round — no class stop button on it');
  run('OVERVIEW=null'); assert.match(run('deploy(1)'),/\/overview לא נקרא/);
  html=run('deploy(2)'); balanced(html); assert.match(html,/obj-name">היסטוריית סבבים</); assert.match(html,/דורש API/); assert.match(html,/selectPageById\('logs'\)">ביומן</); assert.doesNotMatch(html,/class="ev"|mgrid/);
  run('ME.role="deploy"'); assert.match(run('deploy(2)'),/ביומן \(מנהל\)/);
});

test('data: loadDeploy reads /overview, /room, /images, /machines, /groups, /disk-failures, /net and the class roster; the 2s poll re-reads /room + /machines on the deploy page and re-renders only on change (prompt included)', async () => {
  const session={id:'s1',image_id:'img_1',image_name:'Office 2024',group_id:'grp_LAB303',expected_clients:2,joined:0,state:'open',members:[],roster:[],stuck:{}};
  const {run,requests,fixtures}=setup({session});
  run('ROOM=null; IMAGES=null; MACHINES=null; renderActivity=()=>{}; markStatusFresh=()=>{}; rendered=0');
  await run('loadDeploy()');
  for(const u of ['/overview','/room','/images','/machines','/groups','/disk-failures','/net','/machines?group=grp_LAB303']) assert.ok(requests.some(r=>r.url==='/api/console'+u),u);
  assert.equal(run('ROOM.machines.length'),3); assert.equal(run('IMAGES.length'),3); assert.equal(run('SESSION_MACHINES.group'),'grp_LAB303'); assert.ok(run('rendered')>=1);
  run("current='deploy'; rendered=0"); requests.length=0;
  await run('refreshGroupLive()');
  assert.equal(requests.filter(r=>r.url==='/api/console/room').length,1); assert.equal(requests.filter(r=>r.url==='/api/console/machines').length,1); assert.equal(run('rendered'),0,'same answer → no re-render');
  fixtures['/machines']=fixtures['/machines'].map(m=>m.mac===C2?{...m,prompt:'Disk 2: SMART warn - write anyway?'}:m);
  await run('refreshGroupLive()'); assert.equal(run('rendered'),1,'a new operator question re-renders'); assert.equal(run('MACHINES.find(m=>m.mac==="'+C2+'").prompt'),'Disk 2: SMART warn - write anyway?');
  fixtures['/room']=roomRunning(); await run('refreshGroupLive()'); assert.equal(run('rendered'),2); assert.equal(run('ROOM.round.id'),'rnd_1');
  run("current='images'"); requests.length=0; await run('refreshGroupLive()'); assert.equal(requests.length,0,'not on the deploy page (and no cloners object open) → no poll');
  run("ROOM=null; DEPLOY.roomErr=''"); fixtures['/room']={__status:500,detail:'boom'}; await run('loadDeploy()');
  assert.equal(run('ROOM'),null); assert.equal(run('DEPLOY.roomErr'),'boom','a failed read is "unread", not "no round"');
});
