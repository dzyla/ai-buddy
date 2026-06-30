# Google Antigravity Agentic CLI Benchmark Report
**Date:** 2026-06-29 23:44:56 | **Execution Mode:** `Combined Comparison Report`

This benchmark evaluates local LLM performance under the `ai` agentic CLI loop, testing their ability to call tools, interact with the system shell, write and troubleshoot code, and search the web.

## 📊 Executive Summary

| Model | Success Rate | Average Turn Duration | Total Turns | Avg Generation Speed | Avg Context Growth |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf** | 100.0% (6/6) | 4.35s | 16 | 140.3 tok/s | 4531 tok |
| **ornith-1.0-9b-Q4_K_M.gguf** | 100.0% (6/6) | 7.19s | 16 | 108.0 tok/s | 5182 tok |
| **LFM2.5-230M-Q4_K_M.gguf** | 33.3% (2/6) | 11.37s | 15 | 527.1 tok/s | 6460 tok |
| **ornith-9b-mtp-kl-Q4_K_M.gguf** | 100.0% (6/6) | 6.05s | 17 | 123.6 tok/s | 4544 tok |

## 🔍 Detailed Results by Model

### Model: `gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf`

| Task | Status | Duration | Turns | Tokens | Speed | Tools Called |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| Basic Tool Calling (list_directory) | ✅ SUCCESS | 1.48s | 2 | 144 | 124.0 t/s | `list_directory` |
| File Reading & Context Integration | ✅ SUCCESS | 2.24s | 2 | 271 | 139.0 t/s | `read_file` |
| Computer Use (Shell Execution) | ✅ SUCCESS | 1.62s | 2 | 168 | 130.0 t/s | `execute_command` |
| Coding and Execution | ✅ SUCCESS | 5.41s | 3 | 832 | 159.3 t/s | `write_file, execute_command` |
| Error Recovery & Troubleshooting | ✅ SUCCESS | 4.85s | 5 | 667 | 151.8 t/s | `write_file, execute_command, edit_file, execute_command` |
| Web Search & Fetch | ✅ SUCCESS | 10.50s | 2 | 1247 | 137.5 t/s | `web_search` |

#### Task-by-Task Analysis
- **Basic Tool Calling (list_directory)**:
  - Completed successfully in 1.5 seconds over 2 turns.
  - Tools utilized: `['list_directory(?)']`
  <details>
  <summary>🔍 View Execution Output</summary>

  **Stdout:**
  ```
[2m────────────────────────────────────────────[0m

The contents of the current directory have been successfully listed.
  ```
  </details>
- **File Reading & Context Integration**:
  - Completed successfully in 2.2 seconds over 2 turns.
  - Tools utilized: `['read_file(bench_temp_cred.json)']`
  <details>
  <summary>🔍 View Execution Output</summary>

  **Stdout:**
  ```
[2m────────────────────────────────────────────[0m

The API key extracted from 'bench_temp_cred.json' is: SECURE_KEY_12345
  ```
  </details>
- **Computer Use (Shell Execution)**:
  - Completed successfully in 1.6 seconds over 2 turns.
  - Tools utilized: `['execute_command(uname -m)']`
  <details>
  <summary>🔍 View Execution Output</summary>

  **Stdout:**
  ```
[2m────────────────────────────────────────────[0m

The CPU architecture of this machine is x86_64.
  ```
  </details>
- **Coding and Execution**:
  - Completed successfully in 5.4 seconds over 3 turns.
  - Tools utilized: `['write_file(bench_prime.py)', 'execute_command(python bench_prime.py)']`
  <details>
  <summary>🔍 View Execution Output</summary>

  **Stdout:**
  ```
[2m────────────────────────────────────────────[0m

The Python script 'bench_prime.py' was successfully created using [33mwrite_file[39m and executed using [33mexecute_command[39m. The script calculates the sum of the first 50 prime numbers.

The calculated sum is 5117.


[1;34mSources[0m
bench_prime.py
  ```
  </details>
