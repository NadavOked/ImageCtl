#include <math.h>
#include <stdio.h>
#include <string.h>
#include "screens_rounds.h"

/* ---- the cloner screen (clonergui.sh, native-only) --------------------------
 *
 * A cloning machine writes 2-3 drawers at once and now has a screen. It shows
 * one row per drawer -- sorted and labelled by SATA port, never by device
 * order (roomflow.sh: sdb is not "disk 2") -- each with its own big bar, and
 * a dominant SMART panel when a drawer failed its health check. Unlike
 * screen_room this reads per-drawer progress (Drawer), not the aggregate
 * Machine.pct. */

static const char *drawer_state_he(const char *s) {
    if (!strcmp(s, "writing"))   return "כותב";
    if (!strcmp(s, "verifying")) return "מאמת";
    if (!strcmp(s, "done"))      return "הסתיים";
    if (!strcmp(s, "failed"))    return "נכשל";
    if (!strcmp(s, "skipped"))   return "דולג";
    if (!strcmp(s, "replacing")) return "להחלפה";
    return "ממתין";                                   /* waiting / empty */
}

/* "דיסק N" from the port, or the device name when no slot was derived. */
static void drawer_name(const Drawer *d, char *out, size_t n) {
    if (d->port > 0) snprintf(out, n, "דיסק %d", d->port);
    else             snprintf(out, n, "דיסק %s", d->dev);
}

/* Order to draw the drawers in: by port ascending, port 0 last. Stable. */
static void drawer_order(const State *s, int *idx) {
    for (int i = 0; i < s->ndrawers; i++) idx[i] = i;
    for (int i = 1; i < s->ndrawers; i++)
        for (int j = i; j > 0; j--) {
            int a = idx[j - 1], b = idx[j];
            int pa = s->drawers[a].port ? s->drawers[a].port : 1 << 20;
            int pb = s->drawers[b].port ? s->drawers[b].port : 1 << 20;
            if (pb < pa) { idx[j - 1] = b; idx[j] = a; } else break;
        }
}

