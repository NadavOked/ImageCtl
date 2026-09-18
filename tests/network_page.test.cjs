// ‏#954 גל 8: רשת כאובייקט — תרשים (network.md) · חיבורים פיזיים (network-nics.md) ·
// רשת הפצה (network-deploy.md). בלי דפדפן: console.js + net.js + netcfg.js + progress.js
// רצים ב-vm מול DOM מזערי ו-fetch מזויף (כמו health_ports_page.test.cjs).
// עיקרון 5: "בפועל" ≠ "מוגדר" ≠ "לא נקרא" — כל מקור שנכשל הוא מצב משלו, לא "אין".
// ‏#53: DHCP/כתובת נשארים מאחורי הטפסים הקיימים (editNic/editAddress + הקלדת שם הכרטיס).
// ‏#705: "מה מותר על כל וילן" — רק ממה ש-/ports.bind אומר; בלעדיו "דורש API", לא המצאה.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');

const C1='a0:48:1c:8a:18:40', C2='78:ac:c0:9b:11:c2', B1='c8:d9:d2:0b:fe:32', UNREG='de:ad:be:ef:00:01';
const ago=(s)=>new Date(Date.now()-s*1000).toISOString();
const nic=(name,extra={})=>({name,mac:'bc:24:11:00:00:'+name.slice(-2),state:'up',present:true,addresses:[],enabled:false,proxy:false,trunk:false,
  range_start:'',range_end:'',netmask:'255.255.255.0',gateway:'',dns:[],lease:'12h',server_ip:'',description:'',speed_mbps:1000,
  dhcp_live:{state:'off',checked:true,detail:''},dhcp_live_label:'כבוי',dhcp_diverged:false,...extra});
const NICS=[
  nic('ens18',{addresses:['10.44.10.1/24'],description:'ניהול'}),
  nic('ens19',{addresses:['10.44.9.10/24'],description:'הפצה',enabled:true,range_start:'10.44.9.100',range_end:'10.44.9.200',server_ip:'10.44.9.10',dns:['10.44.10.2'],
    dhcp_live:{state:'serving',checked:true,detail:''},dhcp_live_label:'משרת'}),
  nic('ens20',{addresses:['10.44.11.2/24'],description:'כיתות',proxy:true,trunk:true,server_ip:'10.44.11.1',dhcp_live:{state:'serving',checked:true,detail:''},dhcp_live_label:'proxy פעיל'}),
  nic('ens21',{state:'down',speed_mbps:null}),
];
const cfgRow=(name,extra={})=>({name,mode:'static',mode_he:'כתובת סטטית',address:'',netmask:'255.255.255.0',gateway:'',dns:[],routes:[],present:true,state:'up',live_addresses:[],mismatches:[],...extra});
const NETCFG={interfaces:[
    cfgRow('ens18',{address:'10.44.10.1',gateway:'10.44.10.254',dns:['10.44.10.2'],live_addresses:['10.44.10.1/24'],routes:[{destination:'10.44.13.0',netmask:'255.255.255.0',gateway:'10.44.10.254'}]}),
    cfgRow('ens19',{address:'10.44.9.10',live_addresses:['10.44.9.10/24']}),
    cfgRow('ens20',{address:'10.44.11.1',live_addresses:['10.44.11.2/24'],mismatches:['כתובת: מוגדר 10.44.11.1/24, בפועל 10.44.11.2/24']}),
    cfgRow('ens21',{mode:'manual',mode_he:'לא מנוהל',state:'down'}),
  ],
  live:{checked:true,reason:'',routes:['default via 10.44.10.254','10.44.13.0/24 via 10.44.10.254'],nameservers:['10.44.10.2']},
  sourced:true,resolv_path:'/etc/resolv.conf',
  rollback:{armed:true,armed_detail:'active',unit:'imagectl-netrollback.timer',window_seconds:60,pending:false,interface:'',seconds_left:0,expired:false,corrupt:false}};
const NET=[
  {mac:C1,ip:'10.44.9.118',description:'',first_seen:ago(9000),last_seen:ago(10),registered:true,name:'מחשב 1',group_id:'grp_CLONERS',group_label:'מחשבי שיכפול',role:'cloner',
   boot:{step:'hello',label:'hello · בסוכן',index:9,total:9,at:ago(10),seconds:10,next_step:null,next_label:'',stalled:false}},
  {mac:B1,ip:'10.44.9.20',description:'',first_seen:ago(90000),last_seen:ago(3*3600),registered:true,name:'בנייה 1',group_id:'grp_BUILD',group_label:'מחשבי בנייה',role:'build',boot:null},
  {mac:UNREG,ip:'10.44.9.199',description:'',first_seen:ago(60),last_seen:ago(60),registered:false,name:null,group_id:null,group_label:null,role:null,
   boot:{step:'menu-local',label:'GRUB → דיסק מקומי',index:3,total:9,at:ago(60),seconds:60,next_step:null,next_label:'',stalled:false}},
];
const port=(id,name,p,proto,state,detail,extra={})=>({id,name,port:p,proto,desc:'desc '+id,target:'target '+id,state,detail,note:'',...extra});
const PORTS=[
  port('tftp','TFTP','69','udp','ok','dnsmasq מגיש',{enabled:true,bind:'0.0.0.0',toggle:'confirm',off_means:'x'}),
  port('http_boot','HTTP','8080','tcp','ok','uvicorn',{enabled:true,bind:'0.0.0.0',toggle:'confirm',off_means:'x'}),
  port('http_console','HTTP','8081','tcp','ok','uvicorn',{enabled:true,bind:'10.44.10.1',toggle:'confirm',off_means:'x'}),
  port('multicast','Multicast','9000–9001','udp','off','אין סבב',{enabled:true,bind:['10.44.9.10'],toggle:'none',off_means:'x'}),
  port('interserver','mTLS','8443','tcp','off','כבוי',{enabled:false,bind:'',toggle:'api',off_means:'x'}),
  port('ssh_stations','SSH','22','tcp','ok','x'),
];
const PORTS_NO_BIND=PORTS.map(({bind,...p})=>p);
const SSH={stations:{enabled:false,evidence:'closed',detail:'',confirm_word:'imagectl.debug'},listeners:{checked:true,addresses:['10.44.10.1'],wildcard:false,reason:'',port:22},stray:[],
  interfaces:[{name:'ens18',state:'up',addresses:['10.44.10.1/24'],enabled:true,listening:true},{name:'ens19',state:'up',addresses:['10.44.9.10/24'],enabled:false,listening:false},
              {name:'ens20',state:'up',addresses:['10.44.11.2/24'],enabled:false,listening:null}]};
