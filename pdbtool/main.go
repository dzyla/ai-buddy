package main

import (
	"bufio"
	"fmt"
	"math"
	"os"
	"sort"
	"strconv"
	"strings"
)

// ---------------------------------------------------------------------------
// Data structures
// ---------------------------------------------------------------------------

// Atom represents a parsed ATOM/HETATM record from a PDB file.
type Atom struct {
	Name    string
	ResName string
	Chain   string
	ResSeq  int
	ResName3 string // 3-letter residue code (alias of ResName)
	X, Y, Z float64
	Element string
}

// ChainAtoms holds the atoms belonging to a single chain, indexed by residue.
type ChainAtoms struct {
	Atoms     []Atom
	ByResidue map[int][]Atom // residue number -> atoms
}

// ---------------------------------------------------------------------------
// vdW radii (Angstrom) keyed by element symbol.
// ---------------------------------------------------------------------------

var vdW = map[string]float64{
	"H":  1.20, "HE": 1.40, "LI": 1.82, "BE": 1.92,
	"B":  1.92, "C":  1.70, "N":  1.55, "O":  1.52,
	"F":  1.47, "NE": 1.54, "NA": 1.02, "MG": 1.28,
	"AL": 1.84, "SI": 1.84, "P":  1.80, "CL": 1.75,
	"AR": 1.88, "K":  2.03, "CA": 1.00, "TI": 1.78,
	"CR": 1.39, "MN": 1.27, "FE": 1.26, "CO": 1.28,
	"NI": 1.24, "CU": 1.32, "ZN": 1.34, "GA": 1.81,
	"GE": 1.79, "AS": 1.80, "SE": 1.80, "BR": 1.85,
	"KR": 2.00, "RB": 2.16, "SR": 2.00, "Y":  1.90,
	"ZR": 1.75, "NB": 1.74, "MO": 1.73, "TC": 1.69,
	"RU": 1.68, "RH": 1.67, "PD": 1.63, "AG": 1.72,
	"CD": 1.55, "IN": 1.93, "SN": 1.83, "SB": 1.81,
	"TE": 1.78, "I":  1.98, "XE": 2.06, "CS": 2.25,
	"BA": 2.15, "LA": 1.95, "HFE": 1.30, "OXA": 1.52,
}

func radiusFor(element string) float64 {
	elem := strings.ToUpper(strings.TrimSpace(element))
	if r, ok := vdW[elem]; ok {
		return r
	}
	// Fallback: try single-letter element.
	for i := 0; i < len(elem); i++ {
		cand := elem[i : i+1]
		if r, ok := vdW[cand]; ok {
			return r
		}
	}
	return 1.70 // default for unknown elements
}

// ---------------------------------------------------------------------------
// PDB parsing
// ---------------------------------------------------------------------------

const (
	recordLen = 6
)

func isAtomRecord(line string) bool {
	if len(line) < 54 {
		return false
	}
	rec := line[0:6]
	return rec == "ATOM  " || rec == "HETATM"
}

func parseLine(line string) (Atom, bool) {
	if !isAtomRecord(line) {
		return Atom{}, false
	}
	atom := Atom{}
	// Atom name: columns 13-16 (0-based 12:16), right-justified.
	atom.Name = strings.TrimSpace(line[12:16])
	if atom.Name == "" {
		return Atom{}, false
	}
	// Residue name: columns 18-20 (0-based 17:20).
	atom.ResName = strings.TrimSpace(line[17:20])
	// Chain ID: column 22 (0-based 21:22). Blank => "".
	atom.Chain = strings.TrimSpace(line[21:22])
	// Residue sequence number: columns 23-26 (0-based 22:26).
	resSeq, err := strconv.Atoi(strings.TrimSpace(line[22:26]))
	if err != nil {
		return Atom{}, false
	}
	atom.ResSeq = resSeq
	// Coordinates: x cols 31-38, y cols 39-46, z cols 47-54 (0-based).
	x, err := strconv.ParseFloat(line[30:38], 64)
	if err != nil {
		return Atom{}, false
	}
	y, err := strconv.ParseFloat(line[38:46], 64)
	if err != nil {
		return Atom{}, false
	}
	z, err := strconv.ParseFloat(line[46:54], 64)
	if err != nil {
		return Atom{}, false
	}
	atom.X, atom.Y, atom.Z = x, y, z
	// Element symbol: columns 77-78 (0-based 76:78).
	atom.Element = strings.TrimSpace(line[76:78])
	if atom.Element == "" {
		atom.Element = elementFromName(atom.Name)
	}
	return atom, true
}

