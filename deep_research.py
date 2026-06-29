#!/usr/bin/env python3
"""
deep_research.py — deep research orchestrator for the ai CLI

Usage: python3 deep_research.py "topic"
       ai deep-research "topic"

Phases:
  1. Setup      — unique session folder under ~/.cache/ai/research/
  2. Plan       — 6-8 research questions with search queries (JSON via LLM)
  3. Fetch      — parallel sub-agents: search → fetch URLs → write source files
  4. Review     — reviewer agent cross-checks all source files
  5. Report     — report agent synthesizes + cites all sources
  6. Render     — render report.md to terminal
"""

import sys, os, json, re, subprocess, concurrent.futures, textwrap
from pathlib import Path
from datetime import datetime

# ── binary resolution ─────────────────────────────────────────────────────────

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
    today = datetime.now().strftime("%Y-%m-%d")
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
        # Date: {today}
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
    """tasks: list of (label, prompt). Returns list of (label, output) in input order."""
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

# ── JSON extraction ───────────────────────────────────────────────────────────

def extract_json(text: str) -> dict:
    """Find the first complete {...} block in text and parse it."""
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
    print(f"\n🔬 Deep Research: {topic}\n", flush=True)

    # ── Phase 1: Setup ────────────────────────────────────────────────────────
    print("[Phase 1/6] Setting up session folder...", flush=True)
    session_dir = make_session_dir(topic)
    print(f"  → {session_dir}\n", flush=True)

    # ── Phase 2: Research plan ────────────────────────────────────────────────
    print("[Phase 2/6] Generating research plan...", flush=True)
    plan_prompt = build_plan_prompt(topic)
    plan_raw = run_agent(plan_prompt, timeout=120, label="plan")
    try:
        plan = extract_json(plan_raw)
        questions = plan.get("questions", [])
        if not questions:
            raise ValueError("No questions in plan")
    except Exception as e:
        print(f"  ✗ Plan generation failed: {e}")
        print(f"  Raw output:\n{plan_raw[:800]}")
        sys.exit(1)

    plan_path = session_dir / "plan.json"
    plan_path.write_text(json.dumps(plan, indent=2))
    print(f"  ✓ {len(questions)} research questions generated:")
    for q in questions:
        print(f"    [{q['id']}] {q['question'][:80]}")
    print(flush=True)

    # ── Phase 3: Parallel source collection ──────────────────────────────────
    print(f"[Phase 3/6] Collecting sources ({len(questions)} questions × 3 queries each)...", flush=True)
    all_source_files = []
    index_entries = []

    for q in questions:
        q_id = q["id"]
        q_slug = re.sub(r'[^a-z0-9]', '-', q["question"].lower())[:30].strip('-')
        q_dir = session_dir / f"Q{q_id}_{q_slug}"
        q_dir.mkdir(exist_ok=True)

        queries = q.get("search_queries", [])[:3]
        fetch_tasks = []
        for j, query in enumerate(queries):
            out_file = str(q_dir / f"source_{j+1}.md")
            prompt = build_fetch_prompt(topic, q["question"], query, out_file)
            fetch_tasks.append((f"Q{q_id}.{j+1}", prompt))

        print(f"  Q{q_id}: spawning {len(fetch_tasks)} fetch agents...", end="", flush=True)
        agent_results = run_agents_parallel(fetch_tasks, timeout=300)
        ok = sum(1 for _, r in agent_results
                 if "AGENT_ERROR" not in r and "AGENT_TIMEOUT" not in r)
        print(f" {ok}/{len(fetch_tasks)} ok", flush=True)

        for j in range(len(queries)):
            f = q_dir / f"source_{j+1}.md"
            if f.exists() and f.stat().st_size > 50:
                all_source_files.append(str(f))
                index_entries.append(f"- Q{q_id} / source {j+1}: `{f}`")

    index_path = session_dir / "index.md"
    index_path.write_text(
        f"# Source Index: {topic}\n\nGenerated: {datetime.now().isoformat()}\n\n"
        + "\n".join(index_entries) + "\n"
    )
    print(f"\n  ✓ {len(all_source_files)} source files collected\n", flush=True)

    if not all_source_files:
        print("  ✗ No source files were written. Check that the ai binary works and env vars are set.")
        print(f"  Session folder preserved at: {session_dir}")
        sys.exit(1)

    # ── Phase 4: Reviewer ─────────────────────────────────────────────────────
    print("[Phase 4/6] Running reviewer agent...", flush=True)
    review_path = str(session_dir / "review.md")
    reviewer_prompt = build_reviewer_prompt(topic, all_source_files, review_path)
    review_out = run_agent(reviewer_prompt, timeout=600, label="reviewer")
    review_exists = (session_dir / "review.md").exists()
    print(f"  {'✓ Review saved' if review_exists else '⚠ Review agent did not write file (continuing)'}\n",
          flush=True)

    # ── Phase 5: Report generation ────────────────────────────────────────────
    print("[Phase 5/6] Generating report (this may take a few minutes)...", flush=True)
    report_path = str(session_dir / "report.md")
    report_prompt = build_report_prompt(
        topic, all_source_files, review_path, report_path, str(session_dir)
    )
    run_agent(report_prompt, timeout=900, label="report")
    report_file = session_dir / "report.md"
    if not report_file.exists():
        print("  ✗ Report generation failed.")
        print(f"  All source files are preserved at: {session_dir}")
        sys.exit(1)
    print(f"  ✓ Report saved: {report_path}\n", flush=True)

    # ── Phase 6: Render ───────────────────────────────────────────────────────
    print("[Phase 6/6] Rendering report...\n", flush=True)
    print("=" * 72)
    report_text = report_file.read_text()
    try:
        subprocess.run(
            [sys.executable, MCP_PY, "render-markdown", report_text],
            text=True
        )
    except Exception:
        print(report_text)
    print("=" * 72)
    print(f"\n✓ Research complete.")
    print(f"  Report  : {report_path}")
    print(f"  Sources : {len(all_source_files)} files")
    print(f"  Folder  : {session_dir}\n")


if __name__ == "__main__":
    main()
