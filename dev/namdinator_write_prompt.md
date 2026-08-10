# Continuation: WRITE the documentation artifacts (investigation is already done)

You previously investigated the `openmmator` package at /home/dzyla/Code/Namdinator
and learned the critical facts below. That investigation is DONE — do NOT redo it.
Your only job now is to WRITE the artifacts. Do not launch any MDFF simulation.

## Verified facts (trust these — they come from the completed investigation + a real pytest run)

- The package command and module are **`openmmator`** — NOT `namdinator`. It was renamed.
  Env: `source /home/dzyla/miniconda3/etc/profile.d/conda.sh && conda activate namdinator`
  (or `export PATH=/home/dzyla/miniconda3/envs/namdinator/bin:$PATH`).
- The real subcommands: `run` (fit->validate->refine->report, needs Phenix),
  `fit` (MDFF/OpenMM only, no Phenix), `refine` (Phenix), `validate` (Phenix), `report` (no Phenix).
- PyProject name: `openmmator`, version 3.0.0, python>=3.12, entry point `openmmator = openmmator.cli:app`.
- Test tier verified passing just now: `126 passed, 38 deselected` for
  `python -m pytest -q -m "not slow and not gpu and not phenix"` (run via the namdinator env python).
- Worked example: `openmmator run --pdb 3jd8.pdb --map emd_6640.map --resolution 3.5 --workdir out --gscale 5.0 --refine`.
- `--gscale` semantics: energy uses `4.184 * gscale * weight * pot(...)` (kcal/mol units converted to kJ for
  OpenMM). Higher gscale = stronger density attraction = better map fit but worse geometry. NOT comparable to
  the old NAMD Namdinator's 0.3-0.6 — recalibrate per system. Default 5.0 on the shipped pair.
- `--steps` (how far the model moves): close model (~cc>0.55) -> 20000; moderate -> 50000; large moves -> 100000.
  `--minimization-steps`: clean input -> ~200; clashy input (clashscore>20) -> 2000-5000 to avoid NaNs.
- Standard workflow: fit -> refine. Always pass `--refine` by default. `--seed N` narrows variance.
- Outputs in workdir: `results.json` (source of truth, atomic per stage), `summary.txt` (tables),
  `last_frame.pdb` (fitted), `last_frame_rsr.pdb` (refined), `ccc.png`, `validate/{input,fitted,refined}/`,
  `trajectory.dcd` + `trajectory_topology.pdb`.
- Success metric: refined `cc_mainchain` clearly above input with geometry comparable/better. Judge on
  `summary.txt`/`results.json`, NOT the live ccc.png (it under-reports).
- mmCIF model input works directly (auto-converted). Glycans/ligands dropped by default (`--keep-ligands` only if force field has params). Hydrogens added by PDBFixer automatically. Large maps auto-downsampled/cropped.
- Phenix discovery: `--phenix-root` > `$PHENIX` > `/programs/phenix`. fit/report don't need Phenix. PyRosetta optional/auto-skipped.
- The old `.claude/skills/openmmator-mdff/SKILL.md` is STALE: it says `namdinator run` and imports
  `from namdinator.io...` — fix every reference to `openmmator`, and drop/relabel the CUDA-grid-limit
  troubleshooting row if it is speculative (it was an unconfirmed hypothesis). Keep the guide accurate to the CLI.

## What to write (in /home/dzyla/Code/Namdinator/)

1. **CLAUDE.md** — instructions for an AI coding agent in this repo: what the tool does, the five subcommands,
   which need Phenix, env activation, worked example, --gscale tradeoff, --steps/--minimization-steps guidance,
   standard fit->refine workflow, how to read results.
2. **AGENTS.md** — repo conventions: build/test commands (`python -m pytest -q -m "not slow and not gpu and not phenix"`),
   conda env `namdinator`, dir layout (src/openmmator/{cli,pipeline,engine,io,report,phenix}), rule that new
   behavior should have a test.
3. **RUNGUIDE.md** — a short, actionable "how to run a new model/map pair" guide: pre-flight, minimal decision
   procedure for --gscale/--steps/--minimization-steps, exact fit and run commands, how to read results.
4. **Update `.claude/skills/openmmator-mdff/SKILL.md`** — replace `namdinator` -> `openmmator` everywhere
   (command and module imports), keep it accurate to the verified CLI.
5. **Persist a harness skill**: call skill_create/skill_update to save a reusable `openmmator-mdff` / `namdinator-mdff`
   "how to run" skill into the harness skill store (~/.config/ai/skills and the repo's .agents/skills).

## Ground rules
- Write the four files with write_file/edit_file. Verify each file was written (read it back).
- Then call task_complete listing: the four file paths, the test-tier result (126 passed / 38 deselected),
  and the harness skill(s) you created/updated.
- Do NOT launch openmmator, do NOT run pytest again (already verified), do NOT explore further. Write and report.
- Keep the docs factual and concise. No fabricated options — use exactly what's in the verified facts above
  and what you confirmed from the actual CLI during your earlier investigation.
