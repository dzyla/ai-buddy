---
name: llm_server_options
description: llama server options for local AI setup - repeat penalty, presence/frequency penalty, repeat-last-n
---

# llm_server_options

# llm_server_options

## Purpose
Document and manage llama.cpp server sampling options used by ai-backend to break thinking loops.

## Key Environment Variables
- `LLAMA_REPEAT_PENALTY` (multiplier, default 1.0, neutral)
- `LLAMA_PRESENCE_PENALTY` (additive, default 0.0, neutral)
- `LLAMA_FREQUENCY_PENALTY` (additive, default 0.0, neutral)
- `LLAMA_REPEAT_LAST_N` (default 64)

## Where Introduced
In `ai-backend` serve command (lines ~373-381):
```bash
--repeat-penalty "$LLAMA_REPEAT_PENALTY"
--repeat-last-n "$LLAMA_REPEAT_LAST_N"
--presence-penalty "$LLAMA_PRESENCE_PENALTY"
--frequency-penalty "$LLAMA_FREQUENCY_PENALTY"
```

## XDA Article Recommendations
- repeat-penalty: ~1.05-1.1
- presence-penalty: ~0.6

## Defaults
Current defaults are NEUTRAL (repeat=1.0, presence=0.0, frequency=0.0) so per-request
INFER_PRESENCE_PENALTY / INFER_FREQ_PENALTY remain the active mechanism for breaking
thinking loops. Set server-level values in ~/.local/share/ai/env to make them permanent.

## Per-request Override
INFER_PRESENCE_PENALTY and INFER_FREQ_PENALTY override server-level values per request
(see ai.c tool handling in handle_chat_completion / patch_env_key).

## Files
- docs/llm_server_options.md
- AGENTS.md (server options section)
- ai-backend (serve implementation)
