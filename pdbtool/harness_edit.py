import re
p = "/home/dzyla/Code/ai-buddy/ai.c"
s = open(p, encoding="latin-1").read()
edits = []

# EDIT 1: add productivity-watchdog globals after g_state_status (NO duplicate helper -
# reuse the existing tool_is_readonly() used for plan gating, defined later in file)
anchor1 = "static int  g_state_status = 0;      /* 1 if the LAST tool call erred (shared with the header) */"
assert s.count(anchor1) == 1, "anchor1 count=%d" % s.count(anchor1)
add1 = anchor1 + "\n\n"
add1 += "/* ------- Small-model productivity watchdog -------\n"
add1 += "   A small local model can burn its entire output budget on one giant `think`\n"
add1 += "   block (or a long chain of `think` calls) and end the task having produced\n"
add1 += "   NO artifact. We cap how much of a single think we keep and count consecutive\n"
add1 += "   think-without-action loops; crossing the limit forces a concrete-action nudge.\n"
add1 += "   'Productive' = any tool that is NOT read-only (see tool_is_readonly()). */\n"
add1 += "static int g_think_since_action = 0; /* consecutive think/read-only loops since last productive action */\n"
add1 += "static int g_think_max_chars = 2200; /* INFER_THINK_MAX_CHARS: cap a single think's reasoning length */\n"
s = s.replace(anchor1, add1, 1)
edits.append("EDIT1 globals: ok")

# EDIT 2: think handler rewrite - find-based
start = s.find("if (reasoning) {")
assert start != -1 and s.count("if (reasoning) {") == 1, "find if(reasoning) start=%d count=%d" % (start, s.count("if (reasoning) {"))
j = s.find("tool_output = strdup", start)
assert j != -1, "no strdup after reasoning"
semi = s.index(";", j)
end = semi + 1
new2 = (
"if (reasoning) {\n"
"                                   size_t rlen = strlen(reasoning);\n"
"                                   g_think_since_action++;\n"
"                                   if (rlen > (size_t)g_think_max_chars) reasoning[g_think_max_chars] = '\\0';\n"
"                                   add_turn_item(ITEM_THINKING, \"thinking\", NULL, reasoning, 0, 0, 0, 0);\n"
"                                   if (!quiet_mode) {\n"
"                                       print_think_box(reasoning);\n"
"                                       fflush(stderr);\n"
"                                   }\n"
"                                   char think_out[900];\n"
"                                   int act_limit = 3;\n"
"                                   const char *tal = getenv(\"INFER_THINK_ACTION_LIMIT\");\n"
"                                   if (tal && atoi(tal) > 0) act_limit = atoi(tal);\n"
"                                   if (rlen > (size_t)g_think_max_chars) {\n"
"                                       snprintf(think_out, sizeof(think_out),\n"
"                                           \"Error: Your 'think' reasoning was very long (%zu chars) so it was cut to %d chars to save \"\n"
"                                           \"budget. Long think blocks are the #1 reason small local models stall and never produce output. \"\n"
"                                           \"Keep 'think' to a MAXIMUM of 3 short sentences. Do NOT re-type your plan, code, or analysis inside \"\n"
"                                           \"thinking. Take your next concrete action with an ACTION tool (write_file to write your code, or \"\n"
"                                           \"execute_command to build/run) instead of thinking more.\", (unsigned long)rlen, g_think_max_chars);\n"
"                                       tool_output = strdup(think_out);\n"
"                                   } else if (g_think_since_action >= act_limit) {\n"
"                                       snprintf(think_out, sizeof(think_out),\n"
"                                           \"Error: You have called 'think' %d times in a row without taking any concrete action \"\n"
"                                           \"(writing a file or running a command). You are not making progress. \"\n"
"                                           \"STOP thinking. Take ONE concrete action NOW with write_file (write your code) or execute_command \"\n"
"                                           \"(build/run). You may call 'think' again only AFTER you have acted.\", g_think_since_action);\n"
"                                       tool_output = strdup(think_out);\n"
"                                   } else {\n"
"                                       tool_output = strdup(\"{\\\"ok\\\":true}\");\n"
"                                   }\n"
"                                   free(reasoning);\n"
"}"
)
s = s[:start] + new2 + s[end:]
edits.append("EDIT2 think handler: ok")

# EDIT 3: reset watchdog on productive tool (reuse existing tool_is_readonly)
anchor3 = "g_state_status = is_err ? 1 : 0;"
assert s.count(anchor3) == 1, "anchor3 count=%d" % s.count(anchor3)
s = s.replace(anchor3,
    "g_state_status = is_err ? 1 : 0;\n"
    "                                  if (!tool_is_readonly(unescaped_name)) g_think_since_action = 0;", 1)
edits.append("EDIT3 watchdog reset: ok")

out = p + ".new"
open(out, "w", encoding="latin-1").write(s)
print("\n".join(edits))
print("bytes written:", len(s), "->", out)
