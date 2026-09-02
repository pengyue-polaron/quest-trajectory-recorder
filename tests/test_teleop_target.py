from pathlib import Path

import pytest

from quest_trajectory_recorder.teleop_frame import (
    build_axis_map,
    load_quest_calibration,
    matrix_to_quat_xyzw,
    quat_xyzw_to_matrix,
    quest_pos_to_teleop,
    quest_rotation_to_teleop_matrix,
)
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
    assert quest_pos_to_teleop([1.2, 1.7, 3.5], calibration) == pytest.approx(
        [0.2, -0.3, 0.5]
    )


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
    assert decoded.schema_version == "embodied.teleop_target/v1"
    assert decoded.controller_id == "right"
    assert decoded.frame_id == 11
    assert decoded.host_received_monotonic_ns is not None


def test_target_decoder_rejects_payload_without_canonical_schema():
    with pytest.raises(ValueError, match="unsupported teleop target schema"):
        TeleopTarget.from_dict(
            {
                "seq": 1,
                "timestamp": 10.0,
                "position": [0, 0, 0],
                "rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                "gripper": -1,
                "gate_open": False,
            }
        )


def test_build_axis_map_detects_degenerate_axes():
    assert build_axis_map("+y", "+x", "+z") == [
        [0.0, 1.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
    ]
    with pytest.raises(ValueError):
        build_axis_map("+x", "+x", "+z")


def test_rotation_matrix_quaternion_round_trip():
    quaternion = [0.2, -0.3, 0.1, 0.92]
    restored = matrix_to_quat_xyzw(quat_xyzw_to_matrix(quaternion))
    normalized = [
        value / sum(item * item for item in quaternion) ** 0.5 for value in quaternion
    ]
    assert restored == pytest.approx(normalized)


def test_left_handed_quest_basis_still_emits_proper_rotation(tmp_path: Path):
    path = tmp_path / "quest.json"
    path.write_text(
        """
        {
          "origin": {"x": 0, "y": 0, "z": 0},
          "right": {"x": 1, "y": 0, "z": 0},
          "forward": {"x": 0, "y": 0, "z": 1},
          "up": {"x": 0, "y": 1, "z": 0}
        }
        """,
        encoding="utf-8",
    )
    calibration = load_quest_calibration(path)
    rotation = quest_rotation_to_teleop_matrix([0.2, -0.3, 0.1, 0.92], calibration)
    determinant = (
        rotation[0][0]
        * (rotation[1][1] * rotation[2][2] - rotation[1][2] * rotation[2][1])
        - rotation[0][1]
        * (rotation[1][0] * rotation[2][2] - rotation[1][2] * rotation[2][0])
        + rotation[0][2]
        * (rotation[1][0] * rotation[2][1] - rotation[1][1] * rotation[2][0])
    )

    assert determinant == pytest.approx(1.0)
