"""Persistence and process context: session save (+ persistent mirror, atomic), -r resume through
aitools.sessions.session_transcript, the job history log, cache pruning, piped stdin + its writer."""
import json
import os
import sys
import time


def _dirs():
    home = os.environ.get("HOME")
    if not home:
        return None, None
    cache = os.path.join(home, ".cache", "ai", "sessions")
    share = os.path.join(home, ".local", "share", "ai", "sessions")
    return cache, share


def _path(d, session_id):
    os.makedirs(d, mode=0o700, exist_ok=True)
    return os.path.join(d, "%s.json" % session_id if session_id else "last.json")


def _atomic_write(path, data):
    tmp = "%s.tmp.%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8", errors="replace") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def dumps(messages):
    return json.dumps(messages, ensure_ascii=False, separators=(",", ":"))


def save(messages, session_id, private):
    """cache/<id>.json, share/<id>.json, cache/last.json, share/last.json (nothing in private mode)."""
    if private or messages is None:
        return
    cache, share = _dirs()
    if not cache:
        return
    data = dumps(messages)
    for d, sid in ((cache, session_id), (share, session_id), (cache, None), (share, None)):
        try:
            _atomic_write(_path(d, sid), data)
        except OSError:
            pass


def find(resume_id):
    """The saved session file for -r (cache first, then the persistent mirror), or None."""
    cache, share = _dirs()
    if not cache:
        return None
    for d in (cache, share):
        try:
            p = _path(d, resume_id)
        except OSError:
            continue
        if os.access(p, os.R_OK):
            return p
    if resume_id and resume_id.endswith(".json") and os.access(resume_id, os.R_OK):
        return resume_id  # an explicit file path (ai.c would look for <dir>/<path>.json)
    return None


def transcript(resume_id, log):
    """Clean user/assistant messages of the session to resume (see ai_session.c)."""
    p = find(resume_id)
    if not p:
        if resume_id:
            log("\033[2m[ai] --resume: session '%s' not found.\033[0m\n" % resume_id)
        else:
            log("\033[2m[ai] --resume: no previous session found.\033[0m\n")
        return []
    try:
        with open(p, encoding="utf-8") as f:
            msgs = json.load(f)
        from aitools.sessions import session_transcript
        out = [m for m in session_transcript(msgs) if isinstance(m, dict)]
    except Exception:
        return []
    if out:
        log("\033[2m[ai] --resume: restored %d message(s) from last session.\033[0m\n" % len(out))
    return out


def log_job(prompt, pipe_writer, response, interactive, session_id, private):
    if private:
        return
    home = os.environ.get("HOME")
    if not home:
        return
    d = os.path.join(home, ".cache", "ai")
    try:
        os.makedirs(d, exist_ok=True)
        rec = {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime()),
               "session_id": session_id or "unknown", "prompt": prompt or "",
               "pipe_writer": pipe_writer or "", "interactive": bool(interactive), "response": response or ""}
        with open(os.path.join(d, "history.jsonl"), "a", encoding="utf-8", errors="replace") as f:
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


def prune():
    """`ai_mcp.py prune-sessions` (archive-first bounded cache retention); never raises."""
    try:
        import sqlite3
        from aitools.history import HISTORY_DB, _ensure_history_schema, _prune_cache_sessions
        os.makedirs(os.path.dirname(HISTORY_DB), exist_ok=True)
        conn = sqlite3.connect(HISTORY_DB)
        try:
            _ensure_history_schema(conn)
            _prune_cache_sessions(conn)
        finally:
            conn.close()
    except Exception:
        pass


def pipe_writer(exclude=()):
    """Command line of the process writing our piped stdin (/proc scan), else None. `exclude`: pids
    sharing the pipe that are not the writer (this engine and the C front-end)."""
    try:
        if os.isatty(0):
            return None
        target = os.readlink("/proc/self/fd/0")
    except OSError:
        return None
    if not target.startswith("pipe:["):
        return None
    skip = {os.getpid()} | set(exclude)
    try:
        pids = sorted(int(p) for p in os.listdir("/proc") if p.isdigit())
    except OSError:
        return None
    for pid in pids:
        if pid in skip:
            continue
        fdd = "/proc/%d/fd" % pid
        try:
            fds = os.listdir(fdd)
        except OSError:
            continue
        hit = False
        for fd in fds:
            try:
                if os.readlink(os.path.join(fdd, fd)) == target:
                    hit = True
                    break
            except OSError:
                continue
        if not hit:
            continue
        try:
            with open("/proc/%d/cmdline" % pid, "rb") as f:
                raw = f.read(4095)
        except OSError:
            return None
        text = raw.replace(b"\0", b" ").rstrip(b" \n\0").decode("utf-8", "replace")
        return text or None
    return None


def read_stdin():
    try:
        if os.isatty(0):
            return None
    except OSError:
        return None
    try:
        data = sys.stdin.buffer.read()
    except (OSError, ValueError):
        return None
    return data.decode("utf-8", "replace")
