"""Publish calibrated Quest controller targets for any simulator backend."""

from __future__ import annotations

import argparse
import hashlib
import math
import queue
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import zmq
from embodied_ops.teleop import TeleopSourceStatus
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

_DISCONNECTED_DEVICE = {
    "adb_connected": False,
    "model": None,
    "serial": None,
    "app_resumed": False,
}


@dataclass(frozen=True, slots=True)
class _AdbHealthUpdate:
    device: dict[str, Any]
    events: tuple[str, ...]


class _AdbHealthMonitor:
    """Run slow ADB recovery checks away from the target-ingest thread."""

    def __init__(
        self,
        *,
        required_ports: list[int],
        check_sec: float,
        manage_app: bool,
        app_refocus_sec: float,
        initial_device: dict[str, Any],
        connected_fn: Callable[[], bool] = adb_connected,
        reverse_ports_fn: Callable[[], set[int]] = adb_reverse_ports,
        setup_reverse_fn: Callable[[list[int]], None] = setup_adb_reverse,
        focus_fn: Callable[[], None] = focus_frankabot,
        device_info_fn: Callable[[], dict[str, Any]] = quest_device_info,
    ) -> None:
        if check_sec <= 0:
            raise ValueError("ADB health-check interval must be positive")
        self.required_ports = tuple(int(port) for port in required_ports)
        self.check_sec = float(check_sec)
        self.manage_app = bool(manage_app)
        self.app_refocus_sec = float(app_refocus_sec)
        self._connected_fn = connected_fn
        self._reverse_ports_fn = reverse_ports_fn
        self._setup_reverse_fn = setup_reverse_fn
        self._focus_fn = focus_fn
        self._device_info_fn = device_info_fn
        self._previous_connected = bool(initial_device.get("adb_connected"))
        self._last_app_refocus_at = 0.0
        self._updates: queue.SimpleQueue[_AdbHealthUpdate] = queue.SimpleQueue()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="quest-adb-health",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        # ADB is external to this process and may itself be wedged during USB
        # teardown. The worker is a daemon and owns no ZMQ sockets, so cleanup
        # must not wait forever for an uninterruptible platform-tools call.
        self._thread.join(timeout=2.0)

    def take_updates(self) -> list[_AdbHealthUpdate]:
        updates: list[_AdbHealthUpdate] = []
        while True:
            try:
                updates.append(self._updates.get_nowait())
            except queue.Empty:
                return updates

    def _check_once(self, now: float) -> _AdbHealthUpdate:
        events: list[str] = []
        connected = self._connected_fn()
        if connected:
            try:
                missing_ports = sorted(set(self.required_ports) - self._reverse_ports_fn())
                if missing_ports:
                    self._setup_reverse_fn(missing_ports)
                    events.append(f"ADB reverse mappings restored: {missing_ports}")
                if not self._previous_connected and self.manage_app:
                    self._focus_fn()
                    self._last_app_refocus_at = now
                    events.append("FrankaBot refocused after ADB reconnect.")
            except (OSError, RuntimeError, subprocess.SubprocessError):
                connected = False
        if connected != self._previous_connected:
            events.append(f"ADB device {'connected' if connected else 'disconnected'}.")
        self._previous_connected = connected

        try:
            device = self._device_info_fn()
        except (OSError, RuntimeError, subprocess.SubprocessError):
            device = dict(_DISCONNECTED_DEVICE)
        if (
            connected
            and self.manage_app
            and not device.get("app_resumed")
            and now - self._last_app_refocus_at >= self.app_refocus_sec
        ):
            try:
                self._focus_fn()
                self._last_app_refocus_at = now
                device = self._device_info_fn()
                events.append("FrankaBot lost focus and was restored.")
            except (OSError, RuntimeError, subprocess.SubprocessError):
                pass
        return _AdbHealthUpdate(device=dict(device), events=tuple(events))

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            self._updates.put(self._check_once(started))
            remaining = max(0.0, self.check_sec - (time.monotonic() - started))
            if self._stop.wait(remaining):
                return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Quest raw ZMQ -> simulator-neutral TeleopTarget publisher."
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Raw Quest ZMQ bind host. The safe ADB-reverse default is loopback; opt into 0.0.0.0 for LAN.",
    )
    parser.add_argument("--remote-port", type=int, default=DEFAULT_PORTS["remote"])
    parser.add_argument("--pause-port", type=int, default=DEFAULT_PORTS["pause"])
    parser.add_argument("--resolution-port", type=int, default=DEFAULT_PORTS["resolution"])
    parser.add_argument(
        "--no-manage-app",
        action="store_true",
        help="Do not automatically refocus FrankaBot after ADB/app reconnects.",
    )
    parser.add_argument("--app-refocus-sec", type=float, default=10.0)
    parser.add_argument("--gripper-port", type=int, default=DEFAULT_GRIPPER_PORT)
    parser.add_argument("--adb-reverse", action="store_true")
    parser.add_argument("--calibration", type=str, default="calibrations/quest_teleop_frame.json")
    parser.add_argument("--no-gate", action="store_true")
    parser.add_argument("--trajectory-gate-pause", choices=("High", "Low"), default="High")
    parser.add_argument("--allow-initial-high", action="store_true")
    parser.add_argument("--gripper-mode", choices=("toggle", "hold"), default="toggle")
    parser.add_argument(
        "--tracking-loss-grace-ms",
        type=float,
        default=120.0,
        help="Ignore isolated invalid Quest pose placeholders for this long before holding.",
    )
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
    required_ports = [
        args.remote_port,
        args.pause_port,
        args.resolution_port,
        args.gripper_port,
    ]
    if any(port < 1 or port > 65535 for port in required_ports):
        raise ValueError("Quest ports must be integers from 1 to 65535")
    if len(set(required_ports)) != len(required_ports):
        raise ValueError("Quest raw-data ports must be distinct")
    if args.print_every < 0:
        raise ValueError("--print-every must be non-negative")
    if args.status_every_sec < 0 or args.adb_check_sec < 0:
        raise ValueError("status and ADB check intervals must be non-negative")
    if args.app_refocus_sec <= 0:
        raise ValueError("--app-refocus-sec must be positive")
    if not math.isfinite(args.tracking_loss_grace_ms) or args.tracking_loss_grace_ms < 0:
        raise ValueError("--tracking-loss-grace-ms must be finite and non-negative")
    if args.adb_reverse:
        if adb_connected():
            setup_adb_reverse(required_ports)
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
        tracking_loss_grace_ms=args.tracking_loss_grace_ms,
    )
    stop = False
    last_status_at = 0.0
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
    adb_monitor = (
        _AdbHealthMonitor(
            required_ports=required_ports,
            check_sec=args.adb_check_sec,
            manage_app=not args.no_manage_app,
            app_refocus_sec=args.app_refocus_sec,
            initial_device=device,
        )
        if args.adb_reverse and args.adb_check_sec > 0
        else None
    )

    def handle_signal(_signum: int, _frame: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    if adb_monitor is not None:
        adb_monitor.start()
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
            if adb_monitor is not None:
                for update in adb_monitor.take_updates():
                    device = update.device
                    for event in update.events:
                        print(event, flush=True)

            if args.status_every_sec > 0 and now - last_status_at >= args.status_every_sec:
                target_age_sec = (
                    None
                    if source.latest_target_at is None
                    else max(0.0, time.time() - source.latest_target_at)
                )
                raw_age_sec = (
                    None if source.latest_raw_at is None else max(0.0, now - source.latest_raw_at)
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
                status = TeleopSourceStatus(
                    source="quest",
                    session_id=session_id,
                    state=state,
                    target_seq=None if source.latest_target is None else source.latest_target.seq,
                    target_age_ms=None if target_age_sec is None else target_age_sec * 1000.0,
                    gate_open=source.gate_open,
                    control_ready=tracking_valid and source.gate_open,
                    stream_online=raw_online,
                    tracking_valid=tracking_valid,
                    raw_age_ms=None if raw_age_sec is None else raw_age_sec * 1000.0,
                    valid_age_ms=(None if valid_age_sec is None else valid_age_sec * 1000.0),
                    pause_state=source.pause_state,
                    source_metadata={
                        "valid_remote_count": source.remote_count,
                        "raw_remote_count": source.raw_remote_count,
                        "invalid_remote_count": source.invalid_remote_count,
                        "tracking_loss_count": source.tracking_loss_count,
                        "consecutive_invalid_count": source.consecutive_invalid_count,
                        "tracking_loss_grace_ms": source.tracking_loss_grace_ms,
                        "last_invalid_reason": source.last_invalid_reason,
                        "calibration_id": calibration_id,
                        "calibration_sha256": calibration_sha256,
                        **device,
                    },
                )
                publisher.publish_status(status)
                last_status_at = now
    finally:
        if adb_monitor is not None:
            adb_monitor.close()
        source.close()
        publisher.close()
        context.term()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
