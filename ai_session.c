#include "ai_session.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <unistd.h>
#include <dirent.h>
#include <sys/stat.h>
#define JSMN_HEADER
#include "jsmn.h"

extern char current_session_id[64];
extern char *resume_session_id;
extern const char *SYSTEM_PROMPT;
extern volatile int g_esc_requested;
extern int raw_mode_active;
extern int g_compact_in_progress;
extern int g_compact_dot_timer;
extern char *g_system_message_json;

void disable_raw_mode(void);
char* json_escape(const char *str);
char* shell_escape(const char *str);
char* unescape_json_string(const char *str, int len);
char* run_shell_command(const char *cmd, int *exit_code);

static char* append_message(char *messages_json, const char *msg_to_append) {
    if (!messages_json) {
        size_t len = strlen(msg_to_append) + 16;
        char *buf = malloc(len);
        snprintf(buf, len, "[%s]", msg_to_append);
        return buf;
    }
    size_t orig_len = strlen(messages_json);
    if (orig_len >= 2 && messages_json[orig_len - 1] == ']') {
        size_t msg_len = strlen(msg_to_append);
        size_t new_size = orig_len + msg_len + 4;
        char *buf = realloc(messages_json, new_size);
        if (orig_len > 2) {
            buf[orig_len - 1] = ',';
            strcpy(buf + orig_len, msg_to_append);
            strcat(buf, "]");
        } else {
            strcpy(buf, "[");
            strcat(buf, msg_to_append);
            strcat(buf, "]");
        }
        return buf;
    }
    return messages_json;
}

char* read_memory_file(void) {
    char *home = getenv("HOME");
    if (!home) return NULL;
    char path[1024];
    snprintf(path, sizeof(path), "%s/.config/ai/memory.txt", home);
    FILE *fp = fopen(path, "r");
    if (!fp) {
        snprintf(path, sizeof(path), "%s/.local/share/ai/memory.json", home);
        fp = fopen(path, "r");
    }
    if (!fp) return NULL;
    
    fseek(fp, 0, SEEK_END);
    long size = ftell(fp);
    fseek(fp, 0, SEEK_SET);
    
    if (size <= 0 || size > 65536) {
        fclose(fp);
        return NULL;
    }
    
    char *buf = malloc(size + 1);
    if (!buf) {
        fclose(fp);
        return NULL;
    }
    
    size_t read_bytes = fread(buf, 1, size, fp);
    buf[read_bytes] = '\0';
    fclose(fp);

    /* Basic validation */
    char *p = buf;
    while (*p && isspace((unsigned char)*p)) p++;
    if (!*p) {
        free(buf);
        return NULL;
    }
    if (strstr(path, ".json") && *p != '{' && *p != '[') {
        free(buf);
        return NULL;
    }
    return buf;
}

char* load_system_prompt(void) {
    char path[1024];
    const char *override = getenv("INFER_SYSTEM_PROMPT_FILE");
    if (override && override[0]) {
        snprintf(path, sizeof(path), "%s", override);
    } else {
        char *home = getenv("HOME");
        if (!home) return strdup(SYSTEM_PROMPT);
        snprintf(path, sizeof(path), "%s/.config/ai/system_prompt.md", home);
    }

    FILE *fp = fopen(path, "r");
    if (!fp) return strdup(SYSTEM_PROMPT);
    fseek(fp, 0, SEEK_END);
    long sz = ftell(fp);
    fseek(fp, 0, SEEK_SET);
    if (sz <= 0) { fclose(fp); return strdup(SYSTEM_PROMPT); }
    char *buf = malloc(sz + 1);
    if (!buf) { fclose(fp); return strdup(SYSTEM_PROMPT); }
    size_t rb = fread(buf, 1, sz, fp);
    buf[rb] = '\0';
    fclose(fp);

    char *p = buf;
    while (*p && isspace((unsigned char)*p)) p++;
    if (!*p) { free(buf); return strdup(SYSTEM_PROMPT); }
    return buf;
}

static int session_file_path(char *out, size_t out_len, const char *session_id) {
    char *home = getenv("HOME");
    if (!home) return -1;
    char dir[1024];
    snprintf(dir, sizeof(dir), "%s/.cache", home);
    mkdir(dir, 0700);
    snprintf(dir, sizeof(dir), "%s/.cache/ai", home);
    mkdir(dir, 0700);
    snprintf(dir, sizeof(dir), "%s/.cache/ai/sessions", home);
    mkdir(dir, 0700);
    if (session_id && *session_id) {
        snprintf(out, out_len, "%s/%s.json", dir, session_id);
    } else {
        snprintf(out, out_len, "%s/last.json", dir);
    }
    return 0;
}

