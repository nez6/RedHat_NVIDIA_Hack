#!/usr/bin/env bash
# Swap the vLLM server from Gemma to NVIDIA Nemotron 3.5 Lightning (30B-A3B).
# BF16 weights (~60GB) are quantized to FP8 at load time so they fit the 48GB L40S.
set -e
cd "$(dirname "$0")"
MODEL="${NEMOTRON_HF_MODEL:-nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16}"
QUANT="${NEMOTRON_QUANT:-fp8}"

echo ">> stopping any running vLLM server (e.g. gemma)…"
pkill -INT -f "vllm serve" || true
for i in $(seq 1 30); do pgrep -f "vllm serve" >/dev/null || break; sleep 1; done

echo ">> starting $MODEL (quant=$QUANT) on :8000, served as 'nemotron'"
export VLLM_USE_FLASHINFER_SAMPLER=0   # no nvcc on this box, so skip FlashInfer JIT sampler
exec env/bin/vllm serve "$MODEL" \
  --served-model-name nemotron \
  --host 0.0.0.0 --port 8000 \
  --trust-remote-code \
  --quantization "$QUANT" \
  --max-model-len 32768 \
  --max-num-seqs 32 \
  --gpu-memory-utilization 0.92 \
  --enable-prefix-caching \
  --reasoning-parser nemotron_v3 \
  --tool-call-parser qwen3_coder \
  --enable-auto-tool-choice