- **Error Recovery & Troubleshooting**:
  - Completed successfully in 4.8 seconds over 5 turns.
  - Tools utilized: `['write_file(bench_trouble.py)', 'execute_command(python bench_trouble.py)', 'edit_file(bench_trouble.py)', 'execute_command(python bench_trouble.py)']`
  <details>
  <summary>🔍 View Execution Output</summary>

  **Stdout:**
  ```
[2m────────────────────────────────────────────[0m

The script 'bench_trouble.py' was successfully created and executed.

[1mInitial Run:[22m
The script initially failed as expected because the package 'not_a_real_package_123' does not exist.

[90m─────────────────────────────────────────────[0m
  $ python bench_trouble.py
  ModuleNotFoundError: No module named [32m'not_a_real_package_123'[0m
[90m─────────────────────────────────────────────[0m

[1mRecovery and Final Run:[22m
The script was then edited to catch the [33mImportError[39m exception. After the modification, the script ran successfully, executing the exception handler.

[90m─────────────────────────────────────────────[0m
  $ python bench_trouble.py
  Recovered
[90m─────────────────────────────────────────────[0m

The task is complete.


[1;34mSources[0m
bench_trouble.py
  ```
  </details>
- **Web Search & Fetch**:
  - Completed successfully in 10.5 seconds over 2 turns.
  - Tools utilized: `['web_search("Google Antigravity AI coding assistant")']`
  <details>
  <summary>🔍 View Execution Output</summary>

  **Stdout:**
  ```
[2m────────────────────────────────────────────[0m

Google Antigravity is a new, agent-first development platform and IDE from Google, designed to usher in an era where AI agents handle large parts of the software development lifecycle autonomously.

Key findings from the search results:

• [1mNature of the Platform:[22m Unlike traditional AI code completion tools that offer snippets, Antigravity is an "agentic development platform" that allows autonomous AI agents to plan entire projects, write code across multiple files, run tests, and fix bugs automatically.
• [1mAntigravity 2.0:[22m This version acts as a "central command center" for managing multiple local agents in parallel, providing a unified platform to orchestrate their activities.
• [1mComponents:[22m The ecosystem includes:
    • [1mAntigravity:[22m A standalone application for macOS, Linux, and Windows, functioning as the command center.
    • [1mAntigravity IDE:[22m The original fully-featured, agentic IDE.
    • [1mAntigravity CLI:[22m A command-line interface for terminal-based agent interactions.
    • [1mAntigravity SDK:[22m Tools for programmatic integration into other systems.
• [1mFunctionality:[22m Agents within the platform can read project code, edit files, run terminal commands, and even navigate a web browser to plan, implement, and verify features end-to-end.
• [1mAvailability:[22m The platform is available for macOS, Linux, and Windows.


[1;34mSources[0m
• https://codelabs.developers.google.com/getting-started-google-antigravity
• https://www.codecademy.com/article/how-to-set-up-and-use-google-antigravity
• https://antigravityai.io/
• https://developers.googleblog.com/build-with-google-antigravity-our-new-agentic-development-platform/
• https://en.wikipedia.org/wiki/Google_Antigravity
• https://aidevhub.net/blog/google-antigravity-tutorial-guide
• https://antigravityide.net/
• https://realpython.com/ref/ai-coding-tools/google-antigravity/
  ```
  </details>

---

### Model: `ornith-1.0-9b-Q4_K_M.gguf`

| Task | Status | Duration | Turns | Tokens | Speed | Tools Called |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| Basic Tool Calling (list_directory) | ✅ SUCCESS | 5.24s | 2 | 550 | 89.5 t/s | `list_directory` |
| File Reading & Context Integration | ✅ SUCCESS | 3.06s | 2 | 324 | 113.5 t/s | `read_file` |
| Computer Use (Shell Execution) | ✅ SUCCESS | 1.50s | 2 | 139 | 104.5 t/s | `execute_command` |
| Coding and Execution | ✅ SUCCESS | 5.77s | 3 | 677 | 119.7 t/s | `write_file, execute_command` |
| Error Recovery & Troubleshooting | ✅ SUCCESS | 5.65s | 5 | 582 | 109.6 t/s | `write_file, execute_command, edit_file, execute_command` |
| Web Search & Fetch | ✅ SUCCESS | 21.90s | 2 | 2612 | 111.0 t/s | `web_search, Warning` |

