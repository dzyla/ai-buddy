# Bioscience Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create four SKILL.md files that make `ai` a capable structural biology / bioinformatics co-pilot, executing Python on the fly to answer structural questions.

**Architecture:** Plain markdown files auto-loaded by `ai.c` from `.agents/skills/*/SKILL.md`. No changes to C or Python code. Each skill has YAML frontmatter (name, description) and a markdown body injected verbatim into the system prompt each run.

**Tech Stack:** Markdown/YAML only. Runtime deps (gemmi, mdanalysis, openmm, rdkit, etc.) installed by user separately — listed in each skill.

## Global Constraints

- No changes to `ai.c` or `ai_mcp.py`
- Skill files must be concise — they're injected into every system prompt (target < 120 lines each)
- All tool recommendations must be current best-of-breed (no legacy tools as primary)
- Cross-cutting rule present in all skills: write and run code, don't just describe it
- Skills live under `.agents/skills/<name>/SKILL.md`

---

### Task 1: `bio_structure_analysis` skill

**Files:**
- Create: `.agents/skills/bio_structure_analysis/SKILL.md`

**Interfaces:**
- Produces: skill loaded at startup, guides agent for any structural analysis query involving PDB/mmCIF/MRC files

- [ ] **Step 1: Create the skill directory and file**

```bash
mkdir -p .agents/skills/bio_structure_analysis
```

- [ ] **Step 2: Write the skill file**

Create `.agents/skills/bio_structure_analysis/SKILL.md` with this exact content:

```markdown
---
name: bio-structure-analysis
description: Guides structural analysis tasks — parsing PDB/mmCIF/MRC files, residue contact/interaction queries, elastic network models. Always write and run Python instead of describing what to run.
---

# Structural Biology Analysis

## Core Rule
When a structural question involves a file, **write a self-contained Python script, save it to `/tmp/bio_analysis.py`, execute it with `execute_command`, and report the actual output**. Never describe what the user could run — run it.

## Parsing

| Format | Primary tool | Notes |
|--------|-------------|-------|
| PDB / mmCIF | `gemmi` | Fastest; correct mmCIF semantics for large assemblies |
| PDB (simple) | `Bio.PDB` | Fine for single-chain, small structures |
| MRC / MAP (cryo-EM density) | `mrcfile` | Always report `voxel_size` from header |
| Trajectory (DCD/XTC/TRR) | `MDAnalysis` | Pair with topology file |

```python
import gemmi
st = gemmi.read_structure("input.cif")  # or .pdb
for model in st:
    for chain in model:
        for res in chain:
            for atom in res:
                print(chain.name, res.name, res.seqid, atom.name, atom.pos)
```

## Residue Interaction / Contact Analysis

For "what residues interact between chain A and B":

```python
import gemmi, numpy as np
from itertools import product

st = gemmi.read_structure("input.cif")
model = st[0]
chainA = [a for c in model if c.name == "A" for r in c for a in r]
chainB = [a for c in model if c.name == "B" for r in c for a in r]

cutoff = 4.5  # Angstrom
contacts = []
for a, b in product(chainA, chainB):
    d = a.pos.dist(b.pos)
    if d < cutoff:
        contacts.append((a, b, round(d, 2)))

for a, b, d in sorted(contacts, key=lambda x: x[2]):
    print(f"{a.residue().name}{a.residue().seqid.num}:{a.name} -- {b.residue().name}{b.residue().seqid.num}:{b.name}  {d}Å")
