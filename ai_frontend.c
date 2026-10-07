/* ai_frontend.c — the C side of the default (Python) engine (see ai_frontend.h, agent/protocol.py). */
#include "ai_frontend.h"
#include "cJSON.h"
#include "ai_reap.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <termios.h>
#include <unistd.h>

static const frontend_ui_t *U;
static int   g_fd = -1;                 /* our socket end */
static pid_t g_engine = -1;
static int   g_quiet = 0, g_active = 0;
static int   g_hdr = 0, g_ftr = 0;      /* thinking header/footer printed for the current request */
static int   g_read_count = 0;
static int   g_turn = 0, g_tools = 0;  /* latest turn number / session tool count from the engine */
static char *g_last = NULL;             /* last answer (markdown) for :copy */
static struct termios g_saved;
static int   g_raw = 0;
static char  g_btw[4096];
static int   g_btw_len = 0;

/* ── terminal raw mode while a task runs (ECHO/ICANON off, signals kept) ── */
static void raw_on(void) {
    if (g_raw || !isatty(STDIN_FILENO)) return;
    if (tcgetattr(STDIN_FILENO, &g_saved) < 0) return;
    struct termios r = g_saved;
    r.c_lflag &= ~(ECHO | ICANON);
    r.c_cc[VMIN] = 0;
    r.c_cc[VTIME] = 0;
    if (tcsetattr(STDIN_FILENO, TCSANOW, &r) == 0) g_raw = 1;
}

static void raw_off(void) {
    if (!g_raw) return;
    tcsetattr(STDIN_FILENO, TCSANOW, &g_saved);
    g_raw = 0;
}

static void fe_signal(int sig) {
    raw_off();
    if (g_engine > 0 && !reap_tracked(NULL)) kill(g_engine, SIGTERM);  /* not a reused pid */
    static const char msg[] = "\n[ai] Signal received \xe2\x80\x94 restoring terminal and exiting.\n";
    ssize_t w = write(STDERR_FILENO, msg, sizeof(msg) - 1);
    (void)w;
    _exit(128 + sig);
}

/* ── engine location: INFER_ENGINE_PATH, <dir of this binary>/agent, ~/.local/share/ai/lib/agent ── */
static int is_file(const char *p) { return access(p, R_OK) == 0; }

static int find_engine(char *out, size_t n) {
    const char *over = getenv("INFER_ENGINE_PATH");
    char p[PATH_MAX + 32];
    if (over && *over) {
        snprintf(p, sizeof(p), "%s/__main__.py", over);
        if (is_file(p)) {
            int w = snprintf(out, n, "%s", over);
            return w > 0 && (size_t)w < n;
        }
    }
    char exe[PATH_MAX];
    ssize_t r = readlink("/proc/self/exe", exe, sizeof(exe) - 1);
    if (r > 0) {
        exe[r] = '\0';
        char *slash = strrchr(exe, '/');
        if (slash) {
            *slash = '\0';
            snprintf(p, sizeof(p), "%s/agent/__main__.py", exe);
            if (is_file(p)) {
                int w = snprintf(out, n, "%s/agent", exe);
                return w > 0 && (size_t)w < n;
            }
        }
    }
    const char *home = getenv("HOME");
    if (home) {
        snprintf(p, sizeof(p), "%s/.local/share/ai/lib/agent/__main__.py", home);
        if (is_file(p)) {
            int w = snprintf(out, n, "%s/.local/share/ai/lib/agent", home);
            return w > 0 && (size_t)w < n;
        }
    }
    return 0;
}

/* ── protocol out ── */
static void send_json(cJSON *o) {
    char *s = cJSON_PrintUnformatted(o);
    cJSON_Delete(o);
    if (!s) return;
    size_t len = strlen(s);
    s = realloc(s, len + 2);
    if (!s) return;
    s[len] = '\n';
    size_t off = 0;
    while (off < len + 1) {
        ssize_t w = write(g_fd, s + off, len + 1 - off);
        if (w < 0) { if (errno == EINTR) continue; break; }
        off += (size_t)w;
    }
    free(s);
}

