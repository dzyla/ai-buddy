# Deep Research System — Design Spec

## What this builds

A deep research capability for the `ai` CLI that mirrors Google Gemini / OpenAI deep research: given a topic, the system autonomously generates a research plan, dispatches parallel fetch sub-agents, saves structured source summaries to disk, runs a cross-check reviewer, and produces a rich final report with inline citations.

## User experience

```
# Natural language route (SKILL.md triggers):
ai "do deep research on X"

# Direct CLI shortcut:
ai deep-research "X"
```

Terminal shows live phase progress. When complete, the full report renders in the terminal and all files are saved to `~/.cache/ai/research/<session>/`.

---

## Architecture

### Two-file delivery

| File | Role |
|------|------|
| `deep_research.py` | Python orchestrator — mechanical workflow, no LLM reasoning required |
| `.agents/skills/deep_research/SKILL.md` | Tells the main model when and how to invoke the script |

`ai.c` gets a minimal `deep-research` argv shortcut (8 lines) that bypasses the LLM loop entirely.

### Why Python orchestrator, not a skill alone

- Local models lose track across multi-step skills with many iterations
- Sub-agent output cap is 10 KB — file-based hand-off is the only reliable way to pass large summaries
- The workflow is deterministic: each phase has a hardcoded template; the model only does the creative parts (write plan JSON, write summaries, write report)

---

## Data flow

```
Phase 1: Setup
  └─ Create ~/.cache/ai/research/<YYYYMMDD_HHMMSS>_<slug>/

Phase 2: Research Plan  (1 ai sub-agent → JSON)
  └─ 6–8 questions, each with 3 search queries + expected source types
  └─ Saved as plan.json

Phase 3: Parallel Source Collection
  └─ For each question: 3–4 parallel fetch sub-agents (INFER_TASK_TIMEOUT=600)
       └─ web_search → fetch_smart 2–3 URLs → write_file summary
       └─ Each sub-agent returns file path only (bypasses 10 KB output cap)
  └─ Results: Q<N>_<slug>/source_<M>.md

Phase 4: Reviewer Agent  (1 ai sub-agent, INFER_TASK_TIMEOUT=600)
  └─ Reads all source files → write_file review.md
       (gaps, contradictions, credibility)

Phase 5: Report Agent  (1 ai sub-agent, INFER_TASK_TIMEOUT=900)
  └─ Reads all sources + review → write_file report.md
       (1500–3000 words, inline citations [1] [2])

Phase 6: Render
  └─ python3 ai_mcp.py render-markdown < report.md
  └─ Print session folder path
```

---

## Session folder layout

```
~/.cache/ai/research/20260629_143022_quantum-computing/
├── plan.json
├── index.md           ← auto-generated list of all source files
├── Q1_background/
│   ├── source_1.md
│   ├── source_2.md
│   └── source_3.md
├── Q2_current-state/
│   └── ...
├── review.md
└── report.md
```

---

## Sub-agent prompt templates

### Plan generation prompt
```
Generate a JSON research plan for the topic: "<TOPIC>"

Output ONLY valid JSON. No explanation, no markdown fences.

{
  "topic": "<TOPIC>",
  "overview": "2-sentence context",
  "questions": [
    {
      "id": 1,
      "question": "specific research question",
      "search_queries": ["query1", "query2", "query3"],
      "expected_sources": "types of sites: academic, news, official docs"
    }
  ]
}

Requirements:
- 6 to 8 questions
- Cover: background, current state, key findings, data/statistics, controversies, future directions
- Make search queries specific and distinct (not paraphrases of each other)
- Output ONLY the JSON object, nothing else
```

