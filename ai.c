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
#include <poll.h>
#include "remote_harness.h"

/* ── Remote Server State ───────────────────────────────────────────────────── */
static remote_server_t *g_remote_server = NULL;
static int g_remote_discovered = 0;
#include <signal.h>

#define MAX_LINE 1024
#define MAX_VAL  2048

#ifndef AI_VERSION
#define AI_VERSION "dev"
#endif

/* ──────────────────────────────────────────────────────────────────────────
 *  Visual style helpers — modern CLI output with ANSI colors & box drawing
 * ────────────────────────────────────────────────────────────────────────── */

/* ANSI palette */
#define CL_RESET   "\033[0m"
#define CL_BOLD    "\033[1m"
#define CL_DIM     "\033[2m"
#define CL_DIM2    "\033[22m"  /* normal intensity (undo bold) */
#define CL_UNDER   "\033[4m"
#define CL_MAGENTA "\033[35m"
#define CL_CYAN    "\033[36m"
#define CL_GREEN   "\033[32m"
#define CL_RED     "\033[31m"
#define CL_YELLOW  "\033[33m"
#define CL_BLUE    "\033[34m"
#define CL_WHITE   "\033[37m"
#define CL_BG_MAG  "\033[45m"
#define CL_ORANGE  "\033[38;5;208m"
#define CL_TEAL    "\033[38;5;37m"
#define CL_PURPLE  "\033[38;5;135m"
#define CL_BG_DARK "\033[48;5;236m"
#define CL_BG_MED  "\033[48;5;241m"

/* Box-drawing characters */
#define BTLN "\xe2\x95\xad"   /* ╭ top-left corner  */
#define BTRN "\xe2\x95\xae"   /* ╮ top-right corner */
#define BBLN "\xe2\x95\xb0"   /* ╰ bottom-left      */
#define BBBN "\xe2\x95\xb1"   /* ╯ bottom-right     */
#define BVRT "\xe2\x94\x82"   /* │ vertical         */
#define BTHR "\xe2\x94\x80"   /* ─ horizontal       */
#define BTRR "┤"   /* right T-junction   */
#define BTRL "├"   /* left T-junction    */
#define BCTR "┼"   /* cross              */

/* ── Tool-type metadata: icon + color per tool family ─────────────────── */
typedef struct {
    const char *name;
    const char *icon;
    const char *color;
} tool_meta_t;

static const tool_meta_t g_tool_meta[] = {
    {"execute_command",     "⬡",  CL_CYAN},
    {"read_file",           "⌨",  CL_ORANGE},
    {"write_file",          "⌨",  CL_ORANGE},
    {"edit_file",           "⌨",  CL_ORANGE},
    {"fetch_webpage",       "◉",  CL_BLUE},
    {"fetch_webpage_js",    "◉",  CL_BLUE},
    {"fetch_smart",         "◉",  CL_BLUE},
    {"web_search",          "⌕",  CL_GREEN},
    {"arxiv_search",        "⊹",  CL_PURPLE},
    {"pubmed_search",       "⊹",  CL_PURPLE},
    {"gcal_list_events",    "⊡",  CL_TEAL},
    {"gcal_create_event",   "⊡",  CL_TEAL},
    {"gcal_update_event",   "⊡",  CL_TEAL},
    {"gcal_delete_event",   "⊡",  CL_TEAL},
    {"delegate_task",       "⊞",  CL_MAGENTA},
    {"parallel_fetch",      "⊞",  CL_MAGENTA},
    {"schedule_task",       "⊞",  CL_MAGENTA},
    {"unschedule_task",     "⊞",  CL_MAGENTA},
    {"scientific__",        "⟨⟩", CL_PURPLE},
    {"vault_",              "⊞",  CL_PURPLE},
    {"load_skill",          "⊞",  CL_PURPLE},
    {"check_time",          "◷",  CL_CYAN},
    {"get_clipboard",       "⎘",  CL_DIM},
    {"list_processes",      "⬡",  CL_CYAN},
    {"list_scheduled_tasks","⊞",  CL_MAGENTA},
    {"start_background_process","⊞", CL_MAGENTA},
    {"stop_process",        "⊞",  CL_MAGENTA},
    {"check_process_status","⊞",  CL_MAGENTA},
    {NULL, NULL, NULL}
};

static const char *tool_get_icon(const char *name) {
    if (!name) return "⚡";
    for (int i = 0; g_tool_meta[i].name; i++) {
        if (strcmp(g_tool_meta[i].name, name) == 0)
            return g_tool_meta[i].icon;
    }
    if (strstr(name, "search") || strstr(name, "find")) return "⌕";
    if (strstr(name, "fetch") || strstr(name, "web")) return "◉";
    if (strstr(name, "file") || strstr(name, "read") || strstr(name, "write")) return "⌨";
    if (strstr(name, "task") || strstr(name, "process") || strstr(name, "job")) return "⬡";
    if (strstr(name, "__") || strstr(name, "mcp")) return "⟨⟩";
    return "⚡";
}

static const char *tool_get_color(const char *name) {
    if (!name) return CL_CYAN;
    for (int i = 0; g_tool_meta[i].name; i++) {
        if (strcmp(g_tool_meta[i].name, name) == 0)
            return g_tool_meta[i].color;
    }
    if (strstr(name, "search") || strstr(name, "find")) return CL_GREEN;
    if (strstr(name, "fetch") || strstr(name, "web")) return CL_BLUE;
    if (strstr(name, "file") || strstr(name, "read") || strstr(name, "write")) return CL_ORANGE;
    if (strstr(name, "task") || strstr(name, "process")) return CL_MAGENTA;
    if (strstr(name, "__") || strstr(name, "mcp")) return CL_PURPLE;
    return CL_CYAN;
}

/* ── Forward declaration for run_shell_command (used by render_markdown) */
char* run_shell_command(const char *cmd, int *exit_status);

/* Render markdown via the Python helper (used by task_complete and model output).
 * Returns a dynamically allocated string or NULL on failure. Caller must free. */
static char* shell_escape(const char *src);

static char *render_markdown(const char *text)
{
    if (!text || !*text) return NULL;

    const char *home = getenv("HOME");
    char script[1024];
    if (access("./ai_mcp.py", R_OK) == 0)
        snprintf(script, sizeof(script), "./ai_mcp.py");
    else
        snprintf(script, sizeof(script), "%s/.local/bin/ai_mcp.py",
                 home ? home : "~");

    char *safe_text = shell_escape(text);
    if (!safe_text) return NULL;

    size_t cmd_len = strlen(script) + strlen(safe_text) + 64;
    char *cmd = malloc(cmd_len);
    snprintf(cmd, cmd_len, "python3 %s render-markdown %s 2>/dev/null", script, safe_text);
    char *result = run_shell_command(cmd, NULL);
    free(safe_text);
    free(cmd);
    return result;
}

static int lineed_term_cols(void);

/* ── Monotonic clock helper for precise tool & turn timing ── */
static double get_time_sec_mono(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}
char current_session_id[64] = "";
static int  g_hide_details = 0;
static int  g_permission_mode = 0;
static int  g_plan_approved = 0;   /* plan mode: user approved the presented plan */
static int  g_plan_budget = 8;       /* INFER_PLAN_STEP_BUDGET: state-changing actions per plan approval (<=0 = unlimited) */
static int  g_plan_remaining = 0;    /* credits left for the currently approved plan */
static char *g_plan_budget_note = NULL; /* held until a mutating tool's output is finalized */

/* ── Turn history storage for dynamic screen redrawing (Ctrl+O toggle) ── */
typedef enum {
    ITEM_USER_INPUT,
    ITEM_THINKING,
    ITEM_TOOL_CALL,
    ITEM_ASSISTANT_RESPONSE,
    ITEM_INFO_BOX
} turn_item_type_t;

typedef struct {
    turn_item_type_t type;
    char *name;           /* tool name or title */
    char *status;         /* ok / error */
    char *content;        /* text content / output */
    double elapsed_sec;   /* duration */
    double tokens_per_sec;/* tok/s */
    int turn_count;       /* turn number */
    int tool_count;       /* tool count */
} turn_item_t;

#define MAX_TURN_ITEMS 512
static turn_item_t g_turn_items[MAX_TURN_ITEMS];
static int g_turn_item_count = 0;

static void add_turn_item(turn_item_type_t type, const char *name, const char *status,
                           const char *content, double elapsed_sec, double tokens_per_sec,
                           int turn_count, int tool_count)
{
    if (g_turn_item_count >= MAX_TURN_ITEMS) {
        free(g_turn_items[0].name);
        free(g_turn_items[0].status);
        free(g_turn_items[0].content);
        memmove(&g_turn_items[0], &g_turn_items[1], (MAX_TURN_ITEMS - 1) * sizeof(turn_item_t));
        g_turn_item_count = MAX_TURN_ITEMS - 1;
    }

    turn_item_t *item = &g_turn_items[g_turn_item_count++];
    memset(item, 0, sizeof(turn_item_t));
    item->type = type;
    item->name = name ? strdup(name) : NULL;
    item->status = status ? strdup(status) : NULL;
    item->content = content ? strdup(content) : NULL;
    item->elapsed_sec = elapsed_sec;
    item->tokens_per_sec = tokens_per_sec;
    item->turn_count = turn_count;
    item->tool_count = tool_count;
}

static void print_jobs_and_tasks_status(void) {
    printf("\n\033[1;36m╭── 📋 Active Background Jobs & Scheduled Tasks ──────────────────╮\033[0m\n");
    
    const char *home = getenv("HOME");
    char task_dir[512];
    snprintf(task_dir, sizeof(task_dir), "%s/.config/ai/scheduled_tasks", home ? home : "~");
    
    DIR *d = opendir(task_dir);
    int count = 0;
    if (d) {
        struct dirent *dir;
        while ((dir = readdir(d)) != NULL) {
            if (dir->d_name[0] == '.') continue;
            if (strstr(dir->d_name, ".json")) {
                count++;
                printf("\033[36m│\033[0m  \033[1;33m•\033[0m Task: \033[1m%s\033[0m \033[32m[SCHEDULED]\033[0m\n", dir->d_name);
            }
        }
        closedir(d);
    }
    if (count == 0) {
        printf("\033[36m│\033[0m  \033[2mNo background tasks currently running or scheduled.\033[0m\n");
    }
    printf("\033[1;36m╰─────────────────────────────────────────────────────────────────╯\033[0m\n\n");
}

static void print_info_box(const char *title, const char *body);
static void print_user_message(const char *message);
static void print_think_box(const char *reasoning);
static void print_tool_box(const char *name, const char *status, const char *content, double elapsed_sec);
static void print_response_box(const char *model_name, const char *content, int turn_count, int tool_count, double elapsed_sec, double tokens_per_sec, int already_streamed);

static void redraw_turn_history(const char *session_id)
{
    if (!isatty(STDOUT_FILENO)) return;

    /* Clear terminal screen and reset cursor */
    printf("\033[2J\033[H");
    fflush(stdout);

    printf("\033[1;35mai\033[0m \033[2m· session %s · details: %s\033[2m (Ctrl+O to toggle)\033[0m\n\n",
           session_id && *session_id ? session_id : "active",
           g_hide_details ? "\033[33mCOLLAPSED\033[0m" : "\033[36mEXPANDED\033[0m");

    if (!g_hide_details) {
        print_jobs_and_tasks_status();
    }

    for (int i = 0; i < g_turn_item_count; i++) {
        turn_item_t *item = &g_turn_items[i];
        switch (item->type) {
            case ITEM_USER_INPUT:
                print_user_message(item->content);
                break;
            case ITEM_THINKING:
                print_think_box(item->content);
                break;
            case ITEM_TOOL_CALL:
                print_tool_box(item->name, item->status, item->content, item->elapsed_sec);
                break;
            case ITEM_ASSISTANT_RESPONSE:
                print_response_box(item->name, item->content, item->turn_count, item->tool_count,
                                   item->elapsed_sec, item->tokens_per_sec, 0);
                break;
            case ITEM_INFO_BOX:
                print_info_box(item->name, item->content);
                break;
        }
    }
    fflush(stdout);
}

/* ── Code Syntax Highlighter for Terminal Output ──────────────────────────── */
static void print_code_line_highlighted(const char *line, const char *lang) {
    if (!line) return;
    (void)lang;
    
    const char *trimmed = line;
    while (*trimmed == ' ' || *trimmed == '\t') trimmed++;
    if (trimmed[0] == '#' || (trimmed[0] == '/' && trimmed[1] == '/')) {
        printf("%s%s%s", CL_DIM, line, CL_RESET);
        return;
    }
    
    const char *p = line;
    while (*p) {
        if (*p == '"') {
            printf("%s\"", CL_GREEN);
            p++;
            while (*p && *p != '"') {
                if (*p == '\\' && *(p+1)) {
                    printf("\\%c", *(p+1));
                    p += 2;
                } else {
                    putchar(*p++);
                }
            }
            if (*p == '"') { putchar(*p++); }
            printf("%s", CL_RESET);
            continue;
        }
        if (*p == '\'') {
            printf("%s'", CL_GREEN);
            p++;
            while (*p && *p != '\'') {
                if (*p == '\\' && *(p+1)) {
                    printf("\\%c", *(p+1));
                    p += 2;
                } else {
                    putchar(*p++);
                }
            }
            if (*p == '\'') { putchar(*p++); }
            printf("%s", CL_RESET);
            continue;
        }
        
        if (isalpha((unsigned char)*p) || *p == '_') {
            const char *start = p;
            while (isalnum((unsigned char)*p) || *p == '_') p++;
            int klen = (int)(p - start);
            char word[64];
            if (klen >= 64) klen = 63;
            strncpy(word, start, klen);
            word[klen] = '\0';
            
            static const char *keywords[] = {
                "def", "class", "import", "from", "return", "if", "else", "elif",
                "for", "while", "try", "except", "finally", "with", "as", "async",
                "await", "function", "const", "let", "var", "struct", "typedef",
                "int", "char", "void", "double", "float", "static", "extern",
                "include", "define", "public", "private", "true", "false", "null",
                "NULL", "True", "False", "None", "self", "this", NULL
            };
            int is_kw = 0;
            for (int i = 0; keywords[i]; i++) {
                if (strcmp(word, keywords[i]) == 0) {
                    is_kw = 1; break;
                }
            }
            if (is_kw) {
                printf("%s%s%s", CL_YELLOW, word, CL_RESET);
            } else {
                printf("%s", word);
            }
            continue;
        }
        
        if (isdigit((unsigned char)*p)) {
            printf("%s", CL_CYAN);
            while (isdigit((unsigned char)*p) || *p == '.') putchar(*p++);
            printf("%s", CL_RESET);
            continue;
        }
        
        putchar(*p++);
    }
}

/* ── Inline Markdown Parser ───────────────────────────────────────────────── */
static void print_inline_markdown(const char *str) {
    if (!str) return;
    const char *p = str;
    while (*p) {
        if (p[0] == '*' && p[1] == '*') {
            p += 2;
            const char *end = strstr(p, "**");
            if (end) {
                printf("%s", CL_BOLD);
                while (p < end) putchar(*p++);
                printf("%s", CL_RESET);
                p += 2;
                continue;
            } else {
                printf("**");
                continue;
            }
        }
        if (*p == '`') {
            p++;
            const char *end = strchr(p, '`');
            if (end) {
                printf("\033[48;5;237;36m ");
                while (p < end) putchar(*p++);
                printf(" \033[0m");
                p++;
                continue;
            } else {
                putchar('`');
                continue;
            }
        }
        if ((*p == '*' || *p == '_') && p[1] != ' ' && p[1] != '\0') {
            char symbol = *p;
            p++;
            const char *end = strchr(p, symbol);
            if (end && end > p && end[-1] != ' ') {
                printf("\033[3m");
                while (p < end) putchar(*p++);
                printf("\033[0m");
                p++;
                continue;
            } else {
                putchar(symbol);
                continue;
            }
        }
        putchar(*p++);
    }
}

/* ── Terminal Markdown Renderer ───────────────────────────────────────────── */
static void print_terminal_markdown(const char *text) {
    if (!text || !*text) return;
    
    int in_code_block = 0;
    char code_lang[64] = "";
    
    const char *p = text;
    while (*p) {
        const char *next = strchr(p, '\n');
        size_t len = next ? (size_t)(next - p) : strlen(p);
        
        char line[4096];
        if (len >= sizeof(line)) len = sizeof(line) - 1;
        strncpy(line, p, len);
        line[len] = '\0';
        
        if (strncmp(line, "```", 3) == 0) {
            if (!in_code_block) {
                in_code_block = 1;
                const char *lang = line + 3;
                while (*lang == ' ') lang++;
                snprintf(code_lang, sizeof(code_lang), "%.63s", lang);
                char *sp = code_lang;
                while (*sp && !isspace((unsigned char)*sp)) sp++;
                *sp = '\0';
                
                printf("\033[2;33m┌── code: %s ──\033[0m\n", code_lang[0] ? code_lang : "text");
            } else {
                in_code_block = 0;
                printf("\033[2;33m└──\033[0m\n");
                code_lang[0] = '\0';
            }
            p = next ? next + 1 : p + len;
            continue;
        }
        
        if (in_code_block) {
            print_code_line_highlighted(line, code_lang);
            printf("\n");
        } else {
            if (line[0] == '#' && line[1] == ' ') {
                printf("\033[1;4;35m%s\033[0m\n", line);
            } else if (line[0] == '#' && line[1] == '#' && line[2] == ' ') {
                printf("\033[1;36m%s\033[0m\n", line);
            } else if (line[0] == '#' && line[1] == '#' && line[2] == '#' && line[3] == ' ') {
                printf("\033[1;33m%s\033[0m\n", line);
            } else if (strncmp(line, "> ", 2) == 0) {
                printf("  \033[3;33m");
                print_inline_markdown(line + 2);
                printf("\033[0m\n");
            } else if ((strncmp(line, "- ", 2) == 0 || strncmp(line, "* ", 2) == 0) && line[2] != '*') {
                printf("  \033[36m•\033[0m ");
                print_inline_markdown(line + 2);
                printf("\n");
            } else if (isdigit((unsigned char)line[0]) && line[1] == '.' && line[2] == ' ') {
                printf("  \033[36m%c.\033[0m ", line[0]);
                print_inline_markdown(line + 3);
                printf("\n");
            } else if (strcmp(line, "---") == 0 || strcmp(line, "***") == 0) {
                printf("\033[2m────────────────────────────────────────────\033[0m\n");
            } else {
                print_inline_markdown(line);
                printf("\n");
            }
        }
        
        p = next ? next + 1 : p + len;
    }
}

/* ── Print User Command / Message Banner ─────────────────────────────────── */
static void print_user_message(const char *message) {
    if (!message || !*message) return;
    const char *perm_label = g_permission_mode == 0 ? "auto"
                           : g_permission_mode == 1 ? "plan"
                           : "manual";
    printf("\n\033[1;36m👤 User \033[2m[%s]\033[0m\n", perm_label);
    const char *line = message;
    while (*line) {
        const char *nl = strchr(line, '\n');
        int len = nl ? (int)(nl - line) : (int)strlen(line);
        printf("\033[1m%.*s\033[0m\n", len, line);
        if (!nl) break;
        line = nl + 1;
    }
    printf("\n");
}

static void print_markdown_table(const char *content);

/* ── Print Assistant Response (Borderless & Copy-Paste Friendly) ────────── */
static void print_response_box(const char *model_name, const char *content,
                               int turn_count, int tool_count,
                               double elapsed_sec, double tokens_per_sec,
                               int already_streamed)
{
    (void)already_streamed;
    const char *mname = (model_name && *model_name) ? model_name : "ai";

    if (!isatty(STDOUT_FILENO) || getenv("INFER_RAW_OUTPUT") != NULL) {
        if (content && *content) {
            printf("%s\n", content);
        }
        return;
    }

    printf("\n\n\033[1;32m🤖 %s (Answer)\033[0m\n\n", mname);
    if (content && *content) {
        char *rendered = NULL;
        int is_pre_rendered = (strstr(content, "\033[") != NULL);
        if (!is_pre_rendered) {
            rendered = render_markdown(content);
        }
        const char *to_print = rendered ? rendered : content;

        if (to_print && *to_print) {
            if (rendered || is_pre_rendered) {
                const char *line = to_print;
                while (*line) {
                    const char *nl = strchr(line, '\n');
                    int len = nl ? (int)(nl - line) : (int)strlen(line);
                    printf("%.*s\n", len, line);
                    if (!nl) break;
                    line = nl + 1;
                }
            } else {
                print_terminal_markdown(to_print);
            }
        }
        if (rendered) free(rendered);
    }
    if (elapsed_sec > 0 || turn_count > 0 || tool_count > 0) {
        printf("\033[2m⏱ ");
        if (elapsed_sec > 0) {
            if (tokens_per_sec > 0) {
                printf("%.1fs (%d tok/s)", elapsed_sec, (int)tokens_per_sec);
            } else {
                printf("%.1fs", elapsed_sec);
            }
        }
        if (turn_count > 0) printf(" · turn %d", turn_count);
        if (tool_count > 0) printf(" · %d tools", tool_count);
        printf("\033[0m\n");
    }
    printf("\n");
}


/* ── Print Tool Execution Box & Error Cards ──────────────────────────────── */
static void print_tool_box(const char *name, const char *status,
                           const char *content, double elapsed_sec)
{
    const char *ticon = tool_get_icon(name);
    const char *hc   = tool_get_color(name);
    int is_error = (status && strncmp(status, "error", 5) == 0);
    const char *st_icon = is_error ? "\033[31m✕\033[0m"
                        : (status && strcmp(status, "ok") == 0)       ? "\033[32m✔\033[0m"
                        : "\033[36mℹ\033[0m";

    /* Suppress tool-call boxes when stdout is piped (e.g. Zulip bridge).
     * Only the final clean response from print_response_box should appear. */
    if (!isatty(STDOUT_FILENO)) return;

    if (g_hide_details) {
        /* Inline compact rendering for Ctrl+O hidden mode */
        printf("  %s%s\033[0m \033[1m%s\033[0m %s",
               hc ? hc : "\033[36m", ticon ? ticon : "⚡",
               name ? name : "tool", st_icon);
        if (elapsed_sec > 0) {
            printf(" \033[2m(%.1fs)\033[0m", elapsed_sec);
        }
        printf("\n");
        return;
    }

    if (is_error) {
        printf("\n\033[1;31m╭── ✕ Tool Error: %s ────────────────────────────────╮\033[0m\n", name ? name : "tool");
    } else {
        printf("\n\033[1;33m╭── %s Tool: \033[1;37m%s\033[1;33m ───────────── [%s %.1fs] ──╮\033[0m\n",
               ticon ? ticon : "⚡", name ? name : "tool", st_icon, elapsed_sec);
    }

    if (content && *content) {
        const char *line = content;
        const int max_lines = 30;
        int lines = 0;
        while (*line && lines < max_lines) {
            const char *nl = strchr(line, '\n');
            int len = nl ? (int)(nl - line) : (int)strlen(line);
            printf("\033[33m│\033[0m  %.*s\n", len, line);
            if (!nl) break;
            line = nl + 1;
            lines++;
        }
        if (*line && lines >= max_lines) {
            printf("\033[33m│\033[0m  \033[2m... (%zu more lines)\033[0m\n", strlen(line));
        }
    }

    if (is_error) {
        printf("\033[1;31m╰───────────────────────────────────────────────────╯\033[0m\n\n");
    } else {
        printf("\033[1;33m╰───────────────────────────────────────────────────╯\033[0m\n\n");
    }
}

/* ── Thinking Display: Styled reasoning block ───────────────────────────── */
static void print_think_box(const char *reasoning)
{
    /* Suppress thinking blocks when stdout is piped (e.g. Zulip bridge). */
    if (!isatty(STDOUT_FILENO)) return;

    if (g_hide_details) {
        printf("\033[2;35m  ◈ thinking (collapsed)\033[0m\n");
        return;
    }
    if (reasoning && *reasoning) {
        printf("\n\033[2;35m◈ thinking:\n%s\033[0m\n\n", reasoning);
    }
}

/* ── Markdown table renderer: aligned columns with separators ─────────── */
static void print_markdown_table(const char *content)
{
    if (!content) return;

    int cols = lineed_term_cols() - 8;
    if (cols < 40) cols = 40;

    /* Parse rows into dynamic arrays */
    int max_cols = 4;
    int *col_widths = calloc(max_cols, sizeof(int));
    char ***rows = NULL;
    int nrows = 0, row_cap = 32;
    rows = calloc(row_cap, sizeof(char*));

    const char *line = content;
    while (*line) {
        const char *nl = strchr(line, '\n');
        int len = nl ? (int)(nl - line) : (int)strlen(line);
        if (len == 0) { line = nl ? nl + 1 : ""; continue; }

        /* Skip separator rows (|---|---|) */
        int is_sep = 1;
        for (int i = 0; i < len; i++) {
            if (line[i] != '-' && line[i] != ':' && line[i] != '|' && line[i] != ' ') {
                is_sep = 0; break;
            }
        }
        if (is_sep) { line = nl ? nl + 1 : ""; continue; }

        /* Parse columns */
        char *row_str = strndup(line, len);
        char **cells = NULL;
        int ncells = 0, cap = 8;
        cells = calloc(cap, sizeof(char*));

        const char *p = row_str;
        while (*p) {
            while (*p == '|' || *p == ' ') p++;
            const char *cs = p;
            while (*p && *p != '|') p++;
            int clen = p - cs;
            while (*p == '|') p++;
            if (clen > 0) {
                if (ncells >= cap) { cap *= 2; cells = realloc(cells, cap * sizeof(char*)); }
                cells[ncells++] = strndup(cs, clen);
            }
        }
        free(row_str);
        if (ncells == 0) { free(cells); continue; }

        if (nrows >= row_cap) { row_cap *= 2; rows = realloc(rows, row_cap * sizeof(char*)); }
        rows[nrows] = cells;

        for (int c = 0; c < ncells; c++) {
            int lw = (int)strlen(cells[c]);
            if (lw > col_widths[c]) col_widths[c] = lw;
            if (c + 1 > max_cols) max_cols = c + 2;
        }
        nrows++;
    }

    /* Render table */
    for (int r = 0; r < nrows; r++) {
        char **cells = rows[r];
        printf("%s│", CL_DIM);
        for (int c = 0; c < max_cols; c++) {
            char *cell = (c < max_cols) ? cells[c] : "";
            int w = col_widths[c] + 2;
            printf(" %-*.*s%s", w, w, cell, CL_DIM);
            if (c < max_cols - 1) printf("│");
        }
        printf("│%s\n", CL_RESET);
        if (r == 0) {
            /* Separator after header */
            for (int c = 0; c < max_cols; c++) {
                printf("┼");
                for (int i = 0; i < col_widths[c] + 2; i++) printf("─");
            }
            printf("%s\n", CL_RESET);
        }
        free(cells);
    }

    /* Cleanup */
    for (int r = 0; r < nrows; r++) free(rows[r]);
    free(rows);
    free(col_widths);
}

