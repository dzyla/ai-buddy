#!/usr/bin/env python3
"""Streaming speed probe for a live llama-server.

Reproduces the paired A/B method from sudoingX/qwen38-mtp:
  * clocks every generated token (content AND reasoning deltas) against the
    OpenAI-compatible endpoint,
  * discards TTFT (time-to-first-token) so the number reflects pure decode
    speed (what MTP/draft-mtp actually accelerates),
  * warms up, then runs N prompts x R runs and reports medians.

Run it once against a baseline serve and once with the MTP flag, everything
else identical. The paired delta is the honest number.

Usage:
    python3 dev/probe_mtp.py [server_url] [opts]
    python3 dev/probe_mtp.py http://127.0.0.1:8080 --runs 3 --max-tokens 400
    python3 dev/probe_mtp.py http://127.0.0.1:8080 --thinking   # measure CoT path too

Defaults match the repo's measured recipe (thinking off, 131K resident, 400
max tokens, 3x3).
"""
import argparse
import json
import statistics as st
import sys
import time
import urllib.request

DEFAULT_URL = "http://127.0.0.1:8080"

# Three prompts mixing code and prose, as in the reference recipe.
PROMPTS = [
    "write a python function that merges two sorted lists into one sorted list, with docstring.",
    "explain the difference between mmap and read for loading large files, one paragraph.",
    "write a bash script that watches a directory and prints new files as they appear.",
]


def run_once(url, prompt, max_tokens=400, thinking=False, timeout=600):
    """Stream one prompt; return (tok/s, n_tokens, ttft_s).

    tok/s = tokens / (span from first to last token). TTFT excluded: that is the
    prompt-processing + first-token cost, not decode speed.
    """
    endpoint = url.rstrip("/") + "/v1/chat/completions"
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": True,
    }
    if not thinking:
        # Qwen3.8 (and Qwen3) accept enable_thinking via the chat template; this
        # keeps the measurement on the fast non-CoT path the recipe used.
        body["chat_template_kwargs"] = {"enable_thinking": False}

    req = urllib.request.Request(endpoint, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    t0 = time.time()
    ttft = None
    n = 0
    last = t0
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for line in r:
            line = line.decode().strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            delta = json.loads(line[6:])["choices"][0].get("delta", {})
            if delta.get("content") or delta.get("reasoning_content"):
                now = time.time()
                if ttft is None:
                    ttft = now - t0
                last = now
                n += 1
    span = last - t0 - (ttft or 0)
    return (n / span if span > 0 else 0.0), n, (ttft or 0.0)


def probe(url, prompts=None, runs=3, max_tokens=400, thinking=False, verbose=True):
    """Run the full probe; return dict of results. `prompts=None` uses PROMPTS."""
    prompts = list(PROMPTS if prompts is None else prompts)
    if verbose:
        print(f"probe {url}  (runs={runs}, max_tokens={max_tokens}, thinking={thinking})")
    # Warmup: load graph, warm the KV cache, discard.
    run_once(url, "warmup", min(40, max_tokens), thinking)
    per_prompt, all_rates, ttfts = [], [], []
    for p in prompts:
        rs = []
        for _ in range(runs):
            rate, n, ttft = run_once(url, p, max_tokens, thinking)
            rs.append(rate)
            all_rates.append(rate)
            ttfts.append(ttft)
        per_prompt.append((st.median(rs), rs, p))
        if verbose:
            print(f"{st.median(rs):6.1f} tok/s median | runs: {[round(x, 1) for x in rs]} | {p[:52]}")
    if verbose:
        print(f"OVERALL: mean {st.mean(all_rates):.1f}  median {st.median(all_rates):.1f}  tok/s "
              f"(ttft median {st.median(ttfts):.2f}s)")
    return {"url": url, "thinking": thinking, "runs": runs, "max_tokens": max_tokens,
            "per_prompt": [{"median": m, "runs": r, "prompt": p} for m, r, p in per_prompt],
            "overall_mean": st.mean(all_rates), "overall_median": st.median(all_rates)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("server_url", nargs="?", default=DEFAULT_URL, help="live server base URL")
    ap.add_argument("--runs", type=int, default=3, help="runs per prompt (default 3)")
    ap.add_argument("--max-tokens", type=int, default=400, help="max tokens per run (default 400)")
    ap.add_argument("--thinking", action="store_true", help="measure the thinking/CoT path (default: off)")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON result")
    a = ap.parse_args()
    try:
        res = probe(a.server_url, runs=a.runs, max_tokens=a.max_tokens,
                    thinking=a.thinking, verbose=not a.json)
    except Exception as e:
        print(f"probe failed: {e}", file=sys.stderr)
        sys.exit(1)
    if a.json:
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
