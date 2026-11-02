/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef PIXELCTL_STANDALONE_PAGES_H
#define PIXELCTL_STANDALONE_PAGES_H
#include "standalone_providers.h"
#define PC_FRAME_BYTES (52 * 16 * 3)
/* Returns zero for a known built-in page, -1 for an unknown ID. The process TZ
 * selects the local clock; worldclock uses its explicitly fixed UTC offset. */
int pc_render_page(const char *page, const pc_state *state, const pc_config *config,
                   double phase, time_t now, unsigned char out[PC_FRAME_BYTES]);
extern const char *const pc_builtin_pages[];
extern const size_t pc_builtin_page_count;
#endif
