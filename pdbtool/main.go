package main

import (
	"fmt"
	"math"
	"os"
	"sort"
	"strconv"
	"strings"

	pdblib "github.com/tikz/bio/pdb"
)

// Bondi van der Waals radii (Angstrom) for the fast buried-surface
// approximation.  Source: common structural-biology tables (e.g. Bondi,
// J. Phys. Chem. 1964).
var vdW = map[string]float64{
	"H": 1.2, "C": 1.7, "N": 1.55, "O": 1.52, "S": 1.8,
	"P": 1.8, "F": 1.47, "CL": 1.75, "BR": 1.85, "I": 1.98,
}

// radiusFor returns the van der Waals radius of an element, falling back to a
// carbon-like radius for unknown elements.
func radiusFor(element string) float64 {
	r, ok := vdW[element]
	if !ok {
		return 1.7
	}
	return r
}

// buriedSurfaceArea approximates the buried interface area of two overlapping
// spheres of radii rA/rB whose centres are d apart.  It uses the standard
// spherical-cap formula  BSA = 2*pi*rA*hA + 2*pi*rB*hB, with hA/hB the cap
// heights.  Returns 0 when the spheres do not overlap.
func buriedSurfaceArea(rA, rB, d float64) float64 {
	if d <= 0 || d >= rA+rB {
		return 0
	}
	hA := (rB - rA + d) * (rA + rB - d) / (2 * d)
	hB := (rA - rB + d) * (rA + rB - d) / (2 * d)
	if hA <= 0 || hB <= 0 {
		return 0
	}
	area := 2*math.Pi*rA*hA + 2*math.Pi*rB*hB
	totalA := 4 * math.Pi * rA * rA
	totalB := 4 * math.Pi * rB * rB
	if area > totalA+totalB {
		area = totalA + totalB
	}
	return area
}

// chainData holds the atoms belonging to a single chain, plus an index by
// residue number.
type chainData struct {
	Atoms     []*pdblib.Atom
	ByResidue map[int64][]*pdblib.Atom
}

// contactPair holds the per-pair interaction statistics.
type contactPair struct {
	ChainA, ChainB string
	NContacts      int64
	ResiduePairs   map[[2]int64]int64
	BuriedArea     float64
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintf(os.Stderr, "usage: %s <file.pdb> [--cutoff 4.5]\n", os.Args[0])
		os.Exit(1)
	}
	path := os.Args[1]
	cutoff := 4.5
	for i := 2; i < len(os.Args); i++ {
		switch os.Args[i] {
		case "--cutoff":
			if i+1 < len(os.Args) {
				var err error
				cutoff, err = strconv.ParseFloat(os.Args[i+1], 64)
				if err != nil {
					fmt.Fprintf(os.Stderr, "invalid cutoff: %s\n", os.Args[i+1])
					os.Exit(1)
				}
				i++
			} else {
				fmt.Fprintf(os.Stderr, "--cutoff requires a value\n")
				os.Exit(1)
			}
		default:
			fmt.Fprintf(os.Stderr, "unknown flag: %s\n", os.Args[i])
			os.Exit(1)
		}
	}

	raw, err := os.ReadFile(path)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error reading file: %v\n", err)
		os.Exit(1)
	}

	p, err := pdblib.NewPDBFromRaw(raw)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error parsing PDB: %v\n", err)
		os.Exit(1)
	}

	chains := groupByChain(p)
	pairs := analyzeChains(chains, cutoff)
	printReport(pairs, cutoff)
}

// groupByChain collects ATOM and HETATM atoms from the parsed PDB into a map
// keyed by chain ID.  Atoms with an empty chain ID are placed in chain "" so
// they are reported but do not break the analysis.
func groupByChain(p *pdblib.PDB) map[string]*chainData {
	chains := make(map[string]*chainData)

	addAtom := func(a *pdblib.Atom) {
		if a == nil {
			return
		}
		ch, ok := chains[a.Chain]
		if !ok {
			ch = &chainData{ByResidue: make(map[int64][]*pdblib.Atom)}
			chains[a.Chain] = ch
		}
		ch.Atoms = append(ch.Atoms, a)
		ch.ByResidue[a.ResidueNumber] = append(ch.ByResidue[a.ResidueNumber], a)
	}

	for _, a := range p.Atoms {
		addAtom(a)
	}
	for _, a := range p.HetAtoms {
		addAtom(a)
	}

	return chains
}

