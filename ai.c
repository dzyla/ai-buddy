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
#include <sys/ioctl.h>
#include <termios.h>
#include <sys/select.h>
#include <errno.h>

#define MAX_LINE 1024
#define MAX_VAL  2048

#ifndef AI_VERSION
#define AI_VERSION "dev"
#endif

// Config globals
static char  api_url[MAX_VAL];
static char  api_key[MAX_VAL];
static char  model[MAX_VAL];
static float temperature_val  = -1.0f;
static int   max_tokens_val   = -1;
static int   no_tools_mode    = 0;
static int   context_window   = 0;    /* set via INFER_CONTEXT_WINDOW */
static int   task_timeout_sec = 300;  /* set via INFER_TASK_TIMEOUT; 0 = no timeout */
static int   max_tool_output  = 65536;/* set via INFER_MAX_TOOL_OUTPUT; default 65536 */
static int   trim_threshold   = 100000;/* set via INFER_TRIM_THRESHOLD; default 100000 */
static int   stub_threshold   = 250000;/* set via INFER_STUB_THRESHOLD; default 250000 */

static const char *SYSTEM_PROMPT =
    "You are a fully autonomous CLI agent. Output in clean markdown. Follow these rules exactly:\n\n"
    "EFFICIENCY (highest priority — every extra tool call costs 15-30 seconds on local hardware):\n"
    "- Single-step tasks (list files, read one file, run one command, factual question) = ONE tool call then task_complete. No think.\n"
    "- Routine sequences (git operations, file edits, package installs, shell scripts): skip think. Start with the first command directly.\n"
    "- Only use think for genuinely complex planning (3+ interdependent unknowns). If you use it, call it ONCE before your first action. After ANY non-think tool call, NEVER call think again.\n"
    "- Once ALL requested operations succeed (exit 0, file saved, git pushed), call task_complete IMMEDIATELY. Do NOT verify, re-read, or run extra diagnostics.\n\n"
    "TOOL USE:\n"
    "- For facts you already know (e.g. definitions, formulas, capitals), call task_complete directly — no tools needed.\n"
    "- For scientific databases, public APIs, or structured data (PDB, UniProt, NCBI, NASA, arXiv, etc.): use execute_command with curl to query the REST API directly. DO NOT rely on web_search snippets for structured data — the API will give exact answers.\n"
    "  Examples: PDB → `curl 'https://search.rcsb.org/rcsbsearch/v2/query' -d '{...}'`; arXiv → `curl 'https://export.arxiv.org/api/query?search_query=...'`\n"
    "- Use web_search for general questions or current news. web_search now auto-fetches the top result — check [Top result full content] first before calling fetch_webpage again.\n"
    "- Do NOT repeat web_search with slightly different queries — if the first search returns no answer, fetch the top result URL or switch to an API.\n"
    "- After writing a script with write_file, you MUST run it with execute_command to verify it works.\n"
    "- NEVER describe what the user can do themselves. If a tool can get the answer, use it.\n\n"
    "CITATIONS:\n"
    "- fetch_webpage and read_file (PDF) results begin with a [Source: ...] line. Track every source whose content you use.\n"
    "- In your task_complete summary, always end with a '## Sources' section listing each [Source: ...] URL or file path you drew from.\n"
    "- Do not list sources you fetched but did not use in the answer.\n\n"
    "FAILURE RECOVERY:\n"
    "- If execute_command fails, read the error, fix the root cause, and retry. At least 3 attempts before giving up.\n"
    "- If a library is missing, install it with pip/apt. If a web source is blocked or noisy, find an alternative.\n"
    "- If fetch_webpage returns a WARNING about JavaScript or returns fewer than 80 words, the page is JS-only. Switch to execute_command with curl to a plain-text API instead.\n"
    "- For current weather: execute_command `curl -s 'wttr.in/Miami?format=3'` (replace city name). Never rely on weather.com/weather.gov — they require JavaScript.\n"
    "- Never tell the user to 'visit a link' or 'run a command themselves' — do it yourself.\n\n"
    "PARALLEL EXECUTION (use these tools whenever work can be split):\n"
    "- parallel_fetch({\"urls\":[\"url1\",\"url2\",...]}) — fetch N pages at once. Use instead of N sequential fetch_webpage calls. Ideal for reading multiple search results, papers, or docs.\n"
    "- delegate_task({\"tasks\":[\"task1\",\"task2\",...]}) — spawn N agents concurrently. Use for independent sub-tasks that need their own tool loops (summarise a paper, write a script, run a benchmark). Always pass tasks as an ARRAY, never as a single string.\n"
    "- Example — publication digest: parallel_fetch({\"urls\":[paper1,paper2,paper3]}) then synthesise.\n"
    "- Example — multi-site comparison: parallel_fetch({\"urls\":[site1,site2,site3]}).\n"
    "- Example — parallel research: delegate_task({\"tasks\":[\"Search for X and summarise findings\",\"Search for Y and summarise findings\"]}).\n"
    "- Rule: if you would call fetch_webpage or web_search more than once for independent URLs/queries, use parallel_fetch or delegate_task instead.\n\n"
    "SKILLS:\n"
    "- Domain skills exist. Call load_skill() to list them, load_skill(name) to read one.\n"
    "- Additional CRITICAL triggers may follow in the system context — obey them exactly.";

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

/* Extract the "description:" value from SKILL.md frontmatter (reads first 512 bytes). */
static char *parse_skill_description(const char *content) {
    const char *key = "description:";
    const char *found = strstr(content, key);
    if (!found) return strdup("(no description)");
    found += strlen(key);
    while (*found == ' ') found++;
    const char *end = found;
    while (*end && *end != '\n' && *end != '\r') end++;
    size_t len = (size_t)(end - found);
    char *desc = malloc(len + 1);
    memcpy(desc, found, len);
    desc[len] = '\0';
    return desc;
}

/* Build a compact one-line-per-skill index from a directory (name: description). */
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
            char header[512];
            size_t n = fread(header, 1, sizeof(header) - 1, fp);
            header[n] = '\0';
            fclose(fp);

            char *desc = parse_skill_description(header);
            size_t entry_len = strlen(entry->d_name) + strlen(desc) + 16;
            if (len + entry_len + 4 >= cap) {
                cap = cap * 2 + entry_len;
                buf = realloc(buf, cap);
            }
            len += sprintf(buf + len, "- %s: %s\n", entry->d_name, desc);
            free(desc);
        }
    }
    closedir(dir);
    if (len == 0) { free(buf); return NULL; }
    return buf;
}

/* Scan one skill directory and append CRITICAL trigger lines to buf.
   Format: "- CRITICAL — <condition> → load_skill('<name>') before any other tool.\n"
   Only skills whose description starts with "CRITICAL" are included. */
static char* collect_triggers_from_dir(const char *base_dir, char *buf,
                                        size_t *len, size_t *cap,
                                        int *found_any) {
    DIR *dir = opendir(base_dir);
    if (!dir) return buf;

    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL) {
        if (entry->d_name[0] == '.') continue;

        char skill_path[1024];
        snprintf(skill_path, sizeof(skill_path), "%s/%s/SKILL.md", base_dir, entry->d_name);

        FILE *fp = fopen(skill_path, "r");
        if (!fp) continue;
        char header[512];
        size_t n = fread(header, 1, sizeof(header) - 1, fp);
        header[n] = '\0';
        fclose(fp);

        char *desc = parse_skill_description(header);
        if (strncmp(desc, "CRITICAL", 8) != 0) { free(desc); continue; }

        /* Extract condition: everything up to the first ": " separator. */
        const char *sep = strstr(desc, ": ");
        size_t cond_len = sep ? (size_t)(sep - desc) : strlen(desc);
        char *cond = malloc(cond_len + 1);
        memcpy(cond, desc, cond_len);
        cond[cond_len] = '\0';
        free(desc);

        /* Skip if this skill name was already added (exists in both dirs). */
        char check[512];
        snprintf(check, sizeof(check), "load_skill('%s')", entry->d_name);
        if (strstr(buf, check)) { free(cond); continue; }

        char line[1024];
        int llen = snprintf(line, sizeof(line),
            "- %s → call load_skill('%s') before any other tool.\n",
            cond, entry->d_name);
        free(cond);

        if (*len + (size_t)llen + 4 >= *cap) {
            *cap = *cap * 2 + (size_t)llen + 256;
            buf = realloc(buf, *cap);
        }
        memcpy(buf + *len, line, llen);
        *len += llen;
        buf[*len] = '\0';
        (*found_any)++;
    }
    closedir(dir);
    return buf;
}

