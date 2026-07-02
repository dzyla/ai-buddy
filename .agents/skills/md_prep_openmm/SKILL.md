---
name: md-prep-openmm
description: CRITICAL — when preparing PDB structures for molecular dynamics (MD) or running OpenMM simulations: Guidelines for PDBFixer cleaning, force fields, solvation, and minimization.
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

| System | Force field | Water model |
|--------|-------------|-------------|
| Protein only | `charmm36m.xml` | `charmm36/water.xml` (TIP3P-CHARMM) |
| Protein (AMBER-style) | `amber14-all.xml` | `amber14/tip3pfb.xml` |
| Protein + small molecule | `amber14-all.xml` + OpenFF `openff-2.2.0.offxml` | `amber14/tip3pfb.xml` |
| Membrane protein | CHARMM-GUI output or `packmol-memgen`, then CHARMM36m |

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

with open("minimized.pdb", "w") as f:
    PDBFile.writeFile(simulation.topology,
                      simulation.context.getState(getPositions=True).getPositions(), f)
print("Minimized. Saved minimized.pdb")
```

### 4. Short NVT equilibration + production
```python
# Append reporters and run after minimization
simulation.reporters.append(DCDReporter("trajectory.dcd", 1000))
simulation.reporters.append(StateDataReporter("md.log", 1000,
    step=True, potentialEnergy=True, temperature=True, progress=True,
    totalSteps=500000))

simulation.step(500000)  # 1 ns NVT at 2 fs/step
print("Done. trajectory.dcd written.")
```

## Analysis
```python
import MDAnalysis as mda
from MDAnalysis.analysis import rms, align

u = mda.Universe("minimized.pdb", "trajectory.dcd")
protein = u.select_atoms("protein")

# RMSD over trajectory
R = rms.RMSD(protein, select="backbone")
R.run()
print(R.results.rmsd[:, 2])  # column 2 = RMSD values

# Unwrap PBC before structural analysis
import MDAnalysis.transformations as trans
u.trajectory.add_transformations(trans.unwrap(protein))
```

## Pitfalls
- **Always run PDBFixer first** — raw RCSB PDB files have missing atoms that crash `ForceField.createSystem()`
- **Protonation at target pH**: use `pdb2pqr` or `propka` before adding hydrogens if His/Asp/Glu protonation matters
- **Small molecule in binding site**: parameterize with OpenFF/GAFF2 *before* solvating
- **Box size**: padding ≥ 1.0 nm minimum; 1.2 nm recommended for globular proteins
- **Charge neutralization**: `addSolvent` with `ionicStrength` handles this; verify with `system.getForces()`
- Install: `conda install -c conda-forge openmm pdbfixer` then `pip install mdanalysis mdtraj`
