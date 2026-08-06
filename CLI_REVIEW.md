# AI-Buddy CLI Harness — Review & Improvement Plan

## Current Architecture

The harness (`ai.c`, ~5.6k lines) is a monolithic C program with:

- **Token streaming** via `print_token_stream`
- **Tool call display** via `print_tool_box`
- **Response rendering** via `print_response_box`
- **User input** via `read_line_interactive` + `lineed_redraw`
- **Markdown rendering** via `render_markdown`

Everything lives in one file. The color palette is limited to 10 ANSI codes. Box drawing uses basic `│─╭╰╮` characters.

---

## Findings

### 1. Prompt Bar — Functional but Flat
**Location:** `ai.c:4131-4155`

```
magenta| model  session  ▸ auto
dim    ╰──────────╯
```

**Issues:**
- Only 3 data points shown (model, session ID, permission mode)
- Session ID is opaque (`sess_1691234567`) — no way to identify it
- No turn counter shown in prompt (it increments but isn't displayed)
- No model context (which family, context window size)
- Permission mode text is just color — no visual icon

### 2. User Message Box — Adequate
**Location:** `ai.c:114-160`

Uses a `You ▸` header with word-wrapped content. Functional but:
- "You" header is generic — could show timestamp or turn number
- No visual separator between user input and tool output
- Word-wrap logic is simplistic (breaks at space only, no soft-wrap)

### 3. Response Box — Decent but Dated
**Location:** `ai.c:162-215`

Shows model name, turn count, tool count. Good info density. Issues:
- No streaming animation/indicator
- Turn/tool count in footer is fine but could be more prominent
- No model context (parameters, context usage)

### 4. Tool Box — Basic
**Location:** `ai.c:217-280`

Shows tool name, status (ok/error/info), truncated content (50 lines). Issues:
- No distinction between tool types (shell, file, fetch, search, etc.)
- Status is just a word — could use icons/colors better
- Content truncation is blunt — no "click to expand" hint (even in text)
- No timing information (how long did the tool take?)

### 5. Color Palette — Very Limited
**Location:** `ai.c:40-50`

```c
#define CL_RESET   "\033[0m"
#define CL_BOLD    "\033[1m"
#define CL_DIM     "\033[2m"
#define CL_MAGENTA "\033[35m"
#define CL_CYAN    "\033[36m"
#define CL_GREEN   "\033[32m"
#define CL_RED     "\033[31m"
#define CL_YELLOW  "\033[33m"
#define CL_BLUE    "\033[34m"
#define CL_WHITE   "\033[37m"
#define CL_BG_MAG  "\033[45m"
```

Only 10 codes. Missing:
- Bright/intense variants (21, 22, 24)
- Underline (4, 24)
- No extended 256-color or RGB support
- No system color awareness (light/dark terminal detection)

### 6. Box Drawing — Basic ASCII
Uses `╭─╮│╰╯` — these work but look dated compared to modern terminals.

### 7. No Table Rendering
Markdown tables in output are rendered as raw text. No special handling for tabular data.

### 8. No Thinking/Reasoning Display
When the model uses `think` tool, the output is mixed with tool calls. No dedicated "thinking" display that separates reasoning from action.

### 9. Input UX — Good Foundation
`read_line_interactive` has:
- Full readline-style editing
- Bracketed paste
- Shift-Tab to cycle permission mode
- History support

Could be improved with:
- Visual indicator for multi-line input
- Better keybinding hints in prompt
- Paste visualization (show how many lines were pasted)

---

## Recommended Improvements

### Priority 1: Quick Wins (High Impact, Low Effort)

#### 1.1 Enhanced Prompt Bar

Replace the simple prompt with a richer header:

```
magenta  ai  dim│  cyan●  cyan gemma-4-flash  dim·  turn 3  dim·  session 2m ago  dim│  green auto
dim      ─────────────────────────────────────────────────────────────────────────────────────────────
```

Changes:
- Add "ai" brand prefix with bold
- Show model in cyan (distinct from session)
- Show turn counter
- Show relative session time (not just ID)
- Show permission mode with icon: `●auto` / `◐plan` / `○manual`
- Add subtle bottom border line

#### 1.2 Extended Color Palette

Add these to the defines:

```c
#define CL_BRIGHT   "\033[22m"   /* Normal intensity */
#define CL_UNDERLINE "\033[4m"
#define CL_DIM2     "\033[22m"   /* Semi-dim for secondary text */
#define CL_ORANGE   "\033[38;5;208m"  /* Extended color for warnings */
#define CL_TEAL     "\033[38;5;37m"   /* Extended color for info */
#define CL_PURPLE   "\033[38;5;135m"  /* Extended color for special */
```

#### 1.3 Tool Box Enhancements

Add tool-type icons:

| Tool Type | Icon | Color |
|-----------|------|-------|
| shell | `⬡` | cyan |
| file | `⌨` | yellow |
| fetch | `◉` | blue |
| web_search | `⌕` | green |
| arXiv/pubmed | `⊹` | purple |
| calendar | `⊡` | teal |
| email | `✉` | orange |
| code | `⟨⟩` | magenta |
| thinking | `◈` | dim white |

Show execution time: `ok · 1.2s`

#### 1.4 Response Box — Streaming Indicator

Add a subtle animation while streaming:

```
magenta  ◉  cyan gemma-4-flash  dim│  thinking...
```

Replace "thinking..." with a subtle animation (spinning `◐◑◒◓` or `⠋⠙⠹⠸⠼⠴`).

### Priority 2: Medium Effort

#### 2.1 Table Rendering

Add a `render_table` function that detects markdown tables and renders them with proper alignment:

```
│ Col1      │ Col2      │ Col3      │
│───────────┼───────────┼───────────│
│ value1    │ value2    │ value3    │
│───────────┼───────────┼───────────│
```

Use double-line separators between header and rows for clarity.

#### 2.2 Thinking Display

When `think` tool is called, show a distinct "thinking" block:

```
dim  ◈  Thinking
dim  ──────────────
dim  [reasoning content in dim text]
dim  ──────────────
```

This visually separates reasoning from the final response, reducing cognitive load.

#### 2.3 Input UX Improvements

- Show keybinding hint in dim text: `dim[Shift-Tab: cycle mode · Enter: send]`
- Show paste indicator when multi-line input detected
- Show line count for pasted content: `dim(3 lines)`

#### 2.4 Session Identification

Make session IDs more human-readable:
- Show relative time: "2m ago", "1h ago"
- Show first few words of first prompt as a label
- Or: show a short hash + context

### Priority 3: Higher Effort

#### 3.1 Model Context Footer

At the bottom of response boxes, show model metadata:
- Context window usage (e.g., "12k/131k tokens")
- Temperature / parameters (if available from API)
- Latency (time to first token, total time)

#### 3.2 Tool Call Timeline

Instead of individual boxes, show a condensed timeline:
```
dim  [⬡ ls] → [⌨ cat file.txt] → [⊹ web_search] → [⟨⟩ code]
```

Click/expand to see details. This reduces visual clutter during long tool chains.

#### 3.3 Multi-line Input Visualization

When user pastes multiple lines, show:
```
magenta ai ▸  [3 lines pasted]
dim     ──────────────
dim     line1
dim     line2
dim     line3
dim     ──────────────
dim     [Enter to send · Ctrl+D to discard]
```

---

## Implementation Plan

### Phase 1: Colors & Prompt (1-2 hours)
1. Add extended color palette (CL_ORANGE, CL_TEAL, etc.)
2. Rewrite `prompt_str` construction with richer display
3. Add turn counter, relative session time, permission icons

### Phase 2: Tool & Response Polish (2-3 hours)
1. Add tool-type icons and execution time to `print_tool_box`
2. Add streaming animation to response header
3. Implement basic table rendering in `render_markdown`

### Phase 3: Advanced UX (3-4 hours)
1. Thinking display with distinct styling
2. Input UX improvements (hints, paste visualization)
3. Tool call timeline (condensed view)

### Phase 4: Model Context (2 hours)
1. Add latency/usage footer to response boxes
2. Show context window usage if available from API response

---

## Code Locations Reference

| Feature | Function | Line |
|---------|----------|------|
| Prompt bar | Main loop prompt construction | 4131-4155 |
| User message | `print_user_message` | 114-160 |
| Response box | `print_response_box` | 162-215 |
| Tool box | `print_tool_box` | 217-280 |
| Token streaming | `print_token_stream` | ~2800-3000 |
| Markdown render | `render_markdown` | ~3400-3800 |
| Input editing | `read_line_interactive` | 1614-1800 |
| Color defines | Top of file | 40-50 |
| Session ID | `current_session_id` | 335 |
| Turn counter | `g_turn_count` | 408 |
| Permission mode | `g_permission_mode` | 405 |

---

## Design Philosophy

The current interface is **functional but flat**. The goal is to add **visual hierarchy** without clutter:

1. **Information density** — Show more useful context (timing, counts, relative IDs)
2. **Visual separation** — Use color, icons, and spacing to distinguish sections
3. **Cognitive load** — Group related info, de-emphasize noise
4. **Consistency** — Use the same icons/colors across all displays
5. **Brevity** — Modern CLIs show less but mean more (think `gh`, `git`, `starship`)

The aesthetic should be: **minimal, information-rich, with subtle animations**. Think `starship.rs` prompt meets `gh` CLI.
