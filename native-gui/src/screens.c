/* Shared chrome and the #1090 login/menu/standby screens. The remaining
 * flows keep their existing state machine but use the same full-screen frame
 * from widgets.c. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "screens_rounds.h"

/* #1168: the college's logo (PNG) in place of the brandmark. Decoded once
 * per path and kept; a file that does not decode falls back to the brandmark
 * (cairo's status is the evidence, not the file's existence). */
static cairo_surface_t *logo_surface(const char *path) {
    static char cached[256]; static cairo_surface_t *surf=NULL;
    if (strcmp(path,cached)) {
        if (surf) { cairo_surface_destroy(surf); surf=NULL; }
        snprintf(cached,sizeof cached,"%s",path);
        if (path[0]) { surf=cairo_image_surface_create_from_png(path);
            if (cairo_surface_status(surf)!=CAIRO_STATUS_SUCCESS) { cairo_surface_destroy(surf); surf=NULL; } }
    }
    return surf;
}

/* #1252: a value the state file did not carry is said as unknown -- the
 * header once showed the mockup's server, address and version
 * on a server that was TA, 10.44.0.1, v0.59.2 (principle 5). */
static const char *known(const char *v) { return v[0] ? v : "לא ידוע"; }

/* "name · address" when both are known, the one that is known, or unknown. */
static void server_label(const State *s, char *out, size_t n) {
    if (s->server_name[0] && s->server_addr[0]) snprintf(out, n, "%s · %s", s->server_name, s->server_addr);
    else snprintf(out, n, "%s", s->server_name[0] ? s->server_name : known(s->server_addr));
}

static double draw_header(App *a, cairo_t *cr, double W) {
    const Theme *t=a->theme;
    Rect bar={0,0,W,N_HEADER_H}; draw_fill_rrect(cr,bar,0,t->header);
    double right=W-N_HEADER_PAD;
    cairo_surface_t *logo=logo_surface(a->st.logo);
    if (logo) {
        double lh=26, sw=cairo_image_surface_get_width(logo), sh=cairo_image_surface_get_height(logo);
        double lw=sh>0?sw*lh/sh:lh; if (lw>220) { lw=220; lh=sw>0?sh*lw/sw:lh; }
        cairo_save(cr); cairo_translate(cr,right-lw,(N_HEADER_H-lh)/2);
        if (sw>0&&sh>0) cairo_scale(cr,lw/sw,lh/sh);
        cairo_set_source_surface(cr,logo,0,0); cairo_paint(cr); cairo_restore(cr);
        right-=lw+22;
    } else {
        Rect mark={right-26,(N_HEADER_H-26)/2,26,26};
        draw_brand_mark(cr,t,mark); right=mark.x-10;
        Text brand=text_make(cr,FONT_SANS,16,600,"ImageCtl",0,DIR_LTR);
        text_draw_r(cr,&brand,right,(N_HEADER_H-brand.h)/2,t->header_text); right-=brand.w+22;
        text_free(&brand);
    }

    const int clone=a->force_cloner||a->force_standby||a->screen==SCREEN_CLONER||a->screen==SCREEN_STANDBY;
    const char *role=clone?"מחשב שיכפול":"מחשב בנייה";   /* #1252: the "2" was the mockup's */
    Text rt=text_make(cr,FONT_SANS,12,400,role,0,DIR_RTL);
    Rect pill={right-rt.w-18,(N_HEADER_H-20)/2,rt.w+18,20};
    draw_set_a(cr,t->header_text,.14); draw_rrect_path(cr,pill,10); cairo_fill(cr);
    text_draw_r(cr,&rt,right-9,pill.y+(pill.h-rt.h)/2,t->header_text); right=pill.x-22;
    text_free(&rt);

    /* #1252: every value from the state file; unknown is said, not invented.
     * #1257: the versions (agent · server) are drawn on their own line below. */
    char srv[160]; server_label(&a->st,srv,sizeof srv);
    const char *keys[]={"מכונה","שרת"};
    const char *vals[]={known(a->st.machine_name),srv};
    for(int i=0;i<2;i++){
        Text k=text_make(cr,FONT_SANS,13,400,keys[i],0,DIR_RTL);
        Text v=text_make(cr,FONT_MONO,13,400,vals[i],0,DIR_LTR);
        text_draw_r(cr,&k,right,(N_HEADER_H-k.h)/2,(Rgb){1,1,1});
        right-=k.w+5;
        text_draw_r(cr,&v,right,(N_HEADER_H-v.h)/2,(Rgb){.8,.84,.87});
        right-=v.w+18;
        text_free(&k);text_free(&v);
    }
    const char *av=a->st.agent_version, *sv=a->st.server_version;
    /* empty, "unknown" (the agent's stamp) and "לא ידוע" are all "not known" */
    int ak=av[0]&&strcmp(av,"unknown")&&strcmp(av,"לא ידוע"), sk=sv[0]&&strcmp(sv,"unknown")&&strcmp(sv,"לא ידוע");
    if(av[0]=='v')av++;
    if(sv[0]=='v')sv++;
    int mismatch=ak&&sk&&strcmp(av,sv);
    char versions[360];
    snprintf(versions,sizeof versions,"סוכן %s%s · שרת %s%s",
             ak?"v":"",ak?av:"לא ידוע",sk?"v":"",sk?sv:"לא ידוע");
    double vw=right-90; if(vw<100)vw=100;
    Text vt=text_make(cr,FONT_SANS,11,400,versions,vw,DIR_RTL);
    text_draw_r(cr,&vt,right,mismatch?5:(N_HEADER_H-vt.h)/2,t->header_text);
    text_free(&vt);
    if(mismatch){
        Text hint=text_make(cr,FONT_SANS,11,600,"ה-initrd ישן מהשרת",vw,DIR_RTL);
        text_draw_r(cr,&hint,right,N_HEADER_H-hint.h-4,(Rgb){1,.78,.35});
        text_free(&hint);
    }
    time_t now=time(NULL); struct tm tmv; char clock_s[8]="--:--";
    if(a->fixed_clock) snprintf(clock_s,sizeof clock_s,"12:45");   /* the mockup's clock; --png only */
#ifdef _WIN32
    else if(now!=(time_t)-1 && localtime_s(&tmv,&now)==0) strftime(clock_s,sizeof clock_s,"%H:%M",&tmv);
#else
    else if(now!=(time_t)-1 && localtime_r(&now,&tmv)) strftime(clock_s,sizeof clock_s,"%H:%M",&tmv);
#endif
    Text clock=text_make(cr,FONT_MONO,14,400,clock_s,0,DIR_LTR);
    text_draw(cr,&clock,N_HEADER_PAD,(N_HEADER_H-clock.h)/2,t->header_text); text_free(&clock);
    return N_HEADER_H;
}

