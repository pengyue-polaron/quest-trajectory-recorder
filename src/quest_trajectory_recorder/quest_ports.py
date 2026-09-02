"""Quest app port constants and ADB helpers."""

from __future__ import annotations

import subprocess
from typing import Any

DEFAULT_GRIPPER_PORT = 8127


def setup_adb_reverse(ports: list[int]) -> None:
    """Forward Quest localhost traffic back to this host over USB ADB."""
    for port in ports:
        subprocess.run(
            ["adb", "reverse", "--remove", f"tcp:{port}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["adb", "reverse", f"tcp:{port}", f"tcp:{port}"],
            check=True,
            stdout=subprocess.DEVNULL,
        )


def adb_connected() -> bool:
    """Return whether one authorized Android / Quest device is reachable."""
    try:
        result = subprocess.run(
            ["adb", "get-state"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == "device"


def quest_device_info() -> dict[str, Any]:
    """Small read-only device diagnostic used in hub status heartbeats."""
    if not adb_connected():
        return {"adb_connected": False, "model": None, "serial": None}

    def value(*args: str) -> str | None:
        result = subprocess.run(
            ["adb", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=3.0,
        )
        text = result.stdout.strip()
        return text or None

    return {
        "adb_connected": True,
        "model": value("shell", "getprop", "ro.product.model"),
        "serial": value("get-serialno"),
    }
