from __future__ import annotations

import json
from pathlib import Path

from quest_trajectory_recorder import session_cli


def _runtime(monkeypatch, tmp_path: Path) -> Path:
    path = tmp_path / "runtime"
    monkeypatch.setenv("QUEST_TELEOP_RUNTIME_DIR", str(path))
    return path


def test_forcevla_start_command_owns_backend_and_accepts_session_flags(
    monkeypatch, tmp_path: Path
) -> None:
    _runtime(monkeypatch, tmp_path)
    args = session_cli.parse_args(
        ["start", "forcevla", "--profile", "jiangyue", "--", "--record", "--no-open-foxglove"]
    )

    state, command = session_cli._build_start_state(args)

    assert command[:6] == [
        str(session_cli.ROOT / "scripts/run_quest_session.sh"),
        "--backend",
        "mujoco",
        "--profile",
        "jiangyue",
        "--record",
    ]
    assert command[-1] == "--no-open-foxglove"
    assert state["kind"] == "teleop"
    assert state["label"] == "ForceVLA"
    assert state["service_url"] == "ws://127.0.0.1:8765"


def test_maniskill_start_command_has_explicit_task_and_custom_endpoints(
    monkeypatch, tmp_path: Path
) -> None:
    _runtime(monkeypatch, tmp_path)
    args = session_cli.parse_args(
        [
            "start",
            "maniskill",
            "--profile",
            "lab",
            "--task",
            "bar_carry",
            "--",
            "--foxglove-port",
            "8877",
            "--feedback-endpoint",
            "tcp://127.0.0.1:9131",
        ]
    )

    state, command = session_cli._build_start_state(args)

    assert command[1:7] == ["--backend", "maniskill", "--profile", "lab", "--task", "bar_carry"]
    assert state["task"] == "bar_carry"
    assert state["service_url"] == "ws://127.0.0.1:8877"
    assert state["feedback_endpoint"] == "tcp://127.0.0.1:9131"


def test_calibration_is_managed_by_the_same_lifecycle(monkeypatch, tmp_path: Path) -> None:
    _runtime(monkeypatch, tmp_path)
    args = session_cli.parse_args(["start", "calibration", "--profile", "jiangyue"])

    state, command = session_cli._build_start_state(args)

    assert command == [str(session_cli.ROOT / "scripts/run_calibration.sh"), "jiangyue"]
    assert state["kind"] == "calibration"
    assert state["service_url"] == "http://127.0.0.1:8766/"


def test_state_is_atomic_and_stopped_status_is_machine_readable(
    monkeypatch, tmp_path: Path
) -> None:
    runtime = _runtime(monkeypatch, tmp_path)
    assert session_cli.status_snapshot() == {
        "schema_version": session_cli.STATE_SCHEMA,
        "state": "stopped",
        "running": False,
    }

    state = {"schema_version": session_cli.STATE_SCHEMA, "pid": 123, "status": "ready"}
    session_cli._write_state(state)

    assert session_cli._read_state() == state
    assert json.loads((runtime / "session.json").read_text()) == state
    assert not list(runtime.glob("*.tmp"))


