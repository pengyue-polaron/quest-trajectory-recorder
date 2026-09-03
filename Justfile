set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

# List the supported operator and development commands.
default:
    @just --list

# Install the Python environments and local embodied-ops dependency.
setup:
    @scripts/setup.sh

# Show read-only Quest, APK, ADB port, and calibration checks.
doctor profile="lab":
    @scripts/run_quest_doctor.sh --calibration "calibrations/{{ profile }}.json"

# Show concise Quest/app/port state; exits non-zero unless fully ready.
adb-status:
    @.venv/bin/python -m quest_trajectory_recorder.device_cli status

# Print the same ADB state as stable JSON for an Agent or script.
adb-status-json:
    @.venv/bin/python -m quest_trajectory_recorder.device_cli status --json

# Repair ADB reverse ports and focus FrankaBot without restarting it.
adb-prepare:
    @.venv/bin/python -m quest_trajectory_recorder.device_cli prepare

# Focus FrankaBot without restarting it.
adb-focus:
    @.venv/bin/python -m quest_trajectory_recorder.device_cli focus

# Explicitly restart FrankaBot; use only when focus recovery is insufficient.
adb-restart:
    @.venv/bin/python -m quest_trajectory_recorder.device_cli restart

# Install/configure the APK and explicitly start a fresh Quest app process.
adb-install:
    @scripts/start_frankabot.sh

# Start web calibration and return after its page is ready.
calibrate profile="lab":
    @.venv/bin/python -m quest_trajectory_recorder.session_cli start calibration --profile "{{ profile }}"

# Start physical ForceVLA/MuJoCo and return after feedback + Foxglove are ready.
forcevla profile="lab" *args:
    @.venv/bin/python -m quest_trajectory_recorder.session_cli start forcevla --profile "{{ profile }}" -- {{ args }}

# Start physical ManiSkill and return after feedback + Foxglove are ready.
maniskill profile="lab" task="cube_sort" *args:
    @.venv/bin/python -m quest_trajectory_recorder.session_cli start maniskill --profile "{{ profile }}" --task "{{ task }}" -- {{ args }}

# Stop whichever managed calibration or teleoperation task is active.
stop:
    @.venv/bin/python -m quest_trajectory_recorder.session_cli stop

# Show concise health for the active managed task.
status:
    @.venv/bin/python -m quest_trajectory_recorder.session_cli status

# Print stable task health JSON for an Agent or script.
status-json:
    @.venv/bin/python -m quest_trajectory_recorder.session_cli status --json

# Show the tail of the latest managed-task log.
logs lines="80":
    @.venv/bin/python -m quest_trajectory_recorder.session_cli logs --lines "{{ lines }}"

# Run the controller-free ForceVLA smoke workflow.
synthetic-forcevla *args:
    @scripts/run_quest_session.sh --backend mujoco --synthetic {{ args }}

# Run the controller-free ManiSkill smoke workflow.
synthetic-maniskill task="cube_sort" *args:
    @scripts/run_quest_session.sh --backend maniskill --synthetic --task "{{ task }}" {{ args }}

# Package and install the local Foxglove extension.
foxglove-install:
    @npm --prefix foxglove/quest-teleop-controls run local-install

# Run all repository checks used by CI.
check: check-python check-ui check-shell

# Run Python tests and Ruff checks.
check-python:
    .venv/bin/pytest -q
    .venv/bin/ruff check .
    .venv/bin/ruff format --check .

# Typecheck, lint, audit, and package the Foxglove extension.
check-ui:
    npm --prefix foxglove/quest-teleop-controls run typecheck
    npm --prefix foxglove/quest-teleop-controls run lint
    npm --prefix foxglove/quest-teleop-controls audit --audit-level=high
    npm --prefix foxglove/quest-teleop-controls run package

# Validate maintained shell launchers.
check-shell:
    bash -n scripts/*.sh
