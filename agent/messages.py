"""Message construction: the system prompt (ai.c load_system_prompt + live context + memory + skills
+ recap + AGENTS.md), the first user turn (stdin context, image), sizes and protocol-safe trimming."""
import base64
import contextlib
import json
import os
import platform
import time

from .prompt_text import SYSTEM_PROMPT

MEMORY_QUERY_MAX_BYTES = 1000


# ── sizes / trimming ─────────────────────────────────────────────────────────
def size(messages):
    """Length of the history as ai.c measures it (strlen of the compact JSON, UTF-8 bytes)."""
    return len(json.dumps(messages, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def trim(messages, max_bytes):
    """aitools.sessions.trim_messages: keeps the system prompt, the first and latest real user turn
    and whole assistant(tool_calls)+tool units; returns the input unchanged when under budget."""
    from aitools.sessions import trim_messages
    try:
        out = trim_messages(messages, max(2000, int(max_bytes)))
    except Exception:
        return messages
    return out if isinstance(out, list) and out else messages


# ── system prompt pieces ─────────────────────────────────────────────────────
def load_system_prompt(environ=None):
    env = os.environ if environ is None else environ
    path = env.get("INFER_SYSTEM_PROMPT_FILE")
    if not path:
        home = env.get("HOME")
        if not home:
            return SYSTEM_PROMPT
        path = os.path.join(home, ".config", "ai", "system_prompt.md")
    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return SYSTEM_PROMPT
    return text if text.strip() else SYSTEM_PROMPT


def local_time():
    return time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime())


def system_context(permission_mode):
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = "Unknown"
    u = platform.uname()
    os_info = "%s %s %s" % (u.system, u.release, u.machine)
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown"
    shell = os.environ.get("SHELL") or "unknown"
    s = ("Host System Context:\n- Operating System: %s\n- Current Working Directory: %s\n- User: %s\n"
         "- Shell: %s\n- Local Time: %s\n" % (os_info, cwd, user, shell, local_time()))
    if permission_mode == 1:
        s += ("\nCURRENT PERMISSION MODE: PLAN\n"
              "- Investigate, read, search, and run READ-ONLY commands freely.\n"
              "- DO NOT change anything (no write/edit/execute of state-changing commands / memory / schedule) until your plan is approved.\n"
              "- To make changes: call present_plan(plan=\"...\") with your findings, the exact changes, and rationale; wait for approval.\n"
              "- Once a plan is approved you may make a BOUNDED number of state-changing actions (INFER_PLAN_STEP_BUDGET, default 8). When the budget runs out the harness blocks further changes; you MUST present_plan again before more changes.\n"
              "- Execute step by step: do ONE step, validate its result, then continue. For complex work, delegate individual steps to subagents and verify each result. If an approval is rejected, revise and call present_plan again.\n")
    elif permission_mode == 2:
        s += ("\nCURRENT PERMISSION MODE: MANUAL\n"
              "- You must obtain explicit user approval for EVERY state-changing action (commands, file writes/edits, memory, scheduling).\n"
              "- Read-only investigation tools run without prompting.\n"
              "- When the system returns a denial, adjust your approach; do not retry the same action until the user allows it.\n")
    else:
        s += ("\nCURRENT PERMISSION MODE: FULL AUTONOMY (auto)\n"
              "- Investigate, execute, and change state freely until the task is finished, then call task_complete.\n"
              "- Still prefer to think before major actions and verify your results.\n")
    return s[:4095]


def _skill_description(header):
    k = header.find("description:")
    if k < 0:
        return "(no description)"
    rest = header[k + len("description:"):].lstrip(" ")
    end = len(rest)
    for ch in "\n\r":
        p = rest.find(ch)
        if p >= 0:
            end = min(end, p)
    return rest[:end]


def critical_triggers():
    """CRITICAL skill triggers from ~/.config/ai/skills then ./.agents/skills (directory order)."""
    lines = []
    home = os.environ.get("HOME")
    dirs = ([os.path.join(home, ".config", "ai", "skills")] if home else []) + ["./.agents/skills"]
    for base in dirs:
        try:
            names = os.listdir(base)
        except OSError:
            continue
        for name in names:
            if name.startswith("."):
                continue
            try:
                with open(os.path.join(base, name, "SKILL.md"), "rb") as f:
                    header = f.read(511).decode("utf-8", "replace")
            except OSError:
                continue
            desc = _skill_description(header)
            if not desc.startswith("CRITICAL"):
                continue
            sep = desc.find(": ")
            cond = desc[:sep] if sep >= 0 else desc
            if "load_skill('%s')" % name in "".join(lines):
                continue
            lines.append("- %s → call load_skill('%s') before any other tool.\n" % (cond, name))
    return "".join(lines) or None


def load_agents_md(enabled, short_ctx, context_window, log):
    if not enabled:
        return None
    for cand in ("./AGENTS.md", "./docs/AGENTS.md", "./.ai/AGENTS.md"):
        try:
            with open(cand, "rb") as f:
                data = f.read()
        except OSError:
            continue
        fsize = len(data)
        if 0 < fsize < 100000:
            cap = 3000 if (short_ctx or 0 < context_window <= 32768) else 12000
            take = min(fsize, cap)
            text = data[:take].decode("utf-8", "replace")
            if take < fsize:
                text += ("\n\n[... AGENTS.md truncated at %d of %d bytes to save context; read_file('%s') "
                         "for the rest.]" % (take, fsize, cand))
            log("\033[2m[ai] loaded AGENTS.md from %s%s\033[0m\n" % (cand, " (truncated)" if take < fsize else ""))
            return text
    return None


def user_rules():
    home = os.environ.get("HOME") or ""
    try:
        with open(os.path.join(home, ".config", "ai", "rules.txt"), "rb") as f:
            data = f.read()
    except OSError:
        return None
    if 0 < len(data) < 65536:
        return data.decode("utf-8", "replace")
    return None


def _query_head(query):
    b = query.encode("utf-8", "surrogateescape")
    if len(b) <= MEMORY_QUERY_MAX_BYTES:
        return query
    n = MEMORY_QUERY_MAX_BYTES
    while n > 0 and (b[n] & 0xC0) == 0x80:
        n -= 1
    return b[:n].decode("utf-8", "ignore")


def memory_section(query):
    """`state-export memory [--query q]`: the 4 KB memory render plus additive recall; the plain
    render when recall fails or prints nothing. Never raises."""
    from aitools import state
    with (state.lock_budget(1.5) if hasattr(state, "lock_budget") else contextlib.nullcontext()):
        out = None
        if query:
            try:
                from aitools import search
                out = search.memory_section(_query_head(query))
            except Exception:
                out = None
            out = (out or "").strip() or None
        if out is None:
            try:
                out = (state.memory_render() or "").strip() or None
            except Exception:
                out = None
    return out


def rag_memories(query):
    """`rag-memories <q>` (memory.recall), dropped when nothing matched. Keeps print()'s newline."""
    try:
        from aitools.memory import recall
        res = recall(query)
    except Exception:
        return None
    if not isinstance(res, str) or "No memories found" in res or res.startswith("Error"):
        return None
    return res + "\n"


def session_recap():
    if os.environ.get("INFER_SESSION_RECAP") is not None and _atoi0(os.environ["INFER_SESSION_RECAP"]) == 0:
        return None
    try:
        from aitools import state
        from aitools.selfimprove import session_recap as recap
        with state.lock_budget(1.5):
            text = recap()
    except Exception:
        return None
    return text or None


def _atoi0(s):
    s = (s or "").strip()
    j = 1 if s[:1] in "+-" else 0
    while j < len(s) and s[j].isdigit():
        j += 1
    try:
        return int(s[:j])
    except ValueError:
        return 0


class SystemPrompt:
    """ai.c builds the system message once per session. Here everything except the memory section
    is frozen at the first build() (stable prompt-cache prefix, same text as C); the memory section
    is recomputed for each build(query) — per interactive user turn (controller Ruling 19)."""

    def __init__(self, cfg, log):
        self.cfg = cfg
        base = load_system_prompt()
        rules = user_rules()
        if rules is not None:
            base = "%s\n\n--- User-Defined Rules (Continuous Local Learning) ---\n%s" % (base, rules)
        self.base = base
        self.triggers = critical_triggers()
        self.recap = session_recap()
        self.agents_md = load_agents_md(cfg.agents_enabled, cfg.short_ctx, cfg.context_window, log)
        self._frozen = None

    def build(self, memory_query, rag_query=None):
        cfg = self.cfg
        if self._frozen is None:
            head = "%s\n\n%s" % (self.base, system_context(cfg.permission_mode))
            if cfg.goal:
                head += ("\n\nMISSION BOARD:\n- Top Goal: %s\n- Status: In Progress\n- Instruction: Decompose "
                         "into subtasks, execute step-by-step, verify work before finishing." % cfg.goal)
            if cfg.agents_enabled and self.triggers:
                head += "\n\nCRITICAL SKILL TRIGGERS (obey BEFORE any other tool):\n%s" % self.triggers
            rag = rag_memories(rag_query if rag_query else "current context or recent tasks")
            tail = ""
            if rag:
                tail += "\n\nRelevant Context (RAG Memories):\n%s" % rag
            tail += ("\n\n[IMPORTANT: Current system time is %s. Always reference the current year, month, "
                     "and day in your responses. Do not assume outdated dates.]" % local_time())
            if self.recap:
                tail += ("\n\n--- Self-Improvement Session Recap (start from what past sessions learned; obey "
                         "its MASTERED chains when the same error appears) ---\n%s" % self.recap)
            if self.agents_md:
                tail += "\n\n--- Project Context (AGENTS.md) ---\n%s" % self.agents_md
            self._frozen = (head, tail)
        head, tail = self._frozen
        memory = memory_section(memory_query)
        mem = "\n\nPersistent Memory/Preferences:\n%s" % memory if memory else ""
        return {"role": "system", "content": head + mem + tail}


# ── user turn ────────────────────────────────────────────────────────────────
def image_data_url(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if not data:
        return None
    ext = os.path.splitext(path)[1].lower()
    mime = "image/png" if ext == ".png" else "image/webp" if ext == ".webp" else "image/jpeg"
    return "data:%s;base64,%s" % (mime, base64.b64encode(data).decode("ascii"))


def first_user_message(prompt, pipe_in, pipe_writer, image_path, log):
    if pipe_in:
        if pipe_writer:
            text = "%s\n\nContext (output of command `%s`):\n%s" % (prompt, pipe_writer, pipe_in)
        else:
            text = "%s\n\nContext:\n%s" % (prompt, pipe_in)
    elif pipe_writer:
        log("[ai] Warning: Piped command '%s' returned no stdout. The agent will execute it to inspect "
            "stderr.\n" % pipe_writer)
        text = ("%s\n\nContext:\nThe user ran the command `%s` in their terminal, but its stdout was empty. "
                "It might have failed and written to stderr. You should run this command using the "
                "execute_command tool to inspect its output/errors." % (prompt, pipe_writer))
    else:
        text = prompt
    if image_path:
        url = image_data_url(image_path)
        if url:
            return {"role": "user", "content": [{"type": "text", "text": text},
                                                {"type": "image_url", "image_url": {"url": url}}]}
    return {"role": "user", "content": text}


def image_message(path):
    """The user message ai.c adds after read_file returns [IMAGE_DATA_SUCCESS:<path>]."""
    url = image_data_url(path)
    if not url:
        return None
    return {"role": "user", "content": [
        {"type": "text", "text": "Here is the image file '%s' you requested to read:" % path},
        {"type": "image_url", "image_url": {"url": url}}]}
