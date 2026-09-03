#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ROBOTTEAMBENCH_ROOT="${ROBOTTEAMBENCH_ROOT:-$ROOT/../RobotTeamBench}"
FORCEVLA_ROOT="${FORCEVLA_ROOT:-$ROOT/../forceVLA-mujoco}"

BACKEND=""
PROFILE="${CALIBRATION_PROFILE:-quest_teleop_frame}"
SYNTHETIC=0
SYNTHETIC_PATTERN="axes"
OPEN_FOXGLOVE=1
TARGET_ENDPOINT="tcp://127.0.0.1:8130"
FEEDBACK_ENDPOINT="tcp://127.0.0.1:8131"
COMMAND_ENDPOINT="tcp://127.0.0.1:8132"
FOXGLOVE_PORT=8765
FOXGLOVE_URL=""
ADB_WAIT_SECONDS=120
TASK=""
BACKEND_ARGS=()

usage() {
  cat <<'HELP'
Usage: scripts/run_quest_session.sh --backend maniskill|mujoco [options] [-- backend-options]

The session owns exactly three top-level services: one target source, one
backend, and one Foxglove gateway. Foxglove is the only collection UI; all
internal data and commands use the canonical embodied-ops ZMQ contracts.

Options:
  --profile NAME             Required calibration profile for a physical Quest.
  --synthetic                Use deterministic targets without a controller.
  --synthetic-pattern NAME   axes, circle, or hold (default: axes).
  --task NAME                ManiSkill task: cube_sort or bar_carry.
  --scene-seed N             Deterministic backend scene seed.
  --episode-max-steps N      Optional backend timeout; zero means manual reset only.
  --record                   Start recording immediately.
  --recording-root PATH      Backend recording directory.
  --orientation              Enable controller orientation mapping.
  --max-steps N              Stop after N backend steps (useful for tests).
  --no-open-foxglove         Start the gateway without opening Foxglove Desktop.
  --target-endpoint URL      Canonical target ZMQ endpoint.
  --feedback-endpoint URL    Canonical feedback ZMQ endpoint.
  --command-endpoint URL     Canonical command ZMQ endpoint.
  --foxglove-port N          Foxglove WebSocket port (default: 8765).
  --adb-wait-seconds N       Wait this long for Quest USB/ADB (default: 120).
  --                         Pass remaining arguments directly to the backend.

Examples:
  scripts/run_quest_session.sh --backend maniskill --profile desk --task cube_sort --record
  scripts/run_quest_session.sh --backend mujoco --profile desk --record --orientation
  scripts/run_quest_session.sh --backend maniskill --synthetic --max-steps 80
HELP
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend)
      BACKEND="$2"
      shift 2
      ;;
    --profile)
      PROFILE="${2%.json}"
      shift 2
      ;;
    --synthetic)
      SYNTHETIC=1
      shift
      ;;
    --synthetic-pattern)
      SYNTHETIC_PATTERN="$2"
      shift 2
      ;;
    --task)
      TASK="$2"
      BACKEND_ARGS+=("$1" "$2")
      shift 2
      ;;
    --scene-seed|--recording-root|--max-steps|--episode-max-steps)
      BACKEND_ARGS+=("$1" "$2")
      shift 2
      ;;
    --record|--orientation)
      BACKEND_ARGS+=("$1")
      shift
      ;;
    --no-open-foxglove)
      OPEN_FOXGLOVE=0
      shift
      ;;
    --adb-wait-seconds)
      ADB_WAIT_SECONDS="$2"
      shift 2
      ;;
    --target-endpoint)
      TARGET_ENDPOINT="$2"
      shift 2
      ;;
    --feedback-endpoint)
      FEEDBACK_ENDPOINT="$2"
      shift 2
      ;;
    --command-endpoint)
      COMMAND_ENDPOINT="$2"
      shift 2
      ;;
    --foxglove-port)
      FOXGLOVE_PORT="$2"
      shift 2
      ;;
    --)
      shift
      BACKEND_ARGS+=("$@")
      break
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown session option: $1 (put backend-specific options after --)" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$BACKEND" != "maniskill" && "$BACKEND" != "mujoco" ]]; then
  echo "--backend must be maniskill or mujoco" >&2
  exit 2
fi
if [[ ! "$FOXGLOVE_PORT" =~ ^[0-9]+$ ]] || ((FOXGLOVE_PORT < 1 || FOXGLOVE_PORT > 65535)); then
  echo "--foxglove-port must be an integer from 1 to 65535" >&2
  exit 2
