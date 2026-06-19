#!/usr/bin/env python3
"""Teleoperate a LIBERO / robosuite Panda from the recovered Quest controller stream.

This intentionally bypasses the full Open-Teach process graph. The Quest APK is
already producing a reliable world-frame controller pose; this process consumes
that stream and converts it into robosuite OSC_POSE actions for LIBERO.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import zmq

from .live3d import DEFAULT_GRIPPER_PORT, setup_adb_reverse
from .receiver import DEFAULT_PORTS, parse_remote_text

try:  # Optional at import time; required only when running LIBERO.
    import numpy as np
except ModuleNotFoundError:  # pragma: no cover - exercised only without numpy installed
    np = None  # type: ignore[assignment]


AXIS_VECTORS = {
    "+x": (1.0, 0.0, 0.0),
    "-x": (-1.0, 0.0, 0.0),
    "+y": (0.0, 1.0, 0.0),
    "-y": (0.0, -1.0, 0.0),
    "+z": (0.0, 0.0, 1.0),
    "-z": (0.0, 0.0, -1.0),
}
AXIS_NAMES = tuple(AXIS_VECTORS)


@dataclass
class QuestCalibration:
    origin: Any
    right: Any
    forward: Any
    up: Any
    rotation_neutral: Any | None = None


@dataclass
class TeleopHome:
    quest_pos: Any
    quest_rot: Any
    eef_pos: Any
    eef_rot: Any
    target_pos: Any
    target_rot: Any


@dataclass
class DebugSnapshot:
    task: str
    step: int
    gate_open: bool
    pause_state: str | None
    gripper: float
    eef_pos: Any
    eef_rot: Any | None
    target_pos: Any | None
    target_rot: Any | None
    quest_pos: Any | None
    quest_delta: Any | None
    action: Any
    remote_count: int
    remote_age: float | None
    flag: bool | None
    homed: bool


def _require_numpy() -> Any:
    if np is None:
        raise RuntimeError("numpy is required for quest-libero-teleop; run inside the LIBERO / openpi environment.")
    return np


def _axis(name: str) -> Any:
    npx = _require_numpy()
    return npx.asarray(AXIS_VECTORS[name], dtype=float)


def resolve_gripper_axis(name: str, initial_eef_rot: Any) -> str:
    """Return the EEF local axis used for the debug gripper/approach arrow."""
    if name != "auto":
        return name
    npx = _require_numpy()
    world_down = npx.asarray([0.0, 0.0, -1.0], dtype=float)
    scores = {
        axis_name: float((npx.asarray(initial_eef_rot, dtype=float) @ _axis(axis_name)) @ world_down)
        for axis_name in AXIS_NAMES
    }
    return max(scores, key=scores.get)


def _norm(v: Any) -> Any:
    npx = _require_numpy()
    v = npx.asarray(v, dtype=float)
    length = float(npx.linalg.norm(v))
    if length <= 1e-12:
        return v * 0.0
    return v / length


def quat_xyzw_to_matrix(quat: Any) -> Any:
    """Convert xyzw quaternion to a 3x3 rotation matrix."""
    npx = _require_numpy()
    qx, qy, qz, qw = [float(v) for v in quat]
    length = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    if length <= 1e-12:
        return npx.eye(3)
    qx, qy, qz, qw = qx / length, qy / length, qz / length, qw / length
    return npx.asarray(
        [
            [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qw * qz), 2 * (qx * qz + qw * qy)],
            [2 * (qx * qy + qw * qz), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qw * qx)],
            [2 * (qx * qz - qw * qy), 2 * (qy * qz + qw * qx), 1 - 2 * (qx * qx + qy * qy)],
        ],
        dtype=float,
    )


def matrix_to_rotvec(matrix: Any) -> Any:
    """Convert a rotation matrix to an axis-angle rotation vector."""
    npx = _require_numpy()
    matrix = npx.asarray(matrix, dtype=float)
    cos_angle = max(-1.0, min(1.0, (float(npx.trace(matrix)) - 1.0) * 0.5))
    angle = math.acos(cos_angle)
    if angle < 1e-8:
        return npx.zeros(3)
    if abs(math.pi - angle) < 1e-4:
        # Stable enough near pi for teleop; exact axis sign is not critical after clipping.
        axis = npx.sqrt(npx.maximum(npx.diag(matrix) + 1.0, 0.0) / 2.0)
        axis[0] = math.copysign(axis[0], matrix[2, 1] - matrix[1, 2])
        axis[1] = math.copysign(axis[1], matrix[0, 2] - matrix[2, 0])
        axis[2] = math.copysign(axis[2], matrix[1, 0] - matrix[0, 1])
        return _norm(axis) * angle
    axis = npx.asarray(
        [matrix[2, 1] - matrix[1, 2], matrix[0, 2] - matrix[2, 0], matrix[1, 0] - matrix[0, 1]],
        dtype=float,
    ) / (2.0 * math.sin(angle))
    return axis * angle


def current_grip_site_rot(env: Any) -> Any:
    """Return robosuite's controlled grip-site orientation, not the hand body orientation."""
    npx = _require_numpy()
    site_id = env.robots[0].eef_site_id
    return npx.asarray(env.sim.data.site_xmat[site_id], dtype=float).reshape(3, 3).copy()


