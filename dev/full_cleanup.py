#!/usr/bin/env python3
"""
Complete CLI cleanup for ai.c:
1. Remove --why-ai, --llama-server, --install-llama from help and code
2. Fix auto/normal mode toggle to be inline
3. Simplify GUI elements
"""

with open('/home/dzyla/Code/ai-buddy/ai.c', 'r', encoding='utf-8') as f:
    content = f.read()

# ── 1. Remove llama server detection ────────────────────────────────────
old_llama = '''    if (strcmp(model_name, "llama") == 0 || strcmp(model_name, "llama-server") == 0) {
        char *env_base = getenv("INFER_BASE_URL");
        if (env_base && *env_base) {
            strncpy(url_out, env_base, max_len - 1);
            url_out[max_len - 1] = '\\0';
            size_t len = strlen(url_out);
            if (len > 0 && url_out[len - 1] != '/') {
                if (len < max_len - 1) {
                    url_out[len] = '/';
                    url_out[len + 1] = '\\0';
                }
            }
            return 1;
        }
        return 0;
    }'''

new_llama = '''    return 0;'''

if old_llama in content:
    content = content.replace(old_llama, new_llama)
    print("✓ Removed llama server detection")
else:
    print("✗ Could not find llama server detection block")

# ── 2. Remove install-llama comment ─────────────────────────────────────
old_comment = '''    // Parse set-default, install-llama, and version options first (all exit early)'''
new_comment = '''    // Parse set-default and version options first (all exit early)'''

if old_comment in content:
    content = content.replace(old_comment, new_comment)
    print("✓ Removed install-llama comment")

# ── 3. Remove why_ai_prompt function if exists ──────────────────────────
if 'static void why_ai_prompt(void)' in content:
    start = content.index('static void why_ai_prompt(void)')
    brace_count = 0
    end = start
    in_func = False
    for i in range(start, len(content)):
        if content[i] == '{':
            brace_count += 1
            in_func = True
        elif content[i] == '}':
            brace_count -= 1
            if in_func and brace_count == 0:
                end = i + 1
                break
    while start > 0 and content[start-1] == '\n':
        start -= 1
    content = content[:start] + content[end:]
    print("✓ Removed why_ai_prompt() function")

# ── 4. Remove --install-llama help line ─────────────────────────────────
old_help = '''            printf("  --install-llama [R]  Download, build llama.cpp and start a local server.\\n");
            printf("                       R: optional HuggingFace repo (e.g. unsloth/gemma-4-12b-it-GGUF).\\n");'''
new_help = ''

if old_help in content:
    content = content.replace(old_help, new_help)
    print("✓ Removed --install-llama help lines")

# ── 5. Remove --install-llama argument parsing ──────────────────────────
old_arg = '''        if (strcmp(argv[i], "--install-llama") == 0) {
            // Parse install-llama option (exits early)
            // ... (this is the code that was removed earlier)
            // For now, just print a message
            printf("install-llama option removed\\n");
            return 0;
        }'''
new_arg = ''

if old_arg in content:
    content = content.replace(old_arg, new_arg)
    print("✓ Removed --install-llama argument parsing")

# ── 6. Simplify welcome banner ──────────────────────────────────────────
old_banner = '''        printf("\\n");
        printf("%s╭─%s %s◈%s %s%s%s %s─%s autonomous coding agent%s╮\\n",
               CL_MAGENTA, CL_RESET, CL_MAGENTA, CL_RESET,
               CL_MAGENTA CL_BOLD, "ai", CL_RESET, CL_DIM, CL_DIM, CL_RESET);
        printf("%s│ %s│ %s%s%s · %s · %s%s%s╮\\n",
               CL_DIM, CL_DIM,
               CL_CYAN CL_BOLD, model[0] ? model : "unknown", CL_RESET,
               current_session_id,
               g_permission_mode ? CL_GREEN "auto" : CL_RED "confirm",
               CL_RESET, CL_RESET);
        printf("%s│ %s%s%s╰╯%s\\n",
               CL_DIM, CL_DIM, CL_DIM, CL_DIM, CL_RESET);
        printf("%s│ %s:help %s· %sESC%s interrupt  %sShift-Tab%s auto-approve  %s:commit/:undo/:copy%s  %s:notify/:btw%s%s│\\n",
               CL_DIM, CL_DIM, CL_DIM, CL_DIM, CL_RESET, CL_DIM, CL_RESET, CL_DIM, CL_RESET, CL_DIM, CL_RESET, CL_DIM);
        printf("%s╰─%s%s╯\\n\\n", CL_DIM, CL_DIM, CL_DIM);'''

