#!/usr/bin/env bash
# NeMo Gym: 8 eval harnesses + eval/RL rollouts (forecast, deal) against the local vLLM Nemotron server.
# Usage: scripts/gym_eval.sh [limit]      (needs ~/nemo-gym cloned + `uv sync`ed, and vLLM on :8000)
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
GYM="${NEMO_GYM_DIR:-$HOME/nemo-gym}"
LIMIT="${1:-null}"
OUT="$REPO/results/gym"
mkdir -p "$OUT"

cd "$REPO" && env/bin/python resources_servers/estatepulse/build_datasets.py
rsync -a --delete --exclude tests --exclude .venv --exclude __pycache__ "$REPO/resources_servers/estatepulse/" "$GYM/resources_servers/estatepulse/"
cat > "$GYM/env.yaml" <<YAML
policy_base_url: ${NEMOTRON_BASE_URL:-http://localhost:8000/v1}
policy_api_key: EMPTY
policy_model_name: ${NEMOTRON_MODEL:-nemotron}
YAML

cd "$GYM" && source .venv/bin/activate
export DREAMHOME_ROOT="$REPO"
gym env start --resources-server estatepulse --model-type vllm_model > "$OUT/servers.log" 2>&1 &
SERVERS=$!
trap 'kill $SERVERS 2>/dev/null || true' EXIT
for i in $(seq 1 90); do grep -qiE "all servers (are )?(ready|healthy|up)|ready to accept" "$OUT/servers.log" && break; sleep 2; done

echo ">> eval: 8 metric harnesses"
gym eval run --no-serve --agent estatepulse_simple_agent \
  --input resources_servers/estatepulse/data/eval_metrics.jsonl --output "$OUT/eval_rollouts.jsonl" \
  --limit "$LIMIT" --num-repeats 1
echo ">> eval + RL rollouts: forecast & deal (4 samples per task for GRPO-style training)"
gym eval run --no-serve --agent estatepulse_simple_agent \
  --input resources_servers/estatepulse/data/rl_forecast_deal.jsonl --output "$OUT/rl_rollouts.jsonl" \
  --limit "$LIMIT" --num-repeats 4

cd "$REPO" && env/bin/python scripts/gym_summary.py "$OUT"
