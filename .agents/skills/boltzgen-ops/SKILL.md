---
name: boltzgen-ops
description: Use when the user asks to design, launch, resume, check the progress of, or interpret results from a BoltzGen binder-design campaign in this repo — e.g. "start a new boltzgen run", "how's the campaign going", "resume the design_folding step", "is this run stuck", "are these designs good", "kick off design_folding on zyla-lab", or anything mentioning workbench/, design specs, GPU workers, or final_ranked_designs.
---

# BoltzGen Campaign Operations

Operational playbook for running BoltzGen binder-design campaigns on this
specific setup: a local workstation (`dzyla`) and a 4-GPU remote box
(`zyla-lab`, reachable via `ssh dzyla@zyla-lab` and also NFS-mounted at
`/mnt/HDD1`). Follow this literally — commands are copy-pasteable. If
anything here conflicts with what you observe on disk, trust disk state and
update this file.

Read `RUNBOOK_MEV_BINDER.md` in the repo root for a worked historical example
(the `mev_binder` campaign) with full narrative detail. This skill is the
condensed, task-oriented version.

## 0. Orient yourself first

Before doing anything, answer these three questions:

1. **Which machine?** Local (`/home/dzyla/Code/boltzgen`) or zyla-lab
   (`/mnt/HDD1/Software/boltzgen`, same repo layout, either via
   `ssh dzyla@zyla-lab` or directly through the NFS mount from local). zyla-lab has
   4 heterogeneous GPUs (1x RTX PRO 6000 Blackwell Max-Q 97GB + 3x RTX PRO
   4000 Blackwell 24GB); local has whatever `nvidia-smi` shows.
2. **Which campaign / workbench dir?** Each campaign lives in its own
   directory under `workbench/<name>/` (e.g. `workbench/mev_binder/`,
   `workbench/mev3helix/`). `ls workbench/` to see what exists.
3. **Which step is it at?** Every campaign runs the same 6 steps in order;
   check which output directories already exist (see Section 3).

Always activate the conda env and cd to the repo root first — **all config
paths are relative to the repo root**, so running from the wrong directory
silently breaks things:

```bash
source ~/miniconda3/etc/profile.d/conda.sh && conda activate bg
cd /home/dzyla/Code/boltzgen          # local
# or
ssh dzyla@zyla-lab                          # then: cd /mnt/HDD1/Software/boltzgen
```

## 1. The pipeline (memorize this)

```
design → inverse_folding → folding → design_folding → analysis → filtering
 [GPU]        [GPU]          [GPU]        [GPU]          [CPU]      [CPU]
```

| Step | What it does | Output you can count |
|---|---|---|
| `design` | Diffusion generates backbone + sequence | `intermediate_designs/*.cif` |
| `inverse_folding` | Redesigns sequence on the generated backbone | `intermediate_designs_inverse_folded/*.cif` |
| `folding` | Refolds with target to validate structure | `intermediate_designs_inverse_folded/fold_out_npz/*.npz` |
| `design_folding` | Refolds with design-conditioned model (produces ipTM/PAE scores) | `intermediate_designs_inverse_folded/fold_out_design_npz/*.npz` |
| `analysis` | CPU aggregation of all metrics (~32 workers) | `aggregate_metrics_analyze.csv`, `metrics_tmp/` |
| `filtering` | Ranks + selects final set (~20s, fast) | `final_ranked_designs/` |

`design`, `inverse_folding`, `folding`, `design_folding` are GPU-bound and by
far the slowest. `analysis` and `filtering` are CPU-only and run once, at the
end, after all GPU steps for every design are done.

A campaign directory (`workbench/<name>/`) always has:
```
workbench/<name>/
├── steps.yaml              # manifest listing the 6 steps + their config paths
├── config/
│   ├── design.yaml
│   ├── inverse_folding.yaml
│   ├── folding.yaml
│   ├── design_folding.yaml
│   ├── analysis.yaml
│   └── filtering.yaml
├── intermediate_designs/                       # after design
├── intermediate_designs_inverse_folded/         # after inverse_folding
│   ├── fold_out_npz/                            # after folding
│   └── fold_out_design_npz/                     # after design_folding
└── final_ranked_designs/                        # after filtering (the deliverable)
```