```

For typed interactions (H-bonds, hydrophobic, salt bridges): run PLIP via subprocess:
```python
import subprocess
result = subprocess.run(["plip", "-f", "input.pdb", "-t"], capture_output=True, text=True)
print(result.stdout)
```

## Elastic Network / Normal Modes

Use ProDy:
```python
import prody
ag = prody.parsePDB("input.pdb")
ca = ag.select("calpha")
anm = prody.ANM("protein")
anm.buildHessian(ca, cutoff=15.0)
anm.calcModes(n_modes=20)
prody.writeNMD("modes.nmd", anm[:3], ca)
```

## Common Pitfalls
- mmCIF has two residue numbering schemes: `label_seq_id` (sequential) vs `auth_seq_id` (author/PDB). Clarify with user which they mean; `gemmi` exposes both.
- Multi-model PDB: iterate `for model in st` — never assume single model.
- MRC voxel size: `import mrcfile; m = mrcfile.open(f); print(m.voxel_size)` — always report this.
- HETATM residues (ligands, modified AA): `gemmi` includes them by default; `Bio.PDB` needs `SMCRA` or explicit HETATM parsing. Do not filter them out when analyzing binding sites.
- Install: `pip install gemmi biopython mdanalysis prody mrcfile plip`
```

- [ ] **Step 3: Verify the file looks right**

```bash
head -5 .agents/skills/bio_structure_analysis/SKILL.md
```
Expected: frontmatter with `name: bio-structure-analysis`

- [ ] **Step 4: Commit**

```bash
git add .agents/skills/bio_structure_analysis/SKILL.md
git commit -m "feat: add bio_structure_analysis skill"
```

---

### Task 2: `md_prep_openmm` skill

**Files:**
- Create: `.agents/skills/md_prep_openmm/SKILL.md`

**Interfaces:**
- Produces: skill guiding agent through PDBFixer → OpenMM preparation pipeline

- [ ] **Step 1: Create directory**

```bash
mkdir -p .agents/skills/md_prep_openmm
```

- [ ] **Step 2: Write the skill file**

Create `.agents/skills/md_prep_openmm/SKILL.md`:

```markdown
---
name: md-prep-openmm
description: Guides MD simulation preparation using the OpenMM Python ecosystem — PDBFixer cleaning, force field selection, solvation, minimization, and short production runs. Always execute Python directly.
---

# MD Simulation Preparation (OpenMM)

## Core Rule
Always write and execute Python. For "prepare this PDB for MD", immediately run PDBFixer then show the OpenMM setup script. Do not describe — execute.

## Standard Pipeline

### 1. Clean with PDBFixer
```python
from pdbfixer import PDBFixer
from openmm.app import PDBFile

fixer = PDBFixer(filename="input.pdb")
fixer.findMissingResidues()
fixer.findNonstandardResidues()
fixer.replaceNonstandardResidues()
fixer.removeHeterogens(keepWater=False)   # set True to keep crystal waters
fixer.findMissingAtoms()
fixer.addMissingAtoms()
fixer.addMissingHydrogens(pH=7.0)

with open("prepared.pdb", "w") as f:
    PDBFile.writeFile(fixer.topology, fixer.positions, f)
print("Done. Saved prepared.pdb")
```

### 2. Force Field Selection

| System | Force field | Water |
|--------|-------------|-------|
| Protein only | `charmm36m.xml` | `charmm36/water.xml` (TIP3P-CHARMM) |
| Protein (AMBER-style) | `amber14-all.xml` | `amber14/tip3pfb.xml` |
| Protein + small molecule | `amber14-all.xml` + OpenFF `openff-2.2.0.offxml` | `amber14/tip3pfb.xml` |
| Membrane protein | Load CHARMM-GUI output or use `packmol-memgen`, then CHARMM36m |

### 3. Solvate, Minimize, Equilibrate
```python
from openmm.app import *
from openmm import *
from openmm.unit import *

pdb = PDBFile("prepared.pdb")
ff = ForceField("charmm36m.xml", "charmm36/water.xml")

modeller = Modeller(pdb.topology, pdb.positions)
modeller.addSolvent(ff, model="tip3p", padding=1.2*nanometers, ionicStrength=0.15*molar)

system = ff.createSystem(modeller.topology,
                          nonbondedMethod=PME,
                          nonbondedCutoff=1.2*nanometers,
                          constraints=HBonds)

