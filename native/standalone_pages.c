/* SPDX-License-Identifier: GPL-3.0-or-later */
#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L
#include "standalone_pages.h"
#include <arpa/inet.h>
#include <ctype.h>
#include <ifaddrs.h>
#include <math.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

struct glyph { unsigned char character, width, height, rows[7]; };
#include "font.h"

const char *const pc_builtin_pages[] = {"clock", "prs", "deploy", "garden", "health", "uptime", "identity", "date", "dayprogress", "weekprogress", "yearprogress", "worldclock", "text", "countdown", "system", "network", "binaryclock"};
const size_t pc_builtin_page_count = sizeof(pc_builtin_pages) / sizeof(pc_builtin_pages[0]);
enum { WHITE=0xe8e7dc, ACCENT=0xb7bdc2, SECONDARY=0x89a1a2, GREEN=0x48d370, AMBER=0xebb85d, RED=0xf66969, DIM=0x414648 };

static void px(unsigned char *out, int x, int y, unsigned int color) {
    if (x < 0 || x >= 52 || y < 0 || y >= 16) return;
    int i = (y*52+x)*3;
    out[i] = color >> 16; out[i+1] = color >> 8; out[i+2] = color;
}
static const struct glyph *find_glyph(unsigned char c, int large) {
    const struct glyph *set = large ? big : font;
    size_t count = large ? sizeof(big)/sizeof(big[0]) : sizeof(font)/sizeof(font[0]);
    c = (unsigned char)toupper(c);
    for (size_t i=0; i<count; i++) if (set[i].character == c) return &set[i];
    return NULL;
}
static int width(const char *text, int large) {
    int w=0;
    for (const unsigned char *p=(const unsigned char *)text; *p; p++) {
        const struct glyph *g=find_glyph(*p,large);
        if (g) w += g->width+1;
    }
    return w ? w-1 : 0;
}
static void txt(unsigned char *out, const char *text, int x, int y, int large, unsigned int color) {
    for (const unsigned char *p=(const unsigned char *)text; *p; p++) {
        const struct glyph *g=find_glyph(*p,large);
        if (!g) continue;
        for (int row=0;row<g->height;row++) for (int col=0;col<g->width;col++)
            if (g->rows[row] & (1 << (g->width-col-1))) px(out,x+col,y+row,color);
        x += g->width+1;
    }
}
static void label(unsigned char *out, const char *value, int y, unsigned int color) { txt(out,value,1,y,0,color); }
static void centered(unsigned char *out, const char *value, int y, int large, unsigned int color) { txt(out,value,(52-width(value,large))/2,y,large,color); }
static void rect(unsigned char *out, int x, int y, int w, int h, unsigned int color) { for(int dy=0;dy<h;dy++) for(int dx=0;dx<w;dx++) px(out,x+dx,y+dy,color); }
static void unknown(unsigned char *out, const char *title, const char *reason) { label(out,title,0,ACCENT); label(out,reason,9,AMBER); }
static int stale(time_t updated, time_t now, int ttl) { return !updated || now-updated>ttl; }
static void age(char *out, size_t size, time_t value, time_t now) {
    if(!value) {snprintf(out,size,"--");return;}
    long n=(long)difftime(now,value); if(n<0)n=0;
    snprintf(out,size,"%ld%s",n<60?n:n<3600?n/60:n<86400?n/3600:n/86400,n<60?"S":n<3600?"M":n<86400?"H":"D");
}
static void scroll(unsigned char *out, const char *value, int y, double phase, unsigned int color) {
    int distance=width(value,0)-50;
    int offset=distance>0 ? (int)fmax(0,(phase-1)*12) : 0;
    if(offset>distance && distance>0) offset=distance;
    txt(out,value,1-offset,y,0,color);
}
static int load_frame(const char *path, unsigned char *out) {
    if(!path || !path[0]) return -1;
    FILE *f=fopen(path,"rb"); if(!f)return -1;
    size_t n=fread(out,1,PC_FRAME_BYTES,f); int extra=fgetc(f); fclose(f);
    return n==PC_FRAME_BYTES && extra==EOF ? 0 : -1;
}
static void mark(unsigned char *out, const pc_config *config, int x, int y, int size) {
    unsigned char seed[PC_FRAME_BYTES];
    if(load_frame(config->brand_frame,seed)==0) {
        for(int dy=0;dy<size;dy++) for(int dx=0;dx<size;dx++) {
            int sx=dx*14/size, sy=dy*14/size;
            int i=(sy*52+sx)*3;
            px(out,x+dx,y+dy,(seed[i]<<16)|(seed[i+1]<<8)|seed[i+2]);
        }
    } else {
        int r=size/2;
        for(int i=0;i<size;i++) {int dy=abs(i-r);px(out,x+i,y+dy,ACCENT);px(out,x+i,y+size-1-dy,ACCENT);}
    }
}
static void progress(unsigned char *out, const char *title, double fraction) {
    if (fraction < 0) {
        fraction = 0;
    }
    if (fraction > 1) {
        fraction = 1;
    }
    char s[64];snprintf(s,sizeof(s),"%s %.0f%%",title,fraction*100);
    label(out,s,0,ACCENT);rect(out,1,9,50,6,DIM);rect(out,1,9,(int)(50*fraction+0.5),6,SECONDARY);
}
static void render_clock(unsigned char *out,const pc_config *config,const struct tm *local) {
    char s[32];mark(out,config,0,1,13);strftime(s,sizeof(s),"%H:%M",local);txt(out,s,17,1,1,WHITE);
    strftime(s,sizeof(s),"%a %m/%d",local);txt(out,s,17,10,0,SECONDARY);px(out,local->tm_sec%13,15,ACCENT);
}
static const char *pr_display_title(const char *title) {
    const char *p=title;
    for (;;) {
        while(isspace((unsigned char)*p))p++;
        if((unsigned char)*p>=128){while((unsigned char)*p>=128)p++;continue;}
        if(*p==':'){
            const char *end=p+1;
            while((*end>='a'&&*end<='z')||*end=='_')end++;
            if(end>p+1&&*end==':'){p=end+1;continue;}
        }
        break;
    }
    static const char *const verbs[]={"feat","fix","ref","refactor","docs","chore","nit","test","perf","style"};
    for(size_t i=0;i<sizeof(verbs)/sizeof(verbs[0]);i++){
        size_t n=strlen(verbs[i]);if(strncasecmp(p,verbs[i],n))continue;
        const char *end=p+n;
        if(*end=='['||*end=='('){const char *close=strchr(end+1,*end=='['?']':')');if(!close)continue;end=close+1;}
        if(*end==':'){p=end+1;while(isspace((unsigned char)*p))p++;break;}
    }
    return p;
}
static void render_prs(unsigned char *out,const pc_state *state,double phase,time_t now) {
    const pc_pr_list *list=&state->prs;
    if(!list->updated_at || (list->error[0] && !list->count)) {unknown(out,"MY PRS","UNAVAILABLE");return;}
    int old=stale(list->updated_at,now,900)||list->error[0];
    if(!list->count) {if(old)unknown(out,"MY PRS","STALE");else{label(out,"MY PRS",0,ACCENT);label(out,"ALL CLEAR",9,GREEN);}return;}
    int index=(int)(phase/7)%list->count; if(index<0)index=0;
    const pc_pr *p=&list->items[index];unsigned int color=ACCENT;const char *status="REVIEW";
    if(p->draft){status="DRAFT";color=DIM;}else if(p->status==PC_FAILED){status="CI FAIL";color=RED;}else if(p->review==PC_REVIEW_CHANGES){status="CHANGES";color=AMBER;}else if(p->status==PC_BUILDING){status="CI RUN";color=AMBER;}else if(p->review==PC_REVIEW_APPROVED){status="APPROVED";color=GREEN;}
    char s[64];double local=fmod(phase,7);
    if(local<3){snprintf(s,sizeof(s),"PR %d/%d",index+1,list->count);label(out,old?"STALE":s,0,old?AMBER:ACCENT);snprintf(s,sizeof(s),"#%d",p->number);label(out,s,9,WHITE);rect(out,48,10,3,3,color);}
    else{label(out,old?"STALE":status,0,old?AMBER:color);scroll(out,pr_display_title(p->title),9,local-3,WHITE);}
}
static void render_deploy(unsigned char *out,const pc_state *state,time_t now) {
    const pc_deploy *d=&state->deploy;
    if(!d->updated_at || d->state==PC_UNKNOWN){unknown(out,"WEB DEPLOY","UNAVAILABLE");return;}
    int old=stale(d->updated_at,now,900)||d->error[0];const char *name="UNKNOWN";unsigned int color=AMBER;
    if(d->state==PC_OK){name=d->live&&d->serving_state==PC_OK?"LIVE":"READY";color=GREEN;}else if(d->state==PC_BUILDING)name="BUILD";else if(d->state==PC_FAILED){name="FAILED";color=RED;}else if(d->state==PC_CANCELLED)name="CANCEL";
    label(out,old?"STALE":"WEB DEPLOY",0,old?AMBER:ACCENT);char a[32],s[64];age(a,sizeof(a),d->deployed_at?d->deployed_at:d->created_at,now);snprintf(s,sizeof(s),"%s %s",name,a);label(out,s,9,color);
}
static void render_garden(unsigned char *out,const pc_state *state,const struct tm *local,time_t now) {
    const pc_garden *g=&state->garden;
    if(!g->count){unknown(out,"GARDEN","NO DATA");return;}
    label(out,"GH",1,ACCENT);txt(out,stale(g->updated_at,now,900)||g->error[0]?"!":"+",3,9,0,stale(g->updated_at,now,900)||g->error[0]?AMBER:GREEN);
    static const unsigned int palette[]={0x08180d,0x115028,0x1c8539,0x31b54f,0x5de77c};
    char today[16];strftime(today,sizeof(today),"%Y-%m-%d",local);
    struct tm day=*local;day.tm_hour=12;day.tm_min=day.tm_sec=0;day.tm_mday-=day.tm_wday+18*7;mktime(&day);
    for(int i=0;i<133;i++){char date[16];strftime(date,sizeof(date),"%Y-%m-%d",&day);if(strcmp(date,today)>0)break;int count=-1;for(int j=0;j<g->count;j++)if(!strcmp(date,g->days[j].date)){count=g->days[j].count;break;}unsigned int color=count<0?DIM:palette[count==0?0:count<3?1:count<7?2:count<15?3:4];px(out,13+i/7*2,1+i%7*2,color);day.tm_mday++;mktime(&day);}
}
static void render_health(unsigned char *out,const pc_state *state,time_t now) {
    const pc_health *h=&state->health;
    if(stale(h->updated_at,now,180)){unknown(out,"API HEALTH",h->updated_at?"STALE":"NO SIGNAL");return;}
    if(h->state==PC_UNKNOWN){unknown(out,"API HEALTH","NO SIGNAL");return;}
    label(out,h->state==PC_OK?"API OK":"API DOWN",0,h->state==PC_OK?GREEN:RED);char s[32];snprintf(s,sizeof(s),"%dMS",h->latency_ms);label(out,s,9,WHITE);
    int start=h->history_count>20?h->history_count-20:0,ceiling=100;
    for(int i=start;i<h->history_count;i++)if(h->history[i]>ceiling)ceiling=h->history[i];
    for(int i=start;i<h->history_count;i++){int v=h->history[i],x=31+i-start;px(out,x,v<0?14:14-v*5/ceiling,v<0?AMBER:SECONDARY);}
}
static void render_uptime(unsigned char *out,const pc_state *state,double phase,time_t now) {
    if(state->health.state==PC_UNKNOWN||stale(state->health.updated_at,now,180)){unknown(out,"API UPTIME","NO SIGNAL");return;}
    if(!state->uptime.samples||state->uptime.availability_pct<0){unknown(out,"API UPTIME","COLLECTING");return;}
    if(stale(state->uptime.updated_at,now,180)){unknown(out,"API UPTIME","STALE");return;}
    char s[64];if(fmod(phase,8)<5){double p=state->uptime.availability_pct;label(out,"API UPTIME",0,ACCENT);snprintf(s,sizeof(s),"%.2f%%",p);label(out,s,9,p>=99?GREEN:p>=95?AMBER:RED);}else{char a[32];age(a,sizeof(a),state->uptime.window_start,now);label(out,"OBSERVED API",0,ACCENT);snprintf(s,sizeof(s),"SINCE %s",a);label(out,s,9,SECONDARY);}
}
static void render_system(unsigned char *out,double phase) {
    FILE *f;char s[64];
    if(fmod(phase,8)>=4 && (f=fopen("/proc/meminfo","r"))){char line[256];unsigned long available=0,free_kb=0;while(fgets(line,sizeof(line),f)){sscanf(line,"MemAvailable: %lu kB",&available);sscanf(line,"MemFree: %lu kB",&free_kb);}fclose(f);label(out,"FREE RAM",0,ACCENT);snprintf(s,sizeof(s),"%luMB",(available?available:free_kb)/1024);label(out,s,9,WHITE);return;}
    f=fopen("/proc/uptime","r");double seconds=0;if(!f){unknown(out,"SYSTEM","NO STATS");return;}int n=fscanf(f,"%lf",&seconds);fclose(f);if(n!=1){unknown(out,"SYSTEM","NO STATS");return;}long v=(long)seconds;label(out,"DEVICE UP",0,ACCENT);snprintf(s,sizeof(s),"%ldD %ldH %ldM",v/86400,v%86400/3600,v%3600/60);label(out,s,9,WHITE);
}
static void render_network(unsigned char *out,double phase) {
    struct ifaddrs *list=NULL;char address[INET_ADDRSTRLEN]={0};
    if(getifaddrs(&list)==0){for(struct ifaddrs *i=list;i;i=i->ifa_next){if(!i->ifa_addr||i->ifa_addr->sa_family!=AF_INET)continue;struct sockaddr_in *s=(struct sockaddr_in *)i->ifa_addr;if((ntohl(s->sin_addr.s_addr)>>24)==127)continue;inet_ntop(AF_INET,&s->sin_addr,address,sizeof(address));if(!strcmp(i->ifa_name,"wlan0"))break;}freeifaddrs(list);}
    label(out,"LOCAL IP",0,ACCENT);if(address[0])scroll(out,address,9,fmod(phase,7),WHITE);else label(out,"NO ADDRESS",9,AMBER);
}

