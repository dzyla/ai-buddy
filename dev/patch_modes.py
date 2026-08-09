#!/usr/bin/env python3
"""Apply mode-gating + present_plan + self-improvement changes to ai.c (byte-preserving)."""
import sys

P = "/home/dzyla/Code/ai-buddy/ai.c"
raw = open(P, "rb").read()
src = raw.decode("latin-1")  # preserves every byte including the stray NUL

def sub(old, new, count=1):
    global src
    n = src.count(old)
    if n != count:
        print(f"ANCHOR FAIL count={n} expected={count}: {old[:70]!r}")
        sys.exit(1)
    src = src.replace(old, new, count)

# ---------- R1: global g_plan_approved ----------
sub("static int  g_permission_mode = 0;",
    r"""static int  g_permission_mode = 0;
static int  g_plan_approved = 0;   /* plan mode: user approved the presented plan */""")

# ---------- R2: helper functions before SYSTEM_PROMPT ----------
helpers = r"""
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
        "skill_create","skill_update","skill_note",
        NULL
    };
    for (int i = 0; mut[i]; i++)
        if (strcmp(name, mut[i]) == 0) return 1;
    return 0;
}

const char *SYSTEM_PROMPT ="""
sub("const char *SYSTEM_PROMPT =", helpers, 1)

# ---------- R3: step limit ----------
sub("int step_limit = (g_continue_until_done || g_permission_mode || !isatty(STDIN_FILENO)) ? 999999 : 30;",
    r"""int step_limit;
            if (g_continue_until_done) step_limit = 999999;
            else if (g_permission_mode == 0 && !isatty(STDIN_FILENO)) step_limit = 999999; /* auto + non-tty = full autonomy */
            else if (!isatty(STDIN_FILENO)) step_limit = 60; /* plan/manual non-tty: finite, won't run away */
            else step_limit = 30;""")

# ---------- R5: reset g_plan_approved at start of each run ----------
sub("int think_count = 0;",
    "int think_count = 0;\n            g_plan_approved = 0;")

# ---------- R4: think cap ----------
sub("                                   think_count++;\n                                   if (think_count > 1) {\n                                       tool_output = strdup(\"Error: You have already called the",
    "                                   think_count++;\n                                   if (think_count > 12) {\n                                       tool_output = strdup(\"Error: You have already called the")

# ---------- R6: execute_command plan/auto/manual gate ----------
sub("} else {\n                                          int approved = (g_permission_mode == 0);\n                                          if (!approved) {",
    r"""} else if (g_permission_mode == 1 && !g_plan_approved) {
                                          tool_output = strdup("Error: [PLAN MODE] You must call present_plan to present your plan and receive approval before executing commands or changing anything. Nothing was changed.");
                                      } else {
                                          int approved = (g_permission_mode == 0 || g_plan_approved); /* auto or approved-plan auto-run; manual prompts below */
                                          if (!approved) {""")

# ---------- R7: present_plan branch before remote_exec ----------
present_plan = r"""
                                } else if (strcmp(unescaped_name, "present_plan") == 0) {
                                    char *plan_txt = json_get_string(unescaped_args, "plan");
                                    if (!plan_txt) plan_txt = json_get_string(unescaped_args, "summary");
                                    if (!plan_txt) plan_txt = strdup(unescaped_args);
                                    int approved = 0;
                                    if (g_permission_mode == 1) {
                                        approved = prompt_user_ok("APPROVE PLAN", plan_txt);
                                        if (approved == -1) approved = 0;
                                    } else {
                                        approved = 1;
                                    }
                                    g_plan_approved = approved ? 1 : 0;
                                    if (approved) {
                                        tool_output = strdup("PLAN APPROVED. You may now proceed to make the proposed changes. Work autonomously until you have another question, then present_plan again.");
                                    } else {
                                        tool_output = strdup("PLAN NOT APPROVED. Revise your plan based on the user's feedback and call present_plan again. Do not make any changes until the plan is approved.");
                                    }
                                    if (plan_txt) free(plan_txt);
                                } else if (strcmp(unescaped_name, "remote_exec") == 0) {"""