integrator = LangevinMiddleIntegrator(300*kelvin, 1/picosecond, 0.002*picoseconds)
simulation = Simulation(modeller.topology, system, integrator)
simulation.context.setPositions(modeller.positions)

print("Minimizing...")
simulation.minimizeEnergy(tolerance=10*kilojoules_per_mole/nanometer)

# Save minimized
with open("minimized.pdb", "w") as f:
    PDBFile.writeFile(simulation.topology,
                      simulation.context.getState(getPositions=True).getPositions(), f)
print("Minimized. Saved minimized.pdb")
```

### 4. Short NVT equilibration + production (append to above)
```python
simulation.reporters.append(DCDReporter("trajectory.dcd", 1000))
simulation.reporters.append(StateDataReporter("md.log", 1000,
    step=True, potentialEnergy=True, temperature=True, progress=True,
    totalSteps=500000))

# NVT 1 ns
simulation.step(500000)
print("NVT done. trajectory.dcd written.")
```

## Analysis
```python
import MDAnalysis as mda
u = mda.Universe("minimized.pdb", "trajectory.dcd")
protein = u.select_atoms("protein")
# RMSD, RMSF, contacts — use MDAnalysis.analysis.*
```

## Pitfalls
- **Always run PDBFixer first** — raw RCSB PDB files have missing atoms that crash ForceField.createSystem()
- **Protonation at target pH**: use `pdb2pqr` or `propka` before adding hydrogens if non-standard protonation matters
- **Small molecule in binding site**: parameterize with OpenFF/GAFF2 *before* solvating
- **Box size**: padding ≥ 1.0 nm minimum; 1.2 nm recommended for globular proteins
- Install: `conda install -c conda-forge openmm pdbfixer mdtraj` then `pip install mdanalysis`
```

- [ ] **Step 3: Verify**

```bash
head -5 .agents/skills/md_prep_openmm/SKILL.md
```
Expected: frontmatter with `name: md-prep-openmm`

- [ ] **Step 4: Commit**

```bash
git add .agents/skills/md_prep_openmm/SKILL.md
git commit -m "feat: add md_prep_openmm skill"
```

---

### Task 3: `structure_based_design` skill

**Files:**
- Create: `.agents/skills/structure_based_design/SKILL.md`

**Interfaces:**
- Produces: skill guiding structure-based drug discovery pipeline — binding site → docking → hits

- [ ] **Step 1: Create directory**

```bash
mkdir -p .agents/skills/structure_based_design
```

- [ ] **Step 2: Write the skill file**

Create `.agents/skills/structure_based_design/SKILL.md`:

```markdown
---
name: structure-based-design
description: Guides structure-based drug discovery — receptor prep, binding site detection, AutoDock Vina docking (Python API), interaction fingerprints, ADME filters. Execute Python directly.
---

# Structure-Based Drug Design

## Core Rule
Write and run code. When given a receptor PDB and a ligand, immediately script the docking pipeline and execute it.

## Structure Sourcing & Quality
- Fetch from PDB: `gemmi.read_structure("4abc.pdb")` or `Bio.PDB.PDBList().retrieve_pdb_file("4ABC")`
- Quality thresholds: resolution ≤ 2.5 Å preferred; R-free < 0.30; inspect for missing loops
- AlphaFold: trust pLDDT > 70; use PAE matrix to assess interface confidence
  ```python
  import numpy as np, json
  pae = np.array(json.load(open("pae.json"))["predicted_aligned_error"])
  interface_pae = pae[chain_a_indices][:, chain_b_indices].mean()
  print(f"Interface PAE: {interface_pae:.1f} Å")
  ```

## Binding Site Detection
```python
# Option A: known co-crystal ligand → extract pocket center
import gemmi
st = gemmi.read_structure("receptor.pdb")
ligand_atoms = [a.pos for m in st for c in m for r in c if r.name == "LIG" for a in r]
center = [sum(p[i] for p in ligand_atoms)/len(ligand_atoms) for i in range(3)]
print(f"Box center: {center}, use box_size=[20,20,20]")

