#!/usr/bin/env python3
import time
import subprocess
import json

MODES = ['thinking', 'normal', 'low', 'instruct']

results = []

for mode in MODES:
    print(f"=== Testing Mode: {mode} ===")
    
    # Apply mode via ai-backend
    subprocess.run(['./ai-backend', 'mode', mode], capture_output=True, text=True)
    time.sleep(1)
    
    start_t = time.time()
    p = subprocess.run(
        ['./ai', '-y', 'Check the status of my Robinhood Agentic account and summarize in 2 sentences.'],
        capture_output=True,
        text=True,
        timeout=120
    )
    dur = round(time.time() - start_t, 2)
    output = p.stdout + p.stderr
    
    # Extract tok/s and tokens if present
    success = 'Agentic' in output and '150' in output
    
    results.append({
        'mode': mode,
        'duration_seconds': dur,
        'success': success,
        'output_preview': output[-300:] if len(output) > 300 else output
    })
    print(f"  Result: duration={dur}s, success={success}\n")

print("=== FINAL BENCHMARK COMPARISON ===")
for r in results:
    print(f"Mode: {r['mode']:<10} | Time: {r['duration_seconds']:>5.1f}s | Success: {r['success']}")

# Restore to best mode
subprocess.run(['./ai-backend', 'mode', 'normal'], capture_output=True, text=True)
