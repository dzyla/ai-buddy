---
name: karpathy-guidelines
description: Behavioral guidelines to reduce common LLM coding mistakes. Use when writing, reviewing, or refactoring code to avoid overcomplication, make surgical changes, surface assumptions, and define verifiable success criteria.
---

# Karpathy Guidelines

Behavioral guidelines to reduce common LLM coding mistakes, derived from Andrej Karpathy's observations on LLM coding pitfalls.

## 1. Think Before Coding
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them.
- Push back on overcomplication.
- Stop and name confusion immediately.

## 2. Simplicity First
- Minimum code to solve the problem. Nothing speculative.
- No abstractions for single-use code.
- No unneeded configurability.
- No error handling for impossible scenarios.

## 3. Surgical Changes
- Touch only what you must. Match existing style.
- Clean up unused code/imports created by YOUR changes.
- Do not refactor unrelated code.

## 4. Goal-Driven Execution
- Define clear success criteria (e.g. reproducing test case).
- Map multi-step plans with explicit verification steps.
- Verify completion.