static void send_ev(const char *ev, const char *key, const char *val) {
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "ev", ev);
    if (key) cJSON_AddStringToObject(o, key, val ? val : "");
    send_json(o);
}

static void send_set(const char *key, int value) {
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "ev", "set");
    cJSON_AddStringToObject(o, "key", key);
    cJSON_AddNumberToObject(o, "value", value);
    send_json(o);
}

/* ── protocol in ── */
static const char *jstr(cJSON *o, const char *k) {
    cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    return cJSON_IsString(v) ? v->valuestring : NULL;
}
static double jnum(cJSON *o, const char *k, double d) {
    cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    return cJSON_IsNumber(v) ? v->valuedouble : d;
}
static int jbool(cJSON *o, const char *k) {
    cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    return cJSON_IsTrue(v) || (cJSON_IsNumber(v) && v->valueint);
}

static void set_mode(int m) {
    *U->permission_mode = ((m % 3) + 3) % 3;
    if (*U->permission_mode == 0) setenv("INFER_AUTO_APPROVE", "1", 1);
    else unsetenv("INFER_AUTO_APPROVE");
}

static const char *mode_name(int m) {
    static const char *names[] = { "auto", "plan", "manual" };
    return names[((m % 3) + 3) % 3];
}

static void tty_mode(int fd, const struct termios *orig, int vmin, int vtime) {
    struct termios r = *orig;
    r.c_lflag &= ~(ECHO | ICANON);
    r.c_cc[VMIN] = (cc_t)vmin;
    r.c_cc[VTIME] = (cc_t)vtime;
    tcsetattr(fd, TCSANOW, &r);
}

/* execute_command confirmation on /dev/tty: y/Enter, n, b (background), Shift-Tab cycles the
   permission mode (auto approves), ESC / Ctrl-C cancel the task. Same dialog as ai.c. */
static const char *prompt_command(const char *cmd, int *mode_changed) {
    FILE *tty = fopen("/dev/tty", "r+");
    if (!tty) return "notty";
    fprintf(tty, "\n\033[1;33m  \xe2\x96\xb6 execute_command\033[0m\n");
    fprintf(tty, "  \033[2m%s\033[0m\n", cmd);
    fprintf(tty, "  \033[32my\033[0m/\033[31mn\033[0m/\033[36mb\033[0m  \033[2m  Confirm? (b=background)\033[0m\n\n");
    fflush(tty);
    int fd = fileno(tty);
    struct termios orig;
    int has = (tcgetattr(fd, &orig) >= 0);
    if (has) tty_mode(fd, &orig, 1, 0);
    const char *ans = "n";
    char ch = 0;
    for (;;) {
        if (read(fd, &ch, 1) != 1) { ans = "n"; break; }
        if (ch == 27) {
            if (has) tty_mode(fd, &orig, 0, 1);
            char seq[2] = {0, 0};
            int n1 = (int)read(fd, &seq[0], 1), n2 = 0;
            if (n1 == 1 && seq[0] == '[') n2 = (int)read(fd, &seq[1], 1);
            if (n1 == 1 && n2 == 1 && seq[0] == '[' && seq[1] == 'Z') {
                set_mode(*U->permission_mode + 1);
                *mode_changed = 1;
                fprintf(tty, "\n\033[2mpermission mode: %s\033[0m\n", mode_name(*U->permission_mode));
                if (*U->permission_mode == 0) {
                    fprintf(tty, "\033[32mAuto-approved\033[0m\n");
                    ans = "auto";
                    break;
                }
                fprintf(tty, "\033[1;33m[ai] Execute command:\033[0m %s\n", cmd);
                fprintf(tty, "\033[32my\033[0m/\033[31mn\033[0m/\033[36mb\033[0m  confirm  \033[2m(Shift+Tab cycle mode)\033[0m ");
                fflush(tty);
                if (has) tty_mode(fd, &orig, 1, 0);
                continue;
            }
            fprintf(tty, "^[\n");
            ans = "esc";
            break;
        }
        if (ch == '\n' || ch == '\r') { fprintf(tty, "\n"); ans = "y"; break; }
        if (ch == 'y' || ch == 'Y') { fprintf(tty, "%c\n", ch); ans = "y"; break; }
        if (ch == 'n' || ch == 'N') { fprintf(tty, "%c\n", ch); ans = "n"; break; }
        if (ch == 'b' || ch == 'B') { fprintf(tty, "%c\n", ch); ans = "b"; break; }
        if (ch == 3) { fprintf(tty, "^C\n"); ans = "esc"; break; }
    }
    fflush(tty);
    if (has) tcsetattr(fd, TCSAFLUSH, &orig);
    fclose(tty);
    return ans;
}

