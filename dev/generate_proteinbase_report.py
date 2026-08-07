#!/usr/bin/env python3
"""Generate comprehensive HTML report for proteinbase dataset."""

import pandas as pd
import numpy as np
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.gridspec import GridSpec
import seaborn as sns
import base64
import io
from collections import Counter
from datetime import datetime

# Set style
plt.rcParams.update({
    'font.size': 10,
    'font.family': 'sans-serif',
    'figure.facecolor': '#ffffff',
    'axes.facecolor': '#ffffff',
    'axes.grid': True,
    'grid.alpha': 0.3,
    'grid.linewidth': 0.5
})

# Color palette - modern scientific
COLORS = {
    'primary': '#2E86AB',
    'secondary': '#A23B72',
    'accent1': '#F18F01',
    'accent2': '#C73E1D',
    'success': '#3B1771',
    'light': '#F5F5F5',
    'dark': '#2C3E50'
}

# Load data
print("Loading dataset...")
df = pd.read_csv('/mnt/HDD1/Software/Protenix/proteinbase_all_data_28_01_2026.csv')
print(f"Dataset loaded: {len(df)} proteins")

# Extract sequence lengths
df['seq_length'] = df['sequence'].str.len()

# Parse evaluations to extract numeric metrics
def extract_eval_metrics(eval_str):
    """Extract numeric metrics from evaluations JSON string."""
    metrics = {}
    try:
        if pd.isna(eval_str) or eval_str == '[]':
            return metrics
        
        evals = json.loads(eval_str)
        for ev in evals:
            if ev.get('valueType') == 'numeric':
                metric_name = ev['metric']
                value = ev['value']
                if metric_name not in metrics:
                    metrics[metric_name] = []
                metrics[metric_name].append(value)
    except:
        pass
    return metrics

print("Extracting evaluation metrics...")
all_metrics = {}
for idx, row in df.iterrows():
    if pd.notna(row['evaluations']) and row['evaluations'] != '[]':
        metrics = extract_eval_metrics(row['evaluations'])
        for metric, values in metrics.items():
            if metric not in all_metrics:
                all_metrics[metric] = []
            all_metrics[metric].extend(values)

# Convert to DataFrame for analysis
metrics_df = pd.DataFrame({k: pd.Series(v) for k, v in all_metrics.items() if len(v) > 0})

# Filter out NaN for statistics
numeric_cols = []
for col in metrics_df.columns:
    valid_data = metrics_df[col].dropna()
    if len(valid_data) > 0:
        numeric_cols.append(col)

metrics_df_clean = metrics_df[numeric_cols].dropna()

print(f"Found {len(numeric_cols)} numeric metrics")

# Design method distribution
design_methods = df['designMethod'].value_counts()

# Author distribution
authors = df['author'].value_counts()

# Target distribution from evaluations
def extract_targets(eval_str):
    """Extract targets from evaluations."""
    targets = []
    try:
        if pd.isna(eval_str) or eval_str == '[]':
            return targets
        evals = json.loads(eval_str)
        for ev in evals:
            if 'target' in ev and ev['target']:
                targets.append(ev['target'])
    except:
        pass
    return targets

all_targets = []
for eval_str in df['evaluations']:
    targets = extract_targets(eval_str)
    all_targets.extend(targets)

target_counts = Counter(all_targets)

print(f"Unique design methods: {len(design_methods)}")
print(f"Unique authors: {len(authors)}")
print(f"Unique targets: {len(target_counts)}")

# Create visualizations
print("Creating visualizations...")

# 1. Design Method Distribution
fig1, ax1 = plt.subplots(figsize=(10, 6))
colors1 = sns.color_palette("husl", len(design_methods))
bars = ax1.barh(range(len(design_methods)), design_methods.values, color=colors1)
ax1.set_yticks(range(len(design_methods)))
ax1.set_yticklabels(design_methods.index)
ax1.set_xlabel('Count', fontsize=11, fontweight='bold')
ax1.set_title('Design Method Distribution', fontsize=14, fontweight='bold', pad=15)
ax1.invert_yaxis()
for i, (count, bar) in enumerate(zip(design_methods.values, bars)):
    ax1.text(count + 10, i, str(count), va='center', fontsize=10)
