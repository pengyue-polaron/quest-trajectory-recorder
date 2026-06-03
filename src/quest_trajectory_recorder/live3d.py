#!/usr/bin/env python3
"""Live 3D browser view for Quest controller trajectory frames."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import queue
import signal
import subprocess
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import zmq

from .receiver import DEFAULT_PORTS, iso_now, make_socket, parse_remote_text, write_remote_row


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


REMOTE_FIELDS = [
    "recv_unix",
    "recv_iso",
    "seq",
    "channel",
    "port",
    "kind",
    "pos_x",
    "pos_y",
    "pos_z",
    "quat_x",
    "quat_y",
    "quat_z",
    "quat_w",
    "flag",
    "num_points",
    "point0_x",
    "point0_y",
    "point0_z",
    "point1_x",
    "point1_y",
    "point1_z",
    "point2_x",
    "point2_y",
    "point2_z",
    "raw_text",
]
EVENT_FIELDS = ["recv_unix", "recv_iso", "seq", "channel", "port", "text", "bytes"]


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Quest Trajectory Live 3D</title>
  <style>
    :root {
      --ink: #162018;
      --muted: #657064;
      --paper: #f7f1df;
      --panel: #fffaf0;
      --accent: #0b7285;
      --green: #2b9348;
      --red: #d9480f;
      --blue: #2f5fbd;
      --grid: #d9cfb7;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      background:
        radial-gradient(circle at 15% 10%, rgba(255, 200, 87, .35), transparent 28rem),
        radial-gradient(circle at 85% 20%, rgba(70, 143, 175, .25), transparent 26rem),
        linear-gradient(135deg, #f6ecd4 0%, #eef2e4 100%);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }
    .wrap { width: min(1280px, calc(100vw - 28px)); margin: 18px auto; }
    header { display: flex; align-items: flex-end; justify-content: space-between; gap: 18px; margin-bottom: 12px; }
    h1 { margin: 0; font-family: Georgia, serif; font-size: clamp(28px, 4vw, 52px); line-height: .95; letter-spacing: -.04em; }
    .subtitle { color: var(--muted); margin-top: 8px; font-size: 13px; max-width: 760px; }
    .status {
      min-width: 260px;
      padding: 14px 16px;
      border: 1.5px solid rgba(22, 32, 24, .85);
      border-radius: 18px;
      background: rgba(255, 250, 240, .86);
      box-shadow: 0 12px 28px rgba(20, 30, 22, .12);
    }
    .pill { display: inline-flex; align-items: center; gap: 8px; padding: 6px 10px; border-radius: 999px; border: 1px solid rgba(22,32,24,.35); background: white; font-size: 12px; }
    .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--red); box-shadow: 0 0 0 4px rgba(217,72,15,.15); }
    .dot.on { background: var(--green); box-shadow: 0 0 0 4px rgba(43,147,72,.18); }
    .grid { display: grid; grid-template-columns: 1fr 330px; gap: 14px; }
    .stage, .side {
      border: 1.5px solid rgba(22, 32, 24, .9);
      border-radius: 26px;
      background: rgba(255, 250, 240, .78);
      box-shadow: 0 18px 42px rgba(20, 30, 22, .14);
      overflow: hidden;
    }
    canvas { display: block; width: 100%; height: min(72vh, 760px); min-height: 520px; }
    .side { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
    .card { padding: 12px; border-radius: 16px; background: rgba(255,255,255,.62); border: 1px solid rgba(22,32,24,.14); }
    .label { color: var(--muted); font-size: 12px; margin-bottom: 6px; }
    .value { font-size: 18px; white-space: pre-wrap; overflow-wrap: anywhere; }
    .small { font-size: 12px; color: var(--muted); line-height: 1.45; }
    button {
      appearance: none;
      border: 1.5px solid rgba(22,32,24,.8);
      background: #162018;
      color: #fffaf0;
      border-radius: 14px;
      padding: 10px 12px;
      font: inherit;
      cursor: pointer;
    }
    button.secondary { background: #fffaf0; color: var(--ink); }
    .row { display: flex; gap: 8px; flex-wrap: wrap; }
    @media (max-width: 980px) {
      header, .grid { display: block; }
      .status { margin-top: 12px; }
      .side { margin-top: 14px; }
      canvas { min-height: 420px; height: 58vh; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>Quest Trajectory<br/>Live 3D</h1>
        <div class="subtitle">Live local view from the Quest controller pose stream. Green means the trajectory gate is open; red means waiting/paused. Arrival time is local receive time, not device sample time.</div>
      </div>
      <div class="status">
        <span class="pill"><span id="dot" class="dot"></span><span id="gateText">connecting</span></span>
        <div class="small" id="statusText" style="margin-top:10px">Waiting for server events...</div>
      </div>
    </header>

    <div class="grid">
      <div class="stage"><canvas id="canvas"></canvas></div>
      <aside class="side">
        <div class="card"><div class="label">latest position</div><div id="pos" class="value">--</div></div>
        <div class="card"><div class="label">samples / path length</div><div id="samples" class="value">0 / 0.000 m</div></div>
        <div class="card"><div class="label">stream</div><div id="stream" class="value">--</div></div>
        <div class="row">
          <button id="fit">Fit View</button>
          <button id="clear" class="secondary">Clear Local View</button>
        </div>
        <div class="card small">
          Controls: drag to rotate, wheel to zoom. Axes: X red, Y green, Z blue. The server resets this view when the pause gate opens for a new take.
        </div>
      </aside>
    </div>
  </div>

<script>
const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const dot = document.getElementById('dot');
const gateText = document.getElementById('gateText');
const statusText = document.getElementById('statusText');
const posEl = document.getElementById('pos');
const samplesEl = document.getElementById('samples');
const streamEl = document.getElementById('stream');
let points = [];
let latest = null;
let gateOpen = false;
let pauseState = null;
let resolutionState = null;
let lastMessage = null;
let az = 0.72;
let el = 0.42;
let zoom = 1.0;
let dragging = false;
let dragStart = null;

function resize() {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  draw();
}
window.addEventListener('resize', resize);

function distance(a, b) {
  const dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z;
  return Math.hypot(dx, dy, dz);
}
function pathLength() {
  let total = 0;
  for (let i = 1; i < points.length; i++) total += distance(points[i - 1], points[i]);
  return total;
}
function bounds() {
  if (!points.length) return {xmin:-.5,xmax:.5,ymin:-.5,ymax:.5,zmin:-.5,zmax:.5,cx:0,cy:0,cz:0,span:1};
  let xmin=Infinity,xmax=-Infinity,ymin=Infinity,ymax=-Infinity,zmin=Infinity,zmax=-Infinity;
  for (const p of points) { xmin=Math.min(xmin,p.x); xmax=Math.max(xmax,p.x); ymin=Math.min(ymin,p.y); ymax=Math.max(ymax,p.y); zmin=Math.min(zmin,p.z); zmax=Math.max(zmax,p.z); }
  const pad = Math.max((xmax-xmin), (ymax-ymin), (zmax-zmin), .1) * .12;
  xmin-=pad; xmax+=pad; ymin-=pad; ymax+=pad; zmin-=pad; zmax+=pad;
  return {xmin,xmax,ymin,ymax,zmin,zmax,cx:(xmin+xmax)/2,cy:(ymin+ymax)/2,cz:(zmin+zmax)/2,span:Math.max(xmax-xmin,ymax-ymin,zmax-zmin,.1)};
}
function project(p, b) {
  let x = p.x - b.cx, y = p.y - b.cy, z = p.z - b.cz;
  const ca = Math.cos(az), sa = Math.sin(az), ce = Math.cos(el), se = Math.sin(el);
  const xr = ca * x - sa * z;
  const zr = sa * x + ca * z;
  const yr = ce * y - se * zr;
  const depth = se * y + ce * zr;
  const rect = canvas.getBoundingClientRect();
  const scale = Math.min(rect.width, rect.height) * .68 / b.span * zoom;
  return {u: rect.width / 2 + xr * scale, v: rect.height / 2 - yr * scale, d: depth};
}
function line3(a, bpt, b, color, width=1) {
  const A = project(a, b), B = project(bpt, b);
  ctx.strokeStyle = color; ctx.lineWidth = width; ctx.beginPath(); ctx.moveTo(A.u, A.v); ctx.lineTo(B.u, B.v); ctx.stroke();
}
function draw() {
  const rect = canvas.getBoundingClientRect();
  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.fillStyle = '#fffaf0'; ctx.fillRect(0,0,rect.width,rect.height);
  const b = bounds();
  ctx.lineCap = 'round'; ctx.lineJoin = 'round';

  for (let i=0; i<=6; i++) {
    const tx = b.xmin + (b.xmax-b.xmin)*i/6;
    const tz = b.zmin + (b.zmax-b.zmin)*i/6;
    line3({x:tx,y:b.ymin,z:b.zmin}, {x:tx,y:b.ymin,z:b.zmax}, b, '#d9cfb7', 1);
    line3({x:b.xmin,y:b.ymin,z:tz}, {x:b.xmax,y:b.ymin,z:tz}, b, '#d9cfb7', 1);
  }
  line3({x:b.xmin,y:b.ymin,z:b.zmin}, {x:b.xmax,y:b.ymin,z:b.zmin}, b, '#c94033', 3);
  line3({x:b.xmin,y:b.ymin,z:b.zmin}, {x:b.xmin,y:b.ymax,z:b.zmin}, b, '#208f55', 3);
  line3({x:b.xmin,y:b.ymin,z:b.zmin}, {x:b.xmin,y:b.ymin,z:b.zmax}, b, '#2f5fbd', 3);

  if (points.length > 1) {
    for (let i=1; i<points.length; i++) {
      const A = project(points[i-1], b), B = project(points[i], b);
      const t = i / Math.max(points.length-1, 1);
      ctx.strokeStyle = `hsl(${195 - 160*t}, 78%, 34%)`;
      ctx.lineWidth = 2.5 + 1.5*t;
      ctx.beginPath(); ctx.moveTo(A.u,A.v); ctx.lineTo(B.u,B.v); ctx.stroke();
    }
  }
  if (points.length) {
    const S = project(points[0], b), E = project(points[points.length-1], b);
    ctx.fillStyle = '#2b9348'; ctx.beginPath(); ctx.arc(S.u,S.v,7,0,Math.PI*2); ctx.fill();
    ctx.fillStyle = '#d9480f'; ctx.beginPath(); ctx.arc(E.u,E.v,8,0,Math.PI*2); ctx.fill();
  }
  ctx.fillStyle = '#657064'; ctx.font = '12px Menlo, monospace';
  ctx.fillText('X', project({x:b.xmax,y:b.ymin,z:b.zmin}, b).u + 8, project({x:b.xmax,y:b.ymin,z:b.zmin}, b).v);
  ctx.fillText('Y', project({x:b.xmin,y:b.ymax,z:b.zmin}, b).u + 8, project({x:b.xmin,y:b.ymax,z:b.zmin}, b).v);
  ctx.fillText('Z', project({x:b.xmin,y:b.ymin,z:b.zmax}, b).u + 8, project({x:b.xmin,y:b.ymin,z:b.zmax}, b).v);
}
function updateStats() {
  dot.classList.toggle('on', gateOpen);
  gateText.textContent = gateOpen ? 'recording / gate open' : 'waiting / paused';
  statusText.textContent = `pause=${pauseState ?? '--'} | resolution=${resolutionState ?? '--'} | last=${lastMessage ?? '--'}`;
  if (latest) posEl.textContent = `x=${latest.x.toFixed(4)}\ny=${latest.y.toFixed(4)}\nz=${latest.z.toFixed(4)}`;
  samplesEl.textContent = `${points.length} / ${pathLength().toFixed(3)} m`;
  streamEl.textContent = latest ? `seq=${latest.seq}\nkind=${latest.kind}\nrecv=${latest.recv_iso}` : '--';
}
function handleEvent(ev) {
  if (ev.type === 'snapshot') {
    points = ev.points || [];
    gateOpen = !!ev.gate_open;
    pauseState = ev.pause_state;
    resolutionState = ev.resolution_state;
    latest = points.length ? points[points.length - 1] : null;
  } else if (ev.type === 'reset') {
    points = [];
    latest = null;
  } else if (ev.type === 'pose') {
    points.push(ev.point);
    latest = ev.point;
  } else if (ev.type === 'status') {
    gateOpen = !!ev.gate_open;
    pauseState = ev.pause_state;
    resolutionState = ev.resolution_state;
  }
  lastMessage = ev.recv_iso || new Date().toLocaleTimeString();
  updateStats(); draw();
}

fetch('/snapshot').then(r => r.json()).then(handleEvent).catch(() => {});
const es = new EventSource('/events');
es.onmessage = e => handleEvent(JSON.parse(e.data));
es.onerror = () => { gateText.textContent = 'disconnected'; dot.classList.remove('on'); };

canvas.addEventListener('mousedown', e => { dragging = true; dragStart = {x:e.clientX, y:e.clientY, az, el}; });
window.addEventListener('mouseup', () => dragging = false);
window.addEventListener('mousemove', e => {
  if (!dragging) return;
  az = dragStart.az + (e.clientX - dragStart.x) * 0.01;
  el = Math.max(-1.2, Math.min(1.2, dragStart.el + (e.clientY - dragStart.y) * 0.01));
  draw();
});
canvas.addEventListener('wheel', e => { e.preventDefault(); zoom *= Math.exp(-e.deltaY * 0.001); zoom = Math.max(.25, Math.min(6, zoom)); draw(); }, {passive:false});
document.getElementById('fit').onclick = () => { zoom = 1; draw(); };
document.getElementById('clear').onclick = () => { points = []; latest = null; updateStats(); draw(); };
resize(); setInterval(draw, 1000);
</script>
</body>
</html>
"""