/* "N steps completed. Continue for 30 more?" (only with a terminal on stdin, like ai.c). */
static int prompt_continue(int steps) {
    FILE *t = fopen("/dev/tty", "r+");
    if (!t) return 0;
    if (!isatty(STDIN_FILENO)) { fclose(t); return 0; }
    fprintf(t, "\n\033[1;33m  %d steps completed. Continue for 30 more?\033[0m\n\033[32my\033[0m/\033[31mn\033[0m: ", steps);
    fflush(t);
    char resp[64] = {0};
    int ok = 0;
    if (fgets(resp, sizeof(resp), t)) {
        char *c = resp;
        while (*c == ' ' || *c == '\t' || *c == '\r' || *c == '\n') c++;
        if (*c == '\0' || *c == 'y' || *c == 'Y' || strncasecmp(c, "yes", 3) == 0) ok = 1;
    }
    fclose(t);
    return ok;
}

static void on_approve(cJSON *o) {
    int was_raw = g_raw;
    raw_off();
    const char *kind = jstr(o, "kind");
    cJSON *r = cJSON_CreateObject();
    cJSON_AddStringToObject(r, "ev", "approve_reply");
    cJSON_AddNumberToObject(r, "id", jnum(o, "id", 0));
    if (kind && strcmp(kind, "command") == 0) {
        int changed = 0;
        const char *ans = prompt_command(jstr(o, "command") ? jstr(o, "command") : "", &changed);
        cJSON_AddBoolToObject(r, "ok", strcmp(ans, "y") == 0 || strcmp(ans, "auto") == 0);
        cJSON_AddStringToObject(r, "answer", ans);
        if (changed) cJSON_AddNumberToObject(r, "mode", *U->permission_mode);
    } else if (kind && strcmp(kind, "continue") == 0) {
        int ok = prompt_continue((int)jnum(o, "steps", 0));
        cJSON_AddBoolToObject(r, "ok", ok);
        cJSON_AddStringToObject(r, "answer", ok ? "y" : "n");
    } else {
        int v = U->prompt_user_ok(jstr(o, "title"), jstr(o, "detail"));
        cJSON_AddBoolToObject(r, "ok", v == 1);
        cJSON_AddStringToObject(r, "answer", v == 1 ? "y" : (v == 0 ? "n" : "notty"));
    }
    send_json(r);
    if (was_raw) raw_on();
}