ax1.set_xlim(0, design_methods.max() * 1.15)
plt.tight_layout()

# Save and convert to base64
def plot_to_base64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight', 
                facecolor='white', edgecolor='none')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.getvalue()).decode('utf-8')

img1_base64 = plot_to_base64(fig1)

# 2. Author Distribution (Top 15)
fig2, ax2 = plt.subplots(figsize=(12, 7))
top_authors = authors.head(15)
colors2 = sns.color_palette("viridis", len(top_authors))
bars2 = ax2.barh(range(len(top_authors)), top_authors.values, color=colors2)
ax2.set_yticks(range(len(top_authors)))
ax2.set_yticklabels(top_authors.index)
ax2.set_xlabel('Protein Count', fontsize=11, fontweight='bold')
ax2.set_title('Top 15 Authors by Protein Count', fontsize=14, fontweight='bold', pad=15)
ax2.invert_yaxis()
for i, (count, bar) in enumerate(zip(top_authors.values, bars2)):
    ax2.text(count + 5, i, str(count), va='center', fontsize=10)
ax2.set_xlim(0, top_authors.max() * 1.15)
plt.tight_layout()

img2_base64 = plot_to_base64(fig2)

# 3. Sequence Length Distribution
fig3, ax3 = plt.subplots(figsize=(10, 6))
ax3.hist(df['seq_length'], bins=50, color=COLORS['primary'], 
         edgecolor='white', linewidth=0.5, alpha=0.85)
ax3.set_xlabel('Sequence Length (amino acids)', fontsize=11, fontweight='bold')
ax3.set_ylabel('Frequency', fontsize=11, fontweight='bold')
ax3.set_title('Sequence Length Distribution', fontsize=14, fontweight='bold', pad=15)
mean_len = df['seq_length'].mean()
median_len = df['seq_length'].median()
ax3.axvline(mean_len, color=COLORS['secondary'], linestyle='--', linewidth=2, 
            label=f'Mean: {mean_len:.0f} aa')
ax3.axvline(median_len, color=COLORS['accent1'], linestyle=':', linewidth=2, 
            label=f'Median: {median_len:.0f} aa')
ax3.legend(fontsize=10, loc='upper right')
plt.tight_layout()

img3_base64 = plot_to_base64(fig3)

# 4. Target Distribution (Top 20)
fig4, ax4 = plt.subplots(figsize=(14, 8))
top_targets = target_counts.most_common(20)
target_names = [t[0] for t in top_targets]
target_counts_vals = [t[1] for t in top_targets]
colors4 = sns.color_palette("Set2", len(target_names))
bars4 = ax4.barh(range(len(target_names)), target_counts_vals, color=colors4)
ax4.set_yticks(range(len(target_names)))
ax4.set_yticklabels(target_names, fontsize=9)
ax4.set_xlabel('Total Measurements', fontsize=11, fontweight='bold')
ax4.set_title('Top 20 Targets by Measurement Count', fontsize=14, fontweight='bold', pad=15)
ax4.invert_yaxis()
for i, (count, bar) in enumerate(zip(target_counts_vals, bars4)):
    ax4.text(count + 2, i, str(count), va='center', fontsize=9)
ax4.set_xlim(0, max(target_counts_vals) * 1.15)
plt.tight_layout()

img4_base64 = plot_to_base64(fig4)

# Generate HTML report
print("Generating HTML report...")

# Statistical summary
stats_summary = []
for col in numeric_cols:
    valid_data = metrics_df[col].dropna()
    if len(valid_data) > 0:
        stats_summary.append({
            'metric': col,
            'count': len(valid_data),
            'mean': valid_data.mean(),
            'std': valid_data.std(),
            'min': valid_data.min(),
            'median': valid_data.median(),
            'max': valid_data.max()
        })

