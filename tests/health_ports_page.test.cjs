// ‏#954 גל 5: בריאות ושירותים (health.md) + רשת › פורטים (network-ports.md).
// בלי דפדפן: console.js + net.js + progress.js רצים ב-vm מול DOM מזערי ו-fetch מזויף.
// עיקרון 5: "לא נבדק" (unknown) ≠ "כבוי" (off) ≠ "לא מאזין" (bad) — שלושה צבעים;
// כל endpoint שלא נקרא הוא מצב משלו, לא "אין".
// הכרעות נדב 17/09: 06:12 המתגים בפורטים ולא בבריאות; 06:25 מתג בכל שורה, 🔒 = הקלדת שם.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');

const MAC1='a0:48:1c:8a:18:40', MAC2='78:ac:c0:9b:11:c2';
const check=(id,label,state,detail)=>({id,label,state,detail});
const HEALTH=[
  check('server','השרת בכתובת ההפצה','ok','http://10.44.9.10:8080 עונה על תפריט האתחול'),
  check('udp_sender','מולטיקאסט (udp-sender)','warn','udp-sender לא נמצא ב-PATH'),
  check('tftp_port','פורט 69 (TFTP)','bad','אף אחד לא מגיש TFTP — מחשבים לא יעלו ב-PXE'),
  check('ssh_stations','SSH בתחנות','off','imagectl.debug לא בשורת הקרנל'),
  check('shim_fresh','עדכניות ה-shim','unknown','לא ניתן לקרוא /usr/lib/shim — לא נבדק'),
  check('agent_loops','מחשבים שנופלים לסוכן','bad','2 מחשבים בעשר הדקות האחרונות'),
  check('agent_loop:'+MAC1,'מחשבי שיכפול מחשב 1','bad','3 פעמים · נראה לאחרונה לפני 2 דק\''),
  check('agent_loop:'+MAC2,MAC2,'bad','2 פעמים · נראה לאחרונה לפני 4 דק\''),
  check('off_vlan','מחשבים שפונים מרשת אחרת','ok','אף פנייה מרשת שאינה 10.44.9.0/24'),
];
const port=(id,name,p,proto,state,detail,extra={})=>({id,name,port:p,proto,desc:'desc '+id,target:'target '+id,state,detail,note:'לפתוח ב-FW: '+p,...extra});
const PORTS_OLD=[
  port('tftp','TFTP','69','udp','ok','dnsmasq מגיש'),
  port('http_boot','HTTP','8080','tcp','ok','עונה על תפריט האתחול'),
  port('http_console','HTTP','8081','tcp','off','אין hook שקורא את ההאזנה על הפורט הזה — לא אומת'),
  port('pxe_proxy','PXE','4011','udp','off','אין hook — לא אומת'),
  port('multicast','Multicast','9000–9001','udp','off','אין udp-sender חי — ואין סבב'),
  port('monitor','Monitor (RFB)','5900','tcp','off','המתג monitor:stations כבוי'),
  port('kiosk','HTTP','8082','tcp','off','אין hook — לא אומת'),
  port('ssh_stations','SSH','22','tcp','ok','שורת הקרנל אינה נושאת imagectl.debug'),
];
// החוזה של #996: enabled / bind / toggle / off_means + שורות חדשות (8443, טווח דינמי)
const PORTS_NEW=[
  port('tftp','TFTP','69','udp','ok','dnsmasq מגיש',{enabled:true,bind:'0.0.0.0',toggle:'confirm',off_means:'התחנות לא מקבלות shim/GRUB — עולות מהדיסק המקומי'}),
  port('http_boot','HTTP','8080','tcp','ok','uvicorn',{enabled:true,bind:'0.0.0.0',toggle:'confirm',off_means:'אין PXE ואין hello — כל סבב פעיל נכשל בגלוי'}),
  port('http_console','HTTP','8081','tcp','ok','uvicorn',{enabled:true,bind:'10.44.10.1',toggle:'confirm',off_means:'אתה ננעל בחוץ'}),
  port('pxe_proxy','PXE','4011','udp','off','אין hook — לא אומת'),
  port('multicast','Multicast','9000–9001','udp','off','אין סבב רץ',{enabled:true,bind:'10.44.9.10',toggle:'none',off_means:'סבב לא יכול להתחיל לשדר'}),
  port('monitor','Monitor (RFB)','5900','tcp','off','המתג monitor:stations כבוי'),
  port('kiosk','HTTP','8082','tcp','ok','uvicorn',{enabled:true,bind:'0.0.0.0',toggle:'api',off_means:'מסך התחנה לא נטען; הסוכן ממשיך לעבוד'}),
  port('interserver','mTLS','8443','tcp','off','כבוי — שרת ראשי, לא משני',{enabled:false,bind:'',toggle:'api',off_means:'משני לא יכול להירשם או לקבל אימג\'ים'}),
  port('dynamic','UDP','9002–9099','udp','unknown','לא נקרא — ss חסר',{enabled:true,bind:'',toggle:'none',off_means:'שחזור תחנה בודדת ממסך התחנה נכשל'}),
  port('ssh_stations','SSH','22','tcp','ok','שורת הקרנל אינה נושאת imagectl.debug'),
];
const SSH={stations:{enabled:false,evidence:'closed',detail:'שורת הקרנל אינה נושאת imagectl.debug',confirm_word:'imagectl.debug'},
  listeners:{checked:true,addresses:['10.44.10.1'],wildcard:false,reason:'',port:22},stray:[],
  interfaces:[{name:'ens18',state:'up',addresses:['10.44.10.1/24'],enabled:true,listening:true},
              {name:'ens19',state:'up',addresses:['10.44.9.10/24'],enabled:false,listening:false},
              {name:'ens20',state:'up',addresses:['10.44.11.1/24'],enabled:false,listening:null}]};
