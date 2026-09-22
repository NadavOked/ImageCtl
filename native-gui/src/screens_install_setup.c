#include <math.h>
#include <stdio.h>
#include <string.h>
#include "screens_install.h"

static void disk_head(cairo_t *cr,const Theme *t,Rect r){
    draw_fill_rrect(cr,r,0,t->field);
    install_text_r(cr,t,"דגם",12,600,r.x+r.w-54,r.y+9,r.w*.35,t->muted);
    install_text_r(cr,t,"גודל",12,600,r.x+r.w*.58,r.y+9,r.w*.14,t->muted);
    install_text_r(cr,t,"חיבור",12,600,r.x+r.w*.42,r.y+9,r.w*.13,t->muted);
    install_text_r(cr,t,"מה נמצא בדיסק",12,600,r.x+r.w*.27,r.y+9,r.w*.25,t->muted);
}

static void disk_row(App *a,cairo_t *cr,Rect r,int i){
    InstallState *s=&a->install;InstallDisk *d=&s->disks[i];const Theme *t=a->theme;int selected=i==s->disk;
    draw_fill_rrect(cr,r,0,d->iso?t->field:(selected?t->danger_soft:t->surface));
    cairo_rectangle(cr,r.x,r.y+r.h-1,r.w,1);draw_set(cr,t->hair);cairo_fill(cr);
    double cx=r.x+r.w-22,cy=r.y+r.h/2;cairo_arc(cr,cx,cy,7,0,6.283);draw_set(cr,d->iso?t->muted:(selected?t->danger:t->hair));cairo_set_line_width(cr,2);cairo_stroke(cr);
    if(selected){cairo_arc(cr,cx,cy,3.5,0,6.283);draw_set(cr,t->danger);cairo_fill(cr);}
    install_text_ltr(cr,d->model[0]?d->model:d->path,13,600,r.x+r.w-285,r.y+9,220,d->iso?t->muted:t->ink);
    install_text_ltr(cr,d->path,10,400,r.x+r.w-285,r.y+31,220,t->muted);
    char size[32];snprintf(size,sizeof size,"%.1f GiB",d->size_bytes/1073741824.0);install_text_ltr(cr,size,12,500,r.x+r.w*.46,r.y+19,r.w*.14,d->iso?t->muted:t->ink);
    install_text_ltr(cr,d->bus[0]?d->bus:"—",12,400,r.x+r.w*.30,r.y+19,r.w*.12,t->muted);
    install_text_r(cr,t,d->iso?"מדיית ההתקנה":(d->has[0]?d->has:"—"),11,400,r.x+r.w*.28,r.y+14,r.w*.24,t->muted);
    if(selected)install_text_r(cr,t,"יימחק",11,600,r.x+68,r.y+18,58,t->danger);
    if(!d->iso)hit_add(a,r,HIT_INSTALL_DISK_BASE+i);
}

static void disk_ack(App *a,cairo_t *cr,Rect r){
    InstallState *s=&a->install;const Theme *t=a->theme;draw_fill_rrect(cr,r,t->radius,s->disk_ack?t->danger_soft:t->surface);draw_border_rrect(cr,r,t->radius,s->disk_ack?t->danger:t->hair,1);
    Rect box={r.x+r.w-38,r.y+12,20,20};draw_fill_rrect(cr,box,4,s->disk_ack?t->danger:t->field);draw_border_rrect(cr,box,4,s->disk_ack?t->danger:t->hair,1);
    if(s->disk_ack)install_text_r(cr,t,"✓",13,600,box.x+17,box.y+1,16,t->on_ink);
    install_text_r(cr,t,"אני מבין",13,600,r.x+r.w-50,r.y+13,r.w-64,t->ink);hit_add(a,r,HIT_INSTALL_DISK_ACK);
}

