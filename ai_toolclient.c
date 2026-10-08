/* Client for the persistent tool server; see ai_toolclient.h and aitools/server.py (protocol). */
#define _GNU_SOURCE
#include "ai_toolclient.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include "cJSON.h"

extern char **environ;

#define TC_SPAWN_WAIT_MS 2000
#define TC_MAX_SPAWNS    3
#define TC_LOG_MAX       (1024 * 1024)

static char g_script[PATH_MAX];
static int (*g_interrupt)(void) = NULL;
static int g_spawns = 0;
static int g_spawn_failed = 0;

void toolclient_set_script(const char *mcp_script)
{
    g_script[0] = '\0';
    if (mcp_script && !realpath(mcp_script, g_script))
        g_script[0] = '\0';
}

void toolclient_set_interrupt(int (*cb)(void)) { g_interrupt = cb; }

static int tc_enabled(void)
{
    const char *v = getenv("INFER_TOOL_SERVER");
    if (v && strcmp(v, "0") == 0) return 0;
    return g_script[0] != '\0';
}

static double tc_now(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

/* Same resolution as aitools/server.py socket_path(). */
static int tc_socket_path(char *buf, size_t n)
{
    const char *xdg = getenv("XDG_RUNTIME_DIR");
    struct stat st;
    int len;
    if (xdg && *xdg && stat(xdg, &st) == 0 && S_ISDIR(st.st_mode)) {
        len = snprintf(buf, n, "%s/ai/tools.sock", xdg);
    } else {
        const char *home = getenv("HOME");
        if (!home || !*home) return -1;
        len = snprintf(buf, n, "%s/.cache/ai/tools.sock", home);
    }
    if (len < 0 || (size_t)len >= n || (size_t)len >= sizeof(((struct sockaddr_un *)0)->sun_path))
        return -1;
    return 0;
}

static int tc_connect(const char *path)
{
    struct stat st;
    if (lstat(path, &st) != 0 || !S_ISSOCK(st.st_mode) || st.st_uid != getuid())
        return -1;
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) return -1;
    struct sockaddr_un sa;
    memset(&sa, 0, sizeof(sa));
    sa.sun_family = AF_UNIX;
    snprintf(sa.sun_path, sizeof(sa.sun_path), "%s", path);
    if (connect(fd, (struct sockaddr *)&sa, sizeof(sa)) != 0) {
        close(fd);
        return -1;
    }
    struct ucred cr;
    socklen_t cl = sizeof(cr);
    if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &cr, &cl) != 0 || cr.uid != getuid()) {
        close(fd);
        return -1;
    }
    return fd;
}

/* systemd-run on PATH and a reachable systemd user instance ($XDG_RUNTIME_DIR/bus or
 * .../systemd/private)? Then the server is started in its own transient scope, so it does not die
 * with the cgroup of whatever spawned it (zulip-ai-bridge.service restart, a oneshot unit ending).
 * INFER_TOOL_SERVER_SCOPE=0 disables this. Writes systemd-run's path to `out`. */
static int tc_scope_available(char *out, size_t n)
{
    const char *v = getenv("INFER_TOOL_SERVER_SCOPE");
    if (v && strcmp(v, "0") == 0) return 0;
    const char *xdg = getenv("XDG_RUNTIME_DIR");
    char probe[PATH_MAX];
    if (!xdg || !*xdg) return 0;
    snprintf(probe, sizeof(probe), "%s/bus", xdg);
    if (access(probe, F_OK) != 0) {
        snprintf(probe, sizeof(probe), "%s/systemd/private", xdg);
        if (access(probe, F_OK) != 0) return 0;
    }
    const char *path = getenv("PATH");
    if (!path) return 0;
    const char *p = path;
    while (*p) {
        const char *c = strchr(p, ':');
        size_t len = c ? (size_t)(c - p) : strlen(p);
        if (len > 0 && len < n - 16) {
            snprintf(out, n, "%.*s/systemd-run", (int)len, p);
            if (access(out, X_OK) == 0) return 1;
        }
        if (!c) break;
        p = c + 1;
    }
    return 0;
}

