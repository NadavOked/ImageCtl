// ‏#954 גל 6: הגדרות (settings.md) · הרשאות (permissions.md) · יומן (logs.md)
// + WoL למחשב בודד ולקבוצת הבנייה (#984). מוניטור ודרייברים — בקבצים שלהם.
// בלי דפדפן: console.js + net.js + progress.js רצים ב-vm מול DOM מזערי ו-fetch
// מזויף. עיקרון 5: כל endpoint שלא נקרא הוא מצב משלו, לא "ריק"; "נשלח" ≠
// "התעוררה" (#528). #1270: מחיקת משתמש = חלון אישור.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');

const MAC_B='c8:d9:d2:0b:fe:32', MAC_C='a0:48:1c:8a:18:40', MAC_S='00:11:22:33:44:55';
const SETTINGS={recovery_require_login:'true',session_wait_seconds:'300',console_idle_seconds:'600',class_deploy_enabled:'false',update_enabled:'false',server_name:'שרת המכללה'};
const USERS=[{username:'admin',role:'admin',created_at:'2026-08-01T10:00:00',disabled:false,last_login_at:'2026-09-17T08:05:00+00:00',last_login_from:'10.44.10.55'},
  {username:'yossi',role:'admin',created_at:'2026-09-17T08:00:00',disabled:true,last_login_at:null,last_login_from:null},
  {username:'deployer',role:'deploy',created_at:'2026-09-17T09:00:00',disabled:false,last_login_at:null,last_login_from:null}];
const today=new Date().toISOString().slice(0,10);
const JOURNAL=[
  {ts:today+'T09:44:10',user:'',event:'boot_loop_local',label:'מחשב אתחל שוב ושוב — נשלח לדיסק המקומי',text:'LAB303-03 <b>x</b>',severity:'warn'},
  {ts:today+'T09:42:31',user:'nadav',event:'session_open',label:'סבב נפתח',text:'Office 2024 לכיתה 303',severity:'info'},
  {ts:'2026-09-16T08:12:44',user:'',event:'storage_transfer_failed',label:'העברת אימג\' לסניף נכשלה',text:'timeout',severity:'err'},
  {ts:'2026-09-16T08:10:02',user:'deployer',event:'client_done',label:'מחשב סיים לכתוב',text:'',severity:'ok'},
];
const wake=(sent,failed=0,reasons=[])=>({sent,failed,reasons});

