#!/usr/bin/env python3
"""Publish calibrated Quest controller targets for any simulator backend."""

from __future__ import annotations

import argparse
import signal
import time
from pathlib import Path

import zmq

from .quest_ports import DEFAULT_GRIPPER_PORT, setup_adb_reverse
from .quest_target_source import DEFAULT_TARGET_ENDPOINT, DEFAULT_TARGET_TOPIC, DirectQuestTargetSource
from .receiver import DEFAULT_PORTS
from .teleop_frame import load_quest_calibration


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Quest raw ZMQ -> simulator-neutral TeleopTarget publisher.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--remote-port", type=int, default=DEFAULT_PORTS["remote"])
    parser.add_argument("--pause-port", type=int, default=DEFAULT_PORTS["pause"])
    parser.add_argument("--resolution-port", type=int, default=DEFAULT_PORTS["resolution"])
    parser.add_argument("--gripper-port", type=int, default=DEFAULT_GRIPPER_PORT)
    parser.add_argument("--adb-reverse", action="store_true")
    parser.add_argument("--calibration", type=str, default="calibrations/quest_teleop_frame.json")
    parser.add_argument("--no-gate", action="store_true")
    parser.add_argument("--trajectory-gate-pause", choices=("High", "Low"), default="High")
    parser.add_argument("--allow-initial-high", action="store_true")
    parser.add_argument("--gripper-mode", choices=("toggle", "hold"), default="toggle")
    parser.add_argument("--target-bind", default=DEFAULT_TARGET_ENDPOINT)
    parser.add_argument("--target-topic", default=DEFAULT_TARGET_TOPIC)
    parser.add_argument("--print-every", type=int, default=30)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.adb_reverse:
        setup_adb_reverse([args.remote_port, args.pause_port, args.resolution_port, args.gripper_port])
    calibration = load_quest_calibration(None if not args.calibration else Path(args.calibration))
    if calibration is None:
        print(f"Warning: no calibration loaded from {args.calibration}; publishing fallback teleop axes.", flush=True)
    else:
        print(f"Loaded calibration: {args.calibration}", flush=True)

    context = zmq.Context()
    publisher = context.socket(zmq.PUB)
    publisher.setsockopt(zmq.LINGER, 0)
    publisher.setsockopt(zmq.SNDHWM, 1)
    publisher.bind(args.target_bind)
    source = DirectQuestTargetSource(
        context=context,
        host=args.host,
        remote_port=args.remote_port,
        pause_port=args.pause_port,
        calibration=calibration,
        no_gate=args.no_gate,
        trajectory_gate_pause=args.trajectory_gate_pause,
        allow_initial_high=args.allow_initial_high,
        gripper_mode=args.gripper_mode,
    )
    stop = False

    def handle_signal(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    print(f"Publishing TeleopTarget on {args.target_bind} topic={args.target_topic!r}", flush=True)
    try:
        while not stop:
            target = source.poll(50)
            for event in source.take_events():
                print(event, flush=True)
            if target is None and source.latest_target is not None:
                latest = source.latest_target
                if (
                    latest.gate_open != source.gate_open
                    or latest.pause_state != source.pause_state
                    or latest.gripper != source.gripper
                ):
                    latest.gate_open = source.gate_open
                    latest.pause_state = source.pause_state
                    latest.gripper = source.gripper
                    latest.timestamp = time.time()
                    target = latest
            if target is None:
                continue
            publisher.send_multipart([args.target_topic.encode("utf-8"), target.to_json().encode("utf-8")])
            if args.print_every and target.remote_count % args.print_every == 0:
                x, y, z = target.position
                print(
                    f"target seq={target.seq} gate={target.gate_open} grip={target.gripper:+.0f} "
                    f"pos=({x:+.3f},{y:+.3f},{z:+.3f})",
                    flush=True,
                )
    finally:
        source.close()
        publisher.close(0)
        context.term()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
