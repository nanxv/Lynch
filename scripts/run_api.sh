#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/uvicorn src.api.app:app --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8000}" --reload
