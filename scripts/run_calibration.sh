#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="${1:-${CALIBRATION_PROFILE:-libero_default}}"
CALIBRATION_PATH="$ROOT/calibrations/${PROFILE}.json"

cd "$ROOT"
mkdir -p calibrations
scripts/start_frankabot.sh --no-install
exec scripts/run_live3d.sh --adb-reverse --open-browser --calibration-out "$CALIBRATION_PATH"
