"""Structured query, sub-agents, session reports, todo/clarify and system/environment info."""
import sys
import os
import json
import subprocess
import time
from .shell import (  # noqa: F401
    execute_command,
)

def structured_query(target, filter_expr=None, transform=None, aggregate=None):
    text = ""
    if target.startswith("file:"):
        fpath = target[5:]
        if os.path.exists(fpath):
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        else:
            return f"Error: file '{fpath}' not found"
    elif target.startswith("command:"):
        cmd = target[8:]
        text = execute_command(cmd)
    else:
        text = target

    lines = text.splitlines()
    if filter_expr:
        import re
        lines = [l for l in lines if re.search(filter_expr, l)]

    if transform:
        if transform.startswith("head:"):
            n = int(transform[5:])
            lines = lines[:n]
        elif transform.startswith("tail:"):
            n = int(transform[5:])
            lines = lines[-n:]
        elif transform == "sort":
            lines = sorted(lines)
        elif transform == "unique":
            lines = list(dict.fromkeys(lines))

    if aggregate:
        if aggregate == "count":
            return str(len(lines))
        elif aggregate == "first":
            return lines[0] if lines else ""
        elif aggregate == "last":
            return lines[-1] if lines else ""

    return "\n".join(lines)

AGENT_STORE_DIR = os.path.expanduser("~/.config/ai/agents")