def load_quest_calibration(path: Path | None) -> QuestCalibration | None:
    if path is None or not path.exists():
        return None
    npx = _require_numpy()
    data = json.loads(path.read_text())
    try:
        return QuestCalibration(
            origin=npx.asarray([data["origin"][k] for k in ("x", "y", "z")], dtype=float),
            right=_norm([data["right"][k] for k in ("x", "y", "z")]),
            forward=_norm([data["forward"][k] for k in ("x", "y", "z")]),
            up=_norm([data["up"][k] for k in ("x", "y", "z")]),
            rotation_neutral=(
                npx.asarray([data["rotation"]["neutralQuat"][k] for k in ("x", "y", "z", "w")], dtype=float)
                if data.get("rotation", {}).get("neutralQuat")
                else None
            ),
        )
    except KeyError as exc:
        raise ValueError(f"Invalid calibration file {path}: missing {exc}") from exc


def quest_pos_to_teleop(pos: Any, calibration: QuestCalibration | None) -> Any:
    npx = _require_numpy()
    pos = npx.asarray(pos, dtype=float)
    if calibration is None:
        # Fallback matching the live viewer before browser calibration.
        return npx.asarray([pos[0], pos[2], pos[1]], dtype=float)
    delta = pos - calibration.origin
    return npx.asarray(
        [float(delta @ calibration.right), float(delta @ calibration.forward), float(delta @ calibration.up)],
        dtype=float,
    )


def quest_rotation_to_teleop_matrix(quat: Any, calibration: QuestCalibration | None) -> Any:
    npx = _require_numpy()
    quest_rot = quat_xyzw_to_matrix(quat)
    if calibration is None:
        return quest_rot
    quest_to_teleop = npx.vstack([calibration.right, calibration.forward, calibration.up])
    return quest_to_teleop @ quest_rot


def build_teleop_to_libero(right_axis: str, forward_axis: str, up_axis: str) -> Any:
    """Return a matrix mapping [right, forward, up] into LIBERO world xyz."""
    npx = _require_numpy()
    matrix = npx.column_stack([_axis(right_axis), _axis(forward_axis), _axis(up_axis)])
    if abs(float(npx.linalg.det(matrix))) < 1e-6:
        raise ValueError("LIBERO right/forward/up axes must be orthogonal and non-degenerate")
    return matrix


