#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"
if [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
fi
export PYTHONPATH="${PWD}/src:${PYTHONPATH:-}"

use_adb_reverse=1
listen_host="127.0.0.1"
session=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --lan|--no-adb-reverse)
      use_adb_reverse=0
      listen_host="0.0.0.0"
      shift
      ;;
    --adb-reverse)
      use_adb_reverse=1
      listen_host="127.0.0.1"
      shift
      ;;
    -h|--help)
      cat <<'HELP'
Usage: scripts/record_once.sh [--lan|--no-adb-reverse] [--adb-reverse] [session]

Modes:
  --adb-reverse       Quest app IP should be 127.0.0.1. Requires adb/USB. Default.
  --lan              Quest app IP should be this Mac's LAN IP. No adb required.
HELP
      exit 0
      ;;
    *)
      if [[ -n "${session}" ]]; then
        echo "Unexpected extra argument: $1" >&2
        exit 1
      fi
      session="$1"
      shift
      ;;
  esac
done

session="${session:-record_$(date +%Y%m%d_%H%M%S)}"
capture_dir="captures"
plot_dir="plots"
remote_csv="${capture_dir}/${session}_remote.csv"

mkdir -p "${capture_dir}" "${plot_dir}"

echo "Session: ${session}"
if [[ "${use_adb_reverse}" -eq 1 ]]; then
  if ! command -v adb >/dev/null 2>&1; then
    echo "adb was not found on PATH. Install Android platform-tools first, or use --lan." >&2
    exit 1
  fi

  if ! adb get-state >/dev/null 2>&1; then
    echo "No Quest/Android device is visible to adb. Connect the headset and enable USB debugging first, or use --lan." >&2
    exit 1
  fi

  echo "Setting ADB reverse ports..."
  for port in 8087 8095 8100 8125 15001 10505; do
    adb reverse --remove "tcp:${port}" >/dev/null 2>&1 || true
    adb reverse "tcp:${port}" "tcp:${port}" >/dev/null
  done
  adb reverse --list
  echo "Quest app IP should be: 127.0.0.1"
else
  lan_ip="$(ipconfig getifaddr en0 2>/dev/null || true)"
  if [[ -z "${lan_ip}" ]]; then
    lan_ip="$(ifconfig | awk '/inet / && $2 != "127.0.0.1" {print $2; exit}')"
  fi
  echo "LAN mode: skipping adb reverse."
  echo "Quest app IP should be this Mac's LAN IP: ${lan_ip:-<check System Settings -> Wi-Fi -> Details>}"
  echo "Make sure Quest and Mac are on the same Wi-Fi and macOS Firewall allows Python incoming connections."
fi

echo
echo "Recording is gated by pause state:"
echo "  1) Make sure the Quest app is red/paused first. If it is already green, press B once to make it red."
echo "  2) Press B to green/start, move the controller, then press B to red/stop."
"$PYTHON" -m quest_trajectory_recorder.receiver \
  --host "$listen_host" \
  --out-dir "${capture_dir}" \
  --session "${session}" \
  --keypoint-socket pull \
  --remote-socket pull \
  --heartbeat-sec 5 \
  --event-print-interval-sec 2 \
  --stop-on-pause Low \
  --stop-pause-count 20 \
  --stop-no-data-sec 0.5 \
  --stop-idle-sec 2.0 \
  --trajectory-gate-pause High \
  --gate-requires-prior-pause Low

echo
echo "Receiver stopped."
if [[ -s "${remote_csv}" ]]; then
  echo "Analyzing trajectory..."
  "$PYTHON" -m quest_trajectory_recorder.analyze --drop-leading-origin "${remote_csv}" || true

  png_flag=()
  if command -v sips >/dev/null 2>&1; then
    png_flag=(--png)
  else
    echo "sips not found; writing SVG plots only."
  fi

  echo
  echo "Rendering trajectory plots..."
  plot2d_cmd=("$PYTHON" -m quest_trajectory_recorder.plot2d "${remote_csv}" --out "${plot_dir}/${session}_remote.svg")
  plot3d_cmd=("$PYTHON" -m quest_trajectory_recorder.plot3d "${remote_csv}" --out "${plot_dir}/${session}_remote_3d.svg")
  if [[ "${#png_flag[@]}" -gt 0 ]]; then
    plot2d_cmd+=("${png_flag[@]}")
    plot3d_cmd+=("${png_flag[@]}")
  fi
  "${plot2d_cmd[@]}"
  "${plot3d_cmd[@]}"
else
  echo "No remote trajectory CSV was created: ${remote_csv}" >&2
  exit 2
fi