def spawn_agent(name, prompt, tools=None, persistent=True, budget=None):
    os.makedirs(AGENT_STORE_DIR, exist_ok=True)
    agent_id = f"agent_{name}_{int(time.time())}"
    data = {
        "id": agent_id,
        "name": name,
        "prompt": prompt,
        "tools": tools or ["execute_command", "read_file"],
        "budget": budget,
        "history": [],
        "created_at": time.time(),
        "status": "idle"
    }
    with open(os.path.join(AGENT_STORE_DIR, f"{agent_id}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return f"Spawned agent {name} (ID: {agent_id})"

def resume_agent(agent_id, user_message, budget=None):
    path = os.path.join(AGENT_STORE_DIR, f"{agent_id}.json")
    if not os.path.exists(path):
        return f"Error: Agent '{agent_id}' not found."
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["history"].append({"role": "user", "content": user_message})
    ai_bin = _resolve_ai_bin()
    agent_prompt = data.get("prompt", "")
    full_prompt = f"[Agent Persona / Goal: {agent_prompt}]\n\n{user_message}" if agent_prompt else user_message
    
    cmd_args = [ai_bin, "-y", "-q"]
    effective_budget = budget if budget is not None else data.get("budget")
    if effective_budget is not None and str(effective_budget).strip():
        cmd_args.extend(["--budget", str(effective_budget).strip()])
    cmd_args.append(full_prompt)

    res = subprocess.run(cmd_args, capture_output=True, text=True, timeout=180)
    out = (res.stdout or res.stderr or "").strip()
    data["history"].append({"role": "assistant", "content": out})
    data["last_activity"] = time.time()
    data["status"] = "active" if res.returncode == 0 else "error"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return out

def list_agents():
    if not os.path.exists(AGENT_STORE_DIR):
        return "No agents registered."
    files = [f for f in os.listdir(AGENT_STORE_DIR) if f.endswith(".json")]
    if not files:
        return "No agents found."
    agents_info = []
    for fname in files:
        with open(os.path.join(AGENT_STORE_DIR, fname), "r", encoding="utf-8") as f:
            d = json.load(f)
            agents_info.append(f"- {d.get('name')} ({d.get('id')}): {d.get('status', 'idle')} - {d.get('prompt')[:60]}")
    return "\n".join(agents_info)

SESSION_LOG_FILE = os.path.expanduser("~/.config/ai/session_outcomes.json")

def session_report(success=True, failure_modes=None, notes=""):
    os.makedirs(os.path.dirname(SESSION_LOG_FILE), exist_ok=True)
    logs = []
    if os.path.exists(SESSION_LOG_FILE):
        try:
            with open(SESSION_LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except Exception:
            logs = []
    entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "success": bool(success),
        "failure_modes": failure_modes or [],
        "notes": notes
    }
    logs.append(entry)
    with open(SESSION_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, indent=2)
    return "Session outcome logged successfully."

def _todo_path():
    # Session-scoped when the C loop tells us which session we're in, so
    # parallel/resumed sessions keep separate lists; falls back to a global
    # file for direct ai_mcp.py invocations (tests, bridges).
    sid = os.environ.get("INFER_SESSION_ID", "").strip()
    if sid:
        safe = "".join(c for c in sid if c.isalnum() or c in "_-")
        return os.path.expanduser("~/.config/ai/todo_%s.json" % safe)
    return os.path.expanduser("~/.config/ai/todo.json")

def _todo_load():
    p = _todo_path()
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def _todo_save(items):
    try:
        p = _todo_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2)
    except Exception as e:
        return f" (warning: could not persist todo: {e})"
    return ""

def _todo_render(items):
    if not items:
        return "TODO list is empty. Call todo with a 'todos' array to create it for multi-step tasks."
    lines = ["Current task list (use todo to update statuses as you go):"]
    status_mark = {"pending": "[ ]", "in_progress": "[~]",
                   "completed": "[x]", "cancelled": "[-]"}
    for it in items:
        st = it.get("status", "pending")
        lines.append(f"{status_mark.get(st, '[?]')} {it.get('id', '?')}: "
                     f"{it.get('content', '')} ({st})")
    done = sum(1 for i in items if i.get("status") == "completed")
    lines.append(f"{done}/{len(items)} done.")
    return "\n".join(lines)

def todo(todos=None, merge=False):
    """Session-scoped task list. Call with NO arguments to read the current
    list. Pass a 'todos' array of {id, content, status} items to create
    (merge=false, the default) or update (merge=true) it. Statuses:
    pending | in_progress | completed | cancelled. Use for tasks with 3+
    steps so a small model can always see where it is; mark items completed
    immediately when done — don't batch."""
    if todos is None:
        return _todo_render(_todo_load())
    if not isinstance(todos, list):
        return "Error: 'todos' must be an array of {id, content, status} objects."
    for t in todos:
        if not isinstance(t, dict) or not t.get("id"):
            return "Error: each todo item needs an 'id'."
        # content is required when creating a new item; in merge mode you may
        # update only the status of an existing item (its content is kept).
        if not merge and not t.get("content"):
            return "Error: each new todo item needs 'content' (or use merge=True to update an existing item by id)."
    items = _todo_load()
    if not merge:
        items = []
    by_id = {str(i.get("id")): i for i in items}
    for t in todos:
        tid = str(t["id"])
        if tid in by_id:
            for k in ("content", "status"):
                if t.get(k):
                    by_id[tid][k] = t[k]
        else:
            items.append({"id": tid, "content": t["content"],
                          "status": t.get("status", "pending")})
            by_id[tid] = items[-1]
    warn = _todo_save(items)
    return _todo_render(items) + warn

def clarify(question, choices=None, multi_select=False, timeout_seconds=0):
    """Ask the user a question and wait for their answer before continuing.
    Use when a task is ambiguous and proceeding the wrong way wastes work:
    present 2-4 numbered options (the user picks one, or types their own).
    Without options it is an open-ended question. In non-interactive runs
    (bridges, schedulers, cron) this returns a NOTE telling you to make a
    sensible default choice and proceed — never block waiting for a human."""
    q = (question or "").strip()
    if not q:
        return "Error: 'question' is required."
    opts = []
    if isinstance(choices, str):
        opts = [c.strip() for c in choices.split(",") if c.strip()]
    elif isinstance(choices, list):
        opts = [str(c).strip() for c in choices if str(c).strip()]
    opts = opts[:8]
    # Interactive path: ask on the controlling terminal (like the approval
    # prompts in ai.c). Only interactive when the C loop says a human is on
    # stdin (INFER_INTERACTIVE=1) — or, as a fallback, stdin is itself a tty.
    # In pipes/bridges/schedulers there is no one to answer → default-and-go.
    interactive = (os.environ.get("INFER_INTERACTIVE") == "1"
                   or sys.stdin.isatty()) \
        and os.environ.get("INFER_NON_INTERACTIVE") not in ("1", "true")
    if not interactive:
        note = ("[CLARIFY NON-INTERACTIVE] No human is present to answer "
                f"({q[:160]})")
        if opts:
            note += (f". Options were: " + " | ".join(opts[:4]) +
                     ". Choose the first (recommended) option yourself and proceed; "
                     "state your assumption in the final summary.")
        else:
            note += ". Make a sensible default choice, proceed, and state the assumption in the final summary."
        return note
    lines = [f"\n  CLARIFY: {q}"]
    if opts:
        for i, o in enumerate(opts, 1):
            lines.append(f"    {i}. {o}")
        lines.append("  number (1-%d), free text, or Enter for option 1: " % len(opts))
    else:
        lines.append("  answer: ")
    try:
        tty = open("/dev/tty", "r+")
    except OSError:
        return "[CLARIFY NON-INTERACTIVE] Could not open a terminal; proceed with a sensible default and state the assumption."
    import select
    try:
        prompt_str = "\n".join(lines[:-1]) + ("\n" if len(lines) > 1 else "") + lines[-1]
        tty.write(prompt_str)
        tty.flush()
        if timeout_seconds > 0:
            r, _, _ = select.select([tty], [], [], timeout_seconds)
            if not r:
                return "[CLARIFY TIMEOUT] No answer in time; proceed with a sensible default and state the assumption."
        ans = tty.readline().strip()
    finally:
        tty.close()
    if not ans:
        return f"[CLARIFY] Answer: (default) {opts[0] if opts else 'proceed'}"
    if opts and ans.isdigit() and 1 <= int(ans) <= len(opts):
        return f"[CLARIFY] Answer: {opts[int(ans) - 1]}"
    return f"[CLARIFY] Answer: {ans}"

def get_environment():
    """Return comprehensive system and environment diagnostics (OS, GPU, Python, Git, Model server)."""
    import platform, shutil
    info = {
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": sys.version.split()[0],
        "cwd": os.getcwd(),
        "user": os.environ.get("USER", "unknown"),
        "model": os.environ.get("INFER_MODEL", "default"),
        "base_url": os.environ.get("INFER_BASE_URL", "default"),
    }
    if shutil.which("nvidia-smi"):
        try:
            smi = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=5)
            if smi.returncode == 0 and smi.stdout.strip():
                parts = [p.strip() for p in smi.stdout.strip().split(",")]
                if len(parts) >= 4:
                    info["gpu"] = f"{parts[0]} ({parts[2]}MB / {parts[1]}MB used, {parts[3]}% util)"
        except Exception:
            pass
    if os.path.exists(".git") and shutil.which("git"):
        try:
            branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                                    capture_output=True, text=True, timeout=3).stdout.strip()
            status = subprocess.run(["git", "status", "--porcelain"],
                                    capture_output=True, text=True, timeout=3).stdout.strip()
            info["git"] = {
                "branch": branch,
                "clean": len(status) == 0,
                "modified_files": len(status.splitlines()) if status else 0
            }
        except Exception:
            pass
    return json.dumps(info, indent=2)

