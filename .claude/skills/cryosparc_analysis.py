# skill_name: cryosparc_analysis
# skill_file: cryosparc_analysis.py

"""
# CryoSPARC Analysis Skill

Use this skill when the user wants to interact with the CryoSPARC instance at `zyla-lab`.

## Connection Details

CryoSPARC is running on the local workstation `zyla-lab` (port 61000).

```python
from cryosparc.tools import CryoSPARC

cs = CryoSPARC(
    host="zyla-lab",
    base_port=61000,
    email="dawid.zyla@cuanschutz.edu",
    password="cryosparc_master_cli_test_2026"
)
```

Always test the connection first:
```python
cs.test_connection()  # Returns True if connected
```

## Core Operations

### Explore Projects and Workspaces

```python
# List all projects
projects = list(cs.find_projects())
for p in projects:
    print(p.uid, p.title, p.desc)

# Get a specific project
p1 = cs.find_project("P1")

# List workspaces in a project
for w in p1.find_workspaces():
    print(w.uid, w.title)
```

### Explore Jobs

```python
# List all jobs in a project
jobs = list(cs.find_jobs("P1"))
print(f"Found {len(jobs)} jobs")

# List jobs in a specific workspace
ws_jobs = list(cs.find_jobs("P1", "W2"))

# Find jobs of a specific type
from datetime import datetime
jobs_by_type = list(cs.find_jobs(
    type="hetero_refine",
    completed_at=(datetime(2025, 1, 1), datetime(2026, 12, 31)),
))

# Get job details
job = cs.find_job("P1", "J57")
print(job.type, job.status, job.title)
print(job.inputs(), job.outputs())
```

### Load and Inspect Datasets

```python
# Load the final output of a job (default)
dataset = job.load_output("particles")  # or "micrographs", "alignments", etc.

# Load a specific version (e.g., intermediate stage)
dataset_v1 = job.load_output("particles", version=1)

# Load only specific slots (columns)
dataset = job.load_output("particles", slots=["micrograph_blob/path", "uid"])

# Query a dataset (like pandas filtering)
subset = dataset.query({"micrograph_blob/path": "/path/to/micrograph.mrc"})
subset = dataset.query({"ctf_estimation/method": "ctf_find"})

# Access dataset columns
print(dataset.columns())
print(len(dataset))

# Convert to pandas DataFrame for analysis
import pandas as pd
df = dataset.to_pandas()
df.describe()
```

### Job Control

```python
# Get available job types
print(cs.job_register)

# Get job sections (deprecated, use job_register)
cs.get_job_sections()

# Get scheduler lanes and targets
lanes = cs.get_lanes()
targets = cs.get_targets()

# Create a new job
new_job = cs.create_job(
    project_uid="P1",
    workspace_uid="W2",
    type="hetero_refine",
    connections={"particles": ("J55", "particles")},
    params={"refine_volume": "initial_model.abj"},
    title="Hetero refinement of initial model",
)

# Get job sections and status
job.get_job_sections()
```

### Save External Results

```python
from cryosparc.tools import Dataset

# Create a new dataset and save it to a project
new_particles = Dataset()
new_particles["uid"] = [123, 456, 789]
new_particles["micrograph_blob/path"] = ["/path/to/micrograph.mrc"] * 3

job_uid = cs.save_external_result(
    project_uid="P1",
    workspace_uid="W2",
    dataset=new_particles,
    type="particle",
    name="particles",
    title="External particle results",
)
print(f"Saved as job: {job_uid}")
```

### File Operations

```python
# List files in a project
files = cs.list_files("P1")

# Copy files within a project
cs.cp("P1", "source/path", "target/path")

# Create directories
cs.mkdir("P1", "new/directory", parents=True)

# Upload a file to the project
with open("local_file.mrc", "rb") as f:
    cs.upload("P1", "target/path/file.mrc", f)

# Download a file from the project
with cs.download("P1", "some/file.mrc") as response:
    data = response.content

# List job assets in the database
assets = cs.list_assets("P1", "J57")
```

### Dataset Operations Reference

| Operation | Code |
|-----------|------|
| List columns | `dataset.columns()` |
| Length | `len(dataset)` |
| Filter rows | `dataset.query({"col": "value"})` |
| Convert to pandas | `df = dataset.to_pandas()` |
| Select columns | `dataset[["col1", "col2"]]` |
| Get row | `dataset[0]` |
| Get column | `dataset["col_name"]` |
| Iterate rows | `for row in dataset:` |

### Common Job Types

| Category | Job Type | Purpose |
|----------|----------|---------|
| Data Import | `import_micrographs` | Import micrograph movies |
| Motion Correction | `motion_correction` | Patch motion correction |
| CT Estimation | `ctf_estimation` | Estimate CTF parameters |
| 2D Classification | `class_2D_new` | 2D classification |
| 2D Classification (Streaming) | `class_2D_streaming` | Real-time 2D classification |
| Particle Selection | `select_2D` | Select good 2D classes |
| Initial Model | `homo_abinit` | Ab initio reconstruction |
| Heterogeneous Refinement | `hetero_refine` | Heterogeneous refinement |
| Homogeneous Refinement | `nonuniform_refine_new` | Non-uniform refinement |
| Export Particles | `export_live_particles` | Export particles |
| Real-time Preview | `live_session` | Real-time preview |

## Analysis Workflow

Typical workflow for analyzing cryo-EM data:

1. Connect to CryoSPARC instance
2. List projects and find the relevant project
3. List workspaces in the project
4. List jobs in the workspace, filtering by type if needed
5. Inspect job status and parameters
6. Load output datasets and query/filter them
7. Analyze particle counts, CTF statistics, micrograph coverage, etc.
8. Save results or create new jobs if needed

## Example: Analyze a Cryo-EM Processing Pipeline

```python
from cryosparc.tools import CryoSPARC
from datetime import datetime
import pandas as pd

# Connect
cs = CryoSPARC(
    host="zyla-lab",
    base_port=61000,
    email="dawid.zyla@cuanschutz.edu",
    password="cryosparc_master_cli_test_2026"
)

# Find the relevant project
project = cs.find_project("P1")

# Find hetero refinement jobs
hetero_jobs = list(cs.find_jobs(
    "P1",
    type="hetero_refine",
    order=-1,
))

print(f"Found {len(hetero_jobs)} hetero refinement jobs")

for job in hetero_jobs:
    print(f"\nJob {job.uid}: {job.title}")
    print(f"  Status: {job.status}")
    print(f"  Inputs: {[str(i) for i in job.inputs()]}")
    print(f"  Outputs: {[str(o) for o in job.outputs()]}")
    
    # Load particles
    try:
        particles = job.load_output("particles")
        df = particles.to_pandas()
        print(f"  Particles: {len(df)}")
        print(f"  Columns: {list(df.columns)}")
    except Exception as e:
        print(f"  Could not load particles: {e}")
```

## Common Patterns

### Get particle counts across all jobs
```python
total_particles = 0
for job in cs.find_jobs("P1"):
    try:
        particles = job.load_output("particles")
        total_particles += len(particles)
    except:
        pass
print(f"Total particles: {total_particles}")
```

### Find jobs by status
```python
running = list(cs.find_jobs("P1", completed_at=None))
completed = list(cs.find_jobs("P1", completed_at=(datetime.min, datetime.max)))
```

### Filter particles by quality
```python
particles = job.load_output("particles")
df = particles.to_pandas()
# Filter by CTF fit, resolution, etc.
good_particles = df[df["ctf_estimation/ctf_figure_of_merit"] > 0.1]
```

## Notes

- The `cryosparc-tools` library is installed at `/home/dzyla/miniconda3/lib/python3.13/site-packages/cryosparc`.
- The library uses Pydantic models for data structures.
- Datasets are lazy-loaded; use `.to_pandas()` to materialize.
- Job outputs may not be available until the job completes.
- The license ID is `0a048e16-60e5-11f1-98a4-5f6b49ceb406`.
- The CryoSPARC instance is at `http://zyla-lab:61000`.

"""