static const char *status_word(const App *a,char *buf,size_t n,Rgb *dot){
    const State *s=&a->st; *dot=a->theme->led_ok;
    if(s->shrink_pending){*dot=a->theme->warn;return "ממתין להחלטת כיווץ";}   /* #928: the modal owns the screen */
    switch(a->screen){
    case SCREEN_LOGIN:return "ממתין לאימות";
    case SCREEN_MENU:return a->logout_confirming?"אישור התנתקות":"בחירת פעולה";
    case SCREEN_STANDBY:*dot=a->theme->led_idle;return "ממתין לסבב";
    case SCREEN_MESSAGE:*dot=a->theme->warn;return s->msg_title;
    case SCREEN_PICK:return "בחירת דיסק";
    case SCREEN_PROGRESS:return s->task==TASK_PENDING?"ממתין לסוכן":"משימה פעילה";
    case SCREEN_DONE:if(s->task==TASK_FAILED){*dot=a->theme->danger;return "נכשל";}return "הושלם";
    case SCREEN_CLASS:return s->has_session?(s->sess_open?"ממתין להצטרפות":"משדר"):"בחירת כיתה";
    case SCREEN_ROOM:
        if(s->has_round){snprintf(buf,n,"גל %d %s",s->wave_number,s->wave_open?"ממתין":"משדר");return buf;}
        {int awake=0;for(int i=0;i<s->nmachines;i++)awake+=s->machines[i].awake;snprintf(buf,n,"%d יעדים מחוברים",awake);return buf;}
    case SCREEN_CLONER:
        if(s->smart_pending){*dot=a->theme->warn;return "ממתין להכרעת SMART";}
        {int w=0;for(int i=0;i<s->ndrawers;i++)w+=!strcmp(s->drawers[i].state,"writing");snprintf(buf,n,"כותב %d דיסקים במקביל",w);return buf;}
    case SCREEN_RESTORE:*dot=a->theme->warn;return "בחירת אימג' לשחזור";
    case SCREEN_TOOLS:return a->tool_running?"מריץ כלי":"ארגז הכלים";
    case SCREEN_INSTALL:return "אשף התקנה";
    }
    return "מוכן";
}

/* mockup 10/10: the keys a screen lists are the keys main.c handle() acts on
 * there -- nothing it ignores. */
static const char *status_keys(const App *a,char *buf,size_t n){
    if(a->st.shrink_pending)return "1  כווץ וקלוט    2  קלוט בלי כיווץ    3 / Esc  בטל קליטה";   /* #928 */
    if(a->confirm)return "Enter  אישור    Esc  ביטול";   /* #1270: the confirm dialog */
    switch(a->screen){
    case SCREEN_LOGIN:return "Tab  מעבר    Enter  כניסה";
    case SCREEN_MENU:
        if(a->logout_confirming)return "Enter  התנתק    Esc  ביטול";
        snprintf(buf,n,"1–%d  בחירה    Enter  אישור    Esc  התנתק",
                 (a->admin?4:3)+(a->st.menu_class?1:0));
        return buf;
    case SCREEN_ROOM:
        if(!a->st.has_round&&a->dd_open==HIT_ROOM_IMAGE)
            return a->pick_in_folder?"1–9  בחירת אימג'    Esc / Backspace  לתיקיות":"1–9  בחירת תיקייה    Esc  סגירה";
        if(!a->st.has_round)return a->mode==MODE_DIRECT?"W  העֵר    Esc  חזרה":"Tab  מעבר    W  העֵר    Esc  חזרה";
        return a->st.wave_open?"W  העֵר שוב    N  התחל עכשיו    Esc  עצור סבב…":"W  העֵר שוב    Esc  עצור סבב…";
    case SCREEN_CLONER:   /* mockup 10/10: the SMART keys only while a question is open */
        if(!a->st.smart_pending)return "אין פעולה במסך הזה";
        return !strcmp(a->st.smart_verdict,"failed_last")
        ? "R  החלף דיסק    Enter / S  המשך בלי הדיסק"
        : "R  החלף דיסק    S  דלג על הדיסק    Enter  כתוב בכל זאת";
    case SCREEN_RESTORE:return a->pick_in_folder   /* owner 10/10: folder-first */
        ? "1–9 / [ ]  בחירת אימג'    Esc / Backspace  לתיקיות    Enter  מחק ושחזר…"
        : "1–9  בחירת תיקייה    Enter  מחק ושחזר…    Esc  חזרה";
    case SCREEN_STANDBY:return "אין פעולה במסך הזה";   /* "דיסקים: 1 · 2 · 3 SMART" sat where keys go */
    case SCREEN_PICK:return a->chosen_disk>=0?"Tab / Shift-Tab  מעבר    Alt+Shift  שפה    Enter  התחלה    Esc  חזרה":"Esc  חזרה";   /* #1245 */
    case SCREEN_CLASS:return a->st.has_session?"Enter  התחל עכשיו":"Esc  חזרה";
    case SCREEN_DONE:return "Enter  קליטה נוספת";
    case SCREEN_PROGRESS:return "אין פעולה במסך הזה";   /* mockup 10/10: it said "Esc חזרה" and Esc did nothing */
    case SCREEN_TOOLS:return a->tools_view==TOOLS_CONFIRM?"Tab / Shift-Tab  מעבר    Esc  חזרה":"Esc  חזרה";
    case SCREEN_INSTALL:return "Tab / Shift-Tab  מעבר    Enter  אישור    Esc  חזרה";
    default:return "אין פעולה במסך הזה";   /* message: there is no way out to offer */
    }
}

/* #1245: the typing language, at the start of the keys -- only while a text
 * field has the focus. "עב" filled in the action colour, "EN" outlined; a
 * click on either switches, as Alt+Shift does. On a field that takes English
 * only it is the reason, not a switch (owner, 10/10: a field that cannot take
 * Hebrew says why). Returns the width it took, 0 when nothing is drawn. */