const nic=(name,extra={})=>({name,mac:'00:11:22:33:44:'+name.slice(-2),state:'up',present:true,addresses:['10.0.0.1/24'],enabled:false,proxy:false,trunk:false,
  range_start:'',range_end:'',netmask:'255.255.255.0',gateway:'',dns:[],lease:'12h',server_ip:'',description:'',
  dhcp_live:{state:'off'},dhcp_live_label:'כבוי',dhcp_diverged:false,...extra});
const NICS=[nic('ens18'),
  nic('ens19',{enabled:true,range_start:'10.44.9.100',range_end:'10.44.9.200',server_ip:'10.44.9.10',dhcp_live:{state:'serving'},dhcp_live_label:'משרת'}),
  nic('ens20',{proxy:true,dhcp_live:{state:'serving'},dhcp_live_label:'proxy פעיל'})];

function setup(over={}) {
  const nodes=new Map(), requests=[];
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',hidden:false,dataset:{},style:{removeProperty(){}},
      classList:{add(){},remove(){},toggle(){}},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.innerHTML+=html;},setAttribute(){},removeAttribute(){},hasAttribute(){return false;},getAttribute(){return null;},closest(){return {style:{}};},focus(){}});
    return nodes.get(key);
  }
  const fixtures={'/health':HEALTH,'/update':{current:'v0.34.0',enabled:true,previous:'v0.33.0',server_name:'srv'},'/update/status':{state:'idle'},
    '/update/check':{current:'v0.34.0',latest:'v0.35.0',available:true,reason:''},
    '/ports':PORTS_OLD,'/ssh':SSH,'/monitor/settings':{port:5900,enabled:false},'/net/interfaces':NICS,'/net/proxy-support':{read:true,version:'2.90',verified:false,broken:true,reason:'dnsmasq 2.90 — proxy שבור'},
    '/machines':[{mac:MAC1,suffix:'מחשב 1',group_id:'grp_CLONERS',disks:null}],'/groups':[{id:'grp_CLONERS',label:'מחשבי שיכפול',role:'cloner',sort:1}],
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
      return {status:200,ok:true,headers:{get:()=>null},json:async()=>f ?? {ok:true,verified:true,apply_error:null}};
    }});
  for(const f of ['progress.js','console.js','net.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={username:"admin",role:"admin",server_name:"srv",version:"v0.34.0",capabilities:{}}; globalThis.sheets=[]; globalThis.toasts=[]; sheet = o => { sheets.push(o); }; toast=(m)=>{ toasts.push(m); }; renderCurrent=()=>{ globalThis.rendered=(globalThis.rendered||0)+1; }; globalThis.selected=[]; selectPageById=(id)=>{ selected.push(id); current=id; }; openMachineDetail=(m)=>{ globalThis.openedMac=m; };');
  run('MACHINES='+JSON.stringify(fixtures['/machines'])+'; GROUPS='+JSON.stringify(fixtures['/groups'])+';');
  return {run,requests,fixtures,ctx};
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
const cell=(html,needle,span=700)=>{const i=html.indexOf(needle); assert.ok(i>=0,'missing: '+needle); return html.slice(i,i+span);};
const row=(html,needle)=>{const i=html.indexOf(needle); assert.ok(i>=0,'missing row: '+needle); const s=html.lastIndexOf('<tr',i); return html.slice(s,html.indexOf('</tr>',i)+5);};

/* ---------- בריאות ושירותים ---------- */

test('health is an own page without tabs; ports is an own page; loaders wired',()=>{
  const {run}=setup();
  assert.equal(run('pages.health.own'),true); assert.equal(run('pages.health.tabs.length'),0);
  assert.equal(run('pages.health.load===loadHealth'),true);
  assert.equal(run('pages.ports.own'),true); assert.equal(run('pages.ports.load===loadPorts'),true);
  assert.equal(run('typeof loadSsh'),'undefined','loadSsh moved into the ports page');
  assert.equal(run('health()'),run('pagePlaceholder()'),'placeholder before the first read');
});

test('the checks table shows all five states with their own colour — "not checked" is never green, "off" is grey',async()=>{
  const {run}=setup(); run('current="health"');
  await run('loadHealth()');
  const html=run('health()'); balanced(html);
  assert.match(html,/<table class="dg">/); assert.match(html,/<th>בדיקה<\/th><th>מצב<\/th><th>מה נמצא<\/th>/);
  assert.match(row(html,'השרת בכתובת ההפצה'),/class="st ok">תקין</);
  assert.match(row(html,'מולטיקאסט (udp-sender)'),/class="st warn">אזהרה</);
  assert.match(row(html,'פורט 69 (TFTP)'),/class="st err">תקלה</);
  assert.match(row(html,'SSH בתחנות'),/class="st ">כבוי</);
  const unk=row(html,'עדכניות ה-shim');
  assert.match(unk,/class="st unk">לא נבדק</); assert.doesNotMatch(unk,/st ok/);
  assert.equal(run('healthStatusClass("unknown")'),'unk'); assert.equal(run('healthStatusLabel("unknown")'),'לא נבדק');
  // מה נמצא — ה-detail של השרת כלשונו
  assert.match(html,/אף אחד לא מגיש TFTP — מחשבים לא יעלו ב-PXE/);
  // הכותרת: מונים ותג
  assert.match(html,/9 בדיקות · נקרא \d\d:\d\d · 2 תקינות · 1 אזהרות · 4 תקלות · 1 כבויות · 1 לא נבדקו/);
  assert.match(html,/class="pill err">תקלה</);
  assert.match(html,/onclick="loadHealth\(\)">בדוק עכשיו</);
  // בלי מתגים ובלי הלשוניות הישנות
  assert.doesNotMatch(html,/ssh-body|class="switch|role="switch"/);
  assert.doesNotMatch(html,/role="tablist"/);
});

test('per-machine loop rows are grouped under "לולאות אתחול" right after the summary row, with a details action for a registered machine',async()=>{
  const {run}=setup(); run('current="health"');
  await run('loadHealth()');
  const html=run('health()');
  const order=['מחשבים שנופלים לסוכן','לולאות אתחול · 2','מחשבי שיכפול מחשב 1',MAC2,'מחשבים שפונים מרשת אחרת'].map((s)=>html.indexOf(s));
  assert.ok(order.every((v,i)=>v>=0 && (i===0 || v>order[i-1])),'order: '+order.join(','));
  assert.match(html,/<tr class="group"><td colspan="4">לולאות אתחול · 2<\/td><\/tr>/);
  assert.match(row(html,'מחשבי שיכפול מחשב 1'),/grp-kid/);
  assert.match(row(html,'מחשבי שיכפול מחשב 1'),new RegExp("openMachineDetail\\('"+encodeURIComponent(MAC1)+"'\\)"));
  assert.doesNotMatch(row(html,MAC2),/openMachineDetail/,'unregistered MAC has no drawer');
  assert.match(row(html,'פורט 69 (TFTP)'),/selectPageById\('ports'\)/,'a check points to the page where it is fixed');
});

test('health pill follows the worst state: warn-only, unknown-only, off-only, all ok, empty',async()=>{
  const pill=async(checks)=>{const {run}=setup({'/health':checks}); run('current="health"'); await run('loadHealth()'); return run('health()').match(/class="pill ([a-z]*)">([^<]*)</).slice(1,3);};
  assert.deepEqual(await pill([check('a','a','ok',''),check('b','b','warn','')]),['warn','אזהרה']);
  assert.deepEqual(await pill([check('a','a','ok',''),check('b','b','unknown','')]),['warn','לא נבדק']);
  assert.deepEqual(await pill([check('a','a','ok',''),check('b','b','off','')]),['','חלק כבוי']);
  assert.deepEqual(await pill([check('a','a','ok','')]),['ok','הכל תקין']);
  assert.deepEqual(await pill([]),['','אין בדיקות']);
});

test('/health that cannot be read is its own state — red note, "לא נקרא" pill, not a placeholder and not an empty table',async()=>{
  const {run}=setup({'/health':new Error('DB down')}); run('current="health"');
  await run('loadHealth()');
  const html=run('health()');
  assert.notEqual(html,run('pagePlaceholder()'));
  assert.match(html,/class="pill err">לא נקרא</); assert.match(html,/note err.*DB down/);
  assert.doesNotMatch(html,/אין בדיקות/);
  assert.match(run('toasts.join("|")'),/DB down/);
});

test('update card: installed/previous/last-check, check → apply button with typed server name, revert behind typed name',async()=>{
  const {run,requests}=setup(); run('current="health"');
  await run('loadHealth()');
  let html=run('health()');
  const card=cell(html,'גרסה ועדכון',1500);
  assert.match(card,/מותקן<\/span><span class="v"><span class="mono">v0\.34\.0</);
  assert.match(card,/קודם<\/span><span class="v"><span class="mono">v0\.33\.0<\/span> \(חזרה זמינה\)/);
  assert.match(card,/בדיקה אחרונה<\/span><span class="v">לא נבדק בסשן הזה/);
  assert.match(card,/onclick="healthUpdateCheck\(\)">בדוק עדכון</);
  assert.doesNotMatch(card,/החל עדכון/,'no apply before a check found something');
  assert.match(card,/confirmUpdateAction\('חזרה לגרסה הקודמת', UPDATE_INFO\.previous, 'revert', UPDATE_INFO\.previous\)">חזור ל-v0\.33\.0</);
  await run('healthUpdateCheck()');
  assert.ok(requests.some((r)=>r.url==='/update/check' && r.method==='POST'));
  html=run('health()');
  assert.match(cell(html,'גרסה ועדכון',1800),/בדיקה אחרונה<\/span><span class="v">\d\d:\d\d — יש עדכון: v0\.34\.0 → v0\.35\.0/);
  assert.match(html,/confirmUpdateAction\('עדכון שרת', UPDATE_INFO\.latest, 'apply', UPDATE_INFO\.latest\)">החל עדכון v0\.35\.0 \(הקלדת שם השרת\)</);
  run("confirmUpdateAction('עדכון שרת', UPDATE_INFO.latest, 'apply', UPDATE_INFO.latest)");
  const s=run('sheets.at(-1)');
  assert.equal(s.danger,true); assert.equal(s.verify.mustEqual,'srv'); assert.match(s.verify.label,/שם השרת/);
  await s.onSubmit();
  const apply=requests.find((r)=>r.url==='/update/apply');
  assert.deepEqual(apply.body,{confirm_name:'srv',tag:'v0.35.0'});
});

test('update card: switch off → note with a link to settings and no buttons; failed check ≠ "no update"; status line; /update unreadable',async()=>{
  let s=setup({'/update':{current:'v0.34.0',enabled:false,previous:null,server_name:'srv'}}); s.run('current="health"');
  await s.run('loadHealth()');
  let card=cell(s.run('health()'),'גרסה ועדכון',1500);
  assert.match(card,/note warn.*update_enabled.*selectPageById\('settings'\)/); assert.doesNotMatch(card,/<button/);
  assert.match(card,/קודם<\/span><span class="v">—/);
  assert.ok(!s.requests.some((r)=>r.url==='/update/status'),'status is not asked while the switch is off');
  s=setup({'/update/check':new Error('no route to github'),'/update/status':{state:'applying',tag:'v0.35.0',verified:false}}); s.run('current="health"');
  await s.run('loadHealth()'); await s.run('healthUpdateCheck()');
  card=cell(s.run('health()'),'גרסה ועדכון',1800);
  assert.match(card,/הבדיקה נכשלה: no route to github/); assert.doesNotMatch(card,/החל עדכון|אין חדש/);
  assert.match(card,/class="st run">העדכון ל-v0\.35\.0 הופעל — ממתין לאתחול השרת/);
  s=setup({'/update':new Error('403')}); s.run('current="health"');
  await s.run('loadHealth()');
  assert.match(cell(s.run('health()'),'גרסה ועדכון',600),/note err.*\/update לא נקרא: 403/);
});

/* ---------- רשת › פורטים ---------- */

test('ports page (old /ports contract): one table, a switch in every row, wired switches say "קיים", the rest say "דורש API" and explain on click',async()=>{
  const {run}=setup(); run('current="ports"');
  assert.equal(run('ports()'),run('pagePlaceholder()'));
  await run('loadPorts()');
  const html=run('ports()'); balanced(html);
  assert.match(html,/<th>שירות<\/th><th>פורט<\/th><th>מי מתחבר<\/th><th>על איזה כרטיס<\/th><th>מצב נמדד<\/th><th>מתג<\/th><th>מה קורה אם מכבים<\/th><th>API<\/th>/);
  const switches=[...html.matchAll(/<button type="button" class="sw ([a-z]*)" role="switch" aria-checked="(true|false|mixed)"/g)];
  // DHCP (ens19) + 8 שורות שרת + 3 SSH לשרת = 12, מתג בכל שורה
  assert.equal(switches.length,12); assert.equal((html.match(/<tr><td>/g)||[]).length,12);
  // TFTP: אין toggle בחוזה הישן → מתג "לא ידוע", "דורש API", לחיצה מסבירה
  const tftp=row(html,'>TFTP<');
  assert.match(tftp,/class="sw unk" role="switch" aria-checked="mixed"/); assert.match(tftp,/class="pill warn">דורש API</); assert.match(tftp,/דורש API \(#996\)/);
  assert.match(tftp,/69\/udp/); assert.match(tftp,/לפתוח ב-FW: 69/); assert.match(tftp,/title="דורש API \(#996\): כתובת ההאזנה">—/);
  run("portSwitch('tftp')");
  assert.match(run('toasts.at(-1)'),/מתג לפורט 69 דורש API \(#996\)/);
  assert.equal(run('sheets.length'),0,'no sheet for a switch that has no API');
  assert.doesNotMatch(html,/disabled/,'no disabled buttons (README §8)');
  // מוניטור: מתג כבוי, הדלקה מאחורי הקלדה
  const mon=row(html,'Monitor (RFB)');
  assert.match(mon,/class="sw " role="switch" aria-checked="false"/); assert.match(mon,/🔒 כבוי · הדלקה = הקלדת imagectl.monitor/);
  assert.match(mon,/class="pill ok">קיים<\/span> <span class="cap mono">PUT \/monitor\/settings/); assert.match(mon,/בתחנה, לא בשרת/);
  // DHCP מ-/net/interfaces: ens19 משרת → מתג דלוק, נעול, מצב נמדד ירוק עם הטווח
  const dhcp=row(html,'DHCP הפצה — ens19');
  assert.match(dhcp,/class="sw on" role="switch" aria-checked="true"/); assert.match(dhcp,/🔒 דלוק · כיבוי = הקלדת ens19/);
  assert.match(dhcp,/class="st ok">תקין<\/span><span class="sub">משרת · 10\.44\.9\.100–10\.44\.9\.200/);
  // PXE proxy: דלוק על ens20
  assert.match(row(html,'>PXE<'),/class="sw on".*דלוק על ens20/s);
  // סיכום בכותרת
  assert.match(html,/12 שורות · מצב האזנה כפי שנקרא מהשרת ב-\d\d:\d\d/);
  assert.match(html,/מאזינים בפורט 22: 10\.44\.10\.1/);
  // ‏#954 גל 8: הלשוניות הן של אובייקט הרשת — תרשים · חיבורים פיזיים · רשת הפצה · פורטים (הדף הזה, tab 3)
  assert.match(html,/role="tab" aria-selected="false" tabindex="-1" onclick="openNetwork\(0\)">תרשים/);
  assert.match(html,/role="tab" aria-selected="false" tabindex="-1" onclick="openNetwork\(1\)">חיבורים פיזיים/);
  assert.match(html,/role="tab" aria-selected="true" tabindex="0" onclick="activateTab\(3\)">פורטים/);
});

test('SSH to the server: a row per interface with three measured states (listening / closed / not read) and the last-door lock',async()=>{
  const {run}=setup(); run('current="ports"');
  await run('loadPorts()');
  const html=run('ports()');
  const a=row(html,'SSH לשרת — ens18'), b=row(html,'SSH לשרת — ens19'), c=row(html,'SSH לשרת — ens20');
  assert.match(a,/class="st ok">תקין<\/span><span class="sub">מאזין 10\.44\.10\.1:22 · אומת/);
  assert.match(a,/class="sw on"/); assert.match(a,/🔒 הדלת האחרונה · סגירה = הקלדת ens18/); assert.match(a,/אין SSH לשרת מאף רשת/);
  assert.match(b,/class="st ">כבוי<\/span><span class="sub">סגור · אומת/); assert.match(b,/class="sw " /); assert.match(b,/🔒 סגור · פתיחה = הקלדת ens19/);
  assert.match(c,/class="st unk">לא נבדק<\/span><span class="sub">טבלת הסוקטים לא נקראה/); assert.doesNotMatch(c,/סגור · אומת/);
  assert.match(a,/PUT \/ssh\/interfaces\/ens18/);
});

test('the wired switches call the same endpoints as before: monitor, SSH stations, SSH per NIC (open + last door), DHCP off, proxy off',async()=>{
  const {run,requests}=setup(); run('current="ports"');
  await run('loadPorts()'); run('ports()');
  const put=(u)=>requests.filter((r)=>r.method==='PUT' && r.url===u);
  // מוניטור — הדלקה = sheet עם imagectl.monitor → PUT /monitor/settings
  run("portSwitch('monitor')");
  let s=run('sheets.at(-1)'); assert.equal(s.verify.mustEqual,'imagectl.monitor'); assert.match(s.note,/מה קורה אם מכבים/);
  await s.onSubmit(); assert.deepEqual(put('/monitor/settings').at(-1).body,{enabled:true,confirm:'imagectl.monitor'});
  // SSH לתחנות — הדלקה = sheet עם imagectl.debug → PUT /ssh/stations
  run("portSwitch('ssh_stations')");
  s=run('sheets.at(-1)'); assert.equal(s.verify.mustEqual,'imagectl.debug');
  await s.onSubmit(); assert.deepEqual(put('/ssh/stations').at(-1).body,{enabled:true,confirm:'imagectl.debug'});
  // SSH לשרת — פתיחת ens19 = הקלדת ens19
  run("portSwitch('ssh_nic:ens19')");
  s=run('sheets.at(-1)'); assert.equal(s.verify.mustEqual,'ens19'); assert.match(s.title,/פתיחת SSH לשרת על ens19/);
  await s.onSubmit(); assert.deepEqual(put('/ssh/interfaces/ens19').at(-1).body,{enabled:true,confirm:'ens19'});
  // הדלת האחרונה — סגירת ens18 דורשת הקלדה ומזהירה
  run("portSwitch('ssh_nic:ens18')");
  s=run('sheets.at(-1)'); assert.equal(s.verify.mustEqual,'ens18'); assert.match(s.sub,/הדלת האחרונה/); assert.match(s.note,/אין SSH לשרת מאף רשת/);
  await s.onSubmit(); assert.deepEqual(put('/ssh/interfaces/ens18').at(-1).body,{enabled:false,confirm:'ens18'});
  // DHCP — כיבוי = הקלדת שם הכרטיס → PUT /net/interfaces/ens19 עם enabled:false, proxy:false והשאר כמו שהיה
  run("portSwitch('dhcp:ens19')");
  s=run('sheets.at(-1)'); assert.equal(s.verify.mustEqual,'ens19'); assert.match(s.note,/מה קורה אם מכבים: אף מכונה בוילן ההפצה/);
  await s.onSubmit();
  const d=put('/net/interfaces/ens19').at(-1).body;
  assert.equal(d.enabled,false); assert.equal(d.proxy,false); assert.equal(d.confirm,'ens19'); assert.equal(d.range_start,'10.44.9.100'); assert.equal(d.server_ip,'10.44.9.10');
  // PXE proxy — כיבוי = אישור (בלי הקלדה: לא שובר את המערכת) → proxy:false על ens20
  run("portSwitch('pxe_proxy')");
  s=run('sheets.at(-1)'); assert.ok(!s.verify); assert.match(s.title,/כיבוי PXE proxy על ens20/);
  await s.onSubmit(); const p=put('/net/interfaces/ens20').at(-1).body; assert.equal(p.proxy,false); assert.equal(p.confirm,'ens20');
  // כל toggle קורא את /ports מחדש (ראיה חוזרת)
  assert.ok(requests.filter((r)=>r.url==='/ports').length>=6);
});

test('ports page (#996 contract): live switches — "api" confirms, "confirm" types the server name, "none" explains; bind and off_means from the server; new rows appear',async()=>{
  const {run,requests}=setup({'/ports':PORTS_NEW}); run('current="ports"');
  await run('loadPorts()');
  const html=run('ports()'); balanced(html);
  assert.equal((html.match(/<tr><td>/g)||[]).length,14,'DHCP + 10 server rows + 3 SSH');
  const kiosk=row(html,'>desc kiosk<');
  assert.match(kiosk,/class="sw on" role="switch" aria-checked="true"/); assert.match(kiosk,/class="pill ok">קיים<\/span> <span class="cap mono">PUT \/ports\/kiosk/);
  assert.match(kiosk,/<span class="mono">0\.0\.0\.0<\/span>/); assert.match(kiosk,/מסך התחנה לא נטען; הסוכן ממשיך לעבוד/);
  assert.doesNotMatch(kiosk,/דורש API/);
  run("portSwitch('kiosk')");
  let s=run('sheets.at(-1)'); assert.ok(!s.verify); assert.match(s.title,/כיבוי HTTP 8082\/tcp/); assert.match(s.sub,/מסך התחנה לא נטען/);
  await s.onSubmit();
  assert.deepEqual(requests.filter((r)=>r.method==='PUT').at(-1),{url:'/ports/kiosk',method:'PUT',body:{enabled:false}});
  // 🔒 confirm: הקלדת שם השרת + "מה קורה אם מכבים"
  const boot=row(html,'>desc http_boot<');
  assert.match(boot,/🔒 דלוק · כיבוי = הקלדת שם השרת/);
  run("portSwitch('http_boot')");
  s=run('sheets.at(-1)'); assert.equal(s.verify.mustEqual,'srv'); assert.match(s.note,/מה קורה אם מכבים: אין PXE ואין hello/);
  await s.onSubmit();
  assert.deepEqual(requests.filter((r)=>r.method==='PUT').at(-1).body,{enabled:false,confirm:'srv'});
  // none: מתג שמסביר
  const dyn=row(html,'9002–9099/udp');
  assert.match(dyn,/aria-checked="true"/); assert.match(dyn,/אין מתג — לחיצה מסבירה/); assert.match(dyn,/class="st unk">לא נבדק/);
  const before=run('sheets.length'); run("portSwitch('dynamic')");
  assert.equal(run('sheets.length'),before); assert.match(run('toasts.at(-1)'),/אין מתג לפורט הזה: שחזור תחנה בודדת/);
  // 8443 חדש, כבוי, api
  const inter=row(html,'8443/tcp'); assert.match(inter,/class="sw " role="switch" aria-checked="false"/); assert.match(inter,/>כבוי</);
  // תג הכותרת: שתי שורות "לא נקרא" (dynamic מהשרת + SSH ens20 בלי טבלת סוקטים) — לא ירוק
  assert.match(html,/class="pill warn">2 לא נקרא</);
});

test('what could not be read is "לא נקרא", never "כבוי": /ssh, /monitor/settings, /net/interfaces each fail on their own; /ports failure is a red note',async()=>{
  let s=setup({'/ssh':new Error('ssh 500'),'/monitor/settings':new Error('mon 500'),'/net/interfaces':new Error('net 500')}); s.run('current="ports"');
  await s.run('loadPorts()');
  let html=s.run('ports()'); balanced(html);
  const ssh=row(html,'>SSH לשרת<'); assert.match(ssh,/class="st unk">לא נבדק<\/span><span class="sub">‏\/ssh לא נקרא: ssh 500/); assert.match(ssh,/class="sw unk"/);
  assert.doesNotMatch(html,/SSH לשרת — ens/);
  const mon=row(html,'Monitor (RFB)'); assert.match(mon,/class="sw unk"/); assert.match(mon,/לא נקרא: mon 500/);
  const dhcp=row(html,'>DHCP הפצה<'); assert.match(dhcp,/class="st unk">לא נבדק<\/span><span class="sub">‏\/net\/interfaces לא נקרא: net 500/); assert.match(dhcp,/class="sw unk"/);
  assert.match(row(html,'>PXE<'),/class="sw unk"/);
  s.run("portSwitch('dhcp')"); assert.match(s.run('toasts.at(-1)'),/net 500/); assert.equal(s.run('sheets.length'),0);
  assert.doesNotMatch(html,/מאזינים בפורט 22/);
  // אף כרטיס לא מחלק: שורת DHCP אחת כבויה, הדלקה פותחת את טופס ה-DHCP של net.js (הקלדת שם הכרטיס)
  s=setup({'/net/interfaces':[nic('ens18'),nic('ens19')]}); s.run('current="ports"');
  await s.run('loadPorts()'); html=s.run('ports()');
  assert.match(row(html,'>DHCP הפצה<'),/class="st ">כבוי<\/span><span class="sub">לא הודלק על אף כרטיס/);
  s.run("portSwitch('dhcp')"); const sh=s.run('sheets.at(-1)'); assert.match(sh.title,/DHCP על ens18/); assert.equal(sh.verify.mustEqual,'ens18');
  // /ports עצמו לא נקרא
  s=setup({'/ports':new Error('DB down')}); s.run('current="ports"');
  await s.run('loadPorts()'); html=s.run('ports()');
  assert.notEqual(html,s.run('pagePlaceholder()')); assert.match(html,/note err.*DB down/); assert.match(html,/class="pill err">לא נקרא</);
  assert.doesNotMatch(html,/<table/);
});

/* ---------- #1015: החוזה האמיתי — dhcp מרוכז, ssh_server:<nic> מוכן,
   interserver, bind כרשימה — מול PR #1015 / docs/interfaces.md §16 ---------- */
const p1015=(id,name,p,proto,state,detail,extra={})=>({id,name,port:p,proto,desc:'desc '+id,target:'target '+id,state,detail,
  note:'לפתוח ב-FW: '+p,enabled:null,listening:null,bind:[],toggle:'none',toggle_url:null,confirm_word:null,confirm_when:null,off_means:'',...extra});
const PORTS_1015=[
  p1015('tftp','TFTP','69','udp','ok','dnsmasq מגיש',{enabled:true,listening:true,bind:['0.0.0.0:69'],off_means:'אין shim/GRUB — מחשבים לא יעלו ב-PXE'}),
  p1015('dhcp','DHCP','67','udp','ok','dnsmasq מאזין',{enabled:true,listening:true,bind:['0.0.0.0:67'],toggle:'confirm',
    toggle_url:'/api/console/net/interfaces/{name}',confirm_when:'on',off_means:'תחנות לא יקבלו כתובת ולא dhcp-boot — אין PXE מהשרת הזה',
    interfaces:[{name:'ens19',enabled:true,proxy:false}]}),
  p1015('http_boot','HTTP','8080','tcp','ok','uvicorn',{enabled:true,listening:true,bind:['0.0.0.0:8080'],toggle:'confirm',
    toggle_url:'/api/console/ports/http_boot',confirm_when:'off',off_means:'אין PXE ואין hello — כל סבב פעיל נכשל בגלוי'}),
  p1015('http_console','HTTPS','8081','tcp','ok','uvicorn',{enabled:true,listening:true,bind:['10.44.10.1:8081'],toggle:'confirm',
    toggle_url:'/api/console/ports/http_console',confirm_when:'off',off_means:'אתה ננעל בחוץ'}),
  p1015('pxe_proxy','PXE','4011','udp','ok','proxy פעיל',{enabled:true,listening:true,bind:['0.0.0.0:4011'],toggle:'api',
    toggle_url:'/api/console/net/interfaces/{name}',interfaces:[{name:'ens20',enabled:false,proxy:true}]}),
  p1015('multicast','Multicast','9000–9001','udp','off','אין סבב רץ'),
  p1015('monitor','Monitor (RFB)','5900','tcp','off','המתג monitor:stations כבוי',{enabled:false,toggle:'confirm',
    toggle_url:'/api/console/monitor/settings',confirm_word:'imagectl.monitor',confirm_when:'on',off_means:'אין צפייה מרחוק'}),
  p1015('kiosk','HTTP','8082','tcp','ok','uvicorn',{enabled:true,listening:true,bind:['0.0.0.0:8082'],toggle:'api',
    toggle_url:'/api/console/ports/kiosk',off_means:'מסך התחנה לא נטען; הסוכן ממשיך לעבוד'}),
  p1015('interserver','HTTPS (mTLS)','8443','tcp','ok','uvicorn',{enabled:true,listening:true,bind:['10.44.10.1:8443'],toggle:'api',
    toggle_url:'/api/console/ports/interserver',off_means:'משני לא יכול להירשם או לסנכרן'}),
  p1015('ssh_stations','SSH','22','tcp','off','imagectl.debug לא בשורת הקרנל',{enabled:false,toggle:'confirm',
    toggle_url:'/api/console/ssh/stations',confirm_word:'imagectl.debug',confirm_when:'on',off_means:'תחנות עולות בלי dropbear'}),
  p1015('ssh_server:ens18','SSH','22','tcp','ok','מאזין 10.44.10.1:22 · אומת',{enabled:true,listening:true,bind:['10.44.10.1:22'],
    toggle:'confirm',toggle_url:'/api/console/ssh/interfaces/ens18',confirm_word:'ens18',confirm_when:'on_or_last_off',
    off_means:'אין SSH לשרת דרך הכרטיס הזה',interface:'ens18',addresses:['10.44.10.1/24']}),
  p1015('ssh_server:ens19','SSH','22','tcp','off','סגור · אומת',{enabled:false,listening:false,bind:[],
    toggle:'confirm',toggle_url:'/api/console/ssh/interfaces/ens19',confirm_word:'ens19',confirm_when:'on_or_last_off',
    off_means:'אין SSH לשרת דרך הכרטיס הזה',interface:'ens19',addresses:['10.44.9.10/24']}),
  p1015('ssh_server:ens20','SSH','22','tcp','unknown','טבלת הסוקטים לא נקראה',{enabled:false,listening:null,bind:[],
    toggle:'confirm',toggle_url:'/api/console/ssh/interfaces/ens20',confirm_word:'ens20',confirm_when:'on_or_last_off',
    off_means:'אין SSH לשרת דרך הכרטיס הזה',interface:'ens20',addresses:['10.44.11.1/24']}),
];

test('ports page (#1015 contract): rows come only from /ports — no duplicate dhcp/ssh_server rows, bind is a list, confirm follows confirm_when',async()=>{
  const {run,requests}=setup({'/ports':PORTS_1015}); run('current="ports"');
  await run('loadPorts()');
  const html=run('ports()'); balanced(html);
  // מקור אחד לכל שורה: אין עוד שורות DHCP/SSH-לשרת שהדף בונה בעצמו מ-/net/interfaces או /ssh
  assert.equal((html.match(/desc dhcp</g)||[]).length,1,'exactly one DHCP row — no duplicate built from /net/interfaces');
  for (const nic of ['ens18','ens19','ens20'])
    assert.equal((html.match(new RegExp('desc ssh_server:'+nic+'<','g'))||[]).length,1,'exactly one SSH-to-server row per NIC — no duplicate built from /ssh');
  assert.equal((html.match(/<tr><td>/g)||[]).length,PORTS_1015.length,'one row per /ports entry, nothing added, nothing dropped');
  // bind הוא רשימה — כל הכתובות, mono, ולא "a,b" משרשור מחרוזות
  const kiosk=row(html,'>desc kiosk<');
  assert.match(kiosk,/<span class="mono bindlist" dir="ltr">0\.0\.0\.0:8082<\/span>/);
  // kiosk (8082) — toggle:"api" → PUT /ports/kiosk, בלי הקלדה
  run("portSwitch('kiosk')");
  let s=run('sheets.at(-1)'); assert.ok(!s.verify);
  await s.onSubmit();
  assert.deepEqual(requests.filter((r)=>r.method==='PUT').at(-1),{url:'/ports/kiosk',method:'PUT',body:{enabled:false}});
  // DHCP — המתג ממשיך לפנות ל-PUT /net/interfaces/{n} (toggle_url מצביע לשם), לא ל-/ports/dhcp (409 בשרת האמיתי)
  run("portSwitch('dhcp')");
  s=run('sheets.at(-1)'); assert.equal(s.verify.mustEqual,'ens19');
  await s.onSubmit();
  const dhcpPut=requests.find((r)=>r.method==='PUT' && r.url==='/net/interfaces/ens19');
  assert.ok(dhcpPut,'DHCP switch must PUT /net/interfaces/ens19'); assert.equal(dhcpPut.body.enabled,false);
  assert.ok(!requests.some((r)=>r.url==='/ports/dhcp'),'PUT /ports/dhcp must never be sent — the row is not a /ports/{id} switch');
  // confirm_when="off" (8080/8081): הדלקה אינה דורשת הקלדה, רק כיבוי
  let s2=setup({'/ports':PORTS_1015.map((p)=>p.id==='http_boot'?{...p,enabled:false}:p)}); s2.run('current="ports"');
  await s2.run('loadPorts()'); s2.run('ports()');
  s2.run("portSwitch('http_boot')");
  const sb=s2.run('sheets.at(-1)');
  assert.ok(!sb.verify,'turning ON http_boot (confirm_when=off) must not ask for typed confirmation');
  await sb.onSubmit();
  assert.deepEqual(s2.requests.filter((r)=>r.method==='PUT').at(-1),{url:'/ports/http_boot',method:'PUT',body:{enabled:true}});
  // confirm_when="on" (מוניטור): הדלקה דורשת את confirm_word מהשרת, לא ניחוש בלקוח
  run("portSwitch('monitor')");
  s=run('sheets.at(-1)'); assert.equal(s.verify.mustEqual,'imagectl.monitor');
  await s.onSubmit();
  assert.ok(requests.some((r)=>r.method==='PUT' && r.url==='/monitor/settings' && r.body.confirm==='imagectl.monitor'));
  // ssh_server:<nic> — confirm_when="on_or_last_off": פתיחת ens19 (סגור) דורשת הקלדה, ישירות מ-toggle_url של השורה, בלי fetch נוסף ל-/ssh
  run("portSwitch('ssh_server:ens19')");
  s=run('sheets.at(-1)'); assert.equal(s.verify.mustEqual,'ens19'); assert.match(s.title,/פתיחת SSH לשרת על ens19/);
  await s.onSubmit();
  assert.ok(requests.some((r)=>r.method==='PUT' && r.url==='/ssh/interfaces/ens19' && r.body.enabled===true && r.body.confirm==='ens19'));
  // סגירת ens18 — הדלת האחרונה הפתוחה (מחושב מתוך שורות ה-/ports עצמן) — עדיין הקלדה, עם אזהרה
  run("portSwitch('ssh_server:ens18')");
  s=run('sheets.at(-1)'); assert.equal(s.verify.mustEqual,'ens18'); assert.match(s.sub,/הדלת האחרונה/);
  await s.onSubmit();
  assert.ok(requests.some((r)=>r.method==='PUT' && r.url==='/ssh/interfaces/ens18' && r.body.enabled===false && r.body.confirm==='ens18'));
});

test('deploy role: the pages stay admin-only and no admin endpoint is requested',async()=>{
  const {run,requests}=setup({'/health':403,'/ssh':403,'/monitor/settings':403,'/update':403});
  run('ME={username:"mafitz",role:"deploy",server_name:"srv",capabilities:{}}; current="ports"');
  assert.equal(run('pageAllowed("health")'),false); assert.equal(run('pageAllowed("ports")'),false);
  await run('loadPorts()');
  for(const u of ['/ssh','/monitor/settings','/net/interfaces']) assert.ok(!requests.some((r)=>r.url===u),u+' must not be requested by deploy');
});

// --- נדב 17/09: שורת TFTP עם 8 כתובות האזנה מתחה את הטבלה מעבר למסך ---------------
test('a long bind list renders one address per line, IPv6 link-local folded into +N, never one long inline string', () => {
  const {run}=setup();
  const html=run(`portNicHtml(${JSON.stringify(['127.0.0.1:69','10.10.10.8:69','10.44.3.1:69','10.44.0.1:69','[fe80::215:5dff:fe44:3c07]%eth3:69','[fe80::215:5dff:fe10:1]%br0:69','[fe80::215:5dff:fe10:2]%eth1:69','[::1]:69'])})`);
  assert.match(html,/class="mono bindlist" dir="ltr"/);
  assert.match(html,/10\.10\.10\.8:69<br>10\.44\.3\.1:69/,'one per line');
  assert.doesNotMatch(html,/fe80[^<]*<br>/,'link-local is not in the visible list');
  assert.match(html,/title="[^"]*fe80[^"]*">\+4</,'the rest is folded into +N with a tooltip');
  assert.doesNotMatch(html,/, \[fe80/,'no comma-joined inline string');
  assert.match(run(`portNicHtml(['0.0.0.0:8080'])`),/0\.0\.0\.0:8080/);
  assert.match(run(`portNicHtml([])`),/—/);
});
