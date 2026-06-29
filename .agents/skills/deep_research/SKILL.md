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
- "deep research X"

## How to invoke

**Step 1:** Extract the topic from the user's message.

**Step 2:** Run the orchestrator script using execute_command.

Resolve the script path — try `./deep_research.py` first, then fall back to `~/.local/bin/deep_research.py`:

```
execute_command("python3 ./deep_research.py \"<TOPIC>\" 2>&1 || python3 ~/.local/bin/deep_research.py \"<TOPIC>\" 2>&1")
```

Replace `<TOPIC>` with the exact topic the user mentioned.

**Example:** If the user says "do deep research on the future of quantum computing":
```
execute_command("python3 ./deep_research.py \"the future of quantum computing\" 2>&1 || python3 ~/.local/bin/deep_research.py \"the future of quantum computing\" 2>&1")
```

**Step 3:** Wait for the script to finish. It prints live progress through 6 phases and renders the final report automatically. This takes several minutes — do not interrupt.

**Step 4:** After execute_command returns, call task_complete with a brief summary:
```
"Deep research on '<TOPIC>' complete. Report and source files saved to ~/.cache/ai/research/<session>/"
```

## What the script does automatically

The orchestrator runs these phases — you do not manage them:
1. Creates a unique timestamped session folder under `~/.cache/ai/research/`
2. Calls an LLM sub-agent to generate a 6–8 question research plan (saved as `plan.json`)
3. Spawns parallel fetch sub-agents (3 per question) — each searches, fetches URLs, writes a source summary file
4. Runs a reviewer sub-agent that reads all source files and writes `review.md`
5. Runs a report sub-agent that synthesizes everything into `report.md` with inline citations
6. Renders the report to the terminal and prints the session folder path

## Do not

- Do NOT run web_search yourself before invoking the script — the script handles all searching
- Do NOT try to manually orchestrate the research steps
- Do NOT call task_complete until execute_command returns (the script may run for several minutes)
- Do NOT re-run if a phase is slow — the sub-agents have generous timeouts

## If the script fails

If `execute_command` returns an error:
1. Check that env vars are set: `INFER_BASE_URL`, `INFER_API_KEY`, `INFER_MODEL`
2. Verify the ai binary works: `execute_command("./ai --help 2>&1 | head -5")`
3. Check script exists: `execute_command("ls -la ./deep_research.py ~/.local/bin/deep_research.py 2>&1")`
