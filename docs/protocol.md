# Protocol Notes

## APK-facing ports

The observed controller-tracking Quest APK uses NetMQ PUSH sockets. The receiver must bind matching ZMQ PULL sockets.

| Channel | Port | Payload |
| --- | ---: | --- |
| remote/controller pose | 8125 | `absolute|pos|quat|flag|point0|point1|point2` |
| gripper trigger | 8127 | APK-dependent text event for Franka gripper toggle |
| resolution | 8095 | `High`, `Low`, or `None` |
| pause/continue | 8100 | `High`, `Low`, or `None` |

## Controller pose frame

Example:

```text
absolute|0.1,0.9,0.2|0.99,-0.06,0.006,0.06|False|0.1,0.9,0.3|0.2,0.9,0.2|0.1,1.0,0.2
```

Parsed output columns:

- `pos_x,pos_y,pos_z`
- `quat_x,quat_y,quat_z,quat_w`
- `flag` (`True` while the recovered FrankaBot APK reports the trigger/gripper state)
- up to three auxiliary `pointN_x,pointN_y,pointN_z`

## Timing limitation

The APK frame does not include a device timestamp. The receiver records local arrival time (`recv_unix` / `recv_iso`). Arrival time can be bursty due to buffering, so it should not be used as a ground-truth motion timestamp.

## Simulator target stream

`quest-tracker-hub` is the preferred boundary between Quest tracking and
simulator backends. It consumes the raw APK ports above, applies the selected
calibration profile, and publishes `embodied.teleop_target/v1` JSON on
`tcp://127.0.0.1:8130`, topic `teleop_target`. In addition to calibrated/raw
pose and gate/gripper state, the generic fields carry source/session/frame
identity, tracking validity, and host timing. Quest-only raw pose, button,
controller, and calibration provenance live under `source_metadata`. Consumers
reject payloads that do not declare the canonical schema.

The hub also publishes `embodied.teleop_source_status/v1` on topic
`teleop_status`.
Backends send `embodied.teleop_feedback/v1` plus separate Agent/wrist JPEG parts
on ZMQ PUB endpoint `8131`. Foxglove commands use a DEALER/ROUTER connection on
`8132`, and the backend returns
`embodied.teleop_command_result/v1` only after rejecting or applying the request.
Request IDs are idempotent. Camera bytes remain outside JSON so Foxglove
does not add base64 work to the control loop. See
`docs/unified_teleop.md` for field ownership and safety behavior.
