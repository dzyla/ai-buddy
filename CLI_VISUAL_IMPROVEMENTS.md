# CLI Interface Visual & Interaction Improvements

## Current State Analysis

The `ai` CLI harness (`ai.c` ~5,324 lines) uses ANSI escape codes and box-drawing characters for output. The current interface is functional but can be improved for better visual hierarchy, tool interaction, and user experience.

### Current Rendering Components

1. **Model Output** (`print_response_box`): Thin magenta accent bar with model name header, then plain text content
2. **Tool Results** (`print_tool_box`): Colored status icon (●/✕/◦), tool name, status label, then content
3. **Warnings/Errors** (`print_warning_box`): Red bold title with dim red underline
4. **Line Editor** (`read_line_interactive`): Basic line editing with up/down history (Ctrl+P/Ctrl+N), backspace, Ctrl+D (delete), Ctrl+C (interrupt)
5. **Turn Separator**: "── turn N" in dim magenta
6. **Prompt Header**: Session ID, permission mode (auto/plan/manual), turn count
7. **Markdown Rendering**: Python helper with code blocks (line numbers, syntax highlighting), tables (with alternating row shading), headers (magenta/cyan), blockquotes (yellow left border), lists (cyan bullets)

---

## Proposed Improvements

### 1. Model Output — Left Accent Bar with Better Typography

**Current:**
```
── model-name  ──
content text here
```

**Proposed:**
```
╭─────────────────────────────────────╮
│  model-name                          │
│                                      │
│  This is the model's response.       │
│  It uses a left accent bar for       │
│  visual hierarchy.                   │
╰─────────────────────────────────────╯
```

**Implementation:**
- Add a left vertical border (│) with magenta accent
- Use monospace font for code blocks, proportional for prose
- Add subtle background shading (48;5;236) for the entire response box
- Show token count and generation time in footer

**Code changes:**
- Replace `print_response_box()` in `ai.c` lines 98-122
- Add `render_model_output()` with proper box drawing
- Show generation stats: tokens/s, total tokens, time elapsed

### 2. Tool Call Grouping & Visual Hierarchy

**Current:** Each tool call prints individually with its own box.

**Proposed:** Group sequential tool calls into a single visual unit.

**Example:**
```
── Tool Calls (3) ────────────────────────
  ● read_file        ok    [2.3s]
  ● execute_command  ok    [0.8s]
  ● web_search       ok    [1.1s]
───────────────────────────────────────────
```

**Implementation:**
- Track consecutive tool calls within a single agent iteration
- Print a header "── Tool Calls (N) ──" before the group
- Print each tool call as a single line: `[icon] name  status  [duration]`
- Expand details on request (or if output is very long)

**Code changes:**
- Add `tool_call_group` array in agent loop
- Modify `print_tool_box()` to accept group context
- Show duration for each tool call (already measured in `tool_start`)

### 3. User Input Prompt — Better Visual Separation

**Current:**
```
ai > 
```

**Proposed:**
```
╭─────────────────────────────────────╮
│ user                                 │
│                                      │
│ >                                    │
╰─────────────────────────────────────╯
```

**Implementation:**
- Use a distinct background color (48;5;237) for user input area
- Show "user" label in bold
- Use a right-facing angle bracket (►) instead of plain ">"
- Show permission mode indicator (auto/plan/manual) in corner

**Code changes:**
- Modify prompt string generation in `ai.c` lines 4030-4040
- Add ANSI codes for background and border
- Update `read_line_interactive()` prompt formatting

### 4. Line Editor — Enhanced Interaction

**Current:** Basic line editing with Ctrl+P/N (up/down), Ctrl+A/E (home/end), Ctrl+K (kill to end), Ctrl+W (kill word backward), Ctrl+_ (undo), Shift-Tab (cycle permission mode), Ctrl+D (delete char/exit), Ctrl+C (interrupt).