/* Print a minimal warning indicator */
static void print_warning_box(const char *title, const char *body)
{
    printf("\n\033[1;31m⚠ %s\033[0m\n", title ? title : "Warning");
    if (body && *body) {
        printf("\033[2m  %s\033[0m\n", body);
    }
    printf("\n");
}

/* Print a minimal info indicator */
static void print_info_box(const char *title, const char *body)
{
    printf("\n\033[1;36mℹ %s\033[0m\n", title ? title : "Info");
    if (body && *body) {
        printf("\033[2m  %s\033[0m\n", body);
    }
    printf("\n");
}

// Config globals
static char  api_url[MAX_VAL];
static char  api_key[MAX_VAL];
static char  model[MAX_VAL];
static float temperature_val        = -1.0f;
static float top_p_val              = -1.0f;
static int   top_k_val              = -1;
static float min_p_val              = -1.0f;
static char  *reasoning_effort_val  = NULL;
static int   preserve_thinking_val  = 0;
static char  *mode_preset_val       = NULL; /* --mode <xhigh|normal|low|instruct> */
static int   max_tokens_val         = 32768; /* Completion budget. 8192 is too small for 35B models that emit long native reasoning_content: it truncates mid-reasoning before the model ever emits its action tool call, so tasks end with zero artifacts. 32768 lets reasoning + the tool call both fit. Override via INFER_MAX_TOKENS. */
static float frequency_penalty_val  =  0.10f; /* Break repetitive thinking loops (0=off, INFER_FREQ_PENALTY) */
static float presence_penalty_val   =  0.05f; /* Encourage new topics (0=off, INFER_PRESENCE_PENALTY) */
static int   no_tools_mode          = 0;
char  *resume_session_id = NULL; /* -r/--resume [id] or INFER_RESUME */
static int   context_window   = 0;    /* set via INFER_CONTEXT_WINDOW */
static int   task_timeout_sec = 1800; /* set via INFER_TASK_TIMEOUT; 0 = no timeout */
static int   max_tool_output  = 65536;/* set via INFER_MAX_TOOL_OUTPUT; default 65536 */
static int   trim_threshold   = 100000;/* set via INFER_TRIM_THRESHOLD; default 100000 */
static int   stub_threshold   = 250000;/* set via INFER_STUB_THRESHOLD; default 250000 */
static char  *g_goal_text     = NULL; /* -g/--goal flag */

typedef struct ToolCacheNode {
    char *key;
    char *output;
    struct ToolCacheNode *next;
} ToolCacheNode;

static ToolCacheNode *g_tool_cache = NULL;

static const char* get_tool_cache(const char *name, const char *args) {
    if (!name || !args) return NULL;
    if (strcmp(name, "read_file") != 0 &&
        strcmp(name, "web_search") != 0 &&
        strcmp(name, "fetch_webpage") != 0 &&
        strcmp(name, "list_directory") != 0) {
        return NULL;
    }
    size_t klen = strlen(name) + strlen(args) + 2;
    char *key = malloc(klen);
    snprintf(key, klen, "%s:%s", name, args);
    ToolCacheNode *curr = g_tool_cache;
    while (curr) {
        if (strcmp(curr->key, key) == 0) {
            free(key);
            return curr->output;
        }
        curr = curr->next;
    }
    free(key);
    return NULL;
}

static void set_tool_cache(const char *name, const char *args, const char *output) {
    if (!name || !args || !output) return;
    if (strcmp(name, "read_file") != 0 &&
        strcmp(name, "web_search") != 0 &&
        strcmp(name, "fetch_webpage") != 0 &&
        strcmp(name, "list_directory") != 0) {
        return;
    }
    if (strncmp(output, "Error", 5) == 0 || strncmp(output, "[Command Failed", 15) == 0) return;

    size_t klen = strlen(name) + strlen(args) + 2;
    char *key = malloc(klen);
    snprintf(key, klen, "%s:%s", name, args);

    ToolCacheNode *node = malloc(sizeof(ToolCacheNode));
    node->key = key;
    node->output = strdup(output);
    node->next = g_tool_cache;
    g_tool_cache = node;
}

static int g_dry_run = 0; /* --dry-run flag */

/* ── Git integration (auto-commit after successful commands) ── */
static int g_git_commit_enabled = 1;   /* 1 = auto-commit on success */
static char *g_git_commit_msg = NULL;  /* custom commit message from user */

/* ── OS notifications ── */
static int g_notifications_enabled = 0;

/* ── Automatic failure-learning (self-improvement ledger) ──
   The harness records tool failures deterministically (no model discipline
   needed). Per-session map of tools that failed; when the SAME tool later
   succeeds, the working approach is auto-persisted as a FIX lesson so future
   sessions don't repeat the mistake. */
#define SI_MAX_TRACKED 32
static struct {
    int used;
    char tool[140];
    char args[400];
    char error[600];
} g_si_failed[SI_MAX_TRACKED];

/* ── Situational awareness (state log) ──
   Small models drop context and lose track of where they are mid-task. This is a
   bounded, per-task rolling log of tool outcomes (step:tool=status) that gets
   prepended as a [CURRENT STATE] header to every tool result, so the model always
   sees its progress without having to hold it in memory. Disable with
   INFER_STATE_CONTEXT=0. */
static char g_state_log[512] = "";   /* rolling "stepN:tool=ok/ERR; " history for this task */
static int  g_state_status = 0;      /* 1 if the LAST tool call erred (shared with the header) */

/* ------- Small-model productivity watchdog -------
   A small local model can burn its entire output budget on one giant `think`
   block (or a long chain of `think` calls) and end the task having produced
   NO artifact. We cap how much of a single think we keep and count consecutive
   think-without-action loops; crossing the limit forces a concrete-action nudge.
   'Productive' = any tool that is NOT read-only (see tool_is_readonly()). */
static int g_think_since_action = 0; /* consecutive think/read-only loops since last productive action */
static int g_think_max_chars = 2200; /* INFER_THINK_MAX_CHARS: cap a single think's reasoning length */


/* ── Permission modes: 0=auto, 1=plan, 2=manual (defined above) ── */

/* ── AGENTS.md integration ── */
static int g_agents_enabled = 1;       /* auto-load AGENTS.md */
static char *g_agents_content = NULL;  /* cached AGENTS.md content */

/* ── Background sessions ── */
static int g_background = 0;           /* --bg flag */
static char *g_session_file = NULL;    /* session state file */

/* ── Copy / clipboard ── */
static char *g_last_response = NULL;   /* last assistant response text */
__attribute__((unused)) static int g_last_response_len = 0;
static int   g_copy_enabled = 1;

/* ── Thinking spinner animation ── */
static int g_spinner_idx = 0;

/* ── Token tracking ── */
static long g_tokens_prompt = 0;
static long g_tokens_completion = 0;
static int  g_tokens_total = 0;
static long g_session_tokens = 0;  /* latest request total_tokens (context window size) */

static int is_command_denied(const char *cmd) {
    if (!cmd) return 0;
    static const char *default_denied[] = {
        "rm -rf /", "rm -rf ~", "rm -rf $HOME", "mkfs", "dd if=/dev/zero",
        ":(){ :|:& };:", "chmod -R 777 /", "> /dev/sda", NULL
    };
    for (int i = 0; default_denied[i]; i++) {
        if (strstr(cmd, default_denied[i])) return 1;
    }
    const char *denylist = getenv("INFER_COMMAND_DENYLIST");
    if (denylist && *denylist) {
        char *copy = strdup(denylist);
        char *token = strtok(copy, ",");
        while (token) {
            while (isspace((unsigned char)*token)) token++;
            if (*token && strstr(cmd, token)) {
                free(copy);
                return 1;
            }
            token = strtok(NULL, ",");
        }
        free(copy);
    }
    return 0;
}


/* Prompt the user on the controlling terminal for y/n approval.
   Returns: 1 = approved, 0 = denied, -1 = no terminal available. */
static int prompt_user_ok(const char *title, const char *detail) {
    FILE *tty = fopen("/dev/tty", "r+");
    if (!tty) return -1;
    fprintf(tty, "\n\033[1;33m  CONFIRM: %s\033[0m\n", title ? title : "");
    if (detail && *detail) {
        char *d = strdup(detail);
        char *nl = d ? strchr(d, '\n') : NULL;
        if (nl) *nl = '\0';
        fprintf(tty, "  \033[2m%s\033[0m\n", d ? d : "");
        if (d) free(d);
    }
    fprintf(tty, "  \033[32my\033[0m/\033[31mn\033[0m  %s Proceed?%s\n\n", CL_DIM, CL_RESET);
    fflush(tty);
    int fd = fileno(tty);
    struct termios orig, raw;
    int has = (tcgetattr(fd, &orig) >= 0);
    if (has) { raw = orig; raw.c_lflag &= ~(ECHO|ICANON); raw.c_cc[VMIN]=1; raw.c_cc[VTIME]=0; tcsetattr(fd,TCSANOW,&raw); }
    char ch = 0;
    int r = (read(fd, &ch, 1) == 1);
    if (has) tcsetattr(fd, TCSAFLUSH, &orig);
    if (r) fprintf(tty, "%c\n", ch);
    fclose(tty);
    if (!r) return -1;
    return (ch=='y'||ch=='Y'||ch=='\n'||ch=='\r') ? 1 : 0;
}

/* True if a tool changes persistent state and therefore needs approval
   in manual or unapproved-plan mode. Read-only investigation tools return 0. */
static int tool_is_mutating(const char *name) {
    if (!name) return 0;
    static const char *mut[] = {
        "execute_command","execute_remote_command","remote_exec",
        "write_file","edit_file","save_memory","remember",
        "learn_rule","vault_write","schedule_task","set_reminder",
        "unschedule_task","start_background_process","stop_process","delegate_task",
        "spawn_agent","resume_agent","append_context_pool",
        "skill_create","skill_update","skill_note",
        NULL
    };
    for (int i = 0; mut[i]; i++)
        if (strcmp(name, mut[i]) == 0) return 1;
    return 0;
}

static int tool_is_readonly(const char *name) {
    /* Deny-by-default: only tools in this allowlist may run WITHOUT approval in
       PLAN/MANUAL mode. Everything else (mutators AND unknown/new MCP tools) is
       treated as state-changing and gated. This stops a small model from using an
       unlisted tool to change state while a plan is supposed to be in control.
       Keep read-only investigation tools here so plan-mode research stays free. */
    if (!name) return 0;
    static const char *ro[] = {
        "think","web_search","arxiv_search","fetch_webpage","fetch_smart","read_file",
        "list_directory","recall","list_processes","check_process_status","parallel_fetch",
        "load_skill","fetch_webpage_js","get_system_status","get_clipboard",
        "vault_read","vault_search","vault_backlinks","pubmed_search",
        "gcal_list_events","gcal_check_availability","check_time","list_scheduled_tasks",
        "search_history","list_sessions","get_session","present_plan","task_complete",
        "scientific__pdb_parse","scientific__uniprot_search","scientific__align_sequences",
        "scientific__data_analysis","scientific__security_audit",
        NULL
    };
    for (int i = 0; ro[i]; i++)
        if (strcmp(name, ro[i]) == 0) return 1;
    return 0;
}

/* Plan-mode step budget: a single present_plan approval grants a BOUNDED number of
   state-changing actions (execute_command + mutating MCP tools) before the agent is
   forced to present an updated plan and be re-approved. This stops one approval from
   unlocking an unbounded, multi-minute rampage - especially on small models. */
static void plan_budget_consume(void) {
    if (g_permission_mode != 1 || !g_plan_approved) return;
    if (g_plan_budget <= 0) return;              /* INFER_PLAN_STEP_BUDGET=0 => unlimited */
    if (g_plan_remaining > 0) g_plan_remaining--;
    if (g_plan_remaining <= 0) {
        g_plan_approved = 0;
        g_plan_remaining = 0;
        if (!g_plan_budget_note) {
            g_plan_budget_note = strdup(
                "[PLAN BUDGET EXHAUSTED] The approved plan's step budget is spent. "
                "Do NOT start new state-changing work. Either call present_plan with the "
                "next concrete set of changes to receive a new approval, or call task_complete "
                "to report progress. Further changes are blocked until a new plan is approved.");
        }
    }
}

const char *SYSTEM_PROMPT =
    "By default you are a fully autonomous CLI agent in FULL AUTONOMY mode - Output in clean markdown and follow these rules exactly. MOST IMPORTANT: respect the CURRENT PERMISSION MODE injected below - in PLAN/MANUAL mode you must investigate first, then present_plan and wait for approval before ANY state change, and work in small approved steps, not one long unattended run:\n\n"
    "SCIENTIFIC ADVISOR RIGOR (highest priority):\n"
    "- You are an elite scientific advisor. You must use the `think` tool to reason step-by-step before EVERY major action. Do NOT guess.\n"
    "- For small models to succeed, rigorous chain-of-thought is required. Always analyze the current state, form a hypothesis, and plan your next tool call using `think`.\n"
    "- When a command fails, you MUST call `think` to analyze the error log before retrying.\n"
    "- Verify your results. After writing a script, run it. If it produces output, read the output carefully.\n"
    "- Once all requested operations succeed and are empirically verified, call task_complete.\n\n"
    "SYSTEMS ENGINEERING & MEMORY RIGOR (CRITICAL FOR CODE GENERATION):\n"
    "- Memory Safety: In C/C++, NEVER call free() on stack-allocated memory (e.g. `char buf[128]`). Every free() must match a heap allocation (malloc/calloc/strdup). Never access pointers after free().\n"
    "- String & Pointer Bounds: Check string bounds (strlen) before pointer arithmetic. Never advance string pointers past '\\0'.\n"
    "- Complete Verification: When building or editing code, ALWAYS compile (`make` or `g++`) and run unit tests to verify. Update build files (Makefile/CMakeLists.txt) and test suites whenever new source files are added.\n"
    "- Shell Command Safety: Do NOT embed raw single-quoted strings into shell commands without proper escaping. Write complex text to files or stdin to avoid syntax errors.\n\n"
    "TOOL USE:\n"
    "- For facts you already know (e.g. definitions, formulas, capitals), call task_complete directly — no tools needed.\n"
    "- For scientific databases, public APIs, or structured data (PDB, UniProt, NCBI, NASA, arXiv, etc.): use execute_command with curl to query the REST API directly. DO NOT rely on web_search snippets for structured data — the API will give exact answers.\n"
    "  Examples: PDB → `curl 'https://search.rcsb.org/rcsbsearch/v2/query' -d '{...}'`; arXiv → `curl 'https://export.arxiv.org/api/query?search_query=...'`\n"
    "- Use web_search for general questions or current news. web_search now auto-fetches the top result — check [Top result full content] first before calling fetch_webpage again.\n"
    "- Do NOT repeat web_search with slightly different queries — if the first search returns no answer, fetch the top result URL or switch to an API.\n"
    "- After writing a script with write_file, you MUST run it with execute_command to verify it works.\n"
    "- To modify existing files, strictly use the `edit_file` tool instead of `sed`, `awk`, or interactive editors.\n"
    "- NEVER run interactive terminal programs like `vim`, `nano`, `top`, `less`, or `ssh` via execute_command as they will hang the agent. Use provided tools instead.\n"
    "- For long-running jobs (e.g. scrapers, downloads, python scripts, servers, heavy builds), NEVER use execute_command as it blocks the main thread and locks the GUI! Use `start_background_process` instead, and monitor it autonomously using `check_process_status` (often via `schedule_task` polling).\n"
    "- NEVER run `find /` or search indiscriminately from the root directory; it will hang forever. Always constrain searches to specific, relevant directories (e.g., `./` or `~/Code`).\n"
    "- NEVER describe what the user can do themselves - if a tool can get an answer, call it. In PLAN/MANUAL mode call read-only tools freely, but present_plan before any state-changing one.\n\n"
    "CHECKPOINT & VALIDATE (CRITICAL FOR SMALL MODELS):\n"
    "- Do ONE planned step at a time, then check the tool result against the plan before starting the next step.\n"
    "- After any write/edit or command, validate: compile, run the relevant test, or read the output.\n"
    "- In PLAN mode an approval grants a BOUNDED number of state-changing actions (INFER_PLAN_STEP_BUDGET). When that budget is exhausted the harness blocks further changes and tells you to present_plan again - do exactly that (or task_complete), never keep going.\n"
    "- If a step fails validation, stop, think, and adjust; do not barrel through unrelated changes.\n"
    "WORKFLOW (task -> plan -> execution -> tests -> solution):\n"
    "- Follow this 5-phase discipline for every non-trivial task. This stops you from repeating mistakes on autopilot.\n"
    "  1. TASK - restate the goal in your own words via `think`.\n"
    "  2. PLAN - use `think` to lay out the exact steps, files, and commands. Identify what could go wrong BEFORE acting.\n"
    "  - REUSE BEFORE WRITING: do NOT reinvent the wheel. Before implementing any nontrivial component (a file-format parser, an algorithm, a library routine), SEARCH for an existing, maintained library first - `pkg.go.dev` / GitHub / PyPI / crates.io - and build AROUND it (e.g. `go get`, `pip install`). Hand-write only what no good package covers. Name the package you reused and its import path in your final summary.\n"
    "  3. EXECUTION - do ONE low-risk step at a time. If a tool errors, `think` about the error, then change your approach - never retry the identical failing call.\n"
    "  - THINK DISCIPLINE: keep every `think` to a MAXIMUM of 2-3 short sentences. NEVER write your code, plan, or analysis in full inside `think` - that wastes the whole output budget and stalls the task. Write code to a file with write_file, then build/run it with execute_command. A task with zero write_file/execute_command calls after a few turns is failing - stop thinking and ACT.\n"
    "  4. TESTS - empirically verify each change: compile (`make`) and/or run the relevant test, or read the output. Never claim success without evidence.\n"
    "  5. SOLUTION - call task_complete with a summary of what you did, how you verified it, and any lesson learned.\n"
    "- SELF-IMPROVEMENT: The harness AUTOMATICALLY records your tool failures and the fixes that recovered them (persisted across sessions), and surfaces them when you hit the same error again. Treat [REMEMBERED FROM PAST SESSIONS] and [RECURRING FAILURE] messages as instructions. Still proactively persist reusable techniques with skill_create / skill_update / skill_note.\n"
    "- When a tool result contains [RECURRING FAILURE], you have made this exact mistake before - stop and use the past FIX lesson or a genuinely new approach.\n"
    "- The tests phase is mandatory: a change is only done once it is verified, not when you believe it works.\n\n"
    "CITATIONS:\n"
    "- fetch_webpage and read_file (PDF) results begin with a [Source: ...] line. Track every source whose content you use.\n"
    "- In your task_complete summary, always end with a '## Sources' section listing each [Source: ...] URL or file path you drew from.\n"
    "- Do not list sources you fetched but did not use in the answer.\n\n"
    "FAILURE RECOVERY:\n"
    "- If execute_command fails, read the error, fix the root cause, and retry. At least 3 attempts before giving up.\n"
    "- If a library is missing: in AUTO mode install it non-interactively (e.g. `pip install --user` or `sudo apt-get install -y`). In PLAN/MANUAL mode, instead propose the install command in your present_plan and wait for approval. If a web source is blocked or noisy, find an alternative.\n"
    "- If fetch_webpage returns a WARNING about JavaScript or returns fewer than 80 words, the page is JS-only. Switch to execute_command with curl to a plain-text API instead.\n"
    "- For current weather: execute_command `curl -s 'wttr.in/Miami?format=3'` (replace city name). Never rely on weather.com/weather.gov — they require JavaScript.\n"
    "- Never tell the user to 'visit a link' or 'run a command themselves' — do the read-only investigation yourself with tools; in PLAN/MANUAL mode present a plan before state changes.\n\n"
    "PARALLEL EXECUTION (use these tools whenever work can be split):\n"
    "- parallel_fetch({\"urls\":[\"url1\",\"url2\",...]}) — fetch N pages at once. Use instead of N sequential fetch_webpage calls. Ideal for reading multiple search results, papers, or docs.\n"
    "- delegate_task({\"tasks\":[\"task1\",\"task2\",...]}) — spawn N agents concurrently. Use for independent sub-tasks that need their own tool loops (summarise a paper, write a script, run a benchmark). Always pass tasks as an ARRAY, never as a single string.\n"
    "- Example — publication digest: parallel_fetch({\"urls\":[paper1,paper2,paper3]}) then synthesise.\n"
    "- Example — multi-site comparison: parallel_fetch({\"urls\":[site1,site2,site3]}).\n"
    "- Example — parallel research: delegate_task({\"tasks\":[\"Search for X and summarise findings\",\"Search for Y and summarise findings\"]}).\n"
    "- Rule: if you would call fetch_webpage or web_search more than once for independent URLs/queries, use parallel_fetch or delegate_task instead.\n\n"
    "SCHEDULING AND DEFERRED TASKS (CRITICAL):\n"
    "- NEVER use `sleep` inside execute_command for delays. `sleep 60 && ...` blocks the terminal or chat bridge and makes the user wait with no response. It is FORBIDDEN.\n"
    "- For ANY request that involves waiting, timing, or checking something later (e.g. 'remind me in 5 min', 'notify me when done', 'check the folder every hour', 'send a message when job finishes') you MUST use schedule_task.\n"
    "- For plain 'remind me ...' / 'ping me at/in ... to ...' requests, prefer set_reminder — it delivers the reminder straight to Zulip at the given time (convert 'tomorrow 9am' to an ISO timestamp using the current time). From Zulip chat the recipient is auto-filled; just pass message + when.\n"
    "- schedule_task runs the agent loop in a fully detached background process — the current session returns immediately.\n"
    "- Inside the scheduled prompt, instruct the sub-agent to call unschedule_task(task_id) once its condition is satisfied so the loop stops.\n"
    "- Examples:\n"
    "  * 'Show notification in 5 min' → schedule_task(task_id='notify_go_home', prompt='Run: execute_command(notify-send ...); then unschedule_task(notify_go_home).', interval_seconds=300)\n"
    "  * 'Tell me when /data has 1000 files' → schedule_task(task_id='check_data_files', prompt='Count files in /data. If >= 1000: send Zulip message with count, then unschedule_task(check_data_files). Else do nothing.', interval_seconds=120)\n"
    "- After calling schedule_task, immediately call task_complete telling the user the task is scheduled and when/how they will be notified.\n\n"
    "VIRTUAL ASSISTANT & COMPUTER USE:\n"
    "- You are a capable virtual assistant for scheduling, planning, organizing, and daily tasks.\n"
    "- For Outlook without API permissions: use headless browser automation (e.g. Playwright) to interact with Outlook Web App (OWA), use IMAP/SMTP if enabled, or read public ICS links.\n"
    "- For GUI tasks and computer use ('help me fix this error', 'fill that form'): use screenshot description tools (multimodal vision) to understand the screen, and use desktop automation (e.g. xdotool, PyAutoGUI) to navigate, click, and type.\n\n"
    "WRITING STYLE & TONE (CRITICAL):\n"
    "- Generate text that sounds human, direct, and professional.\n"
    "- STRICTLY avoid 'LLM-like' clichés (e.g., 'Here is the...', 'It is important to note', 'In summary', 'As an AI', 'Delve into').\n"
    "- Write concisely without fluff, repetitive structures, or robotic transitions. Use varied sentence lengths.\n\n"
    "SKILLS:\n"
    "- Domain skills exist. Call load_skill() to list them, load_skill(name) to read one.\n"
    "- Additional CRITICAL triggers may follow in the system context — obey them exactly.\n\n"
    "SCIENTIFIC OUTPUT & REVIEW:\n"
    "- For scientific tasks, strictly avoid markdown tables, emojis, and bullet points. Write in long, cohesive, well-reasoned paragraphs.\n"
    "- Always employ a multi-turn review process: generate a draft, verify it against memory maps/RAG context in a second turn, and refine it into scientifically sound prose before delivering the final output.";

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

    if (g_permission_mode == 1) {
        size_t pl = strlen(buf);
        snprintf(buf + pl, 4096 - pl,
                 "\nCURRENT PERMISSION MODE: PLAN\n"
                 "- Investigate, read, search, and run READ-ONLY commands freely.\n"
                 "- DO NOT change anything (no write/edit/execute of state-changing commands / memory / schedule) until your plan is approved.\n"
                 "- To make changes: call present_plan(plan=\"...\") with your findings, the exact changes, and rationale; wait for approval.\n"
                 "- Once a plan is approved you may make a BOUNDED number of state-changing actions (INFER_PLAN_STEP_BUDGET, default 8). When the budget runs out the harness blocks further changes; you MUST present_plan again before more changes.\n"
                 "- Execute step by step: do ONE step, validate its result, then continue. For complex work, delegate individual steps to subagents and verify each result. If an approval is rejected, revise and call present_plan again.\n");
    } else if (g_permission_mode == 2) {
        size_t pl = strlen(buf);
        snprintf(buf + pl, 4096 - pl,
                 "\nCURRENT PERMISSION MODE: MANUAL\n"
                 "- You must obtain explicit user approval for EVERY state-changing action (commands, file writes/edits, memory, scheduling).\n"
                 "- Read-only investigation tools run without prompting.\n"
                 "- When the system returns a denial, adjust your approach; do not retry the same action until the user allows it.\n");
    } else {
        size_t pl = strlen(buf);
        snprintf(buf + pl, 4096 - pl,
                 "\nCURRENT PERMISSION MODE: FULL AUTONOMY (auto)\n"
                 "- Investigate, execute, and change state freely until the task is finished, then call task_complete.\n"
                 "- Still prefer to think before major actions and verify your results.\n");
    }
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
    
    fprintf(fp, "{\"timestamp\":\"%s\",\"session_id\":\"%s\",\"prompt\":\"%s\",\"pipe_writer\":\"%s\",\"interactive\":%s,\"response\":\"%s\"}\n",
            time_str, current_session_id[0] ? current_session_id : "unknown",
            esc_prompt, esc_writer, interactive ? "true" : "false", esc_resp);
    
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

