# Unified Quest teleoperation

The maintained collection path has one UI and one internal transport:

```text
Quest APK -> tracker hub -> embodied.teleop_target/v1 (ZMQ PUB 8130)
                                  |
                         ManiSkill or MuJoCo
                                  |
          feedback + Agent/wrist JPEG (ZMQ PUB 8131)
                                  |
                         Foxglove gateway (8765)
                                  |
                    Foxglove Quest Unified Teleop

Foxglove services -> command/result (ZMQ DEALER/ROUTER 8132) -> backend

Quest calibration page (pre-collection only): http://127.0.0.1:8766
```

Foxglove is the only collection UI. The calibration browser is deliberately
separate because it edits a Quest-owned calibration profile before collection;
it never controls a simulator. There is no MJPEG dashboard, Operator Panel, or
stdin command path in the maintained stack.

ManiSkill uses a stable `1.4x` translation scale. ForceVLA uses a more direct
`1.6x` scale with a `1.5` position gain, 10 ms target filter, and 1.2 m/s
guarded target slew limit. Pass backend options such as `--position-scale`
after the Just recipe arguments when a task needs deliberate tuning.

## One-time setup

```bash
brew install just
just setup
../RobotTeamBench/scripts/setup_quest_teleop_mac.sh
../forceVLA-mujoco/scripts/setup_quest_teleop_env.sh
```

## Calibration and physical collection

Create one profile for each physical setup:

```bash
just calibrate lab
just stop
```

The calibration page uses `http://127.0.0.1:8766`; it never shares
Foxglove's collection port. It starts before Quest is connected, so the profile
editor is always available. A background attachment step waits for authorized
ADB, restores the reverse ports, and softly focuses FrankaBot without restarting
an already-running app. It remains present to repair the mapping after a real
USB disconnect and reconnect.

Then start one complete session:

```bash
# Choose ManiSkill...
just maniskill lab cube_sort --scene-seed 17001 --record

# ...or MuJoCo:
# just forcevla lab --record
```

Each public start command runs as one detached managed task and returns only
after the service is usable. The lifecycle wrapper starts/rechecks FrankaBot,
runs the device doctor, binds the raw Quest ports in the tracker hub, starts the
selected backend and Foxglove SDK gateway, verifies Foxglove plus backend
feedback, and opens the organization layout. Use `just status` or
`just status-json` for health, `just logs` for recent output, and `just stop` to
stop the complete child-process set. Repeating the same start is idempotent;
starting a different task reports the active task instead of replacing it.

It waits up to 120 seconds for an authorized Quest by default, so the practical
sequence is simply: power/wake Quest, connect USB, run the command, wear the
headset, pick up the right controller, then press `B` to clutch.
`--adb-wait-seconds N` changes the startup wait. During a session, the hub restores reverse ports
after an ADB reconnect without restarting the simulator or Quest app. A single
failed ADB probe is debounced. If Meta steals focus, the Foxglove state changes
to `App not active`; run `just adb-focus`. Only use `just adb-restart` when soft
focus fails, because it explicitly starts a fresh Unity activity.

For unattended diagnosis, `just status-json` reports the whole managed task;
`just adb-status-json` returns the lower-level `quest.adb_status/v1` device
object and a non-zero exit code unless USB, FrankaBot focus, and all reverse
ports are ready. `just adb-prepare` is the complete, safe-to-retry preparation:
wait for authorized ADB, wake Quest, apply stay-awake power policy, repair every
mapping, soft-focus FrankaBot, and verify XR foreground without restarting it.

Do wear the headset while controlling. Meta can leave ADB and the controller
radio connected while marking the controller `CONNECTED_INACTIVE`; in that
state the app receives no usable 6DoF pose. If tracking drops, the backend
freezes Cartesian motion. Pick up the right controller and wave it in the
headset's view. Recovery requires six consecutive valid frames and then
re-anchors controller-to-EEF motion at the current pose, so a reacquisition
jump cannot make the arm chase its pre-dropout target.

Real collection refuses a missing calibration profile. The doctor verifies
ADB, authorization, APK/activity, reverse ports, and calibration geometry.
Physical pose signs, trigger behavior, and human-operable gains still require a
controller-in-hand check.

Quest/Unity reports controller poses in a left-handed world frame. Position
calibration intentionally preserves the operator's physical right, forward,
and up directions even when that basis has determinant -1. Controller
orientations use a full basis conjugation, so the canonical target still emits
a proper determinant-1 rotation for robotics backends.

## No-controller validation

The same launcher can substitute a deterministic canonical target publisher:

```bash
just synthetic-maniskill cube_sort --max-steps 80
just synthetic-forcevla --max-steps 80
```

Synthetic mode exercises the ZMQ subscriber, mapper, simulator, feedback,
cameras, Foxglove gateway, and cleanup. It cannot certify the physical Quest
axes, tracking focus, B-button clutch, trigger, or calibration.

## Foxglove surface

The official SDK gateway listens exclusively at `ws://127.0.0.1:8765`. The
session launcher requires a Foxglove-protocol handshake before starting the
backend, so an unrelated HTTP listener cannot be mistaken for a ready gateway.
Every launch sends the complete data-source and organization-layout deep link
to Foxglove Desktop. Merely activating an existing window is insufficient
because that window may still be attached to a stopped WebSocket session.
The organization
layout is `Quest Unified Teleop` (`lay_0eaTLQSSPmExnWfB`); its versioned export
is `foxglove/quest_teleop.foxglove-layout.json`. All buttons live in the
`quest-teleop-controls.controls` React extension panel; there are no scattered
native Call Service panels in the maintained layout.

