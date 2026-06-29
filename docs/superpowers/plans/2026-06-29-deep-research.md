# Deep Research System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deep research orchestration system for the `ai` CLI that autonomously plans, fetches, reviews, and reports on any topic using parallel sub-agents and file-based hand-off.

**Architecture:** A Python orchestrator (`deep_research.py`) drives the workflow across six phases (setup → plan → fetch → review → report → render), spawning `ai` sub-agents for each creative step while the mechanical coordination stays in deterministic Python code. A SKILL.md triggers the workflow from natural language; a minimal `ai.c` change adds a `deep-research` argv shortcut.

**Tech Stack:** Python 3 (stdlib only: subprocess, concurrent.futures, json, pathlib, datetime, hashlib), `ai` binary (existing), `ai_mcp.py render-markdown` (existing)

## Global Constraints

- No new Python dependencies — stdlib only
- Sub-agent invocations use `ai -y -q -auto` to suppress prompts
- `INFER_TASK_TIMEOUT` env var set per-phase: 300s fetch, 600s review, 900s report
- Session folder: `~/.cache/ai/research/YYYYMMDD_HHMMSS_<slug>/`
- Slug = first 3 words of topic, lowercased, hyphens, alphanumeric only
- Every sub-agent prompt ends with an explicit anti-hallucination rule
- Resolve `ai` binary: `./ai` first, then `~/.local/bin/ai`
- Resolve `deep_research.py`: `./deep_research.py` first, then `~/.local/bin/deep_research.py`

---

### Task 1: Session setup + research plan generation

**Files:**
- Create: `deep_research.py`

**Interfaces:**
- Produces: `session_dir: Path`, `plan: dict` with keys `topic`, `overview`, `questions: list[{id, question, search_queries, expected_sources}]`

- [ ] **Step 1: Write the failing smoke test**

Create `test_deep_research.py`:

