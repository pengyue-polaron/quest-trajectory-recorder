import socket
from pathlib import Path

import zmq

from quest_trajectory_recorder.calibration_session import CalibrationSession
from quest_trajectory_recorder.quest_target_source import DirectQuestTargetSource
from quest_trajectory_recorder.teleop_frame import QuestCalibration

VALID = b"absolute|0.1,1.2,0.3|0,0,0,1|False"
ZERO_PLACEHOLDER = b"absolute|0,0,0|0,0,0,1|False"


def unused_tcp_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def make_source(*, initial_gripper: float = -1.0):
    context = zmq.Context()
    remote_port = unused_tcp_port()
    pause_port = unused_tcp_port()
    while pause_port == remote_port:
        pause_port = unused_tcp_port()
    source = DirectQuestTargetSource(
        context=context,
        host="127.0.0.1",
        remote_port=remote_port,
        pause_port=pause_port,
        calibration=None,
        no_gate=True,
        trajectory_gate_pause="High",
        allow_initial_high=True,
        gripper_mode="toggle",
        initial_gripper=initial_gripper,
        tracking_loss_grace_ms=120.0,
    )
    return context, source


def test_initial_gripper_state_is_configurable_for_held_start_tasks():
    context, source = make_source(initial_gripper=1.0)
    try:
        target = source._update_remote(VALID, received_monotonic_ns=1_000_000_000)
        assert target is not None
        assert target.gripper == 1.0
    finally:
        source.close()
        context.term()


def test_editor_blocks_even_no_gate_and_high(tmp_path: Path):
    context, source = make_source()
    try:
        source.editor = CalibrationSession(tmp_path / "new.json", url="http://localhost/")
        source.editor.calibration = QuestCalibration([0, 0, 0], [1, 0, 0], [0, 0, 1], [0, 1, 0])
        source._update_pause("High")
        sample = source._update_remote(VALID)
        assert sample.tracking_valid and not sample.gate_open
        source.editor.state = "awaiting_b"
        source._update_pause("High")
        assert not source.gate_open
        source._update_pause("Low")
        source._update_pause("High")
        assert source.gate_open
        source.editor.state = "calibrating"
        sample = source._update_remote(VALID)
        assert not sample.gate_open
    finally:
        source.close()
        context.term()


def test_editor_ignores_isolated_zero_placeholder(tmp_path: Path):
    context, source = make_source()
    try:
        source.editor = CalibrationSession(tmp_path / "new.json", url="http://localhost/")
        source._update_remote(VALID, received_monotonic_ns=1_000_000_000)
        source._update_remote(ZERO_PLACEHOLDER, received_monotonic_ns=1_020_000_000)
        assert source.editor.state == "calibrating" and source.editor.live.gate_open
        source._update_remote(ZERO_PLACEHOLDER, received_monotonic_ns=1_150_000_000)
        assert source.editor.state == "calibrating" and not source.editor.live.gate_open
    finally:
        source.close()
        context.term()


def test_isolated_origin_placeholder_does_not_break_tracking():
    context, source = make_source()
    try:
        first = source._update_remote(VALID, received_monotonic_ns=1_000_000_000)
        dropped = source._update_remote(
            ZERO_PLACEHOLDER,
            received_monotonic_ns=1_020_000_000,
        )
        resumed = source._update_remote(
            VALID,
            received_monotonic_ns=1_040_000_000,
        )

        assert first is not None and first.tracking_valid
        assert dropped is None
        assert resumed is not None and resumed.tracking_valid
        assert source.tracking_loss_count == 0
        assert source.consecutive_invalid_count == 0
        assert source.take_events() == []
    finally:
        source.close()
        context.term()


def test_sustained_origin_placeholders_still_publish_safety_hold():
    context, source = make_source()
    try:
        source._update_remote(VALID, received_monotonic_ns=1_000_000_000)
        assert (
            source._update_remote(
                ZERO_PLACEHOLDER,
                received_monotonic_ns=1_010_000_000,
            )
            is None
        )
        invalid = source._update_remote(
            ZERO_PLACEHOLDER,
            received_monotonic_ns=1_140_000_000,
        )

        assert invalid is not None and not invalid.tracking_valid
        assert source.tracking_loss_count == 1
        assert invalid.source_metadata["tracking_invalid_duration_ms"] == 130.0
        assert source.take_events() == [
            "Controller tracking lost; publishing an immediate safety hold."
        ]

        recovered = source._update_remote(
            VALID,
            received_monotonic_ns=1_150_000_000,
        )
        assert recovered is not None and recovered.tracking_valid
        assert source.take_events() == [
            "Controller pose returned; downstream guard is stabilizing and re-anchoring."
        ]
    finally:
        source.close()
        context.term()