def get_system_status():
    """Return a compact one-line CPU/RAM/disk summary for quick status checks."""
    import re as _re
    # CPU: parse /proc/stat for aggregate idle fraction
    try:
        with open("/proc/stat") as _f:
            line = _f.readline()
        parts = line.split()
        vals = [int(x) for x in parts[1:]]
        idle = vals[3] + vals[4]  # idle + iowait
        total = sum(vals)
        usage_pct = round(100.0 - (idle * 100.0 / total), 1) if total else 0
    except Exception:
        usage_pct = "?"
    # RAM
    try:
        mem = {}
        with open("/proc/meminfo") as _f:
            for ln in _f:
                k, _, v = ln.partition(":")
                mem[k.strip()] = int(v.split()[0])
        total_kb = mem["MemTotal"]
        avail_kb = mem["MemAvailable"]
        used_pct = round((1 - avail_kb / total_kb) * 100, 1) if total_kb else 0
        used_mb = (total_kb - avail_kb) // 1024
        total_mb = total_kb // 1024
    except Exception:
        used_pct = "?"
        used_mb = "?"
        total_mb = "?"
    # Disk (root)
    try:
        import subprocess as _sp
        out = _sp.run(["df", "-h", "/"], capture_output=True, text=True, check=True)
        disk_line = out.stdout.strip().splitlines()[-1]
        parts = disk_line.split()
        disk_total = parts[1]
        disk_used = parts[2]
        disk_pct = parts[4]
    except Exception:
        disk_total = "?"
        disk_used = "?"
        disk_pct = "?"
    # nproc
    try:
        nproc = os.cpu_count() or "?"
    except Exception:
        nproc = "?"
    return (
        f"CPU: {usage_pct}% | "
        f"RAM: {used_mb}/{total_mb} MB ({used_pct}%) | "
        f"Disk: {disk_used}/{disk_total} ({disk_pct}) | "
        f"Cores: {nproc}"
    )

def get_clipboard():
    """Read the current X/Wayland clipboard content."""
    import subprocess as _sp
    for cmd in [["xclip", "-selection", "clipboard", "-o"],
                ["wl-paste"],
                ["xsel", "--clipboard", "--output"]]:
        try:
            out = _sp.run(cmd, capture_output=True, text=True, timeout=5)
            if out.returncode == 0:
                return out.stdout
        except Exception:
            pass
    return "Error: could not read clipboard (no xclip/wl-paste/xsel available or clipboard is empty)."

def _resolve_ai_bin():
    """Return path to the ai binary, checking environment override first."""
    candidates = [
        os.environ.get("INFER_BIN_PATH", ""),
        os.path.expanduser("~/.local/bin/ai"),
        "/usr/local/bin/ai",
        "./ai",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return "./ai"
