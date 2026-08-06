# CLI Improvements — Actionable Code Changes

## 1. Enhanced Color Palette

Add these defines after the existing `CL_BG_MAG` (line ~50):

```c
#define CL_BRIGHT   "\033[22m"   /* Normal intensity (undo bold) */
#define CL_UNDERLINE "\033[4m"
#define CL_ORANGE   "\033[38;5;208m"
#define CL_TEAL     "\033[38;5;37m"
#define CL_PURPLE   "\033[38;5;135m"
#define CL_DARK_BG  "\033[48;5;236m"
#define CL_MED_BG   "\033[48;5;241m"
#define CL_LIGHT_BG "\033[48;5;246m"
```

## 2. Tool-Type Icons & Colors

Add a helper function near the top of the file (after `print_tool_box`):

```c
/* ── Tool type metadata ── */
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
    {"scientific__",        "⟨⟩", CL_PURPLE},
    {"vault_",              "⊞",  CL_PURPLE},
    {NULL, NULL, NULL}
};

static const char *tool_get_icon(const char *name) {
    for (int i = 0; g_tool_meta[i].name; i++) {
        if (strcmp(g_tool_meta[i].name, name) == 0)
            return g_tool_meta[i].icon;
    }
    return "·";
}

static const char *tool_get_color(const char *name) {
    for (int i = 0; g_tool_meta[i].name; i++) {
        if (strcmp(g_tool_meta[i].name, name) == 0)
            return g_tool_meta[i].color;
    }
    return CL_DIM;
}
```

## 3. Enhanced Prompt Bar

Replace the current prompt construction (around line 4147-4150):

**Current:**
```c
char prompt_str[256];
snprintf(prompt_str, sizeof(prompt_str),
         "\033[1;35mai\033[0m\033[2m▸ \033[0m%s", model_display);
```

**Proposed:**
```c
/* Build rich prompt with turn counter and permission icon */
const char *perm_icon = g_permission_mode == 0 ? CL_GREEN "●"
                      : g_permission_mode == 1 ? CL_YELLOW "◐"
                      : CL_RED "○";
const char *perm_label = g_permission_mode == 0 ? "auto"
                       : g_permission_mode == 1 ? "plan"
                       : "manual";

char prompt_str[512];
snprintf(prompt_str, sizeof(prompt_str),
    "\033[1;35m  ai\033[0m"
    "\033[2m│\033[0m"
    "\033[36m●\033[0m "
    "\033[1;36m%s\033[0m"
    "\033[2m  turn %d\033[0m"
    "\033[2m│\033[0m"
    " %s%s  \033[0m",
    model[0] ? model : "unknown",
    g_turn_count,
    perm_icon, perm_label);
```

Add a bottom border line after the prompt:
```c
printf("\033[2m  ─%*s\033[0m\n", lineed_term_cols() - 4, "");
```

## 4. Enhanced Tool Box

Modify `print_tool_box` to include icon, color, and timing:

```c
static void print_tool_box(const char *name, const char *status,
                           const char *content, double elapsed_sec)
{
    const char *icon = tool_get_icon(name);
    const char *hc   = tool_get_color(name);

    const char *st_c = (status && strncmp(status, "error", 5) == 0) ? CL_RED
                     : (status && strcmp(status, "ok") == 0)      ? CL_GREEN
                     : CL_DIM;
    const char *st_i = (status && strncmp(status, "error", 5) == 0) ? "✕"
                     : (status && strcmp(status, "ok") == 0)        ? "●"
                     : "◦";

    printf("\n");
    printf("%s╭─%s %s%s%s %s%s%s%s╮\n",
           CL_DIM, CL_RESET, hc, icon, CL_RESET,
           CL_BOLD, name, CL_RESET, CL_DIM);

    /* Status line: icon + status + timing */
    printf("%s│ %s%s%s  %s%s%s", CL_DIM, st_c, st_i, CL_RESET,
           st_c, status ? status : "done", CL_RESET);
    if (elapsed_sec > 0) {
        printf("  \033[2m%.1fs\033[0m", elapsed_sec);
    }
    printf("%*s│%s\n", 0, "", CL_DIM);
    printf("%s╰─%s%s╯\n", CL_DIM, CL_DIM, CL_DIM);

    /* Content with left indent */
    if (content) {
        const char *line = content;
        const int max_lines = 50;
        int lines = 0;
        while (*line && lines < max_lines) {
            const char *nl = strchr(line, '\n');
            if (!nl) {
                printf("%s  \033[2m%s\033[0m\n", CL_DIM, line);
                break;
            }
            int len = (int)(nl - line);
            printf("%s  \033[2m%.*s\033[0m\n", CL_DIM, len, line);
            line = nl + 1;
            lines++;
        }
        if (*line && lines >= max_lines) {
            printf("%s  \033[2m... (%zu more lines)\033[0m\n",
                   CL_DIM, strlen(line));
        }
    }
    printf("\n");
}
```

