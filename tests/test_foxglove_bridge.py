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
    assert {
        service["name"] for service in messages["advertiseServices"]["services"]
    } == set(SERVICE_COMMANDS)
