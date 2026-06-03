#!/usr/bin/env python3
"""Bridge the controller-tracking Quest APK frames into Open-Teach-style topics.

The Quest ControllerTracking APK sends plain-text ZMQ PUSH frames on:
  - 8125: controller pose, formatted as absolute|pos|quat|flag|axis_endpoints...
  - 8095: resolution state, e.g. High/Low
  - 8100: pause/continue state, e.g. High/Low

Open-Teach's Python side mainly passes data around with ZMQ PUB/SUB frames:
  topic + space + pickle(payload)

This bridge keeps the APK-facing PULL sockets, then republishes compatible
Open-Teach topics so existing Open-Teach subscribers/operators can consume the
controller pose without changing the Quest app.
"""

from __future__ import annotations

import argparse
import math
import pickle
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import zmq

from .receiver import parse_remote_text


DEFAULT_REMOTE_PORT = 8125
DEFAULT_RESOLUTION_PORT = 8095
DEFAULT_PAUSE_PORT = 8100

DEFAULT_TRANSFORMED_PORT = 8089
DEFAULT_RESOLUTION_PUB_PORT = 8093
DEFAULT_PAUSE_PUB_PORT = 8102


def normalize3(vector: list[float]) -> list[float]:
    length = math.sqrt(sum(value * value for value in vector))
    if length <= 1e-12:
        return [0.0, 0.0, 0.0]
    return [value / length for value in vector]


def subtract3(a: list[float], b: list[float]) -> list[float]:
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def negate3(vector: list[float]) -> list[float]:
    return [-vector[0], -vector[1], -vector[2]]


def quat_to_axes_xyzw(quat: list[float]) -> tuple[list[float], list[float], list[float]]:
    """Return world-space X/Y/Z axes from an xyzw quaternion."""
    x, y, z, w = quat
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm <= 1e-12:
        return [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]
    x, y, z, w = x / norm, y / norm, z / norm, w / norm

    # Rotation matrix columns are the transformed local basis vectors.
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    x_axis = [1 - 2 * (yy + zz), 2 * (xy + wz), 2 * (xz - wy)]
    y_axis = [2 * (xy - wz), 1 - 2 * (xx + zz), 2 * (yz + wx)]
    z_axis = [2 * (xz + wy), 2 * (yz - wx), 1 - 2 * (xx + yy)]
    return normalize3(x_axis), normalize3(y_axis), normalize3(z_axis)


def remote_to_openteach_frame(remote: dict[str, Any], axis_source: str) -> list[list[float]]:
    """Convert ControllerTracking pose to Open-Teach's 4x3 frame payload.

    Open-Teach's arm operator expects:
      [origin, x_axis, y_axis, z_axis]

    In the observed APK stream the three auxiliary points are endpoints of
    -local Z, +local X, and -local Y respectively, each about 0.1 m away from
    the controller position. This mapping is cross-checked against the xyzw
    quaternion by analyze_trajectory.py.
    """
    position = [float(value) for value in remote["position"]]
    points = remote.get("points") or []

    if axis_source == "points" and len(points) >= 3:
        x_axis = normalize3(subtract3(points[1], position))
        y_axis = normalize3(negate3(subtract3(points[2], position)))
        z_axis = normalize3(negate3(subtract3(points[0], position)))
    else:
        x_axis, y_axis, z_axis = quat_to_axes_xyzw([float(value) for value in remote["rotation"]])

    return [position, x_axis, y_axis, z_axis]


@dataclass
class OpenTeachPublisher:
    host: str
    port: int

    def __post_init__(self) -> None:
        self.context: zmq.Context[zmq.Socket] = zmq.Context()
        self.socket = self.context.socket(zmq.PUB)
        self.socket.setsockopt(zmq.LINGER, 0)
        self.socket.bind(f"tcp://{self.host}:{self.port}")

    def publish(self, topic: str, payload: Any) -> None:
        self.socket.send(bytes(f"{topic} ", "utf-8") + pickle.dumps(payload, protocol=-1))

    def close(self) -> None:
        self.socket.close(0)
        self.context.term()