## 5. Table Renderer

Add a new function for markdown table rendering:

```c
/* ── Simple markdown table renderer ── */
static void print_markdown_table(const char *content)
{
    if (!content) return;

    int cols = lineed_term_cols() - 8; /* padding */
    if (cols < 40) cols = 40;

    /* Parse the table into rows */
    const char *line = content;
    int max_cols = 4;
    int **col_widths = calloc(max_cols, sizeof(int));
    char ***rows = NULL;
    int nrows = 0;
    int row_cap = 32;
    rows = calloc(row_cap, sizeof(char*));

    while (*line) {
        const char *nl = strchr(line, '\n');
        int len = nl ? (int)(nl - line) : (int)strlen(line);
        if (len == 0) { line = nl ? nl + 1 : ""; continue; }

        /* Skip separator rows (|---|---|) */
        int is_sep = 1;
        for (int i = 0; i < len; i++) {
            if (line[i] != '-' && line[i] != ':' && line[i] != '|' && line[i] != ' ') {
                is_sep = 0;
                break;
            }
        }
        if (is_sep) { line = nl ? nl + 1 : ""; continue; }

        /* Parse columns */
        char *row_str = strndup(line, len);
        char **cells = NULL;
        int ncells = 0;
        int cap = 8;
        cells = calloc(cap, sizeof(char*));

        const char *p = row_str;
        while (*p) {
            while (*p == '|' || *p == ' ') p++;
            const char *cell_start = p;
            while (*p && *p != '|') p++;
            int clen = p - cell_start;
            while (*p == '|') p++;
            if (clen > 0) {
                if (ncells >= cap) { cap *= 2; cells = realloc(cells, cap * sizeof(char*)); }
                cells[ncells++] = strndup(cell_start, clen);
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

    /* Render */
    for (int r = 0; r < nrows; r++) {
        char **cells = rows[r];
        printf("%s│", CL_DIM);
        for (int c = 0; c < max_cols && c < max_cols; c++) {
            char *cell = (c < nrows) ? cells[c] : "";
            int w = col_widths[c] + 2; /* padding */
            printf(" %-*.*s%s", w, w, cell, CL_DIM);
            if (c < max_cols - 1) printf("│");
        }
        printf("│%s\n", CL_RESET);
        if (r == 0) {
            /* Separator after header */
            printf("%s");
            for (int c = 0; c < max_cols; c++) {
                printf("┼"); /* or "─" for top */
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
```

## 6. Thinking Display

Add a dedicated function for `think` tool output:

```c
static void print_think_box(const char *reasoning)
{
    printf("\n");
    printf("%s╭─%s %s◈%s %s%s%s╮\n",
           CL_DIM, CL_RESET, CL_DIM, CL_RESET,
           CL_DIM, "Thinking", CL_RESET);
    printf("%s╰─%s%s╯\n", CL_DIM, CL_DIM, CL_DIM);
    if (reasoning) {
        printf("%s  %s\033[2m%s\033[0m\n", CL_DIM, CL_DIM, reasoning);
    }
    printf("%s╭─%s%s╮\n\n", CL_DIM, CL_DIM, CL_DIM);
}
```

## 7. Input UX — Keybinding Hints

Add a hint line below the prompt:

```c
/* After prompt_str display, add keybinding hint */
printf("\033[2m  %s[Shift-Tab: cycle mode · Enter: send · Ctrl+D: exit]%s\033[0m\n",
       CL_DIM, CL_RESET);
```

## 8. Response Box — Enhanced Footer

Add latency info to response box footer:

