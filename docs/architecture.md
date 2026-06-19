# Repository Architecture

The repo is organized around one data path: a Quest controller-tracking APK streams pose/button frames to the Mac, and local tools either record, visualize, calibrate, or drive LIBERO from those frames.

## Runtime modules

| Module | Role |
| --- | --- |
| `receiver.py` | Low-level ZMQ receiver, text protocol parser, and CSV row writer. |
| `quest_ports.py` | Shared Quest app port constants and ADB reverse helper. |
| `live_state.py` | In-memory live stream state, SSE broadcast queues, and capture CSV schemas. |
| `calibration_profiles.py` | Named calibration profile paths, validation, and safe profile-name handling. |
| `teleop_frame.py` | Simulator-agnostic calibration math: Quest world -> `[right, forward, up]`. |
| `teleop_target.py` | Neutral `TeleopTarget` schema for calibrated pose, rotation, gripper, buttons, and stream state. |
| `quest_target_source.py` | Transport adapters: raw Quest ZMQ -> target, and target PUB/SUB subscriber. |
| `quest_tracker_hub.py` | Standalone raw Quest receiver that publishes calibrated `TeleopTarget` messages for any backend. |
| `live3d_web.py` | Browser calibration/settings UI and HTTP API for snapshots, SSE events, and profile files. |
| `live3d.py` | Thin CLI entry point that wires ZMQ sockets, live state, recording, and the web server. |
| `libero_teleop.py` | LIBERO backend that can consume direct Quest ports or the decoupled `TeleopTarget` stream. |
| `openteach_bridge.py` | Optional compatibility bridge from the controller stream to Open-Teach-style PUB topics. |
| `analyze.py`, `clean.py`, `plot2d.py`, `plot3d.py` | Offline inspection and plotting utilities for captured CSV files. |

## Calibration data model

Named profiles live under `calibrations/<profile>.json` and are intentionally ignored by git. A profile records:

- `right`, `forward`, `up`: Quest / Unity world vectors defining the teleop frame.
- `origin`: controller position that maps to the LIBERO initial EEF pose.
- `rotation.neutralQuat`: optional controller quaternion that maps to the initial LIBERO gripper orientation.
- `rotation.gripperAxis`: optional controller local axis treated as the physical gripper / approach arrow.

The browser UI saves the exact file that `scripts/run_quest_tracker_hub.sh --profile <profile>` and `scripts/run_libero_teleop.sh --profile <profile>` later consume. Avoid adding parallel calibration formats unless there is a clear migration path.

## Typical flows

### Calibrate and inspect

```text
scripts/run_calibration.sh <profile>
  -> scripts/start_frankabot.sh --no-install
  -> quest-live3d --adb-reverse --calibration-out calibrations/<profile>.json
  -> live3d.py wires ZMQ + live3d_web.py
```

### Teleoperate via decoupled target hub

```text
scripts/run_quest_tracker_hub.sh --profile <profile>
  -> raw Quest ZMQ + calibration profile
  -> publishes TeleopTarget on tcp://127.0.0.1:8130 topic teleop_target

scripts/run_libero_teleop.sh --profile <profile> --input-source target [--orientation]
  -> TeleopTargetSubscriber
  -> LIBERO-specific workspace/action mapping only
  -> drives robosuite OSC_POSE
```

Use the hub flow when adding Isaac Sim, a recorder, a policy process, or ROS2; each consumer subscribes to the same calibrated target instead of competing for the raw Quest ports.

## Extension points

- Add new simulator targets next to `libero_teleop.py`, but make them consume `TeleopTarget` from `quest_target_source.py` rather than raw Quest APK frames.
- Add new Quest APK protocol variants in `receiver.py` or a sibling parser module; do not bake parser assumptions into UI code.
- Add browser controls in `live3d_web.py` only when they change calibration/settings. Keep socket and recording logic in `live3d.py` / `live_state.py`.
- Add task-specific workspace-box calibration as a backend layer that maps calibrated `TeleopTarget.position` into simulator-safe EEF bounds, rather than replacing the base right/forward/up profile.
- Add ROS2 as an adapter around `TeleopTarget` (`PoseStamped`, `Joy`, optional `/tf`) when true robot or rosbag integration is needed; do not move the core calibration math into ROS2-only code.