#### Task-by-Task Analysis
- **Basic Tool Calling (list_directory)**:
  - Completed successfully in 5.2 seconds over 2 turns.
  - Tools utilized: `['list_directory(/home/dzyla/ai-buddy)']`
  <details>
  <summary>🔍 View Execution Output</summary>

  **Stdout:**
  ```
[2m────────────────────────────────────────────[0m

Listed the contents of /home/dzyla/ai-buddy directory:

[1mDirectories:[22m
• [33m.agents[39m - Agent configuration
• [33m.claude[39m - Claude configuration
• [33m.git[39m - Version control
• [33mdocs[39m - Documentation files

[1mSource Files:[22m
• [33mai.c[39m (151.6 KB) - Main C source file
• [33mai_mcp.py[39m (95.3 KB) - MCP Python implementation
• [33mcJSON.c[39m (78.8 KB) - JSON library implementation
• [33mcJSON.h[39m (16.0 KB) - JSON library header
• [33mjsmn.h[39m (11.9 KB) - JSON parser header
• [33mbenchmark.py[39m (24.5 KB) - Benchmarking script
• [33mdeep_research.py[39m (16.1 KB) - Deep research module

[1mScripts:[22m
• [33minstall.sh[39m (13.6 KB) - Installation script
• [33mai-use.sh[39m (13.2 KB) - AI usage script
• [33mllama-server-wrapper.sh[39m (6.1 KB) - Llama server wrapper

[1mDocumentation:[22m
• [33mREADME.md[39m (7.9 KB)
• [33mCLAUDE.md[39m (9.3 KB)
• [33mLICENSE[39m (34.3 KB)
• [33mbenchmark_report.md[39m (7.7 KB)

[1mConfiguration:[22m
• [33mmcp.json[39m (288 B) - MCP configuration
• [33m.gitignore[39m (423 B)

[1mData Files:[22m
• [33mai[39m (114.6 KB) - Binary/compiled file
• [33mai-backend[39m (7.1 KB) - Backend binary
• [33mresults_gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf.json[39m (9.8 KB) - Model results JSON
• [33mtest_deep_research.py[39m (1.8 KB) - Test file
  ```
  </details>
- **File Reading & Context Integration**:
  - Completed successfully in 3.1 seconds over 2 turns.
  - Tools utilized: `['read_file(bench_temp_cred.json)']`
  <details>
  <summary>🔍 View Execution Output</summary>

  **Stdout:**
  ```
[2m────────────────────────────────────────────[0m

The API key from bench_temp_cred.json is: [1mSECURE_KEY_12345[22m (user: benchmark_tester).
  ```
  </details>
- **Computer Use (Shell Execution)**:
  - Completed successfully in 1.5 seconds over 2 turns.
  - Tools utilized: `['execute_command(uname -m)']`
  <details>
  <summary>🔍 View Execution Output</summary>

  **Stdout:**
  ```
[2m────────────────────────────────────────────[0m

The CPU architecture of this machine is [1mx86_64[22m (64-bit AMD64/Intel 64).
  ```
  </details>
- **Coding and Execution**:
  - Completed successfully in 5.8 seconds over 3 turns.
  - Tools utilized: `['write_file(/home/dzyla/ai-buddy/bench_prime.py)', 'execute_command(python3 bench_prime.py)']`
  <details>
  <summary>🔍 View Execution Output</summary>

  **Stdout:**
  ```
[2m────────────────────────────────────────────[0m

The script [33mbench_prime.py[39m was written and executed. It finds the first 50 prime numbers and calculates their sum.

[1mSum of the first 50 prime numbers: 5117[22m

The first 50 primes are: 2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71, 73, 79, 83, 89, 97, 101, 103, 107, 109, 113, 127, 131, 137, 139, 149, 151, 157, 163, 167, 173, 179, 181, 191, 193, 197, 199, 211, 223, 227, 229.
  ```
  </details>
