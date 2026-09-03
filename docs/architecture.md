# Repository architecture

This repository is the Quest input-device and session-composition layer. It
does not implement ManiSkill or MuJoCo control policy.

## Maintained runtime

```text
raw Quest APK ports
  -> calibration browser on HTTP 8766 (pre-collection only)
  -> Quest parser + named calibration
  -> embodied.teleop_target/v1 on ZMQ 8130
  -> shared clutch/guard + backend-owned native action/camera/task record fields
  -> embodied.teleop_feedback/v1 on ZMQ 8131
  -> Foxglove gateway on WebSocket 8765

Foxglove service
  -> embodied.teleop_command/v1 on ZMQ 8132
  -> backend validation/application
  -> embodied.teleop_command_result/v1
  -> Foxglove service response
```

The calibration browser is source-owned, listens on HTTP 8766 by default, and
exists only before collection. Port 8765 is reserved for Foxglove WebSocket.
Foxglove is the sole collection UI. The removed Dashboard and Quest Operator
Panels are not alternate production paths.

## Module ownership

| Module | Role |
| --- | --- |
| `receiver.py` | Raw APK text parser and raw CSV capture. |
| `quest_ports.py`, `device_cli.py` | Quest port constants plus explicit, scriptable ADB status, reverse-port, focus, and restart operations. |
| `calibration_profiles.py`, `teleop_frame.py` | Named profile validation and Quest-to-teleop geometry. |
| `teleop_target.py` | Raw Quest frame to canonical `TeleopTarget`. |
| `quest_target_source.py` | Raw Quest socket ownership and controller gate/gripper state. |
| `quest_tracker_hub.py` | Canonical target/status ZMQ publisher. |
| `synthetic_target.py` | Deterministic canonical source for tests. |
| `device_doctor.py` | Read-only ADB/APK/port/calibration readiness. |
| `foxglove_bridge.py` | Canonical ZMQ observer/command client mapped to Foxglove images, native diagnostics, poses, and services. |
| `foxglove/quest-teleop-controls` | Compact React panel that calls the acknowledged Foxglove services. |
| `foxglove_publish.py` | CI publisher for the versioned `.foxe` and organization layout. |
| `live3d.py`, `live3d_web.py` | Quest-only calibration UI and profile writer. |

Shared schemas, geometry, transport, source status, commands, recording
primitives, and Cartesian clutch mapping are imported directly from
`embodied_ops.teleop`. This repository
contains no compatibility re-export and does not accept Quest-prefixed target
schemas.

## Repository dependency direction

```text
embodied-ops canonical contracts + ZMQ
  <- Quest source/session composition
  <- RobotTeamBench ManiSkill backend
  <- ForceVLA MuJoCo backend
```

Neither backend imports this repository. The source owns APK parsing and
calibration. The shared package owns clutch/freshness/workspace mechanisms,
recording schemas, and command vocabulary; each backend chooses the safety
thresholds and workspace policy and owns task resets, native actions, cameras,
task diagnostics, and only acknowledges a command after its effect is complete.

## Primary commands

```bash
just calibrate <profile>
just maniskill <profile> cube_sort --record
just forcevla <profile> --record
```

`run_quest_session.sh` is the composition root. It starts one target source,
one backend, and one Foxglove gateway, and terminates the complete child set on
exit. Low-level component scripts remain for diagnosis and focused tests; see
`scripts/README.md`.

ADB lifecycle is asymmetric by design: background health monitoring may repair
reverse ports and publish state, but only an explicit operator/Agent command
may focus or restart the Quest activity. This prevents a transient ADB probe or
Meta focus change from resetting the controller stream mid-motion.

## Extension rules

- New input devices publish the canonical `TeleopTarget`; they do not add
  source-specific fields to the top-level schema.
- New backends import `embodied_ops.teleop` directly and never bind Quest raw
  ports.
- New collection controls become idempotent ZMQ commands acknowledged by the
  backend before Foxglove reports success.
- Device calibration stays with the source; task/workspace calibration stays
  with the backend.
- A second UI or second control transport requires an explicit operational
  need; it is not added as a convenience fallback.