/* ── interactive turn: read lines until one goes to the engine ── */
static void read_turn(cJSON *o) {
    int pct = (int)jnum(o, "ctx_pct", 0);
    if (g_read_count++ == 0) {
        if (!isatty(STDIN_FILENO) && !freopen("/dev/tty", "r", stdin)) {
            send_ev("eof", NULL, NULL);
            return;
        }
        U->lineed_init();
    }
    for (;;) {
        int m = *U->permission_mode;
        const char *icon = m == 0 ? "\033[32m\xe2\x97\x8f" : m == 1 ? "\033[33m\xe2\x97\x90" : "\033[31m\xe2\x97\x8b";
        char hint[64] = "";
        if (pct > 75) snprintf(hint, sizeof(hint), " \033[2m[ctx:%d%%]\033[0m", pct);
        char prompt[512];
        snprintf(prompt, sizeof(prompt),
                 "\033[1;35mai\033[0m \033[2m\xe2\x94\x82\033[0m %s %s\033[0m \033[2m\xe2\x94\x82 details: %s\033[0m%s \033[1;35m\xe2\x96\xb8\033[0m ",
                 icon, mode_name(m), *U->hide_details ? "\033[33mhidden" : "\033[36mvisible", hint);
        char *line = U->read_line(prompt);
        if (!line) {
            printf("\n");
            fflush(stdout);
            send_ev("eof", NULL, NULL);
            return;
        }
        if (line[0] == '\033' && line[1] == '[' && line[2] == 'Z') {     /* Shift-Tab */
            free(line);
            set_mode(*U->permission_mode + 1);
            printf("\033[2mpermission mode: %s\033[0m\n", mode_name(*U->permission_mode));
            fflush(stdout);
            send_set("permission_mode", *U->permission_mode);
            continue;
        }
        if (line[0] && line[0] != ':') {
            U->add_turn_item(FE_ITEM_USER, "User", NULL, line, 0, 0, g_turn, g_tools);
            U->print_user_message(line);
        }
        if (strcmp(line, "exit") == 0 || strcmp(line, "quit") == 0 ||
            strcmp(line, ":quit") == 0 || strcmp(line, ":exit") == 0) {
            free(line);
            send_ev("eof", NULL, NULL);
            return;
        }
        if (!line[0]) { free(line); continue; }
        if (line[0] == ':') {
            int local = 1;
            if (strcmp(line, ":details") == 0 || strcmp(line, ":tools") == 0) {
                *U->hide_details = !*U->hide_details;
                send_set("hide_details", *U->hide_details);
                U->print_info_box("Details Visibility Toggled",
                    *U->hide_details ? "Tools and thinking output are now HIDDEN (Ctrl+O to toggle)."
                                     : "Tools and thinking output are now VISIBLE (Ctrl+O to toggle).");
                if (!*U->hide_details) U->jobs_status();
            } else if (strcmp(line, ":jobs") == 0 || strcmp(line, ":tasks") == 0) {
                U->jobs_status();
            } else if (strcmp(line, ":copy") == 0) {
                if (g_last && *g_last) U->copy_to_clipboard(g_last);
                else printf("\033[2mNo response to copy yet.\033[0m\n");
            } else if (strcmp(line, ":help") == 0) {
                U->print_info_box("Interactive Commands",
                    "  :details       Toggle details (tools & thinking output on/off)\n"
                    "  :mode [preset] Live sampling preset: xhigh/normal/low/instruct (no arg = show)\n"
                    "  :budget [tok]  Set thinking reasoning token budget (e.g. 4096, 8192, off)\n"
                    "  :compact       Summarise + reset context (keeps semantic history)\n"
                    "  :clear         Wipe conversation history entirely\n"
                    "  :status        Show context size and model info\n"
                    "  :memory        Show persistent memory\n"
                    "  :auto          Toggle auto-approve for execute_command\n"
                    "  :btw <msg>     Inject a note into the agent context mid-task\n"
                    "  :commit        Commit staged changes to git\n"
                    "  :undo          Revert last git commit (keeps changes staged)\n"
                    "  :git-diff      Show current diff summary\n"
                    "  :git-status    Show git status\n"
                    "  :copy          Copy last response to clipboard\n"
                    "  :agents        Regenerate AGENTS.md from project scan\n"
                    "  :notify        Toggle OS notifications on task complete\n"
                    "  :help          Show this message\n"
                    "  :quit/:exit    Leave interactive mode");
                printf("\033[2mPress Ctrl+O to toggle details (tools/thinking). "
                       "Shift-Tab to cycle permission mode. Ctrl+C or ESC to interrupt.\033[0m\n\n");
            } else {
                local = 0;
            }
            fflush(stdout);
            if (local) { free(line); continue; }
            send_ev("command", "s", line);
            free(line);
            return;
        }
        U->lineed_add_history(line);
        send_ev("input", "s", line);
        free(line);
        return;
    }
}

