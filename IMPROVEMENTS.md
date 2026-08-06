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