/* Return a string of CRITICAL trigger rules, or NULL if none exist. */
static char* load_critical_triggers() {
    size_t cap = 4096, len = 0;
    int found = 0;
    char *buf = malloc(cap);
    buf[0] = '\0';

    char *home = getenv("HOME");
    if (home) {
        char global_path[1024];
        snprintf(global_path, sizeof(global_path), "%s/.config/ai/skills", home);
        buf = collect_triggers_from_dir(global_path, buf, &len, &cap, &found);
    }
    buf = collect_triggers_from_dir("./.agents/skills", buf, &len, &cap, &found);

    if (!found) { free(buf); return NULL; }
    return buf;
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

static void append_utf8(char *dest, int *d, uint32_t cp) {
    if (cp <= 0x7F) {
        dest[(*d)++] = (char)cp;
    } else if (cp <= 0x7FF) {
        dest[(*d)++] = (char)(0xC0 | ((cp >> 6) & 0x1F));
        dest[(*d)++] = (char)(0x80 | (cp & 0x3F));
    } else if (cp <= 0xFFFF) {
        dest[(*d)++] = (char)(0xE0 | ((cp >> 12) & 0x0F));
        dest[(*d)++] = (char)(0x80 | ((cp >> 6) & 0x3F));
        dest[(*d)++] = (char)(0x80 | (cp & 0x3F));
    } else if (cp <= 0x10FFFF) {
        dest[(*d)++] = (char)(0xF0 | ((cp >> 18) & 0x07));
        dest[(*d)++] = (char)(0x80 | ((cp >> 12) & 0x3F));
        dest[(*d)++] = (char)(0x80 | ((cp >> 6) & 0x3F));
        dest[(*d)++] = (char)(0x80 | (cp & 0x3F));
    }
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
                    int valid = 1;
                    for (int k = 0; k < 4; k++) {
                        int hv = hexval(s[i + k]);
                        if (hv < 0) { valid = 0; break; }
                        cp = (cp << 4) | (uint32_t)hv;
                    }
                    if (valid) {
                        i += 4;
                        // Check if it's a high surrogate (0xD800 to 0xDBFF)
                        if (cp >= 0xD800 && cp <= 0xDBFF && i + 6 <= len && s[i] == '\\' && s[i+1] == 'u') {
                            uint32_t cp2 = 0;
                            int valid2 = 1;
                            for (int k = 0; k < 4; k++) {
                                int hv = hexval(s[i + 2 + k]);
                                if (hv < 0) { valid2 = 0; break; }
                                cp2 = (cp2 << 4) | (uint32_t)hv;
                            }
                            if (valid2 && cp2 >= 0xDC00 && cp2 <= 0xDFFF) {
                                cp = 0x10000 + (((cp - 0xD800) << 10) | (cp2 - 0xDC00));
                                i += 6;
                            }
                        }
                        append_utf8(dest, &d, cp);
                    } else {
                        dest[d++] = '\\';
                        dest[d++] = 'u';
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

static struct termios orig_termios;
static int raw_mode_active = 0;
static volatile int g_esc_requested = 0;
static char *g_system_message_json = NULL;
static volatile int g_compact_in_progress = 0;
static int g_compact_dot_timer = 0;
static int g_auto_approve = 0;
static volatile int g_agent_loop_active = 0; /* 1 while the has_more agent loop runs */

/* Shared stdin accumulation buffer for :btw detection (used by progress_cb + poll) */
static char g_agent_stdin_buf[4096] = "";
static int  g_agent_stdin_len = 0;
static char g_btw_message[4096] = "";
static volatile int g_btw_available = 0;

static void disable_raw_mode(void) {
    if (raw_mode_active) {
        tcsetattr(STDIN_FILENO, TCSAFLUSH, &orig_termios);
        raw_mode_active = 0;
    }
}

/* Called by libcurl periodically during transfers; returning non-zero aborts. */
static int curl_progress_cb(void *clientp, curl_off_t dltotal, curl_off_t dlnow,
                             curl_off_t ultotal, curl_off_t ulnow) {
    (void)clientp; (void)dltotal; (void)dlnow; (void)ultotal; (void)ulnow;
    if (g_compact_in_progress) {
        if (++g_compact_dot_timer % 3 == 0) {
            fprintf(stderr, ".");
            fflush(stderr);
        }
        return 0;
    }
    if (raw_mode_active && !g_esc_requested) {
        char ch;
        if (read(STDIN_FILENO, &ch, 1) == 1) {
            if (ch == 27) {
                /* peek for Shift-Tab sequence: ESC [ Z */
                char seq[2] = {0, 0};
                int n = read(STDIN_FILENO, seq, 2);
                if (n == 2 && seq[0] == '[' && seq[1] == 'Z') {
                    if (g_agent_loop_active && !g_auto_approve) {
                        /* Safety: can't enable auto-approve during an active task */
                        fprintf(stderr,
                            "\n\033[2m[Shift-Tab: auto-approve can only be enabled "
                            "before a task starts]\033[0m\n");
                        fflush(stderr);
                    } else {
                        g_auto_approve ^= 1;
                        if (g_auto_approve)
                            setenv("INFER_AUTO_APPROVE", "1", 1);
                        else
                            unsetenv("INFER_AUTO_APPROVE");
                        fprintf(stderr, "\n\033[2mauto-approve %s\033[0m\n",
                                g_auto_approve ? "on" : "off");
                        fflush(stderr);
                    }
                    g_agent_stdin_len = 0;
                } else {
                    g_esc_requested = 1;
                }
            } else if (ch == '\r' || ch == '\n') {
                /* Complete line — check for :btw command */
                g_agent_stdin_buf[g_agent_stdin_len] = '\0';
                const char *line = g_agent_stdin_buf;
                while (*line == ' ') line++;
                if (strncmp(line, ":btw", 4) == 0) {
                    const char *msg = line + 4;
                    while (*msg == ' ') msg++;
                    if (*msg) {
                        strncpy(g_btw_message, msg, sizeof(g_btw_message) - 1);
                        g_btw_message[sizeof(g_btw_message) - 1] = '\0';
                        g_btw_available = 1;
                        fprintf(stderr, "\n\033[2m[btw] queued\033[0m\n");
                        fflush(stderr);
                    } else {
                        fprintf(stderr, "\n\033[2m[btw] usage: :btw <message>\033[0m\n");
                        fflush(stderr);
                    }
                }
                g_agent_stdin_len = 0;
            } else if (ch >= 32 && ch <= 126) {
                /* Echo printable char so user can see what they're typing */
                if (g_agent_stdin_len < (int)sizeof(g_agent_stdin_buf) - 1) {
                    g_agent_stdin_buf[g_agent_stdin_len++] = ch;
                    fputc(ch, stderr);
                    fflush(stderr);
                }
            }
        }
    }
    return g_esc_requested ? 1 : 0;
}

static void enable_raw_mode(void) {
    if (!isatty(STDIN_FILENO)) return;
    if (tcgetattr(STDIN_FILENO, &orig_termios) < 0) return;
    
    struct termios raw = orig_termios;
    raw.c_lflag &= ~(ECHO | ICANON); // Disable echo and canonical mode, keep signals (Ctrl+C works)
    raw.c_cc[VMIN] = 0;
    raw.c_cc[VTIME] = 0;
    
    if (tcsetattr(STDIN_FILENO, TCSAFLUSH, &raw) >= 0) {
        raw_mode_active = 1;
        atexit(disable_raw_mode);
    }
}

/* Poll stdin non-blocking between tool calls for Shift-Tab / ESC / :btw.
   Temporarily enters raw mode so that chars buffered during tool execution
   (typed in canonical mode) are read without blocking. */
static void poll_agent_stdin(void) {
    if (!isatty(STDIN_FILENO) || raw_mode_active) return;

    struct termios raw = orig_termios;
    raw.c_lflag &= ~(ECHO | ICANON);
    raw.c_cc[VMIN]  = 0;
    raw.c_cc[VTIME] = 0;
    if (tcsetattr(STDIN_FILENO, TCSANOW, &raw) < 0) return;

    char ch;
    while (!g_esc_requested && read(STDIN_FILENO, &ch, 1) == 1) {
        if (ch == 27) {
            char seq[2] = {0, 0};
            int n = read(STDIN_FILENO, seq, 2);
            if (n == 2 && seq[0] == '[' && seq[1] == 'Z') {
                /* Shift-Tab */
                if (g_agent_loop_active && !g_auto_approve) {
                    fprintf(stderr,
                        "\n\033[2m[Shift-Tab: auto-approve can only be enabled "
                        "before a task starts]\033[0m\n");
                    fflush(stderr);
                } else {
                    g_auto_approve ^= 1;
                    if (g_auto_approve) setenv("INFER_AUTO_APPROVE", "1", 1);
                    else               unsetenv("INFER_AUTO_APPROVE");
                    fprintf(stderr, "\n\033[2mauto-approve %s\033[0m\n",
                            g_auto_approve ? "on" : "off");
                    fflush(stderr);
                }
                g_agent_stdin_len = 0;
            } else {
                g_esc_requested = 1;
            }
        } else if (ch == '\r' || ch == '\n') {
            g_agent_stdin_buf[g_agent_stdin_len] = '\0';
            const char *line = g_agent_stdin_buf;
            while (*line == ' ') line++;
            if (strncmp(line, ":btw", 4) == 0) {
                const char *msg = line + 4;
                while (*msg == ' ') msg++;
                if (*msg) {
                    strncpy(g_btw_message, msg, sizeof(g_btw_message) - 1);
                    g_btw_message[sizeof(g_btw_message) - 1] = '\0';
                    g_btw_available = 1;
                    fprintf(stderr, "\033[2m[btw] queued\033[0m\n");
                    fflush(stderr);
                } else {
                    fprintf(stderr, "\033[2m[btw] usage: :btw <message>\033[0m\n");
                    fflush(stderr);
                }
            }
            g_agent_stdin_len = 0;
        } else if (ch >= 32 && ch <= 126) {
            if (g_agent_stdin_len < (int)sizeof(g_agent_stdin_buf) - 1)
                g_agent_stdin_buf[g_agent_stdin_len++] = ch;
        }
    }

    tcsetattr(STDIN_FILENO, TCSANOW, &orig_termios);
}

/* ── Minimal interactive line editor with history ─────────────────────────── */

#define LINEED_MAX_LINE    4096
#define LINEED_MAX_HISTORY 500

static char *lineed_history[LINEED_MAX_HISTORY];
static int   lineed_history_len  = 0;
static char  lineed_history_path[2048] = "";
static int   lineed_prev_rows    = 0;   /* rows occupied by last redraw */

static void lineed_add_history(const char *line) {
    if (!line || !*line) return;
    if (lineed_history_len > 0 &&
        strcmp(lineed_history[lineed_history_len - 1], line) == 0)
        return;
    if (lineed_history_len == LINEED_MAX_HISTORY) {
        free(lineed_history[0]);
        memmove(lineed_history, lineed_history + 1,
                (LINEED_MAX_HISTORY - 1) * sizeof(char *));
        lineed_history_len--;
    }
    lineed_history[lineed_history_len++] = strdup(line);
}

static void lineed_save_history(void) {
    if (!lineed_history_path[0]) return;
    FILE *f = fopen(lineed_history_path, "w");
    if (!f) return;
    for (int i = 0; i < lineed_history_len; i++)
        fprintf(f, "%s\n", lineed_history[i]);
    fclose(f);
}

static void lineed_init(void) {
    char *home = getenv("HOME");
    if (!home) return;
    char dir[1024];
    snprintf(dir, sizeof(dir), "%s/.cache/ai", home);
    mkdir(dir, 0755);
    snprintf(lineed_history_path, sizeof(lineed_history_path),
             "%s/input_history", dir);
    FILE *f = fopen(lineed_history_path, "r");
    if (!f) return;
    char buf[LINEED_MAX_LINE];
    while (fgets(buf, sizeof(buf), f)) {
        size_t l = strlen(buf);
        while (l > 0 && (buf[l - 1] == '\n' || buf[l - 1] == '\r')) buf[--l] = '\0';
        if (l > 0) lineed_add_history(buf);
    }
    fclose(f);
    atexit(lineed_save_history);
}

/* Count visible (non-ANSI-escape) display columns in a string. */
static int lineed_visible_len(const char *s) {
    int n = 0;
    while (*s) {
        unsigned char c = (unsigned char)*s;
        if (c == '\033' && *(s + 1) == '[') {
            /* Skip CSI sequence: ESC [ ... <letter> */
            s += 2;
            while (*s && ((*s < 'A' || *s > 'Z') && (*s < 'a' || *s > 'z'))) s++;
            if (*s) s++;
        } else if (c >= 0xF0) { n++; s += 4; }  /* 4-byte UTF-8 → 1 col */
        else if (c >= 0xE0)   { n++; s += 3; }  /* 3-byte UTF-8 → 1 col */
        else if (c >= 0xC0)   { n++; s += 2; }  /* 2-byte UTF-8 → 1 col */
        else if (c >= 0x80)   {      s++;    }  /* continuation byte    */
        else                  { n++; s++;    }  /* plain ASCII          */
    }
    return n;
}

static int lineed_term_cols(void) {
    struct winsize ws;
    if (ioctl(STDOUT_FILENO, TIOCGWINSZ, &ws) == 0 && ws.ws_col > 0)
        return (int)ws.ws_col;
    const char *e = getenv("COLUMNS");
    return (e && *e) ? atoi(e) : 80;
}

/*
 * Redraw the prompt+buffer, handling lines that wrap past the terminal width.
 * Tracks lineed_prev_rows so we can erase the old content before rewriting.
 */
static void lineed_redraw(const char *prompt, const char *buf, int len, int cursor) {
    int cols  = lineed_term_cols();
    int plen  = lineed_visible_len(prompt);
    char tmp[32];

    /* Move to the first row of the previous draw, then erase to end of screen */
    if (lineed_prev_rows > 0) {
        snprintf(tmp, sizeof(tmp), "\033[%dA", lineed_prev_rows);
        write(STDOUT_FILENO, tmp, strlen(tmp));
    }
    write(STDOUT_FILENO, "\r\033[J", 4);

    /* Write prompt and buffer */
    write(STDOUT_FILENO, prompt, strlen(prompt));
    if (len > 0)
        write(STDOUT_FILENO, buf, (size_t)len);

    /* Record how many extra rows this draw occupies */
    int total = plen + len;
    lineed_prev_rows = (total > 0) ? (total - 1) / cols : 0;

    /* Place the cursor at (plen + cursor) in the virtual unwrapped line */
    int cursor_abs = plen + cursor;
    int cursor_row = cursor_abs / cols;
    int cursor_col = cursor_abs % cols;
    int end_row    = (total > 0) ? (total - 1) / cols : 0;

    int rows_up = end_row - cursor_row;
    if (rows_up > 0) {
        snprintf(tmp, sizeof(tmp), "\033[%dA", rows_up);
        write(STDOUT_FILENO, tmp, strlen(tmp));
    }
    write(STDOUT_FILENO, "\r", 1);
    if (cursor_col > 0) {
        snprintf(tmp, sizeof(tmp), "\033[%dC", cursor_col);
        write(STDOUT_FILENO, tmp, strlen(tmp));
    }
}

/*
 * read_line_interactive: draw prompt, read a line with full editing + history.
 * Returns malloc'd string (caller frees), NULL on EOF/Ctrl+D with empty buffer,
 * or "\033[Z" on Shift-Tab (caller should toggle auto-approve and retry).
 */
static char *read_line_interactive(const char *prompt) {
    if (!isatty(STDIN_FILENO)) {
        char buf[LINEED_MAX_LINE];
        write(STDOUT_FILENO, prompt, strlen(prompt));
        if (!fgets(buf, sizeof(buf), stdin)) return NULL;
        size_t l = strlen(buf);
        while (l > 0 && (buf[l - 1] == '\n' || buf[l - 1] == '\r')) buf[--l] = '\0';
        return strdup(buf);
    }

    struct termios saved, raw;
    if (tcgetattr(STDIN_FILENO, &saved) < 0) {
        char buf[LINEED_MAX_LINE];
        write(STDOUT_FILENO, prompt, strlen(prompt));
        if (!fgets(buf, sizeof(buf), stdin)) return NULL;
        size_t l = strlen(buf);
        while (l > 0 && (buf[l - 1] == '\n' || buf[l - 1] == '\r')) buf[--l] = '\0';
        return strdup(buf);
    }

    raw = saved;
    raw.c_lflag &= ~(ECHO | ICANON | IEXTEN | ISIG);
    raw.c_iflag &= ~(BRKINT | ICRNL | INPCK | ISTRIP | IXON);
    raw.c_cflag |= CS8;
    raw.c_cc[VMIN]  = 1;
    raw.c_cc[VTIME] = 0;
    tcsetattr(STDIN_FILENO, TCSAFLUSH, &raw);

    lineed_prev_rows = 0;
    write(STDOUT_FILENO, "\033[?2004h", 8); /* enable bracketed paste */
    write(STDOUT_FILENO, prompt, strlen(prompt));

/* Disable bracketed paste and restore terminal at any exit point. */
#define LINEED_RESTORE() do { \
    write(STDOUT_FILENO, "\033[?2004l", 8); \
    tcsetattr(STDIN_FILENO, TCSAFLUSH, &saved); \
} while (0)

/* Insert one char into buf at cursor (no redraw). */
#define LINEED_INS(ch) do { \
    if (len < LINEED_MAX_LINE - 1) { \
        memmove(buf + cursor + 1, buf + cursor, (size_t)(len - cursor + 1)); \
        buf[cursor] = (char)(ch); cursor++; len++; \
    } \
} while (0)

    char buf[LINEED_MAX_LINE];
    int  len    = 0;
    int  cursor = 0;
    int  hidx   = lineed_history_len;
    char saved_buf[LINEED_MAX_LINE] = "";
    buf[0] = '\0';

    for (;;) {
        unsigned char c;
        if (read(STDIN_FILENO, &c, 1) <= 0) {
            write(STDOUT_FILENO, "\r\n", 2);
            LINEED_RESTORE();
            return len > 0 ? strdup(buf) : NULL;
        }

        if (c == '\r' || c == '\n') {
            write(STDOUT_FILENO, "\r\n", 2);
            break;
        }

        if (c == 4) { /* Ctrl+D */
            if (len == 0) {
                write(STDOUT_FILENO, "\r\n", 2);
                LINEED_RESTORE();
                return NULL;
            }
            if (cursor < len) {
                memmove(buf + cursor, buf + cursor + 1, (size_t)(len - cursor));
                len--;
                buf[len] = '\0';
                lineed_redraw(prompt, buf, len, cursor);
            }
            continue;
        }

        if (c == 3) { /* Ctrl+C — quit interactive mode */
            write(STDOUT_FILENO, "^C\r\n", 4);
            LINEED_RESTORE();
            return NULL;
        }

        if (c == 127 || c == 8) { /* Backspace */
            if (cursor > 0) {
                memmove(buf + cursor - 1, buf + cursor, (size_t)(len - cursor + 1));
                cursor--;
                len--;
                lineed_redraw(prompt, buf, len, cursor);
            }
            continue;
        }

        if (c == 1) { /* Ctrl+A */
            cursor = 0;
            lineed_redraw(prompt, buf, len, cursor);
            continue;
        }
        if (c == 5) { /* Ctrl+E */
            cursor = len;
            lineed_redraw(prompt, buf, len, cursor);
            continue;
        }
        if (c == 2) { /* Ctrl+B */
            if (cursor > 0) { cursor--; lineed_redraw(prompt, buf, len, cursor); }
            continue;
        }
        if (c == 6) { /* Ctrl+F */
            if (cursor < len) { cursor++; lineed_redraw(prompt, buf, len, cursor); }
            continue;
        }
        if (c == 11) { /* Ctrl+K */
            buf[cursor] = '\0';
            len = cursor;
            lineed_redraw(prompt, buf, len, cursor);
            continue;
        }
        if (c == 21) { /* Ctrl+U */
            memmove(buf, buf + cursor, (size_t)(len - cursor + 1));
            len -= cursor;
            cursor = 0;
            lineed_redraw(prompt, buf, len, cursor);
            continue;
        }
        if (c == 23) { /* Ctrl+W — kill word */
            int end = cursor;
            while (cursor > 0 && buf[cursor - 1] == ' ') cursor--;
            while (cursor > 0 && buf[cursor - 1] != ' ') cursor--;
            memmove(buf + cursor, buf + end, (size_t)(len - end + 1));
            len -= (end - cursor);
            lineed_redraw(prompt, buf, len, cursor);
            continue;
        }
        if (c == 12) { /* Ctrl+L — clear screen */
            write(STDOUT_FILENO, "\033[2J\033[H", 7);
            lineed_redraw(prompt, buf, len, cursor);
            continue;
        }

        if (c == 27) { /* ESC — escape sequence */
            unsigned char lead;
            if (read(STDIN_FILENO, &lead, 1) != 1) continue;

            if (lead == '[') {
                /* CSI sequence: read numeric parameter(s) + terminator */
                char   param[16]; int plen2 = 0;
                unsigned char term = 0;
                while (plen2 < 15) {
                    unsigned char ch2;
                    if (read(STDIN_FILENO, &ch2, 1) != 1) break;
                    if ((ch2 >= 'A' && ch2 <= 'Z') || (ch2 >= 'a' && ch2 <= 'z') || ch2 == '~') {
                        term = ch2; break;
                    }
                    param[plen2++] = (char)ch2;
                }
                param[plen2] = '\0';
                int csi_num = plen2 > 0 ? atoi(param) : 0;

                if (term == '~') {
                    if (csi_num == 200) {
                        /* ── Bracketed paste: collect until \033[201~ ── */
                        for (;;) {
                            unsigned char pc;
                            if (read(STDIN_FILENO, &pc, 1) != 1) break;
                            if (pc == '\033') {
                                unsigned char pa;
                                if (read(STDIN_FILENO, &pa, 1) != 1) break;
                                if (pa == '[') {
                                    char pn[16]; int pnl = 0; unsigned char pt = 0;
                                    while (pnl < 15) {
                                        if (read(STDIN_FILENO, &pt, 1) != 1) break;
                                        if ((pt>='A'&&pt<='Z')||(pt>='a'&&pt<='z')||pt=='~') break;
                                        pn[pnl++] = (char)pt;
                                    }
                                    pn[pnl] = '\0';
                                    if (pt == '~' && atoi(pn) == 201) break; /* end paste */
                                }
                                /* Ignore other ESC sequences inside paste */
                                continue;
                            }
                            /* Newlines become spaces — keeps single-line editor clean */
                            if (pc == '\r' || pc == '\n') { LINEED_INS(' '); continue; }
                            if (pc >= 32 || pc == '\t')   { LINEED_INS(pc);  continue; }
                        }
                        lineed_redraw(prompt, buf, len, cursor);
                    } else if (csi_num == 3) { /* Delete */
                        if (cursor < len) {
                            memmove(buf + cursor, buf + cursor + 1, (size_t)(len - cursor));
                            len--; buf[len] = '\0';
                            lineed_redraw(prompt, buf, len, cursor);
                        }
                    } else if (csi_num == 1 || csi_num == 7) { /* Home */
                        cursor = 0; lineed_redraw(prompt, buf, len, cursor);
                    } else if (csi_num == 4 || csi_num == 8) { /* End */
                        cursor = len; lineed_redraw(prompt, buf, len, cursor);
                    }
                } else {
                    switch (term) {
                        case 'A': /* Up — history back */
                            if (hidx > 0) {
                                if (hidx == lineed_history_len)
                                    strncpy(saved_buf, buf, LINEED_MAX_LINE - 1);
                                hidx--;
                                strncpy(buf, lineed_history[hidx], LINEED_MAX_LINE - 1);
                                len = cursor = (int)strlen(buf);
                                lineed_redraw(prompt, buf, len, cursor);
                            }
                            break;
                        case 'B': /* Down — history forward */
                            if (hidx < lineed_history_len) {
                                hidx++;
                                const char *src = (hidx == lineed_history_len)
                                                    ? saved_buf : lineed_history[hidx];
                                strncpy(buf, src, LINEED_MAX_LINE - 1);
                                len = cursor = (int)strlen(buf);
                                lineed_redraw(prompt, buf, len, cursor);
                            }
                            break;
                        case 'C': /* Right */
                            if (cursor < len) { cursor++; lineed_redraw(prompt, buf, len, cursor); }
                            break;
                        case 'D': /* Left */
                            if (cursor > 0) { cursor--; lineed_redraw(prompt, buf, len, cursor); }
                            break;
                        case 'H': cursor = 0;   lineed_redraw(prompt, buf, len, cursor); break;
                        case 'F': cursor = len; lineed_redraw(prompt, buf, len, cursor); break;
                        case 'Z': /* Shift-Tab */
                            write(STDOUT_FILENO, "\r\n", 2);
                            LINEED_RESTORE();
                            return strdup("\033[Z");
                    }
                }
            } else if (lead == 'O') {
                /* SS3 arrow key encoding (some terminals) */
                unsigned char ss3;
                if (read(STDIN_FILENO, &ss3, 1) != 1) continue;
                switch (ss3) {
                    case 'A':
                        if (hidx > 0) {
                            if (hidx == lineed_history_len)
                                strncpy(saved_buf, buf, LINEED_MAX_LINE - 1);
                            hidx--;
                            strncpy(buf, lineed_history[hidx], LINEED_MAX_LINE - 1);
                            len = cursor = (int)strlen(buf);
                            lineed_redraw(prompt, buf, len, cursor);
                        }
                        break;
                    case 'B':
                        if (hidx < lineed_history_len) {
                            hidx++;
                            const char *src2 = (hidx == lineed_history_len)
                                                 ? saved_buf : lineed_history[hidx];
                            strncpy(buf, src2, LINEED_MAX_LINE - 1);
                            len = cursor = (int)strlen(buf);
                            lineed_redraw(prompt, buf, len, cursor);
                        }
                        break;
                    case 'C': if (cursor < len) { cursor++; lineed_redraw(prompt, buf, len, cursor); } break;
                    case 'D': if (cursor > 0)   { cursor--; lineed_redraw(prompt, buf, len, cursor); } break;
                    case 'H': cursor = 0;   lineed_redraw(prompt, buf, len, cursor); break;
                    case 'F': cursor = len; lineed_redraw(prompt, buf, len, cursor); break;
                }
            }
            continue;
        }

        /* Regular printable character — insert at cursor */
        if (c >= 32 && len < LINEED_MAX_LINE - 1) {
            memmove(buf + cursor + 1, buf + cursor, (size_t)(len - cursor + 1));
            buf[cursor] = (char)c;
            cursor++;
            len++;
            lineed_redraw(prompt, buf, len, cursor);
        }
    }

    LINEED_RESTORE();
#undef LINEED_RESTORE
#undef LINEED_INS
    buf[len] = '\0';
    return strdup(buf);
}

static char* run_shell_command(const char *cmd, int *exit_status) {
    int started_raw = 0;
    if (!raw_mode_active && isatty(STDIN_FILENO)) {
        enable_raw_mode();
        started_raw = 1;
    }

    FILE *fp = popen(cmd, "r");
    if (!fp) {
        if (started_raw) disable_raw_mode();
        if (exit_status) *exit_status = -1;
        return strdup("Error: failed to run command");
    }
    
    int pipe_fd = fileno(fp);
    int fd_flags = fcntl(pipe_fd, F_GETFL, 0);
    fcntl(pipe_fd, F_SETFL, fd_flags | O_NONBLOCK);

    size_t size = 4096;
    size_t len = 0;
    char *buf = malloc(size);
    if (!buf) {
        pclose(fp);
        if (started_raw) disable_raw_mode();
        if (exit_status) *exit_status = -1;
        return NULL;
    }
    buf[0] = '\0';

    int interrupted = 0;
    while (1) {
        fd_set fds;
        FD_ZERO(&fds);
        FD_SET(pipe_fd, &fds);
        if (raw_mode_active) {
            FD_SET(STDIN_FILENO, &fds);
        }

        int max_fd = pipe_fd;
        if (raw_mode_active && STDIN_FILENO > max_fd) {
            max_fd = STDIN_FILENO;
        }

        struct timeval tv = {0, 100000}; // 100ms timeout
        int r = select(max_fd + 1, &fds, NULL, NULL, &tv);

        if (r < 0) {
            if (errno == EINTR) continue;
            break;
        }

        // Check ESC key on stdin
        if (raw_mode_active && FD_ISSET(STDIN_FILENO, &fds)) {
            char c;
            if (read(STDIN_FILENO, &c, 1) == 1) {
                if (c == 27) { // ESC key
                    interrupted = 1;
                    char kill_cmd[128];
                    snprintf(kill_cmd, sizeof(kill_cmd), "pkill -P %d 2>/dev/null", getpid());
                    system(kill_cmd);
                    fprintf(stderr, "\n\033[1;31m[ai] Tool execution interrupted by ESC key.\033[0m\n");
                    break;
                }
            }
        }

        // Read from pipe
        if (FD_ISSET(pipe_fd, &fds)) {
            char tmp[1024];
            ssize_t n = read(pipe_fd, tmp, sizeof(tmp) - 1);
            if (n > 0) {
                tmp[n] = '\0';
                if (len + n >= size - 1) {
                    size *= 2;
                    char *new_buf = realloc(buf, size);
                    if (!new_buf) {
                        free(buf);
                        pclose(fp);
                        if (started_raw) disable_raw_mode();
                        if (exit_status) *exit_status = -1;
                        return NULL;
                    }
                    buf = new_buf;
                }
                memcpy(buf + len, tmp, n);
                len += n;
                buf[len] = '\0';
            } else if (n == 0) {
                break; // EOF
            } else {
                if (errno != EAGAIN && errno != EWOULDBLOCK) {
                    break;
                }
            }
        }
    }

    int status = pclose(fp);
    if (started_raw) {
        disable_raw_mode();
    }

    if (exit_status) {
        if (interrupted) {
            *exit_status = 130; // SIGINT / interrupted status
        } else if (status == -1) {
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
    if ((int)strlen(messages_json) <= trim_threshold) return messages_json;
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

static int update_config_file(const char *file_path, const char *new_model, const char *new_url) {
    FILE *fp = fopen(file_path, "r");
    if (!fp) return 0; // File doesn't exist
    
    // Read the file content
    fseek(fp, 0, SEEK_END);
    long size = ftell(fp);
    fseek(fp, 0, SEEK_SET);
    
    char *content = malloc(size + 1024);
    if (!content) {
        fclose(fp);
        return 0;
    }
    
    long read_bytes = fread(content, 1, size, fp);
    content[read_bytes] = '\0';
    fclose(fp);
    
    // 1. Update INFER_MODEL
    char *line = strstr(content, "export INFER_MODEL=");
    char *temp_content = NULL;
    if (line) {
        char *next_line = strchr(line, '\n');
        if (!next_line) next_line = line + strlen(line);
        long prefix_len = line - content;
        long suffix_len = strlen(next_line);
        
        temp_content = malloc(prefix_len + suffix_len + strlen(new_model) + 64);
        if (temp_content) {
            memcpy(temp_content, content, prefix_len);
            int offset = prefix_len;
            offset += sprintf(temp_content + offset, "export INFER_MODEL=\"%s\"", new_model);
            strcpy(temp_content + offset, next_line);
        }
    } else {
        temp_content = malloc(size + strlen(new_model) + 64);
        if (temp_content) {
            sprintf(temp_content, "%s\nexport INFER_MODEL=\"%s\"\n", content, new_model);
        }
    }
    
    if (!temp_content) {
        free(content);
        return 0;
    }
    
    // 2. Update INFER_BASE_URL if new_url is provided
    char *final_content = NULL;
    if (new_url && strlen(new_url) > 0) {
        char *url_line = strstr(temp_content, "export INFER_BASE_URL=");
        if (url_line) {
            char *next_line = strchr(url_line, '\n');
            if (!next_line) next_line = url_line + strlen(url_line);
            long prefix_len = url_line - temp_content;
            long suffix_len = strlen(next_line);
            
            final_content = malloc(prefix_len + suffix_len + strlen(new_url) + 64);
            if (final_content) {
                memcpy(final_content, temp_content, prefix_len);
                int offset = prefix_len;
                offset += sprintf(final_content + offset, "export INFER_BASE_URL=\"%s\"", new_url);
                strcpy(final_content + offset, next_line);
            }
        } else {
            final_content = malloc(strlen(temp_content) + strlen(new_url) + 64);
            if (final_content) {
                sprintf(final_content, "%s\nexport INFER_BASE_URL=\"%s\"\n", temp_content, new_url);
            }
        }
    } else {
        final_content = strdup(temp_content);
    }
    
    free(content);
    free(temp_content);
    
    if (!final_content) return 0;
    
    // Write back
    fp = fopen(file_path, "w");
    if (!fp) {
        free(final_content);
        return 0;
    }
    fputs(final_content, fp);
    fclose(fp);
    free(final_content);
    
    printf("Successfully updated default settings in %s.\n", file_path);
    return 1;
}

static int detect_model_url(const char *model_name, char *url_out, size_t max_len) {
    if (strcmp(model_name, "llama") == 0 || strcmp(model_name, "llama-server") == 0) {
        char *env_base = getenv("INFER_BASE_URL");
        if (env_base && *env_base) {
            strncpy(url_out, env_base, max_len - 1);
            url_out[max_len - 1] = '\0';
            size_t len = strlen(url_out);
            if (len > 0 && url_out[len - 1] != '/') {
                if (len < max_len - 1) {
                    url_out[len] = '/';
                    url_out[len + 1] = '\0';
                }
            }
            return 1;
        }
        strncpy(url_out, "http://localhost:8080/v1/", max_len - 1);
        url_out[max_len - 1] = '\0';
        return 1;
    }
    char cmd[512];
    snprintf(cmd, sizeof(cmd), "%s status 2>/dev/null", model_name);
    FILE *status_fp = popen(cmd, "r");
    if (!status_fp) return 0;
    
    char line[1024];
    int found = 0;
    while (fgets(line, sizeof(line), status_fp)) {
        char *openai_ptr = strstr(line, "openai:");
        if (openai_ptr) {
            char *url_start = openai_ptr + 7;
            while (*url_start == ' ' || *url_start == '\t') url_start++;
            char *url_end = url_start;
            while (*url_end && *url_end != '\n' && *url_end != '\r' && *url_end != ' ' && *url_end != '\t') {
                url_end++;
            }
            int len = url_end - url_start;
            if (len > 0 && (size_t)len < max_len - 2) {
                memcpy(url_out, url_start, len);
                url_out[len] = '\0';
                
                // Ensure trailing slash
                if (len > 0 && url_out[len - 1] != '/') {
                    url_out[len] = '/';
                    url_out[len + 1] = '\0';
                }
                found = 1;
                break;
            }
        }
    }
    pclose(status_fp);
    return found;
}

static void load_from_profiles(char **url, char **key, char **model) {
    char *home = getenv("HOME");
    if (!home) return;

    char paths[2][1024];
    snprintf(paths[0], sizeof(paths[0]), "%s/.bashrc", home);
    snprintf(paths[1], sizeof(paths[1]), "%s/.zshrc", home);

    static char f_url[512] = "";
    static char f_key[256] = "";
    static char f_model[256] = "";

    // Clear static strings
    f_url[0] = '\0';
    f_key[0] = '\0';
    f_model[0] = '\0';

    for (int p = 0; p < 2; p++) {
        FILE *fp = fopen(paths[p], "r");
        if (!fp) continue;

        char line[1024];
        while (fgets(line, sizeof(line), fp)) {
            // Strip trailing spaces and newlines
            char *end = line + strlen(line) - 1;
            while (end >= line && (*end == '\n' || *end == '\r' || *end == ' ' || *end == '\t')) {
                *end = '\0';
                end--;
            }

            char *url_ptr = strstr(line, "export INFER_BASE_URL=");
            if (url_ptr) {
                char *val = url_ptr + 22;
                if (*val == '"' || *val == '\'') val++;
                char *val_end = val + strlen(val) - 1;
                while (val_end > val && (*val_end == '"' || *val_end == '\'')) {
                    *val_end = '\0';
                    val_end--;
                }
                strncpy(f_url, val, sizeof(f_url) - 1);
            }

            char *key_ptr = strstr(line, "export INFER_API_KEY=");
            if (key_ptr) {
                char *val = key_ptr + 21;
                if (*val == '"' || *val == '\'') val++;
                char *val_end = val + strlen(val) - 1;
                while (val_end > val && (*val_end == '"' || *val_end == '\'')) {
                    *val_end = '\0';
                    val_end--;
                }
                strncpy(f_key, val, sizeof(f_key) - 1);
            }

            char *model_ptr = strstr(line, "export INFER_MODEL=");
            if (model_ptr) {
                char *val = model_ptr + 19;
                if (*val == '"' || *val == '\'') val++;
                char *val_end = val + strlen(val) - 1;
                while (val_end > val && (*val_end == '"' || *val_end == '\'')) {
                    *val_end = '\0';
                    val_end--;
                }
                strncpy(f_model, val, sizeof(f_model) - 1);
            }
        }
        fclose(fp);
    }

    if ((!*url || !**url) && strlen(f_url) > 0) *url = f_url;
    if ((!*key || !**key) && strlen(f_key) > 0) *key = f_key;
    if ((!*model || !**model) && strlen(f_model) > 0) *model = f_model;
}

static CURLcode perform_curl_with_retry(CURL *c, struct response *chunk) {
    char *url = NULL;
    curl_easy_getinfo(c, CURLINFO_EFFECTIVE_URL, &url);
    
    int is_local = 0;
    if (url && (strstr(url, "localhost") || strstr(url, "127.0.0.1"))) {
        is_local = 1;
    }
    
    CURLcode res;
    int retries = 0;
    int max_retries = 60; // 60 retries * 500ms = 30 seconds
    
    while (1) {
        res = curl_easy_perform(c);
        if (res == CURLE_OK) {
            if (is_local && chunk && chunk->data && strstr(chunk->data, "Loading model")) {
                retries++;
                if (retries < max_retries) {
                    if (retries == 1) {
                        fprintf(stderr, "\033[2m[ai] Local server model is loading, waiting...\033[0m\n");
                        fflush(stderr);
                    }
                    free(chunk->data);
                    chunk->data = NULL;
                    chunk->size = 0;
                    usleep(500000); // 500ms
                    continue;
                }
            }
            break;
        }
        
        if (is_local && 
            (res == CURLE_COULDNT_CONNECT || 
             res == CURLE_GOT_NOTHING || 
             res == CURLE_RECV_ERROR || 
             res == CURLE_SEND_ERROR || 
             res == CURLE_OPERATION_TIMEDOUT) && 
            retries < max_retries) {
            retries++;
            if (retries == 1) {
                fprintf(stderr, "\033[2m[ai] Local server is starting up, waiting...\033[0m\n");
                fflush(stderr);
            }
            if (chunk) {
                if (chunk->data) free(chunk->data);
                chunk->data = NULL;
                chunk->size = 0;
            }
            usleep(500000); // 500ms
            continue;
        }
        
        break;
    }
    return res;
}

static int detect_context_window(CURL *c, const char *cur_api_url) {
    char models_url[1024];
    const char *chat_ptr = strstr(cur_api_url, "chat/completions");
    if (chat_ptr) {
        size_t prefix_len = chat_ptr - cur_api_url;
        snprintf(models_url, sizeof(models_url), "%.*smodels", (int)prefix_len, cur_api_url);
    } else {
        return 0;
    }
    
    struct response m_chunk = {0};
    curl_easy_setopt(c, CURLOPT_URL, models_url);
    curl_easy_setopt(c, CURLOPT_WRITEDATA, (void *)&m_chunk);
    curl_easy_setopt(c, CURLOPT_HTTPGET, 1L);
    
    CURLcode m_res = perform_curl_with_retry(c, &m_chunk);
    int detected_win = 0;
    if (m_res == CURLE_OK && m_chunk.data) {
        char *n_ctx_ptr = strstr(m_chunk.data, "\"n_ctx\"");
        if (n_ctx_ptr) {
            char *ptr = n_ctx_ptr + 7;
            while (*ptr && !isdigit((unsigned char)*ptr)) ptr++;
            if (*ptr) {
                detected_win = atoi(ptr);
            }
        }
    }
    
    if (m_chunk.data) free(m_chunk.data);
    
    // Restore Curl state
    curl_easy_setopt(c, CURLOPT_URL, cur_api_url);
    curl_easy_setopt(c, CURLOPT_HTTPGET, 0L);
    curl_easy_setopt(c, CURLOPT_POST, 1L);
    curl_easy_setopt(c, CURLOPT_WRITEDATA, NULL);
    
    return detected_win;
}

static int set_default_model(const char *new_model) {
    char detected_url[512] = "";
    if (detect_model_url(new_model, detected_url, sizeof(detected_url))) {
        printf("Detected API endpoint for %s: %s\n", new_model, detected_url);
    } else {
        printf("Could not auto-detect API endpoint for %s (leaving existing INFER_BASE_URL).\n", new_model);
    }
    
    char *home = getenv("HOME");
    if (!home) {
        fprintf(stderr, "Error: HOME environment variable not set.\n");
        return 1;
    }
    
    char bash_path[1024];
    snprintf(bash_path, sizeof(bash_path), "%s/.bashrc", home);
    int updated_any = update_config_file(bash_path, new_model, detected_url);
    
    char zsh_path[1024];
    snprintf(zsh_path, sizeof(zsh_path), "%s/.zshrc", home);
    updated_any |= update_config_file(zsh_path, new_model, detected_url);
    
    if (updated_any) {
        printf("Successfully updated default model to '%s'.\n", new_model);
        printf("Please run 'source ~/.bashrc' (or source ~/.zshrc) or restart your terminal to apply changes.\n");
    } else {
        fprintf(stderr, "Error: could not find or update .bashrc or .zshrc in %s.\n", home);
    }
    return 0;
}

/* compact_session: ask the LLM to summarise the conversation, then rebuild
   messages_json as [system, compacted-user, compacted-assistant].
   Returns a new messages_json (caller must use the returned pointer). */
static char* compact_session(char *messages_json, const char *mcp_script,
                              CURL *curl_handle, const char *model_name, int *out_success) {
    (void)mcp_script;
    if (out_success) *out_success = 0;
    fprintf(stderr, "\033[1;35m[ai] Compacting session — requesting summary...\033[0m\n");
    fflush(stderr);

    size_t orig_size = strlen(messages_json);

    const char *summary_req =
        "{\"role\":\"user\",\"content\":\"Provide a comprehensive summary of this entire "
        "conversation so far. Include: all topics discussed, decisions made, code written "
        "or modified, commands run and their results, key findings, and any pending tasks. "
        "Be thorough — this summary will replace the full conversation history.\"}";

    char *temp_msgs = strdup(messages_json);
    temp_msgs = append_message(temp_msgs, summary_req);

    char *esc_model = json_escape(model_name);
    size_t plen = strlen(esc_model) + strlen(temp_msgs) + 128;
    char *payload = malloc(plen);
    snprintf(payload, plen,
             "{\"model\":\"%s\",\"stream\":false,\"messages\":%s}",
             esc_model, temp_msgs);
    free(esc_model);
    free(temp_msgs);

    struct response chunk = {0};
    int saved_esc = g_esc_requested;
    g_esc_requested = 0;
    if (raw_mode_active) disable_raw_mode();
    g_compact_in_progress = 1;
    g_compact_dot_timer = 0;
    curl_easy_setopt(curl_handle, CURLOPT_POSTFIELDS, payload);
    curl_easy_setopt(curl_handle, CURLOPT_WRITEDATA, (void *)&chunk);
    perform_curl_with_retry(curl_handle, &chunk);
    g_compact_in_progress = 0;
    fprintf(stderr, "\n");
    g_esc_requested = saved_esc;
    free(payload);

    char *summary = NULL;
    if (chunk.data && chunk.size > 5) {
        jsmn_parser cp;
        jsmntok_t ctok[1024];
        jsmn_init(&cp);
        int cr = jsmn_parse(&cp, chunk.data, chunk.size, ctok, 1024);
        int msg_tok = -1;
        for (int i = 1; i < cr; i++) {
            if (ctok[i].type == JSMN_STRING) {
                int clen = ctok[i].end - ctok[i].start;
                if (clen == 7 && strncmp(chunk.data + ctok[i].start, "message", 7) == 0) {
                    msg_tok = i + 1;
                }
            }
        }
        if (msg_tok != -1 && ctok[msg_tok].type == JSMN_OBJECT) {
            int msg_end = ctok[msg_tok].end;
            int k = msg_tok + 1;
            while (k < cr && ctok[k].start < msg_end) {
                if (ctok[k].type == JSMN_STRING) {
                    int flen = ctok[k].end - ctok[k].start;
                    if (flen == 7 && strncmp(chunk.data + ctok[k].start, "content", 7) == 0
                        && k + 1 < cr && ctok[k+1].type == JSMN_STRING) {
                        summary = unescape_json_string(chunk.data + ctok[k+1].start,
                                                       ctok[k+1].end - ctok[k+1].start);
                        break;
                    }
                }
                k = json_skip_token(ctok, cr, k + 1);
            }
        }
    }
    if (chunk.data) free(chunk.data);

    if (!summary || strlen(summary) < 20) {
        fprintf(stderr, "[ai] Compact failed: could not extract summary from model.\n");
        if (summary) free(summary);
        return messages_json;
    }

    char *new_msgs = malloc(4096);
    strcpy(new_msgs, "[]");

    if (g_system_message_json)
        new_msgs = append_message(new_msgs, g_system_message_json);

    char *safe_sum = json_escape(summary);
    size_t cu_len = strlen(safe_sum) + 128;
    char *compact_user = malloc(cu_len);
    snprintf(compact_user, cu_len,
             "{\"role\":\"user\",\"content\":\"[Session compacted. Summary:\\n%s]\"}",
             safe_sum);
    new_msgs = append_message(new_msgs, compact_user);
    free(compact_user);
    free(safe_sum);
    free(summary);

    new_msgs = append_message(new_msgs,
        "{\"role\":\"assistant\",\"content\":\"Understood. I have the context from our "
        "previous conversation. Ready to continue.\"}");

    free(messages_json);

    fprintf(stderr, "\033[1;35m[ai] Session compacted (%.1f KB → %.1f KB).\033[0m\n",
            orig_size / 1024.0, strlen(new_msgs) / 1024.0);
    if (out_success) *out_success = 1;
    return new_msgs;
}


/* Extract summary from a Gemma-style leaked task_complete call in text content.
   Gemma 4 sometimes outputs:  task_complete{summary:<|"|>...<|"|>}
   instead of a proper JSON tool_call when tool_choice=auto.
   Returns malloc'd summary string or NULL if not a task_complete call. */
static char *extract_leaked_task_complete(const char *content) {
    if (!content) return NULL;
    const char *p = content;
    while (*p && isspace((unsigned char)*p)) p++;
    if (strncmp(p, "task_complete", 13) != 0) return NULL;
    p += 13;
    while (*p && isspace((unsigned char)*p)) p++;
    if (*p != '{') return NULL;
    p++;
    const char *sum = strstr(p, "summary:");
    if (!sum) return NULL;
    p = sum + 8;
    while (*p && isspace((unsigned char)*p)) p++;

    /* Strip <|"|> Gemma special-token quote, plain ", or take raw until } */
    const char *val_start;
    const char *val_end;
    if (strncmp(p, "<|\"|>", 5) == 0) {
        p += 5;
        val_start = p;
        val_end = strstr(p, "<|\"|>");
    } else if (*p == '"') {
        p++;
        val_start = p;
        val_end = strchr(p, '"');
    } else {
        val_start = p;
        val_end = strrchr(p, '}');
    }
    if (!val_end || val_end <= val_start) return NULL;
    size_t len = val_end - val_start;
    char *summary = malloc(len + 1);
    if (!summary) return NULL;
    memcpy(summary, val_start, len);
    summary[len] = '\0';
    return summary;
}

static void load_env_file() {
    char *home = getenv("HOME");
    if (!home) return;
    char path[1024];
    snprintf(path, sizeof(path), "%s/.local/share/ai/env", home);
    FILE *fp = fopen(path, "r");
    if (!fp) return;
    char line[1024];
    while (fgets(line, sizeof(line), fp)) {
        char *ptr = line;
        while (isspace((unsigned char)*ptr)) ptr++;
        if (*ptr == '#' || *ptr == '\0') continue;
        if (strncmp(ptr, "export ", 7) == 0) {
            ptr += 7;
        }
        while (isspace((unsigned char)*ptr)) ptr++;
        char *eq = strchr(ptr, '=');
        if (!eq) continue;
        *eq = '\0';
        char *key = ptr;
        char *val = eq + 1;
        char *key_end = key + strlen(key) - 1;
        while (key_end > key && isspace((unsigned char)*key_end)) {
            *key_end = '\0';
            key_end--;
        }
        while (isspace((unsigned char)*val)) val++;
        if (*val == '"') {
            val++;
            char *quote = strchr(val, '"');
            if (quote) *quote = '\0';
        } else if (*val == '\'') {
            val++;
            char *quote = strchr(val, '\'');
            if (quote) *quote = '\0';
        } else {
            char *val_end = val + strlen(val) - 1;
            while (val_end >= val && (isspace((unsigned char)*val_end) || *val_end == '\r' || *val_end == '\n')) {
                *val_end = '\0';
                val_end--;
            }
        }
        setenv(key, val, 1);
    }
    fclose(fp);
}

int main(int argc, char **argv) {
    load_env_file();
    char exe_path[512] = "";
    ssize_t r_exe = readlink("/proc/self/exe", exe_path, sizeof(exe_path) - 1);
    if (r_exe > 0) {
        exe_path[r_exe] = '\0';
        setenv("INFER_BIN_PATH", exe_path, 1);
    }

    int is_stdin_tty = isatty(STDIN_FILENO);
    int interactive_mode = 0;
    int quiet_mode = 0;

    // Parse set-default, install-llama, and version options first (all exit early)
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-v") == 0 || strcmp(argv[i], "--version") == 0) {
            printf("ai %s\n", AI_VERSION);
            return 0;
        }
        if (strcmp(argv[i], "--install-llama") == 0) {
            char *home = getenv("HOME");
            if (!home) { fprintf(stderr, "Error: HOME not set.\n"); return 1; }
            char script[1024];
            snprintf(script, sizeof(script), "%s/.local/bin/llama-install.sh", home);
            char *repo = (i + 1 < argc && argv[i+1][0] != '-') ? argv[i+1] : NULL;
            if (repo)
                execl("/bin/bash", "bash", script, repo, (char *)NULL);
            else
                execl("/bin/bash", "bash", script, (char *)NULL);
            perror("execl: could not run llama-install.sh");
            fprintf(stderr, "Run ./setup_llama.sh first to install the script.\n");
            return 1;
        }
        if (strcmp(argv[i], "--set-default") == 0 || strcmp(argv[i], "-s") == 0) {
            if (i + 1 < argc) {
                return set_default_model(argv[i+1]);
            } else {
                fprintf(stderr, "Error: --set-default requires a model name argument.\n");
                return 1;
            }
        }
    }

    /* deep-research sub-command: bypass LLM loop, delegate to Python orchestrator */
    if (argc >= 2 && strcmp(argv[1], "deep-research") == 0) {
        if (argc < 3) {
            fprintf(stderr, "Usage: ai deep-research \"topic\"\n");
            return 1;
        }
        char topic[4096] = {0};
        for (int i = 2; i < argc; i++) {
            if (i > 2) strncat(topic, " ", sizeof(topic) - strlen(topic) - 1);
            strncat(topic, argv[i], sizeof(topic) - strlen(topic) - 1);
        }
        char script[1024];
        if (access("./deep_research.py", R_OK) == 0) {
            snprintf(script, sizeof(script), "./deep_research.py");
        } else {
            const char *home = getenv("HOME");
            snprintf(script, sizeof(script), "%s/.local/bin/deep_research.py",
                     home ? home : "~");
        }
        char cmd[8192];
        snprintf(cmd, sizeof(cmd), "python3 %s \"%s\"", script, topic);
        return system(cmd);
    }

    // Parse help flags first
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            printf("Usage: ai [options] [\"prompt\"] [path/to/image.png]\n\n");
            printf("A minimal, agentic CLI tool for piping anything into an LLM and executing terminal work.\n\n");
            printf("Options:\n");
            printf("  -i, --interactive    Start an interactive multi-turn chat session.\n");
            printf("  -y, --yes            Auto-approve all command execution requests without prompting.\n");
            printf("  -q, --quiet          Suppress think tool reasoning output.\n");
            printf("  -n, --no-tools       Skip the agent loop — get a direct text response (fast).\n");
            printf("  -t, --temperature N  Set sampling temperature (e.g. 0.0 for deterministic, 1.0 for creative).\n");
            printf("  -f, --file PATH      Attach a file as context (text or image).\n");
            printf("  -m, --model MODEL    Override the default model for this call.\n");
            printf("  -s, --set-default M  Set the global default model in shell configs.\n");
            printf("  -v, --version        Print the build commit and exit.\n");
            printf("  --install-llama [R]  Download, build llama.cpp and start a local server.\n");
            printf("                       R: optional HuggingFace repo (e.g. unsloth/gemma-4-12b-it-GGUF).\n");
            printf("  -h, --help           Display this help screen.\n\n");
            printf("Examples:\n");
            printf("  ai \"what's the tar command to extract .tar.gz?\"\n");
            printf("  ai -n \"what is RNA?\"                 # direct answer, no tool loop\n");
            printf("  ai -t 0.2 \"write a haiku about Rust\"  # low temperature\n");
            printf("  ai -f error.log \"why is it crashing?\"\n");
            printf("  ps aux | head -n 20 | ai \"what's eating memory?\"\n");
            printf("  ai -i \"let's look at this project\"\n");
            printf("  ai -y \"backup ~/.bashrc\"\n");
            printf("  ai deep-research \"quantum computing\"  # deep multi-source research report\n");
            return 0;
        }
    }

    // Parse model, file, and no-tools flags first
    char *cmd_model = NULL;
    char *cmd_file  = NULL;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-m") == 0 || strcmp(argv[i], "--model") == 0) {
            if (i + 1 < argc) { cmd_model = argv[i+1]; i++; }
        } else if (strcmp(argv[i], "-f") == 0 || strcmp(argv[i], "--file") == 0) {
            if (i + 1 < argc) { cmd_file = argv[i+1]; i++; }
        } else if (strcmp(argv[i], "-n") == 0 || strcmp(argv[i], "--no-tools") == 0) {
            no_tools_mode = 1;
        }
    }

    // Load from Environment Variables
    char *env_url = getenv("INFER_BASE_URL");
    char *env_key = getenv("INFER_API_KEY");
    char *env_model = cmd_model ? cmd_model : getenv("INFER_MODEL");

    char *prof_url = NULL;
    char *prof_key = NULL;
    char *prof_model = NULL;
    load_from_profiles(&prof_url, &prof_key, &prof_model);

    if (!env_url || !*env_url) env_url = prof_url;
    if (!env_key || !*env_key) env_key = prof_key;
    if (!env_model || !*env_model) env_model = prof_model;

    // Always try to detect the live URL for the current model.
    // Snap-based models (gemma4, qwen3) use dynamic ports that change between runs,
    // so INFER_BASE_URL in .bashrc can be stale. Detection succeeds quickly via
    // `<model> status`; if it fails (unknown model, remote API), we fall back to env_url.
    static char detected_cmd_url[512] = "";
    if (env_model && *env_model) {
        if (detect_model_url(env_model, detected_cmd_url, sizeof(detected_cmd_url))) {
            env_url = detected_cmd_url;
        }
    }

    if (!env_url || !*env_url || !env_key || !*env_key || !env_model || !*env_model) {
        fprintf(stderr, "Error: missing required environment variables.\n");
        if (!env_url || !*env_url) fprintf(stderr, "Please set INFER_BASE_URL environment variable.\n");
        if (!env_key || !*env_key) fprintf(stderr, "Please set INFER_API_KEY environment variable.\n");
        if (!env_model || !*env_model) fprintf(stderr, "Please set INFER_MODEL environment variable or use -m/--model flag.\n");
        return 1;
    }

    // Set updated environment variables for subagents/python script
    setenv("INFER_BASE_URL", env_url, 1);
    setenv("INFER_API_KEY", env_key, 1);
    setenv("INFER_MODEL", env_model, 1);

    // Parse flags
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-i") == 0 || strcmp(argv[i], "--interactive") == 0) {
            interactive_mode = 1;
        } else if (strcmp(argv[i], "-y") == 0 || strcmp(argv[i], "--yes") == 0) {
            g_auto_approve = 1;
        } else if (strcmp(argv[i], "-q") == 0 || strcmp(argv[i], "--quiet") == 0) {
            quiet_mode = 1;
        } else if (strcmp(argv[i], "-n") == 0 || strcmp(argv[i], "--no-tools") == 0) {
            no_tools_mode = 1;
        } else if ((strcmp(argv[i], "-t") == 0 || strcmp(argv[i], "--temperature") == 0) && i + 1 < argc) {
            temperature_val = (float)atof(argv[i+1]);
            i++;
        } else if ((strcmp(argv[i], "-m") == 0 || strcmp(argv[i], "--model") == 0 ||
                    strcmp(argv[i], "-f") == 0 || strcmp(argv[i], "--file") == 0) && i + 1 < argc) {
            i++;
        }
    }

    char *env_approve = getenv("INFER_AUTO_APPROVE");
    if (env_approve && (strcmp(env_approve, "1") == 0 || strcasecmp(env_approve, "true") == 0)) {
        g_auto_approve = 1;
    }

    char *env_quiet = getenv("INFER_QUIET");
    if (env_quiet && (strcmp(env_quiet, "1") == 0 || strcasecmp(env_quiet, "true") == 0)) {
        quiet_mode = 1;
    }

    // Export resolved settings back to environment variables so subagents inherit them
    if (g_auto_approve) {
        setenv("INFER_AUTO_APPROVE", "1", 1);
    } else {
        unsetenv("INFER_AUTO_APPROVE");
    }
    if (quiet_mode) {
        setenv("INFER_QUIET", "1", 1);
    } else {
        unsetenv("INFER_QUIET");
    }

    char *env_temp = getenv("INFER_TEMPERATURE");
    if (env_temp && *env_temp) temperature_val = (float)atof(env_temp);
    char *env_maxtok = getenv("INFER_MAX_TOKENS");
    if (env_maxtok && *env_maxtok) max_tokens_val = atoi(env_maxtok);
    char *env_ctxwin = getenv("INFER_CONTEXT_WINDOW");
    if (env_ctxwin && *env_ctxwin) context_window = atoi(env_ctxwin);
    char *env_timeout = getenv("INFER_TASK_TIMEOUT");
    if (env_timeout && *env_timeout) task_timeout_sec = atoi(env_timeout);
    char *env_max_tool = getenv("INFER_MAX_TOOL_OUTPUT");
    if (env_max_tool && *env_max_tool) max_tool_output = atoi(env_max_tool);
    char *env_trim = getenv("INFER_TRIM_THRESHOLD");
    if (env_trim && *env_trim) trim_threshold = atoi(env_trim);
    char *env_stub = getenv("INFER_STUB_THRESHOLD");
    if (env_stub && *env_stub) stub_threshold = atoi(env_stub);
    const char *tool_choice_val = "required";
    char *env_tool_choice = getenv("INFER_TOOL_CHOICE");
    if (env_tool_choice && (strcmp(env_tool_choice, "auto") == 0
                         || strcmp(env_tool_choice, "required") == 0)) {
        tool_choice_val = env_tool_choice;
    }

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
    static char user_mcp_path[1024];
    const char *mcp_script = "./ai_mcp.py";
    if (access(mcp_script, F_OK) != 0) {
        char *home = getenv("HOME");
        if (home) {
            snprintf(user_mcp_path, sizeof(user_mcp_path), "%s/.local/bin/ai_mcp.py", home);
            if (access(user_mcp_path, F_OK) == 0) {
                mcp_script = user_mcp_path;
            } else {
                mcp_script = "/usr/local/bin/ai_mcp.py";
            }
        } else {
            mcp_script = "/usr/local/bin/ai_mcp.py";
        }
    }
    
    char *tools_json = NULL;
    if (!no_tools_mode) {
        char tools_cmd[2048];
        snprintf(tools_cmd, sizeof(tools_cmd), "python3 %s list-tools", mcp_script);
        tools_json = run_shell_command(tools_cmd, NULL);
        if (tools_json && (strncmp(tools_json, "Error", 5) == 0 || strlen(tools_json) < 5)) {
            free(tools_json);
            tools_json = NULL;
        }
    }

    // 1. Prepare Inputs
    char *pipe_writer = find_pipe_writer();
    char *pipe_in = read_stdin();

    // Load file context (-f / --file)
    if (cmd_file) {
        if (is_image_file(cmd_file)) {
            // Will be picked up below as image_path
        } else {
            FILE *fp = fopen(cmd_file, "r");
            if (!fp) {
                fprintf(stderr, "Error: cannot open '%s': %s\n", cmd_file, strerror(errno));
                return 1;
            }
            fseek(fp, 0, SEEK_END);
            long fsz = ftell(fp);
            rewind(fp);
            char *fbuf = malloc(fsz + 1);
            fread(fbuf, 1, fsz, fp);
            fbuf[fsz] = '\0';
            fclose(fp);
            if (pipe_in && strlen(pipe_in) > 0) {
                size_t clen = (size_t)fsz + strlen(pipe_in) + 32;
                char *combined = malloc(clen);
                snprintf(combined, clen, "%s\n\n%s", fbuf, pipe_in);
                free(pipe_in);
                free(fbuf);
                pipe_in = combined;
            } else {
                if (pipe_in) free(pipe_in);
                pipe_in = fbuf;
            }
            if (!pipe_writer) {
                const char *base = strrchr(cmd_file, '/');
                pipe_writer = strdup(base ? base + 1 : cmd_file);
            }
        }
    }

    if (interactive_mode && !is_stdin_tty) {
        if (!freopen("/dev/tty", "r", stdin)) {
            // Failed to reopen /dev/tty, disable interactive if prompt is empty
            int has_prompt_args = 0;
            for (int i = 1; i < argc; i++) {
                if (strcmp(argv[i], "-i") == 0 || strcmp(argv[i], "--interactive") == 0) continue;
                if (strcmp(argv[i], "-y") == 0 || strcmp(argv[i], "--yes") == 0) continue;
                if (strcmp(argv[i], "-q") == 0 || strcmp(argv[i], "--quiet") == 0) continue;
                if (strcmp(argv[i], "-n") == 0 || strcmp(argv[i], "--no-tools") == 0) continue;
                if (strcmp(argv[i], "-v") == 0 || strcmp(argv[i], "--version") == 0) continue;
                if ((strcmp(argv[i], "-m") == 0 || strcmp(argv[i], "--model") == 0 ||
                     strcmp(argv[i], "-t") == 0 || strcmp(argv[i], "--temperature") == 0 ||
                     strcmp(argv[i], "-f") == 0 || strcmp(argv[i], "--file") == 0) && i + 1 < argc) {
                    i++; continue;
                }
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
    char *image_path = cmd_file && is_image_file(cmd_file) ? cmd_file : NULL;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-i") == 0 || strcmp(argv[i], "--interactive") == 0) continue;
        if (strcmp(argv[i], "-y") == 0 || strcmp(argv[i], "--yes") == 0) continue;
        if (strcmp(argv[i], "-q") == 0 || strcmp(argv[i], "--quiet") == 0) continue;
        if (strcmp(argv[i], "-n") == 0 || strcmp(argv[i], "--no-tools") == 0) continue;
        if (strcmp(argv[i], "-v") == 0 || strcmp(argv[i], "--version") == 0) continue;
        if ((strcmp(argv[i], "-m") == 0 || strcmp(argv[i], "--model") == 0 ||
             strcmp(argv[i], "-t") == 0 || strcmp(argv[i], "--temperature") == 0 ||
             strcmp(argv[i], "-f") == 0 || strcmp(argv[i], "--file") == 0) && i + 1 < argc) {
            i++; continue;
        }
        if (!image_path && is_image_file(argv[i])) {
            image_path = argv[i];
            break;
        }
    }

    size_t prompt_len = 0;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-i") == 0 || strcmp(argv[i], "--interactive") == 0) continue;
        if (strcmp(argv[i], "-y") == 0 || strcmp(argv[i], "--yes") == 0) continue;
        if (strcmp(argv[i], "-q") == 0 || strcmp(argv[i], "--quiet") == 0) continue;
        if (strcmp(argv[i], "-n") == 0 || strcmp(argv[i], "--no-tools") == 0) continue;
        if (strcmp(argv[i], "-v") == 0 || strcmp(argv[i], "--version") == 0) continue;
        if ((strcmp(argv[i], "-m") == 0 || strcmp(argv[i], "--model") == 0 ||
             strcmp(argv[i], "-t") == 0 || strcmp(argv[i], "--temperature") == 0 ||
             strcmp(argv[i], "-f") == 0 || strcmp(argv[i], "--file") == 0) && i + 1 < argc) {
            i++; continue;
        }
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
        if (strcmp(argv[i], "-n") == 0 || strcmp(argv[i], "--no-tools") == 0) continue;
        if (strcmp(argv[i], "-v") == 0 || strcmp(argv[i], "--version") == 0) continue;
        if ((strcmp(argv[i], "-m") == 0 || strcmp(argv[i], "--model") == 0 ||
             strcmp(argv[i], "-t") == 0 || strcmp(argv[i], "--temperature") == 0 ||
             strcmp(argv[i], "-f") == 0 || strcmp(argv[i], "--file") == 0) && i + 1 < argc) {
            i++; continue;
        }
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
    char *triggers = load_critical_triggers();

    char *safe_system = json_escape(SYSTEM_PROMPT);
    char *safe_ctx = json_escape(sys_ctx);
    char *safe_mem = memory ? json_escape(memory) : NULL;
    char *safe_triggers = triggers ? json_escape(triggers) : NULL;
    char *sys_msg = NULL;

    /* Build the content string: SYSTEM_PROMPT + ctx + optional triggers + optional memory */
    size_t mlen = strlen(safe_system) + strlen(safe_ctx)
                  + (safe_triggers ? strlen(safe_triggers) + 64 : 0)
                  + (safe_mem ? strlen(safe_mem) + 64 : 0) + 256;

    /* Assemble piece by piece into a temporary content buffer, then JSON-wrap */
    char *content = malloc(mlen);
    int clen = snprintf(content, mlen, "%s\n\n%s", SYSTEM_PROMPT, sys_ctx);
    if (triggers && strlen(triggers) > 0)
        clen += snprintf(content + clen, mlen - clen,
                         "\n\nCRITICAL SKILL TRIGGERS (obey BEFORE any other tool):\n%s", triggers);
    if (memory && strlen(memory) > 0)
        clen += snprintf(content + clen, mlen - clen,
                         "\n\nPersistent Memory/Preferences:\n%s", memory);

    /* json_escape can expand content significantly; allocate sys_msg after escaping */
    char *safe_content = json_escape(content);
    size_t sys_msg_len = strlen(safe_content) + 64;
    sys_msg = malloc(sys_msg_len);
    snprintf(sys_msg, sys_msg_len,
             "{\"role\":\"system\",\"content\":\"%s\"}", safe_content);
    free(content);
    free(safe_content);

    messages_json = append_message(messages_json, sys_msg);
    g_system_message_json = strdup(sys_msg); /* saved for compact_session */

    if (safe_triggers) free(safe_triggers);
    if (safe_mem) free(safe_mem);
    if (triggers) free(triggers);
    if (memory) free(memory);
    free(sys_ctx);
    free(safe_system);
    free(safe_ctx);
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
    char auth[MAX_VAL + 64]; snprintf(auth, sizeof(auth), "Authorization: Bearer %s", api_key);
    h = curl_slist_append(h, "Content-Type: application/json");
    h = curl_slist_append(h, auth);

    curl_easy_setopt(c, CURLOPT_URL, api_url);
    curl_easy_setopt(c, CURLOPT_HTTPHEADER, h);
    curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, write_cb);
    curl_easy_setopt(c, CURLOPT_NOPROGRESS, 0L);
    curl_easy_setopt(c, CURLOPT_XFERINFOFUNCTION, curl_progress_cb);

    if (context_window == 0) {
        context_window = detect_context_window(c, api_url);
    }

    int debug_mode = getenv("INFER_DEBUG") != NULL;
    int keep_going = 1;
    int first_turn = 1;
    char *current_prompt = strdup(prompt ? prompt : "");

    if (interactive_mode && !run_query_this_turn) {
        printf("\033[1;36mai\033[0m  \033[2m%s\033[0m\n", model);
        printf("\033[2m:help · ESC to interrupt · Shift-Tab to disable auto-approve · :btw <msg> to inject a note mid-task\033[0m\n\n");
        lineed_init();
    }

    while (keep_going) {
        if (interactive_mode && (!run_query_this_turn || !first_turn)) {
            /* Auto-compact when context grows very large */
            if (strlen(messages_json) > (size_t)(trim_threshold * 3)) {
                fprintf(stderr,
                    "\n\033[1;33m[ai] Context is large (%zu KB). Auto-compacting...\033[0m\n",
                    strlen(messages_json) / 1024);
                messages_json = compact_session(messages_json, mcp_script, c, model, NULL);
            }

            printf("\n");
            fflush(stdout);
            const char *prompt_str = g_auto_approve
                ? "\033[1;33mai\033[0m\033[2m>\033[0m "
                : "\033[1;36mai\033[0m\033[2m>\033[0m ";
            char *line = read_line_interactive(prompt_str);
            if (!line) {
                printf("\n");
                break;
            }

            char user_input[4096];
            strncpy(user_input, line, sizeof(user_input) - 1);
            user_input[sizeof(user_input) - 1] = '\0';
            free(line);

            /* Shift-Tab — returned as sentinel by read_line_interactive */
            if (user_input[0] == '\033' && user_input[1] == '[' && user_input[2] == 'Z') {
                g_auto_approve ^= 1;
                if (g_auto_approve)
                    setenv("INFER_AUTO_APPROVE", "1", 1);
                else
                    unsetenv("INFER_AUTO_APPROVE");
                printf("\033[2mauto-approve %s\033[0m\n",
                       g_auto_approve ? "on" : "off");
                run_query_this_turn = 0;
                continue;
            }

            size_t len = strlen(user_input);

            if (strcmp(user_input, "exit") == 0 || strcmp(user_input, "quit") == 0) {
                break;
            }

            if (len == 0) {
                continue;
            }

            /* ── Interactive slash/colon commands ── */
            if (user_input[0] == ':') {
                if (strcmp(user_input, ":quit") == 0 || strcmp(user_input, ":exit") == 0) {
                    break;
                }
                if (strcmp(user_input, ":compact") == 0) {
                    int compact_ok = 0;
                    messages_json = compact_session(messages_json, mcp_script, c, model, &compact_ok);
                    if (compact_ok)
                        printf("\033[2m[context compacted — continue the conversation]\033[0m\n");
                    run_query_this_turn = 0;
                    continue;
                }
                if (strcmp(user_input, ":clear") == 0) {
                    char *fresh = malloc(4096);
                    strcpy(fresh, "[]");
                    if (g_system_message_json)
                        fresh = append_message(fresh, g_system_message_json);
                    free(messages_json);
                    messages_json = fresh;
                    printf("\033[2m[conversation cleared — starting fresh]\033[0m\n");
                    run_query_this_turn = 0;
                    continue;
                }
                if (strcmp(user_input, ":help") == 0) {
                    printf("\n\033[1;36mInteractive commands:\033[0m\n");
                    printf("  :compact       Summarise + reset context (keeps semantic history)\n");
                    printf("  :clear         Wipe conversation history entirely\n");
                    printf("  :status        Show context size and model info\n");
                    printf("  :memory        Show persistent memory\n");
                    printf("  :auto          Toggle auto-approve for execute_command\n");
                    printf("  :btw <msg>     Inject a note into the agent context mid-task\n");
                    printf("  :help          Show this message\n");
                    printf("  :quit/:exit    Leave interactive mode\n");
                    printf("  exit/quit      Leave interactive mode\n");
                    printf("\n\033[2mPress Ctrl+C or ESC to interrupt. "
                           "Shift-Tab to disable auto-approve (enable only at prompt).\033[0m\n\n");
                    run_query_this_turn = 0;
                    continue;
                }
                if (strcmp(user_input, ":status") == 0) {
                    size_t ctx_bytes = strlen(messages_json);
                    printf("\n\033[1;36mSession status:\033[0m\n");
                    printf("  Model          : %s\n", model);
                    printf("  Context size   : %zu KB\n", ctx_bytes / 1024);
                    if (context_window > 0)
                        printf("  Context window : %d tokens\n", context_window);
                    printf("  Trim threshold : %d bytes\n", trim_threshold);
                    printf("  Auto-compact at: %d bytes (~%.0f KB)\n",
                           trim_threshold * 3, trim_threshold * 3.0 / 1024);
                    printf("  :compact needed: %s\n",
                           ctx_bytes > (size_t)(trim_threshold * 2) ? "YES (recommended)" : "no");
                    printf("  Auto-approve   : %s\n\n",
                           g_auto_approve ? "\033[1;33mON\033[0m (Shift-Tab to disable)" : "off");
                    run_query_this_turn = 0;
                    continue;
                }
                if (strcmp(user_input, ":auto") == 0) {
                    g_auto_approve ^= 1;
                    if (g_auto_approve)
                        setenv("INFER_AUTO_APPROVE", "1", 1);
                    else
                        unsetenv("INFER_AUTO_APPROVE");
                    printf("\033[2mauto-approve %s\033[0m\n",
                           g_auto_approve ? "on" : "off");
                    run_query_this_turn = 0;
                    continue;
                }
                if (strcmp(user_input, ":memory") == 0) {
                    char *mem = read_memory_file();
                    if (mem && strlen(mem) > 0)
                        printf("\n\033[1;36mPersistent memory:\033[0m\n%s\n\n", mem);
                    else
                        printf("\033[2m[no persistent memory saved yet]\033[0m\n");
                    if (mem) free(mem);
                    run_query_this_turn = 0;
                    continue;
                }
            } /* end colon commands */

            lineed_add_history(user_input);

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
            int step_limit = 30;
            g_esc_requested = 0;
            g_agent_loop_active = 1;
            struct timespec task_start;
            clock_gettime(CLOCK_MONOTONIC, &task_start);

step_limit_check:
            while (has_more && loop_count < step_limit) {
                loop_count++;

                messages_json = maybe_trim_messages(messages_json, mcp_script);

                /* Inject any :btw note typed by the user during the previous iteration */
                if (g_btw_available && !g_esc_requested) {
                    g_btw_available = 0;
                    char *safe_btw = json_escape(g_btw_message);
                    size_t btw_len = strlen(safe_btw) + 80;
                    char *btw_msg = malloc(btw_len);
                    snprintf(btw_msg, btw_len,
                             "{\"role\":\"user\",\"content\":\"[User note mid-task: %s]\"}",
                             safe_btw);
                    messages_json = append_message(messages_json, btw_msg);
                    fprintf(stderr, "\033[2m[btw] injected: %s\033[0m\n", g_btw_message);
                    fflush(stderr);
                    free(safe_btw);
                    free(btw_msg);
                }

                /* Build optional parameter fields */
                char opt_fields[128] = "";
                int opt_len = 0;
                if (temperature_val >= 0.0f)
                    opt_len += snprintf(opt_fields + opt_len, (int)sizeof(opt_fields) - opt_len,
                                        ",\"temperature\":%.2f", temperature_val);
                if (max_tokens_val > 0)
                    opt_len += snprintf(opt_fields + opt_len, (int)sizeof(opt_fields) - opt_len,
                                        ",\"max_tokens\":%d", max_tokens_val);

                char *esc_model = json_escape(model);
                char *payload = NULL;
                size_t plen = strlen(esc_model) + strlen(messages_json) + (tools_json ? strlen(tools_json) : 0) + 512;
                payload = malloc(plen);
                if (tools_json && strlen(tools_json) > 10) {
                    snprintf(payload, plen, "{\"model\":\"%s\",\"stream\":false%s,\"messages\":%s,\"tools\":%s,\"tool_choice\":\"%s\"}",
                             esc_model, opt_fields, messages_json, tools_json, tool_choice_val);
                } else {
                    snprintf(payload, plen, "{\"model\":\"%s\",\"stream\":false%s,\"messages\":%s}",
                             esc_model, opt_fields, messages_json);
                }
                free(esc_model);

                if (debug_mode) {
                    fprintf(stderr, "[debug] Loop %d payload: %s\n", loop_count, payload);
                }

                struct response chunk = {0};
                curl_easy_setopt(c, CURLOPT_POSTFIELDS, payload);
                curl_easy_setopt(c, CURLOPT_WRITEDATA, (void *)&chunk);

                g_esc_requested = 0;
                if (interactive_mode) enable_raw_mode();
                struct timespec t_req_start, t_req_end;
                clock_gettime(CLOCK_MONOTONIC, &t_req_start);
                CURLcode res = perform_curl_with_retry(c, &chunk);
                clock_gettime(CLOCK_MONOTONIC, &t_req_end);
                if (interactive_mode) disable_raw_mode();
                double elapsed_sec = (t_req_end.tv_sec  - t_req_start.tv_sec) +
                                     (t_req_end.tv_nsec - t_req_start.tv_nsec) * 1e-9;

                if (res != CURLE_OK && (res == CURLE_COULDNT_CONNECT || res == CURLE_COULDNT_RESOLVE_HOST || res == CURLE_OPERATION_TIMEDOUT)) {
                    char *prof_url = NULL;
                    char *prof_key = NULL;
                    char *prof_model = NULL;
                    load_from_profiles(&prof_url, &prof_key, &prof_model);
                    
                    if (prof_url && strlen(prof_url) > 0) {
                        char prof_api_url[1024];
                        const char *comp_path = "chat/completions";
                        size_t p_len = strlen(prof_url);
                        int p_needs_slash = p_len > 0 && prof_url[p_len - 1] != '/';
                        snprintf(prof_api_url, sizeof(prof_api_url), "%s%s%s", prof_url, p_needs_slash ? "/" : "", comp_path);
                        
                        if (strcmp(prof_api_url, api_url) != 0) {
                            if (debug_mode) {
                                fprintf(stderr, "Warning: Connection to environment endpoint %s failed.\n", api_url);
                                fprintf(stderr, "Attempting connection to profile default endpoint %s (model: %s)...\n", prof_api_url, prof_model ? prof_model : "unknown");
                            }
                            
                            strcpy(api_url, prof_api_url);
                            if (prof_key) strcpy(api_key, prof_key);
                            if (prof_model) strcpy(model, prof_model);
                            
                            setenv("INFER_BASE_URL", prof_url, 1);
                            if (prof_key) setenv("INFER_API_KEY", prof_key, 1);
                            if (prof_model) setenv("INFER_MODEL", prof_model, 1);
                            
                            curl_easy_setopt(c, CURLOPT_URL, api_url);
                            
                            char new_auth[MAX_VAL + 64];
                            snprintf(new_auth, sizeof(new_auth), "Authorization: Bearer %s", api_key);
                            curl_slist_free_all(h);
                            h = NULL;
                            h = curl_slist_append(h, "Content-Type: application/json");
                            h = curl_slist_append(h, new_auth);
                            curl_easy_setopt(c, CURLOPT_HTTPHEADER, h);
                            
                            if (context_window == 0) {
                                context_window = detect_context_window(c, api_url);
                            }
                            curl_easy_setopt(c, CURLOPT_WRITEDATA, (void *)&chunk);
                            
                            free(payload);
                            size_t new_plen = strlen(model) + strlen(messages_json) + (tools_json ? strlen(tools_json) : 0) + 512;
                            payload = malloc(new_plen);
                            if (tools_json && strlen(tools_json) > 10) {
                                snprintf(payload, new_plen, "{\"model\":\"%s\",\"stream\":false%s,\"messages\":%s,\"tools\":%s,\"tool_choice\":\"%s\"}",
                                         model, opt_fields, messages_json, tools_json, tool_choice_val);
                            } else {
                                snprintf(payload, new_plen, "{\"model\":\"%s\",\"stream\":false%s,\"messages\":%s}",
                                         model, opt_fields, messages_json);
                            }
                            curl_easy_setopt(c, CURLOPT_POSTFIELDS, payload);
                            
                            if (chunk.data) {
                                free(chunk.data);
                                chunk.data = NULL;
                                chunk.size = 0;
                            }
                            
                            g_esc_requested = 0;
                            if (interactive_mode) enable_raw_mode();
                            clock_gettime(CLOCK_MONOTONIC, &t_req_start);
                            res = perform_curl_with_retry(c, &chunk);
                            clock_gettime(CLOCK_MONOTONIC, &t_req_end);
                            if (interactive_mode) disable_raw_mode();
                            elapsed_sec = (t_req_end.tv_sec  - t_req_start.tv_sec) +
                                          (t_req_end.tv_nsec - t_req_start.tv_nsec) * 1e-9;
                        }
                    }
                }

                /* ESC pressed during LLM request */
                if (g_esc_requested) {
                    fprintf(stderr, "\n\033[1;31m[ai] Interrupted by user (ESC).\033[0m\n");
                    free(payload);
                    if (chunk.data) free(chunk.data);
                    has_more = 0;
                    break;
                }

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
                jsmntok_t tok[4096];
                jsmn_init(&p);
                int r = jsmn_parse(&p, chunk.data, chunk.size, tok, 4096);

                if (r < 0) {
                    if (r == JSMN_ERROR_NOMEM)
                        fprintf(stderr, "[ai] Error: response JSON exceeds token buffer "
                                        "(>4096 tokens). Increase jsmntok_t array in ai.c.\n");
                    else
                        fprintf(stderr, "Failed to parse JSON response: %d\n", r);
                    free(payload);
                    free(chunk.data);
                    break;
                }

                int finish_reason_tok = -1;
                int message_tok = -1;
                int tool_calls_tok = -1;
                int usage_tok = -1;
                int finish_reason_length = 0;

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

                if (finish_reason_tok != -1) {
                    int flen = tok[finish_reason_tok].end - tok[finish_reason_tok].start;
                    if (flen == 6 && strncmp(chunk.data + tok[finish_reason_tok].start, "length", 6) == 0)
                        finish_reason_length = 1;
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
                /* Always honour tool_calls if present and non-empty, regardless of finish_reason */
                if (!should_call_tools && tool_calls_tok != -1
                        && tok[tool_calls_tok].type == JSMN_ARRAY
                        && tok[tool_calls_tok].size > 0) {
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
                              char *unescaped_args;
                              if (tok[args_tok].type == JSMN_STRING) {
                                  unescaped_args = unescape_json_string(
                                      chunk.data + tok[args_tok].start,
                                      tok[args_tok].end - tok[args_tok].start);
                              } else {
                                  int alen = tok[args_tok].end - tok[args_tok].start;
                                  unescaped_args = malloc(alen + 1);
                                  memcpy(unescaped_args, chunk.data + tok[args_tok].start, alen);
                                  unescaped_args[alen] = '\0';
                              }

                              char *tool_output = NULL;

                               if (strcmp(unescaped_name, "think") == 0) {
                                   jsmn_parser arg_parser;
                                   jsmntok_t arg_toks[256];
                                   jsmn_init(&arg_parser);
                                   int arg_r = jsmn_parse(&arg_parser, unescaped_args, strlen(unescaped_args), arg_toks, 256);
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
                                       fprintf(stderr, "\033[2m[thinking] %s\033[0m\n", reasoning);
                                       fflush(stderr);
                                   }
                                   if (reasoning) free(reasoning);
                                   tool_output = strdup("{\"ok\":true}");
                               } else if (strcmp(unescaped_name, "task_complete") == 0) {
                                   jsmn_parser arg_parser;
                                   jsmntok_t arg_toks[2048];
                                   jsmn_init(&arg_parser);
                                   int arg_r = jsmn_parse(&arg_parser, unescaped_args, strlen(unescaped_args), arg_toks, 2048);
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
                                       size_t rcmd1_len = strlen(mcp_script) + strlen(escaped_summary) + 32;
                                       char *render_cmd = malloc(rcmd1_len);
                                       snprintf(render_cmd, rcmd1_len, "python3 %s render-markdown %s", mcp_script, escaped_summary);
                                       char *rendered = run_shell_command(render_cmd, NULL);
                                       free(render_cmd);
                                       fflush(stderr);
                                       printf("\n\033[2m%s\033[0m\n\n", "────────────────────────────────────────────");
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
                                   jsmntok_t arg_toks[512];
                                   jsmn_init(&arg_parser);
                                   int arg_r = jsmn_parse(&arg_parser, unescaped_args, strlen(unescaped_args), arg_toks, 512);
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
                                      int approved = g_auto_approve;
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

                                          if (exit_code == 130) /* ESC / SIGINT during command */
                                              g_esc_requested = 1;

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
                                      /* Count tasks array entries for a useful log line */
                                      int ntasks = 0;
                                      const char *tp = strstr(unescaped_args, "\"tasks\"");
                                      if (tp) {
                                          const char *arr = strchr(tp, '[');
                                          if (arr) {
                                              arr++;
                                              while (*arr) {
                                                  while (*arr && isspace((unsigned char)*arr)) arr++;
                                                  if (*arr == '"') { ntasks++; /* skip to close */ arr++; while (*arr && !(*arr == '"' && *(arr-1) != '\\')) arr++; if (*arr) arr++; }
                                                  else if (*arr == ']') break;
                                                  else if (*arr == ',') arr++;
                                                  else arr++;
                                              }
                                          }
                                      }
                                      if (ntasks > 0)
                                          fprintf(stderr, "\033[2m[ai] delegate_task: %d parallel agent(s)\033[0m\n", ntasks);
                                      else
                                          fprintf(stderr, "\033[2m[ai] delegate_task\033[0m\n");
                                  } else if (strcmp(mcp_tool_name, "parallel_fetch") == 0) {
                                      /* Count urls array entries */
                                      int nurls = 0;
                                      const char *up = strstr(unescaped_args, "\"urls\"");
                                      if (up) {
                                          const char *arr = strchr(up, '[');
                                          if (arr) { arr++; while (*arr) { while (*arr && isspace((unsigned char)*arr)) arr++; if (*arr == '"') { nurls++; arr++; while (*arr && !(*arr == '"' && *(arr-1) != '\\')) arr++; if (*arr) arr++; } else if (*arr == ']') break; else if (*arr == ',') arr++; else arr++; } }
                                      }
                                      fprintf(stderr, "\033[2m[ai] parallel_fetch: %d URL(s)\033[0m\n", nurls);
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
                              if ((int)strlen(tool_output) > max_tool_output) {
                                  size_t orig_len = strlen(tool_output);
                                  char suffix[350];
                                  snprintf(suffix, sizeof(suffix), 
                                           "\n\n... [TRUNCATED: Tool output was %zu bytes. Capped at %d bytes. The model should decide if it wants to use a different tool/command to narrow down the query (e.g. grep, find, head/tail, line-range read_file), or run the command with pagination, or proceed with the truncated context.]", 
                                           orig_len, max_tool_output);
                                  size_t suffix_len = strlen(suffix);
                                  char *capped = malloc(max_tool_output + suffix_len + 1);
                                  if (capped) {
                                      memcpy(capped, tool_output, max_tool_output);
                                      strcpy(capped + max_tool_output, suffix);
                                      free(tool_output);
                                      tool_output = capped;
                                  }
                              }

                              /* If total context is already large, stub this result */
                              if ((int)strlen(messages_json) > stub_threshold) {
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

                      /* Poll stdin for Shift-Tab / :btw typed while tools were executing */
                      if (interactive_mode) poll_agent_stdin();

                      /* ESC pressed during command execution — stop agent loop */
                      if (g_esc_requested) {
                          fprintf(stderr, "\n\033[1;31m[ai] Task interrupted (ESC). Returning to prompt.\033[0m\n");
                          has_more = 0;
                      }
                  } else {
                      if (finish_reason_length) {
                          fprintf(stderr, "\033[1;33m[ai] Warning: model hit token limit — "
                                          "response truncated. Nudging to complete.\033[0m\n");
                          messages_json = append_message(messages_json,
                              "{\"role\":\"user\",\"content\":\"Your last response was cut off "
                              "by the token limit. Call task_complete now with your current "
                              "best answer.\"}");
                          has_more = 1;
                      } else {
                      has_more = 0;

                      int content_tok = -1;
                      int reasoning_content_tok = -1;
                      if (message_tok != -1) {
                          int msg_end = tok[message_tok].end;
                          int k = message_tok + 1;
                          while (k < r && tok[k].start < msg_end) {
                              if (tok[k].type == JSMN_STRING) {
                                  int flen = tok[k].end - tok[k].start;
                                  if (flen == 7 && strncmp(chunk.data + tok[k].start, "content", 7) == 0) {
                                      content_tok = k + 1;
                                  } else if (flen == 17 && strncmp(chunk.data + tok[k].start, "reasoning_content", 17) == 0) {
                                      reasoning_content_tok = k + 1;
                                  }
                              }
                              k = json_skip_token(tok, r, k + 1);
                          }
                      }

                      if (!quiet_mode && reasoning_content_tok != -1 && tok[reasoning_content_tok].type == JSMN_STRING) {
                          char *unescaped_reasoning = unescape_json_string(chunk.data + tok[reasoning_content_tok].start, tok[reasoning_content_tok].end - tok[reasoning_content_tok].start);
                          if (unescaped_reasoning && strlen(unescaped_reasoning) > 0) {
                              fprintf(stderr, "\033[2m[thinking]\n%s\033[0m\n", unescaped_reasoning);
                              fflush(stderr);
                          }
                          free(unescaped_reasoning);
                      }

                      if (content_tok != -1 && tok[content_tok].type == JSMN_STRING) {
                          char *unescaped_content = unescape_json_string(chunk.data + tok[content_tok].start, tok[content_tok].end - tok[content_tok].start);

                          /* Detect Gemma-style leaked task_complete in text output */
                          char *leaked_summary = extract_leaked_task_complete(unescaped_content);
                          if (leaked_summary) {
                              log_job(current_prompt, pipe_writer, leaked_summary, interactive_mode);
                              char *esc_sum = shell_escape(leaked_summary);
                              size_t rcmd_len = strlen(mcp_script) + strlen(esc_sum) + 32;
                              char *render_cmd = malloc(rcmd_len);
                              snprintf(render_cmd, rcmd_len, "python3 %s render-markdown %s", mcp_script, esc_sum);
                              char *rendered = run_shell_command(render_cmd, NULL);
                              free(render_cmd);
                              fflush(stderr);
                              printf("\n\033[2m%s\033[0m\n\n", "────────────────────────────────────────────");
                              if (rendered) { printf("%s\n", rendered); free(rendered); }
                              else { printf("%s\n", leaked_summary); }
                              free(esc_sum);
                              free(leaked_summary);
                              free(unescaped_content);
                              has_more = 0;
                              goto next_turn;
                          }

                          log_job(current_prompt, pipe_writer, unescaped_content, interactive_mode);
                          char *escaped_content = shell_escape(unescaped_content);
                          size_t rcmd2_len = strlen(mcp_script) + strlen(escaped_content) + 32;
                          char *render_cmd2 = malloc(rcmd2_len);
                          snprintf(render_cmd2, rcmd2_len, "python3 %s render-markdown %s", mcp_script, escaped_content);
                          char *rendered_output = run_shell_command(render_cmd2, NULL);
                          free(render_cmd2);

                          fflush(stderr);
                          printf("\n\033[2m%s\033[0m\n\n", "────────────────────────────────────────────");
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
                      int is_content_empty = 1;
                      if (content_tok != -1 && tok[content_tok].type == JSMN_STRING) {
                          char *unescaped_content = unescape_json_string(chunk.data + tok[content_tok].start, tok[content_tok].end - tok[content_tok].start);
                          if (unescaped_content) {
                              for (size_t idx = 0; idx < strlen(unescaped_content); idx++) {
                                  if (!isspace((unsigned char)unescaped_content[idx])) {
                                      is_content_empty = 0;
                                      break;
                                  }
                              }
                              free(unescaped_content);
                          }
                      }
                      if (is_content_empty && loop_count < 28) {
                          const char *nudge = "{\"role\":\"user\",\"content\":\"Please call task_complete with your final answer.\"}";
                          messages_json = append_message(messages_json, nudge);
                          has_more = 1;
                      }
                      } /* end !finish_reason_length else */
                  }

                  /* Usage / speed stats line */
                  if (!quiet_mode && (prompt_tokens > 0 || completion_tokens > 0)) {
                      double tps = (elapsed_sec > 0.05 && completion_tokens > 0)
                                   ? completion_tokens / elapsed_sec : 0.0;
                      if (context_window > 0) {
                          int pct = (int)(100.0 * total_tokens / context_window);
                          fprintf(stderr,
                              "\n\033[2m[loop %d | ctx %d/%d (%d%%) | +%d tok | %.0f tok/s]\033[0m\n",
                              loop_count, total_tokens, context_window, pct,
                              completion_tokens, tps);
                      } else {
                          fprintf(stderr,
                              "\n\033[2m[loop %d | %d ctx tok | +%d new | %.0f tok/s]\033[0m\n",
                              loop_count, prompt_tokens, completion_tokens, tps);
                      }
                  }

                  /* Task timeout check: if total elapsed > INFER_TASK_TIMEOUT, force one final iteration */
                  if (task_timeout_sec > 0 && has_more) {
                      struct timespec t_now;
                      clock_gettime(CLOCK_MONOTONIC, &t_now);
                      double task_elapsed = (t_now.tv_sec  - task_start.tv_sec) +
                                            (t_now.tv_nsec - task_start.tv_nsec) * 1e-9;
                      if (task_elapsed > (double)task_timeout_sec) {
                          fprintf(stderr,
                              "\033[1;33m[ai] task timeout (%.0fs / %ds limit). Forcing task_complete.\033[0m\n",
                              task_elapsed, task_timeout_sec);
                          messages_json = append_message(messages_json,
                              "{\"role\":\"user\",\"content\":\"[TIMEOUT] Maximum task duration reached. You MUST call task_complete NOW with your current best answer. No more tool calls.\"}");
                          loop_count = 28; /* allow exactly one more iteration */
                      }
                  }

                  next_turn:
                  free(payload);
                  free(chunk.data);
              } /* end inner while */

            /* Step-limit: ask user whether to continue */
            if (has_more && loop_count >= step_limit) {
                FILE *tty_f = fopen("/dev/tty", "r+");
                if (tty_f) {
                    fprintf(tty_f,
                        "\n\033[1;33m[ai] Agent has taken %d steps. Continue for 30 more? [Y/n]: \033[0m",
                        loop_count);
                    fflush(tty_f);
                    char cont_resp[64] = {0};
                    int user_continue = 0;
                    if (fgets(cont_resp, sizeof(cont_resp), tty_f)) {
                        char *cr = cont_resp;
                        while (*cr && isspace((unsigned char)*cr)) cr++;
                        if (*cr == '\0' || *cr == 'y' || *cr == 'Y'
                            || strncasecmp(cr, "yes", 3) == 0)
                            user_continue = 1;
                    }
                    fclose(tty_f);
                    if (user_continue) {
                        step_limit += 30;
                        goto step_limit_check;
                    }
                }
                fprintf(stderr,
                    "\033[1;33m[ai] Task stopped by user after %d steps.\033[0m\n",
                    loop_count);
                has_more = 0;
            }
            g_agent_loop_active = 0;
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
    if (g_system_message_json) free(g_system_message_json);
    curl_slist_free_all(h);
    curl_easy_cleanup(c);
    return 0;
}
