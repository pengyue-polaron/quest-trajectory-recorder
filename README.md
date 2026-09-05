# Quest Trajectory Recorder

## Tracking-frame confirmation

The recovered APK does not provide a native reference-space epoch. On every
source startup, confirm directions with `quest-align start`, move right at least
15 cm, `quest-align finish`, return to neutral, `quest-align forward`, move
forward at least 15 cm, then `quest-align finish`. Pause B and press B again to
enable the stream. `just alignment-status` is a read-only readiness check.

This quick correction is session-local: it does not overwrite the named profile.
Targets include `source_metadata.calibration_valid` and an `alignment` object
containing the state, reason, revision, observed tracking-frame evidence, and
effective calibration axes. Original profile identity describes the loaded file,
not a claim that the effective transform is unchanged.

The source watches ADB boot/process identity and existing Recenter/Relocalization
logs off the ingest thread. A detected change, or 15 seconds without verifiable
frame evidence, invalidates confirmation and closes the stream gate. Alignment
also rejects stale tracking, short strokes and inconsistent directions. Ordinary
B pauses and controller tracking losses do not invalidate completed alignment.
No reconnect path restarts the APK.

The local ZMQ REP endpoint is `tcp://127.0.0.1:8133` (hub `--alignment-bind`). JSON
requests contain `request_id`, `revision` and `action` (`status`, `start`, `finish`,
`forward`). Responses contain `accepted`, `applied`, `message` and `alignment`.
Mutations require the current revision; retries with the same ID are idempotent.
Keep this unauthenticated endpoint on a trusted host/network.

Detection is best effort, not a hardware safety guarantee: logs may be delayed
or unavailable, and physically changing your facing direction cannot be inferred
from a controller pose. Request Align whenever directions feel wrong. Guaranteed
cross-session spatial continuity requires a future APK with explicit frame-change
events or persistent spatial anchors.

An input-device adapter for the controller-tracking Quest APK. Its maintained
runtime boundary is deliberately small:

```text
Quest APK raw ports -> parser -> named calibration -> TeleopTarget ZMQ PUB
```

This repository does not know which simulator, robot, recorder, or UI consumes
the stream. It does not subscribe to backend feedback, send robot commands, or
supervise downstream processes. The canonical message and ZMQ transport come
from the versioned `embodied-ops[teleop-zmq]` package.

## Daily workflow

Install once:

```bash
scripts/setup.sh
```

Prepare the headset and calibrate when the physical setup changes:

```bash
just adb-prepare
just calibrate lab_new
just status-json
```

Named profiles are stored outside the checkout at:

```text
${QUEST_CALIBRATION_DIR:-~/.config/quest-trajectory-recorder/calibrations}
```

Legacy `calibrations/<name>.json` files remain readable and can be copied with
`quest-profile migrate <name>`. Inspect resolution with `just profiles` and
`just profile-path <name>`.

Publish a calibrated target stream directly:

```bash
just source lab_new
# equivalent installed command:
quest-source --profile lab_new --target-bind tcp://127.0.0.1:8130
```

`quest-source` waits for authorized ADB, wakes Quest, restores reverse ports,
soft-focuses FrankaBot without restarting it, validates the profile, and then
executes only the target publisher. Stop it with Ctrl+C or let a downstream
repository own it as one child of that repository's session.

For complete collection, run the session command exposed by the backend that
owns the robot. That package may launch `quest-source` as an opaque child, but
this package neither discovers nor calls it. The backend owns native mapping,
cameras, recording, visualization, full-session status, and cleanup.

## Quest application

- package: `com.Xigbee.FrankaBot`
- activity/build: `FrankaBotControllerTracking`
- controller pose: raw port 8125
- gripper events: raw port 8127
- resolution state: raw port 8095
- B-button stream gate: raw port 8100
- canonical target/status PUB: default port 8130
- calibration page: HTTP 8766

The observed APK emits the right controller and host receive timing only; it
does not provide a device-side sample timestamp. The publisher preserves raw
metadata, source/session/frame identity, calibration SHA-256, tracking validity,
gate state, pose, orientation, and gripper command in
`embodied.teleop_target/v1` / `embodied.teleop_source_status/v1`.

## ADB operations

`just adb-prepare` is safe to retry: wait for ADB, wake Quest, enable stay-awake,
repair reverse mappings, close launch-blocking Meta panels, focus FrankaBot, and
verify XR foreground. `just adb-focus` never restarts the app;
`just adb-restart` is the explicit last resort.

If the headset is awake but poses become exact zero, first suspect Meta system
UI focus. If the controller is `CONNECTED_INACTIVE`, wear the headset and wave
the controller in camera view; ADB health alone cannot restore 6DoF tracking.

## Development

```bash
just check
```

See [architecture](docs/architecture.md) for the strict ownership rules and
[`scripts/README.md`](scripts/README.md) for low-level diagnostic entry points.
