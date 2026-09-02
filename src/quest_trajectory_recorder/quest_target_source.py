"""Transport adapters for calibrated Quest teleop targets."""

from __future__ import annotations

import math
import time
from dataclasses import replace

import zmq

from .receiver import parse_remote_text
from .teleop_frame import QuestCalibration
from .teleop_target import TeleopTarget, target_from_remote, valid_remote


def newest_from_socket(socket: zmq.Socket) -> bytes | None:
    payload = None
    while True:
        try:
            payload = socket.recv(flags=zmq.NOBLOCK)
        except zmq.Again:
            return payload


class DirectQuestTargetSource:
    """Read the Quest APK's raw ZMQ ports and expose calibrated TeleopTarget objects."""

    def __init__(
        self,
        *,
        context: zmq.Context,
        host: str,
        remote_port: int,
        pause_port: int,
        calibration: QuestCalibration | None,
        no_gate: bool,
        trajectory_gate_pause: str,
        allow_initial_high: bool,
        gripper_mode: str,
        session_id: str = "unspecified",
        calibration_id: str | None = None,
        calibration_sha256: str | None = None,
    ) -> None:
        self.context = context
        self.calibration = calibration
        self.no_gate = no_gate
        self.trajectory_gate_pause = trajectory_gate_pause
        self.allow_initial_high = allow_initial_high
        self.gripper_mode = gripper_mode
        self.session_id = session_id
        self.calibration_id = calibration_id
        self.calibration_sha256 = calibration_sha256
        self.events: list[str] = []
        self.pause_state: str | None = None
        self.gate_open = bool(no_gate)
        self.gate_armed = bool(no_gate or allow_initial_high)
        self.initial_high_warned = False
        self.gripper = -1.0
        self.prev_flag = False
        self.remote_count = 0
        self.raw_remote_count = 0
        self.invalid_remote_count = 0
        self.latest_target: TeleopTarget | None = None
        self.latest_target_at: float | None = None
        self.latest_raw_at: float | None = None
        self.latest_valid_at: float | None = None
        self.latest_raw_valid: bool | None = None
        self.tracking_loss_count = 0
        self.last_invalid_reason: str | None = None
        self.remote_socket = context.socket(zmq.PULL)
        self.remote_socket.setsockopt(zmq.LINGER, 0)
        self.remote_socket.setsockopt(zmq.CONFLATE, 1)
        self.remote_socket.bind(f"tcp://{host}:{remote_port}")
        self.pause_socket = context.socket(zmq.PULL)
        self.pause_socket.setsockopt(zmq.LINGER, 0)
        self.pause_socket.setsockopt(zmq.CONFLATE, 1)
        self.pause_socket.bind(f"tcp://{host}:{pause_port}")
        self.poller = zmq.Poller()
        self.poller.register(self.remote_socket, zmq.POLLIN)
        self.poller.register(self.pause_socket, zmq.POLLIN)

    def take_events(self) -> list[str]:
        events = self.events
        self.events = []
        return events

    def poll(self, timeout_ms: int) -> TeleopTarget | None:
        ready = dict(self.poller.poll(timeout=timeout_ms))
        if self.pause_socket in ready:
            payload = newest_from_socket(self.pause_socket)
            if payload is not None:
                self._update_pause(payload.decode("utf-8", errors="replace").strip())
        if self.remote_socket in ready:
            payload = newest_from_socket(self.remote_socket)
            if payload is not None:
                return self._update_remote(payload)
        return None

    def _update_pause(self, state: str) -> None:
        self.pause_state = state
        was_open = self.gate_open
        if self.no_gate or state == self.trajectory_gate_pause and self.gate_armed:
            next_gate_open = True
        else:
            next_gate_open = False
            if state != self.trajectory_gate_pause:
                self.gate_armed = True
        if (
            state == self.trajectory_gate_pause
            and not self.gate_armed
            and not self.initial_high_warned
        ):
            self.events.append(
                "Stream is already High; release B once, then press B again to clutch."
            )
            self.initial_high_warned = True
        self.gate_open = next_gate_open
        if self.gate_open and not was_open:
            self.events.append("Teleop clutch engaged.")
        elif was_open and not self.gate_open:
            self.events.append("Teleop clutch released; robot holds position.")

    def _update_remote(self, payload: bytes) -> TeleopTarget | None:
        received_monotonic_ns = time.monotonic_ns()
        self.latest_raw_at = time.monotonic()
        self.raw_remote_count += 1
        try:
            remote = parse_remote_text(
                payload.decode("utf-8", errors="replace").strip()
            )
        except (TypeError, ValueError):
            remote = None
        if not valid_remote(remote):
            self.invalid_remote_count += 1
            reason = "malformed_pose" if remote is None else "zero_or_invalid_pose"
            if self.latest_raw_valid is True:
                self.tracking_loss_count += 1
                self.events.append(
                    "Controller tracking lost; publishing an immediate safety hold."
                )
            self.latest_raw_valid = False
            self.last_invalid_reason = reason
            if self.latest_target is None:
                return None
            metadata = {
                **self.latest_target.source_metadata,
                "tracking_state": "invalid",
                "tracking_invalid_reason": reason,
                "raw_remote_count": self.raw_remote_count,
                "valid_remote_count": self.remote_count,
                "tracking_loss_count": self.tracking_loss_count,
            }
            if remote is not None:
                for name in ("position", "rotation"):
                    try:
                        values = [float(value) for value in remote[name]]
                    except (KeyError, TypeError, ValueError):
                        continue
                    if all(math.isfinite(value) for value in values):
                        metadata[f"invalid_raw_{name}"] = values
            now_ns = time.time_ns()
            invalid = replace(
                self.latest_target,
                seq=self.raw_remote_count,
                timestamp=now_ns / 1_000_000_000.0,
                frame_id=self.raw_remote_count,
                host_received_monotonic_ns=received_monotonic_ns,
                host_published_unix_ns=now_ns,
                tracking_valid=False,
                source_metadata=metadata,
            )
            self.latest_target = invalid
            return invalid
        recovered = self.latest_raw_valid is False
        self.latest_raw_valid = True
        self.latest_valid_at = time.monotonic()
        self.last_invalid_reason = None
        if recovered:
            self.events.append(
                "Controller pose returned; downstream guard is stabilizing and re-anchoring."
            )
        flag = bool(remote.get("flag"))
        if self.gate_open:
            if self.gripper_mode == "toggle":
                if flag and not self.prev_flag:
                    self.gripper = 1.0 if self.gripper < 0 else -1.0
            else:
                self.gripper = 1.0 if flag else -1.0
        self.prev_flag = flag
        self.remote_count += 1
        target = target_from_remote(
            remote,
            calibration=self.calibration,
            seq=self.raw_remote_count,
            gripper=self.gripper,
            gate_open=self.gate_open,
            pause_state=self.pause_state,
            remote_count=self.raw_remote_count,
            session_id=self.session_id,
            received_monotonic_ns=received_monotonic_ns,
            calibration_id=self.calibration_id,
            calibration_sha256=self.calibration_sha256,
        )
        target = replace(
            target,
            source_metadata={
                **target.source_metadata,
                "tracking_state": "valid",
                "raw_remote_count": self.raw_remote_count,
                "valid_remote_count": self.remote_count,
                "tracking_loss_count": self.tracking_loss_count,
            },
        )
        self.latest_target = target
        self.latest_target_at = target.timestamp
        return target

    def close(self) -> None:
        for socket in (self.remote_socket, self.pause_socket):
            try:
                self.poller.unregister(socket)
            except (KeyError, zmq.ZMQError):
                pass
            socket.close(0)
