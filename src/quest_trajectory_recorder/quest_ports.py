"""Quest app port constants and ADB helpers."""

from __future__ import annotations

import subprocess


DEFAULT_GRIPPER_PORT = 8127


def setup_adb_reverse(ports: list[int]) -> None:
    """Forward Quest localhost traffic back to this host over USB ADB."""
    for port in ports:
        subprocess.run(["adb", "reverse", "--remove", f"tcp:{port}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["adb", "reverse", f"tcp:{port}", f"tcp:{port}"], check=True, stdout=subprocess.DEVNULL)
