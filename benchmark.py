#!/usr/bin/env python3
import os
import sys
import time
import json
import subprocess
import re
import shutil
import argparse
import urllib.request
from typing import Dict, List, Any, Callable, Tuple

# Configuration
CONVERSATION_ID = "45a20dc1-6e2a-4f05-ac4a-233756b6eba4"
ARTIFACT_DIR = f"/home/dzyla/.gemini/antigravity-cli/brain/{CONVERSATION_ID}"
WORKSPACE_DIR = "/home/dzyla/ai-buddy"

def write_temp_file(name: str, content: str):
    path = os.path.join(WORKSPACE_DIR, name)
    with open(path, "w") as f:
        f.write(content)

def delete_temp_file(name: str):
    path = os.path.join(WORKSPACE_DIR, name)
    if os.path.exists(path):
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)

# Test cases definition
TEST_CASES = [
    {
        "id": "1_tool_calling_basic",
        "name": "Basic Tool Calling (list_directory)",
        "prompt": "You MUST call the list_directory tool to check the contents of the current directory. Do not guess. When done, call task_complete to finish.",
        "expected_tool": "list_directory",
        "validator": lambda stdout, stderr, exit_code, tools: any("list_directory" in t.lower() for t in tools) and exit_code == 0
    },
    {
        "id": "2_file_reading",
        "name": "File Reading & Context Integration",
        "setup": lambda: write_temp_file("bench_temp_cred.json", '{"api_key": "SECURE_KEY_12345", "user": "benchmark_tester"}'),
        "prompt": "You MUST call the read_file tool to read the contents of the file 'bench_temp_cred.json'. Extract the API key value from it and report it. Do not guess the key. Call task_complete.",
        "expected_tool": "read_file",
        "validator": lambda stdout, stderr, exit_code, tools: "SECURE_KEY_12345" in stdout and exit_code == 0,
        "cleanup": lambda: delete_temp_file("bench_temp_cred.json")
    },
    {
        "id": "3_shell_execution",
        "name": "Computer Use (Shell Execution)",
        "prompt": "You MUST call the execute_command tool to run a command (like uname -m or lscpu) to determine the CPU architecture of this machine. Call task_complete.",
        "expected_tool": "execute_command",
        "validator": lambda stdout, stderr, exit_code, tools: any("execute_command" in t or "hostname" in t or "uname" in t for t in tools) and exit_code == 0
    },
    {
        "id": "4_coding_execution",
        "name": "Coding and Execution",
        "prompt": "Write a python script 'bench_prime.py' using write_file that calculates the sum of the first 50 prime numbers. You MUST execute it using the execute_command tool to verify the output, and call task_complete with the final sum.",
        "expected_tool": "execute_command",
        "validator": lambda stdout, stderr, exit_code, tools: "5117" in stdout and exit_code == 0,
        "cleanup": lambda: (delete_temp_file("bench_prime.py"), delete_temp_file("__pycache__"))
    },
    {
        "id": "5_error_recovery",
        "name": "Error Recovery & Troubleshooting",
        "prompt": "Write a python script 'bench_trouble.py' using write_file that tries to import a non-existent package 'not_a_real_package_123' and prints 'Imported'. Run it using execute_command. When it fails, edit the file using edit_file to catch the exception and print 'Recovered' instead, run it again using execute_command, and call task_complete.",
        "expected_tool": "execute_command",
        "validator": lambda stdout, stderr, exit_code, tools: "Recovered" in stdout and exit_code == 0,
        "cleanup": lambda: delete_temp_file("bench_trouble.py")
    },
    {
        "id": "6_web_search",
        "name": "Web Search & Fetch",
        "prompt": "You MUST use the web_search tool to search for the phrase 'Google Antigravity AI coding assistant'. Report the findings and call task_complete.",
        "expected_tool": "web_search",
        "validator": lambda stdout, stderr, exit_code, tools: any("web_search" in t.lower() for t in tools) and exit_code == 0
    }
]