## 2. Designing a new campaign (writing a design spec)

A campaign starts from a **design spec YAML** (convention in this repo:
`experiments/<campaign_name>/<name>.yaml`, with any input structures — e.g.
`.cif` target files — alongside it in the same directory).

### Minimal spec anatomy

```yaml
entities:
  - protein:
      id: C                    # chain ID for the binder you're designing
      sequence: 60..120        # random length between 60-120 designed residues
  - file:
      path: my_target.cif      # target structure, resolved relative to the spec's own directory
      include: "all"           # or e.g. only chain A: `include: {chain: {id: A}}`
```

Real example used for the `mev_binder` campaign
(`experiments/measles_binder/mev_binder.yaml`):
```yaml
entities:
  - protein:
      id: C
      sequence: 60..120
  - file:
      path: mev_F_3helix.cif
      include: "all"
```

### Key entity types

- `protein:` — a chain to design or fix. `sequence:` can be a fixed AA
  string, a range (`60..120`), or a mix (`3..5C6C3` = random 3-5 residues,
  then fixed C, then 6 random residues, then fixed C, then 3 random). Add
  `residue_constraints:` to allow/disallow specific residues at specific
  positions.
- `file:` — pull a chain (or the whole structure) from an existing `.cif`
  file as a **fixed target**. `include:` selects chains/residues;
  `include_proximity:` pulls in nearby residues within a radius;
  `binding_types:` marks residues as binding/not_binding hotspots;
  `design:` marks positions in an otherwise-fixed structure as designable
  (partial redesign); `fuse:` glues a `file:` fragment onto a preceding
  `protein:` chain.
- `ligand:` — small molecule by CCD code or SMILES.

For a full syntax reference with every field, read
`example/design_spec_showcasing_all_functionalities.yaml` in the repo root —
it is a live, commented example covering every entity option. Also browse
`example/hard_targets/*.yaml` and `example/streptavidin_partially_flexible_target/*.yaml`
for realistic patterns (nanobody scaffolds, cyclic peptides, partial
redesign, disulfide-constrained binders, etc.).

### Validate before running (cheap, do this first)

```bash
boltzgen check experiments/<campaign>/<spec>.yaml
# optionally also render the parsed structure to inspect it:
boltzgen check experiments/<campaign>/<spec>.yaml --output /tmp/check_out
```
This catches malformed specs (bad ranges, missing files, invalid chain refs)
in seconds, before burning GPU time.

### Choosing a protocol

`--protocol` sets sane defaults for checkpoints/budget/alpha/inverse-fold
Cys-avoidance based on the binder modality:

| Protocol | Use for |
|---|---|
| `protein-anything` (default) | Structured protein/miniprotein binders |
| `peptide-anything` | Linear/cyclic peptide binders (used by `mev_binder`) |
| `nanobody-anything` | Nanobody scaffold binders (used by `mev3helix`) |
| `antibody-anything` | Full antibody binders |
| `protein-small_molecule` | Small-molecule binder design |
| `protein-redesign` | Partial redesign of an existing structure |

### Launch it

```bash
boltzgen run experiments/<campaign>/<spec>.yaml \
    --output workbench/<campaign> \
    --protocol peptide-anything \
    --num_designs 10000 \
    --budget 20
```
This both configures (writes `workbench/<campaign>/config/*.yaml` +
`steps.yaml`) and executes all 6 steps in sequence, single command. See
Section 4 for why you usually do NOT want to run this directly on zyla-lab's
multi-GPU box without the dynamic-queue wrapper.

To only write configs without running anything (useful to inspect/edit
configs before committing GPU time, or as prep for the dynamic-queue tooling
in Section 4):
```bash
boltzgen configure experiments/<campaign>/<spec>.yaml \
    --output workbench/<campaign> --protocol peptide-anything \
    --num_designs 10000 --budget 20
```