static double draw_lang_chip(App *a,cairo_t *cr,double x,Rect bar){
    const Theme *t=a->theme;int lang=app_typing_lang(a);
    if(lang==LANG_NONE)return 0;
    if(lang==LANG_EN_ONLY){
        Text n=text_make(cr,FONT_SANS,12,400,"EN · שדה באנגלית בלבד",0,DIR_RTL);
        text_draw(cr,&n,x,bar.y+(bar.h-n.h)/2,t->muted);double w=n.w+3*N_STATUS_GAP;text_free(&n);return w;
    }
    int he=lang==LANG_HE;
    Text l=text_make(cr,FONT_SANS,12,600,he?"עב":"EN",0,he?DIR_RTL:DIR_LTR);
    Rect c={x,bar.y+(bar.h-22)/2,l.w+20,22};
    if(he)draw_fill_rrect(cr,c,RADIUS_SM,t->indigo);else draw_border_rrect(cr,c,RADIUS_SM,t->hair,1);
    text_draw(cr,&l,c.x+(c.w-l.w)/2,c.y+(c.h-l.h)/2,he?t->on_ink:t->ink);
    hit_add(a,c,HIT_LANG);double w=c.w+3*N_STATUS_GAP;text_free(&l);return w;
}

static void draw_status(App *a,cairo_t *cr,double W,double H){
    const Theme *t=a->theme; Rect bar={0,H-N_STATUS_H,W,N_STATUS_H};
    draw_fill_rrect(cr,bar,0,t->status_bg); cairo_rectangle(cr,0,bar.y,W,1);draw_set(cr,t->hair);cairo_fill(cr);
    char b[96];Rgb dot;const char *word=status_word(a,b,sizeof b,&dot);
    Text w=text_make(cr,FONT_SANS,12,400,word,0,DIR_RTL);
    double rx=W-N_STATUS_PAD;
    cairo_arc(cr,rx-N_STATUS_DOT/2,bar.y+bar.h/2,N_STATUS_DOT/2,0,2*M_PI);draw_set(cr,dot);cairo_fill(cr);
    text_draw_r(cr,&w,rx-N_STATUS_DOT-N_STATUS_GAP,bar.y+(bar.h-w.h)/2,t->ink);text_free(&w);
    double kx=N_STATUS_PAD+draw_lang_chip(a,cr,N_STATUS_PAD,bar);   /* #1245 */
    char kb[200];Text keys=text_make(cr,FONT_SANS,12,400,status_keys(a,kb,sizeof kb),0,DIR_RTL);
    text_draw(cr,&keys,kx,bar.y+(bar.h-keys.h)/2,t->muted);text_free(&keys);
}

/* The owner, 10/10: the login fields as the console draws them -- the label
 * above, a bordered input (draw_field: the radius/border/focus ring of every
 * other input; user and password are LTR, the text from the left edge).
 * Returns the label's height + gap, where the box starts. */
#define LOGIN_LABEL_GAP 4.0
static double login_label(App *a,cairo_t *cr,double x,double y,double w,const char *label){
    Text l=rtl_block_make(cr,12,500,label,w);text_draw_r(cr,&l,x+w,y,a->theme->muted);
    double h=l.h+LOGIN_LABEL_GAP;text_free(&l);return h;
}

void screen_login(App *a,cairo_t *cr,double W,double H,double head_h){
    (void)head_h; const Theme *t=a->theme; double usable=H-N_STATUS_H;
    double pane=fmin(600,fmax(420,W*.32)); Rect pr={W-pane,0,pane,usable}, ar={0,0,W-pane,usable};
    draw_fill_rrect(cr,pr,0,t->surface);draw_station_art(cr,t,ar,1);
    cairo_rectangle(cr,pr.x,0,1,usable);draw_set(cr,t->hair);cairo_fill(cr);
    double right=W-64; Rect mark={right-30,48,30,30};draw_brand_mark(cr,t,mark);
    Text brand=text_make(cr,FONT_SANS,20,600,"ImageCtl",0,DIR_LTR);
    text_draw_r(cr,&brand,mark.x-10,48+(30-brand.h)/2,t->ink);text_free(&brand);
    double fw=fmin(400,pane-128),x=pr.x+(pane-fw)/2;
    Text title=rtl_block_make(cr,24,600,"כניסה",fw);
    char lead_s[128];snprintf(lead_s,sizeof lead_s,"מחשב הבנייה %s",known(a->st.machine_name));   /* #1252 */
    Text lead=rtl_block_make(cr,13,400,lead_s,fw);
    double lab=text_line_height(cr,FONT_SANS,12)+LOGIN_LABEL_GAP;
    double content=title.h+6+lead.h+28+lab+N_FIELD_H+18+lab+N_FIELD_H+22+N_BUTTON_H+(a->error[0]?24:0);
    double y=(usable-content)/2;
    text_draw_r(cr,&title,x+fw,y,t->ink);y+=title.h+6;
    text_draw_r(cr,&lead,x+fw,y,t->muted);y+=lead.h+28;
    y+=login_label(a,cr,x,y,fw,"שם משתמש");
    draw_field(a,cr,(Rect){x,y,fw,N_FIELD_H},a->user,0,NULL,HIT_USER);y+=N_FIELD_H+18;
    y+=login_label(a,cr,x,y,fw,"סיסמה");
    Rect pf={x,y,fw,N_FIELD_H};
    draw_field(a,cr,pf,a->pass,!a->show_pw,NULL,HIT_PASS);   /* keeps 62px clear at the right (widgets.c) */
    /* only "הצג" inside the field, at its right end: action-colour words, no box */
    Text eye=text_make(cr,FONT_SANS,12,500,a->show_pw?"הסתר":"הצג",0,DIR_RTL);
    Rect er={pf.x+pf.w-eye.w-24,pf.y+(pf.h-24)/2,eye.w+16,24};
    text_draw(cr,&eye,er.x+8,er.y+(er.h-eye.h)/2,t->action_fg);hit_add(a,er,HIT_EYE);text_free(&eye);
    y+=N_FIELD_H+10;
    /* the error only after a failed attempt (do_login sets it), in its own row */
    if(a->error[0]){Text e=rtl_block_make(cr,13,400,a->error,fw);text_draw_r(cr,&e,x+fw,y,t->danger_fg);text_free(&e);y+=24;}
    y+=12;
    Text go=btn_label(cr,"כניסה");draw_btn(a,cr,(Rect){x,y,fw,N_BUTTON_H},&go,BTN_PRIMARY,HIT_SUBMIT);text_free(&go);
    char srv[160];server_label(&a->st,srv,sizeof srv);   /* #1252: was the mockup's fixed line */
    char foot_s[320];snprintf(foot_s,sizeof foot_s,"%s   ·   %s",srv,known(a->st.server_version));   /* 160 + 129 + separators */
    Text foot=text_make(cr,FONT_MONO,12,400,foot_s,0,DIR_LTR);text_draw(cr,&foot,pr.x+64,usable-28-foot.h,t->muted);text_free(&foot);
    text_free(&title);text_free(&lead);
}

