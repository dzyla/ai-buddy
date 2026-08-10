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
// Data model
// ---------------------------------------------------------------------------

type Atom struct {
	Name     string
	ResName  string
	Chain    string
	ResSeq   int
	X, Y, Z  float64
	Element  string
}

type ChainAtoms struct {
	Atoms      []Atom
	ByResidue  map[int][]Atom
}

// ---------------------------------------------------------------------------
// PDB parsing
// ---------------------------------------------------------------------------

// vdW radii (Angstrom) for common elements.  Used for a fast buried-surface
// approximation.  Source: the Bondi van der Waals radii commonly used in
// structural biology.
var vdW = map[string]float64{
	"H":  1.2, "C": 1.7, "N": 1.55, "O": 1.52, "S": 1.8,
	"P":  1.8, "F": 1.47, "CL": 1.75, "BR": 1.85, "I": 1.98,
}

func radiusFor(element string) float64 {
	r, ok := vdW[strings.ToUpper(element)]
	if !ok {
		return 1.7 // default carbon-like
	}
	return r
}

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
	if len(line) < 54 {
		return Atom{}, false
	}
	fmt.Fprintf(os.Stderr, "DEBUG parseLine: len=%d\n", len(line))
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
	x, err := strconv.ParseFloat(strings.TrimSpace(line[30:38]), 64)
	if err != nil {
		return Atom{}, false
	}
	y, err := strconv.ParseFloat(strings.TrimSpace(line[38:46]), 64)
	if err != nil {
		return Atom{}, false
	}
	z, err := strconv.ParseFloat(strings.TrimSpace(line[46:54]), 64)
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
	lineNum := 0
	for scanner.Scan() {
		line := scanner.Text()
		lineNum++
		if len(line) < 54 {
			fmt.Fprintf(os.Stderr, "DEBUG line %d: too short (len=%d): %q\n", lineNum, len(line), line)
			continue
		}
		if !isAtomRecord(line) {
			continue
		}
		fmt.Fprintf(os.Stderr, "DEBUG line %d: ATOM record, line[0:6]=%q\n", lineNum, line[0:6])
		atom, ok := parseLine(line)
		if !ok {
			fmt.Fprintf(os.Stderr, "DEBUG line %d: parseLine failed\n", lineNum)
			continue
		}
		fmt.Fprintf(os.Stderr, "DEBUG line %d: parsed atom=%+v\n", lineNum, atom)
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
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}
	if len(atoms) == 0 {
		fmt.Fprintf(os.Stderr, "no ATOM/HETATM records found in %s\n", path)
		os.Exit(1)
	}

	// DEBUG: print parsed atoms
	fmt.Fprintf(os.Stderr, "DEBUG: total atoms parsed=%d\n", len(atoms))
	for _, a := range atoms {
		fmt.Fprintf(os.Stderr, "DEBUG: name=%q res=%q chain=%q resSeq=%d xyz=(%.2f,%.2f,%.2f) elem=%q\n",
			a.Name, a.ResName, a.Chain, a.ResSeq, a.X, a.Y, a.Z, a.Element)
	}
	chains := groupByChain(atoms)
	pairs := analyzeChains(chains, cutoff)
	printReport(pairs, cutoff)
}
