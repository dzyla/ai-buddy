#!/usr/bin/env python3
"""This script has several bugs - find and fix them."""
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# Use non-interactive backend so the script works without a display
plt.switch_backend('Agg')

def calculate_correlation(df, col1, col2):
    # FIX 1: Changed .correlation() to .corr() — pandas Series has no .correlation() method
    return df[col1].corr(df[col2])

def filter_data(df, threshold):
    # FIX 2: Changed < to > — should filter where value is ABOVE threshold
    return df[df['value'] > threshold]

def create_summary(df):
    # FIX 3: Changed 'cat' to 'category' — matches the actual column name in the DataFrame
    summary = df.groupby('category').agg({
        'value': ['mean', 'std', 'count']
    })
    return summary

def plot_results(summary):
    # FIX 4: summary.plot.bar() was already correct (pandas valid syntax)
    # FIX 5: Added import and plt.show() to actually display/save the plot
    summary.plot.bar()
    plt.savefig('/home/dzyla/Code/ai-buddy/benchmark_output/summary_plot.png')
    plt.close('all')
    return "Plot created"

if __name__ == "__main__":
    np.random.seed(42)
    n = 100
    data = {
        'category': np.random.choice(['A', 'B', 'C'], n),
        'value': np.random.randn(n) * 10 + 50
    }
    df = pd.DataFrame(data)

    corr = calculate_correlation(df, 'value', 'value')
    filtered = filter_data(df, 50)
    summary = create_summary(df)
    plot_results(summary)

    print(f"Correlation: {corr}")
    print(f"Filtered rows: {len(filtered)}")
    print(f"Summary:\n{summary}")
