#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <unistd.h>
#include <stdint.h>
#include <curl/curl.h>
#include "jsmn.h"

#include <strings.h>
#include <dirent.h>
#include <fcntl.h>
#include <sys/utsname.h>
#include <time.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>

#define MAX_LINE 1024
#define MAX_VAL  512

// Config globals
static char  api_url[MAX_VAL];
static char  api_key[MAX_VAL];
static char  model[MAX_VAL];
static float temperature_val  = -1.0f;
static int   max_tokens_val   = -1;
static int   context_window   = 0;    /* set via INFER_CONTEXT_WINDOW */

static const char *SYSTEM_PROMPT =
    "You are a fully autonomous CLI agent. Output in clean markdown. Follow these rules exactly:\n\n"
    "SPEED RULE (highest priority):\n"
    "- Single-step tasks (list files, read a file, run one command, check disk/memory, look up a fact you know) must complete in ONE tool call followed immediately by task_complete. Do NOT use think first. Do NOT make extra tool calls.\n"
    "- Only use think when the task genuinely requires planning across 3+ tool calls.\n\n"
    "TOOL USE:\n"
    "- For facts you already know (e.g. definitions, formulas, capitals), call task_complete directly — no tools needed.\n"
    "- Use web_search only when you need current data (prices, news, live stats) or genuinely don't know.\n"
    "- After web_search, you MUST call fetch_webpage on at least one result URL before task_complete.\n"
    "- If fetch_webpage returns noisy or incomplete data, try execute_command with a Python/curl script.\n"
    "- After writing a script with write_file, you MUST run it with execute_command to verify it works.\n"
    "- NEVER describe what the user can do themselves. If a tool can get the answer, use it.\n\n"
    "CITATIONS:\n"
    "- fetch_webpage and read_file (PDF) results begin with a [Source: ...] line. Track every source whose content you use.\n"
    "- In your task_complete summary, always end with a '## Sources' section listing each [Source: ...] URL or file path you drew from.\n"
    "- Do not list sources you fetched but did not use in the answer.\n\n"
    "FAILURE RECOVERY:\n"
    "- If execute_command fails, read the error, fix the root cause, and retry. At least 3 attempts before giving up.\n"
    "- If a library is missing, install it with pip/apt. If a web source is blocked or noisy, find an alternative.\n"
    "- Never tell the user to 'visit a link' or 'run a command themselves' — do it yourself.\n\n"
    "DELEGATION:\n"
    "- For tasks with independent parallel sub-tasks, use delegate_task to run them concurrently.\n"
    "- delegate_task agents have full tool access. Give them specific, self-contained instructions.";

static char* get_system_context() {
    char cwd[1024] = "Unknown";
    if (!getcwd(cwd, sizeof(cwd))) {
        strcpy(cwd, "Unknown");
    }

    struct utsname uts;
    char os_info[512] = "Unknown OS";
    if (uname(&uts) == 0) {
        snprintf(os_info, sizeof(os_info), "%s %s %s", uts.sysname, uts.release, uts.machine);
    }

    const char *user = getenv("USER");
    if (!user) user = getenv("LOGNAME");
    if (!user) user = "unknown";

    const char *shell = getenv("SHELL");
    if (!shell) shell = "unknown";

    time_t t = time(NULL);
    struct tm *tmp = localtime(&t);
    char time_str[64] = "Unknown time";
    if (tmp) {
        strftime(time_str, sizeof(time_str), "%Y-%m-%d %H:%M:%S %Z", tmp);
    }

    char *buf = malloc(4096);
    snprintf(buf, 4096,
             "Host System Context:\n"
             "- Operating System: %s\n"
             "- Current Working Directory: %s\n"
             "- User: %s\n"
             "- Shell: %s\n"
             "- Local Time: %s\n",
             os_info, cwd, user, shell, time_str);
    return buf;
}

static char* json_escape(const char *src);

static void log_job(const char *prompt, const char *pipe_writer, const char *response, int interactive) {
    char *home = getenv("HOME");
    if (!home) return;
    char dir_path[1024];
    snprintf(dir_path, sizeof(dir_path), "%s/.cache/ai", home);
    
    mkdir(dir_path, 0755);
    
    char file_path[2048];
    snprintf(file_path, sizeof(file_path), "%s/history.jsonl", dir_path);
    
    FILE *fp = fopen(file_path, "a");
    if (!fp) return;
    
    time_t t = time(NULL);
    struct tm *tmp = localtime(&t);
    char time_str[64] = "Unknown time";
    if (tmp) {
        strftime(time_str, sizeof(time_str), "%Y-%m-%d %H:%M:%S %Z", tmp);
    }
    
    char *esc_prompt = json_escape(prompt ? prompt : "");
    char *esc_writer = json_escape(pipe_writer ? pipe_writer : "");
    char *esc_resp = json_escape(response ? response : "");
    
    fprintf(fp, "{\"timestamp\":\"%s\",\"prompt\":\"%s\",\"pipe_writer\":\"%s\",\"interactive\":%s,\"response\":\"%s\"}\n",
            time_str, esc_prompt, esc_writer, interactive ? "true" : "false", esc_resp);
    
    free(esc_prompt);
    free(esc_writer);
    free(esc_resp);
    fclose(fp);
}

static char* load_skills_from_dir(const char *base_dir) {
    DIR *dir = opendir(base_dir);
    if (!dir) return NULL;
    
    struct dirent *entry;
    size_t cap = 4096;
    size_t len = 0;
    char *buf = malloc(cap);
    buf[0] = '\0';
    
    while ((entry = readdir(dir)) != NULL) {
        if (entry->d_name[0] == '.') continue;
        
        char skill_path[1024];
        snprintf(skill_path, sizeof(skill_path), "%s/%s/SKILL.md", base_dir, entry->d_name);
        
        FILE *fp = fopen(skill_path, "r");
        if (fp) {
            fseek(fp, 0, SEEK_END);
            long size = ftell(fp);
            fseek(fp, 0, SEEK_SET);
            if (size > 0) {
                char *file_buf = malloc(size + 1);
                size_t read_bytes = fread(file_buf, 1, size, fp);
                file_buf[read_bytes] = '\0';
                
                if (len + read_bytes + 256 >= cap) {
                    cap = cap * 2 + read_bytes + 256;
                    buf = realloc(buf, cap);
                }
                
                len += sprintf(buf + len, "\n\nSkill [%s]:\n%s", entry->d_name, file_buf);
                free(file_buf);
            }
            fclose(fp);
        }
    }
    closedir(dir);
    return buf;
}

static char* load_all_skills() {
    char *global_skills = NULL;
    char *home = getenv("HOME");
    if (home) {
        char global_path[1024];
        snprintf(global_path, sizeof(global_path), "%s/.config/ai/skills", home);
        global_skills = load_skills_from_dir(global_path);
    }
    
    char *local_skills = load_skills_from_dir("./.agents/skills");
    
    size_t total_len = (global_skills ? strlen(global_skills) : 0) + (local_skills ? strlen(local_skills) : 0) + 1;
    char *res = malloc(total_len);
    res[0] = '\0';
    
    if (global_skills) {
        strcat(res, global_skills);
        free(global_skills);
    }
    if (local_skills) {
        strcat(res, local_skills);
        free(local_skills);
    }
    return res;
}

/* ---------------- HELPERS ---------------- */

// Minimal JSON string escaper (handles ", \, and newline)
// Returns a new allocated string you must free.
static char* json_escape(const char *src) {
    if (!src) return calloc(1, 1);
    char *dest = malloc(strlen(src) * 6 + 1);
    char *p = dest;
    while (*src) {
        unsigned char c = *src;
        if (c == '"') { *p++ = '\\'; *p++ = '"'; }
        else if (c == '\\') { *p++ = '\\'; *p++ = '\\'; }
        else if (c == '\n') { *p++ = '\\'; *p++ = 'n'; }
        else if (c == '\r') { *p++ = '\\'; *p++ = 'r'; }
        else if (c == '\t') { *p++ = '\\'; *p++ = 't'; }
        else if (c == '\b') { *p++ = '\\'; *p++ = 'b'; }
        else if (c == '\f') { *p++ = '\\'; *p++ = 'f'; }
        else if (c < 0x20) {
            sprintf(p, "\\u%04x", c);
            p += 6;
        } else {
            *p++ = *src;
        }
        src++;
    }
    *p = 0;
    return dest;
}

