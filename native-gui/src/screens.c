/* Shared chrome and the #1090 login/menu/standby screens. The remaining
 * flows keep their existing state machine but use the same full-screen frame
 * from widgets.c. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "widgets.h"

static double draw_header(App *a, cairo_t *cr, double W) {
    const Theme *t=a->theme;
    Rect bar={0,0,W,N_HEADER_H}; draw_fill_rrect(cr,bar,0,t->header);
    double right=W-N_HEADER_PAD;
    Rect mark={right-26,(N_HEADER_H-26)/2,26,26};
    draw_brand_mark(cr,t,mark); right=mark.x-10;
    Text brand=text_make(cr,FONT_SANS,16,600,"ImageCtl",0,DIR_LTR);
    text_draw_r(cr,&brand,right,(N_HEADER_H-brand.h)/2,t->header_text); right-=brand.w+22;
    text_free(&brand);

    const int clone=a->force_cloner||a->force_standby||a->screen==SCREEN_CLONER||a->screen==SCREEN_STANDBY;
    const char *role=clone?"מחשב שיכפול 2":"מחשב בנייה";
    Text rt=text_make(cr,FONT_SANS,12,400,role,0,DIR_RTL);
    Rect pill={right-rt.w-18,(N_HEADER_H-20)/2,rt.w+18,20};
    draw_set_a(cr,t->header_text,.14); draw_rrect_path(cr,pill,10); cairo_fill(cr);
    text_draw_r(cr,&rt,right-9,pill.y+(pill.h-rt.h)/2,t->header_text); right=pill.x-22;
    text_free(&rt);

    const char *host=a->st.machine_name[0]?a->st.machine_name:(clone?"CLONER-02":"BUILD-01");
    const char *keys[]={"מכונה","שרת","גרסה"};
    const char *vals[]={host,"imagectl-srv · 10.10.10.1","v0.48.1"};
    for(int i=0;i<3;i++){
        Text k=text_make(cr,FONT_SANS,13,400,keys[i],0,DIR_RTL);
        Text v=text_make(cr,FONT_MONO,13,400,vals[i],0,DIR_LTR);
        text_draw_r(cr,&k,right,(N_HEADER_H-k.h)/2,(Rgb){1,1,1});
        right-=k.w+5;
        text_draw_r(cr,&v,right,(N_HEADER_H-v.h)/2,(Rgb){.8,.84,.87});
        right-=v.w+18;
        text_free(&k);text_free(&v);
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
    switch(a->screen){
    case SCREEN_LOGIN:return "ממתין לאימות";
    case SCREEN_MENU:return "בחירת פעולה";
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
    }
    return "מוכן";
}

static const char *status_keys(const App *a){
    switch(a->screen){
    case SCREEN_LOGIN:return "Enter  כניסה";
    case SCREEN_MENU:return "1–4  בחירה    Enter  אישור    Esc  התנתק";
    case SCREEN_ROOM:return "W  העֵר    N  גל הבא    Esc  עצור / חזרה";
    case SCREEN_CLONER:return !strcmp(a->st.smart_verdict,"failed_last")
        ? "Enter / S  המשך בלי הדיסק    R  החלף דיסק"
        : "Enter  כתוב בכל זאת    S  דלג    R  החלף דיסק";
    case SCREEN_RESTORE:return "1–9 / [ ]  בחירת אימג'    Tab  מעבר    Enter  מחק ושחזר    Esc  חזרה";
    case SCREEN_STANDBY:return "דיסקים: 1 · 2 · 3    SMART";
    case SCREEN_PICK:return "Tab  מעבר    Enter  התחלה    Esc  חזרה";
    case SCREEN_CLASS:return "Enter  התחלה    Esc  עצור / חזרה";
    case SCREEN_DONE:return "Enter  קליטה נוספת";
    case SCREEN_TOOLS:return "Tab  מעבר    Enter  הפעלה    Esc  חזרה";
    default:return "Esc  חזרה";
    }
}

static void draw_status(App *a,cairo_t *cr,double W,double H){
    const Theme *t=a->theme; Rect bar={0,H-N_STATUS_H,W,N_STATUS_H};
    draw_fill_rrect(cr,bar,0,t->status_bg); cairo_rectangle(cr,0,bar.y,W,1);draw_set(cr,t->hair);cairo_fill(cr);
    char b[96];Rgb dot;const char *word=status_word(a,b,sizeof b,&dot);
    Text w=text_make(cr,FONT_SANS,12,400,word,0,DIR_RTL);
    double rx=W-N_STATUS_PAD;
    cairo_arc(cr,rx-N_STATUS_DOT/2,bar.y+bar.h/2,N_STATUS_DOT/2,0,2*M_PI);draw_set(cr,dot);cairo_fill(cr);
    text_draw_r(cr,&w,rx-N_STATUS_DOT-N_STATUS_GAP,bar.y+(bar.h-w.h)/2,t->ink);text_free(&w);
    Text keys=text_make(cr,FONT_SANS,12,400,status_keys(a),0,DIR_RTL);
    text_draw(cr,&keys,N_STATUS_PAD,bar.y+(bar.h-keys.h)/2,t->muted);text_free(&keys);
}

static void underlined_field(App *a,cairo_t *cr,Rect r,const char *label,const char *value,int mask,int id){
    const Theme *t=a->theme; Text l=rtl_block_make(cr,12,400,label,r.w);
    text_draw_r(cr,&l,r.x+r.w,r.y,t->indigo); text_free(&l);
    Rect input={r.x,r.y+16,r.w,34};
    cairo_rectangle(cr,input.x,input.y+input.h-(a->focus==id?2:1),input.w,a->focus==id?2:1);
    draw_set(cr,a->focus==id?t->indigo:t->hair);cairo_fill(cr);
    char shown[256];
    if(mask&&value[0]){size_t k=0;for(const char *p=value;*p&&k+4<sizeof shown;p++)if((*p&0xc0)!=0x80){memcpy(shown+k,"\xe2\x80\xa2",3);k+=3;}shown[k]=0;}
    else snprintf(shown,sizeof shown,"%s",value);
    Text v=text_make_field(cr,shown,(int)(input.w-(id==HIT_PASS?46:0)),1);
    text_draw(cr,&v,input.x,input.y+(input.h-v.h)/2,t->ink);text_free(&v);hit_add(a,input,id);
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
    const char *host=a->st.machine_name[0]?a->st.machine_name:"BUILD-01";
    char lead_s[128];snprintf(lead_s,sizeof lead_s,"מחשב הבנייה %s",host);
    Text lead=rtl_block_make(cr,13,400,lead_s,fw);
    double content=title.h+6+lead.h+28+50+22+50+22+38+(a->error[0]?24:0);
    double y=(usable-content)/2;
    text_draw_r(cr,&title,x+fw,y,t->ink);y+=title.h+6;
    text_draw_r(cr,&lead,x+fw,y,t->muted);y+=lead.h+28;
    underlined_field(a,cr,(Rect){x,y,fw,50},"שם משתמש",a->user,0,HIT_USER);y+=72;
    underlined_field(a,cr,(Rect){x,y,fw,50},"סיסמה",a->pass,!a->show_pw,HIT_PASS);
    Text eye=text_make(cr,FONT_SANS,12,400,a->show_pw?"הסתר":"הצג",0,DIR_RTL);
    Rect er={x,y+20,eye.w+8,24};text_draw(cr,&eye,er.x+4,er.y+(er.h-eye.h)/2,t->indigo);hit_add(a,er,HIT_EYE);text_free(&eye);
    y+=72;
    if(a->error[0]){Text e=rtl_block_make(cr,13,400,a->error,fw);text_draw_r(cr,&e,x+fw,y-16,t->danger);text_free(&e);}
    Text go=btn_label(cr,"כניסה");draw_btn(a,cr,(Rect){x,y,fw,38},&go,BTN_PRIMARY,HIT_SUBMIT);text_free(&go);
    char foot_s[160];snprintf(foot_s,sizeof foot_s,"imagectl-srv   ·   v0.48.1   ·   10.10.10.1");
    Text foot=text_make(cr,FONT_MONO,12,400,foot_s,0,DIR_LTR);text_draw(cr,&foot,pr.x+64,usable-28-foot.h,t->muted);text_free(&foot);
    text_free(&title);text_free(&lead);
}

typedef struct{const char *title,*desc,*how[3];int id,multi,admin,cls;}MenuItem;
#define MENU_N 5
static const MenuItem MENU[MENU_N]={
 { "שיכפול מהשרת","אימג' מהספרייה משודר במולטיקאסט לכל מחשבי השיכפול בחדר, בגלים.",{"בוחרים אימג' ויעד","החדר מתעורר ומצטרף","כל גל כותב למגירות המוכנות"},HIT_ROOM,1,0,0},
 { "שיכפול ישיר","הדיסק של המחשב הזה נקרא פעם אחת ומשודר ישירות למגירות — בלי לשמור אימג' בשרת.",{"קריאה בלבד מהדיסק הזה","סבב יחיד","למי שנבחר בלבד"},HIT_DIRECT,1,0,0},
 { "שחזור לדיסק הזה","אימג' מהספרייה נכתב על הדיסק הפנימי של המחשב הזה — כמו שיכפול בודד.",{"בוחרים אימג'","מקלידים את שם המכונה","הדיסק נמחק ונכתב"},HIT_RESTORE,0,0,0},
 { "קליטת אימג' חדש","הדיסק של המחשב הזה נקרא, מכווץ ומועלה לספרייה כאימג' חדש או כגרסה.",{"בדיקת SMART לפני","sha256 בקליטה","שם, תיאור, תיקייה"},HIT_CAPTURE,0,1,0},
 /* #1081: classes are v1.2 -- drawn only when the state file says menu_class=1 (absent = 0). */
 { "הפצה לכיתות","בוחרים כיתה, מחשבים ואימג' — הסבב מעיר את הכיתה ורץ בשרת.",{"בוחרים כיתה ואימג'","הכיתה מתעוררת ומצטרפת","הסבב רץ בשרת"},HIT_CLASSES,1,0,1}
};