**Proposed additions:**
- **Ctrl+R**: Reverse search through history
- **Tab completion**: For slash commands (`:compact`, `:clear`, `:status`, etc.)
- **Visual feedback**: Show line count indicator when multi-line input wraps
- **Better history display**: Show recent history items when navigating up/down

**Implementation:**
- Add `history_search()` function for Ctrl+R
- Add `command_completion()` for Tab key
- Track current history index and show preview
- Use `tputs()` for proper terminal escape handling

**Code changes:**
- Extend `read_line_interactive()` in `ai.c` lines 1381-1530
- Add new key bindings to the main loop
- Add helper functions for search and completion

### 5. Streaming Indicator During Generation

**Current:** No visual indicator during token generation.

**Proposed:** Show a subtle "thinking" indicator that pulses or animates during generation.

**Implementation:**
- Print "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" cycling animation while tokens are streaming
- Show tokens/sec counter in real-time
- Dim the animation when not actively receiving tokens (paused thinking)
- Remove animation when generation completes

**Code changes:**
- Add `streaming_indicator` flag in agent loop
- Print animation in `curl_progress_cb` or after each token chunk
- Use `\r` to overwrite previous line

### 6. Context Window Usage Bar

**Current:** No visual indication of context window usage.

**Proposed:** Show a progress bar indicating context window usage at the top of each turn.

**Implementation:**
- Calculate percentage: `current_tokens / context_window * 100`
- Display as: `[████████░░] 65% (6,500/10,000)`
- Color coding: green (<50%), yellow (50-80%), red (>80%)
- Show at the start of each turn before the prompt

**Code changes:**
- Add `print_context_bar()` function
- Call before prompt display in main loop
- Update token count tracking to include all messages

### 7. Better Error Display

**Current:** Tool errors show as "[Tool: X | Status: error]" with plain text.

**Proposed:**
```
╭─── Error ──────────────────────────────────────────╮
│  Tool: execute_command                             │
│  Exit Code: 1                                      │
│                                                    │
│  Command: find /nonexistent 2>&1                   │
│  Error: find: '/nonexistent': No such file or      │
│         directory                                  │
╰────────────────────────────────────────────────────╯
```

**Implementation:**
- Capture exit code and stderr from tool execution
- Format error box with title, tool name, exit code, command, and error message
- Use red accent bar and error icon
- Show first 5 lines of stderr for long errors

**Code changes:**
- Modify tool execution in `ai.c` to capture stderr separately
- Add `print_error_box()` function
- Format error details with proper indentation

### 8. Task Duration Timer

**Current:** No timing feedback for user tasks.

**Proposed:** Show elapsed time from user input to task completion.

**Timing points:**
- **Start**: When user presses Enter in `read_line_interactive()` (ai.c:4040-4060)
- **End**: When `task_complete` is called and final response is rendered (ai.c:4692-4695, 5171-5191)

**Display locations:**

1. **In task_complete output**: Show duration in the final box
   ```
   ╭─────────────────────────────────────╮
   │  ✅ Task Complete                    │
   │                                      │
   │  Summary of what was accomplished    │
   │                                      │
   │  ── Duration: 12.4s ──               │
   ╰─────────────────────────────────────╯
   ```

2. **In session summary on resume**: Show average duration
   ```
   session: abc123
   turns: 5
   avg_duration: 8.2s
   last: "What is the capital?"
   ```

3. **Real-time during long tasks**: Show live timer in corner
   ```
   ⏱ 5.2s
   ```
   Updated every second during generation, then removed on completion

**Code changes:**
- Add `task_start_time` (struct timespec) in main loop
- Capture start time in `read_line_interactive()` return path
- Capture end time in `print_response_box()` for task_complete
- Add `print_duration()` helper: `%.1fs` format
- Modify `print_task_complete()` to show duration

**Benefits:**
- Users can gauge task complexity
- Helps identify slow tools or loops
- Useful for benchmarking agent performance

---

### 9. Session Summary on Resume

