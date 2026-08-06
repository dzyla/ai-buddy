import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats

sns.set_style('whitegrid')
sns.set_context('notebook')

# Load data
df = pd.read_csv('/mnt/HDD1/Software/Protenix/boltz2_pyrosetta_all_shards.csv')

# Extract sample_type from sample_id (bind, nonb, extra_nonb)
def get_sample_type(sid):
    if 'extra_nonb' in sid:
        return 'nonb'
    elif 'nonb' in sid:
        return 'nonb'
    else:
        return 'bind'

df['sample_type'] = df['sample_id'].apply(get_sample_type)

print(f"Dataset shape: {df.shape}")
print(f"\nColumn info:")
print(df.dtypes)
print(f"\nMissing values per column:")
print(df.isnull().sum())

print(f"\n=== Summary Statistics ===")
print(df.describe().to_string())

# Extract binder target names for context
print(f"\n=== Unique sample_id patterns ===")
targets = df['sample_id'].apply(lambda x: x.split('__')[0]).value_counts()
print(targets)

print(f"\n=== sample_type distribution ===")
print(df['sample_type'].value_counts())

# ---- Plot 1: Histogram of boltz2_confidence_score with KDE ----
fig, ax = plt.subplots(figsize=(9, 6))
bins = 30
ax.hist(df['boltz2_confidence_score'], bins=bins, density=True, alpha=0.7,
        color='steelblue', edgecolor='black', linewidth=0.5, label='Histogram')
kde = sns.kdeplot(df['boltz2_confidence_score'], ax=ax, color='red',
                  linewidth=2, label='KDE')
# Add mean and median lines
mean_val = df['boltz2_confidence_score'].mean()
median_val = df['boltz2_confidence_score'].median()
ax.axvline(mean_val, color='orange', linestyle='--', linewidth=1.5,
           label=f'Mean: {mean_val:.3f}')
ax.axvline(median_val, color='purple', linestyle=':', linewidth=1.5,
           label=f'Median: {median_val:.3f}')
ax.set_xlabel('Boltz2 Confidence Score', fontsize=12)
ax.set_ylabel('Density', fontsize=12)
ax.set_title('Distribution of Boltz2 Confidence Score\nwith KDE Overlay', fontsize=14)
ax.legend(fontsize=10)
plt.tight_layout()
plt.savefig('/home/dzyla/Code/ai-buddy/benchmark_output/task3_confidence_score_hist.png', dpi=150)
plt.close()
print("\nSaved: task3_confidence_score_hist.png")

# ---- Plot 2: Scatter plot of rosetta_dG vs InterfaceHbonds, colored by sample_type ----
fig, ax = plt.subplots(figsize=(10, 7))
bind_data = df[df['sample_type'] == 'bind']
nonb_data = df[df['sample_type'] == 'nonb']
ax.scatter(bind_data['boltz2_rosetta_dG'], bind_data['boltz2_rosetta_n_InterfaceHbonds'],
           alpha=0.6, s=40, color='crimson', label='Bind', zorder=5)
ax.scatter(nonb_data['boltz2_rosetta_dG'], nonb_data['boltz2_rosetta_n_InterfaceHbonds'],
           alpha=0.6, s=40, color='steelblue', label='Nonb', zorder=5)

# Add regression lines for each group
if len(bind_data) > 2:
    z = np.polyfit(bind_data['boltz2_rosetta_dG'], bind_data['boltz2_rosetta_n_InterfaceHbonds'], 1)
    p = np.poly1d(z)
    x_line = np.linspace(bind_data['boltz2_rosetta_dG'].min(), bind_data['boltz2_rosetta_dG'].max(), 100)
    ax.plot(x_line, p(x_line), 'r--', alpha=0.7, linewidth=1.5, label='Bind trend')

if len(nonb_data) > 2:
    z = np.polyfit(nonb_data['boltz2_rosetta_dG'], nonb_data['boltz2_rosetta_n_InterfaceHbonds'], 1)
    p = np.poly1d(z)
    x_line = np.linspace(nonb_data['boltz2_rosetta_dG'].min(), nonb_data['boltz2_rosetta_dG'].max(), 100)
    ax.plot(x_line, p(x_line), 'b--', alpha=0.7, linewidth=1.5, label='Nonb trend')

ax.set_xlabel('Boltz2 Rosetta dG (kcal/mol)', fontsize=12)
ax.set_ylabel('Interface Hydrogen Bonds', fontsize=12)
ax.set_title('Rosetta dG vs Interface H-Bonds\nColored by Sample Type', fontsize=14)
ax.legend(fontsize=11)
plt.tight_layout()
plt.savefig('/home/dzyla/Code/ai-buddy/benchmark_output/task3_scatter_dG_vs_hbonds.png', dpi=150)
plt.close()
print("Saved: task3_scatter_dG_vs_hbonds.png")

