"""Local command execution, Python/JS playgrounds and background-process management."""
import sys
import os
import signal
import json
import subprocess
from .mcp_client import (  # noqa: F401
    _cleanup_proc,
)
from .memory import (  # noqa: F401
    append_to_context_pool,
)

def execute_command(command, timeout=120):
    if not command or not command.strip():
        return "Error: empty command"
    try:
        if isinstance(timeout, str):
            timeout = int(timeout)
    except ValueError:
        timeout = 120
    # Allow the harness to raise/lower the command timeout globally.
    env_timeout = os.environ.get("INFER_COMMAND_TIMEOUT")
    if env_timeout:
        try:
            timeout = int(env_timeout) or timeout
        except ValueError:
            pass
    try:
        # start_new_session puts the command (and everything it spawns) in its
        # own process group, so a timeout can kill the WHOLE tree — not just the
        # direct /bin/sh -c wrapper (whose grandchildren would otherwise leak as
        # orphaned subprocesses and keep burning CPU).
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            out_b, err_b = proc.communicate(timeout=timeout)
            out = (out_b or "") + (err_b or "")
            if proc.returncode == 0:
                res = f"success\n{out}" if out else "success"
            else:
                res = f"failed (exit {proc.returncode})\n{out}\n[SYSTEM WARNING: Command failed. You MUST use the `think` tool to analyze the failure, explain why it failed, and formulate a new plan before executing another command.]"
        except subprocess.TimeoutExpired:
            # Kill the ENTIRE process group so grandchildren don't survive.
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            return f"Error: Command timed out after {timeout} seconds."
        append_to_context_pool(res[:1000])
        return res
    except Exception as e:
        return f"Error executing command: {e}"

def python_playground(code: str, timeout: int = 30) -> str:
    """Execute Python code in an isolated sandbox with stdout/stderr and expression value capture."""
    if not code or not code.strip():
        return "Error: No Python code provided."
    try:
        if isinstance(timeout, str):
            timeout = int(timeout)
    except ValueError:
        timeout = 30

    wrapper = f"""import sys, io, ast

_code = {repr(code)}

try:
    _parsed = ast.parse(_code)
    _ns = {{"__name__": "__main__"}}
    if _parsed.body and isinstance(_parsed.body[-1], ast.Expr):
        _last_expr = _parsed.body.pop()
        if _parsed.body:
            exec(compile(_parsed, "<playground>", "exec"), _ns)
        _res = eval(compile(ast.Expression(_last_expr.value), "<playground>", "eval"), _ns)
        if _res is not None:
            print(repr(_res))
    else:
        exec(compile(_parsed, "<playground>", "exec"), _ns)
except Exception:
    import traceback
    traceback.print_exc()
"""
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", wrapper],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True
        )
        try:
            out, err = proc.communicate(timeout=timeout)
            res = ""
            if out:
                res += out.rstrip()
            if err:
                if res:
                    res += "\n[stderr]: " + err.rstrip()
                else:
                    res = err.rstrip()
            if proc.returncode != 0 and not err and not out:
                res = f"Exited with code {proc.returncode}"
            return res if res else "(executed with no output)"
        finally:
            _cleanup_proc(proc)
    except subprocess.TimeoutExpired:
        return f"Error: Python execution timed out after {timeout} seconds."
    except Exception as e:
        return f"Error running Python playground: {e}"

def js_playground(code: str, timeout: int = 30) -> str:
    """Execute JavaScript/Node.js code in a sandboxed runner with console output and evaluation results."""
    if not code or not code.strip():
        return "Error: No JavaScript code provided."
    try:
        if isinstance(timeout, str):
            timeout = int(timeout)
    except ValueError:
        timeout = 30

    import shutil
    node_bin = shutil.which("node")
    if not node_bin:
        return "Error: Node.js runtime ('node') is not installed in PATH."

    wrapper = f"""const vm = require('vm');
const util = require('util');

const code = {json.dumps(code)};

(async () => {{
    try {{
        const sandbox = {{
            console,
            process: {{ env: {{}}, cwd: process.cwd, stdout: process.stdout, stderr: process.stderr }},
            setTimeout, clearTimeout, setInterval, clearInterval,
            Buffer, URL, URLSearchParams, TextEncoder, TextDecoder,
            fetch: typeof fetch !== 'undefined' ? fetch : undefined
        }};
        vm.createContext(sandbox);
        const result = await vm.runInContext(code, sandbox, {{ timeout: {timeout * 1000} }});
        if (result !== undefined) {{
            console.log(util.inspect(result, {{ depth: 4, colors: false }}));
        }}
    }} catch (err) {{
        console.error(err && err.stack ? err.stack : String(err));
        process.exit(1);
    }}
}})();
"""
    try:
        proc = subprocess.Popen(
            [node_bin, "-e", wrapper],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True
        )
        try:
            out, err = proc.communicate(timeout=timeout)
            res = ""
            if out:
                res += out.rstrip()
            if err:
                if res:
                    res += "\n[stderr]: " + err.rstrip()
                else:
                    res = err.rstrip()
            if proc.returncode != 0 and not err and not out:
                res = f"Exited with code {proc.returncode}"
            return res if res else "(executed with no output)"
        finally:
            _cleanup_proc(proc)
    except subprocess.TimeoutExpired:
        return f"Error: JavaScript execution timed out after {timeout} seconds."
    except Exception as e:
        return f"Error running JavaScript playground: {e}"


