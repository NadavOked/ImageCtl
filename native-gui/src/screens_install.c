#include <math.h>
#include <stdio.h>
#include <string.h>
#include "screens_install.h"

static const char *STEP_NAMES[]={"דיסק","תפקיד השרת","כתובת השרת","שם השרת","חשבון ניהול","החלה","סיום"};

void install_text_r(cairo_t *cr,const Theme *t,const char *v,double px,int weight,double right,double y,double width,Rgb color){(void)t;Text x=text_make(cr,FONT_SANS,px,weight,v,(int)width,DIR_RTL);text_draw_r(cr,&x,right,y,color);text_free(&x);}
void install_text_ltr(cairo_t *cr,const char *v,double px,int weight,double x,double y,double width,Rgb color){Text q=text_make(cr,FONT_MONO,px,weight,v,(int)width,DIR_LTR);text_draw(cr,&q,x,y,color);text_free(&q);}
void install_card(cairo_t *cr,const Theme *t,Rect r){draw_fill_rrect(cr,r,t->radius,t->surface);draw_border_rrect(cr,r,t->radius,t->hair,1);}

static int current_step(const InstallState *s){if(s->view==INSTALL_PROGRESS||s->view==INSTALL_FAILED)return 6;if(s->view==INSTALL_DONE)return 7;return s->view;}

InstallLayout install_frame(App *a,cairo_t *cr,double W,double H){const Theme *t=a->theme;double rw=W<1500?250:300;InstallLayout l={{0,0,W-rw,H},{32,105,W-rw-64,H-185},{0,H-64,W-rw,64},{W-rw,0,rw,H},W-rw-32};
    draw_fill_rrect(cr,(Rect){0,0,W,H},0,t->porcelain);draw_fill_rrect(cr,l.rail,0,t->header);
    Rect mark={l.rail.x+l.rail.w-58,28,30,30};draw_brand_mark(cr,t,mark);install_text_ltr(cr,"ImageCtl",19,600,l.rail.x+30,31,l.rail.w-100,t->header_text);
    install_text_r(cr,t,"התקנת השרת",13,400,l.rail.x+l.rail.w-28,72,l.rail.w-56,t->header_text);
    int cur=current_step(&a->install);double y=124;
    for(int i=0;i<7;i++,y+=65){int done=i+1<cur,active=i+1==cur,bad=a->install.view==INSTALL_FAILED&&i==5;Rgb c=bad?t->danger:(done?t->success_ink:(active?t->header_text:t->muted));
        cairo_arc(cr,l.rail.x+l.rail.w-45,y,15,0,2*M_PI);draw_set_a(cr,c,active||done||bad?1:.35);cairo_fill(cr);
        char num[8];snprintf(num,sizeof num,done?"✓":"%d",i+1);Text n=text_make(cr,FONT_SANS,12,600,num,0,DIR_RTL);text_draw(cr,&n,l.rail.x+l.rail.w-45-n.w/2,y-n.h/2,t->on_ink);text_free(&n);
        install_text_r(cr,t,STEP_NAMES[i],14,active?600:400,l.rail.x+l.rail.w-70,y-9,l.rail.w-95,c);
        if(i<6){cairo_rectangle(cr,l.rail.x+l.rail.w-46,y+17,2,31);draw_set_a(cr,done?t->success_ink:t->muted,.45);cairo_fill(cr);}
    }
    const char *titles[]={"","בחירת דיסק להתקנה","תפקיד השרת במערכת","כתובת השרת","שם השרת","חשבון הניהול","סיכום לפני החלה","סיום ההתקנה","מתקין את ImageCtl…","ההתקנה נכשלה"};
    const char *subs[]={"","הדיסק שנבחר יימחק וישמש את שרת ImageCtl.","התפקיד נבחר בתפריט ההתקנה. אפשר לאשר או לשנות לפני ההחלה.","הכרטיס שדרכו מגיעים לקונסולה; רשת ההפצה מוגדרת אחר כך מהקונסולה","השם שיופיע בקונסולה, במסך השרת ובתעודת TLS.","המשתמש הראשון בקונסולה. משתמשים נוספים מוגדרים אחרי ההתקנה.","מה שיוחל על השרת. כל שורה ניתנת לשינוי.","הסר את מדיית ההתקנה והפעל מחדש.","אל תכבה את השרת בזמן ההתקנה.","התקלה מוצגת בשם שהחזיר מנוע ההתקנה."};
    install_text_r(cr,t,titles[a->install.view],26,600,l.right,28,l.body.w,t->ink);install_text_r(cr,t,subs[a->install.view],14,400,l.right,65,l.body.w,t->muted);if(a->install.error[0]&&a->install.view!=INSTALL_FAILED&&a->install.view!=INSTALL_PROGRESS)install_text_r(cr,t,a->install.error,11,600,l.right,87,l.body.w,t->danger);
    cairo_rectangle(cr,0,l.footer.y,l.footer.w,1);draw_set(cr,t->hair);cairo_fill(cr);draw_fill_rrect(cr,l.footer,0,t->surface);return l;}