```c
static void print_response_box(const char *model_name, const char *content,
                               int turn_count, int tool_count,
                               double total_seconds)
{
    /* ... existing header code ... */

    /* Enhanced footer with timing */
    printf("%s╰─%s", CL_DIM, CL_RESET);
    printf(" turn %d", turn_count > 0 ? turn_count : 0);
    if (tool_count > 0) printf("  %d tools", tool_count);
    if (total_seconds > 0) printf("  \033[2m%.1fs\033[0m", total_seconds);
    printf("%s╯\n\n", CL_DIM);
}
```

---

## Integration Points

Where to call the enhanced functions:

| Change | Location in `ai.c` |
|--------|-------------------|
| Tool timing | Capture `start_time` before tool call, pass elapsed to `print_tool_box` |
| Response timing | Capture end time after streaming, pass to `print_response_box` |
| Think display | Detect "think" in tool name in the tool processing loop |
| Table rendering | In `render_markdown`, detect `|...|` pattern and call `print_markdown_table` |
| Input hints | After `read_line_interactive` prompt display |

---

# Code Quality & Architecture Improvements

## Critical: Security

### 1. SSH password exposed via `sshpass` process list

**File:** `remote_harness.c` — `remote_connect()`

The password is passed as a CLI argument to `sshpass -p '...'`, which means it is visible in `ps aux` to every user on the system. Replace with env-var-based auth:

```c
setenv("SSHPASS", password, 1);
snprintf(cmd, sizeof(cmd), "sshpass -e ssh %s@%s -p %d ...", user, host, port);
```

Or better yet, require key-based auth and drop `sshpass` entirely. If password auth is needed, write the password to a temp file with mode 0600 and use `sshpass -f /tmp/sshpass.XXXXXX`.

### 2. Command denylist is substring-based and easily bypassed

**File:** `ai.c` — `is_command_denied()`

`strstr(cmd, "rm -rf /")` is a trivial substring check. Workarounds: `rm -rf //`, `rm -rf $HOME`, `rm -rf /` with a wildcard like `rm -rf *`. Add glob/wildcard pattern detection, environment variable expansion detection, and file-descriptor tricks. Consider using a proper denylist library or regex with word boundaries.

### 3. `strncpy` does not null-terminate — unbounded read risk

**File:** `remote_harness.c` — `remote_connect()`

```c
strncpy(rh->host, host, sizeof(rh->host) - 1);
```

`strncpy` does not guarantee null termination when the source is >= dest size. If an attacker supplies a 256-byte hostname, the `host` field is not null-terminated, and subsequent `snprintf` reads past the buffer. Fix with `snprintf(rh->host, sizeof(rh->host), "%s", host)`.

---

## High Priority: Architecture

### 4. Split `ai.c` (5600 lines) into modules

The single C file contains: HTTP/SSE, JSON parsing, agent loop, tool dispatch (think/task_complete/execute_command/remote_exec/MCP), interactive mode (lineed), git integration, base64, markdown rendering, system prompt (~24KB literal), memory, session persistence, and argument parsing. Split into:

- `ai_main.c` — entry point, agent loop, argument parsing
- `ai_http.c` — curl setup, SSE streaming, retry logic
- `ai_tools.c` — tool dispatch switch, per-tool handlers
- `ai_interactive.c` — lineed TUI, prompt display, raw mode
- `ai_utils.c` — JSON escape, base64, markdown rendering, shell escape

### 5. Split `ai_mcp.py` (4400 lines) into modules

Current file handles: HTTP, search engines (Brave/SearXNG/DDG), web fetching (curl_cffi/Playwright/urllib), MCP JSON-RPC, memory DB, skill loading. Split into:

- `mcp_server.py` — JSON-RPC server, MCP protocol
- `tools_file.py` — file read/write/edit/list tools
- `tools_search.py` — `web_search`, `arxiv_search`, `pubmed_search`
- `tools_schedule.py` — calendar, reminders, notifications
- `tools_remote.py` — `remote_exec`, HPC job submission
- `search.py` — `brave_search`, `searxng_search`, `ddg_lite_search`, `web_search` orchestrator
- `fetch.py` — `fetch_smart` cascade, `fetch_webpage`, `fetch_webpage_js`, `_html_to_text_fallback`
- `memory.py` — `save_memory`, `recall`, FTS5 DB

### 6. Move system prompt out of C binary

