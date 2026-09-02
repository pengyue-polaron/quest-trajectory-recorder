"""Simulator-agnostic teleop frame calibration and pose math."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .calibration_profiles import calibration_health

AXIS_VECTORS: dict[str, tuple[float, float, float]] = {
    "+x": (1.0, 0.0, 0.0),
    "-x": (-1.0, 0.0, 0.0),
    "+y": (0.0, 1.0, 0.0),
    "-y": (0.0, -1.0, 0.0),
    "+z": (0.0, 0.0, 1.0),
    "-z": (0.0, 0.0, -1.0),
}
AXIS_NAMES = tuple(AXIS_VECTORS)


@dataclass
class QuestCalibration:
    """Browser-saved transform from Quest world coordinates to teleop coordinates."""

    origin: list[float]
    right: list[float]
    forward: list[float]
    up: list[float]
    rotation_neutral: list[float] | None = None


def axis_vector(name: str) -> list[float]:
    return list(AXIS_VECTORS[name])


def _vec3(values: Any) -> list[float]:
    return [float(values[0]), float(values[1]), float(values[2])]


def _norm(values: Any) -> list[float]:
    vector = _vec3(values)
    length = math.sqrt(sum(value * value for value in vector))
    if length <= 1e-12:
        return [0.0, 0.0, 0.0]
    return [value / length for value in vector]


def _dot(a: Any, b: Any) -> float:
    return float(sum(float(x) * float(y) for x, y in zip(a, b)))


def _matmul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [
        [sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)
    ]


def _transpose(matrix: list[list[float]]) -> list[list[float]]:
    return [[matrix[j][i] for j in range(3)] for i in range(3)]


def load_quest_calibration(path: Path | None) -> QuestCalibration | None:
    """Load a calibration profile without binding it to any simulator backend."""
    if path is None or not path.exists():
        return None
    data = json.loads(path.read_text())
    health = calibration_health(data)
    if not health["valid"]:
        raise ValueError(
            f"Invalid calibration file {path}: {'; '.join(health['issues'])}"
        )
    try:
        return QuestCalibration(
            origin=[float(data["origin"][key]) for key in ("x", "y", "z")],
            right=_norm([data["right"][key] for key in ("x", "y", "z")]),
            forward=_norm([data["forward"][key] for key in ("x", "y", "z")]),
            up=_norm([data["up"][key] for key in ("x", "y", "z")]),
            rotation_neutral=(
                [
                    float(data["rotation"]["neutralQuat"][key])
                    for key in ("x", "y", "z", "w")
                ]
                if data.get("rotation", {}).get("neutralQuat")
                else None
            ),
        )
    except KeyError as exc:
        raise ValueError(f"Invalid calibration file {path}: missing {exc}") from exc


def quest_pos_to_teleop(pos: Any, calibration: QuestCalibration | None) -> list[float]:
    """Map Quest world xyz into calibrated [right, forward, up] coordinates."""
    position = _vec3(pos)
    if calibration is None:
        # Fallback matching the original viewer before browser calibration.
        return [position[0], position[2], position[1]]
    delta = [position[i] - calibration.origin[i] for i in range(3)]
    return [
        _dot(delta, calibration.right),
        _dot(delta, calibration.forward),
        _dot(delta, calibration.up),
    ]


def quat_xyzw_to_matrix(quat: Any) -> list[list[float]]:
    """Convert xyzw quaternion to a 3x3 rotation matrix."""
    qx, qy, qz, qw = [float(v) for v in quat]
    length = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    if length <= 1e-12:
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    qx, qy, qz, qw = qx / length, qy / length, qz / length, qw / length
    return [
        [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qw * qz), 2 * (qx * qz + qw * qy)],
        [2 * (qx * qy + qw * qz), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qw * qx)],
        [2 * (qx * qz - qw * qy), 2 * (qy * qz + qw * qx), 1 - 2 * (qx * qx + qy * qy)],
    ]


def matrix_to_quat_xyzw(matrix: Any) -> list[float]:
    """Convert a 3x3 rotation matrix to a normalized xyzw quaternion."""

    m = [[float(matrix[row][column]) for column in range(3)] for row in range(3)]
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        qw = 0.25 * scale
        qx = (m[2][1] - m[1][2]) / scale
        qy = (m[0][2] - m[2][0]) / scale
        qz = (m[1][0] - m[0][1]) / scale
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        scale = math.sqrt(max(0.0, 1.0 + m[0][0] - m[1][1] - m[2][2])) * 2.0
        qx = 0.25 * scale
        qy = (m[0][1] + m[1][0]) / scale
        qz = (m[0][2] + m[2][0]) / scale
        qw = (m[2][1] - m[1][2]) / scale
    elif m[1][1] > m[2][2]:
        scale = math.sqrt(max(0.0, 1.0 + m[1][1] - m[0][0] - m[2][2])) * 2.0
        qx = (m[0][1] + m[1][0]) / scale
        qy = 0.25 * scale
        qz = (m[1][2] + m[2][1]) / scale
        qw = (m[0][2] - m[2][0]) / scale
    else:
        scale = math.sqrt(max(0.0, 1.0 + m[2][2] - m[0][0] - m[1][1])) * 2.0
        qx = (m[0][2] + m[2][0]) / scale
        qy = (m[1][2] + m[2][1]) / scale
        qz = 0.25 * scale
        qw = (m[1][0] - m[0][1]) / scale
    norm = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    if norm <= 1e-12:
        return [0.0, 0.0, 0.0, 1.0]
    return [qx / norm, qy / norm, qz / norm, qw / norm]


def quest_rotation_to_teleop_matrix(
    quat: Any, calibration: QuestCalibration | None
) -> list[list[float]]:
    """Map a Quest controller quaternion into a proper teleop rotation.

    Quest / Unity uses a left-handed world frame, while downstream robotics
    backends use right-handed frames.  The captured position basis may
    therefore have determinant -1.  Conjugating by the basis changes both the
    world and controller coordinate representation and preserves det=+1;
    merely left-multiplying would turn every pose into an improper reflection.
    """
    quest_rot = quat_xyzw_to_matrix(quat)
    if calibration is None:
        quest_to_teleop = [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, 1.0, 0.0]]
    else:
        quest_to_teleop = [calibration.right, calibration.forward, calibration.up]
    return _matmul(
        _matmul(quest_to_teleop, quest_rot),
        _transpose(quest_to_teleop),
    )


def build_axis_map(
    right_axis: str, forward_axis: str, up_axis: str
) -> list[list[float]]:
    """Return a matrix mapping teleop [right, forward, up] into a target world xyz."""
    columns = [axis_vector(right_axis), axis_vector(forward_axis), axis_vector(up_axis)]
    matrix = [[columns[col][row] for col in range(3)] for row in range(3)]
    det = (
        matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1])
        - matrix[0][1] * (matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0])
        + matrix[0][2] * (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0])
    )
    if abs(det) < 1e-6:
        raise ValueError("right/forward/up axes must be orthogonal and non-degenerate")
    return matrix


def resolve_gripper_axis(name: str, initial_eef_rot: Any) -> str:
    """Choose the EEF local axis that best points downward in the initial target frame."""
    if name != "auto":
        return name
    world_down = [0.0, 0.0, -1.0]
    rot = [[float(initial_eef_rot[i][j]) for j in range(3)] for i in range(3)]
    scores = {}
    for axis_name in AXIS_NAMES:
        local = axis_vector(axis_name)
        world = [sum(rot[i][j] * local[j] for j in range(3)) for i in range(3)]
        scores[axis_name] = _dot(world, world_down)
    return max(scores, key=scores.get)