// elementFromName derives an element symbol from an atom name as a fallback
// when the PDB element field is empty.  It handles the common PDB convention
// where the element is the last alphabetic character(s) forming a known symbol.
func elementFromName(name string) string {
	s := strings.TrimSpace(name)
	if s == "" {
		return "C"
	}
	// Special case: "CA" (alpha carbon) is carbon, not calcium.
	if s == "CA" || s == "HA" {
		return map[string]string{"CA": "C", "HA": "H"}[s]
	}
	// Try two-letter then one-letter element symbols.
	for l := 2; l >= 1; l-- {
		for i := 0; i <= len(s)-l; i++ {
			cand := strings.ToUpper(s[i:i+l])
			if _, ok := vdW[cand]; ok {
				return cand
			}
		}
	}
	return "C"
}

// ParsePDB opens a PDB file and returns all ATOM/HETATM atoms.
func ParsePDB(path string) ([]Atom, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()

	var atoms []Atom
	scanner := bufio.NewScanner(file)
	scanner.Split(bufio.ScanLines)
	for scanner.Scan() {
		line := scanner.Text()
		if len(line) < 54 {
			continue
		}
		if !isAtomRecord(line) {
			continue
		}
	atom, ok := parseLine(line)
		if !ok {
			continue
		}
		atoms = append(atoms, atom)
	}
	return atoms, scanner.Err()
}

// ---------------------------------------------------------------------------
// Chain grouping & indexing
// ---------------------------------------------------------------------------

func groupByChain(atoms []Atom) map[string]*ChainAtoms {
	chains := make(map[string]*ChainAtoms)
	for _, a := range atoms {
		ch, ok := chains[a.Chain]
		if !ok {
			ch = &ChainAtoms{ByResidue: make(map[int][]Atom)}
			chains[a.Chain] = ch
		}
		ch.Atoms = append(ch.Atoms, a)
		ch.ByResidue[a.ResSeq] = append(ch.ByResidue[a.ResSeq], a)
	}
	return chains
}

func chainList(chains map[string]*ChainAtoms) []string {
	keys := make([]string, 0, len(chains))
	for c := range chains {
		keys = append(keys, c)
	}
	sort.Strings(keys)
	return keys
}

// ---------------------------------------------------------------------------
// Contact analysis
// ---------------------------------------------------------------------------

type ContactPair struct {
	ChainA, ChainB string
	NContacts      int64
	ResiduePairs   map[[2]int]int // [resA, resB] -> contact count
	BuriedArea     float64
}

func computePairContacts(aAtoms, bAtoms []Atom, cutoff float64, cutoffSq float64) *ContactPair {
	cp := &ContactPair{ResiduePairs: make(map[[2]int]int)}
	n := len(aAtoms)
	m := len(bAtoms)
	for i := 0; i < n; i++ {
		ai := &aAtoms[i]
		for j := 0; j < m; j++ {
			bj := &bAtoms[j]
			dx := ai.X - bj.X
			dy := ai.Y - bj.Y
			dz := ai.Z - bj.Z
			d2 := dx*dx + dy*dy + dz*dz
			if d2 <= cutoffSq {
				cp.NContacts++
			 rp := [2]int{ai.ResSeq, bj.ResSeq}
				cp.ResiduePairs[rp]++
			 // Buried surface approximation.
				rA := radiusFor(ai.Element)
				rB := radiusFor(bj.Element)
				d := math.Sqrt(d2)
				delta := (rA + rB) - d
				if delta > 0 {
					cp.BuriedArea += math.Pi * delta * (rA + rB - delta/2.0)
				}
			}
		}
	}
	return cp
}

func analyzeChains(chains map[string]*ChainAtoms, cutoff float64) []*ContactPair {
	cutoffSq := cutoff * cutoff
	keys := chainList(chains)
	var pairs []*ContactPair
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
	// Sort by contact count descending.
	sort.Slice(pairs, func(i, j int) bool {
		if pairs[i].NContacts != pairs[j].NContacts {
			return pairs[i].NContacts > pairs[j].NContacts
		}
		return pairs[i].ChainA < pairs[j].ChainA
	})
	return pairs
}

// ---------------------------------------------------------------------------
// Output
// ---------------------------------------------------------------------------

func printReport(pairs []*ContactPair, cutoff float64) {
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
		// Sort residue pairs by count.
		type rp struct {
			resA, resB int
			count      int
		}
		var sorted []rp
		for k, v := range cp.ResiduePairs {
			sorted = append(sorted, rp{k[0], k[1], v})
		}
		sort.Slice(sorted, func(i, j int) bool {
			return sorted[i].count > sorted[j].count
		})
		for _, r := range sorted[:25] {
			fmt.Printf("  %s res %d  -  %s res %d : %d\n",
				cp.ChainA, r.resA, cp.ChainB, r.resB, r.count)
		}
		fmt.Println()
	}
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

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

	atoms, err := ParsePDB(path)
	fmt.Fprintf(os.Stderr, "[DEBUG] atoms=%d err=%v\n", len(atoms), err)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}
	if len(atoms) == 0 {
		fmt.Fprintf(os.Stderr, "no ATOM/HETATM records found in %s\n", path)
		os.Exit(1)
	}

	chains := groupByChain(atoms)
	pairs := analyzeChains(chains, cutoff)
	printReport(pairs, cutoff)
}