void install_draw_disk(App *a,cairo_t *cr,InstallLayout l){
    InstallState *s=&a->install;const Theme *t=a->theme;double x=l.body.x,w=l.body.w,y=l.body.y;
    Rect table={x,y,w,42+s->ndisks*58};install_card(cr,t,table);disk_head(cr,t,(Rect){x+1,y+1,w-2,40});
    for(int i=0;i<s->ndisks;i++)disk_row(a,cr,(Rect){x+1,y+41+i*58,w-2,58},i);y+=table.h+16;
    if(s->disk>=0&&s->disk<s->ndisks){char warning[260];snprintf(warning,sizeof warning,"כל הנתונים בדיסק %s יימחקו",s->disks[s->disk].model[0]?s->disks[s->disk].model:s->disks[s->disk].path);install_text_r(cr,t,warning,14,600,x+w,y,w,t->danger);y+=30;disk_ack(a,cr,(Rect){x+w-180,y,180,44});}
    else{install_text_r(cr,t,"לא נמצא דיסק חוקי להתקנה.",13,600,x+w,y,w,t->danger);y+=48;}
    if(s->field_error[0][0]){install_text_r(cr,t,s->field_error[0],12,600,x+w,y+50,w,t->danger);}
    if(s->existing_disk>=0&&s->existing_disk<s->ndisks){InstallDisk *d=&s->disks[s->existing_disk];Rect card={x,y+58,w,72};install_card(cr,t,card);install_text_r(cr,t,"נמצאה התקנה קיימת — אתחל מהדיסק",14,600,x+w-16,card.y+14,w-220,t->ink);install_text_ltr(cr,d->model,11,400,x+180,card.y+42,w-210,t->muted);Text boot=btn_label(cr,"אתחל מהדיסק");draw_btn(a,cr,(Rect){x+16,card.y+17,150,38},&boot,BTN_PLAIN,HIT_INSTALL_BOOT_LOCAL);text_free(&boot);}
    install_footer(a,cr,l,"הבא",HIT_INSTALL_NEXT,0,s->disk>=0&&s->disk_ack);
}

static void result_box(App *a,cairo_t *cr,Rect r){InstallState *s=&a->install;const Theme *t=a->theme;if(!s->primary_checked)return;Rgb bg=s->primary_ok?t->success_soft:t->danger_soft,line=s->primary_ok?t->success_ink:t->danger;draw_fill_rrect(cr,r,t->radius,bg);draw_border_rrect(cr,r,t->radius,line,1);
    if(s->primary_ok){char top[220];snprintf(top,sizeof top,"✓  השרת הראשי ענה · %s · %s",s->primary_name,s->primary_version);install_text_r(cr,t,top,13,600,r.x+r.w-14,r.y+10,r.w-28,line);install_text_ltr(cr,s->primary_fingerprint,11,400,r.x+14,r.y+35,r.w-28,t->ink);}else{char failure[380];snprintf(failure,sizeof failure,"התקשרות אל %s בפורט 8443 נכשלה. ודא כי חומת האש שבין האתרים פתוחה",s->primary_url);install_text_r(cr,t,s->primary_error[0]?s->primary_error:failure,13,600,r.x+r.w-14,r.y+12,r.w-28,line);}}

void install_draw_role(App *a,cairo_t *cr,InstallLayout l){InstallState *s=&a->install;const Theme *t=a->theme;double w=fmin(720,l.body.w),x=l.body.x+l.body.w-w,y=l.body.y;
    install_text_r(cr,t,"נבחר בתפריט ההתקנה",12,500,x+w,y,w,t->muted);y+=24;
    install_radio(a,cr,(Rect){x,y,w,74},"שרת ראשי","מנהל את הספרייה, השיכפול והשרתים המשניים.",!s->secondary,HIT_INSTALL_PRIMARY);y+=86;
    install_radio(a,cr,(Rect){x,y,w,74},"שרת משני","שרת באתר נוסף; הראשי יוזם אליו חיבור מאובטח בפורט 8443.",s->secondary,HIT_INSTALL_SECONDARY);y+=96;
    if(s->secondary){install_field_draw(a,cr,(Rect){x,y,w-150,56},"כתובת השרת הראשי",s->primary_url,0,HIT_INSTALL_PRIMARY_URL,1);Text check=btn_label(cr,"בדוק חיבור");draw_btn(a,cr,(Rect){x+w-138,y+19,138,36},&check,BTN_PLAIN,HIT_INSTALL_CHECK_PRIMARY);text_free(&check);y+=70;result_box(a,cr,(Rect){x,y,w,70});y+=82;}
    if(s->error[0])install_text_r(cr,t,s->error,13,600,x+w,y,w,t->danger);
    install_footer(a,cr,l,"הבא",HIT_INSTALL_NEXT,0,1);
}