/* ── keys typed while a task runs (raw mode): ESC, Shift-Tab, Ctrl-O, ":btw note" lines ── */
static void on_key(void) {
    char ch;
    while (read(STDIN_FILENO, &ch, 1) == 1) {
        if (ch == 27) {
            char seq[2] = {0, 0};
            struct pollfd p = { STDIN_FILENO, POLLIN, 0 };
            int n = 0;
            if (poll(&p, 1, 30) > 0) n = (int)read(STDIN_FILENO, seq, 2);
            if (n == 2 && seq[0] == '[' && seq[1] == 'Z') {
                if (g_active && *U->permission_mode == 0) {
                    fprintf(stderr, "\n\033[2m[Shift-Tab: auto-approve can only be enabled before a task starts]\033[0m\n");
                } else {
                    set_mode(*U->permission_mode + 1);
                    send_set("permission_mode", *U->permission_mode);
                    fprintf(stderr, "\n\033[2mpermission mode: %s\033[0m\n", mode_name(*U->permission_mode));
                }
                fflush(stderr);
                g_btw_len = 0;
            } else {
                send_ev("interrupt", NULL, NULL);
            }
        } else if (ch == 15) {                           /* Ctrl+O */
            *U->hide_details = !*U->hide_details;
            send_set("hide_details", *U->hide_details);
            U->redraw();
            fflush(stdout);
        } else if (ch == '\r' || ch == '\n') {
            g_btw[g_btw_len] = '\0';
            const char *l = g_btw;
            while (*l == ' ') l++;
            if (strncmp(l, ":btw", 4) == 0) { l += 4; while (*l == ' ') l++; }
            if (*l) {
                send_ev("btw", "s", l);
                fprintf(stderr, "\n\033[2m[scheduled] %s\033[0m\n", l);
                fflush(stderr);
            }
            g_btw_len = 0;
        } else if (ch == 127 || ch == 8) {
            if (g_btw_len > 0) { g_btw_len--; fprintf(stderr, "\b \b"); fflush(stderr); }
        } else if (ch >= 32 && ch <= 126) {
            if (g_btw_len < (int)sizeof(g_btw) - 1) {
                g_btw[g_btw_len++] = ch;
                fputc(ch, stderr);
                fflush(stderr);
            }
        }
    }
}

/* ── event dispatch ── */
static void close_thinking(int for_toolcall) {
    if (g_hdr && !g_ftr) {
        fprintf(stderr, for_toolcall ? "\033[0m\n" : "\n\033[0m\n");
        g_ftr = 1;
    } else if (!g_hdr) {
        fprintf(stderr, "\r\033[2K");
        g_hdr = 1;
    }
    fflush(stderr);
}