def test_start_returns_only_after_readiness_and_is_idempotent(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    _runtime(monkeypatch, tmp_path)

    class Process:
        pid = 4321

        @staticmethod
        def poll():
            return None

    monkeypatch.setattr(session_cli.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(
        session_cli,
        "_wait_until_ready",
        lambda state, process, timeout: {
            "backend": "forcevla_mujoco",
            "status": "holding",
            "streaming": False,
            "recording": False,
        },
    )

    argv = ["start", "forcevla", "--profile", "jiangyue", "--", "--no-open-foxglove"]
    assert session_cli.main(argv) == 0
    state = session_cli._read_state()
    assert state is not None
    assert state["status"] == "ready"
    assert state["pid"] == 4321
    assert "READY ForceVLA" in capsys.readouterr().out

    monkeypatch.setattr(
        session_cli, "_managed_process", lambda state: (True, "run_quest_session.sh")
    )
    monkeypatch.setattr(
        session_cli,
        "status_snapshot",
        lambda probe_timeout: {"state": "ready"},
    )
    assert session_cli.main(argv) == 0
    assert "ALREADY RUNNING ForceVLA" in capsys.readouterr().out


def test_repeated_start_fails_if_existing_services_are_degraded(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    _runtime(monkeypatch, tmp_path)
    args = session_cli.parse_args(["start", "forcevla", "--profile", "lab"])
    state, command = session_cli._build_start_state(args)
    state.update({"pid": 4321, "status": "ready", "command": command})
    session_cli._write_state(state)
    monkeypatch.setattr(
        session_cli, "_managed_process", lambda value: (True, "run_quest_session.sh")
    )
    monkeypatch.setattr(
        session_cli,
        "status_snapshot",
        lambda probe_timeout: {"state": "degraded"},
    )

    assert session_cli.main(["start", "forcevla", "--profile", "lab"]) == 1
    assert "is degraded" in capsys.readouterr().out


def test_same_mode_with_different_runtime_arguments_is_not_idempotent(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    _runtime(monkeypatch, tmp_path)
    existing_args = session_cli.parse_args(
        ["start", "forcevla", "--profile", "lab", "--", "--foxglove-port", "8877"]
    )
    existing, command = session_cli._build_start_state(existing_args)
    existing.update(
        {
            "pid": 4321,
            "status": "ready",
            "command": command,
        }
    )
    session_cli._write_state(existing)
    monkeypatch.setattr(
        session_cli, "_managed_process", lambda state: (True, "run_quest_session.sh")
    )

    result = session_cli.main(
        ["start", "forcevla", "--profile", "lab", "--", "--foxglove-port", "8878"]
    )

    assert result == 1
    assert "run `just stop` first" in capsys.readouterr().out


def test_stop_is_idempotent_and_updates_the_managed_state(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    _runtime(monkeypatch, tmp_path)
    state = {
        "schema_version": session_cli.STATE_SCHEMA,
        "session_id": "test-session",
        "pid": 4321,
        "label": "ForceVLA",
        "status": "ready",
        "process_markers": ["run_quest_session.sh"],
    }
    session_cli._write_state(state)
    monkeypatch.setattr(
        session_cli, "_managed_process", lambda value: (True, "run_quest_session.sh")
    )
    monkeypatch.setattr(session_cli, "_terminate", lambda value, timeout: True)

    assert session_cli.main(["stop"]) == 0
    stopped = session_cli._read_state()
    assert stopped is not None
    assert stopped["status"] == "stopped"
    assert "STOPPED ForceVLA" in capsys.readouterr().out


def test_live_status_combines_process_protocol_backend_and_quest(
    monkeypatch, tmp_path: Path
) -> None:
    _runtime(monkeypatch, tmp_path)
    state = {
        "schema_version": session_cli.STATE_SCHEMA,
        "session_id": "test-session",
        "pid": 4321,
        "label": "ManiSkill",
        "kind": "teleop",
        "backend": "maniskill",
        "profile": "lab",
        "task": "cube_sort",
        "synthetic": False,
        "service_url": "ws://127.0.0.1:8765",
        "feedback_endpoint": "tcp://127.0.0.1:8131",
        "log_path": str(tmp_path / "session.log"),
        "process_markers": ["run_quest_session.sh"],
    }
    session_cli._write_state(state)
    monkeypatch.setattr(
        session_cli, "_managed_process", lambda value: (True, "run_quest_session.sh")
    )
    monkeypatch.setattr(session_cli, "is_foxglove_server_ready", lambda url, timeout_sec: True)
    monkeypatch.setattr(
        session_cli,
        "_probe_feedback",
        lambda endpoint, timeout: {
            "backend": "robotteambench_maniskill",
            "status": "holding",
            "streaming": False,
            "recording": False,
        },
    )
    monkeypatch.setattr(session_cli, "device_status_payload", lambda: {"ready": True})

    snapshot = session_cli.status_snapshot()

    assert snapshot["state"] == "ready"
    assert snapshot["running"] is True
    assert snapshot["feedback"]["backend"] == "robotteambench_maniskill"
    assert snapshot["quest"] == {"ready": True}
