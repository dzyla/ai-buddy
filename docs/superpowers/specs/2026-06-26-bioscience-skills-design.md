# Bioscience Skills Design

**Date:** 2026-06-26  
**Scope:** Four new SKILL.md files for the `ai` agent, targeting structural biology, molecular dynamics, structure-based drug design, and bioinformatics/sequence analysis.

---

## Goal

Make `ai` a useful daily co-pilot for structural biology / bioinformatics work. When the user asks a structural question and a file is present, the agent writes and runs Python code immediately — it does not just describe what to do.

---

## Approach: Task-Oriented Skills (Option C)

Four skills organized by task, not domain. All live under `.agents/skills/<name>/SKILL.md` and are auto-loaded into every system prompt.

---

## Skill 1: `bio_structure_analysis`

**File:** `.agents/skills/bio_structure_analysis/SKILL.md`

**Purpose:** On-the-fly structural analysis. Covers parsing, residue interaction queries, cryo-EM density maps, elastic network models.

### Parsing
- **Primary:** `gemmi` for mmCIF and PDB (fast, handles large assemblies, correct mmCIF semantics)
- **Fallback:** `Bio.PDB` (BioPython) for simpler cases
- **MRC/MAP (cryo-EM density):** `mrcfile` — read voxel size, unit cell, inspect map statistics

### Interaction analysis
- Distance-cutoff contact search with MDAnalysis `contacts.Contacts` or a NumPy cdist loop
- PLIP (via subprocess) for typed interaction fingerprints: H-bonds, hydrophobic contacts, salt bridges, pi-stacking
- ProDy for normal mode analysis (ANM/GNM), elastic network models, PRS

### Scripting pattern (mandatory)
When asked any structural question involving a file:
1. Write a self-contained Python script to `/tmp/bio_analysis.py`
2. Execute with `execute_command python3 /tmp/bio_analysis.py`
3. Print results as plain text or compact JSON

Example trigger: *"What residues interact between chain A and B in this mmCIF?"*  
→ write script using `gemmi` to load, iterate over residue pairs, apply distance cutoff, print contacts.

### Common pitfalls to avoid
- mmCIF residue numbering uses `label_seq_id` (sequential) vs `auth_seq_id` (author/PDB numbering) — always clarify which is needed
- Multi-model PDB files: iterate models explicitly, do not assume single model
- MRC files: voxel size is in the header (`mrcfile.open(f).voxel_size`) — always report it
- HETATM vs ATOM: ligands and modified residues are HETATM — do not filter them out when analyzing binding sites

---

## Skill 2: `md_prep_openmm`

**File:** `.agents/skills/md_prep_openmm/SKILL.md`

**Purpose:** Prepare a PDB/mmCIF structure for MD simulation using the OpenMM Python ecosystem. Covers cleaning, force field selection, solvation, minimization, and short production runs.

### Standard pipeline (execute in order)
1. **PDBFixer** — add missing residues, missing heavy atoms, missing hydrogens; remove unwanted heterogens; cap termini
2. **Force field selection:**
   - Protein only: `charmm36m.xml` + `charmm36/water.xml` (TIP3P)
   - Protein + small molecule: `ff14SB` + `tip3p.xml` + OpenFF `openff-2.x.offxml` for ligand
   - Membrane protein: use CHARMM-GUI output or `packmol-memgen`, then load into OpenMM
3. **Solvation:** `Modeller.addSolvent()` with padding ≥ 1.0 nm; add 0.15 M NaCl
4. **Minimization:** `LocalEnergyMinimizer`, tolerance 10 kJ/mol/nm
5. **Equilibration:** short NVT (100 ps) → NPT (500 ps) before production
6. **Output:** save as `.dcd` trajectory + `.pdb` topology for MDAnalysis

### Analysis
- MDAnalysis reads `.dcd` + `.pdb` natively — use for RMSD, RMSF, contacts, H-bonds
- `mdtraj` for quick one-liners (RMSD in 3 lines)
- Always unwrap PBC before structural analysis: `MDAnalysis.transformations.unwrap`

### Pitfalls
- Run PDBFixer before OpenMM — raw PDB files from RCSB often have missing atoms that cause force field errors
- Protonation state matters: use `pdb2pqr` or `propka` to determine correct His/Asp/Glu states at target pH before minimization
- Small molecule in the binding site: parameterize with OpenFF/GAFF2 before solvating, not after

---

## Skill 3: `structure_based_design`

**File:** `.agents/skills/structure_based_design/SKILL.md`

**Purpose:** Structure-based drug discovery pipeline: receptor preparation → binding site detection → docking → interaction analysis → ADME filtering. All steps executable as Python or subprocess calls.