class LiveState:
    def __init__(self, max_points: int) -> None:
        self.max_points = max_points
        self.lock = threading.Lock()
        self.points: list[dict[str, Any]] = []
        self.clients: list[queue.Queue[dict[str, Any]]] = []
        self.gate_open = False
        self.pause_state: str | None = None
        self.resolution_state: str | None = None
        self.gate_prereq_seen = False
        self.seq = 0
        self.total_received = 0
        self.total_written = 0
        self.started_at = time.time()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "type": "snapshot",
                "recv_iso": iso_now(),
                "points": list(self.points),
                "gate_open": self.gate_open,
                "pause_state": self.pause_state,
                "resolution_state": self.resolution_state,
                "total_received": self.total_received,
                "total_written": self.total_written,
                "uptime_sec": time.time() - self.started_at,
            }

    def add_client(self) -> queue.Queue[dict[str, Any]]:
        client: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=256)
        with self.lock:
            self.clients.append(client)
        client.put(self.snapshot())
        return client

    def remove_client(self, client: queue.Queue[dict[str, Any]]) -> None:
        with self.lock:
            if client in self.clients:
                self.clients.remove(client)

    def broadcast(self, event: dict[str, Any]) -> None:
        with self.lock:
            clients = list(self.clients)
        for client in clients:
            try:
                client.put_nowait(event)
            except queue.Full:
                try:
                    client.get_nowait()
                    client.put_nowait(event)
                except queue.Empty:
                    pass

    def set_status(self, *, pause_state: str | None = None, resolution_state: str | None = None) -> None:
        with self.lock:
            if pause_state is not None:
                self.pause_state = pause_state
            if resolution_state is not None:
                self.resolution_state = resolution_state
            event = {
                "type": "status",
                "recv_iso": iso_now(),
                "gate_open": self.gate_open,
                "pause_state": self.pause_state,
                "resolution_state": self.resolution_state,
            }
        self.broadcast(event)

    def reset_points(self) -> None:
        with self.lock:
            self.points = []
        self.broadcast({"type": "reset", "recv_iso": iso_now()})

    def add_pose(self, point: dict[str, Any]) -> None:
        with self.lock:
            self.points.append(point)
            if len(self.points) > self.max_points:
                self.points = self.points[-self.max_points :]
            self.total_written += 1
        self.broadcast({"type": "pose", "recv_iso": point["recv_iso"], "point": point})