# Option B: de novo with fpocket
import subprocess
subprocess.run(["fpocket", "-f", "receptor.pdb"], check=True)
# Results in receptor_out/ — read receptor_info.txt for top pocket
```

## Receptor Preparation
```python
from pdbfixer import PDBFixer
from openmm.app import PDBFile

fixer = PDBFixer(filename="receptor.pdb")
fixer.removeHeterogens(keepWater=False)
fixer.findMissingAtoms()
fixer.addMissingAtoms()
fixer.addMissingHydrogens(pH=7.4)
with open("receptor_prep.pdb", "w") as f:
    PDBFile.writeFile(fixer.topology, fixer.positions, f)
```

Then convert to PDBQT with Meeko (preferred over legacy prepare_receptor4.py):
```bash
mk_prepare_receptor.py -i receptor_prep.pdb -o receptor.pdbqt
```

## Ligand Preparation
```python
from rdkit import Chem
from rdkit.Chem import AllChem

mol = Chem.MolFromSmiles("CC(=O)Oc1ccccc1C(=O)O")  # or read SDF
mol = Chem.AddHs(mol)
AllChem.EmbedMolecule(mol, AllChem.ETKDGv3())
AllChem.MMFFOptimizeMolecule(mol)
Chem.MolToMolFile(mol, "ligand_3d.sdf")
```

Convert to PDBQT:
```bash
mk_prepare_ligand.py -i ligand_3d.sdf -o ligand.pdbqt
```

## Docking (Vina Python API)
```python
from vina import Vina

v = Vina(sf_name="vina")
v.set_receptor("receptor.pdbqt")
v.set_ligand_from_file("ligand.pdbqt")
v.compute_vina_maps(center=center, box_size=[20, 20, 20])
v.dock(exhaustiveness=16, n_poses=10)
v.write_poses("docked_poses.pdbqt", n_poses=5, overwrite=True)
energies = v.energies(n_poses=5)
for i, e in enumerate(energies):
    print(f"Pose {i+1}: {e[0]:.2f} kcal/mol")
```

## Interaction Analysis
```python
# PLIP for typed interactions
import subprocess
subprocess.run(["plip", "-f", "complex.pdb", "-t", "-x"], check=True)
# Outputs XML with H-bonds, hydrophobic contacts, salt bridges, pi-stacking

# ProLIF for fingerprint matrix across poses
import prolif as plf, MDAnalysis as mda
u = mda.Universe("receptor_prep.pdb", "docked_poses.pdbqt")
fp = plf.Fingerprint()
fp.run_from_iterable([u.trajectory[i] for i in range(5)],
                     plf.Molecule.from_mda(u.select_atoms("protein")),
                     plf.Molecule.from_mda(u.select_atoms("resname LIG")))
print(fp.to_dataframe())
```

## ADME Filters (inline, no external call)
```python
from rdkit.Chem import Descriptors, rdMolDescriptors

def lipinski(mol):
    return {
        "MW": Descriptors.MolWt(mol),
        "LogP": Descriptors.MolLogP(mol),
        "HBD": rdMolDescriptors.CalcNumHBD(mol),
        "HBA": rdMolDescriptors.CalcNumHBA(mol),
        "TPSA": Descriptors.TPSA(mol),
        "pass": (Descriptors.MolWt(mol) <= 500 and
                 Descriptors.MolLogP(mol) <= 5 and
                 rdMolDescriptors.CalcNumHBD(mol) <= 5 and
                 rdMolDescriptors.CalcNumHBA(mol) <= 10)
    }
