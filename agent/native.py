"""Tools the engine handles itself (as ai.c does): argument extraction for think / task_complete /
execute_command, the command denylist, running a shell command (timeout, ESC, process-tree kill),
background launch, the Gemma leaked-task_complete detector and git auto-commit (ai_git.c)."""
import json
import os
import select
import signal
import subprocess
import time

THINK_KEYS = ("reasoning", "thought", "thoughts", "plan", "reason")
SUMMARY_KEYS = ("summary", "text", "response", "result", "message", "final_answer", "answer")
COMMAND_KEYS = ("command", "cmd", "command_line", "CommandLine", "args", "script", "code", "c")

DEFAULT_DENIED = ("rm -rf /", "rm -rf ~", "rm -rf $HOME", "mkfs", "dd if=/dev/zero",
                  ":(){ :|:& };:", "chmod -R 777 /", "> /dev/sda")

SYSTEM_WARNING_TIMEOUT = ("\n[SYSTEM WARNING: Command timed out after %d seconds. Process killed. Partial output "
                          "above. Evaluate if script is stuck in an infinite loop, waiting for user input, or just "
                          "intrinsically slow.]\n")

# Tools that change persistent state (need approval in manual / unapproved-plan mode).
# Mirrored in ai.c tool_is_mutating()/tool_is_readonly(); tests/test_list_drift.py keeps them equal.
MUTATING = frozenset((
    "execute_command", "execute_remote_command", "remote_exec",
    "write_file", "edit_file", "patch_file", "save_memory", "remember",
    "learn_rule", "vault_write", "schedule_task", "set_reminder",
    "unschedule_task", "start_background_process", "stop_process", "delegate_task",
    "spawn_agent", "resume_agent", "append_to_context_pool",
    "skill_create", "skill_update", "skill_note",
    "browser", "todo", "structured_query", "session_report"))
# Deny-by-default allowlist: only these run without approval in PLAN/MANUAL mode.
READONLY = frozenset((
    "think", "web_search", "arxiv_search", "fetch_webpage", "fetch_smart", "read_file",
    "list_directory", "recall", "list_processes", "check_process_status", "parallel_fetch",
    "load_skill", "fetch_webpage_js", "get_system_status", "get_clipboard", "get_environment",
    "vault_read", "vault_search", "vault_backlinks", "pubmed_search",
    "gcal_list_events", "gcal_check_availability", "check_time", "list_scheduled_tasks",
    "search_history", "list_sessions", "get_session", "present_plan", "task_complete",
    "search_files", "list_agents", "get_context_snippet", "search_context", "clarify",
    "python_playground", "js_playground",
    "scientific__pdb_parse", "scientific__uniprot_search", "scientific__align_sequences",
    "scientific__data_analysis", "scientific__security_audit"))


def is_command_denied(cmd, environ=None):
    if not cmd:
        return False
    if any(d in cmd for d in DEFAULT_DENIED):
        return True
    deny = (os.environ if environ is None else environ).get("INFER_COMMAND_DENYLIST") or ""
    for tok in deny.split(","):
        tok = tok.lstrip()
        if tok and tok in cmd:
            return True
    return False


def _scalar_text(v):
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def _walk(obj):
    """(key, value) pairs in document order at every depth (jsmn token order)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def arg_text(args, keys):
    """ai.c's think/task_complete argument extraction: a JSON string, raw non-JSON text, or the
    value of the first of `keys` met in the object (any depth); None when absent/unparseable."""
    args = args or ""
    try:
        obj = json.loads(args)
    except ValueError:
        obj = None
        if args and args[0] not in "{[":
            return args
        return None
    if isinstance(obj, str):
        return obj
    if args[:1] not in ("{", "["):
        return args
    for k, v in _walk(obj):
        if k in keys:
            return _scalar_text(v)
    return None


def _atoi(s):
    s = (s or "").strip()
    j = 1 if s[:1] in "+-" else 0
    while j < len(s) and s[j].isdigit():
        j += 1
    try:
        return int(s[:j])
    except ValueError:
        return 0


def command_args(args):
    """(command or None, timeout seconds) for execute_command; later keys win like ai.c's scan."""
    args = args or ""
    cmd, timeout = None, 120
    try:
        obj = json.loads(args)
    except ValueError:
        if args and args[0] not in "{[":
            return args, timeout
        return None, timeout
    if isinstance(obj, str):
        return obj, timeout
    if args[:1] not in ("{", "["):
        return args, timeout
    for k, v in _walk(obj):
        if k in COMMAND_KEYS:
            if isinstance(v, str):
                cmd = v
            elif isinstance(v, list):
                cmd = " ".join(_scalar_text(x) for x in v if not isinstance(x, (dict, list)))
        elif k in ("timeout", "t") and not isinstance(v, (dict, list)):
            timeout = _atoi(_scalar_text(v))
    return cmd, timeout


def extract_leaked_task_complete(content):
    """Gemma-style `task_complete{summary:<|"|>...<|"|>}` in plain text -> the summary, else None."""
    if not content:
        return None
    p = content.lstrip()
    if not p.startswith("task_complete"):
        return None
    p = p[len("task_complete"):].lstrip()
    if not p.startswith("{"):
        return None
    k = p.find("summary:", 1)
    if k < 0:
        return None
    p = p[k + len("summary:"):].lstrip()
    if p.startswith('<|"|>'):
        p = p[5:]
        end = p.find('<|"|>')
    elif p.startswith('"'):
        p = p[1:]
        end = p.find('"')
    else:
        end = p.rfind("}")
    if end <= 0:
        return None
    return p[:end]


def chain_id(tool, error):
    from aitools.selfimprove import _chain_id
    return _chain_id(tool, error)


