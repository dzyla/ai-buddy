#ifndef AI_FRONTEND_H
#define AI_FRONTEND_H

/* Front-end mode (the default engine; not INFER_ENGINE=c): the C binary keeps the terminal — line editor + history,
   raw-mode keys (ESC, Shift-Tab, Ctrl-O, :btw), /dev/tty approval prompts, the answer/tool/think
   boxes and the spinner — and the agent loop runs in the Python engine
   (`python3 <lib>/agent --frontend-fd 3 <ai args>`), newline-delimited JSON over fd 3 (a socketpair).
   No LLM/HTTP code runs in C in this mode. Protocol: see agent/protocol.py. */

/* Turn-item kinds of ai.c's Ctrl-O redraw history (turn_item_type_t order). */
enum { FE_ITEM_USER = 0, FE_ITEM_THINKING, FE_ITEM_TOOL, FE_ITEM_ANSWER, FE_ITEM_INFO };

/* Rendering + terminal primitives that stay in ai.c (static there). */
typedef struct {
    void (*print_tool_box)(const char *name, const char *status, const char *content, double elapsed);
    void (*print_think_box)(const char *reasoning);
    /* prerendered=1: content is already render-markdown output (no second render pass). */
    void (*print_response_box)(const char *model, const char *content, int turn, int tools,
                               double elapsed, double tps, int prerendered);
    void (*print_info_box)(const char *title, const char *body);
    void (*print_user_message)(const char *message);
    char *(*read_line)(const char *prompt);       /* malloc'd; NULL on EOF/Ctrl-C; "\033[Z" Shift-Tab */
    void (*lineed_init)(void);
    void (*lineed_add_history)(const char *line);
    int  (*prompt_user_ok)(const char *title, const char *detail);   /* 1 yes, 0 no, -1 no tty */
    void (*redraw)(void);
    void (*add_turn_item)(int kind, const char *name, const char *status, const char *content,
                          double elapsed, double tps, int turn, int tools);
    void (*jobs_status)(void);
    void (*copy_to_clipboard)(const char *text);
    int *hide_details;
    int *permission_mode;
} frontend_ui_t;

/* Runs the whole session in front-end mode and returns the process exit code, or -1 when the
   Python engine cannot be found or started (the caller then runs the C engine). */
int frontend_main(int argc, char **argv, const frontend_ui_t *ui);

#endif /* AI_FRONTEND_H */
