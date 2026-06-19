# Protocol Notes

## APK-facing ports

The observed controller-tracking Quest APK uses NetMQ PUSH sockets. The receiver must bind matching ZMQ PULL sockets.

| Channel | Port | Payload |
| --- | ---: | --- |
| remote/controller pose | 8125 | `absolute|pos|quat|flag|point0|point1|point2` |
| gripper trigger | 8127 | APK-dependent text event for Franka gripper toggle |
| resolution | 8095 | `High`, `Low`, or `None` |
| pause/continue | 8100 | `High`, `Low`, or `None` |
| original hand keypoints | 8087 | Open-Teach hand keypoint variant, not the controller pose variant |

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

## Open-Teach compatibility

Open-Teach's original hand pipeline receives raw hand keypoints on `8087`, then publishes transformed hand frames on `8089`. This project adds a controller-pose adapter that republishes the observed `8125` stream as Open-Teach-style PUB/SUB topics.


## Simulator target stream

`quest-tracker-hub` is the preferred boundary between Quest tracking and simulator backends. It consumes the raw APK ports above, applies the selected calibration profile, and publishes a `TeleopTarget` JSON message on `tcp://127.0.0.1:8130` with topic `teleop_target`. Backends such as LIBERO should subscribe to this stream instead of binding the raw Quest ports themselves.
