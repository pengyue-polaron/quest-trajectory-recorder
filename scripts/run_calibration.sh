#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="${1:-${CALIBRATION_PROFILE:-quest_teleop_frame}}"
CALIBRATION_PATH="$ROOT/calibrations/${PROFILE}.json"
CALIBRATION_WEB_PORT="${CALIBRATION_WEB_PORT:-8766}"
PYTHON="$ROOT/.venv/bin/python"

cd "$ROOT"
mkdir -p calibrations

if [[ ! -x "$PYTHON" ]]; then
  echo "Run $ROOT/scripts/setup.sh first." >&2
  exit 1
fi

CHILD_PIDS=()
cleanup() {
  local pid
  trap - EXIT INT TERM
  for pid in "${CHILD_PIDS[@]:-}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
  for pid in "${CHILD_PIDS[@]:-}"; do
    wait "$pid" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT INT TERM

prepare_quest_when_available() {
  if ! command -v adb >/dev/null 2>&1; then
    echo "ADB is unavailable; the calibration page will remain in offline mode." >&2
    return
  fi
  echo "Calibration page is starting now; Quest may be connected or authorized afterward."
  while true; do
    until adb get-state >/dev/null 2>&1; do
      sleep 1
    done
    if "$PYTHON" -m quest_trajectory_recorder.device_cli prepare; then
      echo "Quest attached to the live calibration page."
    else
      echo "Quest preparation failed; the calibration page remains available." >&2
    fi
    while adb get-state >/dev/null 2>&1; do
      sleep 1
    done
    echo "Quest disconnected; waiting to restore the calibration link."
  done
}

prepare_quest_when_available &
CHILD_PIDS+=("$!")

scripts/run_live3d.sh \
  --open-browser \
  --web-port "$CALIBRATION_WEB_PORT" \
  --calibration-out "$CALIBRATION_PATH" &
VIEWER_PID="$!"
CHILD_PIDS+=("$VIEWER_PID")
wait "$VIEWER_PID"
