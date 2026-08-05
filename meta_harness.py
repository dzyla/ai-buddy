#!/usr/bin/env python3
import subprocess
import sys
import os

def run_benchmark():
    print("[Meta-Harness] Running benchmark suite...")
    result = subprocess.run(["python3", "benchmark_assistant.py"], capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr

def run_agent_fix(stdout, stderr):
    print("[Meta-Harness] Benchmark failed. Triggering outer-loop agentic rewrite...")
    
    prompt = f"""Adopt your Master Planner skill. The local harness (ai.c/ai_mcp.py) failed the integration benchmark suite.
    
Here is the benchmark output:
{stdout[-2000:]}

Here is the error log:
{stderr[-2000:]}

Analyze why the harness failed. Your task is to dynamically rewrite the harness code (ai.c or ai_mcp.py) to fix the underlying execution issue causing this benchmark failure.
1. Use edit_file or run_command to apply your rewrite.
2. Recompile ai.c using 'gcc -O2 -o ~/.local/bin/ai ai.c cJSON.c -lcurl'.
3. Run the benchmark locally via 'python3 benchmark_assistant.py'.
4. If it still fails, iterate.
5. Exit gracefully when the benchmark passes.
"""
    
    # We use the local ai tool itself as the outer-loop meta-agent!
    process = subprocess.Popen(
        [os.path.expanduser("~/.local/bin/ai"), "-y", "-c", prompt],
        stdout=sys.stdout,
        stderr=sys.stderr
    )
    process.wait()
    return process.returncode

def main():
    max_iterations = 3
    for i in range(max_iterations):
        print(f"\n=== Meta-Harness Loop {i+1}/{max_iterations} ===")
        code, out, err = run_benchmark()
        
        if code == 0:
            print("[Meta-Harness] SUCCESS! The harness passes all benchmarks.")
            sys.exit(0)
            
        print(f"[Meta-Harness] Benchmark failed with exit code {code}.")
        agent_code = run_agent_fix(out, err)
        
        if agent_code != 0:
            print(f"[Meta-Harness] The outer-loop agent failed to compile or complete the fix. Exit code {agent_code}.")
            # We continue the loop, maybe it made partial progress
            
    print("[Meta-Harness] Maximum iterations reached. Harness rewrite unsuccessful.")
    sys.exit(1)

if __name__ == "__main__":
    main()