def setup_libero_imports(openpi_root: Path, config_dir: Path) -> None:
    libero_src = openpi_root / "third_party" / "libero"
    libero_root = libero_src / "libero" / "libero"
    if not libero_root.exists():
        raise FileNotFoundError(f"LIBERO tree not found under {openpi_root}")
    if str(libero_src) not in sys.path:
        sys.path.insert(0, str(libero_src))
    if str(openpi_root) not in sys.path:
        sys.path.insert(0, str(openpi_root))

    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.yaml"
    if not config_file.exists():
        config_file.write_text(
            "\n".join(
                [
                    f"benchmark_root: {libero_root}",
                    f"bddl_files: {libero_root / 'bddl_files'}",
                    f"init_states: {libero_root / 'init_files'}",
                    f"datasets: {openpi_root.parent / 'libero_cam_rlds'}",
                    f"assets: {libero_root / 'assets'}",
                ]
            )
            + "\n"
        )
    os.environ.setdefault("LIBERO_CONFIG_PATH", str(config_dir))


def make_libero_env(args: argparse.Namespace) -> tuple[Any, str, Any | None]:
    setup_libero_imports(Path(args.openpi_root).expanduser(), Path(args.libero_config_dir).expanduser())
    from libero.libero import benchmark, get_libero_path  # type: ignore
    from libero.libero.envs import TASK_MAPPING  # type: ignore
    import libero.libero.envs.bddl_utils as BDDLUtils  # type: ignore
    from robosuite import load_controller_config  # type: ignore

    benchmark_dict = benchmark.get_benchmark_dict()
    task_suite = benchmark_dict[args.task_suite_name]()
    task = task_suite.get_task(args.task_id)
    task_bddl_file = Path(get_libero_path("bddl_files")) / task.problem_folder / task.bddl_file
    problem_info = BDDLUtils.get_problem_info(str(task_bddl_file))
    controller_config = load_controller_config(default_controller="OSC_POSE")
    env = TASK_MAPPING[problem_info["problem_name"]](
        bddl_file_name=str(task_bddl_file),
        robots=["Panda"],
        controller_configs=controller_config,
        has_renderer=not args.offscreen,
        has_offscreen_renderer=args.offscreen,
        render_camera=args.camera,
        ignore_done=True,
        use_camera_obs=False,
        reward_shaping=True,
        control_freq=args.control_freq,
    )
    env.seed(args.seed)
    task_description = getattr(task, "language", problem_info.get("language_instruction", task.name))

    init_states = None
    if args.init_state_index >= 0:
        try:
            import torch  # type: ignore

            init_states_path = Path(get_libero_path("init_states")) / task.problem_folder / task.init_states_file
            init_states = torch.load(init_states_path, weights_only=False)
        except Exception as exc:  # noqa: BLE001 - optional convenience only
            print(f"Warning: could not load LIBERO init states; using env.reset(): {exc}", flush=True)
    return env, str(task_description), init_states


def valid_remote(remote: dict[str, Any] | None) -> bool:
    if not remote:
        return False
    return any(abs(float(v)) > 1e-8 for v in remote["position"])


def newest_from_socket(socket: zmq.Socket) -> bytes | None:
    payload = None
    while True:
        try:
            payload = socket.recv(flags=zmq.NOBLOCK)
        except zmq.Again:
            return payload