Common `--config` overrides (repeatable, format `<step> <key>=<value>`):
```bash
--config design multiplicity=1000
--config folding trainer.devices=4
--config filtering budget=20 top_budget=10
```

## 3. Checking progress / health

### Is it running at all?

```bash
# local
ps aux | grep -E 'main.py|gpu_worker.sh' | grep -v grep
nvidia-smi
# zyla-lab (from local, via ssh)
ssh dzyla@zyla-lab 'nvidia-smi; pgrep -af "workbench/.*/gpu_worker.sh"'
```
`nvidia-smi` GPU utilization should be near 90-100% on whichever GPU(s) are
in use if a GPU step is actively running. `0%` util with a live process
usually means it's stuck loading the checkpoint/dataloader, not dead — wait
~30-60s before concluding it's hung.

### How far along is each step? (count files, don't guess)

```bash
WB=workbench/mev_binder   # substitute the real campaign dir
echo "design:            $(ls $WB/intermediate_designs/*.cif 2>/dev/null | wc -l)"
echo "inverse_folding:    $(ls $WB/intermediate_designs_inverse_folded/*.cif 2>/dev/null | wc -l)"
echo "folding:            $(ls $WB/intermediate_designs_inverse_folded/fold_out_npz/*.npz 2>/dev/null | wc -l)"
echo "design_folding:     $(ls $WB/intermediate_designs_inverse_folded/fold_out_design_npz/*.npz 2>/dev/null | wc -l)"
echo "analysis output:    $(test -f $WB/aggregate_metrics_analyze.csv && echo done || echo not-started)"
echo "filtering output:   $(test -d $WB/final_ranked_designs && echo done || echo not-started)"
```
Compare each count against the total design count (`design` step's file
count, or `num_designs` in `config/design.yaml`) to get a completion
percentage. **This file-count check is the source of truth** — more
reliable than reading a log, which may be stale or from a crashed process.

### Reading logs

If launched via `nohup .../resume_run.log` or the dynamic-queue tooling,
`tail -f` the relevant log. Dynamic-queue workers log per-GPU:
```bash
tail -f /tmp/mev_binder_dynamic_queue/worker_gpu0.log   # or worker_gpu{1,2,3}.log
tail -f workbench/mev_binder/auto_analysis_filtering.log
tail -f workbench/mev_binder/run_full_pipeline.log
```

### Common gotchas (check these before assuming something is broken)

- **`output` is positional, not `--output`, for `execute`.**
  `boltzgen execute workbench/mev_binder --steps design_folding` — NOT
  `boltzgen execute --output workbench/mev_binder ...` (that errors with
  "unrecognized arguments" for `execute`, though `run`/`configure` DO use
  `--output`). Check `boltzgen execute --help` if unsure.
- **Resuming reprocesses everything unless `skip_existing: true`.** The
  configs default to `skip_existing: false`. Before resuming a step, set
  this in `workbench/<campaign>/config/<step>.yaml`:
  ```bash
  sed -i 's/skip_existing: false/skip_existing: true/' workbench/<campaign>/config/<step>.yaml
  ```
  Verify `skip_existing_kind` in the same file matches the step (e.g.
  `design_folded` for `design_folding`) — it's what tells the skip-check
  which output directory to look in.
- **On zyla-lab, `CUDA_VISIBLE_DEVICES` index ≠ `nvidia-smi` index.**
  `CUDA_DEVICE_ORDER` is unset there, so CUDA orders GPUs by its own
  heuristic, not PCI bus order like `nvidia-smi`. Verified mapping: CUDA idx
  0 = RTX PRO 6000 Max-Q, CUDA idx 1/2/3 = RTX PRO 4000s (nvidia-smi indices
  1, and 0/2/3 respectively). **Never trust a per-GPU benchmark or
  assignment without verifying first:**
  ```bash
  CUDA_VISIBLE_DEVICES=N python -c "import torch; print(torch.cuda.get_device_properties(0))"
  ```
