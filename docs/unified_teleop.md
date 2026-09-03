# Downstream integration

Quest Recorder publishes only the canonical latest target:

```text
quest-source --profile <name> -> tcp://127.0.0.1:8130
```

A backend subscribes with `embodied_ops.teleop.TeleopTargetSubscriber`. It owns
clutch behavior, stale-frame and reacquisition policy, coordinate mapping into
the robot frame, native actions, cameras, task reset, recording, and feedback.
The Quest publisher does not wait for or inspect any of those processes.

A backend may launch `quest-source` as an opaque input child and may compose any
observer against the canonical data plane. Those choices are deliberately not
represented by a backend registry, repository path, or launch mode here.
