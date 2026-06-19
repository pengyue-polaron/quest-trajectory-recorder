from pathlib import Path

import pytest

from quest_trajectory_recorder.calibration_profiles import calibration_complete, calibration_file, sanitize_profile


def test_sanitize_profile_accepts_safe_names():
    assert sanitize_profile("robotics_lab.json", "default") == "robotics_lab"
    assert sanitize_profile("desk-1.v2", "default") == "desk-1.v2"


def test_sanitize_profile_rejects_paths():
    with pytest.raises(ValueError):
        sanitize_profile("../secret", "default")
    with pytest.raises(ValueError):
        sanitize_profile("bad/name", "default")


def test_calibration_file_and_complete():
    assert calibration_file(Path("calibrations"), "robotics_lab") == Path("calibrations/robotics_lab.json")
    assert calibration_complete({"origin": {}, "right": {}, "forward": {}, "up": {}})
    assert not calibration_complete({"origin": {}, "right": {}})
