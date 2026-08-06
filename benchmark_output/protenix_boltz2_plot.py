#!/usr/bin/env python3
"""Compare Protenix vs Boltz2 scores: scatter plot + Pearson correlations."""

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from scipy import stats
import os

# Paths
CSV_PATH = "/mnt/HDD1/Software/Protenix/protenix_vs_boltz2_scores.csv"
OUT_DIR = "/home/dzyla/Code/ai-buddy/benchmark_output"
os.makedirs(OUT_DIR, exist_ok=True)
OUT_PATH = os.path.join(OUT_DIR, "task2_protenix_boltz2_comparison.png")

# Load
df = pd.read_csv(CSV_PATH)

# --- Scatter plot ---
fig, ax = plt.subplots(figsize=(9, 7))

# Color mapping: Binder -> red, Non-Binder -> blue
colors = df["label"].map({"Binder": "red", "Non-Binder": "blue"})

# Filter to valid rows
valid_mask = df["protenix_pae_min"].notna() & df["boltz2_pae_min"].notna()
if valid_mask.sum() > 0:
    valid_df = df[valid_mask]
    valid_colors = colors[valid_mask]
    sc = ax.scatter(
        valid_df["protenix_pae_min"],
        valid_df["boltz2_pae_min"],
        c=valid_colors,
        alpha=0.75,
        s=80,
        edgecolors="black",
        linewidths=0.5,
    )
else:
    # If no valid data, plot Protenix values only with a note
    ax.scatter(
        df["protenix_pae_min"],
        [df["protenix_pae_min"].min() * 0.5] * len(df),
        c=colors,
        alpha=0.75,
        s=80,
        edgecolors="black",
        linewidths=0.5,
    )
    ax.text(
        0.5,
        0.5,
        "Boltz2 pAE_min data unavailable\n(All values are NaN)",
        transform=ax.transAxes,
        fontsize=14,
        ha="center",
        va="center",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="wheat", edgecolor="orange", alpha=0.9),
    )

# Diagonal reference line
lims = [
    min(df["protenix_pae_min"].min(), df["boltz2_pae_min"].min()) * 0.95,
    max(df["protenix_pae_min"].max(), df["boltz2_pae_min"].max()) * 1.05,
]
ax.plot(lims, lims, "--", color="gray", linewidth=1.5, label="y = x")

# Correlation coefficient (drop NaN rows)
mask = df["protenix_pae_min"].notna() & df["boltz2_pae_min"].notna()
n_valid = mask.sum()
if n_valid >= 2:
    r, pval = stats.pearsonr(df.loc[mask, "protenix_pae_min"], df.loc[mask, "boltz2_pae_min"])
    ax.text(
        0.05,
        0.95,
        f"Pearson r = {r:.3f}\np = {pval:.2e}",
        transform=ax.transAxes,
        fontsize=12,
        verticalalignment="top",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="gray", alpha=0.85),
    )
else:
    ax.text(
        0.05,
        0.95,
        "Boltz2 pAE_min: All values are NaN\n(Comparison not possible)",
        transform=ax.transAxes,
        fontsize=12,
        verticalalignment="top",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="gray", alpha=0.85),
    )

# Legend
binder_patch = mpatches.Patch(color="red", label="Binder")
nonbinder_patch = mpatches.Patch(color="blue", label="Non-Binder")
ax.legend(
    handles=[binder_patch, nonbinder_patch],
    loc="lower right",
    framealpha=0.9,
)

ax.set_xlabel("Protenix pAE_min", fontsize=12)
ax.set_ylabel("Boltz2 pAE_min", fontsize=12)
ax.set_title("Protenix pAE_min vs Boltz2 pAE_min", fontsize=14, fontweight="bold")
ax.set_aspect("equal", adjustable="box")

plt.tight_layout()
plt.savefig(OUT_PATH, dpi=200, bbox_inches="tight")
plt.close()
print(f"Saved plot to {OUT_PATH}")

# --- Pearson correlation table ---
metrics = [
    ("protenix_iptm", "boltz2_iptm", "IPTM"),
    ("protenix_plddt", "boltz2_plddt", "pLDDT"),
    ("protenix_pae_min", "boltz2_pae_min", "pAE_min"),
    ("protenix_pae_mean", "boltz2_pae_mean", "pAE_mean"),
    ("protenix_ranking_score", "boltz2_confidence", "Ranking / Confidence"),
]

print("\n--- Pearson Correlation Summary ---")
print(f"{'Metric':<25} {'r':>8} {'p-value':>14}")
print("-" * 50)
for px, py, label in metrics:
    # Drop NaN rows
    mask = df[px].notna() & df[py].notna()
    sub = df[mask]
    if len(sub) < 2:
        print(f"{label:<25} {'N/A':>8} {'N/A':>14}")
        continue
    r, pval = stats.pearsonr(sub[px], sub[py])
    print(f"{label:<25} {r:>8.4f} {pval:>14.2e}")

# --- Also save a small CSV with correlations ---
corr_records = []
for px, py, label in metrics:
    mask = df[px].notna() & df[py].notna()
    sub = df[mask]
    if len(sub) < 2:
        corr_records.append({"Metric": label, "Pearson_r": float("NaN"), "p_value": float("NaN")})
    else:
        r, pval = stats.pearsonr(sub[px], sub[py])
        corr_records.append({"Metric": label, "Pearson_r": round(r, 4), "p_value": pval})

corr_df = pd.DataFrame(corr_records)
corr_path = os.path.join(OUT_DIR, "task2_pearson_correlations.csv")
corr_df.to_csv(corr_path, index=False)
print(f"\nSaved correlation table to {corr_path}")
