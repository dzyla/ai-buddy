#include "ai_session.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <unistd.h>
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

void save_session(const char *messages_json) {
    char path[1200];
    if (session_file_path(path, sizeof(path), current_session_id) == 0) {
        FILE *fp = fopen(path, "w");
        if (fp) {
            fputs(messages_json, fp);
            fclose(fp);
        }
    }
    if (session_file_path(path, sizeof(path), NULL) == 0) {
        FILE *fp = fopen(path, "w");
        if (fp) {
            fputs(messages_json, fp);
            fclose(fp);
        }
    }
}

char* load_session_transcript(char *messages_json, const char *mcp_script) {
    char path[1200];
    if (session_file_path(path, sizeof(path), resume_session_id) != 0) return messages_json;
    if (access(path, R_OK) != 0) {
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
    if (trimmed && strlen(trimmed) > 20 && trimmed[0] == '[' && trimmed[strlen(trimmed)-1] == ']') {
        free(messages_json);
        return trimmed;
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
