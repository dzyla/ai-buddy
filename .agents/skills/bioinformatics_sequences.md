# Bioinformatics - Sequences & Pipelines

## Core Rule
When given a FASTA sequence or an alignment query, run the appropriate sequence analysis immediately.

## Multiple Sequence Alignment (MSA)
Use MAFFT for general large alignments:
```python
import subprocess
from Bio import AlignIO

# Assuming sequences are in 'seqs.fasta'
with open("aligned.fasta", "w") as out:
    subprocess.run(["mafft", "--auto", "--thread", "8", "seqs.fasta"], stdout=out, check=True)

aln = AlignIO.read("aligned.fasta", "fasta")
print(f"MSA complete. {len(aln)} sequences, length {aln.get_alignment_length()}")
```

## Sequence Search (Local BLAST)
Use BLAST for sequence homology searching:
```python
import subprocess, pandas as pd

subprocess.run([
    "blastp", "-query", "query.fasta",
    "-db", "/path/to/db", "-outfmt", "6",
    "-evalue", "1e-5", "-out", "blast.tsv",
], check=True)

cols = ["qseqid","sseqid","pident","length","mismatch","gapopen",
        "qstart","qend","sstart","send","evalue","bitscore"]
df = pd.read_csv("blast.tsv", sep="\t", names=cols)
print(df.sort_values("evalue").head(5).to_string())
```

## Phylogenetics (IQ-TREE)
```python
import subprocess

subprocess.run([
    "iqtree2", "-s", "aligned.fasta",
    "-m", "TEST",
    "-bb", "1000",
    "-T", "AUTO",
    "--prefix", "tree_out"
], check=True)

# Visualize with ete3
from ete3 import Tree, TreeStyle
t = Tree("tree_out.treefile")
print(t.get_ascii(show_internal=True))
t.render("tree.png", dpi=150, tree_style=TreeStyle())
```

## Structure–Sequence Mapping
```python
import gemmi

st = gemmi.read_structure("input.pdb")
chain = st[0]["A"]

# Print auth numbering vs sequential label
for res in chain:
    print(f"auth={res.seqid}  label={res.label_seq}  name={res.name}")
```