# ── running commands ──────────────────────────────────────────────────────────
def _children(pid):
    out = []
    try:
        tasks = os.listdir("/proc/%d/task" % pid)
    except OSError:
        return out
    for t in tasks:
        try:
            with open("/proc/%d/task/%s/children" % (pid, t)) as f:
                out += [int(x) for x in f.read().split()]
        except (OSError, ValueError):
            pass
    return out


# Process trees of the command / tool running right now (what a signal must take down — and
# nothing else: background jobs started earlier in the session survive the engine, as under ai.c).
INFLIGHT_PIDS = set()        # root pids (run_shell's /bin/sh)
INFLIGHT_BASELINES = []      # child-pid snapshots taken before a forked tool call started


def inflight_roots():
    roots = set(INFLIGHT_PIDS)
    now = set(_children(os.getpid()))
    for base in list(INFLIGHT_BASELINES):
        roots |= now - base
    return roots


def kill_tree(pid):
    for c in _children(pid):
        kill_tree(c)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def run_shell(cmd, timeout, interrupted, log):
    """run_shell_command_timeout(cmd): (output, exit_status). exit 124 on timeout (warning appended),
    130 when `interrupted` (threading.Event) fires; the command's whole process tree is killed in
    both cases (a job it detached with `nohup … &` has left the tree and survives, as under ai.c)."""
    try:
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=None, close_fds=True)
    except OSError:
        return "Error: failed to run command", -1
    INFLIGHT_PIDS.add(p.pid)
    buf = bytearray()
    t0 = time.time()
    status = None

    def _kill():
        kill_tree(p.pid)

    fd = p.stdout.fileno()
    eof = False
    try:
        while True:
            # After stdout EOF keep honouring the timeout and the interrupt until the shell exits:
            # a command may close its stdout and keep running (`exec >&-; sleep 30`).
            if eof and p.poll() is not None:
                break
            if timeout > 0 and time.time() - t0 > timeout:
                _kill()
                buf += (SYSTEM_WARNING_TIMEOUT % timeout).encode()
                status = 124
                log("\n\033[1;31m[ai] Tool execution timed out after %ds.\033[0m\n" % timeout)
                break
            if interrupted is not None and interrupted.is_set():
                _kill()
                status = 130
                log("\n\033[1;31m[ai] Tool execution interrupted by ESC key.\033[0m\n")
                break
            if eof:
                time.sleep(0.05)
                continue
            r, _, _ = select.select([fd], [], [], 0.1)
            if r:
                chunk = os.read(fd, 65536)
                if not chunk:
                    eof = True
                    continue
                buf += chunk
    finally:
        try:
            p.stdout.close()
        except OSError:
            pass
        rc = p.wait()
        INFLIGHT_PIDS.discard(p.pid)
    if status is None:
        status = rc if rc >= 0 else -rc  # ai.c: WIFEXITED ? WEXITSTATUS : raw wait status
    return bytes(buf).replace(b"\0", b"").decode("utf-8", "replace"), status


def run_background(cmd):
    home = os.environ.get("HOME") or ""
    log_file = "%s/.config/ai/logs/bg_proc_%d.log" % (home, int(time.time()))
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    quoted = "'" + cmd.replace("'", "'\\''") + "'"
    out = subprocess.run("nohup sh -c %s > '%s' 2>&1 & echo $!" % (quoted, log_file), shell=True,
                         capture_output=True, text=True).stdout
    pid = out.split("\n")[0].strip()
    return ("$ %s\n\nCommand launched in background. PID: %s\nOutput is logging to %s\nYou can use "
            "`check_process_status(pid=%s, log_file=\"%s\")` to monitor it." % (cmd, pid, log_file, pid, log_file))


# ── git auto-commit (ai_git.c) ────────────────────────────────────────────────
_GIT_SENSITIVE = (r"\.env", r"\.pem", r"\.key", r"\.p12", r"\.ssh/", "id_rsa", "id_ed25519",
                  r"\.aws/", r"\.gcloud/", r"\.azure/", r"\.db", r"\.sqlite", r"\.sqlite3")


def _git(*args, capture=False):
    return subprocess.run(["git", "-C", "."] + list(args), stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, text=True)


def git_commit(extra_msg, commit_msg, log):
    """Stage tracked-file modifications and commit; skip on sensitive files or a failing hook,
    restoring the user's index exactly. Silent when nothing changed."""
    import re
    if _git("rev-parse", "--git-dir").returncode != 0:
        return
    saved = (_git("write-tree", capture=True).stdout or "").strip()

    def restore():
        if len(saved) == 40 and all(c in "0123456789abcdef" for c in saved):
            _git("read-tree", saved)
        else:
            _git("reset", "-q", "HEAD")
    _git("add", "-u")
    staged = (_git("diff", "--cached", "--name-only", capture=True).stdout or "").splitlines()
    for pat in _GIT_SENSITIVE:
        if any(re.search(pat, f, re.I) for f in staged):
            log("\033[1;33m[ai] Skipping auto-commit: sensitive pattern matched (%s)\033[0m\n" % pat)
            restore()
            return
    if _git("diff", "--cached", "--quiet").returncode == 0:
        return
    msg = (commit_msg or "auto-commit: tool changes").replace("'", "")[:511]
    full = "%s: %s" % (msg, extra_msg) if extra_msg else msg
    if _git("commit", "-q", "-m", full).returncode == 0:
        log("\033[2m[ai] committed: %s\033[0m\n" % (extra_msg or msg))
    else:
        log("\033[1;33m[ai] auto-commit skipped (commit failed or was blocked by a hook)\033[0m\n")
        restore()