/* Start `python3 <script> serve` fully detached: double fork + setsid, stdio on /dev/null
 * (stderr appended to server.log next to the socket), cwd /. With `scope`, the grandchild execs
 * `systemd-run --user --scope --collect --quiet --unit=ai-tools-<uid>-<ts>-<pid> -- python3 ...`,
 * which moves itself into a new transient scope and then execs the server. The intermediate
 * child is reaped here, so no zombie is left; the server is reparented to init. */
static void tc_spawn(const char *sock_path, const char *scope)
{
    char unit[96];
    snprintf(unit, sizeof(unit), "--unit=ai-tools-%u-%ld-%d", (unsigned)getuid(), (long)time(NULL), (int)getpid());
    char dir[PATH_MAX], logp[PATH_MAX + 16];
    snprintf(dir, sizeof(dir), "%s", sock_path);
    char *slash = strrchr(dir, '/');
    if (slash) *slash = '\0';
    mkdir(dir, 0700);
    snprintf(logp, sizeof(logp), "%s/server.log", dir);
    struct stat st;
    int trunc = (stat(logp, &st) == 0 && st.st_size > TC_LOG_MAX);

    /* ai is a child subreaper (main()): while it is one, the orphaned server would be reparented
     * to ai and become ai's zombie if it exits first. Drop the flag until the intermediate child
     * is reaped, so the server goes to init (or an outer subreaper) like any daemon. */
    int subreaper = 0;
    prctl(PR_GET_CHILD_SUBREAPER, &subreaper, 0, 0, 0);
    if (subreaper) prctl(PR_SET_CHILD_SUBREAPER, 0, 0, 0, 0);
    pid_t p = fork();
    if (p < 0) {
        if (subreaper) prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0);
        return;
    }
    if (p == 0) {
        setsid();
        pid_t q = fork();
        if (q != 0) _exit(0);
        sigset_t none;
        sigemptyset(&none);
        sigprocmask(SIG_SETMASK, &none, NULL);
        int sigs[] = {SIGCHLD, SIGPIPE, SIGINT, SIGTERM, SIGHUP, SIGQUIT, SIGTSTP, SIGTTIN, SIGTTOU};
        for (size_t i = 0; i < sizeof(sigs) / sizeof(sigs[0]); i++) signal(sigs[i], SIG_DFL);
        int dn = open("/dev/null", O_RDWR);
        int lf = open(logp, O_WRONLY | O_CREAT | O_APPEND | (trunc ? O_TRUNC : 0), 0600);
        if (dn >= 0) { dup2(dn, 0); dup2(dn, 1); }
        dup2(lf >= 0 ? lf : dn, 2);
        long maxfd = sysconf(_SC_OPEN_MAX);
        if (maxfd < 0 || maxfd > 65536) maxfd = 65536;
        for (int fd = 3; fd < maxfd; fd++) close(fd);
        if (chdir("/") != 0) { /* ignore */ }
        if (scope)
            execl(scope, "systemd-run", "--user", "--scope", "--collect", "--quiet", unit, "--",
                  "python3", g_script, "serve", (char *)NULL);
        else
            execlp("python3", "python3", g_script, "serve", (char *)NULL);
        _exit(127);
    }
    while (waitpid(p, NULL, 0) < 0 && errno == EINTR) {}
    if (subreaper) prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0);
}

static int tc_wait_connect(const char *path, int ms)
{
    double end = tc_now() + ms / 1000.0;
    for (;;) {
        int fd = tc_connect(path);
        if (fd >= 0) return fd;
        if (tc_now() > end) return -1;
        usleep(20000);
    }
}

