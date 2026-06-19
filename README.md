# Quest Trajectory Recorder

A lightweight receiver and visualization toolkit for Quest controller trajectory streams. The recorder listens to the Quest application's ZMQ outputs, saves pose data to CSV/JSONL, and provides offline plots and a live 3D browser view.

## Supported Quest Application

This repository is intended for the controller-tracking Quest application installed as:

- Android package: `com.Xigbee.FrankaBot`
- Application/build name: `FrankaBotControllerTracking`

The upstream Open-Teach project provides Quest APKs under `VR/APK` and refers to:

- `SingleHandArm-APK` for single-arm / hand teleoperation.
- `Bimanual-APK` for bimanual and LIBERO-style teleoperation.

Those Open-Teach APKs stream hand keypoints on the original Open-Teach ports. This repository focuses on the controller-tracking variant, whose primary trajectory stream is the `8125` controller pose stream.

## What Is Recorded

A recording captures:

- Right controller position in Quest / Unity world coordinates.
- Right controller orientation as an `xyzw` quaternion.
- Three auxiliary orientation endpoints included by the Quest app.
- Pause / recording gate state.
- Resolution state.
- Raw inbound frames for later re-parsing.

The recorder uses the host receive time (`recv_unix`, `recv_iso`). The Quest application does not include a device-side sample timestamp in the observed controller pose stream.

## Ports

| Port | Direction | Purpose |
| --- | --- | --- |
| `8125` | Quest -> host | Controller pose stream |
| `8127` | Quest -> host | Franka gripper trigger events, when emitted by the APK |
| `8095` | Quest -> host | Resolution state (`High` / `Low`) |
| `8100` | Quest -> host | Pause / recording gate state (`High` / `Low`) |
| `8087` | Quest -> host | Open-Teach hand keypoints, if using a hand-keypoint APK |
| `8089` | host -> local subscribers | Open-Teach-style transformed frame, produced by the optional bridge |
| `8093` | host -> local subscribers | Open-Teach-style resolution topic, produced by the optional bridge |
| `8102` | host -> local subscribers | Open-Teach-style pause topic, produced by the optional bridge |

## Installation