/* Backup path: the conversation is also mirrored into the persistent local
   user-data directory (~/.local/share/ai/sessions), not just the cache, so it
   survives cache clears and is the canonical searchable history store. */
static int backup_session_file_path(char *out, size_t out_len, const char *session_id) {
    char *home = getenv("HOME");
    if (!home) return -1;
    char dir[1024];
    /* mkdir -p equivalent for ~/.local/share/ai/sessions */
    snprintf(dir, sizeof(dir), "%s/.local", home);      mkdir(dir, 0700);
    snprintf(dir, sizeof(dir), "%s/.local/share", home); mkdir(dir, 0700);
    snprintf(dir, sizeof(dir), "%s/.local/share/ai", home); mkdir(dir, 0700);
    snprintf(dir, sizeof(dir), "%s/.local/share/ai/sessions", home); mkdir(dir, 0700);
    if (session_id && *session_id) {
        snprintf(out, out_len, "%s/%s.json", dir, session_id);
    } else {
        snprintf(out, out_len, "%s/last.json", dir);
    }
    return 0;
}

/* Write atomically: stage to <path>.tmp.<pid>, fsync, then rename over the
   target. A crash/SIGINT mid-write can no longer leave a torn (truncated)
   session JSON that the history indexer would silently skip. */
static int atomic_write_file(const char *path, const char *data) {
    char tmp[1400];
    snprintf(tmp, sizeof(tmp), "%s.tmp.%d", path, (int)getpid());
    FILE *fp = fopen(tmp, "w");
    if (!fp) return -1;
    if (fputs(data, fp) == EOF) { fclose(fp); unlink(tmp); return -1; }
    fflush(fp);
    {
        int fd = fileno(fp);
        if (fd >= 0) fsync(fd);
    }
    if (fclose(fp) != 0) { unlink(tmp); return -1; }
    if (rename(tmp, path) != 0) { unlink(tmp); return -1; }
    return 0;
}

/* Bound the cache session dir: keep the newest INFER_SESSION_RETENTION
   session files (default 200) and unlink the older ones. Safe because the
   persistent mirror (~/.local/share/ai/sessions) + the index archive keep the
   data. Skips last.json and any in-flight .tmp files. */
typedef struct { char name[256]; long mtime; } sess_entry_t;

static int sess_entry_cmp(const void *a, const void *b) {
    const sess_entry_t *ea = (const sess_entry_t *)a;
    const sess_entry_t *eb = (const sess_entry_t *)b;
    return (eb->mtime > ea->mtime) - (eb->mtime < ea->mtime); /* newest first */
}

static void prune_cache_sessions(void) {
    const char *home = getenv("HOME");
    if (!home) return;
    const char *env = getenv("INFER_SESSION_RETENTION");
    long keep = env ? atol(env) : 200;
    if (keep <= 0) return;
    char dir[1100];
    snprintf(dir, sizeof(dir), "%s/.cache/ai/sessions", home);
    DIR *d = opendir(dir);
    if (!d) return;
    sess_entry_t *list = NULL;
    int n = 0, cap = 0;
    struct dirent *de;
    while ((de = readdir(d)) != NULL) {
        if (de->d_name[0] == '.') continue;
        if (strcmp(de->d_name, "last.json") == 0) continue;
        size_t L = strlen(de->d_name);
        if (L < 6 || strcmp(de->d_name + L - 5, ".json") != 0) continue;
        char p[1300];
        snprintf(p, sizeof(p), "%s/%s", dir, de->d_name);
        struct stat st;
        if (stat(p, &st) != 0) continue;
        if (n == cap) {
            cap = cap ? cap * 2 : 1024;
            sess_entry_t *nl = realloc(list, (size_t)cap * sizeof(sess_entry_t));
            if (!nl) break;
            list = nl;
        }
        snprintf(list[n].name, sizeof(list[n].name), "%s", de->d_name);
        list[n].mtime = (long)st.st_mtime;
        n++;
    }
    closedir(d);
    if (!list) return;
    if (n > keep) {
        qsort(list, (size_t)n, sizeof(sess_entry_t), sess_entry_cmp);
        for (int i = (int)keep; i < n; i++) {
            char p[1300];
            snprintf(p, sizeof(p), "%s/%s", dir, list[i].name);
            unlink(p);
        }
    }
    free(list);
}

