# Script map

Run `just` from the repository root for the supported human/Agent command
surface. Scripts in this directory are the implementation layer behind those
recipes and remain directly callable for debugging.

The public Quest recipes are `just calibrate <profile>` and foreground
`just source <profile>`. Complete collection is started and supervised from the
selected backend repository with its `just teleop ...` recipe.

## Primary workflow

- `run_calibration.sh`: open the Quest-only calibration page on HTTP 8766 and
  save a named profile in user configuration storage before physical collection.

## Device and environment

- `setup.sh`: install the Quest tools and the published canonical
  `embodied-ops[teleop-zmq]` dependency. `EMBODIED_OPS_ROOT` is an optional
  editable override for package development, not a runtime prerequisite.
- `start_frankabot.sh`: wait for ADB, install/start or re-focus the recovered
  Quest APK, and configure all reverse ports.
- `run_quest_doctor.sh`: read-only ADB/APK/reverse-port/calibration checks.

Prefer `just adb-status`, `just adb-prepare`, and `just adb-focus` for recovery.
`just adb-restart` is the only routine command that intentionally restarts the
running Quest app.

## Low-level source components

- `run_quest_tracker_hub.sh`: raw Quest ports to canonical ZMQ target stream.
- `run_live3d.sh`: calibration server used by `run_calibration.sh` (HTTP 8766
  by default).

## Raw capture and offline tools

- `record_once.sh`, `run_receiver.sh`: raw APK capture.

Collection scripts removed from the maintained surface: the backend-selecting
Quest launcher, MJPEG dashboard, backend Operator Panels, stdin teleop commands,
Foxglove gateway, and old multi-UI stack launcher.
