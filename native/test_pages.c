/* SPDX-License-Identifier: GPL-3.0-or-later */
#define _POSIX_C_SOURCE 200809L
#include "standalone_pages.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int contains_color(const unsigned char *frame,unsigned int color) {
    for(int i=0;i<PC_FRAME_BYTES;i+=3)
        if(((unsigned int)frame[i]<<16 | (unsigned int)frame[i+1]<<8 | frame[i+2])==color)return 1;
    return 0;
}

int main(void) {
    pc_state *state=calloc(1,sizeof(*state));assert(state);
    pc_config config={0};time_t now=1793484030;
    unsigned char actual[PC_FRAME_BYTES],expected[PC_FRAME_BYTES];
    state->prs.updated_at=now;state->prs.count=1;state->prs.items[0].number=123;
    const char *const titles[]={":bug: fix[ui]: Align the widget", "\xF0\x9F\x90\x9B fix(ui): Align the widget", "fix: Align the widget", "refactor[ui]: Align the widget"};
    strcpy(state->prs.items[0].title,"Align the widget");
    assert(pc_render_page("prs",state,&config,4,now,expected)==0);
    for(size_t i=0;i<sizeof(titles)/sizeof(titles[0]);i++){
        snprintf(state->prs.items[0].title,sizeof(state->prs.items[0].title),"%s",titles[i]);
        assert(pc_render_page("prs",state,&config,4,now,actual)==0);
        assert(!memcmp(actual,expected,PC_FRAME_BYTES));
    }
    /* Ordinary prose starting with a similar word must remain intact. */
    strcpy(state->prs.items[0].title,"Fixing the widget");
    assert(pc_render_page("prs",state,&config,4,now,actual)==0);
    assert(memcmp(actual,expected,PC_FRAME_BYTES));
    state->health.updated_at=now;state->health.state=PC_FAILED;state->health.status_code=503;
    state->health.latency_ms=45;strcpy(state->health.error,"HTTP 503");
    assert(pc_render_page("health",state,&config,0,now,actual)==0);
    assert(contains_color(actual,0xf66969));
    state->health.state=PC_UNKNOWN;state->uptime.samples=10;state->uptime.availability_pct=100;state->uptime.updated_at=now;
    assert(pc_render_page("uptime",state,&config,0,now,actual)==0);
    assert(!contains_color(actual,0x48d370));
    struct {unsigned char before[16],frame[PC_FRAME_BYTES],after[16];} guarded;
    for(size_t page=0;page<pc_builtin_page_count;page++)for(int phase=0;phase<10;phase++){
        memset(&guarded,0xa5,sizeof(guarded));
        assert(pc_render_page(pc_builtin_pages[page],state,&config,phase,now,guarded.frame)==0);
        for(int i=0;i<16;i++){assert(guarded.before[i]==0xa5);assert(guarded.after[i]==0xa5);}
    }
    setenv("TZ","UTC0",1);tzset();strcpy(config.world_label,"OFFSET");
    config.world_offset_minutes=330;assert(pc_render_page("worldclock",state,&config,0,now,actual)==0);
    config.world_offset_minutes=0;assert(pc_render_page("worldclock",state,&config,0,now+19800,expected)==0);
    assert(!memcmp(actual,expected,PC_FRAME_BYTES));
    setenv("TZ","PST8PDT,M3.2.0/2,M11.1.0/2",1);tzset();
    assert(pc_render_page("clock",state,&config,0,1793521800,actual)==0);
    assert(pc_render_page("clock",state,&config,0,1793525400,expected)==0);
    assert(!memcmp(actual,expected,PC_FRAME_BYTES));
    free(state);puts("native pages: title cleanup, 17-page bounds, health, world offset, and DST passed");
    return 0;
}
