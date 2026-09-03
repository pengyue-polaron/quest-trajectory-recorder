# Quest Trajectory Recorder

A lightweight Quest controller tracking toolkit for calibration and input-device
adaptation. The preferred runtime path publishes the source-neutral
`embodied.teleop_target/v1` contract from `embodied-ops`; ManiSkill, MuJoCo,
LIBERO, or another backend subscribes without importing this repository. The
read-only observation gateway shows the backend's Agent view and wrist camera.

The codebase is split into small runtime modules: `receiver.py` parses Quest
frames, `teleop_frame.py` applies saved calibration profiles,
`teleop_target.py` adapts Quest-specific metadata into the shared schema, and
`quest_tracker_hub.py` publishes that target stream. Shared transport contracts
and geometry come from the versioned `embodied-ops` Python package. Foxglove is
the only collection observation and control UI; the browser page is retained
only for Quest calibration.
See `docs/architecture.md` for the full module map.

## Daily workflow

The repository exposes one small `just` command surface for both people and
Agents. Run `just` to list it. The normal physical workflow is:

```bash
just adb-status
just calibrate lab                 # only when the physical setup changed
just stop                          # close calibration after saving the profile
just forcevla lab --record         # ForceVLA / MuJoCo
just status                        # concise live health
just stop                          # stop the complete active task
# Or start ManiSkill instead:
just maniskill lab cube_sort --record
```

`calibrate`, `forcevla`, and `maniskill` are Agent-friendly lifecycle
commands: each starts a detached managed task, performs the device and protocol
checks internally, and returns only after the page or backend is ready. Only one
managed task may run at a time. `just status-json` is the stable machine-readable
probe, `just logs` shows the latest log, and `just stop` is idempotent. Stopping
an active recording never silently labels it training-ready; save it in
Foxglove first when it should become a demonstration.

Calibration is page-first: `just calibrate <profile>` opens the local editor
even when Quest is still offline, then attaches Quest and repairs its reverse
ports as soon as USB debugging becomes available. A later USB reconnect repairs
the link again without restarting an active app. `just status` remains
`DEGRADED` until the device and FrankaBot are ready, while the page stays usable.

`just adb-prepare` is the one-shot Quest readiness command: it waits for an
authorized device, wakes it, enables stay-awake power policy, repairs every ADB
reverse mapping, closes launch-blocking Meta panels, softly focuses FrankaBot,
and verifies XR foreground without restarting the app. `just adb-focus` is the
smaller recovery step if a Meta panel later steals focus. `just adb-restart` is
deliberately explicit and should be used only when the app itself is wedged.
The session health monitor never restarts the Quest app mid-operation.

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
| `8130` | host -> local subscribers | Simulator-neutral `TeleopTarget` PUB stream, produced by `quest-tracker-hub` |
| `8131` | backend -> observers | Action-aligned feedback plus Agent/wrist JPEG frames |
| `8132` | UI <-> backend | Acknowledged Hold/reset/recording operator commands |
| `8765` | Foxglove -> host | Official Foxglove SDK WebSocket gateway |
| `8766` | browser -> host | Quest calibration web page (pre-collection only) |

## Decoupled ManiSkill / MuJoCo workflow

The end-to-end launcher, no-controller synthetic validation, recording layout,
safety behavior, and physical-controller checklist are documented in
[`docs/unified_teleop.md`](docs/unified_teleop.md).

The maintained flow is one managed command followed by an explicit stop:

```bash
# Choose one backend:
just maniskill lab cube_sort --record
# just forcevla lab --record
just status
just stop
```

The launcher owns the target source, selected backend, and Foxglove gateway as
one supervised service set. The organization layout `Quest Unified Teleop` connects to
`ws://127.0.0.1:8765`. For a no-controller test, use `just
synthetic-forcevla` or `just synthetic-maniskill`. See
[`scripts/README.md`](scripts/README.md) for the intentionally small script
surface.

After a profile exists, the start command is the complete physical workflow:
it waits for USB/ADB, configures every reverse port, brings FrankaBot to the
foreground, starts the source/backend/Foxglove processes, verifies a real
Foxglove handshake and backend feedback, then prints `READY` and returns. Put
on the headset, pick up the right controller, press `B` to clutch, and operate.
If USB or controller tracking drops, Cartesian motion freezes; reconnect USB
or pick up and wave the controller. After six stable frames the mapper
re-anchors at the current robot pose instead of chasing the old controller
pose.

