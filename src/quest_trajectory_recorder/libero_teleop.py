#!/usr/bin/env python3
"""Teleoperate a LIBERO / robosuite Panda from Quest-derived TeleopTarget data.

This can read the Quest APK ports directly for compatibility, or subscribe to a
separate quest-tracker-hub publisher so simulator backends do not own raw Quest
transport details.
"""

from __future__ import annotations

import argparse
import math
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import zmq

from .quest_target_source import (
    DEFAULT_TARGET_ENDPOINT,
    DEFAULT_TARGET_TOPIC,
    DirectQuestTargetSource,
    TeleopTargetSubscriber,
    newest_from_socket,
)
from .quest_ports import DEFAULT_GRIPPER_PORT, setup_adb_reverse
from .receiver import DEFAULT_PORTS
from .teleop_frame import (
    AXIS_NAMES,
    AXIS_VECTORS,
    QuestCalibration,
    build_axis_map,
    load_quest_calibration,
    quest_pos_to_teleop,
    quest_rotation_to_teleop_matrix,
    quat_xyzw_to_matrix,
    resolve_gripper_axis,
)
from .teleop_target import TeleopTarget, valid_remote

# Some moved helpers are re-exported from this module for older local scripts.

try:  # Optional at import time; required only when running LIBERO.
    import numpy as np
except ModuleNotFoundError:  # pragma: no cover - exercised only without numpy installed
    np = None  # type: ignore[assignment]


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


def _norm(v: Any) -> Any:
    npx = _require_numpy()
    v = npx.asarray(v, dtype=float)
    length = float(npx.linalg.norm(v))
    if length <= 1e-12:
        return v * 0.0
    return v / length


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


def build_teleop_to_libero(right_axis: str, forward_axis: str, up_axis: str) -> Any:
    """Return a matrix mapping [right, forward, up] into LIBERO world xyz."""
    npx = _require_numpy()
    return npx.asarray(build_axis_map(right_axis, forward_axis, up_axis), dtype=float)


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


def compute_action(
    *,
    target: TeleopTarget,
    obs: dict[str, Any],
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
    quest_pos = npx.asarray(target.position, dtype=float)
    quest_delta = quest_pos - home.quest_pos
    target_pos = home.eef_pos + teleop_to_libero @ (quest_delta * position_scale)

    if smoothing > 0.0:
        alpha = max(0.0, min(0.98, smoothing))
        target_pos = home.target_pos * alpha + target_pos * (1.0 - alpha)

    curr_pos = npx.asarray(obs["robot0_eef_pos"], dtype=float)
    pos_action = (target_pos - curr_pos) * position_action_gain

    if orientation:
        quest_rot = npx.asarray(target.rotation, dtype=float)
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
    parser.add_argument(
        "--input-source",
        choices=("direct", "target"),
        default="direct",
        help="direct reads Quest APK ports; target subscribes to quest-tracker-hub TeleopTarget.",
    )
    parser.add_argument("--target-endpoint", default=DEFAULT_TARGET_ENDPOINT)
    parser.add_argument("--target-topic", default=DEFAULT_TARGET_TOPIC)
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

    if args.adb_reverse and args.input_source == "direct":
        setup_adb_reverse([args.remote_port, args.pause_port, args.resolution_port, args.gripper_port])
    elif args.adb_reverse:
        print("Note: --adb-reverse is ignored with --input-source target; run it on quest-tracker-hub instead.", flush=True)

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
    if args.input_source == "target":
        source = TeleopTargetSubscriber(context=context, endpoint=args.target_endpoint, topic=args.target_topic)
        print(f"Subscribing to TeleopTarget stream: {args.target_endpoint} topic={args.target_topic!r}", flush=True)
    else:
        source = DirectQuestTargetSource(
            context=context,
            host=args.host,
            remote_port=args.remote_port,
            pause_port=args.pause_port,
            calibration=calibration,
            no_gate=args.no_gate,
            trajectory_gate_pause=args.trajectory_gate_pause,
            allow_initial_high=args.allow_initial_high,
            gripper_mode=args.gripper_mode,
        )
        print("Reading Quest APK ports directly. For decoupled simulators, run quest-tracker-hub and use --input-source target.", flush=True)

    latest_target: TeleopTarget | None = None
    latest_target_at: float | None = None
    latest_quest_pos: Any | None = None
    remote_count = 0
    pause_state: str | None = None
    gate_open = bool(args.no_gate)
    home: TeleopHome | None = None
    gripper = -1.0
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
            was_gate_open = gate_open
            target = source.poll(max(1, int(1000 / args.control_freq)))
            for event in source.take_events():
                print(event, flush=True)
            if target is not None:
                latest_target = target
                latest_target_at = target.timestamp
                latest_quest_pos = npx.asarray(target.position, dtype=float)
                remote_count = target.remote_count
                pause_state = target.pause_state
                gate_open = target.gate_open
                gripper = target.gripper
            elif latest_target is not None:
                gate_open = bool(getattr(source, "gate_open", latest_target.gate_open))
                gripper = float(getattr(source, "gripper", latest_target.gripper))
                pause_state = getattr(source, "pause_state", pause_state)

            if gate_open and not was_gate_open and args.home_mode == "clutch-current":
                home = None
                print("Teleop clutch engaged; next valid target will re-home controller to current EEF.", flush=True)

            if latest_target is None:
                action = npx.asarray([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, gripper], dtype=float)
            elif gate_open:
                if home is None:
                    qpos = npx.asarray(latest_target.position, dtype=float)
                    qrot_current = npx.asarray(latest_target.rotation, dtype=float)
                    if args.home_mode == "calibration-origin" and calibration is not None and calibration.rotation_neutral is not None:
                        qrot = npx.asarray(quest_rotation_to_teleop_matrix(calibration.rotation_neutral, calibration), dtype=float)
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
                    target=latest_target,
                    obs=obs,
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
                        remote_age=None if latest_target_at is None else time.time() - latest_target_at,
                        flag=None if latest_target is None else latest_target.flag,
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
        source.close()
        context.term()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