- **Default multi-GPU mode (`trainer.devices: N`) is an equal static split**
  (PyTorch Lightning DDP), which leaves faster GPUs idle once they finish
  their shard. On zyla-lab's heterogeneous 4-GPU setup this wastes ~2/3 of
  the potential throughput (measured: naive DDP 0.226 designs/sec vs a
  theoretical ceiling of 0.70 designs/sec). Use the dynamic-queue tooling in
  Section 4 for `inverse_folding`/`folding`/`design_folding` on zyla-lab
  instead of plain `boltzgen execute` with multi-GPU configs.

## 4. Running efficiently on zyla-lab's 4 heterogeneous GPUs

**Only relevant on zyla-lab** (1x RTX PRO 6000 97GB + 3x RTX PRO 4000 24GB).
On a single-GPU or homogeneous-GPU machine, just use `boltzgen run`/`execute`
directly (Section 2/3) — none of this is needed.

All tooling lives in `workbench/mev_binder/` (built for that campaign but
generic — the scripts take the workbench dir / step / config as arguments,
so they work for any campaign) and is mirrored on both local and zyla-lab at
the same relative path:

- `claim_batch.py` — atomically pops N design IDs off a shared queue file
  (`fcntl.flock`-guarded, safe for concurrent workers).
- `gpu_worker.sh <cuda_idx> <queue_file> <base_config> <batch_size> <work_dir>`
  — persistent per-GPU loop: claim batch → run the step on just that batch
  (via the `data.subset_target_ids` config hook) → repeat until queue empty.
- `dynamic_gpu_step.sh <step_label> <base_config> <input_glob> <output_glob> [batch_4000=30]`
  — generic driver: computes the remaining-work queue as
  `comm -23 <all input IDs> <all output IDs>`, launches 4 `gpu_worker.sh`
  instances with calibrated batch sizes, waits for drain. Works for
  `inverse_folding`, `folding`, `design_folding` (any step using
  `FromGeneratedDataModule`/`subset_target_ids`). **Does NOT work for
  `design`** (see below).
- `run_full_pipeline.sh [workbench_dir=workbench/mev_binder] [batch_4000=30]`
  — one-shot: runs `design` natively (equal DDP split), then
  `inverse_folding` → `folding` → `design_folding` via `dynamic_gpu_step.sh`,
  then `analysis` → `filtering`. Safe to interrupt and rerun — every step
  only touches unfinished work.
- `auto_analysis_filtering.sh` — polls every 20s until all `gpu_worker.sh`
  processes exit and the queue file is confirmed empty, then runs
  `analysis` then `filtering` automatically. Use this after manually
  launching a dynamic-queue step so you don't have to babysit it.
- `resume_dynamic_multi_gpu.sh [batch_4000=30]` — the original,
  `design_folding`-specific version of `dynamic_gpu_step.sh` (predates the
  generic version; still works, kept for the exact command already
  documented in `RUNBOOK_MEV_BINDER.md`).

### Batch-size calibration (already tuned, don't re-derive unless hardware changes)

RTX PRO 6000 batch size = `round(batch_4000 * 1.72)` — this ratio was
measured from **concurrent** per-GPU throughput (0.227 designs/sec on the
6000 vs 0.132 designs/sec per 4000, all four running simultaneously; NOT the
isolated single-GPU rates, which differ under contention). Default
`batch_4000=30` → 6000 gets 52. This is baked into all the scripts above; you
normally don't need to touch it.

### Recommended commands

**Resume/run a single already-configured GPU step, dynamically balanced:**
```bash
cd /mnt/HDD1/Software/boltzgen
./workbench/mev_binder/dynamic_gpu_step.sh design_folding \
  workbench/mev_binder/config/design_folding.yaml \
  "workbench/mev_binder/intermediate_designs_inverse_folded/*.cif" \
  "workbench/mev_binder/intermediate_designs_inverse_folded/fold_out_design_npz/*.npz"
```

