#!/usr/bin/env python3
"""Clean up ai.c CLI: remove why-ai/llama, fix response box, clean GUI."""

with open('/home/dzyla/Code/ai-buddy/ai.c', 'r', encoding='utf-8') as f:
    content = f.read()

changes = 0

# ── 1. Remove "why ai" and "llama" from CLI help ─────────────────────────
# Help section: remove lines containing --why-ai, --llama-server, --install-llama
lines = content.split('\n')
new_lines = []
skip_keywords = ['--why-ai', '--llama-server', '--install-llama']
for line in lines:
    stripped = line.strip()
    skip_any = False
    for kw in skip_keywords:
        if kw in stripped:
            skip_any = True
            break
    if skip_any:
        continue
    new_lines.append(line)
content = '\n'.join(new_lines)
print(f"1. Removed why-ai/llama CLI entries")

# ── 2. Simplify print_response_box ──────────────────────────────────────
old_header = '''    printf("\\n");

    /* Header: model name + meta */
    printf("%s%s%s%s", CL_MAGENTA, model_name ? model_name : "assistant",
           CL_RESET, " ");
    printf("%sturn %d%s", CL_DIM, turn_count > 0 ? turn_count : 0, CL_RESET);
    if (tool_count > 0) {
        printf("  %d tools", tool_count);
    }
    printf("  %s%.1fs", CL_DIM, elapsed_sec);
    if (tokens_per_sec > 0) {
        printf("  %d tok/s", (int)tokens_per_sec);
    }
    printf("\\n%s───\\n", CL_RESET);'''

new_header = '''    printf("\\n");

    /* Header: model name + meta */
    printf("%s%s%s", CL_MAGENTA, model_name ? model_name : "?", CL_RESET);
    if (tool_count > 0) printf("  %s%d tools%s", CL_DIM, tool_count, CL_RESET);
    printf("  %s%.1fs%s", CL_DIM, elapsed_sec, CL_RESET);
    if (tokens_per_sec > 0) printf("  %s%d tok/s%s", CL_DIM, (int)tokens_per_sec, CL_RESET);
    printf("\\n");'''

if old_header in content:
    content = content.replace(old_header, new_header)
    print("2. Simplified response box header (no box border, no turn count)")
else:
    print("2. WARNING: old header not found, skipping")

# ── 3. Simplify print_tool_box ──────────────────────────────────────────
old_tool = '''    printf("\\n%s── %s%s%s %s%s%s  %s%.1fs %s──\\n",
           CL_RESET, CL_GREEN, tool_get_icon(name), CL_RESET,
           CL_BOLD, name, CL_RESET, CL_DIM, elapsed_sec, CL_RESET);'''

new_tool = '''    printf("\\n%s%s: %s%s  %s%.1fs%s\\n",
           CL_GREEN, tool_get_icon(name), CL_BOLD, name, CL_DIM, elapsed_sec, CL_RESET);'''

if old_tool in content:
    content = content.replace(old_tool, new_tool)
    print("3. Simplified tool box header")
else:
    print("3. WARNING: old tool header not found")

# ── 4. Remove the --why-ai function definition (the actual implementation) ──
# Find and remove why_ai_prompt function
if 'static void why_ai_prompt(void)' in content:
    # Find start
    start_idx = content.index('static void why_ai_prompt(void)')
    # Find matching closing brace
    brace_count = 0
    end_idx = start_idx
    in_func = False
    for i in range(start_idx, len(content)):
        if content[i] == '{':
            brace_count += 1
            in_func = True
        elif content[i] == '}':
            brace_count -= 1
            if in_func and brace_count == 0:
                end_idx = i + 1
                break
    # Check what's before - remove leading newlines
    while start_idx > 0 and content[start_idx-1] == '\n':
        start_idx -= 1
    # Also remove preceding blank line if exists
    content = content[:start_idx] + content[end_idx:]
    print("4. Removed why_ai_prompt() function")

# ── 5. Remove llama-related server code in run_chat_loop ────────────────
# Remove the if block that checks for llama
old_llama = '''    if (strcmp(model_name, "llama") == 0 || strcmp(model_name, "llama-server") == 0) {
        is_llama = true;
    } else {
        is_llama = false;
    }'''

if old_llama in content:
    content = content.replace(old_llama, '    is_llama = false;')
    print("5. Cleaned up llama detection in chat loop")

with open('/home/dzyla/Code/ai-buddy/ai.c', 'w', encoding='utf-8') as f:
    f.write(content)
print("\nDone. All changes applied.")
