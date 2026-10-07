"""Python agent engine for the `ai` CLI (INFER_ENGINE=py).

The C binary stays the terminal front-end (ai_frontend.c): it forks `python3 <lib>/agent
--frontend-fd 3 <ai args>` and renders the events this engine sends over fd 3 (protocol.py).
Without --frontend-fd the engine runs headless and prints its own output (protocol.Local).

Modules: cli (argv/env -> Config), llm (HTTP + SSE), messages (system prompt, user turn, trim),
tools (catalog + dispatch through aitools), native (think / task_complete / execute_command /
present_plan / git), session (save / resume / history), protocol (front-end IPC), loop (Engine).
"""