void save_session(const char *messages_json) {
    char path[1200];
    if (!messages_json) return;
    if (session_file_path(path, sizeof(path), current_session_id) == 0)
        atomic_write_file(path, messages_json);
    if (backup_session_file_path(path, sizeof(path), current_session_id) == 0)
        atomic_write_file(path, messages_json);
    if (session_file_path(path, sizeof(path), NULL) == 0)
        atomic_write_file(path, messages_json);
    if (backup_session_file_path(path, sizeof(path), NULL) == 0)
        atomic_write_file(path, messages_json);
    prune_cache_sessions();
}

char* load_session_transcript(char *messages_json, const char *mcp_script) {
    char path[1200];
    int found = 0;
    if (session_file_path(path, sizeof(path), resume_session_id) == 0 &&
        access(path, R_OK) == 0) {
        found = 1;
    } else if (backup_session_file_path(path, sizeof(path), resume_session_id) == 0 &&
               access(path, R_OK) == 0) {
        /* Cache miss — the persistent mirror survives cache clears/pruning. */
        found = 1;
    }
    if (!found) {
        if (resume_session_id && *resume_session_id) {
            fprintf(stderr, "\033[2m[ai] --resume: session '%s' not found.\033[0m\n", resume_session_id);
        } else {
            fprintf(stderr, "\033[2m[ai] --resume: no previous session found.\033[0m\n");
        }
        return messages_json;
    }
    char cmd[1400];
    snprintf(cmd, sizeof(cmd), "python3 %s session-transcript %s", mcp_script, path);
    char *out = run_shell_command(cmd, NULL);
    if (!out) return messages_json;
    int appended = 0;
    char *line = out;
    while (line && *line) {
        char *nl = strchr(line, '\n');
        if (nl) *nl = '\0';
        char *p = line;
        while (*p && isspace((unsigned char)*p)) p++;
        if (*p == '{') {
            messages_json = append_message(messages_json, line);
            appended++;
        }
        if (!nl) break;
        line = nl + 1;
    }
    free(out);
    if (appended > 0)
        fprintf(stderr, "\033[2m[ai] --resume: restored %d message(s) from last session.\033[0m\n", appended);
    return messages_json;
}

char* maybe_trim_messages(char *messages_json, const char *mcp_script) {
    if (!messages_json || !mcp_script) return messages_json;
    const char *tmpdir = getenv("TMPDIR");
    if (!tmpdir || !tmpdir[0]) tmpdir = "/tmp";
    char tmpfile[256];
    snprintf(tmpfile, sizeof(tmpfile), "%s/ai_msgs_XXXXXX", tmpdir);
    int tfd = mkstemp(tmpfile);
    if (tfd < 0) return messages_json;
    FILE *fp = fdopen(tfd, "w");
    if (!fp) { close(tfd); unlink(tmpfile); return messages_json; }
    fputs(messages_json, fp);
    fclose(fp);
    char cmd[2048];
    snprintf(cmd, sizeof(cmd), "python3 %s trim-messages %s", mcp_script, tmpfile);
    char *trimmed = run_shell_command(cmd, NULL);
    unlink(tmpfile);
    if (trimmed) {
        size_t tlen = strlen(trimmed);
        while (tlen > 0 && isspace((unsigned char)trimmed[tlen-1])) {
            trimmed[tlen-1] = '\0';
            tlen--;
        }
        if (tlen > 20 && trimmed[0] == '[' && trimmed[tlen-1] == ']') {
            free(messages_json);
            return trimmed;
        }
    }
    if (trimmed) free(trimmed);
    return messages_json;
}

char* compact_session(char *messages_json, const char *mcp_script, CURL *curl_handle, const char *model_name, int *out_success) {
    (void)curl_handle;
    (void)model_name;
    if (out_success) *out_success = 0;
    if (!messages_json || !mcp_script) return messages_json;

    char *trimmed = maybe_trim_messages(messages_json, mcp_script);

    /* Task 3 Fallback Validation: ensure trimmed result is non-empty, valid JSON array */
    if (!trimmed || strlen(trimmed) < 20 || trimmed[0] != '[' || trimmed[strlen(trimmed) - 1] != ']') {
        fprintf(stderr, "\033[1;33m[ai] Compact warning: session compaction produced invalid output. Retaining original session.\033[0m\n");
        if (trimmed) free(trimmed);
        return messages_json;
    }

    if (out_success) *out_success = 1;
    return trimmed;
}
