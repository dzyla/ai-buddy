---
name: bio-structure-analysis
description: CRITICAL — when the user mentions any .pdb, .cif, .mmcif, .mrc, .map, .xtc, .dcd, or .trr file: DO NOT use read_file. Write a Python script with gemmi/mrcfile/MDAnalysis and execute it immediately.
---

# Structural Biology Analysis

## FORBIDDEN ACTIONS — CHECK BEFORE EVERY TOOL CALL

**If the file extension is `.pdb`, `.cif`, `.mmcif`, `.mrc`, `.map`, `.xtc`, `.dcd`, or `.trr`:**

- ❌ DO NOT call `read_file` — structural files are large binary or multi-megabyte text; reading them raw floods the context with useless data
- ❌ DO NOT call `list_directory` and then `read_file` — still forbidden
- ✅ INSTEAD: immediately write a Python script using `gemmi` (for PDB/mmCIF) or `mrcfile` (for MRC/MAP) or `MDAnalysis` (for trajectories), save to `/tmp/bio_analysis.py`, and run it with `execute_command`

This rule applies even for "tell me about", "what is in", "describe", "summarize" — any query about a structural file.

## Structure Overview (use for "tell me about this file" / "what's in this structure")

When asked to describe or summarize any PDB/mmCIF file, write this to `/tmp/bio_analysis.py` and run it:

```python
import gemmi, sys

path = sys.argv[1]
st = gemmi.read_structure(path)  # works for both .pdb and .cif/.mmcif

# Metadata (populated for mmCIF; may be empty for plain PDB)
meta = st.info
print(f"Entry:       {st.name or '(unnamed)'}")
print(f"Method:      {meta.get('_exptl.method', meta.get('_em_3d_reconstruction.method', 'N/A'))}")
res = meta.get('_refine.ls_d_res_high') or meta.get('_em_3d_reconstruction.resolution', 'N/A')
print(f"Resolution:  {res} Å")
print(f"Space group: {st.spacegroup_hm or 'N/A'}")
print(f"Models:      {len(st)}")

model = st[0]
chains = list(model)
print(f"\nChains ({len(chains)}):")
for chain in chains:
    residues = list(chain)
    polymer = [r for r in residues if r.entity_type == gemmi.EntityType.Polymer]
    hetero  = [r for r in residues if r.entity_type == gemmi.EntityType.NonPolymer]
    water   = [r for r in residues if r.entity_type == gemmi.EntityType.Water]
    seq = "".join(
        (gemmi.find_tabulated_residue(r.name).one_letter_code or '?')
        for r in polymer
    )
    lig_names = [r.name for r in hetero]
    print(f"  Chain {chain.name}: {len(polymer)} residues, "
          f"{len(hetero)} ligands {lig_names if lig_names else ''}, "
          f"{len(water)} waters")
    if seq:
        print(f"    seq: {seq[:80]}{'...' if len(seq)>80 else ''}")

all_ligands = sorted({r.name for c in model for r in c
                      if r.entity_type == gemmi.EntityType.NonPolymer
                      and r.name not in ('HOH', 'WAT')})
if all_ligands:
    print(f"\nLigands/cofactors: {', '.join(all_ligands)}")
print(f"\nTotal atoms: {sum(len(list(r)) for c in model for r in c)}")
```

Run as: `execute_command python3 /tmp/bio_analysis.py /full/path/to/file.pdb`

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
