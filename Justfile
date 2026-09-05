set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

# List the supported operator and development commands.
default:
    @just --list

# Install the Python environments and local embodied-ops dependency.
setup:
    @scripts/setup.sh

# Show read-only Quest, APK, ADB port, and calibration checks.
doctor profile="lab":
    @scripts/run_quest_doctor.sh --calibration "$(.venv/bin/python -m quest_trajectory_recorder.profile_cli path '{{ profile }}')"

# Show concise Quest/app/port state; exits non-zero unless fully ready.
adb-status:
    @.venv/bin/python -m quest_trajectory_recorder.device_cli status

# Print the same ADB state as stable JSON for an Agent or script.
adb-status-json:
    @.venv/bin/python -m quest_trajectory_recorder.device_cli status --json

# Wait for ADB, wake Quest, restore ports, and verify FrankaBot XR foreground.
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
    @.venv/bin/python -m quest_trajectory_recorder.calibration_cli start --profile "{{ profile }}"

# Publish physical Quest targets until interrupted; consumers subscribe over ZMQ.
source profile="lab" *args:
    @.venv/bin/python -m quest_trajectory_recorder.source_cli --profile "{{ profile }}" {{ args }}

# Read source alignment state without moving or restarting anything.
alignment-status:
    @.venv/bin/python -m quest_trajectory_recorder.alignment_cli status

# Advance direction capture: start (right), finish, forward, finish.
align action:
    @.venv/bin/python -m quest_trajectory_recorder.alignment_cli "{{ action }}"

# List profiles and show whether they live in user or legacy storage.
profiles:
    @.venv/bin/python -m quest_trajectory_recorder.profile_cli list

# Print the resolved path for one named profile.
profile-path profile="lab":
    @.venv/bin/python -m quest_trajectory_recorder.profile_cli path "{{ profile }}"

# Stop the managed calibration page.
stop:
    @.venv/bin/python -m quest_trajectory_recorder.calibration_cli stop

# Show concise health for the calibration page.
status:
    @.venv/bin/python -m quest_trajectory_recorder.calibration_cli status

# Print stable calibration health JSON for an Agent or script.
status-json:
    @.venv/bin/python -m quest_trajectory_recorder.calibration_cli status --json

# Show the tail of the latest managed-task log.
logs lines="80":
    @.venv/bin/python -m quest_trajectory_recorder.calibration_cli logs --lines "{{ lines }}"

# Run all repository checks used by CI.
check: check-python check-shell

# Run Python tests and Ruff checks.
check-python:
    .venv/bin/pytest -q
    .venv/bin/ruff check .
    .venv/bin/ruff format --check .

# Validate maintained shell launchers.
check-shell:
    bash -n scripts/*.sh
