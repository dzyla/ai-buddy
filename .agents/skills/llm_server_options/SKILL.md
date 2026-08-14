---
name: llm_server_options
description: llama server options for local AI setup - repeat penalty, presence/frequency penalty, repeat-last-n, Qwen3.8 settings, MTP, Unsloth
---

# llm_server_options

## Purpose
Document and manage llama.cpp server sampling options, Unsloth dynamic quants, MTP speculative decoding, and model-specific configurations used by `ai-backend` and `ai`.

## Key Environment Variables & Server Options
- `LLAMA_REPEAT_PENALTY` (multiplier, default 1.0, neutral)
- `LLAMA_PRESENCE_PENALTY` (additive, default 0.0, neutral)
- `LLAMA_FREQUENCY_PENALTY` (additive, default 0.0, neutral)
- `LLAMA_REPEAT_LAST_N` (default 64)
- `LLAMA_MTP` (1/0, enable Multi-Token Prediction speculative decoding with `--spec-type draft-mtp`)
- `LLAMA_SPEC_DRAFT_N_MAX` (speculative tokens count, default 3 for MTP/dspark)
- `LLAMA_MTP_DRAFT_PATH` (optional companion MTP draft model path)

## Qwen3.8 Recommended Settings (Unsloth)

### Thinking Mode (Default / Complex Reasoning):
- `temperature`: `1.0` (`-t 1.0` / `INFER_TEMPERATURE=1.0`)
- `top_p`: `0.95` (`-p 0.95` / `INFER_TOP_P=0.95`)
- `top_k`: `20` (`-k 20` / `INFER_TOP_K=20`)
- `min_p`: `0.0` (`--min-p 0.0` / `INFER_MIN_P=0.0`)
- `presence_penalty`: `0.0`
- `repeat_penalty`: `1.0`
- `reasoning_effort`: `xhigh` (`--reasoning xhigh` / `INFER_REASONING_EFFORT=xhigh`)
- Context size: `262,144` (256K) for 27B, up to `1,010,000` for 2.4T

### Instruct / Non-thinking Mode:
- `temperature`: `0.7` (`-t 0.7`)
- `top_p`: `0.80` (`-p 0.80`)
- `top_k`: `20`
- `min_p`: `0.0`
- `presence_penalty`: `1.5`
- `repeat_penalty`: `1.0`
- `reasoning_effort`: `none`

Apply easily via:
```bash
ai-backend mode thinking     # sets Qwen3.8 thinking defaults
ai-backend mode instruct     # sets Qwen3.8 instruct defaults
ai-backend mtp on            # enables MTP speculative decoding
```

## Unsloth vs OG llama.cpp
- **Unsloth llama.cpp** (`https://github.com/unslothai/llama.cpp`, branch `iq1-narrow`): provides SOTA dynamic quant support (`IQ1_XXXS` / `Q1_0`, `TQ1_0`, `TQ2_0`), memory offload, and fast inference for Qwen3.8 & DeepSeek.
- Install via: `./install.sh llama unsloth` or switch back with `./install.sh llama og`.
- Update with: `./install.sh --update-llama` (pulls from active flavor's branch).

## Per-request Override
`INFER_PRESENCE_PENALTY`, `INFER_FREQ_PENALTY`, `INFER_TOP_P`, `INFER_TOP_K`, `INFER_MIN_P`, `INFER_REASONING_EFFORT`, and `INFER_PRESERVE_THINKING` allow overriding parameters per request.

