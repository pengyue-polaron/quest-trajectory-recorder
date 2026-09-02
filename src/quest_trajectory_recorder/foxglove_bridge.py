"""Expose the canonical ZMQ teleoperation plane through Foxglove WebSocket."""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

import zmq
from embodied_ops.teleop import (
    TeleopCommand,
    TeleopCommandResult,
    TeleopFeedback,
    TeleopTarget,
)
from embodied_ops.teleop.zmq_transport import (
    DEFAULT_COMMAND_ENDPOINT,
    DEFAULT_FEEDBACK_ENDPOINT,
    DEFAULT_STATUS_TOPIC,
    DEFAULT_TARGET_ENDPOINT,
    DEFAULT_TARGET_TOPIC,
    TeleopCommandClient,
    TeleopFeedbackReceiver,
)
from foxglove.channels import CompressedImageChannel, PoseInFrameChannel
from foxglove.messages import (
    CompressedImage,
    Pose,
    PoseInFrame,
    Quaternion,
    Timestamp,
    Vector3,
)
from foxglove.websocket import Capability, ServiceRequest

import foxglove
from foxglove import MessageSchema, Schema, Service, ServiceSchema

from .teleop_frame import matrix_to_quat_xyzw

SERVICE_COMMANDS = {
    "/teleop/hold": "hold",
    "/teleop/resume": "resume",
    "/teleop/episode/previous": "previous_episode",
    "/teleop/episode/reset": "reset_episode",
    "/teleop/episode/next": "next_episode",
    "/teleop/recording/start": "start_recording",
    "/teleop/recording/stop": "stop_recording",
    "/teleop/recording/discard": "discard_episode",
}

DEFAULT_FOXGLOVE_LAYOUT_ID = "lay_0eaTLQSSPmExnWfB"

TELEMETRY_SCHEMA = {
    "type": "object",
    "properties": {
        "backend": {"type": "string"},
        "episode_id": {"type": "string"},
        "frame_index": {"type": "integer"},
        "status": {"type": "string"},
        "target_seq": {"type": ["integer", "null"]},
        "target_age_ms": {"type": ["number", "null"]},
        "gate_open": {"type": "boolean"},
        "recording": {"type": "boolean"},
        "eef_x_m": {"type": "number"},
        "eef_y_m": {"type": "number"},
        "eef_z_m": {"type": "number"},
        "gripper": {"type": "number"},
        "action_0": {"type": ["number", "null"]},
        "action_1": {"type": ["number", "null"]},
        "action_2": {"type": ["number", "null"]},
        "action_3": {"type": ["number", "null"]},
        "action_4": {"type": ["number", "null"]},
        "action_5": {"type": ["number", "null"]},
        "action_6": {"type": ["number", "null"]},
        "diagnostics": {"type": "object"},
    },
    "additionalProperties": True,
}


class CommandRouter:
    """Translate Foxglove services into acknowledged backend commands."""

    def __init__(
        self,
        request: Callable[[TeleopCommand], TeleopCommandResult],
    ) -> None:
        self.request = request

    def execute(self, command: str) -> dict[str, Any]:
        if command not in set(SERVICE_COMMANDS.values()):
            raise ValueError(f"unsupported teleop command: {command}")
        request = TeleopCommand(command=command, request_id=str(uuid.uuid4()))
        try:
            return self.request(request).to_dict()
        except (TimeoutError, RuntimeError, zmq.ZMQError) as exc:
            return {
                "schema_version": "embodied.teleop_command_result/v1",
                "request_id": request.request_id,
                "command": request.command,
                "accepted": False,
                "applied": False,
                "backend": "",
                "message": str(exc),
                "duplicate": False,
                "completed_unix_ns": time.time_ns(),
            }

    def handler(self, command: str) -> Callable[[ServiceRequest], bytes]:
        def handle(_request: ServiceRequest) -> bytes:
            return json.dumps(self.execute(command), separators=(",", ":")).encode()

        return handle


def _json_message_schema(name: str, value: dict[str, Any]) -> MessageSchema:
    return MessageSchema(
        encoding="json",
        schema=Schema(
            name=name,
            encoding="jsonschema",
            data=json.dumps(value, separators=(",", ":")).encode(),
        ),
    )


def build_services(router: CommandRouter) -> list[Service]:
    empty = _json_message_schema(
        "embodied.teleop.EmptyRequest",
        {"type": "object", "properties": {}, "additionalProperties": False},
    )
    response = _json_message_schema(
        "embodied.teleop.CommandResponse",
        {
            "type": "object",
            "properties": {
                "accepted": {"type": "boolean"},
                "applied": {"type": "boolean"},
                "command": {"type": "string"},
                "request_id": {"type": "string"},
                "backend": {"type": "string"},
                "message": {"type": "string"},
                "duplicate": {"type": "boolean"},
                "completed_unix_ns": {"type": "integer"},
            },
            "required": [
                "accepted",
                "applied",
                "command",
                "request_id",
                "backend",
                "message",
                "duplicate",
                "completed_unix_ns",
            ],
        },
    )
    schema = ServiceSchema("embodied.teleop.Command", request=empty, response=response)
    return [
        Service(name, schema=schema, handler=router.handler(command))
        for name, command in SERVICE_COMMANDS.items()
    ]


