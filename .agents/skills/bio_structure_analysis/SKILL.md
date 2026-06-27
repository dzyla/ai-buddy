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
import gemmi
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
    print(f"{a.residue().name}{a.residue().seqid.num}:{a.name} -- "
          f"{b.residue().name}{b.residue().seqid.num}:{b.name}  {d}Å")
```

For typed interactions (H-bonds, hydrophobic, salt bridges) — run PLIP via subprocess:
```python
import subprocess
result = subprocess.run(["plip", "-f", "input.pdb", "-t"], capture_output=True, text=True)
print(result.stdout)
```

## Cryo-EM Density Maps

```python
import mrcfile
with mrcfile.open("map.mrc", mode="r") as m:
    print("Voxel size:", m.voxel_size)       # Å/voxel
    print("Unit cell:", m.header.cella)
    print("Map shape:", m.data.shape)
    print("Min/Max/Mean:", m.data.min(), m.data.max(), m.data.mean())
```

## Elastic Network / Normal Modes

```python
import prody
ag = prody.parsePDB("input.pdb")
ca = ag.select("calpha")
anm = prody.ANM("protein")
anm.buildHessian(ca, cutoff=15.0)
anm.calcModes(n_modes=20)
prody.writeNMD("modes.nmd", anm[:3], ca)
print("Slowest mode collectivity:", prody.calcCollectivity(anm[0]))
```

## Common Pitfalls
- mmCIF has two numbering schemes: `label_seq_id` (sequential) vs `auth_seq_id` (author/PDB). Clarify which is needed; `gemmi` exposes both via `res.seqid` (auth) and `res.label_seq`.
- Multi-model PDB: iterate `for model in st` — never assume single model.
- MRC voxel size: always report it — cryo-EM maps may not be at 1 Å/voxel.
- HETATM residues (ligands, modified AA): `gemmi` includes them; `Bio.PDB` needs explicit handling. Do not filter them when analyzing binding sites.
- Install: `pip install gemmi biopython mdanalysis prody mrcfile plip`
