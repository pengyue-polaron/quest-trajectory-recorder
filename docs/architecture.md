# Repository architecture

## Owned here

- Quest APK/ADB readiness and raw reverse ports;
- raw frame parsing and optional raw capture;
- Quest-coordinate calibration and named profile storage;
- adaptation to canonical `TeleopTarget` and `TeleopSourceStatus`;
- one latest-value ZMQ publisher;
- a synthetic source for source/consumer contract tests.

## Explicitly not owned here

- simulator or robot imports;
- controller-to-native-action mapping, gains, safety limits, or workspace;
- backend feedback, task state, cameras, recording, or episode manifests;
- Foxglove gateway, layout, buttons, or command routing;
- complete-session process supervision.

The repository must not contain a backend name, sibling repository path, opaque
backend command, feedback subscriber, or backend lifecycle mode. A consumer
depends on the canonical `embodied-ops` contract and subscribes to ZMQ; the
Quest publisher has no reference to that consumer.

```text
                embodied-ops canonical contracts
                    /                       \
Quest source -> TeleopTarget PUB          backend SUB -> native action
                    \                       /
              backend-owned session composition
```

## Modules

| Module | Ownership |
| --- | --- |
| `quest_ports.py`, `device_cli.py` | Quest/ADB readiness and explicit focus/restart operations. |
| `receiver.py`, `live_state.py` | Raw APK parsing and live source state. |
| `calibration_profiles.py`, `profile_cli.py` | User-level profile validation, lookup, and migration. |
| `live3d.py`, `live3d_web.py`, `calibration_cli.py` | Quest-only calibration page and lifecycle. |
| `teleop_frame.py`, `teleop_target.py` | Quest frame conversion into canonical values. |
| `quest_target_source.py`, `quest_tracker_hub.py`, `source_cli.py` | Raw socket ownership and target/status publication. |
| `synthetic_target.py` | Deterministic canonical publisher for integration tests. |

## Extension rules

- A new input device gets its own adapter and publishes the same canonical
  contract; it does not add source-specific top-level fields.
- Device calibration stays with the source. Task/workspace calibration and
  action safety stay with the backend.
- A background ADB health check may repair reverse ports but may never restart
  FrankaBot mid-operation.
- Downstream tools may inspect source metadata, but the source never imports or
  calls downstream code.
