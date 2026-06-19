# Quest to LIBERO Teleop

This path uses the recovered `com.Xigbee.FrankaBot` Quest APK as the tracker, publishes calibrated `TeleopTarget` messages, and drives LIBERO through robosuite `OSC_POSE` actions.

## Why this route

There are two existing approaches worth knowing:

- LIBERO's built-in demonstration script uses robosuite `input2action` with keyboard or SpaceMouse. It is mature, but it is not Quest-based.
- Open-Teach has a `LiberoSimOperator` and `LiberoEnv` pair. It maps VR hand-frame deltas to LIBERO relative actions and streams the simulator image back to the headset. It is mature for the original hand-tracking / bimanual APK path, but it is heavier than needed when the Quest is only a USB tracker.

This repository keeps a small split pipeline:

```text
Quest controller -> FrankaBot APK -> ADB reverse ZMQ 8125/8100
  -> quest-tracker-hub -> TeleopTarget stream
  -> quest-libero-teleop --input-source target
  -> LIBERO robosuite OSC_POSE action -> MuJoCo viewer on the Mac
```

## Calibration model

The preferred workflow is to save named calibration profiles:

```bash
scripts/run_calibration.sh libero_default
```

This writes:

```text
calibrations/libero_default.json
```

That file contains `origin`, `right`, `forward`, and `up` in Quest / Unity world coordinates. Use one profile per physical setup, for example `desk_front.json`, `neck_mount_lab.json`, or `libero_default.json`. `scripts/run_libero_teleop.sh --profile <name>` reads the same file, so the LIBERO controller uses the coordinate frame you already verified in the web diagnostic UI.

The web calibration UI can also load and save these profiles directly. Reopen
the viewer, choose a saved profile from the `Profile` section, and click `Load
profile`; or type a new profile name and click `Save profile` after calibration.

Default LIBERO axis mapping:

| Quest calibrated direction | LIBERO world axis |
| --- | --- |
| right | `+y` |
| forward | `+x` |
| up | `+z` |

If the robot moves in the wrong screen/world direction, do not recalibrate Quest first. Change the LIBERO axis mapping:

```bash
quest-libero-teleop \
  --libero-right-axis -y \
  --libero-forward-axis +x \
  --libero-up-axis +z
```

Each axis must be one of `+x`, `-x`, `+y`, `-y`, `+z`, `-z`, and the three chosen axes must be orthogonal.

## Run

First launch/refocus the Quest APK and open the web calibration UI for a named profile:

```bash
cd ~/Codespace/quest-trajectory-recorder
scripts/run_calibration.sh libero_default
```

In the web UI, calibrate in this order:

1. `Start right sample`, move the controller to your right, then `Save right`.
2. `Start forward sample`, move the controller forward, then `Save forward`.
3. Hold the controller at the neutral teleop origin, then `Save origin`.

The profile is saved automatically after the origin step. `right` is flattened
against Quest gravity, and `forward` is rebuilt as the orthogonal right-handed
axis whose sign matches your forward motion, so the direction samples do not
need to be perfectly orthogonal.

Optional rotation calibration is a second step in the same web UI. The goal is
not to memorize arbitrary controller RGB axes; define the physical controller
axis that should behave like the robot gripper / approach arrow, then save a
neutral pose that maps that arrow to LIBERO's initial downward gripper pose:

1. Switch to `Show rotation view`.
2. In `Controller gripper arrow axis`, choose the controller local axis that points along your intended gripper / approach direction. For the recovered FrankaBot APK, `controller -Z` is a good first guess because the auxiliary `point0` endpoint is approximately `-local Z`.
3. Click `Save arrow axis`.
4. Hold the controller in the neutral gripper pose you want to correspond to the initial LIBERO EEF orientation.
5. Click `Save neutral rotation`.

When LIBERO is launched with `--orientation`, this saved neutral quaternion is
used as the controller rotation zero. Without it, rotation falls back to the
current controller orientation on the first clutch, which is useful for testing
but less repeatable.