/* mockup 10/10 (docs/design/apple-mockups/native.html, "תפריט מחשב הבנייה"):
 * a card is a decision -- icon, title, one line of what it does, and the
 * flow ("a → b → c", the three how[] steps) -- at the height of its content,
 * two to a row. warn is said after desc in the danger colour. */
typedef struct{const char *title,*desc,*warn,*how[3];int id,icon,admin,cls;}MenuItem;
#define MENU_N 5
static const MenuItem MENU[MENU_N]={
 { "קליטת אימג' חדש","הדיסק של המחשב הזה נקרא, מכווץ ומועלה לספרייה.",NULL,{"SMART","sha256","שם · תיאור · תיקייה"},HIT_CAPTURE,MENU_ICON_CAPTURE,1,0},
 { "שיכפול מהשרת","אימג' מהספרייה משודר במולטיקאסט לכל חדר השיכפול, בגלים.",NULL,{"אימג' + יעד","החדר מתעורר","גל כותב"},HIT_ROOM,MENU_ICON_SERVER,0,0},
 { "שיכפול ישיר","הדיסק של המחשב הזה משודר למגירות שנבחרו, בלי לשמור אימג'.",NULL,{"קריאה בלבד","סבב יחיד","מי שנבחר"},HIT_DIRECT,MENU_ICON_DIRECT,0,0},
 { "שחזור לדיסק הזה","אימג' מהספרייה נכתב על הדיסק הפנימי.","הדיסק נמחק.",{"אימג'","שם המכונה","כתיבה"},HIT_RESTORE,MENU_ICON_RESTORE,0,0},
 /* #1081: classes are v1.2 -- drawn only when the state file says menu_class=1 (absent = 0). */
 { "הפצה לכיתות","בוחרים כיתה, מחשבים ואימג' — הסבב מעיר את הכיתה ורץ בשרת.",NULL,{"כיתה + אימג'","הכיתה מתעוררת","הסבב רץ בשרת"},HIT_CLASSES,MENU_ICON_SERVER,0,1}
};

static void hex_rgb(Rgb c,char *out,size_t n){
    snprintf(out,n,"#%02X%02X%02X",(int)(c.r*255+.5),(int)(c.g*255+.5),(int)(c.b*255+.5));
}

/* mockup 10/10: the server pill says what the agent's last hello says
 * (hello_rc / hello_age from guistate.sh) -- never a fixed "connected": the
 * old pill was green on a server that was down. No hello yet is its own
 * words, not a colour (principle 5). Returns the pill's width. */
static double menu_server_pill(App *a,cairo_t *cr,double right,double y){
    const State *s=&a->st;char srv[160],label[240];int kind=PILL_OFF;
    server_label(s,srv,sizeof srv);
    if(s->hello_rc==200&&s->hello_age>=0&&s->hello_age<=120){snprintf(label,sizeof label,"שרת %s מחובר",srv);kind=PILL_OK;}
    else if(s->hello_rc==200){snprintf(label,sizeof label,"שרת %s · ענה לפני %d שנ'",srv,s->hello_age);kind=PILL_WARN;}
    else if(s->hello_rc>=0){snprintf(label,sizeof label,"שרת %s לא מחובר",srv);kind=PILL_BAD;}
    else snprintf(label,sizeof label,"שרת %s · החיבור טרם נבדק",srv);
    return draw_pill(cr,a->theme,right,y,label,kind,kind!=PILL_OFF);
}

/* The cloners, counted from the machine= records (machines[].awake). The
 * menu's own state carries none -- guistate.sh writes them in the room
 * modes only -- and then there is nothing true to say, so no pill. */
static void menu_cloner_pill(App *a,cairo_t *cr,double right,double y){
    const State *s=&a->st;int awake=0,ready=0;
    if(!s->nmachines)return;
    for(int i=0;i<s->nmachines;i++)if(s->machines[i].awake){awake++;ready+=s->machines[i].fresh_drawers;}
    char label[120];snprintf(label,sizeof label,"%d מחשבי שיכפול ערים · %d מגירות מוכנות",awake,ready);
    draw_pill(cr,a->theme,right,y,label,PILL_INFO,0);
}

/* One card's text, measured before anything is drawn: the cards of a row
 * share the row's height (the mockup's grid), so heights come first. */
typedef struct{Text title,desc,flow,key,pill;double h,tw;}MenuCard;

static MenuCard menu_card_make(App *a,cairo_t *cr,const MenuItem *m,int number,double cw){
    MenuCard k;char key[12],flow[160],e[400],mk[700],bad[8];
    snprintf(key,sizeof key,"%d",number);
    k.key=text_make(cr,FONT_MONO,12,400,key,0,DIR_LTR);
    k.pill=m->admin?text_make(cr,FONT_SANS,12,600,"admin",0,DIR_RTL):(Text){NULL,0,0};
    /* padding 20 | icon 48 | gap 14 | text | gap 14 | key chip | padding 20 */
    k.tw=cw-20-48-14-14-(k.key.w+12)-20;
    double pill_w=m->admin?k.pill.w+20+8:0;
    k.title=rtl_block_make(cr,18,600,m->title,k.tw-pill_w);
    text_escape(m->desc,e,sizeof e);
    if(m->warn){char w[120];text_escape(m->warn,w,sizeof w);hex_rgb(a->theme->danger,bad,sizeof bad);
        snprintf(mk,sizeof mk,"%s <span foreground=\"%s\" weight=\"bold\">%s</span>",e,bad,w);}
    else snprintf(mk,sizeof mk,"%s",e);
    k.desc=text_make_markup(cr,FONT_SANS,13,400,mk,(int)k.tw,DIR_RTL);
    pango_layout_set_alignment(k.desc.layout,PANGO_ALIGN_RIGHT);
    snprintf(flow,sizeof flow,"%s → %s → %s",m->how[0],m->how[1],m->how[2]);
    k.flow=text_make_ltr(cr,FONT_SANS,11,400,flow,(int)k.tw);
    k.h=18+dmax(48,k.title.h+4+k.desc.h+8+k.flow.h)+18;
    if(k.h<132)k.h=132;                       /* .act{min-height:132px} */
    return k;
}

