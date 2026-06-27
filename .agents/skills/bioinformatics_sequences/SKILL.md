---
name: bioinformatics-sequences
description: Guides sequence search (PyHMMER/BLAST), MSA (muscle5/mafft), phylogenetics (IQ-TREE2/ete3), structure-sequence mapping, and HPC/SLURM patterns. Execute Python and CLI tools directly.
---

# Bioinformatics — Sequences & Pipelines

## Core Rule
Run it. When given a FASTA or asked a sequence question, write and execute the analysis immediately.

## Sequence Search

### PyHMMER (preferred — Python-native, fast, no subprocess)
```python
import pyhmmer

with pyhmmer.easel.SequenceFile("query.fasta", digital=True) as f:
    seqs = f.read_block()

with pyhmmer.plan7.HMMFile("profile.hmm") as hmm_file:
    for hits in pyhmmer.hmmsearch(hmm_file, [seqs]):
        for hit in hits:
            print(hit.name.decode(), f"score={hit.score:.1f}", f"e={hit.evalue:.2e}")
```

Build HMM from MSA:
```python
with pyhmmer.easel.MSAFile("aligned.fasta", digital=True) as msaf:
    msa = msaf.read()
builder = pyhmmer.plan7.Builder(seqs.alphabet)
hmm, _, _ = builder.build_msa(msa, pyhmmer.plan7.Background(seqs.alphabet))
```

### BLAST (local — faster, reproducible)
```python
import subprocess, pandas as pd

subprocess.run([
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

### Remote BLAST (small queries only)
```python
from Bio.Blast import NCBIWWW, NCBIXML
result = NCBIWWW.qblast("blastp", "nr", open("query.fasta").read())
for rec in NCBIXML.parse(result):
    for aln in rec.alignments[:5]:
        hsp = aln.hsps[0]
        print(aln.title[:60], f"score={hsp.score}", f"e={hsp.expect:.2e}")
```

## Multiple Sequence Alignment

```python
import subprocess
from Bio import AlignIO

# muscle5 (fast, high accuracy)
subprocess.run(["muscle", "-align", "seqs.fasta", "-output", "aligned.fasta"], check=True)

# mafft (best for divergent/large alignments)
with open("aligned.fasta", "w") as out:
    subprocess.run(["mafft", "--auto", "--thread", "8", "seqs.fasta"],
                   stdout=out, check=True)

aln = AlignIO.read("aligned.fasta", "fasta")
print(f"{len(aln)} sequences, alignment length {aln.get_alignment_length()}")
```

Visualize:
```python
from pymsaviz import MsaViz
mv = MsaViz("aligned.fasta", wrap_length=60, show_consensus=True)
mv.savefig("msa.png", dpi=150)
```

## Phylogenetics

```python
import subprocess

# IQ-TREE2 — maximum-likelihood tree with ultrafast bootstrap
subprocess.run([
    "iqtree2", "-s", "aligned.fasta",
    "-m", "TEST",          # auto model selection
    "-bb", "1000",         # 1000 ultrafast bootstrap replicates
    "-T", "AUTO",          # auto-detect CPU threads
    "--prefix", "tree_out"
], check=True)
# Output: tree_out.treefile (Newick), tree_out.log

# Visualize with ete3
from ete3 import Tree, TreeStyle
t = Tree("tree_out.treefile")
print(t.get_ascii(show_internal=True))
t.render("tree.png", dpi=150, tree_style=TreeStyle())
```

Parse and annotate with BioPython:
```python
from Bio import Phylo
tree = Phylo.read("tree_out.treefile", "newick")
Phylo.draw_ascii(tree)
```

## Structure–Sequence Mapping

```python
import gemmi

st = gemmi.read_structure("input.pdb")
chain = st[0]["A"]

# Print auth numbering vs sequential label
for res in chain:
    print(f"auth={res.seqid}  label={res.label_seq}  name={res.name}")

# Look up a specific residue by author number (handles insertion codes like 100A)
seqid = gemmi.SeqId("100A")
res = chain[seqid]
print(res.name, [a.name for a in res])
```

Never address PDB residues by list index — insertion codes (`100A`, `100B`) break simple indexing.

## HPC / SLURM Patterns

### SLURM array job (one sample per task)
```bash
#!/bin/bash
#SBATCH --job-name=blast_array
#SBATCH --array=1-100
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=4:00:00

CHUNK=$(ls chunks/chunk_*.fasta | sed -n "${SLURM_ARRAY_TASK_ID}p")
/path/to/conda/envs/bio/bin/blastp \
    -query "$CHUNK" \
    -db /path/to/db \
    -outfmt 6 -num_threads 8 \
    -out "results/$(basename "$CHUNK" .fasta).tsv"
```

### Split FASTA into chunks for parallel BLAST
```python
from Bio import SeqIO
from itertools import islice
import os

def chunk_fasta(infile, chunk_size=500, outdir="chunks"):
    os.makedirs(outdir, exist_ok=True)
    records = SeqIO.parse(infile, "fasta")
    i = 0
    while True:
        batch = list(islice(records, chunk_size))
        if not batch:
            break
        SeqIO.write(batch, f"{outdir}/chunk_{i:04d}.fasta", "fasta")
        i += 1
    print(f"Split into {i} chunks of {chunk_size}")

chunk_fasta("all_seqs.fasta")
```

## Pitfalls
- Never `conda activate` inside SLURM — use full path to env binary: `/path/to/envs/bio/bin/python`
- Write temp files to `$TMPDIR` (node-local fast scratch), not `/tmp` or `$HOME`
- Check disk quota before large jobs: `quota -s` or `df -h $HOME`
- Reference genome chromosome naming: UCSC uses `chr1`, Ensembl uses `1` — mismatch silently drops all reads; check with `samtools view -H sample.bam | grep "^@SQ"`
- Always index BAM before downstream tools: `samtools sort -o sorted.bam input.bam && samtools index sorted.bam`
- BLAST `-outfmt 6` has no header by default — always define column names when reading with pandas
- Install: `pip install pyhmmer biopython pymsaviz ete3 pandas` and `conda install -c bioconda muscle mafft iqtree blast samtools`