stats_df = pd.DataFrame(stats_summary)

# Build statistics table HTML
stats_table_html = ""
if len(stats_df) > 0:
    stats_table_html = stats_df.to_html(index=False, float_format="%.4f", classes='stats-table')
else:
    stats_table_html = "<p>No numeric metrics extracted from evaluations.</p>"

# Design methods summary
design_methods_html = ""
if len(design_methods) > 0:
    design_methods_html = "<table class='summary-table'><tr><th>Design Method</th><th>Count</th><th>Percentage</th></tr>"
    total_design = design_methods.sum()
    for method, count in design_methods.items():
        pct = (count / total_design) * 100
        design_methods_html += f"<tr><td>{method}</td><td>{count}</td><td>{pct:.1f}%</td></tr>"
    design_methods_html += "</table>"

# Author summary
author_html = ""
if len(authors) > 0:
    author_html = f"<p>The dataset includes contributions from <strong>{len(authors)}</strong> unique authors/research groups.</p>"
    author_html += "<table class='summary-table'><tr><th>Author</th><th>Proteins</th></tr>"
    for author, count in authors.head(10).items():
        author_html += f"<tr><td>{author}</td><td>{count}</td></tr>"
    author_html += "</table>"

# Target summary
target_html = ""
if len(target_counts) > 0:
    top_target_names = [t[0] for t in target_counts.most_common(10)]
    target_html = f"<p>The dataset includes measurements against <strong>{len(target_counts)}</strong> unique targets. Top targets include:</p>"
    target_html += f"<p>{', '.join(top_target_names)}...</p>"

# Sequence length stats
seq_stats = df['seq_length'].describe()
seq_html = f"""
<table class='summary-table'>
<tr><th>Metric</th><th>Value</th></tr>
<tr><td>Mean Length</td><td>{seq_stats['mean']:.1f} amino acids</td></tr>
<tr><td>Median Length</td><td>{seq_stats['50%']:.1f} amino acids</td></tr>
<tr><td>Std Dev</td><td>{seq_stats['std']:.1f} amino acids</td></tr>
<tr><td>Min Length</td><td>{seq_stats['min']:.0f} amino acids</td></tr>
<tr><td>Max Length</td><td>{seq_stats['max']:.0f} amino acids</td></tr>
</table>
"""