static void table_head(cairo_t *cr,const Theme *t,Rect r){draw_fill_rrect(cr,r,0,t->field);const char *h[]={"כרטיס","קישור","כתובת עכשיו","מקור"};double x[]={r.x+r.w-55,r.x+r.w*.57,r.x+r.w*.39,r.x+r.w*.2};double widths[]={r.w*.34,r.w*.16,r.w*.18,r.w*.18};for(int i=0;i<4;i++)install_text_r(cr,t,h[i],12,600,x[i],r.y+9,widths[i],t->muted);}

static void nic_row(App *a,cairo_t *cr,Rect r,int i){InstallState *s=&a->install;InstallNic *n=&s->nics[i];const Theme *t=a->theme;int sel=i==s->nic;draw_fill_rrect(cr,r,0,sel?t->indigo_soft:t->surface);cairo_rectangle(cr,r.x,r.y+r.h-1,r.w,1);draw_set(cr,t->hair);cairo_fill(cr);
    double cx=r.x+r.w-22,cy=r.y+r.h/2;cairo_arc(cr,cx,cy,7,0,6.283);draw_set(cr,sel?t->indigo:t->hair);cairo_set_line_width(cr,2);cairo_stroke(cr);if(sel){cairo_arc(cr,cx,cy,3.5,0,6.283);draw_set(cr,t->indigo);cairo_fill(cr);}
    install_text_ltr(cr,n->name,13,600,r.x+r.w-210,r.y+9,145,t->ink);char model[150];snprintf(model,sizeof model,"%s · %s",n->model[0]?n->model:"כרטיס רשת",n->mac);install_text_ltr(cr,model,10,400,r.x+r.w-210,r.y+31,180,t->muted);
    install_text_r(cr,t,n->link,12,600,r.x+r.w*.56,r.y+20,r.w*.15,!strcmp(n->link,"מחובר")?t->success_ink:t->muted);
    install_text_ltr(cr,n->current[0]?n->current:"—",12,400,r.x+r.w*.21,r.y+20,r.w*.18,t->ink);
    install_text_r(cr,t,n->source[0]?n->source:"—",11,400,r.x+r.w*.19,r.y+15,r.w*.18,t->muted);hit_add(a,r,HIT_INSTALL_NIC_BASE+i);}

void install_draw_network(App *a,cairo_t *cr,InstallLayout l){InstallState *s=&a->install;const Theme *t=a->theme;double w=l.body.w,x=l.body.x,y=l.body.y;Rect table={x,y,w,42+s->nnics*58};install_card(cr,t,table);table_head(cr,t,(Rect){x+1,y+1,w-2,40});for(int i=0;i<s->nnics;i++)nic_row(a,cr,(Rect){x+1,y+41+i*58,w-2,58},i);y+=table.h+16;
    double half=(w-12)/2;install_radio(a,cr,(Rect){x+half+12,y,half,66},"לקוח DHCP (מומלץ)","הכתובת מגיעה משרת ה-DHCP של המכללה.",!s->static_mode,HIT_INSTALL_DHCP);install_radio(a,cr,(Rect){x,y,half,66},"כתובת סטטית","כשאין DHCP או כשהכתובת חייבת להישאר קבועה.",s->static_mode,HIT_INSTALL_STATIC);y+=79;
    double gap=12,fw=(w-gap)/2;install_field_draw(a,cr,(Rect){x+fw+gap,y,fw,56},"כתובת IP",s->address,0,HIT_INSTALL_ADDRESS,s->static_mode);install_field_draw(a,cr,(Rect){x,y,fw,56},"מסכת רשת",s->netmask,0,HIT_INSTALL_NETMASK,s->static_mode);y+=68;install_field_draw(a,cr,(Rect){x+fw+gap,y,fw,56},"שער (רשות)",s->gateway,0,HIT_INSTALL_GATEWAY,s->static_mode);install_field_draw(a,cr,(Rect){x,y,fw,56},"DNS (רשות)",s->dns,0,HIT_INSTALL_DNS,s->static_mode);
    const char *err="";for(int i=3;i<=8;i++)if(s->field_error[i][0]){err=s->field_error[i];break;}if(err[0])install_text_r(cr,t,err,12,600,x+w,y+62,w,t->danger);
    install_footer(a,cr,l,"הבא",HIT_INSTALL_NEXT,1,s->nic>=0);
}
