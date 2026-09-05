"""Exercise the actual source process, HTTP editor, and canonical ZMQ stream."""

import copy
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import zmq
from test_calibration_session import PROFILE


def test_live_source_repeated_editor_roundtrips(tmp_path):
    listeners = [socket.socket() for _ in range(7)]
    for listener in listeners:
        listener.bind(("127.0.0.1", 0))
    remote, pause, resolution, gripper, pub, rpc, web = [s.getsockname()[1] for s in listeners]
    for listener in listeners:
        listener.close()
    profile = tmp_path / "test.json"
    profile.write_text(json.dumps(PROFILE))
    url = f"http://127.0.0.1:{web}"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "quest_trajectory_recorder.quest_tracker_hub",
            "--calibration",
            str(profile),
            "--remote-port",
            str(remote),
            "--pause-port",
            str(pause),
            "--resolution-port",
            str(resolution),
            "--gripper-port",
            str(gripper),
            "--target-bind",
            f"tcp://127.0.0.1:{pub}",
            "--source-control-bind",
            f"tcp://127.0.0.1:{rpc}",
            "--web-port",
            str(web),
            "--allow-initial-high",
            "--print-every",
            "0",
        ],
        env={**os.environ, "QUEST_CALIBRATION_DIR": str(tmp_path)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    stop = threading.Event()
    paused = threading.Event()

    def produce():
        with zmq.Context() as context:
            with context.socket(zmq.PUSH) as poses, context.socket(zmq.PUSH) as pauses:
                for channel, port in ((poses, remote), (pauses, pause)):
                    channel.setsockopt(zmq.LINGER, 0)
                    channel.setsockopt(zmq.SNDHWM, 2)
                    channel.connect(f"tcp://127.0.0.1:{port}")
                while not stop.is_set():
                    for channel, payload in (
                        (poses, b"absolute|0.1,1.2,0.3|0,0,0,1|False"),
                        (pauses, b"Low" if paused.is_set() else b"High"),
                    ):
                        try:
                            channel.send(payload, zmq.NOBLOCK)
                        except zmq.Again:
                            pass
                    stop.wait(0.02)

    def get(path):
        with urlopen(url + path, timeout=1) as response:
            return json.load(response)

    def post(payload, origin=None):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        request = Request(
            url + "/editor/command",
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as exc:
            response = exc
        with response:
            return json.load(response)

    def command(action, **extra):
        return post({"action": action, "request_id": str(uuid.uuid4()), **extra})

    producer = threading.Thread(target=produce)
    producer.start()
    try:
        deadline = time.monotonic() + 5
        while True:
            assert process.poll() is None, "source exited during startup"
            try:
                initial = get("/editor/status")
                break
            except URLError:
                assert time.monotonic() < deadline
                time.sleep(0.05)
        assert initial["state"] == "teleop"
        with zmq.Context() as context, context.socket(zmq.SUB) as targets:
            targets.setsockopt(zmq.LINGER, 0)
            targets.setsockopt(zmq.SUBSCRIBE, b"")
            targets.connect(f"tcp://127.0.0.1:{pub}")

            def wait_target(predicate):
                until = time.monotonic() + 3
                while time.monotonic() < until:
                    if targets.poll(100):
                        value = json.loads(targets.recv_multipart()[-1])
                        if "position" in value and predicate(value):
                            return value
                raise AssertionError("Expected canonical target did not arrive")

            wait_target(lambda value: value["gate_open"])
            for index in range(3):
                # First entry uses the same source RPC as the presentation bridge.
                if index == 0:
                    with context.socket(zmq.REQ) as control:
                        control.setsockopt(zmq.LINGER, 0)
                        control.connect(f"tcp://127.0.0.1:{rpc}")
                        control.send_json({"action": "begin", "request_id": str(uuid.uuid4())})
                        assert control.poll(2000)
                        entered = control.recv_json()
                else:
                    entered = command("begin")
                assert entered["applied"]
                revision = entered["editor"]["revision"]
                wait_target(
                    lambda value: (
                        not value["gate_open"] and not value["source_metadata"]["calibration_valid"]
                    )
                )
                time.sleep(0.08)
                assert get("/snapshot")["points"]
                original = profile.read_bytes()
                bad = command("finish", revision=revision, calibration={})
                assert not bad["applied"] and profile.read_bytes() == original
                assert not post(
                    {"action": "cancel", "request_id": "csrf", "revision": revision},
                    origin="http://other.invalid",
                )["applied"]
                data = copy.deepcopy(PROFILE)
                data["origin"]["x"] = 0.2 + index
                finished = command("finish", revision=revision, profile="test", calibration=data)
                assert finished["applied"] and finished["editor"]["state"] == "awaiting_b"
                assert (
                    finished["editor"]["calibration_sha256"]
                    == hashlib.sha256(profile.read_bytes()).hexdigest()
                )
                assert not command("finish", revision=revision, calibration=data)["applied"]
                saved = get("/snapshot")["points"]
                time.sleep(0.1)
                assert get("/snapshot")["points"] == saved  # idle page receives no new poses
                assert get("/editor/status")["state"] == "awaiting_b"
                paused.set()
                wait_target(lambda value: value["source_metadata"].get("pause_state") == "Low")
                paused.clear()
                target = wait_target(
                    lambda value: (
                        value["gate_open"]
                        and value["source_metadata"]["calibration_revision"]
                        == finished["editor"]["revision"]
                    )
                )
                assert abs(target["position"][0] - (0.1 - data["origin"]["x"])) < 1e-6
                assert process.poll() is None  # one process owns input throughout
            old = profile.read_bytes()
            entered = command("begin")
            assert command("cancel", revision=entered["editor"]["revision"])["applied"]
            assert profile.read_bytes() == old
    finally:
        stop.set()
        producer.join(3)
        process.terminate()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(3)
