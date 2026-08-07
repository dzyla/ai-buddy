#ifndef AI_GIT_H
#define AI_GIT_H

extern int g_git_commit_enabled;
extern char *g_git_commit_msg;

int is_command_denied(const char *cmd);
int has_sensitive_staged_files(void);
void git_commit(const char *extra_msg);

#endif /* AI_GIT_H */
