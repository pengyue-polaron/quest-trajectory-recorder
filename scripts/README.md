# Script map

## Primary workflow

- `run_quest_session.sh`: the only maintained ManiSkill/MuJoCo collection
  launcher. It waits for Quest ADB, owns source/backend/Foxglove, restores the
  Quest stream after reconnects, and cleans up the whole process group.
- `run_calibration.sh`: open the Quest-only calibration page and save a named
  profile before physical collection.

## Device and environment

- `setup.sh`: install the Quest tools and the canonical `embodied-ops` ZMQ
  dependency.
- `start_frankabot.sh`: wait for ADB, install/start or re-focus the recovered
  Quest APK, and configure all reverse ports.
- `run_quest_doctor.sh`: read-only ADB/APK/reverse-port/calibration checks.

## Low-level components

- `run_quest_tracker_hub.sh`: raw Quest ports to canonical ZMQ target stream.
- `run_foxglove_bridge.sh`: canonical ZMQ streams to Foxglove WebSocket.
- `run_live3d.sh`: calibration server used by `run_calibration.sh`.
- `run_libero_teleop.sh`: optional LIBERO backend; it consumes only the
  canonical target stream and never binds Quest ports.

## Raw capture and offline tools

- `record_once.sh`, `run_receiver.sh`: raw APK capture.
- `setup_libero_env.sh`: optional LIBERO environment setup.

Collection scripts removed from the maintained surface: the MJPEG dashboard,
backend Operator Panels, stdin teleop commands, and the old multi-UI stack
launcher.
