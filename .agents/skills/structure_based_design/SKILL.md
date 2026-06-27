---
name: structure-based-design
description: Guides structure-based drug discovery — receptor prep, binding site detection, AutoDock Vina docking (Python API), interaction fingerprints (PLIP/ProLIF), ADME filters. Execute Python directly.
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
  # Define chain index ranges, then:
  interface_pae = pae[chain_a_idx][:, chain_b_idx].mean()
  print(f"Interface PAE: {interface_pae:.1f} Å  (< 5 Å = high confidence)")
  ```

## Binding Site Detection

```python
# Option A: known co-crystal ligand → derive box center
import gemmi
st = gemmi.read_structure("receptor.pdb")
ligand_atoms = [a.pos for m in st for c in m for r in c
                if r.name == "LIG" for a in r]
center = [sum(p[i] for p in ligand_atoms) / len(ligand_atoms) for i in range(3)]
print(f"Box center: {[round(x,2) for x in center]}, box_size=[20,20,20]")

# Option B: de novo with fpocket
import subprocess
subprocess.run(["fpocket", "-f", "receptor.pdb"], check=True)
# Results written to receptor_out/ — inspect receptor_info.txt for top pocket score
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

Convert to PDBQT with Meeko (preferred over legacy `prepare_receptor4.py`):
```bash
mk_prepare_receptor.py -i receptor_prep.pdb -o receptor.pdbqt
```

## Ligand Preparation

```python
from rdkit import Chem
from rdkit.Chem import AllChem

mol = Chem.MolFromSmiles("CC(=O)Oc1ccccc1C(=O)O")  # replace with actual SMILES
mol = Chem.AddHs(mol)
AllChem.EmbedMolecule(mol, AllChem.ETKDGv3())
AllChem.MMFFOptimizeMolecule(mol)
Chem.MolToMolFile(mol, "ligand_3d.sdf")
print("3D conformer generated")
```

Convert to PDBQT:
```bash
mk_prepare_ligand.py -i ligand_3d.sdf -o ligand.pdbqt
```

For format conversion (SDF ↔ MOL2 ↔ PDBQT): `obabel input.sdf -O output.pdbqt --gen3d`

## Docking (Vina Python API)

```python
from vina import Vina

v = Vina(sf_name="vina")
v.set_receptor("receptor.pdbqt")
v.set_ligand_from_file("ligand.pdbqt")
v.compute_vina_maps(center=center, box_size=[20, 20, 20])
v.dock(exhaustiveness=16, n_poses=10)
v.write_poses("docked_poses.pdbqt", n_poses=5, overwrite=True)

for i, e in enumerate(v.energies(n_poses=5)):
    print(f"Pose {i+1}: {e[0]:.2f} kcal/mol")
```

For higher accuracy: Gnina (`gnina --cnn_scoring rescore`) or rDock as alternatives.

## Interaction Analysis

```python
# PLIP — typed interactions (H-bonds, hydrophobic, salt bridges, pi-stacking)
import subprocess
subprocess.run(["plip", "-f", "complex.pdb", "-t", "-x"], check=True)
# XML output in plip_output/ — parse or read the text report

# ProLIF — interaction fingerprint matrix across all poses
import prolif as plf
import MDAnalysis as mda

u = mda.Universe("receptor_prep.pdb", "docked_poses.pdbqt")
fp = plf.Fingerprint()
fp.run_from_iterable(
    [u.trajectory[i] for i in range(min(5, len(u.trajectory)))],
    plf.Molecule.from_mda(u.select_atoms("protein")),
    plf.Molecule.from_mda(u.select_atoms("resname LIG"))
)
print(fp.to_dataframe())
```

## ADME Filters (inline RDKit)

```python
from rdkit.Chem import Descriptors, rdMolDescriptors

def adme_profile(mol):
    mw   = Descriptors.MolWt(mol)
    logp = Descriptors.MolLogP(mol)
    hbd  = rdMolDescriptors.CalcNumHBD(mol)
    hba  = rdMolDescriptors.CalcNumHBA(mol)
    tpsa = Descriptors.TPSA(mol)
    ro5  = mw <= 500 and logp <= 5 and hbd <= 5 and hba <= 10
    return {"MW": round(mw,1), "LogP": round(logp,2), "HBD": hbd,
            "HBA": hba, "TPSA": round(tpsa,1), "Lipinski_pass": ro5}

print(adme_profile(mol))
```

## Pitfalls
- Never dock into a raw PDB — always prep (hydrogens, remove unwanted heterogens)
- Vina box must fully enclose the binding site; undersized box gives artificially high scores
- Check for disulfide bonds before removing HETATM: look for `CONECT` records or `CYX` residues
- Meeko requires `pip install meeko`; Vina Python API: `pip install vina`
- Install: `pip install vina meeko prolif rdkit-pypi plip` and `conda install -c conda-forge pdbfixer openmm`
- fpocket: `sudo apt install fpocket` or build from source (github.com/Discngine/fpocket)