const MON=[{mac:C1,name:'מחשב 1',role:'cloner',ip:'10.44.9.118',online:true},{mac:C2,name:'מחשב 2',role:'cloner',ip:'10.44.9.59',online:false},{mac:B1,name:'בנייה 1',role:'build',ip:null,online:false}];
const NODES=[{id:'sn1',label:'סניף חיפה',base_url:'https://10.44.13.5:8443',group_id:null,node_id:'sn_1',enrolled_at:'2026-09-10T00:00:00+00:00',disabled_at:null,group_label:null},
             {id:'sn2',label:'סניף ישן',base_url:'https://10.44.10.77:8443',group_id:null,node_id:'sn_2',enrolled_at:'2026-09-01T00:00:00+00:00',disabled_at:'2026-09-02T00:00:00+00:00',group_label:null}];

function setup(over={},me={}) {
  const nodes=new Map(), requests=[];
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},className:'',style:{removeProperty(){}},
      classList:{add(){},remove(){},toggle(){},contains(){return false;}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.innerHTML+=html;},setAttribute(){},removeAttribute(){},hasAttribute(){return false;},getAttribute(){return null;},closest(){return {style:{}};},focus(){},scrollIntoView(){}});
    return nodes.get(key);
  }
  const fixtures={'/net/interfaces':NICS,'/net/proxy-support':{read:true,version:'2.91',verified:false,broken:true,reason:'dnsmasq 2.91 — proxy שבור'},
    '/net/config':NETCFG,'/net':NET,'/ports':PORTS,'/ssh':SSH,'/monitor/machines':MON,'/storage-nodes':NODES,
    '/storage-nodes/sn1/machines':{connected:false,error:'timeout',machines:[],node_id:'sn_1'},
    '/net/interfaces/ens19/probe':{interface:'ens19',checked:true,servers:[]},
    '/net/interfaces/ens18/probe':{interface:'ens18',checked:false,servers:[]},
    '/net/interfaces/ens20/probe':{interface:'ens20',checked:true,servers:['10.44.11.254']},
    '/machines':[{mac:C1,suffix:'מחשב 1',group_id:'grp_CLONERS',disks:null}],'/groups':[{id:'grp_CLONERS',label:'מחשבי שיכפול',role:'cloner',sort:1}],
    ...over};
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Object,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options={})=>{
      const key=url.replace('/api/console','').split('?')[0]; requests.push({url:key,method:options.method||'GET',body:options.body?JSON.parse(options.body):null});
      const f=fixtures[key];
      if(f instanceof Error) return {status:500,ok:false,headers:{get:()=>null},json:async()=>({detail:f.message})};
      if(f===403) return {status:403,ok:false,headers:{get:()=>null},json:async()=>({detail:'admin only'})};
      if(f===409) return {status:409,ok:false,headers:{get:()=>null},json:async()=>({detail:'not standalone'})};
      return {status:200,ok:true,headers:{get:()=>null},json:async()=>f ?? {ok:true,verified:true,apply_error:null}};
    }});
  for(const f of ['progress.js','console.js','net.js','netcfg.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME='+JSON.stringify({username:'nadav',role:'admin',server_name:'שרת המכללה',version:'v0.38.1',capabilities:{enroll_secondary:true},...me})
    +'; globalThis.sheets=[]; globalThis.toasts=[]; sheet = o => { sheets.push(o); }; toast=(m)=>{ toasts.push(m); }; renderCurrent=()=>{ globalThis.rendered=(globalThis.rendered||0)+1; };'
    +' globalThis.selected=[]; selectPageById=(id)=>{ selected.push(id); current=id; }; openMachineDetail=(m)=>{ globalThis.openedMac=m; }; openAddMachine=(o)=>{ globalThis.addMachine=o; };');
  run('MACHINES='+JSON.stringify(fixtures['/machines'])+'; GROUPS='+JSON.stringify(fixtures['/groups'])+';');
  return {run,requests,fixtures,ctx,node};
}
function balanced(html) {
  const stack=[]; const voids=new Set(['input','br','hr','img','meta','link','source','wbr','path','rect','circle']);
  for(const m of html.matchAll(/<\/?([a-z][\w-]*)\b[^>]*>/gi)) {
    const tag=m[1].toLowerCase();
    if(m[0].endsWith('/>') || voids.has(tag)) continue;
    if(m[0].startsWith('</')) assert.equal(stack.pop(),tag,'Unbalanced '+m[0]); else stack.push(tag);
  }
  assert.deepEqual(stack,[]);
}
// ‏LRI…PDI (U+2066/2069): רצפי ASCII בתוך טקסט ה-SVG מבודדים (bidi()) — הבדיקות משוות בלעדיהם
const plain=(s)=>String(s).replace(/[\u2066\u2069]/g,'');
const row=(html,needle)=>{const i=html.indexOf(needle); assert.ok(i>=0,'missing row: '+needle); const s=html.lastIndexOf('<tr',i); return html.slice(s,html.indexOf('</tr>',i)+5);};
const card=(html,title)=>{const i=html.indexOf('<span>'+title); assert.ok(i>=0,'missing card: '+title); const re=/<div class="c(?:12|8|6|4|3) card">/g; let s=0,e=html.length; for(const m of html.matchAll(re)){ if(m.index<i) s=m.index; else { e=m.index; break; } } return html.slice(s,e);};
async function loaded(over,me) { const t=setup(over,me); t.run('current="network"'); await t.run('loadNetwork()'); return t; }

/* ---------- האובייקט והניווט ---------- */

test('network is one own page with four tabs (ports = the wave-5 page); the old nic/netdeploy/network pages are gone',()=>{
  const {run}=setup();
  assert.equal(run('pages.network.own'),true); assert.deepEqual(JSON.parse(run('JSON.stringify(pages.network.tabs)')),['תרשים','חיבורים פיזיים','רשת הפצה','פורטים']);
  assert.equal(run('pages.network.load===loadNetwork'),true);
  assert.equal(run('pages.nic'),undefined); assert.equal(run('pages.netdeploy'),undefined);
  for(const fn of ['renderNicCard','netBannerHtml','nic','netdeploy','saveNetwork','confirmSaveNetwork','loadNetcfgData','loadNetPages','editDeployNic','nicUnion','selectSidebarNic'])
    assert.equal(run(`typeof ${fn}`),'undefined',fn+' should be gone');
  assert.equal(run('networkPage(0)'),run('pagePlaceholder()'),'placeholder before the first read');
  // לשונית "פורטים" מנווטת לדף הקיים; דף הפורטים מצביע חזרה ללשוניות האובייקט
  assert.equal(run('netTabClick(3)'),"selectPageById('ports')"); assert.equal(run('netTabClick(1)'),'openNetwork(1)');
});

