#!/usr/bin/env bash
set -euo pipefail

PACKAGE="com.Xigbee.FrankaBot"
ACTIVITY="com.unity3d.player.UnityPlayerActivity"
APK="${APK:-${HOME}/Codespace/openteach_controller_apk/FrankaRemoteTrackingV2.apk}"
INSTALL=1
LAUNCH=1
IP="127.0.0.1"

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
    -h|--help)
      cat <<'HELP'
Usage: scripts/start_frankabot.sh [--apk APK] [--ip 127.0.0.1] [--no-install] [--no-launch]

Installs/configures the FrankaBotControllerTracking APK, sets Unity PlayerPrefs
IP to 127.0.0.1 for ADB reverse mode, forwards ZMQ ports, and launches the app.
HELP
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if ! command -v adb >/dev/null 2>&1; then
  echo "adb was not found on PATH." >&2
  exit 1
fi

if ! adb get-state >/dev/null 2>&1; then
  echo "No Quest/Android device is visible to adb." >&2
  exit 1
fi

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
  echo "Launching ${PACKAGE}..."
  adb shell am start -n "${PACKAGE}/${ACTIVITY}" --es unity "-force-gles" | tr -d '\r'
fi

echo
echo "Ready. The APK should connect to this Mac through ADB reverse."
echo "If the app is red/paused, press the right controller B button once to stream."
