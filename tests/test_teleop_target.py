from pathlib import Path

import pytest

from quest_trajectory_recorder.teleop_frame import build_axis_map, load_quest_calibration, quest_pos_to_teleop
from quest_trajectory_recorder.teleop_target import TeleopTarget, target_from_remote


def test_quest_calibration_maps_origin_relative_axes(tmp_path: Path):
    path = tmp_path / "lab.json"
    path.write_text(
        """
        {
          "origin": {"x": 1, "y": 2, "z": 3},
          "right": {"x": 1, "y": 0, "z": 0},
          "forward": {"x": 0, "y": 1, "z": 0},
          "up": {"x": 0, "y": 0, "z": 1}
        }
        """,
        encoding="utf-8",
    )
    calibration = load_quest_calibration(path)
    assert quest_pos_to_teleop([1.2, 1.7, 3.5], calibration) == pytest.approx([0.2, -0.3, 0.5])


def test_target_from_remote_round_trips_json():
    remote = {"position": [1, 2, 3], "rotation": [0, 0, 0, 1], "flag": True}
    target = target_from_remote(
        remote,
        calibration=None,
        seq=7,
        gripper=1.0,
        gate_open=True,
        pause_state="High",
        remote_count=11,
        source="unit",
    )
    decoded = TeleopTarget.from_json(target.to_json())
    assert decoded.seq == 7
    assert decoded.position == [1.0, 3.0, 2.0]
    assert decoded.rotation == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    assert decoded.flag is True
    assert decoded.gate_open is True
    assert decoded.source == "unit"


def test_build_axis_map_detects_degenerate_axes():
    assert build_axis_map("+y", "+x", "+z") == [[0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
    try:
        build_axis_map("+x", "+x", "+z")
    except ValueError:
        pass
    else:
        raise AssertionError("expected duplicate axes to fail")
