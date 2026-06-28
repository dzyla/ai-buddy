# Structure-Based Drug Design

## Core Rule
Write and run code. When given a receptor PDB and a ligand, immediately script the docking pipeline and execute it.

## Structure Sourcing & Quality
- Fetch from PDB: `gemmi.read_structure("4abc.pdb")`
- Quality thresholds: resolution ≤ 2.5 Å preferred; R-free < 0.30

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
Convert to PDBQT:
```bash
mk_prepare_receptor.py -i receptor_prep.pdb -o receptor.pdbqt
```

## Ligand Preparation
```python
from rdkit import Chem
from rdkit.Chem import AllChem

mol = Chem.MolFromSmiles("CC(=O)Oc1ccccc1C(=O)O")
mol = Chem.AddHs(mol)
AllChem.EmbedMolecule(mol, AllChem.ETKDGv3())
AllChem.MMFFOptimizeMolecule(mol)
Chem.MolToMolFile(mol, "ligand_3d.sdf")
```

## Docking (Vina Python API)
```python
from vina import Vina

v = Vina(sf_name="vina")
v.set_receptor("receptor.pdbqt")
v.set_ligand_from_file("ligand.pdbqt")
v.dock(exhaustiveness=16, n_poses=10)
v.write_poses("docked_poses.pdbqt", n_poses=5, overwrite=True)

for i, e in enumerate(v.energies(n_poses=5)):
    print(f"Pose {i+1}: {e[0]:.2f} kcal/mol")
```

## Interaction Analysis
```python
# PLIP — typed interactions
import subprocess
subprocess.run(["plip", "-f", "complex.pdb", "-t"], check=True)
```

## ADME Filters
```python
from rdkit.Chem import Descriptors
def adme_profile(mol):
    mw   = Descriptors.MolWt(mol)
    logp = Descriptors.MolLogP(mol)
    # ... (rest of function)
    return {"MW": round(mw,1), "LogP": round(logp,2), "Lipinski_pass": True}
print(adme_profile(mol))
```