# Generate HTML
html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ProteinBase Dataset Report</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }}
        
        header {{
            background: linear-gradient(135deg, #2E86AB 0%, #A23B72 100%);
            color: white;
            padding: 40px;
            text-align: center;
        }}
        
        h1 {{
            font-size: 2.5em;
            margin-bottom: 10px;
            font-weight: 700;
        }}
        
        .subtitle {{
            font-size: 1.1em;
            opacity: 0.9;
            margin-top: 10px;
        }}
        
        .content {{
            padding: 40px;
        }}
        
        .section {{
            margin-bottom: 50px;
        }}
        
        h2 {{
            color: #2E86AB;
            font-size: 1.8em;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 3px solid #2E86AB;
        }}
        
        h3 {{
            color: #A23B72;
            font-size: 1.3em;
            margin: 25px 0 15px 0;
        }}
        
        .summary-box {{
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            padding: 25px;
            border-radius: 8px;
            border-left: 5px solid #2E86AB;
            margin-bottom: 20px;
        }}
        
        .summary-box h3 {{
            margin-top: 0;
            color: #2C3E50;
        }}
        
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin: 20px 0;
        }}
        
        .stat-card {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            text-align: center;
            border-top: 4px solid #2E86AB;
        }}
        
        .stat-card .number {{
            font-size: 2em;
            font-weight: bold;
            color: #2E86AB;
        }}
        
        .stat-card .label {{
            color: #666;
            font-size: 0.9em;
            margin-top: 5px;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
            background: white;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        
        .stats-table th,
        .summary-table th {{
            background: linear-gradient(135deg, #2E86AB 0%, #A23B72 100%);
            color: white;
            padding: 12px;
            text-align: left;
            font-weight: 600;
        }}
        
        .stats-table td,
        .summary-table td {{
            padding: 10px 12px;
            border-bottom: 1px solid #e0e0e0;
        }}
        
        .stats-table tr:nth-child(even),
        .summary-table tr:nth-child(even) {{
            background: #f8f9fa;
        }}
        
        .stats-table tr:hover,
        .summary-table tr:hover {{
            background: #e8f4f8;
        }}
        
        .figure-container {{
            margin: 30px 0;
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        
        .figure-container img {{
            width: 100%;
            height: auto;
            border-radius: 4px;
        }}
        
        .figure-caption {{
            text-align: center;
            color: #666;
            font-size: 0.9em;
            margin-top: 10px;
            font-style: italic;
        }}
        
        .insights-list {{
            background: #f8f9fa;
            padding: 25px;
            border-radius: 8px;
            border-left: 5px solid #A23B72;
        }}
        
        .insights-list li {{
            margin-bottom: 12px;
            padding-left: 10px;
        }}
        
        .insights-list li strong {{
            color: #2E86AB;
        }}
        
        footer {{
            background: #2C3E50;
            color: white;
            text-align: center;
            padding: 20px;
            font-size: 0.9em;
        }}
        
        .highlight {{
            background: #fff3cd;
            padding: 2px 6px;
            border-radius: 3px;
            font-weight: 600;
        }}
        
        @media print {{
            body {{
                background: white;
                padding: 0;
            }}
            .container {{
                box-shadow: none;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>ProteinBase Dataset Report</h1>
            <div class="subtitle">Comprehensive Analysis of 5,253 Designed Proteins</div>
            <div class="subtitle">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
        </header>
        
        <div class="content">
            <!-- Executive Summary -->
            <div class="section">
                <h2>Executive Summary</h2>
                <div class="summary-box">
                    <p>The ProteinBase dataset contains <span class="highlight">5,253 proteins</span> curated from experimental and computational studies. This comprehensive analysis reveals key characteristics including sequence diversity, design methodologies, and evaluation metrics across the dataset.</p>
                </div>
                
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="number">{len(df)}</div>
                        <div class="label">Total Proteins</div>
                    </div>
                    <div class="stat-card">
                        <div class="number">{len(authors)}</div>
                        <div class="label">Unique Authors</div>
                    </div>
                    <div class="stat-card">
                        <div class="number">{len(design_methods)}</div>
                        <div class="label">Design Methods</div>
                    </div>
                    <div class="stat-card">
                        <div class="number">{len(numeric_cols)}</div>
                        <div class="label">Numeric Metrics</div>
                    </div>
                </div>
            </div>
            
            <!-- Dataset Overview -->
            <div class="section">
                <h2>Dataset Overview</h2>
                
                <h3>Dataset Statistics</h3>
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="number">{len(df.columns)}</div>
                        <div class="label">Columns</div>
                    </div>
                    <div class="stat-card">
                        <div class="number">{len(df)}</div>
                        <div class="label">Proteins</div>
                    </div>
                    <div class="stat-card">
                        <div class="number">{seq_stats['mean']:.0f}</div>
                        <div class="label">Avg Sequence Length (aa)</div>
                    </div>
                    <div class="stat-card">
                        <div class="number">{len(target_counts)}</div>
                        <div class="label">Unique Targets</div>
                    </div>
                </div>
                
                <h3>Design Methods Distribution</h3>
                {design_methods_html}
                
                <h3>Contributing Authors</h3>
                {author_html}
                
                <h3>Target Coverage</h3>
                {target_html}
                
                <h3>Sequence Length Statistics</h3>
                {seq_html}
            </div>
            
            <!-- Statistical Summary -->
            <div class="section">
                <h2>Statistical Summary of Evaluation Metrics</h2>
                <p>Comprehensive statistics for all numeric evaluation metrics extracted from the protein datasets:</p>
                {stats_table_html}
            </div>
            
            <!-- Figures -->
            <div class="section">
                <h2>Visual Analysis</h2>
                
                <div class="figure-container">
                    <img src="data:image/png;base64,{img1_base64}" alt="Design Method Distribution">
                    <div class="figure-caption">Figure 1: Distribution of proteins across different design methods</div>
                </div>
                
                <div class="figure-container">
                    <img src="data:image/png;base64,{img2_base64}" alt="Author Distribution">
                    <div class="figure-caption">Figure 2: Top 15 authors by number of proteins contributed</div>
                </div>
                
                <div class="figure-container">
                    <img src="data:image/png;base64,{img3_base64}" alt="Sequence Length Distribution">
                    <div class="figure-caption">Figure 3: Distribution of protein sequence lengths with mean and median indicators</div>
                </div>
                
                <div class="figure-container">
                    <img src="data:image/png;base64,{img4_base64}" alt="Target Distribution">
                    <div class="figure-caption">Figure 4: Top 20 targets by number of experimental measurements</div>
                </div>
            </div>
            
            <!-- Key Insights -->
            <div class="section">
                <h2>Key Insights</h2>
                <div class="insights-list">
                    <ul>
                        <li><strong>Dataset Scale:</strong> The ProteinBase dataset represents a substantial collection of {len(df):,} proteins, demonstrating the scale of modern protein engineering efforts.</li>
                        
                        <li><strong>Sequence Diversity:</strong> With a mean sequence length of {seq_stats['mean']:.0f} amino acids (median: {seq_stats['50%']:.0f} aa), the dataset captures proteins across a wide size range from {seq_stats['min']:.0f} to {seq_stats['max']:.0f} amino acids.</li>
                        
                        <li><strong>Multi-institutional Collaboration:</strong> Contributions from {len(authors)} unique research groups indicate broad scientific interest and collaborative efforts in protein design.</li>
                        
                        <li><strong>Target Diversity:</strong> Measurements span {len(target_counts)} different biological targets, with {target_counts.most_common(1)[0][0]} being the most studied target with {target_counts.most_common(1)[0][1]:,} measurements.</li>
                        
                        <li><strong>Computational Metrics:</strong> The dataset includes {len(numeric_cols)} distinct numeric evaluation metrics, providing comprehensive characterization of designed proteins through structural, energetic, and functional assessments.</li>
                        
                        <li><strong>Experimental Validation:</strong> Rich experimental data including binding kinetics (kon, koff, KD), expression rates, and binding strength measurements enable thorough evaluation of design outcomes.</li>
                        
                        <li><strong>Structure Prediction:</strong> Multiple computational structure prediction methods are represented, including ESMFold and AlphaFold-based approaches, providing confidence metrics (pLDDT) for predicted structures.</li>
                    </ul>
                </div>
            </div>
        </div>
        
        <footer>
            <p>ProteinBase Dataset Report | Generated on {datetime.now().strftime('%B %d, %Y at %H:%M:%S')}</p>
            <p>Data source: proteinbase_all_data_28_01_2026.csv</p>
        </footer>
    </div>
</body>
</html>
"""

# Write HTML report
output_path = '/home/dzyla/Code/ai-buddy/benchmark_output/task4_proteinbase_report.html'
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(html)

print(f"\nReport generated successfully: {output_path}")
print(f"Report size: {len(html):,} bytes")

# Print some summary stats
print(f"\n--- Dataset Summary ---")
print(f"Total proteins: {len(df)}")
print(f"Columns: {list(df.columns)}")
print(f"Design methods: {dict(design_methods)}")
print(f"Top authors: {dict(authors.head(5))}")
print(f"Unique targets: {len(target_counts)}")
print(f"Numeric metrics found: {len(numeric_cols)}")
print(f"Sequence length - Mean: {seq_stats['mean']:.1f}, Median: {seq_stats['50%']:.1f}, Std: {seq_stats['std']:.1f}")