#include "ai_git.h"

/* ── OS Notifications ── */
static void notify_completion(const char *summary) {
    (void)summary;
    /* User requested to disable notifications of job done as they are annoying.
       Disabled completely.
    if (!g_notifications_enabled) return;

    char cmd[2048];
    if (summary && *summary) {
        char safe_summary[256] = {0};
        int j = 0;
        for (int i = 0; summary[i] && j < 240; i++) {
            char c = summary[i];
            if (c == '\'' || c == '"' || c == '`' || c == '\\' || c == '\n') safe_summary[j++] = ' ';
            else safe_summary[j++] = c;
        }
        safe_summary[j] = '\0';
        snprintf(cmd, sizeof(cmd), "notify-send -u normal 'ai task done' '%s' 2>/dev/null &",
                 safe_summary);
        (void)system(cmd);
    } else {
        (void)system("notify-send -u normal 'ai task done' 'Task completed successfully' 2>/dev/null &");
    }
    */
}

/* ── AGENTS.md loading ── */
static char* load_agents_md(void) {
    if (!g_agents_enabled) return NULL;
    if (g_agents_content) return g_agents_content; /* cached */

    /* Check common locations */
    static const char *candidates[] = {
        "./AGENTS.md",
        "./docs/AGENTS.md",
        "./.ai/AGENTS.md",
        NULL
    };

    for (int i = 0; candidates[i]; i++) {
        FILE *f = fopen(candidates[i], "r");
        if (f) {
            fseek(f, 0, SEEK_END);
            long fsize = ftell(f);
            fseek(f, 0, SEEK_SET);
            if (fsize > 0 && fsize < 100000) {
                g_agents_content = malloc(fsize + 1);
                fread(g_agents_content, 1, fsize, f);
                g_agents_content[fsize] = '\0';
                fclose(f);
                fprintf(stderr, "\033[2m[ai] loaded AGENTS.md from %s\033[0m\n", candidates[i]);
                return g_agents_content;
            }
            fclose(f);
        }
    }
    return NULL;
}

/* ── Background session management ── */
static void save_session_state(const char *prompt, const char *messages, int session_idx) {
    if (!g_session_file) return;

    char path[1024];
    if (session_idx > 0)
        snprintf(path, sizeof(path), "%s.%d", g_session_file, session_idx);
    else
        snprintf(path, sizeof(path), "%s", g_session_file);

    FILE *f = fopen(path, "w");
    if (f) {
        fprintf(f, "PROMPT: %s\n", prompt ? prompt : "");
        if (messages) fputs(messages, f);
        fclose(f);
        fprintf(stderr, "\033[2m[ai] Session saved to %s\033[0m\n", path);
    }
}

/* ── Copy response to system clipboard ── */
static void copy_to_clipboard(const char *text) {
    if (!text || !*text || !g_copy_enabled) return;

    FILE *fp = popen("xclip -selection clipboard 2>/dev/null", "w");
    if (!fp) fp = popen("xsel --clipboard 2>/dev/null", "w");
    if (!fp) fp = popen("pbcopy 2>/dev/null", "w");

    if (fp) {
        fputs(text, fp);
        int status = pclose(fp);
        if (status == 0) {
            fprintf(stderr, "\033[2m[ai] Response copied to clipboard.\033[0m\n");
            return;
        }
    }
}

/* ── Thinking spinner animation ── */
static const char *spinner_chars = "|/-\\";

static void print_thinking_spinner(void) {
    fprintf(stderr, "\r\033[2m\033[36m  %c thinking\033[0m", spinner_chars[g_spinner_idx % 4]);
    fflush(stderr);
    g_spinner_idx++;
}

/* ── Token estimation (rough) ── */
static long estimate_tokens(const char *text) {
    if (!text) return 0;
    /* Rough estimate: 1 token ≈ 4 chars for English text */
    return (long)(strlen(text) / 4.0);
}

static void print_token_stats(const char *prompt, const char *response) {
    if (!g_tokens_total && !g_tokens_prompt && !g_tokens_completion) return;

    long prompt_toks = g_tokens_prompt > 0 ? g_tokens_prompt : estimate_tokens(prompt);
    long comp_toks = g_tokens_completion > 0 ? g_tokens_completion : estimate_tokens(response);
    long total = prompt_toks + comp_toks;

    fprintf(stderr, "\033[2m  %ld tok · %ld in · %ld out\033[0m\n",
            total, prompt_toks, comp_toks);
}
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

__attribute__((unused)) static void emit_utf8(uint32_t cp);
__attribute__((unused)) static void print_markdown_table(const char *content);
__attribute__((unused)) static void print_warning_box(const char *title, const char *body);
__attribute__((unused)) static char* load_skills_from_dir(const char *base_dir);
__attribute__((unused)) static void save_session_state(const char *prompt, const char *messages, int session_idx);
__attribute__((unused)) static void print_thinking_spinner(void);
__attribute__((unused)) static void print_token_stats(const char *prompt, const char *response);
__attribute__((unused)) static void print_json_string_unescaped(const char *s, int len) {
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
static int g_turn_count = 0;
static int total_tool_count = 0;
static int g_continue_until_done = 0;
static volatile int g_agent_loop_active = 0; /* 1 while the has_more agent loop runs */

#define LINEED_MAX_LINE    1048576

/* Shared stdin accumulation buffer for :btw detection (used by progress_cb + poll) */
static char g_agent_stdin_buf[LINEED_MAX_LINE] = "";
static int  g_agent_stdin_len = 0;
static char g_btw_message[LINEED_MAX_LINE] = "";
static volatile int g_btw_available = 0;

static void disable_raw_mode(void) {
    if (raw_mode_active) {
        tcsetattr(STDIN_FILENO, TCSAFLUSH, &orig_termios);
        raw_mode_active = 0;
    }
}

static void sig_handler(int sig) {
    disable_raw_mode();
    fprintf(stderr, "\n[ai] Signal %d received — restoring terminal and exiting.\n", sig);
    exit(128 + sig);
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
                    if (g_agent_loop_active && !g_permission_mode) {
                        /* Safety: can't enable auto-approve during an active task */
                        fprintf(stderr,
                            "\n\033[2m[Shift-Tab: auto-approve can only be enabled "
                            "before a task starts]\033[0m\n");
                        fflush(stderr);
                    } else {
                        g_permission_mode = (g_permission_mode + 1) % 3;
                        if (g_permission_mode == 0)
                            setenv("INFER_AUTO_APPROVE", "1", 1);
                        else
                            unsetenv("INFER_AUTO_APPROVE");
                        const char *mode_names[] = { "auto", "plan", "manual" };
                        fprintf(stderr, "\033[2mpermission mode: %s\033[0m\n", mode_names[g_permission_mode]);
                        fflush(stderr);
                    }
                    g_agent_stdin_len = 0;
                } else {
                    g_esc_requested = 1;
                }
            } else if (ch == 15) { /* Ctrl+O */
                g_hide_details = !g_hide_details;
                redraw_turn_history(current_session_id);
                fflush(stdout);
            } else if (ch == '\r' || ch == '\n') {
                /* Complete line — check for :btw command */
                g_agent_stdin_buf[g_agent_stdin_len] = '\0';
                const char *line = g_agent_stdin_buf;
                while (*line == ' ') line++;
                if (strncmp(line, ":btw ", 5) == 0) {
                    line += 5;
                    while (*line == ' ') line++;
                } else if (strncmp(line, ":btw", 4) == 0) {
                    line += 4;
                    while (*line == ' ') line++;
                }
                if (*line) {
                    strncpy(g_btw_message, line, sizeof(g_btw_message) - 1);
                    g_btw_message[sizeof(g_btw_message) - 1] = '\0';
                    g_btw_available = 1;
                    fprintf(stderr, "\n\033[2m[scheduled] %s\033[0m\n", g_btw_message);
                    fflush(stderr);
                }
                g_agent_stdin_len = 0;
            } else if (ch == 127 || ch == 8) {
                if (g_agent_stdin_len > 0) {
                    g_agent_stdin_len--;
                    fprintf(stderr, "\b \b");
                    fflush(stderr);
                }
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
                if (g_agent_loop_active && !g_permission_mode) {
                    fprintf(stderr,
                        "\n\033[2m[Shift-Tab: auto-approve can only be enabled "
                        "before a task starts]\033[0m\n");
                    fflush(stderr);
                } else {
                    g_permission_mode = (g_permission_mode + 1) % 3;
                    if (g_permission_mode == 0) setenv("INFER_AUTO_APPROVE", "1", 1);
                    else                        unsetenv("INFER_AUTO_APPROVE");
                    const char *mode_names[] = { "auto", "plan", "manual" };
                    fprintf(stderr, "\n\033[2mpermission mode: %s\033[0m\n", mode_names[g_permission_mode]);
                    fflush(stderr);
                }
                g_agent_stdin_len = 0;
            } else {
                g_esc_requested = 1;
            }
        } else if (ch == 15) { /* Ctrl+O */
            g_hide_details = !g_hide_details;
            redraw_turn_history(current_session_id);
            fflush(stdout);
        } else if (ch == '\r' || ch == '\n') {
            g_agent_stdin_buf[g_agent_stdin_len] = '\0';
            const char *line = g_agent_stdin_buf;
            while (*line == ' ') line++;
            if (strncmp(line, ":btw ", 5) == 0) {
                line += 5;
                while (*line == ' ') line++;
            } else if (strncmp(line, ":btw", 4) == 0) {
                line += 4;
                while (*line == ' ') line++;
            }
            if (*line) {
                strncpy(g_btw_message, line, sizeof(g_btw_message) - 1);
                g_btw_message[sizeof(g_btw_message) - 1] = '\0';
                g_btw_available = 1;
                fprintf(stderr, "\n\033[2m[scheduled] %s\033[0m\n", g_btw_message);
                fflush(stderr);
            }
            g_agent_stdin_len = 0;
        } else if (ch == 127 || ch == 8) {
            if (g_agent_stdin_len > 0) {
                g_agent_stdin_len--;
                fprintf(stderr, "\b \b");
                fflush(stderr);
            }
        } else if (ch >= 32 && ch <= 126) {
            if (g_agent_stdin_len < (int)sizeof(g_agent_stdin_buf) - 1) {
                g_agent_stdin_buf[g_agent_stdin_len++] = ch;
                fputc(ch, stderr);
                fflush(stderr);
            }
        }
    }

    tcsetattr(STDIN_FILENO, TCSANOW, &orig_termios);
}

/* ── Minimal interactive line editor with history ─────────────────────────── */

#define LINEED_MAX_LINE    1048576
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
    static char buf[LINEED_MAX_LINE];
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

static int get_next_scheduled_task_delay(void) {
    char dir_path[512];
    snprintf(dir_path, sizeof(dir_path), "%s/.config/ai/scheduled_tasks", getenv("HOME"));
    DIR *d = opendir(dir_path);
    if (!d) return -1;
    
    struct dirent *dir;
    int min_delay = -1;
    time_t now = time(NULL);
    
    while ((dir = readdir(d)) != NULL) {
        if (strstr(dir->d_name, ".json") != NULL) {
            char filepath[1024];
            snprintf(filepath, sizeof(filepath), "%s/%s", dir_path, dir->d_name);
            FILE *f = fopen(filepath, "r");
            if (f) {
                fseek(f, 0, SEEK_END);
                long fsize = ftell(f);
                fseek(f, 0, SEEK_SET);
                if (fsize > 0 && fsize < 65536) {
                    char *json = malloc(fsize + 1);
                    if (fread(json, 1, fsize, f) == (size_t)fsize) {
                        json[fsize] = '\0';
                        char *interval_ptr = strstr(json, "\"interval_seconds\":");
                        char *last_run_ptr = strstr(json, "\"last_run\":");
                        char *created_ptr = strstr(json, "\"created_at\":");
                        if (interval_ptr) {
                            int interval = atoi(interval_ptr + 19);
                            char *target_ptr = NULL;
                            if (last_run_ptr) {
                                char *q1 = strchr(last_run_ptr + 11, '"');
                                if (q1 && strncmp(q1 + 1, "never", 5) != 0) {
                                    target_ptr = q1 + 1;
                                }
                            }
                            if (!target_ptr && created_ptr) {
                                char *q1 = strchr(created_ptr + 13, '"');
                                if (q1) target_ptr = q1 + 1;
                            }
                            if (target_ptr) {
                                int Y, M, D, h, m, s;
                                if (sscanf(target_ptr, "%d-%d-%d %d:%d:%d", &Y, &M, &D, &h, &m, &s) == 6) {
                                    struct tm tm = {0};
                                    tm.tm_year = Y - 1900;
                                    tm.tm_mon = M - 1;
                                    tm.tm_mday = D;
                                    tm.tm_hour = h;
                                    tm.tm_min = m;
                                    tm.tm_sec = s;
                                    tm.tm_isdst = -1;
                                    time_t start_time = mktime(&tm);
                                    if (start_time != (time_t)-1) {
                                        int delay = (start_time + interval) - now;
                                        if (delay < 0) delay = 0;
                                        if (min_delay == -1 || delay < min_delay) {
                                            min_delay = delay;
                                        }
                                    }
                                }
                            }
                        }
                    }
                    free(json);
                }
                fclose(f);
            }
        }
    }
    closedir(d);
    return min_delay;
}

/*
 * read_line_interactive: draw prompt, read a line with full editing + history.
 * Returns malloc'd string (caller frees), NULL on EOF/Ctrl+D with empty buffer,
 * or "\033[Z" on Shift-Tab (caller should toggle auto-approve and retry).
 */