// Finds the command line of the process writing to our stdin pipe, if any
static char* find_pipe_writer() {
    if (isatty(STDIN_FILENO)) return NULL;

    char pipe_target[256];
    ssize_t r = readlink("/proc/self/fd/0", pipe_target, sizeof(pipe_target) - 1);
    if (r <= 0) return NULL;
    pipe_target[r] = '\0';

    if (strncmp(pipe_target, "pipe:[", 6) != 0) return NULL;

    pid_t my_pid = getpid();
    DIR *proc_dir = opendir("/proc");
    if (!proc_dir) return NULL;

    struct dirent *proc_entry;
    char *cmdline_res = NULL;

    while ((proc_entry = readdir(proc_dir)) != NULL) {
        // Check if directory name is numeric
        char *endptr;
        long pid = strtol(proc_entry->d_name, &endptr, 10);
        if (*endptr != '\0' || pid == my_pid) continue;

        char fd_dir_path[512];
        snprintf(fd_dir_path, sizeof(fd_dir_path), "/proc/%ld/fd", pid);
        DIR *fd_dir = opendir(fd_dir_path);
        if (!fd_dir) continue;

        struct dirent *fd_entry;
        int found_match = 0;
        while ((fd_entry = readdir(fd_dir)) != NULL) {
            if (fd_entry->d_name[0] == '.') continue;

            char fd_link_path[1024];
            snprintf(fd_link_path, sizeof(fd_link_path), "/proc/%ld/fd/%s", pid, fd_entry->d_name);

            char fd_target[256];
            ssize_t lr = readlink(fd_link_path, fd_target, sizeof(fd_target) - 1);
            if (lr > 0) {
                fd_target[lr] = '\0';
                if (strcmp(fd_target, pipe_target) == 0) {
                    found_match = 1;
                    break;
                }
            }
        }
        closedir(fd_dir);

        if (found_match) {
            char cmd_path[512];
            snprintf(cmd_path, sizeof(cmd_path), "/proc/%ld/cmdline", pid);
            int fd = open(cmd_path, O_RDONLY);
            if (fd >= 0) {
                char buf[4096];
                ssize_t bytes = read(fd, buf, sizeof(buf) - 1);
                close(fd);
                if (bytes > 0) {
                    buf[bytes] = '\0';
                    // Reconstruct command line by replacing null bytes with spaces
                    for (ssize_t i = 0; i < bytes - 1; i++) {
                        if (buf[i] == '\0') {
                            buf[i] = ' ';
                        }
                    }
                    // Trim trailing spaces / nulls
                    while (bytes > 0 && (buf[bytes - 1] == '\0' || buf[bytes - 1] == ' ' || buf[bytes - 1] == '\n')) {
                        buf[bytes - 1] = '\0';
                        bytes--;
                    }
                    if (strlen(buf) > 0) {
                        cmdline_res = strdup(buf);
                    }
                }
            }
            break; // Found the writer
        }
    }
    closedir(proc_dir);
    return cmdline_res;
}

// Reads stdin into a dynamically allocated string
static char* read_stdin() {
    if (isatty(fileno(stdin))) return NULL; // No pipe detected

    size_t size = 4096, len = 0;
    char *buf = malloc(size);
    int c;
    while ((c = getchar()) != EOF) {
        buf[len++] = c;
        if (len >= size - 1) {
            size *= 2;
            buf = realloc(buf, size);
        }
    }
    buf[len] = 0;
    return buf;
}

static int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return 10 + (c - 'a');
    if (c >= 'A' && c <= 'F') return 10 + (c - 'A');
    return -1;
}

static void emit_utf8(uint32_t cp) {
    if (cp <= 0x7F) {
        fputc((int)cp, stdout);
    } else if (cp <= 0x7FF) {
        fputc(0xC0 | ((cp >> 6) & 0x1F), stdout);
        fputc(0x80 | (cp & 0x3F), stdout);
    } else if (cp <= 0xFFFF) {
        fputc(0xE0 | ((cp >> 12) & 0x0F), stdout);
        fputc(0x80 | ((cp >> 6) & 0x3F), stdout);
        fputc(0x80 | (cp & 0x3F), stdout);
    } else if (cp <= 0x10FFFF) {
        fputc(0xF0 | ((cp >> 18) & 0x07), stdout);
        fputc(0x80 | ((cp >> 12) & 0x3F), stdout);
        fputc(0x80 | ((cp >> 6) & 0x3F), stdout);
        fputc(0x80 | (cp & 0x3F), stdout);
    } else {
        fputc('?', stdout);
    }
}

static void print_json_string_unescaped(const char *s, int len) {
    int i = 0;
    while (i < len) {
        char c = s[i++];
        if (c != '\\') {
            fputc(c, stdout);
            continue;
        }
        if (i >= len) { fputc('\\', stdout); break; }
        char esc = s[i++];
        switch (esc) {
            case 'n': fputc('\n', stdout); break;
            case 'r': fputc('\r', stdout); break;
            case 't': fputc('\t', stdout); break;
            case 'b': fputc('\b', stdout); break;
            case 'f': fputc('\f', stdout); break;
            case '"': fputc('"', stdout); break;
            case '\\': fputc('\\', stdout); break;
            case '/': fputc('/', stdout); break;
            case 'u': {
                if (i + 4 > len) { fputc('?', stdout); break; }
                uint32_t cp = 0;
                for (int k = 0; k < 4; k++) {
                    int hv = hexval(s[i + k]);
                    if (hv < 0) { cp = 0xFFFD; break; }
                    cp = (cp << 4) | (uint32_t)hv;
                }
                i += 4;

                if (cp >= 0xD800 && cp <= 0xDBFF) {
                    if (i + 6 <= len && s[i] == '\\' && s[i + 1] == 'u') {
                        uint32_t low = 0;
                        int ok = 1;
                        for (int k = 0; k < 4; k++) {
                            int hv = hexval(s[i + 2 + k]);
                            if (hv < 0) { ok = 0; break; }
                            low = (low << 4) | (uint32_t)hv;
                        }
                        if (ok && low >= 0xDC00 && low <= 0xDFFF) {
                            i += 6;
                            cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00);
                        }
                    }
                }
                emit_utf8(cp);
                break;
            }
            default:
                fputc(esc, stdout);
                break;
        }
    }
}

/* ---------------- YAML PARSER (REMOVED) ---------------- */

/* ---------------- HTTP RESPONSE HANDLING ---------------- */

struct response {
    char *data;
    size_t size;
};

static size_t write_cb(void *ptr, size_t size, size_t nmemb, void *userdata) {
    size_t realsize = size * nmemb;
    struct response *mem = (struct response *)userdata;
    
    char *ptr_realloc = realloc(mem->data, mem->size + realsize + 1);
    if(!ptr_realloc) return 0; // Out of memory

    mem->data = ptr_realloc;
    memcpy(&(mem->data[mem->size]), ptr, realsize);
    mem->size += realsize;
    mem->data[mem->size] = 0;
    return realsize;
}