**Auto-chain analysis+filtering after that finishes, detached, don't babysit:**
```bash
setsid nohup ./workbench/mev_binder/auto_analysis_filtering.sh \
  < /dev/null > /dev/null 2>&1 &
```
(Plain `nohup ... & disown` is not reliable here — it did not survive SSH
session exit in testing. `setsid` fully detaches into a new session.)

**Run an entire fresh campaign hands-off from `design` through `filtering`:**
```bash
cd /mnt/HDD1/Software/boltzgen
setsid nohup ./workbench/mev_binder/run_full_pipeline.sh \
  workbench/<campaign> 30 < /dev/null > /dev/null 2>&1 &
```
Then just poll file counts (Section 3) or tail
`workbench/<campaign>/run_full_pipeline.log` — no manual intervention needed
between steps.

### Why `design` is excluded from dynamic balancing

`design` generates brand-new IDs rather than processing an existing list, so
there's no ID set to work-steal from (unlike the other 3 steps, which
process a fixed pool of already-generated design IDs). Building an
index-range-claiming scheme for it was considered but not implemented — risk
of silent output-filename collisions between concurrent workers wasn't
validated. `design` also hasn't been observed to have the idle-tail problem
in practice. Just run it with the native `trainer.devices: N` equal-split
DDP (default behavior of `boltzgen run`/`execute`).

## 5. Interpreting results

Final output is `workbench/<campaign>/final_ranked_designs/`:
```
final_ranked_designs/
├── all_designs_metrics.csv           # every design, full metrics (hundreds of columns)
├── final_designs_metrics_20.csv      # metrics for just the top-N selected
├── final_20_designs/                 # top-N structures (.cif), ranked rank0001..rank00N
├── intermediate_ranked_10_designs/   # diversity-optimized alternate top-10
└── results_overview.pdf              # summary report, open this first
```

### Key metrics (in `all_designs_metrics.csv`)

- `design_iptm` — predicted interface confidence (binder-target). Higher is
  better.
- `design_ptm` — predicted overall structural confidence. Higher is better.
- `min_design_to_target_pae` — predicted alignment error at the interface
  (Å). Lower is better.
- `filter_rmsd` / `designfolding-filter_rmsd` — backbone RMSD between the
  original design and the design-conditioned refold. **< 2.5 Å is the
  standard "designability" pass/fail threshold** used by BoltzGen's own
  filtering step.
- `plip_hbonds_refolded`, `plip_saltbridge_refolded`, `delta_sasa_refolded`
  — physical interface contact metrics used in the paper's ranking
  algorithm.
- `pass_filters`, `num_filters_passed` — whether a design cleared all hard
  filters configured in `config/filtering.yaml`.
- `quality_score`, `final_rank` — the composite ranking used to pick the
  final top-N.

### Judging "is this a good binder" — IMPORTANT CAVEAT

The BoltzGen paper's Appendix C.2 ("Calibrating Filtering Algorithm for
**Protein-Protein Complexes**") calibrates absolute thresholds like
`design_iptm > 0.5/0.6/0.7/0.8` and `design_ptm > 0.75/0.8` against 11,000
validated **miniprotein** binders (Cao et al. 2022) — NOT peptides. The
paper's own peptide wetlab validation (Rag GTPase linear/cyclic peptides,
Section 2.6 / Appendix E.4 / Tables 13-16) reports Kd and binding calls for
confirmed peptide hits but never their `design_iptm`/`design_ptm` values —
there is no peptide-specific numeric ground truth in the paper to calibrate
against. **Do not apply the protein-calibrated absolute thresholds as a hard
pass/fail bar for peptide-modality campaigns** (`peptide-anything`
protocol, e.g. `mev_binder`). What IS still valid for any modality:
- The metrics themselves are computed identically regardless of modality
  (same Boltz-2 confidence model).
