import uuid

import pytest

from quest_trajectory_recorder.alignment import Alignment


def command(alignment, action, now=1.0):
    return alignment.command(
        {"action": action, "revision": alignment.revision, "request_id": str(uuid.uuid4())}, now
    )


def prepared():
    alignment = Alignment(None)
    alignment.evidence({"boot": "boot1", "pid": "1", "event": ""}, 1.0)
    alignment.sample([0.0, 1.0, 0.0], 1.0)
    return alignment


def finish_alignment(alignment):
    assert command(alignment, "start")["applied"]
    for i in range(10):
        alignment.sample([i * 0.04, 1.0, 0.0], 1.0)
    assert command(alignment, "finish")["applied"]
    alignment.sample([0.0, 1.0, 0.0], 1.0)
    assert command(alignment, "forward")["applied"]
    for i in range(10):
        alignment.sample([0.0, 1.0, i * 0.04], 1.0)
    assert command(alignment, "finish")["applied"]


def test_alignment_requires_explicit_b_cycle_and_preserves_axes():
    a = prepared()
    finish_alignment(a)
    assert a.valid and not a.enabled
    assert a.calibration.right == [1.0, 0.0, 0.0]
    assert a.calibration.forward == [0.0, 0.0, 1.0]
    a.pause(False)
    assert not a.enabled
    a.pause(True)
    a.pause(False)
    assert a.enabled
    # Ordinary clutch and controller dropout never change the calibrated frame.
    revision = a.revision
    a.pause(True)
    a.sample(None, 2.0)
    a.pause(False)
    assert a.enabled and a.revision == revision


@pytest.mark.parametrize(
    "frame",
    [
        {"boot": "boot1", "pid": "1", "event": "recenter"},
        {"boot": "boot1", "pid": "2", "event": ""},
        {"boot": "boot2", "pid": "1", "event": ""},
    ],
)
def test_frame_change_aborts_capture(frame):
    a = prepared()
    command(a, "start")
    a.evidence(frame, 2.0)
    assert a.state == "required" and not a.valid and not a.points
    assert not command(a, "finish", 2.0)["applied"]


def test_repeated_evidence_and_short_usb_loss_do_not_invalidate():
    a = prepared()
    finish_alignment(a)
    revision = a.revision
    a.evidence(None, 2.0)
    a.tick(3.0)
    a.evidence(dict(a.frame), 4.0)
    assert a.revision == revision
    a.tick(20.0)
    assert not a.valid


def test_stale_and_duplicate_commands_cannot_advance_steps():
    a = prepared()
    request = {"action": "start", "revision": a.revision, "request_id": "one"}
    first = a.command(request, 1.0)
    assert a.command(request, 2.0) == first
    assert not a.command({**request, "request_id": "two"}, 2.0)["applied"]


def test_tracking_loss_and_short_strokes_rejected():
    a = prepared()
    command(a, "start")
    assert not command(a, "finish")["applied"]
    a.sample(None, 1.0)
    assert a.state == "required"
    assert not command(a, "start")["applied"]


def test_start_without_frame_evidence_is_rejected():
    a = Alignment(None)
    a.sample([1.0, 1.0, 1.0], 1.0)
    assert not command(a, "start")["applied"]