/* ---------------- MAIN ---------------- */
static char* unescape_json_string(const char *s, int len) {
    char *dest = malloc(len + 1);
    int i = 0;
    int d = 0;
    while (i < len) {
        char c = s[i++];
        if (c != '\\') {
            dest[d++] = c;
            continue;
        }
        if (i >= len) { dest[d++] = '\\'; break; }
        char esc = s[i++];
        switch (esc) {
            case 'n': dest[d++] = '\n'; break;
            case 'r': dest[d++] = '\r'; break;
            case 't': dest[d++] = '\t'; break;
            case 'b': dest[d++] = '\b'; break;
            case 'f': dest[d++] = '\f'; break;
            case '"': dest[d++] = '"'; break;
            case '\\': dest[d++] = '\\'; break;
            case '/': dest[d++] = '/'; break;
            case 'u': {
                if (i + 4 <= len) {
                    uint32_t cp = 0;
                    for (int k = 0; k < 4; k++) {
                        int hv = hexval(s[i + k]);
                        if (hv < 0) { cp = 0; break; }
                        cp = (cp << 4) | (uint32_t)hv;
                    }
                    i += 4;
                    if (cp > 0 && cp <= 0x7F) {
                        dest[d++] = (char)cp;
                    } else {
                        dest[d++] = '\\';
                        dest[d++] = 'u';
                        for (int k = 0; k < 4; k++) dest[d++] = s[i - 4 + k];
                    }
                } else {
                    dest[d++] = '\\';
                    dest[d++] = 'u';
                }
                break;
            }
            default:
                dest[d++] = esc;
                break;
        }
    }
    dest[d] = '\0';
    return dest;
}

static char* shell_escape(const char *src) {
    size_t len = strlen(src);
    char *dest = malloc(len * 4 + 3);
    char *p = dest;
    *p++ = '\'';
    while (*src) {
        if (*src == '\'') {
            strcpy(p, "'\\''");
            p += 4;
        } else {
            *p++ = *src;
        }
        src++;
    }
    *p++ = '\'';
    *p = '\0';
    return dest;
}

static char* run_shell_command(const char *cmd, int *exit_status) {
    FILE *fp = popen(cmd, "r");
    if (!fp) {
        if (exit_status) *exit_status = -1;
        return strdup("Error: failed to run command");
    }
    
    size_t size = 4096;
    size_t len = 0;
    char *buf = malloc(size);
    if (!buf) {
        pclose(fp);
        if (exit_status) *exit_status = -1;
        return NULL;
    }
    buf[0] = '\0';
    
    char tmp[1024];
    while (fgets(tmp, sizeof(tmp), fp) != NULL) {
        size_t tmp_len = strlen(tmp);
        if (len + tmp_len >= size - 1) {
            size *= 2;
            char *new_buf = realloc(buf, size);
            if (!new_buf) {
                free(buf);
                pclose(fp);
                if (exit_status) *exit_status = -1;
                return NULL;
            }
            buf = new_buf;
        }
        strcpy(buf + len, tmp);
        len += tmp_len;
    }
    
    int status = pclose(fp);
    if (exit_status) {
        if (status == -1) {
            *exit_status = -1;
        } else {
            *exit_status = WIFEXITED(status) ? WEXITSTATUS(status) : status;
        }
    }
    return buf;
}

/* Extract the string value of a key from a flat JSON object string. */
static char* json_get_string(const char *json_str, const char *key) {
    jsmn_parser p;
    jsmntok_t tok[64];
    jsmn_init(&p);
    int r = jsmn_parse(&p, json_str, strlen(json_str), tok, 64);
    if (r < 0) return NULL;
    int klen = (int)strlen(key);
    for (int i = 1; i < r - 1; i++) {
        if (tok[i].type == JSMN_STRING &&
            tok[i].end - tok[i].start == klen &&
            strncmp(json_str + tok[i].start, key, klen) == 0 &&
            tok[i+1].type == JSMN_STRING) {
            return unescape_json_string(json_str + tok[i+1].start,
                                        tok[i+1].end - tok[i+1].start);
        }
    }
    return NULL;
}

static int json_skip_token(jsmntok_t *tokens, int r, int start_idx) {
    if (start_idx >= r) return r;
    int end = tokens[start_idx - 1].end;
    int i = start_idx;
    while (i < r && tokens[i].start < end) {
        i++;
    }
    return i;
}

static char* append_message(char *messages_json, const char *msg_to_append) {
    size_t orig_len = strlen(messages_json);
    if (orig_len < 2) return messages_json;
    
    size_t append_len = strlen(msg_to_append);
    size_t new_size = orig_len + append_len + 5;
    char *new_buf = realloc(messages_json, new_size);
    if (!new_buf) return NULL;
    
    char *p = new_buf + orig_len - 1;
    while (p > new_buf && *p != ']') p--;
    
    if (p == new_buf) {
        free(new_buf);
        return NULL;
    }
    
    int has_elements = 0;
    char *q = new_buf + 1;
    while (q < p) {
        if (!isspace((unsigned char)*q)) {
            has_elements = 1;
            break;
        }
        q++;
    }
    
    if (has_elements) {
        *p = ',';
        p++;
    }
    
    strcpy(p, msg_to_append);
    strcat(p, "]");
    
    return new_buf;
}

