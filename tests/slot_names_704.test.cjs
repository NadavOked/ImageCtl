// ‏#704: שם לכל חיבור של כונן במחשב השיכפול (נדב, 11/10): "שיראה לי לאיזה
// חריצים מחוברים כוננים ... ואז אני אתן שם לכל התקן" + "NVMe ... 'דיסק מיוחד'".
// המפתח: sata:<port> (השם הולך עם החריץ), serial:<serial> לכונן בלי port.
// בלי שם — התצוגה כמו היום ("דיסק N", SATA 0 → דיסק 1). השם הוא תווית בלבד.
// בלי דפדפן: console.js + net.js + progress.js ב-vm מול DOM מזערי ו-fetch מזויף.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');
const GB256=256060514304;
const C1='a0:48:1c:8a:18:40', C2='78:ac:c0:9b:11:c2';
const disk=(dev,port,serial,extra={})=>({dev,port,serial,model:'870 EVO',size_bytes:GB256,smart:'ok',...extra});
const drawer=(dev,port,serial,extra={})=>({dev,port,serial,model:'870 EVO',size_bytes:GB256,fresh:true,selected:false,state:null,error:null,bytes_written:0,bytes_total:0,stalled_s:null,crc_delta:null,smart:'ok',...extra});

function setup({names2={'sata:2':'האמצעית','serial:NV-1':'דיסק מיוחד'},names1={'sata:2':'תחתונה'},round=false,admin=true}={}) {
  const nodes=new Map(), requests=[];
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},style:{removeProperty(){}},
      classList:{add(){},remove(){},toggle(){}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.innerHTML+=html;},setAttribute(){},removeAttribute(){},focus(){}});
    return nodes.get(key);
  }
  const c2disks=[disk('sda',1,'S-1'),disk('sdb',3,'S-3'),disk('nvme0n1',null,'NV-1'),disk('sdc',null,null,{model:'USB stick',removable:true})];
  const fixtures={
    '/groups':[{id:'grp_CLONERS',label:'מחשבי שיכפול',role:'cloner',sort:2,machines:2}],
    '/machines':[
      {mac:C1,suffix:'מחשב 1',group_id:'grp_CLONERS',note:null,drawer_count:2,prompt:null,disks:[],disks_reported_at:'2026-10-11T08:00:00Z',inventory:null,inventory_seen_at:null,slot_names:names1},
      {mac:C2,suffix:'מחשב 2',group_id:'grp_CLONERS',note:null,drawer_count:3,prompt:null,disks:c2disks,disks_reported_at:'2026-10-11T08:00:00Z',inventory:null,inventory_seen_at:null,slot_names:names2}],
    '/net':[], '/disk-failures':[], '/shrink-records':[], '/monitor/machines':[], '/tasks':[], '/room/history':[], '/journal':[],
    '/room':{round:round?{id:'rnd_1',image_id:'img_1',image_name:'office365',target_drives:3,written_drives:0,remaining_drives:3,wave_number:1,wave_state:'running',ready_drives:3,opened_by:'nadav',source:null}:null,stream_stalled:false,machines:[
      {mac:C1,name:'מחשב 1',drawer_count:2,awake:true,drawers:0,fresh_drawers:0,drawer_list:[],joined:false,state:null,bytes_written:0,bytes_total:0,error:null,slot_names:names1},
      {mac:C2,name:'מחשב 2',drawer_count:3,awake:true,drawers:2,fresh_drawers:2,joined:round,state:round?'running':null,bytes_written:0,bytes_total:0,error:null,slot_names:names2,
        drawer_list:[drawer('sda',1,'S-1',round?{state:'writing',bytes_written:10,bytes_total:100}:{}),drawer('sdb',2,'S-9',round?{state:'writing',bytes_written:10,bytes_total:100}:{})]}]},
  };
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options={})=>{
      const key=url.replace('/api/console','').split('?')[0]; requests.push({url,options});
      return {status:200,ok:true,headers:{get:()=>null},json:async()=>fixtures[key] ?? {ok:true}};
    }});
  for(const f of ['progress.js','console.js','net.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run(`ME={username:"nadav",role:"${admin?'admin':'deploy'}",server_name:"srv",version:"v0.67.1",capabilities:{}}; current="machines"; sheet = o => { globalThis.formOptions=o; }; toast=(m)=>{ globalThis.toasted=m; }; renderCurrent=()=>{}; selectPageById=(id)=>{ current=id; }; openDrawer=(t,h)=>{ globalThis.drawerHtml=h; }; setDrawerBody=(h)=>{ globalThis.drawerHtml=h; };`);
  for(const [name,key] of [['MACHINES','/machines'],['GROUPS','/groups'],['DISK_FAILURES','/disk-failures'],['SHRINK_RECORDS','/shrink-records'],['NET','/net'],['MONITOR_ROWS','/monitor/machines'],['ROOM','/room'],['ROOM_HISTORY','/room/history'],['CAPTURE_TASKS','/tasks']]) run(name+'='+JSON.stringify(fixtures[key]));
  run('CAPTURE_TASKS_READ=true; ROOM_KEY=JSON.stringify(ROOM);');
  return {run,requests,fixtures};
}
const card=(html,mac)=>html.slice(html.indexOf(`data-mac="${mac}"`)).split(/<div class="mcard(?: off)?"/)[0];
const slot=(html,n)=>{ const m=html.match(new RegExp(`<div class="disk[^"]*" data-slot="${n}"[^>]*>[^]*?</div>`)); assert.ok(m,'slot '+n); return m[0]; };

test('grid: a named slot shows its name; an unnamed slot stays "דיסק N"; the number survives in the tooltip', () => {
  const {run}=setup();
  run("openClass('grp_CLONERS')"); const html=run('machines(0)');
  const c2=card(html,C2);
  assert.match(slot(c2,1),/<b>דיסק 1<\/b>/,'no name → as today');
  assert.match(slot(c2,3),/<b>דיסק 3<\/b>/);
  const c1=card(html,C1);
  assert.match(slot(c1,2),/<b>תחתונה<\/b><span class="cap">ריק<\/span>/,'an empty slot keeps its name — the name is the slot, not the disk');
  assert.match(slot(c1,2),/title="דיסק 2 · SATA 1 · ריק"/,'the tooltip still says the number');
  assert.match(slot(c1,1),/<b>דיסק 1<\/b><span class="cap">ריק<\/span>/);
});

test('grid during a round: the name follows the slot — a new disk (other serial) in SATA 1 is still "האמצעית"', () => {
  const {run}=setup({round:true});
  run("openClass('grp_CLONERS')"); const html=run('machines(0)');
  const s2=slot(card(html,C2),2);
  assert.match(s2,/<b>האמצעית<\/b>/);
  assert.match(s2,/title="דיסק 2 · SATA 1 · 870 EVO · S-9/,'named slot: the tooltip opens with "דיסק N"');
});

test('a name is escaped, never HTML', () => {
  const {run}=setup({names2:{'sata:1':'a<b>&"'}});
  run("openClass('grp_CLONERS')"); const html=run('machines(0)');
  assert.match(slot(card(html,C2),1),/<b>a&lt;b&gt;&amp;&quot;<\/b>/);
});

test('drawer disk list: one compact row per SATA/NVMe/USB slot, rename only for admin', () => {
  const {run}=setup();
  run(`openMachineDetail('${C2}')`); const html=run('drawerHtml');
  const sec=html.slice(html.indexOf('<div class="sec" data-cnt="4">דיסקים</div>'));
  assert.doesNotMatch(html,/חריצים וכוננים|דיווח אחרון|ברירת מחדל|>S-1<|>S-3<|>NV-1</);
  const rows=[...sec.matchAll(/<tr data-slot="([^"]*)">([^]*?)<\/tr>/g)].map(m=>[decodeURIComponent(m[1]),m[2]]);
  assert.deepEqual(rows.map(r=>r[0]),['sata:1','sata:2','sata:3','serial:NV-1',''],'SATA 0..2 by port (2 empty), then NVMe, then the USB stick that has no serial');
  const [s0,s1,s2,nv,usb]=rows.map(r=>r[1]);
  assert.match(s0,/<b>דיסק 1<\/b>/); assert.match(s0,/SATA 0/); assert.match(s0,/870 [^]*256GB/);
  assert.match(s1,/<b>האמצעית<\/b>/); assert.match(s1,/SATA 1/); assert.match(s1,/ריק/);
  assert.match(s2,/<b>דיסק 3<\/b>/); assert.match(s2,/SATA 2/); assert.match(s2,/870 [^]*256GB/);
  assert.match(nv,/<b>דיסק מיוחד<\/b>/); assert.match(nv,/NVMe 0/); assert.match(nv,/870 [^]*256GB/);
  assert.match(usb,/USB/); assert.match(usb,/Usb [^]*256GB/); assert.doesNotMatch(usb,/renameSlot/,'no stable key → no rename');
  assert.match(s1,new RegExp(`renameSlot\\('${encodeURIComponent(C2)}','sata%3A2'\\)`));
  assert.match(nv,/renameSlot\('[^']+','serial%3ANV-1'\)/);
});

test('drawer disk marketing label normalises vendor and size; SMART appears only when bad', () => {
  const {run}=setup();
  run(`MACHINES[1].disks=[
    {dev:'sda',port:1,serial:'a',model:'SAMSUNG MZ7LN256',size_bytes:238*1024**3,smart:'ok'},
    {dev:'sdb',port:2,serial:'b',model:'WESTERN WD500',size_bytes:466*1024**3,smart:'warn'},
    {dev:'sdc',port:3,serial:'c',model:'KINGSTON X',size_bytes:931*1024**3,smart:'fail'}];`);
  const html=run(`machineDrawerHtml(findMachine('${C2}'))`);
  assert.match(html,/Samsung [^]*?256GB/); assert.doesNotMatch(html,/SMART תקין/);
  assert.match(html,/Western [^]*?500GB[^]*SMART אזהרה/);
  assert.match(html,/Kingston [^]*?1TB[^]*SMART תקלה/);
});

test('build machines use the same compact rows without slot rename controls', () => {
  const {run}=setup();
  run(`GROUPS[0].role='build'`);
  const html=run(`machineDrawerHtml(findMachine('${C2}'))`);
  assert.match(html,/<th>שם<\/th><th>חריץ<\/th><th>כונן<\/th>/);
  assert.match(html,/SATA 0/); assert.match(html,/870 [^]*256GB/);
  assert.doesNotMatch(html,/renameSlot|חריצים מוגדרים|דיווח אחרון|חריצים וכוננים/);
});

test('rename sheet: PUTs {slot, name}; empty clears to the default with null', async () => {
  const {run,requests}=setup();
  run(`renameSlot('${encodeURIComponent(C2)}','serial%3ANV-1')`);
  const o=run('formOptions');
  assert.match(o.sub,/NVMe 0/); assert.equal(o.fields[0].value,'דיסק מיוחד');
  assert.match(o.fields[0].help,/24/); assert.match(o.fields[0].help,/סידורי/);
  await o.onSubmit({name:'  דיסק 4  '});
  let put=requests.find(r=>r.options.method==='PUT');
  assert.equal(put.url,`/api/console/machines/${encodeURIComponent(C2)}/slot-names`);
  assert.deepEqual(JSON.parse(put.options.body),{slot:'serial:NV-1',name:'דיסק 4'});
  requests.length=0;
  run(`renameSlot('${encodeURIComponent(C2)}','sata%3A1')`);
  assert.equal(run('formOptions').fields[0].value,'');
  assert.equal(run('formOptions').fields[0].placeholder,'דיסק 1');
  await run('formOptions').onSubmit({name:'   '});
  put=requests.find(r=>r.options.method==='PUT');
  assert.deepEqual(JSON.parse(put.options.body),{slot:'sata:1',name:null});
});

test('the drawer for a non-admin has no rename link', () => {
  const {run}=setup({admin:false});
  const html=run(`machineDrawerHtml(findMachine('${C2}'))`);
  assert.ok(html.includes('דיסקים'));
  assert.doesNotMatch(html,/renameSlot/);
  run(`renameSlot('${encodeURIComponent(C2)}','sata%3A1')`);
  assert.equal(run('globalThis.formOptions'),undefined,'no sheet for a non-admin');
});

test('without any names everything reads exactly as before', () => {
  const {run}=setup({names1:{},names2:{}});
  run("openClass('grp_CLONERS')"); const html=run('machines(0)');
  for(const n of [1,2,3]) assert.match(slot(card(html,C2),n),new RegExp(`<b>דיסק ${n}</b>`));
  assert.match(slot(card(html,C1),2),/title="דיסק 2 · SATA 1 · ריק"/);
  const {run:run2}=setup({names1:{},names2:{}});
  // old server: no slot_names field at all
  run2('MACHINES.forEach(m=>delete m.slot_names)');
  run2("openClass('grp_CLONERS')");
  assert.match(slot(card(run2('machines(0)'),C2),1),/<b>דיסק 1<\/b>/);
});