print(lipinski(mol))
```

## Pitfalls
- Never dock into raw PDB — always prep receptor (hydrogens, remove waters/cofactors unless intentional)
- Vina box must fully enclose the binding site; too-small box gives artifactually high scores
- Check for disulfide bonds before removing HETATM records
- Install: `pip install vina meeko prolif rdkit-pypi plip` and `conda install -c conda-forge pdbfixer openmm`
- fpocket: `sudo apt install fpocket` or build from source
```

- [ ] **Step 3: Verify**

```bash
head -5 .agents/skills/structure_based_design/SKILL.md
```
Expected: frontmatter with `name: structure-based-design`

- [ ] **Step 4: Commit**

```bash
git add .agents/skills/structure_based_design/SKILL.md
git commit -m "feat: add structure_based_design skill"
```

---

### Task 4: `bioinformatics_sequences` skill

**Files:**
- Create: `.agents/skills/bioinformatics_sequences/SKILL.md`

**Interfaces:**
- Produces: skill for sequence search, MSA, phylogenetics, structure-sequence mapping, HPC patterns

- [ ] **Step 1: Create directory**

```bash
mkdir -p .agents/skills/bioinformatics_sequences
```

- [ ] **Step 2: Write the skill file**

Create `.agents/skills/bioinformatics_sequences/SKILL.md`:

```markdown
---
name: bioinformatics-sequences
description: Guides sequence search (PyHMMER/BLAST), MSA (muscle5/mafft), phylogenetics (IQ-TREE2), structure-sequence mapping, and HPC/SLURM patterns. Execute Python/CLI directly.
---

# Bioinformatics — Sequences & Pipelines

## Core Rule
Run it. When given a FASTA or asked a sequence question, write and execute the analysis immediately.

## Sequence Search

### PyHMMER (preferred — no subprocess, fast, Python-native)
```python
import pyhmmer

with pyhmmer.easel.SequenceFile("query.fasta", digital=True) as f:
    seqs = f.read_block()

with pyhmmer.plan7.HMMFile("profile.hmm") as hmm_file:
    for hits in pyhmmer.hmmsearch(hmm_file, [seqs]):
        for hit in hits:
            print(hit.name.decode(), hit.score, hit.evalue)
```

### BLAST (local)
```python
import subprocess, pandas as pd

result = subprocess.run([
    "blastp", "-query", "query.fasta",
    "-db", "/path/to/db", "-outfmt", "6",
    "-evalue", "1e-5", "-num_threads", "8",
    "-out", "blast.tsv"
], check=True)

cols = ["qseqid","sseqid","pident","length","mismatch","gapopen",
        "qstart","qend","sstart","send","evalue","bitscore"]
df = pd.read_csv("blast.tsv", sep="\t", names=cols)
print(df.sort_values("evalue").head(20).to_string())
```

### Remote BLAST (small queries only — slow)
```python
from Bio.Blast import NCBIWWW, NCBIXML
result = NCBIWWW.qblast("blastp", "nr", open("query.fasta").read())
records = list(NCBIXML.parse(result))
for aln in records[0].alignments[:5]:
    print(aln.title, aln.hsps[0].score)
```

## Multiple Sequence Alignment
```python
import subprocess
from Bio import AlignIO

# muscle5 (fast, accurate)
subprocess.run(["muscle", "-align", "seqs.fasta", "-output", "aligned.fasta"], check=True)

# mafft (best for divergent sequences)
subprocess.run(["mafft", "--auto", "--thread", "8", "seqs.fasta"], 
               stdout=open("aligned.fasta", "w"), check=True)

aln = AlignIO.read("aligned.fasta", "fasta")
print(f"{len(aln)} sequences, length {aln.get_alignment_length()}")
```

Visualize:
```python
from pymsaviz import MsaViz
mv = MsaViz("aligned.fasta", wrap_length=60, show_consensus=True)
mv.savefig("msa.png")
```

## Phylogenetics
```python
import subprocess

