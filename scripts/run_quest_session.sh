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
FOXGLOVE_URL="ws://127.0.0.1:8765"
ADB_WAIT_SECONDS=120
TASK=""
BACKEND_ARGS=()

usage() {
  cat <<'HELP'
Usage: scripts/run_quest_session.sh --backend maniskill|mujoco [options] [-- backend-options]

The session owns exactly three processes: one target source, one backend, and
one Foxglove gateway. Foxglove is the only collection UI; all internal data and
commands use the canonical embodied-ops ZMQ contracts.

Options:
  --profile NAME             Required calibration profile for a physical Quest.
  --synthetic                Use deterministic targets without a controller.
  --synthetic-pattern NAME   axes, circle, or hold (default: axes).
  --task NAME                ManiSkill task: cube_sort or bar_carry.
  --scene-seed N             ManiSkill scene seed.
  --episode-max-steps N      Optional ManiSkill timeout; zero means manual reset only.
  --record                   Start recording immediately.
  --recording-root PATH      Backend recording directory.
  --orientation              Enable controller orientation mapping.
  --max-steps N              Stop after N backend steps (useful for tests).
  --no-open-foxglove         Start the gateway without opening Foxglove Desktop.
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
  local pid
  for pid in "${CHILD_PIDS[@]:-}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
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
if [[ "$SYNTHETIC" -eq 1 ]]; then
  "$ROOT/.venv/bin/python" -m quest_trajectory_recorder.synthetic_target \
    --pattern "$SYNTHETIC_PATTERN" --amplitude-m 0.015 &
  CHILD_PIDS+=("$!")
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
  "$ROOT/scripts/run_quest_tracker_hub.sh" --profile "$PROFILE" &
  CHILD_PIDS+=("$!")
  SOURCE_LABEL="quest:$PROFILE"
fi

if [[ "$OPEN_FOXGLOVE" -eq 1 ]]; then
  "$ROOT/scripts/run_foxglove_bridge.sh" --open-foxglove &
else
  "$ROOT/scripts/run_foxglove_bridge.sh" &
fi
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

echo "Quest teleop session: source=$SOURCE_LABEL backend=$BACKEND"
echo "Foxglove: $FOXGLOVE_URL (layout: Quest Unified Teleop)"

if [[ "${#BACKEND_ARGS[@]}" -gt 0 ]]; then
  "$BACKEND_LAUNCHER" "${BACKEND_ARGS[@]}" &
else
  "$BACKEND_LAUNCHER" &
fi
BACKEND_PID="$!"
CHILD_PIDS+=("$BACKEND_PID")
if wait "$BACKEND_PID"; then
  BACKEND_STATUS=0
else
  BACKEND_STATUS="$?"
fi
exit "$BACKEND_STATUS"
