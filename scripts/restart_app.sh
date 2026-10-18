#!/usr/bin/env bash
# Restart the Estate Pulse app container on the Brev box with the one canonical set of flags.
#   scripts/restart_app.sh [image-tag]      (default: newest estatepulse-app tag)
set -euo pipefail
cd "$(dirname "$0")/.."
TAG="${1:-$(docker images estatepulse-app --format '{{.Tag}}' | sort -V | tail -1)}"
mkdir -p data/cities
docker rm -f estatepulse >/dev/null 2>&1 || true
docker run -d --name estatepulse --network host --restart unless-stopped \
  --env-file "$HOME/.estatepulse.env" \
  -e NEMOTRON_BASE_URL="${NEMOTRON_BASE_URL:-http://localhost:8000/v1}" \
  -v "$PWD/data/live:/app/data/live:ro" -v "$PWD/data/zillow:/app/data/zillow" \
  -v estatepulse_intake:/app/data/intake -v "$PWD/data/cities:/app/data/cities" \
  "estatepulse-app:$TAG" >/dev/null
for i in $(seq 1 30); do curl -sf localhost:8501/api/meta >/dev/null && break; sleep 1; done
echo "estatepulse-app:$TAG up"
