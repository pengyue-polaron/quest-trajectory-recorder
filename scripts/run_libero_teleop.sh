#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OPENPI_ROOT="${OPENPI_ROOT:-/Users/pengyue/Codespace/openpi_cam}"
EXTRA_ARGS=()
USER_SET_RIGHT_AXIS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --libero-right-axis)
      USER_SET_RIGHT_AXIS=1
      EXTRA_ARGS+=("$1" "$2")
      shift 2
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done
if [[ "$USER_SET_RIGHT_AXIS" -eq 0 ]]; then
  PREFIX_ARGS=(--libero-right-axis "${LIBERO_RIGHT_AXIS:-+y}")
  if [[ "${#EXTRA_ARGS[@]}" -gt 0 ]]; then
    EXTRA_ARGS=("${PREFIX_ARGS[@]}" "${EXTRA_ARGS[@]}")
  else
    EXTRA_ARGS=("${PREFIX_ARGS[@]}")
  fi
fi

if [[ -n "${LIBERO_PYTHON:-}" ]]; then
  PYTHON="$LIBERO_PYTHON"
elif [[ -x "$ROOT/.venv-libero/bin/python" ]]; then
  PYTHON="$ROOT/.venv-libero/bin/python"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="python3"
fi

export PYTHONPATH="$ROOT/src:${OPENPI_ROOT}/third_party/libero:${PYTHONPATH:-}"
cd "$ROOT"
COMMAND=("$PYTHON" -m quest_trajectory_recorder.libero_teleop --openpi-root "$OPENPI_ROOT")
if [[ "${#EXTRA_ARGS[@]}" -gt 0 ]]; then
  COMMAND+=("${EXTRA_ARGS[@]}")
fi
exec "${COMMAND[@]}"
