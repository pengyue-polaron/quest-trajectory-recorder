"""Session-local frame confirmation; no downstream action or workspace policy."""

from __future__ import annotations

import math
import subprocess
import uuid
from dataclasses import asdict
from typing import Any

from .teleop_frame import QuestCalibration


def read_tracking_frame() -> dict[str, str] | None:
    """Best-effort evidence from the recovered APK, never an inferred pose transform.

    This is deliberately called off the pose-ingest thread. The APK has no native
    reference-space epoch, so loss of this evidence requires confirmation.
    """

    def adb(*args: str) -> str:
        return subprocess.run(
            ["adb", *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
        ).stdout.strip()

    try:
        boot = adb("shell", "cat", "/proc/sys/kernel/random/boot_id")
        pid = adb("shell", "pidof", "com.Xigbee.FrankaBot")
        if not boot or not pid:
            return None
        logs = adb(
            "logcat",
            "-d",
            "-t",
            "2000",
            "-v",
            "epoch",
            "-s",
            "Unity:I",
            "GuardianMapDataMgr:I",
            "*:S",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    markers = [
        line.strip()
        for line in logs.splitlines()
        if "Recenter event detected" in line or "Relocalization started" in line
    ]
    return {"boot": boot, "pid": pid, "event": markers[-1] if markers else ""}


class Alignment:
    """Collect two horizontal strokes and require a new clutch edge afterwards.

    Quick alignment is session-local. Original named profiles are never silently
    overwritten. The effective axes and revision travel with every target for
    recording provenance. A frame change aborts a partially collected stroke.
    """

    def __init__(self, calibration: QuestCalibration | None) -> None:
        self.calibration = calibration
        self.state = "required"
        self.reason = "Confirm directions for this tracking session"
        self.revision = str(uuid.uuid4())
        self.frame: dict[str, str] | None = None
        self.last_evidence_at: float | None = None
        self.position: list[float] | None = None
        self.position_at = 0.0
        self.points: list[list[float]] = []
        self.right: list[float] | None = None
        self.saw_pause = False
        self.responses: dict[str, dict[str, Any]] = {}

    @property
    def valid(self) -> bool:
        return self.state in {"awaiting_b", "ready"}

    @property
    def enabled(self) -> bool:
        return self.state == "ready"

    def invalidate(self, reason: str) -> None:
        self.state = "required"
        self.reason = reason
        self.revision = str(uuid.uuid4())
        self.points = []
        self.right = None
        self.saw_pause = False

    def evidence(self, frame: dict[str, str] | None, now: float) -> None:
        if frame is None:
            return
        if self.frame is not None and frame != self.frame:
            self.invalidate("Tracking frame changed — align directions")
        self.frame = frame
        self.last_evidence_at = now

    def tick(self, now: float) -> None:
        if self.last_evidence_at is not None and now - self.last_evidence_at > 15:
            if self.state != "required":
                self.invalidate("Tracking frame cannot be verified — align when connected")

    def sample(self, position: list[float] | None, now: float) -> None:
        if (
            self.state in {"collecting_right", "collecting_forward"}
            and now - self.position_at > 0.25
        ):
            self.invalidate("Tracking gap during alignment — try again")
        self.position = position
        self.position_at = now
        if self.state in {"collecting_right", "collecting_forward"}:
            if position is None:
                self.invalidate("Tracking lost during alignment — try again")
            elif len(self.points) >= 4000:
                self.invalidate("Alignment timed out — try again")
            else:
                self.points.append(list(position))

    def pause(self, paused: bool) -> None:
        if self.state != "awaiting_b":
            return
        if paused:
            self.saw_pause = True
        elif self.saw_pause:
            self.state = "ready"
            self.reason = ""

    def metadata(self) -> dict[str, Any]:
        return {
            "calibration_valid": self.valid,
            "alignment": {
                "state": self.state,
                "message": self.reason,
                "revision": self.revision,
                "frame": self.frame,
                "effective_calibration": asdict(self.calibration) if self.calibration else None,
            },
        }

    def command(self, request: dict[str, Any], now: float) -> dict[str, Any]:
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            return {"accepted": False, "applied": False, "message": "Request ID required"}
        if request_id in self.responses:
            return self.responses[request_id]
        if request.get("action") == "status":
            return {"accepted": True, "applied": True, **self.metadata()}
        try:
            if request.get("revision") != self.revision:
                raise ValueError("Alignment changed — refresh and try again")
            action = request.get("action")
            if action == "start":
                self.invalidate("Move right, then Finish")
                self._begin("collecting_right", now)
            elif action == "forward" and self.state == "right_done":
                self._begin("collecting_forward", now)
                self.reason = "Move forward, then Finish"
            elif action == "finish" and self.state in {"collecting_right", "collecting_forward"}:
                self._finish(now)
            else:
                raise ValueError("Action is not available in this alignment step")
            response = {"accepted": True, "applied": True, "message": self.reason}
        except ValueError as exc:
            response = {"accepted": False, "applied": False, "message": str(exc)}
        response["alignment"] = self.metadata()["alignment"]
        if len(self.responses) >= 128:
            self.responses.pop(next(iter(self.responses)))
        self.responses[request_id] = response
        return response

    def _begin(self, state: str, now: float) -> None:
        self._fresh(now)
        self.points = [list(self.position)]
        self.state = state

    def _fresh(self, now: float) -> None:
        if self.position is None or now - self.position_at > 0.25:
            raise ValueError("Controller unavailable — restore tracking first")
        if self.last_evidence_at is None or now - self.last_evidence_at > 15:
            raise ValueError("Quest frame unavailable — check ADB first")

    def _finish(self, now: float) -> None:
        self._fresh(now)
        if len(self.points) < 6:
            raise ValueError("Move at least 15 cm, then Finish")
        # Average endpoints to reduce hand tremor, retaining physical stroke sign.
        start = [sum(p[i] for p in self.points[:3]) / 3 for i in range(3)]
        end = [sum(p[i] for p in self.points[-3:]) / 3 for i in range(3)]
        delta = [end[0] - start[0], 0.0, end[2] - start[2]]
        norm = math.sqrt(sum(v * v for v in delta))
        if norm < 0.15:
            raise ValueError("Move at least 15 cm, then Finish")
        axis = [v / norm for v in delta]
        if self.state == "collecting_right":
            self.right = axis
            self.state = "right_done"
            self.reason = "Return to neutral, then Collect forward"
        else:
            assert self.right is not None
            expected = [-self.right[2], 0.0, self.right[0]]
            if sum(a * b for a, b in zip(expected, axis)) < 0.75:
                self.invalidate("Directions inconsistent — Align again")
                raise ValueError(self.reason)
            self.calibration = QuestCalibration(
                origin=list(self.position),
                right=self.right,
                forward=expected,
                up=[0.0, 1.0, 0.0],
            )
            self.state = "awaiting_b"
            self.reason = "Aligned — pause B, then press B to resume"
            self.saw_pause = False
        self.points = []
        self.revision = str(uuid.uuid4())
