# CLI Harness Review — Visual & Interaction Improvements

## Current Architecture

The CLI (`ai.c`, ~4900 lines) is a single-file C program that:

- Reads user input via a custom line editor (`lineed`) in interactive mode
- Sends messages to an LLM via HTTP
- Calls MCP tools via `ai_mcp.py` subprocess
- Renders tool results in styled boxes using box-drawing characters (`┌──┐│└┘`)
- Renders the LLM's final response in the same box format (as "💬 model_name")
- Uses a Python `ai_mcp.py render-markdown` subprocess to convert markdown to colored terminal output

---

## Issues Identified

### 1. LLM Response Uses the Same Box as Tool Calls (Major)

**Location:** `ai.c:4775-4782`

```c
snprintf(box_name, sizeof(box_name), "💬 %s",
         model[0] ? model : "model");
if (rendered_output && *rendered_output) {
    print_tool_box(box_name, "response", rendered_output);
```

The model's final answer is wrapped in `print_tool_box` — the same function used for every tool call. This means the user sees:

```
┌── 💬 claude-sonnet-4-20250514 (response) ────────┐
│  Your task is complete...                        │
└──────────────────────────────────────────────────┘
```

This is visually identical to a tool execution box and creates cognitive friction. The user must parse the header to understand this is the model's *answer*, not a tool result.

**Fix:** Add a new `print_response_box` function that renders the LLM's response as a clean, chat-style output — a left accent bar or simple underline, not a full box. The response should feel like a *message*, not a *result*.

---

### 2. No Visual Separation Between Turns

**Location:** `ai.c:3655-3670`

In interactive mode, the loop goes: prompt → user input → tool calls → response → back to prompt. There is no visual break between turns. After many tool calls and a response, the next prompt appears immediately, making it hard to scan where one turn ends and the next begins.

**Fix:** Print a subtle separator line between turns (e.g., a dim dotted line or just extra whitespace with a session marker).

---

### 3. Prompt Header Lacks Context

**Location:** `ai.c:3671-3680`

```c
printf("%s%s%s%s %s%s%s%s  %s%s%s%s%s%s%s%s\n",
       CL_MAGENTA CL_BOLD, "ai", CL_MAGENTA, " ",
       CL_DIM, "[", CL_DIM, model[0] ? model : "unknown", CL_DIM, "]",
       CL_DIM, "  ",
       g_auto_approve ? CL_GREEN "auto-approve" : CL_RED "confirm",
       CL_DIM, " mode");
const char *prompt_str = ...;
```

The prompt header shows model name and approval mode, but not:
- Session ID (useful for `:session` commands)
- Turn number (useful for tracking progress)
- Active tool count (how many tools are registered)
- Remaining context budget indicator

**Fix:** Expand the header to include session ID, turn counter, and tool count. Use a more compact layout.

---

### 4. Startup Banner is Plain

**Location:** `ai.c:3646-3648`

```c
printf("\033[1;36mai\033[0m  \033[2m%s\033[0m\n", model);
printf("\033[2m:help · ESC to interrupt · Shift-Tab to disable auto-approve · :btw <msg> to inject a note mid-task\033[0m\n\n");
```

Just two plain lines. No visual identity.

**Fix:** Add a small decorative element (e.g., a thin accent line or a styled header), show session ID if resuming, and keep the help line.

---

### 5. Tool Box Could Be More Information-Dense

**Location:** `ai.c:103-145`

The current `print_tool_box` has:
- A full bordered box with corner characters
- Status in the header (ok/error/info)
- Content in dim text with vertical bar prefix

The box is visually heavy for simple "ok" results and the status text ("ok") is redundant when the color already conveys success.

**Fix:** Keep the box for complex results but simplify the header. Use icons (●, ✕, ◌) instead of text for status. Remove redundant "ok" text.

---

### 6. Error/Warning Boxes Could Be More Distinct

**Location:** `ai.c:148-175`

Currently use red color but same box format as tool results. The warning is visually buried among tool boxes.

**Fix:** Use a slightly different border style or add a "!" icon to make errors immediately scannable.

---

## Proposed Changes (in priority order)

### Change 1: New `print_response_box` function

```c
/* Print the LLM's final response — distinct from tool call boxes.
 * Renders as a clean chat-style message with a left accent bar
 * and the model name as a subtle header. */
static void print_response_box(const char *model_name, const char *content)
{
    printf("\n");

    /* Model name header — subtle, not boxed */
    printf("%s%s %s %s%s  %s%s%s\n",
           CL_DIM, CL_MAGENTA, "◆", CL_DIM, model_name ? model_name : "model",
           CL_DIM, "─────────────────────────────────────────────────────────────",
           CL_RESET);

    /* Content — rendered markdown, full width, no box */
    if (content) {
        const char *line = content;
        int lines = 0;
        const int max_lines = 100;
        while (*line && lines < max_lines) {
            const char *nl = strchr(line, '\n');
            if (!nl) {
                printf("  %s%s%s\n", CL_RESET, line, CL_DIM);
                break;
            }
            int len = (int)(nl - line);
            printf("  %.*s%s\n", len, line, CL_DIM);
            line = nl + 1;
            lines++;
        }
        if (*line && lines >= max_lines) {
            printf("  ... (%zu more lines)\n", strlen(line));
        }
    }

    printf("%s%s%s\n", CL_DIM, "─", CL_RESET);
    printf("\n");
}
```