void install_field_draw(App *a,cairo_t *cr,Rect r,const char *label,const char *value,int mask,int id,int enabled){const Theme *t=a->theme;install_text_r(cr,t,label,12,500,r.x+r.w,r.y,r.w,enabled?t->muted:t->hair);Rect f={r.x,r.y+19,r.w,36};draw_fill_rrect(cr,f,t->radius,t->field);draw_border_rrect(cr,f,t->radius,a->focus==id&&enabled?t->indigo:t->hair,a->focus==id&&enabled?2:1);
    char shown[300];if(mask&&value[0]){size_t k=0;for(const char *p=value;*p&&k+4<sizeof shown;p++)if((*p&0xc0)!=0x80){memcpy(shown+k,"\xe2\x80\xa2",3);k+=3;}shown[k]=0;}else snprintf(shown,sizeof shown,"%s",value);
    Text text=text_make(cr,FONT_MONO,14,400,shown,(int)f.w-24,DIR_LTR);text_draw(cr,&text,f.x+12,f.y+8,enabled?t->ink:t->muted);if(enabled&&a->focus==id){double caret=f.x+12+text.w+1;cairo_rectangle(cr,caret,f.y+8,1,text.h);draw_set(cr,t->indigo);cairo_fill(cr);}text_free(&text);if(enabled)hit_add(a,f,id);}

void install_radio(App *a,cairo_t *cr,Rect r,const char *title,const char *desc,int selected,int id){const Theme *t=a->theme;draw_fill_rrect(cr,r,t->radius,selected?t->indigo_soft:t->surface);draw_border_rrect(cr,r,t->radius,selected?t->indigo:t->hair,selected?2:1);double cx=r.x+r.w-24,cy=r.y+23;cairo_arc(cr,cx,cy,8,0,2*M_PI);draw_set(cr,selected?t->indigo:t->hair);cairo_set_line_width(cr,2);cairo_stroke(cr);if(selected){cairo_arc(cr,cx,cy,4,0,2*M_PI);draw_set(cr,t->indigo);cairo_fill(cr);}install_text_r(cr,t,title,15,600,r.x+r.w-44,r.y+12,r.w-58,t->ink);install_text_r(cr,t,desc,12,400,r.x+r.w-20,r.y+42,r.w-40,t->muted);hit_add(a,r,id);}

static void disabled_btn(cairo_t *cr,const Theme *t,Rect r,const char *label){draw_fill_rrect(cr,r,t->radius,t->field);draw_border_rrect(cr,r,t->radius,t->hair,1);Text q=btn_label(cr,label);text_draw(cr,&q,r.x+(r.w-q.w)/2,r.y+(r.h-q.h)/2,t->muted);text_free(&q);}
void install_footer(App *a,cairo_t *cr,InstallLayout l,const char *primary,int primary_id,int allow_back,int enabled){const Theme *t=a->theme;double y=l.footer.y+13;Text p=btn_label(cr,primary);double pw=fmax(118,btn_width(&p));Rect pr={l.footer.w-32-pw,y,pw,38};if(enabled)draw_btn(a,cr,pr,&p,BTN_PRIMARY,primary_id);else disabled_btn(cr,t,pr,primary);text_free(&p);if(allow_back){Text b=btn_label(cr,"חזור");double bw=btn_width(&b);draw_btn(a,cr,(Rect){pr.x-bw-12,y,bw,38},&b,BTN_PLAIN,HIT_INSTALL_BACK);text_free(&b);}char step[48];snprintf(step,sizeof step,"שלב %d מתוך 7",current_step(&a->install));install_text_r(cr,t,step,12,400,170,y+10,140,t->muted);}

void screen_install(App *a,cairo_t *cr,double W,double H){InstallLayout l=install_frame(a,cr,W,H);switch(a->install.view){case INSTALL_DISK:install_draw_disk(a,cr,l);break;case INSTALL_ROLE:install_draw_role(a,cr,l);break;case INSTALL_NETWORK:install_draw_network(a,cr,l);break;case INSTALL_HOSTNAME:case INSTALL_ADMIN:install_draw_forms(a,cr,l);break;default:install_draw_final(a,cr,l);break;}}

int install_render_png(App *a,const char *prefix,int w,int h){static const struct{const char *name;int view;} cards[]={{"install-0-disk",INSTALL_DISK},{"install-1-role",INSTALL_ROLE},{"install-2-servers-net",INSTALL_NETWORK},{"install-3-hostname",INSTALL_HOSTNAME},{"install-4-admin",INSTALL_ADMIN},{"install-5-summary",INSTALL_SUMMARY},{"install-5c-progress",INSTALL_PROGRESS},{"install-5b-apply-failed",INSTALL_FAILED},{"install-6-done",INSTALL_DONE}};const Theme *themes[]={&THEME_LIGHT,&THEME_DARK};
    for(size_t i=0;i<sizeof cards/sizeof cards[0];i++)for(int t=0;t<2;t++){install_demo(a,cards[i].view);a->theme=themes[t];cairo_surface_t *sf=cairo_image_surface_create(CAIRO_FORMAT_RGB24,w,h);cairo_t *cr=cairo_create(sf);screen_install(a,cr,w,h);cairo_destroy(cr);char path[512];snprintf(path,sizeof path,"%s-%s-%s.png",prefix,cards[i].name,themes[t]->id);cairo_status_t st=cairo_surface_write_to_png(sf,path);cairo_surface_destroy(sf);if(st!=CAIRO_STATUS_SUCCESS){fprintf(stderr,"native-gui: %s: %s\n",path,cairo_status_to_string(st));return 1;}}return 0;}