void screen_menu(App *a,cairo_t *cr,double W,double H,double head_h){
    const Theme *t=a->theme;double right=W-N_PAD,y=head_h+28,inner=W-2*N_PAD;
    int show[MENU_N] = {1, 1, 1, 1, 1};
    Text title=rtl_block_make(cr,24,600,"מה לעשות עם המחשב הזה?",inner);
    char ss[160];snprintf(ss,sizeof ss,"מחוברים כ־%s · מספר או Enter על הכרטיס",a->signed_user);
    Text sub=rtl_block_make(cr,13,400,ss,inner);
    text_draw_r(cr,&title,right,y,t->ink);text_draw_r(cr,&sub,right,y+title.h+3,t->muted);
    double pillr=W*.52;double pw=draw_pill(cr,t,pillr,y+3,"השרת מחובר",PILL_OK,1);
    draw_pill(cr,t,pillr-pw-10,y+3,"מחשבי שיכפול ערים",PILL_INFO,0);
    double line_y=y+title.h+3+sub.h+12;cairo_rectangle(cr,N_PAD,line_y,inner,1);draw_set(cr,t->hair);cairo_fill(cr);
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
        draw_btn(a, cr, (Rect){ N_PAD, line_y - 12 - N_BUTTON_H, tools_w, N_BUTTON_H }, &tools_l, BTN_PLAIN, HIT_TOOLS);
        text_free(&tools_l);
    }
    int count=(a->admin?4:3)+(a->st.menu_class?1:0),cols=count,focus=0;double gy=line_y+19,bottom=H-N_STATUS_H-24-38-18;
    double cw=(inner-(cols-1)*18)/cols,ch=bottom-gy;
    Rect body={0,line_y+1,W,bottom-line_y-1};   /* .sbody: the card grid, clipped */
    body_clip_begin(a, cr, body);
    for(int i=0;i<MENU_N;i++){
        if(!show[i]||(MENU[i].admin&&!a->admin)||(MENU[i].cls&&!a->st.menu_class))continue;
        Rect c={right-cw-focus*(cw+18),gy,cw,ch};int sel=hovered(a,c)||a->menu_focus==focus;
        draw_fill_rrect(cr,c,t->radius,t->surface);draw_border_rrect(cr,c,t->radius,sel?t->indigo:t->hair,sel?2:1);
        double cx=c.x+26,cright=c.x+c.w-26,cy=c.y+28;
        draw_fill_rrect(cr,(Rect){cright-52,cy,52,52},8,t->indigo_soft);
        draw_native_icon(cr,t,(Rect){cright-40,cy+12,28,28},MENU[i].multi?ICON_NETWORK:ICON_DISK);
        char key[12];snprintf(key,sizeof key,"%d",focus+1);Text kt=text_make(cr,FONT_MONO,13,400,key,0,DIR_LTR);
        Rect kb={c.x+16,c.y+16,kt.w+16,24};draw_fill_rrect(cr,kb,3,t->field);draw_border_rrect(cr,kb,3,t->hair,1);text_draw(cr,&kt,kb.x+8,kb.y+(kb.h-kt.h)/2,t->ink);text_free(&kt);
        cy+=66;if(MENU[i].admin)draw_pill(cr,t,c.x+80,cy,"admin",PILL_WARN,0);
        Text h2=rtl_block_make(cr,22,600,MENU[i].title,MENU[i].admin?c.w-132:c.w-52);text_draw_r(cr,&h2,cright,cy,t->ink);cy+=h2.h+10;
        Text d=rtl_block_make(cr,14,400,MENU[i].desc,c.w-52);text_draw_r(cr,&d,cright,cy,t->muted);
        double hy=c.y+c.h-26-3*24-12;cairo_rectangle(cr,cx,hy-12,c.w-52,1);draw_set(cr,t->hair);cairo_fill(cr);
        for(int k=0;k<3;k++){cairo_arc(cr,cright-3,hy+8+24*k,3,0,2*M_PI);draw_set(cr,t->hair);cairo_fill(cr);Text q=rtl_block_make(cr,13,400,MENU[i].how[k],c.w-70);text_draw_r(cr,&q,cright-14,hy+24*k,t->ink);text_free(&q);}
        text_free(&h2);text_free(&d);hit_add(a,c,MENU[i].id);focus++;
    }
    body_clip_end(a, cr);
    Text lo=btn_label(cr,"התנתק");double lw=btn_width(&lo);draw_btn(a,cr,(Rect){N_PAD,H-N_STATUS_H-24-38,lw,38},&lo,BTN_PLAIN,HIT_LOGOUT);text_free(&lo);
    text_free(&title);text_free(&sub);
}

