import json

from embodied_ops.teleop import TeleopCommandResult, TeleopFeedback
from foxglove.messages import PoseInFrame
from websockets.sync.client import connect

from quest_trajectory_recorder.foxglove_bridge import (
    DEFAULT_FOXGLOVE_LAYOUT_ID,
    SERVICE_COMMANDS,
    CommandRouter,
    FoxgloveTeleopBridge,
    build_services,
    diagnostic_array,
    feedback_telemetry,
    foxglove_deep_link,
    pose_message,
)

EXPECTED_TOPICS = {
    "/teleop/agent_view",
    "/teleop/wrist_camera",
    "/teleop/eef_pose",
    "/teleop/desired_eef_pose",
    "/teleop/controller_target",
    "/teleop/telemetry",
    "/teleop/target",
    "/teleop/source_status",
    "/teleop/diagnostics",
}


def test_command_services_cover_navigation_and_safety() -> None:
    def acknowledge(command):
        return TeleopCommandResult(
            request_id=command.request_id,
            command=command.command,
            accepted=True,
            applied=True,
            backend="unit",
        )

    router = CommandRouter(acknowledge)
    services = build_services(router)
    assert {service.name for service in services} == set(SERVICE_COMMANDS)
    response = router.execute("next_episode")
    assert response["accepted"] is True
    assert response["applied"] is True
    assert response["backend"] == "unit"


def test_feedback_is_flattened_for_foxglove_plots() -> None:
    feedback = TeleopFeedback(
        backend="unit",
        episode_id="episode-2",
        frame_index=4,
        status="running",
        target_seq=8,
        target_age_ms=3.5,
        gate_open=True,
        recording=False,
        eef_position=[0.1, 0.2, 0.3],
        gripper=-1.0,
        action=[0.4, 0.5],
    )
    telemetry = feedback_telemetry(feedback)
    assert telemetry["eef_z_m"] == 0.3
    assert telemetry["action_0"] == 0.4
    assert telemetry["action_2"] is None


def test_pose_message_uses_foxglove_vector_position() -> None:
    message = pose_message(
        timestamp_ns=10,
        frame_id="world",
        position=[1.0, 2.0, 3.0],
        quaternion_xyzw=[0.0, 0.0, 0.0, 1.0],
    )
    assert isinstance(message, PoseInFrame)


def test_diagnostics_explain_tracking_loss_and_safe_hold() -> None:
    feedback = TeleopFeedback(
        backend="robotteambench_maniskill",
        episode_id="episode-2",
        frame_index=4,
        status="holding",
        target_seq=8,
        target_age_ms=3.5,
        gate_open=False,
        recording=False,
        eef_position=[0.1, 0.2, 0.3],
        gripper=-1.0,
        action=[0.0] * 7,
        diagnostics={
            "mapping_reason": "tracking_invalid",
            "guard_state": "holding",
            "recovery_frames": 0,
            "recovery_frames_required": 6,
            "jump_rejections": 1,
        },
    )
    message = diagnostic_array(
        timestamp_ns=123,
        source_status={
            "state": "tracking_invalid",
            "adb_connected": True,
            "app_resumed": True,
            "controller_stream_online": True,
            "tracking_valid": False,
            "gate_open": True,
            "raw_age_ms": 12.0,
            "tracking_loss_count": 2,
            "last_invalid_reason": "zero_position",
        },
        source_age_sec=0.1,
        feedback=feedback,
        feedback_age_sec=0.02,
    )
    statuses = {status["name"]: status for status in message["status"]}
    assert statuses["Teleop/Workflow"]["level"] == 1
    assert "tracking invalid" in statuses["Teleop/Workflow"]["message"]
    assert set(statuses) == {"Teleop/Workflow", "Teleop/Safety"}
    assert statuses["Teleop/Safety"]["hardware_id"] == "quest-teleop"
    assert {item["key"] for item in statuses["Teleop/Safety"]["values"]} >= {
        "Guard",
        "Rejected jumps",
    }


def test_diagnostics_mark_missing_source_as_stale() -> None:
    message = diagnostic_array(
        timestamp_ns=123,
        source_status=None,
        source_age_sec=None,
        feedback=None,
        feedback_age_sec=None,
    )
    workflow = message["status"][0]
    assert workflow["level"] == 3
    assert "Robot motion frozen" in workflow["message"]


def test_deep_link_selects_organization_layout_and_local_bridge() -> None:
    link = foxglove_deep_link(
        websocket_url="ws://127.0.0.1:8765",
        layout_id=DEFAULT_FOXGLOVE_LAYOUT_ID,
    )
    assert link.startswith("https://app.foxglove.dev/~/view?")
    assert "ds=foxglove-websocket" in link
    assert "ds.url=ws%3A%2F%2F127.0.0.1%3A8765" in link
    assert f"layoutId={DEFAULT_FOXGLOVE_LAYOUT_ID}" in link
    assert "openIn=desktop" in link


def test_live_gateway_advertises_only_canonical_topics_and_services() -> None:
    bridge = FoxgloveTeleopBridge(
        target_endpoint="inproc://foxglove-test-target",
        feedback_endpoint="inproc://foxglove-test-feedback",
        command_endpoint="inproc://foxglove-test-command",
        host="127.0.0.1",
        port=0,
    )
    messages = {}
    try:
        with connect(
            f"ws://127.0.0.1:{bridge.port}",
            subprotocols=["foxglove.sdk.v1"],
            open_timeout=2,
        ) as websocket:
            for _ in range(8):
                message = websocket.recv(timeout=2)
                if isinstance(message, str):
                    value = json.loads(message)
                    messages[value["op"]] = value
                if {"serverInfo", "advertise", "advertiseServices"} <= messages.keys():
                    break
    finally:
        bridge.close()

    assert messages["serverInfo"]["capabilities"] == ["services"]
    assert {
        channel["topic"] for channel in messages["advertise"]["channels"]
    } == EXPECTED_TOPICS
    diagnostics_channel = next(
        channel
        for channel in messages["advertise"]["channels"]
        if channel["topic"] == "/teleop/diagnostics"
    )
    assert diagnostics_channel["schemaName"] == "diagnostic_msgs/msg/DiagnosticArray"
    assert {
        service["name"] for service in messages["advertiseServices"]["services"]
    } == set(SERVICE_COMMANDS)