Topics:

- `/teleop/agent_view`
- `/teleop/wrist_camera`
- `/teleop/eef_pose`
- `/teleop/desired_eef_pose`
- `/teleop/controller_target`
- `/teleop/telemetry`
- `/teleop/target`
- `/teleop/source_status`
- `/teleop/operator_state` (typed compact state used by the React panel)
- `/teleop/diagnostics` (`diagnostic_msgs/msg/DiagnosticArray`)

Services:

- `/teleop/hold`, `/teleop/resume`
- `/teleop/episode/previous`, `/teleop/episode/reset`, `/teleop/episode/next`
- `/teleop/recording/start`, `/teleop/recording/stop`,
  `/teleop/recording/discard`

Every service waits for an `embodied.teleop_command_result/v1` response. The
backend remains the safety authority and duplicate request IDs are never
applied twice.

The default layout uses one compact React operator panel for both state and
acknowledged controls. It shows only Quest, controller/B state, backend rate,
view latency, and controller position. The same state is still published as
standard `/teleop/diagnostics` for native Diagnostics panels and as the typed
`/teleop/operator_state` topic for the extension. Raw JSON topics remain
available for engineering inspection but are not part of the operator layout.
The recovered APK intentionally stops pose packets while B is released; that
state is reported as `Paused`, not as a controller disconnect.

Agent view and wrist camera sit side by side in near-native-aspect panels. The
force and torque plots sit directly below them, while the compact operator
state and controls occupy one dedicated right-hand sidebar.

Two compact Plot panels render `/teleop/telemetry.force_*` and
`/teleop/telemetry.torque_*`. ForceVLA publishes zeroed world-frame components
and norms; other backends leave these optional fields empty. The first five
ForceVLA control steps establish display bias, without modifying recorded raw
500 Hz wrench telemetry.

The extension source is `foxglove/quest-teleop-controls`. Run `npm ci && npm
run local-install` there for local development or `npm run package` to produce
the `.foxe` archive. The `Publish Foxglove operator UI` GitHub Action validates
and packages the extension, uploads it to the organization registry, and then
updates and verifies the organization layout. It requires the repository
secret `FOXGLOVE_API_KEY`. Bump the extension version before publishing changed
extension code because Foxglove requires every uploaded version to be unique.

Interactive ManiSkill collection disables the environment's registered
700-step timeout. A completed or explicitly timed episode enters Hold and waits
for Previous, Reset, or Next; it never advances to another scene on its own.
Use `--episode-max-steps N` only when a deliberate timeout is useful.

Default tracking protection is fail-closed: 250 ms target timeout, six-frame
recovery, rejection of a controller step over 6 cm, and a 1 mm positional
deadband. ForceVLA uses its responsive settings above and the full OSC action
range; ManiSkill retains its more conservative backend defaults. Both backends
also apply their own workspace and native-action limits. These values are CLI
options when a task needs deliberate tuning.

The same layout is shared by both maintained simulator backends:

![ManiSkill Foxglove teleoperation](images/foxglove_maniskill.png)

![MuJoCo Foxglove teleoperation](images/foxglove_mujoco.png)

## Ownership

- Quest repository: APK parsing, ADB, calibration, target publication, session
  composition, and the Foxglove gateway/layout.
- `embodied-ops`: canonical schemas, ZMQ transport, relative-clutch Cartesian
  mapping, dropout/reacquisition guard, and episode-manifest mechanics.
- Backend repository: thresholds, workspace/action limits, native action,
  task reset/navigation, cameras, task-specific record fields, and command
  acknowledgement.
- Foxglove: visualization and semantic service requests; never raw actuator
  authority.

Only `embodied.teleop_target/v1`, `embodied.teleop_feedback/v1`,
`embodied.teleop_command/v1`, and `embodied.teleop_command_result/v1` are
accepted. Older Quest-prefixed schemas are rejected instead of upgraded.

## Synchronized records

Both backends write pre-action `agent_view.mp4` and `wrist_camera.mp4` plus one
`embodied.teleop_step/v1` row in `steps.jsonl` per native action. Each row
contains the canonical target, timing, action, EEF/proprioception, exact camera
indexes, watchdog/hold reason, task diagnostics, and saturation evidence.
`manifest.json` is written last as the completion marker and records the
operator disposition, training eligibility, termination reason, aligned sample
counts, byte sizes, and SHA-256 hashes. MuJoCo additionally preserves every
500 Hz physics substep in `force_telemetry.h5`. Discard removes the take instead
of leaving ambiguous partial data; interruption is retained but explicitly
marked in the manifest. Source/session/controller/calibration provenance is
summarized per take; a mixed session, sequence regression, or missing/mixed
Quest calibration digest automatically makes the take ineligible for training.
Camera alignment semantics are backend-declared and explicit. ForceVLA records
the latest completed pre-action snapshot without making control wait for
rendering; source-frame reuse is expected, its age is recorded, and samples
older than 250 ms make the take ineligible. The exact mismatch count, stale
count, and maximum age remain in the manifest for diagnosis.