/* Returns 1 to keep running, 0 after "done" (*code set). */
static int on_event(cJSON *o, int *code) {
    const char *ev = jstr(o, "ev");
    if (!ev) return 1;
    if (strcmp(ev, "text") == 0) {
        const char *s = jstr(o, "s");
        close_thinking(0);
        if (s && isatty(STDOUT_FILENO)) { printf("\033[2m%s\033[0m", s); fflush(stdout); }
    } else if (strcmp(ev, "thinking") == 0) {
        const char *s = jstr(o, "s");
        if (jbool(o, "stream")) {
            if (s && !g_quiet && !*U->hide_details) {
                if (!g_hdr) {
                    fprintf(stderr, "\r\033[2K\n\033[2m\xe2\x96\xb8 thinking\033[0m\n\033[2;35m");
                    g_hdr = 1;
                }
                fputs(s, stderr);
                fflush(stderr);
            }
        } else if (s) {
            U->add_turn_item(FE_ITEM_THINKING, "thinking", NULL, s, 0, 0, 0, 0);
            U->print_think_box(s);
            fflush(stdout);
        }
    } else if (strcmp(ev, "status") == 0) {
        const char *s = jstr(o, "s");
        if (!s) return 1;
        if (strcmp(s, "request") == 0) {
            g_hdr = g_ftr = 0;
            if (!g_quiet && !*U->hide_details) { fprintf(stderr, "\033[2m[thinking]...\033[0m"); fflush(stderr); }
        } else if (strcmp(s, "toolcall") == 0) {
            close_thinking(1);
        } else if (strcmp(s, "response") == 0) {
            if (!g_quiet) {
                if (g_hdr && !g_ftr) fprintf(stderr, "\033[0m\n");
                else if (!g_hdr) fprintf(stderr, "\r\033[2K");
                fflush(stderr);
            }
            const char *rc = jstr(o, "reasoning");
            if (rc && *rc) U->add_turn_item(FE_ITEM_THINKING, "thinking", NULL, rc, 0, 0, 0, 0);
        } else if (strcmp(s, "agent_start") == 0) {
            g_active = 1;
            raw_on();
        } else if (strcmp(s, "agent_end") == 0) {
            g_active = 0;
            raw_off();
        } else if (strcmp(s, "mode") == 0) {
            set_mode((int)jnum(o, "mode", *U->permission_mode));
        }
    } else if (strcmp(ev, "tool_end") == 0) {
        const char *name = jstr(o, "name"), *st = jstr(o, "status"), *pv = jstr(o, "preview");
        double el = jnum(o, "elapsed", 0);
        g_turn = (int)jnum(o, "turn", g_turn);
        g_tools = (int)jnum(o, "tools", g_tools);
        U->print_tool_box(name, st, pv, el);
        U->add_turn_item(FE_ITEM_TOOL, name, st, pv, el, 0, g_turn, g_tools);
        fflush(stdout);
    } else if (strcmp(ev, "approve") == 0) {
        on_approve(o);
    } else if (strcmp(ev, "final") == 0) {
        const char *md = jstr(o, "markdown"), *rd = jstr(o, "rendered"), *model = jstr(o, "model");
        double el = jnum(o, "elapsed", 0), tps = jnum(o, "tps", 0);
        int turn = (int)jnum(o, "turn", 0), tools = (int)jnum(o, "tools", 0);
        g_turn = turn;
        g_tools = tools;
        fflush(stderr);
        U->add_turn_item(FE_ITEM_ANSWER, model, NULL, md, el, tps, turn, tools);
        if (rd && *rd) U->print_response_box(model, rd, turn, tools, el, tps, 1);
        else U->print_response_box(model, md, turn, tools, el, tps, 0);
        fflush(stdout);
        free(g_last);
        g_last = md ? strdup(md) : NULL;
    } else if (strcmp(ev, "out") == 0) {
        const char *s = jstr(o, "s");
        if (s) { fputs(s, stdout); fflush(stdout); }
    } else if (strcmp(ev, "err") == 0) {
        const char *s = jstr(o, "s");
        if (s) { fputs(s, stderr); fflush(stderr); }
    } else if (strcmp(ev, "info") == 0) {
        const char *t = jstr(o, "title"), *b = jstr(o, "body");
        U->add_turn_item(FE_ITEM_INFO, t, NULL, b, 0, 0, g_turn, g_tools);
        U->print_info_box(t, b);
        fflush(stdout);
    } else if (strcmp(ev, "read") == 0) {
        raw_off();
        read_turn(o);
    } else if (strcmp(ev, "config") == 0) {
        g_quiet = jbool(o, "quiet");
        *U->hide_details = jbool(o, "hide_details");
        set_mode((int)jnum(o, "permission_mode", 0));
    } else if (strcmp(ev, "done") == 0) {
        *code = (int)jnum(o, "code", 0);
        return 0;
    }
    return 1;
}

