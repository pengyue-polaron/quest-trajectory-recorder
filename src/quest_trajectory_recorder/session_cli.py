"""Agent-friendly lifecycle control for one local Quest task at a time."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence
from urllib.error import URLError
from urllib.request import urlopen

import zmq
from embodied_ops.teleop.zmq_transport import TeleopFeedbackReceiver

from .device_cli import status_payload as device_status_payload
from .foxglove_probe import is_foxglove_server_ready

ROOT = Path(__file__).resolve().parents[2]
STATE_SCHEMA = "quest.agent_session/v1"
DEFAULT_FOXGLOVE_PORT = 8765
DEFAULT_CALIBRATION_PORT = 8766
DEFAULT_WAIT_SECONDS = 180.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def runtime_dir() -> Path:
    configured = os.environ.get("QUEST_TELEOP_RUNTIME_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(tempfile.gettempdir()) / f"quest-teleop-{os.getuid()}"


def _state_path() -> Path:
    return runtime_dir() / "session.json"


def _log_path() -> Path:
    return runtime_dir() / "session.log"


@contextmanager
def _state_lock() -> Iterator[None]:
    directory = runtime_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "session.lock").open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_state() -> dict[str, Any] | None:
    path = _state_path()
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return (
        value if isinstance(value, dict) and value.get("schema_version") == STATE_SCHEMA else None
    )


def _write_state(state: dict[str, Any]) -> None:
    directory = runtime_dir()
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f"session.{os.getpid()}.tmp"
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(_state_path())


def _process_details(pid: int) -> tuple[bool, str]:
    if pid <= 0:
        return False, ""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat=", "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    line = result.stdout.strip()
    if result.returncode != 0 or not line:
        return False, ""
    parts = line.split(maxsplit=1)
    process_state = parts[0]
    command = parts[1] if len(parts) == 2 else ""
    return "Z" not in process_state, command


def _managed_process(state: dict[str, Any]) -> tuple[bool, str]:
    try:
        pid = int(state["pid"])
    except (KeyError, TypeError, ValueError):
        return False, ""
    alive, command = _process_details(pid)
    markers = state.get("process_markers", [])
    owned = (
        alive and isinstance(markers, list) and any(str(marker) in command for marker in markers)
    )
    return bool(owned), command


def _option_value(arguments: Sequence[str], option: str, default: str) -> str:
    for index, argument in enumerate(arguments):
        if argument == option and index + 1 < len(arguments):
            return str(arguments[index + 1])
        prefix = f"{option}="
        if argument.startswith(prefix):
            return argument[len(prefix) :]
    return default


def _session_arguments(arguments: Sequence[str]) -> list[str]:
    result = list(arguments)
    if result and result[0] == "--":
        result.pop(0)
    forbidden = {"--backend", "--profile", "--task"}
    if any(argument.split("=", 1)[0] in forbidden for argument in result):
        raise ValueError("backend, profile, and task are owned by the lifecycle command")
    return result


def _build_start_state(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    session_id = str(uuid.uuid4())
    log_path = _log_path()
    if args.mode == "calibration":
        port = int(os.environ.get("CALIBRATION_WEB_PORT", DEFAULT_CALIBRATION_PORT))
        command = [str(ROOT / "scripts/run_calibration.sh"), args.profile]
        state = {
            "schema_version": STATE_SCHEMA,
            "session_id": session_id,
            "kind": "calibration",
            "label": "Quest calibration",
            "profile": args.profile,
            "service_url": f"http://127.0.0.1:{port}/",
            "log_path": str(log_path),
            "process_markers": [
                "quest_trajectory_recorder.live3d",
                "run_calibration.sh",
                "run_live3d.sh",
            ],
        }
        return state, command

    extra = _session_arguments(args.session_args)
    backend = "mujoco" if args.mode == "forcevla" else "maniskill"
    command = [
        str(ROOT / "scripts/run_quest_session.sh"),
        "--backend",
        backend,
        "--profile",
        args.profile,
    ]
    if backend == "maniskill":
        command.extend(("--task", args.task))
    command.extend(extra)
    foxglove_port = int(_option_value(extra, "--foxglove-port", str(DEFAULT_FOXGLOVE_PORT)))
    feedback_endpoint = _option_value(extra, "--feedback-endpoint", "tcp://127.0.0.1:8131")
    state = {
        "schema_version": STATE_SCHEMA,
        "session_id": session_id,
        "kind": "teleop",
        "label": "ForceVLA" if backend == "mujoco" else "ManiSkill",
        "backend": backend,
        "profile": args.profile,
        "task": args.task if backend == "maniskill" else None,
        "synthetic": "--synthetic" in extra,
        "service_url": f"ws://127.0.0.1:{foxglove_port}",
        "feedback_endpoint": feedback_endpoint,
        "log_path": str(log_path),
        "process_markers": ["run_quest_session.sh"],
    }
    return state, command


def _http_ready(url: str, timeout: float = 0.25) -> bool:
    try:
        with urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed local runtime URL
            return response.status == 200
    except (OSError, TimeoutError, URLError, ValueError):
        return False


def _feedback_summary(feedback: Any) -> dict[str, Any]:
    return {
        "backend": feedback.backend,
        "episode_id": feedback.episode_id,
        "frame_index": feedback.frame_index,
        "status": feedback.status,
        "streaming": feedback.gate_open,
        "recording": feedback.recording,
        "target_age_ms": feedback.target_age_ms,
    }


def _wait_until_ready(
    state: dict[str, Any], process: subprocess.Popen[Any], timeout: float
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    if state["kind"] == "calibration":
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return None
            if _http_ready(state["service_url"]):
                return {}
            time.sleep(0.1)
        return None

    context = zmq.Context()
    receiver = TeleopFeedbackReceiver(context, state["feedback_endpoint"])
    latest = None
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return None
            candidate = receiver.newest()
            if candidate is not None:
                latest = candidate[0]
            foxglove_ready = is_foxglove_server_ready(state["service_url"], timeout_sec=0.1)
            if latest is not None and foxglove_ready:
                return _feedback_summary(latest)
            time.sleep(0.1)
        return None
    finally:
        receiver.close()
        context.term()


def _tail(path: Path, lines: int) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def _same_request(existing: dict[str, Any], requested: dict[str, Any]) -> bool:
    keys = ("kind", "backend", "profile", "task", "synthetic", "command")
    return all(existing.get(key) == requested.get(key) for key in keys)


def _terminate(state: dict[str, Any], timeout: float) -> bool:
    managed, _command = _managed_process(state)
    if not managed:
        return True
    pid = int(state["pid"])
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_details(pid)[0]:
            return True
        time.sleep(0.1)
    if _managed_process(state)[0]:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return not _process_details(pid)[0]


def _wait_for_existing_ready(state: dict[str, Any], timeout: float) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = _read_state()
        if current is None or current.get("session_id") != state.get("session_id"):
            return False, "replaced"
        managed, _command = _managed_process(current)
        if not managed:
            return False, "process exited"
        lifecycle_state = str(current.get("status", "unknown"))
        if lifecycle_state == "ready":
            snapshot = status_snapshot(probe_timeout=min(0.6, max(0.05, timeout)))
            return snapshot["state"] == "ready", str(snapshot["state"])
        if lifecycle_state in {"failed", "stopped"}:
            return False, lifecycle_state
        time.sleep(0.1)
    return False, "still starting"


def start(args: argparse.Namespace) -> int:
    try:
        requested, command = _build_start_state(args)
    except (TypeError, ValueError) as exc:
        print(f"START FAILED: {exc}")
        return 2
    requested["command"] = command
    runtime_dir().mkdir(parents=True, exist_ok=True)
    matching_existing = None
    with _state_lock():
        existing = _read_state()
        if existing is not None and _managed_process(existing)[0]:
            if _same_request(existing, requested):
                matching_existing = existing
            else:
                print(
                    f"START FAILED: {existing.get('label', 'task')} is already running "
                    f"(pid {existing.get('pid')}); run `just stop` first."
                )
                return 1
        else:
            log_path = Path(requested["log_path"])
            try:
                with log_path.open("w", encoding="utf-8") as log_file:
                    process = subprocess.Popen(  # noqa: S603 - fixed launcher plus argument vector
                        command,
                        cwd=ROOT,
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
            except OSError as exc:
                print(f"START FAILED: could not launch {requested['label']}: {exc}")
                return 1
            requested.update(
                {
                    "pid": process.pid,
                    "status": "starting",
                    "started_at": _utc_now(),
                }
            )
            _write_state(requested)

    if matching_existing is not None:
        ready, detail = _wait_for_existing_ready(matching_existing, args.wait_seconds)
        if ready:
            print(
                f"ALREADY RUNNING {matching_existing['label']} "
                f"pid={matching_existing['pid']} url={matching_existing['service_url']}"
            )
            return 0
        print(
            f"START FAILED: {matching_existing['label']} pid={matching_existing['pid']} "
            f"is {detail}; inspect `just status-json` or `just logs`."
        )
        return 1

    startup_error = None
    try:
        feedback = _wait_until_ready(requested, process, args.wait_seconds)
    except (OSError, ValueError, zmq.ZMQError) as exc:
        feedback = None
        startup_error = str(exc)
    if feedback is None:
        exited = process.poll() is not None
        _terminate(requested, timeout=8.0)
        with _state_lock():
            current = _read_state()
            if (
                current is not None
                and current.get("session_id") == requested["session_id"]
                and current.get("status") != "stopped"
            ):
                current.update(
                    {
                        "status": "failed",
                        "stopped_at": _utc_now(),
                        "failure": (
                            "startup_probe_error"
                            if startup_error
                            else "process_exited"
                            if exited
                            else "startup_timeout"
                        ),
                    }
                )
                if startup_error:
                    current["failure_detail"] = startup_error
                _write_state(current)
        reason = (
            f"readiness probe failed: {startup_error}"
            if startup_error
            else "process exited"
            if exited
            else f"not ready after {args.wait_seconds:g}s"
        )
        print(f"START FAILED: {requested['label']} {reason}")
        recent = _tail(Path(requested["log_path"]), 20)
        if recent:
            print(recent)
        return 1

    with _state_lock():
        current = _read_state()
        if current is None or current.get("session_id") != requested["session_id"]:
            return 1
        current.update({"status": "ready", "ready_at": _utc_now()})
        if feedback:
            current["last_feedback"] = feedback
        _write_state(current)
        requested = current
    detail = f" profile={requested['profile']}"
    if requested.get("task"):
        detail += f" task={requested['task']}"
    print(f"READY {requested['label']} pid={requested['pid']}{detail}")
    print(f"URL {requested['service_url']}")
    return 0


def _probe_feedback(endpoint: str, timeout: float) -> dict[str, Any] | None:
    context = zmq.Context()
    receiver = TeleopFeedbackReceiver(context, endpoint)
    deadline = time.monotonic() + timeout
    latest = None
    try:
        while time.monotonic() < deadline:
            candidate = receiver.newest()
            if candidate is not None:
                latest = candidate[0]
            if latest is not None:
                return _feedback_summary(latest)
            time.sleep(0.05)
        return None
    finally:
        receiver.close()
        context.term()


def status_snapshot(*, probe_timeout: float = 0.6) -> dict[str, Any]:
    state = _read_state()
    if state is None:
        return {"schema_version": STATE_SCHEMA, "state": "stopped", "running": False}
    managed, _command = _managed_process(state)
    snapshot = {
        "schema_version": STATE_SCHEMA,
        "state": "stopped",
        "running": managed,
        "label": state.get("label"),
        "kind": state.get("kind"),
        "backend": state.get("backend"),
        "profile": state.get("profile"),
        "task": state.get("task"),
        "pid": state.get("pid"),
        "service_url": state.get("service_url"),
        "log_path": state.get("log_path"),
    }
    if not managed:
        snapshot["last_state"] = state.get("status", "stopped")
        return snapshot

    if state["kind"] == "calibration":
        service_ready = _http_ready(state["service_url"], timeout=probe_timeout)
        snapshot["calibration_ready"] = service_ready
    else:
        service_ready = is_foxglove_server_ready(state["service_url"], timeout_sec=probe_timeout)
        feedback = _probe_feedback(state["feedback_endpoint"], probe_timeout)
        snapshot["foxglove_ready"] = service_ready
        snapshot["backend_ready"] = feedback is not None
        snapshot["feedback"] = feedback
        service_ready = service_ready and feedback is not None

    if state.get("synthetic"):
        quest = None
        quest_ready = True
    else:
        try:
            quest = device_status_payload()
            quest_ready = bool(quest["ready"])
        except (OSError, subprocess.SubprocessError, KeyError, TypeError):
            quest = None
            quest_ready = False
    snapshot["quest"] = quest
    snapshot["state"] = "ready" if service_ready and quest_ready else "degraded"
    return snapshot


def show_status(args: argparse.Namespace) -> int:
    snapshot = status_snapshot(probe_timeout=args.probe_timeout)
    if args.as_json:
        print(json.dumps(snapshot, indent=2, sort_keys=True))
    elif not snapshot["running"]:
        print("STOPPED no managed Quest task")
    else:
        feedback = snapshot.get("feedback") or {}
        extras = []
        if feedback:
            extras.append(f"backend={feedback['status']}")
            extras.append("streaming" if feedback["streaming"] else "held")
            extras.append("recording" if feedback["recording"] else "not-recording")
        print(
            f"{snapshot['state'].upper()} {snapshot['label']} pid={snapshot['pid']} "
            + " ".join(extras)
        )
        print(f"URL {snapshot['service_url']}")
    return 0 if snapshot["state"] == "ready" else 1


def stop(args: argparse.Namespace) -> int:
    with _state_lock():
        state = _read_state()
        if state is None:
            print("STOPPED no managed Quest task")
            return 0
        managed, command = _managed_process(state)
        if not managed:
            if command:
                print("STOP FAILED: saved PID belongs to an unmanaged process; nothing was killed")
                return 1
            state.update({"status": "stopped", "stopped_at": _utc_now()})
            _write_state(state)
            print("STOPPED no managed Quest task")
            return 0

    if not _terminate(state, timeout=args.wait_seconds):
        print(f"STOP FAILED: {state.get('label', 'task')} pid={state.get('pid')} is still alive")
        return 1
    with _state_lock():
        current = _read_state()
        if current is not None and current.get("session_id") == state.get("session_id"):
            current.update({"status": "stopped", "stopped_at": _utc_now()})
            _write_state(current)
    print(f"STOPPED {state.get('label', 'Quest task')} pid={state.get('pid')}")
    return 0


def show_logs(args: argparse.Namespace) -> int:
    state = _read_state()
    path = Path(state["log_path"]) if state and state.get("log_path") else _log_path()
    content = _tail(path, args.lines)
    if not content:
        print(f"No session log at {path}")
        return 1
    print(content)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    start_parser = subparsers.add_parser("start", help="Start one task and wait until it is ready")
    start_parser.add_argument("mode", choices=("calibration", "forcevla", "maniskill"))
    start_parser.add_argument("--profile", default="lab")
    start_parser.add_argument("--task", choices=("cube_sort", "bar_carry"), default="cube_sort")
    start_parser.add_argument("--wait-seconds", type=float, default=DEFAULT_WAIT_SECONDS)

    status_parser = subparsers.add_parser("status", help="Probe the managed task")
    status_parser.add_argument("--json", action="store_true", dest="as_json")
    status_parser.add_argument("--probe-timeout", type=float, default=0.6)

    stop_parser = subparsers.add_parser("stop", help="Stop the complete managed task")
    stop_parser.add_argument("--wait-seconds", type=float, default=12.0)

    logs_parser = subparsers.add_parser("logs", help="Show the latest task log")
    logs_parser.add_argument("--lines", type=int, default=80)
    args, remaining = parser.parse_known_args(argv)
    if args.command_name == "start":
        args.session_args = remaining
    elif remaining:
        parser.error(f"unrecognized arguments: {' '.join(remaining)}")
    if hasattr(args, "wait_seconds") and args.wait_seconds <= 0:
        parser.error("--wait-seconds must be positive")
    if hasattr(args, "probe_timeout") and args.probe_timeout <= 0:
        parser.error("--probe-timeout must be positive")
    if hasattr(args, "lines") and args.lines <= 0:
        parser.error("--lines must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command_name == "start":
        return start(args)
    if args.command_name == "status":
        return show_status(args)
    if args.command_name == "stop":
        return stop(args)
    return show_logs(args)


if __name__ == "__main__":
    raise SystemExit(main())
