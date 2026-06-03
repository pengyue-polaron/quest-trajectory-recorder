#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"
if [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
fi
PYTHONPATH="${PWD}/src:${PYTHONPATH:-}" exec "$PYTHON" -m quest_trajectory_recorder.live3d "$@"