static void menu_card_draw(App *a,cairo_t *cr,const MenuItem *m,MenuCard *k,Rect c,int focused){
    const Theme *t=a->theme;int hov=hovered(a,c);
    draw_fill_rrect(cr,c,t->radius,t->surface);
    draw_border_rrect(cr,c,t->radius,focused||hov?t->indigo:t->hair,focused?2:1);
    if(focused)draw_focus_ring(a,cr,c);
    double right=c.x+c.w-20,y=c.y+18;
    Rect ic={right-48,y,48,48};draw_fill_rrect(cr,ic,6,t->indigo_soft);
    draw_menu_icon(cr,(Rect){ic.x+12,ic.y+12,24,24},m->icon,t->action_fg);   /* on action-soft: the -fg tone reads */
    Rect kb={c.x+20,y,k->key.w+12,20};draw_fill_rrect(cr,kb,3,t->field);draw_border_rrect(cr,kb,3,t->hair,1);
    text_draw(cr,&k->key,kb.x+6,kb.y+(kb.h-k->key.h)/2,t->ink);
    double tr=ic.x-14;
    text_draw_r(cr,&k->title,tr,y,t->ink);
    if(m->admin){PangoRectangle lg;pango_layout_get_pixel_extents(k->title.layout,NULL,&lg);
        draw_pill(cr,t,tr-lg.width-8,y+1,"admin",PILL_WARN,0);}   /* next to the name: it never wraps the title */
    y+=k->title.h+4;
    text_draw_r(cr,&k->desc,tr,y,t->muted);y+=k->desc.h+8;
    text_draw_r(cr,&k->flow,tr,y,t->muted);
    text_free(&k->title);text_free(&k->desc);text_free(&k->flow);text_free(&k->key);text_free(&k->pill);
}

/* Esc on the menu signs out only after a yes (mockup 10/10: a stray Esc
 * dropped the session). Modal like the shrink question: it replaces the
 * hit table with its two buttons. */
static void draw_logout_confirm(App *a,cairo_t *cr,double W,double H){
    const Theme *t=a->theme;double usable=H-N_STATUS_H;
    cairo_rectangle(cr,0,0,W,usable);draw_set_a(cr,(Rgb){0,0,0},.55);cairo_fill(cr);
    a->nhits=0;a->clip_on=0;
    double dw=fmin(460,W-64),inner=dw-56;
    Text h=rtl_block_make(cr,20,600,"להתנתק?",inner);
    Text p=rtl_block_make(cr,13,400,"המסך יחזור לכניסה. משימה שכבר רצה ממשיכה לרוץ.",inner);
    double dh=28+h.h+6+p.h+24+N_BUTTON_H+28;
    Rect box={(W-dw)/2,(usable-dh)/2,dw,dh};
    draw_fill_rrect(cr,box,t->radius,t->surface);draw_border_rrect(cr,box,t->radius,t->hair,1);
    double right=box.x+box.w-28,y=box.y+28;
    text_draw_r(cr,&h,right,y,t->ink);y+=h.h+6;
    text_draw_r(cr,&p,right,y,t->muted);
    Text go=btn_label(cr,"התנתק"),no=btn_label(cr,"ביטול");
    double gw=btn_width(&go),nw=btn_width(&no),by=box.y+box.h-28-N_BUTTON_H;
    /* the approved window: the action at the edge, cancel next to it */
    draw_btn(a,cr,(Rect){box.x+28,by,gw,N_BUTTON_H},&go,BTN_PRIMARY,HIT_LOGOUT_CONFIRM);
    draw_btn(a,cr,(Rect){box.x+28+gw+N_ACTION_GAP,by,nw,N_BUTTON_H},&no,BTN_PLAIN,HIT_LOGOUT_CANCEL);
    text_free(&h);text_free(&p);text_free(&go);text_free(&no);
}

void screen_menu(App *a,cairo_t *cr,double W,double H,double head_h){
    const Theme *t=a->theme;double right=W-N_PAD,y=head_h+28,inner=W-2*N_PAD;
    int show[MENU_N] = {1, 1, 1, 1, 1};
    Text title=rtl_block_make(cr,24,600,"מה לעשות עם המחשב הזה?",inner);
    char ss[160];snprintf(ss,sizeof ss,"מחוברים כ-%s",a->signed_user);
    Text sub=rtl_block_make(cr,13,400,ss,inner);
    text_draw_r(cr,&title,right,y,t->ink);text_draw_r(cr,&sub,right,y+title.h+3,t->muted);
    double py=y+title.h+3+sub.h+10;
    double pw=menu_server_pill(a,cr,right,py);
    menu_cloner_pill(a,cr,right-pw-10,py);
    double line_y=py+24+12;cairo_rectangle(cr,N_PAD,line_y,inner,1);draw_set(cr,t->hair);cairo_fill(cr);
    /* #649: the toolbox entry -- a secondary 38px button at the title row's
     * inline end (left, next to the pills), bottom-aligned to the title block
     * like the mockup's .ph (align-items:flex-end; its padding-bottom is the
     * 12 above line_y), NOT one of the choice cards: it is
     * the IT person's drawer, not an operator flow. Build menu only (cloner
     * and class route before it). Registered before the body clip. v1 ships
     * without the toolbox (Nadav, 19/09): drawn only when the state file says
     * menu_tools=1 (absent = 0); v1.1 turns it on. */
    if (a->st.menu_tools) {
        Text tools_l = btn_label(cr, "כלים");
        double tools_w = btn_width(&tools_l);
        Rect tools_r = { N_PAD, line_y - 12 - N_BUTTON_H, tools_w, N_BUTTON_H };
        draw_btn(a, cr, tools_r, &tools_l, BTN_PLAIN, HIT_TOOLS);
        text_free(&tools_l);
        /* keyboard order: the cards, "התנתק", then "כלים" (main.c menu_stops) */
        if (a->menu_focus == (a->admin ? 4 : 3) + (a->st.menu_class ? 1 : 0) + 1) draw_focus_ring(a, cr, tools_r);
    }
    /* the cards: two columns (.acts grid 1fr 1fr, gap 16), card 1 at the
     * right, each row as tall as its tallest card -- not the screen */
    int idx[MENU_N],count=0,cols=2;double gap=16,gy=line_y+17,bottom=H-N_STATUS_H-24-N_BUTTON_H-18;
    for(int i=0;i<MENU_N;i++)
        if(show[i]&&!(MENU[i].admin&&!a->admin)&&!(MENU[i].cls&&!a->st.menu_class))idx[count++]=i;
    double cw=(inner-(cols-1)*gap)/cols;
    MenuCard cards[MENU_N];
    for(int k=0;k<count;k++)cards[k]=menu_card_make(a,cr,&MENU[idx[k]],k+1,cw);
    Rect body={0,line_y+1,W,bottom-line_y-1};   /* .sbody: the card grid, clipped */
    body_clip_begin(a, cr, body);
    double row_y=gy;
    for(int k=0;k<count;k+=cols){
        double row_h=cards[k].h;
        for(int j=k+1;j<k+cols&&j<count;j++)row_h=dmax(row_h,cards[j].h);
        for(int j=k;j<k+cols&&j<count;j++){
            Rect c={right-cw-(j-k)*(cw+gap),row_y,cw,row_h};
            menu_card_draw(a,cr,&MENU[idx[j]],&cards[j],c,a->menu_focus==j);
            hit_add(a,c,MENU[idx[j]].id);
        }
        row_y+=row_h+gap;
    }
    body_clip_end(a, cr);
    Text lo=btn_label(cr,"התנתק");double lw=btn_width(&lo);Rect lr={N_PAD,H-N_STATUS_H-24-N_BUTTON_H,lw,N_BUTTON_H};
    draw_btn(a,cr,lr,&lo,BTN_PLAIN,HIT_LOGOUT);text_free(&lo);
    if(a->menu_focus==count)draw_focus_ring(a,cr,lr);
    text_free(&title);text_free(&sub);
    if(a->logout_confirming)draw_logout_confirm(a,cr,W,H);
}

