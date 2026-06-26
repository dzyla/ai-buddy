import matplotlib.pyplot as plt
import pandas as pd
import numpy as np

# Data extracted from the TIOBE Index for June 2026
data = {
    'Language': ['Python', 'C', 'C++', 'Java', 'C#'],
    'Popularity_Score': [18.96, 10.77, 8.03, 7.90, 4.85]
}

df = pd.DataFrame(data)

# Create the bar chart
plt.figure(figsize=(10, 6))
bars = plt.bar(df['Language'], df['Popularity_Score'], color='#4CAF50') # Greenish color
plt.xlabel("Programming Language")
plt.ylabel("Popularity Score (TIOBE Index, %) - Estimated")
plt.title("Top 5 Most Popular Programming Languages (TIOBE Index, June 2026)")
plt.xticks(rotation=45, ha="right")
plt.grid(axis='y', linestyle='--', alpha=0.7)

# Add the percentage score labels on top of the bars
for bar in bars:
    yval = bar.get_height()
    plt.text(bar.get_x() + bar.get_width()/2, yval + 0.2, f'{yval:.2f}%', ha='center', va='bottom')

plt.tight_layout()
plt.savefig("/tmp/langs.png")
print("Chart saved successfully to /tmp/langs.png")