def compute_action(
    *,
    remote: dict[str, Any],
    obs: dict[str, Any],
    calibration: QuestCalibration | None,
    teleop_to_libero: Any,
    home: TeleopHome,
    current_eef_rot: Any,
    position_scale: float,
    position_action_gain: float,
    rotation_action_gain: float,
    smoothing: float,
    max_action: float,
    gripper: float,
    orientation: bool,
) -> tuple[Any, TeleopHome]:
    npx = _require_numpy()
    quest_pos = quest_pos_to_teleop(remote["position"], calibration)
    quest_delta = quest_pos - home.quest_pos
    target_pos = home.eef_pos + teleop_to_libero @ (quest_delta * position_scale)

    if smoothing > 0.0:
        alpha = max(0.0, min(0.98, smoothing))
        target_pos = home.target_pos * alpha + target_pos * (1.0 - alpha)

    curr_pos = npx.asarray(obs["robot0_eef_pos"], dtype=float)
    pos_action = (target_pos - curr_pos) * position_action_gain

    if orientation:
        quest_rot = quest_rotation_to_teleop_matrix(remote["rotation"], calibration)
        quest_rel = quest_rot @ home.quest_rot.T
        robot_rel = teleop_to_libero @ quest_rel @ teleop_to_libero.T
        target_rot = robot_rel @ home.eef_rot
        if smoothing > 0.0:
            # Keep rotation smoothing simple and stable: smooth the resulting rotvec command, not the matrix.
            pass
        curr_rot = npx.asarray(current_eef_rot, dtype=float)
        # robosuite OSC_POSE applies delta as R_delta @ current_orientation.
        rot_action = matrix_to_rotvec(target_rot @ curr_rot.T) * rotation_action_gain
    else:
        target_rot = home.target_rot
        rot_action = npx.zeros(3)

    action = npx.concatenate([pos_action, rot_action, [gripper]]).astype(float)
    action = npx.clip(action, -max_action, max_action)
    home.target_pos = target_pos
    home.target_rot = target_rot
    return action, home


def make_home(
    *,
    mode: str,
    qpos: Any,
    qrot: Any,
    initial_eef_pos: Any,
    initial_eef_rot: Any,
    current_eef_pos: Any,
    current_eef_rot: Any,
) -> TeleopHome:
    npx = _require_numpy()
    if mode == "calibration-origin":
        home_qpos = npx.zeros(3)
        home_eef_pos = initial_eef_pos.copy()
        home_eef_rot = initial_eef_rot.copy()
    else:
        home_qpos = qpos
        home_eef_pos = current_eef_pos.copy()
        home_eef_rot = current_eef_rot.copy()
    return TeleopHome(home_qpos, qrot, home_eef_pos, home_eef_rot, home_eef_pos.copy(), home_eef_rot.copy())



def _fmt_vec(values: Any | None) -> str:
    if values is None:
        return "--"
    npx = _require_numpy()
    values = npx.asarray(values, dtype=float).reshape(-1)
    return "[" + " ".join(f"{v:+.3f}" for v in values[:3]) + "]"


def project_world_to_pixel(env: Any, point: Any, camera_name: str, width: int, height: int) -> tuple[int, int] | None:
    """Project a MuJoCo world point into the OpenCV-rendered LIBERO image."""
    npx = _require_numpy()
    cam_id = env.sim.model.camera_name2id(camera_name)
    cam_pos = npx.asarray(env.sim.data.cam_xpos[cam_id], dtype=float)
    cam_rot = npx.asarray(env.sim.data.cam_xmat[cam_id], dtype=float).reshape(3, 3)

    # MuJoCo uses OpenGL camera axes (x-right, y-up, z-back). Convert the
    # camera-to-world frame to OpenCV convention (x-right, y-down, z-forward).
    cv_rot = cam_rot.copy()
    cv_rot[:, 1] *= -1.0
    cv_rot[:, 2] *= -1.0
    p_cam = cv_rot.T @ (npx.asarray(point, dtype=float).reshape(3) - cam_pos)
    if p_cam[2] <= 1e-6:
        return None

    fovy = math.radians(float(env.sim.model.cam_fovy[cam_id]))
    fy = (height / 2.0) / math.tan(fovy / 2.0)
    fx = fy
    u = fx * (p_cam[0] / p_cam[2]) + width / 2.0
    v = fy * (p_cam[1] / p_cam[2]) + height / 2.0
    if not (math.isfinite(u) and math.isfinite(v)):
        return None
    return int(round(u)), int(round(v))


