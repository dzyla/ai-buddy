#!/usr/bin/env python3
"""Deterministic test for the harness --tool--loop hard-stop.

Feeds the `ai` binary a mock LLM that keeps replying with the *identical*
tool call `execute_command {"command":"echo hi"}` 10 times in a row (the exact
degenerate loop from the user's failing session), then finally task_complete.

The harness must abort when the identical (tool,args) repeat crosses
INFER_TOOL_LOOP_CAP (default 6) instead of looping forever under -c.
"""
import os, json, subprocess, sys, tempfile, time, urllib.request, signal

PORT = 8799
SEQ = [{"id": f"loop_{i}", "type": "function",
        "function": {"name": "execute_command",
                     "arguments": json.dumps({"command": "echo hi"})}}
       for i in range(10)]

env = os.environ.copy()
env["MOCK_TOOL_SEQ"] = json.dumps(SEQ)
env["MOCK_TASK_COMPLETE"] = "DONE-after-loop"
server = subprocess.Popen(["/home/dzyla/miniconda3/bin/python3", "tests/mock_llm_server.py", str(PORT)],
                          env=env, cwd="/home/dzyla/Code/ai-buddy")
try:
    # wait for readiness
    for _ in range(30):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/v1/models", timeout=2)
            break
        except Exception:
            time.sleep(0.2)
    else:
        print("FAIL: mock server never came up"); sys.exit(1)

    r = subprocess.run(
        ["/home/dzyla/Code/ai-buddy/ai", "-q", "", "-n", "do the task"],
        env=env | {"INFER_BASE_URL": f"http://127.0.0.1:{PORT}/v1/",
                   "INFER_API_KEY": "x", "INFER_MODEL": "mock",
                   "INFER_TOOL_CHOICE": "auto",
                   "INFER_STEP_LIMIT": "60",
                   # ai.c's load_env_file() setenv(overwrite=1) pulls values from
                   # ~/.local/share/ai/env (points at the REAL server). Point HOME
                   # at an empty dir so the mock is actually targeted.
                   "HOME": "/tmp/ai-tool-loop-empty-home"},
        capture_output=True, text=True, timeout=60,
        cwd="/tmp")
    out = (r.stdout + r.stderr)
    print("EXIT:", r.returncode)
    print("STDOUT tail:", r.stdout[-800:])
    print("STDERR tail:", r.stderr[-600:])
    # exact identical tool call. The same_tool_count guard fires at >=2 with a
    # warning, and hard-stops at cap (6). Count occurrences of the error marker.
    warn = out.count("stuck in a loop calling the exact same tool")
    abort = out.count("Infinite tool loop: identical tool+args")
    hardstop = out.count("[HARD STOP]")
    print(f"\nloop_warnings={warn} hard_abort={abort} hardstop_msgs={hardstop}")
    if abort >= 1:
        print("PASS: harness hard-stopped the degenerate tool loop")
    else:
        print("FAIL: harness did NOT hard-stop the tool loop")
    sys.exit(0 if abort >= 1 else 1)
finally:
    server.terminate()
    try: server.wait(timeout=5)
    except Exception: server.kill()