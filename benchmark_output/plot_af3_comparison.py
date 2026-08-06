import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np

df = pd.read_csv('/home/dzyla/Code/alphafold3/af3_best10_metrics.csv')
short = df['name'].apply(lambda s: s.split('_vs_')[0] if '_vs_' in s else s)

fig, ax = plt.subplots(figsize=(18, 5.5))
x = np.arange(len(short))
w = 0.35
bars_i = ax.bar(x - w/2, df['overall_iptm'], w, label='overall_iPTM', color='#e74c3c', edgecolor='black', linewidth=0.5)
bars_p = ax.bar(x + w/2, df['overall_ptm'], w, label='overall_pTM', color='#3498db', edgecolor='black', linewidth=0.5)
for b in list(bars_i.patches) + list(bars_p.patches):
    ax.text(b.get_x() + b.get_width()/2, b.get_height() + 0.005,
            f'{b.get_height():.2f}', ha='center', va='bottom', fontsize=7)
ax.set_xticks(x)
ax.set_xticklabels(short, rotation=45, ha='right', fontsize=8.5)
ax.set_ylabel('Score')
ax.set_title('AlphaFold3 Best 10 Designs — overall_iPTM vs overall_pTM')
ax.legend(frameon=False, fontsize=11)
ax.set_ylim(0, 1.15)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
ax.yaxis.set_major_locator(plt.MultipleLocator(0.1))
plt.grid(axis='y', alpha=0.3)
plt.tight_layout()
plt.savefig('/home/dzyla/Code/ai-buddy/benchmark_output/task1_af3_comparison.png', dpi=200, facecolor='white')
print('Saved chart')