static char *tc_build_request(const char *op, const char *server, const char *tool,
                              const char *args_json, int timeout_s)
{
    cJSON *req = cJSON_CreateObject();
    if (!req) return NULL;
    cJSON_AddNumberToObject(req, "id", 1);
    cJSON_AddStringToObject(req, "op", op);
    if (server) cJSON_AddStringToObject(req, "server", server);
    if (tool) cJSON_AddStringToObject(req, "tool", tool);
    if (args_json)
        cJSON_AddStringToObject(req, strcmp(op, "render") == 0 ? "text" : "args", args_json);
    cJSON_AddNumberToObject(req, "timeout", timeout_s > 0 ? timeout_s : 120);
    char cwd[PATH_MAX];
    if (getcwd(cwd, sizeof(cwd))) cJSON_AddStringToObject(req, "cwd", cwd);
    cJSON_AddStringToObject(req, "script", g_script);
    /* The whole environment: tools read INFER_*, AI_*, PATH, ... exactly as a popen'd
     * `ai_mcp.py` would have inherited them. Same uid only (SO_PEERCRED on both ends). */
    cJSON *env = cJSON_AddObjectToObject(req, "env");
    for (char **e = environ; env && e && *e; e++) {
        const char *eq = strchr(*e, '=');
        if (!eq || eq == *e) continue;
        char key[1024];
        size_t kl = (size_t)(eq - *e);
        if (kl >= sizeof(key)) continue;
        memcpy(key, *e, kl);
        key[kl] = '\0';
        cJSON_AddItemToObject(env, key, cJSON_CreateString(eq + 1));
    }
    char *s = cJSON_PrintUnformatted(req);
    cJSON_Delete(req);
    return s;
}

enum { EX_OK = 0, EX_NOT_SENT = 1, EX_LOST = 2, EX_INTERRUPTED = 3, EX_DEADLINE = 4 };

/* Send one request line (one buffer, so a request is never delivered without its newline; the
 * server ignores an unterminated line) and read one response line. Closes fd.
 * EX_NOT_SENT: the server cannot have run it: the send failed (the final "\n" never left us), or
 * the connection was reset before any answer byte. On AF_UNIX a reset means the server closed
 * with our request still unread in its receive queue (a dying listener's backlog, or a server
 * killed before reading); a server that read the line and then died gives EOF instead.
 * EX_LOST: the request was consumed and no answer came: the call may have run.
 * Invariants the no-resend rule relies on (aitools/server.py _Handler.handle keeps the server side):
 * one request per connection; no client write after the newline; the server reads the whole line
 * before dispatching (readline, no size limit); Linux AF_UNIX semantics (closing a socket with
 * unread data resets the peer, a close after reading gives EOF). */
static int tc_exchange(int fd, const char *req, int timeout_s, char **line)
{
    *line = NULL;
    size_t rl = strlen(req), sent = 0;
    while (sent < rl) {
        ssize_t n = send(fd, req + sent, rl - sent, MSG_NOSIGNAL);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) { close(fd); return EX_NOT_SENT; }
        sent += (size_t)n;
    }
    size_t cap = 65536, len = 0;
    char *buf = malloc(cap);
    if (!buf) { close(fd); return EX_LOST; }
    double deadline = tc_now() + (timeout_s > 0 ? timeout_s : 120) + 15;
    for (;;) {
        if (g_interrupt && g_interrupt()) { free(buf); close(fd); return EX_INTERRUPTED; }
        if (tc_now() > deadline) { free(buf); close(fd); return EX_DEADLINE; }
        struct pollfd pfd = {fd, POLLIN, 0};
        int r = poll(&pfd, 1, 100);
        if (r < 0 && errno == EINTR) continue;
        if (r < 0) { free(buf); close(fd); return EX_LOST; }
        if (r == 0) continue;
        if (len + 65536 + 1 > cap) {
            cap *= 2;
            char *nb = realloc(buf, cap);
            if (!nb) { free(buf); close(fd); return EX_LOST; }
            buf = nb;
        }
        ssize_t n = recv(fd, buf + len, cap - len - 1, 0);
        if (n < 0 && (errno == EINTR || errno == EAGAIN)) continue;
        if (n < 0 && errno == ECONNRESET && len == 0) { free(buf); close(fd); return EX_NOT_SENT; }
        if (n <= 0) { free(buf); close(fd); return EX_LOST; }
        len += (size_t)n;
        buf[len] = '\0';
        if (memchr(buf + len - n, '\n', (size_t)n)) break;
    }
    close(fd);
    *line = buf;
    return EX_OK;
}

static const char *tc_str(cJSON *o, const char *k)
{
    cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);
    return cJSON_IsString(v) ? v->valuestring : NULL;
}

/* Answers the server gives BEFORE running anything (aitools/server.py REFUSALS): only these,
 * a failed connect and an unsent request may fall back to popen. */