def render_debug_view(env: Any, debug: DebugSnapshot, *, enabled: bool, window_name: str, target_gripper_axis: str) -> None:
    """Render LIBERO and mark the target point decoded from the Quest controller."""
    if not enabled:
        env.render()
        return

    try:
        import cv2  # type: ignore
    except ModuleNotFoundError:
        env.render()
        return

    npx = _require_numpy()
    viewer = env.viewer
    height = int(getattr(viewer, "height", 800))
    width = int(getattr(viewer, "width", 1280))
    camera_name = getattr(viewer, "camera_name", "agentview")
    image = env.sim.render(camera_name=camera_name, height=height, width=width)[..., ::-1]
    image = npx.flip(image, axis=0).copy()

    # Blue = current simulated EEF. Green = Quest-decoded target EEF.
    eef_px = project_world_to_pixel(env, debug.eef_pos, camera_name, width, height)
    target_px = None if debug.target_pos is None else project_world_to_pixel(env, debug.target_pos, camera_name, width, height)
    if target_px is not None:
        cv2.drawMarker(image, target_px, (0, 220, 0), markerType=cv2.MARKER_CROSS, markerSize=30, thickness=3)
        cv2.circle(image, target_px, 11, (0, 220, 0), 2)
        if debug.target_rot is not None and debug.target_pos is not None:
            arrow_end = npx.asarray(debug.target_pos, dtype=float) + npx.asarray(debug.target_rot, dtype=float) @ _axis(target_gripper_axis) * 0.09
            arrow_px = project_world_to_pixel(env, arrow_end, camera_name, width, height)
            if arrow_px is not None:
                cv2.arrowedLine(image, target_px, arrow_px, (0, 220, 0), 3, tipLength=0.28)
    if eef_px is not None:
        cv2.circle(image, eef_px, 8, (255, 80, 0), -1)
        if debug.eef_rot is not None:
            arrow_end = npx.asarray(debug.eef_pos, dtype=float) + npx.asarray(debug.eef_rot, dtype=float) @ _axis(target_gripper_axis) * 0.075
            arrow_px = project_world_to_pixel(env, arrow_end, camera_name, width, height)
            if arrow_px is not None:
                cv2.arrowedLine(image, eef_px, arrow_px, (255, 80, 0), 2, tipLength=0.28)
    if eef_px is not None and target_px is not None:
        cv2.line(image, eef_px, target_px, (0, 180, 180), 2)

    cv2.imshow(window_name, image)
    cv2.waitKey(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Teleoperate LIBERO Panda from Quest controller poses.")
    parser.add_argument("--openpi-root", default="/Users/pengyue/Codespace/openpi_cam")
    parser.add_argument("--libero-config-dir", default=str(Path.home() / ".libero_quest_teleop"))
    parser.add_argument("--task-suite-name", default="libero_spatial")
    parser.add_argument("--task-id", type=int, default=0)
    parser.add_argument("--init-state-index", type=int, default=-1, help="LIBERO init state index; -1 uses plain env.reset().")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--camera", default="agentview")
    parser.add_argument("--offscreen", action="store_true", help="Do not open the MuJoCo viewer.")
    parser.add_argument("--no-debug-overlay", action="store_true", help="Disable the on-image target marker.")
    parser.add_argument("--debug-window-name", default="LIBERO Quest teleop")
    parser.add_argument(
        "--target-gripper-axis",
        choices=("auto", *AXIS_NAMES),
        default="auto",
        help="EEF local axis drawn as the green gripper/approach arrow; auto chooses the initial downward axis.",
    )
    parser.add_argument("--control-freq", type=int, default=20)

    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--remote-port", type=int, default=DEFAULT_PORTS["remote"])
    parser.add_argument("--pause-port", type=int, default=DEFAULT_PORTS["pause"])
    parser.add_argument("--resolution-port", type=int, default=DEFAULT_PORTS["resolution"])
    parser.add_argument("--gripper-port", type=int, default=DEFAULT_GRIPPER_PORT)
    parser.add_argument("--adb-reverse", action="store_true")
    parser.add_argument("--no-gate", action="store_true", help="Ignore B/stream gate and always teleoperate.")
    parser.add_argument("--trajectory-gate-pause", choices=("High", "Low"), default="High")
    parser.add_argument(
        "--allow-initial-high",
        action="store_true",
        help="Start teleop immediately if the Quest stream is already High. By default, release B once before clutching.",
    )

    parser.add_argument("--calibration", type=Path, default=Path("calibrations/quest_teleop_frame.json"))
    parser.add_argument("--libero-right-axis", choices=tuple(AXIS_VECTORS), default="-y")
    parser.add_argument("--libero-forward-axis", choices=tuple(AXIS_VECTORS), default="+x")
    parser.add_argument("--libero-up-axis", choices=tuple(AXIS_VECTORS), default="+z")
    parser.add_argument("--position-scale", type=float, default=1.0, help="Meters of LIBERO target motion per calibrated Quest meter.")
    parser.add_argument("--position-action-gain", type=float, default=12.0, help="OSC action gain from target-position error to action.")
    parser.add_argument("--rotation-action-gain", type=float, default=1.8)
    parser.add_argument(
        "--home-mode",
        choices=("calibration-origin", "clutch-current"),
        default="calibration-origin",
        help=(
            "calibration-origin maps the saved controller origin to the initial LIBERO EEF; "
            "clutch-current re-homes to the current controller pose whenever B is pressed."
        ),
    )
    parser.set_defaults(orientation=False)
    parser.add_argument("--orientation", dest="orientation", action="store_true", help="Also control EEF orientation from controller rotation.")
    parser.add_argument("--no-orientation", dest="orientation", action="store_false", help="Only control xyz + gripper. This is the default.")
    parser.add_argument("--smoothing", type=float, default=0.35, help="EMA on absolute target position, 0 disables.")
    parser.add_argument("--max-action", type=float, default=1.0)
    parser.add_argument("--gripper-mode", choices=("toggle", "hold"), default="toggle")
    parser.add_argument("--print-every", type=int, default=20)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    npx = _require_numpy()

    if args.adb_reverse:
        setup_adb_reverse([args.remote_port, args.pause_port, args.resolution_port, args.gripper_port])

    calibration = load_quest_calibration(args.calibration)
    if calibration is None:
        print(f"Warning: no calibration loaded from {args.calibration}; using raw fallback axes.", flush=True)
        if args.home_mode == "calibration-origin":
            args.home_mode = "clutch-current"
            print("Warning: --home-mode calibration-origin requires calibration; falling back to clutch-current.", flush=True)
    else:
        print(f"Loaded Quest teleop calibration: {args.calibration}", flush=True)
    teleop_to_libero = build_teleop_to_libero(args.libero_right_axis, args.libero_forward_axis, args.libero_up_axis)
    print(
        "Teleop axes -> LIBERO xyz: "
        f"right={args.libero_right_axis}, forward={args.libero_forward_axis}, up={args.libero_up_axis}",
        flush=True,
    )

    env, task_description, init_states = make_libero_env(args)
    print(f"LIBERO task: {task_description}", flush=True)
    obs = env.reset()
    if init_states is not None and args.init_state_index < len(init_states):
        obs = env.set_init_state(init_states[args.init_state_index])
    initial_eef_pos = npx.asarray(obs["robot0_eef_pos"], dtype=float).copy()
    initial_eef_rot = current_grip_site_rot(env)
    target_gripper_axis = resolve_gripper_axis(args.target_gripper_axis, initial_eef_rot)
    print(f"LIBERO target gripper arrow axis: {target_gripper_axis}", flush=True)
    if not args.offscreen:
        render_debug_view(
            env,
            DebugSnapshot(
                task=task_description,
                step=0,
                gate_open=False,
                pause_state=None,
                gripper=-1.0,
                eef_pos=npx.asarray(obs["robot0_eef_pos"], dtype=float),
                eef_rot=initial_eef_rot,
                target_pos=None,
                target_rot=None,
                quest_pos=None,
                quest_delta=None,
                action=npx.zeros(7),
                remote_count=0,
                remote_age=None,
                flag=None,
                homed=False,
            ),
            enabled=not args.no_debug_overlay,
            window_name=args.debug_window_name,
            target_gripper_axis=target_gripper_axis,
        )

    context = zmq.Context()
    remote_socket = context.socket(zmq.PULL)
    remote_socket.setsockopt(zmq.LINGER, 0)
    remote_socket.setsockopt(zmq.CONFLATE, 1)
    remote_socket.bind(f"tcp://{args.host}:{args.remote_port}")

    pause_socket = context.socket(zmq.PULL)
    pause_socket.setsockopt(zmq.LINGER, 0)
    pause_socket.setsockopt(zmq.CONFLATE, 1)
    pause_socket.bind(f"tcp://{args.host}:{args.pause_port}")

    poller = zmq.Poller()
    poller.register(remote_socket, zmq.POLLIN)
    poller.register(pause_socket, zmq.POLLIN)

    latest_remote: dict[str, Any] | None = None
    latest_remote_at: float | None = None
    latest_quest_pos: Any | None = None
    remote_count = 0
    pause_state: str | None = None
    gate_open = bool(args.no_gate)
    gate_armed = bool(args.no_gate or args.allow_initial_high)
    initial_high_warned = False
    home: TeleopHome | None = None
    gripper = -1.0
    prev_flag = False
    step_idx = 0
    stop = False

    def handle_signal(_signum: int, _frame: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    print(
        "Waiting for Quest frames. Press B/stream High to clutch; trigger toggles/holds the gripper. "
        f"home_mode={args.home_mode}",
        flush=True,
    )

    try:
        while not stop:
            ready = dict(poller.poll(timeout=max(1, int(1000 / args.control_freq))))
            if pause_socket in ready:
                payload = newest_from_socket(pause_socket)
                if payload is not None:
                    state = payload.decode("utf-8", errors="replace").strip()
                    pause_state = state
                    was_open = gate_open
                    if args.no_gate:
                        next_gate_open = True
                    elif state == args.trajectory_gate_pause and gate_armed:
                        next_gate_open = True
                    else:
                        next_gate_open = False
                        if state != args.trajectory_gate_pause:
                            gate_armed = True
                    if state == args.trajectory_gate_pause and not gate_armed and not initial_high_warned:
                        print("Stream is already High; release B once, then press B again to clutch.", flush=True)
                        initial_high_warned = True
                    gate_open = next_gate_open
                    if gate_open and not was_open:
                        if args.home_mode == "clutch-current":
                            home = None
                            print("Teleop clutch engaged; next valid pose will re-home controller to current EEF.", flush=True)
                        else:
                            print("Teleop clutch engaged; using saved calibration origin as controller zero.", flush=True)
                    elif was_open and not gate_open:
                        print("Teleop clutch released; robot holds position.", flush=True)

            if remote_socket in ready:
                payload = newest_from_socket(remote_socket)
                if payload is not None:
                    try:
                        remote = parse_remote_text(payload.decode("utf-8", errors="replace").strip())
                    except (TypeError, ValueError):
                        remote = None
                    if valid_remote(remote):
                        latest_remote = remote
                        latest_remote_at = time.time()
                        latest_quest_pos = quest_pos_to_teleop(latest_remote["position"], calibration)
                        remote_count += 1

            if latest_remote is None:
                action = npx.asarray([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, gripper], dtype=float)
            elif gate_open:
                flag = bool(latest_remote.get("flag"))
                if args.gripper_mode == "toggle":
                    if flag and not prev_flag:
                        gripper = 1.0 if gripper < 0 else -1.0
                else:
                    gripper = 1.0 if flag else -1.0
                prev_flag = flag

                if home is None:
                    qpos = quest_pos_to_teleop(latest_remote["position"], calibration)
                    qrot_current = quest_rotation_to_teleop_matrix(latest_remote["rotation"], calibration)
                    if args.home_mode == "calibration-origin" and calibration is not None and calibration.rotation_neutral is not None:
                        qrot = quest_rotation_to_teleop_matrix(calibration.rotation_neutral, calibration)
                        rotation_home = "saved-neutral"
                    else:
                        qrot = qrot_current
                        rotation_home = "current-controller"
                    eef_pos = npx.asarray(obs["robot0_eef_pos"], dtype=float)
                    eef_rot = current_grip_site_rot(env)
                    home = make_home(
                        mode=args.home_mode,
                        qpos=qpos,
                        qrot=qrot,
                        initial_eef_pos=initial_eef_pos,
                        initial_eef_rot=initial_eef_rot,
                        current_eef_pos=eef_pos,
                        current_eef_rot=eef_rot,
                    )
                    print(
                        f"Homed: mode={args.home_mode} quest_zero={home.quest_pos.round(3)} "
                        f"eef_zero={home.eef_pos.round(3)} current_quest={qpos.round(3)} "
                        f"rotation_home={rotation_home}",
                        flush=True,
                    )
                action, home = compute_action(
                    remote=latest_remote,
                    obs=obs,
                    calibration=calibration,
                    teleop_to_libero=teleop_to_libero,
                    home=home,
                    current_eef_rot=current_grip_site_rot(env),
                    position_scale=args.position_scale,
                    position_action_gain=args.position_action_gain,
                    rotation_action_gain=args.rotation_action_gain,
                    smoothing=args.smoothing,
                    max_action=args.max_action,
                    gripper=gripper,
                    orientation=args.orientation,
                )
            else:
                prev_flag = bool(latest_remote.get("flag")) if latest_remote else prev_flag
                action = npx.asarray([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, gripper], dtype=float)

            obs, _reward, done, _info = env.step(action)
            if not args.offscreen:
                eef = npx.asarray(obs["robot0_eef_pos"], dtype=float)
                eef_rot = current_grip_site_rot(env)
                quest_delta = None if home is None or latest_quest_pos is None else latest_quest_pos - home.quest_pos
                render_debug_view(
                    env,
                    DebugSnapshot(
                        task=task_description,
                        step=step_idx,
                        gate_open=gate_open,
                        pause_state=pause_state,
                        gripper=gripper,
                        eef_pos=eef,
                        eef_rot=eef_rot,
                        target_pos=None if home is None else home.target_pos,
                        target_rot=None if home is None else home.target_rot,
                        quest_pos=latest_quest_pos,
                        quest_delta=quest_delta,
                        action=action,
                        remote_count=remote_count,
                        remote_age=None if latest_remote_at is None else time.time() - latest_remote_at,
                        flag=None if latest_remote is None else bool(latest_remote.get("flag")),
                        homed=home is not None,
                    ),
                    enabled=not args.no_debug_overlay,
                    window_name=args.debug_window_name,
                    target_gripper_axis=target_gripper_axis,
                )
            step_idx += 1
            if args.print_every and step_idx % args.print_every == 0:
                eef = npx.asarray(obs["robot0_eef_pos"], dtype=float)
                print(
                    f"step={step_idx} gate={gate_open} grip={gripper:+.0f} "
                    f"eef=({eef[0]:.3f},{eef[1]:.3f},{eef[2]:.3f}) action={npx.asarray(action).round(3)}",
                    flush=True,
                )
            if done:
                print("LIBERO reports task success.", flush=True)
    finally:
        try:
            env.close()
        except Exception:
            pass
        remote_socket.close(0)
        pause_socket.close(0)
        context.term()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