Then replace the response rendering at `ai.c:4775-4782`:

```c
/* Old: */
char box_name[256];
snprintf(box_name, sizeof(box_name), "💬 %s",
         model[0] ? model : "model");
if (rendered_output && *rendered_output) {
    print_tool_box(box_name, "response", rendered_output);
    free(rendered_output);
} else {
    print_tool_box(box_name, "response", unescaped_content);
}

/* New: */
if (rendered_output && *rendered_output) {
    print_response_box(model, rendered_output);
    free(rendered_output);
} else {
    print_response_box(model, unescaped_content);
}
```

### Change 2: Enhanced Prompt Header

Replace the current header at `ai.c:3671-3680` with:

```c
/* ── Prompt header ── */
printf("%s%s%s%s %s%s%s%s  %s%s%s%s%s%s%s%s%s%s%s%s\n",
       CL_MAGENTA CL_BOLD, "ai", CL_MAGENTA, " ",
       CL_DIM, "[", CL_DIM, model[0] ? model : "unknown", CL_DIM, "]",
       CL_DIM, "  ",
       g_auto_approve ? CL_GREEN "auto" : CL_YELLOW "confirm",
       CL_DIM, " mode  ",
       current_session_id[0] ? CL_DIM : "",
       current_session_id[0] ? "sess:" : "",
       current_session_id[0] ? CL_RESET : "",
       CL_DIM, current_session_id[0] ? current_session_id : "-",
       CL_DIM, "  ",
       CL_RESET);
```

Track a turn counter:

```c
/* In the while loop, at the top, before the prompt: */
static int turn_count = 0;
turn_count++;
```

And include it in the header.

### Change 3: Turn Separator

After the response is printed and before the next prompt, add:

```c
printf("%s%s%s\n\n", CL_DIM, "─", CL_RESET);
```

This creates a subtle visual break between conversation turns.

### Change 4: Improved Startup Banner

```c
printf("\n");
printf("%s%s%s%s\n",
       CL_MAGENTA, "╭──╮", CL_DIM, " ai — autonomous coding agent");
printf("%s│ %s │%s  %s%s%s  %s%s%s\n\n",
       CL_MAGENTA, "ai", CL_DIM,
       CL_CYAN CL_BOLD, model[0] ? model : "unknown model", CL_DIM,
       current_session_id[0] ? CL_GREEN "resuming" : CL_DIM "new session",
       CL_RESET, CL_RESET);
printf("%s:help · ESC to interrupt · Shift-Tab to toggle auto-approve · :btw <msg> to inject a note mid-task%s\n\n",
       CL_DIM, CL_RESET);
```

### Change 5: Simplified Tool Box Header

Update `print_tool_box` to use icons instead of text status:

```c
static void print_tool_box(const char *name, const char *status, const char *content)
{
    const char *hc = (status && strncmp(status, "error", 5) == 0) ? CL_RED
                  : (status && strcmp(status, "ok") == 0)       ? CL_GREEN
                  : (status && strcmp(status, "info") == 0)      ? CL_CYAN
                  : CL_DIM;

    const char *icon = (status && strncmp(status, "error", 5) == 0) ? "✕"
                     : (status && strcmp(status, "ok") == 0)        ? "●"
                     : (status && strcmp(status, "info") == 0)       ? "◌"
                     : "·";

    printf("\n");
    printf("%s%s%s%s %s %s %s%s%s%s\n",
           CL_DIM, BTLN, hc, BTHR, hc, icon, name,
           status ? (status && strncmp(status, "error", 5) == 0) ? "" : "" : "",
           CL_DIM, BTRN, CL_RESET);
    /* ... content stays the same ... */
```

Actually, simpler — just replace the status text with an icon:

```c
    printf("%s%s%s%s %s %s %s %s%s%s%s\n",
           CL_DIM, BTLN, hc, BTHR, hc, icon, name,
           (status && strncmp(status, "error", 5) != 0 &&
            status && strcmp(status, "ok") != 0 &&
            status && strcmp(status, "info") != 0) ? status : "",
           CL_DIM, BTRN, CL_RESET);
```

### Change 6: Distinct Error Box

Add a `!` icon to error boxes:

```c
static void print_warning_box(const char *title, const char *body)
{
    printf("\n%s%s%s%s %s %s %s%s\n",
           CL_RED CL_BOLD, BTLN, CL_RED, BTHR,
           CL_RED CL_BOLD, "⊘", CL_RED, title, BTHR, CL_RESET);
    /* ... rest stays the same ... */
```

---

## Summary of Design Principles

| Element | Current | Proposed |
|---------|---------|----------|
| LLM Response | Same box as tools | Distinct chat-style message |
| Tool Result | Full box with text status | Box with icon status |
| Error | Red box (same shape) | Red box with ⊘ icon |
| Prompt | Model + mode | Model + mode + session + turn |
| Turn Separator | None | Subtle divider line |
| Startup | Plain text | Styled banner with session info |
| Response Header | "💬 model_name" in box | Thin accent line, not boxed |

The core philosophy: **the LLM's response should feel like a conversation, not a data dump.** Tool calls are system events; the model's answer is the human-facing output. They deserve different visual treatment.