static const char *standby_probe_warning(const State *s){
    if(!strcmp(s->disk_probe,"no_ports"))return "אין פורטי SATA בקושחה";
    if(!strcmp(s->disk_probe,"no_disks"))return "לא חוברו דיסקים";
    if(!strcmp(s->disk_probe,"unchecked"))return "הדיסקים לא נבדקו";
    return NULL;
}

static void standby_disk_row(cairo_t *cr,const Theme *t,double center,double y,double row_w,
                             const char *label,const char *model,unsigned long long bytes,
                             const char *smart,int empty){
    Rect row={center-row_w/2,y,row_w,28};
    draw_fill_rrect(cr,row,4,t->surface);draw_border_rrect(cr,row,4,t->hair,1);
    Text name=text_make(cr,FONT_SANS,13,600,label,0,DIR_RTL);
    Text sep=text_make(cr,FONT_SANS,13,400,"·",0,DIR_RTL);
    Text detail=text_make(cr,FONT_SANS,13,400,empty?"ריק":model,0,DIR_LTR);
    char size[32];fmt_bytes(size,sizeof size,(double)bytes);
    Text sz=text_make(cr,FONT_MONO,13,400,size,0,DIR_LTR);
    Text health=text_make(cr,FONT_SANS,13,600,smart_he(smart),0,DIR_RTL);
    double gap=9,total=name.w+gap+sep.w+gap+detail.w;
    if(!empty)total+=gap+sep.w+gap+sz.w+gap+sep.w+gap+health.w;
    double right=center+total/2,ty=y+(row.h-name.h)/2;
    text_draw_r(cr,&name,right,ty,t->ink);right-=name.w+gap;
    text_draw_r(cr,&sep,right,ty,t->muted);right-=sep.w+gap;
    text_draw_r(cr,&detail,right,ty,empty?t->muted:t->ink);
    if(!empty){
        right-=detail.w+gap;text_draw_r(cr,&sep,right,ty,t->muted);right-=sep.w+gap;
        text_draw_r(cr,&sz,right,ty,t->ink);right-=sz.w+gap;
        text_draw_r(cr,&sep,right,ty,t->muted);right-=sep.w+gap;
        text_draw_r(cr,&health,right,ty,smart_color(t,smart));
    }
    text_free(&name);text_free(&sep);text_free(&detail);text_free(&sz);text_free(&health);
}

void screen_standby(App *a,cairo_t *cr,double W,double H,double head_h){
    const Theme *t=a->theme;const State *s=&a->st;Rect area={0,head_h,W,H-head_h-N_STATUS_H};draw_station_art(cr,t,area,.5);
    int cloner=a->force_cloner,compact=W<=1280,rows=0;
    const char *probe=standby_probe_warning(s);
    if(probe)rows=1;else if(cloner)rows=3;else rows=s->ndisks<5?s->ndisks:5;
    double list_h=rows?probe?20:rows*28+(rows-1)*4:0;
    double after_mark=compact?16:22,after_sub=compact?16:26,before_list=compact?12:18,after_list=compact?14:22;
    Text h=text_make(cr,FONT_SANS,30,600,"ממתין לשרת",0,DIR_RTL);
    Text p=text_make(cr,FONT_SANS,15,400,"אין סבב פתוח. המחשב יצטרף לבד כשייפתח סבב.",0,DIR_RTL);
    char srv[160];server_label(s,srv,sizeof srv);   /* #1252 */
    char addr[256];snprintf(addr,sizeof addr,"%s     %s     %s",a->ip[0]?a->ip:"—",a->mac[0]?a->mac:"—",srv);
    Text ad=text_make(cr,FONT_MONO,22,400,addr,0,DIR_LTR);
    char hello[160];
    if(s->hello_age>=0&&s->hello_rc>=0)snprintf(hello,sizeof hello,"hello אחרון לפני %d שניות · השרת ענה %d",s->hello_age,s->hello_rc);
    else snprintf(hello,sizeof hello,"טרם התקבלה ראיית hello מהסוכן");
    Text he=text_make(cr,FONT_SANS,13,400,hello,0,DIR_RTL);
    double content_h=72+after_mark+h.h+8+p.h+after_sub+ad.h+(rows?before_list+list_h:0)+after_list+32;
    double center=W/2,y=head_h+(area.h-content_h)/2;Rect mark={center-36,y,72,72};draw_brand_mark(cr,t,mark);y+=72+after_mark;
    text_draw(cr,&h,center-h.w/2,y,t->ink);y+=h.h+8;
    text_draw(cr,&p,center-p.w/2,y,t->muted);y+=p.h+after_sub;
    text_draw(cr,&ad,center-ad.w/2,y,t->ink);y+=ad.h;
    if(rows){
        y+=before_list;
        if(probe){Text w=text_make(cr,FONT_SANS,13,600,probe,0,DIR_RTL);text_draw(cr,&w,center-w.w/2,y,t->warn);text_free(&w);y+=list_h;}
        else if(cloner)for(int port=1;port<=rows;port++){
            const IdleDisk *d=idle_for_port(s,port);char label[SLOT_NAME_MAX+16];cloner_slot_label(s,port,label,sizeof label);   /* #704 */
            standby_disk_row(cr,t,center,y,fmin(760,W-2*N_PAD),label,d&&d->model[0]?d->model:"לא זוהה",d?d->size:0,d?d->smart:"unchecked",!d);
            y+=32;
        }else for(int i=0;i<rows;i++){
            const Disk *d=&s->disks[i];
            standby_disk_row(cr,t,center,y,fmin(760,W-2*N_PAD),d->removable?"מגירה":"דיסק פנימי",d->model[0]?d->model:d->dev,d->size_bytes,"unchecked",0);
            y+=32;
        }
    }
    y+=after_list;
    Rect hb={center-(he.w+48)/2,y,he.w+48,32};draw_fill_rrect(cr,hb,16,t->surface);draw_border_rrect(cr,hb,16,t->hair,1);
    struct timespec ts; double pulse=9;
    if(!a->fixed_clock&&timespec_get(&ts,TIME_UTC)==TIME_UTC) pulse=8+2*(.5+.5*sin((ts.tv_sec+ts.tv_nsec/1e9)*4));   /* --png: a still frame, no wall-clock pulse */
    draw_led(cr,hb.x+18,hb.y+16,pulse,s->hello_rc==200?t->led_ok:t->warn);text_draw_r(cr,&he,hb.x+hb.w-14,hb.y+(hb.h-he.h)/2,t->muted);
    text_free(&h);text_free(&p);text_free(&ad);text_free(&he);
}

