#ifndef AI_TERMINAL_H
#define AI_TERMINAL_H

#include <stddef.h>

extern int raw_mode_active;
extern volatile int g_esc_requested;
extern volatile int g_btw_available;
extern char g_btw_message[];

void enable_raw_mode(void);
void disable_raw_mode(void);
char* read_line_interactive(const char *prompt);
void poll_agent_stdin(void);

#include "ai_reap.h"   /* orphan reaper, implemented in ai_terminal.c */

#endif /* AI_TERMINAL_H */