# Correlation analysis
print("\n=== Correlation: dG vs InterfaceHbonds ===")
print(f"Overall: r={df['boltz2_rosetta_dG'].corr(df['boltz2_rosetta_n_InterfaceHbonds']):.3f}")
bind_r = bind_data['boltz2_rosetta_dG'].corr(bind_data['boltz2_rosetta_n_InterfaceHbonds'])
nonb_r = nonb_data['boltz2_rosetta_dG'].corr(nonb_data['boltz2_rosetta_n_InterfaceHbonds'])
print(f"Bind: r={bind_r:.3f}")
print(f"Nonb: r={nonb_r:.3f}")

# ---- Plot 3: Box plot of confidence_score by sample_type ----
fig, ax = plt.subplots(figsize=(7, 6))
categories = ['Bind', 'Nonb']
values = [bind_data['boltz2_confidence_score'], nonb_data['boltz2_confidence_score']]
bp = ax.boxplot(values, patch_artist=True,
                widths=0.5, medianprops=dict(color='black', linewidth=1.5))
ax.set_xticklabels(categories)
colors = ['#DC143C', '#4682B4']
for patch, color in zip(bp['boxes'], colors):
    patch.set_facecolor(color)
    patch.set_alpha(0.6)

# Add jittered points
np.random.seed(42)
for i, vals in enumerate(values):
    n = len(vals)
    x = np.random.normal(i + 1, 0.04, n)
    ax.scatter(x, vals, alpha=0.3, s=15, color='gray', edgecolors='none')

# Statistical test
t_stat, p_val = stats.ttest_ind(bind_data['boltz2_confidence_score'],
                                 nonb_data['boltz2_confidence_score'])
ax.text(0.75, 0.95, f'p = {p_val:.2e}\nmean_bind = {bind_data["boltz2_confidence_score"].mean():.3f}\nmean_nonb = {nonb_data["boltz2_confidence_score"].mean():.3f}',
        transform=ax.transAxes, fontsize=10, verticalalignment='top',
        bbox=dict(boxstyle='round,pad=0.3', facecolor='wheat', alpha=0.7))

ax.set_ylabel('Boltz2 Confidence Score', fontsize=12)
ax.set_title('Boltz2 Confidence Score by Sample Type', fontsize=14)
plt.tight_layout()
plt.savefig('/home/dzyla/Code/ai-buddy/benchmark_output/task3_boxplot_confidence_by_type.png', dpi=150)
plt.close()
print("Saved: task3_boxplot_confidence_by_type.png")

# Additional summary statistics
print(f"\n=== Confidence Score by Sample Type ===")
print(f"Bind:  mean={bind_data['boltz2_confidence_score'].mean():.3f}, "
      f"std={bind_data['boltz2_confidence_score'].std():.3f}, "
      f"median={bind_data['boltz2_confidence_score'].median():.3f}")
print(f"Nonb:  mean={nonb_data['boltz2_confidence_score'].mean():.3f}, "
      f"std={nonb_data['boltz2_confidence_score'].std():.3f}, "
      f"median={nonb_data['boltz2_confidence_score'].median():.3f}")

# Wilcoxon test
w_stat, w_pval = stats.mannwhitneyu(bind_data['boltz2_confidence_score'],
                                     nonb_data['boltz2_confidence_score'],
                                     alternative='two-sided')
print(f"Mann-Whitney U p-value: {w_pval:.2e}")

print("\n=== Rosetta dG by Sample Type ===")
print(f"Bind:  mean={bind_data['boltz2_rosetta_dG'].mean():.2f}, "
      f"std={bind_data['boltz2_rosetta_dG'].std():.2f}")
print(f"Nonb:  mean={nonb_data['boltz2_rosetta_dG'].mean():.2f}, "
      f"std={nonb_data['boltz2_rosetta_dG'].std():.2f}")

print("\n=== Interface H-Bonds by Sample Type ===")
print(f"Bind:  mean={bind_data['boltz2_rosetta_n_InterfaceHbonds'].mean():.2f}, "
      f"std={bind_data['boltz2_rosetta_n_InterfaceHbonds'].std():.2f}")
print(f"Nonb:  mean={nonb_data['boltz2_rosetta_n_InterfaceHbonds'].mean():.2f}, "
      f"std={nonb_data['boltz2_rosetta_n_InterfaceHbonds'].std():.2f}")

print("\nAnalysis complete.")
