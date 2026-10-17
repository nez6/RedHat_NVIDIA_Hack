#!/usr/bin/env bash
# Start the DreamHome UI + agent API on :8501
cd "$(dirname "$0")"
export NEMOTRON_BASE_URL="${NEMOTRON_BASE_URL:-http://localhost:8000/v1}"
exec env/bin/python -m uvicorn app.server:app --host 0.0.0.0 --port "${PORT:-8501}"
