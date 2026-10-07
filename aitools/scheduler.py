"""Scheduled tasks, reminders and the run-scheduler loop.

Task specs live in the state db (`scheduled_tasks` table, aitools/state.py); a task exists exactly
as long as its row does (deleting the row cancels it). Only the per-task scheduler PID lock files
stay on disk, in ~/.config/ai/scheduled_tasks/<id>.pid.
"""
import os
from .misc import (  # noqa: F401
    _resolve_ai_bin,
)
from . import entry_script as _entry_script
from . import state


def _task_dir():
    """Directory of the scheduler PID lock files (created on demand)."""
    d = os.path.expanduser("~/.config/ai/scheduled_tasks")
    os.makedirs(d, exist_ok=True)
    return d

def schedule_task(task_id, prompt, interval_seconds, run_once=False, extra=None):
    """Schedule a recurring or one-shot background task.

    Extra optional fields (pass via extra={...} or set in extra param):
      max_runs  (int)   — auto-cancel after this many agent invocations (0 = unlimited).
      ttl_hours (float) — auto-cancel this many hours after creation (0 = unlimited).
    """
    import json
    import os
    import subprocess
    import sys
    import time

    safe_task_id = "".join(c for c in task_id if c.isalnum() or c in ("_", "-"))
    if not safe_task_id:
        return "Error: task_id must contain alphanumeric characters, hyphens, or underscores."

    task_dir = _task_dir()
    pid_file  = os.path.join(task_dir, f"{safe_task_id}.pid")

    saved_env = {}
    for k, v in os.environ.items():
        if k.startswith("INFER_") or k.startswith("ZULIP_") or k.startswith("AI_REMINDER_") or k == "PATH":
            saved_env[k] = v

    try:
        existing = state.task_get(safe_task_id)
    except Exception as e:
        return f"Error reading scheduled tasks: {e}"
    existed = existing is not None

    # --- Build or update task data ---
    if existed:
        task_data = existing
        # Preserve creation time and run counters on update
        task_data["prompt"]           = prompt
        task_data["interval_seconds"] = max(10, int(interval_seconds))
        task_data["run_once"]         = bool(run_once)
        task_data["env"]              = saved_env
    else:
        task_data = {
            "task_id":          safe_task_id,
            "prompt":           prompt,
            "interval_seconds": max(10, int(interval_seconds)),
            "run_once":         bool(run_once),
            "created_at":       time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_run":         "never",
            "run_count":        0,
            "fail_count":       0,
            "consec_failures":  0,
            "env":              saved_env,
        }
    if extra:
        task_data.update(extra)
    # Ensure tracking fields exist on old tasks
    for field, default in [("run_count", 0), ("fail_count", 0),
                           ("consec_failures", 0), ("max_runs", 0), ("ttl_hours", 0)]:
        task_data.setdefault(field, default)

    try:
        state.task_put(safe_task_id, task_data,
                       next_run=time.time() + task_data["interval_seconds"])
    except Exception as e:
        return f"Error writing scheduled task: {e}"

    if existed:
        # Check if a scheduler process is still alive for this task
        alive = False
        if os.path.exists(pid_file):
            try:
                pid = int(open(pid_file).read().strip())
                os.kill(pid, 0)   # signal 0 = existence check
                alive = True
            except (ProcessLookupError, PermissionError, ValueError):
                pass   # stale PID
        if alive:
            return (f"Updated scheduled task '{safe_task_id}' "
                    f"(interval={interval_seconds}s, scheduler already running).")
        # Scheduler died without cleaning up — respawn it
        try:
            os.remove(pid_file)
        except Exception:
            pass

    # Spawn a new scheduler daemon
    try:
        cmd = [sys.executable, _entry_script(), "run-scheduler", safe_task_id]
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True
        )
        if existed:
            return (f"Respawned scheduler for dead task '{safe_task_id}' "
                    f"(running every {interval_seconds}s in the background).")
        return (f"Successfully scheduled task '{safe_task_id}' "
                f"(running every {interval_seconds}s in the background).")
    except Exception as e:
        try:
            state.task_delete(safe_task_id)
        except Exception:
            pass
        return f"Error spawning scheduler process: {e}"