void screen_standby(App *a,cairo_t *cr,double W,double H,double head_h){
    const Theme *t=a->theme;Rect area={0,head_h,W,H-head_h-N_STATUS_H};draw_station_art(cr,t,area,.5);
    double center=W/2,y=head_h+(area.h-330)/2;Rect mark={center-36,y,72,72};draw_brand_mark(cr,t,mark);y+=94;
    Text h=text_make(cr,FONT_SANS,30,600,"ממתין לשרת",0,DIR_RTL);text_draw(cr,&h,center-h.w/2,y,t->ink);y+=h.h+8;text_free(&h);
    Text p=text_make(cr,FONT_SANS,15,400,"אין סבב פתוח. המחשב יצטרף לבד כשייפתח סבב.",0,DIR_RTL);text_draw(cr,&p,center-p.w/2,y,t->muted);y+=p.h+26;text_free(&p);
    char addr[256];snprintf(addr,sizeof addr,"%s     %s     imagectl-srv · 10.10.10.1",a->ip[0]?a->ip:"—",a->mac[0]?a->mac:"—");
    Text ad=text_make(cr,FONT_MONO,22,400,addr,0,DIR_LTR);text_draw(cr,&ad,center-ad.w/2,y,t->ink);y+=ad.h+36;text_free(&ad);
    char hello[160];
    if(a->st.hello_age>=0&&a->st.hello_rc>=0)snprintf(hello,sizeof hello,"hello אחרון לפני %d שניות · השרת ענה %d",a->st.hello_age,a->st.hello_rc);
    else snprintf(hello,sizeof hello,"טרם התקבלה ראיית hello מהסוכן");
    Text he=text_make(cr,FONT_SANS,13,400,hello,0,DIR_RTL);Rect hb={center-(he.w+48)/2,y,he.w+48,32};draw_fill_rrect(cr,hb,16,t->surface);draw_border_rrect(cr,hb,16,t->hair,1);
    struct timespec ts; double pulse=9;
    if(timespec_get(&ts,TIME_UTC)==TIME_UTC) pulse=8+2*(.5+.5*sin((ts.tv_sec+ts.tv_nsec/1e9)*4));
    draw_led(cr,hb.x+18,hb.y+16,pulse,a->st.hello_rc==200?t->led_ok:t->warn);text_draw_r(cr,&he,hb.x+hb.w-14,hb.y+(hb.h-he.h)/2,t->muted);text_free(&he);
}

