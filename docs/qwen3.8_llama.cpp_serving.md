# Running Qwen3.8-27B in llama.cpp (Unsloth) — best settings & use cases

Reference for serving **Qwen3.8-27B** locally on this box via the
`ai-backend` / `llama-server` wrapper and the `ai` CLI. Source of the model facts
and recommended sampling: the official Unsloth page
<https://unsloth.ai/docs/models/qwen3.8> (section *Run Qwen3.8 in llama.cpp*),
cross-checked against `install.sh` and `ai-backend` in this repo.

---

## What Qwen3.8-27B is

- **Hybrid thinking** model: it has distinct recommended settings for *thinking*
  mode vs. *instruct* (non-thinking) mode (see the table below).
- **Vision** + reasoning capabilities.
- **256K (262,144) token native context**; the Unsloth quants can extend to **1M**
  via YaRN (longer context costs more VRAM per token — size ctx from free VRAM,
  see "Context").
- Strong agentic-coding model (Terminal Bench 2.1 73.0, SWE-bench Pro 61.7,
  QwenSWEBench 79.0, IFBench 79.5, LiveCodeBench v6 90.3).
- Unsloth GGUFs use **Dynamic V3.0** quants, include **MTP** (Multi-Token
  Prediction) for fast decode, improved nested **tool calling**, and Developer
  Role support for agents like Codex.

The 27B variant runs on 17–19 GB of total memory (RAM+VRAM or unified) at 4-bit;
it is the one to use on this workstation. (The 2.4T-A95B is the MoE giant — 397 GB
at 1-bit — not a 27B-box target.)

---

## Hardware requirements (total memory: RAM + VRAM / unified)

| Quant | 2-bit | 3-bit | 4-bit | 6-bit | 8-bit | BF16 |
|-------|-------|-------|-------|-------|-------|------|
| Total memory needed | 11–13 GB | 13–16 GB | 17–19 GB | 24 GB | 31 GB | 56 GB |

Rule of thumb (Unsloth): **RAM+VRAM ≈ the quant size**. If the model fits you get
fast GPU decode; if it has to spill to disk/RAM it still runs, just much slower.

---

## Quant selection (real files in `unsloth/Qwen3.8-27B-GGUF`)

Unsloth's **recommended 4-bit is `UD-Q4_K_XL`** (Dynamic 4-bit). Verified available
files in the repo:

```
Qwen3.8-27B-UD-Q4_K_XL.gguf     # <-- Unsloth-recommended 4-bit (Dynamic)
Qwen3.8-27B-UD-Q5_K_XL.gguf     # Dynamic 5-bit
Qwen3.8-27B-UD-Q6_K_XL.gguf     # Dynamic 6-bit
Qwen3.8-27B-UD-Q8_K_XL.gguf     # Dynamic 8-bit
Qwen3.8-27B-UD-Q3_K_XL.gguf     # Dynamic 3-bit
Qwen3.8-27B-UD-Q2_K_XL.gguf     # Dynamic 2-bit
Qwen3.8-27B-Q4_K_M.gguf         # standard 4-bit
Qwen3.8-27B-Q5_K_M.gguf         # standard 5-bit
Qwen3.8-27B-Q6_K.gguf           # standard 6-bit  (~22.9 GB, currently on this box)
Qwen3.8-27B-Q8_0.gguf           # standard 8-bit
Qwen3.8-27B-IQ4_XS.gguf / IQ4_NL.gguf
Qwen3.8-27B-UD-IQ2_M.gguf / UD-IQ3_XXS.gguf
```

