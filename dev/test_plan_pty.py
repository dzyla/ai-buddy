#!/usr/bin/env python3
"""Drive the interactive PLAN-approval flow in a real pty and confirm the agent
only writes the file AFTER the user approves the presented plan."""
import os, pty, re, select, sys, time, subprocess

tc = sys.argv[1] if len(sys.argv) > 1 else "/tmp/plan_pty_test.txt"
approval = sys.argv[2] if len(sys.argv) > 2 else "y"  # 'y' approve, 'n' reject

if os.path.exists(tc):
    os.remove(tc)

argv = ["./ai", "-i", "--plan", f"Create {tc} containing the text pty_approved using write_file."]
pid, fd = pty.fork()
if pid == 0:
    os.execv("./ai", argv)
    os._exit(1)

os.set_blocking(fd, False)
out = b""
start = time.time()
approved_prompted = False
done = False
kill_at = time.time() + 120
while time.time() < kill_at:
    r, _, _ = select.select([fd], [], [], 0.4)
    if r:
        try:
            data = os.read(fd, 4096)
        except OSError:
            break
        if not data:
            break
        out += data
        sys.stdout.buffer.write(data)
        sys.stdout.flush()
        # When we see the CONFIRM prompt, send the approval
        if b"CONFIRM: APPROVE PLAN" in out or b"APPROVE PLAN" in out or b"APPROVE?" in out or b"Proceed?" in out:
            if not approved_prompted:
                approved_prompted = True
                os.write(fd, (approval + "\n").encode())
    if b"session ended" in out or b"Resume" in out.lower():
        done = True
        break
    if time.time() - start > 60 and b"write_file" in out and b"CONFIRM" not in out:
        # Probably looped; stop
        break

try:
    os.kill(pid, 9)
except Exception:
    pass
try:
    os.waitpid(pid, 0)
except Exception:
    pass

exists = os.path.exists(tc)
content = ""
if exists:
    with open(tc) as f:
        content = f.read().strip()
print("\n==== RESULT ====")
print(f"file_exists={exists} content={content!r}")
print(f"approval_prompt_seen={approved_prompted}")
print(f"mode={approval}")
sys.exit(0)