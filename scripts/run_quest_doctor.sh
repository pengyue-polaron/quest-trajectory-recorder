#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ ! -x .venv/bin/python ]]; then
  echo "Run scripts/setup.sh first." >&2
  exit 1
fi
exec .venv/bin/python -m quest_trajectory_recorder.device_doctor "$@"
