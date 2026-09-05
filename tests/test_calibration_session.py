import copy
import json
import time
import uuid

import pytest

from quest_trajectory_recorder.calibration_session import CalibrationSession, PendingRequest

PROFILE = {
    "version": 5,
    "profile": "test",
    "state": "ready",
    "origin": {"x": 0.1, "y": 1.0, "z": 0.2},
    "right": {"x": 1.0, "y": 0.0, "z": 0.0},
    "forward": {"x": 0.0, "y": 0.0, "z": 1.0},
    "up": {"x": 0.0, "y": 1.0, "z": 0.0},
}
RAW = {"position": [0.1, 1.0, 0.2], "rotation": [0.0, 0.0, 0.0, 1.0], "kind": "absolute"}


@pytest.fixture
def editor(tmp_path):
    path = tmp_path / "test.json"
    path.write_text(json.dumps(PROFILE))
    return CalibrationSession(path, url="http://localhost/")


def command(editor, action, **kwargs):
    return editor.command(
        {"request_id": str(uuid.uuid4()), "revision": editor.revision, "action": action, **kwargs}
    )


def test_saved_profile_starts_ready_without_align(editor):
    assert editor.enabled and editor.calibration.right == [1.0, 0.0, 0.0]
    assert editor.metadata()["calibration_valid"]


def test_repeated_roundtrips_save_apply_and_stop_page_updates(editor):
    for index in range(3):
        assert command(editor, "begin")["applied"]
        assert not editor.enabled and not editor.metadata()["calibration_valid"]
        revision = editor.revision
        assert command(editor, "begin")["editor"]["revision"] == revision
        editor.observe(RAW, index, time.monotonic())
        assert len(editor.live.points) == 1
        data = copy.deepcopy(PROFILE)
        data["origin"]["x"] = 0.2 + index
        assert command(editor, "finish", calibration=data, profile="test")["applied"]
        assert json.loads(editor.path.read_text())["origin"]["x"] == 0.2 + index
        assert editor.calibration.origin[0] == 0.2 + index
        assert editor.state == "awaiting_b" and not editor.enabled
        assert editor.snapshot()["last_action"] == "finish"
        editor.observe(RAW, index + 1, time.monotonic())
        assert len(editor.live.points) == 1  # no idle pose stream
        editor.pause(False)
        assert not editor.enabled
        editor.pause(True)
        editor.pause(False)
        assert editor.enabled


def test_invalid_and_stale_finish_do_not_apply(editor):
    original = editor.path.read_bytes()
    command(editor, "begin")
    assert not command(editor, "finish", calibration={})["applied"]
    assert editor.path.read_bytes() == original and not editor.enabled
    assert editor.snapshot()["last_action"] == "begin"
    request = {
        "action": "finish",
        "request_id": "one",
        "revision": editor.revision,
        "calibration": PROFILE,
        "profile": "test",
    }
    result = editor.command(request)
    assert result["applied"]
    command(editor, "begin")
    assert editor.command(request) == result  # retry cannot mutate the new editor session
    assert not editor.command({**request, "request_id": "two"})["applied"]
    assert editor.state == "calibrating"


def test_cancel_keeps_profile_and_needs_explicit_b(editor):
    original = editor.path.read_bytes()
    command(editor, "begin")
    assert command(editor, "cancel")["applied"]
    assert editor.path.read_bytes() == original and not editor.enabled
    assert editor.snapshot()["last_action"] == "cancel"


def test_failed_disk_save_stays_calibrating(editor, monkeypatch):
    command(editor, "begin")
    original = editor.digest

    def fail(*_args):
        raise OSError("disk full")

    monkeypatch.setattr("quest_trajectory_recorder.calibration_session.atomic_write_json", fail)
    assert not command(editor, "finish", calibration=PROFILE)["applied"]
    assert editor.digest == original and editor.state == "calibrating"


def test_expired_http_request_cannot_apply_later(editor):
    pending = PendingRequest({"action": "begin", "request_id": "expired"}, 0)
    editor.requests.put_nowait(pending)
    editor.drain()
    assert pending.done.is_set() and not pending.result["applied"]
    assert editor.enabled
