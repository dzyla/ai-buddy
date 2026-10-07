/* Client for the persistent tool server (`python3 ai_mcp.py serve`, aitools/server.py).
 *
 * toolclient_call() sends one request over $XDG_RUNTIME_DIR/ai/tools.sock (fallback
 * ~/.cache/ai/tools.sock), spawning the server (double fork + setsid) when nothing listens.
 * Only TOOLCLIENT_UNAVAILABLE means "nothing ran" (no connection, nothing sent, or the server
 * refused before running): only then may the caller use its popen path. INFER_TOOL_SERVER=0
 * disables the client; INFER_TOOL_SERVER_SCOPE=0 skips the systemd-run scope spawn.
 */
#ifndef AI_TOOLCLIENT_H
#define AI_TOOLCLIENT_H

#define TOOLCLIENT_OK            0
#define TOOLCLIENT_UNAVAILABLE (-1) /* server unusable; nothing ran: fall back to popen */
#define TOOLCLIENT_TIMEOUT     (-2) /* the call ran past its timeout and was killed; *out = partial stdout */
#define TOOLCLIENT_INTERRUPTED (-3) /* interrupted (ESC); the server kills the call; *out = partial or NULL */
#define TOOLCLIENT_LOST        (-4) /* request delivered, no usable answer: it may have run. Never re-run. */

/* ai_mcp.py path (resolved to an absolute path); without it the client stays disabled. */
void toolclient_set_script(const char *mcp_script);

/* Polled every 100 ms while waiting; a nonzero return aborts the call (e.g. ESC pressed). */
void toolclient_set_interrupt(int (*cb)(void));

/* op: "list_tools" | "call" | "render". For "call", args_json is the raw argument text exactly as
 * the model produced it (the server repairs it like `call-tool`); for "render" it is the markdown
 * text. On TOOLCLIENT_OK *out is the malloc'd stdout of the equivalent `ai_mcp.py` subcommand; the
 * tool's stderr is copied to our stderr (not for "render"). */
int toolclient_call(const char *op, const char *server, const char *tool,
                    const char *args_json, int timeout_s, char **out);

#endif
