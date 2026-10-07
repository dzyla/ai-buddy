#include "ai_git.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int g_git_commit_enabled = 0;   /* opt-in: --git or INFER_AUTO_COMMIT=1 */
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

static void restore_index(const char *tree) {
    char cmd[128];
    int rc;
    if (tree && strlen(tree) == 40 && strspn(tree, "0123456789abcdef") == 40) {
        snprintf(cmd, sizeof(cmd), "git -C . read-tree %s 2>/dev/null", tree);
        rc = system(cmd);
    } else {
        rc = system("git -C . reset -q HEAD 2>/dev/null");
    }
    if (rc != 0)  /* the user's staging may now differ from before auto-commit: say so */
        fprintf(stderr, "\033[1;33m[ai] auto-commit: could not restore the git index; "
                        "check `git status` before committing\033[0m\n");
}

/* Single-quote-safe copy for the shell: drop ' so a custom message can't
   break out of the quoted commit -m argument. */
static void shell_safe(char *dst, size_t n, const char *src) {
    size_t j = 0;
    for (size_t i = 0; src && src[i] && j + 1 < n; i++)
        if (src[i] != '\'') dst[j++] = src[i];
    dst[j] = 0;
}

void git_commit(const char *extra_msg) {
    if (!g_git_commit_enabled) return;

    /* Check if there's a git repo */
    char git_check[64];
    snprintf(git_check, sizeof(git_check), "git -C . rev-parse --git-dir >/dev/null 2>&1");
    int has_git = (system(git_check) == 0);
    if (!has_git) return;

    /* Snapshot the index so a skipped/failed commit leaves the user's own
       staging exactly as it was (a blocked pre-commit hook used to leave
       everything staged). */
    char saved_tree[64] = {0};
    FILE *wt = popen("git -C . write-tree 2>/dev/null", "r");
    if (wt) {
        if (!fgets(saved_tree, sizeof(saved_tree), wt)) saved_tree[0] = 0;
        pclose(wt);
        saved_tree[strcspn(saved_tree, "\r\n")] = 0;
    }

    /* Stage modifications to files git already tracks. Never `add -A`: that
       sweeps untracked files (data, notes, credentials) from the cwd into
       the repo and is how personal files ended up committed. */
    if (system("git -C . add -u 2>/dev/null") != 0) {
        restore_index(saved_tree);  /* partial staging must not linger; skip this commit */
        return;
    }

    /* Safety check for sensitive staged files */
    if (has_sensitive_staged_files()) {
        restore_index(saved_tree);
        return;
    }

    /* Nothing staged? Then there is nothing to commit — stay silent. Do NOT run
       an empty `git commit` (its "nothing to commit" stderr otherwise leaks into
       the model's view on every task). Only report when something actually
       changed and a commit really happens. */
    if (system("git -C . diff --cached --quiet 2>/dev/null") == 0) return;

    char cmd[768];
    char commit_msg[512];
    shell_safe(commit_msg, sizeof(commit_msg),
               g_git_commit_msg ? g_git_commit_msg : "auto-commit: tool changes");
    if (extra_msg && *extra_msg) {
        snprintf(cmd, sizeof(cmd), "git -C . commit -q -m '%s: %s' >/dev/null 2>&1", commit_msg, extra_msg);
    } else {
        snprintf(cmd, sizeof(cmd), "git -C . commit -q -m '%s' >/dev/null 2>&1", commit_msg);
    }
    if (system(cmd) == 0) {
        /* A real commit happened — surface it to the user exactly once. */
        fprintf(stderr, "\033[2m[ai] committed: %s\033[0m\n",
                (extra_msg && *extra_msg) ? extra_msg : commit_msg);
    } else {
        /* e.g. rejected by a pre-commit hook (privacy check) */
        fprintf(stderr, "\033[1;33m[ai] auto-commit skipped (commit failed or was blocked by a hook)\033[0m\n");
        restore_index(saved_tree);
    }
    /* g_git_commit_msg points into argv; it is reused for later commits. */
}
