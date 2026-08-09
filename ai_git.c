#include "ai_git.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int g_git_commit_enabled = 1;
char *g_git_commit_msg = NULL;

static const char *git_denylist[] = {
    "\\.env", "\\.pem", "\\.key", "\\.p12",
    "\\.ssh/", "id_rsa", "id_ed25519",
    "\\.aws/", "\\.gcloud/", "\\.azure/",
    "\\.db", "\\.sqlite", "\\.sqlite3",
    NULL
};

int is_command_denied(const char *cmd) {
    if (!cmd) return 0;
    /* Basic check for dangerous/blocked commands */
    if (strstr(cmd, "rm -rf /") || strstr(cmd, "mkfs") || strstr(cmd, "dd if=")) {
        return 1;
    }
    return 0;
}

int has_sensitive_staged_files(void) {
    for (int i = 0; git_denylist[i]; i++) {
        char cmd[512];
        snprintf(cmd, sizeof(cmd),
            "git -C . diff --cached --name-only 2>/dev/null | grep -iE '%s' >/dev/null 2>&1",
            git_denylist[i]);
        if (system(cmd) == 0) {
            fprintf(stderr, "\033[1;33m[ai] Skipping auto-commit: sensitive pattern matched (%s)\033[0m\n", git_denylist[i]);
            return 1;
        }
    }
    return 0;
}

void git_commit(const char *extra_msg) {
    if (!g_git_commit_enabled) return;

    /* Check if there's a git repo */
    char git_check[64];
    snprintf(git_check, sizeof(git_check), "git -C . rev-parse --git-dir >/dev/null 2>&1");
    int has_git = (system(git_check) == 0);
    if (!has_git) return;

    /* Stage all changes */
    (void)system("git -C . add -A 2>/dev/null");

    /* Safety check for sensitive staged files */
    if (has_sensitive_staged_files()) {
        (void)system("git -C . reset HEAD 2>/dev/null");
        return;
    }

    /* Nothing staged? Then there is nothing to commit — stay silent. Do NOT run
       an empty `git commit` (its "nothing to commit" stderr otherwise leaks into
       the model's view on every task). Only report when something actually
       changed and a commit really happens. */
    if (system("git -C . diff --cached --quiet 2>/dev/null") == 0) return;

    char cmd[768];
    const char *commit_msg = g_git_commit_msg ? g_git_commit_msg : "auto-commit: tool changes";
    if (extra_msg && *extra_msg) {
        snprintf(cmd, sizeof(cmd), "git -C . commit -q -m '%s: %s' >/dev/null 2>&1", commit_msg, extra_msg);
    } else {
        snprintf(cmd, sizeof(cmd), "git -C . commit -q -m '%s' >/dev/null 2>&1", commit_msg);
    }
    if (system(cmd) == 0) {
        /* A real commit happened — surface it to the user exactly once. */
        fprintf(stderr, "\033[2m[ai] committed: %s\033[0m\n",
                (extra_msg && *extra_msg) ? extra_msg : commit_msg);
    }
    if (g_git_commit_msg) free(g_git_commit_msg);
    g_git_commit_msg = NULL;
}