static void draw_cursor(cairo_t *cr,double x,double y){
    static const double P[][2]={{0,0},{0,16},{4,12},{7,19},{9.5,18},{6.5,11},{11.5,11}};
    cairo_move_to(cr,x,y);for(int i=1;i<7;i++)cairo_line_to(cr,x+P[i][0],y+P[i][1]);cairo_close_path(cr);
    cairo_set_source_rgb(cr,1,1,1);cairo_fill_preserve(cr);cairo_set_source_rgb(cr,0,0,0);cairo_set_line_width(cr,1);cairo_stroke(cr);
}

void app_route(App *a){
    const State *s=&a->st;
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

void app_draw(App *a,cairo_t *cr,int W,int H){
    a->nhits=0;a->clip_on=0;app_route(a);draw_station_background(cr,a->theme,W,H);
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
    }
    draw_state_warning(a,cr,W,H);draw_status(a,cr,W,H);draw_toast(a,cr,W,H);
}

void app_draw_pointer(App *a,cairo_t *cr){if(a->ptr_visible)draw_cursor(cr,a->ptr_x,a->ptr_y);}
int app_hit(const App *a,double x,double y){for(int i=a->nhits-1;i>=0;i--)if(rect_has(a->hits[i].r,x,y))return a->hits[i].id;return HIT_NONE;}
