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
    adb_reverse_ports,
    focus_frankabot,
    quest_activity_resumed,
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
    parser.add_argument(
        "--no-manage-app",
        action="store_true",
        help="Do not automatically refocus FrankaBot after ADB/app reconnects.",
    )
    parser.add_argument("--app-refocus-sec", type=float, default=10.0)
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
            if not args.no_manage_app and not quest_activity_resumed():
                focus_frankabot()
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
    last_app_refocus_at = 0.0
    device = (
        quest_device_info()
        if args.adb_reverse
        else {
            "adb_connected": None,
            "model": None,
            "serial": None,
            "app_resumed": None,
        }
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
                and target.tracking_valid
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
                if connected:
                    try:
                        required_ports = [
                            args.remote_port,
                            args.pause_port,
                            args.resolution_port,
                            args.gripper_port,
                        ]
                        missing_ports = sorted(
                            set(required_ports) - adb_reverse_ports()
                        )
                        if missing_ports:
                            setup_adb_reverse(missing_ports)
                            print(
                                f"ADB reverse mappings restored: {missing_ports}",
                                flush=True,
                            )
                        if not previous_adb_connected and not args.no_manage_app:
                            focus_frankabot()
                            last_app_refocus_at = now
                            print(
                                "FrankaBot refocused after ADB reconnect.", flush=True
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
                if (
                    connected
                    and not args.no_manage_app
                    and not device.get("app_resumed")
                    and now - last_app_refocus_at >= args.app_refocus_sec
                ):
                    try:
                        focus_frankabot()
                        last_app_refocus_at = now
                        device = quest_device_info()
                        print("FrankaBot lost focus and was restored.", flush=True)
                    except (OSError, RuntimeError, subprocess.SubprocessError):
                        pass
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
                raw_age_sec = (
                    None
                    if source.latest_raw_at is None
                    else max(0.0, now - source.latest_raw_at)
                )
                valid_age_sec = (
                    None
                    if source.latest_valid_at is None
                    else max(0.0, now - source.latest_valid_at)
                )
                raw_online = raw_age_sec is not None and raw_age_sec <= 0.5
                tracking_valid = bool(
                    raw_online
                    and source.latest_raw_valid
                    and valid_age_sec is not None
                    and valid_age_sec <= 0.5
                )
                if device.get("adb_connected") is False:
                    state = "adb_disconnected"
                elif source.latest_raw_at is None:
                    state = "waiting_for_controller"
                elif not raw_online:
                    state = "controller_offline"
                elif not tracking_valid:
                    state = "tracking_invalid"
                elif source.gate_open:
                    state = "streaming"
                else:
                    state = "ready"
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
                    "control_ready": tracking_valid and source.gate_open,
                    "controller_stream_online": raw_online,
                    "tracking_valid": tracking_valid,
                    "raw_age_ms": None if raw_age_sec is None else raw_age_sec * 1000.0,
                    "valid_age_ms": (
                        None if valid_age_sec is None else valid_age_sec * 1000.0
                    ),
                    "pause_state": source.pause_state,
                    "valid_remote_count": source.remote_count,
                    "raw_remote_count": source.raw_remote_count,
                    "invalid_remote_count": source.invalid_remote_count,
                    "tracking_loss_count": source.tracking_loss_count,
                    "last_invalid_reason": source.last_invalid_reason,
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