The `SYSTEM_PROMPT` variable (~24KB C string literal) is baked into the binary. Editing the prompt requires recompiling. Move to `system_prompt.txt` or `system_prompt.md` loaded at startup, with template variables (e.g. `{model}`, `{tools_json}`) replaced at runtime. This also lets users customize without touching C code.

### 7. Replace static 4096 `jsmntok_t` array with dynamic allocation

**File:** `ai.c` — agent loop

```c
jsmntok_t tok[4096];
```

If a model response JSON exceeds 4096 tokens (e.g. tool arguments with large base64 strings), parsing silently fails with `JSMN_ERROR_NOMEM`. Use dynamic allocation with `malloc` + `realloc`, or expose a configurable limit via `INFER_JSON_TOKENS` env var.

### 8. Add remote_harness.c to the build

`compile.sh` compiles only `ai.c`. `remote_harness.c` and `remote_harness.h` are listed in the directory but never compiled into the binary. If `remote_exec`/`remote_harness` is intended to be part of the build (it provides the `remote_exec` tool), add it to the compile command:

```bash
gcc -o ai ai.c remote_harness.c -lcurl -ljsmn ...
```

---

## Medium Priority: Code Quality

### 9. Duplicate search implementations

**File:** `ai_mcp.py`

`ddg_lite_search`, `brave_search`, `searxng_search`, and `web_search` are all defined, but the C `web_search` tool only calls `ddg_lite_search`. The Brave and SearXNG functions are never invoked from the C side. The C tool should call the `web_search` orchestrator, which routes through the full cascade.

### 10. No proper build system

Replace `compile.sh` with a `Makefile`:

```makefile
CC = gcc
CFLAGS = -Wall -Wextra -Wconversion -O2 -D_GNU_SOURCE
LDFLAGS = -lcurl -ljsmn -lpthread -lm
TARGET = ai

$(TARGET): ai.c remote_harness.c jsmn.h
	$(CC) $(CFLAGS) -o $@ $^ $(LDFLAGS)

install: $(TARGET)
	install -m 755 $(TARGET) $(PREFIX)/bin/

clean:
	rm -f $(TARGET)

.PHONY: install clean
```

Also add `make test` to run `pytest tests/`.

### 11. Remove unused `cJSON` includes and source

`ai.c` includes `cJSON.h` but never uses any cJSON functions — JSON parsing is done by `jsmn`. `cJSON.c` and `cJSON.h` are listed in the directory but never compiled. Remove them or add a comment noting they are unused. The `cJSON.h` header unnecessarily includes `<sys/types.h>` and `<sys/stat.h>`.

### 12. Missing `-lm` and `_GNU_SOURCE` for `remote_harness.c`

`remote_harness.c` uses `clock_gettime` (needs `_POSIX_C_SOURCE` or `-lrt` on older glibc) and `strptime` (needs `_GNU_SOURCE`). The compile script doesn't define these flags. Add `-D_GNU_SOURCE` to `CFLAGS` and ensure `-lm` is linked.

---

## Low Priority: Polish & DX

### 13. Add structured configuration file

All settings are environment variables (`INFER_BASE_URL`, `INFER_API_KEY`, `INFER_MODEL`, `INFER_CONTEXT_WINDOW`, etc.) with no persistent config. Add `~/.config/ai/config.yaml` or `config.json` read at startup so users don't need to set env vars in their shell profile.

### 14. Document undocumented environment variables

`INFER_FETCH_BASIC=1` (forces plain urllib fetch), `INFER_AUTO_APPROVE` (auto-approve commands), `INFER_MAX_TOOL_OUTPUT` — none of these appear in `--help` or documentation. Add them to the help output and to `compile.sh`'s usage text.

### 15. Add CI/CD pipeline

No GitHub Actions, linting, or format checking. Add a CI workflow that:
- Runs `make` to verify the C build doesn't break
- Runs `python -m pytest tests/` for Python tests
- Runs `clang-tidy` or `cppcheck` on C code

### 16. Replace `goto end_tool_iter` with structured control flow

**File:** `ai.c` — agent loop

```c
if (strcmp(unescaped_name, "reset_context") == 0) {
    // ... resets context ...
    goto end_tool_iter;
}
```

The `goto` bypasses normal cleanup. While the current code appears to free resources before the goto, the pattern is fragile. Convert to a helper function or use a `continue` after setting a flag.

