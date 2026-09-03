# Script map

Run `just` from the repository root for the supported human/Agent command
surface. Scripts in this directory are the implementation layer behind those
recipes and remain directly callable for debugging.

## Primary workflow

- `run_quest_session.sh`: the only maintained ManiSkill/MuJoCo collection
  launcher. It waits for Quest ADB, owns source/backend/Foxglove, restores the
  Quest stream after reconnects, and cleans up the whole process group.
- `run_calibration.sh`: open the Quest-only calibration page on HTTP 8766 and
  save a named profile before physical collection.

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

## Low-level components

- `run_quest_tracker_hub.sh`: raw Quest ports to canonical ZMQ target stream.
- `run_foxglove_bridge.sh`: canonical ZMQ streams to Foxglove WebSocket 8765.
- `run_live3d.sh`: calibration server used by `run_calibration.sh` (HTTP 8766
  by default).

## Raw capture and offline tools

- `record_once.sh`, `run_receiver.sh`: raw APK capture.

Collection scripts removed from the maintained surface: the MJPEG dashboard,
backend Operator Panels, stdin teleop commands, and the old multi-UI stack
launcher.
