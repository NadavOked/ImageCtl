// ‏#954 גל 6: הגדרות (settings.md) · הרשאות (permissions.md) · יומן (logs.md)
// + WoL למחשב בודד ולקבוצת הבנייה (#984). מוניטור ודרייברים — בקבצים שלהם.
// בלי דפדפן: console.js + net.js + progress.js רצים ב-vm מול DOM מזערי ו-fetch
// מזויף. עיקרון 5: כל endpoint שלא נקרא הוא מצב משלו, לא "ריק"; "נשלח" ≠
// "התעוררה" (#528). עיקרון 7: מחיקת משתמש = הקלדת שם.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const root=path.resolve(__dirname,'../server/static');

const MAC_B='c8:d9:d2:0b:fe:32', MAC_C='a0:48:1c:8a:18:40', MAC_S='00:11:22:33:44:55';
const SETTINGS={recovery_require_login:'true',session_wait_seconds:'300',console_idle_seconds:'600',class_deploy_enabled:'false',update_enabled:'false',server_name:'שרת המכללה'};
const USERS=[{username:'admin',role:'admin',created_at:'2026-08-01T10:00:00',disabled:false},
  {username:'yossi',role:'admin',created_at:'2026-09-17T08:00:00',disabled:true},
  {username:'deployer',role:'deploy',created_at:'2026-09-17T09:00:00',disabled:false}];
const today=new Date().toISOString().slice(0,10);
const JOURNAL=[
  {ts:today+'T09:44:10',user:'',event:'boot_loop_local',label:'מחשב אתחל שוב ושוב — נשלח לדיסק המקומי',text:'LAB303-03 <b>x</b>'},
  {ts:today+'T09:42:31',user:'nadav',event:'session_open',label:'סבב נפתח',text:'Office 2024 לכיתה 303'},
  {ts:'2026-09-16T08:12:44',user:'',event:'storage_transfer_failed',label:'העברת אימג\' לסניף נכשלה',text:'timeout'},
  {ts:'2026-09-16T08:10:02',user:'deployer',event:'client_done',label:'מחשב סיים לכתוב',text:''},
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
    '/branding/logo':204,
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
      const hdr={get:(h)=>h==='X-Journal-Search-Truncated'&&fixtures.__truncated?'true':null};
      if(f instanceof Error) return {status:500,ok:false,headers:hdr,json:async()=>({detail:f.message})};
      if(f===403) return {status:403,ok:false,headers:hdr,json:async()=>({detail:'WoL לתחנות כיתה — v2'})};
      if(f===404) return {status:404,ok:false,headers:hdr,json:async()=>({detail:'Not Found'})};
      if(f===204) return {status:204,ok:true,headers:hdr,json:async()=>({})};
      return {status:200,ok:true,headers:hdr,json:async()=>f ?? {ok:true}};
    }});
  for(const f of ['progress.js','console.js','net.js']) vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),ctx);
  const run=s=>vm.runInContext(s,ctx);
  run('ME={username:"admin",role:"admin",server_name:"srv",idle_seconds:600,version:"v0.35.0",capabilities:{}}; globalThis.sheets=[]; globalThis.toasts=[]; sheet = o => { sheets.push(o); }; toast=(m)=>{ toasts.push(m); }; renderCurrent=()=>{ globalThis.rendered=(globalThis.rendered||0)+1; }; globalThis.selected=[]; selectPageById=(id)=>{ selected.push(id); current=id; }; globalThis.drawer=""; openDrawer=(t,b)=>{ drawer=t+"|"+b; }; globalThis.idleRestarted=0; startIdleWatch=()=>{ idleRestarted++; };');
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
const row=(html,needle)=>{const i=html.indexOf(needle); assert.ok(i>=0,'missing row: '+needle); const s=html.lastIndexOf('<tr',i); return html.slice(s,html.indexOf('</tr>',i)+5);};

/* ---------- הרישום: חמישה עמודים own, בלי הקוד הישן ---------- */

