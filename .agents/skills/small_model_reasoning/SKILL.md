---
name: small-model-reasoning
description: Strict guidelines for smaller models (like Gemma 4) to improve execution accuracy by forcing step-by-step thinking, plan-validation loops, and strict verification of tool arguments before execution.
---

# Small Model Reasoning Guidelines

Smaller models (e.g., 7B-9B parameter models like Gemma 4) can occasionally jump to conclusions, skip steps, or hallucinate tool outputs. Follow these strict rules to maximize reliability:

## 1. Explicit Chain-of-Thought (CoT)
- Always break down your logic before choosing a tool. Write 1-2 sentences of reasoning explaining *why* you are calling a specific tool and what you expect to learn or achieve.
- Do not make multiple assumptions in a single step; gather facts sequentially.

## 2. Plan-Before-Action Loop
- When given a non-trivial instruction, write out a 3-step checklist of what you need to inspect, modify, and verify.
- Check off each step as you complete it.

## 3. Tool Argument Validation
- Before outputting a tool call, double check that the parameters match the tool schema exactly.
- Verify that paths are fully resolved and arguments (like regex patterns or search blocks) do not contain typos or mismatched strings.
- If a tool returns an error, do not retry the exact same arguments; change your query or method.

## 4. Self-Correction Check
- After receiving a tool's output, ask: "Does this output confirm my expectation, or does it contradict it?"
- If the output is empty or unexpected, adapt your plan immediately instead of repeating the failed steps.