Meta may put a controller into `CONNECTED_INACTIVE` when the headset is not
worn. ADB can still look healthy in that state, but 6DoF input is unavailable.
The launcher clears Oculus first-time/system dialogs that can otherwise block
an ADB-started VR activity, then starts FrankaBot explicitly in the Quest VR
category. During collection, the health monitor only restores lost reverse
ports and reports app focus; it never restarts FrankaBot. One compact React
panel shows Quest, controller/B, backend-loop, and view-latency state alongside
acknowledged safety, episode, and recording actions. Standard Foxglove
diagnostics remain published for engineering inspection without duplicating
the operator-facing status.

For the ForceVLA MuJoCo backend, the source starts the canonical gripper state
closed and the backend starts below the socket in its held-connector pose.
Relative controller orientation is enabled by default. Foxglove adds compact
world-frame wrist force and torque plots next to the same status and controls.

## Installation

```bash
cd ~/Codespace/quest-trajectory-recorder
scripts/setup.sh
source .venv/bin/activate
```

Install `just` once on macOS with `brew install just`; `scripts/setup.sh`
remains available as the bootstrap command before `just` is installed.

`scripts/setup.sh` installs `embodied-ops[teleop-zmq]` from PyPI. A contributor
developing both packages may explicitly set `EMBODIED_OPS_ROOT` to overlay a
local editable checkout; ordinary users do not clone that repository.

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
scripts/run_live3d.sh --host 0.0.0.0 --open-browser
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

## Calibration And Settings Tool

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

For reusable named calibration profiles, prefer:

```bash
cd ~/Codespace/quest-trajectory-recorder
just calibrate lab
```

This writes `calibrations/lab.json`. Use a different profile name for
a different table / chair / camera setup. The web UI also has a `Profile`
section, so you can load an existing profile or save the current calibration
under a new name without restarting the server. After saving, run `just stop`;
the same command also closes a managed teleoperation task.

Start the live viewer in LAN mode:

```bash
scripts/run_live3d.sh --host 0.0.0.0 --open-browser
```

If the browser is not opened automatically, visit:

```text
http://127.0.0.1:8766/
```

The browser tool shows:

- The live 3D trajectory.
- Start and latest points.
- Controller orientation axes derived from the quaternion.
- Quest stream/profile readiness and the next calibration action.
- A browser-side teleop-frame calibration:
  - Click `Start right sample`, move the controller 15-30 cm toward your intended right direction, then click `Save right`.
  - Click `Start forward sample`, move the controller 15-30 cm toward your intended forward direction, then click `Save forward`.
  - Hold the controller at your neutral teleop origin and click `Save origin`.
  - Quest gravity defines up. The right motion is flattened to the horizontal plane, and the forward motion only chooses the sign of the forward axis; the final forward axis is rebuilt as the right-handed direction orthogonal to right and up.
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

### Quest Focus / System UI Gotcha

If the web viewer is connected but the controller pose freezes at the exact origin
`0,0,0`, or the Quest shows Meta / Oculus system panels instead of giving the
FrankaBot app focus, the APK is usually still running but no longer receiving
effective VR tracking focus. Check and recover it from the Mac:

```bash
just adb-status
just adb-focus
```

If soft focus is not enough, run `just adb-restart` once. This is intentionally
separate because restarting the Unity activity invalidates the current Quest
stream. `scripts/start_frankabot.sh` performs that fresh start only during an
explicit session/calibration launch. The tracker hub restores reverse ports
after confirmed ADB disconnects, but never changes app lifecycle state.


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

## Controller Pose Protocol

The controller-tracking Quest application sends pose frames as UTF-8 text:

```text
absolute|pos_x,pos_y,pos_z|quat_x,quat_y,quat_z,quat_w|flag|point0_x,point0_y,point0_z|point1_x,point1_y,point1_z|point2_x,point2_y,point2_z
```

Observed auxiliary endpoint mapping:

- `point1` is approximately `+local X`.
- `point2` is approximately `-local Y`.
- `point0` is approximately `-local Z`.

The calibration viewer uses the quaternion for orientation; the auxiliary
points are retained in raw CSV captures for validation.

## References

- Open-Teach: https://github.com/aadhithya14/Open-Teach
- LIBERO benchmark: https://github.com/Lifelong-Robot-Learning/LIBERO
- robosuite human demonstration collection: https://github.com/ARISE-Initiative/robosuite