static char *read_line_interactive(const char *prompt) {
    if (!isatty(STDIN_FILENO)) {
        static char buf[LINEED_MAX_LINE];
        write(STDOUT_FILENO, prompt, strlen(prompt));
        if (!fgets(buf, sizeof(buf), stdin)) return NULL;
        size_t l = strlen(buf);
        while (l > 0 && (buf[l - 1] == '\n' || buf[l - 1] == '\r')) buf[--l] = '\0';
        return strdup(buf);
    }

    struct termios saved, raw;
    if (tcgetattr(STDIN_FILENO, &saved) < 0) {
        static char buf[LINEED_MAX_LINE];
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

    static char buf[LINEED_MAX_LINE];
    int  len    = 0;
    int  cursor = 0;
    int  hidx   = lineed_history_len;
    static char saved_buf[LINEED_MAX_LINE];
    buf[0] = '\0';
    if (g_btw_available) {
        strncpy(buf, g_btw_message, sizeof(buf) - 1);
        buf[sizeof(buf) - 1] = '\0';
        len = strlen(buf);
        cursor = len;
        g_btw_available = 0;
    }
    saved_buf[0] = '\0';

    if (len > 0) {
        write(STDOUT_FILENO, buf, len);
    }

    for (;;) {
        struct timeval tv;
        tv.tv_sec = 1;
        tv.tv_usec = 0;
        
        fd_set rfds;
        FD_ZERO(&rfds);
        FD_SET(STDIN_FILENO, &rfds);
        
        int retval = select(STDIN_FILENO + 1, &rfds, NULL, NULL, &tv);
        if (retval == -1) {
            if (errno == EINTR) continue;
            break;
        } else if (retval == 0) {
            int delay = get_next_scheduled_task_delay();
            lineed_redraw(prompt, buf, len, cursor);
            if (delay >= 0) {
                int cols = lineed_term_cols();
                char timer_str[64];
                int m = delay / 60;
                int s = delay % 60;
                if (m > 0)
                    snprintf(timer_str, sizeof(timer_str), "⏱ %dm%02ds", m, s);
                else
                    snprintf(timer_str, sizeof(timer_str), "⏱ %ds", s);
                
                int padding = (m > 0) ? 12 : 9;
                int pos = cols - padding;
                if (pos < 20) pos = 20;
                
                char draw_cmd[256];
                snprintf(draw_cmd, sizeof(draw_cmd), "\0337\033[%dG\033[2m[%s]\033[0m\0338", pos, timer_str);
                write(STDOUT_FILENO, draw_cmd, strlen(draw_cmd));
            }
            continue;
        }

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

        if (c == 15) { /* Ctrl+O: toggle details (tools & thinking & jobs) */
            g_hide_details = !g_hide_details;
            redraw_turn_history(current_session_id);
            lineed_redraw(prompt, buf, len, cursor);
            continue;
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
            struct pollfd pfd = { STDIN_FILENO, POLLIN, 0 };
            if (poll(&pfd, 1, 0) == 0) {
                lineed_redraw(prompt, buf, len, cursor);
            }
        }
    }

    LINEED_RESTORE();
#undef LINEED_RESTORE
#undef LINEED_INS
    buf[len] = '\0';
    
    if (len > 30000) {
        fprintf(stderr, "\n\033[1;33m[ai] Warning: large prompt inserted (%d bytes). "
                        "If this exceeds your model's context budget, consider using the "
                        "`delegate_task` tool to have a subagent process it in a fresh context.\033[0m\n", len);
    }
    
    return strdup(buf);
}

/* Recursively kill a process and all of its descendants (via /proc children).
   Fixes the orphaned-subprocess leak where `pkill -P $PPID` only killed the
   direct /bin/sh -c wrapper and left the real command running as a grandchild. */
static void kill_process_tree(pid_t pid) {
    char path[64];
    snprintf(path, sizeof(path), "/proc/%d/task/%d/children", pid, pid);
    FILE *cf = fopen(path, "r");
    if (cf) {
        pid_t child;
        while (fscanf(cf, "%d", &child) == 1) {
            kill_process_tree(child);
        }
        fclose(cf);
    }
    kill(pid, SIGKILL);
}

/* Kill every process launched by this run_shell_command_timeout call. */
static void kill_command_subprocess(void) {
    char path[64];
    snprintf(path, sizeof(path), "/proc/%d/task/%d/children", getpid(), getpid());
    FILE *cf = fopen(path, "r");
    if (cf) {
        pid_t child;
        while (fscanf(cf, "%d", &child) == 1) {
            kill_process_tree(child);
        }
        fclose(cf);
    }
}

static char* run_shell_command_timeout(const char *cmd, int *exit_status, int timeout_sec) {
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

    time_t start_time = time(NULL);
    int interrupted = 0;
    while (1) {
        if (timeout_sec > 0 && time(NULL) - start_time > timeout_sec) {
            interrupted = 2;
            kill_command_subprocess();
            
            char timeout_msg[256];
            snprintf(timeout_msg, sizeof(timeout_msg), "\n[SYSTEM WARNING: Command timed out after %d seconds. Process killed. Partial output above. Evaluate if script is stuck in an infinite loop, waiting for user input, or just intrinsically slow.]\n", timeout_sec);
            size_t tlen = strlen(timeout_msg);
            if (len + tlen >= size - 1) {
                size += tlen + 1024;
                char *new_buf = realloc(buf, size);
                if (new_buf) buf = new_buf;
            }
            if (buf) {
                memcpy(buf + len, timeout_msg, tlen);
                len += tlen;
                buf[len] = '\0';
            }
            if (exit_status) *exit_status = 124;
            fprintf(stderr, "\n\033[1;31m[ai] Tool execution timed out after %ds.\033[0m\n", timeout_sec);
            break;
        }

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
                    kill_command_subprocess();
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
        if (interrupted == 1) {
            *exit_status = 130; // SIGINT / interrupted status
        } else if (interrupted == 2) {
            *exit_status = 124; // Timeout
        } else if (status == -1) {
            *exit_status = -1;
        } else {
            *exit_status = WIFEXITED(status) ? WEXITSTATUS(status) : status;
        }
    }

    return buf;
}

char* run_shell_command(const char *cmd, int *exit_status) {
    int timeout_sec = 120;
    const char *env_timeout = getenv("INFER_COMMAND_TIMEOUT");
    if (env_timeout && *env_timeout) {
        int v = atoi(env_timeout);
        if (v > 0) timeout_sec = v;
    }
    return run_shell_command_timeout(cmd, exit_status, timeout_sec);
}

/* Extract the string value of a key from a flat JSON object string. */
static char* json_get_string(const char *json_str, const char *key) {
    if (!json_str || !key) return NULL;
    jsmn_parser p;
    jsmntok_t tok[64];
    jsmn_init(&p);
    int r = jsmn_parse(&p, json_str, strlen(json_str), tok, 64);
    if (r <= 0) {
        if (json_str && json_str[0] != '{' && json_str[0] != '[') return strdup(json_str);
        return NULL;
    }
    if (tok[0].type == JSMN_STRING) {
        return unescape_json_string(json_str + tok[0].start, tok[0].end - tok[0].start);
    }
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
    const char *aliases[8] = {NULL};
    int n_aliases = 0;
    if (strcmp(key, "path") == 0) {
        aliases[0] = "file"; aliases[1] = "filepath"; aliases[2] = "filename"; aliases[3] = "file_path"; aliases[4] = "p";
        n_aliases = 5;
    } else if (strcmp(key, "command") == 0) {
        aliases[0] = "cmd"; aliases[1] = "command_line"; aliases[2] = "CommandLine"; aliases[3] = "args"; aliases[4] = "script"; aliases[5] = "code"; aliases[6] = "c";
        n_aliases = 7;
    } else if (strcmp(key, "query") == 0) {
        aliases[0] = "q"; aliases[1] = "search"; aliases[2] = "prompt"; aliases[3] = "term";
        n_aliases = 4;
    } else if (strcmp(key, "url") == 0) {
        aliases[0] = "uri"; aliases[1] = "link"; aliases[2] = "address"; aliases[3] = "u";
        n_aliases = 4;
    }
    for (int a = 0; a < n_aliases; a++) {
        int aklen = (int)strlen(aliases[a]);
        for (int i = 1; i < r - 1; i++) {
            if (tok[i].type == JSMN_STRING &&
                tok[i].end - tok[i].start == aklen &&
                strncmp(json_str + tok[i].start, aliases[a], aklen) == 0 &&
                tok[i+1].type == JSMN_STRING) {
                return unescape_json_string(json_str + tok[i+1].start,
                                            tok[i+1].end - tok[i+1].start);
            }
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

struct stream_context {
    char *id;
    char *object;
    long created;
    char *model_name;
    char *finish_reason;

    char *accumulated_reasoning;
    size_t reasoning_len;
    size_t reasoning_cap;

    char *accumulated_content;
    size_t content_len;
    size_t content_cap;

    struct {
        int index;
        char *id;
        char *name;
        char *arguments;
        size_t arguments_len;
        size_t arguments_cap;
    } tool_calls[64];
    int num_tool_calls;

    int prompt_tokens;
    int completion_tokens;
    int total_tokens;

    int quiet_mode;
    int printed_thinking_header;
    int printed_thinking_footer;

    char *line_buf;
    size_t line_len;
    size_t line_cap;

    struct response *original_chunk;
};

static void init_stream_context(struct stream_context *ctx, struct response *original_chunk, int quiet_mode) {
    memset(ctx, 0, sizeof(struct stream_context));
    ctx->original_chunk = original_chunk;
    ctx->quiet_mode = quiet_mode;
}

static void free_stream_context(struct stream_context *ctx) {
    if (ctx->id) free(ctx->id);
    if (ctx->object) free(ctx->object);
    if (ctx->model_name) free(ctx->model_name);
    if (ctx->finish_reason) free(ctx->finish_reason);
    if (ctx->accumulated_reasoning) free(ctx->accumulated_reasoning);
    if (ctx->accumulated_content) free(ctx->accumulated_content);
    for (int i = 0; i < ctx->num_tool_calls; i++) {
        if (ctx->tool_calls[i].id) free(ctx->tool_calls[i].id);
        if (ctx->tool_calls[i].name) free(ctx->tool_calls[i].name);
        if (ctx->tool_calls[i].arguments) free(ctx->tool_calls[i].arguments);
    }
    if (ctx->line_buf) free(ctx->line_buf);
}

static void buf_append_str(char **buf, size_t *len, size_t *cap, const char *str, size_t str_len) {
    if (!str || str_len == 0) return;
    if (*len + str_len >= *cap) {
        *cap = (*cap == 0) ? (str_len + 1024) : (*cap + str_len + 1024) * 2;
        char *new_buf = realloc(*buf, *cap);
        if (!new_buf) return;
        *buf = new_buf;
    }
    memcpy((*buf) + *len, str, str_len);
    *len += str_len;
    (*buf)[*len] = '\0';
}

static int get_tool_call_idx(struct stream_context *ctx, int index) {
    for (int i = 0; i < ctx->num_tool_calls; i++) {
        if (ctx->tool_calls[i].index == index) {
            return i;
        }
    }
    if (ctx->num_tool_calls < 64) {
        int i = ctx->num_tool_calls++;
        ctx->tool_calls[i].index = index;
        ctx->tool_calls[i].id = NULL;
        ctx->tool_calls[i].name = NULL;
        ctx->tool_calls[i].arguments = NULL;
        ctx->tool_calls[i].arguments_len = 0;
        ctx->tool_calls[i].arguments_cap = 0;
        return i;
    }
    return -1;
}

static void process_sse_json(struct stream_context *ctx, const char *json_str, size_t len) {
    jsmn_parser parser;
    jsmntok_t tokens[256];
    jsmn_init(&parser);
    int r = jsmn_parse(&parser, json_str, len, tokens, 256);
    if (r < 0) return;
    
    int choices_tok = -1;
    int usage_tok = -1;
    int id_tok = -1;
    int object_tok = -1;
    int created_tok = -1;
    int model_tok = -1;

    for (int i = 1; i < r; i++) {
        if (tokens[i].type == JSMN_STRING) {
            int tlen = tokens[i].end - tokens[i].start;
            if (tlen == 7 && strncmp(json_str + tokens[i].start, "choices", 7) == 0) {
                choices_tok = i + 1;
            } else if (tlen == 5 && strncmp(json_str + tokens[i].start, "usage", 5) == 0) {
                usage_tok = i + 1;
            } else if (tlen == 2 && strncmp(json_str + tokens[i].start, "id", 2) == 0) {
                id_tok = i + 1;
            } else if (tlen == 6 && strncmp(json_str + tokens[i].start, "object", 6) == 0) {
                object_tok = i + 1;
            } else if (tlen == 7 && strncmp(json_str + tokens[i].start, "created", 7) == 0) {
                created_tok = i + 1;
            } else if (tlen == 5 && strncmp(json_str + tokens[i].start, "model", 5) == 0) {
                model_tok = i + 1;
            }
        }
    }

    if (id_tok != -1 && tokens[id_tok].type == JSMN_STRING && !ctx->id) {
        ctx->id = unescape_json_string(json_str + tokens[id_tok].start, tokens[id_tok].end - tokens[id_tok].start);
    }
    if (object_tok != -1 && tokens[object_tok].type == JSMN_STRING && !ctx->object) {
        ctx->object = unescape_json_string(json_str + tokens[object_tok].start, tokens[object_tok].end - tokens[object_tok].start);
    }
    if (created_tok != -1 && tokens[created_tok].type == JSMN_PRIMITIVE && ctx->created == 0) {
        ctx->created = atol(json_str + tokens[created_tok].start);
    }
    if (model_tok != -1 && tokens[model_tok].type == JSMN_STRING && !ctx->model_name) {
        ctx->model_name = unescape_json_string(json_str + tokens[model_tok].start, tokens[model_tok].end - tokens[model_tok].start);
    }

    if (usage_tok != -1 && tokens[usage_tok].type == JSMN_OBJECT) {
        int u_end = tokens[usage_tok].end;
        int k = usage_tok + 1;
        while (k < r && tokens[k].start < u_end) {
            if (tokens[k].type == JSMN_STRING) {
                int ulen = tokens[k].end - tokens[k].start;
                if (ulen == 13 && strncmp(json_str + tokens[k].start, "prompt_tokens", 13) == 0)
                    ctx->prompt_tokens = atoi(json_str + tokens[k+1].start);
                else if (ulen == 17 && strncmp(json_str + tokens[k].start, "completion_tokens", 17) == 0)
                    ctx->completion_tokens = atoi(json_str + tokens[k+1].start);
                else if (ulen == 12 && strncmp(json_str + tokens[k].start, "total_tokens", 12) == 0)
                    ctx->total_tokens = atoi(json_str + tokens[k+1].start);
            }
            k = json_skip_token(tokens, r, k + 2);
        }
    }

    if (choices_tok != -1 && tokens[choices_tok].type == JSMN_ARRAY && tokens[choices_tok].size > 0) {
        int choice_tok = choices_tok + 1;
        if (tokens[choice_tok].type == JSMN_OBJECT) {
            int c_end = tokens[choice_tok].end;
            int delta_tok = -1;
            int finish_reason_tok = -1;
            
            int k = choice_tok + 1;
            while (k < r && tokens[k].start < c_end) {
                if (tokens[k].type == JSMN_STRING) {
                    int clen = tokens[k].end - tokens[k].start;
                    if (clen == 5 && strncmp(json_str + tokens[k].start, "delta", 5) == 0) {
                        delta_tok = k + 1;
                    } else if (clen == 13 && strncmp(json_str + tokens[k].start, "finish_reason", 13) == 0) {
                        finish_reason_tok = k + 1;
                    }
                }
                k = json_skip_token(tokens, r, k + 2);
            }

            if (finish_reason_tok != -1 && tokens[finish_reason_tok].type == JSMN_STRING && !ctx->finish_reason) {
                ctx->finish_reason = unescape_json_string(json_str + tokens[finish_reason_tok].start, tokens[finish_reason_tok].end - tokens[finish_reason_tok].start);
            }

            if (delta_tok != -1 && tokens[delta_tok].type == JSMN_OBJECT) {
                int d_end = tokens[delta_tok].end;
                int content_tok = -1;
                int reasoning_content_tok = -1;
                int tool_calls_tok = -1;

                k = delta_tok + 1;
                while (k < r && tokens[k].start < d_end) {
                    if (tokens[k].type == JSMN_STRING) {
                        int dlen = tokens[k].end - tokens[k].start;
                        if (dlen == 7 && strncmp(json_str + tokens[k].start, "content", 7) == 0) {
                            content_tok = k + 1;
                        } else if (dlen == 17 && strncmp(json_str + tokens[k].start, "reasoning_content", 17) == 0) {
                            reasoning_content_tok = k + 1;
                        } else if (dlen == 10 && strncmp(json_str + tokens[k].start, "tool_calls", 10) == 0) {
                            tool_calls_tok = k + 1;
                        }
                    }
                    k = json_skip_token(tokens, r, k + 2);
                }

                if (reasoning_content_tok != -1 && tokens[reasoning_content_tok].type == JSMN_STRING) {
                    char *reasoning_chunk = unescape_json_string(json_str + tokens[reasoning_content_tok].start, tokens[reasoning_content_tok].end - tokens[reasoning_content_tok].start);
                    if (reasoning_chunk) {
                        buf_append_str(&ctx->accumulated_reasoning, &ctx->reasoning_len, &ctx->reasoning_cap, reasoning_chunk, strlen(reasoning_chunk));
                        if (!ctx->quiet_mode && !g_hide_details) {
                            if (!ctx->printed_thinking_header) {
                                fprintf(stderr, "\r\033[2K");
                                fprintf(stderr,
                                    "\n%s▸ %s%s\n\033[2;35m",
                                    CL_DIM, "thinking", CL_RESET);
                                ctx->printed_thinking_header = 1;
                            }
                            fprintf(stderr, "%s", reasoning_chunk);
                            fflush(stderr);
                        }
                        free(reasoning_chunk);
                    }
                }

                if (content_tok != -1 && tokens[content_tok].type == JSMN_STRING) {
                    char *content_chunk = unescape_json_string(json_str + tokens[content_tok].start, tokens[content_tok].end - tokens[content_tok].start);
                    if (content_chunk) {
                        buf_append_str(&ctx->accumulated_content, &ctx->content_len, &ctx->content_cap, content_chunk, strlen(content_chunk));
                        if (ctx->printed_thinking_header && !ctx->printed_thinking_footer) {
                            fprintf(stderr, "\n%s\n", CL_RESET);
                            fflush(stderr);
                            ctx->printed_thinking_footer = 1;
                        } else if (!ctx->printed_thinking_header) {
                            fprintf(stderr, "\r\033[2K");
                            fflush(stderr);
                            ctx->printed_thinking_header = 1;
                        }
                        if (isatty(STDOUT_FILENO)) {
                            printf("\033[2m%s\033[0m", content_chunk);
                            fflush(stdout);
                        }
                        free(content_chunk);
                    }
                }

                if (tool_calls_tok != -1 && tokens[tool_calls_tok].type == JSMN_ARRAY) {
                    if (ctx->printed_thinking_header && !ctx->printed_thinking_footer) {
                        fprintf(stderr, "\033[0m\n");
                        fflush(stderr);
                        ctx->printed_thinking_footer = 1;
                    } else if (!ctx->printed_thinking_header) {
                        fprintf(stderr, "\r\033[2K");
                        fflush(stderr);
                        ctx->printed_thinking_header = 1;
                    }

                    int tc_size = tokens[tool_calls_tok].size;
                    int tc_tok = tool_calls_tok + 1;
                    for (int tc = 0; tc < tc_size; tc++) {
                        if (tokens[tc_tok].type != JSMN_OBJECT) break;
                        int tc_end = tokens[tc_tok].end;
                        
                        int idx_tok = -1;
                        int id_tok2 = -1;
                        int func_tok = -1;

                        int j = tc_tok + 1;
                        while (j < r && tokens[j].start < tc_end) {
                            if (tokens[j].type == JSMN_STRING) {
                                int len = tokens[j].end - tokens[j].start;
                                if (len == 5 && strncmp(json_str + tokens[j].start, "index", 5) == 0) {
                                    idx_tok = j + 1;
                                } else if (len == 2 && strncmp(json_str + tokens[j].start, "id", 2) == 0) {
                                    id_tok2 = j + 1;
                                } else if (len == 8 && strncmp(json_str + tokens[j].start, "function", 8) == 0) {
                                    func_tok = j + 1;
                                }
                            }
                            j = json_skip_token(tokens, r, j + 2);
                        }

                        if (idx_tok != -1 && tokens[idx_tok].type == JSMN_PRIMITIVE) {
                            int tc_index = atoi(json_str + tokens[idx_tok].start);
                            int internal_idx = get_tool_call_idx(ctx, tc_index);
                            if (internal_idx != -1) {
                                if (id_tok2 != -1 && tokens[id_tok2].type == JSMN_STRING) {
                                    if (ctx->tool_calls[internal_idx].id) free(ctx->tool_calls[internal_idx].id);
                                    ctx->tool_calls[internal_idx].id = unescape_json_string(json_str + tokens[id_tok2].start, tokens[id_tok2].end - tokens[id_tok2].start);
                                }
                                if (func_tok != -1 && tokens[func_tok].type == JSMN_OBJECT) {
                                    int f_end = tokens[func_tok].end;
                                    int name_tok = -1;
                                    int args_tok = -1;
                                    
                                    int k_f = func_tok + 1;
                                    while (k_f < r && tokens[k_f].start < f_end) {
                                        if (tokens[k_f].type == JSMN_STRING) {
                                            int len = tokens[k_f].end - tokens[k_f].start;
                                            if (len == 4 && strncmp(json_str + tokens[k_f].start, "name", 4) == 0) {
                                                name_tok = k_f + 1;
                                            } else if (len == 9 && strncmp(json_str + tokens[k_f].start, "arguments", 9) == 0) {
                                                args_tok = k_f + 1;
                                            }
                                        }
                                        k_f = json_skip_token(tokens, r, k_f + 2);
                                    }

                                    if (name_tok != -1 && tokens[name_tok].type == JSMN_STRING) {
                                        if (ctx->tool_calls[internal_idx].name) free(ctx->tool_calls[internal_idx].name);
                                        ctx->tool_calls[internal_idx].name = unescape_json_string(json_str + tokens[name_tok].start, tokens[name_tok].end - tokens[name_tok].start);
                                    }
                                    if (args_tok != -1 && tokens[args_tok].type == JSMN_STRING) {
                                        char *args_chunk = unescape_json_string(json_str + tokens[args_tok].start, tokens[args_tok].end - tokens[args_tok].start);
                                        if (args_chunk) {
                                            buf_append_str(&ctx->tool_calls[internal_idx].arguments,
                                                           &ctx->tool_calls[internal_idx].arguments_len,
                                                           &ctx->tool_calls[internal_idx].arguments_cap,
                                                           args_chunk, strlen(args_chunk));
                                            free(args_chunk);
                                        }
                                    }
                                }
                            }
                        }
                        tc_tok = json_skip_token(tokens, r, tc_tok + 1);
                    }
                }
            }
        }
    }
}

static void process_sse_line(struct stream_context *ctx, const char *line, size_t len) {
    while (len > 0 && isspace((unsigned char)*line)) {
        line++;
        len--;
    }
    while (len > 0 && isspace((unsigned char)line[len - 1])) {
        len--;
    }
    if (len == 0) return;

    if (strncmp(line, "data:", 5) == 0) {
        const char *data_ptr = line + 5;
        size_t data_len = len - 5;
        while (data_len > 0 && isspace((unsigned char)*data_ptr)) {
            data_ptr++;
            data_len--;
        }
        if (data_len == 0) return;
        if (strncmp(data_ptr, "[DONE]", 6) == 0) {
            return;
        }
        process_sse_json(ctx, data_ptr, data_len);
    }
}

static size_t stream_write_cb(void *ptr, size_t size, size_t nmemb, void *userdata) {
    size_t realsize = size * nmemb;
    struct stream_context *ctx = (struct stream_context *)userdata;

    if (ctx->original_chunk && ctx->original_chunk->data == NULL && ctx->line_len > 0) {
        int q = ctx->quiet_mode;
        struct response *orig = ctx->original_chunk;
        free_stream_context(ctx);
        init_stream_context(ctx, orig, q);
    }

    buf_append_str(&ctx->line_buf, &ctx->line_len, &ctx->line_cap, (const char *)ptr, realsize);

    if (strstr(ctx->line_buf, "Loading model")) {
        if (ctx->original_chunk->data) free(ctx->original_chunk->data);
        ctx->original_chunk->data = strdup(ctx->line_buf);
        ctx->original_chunk->size = strlen(ctx->line_buf);
    }

    size_t scan_pos = 0;
    while (scan_pos < ctx->line_len) {
        char *newline_ptr = strchr(ctx->line_buf + scan_pos, '\n');
        if (!newline_ptr) break;
        size_t line_length = newline_ptr - (ctx->line_buf + scan_pos);
        process_sse_line(ctx, ctx->line_buf + scan_pos, line_length);
        scan_pos += line_length + 1;
    }

    if (scan_pos > 0) {
        if (scan_pos < ctx->line_len) {
            memmove(ctx->line_buf, ctx->line_buf + scan_pos, ctx->line_len - scan_pos);
            ctx->line_len -= scan_pos;
            ctx->line_buf[ctx->line_len] = '\0';
        } else {
            ctx->line_len = 0;
            ctx->line_buf[0] = '\0';
        }
    }

    return realsize;
}

static void reconstruct_final_json(struct stream_context *ctx) {
    char *tool_calls_json = NULL;
    size_t tc_len = 0;
    size_t tc_cap = 0;
    
    if (ctx->num_tool_calls > 0) {
        buf_append_str(&tool_calls_json, &tc_len, &tc_cap, "[", 1);
        for (int i = 0; i < ctx->num_tool_calls; i++) {
            if (i > 0) {
                buf_append_str(&tool_calls_json, &tc_len, &tc_cap, ",", 1);
            }
            char *safe_id = ctx->tool_calls[i].id ? json_escape(ctx->tool_calls[i].id) : strdup("");
            char *safe_name = ctx->tool_calls[i].name ? json_escape(ctx->tool_calls[i].name) : strdup("");
            char *safe_args = ctx->tool_calls[i].arguments ? json_escape(ctx->tool_calls[i].arguments) : strdup("");
            
            size_t item_cap = strlen(safe_id) + strlen(safe_name) + strlen(safe_args) + 128;
            char *tc_item = malloc(item_cap);
            int tc_item_len = snprintf(tc_item, item_cap,
                "{\"id\":\"%s\",\"type\":\"function\",\"function\":{\"name\":\"%s\",\"arguments\":\"%s\"}}",
                safe_id, safe_name, safe_args);
            
            buf_append_str(&tool_calls_json, &tc_len, &tc_cap, tc_item, tc_item_len);
            
            free(tc_item);
            free(safe_id);
            free(safe_name);
            free(safe_args);
        }
        buf_append_str(&tool_calls_json, &tc_len, &tc_cap, "]", 1);
    }

    char *safe_reasoning = ctx->accumulated_reasoning ? json_escape(ctx->accumulated_reasoning) : strdup("");
    char *safe_content = ctx->accumulated_content ? json_escape(ctx->accumulated_content) : strdup("");
    
    size_t final_cap = (ctx->id ? strlen(ctx->id) : 0) +
                       (ctx->object ? strlen(ctx->object) : 0) +
                       (ctx->model_name ? strlen(ctx->model_name) : 0) +
                       strlen(safe_reasoning) +
                       strlen(safe_content) +
                       (tool_calls_json ? strlen(tool_calls_json) : 0) +
                       (ctx->finish_reason ? strlen(ctx->finish_reason) : 0) +
                       1024;
    char *final_json = malloc(final_cap);
    
    char *message_fields = malloc(final_cap);
    size_t mf_len = 0;
    mf_len += sprintf(message_fields + mf_len, "\"role\":\"assistant\"");
    if (ctx->accumulated_content) {
        mf_len += sprintf(message_fields + mf_len, ",\"content\":\"%s\"", safe_content);
    } else {
        mf_len += sprintf(message_fields + mf_len, ",\"content\":null");
    }
    if (ctx->accumulated_reasoning) {
        mf_len += sprintf(message_fields + mf_len, ",\"reasoning_content\":\"%s\"", safe_reasoning);
    }
    if (tool_calls_json) {
        mf_len += sprintf(message_fields + mf_len, ",\"tool_calls\":%s", tool_calls_json);
    }

    snprintf(final_json, final_cap,
        "{\"id\":\"%s\",\"object\":\"%s\",\"created\":%ld,\"model\":\"%s\","
        "\"choices\":[{\"index\":0,\"message\":{%s},\"finish_reason\":%s%s%s}],"
        "\"usage\":{\"prompt_tokens\":%d,\"completion_tokens\":%d,\"total_tokens\":%d}}",
        ctx->id ? ctx->id : "chatcmpl-reconstructed",
        ctx->object ? ctx->object : "chat.completion",
        ctx->created,
        ctx->model_name ? ctx->model_name : "unknown",
        message_fields,
        ctx->finish_reason ? "\"" : "",
        ctx->finish_reason ? ctx->finish_reason : "null",
        ctx->finish_reason ? "\"" : "",
        ctx->prompt_tokens,
        ctx->completion_tokens,
        ctx->total_tokens);

    ctx->original_chunk->data = final_json;
    ctx->original_chunk->size = strlen(final_json);

    free(message_fields);
    if (tool_calls_json) free(tool_calls_json);
    free(safe_reasoning);
    free(safe_content);
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

#include "ai_session.h"
#include "ai_terminal.h"





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

    if (url && (!*url || !**url) && strlen(f_url) > 0) *url = f_url;
    if (key && (!*key || !**key) && strlen(f_key) > 0) *key = f_key;
    if (model && (!*model || !**model) && strlen(f_model) > 0) *model = f_model;
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

/* Live sampling presets for the interactive REPL. Sets the per-request sampling
   statics (read when each request payload is built) so the switch takes effect on
   the NEXT turn with no server restart and no env-file edit. Values mirror
   `ai-backend mode` (which persists to ~/.local/share/ai/env for new processes).
   Per-request penalties follow the Qwen3.8 doc: neutral (0.0) in thinking mode,
   presence 1.5 in instruct mode. frequency/presence of 0.0 are omitted from the
   request (the builder only emits penalties > 0). */
static int apply_mode_preset(const char *name) {
    static const char *effort = NULL;
    float t = -1.0f, p = -1.0f, minp = -1.0f, pres = 0.0f;
    int k = -1;

    if (strcasecmp(name, "xhigh") == 0) {
        t = 1.0f; p = 0.95f; k = 20; minp = 0.0f; pres = 0.0f; effort = "xhigh";
    } else if (strcasecmp(name, "normal") == 0 || strcasecmp(name, "medium") == 0) {
        t = 1.0f; p = 0.95f; k = 20; minp = 0.0f; pres = 0.0f; effort = "medium";
    } else if (strcasecmp(name, "low") == 0) {
        t = 1.0f; p = 0.95f; k = 20; minp = 0.0f; pres = 0.0f; effort = "low";
    } else if (strcasecmp(name, "instruct") == 0 || strcasecmp(name, "chat") == 0) {
        t = 0.7f; p = 0.80f; k = 20; minp = 0.0f; pres = 1.5f; effort = "none";
    } else {
        return 0;
    }

    free(reasoning_effort_val);
    reasoning_effort_val = strdup(effort);
    temperature_val = t;
    top_p_val = p;
    top_k_val = k;
    min_p_val = minp;
    presence_penalty_val = pres;
    /* frequency_penalty_val is intentionally NOT touched: it is this box's
       loop-breaker (INFER_FREQ_PENALTY, default 0.10) and the Qwen3.8 doc does
       not define a frequency penalty, so keep whatever the env set. */
    return 1;
}

static void print_mode_current(void) {
    printf("  \033[2mcurrent sampling (this session):\033[0m\n");
    printf("  temperature=%s  top_p=%s  top_k=%s  min_p=%s\n",
           temperature_val >= 0.0f ? "set" : "default",
           top_p_val >= 0.0f ? "set" : "default",
           top_k_val > 0 ? "set" : "default",
           min_p_val >= 0.0f ? "set" : "default");
    printf("  presence_penalty=%.2f  frequency_penalty=%.2f  reasoning_effort=%s\n",
           presence_penalty_val, frequency_penalty_val,
           (reasoning_effort_val && *reasoning_effort_val) ? reasoning_effort_val : "(default)");
    printf("  presets: xhigh (deep) · normal (medium) · low (fast) · instruct (no CoT)\n");
}

int main(int argc, char **argv) {
    signal(SIGINT, sig_handler);
    signal(SIGTERM, sig_handler);
    time_t current_time = time(NULL);
    snprintf(current_session_id, sizeof(current_session_id), "sess_%ld", (long)current_time);
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

    // Parse set-default and version options first (all exit early)
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-v") == 0 || strcmp(argv[i], "--version") == 0) {
            printf("ai %s\n", AI_VERSION);
            return 0;
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

    /* metrics sub-command */
    if (argc >= 2 && strcmp(argv[1], "metrics") == 0) {
        char script[1024];
        if (access("./ai_mcp.py", R_OK) == 0) {
            snprintf(script, sizeof(script), "./ai_mcp.py");
        } else {
            const char *home = getenv("HOME");
            snprintf(script, sizeof(script), "%s/.local/bin/ai_mcp.py", home ? home : "~");
        }
        char cmd[8192];
        snprintf(cmd, sizeof(cmd), "python3 %s show-metrics", script);
        return system(cmd);
    }

    // Parse help flags first
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            printf("Usage: ai [options] [\"prompt\"] [path/to/image.png]\n\n");
            printf("A minimal, agentic CLI tool for piping anything into an LLM and executing terminal work.\n\n");
            printf("Options:\n");
            printf("  -i, --interactive    Start an interactive multi-turn chat session.\n");
            printf("  -y, --yes            Auto-approve all command execution requests (FULL AUTONOMY mode).\n");
            printf("  --plan               PLAN mode: investigate & report, but make NO changes until you present a\n");
            printf("                       plan via present_plan() and the user approves it; then work autonomously.\n");
            printf("  --manual             MANUAL mode: ask for explicit approval before EVERY state-changing action.\n");
            printf("                       Read-only investigation tools still run without prompting.\n");
            printf("  -c, --continue       Continue working without turn limits until the job is done.\n");
            printf("  -r, --resume         Resume the previous conversation (from the last `ai` run).\n");
            printf("  -q, --quiet          Suppress think tool reasoning output.\n");
            printf("  -n, --no-tools       Skip the agent loop — get a direct text response (fast).\n");
            printf("  -t, --temperature N  Set sampling temperature (e.g. 0.0 for deterministic, 1.0 for creative).\n");
            printf("  -p, --top-p N        Set top-p nucleus sampling (e.g. 0.95 for thinking, 0.80 for instruct).\n");
            printf("  -k, --top-k N        Set top-k sampling (e.g. 20).\n");
            printf("  --min-p N            Set min-p sampling (e.g. 0.0).\n");
            printf("  --reasoning EFFORT   Set reasoning effort for hybrid models (xhigh, medium, low, none).\n");
            printf("  --mode PRESET        Sampling preset: xhigh (deep) / normal (medium) / low (fast) / instruct (no CoT).\n");
            printf("                       Sets temp/top-p/top-k/min-p/presence/reasoning in one step.\n");
            printf("  --preserve-thinking  Preserve thinking trace across turns in multi-turn chats.\n");
            printf("  -f, --file PATH      Attach a file as context (text or image).\n");
            printf("  -m, --model MODEL    Override the default model for this call.\n");
            printf("  -s, --set-default M  Set the global default model in shell configs.\n");
            printf("  -v, --version        Print the build commit and exit.\n");
            printf("                       R: optional HuggingFace repo (e.g. unsloth/gemma-4-12b-it-GGUF).\n");
            printf("  -h, --help           Display this help screen.\n\n");
            printf("Advanced options:\n");
            printf("  --no-git             Disable auto-git commit after successful commands.\n");
            printf("  --notify             Enable OS desktop notifications on task complete.\n");
            printf("  --no-notifications   Disable OS notifications.\n");
            printf("  --no-agents          Disable automatic AGENTS.md loading.\n");
            printf("  --session FILE       Save session state to FILE.\n");
            printf("  --bg, --background   Run in background mode.\n");
            printf("  --commit-msg MSG     Custom git commit message for auto-commit.\n");
            printf("  --no-copy            Disable auto-copy of last response on task_complete.\n");
            printf("  --trim-threshold N   Set context auto-compact threshold in bytes (default 100000).\n");
            printf("  --tokenizer MODEL    Pre-load tokenizer for precise token counting.\n\n");
            printf("Examples:\n");
            printf("  ai \"what's the tar command to extract .tar.gz?\"\n");
            printf("  ai -n \"what is RNA?\"                 # direct answer, no tool loop\n");
            printf("  ai -t 1.0 -p 0.95 \"design an auth middleware\" # Qwen3.8 thinking settings\n");
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

    // Re-parse all flags in detailed pass
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-i") == 0 || strcmp(argv[i], "--interactive") == 0) {
            interactive_mode = 1;
        } else if (strcmp(argv[i], "-y") == 0 || strcmp(argv[i], "--yes") == 0 ||
                   strcmp(argv[i], "-a") == 0 || strcmp(argv[i], "--auto-approve") == 0 ||
                   strcmp(argv[i], "--auto") == 0) {
            g_permission_mode = 0;
        } else if (strcmp(argv[i], "--plan") == 0) {
            g_permission_mode = 1;
        } else if (strcmp(argv[i], "--manual") == 0) {
            g_permission_mode = 2;
        } else if (strcmp(argv[i], "-c") == 0 || strcmp(argv[i], "--continue") == 0) {
            g_continue_until_done = 1;
        } else if (strcmp(argv[i], "-q") == 0 || strcmp(argv[i], "--quiet") == 0) {
            quiet_mode = 1;
        } else if (strcmp(argv[i], "-n") == 0 || strcmp(argv[i], "--no-tools") == 0) {
            no_tools_mode = 1;
        } else if (strcmp(argv[i], "--dry-run") == 0) {
            g_dry_run = 1;
        } else if (strcmp(argv[i], "--no-git") == 0) {
            g_git_commit_enabled = 0;
        } else if (strcmp(argv[i], "--notify") == 0) {
            g_notifications_enabled = 1;
        } else if (strcmp(argv[i], "--no-notifications") == 0) {
            g_notifications_enabled = 0;
        } else if (strcmp(argv[i], "--no-agents") == 0) {
            g_agents_enabled = 0;
        } else if (strcmp(argv[i], "--session") == 0 && i + 1 < argc) {
            g_session_file = argv[i+1];
            i++;
        } else if (strcmp(argv[i], "--bg") == 0 || strcmp(argv[i], "--background") == 0) {
            g_background = 1;
        } else if (strcmp(argv[i], "--commit-msg") == 0 && i + 1 < argc) {
            g_git_commit_msg = argv[i+1];
            i++;
        } else if (strcmp(argv[i], "--no-copy") == 0) {
            g_copy_enabled = 0;
        } else if (strcmp(argv[i], "--trim-threshold") == 0 && i + 1 < argc) {
            trim_threshold = atoi(argv[i+1]);
            i++;
        } else if (strcmp(argv[i], "--tokenizer") == 0 && i + 1 < argc) {
            setenv("INFER_TOKENIZER", argv[i+1], 1);
            i++;
        } else if (strcmp(argv[i], "--hide-details") == 0 || strcmp(argv[i], "--collapsed") == 0) {
            g_hide_details = 1;
        } else if (strcmp(argv[i], "--expanded") == 0 || strcmp(argv[i], "--details") == 0) {
            g_hide_details = 0;
        } else if (strcmp(argv[i], "--raw") == 0 || strcmp(argv[i], "--raw-output") == 0) {
            setenv("INFER_RAW_OUTPUT", "1", 1);
        } else if (strcmp(argv[i], "-r") == 0 || strcmp(argv[i], "--resume") == 0) {
            if (i + 1 < argc && argv[i+1][0] != '-' &&
                (strncmp(argv[i+1], "sess_", 5) == 0 || strcmp(argv[i+1], "last") == 0 ||
                 strstr(argv[i+1], ".json") != NULL)) {
                resume_session_id = argv[i+1];
                i++;
            } else {
                resume_session_id = "";
            }
        } else if ((strcmp(argv[i], "-g") == 0 || strcmp(argv[i], "--goal") == 0) && i + 1 < argc) {
            g_goal_text = strdup(argv[i+1]);
            i++;
        } else if ((strcmp(argv[i], "-t") == 0 || strcmp(argv[i], "--temperature") == 0) && i + 1 < argc) {
            temperature_val = (float)atof(argv[i+1]);
            i++;
        } else if ((strcmp(argv[i], "-p") == 0 || strcmp(argv[i], "--top-p") == 0) && i + 1 < argc) {
            top_p_val = (float)atof(argv[i+1]);
            i++;
        } else if ((strcmp(argv[i], "-k") == 0 || strcmp(argv[i], "--top-k") == 0) && i + 1 < argc) {
            top_k_val = atoi(argv[i+1]);
            i++;
        } else if (strcmp(argv[i], "--min-p") == 0 && i + 1 < argc) {
            min_p_val = (float)atof(argv[i+1]);
            i++;
        } else if ((strcmp(argv[i], "--reasoning") == 0 || strcmp(argv[i], "--reasoning-effort") == 0) && i + 1 < argc) {
            reasoning_effort_val = strdup(argv[i+1]);
            i++;
        } else if (strcmp(argv[i], "--preserve-thinking") == 0) {
            preserve_thinking_val = 1;
        } else if (strcmp(argv[i], "--mode") == 0 && i + 1 < argc) {
            free(mode_preset_val);
            mode_preset_val = strdup(argv[i+1]);
            i++;
        } else if ((strcmp(argv[i], "-m") == 0 || strcmp(argv[i], "--model") == 0 ||
                    strcmp(argv[i], "-f") == 0 || strcmp(argv[i], "--file") == 0) && i + 1 < argc) {
            i++;
        }
    }

    char *env_hide = getenv("INFER_HIDE_DETAILS");
    if (env_hide && (strcmp(env_hide, "1") == 0 || strcasecmp(env_hide, "true") == 0)) {
        g_hide_details = 1;
    }

    char *env_approve = getenv("INFER_AUTO_APPROVE");
    if (env_approve && (strcmp(env_approve, "1") == 0 || strcasecmp(env_approve, "true") == 0)) {
        g_permission_mode = 0;
    }

    char *env_perm = getenv("INFER_PERMISSION_MODE");
    if (env_perm && *env_perm) {
        if (strcasecmp(env_perm, "plan") == 0) g_permission_mode = 1;
        else if (strcasecmp(env_perm, "manual") == 0) g_permission_mode = 2;
        else if (strcasecmp(env_perm, "auto") == 0) g_permission_mode = 0;
    }

    char *env_quiet = getenv("INFER_QUIET");
    if (env_quiet && (strcmp(env_quiet, "1") == 0 || strcasecmp(env_quiet, "true") == 0)) {
        quiet_mode = 1;
    }

    char *env_resume = getenv("INFER_RESUME");
    if (env_resume && (strcmp(env_resume, "1") == 0 || strcasecmp(env_resume, "true") == 0)) {
        if (!resume_session_id) resume_session_id = "";
    } else if (env_resume && *env_resume) {
        if (!resume_session_id) resume_session_id = env_resume;
    }

    char *env_continue = getenv("INFER_CONTINUE");
    if (env_continue && (strcmp(env_continue, "1") == 0 || strcasecmp(env_continue, "true") == 0)) {
        g_continue_until_done = 1;
    }

    // Export resolved settings back to environment variables so subagents inherit them
    if (g_permission_mode == 0) {
        setenv("INFER_AUTO_APPROVE", "1", 1);
    } else {
        unsetenv("INFER_AUTO_APPROVE");
    }
    if (quiet_mode) {
        setenv("INFER_QUIET", "1", 1);
    } else {
        unsetenv("INFER_QUIET");
    }
    if (g_continue_until_done) {
        setenv("INFER_CONTINUE", "1", 1);
    } else {
        unsetenv("INFER_CONTINUE");
    }

    char *env_temp = getenv("INFER_TEMPERATURE");
    if (env_temp && *env_temp) temperature_val = (float)atof(env_temp);
    char *env_topp = getenv("INFER_TOP_P");
    if (env_topp && *env_topp) top_p_val = (float)atof(env_topp);
    char *env_topk = getenv("INFER_TOP_K");
    if (env_topk && *env_topk) top_k_val = atoi(env_topk);
    char *env_minp = getenv("INFER_MIN_P");
    if (env_minp && *env_minp) min_p_val = (float)atof(env_minp);
    char *env_reasoning = getenv("INFER_REASONING_EFFORT");
    if (env_reasoning && *env_reasoning && !reasoning_effort_val) reasoning_effort_val = strdup(env_reasoning);
    char *env_preserve_think = getenv("INFER_PRESERVE_THINKING");
    if (env_preserve_think && (strcmp(env_preserve_think, "1") == 0 || strcasecmp(env_preserve_think, "true") == 0)) {
        preserve_thinking_val = 1;
    }
    char *env_maxtok = getenv("INFER_MAX_TOKENS");
    if (env_maxtok && *env_maxtok) max_tokens_val = atoi(env_maxtok);
    char *env_freq_pen = getenv("INFER_FREQ_PENALTY");
    if (env_freq_pen && *env_freq_pen) frequency_penalty_val = (float)atof(env_freq_pen);
    char *env_pres_pen = getenv("INFER_PRESENCE_PENALTY");
    if (env_pres_pen && *env_pres_pen) presence_penalty_val = (float)atof(env_pres_pen);
    /* --mode preset overrides the env-file sampling (env is the per-machine default;
       the explicit flag is the per-invocation choice). */
    if (mode_preset_val && *mode_preset_val) {
        if (apply_mode_preset(mode_preset_val)) {
            fprintf(stderr, "\033[2m[ai] mode preset '%s' applied\033[0m\n", mode_preset_val);
        } else {
            fprintf(stderr, "\033[33m[ai] warning: unknown --mode '%s' (xhigh/normal/low/instruct); using defaults\033[0m\n", mode_preset_val);
        }
    }
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
                if (strcmp(argv[i], "-c") == 0 || strcmp(argv[i], "--continue") == 0) continue;
                if (strcmp(argv[i], "--plan") == 0 || strcmp(argv[i], "--manual") == 0
                    || strcmp(argv[i], "--auto") == 0) continue;
                if (strcmp(argv[i], "-q") == 0 || strcmp(argv[i], "--quiet") == 0) continue;
                if (strcmp(argv[i], "-n") == 0 || strcmp(argv[i], "--no-tools") == 0) continue;
                if (strcmp(argv[i], "-r") == 0 || strcmp(argv[i], "--resume") == 0) continue;
                if (strcmp(argv[i], "-v") == 0 || strcmp(argv[i], "--version") == 0) continue;
                if (strcmp(argv[i], "--preserve-thinking") == 0) continue;
                if ((strcmp(argv[i], "-m") == 0 || strcmp(argv[i], "--model") == 0 ||
                     strcmp(argv[i], "-t") == 0 || strcmp(argv[i], "--temperature") == 0 ||
                     strcmp(argv[i], "-p") == 0 || strcmp(argv[i], "--top-p") == 0 ||
                     strcmp(argv[i], "-k") == 0 || strcmp(argv[i], "--top-k") == 0 ||
                     strcmp(argv[i], "--min-p") == 0 ||
                     strcmp(argv[i], "--reasoning") == 0 || strcmp(argv[i], "--reasoning-effort") == 0 ||
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
        if (strcmp(argv[i], "-c") == 0 || strcmp(argv[i], "--continue") == 0) continue;
        if (strcmp(argv[i], "--plan") == 0 || strcmp(argv[i], "--manual") == 0
            || strcmp(argv[i], "--auto") == 0) continue;
        if (strcmp(argv[i], "-q") == 0 || strcmp(argv[i], "--quiet") == 0) continue;
        if (strcmp(argv[i], "-n") == 0 || strcmp(argv[i], "--no-tools") == 0) continue;
        if (strcmp(argv[i], "-r") == 0 || strcmp(argv[i], "--resume") == 0) continue;
        if (strcmp(argv[i], "-v") == 0 || strcmp(argv[i], "--version") == 0) continue;
        if (strcmp(argv[i], "--preserve-thinking") == 0) continue;
        if ((strcmp(argv[i], "-m") == 0 || strcmp(argv[i], "--model") == 0 ||
             strcmp(argv[i], "-t") == 0 || strcmp(argv[i], "--temperature") == 0 ||
             strcmp(argv[i], "-p") == 0 || strcmp(argv[i], "--top-p") == 0 ||
             strcmp(argv[i], "-k") == 0 || strcmp(argv[i], "--top-k") == 0 ||
             strcmp(argv[i], "--min-p") == 0 ||
             strcmp(argv[i], "--reasoning") == 0 || strcmp(argv[i], "--reasoning-effort") == 0 ||
             strcmp(argv[i], "--mode") == 0 ||
             strcmp(argv[i], "-f") == 0 || strcmp(argv[i], "--file") == 0 ||
             strcmp(argv[i], "-g") == 0 || strcmp(argv[i], "--goal") == 0) && i + 1 < argc) {
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
        if (strcmp(argv[i], "-c") == 0 || strcmp(argv[i], "--continue") == 0) continue;
        if (strcmp(argv[i], "--plan") == 0 || strcmp(argv[i], "--manual") == 0
            || strcmp(argv[i], "--auto") == 0) continue;
        if (strcmp(argv[i], "-q") == 0 || strcmp(argv[i], "--quiet") == 0) continue;
        if (strcmp(argv[i], "-n") == 0 || strcmp(argv[i], "--no-tools") == 0) continue;
        if (strcmp(argv[i], "-v") == 0 || strcmp(argv[i], "--version") == 0) continue;
        if (strcmp(argv[i], "--preserve-thinking") == 0) continue;
        if ((strcmp(argv[i], "-m") == 0 || strcmp(argv[i], "--model") == 0 ||
             strcmp(argv[i], "-t") == 0 || strcmp(argv[i], "--temperature") == 0 ||
             strcmp(argv[i], "-p") == 0 || strcmp(argv[i], "--top-p") == 0 ||
             strcmp(argv[i], "-k") == 0 || strcmp(argv[i], "--top-k") == 0 ||
             strcmp(argv[i], "--min-p") == 0 ||
             strcmp(argv[i], "--reasoning") == 0 || strcmp(argv[i], "--reasoning-effort") == 0 ||
             strcmp(argv[i], "--mode") == 0 ||
             strcmp(argv[i], "-f") == 0 || strcmp(argv[i], "--file") == 0 ||
             strcmp(argv[i], "-g") == 0 || strcmp(argv[i], "--goal") == 0) && i + 1 < argc) {
            i++; continue;
        }
        if (strcmp(argv[i], "-r") == 0 || strcmp(argv[i], "--resume") == 0) {
            if (i + 1 < argc && argv[i+1][0] != '-' &&
                (strncmp(argv[i+1], "sess_", 5) == 0 || strcmp(argv[i+1], "last") == 0 ||
                 strstr(argv[i+1], ".json") != NULL)) {
                i++;
            }
            continue;
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
        if (strcmp(argv[i], "-c") == 0 || strcmp(argv[i], "--continue") == 0) continue;
        if (strcmp(argv[i], "--plan") == 0 || strcmp(argv[i], "--manual") == 0
            || strcmp(argv[i], "--auto") == 0) continue;
        if (strcmp(argv[i], "-q") == 0 || strcmp(argv[i], "--quiet") == 0) continue;
        if (strcmp(argv[i], "-n") == 0 || strcmp(argv[i], "--no-tools") == 0) continue;
        if (strcmp(argv[i], "-v") == 0 || strcmp(argv[i], "--version") == 0) continue;
        if (strcmp(argv[i], "--preserve-thinking") == 0) continue;
        if ((strcmp(argv[i], "-m") == 0 || strcmp(argv[i], "--model") == 0 ||
             strcmp(argv[i], "-t") == 0 || strcmp(argv[i], "--temperature") == 0 ||
             strcmp(argv[i], "-p") == 0 || strcmp(argv[i], "--top-p") == 0 ||
             strcmp(argv[i], "-k") == 0 || strcmp(argv[i], "--top-k") == 0 ||
             strcmp(argv[i], "--min-p") == 0 ||
             strcmp(argv[i], "--reasoning") == 0 || strcmp(argv[i], "--reasoning-effort") == 0 ||
             strcmp(argv[i], "--mode") == 0 ||
             strcmp(argv[i], "-f") == 0 || strcmp(argv[i], "--file") == 0 ||
             strcmp(argv[i], "-g") == 0 || strcmp(argv[i], "--goal") == 0) && i + 1 < argc) {
            i++; continue;
        }
        if (strcmp(argv[i], "-r") == 0 || strcmp(argv[i], "--resume") == 0) {
            if (i + 1 < argc && argv[i+1][0] != '-' &&
                (strncmp(argv[i+1], "sess_", 5) == 0 || strcmp(argv[i+1], "last") == 0 ||
                 strstr(argv[i+1], ".json") != NULL)) {
                i++;
            }
            continue;
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

    char *active_system_prompt = load_system_prompt();
    /* Continuous Local Learning: load and append rules */
    {
        char rules_path[1024];
        char *home = getenv("HOME");
        snprintf(rules_path, sizeof(rules_path), "%s/.config/ai/rules.txt", home ? home : "");
        FILE *rfp = fopen(rules_path, "r");
        if (rfp) {
            fseek(rfp, 0, SEEK_END);
            long rlen = ftell(rfp);
            fseek(rfp, 0, SEEK_SET);
            if (rlen > 0 && rlen < 65536) {
                char *rules_buf = malloc(rlen + 1);
                if (rules_buf) {
                    size_t rb = fread(rules_buf, 1, rlen, rfp);
                    rules_buf[rb] = '\0';
                    size_t new_len = strlen(active_system_prompt) + rb + 256;
                    char *new_ap = malloc(new_len);
                    if (new_ap) {
                        snprintf(new_ap, new_len, "%s\n\n--- User-Defined Rules (Continuous Local Learning) ---\n%s", active_system_prompt, rules_buf);
                        free(active_system_prompt);
                        active_system_prompt = new_ap;
                    }
                    free(rules_buf);
                }
            }
            fclose(rfp);
        }
    }
    char *rag_memories = NULL;
    if (prompt && strlen(prompt) > 0) {
        char *safe_prompt = shell_escape(prompt);
        if (safe_prompt) {
            char cmd[8192];
            snprintf(cmd, sizeof(cmd), "python3 %s rag-memories %s", mcp_script, safe_prompt);
            rag_memories = run_shell_command(cmd, NULL);
            free(safe_prompt);
        }
    } else {
        char cmd[8192];
        snprintf(cmd, sizeof(cmd), "python3 %s rag-memories 'current context or recent tasks'", mcp_script);
        rag_memories = run_shell_command(cmd, NULL);
    }

    char *safe_system = json_escape(active_system_prompt);
    char *safe_ctx = json_escape(sys_ctx);
    char *safe_mem = memory ? json_escape(memory) : NULL;
    char *safe_triggers = triggers ? json_escape(triggers) : NULL;
    char *safe_rag = rag_memories ? json_escape(rag_memories) : NULL;
    char *sys_msg = NULL;

    /* Load AGENTS.md if enabled */
    char *agents_md = load_agents_md();

    /* Build the content string: SYSTEM_PROMPT + ctx + optional triggers + optional memory */
    size_t mlen = strlen(safe_system) + strlen(safe_ctx)
                  + (safe_triggers ? strlen(safe_triggers) + 64 : 0)
                  + (safe_mem ? strlen(safe_mem) + 64 : 0)
                  + (safe_rag ? strlen(safe_rag) + 64 : 0)
                  + (agents_md ? strlen(agents_md) + 64 : 0)
                  + (g_goal_text ? strlen(g_goal_text) + 256 : 0) + 256;

    /* Assemble piece by piece into a temporary content buffer, then JSON-wrap */
    char *content = malloc(mlen);
    int clen = snprintf(content, mlen, "%s\n\n%s", active_system_prompt, sys_ctx);
    if (g_goal_text && strlen(g_goal_text) > 0)
        clen += snprintf(content + clen, mlen - clen,
                         "\n\nMISSION BOARD:\n- Top Goal: %s\n- Status: In Progress\n- Instruction: Decompose into subtasks, execute step-by-step, verify work before finishing.", g_goal_text);
    if (triggers && strlen(triggers) > 0)
        clen += snprintf(content + clen, mlen - clen,
                         "\n\nCRITICAL SKILL TRIGGERS (obey BEFORE any other tool):\n%s", triggers);
    if (memory && strlen(memory) > 0)
        clen += snprintf(content + clen, mlen - clen,
                         "\n\nPersistent Memory/Preferences:\n%s", memory);
    if (rag_memories && strlen(rag_memories) > 0)
        clen += snprintf(content + clen, mlen - clen,
                         "\n\nRelevant Context (RAG Memories):\n%s", rag_memories);
    time_t sys_now = time(NULL);
    char time_buf[64];
    struct tm *tm_info = localtime(&sys_now);
    strftime(time_buf, sizeof(time_buf), "%Y-%m-%d %H:%M:%S %Z", tm_info);
    clen += snprintf(content + clen, mlen - clen,
                     "\n\n[IMPORTANT: Current system time is %s. Always reference the current year, month, and day in your responses. Do not assume outdated dates.]", time_buf);
    if (agents_md)
        clen += snprintf(content + clen, mlen - clen,
                         "\n\n--- Project Context (AGENTS.md) ---\n%s", agents_md);

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

    if (safe_system) free(safe_system);
    if (safe_ctx) free(safe_ctx);
    if (safe_mem) free(safe_mem);
    if (safe_triggers) free(safe_triggers);
    if (safe_rag) free(safe_rag);
    if (rag_memories) free(rag_memories);
    if (sys_ctx) free(sys_ctx);
    if (active_system_prompt) free(active_system_prompt);
    if (memory) free(memory);
    if (triggers) free(triggers);
    free(sys_msg);

    /* --resume: splice the previous conversation (as a clean user/assistant
       transcript) between the fresh system message and this turn's user prompt. */
    if (resume_session_id)
        messages_json = load_session_transcript(messages_json, mcp_script);

    // Add User Prompt
    char *raw_user_content = NULL;
    if (pipe_in && strlen(pipe_in) > 0) {
        if (pipe_writer) {
            size_t len = strlen(prompt) + strlen(pipe_in) + strlen(pipe_writer) + 256;
            raw_user_content = malloc(len);
            snprintf(raw_user_content, len, "%s\n\nContext (output of command `%s`):\n%s", prompt, pipe_writer, pipe_in);
        } else {
            size_t len = strlen(prompt) + strlen(pipe_in) + 128;
            raw_user_content = malloc(len);
            snprintf(raw_user_content, len, "%s\n\nContext:\n%s", prompt, pipe_in);
        }
    } else if (pipe_writer) {
        fprintf(stderr, "[ai] Warning: Piped command '%s' returned no stdout. The agent will execute it to inspect stderr.\n", pipe_writer);
        size_t len = strlen(prompt) + strlen(pipe_writer) + 512;
        raw_user_content = malloc(len);
        snprintf(raw_user_content, len, "%s\n\nContext:\nThe user ran the command `%s` in their terminal, but its stdout was empty. It might have failed and written to stderr. You should run this command using the execute_command tool to inspect its output/errors.", prompt, pipe_writer);
    } else {
        raw_user_content = strdup(prompt);
    }

    char *user_content = json_escape(raw_user_content);
    free(raw_user_content);

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
        add_turn_item(ITEM_USER_INPUT, "User", NULL, prompt ? prompt : "", 0, 0, 1, 0);
        run_query_this_turn = 1;
    }
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
        printf("\033[1;35mai\033[0m \033[2m· session %s · type :help for commands\033[0m\n\n",
               current_session_id);
        lineed_init();
    }

    while (keep_going) {
        if (interactive_mode && (!run_query_this_turn || !first_turn)) {
            /* Auto-compact when context grows very large */
            if (strlen(messages_json) > (size_t)(trim_threshold * 3)) {
                fprintf(stderr,
                    "\033[2m[ai] Context is large (%zu KB). Auto-compacting...\033[0m\n",
                    strlen(messages_json) / 1024);
                messages_json = compact_session(messages_json, mcp_script, c, model, NULL);
            }

            g_turn_count++;

            /* Context window indicator (if high) */
            char ctx_hint[64] = "";
            if (context_window > 0 && g_session_tokens > 0) {
                int pct = (int)(100.0 * g_session_tokens / context_window);
                if (pct > 75) {
                    snprintf(ctx_hint, sizeof(ctx_hint), " \033[2m[ctx:%d%%]\033[0m", pct);
                }
            }

            const char *perm_icon = g_permission_mode == 0 ? "\033[32m●"
                                  : g_permission_mode == 1 ? "\033[33m◐"
                                  : "\033[31m○";
            const char *perm_label = g_permission_mode == 0 ? "auto"
                                   : g_permission_mode == 1 ? "plan"
                                   : "manual";
            const char *det_label = g_hide_details ? "\033[33mhidden" : "\033[36mvisible";

            char prompt_str[512];
            snprintf(prompt_str, sizeof(prompt_str),
                "\033[1;35mai\033[0m \033[2m│\033[0m %s %s\033[0m \033[2m│ details: %s\033[0m%s \033[1;35m▸\033[0m ",
                perm_icon, perm_label,
                det_label, ctx_hint);

            char *line = read_line_interactive(prompt_str);
            if (!line) {
                printf("\n");
                break;
            }

            char user_input[4096];
            strncpy(user_input, line, sizeof(user_input) - 1);
            user_input[sizeof(user_input) - 1] = '\0';
            free(line);

            /* Display user message in styled box */
            if (user_input[0] && user_input[0] != ':') {
                add_turn_item(ITEM_USER_INPUT, "User", NULL, user_input, 0, 0, g_turn_count, 0);
                print_user_message(user_input);
            }

            /* Shift-Tab — returned as sentinel by read_line_interactive */
            if (user_input[0] == '\033' && user_input[1] == '[' && user_input[2] == 'Z') {
                g_permission_mode = (g_permission_mode + 1) % 3;
                if (g_permission_mode == 0)
                    setenv("INFER_AUTO_APPROVE", "1", 1);
                else
                    unsetenv("INFER_AUTO_APPROVE");
                const char *mode_names[] = { "auto", "plan", "manual" };
                printf("\033[2mpermission mode: %s\033[0m\n", mode_names[g_permission_mode]);
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
                        print_info_box("Context Compacted", "Conversation summarized — continuing with reduced context.");
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
                    g_turn_item_count = 0;
                    print_info_box("Conversation Cleared", "Starting fresh with no prior context.");
                    run_query_this_turn = 0;
                    continue;
                }
                if (strcmp(user_input, ":mode") == 0) {
                    print_mode_current();
                    run_query_this_turn = 0;
                    continue;
                }
                if (strncmp(user_input, ":mode ", 6) == 0) {
                    char preset[32] = "";
                    const char *src = user_input + 6;
                    while (*src == ' ') src++;
                    strncpy(preset, src, sizeof(preset) - 1);
                    preset[sizeof(preset) - 1] = '\0';
                    if (apply_mode_preset(preset)) {
                        char msg[256];
                        snprintf(msg, sizeof(msg),
                                 "Sampling preset '%s' active for the next turns of this session.\n"
                                 "  temperature=%.2f  top_p=%.2f  top_k=%d  min_p=%.1f\n"
                                 "  presence_penalty=%.2f  frequency_penalty=%.2f  reasoning_effort=%s",
                                 preset, temperature_val, top_p_val, top_k_val, min_p_val,
                                 presence_penalty_val, frequency_penalty_val, reasoning_effort_val);
                        print_info_box("Mode Switched", msg);
                    } else {
                        print_info_box("Unknown Mode",
                            "Presets: :mode xhigh · :mode normal · :mode low · :mode instruct");
                    }
                    run_query_this_turn = 0;
                    continue;
                }
                if (strcmp(user_input, ":jobs") == 0 || strcmp(user_input, ":tasks") == 0) {
                    print_jobs_and_tasks_status();
                    run_query_this_turn = 0;
                    continue;
                }
                if (strcmp(user_input, ":details") == 0 || strcmp(user_input, ":tools") == 0) {
                    g_hide_details = !g_hide_details;
                    print_info_box("Details Visibility Toggled",
                        g_hide_details ? "Tools and thinking output are now HIDDEN (Ctrl+O to toggle)."
                                       : "Tools and thinking output are now VISIBLE (Ctrl+O to toggle).");
                    if (!g_hide_details) print_jobs_and_tasks_status();
                    run_query_this_turn = 0;
                    continue;
                }
                if (strcmp(user_input, ":help") == 0) {
                    print_info_box("Interactive Commands",
                        "  :details       Toggle details (tools & thinking output on/off)\n"
                        "  :mode [preset] Live sampling preset: xhigh/normal/low/instruct (no arg = show)\n"
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
                    run_query_this_turn = 0;
                    continue;
                }
                if (strcmp(user_input, ":commit") == 0) {
                    git_commit("user-commit");
                    print_info_box("Git Committed", "Changes staged and committed to git.");
                    run_query_this_turn = 0;
                    continue;
                }
                if (strcmp(user_input, ":undo") == 0) {
                    system("git -C . log -1 --format=%h 2>/dev/null | xargs -I{} git -C . reset --soft HEAD~1 2>/dev/null");
                    print_info_box("Git Undo", "Last commit undone (changes kept staged).");
                    run_query_this_turn = 0;
                    continue;
                }
                if (strcmp(user_input, ":git-diff") == 0) {
                    char *diff = run_shell_command("git -C . diff --stat 2>/dev/null", NULL);
                    if (diff && *diff) {
                        printf("\n%s\033[1;36mGit Diff:\033[0m\n%s%s\n",
                               CL_CYAN, diff, CL_RESET);
                    } else {
                        printf("%sNo staged changes.\033[0m\n", CL_DIM);
                        free(diff);
                    }
                    run_query_this_turn = 0;
                    continue;
                }
                if (strcmp(user_input, ":git-status") == 0) {
                    char *st = run_shell_command("git -C . status -s 2>/dev/null", NULL);
                    if (st && *st) {
                        printf("\n%s\033[1;36mGit Status:\033[0m\n%s%s\n",
                               CL_CYAN, st, CL_RESET);
                    } else {
                        printf("%sWorking tree clean.\033[0m\n", CL_DIM);
                        free(st);
                    }
                    run_query_this_turn = 0;
                    continue;
                }
                if (strcmp(user_input, ":copy") == 0) {
                    if (g_last_response && g_last_response[0]) {
                        copy_to_clipboard(g_last_response);
                    } else {
                        printf("%sNo response to copy yet.\033[0m\n", CL_DIM);
                    }
                    run_query_this_turn = 0;
                    continue;
                }
                if (strcmp(user_input, ":notify") == 0) {
                    g_notifications_enabled ^= 1;
                    printf("%sOS notifications %s\033[0m\n",
                           CL_CYAN, g_notifications_enabled ? "enabled" : "disabled");
                    run_query_this_turn = 0;
                    continue;
                }
                if (strcmp(user_input, ":agents") == 0) {
                    /* Scan the project and hand the results to the AI for intelligent AGENTS.md generation. */
                    fprintf(stderr, "\033[2m[ai] Scanning project for agents.md generation...\033[0m\n");
                    char cmd[4096];
                    snprintf(cmd, sizeof(cmd),
                        "find . -maxdepth 2 -type f \\( -name '*.py' -o -name '*.js' -o -name '*.ts' "
                        "-o -name '*.rs' -o -name '*.c' -o -name '*.cpp' -o -name '*.h' -o -name '*.go' "
                        "-o -name '*.java' -o -name '*.rb' -o -name '*.sh' -o -name '*.md' -o -name '*.toml' "
                        "-o -name '*.yaml' -o -name '*.yml' -o -name '*.json' -o -name '*.txt' "
                        "-o -name 'Makefile' -o -name 'CMakeLists.txt' -o -name 'Cargo.toml' "
                        "-o -name 'go.mod' -o -name 'package.json' -o -name 'requirements.txt' "
                        "-o -name 'Dockerfile' -o -name 'docker-compose.yml' "
                        "-o -name '*.sql' -o -name '*.html' -o -name '*.css' \\) "
                        "! -path '*/node_modules/*' ! -path '*/.git/*' ! -path '*/__pycache__/*' "
                        "! -path '*/venv/*' ! -path '*/.venv/*' ! -path '*/.tox/*' "
                        "-printf '%%y %%s %%p\\n' 2>/dev/null | sort");
                    char *output = run_shell_command(cmd, NULL);
                    if (!output) {
                        fprintf(stderr, "\033[2m[ai] Failed to scan project for :agents\033[0m\n");
                    } else {
                        /* Build a prompt the AI can use to generate AGENTS.md.
                         * We pass the file scan into a user message so the model can analyze the project
                         * and produce a tailored AGENTS.md rather than writing a hardcoded template. */
                        char *scan_escaped = json_escape(output);
                        char agent_prompt[8192];
                        snprintf(agent_prompt, sizeof(agent_prompt),
                            "Run the command: write_file {\"path\":\"./AGENTS.md\", \"content\": ...} with the content "
                            "you generate below.\n\n"
                            "Here is the project file scan:\n```\n%s\n```\n\n"
                            "Please generate an AGENTS.md that includes: a concise project structure overview "
                            "(highlighting the main entry points and architecture), a clear description of how "
                            "to build and run the project, key rules the agent should follow based on the "
                            "codebase conventions, and a workflow section. Tailor the content to this specific project.",
                            scan_escaped);
                        free(scan_escaped);

                        char *prompt_escaped = json_escape(agent_prompt);
                        char *user_msg = malloc(strlen(prompt_escaped) + 128);
                        sprintf(user_msg, "{\"role\":\"user\",\"content\":\"%s\"}", prompt_escaped);
                        free(prompt_escaped);
                        messages_json = append_message(messages_json, user_msg);
                        free(user_msg);
                        add_turn_item(ITEM_USER_INPUT, "User", NULL, "Generate AGENTS.md from project scan", 0, 0, g_turn_count, 0);
                        fprintf(stderr, "\033[2m[ai] Sending project scan to AI for AGENTS.md generation...\033[0m\n");
                    }
                    free(output);
                    run_query_this_turn = 1;
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
                           g_permission_mode == 0 ? "\033[1;33mON\033[0m (Shift-Tab to disable)" : "off");
                    run_query_this_turn = 0;
                    continue;
                }
                if (strcmp(user_input, ":auto") == 0) {
                    g_permission_mode = (g_permission_mode + 1) % 3;
                    if (g_permission_mode == 0)
                        setenv("INFER_AUTO_APPROVE", "1", 1);
                    else
                        unsetenv("INFER_AUTO_APPROVE");
                    const char *mode_names[] = { "auto", "plan", "manual" };
                    printf("\033[2mpermission mode: %s\033[0m\n", mode_names[g_permission_mode]);
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
            int step_limit;
            const char *step_env = getenv("INFER_STEP_LIMIT");
            int step_env_val = step_env ? atoi(step_env) : 0;
            if (g_continue_until_done) step_limit = 999999;
            else if (!isatty(STDIN_FILENO)) step_limit = (step_env_val > 0) ? step_env_val : 60; /* non-tty always finite; no infinite autonomy */
            else step_limit = (step_env_val > 0) ? step_env_val : 30;
            int think_count = 0;
            for (int _si = 0; _si < SI_MAX_TRACKED; _si++) g_si_failed[_si].used = 0; /* reset failure-learning map per task */
            g_state_log[0] = '\0';      /* reset situational state log per task */
            g_state_status = 0;
            g_plan_approved = 0;
            g_esc_requested = 0;
            g_agent_loop_active = 1;
            char *last_tool_name = NULL;
            char *last_tool_args = NULL;
            int same_tool_count = 0;
            int length_nudge_count = 0;   /* consecutive token-limit nudges; force completion if model never progresses */
            int force_complete_after = 5; /* INFER_NUDGE_CAP: bail out and force task_complete after this many stalled nudges */
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
                char opt_fields[512] = "";
                int opt_len = 0;
                if (temperature_val >= 0.0f)
                    opt_len += snprintf(opt_fields + opt_len, (int)sizeof(opt_fields) - opt_len,
                                        ",\"temperature\":%.2f", temperature_val);
                if (top_p_val >= 0.0f)
                    opt_len += snprintf(opt_fields + opt_len, (int)sizeof(opt_fields) - opt_len,
                                        ",\"top_p\":%.2f", top_p_val);
                if (top_k_val > 0)
                    opt_len += snprintf(opt_fields + opt_len, (int)sizeof(opt_fields) - opt_len,
                                        ",\"top_k\":%d", top_k_val);
                if (min_p_val >= 0.0f)
                    opt_len += snprintf(opt_fields + opt_len, (int)sizeof(opt_fields) - opt_len,
                                        ",\"min_p\":%.2f", min_p_val);
                if (max_tokens_val > 0)
                    opt_len += snprintf(opt_fields + opt_len, (int)sizeof(opt_fields) - opt_len,
                                        ",\"max_tokens\":%d", max_tokens_val);
                /* frequency_penalty: penalises tokens that already appeared → breaks
                   repetitive reasoning loops in thinking models (0.0 = disabled). */
                if (frequency_penalty_val > 0.0f)
                    opt_len += snprintf(opt_fields + opt_len, (int)sizeof(opt_fields) - opt_len,
                                        ",\"frequency_penalty\":%.2f", frequency_penalty_val);
                if (presence_penalty_val > 0.0f)
                    opt_len += snprintf(opt_fields + opt_len, (int)sizeof(opt_fields) - opt_len,
                                        ",\"presence_penalty\":%.2f", presence_penalty_val);
                if (reasoning_effort_val && *reasoning_effort_val) {
                    char *esc_effort = json_escape(reasoning_effort_val);
                    opt_len += snprintf(opt_fields + opt_len, (int)sizeof(opt_fields) - opt_len,
                                        ",\"reasoning_effort\":\"%s\"", esc_effort);
                    free(esc_effort);
                }
                if (preserve_thinking_val) {
                    opt_len += snprintf(opt_fields + opt_len, (int)sizeof(opt_fields) - opt_len,
                                        ",\"preserve_thinking\":true");
                }

                char *esc_model = json_escape(model);
                char *payload = NULL;
                size_t plen = strlen(esc_model) + strlen(messages_json) + (tools_json ? strlen(tools_json) : 0) + 512;
                payload = malloc(plen);
                if (tools_json && strlen(tools_json) > 10) {
                    snprintf(payload, plen, "{\"model\":\"%s\",\"stream\":true,\"stream_options\":{\"include_usage\":true}%s,\"messages\":%s,\"tools\":%s,\"tool_choice\":\"%s\"}",
                             esc_model, opt_fields, messages_json, tools_json, tool_choice_val);
                } else {
                    snprintf(payload, plen, "{\"model\":\"%s\",\"stream\":true,\"stream_options\":{\"include_usage\":true}%s,\"messages\":%s}",
                             esc_model, opt_fields, messages_json);
                }
                free(esc_model);

                if (debug_mode) {
                    fprintf(stderr, "[debug] Loop %d payload: %s\n", loop_count, payload);
                }

                struct response chunk = {0};
                struct stream_context s_ctx;
                init_stream_context(&s_ctx, &chunk, quiet_mode);

                // Show thinking indicator here!
                if (!quiet_mode && !g_hide_details) {
                    fprintf(stderr, "\033[2m[thinking]...\033[0m");
                    fflush(stderr);
                }

                curl_easy_setopt(c, CURLOPT_POSTFIELDS, payload);
                curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, stream_write_cb);
                curl_easy_setopt(c, CURLOPT_WRITEDATA, (void *)&s_ctx);

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

                            free_stream_context(&s_ctx);
                            init_stream_context(&s_ctx, &chunk, quiet_mode);

                            if (!quiet_mode && !g_hide_details) {
                                fprintf(stderr, "\033[2m[thinking]...\033[0m");
                                fflush(stderr);
                            }

                            curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, stream_write_cb);
                            curl_easy_setopt(c, CURLOPT_WRITEDATA, (void *)&s_ctx);
                            
                            free(payload);
                            size_t new_plen = strlen(model) + strlen(messages_json) + (tools_json ? strlen(tools_json) : 0) + 512;
                            payload = malloc(new_plen);
                            if (tools_json && strlen(tools_json) > 10) {
                                snprintf(payload, new_plen, "{\"model\":\"%s\",\"stream\":true,\"stream_options\":{\"include_usage\":true}%s,\"messages\":%s,\"tools\":%s,\"tool_choice\":\"%s\"}",
                                         model, opt_fields, messages_json, tools_json, tool_choice_val);
                            } else {
                                snprintf(payload, new_plen, "{\"model\":\"%s\",\"stream\":true,\"stream_options\":{\"include_usage\":true}%s,\"messages\":%s}",
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

                if (!quiet_mode) {
                    if (s_ctx.printed_thinking_header && !s_ctx.printed_thinking_footer) {
                        fprintf(stderr, "\033[0m\n");
                        fflush(stderr);
                    } else if (!s_ctx.printed_thinking_header) {
                        // Clear the "[thinking]..." message
                        fprintf(stderr, "\r\033[2K");
                        fflush(stderr);
                    }
                }

                if (res == CURLE_OK) {
                    if (s_ctx.line_len > 0) {
                        process_sse_line(&s_ctx, s_ctx.line_buf, s_ctx.line_len);
                    }
                    reconstruct_final_json(&s_ctx);
                    if (s_ctx.accumulated_reasoning && *s_ctx.accumulated_reasoning) {
                        add_turn_item(ITEM_THINKING, "thinking", NULL, s_ctx.accumulated_reasoning, 0, 0, 0, 0);
                    }
                }

                // Restore default write function
                curl_easy_setopt(c, CURLOPT_WRITEFUNCTION, write_cb);
                curl_easy_setopt(c, CURLOPT_WRITEDATA, NULL);

                free_stream_context(&s_ctx);

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
                            if (i + 1 < r && tok[i + 1].type == JSMN_ARRAY) {
                                tool_calls_tok = i + 1;
                            }
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
                        k = json_skip_token(tok, r, k + 2);
                    }
                    /* Accumulate session-wide token count */
                    g_session_tokens = total_tokens;
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
                            k = json_skip_token(tok, r, k + 2);
                        }
                    }
                    if (err_msg) {
                        /* Recoverable: model returned empty output — nudge it to call task_complete */
                        if (strstr(err_msg, "model output must contain") != NULL ||
                            strstr(err_msg, "output text or tool calls") != NULL) {
                            fprintf(stderr, "\033[1;33m[ai] Warning: model returned empty output — nudging to call task_complete.\033[0m\n");
                            messages_json = append_message(messages_json,
                                "{\"role\":\"user\",\"content\":\"Your last response was empty. "
                                "Please call task_complete now with a summary of what you just did.\"}");
                            has_more = 1;
                            free(err_msg);
                            free(payload);
                            free(chunk.data);
                            break;
                        }
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
                            j = json_skip_token(tok, r, j + 2);
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
                                k = json_skip_token(tok, r, k + 2);
                              }
                          }

                          if (call_id_tok != -1 && name_tok != -1 && args_tok != -1) {
                              double tool_t0 = get_time_sec_mono();
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

                              if (last_tool_name && last_tool_args &&
                                  strcmp(last_tool_name, unescaped_name) == 0 &&
                                  strcmp(last_tool_args, unescaped_args) == 0) {
                                  same_tool_count++;
                              } else {
                                  same_tool_count = 0;
                                  if (last_tool_name) free(last_tool_name);
                                  if (last_tool_args) free(last_tool_args);
                                  last_tool_name = strdup(unescaped_name);
                                  last_tool_args = strdup(unescaped_args);
                              }

                               if (same_tool_count >= 2) {
                                                                  /* The identical-(tool,args) repeat is a hard runaway signal: a healthy
                                                                     model breaks out on the first "stuck in a loop" message. If it ignores
                                                                     the warning and keeps repeating, we must TERMINATE, not just warn —
                                                                     otherwise a degenerate model loops forever under -c (step_limit=999999)
                                                                     where the step/nudge brakes below are ineffective. */
                                                                  const char *tool_loop_cap_env = getenv("INFER_TOOL_LOOP_CAP");
                                                                  int tool_loop_cap = (tool_loop_cap_env && atoi(tool_loop_cap_env) > 0)
                                                                                       ? atoi(tool_loop_cap_env) : 6;
                                                                  if (same_tool_count >= tool_loop_cap) {
                                                                      fprintf(stderr,
                                                                          "\033[1;31m[ai] Infinite tool loop: identical tool+args called %d "
                                                                          "times in a row. Aborting agent loop and forcing task_complete.\033[0m\n",
                                                                          same_tool_count);
                                                                      /* Hard stop — do NOT hand the degenerate model another round-trip, or
                                                                         it will keep looping under -c where step_limit is unlimited. */
                                                                      char stall_msg[256];
                                                                      snprintf(stall_msg, sizeof(stall_msg),
                                                                          "{\"role\":\"user\",\"content\":\"[HARD STOP] You called the exact same tool "
                                                                          "with the same arguments %d times in a row. You are in an infinite tool loop. "
                                                                          "Aborting. Call task_complete NOW with your best summary. Do NOT call any "
                                                                          "more tools.\\\"}", same_tool_count);
                                                                      messages_json = append_message(messages_json, stall_msg);
                                                                      tool_output = strdup("{\"ok\":true}");
                                                                      has_more = 0;
                                                                      task_done = 1;
                                                                  } else {
                                                                      tool_output = strdup("Error: You are stuck in a loop calling the exact same tool with the exact same arguments. Stop and try a different approach, or call task_complete.");
                                                                  }
                                                              } else if (strcmp(unescaped_name, "think") == 0) {
                                   think_count++;
                                   if (think_count > 12) {
                                       tool_output = strdup("Error: You have already called the 'think' tool once to plan. Do not call it again. You must call 'task_complete' to report your final answer/summary to the user, or call other action tools if there is more work to do. Calling 'think' repeatedly causes infinite loops.");
                                   } else {
                                       jsmn_parser arg_parser;
                                       jsmntok_t arg_toks[256];
                                       jsmn_init(&arg_parser);
                                       int arg_r = jsmn_parse(&arg_parser, unescaped_args, strlen(unescaped_args), arg_toks, 256);
                                       char *reasoning = NULL;
                                       if (arg_r > 0 && arg_toks[0].type == JSMN_STRING) {
                                           reasoning = unescape_json_string(unescaped_args + arg_toks[0].start, arg_toks[0].end - arg_toks[0].start);
                                       } else if (arg_r > 0 && unescaped_args[0] != '{' && unescaped_args[0] != '[') {
                                           reasoning = strdup(unescaped_args);
                                       } else {
                                           for (int a = 1; a < arg_r; a++) {
                                               if (arg_toks[a].type == JSMN_STRING) {
                                                   int klen = arg_toks[a].end - arg_toks[a].start;
                                                   const char *kstr = unescaped_args + arg_toks[a].start;
                                                   if ((klen == 9 && strncmp(kstr, "reasoning", 9) == 0) ||
                                                       (klen == 7 && strncmp(kstr, "thought", 7) == 0) ||
                                                       (klen == 8 && strncmp(kstr, "thoughts", 8) == 0) ||
                                                       (klen == 4 && strncmp(kstr, "plan", 4) == 0) ||
                                                       (klen == 6 && strncmp(kstr, "reason", 6) == 0)) {
                                                       if (a + 1 < arg_r) {
                                                           reasoning = unescape_json_string(unescaped_args + arg_toks[a+1].start,
                                                                                            arg_toks[a+1].end - arg_toks[a+1].start);
                                                           break;
                                                       }
                                                   }
                                               }
                                           }
                                       }
                                       if (reasoning) {
                                   size_t rlen = strlen(reasoning);
                                   g_think_since_action++;
                                   if (rlen > (size_t)g_think_max_chars) reasoning[g_think_max_chars] = '\0';
                                   add_turn_item(ITEM_THINKING, "thinking", NULL, reasoning, 0, 0, 0, 0);
                                   if (!quiet_mode) {
                                       print_think_box(reasoning);
                                       fflush(stderr);
                                   }
                                   char think_out[900];
                                   int act_limit = 3;
                                   const char *tal = getenv("INFER_THINK_ACTION_LIMIT");
                                   if (tal && atoi(tal) > 0) act_limit = atoi(tal);
                                   if (rlen > (size_t)g_think_max_chars) {
                                       snprintf(think_out, sizeof(think_out),
                                           "Error: Your 'think' reasoning was very long (%zu chars) so it was cut to %d chars to save "
                                           "budget. Long think blocks are the #1 reason small local models stall and never produce output. "
                                           "Keep 'think' to a MAXIMUM of 3 short sentences. Do NOT re-type your plan, code, or analysis inside "
                                           "thinking. Take your next concrete action with an ACTION tool (write_file to write your code, or "
                                           "execute_command to build/run) instead of thinking more.", (unsigned long)rlen, g_think_max_chars);
                                       tool_output = strdup(think_out);
                                   } else if (g_think_since_action >= act_limit) {
                                       snprintf(think_out, sizeof(think_out),
                                           "Error: You have called 'think' %d times in a row without taking any concrete action "
                                           "(writing a file or running a command). You are not making progress. "
                                           "STOP thinking. Take ONE concrete action NOW with write_file (write your code) or execute_command "
                                           "(build/run). You may call 'think' again only AFTER you have acted.", g_think_since_action);
                                       tool_output = strdup(think_out);
                                   } else {
                                       tool_output = strdup("{\"ok\":true}");
                                   }
                                   free(reasoning);
}
                                   }
                               } else if (strcmp(unescaped_name, "task_complete") == 0) {
                                   jsmn_parser arg_parser;
                                   jsmntok_t arg_toks[2048];
                                   jsmn_init(&arg_parser);
                                   int arg_r = jsmn_parse(&arg_parser, unescaped_args, strlen(unescaped_args), arg_toks, 2048);
                                   char *summary = NULL;
                                   if (arg_r > 0 && arg_toks[0].type == JSMN_STRING) {
                                       summary = unescape_json_string(unescaped_args + arg_toks[0].start, arg_toks[0].end - arg_toks[0].start);
                                   } else if (arg_r > 0 && unescaped_args[0] != '{' && unescaped_args[0] != '[') {
                                       summary = strdup(unescaped_args);
                                   } else {
                                       for (int a = 1; a < arg_r; a++) {
                                           if (arg_toks[a].type == JSMN_STRING) {
                                               int klen = arg_toks[a].end - arg_toks[a].start;
                                               const char *kstr = unescaped_args + arg_toks[a].start;
                                               if ((klen == 7 && strncmp(kstr, "summary", 7) == 0) ||
                                                   (klen == 4 && strncmp(kstr, "text", 4) == 0) ||
                                                   (klen == 8 && strncmp(kstr, "response", 8) == 0) ||
                                                   (klen == 6 && strncmp(kstr, "result", 6) == 0) ||
                                                   (klen == 7 && strncmp(kstr, "message", 7) == 0) ||
                                                   (klen == 12 && strncmp(kstr, "final_answer", 12) == 0) ||
                                                   (klen == 6 && strncmp(kstr, "answer", 6) == 0)) {
                                                   if (a + 1 < arg_r) {
                                                       summary = unescape_json_string(unescaped_args + arg_toks[a+1].start,
                                                                                      arg_toks[a+1].end - arg_toks[a+1].start);
                                                       break;
                                                   }
                                               }
                                           }
                                       }
                                   }
                                   if (summary) {
                                       log_job(current_prompt, pipe_writer, summary, interactive_mode);
                                       char *escaped_summary = shell_escape(summary);
                                       /* Render markdown via Python helper and print clean response */
                                       char *rendered = render_markdown(summary);
                                       double elapsed_sec = get_time_sec_mono() - tool_t0;
                                       double tps = (elapsed_sec > 0.05 && completion_tokens > 0)
                                                     ? completion_tokens / elapsed_sec : 0.0;
                                       fflush(stderr);
                                       add_turn_item(ITEM_ASSISTANT_RESPONSE, model[0] ? model : "ai", NULL,
                                                     summary, elapsed_sec, tps, g_turn_count, total_tool_count);
                                       notify_completion(summary);
                                       if (rendered && *rendered) {
                                           print_response_box(model[0] ? model : "ai", rendered,
                                                              g_turn_count, total_tool_count,
                                                              elapsed_sec, tps, 0);
                                           free(rendered);
                                       } else {
                                           print_response_box(model[0] ? model : "ai", summary,
                                                              g_turn_count, total_tool_count,
                                                              elapsed_sec, tps, 0);
                                       }
                                       free(escaped_summary);
                                       free(summary);
                                   }
                                   tool_output = strdup("{\"ok\":true}");
                                   has_more = 0;
                                   task_done = 1;
                               
                                } else if (strcmp(unescaped_name, "present_plan") == 0) {
                                    char *plan_txt = json_get_string(unescaped_args, "plan");
                                    if (!plan_txt) plan_txt = json_get_string(unescaped_args, "summary");
                                    if (!plan_txt) plan_txt = strdup(unescaped_args);
                                    int approved = 0;
                                    if (g_permission_mode == 1) {
                                        const char *pauto = getenv("INFER_PLAN_AUTOAPPROVE");
                                        if (pauto && *pauto) {
                                            approved = 1; /* opt-in auto-approve (trusted/harnessed/bridge runs only) */
                                        } else {
                                            approved = prompt_user_ok("APPROVE PLAN", plan_txt);
                                        if (approved == -1) {
                                            /* No interactive terminal (piped/bridge run). The user cannot
                                               approve here; present the plan and finish so the plan is the
                                               deliverable reported back to the caller. */
                                            g_plan_approved = 0;
                                            char *esc_plan = json_escape(plan_txt ? plan_txt : "");
                                            char *hint = malloc(strlen(esc_plan) + 512);
                                            snprintf(hint, strlen(esc_plan) + 512,
                                                "[PLAN MODE] This is a non-interactive run, so your plan cannot be approved on this terminal. "
                                                "STOP making tool calls now. Report your complete plan (investigation, proposed changes, commands, "
                                                "and rationale) as your final answer via task_complete. Do not attempt any state-changing tool again.\n\n"
                                                "YOUR PLAN TO REPORT TO THE USER:\n%s", esc_plan);
                                            tool_output = hint;
                                            free(esc_plan);
                                            if (plan_txt) free(plan_txt);
                                            plan_txt = NULL;
                                            /* Hand control back so the loop produces a final answer. */
                                            goto present_plan_done;
                                        }
                                        }
                                        /* plan mode: applicant approval unlocks autonomy for this task */
                                        g_plan_approved = approved ? 1 : 0;
                                        g_plan_remaining = approved ? g_plan_budget : 0;
                                        if (g_plan_budget_note) { free(g_plan_budget_note); g_plan_budget_note = NULL; }
                                    } else {
                                        /* auto/manual: present_plan is informational only.
                                           It must NOT set g_plan_approved in manual mode,
                                           otherwise manual gating is bypassed. */
                                        g_plan_approved = 0;
                                        approved = (g_permission_mode == 0); /* auto: treat as fine; manual: still require per-action approval */
                                    }
                                    if (approved) {
                                        tool_output = strdup("PLAN APPROVED. You may now proceed to make the proposed changes. Work autonomously until you have another question, then present_plan again.");
                                    } else {
                                        tool_output = strdup("PLAN NOT APPROVED. Revise your plan based on the user's feedback and call present_plan again. Do not make any changes until the plan is approved.");
                                    }
                                    present_plan_done:
                                    if (plan_txt) free(plan_txt);
                                } else if (strcmp(unescaped_name, "remote_exec") == 0) {
                                   /* ── Remote Server Control Tool ────────────────────────── */
                                   /* Connect, discover, execute commands, monitor resources, submit jobs */
                                   
                                   /* Parse arguments */
                                   jsmn_parser arg_parser;
                                   jsmntok_t arg_toks[512];
                                   jsmn_init(&arg_parser);
                                   int arg_r = jsmn_parse(&arg_parser, unescaped_args, strlen(unescaped_args), arg_toks, 512);
                                   
                                   char *action_val = NULL;
                                   char *host_val = NULL;
                                   char *port_val = NULL;
                                   char *user_val = NULL;
                                   char *pass_val = NULL;
                                   char *cmd_val = NULL;
                                   char *timeout_val = NULL;
                                   
                                   for (int a = 1; a < arg_r; a++) {
                                       if (arg_toks[a].type == JSMN_STRING) {
                                           int klen = arg_toks[a].end - arg_toks[a].start;
                                           
                                           if (klen == 6 && strncmp(unescaped_args + arg_toks[a].start, "action", 6) == 0) {
                                               action_val = unescape_json_string(unescaped_args + arg_toks[a+1].start, arg_toks[a+1].end - arg_toks[a+1].start);
                                           } else if (klen == 4 && strncmp(unescaped_args + arg_toks[a].start, "host", 4) == 0) {
                                               host_val = unescape_json_string(unescaped_args + arg_toks[a+1].start, arg_toks[a+1].end - arg_toks[a+1].start);
                                           } else if (klen == 4 && strncmp(unescaped_args + arg_toks[a].start, "port", 4) == 0) {
                                               port_val = unescape_json_string(unescaped_args + arg_toks[a+1].start, arg_toks[a+1].end - arg_toks[a+1].start);
                                           } else if (klen == 5 && strncmp(unescaped_args + arg_toks[a].start, "user", 5) == 0) {
                                               user_val = unescape_json_string(unescaped_args + arg_toks[a+1].start, arg_toks[a+1].end - arg_toks[a+1].start);
                                           } else if ((klen == 5 && strncmp(unescaped_args + arg_toks[a].start, "passw", 5) == 0) ||
                                                      (klen == 4 && strncmp(unescaped_args + arg_toks[a].start, "pass", 4) == 0)) {
                                               pass_val = unescape_json_string(unescaped_args + arg_toks[a+1].start, arg_toks[a+1].end - arg_toks[a+1].start);
                                           } else if (klen == 7 && strncmp(unescaped_args + arg_toks[a].start, "command", 7) == 0) {
                                               cmd_val = unescape_json_string(unescaped_args + arg_toks[a+1].start, arg_toks[a+1].end - arg_toks[a+1].start);
                                           } else if (klen == 7 && strncmp(unescaped_args + arg_toks[a].start, "timeout", 7) == 0) {
                                               timeout_val = unescape_json_string(unescaped_args + arg_toks[a+1].start, arg_toks[a+1].end - arg_toks[a+1].start);
                                           }
                                       }
                                   }
                                   
                                   char *action = action_val ? action_val : "connect";
                                   int timeout_sec = timeout_val ? atoi(timeout_val) : 120;
                                   if (timeout_sec <= 0) timeout_sec = 120;
                                   
                                   /* Handle connection lifecycle */
                                   if (strcmp(action, "connect") == 0) {
                                       if (!host_val || !user_val) {
                                           tool_output = strdup("Error: 'host' and 'user' arguments required for connect");
                                       } else {
                                           int port = REMOTE_DEFAULT_PORT;
                                           if (port_val) port = atoi(port_val);
                                           
                                           if (g_remote_server) {
                                               remote_disconnect(g_remote_server);
                                           }
                                           
                                           g_remote_server = remote_connect(host_val, port, user_val, 
                                                                               pass_val ? pass_val : "", 
                                                                               "Remote Cluster");
                                           if (g_remote_server) {
                                               tool_output = strdup("Connected to remote server.");
                                           } else {
                                               tool_output = strdup("Error: failed to connect to remote server");
                                           }
                                       }
                                   } else if (strcmp(action, "disconnect") == 0) {
                                       if (g_remote_server) {
                                           remote_disconnect(g_remote_server);
                                           g_remote_server = NULL;
                                       }
                                       tool_output = strdup("Disconnected from remote server.");
                                   } else if (strcmp(action, "discover") == 0) {
                                       if (!g_remote_server) {
                                           tool_output = strdup("Error: not connected to remote server. Call connect first.");
                                       } else if (!remote_discover(g_remote_server)) {
                                           tool_output = strdup("Error: discovery failed");
                                       } else {
                                           char *status = remote_get_status(g_remote_server);
                                           tool_output = status ? status : strdup("Discovery complete.");
                                           g_remote_discovered = 1;
                                       }
                                   } else if (strcmp(action, "status") == 0) {
                                       if (!g_remote_server) {
                                           tool_output = strdup("Error: not connected to remote server");
                                       } else {
                                           char *status = remote_get_status(g_remote_server);
                                           tool_output = status ? status : strdup("Error: failed to get status");
                                       }
                                   } else if (strcmp(action, "mount") == 0) {
                                       if (!g_remote_server) {
                                           tool_output = strdup("Error: not connected to remote server");
                                       } else if (!cmd_val) {
                                           tool_output = strdup("Error: 'command' argument required (format: 'mount <server_path> <mount_point>')");
                                       } else {
                                           char mount_cmd[1024];
                                           snprintf(mount_cmd, sizeof(mount_cmd), "mount %s", cmd_val);
                                           char *output = remote_exec(g_remote_server, mount_cmd, 60);
                                           tool_output = output ? output : strdup("Error: mount failed");
                                       }
                                   } else if (strcmp(action, "exec") == 0) {
                                       if (!g_remote_server) {
                                           tool_output = strdup("Error: not connected to remote server");
                                       } else if (!cmd_val) {
                                           tool_output = strdup("Error: 'command' argument required for exec");
                                       } else {
                                           char *output = remote_exec(g_remote_server, cmd_val, timeout_sec);
                                           tool_output = output ? output : strdup("Error: command execution failed");
                                       }
                                   } else if (strcmp(action, "jobs") == 0) {
                                       if (!g_remote_server) {
                                           tool_output = strdup("Error: not connected to remote server");
                                       } else {
                                           char *output = remote_list_jobs(g_remote_server, NULL, 20);
                                           tool_output = output ? output : strdup("No jobs found");
                                       }
                                   } else if (strcmp(action, "submit") == 0) {
                                       if (!g_remote_server) {
                                           tool_output = strdup("Error: not connected to remote server");
                                       } else {
                                           /* Parse submit arguments */
                                           char *queue_val = NULL;
                                           char *walltime_val = NULL;
                                           char *nodes_val = NULL;
                                           char *cpus_val = NULL;
                                           char *mem_val = NULL;
                                           
                                           for (int a = 1; a < arg_r; a++) {
                                               if (arg_toks[a].type == JSMN_STRING) {
                                                   int klen = arg_toks[a].end - arg_toks[a].start;
                                                   if (klen == 6 && strncmp(unescaped_args + arg_toks[a].start, "queue", 6) == 0) {
                                                       queue_val = unescape_json_string(unescaped_args + arg_toks[a+1].start, arg_toks[a+1].end - arg_toks[a+1].start);
                                                   } else if (klen == 8 && strncmp(unescaped_args + arg_toks[a].start, "walltime", 8) == 0) {
                                                       walltime_val = unescape_json_string(unescaped_args + arg_toks[a+1].start, arg_toks[a+1].end - arg_toks[a+1].start);
                                                   } else if (klen == 5 && strncmp(unescaped_args + arg_toks[a].start, "nodes", 5) == 0) {
                                                       nodes_val = unescape_json_string(unescaped_args + arg_toks[a+1].start, arg_toks[a+1].end - arg_toks[a+1].start);
                                                   } else if (klen == 5 && strncmp(unescaped_args + arg_toks[a].start, "cpus", 5) == 0) {
                                                       cpus_val = unescape_json_string(unescaped_args + arg_toks[a+1].start, arg_toks[a+1].end - arg_toks[a+1].start);
                                                   } else if (klen == 4 && strncmp(unescaped_args + arg_toks[a].start, "mem ", 4) == 0) {
                                                       mem_val = unescape_json_string(unescaped_args + arg_toks[a+1].start, arg_toks[a+1].end - arg_toks[a+1].start);
                                                   }
                                               }
                                           }
                                           
                                           int walltime = walltime_val ? atoi(walltime_val) : 60;
                                           int nodes = nodes_val ? atoi(nodes_val) : 1;
                                           int cpus = cpus_val ? atoi(cpus_val) : 1;
                                           int mem = mem_val ? atoi(mem_val) : 4;
                                           
                                           /* Write script to temp file on remote */
                                           char *write_script = malloc(2048);
                                           snprintf(write_script, 2048, "cat > /tmp/ai_buddy_job.sh << 'EOF'\n%s\nEOF", cmd_val);
                                           char *write_out = remote_exec(g_remote_server, write_script, 30);
                                           
                                           /* Submit job */
                                           char *submit_cmd = malloc(2048);
                                           snprintf(submit_cmd, 2048, "sbatch --job-name='ai-buddy' --time=%d:00:00 --nodes=%d --ntasks=%d --mem=%dG /tmp/ai_buddy_job.sh", walltime, nodes, cpus, mem);
                                           
                                           char *submit_out = remote_exec(g_remote_server, submit_cmd, 30);
                                           
                                           char *result = malloc(2048);
                                           snprintf(result, 2048, "%s\n%s", write_out, submit_out);
                                           
                                           tool_output = result;
                                           free(write_script);
                                           free(write_out);
                                           free(submit_cmd);
                                           free(submit_out);
                                           free(queue_val);
                                           free(walltime_val);
                                           free(nodes_val);
                                           free(cpus_val);
                                           free(mem_val);
                                       }
                                   } else {
                                       tool_output = strdup("Error: unknown action. Valid actions: connect, disconnect, discover, status, exec, mount, jobs, submit");
                                   }
                                   
                                   /* Cleanup */
                                   free(action_val);
                                   free(host_val);
                                   free(port_val);
                                   free(user_val);
                                   free(pass_val);
                                   free(cmd_val);
                                   free(timeout_val);
                               } else if (strcmp(unescaped_name, "execute_command") == 0) {
                                   jsmn_parser arg_parser;
                                   jsmntok_t arg_toks[512];
                                   jsmn_init(&arg_parser);
                                   int arg_r = jsmn_parse(&arg_parser, unescaped_args, strlen(unescaped_args), arg_toks, 512);
                                   char *cmd_val = NULL;
                                   int cmd_timeout = 120; // Default timeout
                                   if (arg_r > 0 && arg_toks[0].type == JSMN_STRING) {
                                       cmd_val = unescape_json_string(unescaped_args + arg_toks[0].start, arg_toks[0].end - arg_toks[0].start);
                                   } else if (arg_r > 0 && unescaped_args[0] != '{' && unescaped_args[0] != '[') {
                                       cmd_val = strdup(unescaped_args);
                                   } else {
                                       for (int a = 1; a < arg_r; a++) {
                                           if (arg_toks[a].type == JSMN_STRING) {
                                               int klen = arg_toks[a].end - arg_toks[a].start;
                                               const char *kstr = unescaped_args + arg_toks[a].start;
                                               if ((klen == 7 && strncmp(kstr, "command", 7) == 0) ||
                                                   (klen == 3 && strncmp(kstr, "cmd", 3) == 0) ||
                                                   (klen == 12 && strncmp(kstr, "command_line", 12) == 0) ||
                                                   (klen == 11 && strncmp(kstr, "CommandLine", 11) == 0) ||
                                                   (klen == 4 && strncmp(kstr, "args", 4) == 0) ||
                                                   (klen == 6 && strncmp(kstr, "script", 6) == 0) ||
                                                   (klen == 4 && strncmp(kstr, "code", 4) == 0) ||
                                                   (klen == 1 && strncmp(kstr, "c", 1) == 0)) {
                                                   if (a + 1 < arg_r) {
                                                       if (arg_toks[a + 1].type == JSMN_STRING) {
                                                           cmd_val = unescape_json_string(unescaped_args + arg_toks[a + 1].start, arg_toks[a + 1].end - arg_toks[a + 1].start);
                                                       } else if (arg_toks[a + 1].type == JSMN_ARRAY) {
                                                           int elem_count = arg_toks[a + 1].size;
                                                           int cur = a + 2;
                                                           size_t buf_cap = 512;
                                                           size_t buf_len = 0;
                                                           char *arr_buf = malloc(buf_cap);
                                                           if (arr_buf) {
                                                               arr_buf[0] = ' ';
                                                               for (int e = 0; e < elem_count && cur < arg_r; e++) {
                                                                   if (arg_toks[cur].type == JSMN_STRING || arg_toks[cur].type == JSMN_PRIMITIVE) {
                                                                       char *item = unescape_json_string(unescaped_args + arg_toks[cur].start, arg_toks[cur].end - arg_toks[cur].start);
                                                                       if (item) {
                                                                           size_t ilen = strlen(item);
                                                                           if (buf_len + ilen + 2 > buf_cap) {
                                                                               buf_cap = (buf_len + ilen + 2) * 2;
                                                                               arr_buf = realloc(arr_buf, buf_cap);
                                                                           }
                                                                           if (buf_len > 0) {
                                                                               strcat(arr_buf, " ");
                                                                               buf_len++;
                                                                           }
                                                                           strcat(arr_buf, item);
                                                                           buf_len += ilen;
                                                                           free(item);
                                                                       }
                                                                   }
                                                                   cur++;
                                                               }
                                                               cmd_val = arr_buf;
                                                           }
                                                       }
                                                   }
                                               } else if ((klen == 7 && strncmp(kstr, "timeout", 7) == 0) ||
                                                          (klen == 1 && strncmp(kstr, "t", 1) == 0)) {
                                                   if (a + 1 < arg_r) {
                                                       if (arg_toks[a + 1].type == JSMN_PRIMITIVE || arg_toks[a + 1].type == JSMN_STRING) {
                                                           char *timeout_str = unescape_json_string(unescaped_args + arg_toks[a + 1].start, arg_toks[a + 1].end - arg_toks[a + 1].start);
                                                           if (timeout_str) {
                                                               cmd_timeout = atoi(timeout_str);
                                                               free(timeout_str);
                                                           }
                                                       }
                                                   }
                                               }
                                           }
                                       }
                                   }


                                  if (cmd_val) {
                                      if (is_command_denied(cmd_val)) {
                                          fprintf(stderr, "[ai] Security block: command matches INFER_COMMAND_DENYLIST filter.\n");
                                          tool_output = strdup("Error: Command execution blocked by security denylist filter (INFER_COMMAND_DENYLIST).");
                                      } else if (g_dry_run) {
                                          fprintf(stderr, "[ai] [Dry-run] Would execute: %s\n", cmd_val);
                                          size_t dlen = strlen(cmd_val) + 128;
                                          tool_output = malloc(dlen);
                                          snprintf(tool_output, dlen, "[Command Dry-Run Success]\nWould execute: %s", cmd_val);
                                      } else if (g_permission_mode == 1 && !g_plan_approved) {
                                          tool_output = strdup("Error: [PLAN MODE] You must call present_plan to present your plan and receive approval before executing commands or changing anything. Nothing was changed.");
                                      } else {
                                          int approved = (g_permission_mode == 0 || g_plan_approved); /* auto or approved-plan auto-run; manual prompts below */
                                          if (!approved) {
                                              FILE *tty = fopen("/dev/tty", "r+");
                                              if (tty) {
                                                   fprintf(tty, "\n\033[1;33m  ▶ execute_command\033[0m\n");
                                                   fprintf(tty, "  \033[2m%s\033[0m\n", cmd_val);
                                                   fprintf(tty, "  \033[32my\033[0m/\033[31mn\033[0m/\033[36mb\033[0m  %s  Confirm? (b=background)%s\n\n",
                                                           CL_DIM, CL_RESET);
                                                   fflush(tty);
                                                   
                                                   int fd = fileno(tty);
                                                   struct termios orig_tty_termios;
                                                   int has_termios = (tcgetattr(fd, &orig_tty_termios) >= 0);
                                                   
                                                   if (has_termios) {
                                                       struct termios raw = orig_tty_termios;
                                                       raw.c_lflag &= ~(ECHO | ICANON);
                                                       raw.c_cc[VMIN] = 1;
                                                       raw.c_cc[VTIME] = 0;
                                                       tcsetattr(fd, TCSANOW, &raw);
                                                   }
                                                   
                                                   char ch = 0;
                                                   while (1) {
                                                       if (read(fd, &ch, 1) != 1) {
                                                           break;
                                                       }
                                                       
                                                       if (ch == 27) { // ESC
                                                           if (has_termios) {
                                                               struct termios timeout_raw = orig_tty_termios;
                                                               timeout_raw.c_lflag &= ~(ECHO | ICANON);
                                                               timeout_raw.c_cc[VMIN] = 0;
                                                               timeout_raw.c_cc[VTIME] = 1; // 100ms
                                                               tcsetattr(fd, TCSANOW, &timeout_raw);
                                                           }
                                                           
                                                           char seq[2] = {0, 0};
                                                           int n1 = read(fd, &seq[0], 1);
                                                           int n2 = 0;
                                                           if (n1 == 1) {
                                                               if (seq[0] == '[') {
                                                                   n2 = read(fd, &seq[1], 1);
                                                               }
                                                           }
                                                           
                                                           if (n1 == 1 && n2 == 1 && seq[0] == '[' && seq[1] == 'Z') {
                                                               g_permission_mode = (g_permission_mode + 1) % 3;
                                                               if (g_permission_mode == 0)
                                                                   setenv("INFER_AUTO_APPROVE", "1", 1);
                                                               else
                                                                   unsetenv("INFER_AUTO_APPROVE");
                                                               const char *mode_names[] = { "auto", "plan", "manual" };
                                                               fprintf(tty, "\n\033[2mpermission mode: %s\033[0m\n",
                                                                       mode_names[g_permission_mode]);
                                                               if (g_permission_mode == 0) {
                                                                   fprintf(tty, "\033[32mAuto-approved\033[0m\n");
                                                                   fflush(tty);
                                                                   approved = 1;
                                                                   break;
                                                               }
                                                               fprintf(tty, "\033[1;33m[ai] Execute command:\033[0m %s\n", cmd_val);
                                                               fprintf(tty, "\033[32my\033[0m/\033[31mn\033[0m/\033[36mb\033[0m  confirm  \033[2m(Shift+Tab cycle mode)\033[0m ");
                                                               fflush(tty);
                                                               if (has_termios) {
                                                                   struct termios raw = orig_tty_termios;
                                                                   raw.c_lflag &= ~(ECHO | ICANON);
                                                                   raw.c_cc[VMIN] = 1;
                                                                   raw.c_cc[VTIME] = 0;
                                                                   tcsetattr(fd, TCSANOW, &raw);
                                                               }
                                                               continue;
                                                           } else {
                                                               fprintf(tty, "^[\n");
                                                               fflush(tty);
                                                               approved = 0;
                                                               g_esc_requested = 1;
                                                               break;
                                                           }
                                                       }
                                                       
                                                       if (ch == '\n' || ch == '\r') {
                                                           fprintf(tty, "\n");
                                                           fflush(tty);
                                                           approved = 1;
                                                           break;
                                                       }
                                                       if (ch == 'y' || ch == 'Y') {
                                                           fprintf(tty, "%c\n", ch);
                                                           fflush(tty);
                                                           approved = 1;
                                                           break;
                                                       }
                                                       if (ch == 'n' || ch == 'N') {
                                                           fprintf(tty, "%c\n", ch);
                                                           fflush(tty);
                                                           approved = 0;
                                                           break;
                                                       }
                                                       if (ch == 'b' || ch == 'B') {
                                                           fprintf(tty, "%c\n", ch);
                                                           fflush(tty);
                                                           approved = 2;
                                                           break;
                                                       }
                                                       if (ch == 3) { // Ctrl+C
                                                           fprintf(tty, "^C\n");
                                                           fflush(tty);
                                                           approved = 0;
                                                           g_esc_requested = 1;
                                                           break;
                                                       }
                                                   }
                                                   
                                                   if (has_termios) {
                                                       tcsetattr(fd, TCSAFLUSH, &orig_tty_termios);
                                                   }
                                                   fclose(tty);
                                              } else {
                                                  fprintf(stderr, "[ai] Warning: cannot open /dev/tty for confirmation. Skipping command execution for safety.\n");
                                              }
                                          }

                                          if (approved == 1) {
                                              fprintf(stderr, "\r\033[2K\033[36m  ⬡ $ %s\033[0m\n", cmd_val);
                                              if (strncmp(cmd_val, "sleep ", 6) == 0 || strstr(cmd_val, " sleep ") || strstr(cmd_val, "&& sleep ") || strstr(cmd_val, "; sleep ")) {
                                                  tool_output = strdup("Error: `sleep` is forbidden in execute_command. Use `schedule_task` to wait or check status asynchronously.");
                                                  fprintf(stderr, "\r\033[2K\033[1;31m  ▶ sleep command rejected. Enforcing schedule_task.\033[0m\n");
                                              } else {
                                                  size_t cmd_len = strlen(cmd_val);
                                                  char *cmd_with_stderr = malloc(cmd_len + 16);
                                                  sprintf(cmd_with_stderr, "%s 2>&1", cmd_val);
                                                  int exit_code = 0;
                                                  char *raw_output = run_shell_command_timeout(cmd_with_stderr, &exit_code, cmd_timeout);
                                                  free(cmd_with_stderr);

                                                  if (exit_code == 130) /* ESC / SIGINT during command */
                                                      g_esc_requested = 1;

                                                  if (raw_output) {
                                                      size_t out_len = strlen(raw_output);
                                                      size_t cmd_len = strlen(cmd_val);
                                                      tool_output = malloc(out_len + cmd_len + 512);
                                                      if (exit_code == 0) {
                                                          sprintf(tool_output, "$ %s\n%s", cmd_val, raw_output);
                                                      } else {
                                                          sprintf(tool_output, "$ %s — failed (exit %d)\n%s\n[SYSTEM WARNING: Command failed. You MUST use the `think` tool to analyze the failure, explain why it failed, and formulate a new plan before executing another command.]", cmd_val, exit_code, raw_output);
                                                      }
                                                      free(raw_output);
                                                  } else {
                                                      tool_output = strdup("Error: failed to run command");
                                                  }

                                                  /* Auto-commit on successful command execution */
                                                  if (exit_code == 0 && g_git_commit_enabled && (g_permission_mode == 0 || g_plan_approved)) {
                                                      git_commit("command");
                                                  }
                                                  if (g_permission_mode == 1) plan_budget_consume();
                                              }
                                          } else if (approved == 2) {
                                              fprintf(stderr, "\r\033[2K\033[36m  ⬡ $ %s (background)\033[0m\n", cmd_val);
                                              char log_file[512];
                                              snprintf(log_file, sizeof(log_file), "%s/.config/ai/logs/bg_proc_%d.log", getenv("HOME"), (int)time(NULL));
                                              char mkdir_cmd[512];
                                              snprintf(mkdir_cmd, sizeof(mkdir_cmd), "mkdir -p %s/.config/ai/logs", getenv("HOME"));
                                              system(mkdir_cmd);
                                              char bg_cmd[4096];
                                              snprintf(bg_cmd, sizeof(bg_cmd), "nohup sh -c '%s' > '%s' 2>&1 & echo $!", cmd_val, log_file);
                                              FILE *fp = popen(bg_cmd, "r");
                                              char pid_str[32] = {0};
                                              if (fp) {
                                                  fgets(pid_str, sizeof(pid_str), fp);
                                                  pclose(fp);
                                              }
                                              char *nl = strchr(pid_str, '\n');
                                              if (nl) *nl = '\0';
                                              tool_output = malloc(1024 + strlen(cmd_val));
                                              sprintf(tool_output, "$ %s\n\nCommand launched in background. PID: %s\nOutput is logging to %s\nYou can use `check_process_status(pid=%s, log_file=\"%s\")` to monitor it.", cmd_val, pid_str, log_file, pid_str, log_file);
                                          } else {
                                              fprintf(stderr, "[ai] command execution cancelled.\n");
                                              tool_output = strdup("Error: Command execution was cancelled/denied by the user.");
                                          }
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

                                  /* Mode-based approval gate. In non-auto modes gate any MCP tool that mutates
                                  OR is not on the read-only allowlist (deny-by-default). */
                                  if (g_permission_mode != 0 && (tool_is_mutating(mcp_tool_name) || !tool_is_readonly(mcp_tool_name))) {
                                      if (g_permission_mode == 2) {
                                          int ok = prompt_user_ok(mcp_tool_name, unescaped_args);
                                          if (ok == 0 || ok == -1) {
                                              if (server_name) { free(server_name); server_name = NULL; }
                                              tool_output = strdup("Error: Action denied by user (manual mode). You must get explicit approval before changing anything.");
                                              goto mcp_gated;
                                          }
                                      } else if (g_permission_mode == 1 && !g_plan_approved) {
                                          if (server_name) { free(server_name); server_name = NULL; }
                                          tool_output = strdup("Error: [PLAN MODE] This action changes state but no plan is approved yet. Call present_plan with your proposed changes and get approval before proceeding. Nothing was changed.");
                                          goto mcp_gated;
                                      }
                                  }
                                  if (g_permission_mode == 1 && g_plan_approved && (tool_is_mutating(mcp_tool_name) || !tool_is_readonly(mcp_tool_name)))
                                      plan_budget_consume();

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
                                  
                                  const char *cached_res = get_tool_cache(mcp_tool_name, unescaped_args);
                                  if (cached_res) {
                                      fprintf(stderr, "\033[2m[ai] %s: (cached)\033[0m\n", mcp_tool_name);
                                      tool_output = strdup(cached_res);
                                  } else {
                                      char *escaped_args_shell = shell_escape(unescaped_args);
                                      char call_cmd[4096 + strlen(escaped_args_shell)];
                                      snprintf(call_cmd, sizeof(call_cmd), "python3 %s call-tool %s %s %s", mcp_script, server_name, mcp_tool_name, escaped_args_shell);
                                      tool_output = run_shell_command(call_cmd, NULL);
                                      if (tool_output && strncmp(tool_output, "Error", 5) != 0) {
                                          set_tool_cache(mcp_tool_name, unescaped_args, tool_output);
                                      }
                                      free(escaped_args_shell);
                                  }

                                  free(server_name);
                              }
                              mcp_gated:
                              if (!tool_output) {
                                  tool_output = strdup("Error: failed to execute tool");
                              }

                              /* User notification when the model improves its skills (self-improvement) */
                                  if (tool_output
                                      && (strstr(tool_output, "[SKILL_CREATED") || strstr(tool_output, "[SKILL_UPDATED")
                                          || strstr(tool_output, "[SKILL: updated"))) {
                                      fprintf(stderr, "\n\033[1;36m[ai] %s\033[0m\n", tool_output);
                                  }

                                  /* Prefix tool results with a structured header so small models
                                 can track which tool produced which data */
                              if (strcmp(unescaped_name, "think") != 0 &&
                                  strcmp(unescaped_name, "task_complete") != 0) {
                                  int is_err = (strncmp(tool_output, "Error:", 6) == 0 ||
                                                strncmp(tool_output, "[Command Failed", 15) == 0 ||
                                                strncmp(tool_output, "{\"error\"", 8) == 0 ||
                                                strstr(tool_output, "failed (exit") != NULL ||
                                                strstr(tool_output, "[SYSTEM WARNING:") != NULL);

                                  /* Situational state: append this outcome to the rolling log
                                     (bounded; oldest entries drop so a small model sees the recent
                                     trajectory without context bloat). Skipped when INFER_STATE_CONTEXT=0. */
                                  g_state_status = is_err ? 1 : 0;
                                  if (!tool_is_readonly(unescaped_name)) g_think_since_action = 0;
                                  {
                                      const char *_sc = getenv("INFER_STATE_CONTEXT");
                                      if (!_sc || atoi(_sc) != 0) {
                                          char _entry[120];
                                          snprintf(_entry, sizeof(_entry), "%d:%s=%s;", loop_count,
                                                   unescaped_name ? unescaped_name : "?", is_err ? "ERR" : "ok");
                                          while (strlen(g_state_log) + strlen(_entry) + 1 > sizeof(g_state_log)) {
                                              char *_p2 = strchr(g_state_log, ';');
                                              if (!_p2) { g_state_log[0] = '\0'; break; }
                                              memmove(g_state_log, _p2 + 1, strlen(_p2 + 1) + 1);
                                          }
                                          strcat(g_state_log, _entry);
                                      }
                                  }
                                                
                                  const char *graph_enforcement = "";
                                  const char *err_hint = "";
                                  if (is_err) {
                                      if (strstr(tool_output, "No such file") || strstr(tool_output, "FileNotFoundError")) {
                                          err_hint = "\n\n[HINT: File or path not found. Use list_directory to check the directory contents.]";
                                      } else if (strstr(tool_output, "Permission denied")) {
                                          err_hint = "\n\n[HINT: Permission denied. Check file permissions or paths.]";
                                      } else if (strstr(tool_output, "timed out") || strstr(tool_output, "Connection refused") || strstr(tool_output, "429") || strstr(tool_output, "503")) {
                                          err_hint = "\n\n[HINT: Network timeout or rate limit. Retry after a brief pause or try an alternative endpoint.]";
                                      } else if (strstr(tool_output, "Missing required argument") || strstr(tool_output, "Invalid argument type")) {
                                          err_hint = "\n\n[HINT: Tool argument validation failed. Verify required parameters and types before retrying.]";
                                      } else if (strncmp(tool_output, "[Command Failed", 15) != 0) {
                                          graph_enforcement = "\n\n[GRAPH ENFORCEMENT: Middleware intercepted an exception. You must pause, recalculate your approach, and try a different strategy.]";
                                      }
                                  }

                                  /* ── Automatic self-improvement: harness learns from tool errors ──
                                     Deterministic (does not depend on the model volunteering to persist):
                                     every failure is logged to a persistent ledger; a recurring failure is
                                     auto-promoted to a lesson; a later success of the same tool is recorded
                                     as the fix; past lessons for this exact mistake are surfaced. */
                                  if (tool_output) {
                                      char si_name[128];
                                      const char *_si_u = strstr(unescaped_name, "__");
                                      if (_si_u) snprintf(si_name, sizeof(si_name), "%s", _si_u + 2);
                                      else snprintf(si_name, sizeof(si_name), "%s", unescaped_name);

                                      int si_failed = is_err ||
                                          (tool_output && strstr(tool_output, "-- failed (exit") != NULL);

                                      if (si_failed) {
                                          /* persist the failure + detect recurrence */
                                          char *ej_args = json_escape(unescaped_args ? unescaped_args : "");
                                          char *ej_err  = json_escape(tool_output);
                                          char si_pay[9000];
                                          snprintf(si_pay, sizeof(si_pay),
                                                   "{\"tool\":\"%s\",\"args\":\"%s\",\"error\":\"%s\",\"phase\":\"execution\"}",
                                                   si_name, ej_args, ej_err);
                                          free(ej_args); free(ej_err);
                                          {
                                              char si_cmd[10000];
                                              snprintf(si_cmd, sizeof(si_cmd),
                                                       "python3 %s record-failure %s 2>/dev/null",
                                                       mcp_script, shell_escape(si_pay));
                                              char *_o = run_shell_command(si_cmd, NULL);
                                              if (_o) free(_o);
                                          }
                                          /* remember it so a later success = auto-learned fix */
                                          int _slot = -1;
                                          for (int _i = 0; _i < SI_MAX_TRACKED; _i++)
                                              if (g_si_failed[_i].used && strcmp(g_si_failed[_i].tool, si_name) == 0) { _slot = _i; break; }
                                          if (_slot < 0)
                                              for (int _i = 0; _i < SI_MAX_TRACKED; _i++)
                                                  if (!g_si_failed[_i].used) { _slot = _i; break; }
                                          if (_slot >= 0) {
                                              g_si_failed[_slot].used = 1;
                                              snprintf(g_si_failed[_slot].tool, sizeof(g_si_failed[_slot].tool), "%s", si_name);
                                              snprintf(g_si_failed[_slot].args,  sizeof(g_si_failed[_slot].args),  "%s", unescaped_args ? unescaped_args : "");
                                              snprintf(g_si_failed[_slot].error, sizeof(g_si_failed[_slot].error), "%s", tool_output);
                                          }
                                          /* surface any past lesson for this exact mistake (cross-session memory) */
                                          {
                                              char *ej_err2 = json_escape(tool_output);
                                              char si_lpay[9000];
                                              snprintf(si_lpay, sizeof(si_lpay), "{\"tool\":\"%s\",\"error\":\"%s\"}", si_name, ej_err2);
                                              free(ej_err2);
                                              char si_lcmd[10000];
                                              snprintf(si_lcmd, sizeof(si_lcmd),
                                                       "python3 %s lessons-for %s 2>/dev/null",
                                                       mcp_script, shell_escape(si_lpay));
                                              char *_less = run_shell_command(si_lcmd, NULL);
                                              if (_less && *_less) {
                                                  size_t _nl = strlen(_less) + strlen(tool_output) + 96;
                                                  char *_nb = malloc(_nl);
                                                  snprintf(_nb, _nl,
                                                           "[REMEMBERED FROM PAST SESSIONS (self-improvement)]\n%s\n---\n%s",
                                                           _less, tool_output);
                                                  free(tool_output);
                                                  tool_output = _nb;
                                                  fprintf(stderr, "\033[1;36m[ai] surfaced past lesson for '%s' to model\033[0m\n", si_name);
                                              }
                                              if (_less) free(_less);
                                          }
                                      } else {
                                          /* success of a tool that failed earlier this task -> record the fix */
                                          int _slot = -1;
                                          for (int _i = 0; _i < SI_MAX_TRACKED; _i++)
                                              if (g_si_failed[_i].used && strcmp(g_si_failed[_i].tool, si_name) == 0) { _slot = _i; break; }
                                          if (_slot >= 0) {
                                              char *ej_args = json_escape(unescaped_args ? unescaped_args : "");
                                              char *ej_prev = json_escape(g_si_failed[_slot].error);
                                              char si_rpay[9000];
                                              snprintf(si_rpay, sizeof(si_rpay),
                                                       "{\"tool\":\"%s\",\"args\":\"%s\",\"prior_error\":\"%s\",\"phase\":\"execution\"}",
                                                       si_name, ej_args, ej_prev);
                                              free(ej_args); free(ej_prev);
                                              {
                                                  char si_rcmd[10000];
                                                  snprintf(si_rcmd, sizeof(si_rcmd),
                                                           "python3 %s record-recovery %s 2>/dev/null",
                                                           mcp_script, shell_escape(si_rpay));
                                                  char *_o = run_shell_command(si_rcmd, NULL);
                                                  if (_o) {
                                                      fprintf(stderr, "\033[1;36m[ai] learned fix for '%s': %s\033[0m\n", si_name, _o);
                                                      free(_o);
                                                  }
                                              }
                                              g_si_failed[_slot].used = 0;
                                          }
                                      }
                                  }

                                  /* ── Display tool result with styled box ── */
                                  {
                                      const char *status_label = is_err ? "error" : "ok";
                                      char *full_content = tool_output ? strdup(tool_output) : strdup("");
                                      if (graph_enforcement && *graph_enforcement) {
                                          size_t gc = strlen(full_content) + strlen(graph_enforcement) + 1;
                                          full_content = realloc(full_content, gc);
                                          strcat(full_content, graph_enforcement);
                                      }
                                      if (err_hint && *err_hint) {
                                          size_t ec = strlen(full_content) + strlen(err_hint) + 1;
                                          full_content = realloc(full_content, ec);
                                          strcat(full_content, err_hint);
                                      }

                                      double tool_elapsed = get_time_sec_mono() - tool_t0;
                                      print_tool_box(unescaped_name, status_label,
                                                     full_content, tool_elapsed);
                                      add_turn_item(ITEM_TOOL_CALL, unescaped_name, status_label,
                                                    full_content, tool_elapsed, 0, g_turn_count, total_tool_count);
                                      free(full_content);
                                  }

                                  size_t hlen = strlen(unescaped_name) + (tool_output ? strlen(tool_output) : 0) + 64
                                              + (graph_enforcement ? strlen(graph_enforcement) : 0)
                                              + (err_hint ? strlen(err_hint) : 0);
                                  char *hout = malloc(hlen);
                                  snprintf(hout, hlen, "%s\n%s%s%s", unescaped_name,
                                           tool_output ? tool_output : "",
                                           (graph_enforcement && *graph_enforcement) ? graph_enforcement : "",
                                           (err_hint && *err_hint) ? err_hint : "");
                                  free(tool_output);
                                  tool_output = hout;
                              }

                              /* Cap individual tool output to prevent context blowup */
                              if ((int)strlen(tool_output) > max_tool_output) {
                                  size_t orig_len = strlen(tool_output);
                                  char suffix[1024];
                                  int display_len = max_tool_output;
                                  
                                  if (orig_len > max_tool_output * 1.5) {
                                      display_len = 2000;
                                      snprintf(suffix, sizeof(suffix), 
                                               "\n\n... [CRITICAL ERROR: Tool output was %zu bytes! This massively exceeds the safety limit of %d bytes.\n"
                                               "To prevent context window overflow and immediate failure, the output has been BLOCKED.\n"
                                               "YOU MUST REDO THIS ACTION using a targeted approach. DO NOT attempt to load large datasets into context.\n"
                                               "Write targeted Python scripts, or use 'head', 'tail', 'grep' to filter the data.]", 
                                               orig_len, max_tool_output);
                                  } else {
                                      snprintf(suffix, sizeof(suffix), 
                                               "\n\n... [TRUNCATED: Tool output was %zu bytes. Capped at %d bytes. The model should decide if it wants to use a different tool/command to narrow down the query (e.g. grep, find, head/tail, line-range read_file), or run the command with pagination, or proceed with the truncated context.]", 
                                               orig_len, max_tool_output);
                                  }
                                  
                                  size_t suffix_len = strlen(suffix);
                                  char *capped = malloc(display_len + suffix_len + 1);
                                  if (capped) {
                                      memcpy(capped, tool_output, display_len);
                                      strcpy(capped + display_len, suffix);
                                      free(tool_output);
                                      tool_output = capped;
                                  }
                              }

                              /* If total context is already large, stub this result */
                              if ((int)strlen(messages_json) > stub_threshold) {
                                  free(tool_output);
                                  tool_output = strdup("[context limit reached \xe2\x80\x94 result omitted to preserve model focus]");
                              }

                              /* Scheduled Context Reset */
                              if (strcmp(unescaped_name, "reset_context") == 0) {
                                  fprintf(stderr, "[ai] Context reset requested — truncating message history to system prompt.\n");
                                  free(messages_json);
                                  messages_json = strdup("[]");
                                  if (g_system_message_json) {
                                      messages_json = append_message(messages_json, g_system_message_json);
                                  }
                                  
                                  free(tool_output);
                                  tool_output = NULL;
                                  
                                  char *reset_msg = strdup("{\"role\":\"user\",\"content\":\"[SYSTEM] Context successfully reset. The history has been cleared to preserve reasoning capacity. Please continue your plan from here.\"}");
                                  messages_json = append_message(messages_json, reset_msg);
                                  free(reset_msg);
                                  
                                  free(unescaped_id);
                                  free(unescaped_name);
                                  free(unescaped_args);
                                  goto end_tool_iter;
                              }

                              if (g_plan_budget_note) {
                                  size_t _bnl = strlen(g_plan_budget_note) + (tool_output ? strlen(tool_output) : 0) + 2;
                                  char *_bnt = malloc(_bnl);
                                  if (tool_output) snprintf(_bnt, _bnl, "%s%s", g_plan_budget_note, tool_output);
                                  else snprintf(_bnt, _bnl, "%s", g_plan_budget_note);
                                  if (tool_output) free(tool_output);
                                  tool_output = _bnt;
                                  free(g_plan_budget_note);
                                  g_plan_budget_note = NULL;
                              }
                              /* Prepend a compact situational header to the tool result so the model
                                 always sees its step + status + recent trajectory (small models drop
                                 this context if left implicit). Skipped when INFER_STATE_CONTEXT=0. */
                              {
                                  const char *_ln = unescaped_name ? unescaped_name : "";
                                  if (g_state_log[0]
                                      && strcmp(_ln, "think") != 0 && strcmp(_ln, "task_complete") != 0
                                      && (!getenv("INFER_STATE_CONTEXT") || atoi(getenv("INFER_STATE_CONTEXT")) != 0)) {
                                      const char *_src = tool_output ? tool_output : "";
                                      size_t _cl = strlen(_src) + strlen(g_state_log) + 96;
                                      char *_combined = malloc(_cl);
                                      snprintf(_combined, _cl, "[CURRENT STATE step %d] %s -> %s  |  %s\n%s",
                                               loop_count, _ln, g_state_status ? "error" : "ok",
                                               g_state_log, _src);
                                      if (tool_output) free(tool_output);
                                      tool_output = _combined;
                                  }
                              }
                              char *safe_output = json_escape(tool_output);
                              char *safe_id = json_escape(unescaped_id);
                              char *safe_name = json_escape(unescaped_name);
                              size_t tool_resp_len = strlen(safe_output) + strlen(safe_id) + strlen(safe_name) + 256;
                              char *tool_resp = malloc(tool_resp_len);
                              snprintf(tool_resp, tool_resp_len, "{\"role\":\"tool\",\"tool_call_id\":\"%s\",\"name\":\"%s\",\"content\":\"%s\"}", safe_id, safe_name, safe_output);
                              messages_json = append_message(messages_json, tool_resp);
                              free(safe_id);
                              free(safe_name);

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

                          end_tool_iter:
                          current_tok = json_skip_token(tok, r, current_tok + 1);
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
                                                const char *nudge_cap_env = getenv("INFER_NUDGE_CAP");
                                                if (nudge_cap_env && atoi(nudge_cap_env) > 0)
                                                    force_complete_after = atoi(nudge_cap_env);
                                                length_nudge_count++;
                                                fprintf(stderr, "\033[1;33m[ai] Warning: model hit token limit — "
                                                                "response truncated. Nudging to complete.\033[0m\n");
                                                if (length_nudge_count >= force_complete_after) {
                                                    fprintf(stderr,
                                                        "\033[1;31m[ai] Model stalled (hit token limit %d consecutive "
                                                        "times with no completion). Forcing task_complete.\033[0m\n",
                                                        length_nudge_count);
                                                    messages_json = append_message(messages_json,
                                                        "{\"role\":\"user\",\"content\":\"[STALL] Your responses keep hitting the "
                                                        "token limit without any progress and without calling task_complete. "
                                                        "STOP taking additional actions. Call task_complete NOW with your best "
                                                        "summary of work done so far. Do not call any more tools.\\\"}");
                                                    loop_count = 28; /* allow exactly one more iteration */
                                                    length_nudge_count = 0;
                                                    has_more = 1;
                                                } else {
                                                    messages_json = append_message(messages_json,
    "{\"role\":\"user\",\"content\":\"Your last response was cut off by the token limit. This means you wrote an over-long chain of thought and consumed the whole output budget WITHOUT emitting a tool call - reasoning alone never produces an artifact, and that is exactly why you are stuck. NEXT TIME: write at most 2-3 short sentences of reasoning, then IMMEDIATELY emit ONE action tool call (write_file to create your code file, or execute_command to build/run). Do NOT call task_complete until the deliverable actually exists. Repeat: short reasoning then one action tool call, right now.\"}");
                                                    has_more = 1;
                                                }
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
                              k = json_skip_token(tok, r, k + 2);
                          }
                      }

                      if (reasoning_content_tok != -1 && tok[reasoning_content_tok].type == JSMN_STRING) {
                          char *reasoning_str = unescape_json_string(chunk.data + tok[reasoning_content_tok].start, tok[reasoning_content_tok].end - tok[reasoning_content_tok].start);
                          if (reasoning_str && *reasoning_str) {
                              add_turn_item(ITEM_THINKING, "thinking", NULL, reasoning_str, 0, 0, 0, 0);
                          }
                          if (reasoning_str) free(reasoning_str);
                      }

                      if (content_tok != -1 && tok[content_tok].type == JSMN_STRING) {
                          char *unescaped_content = unescape_json_string(chunk.data + tok[content_tok].start, tok[content_tok].end - tok[content_tok].start);

                          /* Detect Gemma-style leaked task_complete in text output */
                          char *leaked_summary = extract_leaked_task_complete(unescaped_content);
                          if (leaked_summary) {
                              log_job(current_prompt, pipe_writer, leaked_summary, interactive_mode);
                              char *rendered = render_markdown(leaked_summary);
                              fflush(stderr);
                              double tps = (elapsed_sec > 0.05 && completion_tokens > 0)
                                           ? completion_tokens / elapsed_sec : 0.0;
                              add_turn_item(ITEM_ASSISTANT_RESPONSE, model[0] ? model : "ai", NULL,
                                            leaked_summary, elapsed_sec, tps, g_turn_count, total_tool_count);
                              if (rendered && *rendered) {
                                  print_response_box(model[0] ? model : "ai", rendered,
                                                     g_turn_count, total_tool_count,
                                                     elapsed_sec, tps, 0);
                                  free(rendered);
                              } else {
                                  print_response_box(model[0] ? model : "ai", leaked_summary,
                                                     g_turn_count, total_tool_count,
                                                     elapsed_sec, tps, 0);
                              }
                              free(leaked_summary);
                              free(unescaped_content);
                              has_more = 0;
                              goto next_turn;
                          }

                          log_job(current_prompt, pipe_writer, unescaped_content, interactive_mode);
                          char *rendered_output = render_markdown(unescaped_content);

                          fflush(stderr);
                          {
                              double tps = (elapsed_sec > 0.05 && completion_tokens > 0)
                                           ? completion_tokens / elapsed_sec : 0.0;
                              add_turn_item(ITEM_ASSISTANT_RESPONSE, model[0] ? model : "ai", NULL,
                                            unescaped_content, elapsed_sec, tps, g_turn_count, total_tool_count);
                              if (rendered_output && *rendered_output) {
                                  print_response_box(model[0] ? model : "model", rendered_output,
                                                     g_turn_count, total_tool_count,
                                                     elapsed_sec, tps, 0);
                                  free(rendered_output);
                              } else {
                                  print_response_box(model[0] ? model : "model", unescaped_content,
                                                     g_turn_count, total_tool_count,
                                                     elapsed_sec, tps, 0);
                              }
                          }

                          free(unescaped_content);
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
                              "\n\033[2m  loop %d · ctx %d/%d (%d%%) · +%d tok · %.0f tok/s\033[0m\n",
                              loop_count, total_tokens, context_window, pct,
                              completion_tokens, tps);
                      } else {
                          fprintf(stderr,
                              "\n\033[2m  loop %d · %d ctx · +%d new · %.0f tok/s\033[0m\n",
                              loop_count, prompt_tokens, completion_tokens, tps);
                      }
                  }

                  /* Task 2: Interrupt Session Leak fix — append interrupted message on ESC */
                  if (g_esc_requested) {
                      fprintf(stderr, "\033[1;33m[ai] Task interrupted by user (ESC/Ctrl+C).\033[0m\n");
                      messages_json = append_message(messages_json,
                          "{\"role\":\"user\",\"content\":\"[INTERRUPTED: Task interrupted by user (ESC/Ctrl+C) during tool execution.]\"}");
                      has_more = 0;
                      break;
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
                        "\n\033[1;33m  %d steps completed. Continue for 30 more?\033[0m\n\033[32my\033[0m/\033[31mn\033[0m: ",
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
                    "\033[1;33m  task stopped after %d steps\033[0m\n",
                    loop_count);
                has_more = 0;
            }
            g_agent_loop_active = 0;
            if (last_tool_name) free(last_tool_name);
            if (last_tool_args) free(last_tool_args);
          }

          if (!interactive_mode) {
              keep_going = 0;
          }
      }

    free(pipe_in);
    if (pipe_writer) free(pipe_writer);
    free(prompt);
    /* Persist this conversation so a later `ai -r` / `ai --resume` can continue it. */
    save_session(messages_json);
    free(messages_json);
    if (tools_json) free(tools_json);
    if (current_prompt) free(current_prompt);
    if (g_system_message_json) free(g_system_message_json);
    curl_slist_free_all(h);
    curl_easy_cleanup(c);
    fprintf(stderr, "\033[2m  session ended · resume: ai -r %s\033[0m\n", current_session_id);
    return 0;
}