- **Error Recovery & Troubleshooting**:
  - Completed successfully in 5.6 seconds over 5 turns.
  - Tools utilized: `['write_file(/home/dzyla/ai-buddy/bench_trouble.py)', 'execute_command(python bench_trouble.py)', 'edit_file(/home/dzyla/ai-buddy/bench_trouble.py)', 'execute_command(python bench_trouble.py)']`
  <details>
  <summary>🔍 View Execution Output</summary>

  **Stdout:**
  ```
[2m────────────────────────────────────────────[0m

Successfully demonstrated exception handling in Python:
1. Wrote bench_trouble.py to import non-existent package 'not_a_real_package_123' and print 'Imported'
2. Ran it — it failed with [33mModuleNotFoundError: No module named 'not_a_real_package_123'[39m
3. Edited the file to wrap the import in a try/except ImportError block that prints 'Recovered' instead
4. Ran it again — it now successfully prints 'Recovered'
  ```
  </details>
- **Web Search & Fetch**:
  - Completed successfully in 21.9 seconds over 2 turns.
  - Tools utilized: `['web_search("Google Antigravity AI coding assistant")', 'Warning(model hit token limit — response truncated. Nudging to complete.)']`
  <details>
  <summary>🔍 View Execution Output</summary>

  **Stdout:**
  ```
*No output*
  ```
  </details>

---

### Model: `LFM2.5-230M-Q4_K_M.gguf`

| Task | Status | Duration | Turns | Tokens | Speed | Tools Called |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| Basic Tool Calling (list_directory) | ✅ SUCCESS | 5.49s | 2 | 3952 | 445.0 t/s | `list_directory, Warning` |
| File Reading & Context Integration | ❌ FAILED | 4.81s | 1 | 4250 | 911.0 t/s | `Warning` |
| Computer Use (Shell Execution) | ❌ FAILED | 5.59s | 1 | 4258 | 782.0 t/s | `Warning` |
| Coding and Execution | ❌ FAILED | 0.43s | 2 | 61 | 387.0 t/s | `write_file` |
| Error Recovery & Troubleshooting | ❌ FAILED | 0.73s | 4 | 120 | 373.8 t/s | `write_file, execute_command, edit_file` |
| Web Search & Fetch | ✅ SUCCESS | 51.15s | 5 | 151 | 263.6 t/s | `web_search, web_search, web_search, web_search` |

#### Task-by-Task Analysis
- **Basic Tool Calling (list_directory)**:
  - Completed successfully in 5.5 seconds over 2 turns.
  - Tools utilized: `['list_directory(.)', 'Warning(model hit token limit — response truncated. Nudging to complete.)']`
  <details>
  <summary>🔍 View Execution Output</summary>

  **Stdout:**
  ```
*No output*
  ```
  </details>
- **File Reading & Context Integration**:
  - **Failure/Timeout** (FAILED). Process took 4.8s.
  - Model managed to call: `['Warning(model hit token limit — response truncated. Nudging to complete.)']` before failing.
  <details>
  <summary>🔍 View Execution Output & Error Logs</summary>

  **Stdout:**
  ```
*No output*
  ```

  **Stderr:**
  ```
[1;33m[ai] Warning: model hit token limit — response truncated. Nudging to complete.[0m

[2m[loop 1 | ctx 8192/8192 (100%) | +4250 tok | 911 tok/s][0m

[1;31m[ai Error][0m request (8223 tokens) exceeds the available context size (8192 tokens), try increasing it
  ```
  </details>
- **Computer Use (Shell Execution)**:
  - **Failure/Timeout** (FAILED). Process took 5.6s.
  - Model managed to call: `['Warning(model hit token limit — response truncated. Nudging to complete.)']` before failing.
  <details>
  <summary>🔍 View Execution Output & Error Logs</summary>

  **Stdout:**
  ```
*No output*
  ```

  **Stderr:**
  ```
[1;33m[ai] Warning: model hit token limit — response truncated. Nudging to complete.[0m

[2m[loop 1 | ctx 8192/8192 (100%) | +4258 tok | 782 tok/s][0m

[1;31m[ai Error][0m request (8224 tokens) exceeds the available context size (8192 tokens), try increasing it
  ```
  </details>
