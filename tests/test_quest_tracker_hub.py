from __future__ import annotations

import threading
import time

from quest_trajectory_recorder.quest_tracker_hub import (
    _AdbHealthMonitor,
    _prepare_initial_adb,
    _source_state,
)


def _wait_for_update(monitor: _AdbHealthMonitor):
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        updates = monitor.take_updates()
        if updates:
            return updates[-1]
        time.sleep(0.005)
    raise AssertionError("timed out waiting for ADB health update")


def test_adb_health_check_never_blocks_caller_thread() -> None:
    check_started = threading.Event()
    release_check = threading.Event()
    check_thread_ids: list[int] = []

    def connected() -> bool:
        check_thread_ids.append(threading.get_ident())
        check_started.set()
        assert release_check.wait(timeout=1.0)
        return True

    monitor = _AdbHealthMonitor(
        required_ports=[8100, 8125],
        check_sec=60.0,
        initial_device={"adb_connected": True},
        connected_fn=connected,
        reverse_ports_fn=lambda: {8100, 8125},
        setup_reverse_fn=lambda _ports: None,
        device_info_fn=lambda: {
            "adb_connected": True,
            "model": "Quest 3",
            "serial": "serial",
            "app_resumed": True,
        },
    )
    monitor.start()
    try:
        assert check_started.wait(timeout=1.0)
        started = time.monotonic()
        assert monitor.take_updates() == []
        assert time.monotonic() - started < 0.05
        assert check_thread_ids == [monitor._thread.ident]
        assert check_thread_ids[0] != threading.get_ident()
    finally:
        release_check.set()
        monitor.close()


def test_adb_health_monitor_restores_ports_without_restarting_active_app() -> None:
    restored: list[list[int]] = []
    monitor = _AdbHealthMonitor(
        required_ports=[8125, 8100, 8126, 8127],
        check_sec=60.0,
        initial_device={"adb_connected": False},
        connected_fn=lambda: True,
        reverse_ports_fn=lambda: {8100, 8125},
        setup_reverse_fn=lambda ports: restored.append(ports),
        device_info_fn=lambda: {
            "adb_connected": True,
            "model": "Quest 3",
            "serial": "serial",
            "app_resumed": True,
        },
    )
    monitor.start()
    try:
        update = _wait_for_update(monitor)
    finally:
        monitor.close()

    assert restored == [[8126, 8127]]
    assert update.device["adb_connected"] is True
    assert update.events == (
        "ADB reverse mappings restored: [8126, 8127]",
        "ADB device connected.",
    )


def test_adb_health_monitor_debounces_one_failed_probe() -> None:
    states = iter([False, True])
    initial = {
        "adb_connected": True,
        "model": "Quest 3",
        "serial": "serial",
        "app_resumed": True,
    }
    monitor = _AdbHealthMonitor(
        required_ports=[8100, 8125],
        check_sec=60.0,
        initial_device=initial,
        connected_fn=lambda: next(states),
        reverse_ports_fn=lambda: {8100, 8125},
        setup_reverse_fn=lambda _ports: None,
        device_info_fn=lambda: initial,
    )

    transient = monitor._check_once()
    recovered = monitor._check_once()

    assert transient.device == initial
    assert transient.events == ()
    assert recovered.device == initial
    assert recovered.events == ()


def test_source_state_treats_pose_silence_while_b_released_as_paused() -> None:
    assert (
        _source_state(
            adb_online=True,
            has_received_pose=True,
            raw_online=False,
            tracking_valid=False,
            gate_open=False,
            pause_state="Low",
        )
        == "ready"
    )
    assert (
        _source_state(
            adb_online=True,
            has_received_pose=True,
            raw_online=False,
            tracking_valid=False,
            gate_open=True,
            pause_state="High",
        )
        == "controller_offline"
    )


def test_adb_health_monitor_reports_lost_focus_without_restarting_app() -> None:
    monitor = _AdbHealthMonitor(
        required_ports=[8100, 8125],
        check_sec=60.0,
        initial_device={"adb_connected": True},
        connected_fn=lambda: True,
        reverse_ports_fn=lambda: {8100, 8125},
        setup_reverse_fn=lambda _ports: None,
        device_info_fn=lambda: {
            "adb_connected": True,
            "model": "Quest 3",
            "serial": "serial",
            "app_resumed": False,
        },
    )
    monitor.start()
    try:
        update = _wait_for_update(monitor)
    finally:
        monitor.close()

    assert update.device["app_resumed"] is False
    assert update.events == ()


def test_adb_health_monitor_shutdown_interrupts_long_schedule_wait() -> None:
    monitor = _AdbHealthMonitor(
        required_ports=[8100, 8125],
        check_sec=60.0,
        initial_device={"adb_connected": True},
        connected_fn=lambda: True,
        reverse_ports_fn=lambda: {8100, 8125},
        setup_reverse_fn=lambda _ports: None,
        device_info_fn=lambda: {"adb_connected": True, "app_resumed": True},
    )
    monitor.start()
    _wait_for_update(monitor)
    started = time.monotonic()
    monitor.close()

    assert time.monotonic() - started < 0.1
    assert not monitor._thread.is_alive()


def test_initial_adb_reverse_failure_defers_to_health_monitor(capsys) -> None:
    focus_calls: list[bool] = []

    def fail_reverse(_ports: list[int]) -> None:
        raise RuntimeError("USB re-enumerated")

    device = _prepare_initial_adb(
        [8100, 8125],
        connected_fn=lambda: True,
        setup_reverse_fn=fail_reverse,
        activity_resumed_fn=lambda: False,
        focus_fn=lambda: focus_calls.append(True),
        device_info_fn=lambda: {"adb_connected": True},
    )

    assert device == {
        "adb_connected": False,
        "model": None,
        "serial": None,
        "app_resumed": False,
    }
    assert focus_calls == []
    assert "stay safe and retry after reconnect" in capsys.readouterr().out