fi
if [[ ! "$ADB_WAIT_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "--adb-wait-seconds must be a non-negative integer" >&2
  exit 2
fi
if [[ "$BACKEND" == "mujoco" && -n "$TASK" ]]; then
  echo "--task is only valid for the ManiSkill backend" >&2
  exit 2
fi
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  echo "Run $ROOT/scripts/setup.sh first." >&2
  exit 1
fi

if [[ "$BACKEND" == "maniskill" ]]; then
  BACKEND_LAUNCHER="$ROBOTTEAMBENCH_ROOT/scripts/run_maniskill_quest_teleop.sh"
else
  BACKEND_LAUNCHER="$FORCEVLA_ROOT/scripts/run_mujoco_quest_teleop.sh"
fi
if [[ ! -x "$BACKEND_LAUNCHER" ]]; then
  echo "Backend launcher is missing or not executable: $BACKEND_LAUNCHER" >&2
  exit 1
fi

CHILD_PIDS=()
cleanup() {
  local pid attempt
  for pid in "${CHILD_PIDS[@]:-}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
  for attempt in {1..25}; do
    local any_alive=0
    for pid in "${CHILD_PIDS[@]:-}"; do
      if kill -0 "$pid" >/dev/null 2>&1; then
        any_alive=1
      fi
    done
    [[ "$any_alive" -eq 0 ]] && break
    sleep 0.2
  done
  for pid in "${CHILD_PIDS[@]:-}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill -KILL "$pid" >/dev/null 2>&1 || true
    fi
  done
  for pid in "${CHILD_PIDS[@]:-}"; do
    wait "$pid" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$ROOT"
FOXGLOVE_URL="ws://127.0.0.1:${FOXGLOVE_PORT}"
if [[ "$SYNTHETIC" -eq 1 ]]; then
  "$ROOT/.venv/bin/python" -m quest_trajectory_recorder.synthetic_target \
    --bind "$TARGET_ENDPOINT" \
    --pattern "$SYNTHETIC_PATTERN" --amplitude-m 0.015 &
  SOURCE_PID="$!"
  CHILD_PIDS+=("$SOURCE_PID")
  SOURCE_LABEL="synthetic:$SYNTHETIC_PATTERN"
else
  CALIBRATION_PATH="$ROOT/calibrations/$PROFILE.json"
  if [[ ! -f "$CALIBRATION_PATH" ]]; then
    echo "Calibration profile is missing: $CALIBRATION_PATH" >&2
    echo "Create it with scripts/run_calibration.sh $PROFILE" >&2
    exit 1
  fi
  "$ROOT/scripts/start_frankabot.sh" --no-install \
    --adb-wait-seconds "$ADB_WAIT_SECONDS"
  "$ROOT/scripts/run_quest_doctor.sh" --calibration "$CALIBRATION_PATH"
  "$ROOT/scripts/run_quest_tracker_hub.sh" --profile "$PROFILE" \
    --target-bind "$TARGET_ENDPOINT" &
  SOURCE_PID="$!"
  CHILD_PIDS+=("$SOURCE_PID")
  SOURCE_LABEL="quest:$PROFILE"
fi

FOXGLOVE_ARGS=(
  --target-endpoint "$TARGET_ENDPOINT"
  --feedback-endpoint "$FEEDBACK_ENDPOINT"
  --command-endpoint "$COMMAND_ENDPOINT"
  --port "$FOXGLOVE_PORT"
)
if [[ "$OPEN_FOXGLOVE" -eq 1 ]]; then
  FOXGLOVE_ARGS+=(--open-foxglove)
fi
"$ROOT/scripts/run_foxglove_bridge.sh" "${FOXGLOVE_ARGS[@]}" &
FOXGLOVE_PID="$!"
CHILD_PIDS+=("$FOXGLOVE_PID")

FOXGLOVE_READY=0
for _attempt in {1..50}; do
  if ! kill -0 "$FOXGLOVE_PID" >/dev/null 2>&1; then
    echo "Foxglove gateway exited during startup." >&2
    exit 1
  fi
  if "$ROOT/.venv/bin/python" -m quest_trajectory_recorder.foxglove_probe \
    --url "$FOXGLOVE_URL" --timeout-sec 0.2 >/dev/null 2>&1; then
    if kill -0 "$FOXGLOVE_PID" >/dev/null 2>&1; then
      FOXGLOVE_READY=1
      break
    fi
  fi
  sleep 0.1
done
if [[ "$FOXGLOVE_READY" -ne 1 ]]; then
  if ! kill -0 "$FOXGLOVE_PID" >/dev/null 2>&1; then
    echo "Foxglove gateway exited during startup." >&2
  else
    echo "Foxglove gateway did not complete a protocol handshake on $FOXGLOVE_URL." >&2
  fi
  exit 1
fi
if ! kill -0 "$SOURCE_PID" >/dev/null 2>&1; then
  wait "$SOURCE_PID" || SOURCE_STATUS="$?"
  echo "Target source exited during startup." >&2
  exit "${SOURCE_STATUS:-1}"
fi

echo "Quest teleop session: source=$SOURCE_LABEL backend=$BACKEND"
echo "Foxglove: $FOXGLOVE_URL (layout: Quest Unified Teleop)"

BACKEND_ARGS+=(
  --target-endpoint "$TARGET_ENDPOINT"
  --feedback-endpoint "$FEEDBACK_ENDPOINT"
  --command-endpoint "$COMMAND_ENDPOINT"
)
"$BACKEND_LAUNCHER" "${BACKEND_ARGS[@]}" &
BACKEND_PID="$!"
CHILD_PIDS+=("$BACKEND_PID")

while true; do
  if ! kill -0 "$SOURCE_PID" >/dev/null 2>&1; then
    wait "$SOURCE_PID" || SOURCE_STATUS="$?"
    SOURCE_STATUS="${SOURCE_STATUS:-0}"
    echo "Target source exited; stopping the complete session." >&2
    [[ "$SOURCE_STATUS" -eq 0 ]] && SOURCE_STATUS=1
    exit "$SOURCE_STATUS"
  fi
  if ! kill -0 "$FOXGLOVE_PID" >/dev/null 2>&1; then
    wait "$FOXGLOVE_PID" || FOXGLOVE_STATUS="$?"
    FOXGLOVE_STATUS="${FOXGLOVE_STATUS:-0}"
    echo "Foxglove gateway exited; stopping the complete session." >&2
    [[ "$FOXGLOVE_STATUS" -eq 0 ]] && FOXGLOVE_STATUS=1
    exit "$FOXGLOVE_STATUS"
  fi
  if ! kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
    wait "$BACKEND_PID" || BACKEND_STATUS="$?"
    exit "${BACKEND_STATUS:-0}"
  fi
  sleep 0.2
done
