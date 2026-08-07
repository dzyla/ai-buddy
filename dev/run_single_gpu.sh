#!/bin/bash
# Run Ornith-1.0-35B on single RTX 6000 (GPU 1)
# This bypasses the 4-GPU overhead by using only the 48GB card

set -euo pipefail

# Force single GPU
export CUDA_VISIBLE_DEVICES=1

# Load environment
source ~/.local/share/ai/env

# Verify model exists
MODEL_PATH="$LLAMA_MODEL_PATH"
if [ ! -f "$MODEL_PATH" ]; then
    echo "Error: Model not found at $MODEL_PATH"
    exit 1
fi

echo "Starting llama-server on RTX 6000 (GPU 1)..."
echo "Model: $(basename $MODEL_PATH)"
echo "Context: $LLAMA_CTX_SIZE"

# Run with optimized single-GPU settings
CUDA_VISIBLE_DEVICES=1 llama-server \
    --model "$MODEL_PATH" \
    --host 127.0.0.1 \
    --port 8080 \
    --ctx-size "$LLAMA_CTX_SIZE" \
    --n-gpu-layers 99 \
    --flash-attn on \
    --batch-size 4096 \
    --ubatch-size 2048 \
    --threads 16 \
    --tensor-split 1.0 \
    --metrics
