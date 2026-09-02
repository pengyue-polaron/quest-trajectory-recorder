#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="${1:-${CALIBRATION_PROFILE:-quest_teleop_frame}}"
CALIBRATION_PATH="$ROOT/calibrations/${PROFILE}.json"
CALIBRATION_WEB_PORT="${CALIBRATION_WEB_PORT:-8766}"

cd "$ROOT"
mkdir -p calibrations
scripts/start_frankabot.sh --no-install
exec scripts/run_live3d.sh \
  --adb-reverse \
  --open-browser \
  --web-port "$CALIBRATION_WEB_PORT" \
  --calibration-out "$CALIBRATION_PATH"
