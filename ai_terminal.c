#include "ai_terminal.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <termios.h>
#include <sys/select.h>
#include <errno.h>

int raw_mode_active = 0;
volatile int g_esc_requested = 0;
volatile int g_btw_available = 0;
char g_btw_message[4096] = {0};
static struct termios orig_termios;

void disable_raw_mode(void) {
    if (raw_mode_active) {
        tcsetattr(STDIN_FILENO, TCSAFLUSH, &orig_termios);
        raw_mode_active = 0;
    }
}

void enable_raw_mode(void) {
    if (!isatty(STDIN_FILENO)) return;
    if (tcgetattr(STDIN_FILENO, &orig_termios) < 0) return;
    struct termios raw = orig_termios;
    raw.c_lflag &= ~(ECHO | ICANON);
    raw.c_cc[VMIN] = 1;
    raw.c_cc[VTIME] = 0;
    if (tcsetattr(STDIN_FILENO, TCSAFLUSH, &raw) < 0) return;
    raw_mode_active = 1;
    atexit(disable_raw_mode);
}

void poll_agent_stdin(void) {
    if (!isatty(STDIN_FILENO)) return;
    fd_set fds;
    FD_ZERO(&fds);
    FD_SET(STDIN_FILENO, &fds);
    struct timeval tv = {0, 0};
    if (select(STDIN_FILENO + 1, &fds, NULL, NULL, &tv) > 0) {
        char ch;
        if (read(STDIN_FILENO, &ch, 1) == 1) {
            if (ch == 27) { /* ESC */
                g_esc_requested = 1;
            }
        }
    }
}
