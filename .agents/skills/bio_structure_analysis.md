# Bio-Structure Analysis (Structural Biology)

## Core Rule
CRITICAL: When a file extension is .pdb, .cif, .mmcif, .mrc, .map, .xtc, .dcd, or .trr, DO NOT use read_file. Write a Python script using gemmi, mrcfile, or MDAnalysis and execute it immediately.

## Structure Overview
Run the following Python script on any PDB/mmCIF file:
```python
import gemmi, sys

path = sys.argv[1]
st = gemmi.read_structure(path)

# Metadata
meta = st.info
print(f"Entry:       {st.name or '(unnamed)'}")
print(f"Resolution:  {meta.get('_em_3d_reconstruction.resolution', 'N/A')} Å")
print(f"Chains ({len(st)}):")
for chain in st:
    polymer = [r for r in chain if r.entity_type == gemmi.EntityType.Polymer]
    lig_names = [r.name for r in chain if r.entity_type == gemmi.EntityType.NonPolymer and r.name not in ('HOH', 'WAT')]
    print(f"  Chain {chain.name}: {len(polymer)} residues, {len(lig_names)} ligands {lig_names}")
```
Run as: `execute_command python3 /tmp/bio_analysis.py /path/to/file.pdb`

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
for a, b, d in contacts:
    print(f"{a.residue().name}{a.residue().seqid.num}:{a.name} -- {b.residue().name}{b.residue().seqid.num}:{b.name}  {d}Å")
```

## Cryo-EM Density Maps
```python
import mrcfile
with mrcfile.open("map.mrc", mode="r") as m:
    print("Voxel size:", m.voxel_size)
    print("Map shape:", m.data.shape)
```