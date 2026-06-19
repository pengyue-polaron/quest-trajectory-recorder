"""Calibration profile file helpers shared by the web UI and teleop scripts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


DEFAULT_CALIBRATION_PATH = Path("calibrations/quest_teleop_frame.json")
PROFILE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def sanitize_profile(raw: str | None, default: str) -> str:
    profile = (raw or default).strip()
    if profile.endswith(".json"):
        profile = profile[:-5]
    if not profile or "/" in profile or "\\" in profile or not PROFILE_RE.fullmatch(profile):
        raise ValueError("profile must contain only letters, numbers, '_', '-', or '.'")
    return profile


def calibration_file(calibration_dir: Path, profile: str) -> Path:
    return calibration_dir / f"{profile}.json"


def calibration_complete(data: Any) -> bool:
    return isinstance(data, dict) and all(key in data for key in ("origin", "right", "forward", "up"))
