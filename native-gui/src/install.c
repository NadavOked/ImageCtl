#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/wait.h>
#include "install.h"

typedef struct { const char *name, *label; } EngineState;
static const EngineState ENGINE_STATES[] = {
    {"ready", "מוכן להתחלה"},
    {"partitioning", "מחלק את הדיסק"},
    {"bootstrap", "מתקין את מערכת הבסיס"},
    {"packages", "מתקין את חבילות ImageCtl"},
    {"bootloader", "מתקין את מנהל האתחול"},
    {"finishing", "כותב הגדרות"},
    {"done", "ההתקנה הושלמה"},
    {"failed", "ההתקנה נכשלה"},
};

const char *install_phase_label(const char *phase) {
    for (size_t i = 0; i < sizeof ENGINE_STATES / sizeof ENGINE_STATES[0]; i++)
        if (!strcmp(phase, ENGINE_STATES[i].name)) return ENGINE_STATES[i].label;
    return "מצב התקנה לא מוכר";
}

static int engine_state_index(const char *phase) {
    for (size_t i = 0; i < sizeof ENGINE_STATES / sizeof ENGINE_STATES[0]; i++)
        if (!strcmp(phase, ENGINE_STATES[i].name)) return (int)i;
    return -1;
}

static double monotonic_s(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static void json_string(char **dst, size_t *left, const char *value) {
    if (*left < 2) return;
    *(*dst)++ = '"'; (*left)--;
    for (const unsigned char *p = (const unsigned char *)value; *p && *left > 2; p++) {
        if (*p == '"' || *p == '\\') { *(*dst)++ = '\\'; (*left)--; }
        if (*p < ' ') continue;
        *(*dst)++ = (char)*p; (*left)--;
    }
    if (*left) { *(*dst)++ = '"'; (*left)--; **dst = 0; }
}

static void json_pair(char **p, size_t *n, const char *key, const char *value, int comma) {
    int k = snprintf(*p, *n, "%s\"%s\":", comma ? "," : "", key);
    if (k < 0 || (size_t)k >= *n) return;
    *p += k; *n -= (size_t)k;
    json_string(p, n, value);
}

static void config_json(const App *a, char *out, size_t n, int step) {
    const InstallState *s = &a->install;
    char *p = out; *p++ = '{'; n--;
    json_pair(&p, &n, "disk", s->disk >= 0 && s->disk < s->ndisks ? s->disks[s->disk].path : "", 0);
    json_pair(&p, &n, "role", s->secondary ? "secondary" : "standalone", 1);
    json_pair(&p, &n, "primary_url", s->primary_url, 1);
    json_pair(&p, &n, "interface", s->nnics && s->nic >= 0 ? s->nics[s->nic].name : "", 1);
    json_pair(&p, &n, "mode", s->static_mode ? "static" : "dhcp", 1);
    json_pair(&p, &n, "address", s->address, 1); json_pair(&p, &n, "netmask", s->netmask, 1);
    json_pair(&p, &n, "gateway", s->gateway, 1); json_pair(&p, &n, "dns", s->dns, 1);
    json_pair(&p, &n, "hostname", s->hostname, 1); json_pair(&p, &n, "admin_user", s->admin_user, 1);
    json_pair(&p, &n, "password", s->password, 1); json_pair(&p, &n, "password_confirm", s->confirm, 1);
    json_pair(&p, &n, "current_password", s->current_password, 1);
    if (step > 0) { int k=snprintf(p,n,",\"step\":%d",step); if(k>0&&(size_t)k<n){p+=k;n-=(size_t)k;} }
    if (n > 1) { *p++ = '}'; *p = 0; }
}

static int run_bridge(const InstallBridge *b, const char *action, const char *input, char *out, size_t n) {
    int in_p[2],out_p[2]; if(pipe(in_p)||pipe(out_p))return -1;
    pid_t pid=fork(); if(pid<0)return -1;
    if(pid==0){dup2(in_p[0],0);dup2(out_p[1],1);close(in_p[0]);close(in_p[1]);close(out_p[0]);close(out_p[1]);
        if(b->cwd&&chdir(b->cwd))_exit(126);char command[1024];snprintf(command,sizeof command,"%s %s",b->cmd,action);
        execl("/bin/sh","sh","-c",command,(char *)NULL);_exit(127);}
    close(in_p[0]);close(out_p[1]);FILE *w=fdopen(in_p[1],"w");
    if(w){fputs(input,w);fputc('\n',w);fclose(w);}else close(in_p[1]);
    size_t used=0;while(used+1<n){ssize_t got=read(out_p[0],out+used,n-used-1);if(got<=0)break;used+=(size_t)got;}
    out[used]=0;close(out_p[0]);int status=0;waitpid(pid,&status,0);
    return WIFEXITED(status)&&WEXITSTATUS(status)==0?0:-1;
}

static int truth(const char *v){return !strcmp(v,"1")||!strcmp(v,"true")||!strcmp(v,"True");}
static int error_slot(const char *key){static const char *names[]={"disk","role","primary_url","interface","mode","address","netmask","gateway","dns","hostname","admin_user","password","password_confirm","current_password"};for(int i=0;i<14;i++)if(!strcmp(key,names[i]))return i;return -1;}

static void parse_disk(InstallState *s,char *value){
    if(s->ndisks>=INSTALL_MAX_DISKS)return;InstallDisk *d=&s->disks[s->ndisks];memset(d,0,sizeof *d);
    char *save=NULL,*part=strtok_r(value,"|",&save);if(!part)return;snprintf(d->path,sizeof d->path,"%s",part);
    while((part=strtok_r(NULL,"|",&save))){char *eq=strchr(part,'=');if(!eq)continue;*eq++=0;
        if(!strcmp(part,"model"))snprintf(d->model,sizeof d->model,"%s",eq);else if(!strcmp(part,"size"))d->size_bytes=strtoull(eq,NULL,10);
        else if(!strcmp(part,"bus"))snprintf(d->bus,sizeof d->bus,"%s",eq);else if(!strcmp(part,"removable"))d->removable=truth(eq);
        else if(!strcmp(part,"has"))snprintf(d->has,sizeof d->has,"%s",eq);else if(!strcmp(part,"iso"))d->iso=truth(eq);}
    if(!d->iso&&s->disk<0)s->disk=s->ndisks;if(strstr(d->has,"imagectl")||strstr(d->has,"ImageCtl"))s->existing_disk=s->ndisks;s->ndisks++;
}

static void parse_nic(InstallState *s,char *v){
    if(s->nnics>=INSTALL_MAX_NICS)return;InstallNic *d=&s->nics[s->nnics++];
    char *fields[]={d->name,d->model,d->mac,d->link,d->current,d->source,d->address,d->netmask,d->gateway,d->dns};
    size_t sizes[]={sizeof d->name,sizeof d->model,sizeof d->mac,sizeof d->link,sizeof d->current,sizeof d->source,sizeof d->address,sizeof d->netmask,sizeof d->gateway,sizeof d->dns};
    for(int i=0;i<10;i++){char *end=strchr(v,'|');if(end)*end=0;snprintf(fields[i],sizes[i],"%s",v);v=end?end+1:v+strlen(v);}
}

static void append_output(InstallState *s,const char *line){size_t have=strlen(s->output),add=strlen(line);if(have+add+2>=sizeof s->output)return;memcpy(s->output+have,line,add);s->output[have+add]='\n';s->output[have+add+1]=0;}
static void parse_lines(InstallState *s,char *text,const char *action){
    if(!strcmp(action,"inventory")||!strcmp(action,"interfaces")){s->ndisks=s->nnics=0;s->disk=s->nic=-1;s->existing_disk=-1;}
    if(!strcmp(action,"validate")||!strcmp(action,"apply"))memset(s->field_error,0,sizeof s->field_error);
    if(!strcmp(action,"progress")){s->output[0]=0;s->title[0]=0;s->log[0]=0;}
    char *save=NULL;for(char *line=strtok_r(text,"\n",&save);line;line=strtok_r(NULL,"\n",&save)){char *eq=strchr(line,'=');if(!eq)continue;*eq++=0;
        if(!strcmp(line,"disk")){parse_disk(s,eq);continue;}if(!strcmp(line,"nic")){parse_nic(s,eq);continue;}if(!strcmp(line,"output")){append_output(s,eq);continue;}
        if(!strncmp(line,"errors.",7)){int i=error_slot(line+7);if(i>=0)snprintf(s->field_error[i],sizeof s->field_error[i],"%s",eq);continue;}
        if(!strcmp(line,"rerun"))s->rerun=truth(eq);else if(!strcmp(line,"role"))s->secondary=!strcmp(eq,"secondary");
        else if(!strcmp(line,"primary_url"))snprintf(s->primary_url,sizeof s->primary_url,"%s",eq);
        else if(!strcmp(line,"interface")){for(int i=0;i<s->nnics;i++)if(!strcmp(s->nics[i].name,eq))s->nic=i;}
        else if(!strcmp(line,"mode"))s->static_mode=!strcmp(eq,"static");else if(!strcmp(line,"hostname"))snprintf(s->hostname,sizeof s->hostname,"%s",eq);
        else if(!strcmp(line,"admin_user"))snprintf(s->admin_user,sizeof s->admin_user,"%s",eq);else if(!strcmp(line,"ok"))s->primary_ok=truth(eq);
        else if(!strcmp(line,"name"))snprintf(s->primary_name,sizeof s->primary_name,"%s",eq);else if(!strcmp(line,"version"))snprintf(s->primary_version,sizeof s->primary_version,"%s",eq);
        else if(!strcmp(line,"fingerprint"))snprintf(s->primary_fingerprint,sizeof s->primary_fingerprint,"%s",eq);else if(!strcmp(line,"error"))snprintf(s->error,sizeof s->error,"%s",eq);
        else if(!strcmp(line,"state"))snprintf(s->phase,sizeof s->phase,"%s",eq);else if(!strcmp(line,"pct")){s->progress_pct=atoi(eq);if(s->progress_pct<0)s->progress_pct=0;if(s->progress_pct>100)s->progress_pct=100;}
        else if(!strcmp(line,"title"))snprintf(s->title,sizeof s->title,"%s",eq);else if(!strcmp(line,"log"))snprintf(s->log,sizeof s->log,"%s",eq);
        else if(!strcmp(line,"job"))snprintf(s->job,sizeof s->job,"%s",eq);else if(!strcmp(line,"console_url"))snprintf(s->console_url,sizeof s->console_url,"%s",eq);
        else if(!strcmp(line,"user"))snprintf(s->user,sizeof s->user,"%s",eq);}
}

static int call(App *a,InstallBridge *b,const char *action,const char *json){char *out=malloc(120000);if(!out)return -1;int rc=run_bridge(b,action,json,out,120000);if(!rc)parse_lines(&a->install,out,action);else snprintf(a->install.error,sizeof a->install.error,"פעולת הגשר נכשלה: %s",action);free(out);return rc;}
static void seed_from_nic(InstallState *s){if(s->nic<0||s->nic>=s->nnics)return;InstallNic *n=&s->nics[s->nic];if(!s->address[0])snprintf(s->address,sizeof s->address,"%s",n->address[0]?n->address:n->current);if(!s->netmask[0])snprintf(s->netmask,sizeof s->netmask,"%s",n->netmask);if(!s->gateway[0])snprintf(s->gateway,sizeof s->gateway,"%s",n->gateway);if(!s->dns[0])snprintf(s->dns,sizeof s->dns,"%s",n->dns);}

void install_demo(App *a,int view){InstallState *s=&a->install;memset(s,0,sizeof *s);s->active=s->demo=1;s->view=view;s->disk=0;s->disk_ack=1;s->existing_disk=1;s->ndisks=3;
    s->disks[0]=(InstallDisk){"/dev/sda","VMware Virtual disk","scsi","gpt:2 partitions (ext4, vfat)",64424509440ULL,0,0};
    s->disks[1]=(InstallDisk){"/dev/nvme0n1","Samsung SSD 980","nvme","imagectl installation",1073741824000ULL,0,0};
    s->disks[2]=(InstallDisk){"/dev/sr0","USB install media","usb","iso9660 filesystem",4294967296ULL,1,1};
    s->nnics=3;s->nic=0;s->secondary=1;
    s->nics[0]=(InstallNic){"ens18","Intel I219-LM","52:54:00:aa:10:01","מחובר","10.44.10.37/24","DHCP · שער 10.44.10.254","10.44.10.37","255.255.255.0","10.44.10.254","10.44.10.2"};
    s->nics[1]=(InstallNic){"ens19","Intel I350","52:54:00:aa:12:01","מחובר","—","אין כתובת (אין DHCP ברשת הזו)","","","",""};s->nics[2]=(InstallNic){"ens20","Intel I350","52:54:00:aa:14:01","לא מודלק","—","—","","","",""};
    snprintf(s->primary_url,sizeof s->primary_url,"https://imagectl-ta.college.ac.il:8443");snprintf(s->hostname,sizeof s->hostname,"imagectl-haifa");snprintf(s->admin_user,sizeof s->admin_user,"admin");
    snprintf(s->password,sizeof s->password,"Imag3ctl!haifa");snprintf(s->confirm,sizeof s->confirm,"Imag3ctl!haifa");seed_from_nic(s);s->primary_ok=s->primary_checked=1;
    snprintf(s->primary_name,sizeof s->primary_name,"imagectl-ta");snprintf(s->primary_version,sizeof s->primary_version,"v0.52.1");snprintf(s->phase,sizeof s->phase,"packages");s->progress_pct=52;
    snprintf(s->title,sizeof s->title,"Installing ImageCtl packages");snprintf(s->log,sizeof s->log,"Setting up imagectl-server");if(view==INSTALL_FAILED)snprintf(s->error,sizeof s->error,"packages-failed: apt returned exit 100");
    snprintf(s->console_url,sizeof s->console_url,"https://10.44.10.37:8081");snprintf(s->user,sizeof s->user,"admin");snprintf(s->output,sizeof s->output,"[10:43:02] bootstrap complete\n[10:43:08] unpacking packages\n[10:43:11] apt returned exit 100\n");a->focus=HIT_INSTALL_ADMIN_USER;}

static int finish_install(App *a,InstallBridge *b){a->install.error[0]=0;if(call(a,b,"finish","{}")){a->install.view=INSTALL_FAILED;return -1;}a->install.view=INSTALL_DONE;return 0;}
static void route_engine_state(App *a,InstallBridge *b){InstallState *s=&a->install;int state=engine_state_index(s->phase);if(state<0){snprintf(s->error,sizeof s->error,"מצב מנוע לא מוכר: %s",s->phase);s->view=INSTALL_FAILED;}else if(!strcmp(s->phase,"ready"))s->view=INSTALL_DISK;else if(!strcmp(s->phase,"done"))finish_install(a,b);else if(!strcmp(s->phase,"failed"))s->view=INSTALL_FAILED;else s->view=INSTALL_PROGRESS;}

int install_start(App *a,InstallBridge *b,int demo){if(demo){install_demo(a,INSTALL_DISK);return 0;}memset(&a->install,0,sizeof a->install);a->install.active=1;a->install.view=INSTALL_DISK;a->install.disk=a->install.nic=a->install.existing_disk=-1;
    if(call(a,b,"inventory","{}"))return -1;seed_from_nic(&a->install);if(!a->install.hostname[0])snprintf(a->install.hostname,sizeof a->install.hostname,"imagectl-server");if(!a->install.admin_user[0])snprintf(a->install.admin_user,sizeof a->install.admin_user,"admin");
    if(!call(a,b,"progress","{}")&&strcmp(a->install.phase,"ready")){route_engine_state(a,b);b->next_poll=0;}return 0;}

int install_password_ready(const InstallState *s){const char *p=s->password;int letter=0,digit=0,special=0;for(;*p;p++){letter|=(*p>='A'&&*p<='Z')||(*p>='a'&&*p<='z');digit|=*p>='0'&&*p<='9';special|=strchr("!@#$%^&*(),.?\":{}|<>",*p)!=NULL;}return s->admin_user[0]&&strlen(s->password)>=8&&letter&&digit&&special&&!strcmp(s->password,s->confirm);}
static char *field(InstallState *s,int id,size_t *n){switch(id){case HIT_INSTALL_PRIMARY_URL:*n=sizeof s->primary_url;return s->primary_url;case HIT_INSTALL_ADDRESS:*n=sizeof s->address;return s->address;case HIT_INSTALL_NETMASK:*n=sizeof s->netmask;return s->netmask;case HIT_INSTALL_GATEWAY:*n=sizeof s->gateway;return s->gateway;case HIT_INSTALL_DNS:*n=sizeof s->dns;return s->dns;case HIT_INSTALL_HOSTNAME:*n=sizeof s->hostname;return s->hostname;case HIT_INSTALL_ADMIN_USER:*n=sizeof s->admin_user;return s->admin_user;case HIT_INSTALL_PASSWORD:*n=sizeof s->password;return s->password;case HIT_INSTALL_CONFIRM:*n=sizeof s->confirm;return s->confirm;case HIT_INSTALL_CURRENT_PASSWORD:*n=sizeof s->current_password;return s->current_password;default:*n=0;return NULL;}}
static int fields(InstallState *s,int *ids){int n=0;if(s->view==INSTALL_ROLE&&s->secondary)ids[n++]=HIT_INSTALL_PRIMARY_URL;else if(s->view==INSTALL_NETWORK&&s->static_mode){ids[n++]=HIT_INSTALL_ADDRESS;ids[n++]=HIT_INSTALL_NETMASK;ids[n++]=HIT_INSTALL_GATEWAY;ids[n++]=HIT_INSTALL_DNS;}else if(s->view==INSTALL_HOSTNAME)ids[n++]=HIT_INSTALL_HOSTNAME;else if(s->view==INSTALL_ADMIN){ids[n++]=HIT_INSTALL_ADMIN_USER;if(s->rerun)ids[n++]=HIT_INSTALL_CURRENT_PASSWORD;ids[n++]=HIT_INSTALL_PASSWORD;ids[n++]=HIT_INSTALL_CONFIRM;}return n;}
/* Focus follows the screen: after next/back the old field id would keep
 * receiving keystrokes (typed on the admin screen, landed in the hostname;
 * ESXi 21/09), and the first Tab only moved it here. */
static void refocus(App *a){int ids[8],n=fields(&a->install,ids);a->focus=n?ids[0]:0;}
static int any_errors(const InstallState *s){for(int i=0;i<14;i++)if(s->field_error[i][0])return 1;return 0;}
static int screen_errors(const InstallState *s,int view){int first=0,last=13;if(view==INSTALL_DISK)last=0;else if(view==INSTALL_ROLE){first=1;last=2;}else if(view==INSTALL_NETWORK){first=3;last=8;}else if(view==INSTALL_HOSTNAME){first=last=9;}else if(view==INSTALL_ADMIN)first=10;for(int i=first;i<=last;i++)if(s->field_error[i][0])return 1;return 0;}
static void back(App *a){InstallState *s=&a->install;if(s->view==INSTALL_FAILED){s->view=INSTALL_DISK;refocus(a);return;}if(s->view>INSTALL_DISK&&s->view<=INSTALL_SUMMARY){s->view--;refocus(a);}}
static void check_primary_call(App *a,InstallBridge *b){char json[512],*p=json;size_t n=sizeof json;*p++='{';n--;json_pair(&p,&n,"primary_url",a->install.primary_url,0);if(n>1){*p++='}';*p=0;}a->install.error[0]=0;a->install.primary_checked=1;if(call(a,b,"check-primary",json)){a->install.primary_ok=0;snprintf(a->install.primary_error,sizeof a->install.primary_error,"פעולת הגשר נכשלה: check-primary");}else snprintf(a->install.primary_error,sizeof a->install.primary_error,"%s",a->install.error);a->install.error[0]=0;}
static void next(App *a,InstallBridge *b){InstallState *s=&a->install;if(s->view==INSTALL_DISK&&(s->disk<0||!s->disk_ack))return;if(s->view==INSTALL_SUMMARY){char json[4096];config_json(a,json,sizeof json,0);s->error[0]=0;if(!call(a,b,"apply",json)&&!any_errors(s)&&!s->error[0]){snprintf(s->phase,sizeof s->phase,"partitioning");s->progress_pct=0;s->view=INSTALL_PROGRESS;b->next_poll=0;}return;}char json[4096];int view=s->view,step=view==INSTALL_ADMIN?0:(view==INSTALL_DISK?1:view-INSTALL_DISK);config_json(a,json,sizeof json,step);s->error[0]=0;if(!call(a,b,"validate",json)&&!screen_errors(s,view)){s->view++;refocus(a);}}
static void reboot_or_report(App *a,InstallBridge *b){a->install.error[0]=0;call(a,b,"eject","{}");if(!call(a,b,"reboot","{}"))snprintf(a->install.error,sizeof a->install.error,"פקודת reboot חזרה בלי להפעיל מחדש");}
static void boot_local(App *a,InstallBridge *b){a->install.error[0]=0;if(!call(a,b,"boot-local","{}"))snprintf(a->install.error,sizeof a->install.error,"פקודת boot-local חזרה בלי להפעיל מחדש");}
static void choose(App *a,InstallBridge *b,int id){InstallState *s=&a->install;if(id==HIT_INSTALL_BACK){back(a);return;}if(id==HIT_INSTALL_NEXT){if(s->view==INSTALL_DONE)reboot_or_report(a,b);else if(s->view!=INSTALL_ADMIN||install_password_ready(s))next(a,b);return;}if(id==HIT_INSTALL_PRIMARY)s->secondary=0;else if(id==HIT_INSTALL_SECONDARY){s->secondary=1;refocus(a);}else if(id==HIT_INSTALL_CHECK_PRIMARY)check_primary_call(a,b);else if(id==HIT_INSTALL_DHCP)s->static_mode=0;else if(id==HIT_INSTALL_STATIC){s->static_mode=1;seed_from_nic(s);refocus(a);}else if(id==HIT_INSTALL_RETRY){s->view=INSTALL_DISK;s->disk_ack=0;refocus(a);}else if(id==HIT_INSTALL_DISK_ACK&&s->disk>=0)s->disk_ack=!s->disk_ack;else if(id==HIT_INSTALL_BOOT_LOCAL)boot_local(a,b);else if(id==HIT_INSTALL_SHOW_PASSWORD)s->show_password=!s->show_password;else if(id==HIT_INSTALL_SHOW_CONFIRM)s->show_confirm=!s->show_confirm;else if(id==HIT_INSTALL_TECHNICAL)s->technical_open=!s->technical_open;else if(id==HIT_INSTALL_OUTPUT_UP){s->output_scroll-=5;if(s->output_scroll<0)s->output_scroll=0;}else if(id==HIT_INSTALL_OUTPUT_DOWN)s->output_scroll+=5;else if(id>=HIT_INSTALL_DISK_BASE&&id<HIT_INSTALL_DISK_BASE+s->ndisks){int d=id-HIT_INSTALL_DISK_BASE;if(!s->disks[d].iso){s->disk=d;s->disk_ack=0;}}else if(id>=HIT_INSTALL_NIC_BASE&&id<HIT_INSTALL_NIC_BASE+s->nnics){s->nic=id-HIT_INSTALL_NIC_BASE;seed_from_nic(s);}else if(id>=HIT_INSTALL_SUMMARY_BASE&&id<HIT_INSTALL_SUMMARY_BASE+5){s->view=INSTALL_DISK+id-HIT_INSTALL_SUMMARY_BASE;refocus(a);}else{size_t n;if(field(s,id,&n))a->focus=id;}}

int install_handle(App *a,InstallBridge *b,const Event *e){InstallState *s=&a->install;if(e->type==UIEV_MOVE){a->ptr_x=e->x;a->ptr_y=e->y;a->ptr_visible=!e->is_touch;return 0;}if(e->type==UIEV_CLICK){a->ptr_x=e->x;a->ptr_y=e->y;choose(a,b,app_hit(a,e->x,e->y));return 0;}if(e->type==UIEV_SCROLL&&(s->view==INSTALL_FAILED||(s->view==INSTALL_PROGRESS&&s->technical_open))){s->output_scroll-=e->delta*3;if(s->output_scroll<0)s->output_scroll=0;return 0;}/* keyboard-only servers (a rack has a keyboard, rarely a mouse): on the disk
 * screen Up/Down pick the disk, Space ticks "I understand"; Space also
 * toggles the technical details on the progress screen. */
if(s->view==INSTALL_DISK&&e->type==UIEV_CHAR&&e->ch==' '){if(s->disk>=0)s->disk_ack=!s->disk_ack;return 0;}
if(s->view==INSTALL_PROGRESS&&e->type==UIEV_CHAR&&e->ch==' '){s->technical_open=!s->technical_open;return 0;}
if(s->view==INSTALL_DISK&&e->type==UIEV_KEY&&(e->key==KEYSYM_UP||e->key==KEYSYM_DOWN)){int d=s->disk,step=e->key==KEYSYM_DOWN?1:-1;for(int i=0;i<s->ndisks;i++){d+=step;if(d<0)d=s->ndisks-1;if(d>=s->ndisks)d=0;if(!s->disks[d].iso){s->disk=d;s->disk_ack=0;break;}}return 0;}
if(e->type==UIEV_CHAR){size_t n;char *v=field(s,a->focus,&n);if(v&&strlen(v)+1<n){size_t z=strlen(v);v[z]=(char)e->ch;v[z+1]=0;}return 0;}if(e->type!=UIEV_KEY)return 0;
    if(e->key==KEYSYM_ESC){back(a);return 0;}if(e->key==KEYSYM_UP&&(s->view==INSTALL_FAILED||s->technical_open)){choose(a,b,HIT_INSTALL_OUTPUT_UP);return 0;}if(e->key==KEYSYM_DOWN&&(s->view==INSTALL_FAILED||s->technical_open)){choose(a,b,HIT_INSTALL_OUTPUT_DOWN);return 0;}if(e->key==KEYSYM_BACKSPACE){size_t n;char *v=field(s,a->focus,&n);if(v){size_t z=strlen(v);if(z)v[--z]=0;}return 0;}if(e->key==KEYSYM_ENTER){if(s->view!=INSTALL_PROGRESS)choose(a,b,s->view==INSTALL_FAILED?HIT_INSTALL_RETRY:HIT_INSTALL_NEXT);return 0;}if(e->key==KEYSYM_TAB||e->key==KEYSYM_BACKTAB){int ids[8],n=fields(s,ids);if(n){int at=-1;for(int i=0;i<n;i++)if(ids[i]==a->focus)at=i;if(at<0)at=e->key==KEYSYM_TAB?0:n-1;else at=(at+(e->key==KEYSYM_TAB?1:n-1))%n;a->focus=ids[at];}}return 0;}
int install_polling(const App *a){return a->install.view==INSTALL_PROGRESS;}
int install_tick(App *a,InstallBridge *b){InstallState *s=&a->install;if(s->view!=INSTALL_PROGRESS||monotonic_s()<b->next_poll)return 0;b->next_poll=monotonic_s()+1.0;s->error[0]=0;if(call(a,b,"progress","{}")){snprintf(s->phase,sizeof s->phase,"failed");s->view=INSTALL_FAILED;return 1;}route_engine_state(a,b);return 1;}