/* Mirrors aitools/server.py REFUSALS (tests/test_list_drift.py keeps them equal). */
static int tc_refusal(const char *err)
{
    static const char *refusals[] = {"stale", "mismatch", "unsupported", "bad request"};
    for (size_t i = 0; err && i < sizeof(refusals) / sizeof(refusals[0]); i++)
        if (strcmp(err, refusals[i]) == 0) return 1;
    return 0;
}

static int tc_start(const char *path)
{
    char sr[PATH_MAX];
    if (tc_scope_available(sr, sizeof(sr))) {
        tc_spawn(path, sr);
        int fd = tc_wait_connect(path, TC_SPAWN_WAIT_MS);
        if (fd >= 0) return fd;
        /* systemd-run refused (no session bus, unit clash...): plain detached spawn */
    }
    tc_spawn(path, NULL);
    return tc_wait_connect(path, TC_SPAWN_WAIT_MS);
}

int toolclient_call(const char *op, const char *server, const char *tool,
                    const char *args_json, int timeout_s, char **out)
{
    *out = NULL;
    if (!op || !tc_enabled()) return TOOLCLIENT_UNAVAILABLE;
    char path[PATH_MAX];
    if (tc_socket_path(path, sizeof(path)) != 0) return TOOLCLIENT_UNAVAILABLE;
    char *body = tc_build_request(op, server, tool, args_json, timeout_s);
    if (!body) return TOOLCLIENT_UNAVAILABLE;
    size_t bl = strlen(body);
    char *req = malloc(bl + 2);
    if (!req) { free(body); return TOOLCLIENT_UNAVAILABLE; }
    memcpy(req, body, bl);
    req[bl] = '\n';
    req[bl + 1] = '\0';
    free(body);

    int rc = TOOLCLIENT_UNAVAILABLE;
    int spawned = 0;
    for (int attempt = 0; attempt < 2; attempt++) {
        int fd = tc_connect(path);
        if (fd < 0) {
            /* Nothing listens (first use, idle exit, crash, or a stale server that stepped
             * down): start one, at most once per call and a few times per process. */
            if (spawned || g_spawn_failed || g_spawns >= TC_MAX_SPAWNS) break;
            spawned = 1;
            g_spawns++;
            fd = tc_start(path);
            if (fd < 0) { g_spawn_failed = 1; break; }
        }
        char *line = NULL;
        int ex = tc_exchange(fd, req, timeout_s, &line);
        if (ex == EX_NOT_SENT) continue; /* nothing ran: like a failed connect (respawn once) */
        if (ex == EX_INTERRUPTED) { rc = TOOLCLIENT_INTERRUPTED; break; }
        if (ex == EX_DEADLINE) { *out = strdup(""); rc = TOOLCLIENT_TIMEOUT; break; }
        if (ex != EX_OK) { rc = TOOLCLIENT_LOST; break; }
        cJSON *resp = cJSON_Parse(line);
        free(line);
        if (!resp) { rc = TOOLCLIENT_LOST; break; }
        cJSON *ok = cJSON_GetObjectItemCaseSensitive(resp, "ok");
        const char *err = tc_str(resp, "error");
        const char *res = tc_str(resp, "result");
        const char *serr = tc_str(resp, "stderr");
        if (!cJSON_IsTrue(ok) && tc_refusal(err)) {
            int stale = strcmp(err, "stale") == 0;
            cJSON_Delete(resp);
            if (!stale) break; /* mismatch / unsupported / bad request: nothing ran, popen */
            continue;          /* the old server already unlinked its socket: respawn */
        }
        if (serr && *serr && strcmp(op, "render") != 0) {
            fputs(serr, stderr);
            fflush(stderr);
        }
        *out = strdup(res ? res : "");
        if (cJSON_IsTrue(ok)) rc = TOOLCLIENT_OK;
        else if (err && strcmp(err, "timeout") == 0) rc = TOOLCLIENT_TIMEOUT;
        else if (err && strcmp(err, "interrupted") == 0) rc = TOOLCLIENT_INTERRUPTED;
        else rc = TOOLCLIENT_LOST; /* e.g. "server error": the call may have run */
        cJSON_Delete(resp);
        break;
    }
    free(req);
    return rc;
}