**Pick by what you want to trade:**
- Tightest fit / 24 GB card → `UD-Q4_K_XL` (or `Q4_K_M`).
- Best quality that fits 97 GB (this box's big card) → `UD-Q6_K_XL` or `UD-Q8_K_XL`;
  `Q6_K`/`Q8_0` are fine too. This box currently has **`Qwen3.8-27B-Q6_K.gguf`**
  (22.9 GB) on disk and serving it on GPU1.
- Max accuracy headroom → `Q8_0` / `UD-Q8_K_XL`.

---

## Install (the Unsloth llama.cpp flavor)

The Unsloth build is required for the dynamic quants + native MTP:

```bash
./install.sh llama unsloth     # clone unslothai/llama.cpp @ iq1-narrow, build + install
./install.sh --update-llama    # later: update & rebuild the active flavor
```

What it does (from `install.sh`):
- Clones `https://github.com/unslothai/llama.cpp` branch `iq1-narrow` (the branch
  that adds the `IQ1_XXXS`/`TQ*`/`Q1_0` dynamic types) — **not** upstream master.
- Detects CUDA (nvcc), builds `-DGGML_CUDA=ON`, targets
  `llama-server llama-cli llama-mtmd-cli llama-gguf-split`.
- Installs the binaries to `~/.local/bin` and symlinks `llama-server-wrapper.sh`
  → `ai-backend` (so the socket-activated service runs the wrapper, which does
  GPU selection, VRAM pre-flight, ctx sizing, penalties, then launches
  `llama-server`).
- Writes the systemd **user** units `llama-server.socket` / `llama-server.service`
  (on-demand, idle-unload) and defaults `CUDA_VISIBLE_DEVICES="auto"`.
- Picks/downloads the model; the `unsloth/Qwen3.8-27B-GGUF` repo is preset.

> Use `./install.sh llama og` only if you want stock upstream llama.cpp (standard
> quants, no native MTP). For Qwen3.8 dynamic quants, keep the `unsloth` flavor.

---

## Download & select the model

```bash
ai-backend use qwen3.8                 # alias -> unsloth/Qwen3.8-27B-GGUF (lists quants)
ai-backend use qwen3.8-27b             # same
ai-backend use qwen3.8-2.4t            # the 2.4T-A95B MoE (different beast)
ai-backend list                        # show downloaded GGUFs
```

Direct download of a specific quant (mirrors the Unsloth doc, `hf` CLI):

```bash
pip install -U "huggingface_hub[cli]"
hf download unsloth/Qwen3.8-27B-GGUF \
    --local-dir ~/.local/share/ai/models \
    --include "*UD-Q4_K_XL*"      # change the glob for another quant
```

`ai-backend use <model>` then sets `LLAMA_MODEL_PATH`, updates the systemd unit,
and (re)starts the server.

---

## The llama.cpp serve command (what `ai-backend serve` assembles)

`ai "..."` / the socket never launch `llama-server` directly — `ai-backend serve`
does, after GPU selection + a VRAM pre-flight. The effective command (with this
repo's defaults) is:

```bash
llama-server \
  --model   ~/.local/share/ai/models/Qwen3.8-27B-Q6_K.gguf \
  --host    127.0.0.1 --port 8080 \
  --ctx-size    262144 \
  --n-gpu-layers 99 \
  --flash-attn  on \
  --cache-type-k q8_0 --cache-type-v q8_0 \
  --batch-size  512 --ubatch-size 512 --threads 16 \
  --metrics \
  --sleep-idle-seconds 90 \
  --jinja --reasoning on \
  --repeat-penalty 1.0 --repeat-last-n 64 \
  --presence-penalty 0.0 --frequency-penalty 0.0 \
  --spec-type draft-mtp --spec-draft-n-max 2     # only when MTP is on
```

Notes on the flags:
- `--jinja --reasoning on` are mandatory for Qwen3.8 — without them chat
  completions degrade into raw chain-of-thought garbage. The wrapper always adds
  them.
- `--flash-attn on` + `q8_0` KV cache keep long contexts affordable.
- `--spec-type draft-mtp` + `--spec-draft-n-max N` = MTP speculative decoding
  (see below). Add `-md <draft.gguf>` only if a separate MTP draft file exists.
- `--sleep-idle-seconds` frees GPU VRAM after idle without killing the process.

If you ever want the **raw** llama-cli one-liner from the Unsloth doc (thinking
defaults), it is just the model + the sampling flags:

```bash
./llama-cli --model .../Qwen3.8-27B-UD-Q4_K_XL.gguf \
    --temp 1.0 --top-p 0.95 --top-k 20 --min-p 0.0
```

---

## Recommended settings (from the Unsloth doc)

Qwen3.8-27B is a **hybrid thinking** model — use the mode that matches the task.

| Parameter            | Thinking mode        | Instruct (non-thinking) |
|----------------------|----------------------|-------------------------|
| `temperature`        | `1.0`                | `0.7`                   |
| `top_p`              | `0.95`               | `0.80`                  |
| `top_k`              | `20`                 | `20`                    |
| `min_p`              | `0.0`                | `0.0`                   |
| `presence_penalty`   | `0.0`                | `1.5`                   |
| `repetition_penalty` | `1.0`                | `1.0`                   |
| `reasoning_effort`   | `xhigh` (default)    | `none`                  |

`reasoning_effort` (Qwen3.8-27B-specific, auto-enabled in Unsloth):
- `xhigh` — complex tasks needing thorough analysis (default for thinking).
- `medium` — balance accuracy vs speed.
- `low` — speed/cost optimized.
- `none` — no reasoning (instruct mode).

### Mode presets (one command, all the knobs)

Rather than setting each parameter by hand, the presets above are exposed as
named modes in **both** the `ai` app and `ai-backend`:

| Preset       | temp | top_p | top_k | min_p | pres_pen | reasoning_effort | Use |
|--------------|------|-------|-------|-------|----------|------------------|-----|
| `xhigh`      | 1.0  | 0.95  | 20    | 0.0   | 0.0      | xhigh            | Deep agentic coding, hard reasoning |
| `normal`     | 1.0  | 0.95  | 20    | 0.0   | 0.0      | medium           | Balanced thinking, routine-but-tricky |
| `low`        | 1.0  | 0.95  | 20    | 0.0   | 0.0      | low              | Fast thinking, cost-sensitive |
| `instruct`   | 0.7  | 0.80  | 20    | 0.0   | 1.5      | none             | Quick chat, summarization, no CoT |

(`thinking`/`think` are aliases of `xhigh`; `medium` is an alias of `normal`.)

**Per-invocation (no server restart, no env edit)** — `ai` CLI flag:

```bash
ai --mode xhigh "refactor the auth module"
ai --mode normal "summarize this file"
ai --mode instruct "what does this function do?"
```

**Live, inside an interactive `ai -i` session** — the `:mode` REPL command sets the
sampling for the *next turns* of the current session (no restart):

```
ai -i
> :mode xhigh        # deep reasoning for what follows
> :mode              # print current sampling
> :mode instruct     # drop to no-CoT chat
```

**Persistent (takes effect on next serve)** — `ai-backend mode <preset>` writes the
values to `~/.local/share/ai/env`:

```bash
ai-backend mode xhigh      # == thinking
ai-backend mode normal     # medium reasoning
ai-backend mode low
ai-backend mode instruct
ai-backend mode            # show current
```

The per-request knobs (`temperature`/`top_p`/`top_k`/`min_p`/`reasoning_effort`)
are sent in **every** request, so `--mode` / `:mode` work without touching the
server. Only context length / YaRN / MTP / GPU changes require a (re)serve.
Note: presets set temperature/top-p/top-k/min-p/presence/reasoning — they
deliberately leave `frequency_penalty` at the env default (this box's loop-breaker,
`INFER_FREQ_PENALTY`, default 0.10).

> Server-level vs request-level: the wrapper's `--repeat-penalty/--presence-penalty/
> --frequency-penalty` are **server-wide** (fixed at start, apply to every request).
> `ai` also sends per-request `frequency_penalty`/`presence_penalty`. For Qwen3.8
> keep the server-level ones **neutral** (`repeat=1.0, presence=0.0, freq=0.0`) and
> let the mode + per-request penalties do the loop-breaking.

---

## Use cases → settings to use

Pick a row, then apply the matching `ai-backend` commands. All persist to
`~/.local/share/ai/env` and apply on the next (re)serve.

### 1. Agentic coding / hard reasoning (default workhorse)
Deep multi-step coding, repo-level tasks, SWE-bench-style work, long tool loops.
Use **thinking** mode with maximum reasoning; 256K context for big repos.

```bash
ai-backend mode thinking      # temp 1.0, top_p 0.95, top_k 20, pres 0.0, repeat 1.0, effort xhigh
ai-backend ctx 262144         # full 256K (or `ai-backend ctx auto` to size from VRAM)
ai-backend mtp on             # fast decode (spec-type=draft-mtp, draft-n-max 2)
```

### 2. Complex analysis, want the best answer, latency OK
Same as (1) but be explicit that you want the deepest reasoning:

```bash
ai-backend mode thinking
ai-backend reasoning xhigh    # (already xhigh under mode thinking; keep)
```

### 3. Speed-first reasoning (thinking, cheaper)
Still reason, but trade some depth for speed/cost on routine-but-tricky prompts:

```bash
ai-backend mode normal       # thinking + medium reasoning effort
# or the fastest thinking:
ai-backend mode low          # thinking + low reasoning effort
# one-shot, no re-persist:
ai --mode normal "summarize this file"
ai --mode low "quick pass over this diff"
```

### 4. Quick chat / instruction-following / summarization (no thinking)
Fast, cheap, deterministic-ish. No CoT; the presence penalty stops chatter/repeat.

```bash
ai-backend mode instruct      # temp 0.7, top_p 0.80, top_k 20, pres 1.5, repeat 1.0, effort none
```

### 5. Long-document / long-context agentic (RAG-ish, big transcripts)
Max context; MTP optional (decode speed less important than window). Watch VRAM.

```bash
ai-backend mode thinking
ai-backend ctx 262144         # native max
ai-backend gpus auto          # serve resolves the biggest card that fits
# To go past 256K (e.g. 1M) you must enable YaRN first (see the YaRN section):
ai-backend yarn on            # --rope-scaling yarn --rope-scale 4.0 --yarn-orig-ctx 262144
ai-backend ctx 1048576        # ~1M; re-serve to apply
```

### 6. Highest accuracy, don't care about VRAM/speed (this box's 97 GB card)
Serve an 8-bit / Dynamic-8 quant on the big card, thinking mode:

```bash
ai-backend use unsloth/Qwen3.8-27B-GGUF     # pick Qwen3.8-27B-UD-Q8_K_XL.gguf (or Q8_0)
ai-backend gpus 1                            # pin to the 97 GB card
ai-backend mode thinking
ai-backend ctx 262144
```

### 7. Minimal footprint / 24 GB card
Smallest quant that's still useful, thinking mode, auto ctx:

```bash
ai-backend use unsloth/Qwen3.8-27B-GGUF     # pick Qwen3.8-27B-UD-Q4_K_XL.gguf
ai-backend gpus 0                            # a 24 GB card
ai-backend mode thinking
ai-backend ctx auto
```

### Switching sampling on the fly (per-run `ai` flags)
You can override without re-persisting, per invocation — either the named preset
or the individual knobs:

```bash
ai --mode xhigh "..."                      # one-shot preset (temp/top-p/top-k/min-p/presence/reasoning)
ai -p 0.95 -k 20 --min-p 0.0 --reasoning "..."   # individual thinking-ish sampling
ai --preserve-thinking "..."                      # keep prior CoT for continuation
```

`--preserve-thinking` leaves the previous turn's thinking trace in context: costs
more tokens, can raise accuracy on continued conversations.

---

## Context sizing

- Native max = **262,144**. `ai-backend ctx 262144` locks it.
- `ai-backend ctx auto` removes the lock and lets serve size ctx from **aggregate
  free VRAM** (weights + SSM state + q8_0 KV) — safe default.
- **1M context requires YaRN** (see the [YaRN] section). Qwen3.8's GGUF ships no
  baked `rope_scaling`, so `ai-backend yarn on` (scale 4 → ~1M) must be set *before*
  `ai-backend ctx 1048576`, then re-serve. Without the YaRN flags, 1M is raw RoPE
  extrapolation (degraded output) and the VRAM preflight refuses to launch it.

## MTP (fast decode)

- `ai-backend mtp on` → `LLAMA_MTP=1`, `LLAMA_SPEC_TYPE=draft-mtp`,
  `--spec-draft-n-max 2`. The Unsloth GGUFs ship MTP, so the internal MTP head is
  used (no separate `-md` needed) unless a sibling `*mtp*.gguf` is present.
- `ai-backend mtp 3` → 3 speculative tokens (more speed, a bit less throughput).
- `ai-backend mtp off` → disable.
- MTP forces **single-card** serving (the draft can't tensor-split), so serve
  resolves to the biggest card that fits.
- The **dspark** draft path (`ai-backend draft ...`) is **DeepSeek-V4-only** and
  is automatically ignored for Qwen3.8 (it would crash the graph build). Don't set
  `LLAMA_DRAFT_MODEL_PATH` to the dspark file while serving Qwen3.8 — it's harmless
  (ignored) but pointless.

## Multi-GPU

- `ai-backend gpus` shows detected cards + current selection.
- Default (`gpus auto`): single biggest card that holds the model; if no single
  card fits, spread across **all** cards with a proportional `--tensor-split`.
- `ai-backend gpus 1` pins a specific card; `gpus all` forces every card.

## YaRN (extend the context window beyond native)

Qwen3.8-27B's **native** training context is **262,144**. The GGUF ships **no**
baked `rope_scaling`/YaRN block (it uses plain RoPE, `rope.freq_base=1e7`), so any
context larger than native must be requested **at serve time** — llama.cpp's YaRN
RoPE scaling. Because Qwen3.8 is hybrid (full-attention only every 4th layer), the
KV cache at 1M is small, so a big card handles it easily.

```bash
ai-backend yarn            # show current state
ai-backend yarn on         # scale 4 -> 262144*4 ~= 1,048,576 tokens (~1M)
ai-backend yarn 2          # scale 2 -> ~512K
ai-backend yarn 8          # scale 8 -> ~2M
ai-backend yarn off        # back to native 256K only
```

`ai-backend yarn` persists `LLAMA_ROPE_SCALING=yarn`, `LLAMA_ROPE_SCALE`, and
`LLAMA_YARN_ORIG_CTX=262144` to the env file. On the next serve, `ai-backend serve`
translates those into:

```
--rope-scaling yarn --rope-scale 4.0 --yarn-orig-ctx 262144
```

The other YaRN knobs (`ext-factor`, `attn-factor`, `beta-slow`, `beta-fast`) are
**auto-derived** by llama.cpp from scale + orig-ctx, so you only need those three
flags. Pair with `ai-backend ctx <size>` to actually raise the context (e.g.
`ai-backend ctx 1048576`) — without YaRN flags, a 1M context produces degraded
output (raw RoPE extrapolation), and `vram_preflight` will refuse to launch a
config that can't fit.

> **Pitfall — the systemd unit wins over the env file.** This box's
> `~/.config/systemd/user/llama-server.service` bakes `Environment=LLAMA_CTX_SIZE=131072`,
> which overrides the env file's value. So "1M in the env file" can still come up
> running at 128K. Raise it with `ai-backend ctx <size>` (which syncs the unit) and
> enable YaRN before going past 256K.

---

## This box (current state, 2026-08-14)

- GPUs: GPU0/2/3 = RTX PRO 4000 Blackwell (24 GB each), **GPU1 = RTX PRO 6000
  Blackwell Max-Q (97 GB)**.
- Model on disk: `~/.local/share/ai/models/Qwen3.8-27B-Q6_K.gguf` (22.9 GB),
  active via `LLAMA_MODEL_PATH`.
- Active env: `CUDA_VISIBLE_DEVICES="auto"`, `LLAMA_N_GPU_LAYERS=99`,
  `INFER_TEMPERATURE=1.0 / INFER_TOP_P=0.95 / INFER_TOP_K=20 / INFER_MIN_P=0.0 /
  INFER_REASONING_EFFORT=xhigh` (i.e. **thinking mode** = `ai --mode xhigh`), server
  penalties neutral, `LLAMA_CTX_SIZE=1048576` in the env file but the **systemd
  unit bakes `LLAMA_CTX_SIZE=131072`**, so the live server runs at 128K until you
  `ai-backend ctx <size>`. YaRN is **off** by default — enable it (`ai-backend yarn on`)
  before actually serving past 256K.
- Serve lands on the 97 GB card (auto = biggest). Q6_K + 256K fits comfortably on
  it; MTP is the main speed lever.

---

## Pitfalls

- **Hybrid thinking = two configs.** Forgetting to flip `mode thinking` ↔
  `mode instruct` is the #1 source of "model is chatty / repeats / won't answer".
  Instruct mode needs `presence_penalty=1.5` + `reasoning_effort=none`; thinking
  mode needs the neutral penalties + `xhigh`. (`ai --mode` / `:mode` /
  `ai-backend mode` all set the full preset in one step — see Mode presets.)
- **1M ctx ≠ native.** Only 256K is guaranteed. Past 256K you must enable YaRN
  (`ai-backend yarn on`) *before* raising the context — otherwise llama.cpp does
  raw RoPE extrapolation and the output degrades. The GGUF ships no baked
  `rope_scaling`, so YaRN is a serve-time flag, not a model property.
- **dspark draft is DeepSeek-only.** It's auto-ignored for Qwen3.8 but a stale
  `LLAMA_DRAFT_MODEL_PATH` is confusing — `ai-backend draft off` clears it.
- **Reasoning models return empty `content`** with a small `max_tokens` (the
  budget is eaten by `reasoning_content`). Give `max_tokens` headroom.
- **Stale installed binary.** After editing `ai-backend`, re-run `./install.sh`
  (or `cp ai-backend ~/.local/bin/ai-backend`) or the socket-activated service
  runs old code.
- **Quant fit.** 6-bit/8-bit need ~24/31 GB; on a 24 GB card use 4-bit
  (`UD-Q4_K_XL`) or `gpus all`.
- **VRAM pre-flight** (2026-08): serve now estimates the footprint and exits with
  fix hints (`mtp off` / `ctx auto` / `gpus all`) *before* launching if it can't
  fit — read the hint instead of guessing.
- **The systemd unit wins over the env file** for `LLAMA_CTX_SIZE` (see the YaRN
  pitfall box). Use `ai-backend ctx <size>`, which syncs the unit.

---

*Quick reference for the modes:*

```bash
# Deep reasoning (agentic / hard thinking)
ai-backend mode xhigh    && ai-backend ctx 262144 && ai-backend mtp on

# Balanced / fast thinking
ai-backend mode normal   # medium effort
ai-backend mode low      # low effort
ai --mode normal "..."   # one-shot, no re-persist

# Instruct (fast chat / no CoT)
ai-backend mode instruct && ai-backend mtp off

# 1M context (YaRN required)
ai-backend yarn on && ai-backend ctx 1048576   # then re-serve
```
