from __future__ import annotations

from pathlib import Path

from quest_trajectory_recorder.live3d import DEFAULT_CALIBRATION_WEB_PORT
from quest_trajectory_recorder.live3d import parse_args as parse_live3d_args


def test_calibration_web_port_is_separate_from_foxglove() -> None:
    args = parse_live3d_args([])

    assert DEFAULT_CALIBRATION_WEB_PORT == 8766
    assert args.web_port == 8766
    assert args.web_port != 8765


def test_calibration_page_starts_before_quest_attachment() -> None:
    launcher = (Path(__file__).resolve().parents[1] / "scripts" / "run_calibration.sh").read_text(
        encoding="utf-8"
    )

    assert "prepare_quest_when_available &" in launcher
    assert "scripts/run_live3d.sh" in launcher
    assert "--adb-reverse" not in launcher
    assert "quest_trajectory_recorder.device_cli prepare" in launcher


def test_session_launcher_uses_protocol_probe_and_child_liveness() -> None:
    launcher = (Path(__file__).resolve().parents[1] / "scripts" / "run_quest_session.sh").read_text(
        encoding="utf-8"
    )

    assert "quest_trajectory_recorder.foxglove_probe" in launcher
    assert 'kill -0 "$FOXGLOVE_PID"' in launcher
    assert "socket.create_connection" not in launcher


def test_session_launcher_appends_common_args_without_expanding_an_empty_array() -> None:
    launcher = (Path(__file__).resolve().parents[1] / "scripts" / "run_quest_session.sh").read_text(
        encoding="utf-8"
    )

    assert "BACKEND_ARGS+=(" in launcher
    assert '  "${BACKEND_ARGS[@]}"\n)' not in launcher


def test_forcevla_session_starts_with_closed_gripper() -> None:
    launcher = (Path(__file__).resolve().parents[1] / "scripts" / "run_quest_session.sh").read_text(
        encoding="utf-8"
    )

    assert 'if [[ "$BACKEND" == "mujoco" ]]; then\n  INITIAL_GRIPPER="closed"' in launcher
    assert '--initial-gripper "$INITIAL_GRIPPER"' in launcher


def test_justfile_exposes_one_agent_lifecycle_for_calibration_and_backends() -> None:
    justfile = (Path(__file__).resolve().parents[1] / "Justfile").read_text(encoding="utf-8")

    assert "session_cli start calibration" in justfile
    assert "session_cli start forcevla" in justfile
    assert "session_cli start maniskill" in justfile
    assert "session_cli stop" in justfile
    assert "session_cli status --json" in justfile