- **Coding and Execution**:
  - **Failure/Timeout** (FAILED). Process took 0.4s.
  - Model managed to call: `['write_file(/path/to/bench_prime.py)']` before failing.
  <details>
  <summary>🔍 View Execution Output & Error Logs</summary>

  **Stdout:**
  ```
[2m────────────────────────────────────────────[0m

Write_script_bent_prime.py to calculate the sum of first 50 prime numbers using write_file
  ```

  **Stderr:**
  ```
[2m[ai] write_file: /path/to/bench_prime.py[0m

[2m[loop 1 | ctx 3977/8192 (48%) | +28 tok | 179 tok/s][0m

[2m[loop 2 | ctx 4054/8192 (49%) | +33 tok | 595 tok/s][0m
  ```
  </details>
- **Error Recovery & Troubleshooting**:
  - **Failure/Timeout** (FAILED). Process took 0.7s.
  - Model managed to call: `['write_file(bench_trouble.py)', 'execute_command(write_file)', 'edit_file(bench_trouble.py)']` before failing.
  <details>
  <summary>🔍 View Execution Output & Error Logs</summary>

  **Stdout:**
  ```
[2m────────────────────────────────────────────[0m

Replaced 'import not_a_real_package_123' with 'Imported' in bench_trouble.py. Tool executed write_file and then edited file.
  ```

  **Stderr:**
  ```
[2m[ai] write_file: bench_trouble.py[0m

[2m[loop 1 | ctx 4006/8192 (48%) | +27 tok | 246 tok/s][0m
[ai] executing command: write_file

[2m[loop 2 | ctx 4049/8192 (49%) | +14 tok | 0 tok/s][0m
[2m[ai] edit_file: bench_trouble.py[0m

[2m[loop 3 | ctx 4125/8192 (50%) | +34 tok | 611 tok/s][0m

[2m[loop 4 | ctx 4199/8192 (51%) | +45 tok | 638 tok/s][0m
  ```
  </details>
- **Web Search & Fetch**:
  - Completed successfully in 51.1 seconds over 5 turns.
  - Tools utilized: `['web_search("Google Antigravity AI coding assistant")', 'web_search("Google Antigravity codecademy")', 'web_search("Google Antigravity coding assistant tutorial")', 'web_search("Google Antigravity AI development tutorial")']`
  <details>
  <summary>🔍 View Execution Output</summary>

  **Stdout:**
  ```
[2m────────────────────────────────────────────[0m

Learning about Google Antigravity, an agentic development platform, and its role in agentic development environments. Instructors recommend installing and configuring the tool, exploring its key concepts, and customizing its settings. Antigravity needs to be locally installed on the system.
  ```
  </details>

---

### Model: `ornith-9b-mtp-kl-Q4_K_M.gguf`

| Task | Status | Duration | Turns | Tokens | Speed | Tools Called |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| Basic Tool Calling (list_directory) | ✅ SUCCESS | 2.39s | 2 | 148 | 77.5 t/s | `list_directory` |
| File Reading & Context Integration | ✅ SUCCESS | 2.44s | 2 | 285 | 122.0 t/s | `read_file` |
| Computer Use (Shell Execution) | ✅ SUCCESS | 1.66s | 2 | 187 | 122.5 t/s | `execute_command` |
| Coding and Execution | ✅ SUCCESS | 3.31s | 3 | 446 | 141.3 t/s | `write_file, execute_command` |
| Error Recovery & Troubleshooting | ✅ SUCCESS | 5.54s | 6 | 715 | 141.0 t/s | `write_file, execute_command, edit_file, execute_command, read_file` |
| Web Search & Fetch | ✅ SUCCESS | 20.96s | 2 | 648 | 137.0 t/s | `web_search` |

#### Task-by-Task Analysis
- **Basic Tool Calling (list_directory)**:
  - Completed successfully in 2.4 seconds over 2 turns.
  - Tools utilized: `['list_directory(?)']`
  <details>
  <summary>🔍 View Execution Output</summary>

  **Stdout:**
  ```
[2m────────────────────────────────────────────[0m

Listed the contents of /home/dzyla/ai-buddy. Contains 20 items: source files (ai.c, ai_mcp.py, cJSON, etc.), docs (LLM wrapper scripts, mcp config, benchmark results), and subdirectories (.agents, .claude, .git, docs).
  ```
  </details>