### Structure sourcing and quality
- Fetch from PDB with `gemmi` or `Bio.PDB.PDBList`
- Quality check: resolution ≤ 2.5 Å preferred; check R-free < 0.30; inspect missing loops
- AlphaFold2/ColabFold for missing structures: trust regions with pLDDT > 70; use PAE matrix to assess domain interface confidence
- Compare predicted to experimental with `gemmi superpose` or `ProDy.matchChains`

### Binding site detection
- Known ligand in co-crystal: extract ligand, define box around it (padding 10 Å)
- De novo: `fpocket` via subprocess; parse pocket score and druggability score
- Output binding site as center + dimensions for docking box

### Receptor and ligand preparation
- Receptor: PDBFixer (hydrogens, missing atoms) → save as `.pdbqt` with `Meeko` (preferred over legacy `prepare_receptor4.py`)
- Ligand: RDKit to generate 3D conformer (`AllChem.EmbedMolecule` + MMFF94 minimize) → `Meeko` to `.pdbqt`
- Format conversion fallback: `OpenBabel` via subprocess

### Docking
- AutoDock Vina Python API (`vina.Vina`) — not CLI — for scriptable, reproducible runs
- Parse poses with `Meeko`; rank by binding affinity (kcal/mol)
- For higher accuracy: Gnina (CNN scoring) or rDock as alternatives

### Hit analysis
- PLIP for interaction fingerprints per pose
- `ProLIF` for protein-ligand interaction profiles across multiple poses (fingerprint matrix)
- ADME filters inline with RDKit `Descriptors`: MW ≤ 500, LogP ≤ 5, HBA ≤ 10, HBD ≤ 5, TPSA ≤ 140

---

## Skill 4: `bioinformatics_sequences`

**File:** `.agents/skills/bioinformatics_sequences/SKILL.md`

**Purpose:** Sequence search, multiple sequence alignment, phylogenetics, and structure-sequence mapping. HPC-aware for large-scale jobs.

### Sequence search
- `PyHMMER` (Python bindings for HMMER) — no subprocess needed; profile HMM search against UniProt or custom database
- Local BLAST: `blastp`/`blastn` via subprocess with `-outfmt 6` for tab-separated parseable output
- Remote BLAST: `Bio.Blast.NCBIWWW.qblast` for quick lookups (slow for large queries)

### Multiple sequence alignment
- `muscle5` or `mafft` via subprocess for production alignments
- Parse result with `Bio.AlignIO.read(f, "fasta")`
- Visualize with `pyMSAviz` (produces publication-ready figures)

### Phylogenetics
- IQ-TREE2 via subprocess for maximum-likelihood trees (use `-fast` for quick jobs)
- Parse and visualize trees with `ete3` or `Bio.Phylo`

### Structure-sequence mapping
- Map UniProt positions to PDB/mmCIF residue numbers using SIFTS (`Bio.PDB.MMCIF2Dict` + SIFTS XML)
- Handle insertion codes in PDB (residues like `100A`, `100B`) — never use integer index alone

### HPC patterns
- SLURM array job: one sample per `$SLURM_ARRAY_TASK_ID`
- Never `conda activate` inside SLURM — use full path: `/path/to/env/bin/python`
- Chunk large FASTA files for parallel BLAST: `Bio.SeqIO` + `itertools.islice`
- Always check disk quota before large jobs; write temp files to `$TMPDIR` not `/tmp`

---

## Cross-Cutting Rule (all skills)

**Prefer running code over describing what to run.**

If the user asks a structural or analytical question and a file path is present or inferable, the agent must:
1. Write a minimal, self-contained Python script
2. Execute it with `execute_command`
3. Report the actual output

Do not describe what the user could run — run it.

---

## Implementation

### Files to create
```
.agents/skills/bio_structure_analysis/SKILL.md
.agents/skills/md_prep_openmm/SKILL.md
.agents/skills/structure_based_design/SKILL.md
.agents/skills/bioinformatics_sequences/SKILL.md
```

### No changes to `ai.c` or `ai_mcp.py`
Skills are plain markdown, auto-loaded at startup. No code changes needed.

### Dependency notes (install once, not bundled)
- `gemmi`, `mrcfile`, `mdanalysis`, `prody`, `plip` — `pip install`
- `pdbfixer`, `openmm` — `conda install -c conda-forge`
- `rdkit`, `meeko` — `pip install`
- `prolif`, `pyhmmer`, `pymsviz` — `pip install`
- `fpocket`, `muscle5`, `mafft`, `iq-tree2` — `apt install` or conda
