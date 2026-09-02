#!/usr/bin/env bash
set -euo pipefail

PACKAGE="com.Xigbee.FrankaBot"
ACTIVITY="com.unity3d.player.UnityPlayerActivity"
APK="${APK:-${HOME}/Codespace/openteach_controller_apk/FrankaRemoteTrackingV2.apk}"
INSTALL=1
LAUNCH=1
IP="127.0.0.1"
CLOSE_PANELS=1
ADB_WAIT_SECONDS=120

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apk)
      APK="$2"
      shift 2
      ;;
    --ip)
      IP="$2"
      shift 2
      ;;
    --no-install)
      INSTALL=0
      shift
      ;;
    --no-launch)
      LAUNCH=0
      shift
      ;;
    --keep-panels)
      CLOSE_PANELS=0
      shift
      ;;
    --adb-wait-seconds)
      ADB_WAIT_SECONDS="$2"
      shift 2
      ;;
    -h|--help)
      cat <<'HELP'
Usage: scripts/start_frankabot.sh [--apk APK] [--ip 127.0.0.1] [--no-install] [--no-launch] [--keep-panels] [--adb-wait-seconds N]

Installs/configures the FrankaBotControllerTracking APK, sets Unity PlayerPrefs
IP to 127.0.0.1 for ADB reverse mode, forwards ZMQ ports, and launches the app.
It waits up to 120 seconds for a powered and authorized Quest by default.
HELP
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if ! [[ "${ADB_WAIT_SECONDS}" =~ ^[0-9]+$ ]]; then
  echo "--adb-wait-seconds must be a non-negative integer." >&2
  exit 2
fi

if ! command -v adb >/dev/null 2>&1; then
  echo "adb was not found on PATH." >&2
  exit 1
fi

adb start-server >/dev/null 2>&1 || true
WAIT_STARTED="${SECONDS}"
WAIT_ANNOUNCED=0
until adb get-state >/dev/null 2>&1; do
  if (( SECONDS - WAIT_STARTED >= ADB_WAIT_SECONDS )); then
    echo "Quest did not become available to adb within ${ADB_WAIT_SECONDS}s." >&2
    echo "Connect USB, wake the headset, and accept the USB debugging prompt." >&2
    adb devices -l >&2 || true
    exit 1
  fi
  if [[ "${WAIT_ANNOUNCED}" -eq 0 ]]; then
    echo "Waiting for Quest ADB (connect USB, wake it, and authorize debugging)..."
    WAIT_ANNOUNCED=1
  fi
  sleep 1
done
echo "Quest ADB connected: $(adb get-serialno | tr -d '\r')"

if [[ "${INSTALL}" -eq 1 ]]; then
  if [[ ! -f "${APK}" ]]; then
    echo "APK not found: ${APK}" >&2
    exit 1
  fi
  echo "Installing ${APK}..."
  adb install -r -d -t "${APK}" >/dev/null
fi

echo "Stopping other Quest teleop APKs..."
adb shell am force-stop com.NYUGRAIL.KinovaBot >/dev/null 2>&1 || true
adb shell am force-stop com.rail.oculus.teleop >/dev/null 2>&1 || true
adb shell am force-stop "${PACKAGE}" >/dev/null 2>&1 || true

echo "Setting Unity PlayerPrefs ipAddress=${IP}..."
adb shell run-as "${PACKAGE}" tee "/data/data/${PACKAGE}/shared_prefs/${PACKAGE}.v2.playerprefs.xml" >/dev/null <<XML
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <int name="Screenmanager%20Fullscreen%20mode" value="1" />
    <int name="Screenmanager%20Resolution%20Height" value="2208" />
    <int name="__UNITY_PLAYERPREFS_VERSION__" value="1" />
    <int name="Screenmanager%20Resolution%20Width" value="4128" />
    <string name="ipAddress">${IP}</string>
</map>
XML
adb shell run-as "${PACKAGE}" chmod 660 "/data/data/${PACKAGE}/shared_prefs/${PACKAGE}.v2.playerprefs.xml" >/dev/null

echo "Setting ADB reverse ports..."
for port in 8087 8095 8100 8105 8110 8125 8126 8127 15001 10505; do
  adb reverse --remove "tcp:${port}" >/dev/null 2>&1 || true
  adb reverse "tcp:${port}" "tcp:${port}" >/dev/null
done
adb reverse --list

adb shell svc power stayon true >/dev/null 2>&1 || true
adb shell am broadcast -a com.oculus.vrpowermanager.prox_close >/dev/null 2>&1 || true

if [[ "${LAUNCH}" -eq 1 ]]; then
  if [[ "${CLOSE_PANELS}" -eq 1 ]]; then
    echo "Closing Oculus launch-blocking dialogs..."
    adb shell am force-stop com.oculus.firsttimenux >/dev/null 2>&1 || true
    adb shell am force-stop com.oculus.panelapp.library >/dev/null 2>&1 || true
    adb shell am force-stop com.oculus.store >/dev/null 2>&1 || true
  fi
  echo "Launching ${PACKAGE} as a VR activity..."
  adb shell am start -S \
    -a android.intent.action.MAIN \
    -c com.oculus.intent.category.VR \
    -n "${PACKAGE}/${ACTIVITY}" \
    --es unity "-force-gles" | tr -d '\r'
fi

echo
echo "Ready. The APK should connect to this Mac through ADB reverse."
echo "If the app is red/paused, press the right controller B button once to stream."