def is_origin(position: list[float]) -> bool:
    return all(abs(value) < 1e-8 for value in position)


def make_handler(state: LiveState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def send_text(self, body: str, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/":
                self.send_text(HTML, "text/html; charset=utf-8")
                return
            if path == "/snapshot":
                self.send_text(json.dumps(state.snapshot(), separators=(",", ":")), "application/json")
                return
            if path == "/events":
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                client = state.add_client()
                try:
                    while True:
                        try:
                            event = client.get(timeout=20)
                        except queue.Empty:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                            continue
                        payload = json.dumps(event, separators=(",", ":"))
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    state.remove_client(client)
                return
            self.send_text("Not found", "text/plain; charset=utf-8", HTTPStatus.NOT_FOUND)

    return Handler


def setup_adb_reverse(ports: list[int]) -> None:
    for port in ports:
        subprocess.run(["adb", "reverse", "--remove", f"tcp:{port}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["adb", "reverse", f"tcp:{port}", f"tcp:{port}"], check=True, stdout=subprocess.DEVNULL)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live 3D browser visualizer for Quest controller trajectories.")
    parser.add_argument("--host", default="0.0.0.0", help="ZMQ bind host for Quest/APK frames.")
    parser.add_argument("--remote-port", type=int, default=DEFAULT_PORTS["remote"])
    parser.add_argument("--resolution-port", type=int, default=DEFAULT_PORTS["resolution"])
    parser.add_argument("--pause-port", type=int, default=DEFAULT_PORTS["pause"])
    parser.add_argument("--web-host", default="127.0.0.1")
    parser.add_argument("--web-port", type=int, default=8765)
    parser.add_argument("--out-dir", type=Path, default=Path("captures"))
    parser.add_argument("--session", default=dt.datetime.now().strftime("live_%Y%m%d_%H%M%S"))
    parser.add_argument("--max-points", type=int, default=5000, help="Max points kept in browser memory.")
    parser.add_argument("--print-every", type=int, default=60, help="Print every N accepted poses; 0 disables.")
    parser.add_argument("--trajectory-gate-pause", choices=("High", "Low"), default="High")
    parser.add_argument("--gate-requires-prior-pause", choices=("High", "Low"), default="Low")
    parser.add_argument("--no-gate", action="store_true", help="Accept remote frames immediately, ignoring pause state.")
    parser.add_argument("--keep-leading-origin", action="store_true", help="Keep initial exact 0,0,0 placeholder frames.")
    parser.add_argument("--no-record", action="store_true", help="Do not write captures/*.csv files.")
    parser.add_argument("--adb-reverse", action="store_true", help="Run adb reverse for the Quest ports before listening.")
    parser.add_argument("--open-browser", action="store_true", help="Open the live viewer in the default browser.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.adb_reverse:
        setup_adb_reverse([args.remote_port, args.resolution_port, args.pause_port])

    state = LiveState(max_points=max(10, args.max_points))
    state.gate_open = bool(args.no_gate)
    state.gate_prereq_seen = bool(args.no_gate or not args.gate_requires_prior_pause)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    remote_path = args.out_dir / f"{args.session}_remote.csv"
    events_path = args.out_dir / f"{args.session}_events.csv"

    server = ReusableThreadingHTTPServer((args.web_host, args.web_port), make_handler(state))
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    url = f"http://{args.web_host}:{args.web_port}/"
    print(f"Live 3D viewer: {url}", flush=True)
    if args.open_browser:
        webbrowser.open(url)

    context: zmq.Context[zmq.Socket] = zmq.Context()
    poller = zmq.Poller()
    sockets: dict[zmq.Socket, tuple[str, int]] = {}
    for channel, port in {"remote": args.remote_port, "resolution": args.resolution_port, "pause": args.pause_port}.items():
        socket = make_socket(context, args.host, port, zmq.PULL)
        poller.register(socket, zmq.POLLIN)
        sockets[socket] = (channel, port)
        print(f"ZMQ PULL listening: {channel} tcp://{args.host}:{port}", flush=True)

    stop = False

    def handle_signal(_signum: int, _frame: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print("Waiting for Quest frames. Press Ctrl+C to stop.", flush=True)
    print("Tip: keep Quest IP at 127.0.0.1; press B red -> green to open the trajectory gate.", flush=True)

    remote_file = None
    event_file = None
    try:
        if not args.no_record:
            remote_file = remote_path.open("w", newline="", encoding="utf-8")
            event_file = events_path.open("w", newline="", encoding="utf-8")
            remote_writer = csv.DictWriter(remote_file, fieldnames=REMOTE_FIELDS)
            event_writer = csv.DictWriter(event_file, fieldnames=EVENT_FIELDS)
            remote_writer.writeheader()
            event_writer.writeheader()
            print(f"Remote CSV: {remote_path.resolve()}", flush=True)
            print(f"Event CSV: {events_path.resolve()}", flush=True)
        else:
            remote_writer = None
            event_writer = None

        accepted = 0
        leading_origin_done = False
        while not stop:
            ready = dict(poller.poll(timeout=250))
            for socket in ready:
                channel, port = sockets[socket]
                payload = socket.recv()
                state.seq += 1
                recv_unix = time.time()
                recv_iso = iso_now()
                text = payload.decode("utf-8", errors="replace").strip()

                if channel == "pause":
                    previous_gate = state.gate_open
                    state.pause_state = text
                    if text == args.gate_requires_prior_pause:
                        state.gate_prereq_seen = True
                    if args.no_gate:
                        state.gate_open = True
                    else:
                        state.gate_open = state.gate_prereq_seen and text == args.trajectory_gate_pause
                    if state.gate_open and not previous_gate:
                        state.reset_points()
                        leading_origin_done = False
                        accepted = 0
                        print(f"{recv_iso} trajectory gate opened", flush=True)
                    elif previous_gate and not state.gate_open:
                        print(f"{recv_iso} trajectory gate closed", flush=True)
                    state.set_status(pause_state=text)
                elif channel == "resolution":
                    state.set_status(resolution_state=text)
                elif channel == "remote":
                    state.total_received += 1
                    try:
                        remote = parse_remote_text(text)
                    except (TypeError, ValueError) as exc:
                        print(f"{recv_iso} parse error: {exc}: {text!r}", flush=True)
                        continue
                    if not remote:
                        continue
                    if not state.gate_open:
                        continue
                    position = [float(value) for value in remote["position"]]
                    if not args.keep_leading_origin and not leading_origin_done and is_origin(position):
                        continue
                    leading_origin_done = True

                    event_base = {
                        "recv_unix": recv_unix,
                        "recv_iso": recv_iso,
                        "seq": state.seq,
                        "channel": channel,
                        "port": port,
                    }
                    if remote_writer and remote_file:
                        write_remote_row(remote_writer, event_base, remote, text)
                        remote_file.flush()

                    accepted += 1
                    point = {
                        "seq": state.seq,
                        "recv_unix": recv_unix,
                        "recv_iso": recv_iso,
                        "kind": remote["kind"],
                        "x": position[0],
                        "y": position[1],
                        "z": position[2],
                        "qx": remote["rotation"][0],
                        "qy": remote["rotation"][1],
                        "qz": remote["rotation"][2],
                        "qw": remote["rotation"][3],
                    }
                    state.add_pose(point)
                    if args.print_every and accepted % args.print_every == 0:
                        print(
                            f"{recv_iso} accepted={accepted} pos=({position[0]:.4f},{position[1]:.4f},{position[2]:.4f})",
                            flush=True,
                        )

                if channel != "remote" and event_writer and event_file:
                    event_writer.writerow(
                        {
                            "recv_unix": recv_unix,
                            "recv_iso": recv_iso,
                            "seq": state.seq,
                            "channel": channel,
                            "port": port,
                            "text": text,
                            "bytes": len(payload),
                        }
                    )
                    event_file.flush()
    finally:
        server.shutdown()
        server.server_close()
        for socket in sockets:
            poller.unregister(socket)
            socket.close(0)
        context.term()
        if remote_file:
            remote_file.close()
        if event_file:
            event_file.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