def list_processes():
    """Return the top 10 running processes sorted by CPU usage."""
    import subprocess as _sp
    try:
        out = _sp.run(
            ["ps", "aux", "--sort=-%cpu"],
            capture_output=True, text=True, check=True, timeout=10
        )
        lines = out.stdout.strip().splitlines()
        header = lines[0] if lines else ""
        body = lines[1:11]  # top 10 (excluding header)
        if not body:
            return "No running processes found."
        result_lines = [header] + body
        return "\n".join(result_lines)
    except Exception as e:
        return f"Error listing processes: {e}"

def start_background_process(command, log_file=None):
    """Start a command in the background, writing output to a log file for status monitoring."""
    import subprocess, os, time
    if not command:
        return "Error: command string is required."
    if not log_file:
        log_dir = os.path.expanduser("~/.config/ai/logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f"proc_{int(time.time())}.log")
    else:
        os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
    
    try:
        with open(log_file, "a") as f:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=f,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True
            )
        return (f"[Background Process Started]\n"
                f"PID: {proc.pid}\n"
                f"Command: `{command}`\n"
                f"Log File: `{log_file}`\n"
                f"Use `check_process_status` with pid={proc.pid} or log_file='{log_file}' to evaluate execution health and logs.")
    except Exception as e:
        return f"Error starting background process: {e}"

def check_process_status(pid=None, log_file=None):
    """Inspect process state, read log tail, evaluate execution health, and return a decision summary."""
    import os
    if not pid and not log_file:
        return "Error: provide at least 'pid' or 'log_file' to check status."

    pid_status = "UNKNOWN"
    if pid is not None:
        try:
            pid_int = int(pid)
            os.kill(pid_int, 0)
            pid_status = "RUNNING"
        except ProcessLookupError:
            pid_status = "TERMINATED/EXITED"
        except PermissionError:
            pid_status = "RUNNING (active)"
        except Exception as e:
            pid_status = f"ERROR ({e})"

    log_tail = ""
    target_log = log_file
    if target_log and os.path.isfile(target_log):
        try:
            with open(target_log, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                log_tail = "".join(lines[-50:])
        except Exception as ex:
            log_tail = f"Error reading log file: {ex}"
    elif pid is not None:
        log_dir = os.path.expanduser("~/.config/ai/logs")
        if os.path.isdir(log_dir):
            for fname in sorted(os.listdir(log_dir), reverse=True):
                if fname.endswith(".log"):
                    fpath = os.path.join(log_dir, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read(4096)
                            if f"PID: {pid}" in content or fname.startswith("proc_"):
                                target_log = fpath
                                log_tail = content[-2000:]
                                break
                    except Exception:
                        pass

    health = "HEALTHY"
    if any(term in log_tail for term in ["Error", "Exception", "FAILED", "Traceback", "CRITICAL"]):
        health = "NEEDS ATTENTION (Errors detected in log output)"
    elif pid_status == "TERMINATED/EXITED":
        health = "FINISHED"

    out = [
        f"[Process Health Evaluation Report | PID: {pid if pid is not None else 'N/A'}]",
        f"State: {pid_status}",
        f"Health Verdict: {health}",
        f"Log File: `{target_log if target_log else 'None'}`"
    ]
    if log_tail:
        out.append(f"--- Log Tail ---\n{log_tail.strip()}")
    else:
        out.append("No log output recorded yet.")

    return "\n".join(out)

def stop_process(pid):
    """Terminate a running background process by PID."""
    import os, signal, time
    try:
        pid_int = int(pid)
        os.kill(pid_int, signal.SIGTERM)
        time.sleep(0.2)
        try:
            os.kill(pid_int, 0)
            os.kill(pid_int, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return f"Successfully stopped process PID {pid_int}."
    except ProcessLookupError:
        return f"Process PID {pid} is not running."
    except Exception as e:
        return f"Error stopping process PID {pid}: {e}"