# IQ-TREE2 — ML tree from MSA
subprocess.run(["iqtree2", "-s", "aligned.fasta", "-m", "TEST",
                "-bb", "1000", "-T", "AUTO", "--prefix", "tree_out"], check=True)
# Output: tree_out.treefile (Newick)

# Visualize with ete3
from ete3 import Tree
t = Tree("tree_out.treefile")
print(t.get_ascii(show_internal=True))
t.render("tree.png", dpi=150)
```

## Structure–Sequence Mapping
```python
import gemmi

# Map sequence position to structure residue (handles insertion codes)
st = gemmi.read_structure("input.pdb")
chain = st[0]["A"]
for res in chain:
    # res.seqid.num = author number, res.label_seq = sequential
    print(res.seqid, res.name, res.label_seq)

# Find residue by author number (e.g. 100A insertion code)
seqid = gemmi.SeqId("100A")
res = chain[seqid]
```

Never use integer list index to address PDB residues — insertion codes (`100A`, `100B`) break simple indexing.

## HPC / SLURM Patterns

### Array job template
```bash
#!/bin/bash
#SBATCH --job-name=blast_array
#SBATCH --array=1-100
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=4:00:00

CHUNK=$(ls chunks/chunk_*.fasta | sed -n "${SLURM_ARRAY_TASK_ID}p")
/path/to/conda/envs/bio/bin/blastp \
    -query "$CHUNK" -db /db/nr \
    -outfmt 6 -num_threads 8 \
    -out "results/$(basename $CHUNK .fasta).tsv"
```

### Chunk FASTA for parallel jobs
```python
from Bio import SeqIO
from itertools import islice
import os

def chunk_fasta(infile, chunk_size=1000, outdir="chunks"):
    os.makedirs(outdir, exist_ok=True)
    records = SeqIO.parse(infile, "fasta")
    i = 0
    while True:
        batch = list(islice(records, chunk_size))
        if not batch:
            break
        SeqIO.write(batch, f"{outdir}/chunk_{i:04d}.fasta", "fasta")
        i += 1
    print(f"Split into {i} chunks")

chunk_fasta("all_seqs.fasta")
```

## Pitfalls
- Never `conda activate` inside SLURM — use full path to env binary
- Write temp files to `$TMPDIR` (node-local fast storage), not `/tmp` or home
- Check disk quota before large jobs: `quota -s`
- Reference genome chromosome naming: UCSC uses `chr1`, Ensembl uses `1` — mismatches silently drop all reads
- Always index BAM before downstream tools: `samtools index sorted.bam`
- Install: `pip install pyhmmer biopython pymsaviz ete3` and `conda install -c bioconda muscle mafft iqtree blast`
```

- [ ] **Step 3: Verify**

```bash
head -5 .agents/skills/bioinformatics_sequences/SKILL.md
```
Expected: frontmatter with `name: bioinformatics-sequences`

- [ ] **Step 4: Commit**

```bash
git add .agents/skills/bioinformatics_sequences/SKILL.md
git commit -m "feat: add bioinformatics_sequences skill"
```

---

## Self-Review

**Spec coverage:**
- bio_data_formats → merged into bio_structure_analysis (gemmi, mrcfile, MDAnalysis) ✓
- md_prep_openmm → full pipeline PDBFixer → minimize → equilibrate ✓
- structure_based_design → sourcing → binding site → prep → Vina → PLIP/ProLIF → ADME ✓
- bioinformatics_sequences → PyHMMER, BLAST, muscle5/mafft, IQ-TREE2, structure mapping, HPC ✓
- Cross-cutting "run code" rule → present in all four skills ✓
- MRC/cryo-EM → covered in bio_structure_analysis (`mrcfile`) ✓
- OpenMM (not GROMACS) as primary MD engine ✓
- Meeko preferred over legacy prepare_receptor4.py ✓

**Placeholders:** None found.

**Type consistency:** No shared interfaces between skills (all independent markdown files).