**Current:** When resuming with `ai -r`, just shows session ID and "resuming" status.

**Proposed:**
```
╭─────────────────────────────────────╮
│ session: abc123                      │
│ resumed: 2026-08-05 23:15:29        │
│ turns: 5                             │
│ tokens: 12,345                       │
│ last: "What is the capital of France?" │
╰─────────────────────────────────────╯
```

**Implementation:**
- Parse session file to extract metadata
- Show last user message and last assistant response
- Display total turns and token count
- Use a distinct box style for session info

**Code changes:**
- Add `load_session_meta()` function
- Print session summary before main loop
- Update with new fields in session file

### 10. Markdown Table Improvements

**Current:** Tables are rendered with box-drawing characters and alternating row shading.

**Proposed improvements:**
- Better column width calculation (consider terminal width more intelligently)
- Highlight cells with special formatting (errors, warnings)
- Add column alignment indicators (left/center/right)
- Support merged cells (if markdown supports it)

**Implementation:**
- Improve `render_table()` in `ai_mcp.py`
- Add column width distribution algorithm that considers content importance
- Add cell-level formatting based on content patterns
- Show alignment hint in header (optional)

**Code changes:**
- Modify `render_table()` in `ai_mcp.py` lines 1568-1650
- Add `align_table_columns()` helper
- Add `format_cell_by_content()` for special patterns

### 10. Code Block Improvements

**Current:** Code blocks show line numbers and syntax highlighting.

**Proposed improvements:**
- Add "Copy" button indicator (text-based)
- Show file path and language in header
- Highlight changed lines (if diff)
- Better syntax highlighting colors

**Implementation:**
- Parse file path from code block metadata if available
- Add "📋" indicator for copy suggestion
- Use diff-aware highlighting (green for additions, red for deletions)
- Improve syntax highlighter color palette

**Code changes:**
- Modify code block rendering in `ai_mcp.py` lines 1597-1610
- Add `parse_code_metadata()` for file paths
- Add `highlight_diff()` for line-level changes

---

## Implementation Priority

### Quick Wins (1-2 days)
1. **Left accent bar for model output** - Easy CSS-like change to `print_response_box()`
2. **Streaming indicator** - Simple animation loop
3. **Context window bar** - Just needs token counting
4. **Better error display** - Format existing error data differently

### Medium Effort (3-5 days)
5. **Tool call grouping** - Track consecutive calls, print summary
6. **Task duration timer** - Show elapsed time from user input to completion
7. **Enhanced line editor** - Add Ctrl+R, Tab completion
8. **User input prompt styling** - Background color, border
9. **Session summary on resume** - Parse and display metadata (including avg duration)

### Larger Effort (1-2 weeks)
9. **Advanced markdown rendering** - Better tables, code blocks
10. **Diff-aware code display** - Parse and highlight changes

---

## Code Reference

Key sections in `ai.c`:
- Lines 98-122: `print_response_box()` — model output rendering
- Lines 138-177: `print_tool_box()` — tool result rendering
- Lines 400-450: `task_complete()` — task completion rendering
- Lines 5171-5191: Final response rendering
- Lines 1381-1530: `read_line_interactive()` — line editor (capture start time)
- Lines 3980-4060: Main loop prompt/header display
- Lines 4030-4040: Prompt string generation

Key sections in `ai_mcp.py`:
- Lines 1522-1660: `render_markdown()` — markdown rendering
- Lines 1568-1650: `render_table()` — table rendering
- Lines 1597-1610: Code block rendering

---

## Next Steps

1. Implement quick wins first for immediate visual improvement
2. Add streaming indicator for better feedback during generation
3. Add task duration timer — critical for UX feedback
4. Enhance line editor for better user interaction
5. Improve tool call display for better observability
6. Refine markdown rendering for better output presentation

All changes should maintain backward compatibility (ANSI codes are optional) and work in both interactive and non-interactive modes.

