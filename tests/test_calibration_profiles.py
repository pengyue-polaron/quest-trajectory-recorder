from pathlib import Path

import pytest

from quest_trajectory_recorder.calibration_profiles import (
    calibration_complete,
    calibration_file,
    calibration_health,
    sanitize_profile,
)


def test_sanitize_profile_accepts_safe_names():
    assert sanitize_profile("robotics_lab.json", "default") == "robotics_lab"
    assert sanitize_profile("desk-1.v2", "default") == "desk-1.v2"


def test_sanitize_profile_rejects_paths():
    with pytest.raises(ValueError):
        sanitize_profile("../secret", "default")
    with pytest.raises(ValueError):
        sanitize_profile("bad/name", "default")


def test_calibration_file_and_complete():
    assert calibration_file(Path("calibrations"), "robotics_lab") == Path(
        "calibrations/robotics_lab.json"
    )
    profile = {
        "origin": {"x": 0, "y": 0, "z": 0},
        "right": {"x": 1, "y": 0, "z": 0},
        "forward": {"x": 0, "y": 1, "z": 0},
        "up": {"x": 0, "y": 0, "z": 1},
    }
    assert calibration_complete(profile)
    assert calibration_health(profile)["determinant"] == pytest.approx(1.0)
    profile["forward"] = {"x": 1, "y": 0, "z": 0}
    assert not calibration_complete(profile)
    assert any("orthogonal" in issue for issue in calibration_health(profile)["issues"])
    assert not calibration_complete({"origin": {}, "right": {}})