```bash
cd ~/Codespace/quest-trajectory-recorder
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

Alternatively:

```bash
cd ~/Codespace/quest-trajectory-recorder
scripts/setup.sh
source .venv/bin/activate
```

For ADB reverse mode, install Android platform tools and enable USB debugging on the Quest headset.

## Connection Modes

### ADB Reverse

This is the most reliable mode for local recording and debugging.

1. In the Quest application, set the IP address to `127.0.0.1`.
2. Connect the Quest headset over USB with debugging enabled.
3. Run the recorder with `--adb-reverse`, or use the default `scripts/record_once.sh` mode.

The helper script configures reverse forwarding for the required ports.

### LAN / Wi-Fi

Use this mode when the Quest application should send data directly over the local network.

1. Find the host machine's LAN IP:

   ```bash
   ipconfig getifaddr en0
   ```

2. Set the Quest application's IP address to that LAN IP, not `127.0.0.1`.
3. Make sure the Quest and host machine are on the same network.
4. Allow incoming Python / Terminal connections in the host firewall if prompted.

Run LAN mode with:

```bash
scripts/record_once.sh --lan
```

or:

```bash
scripts/run_live3d.sh --open-browser
```

## Recording a Take

For the default ADB reverse workflow:

```bash
cd ~/Codespace/quest-trajectory-recorder
source .venv/bin/activate
scripts/record_once.sh
```

Recommended recording sequence:

1. Open the Quest application.
2. Set the Quest application IP to `127.0.0.1` for ADB reverse mode, or to the host LAN IP for Wi-Fi mode.
3. Ensure the app is paused / red. If it is already green, press the right controller `B` button once to make it red.
4. Start the recording script.
5. Press `B` once to switch to green and begin recording.
6. Move the right controller.
7. Press `B` again to switch back to red and stop the take.

The script stops automatically after the pause state is stable and no new trajectory frames arrive.

## Live 3D Viewer

If using the recovered `com.Xigbee.FrankaBot` APK over USB/ADB, first configure
and launch the Quest side from the Mac:

```bash
cd ~/Codespace/quest-trajectory-recorder
scripts/start_frankabot.sh
```

Start the live viewer in ADB reverse mode:

```bash
cd ~/Codespace/quest-trajectory-recorder
source .venv/bin/activate
scripts/run_live3d.sh --adb-reverse --open-browser
```

Start the live viewer in LAN mode:

```bash
scripts/run_live3d.sh --open-browser
```

If the browser is not opened automatically, visit:

```text
http://127.0.0.1:8765/
```

The viewer shows:

- The live 3D trajectory.
- Start and latest points.
- Controller orientation axes derived from the quaternion.
- A browser-side teleop-frame calibration:
  - Hold the controller at your neutral pose and click `Start calibration`.
  - Move the controller 15-30 cm toward your intended right direction.
  - Click `Save right direction`.
  - Move the controller 15-30 cm toward your intended forward direction.
  - Click `Save forward direction`.
  - Quest gravity defines up; right and forward are orthogonalized from your motions.
- Current sample count and path length.
- Latest position, quaternion, stream sequence, and gate state.
- Controller status channels: B/stream pause on `8100`, resolution on `8095`,
  the controller pose `flag` field on `8125` used by the recovered APK for the
  trigger/gripper state, and `8127` gripper events if another APK emits them.

Controls:

- Drag to rotate the 3D view.
- Use the mouse wheel to zoom.
- Use `Fit View` to reset zoom.
- Use `Hide Pose Axes` / `Show Pose Axes` to toggle orientation arrows.
- Use `Clear Local View` to clear only the browser's local display.

The live viewer also writes `captures/live_*_remote.csv` unless started with `--no-record`.

## Output Files

For a session named `<session>`, outputs are written to:

```text
captures/<session>_remote.csv
captures/<session>_events.csv
captures/<session>_raw.jsonl
plots/<session>_remote.svg
plots/<session>_remote_3d.svg
```

If `sips` is available on macOS, PNG versions of the plots are also generated.

### Remote CSV

`captures/<session>_remote.csv` contains one row per accepted controller pose frame:

| Column | Description |
| --- | --- |
| `recv_unix`, `recv_iso` | Host receive time |
| `seq` | Receiver sequence number |
| `channel`, `port` | Source channel and port |
| `kind` | `absolute` or `relative` |
| `pos_x`, `pos_y`, `pos_z` | Controller position |
| `quat_x`, `quat_y`, `quat_z`, `quat_w` | Controller orientation quaternion in `xyzw` order |
| `flag` | Boolean flag included by the Quest app |
| `point0_*`, `point1_*`, `point2_*` | Auxiliary orientation endpoints |
| `raw_text` | Original text frame |

### Events CSV

`captures/<session>_events.csv` stores non-trajectory status frames, including pause and resolution state changes.

### Raw JSONL

`captures/<session>_raw.jsonl` stores every inbound frame with base64 payload and decoded text where possible. This file is useful for auditing and re-parsing captures.

## Analysis and Plotting

Analyze a recording:

```bash
quest-analyze --drop-leading-origin captures/<session>_remote.csv
```

Clean placeholder and jump artifacts:

```bash
quest-clean captures/<session>_remote.csv --max-step-m 0.20
```

Generate plots manually:

```bash
quest-plot2d captures/<session>_remote.csv --out plots/<session>_remote.svg --png
quest-plot3d captures/<session>_remote.csv --out plots/<session>_remote_3d.svg --png
```

## Receiver CLI

The one-shot script wraps the lower-level receiver. To run the receiver directly:

```bash
quest-receive \
  --host 0.0.0.0 \
  --out-dir captures \
  --session test \
  --trajectory-gate-pause High \
  --gate-requires-prior-pause Low \
  --stop-on-pause Low \
  --stop-pause-count 20 \
  --stop-no-data-sec 0.5 \
  --stop-idle-sec 2.0
```

Without installing console scripts:

```bash
PYTHONPATH=src python -m quest_trajectory_recorder.receiver --help
```

## Open-Teach Bridge

The optional bridge republishes the controller-tracking stream as Open-Teach-style PUB/SUB topics:

```bash
quest-openteach-bridge --conflate
```

It maps:

| Input | Output |
| --- | --- |
| `8125` controller pose | `8089`, topic `transformed_hand_frame` |
| `8095` resolution state | `8093`, topic `button` |
| `8100` pause state | `8102`, topic `pause` |

The bridge is intended for live Open-Teach-style consumers. For complete trajectory capture, use `scripts/record_once.sh`, `scripts/run_live3d.sh`, or `quest-receive`.

## Controller Pose Protocol

The controller-tracking Quest application sends pose frames as UTF-8 text:

```text
absolute|pos_x,pos_y,pos_z|quat_x,quat_y,quat_z,quat_w|flag|point0_x,point0_y,point0_z|point1_x,point1_y,point1_z|point2_x,point2_y,point2_z
```

Observed auxiliary endpoint mapping:

- `point1` is approximately `+local X`.
- `point2` is approximately `-local Y`.
- `point0` is approximately `-local Z`.

The viewer and Open-Teach bridge primarily use the quaternion for orientation; the auxiliary points are retained in the CSV for validation.

## References

- Open-Teach: https://github.com/aadhithya14/Open-Teach
- Open-Teach network configuration: https://github.com/aadhithya14/Open-Teach/blob/main/configs/network.yaml
- Open-Teach VR documentation: https://github.com/aadhithya14/Open-Teach/blob/main/docs/vr.md