void screen_cloner(App *a, cairo_t *cr, double W, double H, double head_h) {
    const Theme *t=a->theme; const State *s=&a->st;
    double inner=card_width(W)-2*N_PAD, btn_h=btn_height(cr);
    /* #410: the snapshot's age next to the image name -- a kiosk that
     * stopped writing the state shows a growing number, not a live look. */
    char age[64], hsub[200]; pace_age(s->updated,age,sizeof age);
    snprintf(hsub,sizeof hsub,"%s%s%s",s->cloner_image[0] ? s->cloner_image : "ממתין לסבב שיכפול",
             age[0] ? " · " : "",age);
    Head hd=head_make(cr,"מחשב שיכפול",hsub,inner);
    int idx[MAX_DRAWERS]; drawer_order(s,idx);
    /* ---- the SMART panel (measured first, drawn on top of dimmed rows) ---- */
    int panel = s->smart_pending;
    Text sp_title = { NULL, 0, 0 }, sp_sub = sp_title;
    Text sp_btn[3] = { { NULL, 0, 0 }, { NULL, 0, 0 }, { NULL, 0, 0 } };
    double panel_h = 0, panel_pad = N_CLONE_PAD;
    /* #872: a red disk (failed_last) has no "write anyway" -- replace, or
     * continue without it (= skip). Two buttons; the agent maps the click. */
    int red = panel && !strcmp(s->smart_verdict, "failed_last");
    int nbtn = red ? 2 : 3;
    if (panel) {
        char pt[96], ps[256];
        const char *what = red ? "נכשל בשיכפול הקודם" : "בעיית SMART";
        if (s->smart_port > 0) snprintf(pt, sizeof pt, "דיסק %d — %s", s->smart_port, what);
        else                   snprintf(pt, sizeof pt, "דיסק — %s", what);
        if (red)
            /* #874: the reason is the server's cause -- cable/slot or the disk. */
            snprintf(ps, sizeof ps, "%s — כבו, החליפו את הדיסק והדליקו, או המשיכו בלעדיו.",
                     !strcmp(s->smart_reason, "cable") ? "נכשל בכתיבה הקודמת · כבל/חריץ SATA"
                     : !strcmp(s->smart_reason, "disk") ? "נכשל בכתיבה הקודמת · הדיסק"
                     : "הדיסק נכשל בכתיבה הקודמת");
        else
            snprintf(ps, sizeof ps, "%s · %s — כבו, החליפו את הדיסק והדליקו, או בחרו אחרת.",
                     !strcmp(s->smart_verdict, "warn") ? "אזהרת בריאות" : "נכשל בבדיקה",
                     s->smart_reason[0] ? s->smart_reason : "unknown");
        sp_title = text_make(cr, FONT_SANS, N_CHOICE_TITLE, 700, pt, (int)(inner - 2 * panel_pad), DIR_RTL);
        sp_sub   = text_make(cr, FONT_SANS, N_SUB, 400, ps, (int)(inner - 2 * panel_pad), DIR_RTL);
        sp_btn[0] = btn_label(cr, "החלף דיסק");
        if (red) sp_btn[1] = btn_label(cr, "המשך");
        else   { sp_btn[1] = btn_label(cr, "כתוב בכל זאת"); sp_btn[2] = btn_label(cr, "דלג"); }
        panel_h = panel_pad + sp_title.h + N_LABEL_GAP + sp_sub.h + N_FORM_GAP + btn_h + panel_pad;
    }


    int cols=inner >= N_NARROW_W ? 3 : 1;
    double cw=(inner-(cols-1)*N_CLONE_GAP)/cols, tw=cw-2*N_CLONE_PAD;
    Text names[MAX_DRAWERS], devs[MAX_DRAWERS], states[MAX_DRAWERS], speeds[MAX_DRAWERS], errors[MAX_DRAWERS], percents[MAX_DRAWERS];
    int pcts[MAX_DRAWERS]; double row_h=0, written=0, total=0; int known=1;
    for(int k=0;k<s->ndrawers;k++) {
        const Drawer *d=&s->drawers[idx[k]];
        char name[80],nm[40],stat[256],b1[32],b2[32],pct[24];   /* #410: room for the pace */
        drawer_name(d,nm,sizeof nm);
        if(d->port>0) snprintf(name,sizeof name,"%s · SATA %d",nm,d->port-1);
        else snprintf(name,sizeof name,"%s",nm);
        pcts[k]=d->total>0 ? (int)floor(100.0*d->bytes/d->total+0.5) : -1;
        if(pcts[k]>100) pcts[k]=100;
        if(pcts[k]>=0) snprintf(pct,sizeof pct,"%d%%",pcts[k]);
        else snprintf(pct,sizeof pct,"--");
        fmt_bytes(b1,sizeof b1,(double)d->bytes);fmt_bytes(b2,sizeof b2,(double)d->total);
        /* .clone-speed: bytes, and since #410 the rate and ETA the kiosk
         * measured on this drawer (drawer= fields 7-8); unmeasured = bytes only. */
        char pace[96]; pace_text(-1,d->rate_bps,d->eta_s,1,pace,sizeof pace);   /* LTR line -> Latin words */
        if(pace[0]) snprintf(stat,sizeof stat,"%s / %s · %s",b1,b2,pace);
        else        snprintf(stat,sizeof stat,"%s / %s",b1,b2);
        /* .clone-card h3 13px; small 8px (the device -- the state has no model);
         * .clone-big 27px; .clone-status 8px: the state at the start, bytes at the end */
        names[k]=rtl_block_make(cr,N_CLONE_TITLE,600,name,tw);
        devs[k]=text_make(cr,FONT_MONO,N_CLONE_SUB,400,d->dev,0,DIR_LTR);
        states[k]=text_make(cr,FONT_SANS,N_CLONE_STATUS,600,drawer_state_he(d->state),0,DIR_RTL);
        speeds[k]=text_make(cr,FONT_SANS,N_CLONE_STATUS,400,stat,0,DIR_LTR);
        errors[k]=error_make(cr,d->error,tw);
        percents[k]=text_make(cr,FONT_MONO,N_CLONE_PCT,400,pct,0,DIR_LTR);
        row_h=dmax(row_h,2*N_CLONE_PAD+names[k].h+devs[k].h+N_LOGO_GAP+percents[k].h
                   +N_ACTION_GAP+N_BAR_H+N_ACTION_GAP+dmax(states[k].h,speeds[k].h)
                   +(d->error[0] ? N_ACTION_GAP+errors[k].h : 0));
        written+=(double)d->bytes;total+=(double)d->total;
        if(d->total==0) known=0;
    }
    Text idle[MAX_DRAWERS];double idle_h=0;
    int n_idle=s->ndrawers==0 && !panel ? s->nidisks : 0;
    for(int k=0;k<n_idle;k++) {
        const IdleDisk *d=&s->idisks[k];char sz[32],line[256];   /* #867/#874: room for the failed_last suffix and the cause */
        fmt_bytes(sz,sizeof sz,(double)d->size);
        /* #867/#874: the server's memory is said in words next to the dot,
         * with the cause it classified -- the cable/slot or the disk;
         * #872: so is unchecked -- green, because not-checked is not failed. */
        const char *fl = !strcmp(d->smart, "failed_last") ? " · נכשל בשיכפול הקודם"
                       : !strcmp(d->smart, "unchecked")   ? " · לא נבדק" : "";
        char why[48] = "";
        if (!strcmp(d->cause, "cable")) {
            if (d->port > 0) snprintf(why, sizeof why, " · כבל/חריץ SATA %d", d->port - 1);
            else             snprintf(why, sizeof why, " · כבל/חריץ");
        } else if (!strcmp(d->cause, "disk")) {
            snprintf(why, sizeof why, " · הדיסק");
        }
        if(d->port>0) snprintf(line,sizeof line,"דיסק %d (SATA %d): %s · %s%s%s",d->port,d->port-1,d->model[0]?d->model:"דיסק",sz,fl,why);
        else snprintf(line,sizeof line,"%s · %s%s%s",d->model[0]?d->model:"דיסק",sz,fl,why);
        idle[k]=rtl_block_make(cr,N_SUB,500,line,inner-2*N_CLONE_PAD-N_CHOICE_ICON);
        idle_h+=idle[k].h+2*N_CLONE_PAD+N_CLONE_GAP;
    }
    Text empty=sub_make(cr,panel ? "המתן…" : "אין עדיין מגירות בסבב — הן מופיעות כשהשידור מתחיל.",inner);
    /* nativeCloner .native-clone-overview: the source box (image name 10px,
     * small 8px, a bar of the aggregate) and .native-clone-stat boxes (label
     * 8px, value 15px). The mockup's three stats are total / rate / ETA; the
     * state has bytes and totals, so: סה״כ and נכתב. Then the section head:
     * label "שיכפול במקביל" + "N כוננים נכתבים יחד" 20px. */
    char aggregate[32]="--", written_s[32], total_s[32], src_small[96], section[64];
    if(known && total>0) snprintf(aggregate,sizeof aggregate,"%.0f%%",fmin(100,100*written/total));
    fmt_bytes(written_s,sizeof written_s,written); fmt_bytes(total_s,sizeof total_s,total);
    snprintf(src_small,sizeof src_small,"אימג' מקור · %s",known && total>0 ? total_s : "הסך לא ידוע");
    snprintf(section,sizeof section,"%d כוננים נכתבים יחד",s->ndrawers);
    double stat_w=(inner-2*N_CLONE_GAP)*0.7/(1.3+2*0.7), src_w=inner-2*(stat_w+N_CLONE_GAP);   /* 1.3fr + two .7fr, two gaps */
    Text src_t=rtl_block_make(cr,N_CLONE_SOURCE_TITLE,700,s->cloner_image[0]?s->cloner_image:"—",src_w-2*N_CLONE_STAT_PAD);
    Text srcs_t=rtl_block_make(cr,N_CLONE_STAT_LABEL,400,src_small,src_w-2*N_CLONE_STAT_PAD);
    double stat_lh=text_line_height(cr,FONT_SANS,N_CLONE_STAT_LABEL), stat_vh=text_line_height(cr,FONT_SANS,N_CLONE_STAT_VALUE);
    double ov_h=dmax(2*N_CLONE_STAT_PAD+stat_lh+2+stat_vh, 2*N_CLONE_STAT_PAD+src_t.h+srcs_t.h+N_STATUS_GAP+N_BAR_H);
    Text sec_l=label_make(cr,"שיכפול במקביל",inner), sec_t=rtl_block_make(cr,N_CLONER_TITLE,500,section,inner);
    double sec_h=sec_l.h+N_LABEL_GAP+sec_t.h;
    double grid_h=((s->ndrawers+cols-1)/cols)*(row_h+N_CLONE_GAP);
    double body_h=N_PAD+(s->ndrawers ? ov_h+N_ROOM_GAP+sec_h+N_ACTION_GAP+grid_h : n_idle ? idle_h : empty.h)
                  +(panel ? panel_h+N_FORM_GAP : 0);
    Rect body,foot;card_frame(a,cr,W,H,head_h,&hd,body_h,0,&body,&foot);
    body_clip_begin(a,cr,body);
    double x=body.x+N_PAD,y=body.y+N_PAD;
    if(panel) cairo_push_group(cr);
    if(s->ndrawers) {
        Rect src={x+inner-src_w,y,src_w,ov_h};
        draw_fill_rrect(cr,src,0,t->choice); draw_border_rrect(cr,src,0,t->round_line,N_BORDER);
        double sx=src.x+N_CLONE_STAT_PAD, sy=src.y+N_CLONE_STAT_PAD, sw=src_w-2*N_CLONE_STAT_PAD;
        text_draw(cr,&src_t,sx,sy,t->ink); sy+=src_t.h;
        text_draw(cr,&srcs_t,sx,sy,t->muted); sy+=srcs_t.h+N_STATUS_GAP;
        draw_big_bar(cr,t,(Rect){sx,sy,sw,N_BAR_H},known && total>0 ? (int)fmin(100,100*written/total) : -1,written>0);
        const char *sl[2]={"סה״כ","נכתב"}, *sv[2]={aggregate,written_s};
        for(int i=0;i<2;i++) {
            Rect st={src.x-(i+1)*(stat_w+N_CLONE_GAP),y,stat_w,ov_h};
            draw_fill_rrect(cr,st,0,t->choice); draw_border_rrect(cr,st,0,t->round_line,N_BORDER);
            Text l=rtl_block_make(cr,N_CLONE_STAT_LABEL,400,sl[i],stat_w-2*N_CLONE_STAT_PAD);
            Text v=rtl_block_make(cr,N_CLONE_STAT_VALUE,600,sv[i],stat_w-2*N_CLONE_STAT_PAD);
            text_draw(cr,&l,st.x+N_CLONE_STAT_PAD,st.y+N_CLONE_STAT_PAD,t->muted);
            text_draw(cr,&v,st.x+N_CLONE_STAT_PAD,st.y+N_CLONE_STAT_PAD+l.h+2,t->ink);
            text_free(&l); text_free(&v);
        }
        y+=ov_h+N_ROOM_GAP;
        text_draw(cr,&sec_l,x,y,t->muted); y+=sec_l.h+N_LABEL_GAP;
        text_draw(cr,&sec_t,x,y,t->ink);   y+=sec_t.h+N_ACTION_GAP;
    } else if(n_idle) {
        for(int k=0;k<n_idle;k++) {
            double h=idle[k].h+2*N_CLONE_PAD;
            Rect box={x,y,inner,h};
            draw_fill_rrect(cr,box,RADIUS_SM,t->disk_bg);
            draw_border_rrect(cr,box,RADIUS_SM,t->clone_line,N_BORDER);
            draw_fill_rrect(cr,(Rect){x+inner-N_CLONE_PAD-N_LABEL,y+N_CLONE_PAD,N_LABEL,N_LABEL},RADIUS_SM,smart_color(t,s->idisks[k].smart));
            text_draw(cr,&idle[k],x+N_CLONE_PAD,y+N_CLONE_PAD,t->ink);
            y+=h+N_CLONE_GAP;text_free(&idle[k]);
        }
    } else text_draw(cr,&empty,x,y,t->muted);
    for(int k=0;k<s->ndrawers;k++) {
        const Drawer *d=&s->drawers[idx[k]];
        Rect box={x+inner-cw-(k%cols)*(cw+N_CLONE_GAP),y+(k/cols)*(row_h+N_CLONE_GAP),cw,row_h};
        draw_fill_rrect(cr,box,RADIUS_SM,t->disk_bg);
        draw_border_rrect(cr,box,RADIUS_SM,t->clone_line,N_BORDER);
        double dx=box.x+N_CLONE_PAD,dy=box.y+N_CLONE_PAD;
        /* .success-text for writing/done, .smart-warn for a failure (the mockup's
         * status colours); the percentage itself stays ink like .clone-big */
        Rgb ink=!strcmp(d->state,"failed") ? t->danger : !strcmp(d->state,"done")||!strcmp(d->state,"writing") ? t->led_ok : t->muted;
        text_draw(cr,&names[k],dx,dy,t->ink);dy+=names[k].h;
        text_draw_r(cr,&devs[k],dx+tw,dy,t->muted);dy+=devs[k].h+N_LOGO_GAP;
        text_draw(cr,&percents[k],dx,dy,t->ink);dy+=percents[k].h+N_ACTION_GAP;
        draw_big_bar(cr,t,(Rect){dx,dy,tw,N_BAR_H},pcts[k],d->bytes>0);dy+=N_BAR_H+N_ACTION_GAP;
        double sh=dmax(states[k].h,speeds[k].h);
        text_draw_r(cr,&states[k],dx+tw,dy+(sh-states[k].h)/2,ink);
        text_draw(cr,&speeds[k],dx,dy+(sh-speeds[k].h)/2,t->muted);dy+=sh+N_ACTION_GAP;
        if(d->error[0]) text_draw(cr,&errors[k],dx,dy,t->danger);
        text_free(&names[k]);text_free(&devs[k]);text_free(&states[k]);text_free(&speeds[k]);text_free(&errors[k]);text_free(&percents[k]);
    }
    text_free(&empty);text_free(&src_t);text_free(&srcs_t);text_free(&sec_l);text_free(&sec_t);
    if(panel) {cairo_pop_group_to_source(cr);cairo_paint_with_alpha(cr,N_DIM_ALPHA);}
    /* The panel, fully opaque, above the dimmed rows -- the only hit targets. */
    if (panel) {
        double py = body.y + body.h - N_PAD - panel_h;      /* anchored at the body's foot */
        /* the mockup's .native-alert box: the one warning surface it defines */
        Rect box = { x, py, inner, panel_h };
        draw_fill_rrect(cr, box, RADIUS_SM, t->alert_bg);
        draw_border_rrect(cr, box, RADIUS_SM, t->alert_line, N_BORDER);
        double ty = box.y + panel_pad;
        text_draw_r(cr, &sp_title, box.x + inner - panel_pad, ty, t->ink);       ty += sp_title.h + N_LABEL_GAP;
        text_draw_r(cr, &sp_sub,   box.x + inner - panel_pad, ty, t->alert_ink); ty += sp_sub.h + N_FORM_GAP;
        /* buttons from the right: [Replace] [Rescue] [Skip]; red: [Replace] [Continue=Skip] */
        static const int kinds[3] = { BTN_PRIMARY, BTN_PLAIN, BTN_PLAIN };
        const int ids[3] = { HIT_SMART_REPLACE, red ? HIT_SMART_SKIP : HIT_SMART_RESCUE, HIT_SMART_SKIP };
        double bx = box.x + inner - panel_pad;
        for (int i = 0; i < nbtn; i++) {
            double bw = btn_width(&sp_btn[i]);
            bx -= bw;
            draw_btn(a, cr, (Rect){ bx, ty, bw, btn_h }, &sp_btn[i], kinds[i], ids[i]);
            bx -= 8;
            text_free(&sp_btn[i]);
        }
        text_free(&sp_title); text_free(&sp_sub);
    }

    body_clip_end(a,cr);card_end(a,cr);
}