static void draw_cursor(cairo_t *cr,double x,double y){
    static const double P[][2]={{0,0},{0,16},{4,12},{7,19},{9.5,18},{6.5,11},{11.5,11}};
    cairo_move_to(cr,x,y);for(int i=1;i<7;i++)cairo_line_to(cr,x+P[i][0],y+P[i][1]);cairo_close_path(cr);
    cairo_set_source_rgb(cr,1,1,1);cairo_fill_preserve(cr);cairo_set_source_rgb(cr,0,0,0);cairo_set_line_width(cr,1);cairo_stroke(cr);
}

void app_route(App *a){
    const State *s=&a->st;
    if(a->install.active){a->screen=SCREEN_INSTALL;return;}
    if(a->force_standby){a->screen=SCREEN_STANDBY;return;}
    if(a->force_cloner){a->screen=(s->ndrawers||s->cloner_image[0]||s->smart_pending)?SCREEN_CLONER:SCREEN_STANDBY;return;}
    if(s->msg_title[0]){a->screen=SCREEN_MESSAGE;return;}
    if(a->force_progress||s->task==TASK_PENDING||s->task==TASK_RUNNING){a->screen=SCREEN_PROGRESS;a->watching=1;return;}
    if(a->watching&&(s->task==TASK_DONE||s->task==TASK_FAILED)){
        a->watching=0;a->showing_done=1;
        if(s->task==TASK_DONE&&s->task_direct){snprintf(a->done_title,sizeof a->done_title,"ההפצה הישירה הושלמה");snprintf(a->done_sub,sizeof a->done_sub,"הדיסק %s שודר למגירות שנבחרו. שום דבר לא נשמר בשרת.",s->task_disk);}
        else if(s->task==TASK_DONE){snprintf(a->done_title,sizeof a->done_title,"הקליטה הושלמה");snprintf(a->done_sub,sizeof a->done_sub,"\"%s\" נכנס לספרייה.",s->task_name);}
        else{snprintf(a->done_title,sizeof a->done_title,"המשימה נכשלה");snprintf(a->done_sub,sizeof a->done_sub,"%s",s->task_error[0]?s->task_error:"ראו את היומן בקונסולה.");}
    }
    if(a->showing_done){a->screen=SCREEN_DONE;return;}
    if(a->mode==MODE_CLASSES&&(a->signed_in||s->has_session)){a->screen=SCREEN_CLASS;return;}
    if(!a->signed_in){a->screen=SCREEN_LOGIN;return;}
    switch(a->mode){
    case MODE_ROOM: case MODE_DIRECT: a->screen = SCREEN_ROOM; break;
    case MODE_CAPTURE: a->screen = SCREEN_PICK; break;
    case MODE_RESTORE: a->screen = SCREEN_RESTORE; break;
    case MODE_TOOLS:   a->screen = SCREEN_TOOLS; break;     /* #649 */
    default:           a->screen = SCREEN_MENU; break;
    }
}

static void draw_state_warning(App *a,cairo_t *cr,double W,double H){
    if(!a->st.state_warning[0])return;
    const Theme *t=a->theme;
    Text v=text_make(cr,FONT_SANS,12,600,a->st.state_warning,0,DIR_RTL);
    Rect b={(W-v.w-32)/2,H-N_STATUS_H-36,v.w+32,28};draw_fill_rrect(cr,b,t->radius,t->warning_soft);draw_border_rrect(cr,b,t->radius,t->warn,1);text_draw(cr,&v,b.x+16,b.y+(b.h-v.h)/2,t->warn);text_free(&v);
}

/* #434: the power-off countdown, over every screen: the agent found nobody
 * here for IDLE_POWEROFF_S; any key cancels (the GUI emits "touch"). */
static void draw_idle_countdown(App *a,cairo_t *cr,double W,double H){
    if(a->st.idle_poweroff_at<=0)return;
    long long left=a->st.idle_poweroff_at-(long long)time(NULL); if(left<0)left=0;
    char msg[160];snprintf(msg,sizeof msg,"אין פעילות — המחשב יכבה בעוד %lld שניות · כל מקש מבטל",left);
    const Theme *t=a->theme;
    Text v=text_make(cr,FONT_SANS,14,600,msg,0,DIR_RTL);
    Rect b={(W-v.w-40)/2,H-N_STATUS_H-80,v.w+40,40};draw_fill_rrect(cr,b,t->radius,t->warning_soft);draw_border_rrect(cr,b,t->radius,t->warn,2);text_draw(cr,&v,b.x+20,b.y+(b.h-v.h)/2,t->warn);text_free(&v);
}

/* #1270 (owner, 10/10): no typed-word confirmation anywhere. A destructive
 * action asks here: the question, what will happen, [ביטול] (bordered) and
 * the red action; Enter confirms, Esc cancels (main.c confirm_modal). */