def set_reminder(message, when=None, delay_seconds=None, zulip_to=None,
                 zulip_stream=None, zulip_topic=None, task_id=None):
    """Schedule a one-shot reminder delivered directly to Zulip (no LLM spawn).

    Provide either `when` (an ISO 8601 timestamp, e.g. '2026-07-05T09:00:00' —
    convert phrases like 'tomorrow 9am' to ISO yourself using the current time)
    or `delay_seconds` (relative). The reminder is sent to a Zulip DM (`zulip_to`
    email) or a stream (`zulip_stream` + `zulip_topic`). If no recipient is
    given, it falls back to the AI_REMINDER_ZULIP_TO env var (set by the Zulip
    bridge to the requester) so 'remind me ...' just works from chat."""
    import datetime
    import time as _time

    if not message or not str(message).strip():
        return "Error: reminder 'message' is required."

    # Resolve delay
    if delay_seconds is not None:
        try:
            delay = float(delay_seconds)
        except (TypeError, ValueError):
            return "Error: delay_seconds must be a number."
    elif when:
        try:
            when_dt = datetime.datetime.fromisoformat(str(when).replace("Z", "+00:00"))
        except ValueError:
            return (f"Error: could not parse when='{when}'. Use ISO 8601, e.g. "
                    "'2026-07-05T09:00:00'.")
        if when_dt.tzinfo is None:
            now = datetime.datetime.now()
        else:
            now = datetime.datetime.now(when_dt.tzinfo)
        delay = (when_dt - now).total_seconds()
    else:
        return "Error: provide either 'when' (ISO timestamp) or 'delay_seconds'."

    if delay < 5:
        return (f"Error: reminder time is in the past or too soon ({int(delay)}s). "
                "Pick a future time.")

    # Resolve recipient
    zulip_to = zulip_to or os.environ.get("AI_REMINDER_ZULIP_TO")
    if not zulip_to and not zulip_stream:
        return ("Error: no Zulip recipient. Provide zulip_to (email for a DM) or "
                "zulip_stream + zulip_topic. From the Zulip bridge this is filled "
                "in automatically.")
    if zulip_stream and not zulip_topic:
        zulip_topic = "Reminders"

    # Build a unique-ish task id
    if not task_id:
        task_id = "reminder_" + _time.strftime("%Y%m%d_%H%M%S")

    extra = {
        "kind": "reminder",
        "message": str(message),
        "zulip_to": zulip_to,
        "zulip_stream": zulip_stream,
        "zulip_topic": zulip_topic,
        "when": when,
    }
    label = f"[reminder] {str(message)[:60]}"
    result = schedule_task(task_id, label, delay, run_once=True, extra=extra)
    if result.startswith("Error"):
        return result

    # Friendly confirmation with the local delivery time
    fire_at = datetime.datetime.now() + datetime.timedelta(seconds=delay)
    dest = f"DM to {zulip_to}" if zulip_to else f"#{zulip_stream} > {zulip_topic}"
    mins = int(delay // 60)
    when_str = fire_at.strftime("%Y-%m-%d %H:%M")
    return (f"⏰ Reminder set for {when_str} (in ~{mins} min) → {dest}: "
            f"\"{str(message)[:80]}\" (task id: {task_id})")


def _deliver_reminder(task_data):
    """Send a scheduled reminder straight to Zulip. Returns a status string."""
    message = task_data.get("message", "")
    text = f"⏰ **Reminder:** {message}"
    zulip_to = task_data.get("zulip_to")
    zulip_stream = task_data.get("zulip_stream")
    zulip_topic = task_data.get("zulip_topic") or "Reminders"
    try:
        import zulip_mcp_server as zms
        if zulip_stream:
            args = {"message_type": "stream", "to": zulip_stream,
                    "topic": zulip_topic, "content": text}
        else:
            args = {"message_type": "private", "to": zulip_to, "content": text}
        return zms.do_send_message(args)
    except Exception as e:
        # Fallback: desktop notification so the reminder is not silently lost.
        try:
            import subprocess
            subprocess.run(["notify-send", "AI Reminder", message], timeout=10)
            return f"Zulip delivery failed ({e}); sent desktop notification instead."
        except Exception:
            return f"Error: could not deliver reminder: {e}"


def unschedule_task(task_id):
    import os
    import signal
    safe_task_id = "".join(c for c in task_id if c.isalnum() or c in ("_", "-"))
    pid_file = os.path.join(os.path.expanduser("~/.config/ai/scheduled_tasks"), f"{safe_task_id}.pid")

    # Kill the scheduler loop process immediately if alive
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                pid = int(f.read().strip())
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
        try:
            os.remove(pid_file)
        except Exception:
            pass

    try:
        removed = state.task_delete(safe_task_id)
    except Exception as e:
        return f"Error removing scheduled task: {e}"
    if removed:
        return f"Successfully unscheduled/cancelled task '{safe_task_id}'."
    return f"Task '{safe_task_id}' not found or already unscheduled."

def list_scheduled_tasks():
    import os
    try:
        tasks = state.task_list()
    except Exception as e:
        return f"Error reading scheduled tasks: {e}"
    if not tasks:
        return "No scheduled tasks found."

    task_dir = os.path.expanduser("~/.config/ai/scheduled_tasks")
    lines = []
    for data in tasks:
        tid      = data.get("task_id", "?")
        pid_path = os.path.join(task_dir, f"{tid}.pid")
        interval = data.get("interval_seconds", "?")
        created  = data.get("created_at", "?")
        last_run = data.get("last_run", "never")
        prompt   = data.get("prompt", "")
        run_count     = data.get("run_count", "?")
        fail_count    = data.get("fail_count", "?")
        consec_fail   = data.get("consec_failures", 0)
        max_runs      = data.get("max_runs", 0)
        ttl_hours     = data.get("ttl_hours", 0)

        # Check if scheduler process is alive
        scheduler_alive = False
        scheduler_pid   = None
        if os.path.exists(pid_path):
            try:
                pid = int(open(pid_path).read().strip())
                os.kill(pid, 0)
                scheduler_alive = True
                scheduler_pid   = pid
            except Exception:
                pass

        # TTL remaining
        ttl_str = ""
        if ttl_hours > 0 and created != "?":
            try:
                from datetime import datetime
                created_dt = datetime.strptime(created, "%Y-%m-%d %H:%M:%S")
                elapsed_h = (datetime.now() - created_dt).total_seconds() / 3600
                remaining_h = ttl_hours - elapsed_h
                ttl_str = f" | TTL: {remaining_h:.1f}h remaining" if remaining_h > 0 else " | TTL: EXPIRED"
            except Exception:
                pass

        status = (f"[PID {scheduler_pid} ✓ alive]" if scheduler_alive
                  else "[⚠ scheduler DEAD — will respawn on next schedule_task call]")
        lines.append(f"Task ID: {tid}  {status}")
        lines.append(f"  Interval:     {interval}s  |  Created: {created}  |  Last run: {last_run}")
        lines.append(f"  Runs: {run_count}  |  Failures: {fail_count}  |  Consec fails: {consec_fail}"
                     + (f"  |  max_runs: {max_runs}" if max_runs else "") + ttl_str)
        lines.append(f"  Prompt:       {prompt[:120]}")
        lines.append("-" * 60)

    return "\n".join(lines) if lines else "No scheduled tasks found."

DB_BACKOFF_CAP = 300.0


def _db_backoff_delay(n, cap=DB_BACKOFF_CAP):
    """Sleep before retry #n (1-based) when the state db is unusable: 1, 2, 4, ... capped."""
    return float(min(cap, 2 ** (max(1, n) - 1)))


def run_scheduler_loop(task_id):
    import time
    import json
    import os
    import signal
    import subprocess
    from datetime import datetime

    safe_task_id = "".join(c for c in task_id if c.isalnum() or c in ("_", "-"))
    if not safe_task_id:
        return

    task_dir  = _task_dir()
    pid_file  = os.path.join(task_dir, f"{safe_task_id}.pid")

    def task_exists():
        """The row is the task: gone = cancelled. A db error counts as 'still there'."""
        try:
            return state.task_exists(safe_task_id)
        except Exception as e:
            log_message(f"Error checking task: {e}")
            return True

    def remove_task():
        try:
            state.task_delete(safe_task_id)
        except Exception as e:
            log_message(f"Error deleting task: {e}")

    cache_dir = os.path.expanduser("~/.cache/ai")
    os.makedirs(cache_dir, exist_ok=True)
    log_file = os.path.join(cache_dir, "scheduler.log")

    def log_message(msg):
        try:
            t = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(log_file, "a") as lf:
                lf.write(f"[{t}] [Task: {safe_task_id}] {msg}\n")
        except Exception:
            pass

    # ── PID lock: prevent duplicate scheduler processes ─────────────────────
    my_pid = os.getpid()
    if os.path.exists(pid_file):
        try:
            old_pid = int(open(pid_file).read().strip())
            if old_pid != my_pid:
                try:
                    os.kill(old_pid, 0)   # check if alive
                    log_message(f"Another scheduler (PID {old_pid}) already running. Exiting.")
                    return   # defer to the older process
                except (ProcessLookupError, PermissionError):
                    log_message(f"Stale PID file (PID {old_pid} dead). Taking over.")
        except (ValueError, OSError):
            pass

    try:
        with open(pid_file, "w") as pf:
            pf.write(str(my_pid))
    except Exception as e:
        log_message(f"Warning: could not write PID file: {e}")

    def cleanup_pid():
        try:
            if os.path.exists(pid_file):
                stored = int(open(pid_file).read().strip())
                if stored == my_pid:
                    os.remove(pid_file)
        except Exception:
            pass

    def interruptible_sleep(seconds):
        """Sleep for `seconds`, checking every 5s if the task file is gone."""
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True   # continue
            time.sleep(min(remaining, 5.0))
            if not task_exists():
                return False  # cancelled

    log_message(f"Scheduler loop started (PID {my_pid}).")
    db_errors = 0   # consecutive failures to read the task from the state db

    # ── Main loop ────────────────────────────────────────────────────────────
    while True:
        try:
            task_data = state.task_get(safe_task_id)
        except Exception as e:
            db_errors += 1
            delay = _db_backoff_delay(db_errors)
            log_message(f"State db unavailable ({e}); retry #{db_errors} in {delay:g}s.")
            time.sleep(delay)
            continue
        db_errors = 0
        if task_data is None:
            log_message("Task deleted. Exiting scheduler loop.")
            break

        interval        = int(task_data.get("interval_seconds", 300))
        prompt          = task_data.get("prompt", "")
        saved_env       = task_data.get("env", {})
        run_once        = task_data.get("run_once", False)
        max_runs        = int(task_data.get("max_runs", 0))     # 0 = unlimited
        ttl_hours       = float(task_data.get("ttl_hours", 0))  # 0 = unlimited
        run_count       = int(task_data.get("run_count", 0))
        consec_failures = int(task_data.get("consec_failures", 0))
        created_at      = task_data.get("created_at", "")
        MAX_CONSEC_FAIL = 3   # auto-retire after this many back-to-back failures

        # ── Guard: max_runs limit ────────────────────────────────────────────
        if max_runs > 0 and run_count >= max_runs:
            log_message(f"max_runs={max_runs} reached ({run_count} runs). Auto-cancelling task.")
            remove_task()
            break

        # ── Guard: TTL expiry ────────────────────────────────────────────────
        if ttl_hours > 0 and created_at:
            try:
                created_dt = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
                elapsed_h  = (datetime.now() - created_dt).total_seconds() / 3600
                if elapsed_h >= ttl_hours:
                    log_message(f"TTL expired ({elapsed_h:.1f}h >= {ttl_hours}h). Auto-cancelling task.")
                    remove_task()
                    break
            except Exception:
                pass

        # ── Guard: consecutive failure limit ─────────────────────────────────
        if consec_failures >= MAX_CONSEC_FAIL:
            log_message(f"consec_failures={consec_failures} >= {MAX_CONSEC_FAIL}. Auto-retiring task.")
            remove_task()
            break

        # ── Interruptible sleep ──────────────────────────────────────────────
        try:  # ai.c shows the countdown to the next run from this column
            state.task_set_next_run(safe_task_id, time.time() + interval)
        except Exception:
            pass
        if not interruptible_sleep(interval):
            log_message("Task deleted during sleep. Exiting.")
            break

        # Re-read after sleep (may have been updated)
        try:
            fresh = state.task_get(safe_task_id)
        except Exception:
            fresh = task_data
        if fresh is None:
            log_message("Task gone after sleep. Exiting.")
            break
        task_data = fresh

        # ── run_once: delete before spawning so crash can't repeat ───────────
        if run_once:
            remove_task()
            log_message("run_once: task removed before agent spawn.")

        # ── Reminder shortcut (direct Zulip, no LLM) ─────────────────────────
        if task_data.get("kind") == "reminder":
            _saved_env = dict(os.environ)
            try:
                os.environ.update({k: v for k, v in saved_env.items() if isinstance(v, str)})
                status = _deliver_reminder(task_data)
            finally:
                os.environ.clear()
                os.environ.update(_saved_env)
            log_message(f"Reminder delivered: {status}")
            if run_once or not task_exists():
                log_message("Reminder run_once: exiting.")
                break
            continue

        # ── Update run metadata ───────────────────────────────────────────────
        if not run_once and task_exists():
            try:
                task_data["last_run"]  = time.strftime("%Y-%m-%d %H:%M:%S")
                task_data["run_count"] = run_count + 1
                state.task_update(safe_task_id, task_data)
            except Exception as e:
                log_message(f"Error updating task metadata: {e}")

        # ── Spawn agent ──────────────────────────────────────────────────────
        ai_bin   = _resolve_ai_bin()
        cmd      = [ai_bin, "-y", "-q", prompt]
        run_env  = os.environ.copy()
        run_env.update(saved_env)

        # Use INFER_TASK_TIMEOUT if set, else 15 min per sub-agent run.
        # This is distinct from the scheduler-level interval — it caps how long
        # a single agent invocation may run before we consider it stuck.
        try:
            agent_timeout = int(run_env.get("INFER_TASK_TIMEOUT", 0)) or 900
        except Exception:
            agent_timeout = 900

        success = False
        try:
            log_message(f"Spawning: {cmd}")
            proc = subprocess.run(
                cmd, env=run_env, capture_output=True, text=True,
                timeout=agent_timeout
            )
            rc = proc.returncode
            out_snippet = (proc.stdout or proc.stderr or "").strip()[:300]
            if rc == 0:
                log_message(f"Agent finished successfully. Output: {out_snippet}")
                success = True
            else:
                log_message(f"Agent failed (exit {rc}). Stderr: {out_snippet}")
        except subprocess.TimeoutExpired:
            log_message(f"Agent timed out after {agent_timeout}s — treating as failure.")
        except Exception as e:
            log_message(f"Error running agent: {e}")

        # ── Update failure counters ───────────────────────────────────────────
        if not run_once and task_exists():
            try:
                task_data = state.task_get(safe_task_id) or task_data
                if success:
                    task_data["consec_failures"] = 0
                else:
                    task_data["fail_count"]       = int(task_data.get("fail_count", 0)) + 1
                    task_data["consec_failures"]  = int(task_data.get("consec_failures", 0)) + 1
                    task_data["last_error"]        = time.strftime("%Y-%m-%d %H:%M:%S")
                state.task_update(safe_task_id, task_data)
            except Exception as e:
                log_message(f"Error persisting failure counters: {e}")

        # ── Exit if task was cancelled during agent run ───────────────────────
        if run_once or not task_exists():
            log_message("Task gone after agent run. Exiting scheduler loop.")
            break

    # ── Cleanup ───────────────────────────────────────────────────────────────
    cleanup_pid()
    log_message("Scheduler loop exited cleanly.")