- **File Reading & Context Integration**:
  - Completed successfully in 2.4 seconds over 2 turns.
  - Tools utilized: `['read_file(bench_temp_cred.json)']`
  <details>
  <summary>🔍 View Execution Output</summary>

  **Stdout:**
  ```
[2m────────────────────────────────────────────[0m

Read bench_temp_cred.json and extracted the API key value.

• [1mFile:[22m [33mbench_temp_cred.json[39m
• [1mContents:[22m [33m{"api_key": "SECURE_KEY_12345", "user": "benchmark_tester"}[39m
• [1mAPI Key:[22m [33mSECURE_KEY_12345[39m
• [1mUser:[22m [33mbenchmark_tester[39m
  ```
  </details>
- **Computer Use (Shell Execution)**:
  - Completed successfully in 1.7 seconds over 2 turns.
  - Tools utilized: `['execute_command(uname -m)']`
  <details>
  <summary>🔍 View Execution Output</summary>

  **Stdout:**
  ```
[2m────────────────────────────────────────────[0m

CPU architecture: x86_64 (64-bit AMD64/Intel 64)
  ```
  </details>
- **Coding and Execution**:
  - Completed successfully in 3.3 seconds over 3 turns.
  - Tools utilized: `['write_file(/home/dzyla/ai-buddy/bench_prime.py)', 'execute_command(python3 /home/dzyla/ai-buddy/bench_prime.py)']`
  <details>
  <summary>🔍 View Execution Output</summary>

  **Stdout:**
  ```
[2m────────────────────────────────────────────[0m

The script [33mbench_prime.py[39m calculates the sum of the first 50 prime numbers. It uses a trial-division [33mis_prime[39m helper and iterates upward counting primes until 50 are found. The final sum is [1m5117[22m.
  ```
  </details>
- **Error Recovery & Troubleshooting**:
  - Completed successfully in 5.5 seconds over 6 turns.
  - Tools utilized: `['write_file(/home/dzyla/ai-buddy/bench_trouble.py)', 'execute_command(python /home/dzyla/ai-buddy/bench_trouble.py)', 'edit_file(/home/dzyla/ai-buddy/bench_trouble.py)', 'execute_command(python /home/dzyla/ai-buddy/bench_trouble.py)', 'read_file(/home/dzyla/ai-buddy/bench_trouble.py)']`
  <details>
  <summary>🔍 View Execution Output</summary>

  **Stdout:**
  ```
[2m────────────────────────────────────────────[0m

Successfully demonstrated exception handling in Python:
1. Wrote [33mbench_trouble.py[39m with [33mimport not_a_real_package_123[39m and [33mprint("Imported")[39m
2. Ran it — failed with [33mModuleNotFoundError: No module named 'not_a_real_package_123'[39m
3. Edited the file to wrap the import in [33mtry/except ImportError[39m printing "Recovered" instead
4. Ran it again — output: [33mRecovered[39m
  ```
  </details>
- **Web Search & Fetch**:
  - Completed successfully in 21.0 seconds over 2 turns.
  - Tools utilized: `['web_search("Google Antigravity AI coding assistant")']`
  <details>
  <summary>🔍 View Execution Output</summary>

  **Stdout:**
  ```
[2m────────────────────────────────────────────[0m

Searched for 'Google Antigravity AI coding assistant'. Google Antigravity is a real product by Google — an AI-powered developer platform with an IDE, CLI, SDK, and agent orchestration capabilities. It supports natural language code commands, autocompletion, and context-aware AI agents. Version 2.0 expanded it into an "agent-first development platform." Official getting-started codelab available.
  ```
  </details>

---

## 💡 Findings and Insights

Based on the benchmark execution:
1. **Task Execution Success**: `gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf` completed 6/6 tasks successfully, while `ornith-1.0-9b-Q4_K_M.gguf` completed 6/6 tasks.
2. **Token Throughput Speed**: `gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf` averaged `140.3 tok/s` vs. `ornith-1.0-9b-Q4_K_M.gguf`'s `108.0 tok/s` on the RTX 5080.
3. **Recommendation**: Both models exhibit similar reasoning capabilities. Choose the faster model (`gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf`).