func chainList(chains map[string]*chainData) []string {
	keys := make([]string, 0, len(chains))
	for c := range chains {
		keys = append(keys, c)
	}
	sort.Strings(keys)
	return keys
}

func computePairContacts(
	aAtoms, bAtoms []*pdblib.Atom,
	cutoff, cutoffSq float64,
) *contactPair {
	cp := &contactPair{ResiduePairs: make(map[[2]int64]int64)}
	n := len(aAtoms)
	m := len(bAtoms)
	for i := 0; i < n; i++ {
		ai := aAtoms[i]
		for j := 0; j < m; j++ {
			bj := bAtoms[j]
			dx := ai.X - bj.X
			dy := ai.Y - bj.Y
			dz := ai.Z - bj.Z
			d2 := dx*dx + dy*dy + dz*dz
			if d2 <= cutoffSq {
				cp.NContacts++
				rp := [2]int64{ai.ResidueNumber, bj.ResidueNumber}
				cp.ResiduePairs[rp]++
				rA := radiusFor(ai.Element)
				rB := radiusFor(bj.Element)
				d := math.Sqrt(d2)
				cp.BuriedArea += buriedSurfaceArea(rA, rB, d)
			}
		}
	}
	return cp
}

func analyzeChains(chains map[string]*chainData, cutoff float64) []*contactPair {
	cutoffSq := cutoff * cutoff
	keys := chainList(chains)
	var pairs []*contactPair
	for i := 0; i < len(keys); i++ {
		for j := i + 1; j < len(keys); j++ {
			cp := computePairContacts(
				chains[keys[i]].Atoms,
				chains[keys[j]].Atoms,
				cutoff, cutoffSq,
			)
			cp.ChainA, cp.ChainB = keys[i], keys[j]
			pairs = append(pairs, cp)
		}
	}
	sort.Slice(pairs, func(i, j int) bool {
		if pairs[i].NContacts != pairs[j].NContacts {
			return pairs[i].NContacts > pairs[j].NContacts
		}
		return pairs[i].ChainA < pairs[j].ChainA
	})
	return pairs
}

func printReport(pairs []*contactPair, cutoff float64) {
	fmt.Printf("PDB chain-interaction report (cutoff = %.1f A)\n", cutoff)
	fmt.Println(strings.Repeat("-", 70))
	fmt.Printf("%-12s %8s %8s %10s\n", "ChainA", "ChainB", "Contacts", "BuriedSA")
	fmt.Println(strings.Repeat("-", 70))
	for _, cp := range pairs {
		fmt.Printf("%-12s %8s %8d %10.1f\n",
			cp.ChainA, cp.ChainB, cp.NContacts, cp.BuriedArea)
	}
	fmt.Println()
	fmt.Println("Top interacting residue pairs (by contact count):")
	fmt.Println(strings.Repeat("-", 70))
	for _, cp := range pairs {
		if len(cp.ResiduePairs) == 0 {
			continue
		}
		fmt.Printf("--- %s - %s : %d residue pairs, %d contacts ---\n",
			cp.ChainA, cp.ChainB, len(cp.ResiduePairs), cp.NContacts)
		type rp struct {
			resA, resB int64
			count      int64
		}
		var sorted []rp
		for k, v := range cp.ResiduePairs {
			sorted = append(sorted, rp{k[0], k[1], v})
		}
		sort.Slice(sorted, func(i, j int) bool {
			return sorted[i].count > sorted[j].count
		})
		n := 25
		if len(sorted) < n {
			n = len(sorted)
		}
		for _, r := range sorted[:n] {
			fmt.Printf("  %s res %d  -  %s res %d : %d\n",
				cp.ChainA, r.resA, cp.ChainB, r.resB, r.count)
		}
		fmt.Println()
	}
}
