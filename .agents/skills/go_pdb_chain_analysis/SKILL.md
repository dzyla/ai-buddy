---
name: go-pdb-chain-analysis
description: Use when building a Go tool that parses PDB files or analyzes chain/protein interactions. Reuse github.com/tikz/bio/pdb instead of writing a parser.
---
# Go PDB Parsing & Chain-Interaction Analysis (reuse, don't reinvent)

## When to use
Any Go program that reads a `.pdb`/`.mmcif` file or computes protein chain
interactions (contacts, interfaces, buried surface area, PISA-style analysis).

## Key rule: reuse the existing parser
Do NOT hand-write PDB ATOM/HETATM column parsing. Use the maintained module:

```
go get github.com/tikz/bio        # module
import pdblib "github.com/tikz/bio/pdb"   # package
```

## Verified API (works on local PDB files)
```go
raw, err := os.ReadFile("1brs.pdb")
p, err  := pdblib.NewPDBFromRaw(raw)   // parses ATOM + HETATM
p.Atoms     // []*pdblib.Atom with: .Chain, .ResidueNumber, .X, .Y, .Z, .Element, .Residue
p.HetAtoms  // separate HETATM atoms
```
- `NewPDBFromID("1BRS")` fetches+parses by RCSB accession.
- Proven to parse 1BRS into chains A–F correctly (4640 ATOM + 513 HETATM).
- The module also exposes `p.Chains` (chain -> residue map), `p.SeqRes`, SIFTS.

## Interaction analysis on top
Group `p.Atoms` (+ `p.HetAtoms`) by `.Chain`, then for each chain pair:
- contacts: atom pairs with squared distance <= cutoff^2 (default cutoff 4.5 Å)
- residue pairs: map `[2]int64{resA, resB} -> contactCount`
- buried interface SA: spherical-cap approximation
  `BSA = 2*pi*rA*hA + 2*pi*rB*hB`, `hA=(rB-rA+d)*(rA+rB-d)/(2*d)`,
  return 0 when `d >= rA+rB`. Use Bondi vdW radii (C 1.7, N 1.55, O 1.52,
  S 1.8, P 1.8, H 1.2, default 1.7).
- Use squared distances (no sqrt) for the hot loop; O(n^2) is fine up to ~5k atoms.

## Pitfalls
- `go build` warns "GOPATH and GOROOT are the same directory" — harmless when Go is
  installed to ~/go; set `GOPATH=~/gopath` to silence it.
- Handle empty chain IDs (column 22 blank) by grouping under chain `""`.
- `exec.Output()` captures only stdout — errors go to stderr, so tests should use
  `cmd.CombinedOutput()`.

## Verification
- `go test ./...` and `./pdbtool 1brs.pdb`
- 1BRS (barnase/barstar) MUST show barnase/barstar interfaces as top pairs:
  A–D ~518, B–E ~541, C–F ~403 contacts. If not, contact logic is wrong.

## Sources
- [github.com/tikz/bio](https://github.com/tikz/bio) (package github.com/tikz/bio/pdb)
