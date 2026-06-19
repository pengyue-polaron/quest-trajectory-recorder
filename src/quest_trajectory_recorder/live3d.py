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
DEFAULT_GRIPPER_PORT = 8127


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Quest Teleop Frame</title>
  <style>
    :root {
      --bg: #f6f6f6;
      --page: #ffffff;
      --ink: #111111;
      --muted: #666666;
      --soft: #999999;
      --line: #dddddd;
      --line-strong: #bbbbbb;
      --hover: #eeeeee;
      --red: #d44c47;
      --green: #448361;
      --blue: #337ea9;
      --shadow: 0 1px 2px rgba(0,0,0,.04);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      background: var(--bg);
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
      font-size: 14px;
    }
    .wrap { width: min(1380px, calc(100vw - 32px)); margin: 16px auto; }
    header { display: flex; justify-content: space-between; align-items: flex-start; gap: 18px; margin-bottom: 12px; }
    h1 { margin: 0 0 4px; font-size: 24px; line-height: 1.2; font-weight: 600; letter-spacing: -.01em; }
    .subtitle { margin: 0; color: var(--muted); line-height: 1.45; }
    .status { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; min-width: 330px; color: var(--muted); }
    .pill { display: inline-flex; align-items: center; gap: 8px; padding: 4px 9px; border: 1px solid var(--line); border-radius: 999px; background: var(--page); color: var(--ink); font-size: 12px; font-weight: 500; }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--red); }
    .dot.on { background: var(--green); }
    .layout { display: grid; grid-template-columns: minmax(0, 1fr) 390px; gap: 12px; align-items: start; }
    .stage, .side { border: 1px solid var(--line); border-radius: 8px; background: var(--page); box-shadow: var(--shadow); overflow: hidden; }
    .stage-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 10px 12px; border-bottom: 1px solid var(--line); background: #fafafa; }
    .stage-title { font-weight: 600; }
    .legend { display: flex; flex-wrap: wrap; gap: 12px; color: var(--muted); font-size: 12px; }
    .swatch { display: inline-flex; align-items: center; gap: 5px; }
    .swatch::before { content: ""; width: 13px; height: 2px; border-radius: 999px; background: currentColor; }
    .right { color: var(--red); } .forward { color: var(--green); } .up { color: var(--blue); } .start { color: var(--green); } .end { color: var(--red); }
    canvas { display: block; width: 100%; height: min(72vh, 760px); min-height: 540px; background: #fff; }
    .side { padding: 12px; display: flex; flex-direction: column; gap: 10px; }
    .section-title { margin: 8px 2px 2px; color: var(--soft); font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; }
    .section-title:first-child { margin-top: 0; }
    .card { padding: 10px; border-radius: 7px; background: #fff; border: 1px solid var(--line); }
    .card.subtle { background: #fafafa; }
    .card.compact { padding: 8px 10px; }
    .label { color: var(--muted); font-size: 12px; font-weight: 600; margin-bottom: 6px; }
    .value { font-family: "SF Mono", Menlo, Consolas, monospace; font-size: 12px; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; }
    .large { font-size: 16px; font-weight: 600; font-family: inherit; }
    .small { color: var(--muted); font-size: 12px; line-height: 1.45; }
    .button-status { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .status-tile { border: 1px solid var(--line); border-radius: 6px; padding: 8px; background: #fafafa; }
    .status-tile .name { color: var(--muted); font-size: 11px; font-weight: 600; margin-bottom: 4px; }
    .status-tile .state { font-size: 20px; line-height: 1.1; font-weight: 700; letter-spacing: -.02em; }
    .status-tile.trigger-on { background: #111; border-color: #111; color: #fff; }
    .status-tile.trigger-on .name { color: #ddd; }
    .details { border: 1px solid var(--line); border-radius: 7px; background: #fff; overflow: hidden; }
    .details summary { cursor: pointer; padding: 8px 10px; color: var(--muted); font-size: 12px; font-weight: 600; list-style-position: inside; }
    .details[open] summary { border-bottom: 1px solid var(--line); color: var(--ink); background: #fafafa; }
    .raw-grid { display: grid; gap: 6px; padding: 8px; }
    .raw-grid .card { padding: 7px 8px; }
    .raw-grid .label { margin-bottom: 3px; font-size: 11px; }
    .raw-grid .value { font-size: 11px; line-height: 1.4; }
    .actions { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    button {
      appearance: none;
      border: 1px solid var(--line-strong);
      background: #fff;
      color: var(--ink);
      border-radius: 6px;
      padding: 8px 10px;
      font: inherit;
      font-size: 13px;
      font-weight: 500;
      cursor: pointer;
      transition: background .12s ease, color .12s ease, border-color .12s ease;
    }
    button:hover { background: var(--hover); }
    button.primary { background: #111; border-color: #111; color: #fff; }
    button.primary:hover { background: #333; }
    button:disabled { color: #aaa; border-color: #e5e5e5; background: #fafafa; cursor: not-allowed; }
    button.full { grid-column: 1 / -1; }
    .steps { display: grid; gap: 8px; }
    .step { padding: 8px 9px; border: 1px solid var(--line); border-radius: 6px; background: #fafafa; color: var(--muted); }
    .step.active { border-color: #111; color: #111; background: #fff; }
    .step.done { color: #111; }
    @media (max-width: 980px) {
      .wrap { width: min(100vw - 20px, 760px); margin: 10px auto; }
      header, .layout { display: grid; grid-template-columns: 1fr; }
      .status { align-items: flex-start; min-width: 0; }
      canvas { min-height: 420px; height: 58vh; }
      .stage-head { align-items: flex-start; flex-direction: column; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>Quest teleop frame</h1>
        <p class="subtitle">Teleop values are right / forward / up. The 3D view draws them as X / -Z / Y so the frame is physically right-handed.</p>
      </div>
      <div class="status">
        <span class="pill"><span id="dot" class="dot"></span><span id="gateText">connecting</span></span>
        <div class="small" id="statusText">Waiting for server events...</div>
      </div>
    </header>

    <div class="layout">
      <section class="stage">
        <div class="stage-head">
          <div class="stage-title">3D path</div>
          <div class="legend">
            <span class="swatch right">right +X</span>
            <span class="swatch forward">forward +Y</span>
            <span class="swatch up">up +Z</span>
            <span class="swatch start">start</span>
            <span class="swatch end">latest</span>
          </div>
        </div>
        <canvas id="canvas"></canvas>
      </section>

      <aside class="side">
        <p class="section-title">Calibration</p>
        <div class="steps">
          <div id="stepOrigin" class="step active">1. Hold the controller at neutral, then click <b>Start calibration</b>.</div>
          <div id="stepRight" class="step">2. Move the controller to your right and click <b>Save right direction</b>.</div>
          <div id="stepForward" class="step">3. Move the controller forward and click <b>Save forward direction</b>.</div>
        </div>
        <div class="actions">
          <button id="startCalibration" class="primary">Start calibration</button>
          <button id="saveRight" disabled>Save right direction</button>
          <button id="saveForward" disabled>Save forward direction</button>
          <button id="resetCalib">Reset calibration</button>
          <button id="clear" class="full">Clear path</button>
        </div>
        <div id="calibCard" class="card subtle">
          <div class="label">Frame status</div>
          <div id="calibStatus" class="value">No calibration yet.</div>
        </div>

        <p class="section-title">Live data</p>
        <div class="card compact"><div class="label">Samples / path</div><div id="samples" class="value large">0 / 0.000 m</div></div>
        <div class="card compact"><div class="label">Controller</div><div id="buttons">--</div></div>
        <details class="details">
          <summary>Raw numbers</summary>
          <div class="raw-grid">
            <div class="card"><div class="label">Teleop position [right, forward, up]</div><div id="teleopPos" class="value">--</div></div>
            <div class="card"><div class="label">Raw Quest position [x, y, z]</div><div id="rawPos" class="value">--</div></div>
            <div class="card"><div class="label">Quaternion (xyzw)</div><div id="quat" class="value">--</div></div>
            <div class="card"><div class="label">Stream</div><div id="stream" class="value">--</div></div>
          </div>
        </details>
        <div class="actions">
          <button id="fit">Fit view</button>
          <button id="poseAxes">Hide pose axes</button>
        </div>
        <div class="card subtle small">Drag to rotate. Wheel to zoom. Numeric teleop frame is [right, forward, up]. In the 3D view, forward is drawn into the scene (-Z) so right cross forward = up.</div>
      </aside>
    </div>
  </div>

<script>
const CALIBRATION_VERSION = 4;
const canvas = document.getElementById('canvas');
const ctx = canvas.getContext('2d');
const dot = document.getElementById('dot');
const gateText = document.getElementById('gateText');
const statusText = document.getElementById('statusText');
const samplesEl = document.getElementById('samples');
const teleopPosEl = document.getElementById('teleopPos');
const buttonsEl = document.getElementById('buttons');
const rawPosEl = document.getElementById('rawPos');
const quatEl = document.getElementById('quat');
const streamEl = document.getElementById('stream');
const calibStatus = document.getElementById('calibStatus');
const stepOrigin = document.getElementById('stepOrigin');
const stepRight = document.getElementById('stepRight');
const stepForward = document.getElementById('stepForward');
const startCalibrationBtn = document.getElementById('startCalibration');
const saveRightBtn = document.getElementById('saveRight');
const saveForwardBtn = document.getElementById('saveForward');
const poseAxesBtn = document.getElementById('poseAxes');
let rawPoints = [];
let points = [];
let latestRaw = null;
let latest = null;
let gateOpen = false;
let pauseState = null;
let resolutionState = null;
let gripperState = null;
let gripperCount = 0;
let lastMessage = null;
let az = 0.72;
let el = 0.42;
let zoom = 1.0;
let showPoseAxes = true;
let dragging = false;
let dragStart = null;
const QUEST_UP = {x:0, y:1, z:0};
const MIN_RIGHT_M = 0.05;
let calibration = loadCalibration();

function vec(x=0, y=0, z=0) { return {x:Number(x), y:Number(y), z:Number(z)}; }
function add(a,b) { return vec(a.x+b.x, a.y+b.y, a.z+b.z); }
function sub(a,b) { return vec(a.x-b.x, a.y-b.y, a.z-b.z); }
function mul(a,s) { return vec(a.x*s, a.y*s, a.z*s); }
function dot3(a,b) { return a.x*b.x + a.y*b.y + a.z*b.z; }
function cross(a,b) { return vec(a.y*b.z-a.z*b.y, a.z*b.x-a.x*b.z, a.x*b.y-a.y*b.x); }
function norm(a) { return Math.hypot(a.x, a.y, a.z); }
function normalize(a) { const n = norm(a); return n > 1e-9 ? mul(a, 1/n) : vec(); }
function fmt(value, digits=4) { return Number.isFinite(value) ? Number(value).toFixed(digits) : '--'; }
function clonePoint(p) { return {...p}; }
function rawVec(p) { return vec(p.x, p.y, p.z); }
function hasOrigin() { return calibration && calibration.origin; }
function hasRight() { return hasOrigin() && calibration.right; }
function isCalibrated() { return calibration && calibration.origin && calibration.right && calibration.forward && calibration.up && calibration.version === CALIBRATION_VERSION; }
function saveCalibration() { localStorage.setItem('questTeleopCalibration', JSON.stringify(calibration)); }
function loadCalibration() {
  try {
    const parsed = JSON.parse(localStorage.getItem('questTeleopCalibration') || 'null');
    if (!parsed || parsed.version !== CALIBRATION_VERSION) return null;
    if (!parsed.origin) return null;
    return parsed;
  } catch (_) { return null; }
}
function rawToDisplay(p) {
  const out = clonePoint(p);
  if (!isCalibrated()) {
    out.z = -out.z;
    out.raw = rawVec(p);
    out.teleop = vec(p.x, p.z, p.y);
    return out;
  }
  const delta = sub(rawVec(p), calibration.origin);
  const right = dot3(delta, calibration.right);
  const forward = dot3(delta, calibration.forward);
  const up = dot3(delta, calibration.up);
  out.x = right;
  out.y = up;
  out.z = -forward;
  out.raw = rawVec(p);
  out.teleop = vec(right, forward, up);
  return out;
}
function directionToDisplay(dir) {
  if (!isCalibrated()) return vec(dir.x, dir.y, -dir.z);
  return vec(dot3(dir, calibration.right), dot3(dir, calibration.up), -dot3(dir, calibration.forward));
}
function currentRightMotion() {
  if (!hasOrigin() || !latestRaw) return null;
  const rawDelta = sub(rawVec(latestRaw), calibration.origin);
  const horizontal = sub(rawDelta, mul(QUEST_UP, dot3(rawDelta, QUEST_UP)));
  return {horizontal, length:norm(horizontal)};
}
function currentForwardMotion() {
  if (!hasRight() || !latestRaw) return null;
  const rawDelta = sub(rawVec(latestRaw), calibration.origin);
  const horizontal = sub(rawDelta, mul(QUEST_UP, dot3(rawDelta, QUEST_UP)));
  const orthogonal = sub(horizontal, mul(calibration.right, dot3(horizontal, calibration.right)));
  return {orthogonal, length:norm(orthogonal)};
}
function refreshDisplayPoints() {
  points = rawPoints.map(rawToDisplay);
  latestRaw = rawPoints.length ? rawPoints[rawPoints.length - 1] : null;
  latest = points.length ? points[points.length - 1] : null;
}
function resize() {
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  draw();
}
window.addEventListener('resize', resize);
function distance(a, b) { return Math.hypot(b.x-a.x, b.y-a.y, b.z-a.z); }
function pathLength() { let total = 0; for (let i = 1; i < points.length; i++) total += distance(points[i-1], points[i]); return total; }
function bounds() {
  const extras = [{x:0,y:0,z:0}];
  const all = points.concat(extras);
  if (!all.length) return {xmin:-.5,xmax:.5,ymin:-.5,ymax:.5,zmin:-.5,zmax:.5,cx:0,cy:0,cz:0,span:1};
  let xmin=Infinity,xmax=-Infinity,ymin=Infinity,ymax=-Infinity,zmin=Infinity,zmax=-Infinity;
  for (const p of all) { xmin=Math.min(xmin,p.x); xmax=Math.max(xmax,p.x); ymin=Math.min(ymin,p.y); ymax=Math.max(ymax,p.y); zmin=Math.min(zmin,p.z); zmax=Math.max(zmax,p.z); }
  const pad = Math.max((xmax-xmin), (ymax-ymin), (zmax-zmin), .12) * .18;
  xmin-=pad; xmax+=pad; ymin-=pad; ymax+=pad; zmin-=pad; zmax+=pad;
  return {xmin,xmax,ymin,ymax,zmin,zmax,cx:(xmin+xmax)/2,cy:(ymin+ymax)/2,cz:(zmin+zmax)/2,span:Math.max(xmax-xmin,ymax-ymin,zmax-zmin,.12)};
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
function line3(a, bpt, b, color, width=1, alpha=1) {
  const A = project(a, b), B = project(bpt, b);
  ctx.save(); ctx.globalAlpha = alpha; ctx.strokeStyle = color; ctx.lineWidth = width; ctx.beginPath(); ctx.moveTo(A.u, A.v); ctx.lineTo(B.u, B.v); ctx.stroke(); ctx.restore();
}
function quatAxes(p) {
  let qx = Number(p.qx), qy = Number(p.qy), qz = Number(p.qz), qw = Number(p.qw);
  const n = Math.hypot(qx, qy, qz, qw);
  if (!Number.isFinite(n) || n <= 1e-9) return null;
  qx /= n; qy /= n; qz /= n; qw /= n;
  const xx=qx*qx, yy=qy*qy, zz=qz*qz, xy=qx*qy, xz=qx*qz, yz=qy*qz, wx=qw*qx, wy=qw*qy, wz=qw*qz;
  return [
    {label:'X', color:'#d44c47', dir:directionToDisplay(vec(1 - 2*(yy + zz), 2*(xy + wz), 2*(xz - wy)))},
    {label:'Y', color:'#448361', dir:directionToDisplay(vec(2*(xy - wz), 1 - 2*(xx + zz), 2*(yz + wx)))},
    {label:'Z', color:'#337ea9', dir:directionToDisplay(vec(2*(xz + wy), 2*(yz - wx), 1 - 2*(xx + yy)))}
  ];
}
function drawArrow3(origin, dir, b, color, axisLen, width, alpha, label) {
  const end = add(origin, mul(normalize(dir), axisLen));
  const A = project(origin, b), B = project(end, b);
  const dx = B.u - A.u, dy = B.v - A.v, screenLen = Math.hypot(dx, dy);
  ctx.save(); ctx.globalAlpha = alpha; ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = width;
  ctx.beginPath(); ctx.moveTo(A.u, A.v); ctx.lineTo(B.u, B.v); ctx.stroke();
  if (screenLen > 2) {
    const angle = Math.atan2(dy, dx), head = width >= 3 ? 9 : 6;
    ctx.beginPath(); ctx.moveTo(B.u, B.v);
    ctx.lineTo(B.u - head * Math.cos(angle - Math.PI / 7), B.v - head * Math.sin(angle - Math.PI / 7));
    ctx.lineTo(B.u - head * Math.cos(angle + Math.PI / 7), B.v - head * Math.sin(angle + Math.PI / 7));
    ctx.closePath(); ctx.fill();
  }
  if (label) { ctx.font = '12px SF Mono, Menlo, monospace'; ctx.fillText(label, B.u + 6, B.v - 4); }
  ctx.restore();
}
function drawPoseAxes(p, b, emphasis=false) {
  const axes = quatAxes(p); if (!axes) return;
  const axisLen = Math.max(0.035, Math.min(0.22, b.span * (emphasis ? 0.14 : 0.08)));
  for (const axis of axes) drawArrow3(p, axis.dir, b, axis.color, axisLen, emphasis ? 3.0 : 1.3, emphasis ? 0.9 : 0.20, emphasis ? axis.label : null);
}
function drawGrid(b) {
  for (let i=0; i<=8; i++) {
    const tx = b.xmin + (b.xmax-b.xmin)*i/8;
    const tz = b.zmin + (b.zmax-b.zmin)*i/8;
    line3({x:tx,y:0,z:b.zmin}, {x:tx,y:0,z:b.zmax}, b, '#eeeeee', 1);
    line3({x:b.xmin,y:0,z:tz}, {x:b.xmax,y:0,z:tz}, b, '#eeeeee', 1);
  }
}
function drawWorldAxes(b) {
  const axisLen = Math.max(.18, b.span * .24);
  const origin = vec(0,0,0);
  drawArrow3(origin, vec(1,0,0), b, '#d44c47', axisLen, 2.5, .9, 'right');
  drawArrow3(origin, vec(0,0,-1), b, '#448361', axisLen, 2.5, .9, 'forward');
  drawArrow3(origin, vec(0,1,0), b, '#337ea9', axisLen, 2.5, .9, 'up');
}
function draw() {
  const rect = canvas.getBoundingClientRect();
  ctx.clearRect(0, 0, rect.width, rect.height);
  ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, rect.width, rect.height);
  const b = bounds();
  ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  drawGrid(b); drawWorldAxes(b);
  if (points.length > 1) {
    for (let i=1; i<points.length; i++) {
      const A = project(points[i-1], b), B = project(points[i], b);
      const t = i / Math.max(points.length-1, 1);
      const gray = Math.round(170 - 110*t);
      ctx.strokeStyle = `rgba(${gray}, ${gray}, ${gray}, .92)`;
      ctx.lineWidth = 1.6 + 1.2*t;
      ctx.beginPath(); ctx.moveTo(A.u,A.v); ctx.lineTo(B.u,B.v); ctx.stroke();
    }
  }
  if (showPoseAxes && points.length) {
    const stride = Math.max(1, Math.floor(points.length / 16));
    for (let i=0; i<points.length-1; i+=stride) drawPoseAxes(points[i], b, false);
  }
  if (points.length) {
    const S = project(points[0], b), E = project(points[points.length-1], b);
    ctx.fillStyle = '#448361'; ctx.beginPath(); ctx.arc(S.u,S.v,5,0,Math.PI*2); ctx.fill();
    ctx.fillStyle = '#d44c47'; ctx.beginPath(); ctx.arc(E.u,E.v,6,0,Math.PI*2); ctx.fill();
    if (showPoseAxes) drawPoseAxes(points[points.length-1], b, true);
  }
}
function updateCalibrationStatus(message=null) {
  const rightMotion = currentRightMotion();
  const forwardMotion = currentForwardMotion();
  const rightMotionText = rightMotion ? `${fmt(rightMotion.length,3)} m horizontal movement` : '--';
  const forwardMotionText = forwardMotion ? `${fmt(forwardMotion.length,3)} m forward movement` : '--';
  stepOrigin.classList.toggle('done', hasOrigin());
  stepOrigin.classList.toggle('active', !hasOrigin());
  stepRight.classList.toggle('active', hasOrigin() && !hasRight());
  stepRight.classList.toggle('done', hasRight());
  stepForward.classList.toggle('active', hasRight() && !isCalibrated());
  stepForward.classList.toggle('done', isCalibrated());
  saveRightBtn.disabled = !hasOrigin();
  saveForwardBtn.disabled = !hasRight();
  if (message) { calibStatus.textContent = message; return; }
  if (isCalibrated()) {
    calibStatus.textContent = `Ready\nright=[${fmt(calibration.right.x,3)}, ${fmt(calibration.right.y,3)}, ${fmt(calibration.right.z,3)}]\nforward=[${fmt(calibration.forward.x,3)}, ${fmt(calibration.forward.y,3)}, ${fmt(calibration.forward.z,3)}]\nup=[0.000, 1.000, 0.000]`;
  } else if (hasRight()) {
    calibStatus.textContent = `Right saved. Move forward and hold.\nCurrent ${forwardMotionText}\nNeed at least ${fmt(MIN_RIGHT_M,2)} m.`;
  } else if (hasOrigin()) {
    calibStatus.textContent = `Origin saved. Move to your right and hold.\nCurrent ${rightMotionText}\nNeed at least ${fmt(MIN_RIGHT_M,2)} m.`;
  } else {
    calibStatus.textContent = latestRaw ? 'Ready to start. Hold neutral and click Start calibration.' : 'Start Quest streaming first, then calibrate.';
  }
}
function updateStats() {
  dot.classList.toggle('on', gateOpen);
  gateText.textContent = gateOpen ? 'streaming' : 'paused';
  statusText.textContent = `stream=${pauseState ?? '--'} / last=${lastMessage ?? '--'}`;
  samplesEl.textContent = `${points.length} / ${pathLength().toFixed(3)} m`;
  const triggerValue = latestRaw ? (latestRaw.flag ? 'True' : 'False') : '--';
  const triggerClass = latestRaw && latestRaw.flag ? 'trigger-on' : 'trigger-off';
  buttonsEl.innerHTML = `<div class="button-status">
    <div class="status-tile"><div class="name">Stream</div><div class="state">${pauseState ?? '--'}</div></div>
    <div class="status-tile ${triggerClass}"><div class="name">Trigger</div><div class="state">${triggerValue}</div></div>
  </div>`;
  if (latest && latestRaw) {
    teleopPosEl.textContent = `right=${fmt(latest.teleop.x)}\nforward=${fmt(latest.teleop.y)}\nup=${fmt(latest.teleop.z)}`;
    rawPosEl.textContent = `x=${fmt(latestRaw.x)}\ny=${fmt(latestRaw.y)}\nz=${fmt(latestRaw.z)}`;
    const qNorm = Math.hypot(Number(latestRaw.qx), Number(latestRaw.qy), Number(latestRaw.qz), Number(latestRaw.qw));
    quatEl.textContent = `x=${fmt(latestRaw.qx)}\ny=${fmt(latestRaw.qy)}\nz=${fmt(latestRaw.qz)}\nw=${fmt(latestRaw.qw)}\n|q|=${fmt(qNorm, 5)}`;
    streamEl.textContent = `seq=${latestRaw.seq}\nkind=${latestRaw.kind}\nrecv=${latestRaw.recv_iso}`;
  } else {
    teleopPosEl.textContent = '--'; rawPosEl.textContent = '--'; quatEl.textContent = '--'; streamEl.textContent = '--';
  }
  updateCalibrationStatus();
}
function handleEvent(ev) {
  if (ev.type === 'snapshot') {
    rawPoints = (ev.points || []).map(clonePoint);
    gateOpen = !!ev.gate_open; pauseState = ev.pause_state; resolutionState = ev.resolution_state;
    gripperState = ev.gripper_state; gripperCount = ev.gripper_count || 0;
  } else if (ev.type === 'reset') {
    rawPoints = [];
  } else if (ev.type === 'pose') {
    rawPoints.push(clonePoint(ev.point));
  } else if (ev.type === 'status') {
    gateOpen = !!ev.gate_open; pauseState = ev.pause_state; resolutionState = ev.resolution_state;
    gripperState = ev.gripper_state; gripperCount = ev.gripper_count || gripperCount;
  }
  lastMessage = ev.recv_iso || new Date().toLocaleTimeString();
  refreshDisplayPoints(); updateStats(); draw();
}
function requireLatest() {
  if (!latestRaw) { updateCalibrationStatus('No controller sample yet. Start streaming first.'); return null; }
  return rawVec(latestRaw);
}
startCalibrationBtn.onclick = () => {
  const p = requireLatest(); if (!p) return;
  calibration = {version:CALIBRATION_VERSION, origin:p, up:QUEST_UP, state:'awaiting_right', createdAt:new Date().toISOString()};
  saveCalibration(); refreshDisplayPoints(); updateStats(); draw();
};
saveRightBtn.onclick = () => {
  const p = requireLatest(); if (!p) return;
  if (!hasOrigin()) { updateCalibrationStatus('Click Start calibration first.'); return; }
  const rawDelta = sub(p, calibration.origin);
  const horizontal = sub(rawDelta, mul(QUEST_UP, dot3(rawDelta, QUEST_UP)));
  const length = norm(horizontal);
  if (length < MIN_RIGHT_M) { updateCalibrationStatus(`Right movement too small (${fmt(length,3)} m). Move farther right and save again.`); return; }
  const right = normalize(horizontal);
  calibration = {version:CALIBRATION_VERSION, origin:calibration.origin, right, up:QUEST_UP, state:'awaiting_forward', createdAt:calibration.createdAt};
  saveCalibration(); refreshDisplayPoints(); updateStats(); draw();
};
saveForwardBtn.onclick = () => {
  const p = requireLatest(); if (!p) return;
  if (!hasRight()) { updateCalibrationStatus('Save the right direction first.'); return; }
  const rawDelta = sub(p, calibration.origin);
  const horizontal = sub(rawDelta, mul(QUEST_UP, dot3(rawDelta, QUEST_UP)));
  const orthogonal = sub(horizontal, mul(calibration.right, dot3(horizontal, calibration.right)));
  const length = norm(orthogonal);
  if (length < MIN_RIGHT_M) { updateCalibrationStatus(`Forward movement too small (${fmt(length,3)} m). Move farther forward and save again.`); return; }
  const forward = normalize(orthogonal);
  calibration = {version:CALIBRATION_VERSION, origin:calibration.origin, right:calibration.right, forward, up:QUEST_UP, state:'ready', createdAt:calibration.createdAt, completedAt:new Date().toISOString()};
  saveCalibration(); refreshDisplayPoints(); updateStats(); draw();
};
document.getElementById('resetCalib').onclick = () => {
  calibration = null; localStorage.removeItem('questTeleopCalibration'); refreshDisplayPoints(); updateStats(); draw();
};
document.getElementById('clear').onclick = () => { rawPoints = []; refreshDisplayPoints(); updateStats(); draw(); };
document.getElementById('fit').onclick = () => { zoom = 1; draw(); };
poseAxesBtn.onclick = () => { showPoseAxes = !showPoseAxes; poseAxesBtn.textContent = showPoseAxes ? 'Hide pose axes' : 'Show pose axes'; draw(); };
canvas.addEventListener('mousedown', e => { dragging = true; dragStart = {x:e.clientX, y:e.clientY, az, el}; });
window.addEventListener('mouseup', () => dragging = false);
window.addEventListener('mousemove', e => {
  if (!dragging) return;
  az = dragStart.az + (e.clientX - dragStart.x) * 0.01;
  el = Math.max(-1.2, Math.min(1.2, dragStart.el + (e.clientY - dragStart.y) * 0.01));
  draw();
});
canvas.addEventListener('wheel', e => { e.preventDefault(); zoom *= Math.exp(-e.deltaY * 0.001); zoom = Math.max(.25, Math.min(6, zoom)); draw(); }, {passive:false});
fetch('/snapshot').then(r => r.json()).then(handleEvent).catch(() => {});
const es = new EventSource('/events');
es.onmessage = e => handleEvent(JSON.parse(e.data));
es.onerror = () => { gateText.textContent = 'disconnected'; dot.classList.remove('on'); };
updateCalibrationStatus(); resize(); setInterval(draw, 1000);
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
        self.gripper_state: str | None = None
        self.gripper_count = 0
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
                "gripper_state": self.gripper_state,
                "gripper_count": self.gripper_count,
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

    def set_status(
        self,
        *,
        pause_state: str | None = None,
        resolution_state: str | None = None,
        gripper_state: str | None = None,
    ) -> None:
        with self.lock:
            if pause_state is not None:
                self.pause_state = pause_state
            if resolution_state is not None:
                self.resolution_state = resolution_state
            if gripper_state is not None:
                self.gripper_state = gripper_state
                self.gripper_count += 1
            event = {
                "type": "status",
                "recv_iso": iso_now(),
                "gate_open": self.gate_open,
                "pause_state": self.pause_state,
                "resolution_state": self.resolution_state,
                "gripper_state": self.gripper_state,
                "gripper_count": self.gripper_count,
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
    parser.add_argument("--gripper-port", type=int, default=DEFAULT_GRIPPER_PORT)
    parser.add_argument("--web-host", default="127.0.0.1")
    parser.add_argument("--web-port", type=int, default=8765)
    parser.add_argument("--out-dir", type=Path, default=Path("captures"))
    parser.add_argument("--session", default=dt.datetime.now().strftime("live_%Y%m%d_%H%M%S"))
    parser.add_argument("--max-points", type=int, default=5000, help="Max points kept in browser memory.")
    parser.add_argument("--print-every", type=int, default=60, help="Print every N accepted poses; 0 disables.")
    parser.add_argument("--trajectory-gate-pause", choices=("High", "Low"), default="High")
    parser.add_argument("--gate-requires-prior-pause", choices=("High", "Low"), default="Low")
    parser.add_argument("--no-gate", action="store_true", help="Accept remote frames immediately, ignoring pause state.")
    parser.add_argument(
        "--keep-origin",
        "--keep-leading-origin",
        action="store_true",
        help="Keep exact 0,0,0 placeholder frames. By default they are dropped anywhere in the stream.",
    )
    parser.add_argument(
        "--max-step-m",
        type=float,
        default=0.20,
        help="Reset the visible path when a single accepted step exceeds this distance; 0 disables.",
    )
    parser.add_argument("--no-record", action="store_true", help="Do not write captures/*.csv files.")
    parser.add_argument("--adb-reverse", action="store_true", help="Run adb reverse for the Quest ports before listening.")
    parser.add_argument("--open-browser", action="store_true", help="Open the live viewer in the default browser.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.adb_reverse:
        setup_adb_reverse([args.remote_port, args.resolution_port, args.pause_port, args.gripper_port])

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
    for channel, port in {
        "remote": args.remote_port,
        "resolution": args.resolution_port,
        "pause": args.pause_port,
        "gripper": args.gripper_port,
    }.items():
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
        last_position: list[float] | None = None
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
                        last_position = None
                        accepted = 0
                        print(f"{recv_iso} trajectory gate opened", flush=True)
                    elif previous_gate and not state.gate_open:
                        print(f"{recv_iso} trajectory gate closed", flush=True)
                    state.set_status(pause_state=text)
                elif channel == "resolution":
                    state.set_status(resolution_state=text)
                elif channel == "gripper":
                    state.set_status(gripper_state=text)
                    print(f"{recv_iso} gripper event: {text!r}", flush=True)
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
                    if not args.keep_origin and is_origin(position):
                        print(f"{recv_iso} dropped exact origin placeholder frame", flush=True)
                        continue
                    if last_position is not None and args.max_step_m > 0:
                        step = sum((a - b) * (a - b) for a, b in zip(position, last_position)) ** 0.5
                        if step > args.max_step_m:
                            state.reset_points()
                            accepted = 0
                            print(
                                f"{recv_iso} trajectory path reset: step={step:.3f}m > {args.max_step_m:.3f}m",
                                flush=True,
                            )

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
                        "flag": remote["flag"],
                    }
                    state.add_pose(point)
                    last_position = position
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
