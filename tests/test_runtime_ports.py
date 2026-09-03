from __future__ import annotations

from pathlib import Path

from quest_trajectory_recorder.live3d import DEFAULT_CALIBRATION_WEB_PORT
from quest_trajectory_recorder.live3d import parse_args as parse_live3d_args


def test_calibration_web_port_is_separate_from_foxglove() -> None:
    args = parse_live3d_args([])

    assert DEFAULT_CALIBRATION_WEB_PORT == 8766
    assert args.web_port == 8766
    assert args.web_port != 8765


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
