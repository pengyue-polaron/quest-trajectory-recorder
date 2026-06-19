"""Neutral teleop target schema shared by simulator backends."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any

from .teleop_frame import QuestCalibration, quest_pos_to_teleop, quest_rotation_to_teleop_matrix


@dataclass
class TeleopTarget:
    """Calibrated controller target independent of LIBERO, Isaac Sim, or ROS2."""

    seq: int
    timestamp: float
    position: list[float]
    rotation: list[list[float]]
    raw_position: list[float]
    raw_rotation: list[float]
    flag: bool
    gripper: float
    gate_open: bool
    pause_state: str | None
    remote_count: int
    source: str = "quest"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TeleopTarget":
        return cls(
            seq=int(data["seq"]),
            timestamp=float(data["timestamp"]),
            position=[float(v) for v in data["position"]],
            rotation=[[float(v) for v in row] for row in data["rotation"]],
            raw_position=[float(v) for v in data["raw_position"]],
            raw_rotation=[float(v) for v in data["raw_rotation"]],
            flag=bool(data["flag"]),
            gripper=float(data["gripper"]),
            gate_open=bool(data["gate_open"]),
            pause_state=data.get("pause_state"),
            remote_count=int(data["remote_count"]),
            source=str(data.get("source", "quest")),
        )

    @classmethod
    def from_json(cls, text: str) -> "TeleopTarget":
        return cls.from_dict(json.loads(text))


def valid_remote(remote: dict[str, Any] | None) -> bool:
    if not remote:
        return False
    return any(abs(float(v)) > 1e-8 for v in remote["position"])


def target_from_remote(
    remote: dict[str, Any],
    *,
    calibration: QuestCalibration | None,
    seq: int,
    gripper: float,
    gate_open: bool,
    pause_state: str | None,
    remote_count: int,
    source: str = "quest",
) -> TeleopTarget:
    return TeleopTarget(
        seq=seq,
        timestamp=time.time(),
        position=quest_pos_to_teleop(remote["position"], calibration),
        rotation=quest_rotation_to_teleop_matrix(remote["rotation"], calibration),
        raw_position=[float(v) for v in remote["position"]],
        raw_rotation=[float(v) for v in remote["rotation"]],
        flag=bool(remote.get("flag")),
        gripper=gripper,
        gate_open=gate_open,
        pause_state=pause_state,
        remote_count=remote_count,
        source=source,
    )
