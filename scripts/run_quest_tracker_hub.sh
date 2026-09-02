#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="${CALIBRATION_PROFILE:-quest_teleop_frame}"
EXTRA_ARGS=()
USER_SET_CALIBRATION=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      PROFILE="$2"
      shift 2
      ;;
    --calibration)
      USER_SET_CALIBRATION=1
      EXTRA_ARGS+=("$1" "$2")
      shift 2
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done
if [[ "$USER_SET_CALIBRATION" -eq 0 ]]; then
  PREFIX_ARGS=(--calibration "$ROOT/calibrations/${PROFILE}.json")
  if [[ "${#EXTRA_ARGS[@]}" -gt 0 ]]; then
    EXTRA_ARGS=("${PREFIX_ARGS[@]}" "${EXTRA_ARGS[@]}")
  else
    EXTRA_ARGS=("${PREFIX_ARGS[@]}")
  fi
fi

cd "$ROOT"
PYTHON="${PYTHON:-python3}"
if [[ -x .venv-libero/bin/python ]]; then
  PYTHON=.venv-libero/bin/python
elif [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
fi
COMMAND=("$PYTHON" -m quest_trajectory_recorder.quest_tracker_hub --adb-reverse)
if [[ "${#EXTRA_ARGS[@]}" -gt 0 ]]; then
  COMMAND+=("${EXTRA_ARGS[@]}")
fi
PYTHONPATH="${ROOT}/src:${PYTHONPATH:-}" exec "${COMMAND[@]}"
