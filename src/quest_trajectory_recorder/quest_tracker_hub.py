"""Publish calibrated Quest controller targets for any simulator backend."""

from __future__ import annotations

import argparse
import hashlib
import json
import signal
import subprocess
import time
import uuid
from dataclasses import replace
from pathlib import Path

import zmq
from embodied_ops.teleop.zmq_transport import (
    DEFAULT_TARGET_ENDPOINT,
    DEFAULT_TARGET_TOPIC,
    TeleopTargetPublisher,
)

from .quest_ports import (
    DEFAULT_GRIPPER_PORT,
    adb_connected,
    quest_device_info,
    setup_adb_reverse,
)
from .quest_target_source import DirectQuestTargetSource
from .receiver import DEFAULT_PORTS
from .teleop_frame import load_quest_calibration


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Quest raw ZMQ -> simulator-neutral TeleopTarget publisher."
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--remote-port", type=int, default=DEFAULT_PORTS["remote"])
    parser.add_argument("--pause-port", type=int, default=DEFAULT_PORTS["pause"])
    parser.add_argument(
        "--resolution-port", type=int, default=DEFAULT_PORTS["resolution"]
    )
    parser.add_argument("--gripper-port", type=int, default=DEFAULT_GRIPPER_PORT)
    parser.add_argument("--adb-reverse", action="store_true")
    parser.add_argument(
        "--calibration", type=str, default="calibrations/quest_teleop_frame.json"
    )
    parser.add_argument("--no-gate", action="store_true")
    parser.add_argument(
        "--trajectory-gate-pause", choices=("High", "Low"), default="High"
    )
    parser.add_argument("--allow-initial-high", action="store_true")
    parser.add_argument("--gripper-mode", choices=("toggle", "hold"), default="toggle")
    parser.add_argument("--target-bind", default=DEFAULT_TARGET_ENDPOINT)
    parser.add_argument("--print-every", type=int, default=30)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--status-every-sec", type=float, default=1.0)
    parser.add_argument(
        "--adb-check-sec",
        type=float,
        default=3.0,
        help="When using ADB reverse, restore port mappings after a USB reconnect; 0 disables.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.adb_reverse:
        if adb_connected():
            setup_adb_reverse(
                [
                    args.remote_port,
                    args.pause_port,
                    args.resolution_port,
                    args.gripper_port,
                ]
            )
        else:
            print(
                "ADB device is not connected yet; the hub will restore reverse ports after reconnect.",
                flush=True,
            )
    calibration_path = None if not args.calibration else Path(args.calibration)
    calibration = load_quest_calibration(calibration_path)
    calibration_sha256 = (
        hashlib.sha256(calibration_path.read_bytes()).hexdigest()
        if calibration_path is not None and calibration_path.is_file()
        else None
    )
    calibration_id = calibration_path.stem if calibration_path is not None else None
    session_id = args.session_id or str(uuid.uuid4())
    if calibration is None:
        print(
            f"Warning: no calibration loaded from {args.calibration}; publishing fallback teleop axes.",
            flush=True,
        )
    else:
        print(f"Loaded calibration: {args.calibration}", flush=True)

    context = zmq.Context()
    publisher = TeleopTargetPublisher(context, args.target_bind)
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
        session_id=session_id,
        calibration_id=calibration_id,
        calibration_sha256=calibration_sha256,
    )
    stop = False
    last_status_at = 0.0
    last_adb_check_at = 0.0
    device = (
        quest_device_info()
        if args.adb_reverse
        else {"adb_connected": None, "model": None, "serial": None}
    )
    previous_adb_connected = bool(device["adb_connected"])

    def handle_signal(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    print(
        f"Publishing TeleopTarget on {args.target_bind} "
        f"topic={DEFAULT_TARGET_TOPIC.decode('ascii')!r}",
        flush=True,
    )
    print(f"Session: {session_id}", flush=True)
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
                    latest = replace(
                        latest,
                        gate_open=source.gate_open,
                        gripper=source.gripper,
                        host_published_unix_ns=time.time_ns(),
                        source_metadata={
                            **latest.source_metadata,
                            "pause_state": source.pause_state,
                        },
                    )
                    source.latest_target = latest
                    target = latest
            if target is not None:
                target = replace(target, host_published_unix_ns=time.time_ns())
                source.latest_target = target
                publisher.publish(target)
            if (
                target is not None
                and args.print_every
                and target.remote_count % args.print_every == 0
            ):
                x, y, z = target.position
                print(
                    f"target seq={target.seq} gate={target.gate_open} grip={target.gripper:+.0f} "
                    f"pos=({x:+.3f},{y:+.3f},{z:+.3f})",
                    flush=True,
                )

            now = time.monotonic()
            if (
                args.adb_reverse
                and args.adb_check_sec > 0
                and now - last_adb_check_at >= args.adb_check_sec
            ):
                connected = adb_connected()
                if connected and not previous_adb_connected:
                    try:
                        setup_adb_reverse(
                            [
                                args.remote_port,
                                args.pause_port,
                                args.resolution_port,
                                args.gripper_port,
                            ]
                        )
                        print(
                            "ADB reconnected; reverse port mappings restored.",
                            flush=True,
                        )
                    except (OSError, RuntimeError, subprocess.SubprocessError):
                        connected = False
                if connected != previous_adb_connected:
                    print(
                        f"ADB device {'connected' if connected else 'disconnected'}.",
                        flush=True,
                    )
                previous_adb_connected = connected
                device = quest_device_info()
                last_adb_check_at = now

            if (
                args.status_every_sec > 0
                and now - last_status_at >= args.status_every_sec
            ):
                target_age_sec = (
                    None
                    if source.latest_target_at is None
                    else max(0.0, time.time() - source.latest_target_at)
                )
                if target_age_sec is not None and target_age_sec <= 0.5:
                    state = "streaming"
                elif source.raw_remote_count and not source.remote_count:
                    state = "tracking_invalid"
                elif source.latest_target is not None:
                    state = "stale"
                else:
                    state = "waiting_for_controller"
                status = {
                    "schema_version": "embodied.teleop_source_status/v1",
                    "session_id": session_id,
                    "state": state,
                    "timestamp_unix_ns": time.time_ns(),
                    "target_seq": None
                    if source.latest_target is None
                    else source.latest_target.seq,
                    "target_age_ms": None
                    if target_age_sec is None
                    else target_age_sec * 1000.0,
                    "gate_open": source.gate_open,
                    "pause_state": source.pause_state,
                    "valid_remote_count": source.remote_count,
                    "raw_remote_count": source.raw_remote_count,
                    "invalid_remote_count": source.invalid_remote_count,
                    "calibration_id": calibration_id,
                    "calibration_sha256": calibration_sha256,
                    **device,
                }
                publisher.publish_status(
                    json.dumps(status, separators=(",", ":")).encode("utf-8")
                )
                last_status_at = now
    finally:
        source.close()
        publisher.close()
        context.term()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