In the rotation view, the black `gripper` arrow is shown in the calibrated
teleop frame. Immediately after saving neutral rotation and holding still, it
should overlap `standard gripper down`. If it points the opposite way, change
the gripper arrow axis from `+x` to `-x`, `+y` to `-y`, or `+z` to `-z`, save
the arrow axis, and save neutral rotation again.

Then start the split hub/subscriber pipeline:

```bash
# Shell A: only this process owns raw Quest ports and ADB reverse
scripts/run_quest_tracker_hub.sh --profile libero_default

# Shell B: LIBERO subscribes to the calibrated TeleopTarget stream
scripts/run_libero_teleop.sh \
  --profile libero_default \
  --input-source target \
  --task-suite-name libero_spatial \
  --task-id 0
```

LIBERO subscribes to `TeleopTarget` on `tcp://127.0.0.1:8130`, so an Isaac Sim backend, recorder, or future ROS2 bridge can reuse the same stream without touching raw Quest ports.

Enable rotation after xyz translation and trigger control feel correct:

```bash
scripts/run_libero_teleop.sh \
  --profile libero_default \
  --input-source target \
  --task-suite-name libero_spatial \
  --task-id 0 \
  --orientation
```

Controls:

- The LIBERO OpenCV viewer marks the Quest-decoded target EEF as a green cross/circle and green arrow. It marks the current simulated EEF as a blue dot and blue arrow. Use `--no-debug-overlay` to disable markers.
- The arrows are drawn from robosuite's controlled `grip_site` frame, not the Panda `hand` body quaternion. This matters because `robot0_eef_quat` can describe the hand body while `OSC_POSE` controls the gripper site.
- Press `B` / stream `High` to clutch and drive the arm. Releasing the stream gate holds position.
- The saved controller `origin` maps to the initial LIBERO end-effector pose, so opening LIBERO does not silently treat the current controller pose as zero.
- If the stream is already `High` when LIBERO starts, the script waits until you release `B` once. This prevents an accidental jump from a stale clutch state.
- Right trigger controls the gripper. Default mode toggles open/close on the rising edge.
- Move the controller in calibrated right / forward / up directions to move the LIBERO end-effector.
- Rotation is disabled by default for xyz-only debugging. Pass `--orientation` when you want controller rotation to command EEF orientation.

Useful tuning flags:

```bash
# Slower and safer translation
scripts/run_libero_teleop.sh --profile robotics_lab --input-source target --position-scale 0.6 --position-action-gain 8

# Enable controller rotation after xyz feels correct
scripts/run_libero_teleop.sh --profile robotics_lab --input-source target --orientation

# If the overlay arrow uses the wrong robosuite local gripper axis
scripts/run_libero_teleop.sh --profile robotics_lab --input-source target --orientation --target-gripper-axis -z

# If trigger should close only while held instead of toggling
scripts/run_quest_tracker_hub.sh --profile robotics_lab --gripper-mode hold
```

`--target-gripper-axis auto` is the default. It chooses the robosuite gripper
local axis that points closest to world down in the initial state. Override it
with `+x`, `-x`, `+y`, `-y`, `+z`, or `-z` only if the green/blue overlay arrows
visibly disagree with the gripper geometry.

## Workspace-box calibration option

For precise long sessions, the more stable method is a two-stage calibration:

1. Quest frame calibration: define right / forward / up using the web UI.
2. Workspace calibration: sweep the comfortable physical controller box and map it into a safe LIBERO EEF box.

The current teleop script implements the first stage and a relative clutch mapping. The workspace-box layer should be added when the physical setup is fixed, because it needs task-specific safe EEF bounds. Recommended starting LIBERO bounds are roughly:

```text
x: [-0.45, 0.15]
y: [-0.35, 0.35]
z: [0.75, 1.25]
```

Treat these as conservative starting points and inspect `robot0_eef_pos` for the selected LIBERO task before using them for data collection.