function setup(over={}) {
  const nodes=new Map(), requests=[];
  function node(key) {
    if(!nodes.has(key)) nodes.set(key,{value:'',innerHTML:'',textContent:'',hidden:false,disabled:false,dataset:{},style:{removeProperty(){}},cls:new Set(),
      classList:{add(){},remove(){},toggle(c,on){ if(on) this.cls?.add?.(c); }},addEventListener(){},removeEventListener(){},
      querySelector(){return null;},querySelectorAll(){return [];},insertAdjacentHTML(_p,html){this.innerHTML+=html;},setAttribute(k,v){this['@'+k]=v;},removeAttribute(){},hasAttribute(){return false;},getAttribute(k){return this['@'+k]??null;},closest(){return {style:{}};},focus(){},click(){this.clicked=true;}});
    return nodes.get(key);
  }
  const fixtures={'/settings':SETTINGS,'/users':USERS,'/journal':JOURNAL,'/journal/events':[{event:'session_open',label:'סבב נפתח'},{event:'login',label:'כניסה לקונסולה'}],
    '/machines':[{mac:MAC_B,suffix:'בנייה 1',group_id:'grp_BUILD',disks:[]},{mac:MAC_C,suffix:'מחשב 1',group_id:'grp_CLONERS',disks:[]},{mac:MAC_S,suffix:'05',group_id:'grp_LAB1',disks:[]}],
    '/groups':[{id:'grp_BUILD',label:'מחשבי בנייה',role:'build'},{id:'grp_CLONERS',label:'מחשבי שיכפול',role:'cloner'},{id:'grp_LAB1',label:'כיתה 1',role:'classroom'}],
    ['/machines/'+encodeURIComponent(MAC_B)+'/wake']:wake(1),['/machines/'+encodeURIComponent(MAC_C)+'/wake']:wake(0,1,['אין MAC ברשומה']),'/groups/grp_BUILD/wake':wake(2),
    ...over};
  const ctx=vm.createContext({console,URLSearchParams,URL,Date,Set,Map,Number,Math,JSON,Promise,String,Array,Object,encodeURIComponent,decodeURIComponent,
    document:{hidden:false,querySelector:node,querySelectorAll:()=>[],getElementById:id=>node('#'+id),
      createElement:()=>{let text='';return {set textContent(v){text=String(v)},get innerHTML(){return text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')}}},
      documentElement:{setAttribute(){}},addEventListener(){},removeEventListener(){}},
    window:{addEventListener(){},matchMedia:()=>({matches:false}),open(){}},localStorage:{getItem:()=>null,setItem(){}},CSS:{escape:x=>x},
    setInterval:()=>1,clearInterval(){},setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options={})=>{
      const key=url.replace('/api/console','').split('?')[0]; requests.push({url:key,full:url,method:options.method||'GET',body:typeof options.body==='string'?JSON.parse(options.body):(options.body||null)});
      const f=fixtures[key];
      const hdr={get:(h)=>h==='X-Journal-Search-Truncated'&&fixtures.__truncated?'true':h==='X-Journal-Total'&&fixtures.__total!=null?String(fixtures.__total):null};
      if(f instanceof Error) return {status:500,ok:false,headers:hdr,json:async()=>({detail:f.message})};
      if(f===403) return {status:403,ok:false,headers:hdr,json:async()=>({detail:'WoL לתחנות כיתה — v2'})};
      if(f===404) return {status:404,ok:false,headers:hdr,json:async()=>({detail:'Not Found'})};
      if(f===204) return {status:204,ok:true,headers:hdr,json:async()=>({})};
      return {status:200,ok:true,headers:hdr,json:async()=>f ?? {ok:true}};
    }});
  for(const f of ['progress.js','console.js','net.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={username:"admin",role:"admin",server_name:"srv",idle_seconds:600,version:"v0.35.0",capabilities:{classrooms:true}}; globalThis.sheets=[]; globalThis.toasts=[]; sheet = o => { sheets.push(o); }; toast=(m)=>{ toasts.push(m); }; renderCurrent=()=>{ globalThis.rendered=(globalThis.rendered||0)+1; }; globalThis.selected=[]; selectPageById=(id)=>{ selected.push(id); current=id; }; globalThis.drawer=""; openDrawer=(t,b)=>{ drawer=t+"|"+b; }; globalThis.idleRestarted=0; startIdleWatch=()=>{ idleRestarted++; };');
  run('MACHINES='+JSON.stringify(fixtures['/machines'])+'; GROUPS='+JSON.stringify(fixtures['/groups'])+'; NET=[]; DISK_FAILURES=[]; SHRINK_RECORDS=[];');
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
const unesc=(t)=>t.replaceAll('&quot;','"').replaceAll('&#39;',"'").replaceAll('&#96;','`').replaceAll('&lt;','<').replaceAll('&gt;','>').replaceAll('&amp;','&');
const menuOf=(tr)=>{const m=tr.match(/class="kebab"[^>]*data-menu="([^"]*)"/); assert.ok(m,'row has a ⋯ menu'); return JSON.parse(unesc(m[1]));};
const row=(html,needle)=>{const i=html.indexOf(needle); assert.ok(i>=0,'missing row: '+needle); const s=html.lastIndexOf('<tr',i); return html.slice(s,html.indexOf('</tr>',i)+5);};

/* ---------- הרישום: חמישה עמודים own, בלי הקוד הישן ---------- */

test('the five pages are own pages; monitor/settings/permissions/logs without tabs, drivers with two; the old renderers are gone',()=>{
  const {run}=setup();
  for(const id of ['monitor','settings','permissions','logs','drivers']) assert.equal(run(`pages.${id}.own`),true,id);
  for(const id of ['monitor','settings','permissions','logs']) assert.equal(run(`pages.${id}.tabs.length`),0,id+' has no tabs');
  assert.equal(JSON.stringify(run('pages.drivers.tabs')),JSON.stringify(['דגמים','חבילות','כיסוי לפי מכונה']));
  for(const fn of ['usersAdminPage','journalPage','settingsPage','loadUsersAdmin','loadJournal','loadJournalEvents','openRolesDrawer','openLogFilter','applyLogFilter','loadSettings','wireSettings','loadUpdateInfo','checkForUpdate','editUser','saveUser','createUser'])
    assert.equal(run('typeof '+fn),'undefined',fn+' must be deleted');
  assert.equal(run('typeof journalFilters'),'undefined');
  for(const id of ['settings','permissions','logs','monitor','drivers']) assert.equal(run(`typeof tabRender("${id}",0)`),'string');   // הגנרי אינו נקרא ל-own, אבל אסור שיקרוס
  assert.equal(run('settings()'),run('pagePlaceholder()')); assert.equal(run('permissions()'),run('pagePlaceholder()')); assert.equal(run('logs()'),run('pagePlaceholder()'));
});

test('deploy role: the five pages stay admin-only (pageAllowed); WoL is refused client-side for a classroom machine before any request',async()=>{
  const {run,requests}=setup();
  run('ME.role="deploy"');
  for(const id of ['settings','permissions','logs','monitor','drivers']) assert.equal(run(`pageAllowed("${id}")`),false,id);
  await run(`wakeMachine('${encodeURIComponent(MAC_S)}')`);
  assert.ok(!requests.some((r)=>r.url.endsWith('/wake')),'classroom: no request');
  assert.match(run('toasts.at(-1)'),/WoL לתחנות כיתה — v2/);
});

/* ---------- הגדרות ---------- */

test('settings (#1300, console-full.html P.settings 1:1): a value is ONE dl.kv row with a small link action ("שם השרת  NadavPC  שינוי שם"), a switch is label.sw with the switch first and the text after it; save/cancel in the header, cancel first',async()=>{
  const {run,requests}=setup(); run('current="settings"');
  await run('loadSettingsData()');
  assert.ok(requests.some((r)=>r.url==='/settings')); assert.ok(!requests.some((r)=>/branding/.test(r.url)),'#1302: no institution logo to read');
  const html=run('settings()'); balanced(html);
  assert.match(html,/class="obj-name">הגדרות</); assert.doesNotMatch(html,/>Settings<|Console policy|form-grid|set-login/);
  assert.match(html,/<div class="obj-actions"><button class="btn" id="set-cancel" onclick="cancelSettings\(\)" disabled>ביטול<\/button><button class="btn primary" id="set-save" onclick="saveSettings\(\)" disabled>שמירה<\/button><\/div>/);
  for(const c of ['כללי','אבטחה','גיבוי','עדכונים','מראה','תעודת TLS','שם המארח']) assert.match(html,new RegExp('<div class="c12 card"><div class="card-h"><span>'+c+'<'),c);
  const cardOf=(c)=>{const i=html.indexOf('<span>'+c+'<'); return html.slice(i,html.indexOf('<div class="c12 card">',i+5));};
  // ערכים: dl.kv — תווית, ערך, ופעולה קטנה (חלון שנשמר מיד), לא שדה פתוח בדף
  assert.match(cardOf('כללי'),/<dl class="mkv"><dt title="[^"]*">שם השרת<\/dt><dd title="[^"]*">שרת המכללה <button class="btn sm flat" type="button" onclick="settingsValueSheet\('server_name'\)">שינוי שם<\/button><\/dd>/);
  assert.match(cardOf('כללי'),/<dt[^>]*>המתנה מהמצטרף האחרון<\/dt><dd[^>]*>5 דקות <button[^>]*settingsValueSheet\('session_wait_seconds'\)">שינוי</);
  assert.match(cardOf('אבטחה'),/<dt[^>]*>ניתוק הפעלות לא פעילות<\/dt><dd[^>]*>10 דקות <button[^>]*settingsValueSheet\('console_idle_seconds'\)">שינוי</);
  assert.doesNotMatch(html,/<input type="(text|number)"/,'no wide input boxes on the page');
  // מתגים: label.sw — המתג קודם, הטקסט אחריו; ההסבר ב-title
  const sw=(k)=>{const m=html.match(new RegExp(`<label class="msw" id="srow-${k}"[^>]*>.*?</label>`)); assert.ok(m,k); return m[0];};
  assert.match(sw('recovery_require_login'),/^<label class="msw" id="srow-recovery_require_login" title="מומלץ דלוק; כיבוי רק להדגמה"><input type="checkbox" role="switch" aria-label="שחזור תחנה בודדת דורש כניסה" checked onchange="settingsSwitch\('recovery_require_login', this\)"><span class="track"><\/span><span>שחזור תחנה בודדת דורש כניסה<\/span><\/label>$/);
  assert.match(sw('identity_check'),/ checked /,'#855: missing = on'); assert.match(sw('identity_check'),/title="כתובת המקור של hello\/דיווח חייבת להתאים לחכירת ה-DHCP של ה-MAC/);
  assert.doesNotMatch(sw('update_enabled'),/ checked /);
  assert.ok(cardOf('אבטחה').includes('id="srow-recovery_require_login"')&&cardOf('אבטחה').includes('id="srow-identity_check"'));
  assert.ok(cardOf('עדכונים').includes('id="srow-update_enabled"'),'the switch sits in the update card itself');
  // ‏#1303: מתג הקצב הנלמד — בכרטיס "כללי"; חסר = אוטומטי (דלוק)
  assert.ok(cardOf('כללי').includes('id="srow-learned_rate_enabled"')); assert.match(sw('learned_rate_enabled'),/ checked onchange="settingsSwitch\('learned_rate_enabled', this\)"><span class="track"><\/span><span>קצב שידור אוטומטי/);
  run('SETTINGS.learned_rate_enabled="false"'); assert.equal(run('settingCurrent("learned_rate_enabled")'),false,'"false" is the only off');
  assert.equal((html.match(/role="switch"/g)||[]).length,run('classroomsOn()')?5:4,'learned rate · recovery · identity · update (+ class deploy with classrooms)');
  assert.doesNotMatch(html,/class="srow|<b>שם השרת<\/b>|<span class="cap">(דלוק|כבוי)<\/span>/,'#1300: not the old two-column form');
  assert.doesNotMatch(html,/role="tablist"/);
  // מראה: שלושה מצבים, אחד נלחץ — לפי themePref
  assert.match(html,/aria-label="ערכת נושא">.*aria-pressed="true">לפי המערכת<.*aria-pressed="false">בהיר<.*aria-pressed="false">כהה</);
  // ‏#1302 (נדב 12/10): אין אפשרות להחליף לוגו — אין כרטיס "מיתוג", אין העלאה ואין הסרה.
  assert.doesNotMatch(html,/<span>מיתוג<|logo-input|settingsLogo|branding/);
});
test('settings: a switch marks itself and enables save; save posts only the changed switches as "true"/"false" and reloads; cancel drops the draft',async()=>{
  const {run,requests,node}=setup(); run('current="settings"');
  await run('loadSettingsData()'); run('settings()');
  run("settingsSwitch('recovery_require_login', {checked:false})");
  assert.equal(node('#set-save').disabled,false); assert.equal(node('#set-cancel').disabled,false);
  assert.match(run('settings()'),/<label class="msw changed" id="srow-recovery_require_login"/);
  run("settingsSwitch('recovery_require_login', {checked:true})");   // חזרה לערך המקורי = לא שינוי
  assert.equal(run('settingsDirty()'),false); assert.equal(node('#set-save').disabled,true);
  run("settingsSwitch('recovery_require_login', {checked:false}); settingsSwitch('identity_check', {checked:false})");
  await run('saveSettings()');
  const p=requests.find((r)=>r.url==='/settings'&&r.method==='POST');
  assert.deepEqual(p.body,{recovery_require_login:'false',identity_check:'false'},'only the changed keys, as strings');
  assert.equal(run('settingsDirty()'),false); assert.match(run('toasts.at(-1)'),/נשמר — 2 הגדרות/);
  assert.equal(requests.filter((r)=>r.url==='/settings'&&r.method==='GET').length,2,'re-read after save');
  run("settingsSwitch('update_enabled', {checked:true}); cancelSettings()");
  assert.equal(run('settingsDirty()'),false);
});

test('settings: a value is changed in its own small window and saved at once — idle and server name apply locally; below the minimum / empty is refused without a request',async()=>{
  const {run,requests}=setup(); run('current="settings"');
  await run('loadSettingsData()');
  run("settingsValueSheet('console_idle_seconds')");
  let sh=run('sheets.at(-1)'); assert.match(sh.title,/ניתוק הפעלות לא פעילות/); assert.equal(sh.fields[0].value,'600');
  await assert.rejects(()=>sh.onSubmit({v:'30'}),/ניתוק הפעלות לא פעילות: מספר שלם, 60 לפחות/);
  assert.ok(!requests.some((r)=>r.url==='/settings'&&r.method==='POST'),'nothing was posted');
  await sh.onSubmit({v:' 900 '});
  assert.deepEqual(requests.filter((r)=>r.url==='/settings'&&r.method==='POST').at(-1).body,{console_idle_seconds:'900'});
  assert.equal(run('ME.idle_seconds'),900); assert.equal(run('idleRestarted'),1);
  run("settingsValueSheet('server_name')"); sh=run('sheets.at(-1)');
  await assert.rejects(()=>sh.onSubmit({v:'   '}),/שם השרת: לא יכול להיות ריק/);
  await sh.onSubmit({v:'  שרת חדש '});
  assert.deepEqual(requests.filter((r)=>r.url==='/settings'&&r.method==='POST').at(-1).body,{server_name:'שרת חדש'}); assert.equal(run('ME.server_name'),'שרת חדש');
  const s=setup({'/settings':new Error('403')}); s.run('current="settings"');
  await s.run('loadSettingsData()');
  const html=s.run('settings()');
  assert.notEqual(html,s.run('pagePlaceholder()')); assert.match(html,/class="pill err">לא נקרא</); assert.match(html,/note err.*403/);
  assert.doesNotMatch(html,/role="switch"|id="set-save"/,'no controls over values we do not have');
});

/* ---------- הרשאות ---------- */

test('permissions: one users table (name, role pill, state, created, hover actions), "this is you" gets password only, the last active admin cannot be deleted/demoted/disabled, matrix from the code',async()=>{
  const {run,requests}=setup(); run('current="permissions"');
  await run('loadUsersData()');
  const html=run('permissions()'); balanced(html);
  assert.match(html,/<div class="obj-sub"[^>]*>3 משתמשים · תפקידים: מנהל, הפצה<\/div>/,'#1300: console-full.html P.users sub-line');
  assert.match(html,/onclick="openNewUser\(\)"><svg[^]*?<\/svg> משתמש</);
  assert.equal((html.match(/<table class="dg( kebab-col)?">/g)||[]).length,2,'users + matrix'); assert.doesNotMatch(html,/role="tablist"|Add user|<th>User<\/th>|id="users-table"/);
  // ‏#1300 (console-full.html P.users): משתמש · תפקיד · נראה לאחרונה · MFA; כרטיס לכל תפקיד; המטריצה — מקופלת
  assert.match(html,/<th>משתמש<\/th><th>תפקיד<\/th><th class="c-opt">נראה לאחרונה<\/th><th>MFA<\/th><th><span class="sr">פעולות<\/span><\/th>/);
  assert.match(html,/<div class="c6 card"><div class="card-h"><span>מנהל<\/span><\/div><div class="card-b"><p class="cap">גישה מלאה/);
  assert.match(html,/<div class="c6 card"><div class="card-h"><span>הפצה<\/span><\/div><div class="card-b"><p class="cap">[^<]*אין לו כניסה לקונסולה/);
  assert.match(html,/<details class="c12 card perm-more"><summary class="card-h"><span>הרשאות מפורטות/,'the permission matrix is still on the page, folded');
  assert.match(html,/המנהל הפעיל האחרון אינו ניתן למחיקה או להורדה/,'the rule moved from the header to the matrix note');
  const a=row(html,'data-user="admin"'), y=row(html,'data-user="yossi"'), d=row(html,'data-user="deployer"');
  assert.match(a,/<b class="uname" title="נוצר 01\/08\/2026">admin<\/b><span class="sub">זה אתה<\/span>/); assert.match(a,/<td>מנהל<\/td>/); assert.doesNotMatch(a,/מושבת/);
  // אחרי apple-pending-4: כל פעולות השורה בתפריט ⋯ אחד; ההרסנית (מחיקה — חלון אישור) אחרונה, מסומנת "danger"
  // ‏#1300: כל שורה עם ⋯ (כמו המוקאפ); לעצמך — רק "שנה סיסמה" שמוביל ל"החשבון שלי" (השרת מסרב לתפקיד/השבתה/איפוס של עצמך, #1268)
  assert.deepEqual(menuOf(a),[['שנה סיסמה (החשבון שלי)','openAccountPage()']]);
  assert.match(y,/class="pill err">מושבת</); assert.deepEqual(menuOf(y).map((i)=>i[0]),['שנה תפקיד…','איפוס סיסמה…','הפעל משתמש','נתק את כל ההפעלות…','מחק משתמש…'],'#1300: the wording of MENUS.user');
  assert.ok(menuOf(y).some((i)=>i[1]==="userResetPassword('yossi')")); assert.doesNotMatch(y+d,/userPasswordSheet/,'#1268: no typed password for another user');
  assert.deepEqual(menuOf(y).at(-1),['מחק משתמש…',"userDeleteSheet('yossi')",'danger'],'a disabled admin is deletable — the server counts active admins');
  assert.ok(menuOf(y).some((i)=>i[1]==="userDisable('yossi', false)"));
  assert.match(d,/<td>הפצה<\/td>/); assert.ok(menuOf(d).some((i)=>i[1]==="userRoleSheet('deployer')")); assert.ok(menuOf(d).some((i)=>i[0]==='השבת משתמש…'&&i[1]==="userDisable('deployer', true)")); assert.deepEqual(menuOf(d).at(-1),['מחק משתמש…',"userDeleteSheet('deployer')",'danger']);
  assert.doesNotMatch(y+a+d,/class="btn sm/,'no inline buttons left in the table cells');
  // ‏#1008: כניסה אחרונה מ-/users; null = "לא נרשמה", לא תאריך מומצא
  assert.match(a,/<span title="17\/09\/2026 08:05"><bdi dir="ltr">10\.44\.10\.55<\/bdi> · 17\/09<\/span>/,'#1300: "address · when" like the mockup, the full time in the title');
  assert.match(y,/class="muted"[^>]*>לא נרשמה</); assert.doesNotMatch(html,/דורש API/);
  // המטריצה: שורות מהקוד. ‏#1073: deploy — "לא" לכניסה לקונסולה, "ממחשב הבנייה" למה שהקיוסק מגיש
  assert.match(html,/מה כל תפקיד רואה/); assert.match(html,/<th><\/th><th>מנהל<\/th><th>הפצה<\/th>/);
  assert.match(row(html,'כניסה לקונסולה (ניהול'),/class="st ok">כן<\/span><\/td><td><span class="st ">לא</);
  assert.match(row(html,'חדר המשכפלים: סבב, גל, WoL'),/class="st ok">כן<\/span><\/td><td><span class="st ok">ממחשב הבנייה</);
  assert.doesNotMatch(html,/לוגו|branding/,'#1302'); assert.match(row(html,'הרשאות, הגדרות, יומן'),/class="st ok">כן<\/span><\/td><td><span class="st ">לא</);
  assert.match(row(html,'דרייברים: צפייה, ייבוא ומחיקה'),/class="st ">לא</);
  assert.doesNotMatch(html,/צפייה בלבד/,'#1073: no console at all for deploy — nothing is "view only"');
  assert.match(html,/title="[^"]*room_operator[^"]*"/,'the endpoint dependency is visible on hover');
  assert.match(html,/deploy_no_console/,'the refusal code is on the login row');
  assert.equal(run('ROLE_MATRIX.length'),10);
  // עם מנהל פעיל נוסף — admin כבר לא "האחרון" אבל עדיין "זה אתה": רק סיסמה; yossi הפעיל מקבל הכול
  run('USERS[1].disabled=false');
  const h2=run('permissions()');
  assert.deepEqual(menuOf(row(h2,'data-user="yossi"')).map((i)=>i[0]),['שנה תפקיד…','איפוס סיסמה…','השבת משתמש…','נתק את כל ההפעלות…','מחק משתמש…']);
  assert.doesNotMatch(row(h2,'data-user="admin"'),/userDeleteSheet/);
  assert.ok(!requests.some((r)=>r.method!=='GET'),'rendering sends nothing');
});

test('permissions: the sheets call the same endpoints — role (PUT), disable (PUT), delete behind the typed username (DELETE), new user (POST); each reloads /users',async()=>{
  const {run,requests}=setup(); run('current="permissions"');
  await run('loadUsersData()'); run('permissions()');
  const puts=()=>requests.filter((r)=>r.method==='PUT');
  // ‏#1268: אין יותר הקלדת סיסמה בטבלת המשתמשים — לעצמך ב"החשבון שלי", לאחר "איפוס סיסמה…".
  assert.equal(run('typeof userPasswordSheet'),'undefined');
  run("userRoleSheet('deployer')"); let s=run('sheets.at(-1)'); assert.equal(s.fields[0].options.map((o)=>o.value).join(','),'deploy,admin');
  await s.onSubmit({role:'admin'}); assert.deepEqual(puts().at(-1).body,{role:'admin'});
  run("userDisable('deployer', true)"); s=run('sheets.at(-1)'); assert.match(s.note,/סשן פתוח שלו נסגר מיד/);
  await s.onSubmit(); assert.deepEqual(puts().at(-1).body,{disabled:true});
  run("userDeleteSheet('deployer')"); s=run('sheets.at(-1)'); assert.equal(s.danger,true); assert.equal(s.verify,undefined,'#1270: no typed name'); assert.equal(s.fields,undefined);
  await s.onSubmit(); assert.equal(requests.filter((r)=>r.method==='DELETE').at(-1).url,'/users/deployer');
  run('openNewUser()'); s=run('sheets.at(-1)'); assert.equal(s.fields.map((f)=>f.id).join(','),'username,password,role');
  await s.onSubmit({username:'newbie',password:'12345678',role:'deploy'}); assert.deepEqual(requests.filter((r)=>r.method==='POST').at(-1).body,{username:'newbie',password:'12345678',role:'deploy'});
  assert.equal(requests.filter((r)=>r.url==='/users'&&r.method==='GET').length,5,'initial + 4 reloads');
  assert.match(run('toasts.join("|")'),/התפקיד שונה.*המשתמש הושבת.*המשתמש deployer נמחק.*המשתמש newbie נוצר/);
});

test('#1268: "איפוס סיסמה…" — a confirm window (no typed word) → POST reset-password → the one-time password is shown once, LTR with "הצג", and copied from there',async()=>{
  const PW='Xy7-q2Lm9Pz';
  const {run,requests,ctx}=setup({'/users/deployer/reset-password':{new_password:PW}});
  run('current="permissions"'); await run('loadUsersData()');
  run("userResetPassword('deployer')");
  const s=run('sheets.at(-1)');
  assert.match(s.title,/לאפס את הסיסמה/); assert.equal(s.sub,'deployer');
  assert.equal(s.danger,true); assert.ok(!s.verify,'no typed-word confirmation'); assert.equal(s.submitLabel,'איפוס סיסמה');
  assert.match(s.note,/תוצג לך פעם אחת/); assert.match(s.note,/יחויב לבחור סיסמה חדשה/); assert.match(s.note,/ינותק מכל הדפדפנים/);
  assert.ok(!requests.some((r)=>r.method==='POST'),'nothing is sent before the confirmation');
  const next=await s.onSubmit({});
  assert.deepEqual(requests.filter((r)=>r.method==='POST'),[{url:'/users/deployer/reset-password',full:'/api/console/users/deployer/reset-password',method:'POST',body:{}}]);
  assert.equal(requests.filter((r)=>r.url==='/users'&&r.method==='GET').length,2,'the table reloads after the reset');
  assert.equal(run('sheets.length'),1,'the result opens only after the confirm window closes');
  assert.equal(typeof next,'function'); next();
  const r=run('sheets.at(-1)');
  assert.match(r.title,/הסיסמה הזמנית/); assert.equal(r.sub,'deployer');
  assert.match(r.note,/מסור אותה למשתמש — בכניסה הבאה הוא יבחר סיסמה חדשה/);
  // אותו שדה כמו בכניסה: LTR, "הצג" בתוך השדה מימין, מוסתר כברירת מחדל, לקריאה בלבד.
  const field=r.note.match(/<div class="n-f" data-pwf>.*?<\/div><\/div>/s); assert.ok(field,'the password field component');
  assert.match(field[0],/<input class="n-in" type="password" dir="ltr" id="[^"]+" autocomplete="off" value="Xy7-q2Lm9Pz" readonly>/);
  assert.match(field[0],/<button type="button" class="pw-eye"[^>]*>הצג<\/button>/);
  assert.equal(r.submitLabel,'העתקה'); assert.equal(r.cancelLabel,'סגירה');
  // שום מקום אחר לא נושא את הסיסמה: לא חלון האישור, לא הודעה, לא הכותרות.
  assert.ok(!JSON.stringify(s).includes(PW)&&!r.title.includes(PW)&&!r.sub.includes(PW));
  assert.ok(!run('toasts.join("|")').includes(PW));
  const copied=[]; ctx.navigator={clipboard:{writeText:async(t)=>{copied.push(t);}}};
  await r.onSubmit({}); assert.deepEqual(copied,[PW]); assert.match(run('toasts.at(-1)'),/הסיסמה הועתקה/);
  // בלי לוח (HTTP לא מאובטח) — החלון לא נסגר בשקט: שגיאה, והסיסמה עדיין שם להעתקה ידנית.
  ctx.navigator={};
  await assert.rejects(r.onSubmit({}),/ההעתקה אינה זמינה/);
  ctx.navigator={clipboard:{writeText:async()=>{throw new Error('denied');}}};
  await assert.rejects(r.onSubmit({}),/ההעתקה נכשלה/);
});

test('#1268: a reset answer without a password is a failure in the confirm window, not an empty result; yourself gets no reset',async()=>{
  const {run,requests}=setup({'/users/deployer/reset-password':{ok:true}});
  run('current="permissions"'); await run('loadUsersData()');
  run("userResetPassword('deployer')");
  await assert.rejects(run('sheets.at(-1)').onSubmit({}),/לא החזיר סיסמה/);
  assert.equal(run('sheets.length'),1,'no result window');
  run("userResetPassword('admin')");
  assert.equal(run('sheets.length'),1,'the server refuses to reset yourself — the UI does not offer it');
  assert.equal(requests.filter((r)=>r.method==='POST').length,1);
});

test('permissions: /users that cannot be read is its own state (red note, "לא נקרא" pill), not "no users"; an empty list is an empty state',async()=>{
  let s=setup({'/users':new Error('DB locked')}); s.run('current="permissions"');
  await s.run('loadUsersData()');
  let html=s.run('permissions()');
  assert.notEqual(html,s.run('pagePlaceholder()')); assert.match(html,/class="pill err">לא נקרא</); assert.match(html,/note err.*DB locked/); assert.doesNotMatch(html,/אין משתמשים/);
  s=setup({'/users':[]}); s.run('current="permissions"');
  await s.run('loadUsersData()'); html=s.run('permissions()');
  assert.match(html,/class="empty">אין משתמשים/); assert.match(html,/<div class="obj-sub"[^>]*>0 משתמשים<\/div>/);
});

/* ---------- יומן ---------- */

test('logs: one table — time (hh:mm:ss today, date otherwise), severity dot from the server severity field (#968), sentence + text, who ("המערכת" when empty), details drawer; filter bar in the page; no tabs, no modal, no raw event column',async()=>{
  const {run,requests}=setup(); run('current="logs"');
  await run('loadJournalData()');
  assert.ok(requests.some((r)=>r.url==='/journal/events'),'event types for the filter');
  const j=requests.find((r)=>r.url==='/journal'); assert.equal(new URL(j.full,'http://x').searchParams.get('limit'),'200'); assert.equal(new URL(j.full,'http://x').searchParams.get('from'),null,'no range by default');
  const html=run('logs()'); balanced(html);
  assert.match(html,/class="obj-name">יומן</); assert.match(html,/4 אירועים מוצגים \(הסך לא נמסר\) · בלי סינון — האחרונים/,'no X-Journal-Total → not a number');
  assert.match(html,/<th>מה קרה<\/th><th>מי<\/th><th>זמן<\/th><th>חומרה<\/th><th><\/th>/);
  assert.doesNotMatch(html,/role="tablist"|openLogFilter|<th>event<\/th>|עקרון התצוגה|Audit/);
  const r0=row(html,'LAB303-03');
  assert.match(r0,/<span class="mono">09:44:10<\/span>/,'today → hh:mm:ss only'); assert.match(r0,/title="דורש תשומת לב"><span class="pill warn">אזהרה</); assert.match(r0,/&lt;b&gt;x&lt;\/b&gt;/,'text is escaped');
  assert.match(r0,/<td>המערכת<\/td>/); assert.match(r0,/onclick="openLogDetail\(0\)" tabindex="0"/);
  const r2=row(html,'timeout');
  assert.match(r2,/<span class="mono">16\/09\/2026 08:12:44<\/span>/); assert.match(r2,/<span class="pill err">שגיאה</,'stage 3: severity is a pill WITH a word, not a colour dot alone');
  assert.match(row(html,'Office 2024'),/pill info">מידע</); assert.match(row(html,'מחשב סיים לכתוב'),/pill ok">הושלם</); assert.doesNotMatch(html,/<span class="st (ok|info|warn|err)" title=/,'no colour-only dot');
  // שורת סינון בדף
  assert.match(html,/<div class="dg-bar"><input type="search" value="" placeholder="חיפוש בטקסט[^"]*" aria-label="חיפוש ביומן" onchange="logFilter\('q', this\.value\.trim\(\)\)"/);
  assert.match(html,/<select aria-label="סוג אירוע" onchange="logFilter\('event', this\.value\)"><option value="" selected>כל סוגי האירועים<\/option><option value="session_open">סבב נפתח</);
  assert.match(html,/<select aria-label="משתמש"[^>]*><option value="" selected>כל המשתמשים<\/option><option value="deployer">deployer<\/option><option value="nadav">nadav</,'users from the rows');
  assert.match(html,/<select aria-label="טווח זמן"[^>]*><option value="" selected>כל הזמן<\/option><option value="today">היום</);
  assert.match(html,/class="n">4 אירועים</); assert.match(html,/זה הכול לסינון הזה/,'fewer rows than the limit → nothing more to load');
  assert.match(html,/<a class="btn" href="\/api\/console\/journal\.csv" download>ייצוא CSV</,'#1008: the export, same filter');
  run('openLogDetail(2)');
  assert.match(run('drawer'),/פרטי אירוע\|/); assert.match(run('drawer'),/<span class="mono">storage_transfer_failed<\/span>/); assert.match(run('drawer'),/class="st err">כשל \/ סירוב/);
});

test('logs: filters go to the server as query parameters (q, event, user, from for today/7d, to+:59 for a custom range), the CSV link carries them, "more" pages with before= and appends (#1008); truncated header → warning',async()=>{
  const {run,requests,fixtures}=setup(); run('current="logs"');
  await run('loadJournalData()');
  const q=()=>new URL(requests.filter((r)=>r.url==='/journal').at(-1).full,'http://x').searchParams;
  run("logFilter('q','LAB303')"); await new Promise((r)=>setImmediate(r)); assert.equal(q().get('q'),'LAB303');
  run("logFilter('event','session_open')"); await new Promise((r)=>setImmediate(r)); assert.equal(q().get('event'),'session_open'); assert.equal(q().get('q'),'LAB303');
  run("logFilter('user','nadav')"); await new Promise((r)=>setImmediate(r)); assert.equal(q().get('user'),'nadav');
  run("logFilter('range','today')"); await new Promise((r)=>setImmediate(r)); assert.equal(q().get('from'),today);
  run("logFilter('range','7d')"); await new Promise((r)=>setImmediate(r)); assert.match(q().get('from'),/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$/);
  const before=requests.length; run("logFilter('range','custom')"); await new Promise((r)=>setImmediate(r));
  assert.equal(requests.length,before,'custom without dates: no request yet, the inputs appear'); assert.match(run('logs()'),/type="datetime-local" aria-label="מתאריך"/);
  run("logFilter('since','2026-09-01T10:00')"); await new Promise((r)=>setImmediate(r)); run("logFilter('until','2026-09-13T12:00')"); await new Promise((r)=>setImmediate(r));
  assert.equal(q().get('from'),'2026-09-01T10:00'); assert.equal(q().get('to'),'2026-09-13T12:00:59','inclusive end minute');
  assert.match(run('logs()'),/מסונן: &quot;LAB303&quot; · סבב נפתח · nadav · 2026-09-01T10:00 – 2026-09-13T12:00/);
  // ‏#1008: "עוד" = הדף הבא עם before=<id האחרון>, ומוסיף — לא limit גדל
  assert.match(run('logs()'),/href="\/api\/console\/journal\.csv\?q=LAB303&amp;event=session_open&amp;user=nadav&amp;from=2026-09-01T10%3A00&amp;to=2026-09-13T12%3A00%3A59" download>/,'the export carries the filter, without limit');
  fixtures['/journal']=Array.from({length:200},(_,i)=>({id:1000-i,ts:today+'T10:00:00',user:'u',event:'login',label:'L'+i,text:''}));
  fixtures.__total=350;
  run("logFilter('q','')"); await new Promise((r)=>setImmediate(r)); assert.equal(q().get('before'),null,'a filter change starts from the top');
  assert.match(run('logs()'),/200 מתוך 350 אירועים/); assert.match(run('logs()'),/onclick="logMore\(\)">עוד 200</);
  fixtures['/journal']=Array.from({length:150},(_,i)=>({id:800-i,ts:today+'T09:00:00',user:'u',event:'login',label:'M'+i,text:''}));
  run('logMore()'); await new Promise((r)=>setImmediate(r));
  assert.equal(q().get('before'),'801'); assert.equal(q().get('limit'),'200');
  assert.equal(run('LOG.rows.length'),350,'appended, not replaced');
  assert.match(run('logs()'),/350 מתוך 350 אירועים/); assert.match(run('logs()'),/זה הכול לסינון הזה/);
  fixtures.__truncated=true; await run('loadJournalData()');
  const html=run('logs()'); assert.match(html,/class="pill warn">חיפוש חלקי</); assert.match(html,/note warn.*החיפוש כיסה רק את השורות האחרונות/);
});

test('logs: /journal unreadable is a red note (not an empty journal); /journal/events unreadable only degrades the type filter; empty result says "no events match"',async()=>{
  let s=setup({'/journal':new Error('DB down')}); s.run('current="logs"');
  await s.run('loadJournalData()');
  let html=s.run('logs()');
  assert.notEqual(html,s.run('pagePlaceholder()')); assert.match(html,/class="pill err">לא נקרא</); assert.match(html,/note err.*DB down/); assert.doesNotMatch(html,/היומן ריק|<table/);
  assert.match(s.run('toasts.join("|")'),/DB down/);
  s=setup({'/journal/events':new Error('no'),'/journal':[]}); s.run('current="logs"');
  await s.run('loadJournalData()'); html=s.run('logs()');
  assert.match(html,/סוג: לא נקרא/); assert.match(html,/class="empty">היומן ריק/);
  s.run("LOG.q='x'"); assert.match(s.run('logs()'),/אין אירועים שתואמים לסינון/);
});

/* ---------- WoL למחשב בודד (#984) ---------- */

test('WoL: builder card, cloner card (object and room), capture card, drawer, builders header — per-machine POST /machines/{mac}/wake; the room keeps /room/wake; classroom drawer says v2',async()=>{
  const {run,requests}=setup();
  run('CAPTURE_TASKS=[]; CAPTURE_TASKS_READ=true; MONITOR_ROWS=[]; ROOM=null');
  const b=run(`builderCardHtml(findMachine('${MAC_B}'), true)`);
  assert.match(b,new RegExp(`onclick="wakeMachine\\('${encodeURIComponent(MAC_B)}'\\)">דרך WoL<`),'#1485 (P.build): a machine that is not connected says how to wake it');
  assert.doesNotMatch(b,/בקרוב/);
  const c=run(`clonerCardHtml(findMachine('${MAC_C}'), true)`);
  assert.match(c,new RegExp(`onclick="wakeMachine\\('${encodeURIComponent(MAC_C)}'\\)"[^>]*>WoL<`)); assert.doesNotMatch(c,/wakeRoom|אין WoL למחשב יחיד/);
  run('DEPLOY.form={}; ROOM={round:null,machines:[{mac:"'+MAC_C+'",awake:false,joined:false}]}');
  const cr=run(`clonerCardHtml(findMachine('${MAC_C}'), true, true)`);
  assert.match(cr,/wakeMachine\(/,'room card, not connected → per-machine WoL');
  const bv=run("buildersView(GROUPS[0], [findMachine('"+MAC_B+"')], 0)");
  assert.doesNotMatch(bv.actions,/wakeGroup|בקרוב/,'#1485 (P.build): the header has "+ מחשב בנייה" only; the group WoL stays in the machines table (below)');
  const cl=run("clonersView({id:'grp_CLONERS',label:'x',role:'cloner'}, [findMachine('"+MAC_C+"')])");
  assert.match(cl.body,new RegExp(`מחשבים שלא דיווחו[^]*wakeMachine\\('${encodeURIComponent(MAC_C)}'\\)">WoL<`),'#1485 (P.cloners): WoL per machine in "machines that did not report"');
  const dB=run(`machineDrawerHtml(findMachine('${MAC_B}'))`), dC=run(`machineDrawerHtml(findMachine('${MAC_C}'))`), dS=run(`machineDrawerHtml(findMachine('${MAC_S}'))`);
  assert.match(dB,/wakeMachine\(/); assert.match(dC,/wakeMachine\(/); assert.doesNotMatch(dC,/wakeRoom/);
  assert.match(dS,/title="v2">WoL ואתחול מרחוק — לתחנות כיתה ב-v2</); assert.doesNotMatch(dS,/wakeMachine|בקרוב">Wake/);
  run("MCH.history={}; MCH.sel=new Set()");
  const g=run("groupRowHtml(GROUPS[0], [findMachine('"+MAC_B+"')]).html");
  assert.match(g,/wakeGroup\('grp_BUILD'\)">WoL לבנייה</);
  assert.equal(requests.filter((r)=>r.url.endsWith('/wake')).length,0,'rendering sends nothing');
});

test('WoL: the request and the toast — "sent (N)" never "woke up"; failed → reasons; group → /groups/grp_BUILD/wake; 403 classroom from the server; 404 = older server',async()=>{
  const {run,requests,fixtures}=setup();
  await run(`wakeMachine('${encodeURIComponent(MAC_B)}')`);
  let r=requests.at(-1); assert.equal(r.url,'/machines/'+encodeURIComponent(MAC_B)+'/wake','MAC encoded in the path (FastAPI decodes)'); assert.equal(r.method,'POST');
  assert.equal(run('toasts.at(-1)'),'WoL נשלח ל-בנייה 1 (1)'); assert.doesNotMatch(run('toasts.at(-1)'),/התעורר|הוער/);
  await run(`wakeMachine('${encodeURIComponent(MAC_C)}')`);
  assert.equal(run('toasts.at(-1)'),'WoL נשלח ל-מחשב 1 (0), 1 נכשלו — אין MAC ברשומה');
  await run("wakeGroup('grp_BUILD')");
  r=requests.at(-1); assert.equal(r.url,'/groups/grp_BUILD/wake'); assert.equal(run('toasts.at(-1)'),'WoL נשלח ל-מחשבי בנייה (2)');
  fixtures['/groups/grp_LAB1/wake']=403; await run("wakeGroup('grp_LAB1')");
  assert.equal(run('toasts.at(-1)'),'WoL נכשל: WoL לתחנות כיתה — v2');
  fixtures['/machines/'+encodeURIComponent(MAC_B)+'/wake']=404; await run(`wakeMachine('${encodeURIComponent(MAC_B)}')`);
  assert.match(run('toasts.at(-1)'),/דורש שרת חדש יותר \(#984\)/);
  fixtures['/machines/'+encodeURIComponent(MAC_B)+'/wake']=new Error('שידור נכשל'); await run(`wakeMachine('${encodeURIComponent(MAC_B)}')`);
  assert.equal(run('toasts.at(-1)'),'WoL נכשל: שידור נכשל');
  await run("wakeMachine('zz')"); assert.equal(run('toasts.at(-1)'),'מכונה לא נמצאה');
  assert.equal(requests.filter((r)=>r.url==='/room/wake').length,0,'the per-machine path never touches the room endpoint');
});

test('stage 3: the settings "מראה" card sets the theme explicitly — applies it now and saves it on the server (PUT /me/theme); an unknown value does nothing',async()=>{
  const {run,requests}=setup(); run('current="settings"');
  await run('loadSettingsData()');
  await run("setThemePref('dark')");
  assert.equal(run('themePref'),'dark');
  const put=requests.find((r)=>r.url==='/me/theme'); assert.ok(put,'saved on the server'); assert.equal(put.method,'PUT'); assert.deepEqual(put.body,{theme:'dark'});
  assert.match(run('settings()'),/aria-pressed="true">כהה</);
  const n=requests.length; run("setThemePref('rainbow')");
  assert.equal(requests.length,n,'unknown value: no request'); assert.equal(run('themePref'),'dark');
});

test('stage 3: the users table MFA column is a pill — built-in admin "מקומי · ללא MFA", MFA on "פעיל", admin without "כבוי" (warn), a deploy user "—" (never "כבוי")',()=>{
  const {run}=setup(); run('current="permissions"');
  const cell=(u)=>run(`userRowHtml(${JSON.stringify(u)}).cells[3]`);
  assert.equal(cell({username:'a',role:'admin',is_builtin:true}),'<span class="pill info">מקומי · ללא MFA</span>');
  assert.equal(cell({username:'b',role:'admin',mfa_enabled:true}),'<span class="pill ok">פעיל</span>');
  assert.equal(cell({username:'c',role:'admin'}),'<span class="pill warn">כבוי</span>');
  assert.equal(cell({username:'d',role:'deploy'}),'—');
  assert.equal(run('userMfaLabel({is_builtin:true})'),'מקומי','the account page keeps its own label');
});

test('final mockup: every destructive users window is a short question, the subject in the sub, consequences as an icon list, a named red button',async()=>{
  const {run}=setup(); run('current="permissions"');
  await run('loadUsersData()'); run('permissions()');
  for (const [call,title,label] of [["userDeleteSheet('deployer')","למחוק את המשתמש?","מחיקת המשתמש"],["userResetPassword('deployer')","לאפס את הסיסמה?","איפוס סיסמה"],
    ["userRevokeSessions('deployer')","לנתק את כל ההפעלות?","ניתוק ההפעלות"],["userDisable('deployer', true)","להשבית את המשתמש?","השבתת משתמש"]]) {
    run(call); const sh=run('sheets.at(-1)');
    assert.equal(sh.title,title,call); assert.equal(sh.sub,'deployer',call); assert.equal(sh.submitLabel,label,call); assert.equal(sh.danger,true,call);
    assert.match(sh.note,/<ul class="n-impact">/,call); assert.equal(sh.verify,undefined,'no typed word');
  }
});