- **Relative** ranking within one campaign's own design pool is meaningful.
- `filter_rmsd < 2.5 Å` (designability) is a structural check, not a
  protein-calibrated confidence threshold, so it transfers better.
When asked to assess campaign quality, report the relative distribution
(median/range of `design_iptm`, how many pass `filter_rmsd<2.5`, whether
scores cluster tightly or show a separated top tier) rather than a flat
"X% pass the protein threshold" verdict for peptide campaigns.

### Merging multiple output directories

If a campaign was split across machines (e.g. partial runs on both local and
zyla-lab), combine before a final filtering pass:
```bash
boltzgen merge workbench/<campaign>_local workbench/<campaign>_zylalab \
  --output workbench/<campaign>_merged
boltzgen execute workbench/<campaign>_merged --steps filtering
```

## 6. Troubleshooting checklist

| Symptom | Likely cause | Fix |
|---|---|---|
| `execute` reprocesses everything from scratch | `skip_existing: false` in that step's config | `sed -i 's/skip_existing: false/skip_existing: true/' <config>` |
| `unrecognized arguments: --output` | Used `--output` with `execute` | `execute` takes `output` as a **positional** arg |
| "Step not found" | Wrong `--steps` name | Must exactly match `steps.yaml` names: `design`, `inverse_folding`, `folding`, `design_folding`, `analysis`, `filtering` (underscores, not hyphens) |
| GPU benchmark results look backwards / one "slow" GPU seems fastest | `CUDA_VISIBLE_DEVICES` index ≠ `nvidia-smi` index on zyla-lab | Verify identity with `torch.cuda.get_device_properties(0)` before trusting any per-index result |
| Multi-GPU run has poor combined throughput despite all GPUs near 100% util briefly | Static equal-split DDP idle-tail (fast GPU finishes first, sits idle) | Use `dynamic_gpu_step.sh` / `run_full_pipeline.sh` instead of plain multi-GPU `execute` |
| Background job dies when SSH session closes | Plain `nohup cmd & disown` isn't always sufficient | Use `setsid nohup cmd < /dev/null > /dev/null 2>&1 &` |
| Config paths not found / relative path errors | Not running from repo root | Always `cd` to the boltzgen project root first — all config paths are relative to it |
| Out of GPU memory | Batch/diffusion sample size too large for that GPU | Lower `diffusion_samples` (e.g. folding 5→3) or `trainer.devices`/batch size in the step's config, especially on the 24GB RTX 4000s |
| `pgrep` for `gpu_worker.sh` shows one extra process | Self-match artifact: the ssh/bash wrapper's own command line contains the grep pattern | Use `pgrep -af` to see full command lines and confirm; harmless |

## 7. Quick reference: all commands

```bash
# validate a design spec
boltzgen check experiments/<c>/<spec>.yaml

# configure only (no execution)
boltzgen configure experiments/<c>/<spec>.yaml --output workbench/<c> --protocol <protocol>

# configure + run everything
boltzgen run experiments/<c>/<spec>.yaml --output workbench/<c> --protocol <protocol> --num_designs N --budget B

# execute specific steps on an already-configured campaign (note: positional output)
boltzgen execute workbench/<c> --steps design_folding analysis filtering

# merge split outputs, then re-filter
boltzgen merge workbench/<c>_a workbench/<c>_b --output workbench/<c>_merged
boltzgen execute workbench/<c>_merged --steps filtering

# zyla-lab dynamic GPU balancing (any of inverse_folding/folding/design_folding)
./workbench/mev_binder/dynamic_gpu_step.sh <step> <config.yaml> "<input_glob>" "<output_glob>" [batch_4000]

# zyla-lab full hands-off pipeline
./workbench/mev_binder/run_full_pipeline.sh workbench/<c> [batch_4000]

# progress check (file counts, source of truth)
for d in intermediate_designs intermediate_designs_inverse_folded; do echo "$d: $(ls workbench/<c>/$d/*.cif 2>/dev/null | wc -l)"; done
```
