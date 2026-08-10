# Learning task: learn how to run the `openmmator` (Namdinator) software package

You are in /home/dzyla/Code/Namdinator. This is a real scientific software
package for MDFF flexible fitting of atomic models into cryo-EM density maps.
Your job is to LEARN how to run it, DOCUMENT what you learned, TEST it for real,
FIX any problems you find, and PERSIST the knowledge so future sessions inherit it.

Do this as a careful senior engineer would.

## Phase 1 — Investigate (read, don't guess)
Read these to understand the tool. Do not assume — verify against the code.
- README.md  (the command is `openmmator`, NOT `namdinator` — the package was renamed)
- pyproject.toml (name, version, entry point, python requirement, test markers)
- environment.yml (the conda env is already built and named `namdinator`; it has OpenMM 8.5.2 with CUDA)
- src/openmmator/cli.py and the subcommand definitions
- src/openmmator/engine/*.py, src/openmmator/io/*.py, src/openmmator/report/*.py as needed
- tests/ (unit and integration) to see how the API is exercised
- Any existing skill/doc: .claude/skills/openmmator-mdff/SKILL.md is STALE — it says
  `namdinator run` and imports `from namdinator.io...` but the real command and module
  are `openmmator`. You must fix it.

Use the search/read tools liberally. Confirm the real subcommands by running
`openmmator --help` and `openmmator <subcmd> --help` (activate env first).

## Phase 2 — Environment
The python env with openmm is activated via:
  source /home/dzyla/miniconda3/etc/profile.d/conda.sh && conda activate namdinator
The package is installed editable from this repo, so `openmmator` should be on PATH.
Verify: `which openmmator`, `openmmator --version`, and that
`python -c "import openmmator"` works.

## Phase 3 — Test it for real (prove it runs)
1. Run the FAST test tier first (no GPU, no Phenix):
     python -m pytest -q -m "not slow and not gpu and not phenix"
   Fix anything that fails.
2. Run a REAL short MDFF smoke fit on the shipped test pair to prove the actual
   pipeline works end to end. The files already exist in staging/:
     staging/3jd8.pdb   (model)   and   staging/emd_6640.map   (density map)
   Run a SHORT fit (keep it cheap, do NOT run a long simulation):
     openmmator fit --pdb staging/3jd8.pdb --map staging/emd_6640.map \
       --resolution 3.5 --workdir /tmp/openmmator_smoke --gscale 5 \
       --steps 50 --minimization-steps 50
   Then read the outputs in /tmp/openmmator_smoke (results.json, summary.txt, last_frame.pdb).
   Report the metrics honestly, even if the tiny number of steps made little
   movement. The point is to prove the pipeline RUNS.

## Phase 4 — Document (generate the artifacts)
Generate these files in /home/dzyla/Code/Namdinator:
1. CLAUDE.md — high-signal instructions for an AI coding agent working in this repo:
   what the tool does, the five subcommands (run/fit/refine/validate/report and which need Phenix),
   how to activate the env, the worked example command, the critical --gscale tradeoff
   (higher = better map fit but worse geometry; NOT comparable to NAMD's 0.3-0.6),
   --minimization-steps guidance, and the standard workflow (fit -> refine).
2. AGENTS.md — repo conventions: build/test commands, conda env, running pytest,
   directory layout, and the rule that any new behavior should be tested.
3. UPDATE the skill at .claude/skills/openmmator-mdff/SKILL.md to be correct:
   replace every `namdinator` with `openmmator` (command and module imports in the
   pre-flight snippet), and make sure the guide matches the real CLI as you verified it.
4. A short markdown "run guide" file — RUNGUIDE.md — in the repo root capturing exactly
   how to run `fit` and `run` on a new model/map pair, including the pre-flight, the
   minimal decision procedure for picking --gscale/--steps/--minimization-steps, and
   how to read results.json / summary.txt.

## Phase 5 — Self-improve (persist what you learned)
Use the harness's self-improvement mechanisms:
- skill_create or skill_update to persist a reusable "how to run openmmator/namdinator"
  skill into the harness skill store (~/.config/ai/skills and repo .agents/skills) so
  future sessions inherit it. Name it something like `namdinator-mdff` or `openmmator-mdff`.
- skill_note/save_memory for any non-obvious gotchas you hit (e.g. the rename
  namdinator->openmmator, that CUDA needs the driver in step with the pin, how gscale
  is normalized, the fast test tier command).

## Phase 6 — Report
When done, call task_complete with a summary that includes:
- what the package is and the exact working command you verified
- what artifacts you generated (CLAUDE.md, AGENTS.md, RUNGUIDE.md, updated skill)
- the real output of your smoke fit (before/after metrics from results.json)
- the test tier result (pass/fail count)
- any issues found and how you fixed them
- which skills you created/updated for the harness

## Ground rules
- READ before you write. Never invent commands or API shapes — verify against code/--help.
- Run state-changing commands one at a time; change approach on error; never repeat the
  identical failing call three times — investigate instead.
- Use `think` to plan before major actions.
- The conda env is `namdinator`; do NOT try to pip-install or rebuild the world — it is
  already installed editable. If a test needs a dep that's missing, report it.
- Keep the smoke fit SHORT (steps ~50). Do not launch a long MDFF or calibration sweep.
- Do not commit to git unless it's already the norm here.
