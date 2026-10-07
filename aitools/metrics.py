"""Per-tool call metrics (log_metric/show_metrics) and token counting."""
import os
import json
import re as _re

def log_metric(tool_name, duration_ms, success=True, error=None):
    if os.environ.get("INFER_PRIVATE_MODE") in ("1", "true"):
        return
    try:
        import datetime
        metrics_file = os.path.expanduser("~/.cache/ai/metrics.jsonl")
        os.makedirs(os.path.dirname(metrics_file), exist_ok=True)
        entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "tool": tool_name,
            "duration_ms": round(duration_ms, 2),
            "success": success
        }
        if not success and error:
            entry["error"] = str(error)[:200]
        with open(metrics_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass

def show_metrics():
    metrics_file = os.path.expanduser("~/.cache/ai/metrics.jsonl")
    if not os.path.isfile(metrics_file):
        print("No metrics logged yet.")
        return
    
    stats = {}
    total_calls = 0
    try:
        with open(metrics_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    t = data.get("tool", "unknown")
                    dur = data.get("duration_ms", 0)
                    succ = data.get("success", True)
                    total_calls += 1
                    if t not in stats:
                        stats[t] = {"count": 0, "success": 0, "total_ms": 0.0,
                                    "errors": {}}
                    stats[t]["count"] += 1
                    if succ:
                        stats[t]["success"] += 1
                    else:
                        e = str(data.get("error", "")).strip().lower()
                        key = _re.sub(r"\d+", "#", e) or "error"
                        stats[t]["errors"][key] = stats[t]["errors"].get(key, 0) + 1
                    stats[t]["total_ms"] += dur
                except Exception:
                    pass
    except Exception as e:
        print(f"Error reading metrics: {e}")
        return

    print("=== AI Buddy Metrics & Tool Usage Statistics ===")
    print(f"Total Tool Executions Logged: {total_calls}\n")
    print(f"{'Tool Name':<28} | {'Calls':<7} | {'Success':<8} | {'Avg Time (ms)':<14}")
    print("-" * 65)
    for t, s in sorted(stats.items(), key=lambda x: x[1]["count"], reverse=True):
        avg_ms = round(s["total_ms"] / s["count"], 1) if s["count"] > 0 else 0.0
        succ_rate = f"{s['success']}/{s['count']}"
        flaky = ""
        fails = s["count"] - s["success"]
        if s["count"] >= 5 and fails / s["count"] >= 0.3:
            flaky = "  [FLAKY]"
        print(f"{t:<28} | {s['count']:<7} | {succ_rate:<8} | {avg_ms:<14}{flaky}")
    # Top recurring errors (tool health — what to fix first)
    err_rows = []
    for t, s in stats.items():
        for e, n in s.get("errors", {}).items():
            if n >= 2:
                err_rows.append((n, t, e[:70]))
    if err_rows:
        print("\nRecurring tool errors (fix these first):")
        for n, t, e in sorted(err_rows, reverse=True)[:8]:
            print(f"  {n}x {t}: {e}")

def count_tokens(model, text):
    if not text:
        return "0"
    try:
        if "gpt" in model.lower():
            import tiktoken
            enc = tiktoken.encoding_for_model(model)
            return str(len(enc.encode(text)))
        else:
            return str(len(text) // 4)
    except Exception:
        return str(len(text) // 4)
