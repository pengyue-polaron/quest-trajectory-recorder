"""Calibration profile file helpers shared by the web UI and teleop scripts."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

DEFAULT_CALIBRATION_PATH = Path("calibrations/quest_teleop_frame.json")
PROFILE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def sanitize_profile(raw: str | None, default: str) -> str:
    profile = (raw or default).strip()
    profile = profile.removesuffix(".json")
    if (
        not profile
        or "/" in profile
        or "\\" in profile
        or not PROFILE_RE.fullmatch(profile)
    ):
        raise ValueError("profile must contain only letters, numbers, '_', '-', or '.'")
    return profile


def calibration_file(calibration_dir: Path, profile: str) -> Path:
    return calibration_dir / f"{profile}.json"


def calibration_complete(data: Any) -> bool:
    return bool(calibration_health(data)["valid"])


def calibration_health(data: Any) -> dict[str, Any]:
    """Validate geometry and report metrics suitable for a UI or doctor command."""

    issues: list[str] = []
    vectors: dict[str, list[float]] = {}
    if not isinstance(data, dict):
        return {"valid": False, "issues": ["profile must be a JSON object"]}
    for name in ("origin", "right", "forward", "up"):
        value = data.get(name)
        if not isinstance(value, dict):
            issues.append(f"{name} must be an xyz object")
            continue
        try:
            vector = [float(value[axis]) for axis in ("x", "y", "z")]
        except (KeyError, TypeError, ValueError):
            issues.append(f"{name} must contain finite numeric x/y/z")
            continue
        if not all(math.isfinite(item) for item in vector):
            issues.append(f"{name} must contain finite numeric x/y/z")
            continue
        vectors[name] = vector

    norms: dict[str, float] = {}
    normalized: dict[str, list[float]] = {}
    for name in ("right", "forward", "up"):
        if name not in vectors:
            continue
        norm = math.sqrt(sum(item * item for item in vectors[name]))
        norms[name] = norm
        if norm <= 1e-6:
            issues.append(f"{name} axis is degenerate")
        else:
            normalized[name] = [item / norm for item in vectors[name]]

    dot_products: dict[str, float] = {}
    for left, right in (("right", "forward"), ("right", "up"), ("forward", "up")):
        if left in normalized and right in normalized:
            dot = sum(a * b for a, b in zip(normalized[left], normalized[right]))
            dot_products[f"{left}_{right}"] = dot
            if abs(dot) > 0.05:
                issues.append(f"{left}/{right} axes are not orthogonal (dot={dot:.4f})")

    determinant = None
    if all(name in normalized for name in ("right", "forward", "up")):
        r, f, u = (normalized[name] for name in ("right", "forward", "up"))
        determinant = (
            r[0] * (f[1] * u[2] - f[2] * u[1])
            - r[1] * (f[0] * u[2] - f[2] * u[0])
            + r[2] * (f[0] * u[1] - f[1] * u[0])
        )
        if determinant < 0.95:
            issues.append(
                f"axes must form a right-handed frame (det={determinant:.4f})"
            )

    rotation_norm = None
    neutral = (
        data.get("rotation", {}).get("neutralQuat")
        if isinstance(data.get("rotation"), dict)
        else None
    )
    if neutral is not None:
        if not isinstance(neutral, dict):
            issues.append("rotation.neutralQuat must be an xyzw object")
        else:
            try:
                quaternion = [float(neutral[axis]) for axis in ("x", "y", "z", "w")]
                rotation_norm = math.sqrt(sum(item * item for item in quaternion))
                if (
                    not all(math.isfinite(item) for item in quaternion)
                    or rotation_norm <= 1e-6
                ):
                    issues.append(
                        "rotation.neutralQuat must be finite and non-degenerate"
                    )
            except (KeyError, TypeError, ValueError):
                issues.append(
                    "rotation.neutralQuat must contain finite numeric x/y/z/w"
                )

    return {
        "valid": not issues,
        "issues": issues,
        "axis_norms": norms,
        "axis_dot_products": dot_products,
        "determinant": determinant,
        "rotation_quaternion_norm": rotation_norm,
    }
