# PDB chain-interaction analysis tool (deliverable for harness test)
#
# Build:  go build -o pdbtool .
# Run:    ./pdbtool <file.pdb> [--cutoff 4.5]
#
# Goal: parse a PDB file, group atoms by chain, and for every pair of
# chains compute the interface contacts (atoms within a distance cutoff)
# and interacting residue pairs, PISA-style but fast.

package main

import "fmt"

func main() {
	fmt.Println("pdbtool placeholder")
}