```python
import subprocess, sys, os

def test_plan_json_structure():
    """Verify plan generation produces valid JSON with required keys."""
    import json, pathlib, tempfile
    # We'll mock the ai binary call by inspecting the prompt template
    # instead of running a real LLM call in the test
    import importlib.util, types
    # Load the module without executing __main__
    spec = importlib.util.spec_from_file_location("dr", "deep_research.py")
    dr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dr)

    prompt = dr.build_plan_prompt("quantum computing")
    assert "quantum computing" in prompt
    assert "JSON" in prompt
    assert "questions" in prompt
    assert "search_queries" in prompt

def test_session_dir_format():
    import importlib.util
    spec = importlib.util.spec_from_file_location("dr", "deep_research.py")
    dr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dr)

    d = dr.make_session_dir("quantum computing basics")
    assert "quantum" in str(d)
    assert "computing" in str(d)
    # dir name matches YYYYMMDD_HHMMSS_slug pattern
    import re
    assert re.search(r'\d{8}_\d{6}_', str(d))
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/dzyla/Code/ai-buddy && python3 -m pytest test_deep_research.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError` or `AttributeError` (file doesn't exist yet)

- [ ] **Step 3: Implement session setup + plan prompt builder**

Create `deep_research.py`:

```python
#!/usr/bin/env python3
"""
deep_research.py — deep research orchestrator for the ai CLI
Usage: python3 deep_research.py "topic" [--output-dir DIR]
"""

import sys, os, json, re, subprocess, concurrent.futures, textwrap
from pathlib import Path
from datetime import datetime

# ── binary resolution ────────────────────────────────────────────────────────
def _resolve_ai_bin():
    for p in ["./ai", os.path.expanduser("~/.local/bin/ai"), "/usr/local/bin/ai"]:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return "ai"

def _resolve_mcp_py():
    for p in ["./ai_mcp.py", os.path.expanduser("~/.local/bin/ai_mcp.py")]:
        if os.path.isfile(p):
            return p
    return "ai_mcp.py"

AI_BIN = _resolve_ai_bin()
MCP_PY = _resolve_mcp_py()

# ── session directory ─────────────────────────────────────────────────────────
def make_session_dir(topic: str) -> Path:
    words = re.sub(r'[^a-z0-9 ]', '', topic.lower()).split()
    slug = "-".join(words[:3]) if words else "research"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = Path.home() / ".cache" / "ai" / "research" / f"{ts}_{slug}"
    base.mkdir(parents=True, exist_ok=True)
    return base

# ── prompt builders ───────────────────────────────────────────────────────────
def build_plan_prompt(topic: str) -> str:
    return textwrap.dedent(f"""
        Generate a JSON research plan for the topic: "{topic}"

        Output ONLY valid JSON. No explanation, no markdown fences, no extra text.

        {{
          "topic": "{topic}",
          "overview": "2-sentence context about this topic",
          "questions": [
            {{
              "id": 1,
              "question": "specific research question",
              "search_queries": ["query1", "query2", "query3"],
              "expected_sources": "types of sites: academic, news, official docs"
            }}
          ]
        }}

        Requirements:
        - Generate exactly 6 to 8 questions
        - Cover these angles: background/history, current state, key findings,
          data and statistics, controversies or debates, future directions,
          key people or organizations, practical applications
        - Make search queries specific and distinct (not paraphrases of each other)
        - Output ONLY the JSON object, no other text before or after it
    """).strip()


def build_fetch_prompt(topic: str, question: str, query: str, output_file: str) -> str:
    return textwrap.dedent(f"""
        You are a research assistant. FOLLOW THESE STEPS EXACTLY IN ORDER. Do not skip any step.

        RESEARCH TOPIC: {topic}
        QUESTION: {question}
        SEARCH QUERY: {query}
        OUTPUT FILE: {output_file}

        STEP 1: Use web_search with this exact query: "{query}"
        STEP 2: From the results, identify the 2-3 URLs most relevant to the question above.
        STEP 3: Use fetch_smart to read EACH of those URLs. Do not skip any.
        STEP 4: Use write_file to write the following to OUTPUT FILE exactly:

        # Source URLs: [list all URLs you read, comma separated]
        # Date: {datetime.now().strftime("%Y-%m-%d")}
        # Question: {question}

        ## Summary
        [300-400 word summary of what these sources say about the question.
         Include specific facts, numbers, and direct quotes.]

        ## Key Facts
        - [each important data point as a bullet]
        - [include numbers, dates, names where present]

        ## Source Quality
        [1-2 sentences: are these sources reliable? academic? primary? biased?]

        STEP 5: Call task_complete with message: "Saved: {output_file}"

        CRITICAL RULES — you MUST follow these:
        - Write ONLY information found in the fetched pages
        - Do NOT add information from your training data or memory
        - If fetch_smart fails on a URL, try the next result URL from web_search
        - If no useful content is found after trying 3 URLs, write "NO_CONTENT_FOUND" as the summary
        - Never fabricate statistics, quotes, or facts
    """).strip()


def build_reviewer_prompt(topic: str, source_files: list, review_path: str) -> str:
    files_list = "\n".join(f"  {f}" for f in source_files)
    return textwrap.dedent(f"""
        You are a critical research reviewer.

        TOPIC: {topic}
        REVIEW OUTPUT FILE: {review_path}

        SOURCE FILES TO READ (use read_file on EVERY one):
        {files_list}

        STEP 1: Use read_file to read EVERY file listed above, one by one.
        STEP 2: Use write_file to write a review to: {review_path}

        Review format (use these exact headings):
        # Research Review: {topic}

        ## Coverage Assessment
        [Which research questions are well-covered? Which are thin or missing data?]

        ## Contradictions Found
        [List any claims that conflict between sources. Quote the conflicting statements.]

        ## Source Credibility Notes
        [Which sources are strongest? Flag any low-quality, biased, or outdated sources.]

        ## Information Gaps
        [What important aspects of the topic are missing from the sources?]

        ## Recommended Additional Searches
        - [specific search query that would fill the biggest gap]
        - [second recommended search]
        - [third recommended search]

        STEP 3: Call task_complete with: "Review saved to {review_path}"

        RULE: Base your review ONLY on what you read in the source files.
    """).strip()


def build_report_prompt(topic: str, source_files: list, review_path: str,
                         report_path: str, session_dir: str) -> str:
    files_list = "\n".join(f"  {f}" for f in source_files)
    n_sources = len(source_files)
    today = datetime.now().strftime("%Y-%m-%d")
    return textwrap.dedent(f"""
        You are an expert research writer. Your job is to synthesize research into a
        comprehensive, well-cited report.

        TOPIC: {topic}
        REPORT OUTPUT FILE: {report_path}
        DATE: {today}

        SOURCE FILES (use read_file to read ALL of them):
        {files_list}

        REVIEW FILE (read this too): {review_path}

        STEP 1: Use read_file to read EVERY source file and the review file.
        STEP 2: Use write_file to write the complete report to: {report_path}

        Report structure (follow EXACTLY — use these headings):

        # Deep Research Report: {topic}
        *Generated: {today} | Sources reviewed: {n_sources}*

        ## Executive Summary
        [3-5 sentences covering the most critical findings. Be specific with data.]

        ## Background & Context
        [What is this topic? Why does it matter now? Historical context if relevant.]

        ## Key Findings
        [Use ### subheadings for each major theme. 2-4 paragraphs per theme.
         Every factual claim must have an inline citation like [1] or [2].]

        ## Data & Statistics
        [Present key numbers as a table or structured bullet list with citations.]

        ## Contradictions & Open Questions
        [Use the review file: what is disputed, unclear, or actively debated?]

        ## Conclusions & Implications
        [What can be concluded from the evidence? What remains uncertain?]

        ## Sources
        [Numbered list matching your inline citations:
         [1] Page Title — https://url.example.com
         [2] ...
        ]

        Citation rules (MUST follow):
        - Every factual claim requires an inline citation [N]
        - Only cite sources from the source files you read
        - Do NOT add information from your training data
        - If a source file says NO_CONTENT_FOUND, skip that file

        STEP 3: Call task_complete with: "Report saved to {report_path}"
    """).strip()


# ── sub-agent runner ──────────────────────────────────────────────────────────
def run_agent(prompt: str, timeout: int = 300, label: str = "") -> str:
    env = os.environ.copy()
    env["INFER_TASK_TIMEOUT"] = str(timeout)
    try:
        result = subprocess.run(
            [AI_BIN, "-y", "-q", "-auto", prompt],
            capture_output=True, text=True, timeout=timeout + 30, env=env
        )
        out = result.stdout.strip()
        if not out and result.returncode != 0:
            err = result.stderr.strip()[:500]
            return f"[AGENT_ERROR exit={result.returncode}] {err}"
        return out or f"[AGENT_DONE exit={result.returncode}]"
    except subprocess.TimeoutExpired:
        return f"[AGENT_TIMEOUT after {timeout}s]"
    except Exception as e:
        return f"[AGENT_EXCEPTION] {e}"


def run_agents_parallel(tasks: list, timeout: int = 300) -> list:
    """tasks: list of (label, prompt). Returns list of (label, output) in order."""
    results = [None] * len(tasks)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as ex:
        futures = {ex.submit(run_agent, prompt, timeout, label): i
                   for i, (label, prompt) in enumerate(tasks)}
        for fut in concurrent.futures.as_completed(futures):
            i = futures[fut]
            label = tasks[i][0]
            try:
                results[i] = (label, fut.result())
            except Exception as e:
                results[i] = (label, f"[THREAD_ERROR] {e}")
    return results


# ── JSON plan extraction ──────────────────────────────────────────────────────
def extract_json(text: str) -> dict:
    """Find the first {...} block in text and parse it."""
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in response")
    return json.loads(text[start:end])


# ── main orchestrator ─────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print("Usage: deep_research.py \"topic\"", file=sys.stderr)
        sys.exit(1)

    topic = " ".join(sys.argv[1:])
    print(f"\n🔬 Deep Research: {topic}\n")

    # Phase 1: Setup
    print("[Phase 1/6] Setting up session folder...")
    session_dir = make_session_dir(topic)
    print(f"  → {session_dir}\n")

    # Phase 2: Research plan
    print("[Phase 2/6] Generating research plan...")
    plan_prompt = build_plan_prompt(topic)
    plan_raw = run_agent(plan_prompt, timeout=120, label="plan")
    try:
        plan = extract_json(plan_raw)
        questions = plan.get("questions", [])
        if not questions:
            raise ValueError("No questions in plan")
    except Exception as e:
        print(f"  ✗ Plan generation failed: {e}")
        print(f"  Raw output: {plan_raw[:500]}")
        sys.exit(1)

    plan_path = session_dir / "plan.json"
    plan_path.write_text(json.dumps(plan, indent=2))
    print(f"  ✓ {len(questions)} research questions generated")
    for q in questions:
        print(f"    [{q['id']}] {q['question'][:80]}")
    print()

    # Phase 3: Parallel source collection
    print(f"[Phase 3/6] Collecting sources ({len(questions)} questions × 3 queries)...")
    all_source_files = []
    index_entries = []

    for q in questions:
        q_id = q["id"]
        q_slug = re.sub(r'[^a-z0-9]', '-', q["question"].lower())[:30].strip('-')
        q_dir = session_dir / f"Q{q_id}_{q_slug}"
        q_dir.mkdir(exist_ok=True)

        queries = q.get("search_queries", [])[:3]  # cap at 3 per question
        fetch_tasks = []
        for j, query in enumerate(queries):
            out_file = str(q_dir / f"source_{j+1}.md")
            prompt = build_fetch_prompt(topic, q["question"], query, out_file)
            fetch_tasks.append((f"Q{q_id}.{j+1}", prompt))

        print(f"  Q{q_id}: spawning {len(fetch_tasks)} fetch agents...", end="", flush=True)
        results = run_agents_parallel(fetch_tasks, timeout=300)
        ok = sum(1 for _, r in results if "AGENT_ERROR" not in r and "AGENT_TIMEOUT" not in r)
        print(f" {ok}/{len(fetch_tasks)} ok")

        # Collect files that were actually written
        for j in range(len(queries)):
            f = q_dir / f"source_{j+1}.md"
            if f.exists() and f.stat().st_size > 50:
                all_source_files.append(str(f))
                index_entries.append(f"- Q{q_id} / source {j+1}: `{f}`")

    # Write index
    index_path = session_dir / "index.md"
    index_path.write_text(
        f"# Source Index: {topic}\n\nGenerated: {datetime.now().isoformat()}\n\n"
        + "\n".join(index_entries) + "\n"
    )
    print(f"\n  ✓ {len(all_source_files)} source files collected\n")

    if not all_source_files:
        print("  ✗ No source files were written. Aborting.")
        sys.exit(1)

    # Phase 4: Reviewer
    print("[Phase 4/6] Running reviewer agent...")
    review_path = str(session_dir / "review.md")
    reviewer_prompt = build_reviewer_prompt(topic, all_source_files, review_path)
    run_agent(reviewer_prompt, timeout=600, label="reviewer")
    review_exists = (session_dir / "review.md").exists()
    print(f"  ✓ Review {'saved' if review_exists else 'FAILED (will continue)'}\n")

    # Phase 5: Report generation
    print("[Phase 5/6] Generating report (this may take a few minutes)...")
    report_path = str(session_dir / "report.md")
    report_prompt = build_report_prompt(
        topic, all_source_files, review_path, report_path, str(session_dir)
    )
    run_agent(report_prompt, timeout=900, label="report")
    report_file = session_dir / "report.md"
    if not report_file.exists():
        print("  ✗ Report generation failed. Sources are saved at:", session_dir)
        sys.exit(1)
    print(f"  ✓ Report saved: {report_path}\n")

    # Phase 6: Render
    print("[Phase 6/6] Rendering report...\n")
    print("=" * 72)
    try:
        report_text = report_file.read_text()
        render = subprocess.run(
            [sys.executable, MCP_PY, "render-markdown", report_text],
            capture_output=False, text=True
        )
    except Exception as e:
        print(report_file.read_text())
    print("=" * 72)
    print(f"\n✓ Research complete. All files saved to:\n  {session_dir}\n")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/dzyla/Code/ai-buddy && python3 -m pytest test_deep_research.py -v 2>&1
```

Expected: both tests PASS

- [ ] **Step 5: Quick sanity check — verify the script is importable and prints usage**

```bash
cd /home/dzyla/Code/ai-buddy && python3 deep_research.py 2>&1
```

Expected: `Usage: deep_research.py "topic"`

- [ ] **Step 6: Commit**

```bash
cd /home/dzyla/Code/ai-buddy
git add deep_research.py test_deep_research.py
git commit -m "feat: add deep_research.py orchestrator (plan + fetch + review + report)"
```

---

### Task 2: SKILL.md for natural-language trigger

**Files:**
- Create: `.agents/skills/deep_research/SKILL.md`

**Interfaces:**
- Consumes: `deep_research.py` (from Task 1)
- Produces: skill loaded into ai system prompt; model routes "deep research" requests to the script

- [ ] **Step 1: Create the skill directory and write SKILL.md**

```bash
mkdir -p /home/dzyla/Code/ai-buddy/.agents/skills/deep_research
```

Write `.agents/skills/deep_research/SKILL.md`:

```markdown
---
name: deep-research
description: >
  Activate when the user asks for "deep research", "comprehensive research",
  "in-depth investigation", "thoroughly research X", or similar phrasing
  indicating they want multi-source, structured research on a topic.
---

# Deep Research Skill

## When to activate

Activate this skill when the user says any of:
- "do deep research on X"
- "deep dive into X"
- "comprehensive research on X"
- "thoroughly research X"
- "investigate X in depth"
- "research X like Gemini/ChatGPT deep research"

## How to invoke

**Step 1:** Extract the topic from the user's message.

**Step 2:** Run the orchestrator script:

```
execute_command("python3 deep_research.py \"<TOPIC>\"")
```

Resolve the script path:
- Try `./deep_research.py` first (repo/working directory)
- Fall back to `~/.local/bin/deep_research.py`

Full command:
```
execute_command("python3 ./deep_research.py \"<TOPIC>\" || python3 ~/.local/bin/deep_research.py \"<TOPIC>\"")
```

**Step 3:** Wait for the script to finish. It will print live progress and render the final report automatically.

**Step 4:** After it completes, call task_complete with a brief summary:
```
"Deep research on '<TOPIC>' complete. Report and {N} source files saved to ~/.cache/ai/research/<session>/"
```

## What the script does

The orchestrator runs these phases automatically — you do not need to manage them:
1. Creates a unique session folder under `~/.cache/ai/research/`
2. Generates a 6–8 question research plan (JSON)
3. Spawns parallel fetch agents (3 per question) that search, fetch URLs, and save summaries
4. Runs a reviewer agent that cross-checks all sources
5. Generates a comprehensive report with inline citations
6. Renders the report to the terminal

## Do not

- Do not run web_search yourself before invoking the script
- Do not try to manually orchestrate the steps — the script handles everything
- Do not call task_complete until execute_command returns
```

- [ ] **Step 2: Verify skill file is valid**

```bash
head -5 /home/dzyla/Code/ai-buddy/.agents/skills/deep_research/SKILL.md
```

Expected: frontmatter with `name: deep-research`

- [ ] **Step 3: Commit**

```bash
cd /home/dzyla/Code/ai-buddy
git add .agents/skills/deep_research/SKILL.md
git commit -m "feat: add deep_research SKILL.md for natural language trigger"
```

---

### Task 3: ai.c deep-research argv shortcut

**Files:**
- Modify: `ai.c` (add `deep-research` sub-command handling near top of `main()`)

**Interfaces:**
- Consumes: `deep_research.py` (Task 1)
- Produces: `ai deep-research "topic"` invocation that bypasses LLM loop

- [ ] **Step 1: Find the right insertion point in ai.c**

```bash
grep -n "argc\|argv\|interactive\|getenv" /home/dzyla/Code/ai-buddy/ai.c | head -40
```

Look for where `main()` starts processing argv (typically early in `main`, before `getenv` calls for `INFER_BASE_URL`).

- [ ] **Step 2: Add the deep-research sub-command**

Find the line in `main()` that looks like this (the start of env-var validation):

```c
    char *base_url = getenv("INFER_BASE_URL");
```

Insert BEFORE that line:

```c
    /* deep-research sub-command: bypass LLM loop, delegate to Python orchestrator */
    if (argc >= 2 && strcmp(argv[1], "deep-research") == 0) {
        if (argc < 3) {
            fprintf(stderr, "Usage: ai deep-research \"topic\"\n");
            return 1;
        }
        /* Build topic string from remaining argv */
        char topic[4096] = {0};
        for (int i = 2; i < argc; i++) {
            if (i > 2) strncat(topic, " ", sizeof(topic) - strlen(topic) - 1);
            strncat(topic, argv[i], sizeof(topic) - strlen(topic) - 1);
        }
        /* Resolve script path: ./deep_research.py first, then ~/.local/bin/ */
        char script[1024];
        if (access("./deep_research.py", R_OK) == 0) {
            snprintf(script, sizeof(script), "./deep_research.py");
        } else {
            const char *home = getenv("HOME");
            snprintf(script, sizeof(script), "%s/.local/bin/deep_research.py",
                     home ? home : "~");
        }
        char cmd[8192];
        snprintf(cmd, sizeof(cmd), "python3 %s \"%s\"", script, topic);
        return system(cmd);
    }
```

- [ ] **Step 3: Build and test the binary**

```bash
cd /home/dzyla/Code/ai-buddy && gcc -o ai ai.c cJSON.c -lcurl 2>&1
```

Expected: no errors

```bash
./ai deep-research 2>&1
```

Expected: `Usage: ai deep-research "topic"`

- [ ] **Step 4: Commit**

```bash
cd /home/dzyla/Code/ai-buddy
git add ai.c
git commit -m "feat: add 'ai deep-research' argv shortcut in ai.c"
```

---

### Task 4: install.sh update

**Files:**
- Modify: `install.sh`

**Interfaces:**
- Produces: `deep_research.py` installed to `~/.local/bin/deep_research.py` on every `./install.sh`

- [ ] **Step 1: Find where install.sh copies files**

```bash
grep -n "local/bin\|cp \|install" /home/dzyla/Code/ai-buddy/install.sh | head -20
```

- [ ] **Step 2: Add deep_research.py to the install**

Find the line that installs the `ai` binary (something like `cp ai ~/.local/bin/ai`). After it, add:

```bash
cp deep_research.py ~/.local/bin/deep_research.py
chmod +x ~/.local/bin/deep_research.py
```

- [ ] **Step 3: Run install.sh to verify**

```bash
cd /home/dzyla/Code/ai-buddy && ./install.sh 2>&1 | tail -20
```

Expected: no errors, new binary present

```bash
ls -la ~/.local/bin/deep_research.py
```

Expected: file exists, executable

- [ ] **Step 4: Commit**

```bash
cd /home/dzyla/Code/ai-buddy
git add install.sh
git commit -m "feat: install deep_research.py to ~/.local/bin on install"
```

---

### Task 5: Integration test

**Files:**
- No new files — this task verifies the full stack end-to-end

**Goal:** Call `ai "do deep research on X"` and verify the workflow runs, source files are written, and a report is produced.

- [ ] **Step 1: Verify environment is set**

```bash
echo "BASE_URL: $INFER_BASE_URL"
echo "MODEL:    $INFER_MODEL"
echo "KEY set:  $([ -n "$INFER_API_KEY" ] && echo yes || echo NO)"
```

If any are missing, source your env file: `source ~/.config/ai/env`

- [ ] **Step 2: Run the integration test**

```bash
cd /home/dzyla/Code/ai-buddy && ./ai "do deep research on the current state of nuclear fusion energy"
```

Watch for:
- `[Phase 1/6]` — session folder printed
- `[Phase 2/6]` — research plan with 6–8 questions
- `[Phase 3/6]` — fetch agents running per question
- `[Phase 4/6]` — reviewer running
- `[Phase 5/6]` — report generation (may take 2–5 minutes)
- `[Phase 6/6]` — report rendered to terminal

- [ ] **Step 3: Verify output files exist**

```bash
LATEST=$(ls -td ~/.cache/ai/research/*/ | head -1)
echo "Session: $LATEST"
ls "$LATEST"
echo "---"
find "$LATEST" -name "*.md" | head -20
```

Expected:
- `plan.json` exists
- Multiple `Q*/source_*.md` files exist with content
- `review.md` exists
- `report.md` exists (>500 bytes)

- [ ] **Step 4: Check report quality**

```bash
LATEST=$(ls -td ~/.cache/ai/research/*/ | head -1)
wc -w "$LATEST/report.md"
grep -c '\[' "$LATEST/report.md"  # count citation markers
```

Expected:
- Report is >500 words
- At least 5 citation markers `[N]` present

- [ ] **Step 5: Test the CLI shortcut**

```bash
cd /home/dzyla/Code/ai-buddy && ./ai deep-research "benefits of intermittent fasting"
```

Verify same phase progression and output files.

- [ ] **Step 6: If any phase fails, diagnose**

Check which source files were written:
```bash
LATEST=$(ls -td ~/.cache/ai/research/*/ | head -1)
for f in "$LATEST"/Q*/source_*.md; do
    echo "=== $f ==="
    head -5 "$f"
done
```

If sub-agents are timing out, increase timeout in `deep_research.py`:
- Find `run_agents_parallel(fetch_tasks, timeout=300)` → change to `timeout=600`
- Find `run_agent(reviewer_prompt, timeout=600)` → change to `timeout=900`

---

## Self-review

**Spec coverage:**
- ✓ Unique session folder (timestamp + slug)
- ✓ Research plan with 6–8 questions and 3 search queries each
- ✓ Parallel fetch sub-agents (3 per question)
- ✓ Source files saved to disk with structured format
- ✓ Reviewer agent reads all sources, writes review.md
- ✓ Report agent writes report.md with inline citations
- ✓ Report rendered to terminal
- ✓ SKILL.md triggers from natural language
- ✓ `ai deep-research "topic"` CLI shortcut
- ✓ install.sh updated
- ✓ Anti-hallucination rules in every sub-agent prompt
- ✓ Error-tolerant (failed agents skipped, not fatal)

**Placeholder scan:** None found.

**Type consistency:** All function signatures defined in Task 1 and referenced consistently in later tasks.