test('loadNetwork reads every source once, sets the shared globals for net.js/netcfg.js/ports, and selects the deploy NIC',async()=>{
  const {run,requests,node}=await loaded();
  const urls=requests.map(r=>r.url);
  for(const u of ['/net/interfaces','/net/config','/net','/ports','/ssh','/monitor/machines','/storage-nodes','/storage-nodes/sn1/machines']) assert.equal(urls.filter(x=>x===u).length,1,u);
  assert.ok(!urls.includes('/storage-nodes/sn2/machines'),'a disabled secondary is not asked');
  assert.equal(run('NETW.nics.length'),4); assert.equal(run('NETCFG.live.checked'),true); assert.equal(run('NET.length'),3);
  assert.equal(run('SSH_STATE.interfaces.length'),3); assert.equal(run('NETW.sel'),'ens19','the DHCP NIC is selected by default');
  assert.equal(run('JSON.stringify(NETW.err)'),'{}');
  assert.equal(run('globalThis.rendered'),1);
  // העץ: כרטיס לכל NIC פותח את האובייקט בלשונית 1 עם הכרטיס נבחר
  const tree=node('#nicTree').innerHTML;
  assert.match(tree,/onclick="openNetwork\(1,'ens18',this\)"/); assert.equal((tree.match(/class="inventory-node/g)||[]).length,4);
});

test('openNetwork: from another page selects the network page then the tab; inside the page a NIC from the tree becomes the selection',async()=>{
  const {run}=await loaded();
  run('globalThis.calls=[]; selectPage=(el,id)=>{ calls.push(["page",id]); current=id; currentTab=0; }; activateTab=(i)=>{ calls.push(["tab",i]); currentTab=i; }; markTreeSelection=()=>{};');
  run('current="home"; openNetwork(2)');
  assert.deepEqual(JSON.parse(run('JSON.stringify(calls)')),[['page','network'],['tab',2]]);
  run('calls=[]; current="network"; currentTab=1; rendered=0; openNetwork(1,"ens18")');
  assert.equal(run('calls.length'),0); assert.equal(run('NETW.sel'),'ens18'); assert.equal(run('rendered'),1,'same tab → re-render, not a page reload');
});

test('deploy role: the network object is not allowed and loadNetwork reads nothing',async()=>{
  const {run,requests}=setup({},{role:'deploy',capabilities:{}});
  assert.equal(run('pageAllowed("network")'),false); assert.equal(run('pageAllowed("ports")'),false);
  await run('loadNetwork()');
  assert.equal(requests.length,0);
});

/* ---------- לשונית 0: התרשים ---------- */

test('diagram model: a lane per NIC, VLAN kind by configuration, "allowed" only from /ports.bind, clients placed by subnet with every line state',async()=>{
  const {run}=await loaded();
  const m=plain(run('JSON.stringify(netDiagramModel())')); const model=JSON.parse(m);
  assert.match(run('JSON.stringify(netDiagramModel())'),/\u2066de:ad:be:ef:00:01\u2069/,'ASCII runs are isolated for RTL');
  assert.equal(model.lanes.length,4);
  const lane=(name)=>model.lanes.find(l=>l.nic.name===name);
  assert.equal(lane('ens19').vlan.kind,'deploy'); assert.equal(lane('ens18').vlan.kind,'mgmt'); assert.equal(lane('ens20').vlan.kind,'proxy'); assert.equal(lane('ens21').vlan.kind,'none');
  assert.deepEqual(lane('ens19').allowed,['TFTP 69','HTTP 8080','Multicast 9000–9001'],'bind 0.0.0.0 + the NIC address; 8081 bound elsewhere');
  assert.deepEqual(lane('ens18').allowed,['TFTP 69','HTTP 8080','HTTP 8081']);
  assert.deepEqual(lane('ens21').allowed,['TFTP 69','HTTP 8080'],'no address → only wildcard binds');
  assert.equal(lane('ens19').led,'ok'); assert.equal(lane('ens21').led,'off');
  const kinds=(l)=>l.clients.map(c=>[c.kind,c.led,!!c.dashed]);
  // הפצה: משכפלים (1 מתוך 2 מחוברים = כתום), בנייה בלי כתובת (מקווקו), לא רשום (כתום)
  assert.deepEqual(kinds(lane('ens19')),[['cloner','warn',false],['build','',true],['unreg','warn',false]]);
  assert.match(lane('ens19').clients[0].title,/משכפלים — מחשב 1, מחשב 2/); assert.match(lane('ens19').clients[0].sub,/10\.44\.9\.118 · 10\.44\.9\.59 · 1 מתוך 2 מחוברים/);
  assert.match(lane('ens19').clients[2].title,/לא רשום — de:ad:be:ef:00:01/); assert.match(lane('ens19').clients[2].sub,/10\.44\.9\.199 · GRUB → דיסק מקומי/);
  assert.equal(lane('ens19').unreg,1);
  // ניהול: הקונסולה (ירוק — הדף הזה נטען), סניף חיפה (אדום — לא ענה; הכתובת מחוץ לכל רשת → נופל לניהול), סניף מושבת (אפור מקווקו, לפי הכתובת)
  assert.deepEqual(kinds(lane('ens18')),[['console','ok',false],['branch','err',false],['branch','',true]],'not answering = red solid line; disabled = dashed');
  assert.match(lane('ens18').clients[0].sub,/מי עוד מחובר — דורש API/);
  assert.match(lane('ens18').clients[1].sub,/https:\/\/10\.44\.13\.5:8443 · לא ענה: timeout/);
  assert.match(lane('ens18').clients[2].sub,/מושבת/);
  // כיתות = תיבה סטטית v2 על ה-proxy, מקווקו, בלי נתונים
  assert.deepEqual(kinds(lane('ens20')),[['class','',true]]); assert.equal(lane('ens20').clients[0].title,'כיתות — v2');
  assert.deepEqual(model.orphans,[]);
});

test('diagram SVG: explicit width/height (gotcha), line classes per state, dashed = off, selected NIC highlighted, nothing invented',async()=>{
  const {run}=await loaded();
  const html=plain(run('networkPage(0)')); balanced(html);
  assert.match(html,/<svg class="netdiag-svg" xmlns="http:\/\/www\.w3\.org\/2000\/svg" width="1100" height="\d+" viewBox="0 0 1100 \d+" role="img"/);
  assert.match(html,/class="box sel"/); assert.equal((html.match(/class="box sel"/g)||[]).length,1);
  assert.match(html,/<g class="hit" role="button" tabindex="0" aria-label="ens19" onclick="netSelect\('ens19'\)"/);
  assert.match(html,/class="ln ok" d="M840 [\d.]+ H720"/,'NIC up → VLAN: green'); assert.match(html,/class="ln off" d="M840 [\d.]+ H720"/,'NIC down: dashed');
  assert.match(html,/class="ln err"/,'branch not answering: red'); assert.match(html,/class="ln warn"/,'unregistered: orange');
  assert.match(html,/stroke-dasharray="4 4"/);
  assert.match(html,/>bind: TFTP 69 · HTTP 8080 · Multicast 9000–9001</,'the visible line, not the tooltip');
  assert.match(html,/וילן ההפצה — לפי הגדרה/); assert.match(html,/רשת 10\.44\.9\.0\/24/);
  assert.doesNotMatch(html,/VLAN \d/,'no invented VLAN numbers'); assert.doesNotMatch(html,/10\.44\.12\./,'no mockup data');
  assert.match(html,/3 מחוברים · 1 לא רשומים/);
  assert.match(html,/שרת המכללה/); assert.match(html,/v0\.38\.1 · 4 כרטיסים · נקרא \d\d:\d\d/);
  // כותרת: מונים ותג
  assert.match(html,/4 כרטיסים · 4 וילנים \(לפי הגדרה — מודל וילן דורש API \(#705\)\) · DHCP הפצה: ens19 · משרת · 1 לא רשומים · 2 סניפים · 1 לא מגיבים/);
  assert.match(html,/class="pill warn">3 אזהרות</,'unregistered + branch down + ens20 mismatch');
  assert.match(html,/onclick="addNic\(\)">\+ כרטיס/); assert.match(html,/previewDnsmasq\(\)/);
  assert.match(html,/role="tab" aria-selected="true" tabindex="0" onclick="openNetwork\(0\)">תרשים/); assert.match(html,/onclick="selectPageById\('ports'\)">פורטים/);
  // הכרטיס הנבחר מתחת: בפועל/מוגדר/פער · DHCP/חכירה/מי עוד עונה · שירותים/SSH/פעולות
  const sel=card(html,'ens19 — הפצה');
  assert.match(sel,/class="st ok">תואם</); assert.match(sel,/class="st ok">משרת</); assert.match(sel,/12h · שער — · DNS 10\.44\.10\.2/); assert.match(sel,/class="st ">לא נבדק</);
  assert.match(sel,/TFTP 69 · HTTP 8080 · Multicast 9000–9001/); assert.match(sel,/class="st ">סגור</);
  assert.match(sel,/onclick="netEditAddress\('ens19'\)">עריכת כתובת/); assert.match(sel,/onclick="netEditDhcp\('ens19'\)">עריכת DHCP/); assert.match(sel,/onclick="netProbe\('ens19'\)">בדוק מי עונה/);
});

/* 18/09 (צילום מהמעבדה, v0.41.1): <text> ב-SVG אינו נשבר ואינו נחתך — "מותר (לפי bind): SSH 22 · HTTP 8082 · HTTP 8080 · DHCP 67"
   ורשימת ארבע כתובות גלשו מהתיבה ורכבו על הקווים. התיקון: הטקסט ב-foreignObject בגבולות התיבה, ellipsis + title מלא, פורמט קצר. */
const foBoxes=(svg)=>{
  const num=(tag,k)=>Number(tag.match(new RegExp(' '+k+'="([^"]+)"'))[1]);
  const out=[];
  for(const m of svg.matchAll(/<foreignObject([^>]*)>(.*?)<\/foreignObject>/g)){
    const rects=[...svg.slice(0,m.index).matchAll(/<rect class="(box[^"]*|vlan)"[^>]*>/g)]; const rect=rects[rects.length-1][0];
    out.push({fo:{x:num(m[1],'x'),y:num(m[1],'y'),w:num(m[1],'width'),h:num(m[1],'height')},box:{x:num(rect,'x'),y:num(rect,'y'),w:num(rect,'width'),h:num(rect,'height')},
      lines:[...m[2].matchAll(/<div class="fl ([^"]*)" title="([^"]*)">([^<]*)<\/div>/g)].map(l=>({cls:l[1],title:l[2],text:l[3]})),inner:m[2]});
  }
  return out;
};
test('diagram text stays inside its box: every box line is a foreignObject within the rect, ellipsis + full title, no free <text> but the server header',async()=>{
  const {run}=await loaded();
  const html=run('networkPage(0)'); const s0=html.indexOf('<svg class="netdiag-svg"'); const svg=html.slice(s0,html.indexOf('</svg>',s0));
  const boxes=foBoxes(svg);
  assert.equal(boxes.length,4+4+7,'NIC + VLAN per lane, and 7 client boxes (3 deploy, 3 mgmt, 1 class)');
  for(const b of boxes){
    assert.ok(b.fo.x>=b.box.x && b.fo.x+b.fo.w<=b.box.x+b.box.w,`horizontally inside: ${JSON.stringify(b)}`);
    assert.ok(b.fo.y>=b.box.y && b.fo.y+b.fo.h<=b.box.y+b.box.h,`vertically inside: ${JSON.stringify(b)}`);
    assert.ok(b.lines.length>=2,'title + at least one detail line'); assert.match(b.inner,/^<div xmlns="http:\/\/www\.w3\.org\/1999\/xhtml" class="fo(?: nic)?" dir="rtl">/);
    for(const l of b.lines){ assert.ok(l.title.length>=l.text.length,'title carries at least the visible text: '+l.text); assert.ok(l.text,'no empty line'); }
    assert.equal((b.inner.match(/<div class="fl /g)||[]).length,b.lines.length,'every line has a title');
  }
  assert.equal((svg.match(/<text /g)||[]).length,2,'only the two server header lines are free SVG text');
  assert.equal((html.match(/<foreignObject/g)||[]).length,boxes.length);
  // מה שמחזיק את הטקסט בפנים בדפדפן — ה-CSS, לא רק הגיאומטריה
  const css=fs.readFileSync(path.join(root,'console.css'),'utf8');
  assert.match(css,/\.page \.netdiag \.fl\{[^}]*white-space:nowrap;overflow:hidden;text-overflow:ellipsis/);
  assert.match(css,/\.page \.netdiag \.fl\.w2\{[^}]*-webkit-line-clamp:2/);
  assert.match(css,/\.page \.netdiag \.fo\{[^}]*overflow:hidden/);
});

test('diagram lines are short: "bind:" folds same-name services (HTTP 8080/8081) with the raw list in the tooltip; addresses are 2 + "+N" with all of them in the tooltip',async()=>{
  const many=[...MON,{mac:'aa:bb:cc:dd:ee:01',name:'מחשב 3',role:'cloner',ip:'10.44.9.60',online:true},{mac:'aa:bb:cc:dd:ee:02',name:'מחשב 4',role:'cloner',ip:'10.44.9.61',online:false}];
  const {run}=await loaded({'/monitor/machines':many});
  const html=plain(run('networkPage(0)')); const boxes=foBoxes(html);
  const line=(re)=>{ for(const b of boxes) for(const l of b.lines) if(re.test(l.text)) return l; assert.fail('no line matching '+re); };
  const allowed=line(/^bind: TFTP 69 · HTTP 8080\/8081$/);
  assert.equal(allowed.cls,'m w2','may wrap to two lines'); assert.equal(allowed.title,'מותר (לפי bind): TFTP 69 · HTTP 8080 · HTTP 8081');
  assert.doesNotMatch(html,/>מותר \(לפי bind\)/,'the long prefix is tooltip-only');
  assert.equal(run('netAllowedShort(["SSH 22","HTTP 8082","HTTP 8080","DHCP 67"])'),'SSH 22 · HTTP 8082/8080 · DHCP 67','the lab line from the screenshot');
  const cl=line(/מתוך 4 מחוברים$/);
  assert.equal(cl.text,'10.44.9.118 · 10.44.9.59 · +2 · 2 מתוך 4 מחוברים'); assert.match(run('netAddrList(["1.1.1.1","2.2.2.2","3.3.3.3"])'),/⁦\+1⁩$/,'"+1" isolated, not mirrored to "1+"'); assert.equal(cl.title,'10.44.9.118 · 10.44.9.59 · 10.44.9.60 · 10.44.9.61 · 2 מתוך 4 מחוברים');
  assert.equal(line(/^משכפלים — /).text,'משכפלים — מחשב 1, מחשב 2, מחשב 3, מחשב 4','names stay (ellipsis + title when the box is narrower)');
  assert.equal(run('netAddrList([])'),'ללא כתובת'); assert.equal(run('netAddrList(["10.0.0.1"])'),'⁦10.0.0.1⁩'); assert.equal(run('netAddrList(["1.1.1.1","2.2.2.2","3.3.3.3"])'),'⁦1.1.1.1⁩ · ⁦2.2.2.2⁩ · ⁦+1⁩','each address isolated on its own — " · " is not ASCII');
});

test('without bind on any /ports row the VLAN says "דורש API (#705)" — never a guessed policy',async()=>{
  const {run}=await loaded({'/ports':PORTS_NO_BIND});
  assert.equal(run('netServicesOn(NETW.nics[1])'),null);
  const html=run('networkPage(0)');
  assert.match(html,/מה מותר — דורש API \(#705\)/); assert.doesNotMatch(html,/מותר \(לפי bind\)/);
  assert.match(run('networkPage(1)'),/title="דורש API \(#705\): מודל וילן \/ bind">— דורש API/);
});

test('bind as the server sends it — a list of "addr/bits:port", empty list = not read — is normalised; all lists empty = דורש API',async()=>{
  const server=[port('tftp','TFTP','69','udp','ok','',{bind:[]}),port('http_console','HTTP','8081','tcp','ok','',{bind:['10.44.10.1/24:8081']}),
    port('ssh_server:ens18','SSH לשרת — ens18','22','tcp','ok','',{bind:['10.44.10.1/24:22']}),port('multicast','Multicast','9000–9001','udp','off','',{bind:['[::]:9000']})];
  const {run}=await loaded({'/ports':server});
  assert.equal(run('JSON.stringify(netServicesOn(NETW.nics[0]))'),JSON.stringify(['HTTP 8081','SSH לשרת — ens18 22','Multicast 9000–9001']));
  assert.equal(run('JSON.stringify(netServicesOn(NETW.nics[1]))'),JSON.stringify(['Multicast 9000–9001']),'only the wildcard');
  const t=await loaded({'/ports':server.map(p=>({...p,bind:[]}))});
  assert.equal(t.run('netServicesOn(NETW.nics[0])'),null,'every bind empty = nothing was read, not "nothing listens"');
  assert.match(t.run('networkPage(0)'),/מה מותר — דורש API \(#705\)/);
});

test('empty and unread are messages, not an empty drawing: no NICs → empty state; /net/interfaces failed → red note; other sources failed → their own notes',async()=>{
  let t=await loaded({'/net/interfaces':[]});
  let html=t.run('networkPage(0)'); assert.doesNotMatch(html,/<svg class="netdiag-svg"/); assert.match(html,/class="empty">לא נמצאו כרטיסי רשת/);
  t=await loaded({'/net/interfaces':new Error('ip link: לא רץ')});
  assert.equal(t.run('NETW.nics'),null);
  html=t.run('networkPage(0)'); assert.doesNotMatch(html,/<svg class="netdiag-svg"/); assert.match(html,/class="note err".*לא הצלחתי לקרוא את כרטיסי הרשת: ip link: לא רץ — אין מה לצייר/); assert.match(html,/class="pill err">לא נקרא</);
  assert.match(t.run('networkPage(1)'),/לא הצלחתי לקרוא את כרטיסי הרשת/); assert.match(t.run('networkPage(2)'),/לא הצלחתי לקרוא את כרטיסי הרשת/);
  t=await loaded({'/net':new Error('db locked'),'/ports':new Error('ss חסר'),'/monitor/machines':403,'/storage-nodes':409});
  html=t.run('networkPage(0)'); balanced(html);
  assert.match(html,/\/net לא נקרא: db locked/); assert.match(html,/\/ports לא נקרא: ss חסר/); assert.match(html,/\/monitor\/machines לא נקרא: admin only/);
  assert.match(html,/לא רשומים: לא נקרא/); assert.match(html,/אין סניפים/,'409 = no secondaries, not an error');
  assert.match(html,/<svg class="netdiag-svg"/,'NICs were read — the drawing stays, without the unread clients');
  assert.match(html,/מה מותר — דורש API \(#705\)/);
});

/* ---------- לשונית 1: חיבורים פיזיים ---------- */

test('NICs table: actual / configured / gap side by side, DHCP live + stored, role from bind, SSH switch per NIC, actions cell in every row (#1032)',async()=>{
  const {run}=await loaded();
  const html=run('networkPage(1)'); balanced(html);
  assert.match(html,/<table class="dg stable"><thead><tr><th>כרטיס<\/th><th>קישור<\/th><th>בפועל<\/th><th>מוגדר<\/th><th>פער<\/th><th>DHCP<\/th><th>תפקיד<\/th><th>SSH לשרת<\/th><th><\/th>/);
  const r18=row(html,'data-nic="ens18"'), r19=row(html,'data-nic="ens19"'), r20=row(html,'data-nic="ens20"'), r21=row(html,'data-nic="ens21"');
  for(const r of [r18,r19,r20,r21]) { assert.match(r,/<div class="acts"><button class="btn sm" onclick="event\.stopPropagation\(\);netEditAddress\('ens\d\d'\)">כתובת<\/button><button class="btn sm" onclick="event\.stopPropagation\(\);netEditDhcp\('ens\d\d'\)">DHCP<\/button><\/div>/); }
  assert.match(r19,/^<tr data-nic="ens19" class="sel" onclick="netSelect\('ens19'\)" tabindex="0" aria-selected="true">/);
  assert.match(r18,/class="st ok">מחובר · 1000 Mbps</); assert.match(r18,/class="mono">10\.44\.10\.1\/24</); assert.match(r18,/static 10\.44\.10\.1\/24 · gw 10\.44\.10\.254/); assert.match(r18,/class="st ok">תואם</);
  assert.match(r18,/class="st ">כבוי<\/span><span class="sub">שמור: כבוי</); assert.match(r18,/<td>TFTP 69 · HTTP 8080 · HTTP 8081<\/td>/);
  assert.match(r18,/class="sw on" role="switch" aria-checked="true" aria-label="SSH לשרת ens18" onclick="portSwitch\('ssh_nic:ens18'\)"><\/button><span class="cap">🔒 פתוח · מאזין/,'last open door → typing the name');
  assert.match(r19,/class="st ok">משרת<\/span><span class="sub">שמור: מופעל</,'the range lives in the deploy tab and the selected card, not in the 9-column table'); assert.match(r19,/<td>הפצה · DHCP · TFTP 69 · HTTP 8080 · Multicast 9000–9001<\/td>/);
  assert.match(r19,/class="sw " role="switch" aria-checked="false".*🔒 סגור/,'opening = typing the name');
  assert.match(r20,/class="st err">כתובת: מוגדר 10\.44\.11\.1\/24, בפועל 10\.44\.11\.2\/24</,'gap is a state, red'); assert.match(r20,/class="mono">10\.44\.11\.2\/24</); assert.match(r20,/class="pill warn">רשת המכללה</);
  assert.match(r20,/class="st ok">proxy פעיל</); assert.match(r20,/class="sw unk" role="switch" aria-checked="mixed".*סגור · לא נקרא/,'listening null → unknown, not off');
  assert.match(r21,/class="st ">מנותק</); assert.match(r21,/class="mono">—</); assert.match(r21,/class="mono">לא מנוהל</); assert.match(r21,/class="st ">לא מנוהל</); assert.match(r21,/aria-checked="mixed".*לא ברשימת \/ssh/);
  // כותרת + תג
  assert.match(html,/4 כרטיסים · 3 מוגדרים · מצב בפועל נקרא \d\d:\d\d · נתיבים: default via 10\.44\.10\.254 · 10\.44\.13\.0\/24 via 10\.44\.10\.254 · DNS 10\.44\.10\.2/);
  assert.match(html,/class="pill err">1 לא תואמים</); assert.match(html,/onclick="addNic\(\)">\+ כרטיס/); assert.match(html,/onclick="addRoute\(\)">\+ נתיב סטטי/);
  // הכרטיס הנבחר: מפתח-ערך, החזרה לפי השרת (60 שניות, לא מספר קבוע), פעולות משניות
  const sel=card(html,'ens19 — הפצה');
  assert.match(sel,/10\.44\.9\.10\/24<\/span> <span class="cap">\(נקרא \d\d:\d\d\)/); assert.match(sel,/חלון חזרה של 60 שניות/); assert.match(sel,/imagectl-netrollback\.timer/);
  assert.match(sel,/onclick="netShowFile\('ens19'\)">תצוגה מקדימה של הקובץ/); assert.match(sel,/onclick="netForget\('ens19'\)">שכחה/); assert.match(sel,/onclick="netDescribe\('ens19'\)">עריכה/);
  // נתיבים סטטיים: ראיה חיובית מטבלת הניתוב, מחיקה דרך ה-sheet של netcfg.js
  const routes=card(html,'נתיבים סטטיים');
  assert.match(routes,/class="st ok"><\/span><span class="mono">10\.44\.13\.0\/24<\/span><\/td><td><span class="mono">10\.44\.10\.254<\/span><\/td><td><span class="mono">ens18</);
  assert.match(routes,/onclick="routeDeleteSheet\('ens18',0\)">מחק/);
  run("routeDeleteSheet('ens18',0)"); assert.equal(run('sheets.length'),1); assert.equal(run('sheets[0].verify.mustEqual'),'ens18'); assert.equal(run('sheets[0].danger'),true);
});

test('NICs: live state not read → "לא נקרא" everywhere (never green), interfaces.d not sourced → red note, rollback pending → the banner',async()=>{
  const cfg=JSON.parse(JSON.stringify(NETCFG)); cfg.live={checked:false,reason:'ip addr: לא רץ',routes:[],nameservers:[]}; cfg.sourced=false;
  cfg.rollback={...cfg.rollback,pending:true,interface:'ens18',seconds_left:42};
  for(const r of cfg.interfaces) r.mismatches=['המצב בפועל לא נקרא'];
  const {run}=await loaded({'/net/config':cfg});
  const html=run('networkPage(1)'); balanced(html);
  const r18=row(html,'data-nic="ens18"');
  assert.match(r18,/<td><span class="st unk">לא נקרא<\/span><\/td><td><span class="mono">static 10\.44\.10\.1\/24 · gw 10\.44\.10\.254<\/span><\/td><td><span class="st unk">לא נקרא<\/span><\/td>/);
  assert.doesNotMatch(html,/class="st ok">תואם/); assert.match(html,/class="pill warn">לא מאומת</);
  assert.match(html,/מצב בפועל לא נקרא \(ip addr: לא רץ\)/); assert.match(html,/interfaces\.d — כל מה שנכתב שם לא ייקרא באתחול/);
  assert.match(html,/id="netcfg-pending"/); assert.match(html,/ההגדרה של ens18 ממתינה לאישור/); assert.match(html,/onclick="confirmNetRollback\(\)"/);
  assert.match(run('networkPage(0)'),/id="netcfg-pending"/,'the banner is above the diagram too');
  const routes=card(html,'נתיבים סטטיים'); assert.match(routes,/class="st unk"><\/span><span class="mono">10\.44\.13\.0\/24/);
  // ‏/net/config לא נקרא בכלל → אותו דבר, עם השם של המקור
  const t=await loaded({'/net/config':new Error('permission denied')});
  const h2=t.run('networkPage(1)'); assert.match(h2,/\/net\/config לא נקרא: permission denied/); assert.match(row(h2,'data-nic="ens18"'),/class="mono">לא נקרא<\/span><\/td><td><span class="st unk">לא נקרא</);
  assert.match(card(h2,'נתיבים סטטיים'),/\/net\/config לא נקרא: permission denied/);
});

test('the DHCP and address edits are the existing net.js/netcfg.js sheets (typed NIC name, danger) — no new save flow (#53)',async()=>{
  const {run,requests}=await loaded();
  run("netEditDhcp('ens19')");
  assert.equal(run('sheets.length'),1); assert.equal(run('sheets[0].title'),'DHCP על ens19'); assert.equal(run('sheets[0].danger'),true); assert.equal(run('sheets[0].verify.mustEqual'),'ens19');
  run("netEditAddress('ens20')");
  assert.equal(run('sheets[1].title'),'עריכת הגדרות — ens20'); assert.equal(run('sheets[1].danger'),true); assert.equal(run('sheets[1].verify.mustEqual'),'ens20');
  run("netEditAddress('ens21')"); assert.equal(run('sheets[2].title'),'עריכת הגדרות — ens21','manual NIC: the form opens with an empty static row');
  run("netForget('ens21')"); assert.match(run('sheets[3].title'),/הסרת הגדרות הכרטיס/);
  // SSH לשרת — אותו endpoint של גל 5 דרך portSshNic (המתגים נרשמים בציור הטבלה)
  run('networkPage(1)'); run("portSwitch('ssh_nic:ens19')"); assert.match(run('sheets[4].title'),/פתיחת SSH לשרת על ens19/); assert.equal(run('sheets[4].verify.mustEqual'),'ens19');
  assert.ok(!requests.some(r=>r.method==='PUT'),'nothing is written without the sheet being submitted');
});

/* ---------- לשונית 2: רשת הפצה ---------- */

test('deploy network: header from the DHCP NIC, "what the server hands out" says stored-vs-live openly, seen table with "רשום" as the primary action of an unregistered row',async()=>{
  const {run}=await loaded();
  const html=run('networkPage(2)'); balanced(html);
  assert.match(html,/class="obj-name">רשת הפצה — DHCP</);
  assert.match(html,/ens19 · 10\.44\.9\.0\/24 · dnsmasq: משרת · 3 נראו ברשת ההפצה · מי עוד עונה: לא נבדק/); assert.match(html,/class="pill ok">משרת</);
  assert.match(html,/<button class="btn primary" onclick="netEditDhcp\('ens19'\)">עריכת DHCP<\/button><button class="btn" onclick="netProbe\('ens19'\)">בדוק מי עונה<\/button><button class="btn" onclick="previewDnsmasq\(\)/);
  const serves=card(html,'מה השרת מחלק');
  assert.match(serves,/10\.44\.9\.100 – 10\.44\.9\.200/); assert.match(serves,/255\.255\.255\.0/); assert.match(serves,/class="st ok">מופעל — תואם למצב הפעיל</); assert.match(serves,/class="st ">לא נבדק</);
  const seen=card(html,'מי קיבל כתובת');
  assert.match(seen,/<table class="dg stable"><thead><tr><th>מכונה<\/th><th>MAC<\/th><th>IP<\/th><th>נראה<\/th><th>שלב אתחול<\/th><th><\/th>/);
  const r1=row(seen,'data-mac="'+C1+'"'), rb=row(seen,'data-mac="'+B1+'"'), ru=row(seen,'data-mac="'+UNREG+'"');
  assert.match(r1,/class="name">מחשב 1<\/span><span class="sub">מחשב שיכפול · מחשבי שיכפול/); assert.match(r1,/class="st ok">מחובר</); assert.match(r1,/<b>hello · בסוכן<\/b>/);
  assert.match(r1,/<div class="acts"><button class="btn sm" onclick="openMachineDetail\('a0%3A48%3A1c%3A8a%3A18%3A40'\)">פרטים<\/button><\/div>/);
  assert.match(rb,/class="st ">לפני 3 שע'</); assert.match(rb,/<td><span style="color:var\(--muted\)">—<\/span><\/td>/,'no boot trail = dash, not a stage');
  assert.match(ru,/^<tr data-mac="de:ad:be:ef:00:01" class="unreg">/); assert.match(ru,/class="pill warn">לא רשום</); assert.match(ru,/GRUB → דיסק מקומי/);
  assert.match(ru,/<div class="acts on"><button class="btn sm primary" onclick="netRegister\('de%3Aad%3Abe%3Aef%3A00%3A01'\)">רשום<\/button><button class="btn sm" onclick="netDeviceDescribe\(/); assert.match(ru,/netDeviceForget\(/);
  run("netRegister('de%3Aad%3Abe%3Aef%3A00%3A01')"); assert.equal(run('JSON.stringify(globalThis.addMachine)'),JSON.stringify({mac:UNREG}));
  const others=card(html,'כרטיסים אחרים');
  for(const n of ['ens18','ens20','ens21']) assert.match(others,new RegExp('class="name">'+n+'<'));
  assert.doesNotMatch(others,/class="name">ens19</);
  assert.match(row(others,'class="name">ens18<'),/onclick="netEditDhcp\('ens18'\)">הגדר כרשת הפצה/); assert.match(row(others,'class="name">ens20<'),/onclick="netEditDhcp\('ens20'\)">עריכה/);
  assert.match(row(others,'class="name">ens18<'),/onclick="netProbe\('ens18'\)">בדוק/);
});

test('deploy network: stored ≠ live (dhcp_diverged) is orange, live not read is "לא נקרא", no DHCP NIC at all → "הגדר כרשת הפצה" on a non-trunk NIC',async()=>{
  const div=NICS.map(n=>n.name==='ens19'?{...n,dhcp_diverged:true,dhcp_live:{state:'configured_not_running',checked:true,detail:'dnsmasq inactive'},dhcp_live_label:'מוגדר, השירות אינו פועל'}:n);
  let t=await loaded({'/net/interfaces':div}); let html=t.run('networkPage(2)');
  assert.match(html,/class="pill warn">לא תואם למצב הפעיל</); assert.match(card(html,'מה השרת מחלק'),/class="st warn">מופעל — לא תואם למצב הפעיל</);
  const unk=NICS.map(n=>n.name==='ens19'?{...n,dhcp_live:{state:'unknown',checked:false,detail:'לא ניתן לקרוא את מצב ה-DHCP הפעיל'},dhcp_live_label:'לא ידוע'}:n);
  t=await loaded({'/net/interfaces':unk}); html=t.run('networkPage(2)');
  assert.match(html,/class="pill err">לא נקרא</); assert.match(card(html,'מה השרת מחלק'),/class="st unk">מופעל — המצב הפעיל לא נקרא</); assert.doesNotMatch(card(html,'מה השרת מחלק'),/st ok/);
  const none=NICS.map(n=>({...n,enabled:false,proxy:false,dhcp_live:{state:'off',checked:true,detail:''},dhcp_live_label:'כבוי'}));
  t=await loaded({'/net/interfaces':none}); html=t.run('networkPage(2)');
  assert.match(html,/אין כרטיס מוגדר כרשת הפצה, ואין כרטיס שמשרת DHCP כרגע/); assert.match(html,/class="pill ">כבוי</);
  assert.match(html,/<button class="btn primary" onclick="netEditDhcp\('ens18'\)">הגדר כרשת הפצה</,'first present non-trunk NIC'); assert.doesNotMatch(html,/בדוק מי עונה/);
  assert.match(card(html,'מה השרת מחלק'),/class="empty">אין כרטיס מוגדר כרשת הפצה/);
  assert.match(t.run('networkPage(0)'),/DHCP הפצה: כבוי · 1 לא רשומים/,'diagram header still counts');
  // ‏/net לא נקרא → "מי קיבל כתובת" הוא הודעה, ולא טבלה ריקה
  t=await loaded({'/net':new Error('db locked')}); html=t.run('networkPage(2)');
  assert.match(html,/נראו ברשת: לא נקרא/); assert.match(card(html,'מי קיבל כתובת'),/class="note err".*\/net לא נקרא: db locked/); assert.doesNotMatch(card(html,'מי קיבל כתובת'),/<table/);
  t=await loaded({'/net':[]}); html=t.run('networkPage(2)');
  assert.match(html,/0 נראו ברשת ההפצה/); assert.match(card(html,'מי קיבל כתובת'),/class="empty">אף מכונה עוד לא דיברה עם השרת/);
});

test('probe ("מי עוד עונה"): checked=false is "the check did not run", not "nobody"; servers found is red; a failed request is a toast + unknown',async()=>{
  const {run,requests}=await loaded();
  await run("netProbe('ens19')");
  assert.deepEqual(requests.filter(r=>r.url.endsWith('/probe')).map(r=>[r.method,r.url]),[['GET','/net/interfaces/ens19/probe']]);
  let html=run('networkPage(2)');
  assert.match(html,/מי עוד עונה: אף שרת אחר לא עונה \(נבדק \d\d:\d\d\)/); assert.match(card(html,'מה השרת מחלק'),/class="st ok">אף שרת אחר לא עונה \(נבדק \d\d:\d\d\)</);
  await run("netProbe('ens18')"); await run("netProbe('ens20')");
  html=run('networkPage(2)'); const others=card(html,'כרטיסים אחרים');
  assert.match(row(others,'class="name">ens18<'),/class="st unk">הבדיקה לא רצה \(\d\d:\d\d\) — לא ידוע מי עונה</);
  assert.match(row(others,'class="name">ens20<'),/class="st err">עונים: 10\.44\.11\.254 \(נבדק \d\d:\d\d\)</);
  assert.match(row(others,'class="name">ens21<'),/class="st ">לא נבדק</);
  const t=await loaded({'/net/interfaces/ens19/probe':new Error('timeout')});
  await t.run("netProbe('ens19')");
  assert.equal(t.run('JSON.stringify(toasts)'),JSON.stringify(['בדיקת DHCP נכשלה: timeout'])); assert.match(t.run('networkPage(2)'),/class="st unk">הבדיקה נכשלה \(\d\d:\d\d\): timeout</);
  assert.match(t.run('networkPage(0)'),/הבדיקה נכשלה/,'the selected-NIC card in the diagram shows the same result');
});

/* 18/09 — מודל האתרים (הכרעת נדב, interfaces.md §18 "חוקי חומת האש בין האתרים"): לכל אתר שלושה וילנים — שרתים / הפצה / כיתות,
   ואין וילן "ניהול" נפרד. המשני יושב מאחורי FW משלו, הראשי יוזם ורק 8443 פתוח ביניהם — ולכן הוא לא לקוח על הוילן אלא "מעבר ל-FW". */
test('site model: the console/8443 VLAN is "שרתים", and a secondary is "דרך FW · 8443 בלבד" on a dashed line even when its box is red/green',async()=>{
  const {run}=await loaded();
  const model=JSON.parse(plain(run('JSON.stringify(netDiagramModel())')));
  const lane=(name)=>model.lanes.find(l=>l.nic.name===name);
  assert.equal(lane('ens18').vlan.label,'שרתים — קונסולה');
  // כרטיס שנושא רק 8443 (bind של interserver על כתובתו) — עדיין וילן השרתים, לא וילן משלו
  run('NETW.ports=NETW.ports.map(p=>p.id==="interserver"?{...p,bind:"10.44.13.1"}:p)');
  assert.equal(run('JSON.stringify(netVlanOf({enabled:false,proxy:false,trunk:false,addresses:["10.44.13.1/24"],name:"ens22"}))'),JSON.stringify({kind:'inter',label:'שרתים — בין-שרתי'}));
  const [console_,haifa,old]=lane('ens18').clients;
  assert.equal(console_.kind,'console'); assert.ok(!console_.viaFw,'the console is a client on the VLAN, not behind a FW');
  assert.equal(haifa.sub,'דרך FW · 8443 בלבד · https://10.44.13.5:8443 · לא ענה: timeout');
  assert.equal(haifa.tip,'המשני מאחורי FW משלו; הראשי יוזם, רק 8443 · https://10.44.13.5:8443 · לא ענה: timeout');
  assert.equal(old.sub,'דרך FW · 8443 בלבד · https://10.44.10.77:8443 · מושבת');
  assert.equal(haifa.viaFw,true); assert.equal(old.viaFw,true); assert.equal(haifa.dashed,false,'the box stays solid — connected is measured; the line is what marks the FW');
  const html=plain(run('networkPage(0)')); balanced(html);
  assert.match(html,/שרתים — קונסולה — לפי הגדרה/); assert.doesNotMatch(html,/ניהול — קונסולה|>בין-שרתים —/);
  const lines=[...html.matchAll(/<path class="ln ([a-z]*)" d="M470 [\d.]+ H430 V[\d.]+ H390"( stroke-dasharray="4 4")?\/>/g)].map(m=>[m[1],!!m[2]]);
  assert.deepEqual(lines.filter(l=>l[1]),[['err',true],['off',true]],'exactly the two secondaries are dashed lines: red (not answering) and grey (disabled)');
  assert.ok(lines.some(l=>l[0]==='ok'&&!l[1]),'the console line is solid');
  assert.match(html,/title="המשני מאחורי FW משלו; הראשי יוזם, רק 8443 · https:\/\/10\.44\.13\.5:8443 · לא ענה: timeout">דרך FW · 8443 בלבד · https:\/\/10\.44\.13\.5:8443 · לא ענה: timeout</);
});
