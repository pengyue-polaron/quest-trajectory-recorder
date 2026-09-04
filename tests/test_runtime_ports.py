from __future__ import annotations

from pathlib import Path

from quest_trajectory_recorder.live3d import DEFAULT_CALIBRATION_WEB_PORT
from quest_trajectory_recorder.live3d import parse_args as parse_live3d_args


def test_calibration_web_port_is_separate_from_foxglove() -> None:
    args = parse_live3d_args([])

    assert DEFAULT_CALIBRATION_WEB_PORT == 8766
    assert args.web_port == 8766
    assert args.web_port != 8765


def test_calibration_page_uses_package_native_hot_adb_runtime() -> None:
    root = Path(__file__).resolve().parents[1]
    launcher = (Path(__file__).resolve().parents[1] / "scripts" / "run_calibration.sh").read_text(
        encoding="utf-8"
    )
    runtime = (root / "src" / "quest_trajectory_recorder" / "calibration_runtime.py").read_text(
        encoding="utf-8"
    )
    lifecycle = (root / "src" / "quest_trajectory_recorder" / "calibration_cli.py").read_text(
        encoding="utf-8"
    )

    assert "quest_trajectory_recorder.calibration_runtime" in launcher
    assert "quest_trajectory_recorder.calibration_runtime" in lifecycle
    assert "live3d_main" in runtime
    assert "device_main" in runtime
    assert "adb_connected" in runtime


def test_calibration_ui_has_one_sequential_workflow_without_debug_sections() -> None:
    page = (
        Path(__file__).resolve().parents[1] / "src" / "quest_trajectory_recorder" / "live3d_web.py"
    ).read_text(encoding="utf-8")

    for label in (
        "Start new calibration",
        "Start collecting right",
        "Finish right",
        "Start collecting forward",
        "Finish forward",
        "Set origin",
    ):
        assert label in page
    assert "Frame status" not in page
    assert "Live data" not in page
    assert "Quest calibration console" not in page
    assert "Numeric teleop frame" not in page
    assert "1. Right direction" not in page
    assert "Start collecting, move the controller" not in page
    assert "overflow-y: auto" in page


def test_quest_checkout_has_no_downstream_runtime() -> None:
    root = Path(__file__).resolve().parents[1]

    assert not (root / "scripts" / "run_quest_session.sh").exists()
    package = root / "src" / "quest_trajectory_recorder"
    for removed in (
        "session_cli.py",
        "backend_session.py",
        "teleop_stack.py",
        "foxglove_bridge.py",
        "foxglove_probe.py",
        "foxglove_publish.py",
        "managed_session.py",
    ):
        assert not (package / removed).exists()

    source_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(package.glob("*.py"))
    ).lower()
    assert "forcevla" not in source_text
    assert "maniskill" not in source_text
    assert "foxglove" not in source_text


def test_justfile_exposes_only_quest_owned_lifecycle() -> None:
    justfile = (Path(__file__).resolve().parents[1] / "Justfile").read_text(encoding="utf-8")

    assert "calibration_cli start" in justfile
    assert "source_cli" in justfile
    assert "forcevla" not in justfile.lower()
    assert "maniskill" not in justfile.lower()
    assert "calibration_cli stop" in justfile
    assert "calibration_cli status --json" in justfile
