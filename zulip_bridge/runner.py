"""Running the local `ai` agent in its own process group, and recovering a reply from its session file."""
import os
import json
import signal
import logging
import subprocess

from .text import clean_response


logger = logging.getLogger("ZulipBridge")

def _kill_process_group(proc):
    """SIGKILL the whole process group of `proc` (ai + its tool subprocesses)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except Exception:
            pass


def run_ai_subprocess(cmd, env=None, timeout=None, input=None):
    """Run the `ai` agent in its own session/process group.

    Unlike subprocess.run(timeout=...), which only kills the direct child,
    a timeout here kills every process the agent spawned (ai_mcp.py tool
    calls, execute_command shells), so timed-out jobs leave no orphans.
    stdin is /dev/null unless `input` is given, so the agent never grabs the
    bridge's terminal (raw-mode keyboard polling) when run interactively.
    """
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)
    except BaseException:
        _kill_process_group(proc)
        proc.wait()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def extract_session_fallback(last_sess_path=None, raw_stdout="", raw_stderr="", min_mtime=None):
    """
    Extract meaningful information from the session file and logs when stdout is empty.
    Checks:
    1. Direct assistant content in last session
    2. Assistant reasoning_content (cleaned up)
    3. Tool execution results in the session
    4. Subprocess stderr

    If `min_mtime` is given, session files last modified before it are
    ignored: they belong to an earlier, unrelated run (e.g. when `ai` crashed
    before saving), and replaying them would answer with a stale conversation.
    """
    if last_sess_path:
        candidates = [last_sess_path]
    else:
        candidates = [
            os.path.expanduser("~/.cache/ai/sessions/last.json"),
            os.path.expanduser("~/.local/share/ai/sessions/last.json")
        ]

    cleaned_stdout = clean_response(raw_stdout)
    if cleaned_stdout:
        return cleaned_stdout, "stdout", None

    for cand_path in candidates:
        if os.path.isfile(cand_path):
            if min_mtime is not None:
                try:
                    if os.path.getmtime(cand_path) < min_mtime:
                        continue
                except OSError:
                    continue
            try:
                with open(cand_path, "r", encoding="utf-8") as sf:
                    sdata = json.load(sf)
                msgs = sdata if isinstance(sdata, list) else sdata.get("messages", [])

                for m in reversed(msgs):
                    if m.get("role") == "assistant":
                        c = m.get("content")
                        if c and isinstance(c, str) and c.strip() and c.strip() != "None":
                            return clean_response(c), "fallback_content", None

                        rc = m.get("reasoning_content")
                        if rc and isinstance(rc, str) and len(rc.strip()) > 30:
                            clean_rc = clean_response(rc)
                            if clean_rc:
                                return f"*(Response extracted from agent reasoning)*\n\n{clean_rc}", "fallback_reasoning", clean_rc

                        tcs = m.get("tool_calls", [])
                        if tcs:
                            tool_names = [t.get("function", {}).get("name", "tool") for t in tcs if isinstance(t, dict)]
                            tool_outputs = []
                            for tm in msgs:
                                if tm.get("role") == "tool":
                                    tc_out = tm.get("content", "")
                                    if tc_out and len(tc_out.strip()) > 0:
                                        tool_outputs.append(tc_out.strip()[:400])
                            out_summary = f"✅ Agent completed actions ({', '.join(tool_names)})."
                            if tool_outputs:
                                out_summary += f"\n\n**Tool Output Summary:**\n```\n" + "\n---\n".join(tool_outputs[:3]) + "\n```"
                            return out_summary, "fallback_tools", None
            except Exception as ex:
                logger.warning(f"Session fallback extraction failed on {cand_path}: {ex}")

    if raw_stderr and raw_stderr.strip():
        err_clean = clean_response(raw_stderr)
        return f"⚠️ **Agent encountered an error:**\n```\n{err_clean[:2000]}\n```", "error", None

    return (
        "⚠️ **Agent returned no text output.**\n\n"
        "*(The local AI model finished without emitting content or tool calls. "
        "Use `/debug` or `/history` to inspect the chat memory, or switch reasoning effort with `ai-backend reasoning medium`.)*",
        "empty_output",
        None
    )