### Fetch sub-agent prompt
```
You are a research assistant. FOLLOW THESE STEPS EXACTLY IN ORDER.

RESEARCH TOPIC: <TOPIC>
QUESTION: <QUESTION>
SEARCH QUERY: <QUERY>
OUTPUT FILE: <PATH>

STEP 1: Use web_search with this exact query: "<QUERY>"
STEP 2: From the results, identify the 2-3 URLs most relevant to the question
STEP 3: Use fetch_smart to read EACH of those URLs (do not skip any)
STEP 4: Use write_file to write the following to OUTPUT FILE:

# Source URLs: <comma-separated list of URLs you read>
# Date: <today's date>
# Question: <QUESTION>

## Summary
[300-400 word summary of what these sources say about the question.
 Include specific facts, numbers, and quotes.]

## Key Facts
- [each important data point as a bullet]
- [include numbers, dates, names where present]

## Source Quality
[1-2 sentences: are these sources reliable? academic? primary? biased?]

STEP 5: Call task_complete with message: "Saved: <OUTPUT FILE>"

CRITICAL RULES:
- Write ONLY information found in the fetched pages
- Do NOT add information from your training data
- If fetch_smart fails on a URL, try the next result URL
- If no useful content found, write "NO_CONTENT" as the summary
```

### Reviewer prompt
```
You are a critical research reviewer.

TOPIC: <TOPIC>
SOURCE FILES TO READ:
<list of all source file paths, one per line>

STEP 1: Use read_file to read EVERY file listed above
STEP 2: Use write_file to write a review to: <REVIEW_PATH>

Review format:
# Research Review: <TOPIC>

## Coverage Assessment
[Which questions are well-covered? Which are thin or missing?]

## Contradictions
[List any claims that conflict between sources. Quote the conflicting statements.]

## Source Credibility
[Which sources are strongest? Any low-quality or biased sources to note?]

## Data Gaps
[What important data is missing? What would strengthen the research?]

## Recommended Additional Searches
- [specific search query 1]
- [specific search query 2]
- [specific search query 3]

STEP 3: Call task_complete with: "Review saved"
```

### Report generation prompt
```
You are an expert research writer.

TOPIC: <TOPIC>
SESSION FOLDER: <FOLDER>

SOURCE FILES (read ALL of them with read_file):
<list of all source file paths>

REVIEW FILE: <REVIEW_PATH>

STEP 1: Use read_file to read EVERY source file AND the review file
STEP 2: Use write_file to write the report to: <REPORT_PATH>

Report structure (use this EXACTLY):
# Deep Research Report: <TOPIC>
*Generated: <date> | Sources: <N> | Research questions: <N>*

## Executive Summary
[3-5 sentences covering the most important findings]

## Background & Context
[What is this topic? Why does it matter?]

## Key Findings
[One section per major research question. Use ### subheadings.]

## Data & Statistics
[Table or bullet list of the most important numbers found]

## Contradictions & Open Questions
[Based on review.md: what is disputed or unclear?]

## Conclusions
[What can be concluded? What remains uncertain?]

## Sources
[Numbered list: [1] Title — URL]

Citation rules:
- Cite inline as [1], [2] etc. matching the Sources list
- Every factual claim must have a citation
- Do NOT add information from training data — only cite the source files

STEP 3: Call task_complete with: "Report saved to <REPORT_PATH>"
```

---

## anti-hallucination design

- Every sub-agent prompt contains the rule: **"Write ONLY information found in the fetched pages. Do NOT add information from your training data."**
- Reviewer specifically checks for contradictions and gaps
- Report agent is told: **"Every factual claim must have a citation"** and **"only cite the source files"**
- Source files are saved to disk and re-readable — reviewer and report agent work from files, not from memory

---

## ai.c change (minimal)

In `main()`, before the existing argc/argv handling, add:

```c
if (argc >= 3 && strcmp(argv[1], "deep-research") == 0) {
    /* Build the rest of argv into a single topic string */
    /* Then exec: python3 deep_research.py "<topic>" */
}
```

Resolve `deep_research.py` from `./` first, then `~/.local/bin/`.

---

## install.sh change

Copy `deep_research.py` to `~/.local/bin/deep_research.py` on every install, same as the skills sync.

---

## Constraints

- All sub-agent timeouts set via `INFER_TASK_TIMEOUT` env var on each subprocess
- Sub-agent output cap (10 KB) is irrelevant — agents write files and return short confirmation strings
- Works with any OpenAI-compatible backend (local or cloud)
- No new Python dependencies beyond what `ai_mcp.py` already uses
- Session folder name: `YYYYMMDD_HHMMSS_<first-3-words-slug>`