int frontend_main(int argc, char **argv, const frontend_ui_t *ui) {
    char engine[PATH_MAX];
    if (!find_engine(engine, sizeof(engine))) {
        const char *strict = getenv("INFER_ENGINE_STRICT");
        if (strict && *strict && strcmp(strict, "0") != 0) {
            fprintf(stderr, "[ai] the Python engine (agent/) was not found (INFER_ENGINE_STRICT=1).\n");
            return 1;
        }
        fprintf(stderr, "\033[33m[ai] the Python engine (agent/) was not found; "
                        "using the legacy C engine.\033[0m\n");
        return -1;
    }
    U = ui;
    int sv[2];
    if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sv) < 0) {
        perror("[ai] socketpair");
        return -1;
    }
    fflush(stdout);
    fflush(stderr);
    pid_t pid = fork();
    if (pid < 0) {
        perror("[ai] fork");
        close(sv[0]);
        close(sv[1]);
        return -1;
    }
    if (pid == 0) {
        close(sv[0]);
        if (sv[1] != 3) {
            dup2(sv[1], 3);
            close(sv[1]);
        }
        int fl = fcntl(3, F_GETFD);
        if (fl >= 0) fcntl(3, F_SETFD, fl & ~FD_CLOEXEC);
        signal(SIGINT, SIG_DFL);
        signal(SIGTERM, SIG_DFL);
        char **eargv = calloc((size_t)argc + 5, sizeof(char *));
        if (!eargv) _exit(127);
        int k = 0;
        eargv[k++] = "python3";
        eargv[k++] = engine;
        eargv[k++] = "--frontend-fd";
        eargv[k++] = "3";
        for (int i = 1; i < argc; i++) eargv[k++] = argv[i];
        eargv[k] = NULL;
        execvp("python3", eargv);
        fprintf(stderr, "[ai] cannot start the Python engine: %s\n", strerror(errno));
        _exit(127);
    }
    close(sv[1]);
    g_fd = sv[0];
    g_engine = pid;
    reap_track(pid);         /* reap_children() keeps the engine's status for the exit below */
    reap_install_sigchld();  /* a finished orphan wakes poll(); the loop tick reaps it */
    signal(SIGINT, fe_signal);
    signal(SIGTERM, fe_signal);
    signal(SIGPIPE, SIG_IGN);

    int code = -1;
    size_t cap = 65536, len = 0;
    char *buf = malloc(cap);
    int running = 1;
    while (running && buf) {
        reap_children();  /* finished background orphans reparented to us (see ai_reap.h) */
        struct pollfd p[2];
        nfds_t np = 0;
        p[np].fd = g_fd; p[np].events = POLLIN; p[np].revents = 0; np++;
        if (g_active && g_raw) { p[np].fd = STDIN_FILENO; p[np].events = POLLIN; p[np].revents = 0; np++; }
        int r = poll(p, np, -1);
        if (r < 0) { if (errno == EINTR) continue; break; }
        if (np > 1 && (p[1].revents & POLLIN)) on_key();
        if (!(p[0].revents & (POLLIN | POLLHUP | POLLERR))) continue;
        if (cap - len < 4096) {
            char *nb = realloc(buf, cap * 2);
            if (!nb) break;
            buf = nb;
            cap *= 2;
        }
        ssize_t n = read(g_fd, buf + len, cap - len - 1);
        if (n < 0) { if (errno == EINTR) continue; break; }
        if (n == 0) break;
        len += (size_t)n;
        size_t start = 0;
        for (size_t i = 0; i < len && running; i++) {
            if (buf[i] != '\n') continue;
            buf[i] = '\0';
            cJSON *o = cJSON_Parse(buf + start);
            if (o) {
                int c = 0;
                if (!on_event(o, &c)) { code = c; running = 0; }
                cJSON_Delete(o);
            }
            start = i + 1;
        }
        if (start > 0) {
            memmove(buf, buf + start, len - start);
            len -= start;
        }
    }
    free(buf);
    raw_off();
    close(g_fd);
    int st = 0;
    if (!reap_tracked(&st)) {  /* not yet collected by a reaper tick */
        while (waitpid(pid, &st, 0) < 0 && errno == EINTR) {}
    }
    if (code < 0) {
        fprintf(stderr, "[ai] the Python engine exited unexpectedly (%s %d)\n",
                WIFSIGNALED(st) ? "signal" : "status", WIFSIGNALED(st) ? WTERMSIG(st) : WEXITSTATUS(st));
        code = 1;
    }
    free(g_last);
    return code;
}