void draw_confirm_dialog(App *a,cairo_t *cr,double W,double H){
    if(!a->confirm)return;
    const Theme *t=a->theme;const State *s=&a->st;double usable=H-N_STATUS_H;
    char title[200],what[800],ok[120];
    if(a->confirm==CONFIRM_RESTORE){
        const Disk *d=NULL;for(int i=0;i<s->ndisks;i++)if(!s->disks[i].removable){d=&s->disks[i];break;}
        const char *img=a->restore_image_sel>0&&a->restore_image_sel<=s->nimages?s->images[a->restore_image_sel-1].name:"";
        snprintf(title,sizeof title,"למחוק את הדיסק ולשחזר?");
        /* the disk, the machine and the image are Latin runs inside an RTL line:
         * isolated (FSI/PDI); "model · /dev/sda" -- no brackets, which this renderer mirrors */
        char disk[200],disk_i[220],mach_i[120],img_i[200];
        snprintf(disk,sizeof disk,"%s · %s",d&&d->model[0]?d->model:"הדיסק הפנימי",d?d->dev:"—");
        bidi_isolate(disk,disk_i,sizeof disk_i);
        bidi_isolate(known(s->machine_name),mach_i,sizeof mach_i);
        bidi_isolate(img,img_i,sizeof img_i);
        snprintf(what,sizeof what,"כל התוכן של %s במחשב %s יימחק, והאימג' \"%s\" ייכתב עליו. אי־אפשר לבטל אחרי שהתחיל.",
                 disk_i,mach_i,img_i);
        snprintf(ok,sizeof ok,"מחק ושחזר");
    }else if(a->confirm==CONFIRM_ROOM_STOP||a->confirm==CONFIRM_CLASS_STOP){
        snprintf(title,sizeof title,"לעצור את הסבב?");
        snprintf(what,sizeof what,"השידור של \"%s\" ייעצר באמצע. מגירות שלא סיימו לא יהיו שמישות ויש לכתוב אותן מחדש.",
                 a->confirm==CONFIRM_ROOM_STOP?s->round_image:s->sess_image);
        snprintf(ok,sizeof ok,"עצור סבב");
    }else{
        const Tool *tl=a->tool_sel>=0&&a->tool_sel<a->ntools?&a->tools[a->tool_sel]:NULL;
        int lvl=tl?tools_risk_level(tl->risk):2;
        snprintf(title,sizeof title,"להריץ את \"%s\"?",tl?tl->title:"הכלי");
        snprintf(what,sizeof what,lvl==2?"פעולה הרסנית על המחשב %s: מה שיימחק לא יחזור.":"הפעולה משנה את הדיסק של המחשב %s.",
                 known(a->tools_machine));
        snprintf(ok,sizeof ok,"הרץ");
    }
    cairo_rectangle(cr,0,0,W,usable);draw_set_a(cr,(Rgb){0,0,0},.55);cairo_fill(cr);
    a->nhits=0;a->clip_on=0;                       /* modal: only its two buttons */
    double dw=fmin(520,W-64),inner=dw-56;
    Text h=rtl_block_make(cr,20,600,title,inner);
    Text p=rtl_block_make(cr,13,400,what,inner);
    double dh=28+h.h+8+p.h+24+N_BUTTON_H+28;
    Rect box={(W-dw)/2,(usable-dh)/2,dw,dh};
    draw_fill_rrect(cr,box,t->radius,t->surface);draw_border_rrect(cr,box,t->radius,t->hair,1);
    double right=box.x+box.w-28,y=box.y+28;
    text_draw_r(cr,&h,right,y,t->ink);y+=h.h+8;
    text_draw_r(cr,&p,right,y,t->muted);
    Text go=btn_label(cr,ok),no=btn_label(cr,"ביטול");
    double gw=btn_width(&go),nw=btn_width(&no),by=box.y+box.h-28-N_BUTTON_H;
    /* the approved window: the action at the inline end, cancel next to it */
    draw_btn(a,cr,(Rect){box.x+28,by,gw,N_BUTTON_H},&go,BTN_DANGER,HIT_CONFIRM_OK);
    draw_btn(a,cr,(Rect){box.x+28+gw+N_ACTION_GAP,by,nw,N_BUTTON_H},&no,BTN_PLAIN,HIT_CONFIRM_CANCEL);
    text_free(&h);text_free(&p);text_free(&go);text_free(&no);
}

/* clamp(H/800, 1, 1.4), taken down to a quarter: a 1px line then lands on
 * a quarter pixel at most (1080p = 1.25, 60px header, 45px status bar) --
 * 1.35 smeared every hairline over two rows. */
double ui_scale(int H){double s=floor(H/800.0*4)/4;return s<1?1:s>1.4?1.4:s;}

/* The screen in logical pixels: W x H = the framebuffer / a->scale. */
static void app_draw_logical(App *a,cairo_t *cr,double W,double H){
    a->nhits=0;a->clip_on=0;app_route(a);draw_station_background(cr,a->theme,W,H);
    if(a->screen==SCREEN_INSTALL){screen_install(a,cr,W,H);return;}
    double head_h=a->screen==SCREEN_LOGIN?0:draw_header(a,cr,W);
    switch(a->screen){
    case SCREEN_LOGIN:    screen_login(a, cr, W, H, head_h); break;
    case SCREEN_MENU:     screen_menu(a, cr, W, H, head_h); break;
    case SCREEN_PICK:     screen_pick(a, cr, W, H, head_h); break;
    case SCREEN_PROGRESS: screen_progress(a, cr, W, H, head_h); break;
    case SCREEN_DONE:     screen_done(a, cr, W, H, head_h); break;
    case SCREEN_ROOM:     screen_room(a, cr, W, H, head_h); break;
    case SCREEN_CLASS:    screen_class(a, cr, W, H, head_h); break;
    case SCREEN_CLONER:   screen_cloner(a, cr, W, H, head_h); break;
    case SCREEN_STANDBY:  screen_standby(a, cr, W, H, head_h); break;
    case SCREEN_MESSAGE:  screen_message(a, cr, W, H, head_h); break;
    case SCREEN_RESTORE:  screen_restore(a, cr, W, H, head_h); break;
    case SCREEN_TOOLS:    screen_tools(a, cr, W, H, head_h); break;     /* #649 */
    case SCREEN_INSTALL:  break;
    }
    draw_state_warning(a,cr,W,H);draw_idle_countdown(a,cr,W,H);
    draw_shrink_dialog(a,cr,W,H);   /* #928: modal, over every screen but the status bar */
    draw_confirm_dialog(a,cr,W,H);  /* #1270 */
    draw_status(a,cr,W,H);draw_toast(a,cr,W,H);
}

/* mockup 10/10 / audit finding 4: one factor for the whole screen, so a
 * 1080p screen is not a 1024x768 layout with more empty space. */
void app_draw(App *a,cairo_t *cr,int W,int H){
    double s=ui_scale(H);a->scale=s;
    cairo_save(cr);cairo_scale(cr,s,s);
    app_draw_logical(a,cr,W/s,H/s);
    cairo_restore(cr);
}

void app_draw_pointer(App *a,cairo_t *cr){if(a->ptr_visible)draw_cursor(cr,a->ptr_x,a->ptr_y);}
int app_hit(const App *a,double x,double y){double s=a->scale>0?a->scale:1;x/=s;y/=s;   /* screen pixels in, logical hits */
    for(int i=a->nhits-1;i>=0;i--){if(rect_has(a->hits[i].r,x,y))return a->hits[i].id;}return HIT_NONE;}
