---
name: learn-software-package
description: Use when asked to learn how to run an unfamiliar software package/codebase. Read the docs, generate CLAUDE.md/AGENTS.md/skill.md, build a headless API, test on real examples, and persist what you learned.
---

# Learn How To Run A Software Package

When you must figure out how to run an unfamiliar software package and leave the
knowledge behind for future use, follow this methodical workflow. It turns a
black box into a documented, tested, reusable capability.

## When to use
- The user hands you a new repo/tool and says "learn to run it" or "make an API for it".
- You must run a package you have never used before and then persist how to do it.
- You need a headless (no-GUI / no-web-server) way to drive an app that ships with a UI.

## 0. Survey first (read-only, always allowed)
- `ls` the repo root; read `README.md`, `requirements.txt`, `pyproject.toml`,
  `setup.py`, `Makefile`, `*.md` docs, `config*`, and any `examples/` / `notebooks/`.
- Map the module layout (`find . -name "*.py" -o -name "*.ipynb"`).
- Identify the entry point (e.g. `app.py`, `main.py`, a CLI, a notebook).
- Check whether the app is *UI-coupled*: does it `import streamlit`/`flask`/`gradio`
  at module level and sprinkle UI calls (`st.session_state`, `st.spinner`,
  `st.error`, `st.plotly_chart`) through the computational functions? If so you will
  need a **headless stub** (step 3) to run it without the UI server.

## 1. Set up a reproducible environment
- Create a dedicated conda/venv: `conda create -n <pkg> python=<version>` then
  `pip install -r requirements.txt`. Prefer the version pinned in `.python-version`.
- Note any git deps (`git+https://...`) — they need network + a compiler.
- Verify: `import <package>` and each listed dependency succeeds.
- Record the exact interpreter path (e.g. `/home/dzyla/miniconda3/envs/<pkg>/bin/python`)
  so subsequent runs use the right env.

## 2. Build a headless Streamlit stub (for UI-coupled apps)
The clean trick: create a `headless_streamlit.py` module that mimics the *subset* of
the Streamlit API the app actually calls, install it with
`sys.modules["streamlit"] = stub` BEFORE importing the app's modules, and drive the
pure computation directly. Key requirements:
- `session_state` must be a real dict-like (attribute access + `.get()`/`.update()`/`in`).
- UI widgets (`spinner`, `progress`, `error`, `warning`, `info`, `write`, `markdown`,
  `plotly_chart`, `pyplot`, `dataframe`, `selectbox`, `slider`, ...) degrade to no-ops
  or to their default/first option.
- `columns()`/`expander()`/`tabs()`/`form()` must return context managers / column
  stand-ins so chained calls don't crash.
- CRITICAL: mark the stub as a package (`stub.__path__ = []`) and pre-register
  submodules (`sys.modules["streamlit.components"]`, `...v1`) so third-party libs
  (e.g. `stmol` does `import streamlit.components.v1`) import without hitting disk.
- Install BOTH the top-level module and the submodules in `sys.modules`.

## 3. Write a high-level Python API
Create `<pkg>_api.py` that wraps the pipeline into typed, documented functions:
- `install()` the stub, then import the app's modules.
- Expose one function per pipeline stage (fetch / align / msa / analyse / plot / map)
  plus a single `run_pipeline(...)` orchestrator returning a dict of results.
- Write outputs (CSV/TSV tables, HTML plots, trees) to disk with `write_outputs()`.
- IMPORTANT: seed the stub's `session_state` with the keys the modules read
  (e.g. `alignment_mapping`, `result`, `reference_seq`, `proteins_list`, `outputs`).

## 4. Test on real examples
- Self-test with a tiny local FASTA (no network) to validate the pipeline wiring.
- Then run a real example with network (e.g. a real UniProt/NCBI query) if the app
  fetches data. Use a generous but finite `max_seqs` to keep it fast.
- Fix bugs you find (e.g. the native `al2co` binary rejects a `CLUSTAL X` header but
  accepts `CLUSTAL W`; identical sequences make it exit with zero variance and no
  output — so self-test data must be non-identical).

## 5. Generate companion docs
In the app repo, write:
- `CLAUDE.md` / `AGENTS.md` — how to run, the headless API, environment, pitfalls.
- `skill.md` (or a `space_skill.md`) — a reusable skill capturing what you learned.
- A Jupyter notebook (`examples/*.ipynb`) demonstrating the headless API end-to-end.
- README additions: the headless usage + how to cite.

## 6. Persist what you learned
- Use `skill_create` / `skill_update` to save the reusable knowledge into the harness
  skills dir (`.agents/skills/` and `~/.config/ai/skills/`).
- Record quirks/pitfalls you hit so future sessions skip the debugging.

## Pitfalls
- Don't try to run a Streamlit app headless by importing `app.py` — it calls
  `st.set_page_config` and top-level UI code at import. Stub `streamlit` and call the
  `space/*` computation modules directly instead.
- Native binaries called via `subprocess` (like al2co) are strict about input header
  formats — normalize the header before calling.
- Some packages are hard-pinned (`pyfamsa` needs `ipython_genutils`; `stmol` needs
  `streamlit.components.v1`). Respect the pins / add the stub.
- For NCBI Entrez you must pass an email (e.g. `dzyla@lji.org`).
- **Small-model harness finding (validated 2026-08):** a 32B model driving this
  workflow can spin on a failing command (e.g. `conda` error loop) and ignore a
  "do NOT reinstall dependencies" instruction for a while before recovering. Give it
  a bounded timeout and a very explicit interpreter path; do NOT expect it to respect
  "don't touch X" fully on the first pass — verify outputs afterward.
- **Jupyter notebooks the agent generates need a robust `sys.path` preamble** that
  resolves the repo root by walking up from the notebook location, not just `os.getcwd()`.
- **Pitfall-demo cells must catch the error they demonstrate** — e.g. a cell showing
  "identical sequences → zero variance" will crash with a confusing `FileNotFoundError`
  unless wrapped in `try/except`.

## Verification
- Run the package's existing test (`python test_*.py`) and your API self-test.
- Confirm outputs exist: tables, HTML plots, Newick tree.
- If you changed the harness, run `make` and `make test` in the harness repo.