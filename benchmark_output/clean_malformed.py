#!/usr/bin/env python3
"""Clean malformed CSV data and produce a quality report."""

import csv
import os
from collections import OrderedDict

INPUT_FILE = "/tmp/malformed.csv"
OUTPUT_CSV = "/home/dzyla/Code/ai-buddy/benchmark_output/cleaned_malformed.csv"
REPORT_FILE = "/home/dzyla/Code/ai-buddy/benchmark_output/data_quality_report.txt"

os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)

# ── Load and parse ────────────────────────────────────────────────────────────
issues = []
rows = []
with open(INPUT_FILE, newline="") as f:
    reader = csv.reader(f)
    header = next(reader)
    for lineno, raw_row in enumerate(reader, start=2):
        row = {"_lineno": lineno, "_raw": raw_row}
        # 1) Column count mismatch
        if len(raw_row) != len(header):
            issues.append(
                f"Line {lineno}: Expected {len(header)} columns, got {len(raw_row)}. "
                f"Raw: {raw_row}"
            )
            rows.append(row)
            continue
        for col, val in zip(header, raw_row):
            row[col] = val

        # 2) Missing values
        for col in header:
            if row[col].strip() == "":
                issues.append(f"Line {lineno}, column '{col}': Missing value.")

        # 3) Non-numeric in 'value' column
        val = row.get("value", "").strip()
        if val != "":
            try:
                float(val)
            except ValueError:
                issues.append(
                    f"Line {lineno}, column 'value': Non-numeric value '{val}'."
                )

        # 4) Notes with embedded commas (already properly quoted by csv reader,
        #    but worth flagging for downstream consumers)
        notes = row.get("notes", "").strip()
        if "," in notes:
            issues.append(
                f"Line {lineno}, column 'notes': Contains embedded comma — '{notes}'."
            )

        rows.append(row)

# ── Clean: drop malformed rows (wrong column count) and flag non-numeric ──────
clean_rows = []
non_numeric_values = {}
for r in rows:
    if len(r["_raw"]) != len(header):
        # Malformed — skip entirely
        continue
    value = r.get("value", "").strip()
    if value == "":
        r["value"] = "NaN"  # missing → NaN marker
        r["value_filled"] = True
    elif value == "NaN":
        r["value_filled"] = False
    else:
        try:
            float(value)
            r["value_filled"] = False
        except ValueError:
            r["value"] = "NaN"
            r["value_filled"] = True
            non_numeric_values[r["_lineno"]] = value
    notes = r.get("notes", "").strip()
    if notes == "":
        r["notes"] = "NaN"
    clean_rows.append(r)

# ── Write cleaned CSV ────────────────────────────────────────────────────────
out_header = [h for h in header if h != "_lineno" and h != "_raw" and h != "value_filled"]
with open(OUTPUT_CSV, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(out_header)
    for r in clean_rows:
        writer.writerow([r.get(h, "") for h in out_header])

# ── Write quality report ─────────────────────────────────────────────────────
report_lines = [
    "=" * 72,
    "DATA QUALITY REPORT — /tmp/malformed.csv",
    "=" * 72,
    "",
    f"Input file       : {INPUT_FILE}",
    f"Output (cleaned) : {OUTPUT_CSV}",
    f"Total raw rows   : {len(rows)}",
    f"Malformed rows   : {sum(1 for r in rows if len(r['_raw']) != len(header))}",
    f"Rows in output   : {len(clean_rows)}",
    f"Non-numeric replaced: {len(non_numeric_values)}",
    "",
    "-" * 72,
    "ISSUES IDENTIFIED",
    "-" * 72,
    "",
]
for i, issue in enumerate(issues, 1):
    report_lines.append(f"  {i:2d}. {issue}")

report_lines += [
    "",
    "-" * 72,
    "HANDLING APPLIED",
    "-" * 72,
    "",
    "  1. Missing values in 'value' column (lines 3, 8): Replaced with 'NaN'.",
    "     This sentinel is standard for downstream numeric analysis tools.",
    "",
    "  2. Missing values in 'notes' column (line 4): Replaced with 'NaN'.",
    "",
    "  3. Non-numeric 'value' entries (line 5, 'abc'): Replaced with 'NaN' and",
    "     flagged in the report. Cannot be coerced to float.",
    "",
    "  4. Non-numeric 'value' entries (line 7, 'Quoted value'): Replaced with",
    "     'NaN'. The CSV parser correctly preserved the 4-column structure,",
    "     so the row was retained with the value column imputed.",
    "",
    "  5. Embedded commas in 'notes' (line 6): Properly handled by the CSV",
    "     parser via double-quote escaping. No data loss.",
    "",
    "-" * 72,
    "OUTPUT SCHEMA",
    "-" * 72,
    "",
    f"  Columns: {', '.join(out_header)}",
    "  NaN indicates a missing or non-numeric value that was imputed.",
    "",
    "-" * 72,
    "NON-NUMERIC VALUES REPLACED",
    "-" * 72,
    "",
]
if non_numeric_values:
    for lineno, val in non_numeric_values.items():
        report_lines.append(f"  Line {lineno}: '{val}' → 'NaN'")
else:
    report_lines.append("  (none)")

report_lines += [
    "",
    "=" * 72,
    "END OF REPORT",
    "=" * 72,
    "",
]

with open(REPORT_FILE, "w") as f:
    f.write("\n".join(report_lines))

print(f"Cleaned CSV written to {OUTPUT_CSV}")
print(f"Quality report written to {REPORT_FILE}")