static const char b64chars[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static char* base64_encode(const unsigned char *data, size_t input_length) {
    size_t output_length = 4 * ((input_length + 2) / 3);
    char *encoded_data = malloc(output_length + 1);
    if (!encoded_data) return NULL;
    
    for (size_t i = 0, j = 0; i < input_length;) {
        uint32_t octet_a = i < input_length ? data[i++] : 0;
        uint32_t octet_b = i < input_length ? data[i++] : 0;
        uint32_t octet_c = i < input_length ? data[i++] : 0;
        
        uint32_t triple = (octet_a << 16) + (octet_b << 8) + octet_c;
        
        encoded_data[j++] = b64chars[(triple >> 18) & 0x3F];
        encoded_data[j++] = b64chars[(triple >> 12) & 0x3F];
        encoded_data[j++] = i > input_length + 1 ? '=' : b64chars[(triple >> 6) & 0x3F];
        encoded_data[j++] = i > input_length ? '=' : b64chars[triple & 0x3F];
    }
    encoded_data[output_length] = '\0';
    return encoded_data;
}

static int is_image_file(const char *path) {
    const char *ext = strrchr(path, '.');
    if (!ext) return 0;
    if (strcasecmp(ext, ".png") == 0 ||
        strcasecmp(ext, ".jpg") == 0 ||
        strcasecmp(ext, ".jpeg") == 0 ||
        strcasecmp(ext, ".webp") == 0) {
        return access(path, F_OK) == 0;
    }
    return 0;
}

static char* read_image_base64(const char *path, const char **mime_type) {
    FILE *fp = fopen(path, "rb");
    if (!fp) return NULL;
    
    fseek(fp, 0, SEEK_END);
    long size = ftell(fp);
    fseek(fp, 0, SEEK_SET);
    
    if (size <= 0) {
        fclose(fp);
        return NULL;
    }
    
    unsigned char *buf = malloc(size);
    if (!buf) {
        fclose(fp);
        return NULL;
    }
    
    size_t read_bytes = fread(buf, 1, size, fp);
    fclose(fp);
    
    char *b64 = base64_encode(buf, read_bytes);
    free(buf);
    
    const char *ext = strrchr(path, '.');
    if (strcasecmp(ext, ".png") == 0) *mime_type = "image/png";
    else if (strcasecmp(ext, ".webp") == 0) *mime_type = "image/webp";
    else *mime_type = "image/jpeg";
    
    return b64;
}

static char* read_memory_file() {
    char *home = getenv("HOME");
    if (!home) return NULL;
    char path[1024];
    snprintf(path, sizeof(path), "%s/.config/ai/memory.txt", home);
    
    FILE *fp = fopen(path, "r");
    if (!fp) return NULL;
    
    fseek(fp, 0, SEEK_END);
    long size = ftell(fp);
    fseek(fp, 0, SEEK_SET);
    
    if (size <= 0) {
        fclose(fp);
        return NULL;
    }
    
    if (size > 4096) size = 4096;
    
    char *buf = malloc(size + 1);
    if (!buf) {
        fclose(fp);
        return NULL;
    }
    
    size_t read_bytes = fread(buf, 1, size, fp);
    buf[read_bytes] = '\0';
    fclose(fp);
    return buf;
}

/* Write messages_json to a temp file, ask Python to trim it, return trimmed version.
   Keeps: messages[0] (system), messages[1] (first user), last 20 messages. */
static char* maybe_trim_messages(char *messages_json, const char *mcp_script) {
    if (strlen(messages_json) <= 35000) return messages_json;
    char tmpfile[128];
    snprintf(tmpfile, sizeof(tmpfile), "/tmp/ai_msgs_%d.json", (int)getpid());
    FILE *fp = fopen(tmpfile, "w");
    if (!fp) return messages_json;
    fputs(messages_json, fp);
    fclose(fp);
    char cmd[512];
    snprintf(cmd, sizeof(cmd), "python3 %s trim-messages %s", mcp_script, tmpfile);
    char *trimmed = run_shell_command(cmd, NULL);
    unlink(tmpfile);
    if (trimmed && strlen(trimmed) > 5 && trimmed[0] == '[') {
        free(messages_json);
        return trimmed;
    }
    if (trimmed) free(trimmed);
    return messages_json;
}

int main(int argc, char **argv) {
    int is_stdin_tty = isatty(STDIN_FILENO);
    int interactive_mode = 0;
    int auto_approve = 0;
    int quiet_mode = 0;

    // Parse help flags first
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            printf("Usage: ai [options] [\"prompt\"] [path/to/image.png]\n\n");
            printf("A minimal, agentic CLI tool for piping anything into an LLM and executing terminal work.\n\n");
            printf("Options:\n");
            printf("  -i, --interactive    Start an interactive multi-turn chat session.\n");
            printf("  -y, --yes            Auto-approve all command execution requests without prompting.\n");
            printf("  -q, --quiet          Suppress think tool reasoning output.\n");
            printf("  -h, --help           Display this help screen.\n\n");
            printf("Examples:\n");
            printf("  ai \"what's the tar command to extract .tar.gz?\"\n");
            printf("  ps aux | head -n 20 | ai \"what's eating memory?\"\n");
            printf("  ai -i \"let's look at this project\"\n");
            printf("  ai -y \"backup ~/.bashrc\"\n");
            return 0;
        }
    }

    // Load from Environment Variables
    char *env_url = getenv("INFER_BASE_URL");
    char *env_key = getenv("INFER_API_KEY");
    char *env_model = getenv("INFER_MODEL");

    if (!env_url || !*env_url || !env_key || !*env_key || !env_model || !*env_model) {
        fprintf(stderr, "Error: missing required environment variables.\n");
        if (!env_url || !*env_url) fprintf(stderr, "Please set INFER_BASE_URL environment variable.\n");
        if (!env_key || !*env_key) fprintf(stderr, "Please set INFER_API_KEY environment variable.\n");
        if (!env_model || !*env_model) fprintf(stderr, "Please set INFER_MODEL environment variable.\n");
        return 1;
    }

    // Parse flags
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-i") == 0 || strcmp(argv[i], "--interactive") == 0) {
            interactive_mode = 1;
        }
        if (strcmp(argv[i], "-y") == 0 || strcmp(argv[i], "--yes") == 0) {
            auto_approve = 1;
        }
        if (strcmp(argv[i], "-q") == 0 || strcmp(argv[i], "--quiet") == 0) {
            quiet_mode = 1;
        }
    }

    char *env_approve = getenv("INFER_AUTO_APPROVE");
    if (env_approve && (strcmp(env_approve, "1") == 0 || strcasecmp(env_approve, "true") == 0)) {
        auto_approve = 1;
    }

    char *env_quiet = getenv("INFER_QUIET");
    if (env_quiet && (strcmp(env_quiet, "1") == 0 || strcasecmp(env_quiet, "true") == 0)) {
        quiet_mode = 1;
    }

    char *env_temp = getenv("INFER_TEMPERATURE");
    if (env_temp && *env_temp) temperature_val = (float)atof(env_temp);
    char *env_maxtok = getenv("INFER_MAX_TOKENS");
    if (env_maxtok && *env_maxtok) max_tokens_val = atoi(env_maxtok);
    char *env_ctxwin = getenv("INFER_CONTEXT_WINDOW");
    if (env_ctxwin && *env_ctxwin) context_window = atoi(env_ctxwin);

    if (argc < 2 && is_stdin_tty) {
        interactive_mode = 1;
    }

    const char *path = "chat/completions";
    size_t base_len = strlen(env_url);
    int needs_slash = base_len > 0 && env_url[base_len - 1] != '/';
    snprintf(api_url, sizeof(api_url), "%s%s%s", env_url, needs_slash ? "/" : "", path);
    snprintf(api_key, sizeof(api_key), "%s", env_key);
    snprintf(model, sizeof(model), "%s", env_model);

    // Get tools JSON from python script
    const char *mcp_script = "./ai_mcp.py";
    if (access(mcp_script, F_OK) != 0) {
        mcp_script = "/usr/local/bin/ai_mcp.py";
    }
    
    char tools_cmd[1024];
    snprintf(tools_cmd, sizeof(tools_cmd), "python3 %s list-tools", mcp_script);
    char *tools_json = run_shell_command(tools_cmd, NULL);
    if (tools_json && (strncmp(tools_json, "Error", 5) == 0 || strlen(tools_json) < 5)) {
        free(tools_json);
        tools_json = NULL;
    }

    // 1. Prepare Inputs
    char *pipe_writer = find_pipe_writer();
    char *pipe_in = read_stdin();

    if (interactive_mode && !is_stdin_tty) {
        if (!freopen("/dev/tty", "r", stdin)) {
            // Failed to reopen /dev/tty, disable interactive if prompt is empty
            int has_prompt_args = 0;
            for (int i = 1; i < argc; i++) {
                if (strcmp(argv[i], "-i") == 0 || strcmp(argv[i], "--interactive") == 0) continue;
                if (strcmp(argv[i], "-y") == 0 || strcmp(argv[i], "--yes") == 0) continue;
                if (strcmp(argv[i], "-q") == 0 || strcmp(argv[i], "--quiet") == 0) continue;
                has_prompt_args = 1;
                break;
            }
            if (!has_prompt_args && (!pipe_in || strlen(pipe_in) == 0)) {
                fprintf(stderr, "Error: cannot start interactive mode because stdin is not a terminal and /dev/tty cannot be opened.\n");
                if (pipe_in) free(pipe_in);
                if (pipe_writer) free(pipe_writer);
                if (tools_json) free(tools_json);
                return 1;
            }
            interactive_mode = 0;
        }
    }

    // Check if any argument is an image file
    char *image_path = NULL;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-i") == 0 || strcmp(argv[i], "--interactive") == 0) continue;
        if (strcmp(argv[i], "-y") == 0 || strcmp(argv[i], "--yes") == 0) continue;
        if (strcmp(argv[i], "-q") == 0 || strcmp(argv[i], "--quiet") == 0) continue;
        if (is_image_file(argv[i])) {
            image_path = argv[i];
            break;
        }
    }

    size_t prompt_len = 0;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-i") == 0 || strcmp(argv[i], "--interactive") == 0) continue;
        if (strcmp(argv[i], "-y") == 0 || strcmp(argv[i], "--yes") == 0) continue;
        if (strcmp(argv[i], "-q") == 0 || strcmp(argv[i], "--quiet") == 0) continue;
        if (image_path && strcmp(argv[i], image_path) == 0) continue;
        prompt_len += strlen(argv[i]) + 1;
    }

    char *prompt = malloc(prompt_len + 1);
    prompt[0] = '\0';
    int added = 0;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-i") == 0 || strcmp(argv[i], "--interactive") == 0) continue;
        if (strcmp(argv[i], "-y") == 0 || strcmp(argv[i], "--yes") == 0) continue;
        if (strcmp(argv[i], "-q") == 0 || strcmp(argv[i], "--quiet") == 0) continue;
        if (image_path && strcmp(argv[i], image_path) == 0) continue;
        if (added) strcat(prompt, " ");
        strcat(prompt, argv[i]);
        added = 1;
    }

    // Handle empty prompt case
    if (strlen(prompt) == 0) {
        if (pipe_in && strlen(pipe_in) > 0) {
            free(prompt);
            prompt = strdup("Answer or help with the following:");
        } else if (!interactive_mode) {
            fprintf(stderr, "Usage: %s [-i|--interactive] [-y|--yes] [\"prompt\"] [path/to/image.png]\n", argv[0]);
            free(prompt);
            if (pipe_in) free(pipe_in);
            if (pipe_writer) free(pipe_writer);
            if (tools_json) free(tools_json);
            return 1;
        }
    }

    // Initialize messages JSON array
    char *messages_json = malloc(4096);
    strcpy(messages_json, "[]");

    // Add System Prompt, Context, Memory & Skills
    char *memory = read_memory_file();
    char *sys_ctx = get_system_context();
    char *skills = load_all_skills();

    char *safe_system = json_escape(SYSTEM_PROMPT);
    char *safe_ctx = json_escape(sys_ctx);
    char *safe_skills = skills ? json_escape(skills) : strdup("");
    char *sys_msg = NULL;

    size_t mlen = strlen(safe_system) + strlen(safe_ctx) + strlen(safe_skills) + (memory ? strlen(memory) * 6 : 0) + 512;
    sys_msg = malloc(mlen);

    char *safe_mem = memory ? json_escape(memory) : NULL;

    if (safe_mem && strlen(safe_mem) > 0 && strlen(safe_skills) > 0) {
        sprintf(sys_msg, "{\"role\":\"system\",\"content\":\"%s\\n\\n%s\\n\\nSkills/Guidelines:\\n%s\\n\\nPersistent Memory/Preferences:\\n%s\"}", 
                safe_system, safe_ctx, safe_skills, safe_mem);
    } else if (safe_mem && strlen(safe_mem) > 0) {
        sprintf(sys_msg, "{\"role\":\"system\",\"content\":\"%s\\n\\n%s\\n\\nPersistent Memory/Preferences:\\n%s\"}", 
                safe_system, safe_ctx, safe_mem);
    } else if (strlen(safe_skills) > 0) {
        sprintf(sys_msg, "{\"role\":\"system\",\"content\":\"%s\\n\\n%s\\n\\nSkills/Guidelines:\\n%s\"}", 
                safe_system, safe_ctx, safe_skills);
    } else {
        sprintf(sys_msg, "{\"role\":\"system\",\"content\":\"%s\\n\\n%s\"}", 
                safe_system, safe_ctx);
    }

    messages_json = append_message(messages_json, sys_msg);

    if (safe_mem) free(safe_mem);
    if (memory) free(memory);
    free(skills);
    free(sys_ctx);
    free(safe_system);
    free(safe_ctx);
    free(safe_skills);
    free(sys_msg);

    // Add User Prompt
    char *safe_prompt = json_escape(prompt);
    char *user_content = NULL;
    if (pipe_in && strlen(pipe_in) > 0) {
        char *safe_pipe = json_escape(pipe_in);
        if (pipe_writer) {
            char *safe_writer = json_escape(pipe_writer);
            size_t len = strlen(safe_prompt) + strlen(safe_pipe) + strlen(safe_writer) + 256;
            user_content = malloc(len);
            sprintf(user_content, "%s\\n\\nContext (output of command `%s`):\\n%s", safe_prompt, safe_writer, safe_pipe);
            free(safe_writer);
        } else {
            size_t len = strlen(safe_prompt) + strlen(safe_pipe) + 128;
            user_content = malloc(len);
            sprintf(user_content, "%s\\n\\nContext:\\n%s", safe_prompt, safe_pipe);
        }
        free(safe_pipe);
    } else if (pipe_writer) {
        // Print warning to stderr so the user knows the tool is intercepting and handling the empty pipe
        fprintf(stderr, "[ai] Warning: Piped command '%s' returned no stdout. The agent will execute it to inspect stderr.\n", pipe_writer);
        char *safe_writer = json_escape(pipe_writer);
        size_t len = strlen(safe_prompt) + strlen(safe_writer) + 512;
        user_content = malloc(len);
        sprintf(user_content, "%s\\n\\nContext:\\nThe user ran the command `%s` in their terminal, but its stdout was empty. It might have failed and written to stderr. You should run this command using the execute_command tool to inspect its output/errors.", safe_prompt, safe_writer);
        free(safe_writer);
    } else {
        user_content = strdup(safe_prompt);
    }

    char *user_msg = NULL;
    if (image_path) {
        const char *mime_type = NULL;
        char *b64 = read_image_base64(image_path, &mime_type);
        if (b64) {
            size_t msg_len = strlen(user_content) + strlen(b64) + strlen(mime_type) + 512;
            user_msg = malloc(msg_len);
            sprintf(user_msg, "{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"%s\"},{\"type\":\"image_url\",\"image_url\":{\"url\":\"data:%s;base64,%s\"}}]}",
                    user_content, mime_type, b64);
            free(b64);
        }
    }

    if (!user_msg) {
        user_msg = malloc(strlen(user_content) + 128);
        sprintf(user_msg, "{\"role\":\"user\",\"content\":\"%s\"}", user_content);
    }

    int run_query_this_turn = 0;
    if (strlen(prompt) > 0 || (pipe_in && strlen(pipe_in) > 0) || pipe_writer) {
        messages_json = append_message(messages_json, user_msg);
        run_query_this_turn = 1;
    }
    free(safe_prompt);
    free(user_content); free(user_msg);

    // 2. Setup Curl
    CURL *c = curl_easy_init();
    struct curl_slist *h = NULL;
    char auth[1024]; snprintf(auth, sizeof(auth), "Authorization: Bearer %s", api_key);
    h = curl_slist_append(h, "Content-Type: application/json");
    h = curl_slist_append(h, auth);

    curl_easy_setopt(c, CURLOPT_URL, api_url);
    curl_easy_setopt(c, CURLOPT_HTTPHEADER, h);
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, write_cb);

    int debug_mode = getenv("INFER_DEBUG") != NULL;
    int keep_going = 1;
    int first_turn = 1;
    char *current_prompt = strdup(prompt ? prompt : "");

    if (interactive_mode && !run_query_this_turn) {
        printf("\033[1;35m::: ai Agent (local gemma4) interactive mode :::\033[0m\n");
        printf("Type \033[33mexit\033[0m, \033[33mquit\033[0m, or press \033[33mCtrl+D\033[0m to leave.\n\n");
    }

    while (keep_going) {
        if (interactive_mode && (!run_query_this_turn || !first_turn)) {
            printf("\n\033[1;32mai>\033[0m ");
            fflush(stdout);
            
            char user_input[4096];
            if (!fgets(user_input, sizeof(user_input), stdin)) {
                printf("\n");
                break;
            }
            
            // Trim newline
            size_t len = strlen(user_input);
            while (len > 0 && (user_input[len - 1] == '\n' || user_input[len - 1] == '\r')) {
                user_input[len - 1] = '\0';
                len--;
            }
            
            if (strcmp(user_input, "exit") == 0 || strcmp(user_input, "quit") == 0) {
                break;
            }
            
            if (len == 0) {
                continue;
            }
            
            char *safe_input = json_escape(user_input);
            char *user_msg_str = malloc(strlen(safe_input) + 128);
            sprintf(user_msg_str, "{\"role\":\"user\",\"content\":\"%s\"}", safe_input);
            messages_json = append_message(messages_json, user_msg_str);
            free(safe_input);
            free(user_msg_str);
            
            if (current_prompt) free(current_prompt);
            current_prompt = strdup(user_input);
            
            run_query_this_turn = 1;
        }
        
        first_turn = 0;
        
        if (run_query_this_turn) {
            int loop_count = 0;
            int has_more = 1;
            
            while (has_more && loop_count < 30) {
                loop_count++;
                
                messages_json = maybe_trim_messages(messages_json, mcp_script);

                /* Build optional parameter fields */
                char opt_fields[128] = "";
                int opt_len = 0;
                if (temperature_val >= 0.0f)
                    opt_len += snprintf(opt_fields + opt_len, (int)sizeof(opt_fields) - opt_len,
                                        ",\"temperature\":%.2f", temperature_val);
                if (max_tokens_val > 0)
                    opt_len += snprintf(opt_fields + opt_len, (int)sizeof(opt_fields) - opt_len,
                                        ",\"max_tokens\":%d", max_tokens_val);

                char *payload = NULL;
                size_t plen = strlen(model) + strlen(messages_json) + (tools_json ? strlen(tools_json) : 0) + 512;
                payload = malloc(plen);
                if (tools_json && strlen(tools_json) > 10) {
                    snprintf(payload, plen, "{\"model\":\"%s\",\"stream\":false%s,\"messages\":%s,\"tools\":%s,\"tool_choice\":\"auto\"}",
                             model, opt_fields, messages_json, tools_json);
                } else {
                    snprintf(payload, plen, "{\"model\":\"%s\",\"stream\":false%s,\"messages\":%s}",
                             model, opt_fields, messages_json);
                }

                if (debug_mode) {
                    fprintf(stderr, "[debug] Loop %d payload: %s\n", loop_count, payload);
                }

                struct response chunk = {0};
                curl_easy_setopt(c, CURLOPT_POSTFIELDS, payload);
                curl_easy_setopt(c, CURLOPT_WRITEDATA, (void *)&chunk);

                struct timespec t_req_start, t_req_end;
                clock_gettime(CLOCK_MONOTONIC, &t_req_start);
                CURLcode res = curl_easy_perform(c);
                clock_gettime(CLOCK_MONOTONIC, &t_req_end);
                double elapsed_sec = (t_req_end.tv_sec  - t_req_start.tv_sec) +
                                     (t_req_end.tv_nsec - t_req_start.tv_nsec) * 1e-9;

                if (res != CURLE_OK || !chunk.data) {
                    fprintf(stderr, "Request failed: %s\n", curl_easy_strerror(res));
                    free(payload);
                    if (chunk.data) free(chunk.data);
                    break;
                }

                if (debug_mode) {
                    fprintf(stderr, "[debug] Loop %d response: %s\n", loop_count, chunk.data);
                }

                jsmn_parser p;
                jsmntok_t tok[2048];
                jsmn_init(&p);
                int r = jsmn_parse(&p, chunk.data, chunk.size, tok, 2048);

                if (r < 0) {
                    fprintf(stderr, "Failed to parse JSON response: %d\n", r);
                    free(payload);
                    free(chunk.data);
                    break;
                }

                int finish_reason_tok = -1;
                int message_tok = -1;
                int tool_calls_tok = -1;
                int usage_tok = -1;

                int error_tok = -1;
                for (int i = 1; i < r; i++) {
                    if (tok[i].type == JSMN_STRING) {
                        int len = tok[i].end - tok[i].start;
                        if (len == 13 && strncmp(chunk.data + tok[i].start, "finish_reason", 13) == 0) {
                            finish_reason_tok = i + 1;
                        } else if (len == 7 && strncmp(chunk.data + tok[i].start, "message", 7) == 0) {
                            message_tok = i + 1;
                        } else if (len == 10 && strncmp(chunk.data + tok[i].start, "tool_calls", 10) == 0) {
                            tool_calls_tok = i + 1;
                        } else if (len == 5 && strncmp(chunk.data + tok[i].start, "error", 5) == 0) {
                            error_tok = i + 1;
                        } else if (len == 5 && strncmp(chunk.data + tok[i].start, "usage", 5) == 0) {
                            usage_tok = i + 1;
                        }
                    }
                }

                /* Parse token usage for display */
                int prompt_tokens = 0, completion_tokens = 0, total_tokens = 0;
                if (usage_tok != -1 && tok[usage_tok].type == JSMN_OBJECT) {
                    int u_end = tok[usage_tok].end;
                    int k = usage_tok + 1;
                    while (k < r && tok[k].start < u_end) {
                        if (tok[k].type == JSMN_STRING) {
                            int ulen = tok[k].end - tok[k].start;
                            if (ulen == 13 && strncmp(chunk.data + tok[k].start, "prompt_tokens", 13) == 0)
                                prompt_tokens = atoi(chunk.data + tok[k+1].start);
                            else if (ulen == 17 && strncmp(chunk.data + tok[k].start, "completion_tokens", 17) == 0)
                                completion_tokens = atoi(chunk.data + tok[k+1].start);
                            else if (ulen == 12 && strncmp(chunk.data + tok[k].start, "total_tokens", 12) == 0)
                                total_tokens = atoi(chunk.data + tok[k+1].start);
                        }
                        k = json_skip_token(tok, r, k + 1);
                    }
                }

                if (error_tok != -1) {
                    char *err_msg = NULL;
                    if (tok[error_tok].type == JSMN_OBJECT) {
                        int err_end = tok[error_tok].end;
                        int k = error_tok + 1;
                        while (k < r && tok[k].start < err_end) {
                            if (tok[k].type == JSMN_STRING) {
                                int len = tok[k].end - tok[k].start;
                                if (len == 7 && strncmp(chunk.data + tok[k].start, "message", 7) == 0) {
                                    err_msg = unescape_json_string(chunk.data + tok[k + 1].start, tok[k + 1].end - tok[k + 1].start);
                                    break;
                                }
                            }
                            k = json_skip_token(tok, r, k + 1);
                        }
                    }
                    if (err_msg) {
                        fprintf(stderr, "\n\033[1;31m[ai Error]\033[0m %s\n", err_msg);
                        free(err_msg);
                    } else {
                        fprintf(stderr, "\n\033[1;31m[ai Error]\033[0m Unknown server error.\n");
                    }
                    has_more = 0;
                    free(payload);
                    free(chunk.data);
                    break;
                }

                if (message_tok != -1) {
                    char *msg_str = malloc(tok[message_tok].end - tok[message_tok].start + 1);
                    memcpy(msg_str, chunk.data + tok[message_tok].start, tok[message_tok].end - tok[message_tok].start);
                    msg_str[tok[message_tok].end - tok[message_tok].start] = '\0';
                    messages_json = append_message(messages_json, msg_str);
                    free(msg_str);
                }

                int should_call_tools = 0;
                if (finish_reason_tok != -1) {
                    int len = tok[finish_reason_tok].end - tok[finish_reason_tok].start;
                    if (len == 10 && strncmp(chunk.data + tok[finish_reason_tok].start, "tool_calls", 10) == 0) {
                        should_call_tools = 1;
                    }
                } else if (tool_calls_tok != -1) {
                    should_call_tools = 1;
                }

                if (should_call_tools && tool_calls_tok != -1 && tok[tool_calls_tok].type == JSMN_ARRAY) {
                    int num_calls = tok[tool_calls_tok].size;
                    int current_tok = tool_calls_tok + 1;

                    int task_done = 0;
                    for (int tc = 0; tc < num_calls; tc++) {
                        if (tok[current_tok].type != JSMN_OBJECT) break;

                        int call_id_tok = -1;
                        int func_tok = -1;

                        int end_pos = tok[current_tok].end;
                        int j = current_tok + 1;
                        while (j < r && tok[j].start < end_pos) {
                            if (tok[j].type == JSMN_STRING) {
                                int len = tok[j].end - tok[j].start;
                                if (len == 2 && strncmp(chunk.data + tok[j].start, "id", 2) == 0) {
                                    call_id_tok = j + 1;
                                } else if (len == 8 && strncmp(chunk.data + tok[j].start, "function", 8) == 0) {
                                    func_tok = j + 1;
                                }
                            }
                            j = json_skip_token(tok, r, j + 1);
                        }

                        int name_tok = -1;
                        int args_tok = -1;
                        if (func_tok != -1 && tok[func_tok].type == JSMN_OBJECT) {
                            int f_end = tok[func_tok].end;
                            int k = func_tok + 1;
                            while (k < r && tok[k].start < f_end) {
                                if (tok[k].type == JSMN_STRING) {
                                    int len = tok[k].end - tok[k].start;
                                    if (len == 4 && strncmp(chunk.data + tok[k].start, "name", 4) == 0) {
                                        name_tok = k + 1;
                                    } else if (len == 9 && strncmp(chunk.data + tok[k].start, "arguments", 9) == 0) {
                                        args_tok = k + 1;
                                    }
                                }
                                k = json_skip_token(tok, r, k + 1);
                              }
                          }

                          if (call_id_tok != -1 && name_tok != -1 && args_tok != -1) {
                              char *unescaped_id = unescape_json_string(chunk.data + tok[call_id_tok].start, tok[call_id_tok].end - tok[call_id_tok].start);
                              char *unescaped_name = unescape_json_string(chunk.data + tok[name_tok].start, tok[name_tok].end - tok[name_tok].start);
                              char *unescaped_args = unescape_json_string(chunk.data + tok[args_tok].start, tok[args_tok].end - tok[args_tok].start);

                              char *tool_output = NULL;

                              if (strcmp(unescaped_name, "think") == 0) {
                                  jsmn_parser arg_parser;
                                  jsmntok_t arg_toks[32];
                                  jsmn_init(&arg_parser);
                                  int arg_r = jsmn_parse(&arg_parser, unescaped_args, strlen(unescaped_args), arg_toks, 32);
                                  char *reasoning = NULL;
                                  for (int a = 1; a < arg_r; a++) {
                                      if (arg_toks[a].type == JSMN_STRING &&
                                          arg_toks[a].end - arg_toks[a].start == 9 &&
                                          strncmp(unescaped_args + arg_toks[a].start, "reasoning", 9) == 0) {
                                          reasoning = unescape_json_string(unescaped_args + arg_toks[a+1].start,
                                                                           arg_toks[a+1].end - arg_toks[a+1].start);
                                          break;
                                      }
                                  }
                                  if (!quiet_mode && reasoning) {
                                      fprintf(stdout, "\033[2m[thinking] %s\033[0m\n", reasoning);
                                      fflush(stdout);
                                  }
                                  if (reasoning) free(reasoning);
                                  tool_output = strdup("{\"ok\":true}");
                              } else if (strcmp(unescaped_name, "task_complete") == 0) {
                                  jsmn_parser arg_parser;
                                  jsmntok_t arg_toks[32];
                                  jsmn_init(&arg_parser);
                                  int arg_r = jsmn_parse(&arg_parser, unescaped_args, strlen(unescaped_args), arg_toks, 32);
                                  char *summary = NULL;
                                  for (int a = 1; a < arg_r; a++) {
                                      if (arg_toks[a].type == JSMN_STRING &&
                                          arg_toks[a].end - arg_toks[a].start == 7 &&
                                          strncmp(unescaped_args + arg_toks[a].start, "summary", 7) == 0) {
                                          summary = unescape_json_string(unescaped_args + arg_toks[a+1].start,
                                                                         arg_toks[a+1].end - arg_toks[a+1].start);
                                          break;
                                      }
                                  }
                                  if (summary) {
                                      log_job(current_prompt, pipe_writer, summary, interactive_mode);
                                      char *escaped_summary = shell_escape(summary);
                                      char render_cmd[4096 + strlen(escaped_summary)];
                                      snprintf(render_cmd, sizeof(render_cmd), "python3 %s render-markdown %s", mcp_script, escaped_summary);
                                      char *rendered = run_shell_command(render_cmd, NULL);
                                      if (rendered) {
                                          printf("%s\n", rendered);
                                          free(rendered);
                                      } else {
                                          printf("%s\n", summary);
                                      }
                                      free(escaped_summary);
                                      free(summary);
                                  }
                                  tool_output = strdup("{\"ok\":true}");
                                  has_more = 0;
                                  task_done = 1;
                              } else if (strcmp(unescaped_name, "execute_command") == 0) {
                                  jsmn_parser arg_parser;
                                  jsmntok_t arg_toks[64];
                                  jsmn_init(&arg_parser);
                                  int arg_r = jsmn_parse(&arg_parser, unescaped_args, strlen(unescaped_args), arg_toks, 64);
                                  char *cmd_val = NULL;
                                  for (int a = 1; a < arg_r; a++) {
                                      if (arg_toks[a].type == JSMN_STRING && 
                                          arg_toks[a].end - arg_toks[a].start == 7 &&
                                          strncmp(unescaped_args + arg_toks[a].start, "command", 7) == 0) {
                                          cmd_val = unescape_json_string(unescaped_args + arg_toks[a + 1].start, arg_toks[a + 1].end - arg_toks[a + 1].start);
                                          break;
                                      }
                                  }

                                  if (cmd_val) {
                                      int approved = auto_approve;
                                      if (!approved) {
                                          FILE *tty = fopen("/dev/tty", "r+");
                                          if (tty) {
                                              fprintf(tty, "\n\033[1;33m[ai] Execute command:\033[0m %s\n", cmd_val);
                                              fprintf(tty, "\033[1;36mConfirm execution? [Y/n]:\033[0m ");
                                              fflush(tty);
                                              
                                              char response[64] = {0};
                                              if (fgets(response, sizeof(response), tty)) {
                                                  char *p_resp = response;
                                                  while (*p_resp && isspace((unsigned char)*p_resp)) p_resp++;
                                                  if (*p_resp == '\0' || *p_resp == 'y' || *p_resp == 'Y' || strncasecmp(p_resp, "yes", 3) == 0) {
                                                      approved = 1;
                                                  }
                                              }
                                              fclose(tty);
                                          } else {
                                              fprintf(stderr, "[ai] Warning: cannot open /dev/tty for confirmation. Skipping command execution for safety.\n");
                                          }
                                      }

                                      if (approved) {
                                          fprintf(stderr, "[ai] executing command: %s\n", cmd_val);
                                          size_t cmd_len = strlen(cmd_val);
                                          char *cmd_with_stderr = malloc(cmd_len + 16);
                                          sprintf(cmd_with_stderr, "%s 2>&1", cmd_val);
                                          int exit_code = 0;
                                          char *raw_output = run_shell_command(cmd_with_stderr, &exit_code);
                                          free(cmd_with_stderr);
                                          
                                          if (raw_output) {
                                              size_t out_len = strlen(raw_output);
                                              tool_output = malloc(out_len + 128);
                                              if (exit_code == 0) {
                                                  sprintf(tool_output, "[Command Success]\n%s", raw_output);
                                              } else {
                                                  sprintf(tool_output, "[Command Failed with exit status %d]\n%s", exit_code, raw_output);
                                              }
                                              free(raw_output);
                                          } else {
                                              tool_output = strdup("Error: failed to run command");
                                          }
                                      } else {
                                          fprintf(stderr, "[ai] command execution cancelled.\n");
                                          tool_output = strdup("Error: Command execution was cancelled/denied by the user.");
                                      }
                                      free(cmd_val);
                                  } else {
                                      tool_output = strdup("Error: 'command' argument not found");
                                  }
                              } else {
                                  char *server_name = strdup(unescaped_name);
                                  char *mcp_tool_name = strstr(server_name, "__");
                                  if (mcp_tool_name) {
                                      *mcp_tool_name = '\0';
                                      mcp_tool_name += 2;
                                  } else {
                                      mcp_tool_name = unescaped_name;
                                  }

                                  /* Show a human-readable line for what the model is doing */
                                  if (strcmp(mcp_tool_name, "read_file") == 0 ||
                                      strcmp(mcp_tool_name, "write_file") == 0 ||
                                      strcmp(mcp_tool_name, "edit_file") == 0 ||
                                      strcmp(mcp_tool_name, "list_directory") == 0) {
                                      char *fpath = json_get_string(unescaped_args, "path");
                                      fprintf(stderr, "\033[2m[ai] %s: %s\033[0m\n",
                                              mcp_tool_name, fpath ? fpath : "?");
                                      if (fpath) free(fpath);
                                  } else if (strcmp(mcp_tool_name, "web_search") == 0) {
                                      char *q = json_get_string(unescaped_args, "query");
                                      fprintf(stderr, "\033[2m[ai] web_search: \"%s\"\033[0m\n",
                                              q ? q : "?");
                                      if (q) free(q);
                                  } else if (strcmp(mcp_tool_name, "fetch_webpage") == 0) {
                                      char *url = json_get_string(unescaped_args, "url");
                                      fprintf(stderr, "\033[2m[ai] fetch_webpage: %s\033[0m\n",
                                              url ? url : "?");
                                      if (url) free(url);
                                  } else if (strcmp(mcp_tool_name, "delegate_task") == 0) {
                                      char *task = json_get_string(unescaped_args, "task");
                                      if (task) {
                                          task[strcspn(task, "\n")] = '\0';
                                          if (strlen(task) > 72) task[72] = '\0';
                                          fprintf(stderr, "\033[2m[ai] delegate_task: \"%s...\"\033[0m\n", task);
                                          free(task);
                                      } else {
                                          fprintf(stderr, "\033[2m[ai] delegate_task\033[0m\n");
                                      }
                                  } else if (strcmp(mcp_tool_name, "save_memory") == 0) {
                                      char *mem = json_get_string(unescaped_args, "content");
                                      fprintf(stderr, "\033[2m[ai] save_memory (%zu chars)\033[0m\n",
                                              mem ? strlen(mem) : 0UL);
                                      if (mem) free(mem);
                                  } else {
                                      fprintf(stderr, "\033[2m[ai] %s::%s\033[0m\n",
                                              server_name, mcp_tool_name);
                                  }
                                  
                                  char *escaped_args_shell = shell_escape(unescaped_args);
                                  char call_cmd[4096 + strlen(escaped_args_shell)];
                                  snprintf(call_cmd, sizeof(call_cmd), "python3 %s call-tool %s %s %s", mcp_script, server_name, mcp_tool_name, escaped_args_shell);
                                  tool_output = run_shell_command(call_cmd, NULL);

                                  free(server_name);
                                  free(escaped_args_shell);
                              }

                              if (!tool_output) {
                                  tool_output = strdup("Error: failed to execute tool");
                              }

                              /* Prefix tool results with a structured header so small models
                                 can track which tool produced which data */
                              if (strcmp(unescaped_name, "think") != 0 &&
                                  strcmp(unescaped_name, "task_complete") != 0) {
                                  int is_err = (strncmp(tool_output, "Error:", 6) == 0 ||
                                                strncmp(tool_output, "[Command Failed", 15) == 0 ||
                                                strncmp(tool_output, "{\"error\"", 8) == 0);
                                  size_t hlen = strlen(unescaped_name) + strlen(tool_output) + 48;
                                  char *hout = malloc(hlen);
                                  snprintf(hout, hlen, "[Tool: %s | Status: %s]\n%s",
                                           unescaped_name, is_err ? "error" : "ok", tool_output);
                                  free(tool_output);
                                  tool_output = hout;
                              }

                              /* Cap individual tool output to prevent context blowup */
                              if (strlen(tool_output) > 3000) {
                                  char *capped = malloc(3020);
                                  memcpy(capped, tool_output, 3000);
                                  strcpy(capped + 3000, "\n... [truncated]");
                                  free(tool_output);
                                  tool_output = capped;
                              }

                              /* If total context is already large, stub this result */
                              if (strlen(messages_json) > 40000) {
                                  free(tool_output);
                                  tool_output = strdup("[context limit reached \xe2\x80\x94 result omitted to preserve model focus]");
                              }

                              char *safe_output = json_escape(tool_output);
                              size_t tool_resp_len = strlen(safe_output) + strlen(unescaped_id) + strlen(unescaped_name) + 256;
                              char *tool_resp = malloc(tool_resp_len);
                              sprintf(tool_resp, "{\"role\":\"tool\",\"tool_call_id\":\"%s\",\"name\":\"%s\",\"content\":\"%s\"}", unescaped_id, unescaped_name, safe_output);
                              
                              messages_json = append_message(messages_json, tool_resp);

                              // Check if it's an image file returned by read_file/read_image_file
                              if (strncmp(tool_output, "[IMAGE_DATA_SUCCESS:", 20) == 0) {
                                  char *img_path_start = tool_output + 20;
                                  char *img_path_end = strchr(img_path_start, ']');
                                  if (img_path_end) {
                                      *img_path_end = '\0';
                                      const char *mime_type = NULL;
                                      char *b64 = read_image_base64(img_path_start, &mime_type);
                                      if (b64) {
                                          size_t user_msg_len = strlen(b64) + strlen(mime_type) + strlen(img_path_start) + 512;
                                          char *user_msg = malloc(user_msg_len);
                                          sprintf(user_msg, "{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"Here is the image file '%s' you requested to read:\"},{\"type\":\"image_url\",\"image_url\":{\"url\":\"data:%s;base64,%s\"}}]}",
                                                  img_path_start, mime_type, b64);
                                          messages_json = append_message(messages_json, user_msg);
                                          free(user_msg);
                                          free(b64);
                                      }
                                      *img_path_end = ']'; // restore
                                  }
                              }

                              free(unescaped_id);
                              free(unescaped_name);
                              free(unescaped_args);
                              free(tool_output);
                              free(safe_output);
                              free(tool_resp);
                              if (task_done) break;
                          }

                          current_tok = json_skip_token(tok, r, current_tok);
                      }
                  } else {
                      has_more = 0;

                      int content_tok = -1;
                      if (message_tok != -1) {
                          int msg_end = tok[message_tok].end;
                          int k = message_tok + 1;
                          while (k < r && tok[k].start < msg_end) {
                              if (tok[k].type == JSMN_STRING && 
                                  tok[k].end - tok[k].start == 7 &&
                                  strncmp(chunk.data + tok[k].start, "content", 7) == 0) {
                                  content_tok = k + 1;
                                  break;
                              }
                              k = json_skip_token(tok, r, k + 1);
                          }
                      }

                      if (content_tok != -1 && tok[content_tok].type == JSMN_STRING) {
                          char *unescaped_content = unescape_json_string(chunk.data + tok[content_tok].start, tok[content_tok].end - tok[content_tok].start);
                          log_job(current_prompt, pipe_writer, unescaped_content, interactive_mode);
                          char *escaped_content = shell_escape(unescaped_content);
                          
                          char render_cmd[4096 + strlen(escaped_content)];
                          snprintf(render_cmd, sizeof(render_cmd), "python3 %s render-markdown %s", mcp_script, escaped_content);
                          char *rendered_output = run_shell_command(render_cmd, NULL);
                          
                          if (rendered_output) {
                              printf("%s", rendered_output);
                              free(rendered_output);
                          } else {
                              printf("%s\n", unescaped_content);
                          }
                          
                          free(unescaped_content);
                          free(escaped_content);
                      }

                      /* If the model stopped with no content and no tool calls, nudge it
                         to call task_complete rather than silently stalling */
                      if ((content_tok == -1 ||
                           tok[content_tok].end - tok[content_tok].start == 0) &&
                          loop_count < 28) {
                          const char *nudge = "{\"role\":\"user\",\"content\":\"Please call task_complete with your final answer.\"}";
                          messages_json = append_message(messages_json, nudge);
                          has_more = 1;
                      }
                  }

                  /* Usage / speed stats line */
                  if (!quiet_mode && (prompt_tokens > 0 || completion_tokens > 0)) {
                      double tps = (elapsed_sec > 0.05 && completion_tokens > 0)
                                   ? completion_tokens / elapsed_sec : 0.0;
                      if (context_window > 0) {
                          int pct = (int)(100.0 * total_tokens / context_window);
                          fprintf(stderr,
                              "\033[2m[loop %d | ctx %d/%d (%d%%) | +%d tok | %.0f tok/s]\033[0m\n",
                              loop_count, total_tokens, context_window, pct,
                              completion_tokens, tps);
                      } else {
                          fprintf(stderr,
                              "\033[2m[loop %d | %d ctx tok | +%d new | %.0f tok/s]\033[0m\n",
                              loop_count, prompt_tokens, completion_tokens, tps);
                      }
                  }

                  free(payload);
                  free(chunk.data);
              }
          }
          
          if (!interactive_mode) {
              keep_going = 0;
          }
      }

    free(pipe_in);
    if (pipe_writer) free(pipe_writer);
    free(prompt);
    free(messages_json);
    if (tools_json) free(tools_json);
    if (current_prompt) free(current_prompt);
    curl_slist_free_all(h);
    curl_easy_cleanup(c);
    return 0;
}