def feedback_telemetry(feedback: TeleopFeedback) -> dict[str, Any]:
    action = [float(item) for item in feedback.action[:7]]
    action.extend([None] * (7 - len(action)))
    position = [float(item) for item in feedback.eef_position[:3]]
    position.extend([0.0] * (3 - len(position)))
    return {
        "backend": feedback.backend,
        "episode_id": feedback.episode_id,
        "frame_index": feedback.frame_index,
        "status": feedback.status,
        "target_seq": feedback.target_seq,
        "target_age_ms": feedback.target_age_ms,
        "gate_open": feedback.gate_open,
        "recording": feedback.recording,
        "eef_x_m": position[0],
        "eef_y_m": position[1],
        "eef_z_m": position[2],
        "gripper": feedback.gripper,
        **{f"action_{index}": action[index] for index in range(7)},
        "diagnostics": feedback.diagnostics,
    }


def pose_message(
    *,
    timestamp_ns: int,
    frame_id: str,
    position: list[float],
    quaternion_xyzw: list[float],
) -> PoseInFrame:
    return PoseInFrame(
        timestamp=Timestamp(
            timestamp_ns // 1_000_000_000, timestamp_ns % 1_000_000_000
        ),
        frame_id=frame_id,
        pose=Pose(
            position=Vector3(x=position[0], y=position[1], z=position[2]),
            orientation=Quaternion(
                x=quaternion_xyzw[0],
                y=quaternion_xyzw[1],
                z=quaternion_xyzw[2],
                w=quaternion_xyzw[3],
            ),
        ),
    )


def foxglove_deep_link(*, websocket_url: str, layout_id: str = "") -> str:
    """Build the preferred Foxglove web-to-desktop link."""
    parameters = {
        "ds": "foxglove-websocket",
        "ds.url": websocket_url,
        "openIn": "desktop",
    }
    if layout_id:
        parameters["layoutId"] = layout_id
    return f"https://app.foxglove.dev/~/view?{urlencode(parameters)}"


