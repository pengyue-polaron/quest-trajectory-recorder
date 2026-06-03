#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"
if [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
fi
export PYTHONPATH="${PWD}/src:${PYTHONPATH:-}"

session="${1:-record_$(date +%Y%m%d_%H%M%S)}"
capture_dir="captures"
plot_dir="plots"
remote_csv="${capture_dir}/${session}_remote.csv"

if ! command -v adb >/dev/null 2>&1; then
  echo "adb was not found on PATH. Install Android platform-tools first." >&2
  exit 1
fi

if ! adb get-state >/dev/null 2>&1; then
  echo "No Quest/Android device is visible to adb. Connect the headset and enable USB debugging first." >&2
  exit 1
fi

mkdir -p "${capture_dir}" "${plot_dir}"

echo "Session: ${session}"
echo "Setting ADB reverse ports..."
for port in 8087 8095 8100 8125 15001 10505; do
  adb reverse --remove "tcp:${port}" >/dev/null 2>&1 || true
  adb reverse "tcp:${port}" "tcp:${port}" >/dev/null
done
adb reverse --list

echo
echo "Recording is gated by pause state:"
echo "  1) Make sure the Quest app is red/paused first. If it is already green, press B once to make it red."
echo "  2) Press B to green/start, move the controller, then press B to red/stop."
"$PYTHON" -m quest_trajectory_recorder.receiver \
  --host 0.0.0.0 \
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
  "$PYTHON" -m quest_trajectory_recorder.plot2d "${remote_csv}" --out "${plot_dir}/${session}_remote.svg" "${png_flag[@]}"
  "$PYTHON" -m quest_trajectory_recorder.plot3d "${remote_csv}" --out "${plot_dir}/${session}_remote_3d.svg" "${png_flag[@]}"
else
  echo "No remote trajectory CSV was created: ${remote_csv}" >&2
  exit 2
fi