def load_ai_env() -> Dict[str, str]:
    """Read the active env variables from ~/.local/share/ai/env"""
    env = os.environ.copy()
    env_path = os.path.expanduser("~/.local/share/ai/env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:]
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        env[k] = v
    # Enforce auto-approve and tool choice for testing
    env["INFER_AUTO_APPROVE"] = "1"
    env["INFER_TOOL_CHOICE"] = "required"
    env["INFER_TRIM_THRESHOLD"] = "45000"
    env["INFER_STUB_THRESHOLD"] = "52000"
    env["INFER_MAX_TOKENS"] = "2048"
    return env

def discover_models() -> List[str]:
    """Discover available models on the system"""
    model_dir = os.path.expanduser("~/.local/share/ai/models")
    if not os.path.exists(model_dir):
        return []
    models = []
    for f in os.listdir(model_dir):
        if f.endswith(".gguf") and not f.startswith("mmproj-"):
            models.append(os.path.join(model_dir, f))
    return sorted(models)

def switch_model(model_path: str) -> bool:
    """Switch llama-server to a different model and wait for it to load"""
    model_name = os.path.basename(model_path)
    print(f"\n[Model Switch] Changing active model to: {model_name}")
    
    # 1. Run ai-backend llama <model_path>
    res = subprocess.run(["bash", "./ai-use.sh", "local", model_path], capture_output=True, text=True, cwd=WORKSPACE_DIR)
    if res.returncode != 0:
        print(f"Error switching model: {res.stderr}")
        return False
    
    # 2. Restart llama-server service
    print("Restarting llama-server service via systemd...")
    res = subprocess.run(["systemctl", "--user", "restart", "llama-server"], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Error restarting llama-server: {res.stderr}")
        return False
        
    # 3. Poll /v1/models to verify ready and model matched
    print("Waiting for model to load in llama-server (timeout: 150s)...")
    url = "http://localhost:8080/v1/models"
    import urllib.request
    
    start_time = time.time()
    while time.time() - start_time < 150:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    loaded_model = data.get("models", [{}])[0].get("model", "")
                    if os.path.basename(loaded_model) == model_name:
                        print("llama-server is ready and model is loaded successfully!")
                        return True
        except Exception:
            pass
        time.sleep(2)
        
    print("Timeout waiting for model to load.")
    return False

def parse_benchmark_stats(stdout: str, stderr: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Parse stderr and stdout to extract loop statistics and tool calls"""
    loops_stats = []
    tools_called = []
    
    loop_pattern = re.compile(
        r'\[loop (\d+)\s*\|\s*ctx (\d+)/(\d+)\s*\(\d+%\)\s*\|\s*\+(\d+)\s*tok\s*\|\s*([\d\.]+)\s*tok/s\]'
    )
    loop_pattern_alt = re.compile(
        r'\[loop (\d+)\s*\|\s*(\d+)\s*ctx tok\s*\|\s*\+(\d+)\s*new\s*\|\s*([\d\.]+)\s*tok/s\]'
    )
    
    for line in stderr.splitlines():
        line_clean = re.sub(r'\x1b\[[0-9;]*[mGKF]', '', line).strip()
        
        # Tool call prints
        if "[ai] executing command:" in line_clean:
            cmd = line_clean.split("[ai] executing command:", 1)[1].strip()
            tools_called.append(f"execute_command({cmd})")
        elif "[ai] " in line_clean and ":" in line_clean:
            parts = line_clean.split("[ai] ", 1)[1].split(":", 1)
            tool_name = parts[0].strip()
            tool_args = parts[1].strip()
            if tool_name not in ["executing command"]:
                tools_called.append(f"{tool_name}({tool_args})")
        elif "execute_command(" in line_clean:
            # Fallback regex search for python call-tool
            m_tool = re.search(r'(\w+)\((.*)\)', line_clean)
            if m_tool:
                tools_called.append(line_clean)
                
        # Loop stats
        m = loop_pattern.search(line_clean)
        if m:
            loops_stats.append({
                "loop": int(m.group(1)),
                "ctx": int(m.group(2)),
                "tokens": int(m.group(4)),
                "tps": float(m.group(5))
            })
            continue
            
        m_alt = loop_pattern_alt.search(line_clean)
        if m_alt:
            loops_stats.append({
                "loop": int(m_alt.group(1)),
                "ctx": int(m_alt.group(2)),
                "tokens": int(m_alt.group(3)),
                "tps": float(m_alt.group(4))
            })
            
    return loops_stats, tools_called

def cleanup_mcp_processes():
    subprocess.run(["pkill", "-f", "ai_mcp.py"], capture_output=True)
    subprocess.run(["pkill", "-f", "pubmed_mcp_server.py"], capture_output=True)

def run_test_case(test: Dict[str, Any], env: Dict[str, str], timeout: int = 120) -> Dict[str, Any]:
    print(f"\n--- Running Task: {test['name']} ---")
    
    # 1. Setup
    if "setup" in test:
        print(f"Setting up task...")
        test["setup"]()
        
    # 2. Run process
    cmd = ["./ai", test["prompt"]]
    start_time = time.time()
    
    stdout, stderr = "", ""
    exit_code = -1
    elapsed = 0.0
    
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=WORKSPACE_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env
        )
        stdout, stderr = proc.communicate(timeout=timeout)
        exit_code = proc.returncode
        elapsed = time.time() - start_time
    except subprocess.TimeoutExpired:
        print(f"Task TIMEOUT reached ({timeout}s). Terminating process...")
        proc.kill()
        stdout, stderr = proc.communicate()
        elapsed = time.time() - start_time
        exit_code = -99  # Timeout code
    except Exception as e:
        print(f"Error executing CLI: {e}")
        exit_code = -500
        
    # 3. Cleanup task specific files
    if "cleanup" in test:
        print(f"Cleaning up task files...")
        test["cleanup"]()
        
    # 4. Process stats
    loops, tools = parse_benchmark_stats(stdout, stderr)
    
    # 5. Validate success
    success = False
    if exit_code == 0:
        try:
            success = test["validator"](stdout, stderr, exit_code, tools)
        except Exception as e:
            print(f"Validator error: {e}")
            success = False
            
    # Calculate stats
    total_tokens = sum(l["tokens"] for l in loops)
    avg_tps = sum(l["tps"] for l in loops) / len(loops) if loops else 0.0
    num_loops = len(loops)
    final_ctx = loops[-1]["ctx"] if loops else 0
    
    status_str = "SUCCESS" if success else ("TIMEOUT" if exit_code == -99 else "FAILED")
    print(f"Result: {status_str} | Turns: {num_loops} | Time: {elapsed:.2f}s | Tokens: {total_tokens} | Avg Speed: {avg_tps:.1f} tok/s")
    
    return {
        "id": test["id"],
        "name": test["name"],
        "status": status_str,
        "success": success,
        "time": elapsed,
        "turns": num_loops,
        "tokens": total_tokens,
        "tps": avg_tps,
        "final_ctx": final_ctx,
        "tools": tools,
        "stdout": stdout,
        "stderr": stderr
    }

def get_current_model_name() -> str:
    try:
        with urllib.request.urlopen("http://localhost:8080/v1/models", timeout=2) as response:
            data = json.loads(response.read().decode('utf-8'))
            model_path = data.get("models", [{}])[0].get("model", "")
            return os.path.basename(model_path)
    except Exception:
        return "Unknown Local LLM"

def generate_report(results: Dict[str, List[Dict[str, Any]]], run_mode: str) -> str:
    """Compile the results into a gorgeous Markdown report"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    
    md = []
    md.append(f"# Google Antigravity Agentic CLI Benchmark Report")
    md.append(f"**Date:** {timestamp} | **Execution Mode:** `{run_mode}`")
    md.append("")
    
    md.append("This benchmark evaluates local LLM performance under the `ai` agentic CLI loop, testing their ability to call tools, interact with the system shell, write and troubleshoot code, and search the web.")
    md.append("")
    
    # 1. Summary table
    md.append("## 📊 Executive Summary")
    md.append("")
    md.append("| Model | Success Rate | Average Turn Duration | Total Turns | Avg Generation Speed | Avg Context Growth |")
    md.append("| :--- | :---: | :---: | :---: | :---: | :---: |")
    
    for model_name, case_results in results.items():
        total_cases = len(case_results)
        successes = sum(1 for r in case_results if r["success"])
        success_rate = (successes / total_cases) * 100
        avg_time = sum(r["time"] for r in case_results) / total_cases
        total_turns = sum(r["turns"] for r in case_results)
        avg_tps = sum(r["tps"] for r in case_results if r["turns"] > 0) / sum(1 for r in case_results if r["turns"] > 0) if any(r["turns"] > 0 for r in case_results) else 0.0
        avg_ctx = sum(r["final_ctx"] for r in case_results) / total_cases
        
        md.append(f"| **{model_name}** | {success_rate:.1f}% ({successes}/{total_cases}) | {avg_time:.2f}s | {total_turns} | {avg_tps:.1f} tok/s | {avg_ctx:.0f} tok |")
    md.append("")
    
    # 2. Detailed results by model
    md.append("## 🔍 Detailed Results by Model")
    md.append("")
    
    for model_name, case_results in results.items():
        md.append(f"### Model: `{model_name}`")
        md.append("")
        md.append("| Task | Status | Duration | Turns | Tokens | Speed | Tools Called |")
        md.append("| :--- | :---: | :---: | :---: | :---: | :---: | :--- |")
        
        for r in case_results:
            status_emoji = "✅ SUCCESS" if r["success"] else ("⚠️ TIMEOUT" if r["status"] == "TIMEOUT" else "❌ FAILED")
            tools_list = ", ".join([t.split("(", 1)[0] for t in r["tools"]]) if r["tools"] else "*None*"
            md.append(f"| {r['name']} | {status_emoji} | {r['time']:.2f}s | {r['turns']} | {r['tokens']} | {r['tps']:.1f} t/s | `{tools_list}` |")
        md.append("")
        
        # Qualitative notes
        md.append("#### Task-by-Task Analysis")
        for r in case_results:
            md.append(f"- **{r['name']}**:")
            if r["success"]:
                md.append(f"  - Completed successfully in {r['time']:.1f} seconds over {r['turns']} turns.")
                md.append(f"  - Tools utilized: `{r['tools']}`")
                md.append("  <details>")
                md.append("  <summary>🔍 View Execution Output</summary>")
                md.append("")
                md.append("  **Stdout:**")
                md.append("  ```")
                md.append(r["stdout"].strip() if r["stdout"] else "*No output*")
                md.append("  ```")
                md.append("  </details>")
            else:
                md.append(f"  - **Failure/Timeout** ({r['status']}). Process took {r['time']:.1f}s.")
                if r["tools"]:
                    md.append(f"  - Model managed to call: `{r['tools']}` before failing.")
                else:
                    md.append(f"  - No tool calls were successfully parsed from the model response.")
                
                # Show stdout and stderr under details
                md.append("  <details>")
                md.append("  <summary>🔍 View Execution Output & Error Logs</summary>")
                md.append("")
                md.append("  **Stdout:**")
                md.append("  ```")
                md.append(r["stdout"].strip() if r["stdout"] else "*No output*")
                md.append("  ```")
                md.append("")
                md.append("  **Stderr:**")
                md.append("  ```")
                md.append(r["stderr"].strip() if r["stderr"] else "*No error logs*")
                md.append("  ```")
                md.append("  </details>")
        md.append("")
        md.append("---")
        md.append("")
        
    # Conclusion and Recommendations
    md.append("## 💡 Findings and Insights")
    md.append("")
    md.append("Based on the benchmark execution:")
    
    # Analyze if there's comparison data
    if len(results) >= 2:
        models_keys = list(results.keys())
        m1, m2 = models_keys[0], models_keys[1]
        s1 = sum(1 for r in results[m1] if r["success"])
        s2 = sum(1 for r in results[m2] if r["success"])
        tps1 = sum(r["tps"] for r in results[m1]) / len(results[m1])
        tps2 = sum(r["tps"] for r in results[m2]) / len(results[m2])
        
        md.append(f"1. **Task Execution Success**: `{m1}` completed {s1}/{len(results[m1])} tasks successfully, while `{m2}` completed {s2}/{len(results[m2])} tasks.")
        md.append(f"2. **Token Throughput Speed**: `{m1}` averaged `{tps1:.1f} tok/s` vs. `{m2}`'s `{tps2:.1f} tok/s` on the RTX 5080.")
        if s1 > s2:
            md.append(f"3. **Recommendation**: `{m1}` exhibits stronger agentic stability and tool-calling capabilities. It is the recommended local LLM for general CLI developer tasks.")
        elif s2 > s1:
            md.append(f"3. **Recommendation**: `{m2}` exhibits stronger agentic stability and tool-calling capabilities. It is the recommended local LLM for general CLI developer tasks.")
        else:
            md.append(f"3. **Recommendation**: Both models exhibit similar reasoning capabilities. Choose the faster model (`{m1 if tps1 > tps2 else m2}`).")
    else:
        m = list(results.keys())[0]
        s = sum(1 for r in results[m] if r["success"])
        md.append(f"1. The model `{m}` achieved a success rate of {s/len(results[m])*100:.1f}% ({s}/{len(results[m])} tasks completed successfully).")
        md.append("2. Lock the context window size of llama-server (e.g. to `8192` tokens) when running locally to avoid massive KV cache growth and ensure optimal GPU generation speeds.")
        
    return "\n".join(md)

def main():
    parser = argparse.ArgumentParser(description="Antigravity local LLM agentic benchmark runner")
    parser.add_argument("--current", action="store_true", help="Run benchmark ONLY on the currently active model (no switching)")
    parser.add_argument("--all", action="store_true", help="Run benchmark on ALL discovered local GGUF models")
    parser.add_argument("--models", type=str, help="Comma-separated paths of specific GGUF models to benchmark")
    parser.add_argument("--compare", type=str, help="Comma-separated paths to result JSON files to generate a combined comparison report")
    parser.add_argument("--timeout", type=int, default=120, help="Timeout in seconds per task (default: 120)")
    
    args = parser.parse_args()
    
    # Check if we are in comparison mode
    if args.compare:
        print("=== Antigravity Benchmark Comparison Mode ===")
        compare_files = [f.strip() for f in args.compare.split(",") if f.strip()]
        benchmark_results = {}
        for fpath in compare_files:
            if not os.path.exists(fpath):
                print(f"Error: file not found {fpath}")
                continue
            with open(fpath, "r") as f:
                model_runs = json.load(f)
            model_name = os.path.basename(fpath).replace("results_", "").replace(".json", "")
            benchmark_results[model_name] = model_runs
            
        report_md = generate_report(benchmark_results, "Combined Comparison Report")
        
        local_report_path = os.path.join(WORKSPACE_DIR, "benchmark_report.md")
        with open(local_report_path, "w") as f:
            f.write(report_md)
        print(f"\n[Completed] Comparative report saved to workspace: {local_report_path}")
        
        if os.path.exists(ARTIFACT_DIR):
            artifact_report_path = os.path.join(ARTIFACT_DIR, "benchmark_report.md")
            with open(artifact_report_path, "w") as f:
                f.write(report_md)
            print(f"[Completed] Comparative report saved to artifacts: {artifact_report_path}")
            
        return
    
    # Verify environment
    env = load_ai_env()
    print("=== Antigravity LLM Benchmark Initialization ===")
    print(f"Base URL: {env.get('INFER_BASE_URL')}")
    print(f"API Key: {env.get('INFER_API_KEY')}")
    print(f"Tool Choice Mode: {env.get('INFER_TOOL_CHOICE')}")
    print(f"Auto-Approve Commands: {env.get('INFER_AUTO_APPROVE')}")
    
    selected_models = []
    run_mode = "Single Active Model"
    
    if args.current:
        # Run only on current model
        active_model = get_current_model_name()
        print(f"Running benchmark on the currently active model: {active_model}")
        selected_models = [active_model]
        run_mode = "Current Active Model"
    elif args.models:
        # Run on specific comma separated paths
        selected_models = [m.strip() for m in args.models.split(",") if m.strip()]
        run_mode = "Specified Models List"
    elif args.all:
        # Discover and run on all
        selected_models = discover_models()
        print(f"Discovered {len(selected_models)} local model(s): {[os.path.basename(m) for m in selected_models]}")
        run_mode = "All Discovered Models"
    else:
        # Default: default to all models if they fit, or current if none discovered
        discovered = discover_models()
        if discovered:
            # We filter down to gemma-4-E4B and ornith-1.0-9b for standard tests to save time,
            # excluding the slower gemma4-coding unless requested.
            selected_models = [m for m in discovered if "gemma4-coding" not in m]
            if not selected_models:
                selected_models = discovered
            print(f"Defaulting to discovered fast models: {[os.path.basename(m) for m in selected_models]}")
            run_mode = "Standard Benchmark (Fast Models)"
        else:
            active_model = get_current_model_name()
            print(f"No local GGUF models found in directory. Defaulting to current active model: {active_model}")
            selected_models = [active_model]
            run_mode = "Fallback Current Model"
            
    # Execute benchmark
    benchmark_results = {}
    
    for model_path in selected_models:
        model_name = os.path.basename(model_path)
        
        # If we need to switch (i.e. we have paths and we are not in --current mode)
        if not args.current and os.path.isabs(model_path):
            success = switch_model(model_path)
            if not success:
                print(f"Skipping model {model_name} due to switch failure.")
                continue
        else:
            print(f"\n[Execution] Starting benchmark for active model: {model_name}")
            
        model_runs = []
        for case in TEST_CASES:
            res = run_test_case(case, env, timeout=args.timeout)
            model_runs.append(res)
            # Short cooldown between tasks
            time.sleep(2)
            cleanup_mcp_processes()
            
        benchmark_results[model_name] = model_runs
        
        # Save results to a json file
        clean_model_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in model_name)
        json_path = os.path.join(WORKSPACE_DIR, f"results_{clean_model_name}.json")
        with open(json_path, "w") as f:
            json.dump(model_runs, f, indent=2)
        print(f"[Completed] Raw results saved to: {json_path}")
        
    # Write report
    report_md = generate_report(benchmark_results, run_mode)
    
    # Save report locally in workspace
    local_report_path = os.path.join(WORKSPACE_DIR, "benchmark_report.md")
    with open(local_report_path, "w") as f:
        f.write(report_md)
    print(f"\n[Completed] Report saved to workspace: {local_report_path}")
    
    # Save report to artifact directory
    if os.path.exists(ARTIFACT_DIR):
        artifact_report_path = os.path.join(ARTIFACT_DIR, "benchmark_report.md")
        with open(artifact_report_path, "w") as f:
            f.write(report_md)
        print(f"[Completed] Report saved to artifacts: {artifact_report_path}")
        
    # Print high-level summary to console
    print("\n================== BENCHMARK SUMMARY ==================")
    for model_name, runs in benchmark_results.items():
        successes = sum(1 for r in runs if r["success"])
        print(f"Model: {model_name} | Success: {successes}/{len(runs)} ({successes/len(runs)*100:.1f}%)")
    print("=======================================================")

if __name__ == "__main__":
    main()
