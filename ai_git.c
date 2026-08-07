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
    snprintf(git_check, sizeof(git_check), "git -C . rev-parse --git-dir 2>/dev/null");
    int has_git = (system(git_check) == 0);
    if (!has_git) return;

    /* Stage all changes */
    char cmd[512];
    snprintf(cmd, sizeof(cmd), "git -C . add -A 2>/dev/null");
    int exit_code = system(cmd);
    if (exit_code != 0) return; /* No changes to stage */

    /* Safety check for sensitive staged files */
    if (has_sensitive_staged_files()) {
        (void)system("git -C . reset HEAD 2>/dev/null");
        return;
    }

    /* Check if anything was staged */
    char verify_cmd[128];
    snprintf(verify_cmd, sizeof(verify_cmd),
             "git -C . diff --cached --quiet 2>/dev/null; echo $?");
    FILE *fp = popen(verify_cmd, "r");
    if (fp) {
        char buf[16] = {0};
        char *r = fgets(buf, sizeof(buf), fp);
        pclose(fp);
        if (r) {
            int has_changes = atoi(buf);
            if (has_changes == 0) {
                /* There are staged changes - commit them */
                const char *commit_msg = g_git_commit_msg ? g_git_commit_msg : "auto-commit: tool changes";
                if (extra_msg) {
                    snprintf(cmd, sizeof(cmd), "git -C . commit -q -m '%s: %s'", commit_msg, extra_msg);
                    (void)system(cmd);
                } else {
                    snprintf(cmd, sizeof(cmd), "git -C . commit -q -m '%s' 2>/dev/null", commit_msg);
                    (void)system(cmd);
                }
                if (g_git_commit_msg) free(g_git_commit_msg);
                g_git_commit_msg = NULL;
            }
        }
    }
}