test('the five pages are own pages; monitor/settings/permissions/logs without tabs, drivers with two; the old renderers are gone',()=>{
  const {run}=setup();
  for(const id of ['monitor','settings','permissions','logs','drivers']) assert.equal(run(`pages.${id}.own`),true,id);
  for(const id of ['monitor','settings','permissions','logs']) assert.equal(run(`pages.${id}.tabs.length`),0,id+' has no tabs');
  assert.equal(JSON.stringify(run('pages.drivers.tabs')),JSON.stringify(['חבילות','כיסוי לפי מכונה']));
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

test('settings: a row per setting in Hebrew (label + meaning | control), switches with role="switch", identity_check on when missing (#855), save/cancel disabled until a change',async()=>{
  const {run,requests}=setup(); run('current="settings"');
  await run('loadSettingsData()');
  assert.ok(requests.some((r)=>r.url==='/settings')); assert.ok(requests.some((r)=>r.url==='/branding/logo'),'logo state is read');
  const html=run('settings()'); balanced(html);
  assert.match(html,/class="obj-name">הגדרות</); assert.doesNotMatch(html,/>Settings<|Console policy|form-grid|set-login/);
  assert.match(html,/id="set-save" onclick="saveSettings\(\)" disabled>שמור</); assert.match(html,/id="set-cancel" onclick="cancelSettings\(\)" disabled>בטל שינויים</);
  const srow=(k)=>{const i=html.indexOf(`id="srow-${k}"`); assert.ok(i>=0,k); const j=html.indexOf('<div class="srow',i+10); return html.slice(i,j>0?j:html.indexOf('</div></div></div>',i));};
  assert.match(srow('server_name'),/<b>שם השרת<\/b>/); assert.match(srow('server_name'),/type="text" id="set-server_name" value="שרת המכללה"/);
  assert.match(srow('console_idle_seconds'),/type="number" id="set-console_idle_seconds" value="600" min="60"/); assert.match(srow('console_idle_seconds'),/שניות \(מינימום 60\)/);
  assert.match(srow('session_wait_seconds'),/value="300" min="30"/);
  assert.match(srow('recovery_require_login'),/class="sw on" role="switch" aria-checked="true" aria-label="שחזור תחנה בודדת דורש כניסה" onclick="settingsToggle\('recovery_require_login', this\)"/); assert.match(srow('recovery_require_login'),/מומלץ דלוק; כיבוי רק להדגמה/);
  assert.match(srow('class_deploy_enabled'),/class="sw" role="switch" aria-checked="false"/);
  assert.match(srow('update_enabled'),/aria-checked="false"/); assert.match(srow('update_enabled'),/selectPageById\('health'\)">כרטיס העדכון — בבריאות ושירותים</,'the update card itself lives in health (wave 5)');
  assert.match(srow('identity_check'),/class="sw on" role="switch" aria-checked="true"/,'#855: missing = on');
  assert.match(srow('identity_check'),/הכיבוי נרשם ביומן/);
  assert.equal((html.match(/role="switch"/g)||[]).length,4);
  assert.doesNotMatch(html,/role="tablist"/);
  // מיתוג: 204 = אין לוגו → ברירת המחדל, בלי "הסרה"
  assert.match(html,/class="logo-box"><img src="logo\.svg\?v=4\.0"/); assert.match(html,/class="st ">ברירת המחדל</); assert.doesNotMatch(html,/settingsLogoClear/);
  assert.match(html,/id="logo-input" class="hidden" accept="image\/png,image\/jpeg,image\/webp,image\/svg\+xml"/);
});

test('settings: a change marks the row and enables save; save posts only the changed keys as strings, applies idle/server name locally, and reloads; cancel drops the draft',async()=>{
  const {run,requests,node}=setup(); run('current="settings"');
  await run('loadSettingsData()'); run('settings()');
  run("settingsSet('console_idle_seconds','900')");
  assert.equal(node('#set-save').disabled,false); assert.equal(node('#set-cancel').disabled,false);
  assert.match(run('settings()'),/class="srow changed" id="srow-console_idle_seconds"/); assert.match(run('settings()'),/class="pill warn">שינויים לא נשמרו</);
  run("settingsSet('console_idle_seconds','600')");   // חזרה לערך המקורי = לא שינוי
  assert.equal(run('settingsDirty()'),false); assert.equal(node('#set-save').disabled,true);
  // מתג: הופך את הערך ומעדכן את הכפתור עצמו
  const btn=node('#sw'); btn.classList.toggle=function(c,on){ this.on=on; }; btn.nextElementSibling=node('#swcap');
  run("settingsToggle('recovery_require_login', document.querySelector('#sw'))");
  assert.equal(run('SETTINGS_DRAFT.recovery_require_login'),false); assert.equal(btn['@aria-checked'],'false'); assert.equal(node('#swcap').textContent,'כבוי');
  run("settingsSet('server_name','  שרת חדש '); settingsSet('console_idle_seconds','900'); settingsSet('identity_check', false)");
  await run('saveSettings()');
  const p=requests.find((r)=>r.url==='/settings'&&r.method==='POST');
  assert.deepEqual(p.body,{server_name:'שרת חדש',console_idle_seconds:'900',recovery_require_login:'false',identity_check:'false'},'only the changed keys, strings, trimmed');
  assert.equal(run('ME.idle_seconds'),900); assert.equal(run('idleRestarted'),1); assert.equal(run('ME.server_name'),'שרת חדש');
  assert.equal(run('settingsDirty()'),false); assert.match(run('toasts.at(-1)'),/נשמר — 4 הגדרות/);
  assert.equal(requests.filter((r)=>r.url==='/settings'&&r.method==='GET').length,2,'re-read after save');
  // ביטול
  run("settingsSet('session_wait_seconds','30'); cancelSettings()");
  assert.equal(run('settingsDirty()'),false);
});

test('settings: validation refuses below the minimum and an empty server name without a request; /settings unreadable is a red note, not defaults',async()=>{
  const {run,requests}=setup(); run('current="settings"');
  await run('loadSettingsData()');
  run("settingsSet('console_idle_seconds','30')"); await run('saveSettings()');
  assert.match(run('toasts.at(-1)'),/ניתוק אוטומטי בחוסר פעילות: מספר שלם, 60 לפחות/);
  run("SETTINGS_DRAFT={}; settingsSet('server_name','   ')"); await run('saveSettings()');
  assert.match(run('toasts.at(-1)'),/שם השרת: לא יכול להיות ריק/);
  assert.ok(!requests.some((r)=>r.url==='/settings'&&r.method==='POST'),'nothing was posted');
  const s=setup({'/settings':new Error('403')}); s.run('current="settings"');
  await s.run('loadSettingsData()');
  const html=s.run('settings()');
  assert.notEqual(html,s.run('pagePlaceholder()')); assert.match(html,/class="pill err">לא נקרא</); assert.match(html,/note err.*403/);
  assert.doesNotMatch(html,/role="switch"|id="set-save"/,'no controls over values we do not have');
});

test('settings: logo upload posts the file with its type and reloads; a refused SVG is a toast with the reason; removal is behind confirmSheet → DELETE',async()=>{
  const {run,requests,fixtures}=setup({'/branding/logo':{}}); run('current="settings"');   // 200 = יש לוגו
  await run('loadSettingsData()');
  let html=run('settings()');
  assert.match(html,/class="st ok">לוגו מותאם</); assert.match(html,/settingsLogoClear\(\)">הסרה</); assert.match(html,/<img src="\/api\/console\/branding\/logo\?t=\d+" alt="לוגו">/);
  await run('settingsLogoUpload({files:[{type:"image/png",name:"l.png"}],value:"x"})');
  const up=requests.find((r)=>r.url==='/branding/logo'&&r.method==='POST'); assert.ok(up,'POST /branding/logo');
  assert.match(run('toasts.at(-1)'),/הלוגו הוחלף/);
  fixtures['/branding/logo']=new Error('SVG עם script נדחה');
  await run('settingsLogoUpload({files:[{type:"image/svg+xml",name:"l.svg"}],value:"x"})');
  assert.match(run('toasts.at(-1)'),/הלוגו נדחה: SVG עם script נדחה/);
  fixtures['/branding/logo']={};
  run('settingsLogoClear()');
  const sh=run('sheets.at(-1)'); assert.match(sh.title,/הסרת הלוגו/);
  await sh.onSubmit();
  assert.ok(requests.some((r)=>r.url==='/branding/logo'&&r.method==='DELETE'));
});

/* ---------- הרשאות ---------- */

test('permissions: one users table (name, role pill, state, created, hover actions), "this is you" gets password only, the last active admin cannot be deleted/demoted/disabled, matrix from the code',async()=>{
  const {run,requests}=setup(); run('current="permissions"');
  await run('loadUsersData()');
  const html=run('permissions()'); balanced(html);
  assert.match(html,/3 משתמשים · 1 מנהלים פעילים · 1 מפעילי הפצה · המנהל האחרון אינו ניתן למחיקה או להורדה/);
  assert.match(html,/onclick="openNewUser\(\)">\+ משתמש</);
  assert.equal((html.match(/<table class="dg( acts-on)?">/g)||[]).length,2,'users + matrix'); assert.doesNotMatch(html,/role="tablist"|Add user|<th>User<\/th>|id="users-table"/);
  assert.match(html,/<th>משתמש<\/th><th>תפקיד<\/th><th>מצב<\/th><th>נוצר<\/th><th><\/th>/);
  const a=row(html,'data-user="admin"'), y=row(html,'data-user="yossi"'), d=row(html,'data-user="deployer"');
  assert.match(a,/<span class="name">admin<\/span><span class="sub">זה אתה<\/span>/); assert.match(a,/class="pill info">מנהל</); assert.match(a,/class="st ok">פעיל</); assert.match(a,/01\/08\/2026/);
  assert.match(a,/userPasswordSheet\('admin'\)">סיסמה</); assert.doesNotMatch(a,/userRoleSheet|userDisable|userDeleteSheet/,'self: password only (server refuses role/disable on yourself; and admin is the last active admin)');
  assert.match(y,/class="st warn">מושבת</); assert.match(y,/userDisable\('yossi', false\)">הפעל</); assert.match(y,/class="btn sm danger" onclick="userDeleteSheet\('yossi'\)">מחיקה</,'a disabled admin is deletable — the server counts active admins');
  assert.match(d,/class="pill ">הפצה</); assert.match(d,/userRoleSheet\('deployer'\)">תפקיד</); assert.match(d,/userDisable\('deployer', true\)">השבת</); assert.match(d,/userDeleteSheet\('deployer'\)">מחיקה</);
  assert.match(html,/כניסה אחרונה ומאיפה — דורש API/,'not invented');
  // המטריצה: שורות מהקוד, deploy מקבל "כן" רק במה ש-current_user/round_operator/room_operator
  assert.match(html,/<span>מה כל תפקיד רואה/); assert.match(html,/<th><\/th><th>מנהל<\/th><th>הפצה<\/th>/);
  assert.match(row(html,'חדר המשכפלים: סבב, גל, WoL'),/class="st ok">כן<\/span><\/td><td><span class="st ok">כן</);
  assert.match(row(html,'הרשאות, הגדרות, יומן, לוגו'),/class="st ok">כן<\/span><\/td><td><span class="st ">לא</);
  assert.match(row(html,'דרייברים: ייבוא ומחיקה'),/class="st ">צפייה בלבד</);
  assert.match(html,/title="[^"]*room_operator[^"]*"/,'the endpoint dependency is visible on hover');
  assert.equal(run('ROLE_MATRIX.length'),10);
  // עם מנהל פעיל נוסף — admin כבר לא "האחרון" אבל עדיין "זה אתה": רק סיסמה; yossi הפעיל מקבל הכול
  run('USERS[1].disabled=false');
  const h2=run('permissions()');
  assert.match(row(h2,'data-user="yossi"'),/userRoleSheet\('yossi'\)">תפקיד<\/button><button class="btn sm" onclick="userDisable\('yossi', true\)">השבת<\/button><button class="btn sm danger" onclick="userDeleteSheet\('yossi'\)">מחיקה</);
  assert.doesNotMatch(row(h2,'data-user="admin"'),/userDeleteSheet/);
  assert.ok(!requests.some((r)=>r.method!=='GET'),'rendering sends nothing');
});

test('permissions: the sheets call the same endpoints — password (PUT), role (PUT), disable (PUT), delete behind the typed username (DELETE), new user (POST); each reloads /users',async()=>{
  const {run,requests}=setup(); run('current="permissions"');
  await run('loadUsersData()'); run('permissions()');
  const puts=()=>requests.filter((r)=>r.method==='PUT');
  run("userPasswordSheet('deployer')"); let s=run('sheets.at(-1)'); assert.equal(s.fields[0].type,'password'); assert.equal(s.fields[0].confirm,'אימות הסיסמה החדשה');
  await s.onSubmit({password:'12345678'}); assert.deepEqual(puts().at(-1),{url:'/users/deployer',full:'/api/console/users/deployer',method:'PUT',body:{password:'12345678'}});
  run("userRoleSheet('deployer')"); s=run('sheets.at(-1)'); assert.equal(s.fields[0].options.map((o)=>o.value).join(','),'deploy,admin');
  await s.onSubmit({role:'admin'}); assert.deepEqual(puts().at(-1).body,{role:'admin'});
  run("userDisable('deployer', true)"); s=run('sheets.at(-1)'); assert.match(s.sub,/לא יוכל להיכנס, וסשן פתוח שלו נסגר מיד/);
  await s.onSubmit(); assert.deepEqual(puts().at(-1).body,{disabled:true});
  run("userDeleteSheet('deployer')"); s=run('sheets.at(-1)'); assert.equal(s.danger,true); assert.equal(s.verify.mustEqual,'deployer'); assert.match(s.verify.label,/שם המשתמש/);
  await s.onSubmit(); assert.equal(requests.filter((r)=>r.method==='DELETE').at(-1).url,'/users/deployer');
  run('openNewUser()'); s=run('sheets.at(-1)'); assert.equal(s.fields.map((f)=>f.id).join(','),'username,password,role');
  await s.onSubmit({username:'newbie',password:'12345678',role:'deploy'}); assert.deepEqual(requests.filter((r)=>r.method==='POST').at(-1).body,{username:'newbie',password:'12345678',role:'deploy'});
  assert.equal(requests.filter((r)=>r.url==='/users'&&r.method==='GET').length,6,'initial + 5 reloads');
  assert.match(run('toasts.join("|")'),/הסיסמה שונתה.*התפקיד שונה.*המשתמש הושבת.*המשתמש deployer נמחק.*המשתמש newbie נוצר/);
});

test('permissions: /users that cannot be read is its own state (red note, "לא נקרא" pill), not "no users"; an empty list is an empty state',async()=>{
  let s=setup({'/users':new Error('DB locked')}); s.run('current="permissions"');
  await s.run('loadUsersData()');
  let html=s.run('permissions()');
  assert.notEqual(html,s.run('pagePlaceholder()')); assert.match(html,/class="pill err">לא נקרא</); assert.match(html,/note err.*DB locked/); assert.doesNotMatch(html,/אין משתמשים/);
  s=setup({'/users':[]}); s.run('current="permissions"');
  await s.run('loadUsersData()'); html=s.run('permissions()');
  assert.match(html,/class="empty">אין משתמשים/); assert.match(html,/0 משתמשים · 0 מנהלים פעילים/);
});

/* ---------- יומן ---------- */

test('logs: one table — time (hh:mm:ss today, date otherwise), severity dot from journalSeverity, sentence + text, who ("המערכת" when empty), details drawer; filter bar in the page; no tabs, no modal, no raw event column',async()=>{
  const {run,requests}=setup(); run('current="logs"');
  await run('loadJournalData()');
  assert.ok(requests.some((r)=>r.url==='/journal/events'),'event types for the filter');
  const j=requests.find((r)=>r.url==='/journal'); assert.equal(new URL(j.full,'http://x').searchParams.get('limit'),'200'); assert.equal(new URL(j.full,'http://x').searchParams.get('from'),null,'no range by default');
  const html=run('logs()'); balanced(html);
  assert.match(html,/class="obj-name">יומן</); assert.match(html,/4 אירועים מוצגים \(מגבלה 200\) · בלי סינון — האחרונים/);
  assert.match(html,/<th>זמן<\/th><th><\/th><th>מה קרה<\/th><th>מי<\/th><th><\/th>/);
  assert.doesNotMatch(html,/role="tablist"|openLogFilter|<th>event<\/th>|עקרון התצוגה|Audit/);
  const r0=row(html,'LAB303-03');
  assert.match(r0,/<span class="mono">09:44:10<\/span>/,'today → hh:mm:ss only'); assert.match(r0,/class="st warn" title="דורש תשומת לב"/); assert.match(r0,/&lt;b&gt;x&lt;\/b&gt;/,'text is escaped');
  assert.match(r0,/<td>המערכת<\/td>/); assert.match(r0,/onclick="openLogDetail\(0\)" tabindex="0"/);
  const r2=row(html,'timeout');
  assert.match(r2,/<span class="mono">16\/09\/2026 08:12:44<\/span>/); assert.match(r2,/class="st err"/);
  assert.match(row(html,'Office 2024'),/class="st info"/); assert.match(row(html,'מחשב סיים לכתוב'),/class="st ok"/);
  // שורת סינון בדף
  assert.match(html,/<div class="dg-bar"><input type="search" value="" placeholder="חיפוש בטקסט[^"]*" aria-label="חיפוש ביומן" onchange="logFilter\('q', this\.value\.trim\(\)\)"/);
  assert.match(html,/<select aria-label="סוג אירוע" onchange="logFilter\('event', this\.value\)"><option value="" selected>כל סוגי האירועים<\/option><option value="session_open">סבב נפתח</);
  assert.match(html,/<select aria-label="משתמש"[^>]*><option value="" selected>כל המשתמשים<\/option><option value="deployer">deployer<\/option><option value="nadav">nadav</,'users from the rows');
  assert.match(html,/<select aria-label="טווח זמן"[^>]*><option value="" selected>כל הזמן<\/option><option value="today">היום</);
  assert.match(html,/class="n">4 אירועים</); assert.match(html,/זה הכול לסינון הזה/,'fewer rows than the limit → nothing more to load');
  assert.match(html,/title="דורש API">ייצוא CSV — בקרוב/);
  run('openLogDetail(2)');
  assert.match(run('drawer'),/פרטי אירוע\|/); assert.match(run('drawer'),/<span class="mono">storage_transfer_failed<\/span>/); assert.match(run('drawer'),/class="st err">כשל \/ סירוב/);
});

test('logs: filters go to the server as query parameters (q, event, user, from for today/7d, to+:59 for a custom range), reset the limit, "more" raises it; truncated header → warning',async()=>{
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
  // "עוד": limit עולה רק כשיש בדיוק limit שורות (יכול להיות עוד)
  fixtures['/journal']=Array.from({length:400},(_,i)=>({ts:today+'T10:00:00',user:'u',event:'login',label:'L'+i,text:''}));
  run('logMore()'); await new Promise((r)=>setImmediate(r)); assert.equal(q().get('limit'),'400');
  assert.match(run('logs()'),/onclick="logMore\(\)">עוד \(600\)</);
  run("logFilter('q','')"); await new Promise((r)=>setImmediate(r)); assert.equal(q().get('limit'),'200','a filter change resets the limit');
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
  assert.match(b,new RegExp(`onclick="wakeMachine\\('${encodeURIComponent(MAC_B)}'\\)">WoL<`));
  assert.doesNotMatch(b,/בקרוב/);
  const c=run(`clonerCardHtml(findMachine('${MAC_C}'), true)`);
  assert.match(c,new RegExp(`onclick="wakeMachine\\('${encodeURIComponent(MAC_C)}'\\)"[^>]*>WoL<`)); assert.doesNotMatch(c,/wakeRoom|אין WoL למחשב יחיד/);
  run('DEPLOY.form={}; ROOM={round:null,machines:[{mac:"'+MAC_C+'",awake:false,joined:false}]}');
  const cr=run(`clonerCardHtml(findMachine('${MAC_C}'), true, true)`);
  assert.match(cr,/wakeMachine\(/,'room card, not connected → per-machine WoL');
  const bv=run("buildersView(GROUPS[0], [findMachine('"+MAC_B+"')], 0)");
  assert.match(bv.actions,/onclick="wakeGroup\('grp_BUILD'\)">הער את כולם \(WoL\)</); assert.doesNotMatch(bv.actions,/בקרוב/);
  const dB=run(`machineDrawerHtml(findMachine('${MAC_B}'))`), dC=run(`machineDrawerHtml(findMachine('${MAC_C}'))`), dS=run(`machineDrawerHtml(findMachine('${MAC_S}'))`);
  assert.match(dB,/wakeMachine\(/); assert.match(dC,/wakeMachine\(/); assert.doesNotMatch(dC,/wakeRoom/);
  assert.match(dS,/title="v2">WoL — לתחנות כיתה ב-v2</); assert.doesNotMatch(dS,/wakeMachine|בקרוב">Wake/);
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
