#!/usr/bin/env python3
"""Benchmark Qwen3.8 Reasoning Modes (xhigh vs medium/normal vs low vs instruct).

Measures:
  1. Reasoning token volume (<think> / reasoning_content token count)
  2. Output token volume (content tokens)
  3. Total latency (TTFT + decode span)
  4. Effective decoding throughput (tok/s)
  5. Speedup and token savings of medium/normal vs xhigh
"""

import json
import time
import urllib.request
import statistics as st
import sys

SERVER_URL = "http://127.0.0.1:8080/v1/chat/completions"

BENCHMARK_PROMPTS = [
    {
        "id": "coding_quickselect",
        "category": "Algorithms & Code",
        "prompt": "Write a python function `quickselect(arr, k)` that finds the k-th smallest element in O(n) average time with type hints, docstring, and 2 unit tests."
    },
    {
        "id": "multi_step_logic",
        "category": "Multi-Step Logic",
        "prompt": "A store gives a 20% discount on an item, then applies an 8% sales tax on the discounted price. If a customer paid $108 in total, what was the original price of the item before discount and tax? Solve step-by-step."
    },
    {
        "id": "agentic_investigation",
        "category": "Agentic Tool Intent",
        "prompt": "Given a Linux server with high disk usage on /var/log, explain how you would inspect the directory, identify the top 3 space-consuming log files, rotate or compress them safely without breaking active services writing to them."
    }
]

MODES = [
    {"name": "xhigh (deep thinking)", "effort": "xhigh", "temp": 1.0, "top_p": 0.95, "enable_thinking": True},
    {"name": "medium / normal",       "effort": "medium", "temp": 1.0, "top_p": 0.95, "enable_thinking": True},
    {"name": "low (fast thinking)",   "effort": "low",    "temp": 1.0, "top_p": 0.95, "enable_thinking": True},
    {"name": "instruct (no thinking)","effort": "none",   "temp": 0.7, "top_p": 0.80, "enable_thinking": False},
]

def query_stream(prompt: str, mode: dict, timeout: int = 120):
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "temperature": mode["temp"],
        "top_p": mode["top_p"],
        "stream": True,
        "max_tokens": 4096,
    }
    if mode.get("effort") and mode["effort"] != "none":
        body["reasoning_effort"] = mode["effort"]
    if not mode.get("enable_thinking", True):
        body["chat_template_kwargs"] = {"enable_thinking": False}

    req = urllib.request.Request(
        SERVER_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )

    t0 = time.time()
    ttft = None
    think_tokens = 0
    content_tokens = 0
    think_text = ""
    content_text = ""

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for line in resp:
            line = line.decode("utf-8").strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            chunk = json.loads(line[6:])
            delta = chunk["choices"][0].get("delta", {})
            now = time.time()

            if delta.get("reasoning_content"):
                if ttft is None:
                    ttft = now - t0
                think_text += delta["reasoning_content"]
                think_tokens += 1
            elif delta.get("content"):
                if ttft is None:
                    ttft = now - t0
                content_text += delta["content"]
                content_tokens += 1

    total_time = time.time() - t0
    total_tokens = think_tokens + content_tokens
    decode_time = total_time - (ttft or 0)
    tok_per_sec = (total_tokens / decode_time) if decode_time > 0 else 0.0

    return {
        "total_time": round(total_time, 2),
        "ttft": round(ttft or 0, 2),
        "think_tokens": think_tokens,
        "content_tokens": content_tokens,
        "total_tokens": total_tokens,
        "tok_per_sec": round(tok_per_sec, 1),
    }

def run_benchmark():
    # Health check
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=3) as resp:
            if resp.status != 200:
                print("Error: llama-server is not running or health check failed.", file=sys.stderr)
                sys.exit(1)
    except Exception as e:
        print(f"Error: llama-server unreachable at http://127.0.0.1:8080/health ({e})", file=sys.stderr)
        sys.exit(1)

    print("=" * 76)
    print("  QWEN 3.8 REASONING MODES BENCHMARK (Thinking Depth vs Speed)")
    print("=" * 76)

    # Warmup
    print("Warming up inference cache...", end="", flush=True)
    query_stream("warmup", MODES[0], timeout=30)
    print(" Done.\n")

    summary_results = []

    for mode in MODES:
        print(f"--- Mode: {mode['name']} ---")
        latencies, think_counts, content_counts, total_counts, speeds = [], [], [], [], []

        for p in BENCHMARK_PROMPTS:
            print(f"  Running [{p['id']}]...", end="", flush=True)
            res = query_stream(p["prompt"], mode)
            latencies.append(res["total_time"])
            think_counts.append(res["think_tokens"])
            content_counts.append(res["content_tokens"])
            total_counts.append(res["total_tokens"])
            speeds.append(res["tok_per_sec"])
            print(f"  Latency: {res['total_time']:5.2f}s | Think: {res['think_tokens']:4d} tok | Output: {res['content_tokens']:4d} tok | Speed: {res['tok_per_sec']:4.1f} tok/s")

        avg_lat = st.mean(latencies)
        avg_think = st.mean(think_counts)
        avg_out = st.mean(content_counts)
        avg_tot = st.mean(total_counts)
        avg_spd = st.mean(speeds)

        summary_results.append({
            "mode": mode["name"],
            "avg_lat": avg_lat,
            "avg_think": avg_think,
            "avg_out": avg_out,
            "avg_tot": avg_tot,
            "avg_spd": avg_spd,
        })
        print(f"  -> Averages: Latency: {avg_lat:.2f}s | Think Tokens: {avg_think:.0f} | Total Tokens: {avg_tot:.0f} | Speed: {avg_spd:.1f} tok/s\n")

    # Comparative Summary Table
    print("=" * 76)
    print("  FINAL REASONING BENCHMARK COMPARISON")
    print("=" * 76)
    print(f"{'Mode':<26} | {'Avg Time':<10} | {'Think Tok':<10} | {'Total Tok':<10} | {'Throughput':<10}")
    print("-" * 76)
    for s in summary_results:
        print(f"{s['mode']:<26} | {s['avg_lat']:>7.2f}s   | {s['avg_think']:>8.0f}   | {s['avg_tot']:>8.0f}   | {s['avg_spd']:>6.1f} tok/s")
    print("=" * 76)

    # Relative Comparison against xhigh
    xhigh_res = summary_results[0]
    med_res = summary_results[1]
    if xhigh_res["avg_lat"] > 0 and med_res["avg_lat"] > 0:
        time_saved = ((xhigh_res["avg_lat"] - med_res["avg_lat"]) / xhigh_res["avg_lat"]) * 100
        think_saved = ((xhigh_res["avg_think"] - med_res["avg_think"]) / xhigh_res["avg_think"]) * 100 if xhigh_res["avg_think"] > 0 else 0
        print(f"\n★ Key Finding: 'medium / normal' reduces latency by {time_saved:.1f}% and cuts reasoning overhead by {think_saved:.1f}% vs xhigh.")

if __name__ == "__main__":
    run_benchmark()
