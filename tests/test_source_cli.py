from __future__ import annotations

import json
from pathlib import Path

from quest_trajectory_recorder import source_cli


def test_source_resolves_profile_and_executes_only_the_publisher(monkeypatch, tmp_path: Path):
    profile = tmp_path / "lab.json"
    profile.write_text(
        json.dumps(
            {
                "origin": {"x": 0, "y": 0, "z": 0},
                "right": {"x": 1, "y": 0, "z": 0},
                "forward": {"x": 0, "y": 1, "z": 0},
                "up": {"x": 0, "y": 0, "z": 1},
            }
        )
    )
    monkeypatch.setattr(source_cli, "profile_path", lambda name, must_exist: profile)
    stopped = []
    monkeypatch.setattr(source_cli, "calibration_main", lambda argv: stopped.append(argv) or 0)
    monkeypatch.setattr(source_cli, "device_main", lambda argv: 0)
    executed = {}

    def fake_exec(executable, command):
        executed["executable"] = executable
        executed["command"] = command
        raise RuntimeError("exec intercepted")

    monkeypatch.setattr(source_cli.os, "execv", fake_exec)
    try:
        source_cli.main(["--profile", "lab", "--target-bind", "tcp://127.0.0.1:8230"])
    except RuntimeError as exc:
        assert str(exc) == "exec intercepted"

    command = executed["command"]
    assert "quest_trajectory_recorder.quest_tracker_hub" in command
    assert "--target-bind" in command
    assert "foxglove" not in " ".join(command).lower()
    assert "backend" not in " ".join(command).lower()
    assert stopped == [["stop"]]