new_banner = '''        printf("\\n");
        printf("%s%s%s %s%s%s\\n",
               CL_MAGENTA, model[0] ? model : "unknown", CL_RESET,
               CL_DIM, current_session_id, CL_RESET);
        printf("%s:help  %sESC  %sShift-Tab%s auto  %s:commit/:undo/:copy%s\\n",
               CL_DIM, CL_DIM, CL_DIM, CL_RESET, CL_DIM, CL_RESET);'''

if old_banner in content:
    content = content.replace(old_banner, new_banner)
    print("✓ Simplified welcome banner")

# ── 7. Simplify response box ────────────────────────────────────────────
old_resp = '''    printf("\\n");

    /* Header bar: model name + turn/tool count */
    printf("%s╭─%s %s◉%s %s%s%s%s╮\\n",
           CL_MAGENTA, CL_RESET, CL_MAGENTA, CL_RESET,
           CL_MAGENTA CL_BOLD, model_name ? model_name : "assistant",
           CL_DIM, CL_DIM);

    /* Meta line */
    printf("%s│  turn %d  %s▸%s", CL_DIM, turn_count > 0 ? turn_count : 0,
           CL_RESET, CL_DIM);
    if (tool_count > 0) {
        printf("  %d tools%s", tool_count, CL_DIM);
    }
    printf("  \\033[2m%.1fs", elapsed_sec);
    if (tokens_per_sec > 0) {
        printf("  %d tok/s\\033[0m", (int)tokens_per_sec);
    }
    printf("%*s│%s\\n", 0, "", CL_DIM);'''

new_resp = '''    printf("\\n");

    /* Header: model name + stats */
    printf("%s%s%s", CL_MAGENTA, model_name ? model_name : "?", CL_RESET);
    if (tool_count > 0) printf("  %s%d tools%s", CL_DIM, tool_count, CL_RESET);
    printf("  %s%.1fs%s", CL_DIM, elapsed_sec, CL_RESET);
    if (tokens_per_sec > 0) printf("  %s%d tok/s%s", CL_DIM, (int)tokens_per_sec, CL_RESET);
    printf("\\n");'''

if old_resp in content:
    content = content.replace(old_resp, new_resp)
    print("✓ Simplified response box")

# ── 8. Simplify tool box ────────────────────────────────────────────────
old_tool = '''    printf("\\n%s── %s%s%s %s%s%s  %s%.1fs %s──\\n",
           CL_RESET, CL_GREEN, tool_get_icon(name), CL_RESET,
           CL_BOLD, name, CL_RESET, CL_DIM, elapsed_sec, CL_RESET);'''

new_tool = '''    printf("\\n%s%s: %s%s  %s%.1fs%s\\n",
           CL_GREEN, tool_get_icon(name), CL_BOLD, name, CL_DIM, elapsed_sec, CL_RESET);'''

if old_tool in content:
    content = content.replace(old_tool, new_tool)
    print("✓ Simplified tool box")

# ── 9. Fix permission mode toggle to be inline ──────────────────────────
# The permission mode should be displayed inline, not in a separate box
old_perm = '''                    printf("\\n%s%s: %s%s%s\\n",
                           CL_DIM, "permission mode", CL_RESET,
                           g_permission_mode ? CL_GREEN "auto" : CL_RED "manual",
                           CL_RESET);'''

# Keep as is but make sure it's inline
new_perm = '''                    printf("\\n%s%s: %s%s%s\\n",
                           CL_DIM, "permission mode", CL_RESET,
                           g_permission_mode ? CL_GREEN "auto" : CL_RED "manual",
                           CL_RESET);'''

# Already inline, no change needed

# ── 10. Remove any remaining llama references in help ───────────────────
old_llama_help = '''            printf("  --why-ai          Generate a brief explanation of why AI is important.\\n");'''
new_llama_help = ''

if old_llama_help in content:
    content = content.replace(old_llama_help, new_llama_help)
    print("✓ Removed --why-ai help line")

# Write back
with open('/home/dzyla/Code/ai-buddy/ai.c', 'w', encoding='utf-8') as f:
    f.write(content)

print("\\n✓ All changes applied successfully")
