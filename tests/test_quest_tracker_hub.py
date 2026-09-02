from __future__ import annotations

import threading
import time

from quest_trajectory_recorder.quest_tracker_hub import _AdbHealthMonitor


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
        manage_app=False,
        app_refocus_sec=10.0,
        initial_device={"adb_connected": True},
        connected_fn=connected,
        reverse_ports_fn=lambda: {8100, 8125},
        setup_reverse_fn=lambda _ports: None,
        focus_fn=lambda: None,
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


def test_adb_health_monitor_restores_ports_and_refocuses_after_reconnect() -> None:
    restored: list[list[int]] = []
    focus_calls: list[bool] = []
    monitor = _AdbHealthMonitor(
        required_ports=[8125, 8100, 8126, 8127],
        check_sec=60.0,
        manage_app=True,
        app_refocus_sec=10.0,
        initial_device={"adb_connected": False},
        connected_fn=lambda: True,
        reverse_ports_fn=lambda: {8100, 8125},
        setup_reverse_fn=lambda ports: restored.append(ports),
        focus_fn=lambda: focus_calls.append(True),
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
    assert focus_calls == [True]
    assert update.device["adb_connected"] is True
    assert update.events == (
        "ADB reverse mappings restored: [8126, 8127]",
        "FrankaBot refocused after ADB reconnect.",
        "ADB device connected.",
    )


def test_adb_health_monitor_repairs_lost_app_focus() -> None:
    focus_calls: list[bool] = []
    device_states = iter(
        [
            {
                "adb_connected": True,
                "model": "Quest 3",
                "serial": "serial",
                "app_resumed": False,
            },
            {
                "adb_connected": True,
                "model": "Quest 3",
                "serial": "serial",
                "app_resumed": True,
            },
        ]
    )
    monitor = _AdbHealthMonitor(
        required_ports=[8100, 8125],
        check_sec=60.0,
        manage_app=True,
        app_refocus_sec=0.0,
        initial_device={"adb_connected": True},
        connected_fn=lambda: True,
        reverse_ports_fn=lambda: {8100, 8125},
        setup_reverse_fn=lambda _ports: None,
        focus_fn=lambda: focus_calls.append(True),
        device_info_fn=lambda: next(device_states),
    )
    monitor.start()
    try:
        update = _wait_for_update(monitor)
    finally:
        monitor.close()

    assert focus_calls == [True]
    assert update.device["app_resumed"] is True
    assert update.events == ("FrankaBot lost focus and was restored.",)


def test_adb_health_monitor_shutdown_interrupts_long_schedule_wait() -> None:
    monitor = _AdbHealthMonitor(
        required_ports=[8100, 8125],
        check_sec=60.0,
        manage_app=False,
        app_refocus_sec=10.0,
        initial_device={"adb_connected": True},
        connected_fn=lambda: True,
        reverse_ports_fn=lambda: {8100, 8125},
        setup_reverse_fn=lambda _ports: None,
        focus_fn=lambda: None,
        device_info_fn=lambda: {"adb_connected": True, "app_resumed": True},
    )
    monitor.start()
    _wait_for_update(monitor)
    started = time.monotonic()
    monitor.close()

    assert time.monotonic() - started < 0.1
    assert not monitor._thread.is_alive()