int pc_render_page(const char *page,const pc_state *state,const pc_config *config,double phase,time_t now,unsigned char out[PC_FRAME_BYTES]) {
    memset(out,0,PC_FRAME_BYTES);struct tm local;localtime_r(&now,&local);char s[128];if(phase<0)phase=0;
    if(!strcmp(page,"clock"))render_clock(out,config,&local);
    else if(!strcmp(page,"prs"))render_prs(out,state,phase,now);
    else if(!strcmp(page,"deploy"))render_deploy(out,state,now);
    else if(!strcmp(page,"garden"))render_garden(out,state,&local,now);
    else if(!strcmp(page,"health"))render_health(out,state,now);
    else if(!strcmp(page,"uptime"))render_uptime(out,state,phase,now);
    else if(!strcmp(page,"identity")){if(fmod(phase,10)<3){if(load_frame(config->identity_frame,out)!=0)mark(out,config,18,0,16);}else{const char *name=config->identity_label[0]?config->identity_label:"PIXELCTL";if(width(name,0)>50)scroll(out,name,5,fmod(phase,10)-3,WHITE);else centered(out,name,5,0,WHITE);}int x=(int)(phase*5)%52;px(out,x,15,ACCENT);}
    else if(!strcmp(page,"date")){strftime(s,sizeof(s),"%A",&local);centered(out,s,0,0,ACCENT);strftime(s,sizeof(s),"%m/%d/%y",&local);centered(out,s,9,0,WHITE);}
    else if(!strcmp(page,"dayprogress"))progress(out,"DAY",(local.tm_hour*3600+local.tm_min*60+local.tm_sec)/86400.0);
    else if(!strcmp(page,"weekprogress"))progress(out,"WEEK",(((local.tm_wday+6)%7)+(local.tm_hour*3600+local.tm_min*60+local.tm_sec)/86400.0)/7);
    else if(!strcmp(page,"yearprogress")){snprintf(s,sizeof(s),"%d",local.tm_year+1900);int year=local.tm_year+1900;int days=365+(year%4==0&&(year%100!=0||year%400==0));progress(out,s,(local.tm_yday+(local.tm_hour*3600+local.tm_min*60+local.tm_sec)/86400.0)/days);}
    else if(!strcmp(page,"worldclock")){time_t shifted=now+(time_t)config->world_offset_minutes*60;struct tm world;gmtime_r(&shifted,&world);if(config->world_label[0])snprintf(s,sizeof(s),"%.13s",config->world_label);else if(!config->world_offset_minutes)strcpy(s,"UTC");else snprintf(s,sizeof(s),"UTC%c%02d:%02d",config->world_offset_minutes<0?'-':'+',abs(config->world_offset_minutes)/60,abs(config->world_offset_minutes)%60);centered(out,s,0,0,ACCENT);strftime(s,sizeof(s),"%H:%M",&world);centered(out,s,9,1,WHITE);}
    else if(!strcmp(page,"text")){label(out,config->text_label[0]?config->text_label:"NOTE",0,ACCENT);const char *value=config->text[0]?config->text:"HELLO WORLD";double duration=fmax(1,(width(value,0)-50)/12.0+3);scroll(out,value,9,fmod(phase,duration),WHITE);}
    else if(!strcmp(page,"countdown")){label(out,config->countdown_label[0]?config->countdown_label:"COUNTDOWN",0,ACCENT);long n=(long)difftime(config->countdown_target,now);if(!config->countdown_target)label(out,"NO TARGET",9,AMBER);else if(n<=0)centered(out,"DONE",9,0,GREEN);else{if(n>=86400)snprintf(s,sizeof(s),"%ldD %ldH",n/86400,n%86400/3600);else if(n>=3600)snprintf(s,sizeof(s),"%ldH %ldM",n/3600,n%3600/60);else snprintf(s,sizeof(s),"%ldM %ldS",n/60,n%60);centered(out,s,9,0,WHITE);}}
    else if(!strcmp(page,"system"))render_system(out,phase);
    else if(!strcmp(page,"network"))render_network(out,phase);
    else if(!strcmp(page,"binaryclock")){centered(out,"H  M  S",0,0,ACCENT);int values[]={local.tm_hour,local.tm_min,local.tm_sec};for(int col=0;col<3;col++)for(int bit=0;bit<6;bit++)rect(out,3+col*17+bit*2,10,1,4,values[col]&(1<<(5-bit))?SECONDARY:DIM);}
    else return -1;
    if(config->foreground||config->accent||config->secondary){
        for(int i=0;i<PC_FRAME_BYTES;i+=3){unsigned int color=(out[i]<<16)|(out[i+1]<<8)|out[i+2];unsigned int target=color==WHITE?config->foreground:color==ACCENT?config->accent:color==SECONDARY?config->secondary:color;out[i]=target>>16;out[i+1]=target>>8;out[i+2]=target;}
    }
    return 0;
}
