#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="${1:-${CALIBRATION_PROFILE:-quest_teleop_frame}}"
CALIBRATION_WEB_PORT="${CALIBRATION_WEB_PORT:-8766}"
PYTHON="$ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "Run $ROOT/scripts/setup.sh first." >&2
  exit 1
fi

exec "$PYTHON" -m quest_trajectory_recorder.calibration_runtime \
  --profile "$PROFILE" \
  --web-port "$CALIBRATION_WEB_PORT"