sub("} else if (strcmp(unescaped_name, \"remote_exec\") == 0) {", present_plan, 1)

# ---------- R8: generic MCP mutation gate ----------
sub("                                  } else {\n                                      mcp_tool_name = unescaped_name;\n                                  }\n\n                                  /* Show a human-readable line for what the model is doing */",
    r"""                                  } else {
                                      mcp_tool_name = unescaped_name;
                                  }

                                  /* Mode-based approval gate for mutating MCP tools */
                                  if (tool_is_mutating(mcp_tool_name)) {
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

                                  /* Show a human-readable line for what the model is doing */""")

# R8b: label before the display block after free(server_name)
sub("                                  free(server_name);\n                              }\n\n                              if (!tool_output) {",
    "                                  free(server_name);\n                              }\n                              mcp_gated:\n                              if (!tool_output) {")

# ---------- R13: auto-commit gating ----------
sub("if (exit_code == 0 && g_git_commit_enabled) {\n                                                      git_commit(\"command\");",
    "if (exit_code == 0 && g_git_commit_enabled && (g_permission_mode == 0 || g_plan_approved)) {\n                                                      git_commit(\"command\");")

# ---------- R9: CLI flags --plan / --manual ----------
sub("} else if (strcmp(argv[i], \"-c\") == 0 || strcmp(argv[i], \"--continue\") == 0) {\n            g_continue_until_done = 1;",
    r"""} else if (strcmp(argv[i], "--plan") == 0) {
            g_permission_mode = 1;
        } else if (strcmp(argv[i], "--manual") == 0) {
            g_permission_mode = 2;
        } else if (strcmp(argv[i], "-c") == 0 || strcmp(argv[i], "--continue") == 0) {
            g_continue_until_done = 1;""")

# ---------- R11: env INFER_PERMISSION_MODE ----------
sub("if (env_approve && (strcmp(env_approve, \"1\") == 0 || strcasecmp(env_approve, \"true\") == 0)) {\n        g_permission_mode = 0;\n    }",
    r"""if (env_approve && (strcmp(env_approve, "1") == 0 || strcasecmp(env_approve, "true") == 0)) {
        g_permission_mode = 0;
    }

    char *env_perm = getenv("INFER_PERMISSION_MODE");
    if (env_perm && *env_perm) {
        if (strcasecmp(env_perm, "plan") == 0) g_permission_mode = 1;
        else if (strcasecmp(env_perm, "manual") == 0) g_permission_mode = 2;
        else if (strcasecmp(env_perm, "auto") == 0) g_permission_mode = 0;
    }""")

# ---------- R12: mode injection into system context ----------
sub("             os_info, cwd, user, shell, time_str);\n    return buf;",
    r"""             os_info, cwd, user, shell, time_str);

    if (g_permission_mode == 1) {
        size_t pl = strlen(buf);
        snprintf(buf + pl, 4096 - pl,
                 "\nCURRENT PERMISSION MODE: PLAN\n"
                 "- Investigate, read, search, and run READ-ONLY commands freely.\n"
                 "- DO NOT change anything (no write/edit/execute of state-changing commands / memory / schedule) until your plan is approved.\n"
                 "- To make changes: call present_plan(plan=\"...\") with your findings, the exact changes, and rationale; wait for approval.\n"
                 "- Once a plan is approved you work AUTONOMOUSLY until you have another question or the task is done.\n"
                 "- If your present_plan is rejected, revise and call present_plan again. Report discoveries and check your work before finishing.\n");
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
    return buf;""")

# ---------- R14: skill update notification ----------
sub("/* Prefix tool results with a structured header so small models",
    r"""/* User notification when the model improves its skills (self-improvement) */
                                  if (tool_output
                                      && (strstr(tool_output, "[SKILL_CREATED") || strstr(tool_output, "[SKILL_UPDATED")
                                          || strstr(tool_output, "[SKILL: updated"))) {
                                      fprintf(stderr, "\n\033[1;36m[ai] %s\033[0m\n", tool_output);
                                  }

                                  /* Prefix tool results with a structured header so small models""")

open(P, "wb").write(src.encode("latin-1"))
print("ai.c patched OK")