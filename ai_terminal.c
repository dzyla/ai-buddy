#include "ai_terminal.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <termios.h>
#include <sys/select.h>
#include <errno.h>
#include <signal.h>
#include <sys/wait.h>

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

/* ── orphan reaping (see ai_terminal.h) ── */
static pid_t g_reap_tracked = -1;
static int   g_reap_tracked_done = 0, g_reap_tracked_status = 0;

static void on_sigchld(int sig) { (void)sig; /* wake-up only: reaping happens at the next tick */ }

void reap_install_sigchld(void) {
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = on_sigchld;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = SA_RESTART | SA_NOCLDSTOP;
    sigaction(SIGCHLD, &sa, NULL);
}

void reap_track(pid_t pid) {
    g_reap_tracked = pid;
    g_reap_tracked_done = 0;
    g_reap_tracked_status = 0;
}

void reap_children(void) {
    int saved = errno, st;
    pid_t p;
    while ((p = waitpid(-1, &st, WNOHANG)) > 0) {
        if (p == g_reap_tracked) {
            g_reap_tracked_done = 1;
            g_reap_tracked_status = st;
        }
    }
    errno = saved;
}

int reap_tracked(int *status) {
    if (!g_reap_tracked_done) return 0;
    if (status) *status = g_reap_tracked_status;
    return 1;
}