def make_pull_socket(context: zmq.Context[zmq.Socket], host: str, port: int, conflate: bool) -> zmq.Socket:
    socket = context.socket(zmq.PULL)
    socket.setsockopt(zmq.LINGER, 0)
    if conflate:
        # This matches Open-Teach's real-time control style: always consume the latest state.
        socket.setsockopt(zmq.CONFLATE, 1)
    else:
        socket.setsockopt(zmq.RCVHWM, 10000)
    socket.bind(f"tcp://{host}:{port}")
    return socket


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bridge the controller-tracking Quest APK to Open-Teach PUB/SUB topics.")
    parser.add_argument("--host", default="0.0.0.0", help="Host for APK-facing PULL sockets.")
    parser.add_argument("--pub-host", default="127.0.0.1", help="Host for Open-Teach-facing PUB sockets.")
    parser.add_argument("--remote-port", type=int, default=DEFAULT_REMOTE_PORT)
    parser.add_argument("--resolution-port", type=int, default=DEFAULT_RESOLUTION_PORT)
    parser.add_argument("--pause-port", type=int, default=DEFAULT_PAUSE_PORT)
    parser.add_argument("--transformed-port", type=int, default=DEFAULT_TRANSFORMED_PORT)
    parser.add_argument("--resolution-pub-port", type=int, default=DEFAULT_RESOLUTION_PUB_PORT)
    parser.add_argument("--pause-pub-port", type=int, default=DEFAULT_PAUSE_PUB_PORT)
    parser.add_argument(
        "--axis-source",
        choices=("points", "quaternion"),
        default="points",
        help="Use APK auxiliary axis endpoints or derive axes from the quaternion.",
    )
    parser.add_argument(
        "--conflate",
        action="store_true",
        help="Keep only the newest inbound frame, like Open-Teach's live-control sockets.",
    )
    parser.add_argument("--print-every", type=int, default=60, help="Print every N remote frames; 0 disables.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    context: zmq.Context[zmq.Socket] = zmq.Context()
    poller = zmq.Poller()
    sockets: dict[zmq.Socket, str] = {}
    for channel, port in {
        "remote": args.remote_port,
        "resolution": args.resolution_port,
        "pause": args.pause_port,
    }.items():
        socket = make_pull_socket(context, args.host, port, args.conflate)
        sockets[socket] = channel
        poller.register(socket, zmq.POLLIN)
        print(f"APK PULL listening: {channel} tcp://{args.host}:{port}", flush=True)

    transformed_pub = OpenTeachPublisher(args.pub_host, args.transformed_port)
    resolution_pub = OpenTeachPublisher(args.pub_host, args.resolution_pub_port)
    pause_pub = OpenTeachPublisher(args.pub_host, args.pause_pub_port)
    print(f"Open-Teach PUB: transformed_hand_frame tcp://{args.pub_host}:{args.transformed_port}", flush=True)
    print(f"Open-Teach PUB: button tcp://{args.pub_host}:{args.resolution_pub_port}", flush=True)
    print(f"Open-Teach PUB: pause tcp://{args.pub_host}:{args.pause_pub_port}", flush=True)
    print("Waiting for Quest frames...", flush=True)

    stop = False

    def handle_signal(_signum: int, _frame: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    remote_count = 0
    try:
        while not stop:
            ready = dict(poller.poll(timeout=250))
            for socket in ready:
                channel = sockets[socket]
                text = socket.recv().decode("utf-8", errors="replace").strip()
                if channel == "remote":
                    remote = parse_remote_text(text)
                    if not remote:
                        continue
                    frame = remote_to_openteach_frame(remote, args.axis_source)
                    transformed_pub.publish("transformed_hand_frame", frame)
                    transformed_pub.publish(
                        "quest_remote_pose",
                        {
                            "kind": remote["kind"],
                            "position": remote["position"],
                            "rotation": remote["rotation"],
                            "flag": remote["flag"],
                            "points": remote["points"],
                            "frame": frame,
                        },
                    )
                    remote_count += 1
                    if args.print_every and remote_count % args.print_every == 0:
                        pos = remote["position"]
                        print(
                            f"remote #{remote_count}: pos=({pos[0]:.4f},{pos[1]:.4f},{pos[2]:.4f})",
                            flush=True,
                        )
                elif channel == "resolution":
                    # Open-Teach's operators expect a pickled one-element array-like payload.
                    resolution_pub.publish("button", [text])
                elif channel == "pause":
                    pause_pub.publish("pause", [text])
    finally:
        for socket in sockets:
            poller.unregister(socket)
            socket.close(0)
        context.term()
        transformed_pub.close()
        resolution_pub.close()
        pause_pub.close()

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