class FoxgloveTeleopBridge:
    def __init__(
        self,
        *,
        target_endpoint: str,
        feedback_endpoint: str,
        command_endpoint: str,
        host: str,
        port: int,
    ) -> None:
        self.zmq_context = zmq.Context()
        self.target_socket = self.zmq_context.socket(zmq.SUB)
        self.target_socket.setsockopt(zmq.LINGER, 0)
        self.target_socket.setsockopt(zmq.RCVHWM, 4)
        self.target_socket.setsockopt(zmq.SUBSCRIBE, DEFAULT_TARGET_TOPIC)
        self.target_socket.setsockopt(zmq.SUBSCRIBE, DEFAULT_STATUS_TOPIC)
        self.target_socket.connect(target_endpoint)
        self.feedback = TeleopFeedbackReceiver(self.zmq_context, feedback_endpoint)
        self.command = TeleopCommandClient(
            self.zmq_context,
            command_endpoint,
        )
        self.poller = zmq.Poller()
        self.poller.register(self.target_socket, zmq.POLLIN)
        self.poller.register(self.feedback.socket, zmq.POLLIN)

        self.router = CommandRouter(
            lambda command: self.command.request(command, timeout_ms=1500)
        )
        self.foxglove_context = foxglove.Context()
        self.agent_channel = CompressedImageChannel(
            "/teleop/agent_view", context=self.foxglove_context
        )
        self.wrist_channel = CompressedImageChannel(
            "/teleop/wrist_camera", context=self.foxglove_context
        )
        self.eef_pose_channel = PoseInFrameChannel(
            "/teleop/eef_pose", context=self.foxglove_context
        )
        self.desired_eef_pose_channel = PoseInFrameChannel(
            "/teleop/desired_eef_pose", context=self.foxglove_context
        )
        self.target_pose_channel = PoseInFrameChannel(
            "/teleop/controller_target", context=self.foxglove_context
        )
        self.telemetry_channel = foxglove.Channel(
            "/teleop/telemetry",
            schema=TELEMETRY_SCHEMA,
            message_encoding="json",
            context=self.foxglove_context,
        )
        self.target_channel = foxglove.Channel(
            "/teleop/target",
            schema={"type": "object", "additionalProperties": True},
            message_encoding="json",
            context=self.foxglove_context,
        )
        self.tracker_channel = foxglove.Channel(
            "/teleop/source_status",
            schema={"type": "object", "additionalProperties": True},
            message_encoding="json",
            context=self.foxglove_context,
        )
        self.server = foxglove.start_server(
            name="Embodied teleoperation",
            host=host,
            port=port,
            capabilities=[Capability.Services],
            services=build_services(self.router),
            context=self.foxglove_context,
            message_backlog_size=4,
        )
        self.forwarded_feedback = 0
        self.forwarded_targets = 0

    @property
    def port(self) -> int:
        return int(self.server.port)

    def poll(self, timeout_ms: int) -> None:
        ready = dict(self.poller.poll(timeout_ms))
        if self.target_socket in ready:
            self._take_targets()
        if self.feedback.socket in ready:
            latest = self.feedback.newest()
            if latest is not None:
                self._publish_feedback(*latest)

    def _take_targets(self) -> None:
        while True:
            try:
                topic, payload = self.target_socket.recv_multipart(flags=zmq.NOBLOCK)
            except zmq.Again:
                return
            try:
                value = json.loads(payload.decode())
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            timestamp_ns = time.time_ns()
            if topic == DEFAULT_STATUS_TOPIC and isinstance(value, dict):
                self.tracker_channel.log(value, log_time=timestamp_ns)
            elif topic == DEFAULT_TARGET_TOPIC and isinstance(value, dict):
                target = TeleopTarget.from_dict(value)
                timestamp_ns = target.host_published_unix_ns or timestamp_ns
                self.target_channel.log(target.to_dict(), log_time=timestamp_ns)
                self.target_pose_channel.log(
                    pose_message(
                        timestamp_ns=timestamp_ns,
                        frame_id="teleop_target",
                        position=target.position,
                        quaternion_xyzw=matrix_to_quat_xyzw(target.rotation),
                    ),
                    log_time=timestamp_ns,
                )
                self.forwarded_targets += 1

    def _publish_feedback(
        self, feedback: TeleopFeedback, agent_jpeg: bytes, wrist_jpeg: bytes
    ) -> None:
        timestamp_ns = feedback.timestamp_unix_ns
        self.agent_channel.log(
            CompressedImage(
                timestamp=Timestamp(
                    timestamp_ns // 1_000_000_000,
                    timestamp_ns % 1_000_000_000,
                ),
                frame_id=f"{feedback.backend}/agent_camera",
                data=agent_jpeg,
                format="jpeg",
            ),
            log_time=timestamp_ns,
        )
        self.wrist_channel.log(
            CompressedImage(
                timestamp=Timestamp(
                    timestamp_ns // 1_000_000_000,
                    timestamp_ns % 1_000_000_000,
                ),
                frame_id=f"{feedback.backend}/wrist_camera",
                data=wrist_jpeg,
                format="jpeg",
            ),
            log_time=timestamp_ns,
        )
        orientation = feedback.eef_orientation_xyzw or [0.0, 0.0, 0.0, 1.0]
        self.eef_pose_channel.log(
            pose_message(
                timestamp_ns=timestamp_ns,
                frame_id="teleop_world",
                position=feedback.eef_position,
                quaternion_xyzw=orientation,
            ),
            log_time=timestamp_ns,
        )
        if feedback.desired_eef_position is not None:
            self.desired_eef_pose_channel.log(
                pose_message(
                    timestamp_ns=timestamp_ns,
                    frame_id="teleop_world",
                    position=feedback.desired_eef_position,
                    quaternion_xyzw=orientation,
                ),
                log_time=timestamp_ns,
            )
        self.telemetry_channel.log(feedback_telemetry(feedback), log_time=timestamp_ns)
        self.forwarded_feedback += 1

    def close(self) -> None:
        self.server.stop()
        self.poller.unregister(self.target_socket)
        self.poller.unregister(self.feedback.socket)
        self.target_socket.close(0)
        self.feedback.close()
        self.command.close()
        self.zmq_context.term()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-endpoint", default=DEFAULT_TARGET_ENDPOINT)
    parser.add_argument("--feedback-endpoint", default=DEFAULT_FEEDBACK_ENDPOINT)
    parser.add_argument("--command-endpoint", default=DEFAULT_COMMAND_ENDPOINT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-foxglove", action="store_true")
    parser.add_argument(
        "--layout-id",
        default=DEFAULT_FOXGLOVE_LAYOUT_ID,
        help="Remote Foxglove layout ID to select; pass an empty string to omit it.",
    )
    parser.add_argument("--duration-sec", type=float, default=0.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    bridge = FoxgloveTeleopBridge(
        target_endpoint=args.target_endpoint,
        feedback_endpoint=args.feedback_endpoint,
        command_endpoint=args.command_endpoint,
        host=args.host,
        port=args.port,
    )
    stop = False

    def handle_signal(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    url = f"ws://{args.host}:{bridge.port}"
    print(f"Foxglove teleop gateway: {url}", flush=True)
    if args.open_foxglove:
        deep_link = foxglove_deep_link(
            websocket_url=url,
            layout_id=args.layout_id,
        )
        subprocess.run(["open", deep_link], check=False)
    started = time.monotonic()
    try:
        while not stop and (
            args.duration_sec <= 0 or time.monotonic() - started < args.duration_sec
        ):
            bridge.poll(50)
    finally:
        print(
            f"Foxglove bridge stopped: targets={bridge.forwarded_targets} "
            f"feedback={bridge.forwarded_feedback}",
            flush=True,
        )
        bridge.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
