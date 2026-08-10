package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestBuriedSurfaceArea(t *testing.T) {
	tests := []struct {
		rA, rB, d  float64
		expected   float64
	}{
		// No overlap (touching externally)
		{1.7, 1.7, 3.4, 0},
		// No overlap (separated)
		{1.7, 1.7, 5.0, 0},
		// No overlap (d=0)
		{1.7, 1.7, 0, 0},
		// Partial overlap
		{1.7, 1.7, 3.0, 4.0},
		// Equal radii, touching externally (no overlap)
		{2.0, 2.0, 4.0, 0},
	}
	for _, tt := range tests {
		got := buriedSurfaceArea(tt.rA, tt.rB, tt.d)
		if tt.expected > 0 {
			if got <= 0 {
				t.Errorf("buriedSurfaceArea(%f, %f, %f) = %f, want > 0",
					tt.rA, tt.rB, tt.d, got)
			}
		} else {
			if got != 0 {
				t.Errorf("buriedSurfaceArea(%f, %f, %f) = %f, want 0",
					tt.rA, tt.rB, tt.d, got)
			}
		}
	}
}

func TestRadiusFor(t *testing.T) {
	if radiusFor("C") != 1.7 {
		t.Errorf("radiusFor('C') = %f, want 1.7", radiusFor("C"))
	}
	if radiusFor("O") != 1.52 {
		t.Errorf("radiusFor('O') = %f, want 1.52", radiusFor("O"))
	}
	if radiusFor("X") != 1.7 {
		t.Errorf("radiusFor('X') = %f, want 1.7 (default)", radiusFor("X"))
	}
}

func TestRunOn1brs(t *testing.T) {
	cwd, err := os.Getwd()
	if err != nil {
		t.Fatalf("os.Getwd: %v", err)
	}
	cmd := exec.Command(filepath.Join(cwd, "pdbtool"), "1brs.pdb")
	out, err := cmd.Output()
	if err != nil {
		t.Fatalf("run: %v\n%s", err, out)
	}

	lines := strings.Split(string(out), "\n")
	// The first line should be the header with cutoff.
	if len(lines) == 0 {
		t.Fatal("empty output")
	}
	if !strings.Contains(lines[0], "cutoff") {
		t.Errorf("first line should contain 'cutoff', got: %q", lines[0])
	}

	found := false
	for _, line := range lines {
		if strings.HasPrefix(line, "B") && strings.Contains(line, "E") {
			found = true
			break
		}
	}
	if !found {
		t.Error("B-E pair not found in output")
	}
}

func TestRunOn1brsCutoff(t *testing.T) {
	cwd, err := os.Getwd()
	if err != nil {
		t.Fatalf("os.Getwd: %v", err)
	}
	cmd := exec.Command(filepath.Join(cwd, "pdbtool"), "1brs.pdb", "--cutoff", "3.5")
	out, err := cmd.Output()
	if err != nil {
		t.Fatalf("run: %v\n%s", err, out)
	}
	if !strings.Contains(string(out), "cutoff = 3.5") {
		t.Fatal("cutoff not reflected in output")
	}
}

func TestRunOnMissingFile(t *testing.T) {
	cwd, err := os.Getwd()
	if err != nil {
		t.Fatalf("os.Getwd: %v", err)
	}
	cmd := exec.Command(filepath.Join(cwd, "pdbtool"), "nonexistent.pdb")
	out, err := cmd.CombinedOutput()
	if err == nil {
		t.Error("expected non-zero exit for missing file")
	}
	if !strings.Contains(string(out), "error reading") {
		t.Errorf("expected 'error reading' message, got: %q", out)
	}
}
